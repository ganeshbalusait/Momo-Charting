"""Integration: the Stock Paper Trading page's routes over real HTTP.

Risk settings, the paper-account switch, per-row close / Close All and the
stock bot Start / Pause - through api_server's real handler and the REAL
DashboardState methods. The Alpaca stock trader is a fake that records each
call; the journal is a fake that records each saved setting and event.
settings.trading is restored after every test.
"""
from __future__ import annotations

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from config import settings
from test_option_auto_engine import make_state

OWNER = "owner-cov-stock@example.com"
VIEWER = "viewer-cov-stock@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
RISK_FIELDS = ("daily_trade_amount", "fixed_trade_amount", "stop_loss_percent", "stop_loss_amount", "take_profit_1_pct")


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def _restore_trading_settings(monkeypatch):
    for field in RISK_FIELDS:
        monkeypatch.setattr(settings.trading, field, getattr(settings.trading, field))


class FakeStockTrader:
    def __init__(self):
        self.calls = []

    def close_position(self, symbol):
        self.calls.append(("close_position", symbol))
        return {"message": f"Close order submitted for {symbol}.", "symbol": symbol, "status": "submitted"}

    def close_all_positions(self):
        self.calls.append(("close_all_positions",))
        return {"message": "Close orders submitted for 2 positions.", "status": "submitted"}


def stock_state():
    state = make_state()
    state.trader = FakeStockTrader()
    state.client.mode = "paper"
    state.active_profile_id = "default-paper"
    state.scheduler_enabled = False
    state.scheduler_thread = None
    state.dashboard_payload = lambda: {"actionMessage": state.action_message, "riskSettings": state._risk_settings_payload()}
    return state


def saved_settings(state) -> dict:
    return {event[1]: event[2] for event in state.repository.events if len(event) == 3 and event[0] == "setting"}


def events(state, kind):
    return [event for event in state.repository.events if event and event[0] == kind]


# ------------------------------------------------------------ risk settings

def test_risk_settings_are_saved_and_the_next_dashboard_read_shows_them():
    state = stock_state()
    with running_api(state=state) as api:
        status, body = api.post("/api/risk-settings", {
            "dailyTradeAmount": 5000, "tradeAmount": 500, "stopLossPercent": 2, "stopLossAmount": 10,
            "firstProfitTargetPercent": 3,
        }, headers=ADMIN)
        _s, dashboard = api.get("/api/dashboard?compact=true", headers=ADMIN)
    assert status == 200, body
    assert dashboard["riskSettings"]["dailyTradeAmount"] == 5000
    assert dashboard["riskSettings"]["tradeAmount"] == 500
    assert dashboard["riskSettings"]["stopLossPercent"] == 2
    assert dashboard["riskSettings"]["stopLossAmount"] == 10
    assert dashboard["riskSettings"]["firstProfitTargetPercent"] == 3
    saved = saved_settings(state)
    assert (saved["daily_trade_amount"], saved["trade_amount"], saved["stop_loss_percent"]) == ("5000.0", "500.0", "2.0")
    assert body["actionMessage"].startswith("Risk settings updated: $5000 daily capital, $500 per trade, 2.00% stop")


def test_a_stop_dollar_amount_alone_sets_the_stop_percent_from_the_trade_size():
    state = stock_state()
    with running_api(state=state) as api:
        api.post("/api/risk-settings", {"tradeAmount": 500}, headers=ADMIN)
        status, body = api.post("/api/risk-settings", {"stopLossAmount": 15}, headers=ADMIN)
    assert status == 200
    assert body["riskSettings"]["stopLossPercent"] == 3.0
    assert body["riskSettings"]["stopLossAmount"] == 15.0


def test_zero_or_negative_inputs_are_clamped_never_saved_as_zero():
    state = stock_state()
    with running_api(state=state) as api:
        _s, body = api.post("/api/risk-settings", {"dailyTradeAmount": 0, "tradeAmount": -5, "stopLossPercent": 0,
                                                   "firstProfitTargetPercent": 0}, headers=ADMIN)
    risk = body["riskSettings"]
    assert (risk["dailyTradeAmount"], risk["tradeAmount"]) == (1.0, 1.0)
    assert risk["stopLossPercent"] == 0.01 and risk["firstProfitTargetPercent"] == 0.01


