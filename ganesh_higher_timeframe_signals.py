"""Backend replay of the supplied Ganesh higher-timeframe ThinkScript studies.

The browser displays a TOS equity chart whose primary four-hour candles are
anchored to midnight Central (01:00/05:00/09:00/13:00/... Eastern).  The three
studies in this module consume those primary candles while their secondary
prices use regular-session DAY-or-higher closes:

* EMA 4x8 on D/2D/3D/4D/W/M, confirmed by the next timeframe.
* EMA 9x20 on the first primary candle of each Eastern trading day.
* MACD(6, 12, 8) crosses on D/2D/3D/4D/W/M, including the provisional
  extended-hours candles used by the supplied ThinkScript.

Signal calculation intentionally remains independent of browser visibility and
color preferences.  The API can cache one authoritative source-event tape and
every chart panel can project/filter that tape for its selected timeframe.
"""

from __future__ import annotations

from collections import OrderedDict
from datetime import date, datetime, timezone
from functools import lru_cache
import math
import re
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

import pandas as pd

from config import EASTERN_TZ


SCHEMA_VERSION = "ganesh-higher-timeframe-signals-v17"
SIGNAL_MODE = "tos_calendar_and_session_secondary_replay"
SOURCE_AGGREGATION_MINUTES = 240
# The supplied 9x20 chart study runs on TOS's 15-day / 4-hour view.  Its
# ExpAverage state is consequently seeded from the compact study window, not
# from the years of daily bars used by the unrelated levels studies.
_TOS_920_DAILY_WINDOW = 25

GANESH_HIGHER_TIMEFRAMES = (
    {"key": "D", "days": 1, "atrMultiplier": 0.2},
    {"key": "2D", "days": 2, "atrMultiplier": 0.6},
    {"key": "3D", "days": 3, "atrMultiplier": 1.0},
    {"key": "4D", "days": 4, "atrMultiplier": 1.4},
    {"key": "W", "days": 0, "atrMultiplier": 1.8},
    {"key": "M", "days": 0, "atrMultiplier": 2.2},
)
GANESH_MACD_TIMEFRAMES = GANESH_HIGHER_TIMEFRAMES

_NEXT_CONFIRMATION_TIMEFRAME = {
    "D": "2D",
    "2D": "3D",
    "3D": "4D",
    "4D": "W",
    "W": "M",
}
_TIMEFRAME_RANK = {
    definition["key"]: index for index, definition in enumerate(GANESH_HIGHER_TIMEFRAMES)
}
_FAMILY_RANK = {"ganesh48": 0, "ganesh920": 1, "ganeshMacd": 2}
_FAMILY_PREFETCH_BUCKETS = {"ganesh48": 32, "ganesh920": 80, "ganeshMacd": 48}
_DATE_KEY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_EASTERN_ZONE = ZoneInfo(EASTERN_TZ)


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _timestamp(value: Any) -> pd.Timestamp | None:
    try:
        stamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if pd.isna(stamp):
        return None
    if stamp.tzinfo is None:
        return stamp.tz_localize(EASTERN_TZ, nonexistent="shift_forward", ambiguous="NaT")
    return stamp.tz_convert(EASTERN_TZ)


def _epoch_seconds(value: Any) -> int | None:
    stamp = _timestamp(value)
    if stamp is None or pd.isna(stamp):
        return None
    result = math.floor(stamp.timestamp())
    return result if result > 0 else None


def _supplied_date_key(row: Mapping[str, Any]) -> str:
    for key in ("date", "_date"):
        raw = row.get(key)
        if isinstance(raw, (date, datetime, pd.Timestamp)):
            candidate = raw.strftime("%Y-%m-%d")
        else:
            candidate = str(raw or "")
        if _DATE_KEY_PATTERN.fullmatch(candidate):
            return candidate
    return ""


def _date_key_from_timestamp(timestamp: Any) -> str:
    if isinstance(timestamp, (int, float)) and not isinstance(timestamp, bool):
        number = _finite(timestamp)
        if number is None or number <= 0:
            return ""
        stamp = datetime.fromtimestamp(number, tz=timezone.utc).astimezone(_EASTERN_ZONE)
    else:
        stamp = _timestamp(timestamp)
    return "" if stamp is None or pd.isna(stamp) else stamp.strftime("%Y-%m-%d")


def _date_key_for_bar(bar: Mapping[str, Any]) -> str:
    supplied = str(bar.get("date") or "")
    return supplied if _DATE_KEY_PATTERN.fullmatch(supplied) else _date_key_from_timestamp(bar.get("time"))


def _trading_date_key_for_bar(bar: Mapping[str, Any]) -> str:
    """Return the TOS trading date for an overnight primary candle.

    TOS displays the Sunday EXTO candle at 21:00 Sunday, but DAY-or-higher
    studies treat it as the first candle of Monday's trading session.  Other
    weeknights retain their calendar date until midnight; that is the latch
    timing visible in the supplied AMZN reference (Friday bubbles at 01:00 and
    05:00, not Thursday 21:00).
    """
    timestamp = _finite(bar.get("time"))
    if timestamp is None or timestamp <= 0:
        return _date_key_for_bar(bar)
    eastern = datetime.fromtimestamp(timestamp, tz=timezone.utc).astimezone(_EASTERN_ZONE)
    # The canonical 4H series is anchored at 01:00 ET, so Blue Ocean trades
    # from 20:00-20:59 Sunday live inside the Sunday 17:00 bucket. Even though
    # its plotted bucket timestamp precedes the venue open, TOS treats that
    # entire bucket as the first candle of Monday's trading session.
    if eastern.weekday() == 6 and eastern.hour >= 17:
        return date.fromordinal(eastern.date().toordinal() + 1).isoformat()
    return eastern.date().isoformat()


def _eastern_minute_from_timestamp(timestamp: Any) -> int | None:
    number = _finite(timestamp)
    if number is None or number <= 0:
        return None
    stamp = datetime.fromtimestamp(number, tz=timezone.utc).astimezone(_EASTERN_ZONE)
    return stamp.hour * 60 + stamp.minute


