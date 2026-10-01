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
    fetches only the Alpaca TAIL: from the oldest held newest-bar across the batch,
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
import json
import math
import gzip
import os
import pickle
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable, NamedTuple
from zoneinfo import ZoneInfo

import numpy as np
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
SCHWAB_VOLUME_TIMEFRAMES = ("5Min", "30Min", "1Day")

# --- SCHWAB ONLY (2026-09-30, his order: "for scanner use only TOS dont use
# Alpaca") -----------------------------------------------------------------
# Every scanner tape - 5m, 30m AND daily, price AND volume - comes straight
# from Schwab price history: no SIP/BOATS/IEX legs, no SIP price override.
# Why it was needed, measured: TLN 2026-09-30 passed our scan at 23:00 ET
# while his TOS returned nothing; Schwab's 16:00 candle closed 312.69 (the
# closing auction, no after-hours trades) but the SIP price override made it
# 316.61, which passed the 1h close gate. Known consequences, accepted:
#   * Schwab has no CURRENT-day bars before 07:00 ET, so 04:00-07:00 today
#     is empty until Schwab serves it.
#   * Depth is Schwab's (~20 days of 30m, ~10 of 5m, 3 years daily).
#   * Schwab down -> the last held tape is served (stale beats blank).
# The grading fetch (schwab_volume=False) is unchanged.
#
# 2026-09-30, later: his "TOS" checkbox on the scanner toolbar picks this per
# deployment (artifacts/momx_data_source.json, see set_data_source). Ticked =
# "tos" = SCHWAB_ONLY True, RATE-PACED (see TOS_REQUESTS_PER_MINUTE).
# Unticked = "alpaca" = the pre-2026-09-29 tape: SIP + BOATS + IEX tail with
# Schwab used ONLY for the IEX-tail volume fix (default lookback window) and
# the premarket tail. Decided, not inherited: alpaca mode no longer calls
# _overlay_schwab / _sip_prices and never sends full-depth Schwab starts -
# that overlay was a third "mostly Schwab" mode that matched neither his TOS
# (SIP prices, TLN 316.61 vs 312.69) nor plain Alpaca, and it cost the same
# 1,100 full-depth Schwab calls per build that 429'd tonight. The functions
# and SCHWAB_OWNS_INTRADAY stay (unit-tested) as the way back if wanted.
# Set from the saved file at import, below get_data_source().
SCHWAB_ONLY = True
SCHWAB_VOLUME_WORKERS = 12
_SCHWAB_REQUEST_SLOTS = threading.BoundedSemaphore(SCHWAB_VOLUME_WORKERS)
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

# --- Schwab PREMARKET tail (2026-09-29) --------------------------------------
# The IEX live tail above is ~empty premarket (feed=iex: "1 bar vs 36"), so
# before 09:30 the tape ENDED at SIP's ~20-minute recency block. Measured on
# 2026-09-29: the scanner saw premarket CALL2H / CALL4H a median 16 minutes
# after the candle closed (75th pct 27-38 min), while his chart - Schwab - had
# them at once. The Schwab price-history call made for the volume truth above
# already returns full OHLCV candles for the last hour, so premarket the bars
# newer than the tape's newest are APPENDED from it (stamped feed="schwab").
# No extra Schwab request. Regular hours are untouched (the IEX tail covers
# them). Schwab has no current-day bars before 07:00 ET, so 04:00-07:00 still
# waits on SIP. SIP re-owns each bar once its block clears (_splice_tail).
SCHWAB_PREMARKET_TAIL = True

# --- Schwab OWNS the intraday tapes (2026-09-29) ------------------------------
# The trader: "You should take always TOS api key". Measured the same evening:
# Alpaca SIP counts 30-110% MORE shares than Schwab for the same candle (AMGN
# 4h 09:00: 694,703 vs 477,926; PM 1,459,454 vs 695,664), so every RVOL
# z-score and the 4h volume gate read a different tape than his TOS. Now the
# Schwab price-history call requests the SAME full depth on initial builds
# and refreshes, and `_overlay_schwab` makes Schwab the
# owner of every ET date it covers, from its first to its last candle that
# date. Alpaca only fills what Schwab does not have: dates older than
# Schwab's depth (~20 days of 30m), the current day's 04:00-07:00 hole (see
# the premarket-tail note above), overnight bars outside Schwab's span, and
# the whole tape if Schwab is down. Kill switch below; tests turn Schwab off.
SCHWAB_OWNS_INTRADAY = True
PREMARKET_TAIL_FROM_MIN, PREMARKET_TAIL_UNTIL_MIN = 4 * 60, 9 * 60 + 30

# Alpaca mode corrects only the intraday IEX tail; the daily tape has no IEX
# tail, so a daily Schwab call there would buy nothing (pre-09-29 behaviour).
ALPACA_SCHWAB_TIMEFRAMES = ("5Min", "30Min")

# --- TOS mode is RATE-PACED (2026-09-30) --------------------------------------
# Measured, not assumed: a full TOS build is ~368 symbols x 3 tapes ~= 1,100
# Schwab price-history calls. The first Schwab-only build tonight left 124
# symbols EMPTY - Schwab answered 429 and data/schwab_rate_limit.GATE then
# refused the rest. Schwab documents 120/minute; a sustained ~459/minute from
# this worker preceded the 2026-09-04 Akamai IP ban. So TOS mode spends at most
# this many calls per rolling 60s across all three tapes (headroom under 120
# for the charts), never waits for a slot, and serves the held tape for every
# symbol it could not afford this round (stale beats blank). At 90/min a full
# cold load takes ~12 minutes; after that each tape is re-read every few
# minutes, missing tapes first, then the stalest.
TOS_REQUESTS_PER_MINUTE = 90
#: A held daily tape is not re-requested younger than this: today's daily bar
#: moves slowly enough, and every daily call is a call 5m/30m cannot have.
TOS_DAILY_REFRESH_SECONDS = 1800.0
#: A symbol Schwab answered with no candles is retried no sooner than this.
TOS_EMPTY_RETRY_SECONDS = 600.0
#: A held 5m/30m tape is re-read no sooner than this (small lists hogged the budget).
TOS_MIN_REFETCH_SECONDS = 300.0
#: A tape kind's last stated demand counts toward the fair split this long.
TOS_DEMAND_SECONDS = 180.0
#: The tapes that share the TOS budget (the cache kinds of the three fetches).
TOS_KINDS = ("5m", "30m", "daily")
TOS_QUEUED_ERROR = "tos: queued (rate-paced)"
DATA_SOURCES = ("tos", "alpaca")
DEFAULT_DATA_SOURCE = "tos"   # his latest order, b80468b
DATA_SOURCE_FILENAME = "momx_data_source.json"
DATA_SOURCE_PATH_ENV = "AGX_MOMX_DATA_SOURCE_PATH"

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

    __slots__ = ("tapes", "last_used", "full_built_at", "fetched_at", "empty_at")

    def __init__(self, built_at: float) -> None:
        self.tapes: dict[str, pd.DataFrame] = {}
        self.last_used: dict[str, float] = {}
        # Wall-clock stamp of the last FULL build; the split heal keys on it.
        self.full_built_at: float = built_at
        # TOS mode: wall-clock stamp of each symbol's last successful Schwab
        # fetch - the "stalest first" order and the per-symbol split heal.
        self.fetched_at: dict[str, float] = {}
        # TOS mode: when Schwab last answered a symbol with NO candles. Without
        # it a symbol Schwab never serves stays first in the "no held tape"
        # queue forever and spends budget every build (review 2026-09-30).
        self.empty_at: dict[str, float] = {}


_STORE: dict[str, _KindStore] = {}


def clear_cache() -> None:
    """Drop the TTL cache AND the incremental store (split-safe invalidation)."""
    with _CACHE_LOCK:
        _CACHE.clear()
        _STORE.clear()
        _STORES_BY_SOURCE.clear()