def test_a_non_admin_cannot_change_risk_settings():
    state = stock_state()
    before = settings.trading.fixed_trade_amount
    with running_api(state=state) as api:
        status, body = api.post("/api/risk-settings", {"tradeAmount": 1}, headers=USER)
    assert (status, body["error"]) == (403, "Administrator access is required.")
    assert settings.trading.fixed_trade_amount == before and saved_settings(state) == {}


# ------------------------------------------------------------ account switch

def test_account_select_needs_a_known_profile():
    state = stock_state()
    with running_api(state=state) as api:
        missing = api.post("/api/account-select", {}, headers=ADMIN)
        unknown = api.post("/api/account-select", {"profileId": "no-such-profile"}, headers=ADMIN)
        refused = api.post("/api/account-select", {"profileId": "no-such-profile"}, headers=USER)
    assert missing == (400, {"error": "profileId is required."})
    assert unknown == (400, {"error": "Unknown account profile: no-such-profile"})
    assert refused[0] == 403
    assert events(state, "account_switch") == []


def test_an_options_only_account_can_never_become_the_stock_account():
    state = stock_state()
    option_profile = settings.option_account_profile_id("paper")
    if option_profile not in {item["id"].lower() for item in state.available_accounts()}:
        pytest.skip("this PC has no option paper profile configured")
    with running_api(state=state) as api:
        status, body = api.post("/api/account-select", {"profileId": option_profile}, headers=ADMIN)
    assert status == 400
    assert body["error"].endswith("is reserved for option paper trading. Stock trading is blocked on this account.")
    assert state.active_profile_id == "default-paper"


# -------------------------------------------------------- close / close all

def test_close_one_position_needs_a_symbol_then_closes_exactly_that_symbol():
    state = stock_state()
    with running_api(state=state) as api:
        missing = api.post("/api/close-position", {"symbol": "  "}, headers=ADMIN)
        status, body = api.post("/api/close-position", {"symbol": "aapl"}, headers=ADMIN)
    assert missing == (400, {"error": "Symbol is required."})
    assert status == 200
    assert state.trader.calls == [("close_position", "AAPL")]
    assert body["result"]["message"] == "Close order submitted for AAPL."
    assert body["dashboard"]["actionMessage"] == "Close order submitted for AAPL."
    assert [event[1] for event in events(state, "manual_close_request")] == ["Close order submitted for AAPL."]


def test_close_all_makes_one_broker_call_and_logs_it():
    state = stock_state()
    with running_api(state=state) as api:
        status, body = api.post("/api/close-all-positions", {}, headers=ADMIN)
    assert status == 200
    assert state.trader.calls == [("close_all_positions",)]
    assert body["result"]["message"] == "Close orders submitted for 2 positions."
    assert len(events(state, "manual_close_all_request")) == 1


# ------------------------------------------------------------ stock bot

def test_bot_control_rejects_an_unknown_state_and_pause_marks_the_bot_paused():
    state = stock_state()
    with running_api(state=state) as api:
        bad = api.post("/api/bot-control", {"state": "Go"}, headers=ADMIN)
        status, body = api.post("/api/bot-control", {"state": "paused"}, headers=ADMIN)
    assert bad == (400, {"error": "State must be Running, Paused, or Stopped."})
    assert status == 200 and body["botState"] == "Paused"
    assert state.scheduler_enabled is False and state.scheduler_thread is None


def test_running_on_an_options_only_account_ends_stopped_with_the_reason():
    state = stock_state()
    state.active_profile_id = settings.option_account_profile_id("paper")
    with running_api(state=state) as api:
        status, body = api.post("/api/bot-control", {"state": "Running"}, headers=ADMIN)
    assert status == 200
    assert body["botState"] == "Stopped"
    assert body["actionMessage"].endswith("Stock trading is blocked on this account.")
    assert state.scheduler_thread is None and state.scheduler_enabled is False
    assert len(events(state, "bot_state_blocked")) == 1
