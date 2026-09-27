"""Bulk historical bar tapes for the whole watchlist (~400 symbols).

Three tapes, one Alpaca REST endpoint:

    fetch_5m(symbols, days=5)      extended-hours 5-minute bars
    fetch_30m(symbols, days=21)    extended-hours 30-minute bars
    fetch_daily(symbols, years=3)  regular-hours daily bars

Each returns ``FeedResult(bars, errors)``: ``bars`` is ``dict[str, DataFrame]``
keyed by UPPERCASE symbol in the house tape shape (columns
``timestamp/open/high/low/close/volume``, ``timestamp`` tz-aware Eastern,
sorted, deduplicated); ``errors`` is ``dict[str, str]`` for the symbols that
came back empty or whose batch failed. Nothing in here raises for a bad
symbol - one dead ticker must never blank the board.

WHY RAW REST AND NOT ``alpaca-py``
    ``alpaca.data.enums.DataFeed`` has only IEX/SIP/OTC, so the SDK cannot ask
    for ``feed=boats`` - the only feed that carries the 20:00-04:00 overnight
    session thinkorswim draws (see data/alpaca_overnight.py). Extended hours on
    Alpaca is purely a function of ``feed``; there is no ``session=`` flag:

        feed=iex    ~empty premarket (measured: 1 bar vs 36)
        feed=sip    04:00-20:00 ET, but 403s on the most recent ~20 minutes
        feed=boats  20:00-04:00 ET only, 0 bars during RTH

    So an "extended hours" intraday tape is SIP merged with BOATS. SIP is
    required; BOATS is best-effort and its failure never fails the tape.

PERFORMANCE CONTRACT - READ BEFORE EDITING
    This module does NETWORK I/O ONLY. No indicator math, no resampling, no
    study building - the only pandas here is assembling/concatenating the raw
    rows into the canonical frame. This repo has a documented history of
    background collectors saturating the GIL and starving the chart engine, so:
      * never spawn a thread per symbol - requests are batched by symbol and
        the pool is hard-capped at MAX_WORKERS;
      * the pool is created per call and shut down when the call returns, so
        this module owns no long-lived threads;
      * there is no background refresh loop here. Callers pull; the TTL cache
        below is what keeps a chatty caller off the network.

THE INCREMENTAL STORE (why a refresh takes seconds, not minutes)
    The first build of each tape kind fetches full depth - ~600 Alpaca pages
    for 358 symbols on the 30-day 30-minute tape, measured 468s. Between two
    warmer builds only minutes of NEW bars exist, so re-downloading a month
    per cycle is what made the board refresh every ~16 minutes instead of
    every ~60s. So each kind ("5m"/"30m"/"daily") keeps an in-process store of
    the last assembled tape per symbol, and a call that misses the TTL cache
    fetches only the TAIL: from the oldest held newest-bar across the batch,
    minus a 2-bar overlap plus the feed's recency delay, so the previously
    partial last bar is refetched complete and replaced. A symbol without a
    held tape (new to the universe, or its full fetch failed before) still
    gets full depth; symbols idle longer than STORE_PRUNE_SECONDS are dropped.

    *** THE SPLIT HAZARD - read before touching the store. ***
    Bars are fetched with adjustment=split, and after a stock splits the
    provider restates THE ENTIRE HISTORY retroactively. Appending
    new-adjustment tails onto old-adjustment history would build a mixed tape
    - exactly the CRWD 4:1 corruption documented in docs/momx/SPEC.md (weekly
    Skittles 25 vs TOS 71). Therefore a FULL rebuild of a kind is FORCED when
    its full_built_at is older than FULL_REBUILD_SECONDS (12h, wall clock -
    wall and not monotonic because a suspended machine freezes monotonic
    time, and the heal is a calendar contract). Splits take effect before the
    premarket, so the first build after 04:00 ET is always a fresh full
    build. clear_cache() also drops the store.

    MEMORY: the store holds ~358 symbols x (5m + 30m + daily) frames - tens
    of MB resident. That is an accepted cost; it is what buys the fast
    refresh.

    DEPTH CAVEAT: within a FULL_REBUILD_SECONDS window the tail path serves
    the depth the store was first built at; asking for a deeper ``days=`` on
    a later call does not deepen held history until the next full rebuild.
    Every production caller uses fixed per-kind depths, so this never bites.
"""

from __future__ import annotations

import sqlite3
import math
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, NamedTuple
from zoneinfo import ZoneInfo

import pandas as pd

from data.alpaca_overnight import BOATS_BARS_URL, boats_bars_to_frame

EASTERN = ZoneInfo("America/New_York")

BARS_URL = BOATS_BARS_URL  # https://data.alpaca.markets/v2/stocks/bars

# ---------------------------------------------------------------------------
# Tunables (all named on purpose - nothing below is a magic number)
# ---------------------------------------------------------------------------

# Symbols per HTTP request. Alpaca documents no cap and 400 comma-joined
# symbols were observed returning HTTP 200, but the response is paged either
# way, so batching bigger buys nothing and only makes one failure blast wider.
BATCH_SIZE = 200

# Bars per page. 10000 is Alpaca's hard maximum (limit=20000 -> HTTP 400).
PAGE_LIMIT = 10000

# Pagination hard stop per batch. Alpaca cuts an intraday page after roughly
# 19 symbol-trading-days scanned regardless of `limit`, so a 200-symbol /
# 30-day pull is legitimately hundreds of pages. data/alpaca_overnight.py caps
# at 50 because an overnight window is one page; that cap would SILENTLY
# TRUNCATE a bulk sweep, hence the much larger ceiling here.
MAX_PAGES_PER_BATCH = 2000

# Concurrency ceiling. Alpaca allows 200 requests/minute and the chart engine
# needs the CPU more than this does. Never raise without measuring the chart.
MAX_WORKERS = 4

