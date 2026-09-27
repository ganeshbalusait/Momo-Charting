"""A slow chain build must not re-fire the instant it lands.

2026-08-28, "any ticker search takes more than 30 seconds". The chain path
logs every build over 2s. In one session: 52 slow builds, 45 of them the SAME
ticker, worst 874s, 2,284s of build time total.

The loop: a full build with history extras runs longer than the cache it is
refreshing stays fresh. So it finishes already stale, the next client poll
sees a stale cache and fires it again, and the symbol holds one of only four
workers permanently. Every other ticker the trader searched queued behind it.

The cooldown is the symbol's own last build duration, capped. Healthy symbols
are unaffected; pathological ones are contained.
"""

from __future__ import annotations

import api_server

backoff = api_server.DashboardState._oi_finder_refresh_backoff
CAP = api_server.DashboardState.OI_FINDER_REFRESH_MAX_COOLDOWN_SECONDS


def test_a_fast_build_is_effectively_unthrottled():
    # 2s build -> 2s hold. The trader cannot perceive this, and it keeps the
    # rule proportional rather than a flat penalty on healthy symbols.
    assert backoff(2.0) == 2.0


def test_the_pathological_build_is_contained_instead_of_re_firing():
    # The measured worst case. Without the cap this symbol restarted instantly,
    # forever, holding a worker.
    assert backoff(874.0) == CAP
    assert CAP < 874.0


def test_the_cap_bounds_how_often_a_slow_symbol_may_retry():
    # A slow symbol may re-fire at most ~12 times an hour, not continuously.
    assert 3600 / CAP <= 12


def test_a_zero_or_negative_duration_never_throttles():
    assert backoff(0) == 0.0
    assert backoff(-5) == 0.0


def test_bad_input_never_raises_into_the_refresh_path():
    # This runs in a `finally`; an exception here would leak the in-flight
    # guard and wedge the symbol permanently - worse than the bug it fixes.
    assert backoff("nonsense") == 0.0
    assert backoff(None) == 0.0


def test_both_refresh_passes_honour_and_record_the_cooldown():
    import inspect

    source = inspect.getsource(api_server.DashboardState._refresh_oi_finder_in_background)
    # The history pass and the quick pass each need their own gate + record,
    # or the uncovered one keeps the loop alive on its own key.
    assert source.count("oi_finder_refresh_cooldown") >= 4, source.count("oi_finder_refresh_cooldown")
    assert "_oi_finder_refresh_backoff" in source


def test_the_in_flight_guard_is_still_released_on_failure():
    import inspect

    source = inspect.getsource(api_server.DashboardState._refresh_oi_finder_in_background)
    # Both discards must stay inside `finally`, or a raising build wedges the
    # symbol out of every future refresh.
    assert source.count("finally:") >= 2
    assert source.count("oi_finder_background_refreshes.discard") == 2
