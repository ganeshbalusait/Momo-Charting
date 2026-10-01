"""Freshest headline per symbol - BEST-EFFORT ONLY.

Alpaca/Benzinga in bulk, plus a BOUNDED Yahoo RSS backfill for the symbols
Benzinga does not cover (123 of 358 on 2026-09-04). See the RSS section below
for why that backfill is capped rather than complete.

One public function::

    fresh_news(symbols, *, window_hours=24, now=None)
        -> dict[symbol, {"headline", "at", "source", "url"}]

The board calls this once per build for the ~50 survivors that actually ship,
so a symbol whose latest headline is newer than ``window_hours`` gets a small
news icon in its Symbol cell. Everything about this module is subordinate to
one rule: NEWS CAN NEVER SLOW OR BREAK THE BOARD.

* Any exception, any non-200, any missing credential -> return ``{}``. The
  board ships without news icons and nobody notices.
* A failure is remembered for :data:`FAILURE_TTL_SECONDS` (60s), so a dead
  endpoint is not re-probed on every 15-35s build.
* Success is TTL-cached for :data:`CACHE_TTL_SECONDS` (~300s) keyed by the
  sorted symbol tuple + window, so the build cadence does not hammer the API.
  A cache hit returns the SAME dict object - no HTTP at all.
* No retries. One short-timeout (:data:`HTTP_TIMEOUT_SECONDS`) GET per chunk
  of at most :data:`MAX_SYMBOLS_PER_REQUEST` symbols.

Credentials come from :func:`momx.feed.resolve_credentials` - the exact
key-resolution chain the bar feed already uses (vault first, then live env
profiles, dead profiles excluded). This module does NOT load keys itself.

``MOMX_NEWS_NETWORK=0`` disables the fetch entirely (the unit-test default,
mirroring ``MOMX_FLAGS_EARNINGS_NETWORK``).
"""

from __future__ import annotations

import email.utils
import os
import re
import threading
import time
import xml.etree.ElementTree as ElementTree
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable

from momx import feed as _feed

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

#: Trader-approved freshness window: a headline older than this lights nothing.
DEFAULT_WINDOW_HOURS = 24

#: Success cache. The board rebuilds every 15-35s; headlines do not.
CACHE_TTL_SECONDS = 300.0

#: Failure cache. A dead endpoint is not retried more than once a minute.
FAILURE_TTL_SECONDS = 60.0

#: Best-effort means never waiting long.
HTTP_TIMEOUT_SECONDS = 8.0

#: Alpaca accepts long symbol lists; chunk conservatively anyway.
MAX_SYMBOLS_PER_REQUEST = 100

#: Articles per request (Alpaca's maximum). Newest-first (``sort=desc``).
PAGE_LIMIT = 50

#: Pages followed per chunk. Since 2026-09-02 the board asks for the WHOLE
#: universe (357 symbols, 4 chunks) rather than the top 50, and a busy
#: 100-symbol chunk has far more than 50 headlines in 24h - one page would
#: silently miss every symbol whose newest story sat below the cut. Ten pages
#: is 500 articles per chunk; past that the miss is a missing icon, not a
#: wrong one.
MAX_PAGES_PER_CHUNK = 10

NETWORK_ENV = "MOMX_NEWS_NETWORK"

# ---------------------------------------------------------------------------
# Module state: one success cache, one failure stamp
# ---------------------------------------------------------------------------

_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}
_FAILURE_AT: float | None = None


def _monotonic() -> float:
    """Test seam for the TTL clock."""
    return time.monotonic()


def reset_caches() -> None:
    """Drop the success cache, the failure stamp and the RSS cache (tests).

    The RSS cache is module state exactly like the others; leaving it out would
    let one test's backfill answer leak into the next one's assertions.
    """
    global _FAILURE_AT, _RSS_CURSOR
    with _LOCK:
        _CACHE.clear()
        _FAILURE_AT = None
        _RSS_CACHE.clear()
        _RSS_CURSOR = 0


