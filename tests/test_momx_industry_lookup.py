"""Industry lookup for symbols outside the hand map (trader ask, 2026-08-28).

No network: every test injects a fake `get`. The failure that matters most is
a provider outage silently becoming a PERMANENT blank - so transient failures
must not be cached while real "provider has nothing" answers must be.
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


def _finnhub_get(industry_by_symbol):
    calls = []

    def get(url, timeout):
        calls.append(url)
        assert "finnhub.io" in url
        symbol = url.split("symbol=")[1].split("&")[0]
        value = industry_by_symbol.get(symbol)
        if isinstance(value, Exception):
            raise value
        return {"finnhubIndustry": value}

    get.calls = calls
    return get


def test_hand_map_always_wins_over_the_provider():
    get = _finnhub_get({"NVDA": "Technology"})
    out = industry_lookup.resolve(["NVDA"], get=get)
    assert out["NVDA"] == "Semis"          # curated label, not "Tech-HW"
    assert get.calls == []                  # and no fetch was spent on it


def test_unknown_symbol_is_fetched_shortened_and_cached():
    get = _finnhub_get({"ZZZQ": "Banking"})
    assert industry_lookup.resolve(["ZZZQ"], get=get)["ZZZQ"] == "Banks"
    # second resolve: served from cache, no second call
    assert industry_lookup.resolve(["ZZZQ"], get=get)["ZZZQ"] == "Banks"
    assert len(get.calls) == 1
    # and the cache survives a fresh process (memory cache cleared)
    industry_lookup.clear_memory_cache()
    assert industry_lookup.resolve(["ZZZQ"], get=get)["ZZZQ"] == "Banks"
    assert len(get.calls) == 1


def test_provider_nothing_is_cached_but_failure_is_not():
    # "" = real empty answer -> cached; exception = transient -> retried
    get = _finnhub_get({"EMPT": "", "DOWN": RuntimeError("boom")})
    out = industry_lookup.resolve(["EMPT", "DOWN"], get=get)
    assert out["EMPT"] == ""
    assert "DOWN" not in out
    industry_lookup.resolve(["EMPT", "DOWN"], get=get)
    empt_calls = [u for u in get.calls if "EMPT" in u]
    down_calls = [u for u in get.calls if "DOWN" in u]
    assert len(empt_calls) == 1, "an empty answer must not be refetched"
    assert len(down_calls) == 2, "a FAILURE must be retried next build"


def test_fetch_budget_caps_provider_calls():
    symbols = ["SYM%02d" % i for i in range(50)]
    get = _finnhub_get({s: "Banking" for s in symbols})
    out = industry_lookup.resolve(symbols, get=get, budget=10)
    assert len(get.calls) == 10
    assert sum(1 for s in symbols if s in out) == 10


def test_no_keys_means_no_calls_and_no_crash(monkeypatch):
    monkeypatch.delenv("FINNHUB_API_KEY", raising=False)
    get = _finnhub_get({})
    out = industry_lookup.resolve(["MYST"], get=get)
    assert "MYST" not in out
    assert get.calls == []


def test_cache_file_round_trips(tmp_path):
    get = _finnhub_get({"ABCD": "Semiconductors"})
    industry_lookup.resolve(["ABCD"], get=get)
    stored = json.loads(industry_lookup.cache_path().read_text(encoding="utf-8"))
    assert stored["ABCD"]["industry"] == "Semis"
