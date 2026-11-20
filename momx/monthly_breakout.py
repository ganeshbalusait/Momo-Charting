"""Monthly Breakout / Monthly Breakdown - two derived MomX lists (2026-11-20).

Ganesh, after the back-tests in chat: "Build in scanner dropdown? For bull and
bear?" - the four chart patterns from his pictures, on the MONTHLY chart:

  BOTTOM  higher lows + flat resistance ("Trendline + Resistance")
  SYM     symmetrical triangle (lower highs AND higher lows)
  MIDDLE  range (flat resistance AND flat support)
  TOP     lower highs + flat support ("Trendline + Support")

Each can break UP (close above the line) or DOWN (close below it).
"Monthly Breakout" (bull) = the Watchlist's tickers with an UP break;
"Monthly Breakdown" (bear) = the ones with a DOWN break. Membership is
recomputed on every read from the monthly bars the EMA Touch refresher already
fetches (momx/ema_touch.py), so nothing is fetched twice and nothing is saved.

THE RULES (identical to the back-test, artifacts in chat 2026-11-20):
* Swing points: a month whose high (low) is the highest (lowest) of the two
  months either side. A swing only counts once the two months after it exist,
  so the current month can never confirm its own pattern (no look-ahead).
* A base is 10..72 months before the breakout month; the LONGEST base that
  fits wins. Its top/bottom must be within 3x of each other (a base, not a
  trend); a range must be within 2x.
* A flat line = at least 2 swing points within 6% of the base's high (low).
* Rising lows / falling highs: >= 2 swing points after the first touch of the
  flat line, a fitted line sloping the right way, each step no worse than 3%.
* The break: the month's CLOSE crosses the line (last month's close did not).
* EMA filter (required): UP needs close > 21 EMA > 50 EMA on the breakout
  month; DOWN needs close < 21 EMA < 50 EMA. Without it the back-test showed
  no edge (SYM up: 68% -> 74%; BOTTOM up 76% -> 78%).
* Status: the current month is "live" (not closed - it can still fail);
  last month is "confirmed". Both are list members.
* The scan on top (2026-11-20, "Use 'all of the following'"): like the EMA
  Touch lists, the break is an extra ALL condition, the EMA lists' stricter
  Price_Change gates apply (VOLUME 4h >= 1.0%, CLOSE 1h >= 0.5%,
  service._gate_kwargs), and ANY of the following is the Watchlist's.

Back-test, 338 Watchlist tickers, 12 months later, EMA filter on: BOTTOM up
78% / +21%, SYM up 74% / +20%, MIDDLE up 78% / +17%, TOP up 69% / +15%;
any month 62% / +13%. Breakdowns did NOT predict a fall (price was higher a
year later 55-64% of the time) - the bear list is shown for information.
"""

from __future__ import annotations

import bisect
import os
from typing import Any, Iterable, Mapping, Sequence

BULL_LIST = "Monthly Breakout"
BEAR_LIST = "Monthly Breakdown"
DIRECTIONS = {BULL_LIST: "up", BEAR_LIST: "down"}

PIVOT_SPAN = 2          # months either side of a swing point
LEVEL_TOLERANCE = 0.06  # touches within 6% are the same line
STEP_SLACK = 0.03       # a "higher low" may be at most 3% lower than the last
MIN_BASE = 10           # months
MAX_BASE = 72           # months
MAX_BASE_RATIO = 3.0    # base high / base low
MAX_RANGE_RATIO = 2.0
#: Fewer real months than this and the 50 EMA seed still weighs ~9%+; left out.
MIN_MONTHS = 60
#: Which pattern a month is labelled with when several fire (back-test order).
PATTERN_ORDER = ("BOTTOM", "SYM", "MIDDLE", "TOP")
PATTERN_LABELS = {
    "BOTTOM": "Higher lows + resistance",
    "SYM": "Symmetrical triangle",
    "MIDDLE": "Range",
    "TOP": "Lower highs + support",
}


def enabled() -> bool:
    # Same switch as the EMA lists: they share one data feed.
    return os.environ.get("AGX_MOMX_EMA_LISTS", "1").strip() != "0"


def current_lists() -> dict[str, str]:
    """Visible list name -> direction, in picker order."""
    return dict(DIRECTIONS) if enabled() else {}


def list_names() -> list[str]:
    return list(current_lists())


def parse_name(name: Any) -> str | None:
    wanted = str(name or "").strip().lower()
    return next((n for n in DIRECTIONS if n.lower() == wanted), None)


def is_derived(name: Any) -> bool:
    return enabled() and parse_name(name) is not None


