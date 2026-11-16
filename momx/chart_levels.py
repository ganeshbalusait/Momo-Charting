"""What the chart draws, worked out in Python for the chat (standard library only).

The chat must quote the same numbers the trader sees on Charts & OI, and it may
not start a chart build to get them. So this is a line-for-line port of the
browser's own level formulas, run over a chart tape the server already holds:

  frontend/src/chartAggregation.js   candles for the timeframe on screen
  frontend/src/dailyBars.js          one daily candle per trading day
  frontend/src/pivotPeriods.js       which day/week/month a pivot reads
  frontend/src/woodiesPivots.js      Woodies pivots (Daily chart only)
  frontend/src/tosSignalLayout.js    signals pinned to candles
  frontend/src/ganeshHigherTimeframeSignals.js   D..M signals on the chart
  frontend/src/App.jsx               EMA, SMA, VWAP, pivots, previous OHLC,
                                     session levels, MTF averages, signal filter

The port copies the chart exactly, even where the chart's data has known
problems (a Sunday-night stub feeding Monday's pivots, thin daily rows). Fixing
those is a chart job; a chat that disagreed with the chart would be worse.
tests/test_momx_chart_levels.py runs the real JavaScript over fixed tapes and
requires zero differences on all 11 timeframes.

Only the asked timeframe is computed. Nothing here reads a file, touches a
cache or calls anything: callers hand in a tape they already hold.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import statistics
import sys
import time as _time
from collections import deque
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

#: OI_CHART_TIMEFRAMES in App.jsx, in chart order. A parity test watches it.
TIMEFRAMES = {
    "3m": 3, "5m": 5, "10m": 10, "15m": 15, "30m": 30, "1h": 60,
    "2h": 120, "4h": 240, "D": 1440, "W": 10080, "M": 43200,
}
#: The chart's default study settings; a saved profile in the browser can differ.
DEFAULTS = {"ema": [9, 21, 50], "sma": 200, "mtfMaProximityPct": 10, "signalSessionsBack": 1,
            "woodiesTimeFrame": "4 DAYS"}
PIVOT_LEVELS = ("R3", "R2", "R1", "PP", "S1", "S2", "S3")
PERIODS = ("DAY", "WEEK", "MONTH")

_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_HOUR_DATE: dict[int, str] = {}
_DAY_MIDNIGHT: dict[str, int] = {}
_CACHE_LIMIT = 40000


def _pause() -> None:
    """Hand the interpreter to other threads between stages (api_server stays responsive)."""
    _time.sleep(0)


def _number(value) -> float:
    """JavaScript's Number() for the shapes a tape can hold."""
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0.0
        try:
            return float(text)
        except ValueError:
            return math.nan
    return math.nan


def to_fixed4(value: float) -> float:
    """Number(value.toFixed(4)): the exact binary value, a tie rounds away from zero."""
    return float(Decimal(value).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))


def et_date(timestamp: int) -> str:
    """Eastern calendar date. It cannot change inside one UTC hour, even across DST."""
    hour = int(timestamp) // 3600
    held = _HOUR_DATE.get(hour)
    if held is None:
        if len(_HOUR_DATE) >= _CACHE_LIMIT:
            _HOUR_DATE.clear()
        held = datetime.fromtimestamp(timestamp, ET).date().isoformat()
        _HOUR_DATE[hour] = held
    return held


def et_clock(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, ET).strftime("%H:%M")


def _et_minutes(timestamp: int) -> int:
    moment = datetime.fromtimestamp(timestamp, ET)
    return moment.hour * 60 + moment.minute


def _midnight(day: str) -> int:
    held = _DAY_MIDNIGHT.get(day)
    if held is None:
        if len(_DAY_MIDNIGHT) >= _CACHE_LIMIT:
            _DAY_MIDNIGHT.clear()
        parsed = date.fromisoformat(day)
        held = int(datetime(parsed.year, parsed.month, parsed.day, tzinfo=ET).timestamp())
        _DAY_MIDNIGHT[day] = held
    return held


