"""The 60 s dashboard rebuild must not replay the MAG7 D..M signal tables.

Measured 2026-10-06 05:13 ET (py-spy --gil, 30 s, premarket): 35% of all GIL
time sat in mag7_chart_signals_payload under _refresh_dashboard_cache, and
chart opens that landed on it took 1.3-1.9 s instead of 0.1-0.4 s. No screen
has shown those two tables since the page cut (c40c774); the dashboard only
needs to keep the keys, so it serves the snapshot / warming shell.
"""

from __future__ import annotations

import inspect
import unittest

from api_server import DashboardState


class DashboardSkipsTheMag7Replay(unittest.TestCase):
    def test_full_builder_serves_snapshots_not_a_replay(self):
        source = inspect.getsource(DashboardState._build_dashboard_payload)
        self.assertNotIn("mag7_chart_signals_payload(", source)
        self.assertIn('_mag7_chart_signals_snapshot("premarket")', source)
        self.assertIn('_mag7_chart_signals_snapshot("regular")', source)

    def test_warming_shell_keeps_the_payload_shape(self):
        state = DashboardState.__new__(DashboardState)
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]
        for session in ("premarket", "regular"):
            shell = state._mag7_chart_signals_snapshot(session)
            self.assertEqual(shell["status"], "WARMING")
            self.assertEqual(shell["rows"], [])


if __name__ == "__main__":
    unittest.main()