# ---------------------------------------------------------------------------
# the detector (pure)
# ---------------------------------------------------------------------------


def ema(values: Sequence[float], length: int) -> list[float]:
    """thinkorswim ExpAverage: alpha 2/(n+1), seeded with the first value."""
    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    for value in values:
        out.append(value if not out else out[-1] + alpha * (value - out[-1]))
    return out


def _fit(points: list[tuple[int, float]]) -> tuple[float, float]:
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    den = sum((x - mx) ** 2 for x, _ in points) or 1.0
    slope = sum((x - mx) * (y - my) for x, y in points) / den
    return slope, my - slope * mx


def _stepping(values: list[float], rising: bool) -> bool:
    pairs = zip(values, values[1:])
    if rising:
        return all(b > a * (1 - STEP_SLACK) for a, b in pairs)
    return all(b < a * (1 + STEP_SLACK) for a, b in pairs)


def _swings(highs: Sequence[float], lows: Sequence[float]) -> tuple[list[int], list[int]]:
    n, k = len(highs), PIVOT_SPAN
    swing_highs = [j for j in range(k, n - k) if highs[j] == max(highs[j - k:j + k + 1])]
    swing_lows = [j for j in range(k, n - k) if lows[j] == min(lows[j - k:j + k + 1])]
    return swing_highs, swing_lows


def breaks_at(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float], i: int,
              swings: tuple[list[int], list[int]] | None = None) -> dict[tuple[str, str], tuple[float, float]]:
    """Every (pattern, direction) whose line month ``i``'s close crosses.

    Value = (line level at month i, base height). Uses only months before
    ``i`` to draw the lines, and only swings confirmed before ``i``.
    """
    found: dict[tuple[str, str], tuple[float, float]] = {}
    if i < MIN_BASE + 2 or i >= len(closes):
        return found
    swing_highs, swing_lows = swings or _swings(highs, lows)
    c, pc = closes[i], closes[i - 1]
    start = max(0, i - MAX_BASE)
    last_ok = i - 1 - PIVOT_SPAN
    hsw = swing_highs[bisect.bisect_left(swing_highs, start):bisect.bisect_right(swing_highs, last_ok)]
    lsw = swing_lows[bisect.bisect_left(swing_lows, start):bisect.bisect_right(swing_lows, last_ok)]
    for width in range(MIN_BASE, MAX_BASE + 1, 2):
        s = i - width
        if s < 0:
            break
        hs = [j for j in hsw if j >= s]
        ls = [j for j in lsw if j >= s]
        if len(hs) < 2 or len(ls) < 2:
            continue
        top, bottom = max(highs[s:i]), min(lows[s:i])
        if bottom <= 0 or top / bottom > MAX_BASE_RATIO:
            continue
        height = top - bottom
        top_touches = [j for j in hs if highs[j] >= top * (1 - LEVEL_TOLERANCE)]
        bottom_touches = [j for j in ls if lows[j] <= bottom * (1 + LEVEL_TOLERANCE)]
        if len(top_touches) >= 2:
            rising = [j for j in ls if j > top_touches[0]]
            if len(rising) >= 2:
                m, b = _fit([(j, lows[j]) for j in rising])
                if m > 0 and _stepping([lows[j] for j in rising], True):
                    if c > top and pc <= top:
                        found[("BOTTOM", "up")] = (top, height)
                    if c < m * i + b and pc >= m * (i - 1) + b and c < top:
                        found[("BOTTOM", "down")] = (m * i + b, height)
        if len(bottom_touches) >= 2:
            falling = [j for j in hs if j > bottom_touches[0]]
            if len(falling) >= 2:
                m, b = _fit([(j, highs[j]) for j in falling])
                if m < 0 and _stepping([highs[j] for j in falling], False):
                    if c < bottom and pc >= bottom:
                        found[("TOP", "down")] = (bottom, height)
                    if c > m * i + b and pc <= m * (i - 1) + b and c > bottom:
                        found[("TOP", "up")] = (m * i + b, height)
        if len(top_touches) >= 2 and len(bottom_touches) >= 2 and top / bottom < MAX_RANGE_RATIO:
            if c > top and pc <= top:
                found[("MIDDLE", "up")] = (top, height)
            if c < bottom and pc >= bottom:
                found[("MIDDLE", "down")] = (bottom, height)
        mh, bh = _fit([(j, highs[j]) for j in hs])
        ml, bl = _fit([(j, lows[j]) for j in ls])
        if (mh < 0 and ml > 0 and _stepping([highs[j] for j in hs], False)
                and _stepping([lows[j] for j in ls], True)):
            upper, lower = mh * i + bh, ml * i + bl
            if upper > lower:
                if c > upper and pc <= mh * (i - 1) + bh:
                    found[("SYM", "up")] = (upper, height)
                if c < lower and pc >= ml * (i - 1) + bl:
                    found[("SYM", "down")] = (lower, height)
    return found


