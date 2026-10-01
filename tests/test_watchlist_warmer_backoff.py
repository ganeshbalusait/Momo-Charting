from __future__ import annotations

from api_server import DashboardState


class _Stub(DashboardState):
    def __init__(self):  # bypass the real, expensive __init__
        self.oi_finder_interactive_until = 0.0


def test_waits_while_a_session_is_active():
    state = _Stub()
    state.oi_finder_interactive_until = 1000.0
    # Session active, and we built recently -> wait.
    assert state._warmer_should_wait(now=900.0, last_build_at=880.0) is True


def test_does_not_wait_when_no_session_is_active():
    state = _Stub()
    state.oi_finder_interactive_until = 0.0
    assert state._warmer_should_wait(now=900.0, last_build_at=880.0) is False


def test_forces_progress_even_during_a_long_session():
    state = _Stub()
    state.oi_finder_interactive_until = 10_000.0
    # Session still active, but nothing has been built for longer than the
    # force-progress floor -> build anyway, so the warmer cannot be starved.
    stale = 900.0 + DashboardState.WARMER_FORCE_PROGRESS_SECONDS + 1
    assert state._warmer_should_wait(now=stale, last_build_at=900.0) is False


def test_never_built_yet_does_not_force_immediately():
    state = _Stub()
    state.oi_finder_interactive_until = 10_000.0
    # last_build_at == now means "no build yet this loop"; a fresh boot during
    # an active session must still defer rather than force a build instantly.
    assert state._warmer_should_wait(now=100.0, last_build_at=100.0) is True


def test_an_on_demand_build_preempts_background_warming():
    # The warmer rebuilds the whole watchlist; a trader opening a ticker needs
    # ITS full history now. On 2026-08-13 a 392-ticker backlog meant the pane
    # on screen queued behind rebuilds of tickers nobody was looking at, so 4H
    # showed ~13 candles until its turn came - and which ticker was thin
    # rotated as the queue moved (MSFT, then GOOGL).
    state = _Stub()
    state.oi_finder_interactive_until = 0.0          # no interactive window
    state.oi_finder_ondemand_builds = 1              # ...but a trader is waiting
    assert state._warmer_should_wait(now=10_000.0, last_build_at=0.0) is True, (
        "an on-demand build must win even when the warmer is otherwise free to run"
    )


def test_no_on_demand_build_restores_normal_backoff():
    state = _Stub()
    state.oi_finder_interactive_until = 0.0
    state.oi_finder_ondemand_builds = 0
    assert state._warmer_should_wait(now=10_000.0, last_build_at=0.0) is False


def test_a_missing_counter_is_treated_as_zero():
    # Defensive: a DashboardState built before this attribute existed must not
    # deadlock the warmer.
    state = _Stub()
    state.oi_finder_interactive_until = 0.0
    assert state._warmer_should_wait(now=10_000.0, last_build_at=0.0) is False
