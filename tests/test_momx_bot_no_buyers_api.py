"""Integration: a BOT signal whose option has no buyers, through the real route.

The real api_server handler answers /api/option-contract-quotes over HTTP from a
held chain on (scratch) disk and a fake Schwab quote; the real OptionTrackBook
reads that answer exactly as the scanner worker does. Bug 2 of the 2026-11-24
loss audit: a $0 bid was priced at Schwab's mark and "bought", and the
0.20-delta contract was picked off a delta Schwab made up for a strike with no
buyers (EOSE, stock $1.24: the 4.5 call "0.167 delta").

Round 2 (2026-11-25): the LIVE quote decides which strike under 0.20 delta is
tradable (bid above $0, ask no more than 3x the bid), never the held chain's old
bid / ask, and every cause has its own reason. Every test here fails on the code
before the fix.
"""
from __future__ import annotations

import gzip
import json
import threading
from datetime import datetime, timedelta
from urllib.parse import quote
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api
from momx import option_track as ot
from momx import paper_mirror as pm

ET = ZoneInfo("America/New_York")
EXPIRY = (datetime.now(ET).date() + timedelta(days=3)).isoformat()
MON = datetime(2026, 9, 28, 9, 50, tzinfo=ET)


def occ(symbol: str, side: str, strike: float) -> str:
    return f"{symbol:<6}{EXPIRY[2:4]}{EXPIRY[5:7]}{EXPIRY[8:10]}{side}{int(round(strike * 1000)):08d}"


def row(symbol, strike, delta, bid, ask, side="CALL"):
    return {"symbol": occ(symbol, side[0], strike), "side": side, "expiry": EXPIRY, "days_to_expiration": 99,
            "strike": strike, "delta": delta, "bid": bid, "ask": ask, "mark": round((bid + ask) / 2, 4),
            "volume": 10, "open_interest": 100}


def chain(symbol, spot, rows):
    return {"symbol": symbol, "live": True, "optionQuoteSchemaVersion": 1, "underlyingPrice": spot,
            "scannedAt": datetime.now(ET).isoformat(), "selectedExpiryChainRows": rows}


#: The EOSE chain saved on 2026-11-24 (stock $1.24): nobody bids for any OTM call, and the
#: 4.5 and 6.0 strikes read a delta near 0.16 only because their ask is $0.14.
EOSE = chain("EOSE", 1.24, [row("EOSE", 1.0, 0.71, 0.24, 0.26), row("EOSE", 1.5, 0.076, 0.0, 0.01),
                            row("EOSE", 2.0, 0.042, 0.0, 0.01), row("EOSE", 3.0, 0.029, 0.0, 0.01),
                            row("EOSE", 4.0, 0.024, 0.0, 0.01), row("EOSE", 4.5, 0.167, 0.0, 0.14),
                            row("EOSE", 6.0, 0.156, 0.0, 0.14)])
#: A healthy chain with one far strike whose delta Schwab made up (no buyers, wide ask).
AAPL = chain("AAPL", 228.0, [row("AAPL", 235.0, 0.28, 0.9, 1.0), row("AAPL", 240.0, 0.16, 0.35, 0.45),
                             row("AAPL", 245.0, 0.07, 0.1, 0.15), row("AAPL", 300.0, 0.19, 0.0, 0.9)])
#: Held chain shows a $0 bid on the real 0.20-delta strike (410) and a bid on the 415.
MSFT = chain("MSFT", 400.0, [row("MSFT", 405.0, 0.35, 2.0, 2.2), row("MSFT", 410.0, 0.17, 0.0, 0.9),
                             row("MSFT", 415.0, 0.08, 0.3, 0.35)])
#: No strike under 0.20 delta on the expiry.
KO = chain("KO", 60.0, [row("KO", 61.0, 0.4, 0.5, 0.6), row("KO", 62.0, 0.25, 0.2, 0.25)])
#: Six strikes under 0.20 delta: only the first few are quoted live.
WIDE = chain("WIDE", 50.0, [row("WIDE", 51.0, 0.4, 0.5, 0.6)] + [
    row("WIDE", 53.0 + i, round(0.19 - 0.03 * i, 2), 0.1, 0.15) for i in range(6)])
E15, A235, A240, A300 = occ("EOSE", "C", 1.5), occ("AAPL", "C", 235), occ("AAPL", "C", 240), occ("AAPL", "C", 300)
A245, M405, M410, M415 = occ("AAPL", "C", 245), occ("MSFT", "C", 405), occ("MSFT", "C", 410), occ("MSFT", "C", 415)
K61, K62 = occ("KO", "C", 61), occ("KO", "C", 62)


