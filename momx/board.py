"""MomoX board orchestrator: symbols in, the full payload-contract document out.

``build_board()`` is the only thing that knows the whole pipeline::

    momx.feed  ->  momx.buckets  ->  momx.columns  ->  momx.scan  ->  payload

It computes NO indicator of its own. Every number on the board comes from the
modules above, which are in turn pinned to
``ganesh_higher_timeframe_signals`` -- the chart-validated TOS replay. If a
column looks wrong, the bug is in that chain, not here.

*** PERFORMANCE CONSTRAINTS -- NON-NEGOTIABLE, DO NOT RELAX ***

This repository has a documented history of background scanners starving the
chart engine of CPU (the study builder that made every Schwab chart call look
slow; the 5s browser poll that owned the whole backend). The board is
therefore built to be *pulled*, never to run on its own:

1. ``build_board`` is a plain synchronous call and this module STARTS NO
   THREAD ON IMPORT. Importing ``momx.board`` must leave
   ``threading.active_count()`` unchanged; a test asserts exactly that. If a
   future editor wants periodic refresh it must be opt-in through an explicit
   ``start_*()`` function that the caller invokes deliberately -- never a
   module-level thread, timer, scheduler or executor.
2. This module does NOT touch, read, or write any cache inside
   ``api_server.py``. ``cached_board`` owns its own small dict, and nothing
   else may reach into it. ``api_server`` is not imported here, directly or
   transitively.
3. Nothing here warms charts or option chains. No Schwab call, no chain
   builder, no study builder, no streamer. The only network this module can
   cause is ``momx.feed``'s batched Alpaca bar request plus, at most once per
   build (TTL-cached for 15 minutes inside :mod:`momx.flags`), ONE bulk
   earnings-calendar range request for the lightning badge, plus at most one
   TTL-cached (:mod:`momx.news`, ~300s) bulk Alpaca News request for the
   SHIPPED rows only -- never the whole universe. All are per-BUILD and
   batched; none may ever become per-symbol.

2H CLOCK (was an open question, RESOLVED 2026-09-01): ``momx.buckets`` ships
two 2H clocks -- Eastern midnight (02/04/06... ET) and Central midnight
(01/03/05... ET, what the browser chart, the premarket scanner and the legacy
watchlist emulation all use). The board folded 2H on the EASTERN clock, per the
MomoX brief, and was the only one of the four doing so.

thinkorswim support told the trader a 16-hour extended session shows NINE 2h
candles. That is countable, and it decides it: over 04:00-20:00 ET the Eastern
clock yields EIGHT buckets (04 06 08 10 12 14 16 18) and the Central clock
yields NINE (03 05 07 09 11 13 15 17 19). :data:`TWO_HOUR_ANCHOR` is therefore
Central now, and everything folded from the 2h tape moved with it: RVOL 2h,
Squeeze 2h, Skittles 2h, and the High/Low column, which reads that tape
directly. 4H was never in dispute -- the sources agree on 01/05/09/13/17/21 ET.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from config import ARTIFACTS_DIR, settings
from momx import buckets, columns, feed, flags, grade, news, scan
from momx.industries import industry_for
from momx.industry_lookup import resolve as resolve_industries

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: TTL for :func:`cached_board`. One minute: the board's slowest input is the
#: daily tape, and ``momx.feed`` already has its own 55s frame cache under it.
CACHE_TTL_SECONDS = 60.0

#: TOS "Show: 50".
DEFAULT_LIMIT = scan.RANK_LIMIT

#: Which source tape each timeframe key is folded from, and at what span.
#: "5m" is folded from itself so every tape goes through the same repair and
#: de-duplication path.
INTRADAY_FROM_5M: dict[str, int] = {"5m": 5, "15m": 15, "30m": 30}
INTRADAY_FROM_30M: dict[str, int] = {"1h": 60, "2h": 120, "4h": 240}
DAILY_SPANS: tuple[str, ...] = ("D", "2D", "3D", "4D", "Wk", "M")

#: Every timeframe key in the payload contract, in contract order.
TIMEFRAME_KEYS: tuple[str, ...] = (
    "5m", "15m", "30m", "1h", "2h", "4h", "D", "2D", "3D", "4D", "Wk", "M",
)

#: The Central-midnight clock (01/03/05... ET), which the browser chart, the
#: premarket scanner and the legacy watchlist emulation already use. It is the
#: one that yields the NINE 2h candles per 16-hour session thinkorswim support
#: quoted; the Eastern clock yields eight. See the 2H CLOCK note in the module
#: docstring for the measurement.
TWO_HOUR_ANCHOR = buckets.CENTRAL_MIDNIGHT_2H

#: Depth requested per tape. 5 days of 5m covers the 50-bar RVOL window on
#: every intraday key; 30 days of 30m covers 4H; 3 years of daily covers the
#: monthly Skittles column. These are ``momx.feed``'s own defaults, named here
#: so a caller can see what depth the board assumes.
FIVE_MINUTE_DAYS = 5
THIRTY_MINUTE_DAYS = 30
DAILY_YEARS = 3

#: The trader's thinkorswim ``001_Mega7`` watchlist, given 2026-08-27: exactly
#: ``premarket_scanner.PREMARKET_SCAN_SYMBOLS`` plus USO, the list his scanner
#: screenshot was pointed at. Kept named because it is the narrow board he
#: watches premarket; :data:`DEFAULT_UNIVERSE` is a strict superset of it.
MEGA7_SEED: tuple[str, ...] = (
    "AAPL", "AMZN", "GOOGL", "META", "MSFT", "NFLX", "NVDA", "TSLA", "AVGO", "USO",
)

#: The universe the board starts with before the trader pastes his own list --
#: the MomoX brief's seed: the Mega7 names, plus the index/commodity ETFs he
#: keeps on screen. The full ``aa_MOSTWATCHLIST`` (~384 names) replaces it once
#: he pastes the TOS export into the panel.
#: The trader named these ten explicitly on 2026-08-27 ("use this tickers for
#: Mag7"), so the board seeds with exactly that list and nothing else. An
#: earlier draft seeded a superset with index/commodity ETFs; SPX in particular
#: produced a permanently blank row, because Alpaca has no bars for a cash
#: index. The panel's ticker box replaces this the moment he pastes his
#: aa_MOSTWATCHLIST export.
DEFAULT_UNIVERSE: tuple[str, ...] = MEGA7_SEED

#: Persistence follows the repo's existing user-settings convention: a plain
#: JSON document under ``artifacts/`` -- the same place ``chart_grids.json``
#: and ``mag7_signal_scanner_config.json`` live -- written whole and read
#: whole. The write is atomic (temp file + ``os.replace``), which
#: ``chart_grids.json`` is not, so a crash mid-save cannot leave the trader
#: with a truncated 400-symbol list.
#:
#: The document holds NAMED lists, not one universe. Schema 2::
#:
#:     {"schemaVersion": 2, "savedAt": "...", "active": "Mag7",
#:      "lists": {"Mag7": [...], "Watchlist": [...]}}
#:
#: Schema 1 was a single flat ``{"symbols": [...]}`` list (or a bare JSON
#: array). It is MIGRATED on read, never discarded -- see
#: :func:`_document_from_raw`.
#: Windows-only mitigation for ``os.replace`` losing a race with a reader.
#: Five attempts over ~150ms total; a lock held longer than that is a real
#: problem worth surfacing rather than hiding.
_REPLACE_ATTEMPTS = 5
_REPLACE_BACKOFF_SECONDS = 0.01

UNIVERSE_FILENAME = "momx_universe.json"
UNIVERSE_SCHEMA_VERSION = 2
UNIVERSE_PATH_ENV = "MOMX_UNIVERSE_PATH"

#: The named lists the board ships with. Nothing in this module assumes there
#: are exactly two: every lookup iterates this mapping, so a third entry here
#: (or a list saved with ``create=True``) is warmed, listed and served with no
#: further change.
#:
#: * ``Mag7``      -- the trader's TOS ``001_Mega7``, ten names, builds in ~30s.
#: * ``Watchlist`` -- ``settings.scanner.default_universe`` (watchlist.txt,
#:   ~355 names), read LAZILY so a runtime watchlist edit is picked up.
MEGA7_LIST_NAME = "Mag7"
WATCHLIST_LIST_NAME = "Watchlist"

#: Which list a caller gets when it names none and the file names none either.
#: The small one on purpose: the panel's first paint should not wait minutes.
DEFAULT_LIST_NAME = MEGA7_LIST_NAME

_MAX_SYMBOL_LENGTH = 12

# A ticker: letters/digits, optionally $-prefixed (TOS index symbols), with
# dotted/dashed/slashed class suffixes (BRK.B, RDS-A, $SPX.X).
_SYMBOL_RE = re.compile(r"^\$?[A-Z][A-Z0-9]*(?:[./-][A-Z0-9]+)*$")

# Column headers and placeholders that would otherwise pass _SYMBOL_RE. Kept
# short on purpose -- every entry here is a ticker the trader can never use.
#: Words that appear as COLUMN HEADERS in a pasted export. Several are also
#: real, tradeable tickers -- NET (Cloudflare), OPEN (Opendoor), LOW (Lowe's),
#: MARK (Remark) -- so this set is applied ONLY to a header line, NEVER to
#: data. Applying it to every token silently deleted NET, OPEN and LOW from
#: the trader's 358-name watchlist (358 in, 355 out, 2026-08-27).
#: Tokens that are NEVER a ticker under any circumstance, on any line. Kept
#: separate from the header words below, which ARE real tickers. "N/A" needs to
#: be here explicitly because it satisfies _SYMBOL_RE - the "/" is legal so that
#: BRK.B and RDS-A parse - so nothing else rejects it.
_NEVER_SYMBOLS = frozenset({"N/A", "NONE", "NULL", "--", "---"})

_NON_SYMBOLS = frozenset({
    "SYMBOL", "SYMBOLS", "TICKER", "TICKERS", "NAME", "DESCRIPTION", "DESC",
    "LAST", "PRICE", "VOLUME", "CHANGE", "NET", "CHG", "OPEN", "HIGH", "LOW",
    "CLOSE", "BID", "ASK", "N/A", "NA", "NONE", "NULL", "TOTAL", "INDUSTRY",
    "SECTOR", "MARK",
})

_DELIMITERS = (",", "\t", ";", "|")
_FIELD_SPLIT_RE = re.compile("[" + re.escape("".join(_DELIMITERS)) + "]")

_SYMBOL_HEADERS = frozenset({"SYMBOL", "SYMBOLS", "TICKER"})


# ---------------------------------------------------------------------------
# Tape assembly
# ---------------------------------------------------------------------------

def _epoch_seconds(value: Any) -> int | None:
    """Epoch seconds from a pandas Timestamp, datetime, ISO string or number."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    stamp = getattr(value, "timestamp", None)
    if callable(stamp):
        try:
            return int(stamp())
        except Exception:
            return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return int(float(text))
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _frame_to_bars_fast(frame: Any) -> list[dict] | None:
    """The same output as the per-row loop in :func:`frame_to_bars`, for the
    common case (a DataFrame with a datetime ``timestamp`` column and no
    ``time`` column): epoch seconds for the WHOLE column in one vectorised
    step instead of one ``Timestamp.timestamp()`` call per bar. Profiled
    2026-09-25: frame_to_bars was ~10% of a Watchlist build. None = not the
    common case, use the loop. Identity with the loop is pinned by
    tests/test_momx_frame_to_bars_fast.py."""
    try:
        import pandas as pd

        if not isinstance(frame, pd.DataFrame) or "timestamp" not in frame.columns or "time" in frame.columns:
            return None
        stamps = frame["timestamp"]
        if not pd.api.types.is_datetime64_any_dtype(stamps):
            return None
        seconds = (stamps.astype("int64") // 1_000_000_000).tolist()
        natmask = stamps.isna().tolist()
        records = frame.to_dict("records")
        bars: list[dict] = []
        for record, sec, missing in zip(records, seconds, natmask):
            if missing or sec <= 0:
                continue
            record["time"] = int(sec)
            bars.append(record)
        return bars
    except Exception:  # noqa: BLE001 - the loop below is always correct
        return None


def frame_to_bars(frame: Any) -> list[dict]:
    """A ``momx.feed`` DataFrame (or any bar sequence) -> chart-shaped bars.

    ``momx.feed`` stamps its frames with a tz-aware ``timestamp`` column;
    ``momx.buckets`` and ``momx.columns`` both speak ``time`` in epoch
    seconds. This is the one translation between them. Never raises: an
    unusable row is dropped, an unusable frame yields an empty tape.
    """
    fast = _frame_to_bars_fast(frame)
    if fast is not None:
        return fast
    bars: list[dict] = []
    for row in columns._rows(frame):
        time_value = row.get("time")
        if time_value is None:
            time_value = row.get("timestamp")
        if time_value is None:
            time_value = row.get("datetime")
        seconds = _epoch_seconds(time_value)
        if seconds is None or seconds <= 0:
            continue
        bar = dict(row)
        bar["time"] = seconds
        bars.append(bar)
    return bars


def build_tapes(
    five_minute: Any,
    thirty_minute: Any,
    daily: Any,
    *,
    two_hour_anchor: str = TWO_HOUR_ANCHOR,
) -> dict[str, list[dict]]:
    """Fold the three source tapes into every payload-contract timeframe key.

    ``5m``/``15m``/``30m`` come off the 5-minute tape, ``1h``/``2h``/``4h``
    off the 30-minute tape, and ``D``/``2D``/``3D``/``4D``/``Wk``/``M`` off
    the daily tape. Intraday tapes keep extended hours -- that is what the
    scan's EXT rows mean -- while the daily tape is regular-hours, as TOS's
    is.

    The trailing bucket is FORMING and is deliberately left in place: TOS
    scans the current bar. A caller that wants closed buckets only should trim
    with :func:`momx.buckets.last_closed_bucket`.
    """
    fine = frame_to_bars(five_minute)
    coarse = frame_to_bars(thirty_minute)
    days = frame_to_bars(daily)

    tapes: dict[str, list[dict]] = {}
    for key, minutes in INTRADAY_FROM_5M.items():
        tapes[key] = buckets.aggregate_intraday(
            fine, minutes, two_hour_anchor=two_hour_anchor
        )
    for key, minutes in INTRADAY_FROM_30M.items():
        tapes[key] = buckets.aggregate_intraday(
            coarse, minutes, two_hour_anchor=two_hour_anchor
        )
    for span in DAILY_SPANS:
        tapes[span] = buckets.aggregate_daily(days, span)
    return tapes


# ---------------------------------------------------------------------------
# The board
# ---------------------------------------------------------------------------

def _normalize_symbols(symbols: Iterable[Any] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in symbols or []:
        symbol = str(raw or "").strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        out.append(symbol)
    return out


def _record_error(errors: dict[str, str], symbol: str, message: str) -> None:
    """Accumulate per-symbol errors; a symbol can fail more than one tape."""
    existing = errors.get(symbol)
    errors[symbol] = f"{existing}; {message}" if existing else message


def _fetch(
    fetcher: Any,
    name: str,
    symbols: list[str],
    kind: str,
    errors: dict[str, str],
    now: Any,
    fetch_kwargs: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """One tape fetch. A total failure degrades to empty, it never propagates."""
    call = getattr(fetcher, name, None)
    if call is None:
        for symbol in symbols:
            _record_error(errors, symbol, f"{kind}: feed has no {name}()")
        return {}
    kwargs: dict[str, Any] = dict(fetch_kwargs or {})
    if now is not None:
        kwargs.setdefault("now", now)
    try:
        result = call(symbols, **kwargs)
    except Exception as exc:  # noqa: BLE001 - one dead tape must not blank the board
        for symbol in symbols:
            _record_error(errors, symbol, f"{kind}: {type(exc).__name__}: {exc}")
        return {}
    bars = getattr(result, "bars", None)
    failures = getattr(result, "errors", None)
    if isinstance(result, Mapping):
        if bars is None:
            bars = result.get("bars")
        if failures is None:
            failures = result.get("errors")
    for symbol, message in (failures or {}).items():
        _record_error(errors, str(symbol).upper(), f"{kind}: {message}")
    return dict(bars or {})


#: Below this many symbols the sequential path wins: serialising tapes across
#: a process boundary costs more than the compute it saves. Mag7 (10) stays
#: sequential and builds in ~7s; the 357-name Watchlist uses the pool.
PARALLEL_MIN_SYMBOLS = 60

#: Worker count. 8 measured 3.00x on this 12-core machine, but during the
#: 2026-08-28 open that took 8 of 12 cores for the whole build and the trader
#: reported "app is very slow" - api_server's chart engine was left 4 cores.
#: The scanner refreshing in 45s instead of 31s is worth far less than his
#: charts staying responsive, so it takes half the machine at most. The worker
#: process also drops to BELOW_NORMAL priority (see momx_worker.py), which is
#: the bigger lever: it yields cores rather than competing for them.
PARALLEL_WORKERS = 5

_POOL = None
_POOL_LOCK = threading.Lock()


def _skipped_pct(tapes, daily_bars, last):
    """%Chg for a symbol whose row was skipped.

    Deliberately NOT the bare columns.pct_change(daily_bars): that is the
    call which returned YESTERDAY's change premarket (fixed abc9ced by
    giving it the intraday tape and the live price). A skipped row still
    has to rank correctly, so it gets the same three arguments build_row
    passes.
    """
    intraday = tapes.get("5m") or tapes.get("15m") or tapes.get("30m")
    return columns.pct_change(daily_bars, intraday, last)


def _scan_one(args):
    """Scan AND build ONE symbol, at module level so it can be pickled.

    Returns the cheap record rank_board needs plus the finished display row.
    Until 2026-09-02 this returned the TAPES instead and the parent built the
    display columns for the 50 survivors afterwards; the trader then asked
    why the other 307 rows had no RVOL / SQZ / Skittles. Building the row
    here costs each worker ~0.3s per symbol and saves shipping three bar
    tapes back across the process boundary, so every symbol now gets its
    columns for roughly what the top 50 used to cost.

    The row build is guarded on its own: a symbol whose columns blow up
    still returns its scan verdict (so the universe count and the scan stay
    honest) with ``row=None`` and the error text, and the parent records it.
    A raise here would abort the WHOLE pool.map and fall the build back to
    the sequential path.
    """
    # Tolerates the six-element tuple: build_columns was added 2026-09-03 and
    # callers that predate it (and direct callers in tests) mean "build it".
    symbol, five, thirty, daily, two_hour_anchor, industry, *_optional = args
    build_columns = _optional[0] if _optional else True
    tapes = build_tapes(five, thirty, daily, two_hour_anchor=two_hour_anchor)
    daily_bars = tapes.get("D") or []
    # "Stock Last >= $3.00" means the LAST PRICE, which is the newest intraday
    # close -- the same number the row displays. This used to read the newest
    # DAILY close, which premarket (and all session until today's daily bar
    # exists) is YESTERDAY's: a stock that closed at 2.60 and is trading 4.20
    # premarket was blocked on 2.60 while the board displayed 4.20. The gate
    # and the display must not disagree about the price of the same row.
    #
    # No separate daily fallback is needed here: last_price walks the finest
    # available tape and its preference chain ENDS at "D", so a symbol with no
    # intraday bars (halted, newly listed, failed fetch) still yields its daily
    # close rather than None. An absent price therefore cannot become a free
    # pass through the floor.
    last = columns.last_price(tapes)
    passed, reasons = scan.scan_symbol(tapes, last)
    # The BEAR scan on the same tapes (spec 2026-09-24): a few EMA/MACD
    # comparisons and one RVOL pass per timeframe - cheap next to build_row.
    bear_passed, bear_reasons = scan.scan_symbol(tapes, last, direction="bear")
    if not build_columns:
        # Caller is reusing this symbol's columns from the previous build.
        # The SCAN still ran - only the display columns are skipped, which
        # is 53% of the per-symbol cost and the half he is not looking at
        # while "Scan matches only" is ticked.
        return (symbol, None, _skipped_pct(tapes, daily_bars, last), passed, reasons, None, last,
                bear_passed, bear_reasons)
    try:
        row = columns.build_row(symbol, tapes, industry)
        failure = None
    except Exception as exc:  # noqa: BLE001 - per-symbol isolation, see above
        row = None
        failure = f"{type(exc).__name__}: {exc}"
    # ONE source of truth. This used to call columns.pct_change(daily_bars)
    # separately, and when that function learned to handle premarket
    # (abc9ced: measure the live price against yesterday's close while
    # today's daily bar does not exist yet) the fix reached build_row and
    # NOT this call - so before the open every row PRINTED today's change
    # while the board was RANKED by yesterday's. rank_board also decides
    # which symbols land in `rows` rather than `rest`, and only `rows` get
    # leased live quotes, so the wrong names got the live treatment too.
    #
    # Reading the row's own number makes the two impossible to separate
    # again. The daily-only expression remains ONLY for a symbol whose row
    # build raised - the single case where there is no row to read.
    pct = row.get("pctChange") if row is not None else columns.pct_change(daily_bars)
    return symbol, row, pct, passed, reasons, failure, last, bear_passed, bear_reasons


def _scan_one_guarded(args):
    """``_scan_one`` with NOTHING left unguarded.

    build_tapes / last_price / scan_symbol / pct_change sat outside the
    try/except above, and pool.map re-raises in the parent: one newly listed
    or halted symbol with an odd tape shape therefore aborted the whole
    parallel pass and sent all 357 symbols down the single-threaded
    sequential path - 2-4x the build time, EVERY cycle, because the next
    build hits the same symbol again (measured by the 2026-09-02 review).
    A symbol that blows up anywhere now costs exactly itself: no row, no
    scan verdict, one recorded error.
    """
    try:
        return _scan_one(args)
    except Exception as exc:  # noqa: BLE001 - one symbol must not take the pool down
        symbol = args[0] if args else "?"
        return symbol, None, None, False, (), f"{type(exc).__name__}: {exc}", None, False, ()


def _pool():
    """The worker LANES: PARALLEL_WORKERS single-process executors, created once.

    One executor per lane (not one shared pool) so a symbol always lands on
    the SAME process build after build (lane = crc32(symbol) % lanes). That
    is what lets momx.indicators' prefix reuse hit: the previous build's
    series for this symbol live in that process. Same worker count and the
    same IDLE priority as before. None if they cannot be made.
    """
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            try:
                from concurrent.futures import ProcessPoolExecutor

                _POOL = [ProcessPoolExecutor(max_workers=1) for _ in range(PARALLEL_WORKERS)]
            except Exception:  # noqa: BLE001 - sequential is always available
                _POOL = False
        return _POOL or None


def _lane_of(symbol: str, lanes: int) -> int:
    import zlib
    return zlib.crc32(str(symbol or "").encode("utf-8")) % max(1, lanes)


def _scan_batch(items):
    """Scan one lane's symbols in one process call (module level: picklable)."""
    return [_scan_one_guarded(item) for item in items]


def shutdown_pool() -> None:
    """Release the workers. Safe to call when idle; the lanes re-create themselves."""
    global _POOL
    with _POOL_LOCK:
        lanes, _POOL = _POOL, None
        for lane in (lanes or []):
            try:
                lane.shutdown(wait=False)
            except Exception:  # noqa: BLE001
                pass


def _tape_as_of(five: Mapping[str, Any]) -> str | None:
    """ISO timestamp of the newest 5m bar anywhere in this build's tape.

    The 5m tape is the freshest feed the board has (it carries the IEX live
    tail), so its newest bar IS the data's wall-clock. None when no frame has
    any bars - the UI treats that the same as unknown.
    """
    newest = None
    for frame in (five or {}).values():
        bars = frame_to_bars(frame)
        if bars:
            t = bars[-1].get("time")
            if isinstance(t, (int, float)) and (newest is None or t > newest):
                newest = t
    if newest is None:
        return None
    import datetime as _dt

    return _dt.datetime.fromtimestamp(int(newest), tz=_dt.timezone.utc).isoformat()


def build_board(
    symbols: Iterable[Any] | None = None,
    *,
    limit: int | None = DEFAULT_LIMIT,
    now: Any = None,
    two_hour_anchor: str = TWO_HOUR_ANCHOR,
    feed_module: Any = None,
    fetch_kwargs: Mapping[str, Any] | None = None,
    full_rows_for: Iterable[Any] | None = None,
    reuse_rows: Mapping[str, Any] | None = None,
) -> dict:
    """Build the whole board, synchronously, and return the payload document.

    ``symbols`` defaults to the persisted universe. ``limit`` is TOS's
    "Show: 50"; pass ``None`` for every row. ``feed_module`` is the test seam
    -- anything exposing ``fetch_5m`` / ``fetch_30m`` / ``fetch_daily``.

    ONE SYMBOL CAN NEVER BREAK THE BOARD. Any exception raised while folding,
    building or scanning a symbol is caught, recorded in ``payload["errors"]``
    under that symbol, and the remaining symbols still return. The same is
    true of a whole tape fetch blowing up.

    ``payload["errors"]`` and ``payload["rows"]`` are not exclusive: a symbol
    whose 5m tape failed but whose daily tape arrived gets BOTH an error note
    and a row with null intraday cells. Only a symbol that RAISED is missing
    from ``rows`` entirely.
    """
    source = feed_module if feed_module is not None else feed
    wanted = _normalize_symbols(symbols if symbols is not None else load_universe())
    errors: dict[str, str] = {}

    if not wanted:
        return {
            "generatedAt": _generated_at(now),
            "direction": "bull",
            "tapeAsOf": None,
            "universe": [],
            "universeCount": 0,
            "rows": [],
            "rest": [],
            "errors": errors,
        }

    five = _fetch(source, "fetch_5m", wanted, "5m", errors, now, fetch_kwargs)
    tape_as_of = _tape_as_of(five)
    thirty = _fetch(source, "fetch_30m", wanted, "30m", errors, now, fetch_kwargs)
    daily = _fetch(source, "fetch_daily", wanted, "daily", errors, now, fetch_kwargs)
    earnings_map = _earnings_map(wanted, now)

    rows: list[dict] = []
    # Industry for EVERY symbol (trader, 2026-08-28): hand map first, then
    # the persistent provider cache, then a rate-budgeted Finnhub/FMP fetch
    # for whatever is still unknown. Never raises, never blocks on a dead
    # provider - an unknown symbol just stays blank this build.
    try:
        industry_map = resolve_industries(wanted)
    except Exception:
        industry_map = {symbol: industry_for(symbol) or "" for symbol in wanted}

    # ONE pass scans AND builds every symbol, inside the pool for a big list.
    # History: until 2026-08-28 the parent built display columns for all 357
    # (97s of a 191s build), then only for the 50 survivors, then the trader
    # unticked "Scan matches only" and asked why the other 307 rows were blank
    # (2026-09-02). Now each pool worker builds its symbol's row right after
    # scanning it (see _scan_one) - the columns cost the same per symbol but
    # run on PARALLEL_WORKERS cores, and the tapes no longer cross the process
    # boundary at all. Measure, don't assume: momx_worker logs the build time.
    scanned: list[dict] = []

    # Which symbols get their display columns rebuilt this cycle. None =
    # all of them, which is the default and every existing caller.
    needs_row = (
        None if full_rows_for is None
        else {str(item).strip().upper() for item in full_rows_for if str(item).strip()}
    )
    skipped = (
        set() if needs_row is None
        else {symbol for symbol in wanted if symbol not in needs_row}
    )
    work = [
        (
            symbol,
            five.get(symbol),
            thirty.get(symbol),
            daily.get(symbol),
            two_hour_anchor,
            industry_map.get(symbol) or None,
            symbol not in skipped,
        )
        for symbol in wanted
    ]
    pool = _pool() if len(wanted) >= PARALLEL_MIN_SYMBOLS else None

    def _absorb(result):
        symbol, row, pct, passed, reasons, failure, last, bear_passed, bear_reasons = result
        if failure:
            _record_error(errors, symbol, failure)
        scanned.append(
            {
                "symbol": symbol,
                "pctChange": pct,
                "scanPass": passed,
                "_reasons": reasons,
                "_row": row,
                "_last": last,
                # The bear verdict rides beside the bull one; bear_view promotes it.
                "bearPass": bear_passed,
                "_bearReasons": bear_reasons,
            }
        )

    done = False
    if pool is not None:
        try:
            batches: list[list] = [[] for _ in pool]
            for item in work:
                batches[_lane_of(item[0], len(pool))].append(item)
            futures = [lane.submit(_scan_batch, batch) for lane, batch in zip(pool, batches) if batch]
            by_symbol = {}
            for future in futures:
                for result in future.result():
                    by_symbol[result[0]] = result
            # Absorbed in the build's own symbol order, exactly as pool.map did.
            for item in work:
                result = by_symbol.get(item[0])
                if result is None:
                    raise RuntimeError(f"lane returned no result for {item[0]}")
                _absorb(result)
            done = True
        except Exception as exc:  # noqa: BLE001
            # A broken pool must never break the board. Drop it, fall back,
            # and let the next build try a fresh one.
            shutdown_pool()
            scanned.clear()
            _record_error(errors, "_pool", f"parallel pass failed, ran sequentially: {exc}")

    if not done:
        for item in work:
            try:
                _absorb(_scan_one(item))
            except Exception as exc:  # noqa: BLE001 - per-symbol isolation
                _record_error(errors, item[0], f"{type(exc).__name__}: {exc}")

    if skipped:
        cached = {
            str(key).strip().upper(): value
            for key, value in (reuse_rows or {}).items()
            if value
        }
        for record in scanned:
            symbol = record["symbol"]
            if symbol not in skipped or record["_row"] is not None:
                continue
            if record["scanPass"] or record["bearPass"]:
                # A NEW match (bull OR bear). It was skipped because it did
                # not match on the previous build, which is exactly the case
                # that must never show last cycle's columns. Build it here -
                # the frames are already in hand and this is a handful of
                # symbols a cycle.
                try:
                    tapes = build_tapes(
                        frame_to_bars(five.get(symbol)),
                        frame_to_bars(thirty.get(symbol)),
                        frame_to_bars(daily.get(symbol)),
                        two_hour_anchor=two_hour_anchor,
                    )
                    record["_row"] = columns.build_row(
                        symbol, tapes, industry_map.get(symbol) or None
                    )
                except Exception as exc:  # noqa: BLE001 - per-symbol isolation
                    _record_error(errors, symbol, f"{type(exc).__name__}: {exc}")
                continue
            # Not matching: reuse the STUDIES from the previous build, but
            # never the price or the percent change. Those are cheap, this
            # cycle already computed them, and shipping stale ones would
            # display one number while ranking on another - the same defect
            # the audit found twice elsewhere today.
            previous_row = cached.get(symbol)
            if previous_row is None:
                # Nothing cached (first build, or newly added to the list).
                # No row means it is dropped from `rows` exactly as a failed
                # build would be, and it returns next cycle.
                continue
            record["_row"] = {
                **previous_row,
                "pctChange": record["pctChange"],
                "last": record["_last"],
            }

    # rank_board picks the survivors from the cheap fields - matches first,
    # never displaced by a non-matching mover. A symbol whose row build failed
    # in the worker is DROPPED from rows (its error is already recorded), the
    # same outcome the old parent-side PASS 2 gave it.
    chosen = [record for record in scan.rank_board(scanned, limit) if record["_row"] is not None]

    def _finished(record: Mapping[str, Any]) -> dict:
        symbol = record["symbol"]
        row = record["_row"]
        return _contract_row(
            row,
            record["scanPass"],
            record["_reasons"],
            _badge(symbol, row, earnings_map),
            record.get("bearPass", False),
            record.get("_bearReasons"),
        )

    rows.extend(_finished(record) for record in chosen)

    # THE REST - everyone rank_board cut, with the SAME full row as the top
    # 50 now that the worker builds every symbol. Still shipped under a
    # SEPARATE key: ``rows`` is what the matchedSince stamping, the history
    # snapshots, the alert evaluator and the fastlane quote poll read, and
    # none of them wants 357 symbols. The panel shows rest only when "Scan
    # matches only" is off.
    shipped = {row["symbol"] for row in rows}
    rest = [
        _finished(record)
        for record in scan.rank_rows(scanned, None)
        if record["symbol"] not in shipped and record["_row"] is not None
    ]

    # News icon for EVERY shipped row, rows and rest alike (the whole
    # universe, one cached fetch - the sorted symbol set is the cache key, so
    # a stable universe costs one request set per 5 minutes; the old top-50
    # set changed with every re-rank and missed the cache far more often).
    # Strictly best-effort: _news_map can only return a dict, so a dead news
    # endpoint costs icons and nothing else.
    news_map = _news_map([row["symbol"] for row in rows] + [row["symbol"] for row in rest], now)
    for row in rows:
        row["news"] = news_map.get(row["symbol"]) or None
    for row in rest:
        row["news"] = news_map.get(row["symbol"]) or None

    # Scanner grade (spec 2026-09-21, Task 5). Computed AFTER news so the
    # grade's news-push check sees the same headline the row displays.
    # safe_grade_row never raises - a grading bug costs a row its grade, not
    # the row itself.
    graded_at = now if isinstance(now, datetime) else datetime.now(timezone.utc)
    for row in rows:
        row["grade"] = grade.safe_grade_row(row, graded_at)
        row["bear"]["grade"] = grade.safe_grade_row(row, graded_at, "bear")
    for row in rest:
        row["grade"] = grade.safe_grade_row(row, graded_at)
        row["bear"]["grade"] = grade.safe_grade_row(row, graded_at, "bear")

    return {
        "generatedAt": _generated_at(now),
        # Which way this board faces. bear_view() derives the other one.
        "direction": "bull",
        # generatedAt is when we BUILT; tapeAsOf is when the newest BAR is
        # from. They diverge whenever the market is closed - the warmer keeps
        # rebuilding all weekend on Friday's frozen tape, and on 2026-08-30 the
        # trader saw Saturday-night "fresh" matches and reasonably asked why.
        # The UI shows a "data as of ..." note when this is old.
        "tapeAsOf": tape_as_of,
        "universe": wanted,
        "universeCount": len(wanted),
        # Already ranked in pass 1 and built in rank order, so no second
        # rank here - re-ranking would be harmless but wasteful.
        "rows": rows,
        # Full rows for every symbol NOT in rows (see above). Empty when
        # limit=None already shipped everything.
        "rest": rest,
        "errors": errors,
    }


def _earnings_map(symbols: Sequence[str], now: Any = None) -> dict:
    """The earnings calendar for the WHOLE build, fetched exactly ONCE.

    This is the only reason the badge is affordable: :mod:`momx.flags` turns
    it into at most one bulk date-range request per build (TTL-cached), never
    one per symbol. A dead provider degrades to an empty map, which lights no
    earnings reason and blanks nothing else.
    """
    try:
        return flags.load_earnings_calendar(symbols, now=now)
    except Exception:  # noqa: BLE001 - a dead calendar must not abort the board
        return {}


def _news_map(symbols: Sequence[str], now: Any = None) -> dict:
    """Newest fresh headline per shipped symbol, fetched exactly ONCE.

    :func:`momx.news.fresh_news` already TTL-caches (~300s), remembers
    failures for 60s, and returns ``{}`` on ANY exception; this second guard
    exists so even an import-time or contract-level failure ships the board
    with no icons rather than no board.
    """
    try:
        fetched = news.fresh_news(symbols, now=now if isinstance(now, datetime) else None)
        return fetched if isinstance(fetched, dict) else {}
    except Exception:  # noqa: BLE001 - news can never break the board
        return {}


def _badge(symbol: Any, row: Mapping[str, Any], earnings_map: Any) -> dict:
    """The lightning badge for one row, or a blank one if anything goes wrong.

    ``flags.build_badge`` already guards each reason individually; this second
    guard exists so that even an import-time or contract-level failure costs
    the row its badge and nothing more.
    """
    try:
        return flags.build_badge(symbol, row, earnings_map=earnings_map)
    except Exception:  # noqa: BLE001 - never abort a row over a badge
        try:
            return flags.empty_badge()
        except Exception:  # noqa: BLE001
            return {"on": False, "reasons": [], "tooltip": ""}


def _contract_row(
    row: Mapping[str, Any],
    passed: Any,
    reasons: Any,
    badge: Any = None,
    bear_passed: Any = False,
    bear_reasons: Any = None,
) -> dict:
    """Re-key one row into payload-contract order, adding the scan verdict."""
    return {
        "symbol": row.get("symbol"),
        # Which board this row is served on. build_board rows are bull;
        # bear_view() rewrites it on the copies it promotes.
        "direction": "bull",
        "industry": row.get("industry"),
        "last": row.get("last"),
        "pctChange": row.get("pctChange"),
        "scanPass": bool(passed),
        "scanReasons": list(reasons or []),
        "rvol": row.get("rvol"),
        "sqz": row.get("sqz"),
        "skittles": row.get("skittles"),
        "highLow": row.get("highLow"),
        "color": row.get("color"),
        "quoteTrend": row.get("quoteTrend"),
        "sparkline": row.get("sparkline"),
        "badge": badge if isinstance(badge, Mapping) else {"on": False, "reasons": [], "tooltip": ""},
        # The ticker card's "1-hr high / 1-hr low". Added 2026-09-02 and
        # promptly forgotten here: build_row produced it, this contract did
        # not list it, so it never reached the wire and the card showed "--"
        # through a worker restart and three rebuilds. THIS function is the
        # payload contract - a field absent here does not exist to the client,
        # however faithfully the builder computes it.
        "hourHighLow": row.get("hourHighLow"),
        # Overwritten after pass 2 by the bulk news fetch; None = no icon.
        "news": None,
        # Scanner grade inputs + result (spec 2026-09-21). grade is filled after
        # news in build_board; m5/sqzRaw come from build_row.
        "m5": row.get("m5"),
        "sqzRaw": row.get("sqzRaw"),
        # Trend strength (columns.adx_cell), RECORDED not graded. Listed here
        # or it does not exist to the client - the hourHighLow note above is
        # the same lesson learnt the hard way.
        "adx": row.get("adx"),
        "grade": None,
        # BEAR scanner (spec 2026-09-24): the bear verdicts for the SAME
        # columns. grade is filled after news like the bull one. bear_view()
        # promotes these onto a copy; service strips the key before the bull
        # board is published, so it never reaches the bull wire or History.
        "bear": {
            "scanPass": bool(bear_passed),
            "scanReasons": list(bear_reasons or []),
            # A freshly built row carries m5Bear; a REUSED contract row does
            # not, but may still hold last cycle's bear block - keep its m5
            # rather than blanking bear momentum (review 2026-09-25).
            "m5": row.get("m5Bear") if "m5Bear" in row else (
                row["bear"].get("m5") if isinstance(row.get("bear"), Mapping) else None),
            "grade": None,
        },
    }


#: Row keys that belong to the BULL board's books and must not leak onto the
#: bear copy (its own books stamp their own): see service._build_once.
BEAR_DROPPED_ROW_KEYS = ("bear", "matchedSince", "gradeFresh", "strategy", "chartSignals",
                         "solo", "hotLeader", "sectorRotation", "marketTurn", "momoxAPlus")
#: Payload keys the bear board carries over verbatim. Anything else on the
#: bull payload (strategyDaily2, strategyG, sector strip ...) is a bull book's
#: and is left behind.
BEAR_PAYLOAD_KEYS = ("generatedAt", "tapeAsOf", "universe", "universeCount", "errors", "list", "optionsGate")


def bear_view(payload: Mapping[str, Any], limit: int | None = DEFAULT_LIMIT) -> dict:
    """The BEAR board of a built payload (spec 2026-09-24).

    Every row of ``rows`` + ``rest`` becomes a SHALLOW copy with the bear
    verdicts promoted under the names the screen already reads (``scanPass``,
    ``scanReasons``, ``m5``, ``grade``), ``direction="bear"``, and the bull
    books' keys removed. Columns are shared by reference - nothing is
    recomputed. Ranked ascending by % change (biggest loser first), matches
    first, never dropped (scan.rank_board's contract).
    """
    everything: list[dict] = []
    for section in ("rows", "rest"):
        for row in payload.get(section) or []:
            if not isinstance(row, Mapping):
                continue
            b = row.get("bear") if isinstance(row.get("bear"), Mapping) else {}
            copy = {k: v for k, v in row.items() if k not in BEAR_DROPPED_ROW_KEYS}
            copy.update({
                "direction": "bear",
                "scanPass": bool(b.get("scanPass")),
                "scanReasons": list(b.get("scanReasons") or []),
                "m5": b.get("m5"),
                "grade": b.get("grade"),
            })
            everything.append(copy)
    rows = scan.rank_board(everything, limit, ascending=True)
    shipped = {r.get("symbol") for r in rows}
    rest = scan.rank_rows([r for r in everything if r.get("symbol") not in shipped], None, ascending=True)
    out = {k: payload.get(k) for k in BEAR_PAYLOAD_KEYS if k in payload}
    out.update({"rows": rows, "rest": rest, "direction": "bear"})
    return out


def strip_bear(payload: Any) -> None:
    """Drop ``row["bear"]`` from a bull payload in place, before it is published."""
    if not isinstance(payload, Mapping):
        return
    for section in ("rows", "rest"):
        for row in payload.get(section) or []:
            if isinstance(row, dict):
                row.pop("bear", None)


def _generated_at(now: Any) -> str:
    if isinstance(now, datetime):
        moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# The one cache this module owns
# ---------------------------------------------------------------------------

_CACHE_LOCK = threading.Lock()
_CACHE: dict[tuple, tuple[float, dict]] = {}


def _monotonic() -> float:
    """Test seam for the TTL clock."""
    return time.monotonic()


def clear_board_cache() -> None:
    """Drop every cached board. Nothing else may reach into ``_CACHE``."""
    with _CACHE_LOCK:
        _CACHE.clear()


def cached_board(
    symbols: Iterable[Any] | None = None,
    ttl_seconds: float = CACHE_TTL_SECONDS,
    **kwargs: Any,
) -> dict:
    """:func:`build_board` behind a small TTL cache keyed on the request.

    Deliberately dumb: no background refresh, no pre-warm, no eviction thread.
    The build happens on the CALLER'S thread, outside the lock, so a slow
    board cannot block a second caller reading a fresh entry. Two callers
    arriving together on a cold key both build, which costs one duplicate
    fetch; a shared in-flight future would cost a wait-graph, and this module
    is not allowed to own one.

    ``ttl_seconds <= 0`` bypasses the cache in both directions -- it neither
    reads nor writes -- which is how a caller asks for a forced rebuild.

    The cached payload is returned BY REFERENCE. Callers must treat it as
    read-only; mutating it mutates what every other caller sees for the rest
    of the TTL.
    """
    wanted = _normalize_symbols(symbols if symbols is not None else load_universe())
    key = (
        tuple(wanted),
        kwargs.get("limit", DEFAULT_LIMIT),
        kwargs.get("two_hour_anchor", TWO_HOUR_ANCHOR),
    )
    try:
        ttl = float(ttl_seconds)
    except (TypeError, ValueError):
        ttl = CACHE_TTL_SECONDS

    stamp = _monotonic()
    if ttl > 0:
        with _CACHE_LOCK:
            entry = _CACHE.get(key)
        if entry is not None and stamp - entry[0] < ttl:
            return entry[1]

    payload = build_board(wanted, **kwargs)
    if ttl > 0:
        with _CACHE_LOCK:
            _CACHE[key] = (stamp, payload)
    return payload


# ---------------------------------------------------------------------------
# Universe persistence
# ---------------------------------------------------------------------------

def universe_path() -> Path:
    """Where the universe lives: ``artifacts/momx_universe.json`` by default.

    ``MOMX_UNIVERSE_PATH`` overrides it, matching how ``config.py`` lets every
    other artifact path be redirected by environment.
    """
    override = os.getenv(UNIVERSE_PATH_ENV)
    if override:
        return Path(override)
    return ARTIFACTS_DIR / UNIVERSE_FILENAME


def _clean_symbol(token: Any, *, strip_header_words: bool = False) -> str | None:
    symbol = str(token or "").strip().strip("\"'").strip().upper()
    if not symbol or len(symbol) > _MAX_SYMBOL_LENGTH:
        return None
    if symbol in _NEVER_SYMBOLS:
        return None
    if strip_header_words and symbol in _NON_SYMBOLS:
        return None
    if not _SYMBOL_RE.match(symbol):
        return None
    return symbol


def _split_fields(line: str) -> list[str] | None:
    """Split on EVERY delimiter at once, or None when the line has none.

    Splitting on only the first delimiter found left ``AAPL MSFT, NVDA;TSLA``
    as the two fields ``AAPL MSFT`` and ``NVDA;TSLA``, and the semicolon field
    then failed the ticker test and demoted the whole line to an export
    record -- which dropped three of the four symbols.
    """
    if not _FIELD_SPLIT_RE.search(line):
        return None
    return [field.strip() for field in _FIELD_SPLIT_RE.split(line)]


def _header_symbol_column(fields: Sequence[str]) -> int | None:
    for index, field in enumerate(fields):
        if field.strip().strip("\"'").strip().upper() in _SYMBOL_HEADERS:
            return index
    return None


def _dedupe(symbols: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for symbol in symbols:
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _looks_like_header(tokens: list[str]) -> bool:
    """True when a line is a column header rather than a row of tickers.

    Position alone is not enough: "NET LOW OPEN AAPL" is a perfectly ordinary
    first line of a pasted watchlist, and three of those four are also column
    names. So require real evidence -- either an explicit Symbol/Ticker column
    name, or EVERY token being a header word. One real ticker anywhere on the
    line means it is data, and no header word on it will be stripped.
    """
    cleaned = [str(token or "").strip().upper() for token in tokens]
    cleaned = [token for token in cleaned if token]
    if not cleaned:
        return False
    if any(token in _SYMBOL_HEADERS for token in cleaned):
        return True
    return all(token in _NON_SYMBOLS for token in cleaned)


def parse_universe(text: Any) -> list[str]:
    """Any pasted symbol list -> a clean, ordered, de-duplicated ticker list.

    Accepts commas, tabs, semicolons, pipes, spaces, newlines, and a
    thinkorswim CSV export with a header row and extra columns. When a header
    naming a ``Symbol``/``Ticker`` column is found, ONLY that column is read
    from the rows after it -- a description column full of words like "CORP"
    must not leak in as tickers. With no header, the first ticker-shaped field
    of each delimited row is taken.

    Symbols are uppercased, de-duplicated, and returned in first-seen order.
    Obvious non-symbols -- numbers, punctuation runs, column headers, "N/A",
    anything longer than 12 characters -- are dropped silently, because a
    paste is a human action and a clean subset beats a board full of junk
    rows.
    """
    if text is None:
        return []
    if not isinstance(text, str):
        if isinstance(text, Iterable):
            return _dedupe(
                symbol for symbol in (_clean_symbol(item) for item in text) if symbol
            )
        return []

    symbol_column: int | None = None
    found: list[str] = []
    # Header words are stripped from the FIRST non-empty line only. After
    # that, "NET" is Cloudflare and "LOW" is Lowe's.
    seen_first_line = False
    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        strip_header_words = not seen_first_line and _looks_like_header(
            _split_fields(line) or line.split()
        )
        seen_first_line = True
        fields = _split_fields(line)
        if fields is None:
            # Whitespace-separated (or single-token) line: take every
            # ticker-shaped word on it.
            for token in line.split():
                symbol = _clean_symbol(token, strip_header_words=strip_header_words)
                if symbol:
                    found.append(symbol)
            continue
        header_index = _header_symbol_column(fields)
        if header_index is not None:
            symbol_column = header_index
            continue  # the header row itself contributes nothing
        if symbol_column is not None:
            if symbol_column < len(fields):
                symbol = _clean_symbol(fields[symbol_column])
                if symbol:
                    found.append(symbol)
            continue
        found.extend(_symbols_from_unheaded_row(fields))
    return _dedupe(found)


def _symbols_from_unheaded_row(fields: Sequence[str]) -> list[str]:
    """Delimited line, no header seen yet: is it a list, or an export record?

    ``AAPL,MSFT,NVDA`` and ``AAPL MSFT, NVDA`` are pasted lists and every
    ticker on them is wanted. ``NVDA,NVIDIA CORP,180.52`` is an export row and
    only its first field is.

    The tell is whether EVERY whitespace-separated token on the line is
    ticker-shaped. A price ("180.52"), a share count, a percentage or a
    punctuation cell is not, and its presence proves the line carries columns.
    Tokens rather than whole fields are tested so a list pasted with mixed
    separators does not silently lose its first two symbols.

    KNOWN AMBIGUITY: a headerless two-column export whose description column
    happens to be all ticker-shaped words -- ``NVDA,NVIDIA CORP`` with no
    price column and no header -- reads as a list and contributes NVIDIA and
    CORP. There is no signal left in that line to tell the two cases apart,
    and the trader can see and delete a stray row; silently dropping symbols
    from a real list is the failure he cannot see.
    """
    tokens = [token for field in fields for token in str(field).split()]
    if not tokens:
        return []
    cleaned = [_clean_symbol(token) for token in tokens]
    if all(symbol is not None for symbol in cleaned):
        return [symbol for symbol in cleaned if symbol]
    for symbol in cleaned:
        if symbol:
            return [symbol]
    return []


# ---------------------------------------------------------------------------
# Named lists
# ---------------------------------------------------------------------------

class UnknownListError(ValueError):
    """A caller named a list that does not exist.

    Carries the known names so an HTTP layer can turn it straight into a 400
    with a message the trader can act on.
    """

    def __init__(self, name: Any, known: Sequence[str], message: str | None = None):
        self.name = str(name)
        self.known = list(known)
        # `message` is for the cases that are not "no such list" but still
        # belong on a 400 the trader can act on -- restoring a list that has no
        # seed, deleting a built-in one. Without it those read as
        # "Unknown list '<a whole sentence>'", which is worse than useless.
        super().__init__(
            message
            or f"Unknown list {self.name!r}. Known lists: {', '.join(self.known) or '(none)'}."
        )


def _clean_symbols(symbols: Any) -> list[str]:
    """Any sequence (or pasted blob) -> clean, ordered, de-duplicated tickers."""
    if isinstance(symbols, str):
        return parse_universe(symbols)
    if not isinstance(symbols, (list, tuple, set, frozenset)):
        return []
    return _dedupe(
        symbol for symbol in (_clean_symbol(item) for item in symbols) if symbol
    )


def _mega7_seed() -> list[str]:
    return list(MEGA7_SEED)


def _watchlist_seed() -> list[str]:
    """``settings.scanner.default_universe`` -- watchlist.txt, ~355 names.

    Read on every call rather than captured at import: the running server
    edits ``settings.scanner.default_universe`` when the trader changes his
    watchlist, and a snapshot taken at import time would silently freeze the
    seed at whatever the file said when the process booted.
    """
    try:
        raw = list(getattr(settings.scanner, "default_universe", None) or [])
    except Exception:  # noqa: BLE001 - a broken settings object must not blank the board
        raw = []
    return _clean_symbols(raw) or list(MEGA7_SEED)


#: name -> seed factory, in display order. THE list of lists.
LIST_SEEDS: dict[str, Any] = {
    MEGA7_LIST_NAME: _mega7_seed,
    WATCHLIST_LIST_NAME: _watchlist_seed,
}


def _seed_for(name: str) -> list[str]:
    factory = LIST_SEEDS.get(name)
    return list(factory()) if factory else []


def _migration_target(symbols: Sequence[str]) -> str:
    """Which named list a schema-1 flat file belongs to.

    A flat file is the trader's own paste, so it is never dropped. If it is
    exactly one of the seeds it carries no information and goes back to that
    list; anything else is the big pasted export and becomes ``Watchlist``,
    which is what he was actually looking at before the split.
    """
    wanted = set(symbols)
    for name in LIST_SEEDS:
        if wanted and wanted == set(_seed_for(name)):
            return name
    return WATCHLIST_LIST_NAME


def _document_from_raw(raw: Any) -> dict:
    """Any on-disk shape -> ``{"active": name, "lists": {name: [symbols]}}``.

    Never raises. Handles three shapes:

    * schema 2 -- ``{"lists": {...}, "active": "..."}``
    * schema 1 -- ``{"symbols": [...]}`` or a bare ``[...]``; MIGRATED into a
      named list (see :func:`_migration_target`) and made active, so a trader
      who pasted a list keeps both his symbols and the board he was on.
    * anything else -- seeds only.

    Every seeded name is always present, and no list is ever empty: an empty
    list reads back as its seed, because a board with no universe looks
    exactly like a broken board.
    """
    lists: dict[str, list[str]] = {}
    active: str | None = None

    requested = ""
    if isinstance(raw, Mapping):
        stored = raw.get("lists")
        if isinstance(stored, Mapping):
            for name, symbols in stored.items():
                clean_name = str(name or "").strip()
                if clean_name:
                    lists[clean_name] = _clean_symbols(symbols)
            # Resolved against `ordered` below, NOT against `lists`. A seeded
            # list (one with no saved copy, like Watchlist once its snapshot is
            # dropped) is absent from `lists`, so matching here silently threw
            # the trader's active tab away and fell back to Mag7 - which is
            # exactly what happened on 2026-08-27 when the stale EA snapshot
            # was removed.
            requested = str(raw.get("active") or "").strip()
        else:
            migrated = _clean_symbols(raw.get("symbols"))
            if migrated:
                active = _migration_target(migrated)
                lists[active] = migrated
    elif isinstance(raw, (list, tuple)):
        migrated = _clean_symbols(raw)
        if migrated:
            active = _migration_target(migrated)
            lists[active] = migrated

    ordered: dict[str, list[str]] = {}
    for name in LIST_SEEDS:
        ordered[name] = lists.get(name) or _seed_for(name)
    for name, symbols in lists.items():
        if name not in ordered:
            ordered[name] = symbols or _seed_for(name)

    # Now that every seeded name exists in `ordered`, honour the saved active
    # tab. A name that no longer exists at all still falls back to the default.
    if requested and active is None:
        for existing in ordered:
            if existing.lower() == requested.lower():
                active = existing
                break

    if active not in ordered:
        active = DEFAULT_LIST_NAME if DEFAULT_LIST_NAME in ordered else next(
            iter(ordered), DEFAULT_LIST_NAME
        )
    return {"active": active, "lists": ordered}


def _read_document(path: Any = None) -> dict:
    """The whole named-list document. Never raises."""
    target = Path(path) if path is not None else universe_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        raw = None
    return _document_from_raw(raw)


def _write_document(document: Mapping[str, Any], path: Any = None) -> None:
    """Write the whole document atomically (temp file + ``os.replace``).

    Whole-document writes are why one list can never clobber another: the
    caller always starts from :func:`_read_document`, which has already
    migrated and seeded, so the untouched lists are written back intact.
    """
    target = Path(path) if path is not None else universe_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # A list identical to its seed is NOT persisted. _read_document seeds every
    # known name, so writing the document back verbatim - which set_active_list
    # does on a plain tab switch - would materialise the seeded Watchlist as a
    # saved snapshot. From then on it stops tracking settings.scanner
    # .default_universe, and a ticker deleted from My Watchlist keeps being
    # scanned. That is exactly the EA incident of 2026-08-27: the trader deleted
    # EA, the board kept warning "No data came back for EA", and the cause was a
    # saved copy nobody knew had been created by switching tabs.
    #
    # Omitting seed-identical lists keeps them lazy for good. An explicit paste
    # still saves, because a pasted list differs from the seed.
    kept: dict[str, list[str]] = {}
    for name, symbols in (document.get("lists") or {}).items():
        current = list(symbols)
        if name in LIST_SEEDS and current == list(_seed_for(name)):
            continue
        kept[name] = current
    payload = {
        "schemaVersion": UNIVERSE_SCHEMA_VERSION,
        "savedAt": datetime.now(timezone.utc).isoformat(),
        "active": document.get("active") or DEFAULT_LIST_NAME,
        "lists": kept,
    }
    temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        # Keep ONE generation of the previous file before replacing it. On
        # 2026-09-01 a single symbol went through the Tickers box while the
        # Watchlist tab was active; that one write replaced 357 names and the
        # only copy of them was gone in the same instant. The write below is
        # atomic but strictly one-generation, so "atomic" never meant
        # "recoverable". This costs one small file copy per save and turns
        # every future mistake into something that can be undone.
        try:
            if target.exists():
                shutil.copy2(target, target.with_name(f"{target.name}.prev"))
        except OSError:
            pass  # a backup that cannot be written must never block the save
        # os.replace is atomic on POSIX, but on Windows it fails outright with
        # ERROR_ACCESS_DENIED (WinError 5) if ANY process has the destination
        # open - and this file is read by the warmer on every build, by every
        # /lists poll, and by the second worker process. Hit live on
        # 2026-09-02 during a rename. A short retry turns a hard failure into a
        # few milliseconds of waiting; the alternative is a write that
        # half-lands and a list the trader cannot account for.
        for attempt in range(_REPLACE_ATTEMPTS):
            try:
                os.replace(temporary, target)
                break
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS - 1:
                    raise
                time.sleep(_REPLACE_BACKOFF_SECONDS * (attempt + 1))
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def resolve_list_name(
    name: Any = None, *, path: Any = None, document: Mapping[str, Any] | None = None
) -> str:
    """A requested name -> the canonical stored name. Blank/None -> the active one.

    Matching is case-insensitive ("mag7" finds "Mag7") because the name
    travels through a URL query string. An unrecognised name raises
    :class:`UnknownListError` rather than quietly falling back, so a typo
    surfaces as a 400 instead of the wrong board.
    """
    doc = document if document is not None else _read_document(path)
    lists = doc.get("lists") or {}
    wanted = str(name or "").strip()
    if not wanted:
        active = doc.get("active")
        return active if active in lists else DEFAULT_LIST_NAME
    for existing in lists:
        if existing.lower() == wanted.lower():
            return existing
    raise UnknownListError(wanted, list(lists))


def list_names(path: Any = None) -> list[str]:
    """Every known list name, in display order (seeded names first)."""
    return list((_read_document(path).get("lists") or {}).keys())


def active_list(path: Any = None) -> str:
    """The list a caller gets when it names none."""
    return resolve_list_name(None, path=path)


def set_active_list(name: Any, path: Any = None) -> str:
    """Persist which list is active. Returns the canonical name."""
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    document["active"] = target
    _write_document(document, path)
    return target


def load_universes(path: Any = None) -> dict[str, list[str]]:
    """Every named list at once -- one file read, for callers that need all."""
    return {
        name: list(symbols)
        for name, symbols in (_read_document(path).get("lists") or {}).items()
    }


def load_universe(name: Any = None, *, path: Any = None) -> list[str]:
    """One named list's symbols, or its seed when there is none saved.

    ``name`` defaults to the active list, which is what the old
    single-universe ``load_universe()`` call means now. Never raises for a
    missing, unreadable or truncated file, and never returns an empty list:
    those all fall back to the list's seed, because a board with no universe
    looks exactly like a broken board. A name that is not a known list DOES
    raise :class:`UnknownListError` -- that is a caller bug, not bad data.
    """
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    symbols = (document.get("lists") or {}).get(target) or []
    return list(symbols) or _seed_for(target)


def save_universe(
    symbols: Any, name: Any = None, *, path: Any = None, create: bool = False
) -> list[str]:
    """Replace ONE named list and return exactly what was written.

    ``symbols`` may be a sequence or one pasted blob of text; text goes
    through :func:`parse_universe`. ``name`` defaults to the active list, so
    the old single-argument call still means "save the universe I am on".
    Every other list is written back untouched.

    ``create=True`` adds a list under a brand-new name; without it an unknown
    name raises :class:`UnknownListError`, so a typo cannot silently spawn a
    third board that nobody is watching.
    """
    cleaned = parse_universe(symbols) if isinstance(symbols, str) else _clean_symbols(symbols)
    document = _read_document(path)
    try:
        target = resolve_list_name(name, document=document)
    except UnknownListError:
        if not create:
            raise
        target = str(name).strip()
    document["lists"][target] = cleaned
    _write_document(document, path)
    return cleaned


def add_symbols(
    symbols: Any, name: Any = None, *, path: Any = None
) -> dict:
    """APPEND tickers to ONE named list. The safe counterpart to save_universe.

    Returns ``{"universe", "added", "already", "before", "after",
    "materialised"}`` so the caller can tell the trader what actually changed
    rather than just "saved".

    Order-preserving and de-duplicating: existing symbols keep their position,
    genuinely new ones are appended in the order pasted. Adding a symbol that
    is already there is not an error -- it is reported in ``already``.

    MATERIALISATION. A seed-backed list (``Watchlist`` follows watchlist.txt,
    see :func:`_watchlist_seed`) has no saved key at all. Appending to it must
    write a real key, which stops it tracking the seed -- the EA incident of
    2026-08-27. That is unavoidable: "add one ticker" and "keep mirroring a
    file I did not change" cannot both be true. So it is done openly:
    ``materialised`` is True on the write that converts a seed-backed list into
    a saved one, and the panel says so in plain words. :func:`reset_universe`
    puts it back to tracking.
    """
    incoming = parse_universe(symbols) if isinstance(symbols, str) else _clean_symbols(symbols)
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    # `existing` must come from the RESOLVED view, not the raw document: a
    # seed-backed list has no key, and starting from [] there would silently
    # replace 357 names with the two he just typed.
    existing = list(load_universe(target, path=path))
    # "Is this list still tracking its seed?" is a CONTENT test, not a key
    # test: _read_document seeds every known name into document["lists"], so
    # `target not in document["lists"]` is never true for a seeded list. This
    # is the same rule _write_document uses to decide what to persist.
    seed = list(_seed_for(target)) if target in LIST_SEEDS else []
    was_tracking = bool(seed) and existing == seed
    known = set(existing)
    added = [symbol for symbol in incoming if symbol not in known]
    already = [symbol for symbol in incoming if symbol in known]
    merged = existing + added
    document["lists"][target] = merged
    _write_document(document, path)
    return {
        "universe": merged,
        "added": added,
        "already": already,
        "before": len(existing),
        "after": len(merged),
        # Only a write that ACTUALLY changed something converts the list; an
        # add of nothing but duplicates leaves a tracking list tracking,
        # because _write_document drops a seed-identical list on the way out.
        "materialised": bool(was_tracking and merged != seed),
    }


def remove_symbols(
    symbols: Any, name: Any = None, *, path: Any = None
) -> dict:
    """Drop tickers from ONE named list. Mirror of :func:`add_symbols`."""
    outgoing = parse_universe(symbols) if isinstance(symbols, str) else _clean_symbols(symbols)
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    existing = list(load_universe(target, path=path))
    seed = list(_seed_for(target)) if target in LIST_SEEDS else []
    was_tracking = bool(seed) and existing == seed
    drop = set(outgoing)
    kept = [symbol for symbol in existing if symbol not in drop]
    removed = [symbol for symbol in existing if symbol in drop]
    missing = [symbol for symbol in outgoing if symbol not in set(existing)]
    document["lists"][target] = kept
    _write_document(document, path)
    return {
        "universe": kept,
        "removed": removed,
        "missing": missing,
        "before": len(existing),
        "after": len(kept),
        "materialised": bool(was_tracking and kept != seed),
    }


def reset_universe(name: Any = None, *, path: Any = None) -> list[str]:
    """Drop the saved override for ONE list so it tracks its seed again.

    This is the undo for a bad paste. It does not write the seed's symbols
    back in -- it DELETES the saved key, which is what makes the list lazy
    again: :func:`_read_document` re-seeds an absent name from
    :data:`LIST_SEEDS`, so ``Watchlist`` goes back to following
    ``settings.scanner.default_universe`` (watchlist.txt) and picks up every
    future edit the trader makes there. Writing the 357 symbols back as an
    explicit list would restore the count but re-create the EA incident of
    2026-08-27, where a materialised copy silently stopped tracking.

    Returns the symbols the list now resolves to, so the caller can report a
    real count rather than a promise.

    SEEDED LISTS ONLY. Dropping the key of a user-created list is not a reset,
    it is a deletion: there is no seed to fall back to, so the list would be
    gone from disk and the ``load_universe`` below would then raise -- leaving
    the caller with a 400 saying the list does not exist, AFTER the write had
    already destroyed it, and with none of the service-level teardown run. That
    is the same shape as the paste that cost 357 tickers. Deleting a list is a
    different, deliberate action: see :func:`delete_list`.
    """
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    if target not in LIST_SEEDS:
        raise UnknownListError(
            target,
            list((document.get("lists") or {}).keys()),
            message=(
                f"{target} is your own list, so there is nothing to restore it "
                f"to. Delete it instead if you no longer want it."
            ),
        )
    (document.get("lists") or {}).pop(target, None)
    _write_document(document, path)
    return list(load_universe(target, path=path))


def rename_list(name: Any, new_name: Any, *, path: Any = None) -> dict:
    """Rename a user-created list in ONE document write.

    Deliberately not "create the new one, then delete the old one": that is
    three writes (save, delete, set-active), and on Windows a mid-sequence
    ``os.replace`` failure left BOTH names on disk with the caller told only
    that something went wrong. Observed live 2026-09-02. One read, one mutate,
    one write cannot half-succeed.
    """
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    fresh = str(new_name or "").strip()
    if target in LIST_SEEDS:
        raise UnknownListError(
            target,
            list(LIST_SEEDS.keys()),
            message=f"{target} is a built-in list, so its name cannot be changed.",
        )
    if not fresh:
        raise UnknownListError(target, list(LIST_SEEDS.keys()), message="Give the list a name.")
    lists = document.get("lists") or {}
    if fresh.lower() != target.lower() and any(
        fresh.lower() == known.lower() for known in lists
    ):
        raise UnknownListError(
            target,
            list(lists.keys()),
            message=f"You already have a list called {fresh}.",
        )
    # Rebuild the mapping in place so the renamed list keeps its POSITION in
    # the picker rather than jumping to the end.
    rebuilt: dict[str, list[str]] = {}
    for key, symbols in lists.items():
        rebuilt[fresh if key == target else key] = list(symbols)
    document["lists"] = rebuilt
    if document.get("active") == target:
        document["active"] = fresh
    _write_document(document, path)
    return {"list": fresh, "renamedFrom": target, "universe": list(rebuilt.get(fresh) or [])}


def delete_list(name: Any, *, path: Any = None) -> dict:
    """Remove a user-created list for good. Seeded lists cannot be deleted.

    Returns ``{"deleted": name, "active": <name now active>, "names": [...]}``.

    Deliberately does NOT call :func:`load_universe` on the deleted name the
    way :func:`reset_universe` does -- that name no longer resolves, and doing
    so is exactly the bug this function exists to avoid. If the deleted list
    was the active one, ``active`` moves to the default so the panel does not
    open on a tab that is gone.
    """
    document = _read_document(path)
    target = resolve_list_name(name, document=document)
    if target in LIST_SEEDS:
        raise UnknownListError(
            target,
            list(LIST_SEEDS.keys()),
            message=(
                f"{target} is a built-in list and cannot be deleted. Use "
                f"Restore to put it back to its default tickers."
            ),
        )
    (document.get("lists") or {}).pop(target, None)
    if document.get("active") == target:
        document["active"] = DEFAULT_LIST_NAME
    _write_document(document, path)
    remaining = _read_document(path)
    return {
        "deleted": target,
        "active": remaining.get("active") or DEFAULT_LIST_NAME,
        "names": list((remaining.get("lists") or {}).keys()),
    }


__all__ = [
    "CACHE_TTL_SECONDS",
    "DAILY_SPANS",
    "DEFAULT_LIMIT",
    "DEFAULT_LIST_NAME",
    "DEFAULT_UNIVERSE",
    "add_symbols",
    "delete_list",
    "remove_symbols",
    "reset_universe",
    "INTRADAY_FROM_30M",
    "INTRADAY_FROM_5M",
    "LIST_SEEDS",
    "MEGA7_LIST_NAME",
    "MEGA7_SEED",
    "TIMEFRAME_KEYS",
    "TWO_HOUR_ANCHOR",
    "UNIVERSE_FILENAME",
    "UNIVERSE_PATH_ENV",
    "UNIVERSE_SCHEMA_VERSION",
    "UnknownListError",
    "WATCHLIST_LIST_NAME",
    "active_list",
    "build_board",
    "build_tapes",
    "cached_board",
    "clear_board_cache",
    "frame_to_bars",
    "list_names",
    "load_universe",
    "load_universes",
    "parse_universe",
    "resolve_list_name",
    "save_universe",
    "set_active_list",
    "universe_path",
]
