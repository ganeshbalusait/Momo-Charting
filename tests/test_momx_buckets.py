"""Bucket-anchoring parity tests for momx.buckets.

Anchoring is the most expensive parity trap in this repo, so these tests pin
the module to the ALREADY-VALIDATED sources rather than to numbers retyped
here: the daily group keys are asserted equal to
``ganesh_higher_timeframe_signals._timeframe_group_key`` directly, and the
intraday clocks are asserted equal to ``chart_aggregation`` -- the Python
mirror of the browser's ``frontend/src/chartAggregation.js``. If either side
moves, these fail instead of the charts silently disagreeing.

The 2D/3D/4D WATCHLIST clock is the exception: it is pinned to a frozen slice
of the real CRWD daily tape and to the Skittles values the trader read off
thinkorswim on 2026-08-27, because that measurement is the only evidence for
it. See ``CRWD_DAILY_TAPE_2026_08_27`` below.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import chart_aggregation
import ganesh_higher_timeframe_signals as ghts
from momx import buckets, columns

EASTERN = ZoneInfo("America/New_York")


def et(year, month, day, hour=0, minute=0) -> int:
    return int(datetime(year, month, day, hour, minute, tzinfo=EASTERN).timestamp())


def et_parts(timestamp: int) -> tuple[int, int]:
    moment = datetime.fromtimestamp(int(timestamp), EASTERN)
    return moment.hour, moment.minute


def thirty_minute_tape(start_epoch: int, count: int) -> list[dict]:
    """A synthetic 30m tape: close walks up by 1, volume is 10 per bar."""
    return [
        {
            "time": start_epoch + index * 1800,
            "open": 100.0 + index,
            "high": 100.5 + index,
            "low": 99.5 + index,
            "close": 100.0 + index,
            "volume": 10.0,
        }
        for index in range(count)
    ]


# ----------------------------------------------------------------------
# Intraday clocks
# ----------------------------------------------------------------------


def test_thirty_minute_tape_folds_into_two_hour_buckets_on_even_eastern_hours() -> None:
    # A full ET calendar day of 30m bars, 00:00 -> 23:30.
    tape = thirty_minute_tape(et(2026, 6, 10), 48)
    folded = buckets.aggregate_intraday(tape, 120)

    assert [et_parts(bar["time"]) for bar in folded] == [
        (hour, 0) for hour in range(0, 24, 2)
    ]
    # Four 30m bars per 2h bucket, and nothing is dropped.
    assert sum(bar["volume"] for bar in folded) == pytest.approx(480.0)


def test_thirty_minute_tape_folds_into_four_hour_buckets_on_the_tos_central_clock() -> None:
    tape = thirty_minute_tape(et(2026, 6, 10), 48)
    folded = buckets.aggregate_intraday(tape, 240)

    hours = [et_parts(bar["time"])[0] for bar in folded]
    # 00:00-00:30 belongs to the PREVIOUS day's 21:00 bucket; the rest anchor
    # at 01/05/09/13/17/21 ET -- never 00/04/08/12/16/20.
    assert hours == [21, 1, 5, 9, 13, 17, 21]
    assert all(minute == 0 for _, minute in (et_parts(bar["time"]) for bar in folded))
    assert not ({0, 4, 8, 12, 16, 20} & set(hours))


def test_intraday_clocks_match_the_repo_chart_aggregation_mirror() -> None:
    """momx must not become a second aggregator that can drift from the chart."""
    tape = thirty_minute_tape(et(2026, 6, 10), 96)
    for minutes in (15, 30, 60, 120, 240):
        folded = buckets.aggregate_intraday(tape, minutes)
        expected = sorted(
            {
                chart_aggregation.chart_aggregation_bucket_time(bar["time"], minutes)
                for bar in tape
            }
        )
        assert [bar["time"] for bar in folded] == expected


@pytest.mark.parametrize(
    ("before", "after"),
    [
        # Spring forward: 2026-03-08. Fall back: 2026-11-01.
        ((2026, 3, 7), (2026, 3, 9)),
        ((2026, 10, 31), (2026, 11, 2)),
    ],
)
def test_dst_boundary_anchors_on_eastern_wall_clock_not_the_utc_offset(
    before, after,
) -> None:
    """04:00 ET stays 04:00 ET on both sides of a daylight-saving change."""
    for span, expected_hour in ((120, 4), (240, 13)):
        probe_hour = 4 if span == 120 else 14
        for day in (before, after):
            bucket_time = buckets.intraday_bucket_time(
                et(*day, probe_hour, 7), span,
            )
            assert et_parts(bucket_time) == (expected_hour, 0)

    # Pure epoch bucketing -- what an unanchored implementation does -- lands on
    # DIFFERENT Eastern wall-clock hours either side of the change, which is the
    # bug this anchoring exists to prevent.
    epoch_hours = {
        et_parts((et(*day, 4, 7) // 7200) * 7200)[0] for day in (before, after)
    }
    assert len(epoch_hours) == 2

    # A tape spanning the change still folds cleanly, and every ET calendar day
    # in it opens a bucket exactly at its own local midnight / 01:00 anchor.
    tape = thirty_minute_tape(et(*before), 48 * 4)
    assert (0, 0) in {et_parts(bar["time"]) for bar in buckets.aggregate_intraday(tape, 120)}
    assert (1, 0) in {et_parts(bar["time"]) for bar in buckets.aggregate_intraday(tape, 240)}


def test_central_midnight_two_hour_anchor_matches_the_browser_and_the_scanner() -> None:
    """The repo holds TWO 2h clocks; the opt-in one must stay pinned to its source.

    frontend/src/chartAggregation.js and premarket_scanner.py were moved to the
    midnight-CENTRAL clock (01/03/05/07/09... ET) by commit 03ca9a7;
    chart_aggregation.py, which this module defaults to, still anchors 2h at
    Eastern midnight. Until that is resolved by a human, both are reachable.
    """
    import premarket_scanner

    tape = thirty_minute_tape(et(2026, 6, 10), 48)
    folded = buckets.aggregate_intraday(
        tape, 120, two_hour_anchor=buckets.CENTRAL_MIDNIGHT_2H,
    )
    hours = [et_parts(bar["time"])[0] for bar in folded]
    assert hours == [23, 1, 3, 5, 7, 9, 11, 13, 15, 17, 19, 21, 23]
    for bar in tape:
        assert buckets.intraday_bucket_time(
            bar["time"], 120, two_hour_anchor=buckets.CENTRAL_MIDNIGHT_2H,
        ) == premarket_scanner.chart_bucket_time(bar["time"], 120)
    # And the two clocks genuinely disagree -- this is not a no-op switch.
    assert hours != [et_parts(bar["time"])[0] for bar in buckets.aggregate_intraday(tape, 120)]


def test_only_buckets_with_a_source_bar_are_emitted() -> None:
    tape = [
        {"time": et(2026, 6, 10, 9, 30), "open": 1, "high": 2, "low": 0.5, "close": 1.5,
         "volume": 5},
        # A six-hour hole: 10:00 -> 16:00.
        {"time": et(2026, 6, 10, 16, 0), "open": 3, "high": 4, "low": 2.5, "close": 3.5,
         "volume": 7},
    ]
    folded = buckets.aggregate_intraday(tape, 120)
    assert [et_parts(bar["time"]) for bar in folded] == [(8, 0), (16, 0)]


def test_ohlcv_fold_takes_first_open_max_high_min_low_last_close_summed_volume() -> None:
    base = et(2026, 6, 10, 10, 0)
    tape = [
        {"time": base, "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.5, "volume": 3},
        {"time": base + 1800, "open": 10.5, "high": 14.0, "low": 10.0, "close": 13.0,
         "volume": 4},
        {"time": base + 3600, "open": 13.0, "high": 13.5, "low": 6.0, "close": 7.25,
         "volume": 5},
    ]
    (bucket,) = buckets.aggregate_intraday(tape, 120)
    assert bucket["open"] == 10.0
    assert bucket["high"] == 14.0
    assert bucket["low"] == 6.0
    assert bucket["close"] == 7.25
    assert bucket["volume"] == pytest.approx(12.0)


# ----------------------------------------------------------------------
# Forming vs closed
# ----------------------------------------------------------------------


def test_partial_trailing_bucket_is_emitted_and_last_closed_bucket_excludes_it() -> None:
    # 00:00 -> 09:30 ET: the 08:00 2h bucket holds only its first three bars, so
    # at 09:35 it is still forming.
    tape = thirty_minute_tape(et(2026, 6, 10), 20)
    folded = buckets.aggregate_intraday(tape, 120)
    assert et_parts(folded[-1]["time"]) == (8, 0)

    now = et(2026, 6, 10, 9, 35)
    assert buckets.last_closed_bucket(folded, now) == len(folded) - 2
    assert buckets.forming_bucket(folded, now) == len(folded) - 1
    assert buckets.is_bucket_closed(folded[-1], now, span_seconds=7200) is False

    # Once the bucket's span has elapsed it closes and there is no forming one.
    after = et(2026, 6, 10, 10, 0)
    assert buckets.last_closed_bucket(folded, after) == len(folded) - 1
    assert buckets.forming_bucket(folded, after) is None


def test_last_closed_bucket_returns_minus_one_when_nothing_has_closed() -> None:
    tape = thirty_minute_tape(et(2026, 6, 10, 8, 0), 2)
    folded = buckets.aggregate_intraday(tape, 120)
    assert len(folded) == 1
    assert buckets.last_closed_bucket(folded, et(2026, 6, 10, 9, 0)) == -1
    assert buckets.last_closed_bucket([], et(2026, 6, 10, 9, 0)) == -1


# ----------------------------------------------------------------------
# Daily / multi-day anchoring
# ----------------------------------------------------------------------


def daily_bars_for(date_keys) -> list[dict]:
    return [
        {
            "date": key,
            "time": int(datetime.fromisoformat(key + "T00:00").replace(
                tzinfo=EASTERN,
            ).timestamp()),
            "open": 100.0 + index,
            "high": 101.0 + index,
            "low": 99.0 + index,
            "close": 100.5 + index,
            "volume": 1000.0 + index,
        }
        for index, key in enumerate(date_keys)
    ]


TRADING_DAYS = [
    (date(2026, 8, 3) + timedelta(days=offset)).isoformat()
    for offset in range(28)
    if (date(2026, 8, 3) + timedelta(days=offset)).weekday() < 5
]


@pytest.mark.parametrize("span", ["2D", "3D", "4D"])
def test_multi_day_group_keys_equal_the_validated_engine(span: str) -> None:
    """Asserted against ghts directly so the two can never drift apart."""
    for date_key in TRADING_DAYS:
        assert buckets.daily_group_key(span, date_key) == ghts._timeframe_group_key(
            span, date_key,
        )


def test_aggregate_daily_groups_consecutive_weekdays_on_the_tos_clock() -> None:
    """The DEFAULT grouping is the measured watchlist clock, not the engine's."""
    bars = daily_bars_for(TRADING_DAYS)
    folded = buckets.aggregate_daily(bars, "3D")

    expected_keys = []
    for date_key in TRADING_DAYS:
        key = buckets.tos_multiday_group_key("3D", date_key)
        if key not in expected_keys:
            expected_keys.append(key)
    assert [bar["groupKey"] for bar in folded] == expected_keys
    # Interior chunks are exactly three weekdays wide; the first and last are
    # whatever the tape's edges happen to cut, because the phase is ABSOLUTE.
    assert [bar["sourceCount"] for bar in folded][1:-1] == [3] * (len(folded) - 2)
    # Every source day lands in exactly one bucket, volume conserved.
    assert sum(bar["volume"] for bar in folded) == pytest.approx(
        sum(bar["volume"] for bar in bars),
    )
    assert sum(bar["sourceCount"] for bar in folded) == len(bars)


