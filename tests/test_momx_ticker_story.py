"""Unit: the ticker story (momx/ticker_story.py, 2026-11-20): one answer per ticker in simple words, joining the LEAP OI
Scanner and AGX Flow. Every sentence is a number the boards already have; never a pick or a forecast."""
from __future__ import annotations

import json

from momx import ticker_story as ts

LEAP = {
    "tickers": [{"ticker": "INTC", "state": "ready", "spot": 54.6, "readAt": "2026-11-19T20:33:00-05:00",
                 "callOi": 345729, "putOi": 348270}],
    "rows": [
        {"ticker": "INTC", "side": "CALL", "strike": 50.0, "expiry": "2027-02-19", "oi": 73640, "oiChange": 120},
        {"ticker": "INTC", "side": "PUT", "strike": 80.0, "expiry": "2027-06-17", "oi": 48101, "oiChange": -2500},
        {"ticker": "INTC", "side": "PUT", "strike": 70.0, "expiry": "2027-06-17", "oi": 46000, "oiChange": 43513},
        {"ticker": "INTC", "side": "CALL", "strike": 100.0, "expiry": "2027-06-17", "oi": 49045, "oiChange": None},
    ],
    "excluded": [],
}
FLOW = {
    "tickers": [{"ticker": "INTC", "spot": 54.6, "callPremium": 21_500_000, "putPremium": 209_000_000, "unusualCount": 38,
                 "pending": False, "fetchedAt": "2026-11-19T16:04:00-05:00"}],
    "rows": [
        {"ticker": "INTC", "side": "PUT", "strike": 95.0, "expiry": "2026-12-18", "premium": 29_900_000, "fill": "mid"},
        {"ticker": "INTC", "side": "PUT", "strike": 60.0, "expiry": "2026-11-20", "premium": 12_000_000, "fill": "bid"},
        {"ticker": "INTC", "side": "CALL", "strike": 55.0, "expiry": "2026-11-20", "premium": 4_000_000, "fill": "ask"},
        {"ticker": "INTC", "side": "CALL", "strike": 57.0, "expiry": "2026-11-20", "premium": 1_000_000, "fill": "ask"},
    ],
    "excluded": [],
}


def texts(story, title):
    section = next(s for s in story["sections"] if s["title"] == title)
    return [f"{line['mark']}: {line['text']}" for line in section["lines"]]


def test_one_answer_joins_both_pages_in_simple_words():
    s = ts.build("intc", LEAP, FLOW)
    assert s["ticker"] == "INTC" and s["ready"] and not s["pending"]
    assert s["verdict"] == "Today more money went into Puts on INTC: 91% of it."
    assert [sec["title"] for sec in s["sections"]] == ["Price", "Today's bets", "Long bets (3 to 12 months)", "New since yesterday"]
    assert texts(s, "Price") == ["info: One share of INTC costs about 54.60 (at the last read)."]
    assert texts(s, "Today's bets") == [
        "info: Today about $22 million went into Calls (bets the price goes up) and $209 million into Puts (bets it goes down).",
        "yes: Puts got 91% of the money.",
        "info: Big bet: 95 Put ending Dec 18, 2026, $30 million; the last trade can't tell if a buyer or a seller led.",
        "info: Big bet: 60 Put ending Nov 20, 2026, $12 million; the last trade looked like a seller (took the bid price).",
        "info: Big bet: 55 Call ending Nov 20, 2026, $4.0 million; the last trade looked like a buyer (paid the asking price).",
        "yes: 38 of its contracts traded more today than were open before. That is unusual.",
    ]
    assert texts(s, "Long bets (3 to 12 months)") == [
        "info: On long bets (3 to 12 months away) people hold 345,729 Call contracts and 348,270 Put contracts on the biggest strikes: more Puts.",
        "info: The long Call most people hold: 50 Call ending Feb 19, 2027, 73,640 contracts.",
        "info: The long Put most people hold: 80 Put ending Jun 17, 2027, 48,101 contracts.",
    ]
    assert texts(s, "New since yesterday") == [
        "yes: 43,513 new contracts opened on the 70 Put ending Jun 17, 2027.",
        "no: 2,500 contracts closed on the 80 Put ending Jun 17, 2027.",
    ]
    assert "coin flip" in s["meaning"] and "insurance" in s["meaning"]
    assert "not advice" in s["notAdvice"] and "paper trading" in s["notAdvice"]


