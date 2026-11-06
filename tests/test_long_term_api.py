"""Integration: the Long Term (Rule #1 / Buffett) screen through the real routes over HTTP.

GET /api/long-term, GET /api/long-term/<SYM> and POST /api/long-term/refresh are driven through
api_server with the real LongTermService behind them. EDGAR, Finnhub and Schwab are fakes served from
tests/fixtures/long_term (the same fakes as tests/test_long_term_service.py); the store lives in a
pytest tmp folder; "now" is frozen at Friday 2026-11-06 20:00 ET.
"""
from __future__ import annotations

import copy
import os
import threading
from datetime import timedelta

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from long_term import service as lt_service
from test_long_term_leaps import FakeChain, expiry_of, std_chain
from test_long_term_service import NOW, NOW_MS, fake_finnhub, make_svc

VIEWER = "viewer-long-term@example.com"
AAPL_PRICE = 344.17


class Quotes:
    """Schwab stand-in: answers each asked symbol from a fixed table (canonical keys)."""

    def __init__(self, table: dict[str, dict]):
        self.table = table
        self.calls: list[list[str]] = []

    def __call__(self, schwab_symbols):
        self.calls.append(list(schwab_symbols))
        return {s: dict(self.table[s.replace("/", "-")]) for s in schwab_symbols if s.replace("/", "-") in self.table}


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory):
    """One fixture-backed refresh pass for the whole file (the slow part)."""
    directory = tmp_path_factory.mktemp("long_term_store")
    make_svc(directory).refresh(force=True)
    seed_user(VIEWER, role="user")
    return directory


def _install(monkeypatch, store_dir, quotes):
    s = make_svc(store_dir, quotes=quotes)
    monkeypatch.setattr(lt_service, "_INSTANCE", s)
    return s


@pytest.fixture
def svc(monkeypatch, store_dir):
    s = _install(monkeypatch, store_dir,
                 Quotes({"AAPL": {"last_price": AAPL_PRICE, "quote_time_ms": NOW_MS, "description": "Apple"}}))
    yield s
    s.stop_refresher()


def _rows(body):
    return {r["symbol"]: r for r in body["rows"]}


def test_rows_round_trip_with_live_price(svc):
    with running_api() as api:
        status, body = api.get("/api/long-term?mos=50")
    assert status == 200, body
    assert body["meta"]["mos"] == 50
    aapl = _rows(body)["AAPL"]
    assert aapl["price"] == AAPL_PRICE and aapl["quoteTimeMs"] == NOW_MS
    assert body["meta"]["scored"] >= 8 and "SPY" in body["meta"]["etfs"]


def test_default_mos_is_50_and_30_is_allowed(svc):
    with running_api() as api:
        status, body = api.get("/api/long-term")
        status30, body30 = api.get("/api/long-term?mos=30")
    assert (status, body["meta"]["mos"]) == (200, 50)
    assert (status30, body30["meta"]["mos"]) == (200, 30)


def test_bad_mos_is_400(svc):
    with running_api() as api:
        status, body = api.get("/api/long-term?mos=40")
    assert status == 400 and body == {"error": "mos must be 50 or 30"}


def test_anonymous_tunnel_visitor_is_401(svc):
    with running_api() as api:
        rows_status, _ = api.get("/api/long-term", headers=RELAYED)
        detail_status, _ = api.get("/api/long-term/AAPL", headers=RELAYED)
    assert (rows_status, detail_status) == (401, 401)


def test_detail_and_unknown_ticker(svc):
    with running_api() as api:
        status, body = api.get("/api/long-term/aapl")
        missing_status, missing = api.get("/api/long-term/ZZZQ")
    assert status == 200, body
    assert body["symbol"] == "AAPL" and body["row"]["price"] == AAPL_PRICE
    assert body["explain"] and body["checks"]
    assert (missing_status, missing) == (404, {"error": "unknown ticker"})


