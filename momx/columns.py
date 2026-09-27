"""MomoX watchlist columns: one symbol's bar tapes -> board "cells".

Every function here is a LITERAL port of the thinkScript the trader supplied.
The ladders are evaluated in the order they are written in the script, because
thinkScript's ``if/else if`` chains are order-sensitive and reordering them
(even into something that looks equivalent) is how a column silently stops
matching the chart. Nothing here "improves" a formula.

All math is delegated to :mod:`momx.indicators`, which in turn delegates the
EMA recurrence to ``ganesh_higher_timeframe_signals`` -- the chart-validated
engine. There is no second implementation of any indicator in this file.

PAYLOAD CONTRACT
    A cell is ``{"value": number|str|None, "bg": colour|None, "fg": colour|None}``.
    Colours are the LOWERCASE thinkScript names (``"cyan"``, ``"dark_green"``,
    ``"light_red"``, ...). The backend never emits hex; the frontend maps
    names to CSS.

*** WARNING: high_low_cell IS INFERRED, NOT SUPPLIED ***
    The trader gave a script for RVOL, Squeeze, Skittles and Quote Trend. For
    the High/Low column they gave only "2h aggregation, length 8, EXT" -- no
    formula and no colour ladder. :func:`high_low_cell` implements the obvious
    reading (where close sits inside the 8-bar high/low range) and is the ONLY
    function in this module that is not TOS-verifiable. It must be replaced
    the moment the real script arrives. Do not treat its colours as parity.

This module must never import ``api_server``.
"""

from __future__ import annotations

import math
from datetime import datetime
from collections.abc import Mapping, Sequence  # abc, not typing: same isinstance answer, far cheaper (speed pass 2026-09-25)
from typing import Any
from zoneinfo import ZoneInfo

from momx.indicators import (
    ADX_LENGTH,
    adx_lines,
    crosses_above,
    crosses_below,
    ema,
    macd,
    rvol_zscore,
    stochastic_fast_d,
    ttm_squeeze,
    within_bars,
    rvol_ratio,
)

__all__ = [
    "PALETTE",
    "RVOL_TIMEFRAMES",
    "SQUEEZE_TIMEFRAMES",
    "SKITTLES_TIMEFRAMES",
    "RVOL_LENGTH",
    "RVOL_MIN_BARS",
    "SQUEEZE_LENGTH",
    "SQUEEZE_MIN_BARS",
    "SKITTLES_MIN_BARS",
    "HIGH_LOW_LENGTH",
    "HIGH_LOW_MIN_BARS",
    "QUOTE_TREND_POINTS",
    "SPARKLINE_POINTS",
    "null_cell",
    "cell",
    "last_price",
    "rvol_cell",
    "squeeze_cell",
    "squeeze_column_series",
    "skittles_cell",
    "high_low_cell",
    "color_cell",
    "quote_trend",
    "pct_change",
    "sparkline",
    "adx_cell",
    "ADX_TIMEFRAMES",
    "ADX_STRONG",
    "ADX_TAIL_BARS",
    "build_row",
]

#: The only colour names the backend is allowed to emit (payload contract).
PALETTE = (
    "cyan", "magenta", "green", "red", "light_red", "plum", "lime",
    "dark_green", "dark_red", "violet", "downtick", "orange", "white",
    "black", "gray",
)

#: Column -> timeframe keys, exactly as the payload contract names them.
RVOL_TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h", "D")
SQUEEZE_TIMEFRAMES = ("2h", "4h", "D", "Wk")
SKITTLES_TIMEFRAMES = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
#: ADX is RECORDED, not graded: these two tapes are the ones the trader reads
#: the chart's ADX study on. Nothing in momx/grade.py may read this field.
ADX_TIMEFRAMES = ("5m", "30m")

#: Preference order when a column needs "the finest tape available".
_INTRADAY_PREFERENCE = ("5m", "15m", "30m", "1h", "2h", "4h", "D")

#: How many seconds each RVOL timeframe's bucket spans. Daily and longer are
#: absent on purpose: pace only means something inside an intraday bucket.
RVOL_BUCKET_SECONDS = {
    "5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
}

#: A forming bucket must be at least this far along before its PACE is
#: published. The guard is the whole safety story for pace: dividing by the
#: elapsed fraction means a bucket one minute into thirty divides by 0.033,
#: turning ordinary volume into a 30x "spike" and pushing the trader's phone
#: for nothing. At 0.15 the earliest a 30m bucket can speak is ~4.5 minutes
#: in -- early enough to catch a 09:30 move around 09:35, late enough that the
#: sample is real. Below the floor NO pace is published (not a clamped one),
#: so the alert falls back to the plain value rather than reading a number
#: that was quietly rescued.
RVOL_PACE_MIN_ELAPSED = 0.15

RVOL_LENGTH = 50            # input length = 50

#: The highest a z-score over RVOL_LENGTH bars can possibly reach. With one
#: bar dominating the window the mean becomes ~v/n and the stdev ~v*sqrt(n-1)/n,
#: so the score pins at sqrt(n-1) = 7.00 for n=50 no matter how extreme the
#: bar is. Measured 2026-09-03: CHPT traded 30.8x its average and scored 6.97;
#: at 100x it would score the same.
RVOL_CEILING = math.sqrt(RVOL_LENGTH - 1)

#: How close to the ceiling counts as "out of room". Wide enough to catch a
#: reading the scale can no longer separate, narrow enough that an ordinary
#: high number is not mislabelled.
RVOL_CEILING_MARGIN = 0.2
SQUEEZE_LENGTH = 20         # TTM_Squeeze default
HIGH_LOW_LENGTH = 8         # his column config: length 8, 2h, EXT on
QUOTE_TREND_POINTS = 16

#: Bars that make up one hour, per intraday tape. Counted in BARS rather than
#: minutes because a halt or a gap makes timestamp arithmetic lie, while "the
#: last 12 five-minute bars" is exactly what his chart shows.
HOUR_BARS_BY_TAPE = {"5m": 12, "15m": 4, "30m": 2, "1h": 1, "2h": 1, "4h": 1}
SPARKLINE_POINTS = 40

# Warm-up floors used by build_row. A tape shorter than this yields a NULL
# cell rather than a number, because a study that has not warmed up is not a
# reading -- it is a blank. See the note on RVOL_MIN_BARS in build_row.
RVOL_MIN_BARS = RVOL_LENGTH                 # Average/StDev(volume, 50)
SQUEEZE_MIN_BARS = SQUEEZE_LENGTH + 2       # +2: the recursion reads [1] and close[2]
SKITTLES_MIN_BARS = 8 + 8                   # FastK(8) then a WEIGHTED FastD(8)
HIGH_LOW_MIN_BARS = HIGH_LOW_LENGTH

