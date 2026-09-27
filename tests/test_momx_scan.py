"""Parity tests for the "AlertX Bull Momo ALL_Jan26_MyWatchlist" scan.

Every fixture here is hand-built and deterministic. The two tests that matter
most for parity are:

* ``test_daily_momentum_cross_agrees_with_validated_engine`` - the daily-and-
  slower crosses must equal what ``ganesh_higher_timeframe_signals`` (the
  chart-validated replay) computes, reached through that module's OWN daily
  grouping path rather than through ``momx.scan``.
* ``test_scanner_and_column_squeeze_versions_disagree`` - the trader supplied
  TWO squeeze scripts that differ in two places. This test pins a tape where
  they give opposite answers so nobody can "unify" them later.
"""

from __future__ import annotations

import sys

import ganesh_higher_timeframe_signals as ghts
from momx import scan
from momx.indicators import ttm_squeeze


# ----------------------------------------------------------------------
# fixtures
# ----------------------------------------------------------------------


def _bars_from_closes(closes: list[float], volume: float = 1000.0) -> list[dict]:
    """A bar tape whose OHLC all hang off the close (only closes matter here)."""
    return [
        {
            "time": 1_700_000_000 + index * 3600,
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": volume,
        }
        for index, close in enumerate(closes)
    ]


def _turn_closes() -> list[float]:
    """60 falling bars then a rally.

    Under the repo's first-value-seeded EMAs this tape fires exactly one of
    the three momentum crosses on each of bars 60 (MACD), 63 (EMA 4x8) and
    68 (EMA 9x20), and none at bar 70 - which is what lets each cross be
    tested independently.
    """
    closes = [100.0 - 0.5 * index for index in range(60)]
    price = closes[-1]
    for _ in range(40):
        price = round(price + 0.9, 4)
        closes.append(price)
    return closes


TURN_CLOSES = _turn_closes()
MACD_ONLY_INDEX = 60
EMA_4X8_ONLY_INDEX = 63
EMA_9X20_ONLY_INDEX = 68
NO_CROSS_INDEX = 70


def _squeeze_tape() -> list[dict]:
    """45 dead-flat bars (in squeeze), one expansion bar, one gap-down bar.

    Bar 45 releases the squeeze with a rising TTM histogram, so both squeeze
    scripts fire there. Bar 46 is still inside the two-bar fired window but
    its HIGH does not exceed bar 45's CLOSE, which is exactly the input the
    scanner script's ``MFD[1] and higher`` kills and the column script's
    ``MFD[1]`` does not.
    """
    bars = [
        {
            "time": 1_700_000_000 + index * 7200,
            "open": 100.0,
            "high": 100.05,
            "low": 99.95,
            "close": 100.0 + (0.02 if index % 2 else -0.02),
            "volume": 1000.0,
        }
        for index in range(45)
    ]
    bars.append(
        {
            "time": 1_700_000_000 + 45 * 7200,
            "open": 100.0,
            "high": 110.0,
            "low": 99.9,
            "close": 109.0,
            "volume": 5000.0,
        }
    )
    bars.append(
        {
            "time": 1_700_000_000 + 46 * 7200,
            "open": 105.0,
            "high": 105.0,
            "low": 104.0,
            "close": 104.5,
            "volume": 4000.0,
        }
    )
    return bars


SQUEEZE_TAPE = _squeeze_tape()
SQUEEZE_RELEASE_INDEX = 45
SQUEEZE_DISAGREE_INDEX = 46


