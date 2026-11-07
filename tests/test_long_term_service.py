"""Long Term service: store, refresh pass, refresher gate, page and detail payloads. No network.

EDGAR, Finnhub and Schwab are fakes served from tests/fixtures/long_term; every store write goes to
tmp_path; "now" is frozen (Friday 2026-11-06 20:00 ET unless a test says otherwise).
"""
import json
import os
import re
import threading
import urllib.error
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from long_term import service as service_mod
from long_term.edgar import TICKERS_URL, EdgarBlocked, EdgarClient
from long_term.service import ROW_KEYS, LongTermService, Store

ET = ZoneInfo("America/New_York")
FIX = Path(__file__).parent / "fixtures" / "long_term"
SYMBOLS = ["AAPL", "ASML", "TSM", "HOOD", "MCD", "HD", "JPM", "GOOGL", "NVDA", "CRWV"]
CIKS = {"AAPL": 320193, "ASML": 937966, "TSM": 1046179, "HOOD": 1783879, "MCD": 63908,
        "HD": 354950, "JPM": 19617, "GOOGL": 1652044, "NVDA": 1045810, "CRWV": 1769628}
SYM_BY_CIK = {c: s for s, c in CIKS.items()}


def ET_TIME(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=ET)


NOW = ET_TIME(2026, 11, 6, 20, 0)          # Friday evening, market closed
NOW_MS = NOW.timestamp() * 1000.0
EXPECTED_ROW_KEYS = {
    "symbol", "name", "price", "quoteTimeMs", "refPrice", "signal", "leaps", "reason", "buyBelow",
    "leapsBelow", "buyZonePct", "safetyBuy", "sticker", "buffett", "fairValue", "payback", "roic", "roe",
    "financial", "big5", "big5Na", "quality", "pe", "debtToEquity", "salesGrowth", "epsGrowth",
    "fcfGrowth", "equityGrowth", "marketCap", "filedEnd", "years", "currency", "young",
}


def noop(_s=0):
    return None


@lru_cache(maxsize=None)
def _bytes(name):
    return (FIX / name).read_bytes()


@lru_cache(maxsize=None)
def _fh_doc(sym):
    p = FIX / f"finnhub_{sym}.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def fixture_price(sym):
    m = (_fh_doc("GOOGL" if sym == "GOOG" else sym) or {}).get("metric") or {}
    eps, pe = m.get("epsTTM"), m.get("peTTM")
    return eps * pe if isinstance(eps, (int, float)) and isinstance(pe, (int, float)) else None


class FakeSec:
    """EDGAR getter backed by the fixtures; records ("facts"|"sub"|"tickers", cik) per call."""

    def __init__(self, fail_facts=None, block=False):
        self.calls = []
        self.fail_facts = set(fail_facts or ())
        self.block = block

    def get(self, url, headers):
        if url == TICKERS_URL:
            self.calls.append(("tickers", None))
            return _bytes("company_tickers.json")
        cik = int(re.search(r"CIK(\d{10})", url).group(1))
        sym = SYM_BY_CIK[cik]
        if "companyfacts" in url:
            self.calls.append(("facts", cik))
            if self.block:
                raise urllib.error.HTTPError(url, 403, "Forbidden", {}, None)
            if sym in self.fail_facts:
                raise ValueError(f"broken facts for {sym}")
            return _bytes(f"companyfacts_{sym}.json")
        self.calls.append(("sub", cik))
        return _bytes(f"submissions_{sym}.json")

    def facts_calls(self):
        return [c for k, c in self.calls if k == "facts"]


def fake_finnhub(url, timeout):
    sym = re.search(r"symbol=([^&]+)", url).group(1).replace(".", "-")
    return _fh_doc("GOOGL" if sym == "GOOG" else sym)          # GOOG shares GOOGL's numbers


class FakeQuotes:
    def __init__(self, prices=None):
        self.calls = []
        self.prices = prices

    def __call__(self, schwab_symbols):
        self.calls.append(list(schwab_symbols))
        out = {}
        for s in schwab_symbols:
            canon = s.replace("/", "-")
            p = self.prices.get(canon) if self.prices is not None else fixture_price(canon)
            if p:
                out[s] = {"last_price": p, "quote_time_ms": NOW_MS, "description": f"{canon} desc"}
        return out


def make_svc(tmp_path, *, universe=None, sec=None, now=NOW, quotes=None, sleep=noop):
    sec = sec or FakeSec()
    clock = {"now": now}
    svc = LongTermService(
        directory=tmp_path,
        edgar=EdgarClient(get=sec.get, pace_per_sec=0, sleep=noop),
        finnhub_get=fake_finnhub,
        finnhub_key="test-key",
        quotes_fn=quotes or FakeQuotes(),
        now_fn=lambda: clock["now"],
        sleep=sleep,
        is_etf_fn=lambda s: s == "SPY",
        universe_fn=lambda: list(universe if universe is not None else SYMBOLS + ["SPY", "ZZZZ"]),
    )
    svc._test_sec, svc._test_clock = sec, clock
    return svc


@pytest.fixture
def svc(tmp_path):
    s = make_svc(tmp_path)
    yield s
    s.stop_refresher()


# ---------- refresh pass ----------

def test_refresh_builds_rows_for_ten_fixtures(svc):
    r = svc.refresh(force=True)
    assert {"scored", "total", "fetched", "skipped", "errors"} <= set(r)
    assert r["scored"] >= 8 and r["errors"] == 0
    assert (svc.directory / "summary.json").exists() and (svc.directory / "fundamentals.json").exists()
    summary = json.loads((svc.directory / "summary.json").read_text(encoding="utf-8"))
    assert summary["refreshed_at"] and "AAPL" in summary["rows_by_symbol"]
    assert svc.progress is None
    rec = svc.store.records["AAPL"]
    assert rec["cik"] == 320193 and rec["sic"] == 3571 and rec["ref_price"] == pytest.approx(fixture_price("AAPL"))
    assert {"snapshot", "finnhub", "name", "listing_factor", "fetched_at", "last_seen"} <= set(rec)


