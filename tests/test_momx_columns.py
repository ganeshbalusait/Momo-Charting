"""Cell-level parity tests for momx.columns.

Every tape here is hand-built so the expected colour is derivable on paper.

THE RVOL IDENTITY THESE TESTS LEAN ON
    For a window of ``n`` volume bars where ``k`` of them (including the last)
    sit at a high value B and the other ``n - k`` sit at a low value A, the
    population z-score of the last bar is exactly

        z = sqrt((n - k) / k)

    independent of A and B. With n = 50 that gives k = 5 -> z = 3.0 exactly,
    k = 13 -> z = 1.6867 (rounds to 1.7), k = 40 -> z = 0.5 exactly. That is
    how the ladder rungs below are hit precisely without retyping a float.
"""

from __future__ import annotations

import pytest

from momx import columns
from momx.indicators import stochastic_fast_d


# ---------------------------------------------------------------------------
# tape builders
# ---------------------------------------------------------------------------

def bar(close, *, high=None, low=None, open_=None, volume=1000.0, time=0):
    """One bar. ``high``/``low`` default to a symmetric 1-point range."""
    return {
        "time": time,
        "open": close if open_ is None else open_,
        "high": close + 1.0 if high is None else high,
        "low": close - 1.0 if low is None else low,
        "close": close,
        "volume": volume,
    }


def bullish_bar(volume, close=100.0, time=0):
    """close - low > high - close -> isBullish."""
    return bar(close, high=close + 0.25, low=close - 1.0, volume=volume, time=time)


def bearish_bar(volume, close=100.0, time=0):
    """high - close > close - low -> not isBullish."""
    return bar(close, high=close + 1.0, low=close - 0.25, volume=volume, time=time)


def volume_tape(k_high, *, n=50, low=100.0, high=1000.0, bullish=True):
    """n bars, the last ``k_high`` at ``high`` volume -> z = sqrt((n-k)/k)."""
    maker = bullish_bar if bullish else bearish_bar
    volumes = [low] * (n - k_high) + [high] * k_high
    return [maker(volume, time=index * 60) for index, volume in enumerate(volumes)]


def flat_squeeze_tape(count=40, close=100.0, half_range=1.0):
    """Zero close-stdev but a real true range -> permanently IN squeeze.

    2 * StDev(close, 20) == 0 <= nk * ATR for every nk, so both the MS (1.5)
    and the tighter HS (1.0) channels report a squeeze on every settled bar.
    """
    return [
        bar(close, high=close + half_range, low=close - half_range, time=index * 60)
        for index in range(count)
    ]


def released_squeeze_tape(quiet=40, expansion=4, close=100.0):
    """Flat/compressed, then a hard expansion that breaks BOTH channels."""
    bars = flat_squeeze_tape(quiet, close=close)
    price = close
    for index in range(expansion):
        price += 12.0
        bars.append(
            bar(price, high=price + 1.0, low=price - 1.0, time=(quiet + index) * 60)
        )
    return bars


def strong_bar(close, time=0):
    """A bar that closes ON its high - what a bar in a strong trend looks like.

    With ``high == close`` the 8-bar FastK reaches a clean 100 at every new
    high, which is what lets the Skittles tests below talk about "near 100"
    without hand-waving. A symmetric +/-1 bar can never exceed
    100 * 8 / 9 = 88.9 on the same trend.
    """
    return bar(close, high=close, low=close - 1.0, time=time)


def trend_then_dip_tape():
    """Long uptrend, a very shallow 4-bar dip, then a sharp resumption.

    The dip is deliberately too shallow to pull EMA9 under EMA20 (so EXU stays
    true and EXD1/EXU1 never fire) or to knock the stochastic off its highs,
    but it is enough to push the fast MACD value under its slower average. The
    resumption then produces a clean MACD21 cross-up on the LAST bar with EXU
    already true -> the "MACD21 and EXU" rung, Color.GREEN.
    """
    closes = [100.0 + index for index in range(41)]      # 100 .. 140
    closes += [139.8, 139.6, 139.4, 139.2]               # shallow dip
    closes += [141.0, 143.0, 145.0]                      # resumption
    return [strong_bar(close, time=index * 60) for index, close in enumerate(closes)]


# ---------------------------------------------------------------------------
# rvol_cell -- the WATCHLIST COLUMN version
# ---------------------------------------------------------------------------

def zscore_tape(target_z, *, bullish=True, n=50):
    """``n`` bars whose LAST bar has population z-score ``target_z``.

    The column plots his script's ``(volume - Average(volume, 50)) /
    StDev(volume, 50)``, so tests need tapes that land on chosen z values.

    Two things make this less trivial than it looks:

    * A flat base does not work. With ``n-1`` identical volumes and one
      different last bar, the z-score reduces to ``7 * sign(x - A)`` for every
      x, so such a tape can only ever score +7 or -7. The base below therefore
      carries a deterministic spread.
    * Adding the last bar shifts the mean AND the stdev it is measured
      against, so x cannot be computed in one step. z is monotonic in x, so it
      is found by bisection instead of by a closed form that would be easy to
      get subtly wrong.
    """
    from momx.indicators import rvol_zscore

    maker = bullish_bar if bullish else bearish_bar
    # A spread base: any variation will do, as long as StDev is not degenerate.
    base = [100.0 + (index % 7) * 5.0 for index in range(n - 1)]

    def z_for(x):
        return rvol_zscore(base + [x], n)[-1]

    low, high = 0.0, 1_000_000.0
    if z_for(high) < target_z:
        raise AssertionError("target z=%s unreachable in this base" % target_z)
    for _ in range(200):
        mid = (low + high) / 2.0
        if z_for(mid) < target_z:
            low = mid
        else:
            high = mid
    volumes = base + [(low + high) / 2.0]
    return [maker(volume, time=index * 60) for index, volume in enumerate(volumes)]