#: The chart's own "strong" marker on the ADX pane: a DI line crossing 25
#: (App.jsx calculateMtfAdxLines -> signals.strongCall / strongPut).
ADX_STRONG = 25.0
#: How many decimals the recorded ADX numbers carry on the wire. The study is
#: computed at full precision and every comparison below (cross / rising /
#: strong) is made on the RAW value; only the published number is rounded, and
#: only because this rides on 357 rows of a 15-second poll.
ADX_DIGITS = 2
#: How many trailing bars the ADX cell is computed on. THE CELL ONLY EVER
#: PUBLISHES THE LAST TWO BARS, and Wilders smoothing is a decaying recursion:
#: with the chart's length of 10, a bar N back carries weight 0.9^N, so 200
#: bars back is already ~7e-10 of the reading.
#:
#: Measured 2026-09-22 on six random 4,000-13,000 bar tapes: tails of 200 /
#: 300 / 500 give a PUBLISHED cell identical to the full tape's, field for
#: field. Not bit-identical in raw floats -- the truncated recursion differs
#: by up to 9.6e-12 at 300 (and ~2e-7 at 200), which is why 300 and not 200:
#: it is ~5 orders of magnitude inside the 2-decimal publication (ADX_DIGITS)
#: and inside any cross / rising / strong comparison that is not an exact tie.
#: tests/test_momx_adx.py holds the guard on a generated 2,000-bar series.
#:
#: The full tapes are 5m ~6,600 bars and 30m ~13,000, costing 110-124 ms per
#: symbol -- about +8-9 s on a 358-symbol build. At 300 the pair costs ~3 ms.
ADX_TAIL_BARS = 300

NAN = float("nan")


# ---------------------------------------------------------------------------
# cells
# ---------------------------------------------------------------------------

def null_cell() -> dict:
    """A cell with nothing in it. Always a FRESH dict - cells are mutable."""
    return {"value": None, "bg": None, "fg": None}


def cell(value: Any, bg: str | None = None, fg: str | None = None) -> dict:
    return {"value": value, "bg": bg, "fg": fg}


# ---------------------------------------------------------------------------
# tape coercion
# ---------------------------------------------------------------------------

def _rows(bars: Any) -> list[Mapping[str, Any]]:
    """Accept a list of bar mappings or a pandas-like frame; never raise.

    ``momx.feed`` hands back DataFrames, the aggregators in ``momx.buckets``
    hand back lists of dicts, and a broker payload can hand back anything.
    One dead symbol must never blank the board, so unusable input degrades to
    an empty tape instead of an exception.
    """
    if bars is None:
        return []
    if isinstance(bars, Mapping):
        return []
    to_dict = getattr(bars, "to_dict", None)
    if to_dict is not None and hasattr(bars, "columns"):
        try:
            return list(to_dict("records"))
        except Exception:
            return []
    if isinstance(bars, Sequence) and not isinstance(bars, (str, bytes)):
        return [row for row in bars if isinstance(row, Mapping)]
    try:
        return [row for row in bars if isinstance(row, Mapping)]
    except TypeError:
        return []


def _number(row: Mapping[str, Any], *names: str) -> float:
    for name in names:
        if name not in row:
            continue
        try:
            value = float(row[name])
        except (TypeError, ValueError):
            continue
        if math.isfinite(value):
            return value
    return NAN


def _ohlcv(bars: Any) -> tuple[list[float], list[float], list[float], list[float]]:
    rows = _rows(bars)
    highs: list[float] = []
    lows: list[float] = []
    closes: list[float] = []
    volumes: list[float] = []
    for row in rows:
        highs.append(_number(row, "high", "h", "High"))
        lows.append(_number(row, "low", "l", "Low"))
        closes.append(_number(row, "close", "c", "Close"))
        volumes.append(_number(row, "volume", "v", "Volume"))
    return highs, lows, closes, volumes


def _closes(bars: Any) -> list[float]:
    return _ohlcv(bars)[2]


def _ts_round(value: Any, digits: int = 0) -> float | None:
    """thinkScript ``Round(x, n)``: HALF AWAY FROM ZERO, not banker's rounding.

    Python's built-in ``round`` is banker's rounding (``round(0.5) == 0``,
    ``round(2.5) == 2``). TOS rounds 0.5 up to 1 and -0.5 down to -1, so the
    built-in would put a 1-cell-per-thousand disagreement into the Skittles
    and RVOL numbers. Returns None for NaN/inf.
    """
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    factor = 10.0 ** int(digits)
    scaled = number * factor
    if scaled >= 0:
        rounded = math.floor(scaled + 0.5)
    else:
        rounded = math.ceil(scaled - 0.5)
    return rounded / factor


def _truthy(value: Any) -> bool:
    """thinkScript: a numeric def in a boolean position is true when non-zero."""
    try:
        return float(value) != 0.0
    except (TypeError, ValueError):
        return bool(value)


# ---------------------------------------------------------------------------
# RVOL -- WATCHLIST COLUMN version (NOT the scanner version)
# ---------------------------------------------------------------------------

def _pace_zscore(
    volumes: list, length: Any, elapsed: float | None
) -> float | None:
    """The z-score this bucket would show if it kept its current pace.

    A RATIO is linear in volume, so pacing it is just value/elapsed. A z-score
    is not -- ``(v - mean) / sd`` -- so scaling the SCORE would be meaningless
    arithmetic that still produced a plausible-looking number. Instead the
    VOLUME is projected to a full bucket and scored, which is the honest
    reading of "what this bar looks like on track to close at".

    Projecting the last bar also feeds the trailing Average/StDev, exactly as
    the real closing bar would, so the comparison stays like-for-like.

    ``None`` whenever the answer would be untrustworthy: no elapsed fraction,
    a bucket too early to judge (see RVOL_PACE_MIN_ELAPSED), or a non-finite
    result. Consumers fall back to the plain value; a rescued number here
    would reach the trader's phone as a real alert.
    """
    if elapsed is None or not volumes:
        return None
    try:
        fraction = float(elapsed)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fraction) or fraction < RVOL_PACE_MIN_ELAPSED:
        return None
    if fraction >= 1.0:
        # A complete bucket needs no projection; publishing the value itself
        # lets a consumer read "pace" uniformly across timeframes.
        fraction = 1.0
    projected = list(volumes)
    try:
        last = float(projected[-1])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(last):
        return None
    projected[-1] = last / fraction
    paced = rvol_zscore(projected, length)[-1]
    if not math.isfinite(paced):
        return None
    return _ts_round(paced, 1)


def _pace_value(rel_vol: float, elapsed: float | None) -> float | None:
    """``rel_vol`` re-expressed as a FULL-bucket rate, or ``None``.

    Why this exists: RVOL divides a still-forming bucket's volume by an
    average of COMPLETE buckets. Five minutes into a thirty-minute bucket a
    stock trading at exactly twice its normal rate reads 0.33, not 2.0, so a
    threshold of 2.0 cannot be crossed until the bucket is nearly closed --
    which is after the move. Measured live on 2026-09-01: CRML's 09:30 bucket
    ended at 23.4x and the trader was not told until 10:00. Dividing by the
    elapsed fraction restores the comparison to like-for-like.

    ``None`` is returned, rather than a fallback number, whenever the answer
    would be untrustworthy: no elapsed fraction, a bucket too early to judge,
    or a non-finite ratio. Consumers are contracted to fall back to the plain
    value when pace is ``None``. This is deliberate -- a silently rescued
    number here would reach the trader's phone as a real alert.
    """
    if elapsed is None:
        return None
    try:
        fraction = float(elapsed)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(fraction) or fraction < RVOL_PACE_MIN_ELAPSED:
        return None
    if fraction >= 1.0:
        # A complete bucket needs no adjustment; publishing the value itself
        # (not None) lets a consumer read "pace" uniformly across timeframes.
        fraction = 1.0
    if not math.isfinite(rel_vol):
        return None
    return _ts_round(rel_vol / fraction, 1)


