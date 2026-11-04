"""Integration: the Bot's option pricing routes through the real api_server
handler over HTTP.

/api/option-contract-quotes picks the bot's contract at the SIGNAL from the
chain already held (memory, else the disk chain cache - never a rebuild),
re-anchored on the live stock price, and prices it with one batched live
quote. /api/option-quotes prices the 15:50 closes from live bid/ask only.

The held chain is a scratch disk file and the live quote is a fake Schwab
client; only the test's own request threads may use it (STATE's background
collectors run in this process too).
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

ET = ZoneInfo("America/New_York")


def occ(symbol: str, expiry: str, side: str, strike: float) -> str:
    return f"{symbol:<6}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{side}{int(round(strike * 1000)):08d}"


EXPIRY = (datetime.now(ET).date() + timedelta(days=3)).isoformat()
C230, C235, C240 = (occ("AAPL", EXPIRY, "C", strike) for strike in (230, 235, 240))
P225 = occ("AAPL", EXPIRY, "P", 225)


def held_chain() -> dict:
    """What the browser's compact chain read left on disk (the chain's own
    price was 228; the signal fires at 231)."""
    def row(side, strike, delta, mark, contract):
        return {"symbol": contract, "side": side, "expiry": EXPIRY, "days_to_expiration": 99, "strike": strike,
                "delta": delta, "bid": round(mark - 0.05, 2), "ask": round(mark + 0.05, 2), "mark": mark,
                "volume": 100, "open_interest": 500}
    return {
        "symbol": "AAPL", "live": True, "optionQuoteSchemaVersion": 1, "underlyingPrice": 228.0,
        "scannedAt": datetime.now(ET).isoformat(),
        "selectedExpiryChainRows": [
            row("CALL", 230.0, 0.42, 2.10, C230),
            row("CALL", 235.0, 0.28, 0.95, C235),
            row("CALL", 240.0, 0.16, 0.40, C240),
            row("PUT", 225.0, 0.30, 1.20, P225),
        ],
    }


LIVE = {
    C235: {"bid": 1.30, "ask": 1.40, "mark": 1.35, "last_price": 1.31, "quote_time_ms": 1_762_272_000_000},
    C240: {"bid": 0.55, "ask": 0.60, "mark": 0.575, "last_price": 0.5},
    P225: {"bid": 0.80, "ask": 0.90, "mark": 0.85, "last_price": 0.82},
}


class FakeQuotes:
    def __init__(self, book):
        self.book = book
        self.asked: list[list[str]] = []

    def get_quotes(self, contracts):
        if "process_request_thread" not in threading.current_thread().name:
            raise RuntimeError("background collector refused by the test's fake broker")
        self.asked.append(list(contracts))
        return {contract: dict(self.book[contract]) for contract in contracts if contract in self.book}


@pytest.fixture
def held(monkeypatch, tmp_path):
    state = api_server.STATE
    disk = tmp_path / "oi_chain_cache"
    disk.mkdir()
    with gzip.open(disk / "AAPL.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(held_chain(), handle)
    monkeypatch.setattr(state, "oi_finder_chain_disk_cache_dir", disk)
    monkeypatch.setattr(state, "oi_finder_chain_cache", {})
    rebuilds: list = []
    monkeypatch.setattr(state, "_refresh_oi_finder_in_background", lambda *a, **k: rebuilds.append(a))
    quotes = FakeQuotes(LIVE)
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [quotes])
    quotes.rebuilds = rebuilds
    return quotes


def test_the_signal_entry_is_picked_from_the_held_chain_at_the_live_price(held):
    with running_api() as api:
        status, body = api.get("/api/option-contract-quotes?q=AAPL:C:231,TSLA:P:400")
    assert status == 200, body
    aapl, tsla = body["results"]
    # Re-anchored on 231 (the signal), not the chain's 228: first OTM call is 235, not 230.
    assert aapl["ok"] is True and aapl["bear"] is False
    assert (aapl["pick"]["contract"], aapl["pick"]["strike"], aapl["pick"]["expiry"]) == (C235, 235.0, EXPIRY)
    assert aapl["pick"]["entry"] == 1.35                       # the LIVE mid, not the chain's 0.95
    assert (aapl["pick"]["entryBid"], aapl["pick"]["entryAsk"]) == (1.3, 1.4)
    assert aapl["quoteTimeMs"] == 1_762_272_000_000
    assert aapl["e"]["eContract"] == C240 and aapl["e"]["eEntry"] == 0.575     # the 0.20-delta entry strike
    assert (tsla["symbol"], tsla["bear"], tsla["ok"], tsla["why"]) == ("TSLA", True, False, "no chain held")
    assert len(held.asked) == 1 and set(held.asked[0]) == {C235, C240}       # ONE batched quote call
    assert held.rebuilds == [], "pricing must never kick a chain rebuild"


def test_malformed_parts_are_skipped_or_answered_bad_request(held):
    with running_api() as api:
        status, body = api.get("/api/option-contract-quotes?q=AAPL:C,BAD!:C:100,AAPL:C:abc,AAPL:P:231")
    assert status == 200, body
    results = body["results"]
    assert [(r["symbol"], r.get("why")) for r in results[:2]] == [("BAD!", "bad request"), ("AAPL", "bad request")]
    put = results[2]
    assert put["ok"] is True and put["bear"] is True and put["pick"]["contract"] == P225


def test_no_live_quote_means_no_entry(held, monkeypatch):
    held.book = {}
    with running_api() as api:
        _, body = api.get("/api/option-contract-quotes?q=AAPL:C:231")
    assert body["results"][0]["ok"] is False and body["results"][0]["why"] == "no live quote"


def test_closing_quotes_use_the_live_bid_ask_mid_and_are_capped_at_400(held):
    many = [f"ZZ{index:04d}" for index in range(450)]
    held.book = {**LIVE, "STALEONLY": {"last_price": 3.2}}       # last trade but no market
    query = ",".join([C235.lower(), "STALEONLY"] + many)
    with running_api() as api:
        status, body = api.get(f"/api/option-quotes?symbols={quote(query, safe=',')}")
    assert status == 200, body
    assert len(held.asked) == 1 and len(held.asked[0]) <= 400
    assert body["quotes"][C235] == {"bid": 1.3, "ask": 1.4, "mid": 1.35, "last": 1.31}
    assert "STALEONLY" not in body["quotes"], "a close is never priced from a stale last trade"
