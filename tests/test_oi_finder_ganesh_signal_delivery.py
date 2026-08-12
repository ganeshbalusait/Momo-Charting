from __future__ import annotations

from datetime import datetime
import json
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd

from api_server import (
    DashboardState,
    OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS,
    OI_FINDER_CHART_REFRESH_SECONDS,
    OI_FINDER_CHART_WARM_LIMIT,
    _mag7_tos_all_of_gate,
    _merge_intraday_chart_history,
    _oi_finder_chart_signal_tape_date_is_stale,
    _prepare_mag7_tos_gate_frame,
    _stream_chart_history_frame,
)


def _bars(start: str, periods: int, frequency: str) -> pd.DataFrame:
    timestamps = pd.date_range(start, periods=periods, freq=frequency, tz="America/New_York")
    closes = [100.0 + index * 0.1 for index in range(periods)]
    return pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [value + 0.2 for value in closes],
            "low": [value - 0.2 for value in closes],
            "close": closes,
            "volume": [100] * periods,
        }
    )


def test_stream_history_fills_active_overnight_gap_and_replaces_overlap() -> None:
    rest = _bars("2026-08-03 07:00", 2, "1min")
    stream = _stream_chart_history_frame([
        {
            "time": int(pd.Timestamp("2026-08-02 21:00", tz="America/New_York").timestamp()),
            "open": 560.0,
            "high": 561.0,
            "low": 559.0,
            "close": 560.5,
            "volume": 10,
        },
        {
            "time": int(pd.Timestamp("2026-08-03 07:00", tz="America/New_York").timestamp()),
            "open": 600.0,
            "high": 601.0,
            "low": 599.0,
            "close": 600.5,
            "volume": 20,
        },
    ])

    merged = _merge_intraday_chart_history(rest, stream)

    assert [stamp.strftime("%a %H:%M") for stamp in merged["timestamp"]] == [
        "Sun 21:00",
        "Mon 07:00",
        "Mon 07:01",
    ]
    assert float(merged.iloc[1]["close"]) == 600.5


def test_successful_refresh_preserves_missing_overnight_candles_and_rebuilds_signals() -> None:
    friday_13 = int(pd.Timestamp("2026-07-31 13:00", tz="America/New_York").timestamp())
    sunday_21 = int(pd.Timestamp("2026-08-02 21:00", tz="America/New_York").timestamp())
    monday_07 = int(pd.Timestamp("2026-08-03 07:00", tz="America/New_York").timestamp())
    existing = {
        "symbol": "AMZN",
        "bars": [
            {"time": friday_13, "open": 270, "high": 271, "low": 269, "close": 270.5, "volume": 5},
            {"time": sunday_21, "open": 280, "high": 281, "low": 279, "close": 280.5, "volume": 10},
            {"time": monday_07, "open": 284, "high": 285, "low": 283, "close": 284.5, "volume": 20},
        ],
        "studyBars": [
            {"time": friday_13, "open": 270, "high": 271, "low": 269, "close": 270.5, "volume": 5},
            {"time": sunday_21, "open": 280, "high": 281, "low": 279, "close": 280.5, "volume": 10},
            {"time": monday_07, "open": 284, "high": 285, "low": 283, "close": 284.5, "volume": 20},
        ],
        "dailyBars": [],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"key": "old"}]},
        "historyLoading": False,
    }
    recent = {
        "symbol": "AMZN",
        "bars": [
            {"time": friday_13, "open": 270, "high": 271, "low": 269, "close": 270.5, "volume": 5},
            {"time": monday_07, "open": 284, "high": 286, "low": 283, "close": 285.5, "volume": 30},
        ],
        "studyBars": [
            {"time": friday_13, "open": 270, "high": 271, "low": 269, "close": 270.5, "volume": 5},
            {"time": monday_07, "open": 284, "high": 286, "low": 283, "close": 285.5, "volume": 30},
        ],
        "dailyBars": [],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"key": "fresh"}]},
        "historyLoading": False,
    }
    rebuilt = {"historyReady": True, "signals": [{"key": "rebuilt"}]}

    with patch(
        "api_server.build_ganesh_higher_timeframe_signal_payload",
        return_value=rebuilt,
    ) as build_tape:
        merged = DashboardState._merge_oi_finder_chart_snapshot(
            existing,
            recent,
            history_ready=True,
            preserve_existing_studies=False,
        )

    assert [bar["time"] for bar in merged["bars"]] == [friday_13, sunday_21, monday_07]
    assert merged["bars"][-1]["close"] == 285.5
    assert merged["ganeshHigherTimeframeSignals"] == rebuilt
    build_tape.assert_called_once()


def test_oi_finder_chart_delivers_the_versioned_backend_ganesh_tape() -> None:
    one_minute = _bars("2026-07-31 09:30", 30, "1min")
    five_minute = _bars("2026-07-01 04:00", 6001, "5min")
    daily = _bars("2020-01-02 16:00", 100, "1D")

    class FakeSchwabClient:
        configured = True

        def get_chart_bars(self, _symbol: str, *, timeframe: str, days_back: int) -> pd.DataFrame:
            assert days_back in {7, 60}
            return one_minute if timeframe == "1Min" else five_minute

        def get_daily_bars(self, symbols: list[str], *, lookback_days: int) -> dict[str, pd.DataFrame]:
            assert symbols == ["AMZN"]
            assert lookback_days == 3650
            return {"AMZN": daily}

    backend_tape = {
        "schemaVersion": "ganesh-higher-timeframe-signals-v1",
        "mode": "tos_canonical_4h_d_to_m",
        "sourceAggregationMinutes": 240,
        "historyReady": True,
        "signals": [
            {
                "key": "ganeshMacd-D-CALL-1785520800",
                "family": "ganeshMacd",
                "timeframe": "D",
                "direction": "CALL",
                "label": "MACD-D",
                "time": 1785520800,
                "atrMultiplier": 0.0,
                "stateSnapshot": True,
            }
        ],
    }
    state = DashboardState.__new__(DashboardState)

    with (
        patch("api_server.SchwabClient", return_value=FakeSchwabClient()),
        patch(
            "api_server.build_ganesh_higher_timeframe_signal_payload",
            return_value=backend_tape,
        ) as build_signal_payload,
    ):
        payload = state._build_oi_finder_chart_payload("AMZN")

    assert payload["symbol"] == "AMZN"
    assert payload["historyLoading"] is False
    assert payload["ganeshHigherTimeframeSignals"] == backend_tape
    assert payload["ganeshHigherTimeframeSignals"]["signals"][0]["label"] == "MACD-D"
    study_arg, live_arg, daily_arg = build_signal_payload.call_args.args
    assert len(study_arg) == 6000
    assert len(live_arg) == 30
    assert len(daily_arg) == 100


def test_invalid_ticker_keeps_the_backend_signal_contract() -> None:
    state = DashboardState.__new__(DashboardState)

    payload = state._build_oi_finder_chart_payload("not valid!")

    contract = payload["ganeshHigherTimeframeSignals"]
    assert contract["sourceAggregationMinutes"] == 240
    assert contract["historyReady"] is False
    assert contract["signals"] == []


def test_fast_start_returns_recent_candles_without_running_study_builders() -> None:
    one_minute = _bars("2026-07-31 09:30", 30, "1min")

    class FakeSchwabClient:
        configured = True

        def get_chart_bars(self, _symbol: str, *, timeframe: str, days_back: int) -> pd.DataFrame:
            assert timeframe == "1Min"
            assert days_back == 3
            return one_minute

    state = DashboardState.__new__(DashboardState)
    with (
        patch("api_server.SchwabClient", return_value=FakeSchwabClient()),
        patch("api_server._tos_mtf_ema_signal_payload") as build_mtf,
        patch("api_server._tos_watchlist_mtf_signal_payload") as build_watchlist,
    ):
        payload = state._build_oi_finder_chart_payload("AAPL", fast_start=True)

    assert payload["bars"]
    assert payload["studyBars"] == payload["bars"]
    assert payload["historyLoading"] is True
    assert payload["mtfSignals"] == []
    build_mtf.assert_not_called()
    build_watchlist.assert_not_called()


def test_fast_start_uses_only_the_schwab_tos_tape() -> None:
    premarket = _bars("2026-08-03 07:00", 2, "1min")

    class FakeSchwabClient:
        configured = True

        def get_chart_bars(self, _symbol: str, *, timeframe: str, days_back: int) -> pd.DataFrame:
            assert timeframe == "1Min"
            assert days_back == 3
            return premarket

    state = DashboardState.__new__(DashboardState)
    with (
        patch("api_server.SchwabClient", return_value=FakeSchwabClient()),
        patch.object(state, "_oi_finder_overnight_history_frame") as overnight_history,
        patch("api_server._tos_mtf_ema_signal_payload") as build_mtf,
        patch("api_server._tos_watchlist_mtf_signal_payload") as build_watchlist,
    ):
        payload = state._build_oi_finder_chart_payload("AMZN", fast_start=True)

    assert [
        pd.Timestamp(bar["time"], unit="s", tz="UTC")
        .tz_convert("America/New_York")
        .strftime("%a %H:%M")
        for bar in payload["bars"]
    ] == ["Mon 07:00", "Mon 07:01"]
    assert payload["historyLoading"] is True
    overnight_history.assert_not_called()
    build_mtf.assert_not_called()
    build_watchlist.assert_not_called()


def test_recent_refresh_preserves_completed_study_tape() -> None:
    existing = {
        "symbol": "AAPL",
        "bars": [{"time": 1}],
        "studyBars": [{"time": 10}],
        "dailyBars": [{"time": 20}],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"key": "saved"}]},
        "signalTapeUpdatedAt": "2026-08-03T09:00:00-04:00",
        "signalTapeSourceLastBarTime": 10,
        "signalTapeMarketDate": "2026-08-03",
        "signalTapeProvisional": True,
        "mag7PremarketChartSignalTape": {"marketDate": "2026-08-03", "signals": [{"key": "pre"}]},
        "mtfSignals": [{"key": "mtf"}],
        "historyLoading": False,
    }
    recent = {
        "symbol": "AAPL",
        "bars": [{"time": 2}],
        "studyBars": [{"time": 2}],
        "dailyBars": [],
        "ganeshHigherTimeframeSignals": {"historyReady": False, "signals": []},
        "mtfSignals": [],
        "historyLoading": True,
    }

    merged = DashboardState._merge_oi_finder_chart_snapshot(existing, recent, history_ready=True)

    assert merged["bars"] == [{"time": 1}, {"time": 2}]
    assert merged["studyBars"] == [{"time": 2}, {"time": 10}]
    assert merged["dailyBars"] == [{"time": 20}]
    assert merged["ganeshHigherTimeframeSignals"]["signals"] == [{"key": "saved"}]
    assert merged["signalTapeUpdatedAt"] == "2026-08-03T09:00:00-04:00"
    assert merged["signalTapeSourceLastBarTime"] == 10
    assert merged["signalTapeMarketDate"] == "2026-08-03"
    assert merged["signalTapeProvisional"] is True
    assert merged["mag7PremarketChartSignalTape"]["signals"] == [{"key": "pre"}]
    assert merged["mtfSignals"] == [{"key": "mtf"}]
    assert merged["historyLoading"] is False