# In-process TTL cache. Repeated calls inside this window are served from
# memory and never touch the network, which is what lets several panels or
# endpoints ask for the same tape on one refresh tick and pay for one pull.
# Sized just under a typical 1-minute refresh interval so a per-minute caller
# still gets fresh bars every tick, while bursts within a tick collapse.
CACHE_TTL_SECONDS = 55.0

# Free-plan recency blocks, per feed. A request whose `end` is "now" returns
# HTTP 403 "subscription does not permit querying recent SIP data"; BOATS has
# the same block at ~15 minutes. Clamp `end` back by this much.
FEED_RECENT_DELAY_MINUTES = {"sip": 20, "boats": 16, "iex": 0, "otc": 20}

# Extended-hours feed plan (see module docstring).
INTRADAY_PRIMARY_FEED = "sip"      # 04:00-20:00 ET, required
INTRADAY_OVERNIGHT_FEED = "boats"  # 20:00-04:00 ET, best effort
DAILY_FEED = "sip"

# THE IEX LIVE TAIL (see docs/momx/SPEC.md "The IEX live-tail merge"). SIP hard
# 403s on bars newer than ~15-20 min (FEED_RECENT_DELAY_MINUTES["sip"]); IEX has
# NO such block but its volume is IEX-only (~6% of consolidated). So on the two
# INTRADAY tapes only, after the SIP(+BOATS) fetch, a short IEX tail is fetched
# and the bars STRICTLY NEWER than the newest SIP bar are appended. That takes
# PRICE-based signals (EMA/MACD crosses, Skittles, High/Low, %chg) from ~20 min
# stale to ~6 min. VOLUME-based readings (RVOL, the 4h volume gate) stay
# approximate on those newest IEX bars because IEX volume is thin - an accepted,
# documented tradeoff, NOT hidden. The daily tape is regular-hours and unaffected.
INTRADAY_IEX_TAIL_FEED = "iex"
# How far back the IEX tail reaches. It only needs to cover the window SIP's
# recency block refuses (~20 min) plus a couple of bars of margin; a tiny window
# means one extra bulk request per intraday kind per build, batched exactly like
# the SIP legs - never a per-symbol fetch.
IEX_TAIL_MINUTES = 30
# IEX has no recency block, so its end is barely clamped - just enough to skip
# the still-forming current wall-clock minute rather than SIP's 20 minutes.
IEX_TAIL_END_CLAMP_MINUTES = 1

# --- Schwab volume truth for the live tail ---------------------------------
#
# WHY THIS EXISTS. Alpaca SIP carries real consolidated volume but 403s on
# anything newer than ~20 minutes, so the tail above is filled from IEX -- a
# single venue that is ~2% of US volume. The tape therefore ENDS with bars
# whose volume is a small fraction of reality, while every RVOL calculation
# divides them by an average of full SIP bars. Measured live 2026-09-01:
#
#     NVDA 11:20   Alpaca 37,652    Schwab 691,459     5.4%
#     NVDA 11:35   Alpaca 19,136    Schwab 620,889     3.1%
#     AAPL 10:35   Alpaca 27,601    Schwab 611,090     4.5%
#
# Consequence: every short-timeframe RVOL sat near 0.0 permanently, and the
# momentum alert -- which watches exactly those timeframes -- fired ZERO times
# in a session where CRML printed 23.4x. The trader's summary: "I want to
# catch the momentum trade, you give me scan result move already done."
#
# Schwab (his TOS data, already configured here for charts) returns real
# consolidated volume right up to the forming bar, so the tail's volume is
# taken from it. This is REPLACEMENT WITH MEASURED TRUTH, not an estimate. An
# earlier design scaled IEX up by a reconciliation factor and was dropped
# because Schwab's quote volume disagreed with its own bar history by 14-25%,
# which would have silently inflated every corrected bar -- the exact class of
# plausible-but-wrong number this repo keeps getting burned by.
#
# Cost, measured 2026-09-01 with 16 workers: 40 symbols in 0.77s, zero 429s,
# ~6.9s projected for the 357-symbol Watchlist whose build already takes
# 31-39s. Affordable.
SCHWAB_VOLUME_TIMEFRAMES = ("5Min", "30Min")
SCHWAB_VOLUME_WORKERS = 12
#: How far back to ask Schwab for. Must comfortably exceed SIP's recency block
#: (~20 min) so the whole thin tail is covered even when a build runs slow.
SCHWAB_VOLUME_LOOKBACK_MINUTES = 60
#: Bars stamped with this feed are the thin ones needing replacement.
SCHWAB_VOLUME_REPLACES_FEED = "iex"
#: Stamped onto a bar whose volume now comes from Schwab, so a reader can tell
#: measured-elsewhere from settled-SIP and from thin-IEX.
SCHWAB_VOLUME_FEED_MARK = "schwab"
#: Master switch. The test suite turns this OFF (see tests/conftest.py) so no
#: test can reach the network: these tests drive ``_fetch_tape`` with a fake
#: Alpaca ``get`` AND a historical ``now``, and Schwab will cheerfully serve
#: real bars for a historical window -- which silently overwrote fixture
#: volumes and made three tests fail for a reason that had nothing to do with
#: what they were testing. Correction behaviour is covered directly against
#: the pure functions instead, which need no client at all.
SCHWAB_VOLUME_ENABLED = True

# SPLIT-adjusted, deliberately NOT the "raw" house convention used by
# data/alpaca_client.py. That convention is right for drawing raw prices and
# wrong for indicator math: Alpaca's default adjustment=raw leaves a split as a
# real price gap, so every window spanning one is corrupted.
#
# Measured on CRWD, which split 4:1 on 2026-07-02:
#     raw   07-01=772.74  07-02=193.98   <- a fake -75% bar
#     split 07-01=193.19  07-02=193.98   <- continuous
# With raw, its weekly Skittles read 25 against TOS's 71 (the 8-week stochastic
# window straddled the split) while the daily value matched exactly. Volume is
# adjusted too, so RVOL was equally poisoned.
#
# "split", NOT "all": thinkorswim shows split-adjusted prices with dividend
# adjustment OFF by default, and "all" would additionally back-adjust dividends,
# reintroducing a smaller mismatch on every dividend payer.
DAILY_ADJUSTMENT = "split"

