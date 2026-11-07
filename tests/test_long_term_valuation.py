"""Long Term: sticker price, Buffett value, Safety Buy, payback, price guard and signals. No network.

Synthetic Metrics (every check passes, ten years, ROIC 25%) drive the signal truth table; one field
is flipped per case. Real fixtures (tests/fixtures/long_term) check the whole path end to end.
"""
import copy
import dataclasses
import json
import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from long_term.edgar import build_snapshot
from long_term.finnhub import fetch_metrics
from long_term.metrics import CHECK_KEYS, LABELS, THRESHOLDS, Check, Metrics, compute_metrics
from long_term.valuation import (
    Valuation,
    buffett_value,
    fair_value,
    payback_years,
    price_guard,
    signal,
    sticker_price,
    valuation,
)

ET = ZoneInfo("America/New_York")
FIX = Path(__file__).parent / "fixtures" / "long_term"
SYMBOLS = ["AAPL", "ASML", "TSM", "HOOD", "MCD", "HD", "JPM", "GOOGL", "NVDA", "CRWV"]
CIKS = {"AAPL": 320193, "ASML": 937966, "TSM": 1046179, "HOOD": 1783879, "MCD": 63908,
        "HD": 354950, "JPM": 19617, "GOOGL": 1652044, "NVDA": 1045810, "CRWV": 1769628}
FA = "2026-11-06T03:00:00-05:00"
TODAY = date(2026, 11, 6)
VOCAB = {"BUY", "WAIT", "AVOID", "NO_PRICE", "ETF", "NO_FILINGS", "STALE", "SHORT_HISTORY", "CHECK_DATA"}
KEYS = {"signal", "leaps", "reason", "buy_below", "leaps_below", "buy_zone_pct", "safety_buy", "quality"}


def ms(dt):
    return dt.timestamp() * 1000.0


# ---------- real fixtures ----------

@lru_cache(maxsize=None)
def _fh(sym):
    doc = json.loads((FIX / f"finnhub_{sym}.json").read_text(encoding="utf-8"))
    return fetch_metrics(sym, "k", get=lambda u, t: doc, fetched_at=FA)


def fixture_price(sym):
    x = _fh(sym)
    return x.eps_ttm * x.pe_ttm if x and x.eps_ttm and x.pe_ttm else None


@lru_cache(maxsize=None)
def _metrics(sym, price):
    sub = json.loads((FIX / f"submissions_{sym}.json").read_text(encoding="utf-8"))
    facts = json.loads((FIX / f"companyfacts_{sym}.json").read_text(encoding="utf-8"))
    snap = build_snapshot(sym, CIKS[sym], facts, sic=int(sub["sic"]), name=sub["name"], fetched_at=FA)
    return compute_metrics(snap, copy.deepcopy(_fh(sym)), today=TODAY, is_etf=False, price=price)


def real(sym, price="fixture"):
    p = fixture_price(sym) if price == "fixture" else price
    return copy.deepcopy(_metrics(sym, p)), copy.deepcopy(_fh(sym)), p


def real_signal(sym, mos=50):
    m, f, p = real(sym)
    v = valuation(m, f)
    return m, v, p, signal(m, v, price=p, price_ok=True, mos=mos)


# ---------- synthetic Metrics: everything passes, one field flipped per case ----------

def synth_m(**kw):
    checks = {k: Check(k, LABELS[k], 0.25, THRESHOLDS[k], True, "ok") for k in CHECK_KEYS}
    base = dict(symbol="SYN", financial=False, years=10, window=[f"{y}-12-31" for y in range(2016, 2026)],
                currency="USD", roic_now=0.25, roe_now=0.30, net_income_ttm=100.0, fcf_ttm=110.0,
                owner_earnings_ttm=100.0, eps_ttm_edgar=5.0, eps_last_fy=4.8, eps_3yr_avg=4.5,
                growth={"sales": 0.12, "eps": 0.12, "equity": 0.12, "fcf": 0.12},
                growth_source={"eps": "finnhub"}, debt=10.0, cash=5.0, equity=200.0, assets=400.0,
                pe_hist_median=30.0, market_cap_usd=2000.0, listing_factor=1.0, checks=checks,
                big5=5, big5_na=0, rule1_business=True, status=None, status_reason="", young=False,
                eps_ttm_usd=5.0, notes=[])
    base.update(kw)
    return Metrics(**base)


