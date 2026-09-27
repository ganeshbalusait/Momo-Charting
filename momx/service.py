"""The MomX board as a warmed service, so no HTTP handler ever waits on a build.

Why this module exists, separately from :mod:`momx.board`:

``board.cached_board`` builds on the CALLER'S thread by design -- it owns no
threads, and a test asserts that importing ``momx.board`` starts none. That is
the right shape for a library, and the wrong shape for a request handler: a
real ten-symbol build measured 24.7s against live Alpaca, so serving the board
straight out of ``cached_board`` would block a request thread for ~25s every
time the 60s TTL lapsed. This repo has been bitten repeatedly by exactly that
pattern -- a slow collector on a hot path starving everything else.

So the split is:

* ``momx.board``   -- pure, synchronous, thread-free. Safe to import anywhere.
* ``momx.service`` -- owns ONE daemon thread, started explicitly (never at
  import), refreshes on a timer, and hands out the last completed payload
  instantly. Readers never block on a build.

The thread starts on the first :func:`snapshot` call, not on import, so merely
importing this module from ``api_server`` costs nothing.

NAMED LISTS
-----------
There is more than one board now (``Mag7``, ``Watchlist``; see
``momx.board.LIST_SEEDS``). Each named list gets its own last-completed
payload and its own due time, but they share ONE warmer thread and are built
SEQUENTIALLY. That is deliberate and non-negotiable: two concurrent
355-symbol Alpaca pulls is exactly the CPU/network pile-up this repo keeps
suffering, and one thread makes concurrency impossible by construction rather
than by discipline.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from zoneinfo import ZoneInfo

from config import ARTIFACTS_DIR, EASTERN_TZ
from momx import board, chart_signals, feed, grade_log, history, market_turn, momo_alert, momox_aplus, news_catalyst, optionable, scan, sectors, solo, strategy

#: How often the warmer rebuilds a list. 15s makes the board feel like the
#: TOS scanner -- continuously re-running, no dead minute between builds --
#: while staying far under Alpaca's 200 req/min (a build is ~8-14 batched
#: incremental tail fetches). Do NOT lower it further: it is a FLOOR, not a
#: fixed period -- see :func:`_next_delay`, which still caps duty cycle at 50%.
REFRESH_SECONDS = 15.0

#: Every Nth build rebuilds EVERY row's study columns, ignoring the reuse
#: cache. Between those, a symbol that is not matching keeps the columns
#: from the previous build (see board.build_board's full_rows_for /
#: reuse_rows) because build_row is 53% of the per-symbol cost and was
#: being paid for ~290 non-matching symbols a cycle.
#:
#: 4 bounds a non-matching row's studies at three cycles old. Matching
#: rows - the ones he trades - are rebuilt EVERY cycle regardless, and so
#: is any symbol that newly matches.
FULL_ROW_REFRESH_EVERY = 4

#: Hard ceiling on the post-build idle. See _next_delay: without it a single
#: slow build silently freezes the board for as long as the build took.
MAX_IDLE_SECONDS = 45.0

#: How long to wait after a failed build before trying again. Deliberately
#: not zero so a hard outage cannot become a hot retry loop; short enough
#: that a transient Alpaca blip heals within one refresh.
RETRY_SECONDS = 15.0

#: Longest the loop sleeps between checks. Only a ceiling on responsiveness to
#: a newly-added list; a due list is never delayed by it, because
#: :func:`_sleep_span` sleeps exactly until the earliest due time.
MAX_SLEEP_SECONDS = 60.0

#: Shortest the loop sleeps. Stops a pathological due-time from spinning.
MIN_SLEEP_SECONDS = 0.5

#: How much of a build's own duration a list rests for afterwards. 1.0 was the
#: old rule and held the Watchlist at ~50% duty; it existed to stop a build
#: starting before the previous one landed, which the per-list claim and heavy
#: gate now guarantee outright. At 0.5 a 106s build rests 53s instead of 106s,
#: taking that board from a ~212s cadence to ~159s at ~67% duty.
#: 2026-09-25 ("I want scanner no late, make it fast"): 0.5 -> 0.2 and the cap
#: 120s -> 45s. The pool children run at IDLE priority (verified: every
#: worker process PriorityClass Idle, api_server BelowNormal), so Windows
#: already yields every core the charts want; the rest period only added
#: staleness - Friday's boards were 2-6 minutes apart.
IDLE_FRACTION_OF_BUILD = 0.2

#: Where each list's last completed board is cached ON DISK so a restart serves
#: the last board in milliseconds instead of "UPDATED never" for the minutes a
#: cold rebuild takes. One file per list, ``artifacts/momx_board_cache/<list>.json``,
#: written whole and atomically (temp file + ``os.replace``) exactly like
#: ``premarket_scanner_history.py`` -- a half-written file must never be read.
#: ``MOMX_BOARD_CACHE_DIR`` overrides it, so a test never touches the real cache.
BOARD_CACHE_DIRNAME = "momx_board_cache"
BOARD_CACHE_DIR_ENV = "MOMX_BOARD_CACHE_DIR"

#: A disk board older than this is STILL served -- old tickers beat no tickers,
#: and the warmer replaces it within a cycle anyway -- but it is flagged as
#: coming from a previous session so the panel can say so. It is never silently
#: discarded; nothing keys on this to drop a board.
STALE_DISK_MAX_AGE_SECONDS = 24 * 60 * 60


class _ListState:
    """One named list's warmed board. Guarded by ``_LOCK``."""

    __slots__ = (
        "payload", "error", "builds", "last_build_seconds", "due_at", "matched",
        "wake", "building", "bear", "bear_matched",
    )

    def __init__(self) -> None:
        self.payload: dict | None = None
        # The BEAR board of the same build (board.bear_view) and ITS
        # matched-since map. Published together with ``payload``; reset
        # wherever ``payload`` is (spec 2026-09-24).
        self.bear: dict | None = None
        self.bear_matched: dict[str, str] | None = None
        self.error: str = ""
        self.builds: int = 0
        self.last_build_seconds: float = 0.0
        # 0.0 = due now: a list is built the first time the warmer sees it.
        self.due_at: float = 0.0
        # {SYMBOL: enteredAtIso} for every symbol currently scanPass on this
        # list -- WHEN each one entered the matched set. None means "never
        # initialised this session": the first build seeds it from the disk
        # cache so BUD/DHI keep their Friday stamps across a restart. It is
        # not the payload itself: the payload's rows CARRY the stamps, this
        # map is the memory that keeps them stable build over build.
        self.matched: dict[str, str] | None = None
        # THIS list's cadence-cutter, so refresh_now() can shorten the wait of
        # the list it names and no other. Per-list rather than shared: with a
        # thread per list one shared Event is unreliable, because the first
        # thread to wake clears it and the others sleep out their full span
        # having never seen it set. Assigned once here and NEVER replaced,
        # which is what makes it safe to read without holding _LOCK.
        self.wake = threading.Event()
        # True while SOME thread is building this list. Claimed and cleared
        # under _LOCK; it is the only thing standing between the warm thread
        # and a direct _warm_due_lists() caller both building at once.
        self.building: bool = False


