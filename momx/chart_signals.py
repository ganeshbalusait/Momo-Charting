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

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_chart_signals"
TIMEFRAMES = ("2H", "4H")
RECOMPUTE_SECONDS = 60
MAX_CANDIDATES = 120
DEEP_DAYS = 20
IDLE_SECONDS = 5


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


def _default_fetch(symbols: list[str], deep: bool) -> tuple[dict, dict]:
    """(5m bars, daily bars) per symbol from the worker's Alpaca feed."""
    from momx import feed
    if deep:
        five = feed.fetch_5m(symbols, days=DEEP_DAYS, schwab_volume=False, use_cache=False)
    else:
        five = feed.fetch_5m(symbols, schwab_volume=False)          # the board's cached store
    daily = feed.fetch_daily(symbols)
    return dict(five.bars), dict(daily.bars)


def _default_compute(frame, daily_frame) -> dict:
    from scanner import _tos_mtf_ema_signal_payload
    return _tos_mtf_ema_signal_payload(frame, daily_frame=daily_frame)


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
        self._lock = threading.Lock()
        self._day = ""
        self._signals: dict[str, list[dict]] = {}      # symbol -> today's arrows
        self._deep: dict[str, Any] = {}                 # symbol -> frame before today
        self._deep_day = ""
        self._last_run: dict[str, float] = {}
        self._candidates: dict[str, float] = {}         # symbol -> last time it was a candidate
        self._thread: threading.Thread | None = None

    # ---------------------------------------------------------------- public

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
        if not due:
            return
        try:
            if self._deep_day != day:
                self._deep, self._deep_day = {}, day
            need_deep = [s for s in due if s not in self._deep]
            daily = {}
            if need_deep:
                deep_bars, daily = self._fetch(need_deep, True)
                for s in need_deep:
                    frame = _frame(deep_bars.get(s))
                    if not frame.empty:
                        frame = frame[frame["timestamp"].dt.tz_convert(ET).dt.date.astype(str) < day]
                    self._deep[s] = frame
            recent, daily2 = self._fetch(due, False)
            daily = {**daily2, **daily}
        except Exception:  # noqa: BLE001 - a failed fetch = try again next pass
            return
        import pandas as pd
        results = {}
        for s in due:
            try:
                parts = [self._deep.get(s), _frame(recent.get(s))]
                parts = [p for p in parts if p is not None and not p.empty]
                if not parts:
                    continue
                frame = pd.concat(parts).drop_duplicates("timestamp", keep="last").sort_values("timestamp")
                payload = self._compute(frame, _frame(daily.get(s)))
                results[s] = today_signals(payload, day, self.direction)
            except Exception:  # noqa: BLE001 - one bad symbol costs only itself
                continue
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
                    changed = changed or old is None
                # An arrow the chart has since REPAINTED away (a forming 2h/4h
                # bar that crossed back) drops off here, exactly as on the chart.
                if len(merged) != len(held):
                    changed = True
                self._signals[s] = merged
            if changed:
                self._save()

    # ---------------------------------------------------------------- internal

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
        self._day, self._signals, self._last_run = day, {}, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("signals"), dict):
                self._signals = {k: v for k, v in doc["signals"].items() if isinstance(v, list)}
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, "signals": self._signals}), encoding="utf-8")
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
        self._stamp(payload)

    def _stamp(self, payload: Any) -> None:
        for row in self._rows(payload):
            calls = self._signals.get(row.get("symbol")) or []
            row["chartSignals"] = [{k: c.get(k) for k in ("label", "family", "timeframe", "at", "seenAt")}
                                   for c in calls]