def test_engine_grouping_is_still_reachable_and_equals_the_signal_keys() -> None:
    """grouping="ema" must stay bit-identical to the validated signal replay.

    The watchlist moved to the weekday clock; the EMA/MACD signal studies did
    NOT, and this is what stops the two being quietly unified.
    """
    bars = daily_bars_for(TRADING_DAYS)
    folded = buckets.aggregate_daily(bars, "3D", grouping="ema")

    expected_keys = []
    for date_key in TRADING_DAYS:
        key = ghts._timeframe_group_key("3D", date_key)
        if key not in expected_keys:
            expected_keys.append(key)
    assert [bar["groupKey"] for bar in folded] == expected_keys


def test_unknown_grouping_is_rejected_rather_than_silently_defaulted() -> None:
    with pytest.raises(ValueError):
        buckets.aggregate_daily(daily_bars_for(TRADING_DAYS), "3D", grouping="ohno")


def test_aggregate_daily_D_is_identity() -> None:
    bars = daily_bars_for(TRADING_DAYS[:5])
    folded = buckets.aggregate_daily(bars, "D")
    assert [bar["time"] for bar in folded] == [bar["time"] for bar in bars]
    assert [bar["close"] for bar in folded] == [bar["close"] for bar in bars]


def test_the_1969_12_30_phase_keeps_sunday_in_fridays_3D_group_while_2D_rolls() -> None:
    """The exact weekend behaviour the ghts module comment cites.

    Under a Unix-day-zero phase (floor(ordinal / N)) this FAILS, which is why
    it is the regression guard for the whole anchoring question.
    """
    friday = "2026-08-21"
    sunday = "2026-08-23"

    assert buckets.daily_group_key("3D", sunday) == buckets.daily_group_key("3D", friday)
    assert buckets.daily_group_key("2D", sunday) != buckets.daily_group_key("2D", friday)

    # 4D does not open a new group on that Sunday either -- the spurious Sunday
    # bubble the comment names.
    assert buckets.daily_group_key("4D", sunday) == buckets.daily_group_key("4D", friday)

    # Prove the Unix phase would have answered differently on 3D.
    ordinal_friday = (date.fromisoformat(friday) - date(1970, 1, 1)).days
    ordinal_sunday = (date.fromisoformat(sunday) - date(1970, 1, 1)).days
    assert ordinal_friday // 3 != ordinal_sunday // 3