def rvol_cell(
    bars: Any, length: int = RVOL_LENGTH, *, elapsed: float | None = None
) -> dict:
    """The trader's RVOL watchlist column, on the LAST bar of ``bars``.

    ::

        def buying = close - low;
        def selling = high - close;
        def relVol = if IsNaN((volume - Average(volume, length))
                              / StDev(volume, length)) then 0 else that;
        def isBullish = buying >= selling;
        plot RelVolume = round(relVol, 1);

    Two things that are easy to get wrong and are load-bearing here:

    * ``isBullish`` is ``>=``, so a doji (buying == selling) is BULLISH.
    * the colour ladders test the RAW ``relVol``, not the rounded plot, and
      the lowest text rung is ``> 0.5`` while every other rung is ``>=``. A
      z-score of exactly 0.5 therefore shows BLACK text, not dark green.

    This is the COLUMN script. The SCANNER RVOL script is a different thing
    (it thresholds at numDev and compares dollar buying vs selling); it lives
    in the scan module and must not be unified with this one.
    """
    highs, lows, closes, volumes = _ohlcv(bars)
    if not closes:
        return null_cell()

    # The COLUMN displays the Z-SCORE his script plots:
    #     (volume - Average(volume, length)) / StDev(volume, length)
    #
    # It showed the RATIO (volume/avg) from 2026-08-28, adopted because MSFT's
    # 2h matched his TOS at 2.2 exactly. That single sample was not enough:
    # near relVol 2 the two formulas cross closely, so one agreement proves
    # nothing. Settled 2026-09-01 by replaying this tape to 12:35 ET -- the
    # timestamp of a TOS screenshot covering 12 tickers -- and scoring both
    # against his screen at that instant:
    #
    #     mean absolute error      ratio   zscore
    #       his 2h column           0.36     1.07
    #       his 4h column           1.84     0.23
    #
    # with exact z-score hits on MRNA 0.7, NVAX 2.5, BE 1.7, ENVX 0.8. His two
    # columns run DIFFERENT scripts (the 4h one is named "R_4h_New"), which is
    # why correlating his columns against ours never converged - there was
    # never one formula to find. He chose the z-score for every timeframe.
    #
    # The colour ladder below is unchanged and now finally means what the
    # script says: its rungs were always written against relVol, and relVol in
    # his script IS this z-score. The SCANNER rvol condition is untouched.
    rel_vol = rvol_zscore(volumes, length)[-1]
    high = highs[-1]
    low = lows[-1]
    close = closes[-1]
    if not (math.isfinite(high) and math.isfinite(low) and math.isfinite(close)):
        return null_cell()

    buying = close - low
    selling = high - close
    is_bullish = buying >= selling

    if is_bullish:
        if rel_vol >= 3:
            background = "cyan"
        elif rel_vol >= 2:
            background = "green"
        else:
            background = "black"
    else:
        if rel_vol >= 3:
            background = "magenta"
        elif rel_vol >= 2:
            background = "red"
        else:
            background = "black"

    if is_bullish:
        if rel_vol >= 3:
            foreground = "black"
        elif rel_vol >= 2:
            foreground = "black"
        elif rel_vol >= 1.5:
            foreground = "cyan"
        elif rel_vol >= 1:
            foreground = "green"
        elif rel_vol > 0.5:          # NOTE: strictly greater, unlike the rest
            foreground = "dark_green"
        else:
            foreground = "black"
    else:
        if rel_vol >= 3:
            foreground = "black"
        elif rel_vol >= 2:
            foreground = "black"
        elif rel_vol >= 1.5:
            foreground = "magenta"
        elif rel_vol >= 1:
            foreground = "red"
        elif rel_vol > 0.5:          # NOTE: strictly greater, unlike the rest
            foreground = "dark_red"
        else:
            foreground = "black"

    out = cell(_ts_round(rel_vol, 1), background, foreground)
    out["pace"] = _pace_zscore(volumes, length, elapsed)
    # AT THE CEILING the z-score stops discriminating: 30x normal and 100x
    # normal both read 7.0. Carry the plain multiple so the tooltip can say
    # what the number no longer can. Only when saturated - see the note on
    # RVOL_CEILING - so this stays a handful of floats per build.
    if abs(rel_vol) >= RVOL_CEILING - RVOL_CEILING_MARGIN:
        try:
            multiple = rvol_ratio(volumes, length)[-1]
        except Exception:  # noqa: BLE001 - a tooltip must never cost a cell
            multiple = float("nan")
        if math.isfinite(multiple) and multiple > 0:
            out["xAvg"] = _ts_round(multiple, 1)
    return out


# ---------------------------------------------------------------------------
# Squeeze -- WATCHLIST COLUMN version
# ---------------------------------------------------------------------------

def squeeze_column_series(bars: Any, length: int = SQUEEZE_LENGTH) -> dict[str, list]:
    """Every recursive def of the squeeze COLUMN script, bar by bar.

    Exposed (not private) because the recursions are the whole column and a
    black-box test of the final cell cannot show that ``MSF`` caps at 2.

    ::

        def Higher = high > close[2];          <- TWO bars back, not one
        def MS  = TTM_Squeeze().SqueezeAlert == 0;
        def MSC = if MS then MSC[1]+1 else 0;
        def MF  = if !MS and MS[1] then 1 else 0;
        def MSF = if (!MS and MS[1]) or (!MS and MSF[1] > 0 and MSF[1] < 2)
                  then MSF[1]+1 else 0;
        ... and the same four for HS at nk = 1.0
        def MD  = TTM_Squeeze() > TTM_Squeeze()[1];
        def MFD = if MF then MD else MFD[1];

    ``MSF`` walks 1 -> 2 -> 0: the release bar sets 1, the next bar sees
    ``MSF[1] == 1`` (``> 0 and < 2``) and sets 2, the bar after sees 2 and the
    cap closes it. So "fired" lights exactly two bars.

    On bar 0 every ``[1]`` reference is treated as 0/false, which is how
    thinkScript's NaN-seeded recursions behave in a boolean position.
    """
    highs, lows, closes, _ = _ohlcv(bars)
    count = len(closes)

    medium = ttm_squeeze(highs, lows, closes, length, nk=1.5)
    high_compression = ttm_squeeze(highs, lows, closes, length, nk=1.0)

    ms = [alert == 0 for alert in medium.squeeze_alert]
    hs = [alert == 0 for alert in high_compression.squeeze_alert]

    higher: list[bool] = []
    for index in range(count):
        if index < 2:
            higher.append(False)
            continue
        bar_high = highs[index]
        prior_close = closes[index - 2]
        higher.append(
            math.isfinite(bar_high)
            and math.isfinite(prior_close)
            and bar_high > prior_close
        )

    def _rising(histogram: list[float], index: int) -> bool:
        if index < 1:
            return False
        current = histogram[index]
        previous = histogram[index - 1]
        if not (math.isfinite(current) and math.isfinite(previous)):
            return False
        return current > previous

    msc: list[int] = []
    msf: list[int] = []
    mf: list[bool] = []
    mfd: list[bool] = []
    hsc: list[int] = []
    hsf: list[int] = []
    hf: list[bool] = []
    hfd: list[bool] = []

    for index in range(count):
        prior_ms = ms[index - 1] if index else False
        prior_msc = msc[index - 1] if index else 0
        prior_msf = msf[index - 1] if index else 0
        prior_mfd = mfd[index - 1] if index else False
        prior_hs = hs[index - 1] if index else False
        prior_hsc = hsc[index - 1] if index else 0
        prior_hsf = hsf[index - 1] if index else 0
        prior_hfd = hfd[index - 1] if index else False

        msc.append(prior_msc + 1 if ms[index] else 0)
        fired_medium = (not ms[index]) and prior_ms
        mf.append(fired_medium)
        if fired_medium or ((not ms[index]) and 0 < prior_msf < 2):
            msf.append(prior_msf + 1)
        else:
            msf.append(0)
        mfd.append(_rising(medium.histogram, index) if fired_medium else prior_mfd)

        hsc.append(prior_hsc + 1 if hs[index] else 0)
        fired_high = (not hs[index]) and prior_hs
        hf.append(fired_high)
        if fired_high or ((not hs[index]) and 0 < prior_hsf < 2):
            hsf.append(prior_hsf + 1)
        else:
            hsf.append(0)
        hfd.append(
            _rising(high_compression.histogram, index) if fired_high else prior_hfd
        )

    return {
        "MS": ms, "MSC": msc, "MF": mf, "MSF": msf, "MFD": mfd,
        "HS": hs, "HSC": hsc, "HF": hf, "HSF": hsf, "HFD": hfd,
        "Higher": higher,
    }


