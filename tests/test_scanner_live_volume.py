import threading
import unittest
from unittest.mock import patch

import pandas as pd

from indicators import volume_acceleration
from scanner import MomentumScanner, _group_mtf_call_signals, _tos_watchlist_mtf_signal_payload, ema_cloud_snapshot, fast_momentum_confirmation, price_change_scan, scan_live_4h_volume, scan_live_price_change, session_change_scan, volume_scan


class FakeTosScannerClient:
    def __init__(self, bars):
        self.bars = bars

    def get_chart_bars(self, _symbol, timeframe="1Min", days_back=5):
        return self.bars.copy()

    def get_daily_bars(self, symbols, lookback_days=260):
        daily = pd.DataFrame({
            "timestamp": pd.date_range("2026-06-01", periods=60, freq="1D", tz="America/New_York"),
            "open": [100.0] * 60,
            "high": [101.0] * 60,
            "low": [99.0] * 60,
            "close": [100.0] * 60,
            "volume": [1_000_000] * 60,
        })
        return {symbol: daily.copy() for symbol in symbols}


class MtfStockScannerGateTests(unittest.TestCase):
    @staticmethod
    def _aggregated_fixture(closes, frequency="2h"):
        timestamps = pd.date_range("2026-07-01 09:30", periods=len(closes), freq=frequency, tz="America/New_York")
        return pd.DataFrame({
            "timestamp": timestamps,
            "signal_time": timestamps,
            "open": closes,
            "high": [value + 0.5 for value in closes],
            "low": [value - 0.5 for value in closes],
            "close": closes,
            "volume": [1000] * len(closes),
        })

    def test_watchlist_yellow_requires_4x8_cross_inside_9x20_bull_trend(self):
        valid_yellow = [100.0, 101.19, 103.33, 102.95, 104.3, 104.72, 103.57, 101.84, 102.58, 100.04, 102.51, 100.38, 97.54, 95.18, 97.75, 96.82, 94.67, 91.84, 89.09, 90.25, 91.05, 92.24, 93.66, 91.05, 91.59, 90.77, 92.68, 94.6, 96.94, 94.34, 96.55, 99.03, 101.7, 99.34, 97.58, 95.25, 92.46, 94.54, 96.41, 97.22]
        invalid_cross = [100.0, 99.44, 97.83, 99.57, 99.32, 101.31, 100.56, 101.96, 99.13, 97.45, 100.21, 101.31, 102.36, 102.34, 102.18, 100.36, 98.4, 99.27, 100.43, 98.99, 99.85, 97.67, 98.35, 96.38, 96.44, 95.32, 95.62, 93.43, 93.33, 94.03, 91.84, 90.69, 91.76, 92.04, 92.74, 94.42, 94.85, 93.18, 92.83, 94.81]
        bullish_higher = self._aggregated_fixture(list(range(100, 140)), "4h")
        daily = self._aggregated_fixture(list(range(100, 140)), "1D")

        with patch("scanner._aggregate_mtf_bars", side_effect=[self._aggregated_fixture(valid_yellow), bullish_higher, daily]):
            valid = _tos_watchlist_mtf_signal_payload(pd.DataFrame())
        with patch("scanner._aggregate_mtf_bars", side_effect=[self._aggregated_fixture(invalid_cross), bullish_higher, daily]):
            invalid = _tos_watchlist_mtf_signal_payload(pd.DataFrame())

        self.assertEqual(valid["bullishSignalLabels"], ["CALL2H yellow"])
        self.assertFalse(invalid["bullishSignalPass"])

    def test_mtf_call_signals_are_returned_as_five_groups_with_combined_colors(self):
        grouped = _group_mtf_call_signals([
            {"label": "CALL2H", "color": "yellow"},
            {"label": "CALL2H", "color": "cyan"},
            {"label": "CALL4H", "color": "cyan"},
            {"label": "C2H", "color": "yellow"},
            {"label": "C4H", "color": "cyan"},
        ])

        self.assertEqual(grouped["labels"], [
            "BOTH CALL2H & CALL4H yellow+cyan",
            "CALL2H yellow+cyan",
            "C2H yellow",
            "C4H cyan",
        ])
        self.assertTrue(grouped["bothCall2H4H"])

    def test_call_group_requires_both_yellow_and_cyan(self):
        yellow_only = _group_mtf_call_signals([
            {"label": "CALL2H", "color": "yellow"},
        ])
        both_colors = _group_mtf_call_signals([
            {"label": "CALL2H", "color": "yellow"},
            {"label": "CALL2H", "color": "cyan"},
        ])

        self.assertEqual(yellow_only["groups"], [])
        self.assertEqual(both_colors["labels"], ["CALL2H yellow+cyan"])

    def test_both_call_timeframes_accepts_mixed_colors(self):
        grouped = _group_mtf_call_signals([
            {"label": "CALL2H", "color": "yellow"},
            {"label": "CALL4H", "color": "cyan"},
        ])

        self.assertEqual(grouped["labels"], ["BOTH CALL2H & CALL4H yellow+cyan"])
        self.assertTrue(grouped["bothCall2H4H"])

    def test_stock_scanner_returns_symbol_when_all_ten_conditions_pass(self):
        timestamps = pd.date_range("2026-07-13 04:00", periods=1920, freq="1min", tz="America/New_York")
        bars = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0] * len(timestamps),
            "high": [101.0] * len(timestamps),
            "low": [99.0] * len(timestamps),
            "close": ([100.0] * (len(timestamps) - 1)) + [101.0],
            "volume": [1000] * len(timestamps),
        })
        scanner = MomentumScanner(client=FakeTosScannerClient(bars))
        rvol = {"map": {}, "summary": "15m", "any_pass": True, "five_min_early_alert": False}
        cloud = {"state": "BULLISH", "bullish": True, "bearish": False, "ema_9": 100.8, "ema_21": 100.5, "ema_50": 100.0}
        momentum = {"score": 2, "status": "CONFIRMED", "previous_high_break_pass": True}
        bullish_payload = {
            "bullishSignalPass": True,
            "bullishSignalLabels": ["BOTH CALL2H & CALL4H yellow+cyan", "CALL2H yellow+cyan"],
            "bullishFamilies": ["4x8", "9x20"],
            "bullishTimeframes": ["2H", "4H"],
            "bullishBoth2H4H": True,
        }
        bearish_payload = {**bullish_payload, "bullishSignalPass": False, "bullishSignalLabels": []}

        with patch.object(scanner, "_tos_rvol_timeframe_map", return_value=rvol), patch(
            "scanner.scan_live_price_change",
            return_value={"signal": True, "price_change_pct": 0.6, "current_price": 100.6, "price_2_bars_ago": 100.0},
        ), patch(
            "scanner.scan_live_4h_volume",
            return_value={"signal": True, "volume_change_pct": 1.1, "current_volume": 1011, "volume_2_bars_ago": 1000},
        ), patch("scanner._tos_mtf_ema_signal_payload", return_value=bullish_payload), patch(
            "scanner.ema_cloud_snapshot", return_value=cloud,
        ), patch("scanner.fast_momentum_confirmation", return_value=momentum), patch(
            "scanner.cumulative_vwap", side_effect=lambda frame: pd.Series([99.0] * len(frame)),
        ):
            matched = scanner.run_tos_scan(["PLTR"])

        with patch.object(scanner, "_tos_rvol_timeframe_map", return_value=rvol), patch(
            "scanner._tos_mtf_ema_signal_payload",
            return_value=bearish_payload,
        ):
            blocked = scanner.run_tos_scan(["PLTR"])

        self.assertEqual(matched["symbol"].tolist(), ["PLTR"])
        self.assertEqual(matched.iloc[0]["mtf_bullish_signal_labels"], "BOTH CALL2H & CALL4H yellow+cyan, CALL2H yellow+cyan")
        self.assertTrue(matched.iloc[0]["mtf_bullish_signal_both_2h_4h"])
        self.assertTrue(matched.iloc[0]["one_hour_price_change_pass"])
        self.assertTrue(matched.iloc[0]["four_hour_volume_pass"])
        self.assertTrue(matched.iloc[0]["ema_vwap_5m_pass"])
        self.assertTrue(matched.iloc[0]["cloud_alignment_pass"])
        self.assertTrue(matched.iloc[0]["rvol_any_timeframe_pass"])
        self.assertTrue(matched.iloc[0]["fast_momentum_pass"])
        self.assertTrue(matched.iloc[0]["price_action_pass"])
        self.assertTrue(blocked.empty)

    def test_stock_scanner_requires_every_tos_all_condition(self):
        timestamps = pd.date_range("2026-07-13 04:00", periods=1920, freq="1min", tz="America/New_York")
        bars = pd.DataFrame({
            "timestamp": timestamps,
            "open": [100.0] * len(timestamps),
            "high": [101.0] * len(timestamps),
            "low": [99.0] * len(timestamps),
            "close": ([100.0] * (len(timestamps) - 1)) + [101.0],
            "volume": [1000] * len(timestamps),
        })
        scanner = MomentumScanner(client=FakeTosScannerClient(bars))
        rvol = {"map": {}, "summary": "15m", "any_pass": True, "five_min_early_alert": False}
        cloud = {"state": "BULLISH", "bullish": True, "bearish": False, "ema_9": 100.8, "ema_21": 100.5, "ema_50": 100.0}
        momentum = {"score": 2, "status": "CONFIRMED", "previous_high_break_pass": True}
        mtf = {"bullishSignalPass": True, "bullishSignalLabels": ["CALL2H yellow+cyan"]}
        passing_price = {"signal": True, "price_change_pct": 0.5, "current_price": 100.5, "price_2_bars_ago": 100.0}
        failing_volume = {"signal": False, "volume_change_pct": 0.9, "current_volume": 1009, "volume_2_bars_ago": 1000}

        with patch.object(scanner, "_tos_rvol_timeframe_map", return_value=rvol), patch(
            "scanner.scan_live_price_change", return_value=passing_price,
        ), patch("scanner.scan_live_4h_volume", return_value=failing_volume), patch(
            "scanner._tos_mtf_ema_signal_payload", return_value=mtf,
        ), patch("scanner.ema_cloud_snapshot", return_value=cloud), patch(
            "scanner.fast_momentum_confirmation", return_value=momentum,
        ), patch(
            "scanner.cumulative_vwap", side_effect=lambda frame: pd.Series([99.0] * len(frame)),
        ):
            result = scanner.run_tos_scan(["PLTR"])

        self.assertTrue(result.empty)


