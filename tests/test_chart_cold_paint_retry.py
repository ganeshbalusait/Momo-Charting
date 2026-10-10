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
import time

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
                    # A CURRENT tape: an old one would (rightly) earn the
                    # stale-tape paint, which test_chart_stale_tail_paint.py
                    # covers.
                    "payload": {"bars": [{"time": int(time.time()), "open": 1, "high": 1, "low": 1, "close": 1}]},
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
    _age_last_paint(state, "WXYZ", 10)  # queued, well inside any limit
    assert state._retry_cold_chart_paint("WXYZ") is False
    assert len(state.oi_finder_chart_paint_pool.jobs) == 1
    # Still QUEUED past the 45s deadline: that is a line, not a wedge, and
    # replacing it is the S9 duplicate fetch. Not re-claimed.
    _age_last_paint(state, "WXYZ", api_server.OI_FINDER_CHART_COLD_PAINT_DEADLINE_SECONDS)
    assert state._retry_cold_chart_paint("WXYZ") is False
    assert len(state.oi_finder_chart_paint_pool.jobs) == 1
    # A paint that STARTED and then ran past its deadline is replaced.
    state.oi_finder_chart_paint_guard["WXYZ"][3] = True
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


# ---------------------------------------------------------------------------
# Interactive paint lane and paint-claim semantics (2026-10-10, plan item 2).
# The clicked ticker's paint used to share a 3-worker FIFO with background
# paints (Timer wave, board prewarm, warmers): harness S5 5.4s, S9 60s, and a
# queued paint was declared dead at 45s and fetched twice.
# ---------------------------------------------------------------------------

from concurrent.futures import ThreadPoolExecutor  # noqa: E402

from request_context import background_scope  # noqa: E402


def _wait_for(predicate, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_a_clicked_ticker_paints_while_the_background_paint_pool_is_full():
    state = _Stub(paint_results=[True])
    release = threading.Event()
    background = ThreadPoolExecutor(max_workers=3, thread_name_prefix="test-bg-paint")
    live = ThreadPoolExecutor(max_workers=2, thread_name_prefix="test-live-paint")
    try:
        state.oi_finder_chart_paint_pool = background
        state.oi_finder_chart_interactive_paint_pool = live
        blockers = [background.submit(release.wait, 10) for _ in range(3)]
        state._note_chart_symbol_requested("CLICK")  # the request path stamps first
        assert state._kick_interactive_chart_paint("CLICK") is True
        assert _wait_for(lambda: "CLICK" in state.oi_finder_chart_cache)
        # Bars landed while every background paint worker was still blocked.
        assert not any(job.done() for job in blockers)
        assert state.oi_finder_chart_paint_guard["CLICK"][5] == "live"
    finally:
        release.set()
        background.shutdown(wait=True)
        live.shutdown(wait=True)


def test_the_cold_open_paint_of_a_clicked_ticker_uses_the_interactive_pool():
    state = _Stub(paint_results=[True])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=False)
    state.oi_finder_chart_interactive_paint_pool = _RecordingPool(run_inline=False)
    state._note_chart_symbol_requested("CLICK")
    state._start_oi_finder_chart_refresh("CLICK", full_history=True)
    assert len(state.oi_finder_chart_interactive_paint_pool.jobs) == 1
    assert state.oi_finder_chart_paint_pool.jobs == []


def test_background_work_never_takes_an_interactive_paint_worker():
    state = _Stub(paint_results=[True])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=False)
    state.oi_finder_chart_interactive_paint_pool = _RecordingPool(run_inline=False)
    state._note_chart_symbol_requested("CLICK")  # watched...
    with background_scope(True):  # ...but this is the Timer wave / a warmer
        state._start_oi_finder_chart_refresh("CLICK", full_history=True)
        assert state._kick_interactive_chart_paint("CLICK") is False
    assert len(state.oi_finder_chart_paint_pool.jobs) == 1
    assert state.oi_finder_chart_interactive_paint_pool.jobs == []


def test_a_superseded_paint_does_not_fetch():
    state = _Stub(paint_results=[True])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=False)
    first = state._claim_chart_paint("ABCD")
    # Its claim passed the queued cap and a replacement was dispatched.
    state.oi_finder_chart_paint_guard["ABCD"][1] -= api_server.OI_FINDER_CHART_PAINT_QUEUED_CAP_SECONDS + 1
    second = state._claim_chart_paint("ABCD")
    assert second is not None and second != first
    assert state._run_chart_paint("ABCD", first) == 0
    assert state.paints == 0, "the displaced job must not call the broker"
    assert state._run_chart_paint("ABCD", second) == 1
    assert state.paints == 1


