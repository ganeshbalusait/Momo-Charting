import unittest
from datetime import datetime, timedelta
import threading
import time
from unittest.mock import patch

import pandas as pd

from types import SimpleNamespace

from api_server import DashboardState, _history_summary


class DashboardSummaryTests(unittest.TestCase):
    def test_learning_status_uses_precomputed_cache_without_repository_query(self):
        state = DashboardState.__new__(DashboardState)
        state.learning_status_cache_lock = threading.Lock()
        state.learning_status_cache = {
            "observations": 123,
            "catalystShadowStudy": {"status": "COLLECTING", "canBlockTrades": False},
        }
        state.learning_status_cache_timestamp = datetime.now().astimezone()
        state.repository = SimpleNamespace(
            learning_status=lambda: (_ for _ in ()).throw(AssertionError("repository should not be queried"))
        )
        state._learning_symbol_cohorts = lambda: {"PLTR": "watchlist"}
        state.learning_agent = SimpleNamespace(
            status="Monitoring",
            message="ready",
            last_run=None,
            last_error="",
        )
        state.learning_interval_seconds = 300
        state.learning_last_result = {}

        payload = DashboardState._learning_status_payload(state)

        self.assertEqual(payload["observations"], 123)
        self.assertEqual(payload["catalystShadowStudy"]["status"], "COLLECTING")
        self.assertFalse(payload["catalystShadowStudy"]["canBlockTrades"])
        self.assertEqual(payload["scope"]["symbolCount"], 1)

    def test_history_summary_separates_today_and_closed_performance(self):
        frame = pd.DataFrame([
            {"status": "closed", "entry_time": "2026-07-11T09:35:00-04:00", "pnl": 25.0},
            {"status": "closed", "entry_time": "2026-07-11T10:05:00-04:00", "pnl": -10.0},
            {"status": "position_open", "entry_time": "2026-07-10T15:00:00-04:00", "marked_pnl": 5.0},
        ])

        summary = _history_summary(frame, "2026-07-11")

        self.assertEqual(summary["totalTrades"], 3)
        self.assertEqual(summary["closedTrades"], 2)
        self.assertEqual(summary["tradesToday"], 2)
        self.assertEqual(summary["totalPnL"], 15.0)
        self.assertEqual(summary["openPnL"], 5.0)
        self.assertEqual(summary["todayPnL"], 15.0)
        self.assertEqual(summary["wins"], 1)
        self.assertEqual(summary["losses"], 1)
        self.assertEqual(summary["winRate"], 50.0)

    def test_empty_history_returns_zero_metrics(self):
        summary = _history_summary(pd.DataFrame(), "2026-07-11")

        self.assertEqual(summary["totalTrades"], 0)
        self.assertEqual(summary["totalPnL"], 0.0)
        self.assertEqual(summary["winRate"], 0.0)

    def test_dashboard_books_include_every_configured_account(self):
        state = DashboardState.__new__(DashboardState)
        accounts = [
            {
                "id": f"paper{index}",
                "label": f"Account {index}",
                "tradeLabel": f"Trading Account {index}",
                "usage": "options",
                "optionActive": True,
                "active": False,
            }
            for index in range(1, 11)
        ]
        state.available_accounts = lambda: accounts
        state._enrich_option_trade_history = lambda frame: frame
        state.option_bot_state = "Running"
        state.repository = SimpleNamespace(
            get_option_trade_history=lambda **kwargs: pd.DataFrame(),
        )
        option_payloads = [
            {
                "id": account["id"],
                "status": {
                    "accountEquity": index * 1000,
                    "tradeableBuyingPower": index * 2000,
                    "connectionStatus": "Connected",
                },
            }
            for index, account in enumerate(accounts, start=1)
        ]

        books = state._dashboard_account_books("2026-07-12", None, option_payloads)

        self.assertEqual(len(books), len(accounts))
        self.assertEqual([book["id"] for book in books], [account["id"] for account in accounts])
        self.assertEqual(sum(book["equity"] for book in books), 55000)

    def test_dashboard_option_books_keep_mag7_and_watchlist_performance_separate(self):
        state = DashboardState.__new__(DashboardState)
        state.available_accounts = lambda: [
            {
                "id": "paper3",
                "label": "Mag7 OPTION",
                "tradeLabel": "Mag7 OPTION",
                "usage": "mag7_options",
                "optionActive": True,
                "active": False,
            },
            {
                "id": "paper5",
                "label": "Watchlist option",
                "tradeLabel": "Watchlist option",
                "usage": "watchlist_options",
                "optionActive": True,
                "active": False,
            },
        ]
        state._enrich_option_trade_history = lambda frame: frame
        state.option_bot_state = "Running"

        histories = {
            "paper3": pd.DataFrame([
                {"status": "closed", "entry_time": "2026-07-13T09:40:00-04:00", "pnl": 125.0},
            ]),
            "paper5": pd.DataFrame([
                {"status": "closed", "entry_time": "2026-07-13T10:00:00-04:00", "pnl": -40.0},
                {"status": "position_open", "entry_time": "2026-07-13T10:15:00-04:00", "marked_pnl": 15.0},
            ]),
        }
        state.repository = SimpleNamespace(
            get_option_trade_history=lambda **kwargs: histories[kwargs["profile_id"]],
        )
        option_payloads = [
            {"id": "paper3", "status": {"accountEquity": 1_010_000, "tradeableBuyingPower": 900_000, "dailyPnL": 125, "openPositions": 0, "openOrders": 0, "connectionStatus": "Connected"}},
            {"id": "paper5", "status": {"accountEquity": 998_000, "tradeableBuyingPower": 850_000, "dailyPnL": -25, "openPositions": 1, "openOrders": 1, "connectionStatus": "Connected"}},
        ]

        books = state._dashboard_account_books("2026-07-13", None, option_payloads)
        by_id = {book["id"]: book for book in books}

        self.assertEqual(by_id["paper3"]["totalPnL"], 125.0)
        self.assertEqual(by_id["paper3"]["tradesToday"], 1)
        self.assertEqual(by_id["paper3"]["winRate"], 100.0)
        self.assertEqual(by_id["paper5"]["totalPnL"], -40.0)
        self.assertEqual(by_id["paper5"]["openPnL"], 15.0)
        self.assertEqual(by_id["paper5"]["tradesToday"], 2)
        self.assertEqual(by_id["paper5"]["openPositions"], 1)

    def test_runtime_component_does_not_flag_intentionally_disabled_thread(self):
        state = DashboardState.__new__(DashboardState)
        state.runtime_watchdog_stale_multiplier = 4.0

        component = state._runtime_component_state(
            "Disabled engine",
            thread=None,
            required=False,
            last_run=datetime.now().astimezone() - timedelta(hours=1),
            expected_interval_seconds=15,
        )

        self.assertTrue(component["healthy"])
        self.assertFalse(component["alive"])
        self.assertTrue(component["stale"])

    def test_runtime_recovery_starts_only_required_dead_worker(self):
        state = DashboardState.__new__(DashboardState)
        state.runtime_watchdog_auto_recover = True
        starts = []
        state._start_scanner_auto_loop = lambda: starts.append("stockScanner")
        state._start_oi_scanner_auto_loops = lambda: starts.append("oiScanner")
        state._start_stock_position_manager = lambda: starts.append("stockPositionManager")
        state._start_learning_loop = lambda: starts.append("learningAgent")
        state._start_option_scheduler = lambda: starts.append("optionScheduler")

        recovered = state._recover_runtime_components({
            "stockScanner": {"required": True, "alive": False},
            "mag7OiScanner": {"required": True, "alive": True},
            "watchlistOiScanner": {"required": True, "alive": True},
            "stockPositionManager": {"required": True, "alive": True},
            "learningAgent": {"required": True, "alive": True},
            "optionScheduler": {"required": False, "alive": False},
            "stockScheduler": {"required": False, "alive": False},
        })

        self.assertEqual(recovered, ["stockScanner"])
        self.assertEqual(starts, ["stockScanner"])

    def test_manual_oi_scan_reports_busy_when_job_is_running(self):
        state = DashboardState.__new__(DashboardState)
        state.oi_scan_job = {"running": True, "message": "Scanning"}
        state.oi_action_message = ""
        state.action_message = ""
        state._normalize_option_watchlist = lambda symbols: list(symbols)
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]
        state._oi_scan_lock = lambda label: threading.Lock()
        state._invalidate_dashboard_cache = lambda: None
        state._dashboard_control_payload = lambda: {"oiScanJob": dict(state.oi_scan_job)}

        result = state.start_oi_scan_job(["AAPL", "NVDA"], "MAG7 OI Scanner")

        self.assertFalse(result["started"])
        self.assertTrue(result["busy"])
        self.assertTrue(result["dashboard"]["oiScanJob"]["running"])

    def test_manual_oi_scan_lifecycle_finishes_and_records_timestamp(self):
        state = DashboardState.__new__(DashboardState)
        state.oi_scan_job = {"running": False}
        state.oi_action_message = ""
        state.action_message = ""
        state._normalize_option_watchlist = lambda symbols: list(symbols)
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]
        state._watchlist_oi_underlyings = lambda: ["AMD"]
        state._oi_scan_lock = lambda label: threading.Lock()
        state._invalidate_dashboard_cache = lambda: None
        state._dashboard_control_payload = lambda: {"oiScanJob": dict(state.oi_scan_job)}

        def scan(symbols, scan_label, return_payload):
            state.oi_action_message = f"{scan_label} completed for {len(symbols)} symbols."

        state.scan_oi_watchlist = scan

        class ImmediateThread:
            def __init__(self, target, daemon=True):
                self.target = target

            def start(self):
                self.target()

        with patch("api_server.threading.Thread", ImmediateThread):
            result = state.start_oi_scan_job(["AAPL", "NVDA"], "MAG7 OI Scanner")

        self.assertTrue(result["started"])
        self.assertFalse(state.oi_scan_job["running"])
        self.assertEqual(state.oi_scan_job["symbolCount"], 2)
        self.assertIn("completed", state.oi_scan_job["message"])
        self.assertIsNotNone(state.oi_scan_job["startedAt"])
        self.assertIsNotNone(state.oi_scan_job["finishedAt"])
        self.assertFalse(state._oi_manual_priority_event("MAG7 OI Scanner").is_set())

    def test_manual_oi_scan_marks_priority_before_runner_starts(self):
        state = DashboardState.__new__(DashboardState)
        state.oi_scan_job = {"running": False}
        state.oi_action_message = ""
        state.action_message = ""
        state._normalize_option_watchlist = lambda symbols: list(symbols)
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]
        state._watchlist_oi_underlyings = lambda: ["AMD"]
        state._oi_scan_lock = lambda label: threading.Lock()
        state._invalidate_dashboard_cache = lambda: None
        state._dashboard_control_payload = lambda: {"oiScanJob": dict(state.oi_scan_job)}
        runner_started = threading.Event()
        release_runner = threading.Event()

        def scan(symbols, scan_label, return_payload):
            runner_started.set()
            release_runner.wait(timeout=2)
            state.oi_action_message = "completed"

        state.scan_oi_watchlist = scan
        result = state.start_oi_scan_job(["AAPL", "NVDA"], "MAG7 OI Scanner")

        self.assertTrue(result["started"])
        self.assertTrue(runner_started.wait(timeout=1))
        self.assertTrue(state._oi_manual_priority_event("MAG7 OI Scanner").is_set())
        self.assertIn("PRIORITY", state.oi_scan_job["message"])
        release_runner.set()
        deadline = time.time() + 2
        while state.oi_scan_job["running"] and time.time() < deadline:
            time.sleep(0.01)
        self.assertFalse(state._oi_manual_priority_event("MAG7 OI Scanner").is_set())


if __name__ == "__main__":
    unittest.main()