class FastMomentumConfirmationTests(unittest.TestCase):
    def test_live_forming_bar_passes_all_three_confirmations(self):
        bars = pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-07-10T10:00:00-04:00"), "high": 100.0, "low": 99.0, "close": 99.8, "volume": 100_000},
            {"timestamp": pd.Timestamp("2026-07-10T10:05:00-04:00"), "high": 101.0, "low": 99.8, "close": 100.9, "volume": 60_000},
        ])

        result = fast_momentum_confirmation(bars, now=pd.Timestamp("2026-07-10T10:07:00-04:00"))

        self.assertEqual(result["score"], 3)
        self.assertEqual(result["status"], "STRONG")
        self.assertEqual(result["volume_ratio"], 1.5)
        self.assertGreater(result["buying_pressure_pct"], 55.0)
        self.assertTrue(result["previous_high_break_pass"])

    def test_volume_alone_does_not_confirm_fast_momentum(self):
        bars = pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-07-10T10:00:00-04:00"), "high": 100.0, "low": 99.0, "close": 99.8, "volume": 100_000},
            {"timestamp": pd.Timestamp("2026-07-10T10:05:00-04:00"), "high": 100.0, "low": 98.0, "close": 98.5, "volume": 60_000},
        ])

        result = fast_momentum_confirmation(bars, now=pd.Timestamp("2026-07-10T10:07:00-04:00"))

        self.assertEqual(result["score"], 1)
        self.assertEqual(result["status"], "DEVELOPING")
        self.assertTrue(result["volume_pass"])
        self.assertFalse(result["buying_pressure_pass"])
        self.assertFalse(result["previous_high_break_pass"])

