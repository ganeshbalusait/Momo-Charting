"""Long Term: EDGAR fact selection, tag resolution, units and splits. No network.

Fixtures are trimmed real SEC company-facts files (tests/fixtures/long_term).
"""
import json
import re
from datetime import date
from pathlib import Path

import pytest

from long_term import edgar
from long_term.edgar import (
    ALL_TAGS,
    ANNUAL_FORMS,
    CONCEPTS,
    TAGS_BY_NAMESPACE,
    EdgarBlocked,
    EdgarClient,
    Fact,
    Snapshot,
    annual_facts,
    apply_split,
    build_snapshot,
    infer_split_factors,
    is_financial_sic,
    latest_quarterly_shares,
    pick_unit,
    resolve_concept,
    resolve_series,
    to_facts,
    ttm_flows,
)

FIX = Path(__file__).parent / "fixtures" / "long_term"
SYMBOLS = ["AAPL", "ASML", "TSM", "HOOD", "MCD", "HD", "JPM", "GOOGL", "NVDA", "CRWV"]
CIKS = {"AAPL": 320193, "ASML": 937966, "TSM": 1046179, "HOOD": 1783879, "MCD": 63908,
        "HD": 354950, "JPM": 19617, "GOOGL": 1652044, "NVDA": 1045810, "CRWV": 1769628}
FA = "2026-11-06T03:00:00-05:00"


def load(sym):
    return json.loads((FIX / f"companyfacts_{sym}.json").read_text(encoding="utf-8"))


def gaap(doc):
    return doc["facts"].get("us-gaap", {})


def ifrs(doc):
    return doc["facts"].get("ifrs-full", {})


def days(f):
    return (date.fromisoformat(f.end) - date.fromisoformat(f.start)).days


def eps_facts(doc):
    return to_facts(gaap(doc)["EarningsPerShareDiluted"]["units"], "EarningsPerShareDiluted")


def snap(sym):
    return build_snapshot(sym, CIKS[sym], load(sym), sic=None, name=sym, fetched_at=FA)


@pytest.fixture(scope="module")
def aapl():
    return load("AAPL")


@pytest.fixture(scope="module")
def tsm():
    return load("TSM")


@pytest.fixture(scope="module")
def googl():
    return load("GOOGL")


@pytest.fixture(scope="module")
def nvda():
    return load("NVDA")


# ---------- the brief's tests ----------

def test_annual_rows_use_duration_not_fy(aapl):   # FY2025 10-K carries FY2024/2023 comparatives
    rows = annual_facts(aapl["facts"]["us-gaap"]["NetIncomeLoss"]["units"])["USD"]
    assert rows["2026-09-26"].val == 132175000000 and rows["2025-09-27"].form == "10-K"
    assert all(350 <= days(r) <= 380 for r in rows.values())


def test_revenue_stitches_asc606_rename(aapl):     # SalesRevenueNet to 2017, RevenueFromContract... from 2018
    series, unit, tag, broke = resolve_concept(gaap(aapl), {}, "revenue")
    assert unit == "USD" and len(series) >= 10 and not broke
    assert "2012-09-29" in series and "2026-09-26" in series   # both ends of the rename are present


def test_one_unit_per_series_tsm(tsm):
    # The fixture's TWD covers 10 annual years (2015-2024) and USD only 8 (2017-2024): USD would
    # be preferred only on >= coverage, so the series must be TWD, and every value from TWD.
    s = resolve_series({}, ifrs(tsm), "net_income")
    assert s.unit == "TWD" and len(s.values) == 10
    twd = annual_facts(ifrs(tsm)[s.tag.split(":")[-1]]["units"])["TWD"]
    assert all(s.values[e] == twd[e].val for e in s.values)
    series, unit, *_ = resolve_concept({}, ifrs(tsm), "net_income")
    assert unit == "TWD" and series == s.values


def test_debt_recipe_does_not_double_count(aapl):  # LongTermDebtNoncurrent + Current + CommercialPaper, not + LongTermDebt
    cp = annual_facts(gaap(aapl)["CommercialPaper"]["units"])["USD"]["2026-09-26"].val
    series, unit, tag, _ = resolve_concept(gaap(aapl), {}, "debt")
    assert abs(series["2026-09-26"] - (68208000000 + 9906000000 + cp)) < 1e6
    assert tag.startswith("LongTermDebtNoncurrent+LongTermDebtCurrent")


def test_split_factor_googl(googl):               # FY2020 EPS 58.61 (2021 10-K) vs 2.93 (2023 10-K) -> 20
    factors = infer_split_factors(eps_facts(googl))
    assert factors["2020-12-31"] == pytest.approx(1 / 20) and factors["2019-12-31"] == pytest.approx(1 / 20)
    assert factors["2018-12-31"] == pytest.approx(1 / 20)
    assert all(factors[e] == 1.0 for e in factors if e >= "2022")


def test_reverse_split_fixture():                 # synthetic: 1.25 (old) vs 10.0 (restated) -> factor 8
    facts = [Fact("2021-12-31", "2021-01-01", 1.25, "USD/shares", "EarningsPerShareDiluted", "10-K", "a1", "2022-02-01"),
             Fact("2021-12-31", "2021-01-01", 10.0, "USD/shares", "EarningsPerShareDiluted", "10-K", "a2", "2023-02-01")]
    assert infer_split_factors(facts)["2021-12-31"] == pytest.approx(8)


def test_snapshot_googl_eps_is_split_adjusted(googl):
    s = build_snapshot("GOOGL", 1652044, googl, sic=7370, name="Alphabet", fetched_at=FA)
    assert s.annual["2018-12-31"]["eps"] == pytest.approx(43.7 / 20, rel=1e-3)
    assert s.latest_annual_end == "2025-12-31" and s.currency == "USD"


# ---------- fact selection ----------

def test_dedupe_latest_filed_then_accession():
    rows = {"USD": [
        {"start": "2020-01-01", "end": "2020-12-31", "val": 1, "accn": "0001-21-000001", "form": "10-K", "filed": "2021-02-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 2, "accn": "0001-22-000001", "form": "10-K", "filed": "2022-02-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 3, "accn": "0001-22-000009", "form": "10-K/A", "filed": "2022-02-01"},
        {"start": "2020-07-01", "end": "2020-12-31", "val": 9, "accn": "0001-23-000001", "form": "10-K", "filed": "2023-02-01"},
        {"start": "2020-01-01", "end": "2020-12-31", "val": 9, "accn": "0001-24-000001", "form": "10-Q", "filed": "2024-02-01"},
        {"end": "2020-12-31", "val": 7, "accn": "0001-24-000002", "form": "8-K", "filed": "2024-02-01"},
    ]}
    got = annual_facts(rows, tag="X")["USD"]
    assert list(got) == ["2020-12-31"] and got["2020-12-31"].val == 3 and got["2020-12-31"].tag == "X"


def test_instants_are_annual_by_form_only():
    rows = {"USD": [{"end": "2020-12-31", "val": 5, "accn": "a", "form": "10-K", "filed": "2021-02-01"},
                    {"end": "2021-03-31", "val": 6, "accn": "b", "form": "10-Q", "filed": "2021-05-01"}]}
    assert {e: f.val for e, f in annual_facts(rows)["USD"].items()} == {"2020-12-31": 5.0}


def test_annual_forms_constant():
    assert ANNUAL_FORMS == {"10-K", "10-K/A", "10-KT", "20-F", "20-F/A", "40-F"}


# ---------- units ----------

def test_pick_unit_prefers_usd_only_on_equal_or_better_coverage():
    eight = {str(y): 1 for y in range(2017, 2025)}
    ten = {str(y): 1 for y in range(2015, 2025)}
    assert pick_unit({"TWD": eight, "USD": dict(eight)}) == "USD"
    assert pick_unit({"TWD": ten, "USD": eight}) == "TWD"
    assert pick_unit({"TWD/shares": eight, "USD/shares": dict(eight)}) == "USD/shares"
    assert pick_unit({"EUR": eight}) == "EUR" and pick_unit({}) is None


def test_asml_is_euro():
    s = snap("ASML")
    assert s.currency == "EUR" and s.units["eps"] == "EUR/shares" and s.units["shares"] == "shares"


def test_tsm_snapshot_is_one_currency():
    s = snap("TSM")
    assert s.currency == "TWD" and s.units["revenue"] == "TWD" and s.units["eps"] == "TWD/shares"


# ---------- tag resolution ----------

def test_hood_revenue_under_revenues():
    series, unit, tag, broke = resolve_concept(gaap(load("HOOD")), {}, "revenue")
    assert tag == "Revenues" and len(series) == 7 and not broke


def test_bank_has_revenue_but_no_capex():
    jpm = load("JPM")
    series, unit, tag, _ = resolve_concept(gaap(jpm), {}, "revenue")
    assert tag == "Revenues" and len(series) >= 10
    assert resolve_concept(gaap(jpm), {}, "capex") is None
    assert "capex" not in snap("JPM").units


def test_fill_only_when_overlap_agrees_else_tag_break():
    def node(vals):
        return {"units": {"USD": [{"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "accn": f"a{y}",
                                   "form": "10-K", "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
    primary = {y: 100.0 for y in range(2016, 2024)}            # 8 years
    agree = {2014: 50.0, 2015: 60.0, 2016: 101.0}               # 1% off in the overlap -> fills
    disagree = {2014: 50.0, 2015: 60.0, 2016: 110.0}            # 10% off -> blank + break
    series, _, tag, broke = resolve_concept({"NetIncomeLoss": node(primary), "ProfitLoss": node(agree)}, {}, "net_income")
    assert tag == "NetIncomeLoss" and series["2014-12-31"] == 50.0 and series["2016-12-31"] == 100.0 and not broke
    series, _, _, broke = resolve_concept({"NetIncomeLoss": node(primary), "ProfitLoss": node(disagree)}, {}, "net_income")
    assert "2014-12-31" not in series and len(series) == 8 and broke


def test_revenue_takes_larger_of_revenues_and_contract_revenue():
    def node(v):
        return {"units": {"USD": [{"start": "2020-01-01", "end": "2020-12-31", "val": v, "accn": "a",
                                   "form": "10-K", "filed": "2021-02-01"}]}}
    series, *_ = resolve_concept({"Revenues": node(90.0), "RevenueFromContractWithCustomerExcludingAssessedTax": node(100.0)},
                                 {}, "revenue")
    assert series == {"2020-12-31": 100.0}


def test_debt_optional_parts_count_as_zero():
    def inst(v, y=2020):
        return {"units": {"USD": [{"end": f"{y}-12-31", "val": v, "accn": "a", "form": "10-K", "filed": "2021-02-01"}]}}
    series, _, tag, _ = resolve_concept({"LongTermDebtNoncurrent": inst(10.0), "LongTermDebtCurrent": inst(2.0)}, {}, "debt")
    assert series == {"2020-12-31": 12.0}
    # a recipe needs every required part: LongTermDebtCurrent alone is not debt
    assert resolve_concept({"LongTermDebtCurrent": inst(2.0)}, {}, "debt") is None


def test_operating_leases_are_not_debt(aapl):
    leases, *_ = resolve_concept(gaap(aapl), {}, "op_leases")
    debt, *_ = resolve_concept(gaap(aapl), {}, "debt")
    assert "2026-09-26" in leases and "OperatingLease" not in resolve_concept(gaap(aapl), {}, "debt")[2]
    assert debt["2026-09-26"] > 0


def test_capex_is_positive_magnitude():
    s = snap("AAPL")
    assert all(row["capex"] > 0 for row in s.annual.values() if "capex" in row)


def test_negative_equity_resolves():
    for sym in ("MCD", "HD"):
        eq = [row["equity"] for row in snap(sym).annual.values() if "equity" in row]
        assert len(eq) >= 10
    assert min(row["equity"] for row in snap("MCD").annual.values() if "equity" in row) < 0


# ---------- splits ----------

def test_split_factors_nvda_two_splits(nvda):
    factors = infer_split_factors(eps_facts(nvda))
    assert factors["2012-01-29"] == pytest.approx(1 / 40) and factors["2020-01-26"] == pytest.approx(1 / 40)
    assert factors["2022-01-30"] == pytest.approx(1 / 10) and factors["2026-01-25"] == 1.0


def test_split_factors_ignore_unclean_ratios_and_quarters():
    f = [Fact("2021-12-31", "2021-01-01", 1.00, "USD/shares", "E", "10-K", "a1", "2022-02-01"),
         Fact("2021-12-31", "2021-01-01", 1.13, "USD/shares", "E", "10-K", "a2", "2023-02-01"),   # restatement, not a split
         Fact("2021-06-30", "2021-04-01", 1.00, "USD/shares", "E", "10-Q", "q1", "2021-08-01"),
         Fact("2021-06-30", "2021-04-01", 0.50, "USD/shares", "E", "10-Q", "q2", "2022-08-01")]   # quarterly -> ignored
    assert infer_split_factors(f) == {"2021-12-31": 1.0}


def test_apply_split():
    assert apply_split({"a": 10.0, "b": 4.0}, {"a": 0.5}) == {"a": 5.0, "b": 4.0}


def test_snapshot_nvda_eps_and_shares_on_todays_basis(nvda):
    s = build_snapshot("NVDA", 1045810, nvda, sic=3674, name="NVIDIA", fetched_at=FA)
    assert s.annual["2012-01-29"]["eps"] == pytest.approx(0.94 / 40)
    assert s.annual["2021-01-31"]["eps"] == pytest.approx(1.73 / 10)      # latest filing already 4:1 restated
    assert s.annual["2026-01-25"]["eps"] == pytest.approx(4.9)
    raw = annual_facts(gaap(nvda)["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"])["shares"]
    assert s.annual["2026-01-25"]["shares"] == pytest.approx(raw["2026-01-25"].val)
    old = raw["2012-01-29"]
    assert s.annual["2012-01-29"]["shares"] == pytest.approx(old.val * 40)
    assert s.split_factor == pytest.approx(1 / 40)


def test_snapshot_without_split_has_factor_one():
    assert snap("MCD").split_factor == 1.0


# ---------- snapshot shape ----------

def test_snapshot_shape(aapl):
    s = build_snapshot("AAPL", 320193, aapl, sic=3571, name="Apple Inc.", fetched_at=FA)
    assert isinstance(s, Snapshot)
    assert (s.symbol, s.cik, s.name, s.sic, s.fetched_at) == ("AAPL", 320193, "Apple Inc.", 3571, FA)
    # Task 3 fills the TTM fields; AAPL's latest filing is the FY2026 10-K, so TTM = FY.
    assert s.ttm["net_income"] == 132175000000 and s.ttm_period_end == "2026-09-26" and s.ttm_is_fy is True
    assert s.latest_period_end == "2026-09-26"
    assert s.latest_annual_end == "2026-09-26" and s.currency == "USD"
    assert s.tags["net_income"] == "NetIncomeLoss" and s.units["net_income"] == "USD"
    assert list(s.annual) == sorted(s.annual) and isinstance(s.notes, list) and isinstance(s.tag_breaks, list)
    assert all(f"{c}: tag break" in s.notes for c in s.tag_breaks)
    assert "split_ratio" not in s.units


def test_tag_tables():
    assert set(CONCEPTS) >= {"revenue", "net_income", "eps", "equity", "debt", "cash", "cfo", "capex", "sbc",
                             "buybacks", "dividends", "interest", "op_income", "assets", "shares", "op_leases"}
    for sym in SYMBOLS:   # every tag the fixture trimmer kept is in the table
        for ns, tags in load(sym)["facts"].items():
            assert set(tags) <= set(TAGS_BY_NAMESPACE[ns]), (sym, ns)
    assert ALL_TAGS == frozenset(t for tags in TAGS_BY_NAMESPACE.values() for t in tags)


@pytest.mark.parametrize("sym", SYMBOLS)
def test_every_fixture_resolves(sym):
    s = snap(sym)
    # CRWV listed in 2025 and has only ~3-4 annual years on file, so it gets a lower floor.
    floor = 3 if sym == "CRWV" else 5
    for concept in ("net_income", "equity"):
        assert sum(1 for row in s.annual.values() if concept in row) >= floor, concept
    assert s.currency in ("USD", "EUR", "TWD") and s.latest_annual_end


def test_money_concepts_prefer_reporting_currency_on_tie():
    # TSM borrowings: TWD and USD cover the same 5 years; the company reports net income in TWD,
    # so debt must be TWD too (a USD debt next to TWD equity would break ROIC).
    s = snap("TSM")
    assert s.units["debt"] == "TWD" and not any("differs" in n for n in s.notes)
    assert pick_unit({"TWD": {"a": 1}, "USD": {"a": 1}}, prefer="TWD") == "TWD"
    assert pick_unit({"TWD": {"a": 1}, "USD": {"a": 1, "b": 1}}, prefer="TWD") == "USD"


def test_no_tag_break_when_another_tag_fills_the_year():
    # NetIncomeLossAvailable... disagrees and could fill 2014, but ProfitLoss agrees and fills it,
    # so nothing is left blank: not a tag break (the disagreement is still noted in the snapshot).
    def node(vals):
        return {"units": {"USD": [{"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "accn": f"a{y}",
                                   "form": "10-K", "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
    g = {"NetIncomeLoss": node({y: 100.0 for y in range(2016, 2024)}),
         "ProfitLoss": node({2014: 50.0, 2016: 100.0}),
         "NetIncomeLossAvailableToCommonStockholdersBasic": node({2014: 70.0, 2016: 120.0})}
    series, _, _, broke = resolve_concept(g, {}, "net_income")
    assert series["2014-12-31"] == 50.0 and not broke


def test_googl_debt_fills_older_years_from_later_recipes():
    # Ruling 2026-11-06: for debt only, years the primary recipe lacks are filled from later recipes
    # in table order without the 2% rule (they differ structurally); the latest year stays on (a).
    g = gaap(load("GOOGL"))
    s = snap("GOOGL")
    years = [e for e, r in s.annual.items() if "debt" in r]
    a = {t: annual_facts(g[t]["units"])["USD"] for t in ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "CommercialPaper")}
    want = sum(a[t]["2025-12-31"].val for t in a)
    assert s.tags["debt"].startswith("LongTermDebtNoncurrent+LongTermDebtCurrent") and len(years) >= 10
    assert s.annual["2025-12-31"]["debt"] == pytest.approx(want)
    assert "debt" not in s.tag_breaks
    assert any(n.startswith("debt: ") and " from recipe (d) LongTermDebt+" in n for n in s.notes)   # (d) since E2


def test_basic_only_is_last_resort_with_note():
    def node(vals):
        return {"units": {"USD/shares": [{"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "accn": f"a{y}",
                                          "form": "10-K", "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
    facts = {"facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": node({2020: 5.0})["units"]["USD/shares"]}},
                                   "EarningsPerShareBasic": node({2020: 1.0, 2021: 2.0})}}}
    s = build_snapshot("X", 1, facts, sic=None, name="X", fetched_at=FA)
    assert s.tags["eps"] == "EarningsPerShareBasic" and "eps: basic-only" in s.notes
    assert "eps: basic-only" not in snap("AAPL").notes


# ---------- review fixes (2026-11-06) ----------

def _latest_inst(doc, tag, end):
    rows = annual_facts(gaap(doc)[tag]["units"]).get("USD", {}) if tag in gaap(doc) else {}
    return rows[end].val if end in rows else 0.0


def test_debt_recipe_order_hd_latest_year():
    # HD: recipe (b) LongTermDebt + ShortTermBorrowings + CommercialPaper covers the latest year,
    # so it is primary even though (d) LongTermDebtAndCapitalLeaseObligations has more years.
    hd = load("HD")
    s = snap("HD")
    end = s.latest_annual_end
    want = sum(_latest_inst(hd, t, end) for t in ("LongTermDebt", "ShortTermBorrowings", "CommercialPaper"))
    assert s.tags["debt"] == "LongTermDebt+ShortTermBorrowings+CommercialPaper"
    assert s.annual[end]["debt"] == pytest.approx(want) and want > 50e9


def test_debt_recipe_order_asml_latest_year():
    asml = load("ASML")
    s = build_snapshot("ASML", 937966, asml, sic=None, name="ASML", fetched_at=FA)
    end = s.latest_annual_end
    rows = {t: annual_facts(gaap(asml)[t]["units"])["EUR"] for t in ("LongTermDebtNoncurrent", "LongTermDebtCurrent")}
    want = rows["LongTermDebtNoncurrent"][end].val + rows["LongTermDebtCurrent"][end].val
    assert s.tags["debt"].startswith("LongTermDebtNoncurrent+LongTermDebtCurrent")
    assert s.annual[end]["debt"] == pytest.approx(want)


def _node(vals, instant=False):
    rows = []
    for y, v in vals.items():
        r = {"end": f"{y}-12-31", "val": v, "accn": f"a{y}", "form": "10-K", "filed": f"{y + 1}-02-01"}
        if not instant:
            r["start"] = f"{y}-01-01"
        rows.append(r)
    return {"units": {"USD": rows}}


def test_disagreement_is_noted_even_when_nothing_is_blank():
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": _node({y: 100.0 for y in range(2016, 2024)}),
        "ProfitLoss": _node({2016: 110.0, 2017: 120.0, 2018: 100.0}),       # covers no missing year
    }}}
    s = build_snapshot("X", 1, facts, sic=None, name="X", fetched_at=FA)
    assert "net_income" not in s.tag_breaks
    assert "net_income: ProfitLoss disagrees >2% in 2 overlap years" in s.notes


def test_basic_tags_never_become_primary():
    gaap_ = {"NetIncomeLoss": _node({y: 100.0 for y in range(2020, 2024)}),
             "NetIncomeLossAvailableToCommonStockholdersBasic": _node({y: 100.0 for y in range(2014, 2024)}),
             "EarningsPerShareDiluted": _node({y: 1.0 for y in range(2020, 2024)}),
             "EarningsPerShareBasic": _node({y: 1.0 for y in range(2014, 2024)}),
             "WeightedAverageNumberOfDilutedSharesOutstanding": _node({y: 5.0 for y in range(2020, 2024)}),
             "WeightedAverageNumberOfSharesOutstandingBasic": _node({y: 5.0 for y in range(2014, 2024)})}
    for concept, tag in (("net_income", "NetIncomeLoss"), ("eps", "EarningsPerShareDiluted"),
                         ("shares", "WeightedAverageNumberOfDilutedSharesOutstanding")):
        series, _, primary, _ = resolve_concept(gaap_, {}, concept)
        assert primary == tag and len(series) == 10          # still fills the older years (they agree)


def test_non_numeric_val_is_skipped():
    rows = {"USD": [{"start": "2020-01-01", "end": "2020-12-31", "val": "n/a", "accn": "a", "form": "10-K", "filed": "2021-02-01"},
                    {"start": "2021-01-01", "end": "2021-12-31", "val": 5, "accn": "b", "form": "10-K", "filed": "2022-02-01"}]}
    assert [f.val for f in to_facts(rows)] == [5.0]


def test_duration_row_without_start_is_not_an_instant():
    node = _node({2020: 100.0, 2021: 110.0})
    node["units"]["USD"].append({"end": "2022-12-31", "val": 999.0, "accn": "x", "form": "10-K", "filed": "2023-02-01"})
    series, *_ = resolve_concept({"NetIncomeLoss": node}, {}, "net_income")
    assert "2022-12-31" not in series and len(series) == 2
    assert annual_facts(node["units"], instant=False)["USD"].keys() == {"2020-12-31", "2021-12-31"}


def _eps(end, val, accn, filed, unit="USD/shares", tag="EarningsPerShareDiluted"):
    return Fact(end, end[:4] + "-01-01", val, unit, tag, "10-K", accn, filed)


def test_split_needs_share_counts_to_agree_when_present():
    eps = [_eps("2021-12-31", 2.0, "a1", "2022-02-01"), _eps("2021-12-31", 1.0, "a2", "2023-02-01")]   # 1/2 ...
    contra = [_eps("2021-12-31", 100.0, "a1", "2022-02-01", "shares", "W"),
              _eps("2021-12-31", 101.0, "a2", "2023-02-01", "shares", "W")]                           # ... shares flat
    agree = [_eps("2021-12-31", 100.0, "a1", "2022-02-01", "shares", "W"),
             _eps("2021-12-31", 200.0, "a2", "2023-02-01", "shares", "W")]                            # shares x2
    assert infer_split_factors(eps, contra)["2021-12-31"] == 1.0
    assert infer_split_factors(eps, agree)["2021-12-31"] == pytest.approx(0.5)
    assert infer_split_factors(eps)["2021-12-31"] == pytest.approx(0.5)                               # no shares: EPS only
    facts = {"facts": {"us-gaap": {
        "NetIncomeLoss": _node({2021: 10.0}),
        "EarningsPerShareDiluted": {"units": {"USD/shares": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 2.0, "accn": "a1", "form": "10-K", "filed": "2022-02-01"},
            {"start": "2021-01-01", "end": "2021-12-31", "val": 1.0, "accn": "a2", "form": "10-K", "filed": "2023-02-01"}]}},
        "WeightedAverageNumberOfDilutedSharesOutstanding": {"units": {"shares": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 100.0, "accn": "a1", "form": "10-K", "filed": "2022-02-01"},
            {"start": "2021-01-01", "end": "2021-12-31", "val": 101.0, "accn": "a2", "form": "10-K", "filed": "2023-02-01"}]}}}}}
    s = build_snapshot("X", 1, facts, sic=None, name="X", fetched_at=FA)
    assert "eps: unconfirmed split at 2021-12-31" in s.notes and s.split_factor == 1.0


