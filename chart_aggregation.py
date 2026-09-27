"""Candle bucketing that matches frontend/src/chartAggregation.js exactly.

The browser aggregates the one-minute tape into the timeframe on screen. Doing
that work in the browser means shipping every one-minute bar to it — measured
2026-08-13 at 4.5 MB per symbol, 26,320 one-minute bars that the browser then
folds into 1,105 four-hour candles on every load.

This module lets the server fold them instead. That is only safe if both sides
draw the SAME candles, so the bucket boundaries here are a deliberate port of
the browser's, quirks included:

* 4H follows the ThinkOrSwim equity clock, which aggregates from midnight
  CENTRAL. Central is one hour behind Eastern, so the boundaries seen on an
  Eastern chart are 01:00, 05:00, 09:00, 13:00, 17:00 and 21:00.
* 2H is anchored to Eastern midnight rather than to the epoch, so 04:00 stays
  04:00 on both sides of a daylight-saving change.
* D/W/M use Eastern calendar buckets — real weeks and real months — instead of
  fixed 86400/604800/2592000 second spans, which would drift.
* Everything else is plain epoch bucketing.

tests/test_chart_aggregation.py is the port of the browser's own test cases.
"""

from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

_DAY_MINUTES = 1440
_WEEK_MINUTES = 10080
_MONTH_MINUTES = 43200
_CALENDAR_MINUTES = frozenset({_DAY_MINUTES, _WEEK_MINUTES, _MONTH_MINUTES})

# Timeframe labels the chart uses, mapped to their aggregation minutes. "D",
# "W" and "M" are the browser's own keys, so a client can pass the label it
# already has instead of translating it.
AGGREGATION_MINUTES_BY_LABEL = {
    "1m": 1, "2m": 2, "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30,
    "1h": 60, "2h": 120, "4h": 240,
    "D": _DAY_MINUTES, "W": _WEEK_MINUTES, "M": _MONTH_MINUTES,
}


def aggregation_minutes_for_label(label) -> int | None:
    """Minutes for a timeframe label, or None when it is not one we aggregate."""
    if label is None:
        return None
    key = str(label).strip()
    if not key:
        return None
    resolved = AGGREGATION_MINUTES_BY_LABEL.get(key)
    if resolved is not None:
        return resolved
    # Accept the canonical casing of the calendar keys only ("d"/"w"/"m" are
    # ambiguous against minutes, so they are not guessed at).
    return AGGREGATION_MINUTES_BY_LABEL.get(key.upper()) if len(key) == 1 else None


def _eastern_midnight_epoch(day: date) -> int:
    # Midnight is never the ambiguous hour in US daylight-saving transitions
    # (they happen at 02:00), so a plain localized midnight is unambiguous.
    return int(datetime(day.year, day.month, day.day, tzinfo=EASTERN).timestamp())


def _eastern_calendar_date(timestamp: int) -> date:
    return datetime.fromtimestamp(timestamp, EASTERN).date()


def _calendar_bucket_time(timestamp: int, aggregation_minutes: int) -> int:
    day = _eastern_calendar_date(timestamp)
    if aggregation_minutes == _WEEK_MINUTES:
        day -= timedelta(days=day.weekday())
    elif aggregation_minutes == _MONTH_MINUTES:
        day = day.replace(day=1)
    return _eastern_midnight_epoch(day)


def tos_four_hour_bucket_time(timestamp: int) -> int:
    """The primary TOS equity 4H clock: four-hour steps from midnight Central."""
    eastern_midnight = _calendar_bucket_time(timestamp, _DAY_MINUTES)
    midnight_central_in_eastern = eastern_midnight + 3600
    four_hours = 240 * 60
    return midnight_central_in_eastern + (
        (timestamp - midnight_central_in_eastern) // four_hours
    ) * four_hours


