"""Market cap for the Sector heatmap panel (trader ask, 2026-11-03).

The panel sizes watchlist tiles by market cap. The cap is read off the same
Finnhub profile2 response the industry lookup already makes, cached next to the
industry, and filled by a small budgeted pass -- never on the 8s request path.
No network in these tests: every provider call goes through a fake `get`.
"""
from __future__ import annotations

import json

import pytest

from momx import industry_lookup


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setenv(industry_lookup.CACHE_PATH_ENV, str(tmp_path / "cache.json"))
    monkeypatch.setenv("FINNHUB_API_KEY", "test-key")
    monkeypatch.delenv("FMP_API_KEY", raising=False)
    industry_lookup.clear_memory_cache()
    yield
    industry_lookup.clear_memory_cache()


def _finnhub(profiles):
    calls = []

    def get(url, timeout):
        calls.append(url)
        symbol = url.split("symbol=")[1].split("&")[0]
        value = profiles.get(symbol)
        if isinstance(value, Exception):
            raise value
        return value or {}

    get.calls = calls
    return get


def _write_cache(entries):
    industry_lookup.cache_path().write_text(json.dumps(entries), encoding="utf-8")
    industry_lookup.clear_memory_cache()


def test_finnhub_cap_is_converted_from_millions_and_cached():
    get = _finnhub({"NVDA": {"finnhubIndustry": "Semiconductors", "marketCapitalization": 4_200_000}})
    assert industry_lookup.resolve_market_caps(["NVDA"], get=get) == 1
    profile = industry_lookup.cached_profiles(["NVDA"])["NVDA"]
    assert profile["marketCap"] == pytest.approx(4.2e12)
    assert profile["industry"] == "Semis"  # hand map still wins
    stored = json.loads(industry_lookup.cache_path().read_text(encoding="utf-8"))
    assert stored["NVDA"]["marketCap"] == pytest.approx(4.2e12)
    assert "marketCapAt" in stored["NVDA"]


def test_fresh_cap_is_not_refetched():
    get = _finnhub({"ZZZQ": {"finnhubIndustry": "Banking", "marketCapitalization": 10}})
    industry_lookup.resolve_market_caps(["ZZZQ"], get=get)
    industry_lookup.clear_memory_cache()  # and it survives a fresh process
    assert industry_lookup.resolve_market_caps(["ZZZQ"], get=get) == 0
    assert len(get.calls) == 1


def test_provider_answer_with_no_cap_is_cached_but_a_failure_is_not():
    get = _finnhub({"SOXL": {}, "FLAKY": RuntimeError("timeout")})
    assert industry_lookup.resolve_market_caps(["SOXL", "FLAKY"], get=get) == 1
    stored = json.loads(industry_lookup.cache_path().read_text(encoding="utf-8"))
    assert stored["SOXL"]["marketCap"] is None      # real "nothing": not refetched for a week
    assert "FLAKY" not in stored                     # outage: retried next pass
    industry_lookup.resolve_market_caps(["SOXL", "FLAKY"], get=get)
    assert [url for url in get.calls if "SOXL" in url].__len__() == 1


def test_old_cache_entries_without_a_cap_still_load_and_get_one():
    _write_cache({"ABCD": {"industry": "Banks", "fetchedAt": "2026-08-28T00:00:00Z"}})
    assert industry_lookup.cached_profiles(["ABCD"])["ABCD"] == {"industry": "Banks", "marketCap": None}
    get = _finnhub({"ABCD": {"finnhubIndustry": "Insurance", "marketCapitalization": 5}})
    industry_lookup.resolve_market_caps(["ABCD"], get=get)
    profile = industry_lookup.cached_profiles(["ABCD"])["ABCD"]
    assert profile["industry"] == "Banks"            # the cached industry is not overwritten
    assert profile["marketCap"] == pytest.approx(5e6)


