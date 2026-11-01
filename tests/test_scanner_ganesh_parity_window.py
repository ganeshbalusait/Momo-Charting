"""The scanner's D-M override must be windowed like every other chart serve.

Measured 2026-10-06 06:40 ET: NVDA's "slim" first paint was 2.0 MB, of which
1.49 MB was ganeshHigherTimeframeSignals - the scanner replay's full 20-year
history (6,209 signals back to 2006) pasted over the windowed contract. SPY,
not a scanner symbol, was 0.5 MB.
"""

from __future__ import annotations

import threading
import time
import unittest

import api_server
from api_server import DashboardState


def bars(count: int, first: int) -> list[dict]:
    return [{"time": first + index * 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for index in range(count)]


class ScannerParityOverrideIsWindowed(unittest.TestCase):
    def setUp(self) -> None:
        self.symbol = sorted(api_server.PREMARKET_SCAN_SYMBOLS)[0]
        self.state = DashboardState.__new__(DashboardState)
        self.state.oi_finder_chart_lock = threading.RLock()
        self.first = int(time.time()) - 900 * 60
        self.state.oi_finder_chart_cache = {
            self.symbol: {"payload": {"bars": bars(900, self.first)}},
        }
        signals = [
            {"key": "old", "time": self.first - 86_400 * 3_650},
            {"key": "edge", "time": self.first},
            {"key": "new", "time": self.first + 600},
        ]
        floor = (self.symbol, int(time.time()) // 300)
        self.state._premarket_ganesh_cache = {
            self.symbol: (floor, {"historyReady": True, "signals": signals}),
        }

    def keys(self, served: dict) -> list[str]:
        return [row["key"] for row in served["ganeshHigherTimeframeSignals"]["signals"]]

    def test_a_first_paint_carries_only_the_visible_window(self) -> None:
        served = self.state.apply_scanner_ganesh_parity(
            self.symbol, {"bars": bars(900, self.first)[-300:]},
        )
        self.assertEqual(self.keys(served), ["edge", "new"])

    def test_a_delta_poll_keeps_the_same_signals(self) -> None:
        # Delta bars are only the tail; the window still starts at the cached tape.
        served = self.state.apply_scanner_ganesh_parity(
            self.symbol, {"delta": True, "bars": bars(3, self.first + 897 * 60)},
        )
        self.assertEqual(self.keys(served), ["edge", "new"])

    def test_a_short_tape_still_keeps_tonights_overnight_cross(self) -> None:
        # Cached tape starting 06:00 today; a 01:00 CALLM must still ship (the
        # browser snaps it forward to the day's first candle).
        from datetime import datetime, timedelta
        from zoneinfo import ZoneInfo
        today = datetime.now(ZoneInfo("America/New_York")).replace(hour=0, minute=0, second=0, microsecond=0)
        callm = int((today + timedelta(hours=1)).timestamp())
        tape_start = int((today + timedelta(hours=6)).timestamp())
        self.state.oi_finder_chart_cache[self.symbol] = {"payload": {"bars": bars(30, tape_start)}}
        floor = (self.symbol, int(time.time()) // 300)
        self.state._premarket_ganesh_cache[self.symbol] = (
            floor, {"historyReady": True, "signals": [{"key": "callm", "time": callm}]},
        )
        served = self.state.apply_scanner_ganesh_parity(self.symbol, {"bars": bars(30, tape_start)})
        self.assertEqual(self.keys(served), ["callm"])

    def test_the_stored_replay_is_not_trimmed(self) -> None:
        self.state.apply_scanner_ganesh_parity(self.symbol, {"bars": bars(10, self.first)})
        stored = self.state._premarket_ganesh_cache[self.symbol][1]["signals"]
        self.assertEqual(len(stored), 3)


if __name__ == "__main__":
    unittest.main()