@pytest.mark.parametrize("sym", ["NVDA", "AAPL", "GOOGL"])
def test_real_splits_are_confirmed_by_share_counts(sym):
    doc = load(sym)
    shares = to_facts(gaap(doc)["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"], "W")
    assert infer_split_factors(eps_facts(doc), shares) == infer_split_factors(eps_facts(doc))
    assert not any("unconfirmed split" in n for n in snap(sym).notes)


# ---------- Task 3: TTM, stamps, SIC and the paced client ----------

def _rows(doc, tag, unit="USD"):
    return gaap(doc)[tag]["units"][unit]


def test_ttm_falls_back_to_fy_when_the_10k_is_the_latest_filing(aapl):
    # The brief expected an AAPL Q1 FY2027 10-Q; the fixture has none (it would be filed late
    # January 2027), so the FY2026 10-K is the latest filing and TTM = FY.
    assert not [r for r in _rows(aapl, "NetIncomeLoss") if r["form"] == "10-Q" and r["end"] > "2026-09-26"]
    value, end, is_fy = ttm_flows(aapl["facts"]["us-gaap"]["NetIncomeLoss"]["units"], "USD", "2026-09-26")
    assert (value, end, is_fy) == (132175000000, "2026-09-26", True) and isinstance(value, float)


def test_ttm_from_fy_plus_ytd_minus_prior_ytd(googl):
    rows = _rows(googl, "NetIncomeLoss")
    fy = next(r["val"] for r in rows if r["form"] == "10-K" and r["start"] == "2025-01-01" and r["end"] == "2025-12-31")
    ytd = next(r for r in rows if r["form"] == "10-Q" and r["start"] == "2026-01-01" and r["end"] == "2026-09-30")
    prior = next(r["val"] for r in rows if r["accn"] == ytd["accn"] and r["start"] == "2025-01-01" and r["end"] == "2025-09-30")
    value, end, is_fy = ttm_flows(googl["facts"]["us-gaap"]["NetIncomeLoss"]["units"], "USD", "2025-12-31")
    assert end == "2026-09-30" and not is_fy and isinstance(value, float)
    assert value == fy + ytd["val"] - prior


def test_ttm_falls_back_to_fy_for_20f():
    asml = load("ASML")
    value, end, is_fy = ttm_flows(asml["facts"]["us-gaap"]["NetIncomeLoss"]["units"], "EUR", "2025-12-31")
    assert is_fy and end == "2025-12-31" and value == 9609400000


def _flow(start, end, val, accn, form="10-Q", filed="2026-08-01"):
    return {"start": start, "end": end, "val": val, "accn": accn, "form": form, "filed": filed}


FY25 = _flow("2025-01-01", "2025-12-31", 100.0, "k1", "10-K", "2026-02-01")


def test_prior_ytd_must_come_from_same_accession():
    units = {"USD": [FY25, _flow("2026-01-01", "2026-06-30", 60.0, "q2"),
                     _flow("2025-01-01", "2025-06-30", 50.0, "q-old", filed="2025-08-01")]}   # other accession
    assert ttm_flows(units, "USD", "2025-12-31") == (100.0, "2025-12-31", True)
    units["USD"].append(_flow("2025-01-01", "2025-06-30", 45.0, "q2"))                         # same accession
    assert ttm_flows(units, "USD", "2025-12-31") == (115.0, "2026-06-30", False)


def test_ttm_windows():
    # start must be the day after the annual end (+-7 days) and the YTD 80-290 days long.
    def ttm(start, end, pstart, pend):
        units = {"USD": [FY25, _flow(start, end, 60.0, "q"), _flow(pstart, pend, 45.0, "q")]}
        return ttm_flows(units, "USD", "2025-12-31")
    assert ttm("2026-01-06", "2026-06-30", "2025-01-06", "2025-06-30")[2] is False         # 5 days late: ok
    assert ttm("2026-01-12", "2026-06-30", "2025-01-12", "2025-06-30")[2] is True          # 11 days late
    assert ttm("2026-01-01", "2026-03-01", "2025-01-01", "2025-03-01")[2] is True          # 59 days
    assert ttm("2026-01-01", "2026-10-31", "2025-01-01", "2025-10-31")[2] is True          # 303 days
    assert ttm("2026-01-01", "2026-06-30", "2025-01-01", "2025-06-15")[2] is True          # prior end 15 days off
    assert ttm("2026-01-01", "2026-06-30", "2024-12-27", "2025-06-27")[2] is False         # prior within 7 days
    # the latest YTD wins; a missing annual fact gives nothing
    units = {"USD": [FY25, _flow("2026-01-01", "2026-03-31", 20.0, "q1", filed="2026-05-01"),
                     _flow("2025-01-01", "2025-03-31", 25.0, "q1", filed="2026-05-01"),
                     _flow("2026-01-01", "2026-06-30", 60.0, "q2"), _flow("2025-01-01", "2025-06-30", 45.0, "q2")]}
    assert ttm_flows(units, "USD", "2025-12-31") == (115.0, "2026-06-30", False)
    assert ttm_flows(units, "USD", "2024-12-31") == (None, None, True)
    assert ttm_flows(units, "EUR", "2025-12-31") == (None, None, True)


