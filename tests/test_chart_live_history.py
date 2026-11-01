from unittest.mock import Mock
import threading
import time

from chart_live_history import with_recorded_chart_history


NOW = 1793371805  # 10:50:05 ET
MINUTE = NOW - 5


def bar(stamp, price=100, volume=10):
    return dict(time=stamp, open=price, high=price + 1, low=price - 1,
                close=price, volume=volume)


def test_recorded_minutes_fill_the_gap_without_inventing_missing_minutes():
    old = bar(MINUTE - 3600)
    original = {"symbol": "TSLA", "bars": [old], "mtfSignals": [{"id": "held"}]}
    result = with_recorded_chart_history(original,
        [bar(MINUTE - 180), bar(MINUTE - 60), bar(MINUTE)], NOW)
    assert [b["time"] for b in result["bars"]] == [old["time"], MINUTE - 180, MINUTE - 60, MINUTE]
    assert MINUTE - 120 not in [b["time"] for b in result["bars"]]
    assert original["bars"] == [old]
    assert result["mtfSignals"] is original["mtfSignals"]
    assert result["historyBarsThrough"] == MINUTE


def test_rest_closed_minutes_win_and_current_minute_retains_extremes():
    closed = bar(MINUTE - 60, 110)
    forming = bar(MINUTE, 100, 50)
    original = {"bars": [closed, forming]}
    result = with_recorded_chart_history(original,
        [bar(MINUTE - 60, 90), bar(MINUTE, 105, 20)], NOW)
    assert result["bars"][0] is closed
    assert result["bars"][1] == dict(time=MINUTE, open=100, high=106, low=99, close=105, volume=50)


def test_missing_quote_fields_future_and_malformed_records_do_not_create_bars():
    original = {"bars": [bar(MINUTE - 60)]}
    invalid = [dict(time=MINUTE, close=120), bar(MINUTE + 60),
               bar(MINUTE - 3 * 86400), bar(MINUTE, volume=-1),
               {**bar(MINUTE), "high": float("nan")},
               {**bar(MINUTE), "high": 90}, bar(MINUTE + 1)]
    assert with_recorded_chart_history(original, invalid, NOW) is original


def test_empty_cache_can_paint_real_recorded_candles_while_rest_warms():
    payload = {"symbol": "AAPL", "bars": [], "warming": True, "historyLoading": True}
    result = with_recorded_chart_history(payload, [bar(MINUTE - 60), bar(MINUTE)], NOW)
    assert len(result["bars"]) == 2
    assert result["warming"] and result["historyLoading"]
    assert payload["bars"] == []


def test_duplicates_are_deduped_and_unchanged_closed_history_keeps_identity():
    closed = bar(MINUTE - 60)
    original = {"bars": [closed]}
    assert with_recorded_chart_history(original, [closed, closed], NOW) is original
    result = with_recorded_chart_history(original, [bar(MINUTE), bar(MINUTE)], NOW)
    assert len(result["bars"]) == 2


def test_state_reads_only_requested_symbol_and_tolerates_missing_stream():
    from api_server import DashboardState
    state = DashboardState.__new__(DashboardState)
    payload = {"symbol": "AAPL", "bars": []}
    assert state._with_recorded_chart_history("AAPL", payload) is payload
    state.chart_market_stream = Mock()
    state.chart_market_stream.chart_history.return_value = []
    assert state._with_recorded_chart_history("TSLA", payload) is payload
    call = state.chart_market_stream.chart_history.call_args
    assert call.args == ("TSLA",)
    assert abs(call.kwargs["since"] - (time.time() - 2 * 86400)) < 1
    state.chart_market_stream.chart_history.side_effect = RuntimeError("stream restarting")
    assert state._with_recorded_chart_history("TSLA", payload) is payload


def test_cold_initial_request_uses_recorded_history_and_caps_first_paint(monkeypatch):
    monkeypatch.setattr(time, "time", lambda: NOW)
    from api_server import DashboardState
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.Lock()
    state.oi_finder_chart_cache = {}
    state.touch_oi_finder_interactive_window = Mock()
    state._chart_mark = Mock()
    state._note_chart_symbol_requested = Mock()
    state._load_oi_finder_chart_disk_payload = Mock(return_value=None)
    state._start_oi_finder_chart_refresh = Mock()
    state.chart_market_stream = Mock()
    state.chart_market_stream.chart_history.return_value = [bar(MINUTE - i * 60) for i in range(1500)]
    result = state._oi_finder_chart_payload_impl("ZZBRIDGE", initial_paint=True)
    assert len(result["bars"]) == state.OI_CHART_INITIAL_BAR_COUNT
    assert result["bars"][-1]["time"] == MINUTE
    assert result["historyLoading"] and result["warming"] and result["initialSlim"]
    state._start_oi_finder_chart_refresh.assert_called_once_with("ZZBRIDGE", full_history=True)
    call = state.chart_market_stream.chart_history.call_args
    assert call.args == ("ZZBRIDGE",)
    assert abs(call.kwargs["since"] - (time.time() - 2 * 86400)) < 1


def test_background_tail_burst_is_bounded_but_requested_chart_can_enter():
    from api_server import DashboardState
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.Lock()
    state._index_tape_cannot_advance = Mock(return_value=False)
    submitted = []
    state.oi_finder_chart_refresh_pool = Mock()
    state.oi_finder_chart_refresh_pool.submit.side_effect = lambda *args: submitted.append(args)
    state._chart_refresh_queue_depth = lambda full: len(submitted)
    state._chart_symbol_is_interactive = lambda target: target == "TSLA"
    for i in range(100):
        state._start_oi_finder_chart_refresh(f"ZZWARM{i}", False)
    assert len(submitted) == 2
    assert state._chart_background_tail_deferrals == 98
    assert len(state._chart_recency_refreshes()) == 2
    state._start_oi_finder_chart_refresh("TSLA", False)
    assert len(submitted) == 3
    state._start_oi_finder_chart_refresh("TSLA", False)
    assert len(submitted) == 3  # the existing per-symbol guard still dedupes


def test_unknown_queue_depth_and_full_rebuild_keep_existing_completion_paths():
    from api_server import DashboardState
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.Lock()
    state.oi_finder_chart_refreshes = {}
    state._index_tape_cannot_advance = Mock(return_value=False)
    state._chart_refresh_queue_depth = Mock(return_value=-1)
    state._chart_symbol_is_interactive = Mock(return_value=False)
    state.oi_finder_chart_refresh_pool = Mock()
    state.oi_finder_chart_paint_pool = Mock()
    state._start_oi_finder_chart_refresh("ZZTAIL", False)
    state.oi_finder_chart_refresh_pool.submit.assert_called_once()
    state._chart_refresh_queue_depth.return_value = 100
    state._start_oi_finder_chart_refresh("ZZFULL", True)
    state.oi_finder_chart_paint_pool.submit.assert_called_once()
