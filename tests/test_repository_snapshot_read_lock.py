"""The option-chain history read must not wait on the process-wide write lock.

2026-11-03: every OI-finder refresh thread (6 quick + 4 full) sat in
``_connect`` waiting on ``_connection_lock`` while one thread read six months
of AMZN/NVDA daily snapshots (61k-77k rows) under it. Chains stopped
refreshing for 30-70 minutes, so the ticker card had no current chain to
draw levels from. The database runs in WAL mode, where a reader neither
blocks nor waits for the writer, so this read takes no lock.
"""
import os
import tempfile
import threading
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

from config import EASTERN_TZ
from database.repository import TradingRepository


class SnapshotReadLockTests(unittest.TestCase):
    def _repository_with_snapshot(self, temp_dir):
        repository = TradingRepository(db_path=os.path.join(temp_dir, "trades.db"))
        today = datetime.now(ZoneInfo(EASTERN_TZ)).date().isoformat()
        repository.upsert_option_chain_daily_snapshots([
            {
                "snapshot_date": today,
                "captured_at": today + "T15:00:00",
                "symbol": "AMZN",
                "expiry": "2026-11-20",
                "side": "CALL",
                "strike": 260,
                "volume": 1200,
                "open_interest": 9800,
            }
        ])
        return repository

    def test_snapshot_read_returns_while_another_thread_holds_the_write_lock(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self._repository_with_snapshot(temp_dir)
            held = threading.Event()
            release = threading.Event()

            def hold_lock():
                with repository._connection_lock:
                    held.set()
                    release.wait(10)

            holder = threading.Thread(target=hold_lock, daemon=True)
            holder.start()
            self.assertTrue(held.wait(5))
            result = {}
            reader = threading.Thread(
                target=lambda: result.setdefault("rows", repository.option_chain_daily_snapshots("AMZN")),
                daemon=True,
            )
            reader.start()
            reader.join(5)
            finished = not reader.is_alive()
            release.set()
            holder.join(5)
            reader.join(5)
            self.assertTrue(finished, "the history read waited on the write lock")
            self.assertEqual([(r["strike"], r["open_interest"]) for r in result["rows"]], [(260.0, 9800.0)])

    def test_snapshot_read_by_expiry_still_filters(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = self._repository_with_snapshot(temp_dir)
            self.assertEqual(len(repository.option_chain_daily_snapshots("AMZN", expiry="2026-11-20")), 1)
            self.assertEqual(repository.option_chain_daily_snapshots("AMZN", expiry="2026-12-18"), [])


if __name__ == "__main__":
    unittest.main()
