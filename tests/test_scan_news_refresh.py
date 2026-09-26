from __future__ import annotations

import types
import unittest

import api_server


class ScanJobNewsRefreshTests(unittest.TestCase):
    """A manual scan refreshes the ticker-tagged headlines for the symbols it scanned.

    The refresh is fire-and-forget: it runs after the scan result is recorded
    and can never change the scan outcome.
    """

    def _state(self) -> tuple[api_server.DashboardState, list, list]:
        state = api_server.DashboardState.__new__(api_server.DashboardState)
        events: list[tuple[str, str]] = []
        scheduled: list = []
        state.scan_job = {"running": True}
        state.repository = types.SimpleNamespace(log_bot_event=lambda kind, message: events.append((kind, message)))
        state.scan = lambda **kwargs: {"resultCount": 2, "scanLabel": kwargs.get("scan_label")}
        state._schedule_catalyst_information_refresh = lambda symbols=None: scheduled.append(symbols) or True
        return state, scheduled, events

    def test_scan_job_schedules_news_refresh_for_scanned_symbols(self) -> None:
        state, scheduled, _ = self._state()
        state._run_scan_job(["AAPL", "MSFT"], "Watchlist")
        self.assertEqual(scheduled, [["AAPL", "MSFT"]])
        self.assertFalse(state.scan_job["running"])
        self.assertIn("Found 2 matches", state.scan_job["message"])

    def test_news_refresh_failure_never_fails_the_scan(self) -> None:
        state, _, events = self._state()

        def boom(symbols=None):
            raise RuntimeError("news offline")

        state._schedule_catalyst_information_refresh = boom
        state._run_scan_job(["AAPL"], "Watchlist")
        self.assertFalse(state.scan_job["running"])
        self.assertEqual(state.scan_job["error"], "")
        self.assertIn(("catalyst_scan_error", "News refresh after scan failed: news offline"), events)

    def test_scan_failure_does_not_trigger_news_refresh(self) -> None:
        state, scheduled, _ = self._state()

        def failing_scan(**kwargs):
            raise RuntimeError("no market data")

        state.scan = failing_scan
        state._run_scan_job(["AAPL"], "Watchlist")
        self.assertEqual(scheduled, [])
        self.assertEqual(state.scan_job["error"], "no market data")


if __name__ == "__main__":
    unittest.main()
