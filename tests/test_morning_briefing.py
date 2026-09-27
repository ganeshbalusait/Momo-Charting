from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from morning_briefing import (
    build_briefing,
    market_line,
    mover_lines,
    scanner_lines,
    watch_line,
)

NOW = datetime(2026, 8, 24, 6, 5, tzinfo=ZoneInfo("America/New_York"))


def _row(symbol: str, strength: str, score: int, *, cyan=None, yellow=None,
         fires=None, forming=False, catalyst=None) -> dict:
    return {
        "symbol": symbol, "strength": strength, "score": score,
        "signals920": cyan or [], "signals48": yellow or [],
        "fires": fires or [], "forming": forming, "catalyst": catalyst,
    }


class MarketLineTests(unittest.TestCase):
    def test_lean_words_follow_the_average(self) -> None:
        self.assertEqual(
            market_line({"SPY": {"change_pct": 0.4}, "QQQ": {"change_pct": 0.6}}),
            "SPY +0.4%, QQQ +0.6% premarket - tape leans bullish.",
        )
        self.assertIn("bearish", market_line({"SPY": {"change_pct": -0.8}}))
        self.assertIn("flat", market_line({"SPY": {"change_pct": 0.1}, "QQQ": {"change_pct": -0.1}}))
        self.assertIsNone(market_line({}))
        self.assertIsNone(market_line({"SPY": {"change_pct": None}}))


class ScannerLinesTests(unittest.TestCase):
    def test_strongest_row_gets_the_full_sentence(self) -> None:
        payload = {"rows": [
            _row("NVDA", "STRONG", 5, cyan=["CALL2H"], yellow=["CALL4H"], fires=["1h"],
                 catalyst={"tag": "AI/PRODUCT", "headline": "Price Hikes Above 15%", "ageMinutes": 180}),
            _row("META", "MODERATE", 2, cyan=["CALL2H"], forming=True),
        ]}
        lines = scanner_lines(payload)
        self.assertIn("Strongest setup: NVDA", lines[0])
        self.assertIn("CALL2H cyan", lines[0])
        self.assertIn("CALL4H yellow", lines[0])
        self.assertIn("fire 1h", lines[0])
        self.assertIn("STRONG (5)", lines[0])
        self.assertIn("Catalyst - AI/PRODUCT: Price Hikes Above 15% (3h ago)", lines[0])
        self.assertEqual(lines[1], "Also set up: META (MODERATE 2, forming).")

    def test_no_rows_says_so_plainly(self) -> None:
        self.assertEqual(scanner_lines({"rows": []}),
                         ["No signals on the nine scanner tickers yet."])
        self.assertEqual(scanner_lines(None),
                         ["No signals on the nine scanner tickers yet."])

    def test_missing_catalyst_is_stated_not_invented(self) -> None:
        lines = scanner_lines({"rows": [_row("AAPL", "WEAK", 1, fires=["1h"])]})
        self.assertIn("No fresh catalyst found.", lines[0])


# Tickers are wrapped in ** ** so the briefing UI can bold them (trader ask,
# 2026-08-27). The marker is part of the contract these tests pin.
class MoverLinesTests(unittest.TestCase):
    QUOTES = {
        "SMCI": {"change_pct": 6.2}, "MARA": {"change_pct": 4.1},
        "COIN": {"change_pct": 3.8}, "HOOD": {"change_pct": 2.4},
        "BYND": {"change_pct": -5.1}, "PLUG": {"change_pct": -2.7},
        "AAPL": {"change_pct": 0.4},       # under threshold
        "SPY": {"change_pct": 3.0},        # index, excluded
        "NVDA": {"change_pct": 9.0},       # excluded: already a scanner row
    }

    def test_top_movers_ranked_thresholded_and_excluded(self) -> None:
        lines = mover_lines(self.QUOTES, {"SMCI": {"tag": "EARNINGS", "headline": "Beats", "ageMinutes": 60}},
                            exclude={"NVDA"})
        self.assertEqual(lines[0],
                         "Watchlist gainers: **SMCI** +6.2% (EARNINGS: Beats (1h ago)), **MARA** +4.1%, **COIN** +3.8%.")
        self.assertEqual(lines[1], "Watchlist losers: **BYND** -5.1%, **PLUG** -2.7%.")

    def test_quiet_tape_yields_no_lines(self) -> None:
        self.assertEqual(mover_lines({"AAPL": {"change_pct": 0.2}}), [])


class WatchLineTests(unittest.TestCase):
    def test_lead_row_with_catalyst_wins(self) -> None:
        payload = {"rows": [_row("NVDA", "STRONG", 5,
                                 catalyst={"headline": "Price Hikes", "tag": "NEWS"})]}
        self.assertEqual(watch_line(payload, {}),
                         "Watch NVDA at the open - signals and news agree.")

    def test_lead_row_without_catalyst_is_flagged(self) -> None:
        payload = {"rows": [_row("META", "MODERATE", 2)]}
        self.assertIn("no news behind the move yet", watch_line(payload, {}))

    def test_falls_back_to_biggest_mover_then_quiet(self) -> None:
        self.assertIn("BYND -5.1%", watch_line({"rows": []}, {"BYND": {"change_pct": -5.1}}))
        self.assertIn("Quiet tape", watch_line({"rows": []}, {"AAPL": {"change_pct": 0.1}}))


class BuildBriefingTests(unittest.TestCase):
    def test_assembles_all_sections_in_order(self) -> None:
        payload = {"rows": [_row("NVDA", "STRONG", 5, cyan=["CALL2H"],
                                 catalyst={"headline": "Hikes", "tag": "NEWS", "ageMinutes": 30})]}
        quotes = {"SPY": {"change_pct": 0.5}, "QQQ": {"change_pct": 0.7},
                  "SMCI": {"change_pct": 6.2}, "NVDA": {"change_pct": 8.0}}
        briefing = build_briefing(payload, quotes, NOW)
        self.assertEqual(briefing["date"], "2026-08-24")
        lines = briefing["lines"]
        self.assertIn("tape leans bullish", lines[0])
        self.assertIn("Strongest setup: NVDA", lines[1])
        # NVDA is a scanner row, so the movers line must not repeat it.
        self.assertEqual(lines[2], "Watchlist gainers: **SMCI** +6.2%.")
        self.assertEqual(lines[3], "Watch NVDA at the open - signals and news agree.")


if __name__ == "__main__":
    unittest.main()
