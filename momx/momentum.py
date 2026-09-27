"""Momentum now - separate from the setup grade (experimental draft rules).

Spec: docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md, "Momentum now".
Pure: 5m bars + the row's RVOL cells in, a small summary out.
"""
from __future__ import annotations

import math
import time
from datetime import datetime
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from momx.columns import RVOL_LENGTH
from momx.indicators import ema, rvol_zscore, macd, adx_lines
from momx import live_bolt

TRIGGER_LOOKBACK = 6
ATR_LENGTH = 14
EMA_LENGTH = 20
EXTENDED_ATR = 2.0
BUILD_RVOL_BG = frozenset({"cyan", "green"})
#: The bear mirror (spec 2026-09-24): selling volume, bar closing in its lower half.
BEAR_RVOL_BG = frozenset({"magenta", "red"})
FADE_RVOL_Z = 1.0
BAR_SECONDS = 300
MIN_BARS = TRIGGER_LOOKBACK + 1
ET = ZoneInfo("America/New_York")


def _t(bar: Mapping) -> int:
    raw = bar.get("time", bar.get("timestamp"))
    if hasattr(raw, "timestamp"):
        return int(raw.timestamp())
    return int(raw)


def _et_date(epoch: int):
    return datetime.fromtimestamp(epoch, ET).date()


def _atr(bars: list[Mapping], length: int) -> float | None:
    if len(bars) < length + 1:
        return None
    trs = []
    for prev, bar in zip(bars[:-1], bars[1:]):
        trs.append(max(bar["high"] - bar["low"], abs(bar["high"] - prev["close"]), abs(bar["low"] - prev["close"])))
    return sum(trs[-length:]) / length


def _is_bear(direction) -> bool:
    return str(direction or "").strip().lower() == "bear"


def _rvol_push(cells: Mapping | None, direction: str = "bull") -> bool:
    if not isinstance(cells, Mapping):
        return False
    wanted = BEAR_RVOL_BG if _is_bear(direction) else BUILD_RVOL_BG
    return any(isinstance(cells.get(tf), Mapping) and cells[tf].get("bg") in wanted for tf in ("5m", "15m"))


def _position(bar: Mapping) -> float:
    span = bar["high"] - bar["low"]
    return 0.5 if span <= 0 else (bar["close"] - bar["low"]) / span


EXPLOSIVE_Z = 3.0
STEADY_CANDLES = 6
STEADY_MIN_RISES = 4


