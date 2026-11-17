"""Unit: open interest 1 to 12 months out for the Ask chat (2026-11-16).

Ganesh: "Nvda check the leap which one had more OI" got the Long Term page, then "I want any stocks 1 months to 12 months
which strikes have high oi with delta". Covers reading Schwab's chain, picking the strikes, the guard around the one
Schwab request (copy, failure memory, hourly cap, one at a time), the words that route here, and the reply.
Schwab is never reached: momx.long_oi.fetch is a fake in every test.
"""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import chat_ai, chat_market, chat_tools, long_oi

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 16, 23, 15, tzinfo=ET)
TODAY = NOW.date()


def _contract(strike, oi, delta, bid=1.0, ask=1.1, volume=0):
    return {"strikePrice": strike, "openInterest": oi, "delta": delta, "bid": bid, "ask": ask, "totalVolume": volume}


def _key(days):
    day = TODAY + timedelta(days=days)
    return f"{day.isoformat()}:{days}"


def chain(**extra):
    """Three expiries: 32 days (Dec), 95 days (Feb), 305 days (Sep), and one at 400 days that is out of the window."""
    calls = {
        _key(32): {"200.0": [_contract(200, 344959, 0.16)], "160.0": [_contract(160, 5000, 0.48)]},
        _key(95): {"185.0": [_contract(185, 108750, 0.31, 7.0, 7.25)], "250.0": [_contract(250, 75972, 0.08)],
                   "220.0": [_contract(220, 20000, -999.0)]},
        _key(305): {"200.0": [_contract(200, 63583, 0.32, volume=40)], "210.0": [_contract(210, 63583, 0.21, volume=90)]},
        _key(400): {"300.0": [_contract(300, 999999, 0.05)]},
    }
    puts = {
        _key(32): {"135.0": [_contract(135, 42497, -0.23)]},
        _key(95): {"120.0": [_contract(120, 69297, -0.14)], "0.0": [_contract(0, 50, -0.1)]},
        _key(305): {"140.0": [_contract(140, 97510, -0.32)], "150.0": [_contract(150, 0, -0.39)]},
    }
    return {"symbol": "NVDA", "status": "SUCCESS", "underlyingPrice": 155.6, "callExpDateMap": calls, "putExpDateMap": puts, **extra}


@pytest.fixture(autouse=True)
def clean():
    long_oi.reset()
    yield
    long_oi.reset()


@pytest.fixture
def fake(monkeypatch):
    asked: list = []

    def fetch(ticker, today):
        asked.append((ticker, today))
        return chain()
    monkeypatch.setattr(long_oi, "fetch", fetch)
    return asked


# ---------------------------------------------------------------- reading the chain

def test_rows_from_the_chain_carry_side_expiry_days_oi_and_delta():
    spot, rows = long_oi.rows_from_chain(chain(), TODAY)
    assert spot == 155.6
    big = next(r for r in rows if r["side"] == "CALL" and r["strike"] == 185)
    assert big == {"side": "CALL", "expiry": (TODAY + timedelta(days=95)).isoformat(), "days": 95, "strike": 185.0, "oi": 108750,
                   "delta": 0.31, "bid": 7.0, "ask": 7.25, "volume": 0}
    # Schwab's -999 "no greek" is no delta, never a number; a 0 strike is not a contract.
    assert next(r for r in rows if r["strike"] == 220)["delta"] is None
    assert not any(r["strike"] == 0 for r in rows)


@pytest.mark.parametrize("bad", [None, [], "x", {"callExpDateMap": "x"}, {"callExpDateMap": {"not-a-day": {}}}])
def test_a_broken_chain_reads_as_nothing(bad):
    assert long_oi.rows_from_chain(bad, TODAY) == (None, [])


def test_top_strikes_keeps_the_window_ranks_by_oi_and_breaks_ties_on_volume():
    _, rows = long_oi.rows_from_chain(chain(), TODAY)
    calls = long_oi.top_strikes(rows, side="CALL", min_days=28, max_days=366)
    assert [r["strike"] for r in calls][:3] == [200, 185, 250]           # the 344,959 one is Dec (32 days): inside 1-12 months
    assert all(r["days"] <= 366 for r in calls)                          # the 400-day 999,999 is out
    three_to_twelve = long_oi.top_strikes(rows, side="CALL", min_days=88, max_days=366)
    assert [r["strike"] for r in three_to_twelve][:2] == [185, 250]
    tie = [r for r in three_to_twelve if r["oi"] == 63583]
    assert [r["strike"] for r in tie] == [210, 200]                      # same OI: more volume first


