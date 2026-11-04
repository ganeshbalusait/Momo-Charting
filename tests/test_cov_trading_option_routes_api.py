"""Integration: the Option Paper Trading page's routes over real HTTP.

Settings, bot Start / Pause / Stop, the position and order buttons, the order
ticket's "Log ... To Option Journal" and the dashboard keys the page reads -
each through api_server's real handler and the REAL DashboardState methods
behind it. The state is the option-engine test state (tests/test_option_auto_
engine.py make_state): a fake Alpaca option client that records every order,
a fake journal, and a fake chain. Nothing reaches Alpaca.
"""
from __future__ import annotations

import types
from datetime import datetime, timezone

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from config import settings
from test_option_auto_engine import FakeOptionCredentials, make_state, open_pltr_row

OWNER = "owner-cov-option@example.com"
VIEWER = "viewer-cov-option@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
PLTR = "PLTR260702C00129000"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


def saved_settings(state) -> dict:
    return {event[1]: event[2] for event in state.repository.events if len(event) == 3 and event[0] == "setting"}


def orders(state, side=None):
    return [o for o in state.option_client.orders if side is None or o.side == side]


def light(state):
    """The bot-control payload reads the dashboard cache and the watchlist; keep both local."""
    state.scheduler_starts = []
    state._start_option_scheduler = lambda: state.scheduler_starts.append(1)
    return state


# ------------------------------------------------------------- settings

def test_watchlist_and_approval_go_through_the_real_normaliser_and_are_saved():
    state = make_state()
    with running_api(state=state) as api:
        status, _body = api.post("/api/option-bot-settings", {
            "contractPolicy": "only_long_call", "approvalMode": "human", "spreadFilter": " 10% ",
            "deltaTarget": "0.20", "expectedMove": ">=2", "watchlistSource": "momx",
        }, headers=ADMIN)
        assert status == 200
        assert state.option_bot_config["watchlistSource"] == "momx"
        api.post("/api/option-bot-settings", {"watchlistSource": "MomX_Scanner"}, headers=ADMIN)
        assert state.option_bot_config["watchlistSource"] == "momx"
        api.post("/api/option-bot-settings", {"watchlistSource": "something-new"}, headers=ADMIN)
    saved = saved_settings(state)
    assert state.option_bot_config["watchlistSource"] == "option"
    assert saved["option_watchlist_source"] == "option"
    assert saved["option_approval_mode"] == "human"
    assert saved["option_spread_filter"] == "10%"


@pytest.mark.xfail(strict=True, reason=(
    "update_option_bot_config turns any unrecognised approvalMode into 'automatic' (Auto Mode, which sends "
    "paper orders) instead of keeping the safer 'human'"))
def test_an_unrecognised_approval_value_never_switches_auto_mode_on():
    state = make_state()
    state.option_bot_config["approvalMode"] = "human"
    with running_api(state=state) as api:
        api.post("/api/option-bot-settings", {"approvalMode": "approve-later"}, headers=ADMIN)
    assert state.option_bot_config["approvalMode"] == "human"


def test_option_risk_settings_compose_the_target_one_sell_and_save_every_field():
    state = make_state()
    with running_api(state=state) as api:
        status, _ = api.post("/api/option-risk-settings", {
            "dailyTradeAmount": "600", "tradeAmount": "", "contractQuantity": "2 cons", "stopLossPercent": "30",
            "firstProfitTargetPercent": "100", "firstProfitTargetSellMode": "contracts",
            "firstProfitTargetSellValue": "4", "runnerLockStepPercent": "50",
        }, headers=ADMIN)
        assert status == 200
        assert state.option_risk_settings["firstProfitTargetCons"] == "4 cons"
        api.post("/api/option-risk-settings", {"firstProfitTargetSellMode": "percentage", "firstProfitTargetSellValue": "80"}, headers=ADMIN)
    saved = saved_settings(state)
    assert state.option_risk_settings["firstProfitTargetCons"] == "80%"
    assert saved["option_first_profit_target_cons"] == "80%"
    assert saved["option_first_profit_target_sell_mode"] == "percentage"
    assert state.action_message == "Option risk settings updated."


