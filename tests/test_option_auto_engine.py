import json
import math
import os
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone

import pandas as pd

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

import api_server
from api_server import DashboardState
from config import settings
from data.schwab_client import TIMEFRAME_PARAMS


class FakeOptionRepository:
    def __init__(self, rows=None):
        self.rows = rows or []
        self.events = []
        self.logged_trades = []
        self.catalysts = [
            {
                "symbol": "PLTR",
                "headline": "PLTR option catalyst test headline",
                "sentiment": "positive",
                "score": 82,
            }
        ]

    def get_open_option_trades(self, profile_id=None):
        open_rows = [
            row
            for row in self.rows
            if not row.get("closed_at") and str(row.get("status", "")).lower() in api_server.OPTION_AUTO_ACTIVE_STATUSES
        ]
        return pd.DataFrame(open_rows)

    def get_option_trade_history(self, limit=50, profile_id=None, broker_only=False):
        rows = list(self.rows)
        if profile_id:
            rows = [row for row in rows if row.get("account_profile_id") in {profile_id, None, ""}]
        if broker_only:
            rows = [row for row in rows if str(row.get("broker_order_id") or "").strip()]
        return pd.DataFrame(rows[:limit])

    def log_option_trade(self, **kwargs):
        row = {
            **kwargs,
            "id": len(self.rows) + 1,
            "opened_at": "2026-07-02T14:00:00",
            "closed_at": None,
            "pnl": 0.0,
            "account_mode": "paper",
        }
        self.rows.append(row)
        self.logged_trades.append(row)

    def update_option_trade_plan(self, client_order_id, stop_price=None, target_price=None, notes=None, analysis_json=None):
        row = self._row(client_order_id)
        if stop_price is not None:
            row["stop_price"] = stop_price
        if target_price is not None:
            row["target_price"] = target_price
        if notes is not None:
            row["notes"] = notes
        if analysis_json is not None:
            row["analysis_json"] = analysis_json

    def update_option_trade_status(self, client_order_id, status, pnl=None, exit_price=None, closed_at=None, notes=None):
        row = self._row(client_order_id)
        row["status"] = status
        if pnl is not None:
            row["pnl"] = pnl
        if exit_price is not None:
            row["exit_price"] = exit_price
        if closed_at is not None:
            row["closed_at"] = closed_at
        if notes is not None:
            row["notes"] = notes

    def update_option_trade(self, client_order_id, **kwargs):
        row = self._row(client_order_id)
        for key, value in kwargs.items():
            if value is not None:
                row[key] = value

    def option_trades_today_count(self, profile_id=None, broker_only=False):
        rows = [
            row for row in self.rows
            if not profile_id or row.get("account_profile_id") == profile_id
        ]
        if broker_only:
            rows = [row for row in rows if str(row.get("broker_order_id") or "").strip()]
        return len(rows)

    def get_recent_catalysts(self, limit=50):
        return pd.DataFrame(self.catalysts[:limit])

    def log_bot_event(self, *args):
        self.events.append(args)

    def set_app_setting(self, key, value):
        self.events.append(("setting", key, value))

    def _row(self, client_order_id):
        for row in self.rows:
            if row["client_order_id"] == client_order_id:
                return row
        raise AssertionError(f"Missing row {client_order_id}")


class FakeCredentials:
    profile_id = "default-paper"
    label = "Unit Paper"
    paper = True


class FakeOptionCredentials:
    profile_id = "paper3"
    label = "OPTION TRADE"
    paper = True


class FakeBrokerClient:
    credentials = FakeCredentials()


class FakeOptionTrader:
    def get_status(self):
        return types.SimpleNamespace(
            account_equity=9992.31,
            cash=2500.0,
            last_equity=10007.95,
            daily_change=-15.64,
            daily_change_pct=-0.16,
            buying_power=39729.24,
            daily_pnl=0.0,
            trades_today=0,
            open_positions=0,
            open_orders=0,
            account_mode="paper",
        )


class FakeOptionClient:
    def __init__(self, buying_power=39729.24):
        self.credentials = FakeOptionCredentials()
        self.is_paper = True
        self.account = types.SimpleNamespace(
            status="ACTIVE",
            equity="9992.31",
            cash="2500.0",
            last_equity="10007.95",
            buying_power=str(buying_power),
            options_buying_power=str(buying_power),
            account_number="PA3TEST",
            account_blocked=False,
            trading_blocked=False,
            trade_suspended_by_user=False,
        )
        self.orders = []
        self.positions = {}
        self.price_lookup = lambda symbol: None
        self.account_requests = 0

    def normalize_option_symbol(self, symbol):
        return str(symbol or "").strip().upper().replace(" ", "")

    def is_option_symbol(self, symbol):
        normalized = self.normalize_option_symbol(symbol)
        return len(normalized) >= 15 and normalized[-9:-8] in {"C", "P"} and normalized[-8:].isdigit()

    def get_account(self):
        self.account_requests += 1
        return self.account

    def _update_position_prices(self):
        for symbol, position in list(self.positions.items()):
            price = self.price_lookup(symbol)
            if price is None:
                continue
            avg_entry = float(position.avg_entry_price)
            qty = abs(float(position.qty))
            position.current_price = str(price)
            position.unrealized_pl = str(round((price - avg_entry) * qty * 100, 2))
            position.unrealized_intraday_pl = "0.0"

    def get_option_positions(self):
        self._update_position_prices()
        return list(self.positions.values())

    def get_option_orders(self, status=None, symbols=None, after=None, until=None, limit=500):
        normalized_symbols = {self.normalize_option_symbol(symbol) for symbol in symbols or [] if str(symbol).strip()}
        rows = list(self.orders)
        if normalized_symbols:
            rows = [order for order in rows if self.normalize_option_symbol(order.symbol) in normalized_symbols]
        status_name = str(status or "").lower()
        if "open" in status_name:
            rows = [order for order in rows if str(order.status).lower() not in {"filled", "canceled", "rejected", "expired"}]
        return rows[: limit or len(rows)]

    def submit_option_limit_order(self, symbol, qty, limit_price, client_order_id, position_intent="buy_to_open"):
        normalized_symbol = self.normalize_option_symbol(symbol)
        intent = str(position_intent).lower()
        side = "sell" if "sell" in intent else "buy"
        order = types.SimpleNamespace(
            id=f"ord-{len(self.orders) + 1}",
            client_order_id=client_order_id,
            symbol=normalized_symbol,
            status="filled",
            qty=str(int(qty)),
            filled_qty=str(int(qty)),
            limit_price=limit_price,
            filled_avg_price=limit_price,
            submitted_at=datetime.now(timezone.utc),
            side=side,
        )
        self.orders.append(order)
        current_price = self.price_lookup(normalized_symbol)
        if current_price is None:
            current_price = limit_price
        if "buy_to_open" in intent:
            self.positions[normalized_symbol] = types.SimpleNamespace(
                symbol=normalized_symbol,
                qty=str(int(qty)),
                avg_entry_price=str(limit_price),
                current_price=str(current_price),
                unrealized_pl=str(round((current_price - limit_price) * int(qty) * 100, 2)),
                unrealized_intraday_pl="0.0",
                asset_class="us_option",
            )
        elif "sell_to_close" in intent:
            existing = self.positions.get(normalized_symbol)
            if existing is not None:
                remaining = max(int(float(existing.qty)) - int(qty), 0)
                if remaining <= 0:
                    self.positions.pop(normalized_symbol, None)
                else:
                    existing.qty = str(remaining)
                    existing.current_price = str(current_price)
                    existing.unrealized_pl = str(round((current_price - float(existing.avg_entry_price)) * remaining * 100, 2))
        return order

    def cancel_order_by_id(self, order_id):
        for order in self.orders:
            if str(order.id) == str(order_id):
                order.status = "canceled"
                order.canceled_at = datetime.now(timezone.utc)
                return order
        raise ValueError(f"order not found: {order_id}")


class FakeChainMarket:
    def __init__(self, mid=0.50):
        self.mid = mid
        self.requests = []
        self.stream_requests = []
        self.chart_requests = []
        self.chart_bars = None

    def ensure_streaming(self, symbols):
        self.stream_requests.append([str(symbol).upper() for symbol in symbols])

    def get_chart_bars(self, symbol, timeframe="5Min", days_back=2):
        self.chart_requests.append((symbol, timeframe, days_back))
        if self.chart_bars is not None:
            return self.chart_bars
        return pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-07-02T13:30:00Z"),
                    "open": 125.0,
                    "high": 126.25,
                    "low": 124.75,
                    "close": 126.0,
                    "volume": 1000000,
                }
            ]
        )

    def get_option_chain(self, symbol, contract_type="CALL", strike_count=80):
        self.requests.append((symbol, contract_type, strike_count))
        bid = round(self.mid - 0.05, 2)
        ask = round(self.mid + 0.05, 2)
        return {
            "symbol": symbol,
            "underlyingPrice": 126.25,
            "callExpDateMap": {
                "2026-07-02:0": {
                    "126.0": [
                        {
                            "symbol": "PLTR  260702C00126000",
                            "bid": 1.70,
                            "ask": 1.90,
                            "delta": 0.4718,
                            "strikePrice": 126,
                            "totalVolume": 5000,
                            "openInterest": 3000,
                            "expirationDate": "2026-07-02",
                            "daysToExpiration": 0,
                        }
                    ],
                    "128.0": [
                        {
                            "symbol": "PLTR  260702C00128000",
                            "bid": 0.69,
                            "ask": 0.79,
                            "delta": 0.2516,
                            "strikePrice": 128,
                            "totalVolume": 900,
                            "openInterest": 700,
                            "expirationDate": "2026-07-02",
                            "daysToExpiration": 0,
                        }
                    ],
                    "129.0": [
                        {
                            "symbol": "PLTR  260702C00129000",
                            "bid": bid,
                            "ask": ask,
                            "delta": 0.1701,
                            "strikePrice": 129,
                            "totalVolume": 1500,
                            "openInterest": 1200,
                            "expirationDate": "2026-07-02",
                            "daysToExpiration": 0,
                        }
                    ],
                    "130.0": [
                        {
                            "symbol": "PLTR  260702C00130000",
                            "bid": 0.31,
                            "ask": 0.37,
                            "delta": 0.1108,
                            "strikePrice": 130,
                            "totalVolume": 800,
                            "openInterest": 500,
                            "expirationDate": "2026-07-02",
                            "daysToExpiration": 0,
                        }
                    ],
                },
                "2027-12-17:533": {
                    "126.0": [
                        {
                            "symbol": "PLTR  271217C00126000",
                            "bid": 7.45,
                            "ask": 7.65,
                            "delta": 0.198,
                            "strikePrice": 126,
                            "expirationDate": "2027-12-17",
                            "daysToExpiration": 533,
                        }
                    ],
                }
            },
            "putExpDateMap": {
                "2026-07-02:0": {
                    "126.0": [
                        {
                            "symbol": "PLTR  260702P00126000",
                            "bid": 1.45,
                            "ask": 1.65,
                            "delta": -0.5282,
                            "strikePrice": 126,
                            "totalVolume": 4100,
                            "openInterest": 2800,
                            "expirationDate": "2026-07-02",
                            "daysToExpiration": 0,
                        }
                    ]
                },
                "2027-12-17:533": {
                    "126.0": [
                        {
                            "symbol": "PLTR  271217P00126000",
                            "bid": 8.00,
                            "ask": 8.20,
                            "delta": -0.802,
                            "strikePrice": 126,
                            "expirationDate": "2027-12-17",
                            "daysToExpiration": 533,
                        }
                    ]
                }
            },
        }