# The intraday tapes need it too: the 30-minute tape is ~30 days deep, so a
# split inside that window corrupts the 1h/2h/4h columns exactly as it did the
# weekly ones. BOATS ignores the parameter, which is harmless -- an overnight
# session cannot straddle its own split.
INTRADAY_ADJUSTMENT = "split"

# Explicit timeframe strings. NEVER prefix-sniff a timeframe: a loose parse on
# the chart path once served 20 years of 1-minute bars in place of 30-minute
# bars (594,766 rows / 629MB of poisoned cache for one symbol).
TIMEFRAME_5M = "5Min"
TIMEFRAME_30M = "30Min"
TIMEFRAME_DAILY = "1Day"

HTTP_TIMEOUT_SECONDS = 60
RETRY_ATTEMPTS = 3        # ConnectionResetError(10054) from Alpaca is routine
RETRY_BACKOFF_SECONDS = 1.0
RATE_LIMIT_FLOOR = 5      # pause when X-Ratelimit-Remaining drops this low
RATE_LIMIT_MAX_SLEEP = 30.0

# Documented-dead credentials. `.env` pins ALPACA_ACCOUNT_PROFILES and
# ACTIVE_ALPACA_PROFILE to paper3, whose key answers 401 on every feed, so
# settings.credentials_for_profile() alone can only ever hand back a dead key.
DEAD_PROFILE_IDS = frozenset({"paper3"})

# The encrypted per-user vault entry the running app actually uses; the user
# maintains this key on Settings -> API credentials.
VAULT_PROVIDER = "alpaca_market_data"

CANONICAL_COLUMNS = ["timestamp", "open", "high", "low", "close", "volume"]

# ---------------------------------------------------------------------------
# Incremental-store tunables (see "THE INCREMENTAL STORE" in the docstring)
# ---------------------------------------------------------------------------

# Force a full-depth rebuild of a kind when its store is older than this.
# THE SPLIT HEAL: adjustment=split restates all history after a split, so an
# incremental store must not outlive the overnight window in which splits take
# effect. 12h guarantees the first build after 04:00 ET is always full.
FULL_REBUILD_SECONDS = 12 * 3600.0

# Drop a symbol's held tape when nothing has asked for it this long. Several
# named lists (Mag7, Watchlist) share this module and alternate on one warmer
# thread, so pruning "not in this call's universe" would evict each list's
# tapes on every alternation; idle-time pruning keeps both warm while still
# releasing symbols genuinely removed from every universe.
# 2 HOURS, not 15 minutes - the 15-minute horizon caused a measured death
# spiral on 2026-08-28 (~02:30 ET): the warmer idles as long as the last build
# took, so a list's touch-to-touch gap is TWO build times. One slow build
# (verifier scripts sharing the Alpaca key pushed it past 7.5 min) stretched
# the gap past the prune horizon, every symbol was evicted, the next build was
# therefore FULL (slower still: 771s, then 866s), and the spiral locked in.
# The daily 12h split-heal full build (~8 min daytime) would have re-armed it
# every day. The horizon must comfortably exceed 2x the WORST build, not the
# typical one. Cost of 2h: stale tapes for symbols removed from a universe
# linger up to 2h in memory (tens of MB) - accepted.
STORE_PRUNE_SECONDS = 2 * 3600.0

# The tail refetch reaches this many bar-spans behind the newest held bar (on
# top of the feed's recency delay) so the previously-partial last bar - and a
# possible late restatement of the one before it - is refetched complete.
TAIL_OVERLAP_BARS = 2

_TIMEFRAME_SPAN_SECONDS = {
    TIMEFRAME_5M: 5 * 60,
    TIMEFRAME_30M: 30 * 60,
    TIMEFRAME_DAILY: 24 * 60 * 60,
}

# Seams the tests replace; production values are the real thing.
_sleep = time.sleep


def _monotonic() -> float:
    return time.monotonic()


def _wall_clock() -> float:
    """Wall time for the split heal - monotonic freezes across a suspend."""
    return time.time()


class FeedError(Exception):
    """A whole batch failed after every credential and retry."""


class FeedResult(NamedTuple):
    bars: dict[str, pd.DataFrame]
    errors: dict[str, str]


@dataclass(frozen=True)
class FeedCredential:
    profile_id: str
    key: str
    secret: str

    @property
    def headers(self) -> dict[str, str]:
        return {"APCA-API-KEY-ID": self.key, "APCA-API-SECRET-KEY": self.secret}


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


def _vault_credentials() -> list[FeedCredential]:
    """The owner's saved market-data key from the encrypted per-user store.

    This is the known-good path - it is what the live server uses for charts.
    Any failure (no DB, no user, no key) returns an empty list; the env
    profiles below are the fallback.
    """
    try:
        import config
        from auth_service import AuthService

        connection = sqlite3.connect(str(config.DATABASE_PATH), timeout=10.0)
        try:
            row = connection.execute(
                "SELECT id FROM app_users WHERE is_active = 1 "
                "ORDER BY CASE role WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1"
            ).fetchone()
        finally:
            connection.close()
        if not row:
            return []
        payload = AuthService().get_provider_credentials(str(row[0]), VAULT_PROVIDER)
        key = str(payload.get("key_id") or "").strip()
        secret = str(payload.get("secret_key") or "").strip()
        if key and secret:
            return [FeedCredential("vault", key, secret)]
    except Exception:
        return []
    return []