def test_latest_quarterly_shares(googl):
    units = gaap(googl)["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]
    quarters = [r for r in units["shares"] if r["form"] == "10-Q"
                and 80 <= (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days <= 100]
    q = max(quarters, key=lambda r: (r["end"], r["filed"]))
    assert q["end"] == "2026-09-30" and latest_quarterly_shares(units) == q["val"]
    annual_only = {"shares": [_flow("2025-01-01", "2025-12-31", 7.0, "k", "10-K"),
                              _flow("2025-01-01", "2025-06-30", 9.0, "q")]}               # 180-day YTD is not a quarter
    assert latest_quarterly_shares(annual_only) == 7.0
    ten_k_quarter = {"shares": [_flow("2025-01-01", "2025-12-31", 7.0, "k", "10-K"),
                                _flow("2025-10-01", "2025-12-31", 8.0, "k", "10-K")]}   # Q4 inside a 10-K: ignored
    assert latest_quarterly_shares(ten_k_quarter) == 7.0
    ten_q = {"shares": ten_k_quarter["shares"] + [_flow("2025-07-01", "2025-09-30", 6.5, "q")]}
    assert latest_quarterly_shares(ten_q) == 6.5                                     # any 10-Q quarter beats the annual
    assert latest_quarterly_shares({}) is None


def test_snapshot_ttm_googl(googl):
    s = build_snapshot("GOOGL", 1652044, googl, sic=7370, name="Alphabet", fetched_at=FA)
    assert s.ttm_period_end == "2026-09-30" and s.ttm_is_fy is False
    assert s.latest_annual_end == "2025-12-31" and s.latest_period_end == "2026-09-30"
    ni, _, _ = ttm_flows(gaap(googl)["NetIncomeLoss"]["units"], "USD", "2025-12-31")
    assert s.ttm["net_income"] == ni
    capex = _rows(googl, "PaymentsToAcquirePropertyPlantAndEquipment")
    ytd = next(r for r in capex if r["start"] == "2026-01-01" and r["end"] == "2026-09-30")
    prior = next(r["val"] for r in capex if r["accn"] == ytd["accn"] and r["start"] == "2025-01-01" and r["end"] == "2025-09-30")
    assert s.ttm["capex"] == pytest.approx(s.annual["2025-12-31"]["capex"] + ytd["val"] - prior)
    eq = max((r for r in _rows(googl, "StockholdersEquity")), key=lambda r: (r["end"], r["filed"]))
    assert eq["end"] == "2026-09-30" and s.ttm["equity"] == eq["val"]                    # latest 10-Q instant

    def at(tag, end):
        hits = [r for r in _rows(googl, tag) if r["end"] == end] if tag in gaap(googl) else []
        return max(hits, key=lambda r: r["filed"])["val"] if hits else 0.0
    want = sum(at(t, "2026-09-30") for t in ("LongTermDebtNoncurrent", "LongTermDebtCurrent", "ShortTermBorrowings", "CommercialPaper"))
    assert s.ttm["debt"] == pytest.approx(want) and want > 0
    assert s.ttm["shares"] == latest_quarterly_shares(gaap(googl)["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"])
    assert set(s.ttm) >= {"revenue", "net_income", "cfo", "capex", "op_income", "debt", "cash", "equity", "assets", "shares"}


@pytest.mark.parametrize("sym", ["MCD", "HD", "HOOD"])
def test_snapshot_ttm_10q_filers(sym):
    s = snap(sym)
    assert s.ttm_is_fy is False and s.ttm_period_end > s.latest_annual_end
    assert s.latest_period_end >= s.ttm_period_end
    assert s.ttm["net_income"] != s.annual[s.latest_annual_end]["net_income"]


def test_snapshot_ttm_aapl_is_fy_and_balance_is_latest_instant(aapl):
    s = build_snapshot("AAPL", 320193, aapl, sic=3571, name="Apple Inc.", fetched_at=FA)
    row = s.annual["2026-09-26"]
    for c in ("revenue", "net_income", "cfo", "capex", "sbc", "op_income"):
        if c in row:
            assert s.ttm[c] == row[c], c
    assert s.ttm_is_fy and s.ttm["equity"] == row["equity"] and s.ttm["debt"] == pytest.approx(row["debt"])


@pytest.mark.parametrize("sym", ["ASML", "TSM"])
def test_snapshot_ttm_20f_is_fy(sym):
    s = snap(sym)
    assert s.ttm_is_fy is True and s.ttm_period_end == s.latest_annual_end == s.latest_period_end
    assert s.ttm["net_income"] == s.annual[s.latest_annual_end]["net_income"]


def test_is_financial_sic():
    assert is_financial_sic(6021) and is_financial_sic(6211) and is_financial_sic(6331) and not is_financial_sic(7389)
    assert is_financial_sic(6000) and is_financial_sic(6499) and is_financial_sic("6021")
    assert not is_financial_sic(5999) and not is_financial_sic(6500)
    assert not is_financial_sic(None) and not is_financial_sic("x") and not is_financial_sic(True)


# ---------- the client ----------

class FakeHTTP(Exception):
    def __init__(self, code):
        super().__init__(f"HTTP {code}")
        self.code = code


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def sleep(self, s):
        self.slept.append(s)
        self.t += s

    def __call__(self):
        return self.t


def test_client_paces_and_backs_off():
    from urllib.error import HTTPError
    calls, headers, clock = [], [], Clock()

    def get(url, hdrs):
        calls.append(url)
        headers.append(dict(hdrs))
        if len(calls) == 2:
            raise HTTPError(url, 403, "Forbidden", {}, None)
        return b'{"ok":1}'

    client = EdgarClient(get, sleep=clock.sleep, clock=clock)
    assert client.company_facts(320193) == {"ok": 1} and client.company_facts(937966) == {"ok": 1}
    assert calls == ["https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json",
                     "https://data.sec.gov/api/xbrl/companyfacts/CIK0000937966.json",
                     "https://data.sec.gov/api/xbrl/companyfacts/CIK0000937966.json"]
    assert clock.t >= 1.0 and 1 in clock.slept and client.consecutive_403 == 0
    assert all(h["User-Agent"] == "AGX Trading App (admin@agxtrade.com)" for h in headers)
    assert all(h["Accept-Encoding"] == "gzip" and h["Host"] == "data.sec.gov" for h in headers)


def test_client_paces_to_five_per_second():
    clock = Clock()
    client = EdgarClient(lambda u, h: b"{}", sleep=clock.sleep, clock=clock)
    for _ in range(3):
        client.company_facts(1)
    assert clock.slept == [pytest.approx(0.2), pytest.approx(0.2)]
    clock.t += 5.0                                  # idle long enough: no wait
    client.company_facts(1)
    assert len(clock.slept) == 2


def test_client_aborts_after_three_403s():
    clock = Clock()

    def get(url, hdrs):
        raise FakeHTTP(403)

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    with pytest.raises(EdgarBlocked):
        client.company_facts(1)
    assert client.consecutive_403 == 3 and clock.slept == [1, 2]


def test_client_403_streak_counts_across_calls_and_resets_on_success():
    clock, script = Clock(), []

    def get(url, hdrs):
        code = script.pop(0)
        if code != 200:
            raise FakeHTTP(code)
        return b"{}"

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    script[:] = [403, 403, 200]
    client.company_facts(1)
    assert client.consecutive_403 == 0
    script[:] = [403, 403, 200]
    client.company_facts(1)                        # the streak restarted, so 2 more 403s are fine
    script[:] = [403, 429, 403, 200]               # a 429 breaks the streak too
    client.company_facts(1)
    assert client.consecutive_403 == 0 and script == []


def test_client_403_streak_spans_calls():
    clock, script = Clock(), [403, 403, 403, 403, 403]

    def get(url, hdrs):
        raise FakeHTTP(script.pop(0))

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    client.consecutive_403 = 2                      # two 403s left over from an earlier call
    with pytest.raises(EdgarBlocked):
        client.company_facts(1)
    assert client.consecutive_403 == 3 and clock.slept == []


def test_client_429_retries_four_times_then_raises():
    clock = Clock()
    n = [0]

    def get(url, hdrs):
        n[0] += 1
        raise FakeHTTP(429)

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    with pytest.raises(FakeHTTP):
        client.submission(1)
    assert n[0] == 5 and clock.slept == [1, 2, 4, 8]


def test_client_retries_5xx_then_succeeds():
    clock, script = Clock(), [503, 200]

    def get(url, hdrs):
        code = script.pop(0)
        if code != 200:
            raise FakeHTTP(code)
        return b'{"ok":1}'

    client = EdgarClient(get, sleep=clock.sleep, clock=clock)
    assert client.company_facts(1) == {"ok": 1} and script == [] and clock.slept == [1]
    assert client.consecutive_403 == 0


def test_client_retries_network_errors_then_propagates():
    import socket
    from urllib.error import URLError
    clock = Clock()
    errs = [URLError("down"), socket.timeout("slow"), ConnectionResetError("reset"), TimeoutError("t"), URLError("down")]
    n = [0]

    def get(url, hdrs):
        n[0] += 1
        raise errs.pop(0)

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    with pytest.raises(URLError):
        client.company_facts(1)
    assert n[0] == 5 and clock.slept == [1, 2, 4, 8] and client.consecutive_403 == 0


def test_client_other_errors_propagate():
    clock = Clock()
    n = [0]

    def get(url, hdrs):
        n[0] += 1
        raise FakeHTTP(404)

    client = EdgarClient(get, sleep=clock.sleep, clock=clock, pace_per_sec=1e9)
    with pytest.raises(FakeHTTP):
        client.company_facts(1)
    assert n[0] == 1 and clock.slept == []


def test_client_cik_map_canonical_and_cached():
    extra = json.loads((FIX / "company_tickers.json").read_text(encoding="utf-8"))
    extra["90"] = {"cik_str": 1067983, "ticker": "brk.a", "title": "Berkshire"}
    extra["91"] = {"cik_str": 14693, "ticker": "BF/B", "title": "Brown-Forman"}
    calls = []

    def get(url, hdrs):
        calls.append((url, hdrs["Host"]))
        return json.dumps(extra).encode()

    client = EdgarClient(get, sleep=lambda s: None)
    m = client.cik_map()
    assert m["AAPL"] == 320193 and m["GOOG"] == m["GOOGL"] == 1652044 and "BRK-B" in m and m["BRK-A"] == 1067983
    assert m["BF-B"] == 14693 and all(isinstance(v, int) for v in m.values())
    assert client.cik_map() is m and calls == [("https://www.sec.gov/files/company_tickers.json", "www.sec.gov")]


def test_client_submission_is_trimmed():
    raw = json.loads((FIX / "submissions_JPM.json").read_text(encoding="utf-8"))
    raw.update({"filings": {"recent": {}}, "addresses": {}})
    urls = []

    def get(url, hdrs):
        urls.append(url)
        return json.dumps(raw).encode()

    sub = EdgarClient(get, sleep=lambda s: None).submission(19617)
    assert urls == ["https://data.sec.gov/submissions/CIK0000019617.json"]
    assert set(sub) == {"name", "sic", "sicDescription", "fiscalYearEnd", "tickers"}
    assert sub["sic"] == 6021 and sub["name"] == "JPMORGAN CHASE & CO" and is_financial_sic(sub["sic"])
    raw["sic"] = ""
    assert EdgarClient(lambda u, h: json.dumps(raw).encode(), sleep=lambda s: None).submission(1)["sic"] is None


def test_client_user_agent_from_env(monkeypatch):
    seen = []
    monkeypatch.setenv("SEC_USER_AGENT", "Someone (me@example.com)")
    EdgarClient(lambda u, h: seen.append(h["User-Agent"]) or b"{}", sleep=lambda s: None).company_facts(1)
    EdgarClient(lambda u, h: seen.append(h["User-Agent"]) or b"{}", user_agent="X (x@y.z)", sleep=lambda s: None).company_facts(1)
    assert seen == ["Someone (me@example.com)", "X (x@y.z)"]


def test_default_getter_sends_headers_and_gunzips(monkeypatch):
    import gzip as _gzip

    seen = {}

    class Resp:
        def __init__(self, body, enc):
            self.body, self.headers = body, {"Content-Encoding": enc} if enc else {}

        def read(self):
            return self.body

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    replies = [Resp(_gzip.compress(b'{"a":1}'), "gzip"), Resp(b'{"b":2}', None)]

    def urlopen(req, timeout=None):
        seen["ua"], seen["url"], seen["timeout"] = req.get_header("User-agent"), req.full_url, timeout
        return replies.pop(0)

    monkeypatch.setattr(edgar.urllib.request, "urlopen", urlopen)
    hdrs = {"User-Agent": "UA (a@b.c)", "Accept-Encoding": "gzip", "Host": "data.sec.gov"}
    assert edgar._urllib_get("https://data.sec.gov/x.json", hdrs) == b'{"a":1}'
    assert seen["ua"] == "UA (a@b.c)" and seen["url"] == "https://data.sec.gov/x.json" and seen["timeout"]
    assert edgar._urllib_get("https://data.sec.gov/x.json", hdrs) == b'{"b":2}'
    assert EdgarClient(sleep=lambda s: None)._get is edgar._urllib_get


# ---------- Task 3 review fixes ----------

def test_per_concept_ttm_stamps_jpm():
    s = snap("JPM")
    assert s.ttm_ends["revenue"] == (s.latest_annual_end, True)
    assert s.ttm_ends["net_income"] == ("2026-09-30", False) == (s.ttm_period_end, s.ttm_is_fy)
    assert set(s.ttm_ends) == {c for c in ("revenue", "net_income", "cfo", "capex", "sbc", "interest", "op_income", "eps")
                               if c in s.ttm}
    assert "revenue: TTM = FY" in s.notes
    assert s.ttm_ends["eps"] == ("2026-09-30", False)      # EPS TTM from the EPS tag (FY + YTD - prior YTD)


def test_balance_stamps_hd():
    s = snap("HD")
    assert s.balance_ends["debt"] == "2026-02-01" and s.ttm_ends["net_income"][0] == "2026-08-02"
    assert "debt: balance at 2026-02-01, older than TTM 2026-08-02" in s.notes
    assert set(s.balance_ends) == {c for c in ("debt", "cash", "equity", "assets", "shares") if c in s.ttm}
    assert s.balance_ends["equity"] == "2026-08-02" and s.balance_ends["shares"] == "2026-08-02"
    assert not any(n.startswith("equity: balance at") for n in s.notes)


def test_fy_snapshot_stamps_aapl():
    s = snap("AAPL")
    assert all(v == ("2026-09-26", True) for v in s.ttm_ends.values()) and s.ttm_ends
    assert all(e == "2026-09-26" for c, e in s.balance_ends.items() if c != "shares")
    assert not any("older than TTM" in n for n in s.notes if not n.startswith("shares"))


def test_fetch_does_not_gunzip_again():
    import gzip as _gzip
    raw = _gzip.compress(b"{}")
    assert EdgarClient(lambda u, h: raw, sleep=lambda s: None).fetch("https://data.sec.gov/x") == raw


def test_annual_form_of_latest_net_income():
    def snap(sym):
        return build_snapshot(sym, CIKS[sym], load(sym), sic=None, name=sym, fetched_at=FA)
    assert snap("ASML").annual_form == "20-F"
    assert snap("AAPL").annual_form == "10-K"


# ---------- final review fix 10: optional debt part missing at a later 10-Q instant ----------

def test_optional_part_missing_at_10q_falls_back_to_annual_instant():
    def inst(rows):
        return {"units": {"USD": [{"end": e, "val": v, "accn": a, "form": f, "filed": fl} for e, v, a, f, fl in rows]}}
    g = {"LongTermDebtNoncurrent": inst([("2025-12-31", 100.0, "k", "10-K", "2026-02-01"),
                                         ("2026-09-30", 110.0, "q", "10-Q", "2026-11-01")]),
         "LongTermDebtCurrent": inst([("2025-12-31", 10.0, "k", "10-K", "2026-02-01"),
                                      ("2026-09-30", 12.0, "q", "10-Q", "2026-11-01")]),
         "CommercialPaper": inst([("2025-12-31", 50.0, "k", "10-K", "2026-02-01")])}   # untagged in the 10-Q
    label = "LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings+CommercialPaper"
    notes = []
    assert edgar._latest_instant(g, {}, label, "USD", notes) == ("2025-12-31", 160.0)   # not 122 at the 10-Q
    assert notes and "CommercialPaper" in notes[0] and "2025-12-31" in notes[0]
    # an optional part never tagged at the annual instant still counts as 0 at the 10-Q
    notes2 = []
    assert edgar._latest_instant(g, {}, "LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings",
                                 "USD", notes2) == ("2026-09-30", 122.0)
    assert notes2 == []


# ---------- first nightly audit (2026-11-06): coverage gaps on 385 tickers ----------

def _flows(tag_vals, unit="USD"):
    """{tag: {year: val}} -> us-gaap nodes of 10-K annual flows (Jan-Dec)."""
    return {t: {"units": {unit: [{"start": f"{y}-01-01", "end": f"{y}-12-31", "val": v, "accn": f"a{y}",
                                  "form": "10-K", "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
            for t, vals in tag_vals.items()}


def _insts(tag_vals):
    return {t: {"units": {"USD": [{"end": f"{y}-12-31", "val": v, "accn": f"a{y}", "form": "10-K",
                                   "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
            for t, vals in tag_vals.items()}


def test_new_tags_are_in_the_table():
    for tag in ("RevenueFromContractWithCustomerIncludingAssessedTax", "PaymentsToAcquireOtherPropertyPlantAndEquipment",
                "NotesPayable", "ConvertibleLongTermNotesPayable", "ConvertibleNotesPayableCurrent",
                "DebtAndCapitalLeaseObligations", "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
                "LoansPayable", "OtherNotesPayableCurrent"):
        assert tag in ALL_TAGS and tag in TAGS_BY_NAMESPACE["us-gaap"], tag
    debt = [edgar._cand_label(c) for c in CONCEPTS["debt"]]
    spot = debt.index("ifrs-full:LongtermBorrowings+ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings")
    assert debt[spot - 7:spot] == ["NotesPayable", "ConvertibleLongTermNotesPayable+ConvertibleNotesPayableCurrent",
                         "LoansPayable", "OtherNotesPayableCurrent",
                         "LongTermDebtNoncurrent", "ConvertibleDebtNoncurrent+ConvertibleDebtCurrent",
                         "ConvertibleNotesPayable"]
    # audit fix E2: the filer totals moved ahead of LongTermDebt (b), see test_debt_table_order_*
    for tag in ("ConvertibleDebtNoncurrent", "ConvertibleDebtCurrent", "ConvertibleNotesPayable"):
        assert tag in ALL_TAGS and tag in TAGS_BY_NAMESPACE["us-gaap"], tag


def test_revenue_including_assessed_tax_crwd():
    # CRWD tags revenue only as ...IncludingAssessedTax
    series, _, tag, _ = resolve_concept(_flows({"RevenueFromContractWithCustomerIncludingAssessedTax":
                                                {2024: 3.0, 2025: 4.0}}), {}, "revenue")
    assert series == {"2024-12-31": 3.0, "2025-12-31": 4.0}
    assert tag == "RevenueFromContractWithCustomerIncludingAssessedTax"
    # with the Excluding tag in the same year the larger-of rule still applies
    g = _flows({"RevenueFromContractWithCustomerExcludingAssessedTax": {2025: 100.0},
                "RevenueFromContractWithCustomerIncludingAssessedTax": {2025: 104.0}})
    assert resolve_concept(g, {}, "revenue")[0] == {"2025-12-31": 104.0}


def test_capex_other_ppe_tag_lly():
    series, _, tag, _ = resolve_concept(_flows({"PaymentsToAcquireOtherPropertyPlantAndEquipment":
                                                {y: -5.0 for y in range(2020, 2026)}}), {}, "capex")
    assert tag == "PaymentsToAcquireOtherPropertyPlantAndEquipment" and len(series) == 6
    assert set(series.values()) == {5.0}


@pytest.mark.parametrize("tag", ["NotesPayable", "DebtAndCapitalLeaseObligations",
                                 "LoansPayable", "OtherNotesPayableCurrent", "LongTermDebtNoncurrent",
                                 "ConvertibleNotesPayable"])
def test_new_single_tag_debt_recipes(tag):
    series, _, primary, _ = resolve_concept(_insts({tag: {2024: 7.0, 2025: 9.0}}), {}, "debt")
    assert primary == tag and series == {"2024-12-31": 7.0, "2025-12-31": 9.0}


def test_convertible_notes_recipe_current_part_optional():
    both = _insts({"ConvertibleLongTermNotesPayable": {2024: 100.0, 2025: 90.0},
                   "ConvertibleNotesPayableCurrent": {2025: 10.0}})
    series, _, tag, _ = resolve_concept(both, {}, "debt")
    assert tag == "ConvertibleLongTermNotesPayable+ConvertibleNotesPayableCurrent"
    assert series == {"2024-12-31": 100.0, "2025-12-31": 100.0}
    # the current part alone is not that recipe
    assert resolve_concept(_insts({"ConvertibleNotesPayableCurrent": {2025: 10.0}}), {}, "debt") is None


def test_debt_recipes_stay_mutually_exclusive_first_covering_latest_wins():
    # LongTermDebt AND convertible notes both cover the latest year: recipe (b) wins, nothing is summed
    g = _insts({"LongTermDebt": {2024: 50.0, 2025: 60.0},
                "ConvertibleLongTermNotesPayable": {y: 30.0 for y in range(2018, 2026)}})
    series, _, tag, _ = resolve_concept(g, {}, "debt")
    assert tag == "LongTermDebt+ShortTermBorrowings+CommercialPaper" and series["2025-12-31"] == 60.0
    assert series["2018-12-31"] == 30.0                      # older years filled in table order
    # convertible notes alone in the latest year: they are primary, LongTermDebt fills the old years
    g = _insts({"LongTermDebt": {2019: 50.0, 2020: 60.0},
                "ConvertibleLongTermNotesPayable": {2021: 30.0, 2025: 35.0}})
    series, _, tag, _ = resolve_concept(g, {}, "debt")
    assert tag.startswith("ConvertibleLongTermNotesPayable") and series["2025-12-31"] == 35.0
    assert series["2019-12-31"] == 50.0


def test_primary_is_the_tag_covering_the_latest_year_team():
    # TEAM: long 20-F era under ProfitLoss (2014-2022), 10-Ks since 2023 under NetIncomeLoss (with
    # comparatives back to 2021). Coverage alone picked ProfitLoss and dated the company 2022.
    old = {y: 10.0 + y - 2014 for y in range(2014, 2023)}
    new = {y: 10.0 + y - 2014 for y in range(2021, 2026)}
    g = _flows({"ProfitLoss": old, "NetIncomeLoss": new})
    s = resolve_series(g, {}, "net_income")
    assert s.tag == "NetIncomeLoss" and max(s.values) == "2025-12-31" and len(s.values) == 12
    assert s.source["2014-12-31"] == "ProfitLoss" and not s.tag_break
    snap_ = build_snapshot("TEAM", 1, {"facts": {"us-gaap": g}}, sic=None, name="TEAM", fetched_at=FA)
    assert snap_.latest_annual_end == "2025-12-31"
    # the two tags disagree in the overlap (IFRS vs US GAAP): the old tag cannot fill, but the
    # company is still dated by the tag it files today, never by the longer, older one
    g = _flows({"ProfitLoss": old, "NetIncomeLoss": {y: v * 1.1 for y, v in new.items()}})
    s = resolve_series(g, {}, "net_income")
    assert s.tag == "NetIncomeLoss" and sorted(s.values) == [f"{y}-12-31" for y in range(2021, 2026)]
    assert s.tag_break
    snap_ = build_snapshot("TEAM", 1, {"facts": {"us-gaap": g}}, sic=None, name="TEAM", fetched_at=FA)
    assert snap_.latest_annual_end == "2025-12-31" and snap_.annual_form == "10-K"


def test_capex_fills_without_two_percent_rule_amzn():
    # AMZN: PP&E capex 2015-2019, productive-assets capex 2019-2025, 10% apart in 2019. The newer tag
    # covers the latest year so it is primary; the older years fill from the old tag, noted, no break.
    g = _flows({"PaymentsToAcquirePropertyPlantAndEquipment": {y: -100.0 for y in range(2015, 2020)},
                "PaymentsToAcquireProductiveAssets": {**{2019: -110.0}, **{y: -200.0 for y in range(2020, 2026)}}})
    s = resolve_series(g, {}, "capex")
    assert len(s.values) == 11 and min(s.values) == "2015-12-31" and max(s.values) == "2025-12-31"
    assert s.tag == "PaymentsToAcquireProductiveAssets" and not s.tag_break
    assert s.values["2025-12-31"] == 200.0 and s.values["2019-12-31"] == 110.0 and s.values["2015-12-31"] == 100.0
    g.update(_flows({"NetIncomeLoss": {y: 5.0 for y in range(2015, 2026)}}))
    snap_ = build_snapshot("AMZN", 1, {"facts": {"us-gaap": g}}, sic=None, name="AMZN", fetched_at=FA)
    assert "capex" not in snap_.tag_breaks and len([r for r in snap_.annual.values() if "capex" in r]) == 11
    assert "capex: 2015, 2016, 2017, 2018 from PaymentsToAcquirePropertyPlantAndEquipment" in snap_.notes


def test_latest_period_end_spans_every_concept():
    # Net income has nothing after the 10-K; only Assets carries a later 10-Q instant (C, NEE).
    g = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2021, 2026)},
                "Revenues": {y: 50.0 for y in range(2021, 2026)}})
    g.update(_insts({"Assets": {y: 500.0 for y in range(2021, 2026)}}))
    g["Assets"]["units"]["USD"].append({"end": "2026-06-30", "val": 520.0, "accn": "q2", "form": "10-Q",
                                        "filed": "2026-08-01"})
    s = build_snapshot("X", 1, {"facts": {"us-gaap": g}}, sic=None, name="X", fetched_at=FA)
    assert s.latest_annual_end == "2025-12-31" and s.latest_period_end == "2026-06-30"
    # a form outside the period forms (8-K) never moves it
    g["Assets"]["units"]["USD"].append({"end": "2026-09-30", "val": 1.0, "accn": "x", "form": "8-K",
                                        "filed": "2026-10-01"})
    s = build_snapshot("X", 1, {"facts": {"us-gaap": g}}, sic=None, name="X", fetched_at=FA)
    assert s.latest_period_end == "2026-06-30"


def test_long_term_debt_noncurrent_alone_crwd_team():
    # CRWD / REGN / MRNA tag senior notes only as LongTermDebtNoncurrent (no current part)
    series, _, tag, _ = resolve_concept(_insts({"LongTermDebtNoncurrent": {2024: 740.0, 2025: 742.0}}), {}, "debt")
    assert tag == "LongTermDebtNoncurrent" and series["2025-12-31"] == 742.0
    # TEAM: the current part stopped in 2024; the noncurrent part alone carries the latest year
    g = _insts({"LongTermDebtNoncurrent": {2023: 990.0, 2024: 995.0, 2025: 1000.0},
                "LongTermDebtCurrent": {2023: 10.0, 2024: 10.0}})
    series, _, tag, _ = resolve_concept(g, {}, "debt")
    assert tag == "LongTermDebtNoncurrent" and series["2025-12-31"] == 1000.0
    # the primary supplies every year it has (ordered fill only fills gaps), so the 2023-2024
    # current portion is not added: known, small undercount in those years
    assert series["2024-12-31"] == 995.0
    # both parts in the latest year: recipe (a) still wins
    g = _insts({"LongTermDebtNoncurrent": {2025: 1000.0}, "LongTermDebtCurrent": {2025: 10.0}})
    assert resolve_concept(g, {}, "debt")[2].startswith("LongTermDebtNoncurrent+LongTermDebtCurrent")


def test_convertible_debt_recipe_panw():
    g = _insts({"ConvertibleDebtNoncurrent": {2024: 900.0, 2025: 0.0}, "ConvertibleDebtCurrent": {2025: 950.0}})
    series, _, tag, _ = resolve_concept(g, {}, "debt")
    assert tag == "ConvertibleDebtNoncurrent+ConvertibleDebtCurrent"
    assert series == {"2024-12-31": 900.0, "2025-12-31": 950.0}
    assert resolve_concept(_insts({"ConvertibleDebtCurrent": {2025: 950.0}}), {}, "debt") is None


def test_zero_recipe_never_outranks_a_recipe_with_debt_spot():
    # SPOT: ifrs LongtermBorrowings is 0 in the latest year; the exchangeable notes are tagged as
    # CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings. Recipe (f) shows 0 and must not win.
    def inst(vals):
        return {"units": {"EUR": [{"end": f"{y}-12-31", "val": v, "accn": f"a{y}", "form": "20-F",
                                   "filed": f"{y + 1}-02-01"} for y, v in vals.items()]}}
    ifrs_ = {"LongtermBorrowings": inst({2024: 1450.0, 2025: 0.0}),
             "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings": inst({2024: 0.0, 2025: 1458.0})}
    series, unit, tag, _ = resolve_concept({}, ifrs_, "debt")
    assert tag == "ifrs-full:LongtermBorrowings+ifrs-full:CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings"
    assert series["2025-12-31"] == 1458.0 and unit == "EUR"
    # when every covering recipe shows 0, the first in table order stays primary (a real zero)
    ifrs_ = {"LongtermBorrowings": inst({2025: 0.0})}
    assert resolve_concept({}, ifrs_, "debt")[0] == {"2025-12-31": 0.0}


# ---------- review round 3 ----------

def _snap_of(g, ifrs_=None):
    facts = {"us-gaap": g}
    if ifrs_:
        facts["ifrs-full"] = ifrs_
    return build_snapshot("X", 1, {"facts": facts}, sic=None, name="X", fetched_at=FA)


def test_partial_debt_recipe_is_noted():
    base = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2021, 2026)}})
    for tag in ("NotesPayable", "LoansPayable", "OtherNotesPayableCurrent", "LongTermDebtNoncurrent"):
        s = _snap_of({**base, **_insts({tag: {y: 9.0 for y in range(2021, 2026)}})})
        assert f"debt: partial recipe {tag}" in s.notes, tag
    s = _snap_of({**base, **_insts({"LongTermDebtNoncurrent": {y: 9.0 for y in range(2021, 2026)},
                                    "LongTermDebtCurrent": {y: 1.0 for y in range(2021, 2026)}})})
    assert not any("partial recipe" in n for n in s.notes)


def test_ordered_concepts_have_no_two_percent_notes():
    g = _flows({"PaymentsToAcquirePropertyPlantAndEquipment": {y: -100.0 for y in range(2015, 2020)},
                "PaymentsToAcquireProductiveAssets": {**{2019: -110.0}, **{y: -200.0 for y in range(2020, 2026)}},
                "NetIncomeLoss": {y: 5.0 for y in range(2015, 2026)}})
    g.update(_insts({"LongTermDebt": {2024: 50.0, 2025: 60.0}, "NotesPayable": {2024: 70.0, 2023: 40.0}}))
    s = _snap_of(g)
    assert not any(n.startswith(("capex:", "debt:")) and "disagrees" in n for n in s.notes)
    assert "capex: 2015, 2016, 2017, 2018 from PaymentsToAcquirePropertyPlantAndEquipment" in s.notes


def test_capex_fill_refused_beyond_25_percent():
    g = _flows({"PaymentsToAcquirePropertyPlantAndEquipment": {y: -100.0 for y in range(2015, 2020)},
                "PaymentsToAcquireProductiveAssets": {**{2019: -200.0}, **{y: -200.0 for y in range(2020, 2026)}},
                "NetIncomeLoss": {y: 5.0 for y in range(2015, 2026)}})
    s = resolve_series(g, {}, "capex")
    assert sorted(s.values) == [f"{y}-12-31" for y in range(2019, 2026)] and s.far_off == [
        "PaymentsToAcquirePropertyPlantAndEquipment"]
    snap_ = _snap_of(g)
    assert "capex: PaymentsToAcquirePropertyPlantAndEquipment differs >25%, not used" in snap_.notes
    # the AMZN-shaped 10% case still fills (test_capex_fills_without_two_percent_rule_amzn)
    g["PaymentsToAcquireProductiveAssets"]["units"]["USD"][0]["val"] = -110.0
    assert len(resolve_series(g, {}, "capex").values) == 11


def test_fy_ttm_with_later_quarter_is_noted():
    g = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2021, 2026)}})
    g.update(_insts({"Assets": {y: 500.0 for y in range(2021, 2026)}}))
    g["Assets"]["units"]["USD"].append({"end": "2026-06-30", "val": 520.0, "accn": "q2", "form": "10-Q",
                                        "filed": "2026-08-01"})
    s = _snap_of(g)
    assert s.ttm_is_fy and "income TTM = FY; later 10-Q balance at 2026-06-30" in s.notes
    assert not any(n.startswith("income TTM") for n in snap("AAPL").notes)   # AAPL: 10-K is the latest


