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
            # The full-watchlist scanner has no worker by design. Even a stale
            # persisted health snapshot must not make recovery restart it.
            "watchlistOiScanner": {"required": True, "alive": False},
            "stockPositionManager": {"required": True, "alive": True},
            "learningAgent": {"required": True, "alive": True},
            "optionScheduler": {"required": False, "alive": False},
            "stockScheduler": {"required": False, "alive": False},
        })

        self.assertEqual(recovered, ["stockScanner"])
        self.assertEqual(starts, ["stockScanner"])

    def test_dashboard_reuses_full_cache_between_browser_polls(self):
        state = DashboardState.__new__(DashboardState)
        state.dashboard_cache_lock = threading.Lock()
        state.dashboard_cache = {"source": "full"}
        state.dashboard_cache_timestamp = datetime.now().astimezone() - timedelta(seconds=6)
        state.dashboard_refresh_thread = None
        state._dashboard_payload_minimal = lambda: (_ for _ in ()).throw(
            AssertionError("six-second-old full cache should not rebuild")
        )

        payload = DashboardState.dashboard_payload(state)

        self.assertEqual(payload, {"source": "full"})

    def test_background_oi_snapshot_skips_display_analytics_and_interactive_priority(self):
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_cache = {}
        state.oi_finder_chain_cache = {}
        touches = []
        persistence_calls = []
        state.touch_oi_finder_interactive_window = lambda: touches.append(True)
        state._oi_finder_side_rows = lambda chain, side: [
            {"expiry": "2026-08-14", "liquidity_score": 1, "volume": 10, "open_interest": 20}
        ]
        state._oi_finder_selected_expiry_chain_rows = lambda chain: []
        state._oi_finder_current_atm = lambda chain: {}
        state._oi_finder_tos_script_levels = lambda chain: []
        state._option_chain_contracts = lambda chain, side: []
        state._oi_finder_volume_snapshot = lambda *args: {}
        state._oi_finder_volume_momentum = lambda *args: {}
        state._attach_oi_finder_volume_momentum = lambda *args: None
        state._record_oi_finder_daily_chain_snapshot = lambda *args, **kwargs: (
            persistence_calls.append(("daily", kwargs.get("include_history"))) or {}
        )
        state._record_oi_finder_live_wall_snapshot = lambda *args, **kwargs: (
            persistence_calls.append(("wall", kwargs.get("include_history"))) or {}
        )
        state._oi_finder_unusual_otm_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not build unusual activity")
        )
        state._oi_finder_persistent_option_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not build persistence analytics")
        )
        state._option_underlying_day_move = lambda *args: {
            "price": 100.0,
            "change": 1.0,
            "changePercent": 1.0,
        }
        state._chain_implied_volatility = lambda chain: None
        state._option_underlying_price_from_chain = lambda chain: 100.0
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        state.repository = SimpleNamespace(
            option_chain_daily_snapshots=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("background snapshot must not load 183-day history")
            )
        )

        class FakeSchwabClient:
            configured = True

            def get_option_chain(self, *args, **kwargs):
                return {"symbol": "AAPL"}

            def get_quotes(self, symbols):
                return {}

        with patch("api_server.SchwabClient", FakeSchwabClient):
            payload = DashboardState.oi_finder_payload(
                state,
                "AAPL",
                force=True,
                background_snapshot=True,
            )

        self.assertTrue(payload["live"])
        self.assertTrue(payload["analyticsDeferred"])
        self.assertEqual(touches, [])
        self.assertEqual(persistence_calls, [("daily", False), ("wall", False)])

    def test_background_oi_snapshot_preserves_resolved_finder_analytics(self):
        # The paced MAG7/daily collectors run oi_finder_payload with
        # background_snapshot=True, which leaves the display analytics empty. It
        # must NOT clobber the analytics a prior interactive build resolved, or
        # the Finder flashes WAIT FOR DATA every collector cycle. The background
        # build still writes the fresh base chain and does NOT recompute the
        # analytics (the throw-guards below prove it) - it carries them forward.
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_chain_cache = {}
        # A prior interactive build already resolved the analytics for AAPL.
        state.oi_finder_cache = {
            "AAPL": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-14"],
                    "unusualOtmActivity": {
                        "strongestBullish": {"score": 9},
                        "strongestBearish": {"score": 7},
                    },
                    "unusualOtmDashboard": {"x": 1},
                    "persistentActivity": {"y": 1},
                    "dailyLiquidityHeatmap": {"series": [1]},
                    "liveWallTrend": {"series": [1]},
                    "volumeMomentum": {"stale": True},
                },
            )
        }
        touches = []
        state.touch_oi_finder_interactive_window = lambda: touches.append(True)
        state._oi_finder_side_rows = lambda chain, side: [
            {"expiry": "2026-08-14", "liquidity_score": 1, "volume": 10, "open_interest": 20}
        ]
        state._oi_finder_selected_expiry_chain_rows = lambda chain: []
        state._oi_finder_current_atm = lambda chain: {}
        state._oi_finder_tos_script_levels = lambda chain: []
        state._option_chain_contracts = lambda chain, side: []
        state._oi_finder_volume_snapshot = lambda *args: {}
        state._oi_finder_volume_momentum = lambda *args: {}
        state._attach_oi_finder_volume_momentum = lambda *args: None
        state._record_oi_finder_daily_chain_snapshot = lambda *args, **kwargs: {}
        state._record_oi_finder_live_wall_snapshot = lambda *args, **kwargs: {}
        state._oi_finder_unusual_otm_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not recompute unusual activity")
        )
        state._oi_finder_persistent_option_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not recompute persistence analytics")
        )
        state._option_underlying_day_move = lambda *args: {
            "price": 100.0,
            "change": 1.0,
            "changePercent": 1.0,
        }
        state._chain_implied_volatility = lambda chain: None
        state._option_underlying_price_from_chain = lambda chain: 100.0
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        state.repository = SimpleNamespace(
            option_chain_daily_snapshots=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("background snapshot must not load 183-day history")
            )
        )

        class FakeSchwabClient:
            configured = True

            def get_option_chain(self, *args, **kwargs):
                return {"symbol": "AAPL"}

            def get_quotes(self, symbols):
                return {}

        with patch("api_server.SchwabClient", FakeSchwabClient):
            payload = DashboardState.oi_finder_payload(
                state,
                "AAPL",
                force=True,
                background_snapshot=True,
            )

        cached = state.oi_finder_cache["AAPL"][1]
        # Resolved analytics carried forward, not clobbered to the empty stub.
        self.assertEqual(cached["unusualOtmActivity"]["strongestBullish"], {"score": 9})
        self.assertEqual(cached["unusualOtmDashboard"], {"x": 1})
        self.assertEqual(cached["persistentActivity"], {"y": 1})
        self.assertFalse(cached["analyticsDeferred"])
        # ...and the fresh base chain WAS written (the collector still refreshes).
        self.assertTrue(payload["live"])
        self.assertTrue(payload["callRows"])
        # volumeMomentum is the FRESH value, never the preserved stale one.
        self.assertEqual(cached["volumeMomentum"], {})

    def _quick_analytics_state(self, prior=None):
        """A DashboardState stubbed down to the Finder analytics decision points.

        Every history-backed helper throws, so a quick first pass that touches
        one fails loudly instead of silently costing the 85-143s this fix exists
        to remove.
        """
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_chain_cache = {}
        state.oi_finder_cache = dict(prior or {})
        state.touch_oi_finder_interactive_window = lambda: None
        state._oi_finder_side_rows = lambda chain, side: [
            {"expiry": "2026-08-28", "liquidity_score": 1, "volume": 10, "open_interest": 20}
        ]
        state._oi_finder_selected_expiry_chain_rows = lambda chain: []
        state._oi_finder_current_atm = lambda chain: {"expiry": "2026-08-28"}
        state._oi_finder_tos_script_levels = lambda chain: []
        state._option_chain_contracts = lambda chain, side: []
        state._oi_finder_volume_snapshot = lambda *args: {}
        state._oi_finder_volume_momentum = lambda *args: {"fresh": True}
        state._attach_oi_finder_volume_momentum = lambda *args: None
        state._oi_finder_unusual_otm_activity = lambda *args, **kwargs: {
            "available": True,
            "historyReady": bool(kwargs.get("include_history")),
            # Only otm_volume_gt_atm is live-only; the other three comparisons
            # need saved history, so a history-free pass reports 1 of 4.
            "strongestBullish": {"score": 3, "comparisonsAvailable": 4 if kwargs.get("include_history") else 1},
            "strongestBearish": {"score": 2, "comparisonsAvailable": 4 if kwargs.get("include_history") else 1},
        }
        state._record_oi_finder_daily_chain_snapshot = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("quick analytics pass must not record/read the daily heatmap")
        )
        state._record_oi_finder_live_wall_snapshot = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("quick analytics pass must not record/read the live-wall trend")
        )
        state._oi_finder_persistent_option_activity = lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("quick analytics pass must not build persistence analytics")
        )
        state._option_underlying_day_move = lambda *args: {
            "price": 100.0,
            "change": 1.0,
            "changePercent": 1.0,
        }
        state._chain_implied_volatility = lambda chain: None
        state._option_underlying_price_from_chain = lambda chain: 100.0
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        state.repository = SimpleNamespace(
            option_chain_daily_snapshots=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("quick analytics pass must not load 183-day history")
            )
        )
        return state

    @staticmethod
    def _fake_schwab():
        class FakeSchwabClient:
            configured = True

            def get_option_chain(self, *args, **kwargs):
                return {"symbol": "NFLX"}

            def get_quotes(self, symbols):
                return {}

        return FakeSchwabClient

    def test_quick_analytics_pass_resolves_the_decision_board_without_history(self):
        # The decision board renders from strongestBullish/strongestBearish only.
        # A quick pass must produce them - and must not touch any history-backed
        # helper (all of them throw in this fixture).
        state = self._quick_analytics_state()

        with patch("api_server.SchwabClient", self._fake_schwab()):
            payload = DashboardState.oi_finder_payload(
                state, "NFLX", force=True, quick_analytics=True
            )

        self.assertFalse(payload["analyticsDeferred"])
        self.assertEqual(
            payload["unusualOtmActivity"]["strongestBullish"],
            {"score": 3, "comparisonsAvailable": 1},
        )
        self.assertEqual(
            payload["unusualOtmActivity"]["strongestBearish"],
            {"score": 2, "comparisonsAvailable": 1},
        )
        self.assertFalse(payload["unusualOtmActivity"]["historyReady"])
        # volumeMomentum still refreshes on the quick pass; the volume-rate panel
        # reads it and it costs nothing.
        self.assertEqual(payload["volumeMomentum"], {"fresh": True})
        self.assertEqual(
            state.oi_finder_cache["NFLX"][1]["unusualOtmActivity"],
            payload["unusualOtmActivity"],
        )

    def test_quick_analytics_pass_keeps_the_history_extras_it_skipped(self):
        # The quick pass computes none of the history extras, so its write must
        # carry a prior complete build's forward rather than blanking them.
        prior = {
            "NFLX": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-28"],
                    "unusualOtmDashboard": {"x": 1},
                    "persistentActivity": {"y": 1},
                    "dailyLiquidityHeatmap": {"series": [1]},
                    "liveWallTrend": {"series": [1]},
                },
            )
        }
        state = self._quick_analytics_state(prior)

        with patch("api_server.SchwabClient", self._fake_schwab()):
            DashboardState.oi_finder_payload(state, "NFLX", force=True, quick_analytics=True)

        cached = state.oi_finder_cache["NFLX"][1]
        self.assertEqual(cached["unusualOtmDashboard"], {"x": 1})
        self.assertEqual(cached["persistentActivity"], {"y": 1})
        self.assertEqual(cached["dailyLiquidityHeatmap"], {"series": [1]})
        self.assertEqual(cached["liveWallTrend"], {"series": [1]})

    def test_quick_analytics_pass_does_not_downgrade_a_better_evidenced_read(self):
        # A prior full build's picks carry all four comparisons. The quick pass
        # reads no history, so its picks carry one - overwriting the richer read
        # made the board's score oscillate 4/4 -> 1/4 every refresh cycle.
        prior = {
            "NFLX": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-28"],
                    "unusualOtmActivity": {
                        "historyReady": False,
                        "strongestBullish": {"score": 4, "comparisonsAvailable": 4},
                        "strongestBearish": {"score": 3, "comparisonsAvailable": 4},
                    },
                },
            )
        }
        state = self._quick_analytics_state(prior)

        with patch("api_server.SchwabClient", self._fake_schwab()):
            DashboardState.oi_finder_payload(state, "NFLX", force=True, quick_analytics=True)

        activity = state.oi_finder_cache["NFLX"][1]["unusualOtmActivity"]
        self.assertEqual(activity["strongestBullish"], {"score": 4, "comparisonsAvailable": 4})

    def test_quick_analytics_pass_fills_an_equally_thin_prior_read(self):
        # The guard keeps the RICHER read, not merely the older one. A prior
        # quick-pass entry must still be refreshed with live volume.
        prior = {
            "NFLX": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-28"],
                    "unusualOtmActivity": {
                        "strongestBullish": {"score": 0, "comparisonsAvailable": 1},
                        "strongestBearish": {"score": 0, "comparisonsAvailable": 1},
                    },
                },
            )
        }
        state = self._quick_analytics_state(prior)

        with patch("api_server.SchwabClient", self._fake_schwab()):
            DashboardState.oi_finder_payload(state, "NFLX", force=True, quick_analytics=True)

        activity = state.oi_finder_cache["NFLX"][1]["unusualOtmActivity"]
        self.assertEqual(activity["strongestBullish"]["score"], 3)

    def test_otm_pick_comparisons_ignores_the_board_wide_history_flag(self):
        # historyReady is all(history_days >= 5) over EVERY scanned contract, so
        # a few thin far-OTM strikes hold it false while the displayed picks are
        # fully backed (measured TSLA 2026-08-26: 80 signals, both picks 4/4).
        depth = DashboardState._oi_finder_otm_pick_comparisons
        self.assertEqual(depth({"historyReady": False, "strongestBullish": {"comparisonsAvailable": 4}}), 4)
        self.assertEqual(depth({"historyReady": True, "strongestBullish": {"comparisonsAvailable": 1}}), 1)
        # The better-evidenced side wins, so one thin pick cannot mute the read.
        self.assertEqual(depth({
            "strongestBullish": {"comparisonsAvailable": 4},
            "strongestBearish": {"comparisonsAvailable": 1},
        }), 4)
        # Unreported means unknown, never zero - Number-ish coercion of None or
        # a bool must not be treated as a real count.
        self.assertEqual(depth({"strongestBullish": {"comparisonsAvailable": None}}), 0)
        self.assertEqual(depth({"strongestBullish": {"comparisonsAvailable": True}}), 0)
        self.assertEqual(depth({}), 0)
        self.assertEqual(depth(None), 0)

    def test_quick_analytics_pass_does_not_preserve_across_a_front_expiry_roll(self):
        # Same scoping rule as the background-snapshot preserve: a rolled front
        # expiry must not inherit yesterday's extras.
        prior = {
            "NFLX": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-21"],
                    "unusualOtmDashboard": {"x": 1},
                    "persistentActivity": {"y": 1},
                },
            )
        }
        state = self._quick_analytics_state(prior)

        with patch("api_server.SchwabClient", self._fake_schwab()):
            DashboardState.oi_finder_payload(state, "NFLX", force=True, quick_analytics=True)

        cached = state.oi_finder_cache["NFLX"][1]
        self.assertEqual(cached["unusualOtmDashboard"], {})
        self.assertEqual(cached["persistentActivity"], {})

    @staticmethod
    def _refresh_pools_state():
        """A state whose two pools record which one each pass was submitted to."""
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_background_refreshes = set()
        state.submitted = []

        def pool(name):
            def submit(fn):
                state.submitted.append(name)
                fn()

            return SimpleNamespace(submit=submit)

        state.oi_finder_quick_pool = pool("quick")
        state.oi_finder_refresh_pool = pool("refresh")
        return state

    def test_full_finder_refresh_runs_the_quick_pass_on_its_own_pool(self):
        # The split IS the fix. Chaining both passes onto one pool worker meant
        # a 5th open ticker's 2s quick pass queued behind a 4th ticker's 85s
        # history build - measured 2026-08-26: three of six cold tickers lit up
        # in 18s, the rest took 93s+. The quick pass must be submitted to the
        # dedicated pool, and submitted FIRST.
        state = self._refresh_pools_state()
        calls = []
        state.oi_finder_payload = lambda symbol, **kw: calls.append(
            (symbol, kw.get("quick_analytics", False), kw.get("compact", False))
        )

        DashboardState._refresh_oi_finder_in_background(state, "NFLX")

        self.assertEqual(state.submitted, ["quick", "refresh"])
        self.assertEqual(calls, [("NFLX", True, False), ("NFLX", False, False)])
        # Both in-flight keys released, so the next poll can refresh again.
        self.assertEqual(state.oi_finder_background_refreshes, set())

    def test_quick_pass_is_deduped_independently_of_the_history_pass(self):
        # A history build runs for ~85s. Its in-flight key must not suppress a
        # quick pass, or the board stays dark for the whole build.
        state = self._refresh_pools_state()
        state.oi_finder_background_refreshes = {"NFLX"}
        calls = []
        state.oi_finder_payload = lambda symbol, **kw: calls.append(
            kw.get("quick_analytics", False)
        )

        DashboardState._refresh_oi_finder_in_background(state, "NFLX")

        # The history pass is suppressed (already running); the quick pass runs.
        self.assertEqual(state.submitted, ["quick"])
        self.assertEqual(calls, [True])

    def test_compact_and_research_refreshes_skip_the_quick_pass(self):
        # The quick pass only feeds the desktop Finder cache. The compact chain
        # fast path and the research caches have no decision board to unblock.
        for kwargs, label in (({"compact": True}, "compact"), ({"research_section": "flow"}, "flow")):
            state = self._refresh_pools_state()
            calls = []
            state.oi_finder_payload = lambda symbol, **kw: calls.append(
                kw.get("quick_analytics", False)
            )

            DashboardState._refresh_oi_finder_in_background(state, "NFLX", **kwargs)

            self.assertEqual(state.submitted, ["refresh"], label)
            self.assertEqual(calls, [False], label)

    def test_disk_chain_served_to_the_finder_is_marked_analytics_deferred(self):
        # The disk copy is written ONLY by the compact/chain path, so all six
        # Finder analytics in it are {} - and a compact build stamps
        # analyticsDeferred=False because it is not a background snapshot.
        # Serving that to a FULL request unchanged makes an analytics-free
        # payload claim to be complete: needs_full_analytics never fires and the
        # decision board sits on WAIT FOR DATA. Measured 2026-08-26 minutes
        # after a restart - NFLX on a 3h47m-old disk copy, AMD on a 37h one.
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_cache = {}
        state.oi_finder_chain_cache = {}
        state.touch_oi_finder_interactive_window = lambda: None
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        refreshed = []
        state._refresh_oi_finder_in_background = lambda symbol, **kwargs: refreshed.append(symbol)
        state._present_oi_finder_chain_payload = lambda payload, **kwargs: payload

        stale_disk_copy = {
            "symbol": "NFLX",
            "live": True,
            # What a compact build actually writes to disk.
            "analyticsDeferred": False,
            "selectedExpiryChainRows": [{"strike": 81.0}],
            "callRows": [{"strike": 83.0}],
            "putRows": [{"strike": 79.0}],
            "unusualOtmActivity": {},
            "dailyLiquidityHeatmap": {},
            "unusualOtmDashboard": {},
            "persistentActivity": {},
            "volumeMomentum": {},
            "liveWallTrend": {},
        }
        state._load_oi_finder_chain_disk_payload = lambda symbol: dict(stale_disk_copy)
        state._oi_finder_chain_disk_is_servable = staticmethod(lambda *args, **kwargs: True)

        served = DashboardState._oi_finder_payload_impl(state, "NFLX")

        # The chain still paints immediately - that is what the disk cache is for.
        self.assertTrue(served["callRows"])
        self.assertTrue(refreshed, "a disk hit must still revalidate in the background")
        # ...but it must NOT claim its missing analytics are resolved.
        self.assertTrue(
            served["analyticsDeferred"],
            "an analytics-free disk copy must not be served to the Finder as complete",
        )
        cached = state.oi_finder_cache["NFLX"][1]
        self.assertTrue(
            cached["analyticsDeferred"],
            "the cached copy must be marked deferred so needs_full_analytics fires",
        )

    def test_disk_chain_served_to_the_compact_path_is_left_alone(self):
        # The compact chain feed has no decision board to unblock and reads the
        # separate chain cache; stamping it would be noise.
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_cache = {}
        state.oi_finder_chain_cache = {}
        state.touch_oi_finder_interactive_window = lambda: None
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        state._refresh_oi_finder_in_background = lambda symbol, **kwargs: None
        state._present_oi_finder_chain_payload = lambda payload, **kwargs: payload
        state._load_oi_finder_chain_disk_payload = lambda symbol: {
            "symbol": "NFLX",
            "live": True,
            "analyticsDeferred": False,
            "selectedExpiryChainRows": [{"strike": 81.0}],
            "callRows": [{"strike": 83.0}],
            "putRows": [],
        }
        state._oi_finder_chain_disk_is_servable = staticmethod(lambda *args, **kwargs: True)

        served = DashboardState._oi_finder_payload_impl(state, "NFLX", compact=True)

        self.assertFalse(served["analyticsDeferred"])

    def _quote_rows(self, quotes):
        import api_server

        with patch.object(api_server, "USER_SCHWAB_CLIENTS") as schwab, \
                patch.object(api_server, "USER_ALPACA_CLIENTS") as alpaca, \
                patch.object(api_server, "_live_quote_client") as client, \
                patch.dict(api_server._LIVE_QUOTE_CACHE, {}, clear=True):
            schwab.for_user.return_value = None
            alpaca.for_user.return_value = None
            client.return_value.get_quotes.return_value = quotes
            return api_server._live_chart_quote_rows(list(quotes.keys()))

    def test_live_chart_quotes_carry_the_day_move(self):
        # The rails could show a price but never a percentage because this
        # endpoint kept only last_price and dropped change / change_pct /
        # close_price, all of which get_quotes already normalises.
        rows = self._quote_rows({
            "AAPL": {"last_price": 313.04, "change": 3.14, "change_pct": 1.0139, "close_price": 309.90},
        })
        self.assertEqual(rows[0]["symbol"], "AAPL")
        self.assertEqual(rows[0]["lastPrice"], 313.04)
        self.assertEqual(rows[0]["change"], 3.14)
        self.assertEqual(rows[0]["changePercent"], 1.0139)
        self.assertEqual(rows[0]["previousClose"], 309.90)

    def test_day_move_is_recomputed_when_the_provider_omits_the_percent(self):
        # A provider that reports only a last price and a previous close must
        # still produce a percentage rather than a blank badge.
        rows = self._quote_rows({
            "TSLA": {"last_price": 110.0, "close_price": 100.0},
        })
        self.assertEqual(rows[0]["change"], 10.0)
        self.assertEqual(rows[0]["changePercent"], 10.0)

    def test_a_missing_day_move_is_none_not_zero(self):
        # A real 0.00% move and "no data" must not render identically - the rail
        # omits the badge entirely for None and would otherwise claim a flat
        # tape the provider never reported.
        rows = self._quote_rows({"MSFT": {"last_price": 480.0}})
        self.assertIsNone(rows[0]["change"])
        self.assertIsNone(rows[0]["changePercent"])
        self.assertEqual(rows[0]["lastPrice"], 480.0)

    def test_a_zero_previous_close_does_not_produce_an_infinite_move(self):
        rows = self._quote_rows({"BAD": {"last_price": 5.0, "close_price": 0.0}})
        self.assertIsNone(rows[0]["changePercent"])

    def test_a_reported_flat_move_is_kept_as_zero(self):
        rows = self._quote_rows({
            "FLAT": {"last_price": 100.0, "change": 0.0, "change_pct": 0.0, "close_price": 100.0},
        })
        self.assertEqual(rows[0]["change"], 0.0)
        self.assertEqual(rows[0]["changePercent"], 0.0)

    def test_background_oi_snapshot_does_not_preserve_across_a_front_expiry_roll(self):
        # The preserve guard is scoped to a matching front expiry; a rolled
        # expiry must fall back to today's stub behaviour and self-heal on the
        # next interactive poll rather than show stale analytics for the wrong
        # expiry.
        state = DashboardState.__new__(DashboardState)
        state.oi_finder_lock = threading.Lock()
        state.oi_finder_chain_cache = {}
        state.oi_finder_cache = {
            "AAPL": (
                datetime.now().astimezone(),
                {
                    "analyticsDeferred": False,
                    "expiries": ["2026-08-07"],  # yesterday's front expiry
                    "unusualOtmActivity": {"strongestBullish": {"score": 9}},
                },
            )
        }
        state.touch_oi_finder_interactive_window = lambda: None
        state._oi_finder_side_rows = lambda chain, side: [
            {"expiry": "2026-08-14", "liquidity_score": 1, "volume": 10, "open_interest": 20}
        ]
        state._oi_finder_selected_expiry_chain_rows = lambda chain: []
        state._oi_finder_current_atm = lambda chain: {}
        state._oi_finder_tos_script_levels = lambda chain: []
        state._option_chain_contracts = lambda chain, side: []
        state._oi_finder_volume_snapshot = lambda *args: {}
        state._oi_finder_volume_momentum = lambda *args: {}
        state._attach_oi_finder_volume_momentum = lambda *args: None
        state._record_oi_finder_daily_chain_snapshot = lambda *args, **kwargs: {}
        state._record_oi_finder_live_wall_snapshot = lambda *args, **kwargs: {}
        state._oi_finder_unusual_otm_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not recompute unusual activity")
        )
        state._oi_finder_persistent_option_activity = lambda *args: (_ for _ in ()).throw(
            AssertionError("background snapshot must not recompute persistence analytics")
        )
        state._option_underlying_day_move = lambda *args: {"price": 100.0, "change": 1.0, "changePercent": 1.0}
        state._chain_implied_volatility = lambda chain: None
        state._option_underlying_price_from_chain = lambda chain: 100.0
        state._oi_finder_snapshot_schedule_payload = lambda: {}
        state.repository = SimpleNamespace(
            option_chain_daily_snapshots=lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("background snapshot must not load 183-day history")
            )
        )

        class FakeSchwabClient:
            configured = True

            def get_option_chain(self, *args, **kwargs):
                return {"symbol": "AAPL"}

            def get_quotes(self, symbols):
                return {}

        with patch("api_server.SchwabClient", FakeSchwabClient):
            DashboardState.oi_finder_payload(state, "AAPL", force=True, background_snapshot=True)

        cached = state.oi_finder_cache["AAPL"][1]
        # Expiry mismatch -> no preserve -> stub behaviour (deferred stays True).
        self.assertTrue(cached["analyticsDeferred"])
        self.assertEqual(cached.get("unusualOtmActivity"), {})

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
