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
