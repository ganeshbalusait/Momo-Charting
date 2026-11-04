"""Integration (2026-11-04 coverage pass, group charts-studies): the chart tape
the trader's charts read, through the real GET /api/oi-finder-chart route over
HTTP and the real serve path behind it (DashboardState._oi_finder_chart_payload_impl).

Only the expensive or outside work is replaced: the cache is seeded instead of
built from Schwab, the background rebuild and the label replay are recorded
instead of run, and the inline broker tail fetch is a fake splice. Everything
the browser depends on - the first-paint slimming, the staleness verdict, the
refresh=true fold, the recorded-minute splice and the gzip size - is the real
code.

Features covered here:
  * charts-squeeze      - the first paint carries 2,400 five-minute bars, enough
                          for a 20-bucket 2h squeeze (the MRNA 09:00 miss).
  * charts-mobile       - the phone's first paint stays small on the wire.
  * charts-tos-study-overlays - every tape the studies read is OHLCV with
                          strictly ascending, unique times, even after the
                          stream minutes are spliced on.
  * charts-tape-freshness - a tape stuck in an old session says so; a current
                          one does not; refresh=true folds the broker tail once.
"""
from __future__ import annotations

import gzip
import json
import math
import tempfile
import threading
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")
DashboardState = api_server.DashboardState
SYMBOL = "EA"  # not one of the scanner symbols the parity overlay rewrites


def _et(year, month, day, hour=15, minute=59) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=ET).timestamp())


def _walk(start_epoch: int, count: int, step_seconds: int, price: float = 100.0, seed: int = 7) -> list[dict]:
    """Deterministic random-walk OHLCV bars (no clock, no market data)."""
    bars = []
    state = seed
    for index in range(count):
        state = (state * 1103515245 + 12345) % (2 ** 31)
        move = ((state % 2001) - 1000) / 10000.0  # -0.10 .. +0.10
        open_ = round(price, 2)
        close = round(max(1.0, price + move), 2)
        high = round(max(open_, close) + 0.03, 2)
        low = round(min(open_, close) - 0.03, 2)
        bars.append({
            "time": start_epoch + index * step_seconds,
            "open": open_, "high": high, "low": low, "close": close,
            "volume": 1000 + state % 50_000,
        })
        price = close
    return bars


def _session_bars(first_session: datetime, sessions: int, step_minutes: int) -> list[dict]:
    """04:00-20:00 ET bars for `sessions` consecutive weekdays from `first_session`."""
    rows = []
    day = first_session
    made = 0
    while made < sessions:
        if day.weekday() < 5:
            start = int(day.replace(hour=4, minute=0).timestamp())
            per_day = (16 * 60) // step_minutes
            rows.extend(_walk(start, per_day, step_minutes * 60, seed=made + step_minutes))
            made += 1
        day = datetime.fromtimestamp(day.timestamp() + 86_400, tz=ET)
    # one continuous price path is not needed by the server; times are what matter
    return rows


def _twenty_day_payload(symbol: str = SYMBOL) -> dict:
    """A realistic 20-session full chart payload (what the server keeps cached)."""
    first = datetime(2026, 7, 20, tzinfo=ET)  # a Monday
    bars = _session_bars(first, 20, 1)          # 19,200 one-minute bars
    fine = _session_bars(first, 20, 5)          # 3,840 five-minute bars
    study = _session_bars(first, 20, 30)        # 640 thirty-minute bars
    daily = _walk(_et(2025, 2, 3, 0, 0), 400, 86_400)
    return {
        "symbol": symbol,
        "source": "fixture",
        "bars": bars,
        "fineStudyBars": fine,
        "studyBars": study,
        "dailyBars": daily,
        "historyLoading": False,
        "error": "",
    }


class ServeState(DashboardState):
    """The real serve path with the build machinery recorded, not run.

    Same double as tests/test_chart_tape_staleness.py (bypasses the expensive
    __init__), plus the pieces a running request touches: the label-heal pool
    and the inline broker tail fetch are recorded fakes, and the extended-hours
    kick is off so the answer does not depend on the wall clock.
    """

    def __init__(self, payloads: dict[str, dict], market_newest_epoch: float, recorded=None):
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_chart_cache = {
            symbol: {"cached_at": time.monotonic(), "payload": payload, "history_ready": True}
            for symbol, payload in payloads.items()
        }
        self.oi_finder_chart_refreshes: set[str] = set()
        self.oi_finder_interactive_until = 0.0
        self.oi_finder_chart_disk_cache_dir = Path(tempfile.mkdtemp(prefix="agx-chart-disk-"))
        self._chart_market_newest_bar_epoch = market_newest_epoch
        self.refresh_calls: list[tuple[str, bool]] = []
        self.label_heals: list[str] = []
        self.tail_folds: list[str] = []
        if recorded is not None:
            self.chart_market_stream = recorded

    def _start_oi_finder_chart_refresh(self, target: str, full_history: bool) -> None:
        self.refresh_calls.append((target, full_history))

    def _submit_chart_label_heal(self, target: str, source_payload: dict):
        self.label_heals.append(target)
        return None

    def _chart_payload_has_ready_ganesh_signals(self, payload) -> bool:
        return True

    def _chart_payload_has_multi_timeframe_depth(self, payload) -> bool:
        return True

    @staticmethod
    def _extended_session_open(now_epoch: float | None = None) -> bool:
        return False

    def _refresh_oi_finder_chart_payload(self, target, full_history, *args, **kwargs):
        """The broker tail fetch: append one newer minute to the served tape."""
        self.tail_folds.append(target)
        with self.oi_finder_chart_lock:
            entry = self.oi_finder_chart_cache[target]
            payload = dict(entry["payload"])
            last = payload["bars"][-1]
            payload["bars"] = payload["bars"] + [{**last, "time": last["time"] + 60, "close": last["close"] + 0.5}]
            entry["payload"] = payload
            entry.pop("windowed_payload", None)
        return payload


