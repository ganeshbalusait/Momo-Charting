"""Premarket Mag7 scanner primitives (06:00-09:30 ET).

The scanner MIRRORS the 5-minute chart; it never forms a second opinion.
CALL2H/CALL4H are read straight from the cached ``mtfSignals`` array the
chart itself draws from, so that half cannot drift. Only squeeze release is
reimplemented here, because it lives in browser JavaScript
(``frontend/src/squeezeRelease.js``) and the server cannot see it -- and it
is pinned to that JavaScript event-for-event by a golden fixture in
``tests/test_premarket_scanner.py``.

Stdlib only: this runs on a request path polled every five seconds and must
not pull pandas in.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")

SQUEEZE_LENGTH = 20
# 15m and 30m are deliberately absent: they fire near-continuously and would
# drown the strength score.
FIRE_TIMEFRAMES = ((60, "1h"), (120, "2h"), (240, "4h"), (1440, "D"))
# The nine the trader actually watches -- NOT the 22-symbol Mag7 option
# watchlist, which carries leveraged ETFs he does not scan. Every one of
# these is in api_server.QUICK_STRIP_WARM_SYMBOLS, the priority set the chart
# warmer builds WITHOUT yielding to the trader, which is what lets the
# scanner see them while he has no chart open. A symbol added here but not
# there would silently never produce a row.
PREMARKET_SCAN_SYMBOLS = (
    "AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX",
)

WINDOW_START_HOUR = 6           # 06:00 ET
WINDOW_END_HOUR, WINDOW_END_MINUTE = 9, 30
CALL_LABELS = ("CALL2H", "CALL4H")
STRONG_THRESHOLD = 3            # "more than 3" -> STRONG


# ----------------------------------------------------------------------
# Chart aggregation clocks
#
# These are deliberately NOT the backend MTF engine's clocks; see the note in
# frontend/src/chartAggregation.js. Getting this wrong silently misplaces
# every fire.
# ----------------------------------------------------------------------

# Keyed by UTC hour, mirroring chartAggregation.js's EASTERN_CALENDAR_DATE_CACHE.
# Without it, zoneinfo datetime construction ran PER CANDLE -- 13k+ bars x 4
# timeframes x 9 tickers on every 5s scanner poll. py-spy caught four request
# threads simultaneously inside this function on 2026-08-21 while the endpoint
# timed out at 30s; overlapping polls piled up faster than they finished.
_EASTERN_MIDNIGHT_CACHE: dict[int, int] = {}


def _eastern_midnight(timestamp: int) -> int:
    hour_key = int(timestamp) // 3600
    hit = _EASTERN_MIDNIGHT_CACHE.get(hour_key)
    if hit is not None:
        return hit
    moment = datetime.fromtimestamp(int(timestamp), tz=EASTERN)
    value = int(moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
    if len(_EASTERN_MIDNIGHT_CACHE) > 200_000:
        _EASTERN_MIDNIGHT_CACHE.clear()
    _EASTERN_MIDNIGHT_CACHE[hour_key] = value
    return value


def chart_bucket_time(timestamp: object, minutes: object) -> int | None:
    """Bucket start for the CHART's aggregation clocks."""
    try:
        time_value = int(timestamp or 0)
    except (TypeError, ValueError):
        return None
    if time_value <= 0:
        return None
    span = max(int(minutes or 1), 1)
    if span == 1440:
        return _eastern_midnight(time_value)
    if span == 240:
        # TOS aggregates equity bars from midnight CENTRAL, one hour behind
        # Eastern, so the visible ET boundaries are 01:00/05:00/09:00/13:00.
        anchor = _eastern_midnight(time_value) + 3600
        four_hours = 240 * 60
        return anchor + ((time_value - anchor) // four_hours) * four_hours
    if span == 120:
        # Same midnight-CENTRAL clock as the 4h bars: TOS 2h bars run
        # 01/03/05/07/09/11... ET with extended hours on (TOS support: nine
        # 2h candles in a 16-hour session), so the premarket scan's 2h bar is
        # 09:00-11:00, not 08:00-10:00. Verified against the trader's chart
        # 2026-08-24 (P2H 07:00 / CALL2H 09:10 sit on those boundaries).
        anchor = _eastern_midnight(time_value) + 3600
        seconds = span * 60
        return anchor + ((time_value - anchor) // seconds) * seconds
    seconds = span * 60
    return (time_value // seconds) * seconds


def aggregate_chart_bars(bars: object, minutes: object) -> list[dict]:
    """First open, extreme high/low, last close per bucket.

    Input must be time-ascending, which every cached chart tape already is.
    """
    buckets: dict[int, dict] = {}
    for bar in bars or []:
        if not isinstance(bar, dict):
            continue
        try:
            time_value = int(bar["time"])
            open_value = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        bucket_time = chart_bucket_time(time_value, minutes)
        if not bucket_time or bucket_time <= 0:
            continue
        current = buckets.get(bucket_time)
        if current is None:
            buckets[bucket_time] = {
                "time": bucket_time,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
            }
            continue
        current["high"] = max(current["high"], high)
        current["low"] = min(current["low"], low)
        current["close"] = close
    return [buckets[key] for key in sorted(buckets)]


# ----------------------------------------------------------------------
# Squeeze release -- the one thing not already on the chart payload
# ----------------------------------------------------------------------

def rolling_average(values: object, period: object) -> list[float]:
    """Running-total average with a partial leading window.

    Replicates the JavaScript's running-total-with-subtraction exactly,
    including its float accumulation order. Recomputing each window instead
    would drift from the chart in the last decimal places.
    """
    length = max(int(period or 1), 1)
    output: list[float] = []
    window: list[float] = []
    total = 0.0
    for value in values or []:
        numeric = float(value or 0)
        window.append(numeric)
        total += numeric
        if len(window) > length:
            total -= window.pop(0)
        output.append(total / len(window))
    return output


def rolling_stdev(values: object, period: object) -> list[float]:
    """POPULATION standard deviation (divide by N), partial leading window."""
    length = max(int(period or 1), 1)
    numbers = [float(value or 0) for value in (values or [])]
    output: list[float] = []
    for index in range(len(numbers)):
        window = numbers[max(0, index - length + 1): index + 1]
        count = max(len(window), 1)
        average = sum(window) / count
        output.append((sum((item - average) ** 2 for item in window) / count) ** 0.5)
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


def squeeze_release_events(bars: object, minutes: object) -> list[dict]:
    """Squeeze releases on one aggregation, matching the chart's flames.

    A release is a bucket-CLOSE fact (live commit 461d4af): the forming
    bucket's bands and ATR move with every tick, so treating it as live made
    the flame flicker in and out and wander between the bucket's high and low.
    """
    source = [bar for bar in (bars or []) if isinstance(bar, dict)]
    span = max(int(minutes or 1), 1)
    timeframe_bars = aggregate_chart_bars(source, span)
    if len(timeframe_bars) <= SQUEEZE_LENGTH:
        return []

    closes = [float(bar["close"]) for bar in timeframe_bars]
    average = rolling_average(closes, SQUEEZE_LENGTH)
    deviation = rolling_stdev(closes, SQUEEZE_LENGTH)
    keltner = rolling_average(true_ranges(timeframe_bars), SQUEEZE_LENGTH)
    in_squeeze = [
        (average[index] + 2 * deviation[index]) - (average[index] + 1.5 * keltner[index]) <= 0
        for index in range(len(timeframe_bars))
    ]

    try:
        last_source_time = int(source[-1]["time"]) if source else 0
    except (KeyError, TypeError, ValueError):
        last_source_time = 0

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
            "tone": "bull" if closes[index] > closes[index - 1] else "bear",
        })
    return events


# ----------------------------------------------------------------------
# Window, match rule, strength score
# ----------------------------------------------------------------------

def premarket_window(now_et: datetime) -> tuple[int, int]:
    """The [start, end] epoch bounds of today's 06:00-09:30 ET scan window."""
    day = now_et.astimezone(EASTERN)
    start = day.replace(hour=WINDOW_START_HOUR, minute=0, second=0, microsecond=0)
    end = day.replace(hour=WINDOW_END_HOUR, minute=WINDOW_END_MINUTE, second=0, microsecond=0)
    return int(start.timestamp()), int(end.timestamp())


def overnight_window(now_et: datetime) -> tuple[int, int]:
    """[midnight, 09:30] ET - the wider window the cyan D-M scan uses.

    The trader's request (2026-08-23, off a TSLA chart carrying a CALLD at
    01:00 ET): a higher-timeframe cyan cross that prints overnight must be
    on the scanner while it is live, not discovered at 06:00. Daily-and-up
    buckets earn the wider window because their crosses are rare and slow;
    the intraday CALL2H/CALL4H window deliberately stays 06:00-09:30.
    """
    day = now_et.astimezone(EASTERN)
    start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    end = day.replace(hour=WINDOW_END_HOUR, minute=WINDOW_END_MINUTE, second=0, microsecond=0)
    return int(start.timestamp()), int(end.timestamp())


# The chart's D-to-M bubbles come from three families that
# build_ganesh_higher_timeframe_signal_payload computes server-side over
# Daily/2D/3D/4D/Weekly/Monthly buckets: ganesh920 (cyan 9/20, CALLD..CALLM),
# ganesh48 (yellow 4/8, same labels), and ganeshMacd (MACD-D..MACD-M, whose
# bullish cross also carries direction CALL). Reading the SAME payload array
# the chart draws (and the MAG7 tables already consume) is what keeps a
# scanner row and a chart bubble identical.
CYAN_HIGHER_FAMILY = "ganesh920"
YELLOW_HIGHER_FAMILY = "ganesh48"
MACD_HIGHER_FAMILY = "ganeshMacd"


def _higher_signal_times(*family_lists: list[dict]) -> dict[str, str]:
    """Label -> ET print time, first-write-wins across the family lists."""
    stamps: dict[str, str] = {}
    for signals in family_lists:
        for signal in signals:
            stamps.setdefault(
                str(signal.get("label") or ""),
                datetime.fromtimestamp(signal["time"], tz=EASTERN).isoformat(),
            )
    stamps.pop("", None)
    return stamps


def _dedup_labels(signals: list[dict]) -> list[str]:
    """Unique labels in first-seen order (a bucket can re-cross in a replay)."""
    seen: list[str] = []
    for signal in signals:
        label = str(signal.get("label") or "")
        if label and label not in seen:
            seen.append(label)
    return seen


def window_cyan_higher_signals(ganesh_signals: object, start: int, end: int) -> list[dict]:
    """Cyan CALL events from the D-to-M replay, inside the window."""
    return window_higher_signals(ganesh_signals, start, end, CYAN_HIGHER_FAMILY)


def window_higher_signals(ganesh_signals: object, start: int, end: int,
                          family: str) -> list[dict]:
    """Bullish D-to-M events of one family, inside the window.

    Mirrors the MAG7 table's FILTER exactly (family + direction + window,
    api_server._mag7_chart_signal_row). Filter parity is only half the
    story - the SOURCE array must also be equally fresh, which is why the
    scanner path replays the D-M contract off the advancing merged tape
    (api_server, adversarial review 2026-08-23) instead of trusting the
    build-time payload contract, which only full chart builds refresh.
    A bullish MACD event also carries direction CALL (its label is
    MACD-<tf>), so one direction check covers all three families.
    """
    kept: list[dict] = []
    for signal in ganesh_signals if isinstance(ganesh_signals, list) else []:
        if not isinstance(signal, dict):
            continue
        if str(signal.get("family") or "") != family:
            continue
        if str(signal.get("direction") or "").upper() != "CALL":
            continue
        label = str(signal.get("label") or "").strip()
        if not label:
            continue
        try:
            moment = int(signal.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if start <= moment <= end:
            kept.append({"label": label, "time": moment, "family": family})
    # One event per label, keeping the EARLIEST: a replayed bucket can cross
    # more than once, but the row's question is "did CALLD print, and when
    # did it first show" - and the score must not double-count a label.
    earliest: dict[str, dict] = {}
    for signal in kept:
        current = earliest.get(signal["label"])
        if current is None or signal["time"] < current["time"]:
            earliest[signal["label"]] = signal
    return sorted(earliest.values(), key=lambda signal: signal["time"])


def window_call_signals(mtf_signals: object, start: int, end: int) -> list[dict]:
    """CALL2H/CALL4H from the chart's OWN array, inside the window.

    Read-only over ``mtfSignals`` -- the exact array App.jsx draws its boxes
    from -- so a scanner row and a chart bubble can never disagree.

    C2H/C4H (higher timeframe not confirming) are excluded: the user asked
    for CALL specifically.
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


# ---------------------------------------------------------------------------
# TOS "AlertX Bull Momo" parity (trader's scan, 2026-08-24). The two thinkScript
# studies, verbatim:
#
#   2h:  def c48  = ExpAverage(close, 4) crosses above ExpAverage(close, 8);
#        def c920 = ExpAverage(close, 9) crosses above ExpAverage(close, 20);
#        def inPM = SecondsFromTime(0800) >= 0 and SecondsTillTime(0930) > 0;
#        plot scan = Highest(if (c48 or c920) and inPM then 1 else 0, 12) > 0;
#   4h:  same, with inPM = SecondsFromTime(0700) >= 0 and SecondsTillTime(0930) > 0
#   "Any of the following" over the two, "Stock Last >= 3", EXT hours on.
#
# The chart engine's mtfSignals only carry the CURRENT bar's cross state, so
# they cannot answer Highest(..., 12); this evaluates the script itself on the
# 2h/4h bars built with the same TOS aggregation clock the chart uses.
# SecondsFromTime/SecondsTillTime read the BAR'S START on an aggregated chart,
# so a 2h cross qualifies only on the 08:00 bucket and a 4h cross only on the
# 09:00 bucket (TOS 4h buckets sit at 01/05/09/13/17 ET).
#
# WIDENED 2026-09-28 at the trader's request, knowingly past TOS parity: the
# 2h window opens at 07:00 (adds the 07:00 bucket) and the 4h at 05:00 (adds
# the 05:00 bucket). NVDA gapped +3% that morning with CALL4H on the 05:00
# bucket and CALL2H on the 07:00 bucket and never showed. Back-test (9
# tickers, Dec 2025 - Sep 2026, judged at 09:30): the added rows did no better
# than a random ticker-day (-0.09% by 10:30 vs +0.14% for the old window) -
# this is for visibility of early movers, not edge.
# ---------------------------------------------------------------------------
TOS_SCAN_LOOKBACK_BARS = 12
TOS_SCAN_MIN_LAST = 3.0
# minutes -> (timeframe label, window start, window end) as seconds from ET midnight
TOS_SCAN_WINDOWS = {
    120: ("2H", 7 * 3600, 9 * 3600 + 30 * 60),
    240: ("4H", 5 * 3600, 9 * 3600 + 30 * 60),
}


def ema_series(values: object, period: object) -> list[float]:
    """thinkScript ExpAverage: alpha 2/(n+1), seeded with the first value."""
    source = [float(value) for value in (values or [])]
    span = max(int(period or 1), 1)
    if not source:
        return []
    alpha = 2.0 / (span + 1.0)
    out = [source[0]]
    for value in source[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def crosses_above(fast: list[float], slow: list[float], index: int) -> bool:
    """thinkScript `crosses above` at ``index``: below-or-equal, then above."""
    if index <= 0 or index >= len(fast) or index >= len(slow):
        return False
    return fast[index - 1] <= slow[index - 1] and fast[index] > slow[index]


def tos_scan_bars(bars: object, study_bars: object, minutes: object) -> list[dict]:
    """The 2h/4h tape the scan runs on.

    The one-minute live tape can be a single session deep (REST fallback), too
    short for a 12-bar lookback or a 20-period EMA; the 30-minute study tape is
    months deep but coarser. Aggregate both to the target bucket and let a
    bucket built from one-minute bars replace the coarse one where it exists.
    """
    by_time: dict[int, dict] = {}
    for bar in aggregate_chart_bars(study_bars, minutes):
        by_time[int(bar["time"])] = bar
    fine = list(bars or [])
    # The live tape can start MID-bucket (a tail that begins at 07:35 builds
    # a 06:00 two-hour bucket missing its first 95 minutes). That partial
    # bucket must not replace the complete coarse one: its open/high/low are
    # wrong, the EMAs shift and crosses appear or vanish (AMZN dropped off
    # the scan for exactly this on 2026-08-24).
    try:
        first_fine_time = int((fine[0] or {}).get("time") or 0) if fine else 0
    except (AttributeError, TypeError, ValueError):
        first_fine_time = 0
    first_full_bucket = chart_bucket_time(first_fine_time, minutes) if first_fine_time > 0 else None
    if first_full_bucket is not None and first_full_bucket < first_fine_time:
        first_full_bucket += max(int(minutes or 1), 1) * 60  # the leading bucket is partial
    for bar in aggregate_chart_bars(fine, minutes):
        bucket = int(bar["time"])
        if first_full_bucket is not None and bucket < first_full_bucket and bucket in by_time:
            continue
        by_time[bucket] = bar
    return [by_time[key] for key in sorted(by_time)]


def tos_bull_momo_signals(bars: object, study_bars: object = None) -> list[dict]:
    """Every cross the TOS scan would fire on, newest tape included.

    Returns chart-style signal dicts (label CALL2H/CALL4H, family 4x8/9x20,
    direction CALL, time = bucket start) so the existing columns, strength
    tiers and forming logic consume them unchanged.
    """
    signals: list[dict] = []
    for minutes, (timeframe, window_start, window_end) in TOS_SCAN_WINDOWS.items():
        aggregated = tos_scan_bars(bars, study_bars, minutes)
        if len(aggregated) < 2:
            continue
        closes = [float(bar.get("close") or 0) for bar in aggregated]
        ema4, ema8 = ema_series(closes, 4), ema_series(closes, 8)
        ema9, ema20 = ema_series(closes, 9), ema_series(closes, 20)
        first_index = max(1, len(aggregated) - TOS_SCAN_LOOKBACK_BARS)
        for index in range(first_index, len(aggregated)):
            bucket_time = int(aggregated[index]["time"])
            seconds_from_midnight = bucket_time - _eastern_midnight(bucket_time)
            if not (window_start <= seconds_from_midnight < window_end):
                continue
            for family, color, fast, slow in (
                ("4x8", "yellow", ema4, ema8),
                ("9x20", "cyan", ema9, ema20),
            ):
                if crosses_above(fast, slow, index):
                    signals.append({
                        "label": f"CALL{timeframe}",
                        "family": family,
                        "color": color,
                        "timeframe": timeframe,
                        "direction": "CALL",
                        "time": bucket_time,
                        "tosScan": True,
                    })
    signals.sort(key=lambda signal: (int(signal["time"]), signal["label"], signal["family"]))
    return signals


# The chart's TOS MTF engine (scanner._tos_live_mtf_projection, mode
# "tos_final_secondary_5m") is the single source of truth for the crosses:
# it runs on the 5m tape with the overnight session, TOS's final-close
# stair-step and the midnight-Central candle clock. The scanner must never
# disagree with the chart (2026-08-24 23:00 ET: META showed CALL4H on the
# scanner from this module's own EMA path while the chart had no 4H label),
# so when the payload carries that engine's signals the scan is evaluated
# on THEM - only the TOS scan window and the 12-bar lookback are applied here.
CHART_ENGINE_MODE = "tos_final_secondary_5m"


def tos_scan_from_chart_signals(mtf_signals: object, bars: object, study_bars: object = None) -> list[dict]:
    """The TOS scan (c48/c920 on 2h and 4h, window on the bar start, last 12
    bars) evaluated on the chart engine's crosses.

    The scan condition is the raw ``EMA4 crosses above EMA8`` - a compact
    ``C2H`` bubble (cross fired, next timeframe not confirming) counts just
    like ``CALL2H``; only the direction matters.
    """
    signals: list[dict] = []
    if not isinstance(mtf_signals, list):
        return signals
    for minutes, (timeframe, window_start, window_end) in TOS_SCAN_WINDOWS.items():
        aggregated = tos_scan_bars(bars, study_bars, minutes)
        recent = {int(bar["time"]) for bar in aggregated[-TOS_SCAN_LOOKBACK_BARS:]}
        if not recent:
            continue
        seen: set[tuple[str, int]] = set()
        for signal in mtf_signals:
            if not isinstance(signal, dict):
                continue
            if str(signal.get("timeframe") or "").upper() != timeframe:
                continue
            if str(signal.get("direction") or "").upper() != "CALL":
                continue
            family = str(signal.get("family") or "")
            if family not in ("4x8", "9x20"):
                continue
            try:
                bucket_time = int(signal.get("candleTimestamp") or 0) or int(
                    chart_bucket_time(int(signal.get("time") or 0), minutes) or 0
                )
            except (TypeError, ValueError):
                continue
            if bucket_time <= 0 or bucket_time not in recent:
                continue
            seconds_from_midnight = bucket_time - _eastern_midnight(bucket_time)
            if not (window_start <= seconds_from_midnight < window_end):
                continue
            key = (family, bucket_time)
            if key in seen:
                continue
            seen.add(key)
            signals.append({
                "label": f"CALL{timeframe}",
                "family": family,
                "color": "yellow" if family == "4x8" else "cyan",
                "timeframe": timeframe,
                "direction": "CALL",
                "time": bucket_time,
                "tosScan": True,
                "chartEngine": True,
            })
    signals.sort(key=lambda signal: (int(signal["time"]), signal["label"], signal["family"]))
    return signals


def previous_session_close(bars: object, now_et: datetime) -> float | None:
    """The prior session's regular close: the last bar at or before 16:00 ET
    on the most recent earlier calendar day that has bars."""
    today_midnight = int(now_et.astimezone(EASTERN).replace(
        hour=0, minute=0, second=0, microsecond=0,
    ).timestamp())
    # Walk back day by day: a stray evening-only bar (or a holiday tail)
    # on the nearest earlier day must not end the search with nothing.
    days_seen: list[int] = []
    for bar in reversed(list(bars or [])):
        try:
            when = int(bar.get("time") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if when <= 0 or when >= today_midnight:
            continue
        day = _eastern_midnight(when)
        if not days_seen or days_seen[-1] != day:
            days_seen.append(day)
            if len(days_seen) > 7:
                break
        if when - day <= 16 * 3600:
            try:
                return float(bar.get("close"))
            except (TypeError, ValueError):
                continue
    return None


def change_percent(bars: object, now_et: datetime) -> float | None:
    """TOS %Change: last price against the prior session's regular close."""
    try:
        last = float((list(bars or [])[-1] or {}).get("close") or 0)
    except (AttributeError, TypeError, ValueError, IndexError):
        return None
    previous = previous_session_close(bars, now_et)
    if not previous or previous <= 0 or last <= 0:
        return None
    return round((last / previous - 1.0) * 100.0, 2)


def window_fires(bars: object, start: int, end: int) -> list[dict]:
    """Fires the chart is showing this premarket, keyed on bucket CLOSE.

    1h/2h/4h qualify when their bucket closes inside the window. Daily is a
    special case: a daily candle does not close until midnight, so a fire-D
    visible during premarket is ALWAYS the previous session's release. The
    chart draws it, so it counts -- carrying its own date, so it is never
    mistaken for a fresh premarket event. Excluding it would have made the
    user's explicit "1hr to D" request silently impossible.
    """
    source = [bar for bar in (bars or []) if isinstance(bar, dict)]
    try:
        last_source_time = int(source[-1]["time"]) if source else 0
    except (KeyError, TypeError, ValueError):
        last_source_time = 0

    fires: list[dict] = []
    for minutes, label in FIRE_TIMEFRAMES:
        events = squeeze_release_events(source, minutes)
        if not events:
            continue
        if minutes == 1440:
            # Only the MOST RECENTLY CLOSED daily candle counts. Taking the
            # newest release anywhere in the tape surfaced a July release in
            # an August scan -- a flame the chart would not draw today, so it
            # broke the parity this whole design rests on.
            daily = aggregate_chart_bars(source, 1440)
            closed = [
                bar for bar in daily
                if int(bar["time"]) + 1440 * 60 <= last_source_time
            ]
            if not closed:
                continue
            newest_closed = int(closed[-1]["time"])
            latest = events[-1]
            if latest["bucketTime"] == newest_closed and latest["closeTime"] <= end:
                fires.append({**latest, "label": label})
            continue
        fires.extend(
            {**event, "label": label}
            for event in events
            if start <= event["closeTime"] <= end
        )
    return fires


_TIER_RANK = {"WEAK": 0, "MODERATE": 1, "STRONG": 2}


def score_strength(calls: object, fires: object, higher_cyan: object = (),
                   higher_yellow: object = (), higher_macd: object = ()) -> tuple[int, str]:
    """Rank a setup by the QUALITY of its crosses, not just the count.

    Cyan (9x20) outranks yellow (4x8), for two reasons that are facts about
    this codebase rather than opinion. The ThinkScript port in scanner.py
    checks the 9x20 cross before the 4x8 cross in its background priority
    ladder, and ``_group_mtf_call_signals`` sorts cyan above yellow. Cyan is
    also 2.4x rarer in the real tapes (33 yellow vs 14 cyan CALL signals
    across the nine stored charts), which is what a slower, more-confirmed
    cross should look like.

    Colour tier (the trader's rule, 2026-08-21, from a COIN chart carrying a
    yellow CALL4H beside a cyan CALL2H that he called strong):
      * cyan + yellow together, or two cyan          -> STRONG
      * any single cyan, or two yellow               -> MODERATE
      * one yellow alone                             -> WEAK

    Fire-only setups are capped at MODERATE and never reach STRONG. A squeeze
    release is volatility EXPANDING, not direction CONFIRMED -- with no EMA
    cross behind it, nothing says which way it breaks, so STRONG is reserved
    for setups carrying an actual cross. Refined by the trader across two
    charts on 2026-08-21:
      * AAPL, CALL5 + one 1h fire   -> "weak because only fire signals"
      * WRBY, 1h + 2h + 4h fires    -> "more than 1 fire only" = MODERATE
    So one fire alone is WEAK; two or more, with no cross, is MODERATE.

    Once a CALL2H/CALL4H exists, fires do add: two or more of them lift the
    colour tier one step (never past STRONG).

    ``score`` stays a plain count of every hit, so the table can still sort
    strongest-first and show its work.
    """
    call_list = [call for call in (calls or []) if isinstance(call, dict)]
    fire_list = list(fires or [])
    higher_cyan_list = [signal for signal in (higher_cyan or []) if isinstance(signal, dict)]
    higher_yellow_list = [signal for signal in (higher_yellow or []) if isinstance(signal, dict)]
    higher_macd_list = [signal for signal in (higher_macd or []) if isinstance(signal, dict)]
    higher_list = [*higher_cyan_list, *higher_yellow_list]
    # D-to-M crosses join the colour tier as their own colour: a cyan
    # CALLD..CALLM counts as cyan ("if you see any cyan it will be
    # stronger" - the trader's rule, and daily-and-up is the slowest,
    # rarest cross of all), a yellow 4/8 D-M counts as yellow. So one
    # CALLD alone is MODERATE and upgrades to STRONG in combination,
    # exactly like an intraday cyan. A bullish MACD D-M is confirmation,
    # not a cross: it adds to the score so the row sorts higher, but it
    # never changes the colour tier by itself.
    cyan = sum(1 for call in call_list if str(call.get("family") or "") == "9x20")
    cyan += len(higher_cyan_list)
    yellow = sum(1 for call in call_list if str(call.get("family") or "") == "4x8")
    yellow += len(higher_yellow_list)

    if cyan >= 2 or (cyan and yellow):
        colour_tier = "STRONG"
    elif cyan or yellow >= 2:
        colour_tier = "MODERATE"
    else:
        colour_tier = "WEAK"

    tier = colour_tier
    if not call_list and not higher_list:
        # No cross behind them: capped at MODERATE, never STRONG. A MACD-only
        # row lands here too - confirmation without a cross is fire-like.
        tier = "MODERATE" if (len(fire_list) + len(higher_macd_list)) >= 2 else "WEAK"
    elif len(fire_list) >= 2 and _TIER_RANK[tier] < _TIER_RANK["STRONG"]:
        tier = "STRONG" if tier == "MODERATE" else "MODERATE"
    return len(call_list) + len(fire_list) + len(higher_list) + len(higher_macd_list), tier


def merge_live_tail(cached_bars: object, live_bars: object) -> list[dict]:
    """Cached tape plus only the STRICTLY NEWER live streamed bars.

    Appending rather than merging by timestamp is deliberate. ``studyBars``
    ships at a thirty-minute cadence; a one-minute live bar landing on the
    same timestamp as a thirty-minute cached bar would replace it and throw
    away that bucket's true high and low. Anything at or before the cached
    tape's last bar is therefore ignored.

    This is what keeps fires from lagging the chart by a cache refresh: the
    browser computes them off the streamed tape, so the server must too.
    """
    cached = [bar for bar in (cached_bars or []) if isinstance(bar, dict)]
    live = [bar for bar in (live_bars or []) if isinstance(bar, dict)]
    if not live:
        return cached

    def bar_time(bar: dict) -> int:
        try:
            return int(bar.get("time") or 0)
        except (TypeError, ValueError):
            return 0

    cutoff = max((bar_time(bar) for bar in cached), default=0)
    tail = sorted((bar for bar in live if bar_time(bar) > cutoff), key=bar_time)
    return [*cached, *tail]


_SIGNAL_BUCKET_SECONDS = {"CALL2H": 120 * 60, "CALL4H": 240 * 60}


def _any_signal_still_forming(calls: object, bars: object) -> bool:
    """True while any counted signal's secondary bucket has not closed yet.

    ``signal.time`` is the bucket's opening five-minute candle (the engine
    back-projects each cross there), so the bucket locks at open + span.
    """
    try:
        last_bar_time = int((list(bars or [])[-1] or {}).get("time") or 0)
    except (TypeError, ValueError, IndexError):
        return False
    if last_bar_time <= 0:
        return False
    for signal in calls or []:
        span = _SIGNAL_BUCKET_SECONDS.get(str(signal.get("label") or "").upper())
        if not span:
            continue
        try:
            opened = int(signal.get("time") or 0)
        except (TypeError, ValueError):
            continue
        if opened > 0 and opened + span > last_bar_time:
            return True
    return False


def premarket_scan_row(
    symbol: str,
    payload: object,
    now_et: datetime,
    *,
    fires: object = None,
) -> dict | None:
    """One scanner row, or None when the symbol has nothing (or is cold)."""
    if not isinstance(payload, dict):
        return None
    bars = payload.get("bars")
    if not bars:
        return None

    start, end = premarket_window(now_et)
    # CALL2H/CALL4H come from the TOS scan script itself (tos_bull_momo_signals),
    # not from the engine's current-bar markers: the trader wants the row set
    # to match his TOS "AlertX Bull Momo" scan, which looks back 12 bars.
    try:
        last_price_raw = float(bars[-1].get("close") or 0)
    except (AttributeError, TypeError, ValueError):
        last_price_raw = 0.0
    if last_price_raw < TOS_SCAN_MIN_LAST:
        return None
    if (
        str(payload.get("mtfSignalMode") or "") == CHART_ENGINE_MODE
        and not payload.get("mtfSignalsPending")
        and isinstance(payload.get("mtfSignals"), list)
    ):
        calls = tos_scan_from_chart_signals(payload.get("mtfSignals"), bars, payload.get("studyBars"))
    else:
        calls = tos_bull_momo_signals(bars, payload.get("studyBars"))
    # The premarket scanner is a TODAY view. The TOS scan's 12-bar lookback
    # reaches back ~24h on the 2h aggregation, and the window check only tests
    # time-of-day (07:00-09:30 on 2h), so a cross from YESTERDAY'S premarket still
    # qualifies this morning (META/AMZN/AAPL showed 8/24 rows at 8 AM on 8/25).
    # Those belong in Scanner History - drop any cross before today's session
    # start (the same midnight-ET floor the D-M cyan scan uses below).
    session_start = int(
        now_et.astimezone(EASTERN).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    calls = [signal for signal in calls if int(signal.get("time") or 0) >= session_start]
    matched_fires = list(fires) if fires is not None else window_fires(
        payload.get("studyBars") or bars, start, end
    )
    # Cyan D-to-M crosses scan a WIDER window (midnight-09:30): a CALLD that
    # prints at 01:00 ET is on the chart all night, so it must be on the
    # scanner all night too. Same payload array the chart bubbles draw from.
    overnight_start, overnight_end = overnight_window(now_et)
    ganesh_signals = (
        (payload.get("ganeshHigherTimeframeSignals") or {}).get("signals")
        if isinstance(payload.get("ganeshHigherTimeframeSignals"), dict) else None
    )
    # CYAN ONLY for the D-M scan (trader's rule 2026-08-24: "I need only
    # cyan (CALL), no need MACD" - the yellow/MACD families cluttered the
    # column and diluted the tier). window_higher_signals keeps supporting
    # the other families for any future consumer; the scanner just no
    # longer asks for them.
    cyan_higher = window_higher_signals(ganesh_signals, overnight_start, overnight_end, CYAN_HIGHER_FAMILY)
    # A row exists when the TOS scan matches (2h/4h cross) or a cyan D-M cross
    # is live. Squeeze fires stay on the row (column + strength) but no longer
    # create one on their own: TOS has no such row, and the trader wants the
    # same result set.
    if not calls and not cyan_higher:
        return None

    score, strength = score_strength(calls, matched_fires, higher_cyan=cyan_higher)
    times = [int(signal["time"]) for signal in calls]
    times += [int(fire["closeTime"]) for fire in matched_fires]
    times += [int(signal["time"]) for signal in cyan_higher]
    first = min(times) if times else None
    # The table's Date/Time shows the NEWEST signal (trader's rule,
    # 2026-08-23: one row per ticker, "latest timestamp"); firstSeenAt in
    # the history archive still answers "when did it first come".
    latest = max(times) if times else None

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
        "latestSignalAt": datetime.fromtimestamp(latest, tz=EASTERN).isoformat() if latest else None,
        "lastPrice": last_price,
        # TOS sorts the scan by %Change descending; same number, same order.
        # The prior session's close comes from the deep study tape when the
        # live tape is a single session.
        "changePct": change_percent(
            sorted(list(payload.get("studyBars") or []) + list(bars), key=lambda bar: int(bar.get("time") or 0)),
            now_et,
        ),
        "signals48": labels("4x8"),
        "signals920": labels("9x20"),
        "signalsCyanHigher": _dedup_labels(cyan_higher),
        # Kept as empty lists so older frontends and archived history rows
        # keep their shape; the scanner is cyan-only as of 2026-08-24.
        "signalsYellowHigher": [],
        "signalsMacdHigher": [],
        # One label -> ET print time map for the D-M events, so the table's
        # hover (and the history) can answer "when did it come".
        "higherSignalTimes": _higher_signal_times(cyan_higher),
        "fires": [fire["label"] for fire in matched_fires],
        "fireDates": {
            fire["label"]: datetime.fromtimestamp(fire["closeTime"], tz=EASTERN).isoformat()
            for fire in matched_fires
        },
        "score": score,
        "strength": strength,
        # Derived from bucket close vs the tape's last bar -- NEVER from the
        # engine's liveForming flag, which is hardcoded True on every signal
        # (scanner.py:399). A CALL2H sits on a 2h bucket that locks two hours
        # after its open; CALL4H locks four hours after. Until the tape
        # reaches that close the cross can still repaint away: META dropped
        # from STRONG to nothing at 08:00 on 2026-08-21 while the user was
        # comparing the row to his chart. FORMING tells him which rows can
        # still do that.
        "forming": _any_signal_still_forming(calls, bars),
    }