def test_macd_three_day_grouping_differs_from_the_ema_three_day_grouping() -> None:
    disagreements = [
        key
        for key in TRADING_DAYS
        if buckets.macd_daily_group_key("3D", key) != buckets.daily_group_key("3D", key)
    ]
    assert disagreements, "MACD THREE_DAYS must roll a calendar day ahead of EMA 3D"
    # And it is the engine's own key, not a second formula.
    for key in TRADING_DAYS:
        assert buckets.macd_daily_group_key("3D", key) == ghts._macd_timeframe_group_key(
            "3D", key,
        )
    # 2D and 4D are unaffected by the MACD offset.
    for span in ("2D", "4D"):
        for key in TRADING_DAYS:
            assert buckets.macd_daily_group_key(span, key) == buckets.daily_group_key(
                span, key,
            )


def test_macd_grouping_is_selectable_on_aggregate_daily() -> None:
    bars = daily_bars_for(TRADING_DAYS)
    ema = buckets.aggregate_daily(bars, "3D", grouping="ema")
    macd = buckets.aggregate_daily(bars, "3D", grouping="macd")
    assert [bar["groupKey"] for bar in ema] != [bar["groupKey"] for bar in macd]


def test_weekly_span_groups_monday_to_friday_across_a_week_boundary() -> None:
    # Mon 2026-08-17 .. Fri 2026-08-28: two full Mon-Fri weeks.
    week_days = [
        (date(2026, 8, 17) + timedelta(days=offset)).isoformat()
        for offset in range(12)
        if (date(2026, 8, 17) + timedelta(days=offset)).weekday() < 5
    ]
    folded = buckets.aggregate_daily(daily_bars_for(week_days), "Wk")

    assert [bar["groupKey"] for bar in folded] == ["W-2026-08-17", "W-2026-08-24"]
    assert [bar["sourceCount"] for bar in folded] == [5, 5]
    # Friday and the following Monday must NOT share a bucket.
    assert buckets.daily_group_key("Wk", "2026-08-21") != buckets.daily_group_key(
        "Wk", "2026-08-24",
    )
    # Wk delegates to the engine's W key.
    assert buckets.daily_group_key("Wk", "2026-08-19") == ghts._timeframe_group_key(
        "W", "2026-08-19",
    )