def test_a_non_admin_saves_nothing_on_either_settings_route():
    state = make_state()
    with running_api(state=state) as api:
        risk = api.post("/api/option-risk-settings", {"dailyTradeAmount": "1"}, headers=USER)
        bot = api.post("/api/option-bot-settings", {"approvalMode": "automatic"}, headers=USER)
    assert risk[0] == bot[0] == 403
    assert saved_settings(state) == {}
    assert state.option_risk_settings["dailyTradeAmount"] == "500"


# -------------------------------------------------------- Start / Pause / Stop

def test_bot_control_rejects_an_unknown_state_and_pause_or_stop_never_start_the_engine():
    state = light(make_state())
    with running_api(state=state) as api:
        bad = api.post("/api/option-bot-control", {"state": "Go"}, headers=ADMIN)
        paused = api.post("/api/option-bot-control", {"state": "paused"}, headers=ADMIN)
        assert state.option_bot_state == "Paused"
        stopped = api.post("/api/option-bot-control", {"state": "Stopped"}, headers=ADMIN)
    assert bad == (400, {"error": "State must be Running, Paused, or Stopped."})
    assert paused[0] == stopped[0] == 200
    assert paused[1]["optionBot"]["state"] == "Paused" and stopped[1]["optionBot"]["state"] == "Stopped"
    assert state.scheduler_starts == []
    assert saved_settings(state)["option_bot_state"] == "Stopped"


def test_running_starts_the_engine_once_and_says_it_sends_paper_orders():
    state = light(make_state())
    with running_api(state=state) as api:
        status, body = api.post("/api/option-bot-control", {"state": "Running"}, headers=ADMIN)
    assert status == 200
    assert state.scheduler_starts == [1]
    assert body["optionBot"]["state"] == "Running"
    assert "sending Alpaca paper option orders" in body["optionBot"]["message"]
    assert saved_settings(state)["option_bot_state"] == "Running"


# ------------------------------------------------------ positions and orders

def test_close_one_option_needs_a_symbol_and_then_sends_exactly_one_sell_to_close():
    state = make_state(rows=[open_pltr_row(make_state())], mid=0.75)
    with running_api(state=state) as api:
        missing = api.post("/api/close-option-position", {}, headers=ADMIN)
        assert orders(state) == []
        status, body = api.post("/api/close-option-position", {"optionSymbol": PLTR.lower()}, headers=ADMIN)
    assert missing == (400, {"error": "optionSymbol is required."})
    assert status == 200, body
    assert body["result"]["status"] in {"submitted", "closed"}
    sells = orders(state, "sell")
    assert [(o.symbol, o.qty) for o in sells] == [(PLTR, str(open_pltr_row(make_state())["quantity"]))]
    assert len(orders(state)) == 1


class BrokenOptionClient:
    """An account whose Alpaca reads fail (expired key, outage)."""

    def normalize_option_symbol(self, symbol):
        return str(symbol or "").strip().upper().replace(" ", "")

    def get_option_positions(self):
        raise RuntimeError("paper5 401 unauthorized")

    def get_option_orders(self, *args, **kwargs):
        raise RuntimeError("paper5 401 unauthorized")

    def get_account(self):
        raise RuntimeError("paper5 401 unauthorized")

    def submit_option_limit_order(self, *args, **kwargs):
        raise AssertionError("no order may be sent through a broken account")


