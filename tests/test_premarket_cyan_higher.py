from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from premarket_scanner import (
    MACD_HIGHER_FAMILY,
    YELLOW_HIGHER_FAMILY,
    overnight_window,
    score_strength,
    window_cyan_higher_signals,
    window_higher_signals,
)

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 24, 6, 30, tzinfo=ET)  # Monday 06:30 ET


def _epoch(hour: int, minute: int = 0) -> int:
    return int(NOW.replace(hour=hour, minute=minute, second=0, microsecond=0).timestamp())


def _signal(label: str, time: int, *, family: str = "ganesh920",
            direction: str = "CALL") -> dict:
    return {"label": label, "time": time, "family": family, "direction": direction}


class OvernightWindowTests(unittest.TestCase):
    def test_window_spans_midnight_to_930(self) -> None:
        start, end = overnight_window(NOW)
        self.assertEqual(start, _epoch(0))
        self.assertEqual(end, _epoch(9, 30))


class WindowCyanHigherTests(unittest.TestCase):
    def test_one_am_calld_is_in_window(self) -> None:
        start, end = overnight_window(NOW)
        kept = window_cyan_higher_signals([_signal("CALLD", _epoch(1))], start, end)
        self.assertEqual([s["label"] for s in kept], ["CALLD"])

    def test_wrong_family_direction_and_window_are_excluded(self) -> None:
        start, end = overnight_window(NOW)
        kept = window_cyan_higher_signals([
            _signal("CALLD", _epoch(1), family="ganesh48"),        # yellow D-M: not cyan
            _signal("MACD-D", _epoch(1), family="ganeshMacd"),      # macd family
            _signal("PUTD", _epoch(1), direction="PUT"),            # wrong direction
            _signal("CALLW", _epoch(23)),                           # after 09:30
            _signal("CALLM", start - 60),                           # yesterday
            _signal("", _epoch(2)),                                 # no label
        ], start, end)
        self.assertEqual(kept, [])
        self.assertEqual(window_cyan_higher_signals(None, start, end), [])

    def test_repeat_crossings_keep_earliest_only(self) -> None:
        start, end = overnight_window(NOW)
        kept = window_cyan_higher_signals([
            _signal("CALLD", _epoch(3)),
            _signal("CALLD", _epoch(1)),
            _signal("CALLW", _epoch(2)),
        ], start, end)
        self.assertEqual([(s["label"], s["time"]) for s in kept],
                         [("CALLD", _epoch(1)), ("CALLW", _epoch(2))])


class ScoreWithHigherCyanTests(unittest.TestCase):
    CYAN_2H = {"family": "9x20", "label": "CALL2H"}
    YELLOW_4H = {"family": "4x8", "label": "CALL4H"}
    CALLD = {"family": "ganesh920", "label": "CALLD", "time": 0}

    def test_calld_alone_is_moderate(self) -> None:
        score, tier = score_strength([], [], higher_cyan=[self.CALLD])
        self.assertEqual((score, tier), (1, "MODERATE"))

    def test_calld_plus_yellow_is_strong(self) -> None:
        score, tier = score_strength([self.YELLOW_4H], [], higher_cyan=[self.CALLD])
        self.assertEqual((score, tier), (2, "STRONG"))

    def test_calld_plus_intraday_cyan_is_strong(self) -> None:
        score, tier = score_strength([self.CYAN_2H], [], higher_cyan=[self.CALLD])
        self.assertEqual((score, tier), (2, "STRONG"))

    def test_calld_with_two_fires_lifts_to_strong(self) -> None:
        score, tier = score_strength([], ["1h", "2h"], higher_cyan=[self.CALLD])
        self.assertEqual((score, tier), (3, "STRONG"))

    def test_existing_behaviour_unchanged_without_higher(self) -> None:
        self.assertEqual(score_strength([self.YELLOW_4H], []), (1, "WEAK"))
        self.assertEqual(score_strength([], ["1h"]), (1, "WEAK"))
        self.assertEqual(score_strength([], ["1h", "4h"]), (2, "MODERATE"))


class OtherHigherFamiliesTests(unittest.TestCase):
    YELLOW_D = {"family": YELLOW_HIGHER_FAMILY, "label": "CALLD", "time": 0}
    MACD_D = {"family": MACD_HIGHER_FAMILY, "label": "MACD-D", "time": 0}
    CALLD = {"family": "ganesh920", "label": "CALLD", "time": 0}

    def test_window_filter_per_family(self) -> None:
        start, end = overnight_window(NOW)
        signals = [
            _signal("CALLD", _epoch(1), family=YELLOW_HIGHER_FAMILY),
            _signal("MACD-D", _epoch(2), family=MACD_HIGHER_FAMILY),
            _signal("CALLD", _epoch(3)),  # cyan
        ]
        self.assertEqual([s["label"] for s in window_higher_signals(signals, start, end, YELLOW_HIGHER_FAMILY)], ["CALLD"])
        self.assertEqual([s["label"] for s in window_higher_signals(signals, start, end, MACD_HIGHER_FAMILY)], ["MACD-D"])

    def test_yellow_higher_counts_as_yellow(self) -> None:
        # One yellow D-M alone = WEAK (a single yellow), two yellows = MODERATE.
        self.assertEqual(score_strength([], [], higher_yellow=[self.YELLOW_D]), (1, "WEAK"))
        score, tier = score_strength([], [], higher_cyan=[self.CALLD], higher_yellow=[self.YELLOW_D])
        self.assertEqual((score, tier), (2, "STRONG"))  # cyan + yellow agree

    def test_macd_raises_score_but_never_the_tier(self) -> None:
        self.assertEqual(score_strength([], [], higher_macd=[self.MACD_D]), (1, "WEAK"))
        score, tier = score_strength([], [], higher_cyan=[self.CALLD], higher_macd=[self.MACD_D])
        self.assertEqual((score, tier), (2, "MODERATE"))  # still a lone cyan tier-wise

    def test_macd_pair_is_fire_like_moderate(self) -> None:
        macd_w = {"family": MACD_HIGHER_FAMILY, "label": "MACD-W", "time": 0}
        self.assertEqual(score_strength([], [], higher_macd=[self.MACD_D, macd_w]), (2, "MODERATE"))


if __name__ == "__main__":
    unittest.main()