_LOCK = threading.Lock()
#: The SUPERVISOR thread. It builds nothing: its only job is to make sure
#: every known list has a warm thread, including a list pasted long after
#: startup. Keeps the historic name because status() reports it as ``running``
#: and momx_worker's health check reads that.
_THREAD: threading.Thread | None = None
#: list name -> its warm thread. Read and written under _LOCK only.
_THREADS: dict[str, threading.Thread] = {}
_STATES: dict[str, _ListState] = {}
#: The SUPERVISOR's event: "the set of lists may have changed, look again".
#: NOT the per-list cadence-cutter -- that is _ListState.wake; see the note
#: there for why one shared event cannot do both jobs.
_WAKE = threading.Event()

#: A list at least this big is "heavy": its build engages the process pool in
#: momx.board and pulls hundreds of symbols. Mag7 (10) is never heavy;
#: Watchlist (357) always is.
HEAVY_LIST_MIN_SYMBOLS = 50

#: At most ONE heavy build in flight, whatever the thread count. This is the
#: original "two concurrent 355-symbol pulls must be impossible" invariant,
#: preserved by construction. BoundedSemaphore so a release without a matching
#: acquire raises HERE instead of silently widening the ceiling and letting
#: the pile-up back in months later.
_HEAVY_GATE = threading.BoundedSemaphore(1)

#: Set only by _stop_warmer(), which exists for tests. Production never sets
#: it: the warmer runs until the process exits, and the threads are daemons.
_STOP = threading.Event()

#: How often the supervisor re-checks for newly added lists.
SUPERVISOR_POLL_SECONDS = 30.0


# ---------------------------------------------------------------------------
# state helpers
# ---------------------------------------------------------------------------

def _state(name: str) -> _ListState:
    """The state for ``name``, created on first sight. Caller must hold no lock."""
    with _LOCK:
        state = _STATES.get(name)
        if state is None:
            state = _STATES[name] = _ListState()
        return state


def _known_names() -> list[str]:
    """Every list the board knows about, never empty, never raising."""
    try:
        names = board.list_names()
    except Exception:  # noqa: BLE001 - a broken universe file must not stop the warmer
        names = []
    return names or [board.DEFAULT_LIST_NAME]


#: OPTIONS ONLY (2026-09-26, Ganesh: "in scanner only show which ticker have
#: option only. Don't scan which have no option - no guess work"). The store
#: is momx/optionable.py - real option chains, never a hand-made list. Only a
#: CONFIRMED yes is scanned; a confirmed no is hidden; a not-yet-checked
#: ticker is held out until its chain is read (queued first). Filtering the
#: universe BEFORE the build keeps every consumer - bull and bear rows, the
#: books, History, alerts, the live bolt - from ever seeing a hidden name, and
#: saves the tape fetch and compute. AGX_MOMX_OPTIONS_ONLY=0 turns it off (the
#: test suite does, so fixture tickers still build).
_OPTIONABLE = optionable.OptionableBook()


def options_only() -> bool:
    return os.environ.get("AGX_MOMX_OPTIONS_ONLY", "1").strip() != "0"


def _options_gate(universe: Any, *, request: bool = True) -> tuple[list, dict | None]:
    """(symbols to scan, payload["optionsGate"] or None when the gate is off)."""
    symbols = list(universe or [])
    if not options_only():
        return symbols, None
    try:
        kept, hidden, pending = _OPTIONABLE.split(symbols, request=request)
    except Exception:  # noqa: BLE001 - a broken store holds everything out, visibly
        return [], {"listCount": len(symbols), "hidden": [], "pending": symbols}
    return kept, {"listCount": len(symbols), "hidden": hidden, "pending": pending}


def drop_no_options(symbols: Any) -> list[str]:
    """Movers: drop CONFIRMED no-options names before the 40-name cap, so they
    cannot take a slot. Unknown names stay (checked first, held out of the
    build until then)."""
    symbols = [s for s in (symbols or []) if isinstance(s, str)]
    if not options_only():
        return symbols
    try:
        # request=False: of ~120 oversampled names only the 40 kept are
        # checked (by that list's own build), and never ahead of a typed one.
        _kept, hidden, pending = _OPTIONABLE.split(symbols, request=False)
    except Exception:  # noqa: BLE001
        return symbols
    gone = set(hidden)
    return [s for s in symbols if s.strip().upper() not in gone]


def typed_options_note(symbols: Any) -> dict:
    """What the Tickers box tells him about names he just typed."""
    if not options_only():
        return {}
    try:
        _kept, hidden, pending = _OPTIONABLE.split(list(symbols or []))
    except Exception:  # noqa: BLE001
        return {}
    return {"noOptions": hidden, "optionsChecking": pending}


def _warming_payload(name: str, reason: str = "") -> dict:
    """A contract-shaped payload for "the first build has not finished yet".

    Shaped like a real board rather than an error object so the panel renders
    its empty state instead of its failure state -- the board is not broken,
    it just is not ready. ``warming`` is the flag the panel keys on.
    """
    try:
        universe = list(board.load_universe(name))
    except Exception:  # noqa: BLE001
        universe = []
    universe, gate = _options_gate(universe, request=False)
    if reason:
        message = reason
    elif len(universe) > 50:
        message = (
            f"Building the first {name} board from live bars "
            f"({len(universe)} symbols). This takes a few minutes."
        )
    else:
        message = (
            f"Building the first {name} board from live bars. "
            "This takes about half a minute."
        )
    out = {
        "generatedAt": None,
        "direction": "bull",
        "list": name,
        "universe": universe,
        "universeCount": len(universe),
        "rows": [],
        "errors": {},
        "warming": True,
        "message": message,
    }
    if gate is not None:
        out["optionsGate"] = gate
    return out


# ---------------------------------------------------------------------------
# disk persistence -- the last completed board survives a restart
# ---------------------------------------------------------------------------

def _cache_dir() -> Path:
    """Where board caches live: ``artifacts/momx_board_cache`` by default.

    ``MOMX_BOARD_CACHE_DIR`` overrides it, matching how the universe file lets
    its path be redirected by environment, so a test can point it at a tmp dir.
    """
    override = os.getenv(BOARD_CACHE_DIR_ENV)
    if override:
        return Path(override)
    return ARTIFACTS_DIR / BOARD_CACHE_DIRNAME