def test_even_money_no_trades_and_long_bets_only():
    even = {**FLOW, "tickers": [{**FLOW["tickers"][0], "callPremium": 100_000_000, "putPremium": 110_000_000}]}
    assert ts.build("INTC", LEAP, even)["verdict"] == "Today the money on INTC was split about evenly between Calls and Puts."
    quiet = {**FLOW, "rows": [], "tickers": [{**FLOW["tickers"][0], "callPremium": 0, "putPremium": 0, "unusualCount": 0}]}
    s = ts.build("INTC", LEAP, quiet)
    assert s["verdict"] == "On long bets, more people hold Puts on INTC."
    assert texts(s, "Today's bets") == ["info: No option trades in the app's latest read. Trades show here during market hours."]
    no_change = {**LEAP, "rows": [{**r, "oiChange": 10} for r in LEAP["rows"]]}
    assert texts(ts.build("INTC", no_change, quiet), "New since yesterday") == [
        "info: No long bet opened or closed 1,000 or more contracts since yesterday."]


def test_still_reading_and_left_off_the_boards():
    pending = ts.build("PLTR", {"tickers": [{"ticker": "PLTR", "state": "pending"}], "rows": [], "excluded": []},
                       {"tickers": [{"ticker": "PLTR", "pending": True}], "rows": [], "excluded": []})
    assert pending["pending"] and not pending["ready"] and pending["sections"] == []
    assert pending["verdict"] == "Reading PLTR... the card fills in when the option pages have read it."
    etf = ts.build("SPY", {"tickers": [], "rows": [], "excluded": [{"ticker": "SPY", "reason": "ETF"}]},
                   {"tickers": [], "rows": [], "excluded": [{"ticker": "SPY", "reason": "ETF"}]})
    assert etf["verdict"] == "SPY is not on these boards: ETF."


def test_nothing_says_what_to_buy_or_that_a_price_will_move():
    words = json.dumps(ts.build("INTC", LEAP, FLOW)).lower()
    for banned in ("you should", "will go up", "will rise", "will fall", "buy this", "sell this", "guarantee", "sure win"):
        assert banned not in words, banned


def test_money_words():
    assert ts.money(29_900_000) == "$30 million" and ts.money(4_000_000) == "$4.0 million"
    assert ts.money(273_600) == "$273,600" and ts.money(2_400_000_000) == "$2.4 billion"


def test_todays_bets_carries_the_which_strike_table():
    """2026-11-23 "$20 million went to calls which strike is that?" / "Above table I don't see in our app": the Today's
    bets part carries the contracts that took the most money on each side (from the LEAP board's todayBets)."""
    top_calls = [{"strike": 55.0, "expiry": "2026-11-20", "volume": 9000, "premium": 4_000_000, "share": 19, "fill": "ask"}]
    top_puts = [{"strike": 95.0, "expiry": "2026-12-18", "volume": 50000, "premium": 29_900_000, "share": 14, "fill": "mid"}]
    leap = {**LEAP, "byTicker": [{"ticker": "INTC", "todayBets": {"calls": 21_500_000, "puts": 209_000_000,
                                                                  "topCalls": top_calls, "topPuts": top_puts}}]}
    section = next(s for s in ts.build("INTC", leap, FLOW)["sections"] if s["title"] == "Today's bets")
    assert section["table"] == {"calls": top_calls, "puts": top_puts}
    plain = next(s for s in ts.build("INTC", LEAP, FLOW)["sections"] if s["title"] == "Today's bets")
    assert "table" not in plain                                    # no LEAP line yet: no table, not an empty one
