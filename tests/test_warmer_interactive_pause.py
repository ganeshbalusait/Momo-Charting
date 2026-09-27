from __future__ import annotations

import time
import unittest

from api_server import DashboardState


class WarmerInteractivePauseTests(unittest.TestCase):
    """The background warmer must yield while a human is using the app.

    Measured 2026-08-18: the warmer rebuilds one watchlist chart cache every
    ~150s and had 370 of 393 symbols left - 15.5 hours of continuous
    pure-Python CPU. Holding the GIL that long starved every request, so
    /api/auth/status (400 bytes, no work) took 4.8s in the browser and 2.9s
    locally. touch_oi_finder_interactive_window() already existed to pause it,
    but only chart/chain reads called it - the dashboard poll the app runs
    every five seconds did not, so ordinary use never bought any quiet.
    """

    def setUp(self) -> None:
        self.state = DashboardState.__new__(DashboardState)
        self.state.oi_finder_interactive_until = 0.0
        self.state.oi_finder_ondemand_builds = 0

    def test_touching_the_window_makes_the_warmer_wait(self) -> None:
        now = time.monotonic()
        self.assertFalse(self.state._warmer_should_wait(now, now))
        self.state.touch_oi_finder_interactive_window()
        self.assertTrue(self.state._warmer_should_wait(time.monotonic(), time.monotonic()))

    def test_the_window_expires_so_the_warmer_resumes(self) -> None:
        self.state.touch_oi_finder_interactive_window()
        # Pretend the window closed rather than sleeping 45s in a test.
        self.state.oi_finder_interactive_until = time.monotonic() - 1.0
        now = time.monotonic()
        self.assertFalse(self.state._warmer_should_wait(now, now))

    def test_forward_progress_is_still_guaranteed(self) -> None:
        """However busy the app is, one build still lands eventually."""
        self.state.touch_oi_finder_interactive_window()
        stale_build = time.monotonic() - self.state.WARMER_FORCE_PROGRESS_SECONDS - 1.0
        self.assertFalse(self.state._warmer_should_wait(time.monotonic(), stale_build))

    def test_an_on_demand_build_always_wins(self) -> None:
        self.state.oi_finder_ondemand_builds = 1
        now = time.monotonic()
        self.assertTrue(self.state._warmer_should_wait(now, now))


if __name__ == "__main__":
    unittest.main()