def test_no_quotes_means_no_buy_and_no_leaps(monkeypatch, store_dir):
    s = _install(monkeypatch, store_dir, Quotes({}))
    with running_api() as api:
        status, body = api.get("/api/long-term")
    assert status == 200 and body["rows"]
    assert all(r["signal"] != "BUY" for r in body["rows"])
    assert not any(r["leaps"] for r in body["rows"])
    assert all(r["price"] is None for r in body["rows"])
    s.stop_refresher()


def test_three_day_old_quote_makes_every_scorable_row_no_price(monkeypatch, store_dir):
    old_ms = (NOW - timedelta(days=3)).timestamp() * 1000.0
    probe = make_svc(store_dir)
    table = {sym: {"last_price": 100.0, "quote_time_ms": old_ms} for sym in probe.universe()}
    s = _install(monkeypatch, store_dir, Quotes(table))
    with running_api() as api:
        status, body = api.get("/api/long-term")
    assert status == 200
    # AVOID is decided by the business alone (it outranks NO_PRICE by design, long_term/valuation.py
    # signal()); every other scorable row needs a fresh price, so a stale quote leaves it NO_PRICE.
    priced = [r for r in body["rows"] if r["signal"] in {"BUY", "WAIT", "NO_PRICE"}]
    assert priced and all(r["signal"] == "NO_PRICE" and "stale" in r["reason"] for r in priced)
    assert all(r["buyZonePct"] is None for r in body["rows"])
    assert not any(r["leaps"] for r in body["rows"])
    s.stop_refresher()


def test_refresh_is_admin_only(svc):
    with running_api() as api:
        viewer_status, _ = api.post("/api/long-term/refresh", headers={ACCESS_EMAIL_HEADER: VIEWER})
        anon_status, _ = api.post("/api/long-term/refresh", headers=RELAYED)
    assert viewer_status == 403 and anon_status == 401
    assert svc._thread is None


def test_admin_refresh_in_market_hours_is_deferred(svc, monkeypatch):
    monkeypatch.setattr(lt_service, "market_hours", lambda now: True)
    with running_api() as api:
        status, body = api.post("/api/long-term/refresh")
        _, page = api.get("/api/long-term")
    assert (status, body) == (200, {"deferredUntil": "16:05 ET"})
    assert page["meta"]["deferredUntil"] == "16:05 ET"
    assert svc._thread is None                                  # deferred: nothing started


def test_harness_never_starts_the_refresher(svc):
    assert os.environ.get("AGX_LONG_TERM_REFRESH") == "0"
    with running_api() as api:
        api.get("/api/long-term")
    assert svc._thread is None
    assert not any(t.name == "long-term-refresher" for t in threading.enumerate())


# ---------- final review fix 5: the BUY guarantees, end to end over HTTP ----------
# No fixture trades near its Safety Buy, so (as in test_long_term_service's BUY/LEAPS test) AAPL is
# moved there: the nightly pass sees a Schwab quote at 99% of Safety Buy and a Finnhub peTTM re-based
# to that price (price sanity holds; the sticker never depends on today's price).

def _patched_finnhub(base_get, **metric):
    def get(url, timeout):
        doc = copy.deepcopy(base_get(url, timeout))
        if "symbol=AAPL" in url:
            doc["metric"].update(metric)
        return doc
    return get


def _buy_price(store_dir):
    base = make_svc(store_dir)
    row = next(r for r in base.page_payload(50, {})["rows"] if r["symbol"] == "AAPL")
    assert row["safetyBuy"], row
    return round(row["safetyBuy"] * 0.99, 2)