def ratio_tape(target_ratio, *, bullish=True):
    """50 bars: 49 at volume 100, the last sized so volume/avg == target_ratio.

    The column now shows the RATIO thinkorswim displays (volume / Average(volume,
    50)), not the z-score the pasted script text computed - trader decision
    2026-08-28. For 49 bars at 100 and a last bar x, ratio = 50x/(4900+x), so
    x = 4900*r/(50-r) lands the last bar on exactly ``target_ratio``.
    """
    maker = bullish_bar if bullish else bearish_bar
    x = 4900.0 * target_ratio / (50.0 - target_ratio)
    volumes = [100.0] * 49 + [x]
    return [maker(volume, time=index * 60) for index, volume in enumerate(volumes)]


def test_rvol_constant_volume_is_exactly_zero():
    """Constant volume is exactly average, so the z-score is 0.

    His script guards this explicitly (``if IsNaN(rawRelVol) then 0``): a flat
    window has zero StDev, so the raw division is NaN. 0.0 sits below every
    colour rung -- the lowest is "> 0.5" -- so the cell is black on black.
    """
    cell = columns.rvol_cell([bullish_bar(1000.0, time=i * 60) for i in range(60)])
    assert cell["value"] == 0.0
    assert cell["bg"] == "black"
    assert cell["fg"] == "black"


def test_rvol_bullish_three_x_is_cyan_bg_black_fg():
    # 3.2, comfortably past the >=3 rung. Solving a tape to land exactly on
    # 3.0 leaves the raw score at 2.9999... (float), which rounds to 3.0 for
    # display but reads as < 3 in the colour ladder - a test artefact.
    cell = columns.rvol_cell(zscore_tape(3.2, bullish=True))
    assert cell["value"] == 3.2
    assert cell["bg"] == "cyan"
    assert cell["fg"] == "black"


def test_rvol_bearish_one_point_seven_x_is_black_bg_magenta_fg():
    cell = columns.rvol_cell(zscore_tape(1.7, bullish=False))
    assert cell["value"] == 1.7
    assert cell["bg"] == "black"                 # bg ladder floor is >= 2
    assert cell["fg"] == "magenta"               # fg rung is >= 1.5


def test_rvol_bullish_one_point_seven_x_is_black_bg_cyan_fg():
    cell = columns.rvol_cell(zscore_tape(1.7, bullish=True))
    assert cell["value"] == 1.7
    assert cell["bg"] == "black"
    assert cell["fg"] == "cyan"


def test_rvol_exactly_half_x_is_black_fg_because_that_rung_is_strictly_greater():
    """The DARK_GREEN/DARK_RED rung is "> 0.5", not ">= 0.5"."""
    # EXACTLY 0.5 has to be exact, not merely rounding to 0.5: the rung tests
    # the RAW score, so a tape solved to 0.5000001 would fire dark_green and
    # the test would pass for the wrong reason. volume_tape lands it exactly --
    # k=40 of n=50 gives z = sqrt(10/40) = 0.5 with no float slack.
    cell = columns.rvol_cell(volume_tape(40, bullish=True))
    assert cell["value"] == 0.5
    assert cell["fg"] == "black"

    hotter = columns.rvol_cell(zscore_tape(0.6, bullish=True))
    assert hotter["value"] == 0.6
    assert hotter["fg"] == "dark_green"


def test_rvol_bearish_two_x_is_red_bg():
    # 2.2, clear of the >=2 boundary (see the 3.x note about float landing).
    cell = columns.rvol_cell(zscore_tape(2.2, bullish=False))
    assert cell["value"] == 2.2
    assert cell["bg"] == "red"
    assert cell["fg"] == "black"


def test_rvol_empty_tape_is_a_null_cell():
    assert columns.rvol_cell([]) == columns.null_cell()


# ---------------------------------------------------------------------------
# squeeze_cell -- the WATCHLIST COLUMN version
# ---------------------------------------------------------------------------

def test_squeeze_permanent_squeeze_is_white_and_counts_bars():
    tape = flat_squeeze_tape(40)
    cell = columns.squeeze_cell(tape)
    series = columns.squeeze_column_series(tape)

    assert cell["bg"] == "white"                 # the HS branch wins outright
    assert cell["fg"] == "black"                 # the script hardcodes Color.Black
    # TTM warm-up reports "not in squeeze" for the first 19 bars, so the count
    # starts at bar 19 and the label is the raw HSC (HSF is 0 inside a squeeze).
    assert series["HSC"][-1] == 40 - 19
    assert cell["value"] == str(40 - 19)
    assert isinstance(cell["value"], str)


def test_squeeze_release_uses_the_star_prefix_and_the_fired_branch():
    tape = released_squeeze_tape(quiet=40, expansion=1)
    series = columns.squeeze_column_series(tape)
    cell = columns.squeeze_cell(tape)

    assert series["HS"][-1] is False             # the tight channel just broke
    assert series["HSF"][-1] == 1                # first fired bar
    assert series["HSC"][-1] == 0                # the counter resets on release
    assert cell["value"] == "*1"
    assert cell["bg"] == "orange"                # both HSF rungs are orange
    assert cell["fg"] == "black"


