"""Premarket Mag7 scanner primitives.

Mirrors what the 5-minute chart draws. The CALL2H/CALL4H half is read
straight from the cached ``mtfSignals`` array, so only squeeze release is
reimplemented here -- and it is pinned to the JavaScript
(``frontend/src/squeezeRelease.js``) by a golden fixture
(``tests/test_premarket_scanner.py``). Stdlib only: this runs on a 5-second
request path and must not pull pandas in.
"""

from __future__ import annotations

import math
from collections import deque
from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

SQUEEZE_LENGTH = 20
# 15m and 30m are deliberately absent: they fire near-continuously and would
# drown the strength score.
FIRE_TIMEFRAMES = ((60, "1h"), (120, "2h"), (240, "4h"), (1440, "D"))
# The nine the trader actually watches -- NOT the 22-symbol Mag7 option
# watchlist, which carries leveraged ETFs he does not scan. Every one of
# these is in QUICK_STRIP_WARM_SYMBOLS (api_server.py), so its chart tape is
# warmed in the background; a symbol added here but not there would never
# produce a row.
PREMARKET_SCAN_SYMBOLS = (
    "AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX",
)


def _eastern_midnight(timestamp: int) -> int:
    moment = datetime.fromtimestamp(int(timestamp), tz=EASTERN)
    return int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())


