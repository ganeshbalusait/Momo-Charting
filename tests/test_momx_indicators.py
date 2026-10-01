"""Tests for momx.indicators - the literal thinkScript primitive ports.

Two of these are ANTI-DRIFT GUARDS, not unit tests: they assert that the EMA
seeding here equals ``premarket_scanner.ema_series`` and that ``macd()`` here
equals the MACD(6, 12, 8) already replayed inside
``ganesh_higher_timeframe_signals``. Those two modules are validated against
real TOS charts. If a future edit "improves" the seeding or the MACD formula in
momx, these tests fail loudly instead of silently drifting the whole app away
from thinkorswim.
"""

from __future__ import annotations

import math

import pytest

from momx import indicators as ind


# --------------------------------------------------------------------------
# sma / stdev_pop
# --------------------------------------------------------------------------

def test_sma_is_simple_average_with_nan_warmup():
    out = ind.sma([1.0, 2.0, 3.0, 4.0], 3)
    assert math.isnan(out[0])
    assert math.isnan(out[1])
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)


def test_stdev_pop_divides_by_n_not_n_minus_one():
    # values 1, 2, 3 -> mean 2, deviations -1/0/1.
    # population: sqrt((1+0+1)/3) = sqrt(2/3) = 0.8164965...
    # sample:     sqrt((1+0+1)/2) = 1.0
    out = ind.stdev_pop([1.0, 2.0, 3.0], 3)
    assert out[2] == pytest.approx(math.sqrt(2.0 / 3.0))
    assert out[2] != pytest.approx(1.0)


def test_stdev_pop_of_constant_series_is_zero():
    out = ind.stdev_pop([5.0] * 6, 3)
    assert out[-1] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# ema  (anti-drift guard)
# --------------------------------------------------------------------------

def test_ema_seeds_on_first_value_not_an_sma_seed():
    closes = [10.0, 11.0, 12.0]
    out = ind.ema(closes, 4)
    alpha = 2.0 / 5.0
    assert out[0] == pytest.approx(10.0)          # first value seeds
    assert out[1] == pytest.approx(10.0 + alpha * (11.0 - 10.0))
    assert out[2] == pytest.approx(out[1] + alpha * (12.0 - out[1]))


def test_ema_matches_premarket_scanner_ema_series():
    import premarket_scanner

    closes = [100.0 + math.sin(i / 3.0) * 5.0 + i * 0.25 for i in range(120)]
    for length in (4, 8, 9, 12, 20, 50):
        mine = ind.ema(closes, length)
        theirs = premarket_scanner.ema_series(closes, length)
        assert mine == pytest.approx(theirs), "EMA drift at length %s" % length


def test_ema_matches_ganesh_next_ema_recurrence():
    import ganesh_higher_timeframe_signals as ghts

    closes = [50.0, 51.5, 49.0, 52.25, 53.0, 52.5, 55.0]
    mine = ind.ema(closes, 9)
    prev = None
    theirs = []
    for close in closes:
        prev = ghts._next_ema(prev, close, 9)
        theirs.append(prev)
    assert mine == pytest.approx(theirs)


def test_ema_default_length_is_nine():
    closes = [10.0, 12.0, 9.0, 14.0]
    assert ind.ema(closes) == pytest.approx(ind.ema(closes, 9))


# --------------------------------------------------------------------------
# wma
# --------------------------------------------------------------------------

def test_wma_weights_run_one_to_n_with_n_on_the_newest_bar():
    # weights 1,2,3 over [1, 2, 6] -> (1*1 + 2*2 + 3*6) / 6 = 23/6
    out = ind.wma([1.0, 2.0, 6.0], 3)
    assert out[2] == pytest.approx(23.0 / 6.0)
    # the reversed weighting (3,2,1) would give (3+4+6)/6 = 13/6
    assert out[2] != pytest.approx(13.0 / 6.0)


def test_wma_warms_up_with_nan():
    out = ind.wma([1.0, 2.0, 6.0], 3)
    assert math.isnan(out[0]) and math.isnan(out[1])


# --------------------------------------------------------------------------
# true_range
# --------------------------------------------------------------------------

def test_true_range_first_bar_is_high_minus_low():
    tr = ind.true_range([10.0, 11.0], [9.0, 10.5], [9.5, 11.0])
    assert tr[0] == pytest.approx(1.0)


def test_true_range_takes_the_gap_leg_when_it_is_largest():
    highs = [10.0, 20.0]
    lows = [9.0, 19.5]
    closes = [9.5, 20.0]
    # bar 1: h-l = 0.5, abs(h - c[1]) = 10.5, abs(l - c[1]) = 10.0
    assert ind.true_range(highs, lows, closes)[1] == pytest.approx(10.5)


# --------------------------------------------------------------------------
# macd  (anti-drift guard)
# --------------------------------------------------------------------------

def test_macd_value_and_avg_follow_the_thinkscript_definition():
    closes = [10.0, 10.5, 11.0, 10.75, 11.5, 12.0]
    value, avg = ind.macd(closes, 6, 12, 8)
    assert value == pytest.approx(
        [f - s for f, s in zip(ind.ema(closes, 6), ind.ema(closes, 12))]
    )
    assert avg == pytest.approx(ind.ema(value, 8))