def two_accounts(state):
    # Mag7 -> the first option account (paper3), Watchlist -> the second (paper5).
    first = settings.option_account_profile_id("paper")
    second = settings.watchlist_option_account_profile_id("paper")
    assert first != second
    state.option_contexts = {
        first: {"profile_id": first, "credentials": FakeOptionCredentials(), "client": state.option_client, "trader": state.option_trader},
        second: {"profile_id": second, "credentials": types.SimpleNamespace(profile_id=second, label="WATCHLIST", paper=True),
                 "client": BrokenOptionClient(), "trader": state.option_trader},
    }
    state.option_profile_id = first
    return first, second


def test_close_all_still_closes_the_healthy_account_when_the_other_one_fails():
    state = make_state(rows=[open_pltr_row(make_state())], mid=0.75)
    first, second = two_accounts(state)
    with running_api(state=state) as api:
        status, body = api.post("/api/close-all-option-positions", {}, headers=ADMIN)
    assert status == 200, body
    result = body["result"]
    assert result["status"] == "submitted"
    assert [(item["profileId"], item["symbol"]) for item in result["submitted"]] == [(first, PLTR)]
    assert [item["profileId"] for item in result["errors"]] == [second]
    assert "unauthorized" in result["errors"][0]["message"]
    assert result["message"].startswith("Submitted close requests for 1 option position(s) across")
    assert len(orders(state, "sell")) == 1


def stale_orders(state):
    def order(oid, cid, symbol, qty, side):
        return types.SimpleNamespace(id=oid, client_order_id=cid, symbol=symbol, status="new", qty=str(qty),
                                     filled_qty="0", limit_price=0.4, filled_avg_price=None,
                                     submitted_at=datetime.now(timezone.utc), side=side)
    state.option_client.orders.extend([
        order("ord-stale", "option-CART-old", "CART260717C00051000", 5, "buy"),
        order("ord-current", "option-CHWY-new", "CHWY260821C00027500", 2, "buy"),
        order("ord-sell", "option-CART-exit", "CART260717C00051000", 5, "sell"),
    ])


def test_cancel_stale_buys_cancels_only_the_quantity_mismatch():
    state = make_state()
    state.option_risk_settings["contractQuantity"] = "2"
    stale_orders(state)
    with running_api(state=state) as api:
        status, body = api.post("/api/cancel-stale-option-buy-orders", {}, headers=ADMIN)
    assert status == 200, body
    assert [item["symbol"] for item in body["result"]["canceled"]] == ["CART260717C00051000"]
    assert [o.status for o in state.option_client.orders] == ["canceled", "new", "new"]


def test_manage_stops_now_closes_a_stopped_out_position_without_starting_the_bot():
    state = make_state(rows=[open_pltr_row(make_state())], mid=0.25)
    with running_api(state=state) as api:
        status, body = api.post("/api/manage-option-positions", {}, headers=ADMIN)
    assert status == 200, body
    assert body["result"]["closed"][0]["reason"] == "option_stop_loss_hit"
    assert state.option_bot_state == "Stopped"
    assert len(orders(state, "sell")) == 1


# ---------------------------------------------------- the order ticket button

CONTRACT = {"symbol": PLTR, "mid": 0.50, "bid": 0.45, "ask": 0.55, "delta": 0.17, "expected_move": 3.35,
            "expiry_date": "2026-07-02", "strike_price": 129}


def ticket_state(approval: str):
    state = make_state()
    state.option_bot_config["approvalMode"] = approval
    state._resolve_option_contract = lambda underlying, option_symbol=None, entry_price=None: (dict(CONTRACT), "")
    state._record_option_learning_observation = lambda *args, **kwargs: None
    return state


TICKET = {"underlyingSymbol": "PLTR", "optionSymbol": "PLTR CALL | Next Weekly | ATM / 1 ITM",
          "structure": "Only Long Call", "quantity": 1, "notes": "from the ticket preview"}