def _priced_store(tmp_path_factory, store_dir, name, **extra_metric):
    price = _buy_price(store_dir)
    directory = tmp_path_factory.mktemp(name)
    s = make_svc(directory, universe=["AAPL"],
                 quotes=Quotes({"AAPL": {"last_price": price, "quote_time_ms": NOW_MS, "description": "Apple"}}))
    eps = fake_finnhub("symbol=AAPL&", 0)["metric"]["epsTTM"]
    s._finnhub_get = _patched_finnhub(fake_finnhub, peTTM=price / eps, **extra_metric)
    s.refresh(force=True)
    assert s.store.records["AAPL"]["ref_price"] == pytest.approx(price)
    return directory, price


@pytest.fixture(scope="module")
def buy_store(tmp_path_factory, store_dir):
    return _priced_store(tmp_path_factory, store_dir, "long_term_buy")


@pytest.fixture(scope="module")
def roic_store(tmp_path_factory, store_dir):
    return _priced_store(tmp_path_factory, store_dir, "long_term_roic", roiTTM=5.0)


def _aapl_over_http(monkeypatch, directory, quote):
    s = _install(monkeypatch, directory, Quotes({"AAPL": quote}))
    try:
        with running_api() as api:
            status, body = api.get("/api/long-term")
    finally:
        s.stop_refresher()
    assert status == 200, body
    return _rows(body)["AAPL"]


def test_fresh_quote_at_safety_buy_is_buy(monkeypatch, buy_store):
    directory, price = buy_store
    row = _aapl_over_http(monkeypatch, directory, {"last_price": price, "quote_time_ms": NOW_MS})
    assert row["signal"] == "BUY", row["reason"]
    assert row["buyBelow"] and price <= row["buyBelow"] and row["buyZonePct"] > 0


@pytest.mark.parametrize("quote", [
    {"last_price": None, "quote_time_ms": NOW_MS},
    {"last_price": 0, "quote_time_ms": NOW_MS},
    "three_days_old",
    "thirty_pct_above",
], ids=["null", "zero", "three_days_old", "thirty_pct_above"])
def test_bad_quote_on_a_buy_row_is_no_price(monkeypatch, buy_store, quote):
    directory, price = buy_store
    if quote == "three_days_old":
        quote = {"last_price": price, "quote_time_ms": (NOW - timedelta(days=3)).timestamp() * 1000.0}
    elif quote == "thirty_pct_above":
        quote = {"last_price": price * 1.3, "quote_time_ms": NOW_MS}
    row = _aapl_over_http(monkeypatch, directory, quote)
    assert row["signal"] == "NO_PRICE", row
    assert row["leaps"] is False and row["buyZonePct"] is None


def test_finnhub_roic_disagreement_turns_buy_into_check_data(monkeypatch, roic_store):
    directory, price = roic_store
    row = _aapl_over_http(monkeypatch, directory, {"last_price": price, "quote_time_ms": NOW_MS})
    assert row["signal"] == "CHECK_DATA" and "ROIC" in row["reason"], row["reason"]
    assert row["leaps"] is False and row["buyBelow"] is None


# ---------- final review fixes 8 and 11: request-path cost, detail symbol ----------

def test_rows_route_never_reads_the_universe_from_disk(svc, monkeypatch):
    monkeypatch.setattr(svc, "universe", lambda: (_ for _ in ()).throw(AssertionError("universe() per request")))
    with running_api() as api:
        status, body = api.get("/api/long-term")
        status2, _ = api.get("/api/long-term")
    assert (status, status2) == (200, 200), body
    assert _rows(body)["AAPL"]["price"] == AAPL_PRICE
    asked = svc._quotes_fn.calls
    assert len(asked) == 1 and "AAPL" in asked[0] and "SPY" in asked[0]   # second request served from the 45 s cache


def test_detail_route_canonicalises_the_symbol(svc):
    with running_api() as api:
        status, body = api.get("/api/long-term/%20aapl")              # not decoded: not a valid symbol
        ok_status, ok = api.get("/api/long-term/aApL")
    assert status == 404 and body == {"error": "unknown ticker"}
    assert ok_status == 200 and ok["symbol"] == "AAPL" and ok["row"]["price"] == AAPL_PRICE


