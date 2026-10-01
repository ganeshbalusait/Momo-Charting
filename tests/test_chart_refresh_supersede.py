"""Chart refresh churn, 2026-09-22 (market hours).

A tail refresh that was "presumed wedged" was replaced but never cancelled:
the superseded job stayed in the 2-worker, unbounded-queue pool and ran in
FULL when it was finally dequeued. Because the recency arm never stamped
progress, queue wait read as a stall, so every 30s kick past the 180s deadline
manufactured another duplicate - ~1,200-1,400 queued jobs, submissions
outrunning completions ~6:1, /api/health at 2.7-3.9s and ticker cards at 3-4
minutes.

These tests pin the four parts of the fix: drop superseded jobs at dequeue,
stamp progress around the tail build, a deadline above the honest build time,
and deep builds on their own pool so they cannot eat the tail capacity.
"""

from __future__ import annotations

import inspect
import re
import threading

import api_server
from api_server import DashboardState


class _RecordingPool:
    """Stands in for a ThreadPoolExecutor without starting a thread."""

    def __init__(self, name: str, run_inline: bool = False) -> None:
        self.name = name
        self.run_inline = run_inline
        self.jobs: list[tuple] = []

    def submit(self, fn, *args, **kwargs):
        self.jobs.append((fn, args, kwargs))
        if self.run_inline:
            fn(*args, **kwargs)
        return None


class _Stub(DashboardState):
    """Real refresh plumbing, no real build. __init__ is bypassed on purpose:
    the real one opens brokers, caches and pools."""

    def __init__(self) -> None:  # noqa: D107 - see class docstring
        self.oi_finder_chart_lock = threading.Lock()
        self.oi_finder_ondemand_builds = 0
        self.oi_finder_chart_cache: dict = {}
        self.oi_finder_chart_refreshes: dict = {}
        self.oi_finder_chart_recency_refreshes: dict = {}
        self.oi_finder_chart_refresh_pool = _RecordingPool("tail")
        self.oi_finder_chart_full_pool = _RecordingPool("full")
        self.oi_finder_chart_paint_pool = _RecordingPool("paint", run_inline=True)
        self.builds: list[tuple[str, bool]] = []
        self.refreshes: list[bool] = []
        self.stamp_seen_inside_build: list[float] = []

    # The stub build: no broker calls, no splice (empty bars short-circuits
    # the merge/persist block), and it reports the guard stamp it saw.
    def _build_oi_finder_chart_payload(self, target, fast_start=True, skip_studies=False):
        self.builds.append((target, bool(skip_studies)))
        entry = self.oi_finder_chart_recency_refreshes.get(target)
        if entry is not None:
            self.stamp_seen_inside_build.append(float(entry[1]))
            # Pretend the build took long enough for the stamp to go stale, so
            # the post-build stamp is observable without touching the clock.
            entry[1] = -1000.0
        return {"bars": []}


class _RefreshRecorder(_Stub):
    """Records whether the refresh body ran at all (the drop test)."""

    def _refresh_oi_finder_chart_payload(self, target, full_history, release=True,
                                         skip_studies=False, release_token=None):
        self.refreshes.append(bool(full_history))


def _token(state, kind="recency"):
    return (kind, id(state))


# ---------------------------------------------------------------------------
# 1. A superseded job is dropped at dequeue instead of building.
# ---------------------------------------------------------------------------

def test_a_superseded_recency_job_is_dropped_before_building(capsys):
    state = _RefreshRecorder()
    mine = ("recency", 1)
    # While this job sat in the queue it was declared wedged and replaced.
    state.oi_finder_chart_recency_refreshes["QQQ"] = [("recency", 2), 0.0]

    state._run_oi_finder_chart_refresh("QQQ", False, mine, False, 0.0)

    assert state.refreshes == [], "a superseded job must not run the build"
    out = capsys.readouterr().out
    assert "QQQ recency superseded; dropped before build" in out


