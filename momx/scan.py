"""The "AlertX Bull Momo ALL_Jan26_MyWatchlist" scan, ported literally.

WHAT THIS SCAN IS (the trader's TOS scan, verbatim)
---------------------------------------------------
ALL of:
  1. Stock Last >= 3.00
  2. Price_Change(VOLUME) on 4h with EXT: at least 0.5% greater than 2 bars ago
  3. Price_Change(CLOSE)  on 1h with EXT: at least 0.3% greater than 2 bars ago
ANY of:
  A. On 2h(EXT), 4h(EXT), D, 2D, 3D, 4D, Wk, M - MACD(6,12,8) Value crosses
     above Avg, or EMA(9) crosses above EMA(20), or EMA(4) crosses above EMA(8)
  B. On 2h(EXT), 4h(EXT), D, Wk - the SqzFired SCANNER script
  C. On 5m, 15m, 30m, 1h, 2h, 4h, D - the RVOL SCANNER script
Output: sorted by % change descending, top 50.

REUSE (what this module computes vs. what it delegates)
-------------------------------------------------------
NOTHING about EMA/MACD/cross semantics is re-derived here.
:func:`momentum_cross` drives ``ganesh_higher_timeframe_signals._source_values``
(the chart-validated ema4/ema8/ema9/ema20/ema6/ema12/MACD bundle) bar by bar and
asks ``ganesh_higher_timeframe_signals._crossed_above`` whether the last bar
crossed - the exact pair of calls the signal engine makes at
``ganesh_higher_timeframe_signals.py:1379/1415/1440`` for the ganesh48,
ganesh920 and ganeshMacd families. That means D/2D/3D/4D/Wk/M answers are the
engine's answers by construction, not by imitation, and 2h/4h EXT get the same
recurrence for free. The engine works in "one committed state per closed
bucket"; a bar tape IS that sequence of closed buckets, so the only genuinely
new work for 2h/4h is the BUCKETING, which lives in :mod:`momx.buckets` and is
the caller's job.
Squeeze and RVOL come from :mod:`momx.indicators` (``ttm_squeeze``,
``rvol_zscore``); the only logic written here is the two recursive scanner
scripts' state machines, which exist nowhere else in the repo.

BAR TAPES
---------
Every ``bars`` argument is a chronologically ascending list of mappings with
``open``/``high``/``low``/``close``/``volume``. Each entry is ONE bar of that
timeframe (already bucketed - see :mod:`momx.buckets`). Every predicate is
evaluated on the LAST bar of the tape, which is what a TOS scan does. Whether
that last bar is the live forming bucket or the last closed one is the caller's
choice (``buckets.last_closed_bucket`` / ``buckets.forming_bucket``); TOS
itself scans the forming bar intraday.

This module must never import ``api_server``.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence  # abc, not typing: same isinstance answer, far cheaper (speed pass 2026-09-25)
from typing import Any, Iterable

from ganesh_higher_timeframe_signals import (
    _crossed_above as _ghts_crossed_above,
    _crossed_below as _ghts_crossed_below,
    _source_values as _ghts_source_values,
)
from momx.indicators import rvol_zscore, ttm_squeeze

__all__ = [
    "rank_board",
    "CLOSE_CHANGE_BARS_AGO",
    "CLOSE_CHANGE_MIN_PCT",
    "MOMENTUM_CROSS_NAMES",
    "MOMENTUM_TIMEFRAMES",
    "PRICE_FLOOR",
    "RANK_LIMIT",
    "RVOL_LENGTH",
    "RVOL_NUM_DEV",
    "RVOL_NUM_DEV_BY_TIMEFRAME",
    "rvol_num_dev",
    "RVOL_TIMEFRAMES",
    "SQUEEZE_LENGTH",
    "SQUEEZE_NBB",
    "SQUEEZE_NK_HIGH",
    "SQUEEZE_NK_MEDIUM",
    "SQUEEZE_TIMEFRAMES",
    "VOLUME_CHANGE_BARS_AGO",
    "VOLUME_CHANGE_MIN_PCT",
    "is_bear",
    "momentum_cross",
    "momentum_cross_names",
    "price_change_gate",
    "completed_bars",
    "price_floor",
    "rank_rows",
    "rvol_scan",
    "scan_symbol",
    "sqz_fired",
]

#: "Stock Last >= 3.00".
PRICE_FLOOR = 3.00


def is_bear(direction: Any) -> bool:
    """The one place the word is spelled. Anything but "bear" is bull.

    The BEAR scan (spec 2026-09-24) is this bull scan mirrored: crosses
    BELOW, squeezes firing with momentum FALLING, RVOL spikes where sellers
    won the bar, and a 1h close DROP instead of a rise. Every rule takes
    ``direction`` and defaults to bull, so existing callers are untouched.
    """
    return str(direction or "").strip().lower() == "bear"

#: Price_Change(VOLUME) on 4h EXT, at least +0.5% versus 2 bars ago.
VOLUME_CHANGE_MIN_PCT = 0.5

#: Verified directly in TOS's Stock Hacker editor on 2026-09-29:
#: Price_Change uses price[length], with this row's length set to 2.
#: Do not compensate for session/bucket differences by shifting the index.
VOLUME_CHANGE_BARS_AGO = 2


#: Price_Change(CLOSE) on 1h EXT, at least +0.3% versus 2 bars ago.
#: Briefly 0.25 on 2026-09-29 from a pasted note; his live Stock Hacker
#: screenshot the same evening reads 0.3, and the live scan is the spec.
CLOSE_CHANGE_MIN_PCT = 0.3

#: The live 1h filter also compares against price[2].
CLOSE_CHANGE_BARS_AGO = 2

#: RVOL scanner inputs: ``relVolLength = 50``, ``numDev = 3``.
RVOL_LENGTH = 50
RVOL_NUM_DEV = 3.0

#: Rows where his scan overrides numDev. Read off the Stock Hacker rows
#: 2026-09-03: 5m and 15m are configured at 5, every other row at 3.
#:
#: The pasted script text says `input numDev = 3` because that is the
#: study's DEFAULT; the override is per row in the scan configuration. We
#: applied 3 everywhere, so the two fastest and noisiest tapes fired
#: matches his scan never returns - extra rows on our board, never
#: missing ones.
RVOL_NUM_DEV_BY_TIMEFRAME = {"5m": 5.0, "15m": 5.0}


def rvol_num_dev(timeframe: Any) -> float:
    """The numDev his scan configures for ``timeframe``."""
    return RVOL_NUM_DEV_BY_TIMEFRAME.get(str(timeframe), RVOL_NUM_DEV)

#: TTM_Squeeze length; MS uses nk = 1.5, HS ("high compression") uses nk = 1.0.
SQUEEZE_LENGTH = 20
SQUEEZE_NK_MEDIUM = 1.5
SQUEEZE_NK_HIGH = 1.0
SQUEEZE_NBB = 2.0

#: Timeframe lists, in the scan's own row order.
MOMENTUM_TIMEFRAMES = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
SQUEEZE_TIMEFRAMES = ("2h", "4h", "D", "Wk")
RVOL_TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h", "D")

#: The three momentum crosses, in the order the scan lists them. Reasons are
#: emitted with these names so the trader can audit WHICH cross fired.
MOMENTUM_CROSS_NAMES = ("macd", "ema9x20", "ema4x8")

#: TOS "Show: 50, Sorted by: % change, Descending".
RANK_LIMIT = 50

_PRICE_CHANGE_SOURCES = ("open", "high", "low", "close", "volume")


# ----------------------------------------------------------------------
# bar access
# ----------------------------------------------------------------------


def _field(bar: Any, name: str) -> float:
    """One OHLCV field as a float, NaN when absent or unparseable.

    Accepts a mapping (the repo's bar shape) or any object exposing the field
    as an attribute. A bad field degrades one reading to NaN rather than
    raising, so a single gap in a broker tape cannot take down the whole scan.
    """
    if isinstance(bar, Mapping):
        value = bar.get(name)
    else:
        value = getattr(bar, name, None)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return float("nan")
    return number


def _series(bars: Iterable[Any] | None, name: str) -> list[float]:
    return [_field(bar, name) for bar in (bars or [])]


def _rows(bars: Iterable[Any] | None) -> list[Any]:
    return list(bars or [])


# ----------------------------------------------------------------------
# the ALL gates
# ----------------------------------------------------------------------


def price_floor(last: Any, minimum: float = PRICE_FLOOR) -> bool:
    """``Stock Last >= 3.00``.

    Boundary is INCLUSIVE - exactly 3.00 passes. A missing or unparseable last
    price fails: a symbol whose price is unknown cannot be shown to clear a
    price filter.
    """
    try:
        value = float(last)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(value):
        return False
    return value >= float(minimum)


def completed_bars(bars: Iterable[Any] | None, span_minutes: int, now_epoch: float) -> list:
    """The tape with a still-forming final bucket dropped.

    The Price_Change GATES evaluate on completed bars, matching observed
    thinkorswim scan behavior. Proven live 2026-08-31 at 09:23 ET: our
    developing 09:00 4h bucket held 23 minutes of volume (67k) against the
    full overnight bucket's 162k - an unwinnable comparison that blocked ALL
    ten Mag7 names while TOS showed AAPL passing. The same math is why the
    17:00-05:00 stretch looked like a volume-gate dead window all along
    (docs/momx/SPEC.md): a young evening bucket can never out-volume a full
    day bucket, yet TOS kept matching evenings - because TOS's gate never
    sees the developing bucket. Signals/columns still use the full tape
    (developing included), exactly as before; ONLY the two gates read this.
    """
    out = [b for b in (bars or [])]
    if out:
        try:
            start = float(out[-1].get("time"))
            if start + span_minutes * 60.0 > now_epoch:
                out = out[:-1]
        except (TypeError, ValueError, AttributeError):
            pass
    return out


def price_change_gate(
    bars: Iterable[Any] | None,
    source: str,
    min_pct: float,
    bars_ago: int = 2,
    direction: str = "bull",
) -> bool:
    """thinkorswim ``Price_Change`` scan row, as a percent-of-baseline test.

    ``direction="bear"`` flips the comparison to ``<= -min_pct`` (a DROP of at
    least ``min_pct``); the zero/negative-baseline rules are unchanged.

    ``100 * (x[0] / x[-bars_ago] - 1) >= min_pct``, where ``x[0]``
    is the LAST bar of the tape and ``source`` is ``"volume"`` or ``"close"``
    (``open``/``high``/``low`` are accepted too). The comparison is "at least",
    so a reading of exactly ``min_pct`` PASSES. No epsilon is applied: TOS
    compares the same IEEE-754 doubles, and a nominal "0.3%" move can land at
    0.29999999999999716 in either engine. Padding the threshold here would let
    rows through that TOS blocks.

    ZERO BASELINE (documented choice): a zero-volume bucket is real in
    extended hours, and ``x[-bars_ago] == 0`` makes the percentage undefined.
    TOS shows nothing there; this returns **False** rather than dividing by
    zero or treating "0 -> anything" as an infinite increase, because a
    volume-percent gate whose baseline is zero has measured nothing. A
    NEGATIVE baseline is impossible for volume or price but is also rejected -
    dividing by it would silently flip the direction of the comparison.
    A tape shorter than ``bars_ago + 1`` bars fails for the same reason: the
    reading does not exist.
    """
    key = str(source or "").strip().lower()
    if key not in _PRICE_CHANGE_SOURCES:
        raise ValueError(
            f"unsupported Price_Change source {source!r}; "
            f"expected one of {_PRICE_CHANGE_SOURCES}"
        )
    try:
        offset = int(bars_ago)
    except (TypeError, ValueError):
        return False
    if offset < 1:
        return False
    rows = _rows(bars)
    if len(rows) < offset + 1:
        return False
    current = _field(rows[-1], key)
    baseline = _field(rows[-1 - offset], key)
    if not (math.isfinite(current) and math.isfinite(baseline)):
        return False
    if baseline <= 0.0:
        return False
    try:
        threshold = float(min_pct)
    except (TypeError, ValueError):
        return False
    change = 100.0 * (current / baseline - 1.0)
    if is_bear(direction):
        return change <= -threshold
    return change >= threshold


# ----------------------------------------------------------------------
# ANY condition A - the three momentum crosses
# ----------------------------------------------------------------------


def _source_state_series(bars: Iterable[Any] | None) -> list[dict[str, float] | None]:
    """Per-bar EMA/MACD state, produced by the validated engine's own function.

    Each entry is ``ganesh_higher_timeframe_signals._source_values(previous,
    close)`` - the same "advance the committed bucket state by this bucket's
    close" step the engine performs. A bar whose close is unusable yields
    ``None`` and does NOT advance the state, matching the engine's treatment
    of a group with no close (``_literal_secondary_states_by_date`` leaves
    ``committed`` untouched for such a group).
    """
    states: list[dict[str, float] | None] = []
    committed: dict[str, float] | None = None
    for bar in _rows(bars):
        close = _field(bar, "close")
        if not math.isfinite(close):
            states.append(None)
            continue
        current = _ghts_source_values(committed, close)
        states.append(current)
        committed = current
    return states


def momentum_cross_names(bars: Iterable[Any] | None, direction: str = "bull") -> list[str]:
    """Which of the three bullish crosses fired ON THE LAST BAR.

    ``direction="bear"`` asks for the three crosses BELOW instead (the same
    ``_crossed_below`` the engine's PUT families use).

    Returns a subset of :data:`MOMENTUM_CROSS_NAMES` in scan order:
    ``macd`` (MACD(6,12,8) Value crosses above Avg), ``ema9x20``
    (MovAvgExponential() crosses above MovAvgExponential(20)) and ``ema4x8``
    (MovAvgExponential(4) crosses above MovAvgExponential(8)).

    CURRENT BAR ONLY. This scan has no ``within N bars`` wrapper and no
    ``Highest(..., 12)`` lookback - the Skittles COLUMN has the former and the
    premarket scanner has the latter, and conflating any of the three would be
    a parity bug. Do not add a lookback here.

    WARM-UP (documented, deliberately NOT guarded): the repo's EMAs are seeded
    by the first bar's close, so on bar 1 of a tape all six EMAs start from the
    same seed and a single up-bar registers as a cross. The signal engine
    suppresses that with its own ``completedBuckets`` prefetch (32/48/80
    buckets by family); this function does not, because a scanner tape's depth
    is the caller's decision and silently swallowing crosses would be a worse
    parity failure than an obvious warm-up artefact on a stub tape. Feed real
    history.
    """
    states = _source_state_series(bars)
    if len(states) < 2:
        return []
    current = states[-1]
    if not current:
        return []
    previous = None
    for state in reversed(states[:-1]):
        if state:
            previous = state
            break
    if not previous:
        return []
    crossed = _ghts_crossed_below if is_bear(direction) else _ghts_crossed_above
    fired: list[str] = []
    if crossed(
        previous["macdValue"],
        previous["macdAverage"],
        current["macdValue"],
        current["macdAverage"],
    ):
        fired.append("macd")
    if crossed(
        previous["ema9"], previous["ema20"], current["ema9"], current["ema20"]
    ):
        fired.append("ema9x20")
    if crossed(
        previous["ema4"], previous["ema8"], current["ema4"], current["ema8"]
    ):
        fired.append("ema4x8")
    return fired


def momentum_cross(bars: Iterable[Any] | None, direction: str = "bull") -> bool:
    """True when ANY of the three crosses fired on the last bar (bull: above; bear: below)."""
    return bool(momentum_cross_names(bars, direction))


# ----------------------------------------------------------------------
# ANY condition B - SqzFired, the SCANNER version
# ----------------------------------------------------------------------


def sqz_fired(bars: Iterable[Any] | None, direction: str = "bull") -> bool:
    """The SCANNER squeeze script. NOT the watchlist column version.

    ``direction="bear"`` mirrors it: ``lower = low < close[1]`` in place of
    ``higher``, and the direction latch wants the histogram FALLING.

    ::

        def higher = high > close[1];
        def MS  = TTM_Squeeze().SqueezeAlert == 0;
        def MF  = if !MS and MS[1] then 1 else 0;
        def MSF = if (!MS and MS[1]) or (!MS and MSF[1] > 0 and MSF[1] < 2)
                  then MSF[1]+1 else 0;
        def HS  = TTM_Squeeze(nk = 1.0).SqueezeAlert == 0;   (same shape)
        def MD  = TTM_Squeeze() > TTM_Squeeze()[1];
        def MFD = if MF then MD else MFD[1] and higher;      (HFD likewise)
        plot SqzFired = (HSF and HFD) or (MSF and MFD);

    THE TWO DIFFERENCES FROM THE COLUMN VERSION - do not "unify" them:
      1. ``higher`` compares against ``close[1]`` here and ``close[2]`` there.
      2. ``MFD``/``HFD`` latch as ``MFD[1] and higher`` here and as plain
         ``MFD[1]`` there, so the column keeps reporting fired through the
         two-bar window while the scanner drops it the moment a bar fails to
         trade above the previous close.
    ``tests/test_momx_scan.py`` pins a tape where the two disagree.

    ``MSF``/``HSF`` are numeric counters used in a boolean position, so they
    are true when non-zero. ``MSF[1]``/``MFD[1]`` on bar 0 have no prior bar
    and start at 0/False, and ``MS[1]`` on bar 0 is false, which makes bar 0
    unable to fire - correct, since a release needs a prior in-squeeze bar.
    The TTM histogram is NaN through its warm-up (roughly ``2*length - 1``
    bars), and a NaN comparison is false, so ``MD``/``HD`` are false there.
    """
    rows = _rows(bars)
    count = len(rows)
    if count < 2:
        return False
    bear = is_bear(direction)
    highs = _series(rows, "high")
    lows = _series(rows, "low")
    closes = _series(rows, "close")
    medium = ttm_squeeze(
        highs, lows, closes, SQUEEZE_LENGTH, SQUEEZE_NK_MEDIUM, SQUEEZE_NBB
    )
    high_compression = ttm_squeeze(
        highs, lows, closes, SQUEEZE_LENGTH, SQUEEZE_NK_HIGH, SQUEEZE_NBB
    )

    medium_streak = 0
    high_streak = 0
    medium_direction_latch = False
    high_direction_latch = False
    previous_medium_squeeze = False
    previous_high_squeeze = False
    fired = False

    for index in range(count):
        in_medium = medium.squeeze_alert[index] == 0
        in_high = high_compression.squeeze_alert[index] == 0
        # "higher" is the SCANNER's close[1] comparison (bear: "lower").
        higher = index >= 1 and (
            lows[index] < closes[index - 1] if bear else highs[index] > closes[index - 1]
        )

        medium_fire = (not in_medium) and previous_medium_squeeze
        high_fire = (not in_high) and previous_high_squeeze

        next_medium_streak = (
            medium_streak + 1
            if medium_fire or ((not in_medium) and 0 < medium_streak < 2)
            else 0
        )
        next_high_streak = (
            high_streak + 1
            if high_fire or ((not in_high) and 0 < high_streak < 2)
            else 0
        )

        # Momentum rising for bull, FALLING for bear.
        medium_rising = index >= 1 and (
            (medium.histogram[index] < medium.histogram[index - 1]) if bear
            else (medium.histogram[index] > medium.histogram[index - 1])
        )
        high_rising = index >= 1 and (
            (high_compression.histogram[index] < high_compression.histogram[index - 1]) if bear
            else (high_compression.histogram[index] > high_compression.histogram[index - 1])
        )

        medium_direction_latch = (
            medium_rising if medium_fire else (medium_direction_latch and higher)
        )
        high_direction_latch = (
            high_rising if high_fire else (high_direction_latch and higher)
        )

        medium_streak = next_medium_streak
        high_streak = next_high_streak
        previous_medium_squeeze = in_medium
        previous_high_squeeze = in_high

        fired = bool(high_streak and high_direction_latch) or bool(
            medium_streak and medium_direction_latch
        )
    return fired


# ----------------------------------------------------------------------
# ANY condition C - RVOL, the SCANNER version
# ----------------------------------------------------------------------


def rvol_scan(bars: Iterable[Any] | None, num_dev: float = RVOL_NUM_DEV, direction: str = "bull") -> bool:
    """The SCANNER RVOL script, evaluated on the last bar.

    ``direction="bear"``: ``selling > buying`` - the sellers won the spike bar.

    ::

        input relVolLength = 50;  input numDev = 3;
        def rawRelVol = (volume - Average(volume, 50)) / StDev(volume, 50);
        def isAboveThreshold = rawRelVol >= numDev;
        def buying  = volume * (close - low)  / (high - low);
        def selling = volume * (high - close) / (high - low);
        plot Scan = isAboveThreshold and buying > selling;

    Note this is NOT the RVOL COLUMN script: the column's buying/selling are
    the bare ``close - low`` / ``high - close`` distances, the scanner's are
    volume-weighted fractions of the bar's range. Both are kept.

    FLAT BAR (documented choice): when ``high == low`` the buying/selling
    fractions divide by zero. TOS yields NaN there and ``NaN > NaN`` is false,
    so this returns **False** for such a bar - a one-tick or halted bar cannot
    prove buyers outweighed sellers. (The COLUMN script has no division and
    would call that same bar bullish; that difference is intentional.)

    The z-score comes from ``momx.indicators.rvol_zscore``, which maps a NaN
    warm-up or a zero-stdev window to 0.0. The scanner script has no
    ``IsNaN`` guard, but ``NaN >= 3`` and ``0.0 >= 3`` are both false, so the
    outcome is identical.
    """
    rows = _rows(bars)
    if not rows:
        return False
    volumes = _series(rows, "volume")
    z_scores = rvol_zscore(volumes, RVOL_LENGTH)
    if not z_scores or z_scores[-1] < num_dev:
        return False

    high = _field(rows[-1], "high")
    low = _field(rows[-1], "low")
    close = _field(rows[-1], "close")
    volume = volumes[-1]
    if not all(math.isfinite(value) for value in (high, low, close, volume)):
        return False
    span = high - low
    if span <= 0.0:
        return False
    buying = volume * (close - low) / span
    selling = volume * (high - close) / span
    if is_bear(direction):
        return selling > buying
    return buying > selling


# ----------------------------------------------------------------------
# the whole scan
# ----------------------------------------------------------------------


def scan_symbol(
    row_tapes: Mapping[str, Iterable[Any]] | None,
    last: Any,
    direction: str = "bull",
) -> tuple[bool, list[str]]:
    """Run the full scan for one symbol.

    ``direction="bear"`` runs the mirrored scan: the 1h CLOSE gate becomes a
    drop, the crosses are crosses BELOW, SqzFired wants momentum falling and
    RVOL wants sellers. The 4h VOLUME gate and the price floor are the same.

    ``row_tapes`` maps timeframe keys (``"5m"``, ``"15m"``, ``"30m"``,
    ``"1h"``, ``"2h"``, ``"4h"``, ``"D"``, ``"2D"``, ``"3D"``, ``"4D"``,
    ``"Wk"``, ``"M"``) to bar tapes. A missing timeframe is simply not tested;
    it never invents a hit. The 2h/4h/1h tapes are expected to INCLUDE
    extended hours (the scan's EXT rows); the daily-and-slower tapes are
    regular-hours daily bars.

    Returns ``(passed, reasons)``. ``reasons`` lists every ANY-condition that
    fired, as ``"<condition>:<timeframe>"`` - ``"macd:4h"``, ``"ema9x20:D"``,
    ``"ema4x8:2h"``, ``"sqzfired:Wk"``, ``"rvol:30m"``. The specific cross is
    named, never a generic "momentum", because the trader audits these against
    TOS one cross at a time.

    Every ANY-condition is evaluated even after one has fired, so the reason
    list is a complete audit trail rather than a first-hit.

    WHEN AN ALL-GATE FAILS the row is blocked (``passed`` is False) but the
    reason list is still populated, and a ``"blocked:..."`` entry is PREPENDED
    naming the gate that stopped it: ``"blocked:price"``,
    ``"blocked:pricechange:volume:4h"``, ``"blocked:pricechange:close:1h"``.
    A caller rendering chips can filter on the ``blocked:`` prefix; these only
    ever appear when ``passed`` is False.
    """
    tapes: Mapping[str, Iterable[Any]] = row_tapes or {}

    blocked: list[str] = []
    if not price_floor(last):
        blocked.append("blocked:price")
    # Both Stock Hacker rows evaluate the current (possibly forming) bar.
    # The verified study is 100 * (price / price[length] - 1).
    if not price_change_gate(
        tapes.get("4h"),
        "volume", VOLUME_CHANGE_MIN_PCT, VOLUME_CHANGE_BARS_AGO,
    ):
        blocked.append("blocked:pricechange:volume:4h")
    if not price_change_gate(
        tapes.get("1h"),
        "close", CLOSE_CHANGE_MIN_PCT, CLOSE_CHANGE_BARS_AGO, direction,
    ):
        blocked.append("blocked:pricechange:close:1h")

    reasons: list[str] = []
    for timeframe in MOMENTUM_TIMEFRAMES:
        tape = tapes.get(timeframe)
        if tape is None:
            continue
        for name in momentum_cross_names(tape, direction):
            reasons.append(f"{name}:{timeframe}")
    for timeframe in SQUEEZE_TIMEFRAMES:
        tape = tapes.get(timeframe)
        if tape is not None and sqz_fired(tape, direction):
            reasons.append(f"sqzfired:{timeframe}")
    for timeframe in RVOL_TIMEFRAMES:
        tape = tapes.get(timeframe)
        # numDev is per ROW in his scan - 5 on 5m/15m, 3 elsewhere.
        if tape is not None and rvol_scan(tape, rvol_num_dev(timeframe), direction):
            reasons.append(f"rvol:{timeframe}")

    passed = not blocked and bool(reasons)
    return passed, (blocked + reasons if blocked else reasons)


# ----------------------------------------------------------------------
# ranking
# ----------------------------------------------------------------------


def _pct_change(row: Any) -> float | None:
    if isinstance(row, Mapping):
        value = row.get("pctChange")
    else:
        value = getattr(row, "pctChange", None)
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rank_board(
    rows: Sequence[Any] | None, limit: int | None = RANK_LIMIT, ascending: bool = False
) -> list[Any]:
    """Rank for DISPLAY without ever dropping a scan match.

    ``ascending=True`` is the BEAR board's order (biggest loser first).

    thinkorswim's "Show: 50" applies to the SCAN RESULT SET - it showed
    "Showing 16 of 16" for a 387-name watchlist. Our board also carries the
    non-matching rows (the panel has a "Scan matches only" toggle), and applying
    a 50-row cap across ALL of them sorted by %change silently deleted matches.

    Measured 2026-08-27, 355-symbol watchlist: TOS found 16, the board showed 2.
    The 10 we lost that were in our universe - TXN +1.82%, MS +0.36%,
    KVUE -0.10%, WELL -0.81%, PSX -1.00%, BBWI -1.32%, CAR -3.13% and others -
    all MATCHED, and were all cut because a 355-name universe has 50 movers with
    a bigger day gain than a match that is merely crossing. Only DDOG +6.70% and
    ARQQ +5.40% ranked high enough to survive.

    So: EVERY match is kept, ranked first; non-matches then fill whatever room
    is left up to ``limit``. A match is never dropped at all.

    ``limit`` therefore floors the row count, it does not cap it: 85 matches
    return 85 rows, 7 matches return 50 (7 + 43 movers). Until 2026-09-02 the
    cap applied to matches too, and matches 51..N were shipped in ``rest``
    instead - where NOTHING downstream could see them. ``rows`` is what
    _apply_matched_since stamps, what history.py archives, what momo_alert.py
    turns into a phone push and what the AI triage reads; a 2026-09-02 review
    measured 49 of 99 live matches (TSLA, MU, CRWD, IONQ, MARA...) silently
    outside all four. The board payload is unchanged in SIZE by this - a match
    just moves from ``rest`` to ``rows`` - so the cost is the alerts and
    archive entries the trader asked to start receiving, nothing else.
    """
    everything = list(rows or [])
    matches = [row for row in everything if _scan_passed(row)]
    others = [row for row in everything if not _scan_passed(row)]

    if limit is None:
        return rank_rows(matches, None, ascending) + rank_rows(others, None, ascending)
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        cap = RANK_LIMIT
    if cap <= 0:
        return rank_rows(matches, None, ascending) + rank_rows(others, None, ascending)

    # EVERY match, not rank_rows(matches, cap) - see the docstring.
    kept = rank_rows(matches, None, ascending)
    room = max(0, cap - len(kept))
    if room <= 0:
        # NOT rank_rows(others, 0): that function documents a non-positive
        # limit as "return everything" (it is how callers ask for the full
        # board). Passing the leftover room straight through therefore turned
        # a FULL cap into no cap at all - measured 2026-08-28 with 357 rows and
        # 119 matches, limit=50 returned 288 rows. The bug only appears once
        # matches reach the cap, which is exactly a busy session.
        return kept
    return kept + rank_rows(others, room, ascending)


def _scan_passed(row: Any) -> bool:
    try:
        return bool(row.get("scanPass"))
    except AttributeError:
        return False


def rank_rows(
    rows: Sequence[Any] | None, limit: int | None = RANK_LIMIT, ascending: bool = False
) -> list[Any]:
    """TOS "Show: 50, Sorted by: % change, Descending" (``ascending`` for bear).

    Sorts a COPY (the input order is left untouched) by ``pctChange``
    descending and truncates to ``limit``. Rows whose ``pctChange`` is null,
    missing or non-finite sort LAST, keeping their relative order - a symbol
    with no quote must not outrank one that fell 8%, and it must not silently
    disappear either. ``limit=None`` (or a non-positive limit) returns every
    row, for callers that want the full board and only display the top 50.
    """
    ordered = list(rows or [])
    # Python's sort is stable, so equal keys - including every null - keep the
    # order they arrived in.
    sign = 1.0 if ascending else -1.0
    ordered.sort(
        key=lambda row: (1, 0.0) if _pct_change(row) is None else (0, sign * _pct_change(row))
    )
    if limit is None:
        return ordered
    try:
        cap = int(limit)
    except (TypeError, ValueError):
        return ordered
    if cap <= 0:
        return ordered
    return ordered[:cap]
