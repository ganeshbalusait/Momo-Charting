"""Guard livelock, 2026-10-10.

The recency/deep in-flight guards clocked their deadline from SUBMISSION. A
job still waiting in its pool queue past the deadline was "presumed wedged"
and replaced; the replacement joined the back of the same queue and the
original was dropped at dequeue as superseded. Under a deep enough backlog the
symbol's own polls kept replacing its job and it never ran at all.

The deep pool also had ONE worker, so waiting deep jobs queued in the
executor's FIFO - which knows nothing about ChartBuildLane's interactive
priority - and the clicked ticker's deep build sat behind prewarm/Timer builds
(main S8: 185s of deep queue). Four workers let the waiters wait inside the
lane, which still lets exactly one build run.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

import api_server
from api_server import ChartBuildLane, DashboardState


class _RecordingPool:
    """A pool whose workers are all busy: jobs queue and never run."""

    def __init__(self) -> None:
        self.jobs: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))
        return None


class _Stub(DashboardState):
    def __init__(self) -> None:  # noqa: D107
        self.oi_finder_chart_lock = threading.RLock()
        self.oi_finder_ondemand_builds = 0
        self.oi_finder_chart_cache: dict = {}
        self.oi_finder_chart_refreshes: dict = {}
        self.oi_finder_chart_recency_refreshes: dict = {}
        self.oi_finder_chart_refresh_pool = _RecordingPool()
        self.oi_finder_chart_full_pool = _RecordingPool()
        self.oi_finder_chart_paint_pool = _RecordingPool()
        self.ran: list[tuple[str, bool]] = []

    def _refresh_oi_finder_chart_payload(self, target, full_history, release=True,
                                         skip_studies=False, release_token=None):
        self.ran.append((target, bool(full_history)))


def test_a_queued_tail_job_is_never_replaced_by_its_own_polls(monkeypatch):
    monkeypatch.setattr(api_server, "OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS", 0.3)
    state = _Stub()
    # A 40-job backlog of other symbols ahead of the click.
    for index in range(40):
        state._start_oi_finder_chart_refresh(f"BG{index}", full_history=False)
    state._start_oi_finder_chart_refresh("CLICK", full_history=False)
    queued = len(state.oi_finder_chart_refresh_pool.jobs)
    token = state.oi_finder_chart_recency_refreshes["CLICK"][0]

    # Polls every 0.2s for ten "seconds", the job still in line throughout.
    for _ in range(50):
        state.oi_finder_chart_recency_refreshes["CLICK"][1] -= 0.2
        state._start_oi_finder_chart_refresh("CLICK", full_history=False)
        assert state._chart_refresh_in_flight("CLICK", kind="recency")

    assert len(state.oi_finder_chart_refresh_pool.jobs) == queued, "no duplicate queued"
    assert state.oi_finder_chart_recency_refreshes["CLICK"][0] == token, "never replaced"

    # When the backlog drains, CLICK's job runs - once.
    for fn, args, kwargs in state.oi_finder_chart_refresh_pool.jobs:
        fn(*args, **kwargs)
    assert state.ran.count(("CLICK", False)) == 1


def test_a_started_job_past_its_deadline_is_still_replaced(monkeypatch, capsys):
    monkeypatch.setattr(api_server, "OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS", 0.3)
    state = _Stub()
    state._start_oi_finder_chart_refresh("WEDGE", full_history=False)
    token = state.oi_finder_chart_recency_refreshes["WEDGE"][0]
    state._mark_chart_refresh_started("WEDGE", False, token)
    state.oi_finder_chart_recency_refreshes["WEDGE"][1] -= 1.0  # stalled while running
    state._start_oi_finder_chart_refresh("WEDGE", full_history=False)
    assert state.oi_finder_chart_recency_refreshes["WEDGE"][0] != token
    assert "presumed wedged" in capsys.readouterr().out


def test_a_queued_job_is_given_up_on_only_after_the_long_cap():
    state = _Stub()
    state._start_oi_finder_chart_refresh("LOST", full_history=True)
    token = state.oi_finder_chart_refreshes["LOST"][0]
    state.oi_finder_chart_refreshes["LOST"][1] -= api_server.OI_FINDER_CHART_REFRESH_DEADLINE_SECONDS + 1
    state._start_oi_finder_chart_refresh("LOST", full_history=True)
    assert state.oi_finder_chart_refreshes["LOST"][0] == token
    state.oi_finder_chart_refreshes["LOST"][1] -= api_server.OI_FINDER_CHART_REFRESH_QUEUED_CAP_SECONDS
    state._start_oi_finder_chart_refresh("LOST", full_history=True)
    assert state.oi_finder_chart_refreshes["LOST"][0] != token


def test_dequeue_marks_the_job_started():
    state = _Stub()
    state._start_oi_finder_chart_refresh("ABCD", full_history=False)
    entry = state.oi_finder_chart_recency_refreshes["ABCD"]
    assert entry[2] is False
    fn, args, kwargs = state.oi_finder_chart_refresh_pool.jobs[0]
    # Inspect the guard from inside the "build".
    seen = []
    state._refresh_oi_finder_chart_payload = (
        lambda target, full_history, **kw: seen.append(list(entry))
    )
    fn(*args, **kwargs)
    assert seen and seen[0][2] is True


def test_legacy_two_element_entries_keep_the_old_deadline():
    # Test stubs and any pre-2026-10-10 entry: treated as started.
    state = _Stub()
    state.oi_finder_chart_recency_refreshes["OLD"] = [("recency", 1), time.monotonic() - 10_000]
    assert not state._chart_refresh_in_flight("OLD", kind="recency")


def _drive_lane(order: list, holder_release: threading.Event, interactive: set):
    lane = ChartBuildLane(lambda symbol: symbol in interactive)
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="test-deep")
    holding = threading.Event()
    active = {"now": 0, "max": 0}
    guard = threading.Lock()

    def holder() -> None:
        lane.acquire("HOLD")
        holding.set()
        holder_release.wait(10)
        lane.release()

    def build(symbol: str) -> None:
        lane.acquire(symbol)
        try:
            with guard:
                active["now"] += 1
                active["max"] = max(active["max"], active["now"])
            order.append(symbol)
            time.sleep(0.02)
            with guard:
                active["now"] -= 1
        finally:
            lane.release()

    return lane, pool, holding, holder, build, active


def test_with_four_deep_workers_a_polled_symbol_jumps_a_queued_background_build():
    order: list = []
    release = threading.Event()
    lane, pool, holding, holder, build, _ = _drive_lane(order, release, {"CLICK"})
    try:
        pool.submit(holder)
        assert holding.wait(5)
        pool.submit(build, "PREWARM")  # queued first, nobody watching it
        pool.submit(build, "CLICK")    # the trader's ticker, queued second
        deadline = time.monotonic() + 5
        while len(lane._waiters) < 2 and time.monotonic() < deadline:  # noqa: SLF001
            time.sleep(0.01)
        assert len(lane._waiters) == 2, "both wait INSIDE the lane, not in the executor"  # noqa: SLF001
        release.set()
    finally:
        pool.shutdown(wait=True)
    assert order == ["CLICK", "PREWARM"]


def test_the_lane_still_serialises_deep_builds_across_four_workers():
    order: list = []
    release = threading.Event()
    release.set()
    _, pool, _, _, build, active = _drive_lane(order, release, set())
    try:
        for index in range(8):
            pool.submit(build, f"S{index}")
    finally:
        pool.shutdown(wait=True)
    assert len(order) == 8
    assert active["max"] == 1, "one deep build at a time, however many workers wait"