def test_zero_recipe_stays_primary_when_later_recipe_has_only_older_years():
    g = _insts({"LongTermDebtNoncurrent": {2024: 0.0, 2025: 0.0}, "LongTermDebtCurrent": {2024: 0.0, 2025: 0.0},
                "NotesPayable": {2019: 40.0, 2020: 45.0}})
    series, _, tag, _ = resolve_concept(g, {}, "debt")
    assert tag.startswith("LongTermDebtNoncurrent+LongTermDebtCurrent") and series["2025-12-31"] == 0.0
    assert series["2019-12-31"] == 40.0                     # older years still fill in table order


# ---------- "no data" fix (2026-11-07): R36 ordered fills, derived EPS, R37 recipes, R41, R42 ----------

NEW_SYMBOLS = ["HAL", "LHX", "HSY", "WELL", "MAR", "XOM", "XOM2"]
NEW_CIKS = {"HAL": 45012, "LHX": 202058, "HSY": 47111, "WELL": 766704, "MAR": 1048286, "XOM": 34088, "XOM2": 2115436}
NEW_SIC = {"HAL": 1389, "LHX": 3812, "HSY": 2060, "WELL": 6798, "MAR": 7011, "XOM": 2911, "XOM2": 2911}


def nsnap(sym, doc=None):
    return build_snapshot(sym, NEW_CIKS[sym], doc or load(sym), sic=NEW_SIC[sym], name=sym, fetched_at=FA)


def _per_share(tag_vals, unit="USD/shares"):
    return _flows(tag_vals, unit)


def _eps_years(s):
    return {e[:4]: r["eps"] for e, r in s.annual.items() if "eps" in r}


@pytest.mark.parametrize("sym", NEW_SYMBOLS)
def test_new_fixture_tags_are_in_the_table(sym):
    for ns, tags in load(sym)["facts"].items():
        assert set(tags) <= set(TAGS_BY_NAMESPACE[ns]), (sym, ns)


def test_optional_recipe_parts_marker():
    assert edgar.RECIPE_OPTIONAL["LongTermNotesPayable+DebtCurrent"] == {"DebtCurrent"}
    assert edgar._optional("DebtCurrent", "LongTermNotesPayable+DebtCurrent")
    assert not edgar._optional("DebtCurrent", "DebtCurrent+LongTermDebtNoncurrent")   # required in recipe (c)
    assert edgar._optional("CommercialPaper")                                          # globally optional
    assert not any(t.startswith("?") for tags in TAGS_BY_NAMESPACE.values() for t in tags)
    assert not any("?" in edgar._cand_label(c) for cands in CONCEPTS.values() for c in cands)
    for c in ("ni_common", "shares_outstanding", "split_ratio"):
        assert c in CONCEPTS and c not in edgar.TABLE_CONCEPTS


def test_internal_concepts_never_reach_the_table():
    s = nsnap("HSY")
    assert not ({"ni_common", "shares_outstanding", "split_ratio"} & set(s.units))
    assert not any({"ni_common", "shares_outstanding"} & set(r) for r in s.annual.values())


# --- R36: per-year ordered fill for eps / cfo / shares / revenue at 25% ---

def test_eps_cont_plus_disc_recipe_fills_a_tag_switch_hal():
    # EarningsPerShareDiluted stops in 2019; the filer moved to continuing + discontinued per share.
    g = _per_share({"EarningsPerShareDiluted": {y: 2.0 for y in range(2012, 2020)},
                    "IncomeLossFromContinuingOperationsPerDilutedShare": {y: 1.5 for y in range(2012, 2026)},
                    "IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare": {y: 0.5 for y in range(2012, 2020)}})
    s = resolve_series(g, {}, "eps")
    assert s.tag.startswith("IncomeLossFromContinuingOperationsPerDilutedShare+") and len(s.values) == 14
    assert s.values["2025-12-31"] == 1.5 and s.values["2019-12-31"] == 2.0 and not s.tag_break
    assert s.source["2015-12-31"] == s.tag                         # the recipe itself carries every year
    g.update(_flows({"NetIncomeLoss": {y: 100.0 for y in range(2012, 2026)}}))
    snap_ = _snap_of(g)
    assert not any("disagrees" in n and n.startswith("eps") for n in snap_.notes)
    # the old tag fills years the recipe lacks, noted
    del g["IncomeLossFromContinuingOperationsPerDilutedShare"]["units"]["USD/shares"][0:3]
    snap_ = _snap_of(g)
    assert "eps: 2012, 2013, 2014 from EarningsPerShareDiluted" in snap_.notes and _eps_years(snap_)["2012"] == 2.0


def test_eps_fill_tolerance_is_25_percent_dell_and_refuses_beyond():
    # DELL: FY2024 restated 4.60 vs 4.36 in the recipe (5% off) -> the recipe still fills FY2022
    g = _per_share({"EarningsPerShareDiluted": {2020: 3.0, 2021: 3.5, 2023: 4.0, 2024: 4.60, 2025: 5.0},
                    "IncomeLossFromContinuingOperationsPerDilutedShare": {2022: 7.02, 2024: 4.36}})
    s = resolve_series(g, {}, "eps")
    assert s.tag == "EarningsPerShareDiluted" and s.values["2022-12-31"] == 7.02 and not s.tag_break
    # 30% off in an overlap year: refused, the year stays blank, noted as a tag break
    g = _per_share({"EarningsPerShareDiluted": {2020: 3.0, 2021: 3.5, 2023: 4.0, 2024: 4.60, 2025: 5.0},
                    "IncomeLossFromContinuingOperationsPerDilutedShare": {2022: 7.02, 2024: 3.0}})
    s = resolve_series(g, {}, "eps")
    assert "2022-12-31" not in s.values and s.tag_break and s.far_off == [
        "IncomeLossFromContinuingOperationsPerDilutedShare+IncomeLossFromDiscontinuedOperationsNetOfTaxPerDilutedShare"
        "+DiscontinuedOperationIncomeLossFromDiscontinuedOperationNetOfTaxPerDilutedShare"]


