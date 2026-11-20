"""Unit: the bought-or-sold estimate (momx/oi_sides.py), Ganesh 2026-11-19 "I want OI, they are buyers or sellers".

The exchange does not say who opened a contract, so every number here is an estimate from where trades printed
between the bid and the ask while the app was watching, and the code must never claim more than it saw.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import oi_sides

ET = ZoneInfo("America/New_York")
THURSDAY_NOON = datetime(2026, 11, 19, 12, 0, tzinfo=ET)


def row(volume, last, oi=1000, side="CALL", strike=150, bid=1.00, ask=1.20, expiry="2026-11-20"):
    return {"side": side, "expiry": expiry, "strike": strike, "volume": volume, "open_interest": oi, "bid": bid, "ask": ask, "last": last}


@pytest.mark.parametrize("last,expected", [(1.20, "bought"), (1.15, "bought"), (1.25, "bought"), (1.00, "sold"), (1.05, "sold"),
                                           (0.95, "sold"), (1.10, "mid"), (1.08, "mid")])
def test_a_trade_near_the_ask_is_bought_near_the_bid_is_sold_and_the_middle_is_not_sorted(last, expected):
    assert oi_sides.side_of(last, 1.00, 1.20) == expected


@pytest.mark.parametrize("last,bid,ask", [(0, 1.0, 1.2), (1.1, 1.2, 1.2), (1.1, 1.3, 1.2), (None, 1.0, 1.2), ("x", 1.0, 1.2)])
def test_a_quote_that_cannot_be_used_sorts_nothing(last, bid, ask):
    assert oi_sides.side_of(last, bid, ask) is None


def test_volume_that_traded_before_the_first_read_is_never_sorted():
    state = oi_sides.step(None, [row(5000, 1.20)], "2026-11-19")
    entry = state["contracts"]["C|2026-11-20|150"]
    assert (entry["v"], entry["b"], entry["s"], entry["u"]) == (5000, 0, 0, 5000)
    assert oi_sides.read(entry) is None                      # nothing was watched yet


def test_volume_added_between_reads_is_sorted_by_where_the_last_trade_printed():
    state = oi_sides.step(None, [row(1000, 1.10)], "2026-11-19")
    state = oi_sides.step(state, [row(1600, 1.20)], "2026-11-19")      # +600 at the ask
    state = oi_sides.step(state, [row(1800, 1.00)], "2026-11-19")      # +200 at the bid
    state = oi_sides.step(state, [row(1900, 1.10)], "2026-11-19")      # +100 in the middle
    state = oi_sides.step(state, [row(1900, 1.20)], "2026-11-19")      # no new volume: nothing added
    state = oi_sides.step(state, [row(1500, 1.20)], "2026-11-19")      # a lower count is bad data: ignored
    entry = state["contracts"]["C|2026-11-20|150"]
    assert (entry["v"], entry["b"], entry["s"], entry["m"], entry["u"]) == (1900, 600, 200, 100, 1000)
    est = oi_sides.read(entry)
    assert est["label"] == "mostly bought" and est["share"] == 0.75 and est["volume"] == 1900
    assert oi_sides.text("150 call", est) == "150 call: mostly bought, 75% bought of 800 sorted contracts (1,900 traded)."


def test_mostly_sold_and_mixed_read_plainly():
    sold = oi_sides.read({"v": 1000, "b": 100, "s": 400, "m": 0, "u": 0, "oi": 10})
    mixed = oi_sides.read({"v": 1000, "b": 260, "s": 240, "m": 0, "u": 0, "oi": 10})
    assert sold["label"] == "mostly sold" and mixed["label"] == "mixed"
    assert oi_sides.text("140 put", sold) == "140 put: mostly sold, 80% sold of 500 sorted contracts (1,000 traded)."


@pytest.mark.parametrize("entry", [{"v": 1000, "b": 60, "s": 30, "m": 0, "u": 0},          # under 100 sorted contracts
                                   {"v": 10000, "b": 900, "s": 600, "m": 0, "u": 8500}])   # under a quarter of the volume
def test_too_few_sorted_contracts_give_no_read(entry):
    assert oi_sides.read(entry) is None


def test_the_next_session_says_whether_open_interest_went_up_or_down():
    state = oi_sides.step(None, [row(4000, 1.2, oi=10_000)], "2026-11-18")
    state = oi_sides.step(state, [row(300, 1.2, oi=12_500)], "2026-11-19")
    entry = state["contracts"]["C|2026-11-20|150"]
    assert (entry["v"], entry["b"], entry["u"], entry["poi"], entry["pday"]) == (300, 0, 300, 10_000, "2026-11-18")
    est = oi_sides.read(entry)
    assert est["label"] is None and est["oiChange"] == 2500 and est["oiSince"] == "2026-11-18"
    assert oi_sides.text("150 call", est) == "150 call: open interest up 2,500 since 2026-11-18."


def test_calls_and_puts_expiries_and_strikes_never_mix():
    state = oi_sides.step(None, [row(100, 1.1), row(100, 1.1, side="PUT"), row(100, 1.1, strike=152.5), row(100, 1.1, expiry="2026-11-27")], "2026-11-19")
    assert sorted(state["contracts"]) == ["C|2026-11-20|150", "C|2026-11-20|152.5", "C|2026-11-27|150", "P|2026-11-20|150"]


def test_a_read_in_the_session_is_recorded_saved_and_put_on_the_rows(tmp_path):
    oi_sides.reset()
    oi_sides.annotate("NVDA", [row(1000, 1.10)], THURSDAY_NOON, root=tmp_path)
    rows = [row(1800, 1.20)]
    oi_sides.annotate("NVDA", rows, THURSDAY_NOON.replace(minute=5), root=tmp_path)
    assert rows[0]["sideEst"]["label"] == "mostly bought" and rows[0]["sideEst"]["day"] == "2026-11-19"
    oi_sides.reset()                                          # an app restart reads the saved file
    assert oi_sides.load("NVDA", tmp_path)["contracts"]["C|2026-11-20|150"]["b"] == 800


@pytest.mark.parametrize("when", [datetime(2026, 11, 21, 12, 0, tzinfo=ET), datetime(2026, 11, 19, 20, 0, tzinfo=ET),
                                  datetime(2026, 11, 20, 8, 0, tzinfo=ET)], ids=["saturday", "after the close", "before the open"])
def test_a_read_outside_the_session_records_nothing_and_keeps_the_last_estimate(tmp_path, when):
    oi_sides.reset()
    oi_sides.annotate("NVDA", [row(1000, 1.10)], THURSDAY_NOON, root=tmp_path)
    oi_sides.annotate("NVDA", [row(1800, 1.20)], THURSDAY_NOON.replace(minute=5), root=tmp_path)
    rows = [row(9999, 1.00)]                                  # a repeat or stale read
    oi_sides.annotate("NVDA", rows, when, root=tmp_path)
    assert rows[0]["sideEst"]["label"] == "mostly bought" and rows[0]["sideEst"]["volume"] == 1800
    assert oi_sides.load("NVDA", tmp_path)["day"] == "2026-11-19"
