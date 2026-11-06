"""Long Term: per-ticker numbers, growth windows and the 11-point checklist. No network.

Real data comes from the trimmed fixtures in tests/fixtures/long_term (EDGAR company facts and
Finnhub /stock/metric). Synthetic snapshots cover the edge cases the fixtures do not.
"""
import copy
import dataclasses
import json
from datetime import date
from functools import lru_cache
from pathlib import Path

import pytest

from long_term.edgar import Snapshot, build_snapshot
from long_term.finnhub import FinnhubExtract, fetch_metrics
from long_term.metrics import (
    CHECK_KEYS,
    Check,
    Metrics,
    adjusted_equity,
    cagr,
    compute_metrics,
    growth_window,
    listing_factor,
    roic_series,
)

FIX = Path(__file__).parent / "fixtures" / "long_term"
SYMBOLS = ["AAPL", "ASML", "TSM", "HOOD", "MCD", "HD", "JPM", "GOOGL", "NVDA", "CRWV"]
CIKS = {"AAPL": 320193, "ASML": 937966, "TSM": 1046179, "HOOD": 1783879, "MCD": 63908,
        "HD": 354950, "JPM": 19617, "GOOGL": 1652044, "NVDA": 1045810, "CRWV": 1769628}
FA = "2026-11-06T03:00:00-05:00"
TODAY = date(2026, 11, 6)


@lru_cache(maxsize=None)
def _snap(sym):
    sub = json.loads((FIX / f"submissions_{sym}.json").read_text(encoding="utf-8"))
    facts = json.loads((FIX / f"companyfacts_{sym}.json").read_text(encoding="utf-8"))
    return build_snapshot(sym, CIKS[sym], facts, sic=int(sub["sic"]), name=sub["name"], fetched_at=FA)


@lru_cache(maxsize=None)
def _fh(sym):
    doc = json.loads((FIX / f"finnhub_{sym}.json").read_text(encoding="utf-8"))
    return fetch_metrics(sym, "k", get=lambda u, t: doc, fetched_at=FA)


def snap(sym):
    return copy.deepcopy(_snap(sym))


def fh(sym):
    return copy.deepcopy(_fh(sym))


def fixture_price(sym):
    x = _fh(sym)
    return x.eps_ttm * x.pe_ttm if x and x.eps_ttm and x.pe_ttm else None


def run(sym, price="fixture", **kw):
    p = fixture_price(sym) if price == "fixture" else price
    return compute_metrics(snap(sym), fh(sym), today=TODAY, is_etf=False, price=p, **kw)


# ---------- synthetic company: ten clean years, every check passes ----------

FLOWS = ("revenue", "net_income", "cfo", "capex", "sbc", "interest", "op_income")
BALANCES = ("debt", "cash", "equity", "assets")


def synth(n=10, last=2025, md="12-31", debt=20.0, interest_share=0.02, lpe="2026-09-30", fetched=FA):
    annual = {}
    for i, y in enumerate(range(last - n + 1, last + 1)):
        rev = 100.0 * 1.12 ** i
        ni = rev * 0.2
        eq = 50.0 * 1.12 ** i
        row = dict(revenue=rev, net_income=ni, eps=ni / 10.0, shares=10.0, equity=eq, cfo=ni * 1.2,
                   capex=ni * 0.2, sbc=1.0, buybacks=2.0, dividends=1.0, op_income=ni * 1.3,
                   assets=eq * 2.5, cash=10.0)
        if debt is not None:
            row["debt"] = debt
        if interest_share is not None:
            row["interest"] = row["op_income"] * interest_share
        annual[f"{y}-{md}"] = row
    lae = f"{last}-{md}"
    latest = annual[lae]
    ttm = {c: latest[c] for c in FLOWS + BALANCES + ("shares",) if c in latest}
    return Snapshot(symbol="SYN", cik=1, name="Synthetic Co", sic=3571, currency="USD", annual=annual,
                    units={}, tags={}, tag_breaks=[], ttm=ttm, ttm_period_end=lae, ttm_is_fy=True,
                    latest_annual_end=lae, latest_period_end=lpe or lae, fetched_at=fetched,
                    split_factor=1.0, notes=[],
                    ttm_ends={c: (lae, True) for c in FLOWS if c in latest},
                    balance_ends={c: lae for c in BALANCES + ("shares",) if c in latest})