def test_eps_single_tag_recipes_in_table_order():
    for tag in ("EarningsPerShareBasicAndDiluted", "IncomeLossFromContinuingOperationsPerBasicAndDilutedShare",
                "NetIncomeLossNetOfTaxPerOutstandingLimitedPartnershipUnitDiluted"):
        s = resolve_series(_per_share({tag: {2024: 1.0, 2025: 1.1}}), {}, "eps")
        assert s.tag == tag and len(s.values) == 2, tag
    # diluted EPS outranks the recipe when both cover the latest year
    g = _per_share({"EarningsPerShareDiluted": {2025: 1.0}, "IncomeLossFromContinuingOperationsPerDilutedShare": {2025: 1.1}})
    assert resolve_series(g, {}, "eps").tag == "EarningsPerShareDiluted"
    # fill-only tags never lead
    for tag in ("NetIncomeLossPerOutstandingLimitedPartnershipUnitBasicNetOfTax",
                "ifrs-full:BasicEarningsLossPerShareFromContinuingOperations"):
        assert tag in edgar.FILL_ONLY
    ifrs_ = {"DilutedEarningsLossPerShareFromContinuingOperations": _per_share({"x": {2024: 3.0, 2025: 3.0}})["x"],
             "BasicEarningsLossPerShareFromContinuingOperations": _per_share({"x": {2023: 3.1, 2024: 3.1, 2025: 3.1}})["x"]}
    s = resolve_series({}, ifrs_, "eps")
    assert s.tag == "ifrs-full:DilutedEarningsLossPerShareFromContinuingOperations" and len(s.values) == 3   # SHEL


def test_cfo_continuing_operations_fills_at_25_percent_dltr():
    # DLTR: the FY2023+ 10-Ks tag cfo as ...ContinuingOperations (12% off the total tag in the overlap)
    g = _flows({"NetCashProvidedByUsedInOperatingActivitiesContinuingOperations": {2023: 1418.0, 2024: 2401.0, 2025: 2193.0, 2026: 2191.0},
                "NetCashProvidedByUsedInOperatingActivities": {**{y: 1000.0 + y for y in range(2009, 2023)}, 2023: 1615.0, 2024: 2684.0}})
    s = resolve_series(g, {}, "cfo")
    assert s.tag == "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations" and len(s.values) == 18
    assert s.values["2026-12-31"] == 2191.0 and s.values["2023-12-31"] == 1418.0 and s.values["2010-12-31"] == 3010.0
    assert not s.tag_break and s.source["2010-12-31"] == "NetCashProvidedByUsedInOperatingActivities"


def test_shares_basic_fills_the_diluted_tag_xom():
    g = _flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {y: 4000.0 for y in range(2010, 2014)},
                "WeightedAverageNumberOfSharesOutstandingBasic": {y: 3990.0 for y in range(2010, 2026)}}, "shares")
    g.update(_flows({"NetIncomeLoss": {y: 5.0 for y in range(2010, 2026)}}))
    s = resolve_series(g, {}, "shares")
    assert s.tag == "WeightedAverageNumberOfDilutedSharesOutstanding" and len(s.values) == 16 and not s.basic_only
    assert s.source["2025-12-31"] == "WeightedAverageNumberOfSharesOutstandingBasic"
    g["WeightedAverageNumberOfSharesOutstandingBasic"]["units"]["shares"].append(
        {"start": "2026-07-01", "end": "2026-09-30", "val": 3900.0, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"})
    snap_ = _snap_of(g)
    assert snap_.ttm["shares"] == 3900.0 and snap_.balance_ends["shares"] == "2026-09-30"   # from the filling tag
    assert "shares: 2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021, 2022, 2023, 2024, 2025 from " \
           "WeightedAverageNumberOfSharesOutstandingBasic" in snap_.notes


def test_revenue_ifrs_contracts_with_customers_nok_kgc():
    def node(vals):
        return _flows({"x": vals}, "EUR")["x"]
    ifrs_ = {"Revenue": node({2015: 12560.0, 2016: 23641.0, 2017: 23147.0}),
             "RevenueFromContractsWithCustomers": node({2016: 23641.0, 2017: 23147.0, 2018: 22563.0, 2025: 19889.0})}
    s = resolve_series({}, ifrs_, "revenue")
    assert s.tag == "ifrs-full:RevenueFromContractsWithCustomers" and len(s.values) == 5
    assert s.values["2015-12-31"] == 12560.0 and s.source["2015-12-31"] == "ifrs-full:Revenue" and not s.tag_break
    ifrs_ = {"RevenueFromSaleOfGoods": node({2016: 3472.0, 2017: 3303.0}),
             "RevenueFromContractsWithCustomers": node({2017: 3303.0, 2018: 3212.6})}
    assert len(resolve_series({}, ifrs_, "revenue").values) == 3


def test_revenue_and_operating_income_only_for_financial_sic_hsbc_abvx():
    ifrs_ = {"RevenueAndOperatingIncome": _flows({"x": {2024: 65.85, 2025: 68.27}})["x"],
             "ProfitLossAttributableToOwnersOfParent": _flows({"x": {2024: 20.0, 2025: 21.0}})["x"]}
    assert resolve_series({}, ifrs_, "revenue", sic=6035).tag == "ifrs-full:RevenueAndOperatingIncome"
    assert resolve_series({}, ifrs_, "revenue", sic=2834) is None and resolve_series({}, ifrs_, "revenue") is None
    bank = build_snapshot("HSBC", 1, {"facts": {"ifrs-full": ifrs_}}, sic=6035, name="HSBC", fetched_at=FA)
    biotech = build_snapshot("ABVX", 1, {"facts": {"ifrs-full": ifrs_}}, sic=2834, name="ABVX", fetched_at=FA)
    assert bank.annual["2025-12-31"]["revenue"] == 68.27 and "revenue" not in biotech.units


# --- R36: derived EPS = NI to common / diluted shares ---

def test_eps_derived_only_for_years_without_an_eps_tag():
    g = _flows({"NetIncomeLoss": {y: 100.0 for y in range(2018, 2026)}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {y: 50.0 for y in range(2018, 2026)}}, "shares"))
    g.update(_per_share({"EarningsPerShareDiluted": {y: 2.0 for y in range(2021, 2026)}}))
    s = _snap_of(g)
    assert _eps_years(s) == {str(y): 2.0 for y in range(2018, 2026)}
    assert s.tags["eps"] == "EarningsPerShareDiluted" and "eps: 2018, 2019, 2020 derived from NI/shares" in s.notes
    # a tagged year is never replaced, even when NI/shares differs
    g["EarningsPerShareDiluted"]["units"]["USD/shares"][0]["val"] = 1.9
    assert _eps_years(_snap_of(g))["2021"] == 1.9


def test_eps_derived_from_period_end_shares_when_no_weighted_tag_hsy():
    g = _flows({"NetIncomeLoss": {y: 200.0 for y in range(2018, 2026)}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {2018: 100.0, 2019: 100.0}}, "shares"))
    g.update(_insts({"CommonStockSharesOutstanding": {y: 80.0 for y in range(2018, 2026)}}))
    g["CommonStockSharesOutstanding"]["units"] = {"shares": g["CommonStockSharesOutstanding"]["units"]["USD"]}
    s = _snap_of(g)
    assert s.tags["eps"] == "derived" and s.units["eps"] == "USD/shares"
    assert _eps_years(s) == {"2018": 2.0, "2019": 2.0, **{str(y): 2.5 for y in range(2020, 2026)}}
    assert "eps: 2018, 2019 derived from NI/shares" in s.notes
    assert "eps: 2020, 2021, 2022, 2023, 2024, 2025 derived from NI/period-end shares" in s.notes
    assert s.ttm["eps"] == 2.5 and s.ttm_ends["eps"] == ("2025-12-31", True)


def test_eps_derived_uses_ni_to_common_never_profitloss_glxy_fcx():
    g = _flows({"ProfitLoss": {2024: 300.0, 2025: 300.0},                                       # incl. minority
                "NetIncomeLossAvailableToCommonStockholdersDiluted": {2024: 150.0, 2025: 150.0}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {2024: 100.0, 2025: 100.0}}, "shares"))
    s = _snap_of(g)
    assert _eps_years(s) == {"2024": 1.5, "2025": 1.5}
    # ProfitLoss alone (no to-common / parent tag): nothing to derive from
    g2 = _flows({"ProfitLoss": {2024: 300.0, 2025: 300.0}})
    g2.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {2024: 100.0, 2025: 100.0}}, "shares"))
    assert "eps" not in _snap_of(g2).units


def test_eps_not_derived_when_ni_over_shares_contradicts_the_tag():
    # MCD-style: diluted shares tagged in millions; NI/shares is 1e6 off the EPS tag -> no derivation
    g = _flows({"NetIncomeLoss": {y: 100e6 for y in range(2018, 2026)}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {y: 50.0 for y in range(2018, 2026)}}, "shares"))
    g.update(_per_share({"EarningsPerShareDiluted": {y: 2.0 for y in range(2021, 2026)}}))
    s = _snap_of(g)
    assert set(_eps_years(s)) == {str(y) for y in range(2021, 2026)}
    assert "eps: NI/shares disagrees with the EPS tag; not derived" in s.notes


def test_eps_derived_rescales_a_mis_scaled_share_year_hsy_2009():
    g = _flows({"NetIncomeLoss": {y: 400.0 for y in range(2008, 2015)}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {**{y: 230.0 for y in range(2008, 2015)}, 2009: 0.229}},
                    "shares"))
    s = _snap_of(g)
    assert _eps_years(s)["2009"] == pytest.approx(400.0 / 229.0) and _eps_years(s)["2010"] == pytest.approx(400.0 / 230.0)
    assert "eps: share count rescaled for the derived EPS (2009 x1000)" in s.notes
    g["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"][1]["val"] = 7.0   # not a power of 1000
    assert "2009" not in _eps_years(_snap_of(g))


def test_net_income_prefers_to_common_over_profitloss_fcx_ibkr():
    # FCX never tags NetIncomeLoss: ProfitLoss (incl. minority) vs to-common basic, both cover the latest year
    g = _flows({"ProfitLoss": {y: 4152.0 for y in range(2007, 2026)},
                "NetIncomeLossAvailableToCommonStockholdersBasic": {y: 2197.0 for y in range(2010, 2026)}})
    s = resolve_series(g, {}, "net_income")
    assert s.tag == "NetIncomeLossAvailableToCommonStockholdersBasic" and s.values["2025-12-31"] == 2197.0
    assert "2007-12-31" not in s.values and s.tag_break           # ProfitLoss disagrees, old years stay blank
    # NetIncomeLoss covering the latest year still leads (and fills from the basic tag only when it agrees)
    g = _flows({"ProfitLoss": {y: 4152.0 for y in range(2007, 2026)},
                "NetIncomeLoss": {y: 2200.0 for y in range(2020, 2026)},
                "NetIncomeLossAvailableToCommonStockholdersBasic": {y: 2197.0 for y in range(2010, 2026)}})
    s = resolve_series(g, {}, "net_income")
    assert s.tag == "NetIncomeLoss" and s.values["2010-12-31"] == 2197.0 and s.values["2025-12-31"] == 2200.0
    # ProfitLoss alone stays primary
    assert resolve_series(_flows({"ProfitLoss": {2025: 1.0}}), {}, "net_income").tag == "ProfitLoss"


# --- splits: normalised before comparison; 10-Q split ratio ---

def _tagrows(tag, rows, unit="USD/shares"):
    """rows: (start, end, val, accn, form, filed)"""
    return {tag: {"units": {unit: [{"start": s, "end": e, "val": v, "accn": a, "form": f, "filed": fl}
                                   for s, e, v, a, f, fl in rows]}}}


def test_per_share_candidates_are_split_normalised_before_comparison_fcx():
    # 2:1 split reflected from the FY2010 10-K (filed 2011-02) on. The cont-ops tag's 2008 and 2009
    # facts were filed before the split (pre-split basis); the diluted tag's 2009 fact was restated.
    g = {}
    g.update(_tagrows("EarningsPerShareDiluted", [("2009-01-01", "2009-12-31", 5.86, "k09", "10-K", "2010-02-26"),
                                                  ("2009-01-01", "2009-12-31", 2.93, "k10", "10-K", "2011-02-25"),
                                                  ("2010-01-01", "2010-12-31", 4.57, "k10", "10-K", "2011-02-25"),
                                                  ("2011-01-01", "2011-12-31", 4.78, "k11", "10-K", "2012-02-24")]))
    g.update(_tagrows("IncomeLossFromContinuingOperationsPerDilutedShare",
                      [("2008-01-01", "2008-12-31", -29.72, "k08", "10-K", "2009-02-27"),
                       ("2009-01-01", "2009-12-31", 5.86, "k09", "10-K", "2010-02-26"),
                       ("2011-01-01", "2011-12-31", 4.78, "k11", "10-K", "2012-02-24"),
                       ("2025-01-01", "2025-12-31", 1.52, "k25", "10-K", "2026-02-20")]))
    g.update(_tagrows("WeightedAverageNumberOfDilutedSharesOutstanding",
                      [("2009-01-01", "2009-12-31", 470.0, "k09", "10-K", "2010-02-26"),
                       ("2009-01-01", "2009-12-31", 940.0, "k10", "10-K", "2011-02-25"),
                       ("2025-01-01", "2025-12-31", 1443.0, "k25", "10-K", "2026-02-20")], "shares"))
    g.update(_flows({"NetIncomeLoss": {y: 1.0 for y in (2008, 2009, 2010, 2011, 2025)}}))
    s = _snap_of(g)
    eps = _eps_years(s)
    assert eps["2008"] == pytest.approx(-14.86) and eps["2009"] == pytest.approx(2.93) and eps["2025"] == 1.52
    assert eps["2010"] == 4.57 and "eps" not in s.tag_breaks and not any("not used" in n for n in s.notes)
    assert s.annual["2009-12-31"]["shares"] == pytest.approx(940.0) and s.split_factor == pytest.approx(0.5)
    assert any(n.startswith("splits: x0.5 after 2010-02-26") for n in s.notes)


def test_split_scan_compares_within_one_tag_only():
    # continuing-ops EPS at half of total EPS in the same filings is not a split
    g = {}
    g.update(_tagrows("EarningsPerShareDiluted", [("2024-01-01", "2024-12-31", 4.0, "k24", "10-K", "2025-02-01"),
                                                  ("2025-01-01", "2025-12-31", 4.2, "k25", "10-K", "2026-02-01")]))
    g.update(_tagrows("IncomeLossFromContinuingOperationsPerDilutedShare",
                      [("2024-01-01", "2024-12-31", 2.0, "k24", "10-K", "2025-02-01"),
                       ("2025-01-01", "2025-12-31", 2.1, "k25", "10-K", "2026-02-01")]))
    g.update(_flows({"NetIncomeLoss": {2024: 1.0, 2025: 1.0}}))
    s = _snap_of(g)
    assert s.split_factor == 1.0 and not any(n.startswith("splits") for n in s.notes)
    assert _eps_years(s) == {"2024": 4.0, "2025": 4.2}


def test_10q_split_ratio_event_cvna():
    g = {}
    g.update(_tagrows("EarningsPerShareDiluted", [("2024-01-01", "2024-12-31", 1.59, "k24", "10-K", "2025-02-20"),
                                                  ("2025-01-01", "2025-12-31", 8.45, "k25", "10-K", "2026-02-20")]))
    g.update(_tagrows("WeightedAverageNumberOfDilutedSharesOutstanding",
                      [("2025-01-01", "2025-12-31", 224.3, "k25", "10-K", "2026-02-20"),
                       ("2026-07-01", "2026-09-30", 739.9, "q3", "10-Q", "2026-10-30")], "shares"))
    g.update(_tagrows("StockholdersEquityNoteStockSplitConversionRatio1",
                      [(None, "2026-05-05", 0.2, "q2", "10-Q", "2026-07-30")], "pure"))
    g.update(_flows({"NetIncomeLoss": {2024: 1.0, 2025: 1407.0}}))
    s = _snap_of(g)
    assert _eps_years(s) == {"2024": pytest.approx(1.59 / 5), "2025": pytest.approx(8.45 / 5)}
    assert s.annual["2025-12-31"]["shares"] == pytest.approx(224.3 * 5) and s.ttm["shares"] == 739.9
    assert s.split_factor == pytest.approx(0.2) and "split 5:1 from the 10-Q split ratio at 2026-05-05" in s.notes
    # the ratio tagged the other way round (5) means the same
    g["StockholdersEquityNoteStockSplitConversionRatio1"]["units"]["pure"][0]["val"] = 5.0
    assert _eps_years(_snap_of(g))["2025"] == pytest.approx(8.45 / 5)
    # no share-count jump: the ratio fact alone proves nothing
    g["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"][1]["val"] = 230.0
    s = _snap_of(g)
    assert _eps_years(s)["2025"] == 8.45 and s.split_factor == 1.0
    # a split the annual filings already reflect (scan event within 400 days) is not applied twice
    g["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"][1]["val"] = 739.9
    g["EarningsPerShareDiluted"]["units"]["USD/shares"].append(
        {"start": "2025-01-01", "end": "2025-12-31", "val": 1.69, "accn": "k26", "form": "10-K", "filed": "2027-02-20"})
    g["WeightedAverageNumberOfDilutedSharesOutstanding"]["units"]["shares"].append(
        {"start": "2025-01-01", "end": "2025-12-31", "val": 1121.5, "accn": "k26", "form": "10-K", "filed": "2027-02-20"})
    s = _snap_of(g)
    assert _eps_years(s)["2025"] == pytest.approx(1.69) and _eps_years(s)["2024"] == pytest.approx(1.59 / 5)


