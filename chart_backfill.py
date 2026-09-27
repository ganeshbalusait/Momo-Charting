"""Backfill the current day's 04:00-07:00 ET candles from Tradier.

Why this module exists (verified live, 2026-08-21): Schwab's price history
starts the CURRENT day at 07:00 ET -- an explicit request for 04:00-07:00
returned bars from 07:00 -- and its CHART_EQUITY stream is equally silent
before 07:00. Alpaca IEX has essentially no trades that early. Tradier
timesales (the same token the option chains already use) returned all 175
missing TSLA minutes. Without this, every chart shows "yesterday 19:55 then
today 07:00", and the scanner's 06:00-07:00 hour scans data that does not
exist.

Past days are untouched: once a day completes its bars accumulate in the
persisted tapes as before. Only the current day's early-premarket hole is
patched.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

EASTERN = ZoneInfo("America/New_York")

# Schwab's current-day history begins here; everything earlier needs Tradier.
_SCHWAB_DAY_START_HOUR = 7
_PREMARKET_OPEN_HOUR = 4


def today_premarket_hole(
    frame: pd.DataFrame, now_et: datetime
) -> tuple[datetime, datetime] | None:
    """The [start, end) window of today's missing early-premarket bars.

    None when there is nothing to patch: weekend, before 04:10 (nothing to
    fetch yet), or the tape already carries pre-07:00 bars for today (the
    hole only ever exists at the front of the current day).

    The patch stays on for the rest of the calendar day: a backend restart
    after 20:00 rebuilds every tape from Schwab, whose current day starts at
    07:00, and without the patch the evening charts and the TOS MTF labels
    lost today's 04:00-07:00 candles (META 2026-08-24 20:40 ET).
    """
    now = now_et.astimezone(EASTERN)
    minute_of_day = now.hour * 60 + now.minute
    if now.weekday() >= 5:
        return None
    if minute_of_day < _PREMARKET_OPEN_HOUR * 60 + 10:
        return None

    start = now.replace(hour=_PREMARKET_OPEN_HOUR, minute=0, second=0, microsecond=0)
    seven = now.replace(hour=_SCHWAB_DAY_START_HOUR, minute=0, second=0, microsecond=0)
    end = min(now, seven)
    if end <= start:
        return None

    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return start, end

    stamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is None:
        stamps = stamps.dt.tz_localize(EASTERN, nonexistent="shift_forward", ambiguous="NaT")
    else:
        stamps = stamps.dt.tz_convert(EASTERN)
    todays = stamps[stamps.dt.date == now.date()].dropna()
    # Ask whether the 04:00-07:00 WINDOW is covered, not whether the day has
    # any bar before 07:00.
    #
    # The old test was `todays.min() < seven` - "early bars already present" -
    # and it was correct while Schwab was the only source, because then the day
    # genuinely began at 07:00 and the hole could only ever be at the front.
    # The Alpaca BOATS overnight backfill falsified that: it fills 20:00-04:00,
    # so today's tape now starts around 00:03 and satisfies `min() < seven`
    # while 04:00-07:00 is still completely empty. The patch has been skipping
    # itself ever since - measured 2026-08-27 on AMD: 461 bars today spanning
    # 00:03 to 13:58, zero of them inside 04:00-07:00, and the detector
    # returned None.
    #
    # Presence INSIDE the window, which is the original rule scoped correctly.
    # Deliberately not a coverage threshold: premarket is genuinely thin for
    # most names, so "enough bars" has no honest value, and refetching a
    # sparse-but-real window every build would spend requests to learn nothing.
    # Any bar in 04:00-07:00 means the patch has data here; none means it does
    # not.
    in_window = todays[(todays >= start) & (todays < end)]
    if not in_window.empty:
        return None  # bars already present IN THE WINDOW; nothing to do
    return start, end


def timesales_to_frame(series: object) -> pd.DataFrame:
    """Tradier timesales rows -> the chart's frame shape.

    Tradier stamps rows in Eastern wall-clock time with no offset; localize
    rather than convert, or every candle lands four hours off.
    """
    rows: list[dict] = []
    for item in series if isinstance(series, list) else []:
        if not isinstance(item, dict):
            continue
        stamp = pd.to_datetime(item.get("time"), errors="coerce")
        if pd.isna(stamp):
            continue
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(EASTERN)
        else:
            stamp = stamp.tz_convert(EASTERN)
        try:
            rows.append(
                {
                    "timestamp": stamp,
                    "open": float(item["open"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "close": float(item["close"]),
                    "volume": float(item.get("volume") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return pd.DataFrame(rows).sort_values("timestamp").reset_index(drop=True)


def merge_backfill(frame: pd.DataFrame, backfill: pd.DataFrame) -> pd.DataFrame:
    """Union by timestamp; on collision the broker (Schwab) row wins.

    Tradier is the patch, never the authority: where both sources have a
    minute, keep Schwab's so the tape stays consistent with every other
    Schwab-derived value on the chart.
    """
    if backfill is None or backfill.empty:
        return frame
    if frame is None or frame.empty:
        return backfill.reset_index(drop=True)
    merged = pd.concat([frame, backfill], ignore_index=True)
    merged = merged.drop_duplicates(subset=["timestamp"], keep="first")
    return merged.sort_values("timestamp").reset_index(drop=True)


# ---------------------------------------------------------------------------
# Overnight session (Blue Ocean ATS, 20:00 -> 04:00 ET, Sunday night through
# Friday morning). thinkorswim draws it ("EXTO") and computes its MTF EMA
# labels from it; the app's brokers never carried it. Alpaca's ``boats`` feed
# does (see data/alpaca_overnight.py). These helpers say WHICH nights a tape
# is missing so the patch fetches only what is needed.
# ---------------------------------------------------------------------------
_OVERNIGHT_OPEN_HOUR = 20


def overnight_windows(now_et: datetime, nights: int = 6) -> list[tuple[datetime, datetime]]:
    """[start, end) windows of the last ``nights`` overnight sessions, newest first.

    A session belongs to the weekday it opens INTO: Sunday 20:00 -> Monday
    04:00 is Monday's night. After 20:00 the running session (tonight ->
    tomorrow 04:00) is included, clipped to ``now``.
    """
    now = now_et.astimezone(EASTERN)
    day = now.date()
    if now.hour >= _OVERNIGHT_OPEN_HOUR:
        day = day + timedelta(days=1)
    windows: list[tuple[datetime, datetime]] = []
    guard = 0
    while len(windows) < max(0, int(nights)) and guard < 40:
        guard += 1
        if day.weekday() < 5:
            end = datetime(day.year, day.month, day.day, _PREMARKET_OPEN_HOUR, 0, tzinfo=EASTERN)
            start = end - timedelta(hours=24 - _OVERNIGHT_OPEN_HOUR + _PREMARKET_OPEN_HOUR)
            end = min(end, now)
            if end > start:
                windows.append((start, end))
        day = day - timedelta(days=1)
    return windows


def overnight_gaps(
    frame: pd.DataFrame, now_et: datetime, nights: int = 6, minimum_rows: int = 3
) -> list[tuple[datetime, datetime]]:
    """Overnight windows the tape does not cover (fewer than ``minimum_rows`` bars)."""
    windows = overnight_windows(now_et, nights)
    if not windows:
        return []
    if frame is None or frame.empty or "timestamp" not in frame.columns:
        return windows
    stamps = pd.to_datetime(frame["timestamp"], errors="coerce")
    if getattr(stamps.dt, "tz", None) is None:
        stamps = stamps.dt.tz_localize(EASTERN, nonexistent="shift_forward", ambiguous="NaT")
    else:
        stamps = stamps.dt.tz_convert(EASTERN)
    stamps = stamps.dropna()
    gaps = []
    for start, end in windows:
        inside = int(((stamps >= start) & (stamps < end)).sum())
        if inside < minimum_rows:
            gaps.append((start, end))
    return gaps


def keep_inside_windows(frame: pd.DataFrame, windows: list[tuple[datetime, datetime]]) -> pd.DataFrame:
    """Rows of ``frame`` whose timestamp falls in any [start, end) window."""
    if frame is None or frame.empty or not windows:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    stamps = pd.to_datetime(frame["timestamp"], errors="coerce", utc=True).dt.tz_convert(EASTERN)
    mask = pd.Series(False, index=frame.index)
    for start, end in windows:
        mask |= (stamps >= start) & (stamps < end)
    return frame[mask].reset_index(drop=True)
