"""Integration (2026-11-04 coverage pass, group charts-studies, feature
charts-high-oi-walls): the option chain the chart's High OI walls are drawn
from, through the real GET /api/oi-finder-chain route and the real chain
builder (contract parsing, ATM, tosScriptLevels), with Schwab replaced by a
fake client that returns a fixed chain.

Then the same chain through the gateway: the pipeline's compact disk blob is
what the gateway serves the browser for 30 s, so the walls a phone sees come
from the blob the pipeline wrote. The blob folder is a scratch folder.
"""
from __future__ import annotations

import gzip
import json
import threading
from contextlib import contextmanager
from datetime import date, timedelta

import pytest

import gateway
from api_harness import api_server, running_api
from http_client import ApiClient

SYMBOL = "AAPL"
SPOT = 200.0
STRIKES = [180.0, 185.0, 190.0, 195.0, 200.0, 205.0, 210.0, 215.0, 220.0]
# Open interest by strike. Calls pile up above spot, puts below; 220C has none.
CALL_OI = {180.0: 300, 185.0: 500, 190.0: 900, 195.0: 2000, 200.0: 4000, 205.0: 6000, 210.0: 9000, 215.0: 7000, 220.0: 0}
PUT_OI = {180.0: 5000, 185.0: 6500, 190.0: 8800, 195.0: 4100, 200.0: 3900, 205.0: 1200, 210.0: 800, 215.0: 400, 220.0: 100}
DTES = (2, 9, 45)  # the 45-DTE expiry is past the 31-day window and must not make walls


def _contract(side: str, expiry: date, dte: int, strike: float, oi: int) -> dict:
    distance = (strike - SPOT) / SPOT
    delta = max(0.02, min(0.98, 0.5 - distance * 5)) if side == "CALL" else -max(0.02, min(0.98, 0.5 + distance * 5))
    mid = max(0.05, round(abs(SPOT - strike) * 0.1 + 2.0, 2))
    occ = f"{SYMBOL}  {expiry:%y%m%d}{side[0]}{int(strike * 1000):08d}"
    return {
        "putCall": side, "symbol": occ, "description": f"{SYMBOL} {expiry} {strike} {side}",
        "bid": mid - 0.05, "ask": mid + 0.05, "last": mid, "mark": mid,
        "totalVolume": oi // 10, "openInterest": oi, "delta": round(delta, 3),
        "gamma": 0.02, "theta": -0.05, "vega": 0.1, "volatility": 30.0,
        "strikePrice": strike, "expirationDate": f"{expiry.isoformat()}T20:00:00.000+00:00",
        "daysToExpiration": dte, "inTheMoney": (strike < SPOT) if side == "CALL" else (strike > SPOT),
    }


def _chain() -> dict:
    today = date.today()
    calls, puts = {}, {}
    for dte in DTES:
        expiry = today + timedelta(days=dte)
        key = f"{expiry.isoformat()}:{dte}"
        calls[key] = {f"{strike:.1f}": [_contract("CALL", expiry, dte, strike, CALL_OI[strike])] for strike in STRIKES}
        puts[key] = {f"{strike:.1f}": [_contract("PUT", expiry, dte, strike, PUT_OI[strike])] for strike in STRIKES}
    return {"symbol": SYMBOL, "status": "SUCCESS", "underlyingPrice": SPOT,
            "underlying": {"last": SPOT, "change": 1.5, "percentChange": 0.75},
            "callExpDateMap": calls, "putExpDateMap": puts}


class FakeSchwab:
    def __init__(self):
        self.chain_calls = 0

    def get_option_chain(self, symbol, **kwargs):
        assert symbol == SYMBOL
        self.chain_calls += 1
        return _chain()

    def get_quotes(self, symbols):
        return {}


@pytest.fixture
def schwab(tmp_path, monkeypatch):
    fake = FakeSchwab()
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [fake])
    monkeypatch.setattr(api_server, "_house_tradier_client", lambda: (_ for _ in ()).throw(AssertionError("Tradier must not be reached")))
    blobs = tmp_path / "oi_chain_cache"
    monkeypatch.setattr(api_server.STATE, "oi_finder_chain_disk_cache_dir", blobs)
    caches = (api_server.STATE.oi_finder_chain_cache, api_server.STATE.oi_finder_cache)
    for cache in caches:
        cache.pop(SYMBOL, None)
    fake.blobs = blobs
    yield fake
    for cache in caches:
        cache.pop(SYMBOL, None)