def _records(rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    if isinstance(rows, pd.DataFrame):
        return rows.to_dict("records")
    if rows is None:
        return []
    return [row for row in rows if isinstance(row, Mapping)]


def _normalize_bars(
    rows: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Match the frontend's timestamp normalization and last-row deduplication."""
    by_time: dict[int, dict[str, Any]] = {}
    for source in _records(rows):
        raw_time = source.get("time") if source.get("time") is not None else source.get("timestamp")
        if isinstance(raw_time, (int, float)) and not isinstance(raw_time, bool):
            numeric_time = _finite(raw_time)
            time = math.floor(numeric_time) if numeric_time is not None else None
        else:
            time = _epoch_seconds(raw_time)
        close = _finite(source.get("close"))
        if time is None or time <= 0 or close is None:
            continue
        bar: dict[str, Any] = {"time": time, "close": close}
        for key in ("open", "high", "low", "volume"):
            value = _finite(source.get(key))
            if value is not None:
                bar[key] = value
        supplied_date = _supplied_date_key(source)
        if supplied_date:
            bar["date"] = supplied_date
        by_time[time] = bar
    return sorted(by_time.values(), key=lambda bar: int(bar["time"]))


def _tos_four_hour_bucket_time(timestamp: int) -> int:
    """Return the TOS primary 4H bucket anchored to midnight Central."""
    eastern = datetime.fromtimestamp(int(timestamp), tz=timezone.utc).astimezone(_EASTERN_ZONE)
    eastern_midnight = datetime(eastern.year, eastern.month, eastern.day, tzinfo=_EASTERN_ZONE)
    # Add elapsed seconds, as the browser does, rather than calendar arithmetic.
    anchor_time = math.floor(eastern_midnight.timestamp()) + 60 * 60
    bucket_seconds = SOURCE_AGGREGATION_MINUTES * 60
    return anchor_time + ((int(timestamp) - anchor_time) // bucket_seconds) * bucket_seconds


def _aggregate_four_hour_bars(bars: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    buckets: OrderedDict[int, dict[str, Any]] = OrderedDict()
    for source in bars:
        timestamp = int(source["time"])
        bucket_time = _tos_four_hour_bucket_time(timestamp)
        close = float(source["close"])
        open_value = _finite(source.get("open"))
        high = _finite(source.get("high"))
        low = _finite(source.get("low"))
        volume = _finite(source.get("volume")) or 0.0
        open_value = close if open_value is None else open_value
        high = close if high is None else high
        low = close if low is None else low
        current = buckets.get(bucket_time)
        if current is None:
            buckets[bucket_time] = {
                "time": bucket_time,
                "open": open_value,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            }
            continue
        current["high"] = max(float(current["high"]), high)
        current["low"] = min(float(current["low"]), low)
        current["close"] = close
        current["volume"] = float(current.get("volume") or 0.0) + volume
    return list(buckets.values())


def build_ganesh_primary_bars(
    study_frame: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    live_frame: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge the cached 5m seed and live 1m tail, then build canonical 4H bars."""
    historical = _normalize_bars(study_frame)
    live = _normalize_bars(live_frame)
    live_start = int(live[0]["time"]) if live else 0
    source = (
        [bar for bar in historical if int(bar["time"]) < live_start] + live
        if live_start > 0
        else historical
    )
    return _aggregate_four_hour_bars(source)


def _is_weekday(date_key: str) -> bool:
    try:
        return date.fromisoformat(date_key).weekday() < 5
    except ValueError:
        return False


def _higher_timeframe_source_bar(
    bar: Mapping[str, Any],
    daily_close_by_date: Mapping[str, float],
    aggregation_minutes: int,
) -> dict[str, Any] | None:
    minutes = max(1, int(aggregation_minutes or 1))
    date_key = _date_key_for_bar(bar)
    close = _finite(bar.get("close"))
    if not date_key or close is None:
        return None
    if minutes >= 1_440:
        return {**bar, "date": date_key, "sourceClose": close}
    if not _is_weekday(date_key):
        return None
    start_minute = _eastern_minute_from_timestamp(bar.get("time"))
    if start_minute is None:
        return None
    end_minute = start_minute + minutes
    rth_start = 9 * 60 + 30
    rth_end = 16 * 60
    if end_minute <= rth_start or start_minute >= rth_end:
        return None
    settled_close = _finite(daily_close_by_date.get(date_key))
    source_close = settled_close if end_minute >= rth_end and settled_close is not None else close
    return {**bar, "date": date_key, "sourceClose": source_close}


@lru_cache(maxsize=4096)
def _week_key(date_key: str) -> str:
    try:
        parsed = date.fromisoformat(date_key)
    except ValueError:
        return ""
    return date.fromordinal(parsed.toordinal() - parsed.weekday()).isoformat()


@lru_cache(maxsize=4096)
def _calendar_day_ordinal(date_key: str) -> int | None:
    try:
        parsed = date.fromisoformat(date_key)
    except ValueError:
        return None
    return (parsed - date(1970, 1, 1)).days


def _timeframe_group_key(timeframe: str, date_key: str) -> str:
    if timeframe == "D":
        return date_key
    if timeframe == "W":
        return f"W-{_week_key(date_key)}"
    if timeframe == "M":
        return f"M-{date_key[:7]}"
    try:
        days = int(timeframe.rstrip("D"))
    except ValueError:
        return ""
    ordinal = _calendar_day_ordinal(date_key)
    # Thinkorswim's fixed-duration equity aggregations use a 1969-12-30 phase
    # (two days before the Unix epoch), not Unix day zero. That two-day phase
    # is visible in the supplied weekend reference: 3D remains in Friday's
    # group on Sunday, 2D rolls on Monday, and 4D does not emit an extra Sunday
    # CALL bubble. Using the Unix phase shifts all three live signal candles.
    return (
        ""
        if ordinal is None
        else f"{timeframe}-{math.floor((ordinal + 2) / max(1, days))}"
    )


def _macd_timeframe_group_key(timeframe: str, date_key: str) -> str:
    """Return the TOS MACD study's literal higher-timeframe key.

    The supplied MACD script's THREE_DAYS series rolls one calendar day ahead
    of the matching EMA 3D series. Keeping that offset local to the MACD
    source preserves the already-matched 4x8/9x20 studies while placing the
    INTC MACD-3D crossover on its TOS candle. TWO_DAYS and the other MACD
    periods retain their existing source anchors.
    """
    if timeframe != "3D":
        return _timeframe_group_key(timeframe, date_key)
    ordinal = _calendar_day_ordinal(date_key)
    return "" if ordinal is None else f"3D-{math.floor((ordinal + 1) / 3)}"


def _tos_overnight_group_continues_from_prior_session(
    timeframe: str,
    calendar_date_key: str,
) -> bool:
    """Match TOS's Sunday EXTO visibility for DAY-or-higher studies.

    TOS assigns the Sunday 17:00/21:00 primary candles to Monday's trading
    session, but its DAY-or-higher secondary aggregation does not create a new
    calendar period on the non-trading Sunday. A signal may appear there only
    when that higher-timeframe period already existed on the prior weekday.
    This retains the supplied chart's Sunday CALL3D while suppressing the
    synthetic Sunday CALL2D/CALL4D bubbles caused by naïve calendar grouping.
    """
    try:
        current = date.fromisoformat(calendar_date_key)
    except ValueError:
        return False
    if current.weekday() < 5:
        return True
    previous = current
    while True:
        previous = date.fromordinal(previous.toordinal() - 1)
        if previous.weekday() < 5:
            break
    return _timeframe_group_key(
        timeframe,
        calendar_date_key,
    ) == _timeframe_group_key(timeframe, previous.isoformat())


def _next_ema(previous: Any, value: float, length: int) -> float:
    prior = _finite(previous)
    if prior is None:
        return value
    alpha = 2.0 / (max(1, int(length)) + 1.0)
    return value * alpha + prior * (1.0 - alpha)


def _create_source_state() -> dict[str, Any]:
    return {
        "groupKey": "",
        "committed": None,
        "provisional": None,
        "previous": None,
        "completedBuckets": 0,
    }


def _source_values(base: Mapping[str, Any] | None, close: float) -> dict[str, float]:
    base = base or {}
    ema4 = _next_ema(base.get("ema4"), close, 4)
    ema8 = _next_ema(base.get("ema8"), close, 8)
    ema9 = _next_ema(base.get("ema9"), close, 9)
    ema20 = _next_ema(base.get("ema20"), close, 20)
    ema6 = _next_ema(base.get("ema6"), close, 6)
    ema12 = _next_ema(base.get("ema12"), close, 12)
    macd_value = ema6 - ema12
    macd_average = _next_ema(base.get("macdAverage"), macd_value, 8)
    return {
        "ema4": ema4,
        "ema8": ema8,
        "ema9": ema9,
        "ema20": ema20,
        "ema6": ema6,
        "ema12": ema12,
        "macdValue": macd_value,
        "macdAverage": macd_average,
    }


def _advance_source_state(
    state: dict[str, Any],
    group_key: str,
    close: float,
) -> dict[str, Any]:
    if state["groupKey"] and state["groupKey"] != group_key and state["provisional"]:
        state["committed"] = state["provisional"]
        state["completedBuckets"] += 1
    if state["groupKey"] != group_key:
        state["groupKey"] = group_key
    base = state["committed"] or {}
    current = _source_values(base, close)
    previous = state["previous"]
    state["provisional"] = current
    state["previous"] = current
    return {
        "base": state["committed"],
        "previous": previous,
        "current": current,
        "completedBuckets": state["completedBuckets"],
    }


def _crossed_above(previous_fast: Any, previous_slow: Any, fast: Any, slow: Any) -> bool:
    values = [_finite(value) for value in (previous_fast, previous_slow, fast, slow)]
    return all(value is not None for value in values) and values[0] <= values[1] and values[2] > values[3]


def _crossed_below(previous_fast: Any, previous_slow: Any, fast: Any, slow: Any) -> bool:
    values = [_finite(value) for value in (previous_fast, previous_slow, fast, slow)]
    return all(value is not None for value in values) and values[0] >= values[1] and values[2] < values[3]


def _event_record(
    *,
    family: str,
    timeframe: str,
    direction: str,
    time: int,
    bar: Mapping[str, Any],
    atr_multiplier: float = 0.0,
) -> dict[str, Any]:
    label = f"MACD-{timeframe}" if family == "ganeshMacd" else f"{direction}{timeframe}"
    return {
        "key": f"{family}-{timeframe}-{direction}-{int(time)}",
        "family": family,
        "timeframe": timeframe,
        "direction": direction,
        "label": label,
        "time": int(time),
        "low": _finite(bar.get("low")),
        "high": _finite(bar.get("high")),
        "atrMultiplier": float(atr_multiplier),
        "stateSnapshot": False,
        "sourceEvent": True,
    }


def _source_ready(snapshot: Mapping[str, Any] | None, family: str) -> bool:
    completed = int((snapshot or {}).get("completedBuckets") or 0)
    return completed >= _FAMILY_PREFETCH_BUCKETS.get(family, 0)


def _stable_open_long_period_48_snapshot(
    snapshot: Mapping[str, Any],
    context: Mapping[str, Any],
    timeframe: str,
    *,
    incomplete_session: bool,
) -> dict[str, Any]:
    """Keep open 4D/W/M EMA bubbles on TOS's active secondary value.

    The supplied 4x8 study expands one DAY-or-higher secondary value across
    its containing 4H candles. Re-evaluating 4D/W/M from every primary close
    makes the UI invent a late-session re-cross (AVGO's false Monday CALLW).
    D/2D/3D intentionally keep their primary-candle lifecycle, and MACD keeps
    its separate provisional-candle behavior.
    """
    result = dict(snapshot)
    active_current = context.get("activeCurrent")
    if (
        incomplete_session
        and timeframe in {"4D", "W", "M"}
        and isinstance(active_current, Mapping)
        and active_current
    ):
        result["current"] = dict(active_current)
    return result


def _macd_group_keys_by_date(
    daily: Iterable[Mapping[str, Any]],
    intraday_dates: Iterable[str],
    incomplete_session_date: str = "",
    *,
    literal_timeframes: frozenset[str] = frozenset(),
) -> dict[str, dict[str, str]]:
    """Build the TOS chart's stable secondary-period phase.

    Fixed-duration secondary values follow the chart's finalized trading
    session grouping. The group must be stable while live bars arrive, but
    its phase is anchored to the newest chart session rather than imposed as
    one global calendar phase across every ticker.

    ``incomplete_session_date`` remains in the signature because it still
    controls provisional values elsewhere; it does not change membership.
    WEEK and MONTH retain their literal calendar boundaries.
    """

    session_dates = sorted(
        {
            date_key
            for date_key in (
                [str(bar.get("date") or "") for bar in daily]
                + [str(value or "") for value in intraday_dates]
            )
            if _DATE_KEY_PATTERN.fullmatch(date_key) and _is_weekday(date_key)
        }
    )
    result = {
        str(definition["key"]): {} for definition in GANESH_MACD_TIMEFRAMES
    }
    if not session_dates:
        return result

    latest_index = len(session_dates) - 1
    for definition in GANESH_MACD_TIMEFRAMES:
        timeframe = str(definition["key"])
        if timeframe in {"D", "W", "M"} or timeframe in literal_timeframes:
            result[timeframe] = {
                date_key: _macd_timeframe_group_key(timeframe, date_key)
                for date_key in session_dates
            }
            continue
        days = max(1, int(definition["days"]))
        result[timeframe] = {
            date_key: f"{timeframe}-R{math.floor((latest_index - index) / days)}"
            for index, date_key in enumerate(session_dates)
        }
    return result


def _macd_secondary_contexts(
    daily: list[Mapping[str, Any]],
    intraday_dates: list[str],
    incomplete_session_date: str = "",
    *,
    literal_timeframes: frozenset[str] = frozenset(),
) -> dict[str, dict[str, Any]]:
    """Replay finalized secondary bars and retain the open-bucket EMA base.

    TOS expands one secondary value across every primary candle contained by
    that secondary bucket. Historical buckets use their final close. The open
    bucket uses its official daily value when available and otherwise the
    latest primary close, but never recursively consumes every 4H close as a
    separate EMA row.
    """

    group_keys = _macd_group_keys_by_date(
        daily,
        intraday_dates,
        incomplete_session_date,
        literal_timeframes=literal_timeframes,
    )
    active_date = intraday_dates[-1] if intraday_dates else ""
    contexts: dict[str, dict[str, Any]] = {}
    for definition in GANESH_MACD_TIMEFRAMES:
        timeframe = str(definition["key"])
        keys_for_date = group_keys[timeframe]
        active_group_key = keys_for_date.get(active_date, "")
        grouped: OrderedDict[str, float] = OrderedDict()
        for bar in daily:
            date_key = str(bar.get("date") or "")
            group_key = keys_for_date.get(date_key, "")
            close = _finite(bar.get("close"))
            if group_key and close is not None:
                grouped[group_key] = close

        finalized: dict[str, dict[str, Any]] = {}
        committed: dict[str, float] | None = None
        for index, (group_key, close) in enumerate(grouped.items()):
            current = _source_values(committed, close)
            finalized[group_key] = {
                "previous": committed,
                "current": current,
                "completedBuckets": index,
            }
            committed = current

        active_finalized = finalized.get(active_group_key)
        if active_finalized is not None:
            active_base = active_finalized.get("previous")
            completed_buckets = int(active_finalized.get("completedBuckets") or 0)
        else:
            active_base = committed
            completed_buckets = len(finalized)
        contexts[timeframe] = {
            "groupKeysByDate": keys_for_date,
            "activeGroupKey": active_group_key,
            "activeBase": active_base,
            "activeCurrent": active_finalized.get("current") if active_finalized else None,
            "completedBuckets": completed_buckets,
            "finalized": finalized,
        }
    return contexts


def _calendar_secondary_contexts(
    daily: list[Mapping[str, Any]],
    intraday_dates: list[str],
) -> dict[str, dict[str, Any]]:
    """Build literal calendar-period snapshots for the two EMA studies.

    TOS plots the Sunday overnight bucket on Sunday and starts a new calendar
    day again at Monday 01:00. The supplied EMA studies use those calendar
    boundaries for D/2D/3D/4D bubbles, while the MACD study's multi-day source
    uses the trading-session alignment handled by ``_macd_secondary_contexts``.
    Keeping the two clocks separate is required for CALL3D on the first Sunday
    candle and CALL2D on Monday's first candle.
    """
    ordered_dates = sorted(
        {
            value
            for value in (
                [str(bar.get("date") or "") for bar in daily]
                + [str(value or "") for value in intraday_dates]
            )
            if _DATE_KEY_PATTERN.fullmatch(value)
        }
    )
    contexts: dict[str, dict[str, Any]] = {}
    for definition in GANESH_HIGHER_TIMEFRAMES:
        timeframe = str(definition["key"])
        group_dates: OrderedDict[str, list[str]] = OrderedDict()
        for date_key in ordered_dates:
            group_key = _timeframe_group_key(timeframe, date_key)
            if group_key:
                group_dates.setdefault(group_key, []).append(date_key)
        group_closes: dict[str, float] = {}
        for bar in daily:
            date_key = str(bar.get("date") or "")
            group_key = _timeframe_group_key(timeframe, date_key)
            close = _finite(bar.get("close"))
            if group_key and close is not None:
                group_closes[group_key] = close

        committed: dict[str, float] | None = None
        completed_buckets = 0
        by_date: dict[str, dict[str, Any]] = {}
        for group_key, dates in group_dates.items():
            close = group_closes.get(group_key)
            current = _source_values(committed, close) if close is not None else None
            snapshot = {
                "groupKey": group_key,
                "base": committed,
                "current": current,
                "completedBuckets": completed_buckets,
            }
            for date_key in dates:
                by_date[date_key] = snapshot
            if current is not None:
                committed = current
                completed_buckets += 1
        contexts[timeframe] = {"byDate": by_date}
    return contexts


def _event_sort_key(event: Mapping[str, Any]) -> tuple[int, int, int, str]:
    return (
        int(event.get("time") or 0),
        _FAMILY_RANK.get(str(event.get("family") or ""), 99),
        _TIMEFRAME_RANK.get(str(event.get("timeframe") or ""), 99),
        str(event.get("direction") or ""),
    )


def _calculate_signal_result(
    intraday_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    daily_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    aggregation_minutes: int = SOURCE_AGGREGATION_MINUTES,
    incomplete_session_date: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    intraday = _normalize_bars(intraday_bars)
    daily = [
        {**bar, "date": _date_key_for_bar(bar)}
        for bar in _normalize_bars(daily_bars)
    ]
    if not intraday:
        return [], False
    macd_carrier_bars = []
    for bar in intraday:
        calendar_date = _date_key_for_bar(bar)
        trading_date = _trading_date_key_for_bar(bar)
        if _is_weekday(trading_date):
            macd_carrier_bars.append(
                {
                    **bar,
                    "date": trading_date,
                    "calendarDate": calendar_date,
                    "tradingDate": trading_date,
                }
            )
    if not macd_carrier_bars:
        return [], False
    intraday_dates = list(
        dict.fromkeys(
            str(bar.get("tradingDate") or "")
            for bar in macd_carrier_bars
            if str(bar.get("tradingDate") or "")
        )
    )
    calendar_dates = list(
        dict.fromkeys(
            str(bar.get("calendarDate") or "")
            for bar in macd_carrier_bars
            if str(bar.get("calendarDate") or "")
        )
    )
    if not intraday_dates:
        return [], False
    has_sunday_extended = any(
        str(bar.get("calendarDate") or "") != str(bar.get("tradingDate") or "")
        for bar in macd_carrier_bars
    )
    literal_macd_timeframes = (
        frozenset({"2D", "3D"}) if has_sunday_extended else frozenset()
    )
    latest_intraday_date = intraday_dates[-1]
    secondary_daily = [bar for bar in daily if str(bar["date"]) <= latest_intraday_date]
    completed_macd_contexts = _macd_secondary_contexts(
        secondary_daily,
        intraday_dates,
        incomplete_session_date,
    )
    active_macd_contexts = _macd_secondary_contexts(
        secondary_daily,
        intraday_dates,
    )
    calendar_contexts = _calendar_secondary_contexts(
        secondary_daily,
        calendar_dates,
    )
    # The 9x20 script is attached to the 15D / 4H TOS chart, so replay the
    # same compact daily study window.  Feeding it the 5,000-bar levels seed
    # changes the ExpAverage initialization and suppresses the source CALL3D.
    tos_920_contexts = _calendar_secondary_contexts(
        secondary_daily[-_TOS_920_DAILY_WINDOW:],
        calendar_dates,
    )
    tos_920_window_ready = len(secondary_daily) >= _TOS_920_DAILY_WINDOW
    # ThinkScript's `GetYYYYMMDD()` and fixed 2D/3D aggregation are calendar
    # based for the MACD study.  Keep 4D/W/M on the established session replay
    # so their phase is not globally shifted for other symbols.
    literal_macd_contexts = _macd_secondary_contexts(
        secondary_daily,
        calendar_dates,
        incomplete_session_date,
        literal_timeframes=literal_macd_timeframes,
    )

    events: list[dict[str, Any]] = []
    macd_fired = {
        str(definition["key"]): {"CALL": False, "PUT": False}
        for definition in GANESH_MACD_TIMEFRAMES
    }
    previous_raw_macd = {
        str(definition["key"]): {"CALL": False, "PUT": False}
        for definition in GANESH_MACD_TIMEFRAMES
    }
    previous_48_secondary = {
        str(definition["key"]): None for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_48_group = {
        str(definition["key"]): "" for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_920_secondary = {
        str(definition["key"]): None for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_920_group = {
        str(definition["key"]): "" for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_raw_920 = {
        str(definition["key"]): {"CALL": False, "PUT": False}
        for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_calendar_date = ""
    previous_trading_date = ""
    for bar in macd_carrier_bars:
        calendar_date = str(bar.get("calendarDate") or _date_key_for_bar(bar))
        trading_date = str(bar.get("tradingDate") or _trading_date_key_for_bar(bar))
        new_calendar_day = calendar_date != previous_calendar_date
        new_trading_day = trading_date != previous_trading_date
        previous_calendar_date = calendar_date
        previous_trading_date = trading_date
        ema_snapshots: dict[str, dict[str, Any]] = {}
        literal_calendar_snapshots: dict[str, dict[str, Any]] = {}
        calendar_48_snapshots: dict[str, dict[str, Any]] = {}
        signal_48_snapshots: dict[str, dict[str, Any]] = {}
        macd_snapshots: dict[str, dict[str, Any]] = {}
        for definition in GANESH_MACD_TIMEFRAMES:
            timeframe = str(definition["key"])
            calendar_snapshot = (
                calendar_contexts.get(timeframe, {}).get("byDate", {}).get(calendar_date) or {}
            )
            calendar_base = calendar_snapshot.get("base") or {}
            calendar_current = calendar_snapshot.get("current") or {}
            calendar_has_official_current = bool(calendar_current)
            if calendar_base and (
                trading_date == incomplete_session_date or not calendar_current
            ):
                calendar_current = _source_values(calendar_base, float(bar["close"]))
            calendar_ema_snapshot = {
                "groupKey": calendar_snapshot.get("groupKey") or "",
                "base": calendar_base or None,
                "current": calendar_current,
                "completedBuckets": int(calendar_snapshot.get("completedBuckets") or 0),
            }
            literal_calendar_snapshots[timeframe] = calendar_ema_snapshot
            if trading_date == incomplete_session_date:
                ema_snapshots[timeframe] = calendar_ema_snapshot
                calendar_48_snapshots[timeframe] = calendar_ema_snapshot
            else:
                historical_context = completed_macd_contexts[timeframe]
                historical_group_key = historical_context["groupKeysByDate"].get(trading_date, "")
                if historical_group_key == historical_context["activeGroupKey"]:
                    historical_base = historical_context.get("activeBase") or {}
                    historical_current = historical_context.get("activeCurrent") or (
                        _source_values(
                            historical_base,
                            float(macd_carrier_bars[-1]["close"]),
                        )
                        if historical_base
                        else {}
                    )
                    historical_completed = int(historical_context.get("completedBuckets") or 0)
                else:
                    historical_finalized = (
                        historical_context["finalized"].get(historical_group_key) or {}
                    )
                    historical_base = historical_finalized.get("previous") or {}
                    historical_current = historical_finalized.get("current") or {}
                    historical_completed = int(
                        historical_finalized.get("completedBuckets") or 0
                    )
                historical_ema_snapshot = {
                    "groupKey": historical_group_key,
                    "base": historical_base or None,
                    "current": historical_current,
                    "completedBuckets": historical_completed,
                }
                # The 9x20 source and unfinished/no-official-close history keep
                # the source's trading-session expansion. Once an official
                # calendar secondary close exists, 4x8 D/2D/3D must retain
                # that exact calendar period after 16:00 ET. This prevents the
                # AVGO Monday CALL2D from disappearing after market close.
                ema_snapshots[timeframe] = historical_ema_snapshot
                calendar_48_snapshots[timeframe] = (
                    calendar_ema_snapshot
                    if calendar_has_official_current
                    else historical_ema_snapshot
                )

            # The 4x8 family and D/2D/3D/4D MACD bars follow TOS
            # trading-session alignment. During the open session, include
            # Monday in the right-aligned group but evaluate its provisional
            # value from this exact 4H candle close.
            contexts = (
                active_macd_contexts
                if trading_date == incomplete_session_date
                else completed_macd_contexts
            )
            context = contexts[timeframe]
            group_key = context["groupKeysByDate"].get(trading_date, "")
            if not group_key:
                continue
            if trading_date == incomplete_session_date:
                base = context.get("activeBase") or {}
                current = _source_values(base, float(bar["close"])) if base else {}
                completed_buckets = int(context.get("completedBuckets") or 0)
            elif group_key == context["activeGroupKey"]:
                base = context.get("activeBase") or {}
                current = context.get("activeCurrent") or (
                    _source_values(base, float(macd_carrier_bars[-1]["close"]))
                    if base
                    else {}
                )
                completed_buckets = int(context.get("completedBuckets") or 0)
            else:
                finalized = context["finalized"].get(group_key) or {}
                base = finalized.get("previous") or {}
                current = finalized.get("current") or {}
                completed_buckets = int(finalized.get("completedBuckets") or 0)
            right_aligned_snapshot = {
                "groupKey": group_key,
                "base": base or None,
                "current": current,
                "completedBuckets": completed_buckets,
            }
            stable_long_period_48_snapshot = _stable_open_long_period_48_snapshot(
                right_aligned_snapshot,
                context,
                timeframe,
                incomplete_session=trading_date == incomplete_session_date,
            )
            # ThinkScript's D/2D/3D secondary EMA series roll on their literal
            # calendar periods.  Reusing the right-aligned trading-session
            # grouping here happened to fit AMZN, but it moves AVGO's Monday
            # CALL2D onto Sunday and creates a false Monday PUT3D recross.  The
            # longer 4D/W/M carriers retain the source study's trading-session
            # alignment, which also keeps their already-open periods stable.
            signal_48_snapshots[timeframe] = (
                calendar_48_snapshots[timeframe]
                if timeframe in {"D", "2D", "3D"}
                else stable_long_period_48_snapshot
            )
            # WEEK and MONTH in the MACD study retain their literal calendar
            # boundary even after the regular session becomes historical.
            # Reusing ``ema_snapshots`` here was only correct while Monday was
            # live.  At 16:00 it switched to the trading-session snapshot and
            # retroactively painted NFLX's Monday weekly bubbles on Sunday's
            # 17:00/21:00 candles.  The source TOS chart keeps those bubbles on
            # Monday, so keep this clock independent of completion state.
            macd_snapshots[timeframe] = (
                literal_calendar_snapshots[timeframe]
                if timeframe in {"W", "M"}
                else right_aligned_snapshot
            )
            if timeframe == "D" or timeframe in literal_macd_timeframes:
                # `GetYYYYMMDD()` is a calendar value. A Sunday EXTO candle
                # cannot inherit Monday's MACD group merely because the
                # trading-session carrier rolls overnight.
                macd_snapshots[timeframe] = {}
                literal_context = literal_macd_contexts[timeframe]
                literal_group_key = literal_context["groupKeysByDate"].get(calendar_date, "")
                if literal_group_key:
                    if calendar_date == incomplete_session_date:
                        literal_base = literal_context.get("activeBase") or {}
                        literal_current = (
                            _source_values(literal_base, float(bar["close"]))
                            if literal_base
                            else {}
                        )
                        literal_completed = int(literal_context.get("completedBuckets") or 0)
                    elif literal_group_key == literal_context["activeGroupKey"]:
                        literal_base = literal_context.get("activeBase") or {}
                        literal_current = literal_context.get("activeCurrent") or {}
                        literal_completed = int(literal_context.get("completedBuckets") or 0)
                    else:
                        literal_finalized = literal_context["finalized"].get(literal_group_key) or {}
                        literal_base = literal_finalized.get("previous") or {}
                        literal_current = literal_finalized.get("current") or {}
                        literal_completed = int(literal_finalized.get("completedBuckets") or 0)
                    macd_snapshots[timeframe] = {
                        "groupKey": literal_group_key,
                        "base": literal_base or None,
                        "current": literal_current,
                        "completedBuckets": literal_completed,
                    }

        for definition in GANESH_HIGHER_TIMEFRAMES:
            timeframe = str(definition["key"])
            tos_920_snapshot = (
                tos_920_contexts.get(timeframe, {}).get("byDate", {}).get(calendar_date) or {}
            )
            tos_920_base = tos_920_snapshot.get("base") or {}
            if tos_920_base and (
                calendar_date == incomplete_session_date
                or not tos_920_snapshot.get("current")
            ):
                tos_920_snapshot = {
                    **tos_920_snapshot,
                    "current": _source_values(tos_920_base, float(bar["close"])),
                }
            snapshot = ema_snapshots.get(timeframe) or {}
            current = snapshot.get("current") or {}
            base = snapshot.get("base") or {}
            if not current or not base:
                continue

            snapshot_48 = signal_48_snapshots.get(timeframe) or {}
            current_48 = snapshot_48.get("current") or {}
            base_48 = snapshot_48.get("base") or {}
            ready_48 = _source_ready(snapshot_48, "ganesh48")
            group_48 = str(snapshot_48.get("groupKey") or "")
            previous_48 = (
                base_48
                if group_48 != previous_48_group[timeframe]
                else previous_48_secondary[timeframe] or base_48
            )
            cross_48 = {
                "CALL": ready_48 and _crossed_above(
                    previous_48.get("ema4"), previous_48.get("ema8"),
                    current_48.get("ema4"), current_48.get("ema8"),
                ),
                "PUT": ready_48 and _crossed_below(
                    previous_48.get("ema4"), previous_48.get("ema8"),
                    current_48.get("ema4"), current_48.get("ema8"),
                ),
            }
            confirmation_key = _NEXT_CONFIRMATION_TIMEFRAME.get(timeframe)
            confirmation = (
                (signal_48_snapshots.get(confirmation_key) or {}).get("current")
                if confirmation_key
                else None
            )
            for direction, crossed in cross_48.items():
                # TOS associates the first EXTO prices with the next trading
                # session, but the EMA bubble does not print until that
                # trading date's 01:00 ET calendar candle. This is the AVGO
                # Monday CALL2D placement in the supplied reference.
                if not crossed or calendar_date != trading_date:
                    continue
                confirmed = confirmation is None or (
                    float(confirmation["ema4"]) >= float(confirmation["ema8"])
                    if direction == "CALL"
                    else float(confirmation["ema4"]) <= float(confirmation["ema8"])
                )
                if confirmed:
                    events.append(
                        _event_record(
                            family="ganesh48",
                            timeframe=timeframe,
                            direction=direction,
                            time=int(bar["time"]),
                            bar=bar,
                        )
                    )

            snapshot_920 = tos_920_snapshot
            ready_920 = tos_920_window_ready and bool(
                (snapshot_920.get("base") or {}) and (snapshot_920.get("current") or {})
            )
            group_920 = str(snapshot_920.get("groupKey") or "")
            previous_920 = (
                (snapshot_920.get("base") or {})
                if group_920 != previous_920_group[timeframe]
                else previous_920_secondary[timeframe] or (snapshot_920.get("base") or {})
            )
            current_920 = snapshot_920.get("current") or {}
            live_raw_920 = {
                "CALL": ready_920 and _crossed_above(
                    previous_920.get("ema9"), previous_920.get("ema20"),
                    current_920.get("ema9"), current_920.get("ema20"),
                ),
                "PUT": ready_920 and _crossed_below(
                    previous_920.get("ema9"), previous_920.get("ema20"),
                    current_920.get("ema9"), current_920.get("ema20"),
                ),
            }
            # Preserve the source's active-session value when an official
            # daily close is already present in the cache.  This is necessary
            # for a forming Monday candle: the 9x20 study reads that candle's
            # live secondary value, not the compact-window finalization alone.
            legacy_raw_920 = (
                {
                    "CALL": _crossed_above(
                        base.get("ema9"), base.get("ema20"),
                        current.get("ema9"), current.get("ema20"),
                    ),
                    "PUT": _crossed_below(
                        base.get("ema9"), base.get("ema20"),
                        current.get("ema9"), current.get("ema20"),
                    ),
                }
                if calendar_date == incomplete_session_date or timeframe == "D"
                else {"CALL": False, "PUT": False}
            )
            live_raw_920 = {
                direction: bool(raw_signal or (ready_920 and legacy_raw_920[direction]))
                for direction, raw_signal in live_raw_920.items()
            }
            # `crosses ... within 1 bars` remains true on the immediately
            # following primary candle.  The source then gates that result
            # with `GetYYYYMMDD()`, which is the literal calendar transition.
            within_one_920 = {
                direction: bool(raw_signal or previous_raw_920[timeframe][direction])
                for direction, raw_signal in live_raw_920.items()
            }
            if new_calendar_day and calendar_date == trading_date:
                for direction, raw_signal in within_one_920.items():
                    if raw_signal:
                        events.append(
                            _event_record(
                                family="ganesh920",
                                timeframe=timeframe,
                                direction=direction,
                                time=int(bar["time"]),
                                bar=bar,
                                atr_multiplier=float(definition["atrMultiplier"]),
                            )
                        )
            previous_raw_920[timeframe] = live_raw_920
            previous_920_secondary[timeframe] = current_920
            previous_920_group[timeframe] = group_920
            macd_snapshot = macd_snapshots.get(timeframe) or {}
            macd_base = macd_snapshot.get("base") or {}
            macd_current = macd_snapshot.get("current") or {}
            ready_macd = _source_ready(macd_snapshot, "ganeshMacd")
            raw_cross_by_direction = {
                "CALL": ready_macd and _crossed_above(
                    macd_base.get("macdValue"), macd_base.get("macdAverage"),
                    macd_current.get("macdValue"), macd_current.get("macdAverage"),
                ),
                "PUT": ready_macd and _crossed_below(
                    macd_base.get("macdValue"), macd_base.get("macdAverage"),
                    macd_current.get("macdValue"), macd_current.get("macdAverage"),
                ),
            }
            # The source uses `crosses ... within 1 bars`, so its raw value is
            # true on the crossing candle and exactly one following primary
            # candle. CompoundValue below still guarantees one label per day.
            raw_by_direction = {
                direction: bool(
                    raw_cross or previous_raw_macd[timeframe][direction]
                )
                for direction, raw_cross in raw_cross_by_direction.items()
            }
            for direction, raw_signal in raw_by_direction.items():
                previously_fired = bool(macd_fired[timeframe][direction])
                if raw_signal and not previously_fired:
                    events.append(
                        _event_record(
                            family="ganeshMacd",
                            timeframe=timeframe,
                            direction=direction,
                            time=int(bar["time"]),
                            bar=bar,
                        )
                    )
                # Preserve the ThinkScript CompoundValue ordering exactly:
                # print reads fired[1], while the current bar can reset fired.
                # The WEEK/MONTH source and its CompoundValue latch turn over
                # at the calendar boundary.  Using the Sunday EXTO trading
                # date here moved the two NFLX MACD-W bubbles four/eight hours
                # early after the close.  Fixed-duration D/2D/3D/4D sources
                # keep their TOS trading-session latch.
                reset_macd_latch = new_calendar_day
                if reset_macd_latch:
                    macd_fired[timeframe][direction] = False
                elif raw_signal:
                    macd_fired[timeframe][direction] = True
                else:
                    macd_fired[timeframe][direction] = previously_fired
            previous_raw_macd[timeframe] = raw_cross_by_direction
            # The first EXTO bars belong to the next TOS trading session, but
            # the 4x8 bubble is hidden while calendar date and trading date
            # differ. Do not consume that pending crossover: the first 01:00
            # ET calendar candle must still compare with the prior visible
            # secondary state. This places AVGO's CALLW beside CALL2D at
            # Monday 01:00 without symbol-specific handling.
            if calendar_date == trading_date:
                previous_48_secondary[timeframe] = current_48
                previous_48_group[timeframe] = group_48

    unique = {str(event["key"]): event for event in events}
    ordered = sorted(unique.values(), key=_event_sort_key)

    latest_calendar_date = calendar_dates[-1] if calendar_dates else ""
    ema_history_ready = all(
        int(
            (
                calendar_contexts.get(str(definition["key"]), {})
                .get("byDate", {})
                .get(latest_calendar_date, {})
                .get("completedBuckets")
                or 0
            )
        ) >= _FAMILY_PREFETCH_BUCKETS["ganesh920"]
        for definition in GANESH_HIGHER_TIMEFRAMES
    )
    macd_history_ready = all(
        int(
            (
                calendar_contexts.get(str(definition["key"]), {})
                .get("byDate", {})
                .get(latest_calendar_date, {})
                .get("completedBuckets")
                if str(definition["key"]) in {"W", "M"}
                else active_macd_contexts[str(definition["key"])].get("completedBuckets")
            )
            or 0
        ) >= _FAMILY_PREFETCH_BUCKETS["ganeshMacd"]
        for definition in GANESH_MACD_TIMEFRAMES
    )
    return ordered, bool(ema_history_ready and macd_history_ready)


def _literal_secondary_states_by_date(
    daily_bars: list[Mapping[str, Any]],
    carrier_bars: list[Mapping[str, Any]],
) -> dict[str, dict[str, dict[str, Any]]]:
    """Replay the literal D/2D/3D/4D/W/M secondary series in the TOS studies.

    The former backend-only evaluator added Sunday/Monday phase rules on top
    of the supplied ThinkScript.  Those rules caused its source tape to place
    otherwise identical MACD and EMA signals on a different 4H candle than
    the chart.  This uses the same calendar-date carrier and right-aligned
    secondary grouping as the source-study replay used to validate the chart.
    """
    daily_by_date: dict[str, dict[str, Any]] = {}
    for bar in daily_bars:
        date_key = _date_key_for_bar(bar)
        if date_key and _is_weekday(date_key):
            daily_by_date[date_key] = {**bar, "date": date_key}
    daily = [daily_by_date[key] for key in sorted(daily_by_date)]
    session_dates = sorted(
        {
            *daily_by_date,
            *(
                _date_key_for_bar(bar)
                for bar in carrier_bars
                if _is_weekday(_date_key_for_bar(bar))
            ),
        }
    )
    result: dict[str, dict[str, dict[str, Any]]] = {
        str(definition["key"]): {} for definition in GANESH_HIGHER_TIMEFRAMES
    }
    if not session_dates:
        return result

    for definition in GANESH_HIGHER_TIMEFRAMES:
        timeframe = str(definition["key"])
        groups: list[dict[str, Any]] = []
        for index, date_key in enumerate(session_dates):
            if timeframe == "D":
                group_key = date_key
            elif timeframe == "W":
                group_key = f"W-{_week_key(date_key)}"
            elif timeframe == "M":
                group_key = f"M-{date_key[:7]}"
            else:
                days = max(1, int(definition["days"]))
                # TOS groups fixed multi-day secondary periods from the newest
                # trading session visible on the chart, not from a rolling
                # intraday request boundary.
                group_key = f"{timeframe}-R{math.floor((len(session_dates) - 1 - index) / days)}"
            previous = groups[-1] if groups else None
            if previous and previous["key"] == group_key:
                previous["dates"].append(date_key)
            else:
                groups.append({"key": group_key, "dates": [date_key], "close": None})

        group_by_date = {
            date_key: group for group in groups for date_key in group["dates"]
        }
        for bar in daily:
            group = group_by_date.get(str(bar["date"]))
            close = _finite(bar.get("close"))
            if group is not None and close is not None:
                group["close"] = close

        committed: dict[str, float] | None = None
        completed_buckets = 0
        for group_index, group in enumerate(groups):
            close = _finite(group.get("close"))
            current = _source_values(committed, close) if close is not None else None
            snapshot = {
                "groupKey": group["key"],
                "base": committed,
                "current": current,
                "completedBuckets": completed_buckets,
                "open": group_index == len(groups) - 1,
            }
            for date_key in group["dates"]:
                result[timeframe][date_key] = snapshot
            if current is not None:
                committed = current
                completed_buckets += 1
    return result


def _calculate_v13_literal_signal_result(
    intraday_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    daily_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    aggregation_minutes: int = SOURCE_AGGREGATION_MINUTES,
    incomplete_session_date: str = "",
) -> tuple[list[dict[str, Any]], bool]:
    """Evaluate the supplied ThinkScript without backend-only time shifting.

    ``incomplete_session_date`` remains accepted for compatibility with the
    public payload builder, but the literal studies do not switch their
    primary-candle clock when a session is incomplete.
    """
    del aggregation_minutes, incomplete_session_date
    intraday = _normalize_bars(intraday_bars)
    daily = _normalize_bars(daily_bars)
    carrier_bars = [
        {**bar, "date": _date_key_for_bar(bar)}
        for bar in intraday
        if _is_weekday(_date_key_for_bar(bar))
    ]
    if not carrier_bars:
        return [], False

    secondary_states = _literal_secondary_states_by_date(daily, carrier_bars)
    events: list[dict[str, Any]] = []
    macd_fired = {
        str(definition["key"]): {"CALL": False, "PUT": False}
        for definition in GANESH_MACD_TIMEFRAMES
    }
    previous_raw_macd = {
        str(definition["key"]): {"CALL": False, "PUT": False}
        for definition in GANESH_MACD_TIMEFRAMES
    }
    previous_48_secondary = {
        str(definition["key"]): None for definition in GANESH_HIGHER_TIMEFRAMES
    }
    previous_date = ""

    for bar in carrier_bars:
        date_key = str(bar["date"])
        new_day = date_key != previous_date
        previous_date = date_key
        snapshots: dict[str, dict[str, Any]] = {}
        for definition in GANESH_HIGHER_TIMEFRAMES:
            timeframe = str(definition["key"])
            finalized = secondary_states.get(timeframe, {}).get(date_key)
            if not finalized or not finalized.get("base"):
                continue
            active_close = _finite(carrier_bars[-1].get("close"))
            current = finalized.get("current") or (
                _source_values(finalized.get("base"), active_close)
                if finalized.get("open") and active_close is not None
                else None
            )
            if current is not None:
                snapshots[timeframe] = {**finalized, "current": current}

        for definition in GANESH_HIGHER_TIMEFRAMES:
            timeframe = str(definition["key"])
            snapshot = snapshots.get(timeframe)
            if not snapshot:
                continue
            base = snapshot.get("base") or {}
            current = snapshot.get("current") or {}
            if not base or not current:
                continue

            ready_48 = _source_ready(snapshot, "ganesh48")
            previous_48 = previous_48_secondary[timeframe] or base
            cross_48 = {
                "CALL": ready_48 and _crossed_above(
                    previous_48.get("ema4"), previous_48.get("ema8"),
                    current.get("ema4"), current.get("ema8"),
                ),
                "PUT": ready_48 and _crossed_below(
                    previous_48.get("ema4"), previous_48.get("ema8"),
                    current.get("ema4"), current.get("ema8"),
                ),
            }
            confirmation_key = _NEXT_CONFIRMATION_TIMEFRAME.get(timeframe)
            confirmation = (
                (snapshots.get(confirmation_key) or {}).get("current")
                if confirmation_key
                else None
            )
            for direction, crossed in cross_48.items():
                if not crossed:
                    continue
                confirmed = not confirmation or (
                    float(confirmation["ema4"]) >= float(confirmation["ema8"])
                    if direction == "CALL"
                    else float(confirmation["ema4"]) <= float(confirmation["ema8"])
                )
                if confirmed:
                    events.append(
                        _event_record(
                            family="ganesh48",
                            timeframe=timeframe,
                            direction=direction,
                            time=int(bar["time"]),
                            bar=bar,
                        )
                    )

            ready_920 = _source_ready(snapshot, "ganesh920")
            raw_920 = {
                "CALL": ready_920 and _crossed_above(
                    base.get("ema9"), base.get("ema20"),
                    current.get("ema9"), current.get("ema20"),
                ),
                "PUT": ready_920 and _crossed_below(
                    base.get("ema9"), base.get("ema20"),
                    current.get("ema9"), current.get("ema20"),
                ),
            }
            if new_day:
                for direction, crossed in raw_920.items():
                    if crossed:
                        events.append(
                            _event_record(
                                family="ganesh920",
                                timeframe=timeframe,
                                direction=direction,
                                time=int(bar["time"]),
                                bar=bar,
                                atr_multiplier=float(definition["atrMultiplier"]),
                            )
                        )

            ready_macd = _source_ready(snapshot, "ganeshMacd")
            raw_cross_macd = {
                "CALL": ready_macd and _crossed_above(
                    base.get("macdValue"), base.get("macdAverage"),
                    current.get("macdValue"), current.get("macdAverage"),
                ),
                "PUT": ready_macd and _crossed_below(
                    base.get("macdValue"), base.get("macdAverage"),
                    current.get("macdValue"), current.get("macdAverage"),
                ),
            }
            raw_macd = {
                direction: bool(raw_cross or previous_raw_macd[timeframe][direction])
                for direction, raw_cross in raw_cross_macd.items()
            }
            for direction, crossed in raw_macd.items():
                previously_fired = bool(macd_fired[timeframe][direction])
                if crossed and not previously_fired:
                    events.append(
                        _event_record(
                            family="ganeshMacd",
                            timeframe=timeframe,
                            direction=direction,
                            time=int(bar["time"]),
                            bar=bar,
                        )
                    )
                # CompoundValue reads the previous latch before it resets on
                # the first primary candle of a new day.
                if new_day:
                    macd_fired[timeframe][direction] = False
                elif crossed:
                    macd_fired[timeframe][direction] = True
                else:
                    macd_fired[timeframe][direction] = previously_fired
            previous_raw_macd[timeframe] = raw_cross_macd
            previous_48_secondary[timeframe] = current

    unique = {str(event["key"]): event for event in events}
    ordered = sorted(unique.values(), key=_event_sort_key)
    latest_date = str(carrier_bars[-1]["date"])
    history_ready = all(
        _source_ready(
            secondary_states.get(str(definition["key"]), {}).get(latest_date),
            "ganesh920",
        )
        for definition in GANESH_HIGHER_TIMEFRAMES
    ) and all(
        _source_ready(
            secondary_states.get(str(definition["key"]), {}).get(latest_date),
            "ganeshMacd",
        )
        for definition in GANESH_MACD_TIMEFRAMES
    )
    return ordered, history_ready


def calculate_ganesh_higher_timeframe_signals(
    intraday_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    daily_bars: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    *,
    aggregation_minutes: int = SOURCE_AGGREGATION_MINUTES,
) -> list[dict[str, Any]]:
    """Return the raw source-event tape without API wrapper metadata."""
    signals, _ = _calculate_signal_result(
        intraday_bars,
        daily_bars,
        aggregation_minutes=aggregation_minutes,
    )
    return signals


def build_ganesh_higher_timeframe_signal_payload(
    study_frame: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    live_frame: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
    daily_frame: pd.DataFrame | Iterable[Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Build the versioned backend payload consumed by every chart layout."""
    primary_bars = build_ganesh_primary_bars(study_frame, live_frame)
    live_source = _normalize_bars(live_frame)
    historical_source = _normalize_bars(study_frame) if not live_source else []
    latest_source = (live_source or historical_source)[-1] if (live_source or historical_source) else None
    incomplete_session_date = ""
    if latest_source is not None:
        latest_source_time = int(latest_source["time"])
        latest_eastern = datetime.fromtimestamp(
            latest_source_time,
            tz=timezone.utc,
        ).astimezone(_EASTERN_ZONE)
        if latest_eastern.weekday() < 5 and (
            latest_eastern.hour * 60 + latest_eastern.minute
        ) < 16 * 60:
            incomplete_session_date = latest_eastern.date().isoformat()
    signals, history_ready = _calculate_signal_result(
        primary_bars,
        daily_frame,
        aggregation_minutes=SOURCE_AGGREGATION_MINUTES,
        incomplete_session_date=incomplete_session_date,
    )
    return {
        "schemaVersion": SCHEMA_VERSION,
        "mode": SIGNAL_MODE,
        "sourceAggregationMinutes": SOURCE_AGGREGATION_MINUTES,
        "historyReady": history_ready,
        "signals": signals,
    }


__all__ = [
    "GANESH_HIGHER_TIMEFRAMES",
    "GANESH_MACD_TIMEFRAMES",
    "SCHEMA_VERSION",
    "SIGNAL_MODE",
    "SOURCE_AGGREGATION_MINUTES",
    "build_ganesh_higher_timeframe_signal_payload",
    "build_ganesh_primary_bars",
    "calculate_ganesh_higher_timeframe_signals",
]
