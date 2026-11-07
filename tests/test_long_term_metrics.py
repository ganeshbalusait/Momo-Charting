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


def test_cagr_needs_three_rows_and_every_window_value():
    # R33: 3-4 rows use single endpoints (first -> last, real days); 5+ rows the 3-year averages
    vals = {f"{y}-12-31": 10.0 * 1.1 ** i for i, y in enumerate(range(2021, 2025))}
    days = (date(2024, 12, 31) - date(2021, 12, 31)).days
    assert cagr(vals, sorted(vals)) == pytest.approx((1.1 ** 3) ** (365.25 / days) - 1, rel=1e-9)   # 4 rows
    three = {k: v for k, v in list(vals.items())[:3]}
    assert cagr(three, sorted(three)) is not None                     # 3 rows
    two = {k: v for k, v in list(vals.items())[:2]}
    assert cagr(two, sorted(two)) is None                             # 2 rows: no growth
    assert cagr({"2023-12-31": -1.0, "2024-12-31": 2.0, "2025-12-31": 3.0}, ["2023-12-31", "2024-12-31", "2025-12-31"]) is None
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
    # the EPS tag's TTM (12.31) is "EPS now"; NI / rescaled shares (12.36) is the fallback
    assert m.eps_ttm_edgar == pytest.approx(snap("MCD").ttm["eps"])
    assert m.eps_ttm_edgar == pytest.approx(8.787e9 / 711.1e6, rel=0.01)
    assert m.checks["sources"].passed is True
    s = snap("MCD")
    del s.ttm["eps"], s.ttm_ends["eps"]
    m = compute_metrics(s, fh("MCD"), today=TODAY, is_etf=False, price=300)
    assert m.eps_ttm_edgar == pytest.approx(8.787e9 / 711.1e6, rel=1e-6)


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


def test_jpm_debt_is_na_by_design():
    m = run("JPM")
    assert m.financial and m.checks["debt"].passed is None and "n/a (financial)" in m.checks["debt"].detail
    assert m.status is None and m.wait_cap == ""                       # financial: debt is n/a by design
    s = snap("JPM")
    s.balance_ends["debt"] = "2014-06-30"                              # a stale balance is recorded, never estimated
    m = compute_metrics(s, fh("JPM"), today=TODAY, is_etf=False, price=fixture_price("JPM"))
    assert "debt balance stale (2014-06-30)" in m.notes and m.debt is None and m.status is None


def test_eps_growth_prefers_finnhub_series():
    assert run("GOOGL").growth_source["eps"] == "finnhub"


def test_sources_disagree_on_roic_is_avoid_on_the_worse_source():
    # R38: the sources disagree on ROIC and the worse one (Finnhub 5%) already fails the 15% gate,
    # so the row is scored and AVOID with that reason, never "Check data"
    f = fh("AAPL")
    f.roi_ttm = 5.0
    m = compute_metrics(snap("AAPL"), f, today=TODAY, is_etf=False, price=fixture_price("AAPL"))
    assert m.status is None and m.status_reason == ""
    assert m.forced_avoid == "ROIC 5% < 15% (Finnhub; sources differ on ROIC)"
    assert m.checks["sources"].passed is False
    assert "check data: ROIC 69% vs Finnhub 5%" in m.notes


def test_price_sanity_is_a_note_for_usd_10k_filers():
    # R39: Schwab's price is authoritative for a USD 10-K filer; Finnhub's EPS x P/E lags
    m = run("AAPL", price=100.0)                                      # eps x pe = 343.7
    assert m.status is None and m.forced_avoid == "" and m.wait_cap == ""
    assert m.checks["sources"].passed is True
    assert "price sanity: price 100.00 vs Finnhub EPS × P/E 343" in m.checks["sources"].detail
    assert "note only" in m.checks["sources"].detail
    assert m.listing_factor == 1.0                                    # a 10-K filer never gets an ADR factor


def test_price_sanity_still_blocks_usd_20f_filer():
    s = synth(lpe="2025-12-31")
    s.annual_form = "20-F"
    f = synth_fh(s)
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm * 0.5)             # 50% off Finnhub's EPS x P/E
    assert m.status == "CHECK_DATA" and "price sanity" in m.status_reason


def test_aapl_clean_with_right_price():
    m = run("AAPL")
    assert m.status is None and m.status_reason == "" and m.listing_factor == 1.0
    assert m.years == 10 and m.young is False


def test_tsm_adr_no_shares_skips_eps_check_and_is_stale():
    m = run("TSM", price=350.0)
    assert m.eps_ttm_edgar == pytest.approx(snap("TSM").ttm["eps"])     # the EPS tag's TTM, in TWD
    assert "EPS: skipped (non-USD filer)" in m.checks["sources"].detail
    assert m.status == "STALE" and m.status_reason == "Stale filing (annual 2024-12-31)"
    assert 0.1 < m.listing_factor < 0.2
    assert m.eps_ttm_usd == pytest.approx(fh("TSM").eps_ttm * m.listing_factor)


def test_nvda_fcf_from_finnhub_margin_when_capex_has_a_hole():
    m = run("NVDA")
    assert m.growth_source["fcf"] == "finnhub_margin"
    assert m.years == 8 and m.window[0].startswith("2019")            # starts where revenue >= 5% of FY2026
    assert m.growth["fcf"] is not None


def test_crwv_three_years_scored_and_losing_money():
    m = run("CRWV")
    # R34: the 2023 -> 2024 revenue jump (8.4x) no longer breaks the window because 2023 revenue
    # (229M) is above the 100M shell floor, but the 5%-of-latest start rule (257M) still cuts 2023.
    # Two years, and CRWV loses money: AVOID "losing money", never "Short history".
    assert m.window == ["2024-12-31", "2025-12-31"] and m.years == 2 and m.young is False
    assert m.status is None and m.forced_avoid.startswith("losing money")
    assert m.checks["earning"].passed is False
    assert not any("revenue jump" in n for n in m.notes)
    assert any("earlier revenue < 5% of latest" in n for n in m.notes)


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
    m = synth_run(s, synth_fh(s, debt_to_equity=0.35))          # R18 (amended) threshold is 0.3
    # no proof, but R41: Finnhub's D/E x equity is a usable (over-stated) debt -> scored, capped at WAIT
    assert m.status is None and m.debt == pytest.approx(0.35 * s.ttm["equity"])
    assert m.wait_cap == "debt from Finnhub D/E 0.35 × equity (EDGAR debt unresolved)"
    assert m.checks["debt"].passed is True and "Finnhub D/E" in m.checks["debt"].detail