def _column_version_sqz_fired(bars: list[dict]) -> bool:
    """The WATCHLIST COLUMN squeeze script, deliberately re-implemented here.

    This is NOT the function under test and it does not live in ``momx.scan``.
    It exists so the two scripts can be compared. It differs from the scanner
    version in exactly the two places the trader's source differs:
    ``high > close[2]`` instead of ``close[1]``, and ``MFD[1]`` instead of
    ``MFD[1] and higher``.
    """
    highs = [float(bar["high"]) for bar in bars]
    lows = [float(bar["low"]) for bar in bars]
    closes = [float(bar["close"]) for bar in bars]
    count = len(bars)
    if count < 2:
        return False
    medium = ttm_squeeze(highs, lows, closes, 20, 1.5, 2.0)
    high_compression = ttm_squeeze(highs, lows, closes, 20, 1.0, 2.0)

    msf = hsf = 0
    mfd = hfd = False
    fired = False
    previous_ms = previous_hs = False
    for index in range(count):
        in_medium = medium.squeeze_alert[index] == 0
        in_high = high_compression.squeeze_alert[index] == 0
        medium_fire = (not in_medium) and previous_ms
        high_fire = (not in_high) and previous_hs
        next_msf = msf + 1 if medium_fire or ((not in_medium) and 0 < msf < 2) else 0
        next_hsf = hsf + 1 if high_fire or ((not in_high) and 0 < hsf < 2) else 0
        medium_direction = index > 0 and (
            medium.histogram[index] > medium.histogram[index - 1]
        )
        high_direction = index > 0 and (
            high_compression.histogram[index] > high_compression.histogram[index - 1]
        )
        mfd = medium_direction if medium_fire else mfd
        hfd = high_direction if high_fire else hfd
        msf, hsf = next_msf, next_hsf
        previous_ms, previous_hs = in_medium, in_high
        fired = bool(hsf and hfd) or bool(msf and mfd)
    return fired


def _rvol_bars(volumes: list[float], *, bullish: bool = True) -> list[dict]:
    bars = []
    for index, volume in enumerate(volumes):
        bars.append(
            {
                "time": 1_700_000_000 + index * 300,
                "open": 10.5,
                "high": 11.0,
                "low": 10.0,
                "close": 10.9 if bullish else 10.1,
                "volume": volume,
            }
        )
    return bars


# ----------------------------------------------------------------------
# price floor
# ----------------------------------------------------------------------


def test_price_floor_exactly_three_dollars_passes():
    assert scan.price_floor(3.00) is True


def test_price_floor_just_below_three_dollars_fails():
    assert scan.price_floor(2.9999) is False


def test_price_floor_rejects_missing_price():
    assert scan.price_floor(None) is False


# ----------------------------------------------------------------------
# Price_Change gate
# ----------------------------------------------------------------------


# ---------------------------------------------------------------------------
# The 4h volume gate's alignment - settled by ground truth 2026-09-03
#
# He ran his TOS scan twice and sent the results:
#     18:15 ET -> 10 matches inc. WELL and REGN
#     18:21 ET ->  9 matches inc. WELL and REGN, with CHTR dropping out
#
# Our shipped `volume[2]` rejected WELL and REGN in BOTH readings. Sweeping
# bars-ago x session x forming-bar, only bars_ago=3 reproduces them: 10/10 and
# 9/9, and it correctly rejects the CHTR that TOS dropped. 19 positives and one
# true negative, zero errors.
#
# Cause: our 4h tape builds a bucket wherever bars exist, so a thin overnight
# window becomes a full bar and our series carries one more bucket than his -
# exactly one index of offset.
# ---------------------------------------------------------------------------


def test_the_volume_row_compares_against_three_bars_back():
    assert scan.VOLUME_CHANGE_BARS_AGO == 3


def test_the_close_row_still_compares_against_two():
    """Measured, not assumed: shifting this one too gave 7/9 of his matches
    and MORE rows, against 8/9 and fewer at 2."""
    assert scan.CLOSE_CHANGE_BARS_AGO == 2


def _vol_tape(volumes):
    return [{"open": 10.0, "high": 10.4, "low": 9.6, "close": 10.35, "volume": v}
            for v in volumes]


def test_the_gate_reads_the_fourth_bar_back_not_the_third():
    """Pins WHICH bar is the baseline, with the two candidates disagreeing.

    volumes: [2_000_000, 1_000, 5_000_000, 900, 1_000_000]
                          ^3 back   ^2 back            ^current
    Against the 1_000 three back the last bar is +99,900% (pass); against the
    5_000_000 two back it is -80% (fail). Only bars_ago = 3 passes.
    """
    tape = _vol_tape([2_000_000, 1_000, 5_000_000, 900, 1_000_000])
    assert scan.price_change_gate(tape, "volume", 0.5, 3) is True
    assert scan.price_change_gate(tape, "volume", 0.5, 2) is False
    # and the shipped constant picks the passing one
    assert scan.price_change_gate(
        tape, "volume", scan.VOLUME_CHANGE_MIN_PCT, scan.VOLUME_CHANGE_BARS_AGO
    ) is True