def _assert_tape(rows, name):
    assert isinstance(rows, list) and rows, f"{name} is empty"
    times = [row["time"] for row in rows]
    assert times == sorted(set(times)), f"{name} times are not strictly ascending and unique"
    for row in rows:
        for key in ("time", "open", "high", "low", "close", "volume"):
            value = row.get(key)
            assert isinstance(value, (int, float)) and not isinstance(value, bool), f"{name} {key}={value!r}"
            assert math.isfinite(float(value)), f"{name} {key} is not finite"
        assert row["low"] <= min(row["open"], row["close"]) and row["high"] >= max(row["open"], row["close"]), name


# --------------------------------------------------------------- charts-squeeze


def test_first_paint_carries_the_2400_bar_five_minute_tape_the_squeeze_fires_need():
    payload = _twenty_day_payload()
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true")
    assert status == 200, body
    assert body["initialSlim"] is True and body["historyLoading"] is True
    fine = body["fineStudyBars"]
    assert len(fine) == DashboardState.OI_CHART_INITIAL_FINE_BAR_COUNT == 2400
    # It is the NEWEST 2,400, not the oldest: a 2h squeeze needs the last ~40 h.
    assert fine == payload["fineStudyBars"][-2400:]
    span_hours = (fine[-1]["time"] - fine[0]["time"]) / 3600
    assert span_hours >= 20 * 2, f"2,400 5m bars span only {span_hours:.0f} h - too short for a 20-bucket 2h squeeze"
    # The rest of the first paint stays slim.
    assert len(body["bars"]) == DashboardState.OI_CHART_INITIAL_BAR_COUNT
    assert body["bars"][-1] == payload["bars"][-1]
    assert len(body["dailyBars"]) == DashboardState.OI_CHART_INITIAL_DAILY_BAR_COUNT
    assert "studyBars" not in body, "the 30m deep tape must not ride the first paint"


def test_a_short_fine_tape_is_sent_whole_not_padded_or_dropped():
    payload = _twenty_day_payload()
    payload["fineStudyBars"] = payload["fineStudyBars"][-500:]
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true")
    assert status == 200, body
    assert body["fineStudyBars"] == payload["fineStudyBars"]


# ---------------------------------------------------------------- charts-mobile


def test_phone_first_paint_of_a_twenty_day_tape_stays_under_150_kb_on_the_wire():
    payload = _twenty_day_payload()
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(
            f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true",
            headers={"Accept-Encoding": "gzip"},
        )
        headers = api.last_headers
        full_status, full = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}", headers={"Accept-Encoding": "gzip"})
        full_bytes = int(api.last_headers["Content-Length"])
    assert status == 200 and full_status == 200
    assert headers.get("Content-Encoding") == "gzip", "a phone on cellular must get a compressed first paint"
    wire_bytes = int(headers["Content-Length"])
    assert wire_bytes < 150_000, f"first paint is {wire_bytes:,} bytes gzipped"
    assert wire_bytes * 3 < full_bytes, (wire_bytes, full_bytes)  # the slimming is what keeps it small
    assert len(full["bars"]) == len(payload["bars"])
    assert body["symbol"] == SYMBOL


def test_without_a_gzip_offer_the_body_is_plain_json():
    payload = _twenty_day_payload()
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true", headers={"Accept-Encoding": "identity"})
        encoding = api.last_headers.get("Content-Encoding")
    assert status == 200 and encoding in (None, "")
    assert isinstance(body, dict) and body["symbol"] == SYMBOL


# ------------------------------------------------- charts-tos-study-overlays


class RecordedStream:
    """The Schwab CHART_EQUITY minutes the stream recorded for the symbol."""

    def __init__(self, rows):
        self.rows = rows
        self.asked: list[str] = []

    def chart_history(self, symbol, since=0):
        self.asked.append(symbol)
        return [row for row in self.rows if row["time"] >= since]


