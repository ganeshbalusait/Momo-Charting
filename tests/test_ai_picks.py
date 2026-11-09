"""Unit tests: AI Picks point-in-time finances, scoring and portfolio rules."""
from datetime import date

import pandas as pd
import pytest

from ai_picks import model, pit


def _flow(rank, start, end, val, filed):
    return (rank, date.fromisoformat(start), date.fromisoformat(end), val, date.fromisoformat(filed))


def _inst(end, val, filed, rank=0):
    return (rank, None, date.fromisoformat(end), val, date.fromisoformat(filed))


REVENUE = [
    _flow(0, "2022-01-01", "2022-12-31", 100.0, "2023-02-20"),  # FY22
    _flow(0, "2022-01-01", "2022-06-30", 45.0, "2022-08-05"),   # H1-22
    _flow(0, "2023-01-01", "2023-06-30", 60.0, "2023-08-04"),   # H1-23
    _flow(0, "2023-01-01", "2023-12-31", 130.0, "2024-02-21"),  # FY23
]


def test_ttm_uses_annual_plus_ytd_minus_prior_ytd():
    facts = pit._dedupe(REVENUE)
    assert pit.ttm_at(facts, date(2023, 6, 30)) == pytest.approx(100 + 60 - 45)
    assert pit.ttm_at(facts, date(2023, 12, 31)) == 130.0


def test_snapshot_never_sees_a_number_filed_later():
    snap = pit.snapshot({"revenue": REVENUE}, date(2023, 8, 3))  # day before H1-23 was filed
    assert snap["period_end"] == date(2022, 12, 31)
    assert snap["revenue"] == 100.0
    after = pit.snapshot({"revenue": REVENUE}, date(2023, 8, 4))
    assert after["revenue"] == pytest.approx(115.0)


def test_restatement_counts_only_from_its_filing_day():
    restated = REVENUE + [_flow(0, "2022-01-01", "2022-12-31", 90.0, "2023-09-01")]
    before = pit.snapshot({"revenue": restated}, date(2023, 8, 10))
    after = pit.snapshot({"revenue": restated}, date(2023, 9, 2))
    assert before["revenue"] == pytest.approx(115.0)
    assert after["revenue"] == pytest.approx(105.0)


def test_stale_finances_are_dropped():
    assert pit.snapshot({"revenue": REVENUE[:1]}, date(2024, 1, 15)) is None


def test_debt_falls_back_to_current_plus_noncurrent():
    concepts = {"revenue": REVENUE, "debt_noncurrent": [_inst("2023-12-31", 50.0, "2024-02-21")],
                "debt_current": [_inst("2023-12-31", 5.0, "2024-02-21")],
                "cash": [_inst("2023-12-31", 20.0, "2024-02-21")]}
    snap = pit.snapshot(concepts, date(2024, 3, 1))
    assert snap["debt"] == 55.0 and snap["cash"] == 20.0


def test_dual_class_company_falls_back_to_diluted_share_count():
    concepts = {"revenue": REVENUE,
                "shares_wavg": [_flow(0, "2023-10-01", "2023-12-31", 2_500.0, "2024-02-21")]}
    snap = pit.snapshot(concepts, date(2024, 3, 1))
    assert snap["shares"] == 2_500.0 and snap["shares_date"] == date(2023, 12, 31)
    concepts["shares"] = [_inst("2024-02-15", 2_400.0, "2024-02-21")]
    assert pit.snapshot(concepts, date(2024, 3, 1))["shares"] == 2_400.0


def test_normalize_name_matches_alpaca_to_sec():
    assert model.normalize_name("Twitter, Inc. Common Stock") == model.normalize_name("TWITTER, INC.")
    assert model.normalize_name("SVB Financial Group Common Stock") == "svb financial"


def test_metrics_losing_ebitda_is_worst_leverage():
    snap = {"op_income": -10.0, "dna": 2.0, "debt": 100.0, "cash": 0.0, "revenue": 50.0}
    m = model.metrics(snap, mcap=200.0, mom_12_1=0.1, mom_6=0.05)
    assert m["net_debt_ebitda"] == float("inf")
    assert m["op_margin"] == pytest.approx(-0.2)


def _universe():
    rows = []
    for i in range(10):
        row = {"symbol": f"S{i}"}
        for metric, (_sub, higher) in model.METRICS.items():
            row[metric] = float(i) if higher else float(10 - i)
        rows.append(row)
    return pd.DataFrame(rows)


