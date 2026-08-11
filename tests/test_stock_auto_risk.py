import unittest
import threading
import time
from datetime import datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from api_server import DashboardState
from config import settings
from execution.alpaca_paper_trader import AlpacaPaperTrader
from risk_manager import RiskManager


class StockRiskRuleTests(unittest.TestCase):
    def setUp(self):
        self.original_stop = settings.trading.stop_loss_percent
        self.original_target = settings.trading.take_profit_1_pct
        self.original_daily_limit = settings.trading.enforce_daily_loss_limit
        self.original_trade_amount = settings.trading.fixed_trade_amount
        settings.trading.stop_loss_percent = 2.0
        settings.trading.take_profit_1_pct = 2.0
        settings.trading.enforce_daily_loss_limit = False
        settings.trading.fixed_trade_amount = 500.0

    def tearDown(self):
        settings.trading.stop_loss_percent = self.original_stop
        settings.trading.take_profit_1_pct = self.original_target
        settings.trading.enforce_daily_loss_limit = self.original_daily_limit
        settings.trading.fixed_trade_amount = self.original_trade_amount

    def test_stock_uses_two_percent_stop_and_two_percent_first_target(self):
        risk = RiskManager()

        self.assertEqual(risk.effective_stop_price(100.0, 95.0, 5), 98.0)
        self.assertEqual(risk.effective_target_price(100.0, 98.0), 102.0)

    def test_account_daily_loss_does_not_block_when_disabled(self):
        decision = RiskManager().can_open_new_trade(
            equity=10_000.0,
            daily_pnl=-500.0,
            trades_today=2,
            risk_per_share=2.0,
            entry_price=100.0,
            buying_power=10_000.0,
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.quantity, 5.0)
        self.assertEqual(decision.risk_amount, 10.0)