def test_the_well_and_regn_shape_now_passes():
    """The exact failure his readings exposed.

    WELL at 18:16: current bucket 1,673,489 against a 1,853,178 baseline three
    buckets back is -9.7% and was BLOCKED, while TOS matched it. One bucket
    further back is the thin overnight window that TOS's series does not carry.
    """
    tape = _vol_tape([900_000, 50_000, 1_853_178, 1_000_000, 1_673_489])
    #                            ^3 back  ^2 back                ^current
    assert scan.price_change_gate(tape, "volume", 0.5, 2) is False   # -9.7%
    assert scan.price_change_gate(tape, "volume", 0.5, 3) is True    # vs the
    # thin overnight bucket his series does not carry


def test_there_is_no_time_of_day_exception_any_more():
    """b0b9f90 skipped the gate in the 17:00/21:00 buckets. His readings then
    returned 9-10 matches in the evening WITH the gate applied, so the
    blackout was our alignment, not the gate. The workaround is gone."""
    assert not hasattr(scan, "VOLUME_GATE_BLIND_BUCKET_HOURS")
    assert not hasattr(scan, "_volume_gate_applies")
    # An evening tape that fails the gate is blocked like any other.
    failing = {"4h": _vol_tape([5_000_000, 4_000_000, 3_000_000, 10.0])}
    _, reasons = scan.scan_symbol(failing, 50.0)
    assert "blocked:pricechange:volume:4h" in reasons


def test_price_change_gate_exactly_half_percent_passes():
    bars = [
        {"close": 10.0, "volume": 1000.0},
        {"close": 10.0, "volume": 900.0},
        {"close": 10.0, "volume": 1005.0},
    ]
    assert scan.price_change_gate(bars, "volume", 0.5) is True


def test_price_change_gate_just_under_threshold_fails():
    bars = [
        {"close": 10.0, "volume": 1000.0},
        {"close": 10.0, "volume": 900.0},
        {"close": 10.0, "volume": 1004.9},
    ]
    assert scan.price_change_gate(bars, "volume", 0.5) is False


def test_price_change_gate_zero_baseline_is_false_not_a_crash():
    bars = [
        {"close": 10.0, "volume": 0.0},
        {"close": 10.0, "volume": 5.0},
        {"close": 10.0, "volume": 900_000.0},
    ]
    assert scan.price_change_gate(bars, "volume", 0.5) is False


def test_price_change_gate_reads_the_close_source():
    bars = [
        {"close": 100.0, "volume": 1.0},
        {"close": 100.0, "volume": 1.0},
        {"close": 100.4, "volume": 1.0},
    ]
    assert scan.price_change_gate(bars, "close", 0.3) is True
    assert scan.price_change_gate(bars, "close", 0.5) is False


def test_price_change_gate_applies_no_epsilon_to_the_threshold():
    """The comparison is plain IEEE-754, exactly as TOS compares doubles.

    100 * (100.3 - 100) / 100 evaluates to 0.29999999999999716 in binary
    floating point, so a "0.3%" reading built this way does NOT clear a 0.3
    threshold. No tolerance is added to paper over that - TOS is doing the
    same double arithmetic, and inventing an epsilon here would let rows
    through that TOS blocks.
    """
    bars = [
        {"close": 100.0, "volume": 1.0},
        {"close": 100.0, "volume": 1.0},
        {"close": 100.3, "volume": 1.0},
    ]
    assert scan.price_change_gate(bars, "close", 0.3) is False


def test_price_change_gate_needs_enough_bars():
    bars = [{"close": 10.0, "volume": 1000.0}, {"close": 11.0, "volume": 2000.0}]
    assert scan.price_change_gate(bars, "close", 0.3) is False


# ----------------------------------------------------------------------
# momentum crosses
# ----------------------------------------------------------------------


def test_momentum_cross_fires_on_macd_alone():
    bars = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    assert scan.momentum_cross(bars) is True
    assert scan.momentum_cross_names(bars) == ["macd"]


def test_momentum_cross_fires_on_ema_4x8_alone():
    bars = _bars_from_closes(TURN_CLOSES[: EMA_4X8_ONLY_INDEX + 1])
    assert scan.momentum_cross(bars) is True
    assert scan.momentum_cross_names(bars) == ["ema4x8"]