def test_macd_agrees_with_ganesh_higher_timeframe_signals():
    import ganesh_higher_timeframe_signals as ghts

    closes = [200.0 + math.cos(i / 5.0) * 12.0 + i * 0.4 for i in range(150)]
    value, avg = ind.macd(closes)

    base = None
    their_value = []
    their_avg = []
    for close in closes:
        base = ghts._source_values(base, close)
        their_value.append(base["macdValue"])
        their_avg.append(base["macdAverage"])

    assert value == pytest.approx(their_value)
    assert avg == pytest.approx(their_avg)


# --------------------------------------------------------------------------
# stochastic_fast_d
# --------------------------------------------------------------------------

def test_stochastic_fast_k_hand_computed_on_settled_bars():
    highs = [10.0, 11.0, 12.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 10.0, 11.5]
    # k = 2: bar 1 -> highest 11, lowest 8, close 10   -> 100 * 2/3
    #        bar 2 -> highest 12, lowest 9, close 11.5 -> 100 * 2.5/3
    fast_k = ind.stochastic_fast_k(highs, lows, closes, 2)
    assert fast_k[1] == pytest.approx(100.0 * 2.0 / 3.0)
    assert fast_k[2] == pytest.approx(100.0 * 2.5 / 3.0)


def test_stochastic_fast_d_is_the_weighted_average_of_fast_k():
    highs = [10.0, 11.0, 12.0]
    lows = [8.0, 9.0, 10.0]
    closes = [9.0, 10.0, 11.5]
    fast_k = ind.stochastic_fast_k(highs, lows, closes, 2)
    fast_d = ind.stochastic_fast_d(highs, lows, closes, 2, 2)
    assert fast_d[2] == pytest.approx((1.0 * fast_k[1] + 2.0 * fast_k[2]) / 3.0)


def test_stochastic_fast_d_flat_range_does_not_raise_or_emit_nan():
    n = 30
    highs = [50.0] * n
    lows = [50.0] * n
    closes = [50.0] * n
    fast_d = ind.stochastic_fast_d(highs, lows, closes, 8, 8)
    settled = fast_d[8 + 8 - 2:]
    assert settled, "expected settled bars past the warm-up"
    assert all(not math.isnan(value) for value in settled)
    assert all(value == pytest.approx(0.0) for value in settled)


# --------------------------------------------------------------------------
# linear regression endpoint (thinkScript Inertia)
# --------------------------------------------------------------------------

def test_linreg_endpoint_on_a_straight_line_returns_the_exact_endpoint():
    values = [3.0 + 2.0 * i for i in range(10)]
    out = ind.linreg_endpoint(values, 5)
    assert out[9] == pytest.approx(values[9])
    assert out[4] == pytest.approx(values[4])


def test_linreg_endpoint_is_not_a_moving_average():
    values = [float(i) for i in range(10)]
    out = ind.linreg_endpoint(values, 5)
    moving = ind.sma(values, 5)
    assert out[9] == pytest.approx(9.0)
    assert moving[9] == pytest.approx(7.0)


# --------------------------------------------------------------------------
# ttm_squeeze
# --------------------------------------------------------------------------

def test_ttm_squeeze_low_volatility_series_is_in_squeeze():
    n = 60
    closes = [100.0 + (0.01 if i % 2 else -0.01) for i in range(n)]
    highs = [close + 0.4 for close in closes]
    lows = [close - 0.4 for close in closes]
    result = ind.ttm_squeeze(highs, lows, closes)
    assert result.squeeze_alert[-1] == 0


def test_ttm_squeeze_high_volatility_series_is_not_in_squeeze():
    # A directional expansion: closes travel far (wide Bollinger band) while
    # each individual bar range stays small (narrow Keltner channel), so the
    # band breaks outside the channel. Note a whipsaw series would NOT work
    # here - alternating closes inflate TrueRange through the gap leg and TOS
    # correctly still calls that a squeeze.
    n = 60
    closes = [100.0 + i * 5.0 for i in range(n)]
    highs = [close + 0.05 for close in closes]
    lows = [close - 0.05 for close in closes]
    result = ind.ttm_squeeze(highs, lows, closes)
    assert result.squeeze_alert[-1] == 1


def test_ttm_squeeze_warmup_bars_are_not_reported_as_in_squeeze():
    n = 10
    closes = [100.0] * n
    highs = [100.5] * n
    lows = [99.5] * n
    result = ind.ttm_squeeze(highs, lows, closes, length=20)
    assert result.squeeze_alert == [1] * n


def test_ttm_squeeze_tighter_nk_is_a_subset_of_the_default():
    # nk = 1.0 is a TIGHTER Keltner channel than the 1.5 default, so any bar in
    # the nk=1.0 squeeze must also be in the nk=1.5 squeeze.
    n = 80
    closes = [100.0 + math.sin(i / 4.0) * 0.6 for i in range(n)]
    highs = [close + 0.5 for close in closes]
    lows = [close - 0.5 for close in closes]
    wide = ind.ttm_squeeze(highs, lows, closes, nk=1.5)
    tight = ind.ttm_squeeze(highs, lows, closes, nk=1.0)
    for tight_bar, wide_bar in zip(tight.squeeze_alert, wide.squeeze_alert):
        if tight_bar == 0:
            assert wide_bar == 0