def test_squeeze_fired_counter_caps_at_two_bars():
    """MSF/HSF go 1, 2, then 0 -- the cap is "MSF[1] > 0 and MSF[1] < 2"."""
    tape = released_squeeze_tape(quiet=40, expansion=4)
    series = columns.squeeze_column_series(tape)

    assert series["MSF"][39] == 0                # last quiet bar, still squeezed
    assert series["MSF"][40] == 1                # release bar
    assert series["MSF"][41] == 2                # one bar of grace
    assert series["MSF"][42] == 0                # capped
    assert series["MSF"][43] == 0
    assert series["HSF"][40:44] == [1, 2, 0, 0]


def test_squeeze_after_the_fired_window_is_black_with_a_dash():
    tape = released_squeeze_tape(quiet=40, expansion=4)
    cell = columns.squeeze_cell(tape)
    assert cell["value"] == "-"
    assert cell["bg"] == "black"


def test_squeeze_higher_looks_two_bars_back_not_one():
    """The COLUMN script is "high > close[2]" (the SCANNER one is close[1])."""
    tape = [bar(100.0, time=0), bar(200.0, time=60), bar(100.5, time=120)]
    series = columns.squeeze_column_series(tape)
    # high[2] = 101.5 > close[0] = 100.0 -> True. Against close[1] = 200 it
    # would be False, which is how a close[1] port would betray itself.
    assert series["Higher"] == [False, False, True]


def test_squeeze_short_tape_is_a_null_cell():
    assert columns.squeeze_cell([bar(100.0)]) == columns.null_cell()


# ---------------------------------------------------------------------------
# skittles_cell
# ---------------------------------------------------------------------------

def test_skittles_value_is_the_rounded_weighted_fastd():
    tape = trend_then_dip_tape()
    cell = columns.skittles_cell(tape)
    expected = stochastic_fast_d(
        [row["high"] for row in tape],
        [row["low"] for row in tape],
        [row["close"] for row in tape],
        8,
        8,
    )[-1]
    assert cell["value"] == int(columns._ts_round(expected, 0))
    assert cell["value"] == 95           # near 100: the dip barely dents FastD
    assert cell["value"] >= 90


def test_skittles_macd_cross_up_with_ema9_above_ema20_is_green():
    cell = columns.skittles_cell(trend_then_dip_tape())
    assert cell["bg"] == "green"
    # fg rung 3: (MACD21 and EXU) -> Color.BLACK
    assert cell["fg"] == "black"


def test_skittles_cross_colours_only_the_bar_it_happens_on():
    """``within 1 bars`` is a ONE-candle window, so the cross lights one bar.

    This test used to assert the opposite -- that the cell stayed green a bar
    later -- and its comment said so: ``"within 1 bars" keeps it lit``. That
    matched the implementation, and both were wrong. The reference
    (toslc.thinkorswim.com, Reserved Words -> within) defines the window as
    "the given number of bars STARTING FROM THE CURRENT ONE", with
    ``Doji() within 3 bars`` meaning three candles INCLUDING the current one.

    The cost of the old reading was not merely a stale colour: on a bar where
    a cross reversed, the expired cross still won its rung and the cell painted
    the OPPOSITE direction. Measured on the CRWD daily tape in this suite, 13
    of 65 daily end-bars changed colour once this was corrected.
    """
    tape = trend_then_dip_tape()
    on_the_cross = columns.skittles_cell(tape)
    tape_next = tape + [strong_bar(147.0, time=len(tape) * 60)]
    one_bar_later = columns.skittles_cell(tape_next)

    assert on_the_cross["bg"] == "green"
    # The very next bar: the window has already passed.
    assert one_bar_later["bg"] == "black"
    assert one_bar_later["fg"] == "dark_green"

    # And it stays passed.
    tape_two_later = tape_next + [strong_bar(149.0, time=len(tape_next) * 60)]
    two_bars_later = columns.skittles_cell(tape_two_later)
    assert two_bars_later["bg"] == "black"
    assert two_bars_later["fg"] == "dark_green"


def test_skittles_trending_up_without_a_recent_cross_is_black_bg_but_lit_fg():
    tape = [strong_bar(100.0 + index, time=index * 60) for index in range(60)]
    cell = columns.skittles_cell(tape)
    assert cell["bg"] == "black"                 # no cross in the last 2 bars
    # EXU and value >= 90 -> DARK_GREEN text on the black cell.
    assert cell["fg"] == "dark_green"
    assert cell["value"] == 100                  # every bar closes on a new high


def test_skittles_symmetric_bars_cap_fastk_below_the_dark_green_rung():
    """+/-1 bars on a +1 trend can only reach 100 * 8/9 = 88.9 -> CYAN text.

    The pair with the test above is the point: the DARK_GREEN rung is
    "EXU and value >= 90", so where the bar closes inside its own range
    decides the text colour. This is the rung most likely to expose a FastK
    window off-by-one.
    """
    tape = [bar(100.0 + index, time=index * 60) for index in range(60)]
    cell = columns.skittles_cell(tape)
    assert cell["value"] == 89
    assert cell["bg"] == "black"
    assert cell["fg"] == "cyan"


def test_skittles_short_tape_is_a_null_cell():
    assert columns.skittles_cell([bar(100.0), bar(101.0)]) == columns.null_cell()