def test_a_superseded_full_job_is_dropped_and_does_not_count_as_on_demand(capsys):
    state = _RefreshRecorder()
    state.oi_finder_chart_refreshes["NKE"] = [("full", 9), 0.0]

    state._run_oi_finder_chart_refresh("NKE", True, ("full", 8), True, 0.0)

    assert state.refreshes == []
    # A dropped job must not leave the warmer standing aside for nothing.
    assert state.oi_finder_ondemand_builds == 0
    assert "NKE full superseded; dropped before build" in capsys.readouterr().out


def test_a_job_whose_guard_vanished_is_also_dropped():
    state = _RefreshRecorder()  # no guard entry at all
    state._run_oi_finder_chart_refresh("SPY", False, ("recency", 3), False, 0.0)
    assert state.refreshes == []


def test_the_legacy_tokenless_call_shape_still_runs():
    # tests/stubs and old callers invoke the two-arg form; without a token
    # there is nothing to compare, so the job must still run.
    state = _RefreshRecorder()
    state._run_oi_finder_chart_refresh(target="AAPL", full_history=False)
    assert state.refreshes == [False]


def test_drop_logging_is_capped_at_a_summary_per_minute(capsys):
    state = _RefreshRecorder()
    for n in range(12):
        state.oi_finder_chart_recency_refreshes["SPY"] = [("recency", 99), 0.0]
        state._run_oi_finder_chart_refresh("SPY", False, ("recency", n), False, 0.0)
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "superseded" in ln]
    assert len(lines) <= state.CHART_REFRESH_DROP_LOG_LIMIT, (
        "per-drop logging must not itself become the load problem"
    )
    assert state._chart_refresh_drop_quiet == 12 - state.CHART_REFRESH_DROP_LOG_LIMIT


# ---------------------------------------------------------------------------
# 2. The current job runs and releases its own guard.
# ---------------------------------------------------------------------------

def test_the_current_job_runs_and_releases_its_guard():
    state = _Stub()
    mine = ("recency", 4)
    state.oi_finder_chart_recency_refreshes["TSLA"] = [mine, 0.0]

    state._run_oi_finder_chart_refresh("TSLA", False, mine, False, 0.0)

    assert state.builds == [("TSLA", False)], "the current job must build"
    assert "TSLA" not in state.oi_finder_chart_recency_refreshes, (
        "a completed refresh must release its own guard"
    )


def test_a_superseded_job_leaves_the_replacements_guard_alone():
    state = _RefreshRecorder()
    replacement = ("recency", 6)
    state.oi_finder_chart_recency_refreshes["META"] = [replacement, 12.0]

    state._run_oi_finder_chart_refresh("META", False, ("recency", 5), False, 0.0)

    assert state.oi_finder_chart_recency_refreshes["META"][0] == replacement


# ---------------------------------------------------------------------------
# 3. The recency arm stamps progress on both sides of the build.
# ---------------------------------------------------------------------------

def test_the_recency_arm_stamps_progress_before_and_after_the_build():
    state = _Stub()
    mine = ("recency", 7)
    # An ancient stamp stands in for "submitted, then sat in the queue".
    state.oi_finder_chart_recency_refreshes["NVDA"] = [mine, -1000.0]

    state._refresh_oi_finder_chart_payload(
        "NVDA", False, release=False, release_token=mine,
    )

    assert state.stamp_seen_inside_build and state.stamp_seen_inside_build[0] > -1000.0, (
        "progress must be stamped BEFORE the tail build, so queue wait is not "
        "misread as a stall"
    )
    # The stub build set the stamp back to -1000.0; a post-build stamp moves it.
    assert state.oi_finder_chart_recency_refreshes["NVDA"][1] > -1000.0, (
        "progress must be stamped again AFTER the tail build"
    )


def test_a_stalled_build_is_still_detectable_after_the_deadline():
    state = _Stub()
    mine = ("recency", 8)
    stalled = api_server.time.monotonic() - (
        api_server.OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS + 30.0
    )
    state.oi_finder_chart_recency_refreshes["SLV"] = [mine, stalled]
    assert state._chart_refresh_in_flight("SLV", kind="recency") is False, (
        "stamping must not disarm the deadline: a genuinely stalled build is "
        "still replaceable"
    )


