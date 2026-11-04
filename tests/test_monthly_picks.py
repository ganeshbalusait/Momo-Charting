"""Monthly Picks rule (momx/monthly_picks.py) on synthetic daily prices."""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from momx import monthly_picks as mp

DATES = pd.bdate_range("2020-01-01", "2023-12-29")


def _frame(prices, volume=1_000_000, dates=DATES, tz=None):
    # Real daily bars are stamped at midnight ET (Alpaca: 05:00/04:00 UTC).
    ts = dates.tz_localize("America/New_York").tz_convert(tz) if tz else dates
    prices = np.asarray(prices, dtype=float)
    return pd.DataFrame({
        "timestamp": ts,
        "open": prices, "high": prices * 1.001, "low": prices * 0.999,
        "close": prices, "volume": float(volume),
    })


def _drift(daily, start=50.0, noise=0.0, seed=0, n=len(DATES)):
    rng = np.random.default_rng(seed)
    rets = daily + rng.normal(0, noise, n) if noise else np.full(n, daily)
    return start * np.cumprod(1 + rets)


@pytest.fixture()
def universe():
    """40 stocks: S00 strongest drift ... S39 weakest; two sectors."""
    bars, sectors = {}, {}
    for i in range(40):
        sym = f"S{i:02d}"
        bars[sym] = _frame(_drift(0.0015 - i * 0.00005, noise=0.004, seed=i))
        sectors[sym] = "Tech" if i % 2 == 0 else "Energy"
    bars["SPY"] = _frame(_drift(0.0004))
    return bars, sectors


def test_strongest_trends_rank_first_and_benchmark_is_excluded(universe):
    bars, sectors = universe
    panels = mp.build_panels(bars)
    scored = mp.score_on(DATES[-1], panels, sectors, exclude=["SPY"])
    assert "SPY" not in scored.index
    assert len(scored) == 40
    top = list(scored.index[:10])
    assert sum(int(s[1:]) < 15 for s in top) >= 8, top
    assert scored["rank"].tolist() == list(range(1, 41))
    assert scored["score"].between(0, 1).all()


def test_score_uses_no_future_data(universe):
    bars, sectors = universe
    cut = DATES[600]
    base = mp.score_on(cut, mp.build_panels(bars), sectors)
    shocked = {s: f.copy() for s, f in bars.items()}
    after = shocked["S39"]["timestamp"] > cut
    for col in ("open", "high", "low", "close"):
        shocked["S39"].loc[after, col] *= 50     # weakest stock explodes AFTER the date
    again = mp.score_on(cut, mp.build_panels(shocked), sectors)
    pd.testing.assert_series_equal(base["score"], again["score"])


def test_eligibility_filters():
    bars = {
        "OK": _frame(_drift(0.001)),
        "PENNY": _frame(_drift(0.001, start=2.0)),
        "THIN": _frame(_drift(0.001), volume=1_000),       # ~$50k/day
        "LATE": _frame(_drift(0.001)),
        "NEW": _frame(_drift(0.001)[-100:], dates=DATES[-100:]),
    }
    added = {"LATE": DATES[-1] + pd.Timedelta(days=30)}
    scored = mp.score_on(DATES[-1], mp.build_panels(bars), {}, added)
    assert list(scored.index) == ["OK"]


def test_too_early_date_returns_empty_instead_of_wrapping(universe):
    bars, sectors = universe
    assert mp.score_on(DATES[100], mp.build_panels(bars), sectors).empty


def test_hold_buffer_keeps_ranks_inside_keep_rank():
    scored = pd.DataFrame({"rank": range(1, 61)}, index=[f"X{i}" for i in range(1, 61)])
    cfg = mp.PickConfig(top_n=5, keep_rank=10)
    # X8 still inside top 10 -> kept; X30 fell out -> replaced; gone symbol dropped
    picks = mp.select(["X8", "X30", "GONE"], scored, cfg)
    assert picks == ["X1", "X2", "X3", "X4", "X8"]
    assert mp.select([], scored, cfg) == ["X1", "X2", "X3", "X4", "X5"]


def test_build_panels_aligns_timezones_and_drops_duplicates():
    a = _frame(np.arange(1, len(DATES) + 1), tz="America/New_York")
    b = _frame(np.arange(1, len(DATES) + 1), tz="UTC")
    b = pd.concat([b, b.iloc[[5]]])                     # Schwab-style repeated bar
    panels = mp.build_panels({"A": a, "B": b})
    assert panels["close"].index.is_unique
    assert len(panels["close"]) == len(DATES)
    assert panels["close"]["A"].notna().all() and panels["close"]["B"].notna().all()