def test_every_study_tape_is_ohlcv_with_ascending_unique_times_after_the_stream_splice():
    # Anchored to the wall clock only because the splice keeps the last two
    # days: the OUTCOME does not depend on what time it is.
    now_minute = int(time.time()) // 60 * 60
    rest_bars = _walk(now_minute - 120 * 60, 110, 60)            # closed REST minutes up to now-11m
    overlap = [dict(bar, close=bar["close"] + 5, high=bar["high"] + 5) for bar in rest_bars[-5:]]
    newer = _walk(now_minute - 10 * 60, 10, 60, price=rest_bars[-1]["close"], seed=99)
    recorded = RecordedStream(overlap + newer + newer[:3])          # duplicates on purpose
    payload = _twenty_day_payload()
    payload["bars"] = rest_bars
    state = ServeState({SYMBOL: payload}, market_newest_epoch=rest_bars[-1]["time"], recorded=recorded)
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}")
    assert status == 200, body
    assert recorded.asked == [SYMBOL]
    for name in ("bars", "studyBars", "fineStudyBars", "dailyBars"):
        _assert_tape(body[name], name)
    assert body["recordedHistoryApplied"] is True
    served = {bar["time"]: bar for bar in body["bars"]}
    assert len(body["bars"]) == len(rest_bars) + len(newer), "stream minutes were not appended exactly once"
    for bar in rest_bars[-5:]:
        assert served[bar["time"]]["close"] == bar["close"], "a closed REST minute must win over a stream restatement"
    assert body["historyBarsThrough"] == newer[-1]["time"] == body["bars"][-1]["time"]


# ------------------------------------------------------ charts-tape-freshness


def test_a_tape_that_missed_a_session_is_flagged_stale_through_the_route():
    payload = {"symbol": SYMBOL, "bars": _walk(_et(2026, 8, 4, 15, 50), 10, 60), "historyLoading": False, "error": ""}
    state = ServeState({SYMBOL: payload}, market_newest_epoch=_et(2026, 8, 13))
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}")
        slim_status, slim = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&initial=true")
    assert status == 200 and slim_status == 200
    for served in (body, slim):  # the first paint must carry the verdict too
        assert served["tapeStale"] is True
        assert served["tapeNewestSession"] == "2026-08-04"
        assert served["marketNewestSession"] == "2026-08-13"
        assert "2026-08-04" in served["staleTapeMessage"] and SYMBOL in served["staleTapeMessage"]
        assert served["error"] == served["staleTapeMessage"]


def test_a_current_tape_carries_no_stale_flag_or_message():
    payload = {"symbol": SYMBOL, "bars": _walk(_et(2026, 8, 13, 15, 50), 10, 60), "historyLoading": False, "error": ""}
    state = ServeState({SYMBOL: payload}, market_newest_epoch=_et(2026, 8, 13))
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}")
    assert status == 200, body
    assert body["tapeStale"] is False
    assert "staleTapeMessage" not in body
    assert body["error"] == ""


def test_refresh_true_folds_the_broker_tail_once_and_serves_the_newer_candle():
    bars = _walk(_et(2026, 8, 13, 15, 0), 30, 60)
    payload = {"symbol": SYMBOL, "bars": bars, "historyLoading": False, "error": ""}
    state = ServeState({SYMBOL: payload}, market_newest_epoch=bars[-1]["time"])
    with running_api(state) as api:
        plain_status, plain = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}")
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&refresh=true")
        again_status, again = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&refresh=true")
    assert plain_status == status == again_status == 200
    assert plain["bars"][-1]["time"] == bars[-1]["time"], "a plain GET must not fetch the broker on the request thread"
    assert body["bars"][-1]["time"] == bars[-1]["time"] + 60, "refresh=true must answer with the folded tail"
    assert state.tail_folds == [SYMBOL], "the inline broker fetch is rate limited to once per refresh interval"
    assert again["bars"][-1]["time"] == bars[-1]["time"] + 60
    assert any(target == SYMBOL for target, _full in state.refresh_calls), "the async refresh must still be queued"


def test_a_cold_symbol_answers_warming_at_once_and_queues_one_full_build():
    state = ServeState({}, market_newest_epoch=_et(2026, 8, 13))
    with running_api(state) as api:
        started = time.perf_counter()
        status, body = api.get("/api/oi-finder-chart?symbol=ZZCOLD&initial=true")
        elapsed = time.perf_counter() - started
    assert status == 200, body
    assert body["warming"] is True and body["bars"] == []
    assert state.refresh_calls == [("ZZCOLD", True)]
    assert elapsed < 5, "a cold chart must never build on the request thread"


def test_history_status_poll_gets_a_tiny_readiness_object_not_a_tape():
    payload = _twenty_day_payload()
    state = ServeState({SYMBOL: payload}, market_newest_epoch=payload["bars"][-1]["time"])
    with running_api(state) as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={SYMBOL}&historyStatus=true")
        size = len(json.dumps(body))
    assert status == 200, body
    assert "bars" not in body and "fineStudyBars" not in body
    assert body["symbol"] == SYMBOL and body["hasBars"] is True
    assert size < 4_000, f"historyStatus answered {size:,} bytes"