def test_zero_debt_accepted_with_proof():
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.01))
    assert m.checks["debt"].passed is True and "no debt" in m.checks["debt"].detail
    assert m.status is None and m.debt == 0.0
    assert m.roic_now == pytest.approx(s.ttm["net_income"] / s.ttm["equity"])


def test_stale_debt_balance_is_unresolved():
    s = synth()
    s.balance_ends["debt"] = "2014-06-30"
    f = synth_fh(s)
    m = synth_run(s, f)
    assert "debt balance stale (2014-06-30)" in m.notes
    # R41: the stale EDGAR balance is replaced by Finnhub's D/E x equity, capped at WAIT
    assert m.status is None and m.debt == pytest.approx(f.debt_to_equity * s.ttm["equity"])
    assert m.wait_cap.startswith("debt from Finnhub D/E") and m.roic_now is not None
    f.debt_to_equity = None                                          # no Finnhub D/E: unresolved for real
    m = synth_run(s, f)
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved" and m.roic_now is None


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
    m = synth_run(synth(n=2))
    assert m.status == "SHORT_HISTORY" and m.status_reason == "Short history (2 years)"
    assert m.years == 2 and m.young is False


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
    # R40: a missing cross-check is not "no data": scored, capped at WAIT, the gap named
    assert m.status is None and m.wait_cap.startswith("not cross-checked: periods differ (Finnhub FY 2024-12-31")
    assert m.checks["sources"].passed is None


def test_eps_growth_sources_disagree_uses_lower_growth_and_caps():
    s = synth()
    f = synth_fh(s)
    last = f.eps_annual[s.latest_annual_end]
    f.eps_annual = {e: last for e in f.eps_annual}                    # Finnhub flat, EDGAR +12%/yr
    m = synth_run(s, f)
    assert m.status is None and m.wait_cap == "not cross-checked: EPS growth Finnhub 0% vs EDGAR 12% (lower used)"
    assert m.growth["eps"] == pytest.approx(0.0, abs=1e-9) and m.growth_source["eps"] == "finnhub"
    assert m.checks["eps_growth"].passed is False
    assert any("EPS growth: sources differ" in n and "lower used" in n for n in m.notes)


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
    # R39/R40: a USD 10-K filer's price sanity is a cross-check only -> scored, capped at WAIT
    s = synth()
    m = synth_run(s, price=None)
    assert m.checks["sources"].passed is None
    assert "price sanity: not checked (no price)" in m.checks["sources"].detail
    assert m.status is None and m.wait_cap == "not cross-checked: price sanity (no price)"


def test_price_sanity_not_checked_without_pe():
    s = synth()
    m = synth_run(s, synth_fh(s, pe_ttm=None), price=100.0)
    assert m.checks["sources"].passed is None
    assert "price sanity: not checked (no P/E)" in m.checks["sources"].detail
    assert m.status is None and m.wait_cap == "not cross-checked: price sanity (no P/E)"


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
    assert m.checks["sources"].passed is False
    assert m.status is None and m.forced_avoid.startswith("ROIC 5% < 15%")      # R38 beats the WAIT cap
    assert m.wait_cap == "" and "wait cap: not cross-checked: price sanity (no price)" in m.notes


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
    # R40: Finnhub's EPS series missing is a missing cross-check -> scored, capped at WAIT
    s = synth()
    f = synth_fh(s, eps_annual={})
    m = compute_metrics(s, f, today=TODAY, is_etf=False, price=f.eps_ttm * f.pe_ttm)
    assert m.status is None and m.wait_cap == "not cross-checked: EPS growth: one source only"
    assert "EPS growth: one source only" in m.checks["sources"].detail and m.checks["sources"].passed is None


def test_eps_growth_edgar_only_still_allowed_for_financials():
    m = run("JPM", price=250)                        # no Finnhub eps series (see test_financial_rule_set_jpm)
    assert "EPS growth: no Finnhub series (EDGAR used)" in m.checks["sources"].detail
    assert "one source only" not in m.checks["sources"].detail


def test_market_cap_none_for_non_usd_filer():
    assert fh("TSM").market_cap_usd                  # Finnhub has a cap, but in TWD
    assert run("TSM", price=350.0).market_cap_usd is None
    assert run("AAPL").market_cap_usd == fh("AAPL").market_cap_usd   # USD filers unchanged


# ---------- first nightly audit (2026-11-06) ----------

def test_zero_debt_proof_r18_threshold():
    # R18 (amended): Finnhub's D/E includes leases, so the proof accepts D/E < 0.3 (was 0.05)
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.15))
    assert m.status is None and m.debt == 0.0 and m.checks["debt"].passed is True
    assert m.checks["debt"].detail.startswith("no debt (proof: no interest expense; Finnhub D/E 0.15")
    # D/E 0.25 with nothing tagged: the proof holds but the R22 cross-check (gap > 0.2) flags it;
    # R41: the larger candidate (Finnhub D/E x equity) is used and the row is capped at WAIT
    m = synth_run(s, synth_fh(s, debt_to_equity=0.25))
    assert m.status is None and m.debt == pytest.approx(0.25 * s.ttm["equity"])
    assert m.wait_cap == "debt: candidates differ, larger used (EDGAR D/E 0.00 vs Finnhub 0.25)"
    assert "debt understated? EDGAR D/E 0.00 vs Finnhub 0.25" in m.checks["sources"].detail
    # ... unless operating leases explain Finnhub's figure (it counts leases as debt)
    s.annual[s.latest_annual_end]["op_leases"] = 0.2 * s.ttm["equity"]
    m = synth_run(s, synth_fh(s, debt_to_equity=0.25))
    assert m.status is None and m.debt == 0.0 and m.checks["debt"].passed is True and m.wait_cap == ""
    m = synth_run(s, synth_fh(s, debt_to_equity=0.30))          # at the threshold: no proof
    assert m.status is None and m.debt == pytest.approx(0.30 * s.ttm["equity"])   # R41 estimate, capped
    assert m.wait_cap == "debt from Finnhub D/E 0.30 × equity (EDGAR debt unresolved)"


