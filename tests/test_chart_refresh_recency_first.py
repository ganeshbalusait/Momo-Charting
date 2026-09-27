from __future__ import annotations

import threading

from api_server import DashboardState


class _Recorder(DashboardState):
    """Records the refresh phases without doing any real work."""

    def __init__(self):  # bypass the real, expensive __init__
        self.calls: list[bool] = []
        # The real DashboardState always has these; the on-demand-build counter
        # that lets a trader's ticker preempt the warmer is guarded by the lock.
        self.oi_finder_chart_lock = threading.Lock()
        self.oi_finder_ondemand_builds = 0

    def _refresh_oi_finder_chart_payload(self, target: str, full_history: bool, release: bool = True,
                                          skip_studies: bool = False, release_token=None) -> None:
        # Signature tracks the real method (candles-first paint added
        # skip_studies; wedge takeover added release_token).
        self.calls.append(full_history)


def test_a_full_rebuild_lands_todays_candles_first():
    # A ticker whose cache is not history_ready used to go straight to the full
    # rebuild, which takes MINUTES (PLTR measured ~15 min). For that whole
    # window every client poll was answered from the stale cached tape, so the
    # chart showed yesterday's candles plus a synthetic live quote bar - the
    # "gap" the trader sees on any watchlist ticker they have not opened today.
    state = _Recorder()
    state._run_oi_finder_chart_refresh(target="BABA", full_history=True)
    assert state.calls == [False, True], (
        "a full rebuild must run a fast recency pass first, then the deep rebuild"
    )


def test_a_recency_refresh_does_not_trigger_a_second_pass():
    state = _Recorder()
    state._run_oi_finder_chart_refresh(target="AAPL", full_history=False)
    assert state.calls == [False]


def test_warmer_skips_a_cache_rebuilt_during_this_session():
    # Age-based staleness could not work: a full pass over 392 tickers takes
    # ~8h, so a 4h window meant the head of the list went stale before the
    # tail was reached and the warmer thrashed without ever completing a pass.
    # Measured 2026-08-13: only 133 of 392 caches had been rebuilt that day.
    session_start = 1_786_000_000.0
    assert DashboardState._warmer_cache_is_current(
        schema_stale=False,
        cache_epoch=session_start + 60.0,
        session_start_epoch=session_start,
    ) is True


def test_warmer_rebuilds_a_cache_from_before_this_session():
    session_start = 1_786_000_000.0
    assert DashboardState._warmer_cache_is_current(
        schema_stale=False,
        cache_epoch=session_start - 1.0,
        session_start_epoch=session_start,
    ) is False


def test_warmer_always_rebuilds_a_schema_stale_cache():
    session_start = 1_786_000_000.0
    assert DashboardState._warmer_cache_is_current(
        schema_stale=True,
        cache_epoch=session_start + 10_000.0,
        session_start_epoch=session_start,
    ) is False


def test_session_start_is_the_most_recent_one_already_begun():
    import datetime as _dt
    from zoneinfo import ZoneInfo
    et = ZoneInfo("America/New_York")
    # Thursday 17:00 ET -> that same morning 09:30.
    now = _dt.datetime(2026, 8, 13, 17, 0, tzinfo=et)
    assert DashboardState._most_recent_session_start(now).strftime("%Y-%m-%d %H:%M") == "2026-08-13 09:30"
    # Thursday 08:00 ET, before the bell -> Wednesday 09:30.
    now = _dt.datetime(2026, 8, 13, 8, 0, tzinfo=et)
    assert DashboardState._most_recent_session_start(now).strftime("%Y-%m-%d %H:%M") == "2026-08-12 09:30"
    # Sunday -> walks back to Friday.
    now = _dt.datetime(2026, 8, 16, 12, 0, tzinfo=et)
    assert DashboardState._most_recent_session_start(now).strftime("%Y-%m-%d %H:%M") == "2026-08-14 09:30"
