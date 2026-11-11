"""Extended-hours bad prints - the port of frontend/src/chartBadPrints.js.

Schwab's minute candles carry the occasional off-market trade print outside the
regular session. On 2026-11-11 SPY's 16:38 and 16:55 ET minutes opened at
$711.46 while SPY traded at ~$760, so the 16:55 five-minute candle drew a body
from $711 to $760. TradingView and TOS drop those prints.

A price is a bad print only when ALL of these hold:
  - the minute is outside 09:30-16:00 ET;
  - it is at least 1% (and $0.05) from the median close of the ten minutes on
    each side, or further when the ticker is jumpy;
  - nothing traded near that price in the five minutes on either side.
On the newest minutes (fewer than five after it) only a spike that came back
inside its own minute is removed, so a real move is never hidden while it is
happening. Keep this file and the JS one identical in behaviour.
"""

from __future__ import annotations

import statistics
from datetime import datetime
from zoneinfo import ZoneInfo

NEIGHBOUR_WINDOW = 10
CONFIRM_WINDOW = 5
MIN_FRACTION = 0.01
MIN_DOLLARS = 0.05
JUMPINESS_MULTIPLE = 25
FIELDS = ("open", "high", "low", "close")
_EASTERN = ZoneInfo("America/New_York")
_REGULAR_OPEN_MINUTE = 9 * 60 + 30
_REGULAR_CLOSE_MINUTE = 16 * 60
_hour_cache: dict[int, int] = {}


def _eastern_minute_of_day(epoch_seconds: int) -> int:
    utc_hour = int(epoch_seconds) // 3600
    hour = _hour_cache.get(utc_hour)
    if hour is None:
        hour = datetime.fromtimestamp(utc_hour * 3600, _EASTERN).hour
        if len(_hour_cache) > 20000:
            _hour_cache.clear()
        _hour_cache[utc_hour] = hour
    return hour * 60 + (int(epoch_seconds) % 3600) // 60


def is_extended_hours_time(epoch_seconds) -> bool:
    minute = _eastern_minute_of_day(int(epoch_seconds))
    return minute < _REGULAR_OPEN_MINUTE or minute >= _REGULAR_CLOSE_MINUTE


def _is_candidate(bars, index: int) -> bool:
    neighbour = bars[index - 1] if index > 0 else (bars[index + 1] if index + 1 < len(bars) else None)
    anchor = float(neighbour["close"]) if neighbour else 0.0
    if not anchor > 0:
        return False
    bar = bars[index]
    limit = max(anchor * MIN_FRACTION, MIN_DOLLARS)
    return any(abs(float(bar[field]) - anchor) > limit for field in FIELDS)


def _bad_fields_at(bars, index: int):
    count = len(bars)
    start = max(0, index - NEIGHBOUR_WINDOW)
    stop = min(count, index + NEIGHBOUR_WINDOW + 1)
    closes = [float(bars[cursor]["close"]) for cursor in range(start, stop) if cursor != index]
    if len(closes) < 4:
        return None
    reference = statistics.median(closes)
    if not reference > 0:
        return None
    moves = [
        abs(float(bars[cursor]["close"]) - float(bars[cursor - 1]["close"])) / reference
        for cursor in range(max(start, 1), stop)
        if cursor != index and cursor - 1 != index
    ]
    jumpiness = statistics.median(moves) if moves else 0.0
    tolerance = max(reference * max(MIN_FRACTION, JUMPINESS_MULTIPLE * jumpiness), MIN_DOLLARS)
    nearby = [
        float(bars[cursor][field])
        for cursor in range(max(0, index - CONFIRM_WINDOW), min(count, index + CONFIRM_WINDOW + 1))
        if cursor != index
        for field in FIELDS
    ]
    bar = bars[index]
    bad = [
        field for field in FIELDS
        if abs(float(bar[field]) - reference) > tolerance
        and not any(abs(other - float(bar[field])) <= tolerance / 2 for other in nearby)
    ]
    if not bad:
        return None
    has_followers = count - 1 - index >= CONFIRM_WINDOW
    if not has_followers and "close" in bad:
        return None
    return bad, reference


def _repaired_bar(bars, index: int, bad, reference: float) -> dict:
    bar = bars[index]
    previous_close = float(bars[index - 1]["close"]) if index > 0 else 0.0
    bad_set = set(bad)
    if "open" in bad_set:
        open_ = previous_close if previous_close > 0 and "close" not in bad_set else reference
    else:
        open_ = float(bar["open"])
    close = open_ if "close" in bad_set else float(bar["close"])
    high = max(open_, close) if "high" in bad_set else float(bar["high"])
    low = min(open_, close) if "low" in bad_set else float(bar["low"])
    return {
        **bar,
        "open": open_,
        "high": max(high, open_, close),
        "low": min(low, open_, close),
        "close": close,
        "badPrintRemoved": True,
    }


def remove_extended_hours_bad_prints(bars):
    """Return the bars with off-market extended-hours prints repaired.

    Returns the SAME list when nothing was removed.
    """
    if not isinstance(bars, list) or len(bars) < 5:
        return bars
    repaired = None
    for index in range(len(bars)):
        if not _is_candidate(bars, index):
            continue
        if not is_extended_hours_time(bars[index]["time"]):
            continue
        verdict = _bad_fields_at(bars, index)
        if not verdict:
            continue
        if repaired is None:
            repaired = list(bars)
        repaired[index] = _repaired_bar(bars, index, *verdict)
    return repaired if repaired is not None else bars