def synth_fh(s, **kw):
    r = s.annual[s.latest_annual_end]
    eps = r["eps"]
    base = dict(symbol="SYN", eps_annual={e: row["eps"] for e, row in s.annual.items()},
                pe_annual={e: 20.0 for e in s.annual}, roic_annual={}, eps_ttm=eps, eps_incl_ttm=eps,
                pe_ttm=20.0, roi_ttm=r["net_income"] / (r["equity"] + r.get("debt", 0.0)) * 100,
                roe_ttm=r["net_income"] / r["equity"] * 100, market_cap_usd=1e9,
                debt_to_equity=r.get("debt", 0.0) / r["equity"], fetched_at=FA)
    base.update(kw)
    return FinnhubExtract(**base)


def synth_run(s, f="auto", **kw):
    f = synth_fh(s) if f == "auto" else f
    if "price" in kw:
        price = kw.pop("price")
    else:
        price = f.eps_ttm * f.pe_ttm if f else 100.0
    return compute_metrics(s, f, today=kw.pop("today", TODAY), is_etf=False, price=price)


def rows(revenues, start=2016, md="12-31", skip=()):
    out = {}
    for y, rev in zip(range(start, start + len(revenues)), revenues):
        if y in skip:
            continue
        out[f"{y}-{md}"] = dict(revenue=rev, net_income=rev * 0.1, eps=1.0, equity=10.0, cfo=2.0, capex=1.0)
    return out


# ---------- pure helpers ----------

def test_cagr_uses_three_year_endpoints_and_elapsed_days():
    vals = {f"{y}-12-31": v for y, v in zip(range(2016, 2026), [10, 100, 10, 12, 14, 16, 18, 20, 22, 24])}
    # Centres are 2017-12-31 and 2024-12-31: 2557 real days (two leap days), not 7 x 365.25.
    days = (date(2024, 12, 31) - date(2017, 12, 31)).days
    assert days == 2557
    expected = ((22 + 24 + 20) / 3 / ((10 + 100 + 10) / 3)) ** (365.25 / days) - 1
    assert cagr(vals, sorted(vals)) == pytest.approx(expected, rel=1e-6)


def test_cagr_undefined_on_negative_endpoint():
    vals = {f"{y}-12-31": v for y, v in zip(range(2016, 2026), [-10, -5, 1, 2, 3, 4, 5, 6, 7, 8])}
    assert cagr(vals, sorted(vals)) is None                           # first-3 mean -4.67
    vals2 = {f"{y}-12-31": v for y, v in zip(range(2016, 2026), [5, 6, 7, 8, 9, 3, 2, -1, -2, -3])}
    assert cagr(vals2, sorted(vals2)) is None                         # last-3 mean -2


def test_cagr_needs_five_rows_and_every_window_value():
    vals = {f"{y}-12-31": 10.0 * 1.1 ** i for i, y in enumerate(range(2021, 2025))}
    assert cagr(vals, sorted(vals)) is None                           # 4 rows
    vals = {f"{y}-12-31": 10.0 * 1.1 ** i for i, y in enumerate(range(2016, 2026))}
    window = sorted(vals)
    del vals["2020-12-31"]
    assert cagr(vals, window) is None                                 # a hole in the window


def test_growth_window_skips_spac_years():
    annual = rows([50e3, 60e3, 10e6, 11e6, 12e6, 13e6, 14e6, 15e6, 16e6, 17e6])
    w = growth_window(annual)
    assert w[0] == "2018-12-31" and w[-1] == "2025-12-31" and len(w) == 8


def test_growth_window_skips_years_below_five_percent_of_latest():
    annual = rows([1, 2, 3, 4, 10, 20, 30, 40, 50, 60])              # 5% of 60 = 3 -> starts at the 3
    assert growth_window(annual)[0] == "2018-12-31"