def _sqz_raw(tapes: Any) -> dict:
    """Raw squeeze states behind the SQZ cells, for the grade event log only.

    Never raises: a tape that cannot fold, or one too short for the squeeze
    warm-up, yields ``None`` for that timeframe rather than breaking the row.
    """
    out: dict[str, Any] = {}
    for tf in ("4h", "D", "Wk"):
        bars = tapes.get(tf) if isinstance(tapes, Mapping) else None
        try:
            series = squeeze_column_series(bars) if bars else None
        except Exception:  # noqa: BLE001 - logging aid, never fatal
            series = None
        if not series or not series["MS"]:
            out[tf] = None
            continue
        i = len(series["MS"]) - 1
        release = next((j for j in range(i, -1, -1) if series["MF"][j] or series["HF"][j]), None)
        times = [b.get("time", b.get("timestamp")) for b in bars]
        out[tf] = {
            "ms": bool(series["MS"][i]), "hs": bool(series["HS"][i]),
            "msc": int(series["MSC"][i]), "hsc": int(series["HSC"][i]),
            "msf": int(series["MSF"][i]), "hsf": int(series["HSF"][i]),
            "mfd": bool(series["MFD"][i]),
            "lastReleaseAt": None if release is None else times[release],
        }
    return out


def squeeze_cell(bars: Any, length: int = SQUEEZE_LENGTH) -> dict:
    """The squeeze watchlist column cell on the LAST bar.

    Background, in the script's own order (do not reorder)::

        if HS                            -> white
        else if (HSF and HFD and Higher) -> orange
        else if HSF                      -> orange
        else if MS                       -> orange
        else if (MSF and MFD and Higher) -> cyan
        else if MSF                      -> magenta
        else                             -> black

    Yes, the first three orange rungs collapse to one branch as written; they
    are kept separate because that is the source and a future edit to the
    trader's script will most likely change one of them.

    The label is a STRING: ``"*7"`` (a high-compression count), ``"7"``, or
    ``"-"``. The star is real displayed text, so the value must not be
    numeric. thinkScript would render the concatenation as ``*7.00``; this
    port formats the count as an INTEGER because the counts are bar counts and
    the trader's board shows them without decimals. (Judgement call.)
    """
    _, _, closes, _ = _ohlcv(bars)
    if len(closes) < 2:
        return null_cell()

    series = squeeze_column_series(bars, length)
    index = len(closes) - 1
    hs = series["HS"][index]
    ms = series["MS"][index]
    hsf = series["HSF"][index]
    msf = series["MSF"][index]
    hsc = series["HSC"][index]
    msc = series["MSC"][index]
    hfd = series["HFD"][index]
    mfd = series["MFD"][index]
    higher = series["Higher"][index]

    if hs:
        background = "white"
    elif _truthy(hsf) and hfd and higher:
        background = "orange"
    elif _truthy(hsf):
        background = "orange"
    elif ms:
        background = "orange"
    elif _truthy(msf) and mfd and higher:
        background = "cyan"
    elif _truthy(msf):
        background = "magenta"
    else:
        background = "black"

    if _truthy(hsf):
        label = "*" + str(hsc + hsf)
    elif _truthy(msf):
        label = str(msc + msf)
    elif hs:
        label = str(hsc + hsf)
    elif ms:
        label = str(msc + msf)
    else:
        label = "-"

    # AddLabel(..., Color.Black) - the text colour is hardcoded in the script.
    return cell(label, background, "black")


# ---------------------------------------------------------------------------
# Skittles
# ---------------------------------------------------------------------------

def skittles_cell(bars: Any) -> dict:
    """The Skittles column: StochasticFast FastD painted by EMA/MACD crosses.

    ``value`` is ``Round(FastD, 0)`` where FastD is the WEIGHTED (not simple)
    average of FastK(8) over 8 bars. The colour ladders test the UNROUNDED
    ``value`` against 90/10, which is why the raw FastD is kept separately.

    "within 1 bars" = the cross fired on THIS bar or the PREVIOUS one, so a
    cross lights the cell on the bar it happens, and only that bar: the
    script's ``within 1 bars`` is a ONE-candle window (the current one).
    ``within_bars(flags, 1)`` is therefore the identity - see its docstring
    for the reference wording and for what this used to do wrong.
    """
    highs, lows, closes, _ = _ohlcv(bars)
    if len(closes) < SKITTLES_MIN_BARS:
        return null_cell()

    fast_d = stochastic_fast_d(highs, lows, closes, 8, 8)
    raw_value = fast_d[-1]
    if not math.isfinite(raw_value):
        return null_cell()

    macd_value, macd_average = macd(closes, 6, 12, 8)
    ema4 = ema(closes, 4)
    ema8 = ema(closes, 8)
    ema9 = ema(closes, 9)          # MovAvgExponential() with no length = 9
    ema20 = ema(closes, 20)

    macd21 = within_bars(crosses_above(macd_value, macd_average), 1)[-1]
    macd21d = within_bars(crosses_below(macd_value, macd_average), 1)[-1]
    exu2 = within_bars(crosses_above(ema4, ema8), 1)[-1]
    exd2 = within_bars(crosses_below(ema4, ema8), 1)[-1]
    exu1 = within_bars(crosses_above(ema9, ema20), 1)[-1]
    exd1 = within_bars(crosses_below(ema9, ema20), 1)[-1]

    last_ema9 = ema9[-1]
    last_ema20 = ema20[-1]
    exu = (
        math.isfinite(last_ema9)
        and math.isfinite(last_ema20)
        and last_ema9 > last_ema20
    )
    exd = (
        math.isfinite(last_ema9)
        and math.isfinite(last_ema20)
        and last_ema9 < last_ema20
    )

    if exd1:
        background = "magenta"
    elif macd21d and exd:
        background = "red"
    elif exd2 and exd:
        background = "light_red"
    elif macd21d:
        background = "plum"
    elif exu1:
        background = "cyan"
    elif macd21 and exu:
        background = "green"
    elif exu2 and exu:
        background = "lime"
    elif macd21:
        background = "dark_green"
    else:
        background = "black"

    if macd21d and exu:
        foreground = "violet"
    elif macd21 and exd:
        foreground = "downtick"
    elif (
        exd1
        or exu1
        or (exd2 and exd)
        or (exu2 and exu)
        or (macd21d and exd)
        or (macd21 and exu)
    ):
        foreground = "black"
    elif exu and raw_value >= 90:
        foreground = "dark_green"
    elif exd and raw_value <= 10:
        foreground = "plum"
    elif exu:
        foreground = "cyan"
    elif exd:
        foreground = "magenta"
    else:
        foreground = "orange"

    rounded = _ts_round(raw_value, 0)
    return cell(int(rounded) if rounded is not None else None, background, foreground)


