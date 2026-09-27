"""Fold bar tapes onto the ThinkOrSwim clock.

Bucket anchoring is the single most expensive parity trap in this codebase, so
this module DELEGATES rather than deciding. Every clock here comes from a
source that has already been matched against the trader's charts:

* Intraday buckets come from ``chart_aggregation`` -- the Python mirror of
  ``frontend/src/chartAggregation.js``, the aggregator the charts themselves
  draw from. 4H follows the TOS equity clock, which aggregates from midnight
  CENTRAL, so the boundaries visible on an Eastern chart are 01:00, 05:00,
  09:00, 13:00, 17:00 and 21:00. 2H is anchored to Eastern midnight
  (00:00/02:00/.../22:00 ET) so 04:00 stays 04:00 across a daylight-saving
  change instead of sliding an hour the way epoch bucketing does.

  *** OPEN PARITY QUESTION -- READ BEFORE TRUSTING 2H ***
  The repo currently holds TWO 2H clocks. ``chart_aggregation.py`` (used here
  by default) anchors 2H at Eastern midnight. ``frontend/src/chartAggregation
  .js`` and ``premarket_scanner.py`` were both moved to the midnight-CENTRAL
  clock (01/03/05/07/09/11... ET) by commit 03ca9a7, whose message records
  that the even-hour buckets "straddled the 4h boundaries and never matched"
  the trader's chart on 2026-08-24. chart_aggregation.py was NOT updated in
  that commit. Until a human resolves which one TOS actually draws, this
  module exposes both: pass ``two_hour_anchor="central-midnight"`` to get the
  clock the browser and the premarket scanner use. 4H is unaffected -- all
  three sources already agree on it.

* ``D``, ``Wk`` and ``M`` group keys come from
  ``ganesh_higher_timeframe_signals._timeframe_group_key``, the validated
  backend replay. That arithmetic is NOT copied here; it is called.

* ``2D``/``3D``/``4D`` do NOT. Measured against the trader's terminal on
  2026-08-27 (see :func:`tos_multiday_group_key`), thinkorswim chunks N
  consecutive WEEKDAYS, not N consecutive calendar days. The engine's
  1969-12-30 CALENDAR phase cannot reproduce the TOS watchlist numbers at
  any phase offset. The engine keys stay reachable as
  ``grouping="ema"``/``"macd"`` so the signal replay is not disturbed.

* ``merge_live_tail`` is re-exported from ``premarket_scanner`` unchanged.

Import cost: ``chart_aggregation`` and ``premarket_scanner`` are stdlib-only;
``ganesh_higher_timeframe_signals`` pulls pandas + config. Nothing here
imports ``api_server``.
"""

from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from collections.abc import Mapping  # abc, not typing: same isinstance answer, far cheaper (speed pass 2026-09-25)
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import chart_aggregation
import ganesh_higher_timeframe_signals as _ghts
import premarket_scanner as _premarket_scanner

# Reused verbatim -- see the module docstring. Appending only strictly-newer
# bars is what stops a one-minute live bar replacing a thirty-minute cached
# bucket and throwing away that bucket's true high and low.
merge_live_tail = _premarket_scanner.merge_live_tail

DEFAULT_TZ = "America/New_York"
_EASTERN = ZoneInfo(DEFAULT_TZ)

#: The intraday spans this module is exercised for.
SUPPORTED_INTRADAY_MINUTES = (15, 30, 60, 120, 240)

#: Daily spans, in the labels the MomoX columns use.
SUPPORTED_DAILY_SPANS = ("D", "2D", "3D", "4D", "Wk", "M")

#: 2H anchoring choices -- see the OPEN PARITY QUESTION in the docstring.
EASTERN_MIDNIGHT_2H = "eastern-midnight"
CENTRAL_MIDNIGHT_2H = "central-midnight"


# ----------------------------------------------------------------------
# Intraday
# ----------------------------------------------------------------------


def intraday_bucket_time(
    timestamp: Any,
    minutes: int,
    *,
    tz: str = DEFAULT_TZ,
    two_hour_anchor: str = EASTERN_MIDNIGHT_2H,
) -> int | None:
    """Bucket start for a bar time on the TOS clock, or None if unusable."""
    try:
        time_value = int(timestamp)
    except (TypeError, ValueError):
        return None
    if time_value <= 0:
        return None
    span = max(int(minutes or 1), 1)
    return _bucket_time_int(time_value, span, tz, two_hour_anchor)


