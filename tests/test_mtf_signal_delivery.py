from __future__ import annotations

import pandas as pd

from api_server import _merge_chart_and_watchlist_mtf_signals, _merge_live_mtf_history


def _stamp(value: str) -> int:
    return int(pd.Timestamp(value, tz="America/New_York").timestamp())


def test_watchlist_signal_fills_missing_same_session_higher_timeframe_signal() -> None:
    fallback = {
        "time": _stamp("2026-07-31 13:10"),
        "family": "4x8",
        "timeframe": "2H",
        "direction": "CALL",
        "label": "C2H",
        "color": "yellow",
    }

    merged = _merge_chart_and_watchlist_mtf_signals([], [fallback])

    assert merged == [{**fallback, "compact": True, "watchlistFallback": True}]


def test_watchlist_signal_does_not_duplicate_chart_signal_on_same_market_date() -> None:
    chart_signal = {
        "time": _stamp("2026-07-31 10:00"),
        "family": "9x20",
        "timeframe": "4H",
        "direction": "CALL",
        "label": "CALL4H",
        "color": "cyan",
    }
    fallback = {**chart_signal, "time": _stamp("2026-07-31 13:10")}

    assert _merge_chart_and_watchlist_mtf_signals([chart_signal], [fallback]) == [chart_signal]


def test_watchlist_signal_is_kept_for_a_new_market_date() -> None:
    prior_signal = {
        "time": _stamp("2026-07-30 10:00"),
        "family": "4x8",
        "timeframe": "4H",
        "direction": "CALL",
        "label": "CALL4H",
        "color": "yellow",
    }
    fallback = {**prior_signal, "time": _stamp("2026-07-31 13:10")}

    merged = _merge_chart_and_watchlist_mtf_signals([prior_signal], [fallback])

    assert len(merged) == 2
    assert merged[-1]["watchlistFallback"] is True


def test_live_primary_stream_replaces_stale_cached_mtf_candle() -> None:
    cached_times = pd.date_range("2026-07-31 09:55", periods=2, freq="5min", tz="America/New_York")
    cached = pd.DataFrame({
        "timestamp": cached_times,
        "open": [100.0, 100.0],
        "high": [100.0, 100.0],
        "low": [100.0, 100.0],
        "close": [100.0, 100.0],
        "volume": [500, 500],
    })
    live_times = pd.date_range("2026-07-31 10:00", periods=7, freq="1min", tz="America/New_York")
    live = pd.DataFrame({
        "timestamp": live_times,
        "open": [101.0] * 7,
        "high": [102.0] * 7,
        "low": [100.5] * 7,
        "close": [101.0, 101.2, 101.4, 101.6, 101.8, 102.0, 102.2],
        "volume": [100] * 7,
    })

    merged = _merge_live_mtf_history(cached, live)

    at_ten = merged[merged["timestamp"] == pd.Timestamp("2026-07-31 10:00", tz="America/New_York")].iloc[0]
    at_ten_oh_five = merged[merged["timestamp"] == pd.Timestamp("2026-07-31 10:05", tz="America/New_York")].iloc[0]
    assert at_ten["close"] == 101.8
    assert at_ten["volume"] == 500
    assert at_ten_oh_five["close"] == 102.2
    assert at_ten_oh_five["volume"] == 200