class StockSchedulerResponseTests(unittest.TestCase):
    def test_background_execution_omits_dashboard_but_manual_default_keeps_it(self):
        state = DashboardState.__new__(DashboardState)
        state.action_message = ""
        state.dashboard_cache = {"stale": True}
        state.dashboard_cache_timestamp = datetime.now().astimezone()
        state.dashboard_cache_lock = threading.Lock()
        state._stock_account_block_message = lambda: "Account entry blocked for test."
        dashboard_calls = []
        state.dashboard_payload = lambda: dashboard_calls.append(True) or {"sentinel": "dashboard"}

        background = DashboardState.execute_all_trades(state, return_payload=False)

        self.assertEqual(background["result"]["status"], "blocked")
        self.assertNotIn("dashboard", background)
        self.assertEqual(dashboard_calls, [])
        self.assertEqual(state.dashboard_cache, {"stale": True})
        self.assertIsNotNone(state.dashboard_cache_timestamp)

        manual = DashboardState.execute_all_trades(state)

        self.assertEqual(manual["dashboard"], {"sentinel": "dashboard"})
        self.assertEqual(dashboard_calls, [True])

    def test_stock_scheduler_uses_non_dashboard_execution_mode(self):
        state = DashboardState.__new__(DashboardState)
        state.scheduler_enabled = True
        state.scheduler_last_run = None
        state.scheduler_cycle_count = 0
        state.scheduler_cycle_status = ""
        state.scheduler_cycle_message = ""
        state.scheduler_last_error = ""
        state.scheduler_interval_seconds = 0
        state.bot_state = "Running"
        state.trader = SimpleNamespace(
            sync_order_statuses=lambda: None,
            manage_open_trades=lambda: None,
        )
        state.client = SimpleNamespace(get_clock=lambda: object())
        state._session_status = lambda clock: {
            "canAutoTrade": True,
            "currentSession": "Core",
            "automationMode": "Automatic",
            "executionNote": "",
        }
        execution_calls = []

        def execute_all_trades(**kwargs):
            execution_calls.append(kwargs)
            state.scheduler_enabled = False
            return {"result": {"status": "submitted", "message": "done"}}

        state.execute_all_trades = execute_all_trades
        state._schedule_catalyst_information_refresh = lambda: False

        DashboardState._scheduler_loop(state)

        self.assertEqual(execution_calls, [{"return_payload": False}])

    def test_stock_scheduler_non_entry_scan_omits_dashboard_payload(self):
        state = DashboardState.__new__(DashboardState)
        state.scheduler_enabled = True
        state.scheduler_last_run = None
        state.scheduler_cycle_count = 0
        state.scheduler_cycle_status = ""
        state.scheduler_cycle_message = ""
        state.scheduler_last_error = ""
        state.scheduler_interval_seconds = 0
        state.bot_state = "Running"
        state.trader = SimpleNamespace(
            sync_order_statuses=lambda: None,
            manage_open_trades=lambda: None,
        )
        state.client = SimpleNamespace(get_clock=lambda: object())
        state._session_status = lambda clock: {
            "canAutoTrade": False,
            "currentSession": "Closed",
            "automationMode": "Monitoring",
            "executionNote": "US stock sessions are closed.",
        }
        scan_calls = []

        def scan(**kwargs):
            scan_calls.append(kwargs)
            state.scheduler_enabled = False
            return {"resultCount": 0}

        state.scan = scan
        state.repository = SimpleNamespace(log_bot_event=lambda *args, **kwargs: None)
        state._schedule_catalyst_information_refresh = lambda: False

        DashboardState._scheduler_loop(state)

        self.assertEqual(scan_calls, [{"return_payload": False}])

    def test_stock_scheduler_entry_scan_continues_when_position_management_fails(self):
        state = DashboardState.__new__(DashboardState)
        state.scheduler_enabled = True
        state.scheduler_last_run = None
        state.scheduler_cycle_count = 0
        state.scheduler_cycle_status = ""
        state.scheduler_cycle_message = ""
        state.scheduler_last_error = ""
        state.scheduler_interval_seconds = 0
        state.bot_state = "Running"
        state.trader = SimpleNamespace(
            sync_order_statuses=lambda: None,
            manage_open_trades=lambda: (_ for _ in ()).throw(RuntimeError("exit manager unavailable")),
        )
        state.client = SimpleNamespace(get_clock=lambda: object())
        state._session_status = lambda clock: {
            "canAutoTrade": True,
            "currentSession": "Core",
            "automationMode": "Automatic",
            "executionNote": "",
        }
        execution_calls = []
        events = []

        def execute_all_trades(**kwargs):
            execution_calls.append(kwargs)
            state.scheduler_enabled = False
            return {"result": {"status": "submitted", "message": "done"}}

        state.execute_all_trades = execute_all_trades
        state.repository = SimpleNamespace(log_bot_event=lambda *args, **kwargs: events.append(args))
        state._schedule_catalyst_information_refresh = lambda: False

        DashboardState._scheduler_loop(state)

        self.assertEqual(execution_calls, [{"return_payload": False}])
        self.assertTrue(any(event[0] == "stock_scheduler_manage_error" for event in events))
        self.assertIn("exit manager unavailable", state.scheduler_last_error)