class FakeQuotes:
    def __init__(self, book):
        self.book = book
        self.asked: list[list[str]] = []

    def get_quotes(self, contracts):
        if "process_request_thread" not in threading.current_thread().name:
            raise RuntimeError("background collector refused by the test's fake broker")
        self.asked.append(list(contracts))
        return {c: dict(self.book[c]) for c in contracts if c in self.book}


@pytest.fixture
def held(monkeypatch, tmp_path):
    state = api_server.STATE
    disk = tmp_path / "oi_chain_cache"
    disk.mkdir()
    for doc in (EOSE, AAPL, MSFT, KO, WIDE):
        with gzip.open(disk / f"{doc['symbol']}.json.gz", "wt", encoding="utf-8") as handle:
            json.dump(doc, handle)
    monkeypatch.setattr(state, "oi_finder_chain_disk_cache_dir", disk)
    monkeypatch.setattr(state, "oi_finder_chain_cache", {})
    monkeypatch.setattr(state, "_refresh_oi_finder_in_background", lambda *a, **k: None)
    quotes = FakeQuotes({})
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [quotes])
    return quotes


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def worker_book(tmp_path, api, clock, symbol, price):
    """The scanner worker's book, asking the real route for its entry quote."""
    def quote_fetch(query):
        status, body = api.get("/api/option-contract-quotes?q=" + quote(query, safe=":,.-"))
        assert status == 200, body
        return body
    b = ot.OptionTrackBook(tmp_path / "book", direction="bull", fetch=lambda s: None, healthy=lambda: True,
                           background=False, clock=clock, sleep=lambda s: None, quote_fetch=quote_fetch)
    b._day = MON.date().isoformat()
    b._trades = [{"rule": "level1", "symbol": symbol, "at": MON.isoformat(), "status": "pending", "stockPrice": price}]
    return b


def ask_route(api, query):
    status, body = api.get("/api/option-contract-quotes?q=" + query)
    assert status == 200, body
    return body["results"][0]


def test_a_zero_bid_is_answered_as_no_buyers_and_the_bot_records_a_skip_not_a_trade(held, tmp_path):
    held.book = {E15: {"bid": 0.0, "ask": 0.01, "mark": 0.005, "last_price": 0.01}}
    clock = Clock(MON.timestamp())
    with running_api() as api:
        res = ask_route(api, "EOSE:C:1.24")
        # Before: ok with entry 0.005 (Schwab's mark) and the 4.5 call as the "0.20-delta" contract.
        assert (res["ok"], res["why"], res["noQuoteSide"]) == (False, "no buyers", "buyers")
        assert res["pick"]["contract"] == E15 and res["e"] is None
        book = worker_book(tmp_path, api, clock, "EOSE", 1.24)
        book._fast_price(["EOSE"])
    t = book.trades()[0]
    assert t["status"] == "pending" and "entry" not in t and t["why"] == ot.NO_BUYERS_WAIT
    clock.t += 601
    book._price_symbol("EOSE", False)
    t = book.trades()[0]
    assert t["status"] == "no_contract" and "entry" not in t and "eContract" not in t
    c = book.capture_status()
    # Checked once, so it says once (round 1 claimed "in the 10 minutes after the signal").
    assert (c["signals"], c["entered"], c["notEntered"], c["reasons"]) == (1, 0, 1, {ot.NO_BUYERS_ONCE: 1})
    # The paper mirror buys only what the BOT holds open: nothing here.
    assert pm.eligible(t, clock.t, None, "entry") is False and pm.still_open(t, "main") is False
    # The save file keeps the skip, so a restart still counts it.
    book._save(force=True)
    saved = json.loads((tmp_path / "book" / ot.DIRNAME / f"{book._day}.json").read_text(encoding="utf-8"))
    assert [(x["status"], x["why"]) for x in saved["trades"]] == [("no_contract", ot.NO_BUYERS_ONCE)]