# ---------------------------------------------------------------------------
# High/Low -- INFERRED
# ---------------------------------------------------------------------------

def high_low_cell(bars_2h: Any, length: int = HIGH_LOW_LENGTH) -> dict:
    """The trader's "High/Low Graph" column, ported literally.

    Script and config supplied by him 2026-08-28 and re-confirmed 2026-09-03
    from the column's own editor - column name "HighLowGraph", aggregation 2h,
    EXT on, input length 8 (the study text defaults to 1; the COLUMN sets 8)::

        input length = 1;
        def hh = Highest(high, length);
        def ll = Lowest(low, length);
        def mid = (hh + ll) / 2;
        plot HighLowDegree;
        if (hh == ll) { HighLowDegree = 0; }
        else { HighLowDegree = (close - mid) / (hh - mid); }

    Runs -1 (close at the 8-bar low) to +1 (at the high), 0 at the MIDPOINT --
    not at the low. On a 2h EXT tape those 8 bars are about 16 hours of
    extended session, so this is emphatically not "today's range".

    NOT from his script: the COLOUR. His ends at ``plot HighLowDegree;`` with
    no colour assignment at all - thinkorswim draws the column as a graph (it
    is named "High/Low Graph"). Green above the midpoint and red below is
    OUR rendering of his number. Anything else here is his.

    Historical note: this file used to label the column INFERRED at
    HIGH_LOW_LENGTH while this docstring said "ported literally". The
    2026-09-03 parity audit reported the column as invented on the strength of
    that contradiction. The docstring was the correct half.
    """
    highs, lows, closes, _ = _ohlcv(bars_2h)
    span = max(1, int(length or 1))
    if len(closes) < span:
        return null_cell()

    high_window = highs[-span:]
    low_window = lows[-span:]
    close = closes[-1]
    if (
        not math.isfinite(close)
        or any(not math.isfinite(value) for value in high_window)
        or any(not math.isfinite(value) for value in low_window)
    ):
        return null_cell()

    hh = max(high_window)
    ll = min(low_window)
    if hh == ll:
        # The script's own guard: a zero-width range is 0, not a divide -
        # and 0 falls to his colour ladder's else: GRAY.
        return cell(0.0, "gray", "black")
    mid = (hh + ll) / 2.0
    degree = (close - mid) / (hh - mid)
    # His colours (script re-sent 2026-09-25 with AssignBackgroundColor):
    # > 0.5 DARK_GREEN, > 0 GREEN, < -0.5 DARK_RED, < 0 RED, else GRAY.
    # Tested on the UNROUNDED degree, like every thinkScript ladder here.
    if degree > 0.5:
        background = "dark_green"
    elif degree > 0:
        background = "green"
    elif degree < -0.5:
        background = "dark_red"
    elif degree < 0:
        background = "red"
    else:
        background = "gray"
    return cell(_ts_round(degree, 2), background, "black")


# ---------------------------------------------------------------------------
# COLOR separator
# ---------------------------------------------------------------------------

def color_cell() -> dict:
    """The COLOR column: ``plot blank = Double.NaN; blank.Hide();`` + white bg.

    A pure visual divider between column groups. No value, no logic, no
    dependence on the symbol. A fresh dict every call so a caller mutating one
    row cannot repaint every row.
    """
    return {"value": None, "bg": "white", "fg": None}


# ---------------------------------------------------------------------------
# Quote Trend
# ---------------------------------------------------------------------------

def quote_trend(bars: Any, points: int = QUOTE_TREND_POINTS) -> list[dict]:
    """The Quote Trend mini-histogram: one full cell per bar, newest LAST.

    ::

        plot QuoteTrendScore = if close > close[1] then 1
                               else if close < close[1] then -1 else 0;
        value colour:  GREEN / RED / GRAY
        background:    DARK_GREEN / DARK_RED / BLACK

    The scores are computed over the WHOLE tape and only then windowed to
    ``points``, so the first rendered bar still compares against its real
    predecessor. Only the very first bar of the tape has no ``close[1]``; it
    is flat (0 / gray / black), matching thinkScript, where the comparison
    against a NaN prior is neither up nor down.
    """
    closes = _closes(bars)
    cells: list[dict] = []
    for index, close in enumerate(closes):
        previous = closes[index - 1] if index else NAN
        price = float(close) if math.isfinite(close) else None
        if (
            index == 0
            or not math.isfinite(close)
            or not math.isfinite(previous)
            or close == previous
        ):
            entry = cell(0, "black", "gray")
        elif close > previous:
            entry = cell(1, "dark_green", "green")
        else:
            entry = cell(-1, "dark_red", "red")
        # price drives the rendered bar height (the mini price chart MomoX
        # shows); value keeps the +1/-1/0 direction for colour and sorting.
        entry["price"] = price
        cells.append(entry)

    window = max(0, int(points or 0))
    return cells[-window:] if window and len(cells) > window else cells


# ---------------------------------------------------------------------------
# scalars
# ---------------------------------------------------------------------------

_EASTERN = ZoneInfo("America/New_York")


def _bar_session_date(bar: Any) -> str:
    """The ET calendar date a bar belongs to, as YYYY-MM-DD; "" if unknowable.

    Daily bars arrive stamped midnight ET on their session date (verified
    against the live feed 2026-09-03: Timestamp("2026-09-03 00:00:00-0400")),
    so the ET date of the stamp IS the session. Intraday bars stamp a moment
    inside the same session, so the two are directly comparable.
    """
    if not isinstance(bar, Mapping):
        return ""
    try:
        moment = int(bar.get("time"))
    except (TypeError, ValueError):
        return ""
    if moment <= 0:
        return ""
    try:
        return datetime.fromtimestamp(moment, _EASTERN).date().isoformat()
    except (OverflowError, OSError, ValueError):
        return ""