def test_momentum_cross_fires_on_ema_9x20_alone():
    bars = _bars_from_closes(TURN_CLOSES[: EMA_9X20_ONLY_INDEX + 1])
    assert scan.momentum_cross(bars) is True
    assert scan.momentum_cross_names(bars) == ["ema9x20"]


def test_momentum_cross_is_silent_when_fast_was_already_above():
    bars = _bars_from_closes(TURN_CLOSES[: NO_CROSS_INDEX + 1])
    assert scan.momentum_cross(bars) is False
    assert scan.momentum_cross_names(bars) == []


def test_momentum_cross_needs_two_bars():
    assert scan.momentum_cross(_bars_from_closes([10.0])) is False
    assert scan.momentum_cross([]) is False


def test_momentum_cross_has_no_within_bars_lookback():
    """A cross one bar ago must NOT still report on the next bar.

    The Skittles COLUMN wraps its crosses in "within 1 bars"; this scan does
    not. Bar 61 sits one bar after the MACD cross at bar 60 and must be silent.
    """
    bars = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 2])
    assert scan.momentum_cross_names(bars) == []


def _validated_daily_cross_names(daily_bars: list[dict], upto: int) -> list[str]:
    """Cross names for the last bar, computed through ghts' OWN daily replay."""
    window = daily_bars[: upto + 1]
    states = ghts._literal_secondary_states_by_date(window, window)["D"]
    snapshot = states[str(window[-1]["date"])]
    base, current = snapshot["base"], snapshot["current"]
    if not base or not current:
        return []
    names = []
    if ghts._crossed_above(
        base["macdValue"],
        base["macdAverage"],
        current["macdValue"],
        current["macdAverage"],
    ):
        names.append("macd")
    if ghts._crossed_above(
        base["ema9"], base["ema20"], current["ema9"], current["ema20"]
    ):
        names.append("ema9x20")
    if ghts._crossed_above(
        base["ema4"], base["ema8"], current["ema4"], current["ema8"]
    ):
        names.append("ema4x8")
    return names


def test_daily_momentum_cross_agrees_with_validated_engine():
    """momx.scan must match ganesh_higher_timeframe_signals bar for bar on D."""
    import pandas as pd

    sessions = pd.bdate_range(end="2026-08-21", periods=len(TURN_CLOSES))
    daily_bars = [
        {
            "time": int(pd.Timestamp(f"{stamp.date()}T20:00:00Z").timestamp()),
            "date": stamp.strftime("%Y-%m-%d"),
            "open": close,
            "high": close + 0.5,
            "low": close - 0.5,
            "close": close,
            "volume": 1000.0,
        }
        for stamp, close in zip(sessions, TURN_CLOSES)
    ]
    disagreements = []
    fired = 0
    for index in range(1, len(daily_bars)):
        expected = _validated_daily_cross_names(daily_bars, index)
        actual = scan.momentum_cross_names(daily_bars[: index + 1])
        fired += 1 if expected else 0
        if expected != actual:
            disagreements.append((index, expected, actual))
    assert disagreements == []
    assert fired >= 3, "fixture must actually produce crosses to be meaningful"


# ----------------------------------------------------------------------
# squeeze
# ----------------------------------------------------------------------


def test_sqz_fired_true_on_the_release_bar():
    assert scan.sqz_fired(SQUEEZE_TAPE[: SQUEEZE_RELEASE_INDEX + 1]) is True


def test_sqz_fired_false_while_still_compressed():
    assert scan.sqz_fired(SQUEEZE_TAPE[:SQUEEZE_RELEASE_INDEX]) is False


def test_sqz_fired_false_on_a_short_tape():
    assert scan.sqz_fired(SQUEEZE_TAPE[:5]) is False
    assert scan.sqz_fired([]) is False


def test_scanner_and_column_squeeze_versions_disagree():
    """GUARD: do not "unify" the two squeeze scripts.

    On this tape the column version still reports fired (its MFD latches) and
    the scanner version does not (its MFD is ANDed with ``high > close[1]``,
    which this gap-down bar fails).
    """
    tape = SQUEEZE_TAPE[: SQUEEZE_DISAGREE_INDEX + 1]
    scanner = scan.sqz_fired(tape)
    column = _column_version_sqz_fired(tape)
    assert scanner is False
    assert column is True
    assert scanner != column


