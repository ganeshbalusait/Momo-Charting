"""LEAP setup: the monthly 50 EMA intramonth touch in an uptrend (pure; no I/O).

The one EMA entry Ganesh's back-tests found that beats buying on any day (Monthly 50 EMA touch in an
uptrend: n=255, 60% win / median +20% over 6 months vs 54% / +8% on any day, 2015-26). Identical to
the tested scan (artifacts/scan_monthly_ema_touch.py):

  monthly bars   built from DAILY bars (split-adjusted): calendar month in ET; low = min daily low,
                 high = max daily high, close = the last daily close
  level          EMA(50) of the COMPLETED months' closes (every month before the current ET calendar
                 month), ewm(span=50, adjust=False) seeded with the first close (thinkorswim / pandas)
  prevLevel      the same EMA one month earlier; lastClose = the last completed month's close
  uptrend        lastClose > level AND level > prevLevel
  touched        a daily low in the CURRENT month <= level (touchDate = the first such day)
  pass           uptrend AND touched; None when fewer than 2 x 50 = 100 completed months (EMA unsettled)

``asOf`` is the date of the newest daily bar used (the service treats a state whose asOf is more than
4 calendar days old as stale: never a setup).
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone
from typing import Any, Iterable
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LENGTH = 50
MIN_MONTHS_FACTOR = 2                      # >= 2 x length completed months before the EMA is trusted

REASON_SHORT = "fewer than 100 months of history"
REASON_NO_DAY = "no trading day this month yet"
REASON_NO_DATA = "no daily bars"

STATE_KEYS = ("level", "prevLevel", "lastClose", "lastMonth", "uptrend", "closeAbove", "emaRising", "month",
              "monthLow", "touched", "touchDate", "pass", "asOf", "reason")


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _day(ts) -> date | None:
    """The ET trading date of a bar timestamp: a tz-aware datetime / pandas Timestamp is converted to
    ET first (a UTC stamp late on the 31st belongs to the 31st in ET, not the 1st); a naive one is
    taken as ET; a date is itself; a string is ISO; a number is epoch milliseconds."""
    if ts is None:
        return None
    if isinstance(ts, datetime):
        return (ts.astimezone(ET) if ts.tzinfo is not None else ts).date()
    if isinstance(ts, date):
        return ts
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        if not math.isfinite(float(ts)):
            return None
        return datetime.fromtimestamp(float(ts) / 1000.0, tz=timezone.utc).astimezone(ET).date()
    try:
        return _day(datetime.fromisoformat(str(ts).strip().replace("Z", "+00:00")))
    except (TypeError, ValueError):
        return None


def _records(rows) -> list[dict]:
    if rows is None:
        return []
    to_dict = getattr(rows, "to_dict", None)
    if callable(to_dict) and hasattr(rows, "columns"):          # a pandas DataFrame
        return list(rows.to_dict("records"))
    return [r for r in rows if isinstance(r, dict)]


def daily_bars(rows) -> list[tuple[date, float, float, float]]:
    """(day, low, high, close) per usable daily bar, sorted by day, one per day (the first wins: Schwab
    repeats candles). A bar with a missing / non-positive low, high or close is skipped."""
    out: dict[date, tuple[date, float, float, float]] = {}
    for r in _records(rows):
        d = _day(r.get("timestamp", r.get("datetime", r.get("date"))))
        lo, hi, cl = _num(r.get("low")), _num(r.get("high")), _num(r.get("close"))
        if d is None or lo is None or hi is None or cl is None or lo <= 0 or hi <= 0 or cl <= 0:
            continue
        out.setdefault(d, (d, lo, hi, cl))
    return [out[d] for d in sorted(out)]


def _period(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def monthly_from_daily(rows) -> list[dict]:
    """Calendar-month bars from daily bars: [{"period": "YYYY-MM", "low", "high", "close"}], oldest first."""
    months: dict[str, dict] = {}
    for d, lo, hi, cl in daily_bars(rows):
        p = _period(d)
        m = months.get(p)
        if m is None:
            months[p] = {"period": p, "low": lo, "high": hi, "close": cl}
        else:
            m["low"], m["high"], m["close"] = min(m["low"], lo), max(m["high"], hi), cl
    return [months[p] for p in sorted(months)]


def ema(values: Iterable[float], length: int = LENGTH) -> list[float]:
    """thinkorswim ExpAverage / pandas ewm(span=length, adjust=False): seeded with the first value."""
    alpha = 2.0 / (length + 1.0)
    out: list[float] = []
    for v in values:
        out.append(float(v) if not out else out[-1] + alpha * (float(v) - out[-1]))
    return out


def _r(v) -> float | None:
    return None if v is None else round(float(v), 4)


def setup_state(daily_rows, *, today: date, length: int = LENGTH) -> dict[str, Any] | None:
    """The LEAP-setup state as of ``today`` (an ET date) from split-adjusted daily bars. None when there
    is no usable daily bar at all; otherwise a dict with STATE_KEYS (floats rounded to 4, dates ISO)."""
    days = [b for b in daily_bars(daily_rows) if b[0] <= today]
    if not days:
        return None
    cur = _period(today)
    monthly = [m for m in monthly_from_daily([{"timestamp": d, "low": lo, "high": hi, "close": cl}
                                              for d, lo, hi, cl in days]) if m["period"] < cur]
    this_month = [b for b in days if _period(b[0]) == cur]
    state: dict[str, Any] = dict.fromkeys(STATE_KEYS)
    state.update(month=cur, touched=False, uptrend=False, closeAbove=False, emaRising=False,
                 asOf=days[-1][0].isoformat())             # "pass" stays None until the EMA is settled
    if this_month:
        state["monthLow"] = _r(min(b[1] for b in this_month))
    if monthly:
        state.update(lastClose=_r(monthly[-1]["close"]), lastMonth=monthly[-1]["period"])
    if len(monthly) < MIN_MONTHS_FACTOR * length:
        state.update(reason=REASON_SHORT)
        return state
    series = ema([m["close"] for m in monthly], length)
    level, prev = series[-1], series[-2]
    last_close = monthly[-1]["close"]
    close_above, rising = last_close > level, level > prev
    uptrend = close_above and rising
    touch = next((b for b in this_month if b[1] <= level), None)
    state.update(level=_r(level), prevLevel=_r(prev), closeAbove=close_above, emaRising=rising, uptrend=uptrend,
                 touched=touch is not None, touchDate=touch[0].isoformat() if touch else None,
                 reason=None if this_month else REASON_NO_DAY)
    state["pass"] = bool(uptrend and touch is not None)
    return state
