"""Unit: today's trades on each LEAP OI Scanner row (2026-11-20, "If I see leap oi scanner like INTC I don't know
anything like buyers or seller ... then I have to search agx flow ... I feel little confusion").

The LEAP row gets the SAME contract's numbers from AGX Flow's latest read, so the two pages agree and the trader does
not have to switch pages. AGX Flow now reads 12 months out, and a copy read over the old 45-day window is stale.
"""
from __future__ import annotations

import gzip
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import agx_flow
from momx import leap_oi_scanner as scanner

ET = ZoneInfo("America/New_York")
AT = "2026-11-19T16:04:42-05:00"


def _flow_copy(root, ticker, contracts, max_days=agx_flow.FETCH_MAX_DAYS):
    path = root / "agx_flow_cache" / f"{ticker}.json.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"schema": agx_flow.SCHEMA, "symbol": ticker, "fetchedAt": AT, "spot": 54.6, "contracts": contracts}
    if max_days is not None:
        doc["maxDays"] = max_days
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump(doc, handle)


def _contract(side, expiry, strike, volume, oi, last, bid, ask):
    return {"side": side, "expiry": expiry, "days": 210, "strike": strike, "volume": volume, "oi": oi, "last": last,
            "bid": bid, "ask": ask, "mark": round((bid + ask) / 2, 2), "delta": 0.19, "iv": 40.0, "newVolume": 0}


@pytest.fixture(autouse=True)
def _clean():
    agx_flow.reset()
    yield
    agx_flow.reset()


def test_a_leap_row_gets_todays_trades_on_the_same_contract(tmp_path):
    _flow_copy(tmp_path, "INTC", [
        _contract("CALL", "2027-06-17", 100.0, 1200, 49045, 2.31, 2.25, 2.31),      # printed at the ask: bought
        _contract("PUT", "2027-06-17", 40.0, 800, 300, 3.10, 3.10, 3.30),           # at the bid: sold, and unusual
    ])
    rows = [{"ticker": "INTC", "side": "CALL", "expiry": "2027-06-17", "strike": 100.0, "oi": 49045},
            {"ticker": "INTC", "side": "PUT", "expiry": "2027-06-17", "strike": 40.0, "oi": 300},
            {"ticker": "INTC", "side": "CALL", "expiry": "2027-06-17", "strike": 120.0, "oi": 10},   # did not trade
            {"ticker": "CRWV", "side": "CALL", "expiry": "2027-03-19", "strike": 85.0, "oi": 30162}]  # no flow copy
    scanner.with_flow(rows, tmp_path)
    call, put, quiet, other = rows
    assert call["flow"] == {"volume": 1200, "premium": round(1200 * 2.28 * 100), "fill": "ask", "unusual": False, "at": AT}
    assert put["flow"]["fill"] == "bid" and put["flow"]["unusual"] is True
    assert "flow" not in quiet and "flow" not in other


def test_a_flow_copy_from_the_old_45_day_window_is_read_again_in_market_hours_only():
    """Copies saved before 2026-11-19 stop at 45 days, so LEAP contracts are missing from them: re-read in market hours.
    Never at night: Schwab reports volume as 0 then, and a re-read at 23:49 ET on 2026-11-19 emptied 97 tickers."""
    old = {"fetchedAt": "2026-11-20T09:31:00-05:00", "contracts": []}
    new = {**old, "maxDays": agx_flow.FETCH_MAX_DAYS}
    market = datetime(2026, 11, 20, 9, 33, tzinfo=ET)
    assert agx_flow._fresh(old, market) is False
    assert agx_flow._fresh(new, market) is True
    night = datetime(2026, 11, 19, 23, 0, tzinfo=ET)
    yesterday = {"fetchedAt": "2026-11-19T16:04:00-05:00", "contracts": []}
    assert agx_flow._fresh(yesterday, night) is True                # the old copy is kept overnight


def test_a_night_read_with_no_volume_never_replaces_the_days_numbers(tmp_path, monkeypatch):
    agx_flow.reset()
    _flow_copy(tmp_path, "NVDA", [_contract("CALL", "2027-06-17", 200.0, 4000, 70000, 6.1, 5.9, 6.1)], max_days=None)
    zeros = {"underlyingPrice": 144.7, "callExpDateMap": {"2027-06-17:210": {"200.0": [
        {"strikePrice": 200.0, "totalVolume": 0, "openInterest": 70000, "bid": 5.9, "ask": 6.1, "mark": 6.0}]}}}
    monkeypatch.setattr(agx_flow, "fetch", lambda ticker, day: zeros)
    monkeypatch.setattr(agx_flow, "_fresh", lambda doc, now: False)     # as if it were due for a read
    doc = agx_flow.refresh("NVDA", tmp_path, datetime(2026, 11, 19, 23, 49, tzinfo=ET))
    assert doc["contracts"][0]["volume"] == 4000
    assert agx_flow._read_disk(tmp_path, "NVDA")["contracts"][0]["volume"] == 4000   # the file was not replaced