# ---------------------------------------------------------------------------
# Data source: "tos" (Schwab only, paced) or "alpaca" - the toolbar checkbox
# ---------------------------------------------------------------------------

_SOURCE_LOCK = threading.Lock()


def data_source_path():
    """``artifacts/momx_data_source.json``; the env var redirects it (tests)."""
    from pathlib import Path

    override = os.environ.get(DATA_SOURCE_PATH_ENV, "").strip()
    if override:
        return Path(override)
    from config import ARTIFACTS_DIR

    return Path(ARTIFACTS_DIR) / DATA_SOURCE_FILENAME


#: His order 2026-10-01 ("remove the TOS checkbox and run TOS data
#: permanently - do it"): the scanner is TOS (Schwab) data only. Measured
#: against his TOS lists that morning: TOS mode 33/49 vs Alpaca 18/49. The
#: switch machinery stays (tested) behind this lock in case he wants it back.
TOS_LOCKED = True


def get_data_source() -> str:
    """The saved source; a missing or corrupt file means the default."""
    if TOS_LOCKED:
        return "tos"
    try:
        raw = json.loads(data_source_path().read_text(encoding="utf-8"))
        source = str(raw.get("source") or "").strip().lower()
    except Exception:  # noqa: BLE001 - never let a bad file break the scanner
        return DEFAULT_DATA_SOURCE
    return source if source in DATA_SOURCES else DEFAULT_DATA_SOURCE