def test_month_end_dates():
    ends = mp.month_end_dates(DATES)
    assert ends[0] == pd.Timestamp("2020-01-31")
    assert ends[1] == pd.Timestamp("2020-02-28")
    assert len(ends) == 48


def test_backtest_returns_costs_and_reasons(universe):
    bars, sectors = universe
    cfg = mp.PickConfig(top_n=5, keep_rank=10, cost_bps=10)
    bt = mp.backtest(mp.build_panels(bars), sectors, benchmark="SPY", config=cfg)
    assert bt["start"] == "2021-01"        # first rebalance 2020-12-31 (a year of history), held into Jan
    months = bt["months"]
    assert len(months) == 36
    # strongest-drift names win: picks beat their own universe and SPY
    assert bt["stats"]["picks"]["cagr"] > bt["stats"]["universe"]["cagr"] > bt["stats"]["benchmark"]["cagr"]
    # every add has a reason, every drop explains its rank
    adds = [t for t in bt["trades"] if t["action"] == "add"]
    assert len(adds) >= 5 and all(t["reason"].startswith("Rank #") for t in adds)
    assert all("Rank fell" in t["reason"] or "No longer eligible" in t["reason"]
               for t in bt["trades"] if t["action"] == "drop")
    # first month pays one side on all five names
    first = months[0]
    close = mp.build_panels(bars)["close"]
    t0, t1 = pd.Timestamp("2020-12-31"), pd.Timestamp(first["date"])
    first_picks = [t["symbol"] for t in bt["trades"] if t["date"] == "2020-12-31" and t["action"] == "add"]
    gross = np.mean([close[s][t1] / close[s][t0] - 1 for s in first_picks])
    assert first["picks"] == pytest.approx(gross - 0.001, abs=1e-4)
    assert first["benchmark"] == pytest.approx(close["SPY"][t1] / close["SPY"][t0] - 1, abs=1e-4)
    json.dumps(bt)  # serialisable


def test_delisted_stock_exits_at_last_close():
    close = pd.DataFrame({"X": [10.0, 11.0, 12.0, np.nan, np.nan]}, index=DATES[:5])
    assert mp._period_return(close, "X", DATES[0], DATES[4]) == pytest.approx(0.2)


def test_current_picks_marks_keep_new_and_dropped(universe):
    bars, sectors = universe
    panels = mp.build_panels(bars)
    cfg = mp.PickConfig(top_n=5, keep_rank=10)
    first = mp.current_picks(panels, sectors, config=cfg)
    assert {p["status"] for p in first["picks"]} == {"pick"}
    previous = [p["symbol"] for p in first["picks"]][:4] + ["S39"]
    second = mp.current_picks(panels, sectors, previous=previous, config=cfg)
    statuses = {p["symbol"]: p["status"] for p in second["picks"]}
    assert sum(v == "keep" for v in statuses.values()) == 4
    assert sum(v == "new" for v in statuses.values()) == 1
    assert [d["symbol"] for d in second["dropped"]] == ["S39"]
    assert "Rank fell" in second["dropped"][0]["reason"]


def test_script_writes_report(tmp_path, monkeypatch, universe):
    import scripts.monthly_picks as script

    bars, sectors = universe
    uni = tmp_path / "u.csv"
    uni.write_text("symbol,sector,added\n" + "".join(f"{s},{sec},\n" for s, sec in sectors.items()), encoding="utf-8")
    monkeypatch.setattr(script, "OUT", tmp_path / "out")
    monkeypatch.setattr(script, "load_bars", lambda symbols, years, refresh: bars)
    assert script.main(["--universe", str(uni), "--top", "5", "--keep", "10"]) == 0
    report = (tmp_path / "out" / "report.md").read_text(encoding="utf-8")
    assert "Back-test" in report and "Picks as of 2023-12-29" in report
    assert "survivorship" in report
    saved = json.loads((tmp_path / "out" / "current_picks.json").read_text(encoding="utf-8"))
    assert len(saved["picks"]) == 5
    # second run: same picks are marked keep
    assert script.main(["--universe", str(uni), "--top", "5", "--keep", "10", "--picks-only"]) == 0
    again = json.loads((tmp_path / "out" / "current_picks.json").read_text(encoding="utf-8"))
    assert {p["status"] for p in again["picks"]} == {"keep"}