def test_refresh_fetches_each_cik_once(tmp_path):
    svc = make_svc(tmp_path, universe=["GOOG", "GOOGL", "AAPL"])
    svc.refresh(force=True)
    calls = svc._test_sec.facts_calls()
    assert calls.count(1652044) == 1 and calls.count(320193) == 1
    rows = {r["symbol"]: r for r in svc.rows(50, {})}
    assert rows["GOOG"]["years"] == rows["GOOGL"]["years"] == 10
    assert rows["GOOG"]["signal"] == rows["GOOGL"]["signal"] and rows["GOOG"]["sticker"] == rows["GOOGL"]["sticker"]


def test_refresh_skips_fresh_tickers(svc):
    svc.refresh(force=True)
    calls_before = len(svc._test_sec.calls)
    r = svc.refresh()
    assert len(svc._test_sec.calls) == calls_before
    assert r["fetched"] == 0 and r["skipped"] >= 10
    svc._test_clock["now"] = NOW + timedelta(days=4)            # copies now older than 3 days
    r = svc.refresh()
    assert r["fetched"] >= 10 and len(svc._test_sec.facts_calls()) > 10


def test_sic_cached_per_cik_never_refetched(svc):
    svc.refresh(force=True)
    subs = [c for k, c in svc._test_sec.calls if k == "sub"]
    svc.refresh(force=True)
    assert [c for k, c in svc._test_sec.calls if k == "sub"] == subs


def test_per_ticker_error_is_counted_and_pass_continues(tmp_path):
    svc = make_svc(tmp_path, sec=FakeSec(fail_facts={"HD"}))
    r = svc.refresh(force=True)
    assert r["errors"] == 1 and r["scored"] >= 7
    assert "broken facts" in svc.store.records["HD"]["error"]
    row = next(x for x in svc.rows(50, {}) if x["symbol"] == "HD")
    assert row["signal"] == "CHECK_DATA" and "data error" in row["reason"]


def test_edgar_block_stops_the_pass(tmp_path):
    svc = make_svc(tmp_path, sec=FakeSec(block=True))
    r = svc.refresh(force=True)
    assert r["blocked"] is True
    assert len(svc._test_sec.facts_calls()) == 3                 # stopped at the first filer (3 x 403)
    assert not any(rec.get("snapshot") for rec in svc.store.records.values())
    assert any(rec.get("error") == "blocked" for rec in svc.store.records.values())


def test_store_saved_every_ten_tickers(tmp_path, monkeypatch):
    svc = make_svc(tmp_path, universe=SYMBOLS + ["ZZZZ", "SPY"])
    saves = []
    real = svc.store.save_records
    monkeypatch.setattr(svc.store, "save_records", lambda: (saves.append(svc.progress and svc.progress["done"]), real())[1])
    svc.refresh(force=True)
    assert 10 in saves and len(saves) >= 2


def test_store_survives_restart_without_refetch(svc, tmp_path):
    svc.refresh(force=True)
    before = {r["symbol"]: r for r in svc.rows(50, {})}
    again = make_svc(tmp_path)
    after = {r["symbol"]: r for r in again.rows(50, {})}
    assert again._test_sec.calls == []
    assert after.keys() == before.keys()
    assert after["AAPL"]["sticker"] == pytest.approx(before["AAPL"]["sticker"])
    assert after["AAPL"]["signal"] == before["AAPL"]["signal"]


# ---------- store ----------

def test_store_write_is_atomic_and_retries(tmp_path, monkeypatch):
    sleeps = []
    store = Store(tmp_path, sleep=sleeps.append)
    store.records["AAPL"] = {"ref_price": 1.0}
    store.save()
    real = os.replace
    fails = {"n": 2}

    def flaky(src, dst):
        if fails["n"] > 0:
            fails["n"] -= 1
            raise PermissionError("locked by a reader")
        return real(src, dst)

    monkeypatch.setattr(service_mod.os, "replace", flaky)
    store.records["AAPL"] = {"ref_price": 2.0}
    store.save_records()
    assert len(sleeps) == 2
    assert json.loads((tmp_path / "fundamentals.json").read_text(encoding="utf-8"))["records"]["AAPL"]["ref_price"] == 2.0
    assert json.loads((tmp_path / "fundamentals.prev.json").read_text(encoding="utf-8"))["records"]["AAPL"]["ref_price"] == 1.0
    assert not list(tmp_path.glob("*.tmp"))

    # five failures in a row: the old file is left whole and the temp file is cleaned up
    fails["n"] = 99
    store.records["AAPL"] = {"ref_price": 3.0}
    with pytest.raises(PermissionError):
        store.save_records()
    assert json.loads((tmp_path / "fundamentals.json").read_text(encoding="utf-8"))["records"]["AAPL"]["ref_price"] == 2.0
    assert not list(tmp_path.glob("*.tmp"))


def test_store_falls_back_to_prev_on_corrupt_file(tmp_path):
    store = Store(tmp_path)
    store.records["AAPL"] = {"ref_price": 1.0}
    store.save()
    store.records["AAPL"] = {"ref_price": 2.0}
    store.save()
    (tmp_path / "fundamentals.json").write_text("{not json", encoding="utf-8")
    again = Store(tmp_path)
    again.load()
    assert again.records["AAPL"]["ref_price"] == 1.0


# ---------- page payload ----------

def test_page_payload_shape_and_live_merge(svc):
    svc.refresh(force=True)
    page = svc.page_payload(50, {"AAPL": {"last_price": 344.17, "quote_time_ms": NOW_MS}})
    row = next(r for r in page["rows"] if r["symbol"] == "AAPL")
    assert set(row) >= {"symbol", "name", "price", "signal", "leaps", "reason", "buyBelow", "leapsBelow",
                        "buyZonePct", "safetyBuy", "sticker", "buffett", "payback", "roic", "big5", "quality",
                        "pe", "debtToEquity", "salesGrowth", "epsGrowth", "fcfGrowth", "marketCap", "filedEnd",
                        "years", "financial", "young"}
    assert page["meta"]["mos"] == 50 and "SPY" in page["meta"]["etfs"]
    assert row["price"] == 344.17 and row["pe"] > 0 and row["filedEnd"] and row["name"] == "Apple Inc."
    assert row["signal"] in {"BUY", "WAIT", "AVOID"} and row["refPrice"] == pytest.approx(fixture_price("AAPL"))