# 2026-09-25 speed pass: a Watchlist build folds ~358 symbols x 6 intraday
# timeframes x ~1,000 bars, and neighbouring symbols share the same bar
# times - the bucket start is a pure function of (time, span, zone, anchor),
# so it is computed once per distinct input. Identical output (pinned by the
# artifacts/_wf_full_parity.py gate).
@lru_cache(maxsize=262_144)
def _bucket_time_int(time_value: int, span: int, tz: str, two_hour_anchor: str) -> int | None:
    if span == 120 and two_hour_anchor == CENTRAL_MIDNIGHT_2H:
        # The browser/premarket-scanner clock: 01/03/05/07/09/11... ET.
        return _premarket_scanner.chart_bucket_time(time_value, 120)
    if tz != DEFAULT_TZ:
        return _foreign_zone_bucket_time(time_value, span, ZoneInfo(tz))
    return chart_aggregation.chart_aggregation_bucket_time(time_value, span)


def _foreign_zone_bucket_time(timestamp: int, span: int, zone: ZoneInfo) -> int | None:
    """The same clock shape as chart_aggregation, anchored in another zone.

    Only reachable when a caller overrides ``tz``; the shipping path is
    Eastern and delegates to the mirror.
    """
    moment = datetime.fromtimestamp(timestamp, zone)
    midnight = int(
        moment.replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    )
    if span == 1440:
        return midnight
    if span == 240:
        anchor = midnight + 3600
        step = 240 * 60
        return anchor + ((timestamp - anchor) // step) * step
    if span == 120:
        step = 120 * 60
        return midnight + ((timestamp - midnight) // step) * step
    step = span * 60
    return (timestamp // step) * step


def aggregate_intraday(
    bars: Iterable[Mapping[str, Any]] | None,
    minutes: int,
    *,
    tz: str = DEFAULT_TZ,
    two_hour_anchor: str = EASTERN_MIDNIGHT_2H,
) -> list[dict]:
    """Fold a finer intraday tape into ``minutes`` buckets on the TOS clock.

    OHLCV is first open, max high, min low, last close, summed volume. Only
    buckets that actually received a source bar are emitted -- a market hole
    leaves a gap rather than an invented flat candle. A trailing PARTIAL
    bucket IS emitted; use :func:`last_closed_bucket` to exclude it.
    """
    folded: dict[int, dict] = {}
    for bar in chart_aggregation.normalize_chart_candle_bars(bars):
        bucket_time = intraday_bucket_time(
            bar["time"], minutes, tz=tz, two_hour_anchor=two_hour_anchor,
        )
        if not bucket_time or bucket_time <= 0:
            continue
        current = folded.get(bucket_time)
        if current is None:
            folded[bucket_time] = {**bar, "time": bucket_time, "sourceCount": 1}
            continue
        current["high"] = max(current["high"], bar["high"])
        current["low"] = min(current["low"], bar["low"])
        current["close"] = bar["close"]
        current["volume"] = current["volume"] + bar["volume"]
        current["sourceCount"] += 1
    return [folded[key] for key in sorted(folded)]


# ----------------------------------------------------------------------
# Daily / multi-day
# ----------------------------------------------------------------------


def _engine_timeframe(span: Any) -> str:
    """Translate a MomoX span label to the key the validated engine speaks."""
    key = str(span or "").strip()
    if key in {"Wk", "W", "wk", "w"}:
        return "W"
    if key in {"M", "Mo", "m", "mo"}:
        return "M"
    if key in {"D", "d"}:
        return "D"
    upper = key.upper()
    if upper in {"2D", "3D", "4D"}:
        return upper
    raise ValueError(
        f"unsupported daily span {span!r}; expected one of {SUPPORTED_DAILY_SPANS}"
    )


def daily_group_key(span: Any, date_key: str) -> str:
    """The EMA-study grouping key -- ghts._timeframe_group_key, not a copy."""
    return _ghts._timeframe_group_key(_engine_timeframe(span), str(date_key))


def macd_daily_group_key(span: Any, date_key: str) -> str:
    """The MACD-study grouping key.

    A real TOS quirk the engine encodes: the MACD THREE_DAYS series rolls one
    calendar day AHEAD of the EMA 3D series, so 3D uses a different phase.
    Every other span is identical to :func:`daily_group_key`.
    """
    return _ghts._macd_timeframe_group_key(_engine_timeframe(span), str(date_key))


# ----------------------------------------------------------------------
# The WATCHLIST multi-day clock -- weekday chunks, not calendar chunks
# ----------------------------------------------------------------------
#
# MEASURED on CRWD, 2026-08-27, live, on the split-adjusted daily tape.
# Skittles = Round(StochasticFast(8, 8, WEIGHTED)."FastD", 0) on the folded
# tape, i.e. the value this grouping feeds:
#
#     span | 1969-12-30 calendar phase | weekday phase | thinkorswim
#     -----|---------------------------|---------------|------------
#     D    | 31                        | 31            | 31
#     2D   | 41                        | 53            | 53
#     3D   | 55                        | 68            | 68
#     4D   | 67                        | 68            | 68
#     Wk   | 71                        | 71            | 71
#
# WHY THE CALENDAR PHASE IS WRONG HERE. Chunking CALENDAR days puts a weekend
# inside a chunk: 2D pairs Fri+Sat and then Sun+Mon, so Friday sits alone in
# its candle and Monday opens a fresh one. TOS pairs Fri+Mon. Every offset
# k in -3..+3 of floor((ordinal + k) / N) was evaluated: 2D can only produce
# 41 or 51, never 53, and 3D only 55/60/62, never 68. Calendar chunking is
# ruled out at EVERY phase -- this is not a phase bug, it is a units bug.
#
# WHY WEEKDAYS AND NOT TRADING BARS. Chunking the actual bars present (so a
# market holiday is skipped, not just weekends) also reaches 53 and 68, but
# never all three at once: all 753 possible anchor bars in the three-year
# CRWD tape were tried and NONE satisfies 2D=53, 3D=68 and 4D=68 together
# (2D needs an even anchor, 4D an odd one). Counting Mon-Fri -- holidays
# included as phantom days that carry no bar -- is the only unit that admits
# a single consistent phase.
#
# THE PHASE. Searching every uniform k in 0..23 of
# floor((weekdays_since_1970_01_01 + k) / N), exactly k = 6 and its
# period-12 repeat k = 18 satisfy all three spans (12 = lcm(2, 3, 4)).
# k = 6 is therefore the phase, pinned mod 12.
#
# WHAT IS NOT PINNED, AND MUST BE RE-CHECKED IN Q1. One day of data pins the
# phase only modulo 12 weekdays. A DIFFERENT model fits the same measurement
# exactly: restarting the weekday count at each 1 January. For 2026 that
# reset lands on weekday index 14610, which is 6 mod 12 -- indistinguishable
# from the constant phase for the whole of 2026, because the Skittles window
# reaches back at most ~64 weekdays (4D x 16 bars) and so never crosses a
# year boundary outside Q1. The two models DISAGREE in 2024 and 2025. If a
# January/February reading disagrees with TOS, try the year reset before
# touching anything else here.


#: 1970-01-01 was a Thursday, so a remainder of r days from the epoch
#: contains this many weekdays: Thu, Fri, Sat, Sun, Mon, Tue, Wed.
_WEEKDAYS_IN_EPOCH_REMAINDER = (0, 1, 2, 2, 2, 3, 4)

_WEEKDAY_EPOCH = date(1970, 1, 1)

#: The measured TOS watchlist phase; see the block comment above. Pinned
#: mod 12, so 6 and 18 are the same clock.
TOS_WEEKDAY_PHASE = 6

#: ``grouping=`` choices for :func:`aggregate_daily`.
GROUPING_TOS = "tos"
GROUPING_EMA = "ema"
GROUPING_MACD = "macd"


@lru_cache(maxsize=8192)
def weekday_index(date_key: str) -> int | None:
    """Weekdays (Mon-Fri) elapsed since 1970-01-01, or None if unparseable.

    Market holidays ARE counted -- see the block comment above; that is the
    measured behaviour, and it is also what makes this key absolute. A key
    derived from the bars actually present would repaint the whole history
    whenever the tape's depth changed.
    """
    try:
        parsed = date.fromisoformat(str(date_key)[:10])
    except (TypeError, ValueError):
        return None
    whole_weeks, remainder = divmod((parsed - _WEEKDAY_EPOCH).days, 7)
    return whole_weeks * 5 + _WEEKDAYS_IN_EPOCH_REMAINDER[remainder]


def tos_multiday_group_key(span: Any, date_key: str) -> str:
    """The TOS WATCHLIST grouping key: N consecutive weekdays.

    ``D``, ``Wk`` and ``M`` delegate to :func:`daily_group_key` unchanged.
    Only ``2D``, ``3D`` and ``4D`` move onto the weekday clock.

    ``D`` and ``Wk`` were measured to match thinkorswim exactly (31 and 71 on
    CRWD, 2026-08-27). ``M`` was NOT: it reads 76 against the trader's 71, and
    it stays here anyway. 361 alternative month definitions were folded on the
    live tape -- weekday chunks, trading-bar chunks, calendar-day chunks,
    4- and 5-week chunks, every day-of-month anchor, OPT_EXP, and a 1-January
    reset -- and the five that reach 71 do so at phases with no stateable
    mechanism, while 71 is a 1.6%-probability value in that candidate family.
    In particular ``floor((weekday_index + 6) / N)``, the exact rule and phase
    that fixed 2D/3D/4D, gives 82/79/80/75/77/73/75 for N = 18..24 -- the
    multi-day mechanism does NOT extend to the month. Excluding the forming
    month is ruled out too: the same move breaks all five spans that match.
    See docs/momx/SPEC.md, "The Monthly Skittles gap", and get an NVDA M/Wk
    reading off the terminal before rebuilding anything here.
    """
    engine_span = _engine_timeframe(span)
    if engine_span not in {"2D", "3D", "4D"}:
        return daily_group_key(engine_span, date_key)
    index = weekday_index(date_key)
    if index is None:
        return ""
    size = int(engine_span[0])
    return f"{engine_span}-{(index + TOS_WEEKDAY_PHASE) // size}"


_GROUPINGS = {
    GROUPING_TOS: tos_multiday_group_key,
    GROUPING_EMA: daily_group_key,
    GROUPING_MACD: macd_daily_group_key,
}


def _date_key_for_daily_bar(bar: Mapping[str, Any]) -> str:
    supplied = bar.get("date") or bar.get("datetime")
    if isinstance(supplied, str) and len(supplied) >= 10:
        return supplied[:10]
    try:
        timestamp = int(bar["time"])
    except (KeyError, TypeError, ValueError):
        return ""
    if timestamp <= 0:
        return ""
    return _eastern_date_iso(timestamp)


@lru_cache(maxsize=65_536)
def _eastern_date_iso(timestamp: int) -> str:
    """The ET calendar date of an epoch second - pure, so memoised (speed pass)."""
    return datetime.fromtimestamp(timestamp, _EASTERN).date().isoformat()


@lru_cache(maxsize=65_536)
def _group_key_cached(grouping: str, engine_span: str, date_key: str) -> str:
    """The span grouping key - pure in its inputs, so memoised (speed pass)."""
    return _GROUPINGS[grouping](engine_span, date_key)


def aggregate_daily(
    daily_bars: Iterable[Mapping[str, Any]] | None,
    span: Any,
    *,
    grouping: str = GROUPING_TOS,
) -> list[dict]:
    """Group a daily tape into ``span`` candles on the TOS clock.

    ``span`` is one of ``D``/``2D``/``3D``/``4D``/``Wk``/``M``. ``D`` is
    identity. The default ``grouping="tos"`` chunks 2D/3D/4D on the measured
    WEEKDAY clock (:func:`tos_multiday_group_key`) -- an absolute phase, not
    a chunking anchored on the newest bar, which would repaint the whole
    history every session.

    ``grouping="ema"`` and ``grouping="macd"`` select the signal engine's
    1969-12-30 CALENDAR phases instead. Those are the keys
    ``ganesh_higher_timeframe_signals`` replays its EMA/MACD studies on; they
    do NOT reproduce the watchlist column. Use them only to agree with that
    module.
    """
    try:
        key_for = _GROUPINGS[grouping]
    except KeyError:
        raise ValueError(
            f"unsupported grouping {grouping!r}; expected one of "
            f"{sorted(_GROUPINGS)}"
        ) from None
    engine_span = _engine_timeframe(span)

    # Each bar's date key once (it used to be computed for the sort AND again
    # in the loop), stable-sorted exactly as before.
    keyed = [
        (_date_key_for_daily_bar(bar), int(bar.get("time") or 0), bar)
        for bar in (daily_bars or []) if isinstance(bar, Mapping)
    ]
    keyed.sort(key=lambda item: (item[0], item[1]))
    folded: list[dict] = []
    seen: dict[str, dict] = {}
    for date_key, _time, bar in keyed:
        if not date_key:
            continue
        group_key = _group_key_cached(grouping, engine_span, date_key) if key_for is _GROUPINGS.get(grouping) else key_for(engine_span, date_key)
        if not group_key:
            continue
        try:
            open_ = float(bar["open"])
            high = float(bar["high"])
            low = float(bar["low"])
            close = float(bar["close"])
        except (KeyError, TypeError, ValueError):
            continue
        try:
            volume = float(bar.get("volume") or 0.0)
        except (TypeError, ValueError):
            volume = 0.0
        current = seen.get(group_key)
        if current is None:
            current = {
                "time": int(bar.get("time") or 0),
                "date": date_key,
                "groupKey": group_key,
                "open": open_,
                "high": max(high, open_, close),
                "low": min(low, open_, close),
                "close": close,
                "volume": volume,
                "sourceCount": 1,
            }
            seen[group_key] = current
            folded.append(current)
            continue
        current["high"] = max(current["high"], high, close)
        current["low"] = min(current["low"], low, close)
        current["close"] = close
        current["volume"] += volume
        current["sourceCount"] += 1
    return folded


# ----------------------------------------------------------------------
# Forming vs closed
# ----------------------------------------------------------------------


def bucket_span_seconds(buckets: Any) -> int:
    """Modal gap between bucket opens, in seconds; 0 when undeterminable.

    Measured rather than assumed: this repo's tapes have shipped at 5- and
    30-minute cadences across builds, and reading the head of a deep archive
    reports a daily gap for a 30-minute tape.
    """
    rows = list(buckets or [])
    counts: dict[int, int] = {}
    for index in range(1, len(rows)):
        try:
            gap = int(rows[index]["time"]) - int(rows[index - 1]["time"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if gap > 0:
            counts[gap] = counts.get(gap, 0) + 1
    best_gap = 0
    best_count = 0
    # Strictly-greater, so ties keep the first gap seen.
    for gap, count in counts.items():
        if count > best_count:
            best_gap, best_count = gap, count
    return best_gap


def is_bucket_closed(bucket: Mapping[str, Any], now: Any, *, span_seconds: int) -> bool:
    """True once ``bucket``'s whole span has elapsed."""
    try:
        start = int(bucket["time"])
        moment = int(now)
        span = int(span_seconds)
    except (KeyError, TypeError, ValueError):
        return False
    if span <= 0:
        return False
    return start + span <= moment


def last_closed_bucket(buckets: Any, now: Any, *, span_seconds: int | None = None) -> int:
    """Index of the last bucket that has CLOSED, or -1 when none has.

    A FORMING bucket repaints with every tick -- a documented cause of a
    flickering squeeze flame in this repo. Some MomoX columns deliberately
    want the live forming bucket and some want the last settled one, so the
    choice is NOT baked in here: pair this with :func:`forming_bucket`.

    ``span_seconds`` is measured from the bucket spacing when not supplied.
    A tape too short to have a spacing cannot be judged, so its trailing
    bucket is treated as still forming.
    """
    rows = list(buckets or [])
    if not rows:
        return -1
    span = int(span_seconds) if span_seconds else bucket_span_seconds(rows)
    if span <= 0:
        return -1
    for index in range(len(rows) - 1, -1, -1):
        if is_bucket_closed(rows[index], now, span_seconds=span):
            return index
    return -1


def forming_bucket(buckets: Any, now: Any, *, span_seconds: int | None = None) -> int | None:
    """Index of the trailing bucket that has NOT closed yet, else None."""
    rows = list(buckets or [])
    if not rows:
        return None
    span = int(span_seconds) if span_seconds else bucket_span_seconds(rows)
    last = len(rows) - 1
    if span <= 0:
        return last
    return None if is_bucket_closed(rows[last], now, span_seconds=span) else last


__all__ = [
    "CENTRAL_MIDNIGHT_2H",
    "DEFAULT_TZ",
    "EASTERN_MIDNIGHT_2H",
    "GROUPING_EMA",
    "GROUPING_MACD",
    "GROUPING_TOS",
    "TOS_WEEKDAY_PHASE",
    "SUPPORTED_DAILY_SPANS",
    "SUPPORTED_INTRADAY_MINUTES",
    "aggregate_daily",
    "aggregate_intraday",
    "bucket_span_seconds",
    "daily_group_key",
    "forming_bucket",
    "intraday_bucket_time",
    "is_bucket_closed",
    "last_closed_bucket",
    "macd_daily_group_key",
    "merge_live_tail",
    "tos_multiday_group_key",
    "weekday_index",
]
