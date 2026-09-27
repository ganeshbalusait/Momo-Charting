"""Server-side candle bucketing must match the browser's exactly.

Every case here is ported from frontend/src/chartAggregation.test.js. The point
of the port is that a chart aggregated on the server and the same chart
aggregated in the browser draw the SAME candles - so if either side's bucket
boundaries drift, a trader sees a different 4H bar depending on which path
served it. These are the quirks that make the boundaries non-obvious:

* 4H follows the TOS equity clock, which starts at midnight CENTRAL, so the
  Eastern boundaries are 01:00, 05:00, 09:00, 13:00, 17:00, 21:00.
* 2H is anchored to Eastern midnight rather than the epoch, so 04:00 stays
  04:00 on both sides of a daylight-saving change.
* D/W/M use Eastern calendar buckets, not fixed 86400/604800/2592000 spans.
* Everything else is plain epoch bucketing.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from chart_aggregation import (
    aggregate_chart_bars,
    build_chart_display_bars,
    chart_aggregation_bucket_time,
    chart_source_bar_spacing_minutes,
    extend_study_tape,
)


def unix(iso: str) -> int:
    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp())


def bar(time: int, open_: float, high: float, low: float, close: float, volume: int = 0) -> dict:
    return {"time": time, "open": open_, "high": high, "low": low, "close": close, "volume": volume}


def test_four_hour_buckets_align_to_midnight_central() -> None:
    assert chart_aggregation_bucket_time(unix("2026-07-31T12:59:00Z"), 240) == unix("2026-07-31T09:00:00Z")
    assert chart_aggregation_bucket_time(unix("2026-07-31T13:00:00Z"), 240) == unix("2026-07-31T13:00:00Z")
    assert chart_aggregation_bucket_time(unix("2026-07-31T16:59:00Z"), 240) == unix("2026-07-31T13:00:00Z")
    assert chart_aggregation_bucket_time(unix("2026-07-31T17:00:00Z"), 240) == unix("2026-07-31T17:00:00Z")


def test_four_hour_anchor_is_stable_in_standard_time() -> None:
    # Central and Eastern observe DST together, so the 01:00 ET anchor holds
    # in winter too: 18:00Z is 13:00 EST, exactly on a boundary.
    assert chart_aggregation_bucket_time(unix("2026-01-09T18:00:00Z"), 240) == unix("2026-01-09T18:00:00Z")


def test_four_hour_aggregation_merges_ohlcv_across_the_13_00_boundary() -> None:
    bars = [
        bar(unix("2026-07-31T12:55:00Z"), 100, 101, 99, 100.5, 10),
        bar(unix("2026-07-31T13:00:00Z"), 100.5, 103, 100, 102, 20),
        bar(unix("2026-07-31T16:55:00Z"), 102, 104, 101, 103, 30),
        bar(unix("2026-07-31T17:00:00Z"), 103, 105, 102, 104, 40),
        bar(unix("2026-07-31T19:55:00Z"), 104, 105.5, 103.5, 105, 45),
        bar(unix("2026-07-31T20:00:00Z"), 105, 106, 104, 105.5, 50),
    ]

    assert aggregate_chart_bars(bars, 240) == [
        bar(unix("2026-07-31T09:00:00Z"), 100, 101, 99, 100.5, 10),
        bar(unix("2026-07-31T13:00:00Z"), 100.5, 104, 100, 103, 50),
        bar(unix("2026-07-31T17:00:00Z"), 103, 106, 102, 105.5, 135),
    ]


def test_two_hour_buckets_anchor_to_eastern_midnight_not_the_epoch() -> None:
    # 10:07 ET in summer and in winter must both land on the 10:00 ET bucket.
    summer = chart_aggregation_bucket_time(unix("2026-07-31T14:07:00Z"), 120)
    winter = chart_aggregation_bucket_time(unix("2026-01-09T15:07:00Z"), 120)
    assert summer == unix("2026-07-31T14:00:00Z")
    assert winter == unix("2026-01-09T15:00:00Z")
    # Plain epoch bucketing would put the winter bar on 09:00 ET instead.
    assert winter != (unix("2026-01-09T15:07:00Z") // 7200) * 7200


def test_daily_bucket_keeps_one_eastern_trading_date_across_utc_midnight() -> None:
    # 2026-01-10T00:05Z is still Friday 19:05 EST, so it belongs to Jan 9.
    assert chart_aggregation_bucket_time(unix("2026-01-10T00:05:00Z"), 1440) == unix("2026-01-09T05:00:00Z")

    bars = [
        bar(unix("2026-01-09T23:55:00Z"), 100, 102, 99, 101, 10),
        bar(unix("2026-01-10T00:05:00Z"), 101, 104, 100, 103, 20),
    ]
    assert aggregate_chart_bars(bars, 1440) == [
        bar(unix("2026-01-09T05:00:00Z"), 100, 104, 99, 103, 30),
    ]


def test_weekly_buckets_use_eastern_mondays_not_epoch_thursdays() -> None:
    bars = [
        bar(unix("2026-07-27T13:30:00Z"), 100, 102, 99, 101, 10),
        bar(unix("2026-07-31T23:55:00Z"), 101, 105, 100, 104, 20),
        bar(unix("2026-08-03T13:30:00Z"), 104, 106, 103, 105, 30),
    ]
    aggregated = [(row["time"], row["open"], row["close"], row["volume"]) for row in aggregate_chart_bars(bars, 10080)]
    assert aggregated == [
        (unix("2026-07-27T04:00:00Z"), 100, 104, 30),
        (unix("2026-08-03T04:00:00Z"), 104, 105, 30),
    ]


def test_monthly_buckets_use_the_first_eastern_day_without_thirty_day_drift() -> None:
    bars = [
        bar(unix("2026-07-01T13:30:00Z"), 100, 102, 99, 101, 10),
        bar(unix("2026-07-31T23:55:00Z"), 101, 105, 100, 104, 20),
        bar(unix("2026-08-03T13:30:00Z"), 104, 106, 103, 105, 30),
    ]
    aggregated = [(row["time"], row["open"], row["close"], row["volume"]) for row in aggregate_chart_bars(bars, 43200)]
    assert aggregated == [
        (unix("2026-07-01T04:00:00Z"), 100, 104, 30),
        (unix("2026-08-01T04:00:00Z"), 104, 105, 30),
    ]


def test_ordinary_minute_buckets_stay_plain_epoch_math() -> None:
    assert chart_aggregation_bucket_time(unix("2026-07-31T14:07:00Z"), 5) == unix("2026-07-31T14:05:00Z")
    assert chart_aggregation_bucket_time(unix("2026-07-31T14:07:00Z"), 15) == unix("2026-07-31T14:00:00Z")


def test_malformed_candles_are_dropped_instead_of_blanking_the_series() -> None:
    # Lightweight Charts rejects a whole series when one point is invalid, so a
    # bad row must be dropped rather than passed through.
    valid = bar(unix("2026-07-31T14:07:00Z"), 1, 1, 1, 1, 1)
    bars = [valid, {"time": unix("2026-07-31T14:08:00Z"), "open": None, "high": 2, "low": 1, "close": 2}]
    assert aggregate_chart_bars(bars, 5) == [bar(unix("2026-07-31T14:05:00Z"), 1, 1, 1, 1, 1)]


def test_forming_bar_bounds_are_repaired_like_the_browser_does() -> None:
    # A provider can publish a forming bar whose high/low has not caught up
    # with its close; the browser repairs the bounds instead of dropping it.
    bars = [{"time": unix("2026-07-31T14:05:00Z"), "open": 10, "high": 9, "low": 11, "close": 12, "volume": 3}]
    assert aggregate_chart_bars(bars, 5) == [bar(unix("2026-07-31T14:05:00Z"), 10, 12, 10, 12, 3)]


def test_rejects_non_positive_or_unusable_timestamps() -> None:
    assert chart_aggregation_bucket_time(0, 5) is None
    assert chart_aggregation_bucket_time(-1, 5) is None
    assert chart_aggregation_bucket_time(None, 5) is None


# --- which tape a timeframe is built FROM is part of the answer -------------


def test_bar_spacing_is_the_modal_gap_with_a_five_minute_fallback() -> None:
    five = [bar(1000 + i * 300, 1, 1, 1, 1, 1) for i in range(20)]
    thirty = [bar(1000 + i * 1800, 1, 1, 1, 1, 1) for i in range(20)]
    assert chart_source_bar_spacing_minutes(five) == 5
    assert chart_source_bar_spacing_minutes(thirty) == 30
    assert chart_source_bar_spacing_minutes([]) == 5
    assert chart_source_bar_spacing_minutes([{"time": 100}]) == 5


def test_bar_spacing_rounds_half_up_like_the_browser() -> None:
    # Python's round() is banker's rounding and would answer 2 here; the
    # browser's Math.round gives 3, and the tapes must agree.
    rows = [bar(1000 + i * 150, 1, 1, 1, 1, 1) for i in range(10)]
    assert chart_source_bar_spacing_minutes(rows) == 3


def test_four_hour_view_extends_with_study_history_and_cuts_over_to_live() -> None:
    study = [
        bar(unix("2026-07-29T13:00:00Z"), 90, 94, 89, 93, 50),
        # Overlaps the live window: it must be replaced, never counted twice.
        bar(unix("2026-07-31T13:00:00Z"), 100, 999, 1, 500, 5000),
    ]
    live = [
        bar(unix("2026-07-31T13:03:00Z"), 100, 103, 99, 102, 10),
        bar(unix("2026-07-31T14:00:00Z"), 102, 105, 101, 104, 20),
    ]
    rows = build_chart_display_bars(study_bars=study, live_bars=live, aggregation_minutes=240)
    assert [(r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"]) for r in rows] == [
        (unix("2026-07-29T13:00:00Z"), 90, 94, 89, 93, 50),
        (unix("2026-07-31T13:00:00Z"), 100, 105, 99, 104, 30),
    ]


def test_five_minute_view_ignores_a_coarse_study_tape_and_uses_the_live_one() -> None:
    # Two rows two days apart read as a 2880-minute cadence, which is far
    # coarser than 5m, so this tape cannot reconstruct 5m candles and must be
    # left out. (A tape of ONE row has no gap to measure, falls back to five
    # minutes, and would legitimately be used - which is why this fixture
    # keeps both rows.)
    study = [
        bar(unix("2026-07-29T13:00:00Z"), 90, 94, 89, 93, 50),
        bar(unix("2026-07-31T13:00:00Z"), 100, 999, 1, 500, 5000),
    ]
    live = [
        bar(unix("2026-07-31T13:03:00Z"), 100, 103, 99, 102, 10),
        bar(unix("2026-07-31T14:00:00Z"), 102, 105, 101, 104, 20),
    ]
    assert build_chart_display_bars(
        study_bars=study, live_bars=live, aggregation_minutes=5,
    ) == aggregate_chart_bars(live, 5)


def test_daily_view_splices_the_live_day_over_the_stale_seed_row() -> None:
    seed_monday = unix("2026-08-03T04:00:00Z")
    seed_tuesday = unix("2026-08-04T04:00:00Z")
    daily = [
        bar(seed_monday, 100, 105, 99, 104, 1000),
        bar(seed_tuesday, 104, 104.5, 103, 103.5, 10),  # stale partial row
    ]
    live = [
        bar(unix("2026-08-04T13:30:00Z"), 104, 106, 104, 105, 50),
        bar(unix("2026-08-04T19:59:00Z"), 105, 107, 105, 106.5, 60),
    ]
    rows = build_chart_display_bars(live_bars=live, daily_bars=daily, aggregation_minutes=1440)
    assert len(rows) == 2
    assert rows[0]["time"] == seed_monday and rows[0]["close"] == 104
    assert rows[1]["time"] == seed_tuesday
    assert rows[1]["high"] == 107  # the live candle replaced the stale seed row
    assert rows[1]["volume"] == 110


def test_weekly_view_groups_the_daily_seed_into_monday_buckets() -> None:
    daily = [
        bar(unix("2026-07-27T04:00:00Z"), 1, 3, 1, 2, 5),
        bar(unix("2026-07-29T04:00:00Z"), 2, 5, 2, 4, 5),
        bar(unix("2026-08-03T04:00:00Z"), 4, 6, 3, 5, 7),
    ]
    rows = build_chart_display_bars(live_bars=[], daily_bars=daily, aggregation_minutes=10080)
    assert len(rows) == 2
    assert rows[0]["time"] == unix("2026-07-27T04:00:00Z")
    assert rows[0]["high"] == 5 and rows[0]["close"] == 4 and rows[0]["volume"] == 10
    assert rows[1]["time"] == unix("2026-08-03T04:00:00Z")


def test_daily_view_without_a_seed_still_aggregates_the_live_tape() -> None:
    live = [bar(unix("2026-08-04T13:30:00Z"), 10, 12, 9, 11, 5)]
    rows = build_chart_display_bars(live_bars=live, daily_bars=[], aggregation_minutes=1440)
    assert len(rows) == 1 and rows[0]["close"] == 11


def test_four_hour_view_conserves_volume_for_a_thirty_minute_study_tape() -> None:
    base = 1754902800
    study = [bar(base + i * 1800, 100 + i, 101 + i, 99 + i, 100.5 + i, 10) for i in range(16)]
    live = [bar(base + 16 * 1800 + i * 60, 120, 121, 119, 120.5, 2) for i in range(30)]
    rows = build_chart_display_bars(study_bars=study, live_bars=live, aggregation_minutes=240)
    assert len(rows) >= 2
    times = [r["time"] for r in rows]
    assert len(set(times)) == len(times)
    assert sum(r["volume"] for r in rows) == 16 * 10 + 30 * 2


def test_four_hour_view_conserves_volume_for_a_five_minute_study_tape() -> None:
    base = 1754902800
    study = [bar(base + i * 300, 50, 51, 49, 50.5, 3) for i in range(96)]
    rows = build_chart_display_bars(study_bars=study, live_bars=[], aggregation_minutes=240)
    assert sum(r["volume"] for r in rows) == 96 * 3


def test_fine_tape_is_used_only_when_it_reaches_further_back_than_the_live_tape() -> None:
    # Measured 2026-08-12: preferring the fine tape unconditionally SHORTENED
    # the chart when the server's five-minute tape was shallower than the
    # client's one-minute tape.
    live = [bar(unix("2026-08-01T13:30:00Z") + i * 60, 10, 11, 9, 10.5, 1) for i in range(20)]
    shallow_fine = [bar(unix("2026-08-04T13:30:00Z") + i * 300, 20, 21, 19, 20.5, 2) for i in range(20)]
    assert build_chart_display_bars(
        fine_study_bars=shallow_fine, live_bars=live, aggregation_minutes=15,
    ) == aggregate_chart_bars(live, 15)

    deep_fine = [bar(unix("2026-07-20T13:30:00Z") + i * 300, 20, 21, 19, 20.5, 2) for i in range(20)]
    deeper = build_chart_display_bars(
        fine_study_bars=deep_fine, live_bars=live, aggregation_minutes=15,
    )
    assert deeper[0]["time"] < aggregate_chart_bars(live, 15)[0]["time"]


def _minute_tape(start_iso: str, count: int, close: float = 100.0) -> list[dict]:
    """`count` consecutive one-minute bars from an Eastern wall-clock start."""
    start = int(
        datetime.fromisoformat(start_iso).replace(tzinfo=ZoneInfo("America/New_York")).timestamp()
    )
    return [
        {
            "time": start + index * 60,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 10,
        }
        for index in range(count)
    ]


def _eastern(stamp_iso: str) -> int:
    return int(
        datetime.fromisoformat(stamp_iso).replace(tzinfo=ZoneInfo("America/New_York")).timestamp()
    )


def test_extend_study_tape_appends_completed_buckets_from_the_live_tape():
    """The deep study tape must advance intraday, not sit at the last full build.

    Measured 2026-08-21 12:27 ET: every symbol's studyBars trailed its 1-minute
    tape by hours - AAPL 10:00 vs 12:25, TSLA 09:00, MSTR 08:30, IONQ stuck at
    08/20 14:30 - because the splice preserved the old deep tape wholesale on
    every recency refresh. The MTF engines aggregate 1H/2H/4H from this tape,
    so IONQ's 4H state read PUT from yesterday while TOS had flipped to CALL4H.
    """
    existing = aggregate_chart_bars(_minute_tape("2026-08-21T09:00", 180), 30)
    assert existing[-1]["time"] == _eastern("2026-08-21T11:30")

    live = _minute_tape("2026-08-21T11:30", 90)  # 11:30 -> 13:00
    extended = extend_study_tape(existing, live, now_epoch=_eastern("2026-08-21T13:00"))

    assert [bar["time"] for bar in extended[-2:]] == [
        _eastern("2026-08-21T12:00"),
        _eastern("2026-08-21T12:30"),
    ]
    assert len(extended) == len(existing) + 2


def test_extend_study_tape_excludes_the_forming_bucket():
    """A half-built 30-minute candle must not reach the signal engines.

    These are TOS secondary-candle studies: they expect completed candles, and
    a repainting bucket makes a cross appear and vanish.
    """
    existing = aggregate_chart_bars(_minute_tape("2026-08-21T09:00", 180), 30)
    live = _minute_tape("2026-08-21T11:30", 75)  # runs into an unfinished 12:30 bucket

    extended = extend_study_tape(existing, live, now_epoch=_eastern("2026-08-21T12:45"))

    assert extended[-1]["time"] == _eastern("2026-08-21T12:00")


def test_extend_study_tape_never_rewrites_history():
    """Only strictly-newer buckets are appended; the deep archive is immutable."""
    existing = aggregate_chart_bars(_minute_tape("2026-08-21T09:00", 180), 30)
    overlapping = _minute_tape("2026-08-21T09:00", 240, 999.0)

    extended = extend_study_tape(existing, overlapping, now_epoch=_eastern("2026-08-21T13:00"))

    for index, bar in enumerate(existing):
        assert extended[index] == bar
    assert extended[-1]["time"] == _eastern("2026-08-21T12:30")


def test_extend_study_tape_derives_spacing_from_the_tape():
    """studyBars has shipped at both 5m and 30m, so the cadence is measured."""
    existing = aggregate_chart_bars(_minute_tape("2026-08-21T09:00", 60), 5)
    live = _minute_tape("2026-08-21T10:00", 20)

    extended = extend_study_tape(existing, live, now_epoch=_eastern("2026-08-21T10:20"))

    appended = [bar["time"] for bar in extended[len(existing):]]
    # 10:15 counts: it holds 10:15-10:19, a full five-minute span, and closes
    # exactly at now. Bucket end <= now is the completeness rule.
    assert appended == [
        _eastern("2026-08-21T10:00"),
        _eastern("2026-08-21T10:05"),
        _eastern("2026-08-21T10:10"),
        _eastern("2026-08-21T10:15"),
    ]


def test_extend_study_tape_leaves_an_empty_tape_alone():
    """With no tape there is no cadence to anchor to, and no depth to protect."""
    assert extend_study_tape([], _minute_tape("2026-08-21T09:00", 60), now_epoch=_eastern("2026-08-21T10:00")) == []


def test_extend_study_tape_reads_cadence_from_the_recent_tail_not_the_head():
    """A real study tape starts sparse, so its HEAD lies about its cadence.

    Caught in live verification on 2026-08-21: chart_source_bar_spacing_minutes
    samples the first 60 rows, and every production studyBars archive begins
    with a daily-spaced section. It therefore reported 1440 minutes for tapes
    whose recent cadence is 30 - measured on IONQ/AAPL/TSLA/MSTR, head60=1440m
    against tail240=30m on all four. With a daily bucket size nothing was ever
    a "completed bucket", so the first cut of this function appended exactly
    zero bars and the staleness it was written to fix survived the deploy.

    The cadence that matters is the one the tape is running at NOW.
    """
    sparse_head = [
        {
            "time": _eastern("2026-06-01T09:30") + day * 86_400,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 5,
        }
        for day in range(40)
    ]
    # A production tape carries thousands of 30-minute rows behind the sparse
    # opening, so the tail sample lands entirely inside the dense section. A
    # toy tail would leave the sample straddling the daily head and prove
    # nothing.
    dense_start = _eastern("2026-08-14T04:00")
    dense_tail = [
        {
            "time": dense_start + step * 1_800,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 5,
        }
        for step in range(300)
    ]
    tape = sparse_head + dense_tail
    assert chart_source_bar_spacing_minutes(tape) == 1440  # the trap, documented

    last_tape_time = tape[-1]["time"]
    live_start = last_tape_time + 1_800
    live = [
        {
            "time": live_start + minute * 60,
            "open": 10.0, "high": 11.0, "low": 9.0, "close": 10.0, "volume": 5,
        }
        for minute in range(120)
    ]

    extended = extend_study_tape(tape, live, now_epoch=live_start + 120 * 60)

    appended = [bar["time"] for bar in extended[len(tape):]]
    assert appended == [live_start + step * 1_800 for step in range(4)]