def test_ttm_eps_from_the_eps_tag_ibkr_bynd():
    g = {}
    g.update(_tagrows("EarningsPerShareDiluted", [("2025-01-01", "2025-12-31", 2.22, "k25", "10-K", "2026-02-20"),
                                                  ("2026-01-01", "2026-09-30", 1.98, "q3", "10-Q", "2026-10-30"),
                                                  ("2025-01-01", "2025-09-30", 1.58, "q3", "10-Q", "2026-10-30")]))
    g.update(_flows({"NetIncomeLoss": {2025: 1.0}}))
    s = _snap_of(g)
    assert s.ttm["eps"] == pytest.approx(2.22 + 1.98 - 1.58) and s.ttm_ends["eps"] == ("2026-09-30", False)
    # with a split between the 10-K and the 10-Q, the FY value is brought to the 10-Q's basis
    g["EarningsPerShareDiluted"]["units"]["USD/shares"].append(
        {"start": "2024-01-01", "end": "2024-12-31", "val": 4.0, "accn": "k24", "form": "10-K", "filed": "2025-02-20"})
    g["EarningsPerShareDiluted"]["units"]["USD/shares"].append(
        {"start": "2024-01-01", "end": "2024-12-31", "val": 2.0, "accn": "k25", "form": "10-K", "filed": "2026-02-20"})
    g.update(_flows({"NetIncomeLoss": {2024: 1.0, 2025: 1.0}}))
    s = _snap_of(g)
    assert s.ttm["eps"] == pytest.approx(2.22 + 1.98 - 1.58)      # all three filed after the split
    # a recipe label gets its TTM from the parts (continuing + optional discontinued)
    g = {}
    g.update(_tagrows("IncomeLossFromContinuingOperationsPerDilutedShare",
                      [("2025-01-01", "2025-12-31", 1.5, "k25", "10-K", "2026-02-20"),
                       ("2026-01-01", "2026-06-30", 1.0, "q2", "10-Q", "2026-07-30"),
                       ("2025-01-01", "2025-06-30", 0.6, "q2", "10-Q", "2026-07-30")]))
    g.update(_flows({"NetIncomeLoss": {2025: 1.0}}))
    s = _snap_of(g)
    assert s.ttm["eps"] == pytest.approx(1.9) and s.ttm_ends["eps"] == ("2026-06-30", False)


# --- LHX: the full-year revenue tagged with Q4 dates ---

def test_misdated_10k_revenue_is_rescued_from_the_9_month_ytd_lhx():
    g = _flows({"NetIncomeLoss": {2024: 1.0, 2025: 1.0}, "RevenueFromContractWithCustomerExcludingAssessedTax": {2024: 21325.0}})
    g["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"] += [
        {"start": "2025-10-01", "end": "2025-12-31", "val": 21865.0, "accn": "k25", "form": "10-K", "filed": "2026-02-12"},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 16217.0, "accn": "q3", "form": "10-Q", "filed": "2025-10-30"}]
    s = _snap_of(g)
    assert s.annual["2025-12-31"]["revenue"] == 21865.0 and s.ttm["revenue"] == 21865.0
    assert "revenue: 10-K fact 2025-10-01..2025-12-31 taken as the annual value (above the 9-month YTD " \
           "2025-01-01..2025-09-30)" in s.notes
    # a real quarter (below the YTD) is never taken as the year
    g["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"][1]["val"] = 5648.0
    assert "revenue" not in _snap_of(g).annual["2025-12-31"]
    # nor a value more than 1.5x the YTD
    g["RevenueFromContractWithCustomerExcludingAssessedTax"]["units"]["USD"][1]["val"] = 30000.0
    assert "revenue" not in _snap_of(g).annual["2025-12-31"]


# --- R37 capex recipes and R41 larger-value rule ---

@pytest.mark.parametrize("tag", ["PaymentsToAcquireOtherProductiveAssets", "PaymentsForCapitalImprovements",
                                 "PaymentsForProceedsFromProductiveAssets"])
def test_new_capex_tags(tag):
    s = resolve_series(_flows({tag: {2024: -5.0, 2025: -6.0}}), {}, "capex")
    assert s.tag.startswith(tag) and s.values == {"2024-12-31": 5.0, "2025-12-31": 6.0}


def test_new_ifrs_capex_tags_shel_acb():
    for tag in ("PurchaseOfOtherLongtermAssetsClassifiedAsInvestingActivities",
                "PurchaseOfPropertyPlantAndEquipmentIntangibleAssetsOtherThanGoodwillInvestmentPropertyAndOtherNoncurrentAssets"):
        s = resolve_series({}, {tag: _flows({"x": {2024: 5.0, 2025: 6.0}})["x"]}, "capex")
        assert s.tag == "ifrs-full:" + tag and s.values["2025-12-31"] == 6.0


def test_reit_capex_is_improvements_plus_construction_well():
    g = _flows({"PaymentsForCapitalImprovements": {2024: 900.0, 2025: 1050.0},
                "PaymentsForConstructionInProcess": {2025: 438.0},
                "PaymentsToAcquireCommercialRealEstate": {2025: 13914.0}})      # acquisitions: not capex
    s = resolve_series(g, {}, "capex")
    assert s.tag == "PaymentsForCapitalImprovements+PaymentsForConstructionInProcess"
    assert s.values == {"2024-12-31": 900.0, "2025-12-31": 1488.0}


def test_capex_larger_value_wins_and_the_fuller_tag_fills_vz():
    # VZ: the new tag carries the acquisitions line in 2009-2014 (small), the old tag the real capex
    g = _flows({"PaymentsToAcquireOtherProductiveAssets": {**{y: 1000.0 for y in range(2009, 2015)},
                                                           **{y: 17000.0 for y in range(2017, 2026)}},
                "PaymentsToAcquireProductiveAssets": {y: 16000.0 for y in range(2007, 2019)}})
    s = resolve_series(g, {}, "capex")
    assert s.tag == "PaymentsToAcquireOtherProductiveAssets" and len(s.values) == 19
    assert s.values["2012-12-31"] == 16000.0 and s.source["2012-12-31"] == "PaymentsToAcquireProductiveAssets"
    assert s.values["2007-12-31"] == 16000.0 and s.values["2025-12-31"] == 17000.0 and not s.far_off
    assert s.larger == {"PaymentsToAcquireProductiveAssets": [f"{y}-12-31" for y in range(2009, 2015)]}
    g.update(_flows({"NetIncomeLoss": {y: 5.0 for y in range(2007, 2026)}}))
    assert "capex: candidates differ, larger used in 2009, 2010, 2011, 2012, 2013, 2014 " \
           "(PaymentsToAcquireProductiveAssets)" in _snap_of(g).notes


def test_debt_larger_candidate_replaces_a_component_mar_orcl():
    # MAR: LongTermDebt (recipe b) is a 23M component every year; (d) carries the 16B
    g = _insts({"LongTermDebt": {y: 23.0 for y in range(2019, 2026)},
                "LongTermDebtAndCapitalLeaseObligations": {y: 15000.0 for y in range(2019, 2026)},
                "LongTermDebtAndCapitalLeaseObligationsCurrent": {2025: 1209.0}})
    s = resolve_series(g, {}, "debt")
    assert s.tag == "LongTermDebt+ShortTermBorrowings+CommercialPaper"
    assert s.values["2025-12-31"] == 16209.0 and s.values["2024-12-31"] == 15000.0
    assert s.source["2025-12-31"] == "LongTermDebtAndCapitalLeaseObligations+LongTermDebtAndCapitalLeaseObligationsCurrent"
    g.update(_flows({"NetIncomeLoss": {y: 5.0 for y in range(2019, 2026)}}))
    snap_ = _snap_of(g)
    assert any(n.startswith("debt: candidates differ, larger used in 2019, 2020") for n in snap_.notes)
    # ORCL: a one-row 0 under LongTermDebt never beats the issuer total
    g = _insts({"LongTermDebt": {2022: 0.0},
                "DebtLongtermAndShorttermCombinedAmount": {2022: 80000.0, 2025: 92568.0, 2026: 129541.0}})
    s = resolve_series(g, {}, "debt")
    assert s.values == {"2022-12-31": 80000.0, "2025-12-31": 92568.0, "2026-12-31": 129541.0}
    # within 25% the primary keeps its own value (structural differences, no churn)
    g = _insts({"LongTermDebt": {2025: 100.0}, "LongTermDebtAndCapitalLeaseObligations": {2025: 120.0}})
    assert resolve_series(g, {}, "debt").values["2025-12-31"] == 100.0


def test_new_debt_recipes_from_the_probe():
    assert resolve_series(_insts({"DebtLongtermAndShorttermCombinedAmount": {2025: 9.0}}), {}, "debt").values["2025-12-31"] == 9.0
    hl = resolve_series(_insts({"LongTermNotesPayable": {2024: 508.9, 2025: 268.6}, "DebtCurrent": {2024: 33.6}}), {}, "debt")
    assert hl.values == {"2024-12-31": 542.5, "2025-12-31": 268.6}                     # DebtCurrent optional
    xyz = resolve_series(_insts({"OtherLongTermDebtNoncurrent": {2025: 5715.8}, "OtherLongTermDebtCurrent": {2025: 1573.3},
                                 "SecuredDebtCurrent": {2025: 466.9}, "LongTermLineOfCredit": {2025: 897.9}}), {}, "debt")
    assert xyz.values["2025-12-31"] == pytest.approx(8653.9)
    ntra = resolve_series(_insts({"LongTermNotesAndLoans": {2023: 286.5}, "ShortTermBorrowings": {2023: 80.4, 2025: 80.3}}), {}, "debt")
    assert ntra.values == {"2023-12-31": 366.9, "2025-12-31": 80.3} and ntra.source["2025-12-31"] == "ShortTermBorrowings"
    for tag in ("LinesOfCreditCurrent", "LongTermLineOfCredit", "ShortTermBorrowings"):
        s = resolve_series(_insts({tag: {2025: 20.0}}), {}, "debt")
        assert s.tag == tag and s.values["2025-12-31"] == 20.0 and tag in edgar.PARTIAL_DEBT
    tsm = {"NoncurrentPortionOfNoncurrentBondsIssued": _insts({"x": {2024: 926.6}})["x"],
           "CurrentBondsIssuedAndCurrentPortionOfNoncurrentBondsIssued": _insts({"x": {2024: 57.1}})["x"],
           "LongtermBorrowings": _insts({"x": {2024: 31.8}})["x"],
           "CurrentPortionOfLongtermBorrowings": _insts({"x": {2024: 59.9}})["x"]}
    s = resolve_series({}, tsm, "debt")
    # the borrowings recipe (f) is primary by table order; the bonds recipe wins the year (R41, larger)
    assert s.values["2024-12-31"] == pytest.approx(1075.4)
    assert s.source["2024-12-31"].startswith("ifrs-full:NoncurrentPortionOfNoncurrentBondsIssued")
    assert resolve_series({}, {k: v for k, v in tsm.items() if "Bonds" in k}, "debt").values["2024-12-31"] == pytest.approx(983.7)
    tme = {"NotesAndDebenturesIssued": _insts({"x": {2024: 3.572}})["x"],
           "CurrentNotesAndDebenturesIssuedAndCurrentPortionOfNoncurrentNotesAndDebenturesIssued": _insts({"x": {2024: 2.154}})["x"]}
    assert resolve_series({}, tme, "debt").values["2024-12-31"] == pytest.approx(5.726)


def test_debt_tag_ended_at_zero_carries_forward_mdb():
    g = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2022, 2027)}})
    g.update(_insts({"ConvertibleDebtNoncurrent": {2022: 1136.5, 2023: 1139.9, 2024: 1143.3, 2025: 0.0}}))
    s = _snap_of(g)
    assert s.annual["2026-12-31"]["debt"] == 0.0 and s.ttm["debt"] == 0.0 and s.balance_ends["debt"] == "2026-12-31"
    assert "debt: 0 carried forward to 2026 (tag ended at 0 in 2025)" in s.notes
    # a tag that ended on a real balance is not carried
    g["ConvertibleDebtNoncurrent"]["units"]["USD"][-1]["val"] = 900.0
    s = _snap_of(g)
    assert "debt" not in s.annual["2026-12-31"] and not any("carried" in n for n in s.notes)


def test_carry_zero_debt_needs_absent_or_small_interest():
    """E1 (fail-safe): 0 is carried only into a later year whose interest expense is absent or
    < 1% of a positive operating income (CARRY_ZERO_INTEREST_SHARE); the carry stops at the first
    year that fails, and that year and every later one stay blank."""
    assert edgar.CARRY_ZERO_INTEREST_SHARE == 0.01
    base = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2022, 2028)}})
    base.update(_insts({"ConvertibleDebtNoncurrent": {2022: 1136.5, 2023: 1139.9, 2024: 1143.3, 2025: 0.0}}))
    # no interest tag at all (MDB / OUST / SOUN): carried into every later year
    s = _snap_of(dict(base))
    assert s.annual["2026-12-31"]["debt"] == 0.0 and s.annual["2027-12-31"]["debt"] == 0.0
    assert "debt: 0 carried forward to 2026, 2027 (tag ended at 0 in 2025)" in s.notes
    # interest below 1% of operating income: carried
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 0.5e6, 2027: 0.4e6}, "OperatingIncomeLoss": {2026: 90e6, 2027: 95e6}}))
    s = _snap_of(g)
    assert s.annual["2026-12-31"]["debt"] == 0.0 and s.annual["2027-12-31"]["debt"] == 0.0
    assert "debt: 0 carried forward to 2026, 2027 (tag ended at 0 in 2025)" in s.notes
    # material interest in 2027: 2026 is carried, the carry stops at 2027 and 2027 stays blank
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 0.5e6, 2027: 12e6}, "OperatingIncomeLoss": {2026: 90e6, 2027: 90e6}}))
    s = _snap_of(g)
    assert s.annual["2026-12-31"]["debt"] == 0.0 and "debt" not in s.annual["2027-12-31"]
    assert ("debt: 0 carried forward to 2026 (tag ended at 0 in 2025) "
            "(stopped at 2027: interest 12M vs operating income 90M)") in s.notes
    # material interest in the first later year: nothing is carried
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 12e6}, "OperatingIncomeLoss": {2026: 90e6}}))
    s = _snap_of(g)
    assert "debt" not in s.annual["2026-12-31"] and "debt" not in s.annual["2027-12-31"]
    assert ("debt: 0 not carried forward (tag ended at 0 in 2025) "
            "(stopped at 2026: interest 12M vs operating income 90M)") in s.notes
    # interest with no positive operating income to size it against (or no operating income at all): stopped
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 0.5e6}, "OperatingIncomeLoss": {2026: -90e6}}))
    s = _snap_of(g)
    assert "debt" not in s.annual["2026-12-31"]
    assert "debt: 0 not carried forward (tag ended at 0 in 2025) (stopped at 2026: interest 500K vs operating income -90M)" in s.notes
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 0.5e6}}))
    s = _snap_of(g)
    assert "debt" not in s.annual["2026-12-31"]
    assert "debt: 0 not carried forward (tag ended at 0 in 2025) (stopped at 2026: interest 500K vs operating income n/a)" in s.notes
    # an explicit 0 interest is as good as no interest tag, whatever the operating income
    g = dict(base)
    g.update(_flows({"InterestExpense": {2026: 0.0, 2027: 0.0}, "OperatingIncomeLoss": {2026: -90e6, 2027: -90e6}}))
    s = _snap_of(g)
    assert s.annual["2026-12-31"]["debt"] == 0.0 and s.annual["2027-12-31"]["debt"] == 0.0


def test_larger_used_and_derived_eps_notes_have_the_shape_metrics_parses():
    """E2: compute_metrics parses the years out of these two notes (fail-safe caps), so the exact
    prefix and the "YYYY, YYYY" list are pinned here."""
    g = _flows({"PaymentsToAcquireOtherProductiveAssets": {**{y: 1000.0 for y in range(2016, 2019)},
                                                           **{y: 17000.0 for y in range(2020, 2026)}},
                "PaymentsToAcquireProductiveAssets": {y: 16000.0 for y in range(2015, 2022)},
                "NetIncomeLoss": {y: 5.0 for y in range(2015, 2026)}})
    capex = [n for n in _snap_of(g).notes if n.startswith("capex: candidates differ, larger used in ")]
    assert capex == ["capex: candidates differ, larger used in 2016, 2017, 2018 (PaymentsToAcquireProductiveAssets)"]
    m = re.fullmatch(r"capex: candidates differ, larger used in (\d{4}(?:, \d{4})*) \(([^()]+)\)", capex[0])
    assert m and m.group(1) == "2016, 2017, 2018" and m.group(2) == "PaymentsToAcquireProductiveAssets"
    g = _flows({"NetIncomeLoss": {y: 100.0 for y in range(2008, 2012)}})
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {y: 50.0 for y in range(2008, 2012)}}, "shares"))
    g.update(_per_share({"EarningsPerShareDiluted": {2010: 2.0, 2011: 2.0}}))
    eps = [n for n in _snap_of(g).notes if n.startswith("eps: ") and "derived from NI/" in n]
    assert eps == ["eps: 2008, 2009 derived from NI/shares"]
    m = re.fullmatch(r"eps: (\d{4}(?:, \d{4})*) derived from NI/(period-end )?shares", eps[0])
    assert m and m.group(1) == "2008, 2009"


