"""Integration: the one option-chain feed every option page reads
(/api/oi-finder-chain and /api/oi-finder), through the real api_server route
over HTTP and the real chain builder behind it, and through the real gateway
in front of it.

Schwab and Tradier are fakes: each hands back a Schwab-shaped chain (the
callExpDateMap / putExpDateMap JSON the real client returns) or raises the
way a dead credential does. The disk chain cache and the in-memory caches are
scratch copies, and the background rebuild pool is replaced so no thread
outlives a test. Nothing here can reach Schwab, Tradier or artifacts/.
"""
from __future__ import annotations

import copy
import gzip
import json
import threading
import time
from datetime import datetime, timedelta
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from api_harness import RELAYED, api_server, running_api
from http_client import ApiClient

SPOT = 200.0
STRIKES = (190.0, 195.0, 200.0, 205.0, 210.0)
# Walls: the big call OI sits ABOVE spot (205/210) and the big put OI at/below
# it (190/195), with deliberately large ITM OI on the wrong side (190C, 210P)
# that the daily-sheet convention must NOT flag as a wall.
CALL_OI = {190.0: 9000, 195.0: 300, 200.0: 800, 205.0: 2500, 210.0: 7000}
PUT_OI = {190.0: 6000, 195.0: 2000, 200.0: 700, 205.0: 300, 210.0: 8000}
QUOTE = {"last_price": SPOT, "change": 2.5, "change_pct": 1.27}


def occ(symbol: str, expiry: str, side: str, strike: float) -> str:
    """The padded OCC key Schwab uses for a contract: 'AAPL  261106C00200000'."""
    return f"{symbol:<6}{expiry[2:4]}{expiry[5:7]}{expiry[8:10]}{side}{int(round(strike * 1000)):08d}"


def schwab_chain(symbol: str = "AAPL", dtes=(2, 9, 23), spot: float = SPOT, volatility=None) -> dict:
    """A Schwab /chains payload. Expiries are relative to today because the
    builder measures DTE against the real date; every number is fixed."""
    today = datetime.now().date()
    calls: dict = {}
    puts: dict = {}
    for dte in dtes:
        expiry = (today + timedelta(days=dte)).isoformat()
        key = f"{expiry}:{dte}"
        calls[key], puts[key] = {}, {}
        for strike in STRIKES:
            for side, book, oi_table in (("C", calls, CALL_OI), ("P", puts, PUT_OI)):
                moneyness = (strike - spot) if side == "C" else (spot - strike)
                delta = round(max(0.05, min(0.95, 0.5 - moneyness * 0.04)), 2)
                mid = round(max(0.15, 4.0 - moneyness * 0.5) + dte * 0.05, 2)
                book[key][f"{strike:.1f}"] = [{
                    "symbol": occ(symbol, expiry, side, strike),
                    "putCall": "CALL" if side == "C" else "PUT",
                    "strikePrice": strike,
                    "delta": delta if side == "C" else -delta,
                    "gamma": 0.031,
                    "theta": -0.12,
                    "vega": 0.08,
                    "totalVolume": int(1000 + strike),
                    "openInterest": oi_table[strike],
                    "daysToExpiration": dte,
                    "expirationDate": expiry,
                    "bid": round(mid - 0.05, 2),
                    "ask": round(mid + 0.05, 2),
                    "last": mid,
                    "mark": mid,
                }]
    chain = {
        "symbol": symbol,
        "status": "SUCCESS",
        "underlyingPrice": spot,
        "underlying": {"last": spot, "netChange": 2.5, "netPercentChange": 1.27},
        "callExpDateMap": calls,
        "putExpDateMap": puts,
    }
    if volatility is not None:
        chain["volatility"] = volatility
    return chain


def refuse_background_callers() -> None:
    """Importing api_server builds STATE, and STATE starts its collectors (the
    9:15 OI-alert ladder, the MAG7 live collector...) in this process. Only the
    test's own request threads may use a fake broker; a collector that reaches
    one is refused, so it cannot add calls or chains to what a test measures."""
    if "process_request_thread" not in threading.current_thread().name:
        raise RuntimeError("background collector refused by the test's fake broker")