def test_every_row_has_every_key_and_meta_contract(svc):
    svc.refresh(force=True)
    page = svc.page_payload(30, {})
    assert set(ROW_KEYS) == EXPECTED_ROW_KEYS
    for row in page["rows"]:
        assert set(row) == EXPECTED_ROW_KEYS, row["symbol"]
        assert isinstance(row["leaps"], bool) and isinstance(row["financial"], bool) and isinstance(row["young"], bool)
    meta = page["meta"]
    assert set(meta) == {"universe", "scored", "etfs", "noFilings", "refreshedAt", "refreshing", "deferredUntil", "mos"}
    assert isinstance(meta["mos"], int) and meta["mos"] == 30
    assert meta["universe"] == 12 and meta["scored"] >= 8
    assert meta["noFilings"] == ["ZZZZ"] and meta["etfs"] == ["SPY"]
    assert meta["refreshing"] is None and meta["deferredUntil"] is None and meta["refreshedAt"]
    by = {r["symbol"]: r for r in page["rows"]}
    assert by["SPY"]["signal"] == "ETF" and by["ZZZZ"]["signal"] == "NO_FILINGS"
    assert by["JPM"]["financial"] is True and by["HOOD"]["young"] is True
    assert isinstance(svc.page_payload("bogus", {})["meta"]["mos"], int)
    json.dumps(page)                                             # the HTTP layer can serialise it


def test_missing_quote_is_no_price_never_buy(svc):
    svc.refresh(force=True)
    page = svc.page_payload(50, {})
    assert all(r["signal"] != "BUY" and r["leaps"] is False for r in page["rows"])
    assert any(r["signal"] == "NO_PRICE" for r in page["rows"])


def test_price_far_from_reference_is_no_price(svc):
    svc.refresh(force=True)
    ref = fixture_price("AAPL")
    page = svc.page_payload(50, {"AAPL": {"last_price": ref * 0.5, "quote_time_ms": NOW_MS}})
    row = next(r for r in page["rows"] if r["symbol"] == "AAPL")
    assert row["signal"] in ("NO_PRICE", "AVOID") and row["leaps"] is False


def test_low_price_gives_buy_for_a_good_business(svc):
    """No fixture trades near its Safety Buy, so AAPL's stored copy is moved there: reference price
    and Finnhub P/E both re-based to 99% of Safety Buy (price sanity still holds). The service must
    then show BUY with a positive zone %, and the LEAPS flag (price far under 0.8 x sticker)."""
    svc.refresh(force=True)
    base = next(r for r in svc.page_payload(50, {}) ["rows"] if r["symbol"] == "AAPL")
    price = base["safetyBuy"] * 0.99
    with svc.store.lock:
        rec = svc.store.records["AAPL"]
        rec["ref_price"] = price
        rec["finnhub"]["pe_ttm"] = price / rec["finnhub"]["eps_ttm"]
        svc._computed.pop("AAPL")
    row = next(r for r in svc.page_payload(50, {"AAPL": {"last_price": price, "quote_time_ms": NOW_MS}})["rows"]
               if r["symbol"] == "AAPL")
    assert row["signal"] == "BUY", row["reason"]
    assert row["buyZonePct"] == pytest.approx(0.01, abs=1e-6) and row["leaps"] is True
    # the same price at MOS 30 is still a BUY; a stale quote (last week) is NO_PRICE, never BUY
    assert next(r for r in svc.page_payload(30, {"AAPL": {"last_price": price, "quote_time_ms": NOW_MS}})["rows"]
                if r["symbol"] == "AAPL")["signal"] == "BUY"
    stale = (NOW - timedelta(days=7)).timestamp() * 1000.0
    row = next(r for r in svc.page_payload(50, {"AAPL": {"last_price": price, "quote_time_ms": stale}})["rows"]
               if r["symbol"] == "AAPL")
    assert row["signal"] == "NO_PRICE" and row["leaps"] is False and "stale" in row["reason"]