def test_the_010_delta_floor_drops_far_away_lottery_strikes_and_rows_with_no_delta():
    """Ganesh 2026-11-16: "Don't give high oi delta 0.02 like that ... see our option chain entry": 0.10 at least."""
    _, rows = long_oi.rows_from_chain(chain(), TODAY)
    calls = long_oi.top_strikes(rows, side="CALL", min_days=88, max_days=366, min_delta=long_oi.MIN_DELTA)
    assert [r["strike"] for r in calls] == [185, 210, 200]                  # 250 (0.08) and 220 (no delta) are gone
    assert long_oi.MIN_DELTA == 0.10
    puts = long_oi.top_strikes(rows, side="PUT", min_days=88, max_days=366, min_delta=0.10)
    assert [r["strike"] for r in puts] == [140, 120]                          # -0.14 counts by its size
    exact = [{**rows[0], "side": "CALL", "days": 100, "oi": 5, "delta": 0.1}]
    assert long_oi.top_strikes(exact, side="CALL", min_days=0, max_days=999, min_delta=0.10)   # 0.10 itself stays


def test_entry_is_the_option_chains_entry_one_per_expiry_and_side():
    """Ganesh 2026-11-16 ("See the entry like that logic", his NVDA chain: ENTRY on the 172.5 call at 0.18, not the 175 at
    0.15 or the 180 at 0.10 with more OI): out of the money, 0 < |delta| < 0.20, closest to 0.20, ties nearest the price."""
    day = (TODAY + timedelta(days=4)).isoformat()

    def row(side, strike, delta, ask=1.0):
        return {"side": side, "expiry": day, "days": 4, "strike": strike, "oi": 100, "delta": delta, "bid": 0.9, "ask": ask, "volume": 0}
    rows = [row("CALL", 170, 0.22), row("CALL", 172.5, 0.18), row("CALL", 175, 0.15), row("CALL", 180, 0.10),
            row("CALL", 140, 0.19),                                     # in the money: never ENTRY
            row("PUT", 140, -0.19), row("PUT", 141, -0.19),             # a tie: the strike nearer the price
            row("PUT", 130, -0.199, ask=0.0)]                           # no price: never ENTRY
    rows[-1]["bid"] = 0.0
    long_oi.mark_entries(rows, 155.0)
    assert [(r["side"], r["strike"]) for r in rows if r["entry"]] == [("CALL", 172.5), ("PUT", 141)]
    assert long_oi.mark_entries(rows, None) and not any(r["entry"] for r in rows)   # no price: no ENTRY at all


def test_a_delta_filter_matches_puts_by_size_and_skips_rows_with_no_delta():
    _, rows = long_oi.rows_from_chain(chain(), TODAY)
    assert [r["strike"] for r in long_oi.top_strikes(rows, side="PUT", min_days=28, max_days=366, delta=0.2)] == [135]
    assert [r["strike"] for r in long_oi.top_strikes(rows, side="CALL", min_days=28, max_days=366, delta=0.2)] == [200, 210]
    assert all(r["strike"] != 220 for r in long_oi.top_strikes(rows, side="CALL", min_days=0, max_days=999, delta=0.5, band=1))
    # zero open interest is never listed
    assert all(r["oi"] > 0 for r in long_oi.top_strikes(rows, side="PUT", min_days=0, max_days=999))


# ---------------------------------------------------------------- the one Schwab request

def test_one_request_then_the_copy_in_memory_and_on_disk(tmp_path, fake):
    first = long_oi.chain_for("nvda", tmp_path, NOW)
    second = long_oi.chain_for("NVDA", tmp_path, NOW + timedelta(minutes=10))
    assert fake == [("NVDA", TODAY)] and first["cached"] is False and second["cached"] is True
    assert (tmp_path / "long_oi_cache" / "NVDA.json.gz").exists()
    long_oi.reset()                                                       # a restart: the disk copy answers
    assert long_oi.chain_for("NVDA", tmp_path, NOW + timedelta(minutes=20))["cached"] is True and len(fake) == 1
    long_oi.chain_for("NVDA", tmp_path, NOW + timedelta(minutes=31))      # older than 30 minutes: asked again
    assert len(fake) == 2