def pct_change(
    daily_bars: Any, intraday_bars: Any = None, last: float | None = None
) -> float | None:
    """Today's change in percent, correct BEFORE the open as well as after.

    Regular hours: the latest daily close against the previous one, exactly
    as it always was.

    PREMARKET the daily tape has no bar for today yet, so that same
    expression returns YESTERDAY's change - a stable, plausible number that
    is simply wrong, and that silently corrects itself at the open. Measured
    on 2026-09-03: CLS showed -5.07% from 07:21 to 08:57 while its price sat
    at 280.8, then jumped to +2.89% at 09:37 on a price of 282.2. -5.07% was
    its 09-02 close. CRDO showed -20.04% for the whole premarket, unmoving.

    So when today's daily bar is absent, the live intraday price is measured
    against the newest daily close - which in that situation IS yesterday's
    close, the correct base.

    Whether today's bar exists is decided from the DATA, by comparing the
    newest intraday bar's session date with the newest daily bar's. Not from
    the wall clock: the feed runs minutes behind, and a clock would call a
    session open while the tape still ends yesterday.

    None (not 0.0) when the answer is unknown -- the board sorts on this
    column, and a fabricated 0.0 would sort a data-less symbol into the
    middle of the pack instead of out of the way. Every uncertain path falls
    back to the daily-only expression rather than guessing.
    """
    closes = _closes(daily_bars)
    if not closes:
        return None

    daily_rows = _rows(daily_bars)
    intraday_rows = _rows(intraday_bars)
    if daily_rows and intraday_rows:
        daily_date = _bar_session_date(daily_rows[-1])
        intraday_date = _bar_session_date(intraday_rows[-1])
        # A LATER intraday session than the newest daily bar means today's
        # daily bar has not formed: premarket.
        if daily_date and intraday_date and intraday_date > daily_date:
            base = closes[-1]
            if (
                last is not None
                and math.isfinite(last)
                and math.isfinite(base)
                and base != 0
            ):
                return (last - base) / base * 100.0
            # No usable live price: yesterday's change would be WORSE than
            # nothing here, because it looks like today's.
            return None

    if len(closes) < 2:
        return None
    latest = closes[-1]
    previous = closes[-2]
    if not (math.isfinite(latest) and math.isfinite(previous)) or previous == 0:
        return None
    return (latest - previous) / previous * 100.0


def sparkline(intraday_bars: Any, points: int = SPARKLINE_POINTS) -> list[float]:
    """The trailing ``points`` closes, oldest first. Non-finite bars dropped."""
    closes = [value for value in _closes(intraday_bars) if math.isfinite(value)]
    window = max(0, int(points or 0))
    if not window:
        return []
    return closes[-window:]


# ---------------------------------------------------------------------------
# row assembly
# ---------------------------------------------------------------------------

def _tape(tapes: Any, key: str) -> list[Mapping[str, Any]]:
    if not isinstance(tapes, Mapping):
        return []
    return _rows(tapes.get(key))


def _finest_intraday_key(tapes: Any) -> str | None:
    """Which tape :func:`_finest_intraday` picked. Pace needs the RESOLUTION.

    Elapsed-fraction is counted in whole constituent bars, so the caller has
    to know how many seconds one of those bars covers. Returning the key is
    cheaper and less brittle than inferring the spacing from timestamps, which
    a halt, a gap or a single duplicated bar would get wrong.
    """
    for key in _INTRADAY_PREFERENCE:
        if _tape(tapes, key):
            return key
    return None


def _bucket_elapsed_fraction(
    tapes: Any, bars: list[Mapping[str, Any]], key: str
) -> float | None:
    """How much of ``bars``' newest bucket has actually happened, 0-1.

    Derived from DATA -- the count of finer bars that fall inside the newest
    bucket -- and deliberately not from the wall clock. The feed runs minutes
    behind real time, so a clock-derived fraction would claim a bucket is
    further along than the data supports and would understate pace exactly
    when it matters. Counting constituent bars asks "how much of this bucket
    do I actually have?", which is the honest question.

    Returns ``None`` (meaning "cannot say") when there is no finer tape to
    count with, when the timeframe has no fixed intraday span (daily and up),
    or when the bucket is not subdivisible. ``None`` is a first-class answer
    here: the caller publishes no pace rather than a guessed one.
    """
    span = RVOL_BUCKET_SECONDS.get(key)
    if not span or not bars:
        return None
    fine_key = _finest_intraday_key(tapes)
    fine_span = RVOL_BUCKET_SECONDS.get(fine_key or "")
    # Same resolution (or coarser) means the newest bar IS the bucket: there is
    # nothing finer to count, so its fill is unknowable from this data alone.
    if not fine_span or fine_span >= span:
        return None
    try:
        start = float(bars[-1].get("time"))
    except (TypeError, ValueError):
        return None
    if not math.isfinite(start):
        return None
    fine = _tape(tapes, fine_key)
    inside = 0
    for row in fine:
        try:
            moment = float(row.get("time"))
        except (TypeError, ValueError):
            continue
        if start <= moment < start + span:
            inside += 1
    if inside <= 0:
        return None
    return min(1.0, (inside * fine_span) / span)


#: Bucket spans for the volume-midpoint walk. The intraday ones come straight
#: from RVOL_BUCKET_SECONDS; "D" is added HERE rather than there because that
#: constant is PACE's, and pace on a daily bar is meaningless - projecting a
#: part-finished day to a full one says nothing. The midpoint is a different
#: question with a real answer: "when did half of today's volume trade".
#:
#: Measured 2026-09-03: CHPT half of 43.8M shares by 11:20 (its +74% move),
#: HPE half of 72.1M by 14:15, FDX half of 2.1M by 15:35. Without this the D
#: cell fell back to the bar's start, which for a daily bar is always midnight
#: - which is why the column had no time at all and he noticed.
_MIDPOINT_SPAN_SECONDS = {**RVOL_BUCKET_SECONDS, "D": 86_400}


def _volume_midpoint_time(tapes: Any, bars: list[Mapping[str, Any]], key: str) -> int:
    """WHEN the volume in the newest bucket actually arrived.

    Returns the epoch of the finer bar at which cumulative volume inside the
    bucket first reaches half its total - the volume-weighted midpoint.

    Why not simply the bucket's open time: they are wildly different numbers
    and only this one answers his question. Measured on FDX 2026-09-03, the 2h
    bucket that opened 15:00 and read a cyan 3.1:

        16:00   783,900   67.1% of the whole bucket   <- it all landed here
        15:55   167,347   14.3%
        15:45    35,984    3.1%

    The open time says 3:00 and is a BOUND ("nothing older than this"). The
    midpoint says 4:00 and is the answer ("this is when it came").

    Falls back to the bucket's own start whenever the answer is unknowable -
    no finer tape (5m, the finest, is its own answer anyway), an unusable
    stamp, or a bucket with no volume. Never raises.
    """
    try:
        start = int(bars[-1].get("time"))
    except (AttributeError, IndexError, KeyError, TypeError, ValueError):
        return 0
    if start <= 0:
        return 0
    span = _MIDPOINT_SPAN_SECONDS.get(key)
    fine_key = _finest_intraday_key(tapes)
    fine_span = RVOL_BUCKET_SECONDS.get(fine_key or "")
    if not span or not fine_span or fine_span >= span:
        # Nothing finer to look inside with. For the finest tape that is not a
        # limitation: the bar IS the moment.
        return start
    inside = []
    for row in _tape(tapes, fine_key):
        try:
            moment = int(row.get("time"))
            volume = float(row.get("volume") or 0.0)
        except (TypeError, ValueError):
            continue
        if start <= moment < start + span and math.isfinite(volume) and volume > 0:
            inside.append((moment, volume))
    if not inside:
        return start
    inside.sort()
    total = sum(volume for _, volume in inside)
    if total <= 0:
        return start
    running = 0.0
    for moment, volume in inside:
        running += volume
        if running >= total / 2.0:
            return moment
    return inside[-1][0]