def test_missing_finnhub_de_never_proves_no_debt():
    # R23: Finnhub answered but has no debt_to_equity -> unproven, like any other case
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=None))
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved"
    assert m.debt is None and "Finnhub D/E n/a" in m.checks["debt"].detail
    assert m.roic_series == {} and not any("counted as 0" in n for n in m.notes)
    # no Finnhub at all: Finnhub leads the reasons, the debt stays unresolved
    m = compute_metrics(s, None, today=TODAY, is_etf=False, price=100.0)
    assert m.status_reason == "Finnhub: no data" and m.debt is None
    assert "check data: debt unresolved" in m.notes


def test_zero_debt_proof_roic_series_counts_zero():
    # interest absent and Finnhub D/E 0.10: the proof holds; every year without a debt figure is 0
    # in the ROIC series (equity-only denominator), so consistency is computable
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.10))
    assert m.status is None and m.checks["debt"].passed is True
    assert "no debt (proof:" in m.checks["debt"].detail and "Finnhub D/E 0.10" in m.checks["debt"].detail
    assert len(m.roic_series) == 10
    for e, r in s.annual.items():
        assert m.roic_series[e] == pytest.approx(r["net_income"] / r["equity"])
    assert m.checks["roic_consistency"].passed is True and m.rule1_business
    assert any(n.startswith("debt: 2016, 2017") and "counted as 0 (no-debt proof" in n for n in m.notes)


def test_zero_debt_proof_fails_keeps_debt_unresolved():
    for kw, fh_kw in ((dict(interest_share=0.05), dict(debt_to_equity=None)),     # interest 5% of op income
                      (dict(interest_share=None), dict(debt_to_equity=2.0))):     # D/E above liabilities/equity 1.5
        s = synth(debt=None, **kw)
        m = synth_run(s, synth_fh(s, **fh_kw))
        assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved", kw
        assert m.checks["roic_consistency"].passed is None and m.roic_series == {}
        assert not any("counted as 0" in n for n in m.notes)


def test_debt_only_in_recent_years_without_proof_meta():
    # META: debt tagged only since 2022, interest 1.5% of operating income -> no proof, so the older
    # years stay out of the ROIC series and consistency stays n/a (unchanged behaviour)
    s = synth(interest_share=0.015)
    for e in sorted(s.annual)[:-4]:
        del s.annual[e]["debt"]
    m = synth_run(s, synth_fh(s, debt_to_equity=0.28))
    assert m.debt == s.ttm["debt"] and m.checks["debt"].passed is True
    assert sorted(m.roic_series) == sorted(s.annual)[-4:]
    assert m.checks["roic_consistency"].passed is None
    assert m.checks["roic_consistency"].detail == "only 4 computable ROIC years in the window"
    assert not m.rule1_business and not any("counted as 0" in n for n in m.notes)


def test_negative_finnhub_eps_is_avoid_losing_money_not_check_data():
    from long_term.valuation import signal, valuation
    s = synth()
    _set_latest(s, net_income=-10.0, eps=-1.0)                       # EDGAR EPS -1 (10 shares)
    f = synth_fh(s, eps_ttm=-1.0, eps_incl_ttm=-1.0, pe_ttm=None)     # no earnings -> no P/E
    for price in (50.0, None):
        m = compute_metrics(s, f, today=TODAY, is_etf=False, price=price)
        assert "price sanity: n/a (no earnings)" in m.checks["sources"].detail
        assert "price sanity not checked" not in m.status_reason
        assert m.status is None, m.status_reason
        assert m.checks["earning"].passed is False
        out = signal(m, valuation(m, f), price=price, price_ok=price is not None, mos=50)
        assert out["signal"] == "AVOID" and out["reason"] == "losing money"


def test_positive_eps_without_pe_is_still_not_checked():
    s = synth()
    m = synth_run(s, synth_fh(s, pe_ttm=None), price=100.0)
    assert m.status is None and m.wait_cap == "not cross-checked: price sanity (no P/E)"


# ---------- review round 3 ----------

def test_no_zero_backfill_when_debt_is_tagged_today():
    # interest 0.5% and D/E 0.28 would satisfy the proof, but debt IS tagged in the last 4 years,
    # so the older years must not be zeroed (that would inflate ROIC consistency)
    s = synth(interest_share=0.005)
    for e in sorted(s.annual)[:-4]:
        del s.annual[e]["debt"]
    m = synth_run(s, synth_fh(s, debt_to_equity=0.28))
    assert m.debt == s.ttm["debt"]
    assert m.checks["roic_consistency"].passed is None
    assert not any("counted as 0" in n for n in m.notes)


def test_negative_finnhub_de_never_proves_no_debt():
    s = synth(debt=None, interest_share=None)
    m = synth_run(s, synth_fh(s, debt_to_equity=-0.5))
    assert m.status_reason == "Check data: debt unresolved" and m.debt is None


