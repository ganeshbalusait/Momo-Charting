"""The MomX news-store read must not queue behind the app-wide DB lock.

Measured 2026-10-07: /api/news-feed/latest took 6-35 s while the news collector
saved headlines, because every query waited on TradingRepository's RLock even
though the database is in WAL mode (readers never block on SQLite's writer).
"""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path

from database.repository import TradingRepository


class NewsReadSkipsTheLock(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo = TradingRepository(db_path=str(Path(self.tmp.name) / "t.db"))
        self.repo.log_catalysts([
            {"symbol": "NVDA", "headline": "Nvidia beats", "url": "https://x/1",
             "published_at": "2099-01-01T00:00:00+00:00"},
        ])

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_reads_while_another_thread_holds_the_lock(self) -> None:
        held, release = threading.Event(), threading.Event()

        def holder() -> None:
            with self.repo._connect():
                held.set()
                release.wait(5)

        thread = threading.Thread(target=holder)
        thread.start()
        held.wait(5)
        try:
            started = time.perf_counter()
            rows = self.repo.get_catalysts_for_symbols(["NVDA"])
            elapsed = time.perf_counter() - started
        finally:
            release.set()
            thread.join()
        self.assertLess(elapsed, 2.0)
        self.assertEqual([row["headline"] for row in rows], ["Nvidia beats"])


if __name__ == "__main__":
    unittest.main()