def _cache_file(name: str, direction: str = "bull") -> Path:
    """The cache file for ONE list (and direction: ``<name>.bear.json`` for
    the bear board). The name is sanitised to a safe filename."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "_"
    suffix = ".bear.json" if scan.is_bear(direction) else ".json"
    return _cache_dir() / f"{safe}{suffix}"


def _write_disk(name: str, payload: Mapping[str, Any], direction: str = "bull") -> None:
    """Atomically cache one list's completed board. NEVER raises.

    Same temp-file + ``os.replace`` pattern ``premarket_scanner_history.py``
    uses: a reader (or a kill) hitting a half-written file must never see it.
    Any failure -- disk full, permission, a payload that will not serialise --
    degrades to in-memory-only; persistence can never break a build.
    """
    try:
        directory = _cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        target = _cache_file(name, direction)
        temporary = target.with_name(f".{target.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(dict(payload)), encoding="utf-8")
        os.replace(temporary, target)
    except Exception:  # noqa: BLE001 - a dead disk must not break the build
        try:
            temporary.unlink()  # type: ignore[possibly-undefined]
        except Exception:  # noqa: BLE001
            pass


def history_dir(direction: str = "bull") -> Path:
    """Where the 30-day scan-match archive lives (``<root>/bear`` for the bear
    boards - spec 2026-09-24). ONE accessor, used by both
    the recorder below and the worker's /history route, so the writer and the
    reader can never disagree about the path.

    The env override exists for the TEST SUITE, not for operators: a conftest
    rebind of this function was silently undone by importlib.reload(service)
    in test_momx_service.py, after which every _build_once in the suite wrote
    its fixture boards (JD/BILI/BUD/DHI/TQQQ) into the REAL archive - fake
    history on the surface the trader reads as "what the scan matched". An
    environment variable survives any number of reloads. Same pattern as the
    prewarm's cache-dir override.
    """
    override = os.environ.get("AGX_MOMX_HISTORY_DIR", "").strip()
    base = Path(override) if override else (ARTIFACTS_DIR / history.HISTORY_DIRNAME)
    return base / "bear" if scan.is_bear(direction) else base


def grade_dir(direction: str = "bull") -> Path:
    """Root of the scanner-grade tape and events (momx/grade_log.py).

    The BEAR recorder lives under ``<root>/bear`` (spec 2026-09-24): the bull
    track record and the scheduled scorecard glob every board folder under
    the root, and a shared folder would score bear A+ events as long trades.

    Env override for the TEST SUITE, same reason as :func:`history_dir`: a
    test driving _build_once must never write fixture grades into the real
    artifacts/ the trade record is built from.
    """
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    base = Path(override) if override else ARTIFACTS_DIR
    return base / "bear" if scan.is_bear(direction) else base


#: Freshness / first-per-letter latch / 5-minute tape / grade events. Built at
#: import (no I/O in the constructor); its state lives for the worker process.
_GRADE_LOG = grade_log.GradeLog(grade_dir())

#: V2 / V3 / Daily 2 verdicts per row + the Daily 2 latch (momx/strategy.py),
#: read by the FILTERS "A/A+ Setup" dropdown. Same test override as the grade.
_STRATEGY = strategy.StrategyBook(grade_dir())

#: The chart's CALL2H / CALL4H arrows on the row (momx/chart_signals.py),
#: computed off the build path for graded / scan-matched rows only.
_CHART_SIGNALS = chart_signals.ChartSignalBook(grade_dir())

#: The AI news reader (momx/news_catalyst.py): WHY an A+/A ticker moves, as
#: row["catalyst"]. Claude first, free Gemini when the credit runs out.
_NEWS_CATALYST = news_catalyst.CatalystBook(grade_dir())

#: Sector rotation (momx/sectors.py): 3-day strength vs SPY + 9:45 breadth,
#: stamped as row["sectorRotation"] for the sector strip and the 🔥 marker.
_SECTORS = sectors.RotationBook(directory=grade_dir())

#: SOLO big-money single stocks (momx/solo.py), judged AFTER the sector
#: stamp because "its sector is not moving" reads row["sectorRotation"].
_SOLO = solo.SoloBook(grade_dir())


def _push_market_turn(board_name: str, turn: dict) -> None:
    """Phone push for a fresh market turn - Watchlist board only (one buzz,
    not one per list), to accounts that opted in (marketTurnPush) with a
    topic, inside their push window."""
    if board_name != "Watchlist":
        return
    title, body = market_turn.push_message(board_name, turn)
    for config in momo_alert.all_user_configs().values():
        topic = str(config.get("ntfyTopic") or "").strip()
        if topic and config.get("marketTurnPush") is True and momo_alert.push_window_allows(config):
            try:
                momo_alert._default_poster(topic, title, body, "chart_with_upwards_trend")
            except Exception:  # noqa: BLE001 - best effort
                pass


#: MARKET TURN (momx/market_turn.py): SPY back above VWAP after the morning
#: dip -> the 5 strongest names with 30m buyers in control, latched per day.
_MARKET_TURN = market_turn.MarketTurnBook(grade_dir(), notify=_push_market_turn)

#: MOMOX A+ (momx/momox_aplus.py): catalyst + RVOL cyan + squeeze fire +
#: Skittles D..M bullish, latched per symbol per day.
_MOMOX_APLUS = momox_aplus.MomoxAPlusBook(grade_dir())

#: The BEAR books (spec 2026-09-24): own instances on the bear root, applied
#: to the bear board only. No catalyst / sector / SOLO twins: those are
#: bull-side ideas and their keys are dropped from the bear rows.
_BEAR_GRADE_LOG = grade_log.GradeLog(grade_dir("bear"), direction="bear")
_BEAR_STRATEGY = strategy.StrategyBook(grade_dir("bear"), direction="bear")
_BEAR_CHART_SIGNALS = chart_signals.ChartSignalBook(grade_dir("bear"), direction="bear")


def _fetch_5m_bars(symbols: list[str], days: int = 1) -> dict[str, list[dict]]:
    """5-minute bars reaching back ``days`` calendar days, for the grade
    outcomes. NEVER raises: ``{}`` on any failure (nightly() retries later).

    ``use_cache=False`` is load-bearing: the feed's TTL cache and incremental
    store are keyed by kind ("5m") WITHOUT depth, and the board reads the same
    kind at days=5. A cached fetch here would hand the scanner (or store for
    it) a tape only ``days`` deep. The bypass skips both read and write.

    ``schwab_volume=False`` is also load-bearing: this call only reads OHLC
    for ``outcome_for`` and never reads volume, but the catch-up in
    ``GradeLog.nightly`` retries every unscored symbol every 15 minutes, all
    day, for up to 10 days. Without this the Schwab volume-correction pass
    inside ``feed._fetch_tape`` would turn that into a sustained burst of
    uncached, one-call-per-symbol Schwab price-history requests -- this app's
    home IP has been blocked by Schwab's CDN before for far smaller bursts.
    """
    try:
        result = feed.fetch_5m(symbols, days=days, use_cache=False, schwab_volume=False)
        return {sym: board.frame_to_bars(frame) for sym, frame in result.bars.items()}
    except Exception:  # noqa: BLE001 - a feed failure costs a night's scoring, never a scan
        return {}


def _grade_nightly() -> None:
    """Score today's grade events + rebuild the track record once per ET day
    after 16:15. A cheap no-op on every other call (every warm thread calls
    it after each build; GradeLog keeps it to one run). Never raises."""
    try:
        _GRADE_LOG.nightly(datetime.now(ZoneInfo(EASTERN_TZ)), _fetch_5m_bars)
    except Exception:  # noqa: BLE001
        pass
    try:
        _BEAR_GRADE_LOG.nightly(datetime.now(ZoneInfo(EASTERN_TZ)), _fetch_5m_bars)
    except Exception:  # noqa: BLE001
        pass


def _record_history(name: str, payload: Mapping[str, Any], direction: str = "bull") -> None:
    """Archive this build's matched rows. NEVER raises.

    Called right after :func:`_write_disk`, outside the lock, so a slow disk
    cannot block readers. Wrapped exactly like the cache write: a history bug
    -- bad payload, dead disk, a defect in momx.history itself -- must degrade
    to "no history written this cycle", never to a missing board. The ET clock
    is stamped here (not inside history) so the archive module keeps its
    injected-clock testability.
    """
    try:
        history.record_board(
            name,
            payload,
            now_et=datetime.now(ZoneInfo(EASTERN_TZ)),
            directory=history_dir(direction),
        )
    except Exception:  # noqa: BLE001 - a history failure must never cost a scan
        pass


def _delete_disk(name: str) -> None:
    """Drop one list's cached board. NEVER raises.

    Used when the universe is REPLACED: the old cached board is for the old
    ticker list, and serving it under the new one -- which the disk fallback in
    :func:`snapshot` would otherwise do after the in-memory payload is dropped
    -- would be a lie for up to a minute. A missing file is fine.
    """
    for direction in ("bull", "bear"):
        try:
            _cache_file(name, direction).unlink()
        except (OSError, ValueError):
            pass


def _disk_age_seconds(generated_at: Any) -> float | None:
    """Seconds since a payload's ``generatedAt``, or None if it is unparseable."""
    if not isinstance(generated_at, str) or not generated_at.strip():
        return None
    try:
        parsed = datetime.fromisoformat(generated_at.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - parsed).total_seconds()