def test_growth_window_refuses_gap_over_400_days():
    annual = rows([10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21], start=2014, skip=(2017,))
    w = growth_window(annual)
    assert w[0] == "2018-12-31" and len(w) == 8


def test_growth_window_caps_at_ten_and_needs_complete_rows():
    annual = rows([10 + i for i in range(14)], start=2012)
    assert len(growth_window(annual)) == 10 and growth_window(annual)[0] == "2016-12-31"
    del annual["2021-12-31"]["eps"]                                   # incomplete row stops the walk back
    assert growth_window(annual)[0] == "2022-12-31"
    del annual["2025-12-31"]["capex"]                                 # latest row incomplete -> no window
    assert growth_window(annual) == []


def test_roic_guard_negative_denominator():
    assert roic_series({"2025-12-31": {"net_income": 1.0, "equity": -5.0, "debt": 3.0, "assets": 10.0}}) == {}
    # denominator 0.5 is under 10% of assets 10 -> n/a
    assert roic_series({"2025-12-31": {"net_income": 1.0, "equity": -2.5, "debt": 3.0, "assets": 10.0}}) == {}
    assert roic_series({"2025-12-31": {"net_income": 1.0, "equity": 4.0, "debt": 1.0, "assets": 10.0}}) == {
        "2025-12-31": pytest.approx(0.2)}
    assert roic_series({"2025-12-31": {"net_income": 1.0, "equity": 4.0, "assets": 10.0}}) == {}   # debt unknown


def test_adjusted_equity_adds_capital_returned_after_the_start():
    annual = {"2023-12-31": {"equity": 10.0, "buybacks": 5.0, "dividends": 1.0},
              "2024-12-31": {"equity": 8.0, "buybacks": 4.0, "dividends": 1.0},
              "2025-12-31": {"equity": 7.0, "buybacks": 3.0}}
    assert adjusted_equity(annual, sorted(annual)) == {"2023-12-31": 10.0, "2024-12-31": 13.0, "2025-12-31": 15.0}


def test_listing_factor_tsm():
    assert 0.1 < listing_factor(350.0, fh("TSM")) < 0.2
    assert listing_factor(1322.0, fh("ASML")) == 1.0
    assert listing_factor(None, fh("ASML")) is None and listing_factor(100.0, None) is None


# ---------- real fixtures ----------

def test_roic_town_definition_mcd():
    m = run("MCD", price=300)
    assert 0.15 < m.roic_now < 0.35                                   # 8.79B / (-1.02B + 41.5B) = 21.7%
    assert "TTM" in m.checks["roic_now"].detail


def test_mcd_shares_rescaled_from_millions():
    m = run("MCD", price=300)
    assert "shares: rescaled ×1e6" in m.notes
    assert m.eps_ttm_edgar == pytest.approx(8.787e9 / 711.1e6, rel=1e-6)
    assert m.checks["sources"].passed is True


def test_equity_growth_na_for_buybacks_hd():
    assert run("HD").checks["equity_growth"].passed in (None, True)


def test_equity_growth_na_when_start_negative_mcd():
    c = run("MCD", price=300).checks["equity_growth"]
    assert c.passed is None and "buybacks" in c.detail


def test_financial_rule_set_jpm():
    m = run("JPM", price=250)
    assert m.financial and m.roe_now and m.checks["fcf_growth"].passed is None
    assert m.checks["debt"].passed is None
    assert m.growth["eps"] is not None and m.growth_source["eps"] == "edgar"   # no Finnhub eps series
    assert m.checks["roic_now"].label == "ROE now"


def test_jpm_stale_debt_balance_recorded():
    m = run("JPM")
    assert "debt balance stale (2014-06-30)" in m.notes
    assert m.debt is None and m.roic_now is None
    assert m.status is None                                            # financial: debt is n/a by design


def test_eps_growth_prefers_finnhub_series():
    assert run("GOOGL").growth_source["eps"] == "finnhub"


def test_sources_disagree_on_roic_sets_check_data():
    f = fh("AAPL")
    f.roi_ttm = 5.0
    m = compute_metrics(snap("AAPL"), f, today=TODAY, is_etf=False, price=fixture_price("AAPL"))
    assert m.status == "CHECK_DATA" and m.status_reason.startswith("Check data: ROIC 69% vs Finnhub 5%")
    assert m.checks["sources"].passed is False