def test_debt_understated_cross_check():
    s = synth(debt=20.0)                                       # our D/E 20 / 138.7 = 0.14
    m = synth_run(s, synth_fh(s, debt_to_equity=0.90))
    # R41: candidates differ by > 25% -> the LARGER debt (Finnhub D/E x equity) is used, noted, capped
    assert m.status is None and m.debt == pytest.approx(0.90 * s.ttm["equity"])
    assert m.wait_cap == "debt: candidates differ, larger used (EDGAR D/E 0.14 vs Finnhub 0.90)"
    assert "debt understated? EDGAR D/E 0.14 vs Finnhub 0.90" in m.checks["sources"].detail
    assert m.checks["sources"].passed is None
    assert m.checks["debt"].value == pytest.approx(0.90 * s.ttm["equity"] / m.fcf_ttm)
    assert m.roic_now == pytest.approx(s.ttm["net_income"] / (s.ttm["equity"] * 1.90))
    # Finnhub only a little higher (< 2x, or a gap <= 0.2): fine, EDGAR's figure stands
    m = synth_run(s, synth_fh(s, debt_to_equity=0.25))
    assert m.status is None and m.wait_cap == "" and m.debt == 20.0
    assert synth_run(s, synth_fh(s, debt_to_equity=0.33)).wait_cap == ""   # >= 2x but gap 0.19
    # operating leases count on our side (Finnhub's figure includes them)
    s.annual[s.latest_annual_end]["op_leases"] = 100.0
    m = synth_run(s, synth_fh(s, debt_to_equity=0.90))
    assert m.status is None and m.wait_cap == "" and m.debt == 20.0


@pytest.mark.parametrize("sym", ["AAPL", "HD", "MCD", "GOOGL", "NVDA"])
def test_debt_understated_does_not_trip_on_fixtures(sym):
    m = run(sym)
    assert "debt understated" not in m.checks["sources"].detail and "debt understated" not in m.status_reason


def test_eps_exactly_zero_is_no_earnings():
    from long_term.valuation import signal, valuation
    s = synth()
    _set_latest(s, net_income=0.0, eps=0.0)
    f = synth_fh(s, eps_ttm=0.0, eps_incl_ttm=0.0, pe_ttm=None)
    m = compute_metrics(s, f, today=TODAY, is_etf=False, price=50.0)
    assert "price sanity: n/a (no earnings)" in m.checks["sources"].detail and m.status is None
    assert signal(m, valuation(m, f), price=50.0, price_ok=True, mos=50)["reason"] == "losing money"


def test_negative_eps_financial_row_is_no_earnings():
    from long_term.valuation import signal, valuation
    s = synth()
    s.sic = 6021                                             # bank: Financial rule set
    _set_latest(s, net_income=-10.0, eps=-1.0)
    f = synth_fh(s, eps_ttm=-1.0, eps_incl_ttm=-1.0, pe_ttm=None)
    m = compute_metrics(s, f, today=TODAY, is_etf=False, price=50.0)
    assert m.financial and "price sanity: n/a (no earnings)" in m.checks["sources"].detail
    assert m.status is None and m.checks["earning"].passed is False
    out = signal(m, valuation(m, f), price=50.0, price_ok=True, mos=50)
    assert out["signal"] == "AVOID" and out["reason"] == "losing money"


# ---------- "no data" fix (2026-11-07): R33 R34 R35 R38 R39 R40 R41 ----------

def test_three_and_four_year_rows_score_as_young():
    # R33: MIN_YEARS 3; growth from single endpoints; ROIC consistency over the 3 computable years
    for n in (3, 4):
        s = synth(n=n)
        m = synth_run(s)
        assert m.status is None, (n, m.status_reason)
        assert m.years == n and m.young is True and m.forced_avoid == "" and m.wait_cap == ""
        assert m.growth["sales"] == pytest.approx(0.12, abs=2e-3)
        assert m.checks["sales_growth"].passed is True and m.checks["eps_growth"].passed is True
        assert m.checks["roic_consistency"].passed is True
        assert m.checks["roic_consistency"].detail == f"ROIC ≥ 10% in {n} of {n} years"
        assert m.checks["history"].passed is True and m.checks["history"].threshold == "≥ 3 complete years, not stale"
        assert m.eps_3yr_avg is not None


def test_ten_year_row_still_needs_five_consistency_years():
    # a long window with only 4 computable ROIC years stays n/a (unchanged; see the META test)
    s = synth(interest_share=0.015)
    for e in sorted(s.annual)[:-4]:
        del s.annual[e]["debt"]
    m = synth_run(s, synth_fh(s, debt_to_equity=0.28))
    assert m.checks["roic_consistency"].passed is None


def test_revenue_jump_breaks_window_only_under_the_shell_floor():
    # R34: a > 5x jump breaks the window only when the pre-jump revenue is under 100M (shells)
    big = rows([200e6, 220e6, 1.5e9, 1.6e9, 1.7e9, 1.8e9, 1.9e9, 2.0e9, 2.1e9, 2.2e9])
    assert growth_window(big)[0] == "2016-12-31" and len(growth_window(big)) == 10
    shell = rows([20e6, 22e6, 1.5e9, 1.6e9, 1.7e9, 1.8e9, 1.9e9, 2.0e9, 2.1e9, 2.2e9])
    assert growth_window(shell)[0] == "2018-12-31" and len(growth_window(shell)) == 8
    # the 5%-of-latest start rule is untouched
    small = rows([1, 2, 3, 4, 10, 20, 30, 40, 50, 60])
    assert growth_window(small)[0] == "2018-12-31"


def test_revenue_jump_floor_is_usd_equivalent():
    annual = rows([2.0e9, 2.2e9, 15e9, 16e9, 17e9, 18e9, 19e9, 20e9, 21e9, 22e9])
    assert growth_window(annual, currency="USD")[0] == "2016-12-31"        # 2.0B USD: real company
    assert growth_window(annual, currency="JPY")[0] == "2018-12-31"        # 2.0B JPY ~ 13M USD: shell
    assert growth_window(annual, currency="EUR")[0] == "2016-12-31"