def test_ttm_squeeze_histogram_has_one_value_per_bar_and_tracks_trend():
    n = 40
    closes = [100.0 + i * 0.3 for i in range(n)]
    highs = [close + 0.5 for close in closes]
    lows = [close - 0.5 for close in closes]
    result = ind.ttm_squeeze(highs, lows, closes)
    assert len(result.histogram) == n
    assert len(result.squeeze_alert) == n
    # a steady uptrend leaves price above the midline -> positive momentum
    assert result.histogram[-1] > 0


# --------------------------------------------------------------------------
# crosses
# --------------------------------------------------------------------------

def test_crosses_above_requires_below_or_equal_then_strictly_above():
    assert ind.crosses_above([1.0, 3.0], [2.0, 2.0]) == [False, True]
    # touching (equal) then above still counts: <= then >
    assert ind.crosses_above([2.0, 3.0], [2.0, 2.0]) == [False, True]


def test_crosses_above_does_not_fire_when_equal_on_both_bars():
    assert ind.crosses_above([2.0, 2.0], [2.0, 2.0]) == [False, False]


def test_crosses_above_does_not_fire_on_the_first_bar():
    assert ind.crosses_above([5.0], [1.0]) == [False]


def test_crosses_below_requires_above_or_equal_then_strictly_below():
    assert ind.crosses_below([3.0, 1.0], [2.0, 2.0]) == [False, True]
    assert ind.crosses_below([2.0, 1.0], [2.0, 2.0]) == [False, True]
    assert ind.crosses_below([2.0, 2.0], [2.0, 2.0]) == [False, False]


def test_crosses_ignore_non_finite_values():
    nan = float("nan")
    assert ind.crosses_above([nan, 3.0], [2.0, 2.0]) == [False, False]
    assert ind.crosses_below([nan, 1.0], [2.0, 2.0]) == [False, False]


# --------------------------------------------------------------------------
# within_bars
# --------------------------------------------------------------------------

def test_within_one_bar_is_the_current_bar_only():
    """``within 1 bars`` is a ONE-candle window, so it is the identity.

    The reference: "true at least one time for the given number of bars
    starting from the current one", with ``Doji() within 3 bars`` meaning "at
    least one Doji among three candles including the current one".

    This test previously asserted [False, True, True, False] for the same
    input -- an N+1 window -- and that is what kept every Skittles MACD/EMA
    cross lit a bar too long, painting the opposite colour on any bar where a
    cross reversed. The test agreed with the code and both were wrong.
    """
    flags = [False, True, False, False]
    assert ind.within_bars(flags, 1) == flags


def test_within_bars_below_one_still_spans_a_single_bar():
    # thinkScript has no zero-bar window. Clamping to one bar keeps a bad
    # argument harmless; returning all-False would silently blank the column.
    flags = [False, True, False, False]
    assert ind.within_bars(flags, 0) == flags
    assert ind.within_bars(flags, -3) == flags


def test_within_two_bars_spans_the_current_bar_and_one_before():
    # Two CANDLES, not two extra bars: the event bar and the one after it.
    flags = [False, True, False, False]
    assert ind.within_bars(flags, 2) == [False, True, True, False]


# --------------------------------------------------------------------------
# rvol_zscore
# --------------------------------------------------------------------------

def test_rvol_zscore_constant_volume_series_returns_zero():
    out = ind.rvol_zscore([1000.0] * 60, 50)
    assert all(value == 0.0 for value in out)
    assert not any(math.isnan(value) for value in out)


def test_rvol_zscore_warmup_bars_are_zero_not_nan():
    volumes = [float(100 + i) for i in range(10)]
    out = ind.rvol_zscore(volumes, 50)
    assert out == [0.0] * 10


def test_rvol_zscore_matches_hand_computed_zscore():
    volumes = [10.0, 20.0, 60.0]
    out = ind.rvol_zscore(volumes, 3)
    pop_sd = math.sqrt(((10 - 30) ** 2 + (20 - 30) ** 2 + (60 - 30) ** 2) / 3.0)
    assert out[2] == pytest.approx((60.0 - 30.0) / pop_sd)


# --------------------------------------------------------------------------
# input tolerance
# --------------------------------------------------------------------------

def test_functions_accept_pandas_series_input():
    pd = pytest.importorskip("pandas")
    closes = pd.Series([10.0, 11.0, 12.0, 11.5])
    assert ind.ema(closes, 4) == pytest.approx(ind.ema(list(closes), 4))
    assert ind.sma(closes, 2)[-1] == pytest.approx(11.75)


def test_empty_input_returns_empty_lists():
    assert ind.sma([], 5) == []
    assert ind.ema([], 5) == []
    assert ind.wma([], 5) == []
    assert ind.crosses_above([], []) == []
    assert ind.within_bars([], 1) == []
    assert ind.rvol_zscore([], 50) == []