# ---------- LEAP to buy: GET /api/long-term/<SYM>/leap ----------
# The chain is a fake (nothing leaves the PC); the LEAPS-flagged row is the buy_store AAPL.

def _leap_svc(monkeypatch, directory, price, chain):
    s = _install(monkeypatch, directory, Quotes({"AAPL": {"last_price": price, "quote_time_ms": NOW_MS,
                                                           "description": "Apple"}}))
    s._chain_fn = chain
    return s


def test_leap_route_returns_a_contract_for_a_flagged_row(monkeypatch, buy_store):
    directory, price = buy_store
    fake = FakeChain(std_chain((30, 400, 600, 800), spot=price, today=NOW.date()))
    s = _leap_svc(monkeypatch, directory, price, fake)
    try:
        with running_api() as api:
            status, body = api.get("/api/long-term/aapl/leap?mos=50")
            detail_status, detail = api.get("/api/long-term/AAPL?mos=50")
    finally:
        s.stop_refresher()
    assert status == 200, body
    assert body["symbol"] == "AAPL" and body["leaps"] is True and body["price"] == pytest.approx(price)
    assert body["reason"] == "" and body["expiries"] == [expiry_of(400, NOW.date()), expiry_of(600, NOW.date())]
    c = body["contract"]
    assert c and c["dte"] == 600 and c["strike"] < price and c["bid"] > 0 and c["ask"] >= c["bid"]
    assert c["delta"] == 0.75 and c["intrinsic"] == pytest.approx(price - c["strike"])
    assert c["breakeven"] == pytest.approx(c["strike"] + c["ask"]) and c["cost"] == pytest.approx(c["ask"] * 100)
    assert fake.calls and fake.calls[0][0] == "AAPL"
    assert detail_status == 200 and detail["row"]["leaps"] is True and detail["explain"]    # detail still works


def test_leap_route_wait_row_is_not_flagged_and_makes_no_chain_call(svc):
    fake = svc._chain_fn = FakeChain(std_chain(today=NOW.date()))
    with running_api() as api:
        status, body = api.get("/api/long-term/AAPL/leap")
    assert status == 200, body
    assert body["leaps"] is False and body["contract"] is None and body["reason"] == "LEAPS not flagged"
    assert body["price"] == AAPL_PRICE and body["expiries"] == []
    assert fake.calls == []


def test_leap_route_unknown_ticker_is_404(svc):
    svc._chain_fn = FakeChain(std_chain(today=NOW.date()))
    with running_api() as api:
        status, body = api.get("/api/long-term/ZZZQ/leap")
        junk_status, junk = api.get("/api/long-term/%20/leap")
    assert (status, body) == (404, {"error": "unknown ticker"})
    assert (junk_status, junk) == (404, {"error": "unknown ticker"})


def test_leap_route_chain_failure_is_200_chain_unavailable(monkeypatch, buy_store):
    directory, price = buy_store
    s = _leap_svc(monkeypatch, directory, price, FakeChain(error="Schwab down"))
    try:
        with running_api() as api:
            status, body = api.get("/api/long-term/AAPL/leap")
    finally:
        s.stop_refresher()
    assert status == 200, body
    assert body["leaps"] is True and body["contract"] is None and body["reason"] == "chain unavailable"


def test_leap_route_shares_the_mos_validation(svc):
    svc._chain_fn = FakeChain(std_chain(today=NOW.date()))
    with running_api() as api:
        bad_status, bad = api.get("/api/long-term/AAPL/leap?mos=40")
        ok_status, ok = api.get("/api/long-term/AAPL/leap?mos=30")
        anon_status, _ = api.get("/api/long-term/AAPL/leap", headers=RELAYED)
    assert (bad_status, bad) == (400, {"error": "mos must be 50 or 30"})
    assert ok_status == 200 and ok["symbol"] == "AAPL"
    assert anon_status == 401