def _env_credentials(candidates: Iterable[object] | None = None) -> list[FeedCredential]:
    """Every configured Alpaca key except the documented-dead ones.

    ``settings.clock_credential_candidates()`` is used rather than
    ``credentials_for_profile()`` because it is the one config API that ignores
    the ALPACA_ACCOUNT_PROFILES allow-list - and that allow-list pins this repo
    to paper3, which is dead.
    """
    if candidates is None:
        try:
            from config import settings

            candidates = settings.clock_credential_candidates("paper")
        except Exception:
            return []
    out: list[FeedCredential] = []
    for candidate in candidates or []:
        profile_id = str(getattr(candidate, "profile_id", "") or "").strip().lower()
        key = str(getattr(candidate, "key", "") or "").strip()
        secret = str(getattr(candidate, "secret", "") or "").strip()
        if not key or not secret:
            continue
        if profile_id in DEAD_PROFILE_IDS:
            continue
        out.append(FeedCredential(profile_id or "env", key, secret))
    return out


def resolve_credentials(candidates: Iterable[object] | None = None) -> list[FeedCredential]:
    """Ordered credentials to try: vault first, then live env profiles.

    Returned as a list, not a single credential, so a 401 on the first key
    falls through to the next instead of blanking the board.
    """
    resolved: list[FeedCredential] = []
    seen: set[tuple[str, str]] = set()
    for credential in list(_vault_credentials()) + list(_env_credentials(candidates)):
        token = (credential.key, credential.secret)
        if token in seen:
            continue
        seen.add(token)
        resolved.append(credential)
    return resolved


# ---------------------------------------------------------------------------
# TTL cache
# ---------------------------------------------------------------------------

_CACHE: dict[tuple, tuple[float, pd.DataFrame]] = {}
_CACHE_LOCK = threading.Lock()


class _KindStore:
    """One tape kind's held frames. Guarded by ``_CACHE_LOCK``."""

    __slots__ = ("tapes", "last_used", "full_built_at")

    def __init__(self, built_at: float) -> None:
        self.tapes: dict[str, pd.DataFrame] = {}
        self.last_used: dict[str, float] = {}
        # Wall-clock stamp of the last FULL build; the split heal keys on it.
        self.full_built_at: float = built_at


_STORE: dict[str, _KindStore] = {}