def test_price_sanity_catches_scale_error():
    m = run("AAPL", price=100.0)                                      # eps x pe = 343.7
    assert m.status == "CHECK_DATA" and "price sanity" in m.status_reason


def test_aapl_clean_with_right_price():
    m = run("AAPL")
    assert m.status is None and m.status_reason == "" and m.listing_factor == 1.0
    assert m.years == 10 and m.young is False


def test_tsm_adr_no_shares_skips_eps_check_and_is_stale():
    m = run("TSM", price=350.0)
    assert m.eps_ttm_edgar is None
    assert "EPS: skipped (non-USD filer)" in m.checks["sources"].detail
    assert m.status == "STALE" and m.status_reason == "Stale filing (annual 2024-12-31)"
    assert 0.1 < m.listing_factor < 0.2
    assert m.eps_ttm_usd == pytest.approx(fh("TSM").eps_ttm * m.listing_factor)


def test_nvda_fcf_from_finnhub_margin_when_capex_has_a_hole():
    m = run("NVDA")
    assert m.growth_source["fcf"] == "finnhub_margin"
    assert m.years == 8 and m.window[0].startswith("2019")            # starts where revenue >= 5% of FY2026
    assert m.growth["fcf"] is not None


def test_short_history_crwv():
    m = run("CRWV")
    # three annual rows, but 2023 -> 2024 revenue is an 8.4x jump, so the window starts at 2024
    assert m.status == "SHORT_HISTORY" and m.status_reason == "Short history (2 years)"
    assert m.window == ["2024-12-31", "2025-12-31"]


def test_young_eight_year_rule():
    m = run("HOOD")
    assert m.financial and 5 <= m.years < 8 and m.young is True


def test_etf_status():
    m = compute_metrics(None, None, today=TODAY, is_etf=True, price=1)
    assert m.status == "ETF" and set(m.checks) == set(CHECK_KEYS)


def test_no_filings_status():
    assert compute_metrics(None, None, today=TODAY, is_etf=False, price=1).status == "NO_FILINGS"


def test_finnhub_missing_never_scores():
    m = compute_metrics(snap("AAPL"), None, today=TODAY, is_etf=False, price=340.0)
    assert m.status == "CHECK_DATA" and m.status_reason == "Finnhub: no data"
    assert m.checks["sources"].passed is False


def test_big5_counts_and_rule1_business():
    m = run("AAPL")
    assert 0 <= m.big5 <= 5 and isinstance(m.rule1_business, bool)
    five = [m.checks[k].passed for k in ("roic_consistency", "sales_growth", "eps_growth", "equity_growth", "fcf_growth")]
    assert m.big5 == five.count(True) and m.big5_na == five.count(None)


@pytest.mark.parametrize("sym", SYMBOLS)
def test_smoke_every_fixture(sym):
    m = run(sym)
    assert isinstance(m, Metrics) and set(m.checks) == set(CHECK_KEYS)
    assert all(isinstance(c, Check) for c in m.checks.values())
    assert m.status in {None, "SHORT_HISTORY", "CHECK_DATA", "STALE"}
    assert (m.status is None) == (m.status_reason == "")
    m2 = run(sym, price=None)                                          # no price: still never raises
    assert m2.status in {None, "SHORT_HISTORY", "CHECK_DATA", "STALE"}


# ---------- synthetic edge cases ----------

def test_synthetic_clean_company_scores_full_marks():
    m = synth_run(synth())
    assert m.status is None, m.status_reason
    assert all(c.passed is True for c in m.checks.values()), {k: c.detail for k, c in m.checks.items()}
    assert m.big5 == 5 and m.big5_na == 0 and m.rule1_business and m.years == 10
    assert m.growth["sales"] == pytest.approx(0.12, abs=1e-3)


def test_zero_debt_requires_proof():
    s = synth(debt=None, interest_share=0.05)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.01))
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved"
    assert m.checks["debt"].passed is None