def test_the_two_squeeze_versions_agree_on_the_release_bar():
    tape = SQUEEZE_TAPE[: SQUEEZE_RELEASE_INDEX + 1]
    assert scan.sqz_fired(tape) is True
    assert _column_version_sqz_fired(tape) is True


# ----------------------------------------------------------------------
# RVOL scanner
# ----------------------------------------------------------------------


def test_rvol_scan_true_on_a_bullish_volume_spike():
    bars = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    assert scan.rvol_scan(bars) is True


def test_rvol_scan_false_when_only_the_zscore_fails():
    bars = _rvol_bars([1000.0] * 50 + [1000.0], bullish=True)
    assert scan.rvol_scan(bars) is False


def test_rvol_scan_false_when_only_buying_versus_selling_fails():
    bars = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=False)
    assert scan.rvol_scan(bars) is False


def test_rvol_scan_false_when_close_sits_exactly_midrange():
    bars = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    bars[-1] = {**bars[-1], "close": 10.5}
    assert scan.rvol_scan(bars) is False


def test_rvol_scan_flat_bar_does_not_raise():
    bars = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    bars[-1] = {**bars[-1], "high": 10.0, "low": 10.0, "close": 10.0, "open": 10.0}
    assert scan.rvol_scan(bars) is False


def test_rvol_scan_short_tape_is_false():
    assert scan.rvol_scan(_rvol_bars([1000.0] * 3)) is False
    assert scan.rvol_scan([]) is False


# ----------------------------------------------------------------------
# scan_symbol
# ----------------------------------------------------------------------


def _passing_gate_tapes() -> dict[str, list[dict]]:
    # FOUR bars: VOLUME_CHANGE_BARS_AGO became 3 on 2026-09-03 (his TOS
    # ground truth), so the baseline is now the bar four back, not three.
    four_hour = [
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 1000.0},
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 1500.0},
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 900.0},
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 2000.0},
    ]
    one_hour = [
        {"close": 100.0, "high": 100.1, "low": 99.9, "open": 100.0, "volume": 10.0},
        {"close": 100.0, "high": 100.1, "low": 99.9, "open": 100.0, "volume": 10.0},
        {"close": 101.0, "high": 101.1, "low": 99.9, "open": 100.0, "volume": 10.0},
    ]
    return {"4h": four_hour, "1h": one_hour}


def test_scan_symbol_passes_when_all_gates_and_one_or_condition_fire():
    tapes = _passing_gate_tapes()
    tapes["D"] = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    passed, reasons = scan.scan_symbol(tapes, 50.0)
    assert passed is True
    assert "macd:D" in reasons


def test_scan_symbol_reasons_name_the_specific_cross_and_timeframe():
    tapes = _passing_gate_tapes()
    tapes["2h"] = _bars_from_closes(TURN_CLOSES[: EMA_4X8_ONLY_INDEX + 1])
    tapes["Wk"] = _bars_from_closes(TURN_CLOSES[: EMA_9X20_ONLY_INDEX + 1])
    tapes["30m"] = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    passed, reasons = scan.scan_symbol(tapes, 50.0)
    assert passed is True
    assert "ema4x8:2h" in reasons
    assert "ema9x20:Wk" in reasons
    assert "rvol:30m" in reasons
    assert not any(reason.startswith("momentum:") for reason in reasons)


def test_scan_symbol_price_floor_failure_blocks_a_row_full_of_or_hits():
    tapes = _passing_gate_tapes()
    tapes["2h"] = _bars_from_closes(TURN_CLOSES[: EMA_4X8_ONLY_INDEX + 1])
    tapes["D"] = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    tapes["Wk"] = _bars_from_closes(TURN_CLOSES[: EMA_9X20_ONLY_INDEX + 1])
    tapes["30m"] = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    passed, reasons = scan.scan_symbol(tapes, 2.50)
    assert passed is False
    assert "blocked:price" in reasons
    assert "ema4x8:2h" in reasons  # the OR hits are still reported for auditing