def test_a_failure_is_said_plainly_and_not_asked_again_for_five_minutes(tmp_path, monkeypatch):
    asked = []

    def broken(ticker, today):
        asked.append(ticker)
        raise ConnectionError("down")
    monkeypatch.setattr(long_oi, "fetch", broken)
    with pytest.raises(long_oi.Unavailable, match="Schwab did not answer for NVDA"):
        long_oi.chain_for("NVDA", tmp_path, NOW)
    with pytest.raises(long_oi.Unavailable, match="a few minutes ago"):
        long_oi.chain_for("NVDA", tmp_path, NOW)
    assert asked == ["NVDA"]


def test_an_empty_chain_says_there_are_no_options_that_far_out(tmp_path, monkeypatch):
    monkeypatch.setattr(long_oi, "fetch", lambda ticker, today: {"status": "FAILED"})
    with pytest.raises(long_oi.Unavailable, match="no options 1 to 12 months out for ZZZ"):
        long_oi.chain_for("ZZZ", tmp_path, NOW)


def test_the_hourly_cap_across_every_user(tmp_path, fake, monkeypatch):
    monkeypatch.setattr(long_oi, "FETCHES_PER_HOUR", 2)
    long_oi.chain_for("AAA", tmp_path, NOW)
    long_oi.chain_for("BBB", tmp_path, NOW)
    with pytest.raises(long_oi.Unavailable, match="many long-dated chains this hour"):
        long_oi.chain_for("CCC", tmp_path, NOW)
    assert [t for t, _ in fake] == ["AAA", "BBB"]
    assert long_oi.chain_for("AAA", tmp_path, NOW)["cached"] is True       # a copy still answers


def test_only_one_request_at_a_time(tmp_path, monkeypatch):
    monkeypatch.setattr(long_oi, "WAIT_SECONDS", 0.2)
    started, release = threading.Event(), threading.Event()

    def slow(ticker, today):
        started.set()
        release.wait(5)
        return chain()
    monkeypatch.setattr(long_oi, "fetch", slow)
    worker = threading.Thread(target=long_oi.chain_for, args=("AAA", tmp_path, NOW))
    worker.start()
    assert started.wait(5)
    with pytest.raises(long_oi.Unavailable, match="loading right now"):
        long_oi.chain_for("BBB", tmp_path, NOW)
    release.set()
    worker.join(5)


# ---------------------------------------------------------------- the words that route here

def _plan(question, page_ticker=None):
    ctx = {"scope": "owner", "now": NOW, "ticker": None, "pageTicker": page_ticker, "pageName": "Charts & OI" if page_ticker else None}
    plan = chat_ai.sure_calls(question, ctx, chat_tools.STATIC_TICKERS)
    return plan["calls"] if plan else None


def test_his_two_questions_route_to_the_long_dated_board():
    assert _plan("Nvda check the leap which one had more OI") == [
        {"tool": "long_oi", "args": {"ticker": "NVDA", "months_from": 3, "months_to": 12}}]
    assert _plan("I want any stocks 1 months to 12 months which strikes have high oi with delta", page_ticker="NVDA") == [
        {"tool": "long_oi", "args": {"ticker": "NVDA", "months_from": 1, "months_to": 12}}]


@pytest.mark.parametrize("question, args", [
    ("spy 0.20 delta high oi 3 to 6 months puts", {"ticker": "SPY", "months_from": 3, "months_to": 6, "delta": 0.2, "side": "bear"}),
    ("AAPL open interest six months calls delta .3", {"ticker": "AAPL", "months_from": 1, "months_to": 6, "delta": 0.3, "side": "bull"}),
    ("tsla 20 delta long dated oi", {"ticker": "TSLA", "months_from": 1, "months_to": 12, "delta": 0.2}),
    ("meta leaps open interest", {"ticker": "META", "months_from": 3, "months_to": 12}),
    ("amd oi 1 year", {"ticker": "AMD", "months_from": 1, "months_to": 12}),
])
def test_the_ways_to_ask(question, args):
    assert _plan(question) == [{"tool": "long_oi", "args": args}]


@pytest.mark.parametrize("question, tool", [
    ("nvda high oi", "walls"),                      # no months word: the short-dated High OI board, as before
    ("long term nvda", "outlook_long"),             # no OI word: the long-term view, as before
    ("tsla leap", "outlook_long"),
])
def test_questions_without_both_words_keep_their_old_answer(question, tool):
    assert _plan(question)[0]["tool"] == tool