def _pattern(completed: list[Mapping], z: list[float], chart: str, ema20: float | None,
             direction: str = "bull") -> str | None:
    bear = _is_bear(direction)
    recent = list(zip(completed[-6:], z[-6:]))
    if chart in ("breakout_confirmed", "holding") and any(
        math.isfinite(v) and v >= EXPLOSIVE_Z and ((_position(b) <= 0.5) if bear else (_position(b) >= 0.5))
        for b, v in recent
    ):
        return "explosive"
    today = _et_date(_t(completed[-1]))
    groups: dict[int, list[Mapping]] = {}
    for b in completed:
        if _et_date(_t(b)) == today:
            groups.setdefault(_t(b) // 900, []).append(b)
    candles = [
        {"high": max(x["high"] for x in g), "low": min(x["low"] for x in g)}
        for _, g in sorted(groups.items()) if len(g) == 3
    ][-STEADY_CANDLES:]
    if len(candles) < STEADY_CANDLES or ema20 is None:
        return None
    if bear:
        if completed[-1]["close"] >= ema20:
            return None
        steps = sum(b["high"] < a["high"] and b["low"] < a["low"] for a, b in zip(candles, candles[1:]))
    else:
        if completed[-1]["close"] <= ema20:
            return None
        steps = sum(b["high"] > a["high"] and b["low"] > a["low"] for a, b in zip(candles, candles[1:]))
    return "steady" if steps >= STEADY_MIN_RISES else None


def _session_vwap(completed: list[Mapping]) -> float | None:
    """VWAP of TODAY's regular session (09:30 ET on), completed 5m bars only.
    Typical price (H+L+C)/3 x volume. None before the open or with no volume."""
    last_day = _et_date(_t(completed[-1]))
    pv = vol = 0.0
    for b in completed:
        t = datetime.fromtimestamp(_t(b), ET)
        if t.date() != last_day or (t.hour, t.minute) < (9, 30):
            continue
        v = float(b.get("volume") or 0)
        pv += (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0 * v
        vol += v
    return pv / vol if vol > 0 else None


def _last_crosses(completed: list[Mapping], closes: list[float]) -> tuple[int | None, int | None]:
    """(crossUpAt, crossDownAt) from ONE EMA(9)/EMA(20) pass - summarize is
    called twice per row (bull + bear) and each used to run both passes."""
    e9, e20 = ema(closes, 9), ema(closes, EMA_LENGTH)
    today = _et_date(_t(completed[-1]))
    up = down = None
    for i in range(len(completed) - 1, 0, -1):
        if _et_date(_t(completed[i])) != today:
            break
        if up is None and e9[i] > e20[i] and e9[i - 1] <= e20[i - 1]:
            up = _t(completed[i])
        if down is None and e9[i] < e20[i] and e9[i - 1] >= e20[i - 1]:
            down = _t(completed[i])
        if up is not None and down is not None:
            break
    return up, down


def _last_cross(completed: list[Mapping], closes: list[float], direction: str = "bull") -> int | None:
    """Open time of the newest completed 5m bar TODAY where EMA(9) closed above
    EMA(20) after being at/below it - the chart's CALL5 / C5 arrow (CloudMax
    9x20 cross up; CALL5 vs C5 only differs by the 15m trend, and Strategy G
    accepts either). ``direction="bear"``: the cross BELOW (PUT5 / P5). None
    when there was no such cross today. Both are reported on every summary."""
    e9, e20 = ema(closes, 9), ema(closes, EMA_LENGTH)
    today = _et_date(_t(completed[-1]))
    bear = _is_bear(direction)
    for i in range(len(completed) - 1, 0, -1):
        if _et_date(_t(completed[i])) != today:
            break
        if bear:
            if e9[i] < e20[i] and e9[i - 1] >= e20[i - 1]:
                return _t(completed[i])
        elif e9[i] > e20[i] and e9[i - 1] <= e20[i - 1]:
            return _t(completed[i])
    return None


GAP_GO_MIN_GAP = 0.02
GAP_GO_LAST_BAR = (10, 25)


def _gap_go(completed: list[Mapping], direction: str = "bull") -> dict | None:
    """The gap-and-go picture (META 2026-09-21: gapped +2.2%, cleared VWAP, the
    open and the 09:30 candle's high at 09:40, ran +10%). Back-test 09-01..23 on
    tickers already graded A/A+: 44% ran +4% vs 31% for the OPT picture.

    ``open`` = the last close before 09:30 (what the back-test used), ``orHigh``
    = the 09:30 candle's high, ``goAt`` = open time of the FIRST completed bar
    from 09:35 through 10:25 that closed above VWAP (so far), the open and
    orHigh. The 30m buyers check and the letter are applied by the browser on
    the live row. None when today has no regular-session bar yet or no prior
    session to measure the gap from.

    ``direction="bear"`` is gap DOWN and go: gap <= -2%, the first completed
    bar 09:35-10:25 closing BELOW VWAP, the open and the 09:30 candle's LOW.
    ``orHigh`` and ``orLow`` are both reported either way."""
    bear = _is_bear(direction)
    today = _et_date(_t(completed[-1]))
    prev_close = open_ = None
    session: list[Mapping] = []
    for b in completed:
        t = datetime.fromtimestamp(_t(b), ET)
        hm = (t.hour, t.minute)
        if t.date() < today and (9, 30) <= hm < (16, 0):
            prev_close = float(b["close"])
        elif t.date() == today and hm < (9, 30):
            open_ = float(b["close"])
        elif t.date() == today and (9, 30) <= hm < (16, 0):
            session.append(b)
    if not session or prev_close is None or prev_close <= 0:
        return None
    if open_ is None:
        open_ = float(session[0].get("open") or session[0]["close"])
    gap = open_ / prev_close - 1
    or_high = float(session[0]["high"])
    or_low = float(session[0]["low"])
    go_at = None
    if (gap <= -GAP_GO_MIN_GAP) if bear else (gap >= GAP_GO_MIN_GAP):
        pv = vol = 0.0
        for i, b in enumerate(session):
            v = float(b.get("volume") or 0)
            pv += (float(b["high"]) + float(b["low"]) + float(b["close"])) / 3.0 * v
            vol += v
            t = datetime.fromtimestamp(_t(b), ET)
            if (t.hour, t.minute) > GAP_GO_LAST_BAR:
                break
            if i == 0 or vol <= 0:
                continue
            c = float(b["close"])
            vwap = pv / vol
            went = (c < vwap and c < open_ and c < or_low) if bear else (c > vwap and c > open_ and c > or_high)
            if went:
                go_at = _t(b)
                break
    return {"gap": round(gap * 100, 2), "open": round(open_, 4), "orHigh": round(or_high, 4),
            "orLow": round(or_low, 4), "goAt": go_at}


TOD_MIN_SESSIONS = 3
TOD_MAX_SESSIONS = 10


def _time_of_day_volume(completed: list[Mapping]) -> dict | None:
    """Today's regular-session volume so far vs the SAME time of day on the
    previous sessions in the tape (up to TOD_MAX_SESSIONS, at least
    TOD_MIN_SESSIONS) - "3x its normal volume by 10:05". For SOLO
    (momx/solo.py). ``open`` is the 09:30 candle's close, as the back-test
    used. None before the open or without enough history."""
    by_day: dict = {}
    for b in completed:
        t = datetime.fromtimestamp(_t(b), ET)
        if (9, 30) <= (t.hour, t.minute) < (16, 0):
            by_day.setdefault(t.date(), []).append((t.hour * 60 + t.minute, b))
    if not by_day:
        return None
    today = max(by_day)
    session = by_day[today]
    minute = session[-1][0]
    so_far = sum(float(b.get("volume") or 0) for _, b in session)
    history = []
    for day in sorted(d for d in by_day if d < today)[-TOD_MAX_SESSIONS:]:
        cum = sum(float(b.get("volume") or 0) for m, b in by_day[day] if m <= minute)
        if cum > 0:
            history.append(cum)
    if len(history) < TOD_MIN_SESSIONS:
        return None
    normal = sum(history) / len(history)
    return {"ratio": round(so_far / normal, 2) if normal > 0 else None,
            "open": round(float(session[0][1]["close"]), 4), "sessions": len(history)}


def _go_confirmation(completed: list[Mapping]) -> dict | None:
    """Sep 1-25 research rule: closed 5m MACD and closed 30m DI only.

    Includes extended-hours bars, matching the research tape. Never infer
    this from Skittles paints or from the developing 30m board ADX cell.
    """
    if len(completed) < 51:
        return None
    closes = [float(b['close']) for b in completed]
    line, signal = macd(closes, 12, 26, 9)
    hist, prev = line[-1] - signal[-1], line[-2] - signal[-2]
    if not all(math.isfinite(v) for v in (hist, prev)):
        return None
    closed_at = _t(completed[-1]) + BAR_SECONDS
    buckets = {}
    for b in completed:
        start = _t(b) // 1800 * 1800
        if start + 1800 > closed_at:
            continue
        if start not in buckets:
            buckets[start] = dict(b)
        else:
            q = buckets[start]
            q['high'] = max(q['high'], b['high'])
            q['low'] = min(q['low'], b['low'])
            q['close'] = b['close']
    candles = list(buckets.values())
    di = adx_lines([b['high'] for b in candles], [b['low'] for b in candles],
                   [b['close'] for b in candles]) if candles else None
    plus = di['plus'][-1] if di else None
    minus = di['minus'][-1] if di else None
    buyers = (plus is not None and minus is not None and math.isfinite(plus)
              and math.isfinite(minus) and plus > minus)
    return {'barAt': _t(completed[-1]), 'macdUp': hist > 0 and hist > prev,
            'buyers30': bool(buyers)}


def summarize(bars_5m: Any, rvol_cells: Mapping | None, *, now_epoch: float | None = None,
              direction: str = "bull") -> dict | None:
    """Momentum now. ``direction="bear"`` (spec 2026-09-24) mirrors every rule:
    trigger = lowest low of the last 6 completed bars, a breakDOWN below it,
    Building = close in the bottom third on selling RVOL, Extended = 2 ATR
    BELOW EMA20, Steady = lower highs and lower lows. The state names are
    shared (``breakout_confirmed`` reads "breakdown confirmed" on a bear row);
    the bear no-breakdown state is ``"above"`` where bull says ``"below"``."""
    if not isinstance(bars_5m, list) or len(bars_5m) < MIN_BARS:
        return None
    bear = _is_bear(direction)
    now = time.time() if now_epoch is None else float(now_epoch)
    developing = now - _t(bars_5m[-1]) < BAR_SECONDS
    completed = bars_5m[:-1] if developing else list(bars_5m)
    if len(completed) < MIN_BARS:
        return None
    last_price = float(bars_5m[-1]["close"])

    def trigger_before(i: int) -> float | None:
        window = completed[max(0, i - TRIGGER_LOOKBACK):i]
        if len(window) != TRIGGER_LOOKBACK:
            return None
        return min(b["low"] for b in window) if bear else max(b["high"] for b in window)

    def broke(price: float, level: float) -> bool:
        return price < level if bear else price > level

    trigger = trigger_before(len(completed))
    today = _et_date(_t(completed[-1]))
    breakout_index, breakout_trigger = None, None
    for i in range(len(completed) - 1, -1, -1):
        if _et_date(_t(completed[i])) != today:
            break
        level = trigger_before(i)
        if level is not None and broke(completed[i]["close"], level):
            breakout_index, breakout_trigger = i, level
            break

    if breakout_index is None:
        if developing and trigger is not None and broke(last_price, trigger):
            chart = "breakout_provisional"
        else:
            chart = "above" if bear else "below"
    elif breakout_index == len(completed) - 1:
        chart = "breakout_confirmed"
    elif all((b["close"] <= breakout_trigger) if bear else (b["close"] >= breakout_trigger)
             for b in completed[breakout_index + 1:]):
        chart = "holding"
    else:
        chart = "failed"

    closes = [float(b["close"]) for b in completed]
    ema_series = ema(closes, EMA_LENGTH)
    ema20 = ema_series[-1] if ema_series and math.isfinite(ema_series[-1]) else None
    atr14 = _atr(completed, ATR_LENGTH)
    z = rvol_zscore([float(b.get("volume") or 0) for b in completed], RVOL_LENGTH)
    rvol = _rvol_push(rvol_cells, direction)
    last = completed[-1]

    def stretched() -> bool:
        if ema20 is None or not atr14:
            return False
        return last_price < ema20 - EXTENDED_ATR * atr14 if bear else last_price > ema20 + EXTENDED_ATR * atr14

    def strong_close(b: Mapping) -> bool:
        return _position(b) <= 1 / 3 if bear else _position(b) >= 2 / 3

    def weak_close(b: Mapping) -> bool:
        return _position(b) > 0.5 if bear else _position(b) < 0.5

    crosses = _last_crosses(completed, closes)
    provisional = False
    if chart == "failed":
        state = "fading"
    elif stretched():
        state = "extended"
    elif chart in ("breakout_confirmed", "holding") and strong_close(last) and rvol:
        state = "building"
    elif chart in ("breakout_confirmed", "holding") and len(completed) >= 2 and all(
        weak_close(b) for b in completed[-2:]
    ) and all(math.isfinite(v) and v < FADE_RVOL_Z for v in z[-2:]):
        state = "fading"
    elif chart in ("breakout_confirmed", "holding"):
        state = "holding"
    elif chart == "breakout_provisional" and rvol:
        state, provisional = "building", True
    else:
        state = "quiet"

    return {
        "trigger": None if trigger is None else round(float(trigger), 4),
        "chart": chart,
        "state": state,
        "pattern": _pattern(completed, z, chart, ema20, direction),
        "direction": "bear" if bear else "bull",
        "provisional": provisional,
        "breakoutAt": None if breakout_index is None else _t(completed[breakout_index]),
        "lastCompleted": {k: last.get(k) for k in ("open", "high", "low", "close", "volume")} | {"time": _t(last)},
        "atr14": None if atr14 is None else round(atr14, 4),
        "ema20": None if ema20 is None else round(ema20, 4),
        # Strategy G rule 4 (momx/strategy.py): above EMA20 AND VWAP, or a
        # recent 9x20 cross up. Both from completed bars, like everything here.
        "vwap": _round_or_none(_session_vwap(completed)),
        "crossUpAt": crosses[0],
        "crossDownAt": crosses[1],
        "gapGo": _gap_go(completed, direction),
        "goConfirmation": None if bear else _go_confirmation(completed),
        "todVol": _time_of_day_volume(completed),
        # Five Pillars inputs: bull pass only (summarize runs twice per row -
        # bull and bear; the card reads the bull m5) - one computation per row.
        "pillars": None if bear else _pillars(completed),
        # The LIVE ⚡ seed (momx/live_bolt.py): harvested by service before the
        # board is published, so it never travels to a browser.
        "liveSeed": None if bear else live_bolt.seed_from_bars(completed),
    }


# ---------------------------------------------------------------------------
# The MomoX "Five Pillars" inputs (2026-09-25, his ask "build the checklist"):
# 01 trend = the EMA ribbon 9 / 21 / 50, 05 buy signals = MACD crosses and the
# stochastic, 04 sell targets = the prior session's high and today's
# premarket high. The card (MomxTickerCard) turns these into ✅ / ✖ rows.
# All from COMPLETED bars; never raises (None pieces are simply absent).
# ---------------------------------------------------------------------------

def _ribbon(closes: list[float]) -> dict | None:
    if len(closes) < 50:
        return None
    e9, e21, e50 = ema(closes, 9)[-1], ema(closes, 21)[-1], ema(closes, 50)[-1]
    if not all(math.isfinite(v) for v in (e9, e21, e50)):
        return None
    last = closes[-1]
    if e9 > e21 > e50 and last > e9:
        trend = "bull"
    elif e9 < e21 < e50 and last < e9:
        trend = "bear"
    else:
        trend = "mixed"
    return {"trend": trend, "ema9": round(e9, 4), "ema21": round(e21, 4), "ema50": round(e50, 4)}


def _thirty_minute_closes(bars: list[Mapping]) -> list[float]:
    """Closes of COMPLETE 30m buckets (6 x 5m) built from the 5m tape."""
    buckets: dict[int, list[Mapping]] = {}
    for bar in bars:
        buckets.setdefault(_t(bar) // 1800, []).append(bar)
    keys = sorted(buckets)
    if keys and len(buckets[keys[-1]]) < 6:
        keys = keys[:-1]  # the bucket still forming
    return [float(max(buckets[k], key=_t)["close"]) for k in keys]


def _macd_crosses(bars: list[Mapping], closes: list[float]) -> tuple[int | None, int | None]:
    if len(closes) < 35:
        return None, None
    fast, slow = ema(closes, 12), ema(closes, 26)
    line = [a - b for a, b in zip(fast, slow)]
    signal = ema(line, 9)
    up = down = None
    for i in range(len(line) - 1, max(0, len(line) - 60), -1):
        before, now_ = line[i - 1] - signal[i - 1], line[i] - signal[i]
        if up is None and before <= 0 < now_:
            up = _t(bars[i])
        if down is None and before >= 0 > now_:
            down = _t(bars[i])
        if up is not None and down is not None:
            break
    return up, down


def _stochastic(bars: list[Mapping], length: int = 14, smooth: int = 3) -> dict | None:
    if len(bars) < length + smooth:
        return None
    ks = []
    for i in range(len(bars) - smooth, len(bars)):
        window = bars[i - length + 1:i + 1]
        high, low = max(b["high"] for b in window), min(b["low"] for b in window)
        ks.append(50.0 if high == low else 100.0 * (float(bars[i]["close"]) - low) / (high - low))
    return {"k": round(ks[-1], 1), "d": round(sum(ks) / len(ks), 1)}


def _session_levels(bars: list[Mapping]) -> dict:
    """Prior regular session's high and today's premarket (04:00-09:30) high."""
    if not bars:
        return {}
    today = _et_date(_t(bars[-1]))
    by_day: dict = {}
    premarket = []
    for bar in bars:
        stamp = datetime.fromtimestamp(_t(bar), ET)
        minute = stamp.hour * 60 + stamp.minute
        if stamp.date() == today and 4 * 60 <= minute < 9 * 60 + 30:
            premarket.append(float(bar["high"]))
        elif stamp.date() < today and 9 * 60 + 30 <= minute < 16 * 60:
            by_day.setdefault(stamp.date(), []).append(float(bar["high"]))
    out = {}
    if by_day:
        out["prevHigh"] = round(max(by_day[max(by_day)]), 4)
    if premarket:
        out["premarketHigh"] = round(max(premarket), 4)
    return out


def _pillars(completed: list[Mapping]) -> dict | None:
    try:
        closes = [float(b["close"]) for b in completed]
        up, down = _macd_crosses(completed, closes)
        return {
            "ribbon5m": _ribbon(closes),
            "ribbon30m": _ribbon(_thirty_minute_closes(completed)),
            "macdCrossUpAt": up,
            "macdCrossDownAt": down,
            "stoch": _stochastic(completed),
            **_session_levels(completed),
        }
    except Exception:  # noqa: BLE001 - a pillar input never costs the row
        return None


def _round_or_none(value: float | None) -> float | None:
    return None if value is None or not math.isfinite(value) else round(value, 4)