def test_the_paint_deadline_starts_when_the_job_starts():
    state = _Stub(paint_results=[True])
    token = state._claim_chart_paint("ABCD")
    entry = state.oi_finder_chart_paint_guard["ABCD"]
    entry[1] -= 200.0  # queued for 200s
    assert entry[3] is False
    assert state._claim_chart_paint("ABCD") is None  # in line, not wedged
    before = time.monotonic()
    state._run_chart_paint("ABCD", token)
    assert entry[3] is True and entry[1] >= before, "re-stamped at start"


def test_a_click_preempts_a_background_paint_that_has_not_started():
    state = _Stub(paint_results=[True])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=False)
    state.oi_finder_chart_interactive_paint_pool = _RecordingPool(run_inline=False)
    with background_scope(True):  # the board prewarm queued a paint
        bg_token = state._claim_chart_paint("ABCD", lane="bg")
    state._note_chart_symbol_requested("ABCD")
    assert state._kick_interactive_chart_paint("ABCD") is True
    assert len(state.oi_finder_chart_interactive_paint_pool.jobs) == 1
    assert state._run_chart_paint("ABCD", bg_token) == 0  # displaced: no fetch
    assert state.paints == 0
    # A live paint already queued is NOT preempted by a second poll.
    assert state._kick_interactive_chart_paint("ABCD") is False


# ---------------------------------------------------------------------------
# Cooldown-aware paint (2026-10-10, plan item 3). The fixed 3s retry did not
# know when the gate cooldown ends (S6: 1.09-4.16s late) and its retry became
# the next 429 strike; every empty paint also spent the spare profile and a
# broker quote probe the shared gate refused anyway.
# ---------------------------------------------------------------------------

import pandas as pd  # noqa: E402

from data.schwab_client import SchwabClient  # noqa: E402
from data.schwab_rate_limit import GATE, SchwabUnavailable  # noqa: E402


class _GatedSchwab(SchwabClient):
    """A real SchwabClient whose wire is a counter behind the real GATE."""

    configured = True

    def __init__(self) -> None:  # noqa: D107 - no credentials, no files
        from zoneinfo import ZoneInfo
        self._tz = ZoneInfo(api_server.EASTERN_TZ)
        self.attempts = 0
        self.wire = 0

    def _get_price_history(self, symbol, timeframe, start, end):
        self.attempts += 1
        GATE.acquire()
        self.wire += 1
        now = pd.Timestamp.now(tz="UTC").floor("min")
        stamps = [now - pd.Timedelta(minutes=offset) for offset in (2, 1, 0)]
        return pd.DataFrame({
            "timestamp": stamps,
            "open": [10.0] * 3, "high": [10.0] * 3, "low": [10.0] * 3,
            "close": [10.0] * 3, "volume": [100] * 3,
        })


class _BuildStub(DashboardState):
    """The real candles-only build and refresh, broker swapped for the gate."""

    def __init__(self) -> None:  # noqa: D107
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_ondemand_builds = 0
        self.oi_finder_chart_cache: dict = {}
        self.oi_finder_chart_refreshes: dict = {}
        self.oi_finder_chart_recency_refreshes: dict = {}
        self.oi_finder_chart_refresh_pool = _RecordingPool()
        self.oi_finder_chart_full_pool = _RecordingPool()
        self.oi_finder_chart_paint_pool = _RecordingPool(run_inline=True)
        self.probes = 0

    def _symbol_unknown_to_broker(self, symbol):
        self.probes += 1
        return None

    def _save_oi_finder_chart_disk_payload(self, target, payload):
        return None


class _FakeClock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _gate_cooling(monkeypatch, seconds: float) -> _FakeClock:
    clock = _FakeClock()
    GATE.reset()
    monkeypatch.setattr(GATE, "_clock", clock)
    GATE.note_rate_limited(seconds)
    return clock


def _shift_paint_clock(state, target, seconds):
    """Pretend `seconds` passed for the paint guard (real monotonic)."""
    entry = state.oi_finder_chart_paint_guard[target]
    entry[1] -= seconds
    if entry[2] is not None:
        entry[2] -= seconds
    if entry[4]:
        entry[4] -= seconds


def test_the_paint_waits_out_the_gate_cooldown_then_dispatches_on_the_next_poll(monkeypatch):
    primary, spare = _GatedSchwab(), _GatedSchwab()
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [primary, spare])
    try:
        clock = _gate_cooling(monkeypatch, 10.0)
        state = _BuildStub()
        assert state._kick_interactive_chart_paint("COLD") is True
        assert primary.wire == 0, "nothing goes on the wire during a cooldown"
        assert spare.attempts == 0, "the spare profile shares the gate: not tried"
        assert state.probes == 0, "a refusal says nothing about the symbol"
        entry = state.oi_finder_chart_paint_guard["COLD"]
        assert entry[4] - entry[2] >= 9.0, "the retry is booked for the cooldown's end"

        # Past the blind 3s retry, still inside the cooldown: no dispatch.
        _shift_paint_clock(state, "COLD", api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS + 1)
        clock.now += api_server.OI_FINDER_CHART_COLD_PAINT_RETRY_SECONDS + 1
        assert state._kick_interactive_chart_paint("COLD") is False
        assert primary.attempts == 1

        # The first poll after the cooldown dispatches, and the bars land.
        _shift_paint_clock(state, "COLD", 7.0)
        clock.now += 7.0
        assert state._kick_interactive_chart_paint("COLD") is True
        assert primary.wire == 1
        assert state.oi_finder_chart_cache["COLD"]["payload"]["bars"]
    finally:
        GATE.reset()