def test_monthly_span_is_the_calendar_month() -> None:
    days = [
        (date(2026, 8, 25) + timedelta(days=offset)).isoformat()
        for offset in range(14)
        if (date(2026, 8, 25) + timedelta(days=offset)).weekday() < 5
    ]
    folded = buckets.aggregate_daily(daily_bars_for(days), "M")
    assert [bar["groupKey"] for bar in folded] == ["M-2026-08", "M-2026-09"]


# ----------------------------------------------------------------------
# The WATCHLIST multi-day clock -- pinned to the CRWD measurement
# ----------------------------------------------------------------------
#
# MEASURED by the trader against his own thinkorswim watchlist on
# 2026-08-27, live, on the split-adjusted daily tape:
#
#     span | ours (1969-12-30 calendar phase) | thinkorswim
#     -----|----------------------------------|------------
#     D    | 31                               | 31   MATCH
#     2D   | 41                               | 53   WRONG
#     3D   | 55                               | 68   WRONG
#     4D   | 67                               | 68   MATCH (drift +-1)
#     Wk   | 71                               | 71   MATCH
#     M    | 76                               | 71   STILL UNEXPLAINED
#
# D, 4D and Wk matching proved the Skittles maths was right and the MULTI-DAY
# GROUPING was wrong. The fix is in momx/buckets.py: TOS chunks WEEKDAYS, not
# calendar days. M is a separate, still-open gap -- see docs/momx/SPEC.md.
#
# The 80 rows below are the tail of the real CRWD daily tape that produced
# those numbers, frozen so the pin cannot drift with the market. Prices are
# (open, high, low, close), rounded to cents; volume is unused by Skittles.

