"""HTTP wrappers for the five AI features: one cache, one never-block rule.

Every ``/api/ai/*`` route in ``api_server.py`` and in ``momx_dev_server.py`` is
served from here, so the two servers cannot drift apart and the two properties
that actually matter are written exactly once:

* **A model call never happens on a request thread.** The builder runs on a
  daemon thread; the request thread waits a fraction of a second for it and
  then hands back whatever is already in hand. A 20-second OpenAI timeout can
  therefore never hold an HTTP worker, and on this server holding a worker is
  not a local problem -- the chart engine shares the same GIL.
* **Every answer is cached for five minutes.** A browser polling one of these
  routes cannot cost more than one model call per feature per five minutes.
  Nothing here is ever driven by a poll timer of its own; a build happens only
  because somebody asked and the cached copy had expired.

Nothing in here raises. A missing key, a broken import, a builder that throws --
each comes back as the standard feature body
``{"available": false, "reason": "<plain English>", ...}`` which the UI renders
as written.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable

# Five minutes, the floor the brief asked for. A board triage or a journal
# lesson does not change meaningfully faster than this, and the trader is not
# paying per poll.
CACHE_TTL_SECONDS = 300.0

# How long a request thread will wait for a build before giving up on it and
# answering from cache. Deliberately short: the no-key path finishes in
# microseconds (it makes no network call at all), so today every endpoint
# answers on the first request. A real model call blows straight past this and
# lands in the cache for the next poll.
INLINE_WAIT_SECONDS = 1.5

PENDING_REASON = (
    "The AI is writing this now. It appears here on its own in a few seconds -- "
    "no need to do anything."
)

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict]] = {}
_BUILDING: dict[str, threading.Event] = {}

__all__ = [
    "CACHE_TTL_SECONDS",
    "brief",
    "cached",
    "catalyst",
    "default_board_loader",
    "default_headlines_loader",
    "invalidate",
    "journal_lessons",
    "status",
    "triage",
]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _unavailable(reason: str, **extra: Any) -> dict:
    body = {
        "available": False,
        "reason": reason,
        "generatedAt": utc_now_iso(),
        "provider": None,
    }
    body.update(extra)
    return body


def _stamp(payload: dict, age: float, *, fresh: bool) -> dict:
    """Tell the caller how old this copy is, without touching the payload."""
    body = dict(payload)
    body["cacheAgeSeconds"] = round(max(0.0, float(age)), 1)
    if not fresh:
        # A refresh is already running on another thread; this is last time's
        # answer. Saying so is cheaper than making the trader guess.
        body["stale"] = True
    return body


def _run(key: str, builder: Callable[[], Any], done: threading.Event) -> None:
    try:
        payload = builder()
        if not isinstance(payload, dict):
            payload = _unavailable("This AI feature returned nothing usable.")
    except Exception as exc:  # noqa: BLE001 - a feature bug must not 500 a route
        payload = _unavailable(f"This AI feature failed: {type(exc).__name__}: {exc}")
    with _LOCK:
        _CACHE[key] = (time.monotonic(), payload)
        _BUILDING.pop(key, None)
    done.set()


def cached(
    key: str,
    builder: Callable[[], Any],
    *,
    ttl: float = CACHE_TTL_SECONDS,
    wait: float = INLINE_WAIT_SECONDS,
) -> dict:
    """Fresh copy, else last copy, else "working on it" -- but never a stall.

    At most one build per key runs at a time, so a page with several AI panels
    open, or several browsers, still costs one model call per key per ``ttl``.
    """
    now = time.monotonic()
    with _LOCK:
        entry = _CACHE.get(key)
        if entry is not None and (now - entry[0]) < ttl:
            return _stamp(entry[1], now - entry[0], fresh=True)
        done = _BUILDING.get(key)
        starting = done is None
        if starting:
            done = threading.Event()
            _BUILDING[key] = done

    if starting:
        threading.Thread(
            target=_run, args=(key, builder, done), name=f"ai-build-{key[:32]}", daemon=True
        ).start()

    done.wait(max(0.0, float(wait)))

    with _LOCK:
        entry = _CACHE.get(key)
    if entry is not None:
        age = time.monotonic() - entry[0]
        return _stamp(entry[1], age, fresh=age < ttl)
    return _unavailable(PENDING_REASON, pending=True)


def invalidate(prefix: str = "") -> int:
    """Drop cached answers (all of them, or one prefix). For tests and admin."""
    with _LOCK:
        keys = [key for key in _CACHE if not prefix or key.startswith(prefix)]
        for key in keys:
            _CACHE.pop(key, None)
    return len(keys)


# ------------------------------------------------------------------ status --


def _status_error(message: str) -> dict:
    return {
        "configured": False,
        "provider": None,
        "model": None,
        "candidates": [],
        "message": message,
    }


def status() -> dict:
    """``provider_status()`` -- a dict lookup, no network, so it is not cached."""
    try:
        from agents.ai_provider import provider_status
    except Exception as exc:  # noqa: BLE001
        return _status_error(f"The AI layer could not be loaded: {exc}")
    try:
        return provider_status()
    except Exception as exc:  # noqa: BLE001
        return _status_error(f"AI configuration could not be read: {exc}")


# ---------------------------------------------------------------- features --


def default_board_loader(list_name: str | None = None) -> Callable[[], Any]:
    """The MomX board, from the warmed snapshot -- never a fresh 25s build."""

    def load() -> Any:
        from momx import service as momx_service

        return momx_service.snapshot(list_name or None)

    return load


def triage(
    board_loader: Callable[[], Any] | None = None,
    *,
    list_name: str | None = None,
    limit: int = 5,
) -> dict:
    """Which few of today's scan matches deserve a chart."""
    try:
        limit = max(1, min(int(limit), 10))
    except (TypeError, ValueError):
        limit = 5
    loader = board_loader or default_board_loader(list_name)

    def build() -> dict:
        from agents import scanner_triage

        return scanner_triage.triage(loader(), limit=limit)

    return cached(f"triage:{(list_name or '').strip().lower()}:{limit}", build)