def _load_disk(name: str, direction: str = "bull") -> dict | None:
    """The last cached board for ``name`` from disk, flagged stale, or None.

    Returns a contract-shaped payload carrying its ORIGINAL ``generatedAt`` and
    ``stale=True`` so the panel shows old tickers with an updating note rather
    than a blank table. A missing, corrupt or non-board file returns None and
    the caller falls through to the warming stub. NEVER raises.
    """
    try:
        raw = json.loads(_cache_file(name, direction).read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(raw, dict) or not isinstance(raw.get("rows"), list):
        return None
    payload = dict(raw)
    if options_only():
        try:
            keep = set(_options_gate(payload.get("universe") or [
                r.get("symbol") for section in ("rows", "rest") for r in (payload.get(section) or [])
                if isinstance(r, dict)], request=False)[0])
            for section in ("rows", "rest"):
                if isinstance(payload.get(section), list):
                    payload[section] = [r for r in payload[section]
                                        if isinstance(r, dict) and r.get("symbol") in keep]
            if isinstance(payload.get("universe"), list):
                payload["universe"] = [s for s in payload["universe"] if s in keep]
                payload["universeCount"] = len(payload["universe"])
        except Exception:  # noqa: BLE001 - a disk board is a fallback, never a crash
            pass
    payload["list"] = name
    payload["direction"] = "bear" if scan.is_bear(direction) else "bull"
    payload["stale"] = True
    payload["fromDisk"] = True
    age = _disk_age_seconds(raw.get("generatedAt"))
    if age is not None and age > STALE_DISK_MAX_AGE_SECONDS:
        # Still served -- old tickers beat no tickers -- but say where it came
        # from. The warmer replaces it within a cycle.
        payload["message"] = (
            f"Showing the last saved {name} board from a previous session "
            "while it refreshes."
        )
    return payload


# ---------------------------------------------------------------------------
# matched-since -- WHEN each symbol entered the matched set
# ---------------------------------------------------------------------------
# The momentum ledger only diffs while a browser tab polls and dies with the
# process, so before this nobody could say when JD/NIO/BILI entered the set.
# The stamp lives at the BUILD layer instead: every completed build stamps
# ``matchedSince`` onto its scanPass rows, and because it is stamped BEFORE
# ``_write_disk`` the stamps ride the existing disk cache and survive a
# restart. ``momx.board`` and its payload contract are untouched -- this is a
# service-layer decoration only.

def _seed_matched_from_disk(name: str, direction: str = "bull") -> dict[str, str]:
    """The persisted matched map, recovered from the disk cache's rows.

    The map is never written as its own file: the stamped rows inside the
    existing ``_write_disk`` payload ARE the persistence, so there is exactly
    one cache file per list and no second file to drift out of sync. A
    missing, corrupt or pre-feature cache (rows without ``matchedSince``)
    seeds an empty map, and the next build stamps its current set with that
    build's time -- honest, one-time. NEVER raises.
    """
    try:
        raw = json.loads(_cache_file(name, direction).read_text(encoding="utf-8"))
        rows = raw.get("rows") if isinstance(raw, dict) else None
    except (OSError, ValueError, TypeError):
        return {}
    seeded: dict[str, str] = {}
    for entry in rows if isinstance(rows, list) else []:
        if not isinstance(entry, dict) or not entry.get("scanPass"):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        since = entry.get("matchedSince")
        if symbol and isinstance(since, str) and since:
            seeded[symbol] = since
    return seeded


def _apply_matched_since(name: str, payload: dict, direction: str = "bull") -> None:
    """Stamp ``matchedSince`` onto ``payload``'s scanPass rows, in place.

    Rules: a symbol ENTERING the matched set is stamped with this build's
    ``generatedAt``; a symbol still matched keeps its ORIGINAL stamp; a symbol
    dropping out is forgotten, so re-entering restamps fresh. Non-pass rows
    never carry the key.

    Called by the warmer thread only, BEFORE the payload is published or
    written to disk, so nothing else can be reading these rows yet. The disk
    seed read happens outside ``_LOCK`` -- a slow disk must not block readers.
    """
    state = _state(name)
    bear = scan.is_bear(direction)
    with _LOCK:
        matched = state.bear_matched if bear else state.matched
    if matched is None:
        matched = _seed_matched_from_disk(name, direction)
    stamp = payload.get("generatedAt") or datetime.now(timezone.utc).isoformat()
    fresh: dict[str, str] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        symbol = str(row.get("symbol") or "").strip().upper()
        if symbol and row.get("scanPass"):
            fresh[symbol] = matched.get(symbol) or stamp
            row["matchedSince"] = fresh[symbol]
        else:
            row.pop("matchedSince", None)
    with _LOCK:
        if bear:
            state.bear_matched = fresh
        else:
            state.matched = fresh


# ---------------------------------------------------------------------------
# the warmer
# ---------------------------------------------------------------------------

def _reuse_kwargs(state) -> dict:
    """Tell build_board which rows may reuse their previous study columns.

    Returns {} - i.e. rebuild everything, the original behaviour - on a
    full-refresh cycle, on the first build, or whenever the previous
    payload cannot supply a usable cache. Every uncertain path costs work
    rather than risking a stale column.
    """
    with _LOCK:
        previous = state.payload
        previous_bear = state.bear
        builds = state.builds
    if not isinstance(previous, Mapping):
        return {}
    if builds % FULL_ROW_REFRESH_EVERY == 0:
        return {}
    cache: dict[str, Any] = {}
    matched: set[str] = set()
    for bucket in (previous.get("rows"), previous.get("rest")):
        if not isinstance(bucket, (list, tuple)):
            continue
        for row in bucket:
            if not isinstance(row, Mapping):
                continue
            symbol = str(row.get("symbol") or "").strip().upper()
            if not symbol:
                continue
            cache[symbol] = row
            if row.get("scanPass"):
                matched.add(symbol)
    # A BEAR match wants fresh columns just like a bull one: his bear board
    # must not show last cycle's studies on the rows it is about. And every
    # reused row must carry the bear board's m5 as ``m5Bear``: the published
    # bull row was strip_bear'd, so without this a reused row lost its bear
    # momentum on 3 of every 4 builds (review 2026-09-25). A COPY of the row
    # gets the key - the published payload is never touched.
    bear_m5: dict[str, Any] = {}
    if isinstance(previous_bear, Mapping):
        for bucket in (previous_bear.get("rows"), previous_bear.get("rest")):
            for row in bucket if isinstance(bucket, (list, tuple)) else []:
                if not isinstance(row, Mapping):
                    continue
                symbol = str(row.get("symbol") or "").strip().upper()
                if not symbol:
                    continue
                bear_m5[symbol] = row.get("m5")
                if row.get("scanPass"):
                    matched.add(symbol)
    for symbol, row in list(cache.items()):
        if "m5Bear" not in row:
            cache[symbol] = {**row, "m5Bear": bear_m5.get(symbol)}
    if not cache:
        return {}
    return {"full_rows_for": matched, "reuse_rows": cache}


#: The LIVE ⚡ seeds per board (momx/live_bolt.py): {board: {symbol: seed}}.
#: Harvested off row["m5"]["liveSeed"] before a board is published (so the seed
#: never reaches a browser or the archive) and served to api_server's
#: per-second live layer by /api/momx-scanner/live-seeds.
_LIVE_SEEDS: dict[str, dict] = {}
_LIVE_SEEDS_LOCK = threading.Lock()


def _harvest_live_seeds(name: str, payload: Any) -> None:
    try:
        rows = [r for section in ("rows", "rest") for r in ((payload or {}).get(section) or []) if isinstance(r, dict)]
        fresh = {}
        rvol4h = {}
        for row in rows:
            m5 = row.get("m5")
            symbol = row.get("symbol")
            if not isinstance(symbol, str):
                continue
            cell = ((row.get("rvol") or {}).get("4h") or {}) if isinstance(row.get("rvol"), dict) else {}
            rvol4h[symbol] = cell.get("value") if isinstance(cell, dict) else None
            if isinstance(m5, dict) and "liveSeed" in m5:
                seed = m5.get("liveSeed")
                row["m5"] = {k: v for k, v in m5.items() if k != "liveSeed"}
                if isinstance(seed, dict):
                    fresh[symbol] = seed
        present = {row.get("symbol") for row in rows}
        with _LIVE_SEEDS_LOCK:
            held = _LIVE_SEEDS.setdefault(name, {})
            held.update(fresh)
            # A name that left the board (options gate, list edit) must leave
            # the per-second live bolt too.
            for symbol in [s for s in held if s not in present]:
                del held[symbol]
            for symbol, seed in held.items():
                seed["rvol4h"] = rvol4h.get(symbol, seed.get("rvol4h"))
    except Exception:  # noqa: BLE001 - the live layer never costs a build
        return


def live_seeds(name: str | None = None) -> dict:
    """{symbol: seed} for one board, or the union of every board."""
    with _LIVE_SEEDS_LOCK:
        if name:
            return {k: dict(v) for k, v in (_LIVE_SEEDS.get(name) or {}).items()}
        out: dict = {}
        for board_seeds in _LIVE_SEEDS.values():
            for k, v in board_seeds.items():
                if k not in out or int(v.get("t") or 0) > int(out[k].get("t") or 0):
                    out[k] = dict(v)
        return out


def _build_once(name: str) -> float:
    """Rebuild ONE named list. Returns how long it took, in seconds."""
    state = _state(name)
    started = time.monotonic()
    try:
        symbols, gate = _options_gate(board.load_universe(name))
        payload = board.cached_board(
            symbols, ttl_seconds=0.0, **_reuse_kwargs(state)
        )
        payload["list"] = name
        if gate is not None:
            payload["optionsGate"] = gate
    except Exception as exc:  # noqa: BLE001 - a warmer must never die
        elapsed = time.monotonic() - started
        with _LOCK:
            state.error = f"{type(exc).__name__}: {exc}"
            state.last_build_seconds = elapsed
        return elapsed
    elapsed = time.monotonic() - started
    _harvest_live_seeds(name, payload)
    # Stamp matched-since BEFORE the payload is published in memory or cached
    # to disk, so every serving path -- fresh, stale-with-error, post-restart
    # disk fallback -- carries the same stamps.
    _apply_matched_since(name, payload)
    # Grade freshness/latch runs BEFORE the payload is published (below) or
    # cached to disk, so every serving path -- the in-memory publish, the
    # disk cache, and History -- always sees rows that already carry
    # gradeFresh. Running it after publish would let a reader that grabs
    # state.payload under _LOCK observe a board mid-mutation (some rows
    # stamped, some not), and a row dict being written from another thread
    # while iterated can raise "dictionary changed size during iteration".
    # apply() only ADDS a key per row and never raises.
    stamped_at = datetime.now(ZoneInfo(EASTERN_TZ))
    _GRADE_LOG.apply(name, payload, stamped_at)
    # Strategy verdicts ride the same pre-publish slot, for the same reason:
    # every serving path sees rows that already carry row["strategy"].
    _STRATEGY.apply(name, payload, stamped_at)
    _CHART_SIGNALS.apply(name, payload, stamped_at)
    _NEWS_CATALYST.apply(name, payload, stamped_at)
    _SECTORS.apply(name, payload, stamped_at)
    _SOLO.apply(name, payload, stamped_at)
    _MARKET_TURN.apply(name, payload, stamped_at)
    _MOMOX_APLUS.apply(name, payload, stamped_at)
    # The BEAR board (spec 2026-09-24): the same rows with the bear verdicts
    # promoted, its own books, its own matched-since, published together with
    # the bull board so a reader never sees one without the other. The bull
    # rows lose their "bear" block here, BEFORE publish, so the bull wire
    # format and History archive are unchanged.
    bear = board.bear_view(payload)
    board.strip_bear(payload)
    bear["list"] = name
    _apply_matched_since(name, bear, "bear")
    _BEAR_GRADE_LOG.apply(name, bear, stamped_at)
    _BEAR_STRATEGY.apply(name, bear, stamped_at)
    _BEAR_CHART_SIGNALS.apply(name, bear, stamped_at)
    with _LOCK:
        state.payload = payload
        state.bear = bear
        state.error = ""
        state.builds += 1
        state.last_build_seconds = elapsed
    # Cache the completed board to disk OUTSIDE the lock so a slow disk cannot
    # block a reader, and so a restart serves this board instantly instead of
    # "UPDATED never". Failures degrade to in-memory-only inside _write_disk.
    _write_disk(name, payload)
    _write_disk(name, bear, "bear")
    # The 30-day scan-match archive rides the same completed build. Wrapped
    # (never raises) so history can never cost a scan.
    _record_history(name, payload)
    _record_history(name, bear, "bear")
    return elapsed


def _next_delay(elapsed: float, failed: bool) -> float:
    """How long to idle before rebuilding THAT list again.

    REFRESH_SECONDS is a FLOOR, not a fixed period. Mag7 builds in ~4-10s so
    it re-runs every 15s, but the 357-symbol Watchlist takes ~31-39s, and a
    fixed 15s timer at that size means the warmer starts its next build before
    the last one lands -- a permanent rebuild loop burning CPU on a machine
    whose chart engine has been starved this way before. Idling at least as
    long as the build took caps the warmer at ~50% duty cycle whatever the
    universe size, so the Watchlist settles at ~every-build-time (~35s).

    The delay is per-list, so the slow Watchlist cannot drag Mag7's 15s
    refresh out to its own longer period.
    """
    if failed:
        return RETRY_SECONDS
    # CAP the idle. "Idle at least as long as the build took" keeps the warmer
    # at <=50% duty for NORMAL builds, but it turns one pathological build into
    # a blind board: measured live 2026-08-31 11:05 ET, a Watchlist build took
    # 1312s (normally ~35s) and the rule then scheduled a 1312s idle, so the
    # board would have shown 10:30 data until ~11:39 - the trader saw "DATA AS
    # OF 10:30" at 11:00. Builds never overlap - once because the warmer was
    # sequential, now because of the per-list build claim and the heavy gate -
    # so the delay is politeness, not safety: bounding it bounds staleness
    # while a slow build still gets a breather.
    #
    # It idled for the FULL build duration until 2026-09-01, which put the
    # 357-name Watchlist on a ~212s cadence (106s build + 106s idle) and made
    # every alert from that list up to three and a half minutes late. Now that
    # overlap is structurally impossible, a full-duration idle buys nothing, so
    # it is scaled down. NOT to zero: duty cycle is still real and CPU here has
    # starved the chart engine before, so this is a bounded step from ~50% to
    # ~67% busy rather than a removal.
    return min(max(REFRESH_SECONDS, elapsed * IDLE_FRACTION_OF_BUILD), MAX_IDLE_SECONDS)


def _sleep_span(names: list[str]) -> float:
    """Sleep exactly until the earliest due time, clamped to sane bounds."""
    now = time.monotonic()
    with _LOCK:
        due = [
            _STATES[name].due_at for name in names if name in _STATES
        ]
    span = min((moment - now for moment in due), default=MIN_SLEEP_SECONDS)
    return max(MIN_SLEEP_SECONDS, min(span, MAX_SLEEP_SECONDS))


def _is_heavy(name: str) -> bool:
    """Is this list big enough that two of them at once would hurt?

    Reads the universe rather than trusting the name, so a Mag7 someone pastes
    300 tickers into is correctly treated as heavy. Never raises: an unreadable
    universe is assumed heavy, which is the cautious direction -- it queues
    behind the gate instead of running free alongside another big pull.
    """
    try:
        return len(board.load_universe(name)) >= HEAVY_LIST_MIN_SYMBOLS
    except Exception:  # noqa: BLE001 - a broken universe must not stop the warmer
        return True


def _build_claimed(name: str) -> bool:
    """Build ``name`` if no one else is, honouring the heavy gate.

    Returns True if THIS caller built it. The claim and the gate are both
    taken OUTSIDE any build work, and _LOCK is never held across the build --
    holding it there would re-serialise every list and silently undo the whole
    point of this change.
    """
    state = _state(name)
    with _LOCK:
        if state.building:
            return False
        state.building = True
    heavy = _is_heavy(name)
    if heavy:
        _HEAVY_GATE.acquire()
    elapsed = 0.0
    try:
        elapsed = _build_once(name)
    finally:
        # Release in the reverse order of acquisition, and ALWAYS: a build
        # that raised must not leave the gate held, or every heavy list stops
        # for the life of the process.
        if heavy:
            _HEAVY_GATE.release()
        with _LOCK:
            state.building = False
            state.due_at = time.monotonic() + _next_delay(elapsed, bool(state.error))
    return True


def _warm_due_lists(names: list[str]) -> list[str]:
    """One pass: build every list that is due, ONE AT A TIME. Returns the built.

    Still sequential, and still the synchronous seam the tests drive. In
    production the per-list threads below are what actually keeps the cadence;
    this remains because a deterministic "build these, now, in order" entry
    point is worth having and the claim in _build_claimed keeps the two from
    ever building the same list at once.
    """
    built: list[str] = []
    for name in names:
        state = _state(name)
        with _LOCK:
            due_at = state.due_at
        if time.monotonic() < due_at:
            continue
        if _build_claimed(name):
            built.append(name)
    return built


def _warm_one(name: str) -> None:
    """One list's own warm loop: sleep until due, build, repeat. Never dies.

    Every exception is swallowed and followed by a short pause. A warm thread
    that dies is invisible -- the board simply stops updating for that list
    while status() still says ``running`` -- so the only acceptable behaviour
    is to keep going.
    """
    state = _state(name)
    while not _STOP.is_set():
        try:
            with _LOCK:
                due_at = state.due_at
            wait = due_at - time.monotonic()
            if wait > 0:
                state.wake.wait(min(wait, MAX_SLEEP_SECONDS))
                state.wake.clear()
                continue
            if not _build_claimed(name):
                # Someone else is on it; wait briefly rather than spin.
                state.wake.wait(MIN_SLEEP_SECONDS)
                state.wake.clear()
            else:
                _grade_nightly()
        except Exception:  # noqa: BLE001 - a warm thread must never die
            _STOP.wait(RETRY_SECONDS)


def _ensure_threads(names: list[str]) -> None:
    """Give every known list a live warm thread. Idempotent and cheap."""
    for name in names:
        _state(name)  # created outside _LOCK before we take it below
        with _LOCK:
            existing = _THREADS.get(name)
            if existing is not None and existing.is_alive():
                continue
            thread = threading.Thread(
                target=_warm_one,
                args=(name,),
                name=f"momx-warm-{name}",
                daemon=True,
            )
            _THREADS[name] = thread
        thread.start()


def _loop() -> None:
    """The supervisor. Builds nothing; only keeps the warm threads staffed."""
    while not _STOP.is_set():
        try:
            _ensure_threads(_known_names())
        except Exception:  # noqa: BLE001 - the supervisor must never die
            pass
        _WAKE.wait(SUPERVISOR_POLL_SECONDS)
        _WAKE.clear()


def start_warmer() -> None:
    """Start the warmer threads if they are not running. Idempotent."""
    global _THREAD
    _STOP.clear()
    # Staff the lists FIRST so the very first snapshot() triggers builds
    # immediately rather than waiting on the supervisor's first tick.
    _ensure_threads(_known_names())
    with _LOCK:
        if _THREAD is not None and _THREAD.is_alive():
            return
        _THREAD = threading.Thread(
            target=_loop, name="momx-board-warmer", daemon=True
        )
        thread = _THREAD
    thread.start()


def _stop_warmer(timeout: float = 5.0) -> None:
    """Stop every warm thread. For tests; production never calls this."""
    _STOP.set()
    _WAKE.set()
    with _LOCK:
        states = list(_STATES.values())
        threads = list(_THREADS.values())
        supervisor = _THREAD
        _THREADS.clear()
    for state in states:
        state.wake.set()
    for thread in threads + ([supervisor] if supervisor else []):
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)