# ---------------------------------------------------------------------------
# high_low_cell -- HIS script, not inferred. Supplied 2026-08-28 and
# re-confirmed 2026-09-03 from the column editor: name "HighLowGraph",
# aggregation 2h, EXT on, length 8. Colours are HIS too since 2026-09-25
# (AssignBackgroundColor: >0.5 DARK_GREEN, >0 GREEN, <-0.5 DARK_RED, <0 RED,
# else GRAY).
# ---------------------------------------------------------------------------

def test_high_low_close_at_the_range_high_is_plus_one():
    """close == Highest(high, 8) -> HighLowDegree == +1 (script: (close-mid)/(hh-mid))."""
    tape = [strong_bar(100.0 + index, time=index * 60) for index in range(8)]
    cell = columns.high_low_cell(tape)
    assert cell["value"] == 1.0
    assert cell["bg"] == "dark_green"


def test_high_low_uses_only_the_trailing_eight_bars():
    tape = [bar(100.0 + index, time=index * 60) for index in range(10)]
    # Window = bars 2..9: hh = 110, ll = 101, close = 109, mid = 105.5
    # -> (109 - 105.5) / (110 - 105.5) = 3.5/4.5 = 0.778. If bars 0-1 leaked in,
    # ll would be 99, mid 104.5, and the answer 0.818 - a different number, so
    # this still proves only the trailing 8 bars are used.
    cell = columns.high_low_cell(tape)
    assert cell["value"] == pytest.approx(0.78, abs=0.01)
    assert cell["bg"] == "dark_green"


def test_high_low_close_at_the_range_low_is_minus_point_seven_eight():
    """The magnitude, not just the sign.

    `< 50` was the old assertion, which every value in a -1..+1 range passes -
    so it could not have caught a change to the formula at the bottom of the
    scale, which is the half that decides what sinks out of his default sort.

    Descending tape, trailing 8 bars have closes 108..101, highs 109..102,
    lows 107..100. hh = 109, ll = 100, mid = 104.5, close = 101:
        (101 - 104.5) / (109 - 104.5) = -3.5 / 4.5 = -0.778
    """
    tape = [bar(110.0 - index, time=index * 60) for index in range(10)]
    cell = columns.high_low_cell(tape)
    assert cell["value"] == pytest.approx(-0.78, abs=0.01)
    assert cell["bg"] == "dark_red"


def test_high_low_midpoint_is_zero_not_the_low():
    """0 means the MIDDLE of the range. The Learn page used to say it meant
    the low, which is the opposite reading of an empty cell."""
    tape = [bar(100.0, high=110.0, low=90.0, time=index * 60) for index in range(8)]
    cell = columns.high_low_cell(tape)
    assert cell["value"] == 0                    # close 100 == mid of 90..110
    assert cell["bg"] == "gray"


def test_high_low_flat_range_does_not_divide_by_zero():
    tape = [bar(100.0, high=100.0, low=100.0, time=index * 60) for index in range(10)]
    cell = columns.high_low_cell(tape)
    assert cell["value"] == 0                     # the script's hh == ll guard
    assert cell["bg"] == "gray"                  # his colour ladder: 0 -> GRAY


def test_high_low_mild_readings_are_plain_green_and_red():
    # hh 110, ll 90, mid 100: close 103 -> +0.3 (GREEN), close 97 -> -0.3 (RED)
    up = [bar(100.0, high=110.0, low=90.0, time=index * 60) for index in range(7)] + [bar(103.0, high=110.0, low=90.0, time=420)]
    down = up[:-1] + [bar(97.0, high=110.0, low=90.0, time=420)]
    assert columns.high_low_cell(up)["bg"] == "green"
    assert columns.high_low_cell(down)["bg"] == "red"


def test_high_low_short_tape_is_a_null_cell():
    assert columns.high_low_cell([bar(100.0)]) == columns.null_cell()


# ---------------------------------------------------------------------------
# color_cell
# ---------------------------------------------------------------------------

def test_color_cell_is_a_white_separator_with_no_value():
    assert columns.color_cell() == {"value": None, "bg": "white", "fg": None}
    assert columns.color_cell() is not columns.color_cell()   # never shared


# ---------------------------------------------------------------------------
# quote_trend
# ---------------------------------------------------------------------------

def test_quote_trend_marks_up_down_and_flat():
    closes = [100.0, 101.0, 101.0, 99.0]
    tape = [bar(close, time=index * 60) for index, close in enumerate(closes)]
    cells = columns.quote_trend(tape)

    assert [cell["value"] for cell in cells] == [0, 1, 0, -1]
    assert [cell["fg"] for cell in cells] == ["gray", "green", "gray", "red"]
    assert [cell["bg"] for cell in cells] == ["black", "dark_green", "black", "dark_red"]


def test_quote_trend_first_bar_of_a_one_bar_tape_is_flat():
    cells = columns.quote_trend([bar(100.0)])
    # Each cell now carries the close price too (drives the MomoX-style bar
    # height); the direction/colour of a lone first bar is still flat.
    assert cells == [{"value": 0, "bg": "black", "fg": "gray", "price": 100.0}]


def test_quote_trend_windows_the_tail_but_compares_against_the_real_prior_bar():
    tape = [bar(100.0 + index, time=index * 60) for index in range(20)]
    cells = columns.quote_trend(tape, points=3)
    assert len(cells) == 3
    # The first cell of the WINDOW is bar 17, which does have a close[1].
    assert [cell["value"] for cell in cells] == [1, 1, 1]