def bucket_time(timestamp: int, minutes: int) -> int:
    """chartAggregationBucketTime."""
    if minutes in (120, 240):   # TOS clock: midnight Central, one hour after Eastern midnight
        anchor = _midnight(et_date(timestamp)) + 3600
        seconds = minutes * 60
        return anchor + ((timestamp - anchor) // seconds) * seconds
    if minutes == 1440:
        return _midnight(et_date(timestamp))
    if minutes == 10080:
        day = date.fromisoformat(et_date(timestamp))
        return _midnight((day - timedelta(days=day.weekday())).isoformat())
    if minutes == 43200:
        return _midnight(et_date(timestamp)[:8] + "01")
    return (timestamp // (minutes * 60)) * minutes * 60


def _normalize_slow(bar: dict) -> dict | None:
    """One bar with any oddity (strings, nulls, NaN): the exact JavaScript rules."""
    raw_time, raw_open = bar.get("time"), bar.get("open")
    raw_high, raw_low, raw_close = bar.get("high"), bar.get("low"), bar.get("close")
    if raw_time is None or raw_open is None or raw_high is None or raw_low is None or raw_close is None:
        return None
    stamp, open_, high = _number(raw_time), _number(raw_open), _number(raw_high)
    low, close = _number(raw_low), _number(raw_close)
    if not all(map(math.isfinite, (stamp, open_, high, low, close))):
        return None
    stamp = math.floor(stamp)
    if stamp <= 0:
        return None
    volume = bar.get("volume")
    volume = _number(volume) if volume is not None else 0.0
    clean = {"time": stamp, "open": open_, "high": max(high, open_, close), "low": min(low, open_, close),
             "close": close, "volume": volume if math.isfinite(volume) else 0.0}
    if bar.get("date"):
        clean["date"] = bar["date"]
    return clean


def normalize(rows) -> list[dict]:
    """normalizeChartCandleBars: one bar per time, repaired bounds, sorted.

    Almost every row is plain finite numbers, so that case is kept short: this
    loop is most of the cost of a chat answer and it shares a CPU with the app.
    """
    if not isinstance(rows, (list, tuple)):
        return []
    by_time: dict[int, dict] = {}
    for bar in rows:
        if bar.__class__ is not dict:
            continue
        try:
            stamp, open_, high, low, close = bar["time"], bar["open"], bar["high"], bar["low"], bar["close"]
            volume = bar.get("volume", 0.0)
            total = stamp + open_ + high + low + close + volume   # TypeError for a string or null
            if total - total != 0 or stamp <= 0:                  # NaN or infinity somewhere
                raise TypeError
        except (KeyError, TypeError):
            clean = _normalize_slow(bar)
            if clean is not None:
                by_time[clean["time"]] = clean
            continue
        if stamp.__class__ is not int:
            stamp = math.floor(stamp)
            if stamp <= 0:
                continue
        if high < open_ or high < close:
            high = max(high, open_, close)
        if low > open_ or low > close:
            low = min(low, open_, close)
        day = bar.get("date")
        by_time[stamp] = ({"time": stamp, "open": open_, "high": high, "low": low, "close": close, "volume": volume, "date": day}
                          if day else
                          {"time": stamp, "open": open_, "high": high, "low": low, "close": close, "volume": volume})
    return [by_time[stamp] for stamp in sorted(by_time)]


def daily_trading_day(timestamp: int) -> str | None:
    """dailyBarTradingDay: a weekend-evening stub is not a session."""
    moment = datetime.fromtimestamp(timestamp, ET)
    if moment.hour >= 20 and moment.weekday() >= 5:
        return None
    return moment.date().isoformat()


def dedupe_daily(rows: list[dict]) -> list[dict]:
    """dedupeDailyBars: the fullest copy of each trading day wins."""
    by_day: dict[str, dict] = {}
    duplicated = False
    for bar in rows:
        day = daily_trading_day(bar["time"])
        if not day:
            duplicated = True
            continue
        held = by_day.get(day)
        if held is not None or (bar.get("date") and bar["date"] != day):
            duplicated = True
        volume = bar.get("volume") or 0
        held_volume = (held or {}).get("volume") or 0
        if held is None or volume > held_volume or (volume == held_volume and bar["time"] > held["time"]):
            by_day[day] = bar
    if not duplicated:
        return rows
    return [by_day[day] if by_day[day].get("date") == day else {**by_day[day], "date": day} for day in sorted(by_day)]


def aggregate(bars: list[dict], minutes: int) -> list[dict]:
    """aggregateChartBars over already-normalized bars."""
    minutes = max(minutes, 1)
    buckets: dict[int, dict] = {}
    seconds = minutes * 60
    calendar = minutes in (120, 240, 1440, 10080, 43200)
    for bar in bars:
        stamp = bucket_time(bar["time"], minutes) if calendar else bar["time"] // seconds * seconds
        if stamp <= 0:
            continue
        current = buckets.get(stamp)
        if current is None:
            buckets[stamp] = {**bar, "time": stamp}
            continue
        high, low = bar["high"] or 0, bar["low"] or 0
        if high > (current["high"] or 0):
            current["high"] = high
        if low < (current["low"] or 0):
            current["low"] = low
        current["close"] = bar["close"] or current["close"] or 0
        current["volume"] += bar["volume"]
    return [buckets[stamp] for stamp in sorted(buckets)]


def spacing_minutes(bars: list[dict], fallback: int = 5) -> int:
    """chartSourceBarSpacingMinutes: the modal gap of the first rows."""
    counts: dict[int, int] = {}
    for index in range(1, min(len(bars), 60)):
        gap = bars[index]["time"] - bars[index - 1]["time"]
        if gap > 0:
            counts[gap] = counts.get(gap, 0) + 1
    best_gap = best_count = 0
    for gap, count in counts.items():
        if count > best_count:
            best_gap, best_count = gap, count
    return max(1, math.floor(best_gap / 60 + 0.5)) if best_gap > 0 else fallback


def overlay(historical: list[dict], live: list[dict]) -> list[dict]:
    """overlayLiveOnHistorical: a closed historical candle is never replaced."""
    merged = {bar["time"]: bar for bar in historical}
    last_closed = historical[-1]["time"] if historical else -math.inf
    for bar in live:
        stamp = bar["time"]
        held = merged.get(stamp)
        if held is None:
            merged[stamp] = bar
        elif stamp >= last_closed:
            merged[stamp] = {**bar, "volume": max(bar["volume"], held["volume"])}
    return [merged[stamp] for stamp in sorted(merged)]


def display_bars(study: list[dict], fine: list[dict], live: list[dict], daily: list[dict], minutes: int) -> list[dict]:
    """buildChartDisplayBars with normalized sources."""
    minutes = max(minutes, 1)
    if minutes >= 1440 and daily:
        return aggregate(overlay(daily, aggregate(live, 1440)), minutes)
    study_cadence = spacing_minutes(study)
    if study and (minutes >= 240 or (study_cadence > 0 and study_cadence <= minutes)):
        shared = 30 if study_cadence >= 30 else 5
        return aggregate(overlay(aggregate(study, shared), aggregate(live, shared)), minutes)
    fine_cadence = spacing_minutes(fine)
    reaches_further = bool(fine) and (not live or (fine[0]["time"] or 0) < (live[0]["time"] or 0))
    if fine and 0 < fine_cadence <= minutes and reaches_further:
        return overlay(aggregate(fine, minutes), aggregate(live, minutes))
    return aggregate(live, minutes)


def ema_last(bars: list[dict], period: int) -> float | None:
    """calculateChartEma(...).at(-1).value"""
    if not bars:
        return None
    multiplier = 2 / (period + 1)
    ema = bars[0]["close"] or 0
    for bar in bars:
        ema = (bar["close"] or 0) * multiplier + ema * (1 - multiplier)
    return to_fixed4(ema)


def ema_series(bars: list[dict], period: int) -> list[float]:
    """calculateChartEma(...) point values: the running value unrounded, each point rounded with toFixed(4),
    exactly as the chart draws them. The last one equals ema_last."""
    multiplier = 2 / (period + 1)
    ema = (bars[0]["close"] or 0) if bars else 0
    out = []
    for bar in bars:
        ema = (bar["close"] or 0) * multiplier + ema * (1 - multiplier)
        out.append(to_fixed4(ema))
    return out


#: Bars back for the 21/50 EMA slope ("rising over the last 5 bars"), and the change below which it is "flat".
SLOPE_BARS = 5
FLAT_FRACTION = 0.0005


def _last_cross(first: list[float], second: list[float]) -> dict | None:
    """The newest bar where `first` crossed `second`: {"index", "dir": "up"|"down"}."""
    for index in range(len(first) - 1, 0, -1):
        now_gap, before_gap = first[index] - second[index], first[index - 1] - second[index - 1]
        if now_gap > 0 >= before_gap:
            return {"index": index, "dir": "up"}
        if now_gap < 0 <= before_gap:
            return {"index": index, "dir": "down"}
    return None


def _slope(series: list[float]) -> str | None:
    if len(series) <= SLOPE_BARS:
        return None
    change = series[-1] - series[-1 - SLOPE_BARS]
    if abs(change) <= abs(series[-1]) * FLAT_FRACTION:
        return "flat"
    return "rising" if change > 0 else "falling"


def ema_detail(bars: list[dict]) -> dict | None:
    """The 21 and 50 EMA on one timeframe's bars, as the chart computes them: values (equal to ema_last), price
    distance in %, above/below, 21 vs 50, the newest 21/50 cross and the newest price cross of each, and slope."""
    if len(bars) < 2:
        return None
    closes = [bar["close"] or 0 for bar in bars]
    e21, e50 = ema_series(bars, 21), ema_series(bars, 50)
    close = closes[-1]

    def stamp(found: dict | None) -> dict | None:
        return {"time": bars[found["index"]]["time"], "dir": found["dir"]} if found else None

    def side(value: float) -> dict:
        return {"value": value, "above": close > value, "pct": to_fixed4((close - value) / value * 100) if value else None}

    return {"time": bars[-1]["time"], "close": close, "ema21": side(e21[-1]), "ema50": side(e50[-1]),
            "ema21Above50": e21[-1] > e50[-1], "cross2150": stamp(_last_cross(e21, e50)),
            "priceCross21": stamp(_last_cross(closes, e21)), "priceCross50": stamp(_last_cross(closes, e50)),
            "slope21": _slope(e21), "slope50": _slope(e50), "bars": len(bars)}


def sma_last(bars: list[dict], period: int) -> float | None:
    """calculateChartSma(...).at(-1)?.value; same running total, same float drift."""
    if not bars or period <= 0 or len(bars) < period:
        return None
    total = 0.0
    closes: deque[float] = deque()
    for bar in bars:
        close = bar["close"] or 0
        closes.append(close)
        total += close
        if len(closes) > period:
            total -= closes.popleft()
    return to_fixed4(total / period)


def vwap_last(bars: list[dict]) -> float | None:
    """calculateSessionVwap(...).at(-1).value: resets each Eastern calendar date."""
    if not bars:
        return None
    day = ""
    volume_sum = price_volume_sum = 0.0
    value = 0.0
    for bar in bars:
        bar_day = et_date(bar["time"])
        if bar_day != day:
            day, volume_sum, price_volume_sum = bar_day, 0.0, 0.0
        volume = max(bar["volume"] or 0, 0)
        typical = ((bar["high"] or 0) + (bar["low"] or 0) + (bar["close"] or 0)) / 3
        volume_sum += volume
        price_volume_sum += typical * volume
        value = price_volume_sum / volume_sum if volume_sum > 0 else (bar["close"] or 0)
    return to_fixed4(value)


def _week_key(day: str) -> str:
    parsed = date.fromisoformat(day)
    return (parsed - timedelta(days=parsed.weekday())).isoformat()


def session_levels(bars: list[dict], daily: list[dict]) -> dict | None:
    """The sessionLevelsStudy memo: pmH/pmL, dH/dL, wH/wL and ATH."""
    if not bars:
        return None
    latest_date = et_date(bars[-1]["time"])
    latest_week = _week_key(latest_date)
    track: dict[str, dict | None] = {"pm": None, "day": None, "week": None}

    def merge(slot: str, bar: dict) -> None:
        held = track[slot]
        track[slot] = ({"high": max(held["high"], bar["high"]), "low": min(held["low"], bar["low"])}
                       if held else {"high": bar["high"], "low": bar["low"]})

    for bar in reversed(bars):   # dates only go up, so stop at the week's start
        day = et_date(bar["time"])
        if day < latest_week:
            break
        if day == latest_date:
            merge("day", bar)
            if et_clock(bar["time"]) < "09:30":
                merge("pm", bar)
        merge("week", bar)
    highs = [bar["high"] or -math.inf for bar in daily] + [bar["high"] or -math.inf for bar in bars]
    all_time_high = max(highs)
    return {**track, "allTimeHigh": all_time_high if math.isfinite(all_time_high) else None}


def _date_key(value) -> str:
    """normalizedDateKey: an impossible date rolls over the way Date.UTC does."""
    match = _ISO_DATE.match(str(value or ""))
    if not match:
        return ""
    year, month, day = int(match[1]), int(match[2]), int(match[3])
    try:
        first = date(year + (month - 1) // 12, (month - 1) % 12 + 1, 1)
        return (first + timedelta(days=day - 1)).isoformat()
    except (ValueError, OverflowError):
        return ""


def _bar_date(bar: dict) -> str:
    return str(bar.get("date") or et_date(bar["time"]))


def merged_days(daily: list[dict], chart: list[dict]) -> list[dict]:
    """mergeDailyAndChartOhlcDays: daily rows lead; chart fills gaps and the live date."""
    days: dict[str, dict] = {}

    keys: dict[str, str] = {}

    def key_of(bar: dict) -> str:
        raw = _bar_date(bar)
        held = keys.get(raw)
        if held is None:
            held = keys[raw] = _date_key(raw)
        return held

    def merge(day: str, bar: dict) -> None:
        held = days.get(day)
        days[day] = {"date": day, "open": held["open"], "high": max(held["high"], bar["high"]),
                     "low": min(held["low"], bar["low"]), "close": bar["close"]} if held else {
            "date": day, "open": bar["open"], "high": bar["high"], "low": bar["low"], "close": bar["close"]}

    for bar in daily:
        day = key_of(bar)
        if day:
            merge(day, bar)
    source = [(day, bar) for day, bar in ((key_of(bar), bar) for bar in chart) if day]
    latest = source[-1][0] if source else ""
    daily_dates = set(days)
    for day, bar in source:
        if day == latest or day not in daily_dates:
            merge(day, bar)
    return [days[key] for key in sorted(days)]


def _period_key(day: str, timeframe: str) -> str:
    if timeframe == "MONTH":
        return day[:7]
    if timeframe == "WEEK":
        return _week_key(day)
    return day


def _periods(days: list[dict], timeframe: str) -> list[dict]:
    grouped: dict[str, dict] = {}
    for day in days:
        key = _period_key(day["date"], timeframe)
        held = grouped.get(key)
        grouped[key] = ({"key": key, "open": held["open"], "high": max(held["high"], day["high"]),
                         "low": min(held["low"], day["low"]), "close": day["close"]} if held else
                        {"key": key, "open": day["open"], "high": day["high"], "low": day["low"], "close": day["close"]})
    return [grouped[key] for key in sorted(grouped)]


def pivot_source_period(days: list[dict], current: str, timeframe: str) -> dict | None:
    """pivotPointsSourcePeriod: last completed calendar week/month; DAY is the prior row."""
    if timeframe in ("WEEK", "MONTH"):
        current_key = _period_key(current, timeframe)
        prior = [period for period in _periods(days, timeframe) if period["key"] < current_key]
        return prior[-1] if prior else None
    prior_days = [day for day in days if day["date"] < current]
    return prior_days[-1] if prior_days else None


def standard_pivots(period: dict | None) -> dict | None:
    """standardPivotLevels."""
    if not period:
        return None
    high, low, close = period["high"], period["low"], period["close"]
    pivot = (high + low + close) / 3
    spread = high - low
    return {"R3": pivot + 2 * spread, "R2": pivot + spread, "R1": 2 * pivot - low, "PP": pivot,
            "S1": 2 * pivot - high, "S2": pivot - spread, "S3": pivot - 2 * spread}


def previous_ohlc(days: list[dict], latest_date: str) -> dict:
    """buildPreviousOhlcStudy on the newest candle: the period before the current one."""
    result: dict[str, dict | None] = {}
    for timeframe in PERIODS:
        periods = _periods(days, timeframe)
        key = _period_key(latest_date, timeframe)
        index = next((position for position, period in enumerate(periods) if period["key"] == key), None)
        prior = periods[index - 1] if index else None
        result[timeframe] = ({field: prior[field] for field in ("open", "high", "low", "close")} if prior else None)
    return result


_WEEKDAY_REMAINDER = (0, 1, 2, 2, 2, 3, 4)   # 1970-01-01 was a Thursday
_EPOCH_ORDINAL = date(1970, 1, 1).toordinal()


def _woodies_key(day: str) -> str:
    """woodiesPeriodKey for the chart's default 4 DAYS."""
    days = date.fromisoformat(day).toordinal() - _EPOCH_ORDINAL
    weeks = days // 7
    return f"4D-{(weeks * 5 + _WEEKDAY_REMAINDER[days - weeks * 7] + 6) // 4}"


def woodies(chart: list[dict], daily: list[dict]) -> dict | None:
    """buildWoodiesPivots on the newest candle, default 4 DAYS / show only today."""
    periods: dict[str, dict] = {}
    for bar in dedupe_daily(daily):
        day = daily_trading_day(bar["time"])
        if not day:
            continue
        key = _woodies_key(day)
        held = periods.get(key)
        if held is None:
            periods[key] = {"open": bar["open"], "high": bar["high"], "low": bar["low"]}
        else:
            held["high"] = max(held["high"], bar["high"])
            held["low"] = min(held["low"], bar["low"])
    if len(periods) < 2 or not chart:
        return None
    order = list(periods)
    latest_key = order[-1]
    day = daily_trading_day(chart[-1]["time"])
    if not day or _woodies_key(day) != latest_key:
        return None
    previous = periods[order[-2]]
    high, low = previous["high"], previous["low"]
    pivot = (high + low + 2 * periods[latest_key]["open"]) / 4
    r1 = 2 * pivot - low
    s1 = 2 * pivot - high
    r2 = pivot + r1 - s1
    s2 = pivot + s1 - r1
    return {"R3": r2 + r1 - pivot, "R2": r2, "R1": r1, "PP": pivot, "S1": s1, "S2": s2, "S3": s2 + s1 - pivot}


_MTF_DEFINITIONS = (("ema1", "EMA", 9), ("ema2", "EMA", 21), ("ema3", "EMA", 50),
                    ("sma4", "SMA", 200), ("sma3", "SMA", 50), ("ema4", "EMA", 200))
_MTF_TIMEFRAMES = (("DAY", "D"), ("WEEK", "Wk"), ("MONTH", "Mo"))
_MTF_MIN_SAMPLES = 30


def mtf_ma_levels(chart: list[dict], daily: list[dict]) -> list[dict]:
    """buildMtfMaLevelsStudy with the chart's default options (within 10% of price)."""
    if not chart or len(daily) < _MTF_MIN_SAMPLES:
        return []
    latest_close = chart[-1]["close"] or 0
    if not math.isfinite(latest_close) or latest_close <= 0:
        return []

    def date_for(bar: dict) -> str:
        value = bar.get("date")
        return value if isinstance(value, str) and _ISO_DATE.match(value) else et_date(bar["time"])

    closes_by_date: dict[str, float] = {}
    for bar in daily:
        close = bar["close"] or 0
        if math.isfinite(close) and close > 0:
            closes_by_date[date_for(bar)] = close
    closes_by_date[date_for(chart[-1])] = latest_close
    ordered = sorted(closes_by_date)

    def closes_for(timeframe: str) -> list[float]:
        if timeframe == "DAY":
            return [closes_by_date[day] for day in ordered]
        grouped: dict[str, float] = {}
        for day in ordered:
            grouped[_week_key(day) if timeframe == "WEEK" else day[:7]] = closes_by_date[day]
        return list(grouped.values())

    def average(values: list[float], kind: str, period: int) -> float | None:
        if not values or (kind == "SMA" and len(values) < period):
            return None
        if kind == "EMA":
            if len(values) < min(period, _MTF_MIN_SAMPLES):
                return None
            multiplier = 2 / (period + 1)
            ema = values[0] or 0
            for value in values:
                ema = (value or 0) * multiplier + ema * (1 - multiplier)
            return ema
        total = 0.0
        for value in values[-period:]:
            total += value or 0
        return total / period

    proximity = DEFAULTS["mtfMaProximityPct"]
    result: list[dict] = []
    for timeframe, suffix in _MTF_TIMEFRAMES:
        closes = closes_for(timeframe)
        for key, kind, period in _MTF_DEFINITIONS:
            value = average(closes, kind, period)
            if value is None or not math.isfinite(value) or value <= 0:
                continue
            if latest_close < value * (1 - proximity / 100) or latest_close > value * (1 + proximity / 100):
                continue
            result.append({"key": f"{timeframe}-{key}", "value": to_fixed4(value),
                           "label": f"{period}{'e' if kind == 'EMA' else 's'}{suffix}"})
    return result


_FAMILY_RANK = {"4x8": 0, "9x20": 1}
_HIGHER_RANK = {"15": 0, "30": 1, "1H": 2, "2H": 3, "4H": 4}
_GANESH_FAMILY_RANK = {"ganesh48": 0, "ganesh920": 1, "ganeshMacd": 2}
_GANESH_TIMEFRAME_RANK = {key: index for index, key in enumerate(("D", "2D", "3D", "4D", "W", "M"))}
_SIGNAL_RTH_START = 300    # signalRthStartTime 500 -> 05:00 ET
_SIGNAL_RTH_END = 960      # signalRthEndTime 1600 -> 16:00 ET


def _snap(signals: list, times: list[int]) -> list:
    """snapTosMtfSignalsToBars: pin to the last candle at or before the stamp."""
    if not signals or not times:
        return signals
    snapped = []
    for signal in signals:
        stamp = _number(signal.get("time"))
        if not math.isfinite(stamp) or stamp < times[0]:
            snapped.append(signal)
            continue
        low, high = 0, len(times) - 1
        while low < high:
            middle = (low + high + 1) >> 1
            if times[middle] <= stamp:
                low = middle
            else:
                high = middle - 1
        snapped.append(signal if times[low] == stamp else {**signal, "time": times[low]})
    return snapped


def _signal_row(signal: dict, stamp: int, source_time: int) -> dict:
    return {"time": stamp, "sourceTime": source_time, "family": signal.get("family"),
            "timeframe": signal.get("timeframe"), "label": signal.get("label"),
            "direction": "PUT" if signal.get("direction") == "PUT" else "CALL",
            "compact": signal.get("compact") is True,
            "forming": bool(signal.get("liveForming") or signal.get("isLiveSignal"))}


def mtf_chart_signals(payload: dict, bars: list[dict], minutes: int) -> list[dict]:
    """The chartSignals filter in App.jsx with default options: intraday CALL/PUT bubbles."""
    by_timeframe = payload.get("mtfSignalsByTimeframe")
    by_timeframe = by_timeframe if isinstance(by_timeframe, dict) else {}
    every = payload.get("mtfSignals") if isinstance(payload.get("mtfSignals"), list) else []
    if minutes == 5:
        source = every
    elif minutes > 240:
        source = []
    else:
        held = by_timeframe.get(str(minutes))
        source = held if isinstance(held, list) else every
    source = [signal for signal in source if isinstance(signal, dict)]
    times = [bar["time"] for bar in bars]
    chart_times = set(times)
    latest_session = ""
    for bar in reversed(bars):   # the newest 05:00-16:00 session on the chart
        if _SIGNAL_RTH_START <= _et_minutes(bar["time"]) < _SIGNAL_RTH_END:
            latest_session = et_date(bar["time"] - _SIGNAL_RTH_START * 60)
            break
    rows: list[tuple[dict, dict]] = []
    seen: set[tuple] = set()
    for original, signal in zip(source, _snap(source, times)):
        stamp = _number(signal.get("time"))
        if not math.isfinite(stamp) or stamp <= 0:
            continue
        stamp = int(stamp)
        if latest_session and stamp - _SIGNAL_RTH_START * 60 > 0 and et_date(stamp - _SIGNAL_RTH_START * 60) != latest_session:
            continue
        containing = bucket_time(math.floor(stamp), max(minutes, 1))
        if containing not in chart_times:
            continue
        mark = (containing, signal.get("family"), signal.get("label"))
        if mark in seen:
            continue
        seen.add(mark)
        rows.append((signal, _signal_row(signal, containing, int(_number(original.get("time"))))))
    # layoutTosMtfChartSignals sorts the pre-pin stamps' pinned result.
    rows.sort(key=lambda pair: (pair[1]["time"], _FAMILY_RANK.get(str(pair[0].get("family") or ""), 99),
                                _HIGHER_RANK.get(str(pair[0].get("timeframe") or "").upper(), 99)))
    return [row for _, row in rows]


def ganesh_chart_signals(payload: dict, bars: list[dict], minutes: int) -> list[dict]:
    """projectGaneshSignalsToChart over the server's windowed D..M signals."""
    tape = payload.get("ganeshHigherTimeframeSignals")
    signals = tape.get("signals") if isinstance(tape, dict) else None
    if not isinstance(signals, list) or not bars:
        return []
    live = payload.get("bars")
    try:
        first_live = int((live[0] or {}).get("time") or 0) if isinstance(live, list) and live else 0
    except (AttributeError, TypeError, ValueError):
        first_live = 0
    if first_live > 0:   # _windowed_ganesh_chart_payload
        signals = [row for row in signals if isinstance(row, dict) and int(row.get("time") or 0) >= first_live]
    index_by_time = {bar["time"]: position for position, bar in enumerate(bars)}
    times = [bar["time"] for bar in bars]
    rows: list[dict] = []
    seen: set[str] = set()
    for signal in signals:
        if not isinstance(signal, dict):
            continue
        source_time = _number(signal.get("time") or 0)
        if not math.isfinite(source_time) or source_time <= 0:
            continue
        source_time = int(source_time)
        position = index_by_time.get(bucket_time(source_time, max(minutes, 1)))
        if position is None:   # snap forward to the first candle at/after the stamp
            low, high, snapped = 0, len(times) - 1, -1
            while low <= high:
                middle = (low + high) >> 1
                if times[middle] >= source_time:
                    snapped, high = middle, middle - 1
                else:
                    low = middle + 1
            if snapped < 0:
                continue
            position = snapped
        stamp = times[position]
        direction = "PUT" if signal.get("direction") == "PUT" else "CALL"
        key = f"{signal.get('family')}-{signal.get('timeframe')}-{direction}-{stamp}"
        if key in seen:
            continue
        seen.add(key)
        rows.append({**_signal_row(signal, stamp, source_time), "key": key})
    rows.sort(key=lambda row: (row["time"], _GANESH_FAMILY_RANK.get(row["family"], 99),
                               _GANESH_TIMEFRAME_RANK.get(row["timeframe"], 99), row["direction"]))
    return rows


def signals_pending(payload: dict) -> dict:
    """What the chart is still working out. A pending list may be incomplete."""
    tape = payload.get("ganeshHigherTimeframeSignals")
    history_ready = tape.get("historyReady") if isinstance(tape, dict) else None
    return {"mtfSignalsPending": bool(payload.get("mtfSignalsPending")),
            "studiesPending": bool(payload.get("studiesPending")),
            "historyReady": history_ready is True}


#: api_server's OI_CHART_MTF_ENGINE_MODE and scanner's TOS_CHART_SESSION_DAYS; a parity test watches them.
MTF_ENGINE_MODE = "tos_repaint_secondary_5m"
MTF_SESSION_DAYS = 5
STUDY_TAPE_MINUTES = 30


def _labels_current(payload: dict) -> bool:
    """_chart_payload_has_current_mtf_labels: a saved copy from an older engine shows as pending."""
    if not (str(payload.get("mtfSignalMode") or "") == MTF_ENGINE_MODE and not payload.get("mtfSignalsPending")
            and isinstance(payload.get("mtfSignalsByTimeframe"), dict)):
        return False
    try:
        if int(payload.get("mtfSourceSessions") or 0) >= MTF_SESSION_DAYS:
            return True
    except (TypeError, ValueError):
        pass
    fine = payload.get("fineStudyBars")
    if not isinstance(fine, list) or not fine:
        return True
    try:
        sessions = {et_date(int(bar.get("time") or 0) + 4 * 3600) for bar in fine if int(bar.get("time") or 0) > 0}
    except (AttributeError, TypeError, ValueError, OverflowError, OSError):
        return True
    return len(sessions) < MTF_SESSION_DAYS


def fold_study_tape(rows) -> list:
    """chart_aggregation.normalize_study_tape: a too-fine saved study tape folds to 30 minutes on read."""
    clean = normalize(rows)
    if len(clean) < 3:
        return clean
    counts: dict[int, int] = {}
    for index in range(max(1, len(clean) - 240), len(clean)):
        gap = clean[index]["time"] - clean[index - 1]["time"]
        if gap > 0:
            counts[gap] = counts.get(gap, 0) + 1
    best_gap = best_count = 0
    for gap, count in counts.items():
        if count > best_count:
            best_gap, best_count = gap, count
    spacing = max(1, int(best_gap / 60 + 0.5)) if best_gap > 0 else 0
    if spacing >= STUDY_TAPE_MINUTES:
        return clean
    folded = aggregate(clean, STUDY_TAPE_MINUTES)
    return folded if folded else clean


def analyze(payload: dict, timeframe: str, from_disk: bool = False) -> dict | None:
    """Levels and signals for ONE timeframe of one tape. None when the tape has no candles."""
    minutes = TIMEFRAMES.get(timeframe)
    if minutes is None or not isinstance(payload, dict):
        return None
    # Every intraday source is cleaned of off-market extended-hours bad prints before a candle is built, exactly as
    # buildChartDisplayBars does (chartBadPrints.js, d0db99ff). The daily seed row of a day the rule repaired is put
    # back to the cleaned minutes, as the chart panel does (withoutDailySeedBadPrints, 2026-11-13).
    repair = _bad_print_filter()
    raw_live = normalize(payload.get("bars"))
    live = repair(raw_live)
    _pause()
    daily = reconcile_daily_seed(dedupe_daily(normalize(payload.get("dailyBars"))), raw_live, live)
    _pause()
    # A tape this timeframe never reads is not worth normalizing.
    study_source = [] if minutes >= 1440 and daily else payload.get("studyBars")
    study = repair(fold_study_tape(study_source) if from_disk else normalize(study_source))
    _pause()
    uses_study = bool(study) and (minutes >= 240 or 0 < spacing_minutes(study) <= minutes)
    fine = [] if uses_study or (minutes >= 1440 and daily) else repair(normalize(payload.get("fineStudyBars")))
    _pause()
    bars = display_bars(study, fine, live, daily, minutes)
    _pause()
    if not bars:
        return None
    last = bars[-1]
    latest_date = et_date(last["time"])
    days = merged_days(daily, bars)
    _pause()
    levels = {
        "timeframe": timeframe, "minutes": minutes, "n": len(bars), "first": bars[0]["time"],
        "lastTime": last["time"], "lastClose": last["close"], "latestDate": latest_date,
        "ema9": ema_last(bars, 9), "ema21": ema_last(bars, 21), "ema50": ema_last(bars, 50),
        "sma200": sma_last(bars, 200), "vwap": vwap_last(bars),
        "session": session_levels(bars, daily),
        "previous": previous_ohlc(days, latest_date),
        "pivots": {period: standard_pivots(pivot_source_period(days, latest_date, period)) for period in PERIODS},
        "woodies": woodies(bars, daily) if minutes == 1440 else None,
        "mtfMa": mtf_ma_levels(bars, daily),
    }
    _pause()
    if from_disk and not _labels_current(payload):   # the server hides these until it recomputes them
        payload = {**payload, "mtfSignals": [], "mtfSignalsByTimeframe": {}, "mtfSignalsPending": True}
    signals = {"mtf": mtf_chart_signals(payload, bars, minutes),
               "higher": ganesh_chart_signals(payload, bars, minutes),
               "pending": signals_pending(payload)}
    levels["emaDetail"] = ema_detail(bars)
    tape = {"newestData": live[-1]["time"] if live else last["time"],
            "historyLoading": bool(payload.get("historyLoading"))}
    return {"levels": levels, "signals": signals, "defaults": DEFAULTS, "tape": tape}


#: The timeframes of the chat's 21/50 EMA answer, in order.
EMA_TIMEFRAMES = ("5m", "15m", "1h", "4h", "D", "W", "M")


def ema_report(payload: dict, timeframes: tuple | list = EMA_TIMEFRAMES, from_disk: bool = False) -> dict | None:
    """The 21/50 EMA detail (plus EMA 9 and SMA 200) on several timeframes of one tape, reading the tape once.
    Each timeframe's bars are built exactly as analyze() builds them."""
    if not isinstance(payload, dict):
        return None
    repair = _bad_print_filter()                        # the chart's bad-print rule, as in analyze()
    raw_live = normalize(payload.get("bars"))
    live = repair(raw_live)
    _pause()
    daily = reconcile_daily_seed(dedupe_daily(normalize(payload.get("dailyBars"))), raw_live, live)   # as in analyze()
    _pause()
    study = fine = None
    out: dict = {}
    for timeframe in timeframes:
        minutes = TIMEFRAMES.get(timeframe)
        if minutes is None:
            continue
        if minutes >= 1440 and daily:
            study_tf: list = []
        else:
            if study is None:
                study = repair(fold_study_tape(payload.get("studyBars")) if from_disk else normalize(payload.get("studyBars")))
            study_tf = study
        uses_study = bool(study_tf) and (minutes >= 240 or 0 < spacing_minutes(study_tf) <= minutes)
        if uses_study or (minutes >= 1440 and daily):
            fine_tf: list = []
        else:
            if fine is None:
                fine = repair(normalize(payload.get("fineStudyBars")))
            fine_tf = fine
        bars = display_bars(study_tf, fine_tf, live, daily, minutes)
        _pause()
        detail = ema_detail(bars) if bars else None
        if detail:
            detail.update(ema9=ema_last(bars, 9), sma200=sma_last(bars, 200), timeframe=timeframe)
            out[timeframe] = detail
    days = daily[-260:]
    weekly: list[dict] = []
    for bar in days:
        key = _week_key(et_date(bar["time"]))
        if weekly and weekly[-1]["key"] == key:
            week = weekly[-1]
            week.update(high=max(week["high"], bar["high"]), low=min(week["low"], bar["low"]), close=bar["close"],
                        volume=(week["volume"] or 0) + (bar["volume"] or 0))
        else:
            weekly.append({"key": key, "time": bar["time"], "open": bar["open"], "high": bar["high"], "low": bar["low"],
                           "close": bar["close"], "volume": bar["volume"] or 0})
    plain = ("time", "open", "high", "low", "close", "volume")
    return {"ema": out, "newestData": live[-1]["time"] if live else None, "defaults": DEFAULTS,
            "daily": [{k: bar[k] for k in plain} for bar in daily[-60:]], "weekly": [{k: week[k] for k in plain} for week in weekly[-52:]],
            "dailyVolume20": (sum((bar["volume"] or 0) for bar in daily[-21:-1]) / 20) if len(daily) >= 21 else None}


#: The regular session: a bar that starts at 09:30 ET or later and before 16:00 ET.
REGULAR_OPEN_MINUTE, REGULAR_CLOSE_MINUTE = 9 * 60 + 30, 16 * 60
#: A day's last regular bar must start this late to be its close (15:55 is the last 5-minute bar).
CLOSE_BAR_MINUTE = 15 * 60 + 55
#: NYSE early closes (13:00 ET), 2026 and 2027, from the exchange's published holiday calendar: the day after
#: Thanksgiving and Christmas Eve. 2027-12-24 is the observed Christmas holiday (closed), so it is not here. Used by
#: the chat's session split only (session_closes, _extended, chat_facts.market_open); the chart's candles and its
#: bad-print rule keep 09:30-16:00, so chart parity is untouched.
EARLY_CLOSE_DAYS = frozenset({"2026-11-27", "2026-12-24", "2027-11-26"})
EARLY_CLOSE_MINUTE = 13 * 60
#: NYSE full-day holidays (closed), 2026 and 2027, from the exchange's published calendar. Used by the chat's clock
#: only (chat_facts.market_open / trading_day): on Thanksgiving at 10:00 ET "why is spy down" must explain the last
#: session, not an empty "today" (2026-11-14 review). The chart's candles are untouched.
MARKET_HOLIDAYS = frozenset({
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25", "2026-06-19", "2026-07-03", "2026-09-07",
    "2026-11-26", "2026-12-25",
    "2027-01-01", "2027-01-18", "2027-02-15", "2027-03-26", "2027-05-31", "2027-06-18", "2027-07-05", "2027-09-06",
    "2027-11-25", "2027-12-24"})


def close_minute(day: str) -> int:
    """The ET minute the regular session of `day` (YYYY-MM-DD) ends: 13:00 on an early-close day, else 16:00."""
    return EARLY_CLOSE_MINUTE if day in EARLY_CLOSE_DAYS else REGULAR_CLOSE_MINUTE


def session_closes(payload: dict) -> dict:
    """Each weekday's regular-session close from the intraday tapes (2026-11-11 review: the saved daily bars run
    midnight to midnight, so their close is the overnight print, not 16:00). Minute bars win over 5-minute bars on a
    day both hold. {"sessions": [{"day", "close", "time", "complete"}, ...] oldest first, "oldest": first day held}.
    A day appears only when it has a bar inside 09:30-16:00 ET; `complete` is False when its last regular bar starts
    before 15:55 (the tape stops early). A later overnight print is never used."""
    if not isinstance(payload, dict):
        return {"sessions": [], "oldest": None, "extended": []}
    days: dict[str, dict] = {}
    minute_bars: list[dict] = []
    for key in ("fineStudyBars", "bars"):              # minute bars last, so they replace the 5-minute day
        found: dict[str, dict] = {}
        bars = normalize(payload.get(key))
        if key == "bars" or not minute_bars:
            minute_bars = bars or minute_bars
        for bar in bars:
            day, weekday, hour, _ = _et_hour_parts(bar["time"])
            minute = hour * 60 + (bar["time"] % 3600) // 60
            if weekday >= 5 or not REGULAR_OPEN_MINUTE <= minute < close_minute(day):
                continue
            found[day] = {"close": bar["close"], "time": bar["time"], "minute": minute}
        _pause()
        for day, last in found.items():
            held = days.get(day)
            closing = close_minute(day) - 5
            if held is None or last["minute"] >= closing or held["minute"] < closing:
                days[day] = last
    sessions = [{"day": day, "close": days[day]["close"], "time": days[day]["time"],
                 "complete": days[day]["minute"] >= close_minute(day) - 5} for day in sorted(days)]
    return {"sessions": sessions, "oldest": sessions[0]["day"] if sessions else None, "extended": _extended(minute_bars)}


#: Extended-hours windows, ET minutes of the day: premarket 04:00-09:30, after hours 16:00-20:00, overnight 20:00-04:00.
PREMARKET_OPEN_MINUTE, AFTER_HOURS_END_MINUTE = 4 * 60, 20 * 60
EXTENDED_DAYS = 10
#: Bars on each side of a window's last bar the bad-print rule is run over (it reads ten each side, five to confirm).
BAD_PRINT_REACH = 25
_HOUR_PARTS: dict[int, tuple] = {}


def _et_hour_parts(timestamp: int) -> tuple:
    """(ET date, weekday, ET hour, the day before's date) for the UTC hour holding `timestamp`. The Eastern offset is
    whole hours, so one datetime per hour serves every minute in it (a datetime per bar cost most of the walk)."""
    key = int(timestamp) // 3600
    held = _HOUR_PARTS.get(key)
    if held is None:
        if len(_HOUR_PARTS) >= _CACHE_LIMIT:
            _HOUR_PARTS.clear()
        moment = datetime.fromtimestamp(key * 3600, ET)
        held = (moment.date().isoformat(), moment.weekday(), moment.hour, (moment.date() - timedelta(days=1)).isoformat())
        _HOUR_PARTS[key] = held
    return held


#: The chart's extended-hours bad-print rule (chart_bad_prints.py / frontend/src/chartBadPrints.js, d0db99ff), ported
#: here because this module runs in a child with no app code on its path. Same numbers, same steps; the why-answer
#: tests compare it with chart_bad_prints on every bar of a tape (tests/test_momx_chat_why_sessions.py).
_BAD_NEIGHBOUR_WINDOW, _BAD_CONFIRM_WINDOW, _BAD_MIN_FRACTION, _BAD_MIN_DOLLARS, _BAD_JUMPINESS = 10, 5, 0.01, 0.05, 25
_BAD_FIELDS = ("open", "high", "low", "close")


def _bad_candidate(bars: list[dict], index: int) -> bool:
    neighbour = bars[index - 1] if index > 0 else (bars[index + 1] if index + 1 < len(bars) else None)
    anchor = float(neighbour["close"]) if neighbour else 0.0
    if not anchor > 0:
        return False
    limit = max(anchor * _BAD_MIN_FRACTION, _BAD_MIN_DOLLARS)
    return any(abs(float(bars[index][field]) - anchor) > limit for field in _BAD_FIELDS)


def _bad_fields_at(bars: list[dict], index: int, confirm_after: bool = True):
    """The chart's verdict for one bar: (bad fields, reference) or None. confirm_after=False (the chat's session price
    only, never the candles): a bad close is flagged even with fewer than five bars after it."""
    count = len(bars)
    start, stop = max(0, index - _BAD_NEIGHBOUR_WINDOW), min(count, index + _BAD_NEIGHBOUR_WINDOW + 1)
    closes = [float(bars[cursor]["close"]) for cursor in range(start, stop) if cursor != index]
    if len(closes) < 4:
        return None
    reference = statistics.median(closes)
    if not reference > 0:
        return None
    moves = [abs(float(bars[cursor]["close"]) - float(bars[cursor - 1]["close"])) / reference
             for cursor in range(max(start, 1), stop) if cursor != index and cursor - 1 != index]
    jumpiness = statistics.median(moves) if moves else 0.0
    tolerance = max(reference * max(_BAD_MIN_FRACTION, _BAD_JUMPINESS * jumpiness), _BAD_MIN_DOLLARS)
    nearby = [float(bars[cursor][field]) for cursor in range(max(0, index - _BAD_CONFIRM_WINDOW), min(count, index + _BAD_CONFIRM_WINDOW + 1))
              if cursor != index for field in _BAD_FIELDS]
    bar = bars[index]
    bad = [field for field in _BAD_FIELDS if abs(float(bar[field]) - reference) > tolerance
           and not any(abs(other - float(bar[field])) <= tolerance / 2 for other in nearby)]
    if not bad or (confirm_after and count - 1 - index < _BAD_CONFIRM_WINDOW and "close" in bad):
        return None
    return bad, reference


def _repaired_bar(bars: list[dict], index: int, bad: list, reference: float) -> dict:
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
    return {**bar, "open": open_, "high": max(high, open_, close), "low": min(low, open_, close), "close": close, "badPrintRemoved": True}


def remove_extended_hours_bad_prints(bars: list[dict]) -> list[dict]:
    """chart_bad_prints.remove_extended_hours_bad_prints: the bars with off-market extended-hours prints repaired
    (the same list when nothing was removed)."""
    if not isinstance(bars, list) or len(bars) < 5:
        return bars
    repaired = None
    for index in range(len(bars)):
        if not _bad_candidate(bars, index):
            continue
        _, _, hour, _ = _et_hour_parts(bars[index]["time"])
        minute = hour * 60 + (int(bars[index]["time"]) % 3600) // 60
        if REGULAR_OPEN_MINUTE <= minute < REGULAR_CLOSE_MINUTE:
            continue
        verdict = _bad_fields_at(bars, index)
        if not verdict:
            continue
        if repaired is None:
            repaired = list(bars)
        repaired[index] = _repaired_bar(bars, index, *verdict)
    return repaired if repaired is not None else bars


def _bad_print_filter():
    """The bad-print rule above (kept as a function so callers read it the same way)."""
    return remove_extended_hours_bad_prints


def reconcile_daily_seed(daily: list[dict], raw: list[dict], cleaned: list[dict]) -> list[dict]:
    """reconcileDailySeedWithCleanedTape (chartBadPrints.js, 2026-11-13): a daily seed row built from the unfiltered
    minutes (SPY 2026-11-11 kept low 711.4601 from the $711.46 prints) has the high/low that sits on a print the minute
    rule removed put back to the cleaned minutes' range for that day, never past the row's own open/close. Only days
    the minute tape holds whole (it starts on an earlier day). `cleaned` is remove_extended_hours_bad_prints(raw); the
    same list comes back when nothing changed. A real after-hours move is never a removed print, so it always counts."""
    if not daily or not raw or cleaned is raw or len(cleaned) != len(raw):
        return daily
    removed: dict[str, tuple[list, list]] = {}
    for before, after in zip(raw, cleaned):
        if before is after:
            continue
        lows, highs = removed.setdefault(et_date(before["time"]), ([], []))
        if before["low"] < after["low"]:
            lows.append(before["low"])
        if before["high"] > after["high"]:
            highs.append(before["high"])
    if not removed:
        return daily
    first_day = et_date(cleaned[0]["time"])
    ranges: dict[str, list] = {}
    for bar in cleaned:
        day = et_date(bar["time"])
        if day <= first_day or day not in removed:
            continue
        held = ranges.get(day)
        if held is None:
            ranges[day] = [bar["high"], bar["low"]]
        else:
            if bar["high"] > held[0]:
                held[0] = bar["high"]
            if bar["low"] < held[1]:
                held[1] = bar["low"]
    out = None
    for index, row in enumerate(daily):
        day = et_date(row["time"])
        clean = ranges.get(day)
        if clean is None:
            continue
        lows, highs = removed[day]
        low, high = row["low"], row["high"]
        if low < clean[1] and any(abs(low - bad) <= _BAD_MIN_DOLLARS for bad in lows):
            low = min(clean[1], row["open"], row["close"])
        if high > clean[0] and any(abs(high - bad) <= _BAD_MIN_DOLLARS for bad in highs):
            high = max(clean[0], row["open"], row["close"])
        if low == row["low"] and high == row["high"]:
            continue
        if out is None:
            out = list(daily)
        out[index] = {**row, "high": high, "low": low, "badPrintRemoved": True}
    return out if out is not None else daily


def extended_prints(rows) -> list[dict]:
    """The last print of each extended-hours window, newest EXTENDED_DAYS days: [{"window", "anchor", "time", "price"}]
    oldest first. window: "pre" (04:00-09:30), "after" (16:00-20:00) or "overnight" (20:00-04:00, anchored to the
    evening it started). Off-market bad prints are repaired first, as the chart does before it draws candles (the port above),
    so a $711 print on a $760 SPY is never the after-hours price."""
    if isinstance(rows, (list, tuple)):
        rows = rows[-(EXTENDED_DAYS + 1) * 1440:]                # a minute tape is oldest first: the newest days only
    return _extended(normalize(rows))


def _extended(bars: list[dict]) -> list[dict]:
    """extended_prints over bars already normalized (oldest first). The bad-print rule reads at most ten bars on either
    side (and whether five follow), so it is run on BAD_PRINT_REACH bars around each window's last bar only: the same
    verdict as the whole tape, for a fraction of the walk."""
    if not bars:
        return []
    cutoff = bars[-1]["time"] - (EXTENDED_DAYS + 1) * 86400
    start = next((index for index, bar in enumerate(bars) if bar["time"] >= cutoff), len(bars))
    last: dict[tuple, int] = {}
    keys: dict[int, tuple] = {}
    for index in range(start, len(bars)):
        key = _window_key(bars[index]["time"])
        if key is None:
            continue                                            # the regular session, or a weekend bar inside its hours
        last[key] = index
        keys[index] = key
    repair = _bad_print_filter()
    out = []
    for key, index in last.items():
        low = max(0, index - BAD_PRINT_REACH)
        piece = repair(bars[low:index + BAD_PRINT_REACH + 1])
        at = index - low
        if len(piece) - 1 - at < _BAD_CONFIRM_WINDOW:
            # The tape's last bars: the chart's rule waits for five bars after a bad close before it repairs it, so a
            # bad print as the newest bar would be the session price (SPY flat 756 after hours, a 711.00 print at 19:57
            # as the last bar: -6.52%). For the session price only, the same rule without the five-bars-after wait;
            # a flagged bar gives way to the newest earlier unflagged bar of the same window. The candles are untouched.
            while at > 0 and _bad_close_at_end(piece, at) and keys.get(low + at - 1) == key:
                at -= 1
            if _bad_close_at_end(piece, at):
                continue                                        # no clean bar left in the window: no price for it
        bar = piece[at]
        out.append({"window": key[0], "anchor": key[1], "time": bar["time"], "price": bar["close"]})
    _pause()
    return sorted(out, key=lambda item: item["time"])


def _window_key(stamp: int) -> tuple | None:
    """("pre" | "after" | "overnight", the ET day it is anchored to) for an extended-hours bar, None for the regular
    session (09:30 to 16:00 ET, or to 13:00 on an early-close day; a weekend bar inside those hours too)."""
    day, _, hour, day_before = _et_hour_parts(stamp)
    minute = hour * 60 + (stamp % 3600) // 60
    if REGULAR_OPEN_MINUTE <= minute < close_minute(day):
        return None
    if PREMARKET_OPEN_MINUTE <= minute < REGULAR_OPEN_MINUTE:
        return ("pre", day)
    if minute < PREMARKET_OPEN_MINUTE:
        return ("overnight", day_before)
    if minute < AFTER_HOURS_END_MINUTE:
        return ("after", day)
    return ("overnight", day)


def _bad_close_at_end(bars: list[dict], index: int) -> bool:
    """The chart's bad-print verdict on one bar's close, without waiting for five bars after it (session price only)."""
    if not _bad_candidate(bars, index):
        return False
    verdict = _bad_fields_at(bars, index, confirm_after=False)
    return bool(verdict) and "close" in verdict[0]


#: The why-answer's window ask, as the child's "timeframe": window:<YYYY-MM-DD>:<start minute>:<end minute> (ET minutes).
_WINDOW_ASK = re.compile(r"^window:(\d{4}-\d{2}-\d{2}):(\d{1,4}):(\d{1,4})$")


def _pct(price: float, base: float | None) -> float | None:
    return round((price / base - 1) * 100, 2) if base and base > 0 else None


def window_move(payload: dict, day: str, start_min: int, end_min: int) -> dict:
    """One symbol's move inside part of one regular session (2026-11-14, "why spy went down this morning" answered with
    the whole day and after hours): the bars of `day` that start at ET minute start_min or later and at end_min or
    earlier, inside 09:30 to the close (13:00 on an early-close day). Minute bars win; a day the minute bars do not hold
    is read from the 5-minute bars. The regular session is not touched by the chart's bad-print rule (it repairs only
    extended hours), so it is read as the chart draws it, the same as session_closes. The previous close is the newest
    earlier weekday whose last regular bar starts at 15:55 or later (session_closes' "complete" rule), read in the same walk.
    {"day", "start", "end", "prevClose", "prevDay", "open", "openAt", "startPrice", "startAt", "price", "at", "pctClose",
     "pctOpen", "pctStart", "low", "lowAt", "high", "highAt", "covers"}; times are epoch seconds. "covers" is False
    when the tape stops before the window's end. {"day", "start", "end", "missing": why} when nothing is held."""
    out: dict = {"day": day, "start": int(start_min), "end": int(end_min)}
    if not isinstance(payload, dict) or not _ISO_DATE.match(str(day or "")):
        return {**out, "missing": "no tape"}
    closing = close_minute(day)
    start, end = max(int(start_min), REGULAR_OPEN_MINUTE), min(int(end_min), closing - 1)
    held, prev = None, None
    for key in ("bars", "fineStudyBars"):
        found, later = [], False
        last: dict[str, tuple] = {}                     # each earlier weekday's last regular bar: (minute, bar)
        for bar in normalize(payload.get(key)):
            bar_day, weekday, hour, _ = _et_hour_parts(bar["time"])
            if weekday >= 5 or bar_day > day:
                continue
            minute = hour * 60 + (bar["time"] % 3600) // 60
            if not REGULAR_OPEN_MINUTE <= minute < close_minute(bar_day):
                continue
            if bar_day < day:
                last[bar_day] = (minute, bar)
                continue
            found.append((minute, bar))
            later = later or minute > end
        _pause()
        if prev is None:
            complete = [d for d, (minute, _) in last.items() if minute >= close_minute(d) - 5]
            if complete:
                newest = max(complete)
                prev = (newest, float(last[newest][1]["close"]))
        if held is None and found:
            held = (found, later)
        if held is not None and prev is not None:
            break
    if held is None:
        return {**out, "missing": f"no regular-session bars on {day}"}
    found, later = held
    inside = [(minute, bar) for minute, bar in found if start <= minute <= end]
    if not inside:
        return {**out, "missing": f"no bars between those times on {day}"}
    first, last_bar = found[0][1], inside[-1][1]
    low = min(inside, key=lambda item: item[1]["low"])[1]
    high = max(inside, key=lambda item: item[1]["high"])[1]
    price = float(last_bar["close"])
    prev_close = prev[1] if prev else None
    out.update({"prevClose": prev_close, "prevDay": prev[0] if prev else None,
                "open": float(first["open"]), "openAt": first["time"], "startPrice": float(inside[0][1]["open"]),
                "startAt": inside[0][1]["time"], "price": price, "at": last_bar["time"],
                "pctClose": _pct(price, prev_close), "pctOpen": _pct(price, float(first["open"])),
                "pctStart": _pct(price, float(inside[0][1]["open"])), "low": float(low["low"]), "lowAt": low["time"],
                "high": float(high["high"]), "highAt": high["time"],
                "covers": later or inside[-1][0] >= end - 5})
    return out


def child_answer(payload: dict, timeframe: str, from_disk: bool = False) -> dict | None:
    """What the child process answers for one tape and one ask."""
    if timeframe == "sessions":
        return session_closes(payload)
    asked = _WINDOW_ASK.match(timeframe)
    if asked:
        return window_move(payload, asked.group(1), int(asked.group(2)), int(asked.group(3)))
    if timeframe.startswith("ema:"):
        return ema_report(payload, [t for t in timeframe[4:].split(",") if t], from_disk=from_disk)
    return analyze(payload, timeframe, from_disk=from_disk)


def _main(argv: list[str]) -> int:
    """Child process: `chart_levels.py <timeframe> <file.json.gz>`, or `-` for a tape on stdin.

    The chart maths is CPU work and api_server shares one interpreter lock with
    the live app, so the chat runs it here, in its own process, and reads one
    small JSON answer back.
    """
    if len(argv) != 2:
        return 2
    timeframe, source = argv
    if _WINDOW_ASK.match(timeframe) and "|" in source:
        # Several saved charts for one window (the why-answer's drivers): {path: answer}, one process for all of them.
        out = {}
        for path in [p for p in source.split("|") if p]:
            try:
                with open(path, "rb") as handle:
                    out[path] = child_answer(json.loads(gzip.decompress(handle.read())), timeframe, True)
            except (OSError, ValueError, EOFError):
                out[path] = None
        sys.stdout.write(json.dumps(out))
        return 0
    if source == "-":
        payload, from_disk = json.loads(sys.stdin.buffer.read()), False
    else:
        with open(source, "rb") as handle:
            payload, from_disk = json.loads(gzip.decompress(handle.read())), True
    sys.stdout.write(json.dumps(child_answer(payload, timeframe, from_disk)))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