def test_zero_debt_needs_finnhub_debt_to_equity_too():
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.30))
    assert m.status == "CHECK_DATA" and "debt unresolved" in m.status_reason


def test_zero_debt_accepted_with_proof():
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.01))
    assert m.checks["debt"].passed is True and "no debt" in m.checks["debt"].detail
    assert m.status is None and m.debt == 0.0
    assert m.roic_now == pytest.approx(s.ttm["net_income"] / s.ttm["equity"])


def test_stale_debt_balance_is_unresolved():
    s = synth()
    s.balance_ends["debt"] = "2014-06-30"
    m = synth_run(s)
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved"
    assert "debt balance stale (2014-06-30)" in m.notes and m.roic_now is None


def test_debt_over_three_times_fcf_fails():
    s = synth(debt=1000.0)
    m = synth_run(s)
    assert m.checks["debt"].passed is False and m.checks["debt"].value > 3


def test_stale_by_annual_end():
    s = synth(md="06-30", lpe="2025-06-30")                           # annual end 16 months before TODAY
    m = synth_run(s)
    assert m.status == "STALE" and m.status_reason == "Stale filing (annual 2025-06-30)"
    assert m.checks["history"].passed is False


def test_stale_by_quarter_for_us_filer():
    m = synth_run(synth(lpe="2026-03-31"))                            # 7+ months without a 10-Q
    assert m.status == "STALE" and "2026-03-31" in m.status_reason


def test_quarter_rule_skips_non_usd_annual_filer():
    s = synth(lpe="2025-12-31")
    s.currency = "EUR"
    assert synth_run(s).status != "STALE"


def test_stale_by_fetched_at():
    m = synth_run(synth(fetched="2026-11-01T03:00:00-05:00"))
    assert m.status == "STALE" and "fetched" in m.status_reason


def test_short_history_synthetic():
    m = synth_run(synth(n=4))
    assert m.status == "SHORT_HISTORY" and m.status_reason == "Short history (4 years)"


def test_mixed_ttm_stamps_use_fy_for_both():
    s = synth()
    lae = s.latest_annual_end
    s.ttm["cfo"] = s.annual[lae]["cfo"] * 3                          # a TTM cfo with capex still FY
    s.ttm_ends["cfo"] = ("2026-09-30", False)
    m = synth_run(s)
    r = s.annual[lae]
    assert m.fcf_ttm == pytest.approx(r["cfo"] - r["capex"])
    assert "FY" in m.checks["debt"].detail and "FY" in m.checks["earning"].detail


def test_owner_earnings_without_sbc_notes_zero():
    s = synth()
    for r in s.annual.values():
        del r["sbc"]
    del s.ttm["sbc"], s.ttm_ends["sbc"]
    m = synth_run(s)
    assert m.owner_earnings_ttm == pytest.approx(m.fcf_ttm)
    assert any("sbc" in n for n in m.notes)


def test_owner_earnings_mixed_sbc_stamp_uses_fy_sbc():
    s = synth()
    s.ttm["sbc"], s.ttm_ends["sbc"] = 5.0, ("2026-09-30", False)    # sbc TTM, cfo/capex FY
    m = synth_run(s)
    assert m.owner_earnings_ttm == pytest.approx(m.fcf_ttm - s.annual[s.latest_annual_end]["sbc"])


def test_eps_falling_fails_check_10():
    s = synth()
    m = synth_run(s, synth_fh(s, eps_ttm=s.annual[s.latest_annual_end]["eps"] * 0.7))
    assert m.checks["eps_not_falling"].passed is False


def test_eps_cross_check_periods_differ():
    s = synth()
    f = synth_fh(s)
    f.eps_annual = {e: v for e, v in f.eps_annual.items() if e < "2025"}
    f.pe_annual = {e: v for e, v in f.pe_annual.items() if e < "2025"}
    m = synth_run(s, f)
    assert m.status == "CHECK_DATA" and "periods differ" in m.status_reason


def test_eps_growth_sources_disagree():
    s = synth()
    f = synth_fh(s)
    f.eps_annual = {e: 1.0 for e in f.eps_annual}                     # Finnhub flat, EDGAR +12%/yr
    m = synth_run(s, f)
    assert m.status == "CHECK_DATA" and "EPS growth" in m.status_reason


