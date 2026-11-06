"""The chart's CALL2H / CALL4H arrows, on the scanner row.

Ganesh 2026-09-24: "if I see the signal in chart same way I should see in
scanner". FSLY 2026-09-23 fired CALL2H + CALL4H at 13:00 ET on his chart and
ran +25%, while the scanner's columns only lit up ~50 minutes later.

ONE calculator: this calls scanner._tos_mtf_ema_signal_payload - the exact
function the chart endpoint uses (api_server.chart_payload) - so the two can
never disagree about the study. What differs is only the tape: the chart
reads Schwab (+Tradier premarket), this reads the worker's Alpaca tapes
(SIP 04-20 + BOATS overnight, the same the board uses). Checked before
shipping on 2026-09-23: FSLY 13:00 CALL2H(4x8) CALL2H(9x20) CALL4H(4x8) and
META 09:00 CALL2H(4x8) identical on both.

The study needs ~10+ sessions for its EMAs to settle (scanner.py notes MSFT
missed a CALL4H on five sessions), the board's 5m store holds ~5 days, so a
20-day tape is fetched ONCE per symbol per day and today's bars come from the
board's own cached store on every pass.

Cost: ~60-100 ms per symbol. Only candidates are computed (graded A+/A/B or
scan matches, capped), each at most every RECOMPUTE_SECONDS, in one
background thread - never on the build path.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo
from momx import etf_list

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_chart_signals"
TIMEFRAMES = ("2H", "4H")
RECOMPUTE_SECONDS = 60
MAX_CANDIDATES = 120
#: SLOW LANE (2026-09-29, CHPT: the chart showed C2H at 09:45 and C4H at 10:30,
#: but CHPT had no grade until 13:05 news, so its arrows were never computed
#: and the scanner first "saw" them at 13:05). Every other non-ETF row on the
#: board is now computed too, every SLOW_RECOMPUTE_SECONDS, at most
#: MAX_SLOW_PER_PASS a pass, after the graded / scan-match rows.
SLOW_RECOMPUTE_SECONDS = 300
MAX_SLOW_PER_PASS = 40
DEEP_DAYS = 20
IDLE_SECONDS = 5
#: The chart's squeeze flames (shared_Cloud_Signal_Squeeze, App.jsx
#: calculateMtfSqueezeReleaseClouds) the Setup column shows (2026-10-11, his
#: "if you see SQZ in chart I want in setup column"). 15m / 30m / current are
#: left out (they fire all day); DAY reads the real daily bars like the chart.
SQUEEZE_TIMEFRAMES = ((60, "1h"), (120, "2h"), (240, "4h"), (1440, "D"))


#: Shared between the bull and bear books (2026-09-29, Bear-scanner review of
#: the slow lane): the study payload carries BOTH directions' arrows - only
#: today_signals() filters - and the 20-day tape is the same, so each symbol
#: is fetched deep and computed ONCE per new 5m bar for both books.
_SHARED_LOCK = threading.Lock()
_SHARED_DEEP: dict = {}        # (fetch fn, day, symbol) -> frame before today
_SHARED_PAYLOAD: dict = {}     # symbol -> {owner book, compute, tape mark, payload}


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def is_candidate(row: Any) -> bool:
    """A+/A/B graded or a scan match - the rows he actually looks at."""
    return isinstance(row, Mapping) and (
        _m(row.get("grade")).get("letter") in ("A+", "A", "B") or bool(row.get("scanPass")))


def today_signals(payload: Any, day: str, direction: str = "bull") -> list[dict]:
    """Today's 2H/4H arrows of one direction from a chart signal payload
    (labels as the chart prints them: CALL2H when the higher timeframe
    confirms, the compact C2H when it does not). ``direction="bear"`` keeps
    the PUT2H / PUT4H arrows instead (spec 2026-09-24)."""
    bear = str(direction or "").strip().lower() == "bear"
    want, prefix = ("PUT", "PUT") if bear else ("CALL", "CALL")
    out = []
    for s in _m(payload).get("signals") or []:
        if not isinstance(s, Mapping) or s.get("direction") != want or s.get("timeframe") not in TIMEFRAMES:
            continue
        t = s.get("time")
        if not isinstance(t, (int, float)):
            continue
        at = datetime.fromtimestamp(t, ET)
        if at.date().isoformat() != day:
            continue
        # The confirmed CALL2H / CALL4H (PUT2H / PUT4H) any time of day. The
        # compact C2H / C4H (P2H / P4H: a cross AGAINST the higher timeframe's
        # trend) only in regular hours: premarket they fired on HAL/OXY/CRM/USO
        # at 05:00-07:00 on 2026-09-23 and buried the real ones, but PYPL's
        # C4H at 10:10 on 2026-09-25 led a +3.3% run (his ask to show them).
        label = str(s.get("label") or "")
        compact = not label.startswith(prefix)
        if compact and not ("09:30" <= at.strftime("%H:%M") < "16:00"):
            continue
        out.append({"label": s.get("label"), "family": s.get("family"), "timeframe": s.get("timeframe"),
                    "time": int(t), "at": at.replace(microsecond=0).isoformat(), "compact": compact})
    out.sort(key=lambda s: (s["time"], s["family"] or "", s["timeframe"]))
    return out


def today_calls(payload: Any, day: str) -> list[dict]:
    """The bull reading of :func:`today_signals` (kept for its callers)."""
    return today_signals(payload, day, "bull")


def _frame(bars: Any):
    """A pandas frame (timestamp, close) from a feed DataFrame or a bar list."""
    import pandas as pd
    if bars is None:
        return pd.DataFrame(columns=["timestamp", "close"])
    if isinstance(bars, pd.DataFrame):
        d = bars.reset_index()
        cols = {str(c).lower(): c for c in d.columns}
        tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
        stamps = d[tcol]
        if pd.api.types.is_numeric_dtype(stamps):
            stamps = pd.to_datetime(stamps, unit="s", utc=True)
        return pd.DataFrame({"timestamp": pd.to_datetime(stamps, utc=True), "close": d[cols["close"]].astype(float)})
    rows = [b for b in bars if isinstance(b, Mapping)]
    return pd.DataFrame({"timestamp": pd.to_datetime([b["time"] for b in rows], unit="s", utc=True),
                         "close": [float(b["close"]) for b in rows]})


def _ohlc(bars: Any) -> list[dict]:
    """[{time, open, high, low, close}] (epoch s, ascending) from a feed
    DataFrame or a bar list - what the chart's squeeze calculator reads."""
    try:
        import pandas as pd
        if bars is None:
            return []
        if isinstance(bars, pd.DataFrame):
            d = bars.reset_index()
            cols = {str(c).lower(): c for c in d.columns}
            tcol = cols.get("time") or cols.get("timestamp") or d.columns[0]
            stamps = d[tcol]
            if pd.api.types.is_numeric_dtype(stamps):
                secs = stamps.astype("int64")
            else:
                secs = (pd.to_datetime(stamps, utc=True).astype("int64") // 10**9)
            out = [{"time": int(t), "open": float(o), "high": float(h), "low": float(lo), "close": float(c)}
                   for t, o, h, lo, c in zip(secs, d[cols["open"]], d[cols["high"]], d[cols["low"]], d[cols["close"]])]
        else:
            out = [{"time": int(b["time"]), "open": float(b["open"]), "high": float(b["high"]),
                    "low": float(b["low"]), "close": float(b["close"])} for b in bars if isinstance(b, Mapping)]
        out.sort(key=lambda b: b["time"])
        return out
    except Exception:  # noqa: BLE001
        return []


def _hourly(bars: list[dict]) -> list[dict]:
    """5m bars -> chart 60m buckets. The 1h/2h/4h buckets all start on the
    hour, so building 2h / 4h from these equals building them from 5m."""
    from premarket_scanner import aggregate_chart_bars
    return aggregate_chart_bars(bars, 60)


def today_squeezes(hourly: list[dict], daily: list[dict], day: str, direction: str = "bull") -> list[dict]:
    """Today's chart squeeze flames of one tone (bull rows: bull flames), each
    on its (possibly still forming) bucket, like the chart draws them."""
    from premarket_scanner import squeeze_release_events
    want = "bear" if str(direction or "").lower() == "bear" else "bull"
    out = []
    for minutes, name in SQUEEZE_TIMEFRAMES:
        try:
            if minutes == 1440:
                if not daily:
                    continue
                events = squeeze_release_events(hourly or daily, 1440, include_forming=True, timeframe_bars=daily)
            else:
                events = squeeze_release_events(hourly, minutes, include_forming=True)
        except Exception:  # noqa: BLE001
            continue
        for e in events:
            at = datetime.fromtimestamp(int(e["bucketTime"]), ET)
            if e.get("tone") != want or at.date().isoformat() != day:
                continue
            out.append({"timeframe": name, "time": int(e["bucketTime"]), "at": at.replace(microsecond=0).isoformat()})
    out.sort(key=lambda s: (s["time"], s["timeframe"]))
    return out


#: Today's bars come from the board's last build when it is this fresh
#: (board.recent_tapes); only symbols no build has fetched lately are
#: downloaded here. The study reads open/high/low/close only, so the board's
#: Schwab-corrected volume makes no difference to an arrow.
RECENT_TAPE_MAX_AGE_SECONDS = 180.0


def _default_fetch(symbols: list[str], deep: bool) -> tuple[dict, dict]:
    """(5m bars, daily bars) per symbol from the worker's Alpaca feed."""
    from momx import board, feed
    if deep:
        five = feed.fetch_5m(symbols, days=DEEP_DAYS, schwab_volume=False, use_cache=False)
        daily = feed.fetch_daily(symbols)
        return dict(five.bars), dict(daily.bars)
    five_bars, daily_bars, missing = board.recent_tapes(symbols, RECENT_TAPE_MAX_AGE_SECONDS)
    if missing:
        five = feed.fetch_5m(missing, schwab_volume=False)
        daily = feed.fetch_daily(missing)
        five_bars.update(five.bars)
        daily_bars.update(daily.bars)
    return five_bars, daily_bars


def _default_compute(frame, daily_frame) -> dict:
    from scanner import _tos_mtf_ema_signal_payload
    return _tos_mtf_ema_signal_payload(frame, daily_frame=daily_frame)


#: OFF THE WORKER'S MAIN PROCESS (2026-10-16, "every list under 1 min").
#: py-spy --gil on momx_worker during market hours: these two books' study
#: runs (the EMA study, the frame building and the squeeze math) held the
#: main process's GIL 61% of the time, and every Watchlist build - its tape
#: fetch, its lane hand-off and its bookkeeping - queued behind them. The
#: per-symbol work now runs in ONE child process both books share (so the
#: bull/bear "one study per bar" reuse still holds - it lives in the child).
#: The 20-day frame of a symbol crosses the process boundary once a day.
#: MOMX_CHART_SIGNALS_OFFLOAD=0 runs it in-process as before.
OFFLOAD = os.environ.get("MOMX_CHART_SIGNALS_OFFLOAD", "1").strip() != "0"
OFFLOAD_TIMEOUT_SECONDS = 300
_EXEC = None
_EXEC_LOCK = threading.Lock()
_EXEC_HAS_DEEP: set = set()     # (day, symbol) the child already holds
_CHILD_DEEP: dict = {}          # in the child: (day, symbol) -> (20-day frame, 60m buckets)


def _symbol_pass(s: str, deep: Any, deep_hourly: Any, recent_bars: Any, daily_bars: Any, day: str,
                 direction: str, compute: Callable, owner: Any, cache: dict) -> tuple[Any, Any]:
    """(today's arrows or None, today's squeeze flames or None) for one symbol.

    (None, None) when it has no bars or its study raised - one bad symbol
    costs only itself. Pure apart from ``cache`` (symbol -> the last study
    run), which lets the OTHER book reuse a study on the same tape.
    """
    import pandas as pd
    try:
        parts = [deep, _frame(recent_bars)]
        parts = [p for p in parts if p is not None and not p.empty]
        if not parts:
            return None, None
        frame = pd.concat(parts).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
        # Same tape = same last bar time, bar count AND last close (a
        # developing bar keeps its time while its close moves).
        mark = (frame["timestamp"].iloc[-1], len(frame), float(frame["close"].iloc[-1]))
        with _SHARED_LOCK:
            hit = cache.get(s)
        # Reuse ONLY what the OTHER book computed with the same study on
        # the same tape - a book never reuses its own (it recomputes on
        # its own cadence exactly as before).
        if (hit is not None and hit["owner"] != owner and hit["compute"] == compute
                and hit["mark"] == mark):
            payload = hit["payload"]
        else:
            payload = compute(frame, _frame(daily_bars))
            with _SHARED_LOCK:
                cache[s] = {"owner": owner, "compute": compute, "mark": mark, "payload": payload}
        calls = today_signals(payload, day, direction)
    except Exception:  # noqa: BLE001 - one bad symbol costs only itself
        return None, None
    fires = None
    try:
        today_hourly = _hourly(_ohlc(recent_bars))
        start_today = datetime.fromisoformat(day).replace(tzinfo=ET).timestamp()
        hourly = (deep_hourly or []) + [b for b in today_hourly if b["time"] >= start_today]
        fires = today_squeezes(hourly, _ohlc(daily_bars), day, direction)
    except Exception:  # noqa: BLE001
        pass
    return calls, fires


def _child_batch(items: list, day: str, direction: str, owner: int) -> dict:
    """The child process's side: {symbol: (arrows, flames, missing_deep)}."""
    for key in [k for k in _CHILD_DEEP if k[0] != day]:
        _CHILD_DEEP.pop(key, None)
    out = {}
    for s, send, deep, deep_hourly, recent_bars, daily_bars in items:
        key = (day, s)
        if send:
            _CHILD_DEEP[key] = (deep, deep_hourly)
        elif key not in _CHILD_DEEP:
            out[s] = (None, None, True)     # the parent resends it next pass
            continue
        deep, deep_hourly = _CHILD_DEEP[key]
        calls, fires = _symbol_pass(s, deep, deep_hourly, recent_bars, daily_bars, day, direction,
                                    _default_compute, owner, _SHARED_PAYLOAD)
        out[s] = (calls, fires, False)
    return out


def _child_warm() -> bool:
    """Load the study's heavy imports in the child, before any real batch,
    and put its worker thread BELOW the scan lanes (see _yield_to_lanes)."""
    import pandas  # noqa: F401
    import scanner  # noqa: F401
    _yield_to_lanes()
    return True


def _yield_to_lanes() -> bool:
    """THREAD_PRIORITY_LOWEST for the child's worker thread (Windows only).

    Measured 2026-10-16: this child used ~0.8 of a core continuously, at the
    same IDLE class as the board's scan lanes, and each lane got only
    0.45-0.8 of a core during a Watchlist scan. Inside the IDLE class this
    thread now runs at base priority 2 against the lanes' 4, so a lane always
    wins a contested core and the arrows use what the scan leaves. Nothing
    changes against api_server or any other process (they outrank both).
    Every batch runs on this same thread (max_workers=1). Never raises."""
    try:
        import ctypes
        import sys
        if sys.platform != "win32":
            return False
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentThread.restype = ctypes.c_void_p
        kernel32.SetThreadPriority.argtypes = [ctypes.c_void_p, ctypes.c_int]
        kernel32.SetThreadPriority.restype = ctypes.c_int
        THREAD_PRIORITY_LOWEST = -2
        return bool(kernel32.SetThreadPriority(kernel32.GetCurrentThread(), THREAD_PRIORITY_LOWEST))
    except Exception:  # noqa: BLE001
        return False


#: The child is warmed (and remade if it dies) before it is handed real work.
#: NOTE 2026-10-16: spawned children that are NOT the worker's own were dying
#: "at random" (BrokenProcessPool, no traceback) in tests - that was
#: scripts/scanner_watchdog.ps1 reaping every spawn_main process whose parent
#: is not momx_worker, i.e. any pytest child. The worker's own children are
#: never reaped; the retries cover a child that dies on start-up for real.
CHILD_START_ATTEMPTS = 3


_EXEC_FAILED_AT = [float("-inf")]
CHILD_RETRY_SECONDS = 300


def _executor():
    global _EXEC
    with _EXEC_LOCK:
        if _EXEC is False and time.monotonic() - _EXEC_FAILED_AT[0] > CHILD_RETRY_SECONDS:
            _EXEC = None          # no child for a while: try again, never give up for good
        if _EXEC is None:
            _EXEC = False
            _EXEC_FAILED_AT[0] = time.monotonic()
            try:
                from concurrent.futures import ProcessPoolExecutor
                for _ in range(CHILD_START_ATTEMPTS):
                    from momx.worker_priority import idle_calculation_worker
                    ex = ProcessPoolExecutor(max_workers=1, initializer=idle_calculation_worker)
                    try:
                        ex.submit(_child_warm).result(timeout=OFFLOAD_TIMEOUT_SECONDS)
                    except Exception:  # noqa: BLE001 - died on start-up: make another
                        ex.shutdown(wait=False, cancel_futures=True)
                        continue
                    _EXEC = ex
                    break
                _EXEC_HAS_DEEP.clear()
            except Exception:  # noqa: BLE001 - in-process is always available
                _EXEC = False
        return _EXEC or None


def shutdown_executor() -> None:
    """Release the child (worker exit, or after it broke - the next pass makes a new one)."""
    global _EXEC
    with _EXEC_LOCK:
        ex, _EXEC = _EXEC, None
        _EXEC_HAS_DEEP.clear()
    if ex:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001
            pass


def _offloaded(book: "ChartSignalBook", due: list[str], recent: Mapping, daily: Mapping,
               day: str) -> tuple[dict, dict] | None:
    """Run ``due`` in the child. None = the child failed; the caller runs it here."""
    ex = _executor()
    if ex is None:
        return None
    with _EXEC_LOCK:
        held = set(_EXEC_HAS_DEEP)
    items = []
    for s in due:
        send = (day, s) not in held
        items.append((s, send, book._deep.get(s) if send else None, book._deep_hourly.get(s) if send else None,
                      recent.get(s), daily.get(s)))
    try:
        out = ex.submit(_child_batch, items, day, book.direction, id(book)).result(timeout=OFFLOAD_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - broken / hung child: drop it, run here this pass
        shutdown_executor()
        return None
    results: dict = {}
    squeeze_results: dict = {}
    with _EXEC_LOCK:
        same = _EXEC is ex
        for s, send, *_ in items:
            calls, fires, missing = out.get(s, (None, None, False))
            if same and send:
                _EXEC_HAS_DEEP.add((day, s))
            if same and missing:
                _EXEC_HAS_DEEP.discard((day, s))
            if calls is not None:
                results[s] = calls
            if fires is not None:
                squeeze_results[s] = fires
    return results, squeeze_results


class ChartSignalBook:
    """Keeps today's chart arrows per symbol and stamps ``row["chartSignals"]``.

    Each arrow carries ``at`` (the chart candle it is drawn on - what he sees
    on the chart) and ``seenAt`` (when the scanner first saw it). Persisted
    per ET day so a worker restart keeps ``seenAt``. Never raises.
    """

    def __init__(self, directory: Path, fetch: Callable | None = None, compute: Callable | None = None,
                 background: bool = True, direction: str = "bull") -> None:
        self.directory = Path(directory)
        # BEAR book (spec 2026-09-24): PUT2H / PUT4H on the bear rows.
        self.direction = "bear" if str(direction or "").strip().lower() == "bear" else "bull"
        self._fetch = fetch or _default_fetch
        self._compute = compute or _default_compute
        self._background = background
        # Only the real study leaves the process: a test's fake compute cannot
        # be pickled, and the foreground mode is the tests' synchronous path.
        self._offload = OFFLOAD and background and self._compute is _default_compute
        self._lock = threading.Lock()
        self._day = ""
        self._signals: dict[str, list[dict]] = {}      # symbol -> today's arrows
        self._squeezes: dict[str, list[dict]] = {}     # symbol -> today's chart squeeze flames
        self._deep_hourly: dict[str, list] = {}        # symbol -> 60m buckets before today
        self._deep: dict[str, Any] = {}                 # symbol -> frame before today
        self._deep_day = ""
        self._last_run: dict[str, float] = {}
        self._candidates: dict[str, float] = {}         # symbol -> last time it was a candidate
        self._slow: dict[str, float] = {}               # every other non-ETF row (slow lane)
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- public

    def signals_for(self, symbol: str) -> list[dict]:
        """Today's arrows for one symbol (copies) - Jev reads the OTHER side's
        arrows (PUT2H on a call ticker) through this (momx/jev_trade.py)."""
        with self._lock:
            return [dict(c) for c in self._signals.get(str(symbol or "").upper()) or self._signals.get(symbol) or []]

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            with self._lock:
                self._apply(payload, now)
            if not self._background:
                self.run_once(now)
                with self._lock:
                    self._stamp(payload)
            elif self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._loop, name="momx-chart-signals", daemon=True)
                self._thread.start()
        except Exception:  # noqa: BLE001 - a chart-signal bug never costs a scan
            return

    def run_once(self, now: datetime | None = None) -> None:
        """Compute every due candidate once (the background loop's body)."""
        now = now or datetime.now(ET)
        local = now.astimezone(ET)
        day = local.date().isoformat()
        with self._lock:
            self._load(day)
            cutoff = local.timestamp() - 30 * 60
            due = [s for s, seen in sorted(self._candidates.items(), key=lambda kv: -kv[1])
                   if seen >= cutoff and local.timestamp() - self._last_run.get(s, 0) >= RECOMPUTE_SECONDS]
            due = due[:MAX_CANDIDATES]
            slow = [s for s, seen in sorted(self._slow.items(), key=lambda kv: self._last_run.get(kv[0], 0))
                    if seen >= cutoff and s not in self._candidates
                    and local.timestamp() - self._last_run.get(s, 0) >= SLOW_RECOMPUTE_SECONDS]
            due = due + slow[:MAX_SLOW_PER_PASS]
        if not due:
            return
        try:
            if self._deep_day != day:
                self._deep, self._deep_day, self._deep_hourly = {}, day, {}
                with _SHARED_LOCK:
                    for k in [k for k in _SHARED_DEEP if k[1] != day]:
                        _SHARED_DEEP.pop(k, None)
            with _SHARED_LOCK:     # the other book may already hold it
                for s in due:
                    got = _SHARED_DEEP.get((self._fetch, day, s))
                    if s not in self._deep and got is not None:
                        self._deep[s] = got
            need_deep = [s for s in due if s not in self._deep]
            daily = {}
            if need_deep:
                deep_bars, daily = self._fetch(need_deep, True)
                for s in need_deep:
                    start_today = datetime.fromisoformat(day).replace(tzinfo=ET).timestamp()
                    self._deep_hourly[s] = [b for b in _hourly(_ohlc(deep_bars.get(s))) if b["time"] < start_today]
                    frame = _frame(deep_bars.get(s))
                    if not frame.empty:
                        frame = frame[frame["timestamp"].dt.tz_convert(ET).dt.date.astype(str) < day]
                    self._deep[s] = frame
                    with _SHARED_LOCK:
                        _SHARED_DEEP[(self._fetch, day, s)] = frame
            recent, daily2 = self._fetch(due, False)
            daily = {**daily2, **daily}
        except Exception:  # noqa: BLE001 - a failed fetch = try again next pass
            return
        results, squeeze_results = self._study(due, recent, daily, day)
        stamp = local.replace(microsecond=0).isoformat()
        with self._lock:
            if self._day != day:
                return
            changed = False
            for s, calls in results.items():
                self._last_run[s] = local.timestamp()
                held = {(c["label"], c["family"], c["time"]): c for c in self._signals.get(s, [])}
                merged = []
                for c in calls:
                    old = held.get((c["label"], c["family"], c["time"]))
                    merged.append({**c, "seenAt": old["seenAt"] if old else stamp})
                    changed = changed or old is None or bool(old.get("goneAt"))
                # An arrow the chart has since REPAINTED away (a forming 2h/4h
                # bar that crossed back) is KEPT with goneAt (2026-09-28, ABVX:
                # its 09:40 C2H showed 09:45-11:05 then vanished, and with it
                # any trace that it was there to trade). The chart drops it; the
                # scanner says "gone 11:05" instead of forgetting it.
                live = {(c["label"], c["family"], c["time"]) for c in calls}
                for key, old in held.items():
                    if key not in live:
                        merged.append({**old, "goneAt": old.get("goneAt") or stamp})
                        changed = changed or not old.get("goneAt")
                merged.sort(key=lambda c: (c["time"], c.get("family") or "", c.get("timeframe") or ""))
                self._signals[s] = merged
            # Chart squeeze flames: same seenAt / goneAt keeping as the arrows -
            # a flame on a forming bucket can vanish again (TOS repaints too).
            for s, fires in squeeze_results.items():
                held = {(f["timeframe"], f["time"]): f for f in self._squeezes.get(s, [])}
                merged = []
                for f in fires:
                    old = held.get((f["timeframe"], f["time"]))
                    merged.append({**f, "seenAt": old["seenAt"] if old else stamp})
                    changed = changed or old is None or bool(old.get("goneAt"))
                live = {(f["timeframe"], f["time"]) for f in fires}
                for key, old in held.items():
                    if key not in live:
                        merged.append({**old, "goneAt": old.get("goneAt") or stamp})
                        changed = changed or not old.get("goneAt")
                merged.sort(key=lambda f: (f["time"], f["timeframe"]))
                self._squeezes[s] = merged
            if changed:
                self._save()

    # ---------------------------------------------------------------- internal

    def _study(self, due: list[str], recent: Mapping, daily: Mapping, day: str) -> tuple[dict, dict]:
        """({symbol: today's arrows}, {symbol: today's squeeze flames}) for ``due``.

        In the worker this runs in the chart-signal child process (see
        OFFLOAD); tests with a fake compute, or a dead child, run it here.
        """
        if self._offload:
            got = _offloaded(self, due, recent, daily, day)
            if got is not None:
                return got
        results: dict = {}
        squeeze_results: dict = {}
        for s in due:
            calls, fires = _symbol_pass(s, self._deep.get(s), self._deep_hourly.get(s), recent.get(s),
                                        daily.get(s), day, self.direction, self._compute, self, _SHARED_PAYLOAD)
            if calls is not None:
                results[s] = calls
            if fires is not None:
                squeeze_results[s] = fires
        return results, squeeze_results

    def _loop(self) -> None:
        while True:
            try:
                self.run_once()
            except Exception:  # noqa: BLE001
                pass
            time.sleep(IDLE_SECONDS)

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._signals, self._last_run, self._squeezes = day, {}, {}, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("signals"), dict):
                self._signals = {k: v for k, v in doc["signals"].items() if isinstance(v, list)}
            if isinstance(doc, dict) and isinstance(doc.get("squeezes"), dict):
                self._squeezes = {k: v for k, v in doc["squeezes"].items() if isinstance(v, list)}
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, "signals": self._signals, "squeezes": self._squeezes}), encoding="utf-8")
        os.replace(tmp, path)

    def _rows(self, payload: Any) -> list[dict]:
        rows = []
        for section in ("rows", "rest"):
            part = _m(payload).get(section)
            if isinstance(part, list):
                rows.extend(r for r in part if isinstance(r, dict))
        return rows

    def _apply(self, payload: Any, now: datetime) -> None:
        local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
        self._load(local.date().isoformat())
        for row in self._rows(payload):
            symbol = row.get("symbol")
            if isinstance(symbol, str) and symbol and is_candidate(row):
                self._candidates[symbol] = local.timestamp()
                self._slow.pop(symbol, None)
            elif isinstance(symbol, str) and symbol and not etf_list.rule_excluded(row):
                self._slow[symbol] = local.timestamp()
        self._stamp(payload)

    def _stamp(self, payload: Any) -> None:
        for row in self._rows(payload):
            calls = self._signals.get(row.get("symbol")) or []
            row["chartSignals"] = [{k: c.get(k) for k in ("label", "family", "timeframe", "at", "seenAt", "goneAt")
                                    if c.get(k) is not None} for c in calls]
            fires = self._squeezes.get(row.get("symbol")) or []
            row["chartSqueeze"] = [{k: f.get(k) for k in ("timeframe", "at", "seenAt", "goneAt") if f.get(k) is not None}
                                   for f in fires]