def _expected_side(oi_by_strike: dict, side: str) -> list[float]:
    ranked = sorted(((oi, strike) for strike, oi in oi_by_strike.items() if oi > 0),
                    key=lambda item: (-item[0], item[1] if side == "CALL" else -item[1]))
    return [strike for _oi, strike in ranked[:5]]


def test_a_forced_chain_build_returns_top_five_oi_walls_per_side_per_expiry(schwab):
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}&force=true")
    assert status == 200, body
    assert schwab.chain_calls == 1
    assert body["live"] is True and body["symbol"] == SYMBOL and body["errors"] == []
    assert body["underlyingPrice"] == pytest.approx(SPOT)
    levels = body["tosScriptLevels"]
    assert [level["daysToExpiration"] for level in levels] == [2, 9], "expiries past 31 DTE must not draw walls"
    for level in levels:
        assert [row["strike"] for row in level["callLevels"]] == _expected_side(CALL_OI, "CALL")
        assert [row["strike"] for row in level["putLevels"]] == _expected_side(PUT_OI, "PUT")
        assert all(row["openInterest"] > 0 for row in level["callLevels"] + level["putLevels"])
        ois = [row["openInterest"] for row in level["callLevels"]]
        assert ois == sorted(ois, reverse=True)
    # The biggest wall per side is the one the chart draws bold and flashes.
    front = levels[0]
    assert (front["callLevels"][0]["strike"], front["callLevels"][0]["openInterest"]) == (210.0, 9000)
    assert (front["putLevels"][0]["strike"], front["putLevels"][0]["openInterest"]) == (190.0, 8800)
    assert body["callRows"] and body["putRows"], "the chain table rows come with the walls"
    assert body["currentAtm"], "the chart's ATM banner needs currentAtm"


def test_the_charts_15s_revalidation_is_served_from_cache_not_rebuilt(schwab):
    with running_api() as api:
        _s, built = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}&force=true")
        status, again = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}")
    assert status == 200
    assert schwab.chain_calls == 1, "a poll inside 15 s must not ask Schwab again"
    assert again["cached"] is True
    assert again["tosScriptLevels"] == built["tosScriptLevels"]


@contextmanager
def running_gateway(pipeline_base: str):
    server = gateway.GatewayServer(("127.0.0.1", 0), gateway.GatewayHandler)
    original = gateway.GatewayHandler.pipeline_base
    gateway.GatewayHandler.pipeline_base = pipeline_base
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        gateway.GatewayHandler.pipeline_base = original


def test_the_gateway_serves_the_pipelines_fresh_chain_blob_with_the_same_walls(schwab, monkeypatch):
    monkeypatch.setattr(gateway, "CHAIN_BLOBS", schwab.blobs)
    with running_api() as api:
        _s, built = api.get(f"/api/oi-finder-chain?symbol={SYMBOL}&force=true")
        blob_path = schwab.blobs / f"{SYMBOL}.json.gz"
        assert blob_path.is_file(), "a browser chain read must persist the compact blob"
        with gzip.open(blob_path, "rt", encoding="utf-8") as handle:
            assert json.load(handle)["tosScriptLevels"] == built["tosScriptLevels"]
        with running_gateway(api.base_url) as front:
            before = dict(gateway.SERVE_STATS)
            status, body = front.get(f"/api/oi-finder-chain?symbol={SYMBOL}")
            after = dict(gateway.SERVE_STATS)
    assert status == 200, body
    assert after["chainBlob"] - before["chainBlob"] == 1 and after["chainProxy"] == before["chainProxy"]
    assert body["tosScriptLevels"] == built["tosScriptLevels"]
    assert (body["cached"], body["stale"], body["refreshing"]) == (True, True, True)
    assert 0 <= body["gatewayBlobAgeSeconds"] <= gateway.CHAIN_BLOB_MAX_AGE_SECONDS
    assert schwab.chain_calls == 1
