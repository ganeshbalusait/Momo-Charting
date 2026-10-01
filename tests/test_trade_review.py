from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trade_review import build_trade_review, judge_trade

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 24, 16, 5, tzinfo=ET)

# 06:40 ET on 2026-08-24 as epoch (10:40 UTC).
CALL_TIME = datetime(2026, 8, 24, 10, 40, tzinfo=timezone.utc).timestamp()
SIGNALS = {"calls": [{"label": "CALL2H", "time": CALL_TIME}],
           "fires": [{"label": "1h", "closeTime": CALL_TIME - 600}]}


def _trade(symbol="NVDA", *, entry_utc="2026-08-24T10:52:00+00:00",
           exit_utc="2026-08-24T11:30:00+00:00", pnl=240.0) -> dict:
    return {"symbol": symbol, "opened_at": entry_utc, "closed_at": exit_utc, "pnl": pnl}


class JudgeTradeTests(unittest.TestCase):
    def test_entry_after_call_is_backed_with_lag(self) -> None:
        verdict = judge_trade(_trade(), SIGNALS)
        self.assertTrue(verdict["backed"])
        self.assertEqual(verdict["line"],
                         "NVDA: in 06:52 (12m after CALL2H), out 07:30, +$240 - winner.")

    def test_entry_before_any_signal_is_unbacked(self) -> None:
        verdict = judge_trade(_trade(entry_utc="2026-08-24T10:00:00+00:00", pnl=-120.0), SIGNALS)
        self.assertFalse(verdict["backed"])
        self.assertIn("no signal behind the entry", verdict["line"])
        self.assertIn("-$120 - loser", verdict["line"])

    def test_entry_hours_after_signal_is_unbacked(self) -> None:
        verdict = judge_trade(_trade(entry_utc="2026-08-24T14:00:00+00:00"), SIGNALS)
        self.assertFalse(verdict["backed"])  # 3h20m > the 2h backing window

    def test_fire_only_backing_is_labelled_as_such(self) -> None:
        verdict = judge_trade(
            _trade(entry_utc="2026-08-24T10:35:00+00:00"),
            {"calls": [], "fires": SIGNALS["fires"]},
        )
        self.assertTrue(verdict["backed"])
        self.assertIn("fire only - no CALL", verdict["line"])

    def test_open_trade_and_junk_rows(self) -> None:
        verdict = judge_trade(_trade(exit_utc=None, pnl=None), SIGNALS)
        self.assertTrue(verdict["open"])
        self.assertIn("still open", verdict["line"])
        self.assertIsNone(judge_trade({"symbol": "", "opened_at": "junk"}, SIGNALS))
        self.assertIsNone(judge_trade(None, SIGNALS))


class BuildTradeReviewTests(unittest.TestCase):
    def test_summary_and_pattern_lines(self) -> None:
        trades = [
            _trade(),                                                        # backed winner
            _trade(entry_utc="2026-08-24T10:00:00+00:00", pnl=-120.0),        # unbacked loser
            _trade(symbol="META", exit_utc=None, pnl=None),                   # open, no META signals
        ]
        review = build_trade_review(trades, {"NVDA": SIGNALS}, NOW)
        lines = review["lines"]
        self.assertEqual(lines[0], "3 trades today: 1 winner, 1 loser, 1 still open, net +$120.")
        self.assertIn("Pattern: with-signal trades netted +$240 (1), "
                      "no-signal trades netted -$120 (1).", lines)
        self.assertIn("The losses came from trades the scanner never called.", lines)

    def test_no_trades_says_so(self) -> None:
        review = build_trade_review([], {}, NOW)
        self.assertEqual(review["lines"], ["No trades in the journal today."])
        self.assertEqual(review["trades"], [])

    def test_all_unbacked_day_is_called_out(self) -> None:
        review = build_trade_review(
            [_trade(entry_utc="2026-08-24T10:00:00+00:00", pnl=-50.0)],
            {"NVDA": SIGNALS},
            NOW,
        )
        self.assertIn("None of today's closed trades had a signal behind the entry.",
                      review["lines"])


if __name__ == "__main__":
    unittest.main()