def refresh_now(list_name: Any = None) -> None:
    """Ask the warmer to rebuild at once instead of waiting out its timer.

    ``list_name=None`` marks every known list due. Naming one marks only that
    one, so pasting a new Mag7 list does not also restart a Watchlist build
    that is minutes from finishing.
    """
    names = _known_names() if list_name is None else [str(list_name)]
    with _LOCK:
        woken = []
        for name in names:
            state = _STATES.get(name)
            if state is None:
                state = _STATES[name] = _ListState()
            state.due_at = 0.0
            woken.append(state)
    # Wake the warm thread of each named list. due_at=0.0 above is the durable
    # signal -- the event only shortens the sleep -- so a set that races with a
    # clear costs at most one sleep span, never a missed rebuild.
    for state in woken:
        state.wake.set()
    _WAKE.set()


def request_rebuild(list_name: Any = None) -> dict:
    """Queue an immediate rebuild of ONE list and return WITHOUT waiting.

    The build itself always runs on the warmer thread, never here -- a
    Watchlist build takes ~35s, and a 35s HTTP request would be its own bug.
    This only zeroes the list's due time and sets the warmer's wake event (the
    same mechanism :func:`refresh_now` uses; the loop sleeps on ``_WAKE``), so
    it is idempotent and spam-safe: if a build for the list is already
    running, the zeroed due time simply queues the next one right behind it,
    and calling this twice is the same as calling it once.

    ``list_name`` defaults to the active list; an unknown name raises
    ``board.UnknownListError`` for the HTTP layer to turn into a 400. The
    returned ``generatedAt`` is the CURRENT board's stamp (None while still
    warming), so a client can poll until it changes to detect the new build.
    """
    name = board.resolve_list_name(list_name)
    state = _state(name)
    with _LOCK:
        state.due_at = 0.0
        generated_at = (state.payload or {}).get("generatedAt")
    start_warmer()
    _WAKE.set()
    return {"ok": True, "list": name, "queued": True, "generatedAt": generated_at}


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------

