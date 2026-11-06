"""Long Term: EDGAR fact selection, tag resolution, units and splits. No network.

Fixtures are trimmed real SEC company-facts files (tests/fixtures/long_term).
"""
import json
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
    assert any(n.startswith("debt: ") and " from recipe (b) LongTermDebt+" in n for n in s.notes)


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
    assert set(s.ttm_ends) == {c for c in ("revenue", "net_income", "cfo", "capex", "sbc", "interest", "op_income")
                               if c in s.ttm}
    assert "revenue: TTM = FY" in s.notes


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