def set_data_source(source: object) -> str:
    """Persist and apply the source. The caches are dropped only on a real
    change: a tape built from one source must never be served as the other."""
    global SCHWAB_ONLY
    value = str(source or "").strip().lower()
    if value not in DATA_SOURCES:
        raise ValueError(f"source must be one of {', '.join(DATA_SOURCES)}")
    if TOS_LOCKED and value != "tos":
        raise ValueError("The scanner runs on TOS data permanently.")
    with _SOURCE_LOCK:
        path = data_source_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({
            "source": value,
            "savedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }), encoding="utf-8")
        os.replace(tmp, path)
        changed = SCHWAB_ONLY != (value == "tos")
        old = "tos" if SCHWAB_ONLY else "alpaca"
        SCHWAB_ONLY = value == "tos"
        if changed:
            # Each source keeps its OWN held tapes (2026-10-01: "taking too much
            # time" - a TOS->Alpaca->TOS flick threw away a fully loaded TOS
            # board and the paced reload took ~17 minutes). The TTL cache is
            # dropped, so no tape from one source is ever served as the other.
            with _CACHE_LOCK:
                _CACHE.clear()
                _STORES_BY_SOURCE[old] = dict(_STORE)
                _STORE.clear()
                _STORE.update(_STORES_BY_SOURCE.pop(value, {}))
    return value


try:
    SCHWAB_ONLY = get_data_source() == "tos"
except Exception:  # noqa: BLE001 - import must never fail over a setting
    SCHWAB_ONLY = DEFAULT_DATA_SOURCE == "tos"


# The TOS budget: one rolling 60s window shared by the 5m/30m/daily tapes.
# The three kinds build in PARALLEL (a74d5e2), so first-come would let one
# kind take all 90 every build and starve the others forever; instead each
# call is capped at its max-min fair share of the window given every kind's
# latest stated demand (an idle daily tape leaves its share to 5m/30m).
_TOS_LOCK = threading.Lock()
_TOS_SPENT: deque = deque()           # (monotonic stamp, kind)
_TOS_DEMAND: dict[str, tuple[float, int]] = {}   # kind -> (stamp, outstanding)
# Alpaca mode spends the same bucket on its IEX-tail volume fix; this orders
# it: (timeframe, symbol) -> wall-clock of that symbol's last Schwab read.
_ALPACA_SCHWAB_AT: dict[tuple[str, str], float] = {}


def _fair_shares(capacity: int, needs: dict[str, int]) -> dict[str, int]:
    """Max-min fair split of ``capacity`` over ``needs`` (water-filling)."""
    shares: dict[str, int] = {}
    left = sorted(needs.items(), key=lambda item: item[1])
    remaining = capacity
    while left:
        even = remaining // len(left)
        kind, need = left[0]
        if need <= even:
            shares[kind] = need
            remaining -= need
            left.pop(0)
            continue
        for index, (kind, _need) in enumerate(left):
            # The integer remainder goes to the first few, so none is lost.
            shares[kind] = even + (1 if index < remaining - even * len(left) else 0)
        break
    return shares


def _tos_take(n: int, kind: str | None = None) -> int:
    """Grant up to ``n`` Schwab calls available RIGHT NOW and record them.

    Never waits: whatever is not granted is simply served from the held tape
    this round. ``kind`` (the tape) enables the fair split described above;
    without it the call competes for the whole window."""
    want = max(0, int(n))
    stamp = _monotonic()
    with _TOS_LOCK:
        while _TOS_SPENT and stamp - _TOS_SPENT[0][0] >= 60.0:
            _TOS_SPENT.popleft()
        free = max(0, TOS_REQUESTS_PER_MINUTE - len(_TOS_SPENT))
        allowed = free
        if kind is not None:
            # EVEN split among the kinds that asked for anything in the last
            # TOS_DEMAND_SECONDS (2026-09-30: the max-min version starved 30m
            # completely - simulated 10 min: 5m 720, daily 180, 30m 0 - and the
            # live board loaded 12 of 368 symbols in 9 minutes). A kind with
            # nothing left to load stops asking and its share goes to the rest.
            used: dict[str, int] = {}
            for _when, spent_kind in _TOS_SPENT:
                used[spent_kind] = used.get(spent_kind, 0) + 1
            # Several LISTS call each tape (Watchlist, Mag7, Movers). A small
            # list asking for nothing must not mark the tape idle while the big
            # one still needs it - measured 2026-10-01: Mag7's "daily wants 0"
            # overwrote Watchlist's 281, 5m/30m took the whole window and the
            # Watchlist's last 14 daily tapes were never asked. So a tape is
            # idle only when NO caller wanted anything for TOS_DEMAND_SECONDS:
            # the record keeps the last POSITIVE ask, a zero only refreshes
            # "asked at".
            previous = _TOS_DEMAND.get(kind)
            if want > 0 or previous is None or stamp - previous[0] > TOS_DEMAND_SECONDS:
                _TOS_DEMAND[kind] = (stamp, want)
            # A tape that has not asked yet is presumed to want its share (so
            # the first caller of a cold window cannot take all of it); only
            # one whose every recent ask was for NOTHING gives its share away.
            idle = {
                other for other, (when, outstanding) in _TOS_DEMAND.items()
                if outstanding <= 0 and stamp - when <= TOS_DEMAND_SECONDS
            }
            active = (set(TOS_KINDS) - idle) | {kind}
            share = TOS_REQUESTS_PER_MINUTE // len(active)
            allowed = min(free, max(0, share - used.get(kind, 0)))
        granted = min(want, allowed)
        _TOS_SPENT.extend((stamp, kind) for _ in range(granted))
    return granted


def _tos_reset() -> None:
    """Empty the TOS budget (tests; a process-wide window leaks between them)."""
    with _TOS_LOCK:
        _TOS_SPENT.clear()
        _TOS_DEMAND.clear()
        _ALPACA_SCHWAB_AT.clear()


# --- TOS tapes on disk (2026-10-01) -------------------------------------------
# A cold TOS board takes ~17 minutes to fill at the safe 90 calls/min, and every
# worker restart started cold. The held TOS tapes are saved per kind (one pickle,
# atomic replace) at most every TOS_PERSIST_SECONDS, ON A BACKGROUND THREAD -
# never in the build (the chart-history save that ran inline is what froze the
# Schwab stream, 7e93092). A new TOS store loads its kind back at once; tapes
# older than FULL_REBUILD_SECONDS are dropped on load (the split heal), and the
# usual stalest-first refresh brings the rest current.
TOS_PERSIST_SECONDS = 60.0
_STORES_BY_SOURCE: dict[str, dict] = {}
_TOS_PERSIST_LOCK = threading.Lock()
_TOS_PERSIST_AT: dict[str, float] = {}
_TOS_PERSIST_THREADS: dict[str, threading.Thread] = {}


def tos_tapes_dir():
    """Next to the data-source file, so tests that redirect it are isolated."""
    return data_source_path().parent / "momx_tos_tapes"


def _write_tos_tapes(kind: str, payload: dict) -> None:
    path = tos_tapes_dir() / f"{kind}.pkl"
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(tmp, "wb") as handle:
            pickle.dump(payload, handle, protocol=pickle.HIGHEST_PROTOCOL)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 - a failed save only costs the next restart
        try:
            os.unlink(tmp)
        except OSError:
            pass


def _persist_tos_store(kind: str, store, *, force: bool = False) -> None:
    """Save ``store`` for ``kind`` off-thread, at most every TOS_PERSIST_SECONDS."""
    wall = _wall_clock()
    with _TOS_PERSIST_LOCK:
        running = _TOS_PERSIST_THREADS.get(kind)
        if not force and (wall - _TOS_PERSIST_AT.get(kind, 0.0) < TOS_PERSIST_SECONDS
                          or (running is not None and running.is_alive())):
            return
        _TOS_PERSIST_AT[kind] = wall
        with _CACHE_LOCK:
            # Shallow copies: tapes are REPLACED on refresh, never mutated.
            payload = {"version": 1, "savedAt": wall,
                       "tapes": dict(store.tapes), "fetchedAt": dict(store.fetched_at)}
        if force:
            _write_tos_tapes(kind, payload)
            return
        thread = threading.Thread(target=_write_tos_tapes, args=(kind, payload),
                                  name=f"momx-tos-save-{kind}", daemon=True)
        _TOS_PERSIST_THREADS[kind] = thread
        thread.start()


def _load_tos_tapes(kind: str, store, wall: float, stamp: float) -> int:
    """Fill a NEW TOS store from disk; returns how many tapes were restored."""
    path = tos_tapes_dir() / f"{kind}.pkl"
    try:
        with open(path, "rb") as handle:
            payload = pickle.load(handle)
        tapes = payload.get("tapes") or {}
        fetched = payload.get("fetchedAt") or {}
    except Exception:  # noqa: BLE001 - missing/corrupt file = a cold start
        return 0
    restored = 0
    for symbol, frame in tapes.items():
        at = float(fetched.get(symbol, 0.0) or 0.0)
        if frame is None or getattr(frame, "empty", True) or wall - at > FULL_REBUILD_SECONDS:
            continue
        store.tapes[symbol] = frame
        store.fetched_at[symbol] = at
        store.last_used[symbol] = stamp
        restored += 1
    return restored


# --- Stream tail (2026-10-01, "do it now") ------------------------------------
# The paced REST budget refreshes each 5m/30m tape only every ~10-15 minutes.
# api_server already receives Schwab CHART_EQUITY 1-minute bars for up to 300
# leased symbols and saves them to artifacts/schwab_stream_chart_history.json.gz
# every 30s (off its event loop since 7e93092). The worker READS that file - no
# api_server change - and extends each tape past its last REST bar with the
# stream's minutes, so a streamed symbol is ~1 minute behind instead of ~15.
#
# Measured before trusting it (2026-10-01, 10 liquid names): stream-built 5m
# buckets equal Schwab price-history volume EXACTLY whenever the stream was up
# (median ratio 1.000; 91.5% exact today, every miss at 08:15-08:20 = the api
# restart, and yesterday's misses = the 10:43/12:25 stream freezes). So the
# stream has GAPS. Guard, per symbol per build: the stream must reproduce the
# tape's last STREAM_VERIFY_BUCKETS complete REST buckets' volume within
# STREAM_VOLUME_TOLERANCE, else the tape is served as REST alone.
#
# Restart gaps (2026-10-01 13:10 ET: 0/368 tapes extended): an api_server
# restart drops a few minutes for EVERY symbol, and a tape whose REST end sat
# in that hole could never verify - the guard now skips buckets the stream
# UNDER-counts (a hole) while scanning back STREAM_VERIFY_LOOKBACK buckets,
# rejects any bucket it OVER-counts (a different feed), and stops appending at
# the first bucket holding a "dead" minute (no symbol at all streamed it).
STREAM_TAIL_ENABLED = True
STREAM_VERIFY_BUCKETS = 3
STREAM_VERIFY_LOOKBACK = 12
STREAM_VOLUME_TOLERANCE = 0.005
#: Older than this, the stream file is treated as dead (api_server down).
STREAM_FILE_MAX_AGE_SECONDS = 180.0
STREAM_FEED_MARK = "stream"
_STREAM_LOCK = threading.Lock()
_STREAM_CACHE: dict = {"mtime": None, "symbols": {}}


def stream_history_path():
    from pathlib import Path

    override = os.environ.get("AGX_MOMX_STREAM_HISTORY_PATH", "").strip()
    if override:
        return Path(override)
    return data_source_path().parent / "schwab_stream_chart_history.json.gz"


# Live minutes straight from api_server's memory (2026-10-01, "no delay").
STREAM_LIVE_ENABLED = True
STREAM_LIVE_URL = "http://127.0.0.1:3002/api/momx-stream-bars"
STREAM_LIVE_POLL_SECONDS = 15.0
STREAM_LIVE_WINDOW_SECONDS = 3 * 3600
_STREAM_LIVE: dict = {"at": 0.0, "ok_at": 0.0, "max_t": 0.0, "bars": {}, "dead": []}


def _dead_minutes(minute_sets) -> list:
    """Minutes between the first and last streamed minute that NO symbol has:
    the stream itself was down (api_server restart / freeze), not a quiet name."""
    seen: set = set()
    for minutes in minute_sets:
        seen.update(minutes)
    if not seen:
        return []
    first, last = min(seen), max(seen)
    first -= first % 60
    return [t for t in range(first, last, 60) if t not in seen]
_STREAM_LIVE_LOCK = threading.Lock()


def _stream_live_refresh() -> bool:
    """Poll the endpoint at most every STREAM_LIVE_POLL_SECONDS; merge rows
    by minute (the forming minute is re-sent and replaced). False if down."""
    import urllib.request

    now_mono = _monotonic()
    with _STREAM_LIVE_LOCK:
        if now_mono - _STREAM_LIVE["at"] < STREAM_LIVE_POLL_SECONDS:
            return now_mono - _STREAM_LIVE["ok_at"] < 4 * STREAM_LIVE_POLL_SECONDS and _STREAM_LIVE["ok_at"] > 0
        _STREAM_LIVE["at"] = now_mono
        since = (_STREAM_LIVE["max_t"] - 180) if _STREAM_LIVE["max_t"] else time.time() - STREAM_LIVE_WINDOW_SECONDS
    try:
        request = urllib.request.Request(f"{STREAM_LIVE_URL}?since={int(since)}",
                                         headers={"X-AGX-Background": "1"})
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - endpoint missing/down: the file is the fallback
        return False
    rows_by_symbol = payload.get("bars") or {}
    floor = time.time() - STREAM_LIVE_WINDOW_SECONDS
    with _STREAM_LIVE_LOCK:
        held = _STREAM_LIVE["bars"]
        for symbol, rows in rows_by_symbol.items():
            minutes = held.setdefault(symbol, {})
            for row in rows:
                try:
                    t = int(row[0])
                except (TypeError, ValueError, IndexError):
                    continue
                minutes[t] = {"time": t, "open": row[1], "high": row[2], "low": row[3],
                              "close": row[4], "volume": row[5]}
                if t > _STREAM_LIVE["max_t"]:
                    _STREAM_LIVE["max_t"] = t
        for symbol in list(held):
            minutes = held[symbol]
            for t in [t for t in minutes if t < floor]:
                minutes.pop(t, None)
            if not minutes:
                held.pop(symbol, None)
        _STREAM_LIVE["dead"] = _dead_minutes(held.values())
        _STREAM_LIVE["ok_at"] = _monotonic()
    return True


def _stream_minutes(symbol: str) -> list:
    """The symbol's stream 1-minute bars: live from api_server's memory when
    that endpoint answers, else from the saved history file."""
    if STREAM_LIVE_ENABLED and _stream_live_refresh():
        with _STREAM_LIVE_LOCK:
            minutes = _STREAM_LIVE["bars"].get(symbol)
            return [minutes[t] for t in sorted(minutes)] if minutes else []
    return _stream_minutes_from_file(symbol)


TOS_STREAMED_REFETCH_SECONDS = 1800.0


def _stream_live_symbols() -> set:
    """Symbols the live stream carried in its last 3 minutes (empty if down)."""
    if not (STREAM_LIVE_ENABLED and _STREAM_LIVE["ok_at"] > 0
            and _monotonic() - _STREAM_LIVE["ok_at"] < 4 * STREAM_LIVE_POLL_SECONDS):
        return set()
    with _STREAM_LIVE_LOCK:
        recent = _STREAM_LIVE["max_t"] - 180
        return {symbol for symbol, minutes in _STREAM_LIVE["bars"].items()
                if minutes and max(minutes) >= recent}


def _stream_dead() -> list:
    """Sorted dead minutes of whichever source _stream_minutes just used."""
    if STREAM_LIVE_ENABLED and _STREAM_LIVE["ok_at"] > 0 and             _monotonic() - _STREAM_LIVE["ok_at"] < 4 * STREAM_LIVE_POLL_SECONDS:
        return _STREAM_LIVE["dead"]
    return _STREAM_CACHE.get("dead") or []


def _stream_minutes_from_file(symbol: str) -> list:
    """The symbol's stream 1-minute bars (re-read only when the file changes)."""
    path = stream_history_path()
    try:
        stat = path.stat()
    except OSError:
        return []
    if time.time() - stat.st_mtime > STREAM_FILE_MAX_AGE_SECONDS:
        return []
    with _STREAM_LOCK:
        if _STREAM_CACHE["mtime"] != stat.st_mtime:
            try:
                with gzip.open(path, "rt", encoding="utf-8") as handle:
                    payload = json.load(handle)
                _STREAM_CACHE["symbols"] = payload.get("symbols") or {}
                _STREAM_CACHE["dead"] = _dead_minutes(
                    [int(bar["time"]) for bar in bars if isinstance(bar, dict) and "time" in bar]
                    for bars in _STREAM_CACHE["symbols"].values())
                _STREAM_CACHE["mtime"] = stat.st_mtime
            except Exception:  # noqa: BLE001 - mid-write/corrupt: keep the last good read
                pass
        return _STREAM_CACHE["symbols"].get(symbol) or []


def _stream_buckets(minutes: list, span_minutes: int) -> dict:
    """1-minute bars -> {bucket_start_epoch: [o, h, l, c, v]}. Buckets are
    epoch-aligned, which for 5m/30m equals Eastern clock alignment."""
    span = span_minutes * 60
    out: dict = {}
    for bar in minutes:
        try:
            t = int(bar["time"])
            o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
            v = float(bar.get("volume") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        key = t - t % span
        held = out.get(key)
        if held is None:
            out[key] = [o, h, l, c, v, t, t]
        else:
            if t < held[5]:
                held[0], held[5] = o, t
            if t >= held[6]:
                held[3], held[6] = c, t
            held[1] = max(held[1], h)
            held[2] = min(held[2], l)
            held[4] += v
    return out


def _extend_with_stream(frame: pd.DataFrame, symbol: str, span_minutes: int) -> pd.DataFrame:
    """REST tape + the stream's newer buckets, if the stream proves itself on
    the overlap; ``frame`` unchanged (same object) otherwise."""
    if not STREAM_TAIL_ENABLED or frame is None or frame.empty or len(frame) < 2:
        return frame
    minutes = _stream_minutes(symbol)
    if not minutes:
        return frame
    # CHEAP BY CONSTRUCTION (2026-10-01 10:16 ET): the first version bucketed
    # every held minute (up to 12,000/symbol) and converted every tape row's
    # timestamp in Python on every build - builds went from ~5s to ~280s and
    # the worker stopped answering. Only the tape's last few rows and the
    # stream minutes from the first verify bucket on are touched now.
    tail_rows = frame.iloc[-(STREAM_VERIFY_LOOKBACK + 1):]
    stamps = [int(t.timestamp()) for t in tail_rows["timestamp"]]
    last_rest = stamps[-1]
    since = stamps[0]
    try:
        first_minute = int(minutes[0]["time"])
    except (KeyError, TypeError, ValueError, IndexError):
        return frame
    lo, hi = 0, len(minutes)
    while lo < hi:                      # minutes are ascending by time
        mid = (lo + hi) // 2
        if int(minutes[mid].get("time") or 0) < since:
            lo = mid + 1
        else:
            hi = mid
    buckets = _stream_buckets(minutes[lo:], span_minutes)
    if not buckets:
        return frame
    # Verify on the REST tape's last COMPLETE buckets (the final row may still
    # be forming) that the stream also fully covers.
    checked = 0
    volumes = [float(v) for v in tail_rows["volume"]]
    for position in range(len(stamps) - 2, -1, -1):
        if checked >= STREAM_VERIFY_BUCKETS:
            break
        key = stamps[position]
        if key < first_minute:
            break
        rest_volume = volumes[position]
        mine = buckets.get(key)
        stream_volume = mine[4] if mine else 0.0
        slack = max(1.0, STREAM_VOLUME_TOLERANCE * rest_volume)
        if stream_volume > rest_volume + slack:
            return frame                # over-counts: not the same feed
        if rest_volume - stream_volume > slack:
            continue                    # under-counts: a stream hole, not evidence
        checked += 1
    if checked < STREAM_VERIFY_BUCKETS:
        return frame
    newer = sorted(key for key in buckets if key >= last_rest)
    dead = _stream_dead()
    if dead and newer:
        import bisect
        span = span_minutes * 60
        lo = bisect.bisect_left(dead, newer[0])
        if lo < len(dead):
            first_dead = dead[lo]       # keep only buckets that end before it
            newer = [key for key in newer if key + span <= first_dead]
    if not newer:
        return frame
    tz = frame["timestamp"].iloc[-1].tzinfo
    tail = pd.DataFrame({
        "timestamp": [pd.Timestamp(key, unit="s", tz="UTC").tz_convert(tz) for key in newer],
        "open": [buckets[key][0] for key in newer], "high": [buckets[key][1] for key in newer],
        "low": [buckets[key][2] for key in newer], "close": [buckets[key][3] for key in newer],
        "volume": [buckets[key][4] for key in newer], "feed": STREAM_FEED_MARK,
    })
    body = frame.iloc[:-1] if newer[0] == last_rest else frame
    return pd.concat([body, tail], ignore_index=True)


# --- Current-day premarket hole, 04:00-07:00 ET (2026-10-01, his call: "use
# charts alpaca and tos then you don't see 2 bar issue") ---------------------
# Schwab serves NO current-day bars before 07:00 ET (REST or stream). Without
# them the 01:00 4h bucket does not exist, so at 09:16 the 4h volume gate's
# "2 bars ago" landed on yesterday's 17:00 bucket and blocked 352/368 symbols,
# while TOS (which draws those hours) did not. The charts fill the same hole
# from Alpaca SIP; TOS mode now does too - ONLY that window, ONLY where Schwab
# has no bar (Schwab always wins), one bulk request per 200 symbols per tape,
# cached per day and frozen once 07:00 is past SIP's recency block.
PREMARKET_FILL_ENABLED = True
PREMARKET_FILL_REFRESH_SECONDS = 300.0
_PM_FILL_LOCK = threading.Lock()
_PM_FILL: dict = {}   # (date, timeframe) -> {"at": wall, "final": bool, "frames": {sym: df}}


def _premarket_fill(symbols, timeframe: str, now: datetime | None) -> dict:
    if not PREMARKET_FILL_ENABLED or not symbols:
        return {}
    current = (now or datetime.now(timezone.utc)).astimezone(EASTERN)
    open_at = current.replace(hour=4, minute=0, second=0, microsecond=0)
    close_at = current.replace(hour=7, minute=0, second=0, microsecond=0)
    if current.weekday() >= 5 or current < open_at + timedelta(minutes=FEED_RECENT_DELAY_MINUTES["sip"]):
        return {}
    key = (current.date().isoformat(), timeframe)
    wall = _wall_clock()
    with _PM_FILL_LOCK:
        entry = _PM_FILL.setdefault(key, {"at": 0.0, "final": False, "frames": {}})
        stale = not entry["final"] and wall - entry["at"] >= PREMARKET_FILL_REFRESH_SECONDS
        missing = [s for s in symbols if s not in entry["frames"]]
        want = list(symbols) if stale else missing
        if not want:
            return {s: entry["frames"][s] for s in symbols if s in entry["frames"]}
        if stale:
            entry["at"] = wall
    try:
        credentials = resolve_credentials()
        if not credentials:
            return {}
        end = min(close_at, _clamp_end(current.astimezone(timezone.utc), "sip", now))
        got: dict = {}
        for batch in _chunk(_normalize_symbols(want), BATCH_SIZE):
            payload = _fetch_batch(batch, timeframe=timeframe, start=open_at.astimezone(timezone.utc),
                                   end=end, feed="sip", credentials=credentials,
                                   get=_default_get(), adjustment=INTRADAY_ADJUSTMENT)
            for symbol, raw in (payload or {}).items():
                if raw:
                    frame = boats_bars_to_frame(raw)
                    stamps = frame["timestamp"].dt.tz_convert(EASTERN)
                    frame = frame[(stamps >= open_at) & (stamps < close_at)].reset_index(drop=True)
                    frame["feed"] = "sip-premarket"
                    got[str(symbol).upper()] = frame
    except Exception:  # noqa: BLE001 - a missing fill only costs the premarket gate
        return {}
    with _PM_FILL_LOCK:
        entry = _PM_FILL[key]
        for symbol in want:
            entry["frames"][symbol] = got.get(symbol, _empty_frame())
        entry["final"] = current >= close_at + timedelta(minutes=FEED_RECENT_DELAY_MINUTES["sip"] + 5)
        if len(_PM_FILL) > 8:
            for old in sorted(_PM_FILL)[:-4]:
                _PM_FILL.pop(old, None)
        return {s: entry["frames"][s] for s in symbols if s in entry["frames"]}


def _fill_premarket_hole(frame: pd.DataFrame, fill) -> pd.DataFrame:
    """Insert the fill's bars where the Schwab tape has none (Schwab wins)."""
    if fill is None or getattr(fill, "empty", True) or frame is None or frame.empty:
        return frame
    # int64 membership, not a set of Timestamp objects: the object path cost
    # ~36 ms per symbol and pinned the worker's GIL (2026-10-01 12:25 ET hang).
    have = pd.DatetimeIndex(frame["timestamp"]).as_unit("ns").asi8
    wanted = pd.DatetimeIndex(fill["timestamp"]).as_unit("ns").asi8
    extra = fill[~np.isin(wanted, have)]
    if extra.empty:
        return frame
    merged = pd.concat([frame, extra[[c for c in frame.columns if c in extra.columns]]], ignore_index=True)
    return merged.sort_values("timestamp").reset_index(drop=True)


def tos_debug(symbols: Iterable[str] | None = None) -> dict:
    """Read-only TOS budget/store state for the worker's diagnostics route.

    Added 2026-10-01 because 14 symbols' daily tapes stayed "queued" for 25+
    minutes while the rest loaded, and nothing outside the process could say
    whether they were never asked, backing off, or starved of budget."""
    wanted = set(_normalize_symbols(symbols)) if symbols is not None else None
    stamp = _monotonic()
    wall = _wall_clock()
    with _TOS_LOCK:
        spent: dict[str, int] = {}
        for when, kind in _TOS_SPENT:
            if stamp - when < 60.0:
                spent[str(kind)] = spent.get(str(kind), 0) + 1
        demand = {str(kind): {"ageSeconds": round(stamp - when, 1), "want": want}
                  for kind, (when, want) in _TOS_DEMAND.items()}
    kinds: dict[str, dict] = {}
    with _CACHE_LOCK:
        for kind, store in _STORE.items():
            def pick(mapping):
                return {s: round(wall - t, 1) for s, t in mapping.items()
                        if wanted is None or s in wanted}
            kinds[kind] = {
                "held": len(store.tapes),
                "emptyAgeSeconds": pick(store.empty_at),
                "fetchedAgeSeconds": pick(store.fetched_at) if wanted is not None else None,
            }
    return {"source": get_data_source(), "budgetPerMinute": TOS_REQUESTS_PER_MINUTE,
            "spentLast60s": spent, "demand": demand, "kinds": kinds}


def tos_status(symbols: Iterable[str]) -> dict:
    """How much of ``symbols`` TOS mode has loaded: a symbol is loaded once it
    holds a 5m, a 30m AND a daily tape. ``oldestSeconds`` is the age of the
    stalest 5m/30m tape among loaded symbols (None when nothing is loaded)."""
    wanted = _normalize_symbols(symbols)
    wall = _wall_clock()
    loaded = 0
    oldest: float | None = None
    with _CACHE_LOCK:
        stores = [_STORE.get(kind) for kind in ("5m", "30m", "daily")]
        for symbol in wanted:
            if not all(store is not None and symbol in store.tapes
                       and not store.tapes[symbol].empty for store in stores):
                continue
            loaded += 1
            for store in stores[:2]:
                age = wall - store.fetched_at.get(symbol, store.full_built_at)
                oldest = age if oldest is None else max(oldest, age)
    return {
        "source": get_data_source(),
        "total": len(wanted),
        "loaded": loaded,
        "pending": len(wanted) - loaded,
        "oldestSeconds": None if oldest is None else max(0, int(round(oldest))),
    }


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
    paced = SCHWAB_ONLY and SCHWAB_VOLUME_ENABLED
    with _CACHE_LOCK:
        store = _STORE.get(cache_kind)
        if (store is not None and not paced
                and wall - store.full_built_at > FULL_REBUILD_SECONDS):
            # THE SPLIT HEAL - see the module docstring. Everything held for
            # this kind may carry a pre-split adjustment; none of it survives.
            store = None
        if store is None:
            store = _STORE[cache_kind] = _KindStore(wall)
            if paced:
                _load_tos_tapes(cache_kind, store, wall, stamp)
        if paced:
            # TOS mode: every Schwab fetch is already full depth, so the heal
            # is PER TAPE - drop only a tape not re-read in 12h. Dropping the
            # whole kind would blank the board for the ~12 minutes the paced
            # budget needs to reload ~1,100 tapes, twice a day.
            for symbol in [s for s in store.tapes
                           if wall - store.fetched_at.get(s, store.full_built_at)
                           > FULL_REBUILD_SECONDS]:
                store.tapes.pop(symbol, None)
                store.fetched_at.pop(symbol, None)
                store.empty_at.pop(symbol, None)
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
            store.fetched_at.pop(symbol, None)
            store.empty_at.pop(symbol, None)
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


def _schwab_volume_map(symbols, timeframe, now, client=None, bars_out=None, starts=None):
    """``{symbol: {epoch_seconds: real_volume}}`` for the recent tail.

    ``client`` is injectable so a test can exercise this without a network or
    credentials; production passes nothing and gets the shared cached client.

    Best-effort in every direction: no client, an unsupported timeframe, a
    raising call, or an empty frame all yield an empty map for that symbol,
    and an empty map means "change nothing". A symbol that fails here keeps
    its thin IEX volume rather than blocking the build -- a slightly wrong
    RVOL is survivable; a blank board during market hours is not.

    ``starts`` (a dict, optional): ``{symbol: datetime}`` per-symbol history
    start; a symbol not in it uses the default SCHWAB_VOLUME_LOOKBACK_MINUTES
    window. A full build passes the tape's own start so Schwab can own it.

    ``bars_out`` (a dict, optional): also filled with the full candles,
    ``{symbol: {epoch_seconds: (open, high, low, close, volume)}}`` - the
    premarket tail (``_merge_schwab_tail``) reuses this same call.
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
            # Shared across tape kinds and watchlists: overlapping independent
            # downloads must not multiply the broker request concurrency.
            with _SCHWAB_REQUEST_SLOTS:
                frame = client._get_price_history(
                    symbol, timeframe, (starts or {}).get(symbol) or start, None
                )
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
        if bars_out is not None:
            candles = {}
            try:
                for row in frame.itertuples(index=False):
                    o, h, l, c, v = (float(row.open), float(row.high), float(row.low), float(row.close), float(row.volume))
                    if all(math.isfinite(x) for x in (o, h, l, c)) and c > 0:
                        candles[int(pd.Timestamp(row.timestamp).timestamp())] = (o, h, l, c, v)
            except Exception:
                candles = {}
            bars_out[symbol] = candles     # dict item set: safe across pool threads
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


def _in_premarket(now: datetime | None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo("America/New_York"))
    minute = local.hour * 60 + local.minute
    return local.weekday() < 5 and PREMARKET_TAIL_FROM_MIN <= minute < PREMARKET_TAIL_UNTIL_MIN


def _merge_schwab_tail(frame: pd.DataFrame, candles, now: datetime | None) -> pd.DataFrame:
    """Premarket only: append Schwab candles STRICTLY NEWER than the tape's
    newest bar, stamped ``feed="schwab"``. Returns ``frame`` unchanged (same
    object) outside 04:00-09:30 ET, with no candles, or with nothing newer -
    so it can never make the tape worse (see the premarket-tail note above)."""
    if (not SCHWAB_PREMARKET_TAIL or not candles or frame is None or frame.empty
            or not _in_premarket(now)):
        return frame
    newest = int(pd.Timestamp(frame["timestamp"].iloc[-1]).timestamp())
    fresh = sorted(k for k in candles if k > newest)
    if not fresh:
        return frame
    tz = frame["timestamp"].iloc[-1].tzinfo
    add = pd.DataFrame({
        "timestamp": [pd.Timestamp(k, unit="s", tz="UTC").tz_convert(tz) for k in fresh],
        "open": [candles[k][0] for k in fresh], "high": [candles[k][1] for k in fresh],
        "low": [candles[k][2] for k in fresh], "close": [candles[k][3] for k in fresh],
        "volume": [candles[k][4] for k in fresh], "feed": SCHWAB_VOLUME_FEED_MARK,
    })
    body = frame.copy()
    if "feed" not in body.columns:
        body["feed"] = "sip"
    merged = pd.concat([body, add], ignore_index=True)
    return merged.drop_duplicates(subset=["timestamp"], keep="first").sort_values("timestamp").reset_index(drop=True)


def _overlay_schwab(frame: pd.DataFrame, candles, *, requested_start=None) -> pd.DataFrame:
    """Make Schwab the owner of every ET date its candles cover.

    For each ET date Schwab returned candles for, every held bar between
    Schwab's FIRST and LAST candle of that date is dropped and Schwab's
    candles are used instead (stamped ``feed="schwab"``) - including a bar
    Schwab simply does not have, because his TOS does not have it either.
    Bars outside those spans (older dates, the current day's 04:00-07:00
    hole, overnight bars before Schwab's first candle, IEX bars newer than
    Schwab's last) are kept. Schwab candles newer than the tape are appended.

    Returns ``frame`` unchanged (same object) with no candles, so a failed
    Schwab call leaves the Alpaca tape exactly as it was.
    """
    if not SCHWAB_OWNS_INTRADAY or not candles or frame is None or frame.empty:
        return frame
    tz = frame["timestamp"].iloc[-1].tzinfo
    keys = sorted(candles)
    stamps = pd.to_datetime(keys, unit="s", utc=True).tz_convert(tz)
    spans = pd.DataFrame({"stamp": stamps, "day": stamps.tz_convert(EASTERN).date})
    spans = spans.groupby("day")["stamp"].agg(["min", "max"])
    body = frame.copy()
    if "feed" in body.columns:
        body["feed"] = body["feed"].fillna("sip")
    else:
        body["feed"] = "sip"
    keep = pd.Series(True, index=body.index)
    for low, high in spans.itertuples(index=False, name=None):
        keep &= ~body["timestamp"].between(low, high)
    # A successful restatement can retract the last candle. Do not leave an
    # old Schwab-only print after the new last candle forever (DOCU 16:50).
    # Only remove our previously owned records in the requested window;
    # supplemental feeds outside the returned spans retain their semantics.
    if requested_start is not None:
        obsolete = (body["feed"] == SCHWAB_VOLUME_FEED_MARK) & (body["timestamp"] >= requested_start)
        keep &= ~obsolete
    body = body[keep]
    owned = pd.DataFrame({
        "timestamp": stamps,
        "open": [candles[k][0] for k in keys], "high": [candles[k][1] for k in keys],
        "low": [candles[k][2] for k in keys], "close": [candles[k][3] for k in keys],
        "volume": [candles[k][4] for k in keys], "feed": SCHWAB_VOLUME_FEED_MARK,
    })
    merged = pd.concat([owned, body], ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="first")
    return merged.sort_values("timestamp").reset_index(drop=True)


#: The consolidated (SIP) session: 04:00-20:00 ET. Alpaca rows inside it came
#: from SIP; rows outside it came from BOATS (overnight), which is one venue.
SIP_SESSION_MIN = (4 * 60, 20 * 60)
#: Kill switch for _sip_prices.
SIP_OWNS_PRICES = True


def _sip_session(stamp) -> bool:
    local = pd.Timestamp(stamp).tz_convert(EASTERN)
    minute = local.hour * 60 + local.minute
    return SIP_SESSION_MIN[0] <= minute < SIP_SESSION_MIN[1]


def _sip_session_mask(stamps):
    local = pd.DatetimeIndex(stamps).tz_convert(EASTERN)
    minutes = local.hour * 60 + local.minute
    return (minutes >= SIP_SESSION_MIN[0]) & (minutes < SIP_SESSION_MIN[1])


def _sip_prices(frame: pd.DataFrame, alpaca: pd.DataFrame) -> pd.DataFrame:
    """Prices from SIP, volume from Schwab, wherever SIP has settled bars.

    WHY (2026-09-29, measured, not assumed): DOCU's 16:50 Schwab candle was one
    18,999-share trade flagged "P" (Prior Reference Price) on the FINRA TRF.
    Under the consolidated last-sale rules a "P" trade (and an odd lot) adds
    volume but may NOT set open/high/low/close - so SIP drew no 16:50 bar, his
    TOS chart drew no 16:50 bar, and Schwab's price history did. The same
    print set Schwab's 16:30 30-minute close to 66.98 (SIP 66.655), which fed a
    fake 2h EMA 4/8 cross that put DOCU on our board and not on his.
    Across 4,925 settled 30m bars on 60 names, 45 (0.9%) closes disagree this
    way; Schwab had no 30m bar SIP lacked. Volume stays Schwab's: LASR 2h RVOL
    matched TOS (3.1) on Schwab volume, never on SIP's larger counts.

    Rule, inside [first, last] SIP bar held for the symbol and inside the SIP
    session (04:00-20:00 ET) only:
      * a Schwab bar SIP also has -> SIP open/high/low/close, Schwab volume;
      * a Schwab bar SIP does not have -> dropped (no price-eligible trade).
    Outside that window (overnight, the last ~20 minutes SIP refuses, days
    older than SIP's depth) Schwab bars are left exactly as they are.
    """
    if (not SIP_OWNS_PRICES or frame is None or frame.empty or alpaca is None
            or alpaca.empty or "feed" not in frame.columns):
        return frame
    marks = alpaca["feed"] if "feed" in alpaca.columns else pd.Series("sip", index=alpaca.index)
    sip_rows = alpaca[(marks.fillna("sip") == "sip").values
                      & _sip_session_mask(alpaca["timestamp"])]
    if sip_rows.empty:
        return frame
    sip = sip_rows.set_index("timestamp")
    # The window ends at the LAST SIP bar, not at SIP's fetch cutoff. Tested
    # and rejected 2026-09-29 00:14 ET: extending it to the cutoff dropped
    # the after-hours Schwab bars of quiet names and passed NTRA/PM/SHEL/WELL
    # while his refreshed TOS showed LASR only - TOS draws those bars. Known
    # residual: DOCU's lone 16:50 "P" 5m candle (after its last SIP bar,
    # 16:35) stays on the 5m tape; the 30m price fix already removes its
    # effect on 1h/2h/4h.
    low, high = sip.index.min(), sip.index.max()
    eligible = ((frame["feed"] == SCHWAB_VOLUME_FEED_MARK).to_numpy()
                & frame["timestamp"].between(low, high).to_numpy()
                & _sip_session_mask(frame["timestamp"]))
    present = frame["timestamp"].isin(sip.index).to_numpy()
    keep = ~eligible | present
    replace = eligible & present
    if not eligible.any():
        return frame
    out = frame.copy()
    prices = sip.reindex(pd.DatetimeIndex(frame.loc[replace, "timestamp"]))
    for column in ("open", "high", "low", "close"):
        out.loc[replace, column] = prices[column].to_numpy(dtype=float)
    return out[keep].reset_index(drop=True)


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

    if SCHWAB_ONLY and schwab_volume and SCHWAB_VOLUME_ENABLED and timeframe in SCHWAB_VOLUME_TIMEFRAMES:
        return _fetch_tape_schwab_only(
            pending, held, store, bars, errors, cache_key,
            timeframe=timeframe, start=start, now=now, use_cache=use_cache,
            cache_kind=cache_kind,
        )
    # TOS mode, but a caller that opted out of Schwab (chart_signals, the
    # market-turn SPY read): it shares the board's cache key and store, so an
    # Alpaca tape written here would be served on the TOS board as if it were
    # Schwab's. Read the held TOS tape, never write.
    writes = use_cache and not (SCHWAB_ONLY and SCHWAB_VOLUME_ENABLED)

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
    schwab_candles: dict = {}
    # Alpaca mode (the TOS box unticked, 2026-09-30): Schwab only for the IEX
    # tail's volume and the premarket tail, over the default
    # SCHWAB_VOLUME_LOOKBACK_MINUTES window - no full-depth starts, no
    # _overlay_schwab, no _sip_prices (see the SCHWAB_ONLY note at the top).
    # The full-depth, same-start-every-build Schwab request (LASR 178,649 vs
    # 201,372, 251b098) now lives only in TOS mode.
    #
    # PACED through the same TOS_REQUESTS_PER_MINUTE bucket: unpaced, this was
    # one call per symbol per 5m/30m on every build (~736 per ~minute) - the
    # 459/minute that preceded the 2026-09-04 Akamai ban - and unticking TOS
    # (clear_cache) fired it all at once. Only a frame that holds IEX-marked
    # bars (or premarket, for the tail) is asked for, longest-uncorrected
    # first. An ungranted symbol keeps its IEX volume this round; those bars
    # stay marked "iex" in the store, so its next turn (well inside the 60-min
    # lookback) corrects them all.
    frames: dict[str, pd.DataFrame] = {}
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
        frames[symbol] = _merge_iex_tail(frame, _merge_frames(iex_rows.get(symbol) or []))

    schwab_map: dict = {}
    if schwab_volume and timeframe in ALPACA_SCHWAB_TIMEFRAMES and SCHWAB_VOLUME_ENABLED:
        premarket = SCHWAB_PREMARKET_TAIL and _in_premarket(now)
        candidates = [
            symbol for symbol, frame in frames.items()
            if premarket or ("feed" in frame.columns
                             and (frame["feed"] == SCHWAB_VOLUME_REPLACES_FEED).any())
        ]
        candidates.sort(key=lambda symbol: _ALPACA_SCHWAB_AT.get((timeframe, symbol), 0.0))
        asked = candidates[:_tos_take(len(candidates), cache_kind)]
        if asked:
            schwab_map = _schwab_volume_map(asked, timeframe, now, bars_out=schwab_candles)
            stamp = _wall_clock()
            for symbol in asked:
                _ALPACA_SCHWAB_AT[(timeframe, symbol)] = stamp

    for symbol, frame in frames.items():
        # The IEX tail above carries ~2% of real volume. Swap in Schwab's
        # measured volume for exactly those bars BEFORE anything caches or
        # reads them, so RVOL divides like against like. An empty map for a
        # symbol returns the frame unchanged.
        frame = _apply_schwab_volume(frame, schwab_map.get(symbol) or {})
        # Premarket: the bars IEX could not give, from the same Schwab call.
        frame = _merge_schwab_tail(frame, schwab_candles.get(symbol), now)
        bars[symbol] = frame
        if writes:
            _cache_put(cache_key(symbol), frame)
            if store is not None:
                with _CACHE_LOCK:
                    store.tapes[symbol] = frame
    return FeedResult(bars, errors)


def _schwab_frame(candles: dict, timeframe: str) -> pd.DataFrame:
    """Schwab candles ``{epoch: (o, h, l, c, v)}`` -> the house tape frame.

    Daily candles are stamped at Eastern MIDNIGHT of their date (Schwab stamps
    most at 01:00 ET = 00:00 CT and the newest at 00:00 ET); every daily
    consumer keys on the Eastern date, the same convention the Alpaca daily
    tape used."""
    keys = sorted(candles)
    stamps = [pd.Timestamp(key, unit="s", tz="UTC").tz_convert(EASTERN) for key in keys]
    if timeframe == TIMEFRAME_DAILY:
        stamps = [stamp.normalize() for stamp in stamps]
    frame = pd.DataFrame({
        "timestamp": stamps,
        "open": [candles[key][0] for key in keys],
        "high": [candles[key][1] for key in keys],
        "low": [candles[key][2] for key in keys],
        "close": [candles[key][3] for key in keys],
        "volume": [candles[key][4] for key in keys],
        "feed": SCHWAB_VOLUME_FEED_MARK,
    })
    return frame.drop_duplicates(subset=["timestamp"], keep="last").reset_index(drop=True)


def _fetch_tape_schwab_only(pending, held, store, bars, errors, cache_key, *,
                            timeframe, start, now, use_cache, cache_kind=None) -> FeedResult:
    """SCHWAB_ONLY: one full-depth Schwab price-history call per symbol (the
    same request shape on every build - Schwab's volume depends on startDate,
    see the LASR note in git log 251b098), nothing from Alpaca.

    RATE-PACED (see TOS_REQUESTS_PER_MINUTE): only as many symbols as the
    budget grants right now are requested - first those with NO held tape,
    then the stalest held ones; a held daily tape younger than
    TOS_DAILY_REFRESH_SECONDS is not asked for at all. Everyone else is served
    their held tape; a symbol with neither is "queued", not failed."""
    wall = _wall_clock()
    _tos_register(cache_kind or timeframe, timeframe, start, cache_key)
    order = _tos_order(pending, held, store, timeframe, wall)
    asked = order[:_tos_take(len(order), cache_kind or timeframe)]
    candles: dict = {}
    if asked:
        _schwab_volume_map(asked, timeframe, now, bars_out=candles,
                           starts={symbol: start for symbol in asked})
    return _tos_serve(pending, held, store, bars, errors, cache_key, candles, asked,
                      timeframe=timeframe, now=now, use_cache=use_cache,
                      cache_kind=cache_kind, wall=wall)


# --- Background TOS refresher (2026-10-01 10:45 ET, "make it fast quick") ----
# Schwab tapes were refreshed only INSIDE board builds, and a Watchlist build
# takes ~2 minutes in market hours, so only ~22 of the 90 calls/min were spent
# and some tapes went 70+ minutes unrefreshed (MRNA, LITE, CVX). This thread
# spends the same budget continuously - stalest first, over every symbol any
# list asked for in the last STORE_PRUNE_SECONDS - so freshness no longer
# depends on build time. Builds keep their own small asks (missing tapes).
TOS_REFRESHER_ENABLED = True
TOS_REFRESHER_INTERVAL_SECONDS = 2.0
_TOS_REFRESH_LOCK = threading.Lock()
_TOS_REFRESH_KINDS: dict = {}       # kind -> {"timeframe", "start", "cache_key"}
_TOS_REFRESH_THREAD: dict = {"thread": None}


def _tos_register(kind, timeframe, start, cache_key) -> None:
    with _TOS_REFRESH_LOCK:
        _TOS_REFRESH_KINDS[kind] = {"timeframe": timeframe, "start": start, "cache_key": cache_key}
        thread = _TOS_REFRESH_THREAD["thread"]
        if TOS_REFRESHER_ENABLED and (thread is None or not thread.is_alive()):
            thread = threading.Thread(target=_tos_refresher_loop, name="momx-tos-refresher", daemon=True)
            _TOS_REFRESH_THREAD["thread"] = thread
            thread.start()


def _tos_refresh_once(now=None) -> int:
    """One pass: for each tape, ask Schwab for the best symbols the budget
    grants right now and fold them into the store. Returns calls made."""
    if not (SCHWAB_ONLY and SCHWAB_VOLUME_ENABLED):
        return 0
    with _TOS_REFRESH_LOCK:
        kinds = dict(_TOS_REFRESH_KINDS)
    made = 0
    for kind, info in kinds.items():
        with _CACHE_LOCK:
            store = _STORE.get(kind)
            if store is None:
                continue
            symbols = list(store.last_used)
            held = {s: store.tapes[s] for s in symbols if s in store.tapes}
        if not symbols:
            continue
        wall = _wall_clock()
        order = _tos_order(symbols, held, store, info["timeframe"], wall)
        asked = order[:_tos_take(len(order), kind)]
        if not asked:
            continue
        candles: dict = {}
        _schwab_volume_map(asked, info["timeframe"], now, bars_out=candles,
                           starts={symbol: info["start"] for symbol in asked})
        made += len(asked)
        with _CACHE_LOCK:
            if _STORE.get(kind) is not store:       # source switched meanwhile
                continue
            for symbol in asked:
                got = candles.get(symbol)
                if got:
                    frame = _schwab_frame(got, info["timeframe"])
                    store.tapes[symbol] = frame
                    store.fetched_at[symbol] = wall
                    store.empty_at.pop(symbol, None)
                else:
                    store.empty_at[symbol] = wall
        for symbol in asked:
            got = candles.get(symbol)
            if got:
                _cache_put(info["cache_key"](symbol), store.tapes.get(symbol))
        if candles:
            _persist_tos_store(kind, store)
    return made


def _tos_refresher_loop() -> None:
    while True:
        time.sleep(TOS_REFRESHER_INTERVAL_SECONDS)
        try:
            _tos_refresh_once()
        except Exception:  # noqa: BLE001 - the refresher must never die
            pass


def _tos_order(pending, held, store, timeframe, wall) -> list:
    """Which symbols to ask Schwab for, best first (missing, then stalest)."""
    with _CACHE_LOCK:
        fetched_at = dict(store.fetched_at) if store is not None else {}
        empty_at = dict(store.empty_at) if store is not None else {}
    # A symbol Schwab answered with nothing waits TOS_EMPTY_RETRY_SECONDS and
    # then queues behind the never-tried ones, so it cannot starve the list.
    missing = sorted(
        (symbol for symbol in pending if symbol not in held
         and wall - empty_at.get(symbol, -1e18) >= TOS_EMPTY_RETRY_SECONDS),
        key=lambda symbol: empty_at.get(symbol, 0.0),
    )
    # A held intraday tape is not re-read sooner than TOS_MIN_REFETCH_SECONDS:
    # Mag7 rebuilds every 15s and re-read its ten tapes on every build (AAPL
    # every ~11s, 2026-10-01) while 200+ Watchlist tapes had none.
    due = [symbol for symbol in pending if symbol in held
           and wall - fetched_at.get(symbol, 0.0) >= TOS_MIN_REFETCH_SECONDS]
    if timeframe == TIMEFRAME_DAILY:
        due = [symbol for symbol in due
               if wall - fetched_at.get(symbol, 0.0) >= TOS_DAILY_REFRESH_SECONDS]
    if timeframe in (TIMEFRAME_5M, TIMEFRAME_30M):
        # Streamed symbols are already ~1 minute fresh through the stream tail;
        # the budget goes to the ~165 the 300-symbol stream cannot carry first,
        # and a streamed tape is re-read once it is TOS_STREAMED_REFETCH_SECONDS
        # old (the tail still needs recent REST buckets to verify against).
        live = _stream_live_symbols()
        due.sort(key=lambda symbol: (
            symbol in live and wall - fetched_at.get(symbol, 0.0) < TOS_STREAMED_REFETCH_SECONDS,
            fetched_at.get(symbol, 0.0)))
    else:
        due.sort(key=lambda symbol: fetched_at.get(symbol, 0.0))
    return missing + due


def _tos_serve(pending, held, store, bars, errors, cache_key, candles, asked, *,
               timeframe, now, use_cache, cache_kind, wall) -> FeedResult:
    premarket = (_premarket_fill(list(pending), timeframe, now)
                 if timeframe in (TIMEFRAME_5M, TIMEFRAME_30M) else {})
    for symbol in pending:
        got = candles.get(symbol)
        fresh = bool(got)
        if fresh:
            frame = _schwab_frame(got, timeframe)
        elif symbol in held:
            frame = held[symbol]  # not our turn, or Schwab blipped: stale beats blank
        else:
            if symbol in asked:
                errors[symbol] = "schwab: no candles returned"
                if store is not None:
                    with _CACHE_LOCK:
                        store.empty_at[symbol] = wall
            else:
                errors[symbol] = TOS_QUEUED_ERROR
            continue
        if timeframe in (TIMEFRAME_5M, TIMEFRAME_30M):
            # Served copy only: the store keeps the REST tape, re-verified and
            # re-extended every build.
            filled = _fill_premarket_hole(frame, premarket.get(symbol))
            if filled is not frame and not fresh and store is not None:
                # Keep the merged tape so the next build finds nothing to add
                # (one concat per symbol per day, not one per build).
                with _CACHE_LOCK:
                    if store.tapes.get(symbol) is frame:
                        store.tapes[symbol] = filled
            frame = filled
            bars[symbol] = _extend_with_stream(
                frame, symbol, 5 if timeframe == TIMEFRAME_5M else 30)
        else:
            bars[symbol] = frame
        # Only a fresh read is cached: a held tape served from the TTL cache
        # would hide from the "stalest first" order for CACHE_TTL_SECONDS.
        if use_cache and fresh:
            _cache_put(cache_key(symbol), frame)
            if store is not None:
                with _CACHE_LOCK:
                    store.tapes[symbol] = frame
                    store.fetched_at[symbol] = wall
                    store.empty_at.pop(symbol, None)
    if use_cache and store is not None and candles and cache_kind:
        _persist_tos_store(cache_kind, store)
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
    "get_data_source",
    "resolve_credentials",
    "set_data_source",
    "tos_status",
]