def test_buyers_appearing_inside_the_window_open_the_trade_at_the_real_live_quote(held, tmp_path):
    """The missing round-1 test: no buyers at the signal, buyers two minutes later."""
    held.book = {A235: {"bid": 0.0, "ask": 1.4, "mark": 0.7}, A240: {"bid": 0.55, "ask": 0.6}}
    clock = Clock(MON.timestamp())
    with running_api() as api:
        book = worker_book(tmp_path, api, clock, "AAPL", 231.0)
        book._fast_price(["AAPL"])
        t = book.trades()[0]
        assert t["status"] == "pending" and "entry" not in t and t["why"] == ot.NO_BUYERS_WAIT
        held.book[A235] = {"bid": 1.3, "ask": 1.4}
        clock.t += 120
        book._fast_price(["AAPL"])
    t = book.trades()[0]
    assert (t["status"], t["contract"], t["entry"], t["entryBid"], t["entryAsk"]) == ("open", A235, 1.35, 1.3, 1.4)
    assert t["entryAt"] == datetime.fromtimestamp(clock.t, ET).replace(microsecond=0).isoformat()     # not backdated
    assert "why" not in t and "quoteGap" not in t and t["eContract"] == A240
    c = book.capture_status()
    assert (c["entered"], c["notEntered"], c["reasons"]) == (1, 0, {})


def test_the_live_quote_not_the_held_chain_decides_the_020_delta_contract(held, tmp_path):
    """Decision 1. The held MSFT chain shows a $0 bid on the 410 call (0.17 delta) and a
    bid on the 415 (0.08). Round 1 tested THOSE old prices: it threw the 410 away."""
    held.book = {M405: {"bid": 2.0, "ask": 2.2}, M410: {"bid": 0.8, "ask": 0.9}, M415: {"bid": 0.3, "ask": 0.35}}
    with running_api() as api:
        res = ask_route(api, "MSFT:C:400")
        # Live buyers on the 410: it is the pick although the held chain showed bid $0.
        assert res["ok"] is True and (res["e"]["eContract"], res["e"]["eDelta"]) == (M410, 0.17)
        assert (res["e"]["eEntry"], res["e"]["eEntryBid"], res["e"]["eEntryAsk"]) == (0.85, 0.8, 0.9)
        assert "eGap" not in res
        # ...and a strike whose OLD chain showed a bid does not pass when nobody bids live.
        held.book = {M405: {"bid": 2.0, "ask": 2.2}, M410: {"bid": 0.0, "ask": 0.9}, M415: {"bid": 0.0, "ask": 0.05}}
        res = ask_route(api, "MSFT:C:400")
        assert res["ok"] is True and res["e"] is None and res["eGap"] == "buyers"
        book = worker_book(tmp_path, api, Clock(MON.timestamp()), "MSFT", 400.0)
        book._fast_price(["MSFT"])
    t = book.trades()[0]
    assert t["status"] == "open" and t["eStatus"] == "none" and "eContract" not in t
    assert (t["eGap"], t["eNote"]) == ("buyers", ot.ENTRY_LEG_NOTE["buyers"])
    assert book.capture_status()["entryLeg"]["buyers"] == 1
    assert pm.eligible(t, MON.timestamp() + 5, None, "entry") is False


def test_a_made_up_delta_far_from_the_stock_is_rejected_on_its_live_quote(held, tmp_path):
    """Changed on purpose from round 1, which asserted the 300 call was never quoted
    (it was dropped on the HELD chain's $0 bid). Now the live quote decides: the 300
    call IS quoted, has no buyers, and the 240 call is the pick."""
    held.book = {A235: {"bid": 1.3, "ask": 1.4}, A240: {"bid": 0.55, "ask": 0.6}, A300: {"bid": 0.0, "ask": 0.9}}
    with running_api() as api:
        res = ask_route(api, "AAPL:C:231")
        assert res["ok"] is True and res["pick"]["contract"] == A235 and res["pick"]["entry"] == 1.35
        assert (res["e"]["eContract"], res["e"]["eDelta"]) == (A240, 0.16)      # not the 300 call's 0.19
        assert A300 in sum(held.asked, [])
        book = worker_book(tmp_path, api, Clock(MON.timestamp()), "AAPL", 231.0)
        book._fast_price(["AAPL"])
    t = book.trades()[0]
    assert (t["status"], t["contract"], t["entry"], t["eContract"], t["eEntryBid"]) == ("open", A235, 1.35, A240, 0.55)
    assert "eGap" not in t and "eNote" not in t
    assert pm.eligible(t, MON.timestamp() + 5, None, "entry") is True      # a real quote: paper may buy it