def _signal(highs, lows, closes, e21, e50, i, direction, swings, status, period) -> dict | None:
    hits = breaks_at(highs, lows, closes, i, swings)
    c = closes[i]
    trend = (c > e21[i] > e50[i]) if direction == "up" else (c < e21[i] < e50[i])
    if not trend:
        return None
    for pattern in PATTERN_ORDER:
        hit = hits.get((pattern, direction))
        if hit:
            level, height = hit
            target = level + height if direction == "up" else level - height
            # A base taller than the price projects a target at/below $0 - no target.
            return {
                "pattern": pattern,
                "label": PATTERN_LABELS[pattern],
                "direction": direction,
                "status": status,
                "period": period,
                "level": round(level, 4),
                "target": round(target, 4) if target > 0 else None,
                "close": round(c, 4),
                "ema21": round(e21[i], 4),
                "ema50": round(e50[i], 4),
            }
    return None


def _previous_month(period: str) -> str:
    try:
        year, month = int(period[:4]), int(period[5:7])
    except (TypeError, ValueError):
        return ""
    year, month = (year - 1, 12) if month == 1 else (year, month - 1)
    return f"{year:04d}-{month:02d}"


def breakout_state(bars: Sequence[Mapping[str, Any]], period: str) -> dict | None:
    """One symbol's monthly verdict for the current ``period`` ("YYYY-MM").

    ``bars`` ascending with ``period``, ``high``, ``low``, ``close``. Returns
    ``None`` when there are fewer than MIN_MONTHS real months, else
    ``{"up": signal|None, "down": signal|None}``. A signal on the current
    month is ``status: "live"`` (not closed); one on last month is
    ``"confirmed"``. The current month wins when both fire.
    """
    from momx.ema_touch import _drop_placeholder_lead  # same SPAC flat-lead fix

    rows = _drop_placeholder_lead([b for b in bars if str(b.get("period")) <= period])
    rows = [b for b in rows if b.get("high") is not None and b.get("low") is not None]
    if len(rows) < MIN_MONTHS:
        return None
    highs = [float(b["high"]) for b in rows]
    lows = [float(b["low"]) for b in rows]
    closes = [float(b["close"]) for b in rows]
    e21, e50 = ema(closes, 21), ema(closes, 50)
    swings = _swings(highs, lows)
    n = len(rows)
    has_current = str(rows[-1].get("period")) == period
    out: dict[str, Any] = {"up": None, "down": None}
    for direction in ("up", "down"):
        candidates = []
        if has_current:
            candidates.append((n - 1, "live"))
            candidates.append((n - 2, "confirmed"))
        elif str(rows[-1].get("period"))[:7] == _previous_month(period):
            # No bar yet this month: last month's close is the confirmed one.
            # An older last bar (halted / delisted) is not a current signal.
            candidates.append((n - 1, "confirmed"))
        for i, status in candidates:
            hit = _signal(highs, lows, closes, e21, e50, i, direction, swings, status, str(rows[i]["period"]))
            if hit:
                out[direction] = hit
                break
    return out


# ---------------------------------------------------------------------------
# reading (never blocks, never raises)
# ---------------------------------------------------------------------------

CACHE_KEY = "breakout:Monthly"


def members(symbols: Iterable[str], name: str) -> list[str]:
    """The given symbols (in their order) with a break in the list's direction."""
    from momx import ema_touch

    direction = DIRECTIONS.get(parse_name(name) or "")
    if not enabled() or direction is None:
        return []
    states = ema_touch.cached_states(symbols, CACHE_KEY)
    return [s for s, state in states.items() if (state or {}).get(direction)]


def details(symbols: Iterable[str], name: str) -> dict[str, dict]:
    """``{symbol: signal}`` for a list's members - what the panel shows."""
    from momx import ema_touch

    direction = DIRECTIONS.get(parse_name(name) or "")
    if not enabled() or direction is None:
        return {}
    states = ema_touch.cached_states(symbols, CACHE_KEY)
    return {s: state[direction] for s, state in states.items() if (state or {}).get(direction)}


__all__ = [
    "BEAR_LIST",
    "BULL_LIST",
    "CACHE_KEY",
    "PATTERN_LABELS",
    "breakout_state",
    "breaks_at",
    "current_lists",
    "details",
    "enabled",
    "is_derived",
    "list_names",
    "members",
    "parse_name",
]
