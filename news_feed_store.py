"""The MomX news store: the ticker-tagged scraper (catalyst_engine.py) plus the
catalyst_items table, behind the two calls the MomX tab makes.

* ``latest(symbols)``  - stored headlines per ticker. DB only, never scrapes.
* ``refresh(symbols)`` - ONE background scrape for the board's tickers (capped),
  or a report that one is already running. Returns at once; the tab polls
  ``latest`` until ``refreshing`` clears.

api_server's DashboardState owns one of these, but the module deliberately
imports nothing from api_server, so a script or a throwaway smoke server can
drive the exact same code against a scratch database without constructing the
whole dashboard (which starts market-data clients).
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Any, Callable

from catalyst_engine import CatalystEngine, parse_source_list


def dedupe_symbols(raw_items: Any) -> list[str]:
    """Upper-cased, stripped, first-seen order, blanks dropped."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in raw_items if isinstance(raw_items, (list, tuple, set)) else []:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def alpaca_credential_pool(settings: Any) -> list[tuple[str, str]]:
    """EVERY configured Alpaca pair, the active profile first, never printed.

    A single pair is not enough on this box: the live probe of 2026-09-26 got
    HTTP 401 from the active profile's key while another profile's key
    answered (the same "dead paper3 key" lesson momx/news.py learned). The
    Alpaca source tries the pool in order and keeps the first pair that answers.
    """
    pool: list[tuple[str, str]] = []

    def add(key: str, secret: str) -> None:
        if key and secret and (key, secret) not in pool:
            pool.append((key, secret))

    try:
        credentials = settings.credentials_for_profile(settings.default_account_profile, settings.execution_mode)
        add(credentials.key or "", credentials.secret or "")
    except Exception:  # noqa: BLE001 - a broken profile must not stop the others
        pass
    # News, like the market clock, is account-agnostic: ANY authorised key
    # answers the same. clock_credential_candidates deliberately ignores the
    # ALPACA_ACCOUNT_PROFILES allow-list (which on this box names only the
    # dead paper3 key), so the PAPER4/PAPER5 keys join the pool.
    for mode in ("paper", "live"):
        try:
            for credential in settings.clock_credential_candidates(mode):
                add(credential.key or "", credential.secret or "")
        except Exception:  # noqa: BLE001 - older config without the helper
            pass
    try:
        for profile in settings.available_profiles("paper") + settings.available_profiles("live"):
            add(profile.key or "", profile.secret or "")
    except Exception:  # noqa: BLE001
        pass
    return pool


def build_catalyst_engine(settings: Any) -> CatalystEngine:
    """News is scraped from ticker-tagged feeds (Yahoo Finance, Alpaca/Benzinga,
    Benzinga site API, Finviz, Nasdaq, SEC EDGAR, plus Finnhub/Polygon/Alpha Vantage/Tiingo
    when their free-tier key is set). Alpaca news works with any configured Alpaca key pair;
    the pairs come from the app's own profile config, never from this file."""
    news = settings.news
    pool = alpaca_credential_pool(settings)
    return CatalystEngine(
        timeout_seconds=news.timeout_seconds,
        max_workers=news.max_workers,
        cache_ttl_seconds=news.cache_ttl_seconds,
        sources=parse_source_list(news.sources),
        per_symbol_limit=news.per_symbol_limit,
        lookback_days=news.lookback_days,
        alpaca_credentials=pool[0] if pool else ("", ""),
        alpaca_credential_pool=pool,
        api_keys={
            "finnhub": news.finnhub_api_key,
            "polygon": news.polygon_api_key,
            "alphavantage": news.alpha_vantage_api_key,
            "tiingo": news.tiingo_api_key,
        },
        contact_email=news.contact_email,
    )


def scrape_symbols(engine: Any, repository: Any, symbols: list[str], news_settings: Any) -> dict:
    """Read every source for ``symbols``, store what is new, report per source.

    The dict is what /api/news-feed's ``newsFeedMeta`` and the MomX list's
    health line are built from; ``message`` is the one-line bot-event text.
    """
    scoped_symbols = list(symbols)
    started = time.monotonic()
    items = engine.load_watchlist_news(scoped_symbols)
    stored = repository.log_catalysts(items)
    source_status = engine.source_status() if hasattr(engine, "source_status") else []
    available_sources = engine.available_sources() if hasattr(engine, "available_sources") else []
    healthy = [entry for entry in source_status if entry.get("status") in {"ok", "partial"}]
    failed = [entry for entry in source_status if entry.get("status") in {"blocked", "error"}]
    message = (
        f"News scan completed for {len(scoped_symbols)} symbols; {len(items)} ticker-tagged headlines fetched, "
        f"{int(stored or 0)} new. Sources ok: {len(healthy)}/{len(source_status)}."
    )
    if failed:
        message += " Unavailable: " + ", ".join(str(entry.get("label")) for entry in failed) + "."
    try:
        repository.log_bot_event("catalyst_scan", message)
    except Exception:  # noqa: BLE001 - the event log is a nicety
        pass
    return {
        "message": message,
        "symbolsScanned": len(scoped_symbols),
        "symbols": scoped_symbols,
        "headlinesRefreshed": len(items),
        "headlinesStored": int(stored or 0),
        "refreshedAt": datetime.now().astimezone().isoformat(),
        "elapsedMs": int((time.monotonic() - started) * 1000),
        "sources": source_status,
        "availableSources": available_sources,
        "lookbackDays": int(getattr(engine, "lookback_days", news_settings.lookback_days)),
        "perSymbolLimit": int(getattr(engine, "per_symbol_limit", news_settings.per_symbol_limit)),
    }