def test_quote_trend_of_an_empty_tape_is_empty():
    assert columns.quote_trend([]) == []


# ---------------------------------------------------------------------------
# pct_change / sparkline
# ---------------------------------------------------------------------------

def test_pct_change_is_latest_close_versus_the_previous_daily_close():
    tape = [bar(100.0, time=0), bar(110.0, time=86400)]
    assert columns.pct_change(tape) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# Premarket %Chg
#
# Before the open the daily tape has no bar for today, so "closes[-1] vs
# closes[-2]" answers with YESTERDAY's change: stable, plausible and wrong,
# and it corrects itself at the open so it never looked broken. Measured on
# his board 2026-09-03, and confirmed against the previous session's last row
# to two decimals on five symbols.
#
# ET midnight epochs used below (daily bars arrive stamped midnight ET on
# their session date):
#   2026-09-01 00:00 -04:00 = 1788235200
#   2026-09-02 00:00 -04:00 = 1788321600
#   2026-09-03 00:00 -04:00 = 1788408000
#   2026-09-03 07:21 -04:00 = 1788434460   (a premarket intraday bar)
# ---------------------------------------------------------------------------

SEP_01 = 1788235200
SEP_02 = 1788321600
SEP_03 = 1788408000
SEP_03_PRE = 1788434460


def test_premarket_pct_change_is_measured_against_yesterdays_close():
    """CLS, 2026-09-03 07:21. The board showed -5.07%; that was 09-02's change.

    Daily tape ends on 09-02 (277.77). Intraday has an 09-03 premarket bar and
    the stock is trading 280.78, so the honest answer is +1.08%, not -5.07%.
    """
    daily = [bar(292.59, time=SEP_01), bar(277.77, time=SEP_02)]
    intraday = [bar(280.78, time=SEP_03_PRE)]
    assert columns.pct_change(daily, intraday, 280.78) == pytest.approx(1.084, abs=0.01)
    # The bug it replaces: yesterday's own change, frozen.
    yesterday = (277.77 - 292.59) / 292.59 * 100
    assert columns.pct_change(daily) == pytest.approx(yesterday)
    assert yesterday == pytest.approx(-5.07, abs=0.01)


def test_crdo_the_worst_case_no_longer_reads_minus_twenty():
    """CRDO sat at exactly -20.04% for the whole premarket while flat at ~166."""
    daily = [bar(207.0, time=SEP_01), bar(165.52, time=SEP_02)]
    intraday = [bar(165.38, time=SEP_03_PRE)]
    fixed = columns.pct_change(daily, intraday, 165.38)
    assert fixed == pytest.approx(-0.085, abs=0.05)   # flat, which it was
    assert columns.pct_change(daily) == pytest.approx(-20.04, abs=0.01)


def test_regular_hours_is_untouched():
    """Once today's daily bar exists the old expression runs, unchanged.

    This is the guarantee that makes the fix safe: the column keeps whatever
    parity it has with his TOS during the session, and only the premarket
    hours - which were wrong - can move.
    """
    daily = [bar(292.59, time=SEP_01), bar(277.77, time=SEP_02), bar(310.75, time=SEP_03)]
    intraday = [bar(310.75, time=SEP_03_PRE)]
    expected = (310.75 - 277.77) / 277.77 * 100
    assert columns.pct_change(daily, intraday, 999.0) == pytest.approx(expected)
    assert columns.pct_change(daily) == pytest.approx(expected)


def test_premarket_without_a_live_price_says_nothing_rather_than_yesterday():
    """None beats a number that LOOKS like today's change and is not."""
    daily = [bar(292.59, time=SEP_01), bar(277.77, time=SEP_02)]
    intraday = [bar(280.78, time=SEP_03_PRE)]
    assert columns.pct_change(daily, intraday, None) is None
    assert columns.pct_change(daily, intraday, float("nan")) is None
    assert columns.pct_change([bar(0.0, time=SEP_02)], intraday, 10.0) is None


def test_undated_bars_fall_back_to_the_daily_expression():
    """Every existing caller and test passes bars stamped time=0."""
    daily = [bar(100.0), bar(110.0)]
    assert columns.pct_change(daily, [bar(120.0)], 120.0) == pytest.approx(10.0)
    assert columns.pct_change(daily, None, 120.0) == pytest.approx(10.0)
    assert columns.pct_change(daily, [], 120.0) == pytest.approx(10.0)


def test_pct_change_stays_total():
    assert columns.pct_change(None) is None
    assert columns.pct_change([]) is None
    assert columns.pct_change("nope", "nope", "nope") is None
    assert columns.pct_change([bar(100.0, time=SEP_02)], [bar(1.0, time=SEP_03_PRE)], None) is None


def test_build_row_hands_pct_change_what_it_needs():
    """The wiring, not just the function: a premarket tape must not print
    yesterday's change on the actual row."""
    tapes = {
        "D": [bar(292.59, time=SEP_01), bar(277.77, time=SEP_02)],
        "5m": [bar(280.78, time=SEP_03_PRE)],
    }
    row = columns.build_row("CLS", tapes)
    assert row["pctChange"] == pytest.approx(1.084, abs=0.01)