def test_stale_caps_refresh_after_never_fetched_ones():
    _write_cache({
        "OLD": {"industry": "Banks", "marketCap": 1.0, "marketCapAt": "2020-01-01T00:00:00Z"},
    })
    get = _finnhub({"OLD": {"marketCapitalization": 2}, "NEW": {"marketCapitalization": 3}})
    industry_lookup.resolve_market_caps(["OLD", "NEW"], budget=1, get=get)
    assert len(get.calls) == 1 and "symbol=NEW" in get.calls[0]
    industry_lookup.resolve_market_caps(["OLD", "NEW"], budget=1, get=get)
    assert industry_lookup.cached_profiles(["OLD"])["OLD"]["marketCap"] == pytest.approx(2e6)


def test_budget_caps_provider_calls_per_pass():
    get = _finnhub({})
    industry_lookup.resolve_market_caps([f"S{i}" for i in range(50)], budget=7, get=get)
    assert len(get.calls) == 7


def test_resolve_also_stores_the_cap_it_already_received():
    get = _finnhub({"ZZZQ": {"finnhubIndustry": "Banking", "marketCapitalization": 12}})
    industry_lookup.resolve(["ZZZQ"], get=get)
    assert industry_lookup.cached_profiles(["ZZZQ"])["ZZZQ"]["marketCap"] == pytest.approx(12e6)
    assert industry_lookup.resolve_market_caps(["ZZZQ"], get=get) == 0
    assert len(get.calls) == 1


def test_cached_profiles_never_touches_the_network(monkeypatch):
    monkeypatch.setattr(industry_lookup, "_http_get", lambda *a, **k: pytest.fail("network on request path"))
    out = industry_lookup.cached_profiles(["nvda", "UNKNOWN", "nvda", ""])
    assert list(out) == ["NVDA", "UNKNOWN"]
    assert out["UNKNOWN"] == {"industry": None, "marketCap": None}


def test_fill_is_rate_limited_and_skipped_when_nothing_is_missing(monkeypatch):
    started = []
    monkeypatch.setattr(industry_lookup, "resolve_market_caps", lambda symbols: 0)
    assert industry_lookup.schedule_market_cap_fill(["AAA"], start_thread=started.append) is True
    started[0]()  # run the pass; it releases the "running" flag
    # a second poll inside the interval does not start another pass
    assert industry_lookup.schedule_market_cap_fill(["AAA"], start_thread=started.append) is False
    assert len(started) == 1

    industry_lookup.clear_memory_cache()
    _write_cache({"BBB": {"industry": "Banks", "marketCap": 1.0, "marketCapAt": industry_lookup._now_stamp()}})
    assert industry_lookup.schedule_market_cap_fill(["BBB"], start_thread=started.append) is False


def test_watchlist_rows_get_profiles_without_mutating_the_quote_cache(monkeypatch):
    import api_server

    _write_cache({"ZZZQ": {"industry": "Banks", "marketCap": 9e9, "marketCapAt": industry_lookup._now_stamp()}})
    monkeypatch.setattr(industry_lookup, "schedule_market_cap_fill", lambda symbols: False)
    cached_rows = [
        {"symbol": "NVDA", "lastPrice": 1.0, "changePct": 3.1},
        {"symbol": "ZZZQ", "lastPrice": 2.0, "changePct": -1.0},
        {"symbol": "NOPE", "lastPrice": None, "changePct": None},
    ]
    out = api_server._with_sector_profiles(cached_rows, ["NVDA", "ZZZQ", "NOPE"])
    assert out[0]["industry"] == "Semis" and out[0]["marketCap"] is None
    assert out[1]["industry"] == "Banks" and out[1]["marketCap"] == 9e9
    assert out[2]["industry"] is None and out[2]["marketCap"] is None
    assert out[1]["changePct"] == -1.0
    assert "industry" not in cached_rows[0]  # shared 5s cache rows untouched


def test_watchlist_rows_survive_a_profile_failure(monkeypatch):
    import api_server

    def boom(symbols):
        raise RuntimeError("corrupt cache")

    monkeypatch.setattr(industry_lookup, "cached_profiles", boom)
    rows = [{"symbol": "NVDA", "lastPrice": 1.0}]
    assert api_server._with_sector_profiles(rows, ["NVDA"]) is rows