def test_a_refused_paint_payload_says_so_honestly(monkeypatch):
    primary = _GatedSchwab()
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [primary])
    try:
        _gate_cooling(monkeypatch, 15.0)
        state = _BuildStub()
        payload = state._build_oi_finder_chart_payload("COLD", fast_start=True, skip_studies=True)
        assert payload["bars"] == []
        assert payload["brokerUnavailableKind"] == "rate_limit"
        assert payload["brokerRetryAfter"] >= 14.0
        assert "cooling down" in payload["error"]
        # The Alpaca fallback is OFF; the error must not send him to Settings.
        assert not api_server.CHART_BARS_USE_ALPACA_FALLBACK
        assert "Alpaca" not in payload["error"]
    finally:
        GATE.reset()


def test_existing_bar_callers_still_get_an_empty_frame_during_a_cooldown(monkeypatch):
    client = _GatedSchwab()
    try:
        _gate_cooling(monkeypatch, 10.0)
        assert client.get_chart_bars("AAPL").empty
        assert client.get_intraday_bars(["AAPL"]) == {}
        try:
            client.get_chart_bars("AAPL", raise_unavailable=True)
        except SchwabUnavailable as exc:
            assert exc.kind == "rate_limit" and exc.retry_after > 0
        else:  # pragma: no cover - the assertion is the point
            raise AssertionError("raise_unavailable=True must surface the refusal")
    finally:
        GATE.reset()


def test_the_warming_stub_carries_the_cooldown_the_paint_met(monkeypatch):
    # Review 2026-10-10: the refusal fields were only ever built into an
    # EMPTY paint payload, which is never stored - so no response carried
    # them. The warming stub now reads them from the paint guard.
    primary = _GatedSchwab()
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [primary])
    try:
        _gate_cooling(monkeypatch, 15.0)
        state = _BuildStub()
        state.touch_oi_finder_interactive_window = lambda: None
        state._load_oi_finder_chart_disk_payload = lambda symbol: None
        state._kick_interactive_chart_paint("COLD")
        stub = state._oi_finder_chart_payload_impl("COLD", initial_paint=True)
        assert stub["warming"] is True
        assert stub["brokerUnavailableKind"] == "rate_limit"
        assert 0 < stub["brokerRetryAfter"] <= 15.0
        # Once the booked retry time passes, the stub stops claiming it.
        _shift_paint_clock(state, "COLD", 16.0)
        assert state._chart_paint_refusal_fields("COLD") == {}
    finally:
        GATE.reset()


def test_the_raise_unavailable_check_reads_the_instance_not_the_class():
    # Review 2026-10-10: cached per type(client), a real SchwabClient answered
    # True for the class, and a later instance-patched get_chart_bars that
    # takes no such keyword got it anyway - a TypeError swallowed as "no bars".
    real = _GatedSchwab()
    assert api_server._bars_call_accepts_raise_unavailable(real) is True
    patched = _GatedSchwab()
    patched.get_chart_bars = lambda symbol, timeframe="1Min", days_back=30: pd.DataFrame()
    assert api_server._bars_call_accepts_raise_unavailable(patched) is False
    assert api_server._bars_call_accepts_raise_unavailable(real) is True


def test_a_clicked_tickers_deep_build_is_dispatched_to_its_own_pool():
    # Review 2026-10-10: only the first four dequeued deep jobs reach the
    # lane's priority sort; a click behind 10 background jobs waited 7
    # builds in the shared executor's FIFO. Its deep build now goes to the
    # interactive pair (same ChartBuildLane, so still one build at a time).
    state = _Stub(paint_results=[True])
    state.oi_finder_chart_paint_pool = _RecordingPool(run_inline=True)
    state.oi_finder_chart_interactive_paint_pool = _RecordingPool(run_inline=True)
    state.oi_finder_chart_interactive_full_pool = _RecordingPool()
    with background_scope(True):
        for index in range(10):
            state._start_oi_finder_chart_refresh(f"BG{index}", full_history=True)
    state._note_chart_symbol_requested("CLICK")
    state._start_oi_finder_chart_refresh("CLICK", full_history=True)
    assert len(state.oi_finder_chart_full_pool.jobs) == 10
    assert [job[1][0] for job in state.oi_finder_chart_interactive_full_pool.jobs] == ["CLICK"]
