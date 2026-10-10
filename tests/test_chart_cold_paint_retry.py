"""Cold scanner click, 2026-10-10: a chart that stayed blank for 3+ minutes.

A ticker with no cached tape got ONE candles-only paint, dispatched with its
deep rebuild. The deep rebuild's in-flight guard is held from that moment, so
when that one paint came back empty (Schwab 429 cooldown / throttled slot while
the scanner was busy) every later poll saw "refreshing" and simply waited for
the deep build - which queues behind other symbols' 40-200s builds on a
one-worker lane. These tests pin the fix: the paint has its own short guard and
is retried by the client's polls until candles exist.
"""

from __future__ import annotations

import threading

import api_server
from api_server import DashboardState


class _RecordingPool:
    def __init__(self, run_inline: bool = False) -> None:
        self.run_inline = run_inline
        self.jobs: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))
        if self.run_inline:
            fn(*args, **kwargs)
        return None


class _Stub(DashboardState):
    """Real dispatch plumbing; the paint is scripted. __init__ bypassed."""

    def __init__(self, paint_results: list[bool]) -> None:  # noqa: D107
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_ondemand_builds = 0
        self.oi_finder_chart_cache: dict = {}
        self.oi_finder_chart_refreshes: dict = {}
        self.oi_finder_chart_recency_refreshes: dict = {}
        self.oi_finder_chart_refresh_pool = _RecordingPool()
        self.oi_finder_chart_full_pool = _RecordingPool()  # deep builds: queued, never run
        self.oi_finder_chart_paint_pool = _RecordingPool(run_inline=True)
        self.paint_results = list(paint_results)
        self.paints = 0

    def _refresh_oi_finder_chart_payload(self, target, full_history, release=True,
                                         skip_studies=False, release_token=None):
        assert not full_history and skip_studies, "only the candles-only paint may run here"
        self.paints += 1
        produced = self.paint_results.pop(0) if self.paint_results else True
        if produced:
            with self.oi_finder_chart_lock:
                self.oi_finder_chart_cache[target] = {
                    "cached_at": 0.0,
                    "payload": {"bars": [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1}]},
                    "history_ready": False,
                }


def _age_last_paint(state, target, seconds):
    entry = state.oi_finder_chart_paint_guard[target]
    entry[1] -= seconds
    if entry[2] is not None:
        entry[2] -= seconds


def test_empty_first_paint_is_retried_while_the_deep_build_holds_its_guard():
    state = _Stub(paint_results=[False, True])
    state._start_oi_finder_chart_refresh("ABCD", full_history=True)
    assert state.paints == 1
    assert "ABCD" in state.oi_finder_chart_refreshes  # deep guard held
    assert len(state.oi_finder_chart_full_pool.jobs) == 1  # deep build queued
    assert "ABCD" not in state.oi_finder_chart_cache  # the paint came back empty

    # The deep guard blocks a second dispatch through the normal door...
    state._start_oi_finder_chart_refresh("ABCD", full_history=True)
    assert state.paints == 1
    # ...and an immediate poll is rate limited, not a broker call per poll.
    assert state._retry_cold_chart_paint("ABCD") is False
    assert state.paints == 1

    _age_last_paint(state, "ABCD", api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS + 1)
    assert state._retry_cold_chart_paint("ABCD") is True
    assert state.paints == 2
    assert state.oi_finder_chart_cache["ABCD"]["payload"]["bars"]
    # Candles are cached: no further paint attempts.
    _age_last_paint(state, "ABCD", 60)
    assert state._retry_cold_chart_paint("ABCD") is False
    assert state.paints == 2
    # The deep build was not duplicated.
    assert len(state.oi_finder_chart_full_pool.jobs) == 1


def test_a_paint_already_in_flight_is_not_doubled():
    state = _Stub(paint_results=[])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=False)
    state._start_oi_finder_chart_refresh("WXYZ", full_history=True)
    assert len(state.oi_finder_chart_paint_pool.jobs) == 1
    _age_last_paint(state, "WXYZ", 10)  # in flight, well inside its deadline
    assert state._retry_cold_chart_paint("WXYZ") is False
    assert len(state.oi_finder_chart_paint_pool.jobs) == 1
    # A paint wedged past its deadline is replaced.
    _age_last_paint(state, "WXYZ", api_server.OI_FINDER_CHART_COLD_PAINT_DEADLINE_SECONDS)
    assert state._retry_cold_chart_paint("WXYZ") is True
    assert len(state.oi_finder_chart_paint_pool.jobs) == 2


def test_history_status_poll_retries_the_paint_and_then_reports_bars():
    state = _Stub(paint_results=[False, True])
    state._note_chart_symbol_requested = lambda symbol: None
    state._start_oi_finder_chart_refresh("ABCD", full_history=True)
    _age_last_paint(state, "ABCD", api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS + 1)
    status = state.oi_finder_chart_history_status("ABCD")
    assert state.paints == 2
    assert status["refreshing"] is True
    assert state.oi_finder_chart_history_status("ABCD")["hasBars"] is True


def test_cold_request_path_retries_the_paint():
    state = _Stub(paint_results=[False, True])
    state.touch_oi_finder_interactive_window = lambda: None
    state._note_chart_symbol_requested = lambda symbol: None
    state._load_oi_finder_chart_disk_payload = lambda symbol: None
    state._start_oi_finder_chart_refresh("ABCD", full_history=True)
    _age_last_paint(state, "ABCD", api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS + 1)
    payload = state._oi_finder_chart_payload_impl("ABCD", initial_paint=True)
    assert payload.get("warming") is True  # this response is still the stub...
    assert state.paints == 2  # ...but the retried paint has cached candles
    assert state.oi_finder_chart_cache["ABCD"]["payload"]["bars"]