CRWD_DAILY_TAPE_2026_08_27 = [
    ("2026-05-05", 118.60, 120.17, 116.50, 119.13),
    ("2026-05-06", 117.10, 118.82, 114.37, 117.02),
    ("2026-05-07", 121.52, 126.70, 121.52, 126.43),
    ("2026-05-08", 124.79, 132.23, 123.08, 131.94),
    ("2026-05-11", 130.95, 135.67, 130.50, 135.57),
    ("2026-05-12", 135.50, 138.11, 133.25, 136.54),
    ("2026-05-13", 135.21, 142.09, 134.59, 140.64),
    ("2026-05-14", 139.93, 145.95, 138.75, 144.99),
    ("2026-05-15", 143.29, 149.56, 140.17, 148.52),
    ("2026-05-18", 147.37, 155.26, 146.84, 154.71),
    ("2026-05-19", 155.00, 158.56, 153.32, 154.22),
    ("2026-05-20", 153.57, 162.75, 153.52, 162.53),
    ("2026-05-21", 162.63, 164.89, 160.51, 162.06),
    ("2026-05-22", 162.63, 168.71, 162.40, 165.87),
    ("2026-05-26", 166.25, 169.37, 162.00, 167.89),
    ("2026-05-27", 160.25, 165.16, 158.27, 161.34),
    ("2026-05-28", 162.33, 169.38, 160.38, 167.75),
    ("2026-05-29", 169.36, 182.87, 168.74, 182.75),
    ("2026-06-01", 183.95, 196.42, 183.50, 195.54),
    ("2026-06-02", 191.21, 194.71, 186.35, 192.24),
    ("2026-06-03", 191.42, 191.75, 185.53, 186.90),
    ("2026-06-04", 168.52, 180.24, 167.78, 179.77),
    ("2026-06-05", 174.19, 176.56, 167.53, 167.76),
    ("2026-06-08", 168.38, 171.00, 163.00, 164.70),
    ("2026-06-09", 164.70, 166.22, 154.44, 161.23),
    ("2026-06-10", 159.76, 165.53, 158.99, 161.94),
    ("2026-06-11", 161.32, 174.11, 160.25, 172.88),
    ("2026-06-12", 172.67, 175.54, 169.50, 170.70),
    ("2026-06-15", 173.17, 174.37, 167.22, 173.23),
    ("2026-06-16", 173.27, 175.22, 166.49, 169.87),
    ("2026-06-17", 170.00, 172.73, 167.73, 170.74),
    ("2026-06-18", 171.69, 173.98, 165.23, 171.22),
    ("2026-06-22", 171.27, 178.05, 168.30, 168.86),
    ("2026-06-23", 167.25, 172.72, 165.50, 170.23),
    ("2026-06-24", 170.12, 171.79, 167.25, 168.26),
    ("2026-06-25", 170.10, 173.19, 167.50, 169.66),
    ("2026-06-26", 171.55, 176.21, 168.40, 175.27),
    ("2026-06-29", 177.38, 189.00, 176.03, 185.73),
    ("2026-06-30", 184.13, 191.33, 183.00, 190.79),
    ("2026-07-01", 193.75, 196.50, 191.25, 193.19),
    ("2026-07-02", 191.25, 199.53, 190.61, 193.98),
    ("2026-07-06", 189.51, 209.50, 188.55, 199.38),
    ("2026-07-07", 200.69, 201.37, 192.22, 194.62),
    ("2026-07-08", 193.51, 195.66, 185.30, 191.12),
    ("2026-07-09", 188.31, 198.75, 187.00, 198.40),
    ("2026-07-10", 196.60, 198.00, 186.48, 187.18),
    ("2026-07-13", 186.86, 189.25, 181.00, 187.91),
    ("2026-07-14", 191.00, 211.00, 189.46, 210.73),
    ("2026-07-15", 212.38, 217.50, 205.10, 206.77),
    ("2026-07-16", 207.17, 207.69, 200.07, 203.76),
    ("2026-07-17", 200.40, 209.50, 199.52, 203.08),
    ("2026-07-20", 203.08, 208.25, 197.84, 198.49),
    ("2026-07-21", 199.81, 200.00, 189.68, 191.15),
    ("2026-07-22", 192.56, 193.49, 185.05, 188.42),
    ("2026-07-23", 189.51, 190.21, 181.78, 183.42),
    ("2026-07-24", 185.00, 185.84, 181.83, 183.28),
    ("2026-07-27", 187.91, 188.50, 179.50, 180.11),
    ("2026-07-28", 181.33, 184.66, 174.14, 181.80),
    ("2026-07-29", 182.62, 186.39, 176.46, 179.38),
    ("2026-07-30", 180.58, 185.46, 177.75, 185.22),
    ("2026-07-31", 187.22, 191.65, 184.16, 190.86),
    ("2026-08-03", 194.04, 203.17, 192.60, 202.54),
    ("2026-08-04", 205.80, 212.64, 203.50, 211.22),
    ("2026-08-05", 213.55, 219.35, 209.56, 209.86),
    ("2026-08-06", 203.00, 207.63, 201.75, 207.39),
    ("2026-08-07", 213.82, 216.57, 208.20, 214.42),
    ("2026-08-10", 215.31, 226.90, 214.55, 225.16),
    ("2026-08-11", 224.00, 225.52, 219.24, 221.90),
    ("2026-08-12", 219.93, 224.95, 217.89, 221.78),
    ("2026-08-13", 224.98, 226.69, 220.23, 225.53),
    ("2026-08-14", 226.64, 227.50, 216.47, 216.95),
    ("2026-08-17", 216.53, 219.18, 213.01, 213.90),
    ("2026-08-18", 213.00, 216.06, 209.07, 212.92),
    ("2026-08-19", 213.38, 213.98, 197.25, 201.63),
    ("2026-08-20", 197.28, 200.74, 189.93, 190.34),
    ("2026-08-21", 190.94, 192.85, 187.01, 191.95),
    ("2026-08-24", 189.52, 195.40, 188.43, 190.68),
    ("2026-08-25", 191.62, 194.50, 182.35, 185.38),
    ("2026-08-26", 182.75, 191.32, 181.24, 189.18),
    ("2026-08-27", 208.25, 229.08, 206.10, 227.96),
]