def test_scan_symbol_price_change_gate_failure_blocks_a_row():
    tapes = _passing_gate_tapes()
    tapes["4h"] = [
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 1000.0},
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 900.0},
        {"close": 10.0, "high": 10.1, "low": 9.9, "open": 10.0, "volume": 1000.0},
    ]
    tapes["D"] = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    passed, reasons = scan.scan_symbol(tapes, 50.0)
    assert passed is False
    assert "blocked:pricechange:volume:4h" in reasons


def test_scan_symbol_fails_when_no_or_condition_fires():
    tapes = _passing_gate_tapes()
    tapes["D"] = _bars_from_closes(TURN_CLOSES[: NO_CROSS_INDEX + 1])
    passed, reasons = scan.scan_symbol(tapes, 50.0)
    assert passed is False
    assert reasons == []


def test_scan_symbol_ignores_timeframes_outside_each_condition_list():
    """RVOL is not scanned on 2D and momentum is not scanned on 5m."""
    tapes = _passing_gate_tapes()
    tapes["5m"] = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    tapes["2D"] = _rvol_bars([1000.0] * 50 + [50_000.0], bullish=True)
    passed, reasons = scan.scan_symbol(tapes, 50.0)
    assert passed is False
    assert reasons == []


def test_scan_symbol_tolerates_missing_tapes():
    passed, reasons = scan.scan_symbol({}, 50.0)
    assert passed is False
    assert "blocked:pricechange:volume:4h" in reasons


# ----------------------------------------------------------------------
# ranking
# ----------------------------------------------------------------------


def test_rank_rows_sorts_by_pct_change_descending():
    rows = [
        {"symbol": "A", "pctChange": 1.0},
        {"symbol": "B", "pctChange": 9.0},
        {"symbol": "C", "pctChange": 5.0},
    ]
    assert [row["symbol"] for row in scan.rank_rows(rows)] == ["B", "C", "A"]


def test_rank_rows_caps_at_the_limit():
    rows = [{"symbol": str(index), "pctChange": float(index)} for index in range(80)]
    ranked = scan.rank_rows(rows)
    assert len(ranked) == 50
    assert ranked[0]["symbol"] == "79"
    assert scan.rank_rows(rows, limit=3)[0]["symbol"] == "79"
    assert len(scan.rank_rows(rows, limit=3)) == 3


def test_rank_rows_puts_nulls_last_and_keeps_their_order():
    rows = [
        {"symbol": "N1", "pctChange": None},
        {"symbol": "A", "pctChange": -2.0},
        {"symbol": "N2", "pctChange": None},
        {"symbol": "B", "pctChange": 4.0},
    ]
    assert [row["symbol"] for row in scan.rank_rows(rows)] == ["B", "A", "N1", "N2"]


def test_rank_rows_does_not_mutate_the_input():
    rows = [{"symbol": "A", "pctChange": 1.0}, {"symbol": "B", "pctChange": 2.0}]
    scan.rank_rows(rows)
    assert [row["symbol"] for row in rows] == ["A", "B"]


def test_rank_rows_limit_none_returns_everything():
    rows = [{"symbol": str(index), "pctChange": float(index)} for index in range(60)]
    assert len(scan.rank_rows(rows, limit=None)) == 60


# ----------------------------------------------------------------------
# import hygiene
# ----------------------------------------------------------------------


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


def test_scan_module_does_not_pull_in_api_server():
    """momx.scan must not drag in the 1MB api_server module.

    Asked of a FRESH interpreter. Asking the pytest process instead makes
    this assert whichever test ran first, which is why it used to fail in
    the full suite and pass alone.
    """
    assert not _imports_api_server_in_a_fresh_interpreter("momx.scan")


# ---------------------------------------------------------------------------
# rank_board: a display cap must never delete a scan match
#
# Regression for 2026-08-27: on the 355-name watchlist TOS found 16 matches and
# the board showed 2. build_board ranked ALL rows by %change and cut to 50, so
# every match that was not also a top-50 mover vanished before the UI saw it -
# TXN +1.82%, MS +0.36%, KVUE -0.10%, CAR -3.13% and six more, all of which pass
# the scan when built on their own.
# ---------------------------------------------------------------------------

def _rank_row(symbol, pct, passed):
    return {"symbol": symbol, "pctChange": pct, "scanPass": passed}


