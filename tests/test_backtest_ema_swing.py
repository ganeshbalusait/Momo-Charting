"""Unit: the 21/50 EMA swing back-test engine (artifacts/backtest_ema_swing.py).

The back-test decides which setups may go on screen, so its own pieces are
checked: the EMA is TOS ExpAverage, signals never look ahead, the option
model charges the spread, and the verdict needs BOTH periods to win.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "artifacts"))
import backtest_ema_swing as bt  # noqa: E402
from momx import ema_touch  # noqa: E402


def _bars(closes, lows=None, highs=None):
    idx = pd.date_range("2010-01-04", periods=len(closes), freq="B")
    c = pd.Series(closes, index=idx, dtype=float)
    return pd.DataFrame({
        "open": c, "close": c,
        "low": pd.Series(lows if lows is not None else closes, index=idx, dtype=float),
        "high": pd.Series(highs if highs is not None else closes, index=idx, dtype=float),
        "last_day": idx,
    })


def test_ema_is_the_scanners_tos_expaverage():
    closes = [10.0, 11.0, 9.5, 12.0, 13.0, 12.5]
    got = bt.tos_ema(pd.Series(closes), 3).tolist()
    assert got == pytest.approx(ema_touch.ema(closes, 3))


def test_norm_cdf_matches_erf():
    for x in (-3.0, -1.0, 0.0, 0.5, 2.5):
        assert float(bt.norm_cdf(x)) == pytest.approx(0.5 * (1 + math.erf(x / math.sqrt(2))), abs=2e-7)


def test_strike_for_delta_gives_that_delta():
    s, t, vol = 100.0, 45 / 252, 0.35
    for delta in (0.5, 0.3):
        k = bt.strike_for_delta(s, t, vol, delta)
        d1 = (math.log(s / k) + (bt.RATE + vol * vol / 2) * t) / (vol * math.sqrt(t))
        assert float(bt.norm_cdf(d1)) == pytest.approx(delta, abs=1e-4)


def test_option_round_trip_with_no_move_loses_the_spread_and_time():
    r = bt.option_returns(np.array([100.0]), np.array([100.0]), np.array([0]), np.array([0.3]), 0.5, 45)
    mid = float(bt.bs_call(100.0, bt.strike_for_delta(100.0, 45 / 252, 0.3, 0.5), 45 / 252, 0.3))
    hs = max(0.04 * mid, 0.025)
    assert float(r[0]) == pytest.approx((mid - hs) / (mid + hs) - 1, rel=1e-6)
    assert float(r[0]) < -0.07          # ~8% round trip charged


def test_touch_needs_the_uptrend_and_signals_only_after_warm_up():
    closes = [50 + 0.2 * i for i in range(160)]
    lows = list(closes)
    e21 = bt.tos_ema(pd.Series(closes), 21)
    lows[150] = float(e21.iat[150]) - 0.01      # tag the 21 EMA, close stays above
    lows[50] = float(e21.iat[50]) - 0.01        # same, but inside the warm-up
    sig, _ = bt.bar_signals(_bars(closes, lows))
    hits = list(np.flatnonzero(sig["touch21"].values))
    assert hits == [150]


def test_bounce_fires_on_the_bar_after_the_touch():
    closes = [50 + 0.2 * i for i in range(160)]
    lows, highs = list(closes), list(closes)
    e21 = bt.tos_ema(pd.Series(closes), 21)
    lows[150] = float(e21.iat[150]) - 0.01
    highs[150] = closes[150] + 0.05             # bar 151 closes 0.2 higher -> above
    sig, _ = bt.bar_signals(_bars(closes, lows, highs))
    assert list(np.flatnonzero(sig["bounce21"].values)) == [151]


def test_cross_and_reclaim():
    closes = [100 - 0.3 * i for i in range(130)] + [61 + 1.5 * i for i in range(60)]
    sig, e50 = bt.bar_signals(_bars(closes))
    cross = np.flatnonzero(sig["cross"].values)
    reclaim = np.flatnonzero(sig["reclaim"].values)
    assert len(cross) == 1 and len(reclaim) == 1 and reclaim[0] < cross[0]


def test_trades_never_overlap_and_exit_at_the_stop():
    close = np.array([100, 101, 102, 95, 96, 97, 98, 99, 100, 101, 102, 103], dtype=float)
    rows = bt.run_trades(close, [(0, 96.0), (1, 96.0), (5, 90.0), (6, 90.0)], hold=5)
    starts = [r[0] for r in rows]
    assert starts == [0, 6]                     # 1 and 5 fall inside trade 0 (days 0-5)
    i, j, plain, managed = rows[0][:4]
    assert j == 3 and managed == pytest.approx(95 / 100 - 1)   # stopped on the 95 close
    assert plain == pytest.approx(97 / 100 - 1)                # held 5 days (close[5] = 97)


def test_a_close_under_the_line_uses_the_day_low_as_stop():
    close = np.array([100, 99, 98, 97, 96, 95], dtype=float)
    low = np.array([97, 99, 98, 97, 96, 95], dtype=float)
    rows = bt.run_trades(close, [(0, 101.0)], hold=5, low=low)
    assert len(rows) == 1 and rows[0][1] == 3   # 97 close <= day-low stop 97


def test_verdict_needs_both_margin_and_median_and_enough_trades():
    base = {"n": 1000, "win": 0.55, "median": 0.01}
    assert bt.beats({"n": 40, "win": 0.58, "median": 0.02}, base)
    assert not bt.beats({"n": 40, "win": 0.56, "median": 0.02}, base)      # < 2 pts
    assert not bt.beats({"n": 40, "win": 0.60, "median": 0.005}, base)     # lower median
    assert not bt.beats({"n": 20, "win": 0.70, "median": 0.05}, base)      # too few
    assert not bt.beats(None, base)