@pytest.mark.parametrize("quotes,gap", [
    ({A240: {"bid": 0.0, "ask": 0.6, "mark": 0.3}, A245: {"bid": 0.0, "ask": 0.1}, A300: {"bid": 0.0, "ask": 0.9}}, "buyers"),
    ({A240: {"bid": 0.01, "ask": 0.71}, A245: {"bid": 0.0, "ask": 0.1}, A300: {"bid": 0.0, "ask": 0.9}}, "unusable"),
    ({A240: {"bid": 0.4, "ask": 0.0}, A300: {"bid": 0.0, "ask": 0.9}}, "sellers"),
    ({A240: {"ask": 0.6}}, "missing"),          # a quote with no bid number, the others not quoted at all
    ({}, "missing"),
])
def test_each_cause_of_no_020_delta_contract_is_named_and_counted_separately(held, tmp_path, quotes, gap):
    held.book = {A235: {"bid": 1.3, "ask": 1.4}, **quotes}
    with running_api() as api:
        res = ask_route(api, "AAPL:C:231")
        assert res["ok"] is True and res["e"] is None and res["eGap"] == gap
        book = worker_book(tmp_path, api, Clock(MON.timestamp()), "AAPL", 231.0)
        book._fast_price(["AAPL"])
    t = book.trades()[0]
    assert t["status"] == "open" and t["eStatus"] == "none" and (t["eGap"], t["eNote"]) == (gap, ot.ENTRY_LEG_NOTE[gap])
    counts = book.capture_status()["entryLeg"]
    assert {k: n for k, n in counts.items() if n} == {gap: 1}
    assert pm.eligible(t, MON.timestamp() + 5, None, "entry") is False


def test_no_strike_under_020_delta_on_the_expiry_is_its_own_reason(held, tmp_path):
    held.book = {K61: {"bid": 0.5, "ask": 0.6}, K62: {"bid": 0.2, "ask": 0.25}}
    with running_api() as api:
        res = ask_route(api, "KO:C:60")
        assert res["ok"] is True and res["e"] is None and res["eGap"] == "none"
        book = worker_book(tmp_path, api, Clock(MON.timestamp()), "KO", 60.0)
        book._fast_price(["KO"])
    t = book.trades()[0]
    assert t["status"] == "open" and t["eNote"] == ot.ENTRY_LEG_NOTE["none"] and "bid" not in t["eNote"]
    assert book.capture_status()["entryLeg"] == {"none": 1, "missing": 0, "buyers": 0, "sellers": 0, "unusable": 0}


def test_only_the_first_few_strikes_under_020_delta_are_quoted_live(held):
    held.book = {occ("WIDE", "C", 51): {"bid": 0.5, "ask": 0.6}}
    with running_api() as api:
        res = ask_route(api, "WIDE:C:50")
    asked = set(sum(held.asked, []))
    under = [occ("WIDE", "C", s) for s in (53, 54, 55, 56, 57, 58)]     # deltas 0.19 falling to 0.04
    assert [c in asked for c in under] == [True] * ot.ENTRY_LIVE_CANDIDATES + [False] * (6 - ot.ENTRY_LIVE_CANDIDATES)
    assert res["ok"] is True and res["e"] is None and res["eGap"] == "missing"


@pytest.mark.parametrize("quote,side,why,final", [
    (None, "missing", "no live quote", "No quote for this option when it was checked: no trade taken"),
    ({"ask": 1.4}, "missing", "no live quote", "No quote for this option when it was checked: no trade taken"),
    ({"bid": 1.3, "ask": 0.0}, "sellers", "no sellers", "No sellers when it was checked (no ask): no trade taken"),
    ({"bid": 1.5, "ask": 1.4}, "unusable", "quote not usable",
     "Quote not usable when it was checked (buyers priced above sellers): no trade taken"),
])
def test_a_main_contract_that_cannot_be_traded_says_which_way(held, tmp_path, quote, side, why, final):
    """A contract Schwab did not quote, or quoted with no bid number, is "no quote":
    round 1 answered a missing bid as "no buyers (bid $0)"."""
    held.book = {A235: quote} if quote else {}
    clock = Clock(MON.timestamp())
    with running_api() as api:
        res = ask_route(api, "AAPL:C:231")
        assert (res["ok"], res["why"], res["noQuoteSide"]) == (False, why, side)
        book = worker_book(tmp_path, api, clock, "AAPL", 231.0)
        book._fast_price(["AAPL"])
    t = book.trades()[0]
    assert t["status"] == "pending" and t["why"] == ot.QUOTE_GAP_WAIT[side]
    clock.t += 601
    book._price_symbol("AAPL", False)
    assert book.capture_status()["reasons"] == {final: 1}
    assert "$0" not in final