def test_the_recency_build_logs_its_own_duration(capsys):
    state = _Stub()
    state._refresh_oi_finder_chart_payload("AMD", False, release=False)
    out = capsys.readouterr().out
    assert re.search(r"\[build-timing\] AMD\s+recency\s+\d+\.\d\ds bars=0", out), out


# ---------------------------------------------------------------------------
# 4. A merely QUEUED job is no longer replaced.
# ---------------------------------------------------------------------------

def test_a_queued_job_past_the_old_deadline_is_not_replaced(capsys):
    state = _Stub()
    mine = ("recency", 9)
    # Progress stamped 300s ago: past the old 180s deadline, inside the new one.
    state.oi_finder_chart_recency_refreshes["SPY"] = [mine, api_server.time.monotonic() - 300.0]

    state._start_oi_finder_chart_refresh("SPY", full_history=False)

    assert state.oi_finder_chart_recency_refreshes["SPY"][0] == mine, (
        "a healthy job that is merely queued must keep its guard"
    )
    assert state.oi_finder_chart_refresh_pool.jobs == [], "no duplicate may be queued"
    assert "presumed wedged" not in capsys.readouterr().out


def test_a_truly_wedged_job_is_still_replaced_and_the_line_reports_the_backlog(capsys):
    state = _Stub()
    stale = api_server.time.monotonic() - (
        api_server.OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS + 60.0
    )
    state.oi_finder_chart_recency_refreshes["SPY"] = [("recency", 10), stale]

    state._start_oi_finder_chart_refresh("SPY", full_history=False)

    assert state.oi_finder_chart_recency_refreshes["SPY"][0] != ("recency", 10)
    assert len(state.oi_finder_chart_refresh_pool.jobs) == 1
    out = capsys.readouterr().out
    assert "presumed wedged" in out and "queued=" in out


def test_the_recency_deadline_covers_the_measured_build_time():
    # Measured 2026-09-22: p90 recency build 271s. A deadline under that is a
    # false-positive generator, and every false positive queues a duplicate.
    assert api_server.OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS >= 300.0
    assert (
        api_server.OI_FINDER_CHART_RECENCY_DEADLINE_SECONDS
        < api_server.OI_FINDER_CHART_REFRESH_DEADLINE_SECONDS
    ), "the tail guard must still be shorter than the deep one"


# ---------------------------------------------------------------------------
# 5. Deep builds have their own pool.
# ---------------------------------------------------------------------------

def test_full_and_recency_builds_use_different_executors():
    state = _Stub()
    assert state._chart_refresh_executor(True) is not state._chart_refresh_executor(False)

    state._start_oi_finder_chart_refresh("HOOD", full_history=True)
    assert len(state.oi_finder_chart_full_pool.jobs) == 1, "deep build on the deep pool"
    assert state.oi_finder_chart_refresh_pool.jobs == [], (
        "a 39-79s deep build must not occupy half the two-slot tail pool"
    )

    state._start_oi_finder_chart_refresh("ABNB", full_history=False)
    assert len(state.oi_finder_chart_refresh_pool.jobs) == 1
    assert len(state.oi_finder_chart_full_pool.jobs) == 1


def test_the_deep_pool_is_one_worker_and_names_its_threads():
    init = inspect.getsource(DashboardState.__init__)
    m = re.search(
        r"oi_finder_chart_full_pool = ThreadPoolExecutor\(\s*max_workers=(\d+),\s*"
        r'thread_name_prefix="([^"]+)"',
        init,
    )
    assert m, "the deep build pool must exist, bounded and named for py-spy"
    assert int(m.group(1)) == 1, "ChartBuildLane already serialises deep builds"
    assert m.group(2) != "oi-finder-chart", "the two pools must be tellable apart in a dump"


def test_a_state_without_the_deep_pool_falls_back_to_the_tail_pool():
    # Every helper must tolerate a state built with __new__ (stubs, old tests).
    state = _Stub()
    del state.oi_finder_chart_full_pool
    assert state._chart_refresh_executor(True) is state.oi_finder_chart_refresh_pool


def test_the_queue_depth_probe_never_raises():
    state = _Stub()
    # _RecordingPool has no _work_queue: the probe must answer, not explode.
    assert state._chart_refresh_queue_depth(False) == -1