def test_rule1_business_needs_roic_consistency_pass():
    s = synth()
    for r in s.annual.values():
        r["net_income"] = r["revenue"] * 0.02                         # ROIC ~3% every year
    m = synth_run(s)
    assert m.checks["roic_consistency"].passed is False and m.rule1_business is False


def test_metrics_dataclass_fields():
    names = {f.name for f in dataclasses.fields(Metrics)}
    need = {"symbol", "financial", "years", "window", "currency", "roic_now", "roic_series", "roe_now",
            "net_income_ttm", "fcf_ttm", "owner_earnings_ttm", "eps_ttm_edgar", "eps_last_fy", "eps_3yr_avg",
            "growth", "growth_source", "debt", "cash", "equity", "assets", "pe_hist_median", "market_cap_usd",
            "listing_factor", "checks", "big5", "big5_na", "rule1_business", "status", "status_reason",
            "young", "eps_ttm_usd", "notes"}
    assert need <= names
    assert [f.name for f in dataclasses.fields(Check)] == ["key", "label", "value", "threshold", "passed", "detail"]
    assert CHECK_KEYS == ("roic_now", "roic_consistency", "sales_growth", "eps_growth", "equity_growth",
                          "fcf_growth", "debt", "earning", "history", "eps_not_falling", "sources")


def test_input_snapshot_not_mutated():
    s = snap("MCD")
    before = copy.deepcopy(s)
    compute_metrics(s, fh("MCD"), today=TODAY, is_etf=False, price=300)
    assert s == before


# ---------- review fixes (2026-11-06) ----------

def test_price_sanity_not_checked_without_price_usd():
    s = synth()
    m = synth_run(s, price=None)
    assert m.checks["sources"].passed is None
    assert "price sanity: not checked (no price)" in m.checks["sources"].detail
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: price sanity not checked"


def test_price_sanity_not_checked_without_pe():
    s = synth()
    m = synth_run(s, synth_fh(s, pe_ttm=None), price=100.0)
    assert m.checks["sources"].passed is None
    assert "price sanity: not checked (no P/E)" in m.checks["sources"].detail
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: price sanity not checked"


def test_price_sanity_not_checked_non_usd_no_price():
    s = synth(lpe="2025-12-31")
    s.currency, s.annual_form = "EUR", "20-F"
    m = synth_run(s, price=None)
    assert m.eps_ttm_usd is None and m.checks["sources"].passed is None
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: price sanity not checked"


def test_price_sanity_real_failure_outranks_not_checked():
    f = fh("AAPL")
    f.roi_ttm = 5.0
    m = compute_metrics(snap("AAPL"), f, today=TODAY, is_etf=False, price=None)
    assert m.checks["sources"].passed is False and m.status_reason.startswith("Check data: ROIC")


def test_eps_not_falling_na_when_edgar_ttm_is_fy_bank():
    s = synth()
    s.sic = 6021                                                      # bank; net income stamped TTM = FY
    f = synth_fh(s, eps_annual={})
    m = synth_run(s, f)
    c = m.checks["eps_not_falling"]
    assert c.passed is None and c.detail == "TTM unavailable (TTM = FY)"


def test_quarter_rule_skips_usd_20f_filer():
    s = synth(lpe="2025-12-31")
    s.annual_form = "20-F"                                            # USD-reporting foreign filer (ARM/INFY-like)
    assert synth_run(s).status is None
    s.annual_form = "40-F"
    assert synth_run(s).status is None
    s.annual_form = "10-K"
    assert synth_run(s).status == "STALE"


def test_quarter_rule_currency_fallback_when_form_unknown():
    s = synth(lpe="2025-12-31")
    s.annual_form = None
    assert synth_run(s).status == "STALE"                             # USD, form unknown -> 10-Q filer


def _capex_hole(s, years):
    for e, r in s.annual.items():
        if e[:4] in years:
            del r["capex"]