def flip(m, key, passed=False, value=None, detail="x"):
    m = copy.deepcopy(m)
    m.checks[key] = dataclasses.replace(m.checks[key], passed=passed, value=value, detail=detail)
    return m


def synth_v(sticker=100.0, buffett=120.0, financial=False):
    return Valuation(sticker=sticker, buffett=buffett, fair_value=fair_value(sticker, buffett, financial),
                     g=0.12, g_source="eps", future_pe=24.0, pe_source="2 × g", sticker_eps=5.0,
                     payback_years=10.0, explain=[])


# ---------- sticker, Buffett, payback ----------

def test_sticker_matches_hand_math():
    s, pe = sticker_price(8.98, 0.12, 30.0)
    assert pe == 24 and s == pytest.approx(8.98 * 1.12 ** 10 * 24 / 1.15 ** 10, rel=1e-9)


def test_future_pe_floor_and_cap():
    assert sticker_price(1, 0.0, 3)[1] == 5
    assert sticker_price(1, 0.15, 90)[1] == 30
    assert sticker_price(1, 0.15, None)[1] == 30
    assert sticker_price(1, 0.15, 25)[1] == 25          # historical median lower than 2 × g


def test_sticker_is_price_invariant():
    f = _fh("AAPL")
    p = fixture_price("AAPL")
    a = valuation(*real("AAPL", p)[:2])
    b = valuation(*real("AAPL", p * 1.10)[:2])            # within the 15% sanity band: same listing factor
    assert a.sticker is not None and a.sticker == b.sticker and a.fair_value == b.fair_value
    m = synth_m()
    assert valuation(m, None).sticker == valuation(copy.deepcopy(m), None).sticker
    assert f is not None


def test_buffett_value_hand_math():
    exp = sum(10 * 1.1 ** t / 1.1 ** t for t in range(1, 11)) + (10 * 1.1 ** 10 * 1.03 / (0.10 - 0.03)) / 1.1 ** 10
    assert buffett_value(10.0, 0.10) == pytest.approx(exp)


def test_buffett_value_zero_growth():
    exp = sum(10 / 1.1 ** t for t in range(1, 11)) + (10 * 1.03 / 0.07) / 1.1 ** 10
    assert buffett_value(10.0, 0.0) == pytest.approx(exp)


def test_payback_years():
    assert payback_years(100.0, 0.0, 800.0) == 8
    assert payback_years(100.0, 0.10, 800.0) == 6            # 110+121+133+146+161 = 671 < 800 ≤ 848
    assert payback_years(1.0, 0.0, 1e9) == 50                 # capped
    assert payback_years(None, 0.1, 800.0) is None
    assert payback_years(-5.0, 0.1, 800.0) is None
    assert payback_years(100.0, 0.1, 0.0) is None
    assert payback_years(100.0, None, 800.0) is None


def test_valuation_synthetic_hand_math():
    m = synth_m()
    v = valuation(m, None)
    # sticker EPS = min(5.0, 1.5 × 4.5 × 1.0); g = min(eps .12, equity .12); future P/E min(24, 30)
    assert v.sticker_eps == 5.0 and v.g == pytest.approx(0.12) and v.future_pe == 24
    assert v.sticker == pytest.approx(5.0 * 1.12 ** 10 * 24 / 1.15 ** 10)
    # Buffett per share = 5.0 × 100/100 at fcf growth 12%
    assert v.buffett == pytest.approx(buffett_value(5.0, 0.12))
    assert v.fair_value == pytest.approx(min(v.sticker, v.buffett))
    assert v.payback_years == payback_years(100.0, 0.12, 2000.0)
    assert any("Sticker EPS $5.00" in s for s in v.explain)
    assert any(s.startswith("Fair value") for s in v.explain)


