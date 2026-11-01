"""2026-10-02 18:20 ET py-spy: /api/premarket-scanner held 33% of the
api_server GIL after hours - one uncached pass cost 6.4s, cached only 4s, while
nothing in the 06:00-09:30 table can change after the 10:00 refresh cut-off."""
from datetime import datetime
from zoneinfo import ZoneInfo

import api_server

ET = ZoneInfo("America/New_York")


def test_cache_is_short_inside_the_scanner_window_and_long_outside() -> None:
    seconds = api_server._premarket_scanner_cache_seconds
    assert seconds(datetime(2026, 10, 2, 7, 15, tzinfo=ET)) == 4.0      # Fri premarket
    assert seconds(datetime(2026, 10, 2, 9, 59, tzinfo=ET)) == 4.0
    assert seconds(datetime(2026, 10, 2, 10, 0, tzinfo=ET)) == 300.0    # table is final
    assert seconds(datetime(2026, 10, 2, 18, 20, tzinfo=ET)) == 300.0   # the measured case
    assert seconds(datetime(2026, 10, 3, 7, 15, tzinfo=ET)) == 300.0    # Saturday


def test_after_hours_polls_reuse_one_build(monkeypatch) -> None:
    state = api_server.DashboardState.__new__(api_server.DashboardState)
    builds = []
    monkeypatch.setattr(
        api_server.DashboardState,
        "_premarket_scanner_payload_locked",
        lambda self: builds.append(1) or {"rows": [], "n": len(builds)},
    )
    monkeypatch.setattr(api_server, "_premarket_scanner_cache_seconds", lambda now=None: 300.0)
    clock = [1000.0]
    monkeypatch.setattr(api_server.time, "monotonic", lambda: clock[0])
    first = state.premarket_scanner_payload()
    clock[0] += 60.0  # well past the old 4s window
    second = state.premarket_scanner_payload()
    assert builds == [1]
    assert second is first
    clock[0] += 300.0
    state.premarket_scanner_payload()
    assert builds == [1, 1]