def item_from_row(row: dict) -> dict:
    """One stored catalyst_items row -> the shape the MomX tab reads."""
    related = [
        token.strip().upper()
        for token in str(row.get("related_symbols") or "").split(",")
        if token and str(token).strip()
    ]
    return {
        "symbol": str(row.get("symbol") or "").upper(),
        "headline": str(row.get("headline") or ""),
        "url": str(row.get("url") or ""),
        "source": str(row.get("source") or ""),
        "via": str(row.get("via") or ""),
        "publishedAt": str(row.get("published_at") or ""),
        "storedAt": str(row.get("created_at") or ""),
        "sentiment": str(row.get("sentiment") or ""),
        "score": int(row.get("score") or 0),
        "tags": str(row.get("tags") or ""),
        "summary": str(row.get("summary") or ""),
        "relatedSymbols": related,
        "articleId": str(row.get("article_id") or ""),
    }


class MomxNewsStore:
    """Stored headlines per ticker + one background scrape at a time."""

    def __init__(
        self,
        repository: Any,
        engine: Any,
        news_settings: Any,
        scrape: Callable[[list[str]], dict] | None = None,
    ) -> None:
        self.repository = repository
        self.engine = engine
        self.news = news_settings
        # The scrape callable is injectable so DashboardState can route it
        # through its own _refresh_catalyst_information (tests swap the engine
        # on the state, not on the store).
        self._scrape = scrape or (lambda symbols: scrape_symbols(self.engine, self.repository, symbols, self.news))
        self.lock = threading.Lock()
        self.state: dict = {
            "running": False,
            "startedAt": None,
            "finishedAt": None,
            "symbols": [],
            "meta": None,
            "error": None,
        }

    # ---- reads ---------------------------------------------------------------
    def latest(self, symbols: Any) -> dict:
        """Stored ticker-tagged headlines for the MomX board. DB only, never scrapes."""
        wanted = dedupe_symbols(symbols)
        error = None
        rows: list[dict] = []
        if wanted:
            try:
                rows = self.repository.get_catalysts_for_symbols(
                    wanted, per_symbol=self.news.momx_feed_per_symbol, lookback_days=self.news.lookback_days,
                )
            except Exception as exc:  # noqa: BLE001 - a store error must not blank the board
                error = f"{type(exc).__name__}: {exc}"
        feed = [item_from_row(row) for row in rows]
        latest: dict[str, dict] = {}
        for item in feed:
            latest.setdefault(item["symbol"], item)
        with self.lock:
            state = dict(self.state)
        return {
            "symbols": wanted,
            "latest": latest,
            "feed": feed,
            "count": len(latest),
            "lookbackDays": int(self.news.lookback_days),
            "refreshing": bool(state.get("running")),
            "refreshStartedAt": state.get("startedAt"),
            "refreshFinishedAt": state.get("finishedAt"),
            "refreshSymbols": list(state.get("symbols") or []),
            "refreshError": state.get("error"),
            "newsFeedMeta": state.get("meta") or {},
            "storeError": error,
        }

    # ---- writes --------------------------------------------------------------
    def refresh(self, symbols: Any) -> dict:
        """Start ONE background scrape for the board's tickers (capped), or report
        the one already running. Returns at once; the tab polls ``latest``."""
        wanted = dedupe_symbols(symbols)[: max(int(self.news.momx_refresh_max_symbols), 1)]
        if not wanted:
            return {"started": False, "reason": "no symbols", "running": False}
        with self.lock:
            if self.state.get("running"):
                return {
                    "started": False,
                    "reason": "already running",
                    "running": True,
                    "symbols": list(self.state.get("symbols") or []),
                    "startedAt": self.state.get("startedAt"),
                }
            self.state.update({
                "running": True,
                "startedAt": datetime.now().astimezone().isoformat(),
                "finishedAt": None,
                "symbols": list(wanted),
                "error": None,
            })

        def runner() -> None:
            meta = None
            error = None
            try:
                meta = self._scrape(list(wanted))
            except Exception as exc:  # noqa: BLE001 - news can never take the server down
                error = f"{type(exc).__name__}: {exc}"
                try:
                    self.repository.log_bot_event("catalyst_scan_error", f"MomX news refresh failed: {exc}")
                except Exception:  # noqa: BLE001
                    pass
            finally:
                with self.lock:
                    self.state.update({
                        "running": False,
                        "finishedAt": datetime.now().astimezone().isoformat(),
                        "error": error,
                    })
                    if meta is not None:
                        self.state["meta"] = {k: v for k, v in meta.items() if k != "message"}

        threading.Thread(target=runner, name="momx-news-refresh", daemon=True).start()
        return {"started": True, "running": True, "symbols": list(wanted)}

    def wait(self, timeout: float = 30.0) -> bool:
        """Block until the running scrape (if any) finishes. Tests and scripts only."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if not self.state.get("running"):
                    return True
            time.sleep(0.05)
        return False


__all__ = [
    "MomxNewsStore",
    "alpaca_credential_pool",
    "build_catalyst_engine",
    "dedupe_symbols",
    "item_from_row",
    "scrape_symbols",
]
