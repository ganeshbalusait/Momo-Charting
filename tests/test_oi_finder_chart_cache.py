from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path

from api_server import DashboardState


def bars(count: int, *, step: int = 60, start: int = 1_700_000_000) -> list[dict]:
    return [
        {"time": start + index * step, "open": 1.0, "high": 1.0,
         "low": 1.0, "close": 1.0, "volume": 1}
        for index in range(count)
    ]


def deep_payload() -> dict:
    """A healthy cache: the 20-year archive the 4H/D panes aggregate from.

    Real shape on disk is ~785KB - 13,397 studyBars and 5,027 dailyBars. The
    guard keys off fourHourCoverage, not the counts, so the test stays small.
    """
    return {
        "symbol": "MSFT",
        "bars": bars(120),
        "studyBars": bars(90, step=1_800),
        "dailyBars": bars(60, step=86_400),
        "fourHourCoverage": {"requestedYears": 20},
        "historyLoading": False,
    }


def stub_payload() -> dict:
    """What a full build returns when the broker answers with two days only."""
    return {
        "symbol": "MSFT",
        "bars": bars(40),
        "studyBars": bars(40, step=1_800),
        "dailyBars": bars(3, step=86_400),
        "fourHourCoverage": {"requestedYears": 0},
        "historyLoading": False,
    }


class ChartDiskCacheWriteTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = DashboardState.__new__(DashboardState)
        self.state.oi_finder_chart_disk_cache_dir = self.root

    def stored(self, symbol: str = "MSFT") -> dict | None:
        path = self.root / f"{symbol}.json.gz"
        if not path.exists():
            return None
        return json.loads(gzip.decompress(path.read_bytes()).decode("utf-8"))

    def test_a_stub_never_overwrites_a_twenty_year_archive(self) -> None:
        """The 2026-08-18 data loss: MSFT's cache went 784,645 B -> 58,704 B.

        A full rebuild that came back with two days of history was written
        straight over the 20-year archive, which left 4H rendering 11 candles
        and D rendering 3 until a fresh rebuild finished minutes later.
        """
        self.state._save_oi_finder_chart_disk_payload("MSFT", deep_payload())
        self.assertEqual(len(self.stored()["studyBars"]), 90)

        self.state._save_oi_finder_chart_disk_payload("MSFT", stub_payload())
        kept = self.stored()
        self.assertEqual(len(kept["studyBars"]), 90)
        self.assertEqual(len(kept["dailyBars"]), 60)
        self.assertEqual(kept["fourHourCoverage"]["requestedYears"], 20)

    def test_a_real_archive_still_replaces_the_previous_one(self) -> None:
        """The guard must not freeze the cache - good builds still land."""
        self.state._save_oi_finder_chart_disk_payload("MSFT", deep_payload())
        fresher = deep_payload()
        fresher["bars"] = bars(200)
        self.state._save_oi_finder_chart_disk_payload("MSFT", fresher)
        self.assertEqual(len(self.stored()["bars"]), 200)

    def test_a_stub_writes_when_there_is_no_archive_to_protect(self) -> None:
        """A genuinely short-history ticker must still get cached."""
        self.state._save_oi_finder_chart_disk_payload("NEWCO", stub_payload())
        self.assertIsNotNone(self.stored("NEWCO"))
        self.assertEqual(len(self.stored("NEWCO")["studyBars"]), 40)

    def test_a_stub_replaces_an_existing_stub(self) -> None:
        self.state._save_oi_finder_chart_disk_payload("NEWCO", stub_payload())
        fresher = stub_payload()
        fresher["bars"] = bars(75)
        self.state._save_oi_finder_chart_disk_payload("NEWCO", fresher)
        self.assertEqual(len(self.stored("NEWCO")["bars"]), 75)

    def test_a_loading_or_empty_payload_is_never_written(self) -> None:
        loading = deep_payload()
        loading["historyLoading"] = True
        self.state._save_oi_finder_chart_disk_payload("ZZZ", loading)
        self.assertIsNone(self.stored("ZZZ"))
        empty = deep_payload()
        empty["bars"] = []
        self.state._save_oi_finder_chart_disk_payload("ZZZ", empty)
        self.assertIsNone(self.stored("ZZZ"))


if __name__ == "__main__":
    unittest.main()
