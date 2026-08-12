from __future__ import annotations

import threading
from unittest.mock import patch

from api_server import DashboardState


def _payload(signal_contract=None) -> dict:
    newest = 1_786_377_600
    return {
        "symbol": "UBER",
        "bars": [{"time": newest, "close": 77.45}],
        "studyBars": [
            {"time": newest - ((29 - index) * 86_400), "close": 77.45}
            for index in range(30)
        ],
        "dailyBars": [
            {"time": newest - ((29 - index) * 86_400), "close": 76.0}
            for index in range(30)
        ],
        "fourHourCoverage": {"requestedYears": 20},
        "ganeshHigherTimeframeSignals": signal_contract,
        "historyLoading": False,
    }


def test_missing_ganesh_contract_is_not_a_ready_chart_cache() -> None:
    assert DashboardState._chart_payload_has_ready_ganesh_signals(_payload()) is False


def test_one_session_seed_is_not_complete_multi_timeframe_history() -> None:
    payload = _payload()
    payload["studyBars"] = payload["studyBars"][-8:]
    payload["dailyBars"] = payload["dailyBars"][-1:]

    assert DashboardState._chart_payload_has_multi_timeframe_depth(payload) is False
    assert DashboardState._chart_payload_has_multi_timeframe_depth(_payload()) is True


def test_initial_chart_payload_defers_the_heavy_signal_tape() -> None:
    state = DashboardState.__new__(DashboardState)
    payload = _payload({
        "historyReady": True,
        "signals": [{"key": f"signal-{index}"} for index in range(50)],
    })
    payload["bars"] = [{"time": index + 1, "close": 77.45} for index in range(1_000)]

    slim = state._slim_initial_chart_payload(payload)

    assert len(slim["bars"]) == state.OI_CHART_INITIAL_BAR_COUNT
    assert "studyBars" not in slim
    assert "ganeshHigherTimeframeSignals" not in slim
    assert slim["initialSlim"] is True


def test_current_complete_cache_replays_only_the_missing_ganesh_tape() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {
        "UBER": {"payload": _payload(), "history_ready": True},
    }
    rebuilt = {
        "schemaVersion": "ganesh-higher-timeframe-signals-v17",
        "mode": "tos_calendar_and_session_secondary_replay",
        "sourceAggregationMinutes": 240,
        "historyReady": True,
        "signals": [{"key": "ganesh920-D-CALL-test"}],
    }

    with (
        patch("api_server.time.time", return_value=1_786_377_601),
        patch.object(state, "_ganesh_signal_payload_for_chart", return_value=rebuilt) as replay,
    ):
        upgraded = state._upgrade_cached_ganesh_signal_tape("UBER")

    assert upgraded is not None
    assert upgraded["ganeshHigherTimeframeSignals"] == rebuilt
    assert upgraded["historyLoading"] is False
    replay.assert_called_once()


def test_ready_stale_chart_cache_refreshes_only_the_recent_tail() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_interactive_until = 0.0
    state.oi_finder_chart_cache = {
        "UBER": {
            "cached_at": 1.0,
            "payload": _payload({
                "schemaVersion": "ganesh-higher-timeframe-signals-v17",
                "mode": "tos_calendar_and_session_secondary_replay",
                "sourceAggregationMinutes": 240,
                "historyReady": True,
                "signals": [],
            }),
            "history_ready": True,
        },
    }

    with (
        patch("api_server.time.monotonic", return_value=40.0),
        patch.object(state, "_chart_payload_has_ready_ganesh_signals", return_value=True),
        patch.object(state, "_start_oi_finder_chart_refresh") as refresh,
    ):
        payload = state.oi_finder_chart_payload("UBER")

    assert payload["historyLoading"] is False
    refresh.assert_called_once_with("UBER", full_history=False)


def test_prefetch_defers_an_incomplete_cache_deep_replay_until_selection() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_interactive_until = 0.0
    state.oi_finder_chart_cache = {
        "UBER": {
            "cached_at": 10.0,
            "payload": _payload(),
            "history_ready": False,
        },
    }

    with (
        patch("api_server.time.monotonic", return_value=10.5),
        patch.object(state, "_start_oi_finder_chart_refresh") as refresh,
    ):
        preview = state.oi_finder_chart_payload("UBER", prefetch=True)
        selected = state.oi_finder_chart_payload("UBER")

    assert preview["historyLoading"] is True
    assert selected["historyLoading"] is True
    refresh.assert_called_once_with("UBER", full_history=True)


def test_cold_chart_paints_before_deep_history_replay() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_interactive_until = 0.0
    state.oi_finder_chart_cache = {}

    with (
        patch.object(state, "_load_oi_finder_chart_disk_payload", return_value=None),
        patch.object(state, "_build_oi_finder_chart_payload", return_value={
            **_payload(),
            "historyLoading": True,
        }),
        patch.object(state, "_start_oi_finder_chart_refresh") as refresh,
    ):
        payload = state.oi_finder_chart_payload("UBER", initial_paint=True)

    assert payload["initialSlim"] is True
    refresh.assert_called_once_with("UBER", full_history=False)