def test_corrupt_snapshot_is_check_data_not_an_exception(svc):
    svc.refresh(force=True)
    with svc.store.lock:
        svc.store.records["BAD"] = {"cik": 1, "snapshot": {"bogus": 1}, "finnhub": None, "name": "Bad Co",
                                    "ref_price": 10.0, "fetched_at": NOW.isoformat(), "last_seen": NOW.isoformat()}
    page = svc.page_payload(50, {"BAD": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
    row = next(r for r in page["rows"] if r["symbol"] == "BAD")
    assert row["signal"] == "CHECK_DATA" and "data error" in row["reason"] and set(row) == EXPECTED_ROW_KEYS
    assert row["leaps"] is False
    assert svc.detail_payload("BAD", 50, None)["checks"] == []


def test_rows_never_raise_even_with_garbage_quotes(svc):
    svc.refresh(force=True)
    garbage = {"AAPL": {"last_price": "abc", "quote_time_ms": None}, "MCD": None, "HD": {"last_price": float("nan")}}
    rows = svc.rows(50, garbage)
    assert {r["symbol"] for r in rows} >= {"AAPL", "MCD", "HD"}
    assert all(r["signal"] != "BUY" for r in rows if r["symbol"] in garbage)


def test_rows_do_not_reread_the_file(svc, monkeypatch):
    svc.refresh(force=True)
    monkeypatch.setattr(svc.store, "load", lambda: (_ for _ in ()).throw(AssertionError("re-read")))
    svc.rows(50, {})
    svc.page_payload(50, {})


# ---------- detail payload ----------

def test_detail_payload_has_explain_and_table(svc):
    svc.refresh(force=True)
    d = svc.detail_payload("AAPL", 50, None)
    assert len(d["years"]) >= 10 and any("sticker" in line.lower() for line in d["explain"])
    assert d["years"][0]["end"] < d["years"][-1]["end"]
    assert set(d["years"][0]) == {"end", "revenue", "netIncome", "eps", "equity", "fcf", "roic"}
    assert d["unit"] == "USD" and d["symbol"] == "AAPL" and d["name"] == "Apple Inc."
    assert len(d["checks"]) == 11 and {"key", "label", "value", "threshold", "passed", "detail"} == set(d["checks"][0])
    assert d["crossChecks"] and all({"label", "ok", "detail"} == set(c) for c in d["crossChecks"])
    labels = {s["label"] for s in d["sources"]}
    assert {"EDGAR CIK", "Latest annual period", "Finnhub fetched", "Listing factor", "SIC"} <= labels
    assert "listingFactor" in d
    json.dumps(d)


def test_detail_payload_unknown_symbol_is_none(svc):
    svc.refresh(force=True)
    assert svc.detail_payload("NOPE", 50, None) is None
    assert svc.detail_payload("SPY", 50, None)["checks"] == []


def test_detail_payload_non_usd_unit(svc):
    svc.refresh(force=True)
    d = svc.detail_payload("ASML", 50, None)
    assert d["unit"] == "EUR" and d["listingFactor"] is not None


# ---------- quotes ----------

def test_quotes_for_maps_brk_b_and_caches(tmp_path):
    quotes = FakeQuotes(prices={"BRK-B": 480.0, "AAPL": 300.0})
    svc = make_svc(tmp_path, quotes=quotes)
    t = {"mono": 100.0}
    svc._mono = lambda: t["mono"]
    out = svc.quotes_for(["brk.b", "AAPL"])
    assert quotes.calls == [["BRK/B", "AAPL"]]
    assert out["BRK-B"] == {"last_price": 480.0, "quote_time_ms": NOW_MS, "description": "BRK-B desc"}
    svc.quotes_for(["BRK-B"])
    assert len(quotes.calls) == 1                                 # cached for 45 s (final review fix 8)
    t["mono"] += 46
    svc.quotes_for(["BRK-B"])
    assert len(quotes.calls) == 2


def test_quotes_for_chunks_and_survives_errors(tmp_path):
    calls = []

    def boom(symbols):
        calls.append(len(symbols))
        raise RuntimeError("schwab down")

    svc = make_svc(tmp_path, quotes=boom)
    assert svc.quotes_for([f"T{i}" for i in range(250)]) == {}
    assert calls == [100, 100, 50]


# ---------- refresher gate ----------

def test_request_refresh_deferred_in_market_hours(svc):
    assert svc.request_refresh(now=ET_TIME(2026, 11, 6, 10, 0)) == {"deferredUntil": "16:05 ET"}
    assert svc.page_payload(50, {})["meta"]["deferredUntil"] == "16:05 ET"
    assert svc._due(ET_TIME(2026, 11, 6, 15, 59)) is False
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 6, 9, 0).isoformat()   # store fresh
    assert svc._due(ET_TIME(2026, 11, 6, 16, 6)) is True                         # the deferral is due


def test_request_refresh_running_and_started(tmp_path):
    gate, entered = threading.Event(), threading.Event()
    inner = FakeQuotes()

    def slow_quotes(symbols):
        entered.set()
        gate.wait(10)
        return inner(symbols)

    svc = make_svc(tmp_path, quotes=slow_quotes)
    try:
        t = threading.Thread(target=svc.refresh, kwargs={"force": True}, daemon=True)
        t.start()
        assert entered.wait(10)
        assert svc.request_refresh(now=NOW) == {"running": True}
        assert svc.progress is not None and svc.page_payload(50, {})["meta"]["refreshing"] is not None
        gate.set()
        t.join(30)

        ran = threading.Event()
        svc.refresh = lambda symbols=None, *, force=False, _gated=False: ran.set() or {}
        assert svc.request_refresh(now=NOW) == {"started": True}
        assert ran.wait(10)                                       # ran on the refresher thread
    finally:
        gate.set()
        svc.stop_refresher()


def test_refresher_runs_nightly_window_only(svc):
    calls = []
    svc.refresh = lambda symbols=None, *, force=False, _gated=False: calls.append(_gated) or {}
    # empty store, 10:00 ET Tuesday -> not due (market hours)
    assert svc._tick(ET_TIME(2026, 11, 10, 10, 0)) is False and calls == []
    # refreshed Monday 17:00 (10 h ago): 03:05 Tuesday is the nightly window -> due
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 9, 17, 0).isoformat()
    assert svc._due(ET_TIME(2026, 11, 10, 3, 5)) is True
    assert svc._due(ET_TIME(2026, 11, 10, 3, 35)) is False       # outside the window, < 24 h old
    # already ran tonight -> not due again
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 10, 3, 1).isoformat()
    assert svc._due(ET_TIME(2026, 11, 10, 3, 20)) is False
    # 17:00 Tuesday with a store 25 h old -> due; 1 h old -> not
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 9, 16, 0).isoformat()
    assert svc._tick(ET_TIME(2026, 11, 10, 17, 0)) is True and calls == [True]
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 10, 16, 0).isoformat()
    assert svc._due(ET_TIME(2026, 11, 10, 17, 0)) is False
    # stale store during market hours -> still not due; weekends have no market hours
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 1, 16, 0).isoformat()
    assert svc._due(ET_TIME(2026, 11, 10, 12, 0)) is False
    assert svc._due(ET_TIME(2026, 11, 8, 12, 0)) is True


def test_start_refresher_is_idempotent(svc):
    svc.start_refresher()
    first = svc._thread
    svc.start_refresher()
    assert svc._thread is first and first.is_alive() and first.daemon
    assert sum(1 for t in threading.enumerate() if t.name == "long-term-refresher" and t.is_alive()) >= 1
    svc.stop_refresher()
    assert not first.is_alive()


def test_departed_ticker_kept_30_days(svc):
    svc.refresh(force=True)
    with svc.store.lock:
        svc.store.records["AAPL"]["last_seen"] = (NOW - timedelta(days=29)).isoformat()
        svc.store.records["HD"]["last_seen"] = (NOW - timedelta(days=31)).isoformat()
    svc.drop_departed(NOW)
    assert "AAPL" in svc.store.records and "HD" not in svc.store.records
    assert all(r["symbol"] != "HD" for r in svc.rows(50, {}))