def test_window_drops_duplicate_fiscal_years_under_350_days_apart():
    # TLRY / SYM carry a second entity's fiscal years next to their own: the later row of a pair wins
    annual = rows([10, 11, 12, 13, 14, 15, 16, 17], start=2018, md="09-30")
    annual["2021-12-31"] = dict(revenue=99.0, net_income=1.0, eps=1.0, equity=10.0, cfo=2.0, capex=1.0)   # SPAC shell
    w = growth_window(annual)
    assert "2021-12-31" not in w and "2021-09-30" in w and len(w) == 8
    s = synth()
    s.annual["2025-06-30"] = dict(s.annual["2025-12-31"])            # a stray row 184 days before the latest
    m = synth_run(s)
    assert "2025-06-30" not in m.window and m.years == 10
    assert any(n.startswith("window: 2025-06-30 dropped (") for n in m.notes)


def ttm_based(s):
    """Stamp the synthetic company's flows as a TTM (a 10-K filer with 10-Qs), not the FY."""
    for c in list(s.ttm_ends):
        s.ttm_ends[c] = (s.latest_period_end, False)
    return s


def _pre_revenue(revenue="absent", ni=-5.0):
    s = synth(n=6)
    for r in s.annual.values():
        r["net_income"] = ni
        r["eps"] = ni / 10.0
        if revenue == "absent":
            del r["revenue"]
        else:
            r["revenue"] = revenue
    s.ttm["net_income"] = ni
    if revenue == "absent":
        del s.ttm["revenue"], s.ttm_ends["revenue"]
    else:
        s.ttm["revenue"] = revenue
    return s


def test_pre_revenue_company_is_avoid_no_revenue_yet():
    from long_term.valuation import signal, valuation
    for revenue in ("absent", 0.0):
        s = _pre_revenue(revenue)
        f = synth_fh(s, eps_ttm=-0.5, eps_incl_ttm=-0.5, pe_ttm=None, roi_ttm=-20.0)
        m = synth_run(s, f, price=10.0)
        assert m.pre_revenue is True and m.status is None and m.status_reason == "", revenue
        assert m.forced_avoid == "no revenue yet" and m.years == 0
        out = signal(m, valuation(m, f), price=10.0, price_ok=True, mos=50)
        assert out["signal"] == "AVOID" and out["reason"] == "no revenue yet" and out["leaps"] is False
        assert all(out[k] is None for k in ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct"))


def test_pre_revenue_needs_losses_a_profitable_row_without_revenue_is_a_tag_gap():
    # HSBC-shaped: net income and equity but no revenue tag -> honest SHORT_HISTORY, never "no revenue yet"
    s = _pre_revenue("absent", ni=20.0)
    m = synth_run(s, synth_fh(s, eps_ttm=2.0, eps_incl_ttm=2.0))
    assert m.pre_revenue is False and m.status == "SHORT_HISTORY" and m.forced_avoid == ""


def test_revenue_after_the_latest_annual_is_not_pre_revenue():
    # OKLO-shaped: no annual revenue yet, but the TTM already carries revenue > 0 -> not pre-revenue;
    # the company loses money, so the row is AVOID "losing money" (never "no data")
    s = _pre_revenue("absent")
    s.ttm["revenue"], s.ttm_ends["revenue"] = 3.0, ("2026-09-30", False)
    m = synth_run(s, synth_fh(s, eps_ttm=-0.5, eps_incl_ttm=-0.5, pe_ttm=None, roi_ttm=-20.0), price=10.0)
    assert m.pre_revenue is False and m.status is None and m.forced_avoid == "losing money"


def test_loss_maker_with_short_history_is_avoid_losing_money():
    # LAES-shaped: the latest year has net income < 0 but no EPS, so the window is empty; the
    # company loses money, which is enough for AVOID (R38 spirit), never "Short history"
    from long_term.valuation import signal, valuation
    s = synth(n=6)
    lae = s.latest_annual_end
    f = synth_fh(s, eps_ttm=-0.38, eps_incl_ttm=-0.38, pe_ttm=None, roi_ttm=-12.0)
    del s.annual[lae]["eps"]
    _set_latest(s, net_income=-30.0)
    m = synth_run(s, f, price=10.0)
    assert m.years == 0 and m.status is None and m.forced_avoid == "losing money"
    assert signal(m, valuation(m, f), price=10.0, price_ok=True, mos=50)["reason"] == "losing money"
    # a 2-year company that loses money: AVOID too (true, and fail-safe)
    s2 = synth(n=2)
    _set_latest(s2, net_income=-5.0, eps=-0.5)
    m2 = synth_run(s2, synth_fh(s2, eps_ttm=-0.5, eps_incl_ttm=-0.5, pe_ttm=None), price=10.0)
    assert m2.status is None and m2.forced_avoid == "losing money"
    # ... while a 2-year company that earns money is still SHORT_HISTORY
    assert synth_run(synth(n=2)).status == "SHORT_HISTORY"


def test_check_data_with_both_eps_negative_is_avoid_losing_money():
    # R38: the sources disagree on the size of the loss but both say loss -> AVOID "losing money"
    s = ttm_based(synth())
    _set_latest(s, net_income=-10.0, eps=-1.0)
    f = synth_fh(s, eps_ttm=-3.0, eps_incl_ttm=-3.0, pe_ttm=None)    # EDGAR -1.00 vs Finnhub -3.00
    m = synth_run(s, f, price=10.0)
    assert "EPS -1.00" in m.checks["sources"].detail and m.checks["sources"].passed is False
    assert m.status is None and m.forced_avoid == "losing money"


def test_check_data_with_one_source_negative_is_avoid_sources_differ():
    s = ttm_based(synth())
    f = synth_fh(s, eps_ttm=-0.5, eps_incl_ttm=-0.5, pe_ttm=None)    # EDGAR +5.55 vs Finnhub -0.50
    m = synth_run(s, f, price=10.0)
    assert m.status is None and m.forced_avoid == "losing money (sources differ on the size)"


def test_check_data_stays_when_the_worse_source_still_passes():
    # EPS disagree (both positive, ROIC fine either way, debt fine): still CHECK_DATA (R38 keeps it)
    s = ttm_based(synth())
    f = synth_fh(s)
    f.eps_ttm = f.eps_incl_ttm = f.eps_ttm * 2.0
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm / 2.0)          # price sanity ok at the EDGAR EPS
    assert m.status == "CHECK_DATA" and m.status_reason.startswith("Check data: EPS ")
    assert m.forced_avoid == ""