def chart_aggregation_bucket_time(timestamp, minutes) -> int | None:
    """Bucket start for a bar time, or None when the timestamp is unusable."""
    try:
        time = int(timestamp)
    except (TypeError, ValueError):
        return None
    if time <= 0:
        return None
    try:
        aggregation_minutes = max(int(minutes), 1)
    except (TypeError, ValueError):
        aggregation_minutes = 1
    if aggregation_minutes == 240:
        return tos_four_hour_bucket_time(time)
    if aggregation_minutes == 120:
        # Two-hour epoch buckets slide by an hour on the Eastern clock when DST
        # changes. Anchoring to exchange midnight keeps 04:00 at 04:00.
        eastern_midnight = _calendar_bucket_time(time, _DAY_MINUTES)
        seconds = aggregation_minutes * 60
        return eastern_midnight + ((time - eastern_midnight) // seconds) * seconds
    if aggregation_minutes in _CALENDAR_MINUTES:
        return _calendar_bucket_time(time, aggregation_minutes)
    seconds = aggregation_minutes * 60
    return (time // seconds) * seconds


def _normalize_bar(bar) -> dict | None:
    if not isinstance(bar, dict):
        return None
    if any(bar.get(field) is None for field in ("time", "open", "high", "low", "close")):
        return None
    try:
        time = int(float(bar["time"]))
        open_ = float(bar["open"])
        high = float(bar["high"])
        low = float(bar["low"])
        close = float(bar["close"])
    except (TypeError, ValueError):
        return None
    if time <= 0:
        return None
    try:
        volume = float(bar.get("volume") or 0)
    except (TypeError, ValueError):
        volume = 0.0
    return {
        "time": time,
        "open": open_,
        # A provider can publish a forming bar whose reported high or low has
        # not caught up with its close. Repair the bounds rather than hand
        # Lightweight Charts an invalid point, which makes it reject the whole
        # series and leave a blank chart behind.
        "high": max(high, open_, close),
        "low": min(low, open_, close),
        "close": close,
        "volume": volume,
    }


def normalize_chart_candle_bars(bars) -> list[dict]:
    """Drop unusable rows, repair bounds, de-duplicate by time, sort ascending."""
    by_time: dict[int, dict] = {}
    for bar in bars or []:
        normalized = _normalize_bar(bar)
        if normalized:
            by_time[normalized["time"]] = normalized
    return [by_time[key] for key in sorted(by_time)]


def chart_source_bar_spacing_minutes(bars, fallback_minutes: int = 5) -> int:
    """Native cadence of a tape in minutes, from the modal gap of its first rows.

    The backend has shipped `studyBars` at five- AND thirty-minute cadences
    across builds, so consumers must adapt to what actually arrived: a
    30-minute tape bucketed at 5 minutes renders one candle per six slots with
    ghost gaps between.
    """
    source = bars or []
    counts: dict[int, int] = {}
    probe = min(len(source), 60)
    for index in range(1, probe):
        try:
            gap = int(source[index]["time"]) - int(source[index - 1]["time"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if gap > 0:
            counts[gap] = counts.get(gap, 0) + 1
    best_gap = 0
    best_count = 0
    # Insertion order with a strictly-greater test, so ties keep the first gap
    # seen - the same tie-break the browser's Map iteration gives.
    for gap, count in counts.items():
        if count > best_count:
            best_gap = gap
            best_count = count
    if best_gap <= 0:
        return fallback_minutes
    # floor(x + 0.5), not Python's round(): round() is banker's rounding and
    # would send a 150-second gap to 2 minutes where the browser gives 3.
    return max(1, int(best_gap / 60 + 0.5))


def _merge_by_time(base, overrides) -> list[dict]:
    merged = {int(bar["time"]): bar for bar in base}
    for bar in overrides:
        merged[int(bar["time"])] = bar
    return [merged[key] for key in sorted(merged)]


def build_chart_display_bars(
    study_bars=None,
    fine_study_bars=None,
    live_bars=None,
    daily_bars=None,
    aggregation_minutes: int = 5,
) -> list[dict]:
    """The candles the chart actually draws — the port of buildChartDisplayBars.

    Bucketing alone is not enough to aggregate on the server: which tape a
    timeframe is built FROM is itself part of the answer. D/W/M draw from the
    long daily seed, coarse intraday timeframes draw from the study tape, and
    fine ones prefer the five-minute tape only when it reaches further back
    than the live one. Get the source wrong and the candles are still valid -
    just not the ones the trader was looking at.
    """
    minutes = max(int(aggregation_minutes or 1), 1)
    live = normalize_chart_candle_bars(live_bars)
    if minutes >= 1440:
        # D/W/M draw from the daily seed (decades) rather than the short
        # one-minute tape. Both use Eastern-midnight epochs, so the live tape's
        # aggregated current day replaces the seed's same-day row and the
        # visible candle keeps ticking.
        seed = normalize_chart_candle_bars(daily_bars)
        if seed:
            return aggregate_chart_bars(
                _merge_by_time(seed, aggregate_chart_bars(live, 1440)), minutes,
            )
    study = normalize_chart_candle_bars(study_bars)
    study_cadence = chart_source_bar_spacing_minutes(study)
    uses_study_history = bool(study) and (
        # 4H has always merged the study tape regardless of its cadence.
        minutes >= 240
        # Any timeframe at least as coarse as the tape's own cadence can use it
        # too; without this, 1h/2h fall back to the one-minute tape, which the
        # ingestion contract caps at ~5 days.
        or (0 < study_cadence <= minutes)
    )
    if not uses_study_history:
        # Low timeframes prefer the 60-day five-minute tape over the capped
        # one-minute tape - but ONLY when it actually reaches further back.
        # Measured 2026-08-12: the server's five-minute tape covered two days
        # while the client's one-minute tape covered two weeks, so using it
        # unconditionally would have SHORTENED the chart.
        fine = normalize_chart_candle_bars(fine_study_bars)
        fine_cadence = chart_source_bar_spacing_minutes(fine)
        earliest = lambda rows: int(rows[0]["time"]) if rows else 0  # noqa: E731
        fine_reaches_further = bool(fine) and (not live or earliest(fine) < earliest(live))
        if fine and 0 < fine_cadence <= minutes and fine_reaches_further:
            return _merge_by_time(
                aggregate_chart_bars(fine, minutes), aggregate_chart_bars(live, minutes),
            )
        return aggregate_chart_bars(live, minutes)
    # Bucket BOTH tapes at the study history's native cadence so no ghost
    # sub-cadence slots appear AND a live partial bucket replaces its
    # overlapping cached counterpart at the same key instead of double-counting.
    shared_cadence = 30 if study_cadence >= 30 else 5
    return aggregate_chart_bars(
        _merge_by_time(
            aggregate_chart_bars(study, shared_cadence),
            aggregate_chart_bars(live, shared_cadence),
        ),
        minutes,
    )


def aggregate_chart_bars(bars, minutes) -> list[dict]:
    """Fold a finer tape into `minutes` candles, matching the browser's output."""
    try:
        aggregation_minutes = max(int(minutes), 1)
    except (TypeError, ValueError):
        aggregation_minutes = 1
    buckets: dict[int, dict] = {}
    for bar in normalize_chart_candle_bars(bars):
        bucket_time = chart_aggregation_bucket_time(bar["time"], aggregation_minutes)
        if not bucket_time or bucket_time <= 0:
            continue
        current = buckets.get(bucket_time)
        if current is None:
            buckets[bucket_time] = {**bar, "time": bucket_time}
            continue
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
        current["volume"] = current["volume"] + bar["volume"]
    return [buckets[key] for key in sorted(buckets)]


def _recent_tape_spacing_minutes(tape, probe: int = 240) -> int:
    """Modal gap over the tape's RECENT tail, in minutes.

    chart_source_bar_spacing_minutes samples the first 60 rows, which is right
    for a display tape handed to the browser but wrong for a deep archive: a
    production studyBars file opens with a daily-spaced section, so the head
    reports 1440 for a tape whose live cadence is 30. Measured 2026-08-21 on
    IONQ/AAPL/TSLA/MSTR - head60 = 1440m, tail240 = 30m, all four.

    Session breaks (overnight, weekends) appear in the tail too, but they are
    far outnumbered by intraday gaps, so the MODE is unaffected by them.
    """
    counts: dict[int, int] = {}
    start = max(1, len(tape) - max(int(probe), 2))
    for index in range(start, len(tape)):
        try:
            gap = int(tape[index]["time"]) - int(tape[index - 1]["time"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if gap > 0:
            counts[gap] = counts.get(gap, 0) + 1
    best_gap = 0
    best_count = 0
    for gap, count in counts.items():
        if count > best_count:
            best_gap, best_count = gap, count
    if best_gap <= 0:
        return 0
    return max(1, int(best_gap / 60 + 0.5))


STUDY_TAPE_MINUTES = 30


def normalize_study_tape(rows, minutes: int = STUDY_TAPE_MINUTES) -> list[dict]:
    """Force a study tape back to its contracted cadence.

    studyBars is contractually a 30-minute tape. Trusting the broker to honour
    the cadence we asked for was the whole bug: _alpaca_fallback_chart_bars used
    to answer a "30Min" request with ONE-minute bars, so the deep study pull
    (7300 days) produced twenty years of minute data - measured 2026-08-27,
    AAPL 594,766 rows against an intended ~13,000, 97.9% of a 60 MB payload, and
    130 of 399 cached symbols (629 MB) poisoned the same way.

    Enforcing the contract here rather than only at the fetch means a tape that
    arrives too fine - from any provider, now or later - is folded before it can
    be stored, shipped, or used to seed the next extend. Already-correct tapes
    are returned UNCHANGED (identity, not a rebuild) so this is safe to call on
    every load.
    """
    normalized = normalize_chart_candle_bars(rows)
    # A tape too short to HAVE a cadence must not be folded. Measuring spacing
    # needs at least one gap, and aggregating a lone bar can drop it entirely
    # (a bucket function may reject an out-of-range timestamp), turning a small
    # cache into an empty one - caught by
    # test_chart_and_chain_browser_caches_survive_restart, which round-trips a
    # single synthetic bar.
    if len(normalized) < 3:
        return normalized
    spacing = _recent_tape_spacing_minutes(normalized)
    # Only ever COARSEN. A tape that is already 30-minute or wider is left
    # alone - re-bucketing a 4H archive onto a 30-minute grid would invent
    # slots that never traded.
    if spacing >= minutes:
        return normalized
    folded = aggregate_chart_bars(normalized, minutes)
    # Never trade a real tape for an empty one. Folding should always yield at
    # least one bucket; if it does not, the input is not what this function
    # assumes and the safe answer is to leave it alone.
    return folded if folded else normalized


def extend_study_tape(existing, live_bars, now_epoch: int | None = None) -> list[dict]:
    """Append completed buckets from a fresh fine tape onto a deep study tape.

    The deep study tape only ships with FULL chart builds, and the splice
    preserves it wholesale so a recency refresh can never erase the archive.
    That was correct about depth and wrong about freshness: once the
    full-rebuild storm was fixed (2026-08-21), nothing advanced the tape
    intraday any more, because the storm had been its accidental refresher.

    Measured that day at 12:27 ET - AAPL's study tape stood at 10:00, TSLA at
    09:00, MSTR at 08:30 and IONQ at 08/20 14:30, while every 1-minute tape was
    current to the minute. The MTF engines aggregate 1H/2H/4H from this tape,
    so IONQ's 4H state still read yesterday's PUT while TOS had flipped to
    CALL4H. Charts looked perfect; only the signals painted on them were stale.

    Extending it here keeps the depth guarantee and adds the missing tail:

    * The cadence is MEASURED, never assumed - this tape has shipped at both
      5-minute and 30-minute spacing across builds.
    * Only buckets strictly newer than the tape's last bar are appended, so
      history is immutable and no duplicate timestamps appear.
    * Only COMPLETED buckets are appended. These are TOS secondary-candle
      studies: a half-built candle repaints, which makes a cross appear and
      then vanish.
    """
    tape = normalize_chart_candle_bars(existing)
    if not tape:
        # No cadence to anchor to, and no archive to protect.
        return tape
    fine = normalize_chart_candle_bars(live_bars)
    if not fine:
        return tape
    # From the TAIL, never the head: this archive opens daily-spaced, and
    # reading the head reported 1440 minutes for a 30-minute tape, which made
    # every candidate bucket "incomplete" and appended nothing.
    spacing_minutes = _recent_tape_spacing_minutes(tape)
    if spacing_minutes <= 0:
        return tape
    resolved_now = int(now_epoch) if now_epoch is not None else int(time.time())
    span_seconds = spacing_minutes * 60
    last_time = int(tape[-1]["time"])
    appended = [
        bucket
        for bucket in aggregate_chart_bars(fine, spacing_minutes)
        if int(bucket["time"]) > last_time
        and int(bucket["time"]) + span_seconds <= resolved_now
    ]
    return tape + appended