def test_sticker_eps_uses_three_year_average_times_listing_factor():
    v = valuation(synth_m(eps_ttm_usd=10.0, eps_3yr_avg=4.0, listing_factor=0.5), None)
    assert v.sticker_eps == pytest.approx(3.0)                  # 1.5 × 4.0 × 0.5
    assert valuation(synth_m(eps_3yr_avg=None), None).sticker_eps == 5.0
    assert valuation(synth_m(eps_ttm_usd=None), None).sticker_eps == pytest.approx(6.75)
    v = valuation(synth_m(eps_ttm_usd=None, eps_3yr_avg=None), None)
    assert v.sticker is None and v.fair_value is None


def test_growth_choice_and_fallbacks():
    assert valuation(synth_m(growth={"sales": .1, "eps": .14, "equity": .11, "fcf": .2}), None).g == pytest.approx(.11)
    v = valuation(synth_m(growth={"sales": .1, "eps": .14, "equity": None, "fcf": .09}), None)
    assert v.g == pytest.approx(.09) and v.g_source == "fcf"
    assert valuation(synth_m(growth={"sales": .1, "eps": .3, "equity": .4, "fcf": .2}), None).g == 0.15
    assert valuation(synth_m(growth={"sales": .1, "eps": -.05, "equity": .4, "fcf": .2}), None).g == 0.0
    v = valuation(synth_m(growth={"sales": .1, "eps": None, "equity": .4, "fcf": .2}), None)
    assert v.g is None and v.sticker is None
    v = valuation(synth_m(growth={"sales": .1, "eps": .12, "equity": None, "fcf": None}), None)
    assert v.g is None and v.sticker is None


def test_negative_sticker_eps_gives_no_sticker():
    v = valuation(synth_m(eps_ttm_usd=-1.0, eps_3yr_avg=2.0), None)
    assert v.sticker is None and v.fair_value is None


def test_buffett_needs_positive_net_income_and_owner_earnings():
    assert valuation(synth_m(net_income_ttm=-5.0), None).buffett is None
    assert valuation(synth_m(owner_earnings_ttm=-5.0), None).buffett is None
    assert valuation(synth_m(net_income_ttm=None), None).buffett is None
    v = valuation(synth_m(owner_earnings_ttm=None), None)
    assert v.buffett is None and v.fair_value == v.sticker
    assert valuation(synth_m(growth={"sales": .1, "eps": .12, "equity": .12, "fcf": None}), None).buffett \
        == pytest.approx(buffett_value(5.0, 0.0))


def test_financial_synthetic_uses_sticker_only():
    v = valuation(synth_m(financial=True), None)
    assert v.buffett is None and v.fair_value == v.sticker and v.sticker is not None


def test_financial_signal_uses_sticker_only():
    m, f, _ = real("JPM")
    v = valuation(m, f)
    assert m.financial and v.buffett is None and v.fair_value == v.sticker


def test_valuation_empty_metrics_never_raises():
    v = valuation(Metrics(symbol="X", status="ETF", status_reason="ETF"), None)
    assert v.sticker is None and v.fair_value is None and v.buffett is None


# ---------- fair value and Safety Buy ----------

def test_safety_buy_is_lower_of_sticker_and_buffett():
    m = synth_m()
    assert signal(m, synth_v(100.0, 120.0), price=40, price_ok=True, mos=50)["safety_buy"] == 50.0
    assert signal(m, synth_v(100.0, 80.0), price=40, price_ok=True, mos=50)["safety_buy"] == 40.0
    assert signal(m, synth_v(100.0, None), price=40, price_ok=True, mos=50)["safety_buy"] == 50.0
    assert fair_value(100.0, 80.0, True) == 100.0               # financial: sticker alone
    assert fair_value(None, 80.0, False) is None


# ---------- price guard ----------

def test_price_guard_null_zero_stale_far():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ET)
    assert price_guard(None, None, 100, now=now)[0] is False and price_guard(0, ms(now), 100, now=now)[0] is False
    assert price_guard(100, ms(now - timedelta(days=3)), 100, now=now)[0] is False
    assert price_guard(140, ms(now), 100, now=now)[0] is False
    assert price_guard(100, ms(now - timedelta(hours=20)), 102, now=now)[0] is True   # last session