class FakeSchwab:
    """Stands in for one configured SchwabClient profile."""

    def __init__(self, chain=None, error: str = "", quote=None, empty_narrow: bool = False):
        self.chain = chain
        self.error = error
        self.quote = QUOTE if quote is None else quote
        self.empty_narrow = empty_narrow
        self.calls: list[dict] = []
        self.quote_calls: list[list[str]] = []

    def get_option_chain(self, symbol, contract_type="ALL", strike_count=0, from_date=None, to_date=None, **_kw):
        refuse_background_callers()
        days = (to_date - from_date).days if from_date and to_date else None
        self.calls.append({"symbol": symbol, "strikeCount": strike_count, "days": days})
        if self.error:
            raise RuntimeError(self.error)
        if self.empty_narrow and days is not None and days <= 7:
            return {}
        chain = self.chain(symbol) if callable(self.chain) else self.chain
        return copy.deepcopy(chain) if chain else {}

    def get_quotes(self, symbols):
        refuse_background_callers()
        self.quote_calls.append(list(symbols))
        return {symbol: dict(self.quote) for symbol in symbols}


class FakeTradier:
    def __init__(self, chain=None, error: str = ""):
        self.chain = chain
        self.error = error
        self.calls: list[str] = []

    def get_option_chain_range(self, symbol, **_kw):
        refuse_background_callers()
        self.calls.append(symbol)
        if self.error:
            raise RuntimeError(self.error)
        return copy.deepcopy(self.chain(symbol) if callable(self.chain) else self.chain)


class InlinePool:
    """Runs a background rebuild on the request thread, so a test can see
    exactly what one rebuild does and no thread outlives the test."""

    def __init__(self):
        self.submitted = 0

    def submit(self, fn, *args, **kwargs):
        self.submitted += 1
        fn(*args, **kwargs)


def pause_collectors(monkeypatch, state) -> None:
    """Background collectors build with background_snapshot=True; answer them
    without a build, so they cannot write into the scratch caches a test reads."""
    real = state.oi_finder_payload

    def foreground_only(symbol, *args, background_snapshot=False, **kwargs):
        if background_snapshot:
            return {"live": False, "symbol": symbol, "callRows": [], "putRows": [],
                    "errors": [{"error": "collectors are paused in this test"}]}
        return real(symbol, *args, **kwargs)

    monkeypatch.setattr(state, "oi_finder_payload", foreground_only)


@pytest.fixture
def chain_env(monkeypatch, tmp_path):
    """Scratch caches, fake brokers, and a recorder in place of the
    background rebuild (tests that need the real rebuild swap in InlinePool)."""
    state = api_server.STATE
    env = SimpleNamespace(
        schwab=[FakeSchwab(chain=schwab_chain)],
        tradier=FakeTradier(error="tradier should not be needed"),
        rebuilds=[],
        disk=tmp_path / "oi_chain_cache",
    )
    pause_collectors(monkeypatch, state)
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: list(env.schwab))
    monkeypatch.setattr(api_server, "_house_tradier_client", lambda: env.tradier)
    monkeypatch.setattr(state, "oi_finder_chain_disk_cache_dir", env.disk)
    for name in ("oi_finder_chain_cache", "oi_finder_cache", "oi_finder_research_cache",
                 "oi_finder_volume_history", "oi_finder_refresh_cooldown"):
        monkeypatch.setattr(state, name, {})
    monkeypatch.setattr(state, "oi_finder_background_refreshes", set())
    monkeypatch.setattr(
        state, "_refresh_oi_finder_in_background",
        lambda symbol, compact=False, research_section="": env.rebuilds.append((symbol, compact, research_section)),
    )
    return env


def broker_chain_calls(env) -> int:
    return sum(len(client.calls) for client in env.schwab)


# --------------------------------------------------------------------------- the route's answers

def test_a_malformed_ticker_reads_plainly_and_never_reaches_a_broker(chain_env):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=1BAD")
    assert status == 200, body
    assert body["errors"][0]["error"] == "Enter a valid ticker symbol."
    assert (body["callRows"], body["putRows"], body["live"]) == ([], [], False)
    assert broker_chain_calls(chain_env) == 0 and chain_env.rebuilds == []


def test_a_cold_ticker_answers_the_warming_shape_and_queues_one_rebuild(chain_env):
    """The request thread must never ride to Schwab on a cold ticker (that is
    what parked every request for 60 s+ on 2026-08-19)."""
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=aapl")
    assert status == 200, body
    assert (body["symbol"], body["warming"], body["live"]) == ("AAPL", True, False)
    assert body["selectedExpiryChainRows"] == []
    assert broker_chain_calls(chain_env) == 0
    assert chain_env.rebuilds == [("AAPL", True, "")]