def test_recent_refresh_replays_completed_ganesh_tape_when_live_source_advances() -> None:
    source_time = int(pd.Timestamp("2026-08-05 10:00", tz="America/New_York").timestamp())
    live_time = int(pd.Timestamp("2026-08-05 10:05", tz="America/New_York").timestamp())
    existing = {
        "symbol": "INTC",
        "bars": [{"time": source_time, "close": 101.0}],
        "studyBars": [{"time": source_time, "close": 101.0}],
        "dailyBars": [{"time": source_time, "close": 101.0}],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"key": "stale"}]},
        "signalTapeUpdatedAt": "2026-08-05T10:00:00-04:00",
        "signalTapeSourceLastBarTime": source_time,
        "signalTapeMarketDate": "2026-08-05",
        "signalTapeProvisional": True,
        "historyLoading": False,
    }
    recent = {
        "symbol": "INTC",
        "bars": [{"time": live_time, "close": 102.0}],
        "studyBars": [{"time": live_time, "close": 102.0}],
        "dailyBars": [],
        "ganeshHigherTimeframeSignals": {"historyReady": False, "signals": []},
        "historyLoading": True,
    }
    rebuilt = {"historyReady": True, "signals": [{"key": "current"}]}

    with patch(
        "api_server.build_ganesh_higher_timeframe_signal_payload",
        return_value=rebuilt,
    ) as build_tape:
        merged = DashboardState._merge_oi_finder_chart_snapshot(
            existing,
            recent,
            history_ready=True,
        )

    build_tape.assert_called_once()
    assert merged["ganeshHigherTimeframeSignals"] == rebuilt
    assert merged["signalTapeSourceLastBarTime"] == live_time
    assert merged["signalTapeMarketDate"] == "2026-08-05"
    assert merged["signalTapeProvisional"] is True


def test_full_build_keeps_incomplete_ganesh_history_loading_and_retries_daily_seed() -> None:
    one_minute = _bars("2026-07-31 09:30", 30, "1min")
    five_minute = _bars("2026-07-01 04:00", 240, "5min")
    short_daily = _bars("2026-06-01 16:00", 5, "1D")
    calls = {"fiveMinute": 0, "daily": 0}

    class FakeSchwabClient:
        configured = True

        def get_chart_bars(self, _symbol: str, *, timeframe: str, days_back: int) -> pd.DataFrame:
            if timeframe == "1Min":
                assert days_back == 7
                return one_minute
            assert timeframe == "5Min"
            assert days_back == 60
            calls["fiveMinute"] += 1
            return five_minute

        def get_daily_bars(self, symbols: list[str], *, lookback_days: int) -> dict[str, pd.DataFrame]:
            assert symbols == ["AAPL"]
            assert lookback_days == 3650
            calls["daily"] += 1
            return {"AAPL": short_daily}

    incomplete_tape = {
        "schemaVersion": "ganesh-higher-timeframe-signals-v3",
        "mode": "tos_literal_secondary_crosses_d_to_m",
        "sourceAggregationMinutes": 240,
        "historyReady": False,
        "signals": [],
    }
    empty_mtf = {"signals": [], "states": [], "liveSignalContexts": [], "mode": "test"}
    state = DashboardState.__new__(DashboardState)

    with (
        patch("api_server.SchwabClient", return_value=FakeSchwabClient()),
        patch("api_server._tos_mtf_ema_signal_payload", return_value=empty_mtf),
        patch("api_server._tos_watchlist_mtf_signal_payload", return_value={"states": []}),
        patch("api_server.build_ganesh_higher_timeframe_signal_payload", return_value=incomplete_tape),
    ):
        first = state._build_oi_finder_chart_payload("AAPL")
        second = state._build_oi_finder_chart_payload("AAPL")

    assert first["historyLoading"] is True
    assert second["historyLoading"] is True
    assert calls == {
        "fiveMinute": 1,
        "daily": 2,
    }, "fresh five-minute history should be reused while an incomplete long daily seed is retried"


def test_two_symbol_cold_caches_promote_only_their_own_full_ganesh_tape() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    refresh_requests: list[tuple[str, bool]] = []

    def chart_payload(symbol: str, fast_start: bool = False) -> dict:
        target = symbol.upper()
        ready = not fast_start
        return {
            "symbol": target,
            "timeframe": "1Min",
            "source": "Schwab/TOS API",
            "live": True,
            "bars": [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
            "studyBars": [],
            "dailyBars": [],
            "ganeshHigherTimeframeSignals": {
                "historyReady": ready,
                "signals": [] if not ready else [{"key": f"{target}-MACD-D", "symbol": target}],
            },
            "historyLoading": not ready,
            "error": "",
        }

    with (
        patch.object(state, "_build_oi_finder_chart_payload", side_effect=chart_payload),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refresh_requests.append((symbol, full_history)),
        ),
    ):
        cold_aapl = state.oi_finder_chart_payload("AAPL")
        cold_amzn = state.oi_finder_chart_payload("AMZN")
        state._refresh_oi_finder_chart_payload("AAPL", full_history=True)
        state._refresh_oi_finder_chart_payload("AMZN", full_history=True)
        full_aapl = state.oi_finder_chart_payload("AAPL")
        full_amzn = state.oi_finder_chart_payload("AMZN")

    assert cold_aapl["historyLoading"] is True
    assert cold_amzn["historyLoading"] is True
    assert refresh_requests == [("AAPL", False), ("AMZN", False)]
    assert full_aapl["historyLoading"] is False
    assert full_amzn["historyLoading"] is False
    assert full_aapl["ganeshHigherTimeframeSignals"]["signals"] == [
        {"key": "AAPL-MACD-D", "symbol": "AAPL"}
    ]
    assert full_amzn["ganeshHigherTimeframeSignals"]["signals"] == [
        {"key": "AMZN-MACD-D", "symbol": "AMZN"}
    ]


def test_incomplete_cached_ticker_starts_full_history_refresh_immediately() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {
        "MSFT": {
            "cached_at": 100.0,
            "history_ready": False,
            "payload": {
                "symbol": "MSFT",
                "bars": [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
                "historyLoading": True,
            },
        }
    }
    refresh_requests: list[tuple[str, bool]] = []

    with (
        patch("api_server.time.monotonic", return_value=100.5),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refresh_requests.append((symbol, full_history)),
        ),
    ):
        payload = state.oi_finder_chart_payload("MSFT")

    assert payload["historyLoading"] is True
    assert payload["cacheAgeSeconds"] == 0.5
    assert refresh_requests == [("MSFT", True)]


def test_chart_prefetch_does_not_queue_an_indicator_history_replay() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {
        "NVDA": {
            "cached_at": 100.0,
            "history_ready": False,
            "payload": {
                "symbol": "NVDA",
                "bars": [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
                "historyLoading": True,
            },
        }
    }
    refresh_requests: list[tuple[str, bool]] = []

    with (
        patch("api_server.time.monotonic", return_value=100.5),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refresh_requests.append((symbol, full_history)),
        ),
    ):
        preview = state.oi_finder_chart_payload("NVDA", prefetch=True)
        selected = state.oi_finder_chart_payload("NVDA")

    assert preview["historyLoading"] is True
    assert selected["historyLoading"] is True
    assert refresh_requests == [("NVDA", True)]


def test_ready_chart_cache_reconciles_only_after_the_rest_refresh_interval() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {
        "AAPL": {
            "cached_at": 100.0,
            "history_ready": True,
            "payload": {
                "symbol": "AAPL",
                "bars": [{"time": 1, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}],
                "historyLoading": False,
            },
        }
    }
    refresh_requests: list[tuple[str, bool]] = []

    with patch.object(
        state,
        "_start_oi_finder_chart_refresh",
        side_effect=lambda symbol, full_history: refresh_requests.append((symbol, full_history)),
    ):
        with patch(
            "api_server.time.monotonic",
            return_value=100.0 + OI_FINDER_CHART_REFRESH_SECONDS - 0.01,
        ):
            fresh = state.oi_finder_chart_payload("AAPL")
        with patch(
            "api_server.time.monotonic",
            return_value=100.0 + OI_FINDER_CHART_REFRESH_SECONDS,
        ):
            stale = state.oi_finder_chart_payload("AAPL")

    assert fresh["historyLoading"] is False
    assert stale["historyLoading"] is False
    assert refresh_requests == [("AAPL", False)]


def test_explicit_chart_refresh_returns_ready_cache_before_slow_rest_work() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {
        "AVGO": {
            "cached_at": 100.0,
            "history_ready": True,
            "payload": {
                "symbol": "AVGO",
                "bars": [
                    {"time": 1, "open": 390, "high": 392, "low": 388, "close": 391, "volume": 1}
                ],
                "historyLoading": False,
            },
        }
    }
    refresh_requests: list[tuple[str, bool]] = []

    with (
        patch("api_server.time.monotonic", return_value=100.5),
        patch.object(
            state,
            "_build_oi_finder_chart_payload",
            side_effect=AssertionError("refresh request must not block on REST"),
        ),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refresh_requests.append((symbol, full_history)),
        ),
    ):
        payload = state.oi_finder_chart_payload("AVGO", refresh=True)

    assert payload["bars"][0]["close"] == 391
    assert payload["historyLoading"] is False
    assert payload["refreshing"] is True
    assert refresh_requests == [("AVGO", False)]


def test_quick_ticker_warmup_subscribes_without_fetching_chart_history() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    watched: list[str] = []

    class FakeStream:
        def watch(self, symbol: str) -> None:
            watched.append(symbol)

    state.live_market_stream = FakeStream()
    with (
        patch("api_server.time.sleep"),
        patch("api_server.current_user_provider_context", return_value=None),
        patch.object(state, "_mag7_option_underlyings", return_value=[]),
        patch.object(state, "_oi_finder_chart_warm_symbols", return_value=["MSFT"]),
        patch.object(state, "_provider_cache_key", return_value="user-7:MSFT"),
        patch.object(state, "_build_oi_finder_chart_payload") as build_payload,
    ):
        state._oi_finder_chart_warmup_loop()

    build_payload.assert_not_called()
    assert watched == ["MSFT"]
    assert state.oi_finder_chart_cache == {}
    assert state.oi_finder_chart_warmup_status == "Ready"