def chart_bucket_time(timestamp: object, minutes: object) -> int | None:
    """Bucket start for the CHART's aggregation clocks
    (``chartAggregationBucketTime`` in frontend/src/chartAggregation.js).

    These are deliberately not the backend MTF engine's clocks. Getting this
    wrong silently misplaces every fire.
    """
    try:
        time_value = math.floor(float(timestamp or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    if time_value <= 0:
        return None
    span = max(int(minutes or 1), 1)
    if span == 1440:
        return _eastern_midnight(time_value)
    if span == 240:
        # TOS aggregates equity bars from midnight CENTRAL, one hour behind
        # Eastern, so the visible ET boundaries are 01:00/05:00/09:00/...
        anchor = _eastern_midnight(time_value) + 3600
        four_hours = 240 * 60
        return anchor + ((time_value - anchor) // four_hours) * four_hours
    if span == 120:
        # Anchored to exchange midnight so 04:00 stays 04:00 across DST.
        anchor = _eastern_midnight(time_value)
        seconds = span * 60
        return anchor + ((time_value - anchor) // seconds) * seconds
    seconds = span * 60
    return (time_value // seconds) * seconds


def _finite(value: object) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def normalize_chart_bars(bars: object) -> list[dict]:
    """``normalizeChartCandleBars``: drop incomplete bars, last one wins per
    timestamp, sort ascending, and repair a high/low that has not caught up
    with open/close."""
    by_time: dict[int, dict] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        values = [_finite(bar.get(key)) for key in ("time", "open", "high", "low", "close")]
        if any(value is None for value in values):
            continue
        time_raw, open_value, high, low, close = values
        time_value = math.floor(time_raw)
        if time_value <= 0:
            continue
        by_time[time_value] = {
            "time": time_value,
            "open": open_value,
            "high": max(high, open_value, close),
            "low": min(low, open_value, close),
            "close": close,
        }
    return [by_time[key] for key in sorted(by_time)]


def aggregate_chart_bars(bars: object, minutes: object) -> list[dict]:
    """``aggregateChartBars``: first open, extreme high/low, last close per
    bucket, after the chart's own normalization."""
    buckets: dict[int, dict] = {}
    for bar in normalize_chart_bars(bars):
        bucket_time = chart_bucket_time(bar["time"], minutes)
        if not bucket_time or bucket_time <= 0:
            continue
        current = buckets.get(bucket_time)
        if current is None:
            buckets[bucket_time] = {**bar, "time": bucket_time}
            continue
        current["high"] = max(current["high"] or 0.0, bar["high"] or 0.0)
        current["low"] = min(current["low"] or 0.0, bar["low"] or 0.0)
        current["close"] = bar["close"] or current["close"] or 0.0
    return [buckets[key] for key in sorted(buckets)]


def rolling_average(values: object, period: object) -> list[float]:
    """Running-total average with a partial leading window.

    Replicates the JavaScript's running-total-with-subtraction exactly,
    including its float accumulation order -- recomputing each window
    instead would drift from the chart in the last decimal places.
    """
    length = max(int(period or 1), 1)
    output: list[float] = []
    window: deque[float] = deque()
    total = 0.0
    for value in values or []:
        numeric = float(value or 0)
        window.append(numeric)
        total += numeric
        if len(window) > length:
            total -= window.popleft()
        output.append(total / len(window))
    return output


def rolling_stdev(values: object, period: object) -> list[float]:
    """POPULATION standard deviation (divide by N), partial leading window.

    Sums with a plain left-to-right loop, like ``Array.reduce``: Python
    3.12+ ``sum()`` uses compensated summation and would differ from the
    chart in the last bit.
    """
    length = max(int(period or 1), 1)
    numbers = [float(value or 0) for value in (values or [])]
    output: list[float] = []
    for index in range(len(numbers)):
        window = numbers[max(0, index - length + 1): index + 1]
        count = max(len(window), 1)
        total = 0.0
        for item in window:
            total += item
        average = total / count
        squares = 0.0
        for item in window:
            difference = item - average
            squares += difference * difference
        output.append(math.sqrt(squares / count))
    return output


def true_ranges(bars: object) -> list[float]:
    source = list(bars or [])
    output: list[float] = []
    for index, bar in enumerate(source):
        high = float((bar or {}).get("high") or 0)
        low = float((bar or {}).get("low") or 0)
        if index == 0:
            output.append(high - low)
            continue
        previous_close = float((source[index - 1] or {}).get("close") or 0)
        output.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    return output


def _last_source_time(source: list) -> float:
    if not source:
        return 0.0
    return _finite(source[-1].get("time")) or 0.0


def squeeze_release_events(bars: object, minutes: object) -> list[dict]:
    """Squeeze releases on one aggregation, matching ``squeezeReleaseEvents``.

    A release is a bucket-CLOSE fact (live commit 461d4af): the forming
    bucket's bands and ATR move with every tick, so treating it as live made
    the flame flicker in and out.
    """
    source = [bar for bar in (bars or []) if isinstance(bar, dict)]
    span = max(int(minutes or 1), 1)
    timeframe_bars = aggregate_chart_bars(source, span)
    if len(timeframe_bars) <= SQUEEZE_LENGTH:
        return []

    closes = [float(bar["close"] or 0) for bar in timeframe_bars]
    average = rolling_average(closes, SQUEEZE_LENGTH)
    deviation = rolling_stdev(closes, SQUEEZE_LENGTH)
    keltner = rolling_average(true_ranges(timeframe_bars), SQUEEZE_LENGTH)
    # Unreduced form kept on purpose so it reads line-for-line against the
    # JavaScript: (avg + 2*stdev) - (avg + 1.5*atr) <= 0.
    in_squeeze = [
        average[index] + 2 * deviation[index] - (average[index] + 1.5 * keltner[index]) <= 0
        for index in range(len(timeframe_bars))
    ]

    last_source_time = _last_source_time(source)
    events: list[dict] = []
    for index in range(SQUEEZE_LENGTH, len(timeframe_bars)):
        if in_squeeze[index] or not in_squeeze[index - 1]:
            continue
        bucket_time = int(timeframe_bars[index]["time"])
        close_time = bucket_time + span * 60
        if close_time > last_source_time:
            continue
        events.append({
            "minutes": span,
            "bucketTime": bucket_time,
            "closeTime": close_time,
            "tone": "bull" if timeframe_bars[index]["close"] > timeframe_bars[index - 1]["close"] else "bear",
        })
    return events


# --- Window filter, match rule and strength score -------------------------

WINDOW_START_MINUTE = 6 * 60          # 06:00 ET
WINDOW_END_MINUTE = 9 * 60 + 30       # 09:30 ET
CALL_LABELS = ("CALL2H", "CALL4H")
STRONG_THRESHOLD = 3                  # "more than 3" -> STRONG


def premarket_window(now_et: datetime) -> tuple[int, int]:
    """Inclusive 06:00:00-09:30:00 ET bounds for ``now_et``'s trading day."""
    day = now_et.astimezone(EASTERN)
    start = day.replace(
        hour=WINDOW_START_MINUTE // 60, minute=WINDOW_START_MINUTE % 60, second=0, microsecond=0
    )
    end = day.replace(
        hour=WINDOW_END_MINUTE // 60, minute=WINDOW_END_MINUTE % 60, second=0, microsecond=0
    )
    return int(start.timestamp()), int(end.timestamp())


def window_call_signals(mtf_signals: object, start: int, end: int) -> list[dict]:
    """CALL2H/CALL4H from the chart's own array, inside the window.

    Read-only over ``mtfSignals`` -- the exact array the chart draws its
    boxes from -- so these can never disagree with the chart.
    """
    kept: list[dict] = []
    for signal in mtf_signals or []:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("direction") or "").upper() != "CALL":
            continue
        if str(signal.get("label") or "").upper() not in CALL_LABELS:
            continue
        try:
            when = int(signal.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if start <= when <= end:
            kept.append(signal)
    return kept


def latest_closed_bucket_time(bars: object, minutes: object) -> int | None:
    """Start of the newest bucket that has CLOSED, by the same rule the
    release detector uses (bucket start + span <= last source bar time)."""
    source = [bar for bar in (bars or []) if isinstance(bar, dict)]
    span = max(int(minutes or 1), 1)
    last_source_time = _last_source_time(source)
    closed = [
        int(bar["time"])
        for bar in aggregate_chart_bars(source, span)
        if int(bar["time"]) + span * 60 <= last_source_time
    ]
    return closed[-1] if closed else None


def window_fires(bars: object, start: int, end: int) -> list[dict]:
    """Fires the chart is showing this premarket.

    1h/2h/4h qualify on their bucket CLOSE landing in the window. Daily is
    special: a daily candle does not close until midnight, so a D fire seen
    during premarket is the previous session's release. Only the MOST
    RECENTLY CLOSED daily candle counts -- an older release anywhere in the
    tape is history, not a signal (the first build reported a July fire in
    August by taking the newest release in the whole tape).
    """
    fires: list[dict] = []
    for minutes, label in FIRE_TIMEFRAMES:
        events = squeeze_release_events(bars, minutes)
        if not events:
            continue
        if minutes == 1440:
            latest = events[-1]
            if (
                latest["bucketTime"] == latest_closed_bucket_time(bars, minutes)
                and latest["closeTime"] <= end
            ):
                fires.append({**latest, "label": label})
            continue
        fires.extend(
            {**event, "label": label}
            for event in events
            if start <= event["closeTime"] <= end
        )
    return fires


def score_strength(calls: object, fires: object) -> tuple[int, str]:
    """One point per hit. Max 8: four CALL slots and four fire timeframes."""
    score = len(list(calls or [])) + len(list(fires or []))
    if score > STRONG_THRESHOLD:
        return score, "STRONG"
    if score == STRONG_THRESHOLD:
        return score, "MODERATE"
    return score, "WEAK"


def _fire_date_label(fire: dict) -> str:
    # Daily fires carry their own SESSION date (the bucket's day), so a
    # prior-session release is never mistaken for a fresh premarket event.
    # Intraday fires carry their bucket-close time.
    if int(fire.get("minutes") or 0) == 1440:
        return datetime.fromtimestamp(int(fire["bucketTime"]), tz=EASTERN).date().isoformat()
    return datetime.fromtimestamp(int(fire["closeTime"]), tz=EASTERN).isoformat()


def premarket_scan_row(
    symbol: str,
    payload: object,
    now_et: datetime,
    *,
    fires: object = None,
) -> dict | None:
    """One scanner row, or None when the symbol has nothing (or is cold).

    There is intentionally no FORMING/CONFIRMED flag: scanner.py stamps
    ``liveForming=True`` on every MTF signal, so such a badge would show on
    every row forever and mean nothing.
    """
    if not isinstance(payload, dict):
        return None
    bars = payload.get("bars")
    if not bars:
        return None

    start, end = premarket_window(now_et)
    calls = window_call_signals(payload.get("mtfSignals"), start, end)
    matched_fires = list(fires) if fires is not None else window_fires(
        payload.get("studyBars") or bars, start, end
    )
    if not calls and not matched_fires:
        return None

    score, strength = score_strength(calls, matched_fires)
    times = [int(signal["time"]) for signal in calls]
    times += [int(fire["closeTime"]) for fire in matched_fires]
    first = min(times) if times else None

    def labels(family: str) -> list[str]:
        seen: list[str] = []
        for signal in calls:
            if str(signal.get("family") or "") != family:
                continue
            label = str(signal.get("label") or "")
            if label and label not in seen:
                seen.append(label)
        return seen

    try:
        last_price = round(float(bars[-1].get("close") or 0), 2)
    except (AttributeError, TypeError, ValueError):
        last_price = 0.0

    return {
        "symbol": str(symbol or "").upper(),
        "signalAt": datetime.fromtimestamp(first, tz=EASTERN).isoformat() if first else None,
        "lastPrice": last_price,
        "signals48": labels("4x8"),
        "signals920": labels("9x20"),
        "fires": [fire["label"] for fire in matched_fires],
        "fireDates": {fire["label"]: _fire_date_label(fire) for fire in matched_fires},
        "score": score,
        "strength": strength,
    }