def snapshot(list_name: Any = None, direction: str = "bull") -> dict:
    """The last completed board for one named list, instantly.

    Never builds on the caller. ``list_name`` defaults to the active list; an
    unknown name raises ``board.UnknownListError``, which the HTTP layers turn
    into a 400. ``direction="bear"`` serves the BEAR board of the same build
    (spec 2026-09-24); anything else is bull.
    """
    name = board.resolve_list_name(list_name)
    start_warmer()
    state = _state(name)
    bear = scan.is_bear(direction)
    with _LOCK:
        payload, error = (state.bear if bear else state.payload), state.error
    # Precedence: fresh in-memory build > in-memory (any) > disk (stale) >
    # warming stub. The stub -- an empty table -- is the LAST resort, only when
    # nothing exists anywhere; a stale board always beats a blank one.
    if payload is not None:
        if not error:
            return payload
        # Serve the stale in-memory board WITH the error attached rather than
        # replacing a good board with a failure page: a trader would rather see
        # minute-old numbers labelled stale than an empty screen.
        stale = dict(payload)
        stale["errors"] = {**(payload.get("errors") or {}), "_refresh": error}
        stale["stale"] = True
        return stale
    # Nothing in memory (this is the post-restart cold path). The last board
    # cached on disk is served instantly, flagged stale, so a restart shows the
    # last tickers in milliseconds instead of "UPDATED never" for minutes.
    disk = _load_disk(name, "bear" if bear else "bull")
    if disk is not None:
        return disk
    warming = _warming_payload(
        name, f"First {name} board build failed: {error}" if error else ""
    )
    if bear:
        warming["direction"] = "bear"
    return warming