def test_a_definition_is_not_this():
    plan = _plan("what is open interest on leaps")
    assert not plan or plan[0]["tool"] != "long_oi"


# ---------------------------------------------------------------- the reply

def _ask(args, scope="scanner", tmp=None):
    ctx = {"scope": scope, "account": "x@example.com", "now": NOW, "pageTicker": None, "ticker": None, "artifacts": str(tmp)}
    return chat_tools.run("long_oi", args, ctx)


def test_the_reply_names_the_biggest_call_and_put_with_delta_and_a_table(tmp_path, fake):
    out = _ask({"ticker": "NVDA", "months_from": 3, "months_to": 12}, tmp=tmp_path)
    text = "\n".join(out["lines"])
    assert "NVDA strikes with the most open interest, expiries 3 to 12 months out" in text
    assert "Most call OI: 185 call, Feb 19 2027 (95 days), OI 108,750, delta 0.31." in text
    assert "Most put OI: 140 put, Sep 17 2027 (305 days), OI 97,510, delta -0.32." in text
    assert "Facts only, not a pick." in text and "Only strikes with delta 0.10 or more" in text
    table = out["table"]
    assert [c["label"] for c in table["columns"]] == ["Contract", "Expiry", "Days", "OI", "Delta", "Bid", "Ask"]
    assert table["rows"][0] == {"contract": "185 Call", "expiry": (TODAY + timedelta(days=95)).isoformat(), "days": "95",
                                "oi": "108,750", "delta": "0.31", "bid": "7.00", "ask": "7.25"}
    # calls and puts take turns, so the first cards on the phone show both sides
    # 250 (delta 0.08) and 220 (no delta) are below the 0.10 floor and never shown.
    assert [r["contract"] for r in table["rows"]] == ["185 Call", "140 Put", "210 Call", "120 Put · ENTRY", "200 Call"]
    # The chain's ENTRY with the most OI: the Feb 120 put (-0.14). The Feb 250 call is that expiry's ENTRY too, but at
    # 0.08 it is under the 0.10 floor, so no ENTRY call line.
    assert "ENTRY put with the most OI (the option chain's ENTRY rule, delta just under 0.20): 120 put (ENTRY), Feb 19 2027" in text
    assert "ENTRY call with the most OI" not in text
    assert all(abs(float(r["delta"])) >= 0.10 for r in table["rows"])
    assert out["links"][0]["view"] == "Charts & OI" and out["asOf"] == "as of 23:15 ET"


def test_a_delta_just_under_020_never_reads_020():
    assert chat_market._delta_text(0.198) == "0.198" and chat_market._delta_text(-0.197) == "-0.197"
    assert chat_market._delta_text(0.31) == "0.31" and chat_market._delta_text(0.2) == "0.20" and chat_market._delta_text(0.18) == "0.18"


def test_one_side_and_a_delta_that_nothing_matches(tmp_path, fake):
    puts = _ask({"ticker": "NVDA", "side": "bear"}, tmp=tmp_path)
    assert all(" Put" in row["contract"] and " Call" not in row["contract"] for row in puts["table"]["rows"])
    assert "Most call OI" not in "\n".join(puts["lines"]) and "ENTRY call" not in "\n".join(puts["lines"])
    none = _ask({"ticker": "NVDA", "delta": 0.9, "side": "bull"}, tmp=tmp_path)
    assert "No calls, delta near 0.90 (+/- 0.05) with open interest" in "\n".join(none["lines"]) and none["table"] is None


def test_no_ticker_asks_for_one_and_schwab_trouble_is_one_plain_line(tmp_path, monkeypatch):
    assert "Which ticker" in _ask({}, tmp=tmp_path)["lines"][0]
    monkeypatch.setattr(long_oi, "fetch", lambda t, d: (_ for _ in ()).throw(TimeoutError()))
    out = _ask({"ticker": "NVDA"}, tmp=tmp_path)
    assert out["lines"] == ["Schwab did not answer for NVDA (TimeoutError). Try again in a few minutes."] and out["table"] is None


def test_offered_to_every_signed_in_session_and_refused_to_no_one_else(tmp_path, fake):
    assert "long_oi" in chat_tools.registry("owner") and "long_oi" in chat_tools.registry("scanner")
    assert chat_market.TOOLS["long_oi"]["handler"]({"ticker": "NVDA"}, {"scope": "guest"})["facts"] == {"refused": True}
    assert fake == []
