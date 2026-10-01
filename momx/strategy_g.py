"""Strategy G - Ganesh's own entry method, as he described it on 2026-09-23.

Pure functions; momx/strategy.py owns the state (previous RVOL, the day list,
the option chain fetches and the option-price tracking).

    1  RVOL cyan (>= 3.0) on 30m / 1h / 2h / 4h / D, and that reading RISING
       against the previous one ("real time increasing")
    2  SQZ 2h and 4h: fired (cyan) or no squeeze - never orange / white
    3  Skittles 2h cyan cross (EMA 9 x 20) on a 2h bar opened today at/after
       08:00 ET, plus a cyan cross on 4h or D within 24 h (any 4h/D cyan
       cross older than that fails)
    4  5m close above EMA20 AND VWAP, OR a 9x20 cross up (the chart's CALL5 /
       C5 arrow) in the last 30 minutes - "or", his correction
    5  Option plan: target = nearest STRONG call-OI wall above the price
       (OI >= 66% of the biggest call OI above spot, the chain's "C OI
       STRONG"); ENTRY = the OTM call with delta closest under 0.20 (the
       chain's ENTRY row); ROI = the target strike's call price vs the ENTRY
       price (the chain's "ROI" column) must be >= 150%
    exit   the stock reaches the target, or the option falls 50%, or the close

Back-test of rules 1-4 on 2026-09-01..09-23 (stock, +2%/-1.5%): +0.43% per
trade over 49 trades; rule 5 cannot be back-tested (no saved option chains),
so the option side is measured live only.
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
WINDOW = (dtime(9, 30), dtime(15, 30))
RVOL_TFS = ("30m", "1h", "2h", "4h", "D")
SQZ_TFS = ("2h", "4h")
SQZ_OK = frozenset({"cyan", "black", "", None})
#: BEAR (spec 2026-09-24): fired DOWN (magenta) or no squeeze - never orange / white.
SQZ_OK_BEAR = frozenset({"magenta", "black", "", None})
CROSS_2H_FROM = dtime(8, 0)
HIGHER_CROSS_HOURS = 24
ARROW_MINUTES = 30
STRONG_WALL = 0.66
ENTRY_DELTA = 0.20
MIN_ROI = 150.0
STOP_PCT = -50.0

_FAIL = {"r1": False, "r2": False, "r3": False, "r4": False, "pass": False, "why": []}


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _local(now: datetime) -> datetime:
    return now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)


def _is_bear(direction) -> bool:
    return str(direction or "").strip().lower() == "bear"


def rvol_readings(row: Any) -> dict[str, float]:
    """The numeric RVOL value per G timeframe - what 'rising' is judged against."""
    out = {}
    for tf in RVOL_TFS:
        value = _num(_m(_m(_m(row).get("rvol")).get(tf)).get("value"))
        if value is not None:
            out[tf] = value
    return out


def rules(row: Any, now: datetime, prev_rvol: Mapping | None, direction: str = "bull") -> dict:
    """Rules 1-4 for the row as it reads at ``now``. Never raises.

    ``direction="bear"`` (spec 2026-09-24) is the same method on the sell
    side: RVOL magenta and rising, SQZ fired down or none, Skittles magenta
    crosses, 5m BELOW EMA20 and VWAP or a 9x20 cross DOWN (PUT5 / P5).
    """
    try:
        if not isinstance(row, Mapping):
            return dict(_FAIL, why=["no row"])
        bear = _is_bear(direction)
        fired = "magenta" if bear else "cyan"
        sqz_ok = SQZ_OK_BEAR if bear else SQZ_OK
        local = _local(now)
        if local.weekday() >= 5 or not (WINDOW[0] <= local.time() <= WINDOW[1]):
            return dict(_FAIL, why=["outside 09:30-15:30 ET"])
        prev = prev_rvol if isinstance(prev_rvol, Mapping) else {}
        why = []

        rvol = _m(row.get("rvol"))
        rising = [tf for tf in RVOL_TFS
                  if _m(rvol.get(tf)).get("bg") == fired
                  and _num(_m(rvol.get(tf)).get("value")) is not None
                  and _num(prev.get(tf)) is not None
                  and _num(_m(rvol.get(tf)).get("value")) > _num(prev.get(tf))]
        r1 = bool(rising)
        if not r1:
            why.append(f"RVOL: no {fired} 30m-D reading rising")

        sqz = _m(row.get("sqz"))
        r2 = all(_m(sqz.get(tf)).get("bg") in sqz_ok for tf in SQZ_TFS)
        if not r2:
            why.append("SQZ 2h/4h still squeezing")

        sk = _m(row.get("skittles"))
        c2 = _m(sk.get("2h"))
        t2 = _num(c2.get("barAt"))
        fresh2 = (c2.get("bg") == fired and t2 is not None
                  and datetime.fromtimestamp(t2, ET).date() == local.date()
                  and datetime.fromtimestamp(t2, ET).time() >= CROSS_2H_FROM)
        limit = local.timestamp() - HIGHER_CROSS_HOURS * 3600
        higher = [(tf, _num(_m(sk.get(tf)).get("barAt"))) for tf in ("4h", "D")
                  if _m(sk.get(tf)).get("bg") == fired]
        higher_ok = bool(higher) and all(t is not None and t >= limit for _, t in higher)
        r3 = bool(fresh2 and higher_ok)
        if not r3:
            why.append(f"Skittles: needs a 2h {fired} cross today after 8 AM + a 4h/D cross within 24h")

        m5 = _m(row.get("m5"))
        close = _num(_m(m5.get("lastCompleted")).get("close"))
        e20, vwap = _num(m5.get("ema20")), _num(m5.get("vwap"))
        if bear:
            above = close is not None and e20 is not None and vwap is not None and close < e20 and close < vwap
            cross = _num(m5.get("crossDownAt"))
        else:
            above = close is not None and e20 is not None and vwap is not None and close > e20 and close > vwap
            cross = _num(m5.get("crossUpAt"))
        arrow = cross is not None and 0 <= local.timestamp() - cross <= ARROW_MINUTES * 60 + 300
        r4 = bool(above or arrow)
        if not r4:
            why.append("5m: not below EMA20 + VWAP and no 9x20 cross down in 30 min" if bear
                       else "5m: not above EMA20 + VWAP and no 9x20 cross up in 30 min")

        return {"r1": r1, "r2": r2, "r3": r3, "r4": r4, "pass": r1 and r2 and r3 and r4,
                "why": why, "rising": rising, "arrow": bool(arrow), "aboveEmaVwap": bool(above)}
    except Exception:  # noqa: BLE001 - a bad row never passes, never raises
        return dict(_FAIL, why=["error"])


def _price(r: Mapping) -> float:
    bid, ask = _num(r.get("bid")) or 0.0, _num(r.get("ask")) or 0.0
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    return _num(r.get("mark")) or _num(r.get("last")) or 0.0


def _gamma_roi(spot: float, strike: float, entry: float, target: float, delta: float, gamma: float,
               direction: str = "bull") -> float:
    """The chain's delta + gamma projection (App.jsx calculateGammaRoiEstimate).
    For a put the favourable move is DOWN and the intrinsic value is strike - target."""
    bear = _is_bear(direction)
    move = (spot - target) if bear else (target - spot)
    d = abs(delta)
    projected = min(max(d + abs(gamma) * move, 0.0), 1.0)
    intrinsic = max(strike - target, 0.0) if bear else max(target - strike, 0.0)
    exit_price = max(entry + (d + projected) / 2 * move, intrinsic, 0.01)
    return (exit_price - entry) / entry * 100.0


def option_plan(chain: Any, direction: str = "bull") -> dict:
    """Target wall, ENTRY contract and ROI from an /api/oi-finder-chain payload.

    Uses the nearest expiry that is NOT today (his PLTR trade: the Sep 25
    weekly on Sep 23). ``ok`` False = the chain was unusable; ``pass`` is
    rule 5. Never raises.

    ``direction="bear"`` (spec 2026-09-24) is the PUT plan: strikes BELOW
    spot, ENTRY = the put with |delta| closest under 0.20, target = the
    nearest strong put wall (OI >= 66% of the biggest put OI below spot)
    between spot and the ENTRY strike, ROI = that wall's put price vs the
    ENTRY price. ``side`` is "C" or "P".
    """
    try:
        bear = _is_bear(direction)
        side, side_letter = ("PUT", "P") if bear else ("CALL", "C")
        spot = _num(_m(chain).get("underlyingPrice"))
        rows = _m(chain).get("selectedExpiryChainRows")
        if not spot or spot <= 0 or not isinstance(rows, list):
            return {"ok": False, "why": "no chain"}
        legs = [r for r in rows if isinstance(r, Mapping) and r.get("side") == side
                and _num(r.get("strike")) and (_num(r.get("days_to_expiration")) or 0) >= 1 and r.get("expiry")]
        if not legs:
            return {"ok": False, "why": f"no {side.lower()} contracts after today"}
        expiry = min(r["expiry"] for r in legs)
        legs = [r for r in legs if r["expiry"] == expiry]
        beyond = [r for r in legs if ((_num(r["strike"]) < spot) if bear else (_num(r["strike"]) > spot))]
        if not beyond:
            return {"ok": False, "why": "no strikes below the price" if bear else "no strikes above the price"}

        otm = [r for r in beyond if _price(r) > 0 and 0 < abs(_num(r.get("delta")) or 0) < ENTRY_DELTA]
        if not otm:
            return {"ok": True, "pass": False, "expiry": expiry, "side": side_letter,
                    "why": "no ENTRY contract (delta under 0.20)"}
        entry = sorted(otm, key=lambda r: (abs(ENTRY_DELTA - abs(_num(r.get("delta")))), abs(_num(r["strike"]) - spot)))[0]
        entry_strike, entry_price = _num(entry["strike"]), _price(entry)

        biggest = max((_num(r.get("open_interest")) or 0) for r in beyond)
        # Nearest to the price first: ascending strikes for calls, descending for puts.
        strong = sorted((r for r in beyond if biggest > 0 and (_num(r.get("open_interest")) or 0) >= STRONG_WALL * biggest),
                        key=lambda r: _num(r["strike"]), reverse=bear)
        target_row = next((r for r in strong
                           if ((_num(r["strike"]) > entry_strike) if bear else (_num(r["strike"]) < entry_strike))), None)
        plan = {
            "ok": True, "expiry": expiry, "spot": spot, "side": side_letter,
            "entrySymbol": entry.get("symbol"), "entryStrike": entry_strike, "entryPrice": round(entry_price, 4),
            "entryDelta": abs(_num(entry.get("delta")) or 0),
        }
        if target_row is None:
            return {**plan, "pass": False,
                    "why": f"no strong {side.lower()} wall between the price and the ENTRY strike"}
        target = _num(target_row["strike"])
        roi = (_price(target_row) - entry_price) / entry_price * 100.0 if entry_price > 0 else 0.0
        groi = _gamma_roi(spot, entry_strike, entry_price, target, _num(entry.get("delta")) or 0.0,
                          _num(entry.get("gamma")) or 0.0, direction)
        return {**plan, "target": target, "targetOi": int(_num(target_row.get("open_interest")) or 0),
                "roi": round(roi, 1), "gammaRoi": round(groi, 1), "pass": roi >= MIN_ROI,
                "why": "" if roi >= MIN_ROI else f"ROI {roi:.0f}% under {MIN_ROI:.0f}%"}
    except Exception:  # noqa: BLE001
        return {"ok": False, "why": "error"}


def contract_mark(chain: Any, symbol: str) -> float | None:
    """The live price of one contract (mid of bid/ask) from a chain payload."""
    try:
        for r in _m(chain).get("selectedExpiryChainRows") or []:
            if isinstance(r, Mapping) and r.get("symbol") == symbol:
                p = _price(r)
                return p if p > 0 else None
    except Exception:  # noqa: BLE001
        return None
    return None