def last_price(tapes: Any) -> float | None:
    """The newest finite intraday close -- the row's "Last".

    Public because the SCANNER's $3.00 price floor has to gate on the same
    number the row displays. It previously gated on the newest DAILY close,
    which premarket is yesterday's, so a stock trading 4.20 on a 2.60 prior
    close was blocked on 2.60 while the board showed 4.20.

    ``None`` when there is no usable intraday tape; the caller decides what a
    missing price means rather than having a default chosen for it here.
    """
    for value in reversed(_closes(_finest_intraday(tapes))):
        if math.isfinite(value):
            return value
    return None


def hour_high_low(tapes: Any) -> dict:
    """The last hour's true high and low off the finest intraday tape.

    From the bars' HIGH and LOW, never from closes: the highest close of the
    last hour is not the hour's high, and a card printing that next to the
    last price would be quietly wrong about a level he can see was touched.

    Total: no tape, an unknown resolution, or bars without usable numbers all
    give ``{"high": None, "low": None}`` rather than raising or inventing.
    """
    key = _finest_intraday_key(tapes)
    rows = _finest_intraday(tapes)
    if not key or not rows:
        return {"high": None, "low": None}
    window = rows[-HOUR_BARS_BY_TAPE.get(key, 1):]
    highs: list[float] = []
    lows: list[float] = []
    for bar in window:
        if not isinstance(bar, Mapping):
            continue
        for field, sink in (("high", highs), ("low", lows)):
            try:
                value = float(bar.get(field))
            except (TypeError, ValueError):
                continue
            if value == value:            # not NaN
                sink.append(value)
    return {
        "high": max(highs) if highs else None,
        "low": min(lows) if lows else None,
    }


def adx_cell(bars: Any, length: int = ADX_LENGTH) -> dict:
    """One timeframe's ADX / +DI / -DI snapshot, as the CHART draws it.

    RECORDED FACT, NOT A GRADE. Nothing here feeds the A+/A/B letter; this
    exists so the track record can later be asked whether "+DI crosses -DI
    with ADX rising" (INOD 2026-09-22: ADX 12 -> 35 at 13:45 as +DI 48.6
    crossed -DI 13.0, eleven minutes before an 11.9% run the letter graded B)
    is worth anything.

    Shape (stable keys, always)::

        {"plus": float|None, "minus": float|None, "adx": float|None,
         "prevPlus": ..., "prevMinus": ..., "prevAdx": ...,
         "cross": "bull"|"bear"|None,   # a DI cross ON THE LAST BAR
         "rising": bool,                # adx > prevAdx
         "strongPlus": bool,            # prevPlus <= 25 < plus
         "strongMinus": bool,
         "barAt": int|None}             # epoch seconds of THAT last bar

    ``barAt`` is the open time of the bar every other field was computed on.
    It is what lets the grade recorder latch a cross per bar: the last bar is
    still FORMING, so ``cross`` flickers bull -> None -> bull as the high /
    low / close move, and without a bar identity the same cross logs twice.

    Only the last ``ADX_TAIL_BARS`` bars are read (see that constant): the
    cell publishes the last two bars only, and Wilders decay makes anything
    further back invisible at this precision.

    ``cross``/``strong*`` are gated on the chart's own condition for drawing a
    signal: the last bar's ADX must be a real reading. That gate is why this
    cannot use ``Number(null) === 0`` style comparisons - a warm-up ``None``
    must never read as a +DI of 0 and fake a cross on the first bar of the
    study (see the "JS Number(null) false zero" lesson).
    """
    # Sliced BEFORE _rows() where the input is already a plain sequence:
    # _rows walks every element, so coercing 13,000 bars to throw 12,700 away
    # kept ~19 ms of the ~24 ms this call used to cost on a 30m tape.
    if isinstance(bars, Sequence) and not isinstance(bars, (str, bytes)):
        bars = bars[-ADX_TAIL_BARS:]
    rows = _rows(bars)[-ADX_TAIL_BARS:]
    highs, lows, closes, _volumes = _ohlcv(rows)
    lines = adx_lines(highs, lows, closes, length)
    plus_line, minus_line, adx_line = lines["plus"], lines["minus"], lines["adx"]

    def published(value: Any) -> float | None:
        return round(float(value), ADX_DIGITS) if isinstance(value, (int, float)) else None

    # The bar every field below belongs to. Taken from the SAME row the study
    # was computed on, so it can never drift from the reading it labels.
    bar_at: int | None = None
    if rows:
        try:
            bar_at = int(rows[-1].get("time"))
        except (AttributeError, KeyError, TypeError, ValueError):
            bar_at = None

    if len(adx_line) < 2:
        return {
            "plus": None, "minus": None, "adx": None,
            "prevPlus": None, "prevMinus": None, "prevAdx": None,
            "cross": None, "rising": False,
            "strongPlus": False, "strongMinus": False,
            "barAt": bar_at,
        }

    plus, minus, adx = plus_line[-1], minus_line[-1], adx_line[-1]
    prev_plus, prev_minus, prev_adx = plus_line[-2], minus_line[-2], adx_line[-2]

    # The chart's `if (!index || !Number.isFinite(adx[index])) return;`.
    drawable = adx is not None and None not in (plus, minus, prev_plus, prev_minus)
    cross: str | None = None
    strong_plus = strong_minus = False
    if drawable:
        if prev_plus <= prev_minus and plus > minus:
            cross = "bull"
        elif prev_minus <= prev_plus and minus > plus:
            cross = "bear"
        strong_plus = prev_plus <= ADX_STRONG < plus
        strong_minus = prev_minus <= ADX_STRONG < minus

    return {
        "plus": published(plus),
        "minus": published(minus),
        "adx": published(adx),
        "prevPlus": published(prev_plus),
        "prevMinus": published(prev_minus),
        "prevAdx": published(prev_adx),
        "cross": cross,
        "rising": adx is not None and prev_adx is not None and adx > prev_adx,
        "strongPlus": strong_plus,
        "strongMinus": strong_minus,
        "barAt": bar_at,
    }


def _finest_intraday(tapes: Any) -> list[Mapping[str, Any]]:
    """The finest-resolution tape actually present, for last/spark/quote trend."""
    for key in _INTRADAY_PREFERENCE:
        rows = _tape(tapes, key)
        if rows:
            return rows
    return []