def test_the_board_carries_flow_on_its_rows(tmp_path, monkeypatch):
    """Through scanner.board(): the headline, ENTRY and all-strike rows carry the flow of their contract."""
    doc = {"schema": scanner.COMPACT_SCHEMA, "symbol": "INTC", "fetchedAt": AT, "spot": 54.6, "rows": [
        {"side": "CALL", "expiry": "2027-06-17", "strike": 100.0, "days": 210, "oi": 49045, "volume": 0, "delta": 0.19,
         "bid": 2.25, "ask": 2.31, "entry": True}]}
    monkeypatch.setattr(scanner, "held", lambda ticker, root, now: doc if ticker == "INTC" else None)
    monkeypatch.setattr(scanner, "_schedule", lambda *a, **k: None)
    _flow_copy(tmp_path, "INTC", [_contract("CALL", "2027-06-17", 100.0, 1200, 49045, 2.31, 2.25, 2.31)])
    out = scanner.board(["INTC"], tmp_path, datetime(2026, 11, 19, 23, 0, tzinfo=ET), months_from=3, months_to=12)
    assert out["rows"][0]["flow"]["fill"] == "ask"
    assert out["biggestCall"]["flow"]["volume"] == 1200
    assert out["entries"][0]["flow"]["premium"] == 273600              # 1,200 contracts x mid 2.28 x 100 shares


def test_todays_bets_sum_the_money_on_every_call_and_put_traded_today(tmp_path):
    """2026-11-23 "in leap oi scanner ... I want: Today's bets ... Calls got 73% of the money": the same totals the
    ticker story shows (AGX Flow, every contract it read, volume x mid x 100), per ticker."""
    _flow_copy(tmp_path, "SMCI", [
        _contract("CALL", "2026-12-04", 25.5, 1000, 500, 1.00, 0.95, 1.05),        # $100,000
        _contract("CALL", "2027-06-17", 40.0, 0, 9000, 0.50, 0.45, 0.55),          # did not trade: $0
        _contract("PUT", "2026-11-27", 25.0, 200, 800, 1.80, 1.75, 1.85),          # $36,000
    ])
    bets = scanner.todays_bets("SMCI", tmp_path)
    assert {k: bets[k] for k in ("calls", "puts", "at")} == {"calls": 100000, "puts": 36000, "at": AT}
    assert scanner.todays_bets("CRWV", tmp_path) is None                       # AGX Flow has not read it


def test_todays_bets_name_the_contracts_that_took_the_most_money(tmp_path):
    """2026-11-23 "$20 million went to calls which strike is that?" then "Above table I don't see in our app, can we do
    it?": the top contracts by today's money on each side, with their share of that side, whatever their open interest
    (the 22 Call took $4.4M with more open than traded, so the unusual-only Big bets missed it)."""
    calls = [_contract("CALL", "2026-11-27", 22.0, 15338, 40000, 2.95, 2.85, 2.95),   # most money, NOT unusual
             _contract("CALL", "2026-12-04", 25.5, 22522, 9000, 1.24, 1.24, 1.30)]
    calls += [_contract("CALL", "2027-03-19", 30.0 + i, 100, 50, 1.00, 0.95, 1.05) for i in range(6)]
    _flow_copy(tmp_path, "SMCI", calls + [_contract("PUT", "2026-11-27", 25.0, 200, 800, 1.80, 1.75, 1.85)])
    bets = scanner.todays_bets("SMCI", tmp_path)
    top = bets["topCalls"]
    assert len(top) == scanner.TOP_BET_CONTRACTS == 5
    assert top[0] == {"strike": 22.0, "expiry": "2026-11-27", "volume": 15338, "premium": round(15338 * 2.9 * 100),
                      "share": round(100 * round(15338 * 2.9 * 100) / bets["calls"]), "fill": "ask"}
    assert [r["strike"] for r in top[:2]] == [22.0, 25.5] and top[1]["fill"] == "bid"
    assert [r["premium"] for r in top] == sorted((r["premium"] for r in top), reverse=True)
    assert bets["topPuts"] == [{"strike": 25.0, "expiry": "2026-11-27", "volume": 200, "premium": 36000, "share": 100,
                                "fill": "mid"}]


def test_the_board_puts_todays_bets_on_each_by_ticker_line(tmp_path, monkeypatch):
    doc = {"schema": scanner.COMPACT_SCHEMA, "symbol": "INTC", "fetchedAt": AT, "spot": 54.6, "rows": [
        {"side": "CALL", "expiry": "2027-06-17", "strike": 100.0, "days": 210, "oi": 49045, "volume": 0, "delta": 0.19,
         "bid": 2.25, "ask": 2.31, "entry": True}]}
    monkeypatch.setattr(scanner, "held", lambda ticker, root, now: doc if ticker in {"INTC", "AMD"} else None)
    monkeypatch.setattr(scanner, "_schedule", lambda *a, **k: None)
    _flow_copy(tmp_path, "INTC", [_contract("CALL", "2027-06-17", 100.0, 1200, 49045, 2.31, 2.25, 2.31)])
    out = scanner.board(["INTC", "AMD"], tmp_path, datetime(2026, 11, 19, 23, 0, tzinfo=ET))
    lines = {t["ticker"]: t for t in out["byTicker"]}
    assert {k: lines["INTC"]["todayBets"][k] for k in ("calls", "puts", "at")} == {"calls": 273600, "puts": 0, "at": AT}
    assert lines["INTC"]["todayBets"]["topCalls"][0]["strike"] == 100.0 and lines["INTC"]["todayBets"]["topPuts"] == []
    assert lines["AMD"]["todayBets"] is None