def test_rank_board_keeps_a_match_over_bigger_non_matches():
    rows = [_rank_row(f"HOT{i}", 50.0 - i, False) for i in range(60)]
    rows.append(_rank_row("CAR", -3.13, True))
    kept = scan.rank_board(rows, limit=50)
    symbols = [row["symbol"] for row in kept]
    assert "CAR" in symbols, "a scan match was cut by non-matching movers"
    assert symbols[0] == "CAR", "matches must rank ahead of non-matches"
    assert len(kept) == 50


def test_rank_board_keeps_every_match_that_fits_the_cap():
    rows = [_rank_row(f"M{i}", -float(i), True) for i in range(16)]
    rows += [_rank_row(f"BIG{i}", 90.0 + i, False) for i in range(100)]
    kept = scan.rank_board(rows, limit=50)
    matched = [row["symbol"] for row in kept if row["scanPass"]]
    assert len(matched) == 16, "TOS showed 16 of 16; none may be dropped"


def test_rank_board_orders_matches_by_pct_change_desc():
    rows = [_rank_row("A", 1.0, True), _rank_row("B", 9.0, True), _rank_row("C", -2.0, True)]
    assert [r["symbol"] for r in scan.rank_board(rows, limit=50)] == ["B", "A", "C"]


def test_rank_board_never_truncates_matches_and_still_orders_them():
    """``limit`` FLOORS the row count for matches, it does not cap it.

    Until 2026-09-02 matches 51..N were cut here and shipped in ``rest``,
    where matchedSince stamping, the history archive and the Momo phone
    alerts - all of which read ``rows`` - could not see them.
    """
    rows = [_rank_row(f"M{i}", float(i), True) for i in range(60)]
    kept = scan.rank_board(rows, limit=50)
    assert len(kept) == 60
    assert kept[0]["symbol"] == "M59"


def test_rank_board_limit_none_returns_everything_matches_first():
    rows = [_rank_row("X", 99.0, False), _rank_row("Y", -1.0, True)]
    assert [r["symbol"] for r in scan.rank_board(rows, limit=None)] == ["Y", "X"]


def test_rank_board_respects_the_cap_when_matches_fill_it():
    """Regression: a full cap must not become NO cap.

    rank_rows treats limit<=0 as "return everything" (that is how a caller asks
    for the whole board). rank_board passed its leftover room straight through,
    so once matches filled the cap, room hit 0 and every non-matching row came
    back. Measured 2026-08-28: 357 rows with 119 matches and limit=50 returned
    288 rows. It only shows up on a busy session, which is the worst time.
    """
    rows = [_rank_row("M%03d" % i, float(i), True) for i in range(119)]
    rows += [_rank_row("N%03d" % i, float(i), False) for i in range(238)]
    kept = scan.rank_board(rows, limit=50)
    # Since 2026-09-02 every match is kept, so the count is the match count -
    # but the leak this test exists for (non-matching rows coming back once
    # room hits 0) must still be impossible.
    assert len(kept) == 119
    assert all(row["scanPass"] for row in kept), "not one non-matching row may leak in"


def test_rank_board_fills_leftover_room_with_non_matches():
    rows = [_rank_row("M%03d" % i, float(i), True) for i in range(10)]
    rows += [_rank_row("N%03d" % i, float(i), False) for i in range(238)]
    kept = scan.rank_board(rows, limit=50)
    assert len(kept) == 50
    assert sum(1 for row in kept if row["scanPass"]) == 10


def test_completed_bars_drops_a_forming_final_bucket_only():
    """The 09:23 open-bell blackout (2026-08-31): the developing 4h bucket's
    23 minutes of volume was gated against a FULL prior bucket, blocking all
    ten Mag7 names while TOS passed AAPL. Gates read completed bars only."""
    now = 1_900_000_000.0
    span = 240  # minutes
    forming = {"time": now - 20 * 60, "volume": 100.0, "close": 10.0}
    done1 = {"time": now - 20 * 60 - span * 60, "volume": 5000.0, "close": 9.0}
    done2 = {"time": now - 20 * 60 - 2 * span * 60, "volume": 4000.0, "close": 8.0}
    tape = [done2, done1, forming]
    out = scan.completed_bars(tape, span, now)
    assert out == [done2, done1]          # forming bucket dropped
    # a tape whose last bucket is complete is untouched
    closed_tape = [done2, done1]
    assert scan.completed_bars(closed_tape, span, now) == closed_tape
    # empty and None are safe
    assert scan.completed_bars([], span, now) == []
    assert scan.completed_bars(None, span, now) == []