class StockOiConfirmationTests(unittest.TestCase):
    def test_quote_priority_moves_hot_symbols_first_without_losing_breadth(self):
        state = DashboardState.__new__(DashboardState)
        state.scanner = SimpleNamespace(
            client=SimpleNamespace(
                get_quotes=lambda symbols: {
                    "AAPL": {"last_price": 210, "change_pct": 0.2, "volume": 1000},
                    "PLTR": {"last_price": 140, "change_pct": 4.5, "volume": 9000},
                    "MDB": {"last_price": 330, "change_pct": 1.8, "volume": 3000},
                }
            )
        )

        ordered = DashboardState._quote_priority_order(state, ["AAPL", "MDB", "PLTR"])

        self.assertEqual(ordered[:2], ["PLTR", "MDB"])
        self.assertCountEqual(ordered, ["AAPL", "MDB", "PLTR"])
        self.assertEqual(len(ordered), len(set(ordered)))

    def test_fresh_trade_grade_oi_wakes_stock_scheduler(self):
        state = DashboardState.__new__(DashboardState)
        state.option_scan_wakeup = threading.Event()
        state.stock_entry_wakeup = threading.Event()

        result = DashboardState._wake_execution_for_oi_rows(
            state,
            [
                {
                    "priority_label": "A ACTIVE",
                    "trade_eligible": True,
                    "stock_cloud_alignment_pass": True,
                }
            ],
        )

        self.assertTrue(result["stockReady"])
        self.assertTrue(state.stock_entry_wakeup.is_set())
        self.assertFalse(state.option_scan_wakeup.is_set())

    def test_premarket_plan_is_read_only_and_keeps_news_informational(self):
        state = DashboardState.__new__(DashboardState)
        now = datetime.now().astimezone()
        state.oi_mag7_scan_results = pd.DataFrame(
            [
                {
                    "underlying": "PLTR",
                    "priority_label": "A+ HOT",
                    "trade_eligible": True,
                    "strength_score": 91,
                    "last_seen_at": now.isoformat(),
                    "stock_cloud_alignment_pass": True,
                    "stock_five_min_cloud_state": "BULLISH",
                    "stock_four_hour_cloud_state": "BULLISH",
                    "stock_above_vwap": True,
                    "stock_ema_stack": True,
                    "stock_fast_momentum_score": 3,
                    "contract": "PLTR260717C00130000",
                    "delta": 0.20,
                    "bid": 1.10,
                    "ask": 1.20,
                    "expected_move": 4.25,
                    "days_to_expiration": 2,
                }
            ]
        )
        state.oi_watchlist_scan_results = pd.DataFrame()
        state.oi_mag7_scan_timestamp = now
        state.oi_watchlist_scan_timestamp = now
        state.oi_watchlist_completed_cycles = 1
        state.oi_watchlist_universe_count = 397
        state.oi_watchlist_batch_end = 397
        state.oi_watchlist_cycle_duration_seconds = 45.0
        state.repository = SimpleNamespace(
            get_catalyst_snapshot=lambda symbols, **kwargs: {
                "PLTR": {
                    "sentiment": "Strong",
                    "score": 3,
                    "headline": "Contract award",
                    "published_at": now.isoformat(),
                    "age_hours": 0.1,
                }
            }
        )

        plan = DashboardState._build_premarket_plan(state, as_of=now)

        self.assertEqual(plan["status"], "READY")
        self.assertEqual(plan["stockCandidates"][0]["symbol"], "PLTR")
        self.assertEqual(plan["optionCandidates"][0]["symbol"], "PLTR")
        self.assertFalse(plan["stockCandidates"][0]["orderAuthority"])
        self.assertTrue(plan["stockCandidates"][0]["news"]["informationOnly"])
        self.assertFalse(plan["execution"]["usesCachedPlanForOrders"])
        self.assertFalse(plan["newsPolicy"]["canRankTrades"])

    def test_only_fresh_trade_grade_oi_rows_confirm_stock_entries(self):
        state = DashboardState.__new__(DashboardState)
        now = datetime.now().astimezone()
        state.oi_mag7_scan_results = pd.DataFrame(
            [
                {
                    "underlying": "AAPL",
                    "priority_label": "A+ HOT",
                    "trade_eligible": True,
                    "stock_cloud_alignment_pass": True,
                    "strength_score": 88,
                    "last_seen_at": now.isoformat(),
                },
                {
                    "underlying": "AMD",
                    "priority_label": "A ACTIVE",
                    "trade_eligible": True,
                    "strength_score": 72,
                    "last_seen_at": (now - timedelta(minutes=10)).isoformat(),
                },
            ]
        )
        state.oi_watchlist_scan_results = pd.DataFrame(
            [
                {
                    "underlying": "PLTR",
                    "priority_label": "Watchlist",
                    "trade_eligible": False,
                    "strength_score": 60,
                    "last_seen_at": now.isoformat(),
                }
            ]
        )
        state.oi_mag7_scan_timestamp = now
        state.oi_watchlist_scan_timestamp = now
        state._mag7_option_underlyings = lambda: ["AAPL", "AMD"]

        confirmation = DashboardState._fresh_stock_auto_oi_confirmation(state)

        self.assertEqual(confirmation["symbols"], ["AAPL"])
        self.assertEqual(confirmation["thresholds"]["AAPL"], 0.4)