def test_the_daily_bar_gets_a_volume_midpoint_span():
    """D is absent from RVOL_BUCKET_SECONDS on purpose - pace is meaningless on
    a daily bar - but the volume MIDPOINT is a real question there.

    Without a daily span the walk fell back to the bar's start, which for a
    daily bar is always midnight, so the D column carried no usable time at
    all. He noticed: "some rvol timestamp is missing".
    """
    assert "D" not in columns.RVOL_BUCKET_SECONDS          # pace: unchanged
    assert columns._MIDPOINT_SPAN_SECONDS["D"] == 86_400   # midpoint: has one
    for key, span in columns.RVOL_BUCKET_SECONDS.items():
        assert columns._MIDPOINT_SPAN_SECONDS[key] == span, key


def test_volume_midpoint_finds_when_half_the_days_volume_traded():
    """A day whose volume is front-loaded reports a morning time, not midnight."""
    et_midnight = 1788408000                       # 2026-09-03 00:00 ET
    five = []
    # 09:30-10:00: the bulk. 14:00-14:30: a trickle.
    for i in range(6):
        five.append(bar(10.0, volume=1_000_000.0, time=et_midnight + 9 * 3600 + 1800 + i * 300))
    for i in range(6):
        five.append(bar(10.0, volume=1_000.0, time=et_midnight + 14 * 3600 + i * 300))
    tapes = {"5m": five, "D": [bar(10.0, volume=6_006_000.0, time=et_midnight)]}
    got = columns._volume_midpoint_time(tapes, tapes["D"], "D")
    assert got != et_midnight, "fell back to midnight instead of walking the day"
    assert et_midnight + 9 * 3600 <= got < et_midnight + 11 * 3600, got


def test_the_zscore_ceiling_is_the_arithmetic_one():
    """sqrt(n-1). Not a tuning choice - it is where the maths stops."""
    assert columns.RVOL_CEILING == pytest.approx((columns.RVOL_LENGTH - 1) ** 0.5)
    assert columns.RVOL_CEILING == pytest.approx(7.0)


def _dominant_volume_tape(spike, base=1000.0, n=50):
    """n bars, the last one dominating - the shape that pins the z-score."""
    bars = [bar(10.0, high=10.4, low=9.6, volume=base) for _ in range(n - 1)]
    bars.append(bar(10.35, high=10.4, low=9.6, volume=spike))
    return bars


def test_a_saturated_cell_carries_the_multiple_the_score_cannot_show():
    """CHPT's case: 30.8x average volume, and the column can only say 7.0."""
    tape = _dominant_volume_tape(1_000_000.0)
    cell = columns.rvol_cell(tape)
    assert cell["value"] >= columns.RVOL_CEILING - columns.RVOL_CEILING_MARGIN
    assert cell["xAvg"] > 10, cell["xAvg"]        # the real multiple, uncapped
    # and it is the plain ratio, not something new
    from momx.indicators import rvol_ratio
    assert cell["xAvg"] == pytest.approx(
        columns._ts_round(rvol_ratio([float(b["volume"]) for b in tape], 50)[-1], 1)
    )


def test_an_ordinary_reading_carries_no_multiple():
    """The tooltip is for cells the scale can no longer separate, not all of
    them - otherwise it is noise, and the payload grows for nothing."""
    tape = _dominant_volume_tape(1000.0)          # perfectly flat: z = 0
    assert "xAvg" not in columns.rvol_cell(tape)
    assert "xAvg" not in columns.rvol_cell(volume_tape(40))


def test_the_plotted_value_is_untouched_by_any_of_this():
    """TOS parity: the number on screen is still round(relVol, 1)."""
    tape = _dominant_volume_tape(1_000_000.0)
    cell = columns.rvol_cell(tape)
    from momx.indicators import rvol_zscore
    expected = columns._ts_round(rvol_zscore([float(b["volume"]) for b in tape], 50)[-1], 1)
    assert cell["value"] == expected


def test_skittles_cells_carry_their_bars_start_not_a_volume_midpoint():
    """The two ages are computed differently ON PURPOSE.

    An RVOL cell is an accumulation, so its stamp is the volume-weighted
    midpoint. A Skittles cell has no volume - its colour is a cross that fired
    inside the CURRENT bar - so its stamp is the bar's own start, meaning "the
    cross is at most this old". Faking a midpoint for a stochastic would print
    a number that looks like its neighbour and means something incoherent.
    """
    et_midnight = 1788408000                      # 2026-09-03 00:00 ET
    step = 2 * 3600
    two_hour = [
        bar(100.0 + i, high=101.0 + i, low=99.0 + i, volume=1000.0,
            time=et_midnight + 9 * 3600 + i * step)
        for i in range(30)
    ]
    # a finer tape with LOPSIDED volume - if skittles wrongly used the volume
    # midpoint it would land on the heavy bar, not the bucket start
    five = [bar(100.0, volume=1.0, time=et_midnight + 9 * 3600 + i * 300) for i in range(20)]
    five.append(bar(100.0, volume=9_000_000.0, time=et_midnight + 9 * 3600 + 20 * 300))
    tapes = {"2h": two_hour, "5m": five, "D": [bar(100.0, volume=1000.0, time=et_midnight)]}
    row = columns.build_row("TEST", tapes)
    cell = row["skittles"]["2h"]
    assert cell["barAt"] == int(two_hour[-1]["time"]), "not the bar's own start"


def test_every_skittles_timeframe_gets_a_stamp():
    """His call: 'D, 2D, 3D, 4D, W, M - also be fresh just add it'."""
    et_midnight = 1788408000
    tapes = {}
    for key in columns.SKITTLES_TIMEFRAMES:
        tapes[key] = [
            bar(100.0 + i, high=101.0 + i, low=99.0 + i, volume=1000.0,
                time=et_midnight - (30 - i) * 86_400)
            for i in range(30)
        ]
    row = columns.build_row("TEST", tapes)
    for key in columns.SKITTLES_TIMEFRAMES:
        assert row["skittles"][key].get("barAt"), key


