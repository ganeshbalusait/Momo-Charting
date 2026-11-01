"""MomX board rows are parsed once per file version, not on every read.

Measured 2026-10-06 19:45 ET (py-spy --gil): Watchlist.json (~3 MB) was
json.loads-ed every ~2-3 s by the option-watchlist readers, one ~0.1-0.3 s
GIL hold each (4.9% of all GIL time).
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import api_server
from api_server import DashboardState


class BoardRowsCache(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.state = DashboardState.__new__(DashboardState)
        self.state._board_prewarm_cache_dir = lambda: self.dir

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write(self, rows, age_seconds=0, mtime_ns=None):
        stamp = (datetime.now(timezone.utc) - timedelta(seconds=age_seconds)).isoformat()
        path = self.dir / "Watchlist.json"
        path.write_text(json.dumps({"generatedAt": stamp, "rows": rows}), encoding="utf-8")
        if mtime_ns is not None:
            os.utime(path, ns=(mtime_ns, mtime_ns))

    def test_second_read_does_not_parse_again(self) -> None:
        self.write([{"symbol": "NVDA"}, "junk", {"symbol": "AMD"}])
        first = self.state._momx_board_rows("Watchlist.json")
        with mock.patch.object(api_server.json, "loads", side_effect=AssertionError("re-parsed")):
            second = self.state._momx_board_rows("Watchlist.json")
        self.assertEqual(first, [{"symbol": "NVDA"}, {"symbol": "AMD"}])
        self.assertEqual(second, first)
        self.assertIsNot(second, first)

    def test_a_rewritten_file_is_read_again(self) -> None:
        self.write([{"symbol": "NVDA"}], mtime_ns=1_000_000_000_000_000_000)
        self.state._momx_board_rows("Watchlist.json")
        self.write([{"symbol": "TSLA"}, {"symbol": "AMD"}], mtime_ns=1_000_000_001_000_000_000)
        self.assertEqual(
            self.state._momx_board_rows("Watchlist.json"),
            [{"symbol": "TSLA"}, {"symbol": "AMD"}],
        )

    def test_a_stale_board_still_returns_nothing_from_cache(self) -> None:
        self.write([{"symbol": "NVDA"}])
        self.assertTrue(self.state._momx_board_rows("Watchlist.json"))
        cached = self.state._momx_board_rows_cache[str(self.dir / "Watchlist.json")]
        self.state._momx_board_rows_cache[str(self.dir / "Watchlist.json")] = (cached[0], cached[1] - 3600, cached[2])
        self.assertEqual(self.state._momx_board_rows("Watchlist.json"), [])

    def test_missing_file_returns_nothing(self) -> None:
        self.assertEqual(self.state._momx_board_rows("Mag7.json"), [])


if __name__ == "__main__":
    unittest.main()