def test_check_data_eps_disagreement_scales_roic_to_the_lower_eps():
    # R38 example: ROIC evaluated with the LESS favourable EPS -> "ROIC 7% < 15% (sources differ on EPS)"
    s = ttm_based(synth())
    for r in s.annual.values():
        r["net_income"] = r["revenue"] * 0.1                           # ROIC ~ 17%
        r["eps"] = r["net_income"] / r["shares"]
    s.ttm["net_income"] = s.annual[s.latest_annual_end]["net_income"]
    f = synth_fh(s)
    f.eps_ttm = f.eps_incl_ttm = f.eps_ttm / 3.0                       # Finnhub EPS a third of EDGAR's
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm * 3.0)
    assert m.roic_now > 0.15
    assert m.status is None and m.forced_avoid.endswith("< 15% (sources differ on EPS)")
    assert m.forced_avoid.startswith("ROIC 6%") or m.forced_avoid.startswith("ROIC 7%")


def test_negative_equity_roic_na_caps_at_wait_or_avoids_on_finnhub():
    # R40: ROIC n/a from negative equity is scored and capped at WAIT; R38: Finnhub ROI < 15% -> AVOID
    s = synth(debt=5.0)
    _set_latest(s, equity=-10.0)                                       # denominator -5 -> n/a
    m = synth_run(s, synth_fh(s, roi_ttm=25.0, debt_to_equity=-0.5))
    assert m.roic_now is None and m.status is None
    assert m.wait_cap == "ROIC not measurable: negative equity"
    assert m.checks["roic_now"].passed is None
    m = synth_run(s, synth_fh(s, roi_ttm=9.6, debt_to_equity=-0.5))
    assert m.status is None and m.forced_avoid == "ROIC 10% < 15% (Finnhub; EDGAR not measurable)"


def test_missing_finnhub_eps_caps_at_wait():
    s = synth()
    f = synth_fh(s, eps_ttm=None, eps_incl_ttm=None)
    m = synth_run(s, f, price=100.0)
    assert m.status is None
    assert m.wait_cap == "not cross-checked: Finnhub EPS missing"
    assert m.checks["sources"].passed is None and m.checks["earning"].passed is True
    assert m.checks["earning"].detail.startswith("EPS 5.55 (EDGAR)")


def test_wait_cap_lists_every_gap_in_notes():
    s = synth()
    f = synth_fh(s, eps_ttm=None, eps_incl_ttm=None, eps_annual={})
    m = synth_run(s, f, price=100.0)
    assert m.wait_cap == "not cross-checked: Finnhub EPS missing"
    assert "wait cap: not cross-checked: EPS growth: one source only" in m.notes


def test_debt_unresolved_estimate_needs_plausible_interest():
    # interest 5% of operating income cannot be carried by a 1%-of-equity debt: the Finnhub estimate
    # is implausible, so the debt stays unresolved (CHECK_DATA)
    s = synth(debt=None, interest_share=0.05)
    m = synth_run(s, synth_fh(s, debt_to_equity=0.01))
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved"
    assert any("implausible" in n for n in m.notes)
    # the same interest with a D/E that can carry it: estimate used, capped
    m = synth_run(s, synth_fh(s, debt_to_equity=0.50))
    assert m.status is None and m.debt == pytest.approx(0.5 * s.ttm["equity"])
    assert m.wait_cap == "debt from Finnhub D/E 0.50 × equity (EDGAR debt unresolved)"


def test_finnhub_de_above_liabilities_is_ignored_and_no_debt_proof_uses_liabilities():
    # CAVA-shaped: no debt tag ever, no interest, Finnhub D/E 1.51 while liabilities/equity is 0.74
    s = synth(debt=None, interest_share=None)
    lae = s.latest_annual_end
    eq = s.ttm["equity"]
    _set_latest(s, assets=eq * 1.74, op_leases=eq * 0.60)              # liabilities 0.74 eq, leases 0.60 eq
    m = synth_run(s, synth_fh(s, debt_to_equity=1.51))
    assert m.status is None and m.debt == 0.0 and m.checks["debt"].passed is True and m.wait_cap == ""
    assert any(n.startswith("Finnhub D/E 1.51 not reproducible (liabilities/equity 0.74") for n in m.notes)
    assert "debt understated" not in m.checks["sources"].detail
    # without the leases the non-lease liabilities (0.74 eq) could hide debt: no proof, no estimate
    del s.annual[lae]["op_leases"], s.ttm["op_leases"]
    m = synth_run(s, synth_fh(s, debt_to_equity=1.51))
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: debt unresolved"


def test_debt_understated_compares_at_the_fiscal_year_end_too():
    # PL / STX: an equity raise after the FY end makes the TTM D/E look far below Finnhub's FY-end
    # figure; when the FY-end comparison agrees, the flag is dropped
    s = synth(debt=100.0)
    s.ttm["equity"] = s.ttm["equity"] * 4.0                            # TTM D/E a quarter of the FY's
    s.balance_ends["equity"] = "2026-09-30"
    fy = s.annual[s.latest_annual_end]
    de = 100.0 / fy["equity"]
    m = synth_run(s, synth_fh(s, debt_to_equity=de))
    assert m.status is None and m.wait_cap == "" and "debt understated" not in m.checks["sources"].detail


def test_capex_unknown_with_negative_cfo_is_avoid():
    # RUN / TLRY: capex unknown => FCF <= CFO; CFO < 0 settles it: AVOID "cash from operations negative"
    s = synth()
    lae = s.latest_annual_end
    del s.annual[lae]["capex"], s.ttm["capex"], s.ttm_ends["capex"]
    _set_latest(s, cfo=-50.0)
    m = synth_run(s)
    assert m.fcf_ttm is None and m.status is None
    assert m.forced_avoid == "cash from operations negative"
    # capex unknown with CFO > 0: nothing can be said -> honest SHORT_HISTORY (latest row incomplete)
    _set_latest(s, cfo=50.0)
    m = synth_run(s)
    assert m.status == "SHORT_HISTORY" and m.forced_avoid == ""


