"""Unit: the option paper engine's settings, bot state and close guards.

Driven on the option-engine test state (tests/test_option_auto_engine.py
make_state): a fake Alpaca option client that records orders and a fake
journal. Covers the edge cases behind the Option Paper Trading buttons.
"""
from __future__ import annotations

import threading
import types

import pytest

from test_option_auto_engine import make_state, open_pltr_row

PLTR = "PLTR260702C00129000"


@pytest.mark.parametrize("given,expected", [
    ("momx", "momx"), ("MomX-Scanner", "momx"), ("momx_scanner", "momx"),
    ("mag7", "mag7"), ("Mag7_Watchlist_Options", "mag7"), ("mag7-options", "mag7"),
    ("option", "option"), ("", "option"), ("something-new", "option"),
])
def test_the_watchlist_source_normalises_to_one_of_three_books(given, expected):
    state = make_state()
    state.update_option_bot_config(watchlist_source=given)
    assert state.option_bot_config["watchlistSource"] == expected


def test_omitted_fields_keep_their_saved_values_and_the_policy_is_always_long_calls():
    state = make_state()
    state.update_option_bot_config(approval_mode="human", spread_filter="10%", watchlist_source="mag7")
    state.update_option_bot_config(contract_policy="naked puts")
    config = state.option_bot_config
    assert (config["approvalMode"], config["spreadFilter"], config["watchlistSource"]) == ("human", "10%", "mag7")
    assert config["contractPolicy"] == "only_long_call"


def test_pause_and_stop_persist_without_starting_the_engine():
    state = make_state()
    starts = []
    state._start_option_scheduler = lambda: starts.append(1)
    for value, message in (("Paused", "Option bot is paused."), ("Stopped", "Option bot is stopped.")):
        state.set_option_bot_state(value)
        assert state.option_bot_state == value
        assert state.option_bot_message.startswith(message)
        assert ("setting", "option_bot_state", value) in state.repository.events
    assert starts == []


def test_pressing_start_twice_runs_one_engine_thread():
    state = make_state()
    state.option_scheduler_thread = None
    release = threading.Event()
    runs = []

    def engine_loop():
        runs.append(threading.current_thread())
        release.wait(10)

    state._option_scheduler_loop = engine_loop
    try:
        state.set_option_bot_state("Running")
        first = state.option_scheduler_thread
        state.set_option_bot_state("Running")
        assert state.option_scheduler_thread is first and first.is_alive()
    finally:
        release.set()
        first.join(timeout=5)
    assert len(runs) == 1


# ------------------------------------------------------------ close guards

def pltr_state(mid=0.75):
    return make_state(rows=[open_pltr_row(make_state())], mid=mid)


def test_a_pending_sell_blocks_a_second_close_of_the_same_contract():
    state = pltr_state()
    snapshot = state._option_broker_snapshot()
    snapshot["open_sell_symbols"] = {PLTR}
    result = state._close_option_position_result(PLTR, snapshot=snapshot)
    assert result == {"status": "blocked", "message": f"Sell-to-close is already pending for {PLTR}."}
    assert state.option_client.orders == []


def test_a_zero_mark_falls_back_to_the_average_entry_price():
    state = make_state()
    state.option_client.positions[PLTR] = types.SimpleNamespace(
        symbol=PLTR, qty="2", avg_entry_price="0.40", current_price="0", unrealized_pl="0", unrealized_intraday_pl="0", asset_class="us_option")
    state.option_client.price_lookup = lambda symbol: None
    result = state._close_option_position_result(PLTR)
    assert result["status"] == "submitted" and result["journalStatus"] == "broker_only"
    sell = state.option_client.orders[-1]
    assert (sell.side, sell.qty, sell.limit_price) == ("sell", "2", 0.4)


def test_with_no_mark_and_no_entry_price_nothing_is_sent():
    state = make_state()
    state.option_client.positions[PLTR] = types.SimpleNamespace(
        symbol=PLTR, qty="2", avg_entry_price="0", current_price="0", unrealized_pl="0", unrealized_intraday_pl="0", asset_class="us_option")
    state.option_client.price_lookup = lambda symbol: None
    result = state._close_option_position_result(PLTR)
    assert result == {"status": "error", "message": f"Current option mark is unavailable for {PLTR}."}
    assert state.option_client.orders == []


def test_an_unreachable_broker_is_reported_and_nothing_is_sent():
    state = pltr_state()

    def down():
        raise ConnectionError("alpaca down")

    state._option_broker_snapshot = down
    result = state._close_option_position_result(PLTR)
    assert result["status"] == "error"
    assert result["message"] == "Unable to reach Alpaca option account right now: alpaca down"
    assert state.option_client.orders == []