def crwd_bars() -> list[dict]:
    return [
        {
            "date": date_key,
            "time": int(
                datetime.fromisoformat(date_key + "T00:00")
                .replace(tzinfo=EASTERN)
                .timestamp()
            ),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 0.0,
        }
        for date_key, open_, high, low, close in CRWD_DAILY_TAPE_2026_08_27
    ]


def skittles_value(span: str, *, grouping: str = "tos") -> int | None:
    folded = buckets.aggregate_daily(crwd_bars(), span, grouping=grouping)
    return columns.skittles_cell(folded).get("value")


def test_skittles_spans_reproduce_the_measured_thinkorswim_values() -> None:
    """THE regression pin. These five numbers came off the trader's terminal."""
    assert skittles_value("D") == 31
    assert skittles_value("2D") == 53
    assert skittles_value("3D") == 68
    assert skittles_value("4D") == 68
    assert skittles_value("Wk") == 71


def test_the_calendar_phase_the_watchlist_used_to_use_is_the_wrong_answer() -> None:
    """The bug this file records: 2D read 41 and 3D read 55 against 53 and 68."""
    assert skittles_value("2D", grouping="ema") == 41
    assert skittles_value("3D", grouping="ema") == 55


def test_no_calendar_phase_offset_can_reach_the_measured_values() -> None:
    """Why the fix changes UNITS, not the phase constant.

    Every offset k in -3..+3 of floor((calendar_ordinal + k) / N) was tried:
    2D can only produce 41 or 51 and 3D only 55/60/62. Neither reaches the
    measured 53 and 68 at ANY phase, so no amount of tuning the 1969-12-30
    constant would have fixed this. Chunking WEEKDAYS does.
    """
    bars = crwd_bars()
    for span, target in (("2D", 53), ("3D", 68)):
        size = int(span[0])
        reachable = set()
        for offset in range(-3, 4):
            folded = _fold_by(
                bars,
                lambda key, size=size, offset=offset: (
                    (date.fromisoformat(key) - date(1970, 1, 1)).days + offset
                )
                // size,
            )
            reachable.add(columns.skittles_cell(folded).get("value"))
        assert target not in reachable, f"{span}: calendar phase reached {target}"


def _fold_by(bars, key_for) -> list[dict]:
    """Minimal re-fold used only to prove a REJECTED grouping is rejected."""
    seen: dict = {}
    out: list[dict] = []
    for bar in bars:
        key = key_for(bar["date"])
        current = seen.get(key)
        if current is None:
            seen[key] = current = dict(bar)
            out.append(current)
            continue
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
    return out


def test_weekday_index_counts_monday_to_friday_from_a_thursday_epoch() -> None:
    # 1970-01-01 was a Thursday.
    assert buckets.weekday_index("1970-01-01") == 0
    assert buckets.weekday_index("1970-01-02") == 1   # Friday
    assert buckets.weekday_index("1970-01-03") == 2   # Saturday: no new weekday
    assert buckets.weekday_index("1970-01-04") == 2   # Sunday
    assert buckets.weekday_index("1970-01-05") == 2   # Monday starts the next
    assert buckets.weekday_index("1970-01-06") == 3
    # Runs backwards through the epoch too.
    assert buckets.weekday_index("1969-12-31") == -1
    # A market HOLIDAY still counts -- that is the measured behaviour, and it
    # is what keeps the key absolute instead of tape-dependent.
    assert (
        buckets.weekday_index("2026-07-06") - buckets.weekday_index("2026-07-02")
    ) == 2  # Fri 07-03 (Independence Day, observed) is counted
    assert buckets.weekday_index("2026-08-27") == 14780
    assert buckets.weekday_index("not-a-date") is None


def test_two_day_groups_pair_friday_with_monday_instead_of_stranding_friday() -> None:
    """The exact shape of the bug, on the week the measurement was taken.

    The calendar phase pairs Fri+Sat then Sun+Mon, which leaves Friday alone
    in its candle and opens a fresh one on Monday. TOS pairs Fri+Mon.
    """
    friday, monday = "2026-08-21", "2026-08-24"
    assert buckets.tos_multiday_group_key("2D", friday) == (
        buckets.tos_multiday_group_key("2D", monday)
    )
    assert buckets.daily_group_key("2D", friday) != buckets.daily_group_key(
        "2D", monday,
    )