def test_initial_chart_payload_is_small_but_keeps_daily_timeframes_paintable() -> None:
    payload = {
        "symbol": "MSFT",
        "bars": [{"time": index} for index in range(2_000)],
        "dailyBars": [{"time": index} for index in range(500)],
        "studyBars": [{"time": index} for index in range(2_000)],
        "mtfSignals": [{"time": 1}],
        "historyLoading": False,
    }

    slim = DashboardState._slim_initial_chart_payload(payload)

    assert len(slim["bars"]) == 900
    assert len(slim["dailyBars"]) == 320
    assert "studyBars" not in slim
    assert "mtfSignals" not in slim
    assert slim["initialSlim"] is True
    assert slim["historyLoading"] is True


def test_chart_warm_symbols_put_the_saved_mag7_before_general_watchlists() -> None:
    state = DashboardState.__new__(DashboardState)
    state.option_watchlist = ["SPY", "AMD"]
    state.scanner = SimpleNamespace(settings=SimpleNamespace(default_universe=["NFLX", "SHOP"]))

    with patch.object(state, "_mag7_option_underlyings", return_value=["NVDA", "MSFT", "AAPL"]):
        symbols = state._oi_finder_chart_warm_symbols()

    assert symbols[:3] == ["NVDA", "MSFT", "AAPL"]
    assert len(symbols) == len(set(symbols))
    assert "AMD" in symbols


def test_chart_cache_eviction_keeps_mag7_warm_during_broad_watchlist_searches() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_cache = {
        "user:AAPL": {
            "cached_at": 1.0,
            "payload": {"symbol": "AAPL"},
            "history_ready": True,
        },
        **{
            f"user:T{index}": {
                "cached_at": float(index + 2),
                "payload": {"symbol": f"T{index}"},
                "history_ready": True,
            }
            for index in range(OI_FINDER_CHART_WARM_LIMIT)
        },
    }

    with patch.object(state, "_mag7_option_underlyings", return_value=["AAPL"]):
        state._trim_oi_finder_chart_cache()

    assert len(state.oi_finder_chart_cache) == OI_FINDER_CHART_WARM_LIMIT
    assert "user:AAPL" in state.oi_finder_chart_cache
    assert "user:T0" not in state.oi_finder_chart_cache