class FakeTslaLiquidityChainMarket(FakeChainMarket):
    def __init__(
        self,
        mid=1.25,
        underlying_price=420.50,
        atm_volume=45770,
        atm_open_interest=3684,
        target_volume=17210,
        target_open_interest=2634,
    ):
        super().__init__(mid=mid)
        self.underlying_price = underlying_price
        self.atm_volume = atm_volume
        self.atm_open_interest = atm_open_interest
        self.target_volume = target_volume
        self.target_open_interest = target_open_interest

    def get_option_chain(self, symbol, contract_type="CALL", strike_count=80):
        self.requests.append((symbol, contract_type, strike_count))
        return {
            "symbol": symbol,
            "underlyingPrice": self.underlying_price,
            "callExpDateMap": {
                "2026-07-08:2": {
                    "420.0": [
                        {
                            "symbol": "TSLA  260708C00420000",
                            "bid": 4.90,
                            "ask": 5.10,
                            "delta": 0.42,
                            "strikePrice": 420,
                            "totalVolume": self.atm_volume,
                            "openInterest": self.atm_open_interest,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ],
                    "425.0": [
                        {
                            "symbol": "TSLA  260708C00425000",
                            "bid": 1.35,
                            "ask": 1.55,
                            "delta": 0.30,
                            "strikePrice": 425,
                            "totalVolume": 15080,
                            "openInterest": 1474,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ],
                    "430.0": [
                        {
                            "symbol": "TSLA  260708C00430000",
                            "bid": 1.20,
                            "ask": 1.30,
                            "delta": 0.12,
                            "strikePrice": 430,
                            "totalVolume": self.target_volume,
                            "openInterest": self.target_open_interest,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ],
                    "432.5": [
                        {
                            "symbol": "TSLA  260708C00432500",
                            "bid": 0.72,
                            "ask": 0.82,
                            "delta": 0.16,
                            "strikePrice": 432.5,
                            "totalVolume": 5280,
                            "openInterest": 1237,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ],
                    "440.0": [
                        {
                            "symbol": "TSLA  260708C00440000",
                            "bid": 0.25,
                            "ask": 0.35,
                            "delta": 0.08,
                            "strikePrice": 440,
                            "totalVolume": 9030,
                            "openInterest": 1261,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ],
                }
            },
            "putExpDateMap": {
                "2026-07-08:2": {
                    "420.0": [
                        {
                            "symbol": "TSLA  260708P00420000",
                            "bid": 1.70,
                            "ask": 1.90,
                            "delta": -0.48,
                            "strikePrice": 420,
                            "totalVolume": 11300,
                            "openInterest": 1290,
                            "expirationDate": "2026-07-08",
                            "daysToExpiration": 2,
                        }
                    ]
                }
            },
        }


def bind_state_methods(state, names):
    for name in names:
        setattr(state, name, types.MethodType(getattr(DashboardState, name), state))


def make_state(rows=None, mid=0.50, buying_power=39729.24):
    state = DashboardState.__new__(DashboardState)
    state.active_profile_id = "default-paper"
    state.client = FakeBrokerClient()
    state.option_credentials = FakeOptionCredentials()
    state.option_profile_id = "paper3"
    state.option_trader = FakeOptionTrader()
    state.option_client = FakeOptionClient(buying_power=buying_power)
    state.repository = FakeOptionRepository(rows)
    state.market_data_client = FakeChainMarket(mid=mid)
    state.option_client.price_lookup = lambda symbol, market=state.market_data_client: market.mid
    for row in rows or []:
        option_symbol = state.option_client.normalize_option_symbol(row.get("option_symbol") or "")
        quantity = int(float(row.get("quantity") or 0))
        entry = float(row.get("entry_price") or 0.0)
        if option_symbol and quantity > 0 and str(row.get("status") or "").lower() in api_server.OPTION_AUTO_ACTIVE_STATUSES:
            state.option_client.positions[option_symbol] = types.SimpleNamespace(
                symbol=option_symbol,
                qty=str(quantity),
                avg_entry_price=str(entry),
                current_price=str(mid),
                unrealized_pl=str(round((mid - entry) * quantity * 100, 2)),
                unrealized_intraday_pl="0.0",
                asset_class="us_option",
            )
    state.option_risk_settings = {
        "dailyTradeAmount": "500",
        "tradeAmount": "150",
        "contractQuantity": "",
        "stopLossPercent": "20",
        "firstProfitTargetPercent": "100",
        "firstProfitTargetCons": "2",
        "firstProfitTargetSellMode": "contracts",
        "firstProfitTargetSellValue": "2",
        "runnerLockStepPercent": "50",
    }
    state.option_bot_config = {
        "approvalMode": "automatic",
        "spreadFilter": "",
        "deltaTarget": "0.20",
        "expectedMove": "",
        "contractPolicy": "only_long_call",
        "watchlistSource": "option",
    }
    state.option_watchlist = ["PLTR"]
    state.option_candidate_results = pd.DataFrame()
    state.option_plan_blocks = []
    state.option_scan_timestamp = None
    state.option_bot_state = "Stopped"
    state.option_entry_lock = threading.RLock()
    state.option_supervisor = api_server.OptionLLMSupervisor(enabled=False)
    state.option_supervisor_report = DashboardState._empty_option_supervisor_report(state)
    state.option_bot_message = ""
    state.action_message = ""
    state._option_market_hours_open = types.MethodType(lambda self: True, state)
    state._option_entry_window_open = types.MethodType(lambda self, now_et=None: True, state)
    state.dashboard_payload = types.MethodType(lambda self: {"ok": True}, state)
    bind_state_methods(
        state,
        [
            "_normalize_option_symbol",
            "_parse_numeric_guardrail",
            "_safe_float",
            "_option_order_cost",
            "_occ_underlying",
            "_option_client_order_id",
            "_option_tradeable_buying_power",
            "_option_account_gate",
            "_option_chain_contracts",
            "_option_underlying_price_from_chain",
            "_option_liquidity_score",
            "_option_combined_liquidity_score",
            "_option_oi_wall_plan",
            "_option_liquidity_strike_plan",
            "_contract_mid_price",
            "_option_spread_allowed",
            "_expected_move_for_expiry",
            "_select_option_contract",
            "_option_percent_setting",
            "_option_contract_quantity",
            "_option_target_1_contracts",
            "_option_runner_lock_step_percent",
            "_option_stop_loss_config",
            "_option_initial_trade_plan",
            "_option_ema20_underlying_break",
            "_option_underlying_last_price",
            "_safe_json_loads",
            "_option_trade_plan_state",
            "_option_analysis_payload",
            "_option_watchlist_source",
            "_option_watchlist_source_label",
            "_active_option_watchlist",
            "_resolve_option_contract",
            "_submit_option_trade_request",
            "_submit_option_trade_request_unlocked",
            "_option_quote_for_trade",
            "_option_broker_snapshot",
            "_option_broker_orders_payload",
            "_sync_option_broker_state",
            "_cancel_stale_option_buy_orders_result",
            "_submit_option_exit_order",
            "_option_marked_pnl",
            "_close_option_trade",
            "_option_trade_row_for_contract",
            "_option_position_symbol_for_close",
            "_close_option_position_result",
            "close_option_position",
            "close_all_option_positions",
            "cancel_stale_option_buy_orders",
            "manage_option_positions_now",
            "chart_payload",
            "option_strategy_frame_for_symbols",
            "manage_option_paper_trades",
            "_active_option_trade_underlyings",
            "_active_option_contracts",
            "_option_account_credentials",
            "_option_account_profile_id",
            "_account_status_payload",
            "_option_account_status_payload",
            "_option_account_payload",
            "_option_positions_frame",
            "_apply_option_entry_logic",
            "_option_signal_source_bars",
            "_aggregate_option_bars",
            "_pct_change_vs_bars_back",
            "_option_signal_checks",
            "_option_candidate_symbol_set",
            "_option_no_candidate_results",
            "_option_scan_coverage_payload",
            "_empty_option_supervisor_report",
            "_option_supervisor_context",
            "_refresh_option_supervisor_report",
            "update_option_risk_settings",
            "log_option_paper_trade",
            "_plan_option_paper_trades",
        ],
    )
    return state


def open_pltr_row(state):
    selected = {
        "symbol": "PLTR260702C00129000",
        "mid": 0.50,
        "bid": 0.45,
        "ask": 0.55,
        "delta": 0.1701,
        "expected_move": 3.35,
        "expiry_date": "2026-07-02",
        "strike_price": 129,
    }
    quantity = state._option_contract_quantity(selected["mid"])
    plan = state._option_initial_trade_plan(selected["mid"], quantity, selected, {"setup_name": "EMA + VWAP + ORB"})
    return {
        "client_order_id": "option-paper-PLTR-test",
        "underlying_symbol": "PLTR",
        "option_symbol": selected["symbol"],
        "quantity": quantity,
        "entry_price": selected["mid"],
        "stop_price": plan["runner_stop"],
        "target_price": plan["take_profit_1"],
        "status": "position_open",
        "closed_at": None,
        "pnl": 0.0,
        "exit_price": None,
        "analysis_json": json.dumps(plan),
        "notes": "",
    }


class OptionEngineUnitTests(unittest.TestCase):
    def test_option_entry_window_closes_at_345_pm_et(self):
        state = make_state()
        state._option_market_hours_open = types.MethodType(lambda self: True, state)

        self.assertFalse(DashboardState._option_entry_window_open(state, "2026-07-14T09:29:59-04:00"))
        self.assertTrue(DashboardState._option_entry_window_open(state, "2026-07-14T09:30:00-04:00"))
        self.assertTrue(DashboardState._option_entry_window_open(state, "2026-07-14T15:44:59-04:00"))
        self.assertFalse(DashboardState._option_entry_window_open(state, "2026-07-14T15:45:00-04:00"))

    def test_option_entry_cutoff_blocks_order_and_journal_submission(self):
        state = make_state()
        state._option_entry_window_open = types.MethodType(lambda self, now_et=None: False, state)
        contract, error = state._select_option_contract("PLTR")
        self.assertEqual(error, "")

        result = state._submit_option_trade_request(
            "PLTR",
            contract["symbol"],
            "Only Long Call",
            quantity=3,
            entry_price=contract["mid"],
            selected_contract_override=contract,
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "entry_window_closed")
        self.assertEqual(state.option_client.orders, [])
        self.assertEqual(state.repository.logged_trades, [])

    def setUp(self):
        self.previous_provider = api_server.settings.market_data_provider
        api_server.settings.market_data_provider = "schwab"

    def tearDown(self):
        api_server.settings.market_data_provider = self.previous_provider

    def test_dashboard_payload_returns_minimal_immediately_and_builds_full_snapshot_in_background(self):
        state = DashboardState.__new__(DashboardState)
        state.repository = FakeOptionRepository()
        state.dashboard_cache = None
        state.dashboard_cache_timestamp = None
        state.dashboard_refresh_thread = None
        state.dashboard_cache_lock = threading.Lock()

        build_calls = []

        def fake_build_dashboard_payload(self):
            build_calls.append("built")
            return {"scannerHistory": [{"symbol": "AMDL"}], "scannerHistoryDays": [{"scan_date": "2026-07-09"}]}

        state._build_dashboard_payload = types.MethodType(fake_build_dashboard_payload, state)
        state._dashboard_payload_minimal = types.MethodType(
            lambda self: {"scannerHistory": [], "scannerHistoryDays": []},
            state,
        )

        payload = DashboardState.dashboard_payload(state)

        self.assertEqual(payload["scannerHistory"], [])
        for _ in range(100):
            if state.dashboard_cache and state.dashboard_cache.get("scannerHistory"):
                break
            time.sleep(0.01)
        self.assertEqual(build_calls, ["built"])
        self.assertEqual(state.dashboard_cache["scannerHistory"][0]["symbol"], "AMDL")

    def test_oi_priority_meta_uses_score_for_a_plus_even_with_atm_lead_summary(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 71,
                "atm_volume": 112,
                "open_interest": 28,
                "atm_open_interest": 78,
                "delta": 0.419,
                "expected_move": 4.775,
                "one_hour_close_change_pct": 9.77,
                "days_to_expiration": 1,
                "mid": 1.825,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_volume_trend": True,
                "stock_tos_rvol_any_pass": True,
                "stock_tos_rvol_5m": 3.13,
                "flow_type": "Big real-time volume only",
                "flow_summary": "Big real-time volume only / Big OI only",
                "liquidity_winner": "ATM > OTM",
                "liquidity_summary": "ATM > OTM / OTM > ATM",
            },
        )
        self.assertEqual(result["strength_score"], 82)
        self.assertEqual(result["priority_label"], "A+ HOT")
        self.assertTrue(result["trade_eligible"])
        self.assertTrue(result["display_row"])
        self.assertEqual(result["signal_shape_label"], "Clean Flow")

    def test_oi_priority_meta_keeps_mixed_shape_informational_when_score_is_a_plus(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 1200,
                "atm_volume": 300,
                "open_interest": 300,
                "atm_open_interest": 900,
                "delta": 0.30,
                "expected_move": 6.0,
                "change_pct": 3.0,
                "days_to_expiration": 2,
                "mid": 2.0,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_tos_rvol_any_pass": True,
                "flow_type": "Big real-time volume only",
                "liquidity_winner": "OTM > ATM",
            },
        )

        self.assertEqual(result["priority_label"], "A+ HOT")
        self.assertTrue(result["trade_eligible"])
        self.assertEqual(result["signal_shape_label"], "Mixed Flow")

    def test_oi_priority_meta_caps_clean_atm_dominance_at_a_active(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 220,
                "atm_volume": 500,
                "open_interest": 300,
                "atm_open_interest": 900,
                "delta": 0.20,
                "expected_move": 5.5,
                "one_hour_close_change_pct": 4.2,
                "days_to_expiration": 2,
                "mid": 4.2,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_volume_trend": True,
                "stock_tos_rvol_any_pass": True,
                "stock_tos_rvol_5m": 4.1,
                "flow_type": "Big real-time volume only",
                "liquidity_winner": "ATM > OTM",
            },
        )
        self.assertGreaterEqual(result["strength_score"], 65)
        self.assertEqual(result["priority_label"], "A ACTIVE")
        self.assertTrue(result["trade_eligible"])
        self.assertTrue(result["display_row"])
        self.assertEqual(result["signal_shape_label"], "Clean Flow")

    def test_oi_priority_meta_caps_five_minute_early_alert_at_watchlist(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 5000,
                "atm_volume": 100,
                "open_interest": 7000,
                "atm_open_interest": 200,
                "delta": 0.30,
                "expected_move": 7.0,
                "change_pct": 4.0,
                "days_to_expiration": 2,
                "mid": 3.0,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_volume_trend": True,
                "stock_tos_rvol_any_pass": False,
                "stock_tos_rvol_5m_early_alert": True,
                "stock_tos_rvol_5m": 3.5,
                "flow_type": "Big OI only",
                "liquidity_winner": "OTM > ATM",
            },
        )
        self.assertEqual(result["priority_label"], "Watchlist")
        self.assertFalse(result["trade_eligible"])
        self.assertFalse(result["display_row"])
        self.assertEqual(result["rvol_confirmation"], "5m Early")

    def test_oi_priority_meta_allows_fast_momentum_without_waiting_for_15m_rvol(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 5000,
                "atm_volume": 100,
                "open_interest": 7000,
                "atm_open_interest": 200,
                "delta": 0.30,
                "expected_move": 7.0,
                "change_pct": 4.0,
                "days_to_expiration": 2,
                "mid": 3.0,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_volume_trend": True,
                "stock_tos_rvol_any_pass": False,
                "stock_tos_rvol_5m_early_alert": True,
                "stock_fast_momentum_score": 2,
                "stock_tos_rvol_5m": 3.5,
                "flow_type": "Big OI only",
                "liquidity_winner": "OTM > ATM",
            },
        )

        self.assertEqual(result["priority_label"], "A+ HOT")
        self.assertTrue(result["trade_eligible"])
        self.assertTrue(result["fast_momentum_entry_pass"])
        self.assertEqual(result["rvol_confirmation"], "Confirmed")

    def test_oi_priority_meta_keeps_clean_otm_dominance_as_a_plus_hot(self):
        state = make_state()
        result = DashboardState._oi_priority_meta(
            state,
            {
                "volume": 1200,
                "atm_volume": 300,
                "open_interest": 2400,
                "atm_open_interest": 600,
                "delta": 0.29,
                "expected_move": 6.9,
                "one_hour_close_change_pct": 4.13,
                "days_to_expiration": 1,
                "mid": 1.525,
                "stock_above_vwap": True,
                "stock_ema_stack": True,
                "stock_volume_trend": True,
                "stock_tos_rvol_any_pass": True,
                "stock_tos_rvol_5m": 3.87,
                "flow_type": "Big OI only",
                "liquidity_winner": "OTM > ATM",
            },
        )
        self.assertEqual(result["strength_score"], 100)
        self.assertEqual(result["priority_label"], "A+ HOT")
        self.assertTrue(result["trade_eligible"])
        self.assertTrue(result["display_row"])
        self.assertEqual(result["signal_shape_label"], "Clean Flow")

    def test_option_initial_plan_calculates_stop_target_quantity_and_runner(self):
        state = make_state()
        selected_contract = {
            "symbol": "PLTR260702C00129000",
            "mid": 0.50,
            "bid": 0.45,
            "ask": 0.55,
            "delta": 0.1701,
            "expected_move": 3.35,
            "expiry_date": "2026-07-02",
            "strike_price": 129,
        }

        quantity = state._option_contract_quantity(selected_contract["mid"])
        plan = state._option_initial_trade_plan(selected_contract["mid"], quantity, selected_contract, {"setup_name": "EMA + VWAP + ORB"})

        self.assertEqual(quantity, 3)
        self.assertEqual(plan["runner_stop"], 0.4)
        self.assertEqual(plan["take_profit_1"], 1.0)
        self.assertEqual(plan["contracts_to_sell_at_target_1"], 2)
        self.assertEqual(plan["remaining_quantity"], 3)
        self.assertEqual(plan["runner_stop_locked_pct"], -20.0)

    def test_option_empty_stop_uses_underlying_ema20_candle_stop(self):
        state = make_state()
        state.option_risk_settings["stopLossPercent"] = ""
        selected_contract = {
            "symbol": "PLTR260702C00129000",
            "mid": 0.50,
            "bid": 0.45,
            "ask": 0.55,
            "delta": 0.1701,
            "expected_move": 3.35,
            "expiry_date": "2026-07-02",
            "strike_price": 129,
        }

        plan = state._option_initial_trade_plan(selected_contract["mid"], 3, selected_contract, {"setup_name": "EMA + VWAP + ORB"})

        self.assertEqual(plan["stop_loss_mode"], "ema20_candle")
        self.assertEqual(plan["stop_loss_label"], "5-minute candle close below EMA20")
        self.assertIsNone(plan["stop_loss_percent"])
        self.assertIsNone(plan["runner_stop"])
        self.assertIsNone(plan["runner_stop_locked_pct"])

    def test_spread_filter_supports_adaptive_absolute_and_percent_cutoffs(self):
        state = make_state()

        state.option_bot_config["spreadFilter"] = ""
        self.assertTrue(state._option_spread_allowed(0.80, 18.40))

        state.option_bot_config["spreadFilter"] = "0.05"
        self.assertTrue(state._option_spread_allowed(0.04, 0.50))
        self.assertFalse(state._option_spread_allowed(0.20, 0.50))
        self.assertTrue(state._option_spread_allowed(0.80, 18.40))
        self.assertFalse(state._option_spread_allowed(3.00, 18.40))

        state.option_bot_config["spreadFilter"] = "$0.05"
        self.assertTrue(state._option_spread_allowed(0.04, 0.50))
        self.assertFalse(state._option_spread_allowed(0.80, 18.40))

        state.option_bot_config["spreadFilter"] = "10%"
        self.assertTrue(state._option_spread_allowed(0.04, 0.50))
        self.assertFalse(state._option_spread_allowed(0.06, 0.50))

        state.option_bot_config["spreadFilter"] = "10"
        self.assertTrue(state._option_spread_allowed(0.80, 18.40))
        self.assertFalse(state._option_spread_allowed(3.00, 18.40))

    def test_option_contract_quantity_can_override_trade_amount(self):
        state = make_state()
        state.option_risk_settings["tradeAmount"] = "1000"
        state.option_risk_settings["contractQuantity"] = "2 cons"

        self.assertEqual(state._option_contract_quantity(0.50), 2)

    def test_active_option_underlyings_include_open_broker_buy_orders(self):
        state = make_state()
        state.option_client.orders.append(
            types.SimpleNamespace(
                id="ord-open-buy",
                client_order_id="option-TSLA-test",
                symbol="TSLA260710C00185000",
                status="new",
                qty="2",
                filled_qty="0",
                limit_price=0.50,
                filled_avg_price=None,
                submitted_at=datetime.now(timezone.utc),
                side="buy",
            )
        )

        active = state._active_option_trade_underlyings(state._option_broker_snapshot())

        self.assertIn("TSLA", active)

    def test_same_option_contract_cannot_be_submitted_twice(self):
        state = make_state()
        state.option_risk_settings["contractQuantity"] = "3"
        contract, error = state._select_option_contract("PLTR")
        self.assertEqual(error, "")
        quantity = state._option_contract_quantity(contract["mid"])

        first = state._submit_option_trade_request(
            "PLTR",
            contract["symbol"],
            "Only Long Call",
            quantity=quantity,
            entry_price=contract["mid"],
            selected_contract_override=contract,
        )
        second = state._submit_option_trade_request(
            "PLTR",
            contract["symbol"],
            "Only Long Call",
            quantity=quantity,
            entry_price=contract["mid"],
            selected_contract_override=contract,
        )

        self.assertTrue(first["ok"])
        self.assertFalse(second["ok"])
        self.assertEqual(second["status"], "duplicate_blocked")
        self.assertEqual(len(state.option_client.orders), 1)
        self.assertEqual(len(state.repository.logged_trades), 1)
        self.assertEqual(state.repository.logged_trades[0]["quantity"], 3)

    def test_cancel_stale_option_buy_orders_only_cancels_quantity_mismatches(self):
        state = make_state()
        state.option_risk_settings["contractQuantity"] = "2"
        state.option_client.orders.extend(
            [
                types.SimpleNamespace(
                    id="ord-stale",
                    client_order_id="option-CART-old",
                    symbol="CART260717C00051000",
                    status="new",
                    qty="5",
                    filled_qty="0",
                    limit_price=0.38,
                    filled_avg_price=None,
                    submitted_at=datetime.now(timezone.utc),
                    side="buy",
                ),
                types.SimpleNamespace(
                    id="ord-current",
                    client_order_id="option-CHWY-new",
                    symbol="CHWY260821C00027500",
                    status="new",
                    qty="2",
                    filled_qty="0",
                    limit_price=0.20,
                    filled_avg_price=None,
                    submitted_at=datetime.now(timezone.utc),
                    side="buy",
                ),
                types.SimpleNamespace(
                    id="ord-sell",
                    client_order_id="option-CART-exit",
                    symbol="CART260717C00051000",
                    status="new",
                    qty="5",
                    filled_qty="0",
                    limit_price=0.38,
                    filled_avg_price=None,
                    submitted_at=datetime.now(timezone.utc),
                    side="sell",
                ),
            ]
        )

        result = state._cancel_stale_option_buy_orders_result()

        self.assertEqual(result["status"], "ok")
        self.assertEqual([item["symbol"] for item in result["canceled"]], ["CART260717C00051000"])
        self.assertEqual(state.option_client.orders[0].status, "canceled")
        self.assertEqual(state.option_client.orders[1].status, "new")
        self.assertEqual(state.option_client.orders[2].status, "new")

    def test_option_target_1_contracts_accepts_percent_or_contract_count(self):
        state = make_state()

        state.option_risk_settings["firstProfitTargetCons"] = "80%"
        self.assertEqual(state._option_target_1_contracts(5), 4)

        state.option_risk_settings["firstProfitTargetCons"] = "4 cons"
        self.assertEqual(state._option_target_1_contracts(5), 4)

        state.option_risk_settings["firstProfitTargetCons"] = ""
        self.assertEqual(state._option_target_1_contracts(5), 4)

    def test_option_risk_settings_compose_target_1_percentage_value(self):
        state = make_state()

        state.update_option_risk_settings(
            first_profit_target_sell_mode="percentage",
            first_profit_target_sell_value="80",
            runner_lock_step_percent="50",
        )

        self.assertEqual(state.option_risk_settings["firstProfitTargetSellMode"], "percentage")
        self.assertEqual(state.option_risk_settings["firstProfitTargetSellValue"], "80")
        self.assertEqual(state.option_risk_settings["firstProfitTargetCons"], "80%")
        self.assertEqual(state.option_risk_settings["runnerLockStepPercent"], "50")

    def test_option_risk_settings_compose_target_1_contract_value(self):
        state = make_state()

        state.update_option_risk_settings(
            first_profit_target_sell_mode="contracts",
            first_profit_target_sell_value="4",
        )

        self.assertEqual(state.option_risk_settings["firstProfitTargetSellMode"], "contracts")
        self.assertEqual(state.option_risk_settings["firstProfitTargetSellValue"], "4")
        self.assertEqual(state.option_risk_settings["firstProfitTargetCons"], "4 cons")

    def test_option_entry_logic_ignores_stock_ai_score_rejection(self):
        state = make_state()
        state._option_signal_checks = types.MethodType(
            lambda self, row: {
                "option_rule_price_pass": True,
                "option_rule_one_hour_close_pass": True,
                "option_rule_four_hour_close_pass": True,
                "option_rule_four_hour_volume_pass": True,
                "option_rule_trigger_pass": True,
                "option_rule_all_passed": True,
                "option_rule_any_passed": True,
                "option_rule_passed": True,
                "option_rule_trigger_match": "EMA + VWAP",
                "option_one_hour_close_change_pct": 0.35,
                "option_four_hour_close_change_pct": 0.75,
                "option_four_hour_volume_change_pct": 1.25,
                "option_rule_rejection_reason": "",
            },
            state,
        )
        candidates = pd.DataFrame(
            [
                {
                    "symbol": "PLTR",
                    "allowed": False,
                    "rejection_reason": "AI trade score below threshold 50",
                    "setup_name": "EMA + VWAP",
                    "final_score": 41,
                }
            ]
        )

        result = state._apply_option_entry_logic(candidates)
        row = result.iloc[0]

        self.assertTrue(bool(row["allowed"]))
        self.assertEqual(row["stock_rejection_reason"], "AI trade score below threshold 50")
        self.assertEqual(row["rejection_reason"], "")

    def test_option_entry_logic_blocks_only_on_option_rule_failure(self):
        state = make_state()
        state._option_signal_checks = types.MethodType(
            lambda self, row: {
                "option_rule_price_pass": True,
                "option_rule_one_hour_close_pass": True,
                "option_rule_four_hour_close_pass": True,
                "option_rule_ema_trend_pass": False,
                "option_rule_ema9_retest_pass": True,
                "option_rule_trigger_pass": True,
                "option_rule_all_passed": False,
                "option_rule_any_passed": True,
                "option_rule_passed": False,
                "option_rule_trigger_match": "EMA + VWAP",
                "option_one_hour_close_change_pct": 0.35,
                "option_four_hour_close_change_pct": 0.75,
                "option_rule_rejection_reason": "EMA trend not stacked: EMA 9 > EMA 21 > EMA 50 required",
            },
            state,
        )
        candidates = pd.DataFrame(
            [
                {
                    "symbol": "PLTR",
                    "allowed": False,
                    "rejection_reason": "AI trade score below threshold 50",
                    "setup_name": "EMA + VWAP",
                    "final_score": 41,
                }
            ]
        )

        result = state._apply_option_entry_logic(candidates)
        row = result.iloc[0]

        self.assertFalse(bool(row["allowed"]))
        self.assertEqual(row["stock_rejection_reason"], "AI trade score below threshold 50")
        self.assertEqual(row["rejection_reason"], "EMA trend not stacked: EMA 9 > EMA 21 > EMA 50 required")

    def test_option_signal_percent_change_matches_tos_price_change_script(self):
        state = make_state()
        frame = pd.DataFrame(
            {
                "close": [100.0, 105.0, 110.0],
                "volume": [200.0, 250.0, 500.0],
            }
        )

        self.assertEqual(state._pct_change_vs_bars_back(frame, "close", 2), 10.0)
        self.assertEqual(state._pct_change_vs_bars_back(frame, "volume", 2), 150.0)

    def test_option_signal_accepts_low_above_candle_setups(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )
        state._pct_change_vs_bars_back = types.MethodType(lambda self, frame, column, length=2: 1.0, state)

        for setup_name in [
            "EMA + VWAP + Premarket Low Above Candle",
            "EMA + VWAP + Previous Day Low Above Candle",
        ]:
            checks = state._option_signal_checks(
                {
                    "symbol": "PLTR",
                    "last_price": 125.0,
                    "setup_name": setup_name,
                    "ema_stack": True,
                    "above_vwap": True,
                    "four_hour_cloud_bullish": True,
                    "cloud_alignment_pass": True,
                    "volume_trend": True,
                    "ema9_retest_5m": True,
                }
            )
            self.assertTrue(checks["option_rule_trigger_pass"])
            self.assertTrue(checks["option_rule_passed"])
            self.assertEqual(checks["option_rule_trigger_match"], setup_name)

    def test_option_signal_keeps_one_and_four_hour_changes_informational_only(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )

        def pct_change(_self, _frame, column, _length=2):
            return -20.0 if column == "volume" else 1.0

        state._pct_change_vs_bars_back = types.MethodType(pct_change, state)

        checks = state._option_signal_checks(
            {
                "symbol": "ANET",
                "last_price": 175.75,
                "setup_name": "EMA + VWAP + ORB",
                "ema_stack": True,
                "above_vwap": True,
                "four_hour_cloud_bullish": True,
                "cloud_alignment_pass": True,
                "volume_trend": False,
                "ema9_retest_5m": True,
            }
        )

        self.assertTrue(checks["option_rule_passed"])
        self.assertTrue(checks["option_rule_volume_pass"])
        self.assertFalse(checks["option_rule_volume_acceleration_observed"])
        self.assertIsNone(checks["option_rule_one_hour_close_pass"])
        self.assertIsNone(checks["option_rule_four_hour_close_pass"])
        self.assertIsNone(checks["option_rule_four_hour_price_change_pass"])
        self.assertEqual(checks["option_four_hour_close_change_pct"], 1.0)
        self.assertEqual(checks["option_four_hour_price_change_pct"], 1.0)
        self.assertTrue(checks["option_rule_four_hour_volume_pass"])
        self.assertEqual(checks["option_four_hour_volume_change_pct"], -20.0)
        self.assertNotIn("4H volume", checks["option_rule_rejection_reason"])
        self.assertNotIn("volume is not increasing", checks["option_rule_rejection_reason"])

    def test_option_signal_does_not_block_when_live_four_hour_price_change_is_below_threshold(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )

        def aggregate(_self, _bars, bucket_minutes):
            closes = [100.0, 100.2, 100.6] if bucket_minutes == 60 else [100.0, 100.1, 100.4]
            return pd.DataFrame({"close": closes, "volume": [100.0, 150.0, 200.0]})

        state._aggregate_option_bars = types.MethodType(aggregate, state)

        checks = state._option_signal_checks(
            {
                "symbol": "ANET",
                "last_price": 175.75,
                "setup_name": "EMA + VWAP + ORB",
                "ema_stack": True,
                "above_vwap": True,
                "four_hour_cloud_bullish": True,
                "cloud_alignment_pass": True,
                "volume_trend": True,
                "ema9_retest_5m": True,
            }
        )

        self.assertTrue(checks["option_rule_passed"])
        self.assertIsNone(checks["option_rule_four_hour_close_pass"])
        self.assertIsNone(checks["option_rule_four_hour_price_change_pass"])
        self.assertEqual(checks["option_four_hour_close_change_pct"], 0.4)
        self.assertEqual(checks["option_four_hour_price_change_pct"], 0.4)
        self.assertNotIn("live 4H price_change", checks["option_rule_rejection_reason"])

    def test_option_signal_ignores_ema9_retest_gate(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )
        state._pct_change_vs_bars_back = types.MethodType(lambda self, frame, column, length=2: 1.0, state)

        checks = state._option_signal_checks(
            {
                "symbol": "ANET",
                "last_price": 175.75,
                "setup_name": "EMA + VWAP + ORB",
                "ema_stack": True,
                "above_vwap": True,
                "four_hour_cloud_bullish": True,
                "cloud_alignment_pass": True,
                "volume_trend": True,
                "ema9_retest_5m": False,
            }
        )

        self.assertTrue(checks["option_rule_passed"])
        self.assertTrue(checks["option_rule_ema9_retest_pass"])
        self.assertFalse(checks["option_rule_ema9_retest_observed"])
        self.assertNotIn("retest", checks["option_rule_rejection_reason"].lower())

    def test_option_signal_waits_for_five_minute_reclaim_when_four_hour_cloud_is_bullish(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(),
            state,
        )
        checks = state._option_signal_checks(
            {
                "symbol": "AAPL",
                "last_price": 225.0,
                "setup_name": "EMA + VWAP + ORB",
                "ema_stack": False,
                "above_vwap": True,
                "four_hour_cloud_bullish": True,
                "cloud_alignment_pass": False,
                "cloud_alignment_action": "WAIT 5M RECLAIM",
            }
        )

        self.assertFalse(checks["option_rule_passed"])
        self.assertTrue(checks["option_rule_four_hour_cloud_pass"])
        self.assertFalse(checks["option_rule_cloud_alignment_pass"])
        self.assertEqual(checks["option_cloud_alignment_action"], "WAIT 5M RECLAIM")
        self.assertIn("WAIT 5M RECLAIM", checks["option_rule_rejection_reason"])

    def test_option_strategy_scan_passes_option_only_gate_overrides(self):
        state = make_state()
        calls = []

        class FakeScanner:
            def run(
                self,
                symbols,
                max_results=None,
                ignore_one_hour_price_change=False,
                ignore_four_hour_price_change=False,
                ignore_four_hour_volume=False,
                ignore_ema9_retest=False,
            ):
                calls.append(
                    {
                        "symbols": symbols,
                        "max_results": max_results,
                        "ignore_one_hour_price_change": ignore_one_hour_price_change,
                        "ignore_four_hour_price_change": ignore_four_hour_price_change,
                        "ignore_four_hour_volume": ignore_four_hour_volume,
                        "ignore_ema9_retest": ignore_ema9_retest,
                    }
                )
                return pd.DataFrame(
                    [
                        {
                            "symbol": "PLTR",
                            "setup_name": "EMA + VWAP + ORB",
                            "ema9_retest_5m": False,
                            "ema_stack": True,
                            "above_vwap": True,
                            "volume_trend": True,
                        }
                    ]
                )

        class FakeAiModel:
            def score_frame(self, frame):
                return frame

        class FakeStrategy:
            def build_trade_candidates(self, frame, existing_symbols=None, symbol_memory=None, catalysts=None):
                return [{"symbol": "PLTR", "allowed": True}]

            def candidates_to_frame(self, candidates):
                return pd.DataFrame(candidates)

        state.scanner = FakeScanner()
        state.trader = types.SimpleNamespace(
            ai_model=FakeAiModel(),
            strategy=FakeStrategy(),
            _existing_symbols=lambda: set(),
        )
        state.repository.get_symbol_memory = lambda limit=500: pd.DataFrame()

        frame = state.option_strategy_frame_for_symbols(["PLTR"], max_results=10)

        self.assertFalse(frame.empty)
        self.assertTrue(calls[0]["ignore_one_hour_price_change"])
        self.assertTrue(calls[0]["ignore_four_hour_price_change"])
        self.assertTrue(calls[0]["ignore_four_hour_volume"])
        self.assertTrue(calls[0]["ignore_ema9_retest"])

    def test_option_bot_state_is_persisted_when_started(self):
        state = make_state()
        persisted = []
        state.repository.set_app_setting = lambda key, value: persisted.append((key, value))
        state.scan_options = lambda create_plans=False: {"ok": True}
        state._start_option_scheduler = lambda: None

        DashboardState.set_option_bot_state(state, "Running")

        self.assertIn(("option_bot_state", "Running"), persisted)

    def test_scanner_storage_settings_are_persisted(self):
        state = make_state()
        persisted = []
        state.repository.set_app_setting = lambda key, value: persisted.append((key, value))
        state.repository.log_bot_event = lambda *args, **kwargs: None
        original_retention = settings.scanner.history_retention_days

        try:
            payload = DashboardState.update_scanner_storage_settings(state, history_retention_days=45)

            self.assertEqual(settings.scanner.history_retention_days, 45)
            self.assertIn(("scanner_history_retention_days", "45"), persisted)
            self.assertEqual(payload["ok"], True)
        finally:
            settings.scanner.history_retention_days = original_retention

    def test_option_scan_manages_open_positions_before_new_plans(self):
        state = make_state()
        calls = []
        state.client = types.SimpleNamespace(ensure_streaming=lambda symbols: calls.append("stream"))
        state._option_market_hours_open = types.MethodType(lambda self: True, state)
        state._active_option_watchlist = types.MethodType(lambda self: ["PLTR"], state)
        state._option_watchlist_source_label = types.MethodType(lambda self: "Option Watchlist", state)
        state.manage_option_paper_trades = lambda: calls.append("manage") or {"managed": 1, "closed": []}
        state._option_signal_frame_from_fresh_oi = lambda: calls.append("scan") or pd.DataFrame(
            [{"symbol": "PLTR", "allowed": True}]
        )
        state._apply_option_entry_logic = lambda frame: frame
        state._plan_option_paper_trades = lambda candidates: calls.append("plan") or {"created": 0}
        state._refresh_option_supervisor_report = lambda *args, **kwargs: {}
        state.dashboard_cache = {"stale": True}
        state.dashboard_cache_timestamp = datetime.now(timezone.utc)
        state.dashboard_payload = lambda: calls.append("dashboard") or {"sentinel": "dashboard"}

        result = DashboardState.scan_options(state, create_plans=True, return_payload=False)

        self.assertLess(calls.index("manage"), calls.index("scan"))
        self.assertLess(calls.index("scan"), calls.index("plan"))
        self.assertLess(calls.index("manage"), calls.index("plan"))
        self.assertNotIn("dashboard", calls)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["resultCount"], len(state.option_candidate_results.index))
        self.assertEqual(result["scanLabel"], "Mag7 + Watchlist Options")
        self.assertEqual(state.dashboard_cache, {"stale": True})
        self.assertIsNotNone(state.dashboard_cache_timestamp)

        default_result = DashboardState.scan_options(state, create_plans=True)

        self.assertEqual(default_result, {"sentinel": "dashboard"})
        self.assertEqual(calls.count("dashboard"), 1)

    def test_option_scheduler_skips_dashboard_payload_work(self):
        state = make_state()
        calls = []
        state.option_bot_state = "Running"
        state.option_scheduler_interval_seconds = 5
        state.option_scan_wakeup = threading.Event()
        state.option_scan_wakeup.set()

        def scan_options(**kwargs):
            calls.append(kwargs)
            state.option_bot_state = "Stopped"
            return {"status": "completed"}

        state.scan_options = scan_options

        DashboardState._option_scheduler_loop(state)

        self.assertEqual(calls, [{"create_plans": True, "return_payload": False}])

    def test_option_signal_does_not_block_when_live_four_hour_volume_gate_fails(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )
        state._pct_change_vs_bars_back = types.MethodType(lambda self, frame, column, length=2: 1.0, state)

        checks = state._option_signal_checks(
            {
                "symbol": "ANET",
                "last_price": 175.75,
                "setup_name": "EMA + VWAP + ORB",
                "ema_stack": True,
                "above_vwap": True,
                "four_hour_cloud_bullish": True,
                "cloud_alignment_pass": True,
                "volume_trend": True,
                "four_hour_volume_pass": False,
                "four_hour_volume_change_pct": 0.4,
                "ema9_retest_5m": True,
            }
        )

        self.assertTrue(checks["option_rule_passed"])
        self.assertTrue(checks["option_rule_live_four_hour_volume_pass"])
        self.assertNotIn("4H volume", checks["option_rule_rejection_reason"])

    def test_option_signal_blocks_when_common_option_gates_fail(self):
        state = make_state()
        state._option_signal_source_bars = types.MethodType(
            lambda self, symbol: pd.DataFrame(
                [
                    {
                        "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                        "open": 100.0,
                        "high": 101.0,
                        "low": 99.0,
                        "close": 100.5,
                        "volume": 1000,
                    }
                ]
            ),
            state,
        )
        state._pct_change_vs_bars_back = types.MethodType(lambda self, frame, column, length=2: 1.0, state)

        checks = state._option_signal_checks(
            {
                "symbol": "PLTR",
                "last_price": 125.0,
                "setup_name": "EMA + VWAP",
                "ema_stack": False,
                "above_vwap": True,
                "volume_trend": True,
                "ema9_retest_5m": False,
            }
        )

        self.assertFalse(checks["option_rule_passed"])
        self.assertFalse(checks["option_rule_ema_trend_pass"])
        self.assertTrue(checks["option_rule_ema9_retest_pass"])
        self.assertFalse(checks["option_rule_ema9_retest_observed"])
        self.assertIn("EMA trend not stacked", checks["option_rule_rejection_reason"])
        self.assertNotIn("retest", checks["option_rule_rejection_reason"].lower())

    def test_option_signal_source_keeps_extended_hours_like_tos_ext_scan(self):
        state = make_state()
        state.market_data_client.get_chart_bars = lambda symbol, timeframe="5Min", days_back=2: pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-07-02 03:59:00", tz="America/New_York"),
                    "open": 124.0,
                    "high": 124.0,
                    "low": 124.0,
                    "close": 124.0,
                    "volume": 100,
                },
                {
                    "timestamp": pd.Timestamp("2026-07-02 04:00:00", tz="America/New_York"),
                    "open": 125.0,
                    "high": 125.5,
                    "low": 124.9,
                    "close": 125.25,
                    "volume": 1000,
                },
                {
                    "timestamp": pd.Timestamp("2026-07-02 09:30:00", tz="America/New_York"),
                    "open": 126.0,
                    "high": 126.5,
                    "low": 125.9,
                    "close": 126.25,
                    "volume": 2000,
                },
                {
                    "timestamp": pd.Timestamp("2026-07-02 16:30:00", tz="America/New_York"),
                    "open": 127.0,
                    "high": 127.5,
                    "low": 126.9,
                    "close": 127.25,
                    "volume": 1500,
                },
                {
                    "timestamp": pd.Timestamp("2026-07-02 20:00:00", tz="America/New_York"),
                    "open": 128.0,
                    "high": 128.5,
                    "low": 127.9,
                    "close": 128.25,
                    "volume": 1200,
                },
                {
                    "timestamp": pd.Timestamp("2026-07-02 20:01:00", tz="America/New_York"),
                    "open": 129.0,
                    "high": 129.0,
                    "low": 129.0,
                    "close": 129.0,
                    "volume": 100,
                },
            ]
        )

        bars = state._option_signal_source_bars("PLTR")

        self.assertEqual([item.strftime("%H:%M") for item in bars["timestamp"]], ["04:00", "09:30", "16:30", "20:00"])

    def test_schwab_daily_history_uses_year_period_type(self):
        daily_params = TIMEFRAME_PARAMS["1Day"]

        self.assertEqual(daily_params["periodType"], "year")
        self.assertEqual(daily_params["frequencyType"], "daily")

    def test_oi_scan_prefilters_with_quotes_and_fails_open_for_missing_quote(self):
        state = DashboardState.__new__(DashboardState)
        scanned_symbols = []

        class FakeScanner:
            settings = types.SimpleNamespace(tos_rvol_num_dev=1.0)
            client = types.SimpleNamespace(
                get_quotes=lambda symbols: {
                    "AAPL": {"last_price": 200.0, "change_pct": 0.5},
                    "AMD": {"last_price": 2.0, "change_pct": 5.0},
                }
            )

            @staticmethod
            def run(symbols, **kwargs):
                scanned_symbols.extend(symbols)
                return pd.DataFrame()

        state.scanner = FakeScanner()
        state.market_data_client = types.SimpleNamespace(get_option_chain=lambda *args, **kwargs: {})

        payload = DashboardState.scan_option_chain_liquidity(
            state,
            symbols=["AAPL", "AMD", "MISSING"],
            min_underlying_price=3.0,
        )

        self.assertEqual(scanned_symbols, ["AAPL", "MISSING"])
        self.assertEqual(payload["quotePrefilterCount"], 2)
        self.assertEqual(payload["strategyMatchCount"], 0)

    def test_oi_scan_quote_prefilter_uses_last_price_not_daily_change(self):
        state = DashboardState.__new__(DashboardState)
        scanned_symbols = []
        scan_kwargs = {}

        class FakeScanner:
            settings = types.SimpleNamespace(tos_rvol_num_dev=1.0)
            client = types.SimpleNamespace(
                get_quotes=lambda symbols: {
                    "NVDA": {"last_price": 200.0, "change_pct": 1.25},
                    "AAPL": {"last_price": 200.0, "change_pct": 0.25},
                }
            )

            @staticmethod
            def run(symbols, **kwargs):
                scanned_symbols.extend(symbols)
                scan_kwargs.update(kwargs)
                return pd.DataFrame()

        state.scanner = FakeScanner()
        state.market_data_client = types.SimpleNamespace(get_option_chain=lambda *args, **kwargs: {})

        payload = DashboardState.scan_option_chain_liquidity(
            state,
            symbols=["NVDA", "AAPL"],
            min_underlying_price=3.0,
        )

        self.assertEqual(scanned_symbols, ["NVDA", "AAPL"])
        self.assertEqual(payload["quotePrefilterCount"], 2)
        self.assertFalse(scan_kwargs["require_rvol_confirmation"])

    def test_oi_scan_eligible_chain_uses_live_quote_change(self):
        state = DashboardState.__new__(DashboardState)

        class FakeScanner:
            settings = types.SimpleNamespace(tos_rvol_num_dev=1.0)
            client = types.SimpleNamespace(
                get_quotes=lambda symbols: {
                    "PLTR": {"last_price": 150.0, "change_pct": 2.75},
                }
            )

            @staticmethod
            def run(symbols, **kwargs):
                return pd.DataFrame([{
                    "symbol": "PLTR",
                    "setup_name": next(iter(api_server.OPTION_ALLOWED_SETUPS)),
                    "session_change_pct": 1.0,
                }])

        state.scanner = FakeScanner()
        state.market_data_client = types.SimpleNamespace(
            get_option_chain=lambda *args, **kwargs: {"underlyingPrice": 150.0},
        )
        state.option_client = types.SimpleNamespace(normalize_option_symbol=lambda value: value)
        state._approved_oi_strategy_frame = lambda frame: frame
        state._option_underlying_price_from_chain = lambda payload: 150.0
        state._oi_symbol_expiry_cutoff = lambda payload, anchor: None
        state._option_chain_contracts = lambda payload, contract_type: [{
            "strike_price": 155.0,
            "delta": 0.30,
            "expiry_date": "2026-07-24",
            "daysToExpiration": 9,
            "bid": 2.0,
            "ask": 2.2,
            "total_volume": 100.0,
            "open_interest": 200.0,
            "symbol": "PLTR260724C00155000",
        }]
        state._expiry_timestamp = lambda expiry: pd.Timestamp(expiry)
        state._expected_move_for_expiry = lambda payload, expiry: 5.0
        state._contract_mid_price = lambda contract: 2.1
        state._option_liquidity_strike_plan = lambda payload, expiry: {
            "atm_strike": 150.0,
            "atm_volume": 50.0,
            "atm_open_interest": 50.0,
        }

        payload = DashboardState.scan_option_chain_liquidity(
            state,
            symbols=["PLTR"],
            min_underlying_price=3.0,
            min_expected_move=2.0,
            max_days_to_expiration=14,
        )

        self.assertEqual(len(payload["rows"]), 1)
        self.assertEqual(payload["rows"][0]["change_pct"], 2.75)
        self.assertTrue(payload["rows"][0]["change_pass"])


class OptionEngineIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.previous_provider = api_server.settings.market_data_provider
        api_server.settings.market_data_provider = "schwab"

    def tearDown(self):
        api_server.settings.market_data_provider = self.previous_provider

    def test_contract_selector_prefers_nearest_expiry_then_highest_delta_below_cap(self):
        state = make_state()

        contract, error = state._select_option_contract("PLTR")

        self.assertEqual(error, "")
        self.assertEqual(contract["symbol"], "PLTR260702C00129000")
        self.assertAlmostEqual(contract["delta"], 0.1701)
        self.assertGreaterEqual(contract["expected_move"], 2.0)
        self.assertEqual(state.market_data_client.requests[0][1], "ALL")

    def test_contract_selector_prefers_next_high_liquidity_otm_strike_after_atm_break(self):
        state = make_state()
        state.market_data_client = FakeTslaLiquidityChainMarket(underlying_price=420.50)

        contract, error = state._select_option_contract("TSLA")

        self.assertEqual(error, "")
        self.assertEqual(contract["symbol"], "TSLA260708C00430000")
        self.assertEqual(contract["strike_price"], 430.0)
        self.assertEqual(contract["delta"], 0.12)
        self.assertEqual(contract["liquidity_breakout_level"], 420.0)
        self.assertTrue(contract["liquidity_breakout_passed"])
        self.assertTrue(contract["liquidity_breakout_required"])
        self.assertTrue(contract["liquidity_atm_dominates_otm"])
        self.assertEqual(contract["liquidity_atm_volume"], 45770)
        self.assertEqual(contract["liquidity_atm_open_interest"], 3684)
        self.assertEqual(contract["underlying_target_strike"], 430.0)
        self.assertEqual(contract["underlying_target_volume"], 17210)
        self.assertEqual(contract["underlying_target_open_interest"], 2634)
        self.assertEqual(contract["underlying_target_liquidity_metric"], "volume")

    def test_oi_wall_plan_finds_strongest_call_and_put_open_interest(self):
        state = make_state()
        state.market_data_client = FakeTslaLiquidityChainMarket(
            underlying_price=420.50,
            target_open_interest=9000,
        )
        chain = state.market_data_client.get_option_chain("TSLA", contract_type="ALL")

        wall = state._option_oi_wall_plan(chain, "2026-07-08", atm_strike=420.0)

        self.assertEqual(wall["call_wall_strike"], 430.0)
        self.assertEqual(wall["call_wall_open_interest"], 9000)
        self.assertEqual(wall["put_wall_strike"], 420.0)
        self.assertEqual(wall["put_wall_open_interest"], 1290)
        self.assertEqual(wall["oi_wall_signal"], "APPROACHING CALL WALL")
        self.assertAlmostEqual(wall["call_wall_distance_pct"], 2.26, places=2)

    def test_liquidity_plan_exposes_broken_atm_call_wall(self):
        state = make_state()
        state.market_data_client = FakeTslaLiquidityChainMarket(underlying_price=420.50)
        chain = state.market_data_client.get_option_chain("TSLA", contract_type="ALL")

        plan = state._option_liquidity_strike_plan(chain, "2026-07-08")

        self.assertEqual(plan["call_wall_strike"], 420.0)
        self.assertEqual(plan["call_wall_strength"], "MODERATE")
        self.assertEqual(plan["oi_wall_signal"], "CALL WALL BREAK")

    def test_contract_selector_waits_for_atm_liquidity_level_when_atm_dominates(self):
        state = make_state()
        state.market_data_client = FakeTslaLiquidityChainMarket(underlying_price=419.77)

        contract, error = state._select_option_contract("TSLA")

        self.assertIsNone(contract)
        self.assertIn("waiting for ATM liquidity support break", error)
        self.assertIn("$419.77 below ATM $420.00", error)

    def test_contract_selector_does_not_require_atm_break_when_otm_liquidity_dominates(self):
        state = make_state()
        state.market_data_client = FakeTslaLiquidityChainMarket(
            underlying_price=419.77,
            atm_volume=5000,
            atm_open_interest=2000,
            target_volume=17210,
            target_open_interest=2634,
        )

        contract, error = state._select_option_contract("TSLA")

        self.assertEqual(error, "")
        self.assertEqual(contract["symbol"], "TSLA260708C00430000")
        self.assertEqual(contract["strike_price"], 430.0)
        self.assertEqual(contract["delta"], 0.12)
        self.assertEqual(contract["liquidity_breakout_level"], 420.0)
        self.assertFalse(contract["liquidity_breakout_passed"])
        self.assertFalse(contract["liquidity_breakout_required"])
        self.assertFalse(contract["liquidity_atm_dominates_otm"])
        self.assertEqual(contract["underlying_target_strike"], 430.0)

    def test_option_initial_plan_uses_80_percent_target_for_liquidity_strike(self):
        state = make_state()
        selected_contract = {
            "symbol": "TSLA260708C00430000",
            "mid": 1.25,
            "bid": 1.20,
            "ask": 1.30,
            "delta": 0.20,
            "expected_move": 6.80,
            "expiry_date": "2026-07-08",
            "strike_price": 430,
            "underlying_target_strike": 430,
            "total_volume": 17210,
            "open_interest": 2634,
        }

        plan = state._option_initial_trade_plan(
            selected_contract["mid"],
            5,
            selected_contract,
            {"setup_name": "EMA + VWAP + ORB"},
        )

        self.assertEqual(plan["contracts_to_sell_at_target_1"], 4)
        self.assertEqual(plan["underlying_target_1_strike"], 430)
        self.assertEqual(plan["underlying_target_1_sell_percent"], 80.0)
        self.assertEqual(plan["selected_option_volume"], 17210)
        self.assertEqual(plan["selected_option_open_interest"], 2634)

    def test_expected_move_guardrail_defaults_to_two_and_accepts_custom_values(self):
        state = make_state()
        state.option_bot_config["expectedMove"] = ">=20"

        contract, error = state._select_option_contract("PLTR")

        self.assertIsNone(contract)
        self.assertIn("expected move >= $20.00", error)

        state.option_bot_config["expectedMove"] = ">=0.6"
        contract, error = state._select_option_contract("PLTR")

        self.assertEqual(error, "")
        self.assertEqual(contract["symbol"], "PLTR260702C00129000")

    def test_option_candidates_require_fresh_a_plus_hot_oi_confirmation(self):
        state = make_state()
        state._fresh_option_a_plus_hot_confirmation = lambda: {
            "rows": [
                {
                    "symbol": "PLTR",
                    "priority_label": "A+ HOT",
                    "strength_score": 91,
                    "last_seen_at": "2026-07-11T10:00:00-04:00",
                    "option_contract": "PLTR260717C00130000",
                    "contract_snapshot": {
                        "symbol": "PLTR260717C00130000",
                        "bid": 1.10,
                        "ask": 1.20,
                        "mid": 1.15,
                        "expected_move": 4.25,
                        "days_to_expiration": 6,
                    },
                }
            ]
        }
        candidates = pd.DataFrame(
            [
                {"symbol": "PLTR", "allowed": True, "setup_name": "EMA + VWAP"},
                {"symbol": "TSLA", "allowed": True, "setup_name": "EMA + VWAP + ORB"},
            ]
        )

        filtered = state._option_a_plus_hot_candidates(candidates)

        self.assertEqual(filtered["symbol"].tolist(), ["PLTR"])
        self.assertEqual(filtered.iloc[0]["oi_priority_label"], "A+ HOT")
        self.assertEqual(int(filtered.iloc[0]["oi_strength_score"]), 91)
        self.assertEqual(filtered.iloc[0]["oi_scanner_contract"], "PLTR260717C00130000")
        self.assertEqual(filtered.iloc[0]["oi_contract_snapshot"]["mid"], 1.15)

    def test_option_signal_frame_uses_fresh_oi_snapshot_without_full_universe_rescan(self):
        state = make_state()
        state._fresh_option_a_plus_hot_confirmation = lambda: {
            "rows": [
                {
                    "signal_snapshot": {
                        "symbol": "NOW",
                        "last_price": 111.72,
                        "entry": 111.72,
                        "setup_name": "EMA + VWAP + ORB",
                        "ema_stack": True,
                        "above_vwap": True,
                        "allowed": True,
                    }
                }
            ]
        }

        frame = DashboardState._option_signal_frame_from_fresh_oi(state)

        self.assertEqual(frame["symbol"].tolist(), ["NOW"])
        self.assertEqual(frame.iloc[0]["setup_name"], "EMA + VWAP + ORB")
        self.assertTrue(bool(frame.iloc[0]["ema_stack"]))
        self.assertTrue(bool(frame.iloc[0]["above_vwap"]))

    def test_option_contract_uses_fresh_a_plus_snapshot_without_chain_download(self):
        state = make_state()
        before_requests = len(state.market_data_client.requests)
        row = {
            "oi_priority_label": "A+ HOT",
            "oi_contract_snapshot": {
                "symbol": "PLTR260717C00130000",
                "source_symbol": "PLTR  260717C00130000",
                "underlying": "PLTR",
                "expiry_date": "2026-07-17",
                "strike_price": 130.0,
                "delta": 0.20,
                "bid": 1.10,
                "ask": 1.20,
                "mid": 1.15,
                "expected_move": 4.25,
                "days_to_expiration": 6,
                "total_volume": 5500,
                "open_interest": 3200,
                "liquidity_breakout_required": False,
                "liquidity_breakout_passed": True,
                "underlying_target_strike": 130.0,
            },
        }

        contract, error = state._option_contract_from_a_plus_snapshot(row)

        self.assertEqual(error, "")
        self.assertEqual(contract["symbol"], "PLTR260717C00130000")
        self.assertEqual(contract["selection_source"], "fresh_a_plus_hot_oi_snapshot")
        self.assertEqual(len(state.market_data_client.requests), before_requests)

    def test_option_contract_rejects_a_plus_snapshot_above_saved_delta_cap(self):
        state = make_state()
        row = {
            "oi_priority_label": "A+ HOT",
            "oi_contract_snapshot": {
                "symbol": "PLTR260717C00130000",
                "underlying": "PLTR",
                "expiry_date": "2026-07-17",
                "strike_price": 130.0,
                "delta": 0.31,
                "bid": 1.10,
                "ask": 1.20,
                "mid": 1.15,
                "expected_move": 4.25,
                "days_to_expiration": 6,
                "liquidity_breakout_required": False,
                "liquidity_breakout_passed": True,
            },
        }

        contract, error = state._option_contract_from_a_plus_snapshot(row)

        self.assertIsNone(contract)
        self.assertIn("exceeds the saved maximum 0.20", error)

    def test_chart_payload_uses_configured_market_data_client(self):
        state = make_state()

        payload = state.chart_payload("PLTR", "1Min")

        self.assertEqual(payload["source"], "Schwab/TOS API")
        self.assertEqual(len(payload["bars"]), 1)
        self.assertEqual(payload["bars"][0]["close"], 126.0)
        self.assertEqual(state.market_data_client.chart_requests, [("PLTR", "5Min", 20)])
        self.assertEqual(payload["mtfSignalMode"], "live_forming_5m_projection")
        self.assertEqual(payload["mtfSignals"], [])

    def test_tos_mtf_ema_signal_payload_builds_2h_and_4h_crosses(self):
        timestamps = pd.date_range("2026-06-01T04:00:00-04:00", periods=2880, freq="5min")
        closes = [100 + (12 * math.sin(index / 144)) for index in range(len(timestamps))]
        frame = pd.DataFrame(
            {
                "timestamp": timestamps,
                "open": closes,
                "high": [value + 0.5 for value in closes],
                "low": [value - 0.5 for value in closes],
                "close": closes,
                "volume": [1000] * len(timestamps),
            }
        )

        payload = api_server._tos_mtf_ema_signal_payload(frame)

        self.assertEqual(payload["mode"], "live_forming_5m_projection")
        self.assertEqual(payload["sourceTimeframe"], "5Min")
        self.assertEqual(
            {(row["family"], row["timeframe"]) for row in payload["states"]},
            {
                ("4x8", "15"), ("4x8", "30"), ("4x8", "1H"), ("4x8", "2H"), ("4x8", "4H"),
                ("9x20", "30"), ("9x20", "1H"), ("9x20", "2H"), ("9x20", "4H"),
            },
        )
        self.assertTrue(payload["signals"])
        self.assertTrue(all(row["liveForming"] for row in payload["signals"]))
        self.assertTrue(all(row["direction"] in {"CALL", "PUT"} for row in payload["signals"]))
        self.assertTrue(all(row["label"].startswith(("C", "P")) for row in payload["signals"]))
        self.assertEqual({row["color"] for row in payload["signals"]}, {"yellow", "cyan"})
        self.assertTrue(set(payload["bullishTimeframes"]).issubset({"2H", "4H"}))

    def test_oi_strategy_gate_requires_all_ten_scanner_conditions(self):
        frame = pd.DataFrame([
            {
                "symbol": "CRWD",
                "last_price": 188.0,
                "setup_name": "MTF EMA C/CALL 2H/4H",
                "above_vwap": True,
                "ema_stack": True,
                "cloud_alignment_pass": True,
                "tos_rvol_any_pass": True,
                "tos_rvol_5m_early_alert": False,
                "fast_momentum_score": 2,
                "price_action_pass": True,
                "mtf_bullish_signal_pass": True,
                "one_hour_price_change_pass": True,
                "four_hour_volume_pass": True,
            },
            {
                "symbol": "BLOCKED",
                "last_price": 188.0,
                "setup_name": "MTF EMA C/CALL 2H/4H",
                "above_vwap": False,
                "ema_stack": False,
                "cloud_alignment_pass": False,
                "tos_rvol_any_pass": False,
                "tos_rvol_5m_early_alert": False,
                "fast_momentum_score": 0,
                "price_action_pass": False,
                "mtf_bullish_signal_pass": False,
                "one_hour_price_change_pass": True,
                "four_hour_volume_pass": True,
            },
            {
                "symbol": "OLD_STANDARD_ONLY",
                "last_price": 188.0,
                "setup_name": "EMA + VWAP",
                "above_vwap": True,
                "ema_stack": True,
                "cloud_alignment_pass": True,
                "tos_rvol_any_pass": True,
                "tos_rvol_5m_early_alert": False,
                "fast_momentum_score": 3,
                "price_action_pass": True,
                "mtf_bullish_signal_pass": False,
                "one_hour_price_change_pass": True,
                "four_hour_volume_pass": True,
            },
            {
                "symbol": "ONE_HOUR_FAIL",
                "last_price": 188.0,
                "setup_name": "MTF EMA C/CALL 2H/4H",
                "mtf_bullish_signal_pass": True,
                "one_hour_price_change_pass": False,
                "four_hour_volume_pass": True,
            },
            {
                "symbol": "FOUR_HOUR_VOLUME_FAIL",
                "last_price": 188.0,
                "setup_name": "MTF EMA C/CALL 2H/4H",
                "mtf_bullish_signal_pass": True,
                "one_hour_price_change_pass": True,
                "four_hour_volume_pass": False,
            },
            {
                "symbol": "PRICE_FAIL",
                "last_price": 2.99,
                "setup_name": "MTF EMA C/CALL 2H/4H",
                "mtf_bullish_signal_pass": True,
                "one_hour_price_change_pass": True,
                "four_hour_volume_pass": True,
            },
        ])

        approved = DashboardState._approved_oi_strategy_frame(frame)

        self.assertEqual(approved["symbol"].tolist(), ["CRWD"])

    def test_option_account_payload_uses_option_trade_account_status(self):
        state = make_state()

        payload = state._option_account_payload()

        self.assertEqual(payload["id"], "paper3")
        self.assertEqual(payload["label"], "OPTION TRADE")
        self.assertTrue(payload["stockTradingDisabled"])
        self.assertEqual(payload["status"]["accountEquity"], 9992.31)
        self.assertEqual(payload["status"]["buyingPower"], 39729.24)
        self.assertEqual(payload["status"]["optionsBuyingPower"], 39729.24)
        self.assertEqual(payload["status"]["accountNumber"], "PA3TEST")
        self.assertEqual(payload["status"]["dailyChange"], -15.64)
        self.assertEqual(payload["status"]["tradesToday"], 0)

    def test_plan_option_trade_creates_auto_open_position_with_premium_risk(self):
        state = make_state()
        candidates = pd.DataFrame(
            [
                {
                    "symbol": "PLTR",
                    "allowed": True,
                    "final_score": 91,
                    "entry": 125.73,
                    "setup_name": "EMA + VWAP + ORB",
                    "strategy_family": "Momentum + Price Action Trend",
                    "option_rule_trigger_match": "EMA + VWAP + ORB",
                    "option_one_hour_close_change_pct": 0.42,
                    "option_four_hour_close_change_pct": 0.82,
                    "option_four_hour_volume_change_pct": 2.10,
                    "option_rule_passed": True,
                }
            ]
        )

        result = state._plan_option_paper_trades(candidates)

        self.assertEqual(result["created"], 1)
        logged = state.repository.logged_trades[0]
        self.assertEqual(logged["account_profile_id"], "paper3")
        self.assertEqual(logged["account_label"], "OPTION TRADE")
        self.assertEqual(logged["status"], "position_open")
        self.assertEqual(logged["quantity"], 3)
        self.assertEqual(logged["stop_price"], 0.4)
        self.assertEqual(logged["target_price"], 1.0)
        analysis = json.loads(logged["analysis_json"])
        self.assertEqual(analysis["contracts_to_sell_at_target_1"], 2)
        self.assertTrue(analysis["fast_execution_path"])
        self.assertIsNotNone(analysis["broker_submit_ms"])
        self.assertEqual(analysis["selected_option_delta"], 0.1701)
        self.assertEqual(state.option_client.account_requests, 1)

    def test_option_entries_route_mag7_to_paper3_and_watchlist_to_paper5(self):
        state = make_state()
        mag7_client = FakeOptionClient()
        watchlist_client = FakeOptionClient()
        mag7_credentials = types.SimpleNamespace(profile_id="paper3", label="Mag7 OPTION", paper=True)
        watchlist_credentials = types.SimpleNamespace(profile_id="paper5", label="Watchlist option", paper=True)
        state.option_contexts = {
            "paper3": {"profile_id": "paper3", "credentials": mag7_credentials, "client": mag7_client, "trader": FakeOptionTrader()},
            "paper5": {"profile_id": "paper5", "credentials": watchlist_credentials, "client": watchlist_client, "trader": FakeOptionTrader()},
        }
        state.option_context_lock = threading.RLock()
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]
        contract = {
            "symbol": "AAPL260710C00210000",
            "mid": 1.0,
            "bid": 0.95,
            "ask": 1.05,
            "delta": 0.3,
            "expected_move": 5.0,
            "expiry_date": "2026-07-10",
            "strike_price": 210.0,
            "spread": 0.1,
        }

        state._submit_option_trade_request("AAPL", contract["symbol"], "Only Long Call", selected_contract_override=contract, submit_to_broker=False)
        watchlist_contract = {**contract, "symbol": "CART260710C00050000", "strike_price": 50.0}
        state._submit_option_trade_request("CART", watchlist_contract["symbol"], "Only Long Call", selected_contract_override=watchlist_contract, submit_to_broker=False)

        self.assertEqual([row["account_profile_id"] for row in state.repository.logged_trades], ["paper3", "paper5"])
        self.assertEqual([row["account_label"] for row in state.repository.logged_trades], ["Mag7 OPTION", "Watchlist option"])

    def test_automatic_option_universe_uses_the_selected_book_only(self):
        state = make_state()
        state.option_watchlist = ["CART", "AAPL", "PLTR"]
        state._mag7_option_underlyings = lambda: ["AAPL", "NVDA"]

        state.option_bot_config["watchlistSource"] = "mag7"
        self.assertEqual(state._option_bot_trade_universe(), ["AAPL", "NVDA"])

        state.option_bot_config["watchlistSource"] = "option"
        self.assertEqual(state._option_bot_trade_universe(), ["CART", "AAPL", "PLTR"])

    def test_option_broker_orders_payload_exposes_submitted_order_details(self):
        state = make_state()
        state.option_client.submit_option_limit_order(
            "PLTR260702C00129000",
            qty=5,
            limit_price=0.50,
            client_order_id="option-PLTR-test",
            position_intent="buy_to_open",
        )

        payload = state._option_broker_orders_payload(state._option_broker_snapshot())

        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["symbol"], "PLTR260702C00129000")
        self.assertEqual(payload[0]["underlying"], "PLTR")
        self.assertEqual(payload[0]["status"], "filled")
        self.assertEqual(payload[0]["side"], "buy")
        self.assertEqual(payload[0]["qty"], 5)
        self.assertEqual(payload[0]["filledQty"], 5)
        self.assertEqual(payload[0]["limitPrice"], 0.50)
        self.assertEqual(payload[0]["notionalCost"], 250.0)

    def test_close_option_position_submits_sell_to_close_and_updates_journal(self):
        row = open_pltr_row(make_state())
        state = make_state(rows=[row], mid=0.75)

        payload = state.close_option_position("PLTR260702C00129000")

        result = payload["result"]
        self.assertIn(result["status"], {"submitted", "closed"})
        self.assertEqual(result["symbol"], "PLTR260702C00129000")
        self.assertEqual(result["journalStatus"], "closed")
        self.assertFalse(state.option_client.positions)
        sell_order = state.option_client.orders[-1]
        self.assertEqual(sell_order.side, "sell")
        self.assertEqual(sell_order.symbol, "PLTR260702C00129000")
        updated = state.repository.rows[0]
        self.assertEqual(updated["status"], "closed")
        self.assertEqual(updated["notes"], "manual_close_requested")
        self.assertGreater(updated["pnl"], 0)

    def test_close_option_position_reports_missing_contract(self):
        state = make_state()

        payload = state.close_option_position("MISSING260702C00100000")

        self.assertEqual(payload["result"]["status"], "missing")
        self.assertIn("No open option position", payload["result"]["message"])

    def test_manage_option_positions_now_closes_stopped_out_position_without_starting_bot(self):
        row = open_pltr_row(make_state())
        state = make_state(rows=[row], mid=0.25)
        state.option_bot_state = "Stopped"

        payload = state.manage_option_positions_now()

        result = payload["result"]
        self.assertEqual(state.option_bot_state, "Stopped")
        self.assertEqual(result["status"], "managed")
        self.assertEqual(result["managed"], 1)
        self.assertEqual(result["closed"][0]["reason"], "option_stop_loss_hit")
        self.assertEqual(state.repository.rows[0]["status"], "closed")
        self.assertFalse(state.option_client.positions)

    def test_mag7_option_watchlist_maps_leveraged_symbols_to_underlyings(self):
        state = make_state()
        state.option_bot_config["watchlistSource"] = "mag7"

        active_watchlist = state._active_option_watchlist()

        self.assertIn("NVDA", active_watchlist)
        self.assertIn("AMD", active_watchlist)
        self.assertIn("META", active_watchlist)
        self.assertIn("TSLA", active_watchlist)
        self.assertIn("SPCX", active_watchlist)
        self.assertNotIn("NVDL", active_watchlist)
        self.assertNotIn("AMDL", active_watchlist)
        self.assertNotIn("METU", active_watchlist)
        self.assertNotIn("TSLL", active_watchlist)
        self.assertNotIn("SPCU", active_watchlist)
        self.assertEqual(active_watchlist.count("AVGO"), 1)
        self.assertEqual(state._option_watchlist_source_label(), "MAG7-Watchlist Options")

    def test_option_scan_coverage_reports_no_candidate_symbols(self):
        state = make_state()
        state.option_watchlist = ["PLTR", "TSLA", "NVDA"]
        state.option_scan_timestamp = datetime(2026, 7, 2, 13, 35, tzinfo=timezone.utc)
        state.option_candidate_results = pd.DataFrame(
            [
                {"symbol": "PLTR", "allowed": True},
                {"symbol": "TSLA", "allowed": False},
            ]
        )
        state.option_plan_blocks = [{"symbol": "TSLA", "reason": "wide spread"}]

        coverage = state._option_scan_coverage_payload()
        no_candidate_rows = state._option_no_candidate_results()

        self.assertEqual(coverage["watchlistCount"], 3)
        self.assertEqual(coverage["candidateCount"], 2)
        self.assertEqual(coverage["qualifiedCount"], 1)
        self.assertEqual(coverage["entryRuleBlockedCount"], 1)
        self.assertEqual(coverage["plannerBlockedCount"], 1)
        self.assertEqual(coverage["noCandidateCount"], 1)
        self.assertEqual(coverage["noCandidateSymbols"], ["NVDA"])
        self.assertEqual(no_candidate_rows[0]["stage"], "Scanner")
        self.assertIn("No current option setup row", no_candidate_rows[0]["reason"])

    def test_option_llm_supervisor_is_advisory_only_and_reviews_blocks(self):
        state = make_state()
        state.option_bot_state = "Running"
        state.option_watchlist = ["PLTR", "TSLA"]
        state.option_scan_timestamp = datetime(2026, 7, 2, 13, 35, tzinfo=timezone.utc)
        state.option_candidate_results = pd.DataFrame(
            [
                {
                    "symbol": "PLTR",
                    "allowed": True,
                    "option_rule_passed": True,
                    "setup_name": "EMA + VWAP + ORB",
                },
                {
                    "symbol": "TSLA",
                    "allowed": False,
                    "option_rule_passed": False,
                    "option_rule_rejection_reason": "volume is not increasing",
                },
            ]
        )
        state.option_plan_blocks = [{"symbol": "TSLA", "reason": "wide spread"}]

        report = state._refresh_option_supervisor_report(state.option_candidate_results)

        self.assertEqual(report["mode"], "advisory_only")
        self.assertFalse(report["authority"]["canPlaceOrders"])
        self.assertFalse(report["authority"]["canOverrideRules"])
        self.assertEqual(report["llm"]["mode"], "disabled")
        self.assertTrue(report["skipped"])
        self.assertTrue(report["suggestions"])
        self.assertIn("LLM", report["name"])
        self.assertTrue(any(event[0] == "option_llm_supervisor" for event in state.repository.events))

    def test_plan_option_trade_blocks_when_alpaca_option_buying_power_is_zero(self):
        state = make_state(buying_power=0.0)
        candidates = pd.DataFrame(
            [
                {
                    "symbol": "PLTR",
                    "allowed": True,
                    "entry": 125.73,
                    "setup_name": "EMA + VWAP + ORB",
                    "strategy_family": "Momentum + Price Action Trend",
                    "option_rule_trigger_match": "EMA + VWAP + ORB",
                    "option_one_hour_close_change_pct": 0.42,
                    "option_four_hour_close_change_pct": 0.82,
                    "option_four_hour_volume_change_pct": 2.10,
                    "option_rule_passed": True,
                }
            ]
        )

        result = state._plan_option_paper_trades(candidates)

        self.assertEqual(result["created"], 0)
        self.assertTrue(result["blocked"])
        self.assertIn("buying power", result["blocked"][0]["reason"])
        self.assertEqual(state.repository.logged_trades, [])

    def test_sync_archives_local_only_open_rows_and_dashboard_hides_them(self):
        seed_state = make_state()
        row = open_pltr_row(seed_state)
        row["broker_order_id"] = ""
        row["account_profile_id"] = "paper3"
        row["account_label"] = "OPTION TRADE"
        state = make_state(rows=[row], mid=0.50)
        state.option_client.positions = {}

        snapshot = state._sync_option_broker_state()
        self.assertEqual(snapshot["positions"], [])
        self.assertEqual(state.repository.rows[0]["status"], "archived_local_only")
        self.assertIsNotNone(state.repository.rows[0]["closed_at"])
        self.assertIn("Alpaca-only sync", state.repository.rows[0]["notes"])
        self.assertTrue(state.repository.get_option_trade_history(broker_only=True).empty)
        self.assertEqual(state.repository.option_trades_today_count(profile_id="paper3", broker_only=True), 0)

    def test_sync_keeps_order_quantity_when_broker_position_is_aggregate(self):
        seed_state = make_state()
        first = open_pltr_row(seed_state)
        first["client_order_id"] = "option-paper-PLTR-first"
        first["broker_order_id"] = "ord-first"
        first["account_profile_id"] = "paper3"
        first["quantity"] = 3
        second = dict(first)
        second["client_order_id"] = "option-paper-PLTR-second"
        second["broker_order_id"] = "ord-second"
        state = make_state(rows=[first, second], mid=0.25)
        option_symbol = first["option_symbol"]
        state.option_client.positions[option_symbol] = types.SimpleNamespace(
            symbol=option_symbol,
            qty="6",
            avg_entry_price="0.50",
            current_price="0.25",
            unrealized_pl="-150.0",
            unrealized_intraday_pl="0.0",
            asset_class="us_option",
        )

        state._sync_option_broker_state()

        self.assertEqual([row["quantity"] for row in state.repository.rows], [3, 3])
        for row in state.repository.rows:
            plan = json.loads(row["analysis_json"])
            self.assertEqual(plan["broker_position_quantity"], 6)
            self.assertEqual(plan["remaining_quantity"], 3)
            self.assertEqual(row["pnl"], -75.0)

    def test_manage_option_trade_scales_out_trails_runner_and_closes(self):
        initial_state = make_state()
        row = open_pltr_row(initial_state)
        state = make_state(rows=[row], mid=1.10)

        first = state.manage_option_paper_trades()
        plan_after_target = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(first["managed"], 1)
        self.assertTrue(plan_after_target["partial_exit_taken"])
        self.assertEqual(plan_after_target["remaining_quantity"], 1)
        self.assertEqual(plan_after_target["runner_stop"], 0.5)
        self.assertEqual(round(plan_after_target["realized_pnl"], 2), 120.0)
        self.assertEqual(round(state.repository.rows[0]["pnl"], 2), 180.0)

        state.market_data_client.mid = 0.45
        second = state.manage_option_paper_trades()

        self.assertEqual(second["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "closed")
        self.assertEqual(state.repository.rows[0]["exit_price"], 0.45)
        self.assertEqual(round(state.repository.rows[0]["pnl"], 2), 115.0)
        self.assertEqual(state.repository.rows[0]["notes"], "runner_stop_hit")

    def test_manage_option_trade_scales_out_at_underlying_liquidity_target_before_premium_target(self):
        initial_state = make_state(mid=1.25)
        initial_state.option_risk_settings["contractQuantity"] = "5"
        initial_state.market_data_client = FakeTslaLiquidityChainMarket(mid=1.25, underlying_price=420.50)
        selected, error = initial_state._select_option_contract("TSLA")
        self.assertEqual(error, "")
        quantity = initial_state._option_contract_quantity(selected["mid"])
        plan = initial_state._option_initial_trade_plan(
            selected["mid"],
            quantity,
            selected,
            {"setup_name": "EMA + VWAP + ORB"},
        )
        row = {
            "client_order_id": "option-paper-TSLA-liquidity-target",
            "underlying_symbol": "TSLA",
            "option_symbol": selected["symbol"],
            "quantity": quantity,
            "entry_price": selected["mid"],
            "stop_price": plan["runner_stop"],
            "target_price": plan["take_profit_1"],
            "status": "position_open",
            "closed_at": None,
            "pnl": 0.0,
            "exit_price": None,
            "analysis_json": json.dumps(plan),
            "notes": "",
        }
        state = make_state(rows=[row], mid=1.35)
        state.market_data_client = FakeTslaLiquidityChainMarket(mid=1.35, underlying_price=430.25)
        state.option_client.price_lookup = lambda symbol, market=state.market_data_client: market.mid
        state.market_data_client.chart_bars = pd.DataFrame(
            [
                {
                    "timestamp": pd.Timestamp("2026-07-08T15:45:00Z"),
                    "open": 429.50,
                    "high": 430.50,
                    "low": 429.25,
                    "close": 430.25,
                    "volume": 2500000,
                }
            ]
        )

        result = state.manage_option_paper_trades()
        updated_plan = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(result["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "position_open")
        self.assertEqual(state.repository.rows[0]["notes"], "underlying_liquidity_strike_target_1_hit")
        self.assertTrue(updated_plan["partial_exit_taken"])
        self.assertEqual(updated_plan["partial_exit_qty"], 4.0)
        self.assertEqual(updated_plan["remaining_quantity"], 1.0)
        self.assertEqual(updated_plan["runner_stop"], 1.25)
        self.assertEqual(updated_plan["underlying_target_1_hit_price"], 430.25)
        self.assertEqual(state.option_client.positions[selected["symbol"]].qty, "1")
        self.assertEqual(state.option_client.orders[-1].side, "sell")
        self.assertEqual(state.option_client.orders[-1].qty, "4")

    def test_manage_option_trade_exits_on_default_underlying_ema20_break(self):
        initial_state = make_state()
        initial_state.option_risk_settings["stopLossPercent"] = ""
        row = open_pltr_row(initial_state)
        row["broker_order_id"] = "ord-entry"
        row["account_profile_id"] = "paper3"
        state = make_state(rows=[row], mid=0.45)
        timestamps = pd.date_range("2026-07-02T13:30:00Z", periods=21, freq="5min")
        closes = [100.0] * 20 + [90.0]
        state.market_data_client.chart_bars = pd.DataFrame(
            [
                {
                    "timestamp": timestamp,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000000,
                }
                for timestamp, close in zip(timestamps, closes)
            ]
        )

        result = state.manage_option_paper_trades()
        updated_plan = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(result["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "closed")
        self.assertEqual(state.repository.rows[0]["notes"], "option_underlying_5m_ema20_break")
        self.assertEqual(updated_plan["stop_loss_mode"], "ema20_candle")
        self.assertEqual(updated_plan["ema20_stop"]["underlying"], "PLTR")
        self.assertLess(updated_plan["ema20_stop"]["close"], updated_plan["ema20_stop"]["ema20"])

    def test_default_ema20_runner_moves_to_breakeven_after_target_1(self):
        initial_state = make_state()
        initial_state.option_risk_settings["stopLossPercent"] = ""
        row = open_pltr_row(initial_state)
        state = make_state(rows=[row], mid=1.10)
        timestamps = pd.date_range("2026-07-02T13:30:00Z", periods=21, freq="5min")
        state.market_data_client.chart_bars = pd.DataFrame(
            [
                {
                    "timestamp": timestamp,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000000,
                }
                for timestamp in timestamps
            ]
        )

        first = state.manage_option_paper_trades()
        plan_after_target = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(first["managed"], 1)
        self.assertTrue(plan_after_target["partial_exit_taken"])
        self.assertEqual(plan_after_target["stop_loss_mode"], "ema20_candle")
        self.assertEqual(plan_after_target["runner_stop"], 0.5)
        self.assertEqual(plan_after_target["runner_stop_locked_pct"], 0.0)

        state.market_data_client.mid = 0.49
        second = state.manage_option_paper_trades()

        self.assertEqual(second["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "closed")
        self.assertEqual(state.repository.rows[0]["notes"], "runner_stop_hit")

    def test_default_ema20_runner_exits_on_ema20_break_after_target_1(self):
        initial_state = make_state()
        initial_state.option_risk_settings["stopLossPercent"] = ""
        row = open_pltr_row(initial_state)
        state = make_state(rows=[row], mid=1.10)
        timestamps = pd.date_range("2026-07-02T13:30:00Z", periods=21, freq="5min")
        flat_bars = pd.DataFrame(
            [
                {
                    "timestamp": timestamp,
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.0,
                    "volume": 1000000,
                }
                for timestamp in timestamps
            ]
        )
        state.market_data_client.chart_bars = flat_bars

        first = state.manage_option_paper_trades()
        plan_after_target = json.loads(state.repository.rows[0]["analysis_json"])
        self.assertEqual(first["managed"], 1)
        self.assertTrue(plan_after_target["partial_exit_taken"])
        self.assertEqual(plan_after_target["runner_stop"], 0.5)

        state.market_data_client.mid = 0.70
        state.market_data_client.chart_bars = pd.DataFrame(
            [
                {
                    "timestamp": timestamp,
                    "open": close,
                    "high": close + 1,
                    "low": close - 1,
                    "close": close,
                    "volume": 1000000,
                }
                for timestamp, close in zip(timestamps, [100.0] * 20 + [90.0])
            ]
        )
        second = state.manage_option_paper_trades()
        updated_plan = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(second["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "closed")
        self.assertEqual(state.repository.rows[0]["notes"], "option_underlying_5m_ema20_break")
        self.assertEqual(updated_plan["runner_stop"], 0.5)
        self.assertGreater(state.repository.rows[0]["exit_price"], updated_plan["runner_stop"])

    def test_runner_lock_step_moves_stop_to_100_percent_at_200_percent_gain(self):
        initial_state = make_state()
        initial_state.option_risk_settings["runnerLockStepPercent"] = "50"
        row = open_pltr_row(initial_state)
        state = make_state(rows=[row], mid=1.10)

        first = state.manage_option_paper_trades()
        plan_after_target = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(first["managed"], 1)
        self.assertTrue(plan_after_target["partial_exit_taken"])
        self.assertEqual(plan_after_target["runner_stop"], 0.5)

        state.market_data_client.mid = 1.50
        second = state.manage_option_paper_trades()
        plan_after_200 = json.loads(state.repository.rows[0]["analysis_json"])

        self.assertEqual(second["managed"], 1)
        self.assertEqual(state.repository.rows[0]["status"], "position_open")
        self.assertEqual(plan_after_200["runner_locked_profit_percent"], 100.0)
        self.assertEqual(plan_after_200["runner_stop"], 1.0)
        self.assertEqual(plan_after_200["runner_stop_locked_pct"], 100.0)


class OptionEnginePerformanceTests(unittest.TestCase):
    def setUp(self):
        self.previous_provider = api_server.settings.market_data_provider
        api_server.settings.market_data_provider = "schwab"

    def tearDown(self):
        api_server.settings.market_data_provider = self.previous_provider

    def test_manage_100_open_option_positions_under_three_seconds(self):
        seed_state = make_state()
        rows = []
        for index in range(100):
            row = open_pltr_row(seed_state)
            underlying = f"P{index:03d}"
            row["client_order_id"] = f"option-paper-{underlying}-perf"
            row["underlying_symbol"] = underlying
            row["option_symbol"] = f"{underlying}260702C00129000"
            rows.append(row)
        state = make_state(rows=rows, mid=0.70)

        started = time.perf_counter()
        result = state.manage_option_paper_trades()
        elapsed = time.perf_counter() - started

        self.assertEqual(result["managed"], 100)
        self.assertLess(elapsed, 3.0)


class WatchlistBatchEngineTests(unittest.TestCase):
    def test_incremental_merge_preserves_existing_order_and_prepends_new_symbols(self):
        state = make_state()
        existing = pd.DataFrame(
            [
                {"underlying": "AAPL", "contract": "AAPL OLD", "priority_score": 70},
                {"underlying": "AMD", "contract": "AMD OLD", "priority_score": 60},
            ]
        )
        incoming = pd.DataFrame(
            [
                {"underlying": "AMD", "contract": "AMD NEW", "priority_score": 80},
                {"underlying": "NVDA", "contract": "NVDA NEW", "priority_score": 90},
            ]
        )

        merged = state._merge_watchlist_oi_results(existing, incoming)

        self.assertEqual(merged["underlying"].tolist(), ["NVDA", "AAPL", "AMD"])
        self.assertEqual(merged.loc[merged["underlying"] == "AMD", "contract"].iloc[0], "AMD NEW")

    def test_empty_batch_does_not_erase_daily_watchlist_results(self):
        state = make_state()
        existing = pd.DataFrame([{"underlying": "AAPL", "contract": "AAPL 315C"}])

        merged = state._merge_watchlist_oi_results(existing, pd.DataFrame())

        self.assertEqual(merged.to_dict("records"), existing.to_dict("records"))

    def test_daily_oi_merge_drops_previous_market_day_and_keeps_today(self):
        state = make_state()
        existing = pd.DataFrame(
            [
                {
                    "underlying": "AAPL",
                    "contract": "AAPL OLD",
                    "last_seen_at": "2026-07-13T15:55:00-04:00",
                }
            ]
        )
        incoming = pd.DataFrame(
            [
                {
                    "underlying": "AMZN",
                    "contract": "AMZN NEW",
                    "first_seen_at": "2026-07-14T09:30:00-04:00",
                    "last_seen_at": "2026-07-14T09:30:00-04:00",
                }
            ]
        )

        merged = state._merge_daily_oi_results(
            existing,
            incoming,
            datetime.fromisoformat("2026-07-14T09:30:00-04:00"),
        )

        self.assertEqual(merged["underlying"].tolist(), ["AMZN"])

    def test_restore_today_oi_results_uses_latest_persisted_symbol_snapshot(self):
        state = DashboardState.__new__(DashboardState)
        today = pd.Timestamp.now(tz=api_server.EASTERN_TZ).date().isoformat()
        first_seen = f"{today}T09:30:00-04:00"
        last_seen = f"{today}T09:45:00-04:00"
        history = pd.DataFrame(
            [
                {
                    "scan_date": today,
                    "source": "MAG7 OI Scanner",
                    "scanned_at": first_seen,
                    "symbol": "AAPL",
                    "raw_json": json.dumps({"underlying": "AAPL", "contract": "AAPL OLD"}),
                },
                {
                    "scan_date": today,
                    "source": "MAG7 OI Scanner",
                    "scanned_at": last_seen,
                    "symbol": "AAPL",
                    "raw_json": json.dumps({"underlying": "AAPL", "contract": "AAPL NEW"}),
                },
            ]
        )
        state.repository = types.SimpleNamespace(get_scanner_history=lambda days=2: (history, pd.DataFrame()))
        state.oi_mag7_scan_results = pd.DataFrame()
        state.oi_mag7_last_non_empty_results = pd.DataFrame()
        state.oi_watchlist_scan_results = pd.DataFrame()
        state.oi_watchlist_last_non_empty_results = pd.DataFrame()

        state._restore_today_oi_results()

        restored = state.oi_mag7_scan_results.iloc[0]
        self.assertEqual(restored["contract"], "AAPL NEW")
        self.assertEqual(restored["first_seen_at"], first_seen)
        self.assertEqual(restored["last_seen_at"], last_seen)

    def test_parallel_cycle_scans_every_symbol_once_with_five_workers(self):
        state = DashboardState.__new__(DashboardState)
        state.oi_watchlist_batch_size = 25
        state.oi_watchlist_worker_count = 5
        state.oi_watchlist_scan_lock = threading.Lock()
        state.oi_watchlist_scan_results = pd.DataFrame()
        state.oi_watchlist_completed_cycles = 0
        state.oi_watchlist_batch_cursor = 0
        state.oi_watchlist_batch_start = 0
        state.oi_watchlist_batch_end = 0
        state.oi_watchlist_cycle_started_at = None
        state.oi_watchlist_cycle_duration_seconds = 0.0
        state.oi_watchlist_batches_completed = 0
        state.oi_watchlist_batch_count = 0
        prior_heartbeat = datetime.now().astimezone() - timedelta(hours=1)
        state.oi_watchlist_auto_last_run = prior_heartbeat
        state._normalize_option_watchlist = lambda symbols: list(dict.fromkeys(symbols))
        state._update_oi_scanner_auto_summary = lambda: None
        seen = []
        heartbeats_at_batch_start = []
        active = 0
        max_active = 0
        guard = threading.Lock()

        def fake_scan(self, symbols, **kwargs):
            nonlocal active, max_active
            with guard:
                heartbeats_at_batch_start.append(state.oi_watchlist_auto_last_run)
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.01)
            with guard:
                seen.extend(symbols)
                active -= 1
            return {"resultCount": 0, "errors": []}

        state._execute_oi_scan = types.MethodType(fake_scan, state)
        symbols = [f"T{index:03d}" for index in range(397)]
        payload = state._execute_parallel_watchlist_oi_cycle(symbols)

        self.assertEqual(sorted(seen), sorted(symbols))
        self.assertEqual(len(seen), len(set(seen)))
        self.assertEqual(payload["workers"], 5)
        self.assertEqual(payload["batches"], 16)
        self.assertLessEqual(max_active, 5)
        self.assertGreaterEqual(max_active, 2)
        self.assertTrue(heartbeats_at_batch_start)
        self.assertTrue(all(value > prior_heartbeat for value in heartbeats_at_batch_start))
        self.assertGreater(state.oi_watchlist_auto_last_run, prior_heartbeat)

    def test_catalyst_information_batches_rotate_across_full_universe(self):
        state = DashboardState.__new__(DashboardState)
        state.catalyst_refresh_batch_size = 2
        state.catalyst_refresh_cursor = 0

        first = state._next_catalyst_information_batch(["AAPL", "PLTR", "AMD"])
        second = state._next_catalyst_information_batch(["AAPL", "PLTR", "AMD"])

        self.assertEqual(first, ["AAPL", "PLTR"])
        self.assertEqual(second, ["AMD", "AAPL"])

    def test_catalyst_information_refresh_is_non_blocking_and_non_overlapping(self):
        state = DashboardState.__new__(DashboardState)
        state.catalyst_refresh_lock = threading.Lock()
        state.catalyst_refresh_batch_size = 2
        state.catalyst_refresh_cursor = 0
        state.repository = types.SimpleNamespace(log_bot_event=lambda *args, **kwargs: None)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_refresh(symbols):
            calls.append(list(symbols))
            started.set()
            release.wait(timeout=2)
            return {}

        state._refresh_catalyst_information = fake_refresh

        self.assertTrue(state._schedule_catalyst_information_refresh(["AAPL", "PLTR", "AMD"]))
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(state._schedule_catalyst_information_refresh(["AAPL", "PLTR", "AMD"]))
        release.set()
        state.catalyst_refresh_thread.join(timeout=2)

        self.assertEqual(calls, [["AAPL", "PLTR"]])
        self.assertFalse(state.catalyst_refresh_thread.is_alive())

    def test_option_supervisor_review_is_non_blocking_and_non_overlapping(self):
        state = DashboardState.__new__(DashboardState)
        state.option_supervisor_refresh_lock = threading.Lock()
        state.repository = types.SimpleNamespace(log_bot_event=lambda *args, **kwargs: None)
        started = threading.Event()
        release = threading.Event()
        calls = []

        def fake_refresh(candidates, plan_result, manage_result):
            calls.append((candidates.copy(), dict(plan_result), dict(manage_result)))
            started.set()
            release.wait(timeout=2)
            return {}

        state._refresh_option_supervisor_report = fake_refresh
        candidates = pd.DataFrame([{"symbol": "PLTR", "allowed": True}])

        self.assertTrue(state._schedule_option_supervisor_report(candidates, {"created": 1}, {"managed": 1}))
        self.assertTrue(started.wait(timeout=1))
        self.assertFalse(state._schedule_option_supervisor_report(candidates, {}, {}))
        release.set()
        state.option_supervisor_refresh_thread.join(timeout=2)

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0].iloc[0]["symbol"], "PLTR")
        self.assertFalse(state.option_supervisor_refresh_thread.is_alive())

if __name__ == "__main__":
    unittest.main(verbosity=2)
