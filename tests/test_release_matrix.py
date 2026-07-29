from __future__ import annotations

import unittest

import pandas as pd

from scanner import (
    fast_momentum_confirmation,
    scan_live_4h_volume,
    scan_live_price_change,
    session_change_scan,
    tos_price_change_scan,
    tos_price_change_value,
)


class ReleaseBoundaryMatrixTests(unittest.TestCase):
    """Generated boundary cases that remain individually visible to unittest."""


def _install_test(name, callback):
    callback.__name__ = name
    setattr(ReleaseBoundaryMatrixTests, name, callback)


def _greater_case(index):
    def test(self):
        reference = 3.0 + (index * 7.25)
        threshold = [0.25, 0.5, 1.0, 1.5, 2.0][index % 5]
        offset = [-0.01, 0.0, 0.01, 0.25][index % 4]
        actual_change = threshold + offset
        current = reference * (1.0 + (actual_change / 100.0))
        self.assertEqual(
            tos_price_change_scan(current, reference, threshold, "greater"),
            actual_change >= threshold,
        )
        self.assertAlmostEqual(tos_price_change_value(current, reference), actual_change, places=7)

    return test


def _less_case(index):
    def test(self):
        reference = 5.0 + (index * 11.75)
        threshold = [0.25, 0.5, 1.0, 1.5, 3.0][index % 5]
        offset = [-0.01, 0.0, 0.01, 0.5][index % 4]
        decline = threshold + offset
        current = reference * (1.0 - (decline / 100.0))
        self.assertEqual(
            tos_price_change_scan(current, reference, threshold, "less"),
            decline >= threshold,
        )

    return test


def _live_price_case(index):
    def test(self):
        reference = 3.0 + (index * 2.5)
        threshold = [0.25, 0.5, 1.0, 2.0][index % 4]
        offset = [-0.01, 0.0, 0.01, 0.4][index % 4]
        actual_change = threshold + offset
        current = reference * (1.0 + (actual_change / 100.0))
        bars = [{"close": reference}, {"close": reference * 0.5}, {"close": current}]
        result = scan_live_price_change(f"px{index}", bars, threshold)
        self.assertEqual(result["ticker"], f"PX{index}")
        self.assertAlmostEqual(result["price_2_bars_ago"], reference, places=4)
        self.assertAlmostEqual(result["current_price"], current, places=4)
        self.assertEqual(result["signal"], actual_change >= threshold)

    return test


def _live_volume_case(index):
    def test(self):
        reference = 1_000 + (index * 19_123)
        threshold = [0.25, 0.5, 1.0, 2.0][index % 4]
        offset = [-0.01, 0.0, 0.01, 0.75][index % 4]
        actual_change = threshold + offset
        current = reference * (1.0 + (actual_change / 100.0))
        bars = [{"volume": reference}, {"volume": 1}, {"volume": current}]
        result = scan_live_4h_volume(f"vol{index}", bars, threshold)
        self.assertEqual(result["ticker"], f"VOL{index}")
        self.assertEqual(result["volume_2_bars_ago"], reference)
        self.assertAlmostEqual(float(result["current_volume"]), current, places=5)
        self.assertEqual(result["signal"], actual_change >= threshold)

    return test


def _session_case(index):
    def test(self):
        threshold = [0.5, 1.0, 1.5, 2.0][index % 4]
        offset = [-0.01, 0.0, 0.01, 1.0][index % 4]
        change = threshold + offset
        self.assertEqual(session_change_scan(change, threshold), change >= threshold)
        if index % 10 == 0:
            self.assertFalse(session_change_scan(None, threshold))

    return test


def _momentum_case(index):
    def test(self):
        volume_pass_expected = index % 2 == 0
        pressure_pass_expected = index % 4 in (0, 1)
        breakout_pass_expected = index % 5 in (0, 1, 2)
        previous_volume = 100_000.0
        current_volume = 60_000.0 if volume_pass_expected else 35_000.0
        low = 99.0
        high = 102.0
        close = 101.0 if pressure_pass_expected else 100.0
        previous_high = 99.9 if breakout_pass_expected else 102.0
        bars = pd.DataFrame([
            {"timestamp": pd.Timestamp("2026-07-10T10:00:00-04:00"), "high": previous_high, "low": 99.0, "close": 99.8, "volume": previous_volume},
            {"timestamp": pd.Timestamp("2026-07-10T10:05:00-04:00"), "high": high, "low": low, "close": close, "volume": current_volume},
        ])
        result = fast_momentum_confirmation(
            bars,
            now=pd.Timestamp("2026-07-10T10:07:00-04:00"),
            breakout_buffer_pct=0.05,
        )
        expected = int(volume_pass_expected) + int(pressure_pass_expected) + int(breakout_pass_expected)
        self.assertEqual(result["volume_pass"], volume_pass_expected)
        self.assertEqual(result["buying_pressure_pass"], pressure_pass_expected)
        self.assertEqual(result["previous_high_break_pass"], breakout_pass_expected)
        self.assertEqual(result["score"], expected)

    return test


for _index in range(100):
    _install_test(f"test_greater_boundary_{_index:03d}", _greater_case(_index))
    _install_test(f"test_less_boundary_{_index:03d}", _less_case(_index))
    _install_test(f"test_live_price_bar_{_index:03d}", _live_price_case(_index))
    _install_test(f"test_live_volume_bar_{_index:03d}", _live_volume_case(_index))
    _install_test(f"test_session_change_{_index:03d}", _session_case(_index))
    _install_test(f"test_fast_momentum_{_index:03d}", _momentum_case(_index))