def test_eps_now_prefers_the_snapshot_ttm_eps_tag():
    # IBKR / MCHP / BYND: when the snapshot carries a TTM EPS from the EPS tag, it beats NI / shares
    s = synth()
    s.ttm["eps"], s.ttm_ends["eps"] = 1.23, (s.latest_annual_end, True)
    m = synth_run(s)
    assert m.eps_ttm_edgar == 1.23
    del s.ttm["eps"], s.ttm_ends["eps"]
    m = synth_run(s)
    assert m.eps_ttm_edgar == pytest.approx(s.ttm["net_income"] / s.ttm["shares"])


def test_annual_only_usd_filer_compares_fy_eps_with_finnhub_annual():
    # WB: a 20-F filer's EDGAR "TTM" is the FY; Finnhub's TTM runs two quarters later. Compare the FY
    # with Finnhub's annual EPS for the same FY instead of calling it a disagreement.
    s = synth(lpe="2025-12-31")
    s.annual_form = "20-F"
    f = synth_fh(s)
    fy_eps = s.annual[s.latest_annual_end]["eps"]
    f.eps_ttm = f.eps_incl_ttm = fy_eps * 0.7                          # newer TTM, 30% lower
    f.pe_ttm = 20.0
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm)
    assert f"EPS {fy_eps:.2f} (FY {s.latest_annual_end}) vs Finnhub FY {fy_eps:.2f} ok" in m.checks["sources"].detail
    # the TTM in use is Finnhub's and newer than anything EDGAR can confirm: capped at WAIT (R40)
    assert m.status is None and m.checks["sources"].passed is None
    assert m.wait_cap == f"not cross-checked: Finnhub EPS TTM {f.eps_ttm:.2f} newer than EDGAR FY {fy_eps:.2f}"
    f.eps_ttm = f.eps_incl_ttm = fy_eps                                # Finnhub's TTM still the FY: no cap
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm)
    assert m.status is None and m.wait_cap == "" and m.checks["sources"].passed is True


def test_forced_avoid_never_set_on_a_clean_row():
    m = synth_run(synth())
    assert m.forced_avoid == "" and m.wait_cap == "" and m.pre_revenue is False


# ---------- "no data" fix round (2026-11-07, after review): M1-M5 ----------

def test_tenk_means_a_usd_10k_filer_cad_reporter_keeps_the_listing_factor():
    # R39 (fix round M1): `tenk` is a USD 10-K filer. A 10-K filer reporting in CAD with a USD quote
    # keeps its listing factor (the FX), price sanity stays a CHECK_DATA blocker, and the sticker is
    # never computed from unconverted CAD EPS.
    from long_term.valuation import valuation
    s = synth(lpe="2026-09-30")
    s.currency, s.annual_form = "CAD", "10-K"
    f = synth_fh(s)
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm * 0.73)             # USD price = CAD-implied x 0.73
    assert m.status is None and m.listing_factor == pytest.approx(0.73) and m.listing_factor != 1.0
    assert m.eps_ttm_usd == pytest.approx(f.eps_ttm * 0.73)
    assert "listing factor 0.7300" in m.checks["sources"].detail and "note only" not in m.checks["sources"].detail
    v = valuation(m, f)
    assert v.sticker_eps == pytest.approx(min(f.eps_ttm * 0.73, 1.5 * m.eps_3yr_avg * 0.73))
    # no P/E: the listing factor cannot be checked -> blocker (not a WAIT cap), and no sticker
    f2 = synth_fh(s, pe_ttm=None)
    m = synth_run(s, f2, price=100.0)
    assert m.status == "CHECK_DATA" and m.status_reason == "Check data: price sanity not checked"
    assert m.eps_ttm_usd is None and m.listing_factor is None and valuation(m, f2).sticker is None
    assert not any("price sanity" in n for n in m.notes if n.startswith("wait cap"))


def test_annual_only_filer_with_a_newer_finnhub_fy_is_periods_differ_even_when_eps_agree():
    # R40 (fix round M2, the reviewer's probe): EDGAR's latest FY is 2025, Finnhub's annual series
    # already carries FY2026 and its TTM is 1.2x EDGAR's FY (inside the EPS tolerance). Every EPS
    # behind the sticker is then one source: capped at WAIT, no BUY / LEAPS level at any price.
    from long_term.valuation import signal, valuation
    s = synth(lpe="2025-12-31")
    s.annual_form = "20-F"
    f = synth_fh(s)
    fy_eps = s.annual[s.latest_annual_end]["eps"]
    f.eps_annual["2026-12-31"], f.pe_annual["2026-12-31"] = fy_eps * 1.25, 20.0
    f.eps_ttm = f.eps_incl_ttm = fy_eps * 1.2
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm)
    assert f"EPS {fy_eps:.2f} (FY 2025-12-31) vs Finnhub FY {fy_eps:.2f} ok" in m.checks["sources"].detail
    assert m.status is None and m.forced_avoid == "" and m.checks["sources"].passed is None
    assert m.wait_cap == "not cross-checked: periods differ (Finnhub FY 2026-12-31 vs EDGAR 2025-12-31)"
    out = signal(m, valuation(m, f), price=1.0, price_ok=True, mos=50)
    assert out["signal"] == "WAIT" and out["reason"] == m.wait_cap and out["leaps"] is False
    assert out["buy_below"] is None and out["leaps_below"] is None
    # WB's behaviour where the periods do NOT differ is kept: a TTM 1.2x the FY (inside the
    # tolerance) is not a cap, one 30% off is the "newer than EDGAR FY" cap
    del f.eps_annual["2026-12-31"], f.pe_annual["2026-12-31"]
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm)
    assert m.status is None and m.wait_cap == "" and m.checks["sources"].passed is True
    f.eps_ttm = f.eps_incl_ttm = fy_eps * 0.7
    m = synth_run(s, f, price=f.eps_ttm * f.pe_ttm)
    assert m.wait_cap == f"not cross-checked: Finnhub EPS TTM {f.eps_ttm:.2f} newer than EDGAR FY {fy_eps:.2f}"