def test_price_guard_reasons():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ET)
    assert price_guard(None, ms(now), 100, now=now) == (False, "no price")
    assert price_guard(float("nan"), ms(now), 100, now=now) == (False, "no price")
    assert price_guard(True, ms(now), 100, now=now) == (False, "no price")
    assert price_guard(0, ms(now), 100, now=now) == (False, "price 0")
    assert price_guard(100, None, 100, now=now)[0] is False
    assert price_guard(100, ms(now - timedelta(days=3)), 100, now=now) == (False, "quote stale (2026-11-03)")
    assert price_guard(140, ms(now), 100, now=now) == (False, "price 40% from reference")
    assert price_guard(125, ms(now), 100, now=now) == (True, "")                         # exactly 25% is ok
    assert price_guard(100, ms(now), None, now=now) == (True, "")                        # no reference


def test_price_guard_monday_accepts_friday_and_rejects_thursday():
    mon = datetime(2026, 11, 9, 9, 35, tzinfo=ET)
    assert price_guard(100, ms(datetime(2026, 11, 6, 15, 59, tzinfo=ET)), 100, now=mon)[0] is True
    assert price_guard(100, ms(datetime(2026, 11, 5, 15, 59, tzinfo=ET)), 100, now=mon)[0] is False


def test_price_guard_uses_et_date_not_utc():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ET)
    # 2026-11-05 00:30 UTC is still 2026-11-04 in New York: older than the previous trading day.
    t = datetime(2026, 11, 5, 0, 30, tzinfo=ZoneInfo("UTC"))
    assert price_guard(100, ms(t), 100, now=now) == (False, "quote stale (2026-11-04)")


# ---------- signals ----------

def test_signal_truth_table():
    m, v = synth_m(), synth_v(100.0, 120.0)
    young_m = synth_m(years=6, young=True)
    falling_m = flip(synth_m(), "eps_not_falling")
    avoid_m = flip(synth_m(roic_now=0.09), "roic_now", value=0.09)
    buy = signal(m, v, price=40, price_ok=True, mos=50)
    assert buy["signal"] == "BUY" and buy["leaps"] is True and set(buy) == KEYS
    assert signal(m, v, price=70, price_ok=True, mos=50) == {
        "signal": "WAIT", "leaps": True, "reason": "price above safety buy", "buy_below": 50.0,
        "leaps_below": 80.0, "buy_zone_pct": pytest.approx(1 - 70 / 50), "safety_buy": 50.0, "quality": "HIGH"}
    assert signal(m, v, price=90, price_ok=True, mos=50)["leaps"] is False
    np_ = signal(m, v, price=40, price_ok=False, mos=50)
    assert np_["signal"] == "NO_PRICE" and np_["leaps"] is False
    assert np_["buy_below"] == 50.0 and np_["leaps_below"] == 80.0
    y = signal(young_m, v, price=40, price_ok=True, mos=50)
    assert y["signal"] == "WAIT" and y["reason"] == "young: 6 years, BUY needs 8" and y["leaps"] is False
    fa = signal(falling_m, v, price=40, price_ok=True, mos=50)
    assert fa["signal"] == "WAIT" and fa["reason"] == "earnings falling" and fa["leaps"] is False
    av = signal(avoid_m, v, price=40, price_ok=True, mos=50)
    assert av["signal"] == "AVOID" and av["reason"] == "ROIC 9% < 15%" and av["leaps"] is False
    assert signal(m, v, price=60, price_ok=True, mos=30)["signal"] == "BUY"             # 30% MOS: safety 70


def test_leaps_band_ignores_mos():
    m, v = synth_m(), synth_v()
    assert signal(m, v, price=75, price_ok=True, mos=30)["leaps"] == signal(m, v, price=75, price_ok=True, mos=50)["leaps"]
    assert signal(m, v, price=75, price_ok=True, mos=30)["leaps_below"] == 80.0