def brief(
    briefing_loader: Callable[[], Any],
    scanner_loader: Callable[[], Any],
    oi_loader: Callable[[], Any] | None = None,
) -> dict:
    """One AI paragraph on top of the measured morning briefing lines."""

    def build() -> dict:
        from agents import morning_brief_ai

        oi_context = oi_loader() if callable(oi_loader) else None
        return morning_brief_ai.synthesize(briefing_loader(), scanner_loader(), oi_context)

    return cached("brief", build)


def journal_lessons(*, db_path: str | None = None, min_trades: int = 10) -> dict:
    """What his own closed trades say about how he trades."""

    def build() -> dict:
        from agents import journal_lessons as module

        return module.lessons_from_journal(db_path=db_path, min_trades=min_trades)

    return cached(f"journal:{db_path or 'default'}:{min_trades}", build)


def _provider_configured() -> bool:
    try:
        from agents.ai_provider import get_provider

        return get_provider() is not None
    except Exception:  # noqa: BLE001
        return False


def default_headlines_loader(cache: Any = None) -> Callable[[str], list]:
    """Headlines for one symbol.

    Given the running server's ``CatalystCache`` this is a pure read -- the
    background refresher does all the fetching, so no request can be slowed by
    the news feed. Without one (the :3010 side server has no refresher) it
    fetches directly, which is safe here only because this loader is called on
    the build thread, never on a request thread.
    """

    def load(symbol: str) -> list:
        from agents.catalyst_explainer import cached_headlines

        if cache is not None:
            cached = cached_headlines(symbol, cache)
            if cached:
                return cached
            # EMPTY CACHE IS NOT "NO NEWS". CatalystCache holds at most 64
            # symbols (catalyst_news.py:222) and its refresher only walks the
            # last-served scanner rows, so for almost every ticker the trader
            # can click on it is simply absent. Measured 2026-09-04: CHPT
            # returned headlinesConsidered 0 and "No news explains this move."
            # while the board was showing CHPT a Benzinga headline from the
            # same afternoon. Answering a question with "no news" when we
            # merely have not looked is the same class of wrong as the green
            # lamp over a dead connection.
            #
            # Falling through to a direct fetch is safe on THIS path: the
            # docstring's "never on a request thread" rule was written for the
            # scanner payload, which must never wait on news. This loader is
            # reached from /api/ai/catalyst - a button the trader presses, that
            # already waits seconds on a model call. One 8s-timeout news fetch
            # beside it is proportionate, and it hits Alpaca, not Schwab, so it
            # cannot contribute to the quota that has been refusing us.
        from catalyst_news import fetch_symbol_news, news_credentials

        credentials = news_credentials()
        if credentials is None:
            return []
        key_id, secret = credentials
        try:
            return fetch_symbol_news(symbol, key_id, secret)
        except Exception:  # noqa: BLE001 - no news is a valid answer
            return []

    return load


def catalyst(
    symbol: str,
    *,
    change_pct: float | None = None,
    headlines_loader: Callable[[str], list] | None = None,
) -> dict:
    """Why is this thing moving -- or a plain "nothing explains this".

    NOTE: the cache key is the SYMBOL ONLY, deliberately. Keying on the percent
    move too would buy a fresh model call on every tick, which is exactly the
    bill this cache exists to prevent. The consequence is that ``changePct`` in
    the body is the move as it stood when the answer was written; read it next
    to ``cacheAgeSeconds``.
    """
    ticker = str(symbol or "").strip().upper()
    if not ticker:
        return _unavailable(
            "Name a symbol, for example /api/ai/catalyst?symbol=NVDA.",
            symbol="",
            changePct=None,
            headlinesConsidered=0,
            summary="",
            category="UNKNOWN",
            confidence="low",
            headline=None,
        )
    loader = headlines_loader or default_headlines_loader()

    def build() -> dict:
        from agents import catalyst_explainer

        # Resolve the key BEFORE touching the news feed. With no key the answer
        # is fixed ("AI is off"), and explain() checks the provider before it
        # looks at the headlines -- so fetching them first would be a wasted
        # round trip on every cache miss. On the :3010 side server, where the
        # loader really does hit Alpaca, it would be a wasted network call.
        rows = loader(ticker) if _provider_configured() else []
        return catalyst_explainer.explain(ticker, change_pct, rows)

    return cached(f"catalyst:{ticker}", build)