def test_complete_mag7_indicator_payload_round_trips_through_disk_cache(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    payload = {
        "symbol": "AMZN",
        "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "historyLoading": False,
    }

    with patch.object(state, "_mag7_option_underlyings", return_value=["AMZN"]):
        state._save_oi_finder_chart_disk_payload("AMZN", payload)
        loaded = state._load_oi_finder_chart_disk_payload("AMZN")

    assert loaded is not None
    assert loaded["symbol"] == "AMZN"
    assert loaded["studyBars"] == payload["studyBars"]
    assert loaded["diskCached"] is True


def test_non_mag7_indicator_payload_round_trips_through_disk_cache(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    payload = {
        "symbol": "QCOM",
        "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"label": "MACD-2D"}]},
        "historyLoading": False,
    }

    # QCOM intentionally is not part of the priority MAG7 list. Its complete
    # tape must still survive a service restart and paint before refresh.
    with patch.object(state, "_mag7_option_underlyings", return_value=["NVDA"]):
        state._save_oi_finder_chart_disk_payload("QCOM", payload)
        loaded = state._load_oi_finder_chart_disk_payload("QCOM")

    assert loaded is not None
    assert loaded["symbol"] == "QCOM"
    assert loaded["ganeshHigherTimeframeSignals"] == payload["ganeshHigherTimeframeSignals"]
    assert loaded["diskCached"] is True


def test_prior_market_day_disk_tape_is_promoted_for_every_ticker(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    payload = {
        "symbol": "SPY",
        "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "ganeshHigherTimeframeSignals": {"historyReady": True, "signals": [{"label": "MACD-2D"}]},
        "signalTapeMarketDate": "2026-08-03",
        "historyLoading": False,
    }
    refreshes: list[tuple[str, bool]] = []

    with (
        patch.object(state, "_provider_cache_key", return_value="user:SPY"),
        patch(
            "api_server._oi_finder_chart_signal_tape_date_is_stale",
            return_value=True,
        ),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refreshes.append((symbol, full_history)),
        ),
        patch.object(state, "_build_oi_finder_chart_payload") as build_payload,
    ):
        state._save_oi_finder_chart_disk_payload("SPY", payload)
        loaded = state.oi_finder_chart_payload("SPY")

    build_payload.assert_not_called()
    assert loaded["diskCached"] is True
    assert loaded["signalTapeDateStale"] is True
    assert loaded["historyLoading"] is True
    assert refreshes == [("SPY", True)]


def test_signal_tape_date_staleness_skips_weekends() -> None:
    payload = {"signalTapeMarketDate": "2026-08-03"}

    assert _oi_finder_chart_signal_tape_date_is_stale(
        payload,
        as_of=datetime.fromisoformat("2026-08-04T10:00:00-04:00"),
    ) is True
    assert _oi_finder_chart_signal_tape_date_is_stale(
        payload,
        as_of=datetime.fromisoformat("2026-08-08T10:00:00-04:00"),
    ) is False


def test_expired_mag7_indicator_disk_cache_is_not_used(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    payload = {
        "symbol": "AMZN",
        "bars": [{"time": 1}],
        "historyLoading": False,
    }

    with patch.object(state, "_mag7_option_underlyings", return_value=["AMZN"]):
        state._save_oi_finder_chart_disk_payload("AMZN", payload)
        cache_path = state._oi_finder_chart_disk_path("AMZN")
        assert cache_path is not None
        with patch(
            "api_server.time.time",
            return_value=cache_path.stat().st_mtime + OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS + 1,
        ):
            assert state._load_oi_finder_chart_disk_payload("AMZN") is None


def test_cold_mag7_chart_uses_complete_disk_indicators_without_broker_wait(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    payload = {
        "symbol": "AMZN",
        "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "historyLoading": False,
    }
    refreshes: list[tuple[str, bool]] = []

    with (
        patch.object(state, "_mag7_option_underlyings", return_value=["AMZN"]),
        patch.object(state, "_provider_cache_key", return_value="user:AMZN"),
        patch.object(state, "_start_oi_finder_chart_refresh", side_effect=lambda symbol, full_history: refreshes.append((symbol, full_history))),
        patch.object(state, "_build_oi_finder_chart_payload") as build_payload,
    ):
        state._save_oi_finder_chart_disk_payload("AMZN", payload)
        loaded = state.oi_finder_chart_payload("AMZN")

    build_payload.assert_not_called()
    assert loaded["historyLoading"] is False
    assert loaded["diskCached"] is True
    assert refreshes == [("AMZN", False)]


def test_schema_stale_disk_tape_paints_immediately_then_promotes_selected_symbol(tmp_path) -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_disk_cache_dir = tmp_path
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    payload = {
        "symbol": "NVDA",
        "bars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "studyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "dailyBars": [{"time": 1, "open": 1, "high": 2, "low": 1, "close": 2, "volume": 10}],
        "ganeshHigherTimeframeSignals": {
            "schemaVersion": "ganesh-higher-timeframe-signals-v16",
            "historyReady": True,
            "signals": [{"label": "MACD-2D", "time": 1}],
        },
        "historyLoading": False,
    }
    refreshes: list[tuple[str, bool]] = []

    with (
        patch.object(state, "_mag7_option_underlyings", return_value=["NVDA"]),
        patch.object(state, "_provider_cache_key", return_value="user:NVDA"),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refreshes.append((symbol, full_history)),
        ),
        patch.object(state, "_build_oi_finder_chart_payload") as build_payload,
    ):
        state._save_oi_finder_chart_disk_payload("NVDA", payload)
        loaded = state.oi_finder_chart_payload("NVDA")

    build_payload.assert_not_called()
    assert loaded["diskCached"] is True
    assert loaded["studySchemaStale"] is True
    assert loaded["historyLoading"] is True
    assert loaded["studyBars"] == payload["studyBars"]
    assert loaded["dailyBars"] == payload["dailyBars"]
    assert loaded["ganeshHigherTimeframeSignals"]["signals"] == payload["ganeshHigherTimeframeSignals"]["signals"]
    assert refreshes == [("NVDA", True)]


def test_interactive_full_history_refresh_waits_for_the_single_study_slot() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_full_refresh_lock = threading.Lock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_full_refresh_lock.acquire()
    build_started = threading.Event()
    finished = threading.Event()

    def build_payload(_symbol: str, fast_start: bool) -> dict:
        assert fast_start is False
        build_started.set()
        return {"symbol": "AMZN", "bars": [{"time": 1}], "historyLoading": False}

    with (
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
        patch.object(state, "_build_oi_finder_chart_payload", side_effect=build_payload),
    ):
        worker = threading.Thread(
            target=lambda: (
                state._refresh_oi_finder_chart_payload("AMZN", True, wait_for_full_slot=True),
                finished.set(),
            )
        )
        worker.start()
        assert build_started.wait(timeout=0.05) is False
        state.oi_finder_chart_full_refresh_lock.release()
        assert finished.wait(timeout=1)
        worker.join(timeout=1)

    assert build_started.is_set()


def test_indicator_history_status_is_small_and_restarts_an_interrupted_promotion() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.oi_finder_chart_cache = {
        "user:AMZN": {
            "cached_at": 1.0,
            "payload": {"symbol": "AMZN", "bars": [{"time": 1}], "historyLoading": True},
            "history_ready": False,
        }
    }
    refreshes: list[tuple[str, bool]] = []

    with (
        patch.object(state, "_provider_cache_key", return_value="user:AMZN"),
        patch.object(
            state,
            "_start_oi_finder_chart_refresh",
            side_effect=lambda symbol, full_history: refreshes.append((symbol, full_history)),
        ),
    ):
        status = state.oi_finder_chart_history_status("AMZN")

    assert status == {
        "symbol": "AMZN",
        "historyReady": False,
        "refreshing": True,
        "hasBars": True,
        "warming": False,
    }
    assert "bars" not in status
    assert refreshes == [("AMZN", True)]


def test_chart_warmup_never_queues_indicator_history_before_a_ticker_is_selected() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {}
    state.oi_finder_chart_refreshes = set()
    state.live_market_stream = None
    with (
        patch("api_server.time.sleep"),
        patch("api_server.current_user_provider_context", return_value=None),
        patch.object(state, "_mag7_option_underlyings", return_value=["MSFT"]),
        patch.object(state, "_oi_finder_chart_warm_symbols", return_value=["MSFT", "SPY"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch.object(state, "_build_oi_finder_chart_payload") as build_payload,
    ):
        state._oi_finder_chart_warmup_loop()

    build_payload.assert_not_called()
    assert state.oi_finder_chart_warmup_progress["completed"] == 2


def test_chart_warmup_starts_only_once_per_provider_scope() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_warmup_threads = {}
    state.oi_finder_chart_warmup_thread = None
    started = threading.Event()
    release = threading.Event()
    calls: list[str] = []

    def warm_loop() -> None:
        calls.append("warm")
        started.set()
        release.wait(timeout=1)

    with patch.object(state, "_oi_finder_chart_warmup_loop", side_effect=warm_loop):
        state._start_oi_finder_chart_warmup("user-7")
        assert started.wait(timeout=1)
        state._start_oi_finder_chart_warmup("user-7")
        release.set()
        state.oi_finder_chart_warmup_threads["user-7"].join(timeout=1)

    assert calls == ["warm"]


def _signal_epoch(value: str) -> int:
    return int(pd.Timestamp(value, tz="America/New_York").timestamp())


def _passing_tos_all_gate(symbol: str) -> dict:
    return {
        "ticker": symbol,
        "schemaVersion": "tos_all_same_signal_candle_v2",
        "timing": "SIGNAL_CANDLE",
        "ready": True,
        "extendedHours": True,
        "evaluatedAt": "2026-08-03T09:29:00-04:00",
        "lastPrice": 100.0,
        "lastPriceMinimum": 3.0,
        "lastPricePass": True,
        "fourHourCurrentVolume": 1000.0,
        "fourHourVolumeTwoBarsAgo": 500.0,
        "fourHourVolumeChangePct": 100.0,
        "fourHourVolumeMinimumPct": 0.5,
        "fourHourVolumeEnabled": True,
        "fourHourVolumePass": True,
        "oneHourCurrentClose": 100.0,
        "oneHourCloseTwoBarsAgo": 99.0,
        "oneHourCloseChangePct": 1.0101,
        "oneHourCloseMinimumPct": 0.3,
        "oneHourCloseEnabled": True,
        "oneHourClosePass": True,
        "allOfPass": True,
    }


def _passing_gate_source_bars() -> list[dict]:
    timestamps = pd.date_range(
        "2026-08-01 00:00",
        "2026-08-03 16:00",
        freq="1h",
        tz="America/New_York",
    )
    return [
        {
            "time": int(timestamp.timestamp()),
            "open": 100.0 + index * 0.25,
            "high": 100.2 + index * 0.25,
            "low": 99.8 + index * 0.25,
            "close": 100.0 + index * 0.25,
            "volume": int(100 * (1.25 ** index)),
        }
        for index, timestamp in enumerate(timestamps)
    ]


def test_mag7_tos_all_of_gate_matches_the_three_extended_hours_tos_conditions() -> None:
    timestamps = pd.date_range(
        "2026-08-03 01:00",
        periods=12 * 60,
        freq="1min",
        tz="America/New_York",
    )
    closes = [101.0 if timestamp.hour >= 12 else 100.0 for timestamp in timestamps]
    volumes = [200.0 if timestamp.hour >= 9 else 100.0 for timestamp in timestamps]
    frame = pd.DataFrame(
        {
            "timestamp": timestamps,
            "open": closes,
            "high": [value + 0.1 for value in closes],
            "low": [value - 0.1 for value in closes],
            "close": closes,
            "volume": volumes,
        }
    )

    gate = _mag7_tos_all_of_gate(
        "AMZN",
        frame,
        as_of=pd.Timestamp("2026-08-03 12:59", tz="America/New_York").to_pydatetime(),
    )

    assert gate["ready"] is True
    assert gate["lastPricePass"] is True
    assert gate["fourHourVolumePass"] is True
    assert gate["oneHourClosePass"] is True
    assert gate["allOfPass"] is True
    assert gate["fourHourVolumeChangePct"] == 100.0
    assert gate["oneHourCloseChangePct"] == 1.0


def test_mag7_tos_all_of_gate_ignores_only_the_disabled_optional_conditions() -> None:
    lone_bar = [
        {
            "time": _signal_epoch("2026-08-03 09:00"),
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 100,
        }
    ]

    enabled = _mag7_tos_all_of_gate(
        "AMZN",
        lone_bar,
        as_of=pd.Timestamp("2026-08-03 09:00", tz="America/New_York").to_pydatetime(),
    )
    disabled = _mag7_tos_all_of_gate(
        "AMZN",
        lone_bar,
        as_of=pd.Timestamp("2026-08-03 09:00", tz="America/New_York").to_pydatetime(),
        four_hour_volume_enabled=False,
        one_hour_close_enabled=False,
    )

    assert enabled["ready"] is False
    assert enabled["allOfPass"] is False
    assert disabled["ready"] is True
    assert disabled["lastPricePass"] is True
    assert disabled["fourHourVolumeEnabled"] is False
    assert disabled["oneHourCloseEnabled"] is False
    assert disabled["allOfPass"] is True


def test_mag7_signal_scanner_config_updates_each_optional_gate() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_tos_four_hour_volume_enabled = True
    state.mag7_tos_one_hour_close_enabled = True
    state.dashboard_cache_lock = threading.Lock()
    state.dashboard_cache = {"stale": True}
    state.dashboard_cache_timestamp = 1.0

    payload = state.update_mag7_signal_scanner_config(
        four_hour_volume_enabled=False,
        one_hour_close_enabled=True,
    )

    config = payload["mag7SignalScannerConfig"]
    assert config["fourHourVolumeEnabled"] is False
    assert config["oneHourCloseEnabled"] is True
    assert config["timing"] == "SIGNAL_CANDLE"
    assert config["manualScanAllowedAnytime"] is True
    assert config["symbols"] == [
        "SPY",
        "QQQ",
        "SLV",
        "AAPL",
        "AMZN",
        "GOOGL",
        "META",
        "MSFT",
        "NFLX",
        "NVDA",
        "TSLA",
        "AVGO",
        "USO",
        "INTC",
    ]
    assert state.dashboard_cache is None
    assert state.dashboard_cache_timestamp is None


def test_mag7_chart_signal_scanners_use_fixed_tos_scan_universe() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_scanner_watchlist = ["INTC", "AMD", "NVDL"]

    assert state._mag7_signal_scanner_symbols() == [
        "SPY",
        "QQQ",
        "SLV",
        "AAPL",
        "AMZN",
        "GOOGL",
        "META",
        "MSFT",
        "NFLX",
        "NVDA",
        "TSLA",
        "AVGO",
        "USO",
        "INTC",
    ]


def test_mag7_signal_gate_reuses_prepared_tape_and_candle_result() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_tos_four_hour_volume_enabled = True
    state.mag7_tos_one_hour_close_enabled = True
    state.mag7_tos_gate_cache_lock = threading.RLock()
    state.mag7_tos_gate_frame_cache = {}
    state.mag7_tos_gate_result_cache = {}
    source_bars = _passing_gate_source_bars()
    as_of = pd.Timestamp("2026-08-03 09:00", tz="America/New_York").to_pydatetime()

    with patch(
        "api_server._prepare_mag7_tos_gate_frame",
        wraps=_prepare_mag7_tos_gate_frame,
    ) as prepare:
        first = state._mag7_tos_gate_for_signal("SLV", source_bars, as_of=as_of)
        second = state._mag7_tos_gate_for_signal("SLV", source_bars, as_of=as_of)

        assert first == second
        assert prepare.call_count == 1

        source_bars[-1]["close"] += 1.0
        state._mag7_tos_gate_for_signal("SLV", source_bars, as_of=as_of)
        assert prepare.call_count == 2


def test_mag7_tos_all_results_lists_all_only_matches_from_the_fixed_universe() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {
        "SLV": {"payload": {"studyBars": [{"time": 1, "close": 38.42, "volume": 100}]}},
        "AAPL": {"payload": {"studyBars": [{"time": 1, "close": 200.0, "volume": 100}]}},
        # A saved alias must never leak into the fixed scanner universe.
        "METU": {"payload": {"studyBars": [{"time": 1, "close": 40.0, "volume": 100}]}},
    }
    passing_gate = {
        "ready": True,
        "schemaVersion": "tos_all_same_signal_candle_v2",
        "timing": "SIGNAL_CANDLE",
        "evaluatedAt": "2026-08-04T14:00:00-04:00",
        "lastPrice": 38.42,
        "lastPricePass": True,
        "fourHourVolumeEnabled": False,
        "fourHourVolumePass": False,
        "oneHourCloseEnabled": True,
        "oneHourCloseChangePct": 2.63,
        "oneHourClosePass": True,
        "allOfPass": True,
    }
    blocked_gate = {
        **passing_gate,
        "lastPrice": 200.0,
        "oneHourCloseChangePct": -0.1,
        "oneHourClosePass": False,
        "allOfPass": False,
    }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["SLV", "AAPL"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch.object(
            state,
            "_mag7_tos_gate_for_signal",
            side_effect=lambda symbol, _bars, as_of: passing_gate if symbol == "SLV" else blocked_gate,
        ),
    ):
        payload = state.mag7_tos_all_results_payload(
            pd.Timestamp("2026-08-04 14:05", tz="America/New_York").to_pydatetime()
        )

    assert payload["status"] == "MATCHES"
    assert payload["passSymbols"] == ["SLV"]
    assert [row["symbol"] for row in payload["rows"]] == ["SLV"]
    assert payload["blockedSymbols"] == ["AAPL"]
    assert payload["coverage"] == {
        "total": 2,
        "ready": 2,
        "passed": 1,
        "blocked": 1,
        "pending": 0,
    }
    assert "SLV" in payload["message"]
    assert "METU" not in payload["symbols"]


def test_mag7_tos_all_results_use_one_live_scan_and_do_not_leak_stale_passes() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_tos_four_hour_volume_enabled = False
    state.mag7_tos_one_hour_close_enabled = True
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {
        "AVGO": {"payload": {"studyBars": _passing_gate_source_bars()}},
    }
    passing_gate = {
        **_passing_tos_all_gate("SLV"),
        "fourHourVolumeEnabled": False,
        "oneHourCloseEnabled": True,
    }
    blocked_gate = {
        **passing_gate,
        "ticker": "AAPL",
        "oneHourClosePass": False,
        "allOfPass": False,
    }
    state.mag7_tos_all_live_snapshot = {
        "scannedAt": "2026-08-04T14:05:00-04:00",
        "gates": {"SLV": passing_gate, "AAPL": blocked_gate},
    }

    with (
        patch.object(
            state,
            "_mag7_signal_scanner_symbols",
            return_value=["SLV", "AAPL", "AVGO"],
        ),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
    ):
        payload = state.mag7_tos_all_results_payload(
            pd.Timestamp("2026-08-04 14:06", tz="America/New_York").to_pydatetime()
        )

    assert payload["passSymbols"] == ["SLV"]
    assert payload["blockedSymbols"] == ["AAPL"]
    assert payload["pendingSymbols"] == ["AVGO"]
    assert payload["coverage"] == {
        "total": 3,
        "ready": 2,
        "passed": 1,
        "blocked": 1,
        "pending": 1,
    }
    assert payload["liveScanAt"] == "2026-08-04T14:05:00-04:00"


def test_mag7_manual_signal_scan_refreshes_recent_tos_tapes_without_deep_history() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_signal_scan_job_lock = threading.Lock()
    state.mag7_signal_scan_job = {"running": False}
    state.dashboard_cache_lock = threading.Lock()
    state.dashboard_cache = {"stale": True}
    state.dashboard_cache_timestamp = 1.0
    finished = threading.Event()
    def refresh_five_minute(*, refresh: bool) -> dict:
        assert refresh is False
        return {
            "matchCount": 2,
            "pendingSymbols": ["NVDA"],
        }

    def refresh_premarket(*, refresh: bool) -> dict:
        assert refresh is False
        finished.set()
        return {
            "matchCount": 1,
            "pendingSymbols": ["NVDA"],
        }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN", "NVDA"]),
        patch.object(state, "_refresh_oi_finder_chart_payload") as deep_refresh,
        patch.object(
            state,
            "_refresh_mag7_tos_all_symbol",
            side_effect=lambda symbol: _passing_tos_all_gate(symbol),
        ) as recent_refresh,
        patch.object(
            state,
            "mag7_tos_all_results_payload",
            return_value={
                "matchCount": 1,
                "passSymbols": ["AMZN"],
                "pendingSymbols": [],
            },
        ),
        patch.object(state, "mag7_five_minute_chart_signals_payload", side_effect=refresh_five_minute) as five_minute,
        patch.object(state, "mag7_premarket_chart_signals_payload", side_effect=refresh_premarket),
    ):
        started = state.start_mag7_signal_scan_job()
        assert started["mag7SignalScanJob"]["running"] is True
        assert started["mag7SignalScanJob"]["total"] == 2
        assert started["mag7SignalScanJob"]["message"] == "Waiting for result."
        assert finished.wait(timeout=2)

    deep_refresh.assert_not_called()
    assert recent_refresh.call_count == 2
    five_minute.assert_called_once_with(refresh=False)
    assert state.mag7_signal_scan_job["running"] is False
    assert state.mag7_signal_scan_job["completed"] == 2
    assert state.mag7_signal_scan_job["pending"] == 0
    assert state.mag7_signal_scan_job["matchCount"] == 1
    assert state.mag7_signal_scan_job["signalMatchCount"] == 3
    assert state.mag7_signal_scan_job["mode"] == "live"
    assert "1 TOS All match (AMZN)" in state.mag7_signal_scan_job["message"]
    assert "all 2 tickers refreshed" in state.mag7_signal_scan_job["message"]
    assert state.mag7_signal_scan_job["error"] == ""
    assert state.dashboard_cache is None
    assert state.dashboard_cache_timestamp is None


def test_mag7_manual_signal_scan_finishes_when_a_provider_request_stalls() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_signal_scan_job_lock = threading.Lock()
    state.mag7_signal_scan_job = {"running": False}
    state.dashboard_cache_lock = threading.Lock()
    state.dashboard_cache = None
    state.dashboard_cache_timestamp = None
    finished = threading.Event()

    def stalled_refresh(_symbol: str) -> dict:
        time.sleep(0.2)
        return _passing_tos_all_gate("USO")

    def finish_panel(*, refresh: bool) -> dict:
        assert refresh is False
        finished.set()
        return {"matchCount": 0, "pendingSymbols": []}

    with (
        patch("api_server.MAG7_TOS_LIVE_SCAN_TIMEOUT_SECONDS", 0.05),
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["USO"]),
        patch.object(state, "_refresh_mag7_tos_all_symbol", side_effect=stalled_refresh),
        patch.object(
            state,
            "mag7_tos_all_results_payload",
            return_value={"matchCount": 0, "passSymbols": [], "pendingSymbols": ["USO"]},
        ),
        patch.object(
            state,
            "mag7_five_minute_chart_signals_payload",
            return_value={"matchCount": 0, "pendingSymbols": []},
        ),
        patch.object(state, "mag7_premarket_chart_signals_payload", side_effect=finish_panel),
    ):
        state.start_mag7_signal_scan_job()
        assert finished.wait(timeout=0.5)

    assert state.mag7_signal_scan_job["running"] is False
    assert state.mag7_signal_scan_job["pending"] == 1
    assert "1 ticker unavailable" in state.mag7_signal_scan_job["message"]


def test_mag7_manual_signal_scan_reports_archived_counts_after_rollover() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_signal_scan_job_lock = threading.Lock()
    state.mag7_signal_scan_job = {"running": False}
    state.dashboard_cache_lock = threading.Lock()
    state.dashboard_cache = None
    state.dashboard_cache_timestamp = None
    finished = threading.Event()

    def archived_premarket(*, refresh: bool) -> dict:
        assert refresh is False
        finished.set()
        return {
            "archivedForDay": True,
            "archivedMatchCount": 1,
            "matchCount": 0,
            "pendingSymbols": [],
        }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["SLV"]),
        patch.object(
            state,
            "_refresh_mag7_tos_all_symbol",
            return_value=_passing_tos_all_gate("SLV"),
        ),
        patch.object(
            state,
            "mag7_tos_all_results_payload",
            return_value={
                "matchCount": 1,
                "passSymbols": ["SLV"],
                "pendingSymbols": [],
            },
        ),
        patch.object(
            state,
            "mag7_five_minute_chart_signals_payload",
            return_value={
                "archivedForDay": True,
                "archivedMatchCount": 2,
                "matchCount": 0,
                "pendingSymbols": [],
            },
        ),
        patch.object(
            state,
            "mag7_premarket_chart_signals_payload",
            side_effect=archived_premarket,
        ),
    ):
        state.start_mag7_signal_scan_job()
        assert finished.wait(timeout=2)

    assert state.mag7_signal_scan_job["running"] is False
    assert state.mag7_signal_scan_job["matchCount"] == 1
    assert state.mag7_signal_scan_job["signalMatchCount"] == 3
    assert "1 TOS All match (SLV)" in state.mag7_signal_scan_job["message"]
    assert "3 chart signals" in state.mag7_signal_scan_job["message"]


def _ready_ganesh_cache(
    symbol: str,
    signals: list[dict],
    *,
    mtf_signals: list[dict] | None = None,
) -> dict:
    source_time = _signal_epoch("2026-08-03 09:29")
    cutoff_mtf_signals = list(mtf_signals or [])
    premarket_tape = {
        "historyReady": True,
        "sourceAggregationMinutes": 240,
        "marketDate": "2026-08-03",
        "sourceLastBarTime": source_time,
        "updatedAt": "2026-08-03T09:30:00-04:00",
        "complete": True,
        "provisional": False,
        "signals": signals,
        "mtfSignals": cutoff_mtf_signals,
        "mtfSignalMode": "tos_live_secondary_primary_projection",
        "tosAllOfGate": _passing_tos_all_gate(symbol),
    }
    return {
        "cached_at": 100.0,
        "study_cached_at": 100.0,
        "history_ready": True,
        "payload": {
            "symbol": symbol,
            "bars": [{"time": _signal_epoch("2026-08-03 09:05"), "close": 100}],
            "studyBars": _passing_gate_source_bars(),
            "dailyBars": [{"time": _signal_epoch("2026-07-31 16:00"), "close": 99}],
            "historyLoading": False,
            "signalTapeUpdatedAt": "2026-08-03T09:06:00-04:00",
            "mtfSignals": cutoff_mtf_signals,
            "ganeshHigherTimeframeSignals": {
                "historyReady": True,
                "sourceAggregationMinutes": 240,
                "signals": signals,
            },
            "mag7PremarketChartSignalTape": premarket_tape,
            "tosAllOfGate": _passing_tos_all_gate(symbol),
        },
    }


def _ready_five_minute_signal_cache(
    symbol: str,
    *,
    bars: list[dict],
    mtf_signals: list[dict],
    ganesh_signals: list[dict],
) -> dict:
    source_time = max(int(bar["time"]) for bar in bars)
    return {
        "cached_at": 100.0,
        "study_cached_at": 100.0,
        "history_ready": True,
        "payload": {
            "symbol": symbol,
            "bars": bars,
            "studyBars": _passing_gate_source_bars(),
            "dailyBars": [{"time": _signal_epoch("2026-07-31 16:00"), "close": 99}],
            "historyLoading": False,
            "signalTapeUpdatedAt": "2026-08-03T13:06:00-04:00",
            "signalTapeSourceLastBarTime": source_time,
            "signalTapeMarketDate": "2026-08-03",
            "mtfSignalMode": "tos_live_secondary_primary_projection",
            "mtfSignals": mtf_signals,
            "ganeshHigherTimeframeSignals": {
                "historyReady": True,
                "sourceAggregationMinutes": 240,
                "signals": ganesh_signals,
            },
            "tosAllOfGate": _passing_tos_all_gate(symbol),
        },
    }


def test_mag7_five_minute_panel_matches_only_events_drawable_on_exact_chart_candles() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    chart_times = [
        "2026-08-02 21:00",
        "2026-08-03 01:00",
        "2026-08-03 05:00",
        "2026-08-03 07:00",
        "2026-08-03 09:00",
        "2026-08-03 13:05",
    ]
    bars = [
        {
            "time": _signal_epoch(value),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
        }
        for value in chart_times
    ]
    state.oi_finder_chart_cache = {
        "AMZN": _ready_five_minute_signal_cache(
            "AMZN",
            bars=bars,
            mtf_signals=[
                {
                    "family": "4x8",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": _signal_epoch("2026-08-03 07:02"),
                },
                {
                    "family": "4x8",
                    "timeframe": "2H",
                    "direction": "CALL",
                    "label": "C2H",
                    "time": _signal_epoch("2026-08-03 07:00"),
                },
                {
                    "family": "9x20",
                    "timeframe": "4H",
                    "direction": "CALL",
                    "label": "CALL4H",
                    "time": _signal_epoch("2026-08-03 09:00"),
                },
                {
                    "family": "9x20",
                    "timeframe": "2H",
                    "direction": "PUT",
                    "label": "PUT2H",
                    "time": _signal_epoch("2026-08-03 09:00"),
                },
                {
                    "family": "4x8",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": _signal_epoch("2026-08-03 10:02"),
                },
            ],
            ganesh_signals=[
                {
                    "family": "ganesh920",
                    "timeframe": "2D",
                    "direction": "CALL",
                    "label": "CALL2D",
                    "time": _signal_epoch("2026-08-02 21:00"),
                },
                {
                    "family": "ganeshMacd",
                    "timeframe": "W",
                    "direction": "CALL",
                    "label": "MACD-W",
                    "time": _signal_epoch("2026-08-03 01:00"),
                },
                {
                    "family": "ganesh48",
                    "timeframe": "D",
                    "direction": "CALL",
                    "label": "CALLD",
                    "time": _signal_epoch("2026-08-03 05:00"),
                },
                {
                    "family": "ganeshMacd",
                    "timeframe": "D",
                    "direction": "CALL",
                    "label": "MACD-D",
                    "time": _signal_epoch("2026-08-03 09:00"),
                },
                {
                    "family": "ganesh48",
                    "timeframe": "M",
                    "direction": "CALL",
                    "label": "CALLM",
                    "time": _signal_epoch("2026-08-02 17:00"),
                },
            ],
        ),
    }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN", "GOOGL"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_five_minute_chart_signals_payload(
            pd.Timestamp("2026-08-03 13:10", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "PARTIAL"
    assert payload["readySymbols"] == ["AMZN"]
    assert payload["pendingSymbols"] == ["GOOGL"]
    assert payload["matchCount"] == 1
    row = payload["rows"][0]
    assert row["symbol"] == "AMZN"
    assert row["intraday48Signals"] == []
    assert row["intraday920Signals"] == ["CALL4H"]
    assert row["higher48Signals"] == []
    assert row["higher920Signals"] == []
    assert row["macdSignals"] == ["MACD-D"]
    assert row["signalCount"] == 2
    assert row["signalCandles"] == ["2026-08-03T09:00:00-04:00"]
    assert payload["latestCandleOnly"] is True
    assert payload["intradayWindowStart"] == "2026-08-03T09:00:00-04:00"
    assert payload["sessionCutoff"] == "2026-08-03T15:35:00-04:00"
    assert {signal["label"] for signal in row["signals"]} == {"CALL4H", "MACD-D"}
    assert not any(
        signal["label"] in {"CALL1H", "C2H", "PUT2H", "CALLD", "CALL2D", "MACD-W", "CALLM"}
        for signal in row["signals"]
    )

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN", "GOOGL"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        rolled_over = state.mag7_five_minute_chart_signals_payload(
            pd.Timestamp("2026-08-03 20:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert rolled_over["status"] == "ARCHIVED"
    assert rolled_over["archivedForDay"] is True
    assert rolled_over["archivedMatchCount"] == 1
    assert [row["symbol"] for row in rolled_over["rows"]] == ["AMZN"]
    assert "remain visible until the next day starts fresh" in rolled_over["message"]


def test_mag7_five_minute_panel_uses_0900_through_1530_et_candles_only() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    chart_times = [
        "2026-08-03 08:55",
        "2026-08-03 09:00",
        "2026-08-03 15:30",
        "2026-08-03 15:35",
    ]
    bars = [
        {
            "time": _signal_epoch(value),
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
        }
        for value in chart_times
    ]
    state.oi_finder_chart_cache = {
        "AMZN": _ready_five_minute_signal_cache(
            "AMZN",
            bars=bars,
            mtf_signals=[
                {
                    "family": "4x8",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": _signal_epoch(value),
                }
                for value in chart_times
            ],
            ganesh_signals=[],
        ),
    }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN"]),
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_five_minute_chart_signals_payload(
            pd.Timestamp("2026-08-03 15:40", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "COMPLETE"
    assert payload["sessionActive"] is False
    assert payload["sessionComplete"] is True
    assert payload["sessionWindowStart"] == "2026-08-03T09:00:00-04:00"
    assert payload["sessionWindowEnd"] == "2026-08-03T15:35:00-04:00"
    assert payload["matchCount"] == 1
    row = payload["rows"][0]
    assert row["latestSignalAt"] == "2026-08-03T15:30:00-04:00"
    assert row["signalCandles"] == ["2026-08-03T15:30:00-04:00"]
    assert {signal["signalAt"] for signal in row["signals"]} == {
        "2026-08-03T15:30:00-04:00"
    }


def test_mag7_five_minute_panel_does_not_apply_a_later_gate_to_an_older_signal() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    signal_epoch = _signal_epoch("2026-08-03 09:00")
    bars = [
        {
            "time": signal_epoch,
            "open": 100,
            "high": 101,
            "low": 99,
            "close": 100,
            "volume": 10,
        }
    ]
    state.oi_finder_chart_cache = {
        "AMZN": _ready_five_minute_signal_cache(
            "AMZN",
            bars=bars,
            mtf_signals=[
                {
                    "family": "4x8",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": signal_epoch,
                }
            ],
            ganesh_signals=[],
        ),
    }
    evaluated_hours: list[int] = []

    def gate_at_time(symbol: str, source_bars: object, *, as_of: datetime) -> dict:
        del source_bars
        evaluated_hours.append(as_of.hour)
        gate = _passing_tos_all_gate(symbol)
        gate["evaluatedAt"] = as_of.isoformat()
        gate["allOfPass"] = as_of.hour >= 13
        gate["fourHourVolumePass"] = as_of.hour >= 13
        gate["oneHourClosePass"] = as_of.hour >= 13
        return gate

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN"]),
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
        patch.object(state, "_mag7_tos_gate_for_signal", side_effect=gate_at_time),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_five_minute_chart_signals_payload(
            pd.Timestamp("2026-08-03 13:10", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert evaluated_hours == [9]
    assert payload["rows"] == []
    assert payload["coverage"]["allOfPassed"] == 0
    assert payload["coverage"]["allOfBlocked"] == 1


def test_mag7_five_minute_panel_schedules_full_tape_refresh() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_premarket_chart_signal_refresh_enabled = True
    state.mag7_five_minute_chart_signal_refresh_attempts = {}
    state.oi_finder_chart_full_refresh_lock = threading.Lock()

    with patch.object(state, "_start_oi_finder_chart_refresh") as start_full:
        scheduled = state._schedule_mag7_five_minute_chart_signal_refresh(
            ["AMZN"],
            {"AMZN": "user:AMZN"},
            set(),
            500.0,
        )

    assert scheduled == "AMZN"
    start_full.assert_called_once_with("AMZN", full_history=True)


def test_mag7_chart_signal_history_hides_current_day_until_eight_pm_rollover() -> None:
    state = DashboardState.__new__(DashboardState)
    nvda_row = {
        "symbol": "NVDA",
        "latestSignalAt": "2026-08-02T14:00:00-04:00",
        "signalCount": 1,
        "tosAllOfGate": _passing_tos_all_gate("NVDA"),
    }
    avgo_row = {
        "symbol": "AVGO",
        "latestSignalAt": "2026-08-03T14:00:00-04:00",
        "signalCount": 1,
        "tosAllOfGate": _passing_tos_all_gate("AVGO"),
    }
    amzn_row = {
        "symbol": "AMZN",
        "latestSignalAt": "2026-08-03T09:00:00-04:00",
        "signalCount": 2,
        "tosAllOfGate": _passing_tos_all_gate("AMZN"),
    }
    history = pd.DataFrame(
        [
            {
                "scan_date": "2026-08-02",
                "scanner": "mag7_5m",
                "symbol": "NVDA",
                "first_seen_at": "2026-08-02T09:00:00-04:00",
                "last_seen_at": "2026-08-02T14:00:00-04:00",
                "latest_signal_at": "2026-08-02T14:00:00-04:00",
                "row_json": json.dumps(nvda_row),
            },
            {
                "scan_date": "2026-08-03",
                "scanner": "mag7_5m",
                "symbol": "AVGO",
                "first_seen_at": "2026-08-03T08:00:00-04:00",
                "last_seen_at": "2026-08-03T14:00:00-04:00",
                "latest_signal_at": "2026-08-03T14:00:00-04:00",
                "row_json": json.dumps(avgo_row),
            },
            {
                "scan_date": "2026-08-03",
                "scanner": "mag7_4h_premarket",
                "symbol": "AMZN",
                "first_seen_at": "2026-08-03T09:05:00-04:00",
                "last_seen_at": "2026-08-03T09:30:00-04:00",
                "latest_signal_at": "2026-08-03T09:00:00-04:00",
                "row_json": json.dumps(amzn_row),
            },
        ]
    )
    state.repository = SimpleNamespace(
        get_chart_signal_scanner_history=lambda days=30: history,
    )

    before_rollover = state.mag7_chart_signal_history_payload(
        pd.Timestamp("2026-08-03 19:59", tz="America/New_York").to_pydatetime()
    )
    after_rollover = state.mag7_chart_signal_history_payload(
        pd.Timestamp("2026-08-03 20:00", tz="America/New_York").to_pydatetime()
    )

    assert before_rollover["currentDayArchived"] is False
    assert [row["symbol"] for row in before_rollover["fiveMinuteRows"]] == ["NVDA"]
    assert before_rollover["premarketRows"] == []
    assert after_rollover["currentDayArchived"] is True
    assert [row["symbol"] for row in after_rollover["fiveMinuteRows"]] == ["AVGO", "NVDA"]
    assert [row["symbol"] for row in after_rollover["premarketRows"]] == ["AMZN"]
    assert after_rollover["latestFiveMinuteDate"] == "2026-08-03"
    assert after_rollover["latestPremarketDate"] == "2026-08-03"


def test_background_chart_signal_capture_runs_both_scanners_without_dashboard_reads() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_chart_signal_archive_last_run = None
    state.mag7_chart_signal_archive_last_error = "old error"
    five_minute = {
        "matchCount": 3,
        "archivedMatchCount": 3,
        "archivedForDay": False,
    }
    premarket = {
        "matchCount": 2,
        "archivedMatchCount": 2,
        "archivedForDay": False,
    }

    with (
        patch.object(state, "mag7_five_minute_chart_signals_payload", return_value=five_minute) as five,
        patch.object(state, "mag7_premarket_chart_signals_payload", return_value=premarket) as four,
    ):
        result = state._capture_mag7_chart_signal_archive_once(
            pd.Timestamp("2026-08-03 14:00", tz="America/New_York").to_pydatetime()
        )

    assert result["fiveMinuteMatches"] == 3
    assert result["premarketMatches"] == 2
    assert result["archivedForDay"] is False
    five.assert_called_once()
    four.assert_called_once()
    assert five.call_args.kwargs["refresh"] is True
    assert four.call_args.kwargs["refresh"] is True
    assert state.mag7_chart_signal_archive_last_error == ""


def test_mag7_premarket_chart_panel_keeps_session_signals_and_reports_latest_scan_candle() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    state.oi_finder_chart_cache = {
        "NVDA": _ready_ganesh_cache(
            "NVDA",
            [
                {
                    "key": "nvda-cyan",
                    "family": "ganesh920",
                    "timeframe": "4D",
                    "direction": "CALL",
                    "label": "CALL4D",
                    "time": _signal_epoch("2026-08-03 09:00"),
                },
                {
                    "key": "nvda-after-open",
                    "family": "ganeshMacd",
                    "timeframe": "M",
                    "direction": "CALL",
                    "label": "MACD-M",
                    "time": _signal_epoch("2026-08-03 13:00"),
                },
            ],
            mtf_signals=[
                {
                    "key": "nvda-4x8-call4h",
                    "family": "4x8",
                    "timeframe": "4H",
                    "direction": "CALL",
                    "label": "CALL4H",
                    "time": _signal_epoch("2026-08-03 09:05"),
                }
            ],
        ),
        "AMZN": _ready_ganesh_cache(
            "AMZN",
            [
                {
                    "key": "amzn-cyan-1",
                    "family": "ganesh920",
                    "timeframe": "2D",
                    "direction": "CALL",
                    "label": "CALL2D",
                    "time": _signal_epoch("2026-08-03 01:00"),
                },
                {
                    "key": "amzn-prior-evening-cyan",
                    "family": "ganesh920",
                    "timeframe": "3D",
                    "direction": "CALL",
                    "label": "CALL3D",
                    "time": _signal_epoch("2026-08-02 17:00"),
                },
                {
                    "key": "amzn-prior-evening-macd-2d",
                    "family": "ganeshMacd",
                    "timeframe": "2D",
                    "direction": "CALL",
                    "label": "MACD-2D",
                    "time": _signal_epoch("2026-08-02 21:00"),
                },
                {
                    "key": "amzn-prior-evening-macd-3d",
                    "family": "ganeshMacd",
                    "timeframe": "3D",
                    "direction": "CALL",
                    "label": "MACD-3D",
                    "time": _signal_epoch("2026-08-02 21:00"),
                },
                {
                    "key": "amzn-prior-evening-macd-4d",
                    "family": "ganeshMacd",
                    "timeframe": "4D",
                    "direction": "CALL",
                    "label": "MACD-4D",
                    "time": _signal_epoch("2026-08-02 21:00"),
                },
                {
                    "key": "amzn-prior-evening-macd-w",
                    "family": "ganeshMacd",
                    "timeframe": "W",
                    "direction": "CALL",
                    "label": "MACD-W",
                    "time": _signal_epoch("2026-08-02 21:00"),
                },
                {
                    "key": "amzn-prior-evening-too-early",
                    "family": "ganeshMacd",
                    "timeframe": "M",
                    "direction": "CALL",
                    "label": "MACD-M",
                    "time": _signal_epoch("2026-08-02 13:00"),
                },
                {
                    "key": "amzn-put",
                    "family": "ganeshMacd",
                    "timeframe": "4D",
                    "direction": "PUT",
                    "label": "MACD-4D",
                    "time": _signal_epoch("2026-08-03 05:00"),
                },
                {
                    "key": "amzn-yellow",
                    "family": "ganesh48",
                    "timeframe": "D",
                    "direction": "CALL",
                    "label": "CALLD",
                    "time": _signal_epoch("2026-08-03 05:00"),
                },
                {
                    "key": "amzn-prior-day",
                    "family": "ganeshMacd",
                    "timeframe": "W",
                    "direction": "CALL",
                    "label": "MACD-W",
                    "time": _signal_epoch("2026-07-31 05:00"),
                },
            ],
            mtf_signals=[
                {
                    "key": "amzn-4x8-call1h",
                    "family": "4x8",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": _signal_epoch("2026-08-03 08:05"),
                },
                {
                    "key": "amzn-4x8-call2h",
                    "family": "4x8",
                    "timeframe": "2H",
                    "direction": "CALL",
                    "label": "CALL2H",
                    "time": _signal_epoch("2026-08-03 09:10"),
                },
                {
                    "key": "amzn-4x8-partial",
                    "family": "4x8",
                    "timeframe": "4H",
                    "direction": "CALL",
                    "label": "C4H",
                    "time": _signal_epoch("2026-08-03 09:15"),
                },
                {
                    "key": "amzn-4x8-after-open",
                    "family": "4x8",
                    "timeframe": "4H",
                    "direction": "CALL",
                    "label": "CALL4H",
                    "time": _signal_epoch("2026-08-03 10:00"),
                },
                {
                    "key": "amzn-9x20-not-requested",
                    "family": "9x20",
                    "timeframe": "1H",
                    "direction": "CALL",
                    "label": "CALL1H",
                    "time": _signal_epoch("2026-08-03 08:30"),
                },
            ],
        ),
        "GOOGL": _ready_ganesh_cache(
            "GOOGL",
            [
                {
                    "key": "googl-macd",
                    "family": "ganeshMacd",
                    "timeframe": "D",
                    "direction": "CALL",
                    "label": "MACD-D",
                    "time": _signal_epoch("2026-08-03 01:00"),
                }
            ],
        ),
        "TSLA": _ready_ganesh_cache("TSLA", []),
    }

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["NVDA", "AMZN", "GOOGL", "TSLA"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )
        rolled_over = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 20:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "MATCHES"
    assert payload["coverage"] == {
        "total": 4,
        "ready": 4,
        "historyReady": 4,
        "loading": 0,
        "stale": 0,
        "refreshing": 0,
        "allOfPassed": 3,
        "allOfBlocked": 0,
        "allOfPending": 0,
    }
    assert payload["latestScanCandleAt"] == "2026-08-03T09:00:00-04:00"
    assert {row["symbol"] for row in payload["rows"]} == {"NVDA", "AMZN", "GOOGL"}
    by_symbol = {row["symbol"]: row for row in payload["rows"]}
    assert by_symbol["NVDA"]["intraday48Signals"] == ["CALL4H"]
    assert by_symbol["NVDA"]["higher48Signals"] == []
    assert by_symbol["NVDA"]["cyanSignals"] == ["CALL4D"]
    assert by_symbol["NVDA"]["macdSignals"] == []
    assert by_symbol["AMZN"]["intraday48Signals"] == ["CALL1H", "CALL2H"]
    assert by_symbol["AMZN"]["higher48Signals"] == ["CALLD"]
    assert by_symbol["AMZN"]["cyanSignals"] == ["CALL2D", "CALL3D"]
    assert by_symbol["AMZN"]["macdSignals"] == [
        "MACD-2D",
        "MACD-3D",
        "MACD-4D",
        "MACD-W",
    ]
    assert by_symbol["AMZN"]["signalCount"] == 9
    assert len(by_symbol["AMZN"]["signalCandles"]) == 5
    assert by_symbol["AMZN"]["signalCandles"][-2:] == [
        "2026-08-03T05:00:00-04:00",
        "2026-08-03T09:00:00-04:00",
    ]
    assert by_symbol["AMZN"]["latestSignalAt"] == "2026-08-03T09:00:00-04:00"
    assert by_symbol["AMZN"]["latestScanCandleAt"] == "2026-08-03T09:00:00-04:00"
    assert not any(
        signal["label"] in {"C4H"}
        or signal["family"] == "9x20"
        or signal["signalAt"].startswith("2026-08-03T10:00:00")
        for signal in by_symbol["AMZN"]["signals"]
    )
    assert by_symbol["AMZN"]["firstSignalAt"].startswith("2026-08-02T17:00:00")
    assert payload["sessionWindowStart"].startswith("2026-08-02T17:00:00")
    assert payload["sessionWindowEnd"].startswith("2026-08-03T09:30:00")
    assert by_symbol["GOOGL"]["macdSignals"] == ["MACD-D"]
    assert by_symbol["GOOGL"]["latestScanCandleAt"] == "2026-08-03T09:00:00-04:00"
    assert all(symbol in payload["message"] for symbol in ("NVDA", "AMZN", "GOOGL"))
    assert rolled_over["status"] == "ARCHIVED"
    assert rolled_over["archivedForDay"] is True
    assert rolled_over["archivedMatchCount"] == 3
    assert {row["symbol"] for row in rolled_over["rows"]} == {"NVDA", "AMZN", "GOOGL"}
    assert "remain visible until the next day starts fresh" in rolled_over["message"]


def test_mag7_premarket_panel_rejects_any_signal_when_one_tos_all_gate_fails() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    signal = {
        "key": "amzn-call",
        "family": "ganesh920",
        "timeframe": "D",
        "direction": "CALL",
        "label": "CALLD",
        "time": _signal_epoch("2026-08-03 09:00"),
    }
    cache = _ready_ganesh_cache("AMZN", [signal])
    gate = cache["payload"]["mag7PremarketChartSignalTape"]["tosAllOfGate"]
    gate.update({
        "fourHourVolumeChangePct": 0.49,
        "fourHourVolumePass": False,
        "allOfPass": False,
    })
    cache["payload"]["studyBars"] = [
        {
            **bar,
            "open": 100.0,
            "high": 100.1,
            "low": 99.9,
            "close": 100.0,
            "volume": 100,
        }
        for bar in _passing_gate_source_bars()
    ]
    state.oi_finder_chart_cache = {"AMZN": cache}

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN"]),
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "NO MATCHES"
    assert payload["rows"] == []
    assert payload["allOfPassSymbols"] == []
    assert payload["allOfBlockedSymbols"] == ["AMZN"]
    assert payload["coverage"]["allOfBlocked"] == 1


def test_mag7_premarket_tape_replays_source_only_through_0929_et() -> None:
    state = DashboardState.__new__(DashboardState)
    previous_day = {
        "time": _signal_epoch("2026-07-31 15:59"),
        "open": 99,
        "high": 100,
        "low": 98,
        "close": 99,
        "volume": 10,
    }
    premarket_bar = {
        "time": _signal_epoch("2026-08-03 09:29"),
        "open": 100,
        "high": 102,
        "low": 100,
        "close": 102,
        "volume": 20,
    }
    after_open_bar = {
        "time": _signal_epoch("2026-08-03 10:30"),
        "open": 102,
        "high": 110,
        "low": 101,
        "close": 109,
        "volume": 100,
    }
    daily_bars = [
        {**previous_day, "date": "2026-07-31"},
        {**after_open_bar, "date": "2026-08-03"},
    ]
    rebuilt_tape = {
        "historyReady": True,
        "sourceAggregationMinutes": 240,
        "signals": [],
    }

    cutoff_mtf_signal = {
        "family": "4x8",
        "timeframe": "1H",
        "direction": "CALL",
        "label": "CALL1H",
        "time": premarket_bar["time"],
    }
    with (
        patch(
            "api_server.build_ganesh_higher_timeframe_signal_payload",
            return_value=rebuilt_tape,
        ) as rebuild,
        patch(
            "api_server._tos_mtf_ema_signal_payload",
            return_value={
                "signals": [cutoff_mtf_signal],
                "mode": "tos_live_secondary_primary_projection",
            },
        ) as rebuild_mtf,
    ):
        snapshot = state._build_mag7_premarket_chart_signal_tape(
            [previous_day, premarket_bar, after_open_bar],
            [previous_day, premarket_bar, after_open_bar],
            daily_bars,
            as_of=pd.Timestamp("2026-08-03 12:00", tz="America/New_York").to_pydatetime(),
        )

    study_arg, live_arg, daily_arg = rebuild.call_args.args
    assert [bar["time"] for bar in study_arg] == [previous_day["time"], premarket_bar["time"]]
    assert [bar["time"] for bar in live_arg] == [previous_day["time"], premarket_bar["time"]]
    assert [bar["date"] for bar in daily_arg] == ["2026-07-31"]
    mtf_frame = rebuild_mtf.call_args.args[0]
    assert [int(timestamp.timestamp()) for timestamp in mtf_frame["timestamp"]] == [
        previous_day["time"],
        premarket_bar["time"],
    ]
    assert snapshot["mtfSignals"] == [cutoff_mtf_signal]
    assert snapshot["sourceLastBarTime"] == premarket_bar["time"]
    assert snapshot["complete"] is True
    assert snapshot["provisional"] is False


def test_mag7_premarket_panel_does_not_use_stale_canonical_chart_tape() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    canonical_match = {
        "key": "late-canonical-call",
        "family": "ganesh920",
        "timeframe": "D",
        "direction": "CALL",
        "label": "CALLD",
        "time": _signal_epoch("2026-08-03 09:00"),
    }
    cache = _ready_ganesh_cache("AMZN", [canonical_match])
    cache["payload"]["mag7PremarketChartSignalTape"].update({
        "marketDate": "2026-07-31",
        "sourceLastBarTime": _signal_epoch("2026-07-31 09:29"),
        "updatedAt": "2026-07-31T09:30:00-04:00",
        "complete": True,
    })
    state.oi_finder_chart_cache = {"AMZN": cache}

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN"]),
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
    ):
        payload = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "WARMING"
    assert payload["rows"] == []
    assert payload["readySymbols"] == []
    assert payload["pendingSymbols"] == ["AMZN"]
    assert payload["staleSymbols"] == ["AMZN"]


def test_mag7_premarket_panel_restores_today_matches_while_tapes_warm() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    state.oi_finder_chart_cache = {}
    saved_row = {
        "symbol": "AMZN",
        "session": "PREMARKET",
        "chartTimeframe": "4h",
        "latestSignalAt": "2026-08-03T05:00:00-04:00",
        "firstSignalAt": "2026-08-03T05:00:00-04:00",
        "signalCandles": ["2026-08-03T05:00:00-04:00"],
        "intraday48Signals": ["CALL4H"],
        "higher48Signals": [],
        "cyanSignals": ["CALL2D"],
        "macdSignals": ["MACD-4D"],
        "signalCount": 3,
        "signals": [],
        "tosAllOfGate": _passing_tos_all_gate("AMZN"),
        "tosAllOfPass": True,
    }
    history = pd.DataFrame(
        [
            {
                "scan_date": "2026-08-03",
                "scanner": "mag7_4h_premarket",
                "symbol": "AMZN",
                "row_json": json.dumps(saved_row),
            }
        ]
    )
    state.repository = SimpleNamespace(
        get_chart_signal_scanner_history=lambda days=1: history,
    )

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN", "NVDA"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        payload = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert payload["status"] == "PARTIAL"
    assert payload["coverage"]["ready"] == 0
    assert payload["pendingSymbols"] == ["AMZN", "NVDA"]
    assert payload["matchCount"] == 1
    assert payload["restoredMatchCount"] == 1
    assert payload["latestScanCandleAt"] == "2026-08-03T09:00:00-04:00"
    assert payload["rows"][0]["symbol"] == "AMZN"
    assert payload["rows"][0]["restoredFromToday"] is True
    assert payload["rows"][0]["latestSignalAt"] == "2026-08-03T05:00:00-04:00"
    assert payload["rows"][0]["latestScanCandleAt"] == "2026-08-03T09:00:00-04:00"


def test_mag7_premarket_chart_panel_distinguishes_warming_from_no_matches() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_refreshes = set()
    state.mag7_premarket_chart_signal_refreshes = set()
    state.oi_finder_chart_cache = {"AMZN": _ready_ganesh_cache("AMZN", [])}
    morning_tape = state.oi_finder_chart_cache["AMZN"]["payload"]["mag7PremarketChartSignalTape"]
    morning_tape.update({
        "sourceLastBarTime": _signal_epoch("2026-08-03 07:59"),
        "updatedAt": "2026-08-03T07:59:00-04:00",
        "complete": False,
        "provisional": True,
    })

    with (
        patch.object(state, "_mag7_signal_scanner_symbols", return_value=["AMZN", "NVDA"]),
        patch.object(state, "_provider_cache_key", side_effect=lambda symbol: symbol),
        patch("api_server.time.monotonic", return_value=100.0),
    ):
        warming = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 08:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )
        state.oi_finder_chart_cache["AMZN"] = _ready_ganesh_cache("AMZN", [])
        state.oi_finder_chart_cache["NVDA"] = _ready_ganesh_cache("NVDA", [])
        complete = state.mag7_premarket_chart_signals_payload(
            pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
            refresh=False,
        )

    assert warming["status"] == "WARMING"
    assert warming["pendingSymbols"] == ["NVDA"]
    assert complete["status"] == "NO MATCHES"
    assert complete["pendingSymbols"] == []


def test_ready_mag7_signal_refresh_reuses_seed_and_recent_tail() -> None:
    state = DashboardState.__new__(DashboardState)
    state.oi_finder_chart_lock = threading.RLock()
    state.oi_finder_chart_cache = {"AMZN": _ready_ganesh_cache("AMZN", [])}
    state.oi_finder_chart_refreshes = set()
    recent_time = _signal_epoch("2026-08-03 09:10")
    recent_payload = {
        "symbol": "AMZN",
        "bars": [{"time": recent_time, "open": 101, "high": 102, "low": 100, "close": 102, "volume": 10}],
        "studyBars": [],
        "dailyBars": [],
        "historyLoading": True,
    }
    refreshed_tape = {
        "historyReady": True,
        "sourceAggregationMinutes": 240,
        "signals": [{"key": "fresh-macd", "family": "ganeshMacd"}],
    }

    with (
        patch.object(state, "_provider_cache_key", return_value="AMZN"),
        patch.object(state, "_build_oi_finder_chart_payload", return_value=recent_payload) as build_recent,
        patch("api_server.build_ganesh_higher_timeframe_signal_payload", return_value=refreshed_tape) as rebuild,
        patch("api_server.time.monotonic", return_value=200.0),
    ):
        refreshed = state._refresh_mag7_premarket_chart_signal_payload(
            "AMZN",
            as_of=pd.Timestamp("2026-08-03 10:00", tz="America/New_York").to_pydatetime(),
        )

    assert refreshed is True
    build_recent.assert_called_once_with("AMZN", fast_start=True)
    seed_arg, live_arg, daily_arg = rebuild.call_args.args
    premarket_cutoff_epoch = _signal_epoch("2026-08-03 09:30")
    expected_seed = [
        bar
        for bar in _ready_ganesh_cache("AMZN", [])["payload"]["studyBars"]
        if int(bar["time"]) < premarket_cutoff_epoch
    ]
    assert seed_arg == expected_seed
    assert live_arg == recent_payload["bars"]
    assert daily_arg == _ready_ganesh_cache("AMZN", [])["payload"]["dailyBars"]
    cached = state.oi_finder_chart_cache["AMZN"]
    assert cached["history_ready"] is True
    assert cached["study_cached_at"] == 100.0
    assert cached["payload"]["ganeshHigherTimeframeSignals"]["signals"] == []
    assert cached["payload"]["mag7PremarketChartSignalTape"]["signals"] == refreshed_tape["signals"]
    assert cached["payload"]["mag7PremarketChartSignalTape"]["sourceLastBarTime"] == recent_time


def test_ready_mag7_signal_tape_schedules_lightweight_recompute_not_full_promotion() -> None:
    state = DashboardState.__new__(DashboardState)
    state.mag7_premarket_chart_signal_refresh_enabled = True
    state.mag7_premarket_chart_signal_refresh_attempts = {}
    state.oi_finder_chart_full_refresh_lock = threading.Lock()

    with (
        patch.object(
            state,
            "_start_mag7_premarket_chart_signal_refresh",
            return_value=True,
        ) as start_lightweight,
        patch.object(state, "_start_oi_finder_chart_refresh") as start_full,
    ):
        scheduled = state._schedule_mag7_premarket_chart_signal_refresh(
            ["AMZN"],
            {"AMZN"},
            {"AMZN": "user:AMZN"},
            set(),
            500.0,
        )

    assert scheduled == "AMZN"
    start_lightweight.assert_called_once_with("AMZN")
    start_full.assert_not_called()