def test_pct_change_needs_two_bars_and_a_non_zero_base():
    assert columns.pct_change([bar(100.0)]) is None
    assert columns.pct_change([]) is None
    assert columns.pct_change([bar(0.0), bar(5.0)]) is None


def test_sparkline_is_the_trailing_closes():
    tape = [bar(100.0 + index, time=index * 60) for index in range(100)]
    spark = columns.sparkline(tape, points=5)
    assert spark == [195.0, 196.0, 197.0, 198.0, 199.0]
    assert columns.sparkline([], points=5) == []


# ---------------------------------------------------------------------------
# build_row
# ---------------------------------------------------------------------------

def test_build_row_with_almost_no_history_is_all_null_cells_and_does_not_raise():
    tapes = {
        "5m": [bar(10.0, time=0), bar(10.5, time=300), bar(11.0, time=600)],
        "D": [bar(10.0, time=0)],
    }
    row = columns.build_row("TINY", tapes, industry="Semis")

    assert row["symbol"] == "TINY"
    assert row["industry"] == "Semis"
    assert row["last"] == 11.0
    assert row["pctChange"] is None
    assert "scanPass" not in row and "scanReasons" not in row

    for key in columns.RVOL_TIMEFRAMES:
        assert row["rvol"][key] == columns.null_cell()
    for key in columns.SQUEEZE_TIMEFRAMES:
        assert row["sqz"][key] == columns.null_cell()
    for key in columns.SKITTLES_TIMEFRAMES:
        assert row["skittles"][key] == columns.null_cell()
    assert row["highLow"] == columns.null_cell()
    assert row["color"] == {"value": None, "bg": "white", "fg": None}
    assert len(row["quoteTrend"]) == 3
    assert row["sparkline"] == [10.0, 10.5, 11.0]


def test_build_row_with_no_tapes_at_all_does_not_raise():
    row = columns.build_row("EMPTY", {})
    assert row["last"] is None
    assert row["pctChange"] is None
    assert row["quoteTrend"] == []
    assert row["sparkline"] == []
    assert set(row["rvol"]) == set(columns.RVOL_TIMEFRAMES)


def test_build_row_populates_the_timeframes_it_has_data_for():
    tapes = {
        "5m": volume_tape(5, bullish=True),
        "2h": flat_squeeze_tape(40),
        "D": [bar(100.0 + index, time=index * 86400) for index in range(60)],
    }
    row = columns.build_row("NVDA", tapes)

    assert row["rvol"]["5m"]["bg"] == "cyan"
    assert row["rvol"]["15m"] == columns.null_cell()
    assert row["sqz"]["2h"]["bg"] == "white"
    assert row["sqz"]["4h"] == columns.null_cell()
    assert row["skittles"]["D"]["value"] == 89    # +/-1 bars cap FastK at 88.9
    assert row["highLow"]["value"] is not None
    assert row["pctChange"] == pytest.approx(1.0 / 158.0 * 100.0, abs=0.01)
    assert row["last"] == 100.0                  # the 5m tape is the finest one


def test_build_row_never_raises_on_ragged_input():
    row = columns.build_row("JUNK", {"5m": None, "D": [{"close": "n/a"}], "2h": []})
    assert row["last"] is None
    assert row["rvol"]["5m"] == columns.null_cell()


def test_build_row_payload_keys_match_the_contract():
    row = columns.build_row("NVDA", {})
    assert set(row) == {
        "symbol", "industry", "last", "pctChange", "rvol", "sqz", "skittles",
        "highLow", "color", "quoteTrend", "sparkline", "hourHighLow",
        # Scanner grade inputs (spec 2026-09-21, Task 5): m5 (momentum
        # summary) and sqzRaw (raw squeeze states). "grade" itself is added
        # later, by board._contract_row, once news has been attached.
        "m5", "sqzRaw",
        # Trend strength (2026-09-22): RECORDED, never graded.
        "adx",
        # BEAR scanner (spec 2026-09-24): the bear reading of the 5m tape.
        # board._contract_row files it under row["bear"]["m5"].
        "m5Bear",
    }


# ---------------------------------------------------------------------------
# import hygiene / palette
# ---------------------------------------------------------------------------

def _imports_api_server_in_a_fresh_interpreter(module: str) -> bool:
    """Does importing `module` pull in api_server, asked of a CLEAN interpreter.

    This must not be asked of the running pytest process: its sys.modules is
    shared by every test in the run, so the answer would depend on whether some
    earlier test happened to import api_server. That is exactly how these
    assertions used to fail under whole-suite collection and pass in isolation.
    """
    import subprocess
    import sys as _sys
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    probe = (
        "import sys; import %s; "
        "print('YES' if 'api_server' in sys.modules else 'NO')" % module
    )
    result = subprocess.run(
        [_sys.executable, "-c", probe],
        capture_output=True, text=True, cwd=str(root), timeout=120,
    )
    assert result.returncode == 0, (
        "importing %s failed in a fresh interpreter:\n%s" % (module, result.stderr)
    )
    return result.stdout.strip().endswith("YES")