def test_10q_comparative_instant_at_the_fiscal_year_end_counts_chwy():
    g = _flows({"NetIncomeLoss": {2024: 5.0, 2025: 5.0}})
    g["LongTermDebt"] = {"units": {"USD": [
        {"end": "2025-12-31", "val": 0.0, "accn": "q2", "form": "10-Q", "filed": "2026-08-01"},        # FY-end comparative
        {"end": "2026-06-30", "val": 591.7, "accn": "q2", "form": "10-Q", "filed": "2026-08-01"}]}}
    s = _snap_of(g)
    assert s.annual["2025-12-31"]["debt"] == 0.0 and s.ttm["debt"] == 591.7 and s.balance_ends["debt"] == "2026-06-30"
    # a 10-Q instant that is not at a fiscal year end is still not an annual row
    assert "2026-06-30" not in s.annual
    # flows never take 10-Q rows
    g["NetIncomeLoss"]["units"]["USD"].append({"start": "2026-01-01", "end": "2026-12-31", "val": 9.0, "accn": "q",
                                               "form": "10-Q", "filed": "2027-02-01"})
    assert "2026-12-31" not in _snap_of(g).annual


# --- R42: facts split across CIKs ---

def test_merge_companyfacts_takes_only_accessions_the_base_lacks():
    base = {"cik": 1, "facts": {"us-gaap": {"NetIncomeLoss": {"units": {"USD": [
        {"start": "2025-01-01", "end": "2025-12-31", "val": 1.0, "accn": "k25", "form": "10-K", "filed": "2026-02-01"}]}}}}}
    extra = {"cik": 2, "facts": {"us-gaap": {
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2025-01-01", "end": "2025-12-31", "val": 99.0, "accn": "k25", "form": "10-K", "filed": "2026-02-01"},
            {"start": "2026-01-01", "end": "2026-09-30", "val": 3.0, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"},
            {"start": "2025-01-01", "end": "2025-09-30", "val": 2.0, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"}]}},
        "Assets": {"units": {"USD": [{"end": "2026-09-30", "val": 50.0, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"},
                                     {"end": "2024-12-31", "val": 40.0, "accn": "other", "form": "10-K", "filed": "2025-02-01"}]}}}}}
    merged = edgar.merge_companyfacts(base, extra)
    rows = merged["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"]
    assert [r["val"] for r in rows] == [1.0, 3.0, 2.0]                      # k25 already in base: skipped
    assert [r["val"] for r in merged["facts"]["us-gaap"]["Assets"]["units"]["USD"]] == [50.0, 40.0]
    assert base["facts"]["us-gaap"]["NetIncomeLoss"]["units"]["USD"] == rows[:1]   # base untouched
    limited = edgar.merge_companyfacts(base, extra, allowed_accns={"q3"})
    assert [r["val"] for r in limited["facts"]["us-gaap"]["Assets"]["units"]["USD"]] == [50.0]
    s = build_snapshot("X", 1, merged, sic=None, name="X", fetched_at=FA)
    assert s.ttm["net_income"] == 2.0 and s.latest_period_end == "2026-09-30"
    assert edgar.EXTRA_CIKS == {"XOM": (2115436,), "NEE": (37634,)} and "NEE" in edgar.NEEDS_ACCESSION_FILTER


def test_client_submission_with_accessions():
    raw = json.loads((FIX / "submissions_JPM.json").read_text(encoding="utf-8"))
    raw.update({"filings": {"recent": {"accessionNumber": ["0000019617-26-000001", "0000019617-26-000002"]}}})
    client = EdgarClient(lambda u, h: json.dumps(raw).encode(), sleep=lambda s: None)
    assert "accessions" not in client.submission(19617)
    sub = client.submission(19617, with_accessions=True)
    assert sub["accessions"] == ["0000019617-26-000001", "0000019617-26-000002"] and sub["sic"] == 6021


# --- fixture-pinned (trimmed real companyfacts) ---

def test_fixture_hal_eps_recipe_covers_the_tag_switch():
    s = nsnap("HAL")
    eps = _eps_years(s)
    assert s.tags["eps"].startswith("IncomeLossFromContinuingOperationsPerDilutedShare+")
    assert len(eps) == 14 and eps["2025"] == 1.5 and eps["2022"] == 1.73 and eps["2019"] == -1.29
    assert "eps" not in s.tag_breaks and not any(n.startswith("eps:") and "not used" in n for n in s.notes)
    assert s.ttm["eps"] == 1.5                                   # FY2025 10-K is the latest EPS filing in the fixture


def test_fixture_lhx_misdated_annual_revenue():
    s = nsnap("LHX")
    assert s.latest_annual_end == "2026-01-02" and s.annual["2026-01-02"]["revenue"] == 21865000000.0
    assert "revenue: 10-K fact 2025-10-04..2026-01-02 taken as the annual value (above the 9-month YTD " \
           "2025-01-04..2025-10-03)" in s.notes
    assert s.ttm["revenue"] == 21865000000.0 and s.ttm_ends["revenue"] == ("2026-01-02", True)


def test_fixture_hsy_eps_derived_from_period_end_shares():
    s = nsnap("HSY")
    eps = _eps_years(s)
    assert s.tags["eps"] == "derived" and s.units["eps"] == "USD/shares" and len(eps) == 14
    assert eps["2024"] == pytest.approx(10.92, rel=0.01) and eps["2022"] == pytest.approx(7.96, rel=0.01)   # reported
    assert "eps: 2012, 2013, 2014 derived from NI/shares" in s.notes
    assert any(n.startswith("eps: 2015, 2016") and n.endswith("derived from NI/period-end shares") for n in s.notes)


def test_fixture_well_reit_capex():
    s = nsnap("WELL")
    g = gaap(load("WELL"))
    parts = sum(annual_facts(g[t]["units"])["USD"]["2025-12-31"].val
                for t in ("PaymentsForCapitalImprovements", "PaymentsForConstructionInProcess"))
    assert s.tags["capex"] == "PaymentsForCapitalImprovements+PaymentsForConstructionInProcess"
    assert s.annual["2025-12-31"]["capex"] == pytest.approx(parts) and parts > 1e9
    assert len([r for r in s.annual.values() if "capex" in r]) == 14 and s.ttm_ends["capex"] == ("2026-09-30", False)
    assert s.tags["net_income"] == "NetIncomeLossAvailableToCommonStockholdersBasic"   # to common, not ProfitLoss


def test_fixture_mar_debt_takes_the_larger_recipe():
    s = nsnap("MAR")
    g = gaap(load("MAR"))
    assert annual_facts(g["LongTermDebt"]["units"])["USD"]["2025-12-31"].val == 23000000.0
    assert s.annual["2025-12-31"]["debt"] == 16204000000.0 and s.ttm["debt"] == 16843000000.0
    assert s.balance_ends["debt"] == "2026-09-30"
    # audit fix E2/E7: the filer's own total (DebtAndCapitalLeaseObligations) now ranks ahead of the 23M
    # LongTermDebt fragment, so it is primary outright (no larger-value replacement needed) and names
    # the series; every window year is the total, never the fragment
    assert s.tags["debt"] == "DebtAndCapitalLeaseObligations"
    totals = annual_facts(g["DebtAndCapitalLeaseObligations"]["units"])["USD"]
    assert all(s.annual[e]["debt"] == totals[e].val for e in totals if e >= "2016" and e in s.annual)
    assert not any(n.startswith("debt: candidates differ, larger used") for n in s.notes)


def test_fixture_xom_merge_brings_the_2026_quarters():
    alone, merged = nsnap("XOM"), nsnap("XOM", edgar.merge_companyfacts(load("XOM"), load("XOM2")))
    assert alone.latest_period_end == "2026-03-31" and merged.latest_period_end == "2026-09-30"
    assert merged.ttm["net_income"] == 40622000000.0 and merged.ttm_ends["net_income"] == ("2026-09-30", False)
    assert merged.annual["2025-12-31"] == alone.annual["2025-12-31"]                  # history unchanged
    assert merged.balance_ends["shares"] == "2026-09-30" and alone.balance_ends["shares"] == "2026-03-31"
    assert any(n.startswith("shares: 2014, 2015") and n.endswith("from WeightedAverageNumberOfSharesOutstandingBasic")
               for n in merged.notes)
    assert not nsnap("XOM2").annual                                                   # the successor CIK alone has no history


# ---------- audit fix round (2026-11-07, ten-ticker audit): E1-E7 ----------

def _rows_node(rows, unit="USD"):
    """[(end, val, accn, form, filed)] or [(start, end, val, accn, form, filed)] -> one tag node."""
    out = []
    for r in rows:
        if len(r) == 6:
            st, e, v, a, f, fl = r
            out.append({"start": st, "end": e, "val": v, "accn": a, "form": f, "filed": fl})
        else:
            e, v, a, f, fl = r
            out.append({"end": e, "val": v, "accn": a, "form": f, "filed": fl})
    return {"units": {unit: out}}


def _ni(years, val=100.0):
    return _flows({"NetIncomeLoss": {y: val for y in years}})


# --- E1 (B4): split ratios ---

def test_split_ratios_include_every_real_ratio_e1():
    for r in (6.0, 9.0, 15.0, 25.0, 30.0, 40.0, 100.0):
        assert edgar._clean(r) == r and edgar._clean(1 / r) == pytest.approx(1 / r), r
    assert edgar._clean(19.37 / 3.23) == 6.0                       # DECK FY2023 EPS, 2024 vs 2025 10-K
    assert edgar._clean(29.16 / 4.86) == 6.0                       # DECK FY2024
    assert edgar._clean(5.5) is None and edgar._clean(1.13) is None   # still clean ratios only
    assert edgar.SPLIT_TOL == 0.03


def test_six_for_one_split_restates_the_pre_split_years_deck():
    # DECK: FY2023 diluted EPS 19.37 in the 2023 and 2024 10-Ks, 3.23 in the 2025 10-K (6-for-1, Sep
    # 2024); diluted shares 26,686,000 -> 160,111,000 confirm it. FY2022 exists only before the split.
    rows = [Fact("2023-03-31", "2022-04-01", 19.37, "USD/shares", "EarningsPerShareDiluted", "10-K", "k23", "2023-05-26"),
            Fact("2023-03-31", "2022-04-01", 19.37, "USD/shares", "EarningsPerShareDiluted", "10-K", "k24", "2024-05-24"),
            Fact("2023-03-31", "2022-04-01", 3.23, "USD/shares", "EarningsPerShareDiluted", "10-K", "k25", "2025-05-23"),
            Fact("2022-03-31", "2021-04-01", 16.26, "USD/shares", "EarningsPerShareDiluted", "10-K", "k22", "2022-05-27"),
            Fact("2022-03-31", "2021-04-01", 16.26, "USD/shares", "EarningsPerShareDiluted", "10-K", "k24", "2024-05-24")]
    shares = [Fact("2023-03-31", "2022-04-01", 26686000.0, "shares", "WeightedAverageNumberOfDilutedSharesOutstanding",
                   "10-K", "k24", "2024-05-24"),
              Fact("2023-03-31", "2022-04-01", 160111000.0, "shares", "WeightedAverageNumberOfDilutedSharesOutstanding",
                   "10-K", "k25", "2025-05-23")]
    assert edgar.split_events(rows, shares) == [("2024-05-24", "2025-05-23", pytest.approx(1 / 6))]
    f = infer_split_factors(rows, shares)
    assert f["2022-03-31"] == pytest.approx(1 / 6) and f["2023-03-31"] == pytest.approx(1 / 6)
    assert 16.26 * f["2022-03-31"] == pytest.approx(2.71, abs=0.005)
    # a "split" the share counts contradict is still refused
    bad = shares[:1] + [Fact("2023-03-31", "2022-04-01", 26700000.0, "shares",
                             "WeightedAverageNumberOfDilutedSharesOutstanding", "10-K", "k25", "2025-05-23")]
    assert edgar.split_events(rows, bad) == []


def test_a_pair_spanning_two_splits_is_not_a_third_split():
    # 2:1 (seen through FY2021) then 3:1 (seen through FY2023); FY2020 is seen only before the first
    # and after the second, showing their product 6. Applying all three would scale FY2020 by 1/36.
    def eps(end, val, accn, filed):
        return Fact(end, end[:4] + "-01-01", val, "USD/shares", "EarningsPerShareDiluted", "10-K", accn, filed)
    rows = [eps("2021-12-31", 1.2, "k22", "2022-02-01"), eps("2021-12-31", 0.6, "k23", "2023-02-01"),
            eps("2023-12-31", 0.9, "k24", "2024-02-01"), eps("2023-12-31", 0.3, "k25", "2025-02-01"),
            eps("2020-12-31", 6.0, "k21", "2021-02-01"), eps("2020-12-31", 1.0, "k25", "2025-02-01")]
    events = edgar.split_events(rows)
    assert [round(r, 6) for *_, r in events] == [0.5, round(1 / 3, 6)]
    assert infer_split_factors(rows)["2020-12-31"] == pytest.approx(1 / 6)
    # the product alone (no single splits seen) is still one split
    assert [round(r, 6) for *_, r in edgar.split_events(rows[-2:])] == [round(1 / 6, 6)]


def test_fixture_deck_six_for_one_split():
    s = build_snapshot("DECK", 910521, load("DECK"), sic=3021, name="DECK", fetched_at=FA)
    eps = _eps_years(s)
    assert s.split_factor == pytest.approx(1 / 6) and "splits: x0.166667 after 2024-05-24" in s.notes
    assert eps["2017"] == pytest.approx(0.03, abs=0.005) and eps["2022"] == pytest.approx(2.71, abs=0.005)
    assert eps["2019"] == pytest.approx(8.84 / 6, abs=0.005) and eps["2021"] == pytest.approx(13.47 / 6, abs=0.005)
    assert eps["2023"] == 3.23 and eps["2024"] == 4.86                      # restated by the 2025 10-K already
    assert all(eps[str(y)] < eps[str(y + 1)] * 3 for y in range(2018, 2026))   # no pre-split cliff left


# --- E2 (B1/B2): debt recipes that carry the current part ---

def test_debt_table_order_current_part_first_e2():
    debt = [edgar._cand_label(c) for c in CONCEPTS["debt"]]
    assert debt[:6] == ["LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings+CommercialPaper",
                        "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities+ShortTermBorrowings+CommercialPaper",
                        "DebtAndCapitalLeaseObligations",
                        "LongTermDebt+ShortTermBorrowings+CommercialPaper",
                        "DebtCurrent+LongTermDebtNoncurrent",
                        "LongTermDebtAndCapitalLeaseObligations+LongTermDebtAndCapitalLeaseObligationsCurrent"]
    # the noncurrent-only (d) never stands alone any more; the bare noncurrent tag sits after every total
    assert "LongTermDebtAndCapitalLeaseObligations" not in debt
    assert debt.index("LongTermDebtNoncurrent") > max(debt.index(lab) for lab in debt[:6])
    assert set(edgar.CURRENT_MAX) == {debt[0], debt[3], debt[5]} and edgar.LTD_EXCLUDES_CURRENT == {debt[3]}


def test_filer_total_outranks_the_noncurrent_tag_cls_lhx():
    # CLS: (d) 791.5M is noncurrent; ...IncludingCurrentMaturities 818.8M is the total
    g = _insts({"LongTermDebtAndCapitalLeaseObligations": {2023: 648.0, 2025: 791.5},
                "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": {2023: 675.3, 2025: 818.8}})
    s = resolve_series(g, {}, "debt")
    assert s.values == {"2023-12-31": 675.3, "2025-12-31": 818.8}
    assert s.tag.startswith("LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities")
    # LHX FY2023: the total plus 1,602M short-term borrowings (outside it by definition)
    g = _insts({"LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": {2023: 11523.0, 2025: 11116.0},
                "ShortTermBorrowings": {2023: 1602.0, 2025: 0.0},
                "LongTermDebtAndCapitalLeaseObligations": {2023: 11160.0, 2025: 10443.0},
                "LongTermDebtAndCapitalLeaseObligationsCurrent": {2023: 363.0, 2025: 673.0}})
    assert resolve_series(g, {}, "debt").values == {"2023-12-31": 13125.0, "2025-12-31": 11116.0}
    # the total alone (GM) still resolves on its own
    s = resolve_series(_insts({"LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": {2024: 7.0, 2025: 9.0}}),
                       {}, "debt")
    assert s.values == {"2024-12-31": 7.0, "2025-12-31": 9.0}
    # (d) with no current part anywhere is still debt (noncurrent only), through the (d)+current recipe
    s = resolve_series(_insts({"LongTermDebtAndCapitalLeaseObligations": {2025: 50.0}}), {}, "debt")
    assert s.values == {"2025-12-31": 50.0}


def test_filer_totals_fill_before_long_term_debt_cop():
    # COP: the total ...IncludingCurrentMaturities from 2020, DebtAndCapitalLeaseObligations to 2020,
    # and a LongTermDebt series (2017-2021) larger than both totals: the totals fill first
    g = _insts({"LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": {2020: 15369.0, 2025: 23400.0},
                "DebtAndCapitalLeaseObligations": {2016: 27275.0, 2017: 19703.0, 2018: 14968.0, 2020: 15400.0},
                "LongTermDebt": {2017: 22435.0, 2018: 16147.0, 2021: 22797.0},
                "LongTermDebtAndCapitalLeaseObligations": {y: 14000.0 for y in range(2016, 2026)}})
    s = resolve_series(g, {}, "debt")
    assert s.values["2017-12-31"] == 19703.0 and s.values["2018-12-31"] == 14968.0 and s.values["2016-12-31"] == 27275.0
    assert s.values["2021-12-31"] == 22797.0                                  # only LongTermDebt has 2021
    assert s.values["2025-12-31"] == 23400.0


def test_debt_current_replaces_smaller_current_parts_qcom_pypl():
    # QCOM FY2026: DebtCurrent 2,492 = current maturities 1,993 + ~499 commercial paper with no instant
    g = _insts({"LongTermDebtNoncurrent": {2025: 14811.0, 2026: 12620.0}, "LongTermDebtCurrent": {2025: 0.0, 2026: 1993.0},
                "DebtCurrent": {2025: 0.0, 2026: 2492.0}})
    s = resolve_series(g, {}, "debt")
    assert s.values == {"2025-12-31": 14811.0, "2026-12-31": 15112.0} and s.current_max == ["2026-12-31"]
    assert s.tag == "LongTermDebtNoncurrent+LongTermDebtCurrent+ShortTermBorrowings+CommercialPaper"
    # never DebtCurrent ON TOP of the parts it already holds (no double count)
    g["CommercialPaper"] = _insts({"x": {2026: 499.0}})["x"]
    s = resolve_series(g, {}, "debt")
    assert s.values["2026-12-31"] == 15112.0 and s.current_max == []          # 12,620 + 1,993 + 499 = 15,112
    # DebtCurrent smaller than the parts (AAPL-style): the parts stand
    g["CommercialPaper"] = _insts({"x": {2026: 1000.0}})["x"]
    s = resolve_series(g, {}, "debt")
    assert s.values["2026-12-31"] == 15613.0 and s.current_max == []
    # PYPL 2026-09-30 10-Q: DebtCurrent 2,533 vs LongTermDebtCurrent 2,333 -> TTM 13,472 (Finnhub 13,482)
    g = _ni(range(2024, 2026))
    g.update({"LongTermDebtNoncurrent": _rows_node([("2025-12-31", 9987.0, "k25", "10-K", "2026-02-01"),
                                                    ("2026-09-30", 10939.0, "q3", "10-Q", "2026-11-01")]),
              "LongTermDebtCurrent": _rows_node([("2025-12-31", 1396.0, "k25", "10-K", "2026-02-01"),
                                                 ("2026-09-30", 2333.0, "q3", "10-Q", "2026-11-01")]),
              "DebtCurrent": _rows_node([("2025-12-31", 1596.0, "k25", "10-K", "2026-02-01"),
                                         ("2026-09-30", 2533.0, "q3", "10-Q", "2026-11-01")])})
    snap_ = _snap_of(g)
    assert snap_.annual["2025-12-31"]["debt"] == 11583.0
    assert snap_.ttm["debt"] == 13472.0 and snap_.balance_ends["debt"] == "2026-09-30"
    assert "debt: 2025 current part from DebtCurrent (total current debt), larger than the recipe's current parts" in snap_.notes
    assert any(n.startswith("debt: current part at 2026-09-30 from DebtCurrent") for n in snap_.notes)


def _adbe(with_combined=True, trade_off=False, ltdn_at_fy2019=False):
    g = _ni(range(2019, 2026))
    ltd = [("2019-12-31", 989.0, "k19", "10-K", "2020-01-20"), ("2024-12-31", 4129.0, "k24", "10-K", "2025-01-20"),
           ("2025-12-31", 6210.0, "k25", "10-K", "2026-01-20"), ("2026-09-30", 4766.0, "q3", "10-Q", "2026-10-01")]
    dc = [("2019-12-31", 3149.0, "k19", "10-K", "2020-01-20"), ("2024-12-31", 1499.0, "k24", "10-K", "2025-01-20"),
          ("2025-12-31", 0.0, "k25", "10-K", "2026-01-20"), ("2026-09-30", 1597.0, "q3", "10-Q", "2026-10-01")]
    if trade_off:   # 2019 Q1 -> Q2: a 2.25B maturity moves from LongTermDebt to DebtCurrent
        ltd += [("2019-03-31", 3237.0, "q1", "10-Q", "2019-04-20"), ("2019-06-30", 988.0, "q2", "10-Q", "2019-07-20")]
        dc += [("2019-03-31", 893.0, "q1", "10-Q", "2019-04-20"), ("2019-06-30", 3146.0, "q2", "10-Q", "2019-07-20")]
    g.update({"LongTermDebt": _rows_node(ltd), "DebtCurrent": _rows_node(dc)})
    if with_combined:
        g["DebtLongtermAndShorttermCombinedAmount"] = _rows_node([("2019-12-31", 4138.0, "k19", "10-K", "2020-01-20")])
    if ltdn_at_fy2019:
        g["LongTermDebtNoncurrent"] = _rows_node([("2019-12-31", 989.0, "k19", "10-K", "2020-01-20")])
    return g


def test_long_term_debt_plus_debt_current_only_with_evidence_adbe():
    # ADBE tags LongTermDebt as its noncurrent line: FY2019 989 + DebtCurrent 3,149 = its own combined
    # 4,138; Finnhub's D/E reproduces LongTermDebt + DebtCurrent = 6,363 at the latest 10-Q
    g = _adbe()
    assert edgar.ltd_excludes_current(g)
    s = _snap_of(g)
    assert s.annual["2024-12-31"]["debt"] == 5628.0 and s.annual["2025-12-31"]["debt"] == 6210.0
    assert s.ttm["debt"] == 6363.0 and s.balance_ends["debt"] == "2026-09-30"
    assert s.tags["debt"] == "LongTermDebt+ShortTermBorrowings+CommercialPaper"
    assert any(n.startswith("debt: 2019, 2024 current part from DebtCurrent") for n in s.notes)
    # no evidence (no combined total, no trade-off): LongTermDebt is taken as defined (incl. current)
    g = _adbe(with_combined=False)
    assert not edgar.ltd_excludes_current(g)
    s = _snap_of(g)
    assert s.annual["2024-12-31"]["debt"] == 4129.0 and s.ttm["debt"] == 4766.0
    # the trade-off between two quarter-ends is evidence too
    assert edgar.ltd_excludes_current(_adbe(with_combined=False, trade_off=True))
    assert _snap_of(_adbe(with_combined=False, trade_off=True)).ttm["debt"] == 6363.0
    # counter-evidence: LongTermDebt alone IS the combined total -> it includes the current part
    g = _adbe()
    g["DebtLongtermAndShorttermCombinedAmount"] = _rows_node([("2019-12-31", 989.0, "k19", "10-K", "2020-01-20")])
    assert not edgar.ltd_excludes_current(g) and _snap_of(g).ttm["debt"] == 4766.0
    # an instant with LongTermDebtNoncurrent is (a)/(e)'s business: never LongTermDebt + DebtCurrent there
    s = resolve_series(_adbe(ltdn_at_fy2019=True), {}, "debt")
    assert "2019-12-31" not in s.current_max


def test_noncurrent_d_pairs_the_filers_current_debt_xom():
    # XOM from 2018: (d) is the noncurrent line, DebtCurrent (notes and loans payable, incl. commercial
    # paper) the current one; the 2026-09-30 10-Q has no ...Current part, DebtCurrent covers it
    g = _ni(range(2017, 2026))
    g.update({"LongTermDebtAndCapitalLeaseObligations": _rows_node(
                  [("2018-12-31", 20538.0, "k18", "10-K", "2019-02-27"), ("2025-12-31", 34241.0, "k25", "10-K", "2026-02-18"),
                   ("2026-09-30", 29727.0, "q3", "10-Q", "2026-11-01")]),
              "LongTermDebtAndCapitalLeaseObligationsCurrent": _rows_node(
                  [("2018-12-31", 4070.0, "k18", "10-K", "2019-02-27"), ("2025-12-31", 6234.0, "k25", "10-K", "2026-02-18")]),
              "DebtCurrent": _rows_node(
                  [("2018-12-31", 17258.0, "k18", "10-K", "2019-02-27"), ("2025-12-31", 9296.0, "k25", "10-K", "2026-02-18"),
                   ("2026-09-30", 4523.0, "q3", "10-Q", "2026-11-01")])})
    s = _snap_of(g)
    assert s.annual["2018-12-31"]["debt"] == 37796.0 and s.annual["2025-12-31"]["debt"] == 43537.0
    assert s.ttm["debt"] == 34250.0 and s.balance_ends["debt"] == "2026-09-30"
    # without DebtCurrent at the 10-Q the recipe still dates back to the annual end (VZ, unchanged)
    g["DebtCurrent"] = _rows_node([("2018-12-31", 17258.0, "k18", "10-K", "2019-02-27"),
                                   ("2025-12-31", 9296.0, "k25", "10-K", "2026-02-18")])
    s = _snap_of(g)
    assert s.ttm["debt"] == 43537.0 and s.balance_ends["debt"] == "2025-12-31"


def test_optional_part_at_an_explicit_zero_does_not_date_the_recipe_back_lhx():
    # LHX: ShortTermBorrowings 0 at FY2025, untagged at the later 10-Qs -> the fresh 10-Q balance stands
    g = _ni(range(2024, 2026))
    g.update({"LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities": _rows_node(
                  [("2025-12-31", 11116.0, "k25", "10-K", "2026-02-01"), ("2026-09-30", 10996.0, "q3", "10-Q", "2026-11-01")]),
              "ShortTermBorrowings": _rows_node([("2025-12-31", 0.0, "k25", "10-K", "2026-02-01")])})
    s = _snap_of(g)
    assert s.ttm["debt"] == 10996.0 and s.balance_ends["debt"] == "2026-09-30"
    assert not any("recipe dated at the annual end" in n for n in s.notes)


# --- E3 (B3): a current part copying the noncurrent part ---

def test_current_part_equal_to_the_noncurrent_part_in_one_filing_is_skipped_xom_2010():
    g = _ni(range(2010, 2012))
    g.update({"LongTermDebtAndCapitalLeaseObligations": _rows_node(
                  [("2010-12-31", 12227.0, "k10", "10-K", "2011-02-25"), ("2010-12-31", 12227.0, "q1", "10-Q", "2011-05-05"),
                   ("2011-12-31", 9322.0, "k11", "10-K", "2012-02-24")]),
              "LongTermDebtAndCapitalLeaseObligationsCurrent": _rows_node(
                  [("2010-12-31", 12227.0, "q1", "10-Q", "2011-05-05")])})
    s = _snap_of(g)
    assert s.annual["2010-12-31"]["debt"] == 12227.0                          # not 24,454
    assert ("debt: 2010 LongTermDebtAndCapitalLeaseObligationsCurrent equals the noncurrent part in the same "
            "filing (filer error), not added") in s.notes
    # a genuine current part (345M) is added
    g["LongTermDebtAndCapitalLeaseObligationsCurrent"] = _rows_node([("2010-12-31", 345.0, "k10", "10-K", "2011-02-25")])
    s = _snap_of(g)
    assert s.annual["2010-12-31"]["debt"] == 12572.0 and not any("filer error" in n for n in s.notes)
    # an equal value from a filing whose noncurrent value differs is not a copy
    g["LongTermDebtAndCapitalLeaseObligationsCurrent"] = _rows_node([("2010-12-31", 9322.0, "k11", "10-K", "2012-02-24")])
    assert _snap_of(g).annual["2010-12-31"]["debt"] == 12227.0 + 9322.0


# --- E4 (B5), E5 (J9) ---

def test_dividends_ordinary_tag_qcom():
    g = _flows({"PaymentsOfDividends": {2011: 1346.0, 2012: 1583.0, 2013: 2055.0},
                "PaymentsOfOrdinaryDividends": {**{2013: 2055.0}, **{y: 3000.0 + y for y in range(2014, 2027)}}})
    s = resolve_series(g, {}, "dividends")
    assert s.tag == "PaymentsOfOrdinaryDividends" and len(s.values) == 16 and s.values["2011-12-31"] == 1346.0
    assert "PaymentsOfOrdinaryDividends" in TAGS_BY_NAMESPACE["us-gaap"]


def test_sales_revenue_net_joins_the_larger_of_group_lhx():
    g = _flows({"Revenues": {2010: 102.0, 2011: 189.0, 2012: 374.0},
                "SalesRevenueNet": {2010: 4725.0, 2011: 5418.0, 2012: 5451.0}})
    s = resolve_series(g, {}, "revenue")
    assert s.values == {"2010-12-31": 4725.0, "2011-12-31": 5418.0, "2012-12-31": 5451.0} and not s.tag_break
    assert s.tag == "SalesRevenueNet" and "SalesRevenueNet" in edgar.LARGER_OF["revenue"]


# --- E6 (B7): TTM EPS for a filer with no EPS tag and no diluted shares ---

def _hsy_like(with_eps_tag=False):
    g = _flows({"NetIncomeLoss": {2024: 2200.0, 2025: 883.3}})
    g["NetIncomeLoss"]["units"]["USD"] += [
        {"start": "2026-01-01", "end": "2026-09-30", "val": 1473.6, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"},
        {"start": "2025-01-01", "end": "2025-09-30", "val": 563.3, "accn": "q3", "form": "10-Q", "filed": "2026-11-01"}]
    g["CommonStockSharesOutstanding"] = {"units": {"shares": [
        {"end": "2024-12-31", "val": 202.0, "accn": "a2024", "form": "10-K", "filed": "2025-02-01"},
        {"end": "2025-12-31", "val": 202.84, "accn": "a2025", "form": "10-K", "filed": "2026-02-01"}]}}
    if with_eps_tag:
        g.update(_per_share({"EarningsPerShareDiluted": {2024: 10.9, 2025: 4.35}}))
    return g


def test_ttm_eps_derived_from_ni_over_period_end_shares_hsy():
    from long_term.metrics import EPS_TTM_DERIVED_NOTE
    s = _snap_of(_hsy_like())
    assert s.tags["eps"] == "derived" and _eps_years(s)["2025"] == pytest.approx(883.3 / 202.84)
    assert s.ttm["eps"] == pytest.approx(1793.6 / 202.84) and s.ttm["eps"] == pytest.approx(8.84, abs=0.005)
    assert s.ttm_ends["eps"] == ("2026-09-30", False)
    note = [n for n in s.notes if n.startswith(EPS_TTM_DERIVED_NOTE)]
    assert note == ["eps TTM derived from NI/period-end shares (TTM 2026-09-30, shares at 2025-12-31)"]
    assert not any(n.startswith("eps: TTM") for n in s.notes)
    # a filer whose latest EPS comes from the tag keeps the tag's TTM (no derived TTM, no note)
    s = _snap_of(_hsy_like(with_eps_tag=True))
    assert s.ttm["eps"] == 4.35 and not any(n.startswith(EPS_TTM_DERIVED_NOTE) for n in s.notes)
    # nor one whose latest derived EPS used the diluted weighted count
    g = _hsy_like()
    g.update(_flows({"WeightedAverageNumberOfDilutedSharesOutstanding": {2024: 203.0, 2025: 203.0}}, "shares"))
    assert not any(n.startswith(EPS_TTM_DERIVED_NOTE) for n in _snap_of(g).notes)


def test_fixture_hsy_ttm_eps_derived():
    s = nsnap("HSY")
    assert s.ttm["eps"] == pytest.approx(8.84, abs=0.01) and s.ttm_ends["eps"] == ("2026-09-27", False)
    assert "eps TTM derived from NI/period-end shares (TTM 2026-09-27, shares at 2025-12-31)" in s.notes


# --- E7 (B6b): tags.debt names the recipe of the latest year ---

def test_debt_tag_names_the_recipe_that_supplied_the_latest_year_mar():
    g = _insts({"LongTermDebt": {y: 23.0 for y in range(2019, 2026)},
                "LongTermDebtAndCapitalLeaseObligations": {y: 15000.0 for y in range(2019, 2026)},
                "LongTermDebtAndCapitalLeaseObligationsCurrent": {2025: 1209.0}})
    assert resolve_series(g, {}, "debt").tag == "LongTermDebt+ShortTermBorrowings+CommercialPaper"   # primary
    g.update(_flows({"NetIncomeLoss": {y: 5.0 for y in range(2019, 2026)}}))
    s = _snap_of(g)
    assert s.tags["debt"] == "LongTermDebtAndCapitalLeaseObligations+LongTermDebtAndCapitalLeaseObligationsCurrent"
    # a carried 0 never names the series
    g = _flows({"NetIncomeLoss": {y: 5.0 for y in range(2022, 2027)}})
    g.update(_insts({"ConvertibleDebtNoncurrent": {2022: 1136.5, 2023: 1139.9, 2024: 1143.3, 2025: 0.0}}))
    assert _snap_of(g).tags["debt"] == "ConvertibleDebtNoncurrent+ConvertibleDebtCurrent"