class StockAutoSessionTests(unittest.TestCase):
    def test_auto_entries_run_during_every_supported_stock_session(self):
        state = DashboardState.__new__(DashboardState)
        state.bot_state = "Running"

        for session_name in ("Pre-Market", "Core", "After-Hours", "Overnight"):
            with self.subTest(session=session_name):
                self.assertTrue(
                    DashboardState._stock_auto_entries_allowed(
                        state,
                        {"currentSession": session_name, "canAutoTrade": True},
                    )
                )

    def test_auto_entries_do_not_run_when_session_closed_or_bot_stopped(self):
        state = DashboardState.__new__(DashboardState)
        state.bot_state = "Running"
        self.assertFalse(
            DashboardState._stock_auto_entries_allowed(
                state,
                {"currentSession": "Closed", "canAutoTrade": False},
            )
        )

        state.bot_state = "Stopped"
        self.assertFalse(
            DashboardState._stock_auto_entries_allowed(
                state,
                {"currentSession": "Core", "canAutoTrade": True},
            )
        )

    def _playbook_state(self, session_name, can_auto_trade):
        state = DashboardState.__new__(DashboardState)
        state.client = type("Client", (), {"get_clock": lambda self: object()})()
        state._session_status = lambda clock: {
            "currentSession": session_name,
            "canAutoTrade": can_auto_trade,
        }
        state._mag7_option_underlyings = lambda: ["AAPL"]
        return state

    def test_core_playbook_requires_oi_and_carries_otm_liquidity_target(self):
        state = self._playbook_state("Core", True)
        state._fresh_stock_auto_oi_confirmation = lambda: {
            "symbols": ["AAPL"],
            "thresholds": {"AAPL": 0.4},
            "rows": [
                {
                    "symbol": "AAPL",
                    "priority_label": "A+ HOT",
                    "strength_score": 90,
                    "otm_target": 315.0,
                    "option_contract": "AAPL 7/10/2026 315C",
                }
            ],
            "maxAgeSeconds": 300,
        }

        playbook = DashboardState._stock_auto_playbook(state)

        self.assertTrue(playbook["requiresFreshOi"])
        self.assertEqual(playbook["mode"], "core_oi_confirmed")
        self.assertEqual(playbook["tradeOverrides"]["AAPL"]["liquidity_target_price"], 315.0)

    def test_extended_playbook_scans_stocks_without_fresh_oi(self):
        state = self._playbook_state("After-Hours", True)

        playbook = DashboardState._stock_auto_playbook(state)

        self.assertFalse(playbook["requiresFreshOi"])
        self.assertEqual(playbook["mode"], "extended_stock_momentum")
        self.assertGreater(len(playbook["symbols"]), 300)
        self.assertEqual(playbook["tradeOverrides"], {})

    def test_closed_playbook_has_no_entry_universe(self):
        state = self._playbook_state("Closed", False)

        playbook = DashboardState._stock_auto_playbook(state)

        self.assertEqual(playbook["mode"], "closed")
        self.assertEqual(playbook["symbols"], [])


