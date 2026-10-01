"""RVOL pace: letting a forming bucket speak before it closes.

THE BUG THIS EXISTS FOR (measured live 2026-09-01)
    RVOL divides a still-forming bucket's volume by an average of COMPLETE
    buckets. Five minutes into a thirty-minute bucket, a stock trading at
    exactly twice its normal rate reads 0.33 -- not 2.0 -- so a 2.0 alert
    threshold cannot be crossed until the bucket is nearly closed. CRML's
    09:30 bucket finished at 23.4x normal volume and the trader was not shown
    it until 10:00, by which time the move was over. His words: "I want to
    catch the momentum trade, you give me scan result move already done."

    ``pace`` re-expresses the ratio as a full-bucket rate so the comparison is
    like-for-like while the bucket is still running.

THE DANGEROUS DIRECTION
    Dividing by a small elapsed fraction amplifies. One minute into thirty
    divides by 0.033, so ordinary volume becomes a 30x "spike" and the
    trader's phone buzzes for nothing. Several tests below exist only to pin
    the guard that stops that, and to pin that the guard withholds pace
    (``None``) rather than silently substituting a rescued number -- the
    consumer must be able to tell "no answer" from "an answer".
"""

from __future__ import annotations

from momx import columns
from test_momx_columns import ratio_tape, zscore_tape


def tapes_with(fine_bars_in_last_bucket, *, coarse="30m", fine="5m", ratio=1.0):
    """A coarse tape whose newest bucket contains ``n`` fine bars.

    Only ``time`` and ``volume`` matter to the elapsed count, but real cells
    are built from these so the tape has to survive the whole column path.
    """
    coarse_bars = ratio_tape(ratio)
    span = columns.RVOL_BUCKET_SECONDS[coarse]
    fine_span = columns.RVOL_BUCKET_SECONDS[fine]
    # Anchor the coarse tape's newest bucket at a known start, then lay the
    # requested number of fine bars inside it.
    start = 10_000_000
    for index, row in enumerate(coarse_bars):
        row["time"] = start - (len(coarse_bars) - 1 - index) * span
    fine_bars = [
        dict(coarse_bars[-1], time=start + i * fine_span)
        for i in range(fine_bars_in_last_bucket)
    ]
    return {coarse: coarse_bars, fine: fine_bars}


# ---------------------------------------------------------------------------
# the elapsed fraction itself
# ---------------------------------------------------------------------------

def test_elapsed_counts_constituent_bars_not_the_wall_clock():
    # 3 five-minute bars inside a thirty-minute bucket = 15 of 30 minutes.
    tapes = tapes_with(3)
    fraction = columns._bucket_elapsed_fraction(tapes, tapes["30m"], "30m")
    assert fraction == 0.5


def test_a_full_bucket_is_one_not_more():
    # 8 fine bars would be 40 of 30 minutes; a fraction above 1.0 would DEFLATE
    # pace below the true ratio, so it must clamp.
    tapes = tapes_with(8)
    assert columns._bucket_elapsed_fraction(tapes, tapes["30m"], "30m") == 1.0


def test_no_finer_tape_means_no_answer():
    # Asking a 5m tape how full its own 5m bar is has no data-derived answer.
    tapes = tapes_with(3)
    assert columns._bucket_elapsed_fraction(tapes, tapes["5m"], "5m") is None


def test_daily_has_no_intraday_span_so_no_pace():
    tapes = tapes_with(3)
    assert columns._bucket_elapsed_fraction(tapes, tapes["30m"], "D") is None


def test_a_bucket_with_no_fine_bars_inside_is_unknowable():
    tapes = tapes_with(0)
    assert columns._bucket_elapsed_fraction(tapes, tapes["30m"], "30m") is None


# ---------------------------------------------------------------------------
# the pace value and its guard
# ---------------------------------------------------------------------------

def test_pace_restores_a_half_finished_bucket_to_its_true_rate():
    # Half the bucket elapsed and reading 1.0 means it is pacing at 2.0.
    assert columns._pace_value(1.0, 0.5) == 2.0


def test_pace_equals_value_once_the_bucket_is_complete():
    assert columns._pace_value(2.4, 1.0) == 2.4


def test_a_barely_started_bucket_publishes_NO_pace_rather_than_a_huge_one():
    # This is the guard that keeps the trader's phone honest. One minute into
    # thirty is 0.033 elapsed; without the floor an ordinary 1.0 would be
    # published as 30.0 and would push as a violent spike.
    assert columns.RVOL_PACE_MIN_ELAPSED > 0.033
    assert columns._pace_value(1.0, 0.033) is None


def test_the_floor_is_low_enough_to_catch_an_0930_move_early():
    # A 30m bucket must be able to speak within about 5 minutes, or the whole
    # feature fails at its one job. 5/30 = 0.1667.
    assert columns._pace_value(1.0, 5.0 / 30.0) is not None


def test_missing_or_malformed_elapsed_is_no_answer_not_a_default():
    for bad in (None, "", "abc", float("nan"), float("inf")):
        assert columns._pace_value(2.0, bad) is None


def test_a_non_finite_ratio_never_becomes_a_pace():
    assert columns._pace_value(float("nan"), 0.5) is None


# ---------------------------------------------------------------------------
# the cell contract the alert path depends on
# ---------------------------------------------------------------------------

def test_the_cell_carries_pace_without_disturbing_the_displayed_value():
    # The displayed value is verified thinkScript parity and must not move
    # just because pace rides along with it.
    plain = columns.rvol_cell(zscore_tape(2.2))
    paced = columns.rvol_cell(zscore_tape(2.2), elapsed=0.5)
    assert plain["value"] == paced["value"] == 2.2
    assert plain["bg"] == paced["bg"] and plain["fg"] == paced["fg"]
    assert plain["pace"] is None
    # Pace is NOT the value scaled: a z-score is not linear in volume, so it
    # is recomputed from the projected volume. Half-elapsed means the bucket
    # is on track for twice the volume, which scores strictly higher than 2.2
    # but nowhere near the 4.4 a naive doubling would have produced.
    assert paced["pace"] > 2.2
    assert paced["pace"] != 4.4


def test_an_empty_tape_is_still_exactly_the_null_cell():
    # Pinned elsewhere too, but pace must not sneak a key into the null cell.
    assert columns.rvol_cell([]) == columns.null_cell()
    assert "pace" not in columns.null_cell()