def build_row(symbol: str, tapes: Any, industry: str | None = None) -> dict:
    """Assemble one board row from a dict of aggregated tapes.

    ``tapes`` is keyed by the payload-contract timeframe keys
    (``"5m"``, ``"15m"``, ``"30m"``, ``"1h"``, ``"2h"``, ``"4h"``, ``"D"``,
    ``"2D"``, ``"3D"``, ``"4D"``, ``"Wk"``, ``"M"``); each value is a bar tape
    (list of OHLCV mappings, or a feed DataFrame).

    ``scanPass`` / ``scanReasons`` are deliberately absent -- the scan module
    owns those keys.

    NEVER RAISES. A missing tape, a too-short tape, a tape full of strings and
    a tape that is ``None`` all yield null cells for the affected timeframes.
    A symbol with three bars of history still produces a complete, renderable
    row; that is the difference between one broken symbol and a blank board.

    WARM-UP POLICY (judgement call): a tape shorter than the study's window
    yields a NULL cell. For Skittles and Squeeze that matches TOS, which
    leaves those bars blank. For RVOL it does NOT: TOS's own
    ``if IsNaN(rawRelVol) then 0`` would print ``0.0`` on a black cell during
    warm-up. Null is used anyway so the board can tell "no data yet" apart
    from "genuinely average volume".
    """
    # Kept as a local: quoteTrend and sparkline below both read the same
    # finest tape, and resolving it three times would let them drift apart.
    intraday = _finest_intraday(tapes)
    last = last_price(tapes)

    rvol: dict[str, dict] = {}
    for key in RVOL_TIMEFRAMES:
        rows = _tape(tapes, key)
        if len(rows) < RVOL_MIN_BARS:
            rvol[key] = null_cell()
            continue
        # The displayed value is untouched thinkScript parity; "pace" rides
        # alongside it for the alert path, which needs to fire DURING a
        # forming bucket rather than after it closes.
        rvol[key] = rvol_cell(
            rows, elapsed=_bucket_elapsed_fraction(tapes, rows, key)
        )
        # WHEN THIS BAR OPENED. A high RVOL on a long timeframe means "this
        # bucket holds a lot of volume", not "volume is arriving now" - and
        # nothing on screen told them apart. FDX showed a cyan 2h 3.1 that had
        # arrived at 16:55 and sat unchanged for nearly two hours; HPE showed a
        # cyan 4h 3.1 that was the morning's volume while every fast column was
        # negative. The bucket start is already in the tape, so this needs no
        # clock and no elapsed fraction, and it works on EVERY timeframe
        # including 5m (which _bucket_elapsed_fraction cannot answer for).
        # WHEN the volume came - the volume-weighted midpoint inside the
        # bucket, not the bucket's open time. On FDX 2026-09-03 the 2h bucket
        # opened 15:00 and 67% of its volume landed in one 5-minute bar at
        # 16:00: "3:00" is a bound, "4:00" is the answer.
        came = _volume_midpoint_time(tapes, rows, key)
        if came > 0:
            rvol[key]["barAt"] = came

    squeeze: dict[str, dict] = {}
    for key in SQUEEZE_TIMEFRAMES:
        rows = _tape(tapes, key)
        squeeze[key] = squeeze_cell(rows) if len(rows) >= SQUEEZE_MIN_BARS else null_cell()

    skittles: dict[str, dict] = {}
    for key in SKITTLES_TIMEFRAMES:
        rows = _tape(tapes, key)
        skittles[key] = (
            skittles_cell(rows) if len(rows) >= SKITTLES_MIN_BARS else null_cell()
        )
        # HOW OLD the cross behind the colour can be. Deliberately the BAR's
        # own start, not a volume midpoint like the RVOL cells: Skittles has
        # no volume, and its colour comes from a cross that fired inside the
        # CURRENT bar (the script's `within 1 bars`, which is the identity -
        # see skittles_cell). So the bar's age is the most the cross can be.
        #
        # It cannot be narrowed further: a 4h EMA cross is defined on 4h
        # closes, so there is no finer truth to look up. Computing EMAs on a
        # finer tape would be a different study, not a better age.
        try:
            opened = int(rows[-1].get("time"))
        except (AttributeError, IndexError, KeyError, TypeError, ValueError):
            opened = 0
        if opened > 0:
            skittles[key]["barAt"] = opened

    two_hour = _tape(tapes, "2h")
    high_low = (
        high_low_cell(two_hour) if len(two_hour) >= HIGH_LOW_MIN_BARS else null_cell()
    )

    # Scanner grade inputs (spec 2026-09-21, Task 5). Both feed the Draft 2
    # grade computed later in build_board; neither may ever break the row -
    # momentum.summarize indexes bar fields directly and raises KeyError on a
    # malformed bar, and squeeze_column_series can do the same on a garbage
    # tape, so both are wrapped here rather than trusted to stay well-behaved.
    from momx import momentum  # local import: momentum imports columns
    try:
        m5 = momentum.summarize(tapes.get("5m") if isinstance(tapes, Mapping) else None, rvol)
    except Exception:  # noqa: BLE001 - a bad tape costs m5, never the row
        m5 = None
    # The BEAR reading of the same 5m tape (spec 2026-09-24). Same guard.
    try:
        m5_bear = momentum.summarize(
            tapes.get("5m") if isinstance(tapes, Mapping) else None, rvol, direction="bear"
        )
    except Exception:  # noqa: BLE001 - a bad tape costs m5Bear, never the row
        m5_bear = None
    try:
        sqz_raw = _sqz_raw(tapes)
    except Exception:  # noqa: BLE001 - a bad tape costs sqzRaw, never the row
        sqz_raw = {}

    # ADX / +DI / -DI, recorded only (2026-09-22). Wrapped for the same reason
    # as the two above: this is a new field whose only job is to be recorded,
    # and it must never be the reason a row goes missing from the board.
    try:
        adx = {key: adx_cell(_tape(tapes, key)) for key in ADX_TIMEFRAMES}
    except Exception:  # noqa: BLE001 - a bad tape costs adx, never the row
        adx = None

    return {
        "symbol": str(symbol or "").upper(),
        "industry": industry,
        "last": last,
        # intraday + last so the premarket case has a live price and a way to
        # tell that today's daily bar has not formed yet.
        "pctChange": pct_change(_tape(tapes, "D"), intraday, last),
        "rvol": rvol,
        "sqz": squeeze,
        "skittles": skittles,
        "highLow": high_low,
        "color": color_cell(),
        "quoteTrend": quote_trend(intraday),
        "sparkline": sparkline(intraday),
        # For the ticker card's right-hand column. Cheap: the tape is already
        # resolved above and this is one pass over at most twelve bars.
        "hourHighLow": hour_high_low(tapes),
        # Scanner grade inputs (spec 2026-09-21, Task 5). "grade" itself is
        # NOT set here - it needs the row's "news" (attached later in
        # build_board) so it is computed there, after that attach.
        "m5": m5,
        # Bear momentum; board._contract_row files it under row["bear"]["m5"].
        "m5Bear": m5_bear,
        "sqzRaw": sqz_raw,
        # Trend strength, RECORDED not graded (see adx_cell). None only when
        # the computation itself failed.
        "adx": adx,
    }