def clear_cache() -> None:
    """Drop the TTL cache AND the incremental store (split-safe invalidation)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _STORE.clear()


def _cache_get(key: tuple) -> pd.DataFrame | None:
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if entry is None:
        return None
    stored_at, frame = entry
    if _monotonic() - stored_at > CACHE_TTL_SECONDS:
        return None
    return frame


def _cache_put(key: tuple, frame: pd.DataFrame) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (_monotonic(), frame)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _default_get() -> Callable:
    import requests

    # Connection reuse matters: a fresh TLS context costs ~400ms on this box.
    session = requests.Session()
    return session.get


def _clamp_end(end: datetime, feed: str, now: datetime | None = None) -> datetime:
    delay = FEED_RECENT_DELAY_MINUTES.get(feed, 20)
    current = now or datetime.now(timezone.utc)
    return min(end, current - timedelta(minutes=delay))


def _honour_rate_limit(response: object) -> None:
    """Pause only when Alpaca says we are nearly out of budget.

    The limit is published per response (X-Ratelimit-Remaining / -Reset); read
    it instead of hardcoding 200/min, which is plan-dependent.
    """
    headers = getattr(response, "headers", None) or {}
    try:
        remaining = int(headers.get("X-Ratelimit-Remaining"))
    except (TypeError, ValueError):
        return
    if remaining > RATE_LIMIT_FLOOR:
        return
    try:
        reset = float(headers.get("X-Ratelimit-Reset"))
    except (TypeError, ValueError):
        return
    wait = reset - time.time()
    if wait > 0:
        _sleep(min(wait, RATE_LIMIT_MAX_SLEEP))


def _fetch_batch(
    symbols: list[str],
    *,
    timeframe: str,
    start: datetime,
    end: datetime,
    feed: str,
    credentials: list[FeedCredential],
    get: Callable,
    adjustment: str | None = None,
) -> dict[str, list[dict]]:
    """Raw bar rows for one batch of symbols, paged to exhaustion.

    Tries each credential in turn; an auth rejection or a transport failure
    retires that credential for this batch and moves to the next one. Raises
    FeedError only when every credential is spent - the caller turns that into
    per-symbol errors rather than letting it escape.
    """
    if not symbols or not credentials:
        raise FeedError("no symbols or no usable credentials")
    if end <= start:
        return {symbol: [] for symbol in symbols}

    params = {
        "symbols": ",".join(symbols),
        "timeframe": timeframe,
        "start": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": PAGE_LIMIT,
        "feed": feed,
    }
    if adjustment:
        params["adjustment"] = adjustment

    last_error = "unknown error"
    for credential in credentials:
        out: dict[str, list[dict]] = {symbol: [] for symbol in symbols}
        token: str | None = None
        pages = 0
        failed = False
        while pages < MAX_PAGES_PER_BATCH:
            pages += 1
            query = dict(params)
            if token:
                query["page_token"] = token
            response = None
            for attempt in range(RETRY_ATTEMPTS):
                try:
                    response = get(
                        BARS_URL,
                        params=query,
                        headers=credential.headers,
                        timeout=HTTP_TIMEOUT_SECONDS,
                    )
                    break
                except Exception as exc:  # transient: reset connections are routine
                    last_error = f"{credential.profile_id}: {exc}"
                    response = None
                    if attempt + 1 < RETRY_ATTEMPTS:
                        _sleep(RETRY_BACKOFF_SECONDS * (attempt + 1))
            if response is None:
                failed = True
                break
            status = int(getattr(response, "status_code", 200) or 200)
            if status != 200:
                # 401/403 = this key is dead or unsubscribed; anything else is
                # a bad request. Either way the next credential gets a turn.
                last_error = f"{credential.profile_id}: HTTP {status}"
                failed = True
                break
            _honour_rate_limit(response)
            try:
                payload = response.json()
            except Exception as exc:
                last_error = f"{credential.profile_id}: bad payload ({exc})"
                failed = True
                break
            if not isinstance(payload, dict):
                last_error = f"{credential.profile_id}: bad payload"
                failed = True
                break
            for symbol, rows in (payload.get("bars") or {}).items():
                if isinstance(rows, list):
                    out.setdefault(str(symbol).upper(), []).extend(rows)
            token = payload.get("next_page_token") or None
            if not token:
                break
        if not failed:
            return out
    raise FeedError(last_error)


# ---------------------------------------------------------------------------
# Frame assembly (the only pandas allowed in this module)
# ---------------------------------------------------------------------------


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=CANONICAL_COLUMNS)


def _merge_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    usable = [frame for frame in frames if frame is not None and not frame.empty]
    if not usable:
        return _empty_frame()
    merged = usable[0] if len(usable) == 1 else pd.concat(usable, ignore_index=True)
    # keep="last": a restated bar (or the SIP copy of an overlapping stamp)
    # should win over the earlier copy.
    merged = merged.drop_duplicates(subset=["timestamp"], keep="last")
    return merged.sort_values("timestamp").reset_index(drop=True)


def _chunk(symbols: list[str], size: int) -> list[list[str]]:
    return [symbols[index:index + size] for index in range(0, len(symbols), size)]


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols or []:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


# ---------------------------------------------------------------------------
# Incremental store internals
# ---------------------------------------------------------------------------


def _store_lookup(cache_kind: str, pending: list[str]) -> tuple[_KindStore, dict[str, pd.DataFrame]]:
    """The kind's store plus the held tapes for ``pending``.

    Applies the split heal (drop the whole kind when ``full_built_at`` is
    older than FULL_REBUILD_SECONDS), stamps ``last_used`` for every pending
    symbol, and prunes symbols idle past STORE_PRUNE_SECONDS - in that order,
    so a currently-requested symbol can never prune itself.
    """
    wall = _wall_clock()
    stamp = _monotonic()
    with _CACHE_LOCK:
        store = _STORE.get(cache_kind)
        if store is not None and wall - store.full_built_at > FULL_REBUILD_SECONDS:
            # THE SPLIT HEAL - see the module docstring. Everything held for
            # this kind may carry a pre-split adjustment; none of it survives.
            store = None
        if store is None:
            store = _STORE[cache_kind] = _KindStore(wall)
        for symbol in pending:
            store.last_used[symbol] = stamp
        idle = [
            symbol
            for symbol, used in store.last_used.items()
            if stamp - used > STORE_PRUNE_SECONDS
        ]
        for symbol in idle:
            store.tapes.pop(symbol, None)
            store.last_used.pop(symbol, None)
        held = {}
        for symbol in pending:
            tape = store.tapes.get(symbol)
            if tape is not None and not tape.empty:
                held[symbol] = tape
    return store, held


def _tail_start(
    held: dict[str, pd.DataFrame],
    tail_symbols: list[str],
    timeframe: str,
    required_feed: str,
) -> datetime:
    """Where the shared tail fetch begins: behind the OLDEST newest-held bar.

    One start for the whole batch (the request is batched per 200 symbols),
    so a symbol that fell behind - a halted name, or one whose last tail
    failed - drags the window back for everyone. Bounded by the 12h full
    rebuild, and a few extra bars for 358 symbols is still pages, not
    hundreds of pages.
    """
    newest = min(held[symbol]["timestamp"].iloc[-1] for symbol in tail_symbols)
    span = _TIMEFRAME_SPAN_SECONDS.get(timeframe, 30 * 60)
    overlap = timedelta(
        seconds=TAIL_OVERLAP_BARS * span
        + FEED_RECENT_DELAY_MINUTES.get(required_feed, 20) * 60
    )
    return newest.to_pydatetime().astimezone(timezone.utc) - overlap


def _splice_tail(held: pd.DataFrame, fetched: pd.DataFrame) -> pd.DataFrame:
    """Fold a freshly fetched tail onto a held tape of the SAME resolution.

    Held bars at or after the earliest fetched stamp are dropped and the
    fetched bars appended: the refetched window replaces the held copy
    wholesale, so the previously-partial last bar is replaced by its complete
    restatement rather than duplicated.

    This is deliberately NOT ``premarket_scanner.merge_live_tail``. That
    helper appends only STRICTLY NEWER live bars because its two sides are
    different resolutions - a 1-minute live bar must never clobber a
    30-minute cached bucket. Here both sides are the same timeframe from the
    same endpoint, so replacement is the correct merge.
    """
    cutoff = fetched["timestamp"].iloc[0]
    kept = held[held["timestamp"] < cutoff]
    if kept.empty:
        return fetched
    return pd.concat([kept, fetched], ignore_index=True)


_SCHWAB_LOCK = threading.Lock()
_SCHWAB_CLIENT = None
_SCHWAB_UNAVAILABLE = False


def _schwab_client():
    """The shared market-data client, or ``None`` if Schwab cannot be used.

    Cached because constructing one re-reads and parses the token file, which
    would otherwise happen once per symbol per build. ``None`` is sticky for an
    import or configuration failure, but NOT for a transient error: a call that
    raises is handled by the caller, which simply leaves that tape alone for
    the round and retries on the next build.
    """
    global _SCHWAB_CLIENT, _SCHWAB_UNAVAILABLE
    with _SCHWAB_LOCK:
        if _SCHWAB_CLIENT is not None or _SCHWAB_UNAVAILABLE:
            return _SCHWAB_CLIENT
        try:
            from data.schwab_client import SchwabClient

            client = SchwabClient()
            if not client.configured:
                _SCHWAB_UNAVAILABLE = True
                return None
            _SCHWAB_CLIENT = client
        except Exception:
            # No Schwab, no correction -- but the board must still build.
            _SCHWAB_UNAVAILABLE = True
            return None
        return _SCHWAB_CLIENT


def _schwab_volume_map(symbols, timeframe, now, client=None):
    """``{symbol: {epoch_seconds: real_volume}}`` for the recent tail.

    ``client`` is injectable so a test can exercise this without a network or
    credentials; production passes nothing and gets the shared cached client.

    Best-effort in every direction: no client, an unsupported timeframe, a
    raising call, or an empty frame all yield an empty map for that symbol,
    and an empty map means "change nothing". A symbol that fails here keeps
    its thin IEX volume rather than blocking the build -- a slightly wrong
    RVOL is survivable; a blank board during market hours is not.
    """
    if not SCHWAB_VOLUME_ENABLED:
        return {}
    if timeframe not in SCHWAB_VOLUME_TIMEFRAMES or not symbols:
        return {}
    if client is None:
        client = _schwab_client()
    if client is None:
        return {}
    start = (now or datetime.now(timezone.utc)) - timedelta(
        minutes=SCHWAB_VOLUME_LOOKBACK_MINUTES
    )

    def one(symbol):
        try:
            frame = client._get_price_history(symbol, timeframe, start, None)
        except Exception:
            return symbol, {}
        if frame is None or getattr(frame, "empty", True):
            return symbol, {}
        out = {}
        try:
            pairs = zip(frame["timestamp"], frame["volume"])
        except Exception:
            return symbol, {}
        for stamp, volume in pairs:
            try:
                key = int(pd.Timestamp(stamp).timestamp())
                value = float(volume)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value > 0:
                out[key] = value
        return symbol, out

    workers = max(1, min(SCHWAB_VOLUME_WORKERS, len(symbols)))
    try:
        with ThreadPoolExecutor(
            max_workers=workers, thread_name_prefix="momx-schwab-vol"
        ) as pool:
            return dict(pool.map(one, symbols))
    except Exception:
        return {}


def _apply_schwab_volume(frame, truth):
    """Replace the thin IEX tail's volume with Schwab's measured volume.

    Only bars stamped ``feed="iex"`` are touched. The SIP body already holds
    real consolidated volume and must never be overwritten by a second source:
    two sources disagreeing by a few percent on settled bars would make the
    tape jitter as each bar aged from one owner to the other.

    Two guards carry the safety here:

    * A bar Schwab has no row for is LEFT ALONE. Fabricating a value for a
      halted or illiquid name is exactly the failure this change exists to
      remove.
    * A replacement SMALLER than what we already hold is REJECTED. IEX volume
      is a strict subset of consolidated volume, so a smaller "truth" means the
      two sides are not describing the same bar -- a boundary or timezone
      mismatch -- and applying it would quietly UNDERSTATE the very spike we
      are trying to catch.

    Returns ``frame`` unchanged (the same object) when nothing applies, so a
    failed or empty correction can never make the tape worse than it was.
    """
    if not truth or frame is None or frame.empty or "volume" not in frame.columns:
        return frame
    if "feed" in frame.columns:
        feeds = list(frame["feed"])
    else:
        # No feed column means nothing was ever marked as the thin tail, so
        # there is nothing this function is entitled to touch.
        return frame
    replacements = {}
    for position, (stamp, feed_name, held) in enumerate(
        zip(frame["timestamp"], feeds, frame["volume"])
    ):
        if feed_name != SCHWAB_VOLUME_REPLACES_FEED:
            continue
        try:
            key = int(pd.Timestamp(stamp).timestamp())
            current = float(held)
        except (TypeError, ValueError):
            continue
        real = truth.get(key)
        if real is None or not math.isfinite(real):
            continue
        if math.isfinite(current) and real < current:
            continue
        replacements[position] = real
    if not replacements:
        return frame
    patched = frame.copy()
    volumes = list(patched["volume"])
    marks = list(feeds)
    for position, value in replacements.items():
        volumes[position] = value
        marks[position] = SCHWAB_VOLUME_FEED_MARK
    patched["volume"] = volumes
    patched["feed"] = marks
    return patched


def _merge_iex_tail(base: pd.DataFrame, iex: pd.DataFrame) -> pd.DataFrame:
    """Extend a SIP-built tape with the IEX bars SIP does not have yet.

    SIP is complete but recency-blocked (~20 min); IEX is unblocked but its
    volume is IEX-only. So only bars STRICTLY NEWER than the newest SIP bar are
    taken from IEX - SIP always wins on overlap, its volume is the real one -
    and each appended bar is stamped ``feed="iex"`` so downstream can tell the
    thin-volume tail from the settled SIP body. The kept SIP body is stamped
    ``feed="sip"`` (missing values only, so a bar SIP has not yet re-owned keeps
    its earlier ``iex`` mark until SIP catches up).

    Best-effort by construction: an empty IEX frame, or one with nothing newer,
    returns ``base`` UNCHANGED (same object) so a failed IEX leg can never make
    the tape worse than the SIP-only board. This does NOT do the split-safe
    same-resolution replacement - that is ``_splice_tail``'s job on the next
    incremental build, which drops the thin IEX tail bar and re-owns it from SIP
    once SIP's recency block clears (keep="last", the real volume wins).
    """
    if iex is None or iex.empty or base is None or base.empty:
        return base
    newest = base["timestamp"].iloc[-1]
    fresh = iex[iex["timestamp"] > newest]
    if fresh.empty:
        return base
    body = base.copy()
    if "feed" in body.columns:
        body["feed"] = body["feed"].fillna("sip")
    else:
        body["feed"] = "sip"
    fresh = fresh.copy()
    fresh["feed"] = "iex"
    # keep="first" with the SIP body first is belt-and-braces: ``fresh`` is
    # already strictly newer so there is no overlap, but if one ever appeared
    # SIP must still win.
    merged = pd.concat([body, fresh], ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="first")
    return merged.sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------------------
# The tape builder
# ---------------------------------------------------------------------------


def _fetch_tape(
    symbols: Iterable[str],
    *,
    cache_kind: str,
    timeframe: str,
    feeds: tuple[str, ...],
    required_feed: str,
    start: datetime,
    adjustment: str | None = None,
    iex_tail_feed: str | None = None,
    now: datetime | None = None,
    get: Callable | None = None,
    credentials: list[FeedCredential] | None = None,
    batch_size: int = BATCH_SIZE,
    max_workers: int = MAX_WORKERS,
    use_cache: bool = True,
    schwab_volume: bool = True,
) -> FeedResult:
    wanted = _normalize_symbols(symbols)
    bars: dict[str, pd.DataFrame] = {}
    errors: dict[str, str] = {}
    if not wanted:
        return FeedResult(bars, errors)

    def cache_key(symbol: str) -> tuple:
        return (cache_kind, timeframe, feeds, symbol)

    pending: list[str] = []
    for symbol in wanted:
        cached = _cache_get(cache_key(symbol)) if use_cache else None
        if cached is not None:
            bars[symbol] = cached
        else:
            pending.append(symbol)
    if not pending:
        return FeedResult(bars, errors)

    # Incremental split: a symbol with a held tape only needs the tail; one
    # without (first build, new to the universe, or its earlier full fetch
    # failed) needs full depth. ``use_cache=False`` bypasses the store in both
    # directions, exactly like it bypasses the TTL cache.
    store: _KindStore | None = None
    held: dict[str, pd.DataFrame] = {}
    if use_cache:
        store, held = _store_lookup(cache_kind, pending)
    full_symbols = [symbol for symbol in pending if symbol not in held]
    tail_symbols = [symbol for symbol in pending if symbol in held]

    if credentials is None:
        credentials = resolve_credentials()
    if not credentials:
        for symbol in tail_symbols:
            bars[symbol] = held[symbol]  # a stale tape beats a blank board
        for symbol in full_symbols:
            errors[symbol] = "no usable Alpaca credentials"
        return FeedResult(bars, errors)
    if get is None:
        get = _default_get()

    size = max(1, int(batch_size))
    plans: list[tuple[list[str], datetime, bool]] = [
        (batch, start, False) for batch in _chunk(full_symbols, size)
    ]
    if tail_symbols:
        tail_from = _tail_start(held, tail_symbols, timeframe, required_feed)
        plans.extend((batch, tail_from, True) for batch in _chunk(tail_symbols, size))

    jobs: list[tuple[str, list[str], datetime, bool]] = [
        (feed, batch, batch_start, is_tail)
        for feed in feeds
        for batch, batch_start, is_tail in plans
    ]

    # The IEX live tail (intraday only): one short, unclamped bulk request per
    # batch over ALL pending symbols - both the full-depth first builds and the
    # incremental tails need their newest ~IEX_TAIL_MINUTES extended past SIP's
    # recency block. Same batching as the SIP legs; never per symbol. It is a
    # best-effort supplement - its failure is handled exactly like BOATS below.
    if iex_tail_feed:
        iex_from = (now or datetime.now(timezone.utc)) - timedelta(minutes=IEX_TAIL_MINUTES)
        jobs.extend((iex_tail_feed, batch, iex_from, False) for batch in _chunk(pending, size))

    def run(job: tuple[str, list[str], datetime, bool]):
        feed, batch, batch_start, is_tail = job
        base_now = now or datetime.now(timezone.utc)
        if feed == iex_tail_feed:
            # IEX has no recency block; clamp only the still-forming minute.
            end = base_now - timedelta(minutes=IEX_TAIL_END_CLAMP_MINUTES)
        else:
            end = _clamp_end(base_now, feed, now)
        try:
            payload = _fetch_batch(
                batch,
                timeframe=timeframe,
                start=batch_start,
                end=end,
                feed=feed,
                credentials=credentials,
                get=get,
                adjustment=adjustment,
            )
            return feed, batch, is_tail, payload, None
        except Exception as exc:  # one bad batch must not blank the board
            return feed, batch, is_tail, {}, str(exc)

    workers = max(1, min(int(max_workers), MAX_WORKERS, len(jobs)))
    if workers == 1 or len(jobs) == 1:
        results = [run(job) for job in jobs]
    else:
        # Per-call pool, shut down on exit: this module owns no live threads.
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="momx-feed") as pool:
            results = list(pool.map(run, jobs))

    rows: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in pending}
    iex_rows: dict[str, list[pd.DataFrame]] = {symbol: [] for symbol in pending}
    failures: dict[str, str] = {}
    tail_failed: set[str] = set()
    for feed, batch, is_tail, payload, failure in results:
        if failure is not None:
            if feed == required_feed:
                if is_tail:
                    # A held symbol whose tail fetch failed keeps its tape
                    # and is NOT an error - stale beats blank.
                    tail_failed.update(batch)
                else:
                    for symbol in batch:
                        failures.setdefault(symbol, failure)
            continue  # a supplemental feed (BOATS or the IEX tail) is never fatal
        target = iex_rows if feed == iex_tail_feed else rows
        for symbol, raw in payload.items():
            if symbol in target and raw:
                target[symbol].append(boats_bars_to_frame(raw))

    # One parallel Schwab pass for the whole batch, rather than a call per
    # symbol inside the loop. Measured: 40 symbols in 0.77s, ~6.9s for 357.
    # ``schwab_volume=False`` skips this leg entirely -- one uncached Schwab
    # price-history call per symbol is too much upstream cost to pay for a
    # caller (the grading fetch) that never reads volume. See fetch_5m.
    schwab_map = _schwab_volume_map(list(pending), timeframe, now) if schwab_volume else {}

    for symbol in pending:
        fetched = _merge_frames(rows.get(symbol) or [])
        if symbol in held:
            if symbol in tail_failed or fetched.empty:
                frame = held[symbol]  # unchanged; the next call retries the tail
            else:
                frame = _splice_tail(held[symbol], fetched)
        elif fetched.empty:
            # Not cached: a symbol that failed should be retried on the next
            # call, not remembered as empty for the whole TTL window.
            errors[symbol] = failures.get(symbol, "no bars returned")
            continue
        else:
            frame = fetched
        # Extend the SIP body with the IEX live tail (intraday only; ``iex_rows``
        # is empty on the daily tape and for a failed IEX leg, so this is a
        # no-op that returns ``frame`` unchanged in both cases).
        frame = _merge_iex_tail(frame, _merge_frames(iex_rows.get(symbol) or []))
        # The IEX tail above carries ~2% of real volume. Swap in Schwab's
        # measured volume for exactly those bars BEFORE anything caches or
        # reads them, so RVOL divides like against like. An empty map for a
        # symbol returns the frame unchanged.
        frame = _apply_schwab_volume(frame, schwab_map.get(symbol) or {})
        bars[symbol] = frame
        if use_cache:
            _cache_put(cache_key(symbol), frame)
            if store is not None:
                with _CACHE_LOCK:
                    store.tapes[symbol] = frame
    return FeedResult(bars, errors)


def _session_start(now: datetime | None, *, days: int) -> datetime:
    current = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    return (current - timedelta(days=max(1, int(days)))).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_5m(
    symbols: Iterable[str], days: int = 5, *, schwab_volume: bool = True, **kwargs
) -> FeedResult:
    """Extended-hours 5-minute bars (SIP 04:00-20:00 + BOATS 20:00-04:00).

    ``schwab_volume=False`` skips the Schwab volume-correction pass (one
    uncached Schwab price-history call per symbol). The board's live callers
    always want the corrected volume and get the default ``True``; the
    grading fetch (``momx/service.py::_fetch_5m_bars``) never reads volume at
    all and passes ``False`` so a 15-minute catch-up retry over unscored
    symbols, all day, for up to 10 days, cannot turn into a sustained burst
    of uncached Schwab calls -- this app's home IP has been blocked by
    Schwab's CDN before for far smaller bursts.
    """
    now = kwargs.pop("now", None)
    return _fetch_tape(
        symbols,
        cache_kind="5m",
        adjustment=INTRADAY_ADJUSTMENT,
        timeframe=TIMEFRAME_5M,
        feeds=(INTRADAY_PRIMARY_FEED, INTRADAY_OVERNIGHT_FEED),
        required_feed=INTRADAY_PRIMARY_FEED,
        iex_tail_feed=INTRADAY_IEX_TAIL_FEED,
        start=_session_start(now, days=days),
        now=now,
        schwab_volume=schwab_volume,
        **kwargs,
    )


#: Calendar days of 30-minute history. This tape feeds the 1h/2h/4h tapes, and
#: 4h RVOL needs RVOL_MIN_BARS+1 = 51 buckets or the 4h column silently goes
#: NULL. Measured 2026-09-01 across the thinnest names in the universe:
#: 30 days -> 104 buckets worst case, 21 -> 76, 14 -> 51 (no margin at all).
#: 21 keeps ~49% headroom while cutting the fetch from 120.1s to roughly 88s
#: for 357 symbols, which was the single largest component of the Watchlist
#: build. Do not drop below ~18 without re-running that measurement: the day
#: it breaks will be a holiday week, not the day of the edit.
INTRADAY_30M_DAYS = 21


def fetch_30m(symbols: Iterable[str], days: int = INTRADAY_30M_DAYS, **kwargs) -> FeedResult:
    """Extended-hours 30-minute bars (SIP 04:00-20:00 + BOATS 20:00-04:00)."""
    now = kwargs.pop("now", None)
    return _fetch_tape(
        symbols,
        cache_kind="30m",
        adjustment=INTRADAY_ADJUSTMENT,
        timeframe=TIMEFRAME_30M,
        feeds=(INTRADAY_PRIMARY_FEED, INTRADAY_OVERNIGHT_FEED),
        required_feed=INTRADAY_PRIMARY_FEED,
        iex_tail_feed=INTRADAY_IEX_TAIL_FEED,
        start=_session_start(now, days=days),
        now=now,
        **kwargs,
    )


def fetch_daily(symbols: Iterable[str], years: int = 3, **kwargs) -> FeedResult:
    """Regular-hours daily bars, unadjusted (house convention)."""
    now = kwargs.pop("now", None)
    return _fetch_tape(
        symbols,
        cache_kind="daily",
        timeframe=TIMEFRAME_DAILY,
        feeds=(DAILY_FEED,),
        required_feed=DAILY_FEED,
        start=_session_start(now, days=int(round(365.25 * max(1, int(years))))),
        adjustment=DAILY_ADJUSTMENT,
        now=now,
        **kwargs,
    )


__all__ = [
    "BATCH_SIZE",
    "CACHE_TTL_SECONDS",
    "FULL_REBUILD_SECONDS",
    "IEX_TAIL_MINUTES",
    "INTRADAY_IEX_TAIL_FEED",
    "MAX_WORKERS",
    "STORE_PRUNE_SECONDS",
    "TAIL_OVERLAP_BARS",
    "FeedCredential",
    "FeedError",
    "FeedResult",
    "clear_cache",
    "fetch_5m",
    "fetch_30m",
    "fetch_daily",
    "resolve_credentials",
]