def test_score_best_on_every_metric_ranks_first():
    scored = model.score(_universe()).sort_values("total", ascending=False)
    assert scored.iloc[0]["symbol"] == "S9"
    assert scored.iloc[0]["total"] == pytest.approx(100.0)
    assert scored["total"].between(0, 100).all()


def test_score_needs_three_subscores():
    df = _universe()
    df.loc[0, ["ebitda_ev", "earn_yield", "fcf_yield", "rev_growth", "eps_growth"]] = None
    assert pd.isna(model.score(df).set_index("symbol").loc["S0", "total"])


def test_pick_keeps_holdings_inside_keep_rank():
    scored = pd.DataFrame({"symbol": [f"S{i}" for i in range(50)], "total": [100 - i for i in range(50)]})
    picks = model.pick(scored, held=["S30", "S45"], top_n=20, keep_rank=40)
    assert "S30" in picks and "S45" not in picks
    assert len(picks) == 20


def test_holding_return_buyout_vs_failure():
    days = pd.bdate_range("2023-01-02", periods=120)
    buyout = pd.Series([10.0] * 100 + [None] * 20, index=days, dtype=float)
    assert model.holding_return(buyout, days[90], days[119]) == pytest.approx(0.0)
    crash = pd.Series([100.0] * 60 + list(range(40, 0, -1)) + [None] * 20, index=days, dtype=float)
    assert model.holding_return(crash, days[90], days[119]) == -1.0


def test_turnover_and_stats():
    assert model.turnover({"A": 0.5, "B": 0.5}, {"A": 0.5, "C": 0.5}) == pytest.approx(1.0)
    assert model.drawdown(pd.Series([1.0, 1.2, 0.9, 1.3])) == pytest.approx(-0.25)
    assert model.cagr(pd.Series([0.01] * 12)) == pytest.approx(1.01 ** 12 - 1)


def _quality_frame():
    base = {"mcap": 5e9, "profit_years": 5, "debt_equity": 0.5, "roe_5y": 0.2, "roc": 0.3,
            "ni_growth_5y": 0.1, "ebit_ev": 0.08}
    rows = [dict(base, symbol=f"Q{i}", roe_5y=0.1 + i / 100, roc=0.2 + i / 100, ebit_ev=0.05 + i / 1000)
            for i in range(10)]
    rows.append(dict(base, symbol="LOSER", profit_years=4, roe_5y=0.9))  # one losing year
    rows.append(dict(base, symbol="TINY", mcap=5e8, roe_5y=0.9))        # too small
    return pd.DataFrame(rows)


def test_buffett_needs_five_profitable_years_and_size():
    scored = model.score_buffett(_quality_frame()).set_index("symbol")
    assert pd.isna(scored.loc["LOSER", "buffett"]) and pd.isna(scored.loc["TINY", "buffett"])
    assert scored["buffett"].idxmax() == "Q9"


def test_magic_formula_ranks_return_on_capital_and_cheapness():
    frame = _quality_frame()
    frame.loc[frame["symbol"] == "Q0", "ebit_ev"] = -0.01  # losing money: excluded
    scored = model.score_magic(frame).set_index("symbol")
    # no profit-history rule here: LOSER has the best return on capital and price
    assert pd.isna(scored.loc["Q0", "magic"]) and scored["magic"].idxmax() == "LOSER"
    assert pd.isna(scored.loc["TINY", "magic"])  # under $1B
    assert scored.loc["Q9", "magic"] > scored.loc["Q1", "magic"]


def test_return_on_capital_metrics():
    snap = {"op_income": 30.0, "dna": 0.0, "revenue": 100.0, "assets_current": 50.0, "liab_current": 30.0,
            "ppe": 80.0, "equity": 200.0, "debt": 50.0, "cash": 10.0, "ni_years": [16.0, 12, 10, 9, 8]}
    m = model.metrics(snap, mcap=400.0, mom_12_1=0.0, mom_6=0.0)
    assert m["roc"] == pytest.approx(0.3)
    assert m["ebit_ev"] == pytest.approx(30 / 440)
    assert m["debt_equity"] == pytest.approx(0.25)
    assert m["profit_years"] == 5 and m["ni_growth_5y"] == pytest.approx(2 ** 0.25 - 1)