def test_tos_grouping_leaves_D_Wk_and_M_on_the_validated_engine_keys() -> None:
    """Only 2D/3D/4D moved. D, Wk and M already matched thinkorswim."""
    for date_key in TRADING_DAYS:
        for span in ("D", "Wk", "M"):
            assert buckets.tos_multiday_group_key(span, date_key) == (
                buckets.daily_group_key(span, date_key)
            )


def test_the_weekday_phase_is_the_only_uniform_one_that_fits_all_three_spans() -> None:
    """Pins the phase constant to the search that produced it.

    Of every uniform offset k in 0..23 of
    floor((weekdays_since_1970_01_01 + k) / N), only k = 6 and its period-12
    repeat k = 18 give 2D=53, 3D=68 and 4D=68 together (12 = lcm(2, 3, 4)).
    """
    bars = crwd_bars()
    targets = {"2D": 53, "3D": 68, "4D": 68}
    solutions = []
    for offset in range(24):
        values = {}
        for span, _target in targets.items():
            size = int(span[0])
            folded = _fold_by(
                bars,
                lambda key, size=size, offset=offset: (
                    buckets.weekday_index(key) + offset
                )
                // size,
            )
            values[span] = columns.skittles_cell(folded).get("value")
        if values == targets:
            solutions.append(offset)
    assert solutions == [6, 18]
    assert buckets.TOS_WEEKDAY_PHASE == 6


def test_aggregate_daily_folds_ohlcv_across_the_group() -> None:
    bars = [
        {"date": "2026-08-17", "time": et(2026, 8, 17), "open": 10.0, "high": 12.0,
         "low": 9.0, "close": 11.0, "volume": 100.0},
        {"date": "2026-08-18", "time": et(2026, 8, 18), "open": 11.0, "high": 15.0,
         "low": 8.0, "close": 14.0, "volume": 200.0},
    ]
    assert buckets.daily_group_key("2D", "2026-08-17") == buckets.daily_group_key(
        "2D", "2026-08-18",
    )
    (bucket,) = buckets.aggregate_daily(bars, "2D")
    assert (bucket["open"], bucket["high"], bucket["low"], bucket["close"]) == (
        10.0, 15.0, 8.0, 14.0,
    )
    assert bucket["volume"] == pytest.approx(300.0)
    assert bucket["time"] == et(2026, 8, 17)
    assert bucket["date"] == "2026-08-17"


# ----------------------------------------------------------------------
# Live tail
# ----------------------------------------------------------------------


def test_merge_live_tail_appends_only_strictly_newer_bars() -> None:
    base = thirty_minute_tape(et(2026, 6, 10, 9, 30), 4)
    last = int(base[-1]["time"])
    live = [
        {"time": last - 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1},
        {"time": last, "open": 2, "high": 2, "low": 2, "close": 2, "volume": 1},
        {"time": last + 60, "open": 3, "high": 3, "low": 3, "close": 3, "volume": 1},
        {"time": last + 120, "open": 4, "high": 4, "low": 4, "close": 4, "volume": 1},
    ]
    merged = buckets.merge_live_tail(base, live)
    assert [bar["time"] for bar in merged] == [
        *[bar["time"] for bar in base], last + 60, last + 120,
    ]
    # The cached bucket's own high/low survive: nothing at or before the cutoff
    # is allowed to replace it.
    assert merged[len(base) - 1] is base[-1]


def test_merge_live_tail_is_the_premarket_scanner_implementation() -> None:
    import premarket_scanner

    assert buckets.merge_live_tail is premarket_scanner.merge_live_tail


# ----------------------------------------------------------------------
# The MONTHLY Skittles gap -- measured, searched, and left UNFIXED
# ----------------------------------------------------------------------
#
# MEASURED on CRWD, 2026-08-27, live, AFTER both the split-adjustment fix and
# the weekday-chunking fix above:
#
#     span | ours | thinkorswim
#     -----|------|------------
#     D    |  31  | 31   MATCH
#     2D   |  53  | 53   MATCH
#     3D   |  68  | 68   MATCH
#     4D   |  68  | 68   MATCH
#     Wk   |  71  | 71   MATCH
#     M    |  76  | 71   WRONG by +5
#
# A 361-candidate search over alternative MONTH definitions found no rule with
# a stateable mechanism that reaches 71 -- see docs/momx/SPEC.md, "The Monthly
# Skittles gap". M is therefore still the plain calendar month, and 76 is
# pinned below as a KNOWN GAP, not as a correct answer. If the trader re-reads
# the M cell and it is not 71, change the comment, not the grouping.