def test_leaps_needs_roic_20():
    m, v = synth_m(roic_now=0.18), synth_v()
    s = signal(m, v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "BUY" and s["leaps"] is False and s["quality"] == "LOW"
    fin = synth_m(financial=True, roic_now=None, roe_now=0.22)
    s = signal(fin, synth_v(financial=True), price=40, price_ok=True, mos=50)
    assert s["signal"] == "BUY" and s["leaps"] is True and s["quality"] == "HIGH"


def test_avoid_reasons_in_words():
    v = synth_v()
    s = signal(flip(synth_m(), "debt", value=5.3), v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["reason"] == "debt 5.3× FCF"
    assert signal(flip(synth_m(), "earning", value=-1.0), v, price=40, price_ok=True, mos=50)["reason"] == "losing money"
    s = signal(flip(synth_m(), "sources", detail="EPS 1.00 vs Finnhub 2.00"), v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["reason"].startswith("Check data")
    m = synth_m(rule1_business=False)
    m = flip(m, "sales_growth", value=0.02)
    m = flip(m, "fcf_growth", value=0.08)
    s = signal(m, v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["reason"] == "not a Rule #1 business: sales growth 2%, FCF growth 8%"
    s = signal(synth_m(), synth_v(None, None), price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["safety_buy"] is None and s["leaps"] is False
    fin = flip(synth_m(financial=True, roe_now=0.09), "roic_now", value=0.09)
    assert signal(fin, synth_v(financial=True), price=40, price_ok=True, mos=50)["reason"] == "ROE 9% < 15%"


def test_status_rows_carry_their_status():
    for st, why in (("ETF", "ETF"), ("STALE", "latest 10-K 2023-12-31"), ("CHECK_DATA", "Check data: debt unresolved"),
                    ("SHORT_HISTORY", "Short history (4 years)"), ("NO_FILINGS", "No filings")):
        s = signal(synth_m(status=st, status_reason=why), synth_v(), price=40, price_ok=True, mos=50)
        assert s["signal"] == st and s["reason"] == why and s["leaps"] is False


def test_wait_rows_carry_levels():
    # final review fix 1: a WAIT row publishes only the levels that can fire. A full-history row
    # above the safety buy shows both; a young row keeps safety_buy for display but no BUY/LEAPS level.
    s = signal(synth_m(), synth_v(), price=90, price_ok=True, mos=30)
    assert s["signal"] == "WAIT" and s["buy_below"] == pytest.approx(70.0) and s["leaps_below"] == 80.0
    s = signal(synth_m(years=6, young=True), synth_v(), price=90, price_ok=True, mos=30)
    assert s["signal"] == "WAIT" and s["safety_buy"] == pytest.approx(70.0)
    assert s["buy_below"] is None and s["leaps_below"] is None


def test_bad_price_inputs_never_buy():
    m, v = synth_m(), synth_v()
    for p in (None, 0, -5, float("nan"), float("inf")):
        s = signal(m, v, price=p, price_ok=True, mos=50)
        assert s["signal"] == "NO_PRICE" and s["leaps"] is False and s["buy_zone_pct"] is None


# ---------- real fixtures ----------

def test_aapl_is_wait_with_levels_below_price():
    m, v, p, s = real_signal("AAPL")
    assert v.sticker is not None and v.sticker > 0
    assert s["signal"] == "WAIT" and s["buy_below"] < p


def test_jpm_has_no_buffett_value():
    m, v, p, s = real_signal("JPM")
    assert v.buffett is None


def test_hood_is_young_but_fails_the_business_test():
    # HOOD (7 years, young) is a financial whose ROE consistency (40%) and EPS growth (undefined) fail
    # the Rule #1 business test, so the spec's AVOID wins over the young WAIT cap.
    m, v, p, s = real_signal("HOOD")
    assert m.young and m.financial and not m.rule1_business
    assert s["signal"] == "AVOID" and s["reason"].startswith("not a Rule #1 business") and s["leaps"] is False


def test_hood_shape_passing_business_test_is_young_wait():
    m, f, p = real("HOOD")
    m.rule1_business = True
    m.growth = dict(m.growth, eps=0.20, equity=0.18)
    s = signal(m, valuation(m, f), price=1.0, price_ok=True, mos=50)
    assert s["signal"] == "WAIT" and s["reason"] == "young: 7 years, BUY needs 8" and s["leaps"] is False


def test_tsm_is_stale():
    assert real_signal("TSM")[3]["signal"] == "STALE"


def test_crwv_is_avoid_not_short_history():
    # two years (the 5%-of-latest rule cuts 2023) and losing money: the loss decides, not the history
    m, v, p, s = real_signal("CRWV")
    assert m.years == 2 and s["signal"] == "AVOID" and s["reason"] == "losing money"


@pytest.mark.parametrize("sym", SYMBOLS)
def test_every_fixture_smoke(sym):
    m, v, p, s = real_signal(sym)
    assert set(s) == KEYS and s["signal"] in VOCAB and isinstance(s["leaps"], bool)
    assert isinstance(v.explain, list) and all(isinstance(x, str) for x in v.explain)
    for k in ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct"):
        assert s[k] is None or math.isfinite(s[k])
    if s["signal"] == "BUY":
        assert s["safety_buy"] is not None and p <= s["safety_buy"]
    if s["leaps"]:
        assert s["leaps_below"] is not None and p <= s["leaps_below"]
    assert signal(m, v, price=p, price_ok=False, mos=50)["leaps"] is False


# ---------- review fixes ----------

@pytest.mark.parametrize("key", ["roic_now", "debt", "earning", "history", "sources"])
def test_unknown_gate_is_avoid_not_buy(key):
    m = flip(synth_m(), key, passed=None)
    s = signal(m, synth_v(), price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["leaps"] is False
    assert s["reason"] == f"Check data: {LABELS[key]} n/a"


def test_financial_debt_na_is_not_avoid():
    m = flip(synth_m(financial=True), "debt", passed=None)
    assert signal(m, synth_v(financial=True), price=40, price_ok=True, mos=50)["signal"] == "BUY"


def test_status_rows_publish_no_levels():
    m, v, p, s = real_signal("TSM")
    assert s["signal"] == "STALE"
    synth = signal(synth_m(status="CHECK_DATA", status_reason="Check data: debt unresolved"), synth_v(),
                   price=40, price_ok=True, mos=50)
    assert synth["signal"] == "CHECK_DATA"
    for row in (s, synth):
        assert row["leaps"] is False
        assert all(row[k] is None for k in ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct"))


def test_mos_whitelist():
    m, v = synth_m(), synth_v()
    assert signal(m, v, price=40, price_ok=True, mos=30)["safety_buy"] == pytest.approx(70.0)
    for bad in (0, 10, 49, 70, 90, None, float("nan"), "30"):
        assert signal(m, v, price=40, price_ok=True, mos=bad)["safety_buy"] == 50.0


def test_bad_pe_median_falls_back_to_two_g():
    assert sticker_price(1, 0.12, 0)[1] == 24 and sticker_price(1, 0.12, -8)[1] == 24
    assert sticker_price(1, 0.12, float("nan"))[1] == 24
    v = valuation(synth_m(pe_hist_median=-3.0), None)
    assert v.future_pe == 24 and any("historical p/e unavailable" in x.lower() for x in v.explain)


def test_buffett_explain_says_growth_unknown():
    v = valuation(synth_m(growth={"sales": .1, "eps": .12, "equity": .12, "fcf": None}), None)
    assert any("growth unknown, 0%/yr assumed" in x for x in v.explain)


def test_price_exactly_at_levels():
    m, v = synth_m(), synth_v()
    assert signal(m, v, price=50.0, price_ok=True, mos=50)["signal"] == "BUY"
    s = signal(m, v, price=80.0, price_ok=True, mos=50)
    assert s["leaps"] is True and s["signal"] == "WAIT"


def test_price_guard_saturday_accepts_friday_quote():
    sat = datetime(2026, 11, 7, 12, 0, tzinfo=ET)
    assert price_guard(100, ms(datetime(2026, 11, 6, 16, 0, tzinfo=ET)), 100, now=sat) == (True, "")


def test_price_guard_seconds_valued_quote_time_is_stale():
    now = datetime(2026, 11, 6, 10, 0, tzinfo=ET)
    ok, why = price_guard(100, now.timestamp(), 100, now=now)       # seconds, not milliseconds
    assert ok is False and why.startswith("quote stale (1970-")


def test_buffett_value_growth_differs_from_discount():
    exp = sum(10 * 1.05 ** t / 1.1 ** t for t in range(1, 11)) + (10 * 1.05 ** 10 * 1.03 / 0.07) / 1.1 ** 10
    assert buffett_value(10.0, 0.05) == pytest.approx(exp, rel=1e-12)


# ---------- final review fix 1: levels only where they can fire ----------

LEVELS = ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct")


def test_avoid_row_has_no_levels():
    m = flip(synth_m(roic_now=0.09), "roic_now", value=0.09)
    for p, ok in ((40, True), (70, True), (40, False)):
        s = signal(m, synth_v(), price=p, price_ok=ok, mos=50)
        assert s["signal"] == "AVOID" and s["leaps"] is False
        assert all(s[k] is None for k in LEVELS), s


def test_young_row_has_no_buy_below():
    s = signal(synth_m(years=6, young=True), synth_v(), price=40, price_ok=True, mos=50)
    assert s["signal"] == "WAIT" and s["buy_below"] is None and s["leaps_below"] is None
    assert s["safety_buy"] == 50.0


def test_falling_or_unknown_earnings_row_has_no_buy_below():
    for passed in (False, None):
        s = signal(flip(synth_m(), "eps_not_falling", passed=passed), synth_v(), price=40, price_ok=True, mos=50)
        assert s["signal"] == "WAIT" and s["buy_below"] is None and s["leaps_below"] is None


def test_roe_17pct_bank_has_no_leaps_below():
    fin = synth_m(financial=True, roic_now=None, roe_now=0.17)
    s = signal(fin, synth_v(financial=True), price=40, price_ok=True, mos=50)
    assert s["signal"] == "BUY" and s["buy_below"] == 50.0 and s["leaps_below"] is None and s["leaps"] is False


def test_no_price_row_keeps_reachable_levels_only():
    s = signal(synth_m(), synth_v(), price=None, price_ok=False, mos=50)
    assert s["signal"] == "NO_PRICE" and s["buy_below"] == 50.0 and s["leaps_below"] == 80.0
    s = signal(synth_m(years=6, young=True), synth_v(), price=None, price_ok=False, mos=50)
    assert s["signal"] == "NO_PRICE" and s["buy_below"] is None and s["leaps_below"] is None


def test_payback_none_for_non_usd_filer():
    from long_term.finnhub import FinnhubExtract
    f = FinnhubExtract(symbol="SYN", market_cap_usd=2000.0)
    assert valuation(synth_m(), f).payback_years is not None
    assert valuation(synth_m(currency="TWD", market_cap_usd=None), f).payback_years is None


# ---------- "no data" fix (2026-11-07): forced AVOID, WAIT cap, young at 3 ----------

def test_signal_truth_table_no_data_rows():
    v = synth_v(100.0, 120.0)
    # R35 / R38: a forced AVOID reason wins over everything and publishes no levels
    for why in ("no revenue yet", "losing money", "losing money (sources differ on the size)",
                "ROIC 7% < 15% (sources differ on EPS)", "cash from operations negative"):
        s = signal(synth_m(forced_avoid=why, years=0), v, price=40, price_ok=True, mos=50)
        assert s["signal"] == "AVOID" and s["reason"] == why and s["leaps"] is False
        assert all(s[k] is None for k in ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct"))
        assert signal(synth_m(forced_avoid=why), v, price=None, price_ok=False, mos=50)["signal"] == "AVOID"
    # R40: a WAIT cap never BUYs or LEAPS, whatever the price; the reason names the gap
    cap = "not cross-checked: Finnhub EPS missing"
    s = signal(synth_m(wait_cap=cap), v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "WAIT" and s["reason"] == cap and s["leaps"] is False
    assert s["buy_below"] is None and s["leaps_below"] is None and s["safety_buy"] == 50.0
    # a capped row with an n/a gate is WAIT (the cap explains the n/a), not "Check data: ... n/a"
    m = flip(synth_m(wait_cap="ROIC not measurable: negative equity", roic_now=None), "roic_now", passed=None)
    s = signal(m, v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "WAIT" and s["reason"] == "ROIC not measurable: negative equity" and s["quality"] == "LOW"
    # ... but a failing gate on a capped row is still AVOID
    m = flip(synth_m(wait_cap=cap), "debt", value=5.3)
    assert signal(m, v, price=40, price_ok=True, mos=50)["reason"] == "debt 5.3× FCF"
    # a capped row without a price is NO_PRICE with no reachable BUY level
    s = signal(synth_m(wait_cap=cap), v, price=None, price_ok=False, mos=50)
    assert s["signal"] == "NO_PRICE" and s["buy_below"] is None and s["leaps_below"] is None
    # R33: three- and four-year rows are young WAIT rows
    for n in (3, 4):
        s = signal(synth_m(years=n, young=True, window=[f"{y}-12-31" for y in range(2026 - n, 2026)]),
                   v, price=40, price_ok=True, mos=50)
        assert s["signal"] == "WAIT" and s["reason"] == f"young: {n} years, BUY needs 8" and s["leaps"] is False
    # the cap beats "young" in the reason (the gap is the more useful words)
    s = signal(synth_m(years=4, young=True, wait_cap=cap), v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "WAIT" and s["reason"] == cap
    # fix round (M2 / M4 / M5): the new cap texts behave like every other cap, at any price
    for cap2 in ("not cross-checked: periods differ (Finnhub FY 2026-12-31 vs EDGAR 2025-12-31)",
                 "not cross-checked: capex from two tags (larger used in 2016-2018)",
                 "not cross-checked: EPS derived in 2016, 2017"):
        for price in (1.0, 40.0, 500.0):
            s = signal(synth_m(wait_cap=cap2), v, price=price, price_ok=True, mos=50)
            assert s["signal"] == "WAIT" and s["reason"] == cap2 and s["leaps"] is False, (cap2, price)
            assert s["buy_below"] is None and s["leaps_below"] is None
    # unchanged: a clean full row still BUYs
    assert signal(synth_m(), v, price=40, price_ok=True, mos=50)["signal"] == "BUY"


def test_earning_gate_words_tell_the_cash_side_apart():
    # ORCL: EPS +6.44 but FCF -$28.7B is "no free cash flow", not "losing money"
    v = synth_v()
    s = signal(flip(synth_m(), "earning", value=6.44, detail="EPS 6.44 (Finnhub); FCF -28,720,000,000 (TTM)"),
               v, price=40, price_ok=True, mos=50)
    assert s["signal"] == "AVOID" and s["reason"] == "no free cash flow"
    s = signal(flip(synth_m(), "earning", value=2.0, detail="owner earnings negative after stock comp (FCF $19.7B − SBC $30.4B)"),
               v, price=40, price_ok=True, mos=50)
    assert s["reason"] == "owner earnings negative after stock comp"
    assert signal(flip(synth_m(), "earning", value=-1.0), v, price=40, price_ok=True, mos=50)["reason"] == "losing money"
    assert signal(flip(synth_m(), "earning", value=None), v, price=40, price_ok=True, mos=50)["reason"] == "losing money"


def test_unknown_gate_without_cap_is_still_avoid():
    m = flip(synth_m(), "roic_now", passed=None)
    assert signal(m, synth_v(), price=40, price_ok=True, mos=50)["reason"] == "Check data: ROIC now n/a"


def test_history_avoid_words_use_min_years():
    from long_term.metrics import MIN_YEARS
    assert MIN_YEARS == 3
    m = flip(synth_m(years=2), "history", value=2.0)
    assert signal(m, synth_v(), price=40, price_ok=True, mos=50)["reason"] == "Short history (2 years)"
    m = flip(synth_m(years=6), "history", value=6.0, detail="6 complete years; Stale filing (annual 2025-06-30)")
    assert signal(m, synth_v(), price=40, price_ok=True, mos=50)["reason"].startswith("history: 6 complete years")


def test_crwv_is_avoid_losing_money():
    m, v, p, s = real_signal("CRWV")
    assert m.years == 2 and m.status is None and m.forced_avoid == "losing money"
    assert s["signal"] == "AVOID" and s["reason"] == "losing money" and s["leaps"] is False
    assert all(s[k] is None for k in ("buy_below", "leaps_below", "safety_buy", "buy_zone_pct"))


def test_metrics_round_trip_keeps_the_new_fields():
    # the service rebuilds Metrics from summary.json: the new fields must survive asdict/**kw
    m = synth_m(forced_avoid="no revenue yet", wait_cap="x", pre_revenue=True)
    d = dataclasses.asdict(m)
    assert d["forced_avoid"] == "no revenue yet" and d["wait_cap"] == "x" and d["pre_revenue"] is True