def test_four_year_row_with_one_uncomputable_roic_year_is_judged_on_three():
    # R33 (fix round M3): a 3-4 year row needs >= 3 computable ROIC years, not every year
    from long_term.valuation import signal, valuation
    s = synth(n=4)
    first = sorted(s.annual)[0]
    del s.annual[first]["debt"]                     # interest 2% of op income: no zero-debt proof -> year n/a
    f = synth_fh(s)
    m = synth_run(s, f)
    assert m.years == 4 and sorted(m.roic_series) == sorted(s.annual)[1:]
    assert m.checks["roic_consistency"].passed is True
    assert m.checks["roic_consistency"].detail == "ROIC ≥ 10% in 3 of 3 years"
    assert m.status is None and m.forced_avoid == "" and m.wait_cap == "" and m.rule1_business
    out = signal(m, valuation(m, f), price=1.0, price_ok=True, mos=50)
    assert out["signal"] == "WAIT" and out["reason"] == "young: 4 years, BUY needs 8"
    # a 4-year row with two uncomputable years is still n/a
    del s.annual[sorted(s.annual)[1]]["debt"]
    m = synth_run(s, synth_fh(s))
    assert m.checks["roic_consistency"].passed is None
    assert m.checks["roic_consistency"].detail == "only 2 computable ROIC years in the window"


def test_capex_larger_used_inside_the_window_caps_at_wait():
    # fix round M4 (fail-safe): edgar's "larger used" capex is fail-safe for FCF now but not for FCF
    # growth (a larger early-year capex lowers the early FCF and inflates the growth): two tags, one
    # unconfirmed -> WAIT at most. The same note for years before the window does not matter.
    tag = "(PaymentsToAcquireProductiveAssets)"
    s = synth()
    s.notes.append(f"capex: candidates differ, larger used in 2016, 2017, 2018 {tag}")
    m = synth_run(s)
    assert m.status is None and m.forced_avoid == ""
    assert m.wait_cap == "not cross-checked: capex from two tags (larger used in 2016-2018)"
    s = synth()
    s.notes.append(f"capex: candidates differ, larger used in 2014, 2015, 2016 {tag}")
    m = synth_run(s)
    assert m.wait_cap == "not cross-checked: capex from two tags (larger used in 2016)"
    s = synth()
    s.notes.append(f"capex: candidates differ, larger used in 2009, 2010 {tag}")
    s.notes.append("debt: candidates differ, larger used in 2016, 2017 (DebtAndCapitalLeaseObligations)")
    m = synth_run(s)
    assert m.wait_cap == "" and not any("capex from two tags" in n for n in m.notes)


def test_derived_eps_inside_the_window_caps_without_a_finnhub_series():
    # fix round M5 (fail-safe): EPS derived from NI/shares is one source; unless a full Finnhub annual
    # series confirms the EPS growth, the row is WAIT at most. Derived years before the window and
    # rows with an agreeing Finnhub series are untouched.
    cap = "not cross-checked: EPS derived in 2016, 2017"
    s = synth()
    s.notes.append("eps: 2016, 2017 derived from NI/shares")
    m = synth_run(s, synth_fh(s, eps_annual={}))
    assert m.status is None and m.growth_source["eps"] == "edgar" and m.wait_cap == cap
    assert "wait cap: not cross-checked: EPS growth: one source only" in m.notes
    m = synth_run(s)                                                  # full Finnhub series agrees: no cap
    assert m.wait_cap == "" and not any("EPS derived in" in n for n in m.notes)
    s.sic = 6021                                                      # bank: the "no Finnhub series" branch
    m = synth_run(s, synth_fh(s, eps_annual={}))
    assert m.financial and m.status is None and m.wait_cap == cap
    assert "EPS growth: no Finnhub series (EDGAR used)" in m.checks["sources"].detail
    s = synth()
    s.notes.append("eps: 2008, 2009 derived from NI/period-end shares")
    m = synth_run(s, synth_fh(s, eps_annual={}))
    assert m.wait_cap == "not cross-checked: EPS growth: one source only"
    assert not any("EPS derived in" in n for n in m.notes)


def test_short_history_row_with_failing_roic_is_avoid_with_the_reason():
    # R46 (NEE-shaped): no capex tag so the window is empty, but the TTM ROIC is 7% on both
    # sources: AVOID "ROIC 7% < 15%" beats "Short history (0 years)"; a short history with a
    # passing ROIC stays SHORT_HISTORY (fail-safe both ways)
    from long_term.valuation import signal, valuation
    s = synth(n=6)
    for e in s.annual:
        s.annual[e].pop("capex", None)
    s.ttm.pop("capex", None)
    f = synth_fh(s, roi_ttm=7.0)
    _set_latest(s, net_income=7.0)                       # equity 100 (synth): ROIC 7%
    m = synth_run(s, f, price=10.0)
    assert m.years == 0 and m.status is None, (m.years, m.status, m.status_reason)
    assert m.forced_avoid.startswith("ROIC ") and "< 15%" in m.forced_avoid, m.forced_avoid
    assert signal(m, valuation(m, f), price=10.0, price_ok=True, mos=50)["signal"] == "AVOID"
    # the same empty window with a healthy ROIC is an honest SHORT_HISTORY
    s2 = synth(n=6)
    for e in s2.annual:
        s2.annual[e].pop("capex", None)
    s2.ttm.pop("capex", None)
    m2 = synth_run(s2, synth_fh(s2), price=10.0)
    assert m2.years == 0 and m2.status == "SHORT_HISTORY" and m2.forced_avoid == ""