#: The real CRWD calendar-MONTH bars behind the 76, as
#: ``aggregate_daily(daily_tape, "M")`` folded them on 2026-08-27.
#: (open, high, low, close); volume is unused by Skittles. The trailing month
#: is FORMING, which is deliberate -- TOS scans the current bar.
CRWD_MONTHLY_BARS_2026_08_27 = [
    ("2025-03-03", 99.75, 101.15, 75.95, 88.15),
    ("2025-04-01", 89.74, 108.09, 74.50, 107.22),
    ("2025-05-01", 107.88, 118.56, 101.16, 117.84),
    ("2025-06-02", 117.75, 127.51, 111.32, 127.33),
    ("2025-07-01", 127.34, 129.49, 113.36, 113.64),
    ("2025-08-01", 112.24, 114.45, 102.31, 105.93),
    ("2025-09-02", 104.28, 126.80, 100.67, 122.60),
    ("2025-10-01", 121.94, 138.41, 118.85, 135.75),
    ("2025-11-03", 137.33, 141.73, 119.39, 127.29),
    ("2025-12-01", 125.80, 132.47, 117.10, 117.19),
    ("2026-01-02", 118.50, 121.80, 107.85, 110.35),
    ("2026-02-02", 110.00, 111.81, 85.68, 93.00),
    ("2026-03-02", 93.98, 113.00, 90.45, 97.60),
    ("2026-04-01", 99.19, 116.99, 91.12, 111.44),
    ("2026-05-01", 113.67, 182.87, 111.39, 182.75),
    ("2026-06-01", 183.95, 196.42, 154.44, 190.79),
    ("2026-07-01", 193.75, 217.50, 174.14, 190.86),
    ("2026-08-03", 194.04, 229.08, 181.24, 227.96),
]


def crwd_monthly_bars(count: int | None = None) -> list[dict]:
    rows = CRWD_MONTHLY_BARS_2026_08_27
    if count is not None:
        rows = rows[-count:]
    return [
        {
            "date": date_key,
            "time": int(
                datetime.fromisoformat(date_key + "T00:00")
                .replace(tzinfo=EASTERN)
                .timestamp()
            ),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": 0.0,
        }
        for date_key, open_, high, low, close in rows
    ]


def test_monthly_skittles_reads_76_where_thinkorswim_read_71() -> None:
    """The KNOWN GAP, pinned so a change to M grouping cannot pass silently.

    76 is NOT asserted because it is right. It is asserted because it is what
    the shipped calendar-month rule produces on the bars the trader's 71 was
    read against, and because the search recorded in docs/momx/SPEC.md found
    no principled rule that closes the 5 points.
    """
    assert columns.skittles_cell(crwd_monthly_bars()).get("value") == 76


def test_the_monthly_reading_cannot_be_a_warmup_or_history_depth_problem() -> None:
    """Rules out "TOS has more monthly history than our 3-year daily tape".

    FastD(8) of FastK(8) reads back exactly 15 bars. Anything older than the
    15th monthly bar is arithmetically incapable of moving the reading, so a
    deeper tape cannot explain the gap. Proven here by truncating instead of
    extending: 16 bars and 18 bars give the same number.
    """
    assert (
        columns.skittles_cell(crwd_monthly_bars(16)).get("value")
        == columns.skittles_cell(crwd_monthly_bars(18)).get("value")
        == 76
    )


def test_dropping_the_forming_bar_breaks_every_span_that_matches_thinkorswim() -> None:
    """Why "M excludes the incomplete month" is not an available explanation.

    Excluding the forming bar is where a monthly 71 lives naturally -- 55 of
    the 497 chunking candidates searched produce 71 once the trailing bar is
    dropped, which is the mode of that distribution and therefore worthless as
    evidence. This test is the reason the whole family is rejected: the SAME
    move destroys all five spans that currently match, so TOS demonstrably
    includes the forming bar.
    """
    bars = crwd_bars()[:-1]
    matched = {"D": 31, "2D": 53, "3D": 68, "4D": 68, "Wk": 71}
    for span, tos_value in matched.items():
        folded = buckets.aggregate_daily(bars, span)
        assert columns.skittles_cell(folded).get("value") != tos_value, (
            f"{span}: dropping the forming bar still reproduced {tos_value}; "
            "the forming-bar argument in docs/momx/SPEC.md needs re-checking"
        )


def test_the_watchlist_weekday_clock_deliberately_does_not_own_the_month() -> None:
    """M stays on the validated engine key; the weekday fix is 2D/3D/4D only.

    Chunking 18-24 weekdays at the measured phase k=6 -- the exact rule and
    phase that fixed 2D/3D/4D -- was evaluated on the live CRWD tape and
    produced 73, 79, 80, 75, 77, 73, 75. None is 71, so the multi-day fix does
    not extend to the month.
    """
    for date_key in ("2026-08-03", "2026-08-27", "2026-01-02"):
        assert buckets.tos_multiday_group_key(
            "M", date_key
        ) == buckets.daily_group_key("M", date_key)
