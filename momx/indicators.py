"""Literal thinkScript indicator primitives for the MomoX watchlist/scanner.

PARITY RULES (do not "improve" anything in this file)
-----------------------------------------------------
This is a thinkorswim parity project: what TOS shows must be what the app
shows. Every function here is a direct port of a thinkScript built-in, with the
thinkScript conventions honoured exactly:

* ``StDev(x, n)`` is POPULATION standard deviation (divide by n).
* ``Average(x, n)`` is a SIMPLE moving average.
* ``ExpAverage``/``MovAvgExponential`` uses alpha = 2/(n+1) and, in THIS repo,
  is seeded by the FIRST value - not by an SMA of the first n bars. That
  convention is already settled in ``ganesh_higher_timeframe_signals._next_ema``
  and ``premarket_scanner.ema_series``, both validated against real TOS charts.
  ``ema()`` here reuses ``_next_ema`` directly so it can never drift.
* ``MovAvgExponential()`` with no length is length 9.
* ``TrueRange(high, close, low) = max(h-l, abs(h-c[1]), abs(l-c[1]))``.
* "x crosses above y" = ``x[1] <= y[1] and x > y``.

RETURN TYPE
-----------
Every function returns a plain Python ``list`` (``list[float]`` /
``list[bool]`` / ``list[int]``), one entry per input bar, aligned to the input.
Inputs may be lists, tuples, numpy arrays or pandas Series - anything
iterable of numbers. Lists (not Series) are the return type on purpose: the
two validated modules this file must agree with
(``ganesh_higher_timeframe_signals``, ``premarket_scanner``) speak
``list[float]``, and the spec for ``ttm_squeeze``/``crosses_above``/
``within_bars`` demands lists, so lists everywhere keeps one rule instead of
two.

WARM-UP
-------
Window functions (``sma``, ``stdev_pop``, ``wma``, ``linreg_endpoint``,
``stochastic_fast_k``) emit ``float('nan')`` until they have a full window,
exactly as TOS leaves the leading bars of a study blank. NaN propagates: a NaN
anywhere in a window makes that bar NaN. The two places where TOS itself
swallows the NaN - the RVOL column's ``if IsNaN(rawRelVol) then 0`` and the
squeeze test - do that swallowing explicitly and are documented at the call
site.

This module imports only the standard library plus
``ganesh_higher_timeframe_signals`` (pandas + config only). It must never
import ``api_server``.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any, Iterable, NamedTuple
import math
import os
import threading

from ganesh_higher_timeframe_signals import _next_ema as _ghts_next_ema

__all__ = [
    "sma",
    "stdev_pop",
    "ema",
    "wma",
    "true_range",
    "macd",
    "stochastic_fast_k",
    "stochastic_fast_d",
    "linreg_endpoint",
    "ttm_squeeze",
    "TTMSqueezeResult",
    "crosses_above",
    "crosses_below",
    "within_bars",
    "rvol_zscore",
    "wilders",
    "adx_lines",
    "ADX_LENGTH",
]

NAN = float("nan")


# ---------------------------------------------------------------------------
# input coercion
# ---------------------------------------------------------------------------

def _as_floats(values: Any) -> list[float]:
    """Coerce any bar sequence (list/tuple/ndarray/pandas Series) to floats.

    Anything that is not a finite number becomes NaN rather than raising, so a
    gap in a broker tape degrades one bar instead of the whole study.
    """
    if values is None:
        return []
    out: list[float] = []
    for value in values:
        try:
            number = float(value)
        except (TypeError, ValueError):
            out.append(NAN)
            continue
        out.append(number)
    return out


def _length(length: Any, default: int = 1) -> int:
    try:
        span = int(length)
    except (TypeError, ValueError):
        span = default
    return max(1, span)


def _finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


# ---------------------------------------------------------------------------
# moving averages / dispersion
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# the duplicate-work memo
# ---------------------------------------------------------------------------
#
# rvol_zscore and ttm_squeeze are each computed TWICE per symbol per timeframe:
# once by the scanner (scan.rvol_scan / scan.sqz_fired) and once by the display
# column (columns.rvol_cell / columns.squeeze_column_series), on the same bars
# with the same constants - scan.RVOL_TIMEFRAMES == columns.RVOL_TIMEFRAMES,
# scan.RVOL_LENGTH == columns.RVOL_LENGTH == 50, and likewise for the squeeze
# (("2h","4h","D","Wk"), length 20, nk 1.5 and 1.0). A 2026-09-02 review
# measured that duplication at ~29% of the pool's CPU: squeeze 29.7% and rvol
# 27.6% of the callpath, about half of each redundant. It only mattered for 50
# symbols until 2c31a65 moved build_row into the pool for all 357.
#
# The key is the INPUT VALUES, not the identity of the list holding them.
# Identity would never hit: scan._series and columns._ohlcv each build a fresh
# list, and they are not even interchangeable - columns._number accepts the
# "h"/"High" aliases that scan._field does not, so on an aliased tape they
# would legitimately disagree. Keying on the values makes a shared result
# impossible to get wrong: same numbers in, one computation; different numbers
# in (aliases, a re-fetched tape, anything), a miss and both sides compute as
# before. A hit is therefore bit-identical to no cache at all.
#
# Cost of the key is one O(n) tuple build against the several O(n*length)
# passes it may skip. Bounded LRU because a pool worker walks 71 symbols and
# must not accumulate their tapes; 64 entries covers one symbol's ~15 distinct
# (series, params) pairs several times over. NaN keys simply never match
# themselves (NaN != NaN), which costs a hit and can never cause a wrong one.
_MEMO_MAX = 64
_MEMO_LOCK = threading.Lock()
_RVOL_MEMO: "OrderedDict[Any, list[float]]" = OrderedDict()
_SQUEEZE_MEMO: "OrderedDict[Any, Any]" = OrderedDict()


def _memo_get(memo: "OrderedDict[Any, Any]", key: Any) -> Any:
    with _MEMO_LOCK:
        try:
            value = memo[key]
            memo.move_to_end(key)
            return value
        except KeyError:
            return None


def _memo_put(memo: "OrderedDict[Any, Any]", key: Any, value: Any) -> None:
    with _MEMO_LOCK:
        memo[key] = value
        while len(memo) > _MEMO_MAX:
            try:
                memo.popitem(last=False)
            except KeyError:  # pragma: no cover - empty under a race
                break


def memo_clear() -> None:
    """Drop both memos. For tests that assert on recomputation."""
    with _MEMO_LOCK:
        _RVOL_MEMO.clear()
        _SQUEEZE_MEMO.clear()
        _PREFIX_STORES.clear()


# ---------------------------------------------------------------------------
# PREFIX REUSE (2026-09-25 speed pass, "re-check only the newest bars")
# ---------------------------------------------------------------------------
# sma / stdev_pop / linreg_endpoint / the squeeze momentum source compute
# every output index from ITS OWN trailing window only - no running sums
# (those would drift from thinkScript). So between two builds of the same
# symbol, where the tape differs only in its last few bars, every output whose
# window lies entirely inside the common input prefix is the same arithmetic
# on the same numbers: bit-identical. We keep the previous (input, output)
# per series and recompute only from the first changed index. A different
# series (another symbol / timeframe) has a different head, so it simply
# misses; a matching head with a changed prefix only shortens the reuse.
# Verified end to end by tests/test_momx_prefix_reuse.py (0 diffs on real
# tapes, cold vs warm). MOMX_PREFIX_REUSE=0 turns it off.
_PREFIX_HEAD = 32
_PREFIX_MAX = 4096
_PREFIX_STORES: dict = {}
_PREFIX_ON = str(os.getenv("MOMX_PREFIX_REUSE", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _common_prefix(a: list, b: list) -> int:
    n = min(len(a), len(b))
    if a[:n] == b[:n]:
        return n
    lo, hi = 0, n  # a[:lo] == b[:lo], a[:hi] != b[:hi]
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if a[:mid] == b[:mid]:
            lo = mid
        else:
            hi = mid
    return lo


def _prefix_reuse(kind: Any, source: list) -> tuple[int, list]:
    """(how many leading outputs are reusable, those outputs)."""
    if not _PREFIX_ON or not source:
        return 0, []
    head = tuple(source[:_PREFIX_HEAD])
    with _MEMO_LOCK:
        store = _PREFIX_STORES.get(kind)
        entry = store.get(head) if store is not None else None
    if entry is None:
        return 0, []
    old_source, old_out = entry
    reuse = min(_common_prefix(old_source, source), len(old_out))
    return reuse, old_out[:reuse]


def _prefix_save(kind: Any, source: list, out: list) -> None:
    if not _PREFIX_ON or not source:
        return
    head = tuple(source[:_PREFIX_HEAD])
    with _MEMO_LOCK:
        store = _PREFIX_STORES.get(kind)
        if store is None:
            store = OrderedDict()
            _PREFIX_STORES[kind] = store
        store[head] = (source, list(out))
        store.move_to_end(head)
        while len(store) > _PREFIX_MAX:
            store.popitem(last=False)


def sma(values: Iterable[Any], length: Any = 1) -> list[float]:
    """thinkScript ``Average(values, length)`` - a SIMPLE moving average.

    The first ``length - 1`` bars are NaN (TOS leaves them blank).
    """
    source = _as_floats(values)
    span = _length(length)
    reuse, out = _prefix_reuse(("sma", span), source)
    bad = _nonfinite_prefix(source)
    for index in range(reuse, len(source)):
        if index + 1 < span:
            out.append(NAN)
            continue
        start = index + 1 - span
        if bad[index + 1] - bad[start]:
            out.append(NAN)
            continue
        out.append(sum(source[start:index + 1]) / span)
    _prefix_save(("sma", span), source, out)
    return out


def _nonfinite_prefix(source: list[float]) -> list[int]:
    """Running count of non-finite values, so "is any bar in this window bad?"
    is two lookups instead of a rescan.

    ``sma``, ``stdev_pop`` and ``ttm_squeeze``'s momentum loop each asked
    ``any(not isfinite(v) for v in window)`` once PER BAR, which is O(n*length)
    and was the single biggest cost in the pool: 16.8M math.isfinite calls and
    14.2s of a 45s cProfile of _scan_one over 24 real symbols (2026-09-02).
    ``prefix[b] - prefix[a]`` answers the same question for window [a, b) in
    constant time.

    Exact by construction - it counts a predicate, it does not touch the
    arithmetic. The sums stay as they were (a rolling sum would drift from
    thinkScript in the last decimals).
    """
    out = [0] * (len(source) + 1)
    total = 0
    for index, value in enumerate(source):
        if not math.isfinite(value):
            total += 1
        out[index + 1] = total
    return out


def stdev_pop(values: Iterable[Any], length: Any = 1) -> list[float]:
    """thinkScript ``StDev(values, length)`` - POPULATION standard deviation.

    Divides by n, NOT by n-1. thinkScript's StDev is the population form and
    the whole RVOL/squeeze parity depends on it; using the sample form would
    inflate every reading on short windows.
    """
    source = _as_floats(values)
    span = _length(length)
    reuse, out = _prefix_reuse(("stdev_pop", span), source)
    bad = _nonfinite_prefix(source)
    for index in range(reuse, len(source)):
        if index + 1 < span:
            out.append(NAN)
            continue
        start = index + 1 - span
        if bad[index + 1] - bad[start]:
            out.append(NAN)
            continue
        window = source[start:index + 1]
        mean = sum(window) / span
        variance = sum((value - mean) ** 2 for value in window) / span
        out.append(math.sqrt(variance) if variance > 0.0 else 0.0)
    _prefix_save(("stdev_pop", span), source, out)
    return out


def ema(values: Iterable[Any], length: Any = 9) -> list[float]:
    """thinkScript ``ExpAverage`` / ``MovAvgExponential`` (default length 9).

    Seeding: the FIRST value seeds the average, then alpha = 2/(length+1).
    This is NOT an SMA seed. The recurrence is delegated to
    ``ganesh_higher_timeframe_signals._next_ema`` so this module and the
    chart-validated signal engine can never disagree.
    """
    source = _as_floats(values)
    span = _length(length)
    # Prefix reuse (see _prefix_reuse): EMA[i] depends on inputs[0..i] only, so
    # the outputs over the common input prefix are the same recurrence on the
    # same numbers - bit-identical - and the recurrence resumes from the last
    # reused value.
    reuse, out = _prefix_reuse(("ema", span), source)
    previous: float | None = out[-1] if out else None
    for value in source[reuse:]:
        current = _ghts_next_ema(previous, value, span)
        out.append(current)
        previous = current
    _prefix_save(("ema", span), source, out)
    return out


def wma(values: Iterable[Any], length: Any = 1) -> list[float]:
    """Weighted moving average, thinkScript ``average type = WEIGHTED``.

    Weights are 1..n with n on the MOST RECENT bar (the oldest bar in the
    window gets weight 1). Denominator is n(n+1)/2.
    """
    source = _as_floats(values)
    span = _length(length)
    denominator = span * (span + 1) / 2.0
    out: list[float] = []
    for index in range(len(source)):
        if index + 1 < span:
            out.append(NAN)
            continue
        window = source[index + 1 - span:index + 1]
        if any(not math.isfinite(value) for value in window):
            out.append(NAN)
            continue
        total = 0.0
        for offset, value in enumerate(window):
            total += value * (offset + 1)
        out.append(total / denominator)
    return out


# ---------------------------------------------------------------------------
# range
# ---------------------------------------------------------------------------

def true_range(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
) -> list[float]:
    """thinkScript ``TrueRange(high, close, low)``.

    ``max(h - l, abs(h - c[1]), abs(l - c[1]))``. The first bar has no prior
    close, so it is simply ``h - l``.
    """
    high_values = _as_floats(highs)
    low_values = _as_floats(lows)
    close_values = _as_floats(closes)
    count = min(len(high_values), len(low_values), len(close_values))
    out: list[float] = []
    for index in range(count):
        high = high_values[index]
        low = low_values[index]
        if not (math.isfinite(high) and math.isfinite(low)):
            out.append(NAN)
            continue
        if index == 0:
            out.append(high - low)
            continue
        previous_close = close_values[index - 1]
        if not math.isfinite(previous_close):
            out.append(high - low)
            continue
        out.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    return out


# ---------------------------------------------------------------------------
# MACD
# ---------------------------------------------------------------------------

def macd(
    closes: Iterable[Any],
    fast: Any = 6,
    slow: Any = 12,
    signal: Any = 8,
) -> tuple[list[float], list[float]]:
    """thinkScript ``MACD(fast, slow, macd length)`` -> (Value, Avg).

    ``Value = ExpAverage(close, fast) - ExpAverage(close, slow)``
    ``Avg   = ExpAverage(Value, signal)``

    The trader's studies all use MACD(6, 12, 8), which is exactly what
    ``ganesh_higher_timeframe_signals._source_values`` already replays
    (ema6 - ema12, then ``_next_ema(macd_value, 8)``). ``tests/
    test_momx_indicators.py`` asserts the two agree bar for bar.
    """
    source = _as_floats(closes)
    fast_ema = ema(source, fast)
    slow_ema = ema(source, slow)
    value = [f - s for f, s in zip(fast_ema, slow_ema)]
    average = ema(value, signal)
    return value, average


# ---------------------------------------------------------------------------
# Stochastic (Skittles column)
# ---------------------------------------------------------------------------

def stochastic_fast_k(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    k_period: Any = 8,
) -> list[float]:
    """FastK = ``100 * (close - Lowest(low, k)) / (Highest(high, k) - Lowest(low, k))``.

    DIVIDE-BY-ZERO / FLAT-RANGE BEHAVIOUR (chosen, documented, tested):
    when ``Highest(high, k) == Lowest(low, k)`` the denominator is zero. TOS
    would produce a blank bar there; a blank would poison the WEIGHTED FastD
    average for ``d_period`` bars and blank the Skittles column on a genuinely
    flat, halted or one-tick instrument. This port returns **0.0** for that
    bar instead: a flat range means price is sitting at both the high and the
    low of the window, and 0.0 keeps the column rendering. This is the one
    deliberate deviation in the module and it only fires on a degenerate bar.
    """
    high_values = _as_floats(highs)
    low_values = _as_floats(lows)
    close_values = _as_floats(closes)
    span = _length(k_period)
    count = min(len(high_values), len(low_values), len(close_values))
    out: list[float] = []
    for index in range(count):
        if index + 1 < span:
            out.append(NAN)
            continue
        high_window = high_values[index + 1 - span:index + 1]
        low_window = low_values[index + 1 - span:index + 1]
        close = close_values[index]
        if (
            not math.isfinite(close)
            or any(not math.isfinite(value) for value in high_window)
            or any(not math.isfinite(value) for value in low_window)
        ):
            out.append(NAN)
            continue
        highest = max(high_window)
        lowest = min(low_window)
        span_range = highest - lowest
        if span_range <= 0.0:
            out.append(0.0)
            continue
        out.append(100.0 * (close - lowest) / span_range)
    return out


def stochastic_fast_d(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    k_period: Any = 8,
    d_period: Any = 8,
) -> list[float]:
    """thinkScript ``StochasticFast(... "average type" = "WEIGHTED")."FastD"``.

    FastD is the WEIGHTED moving average of FastK over ``d_period`` bars, not
    a simple average. The Skittles column plots this value.
    """
    return wma(
        stochastic_fast_k(highs, lows, closes, k_period),
        d_period,
    )


# ---------------------------------------------------------------------------
# Inertia / linear regression endpoint (TTM_Squeeze histogram)
# ---------------------------------------------------------------------------

def linreg_endpoint(values: Iterable[Any], length: Any = 20) -> list[float]:
    """thinkScript ``Inertia(values, length)``.

    The ENDPOINT of a least-squares linear regression fitted to the trailing
    ``length`` points: fit y = a + b*x over x = 0..length-1 and return the
    fitted value at x = length-1 (the current bar), NOT the slope and NOT a
    moving average. On a perfectly straight input line this returns the input
    exactly, which is the test that separates it from an average.
    """
    source = _as_floats(values)
    span = _length(length)
    out: list[float] = []
    if span == 1:
        return [value if math.isfinite(value) else NAN for value in source]

    x_values = list(range(span))
    x_mean = sum(x_values) / span
    x_variance = sum((x - x_mean) ** 2 for x in x_values)

    reuse, out = _prefix_reuse(("linreg", span), source)
    for index in range(reuse, len(source)):
        if index + 1 < span:
            out.append(NAN)
            continue
        window = source[index + 1 - span:index + 1]
        if any(not math.isfinite(value) for value in window):
            out.append(NAN)
            continue
        y_mean = sum(window) / span
        covariance = sum(
            (x_values[offset] - x_mean) * (window[offset] - y_mean)
            for offset in range(span)
        )
        slope = covariance / x_variance if x_variance else 0.0
        intercept = y_mean - slope * x_mean
        out.append(intercept + slope * (span - 1))
    _prefix_save(("linreg", span), source, out)
    return out


# ---------------------------------------------------------------------------
# TTM_Squeeze
# ---------------------------------------------------------------------------

class TTMSqueezeResult(NamedTuple):
    """``squeeze_alert`` is 0 while IN squeeze, 1 otherwise (TOS convention).

    The thinkScript the trader supplied tests ``TTM_Squeeze().SqueezeAlert == 0``
    to mean "in squeeze", so the 0/1 polarity here is deliberately inverted
    relative to how a boolean would read. ``histogram`` is the default
    TTM_Squeeze plot (the momentum bars).
    """

    squeeze_alert: list[int]
    histogram: list[float]


def ttm_squeeze(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    length: Any = 20,
    nk: Any = 1.5,
    nbb: Any = 2.0,
) -> TTMSqueezeResult:
    """TTM_Squeeze: Bollinger width inside Keltner width, plus the momentum plot.

    In squeeze when ``nbb * StDev(close, length) <= nk * Average(TrueRange, length)``
    (the Bollinger band sits inside the Keltner channel). ``nk = 1.0`` is the
    TIGHTER "high compression" channel the trader's column calls ``HS``; the
    1.5 default is the ``MS`` channel.

    Warm-up bars, where either side is NaN, report 1 (NOT in squeeze). A blank
    study bar is not evidence of compression, and reporting 0 there would make
    every symbol fire a fake squeeze-release on bar ``length``.

    ``histogram`` = ``Inertia(close - ((Highest(high, length) + Lowest(low, length)) / 2
    + Average(close, length)) / 2, length)``.
    """
    high_values = _as_floats(highs)
    low_values = _as_floats(lows)
    close_values = _as_floats(closes)
    span = _length(length)
    count = min(len(high_values), len(low_values), len(close_values))
    high_values = high_values[:count]
    low_values = low_values[:count]
    close_values = close_values[:count]

    try:
        keltner_factor = float(nk)
    except (TypeError, ValueError):
        keltner_factor = 1.5
    try:
        bollinger_factor = float(nbb)
    except (TypeError, ValueError):
        bollinger_factor = 2.0

    key = (
        span,
        keltner_factor,
        bollinger_factor,
        tuple(high_values),
        tuple(low_values),
        tuple(close_values),
    )
    cached = _memo_get(_SQUEEZE_MEMO, key)
    if cached is not None:
        return cached

    close_stdev = stdev_pop(close_values, span)
    average_true_range = sma(true_range(high_values, low_values, close_values), span)

    squeeze_alert: list[int] = []
    for index in range(count):
        deviation = close_stdev[index]
        atr = average_true_range[index]
        if not (math.isfinite(deviation) and math.isfinite(atr)):
            squeeze_alert.append(1)
            continue
        in_squeeze = (bollinger_factor * deviation) <= (keltner_factor * atr)
        squeeze_alert.append(0 if in_squeeze else 1)

    close_average = sma(close_values, span)
    # Prefix reuse (see _prefix_reuse): each momentum value reads only its own
    # trailing window of high / low / close, so the common prefix of all three
    # is reusable bit for bit.
    triples = list(zip(high_values, low_values, close_values))
    reuse, momentum_source = _prefix_reuse(("ttm_mom", span), triples)
    # Same O(1) window check as sma/stdev_pop - see _nonfinite_prefix.
    bad_high = _nonfinite_prefix(high_values)
    bad_low = _nonfinite_prefix(low_values)
    for index in range(reuse, count):
        if index + 1 < span or not math.isfinite(close_average[index]):
            momentum_source.append(NAN)
            continue
        start = index + 1 - span
        close = close_values[index]
        if (
            not math.isfinite(close)
            or bad_high[index + 1] - bad_high[start]
            or bad_low[index + 1] - bad_low[start]
        ):
            momentum_source.append(NAN)
            continue
        # Sliced only for the bars that survive the check above; before the
        # 2026-09-02 rewrite both windows were built for EVERY bar just to be
        # scanned for non-finites and thrown away.
        donchian_mid = (
            max(high_values[start:index + 1]) + min(low_values[start:index + 1])
        ) / 2.0
        baseline = (donchian_mid + close_average[index]) / 2.0
        momentum_source.append(close - baseline)
    _prefix_save(("ttm_mom", span), triples, momentum_source)

    result = TTMSqueezeResult(
        squeeze_alert=squeeze_alert,
        histogram=linreg_endpoint(momentum_source, span),
    )
    # Safe to hand the SAME object to every caller: TTMSqueezeResult is a
    # NamedTuple and nothing in momx mutates its lists (verified by grep for
    # .squeeze_alert / .histogram assignment). rvol_zscore returns a copy
    # because its result IS a plain list.
    _memo_put(_SQUEEZE_MEMO, key, result)
    return result


# ---------------------------------------------------------------------------
# crosses
# ---------------------------------------------------------------------------

def crosses_above(a: Iterable[Any], b: Iterable[Any]) -> list[bool]:
    """thinkScript ``a crosses above b``: ``a[i-1] <= b[i-1] and a[i] > b[i]``.

    Touching then breaking up (equal on the prior bar) DOES fire; equal on
    both bars does NOT. Bar 0 can never fire - there is no prior bar. Any
    non-finite value in the four inputs suppresses the cross, matching
    ``ganesh_higher_timeframe_signals._crossed_above``.
    """
    left = _as_floats(a)
    right = _as_floats(b)
    count = min(len(left), len(right))
    out: list[bool] = [False] * count
    for index in range(1, count):
        values = (left[index - 1], right[index - 1], left[index], right[index])
        if not all(_finite(value) for value in values):
            continue
        out[index] = values[0] <= values[1] and values[2] > values[3]
    return out


def crosses_below(a: Iterable[Any], b: Iterable[Any]) -> list[bool]:
    """thinkScript ``a crosses below b``: ``a[i-1] >= b[i-1] and a[i] < b[i]``."""
    left = _as_floats(a)
    right = _as_floats(b)
    count = min(len(left), len(right))
    out: list[bool] = [False] * count
    for index in range(1, count):
        values = (left[index - 1], right[index - 1], left[index], right[index])
        if not all(_finite(value) for value in values):
            continue
        out[index] = values[0] >= values[1] and values[2] < values[3]
    return out


def within_bars(flags: Iterable[Any], n: Any = 1) -> list[bool]:
    """thinkScript ``... within n bars``: an ``n``-candle window.

    The window spans ``n`` bars INCLUDING the current one, so it looks back
    ``n - 1``. From the official reference (Reserved Words -> within):

        "checks if the specified condition is true at least one time for the
         given number of bars STARTING FROM THE CURRENT ONE"

        ``Doji() within 3 bars`` -> "at least one Doji among three candles
        INCLUDING THE CURRENT ONE"

    So ``within_bars(flags, 1)`` is the IDENTITY -- the current bar only, i.e.
    a plain cross -- and ``within_bars(flags, 3)`` spans the current bar plus
    the two before it.

    This was previously implemented as an ``n + 1`` wide window, under a
    docstring that asserted that in capital letters and a test that pinned it.
    Every Skittles MACD/EMA cross therefore stayed lit one bar too long, and on
    a bar where a cross reversed, the cell painted the OPPOSITE colour. Fixed
    2026-09-01 after fetching the reference; the emphatic comment was not
    evidence, which is the same lesson the RVOL column taught the same day.

    ``n`` below 1 is clamped to a single-bar window rather than an empty one:
    thinkScript has no zero-bar window, and returning all-False for ``n = 0``
    would silently blank the column instead of failing loudly.
    """
    source = list(flags or [])
    lookback = 0
    try:
        lookback = max(0, int(n) - 1)
    except (TypeError, ValueError):
        lookback = 0
    out: list[bool] = []
    for index in range(len(source)):
        start = max(0, index - lookback)
        out.append(any(bool(value) for value in source[start:index + 1]))
    return out


# ---------------------------------------------------------------------------
# RVOL
# ---------------------------------------------------------------------------

def rvol_ratio(volumes: Iterable[Any], length: Any = 50) -> list[float]:
    """``volume / Average(volume, length)`` -- the RATIO thinkorswim DISPLAYS.

    The trader's watchlist RVOL column shows a ratio ("2.2" = 2.2x the 50-bar
    average volume), NOT the z-score his pasted script text computes. Confirmed
    live 2026-08-28: his TOS watchlist read RVOL 1-5 across many names at once,
    which is impossible for z-scores (half a watchlist at 2-5 sigma) but normal
    for ratios; MSFT's 2h ratio matched his TOS 2.2 exactly. So the COLUMN uses
    this. The SCANNER RVOL condition is a separate z-score and is unchanged.

    A warm-up or zero-average window returns 0.0, matching the column's
    IsNaN -> 0 guard.
    """
    source = _as_floats(volumes)
    span = _length(length)
    averages = sma(source, span)
    out: list[float] = []
    for index in range(len(source)):
        average = averages[index]
        value = source[index]
        if (
            not math.isfinite(value)
            or not math.isfinite(average)
            or average == 0.0
        ):
            out.append(0.0)
            continue
        ratio = value / average
        out.append(ratio if math.isfinite(ratio) else 0.0)
    return out


def rvol_zscore(volumes: Iterable[Any], length: Any = 50) -> list[float]:
    """``(volume - Average(volume, length)) / StDev(volume, length)``, NaN -> 0.0.

    The trader's RVOL column does ``if IsNaN(rawRelVol) then 0 else rawRelVol``,
    so warm-up bars AND a zero-stdev (perfectly flat volume) window both come
    back as 0.0 rather than NaN or an infinity. Note this returns the RAW
    z-score; the column rounds to 1 decimal at display time, the scanner
    compares it against ``numDev`` unrounded.
    """
    source = _as_floats(volumes)
    span = _length(length)
    key = (span, tuple(source))
    cached = _memo_get(_RVOL_MEMO, key)
    if cached is not None:
        return list(cached)
    averages = sma(source, span)
    deviations = stdev_pop(source, span)
    out: list[float] = []
    for index in range(len(source)):
        average = averages[index]
        deviation = deviations[index]
        if (
            not math.isfinite(source[index])
            or not math.isfinite(average)
            or not math.isfinite(deviation)
            or deviation == 0.0
        ):
            out.append(0.0)
            continue
        raw = (source[index] - average) / deviation
        out.append(raw if math.isfinite(raw) else 0.0)
    _memo_put(_RVOL_MEMO, key, out)
    return list(out)


# ---------------------------------------------------------------------------
# ADX / DMI  --  CHART PARITY PORT
# ---------------------------------------------------------------------------
#
# A line-by-line port of the chart's own study, so the scanner records exactly
# what the trader is looking at:
#
#   frontend/src/App.jsx  calculateMtfAdxAverage()  -> wilders()
#   frontend/src/App.jsx  calculateMtfAdxLines()    -> adx_lines()
#
# Deliberate departures from the rest of this module, all in the name of that
# parity - do not "tidy" them into the house style:
#
# * WARM-UP IS ``None``, NOT ``NaN``. The JS leaves those bars ``null`` and the
#   caller distinguishes "no reading" from a number; NaN would silently pass a
#   ``> 25`` test as False while also passing ``is not None``.
# * ``true_range()`` ABOVE IS NOT REUSED. It falls back to ``h - l`` when the
#   previous close is non-finite; the chart's TR does not (it produces NaN, via
#   Math.max). One gap bar would then put the two engines on different numbers.
# * ``max()`` IS GUARDED FOR NaN. ``Math.max`` in JS returns NaN if ANY
#   argument is NaN; Python's ``max`` returns whichever value the comparisons
#   happen to favour. The guard below reproduces the JS.
#
# Proven equal to the JS to < 1e-6 by scripts/validate_adx_against_chart.py.

#: The chart's default ADX length (App.jsx mtfAdxLength), and what the scanner
#: records on every timeframe.
ADX_LENGTH = 10


def wilders(values: Iterable[Any], length: Any = 1) -> list[float | None]:
    """Wilder's smoothing, seeded by a SIMPLE average of the first ``length``
    finite values, then ``previous = (previous * (length - 1) + value) / length``.

    Port of ``calculateMtfAdxAverage(values, period, "WILDERS")``. Non-finite
    inputs are SKIPPED (they neither seed nor advance the recurrence) and leave
    ``None`` at their own index, exactly as the JS leaves ``null`` there.
    Returns one entry per input value; ``None`` until the seed is full.
    """
    source = list(values) if values is not None else []
    span = _length(length)
    out: list[float | None] = [None] * len(source)
    seed: list[float] = []
    previous: float | None = None
    for index, raw in enumerate(source):
        if not _finite(raw):
            continue
        value = float(raw)
        if previous is None:
            seed.append(value)
            if len(seed) < span:
                continue
            # Left-to-right sum from 0, like the JS reduce, so the seed is
            # bit-identical rather than merely close.
            previous = sum(seed[-span:]) / span
        else:
            previous = (previous * (span - 1) + value) / span
        out[index] = previous
    return out


def _js_max(*candidates: float) -> float:
    """``Math.max`` semantics: NaN anywhere wins (Python's ``max`` does not)."""
    for candidate in candidates:
        if math.isnan(candidate):
            return NAN
    return max(candidates)


def adx_lines(
    highs: Iterable[Any],
    lows: Iterable[Any],
    closes: Iterable[Any],
    length: Any = ADX_LENGTH,
) -> dict[str, list[float | None]]:
    """+DI / -DI / ADX, aligned to the input bars.

    Port of ``calculateMtfAdxLines(bars, {mtfAdxLength: length})``:

    * ``TR = max(h - l, |h - c[1]|, |l - c[1]|)``; the first bar is ``h - l``.
    * ``+DM`` = up move when it beats the down move and is positive, else 0;
      ``-DM`` the mirror. The first bar is 0 / 0.
    * ``+DI = 100 * Wilders(+DM) / Wilders(TR)``, ``-DI`` likewise, and only
      where the smoothed TR is finite and > 0 (else ``None``).
    * ``DX = 100 * |+DI - -DI| / (+DI + -DI)`` (0 when the sum is 0).
    * ``ADX = Wilders(DX)``.

    NEVER RAISES. Fewer than two bars - or no bars at all - returns three EMPTY
    lists (the JS's early return); a tape shorter than ``length`` returns lists
    of ``None``. ``length`` is clamped to 1..100 and a falsy/unreadable value
    becomes the chart's default, both as the JS does.
    """
    high_values = _as_floats(highs)
    low_values = _as_floats(lows)
    close_values = _as_floats(closes)
    count = min(len(high_values), len(low_values), len(close_values))
    if count < 2:
        return {"plus": [], "minus": [], "adx": []}
    try:
        span = int(length)
    except (TypeError, ValueError):
        span = ADX_LENGTH
    span = max(1, min(100, span or ADX_LENGTH))

    true_ranges: list[float] = []
    plus_dm: list[float] = []
    minus_dm: list[float] = []
    for index in range(count):
        high = high_values[index]
        low = low_values[index]
        if index == 0:
            true_ranges.append(high - low)
            plus_dm.append(0.0)
            minus_dm.append(0.0)
            continue
        previous_close = close_values[index - 1]
        up_move = high - high_values[index - 1]
        down_move = low_values[index - 1] - low
        true_ranges.append(
            _js_max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)

    atr = wilders(true_ranges, span)
    smooth_plus = wilders(plus_dm, span)
    smooth_minus = wilders(minus_dm, span)

    def directional(smoothed: list[float | None]) -> list[float | None]:
        out: list[float | None] = []
        for index in range(count):
            band = atr[index]
            if band is None or not math.isfinite(band) or band <= 0:
                out.append(None)
                continue
            # `Number(smoothPlus[index] || 0)` in the JS: a null smoothed DM
            # reads as 0, it does not blank the line.
            out.append(100.0 * (smoothed[index] or 0.0) / band)
        return out

    plus = directional(smooth_plus)
    minus = directional(smooth_minus)

    dx: list[float | None] = []
    for index in range(count):
        plus_value = plus[index]
        minus_value = minus[index]
        if plus_value is None or minus_value is None:
            dx.append(None)
            continue
        total = plus_value + minus_value
        dx.append(100.0 * abs(plus_value - minus_value) / total if total > 0 else 0.0)

    return {"plus": plus, "minus": minus, "adx": wilders(dx, span)}