# ----------------------------------------------------------------------
# BEAR direction - the bull scan mirrored (spec 2026-09-24)
# ----------------------------------------------------------------------

from momx_mirror import mirror_bars, mirror_price, mirror_tapes  # noqa: E402  (tests/ is on sys.path)


def test_bear_momentum_cross_is_the_mirror_of_the_bull_cross():
    bars = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    assert scan.momentum_cross_names(bars) == ["macd"]
    assert scan.momentum_cross_names(mirror_bars(bars), direction="bear") == ["macd"]
    assert scan.momentum_cross_names(bars, direction="bear") == []
    assert scan.momentum_cross(mirror_bars(bars), direction="bear") is True


def test_bear_rvol_scan_wants_sellers():
    volumes = [1000.0] * 60 + [100_000.0]
    bull_bar = _rvol_bars(volumes, bullish=True)
    bear_bar = _rvol_bars(volumes, bullish=False)
    assert scan.rvol_scan(bull_bar, 3.0) is True
    assert scan.rvol_scan(bull_bar, 3.0, direction="bear") is False
    assert scan.rvol_scan(bear_bar, 3.0) is False
    assert scan.rvol_scan(bear_bar, 3.0, direction="bear") is True


def test_bear_close_gate_is_a_drop_of_the_same_size():
    # 0.4%, not 0.3%: 100 -> 100.3 lands at 0.2999...% in IEEE doubles (see the
    # gate's docstring), and this test is about the DIRECTION of the comparison.
    up = _bars_from_closes([100.0, 100.0, 100.4])
    down = _bars_from_closes([100.0, 100.0, 99.6])
    assert scan.price_change_gate(up, "close", 0.3, 2) is True
    assert scan.price_change_gate(up, "close", 0.3, 2, direction="bear") is False
    assert scan.price_change_gate(down, "close", 0.3, 2) is False
    assert scan.price_change_gate(down, "close", 0.3, 2, direction="bear") is True


def test_bear_sqz_fired_is_the_mirror_of_the_bull_fire():
    tape = SQUEEZE_TAPE[: SQUEEZE_RELEASE_INDEX + 1]
    assert scan.sqz_fired(tape) is True
    assert scan.sqz_fired(mirror_bars(tape), direction="bear") is True
    assert scan.sqz_fired(mirror_bars(tape)) is False
    assert scan.sqz_fired(tape, direction="bear") is False


def test_scan_symbol_bear_mirrors_bull_on_a_reflected_tape():
    """The whole scan, reflected around 100: same reasons, same verdict."""
    tapes = _passing_gate_tapes()
    tapes["D"] = _bars_from_closes(TURN_CLOSES[: MACD_ONLY_INDEX + 1])
    bull = scan.scan_symbol(tapes, 50.0)
    bear = scan.scan_symbol(mirror_tapes(tapes, pivot=100.0), mirror_price(50.0, pivot=100.0), direction="bear")
    assert bull[0] is True
    assert bear == bull
    # The bull reading of the mirrored tape is blocked by the 1h close gate.
    assert scan.scan_symbol(mirror_tapes(tapes, pivot=100.0), 150.0)[0] is False


def test_rank_rows_ascending_puts_the_biggest_loser_first():
    rows = [
        {"symbol": "A", "pctChange": -1.0},
        {"symbol": "B", "pctChange": -8.0},
        {"symbol": "C", "pctChange": None},
        {"symbol": "D", "pctChange": 5.0},
    ]
    assert [r["symbol"] for r in scan.rank_rows(rows, None, ascending=True)] == ["B", "A", "D", "C"]
    assert [r["symbol"] for r in scan.rank_rows(rows, None)] == ["D", "A", "B", "C"]
    board = [dict(r, scanPass=r["symbol"] == "D") for r in rows]
    # The match leads even though it is the day's gainer; the room fills with the losers.
    assert [r["symbol"] for r in scan.rank_board(board, 2, ascending=True)] == ["D", "B"]


def test_is_bear_spells_the_direction_once():
    assert scan.is_bear("bear") and scan.is_bear("BEAR")
    assert not scan.is_bear("bull") and not scan.is_bear(None) and not scan.is_bear("")