def test_departed_ticker_stays_on_page_until_dropped(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    svc2 = make_svc(tmp_path, universe=[s for s in SYMBOLS if s != "MCD"])
    svc2.refresh()
    assert "MCD" in {r["symbol"] for r in svc2.rows(50, {})}
    assert svc2.store.records["MCD"]["last_seen"] == NOW.isoformat()


def test_service_does_not_import_api_server():
    src = Path(service_mod.__file__).read_text(encoding="utf-8")
    assert "api_server" not in src and "print(" not in src


def test_service_instance_singleton(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_LONG_TERM_DIR", str(tmp_path / "lt"))
    monkeypatch.setattr(service_mod, "_INSTANCE", None)
    a = service_mod.service_instance()
    assert a is service_mod.service_instance() and a.directory == tmp_path / "lt"


# ---------- review fixes (2026-11-06) ----------

def test_reloaded_store_recomputes_identically(svc, tmp_path):
    """JSON turns Snapshot.ttm_ends tuples into lists; a reloaded record must still compute."""
    svc.refresh(force=True)
    in_pass = {r["symbol"]: r for r in svc.rows(50, {})}
    again = make_svc(tmp_path)
    today = NOW.date()
    for sym, rec in again.store.records.items():
        c = again._compute(rec, today)
        assert "error" not in c, (sym, c)
    result = {"scored": 0}
    again._rebuild_summary(None, None, NOW.isoformat(), today, result, {})
    rows = {r["symbol"]: r for r in again.rows(50, {})}
    assert rows["AAPL"]["sticker"] == pytest.approx(in_pass["AAPL"]["sticker"])
    assert not any("data error" in (r["reason"] or "") for r in rows.values())
    assert result["scored"] == sum(1 for r in in_pass.values() if r["signal"] in ("BUY", "WAIT", "AVOID", "NO_PRICE"))
    # and a later (non-forced) pass after the restart keeps a good summary
    again._test_clock["now"] = NOW + timedelta(hours=1)
    again.refresh()
    assert not any("data error" in (r["reason"] or "") for r in again.rows(50, {}))


def test_broken_record_is_not_fresh(svc):
    svc.refresh(force=True)
    with svc.store.lock:
        svc.store.records["AAPL"]["snapshot"] = None                       # unloadable copy
        svc.store.records["HD"]["snapshot"] = {"bogus": 1}                 # computes to an error
        svc._computed.pop("HD")
    svc.rows(50, {})                                                       # HD's computed entry -> error
    before = len(svc._test_sec.facts_calls())
    r = svc.refresh()
    assert r["fetched"] == 2 and len(svc._test_sec.facts_calls()) == before + 2
    assert svc.store.records["AAPL"]["snapshot"] and "m" in svc._computed["HD"]
    assert svc.store.records["ZZZZ"]["cik"] is None and svc._is_fresh(svc.store.records["ZZZZ"], NOW, "ZZZZ")


def test_missing_quote_keeps_previous_reference(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    ref, lf = svc.store.records["AAPL"]["ref_price"], svc.store.records["AAPL"]["listing_factor"]
    assert ref and lf is not None
    svc2 = make_svc(tmp_path, quotes=FakeQuotes(prices={}))               # Schwab returned nothing tonight
    svc2.refresh(force=True)
    assert svc2.store.records["AAPL"]["ref_price"] == ref
    assert svc2.store.records["AAPL"]["listing_factor"] == lf
    # the 25% guard therefore still applies
    row = next(r for r in svc2.page_payload(50, {"AAPL": {"last_price": ref * 0.5, "quote_time_ms": NOW_MS}})["rows"]
               if r["symbol"] == "AAPL")
    assert row["signal"] != "BUY"


def test_store_load_drops_junk_records(tmp_path):
    (tmp_path / "fundamentals.json").write_text(json.dumps(
        {"records": {"HD": ["junk"], "AAPL": {"ref_price": 1.0}}, "ciks": {"1": "junk", "2": {"sic": 1}}}),
        encoding="utf-8")
    store = Store(tmp_path)
    store.load()
    assert set(store.records) == {"AAPL"} and set(store.ciks) == {"2"}
    (tmp_path / "fundamentals.json").write_text(json.dumps({"records": ["x"], "ciks": 5}), encoding="utf-8")
    store.load()
    assert store.records == {} and store.ciks == {}
    svc = make_svc(tmp_path)                                               # a pass over the junk works
    assert svc.refresh(force=True)["errors"] == 0


def test_deferred_refresh_now_runs_forced(svc):
    calls = []
    svc.refresh = lambda symbols=None, *, force=False, _gated=False: calls.append(force) or {}
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 6, 9, 0).isoformat()
    svc.request_refresh(now=ET_TIME(2026, 11, 6, 10, 0))
    assert svc._tick(ET_TIME(2026, 11, 6, 15, 0)) is False
    assert svc._tick(ET_TIME(2026, 11, 6, 16, 6)) is True and calls == [True]
    assert svc.page_payload(50, {})["meta"]["deferredUntil"] is None
    # a nightly / catch-up pass is not forced
    svc.store.summary["refreshed_at"] = ET_TIME(2026, 11, 5, 9, 0).isoformat()
    assert svc._tick(ET_TIME(2026, 11, 6, 17, 0)) is True and calls == [True, False]


def test_finnhub_failure_keeps_edgar_snapshot(tmp_path, monkeypatch):
    svc = make_svc(tmp_path, universe=["AAPL"])

    def boom(*a, **k):
        raise TimeoutError("finnhub timed out")

    monkeypatch.setattr(service_mod, "fetch_metrics", boom)
    r = svc.refresh(force=True)
    rec = svc.store.records["AAPL"]
    assert r["errors"] == 0 and rec["snapshot"] and rec["finnhub"] is None and "error" not in rec
    row = next(x for x in svc.rows(50, {}) if x["symbol"] == "AAPL")
    assert "Finnhub: no data" in row["reason"]


def test_prev_copy_taken_once_per_pass(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    first = json.loads((tmp_path / "fundamentals.json").read_text(encoding="utf-8"))
    svc._test_clock["now"] = NOW + timedelta(days=4)
    svc.refresh(force=True)                                                # saves at ticker 10 and at the end
    prev = json.loads((tmp_path / "fundamentals.prev.json").read_text(encoding="utf-8"))
    assert prev == first                                                   # the pre-pass copy, not ticker 10's


def test_store_readers_never_wait_on_disk(tmp_path, monkeypatch):
    store = Store(tmp_path)
    store.records["AAPL"] = {"ref_price": 1.0}
    in_replace, release = threading.Event(), threading.Event()
    real = os.replace

    def slow_replace(src, dst):
        in_replace.set()
        release.wait(10)
        return real(src, dst)

    monkeypatch.setattr(service_mod.os, "replace", slow_replace)
    t = threading.Thread(target=store.save_records, daemon=True)
    t.start()
    try:
        assert in_replace.wait(10)
        assert store.lock.acquire(timeout=1), "reader blocked behind the disk write"
        store.lock.release()
    finally:
        release.set()
        t.join(10)
    assert json.loads((tmp_path / "fundamentals.json").read_text(encoding="utf-8"))["records"]["AAPL"]["ref_price"] == 1.0


# ---------- final review fixes ----------

def _row_of(svc, sym, quotes=None, mos=50):
    return next(r for r in svc.page_payload(mos, quotes or {})["rows"] if r["symbol"] == sym)


def test_listing_factor_drift_turns_row_check_data(svc):
    # the drift check is for rows that carry a listing factor (ASML: EUR 20-F); audit fix M2 (B8)
    # moved the USD 10-K case to test_usd_10k_filer_stores_listing_factor_one_and_never_drifts
    svc.refresh(force=True)
    rec = svc.store.records["ASML"]
    assert rec["prior_listing_factor"] is None                       # first night: no baseline, no check
    assert "listing factor" not in (_row_of(svc, "ASML")["reason"] or "")
    lf = rec["listing_factor"]
    with svc.store.lock:
        rec["listing_factor"] = lf / 1.31                            # last night's factor was 31% lower
    svc.refresh(force=True)
    assert svc.store.records["ASML"]["prior_listing_factor"] == pytest.approx(lf / 1.31)
    row = _row_of(svc, "ASML", {"ASML": {"last_price": fixture_price("ASML"), "quote_time_ms": NOW_MS}})
    assert row["signal"] == "CHECK_DATA" and row["reason"] == "Check data: listing factor moved 31%"
    assert row["buyBelow"] is None and row["leaps"] is False
    svc.refresh(force=True)                                          # a steady factor the next night clears it
    assert "listing factor" not in (_row_of(svc, "ASML")["reason"] or "")


def test_usd_10k_filer_stores_listing_factor_one_and_never_drifts(tmp_path):
    # audit fix M2 (B8): a USD 10-K filer's stored factor is 1.0 (R39 pins it), never the raw
    # price / (EPS x P/E) ratio (HSY 1.26, CLS 0.82 in the live store), so a later night cannot see a
    # false "Check data: listing factor moved"; a 20-F filer (ASML) keeps the raw ratio and the check
    prices = {s: fixture_price(s) for s in SYMBOLS if fixture_price(s)}
    prices["AAPL"] *= 1.3                                            # Schwab 30% above Finnhub's EPS x P/E
    prices["ASML"] *= 1.3
    quotes = FakeQuotes(prices)
    svc = make_svc(tmp_path, quotes=quotes)
    clock = {"mono": 0.0}
    svc._mono = lambda: clock["mono"]
    try:
        svc.refresh(force=True)
        aapl, asml = svc.store.records["AAPL"], svc.store.records["ASML"]
        assert aapl["listing_factor"] == 1.0 and aapl["ref_price"] == pytest.approx(prices["AAPL"])
        assert asml["listing_factor"] == pytest.approx(1.3)
        with svc.store.lock:
            aapl["listing_factor"] = 1.26                            # a raw ratio the old code stored (HSY)
        quotes.prices["AAPL"] = fixture_price("AAPL")                # tonight the price is back at EPS x P/E
        quotes.prices["ASML"] = fixture_price("ASML")
        clock["mono"] += 60.0                                          # past the 45 s quote cache: tonight's quotes
        svc.refresh(force=True)
        aapl, asml = svc.store.records["AAPL"], svc.store.records["ASML"]
        assert aapl["prior_listing_factor"] == pytest.approx(1.26) and aapl["listing_factor"] == 1.0
        assert asml["prior_listing_factor"] == pytest.approx(1.3) and asml["listing_factor"] == 1.0
        q = {s: {"last_price": fixture_price(s), "quote_time_ms": NOW_MS} for s in ("AAPL", "ASML")}
        assert "listing factor" not in (_row_of(svc, "AAPL", q)["reason"] or "")
        assert _row_of(svc, "ASML", q)["reason"] == "Check data: listing factor moved 23%"
    finally:
        svc.stop_refresher()


def test_old_data_is_served_stale_at_request_time(svc):
    svc.refresh(force=True)
    later = NOW + timedelta(days=5)
    svc._test_clock["now"] = later
    row = _row_of(svc, "AAPL", {"AAPL": {"last_price": fixture_price("AAPL"), "quote_time_ms": later.timestamp() * 1000}})
    assert row["signal"] == "STALE" and row["reason"] == "Stale: last refresh 5 days ago"
    assert row["leaps"] is False
    assert all(row[k] is None for k in ("buyBelow", "leapsBelow", "safetyBuy", "buyZonePct"))
    svc._test_clock["now"] = NOW + timedelta(days=3)                  # 3 days is not stale
    assert _row_of(svc, "AAPL")["signal"] != "STALE"


def test_stale_beats_a_cached_buy(svc):
    svc.refresh(force=True)
    price = _row_of(svc, "AAPL")["safetyBuy"] * 0.99
    with svc.store.lock:
        rec = svc.store.records["AAPL"]
        rec["ref_price"] = price
        rec["finnhub"]["pe_ttm"] = price / rec["finnhub"]["eps_ttm"]
        svc._computed.pop("AAPL")
    assert _row_of(svc, "AAPL", {"AAPL": {"last_price": price, "quote_time_ms": NOW_MS}})["signal"] == "BUY"
    later = NOW + timedelta(days=5)
    svc._test_clock["now"] = later
    row = _row_of(svc, "AAPL", {"AAPL": {"last_price": price, "quote_time_ms": later.timestamp() * 1000}})
    assert row["signal"] == "STALE" and row["leaps"] is False and row["buyBelow"] is None


def test_unreadable_summary_serves_computing_then_rebuilds(tmp_path, monkeypatch):
    make_svc(tmp_path).refresh(force=True)
    for name in ("summary.json", "summary.prev.json"):
        p = tmp_path / name
        if p.exists():
            p.write_text("{broken", encoding="utf-8")
    svc = make_svc(tmp_path)
    assert svc.store.records and svc._pending_rebuild
    calls = []
    real = svc._compute
    monkeypatch.setattr(svc, "_compute", lambda rec, today: calls.append(1) or real(rec, today))
    rows = {r["symbol"]: r for r in svc.rows(50, {})}
    assert calls == []                                              # nothing computed on the request
    assert rows["AAPL"]["signal"] == "CHECK_DATA" and rows["AAPL"]["reason"] == "computing"
    assert all(r["reason"] == "computing" for r in rows.values())
    svc._rebuild_summary()                                          # what the refresher thread runs first
    assert not svc._pending_rebuild and calls
    rows = {r["symbol"]: r for r in svc.rows(50, {})}
    assert rows["AAPL"]["signal"] in {"NO_PRICE", "WAIT", "AVOID", "BUY"} and rows["AAPL"]["sticker"]
    assert svc.store.summary["refreshed_at"] == svc.store.records["AAPL"]["fetched_at"]
    assert (tmp_path / "summary.json").exists()


def test_known_symbols_is_records_plus_etfs_from_memory(svc, monkeypatch):
    svc.refresh(force=True)
    monkeypatch.setattr(svc, "_universe_fn", lambda: (_ for _ in ()).throw(AssertionError("disk")))
    assert svc.known_symbols() == sorted(set(SYMBOLS) | {"SPY", "ZZZZ"})


def test_quote_cache_serves_45_seconds(tmp_path):
    quotes = FakeQuotes(prices={"AAPL": 300.0})
    svc = make_svc(tmp_path, quotes=quotes)
    t = {"now": 1000.0}
    svc._mono = lambda: t["now"]
    svc.quotes_for(["AAPL"])
    t["now"] += 44.0
    svc.quotes_for(["AAPL"])
    assert len(quotes.calls) == 1
    t["now"] += 2.0
    svc.quotes_for(["AAPL"])
    assert len(quotes.calls) == 2


def test_non_usd_filer_has_no_market_cap_or_payback(svc):
    svc.refresh(force=True)
    for sym in ("TSM", "ASML"):
        row = _row_of(svc, sym)
        assert row["marketCap"] is None and row["payback"] is None, sym
    assert _row_of(svc, "AAPL")["marketCap"]                        # USD filers unchanged


# ---------- first nightly audit (2026-11-06): CIK overrides (R20) ----------

class XomSec(FakeSec):
    """company_tickers.json maps XOM to ExxonMobil Holdings (2115436: the 2026 10-Qs, no history);
    the historical filer is 34088 (CIK_OVERRIDES). 34088 is served from the trimmed XOM fixture,
    2115436 from the XOM2 fixture (R42). NEE stands in with the same two fixtures: primary 753308 =
    the XOM history, extra 37634 = XOM2, of which only the Q2 2026 accession is in NEE's own
    submissions index. XOMX is a second ticker on XOM's CIK (never merged)."""

    FACTS = {34088: "XOM", 2115436: "XOM2", 753308: "XOM", 37634: "XOM2"}
    Q2_ACCN, Q3_ACCN = "0000034088-26-000093", "0002115436-26-000020"     # the two 10-Qs in XOM2

    def __init__(self, fail_extra=False):
        super().__init__()
        self.fail_extra = fail_extra

    def get(self, url, headers):
        if url == TICKERS_URL:
            self.calls.append(("tickers", None))
            return json.dumps({"0": {"cik_str": 2115436, "ticker": "XOM", "title": "ExxonMobil Holdings Corp"},
                               "1": {"cik_str": 753308, "ticker": "NEE", "title": "NEXTERA ENERGY INC"},
                               "2": {"cik_str": 34088, "ticker": "XOMX", "title": "second ticker on 34088"}}).encode()
        cik = int(re.search(r"CIK(\d{10})", url).group(1))
        if cik not in self.FACTS:
            return super().get(url, headers)
        if "companyfacts" in url:
            self.calls.append(("facts", cik))
            if self.fail_extra and cik in (2115436, 37634):
                raise ValueError(f"extra CIK {cik} facts unavailable")
            return _bytes(f"companyfacts_{self.FACTS[cik]}.json")
        self.calls.append(("sub", cik))
        return json.dumps({"name": f"filer {cik}", "sic": "2911", "sicDescription": "Petroleum Refining",
                           "filings": {"recent": {"accessionNumber": [self.Q2_ACCN, "0000753308-26-000001"]}}}).encode()


def test_cik_override_wins_over_the_sec_ticker_map(tmp_path):
    from long_term.symbols import CIK_OVERRIDES
    assert CIK_OVERRIDES["XOM"] == 34088
    sec = XomSec()
    svc = make_svc(tmp_path, universe=["XOM"], sec=sec)
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 1 and r["errors"] == 0
        assert svc.store.records["XOM"]["cik"] == 34088
        # re-pinned under R42 (fix round M6): the override still names the primary CIK, and the
        # successor CIK is fetched as XOM's extra CIK (EXTRA_CIKS) for the 2026 10-Qs, never as primary
        assert sec.facts_calls() == [34088, 2115436]
        assert svc.store.records["XOM"]["snapshot"]["cik"] == 34088
    finally:
        svc.stop_refresher()


# ---------- R42 (fix round M6): facts split across CIKs are merged in the pass ----------

def test_xom_merges_the_successor_cik_facts(tmp_path):
    from long_term.edgar import EXTRA_CIKS
    assert EXTRA_CIKS["XOM"] == (2115436,)
    sec = XomSec()
    svc = make_svc(tmp_path, universe=["XOM"], sec=sec)
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 1 and r["errors"] == 0
        assert sec.facts_calls() == [34088, 2115436]
        snap = svc.store.records["XOM"]["snapshot"]
        assert snap["cik"] == 34088 and snap["latest_annual_end"] == "2025-12-31"
        assert snap["latest_period_end"] == "2026-09-30"                 # 34088 alone stops at 2026-03-31
        assert snap["ttm"]["net_income"] == 40622000000.0 and tuple(snap["ttm_ends"]["net_income"]) == ("2026-09-30", False)
        row = _row_of(svc, "XOM")                                        # scored (the 34088 copy alone was STALE)
        assert row["signal"] == "AVOID" and row["reason"] == "ROIC 14% < 15%" and row["years"] == 10
    finally:
        svc.stop_refresher()


def test_merged_facts_are_never_reused_for_a_symbol_sharing_the_primary_cik(tmp_path):
    sec = XomSec()
    svc = make_svc(tmp_path, universe=["XOM", "XOMX"], sec=sec)
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 2 and r["errors"] == 0
        assert sec.facts_calls() == [34088, 2115436, 34088]              # XOMX re-fetches the primary alone
        assert svc.store.records["XOM"]["snapshot"]["latest_period_end"] == "2026-09-30"
        assert svc.store.records["XOMX"]["snapshot"]["latest_period_end"] == "2026-03-31"
        assert _row_of(svc, "XOMX")["signal"] == "STALE" and _row_of(svc, "XOM")["signal"] == "AVOID"
    finally:
        svc.stop_refresher()


def test_nee_takes_only_its_own_accessions_from_the_extra_cik(tmp_path):
    from long_term.edgar import EXTRA_CIKS, NEEDS_ACCESSION_FILTER
    assert EXTRA_CIKS["NEE"] == (37634,) and "NEE" in NEEDS_ACCESSION_FILTER
    sec = XomSec()
    svc = make_svc(tmp_path, universe=["NEE"], sec=sec)
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 1 and r["errors"] == 0
        assert sec.facts_calls() == [753308, 37634]
        assert [c for k, c in sec.calls if k == "sub"].count(753308) >= 1 and 37634 not in [c for k, c in sec.calls if k == "sub"]
        snap = svc.store.records["NEE"]["snapshot"]
        assert snap["cik"] == 753308 and snap["latest_annual_end"] == "2025-12-31"
        assert snap["latest_period_end"] == "2026-06-30"                 # Q2 accession allowed, Q3 (FPL's) not
    finally:
        svc.stop_refresher()


def test_extra_cik_failure_keeps_the_primary_facts(tmp_path):
    sec = XomSec(fail_extra=True)
    svc = make_svc(tmp_path, universe=["XOM", "NEE"], sec=sec)
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 2 and r["errors"] == 0                   # the ticker is never aborted
        assert sorted(sec.facts_calls()) == [34088, 37634, 753308, 2115436]
        for sym in ("XOM", "NEE"):
            rec = svc.store.records[sym]
            assert "error" not in rec and rec["snapshot"]["latest_period_end"] == "2026-03-31", sym
    finally:
        svc.stop_refresher()


# ---------- "no data" fix (2026-11-07) ----------

def _synthetic_record(n=2, **latest):
    """A stored record built from the synthetic snapshot of test_long_term_metrics."""
    import dataclasses
    from test_long_term_metrics import _set_latest, synth, synth_fh
    s = synth(n=n)
    if latest:
        _set_latest(s, **latest)
    eps = s.annual[s.latest_annual_end]["eps"]
    f = synth_fh(s, eps_ttm=eps, eps_incl_ttm=eps, pe_ttm=None if eps <= 0 else 20.0)
    s.symbol = f.symbol = "SYN"
    return {"cik": 1, "name": "Synthetic Co", "sic": 3571, "snapshot": dataclasses.asdict(s),
            "finnhub": dataclasses.asdict(f), "ref_price": 10.0, "listing_factor": None,
            "prior_listing_factor": None, "fetched_at": NOW.isoformat(), "last_seen": NOW.isoformat()}


def test_loss_making_short_history_row_is_avoid_not_no_data(svc):
    with svc.store.lock:
        svc.store.records["SYN"] = _synthetic_record(n=2, net_income=-5.0, eps=-0.5)
    row = _row_of(svc, "SYN", {"SYN": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
    assert row["signal"] == "AVOID" and row["reason"] == "losing money" and row["years"] == 2
    assert row["leaps"] is False and row["buyBelow"] is None and row["safetyBuy"] is None
    assert set(row) == EXPECTED_ROW_KEYS
    # a profitable two-year company stays SHORT_HISTORY
    with svc.store.lock:
        svc.store.records["SYN"] = _synthetic_record(n=2)
        svc._computed.pop("SYN", None)
    row = _row_of(svc, "SYN", {"SYN": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
    assert row["signal"] == "SHORT_HISTORY" and row["reason"] == "Short history (2 years)"


def test_three_year_row_is_young_wait_with_no_buy_level(svc):
    with svc.store.lock:
        svc.store.records["SYN"] = _synthetic_record(n=3)
    row = _row_of(svc, "SYN", {"SYN": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
    assert row["signal"] in ("WAIT", "AVOID") and row["years"] == 3 and row["young"] is True
    assert row["leaps"] is False and row["buyBelow"] is None and row["leapsBelow"] is None


def test_etn_is_classified_as_etf_and_never_fetched(tmp_path):
    from long_term.service import ETN_SYMBOLS
    assert {"VXX", "VXZ"} <= ETN_SYMBOLS
    sec = FakeSec()
    svc = make_svc(tmp_path, universe=["AAPL", "VXX", "VXZ"])
    svc._test_sec = sec
    try:
        r = svc.refresh(force=True)
        assert r["fetched"] == 1
        assert "VXX" not in svc.store.records and "VXZ" not in svc.store.records
        assert set(svc.store.summary["etfs"]) >= {"VXX", "VXZ"}
        page = svc.page_payload(50, {})
        by = {row["symbol"]: row for row in page["rows"]}
        assert by["VXX"]["signal"] == "ETF" and by["VXX"]["reason"] == "ETF" and "VXX" in page["meta"]["etfs"]
    finally:
        svc.stop_refresher()


def test_stored_etn_record_is_served_as_etf_until_dropped(tmp_path):
    # VXX was fetched as Barclays Bank PLC before the rule: the stale record must not score a bank
    svc = make_svc(tmp_path, universe=["AAPL", "VXX"])
    try:
        with svc.store.lock:
            svc.store.records["VXX"] = dict(_synthetic_record(n=10), name="BARCLAYS BANK PLC", sic=6029)
        svc.refresh(force=True)
        row = _row_of(svc, "VXX", {"VXX": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
        assert row["signal"] == "ETF" and row["reason"] == "ETF"
    finally:
        svc.stop_refresher()


def test_departed_etn_record_is_served_as_etf(tmp_path):
    # VXX left the universe (watchlist edit) but its record is kept for KEEP_DAYS: the ETN list
    # still makes it an ETF row, never a bank scored as "Short history"
    svc = make_svc(tmp_path, universe=["AAPL"])
    try:
        with svc.store.lock:
            svc.store.records["VXX"] = dict(_synthetic_record(n=10), name="BARCLAYS BANK PLC", sic=6029)
        svc.refresh(force=True)
        assert "VXX" not in set(svc.store.summary.get("etfs") or [])
        row = _row_of(svc, "VXX", {"VXX": {"last_price": 10.0, "quote_time_ms": NOW_MS}})
        assert row["signal"] == "ETF" and row["reason"] == "ETF"
    finally:
        svc.stop_refresher()