def test_fcf_margin_disagrees_not_used():
    s = synth()
    _capex_hole(s, {"2016", "2017", "2018"})
    m = synth_run(s, synth_fh(s, fcf_margin_annual={e: 0.5 for e in s.annual}))   # true margin is 0.2
    assert m.growth_source["fcf"] == "edgar" and m.years == 7
    assert any("disagrees" in n for n in m.notes)


def test_fcf_margin_agreeing_is_used():
    s = synth()
    _capex_hole(s, {"2016", "2017", "2018"})
    m = synth_run(s, synth_fh(s, fcf_margin_annual={e: 0.2 for e in s.annual}))
    assert m.growth_source["fcf"] == "finnhub_margin" and m.years == 10


def test_fcf_margin_needs_two_agreeing_overlap_years():
    s = synth()
    _capex_hole(s, {str(y) for y in range(2016, 2025)})              # EDGAR FCF only in 2025
    m = synth_run(s, synth_fh(s, fcf_margin_annual={e: 0.2 for e in s.annual}))
    assert m.growth_source["fcf"] == "edgar" and m.years == 1


def _set_latest(s, **vals):
    for k, v in vals.items():
        s.annual[s.latest_annual_end][k] = v
        s.ttm[k] = v


def test_earning_fails_when_owner_earnings_negative():
    s = synth()
    _set_latest(s, cfo=12.0, capex=2.0, sbc=12.0)                    # FCF 10, SBC 12
    c = synth_run(s).checks["earning"]
    assert c.passed is False and "owner earnings negative after stock comp" in c.detail


def test_earning_passes_when_owner_earnings_positive():
    s = synth()
    _set_latest(s, cfo=12.0, capex=2.0, sbc=3.0)                     # FCF 10, SBC 3
    assert synth_run(s).checks["earning"].passed is True


def test_earning_fails_googl_owner_earnings():
    m = run("GOOGL")
    c = m.checks["earning"]
    assert m.fcf_ttm > 0 and m.owner_earnings_ttm <= 0
    assert c.passed is False and c.detail.startswith("owner earnings negative after stock comp (FCF $19.7B − SBC $30.4B)")


def test_money_format():
    from long_term.metrics import _money
    assert [_money(v) for v in (100e9, 19.747e9, 25e9, -3.5e6, 1.2e12, 950.0)] == [
        "$100B", "$19.7B", "$25B", "-$3.5M", "$1.2T", "$950"]


# ---------- final review fixes ----------

def _synth_prior(prior):
    s = synth()
    f = synth_fh(s)
    return compute_metrics(s, f, today=TODAY, is_etf=False, price=f.eps_ttm * f.pe_ttm,
                           prior_listing_factor=prior)


def test_listing_factor_drift_over_20pct_is_check_data():
    m = _synth_prior(1 / 1.31)                       # factor now 1.0, was 0.763 -> moved 31%
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: listing factor moved 31%"
    assert m.checks["sources"].passed is False


def test_listing_factor_small_move_or_no_baseline_is_fine():
    assert _synth_prior(0.9).status is None          # 11% move
    assert _synth_prior(None).status is None         # first night: no baseline, no check


def test_eps_growth_one_source_only_non_financial():
    s = synth()
    f = synth_fh(s, eps_annual={})
    m = compute_metrics(s, f, today=TODAY, is_etf=False, price=f.eps_ttm * f.pe_ttm)
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: EPS growth: one source only"
    assert "EPS growth: one source only" in m.checks["sources"].detail and m.checks["sources"].passed is False


def test_eps_growth_edgar_only_still_allowed_for_financials():
    m = run("JPM", price=250)                        # no Finnhub eps series (see test_financial_rule_set_jpm)
    assert "EPS growth: no Finnhub series (EDGAR used)" in m.checks["sources"].detail
    assert "one source only" not in m.checks["sources"].detail


def test_market_cap_none_for_non_usd_filer():
    assert fh("TSM").market_cap_usd                  # Finnhub has a cap, but in TWD
    assert run("TSM", price=350.0).market_cap_usd is None
    assert run("AAPL").market_cap_usd == fh("AAPL").market_cap_usd   # USD filers unchanged