class StockAutoScannerGateTests(unittest.TestCase):
    def test_automated_stock_scan_keeps_legacy_hourly_gates_informational(self):
        captured = {}

        class Scanner:
            settings = type("Settings", (), {"default_universe": ["AAPL"]})()

            def run(self, **kwargs):
                captured.update(kwargs)
                return pd.DataFrame()

        class Repository:
            def get_symbol_memory(self, limit):
                return {}

            def get_recent_catalysts(self, limit):
                raise RuntimeError("informational catalyst store unavailable")

            def log_scan_run(self, **kwargs):
                return None

        trader = AlpacaPaperTrader.__new__(AlpacaPaperTrader)
        trader.scanner = Scanner()
        trader.ai_model = type("AiModel", (), {"score_frame": lambda self, frame: frame})()
        trader._existing_symbols = lambda: set()
        trader.repository = Repository()
        trader.strategy = type(
            "Strategy",
            (),
            {
                "build_trade_candidates": lambda self, *args, **kwargs: [],
                "candidates_to_frame": lambda self, candidates: pd.DataFrame(),
            },
        )()
        trader.llm_advisor = type("Advisor", (), {"enrich_frame": lambda self, frame: frame})()

        trader.run_scan_and_prepare_trades(symbols=["AAPL"])

        self.assertTrue(captured["ignore_one_hour_price_change"])
        self.assertTrue(captured["ignore_four_hour_price_change"])
        self.assertTrue(captured["ignore_four_hour_volume"])

    def test_concurrent_repeated_stock_signal_submits_only_once(self):
        trader = AlpacaPaperTrader.__new__(AlpacaPaperTrader)
        active_symbols = set()
        submitted_orders = []
        candidate = pd.DataFrame(
            [
                {
                    "symbol": "NVDA",
                    "allowed": True,
                    "final_score": 100,
                    "score": 100,
                    "entry": 200.0,
                    "risk_per_share": 4.0,
                }
            ]
        )
        trader.run_scan_and_prepare_trades = lambda *args, **kwargs: (candidate.copy(), candidate.copy())
        trader._existing_symbols = lambda: set(active_symbols)
        trader.get_status = lambda: SimpleNamespace(
            account_equity=100_000.0,
            daily_pnl=0.0,
            trades_today=0,
            buying_power=100_000.0,
            deployed_capital=0.0,
        )
        trader.risk_manager = SimpleNamespace(
            can_open_new_trade=lambda **kwargs: SimpleNamespace(allowed=True, reason="", quantity=5, risk_amount=20.0)
        )
        trader.client = SimpleNamespace(is_paper=True)
        trader._session_name = lambda: "Core"
        trader._trade_ticket = lambda *args, **kwargs: {}
        trader._log_trade_submission = lambda *args, **kwargs: None

        def submit_entry(row, quantity, client_order_id, session_name):
            time.sleep(0.03)
            submitted_orders.append((row["symbol"], quantity, client_order_id))
            active_symbols.add(str(row["symbol"]).upper())
            return SimpleNamespace(id="order-1")

        trader._submit_entry_order = submit_entry
        barrier = threading.Barrier(3)
        results = []

        def run_once():
            barrier.wait()
            results.append(trader.execute_all_eligible_candidates(symbols=["NVDA"]))

        threads = [threading.Thread(target=run_once) for _ in range(2)]
        for thread in threads:
            thread.start()
        barrier.wait()
        for thread in threads:
            thread.join(timeout=2)

        self.assertEqual(len(submitted_orders), 1)
        self.assertEqual(submitted_orders[0][0:2], ("NVDA", 5))
        self.assertEqual(sum(len(result.get("submitted", [])) for result in results), 1)


class StockLiquidityTargetTests(unittest.TestCase):
    def setUp(self):
        self.original_target = settings.trading.take_profit_1_pct
        settings.trading.take_profit_1_pct = 2.0

    def tearDown(self):
        settings.trading.take_profit_1_pct = self.original_target
    def test_valid_otm_liquidity_strike_becomes_first_target(self):
        trader = AlpacaPaperTrader.__new__(AlpacaPaperTrader)
        target = AlpacaPaperTrader._first_target_for_row(
            trader,
            {"liquidity_target_price": 105.0},
            100.0,
        )
        self.assertEqual(target, 105.0)

    def test_missing_or_non_otm_target_falls_back_to_configured_percent(self):
        trader = AlpacaPaperTrader.__new__(AlpacaPaperTrader)
        self.assertEqual(
            AlpacaPaperTrader._first_target_for_row(trader, {}, 100.0),
            102.0,
        )
        self.assertEqual(
            AlpacaPaperTrader._first_target_for_row(
                trader,
                {"liquidity_target_price": 99.0},
                100.0,
            ),
            102.0,
        )

if __name__ == "__main__":
    unittest.main()
