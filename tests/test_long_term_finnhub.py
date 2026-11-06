import pytest

from long_term.finnhub import FinnhubExtract, FinnhubPacer, fetch_metrics

NVDA = {
    "metric": {"epsTTM": 7.9108, "epsInclExtraItemsTTM": 7.9108, "peTTM": 25.006, "roiTTM": 101.59,
               "roeTTM": 110.11, "marketCapitalization": 4823133.0, "totalDebt/totalEquityAnnual": 0.0538},
    "series": {"annual": {
        "eps": [{"period": "2026-01-25", "v": 4.8979}, {"period": "2015-01-25", "v": 0.028}],
        "pe": [{"period": "2026-01-25", "v": 37.7}, {"period": "2025-01-26", "v": None}],
        "roic": [{"period": "2026-01-25", "v": 0.724}]}},
    "symbol": "NVDA",
}
JPM = {"metric": {"peTTM": 12.0, "roeTTM": 17.0},
       "series": {"annual": {"pe": [{"period": "2025-12-31", "v": 11.0}], "roic": [{"period": "2025-12-31", "v": 0.1}]}}}
CRWV = {"metric": {"peTTM": None, "epsTTM": -1.0},
        "series": {"annual": {"pe": [{"period": "2025-12-31", "v": None}], "eps": [{"period": "2025-12-31", "v": -1.0}]}}}
FA = "2026-11-06T03:00:00-05:00"


def test_extract_nvda():
    x = fetch_metrics("NVDA", "k", get=lambda url, t: NVDA, fetched_at=FA)
    assert isinstance(x, FinnhubExtract)
    assert x.eps_annual["2015-01-25"] == pytest.approx(0.028)
    assert x.market_cap_usd == pytest.approx(4823133.0e6) and x.market_cap_usd > 1e12
    assert x.eps_incl_ttm == x.eps_ttm == pytest.approx(7.9108)
    assert x.roi_ttm == pytest.approx(101.59) and x.roe_ttm == pytest.approx(110.11)
    assert x.debt_to_equity == pytest.approx(0.0538) and x.pe_ttm == pytest.approx(25.006)
    assert x.roic_annual == {"2026-01-25": 0.724}
    assert x.fetched_at == FA and x.symbol == "NVDA"


def test_bank_without_eps_series():
    assert fetch_metrics("JPM", "k", get=lambda u, t: JPM, fetched_at="x").eps_annual == {}


def test_null_pe_dropped():
    x = fetch_metrics("CRWV", "k", get=lambda u, t: CRWV, fetched_at="x")
    assert x.pe_annual == {} and x.pe_ttm is None


def test_nvda_pe_null_dropped():
    x = fetch_metrics("NVDA", "k", get=lambda u, t: NVDA, fetched_at="x")
    assert x.pe_annual == {"2026-01-25": 37.7}


def test_no_key_returns_none():
    calls = []
    assert fetch_metrics("AAPL", "", get=lambda u, t: calls.append(u) or {}, fetched_at="x") is None
    assert calls == []


def test_malformed_shapes():
    assert fetch_metrics("A", "k", get=lambda u, t: {"metric": [1]}, fetched_at="x") is None
    x = fetch_metrics("A", "k", get=lambda u, t: {"metric": {"epsTTM": float("nan"), "peTTM": 5}, "series": "bad"},
                      fetched_at="x")
    assert x.eps_annual == {} and x.eps_ttm is None and x.pe_ttm == 5.0


def test_empty_metric_returns_none():
    assert fetch_metrics("ZZZZ", "k", get=lambda u, t: {"metric": {}, "series": {}}, fetched_at="x") is None
    assert fetch_metrics("ZZZZ", "k", get=lambda u, t: {}, fetched_at="x") is None


def test_exception_returns_none():
    def boom(u, t):
        raise OSError("down")
    assert fetch_metrics("AAPL", "k", get=boom, fetched_at="x") is None


def test_symbol_uses_dots():
    captured = []
    fetch_metrics("BRK-B", "k", get=lambda u, t: captured.append(u) or {"metric": {}}, fetched_at="x")
    assert "symbol=BRK.B" in captured[0]


def test_pacer_spaces_calls():
    now = [100.0]
    sleeps = []

    def sleep(s):
        sleeps.append(s)
        now[0] += s

    p = FinnhubPacer(per_minute=30, sleep=sleep, clock=lambda: now[0])
    p.wait()
    assert sleeps == []
    now[0] += 0.5
    p.wait()
    assert sleeps == [pytest.approx(1.5)]
    now[0] += 10
    p.wait()
    assert len(sleeps) == 1


def test_pacer_backoff():
    sleeps = []
    p = FinnhubPacer(sleep=sleeps.append)
    for a in range(4):
        p.backoff(a)
    assert sleeps == [1, 2, 4, 8]


def test_fcf_margin_series_fractions_nulls_dropped():
    doc = {"metric": {"epsTTM": 1.0},
           "series": {"annual": {"fcfMargin": [{"period": "2026-01-25", "v": 0.4477},
                                               {"period": "2021-01-31", "v": None},
                                               {"period": "2020-01-26", "v": 0.3913}]}}}
    x = fetch_metrics("NVDA", "k", get=lambda u, t: doc, fetched_at=FA)
    assert x.fcf_margin_annual == {"2026-01-25": pytest.approx(0.4477), "2020-01-26": pytest.approx(0.3913)}
    assert fetch_metrics("JPM", "k", get=lambda u, t: JPM, fetched_at=FA).fcf_margin_annual == {}


def test_failed_fetch_logs_one_warning_without_the_key(caplog):
    def boom(url, timeout):
        raise TimeoutError(f"timed out fetching {url}")
    with caplog.at_level("WARNING", logger="long_term"):
        assert fetch_metrics("AAPL", "secret-key-123", get=boom, fetched_at="x") is None
    recs = [r for r in caplog.records if r.name == "long_term"]
    assert len(recs) == 1 and recs[0].levelname == "WARNING"
    msg = recs[0].getMessage()
    assert "AAPL" in msg and "TimeoutError" in msg and "secret-key-123" not in msg