def test_fifteen_second_cache_then_force_rebuilds(chain_env):
    with running_api() as api:
        _, built = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        status, cached = api.get("/api/oi-finder-chain?symbol=AAPL")
        assert broker_chain_calls(chain_env) == 1
        _, rebuilt = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert built["live"] is True and built["cached"] is False
    assert status == 200 and cached["cached"] is True and not cached.get("stale")
    assert cached["selectedExpiryChainRows"] == built["selectedExpiryChainRows"]
    assert rebuilt["cached"] is False
    assert broker_chain_calls(chain_env) == 2
    assert chain_env.rebuilds == []          # a fresh cache hit never queues a rebuild


def test_the_disk_copy_paints_after_a_restart_and_revalidates(chain_env, monkeypatch):
    with running_api() as api:
        api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        saved = chain_env.disk / "AAPL.json.gz"
        assert saved.exists(), "a compact browser build is saved for the next restart"
        monkeypatch.setattr(api_server.STATE, "oi_finder_chain_cache", {})   # the restart
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL")
    assert status == 200, body
    assert (body["diskCached"], body["stale"], body["refreshing"]) == (True, True, True)
    assert body["selectedExpiryChainRows"], "the restart paints the saved chain, not a spinner"
    assert broker_chain_calls(chain_env) == 1
    assert chain_env.rebuilds == [("AAPL", True, "")]


def test_a_rejected_schwab_profile_falls_through_to_the_next_one(chain_env):
    """2026-08-27 DOCU: market_data raised invalid_client while trading answered."""
    dead = FakeSchwab(error="invalid_client: Unauthorized")
    alive = FakeSchwab(chain=schwab_chain)
    chain_env.schwab = [dead, alive]
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    assert body["live"] is True and body["source"] == "Schwab/TOS option chain"
    assert body["sourceNote"] == "" and body["errors"] == []
    assert (len(dead.calls), len(alive.calls)) == (1, 1)
    assert chain_env.tradier.calls == []


def test_the_tradier_fallback_is_announced_on_screen(chain_env):
    chain_env.schwab = [FakeSchwab(error="invalid_client: Unauthorized")]
    chain_env.tradier = FakeTradier(chain=schwab_chain)
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    assert body["live"] is True and body["selectedExpiryChainRows"]
    assert body["source"] == "Tradier option chain (Schwab/TOS fallback)"
    assert "Tradier" in body["sourceNote"] and "Schwab/TOS chain was unavailable" in body["sourceNote"]
    assert chain_env.tradier.calls == ["AAPL"]


def test_when_both_providers_fail_schwab_is_named_first(chain_env):
    """2026-08-28: naming only Tradier sent the trader to renew the wrong key."""
    chain_env.schwab = [FakeSchwab(error="invalid_client: Unauthorized")]
    chain_env.tradier = FakeTradier(error="token not approved")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    error = body["errors"][0]
    assert error["error"] == (
        "Schwab/TOS failed (invalid_client: Unauthorized); "
        "the Tradier fallback also failed (token not approved)."
    )
    assert (error["primary"], error["fallback"]) == ("invalid_client: Unauthorized", "token not approved")
    assert (body["live"], body["callRows"], body["putRows"]) == (False, [], [])


def test_a_research_section_is_built_fresh_even_right_after_a_compact_read(chain_env):
    """The compact copy has an empty heatmap; serving it for section=heatmap
    left Heatmap blank on phone and desktop."""
    with running_api() as api:
        api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        status, body = api.get("/api/oi-finder?symbol=AAPL&section=heatmap")
    assert status == 200, body
    assert body["researchSection"] == "heatmap"
    heatmap = body["dailyLiquidityHeatmap"]
    assert heatmap["liveOnly"] is True and heatmap["call"] and heatmap["put"]
    assert broker_chain_calls(chain_env) == 2      # its own build, not the compact cache


def test_a_signed_out_tunnel_visitor_is_refused(chain_env):
    with running_api() as api:
        status, _body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true", headers=RELAYED)
    assert status == 401
    assert broker_chain_calls(chain_env) == 0


@pytest.mark.xfail(strict=True, reason=(
    "Invalid-symbol rebuild loop (memory: OPEN): a well-formed ticker with no options "
    "is never remembered as missing - its failure is not cached and the cooldown equals "
    "the (instant) build time, so every 15 s poll asks Schwab and Tradier again."))