def test_module_does_not_pull_in_api_server():
    """momx.columns must not drag in the 1MB api_server module.

    Asked of a FRESH interpreter. Asking the pytest process instead makes
    this assert whichever test ran first, which is why it used to fail in
    the full suite and pass alone.
    """
    assert not _imports_api_server_in_a_fresh_interpreter("momx.columns")


def test_every_colour_emitted_is_in_the_contract_palette():
    """No hex, no CSS names -- the backend emits thinkScript colour names only."""
    tapes = {
        "5m": volume_tape(5, bullish=True),
        "2h": flat_squeeze_tape(40),
        "D": [bar(100.0 + index, time=index * 86400) for index in range(60)],
    }
    row = columns.build_row("NVDA", tapes)
    seen = set()

    def walk(node):
        if isinstance(node, dict):
            if "bg" in node and "fg" in node:
                seen.add(node["bg"])
                seen.add(node["fg"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(row)
    assert seen - {None} <= set(columns.PALETTE)


# ----------------------------------------------------------------------
# hour_high_low: the ticker card's "1-hr high / 1-hr low"
#
# Added 2026-09-02 for the per-ticker card. The behaviour worth pinning is
# that it reads the bars' HIGH and LOW, never their closes: the highest close
# of the last hour is not the hour's high, and a card printing that beside the
# last price would be quietly wrong about a level he can see was touched.
# ----------------------------------------------------------------------

def _hl_bars(count, *, high_gap=1.0):
    """Bars whose high/low straddle the close, so a close-based answer differs."""
    return [
        {"close": 100.0 + i, "high": 100.0 + i + high_gap, "low": 100.0 + i - high_gap}
        for i in range(count)
    ]


def test_hour_high_low_reads_highs_and_lows_not_closes():
    bars = _hl_bars(30)
    out = columns.hour_high_low({"5m": bars})
    # Last 12 bars are index 18..29: closes 118..129, highs 119..130, lows 117..128.
    assert out == {"high": 130.0, "low": 117.0}
    assert out["high"] != 129.0, "that is the highest CLOSE, not the high"


def test_hour_high_low_window_is_twelve_five_minute_bars():
    # 12 bars: closes 100..111, so highs 101..112 and lows 99..110.
    assert columns.hour_high_low({"5m": _hl_bars(12)}) == {"high": 112.0, "low": 99.0}


def test_hour_high_low_counts_bars_per_tape_resolution():
    # One hour is 12 bars of 5m but only 4 of 15m and 1 of 1h.
    bars = _hl_bars(30)
    five = columns.hour_high_low({"5m": bars})
    quarter = columns.hour_high_low({"15m": bars})
    hourly = columns.hour_high_low({"1h": bars})
    assert five["low"] < quarter["low"] < hourly["low"], "a coarser tape looks back over fewer bars"
    assert five["high"] == quarter["high"] == hourly["high"], "all end on the same newest bar"


def test_hour_high_low_prefers_the_finest_tape_available():
    fine = _hl_bars(30)
    coarse = [{"close": 9.0, "high": 9.5, "low": 8.5}]
    assert columns.hour_high_low({"5m": fine, "1h": coarse})["high"] == 130.0


def test_hour_high_low_is_total():
    assert columns.hour_high_low({}) == {"high": None, "low": None}
    assert columns.hour_high_low(None) == {"high": None, "low": None}
    assert columns.hour_high_low({"5m": []}) == {"high": None, "low": None}
    assert columns.hour_high_low({"5m": ["junk", {"high": "x", "low": None}]}) == {"high": None, "low": None}


def test_hour_high_low_survives_bars_missing_one_side():
    bars = [{"close": 5.0, "high": 6.0}, {"close": 5.0, "low": 4.0}]
    assert columns.hour_high_low({"5m": bars}) == {"high": 6.0, "low": 4.0}


def test_contract_row_carries_every_field_build_row_produces():
    """The payload contract must not silently drop a builder's field.

    2026-09-02: hour_high_low was added to build_row, tested, and shipped -
    and never reached the browser, because board._contract_row re-keys rows
    into an explicit contract and nobody added it there. The card showed "--"
    through a worker restart and three rebuilds while every unit test passed.

    This fails the moment build_row grows a field the wire will not carry.
    """
    from momx import board

    built = columns.build_row("NVDA", {"5m": [bar(10.0) for _ in range(30)]})
    shipped = board._contract_row(built, True, ["macd:4h"])
    # m5Bear is the one builder field that ships under another name: the bear
    # verdict block (spec 2026-09-24). Pin that it really is carried there.
    assert shipped["bear"]["m5"] is built["m5Bear"]
    missing = sorted(set(built) - set(shipped) - {"m5Bear"})
    assert missing == [], f"build_row produces {missing}, which _contract_row drops"



def test_build_row_carries_a_bear_momentum_summary_beside_the_bull_one():
    """BEAR scanner (spec 2026-09-24): m5Bear is momentum.summarize(direction="bear")."""
    et_midnight = 1788408000
    five = [bar(100.0 - i * 0.2, high=100.3 - i * 0.2, low=99.7 - i * 0.2, volume=1000.0,
                time=et_midnight + 9 * 3600 + i * 300) for i in range(40)]
    row = columns.build_row("TEST", {"5m": five, "D": [bar(100.0, volume=1000.0, time=et_midnight)]})
    assert row["m5"]["direction"] == "bull"
    assert row["m5Bear"]["direction"] == "bear"
    assert row["m5Bear"]["trigger"] <= row["m5"]["trigger"]
