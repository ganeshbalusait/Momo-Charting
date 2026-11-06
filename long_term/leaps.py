"""LEAP to buy: pick the one call contract the Long Term page shows for a LEAPS-flagged row.

Pure: ``pick_leap`` takes a Schwab ``/chains`` payload (``callExpDateMap``) and the row's price and
returns the contract or the exact reason there is none. Malformed input never raises.

The rule (Rule #1 LEAPS):
  * expiry window 365..760 days out (12-25 months); the FARTHEST expiry with a usable contract wins
  * a CALL in the money (strike < price) with a live two-sided quote (bid > 0, ask > 0, ask >= bid)
  * delta closest to 0.75, ties -> the lower strike (deeper in the money); an expiry with no deltas
    at all falls back to the strike closest to 0.80 x price among the quoted in-the-money calls
  * a delta outside (0, 1] (Schwab's +/-999 or 0 after-hours sentinel) counts as no delta
"""
from __future__ import annotations

import math
from datetime import date, datetime

LEAP_MIN_DTE = 365
LEAP_MAX_DTE = 760
TARGET_DELTA = 0.75
FALLBACK_MONEYNESS = 0.80
MAX_SPREAD = 0.25
THIN_OI = 50            # open interest under this (or unknown) is a thin market: warn, never hide

REASON_NO_CHAIN = "no chain"
REASON_NO_PRICE = "no price"
REASON_NO_EXPIRY = "no expiry 12-25 months out"
REASON_NO_CONTRACT = "no in-the-money call with a live quote"
REASON_CHAIN_UNAVAILABLE = "chain unavailable"
REASON_NOT_FLAGGED = "LEAPS not flagged"

CONTRACT_KEYS = ("symbol", "expiry", "dte", "strike", "bid", "ask", "mid", "delta", "openInterest", "volume",
                 "breakeven", "breakevenPct", "intrinsic", "extrinsic", "cost", "wideSpread", "thin")


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _pos(v) -> float | None:
    f = _num(v)
    return f if f is not None and f > 0 else None


def _int(v) -> int | None:
    f = _num(v)
    return int(f) if f is not None else None


def _delta(v) -> float | None:
    """A call delta the rule may use: finite and inside (0, 1]. Schwab stamps +/-999 (or 0) on every
    greek outside regular hours (see api_server's OI finder); such a sentinel is "no delta", so the
    0.80 x price fallback decides instead of a tie on the lowest strike."""
    f = _num(v)
    return f if f is not None and 0.0 < f <= 1.0 else None


def _date(text) -> date | None:
    """The date in the first 10 characters of a map key or an expirationDate; None when unreadable."""
    if not isinstance(text, str) or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _result(contract: dict | None, reason: str, expiries: list[str]) -> dict:
    return {"contract": contract, "reason": reason, "expiries": expiries}


def _expiries(chain: dict | None, today: date | None) -> list[tuple[int, str, list[dict]]] | None:
    """Every expiry in the call map as (dte, iso date, contracts). None when there is no call map."""
    if not isinstance(chain, dict):
        return None
    calls = chain.get("callExpDateMap")
    if not isinstance(calls, dict):
        return None
    out: list[tuple[int, str, list[dict]]] = []
    for key, by_strike in calls.items():
        if not isinstance(by_strike, dict):
            continue
        contracts: list[dict] = []
        for strike_key, items in by_strike.items():
            if not isinstance(items, list):
                continue
            for c in items:
                if isinstance(c, dict):
                    contracts.append({**c, "_strikeKey": strike_key})
        expiry = _date(key)
        if expiry is None:
            for c in contracts:
                expiry = _date(c.get("expirationDate"))
                if expiry is not None:
                    break
        if expiry is None:
            continue
        if today is not None:
            dte = (expiry - today).days
        else:
            dte = None
            for c in contracts:
                dte = _int(c.get("daysToExpiration"))
                if dte is not None:
                    break
            if dte is None and isinstance(key, str) and ":" in key:
                dte = _int(key.rsplit(":", 1)[1])
        if dte is None:
            continue
        out.append((dte, expiry.isoformat(), contracts))
    return out