def test_a_ticker_with_no_options_is_not_rebuilt_on_every_poll(monkeypatch, tmp_path):
    # Not chain_env: this test needs the REAL background rebuild, run inline on
    # scratch state so nothing outlives the test.
    state = api_server.STATE
    pause_collectors(monkeypatch, state)
    for name in ("oi_finder_chain_cache", "oi_finder_cache", "oi_finder_research_cache",
                 "oi_finder_volume_history", "oi_finder_refresh_cooldown"):
        monkeypatch.setattr(state, name, {})
    monkeypatch.setattr(state, "oi_finder_background_refreshes", set())
    monkeypatch.setattr(state, "oi_finder_chain_disk_cache_dir", tmp_path / "oi_chain_cache")
    pool = InlinePool()
    monkeypatch.setattr(state, "oi_finder_refresh_pool", pool)
    monkeypatch.setattr(state, "oi_finder_quick_pool", pool)
    empty = FakeSchwab(chain={})                      # Schwab answers: no chain for ZZZZQ
    tradier = FakeTradier(error="no option chain for ZZZZQ")
    monkeypatch.setattr(api_server, "_schwab_market_clients", lambda: [empty])
    monkeypatch.setattr(api_server, "_house_tradier_client", lambda: tradier)
    with running_api() as api:
        _, first = api.get("/api/oi-finder-chain?symbol=ZZZZQ")
        assert first["warming"] is True and pool.submitted == 1
        asked_after_first = len(empty.calls)
        assert asked_after_first >= 1
        time.sleep(0.5)                               # the next poll comes 15 s later in the app
        api.get("/api/oi-finder-chain?symbol=ZZZZQ")
    assert len(empty.calls) == asked_after_first, (
        f"Schwab was asked for ZZZZQ again ({len(empty.calls)} chain calls in two polls)")


# --------------------------------------------------------------------------- the gateway in front

@pytest.fixture
def gateway_in_front(monkeypatch, tmp_path):
    """The real gateway handler on a free port, proxying to a running_api."""
    import gateway

    blobs = tmp_path / "gateway_chain_blobs"
    blobs.mkdir()
    monkeypatch.setattr(gateway, "CHAIN_BLOBS", blobs)

    def start(api_base: str):
        handler = type("TestGatewayHandler", (gateway.GatewayHandler,), {"pipeline_base": api_base})
        server = gateway.GatewayServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        started.append((server, thread))
        return ApiClient(f"http://127.0.0.1:{server.server_address[1]}")

    started: list = []
    yield SimpleNamespace(start=start, blobs=blobs)
    for server, thread in started:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _write_blob(directory, symbol: str, scanned_at: datetime) -> None:
    payload = {"symbol": symbol, "live": True, "fromGatewayBlob": True,
               "scannedAt": scanned_at.isoformat(), "selectedExpiryChainRows": [{"strike": 200}]}
    with gzip.open(directory / f"{symbol}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def test_the_gateway_serves_a_fresh_blob_and_proxies_everything_else(chain_env, gateway_in_front):
    now = datetime.now().astimezone()
    _write_blob(gateway_in_front.blobs, "AAPL", now - timedelta(seconds=2))
    _write_blob(gateway_in_front.blobs, "NVDA", now - timedelta(minutes=10))   # older than 30 s
    with running_api() as api:
        front = gateway_in_front.start(api.base_url)
        status, blob = front.get("/api/oi-finder-chain?symbol=AAPL")
        assert status == 200, blob
        assert blob["fromGatewayBlob"] is True and blob["stale"] is True
        assert blob["gatewayBlobAgeSeconds"] is not None and blob["gatewayBlobAgeSeconds"] < 30
        assert broker_chain_calls(chain_env) == 0 and chain_env.rebuilds == []

        _, old = front.get("/api/oi-finder-chain?symbol=NVDA")
        assert "fromGatewayBlob" not in old and old["warming"] is True      # api_server answered
        _, research = front.get("/api/oi-finder?symbol=AAPL&section=heatmap")
        assert "fromGatewayBlob" not in research and research["researchSection"] == "heatmap"
        _, forced = front.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        assert "fromGatewayBlob" not in forced and forced["live"] is True
    assert chain_env.rebuilds == [("NVDA", True, "")]