def lists() -> dict:
    """Every named list, for the panel's list picker. One file read."""
    try:
        universes = board.load_universes()
    except Exception:  # noqa: BLE001
        universes = {}
    try:
        active = board.active_list()
    except Exception:  # noqa: BLE001
        active = board.DEFAULT_LIST_NAME
    entries = []
    for name, symbols in universes.items():
        state = _state(name)
        with _LOCK:
            payload = state.payload
        entries.append(
            {
                "name": name,
                "count": len(symbols),
                "builtAt": (payload or {}).get("generatedAt"),
                "warming": payload is None,
                # The panel must not offer Delete on a built-in list or
                # Restore on a user one -- they are different actions with
                # different failure modes, and only the server knows which is
                # which. Sent per list so the UI never has to guess from the
                # name.
                "builtIn": name in board.LIST_SEEDS,
            }
        )
    return {"lists": entries, "active": active, "maxLists": MAX_LISTS}


def status() -> dict:
    """Warmer health, for debugging without reading the boards themselves."""
    try:
        active = board.active_list()
    except Exception:  # noqa: BLE001
        active = board.DEFAULT_LIST_NAME
    # Read OUTSIDE _LOCK: the grade log has its own lock, held during an
    # apply, and a status poll must never make board readers wait on it.
    grade_status = _GRADE_LOG.status()
    bear_grade_status = _BEAR_GRADE_LOG.status()
    with _LOCK:
        running = bool(_THREAD is not None and _THREAD.is_alive())
        detail = []
        for name in _known_names_locked():
            state = _STATES.get(name)
            if state is None:
                detail.append(
                    {
                        "name": name,
                        "builds": 0,
                        "hasPayload": False,
                        "error": "",
                        "lastBuildSeconds": 0.0,
                        "nextDelaySeconds": 0.0,
                        "dueInSeconds": 0.0,
                    }
                )
                continue
            detail.append(
                {
                    "name": name,
                    "builds": state.builds,
                    "hasPayload": state.payload is not None,
                    "error": state.error,
                    "lastBuildSeconds": round(state.last_build_seconds, 1),
                    "nextDelaySeconds": round(
                        _next_delay(state.last_build_seconds, bool(state.error)), 1
                    ),
                    "dueInSeconds": round(
                        max(0.0, state.due_at - time.monotonic()), 1
                    ),
                }
            )
        return {
            "running": running,
            "active": active,
            "refreshSeconds": REFRESH_SECONDS,
            # Aggregates kept so the old single-board status keys still mean
            # something to anything already reading them.
            "builds": sum(item["builds"] for item in detail),
            "hasPayload": any(item["hasPayload"] for item in detail),
            "error": next((item["error"] for item in detail if item["error"]), ""),
            "lists": detail,
            # Proof the grade recorder is really writing (spec: checked live
            # during market hours before the build is called done).
            "grade": grade_status,
            # The bear recorder, same shape (spec 2026-09-24).
            "gradeBear": bear_grade_status,
        }


def _known_names_locked() -> list[str]:
    """``_known_names`` for a caller already holding ``_LOCK``.

    ``board.list_names()`` reads a file and touches none of this module's
    state, so calling it under the lock is safe; it is factored out only to
    make that reasoning explicit at the call site.
    """
    names = list(_STATES.keys())
    try:
        for name in board.list_names():
            if name not in names:
                names.append(name)
    except Exception:  # noqa: BLE001
        pass
    return names or [board.DEFAULT_LIST_NAME]


# ---------------------------------------------------------------------------
# writers
# ---------------------------------------------------------------------------

def set_universe(text: Any, list_name: Any = None) -> dict:
    """Persist a pasted ticker list into ONE named list, then rebuild it.

    Returns immediately with the accepted symbol list. The board itself
    refreshes a moment later; the panel refetches to pick it up. The list that
    was written also becomes the ACTIVE one, because the trader pasted it into
    the tab he is looking at.
    """
    name = board.resolve_list_name(list_name)
    symbols = board.parse_universe(text)
    if not symbols:
        return {
            "ok": False,
            "error": "No usable tickers found in that text.",
            "list": name,
            "universe": list(board.load_universe(name)),
        }
    board.save_universe(symbols, name)
    board.set_active_list(name)
    board.clear_board_cache()
    state = _state(name)
    with _LOCK:
        # Drop the old board for THIS list only: it is for a different
        # universe, and showing it under the new ticker list would be a lie
        # for up to a minute. The other lists are untouched and still valid.
        state.payload = None
        state.bear = None
        state.error = ""
        # The matched-since map is for the OLD universe's symbols; keeping it
        # would carry stale stamps onto a new list. None (not {}) so the next
        # build re-seeds from disk -- and the disk cache is deleted below, so
        # the new universe stamps fresh.
        state.matched = None
        state.bear_matched = None
    # Drop the DISK cache too, for the same reason: otherwise snapshot's
    # restart fallback would serve the old universe's board under the new list.
    _delete_disk(name)
    start_warmer()
    refresh_now(name)
    return {
        "ok": True,
        "list": name,
        "universe": symbols,
        "universeCount": len(symbols),
        **typed_options_note(symbols),
    }


#: Ceiling on named lists. Every list gets its own warm thread, and any list
#: of 50+ symbols queues behind a single heavy semaphore, so lists do not scan
#: in parallel -- they take turns. Six is generous; past it each list refreshes
#: noticeably less often.
MAX_LISTS = 6

#: Long enough for "These 7 Energy Stocks", short enough to fit the picker.
MAX_LIST_NAME_LENGTH = 32