def _network_allowed() -> bool:
    return str(os.getenv(NETWORK_ENV, "1")).strip().lower() not in {"0", "false", "no", "off"}


def _default_get() -> Callable:
    import requests

    session = requests.Session()
    return session.get


def _credentials() -> list:
    """The bar feed's credential chain, reused verbatim (test seam)."""
    return _feed.resolve_credentials()


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------


def _normalize_symbols(symbols: Iterable[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in symbols or []:
        symbol = str(raw or "").strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _parse_when(value: Any) -> datetime | None:
    """Alpaca stamps ``2026-08-30T12:34:56Z`` (sometimes with fractions)."""
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _chunk(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[index:index + size] for index in range(0, len(symbols), size)]


def _fetch_articles(
    symbols: list[str],
    *,
    start: datetime,
    get: Callable,
) -> list[dict]:
    """Every article for ``symbols`` since ``start``, or raise.

    One GET per chunk. The first credential that answers a chunk with 200 is
    kept for the remaining chunks; a chunk that no credential can answer
    raises, and the caller turns that into the cached-failure empty result.
    """
    credentials = _credentials()
    if not credentials:
        raise _feed.FeedError("no usable credentials for news")

    articles: list[dict] = []
    ordered = list(credentials)
    for chunk in _chunk(symbols, MAX_SYMBOLS_PER_REQUEST):
        page_token = None
        for _page in range(MAX_PAGES_PER_CHUNK):
            params = {
                "symbols": ",".join(chunk),
                "limit": PAGE_LIMIT,
                "sort": "desc",
                "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            if page_token:
                params["page_token"] = page_token
            answered = False
            for index, credential in enumerate(ordered):
                response = get(
                    NEWS_URL,
                    params=params,
                    headers=credential.headers,
                    timeout=HTTP_TIMEOUT_SECONDS,
                )
                status = getattr(response, "status_code", None)
                if status == 200:
                    payload = response.json() or {}
                    rows = payload.get("news")
                    if isinstance(rows, list):
                        articles.extend(row for row in rows if isinstance(row, dict))
                    page_token = payload.get("next_page_token") or None
                    if index:
                        # Remember the working key for the remaining chunks.
                        ordered.insert(0, ordered.pop(index))
                    answered = True
                    break
                if status in (401, 403):
                    continue  # auth rejection: try the next credential
                raise _feed.FeedError(f"news HTTP {status}")
            if not answered:
                raise _feed.FeedError("every credential rejected the news request")
            if not page_token:
                break
    return articles


#: How many tickers an article may name and still be ABOUT one of them.
#:
#: Measured on the live board 2026-09-04: 120 of 358 rows drew their headline
#: from just 79 distinct articles, because a market-wide story credits every
#: symbol it mentions. "SanDisk Jumps 10%, Lululemon Crashes 17%: Stock Market
#: Today" and "10 Information Technology Stocks Whale Activity In Today's
#: Session" landed on row after row, telling the trader nothing about why HIS
#: ticker moved. The competitor he compared us to has the identical defect -
#: three of their rows (AMD, MRVL, AMDL) show one Nvidia/Hugging Face story
#: word for word.
#:
#: Judged on the article's RAW symbol count, not on how many of them are in our
#: universe: measured, 23 of 51 articles naming >=4 tickers intersect our
#: universe in <=2, so an in-universe test would wave real listicles through
#: ("12 Information Technology Stocks Moving...", 12 raw / 2 in-universe).
SPECIFIC_SYMBOL_LIMIT = 3


def _newest_per_symbol(
    articles: list[dict],
    requested: set[str],
    cutoff: datetime,
) -> dict[str, dict]:
    """Newest fresh article per requested symbol, SPECIFIC stories first.

    An article lists MULTIPLE symbols. One naming a handful is about them; one
    naming twenty is a market round-up that happens to mention them. Both are
    worth showing and they are not worth the same, so the scope is carried on
    the entry rather than decided by hiding things:

        scope "specific"    <= SPECIFIC_SYMBOL_LIMIT tickers named
        scope "market-wide" more than that

    DEMOTE, NEVER HIDE. A market-wide story is still the answer to "is there
    any news at all", and suppressing it would trade one wrong impression for
    another - the trader would read an empty cell as "nothing happened". A
    specific story always wins over a market-wide one regardless of age; among
    equals, newest wins.
    """
    best_at: dict[str, datetime] = {}
    out: dict[str, dict] = {}
    for article in articles:
        at = _parse_when(article.get("created_at") or article.get("updated_at"))
        if at is None or at < cutoff:
            continue
        headline = str(article.get("headline") or "").strip()
        if not headline:
            continue
        named = {str(raw or "").strip().upper() for raw in (article.get("symbols") or [])}
        named.discard("")
        specific = len(named) <= SPECIFIC_SYMBOL_LIMIT
        entry = {
            "headline": headline,
            "at": at.astimezone(timezone.utc).isoformat(),
            "source": str(article.get("source") or "") or None,
            "url": str(article.get("url") or "") or None,
            "scope": "specific" if specific else "market-wide",
            # How many tickers the story names, so the UI can say "one of 12"
            # rather than implying this article is about this company.
            "namedCount": len(named),
        }
        for symbol in named:
            if symbol not in requested:
                continue
            held = out.get(symbol)
            if held is not None:
                held_specific = held.get("scope") == "specific"
                if held_specific and not specific:
                    continue  # a market-wide story never displaces a specific one
                if held_specific == specific and at <= best_at.get(symbol, at - timedelta(seconds=1)):
                    continue  # same class, older
            best_at[symbol] = at
            out[symbol] = entry
    return out


# ---------------------------------------------------------------------------
# RSS backfill - the symbols Benzinga simply does not cover
# ---------------------------------------------------------------------------
#
# Benzinga answered 123 of 358 watchlist rows on 2026-09-04. NBIS's newest
# Benzinga headline was ~53h old so the 24h window correctly dropped it, while
# Yahoo's keyless per-ticker RSS had a 6.1h Nebius story for the same symbol.
# A coverage gap, not a logic bug.
#
# BOUNDED ON PURPOSE. Benzinga's bulk ?symbols= query delivers ~44.75 symbols
# per request; RSS is ONE REQUEST PER TICKER. Backfilling all 235 missing
# symbols every build would be 74-144 requests/minute against a measured
# 2.4/min today - the same shape that got this IP banned twice this week, and
# a direct breach of momx/board.py's rule that its network "may never become
# per-symbol". Hence: a fixed budget per pass, rotating, with misses cached.
# Steady state 24 requests per 300s = 4.8/min, full coverage in ~10 passes.

#: Keyless, ticker-keyed, no account required.
YAHOO_RSS_URL = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
)

#: Symbols fetched from RSS per build. See the arithmetic above.
RSS_BACKFILL_PER_BUILD = 24

#: One symbol's answer is reused this long. Longer than the Benzinga cache
#: because the backfill rotates: a symbol re-fetched every pass would starve
#: the rest of the queue.
RSS_CACHE_TTL_SECONDS = 900.0

#: Shorter than the Benzinga timeout. This is a nice-to-have on a best-effort
#: path and must never be what makes a build slow.
RSS_TIMEOUT_SECONDS = 6.0

RSS_ENV = "MOMX_NEWS_RSS"

#: "10 Information Technology Stocks Whale Activity", "3 Founder Backed Growth
#: Stocks To Watch" - headlines naming many companies and explaining none. RSS
#: carries no symbols list, so the scope test used for Benzinga articles cannot
#: run; this is the cheap deterministic stand-in. Matches are DEMOTED to
#: market-wide, never dropped - the same rule as _newest_per_symbol.
_LISTICLE_PATTERNS = (
    re.compile(r"^\s*\d+\s+\S+.*\bstocks?\b", re.IGNORECASE),
    re.compile(r"\bstocks?\s+(to\s+watch|moving|whale|that)\b", re.IGNORECASE),
    re.compile(r"\b(top|best)\s+\d+\b", re.IGNORECASE),
)

_RSS_CACHE: dict = {}
_RSS_CURSOR = 0


def _rss_enabled() -> bool:
    return str(os.environ.get(RSS_ENV, "1")).strip().lower() not in ("0", "false", "no")


def _looks_like_a_listicle(headline) -> bool:
    text = str(headline or "")
    return any(pattern.search(text) for pattern in _LISTICLE_PATTERNS)


def _parse_rss(body, cutoff):
    """Newest item inside the window, in the shape Benzinga entries use.

    RSS dates are RFC-822, not ISO. The module's own _parse_when handles ISO
    only, so converting here is what stops every RSS item being silently
    discarded as undated.
    """
    root = ElementTree.fromstring(body)
    best = None
    best_at = None
    for item in root.findall(".//item"):
        headline = (item.findtext("title") or "").strip()
        if not headline:
            continue
        raw_date = item.findtext("pubDate")
        if not raw_date:
            continue
        try:
            at = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < cutoff:
            continue
        if best_at is not None and at <= best_at:
            continue
        best_at = at
        best = {
            "headline": headline,
            "at": at.astimezone(timezone.utc).isoformat(),
            "source": "Yahoo Finance",
            "url": (item.findtext("link") or "").strip() or None,
            "scope": "market-wide" if _looks_like_a_listicle(headline) else "specific",
            "namedCount": 0,
        }
    return best


def _rss_backfill(missing, cutoff, get) -> dict:
    """Bounded, rotating per-ticker backfill. Never raises, never blocks long."""
    global _RSS_CURSOR
    out = {}
    if not missing or not _rss_enabled():
        return out
    stamp = _monotonic()
    fetch = []
    for symbol in missing:
        held = _RSS_CACHE.get(symbol)
        if held is not None and stamp - held[0] < RSS_CACHE_TTL_SECONDS:
            if held[1]:
                out[symbol] = held[1]
            continue
        fetch.append(symbol)
    if fetch:
        # Rotate, so every missing symbol is reached over successive builds
        # rather than the same alphabetical head being retried forever.
        start = _RSS_CURSOR % len(fetch)
        ordered = fetch[start:] + fetch[:start]
        taken = ordered[:RSS_BACKFILL_PER_BUILD]
        _RSS_CURSOR = (_RSS_CURSOR + len(taken)) % max(len(fetch), 1)
        for symbol in taken:
            entry = None
            try:
                response = get(
                    YAHOO_RSS_URL.format(symbol=symbol),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=RSS_TIMEOUT_SECONDS,
                )
                if getattr(response, "status_code", 0) == 200:
                    entry = _parse_rss(response.text, cutoff)
            except Exception:  # noqa: BLE001 - news can never break the board
                entry = None
            # Remember the MISS too, so a symbol with genuinely no news does not
            # burn a slot on every build.
            _RSS_CACHE[symbol] = (stamp, entry)
            if entry:
                out[symbol] = entry
    return out


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fresh_news(
    symbols: Iterable[Any],
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: Any = None,
    get: Callable | None = None,
    rss_get: Callable | None = None,
) -> dict[str, dict]:
    """Newest headline (within ``window_hours``) for each of ``symbols``.

    Returns ``{symbol: {"headline", "at", "source", "url"}}`` with an entry
    ONLY for symbols that have a fresh article. Absolutely never raises:
    any failure returns ``{}`` and is remembered for 60s.
    """
    global _FAILURE_AT
    try:
        wanted = _normalize_symbols(symbols)
        if not wanted or not _network_allowed():
            return {}

        key = (tuple(sorted(wanted)), float(window_hours))
        stamp = _monotonic()
        with _LOCK:
            held = _CACHE.get(key)
            if held is not None and stamp - held[0] < CACHE_TTL_SECONDS:
                return held[1]
            if _FAILURE_AT is not None and stamp - _FAILURE_AT < FAILURE_TTL_SECONDS:
                return {}

        moment = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        cutoff = moment - timedelta(hours=float(window_hours))

        articles = _fetch_articles(
            wanted,
            start=cutoff,
            get=get if get is not None else _default_get(),
        )
        result = _newest_per_symbol(articles, set(wanted), cutoff)

        # Benzinga leaves roughly two thirds of the watchlist with nothing.
        # Fill some of that gap from a keyless per-ticker feed, bounded per
        # build - see the RSS section above for why this is capped, not
        # complete. A Benzinga entry always wins over a backfilled one.
        # The RSS getter is a SEPARATE seam from ``get`` on purpose. ``get`` is
        # Benzinga-shaped (called with params= and credential headers) and the
        # existing tests inject it and count its calls; reusing it for a
        # per-ticker RSS URL would both break those counts and hand the wrong
        # request shape to a stub. So RSS runs with its own getter: the real
        # one in production (``get`` unset), and only an explicitly injected
        # ``rss_get`` under test - never the live network from a unit test.
        missing = [symbol for symbol in wanted if symbol not in result]
        if missing and (rss_get is not None or get is None):
            backfilled = _rss_backfill(
                missing, cutoff, rss_get if rss_get is not None else _default_get(),
            )
            if backfilled:
                result = {**backfilled, **result}

        with _LOCK:
            _CACHE[key] = (stamp, result)
            _FAILURE_AT = None
        return result
    except Exception:  # noqa: BLE001 - news can never break the board
        with _LOCK:
            _FAILURE_AT = _monotonic()
        return {}


def recent_headlines(
    symbol: Any,
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    limit: int = 8,
    now: Any = None,
    get: Callable | None = None,
) -> list[dict]:
    """Up to ``limit`` recent headlines for ONE symbol, newest first.

    For the AI news reader (momx/news_catalyst.py), which needs the handful of
    stories behind a move, not only the newest one the icon shows. Stories
    naming this symbol among at most SPECIFIC_SYMBOL_LIMIT tickers come first;
    market round-ups follow (a listicle rarely explains a move). ONE request,
    called only for the few graded tickers the reader judges, never per build.
    Never raises: any failure is [].
    """
    try:
        wanted = _normalize_symbols([symbol])
        if not wanted or not _network_allowed():
            return []
        moment = now if isinstance(now, datetime) else datetime.now(timezone.utc)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        cutoff = moment - timedelta(hours=float(window_hours))
        articles = _fetch_articles(wanted, start=cutoff, get=get if get is not None else _default_get())
        out: list[dict] = []
        seen: set[str] = set()
        for article in articles:
            at = _parse_when(article.get("created_at") or article.get("updated_at"))
            headline = str(article.get("headline") or "").strip()
            if at is None or at < cutoff or not headline or headline.casefold() in seen:
                continue
            named = {str(raw or "").strip().upper() for raw in (article.get("symbols") or [])}
            named.discard("")
            if wanted[0] not in named:
                continue
            seen.add(headline.casefold())
            out.append({
                "headline": headline,
                "publishedAt": at.astimezone(timezone.utc).isoformat(),
                "source": str(article.get("source") or "") or None,
                "url": str(article.get("url") or "") or None,
                "specific": len(named) <= SPECIFIC_SYMBOL_LIMIT,
            })
        specific = sorted((r for r in out if r["specific"]), key=lambda r: r["publishedAt"], reverse=True)
        wide = sorted((r for r in out if not r["specific"]), key=lambda r: r["publishedAt"], reverse=True)
        return (specific + wide)[: max(int(limit), 0)]
    except Exception:  # noqa: BLE001 - news can never break anything
        return []


__all__ = ["fresh_news", "recent_headlines", "reset_caches", "DEFAULT_WINDOW_HOURS"]
