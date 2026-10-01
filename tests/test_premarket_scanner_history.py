from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import premarket_scanner_history as history
from premarket_scanner_history import RETENTION_DAYS, load_history, record_rows

ET = ZoneInfo("America/New_York")
MORNING = datetime(2026, 8, 24, 6, 12, tzinfo=ET)
LATER = datetime(2026, 8, 24, 8, 45, tzinfo=ET)


def _row(symbol: str = "NVDA", strength: str = "MODERATE") -> dict:
    return {"symbol": symbol, "strength": strength, "score": 1,
            "signalsCyanHigher": ["CALLD"], "signals48": [], "signals920": [], "fires": []}


class RecordRowsTests(unittest.TestCase):
    def test_first_seen_survives_later_updates(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assertTrue(record_rows([_row()], MORNING, directory))
            # The row strengthens later: snapshot updates, arrival stamp does not.
            self.assertTrue(record_rows([_row(strength="STRONG")], LATER, directory))
            days = load_history(directory)
            self.assertEqual(len(days), 1)
            entry = days[0]["rows"][0]
            self.assertEqual(entry["symbol"], "NVDA")
            self.assertEqual(entry["firstSeenAt"], MORNING.isoformat())
            self.assertEqual(entry["lastSeenAt"], LATER.isoformat())
            self.assertEqual(entry["row"]["strength"], "STRONG")

    def test_unchanged_rows_do_not_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_rows([_row()], MORNING, directory)
            self.assertFalse(record_rows([_row()], LATER, directory))
            self.assertFalse(record_rows([], LATER, directory))
            self.assertFalse(record_rows(None, LATER, directory))

    def test_days_are_separate_and_ordered_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_rows([_row("AAPL")], MORNING - timedelta(days=1), directory)
            record_rows([_row("NVDA")], MORNING, directory)
            days = load_history(directory)
            self.assertEqual([d["date"] for d in days], ["2026-08-24", "2026-08-23"])

    def test_retention_prunes_old_days(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            old = MORNING - timedelta(days=RETENTION_DAYS + 3)
            record_rows([_row("OLD")], old, directory)
            record_rows([_row("NEW")], MORNING, directory)
            names = {p.stem for p in directory.glob("*.json")}
            self.assertNotIn(old.date().isoformat(), names)
            self.assertIn(MORNING.date().isoformat(), names)

    def test_corrupt_file_is_replaced_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            directory.joinpath(f"{MORNING.date().isoformat()}.json").write_text("{not json", encoding="utf-8")
            self.assertTrue(record_rows([_row()], MORNING, directory))
            self.assertEqual(len(load_history(directory)[0]["rows"]), 1)


if __name__ == "__main__":
    unittest.main()


class BriefingArchiveTests(unittest.TestCase):
    """The morning briefing is archived beside the day's rows.

    Trader ask 2026-08-27: show the morning brief under the scanner-history
    table. The interesting case is not the write, it is that record_rows used
    to rebuild the day document as {"date", "rows"} and therefore ERASED the
    briefing on the very next row write, a few minutes later, silently.
    """

    def _now(self, day="2026-08-27"):
        y, m, d = (int(part) for part in day.split("-"))
        return datetime(y, m, d, 8, 12, tzinfo=ZoneInfo("America/New_York"))

    def test_briefing_round_trips_with_bold_markers(self):
        with tempfile.TemporaryDirectory() as directory:
            history.record_briefing(
                ["Watchlist gainers: **OKTA** +17.3%."], self._now(), directory
            )
            day = history.load_history(directory)[0]
            self.assertEqual(day["briefing"]["lines"], ["Watchlist gainers: **OKTA** +17.3%."])

    def test_recording_rows_later_does_not_wipe_the_briefing(self):
        with tempfile.TemporaryDirectory() as directory:
            now = self._now()
            history.record_briefing(["Tape leans bullish."], now, directory)
            history.record_rows([{"symbol": "MSFT"}], now, directory)
            day = history.load_history(directory)[0]
            self.assertTrue(day["briefing"], "record_rows erased the archived briefing")
            self.assertEqual([row["symbol"] for row in day["rows"]], ["MSFT"])

    def test_a_day_with_only_a_briefing_is_still_returned(self):
        # A quiet morning matches nothing but still produces a brief. Dropping
        # that day would hide the briefing exactly when it is most worth reading.
        with tempfile.TemporaryDirectory() as directory:
            history.record_briefing(["Quiet tape - no signals."], self._now(), directory)
            days = history.load_history(directory)
            self.assertEqual(len(days), 1)
            self.assertEqual(days[0]["rows"], [])
            self.assertTrue(days[0]["briefing"])

    def test_identical_briefing_is_not_rewritten(self):
        with tempfile.TemporaryDirectory() as directory:
            now = self._now()
            self.assertTrue(history.record_briefing(["Same."], now, directory))
            self.assertFalse(history.record_briefing(["Same."], now, directory))

    def test_empty_briefing_writes_nothing(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(history.record_briefing([], self._now(), directory))
            self.assertFalse(history.record_briefing(["  "], self._now(), directory))


class NewOutEdgeTest(unittest.TestCase):
    def test_new_out_reports_only_first_time_symbols(self):
        """The phone push rides this edge: a symbol appears in new_out exactly
        once per day - on the write that stamps its firstSeenAt - never on
        later strengthens or re-serves (trader, 2026-08-30)."""
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            first: list = []
            record_rows([_row()], MORNING, directory, new_out=first)
            self.assertEqual(first, [_row()["symbol"].upper()])
            again: list = []
            record_rows([_row(strength="STRONG")], LATER, directory, new_out=again)
            self.assertEqual(again, [])          # same symbol, not new
            # a genuinely new symbol later in the morning reports itself
            mixed: list = []
            record_rows([_row(), _row("AAPL")], LATER, directory, new_out=mixed)
            self.assertEqual(mixed, ["AAPL"])
