"""SPX as an index (2026-10-04): $-names for Schwab, open interest from CBOE."""
from __future__ import annotations

from data import cboe_oi
from data.index_symbols import from_schwab, is_index, to_schwab


def test_index_names_translate_only_at_the_schwab_boundary():
    assert to_schwab("spx") == "$SPX" and to_schwab("$SPX") == "$SPX"
    assert to_schwab("SPY") == "SPY" and to_schwab("AAPL") == "AAPL"
    assert from_schwab("$SPX") == "SPX" and from_schwab("$TSLA") == "$TSLA" and from_schwab("AAPL") == "AAPL"
    assert is_index("SPX") and is_index("$NDX") and not is_index("SPY")


def _chain():
    leg = lambda sym, oi: {"symbol": sym, "openInterest": oi}  # noqa: E731
    return {"callExpDateMap": {"2026-10-05:1": {"7700.0": [leg("SPXW  261005C07700000", 0)]}},
            "putExpDateMap": {"2026-10-05:1": {"7600.0": [leg("SPXW  261005P07600000", 0)],
                                               "7500.0": [leg("SPXW  261005P07500000", 12)]}}}


def test_cboe_fills_zero_open_interest_on_index_chains_only(monkeypatch):
    monkeypatch.setattr(cboe_oi, "_CACHE", {})
    monkeypatch.setattr(cboe_oi, "_FAILED", {})
    feed = {"data": {"options": [{"option": "SPXW261005C07700000", "open_interest": 8484.0},
                                 {"option": "SPXW261005P07600000", "open_interest": 2100.0},
                                 {"option": "SPXW261005P07500000", "open_interest": 999.0}]}}
    calls = []
    get = lambda url: calls.append(url) or feed  # noqa: E731
    out = cboe_oi.fill_open_interest("SPX", _chain(), get=get)
    assert out["callExpDateMap"]["2026-10-05:1"]["7700.0"][0]["openInterest"] == 8484
    assert out["putExpDateMap"]["2026-10-05:1"]["7600.0"][0]["openInterest"] == 2100
    assert out["putExpDateMap"]["2026-10-05:1"]["7500.0"][0]["openInterest"] == 12      # Schwab's own number kept
    assert calls == ["https://cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json"]
    cboe_oi.fill_open_interest("SPX", _chain(), get=get)
    assert len(calls) == 1                                                             # cached
    spy = {"callExpDateMap": {"x": {"1": [{"symbol": "SPY", "openInterest": 0}]}}}
    assert cboe_oi.fill_open_interest("SPY", spy, get=get) is spy and len(calls) == 1   # not an index: untouched


def test_cboe_down_leaves_the_chain_alone(monkeypatch):
    monkeypatch.setattr(cboe_oi, "_CACHE", {})
    monkeypatch.setattr(cboe_oi, "_FAILED", {})
    def boom(url):
        raise OSError("down")
    chain = _chain()
    assert cboe_oi.fill_open_interest("SPX", chain, get=boom) is chain
    assert chain["callExpDateMap"]["2026-10-05:1"]["7700.0"][0]["openInterest"] == 0