def _usable(contracts: list[dict], price: float) -> list[dict]:
    """In-the-money calls with a live two-sided quote: (strike, bid, ask, delta, raw)."""
    out = []
    for c in contracts:
        side = c.get("putCall")
        if side is not None and str(side).upper() != "CALL":
            continue
        strike = _pos(c.get("strikePrice"))
        if strike is None:
            strike = _pos(c.get("_strikeKey"))
        if strike is None or strike >= price:
            continue
        bid, ask = _pos(c.get("bid")), _pos(c.get("ask"))
        if bid is None or ask is None or ask < bid:
            continue
        # An ask under the intrinsic value is a stale print against a moved stock (after-hours
        # closing quotes vs an extended-hours price): nobody fills it, so it is not a live quote.
        if ask < price - strike:
            continue
        out.append({"strike": strike, "bid": bid, "ask": ask, "delta": _delta(c.get("delta")), "raw": c})
    return out


def _choose(usable: list[dict], price: float) -> dict | None:
    with_delta = [u for u in usable if u["delta"] is not None]
    if with_delta:
        return min(with_delta, key=lambda u: (abs(u["delta"] - TARGET_DELTA), u["strike"]))
    if usable:
        target = FALLBACK_MONEYNESS * price
        return min(usable, key=lambda u: (abs(u["strike"] - target), u["strike"]))
    return None


def _contract(u: dict, expiry: str, dte: int, price: float) -> dict:
    raw = u["raw"]
    strike, bid, ask = u["strike"], u["bid"], u["ask"]
    mid = (bid + ask) / 2.0
    breakeven = strike + ask
    intrinsic = max(price - strike, 0.0)
    symbol = raw.get("symbol")
    oi = _int(raw.get("openInterest"))
    return {
        "symbol": str(symbol).strip() if symbol is not None else None,
        "expiry": expiry,
        "dte": dte,
        "strike": strike,
        "bid": bid,
        "ask": ask,
        "mid": mid,
        "delta": u["delta"],
        "openInterest": oi,
        "volume": _int(raw.get("totalVolume")),
        "breakeven": breakeven,
        "breakevenPct": breakeven / price - 1.0,
        "intrinsic": intrinsic,
        "extrinsic": ask - intrinsic,
        "cost": ask * 100.0,
        "wideSpread": bool(mid > 0 and (ask - bid) / mid > MAX_SPREAD),
        "thin": oi is None or oi < THIN_OI,
    }


def pick_leap(chain: dict | None, *, price: float | None, today: date | None = None) -> dict:
    """-> {"contract": Contract | None, "reason": str, "expiries": [iso, ...]}.

    ``expiries`` lists every expiry inside the 12-25 month window (nearest first), whether or not it
    carried a usable contract. ``reason`` is "" when a contract was found. DTE is measured from
    ``today`` when given, else taken from the contract's ``daysToExpiration``.
    """
    try:
        if isinstance(today, datetime):
            today = today.date()
        found = _expiries(chain, today)
        if found is None:
            return _result(None, REASON_NO_CHAIN, [])
        if not found:
            # Schwab answered the windowed request with an empty call map: the chain came back
            # fine, the company simply lists no option 12-25 months out. A verdict, not an outage.
            return _result(None, REASON_NO_EXPIRY, [])
        window = sorted((e for e in found if LEAP_MIN_DTE <= e[0] <= LEAP_MAX_DTE), key=lambda e: (e[0], e[1]))
        expiries = [iso for _, iso, _ in window]
        p = _pos(price)
        if p is None:
            return _result(None, REASON_NO_PRICE, expiries)
        if not window:
            return _result(None, REASON_NO_EXPIRY, [])
        for dte, iso, contracts in reversed(window):              # farthest first
            best = _choose(_usable(contracts, p), p)
            if best is not None:
                return _result(_contract(best, iso, dte, p), "", expiries)
        return _result(None, REASON_NO_CONTRACT, expiries)
    except Exception:  # noqa: BLE001 - a malformed chain is "no chain", never an error
        return _result(None, REASON_NO_CHAIN, [])