def reset_universe(list_name: Any = None) -> dict:
    """Undo a paste: put ONE list back to its default ticker set.

    Same teardown as :func:`set_universe` -- the in-memory board, the
    matched-since map and the disk cache all describe the WRONG universe the
    moment the list changes, and leaving any of them would serve the one-symbol
    board under a restored 357-symbol list. Only this list is touched.
    """
    name = board.resolve_list_name(list_name)
    symbols = board.reset_universe(name)
    board.set_active_list(name)
    board.clear_board_cache()
    state = _state(name)
    with _LOCK:
        state.payload = None
        state.bear = None
        state.error = ""
        state.matched = None
        state.bear_matched = None
    _delete_disk(name)
    start_warmer()
    refresh_now(name)
    return {
        "ok": True,
        "list": name,
        "universe": symbols,
        "universeCount": len(symbols),
        "restored": True,
    }


def _invalidate(name: str) -> None:
    """Throw away everything describing the OLD contents of one list.

    Every writer below changes which symbols a list holds, and the moment that
    happens the in-memory board, the matched-since stamps and the disk cache
    all describe a universe that no longer exists. Serving any of them would
    show the old board under the new list -- which is how a paste appeared to
    do nothing for a minute. Factored out of set_universe/reset_universe so a
    new writer cannot forget one of the three.
    """
    board.clear_board_cache()
    state = _state(name)
    with _LOCK:
        state.payload = None
        state.bear = None
        state.error = ""
        state.matched = None
        state.bear_matched = None
    _delete_disk(name)
    start_warmer()
    refresh_now(name)


def add_universe(text: Any, list_name: Any = None) -> dict:
    """APPEND tickers to one list. The safe counterpart to :func:`set_universe`.

    Reports what actually changed -- added, already there, before/after counts
    -- because "saved" told the trader nothing on the day a paste replaced 357
    tickers with one.
    """
    name = board.resolve_list_name(list_name)
    result = board.add_symbols(text, name)
    if not result["added"]:
        # Nothing changed, so nothing is stale: do NOT invalidate the board or
        # he watches a good board rebuild for 35s to learn that he re-typed a
        # ticker he already had.
        return {
            "ok": True,
            "list": name,
            "added": [],
            "already": result["already"],
            "universeCount": result["after"],
            "materialised": False,
            "noop": True,
            **typed_options_note(result["already"]),
        }
    board.set_active_list(name)
    _invalidate(name)
    return {
        "ok": True,
        "list": name,
        "added": result["added"],
        "already": result["already"],
        "universeBefore": result["before"],
        "universeCount": result["after"],
        "materialised": result["materialised"],
        "noop": False,
        **typed_options_note(result["added"]),
    }


def remove_universe(text: Any, list_name: Any = None) -> dict:
    """Drop tickers from one list. Mirror of :func:`add_universe`."""
    name = board.resolve_list_name(list_name)
    result = board.remove_symbols(text, name)
    if not result["removed"]:
        return {
            "ok": True,
            "list": name,
            "removed": [],
            "missing": result["missing"],
            "universeCount": result["after"],
            "materialised": False,
            "noop": True,
        }
    board.set_active_list(name)
    _invalidate(name)
    return {
        "ok": True,
        "list": name,
        "removed": result["removed"],
        "missing": result["missing"],
        "universeBefore": result["before"],
        "universeCount": result["after"],
        "materialised": result["materialised"],
        "noop": False,
    }


def create_list(list_name: Any, text: Any = None) -> dict:
    """Add a brand-new named list. This is what "+ New list" calls.

    Creating is the one write that must NOT go through
    :func:`board.resolve_list_name` first -- that is precisely what has been
    blocking creation: an unknown name raises before ``save_universe`` is ever
    reached, so the ``create=True`` branch has been unreachable over HTTP.
    """
    name = str(list_name or "").strip()
    if not name:
        return {"ok": False, "error": "Give the list a name."}
    if len(name) > MAX_LIST_NAME_LENGTH:
        return {
            "ok": False,
            "error": f"That name is too long (keep it under {MAX_LIST_NAME_LENGTH} characters).",
        }
    existing = board.list_names()
    if any(name.lower() == known.lower() for known in existing):
        return {"ok": False, "error": f"You already have a list called {name}."}
    if len(existing) >= MAX_LISTS:
        # Not arbitrary: every list gets its own warm thread, and any list of
        # 50+ symbols queues behind ONE heavy semaphore, so lists do not scan
        # in parallel -- they take turns. Past this, adding a list makes every
        # other list update less often.
        return {
            "ok": False,
            "error": (
                f"You already have {len(existing)} lists, which is the limit. "
                f"Each list makes the others refresh more slowly. Delete one first."
            ),
        }
    symbols = board.parse_universe(text) if text else []
    board.save_universe(symbols, name, create=True)
    board.set_active_list(name)
    _invalidate(name)
    return {
        "ok": True,
        "list": name,
        "universe": symbols,
        "universeCount": len(symbols),
        "created": True,
    }


def delete_list(list_name: Any) -> dict:
    """Remove a user-created list, and every piece of state keyed by its name.

    ``board.delete_list`` refuses the built-in lists and moves ``active`` off
    the deleted name. The rest here is the state the board module cannot see:
    the disk cache, and this module's per-list warm state. Without the
    ``_STATES`` pop the warmer keeps a thread rebuilding a universe that no
    longer exists, forever, on a machine whose CPU is already the constraint.
    """
    result = board.delete_list(list_name)
    name = result["deleted"]
    board.clear_board_cache()
    _delete_disk(name)
    with _LOCK:
        _STATES.pop(name, None)
    return {"ok": True, **result}


def rename_list(list_name: Any, new_name: Any) -> dict:
    """Rename a user-created list, keeping its symbols.

    One document write, in ``board.rename_list``. An earlier version did
    save-then-delete-then-set-active and a Windows ``os.replace`` failure
    between those steps left BOTH names on disk.
    """
    fresh = str(new_name or "").strip()
    if len(fresh) > MAX_LIST_NAME_LENGTH:
        return {
            "ok": False,
            "error": f"That name is too long (keep it under {MAX_LIST_NAME_LENGTH} characters).",
        }
    result = board.rename_list(list_name, fresh)
    old = result["renamedFrom"]
    board.clear_board_cache()
    # The old name's cached board and warm state describe a list that no longer
    # exists under that name; the new name starts clean and rebuilds.
    _delete_disk(old)
    with _LOCK:
        _STATES.pop(old, None)
    _invalidate(result["list"])
    return {
        "ok": True,
        "list": result["list"],
        "renamedFrom": old,
        "universeCount": len(result["universe"]),
    }


def set_active(list_name: Any) -> dict:
    """Persist which list a caller gets when it names none."""
    name = board.set_active_list(list_name)
    return {"ok": True, "active": name}


__all__ = [
    "BOARD_CACHE_DIRNAME",
    "BOARD_CACHE_DIR_ENV",
    "MAX_SLEEP_SECONDS",
    "MIN_SLEEP_SECONDS",
    "REFRESH_SECONDS",
    "RETRY_SECONDS",
    "STALE_DISK_MAX_AGE_SECONDS",
    "MAX_LISTS",
    "add_universe",
    "create_list",
    "delete_list",
    "lists",
    "refresh_now",
    "remove_universe",
    "rename_list",
    "request_rebuild",
    "reset_universe",
    "set_active",
    "set_universe",
    "snapshot",
    "start_warmer",
    "status",
]
