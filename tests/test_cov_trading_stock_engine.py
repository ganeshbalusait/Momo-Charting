"""Unit: stock paper trading risk settings, account switch and bot state.

DashboardState methods on a light test state; settings.trading is restored
after every test. No broker call is possible: the trader is a recorder.
"""
from __future__ import annotations

import pytest

from config import settings
from test_option_auto_engine import make_state

RISK_FIELDS = ("daily_trade_amount", "fixed_trade_amount", "stop_loss_percent", "stop_loss_amount", "take_profit_1_pct")


@pytest.fixture(autouse=True)
def _restore(monkeypatch):
    for field in RISK_FIELDS:
        monkeypatch.setattr(settings.trading, field, getattr(settings.trading, field))


class Recorder:
    def __init__(self):
        self.calls = []

    def close_position(self, symbol):
        self.calls.append(symbol)
        return {"message": f"Close order submitted for {symbol}."}


def state():
    s = make_state()
    s.trader = Recorder()
    s.client.mode = "paper"
    s.active_profile_id = "default-paper"
    s.scheduler_enabled = False
    s.scheduler_thread = None
    return s


def test_the_stop_dollar_amount_is_used_only_when_no_stop_percent_is_given():
    s = state()
    s.update_risk_settings(trade_amount=500, stop_loss_percent=2)
    assert (settings.trading.stop_loss_percent, settings.trading.stop_loss_amount) == (2.0, 10.0)
    s.update_risk_settings(stop_loss_amount=25)                      # % absent: $ sets %
    assert (settings.trading.stop_loss_percent, settings.trading.stop_loss_amount) == (5.0, 25.0)
    s.update_risk_settings(stop_loss_percent=1, stop_loss_amount=99)  # both: % wins, $ kept as typed
    assert settings.trading.stop_loss_percent == 1.0
    assert settings.trading.stop_loss_amount == 99.0


def test_minimums_protect_the_sizing_from_zero_and_negative_input():
    s = state()
    s.update_risk_settings(daily_trade_amount=0, trade_amount=0, stop_loss_percent=-3, first_profit_target_percent=0)
    assert settings.trading.daily_trade_amount == 1.0
    assert settings.trading.fixed_trade_amount == 1.0
    assert settings.trading.stop_loss_percent == 0.01
    assert settings.trading.take_profit_1_pct == 0.01
    assert settings.trading.stop_loss_amount == 0.0  # 1.0 x 0.01% rounds to $0.00 - the floor is on the %


def test_an_unknown_profile_is_an_error_payload_and_nothing_switches():
    s = state()
    assert s.select_account("not-a-profile") == {"error": "Unknown account profile: not-a-profile"}
    assert s.active_profile_id == "default-paper"
    assert not [e for e in s.repository.events if e and e[0] == "account_switch"]


def test_running_on_an_options_only_account_ends_stopped_and_logs_why():
    s = state()
    s.active_profile_id = settings.option_account_profile_id("paper")
    payload = s.set_bot_state("Running")
    assert payload["botState"] == "Stopped" and s.bot_state == "Stopped"
    assert s.scheduler_enabled is False and s.scheduler_thread is None
    assert [e[0] for e in s.repository.events if e and e[0].startswith("bot_state")] == ["bot_state_blocked"]


def test_a_manual_close_is_logged_and_the_traders_message_is_surfaced():
    s = state()
    result = s.close_position("AAPL")
    assert s.trader.calls == ["AAPL"]
    assert result["result"]["message"] == "Close order submitted for AAPL."
    assert s.action_message == "Close order submitted for AAPL."
    assert ("manual_close_request", "Close order submitted for AAPL.") == s.repository.events[-1][:2]