def test_auto_mode_ticket_sends_one_buy_at_the_mid_with_a_client_order_id():
    state = ticket_state("automatic")
    with running_api(state=state) as api:
        status, _ = api.post("/api/option-paper-trades", TICKET, headers=ADMIN)
    assert status == 201
    buys = orders(state, "buy")
    assert [(o.symbol, o.qty, o.limit_price) for o in buys] == [(PLTR, "1", 0.5)]
    assert buys[0].client_order_id.startswith("option-PLTR-")
    assert state.action_message == "Submitted Alpaca paper option order for PLTR."
    assert state.repository.logged_trades[0]["approval_mode"] == "automatic"


@pytest.mark.xfail(strict=True, reason=(
    "the duplicate-entry guard in _submit_option_trade_request compares the requested option text; the ticket "
    "button sends 'PLTR CALL | Next Weekly | ...', never the OCC contract it resolves to, so a second click buys "
    "the same already-open contract again"))
def test_a_second_ticket_click_for_the_same_open_contract_is_blocked():
    state = ticket_state("automatic")
    with running_api(state=state) as api:
        api.post("/api/option-paper-trades", TICKET, headers=ADMIN)
        profile = state._option_profile_for_underlying("PLTR")
        assert PLTR in state._active_option_contracts(profile_id=profile, option_client=state.option_client)
        api.post("/api/option-paper-trades", TICKET, headers=ADMIN)
    assert len(orders(state, "buy")) == 1
    assert "duplicate entry blocked" in state.action_message


@pytest.mark.xfail(strict=True, reason=(
    "POST /api/option-paper-trades always calls log_option_paper_trade with submit_to_broker=True, so the "
    "'Log ... To Option Journal' button sends an Alpaca paper buy even when Approval Mode is Human"))
def test_human_mode_ticket_is_queued_for_approval_and_sends_no_order():
    state = ticket_state("human")
    with running_api(state=state) as api:
        status, _ = api.post("/api/option-paper-trades", TICKET, headers=ADMIN)
    assert status == 201
    assert orders(state) == []
    assert state.action_message.startswith("Queued option approval ticket")


def test_a_ticket_without_an_underlying_sends_nothing_and_says_why():
    state = ticket_state("automatic")
    with running_api(state=state) as api:
        status, _ = api.post("/api/option-paper-trades", {**TICKET, "underlyingSymbol": ""}, headers=ADMIN)
    assert status == 201
    assert orders(state) == []
    assert state.action_message == "Underlying symbol is required."


# ------------------------------------------------------- dashboard reads

OPTION_PAGE_KEYS = ("optionBot", "optionSupervisorReport", "optionPositions", "optionAccounts", "dashboardSummary")


def test_the_five_second_poll_keeps_every_key_the_option_page_reads():
    state = make_state()
    payload = {key: {"marker": key} for key in OPTION_PAGE_KEYS}
    payload.update({key: ["heavy"] for key in api_server.DASHBOARD_COMPACT_OMIT_KEYS})
    state.dashboard_payload = lambda: dict(payload)
    with running_api(state=state) as api:
        _s, compact = api.get("/api/dashboard?compact=true", headers=ADMIN)
        _s, full = api.get("/api/dashboard", headers=ADMIN)
    for key in OPTION_PAGE_KEYS:
        assert compact[key] == {"marker": key}, key
    assert "optionTradeHistory" not in compact          # heavy list: full read only
    assert full["optionTradeHistory"] == ["heavy"]


def test_an_account_whose_broker_read_fails_is_never_reported_connected():
    state = make_state()
    first, second = two_accounts(state)
    state.dashboard_payload = lambda: {"optionAccounts": state._option_accounts_payload()}
    with running_api(state=state) as api:
        status, body = api.get("/api/dashboard?compact=true", headers=ADMIN)
    assert status == 200
    by_id = {account["id"]: account["status"] for account in body["optionAccounts"]}
    assert by_id[first]["connectionStatus"] == "Connected"
    assert by_id[second]["connectionStatus"] == "Error"
    assert by_id[second]["tradeableBuyingPower"] == 0.0
    assert "unauthorized" in by_id[second]["statusMessage"]