class LiveFourHourVolumeScannerTests(unittest.TestCase):
    def test_ema_cloud_snapshot_uses_latest_live_bar(self):
        bullish = pd.DataFrame({"close": [100.0 + (index * 0.5) for index in range(80)]})
        bearish = pd.DataFrame({"close": [140.0 - (index * 0.5) for index in range(80)]})

        bullish_cloud = ema_cloud_snapshot(bullish)
        bearish_cloud = ema_cloud_snapshot(bearish)

        self.assertEqual(bullish_cloud["state"], "BULLISH")
        self.assertTrue(bullish_cloud["bullish"])
        self.assertGreater(bullish_cloud["ema_9"], bullish_cloud["ema_21"])
        self.assertGreater(bullish_cloud["ema_21"], bullish_cloud["ema_50"])
        self.assertEqual(bearish_cloud["state"], "BEARISH")
        self.assertTrue(bearish_cloud["bearish"])

    def test_all_session_change_gate_requires_at_least_one_percent(self):
        self.assertFalse(session_change_scan(None, 1.0))
        self.assertFalse(session_change_scan(0.99, 1.0))
        self.assertTrue(session_change_scan(1.0, 1.0))
        self.assertTrue(session_change_scan(2.5, 1.0))

    def test_rvol_5m_is_early_only_and_30m_confirms_at_one(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})
        daily = intraday.copy()

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=[payload(2.2), payload(1.9), payload(2.1), payload(0.9), payload(0.8), payload(0.7), payload(0.6)],
        ):
            result = scanner._tos_rvol_timeframe_map("NVDA", intraday, daily)

        self.assertEqual(result["passing"], ["15m", "30m"])
        self.assertTrue(result["any_pass"])
        self.assertTrue(result["five_min_early_alert"])
        self.assertTrue(result["gate_pass"])
        self.assertEqual(result["summary"], "5m early, 15m, 30m")

    def test_negative_rvol_does_not_block_confirmed_result(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        fixture_values = iter([2.2, 2.2, 2.2, -0.1, 1.2, 1.1, 1.0])

        def fixture_scan(*_args, **_kwargs):
            if threading.current_thread() is not threading.main_thread():
                return payload(0.0)
            return payload(next(fixture_values))

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=fixture_scan,
        ):
            result = scanner._tos_rvol_timeframe_map("NVDA", intraday, intraday.copy())

        self.assertEqual(result["negative_timeframes"], [])
        self.assertTrue(result["negative_veto_pass"])
        self.assertTrue(result["any_pass"])
        self.assertTrue(result["five_min_early_alert"])
        self.assertTrue(result["gate_pass"])

    def test_negative_4h_or_daily_rvol_does_not_trigger_short_timeframe_veto(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=[payload(2.1), payload(2.1), payload(2.1), payload(1.1), payload(1.1), payload(-0.2), payload(-0.3)],
        ):
            result = scanner._tos_rvol_timeframe_map("NVDA", intraday, intraday.copy())

        self.assertEqual(result["negative_timeframes"], [])
        self.assertTrue(result["negative_veto_pass"])
        self.assertTrue(result["gate_pass"])

    def test_4h_rvol_alone_confirms_result(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})
        fixture_values = iter([0.2, 0.3, 0.4, 0.5, 0.6, 2.5, 0.7])

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        def fixture_scan(frame, **_kwargs):
            if frame.empty or "timestamp" not in frame.columns:
                return payload(0.0)
            frame_timestamps = pd.to_datetime(frame["timestamp"], errors="coerce")
            if frame_timestamps.empty or frame_timestamps.iloc[0].date().isoformat() != "2026-07-10":
                return payload(0.0)
            return payload(next(fixture_values))

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=fixture_scan,
        ):
            result = scanner._tos_rvol_timeframe_map("NVDA", intraday, intraday.copy())

        self.assertEqual(result["passing"], ["4h"])
        self.assertTrue(result["any_pass"])
        self.assertTrue(result["gate_pass"])

    def test_mag7_threshold_override_confirms_at_point_four(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=[payload(0.1), payload(0.45), payload(0.2), payload(0.3), payload(0.2), payload(0.1), payload(0.1), payload(0.1)],
        ):
            mag7 = scanner._tos_rvol_timeframe_map("AAPL", intraday, intraday.copy(), confirmation_threshold=0.4)

        self.assertEqual(mag7["confirmation_threshold"], 0.4)
        self.assertEqual(mag7["passing"], ["15m"])
        self.assertTrue(mag7["gate_pass"])

    def test_negative_5m_rvol_does_not_block_confirmed_timeframe(self):
        scanner = MomentumScanner(client=object())
        timestamps = pd.to_datetime(["2026-07-10 10:00:00"], utc=True).tz_convert("America/New_York")
        intraday = pd.DataFrame({"timestamp": timestamps, "volume": [100], "open": [10], "high": [11], "low": [9], "close": [10.8]})

        def payload(value):
            return {"raw_rel_vol": value, "buying_gt_selling": True, "signal": value >= 1.0}

        fixture_values = iter([-1.2, 2.1, 2.2, 1.1, 1.1, 0.5, 0.4])

        def fixture_scan(*_args, **_kwargs):
            if threading.current_thread() is not threading.main_thread():
                return payload(0.0)
            return payload(next(fixture_values))

        with patch(
            "scanner.tos_relative_volume_scan",
            side_effect=fixture_scan,
        ):
            result = scanner._tos_rvol_timeframe_map("AAPL", intraday, intraday.copy())

        self.assertEqual(result["negative_timeframes"], [])
        self.assertTrue(result["negative_veto_pass"])
        self.assertTrue(result["any_pass"])
        self.assertTrue(result["gate_pass"])

    def test_volume_acceleration_uses_current_bar_vs_previous_bar(self):
        increasing = pd.DataFrame({"volume": [100_000, 125_000]})
        decreasing = pd.DataFrame({"volume": [125_000, 100_000]})

        self.assertTrue(volume_acceleration(increasing, bars=2))
        self.assertFalse(volume_acceleration(decreasing, bars=2))

    def test_volume_scan_matches_tos_formula(self):
        self.assertTrue(volume_scan(1_050_000, 1_000_000))
        self.assertFalse(volume_scan(1_004_000, 1_000_000))

    def test_scan_live_4h_volume_uses_current_bar_and_two_bars_ago(self):
        bars = [
            {"volume": 1_000_000},
            {"volume": 500_000},
            {"volume": 1_006_000},
        ]

        result = scan_live_4h_volume("TSLA", bars)

        self.assertEqual(result["ticker"], "TSLA")
        self.assertEqual(result["current_volume"], 1_006_000)
        self.assertEqual(result["volume_2_bars_ago"], 1_000_000)
        self.assertEqual(result["volume_change_pct"], 0.6)
        self.assertTrue(result["signal"])

    def test_price_change_scan_matches_tos_formula(self):
        self.assertTrue(price_change_scan(100.50, 100.00, threshold_pct=0.5))
        self.assertTrue(price_change_scan(100.60, 100.00, threshold_pct=0.5))
        self.assertFalse(price_change_scan(100.40, 100.00, threshold_pct=0.5))

    def test_scan_live_price_change_uses_current_bar_and_two_bars_ago(self):
        bars = [
            {"close": 100.0},
            {"close": 99.0},
            {"close": 100.6},
        ]

        result = scan_live_price_change("TSLA", bars, threshold_pct=0.5)

        self.assertEqual(result["ticker"], "TSLA")
        self.assertEqual(result["current_price"], 100.6)
        self.assertEqual(result["price_2_bars_ago"], 100.0)
        self.assertEqual(result["price_change_pct"], 0.6)
        self.assertTrue(result["signal"])

    def test_stock_signal_changes_uses_latest_forming_1h_4h_candles(self):
        scanner = MomentumScanner(client=object())
        bars = pd.DataFrame(
            [
                {"timestamp": pd.Timestamp("2026-07-06 04:00:00", tz="America/New_York"), "open": 100, "high": 101, "low": 99, "close": 100.0, "volume": 1_000_000},
                {"timestamp": pd.Timestamp("2026-07-06 08:00:00", tz="America/New_York"), "open": 100, "high": 101, "low": 99, "close": 100.1, "volume": 500_000},
                {"timestamp": pd.Timestamp("2026-07-06 12:00:00", tz="America/New_York"), "open": 100, "high": 101, "low": 99, "close": 100.6, "volume": 1_011_000},
            ]
        )

        result = scanner._stock_signal_changes("TSLA", bars)
        one_hour_result = result["one_hour_price"]
        four_hour_result = result["four_hour_price"]
        volume_result = result["four_hour_volume"]

        self.assertEqual(one_hour_result["current_price"], 100.6)
        self.assertEqual(one_hour_result["price_change_pct"], 0.6)
        self.assertTrue(one_hour_result["signal"])
        self.assertEqual(four_hour_result["current_price"], 100.6)
        self.assertEqual(four_hour_result["price_change_pct"], 0.6)
        self.assertTrue(four_hour_result["signal"])
        self.assertEqual(volume_result["current_volume"], 1_011_000)
        self.assertEqual(volume_result["volume_2_bars_ago"], 1_000_000)
        self.assertEqual(volume_result["volume_change_pct"], 1.1)
        self.assertTrue(volume_result["signal"])


if __name__ == "__main__":
    unittest.main()
