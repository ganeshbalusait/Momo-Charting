"""LEAP to buy: the two call contracts the Long Term page shows for a LEAPS-flagged row.

Pure: ``pick_leap`` takes a Schwab ``/chains`` payload (``callExpDateMap``) and the row's price and
returns both picks (or the exact reason there is none). Malformed input never raises.

The rule (v2, 2026-11-06 - pick it like the Options chain page marks ENTRY):
  * expiry window 90..365 days out (3-12 months; Ganesh 11-06 15:25: "Leap I will take 3 months to 12
    months" - the earlier 12-25 month window is gone). Both picks sit on the SAME expiry: the FARTHEST
    expiry in the window that has a usable ENTRY pick; if none has one, the farthest with a usable
    deep pick (then ``contract`` is None and ``deep`` carries the stock substitute).
  * a usable contract is a CALL with a live two-sided quote (bid > 0, ask > 0, ask >= bid) whose ask is
    not under its intrinsic value; a delta is usable only when finite and inside (0, 1] (Schwab's
    +/-999 or 0 after-hours sentinel counts as no delta)
  * ENTRY pick (``contract``): out of the money (strike > price), delta from 0.10 up to but NOT
    including 0.20 - the chain page's own ENTRY rule (momx/option_track.py::pick_entry_strike and
    App.jsx::buildOptionRoiEstimates take 0 < delta < 0.20; Ganesh 11-06: "high oi less than 0.2
    delta") - the HIGHEST open interest; ties -> delta closest to 0.20 -> lower strike. It needs a
    usable delta: an expiry with no deltas has no ENTRY pick (never guess an out-of-the-money strike).
    Open interest missing counts as 0.
  * reason: "" with an ENTRY pick. Live quotes with NO usable delta on any of them mean the chain feed
    is hiding its greeks (Schwab stamps +/-999 or 0 after 16:00 ET) -> REASON_NO_DELTA, a passing
    state the page retries, never a verdict on the row (the deep pick still comes from the 0.80 x price
    fallback). Deltas present but no out-of-the-money call inside the band -> REASON_NO_ENTRY, a verdict.
  * deep pick (``deep``, the Rule #1 stock substitute): in the money (strike < price); among usable
    deltas within +/-0.05 of 0.75 the HIGHEST open interest; ties -> delta closest to 0.75 -> lower
    strike. No usable delta inside 0.70..0.80 but some deltas -> delta closest to 0.75 (ties deeper).
    No deltas at all -> the strike closest to 0.80 x price among the quoted in-the-money calls.
"""
from __future__ import annotations

import math
from datetime import date, datetime

LEAP_MIN_DTE = 90       # expiry window: 3 months out (inclusive) ...
LEAP_MAX_DTE = 365      # ... to 12 months out (inclusive); the farthest expiry inside it wins
ENTRY_DELTA_LO = 0.10   # ENTRY pick: out-of-the-money call with delta from here (inclusive) ...
ENTRY_DELTA_HI = 0.20   # ... up to but NOT including this: the chain page's ENTRY is 0 < delta < 0.20
ENTRY_TARGET = 0.20     # ... ties on open interest go to the delta closest to this
TARGET_DELTA = 0.75     # deep pick: in-the-money call near this delta ...
DEEP_DELTA_BAND = 0.05  # ... highest open interest inside +/- this band
FALLBACK_MONEYNESS = 0.80
MAX_SPREAD = 0.25
THIN_OI = 50            # open interest under this (or unknown) is a thin market: warn, never hide
_EPS = 1e-9             # band edges are inclusive even when 0.80 - 0.75 is 0.050000000000000044 in floats

REASON_NO_CHAIN = "no chain"
REASON_NO_PRICE = "no price"
REASON_NO_EXPIRY = "no expiry 3-12 months out"
REASON_NO_CONTRACT = "no in-the-money call with a live quote"      # v1 reason, kept for older callers
REASON_NO_ENTRY = "no out-of-the-money call near 0.20 delta with a live quote"
REASON_NO_DELTA = "no deltas from the chain feed (hidden outside market hours)"   # a feed state, retried
REASON_CHAIN_UNAVAILABLE = "chain unavailable"
REASON_NOT_FLAGGED = "LEAPS not flagged"

CONTRACT_KEYS = ("kind", "symbol", "expiry", "dte", "strike", "bid", "ask", "mid", "delta", "openInterest", "volume",
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
    greek outside regular hours (see api_server's OI finder); such a sentinel is "no delta"."""
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


def _result(contract: dict | None, deep: dict | None, reason: str, expiries: list[str]) -> dict:
    return {"contract": contract, "deep": deep, "reason": reason, "expiries": expiries}


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
    """Calls with a live two-sided quote, in or out of the money (never at the money):
    (strike, bid, ask, delta, oi, raw). ``oi`` is the open interest with missing/junk as 0."""
    out = []
    for c in contracts:
        side = c.get("putCall")
        if side is not None and str(side).upper() != "CALL":
            continue
        strike = _pos(c.get("strikePrice"))
        if strike is None:
            strike = _pos(c.get("_strikeKey"))
        if strike is None or strike == price:
            continue
        bid, ask = _pos(c.get("bid")), _pos(c.get("ask"))
        if bid is None or ask is None or ask < bid:
            continue
        # An ask under the intrinsic value is a stale print against a moved stock (after-hours
        # closing quotes vs an extended-hours price): nobody fills it, so it is not a live quote.
        if ask < price - strike:
            continue
        oi = _int(c.get("openInterest"))
        out.append({"strike": strike, "bid": bid, "ask": ask, "delta": _delta(c.get("delta")),
                    "oi": oi if oi is not None and oi > 0 else 0, "raw": c})
    return out


def _choose_entry(usable: list[dict], price: float) -> dict | None:
    """The ENTRY-style pick: out of the money, delta from 0.10 to just under 0.20, most open interest."""
    band = [u for u in usable
            if u["strike"] > price and u["delta"] is not None
            and ENTRY_DELTA_LO - _EPS <= u["delta"] < ENTRY_DELTA_HI]
    if not band:
        return None
    return min(band, key=lambda u: (-u["oi"], abs(u["delta"] - ENTRY_TARGET), u["strike"]))


def _choose_deep(usable: list[dict], price: float) -> dict | None:
    """The deep in-the-money pick (stock substitute)."""
    itm = [u for u in usable if u["strike"] < price]
    with_delta = [u for u in itm if u["delta"] is not None]
    band = [u for u in with_delta if abs(u["delta"] - TARGET_DELTA) <= DEEP_DELTA_BAND + _EPS]
    if band:
        return min(band, key=lambda u: (-u["oi"], abs(u["delta"] - TARGET_DELTA), u["strike"]))
    if with_delta:
        return min(with_delta, key=lambda u: (abs(u["delta"] - TARGET_DELTA), u["strike"]))
    if itm:
        target = FALLBACK_MONEYNESS * price
        return min(itm, key=lambda u: (abs(u["strike"] - target), u["strike"]))
    return None


def _contract(u: dict, kind: str, expiry: str, dte: int, price: float) -> dict:
    raw = u["raw"]
    strike, bid, ask = u["strike"], u["bid"], u["ask"]
    mid = (bid + ask) / 2.0
    breakeven = strike + ask
    intrinsic = max(price - strike, 0.0)
    symbol = raw.get("symbol")
    oi = _int(raw.get("openInterest"))
    return {
        "kind": kind,
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
    """-> {"contract": Contract | None, "deep": Contract | None, "reason": str, "expiries": [iso, ...]}.

    ``contract`` is the ENTRY-style pick and ``deep`` the stock substitute, both on the same expiry.
    ``expiries`` lists every expiry inside the 3-12 month window (nearest first), whether or not it
    carried a usable contract. ``reason`` is "" when an ENTRY pick was found. DTE is measured from
    ``today`` when given, else taken from the contract's ``daysToExpiration``.
    """
    try:
        if isinstance(today, datetime):
            today = today.date()
        found = _expiries(chain, today)
        if found is None:
            return _result(None, None, REASON_NO_CHAIN, [])
        if not found:
            # Schwab answered the windowed request with an empty call map: the chain came back
            # fine, the company simply lists no option 3-12 months out. A verdict, not an outage.
            return _result(None, None, REASON_NO_EXPIRY, [])
        window = sorted((e for e in found if LEAP_MIN_DTE <= e[0] <= LEAP_MAX_DTE), key=lambda e: (e[0], e[1]))
        expiries = [iso for _, iso, _ in window]
        p = _pos(price)
        if p is None:
            return _result(None, None, REASON_NO_PRICE, expiries)
        if not window:
            return _result(None, None, REASON_NO_EXPIRY, [])
        usable_by_expiry = [(dte, iso, _usable(contracts, p)) for dte, iso, contracts in reversed(window)]
        for dte, iso, usable in usable_by_expiry:                    # farthest first
            entry = _choose_entry(usable, p)
            if entry is not None:
                deep = _choose_deep(usable, p)
                return _result(_contract(entry, "entry", iso, dte, p),
                               _contract(deep, "deep", iso, dte, p) if deep is not None else None, "", expiries)
        # No ENTRY anywhere. Live quotes with no usable delta on ANY of them is the feed hiding its
        # greeks (Schwab stamps +/-999 or 0 after 16:00 ET), not "no such call": say so, so the page
        # retries instead of holding "nothing to buy" until the next reload.
        quoted = [u for _, _, usable in usable_by_expiry for u in usable]
        reason = REASON_NO_DELTA if quoted and all(u["delta"] is None for u in quoted) else REASON_NO_ENTRY
        for dte, iso, usable in usable_by_expiry:                    # farthest deep pick
            deep = _choose_deep(usable, p)
            if deep is not None:
                return _result(None, _contract(deep, "deep", iso, dte, p), reason, expiries)
        return _result(None, None, reason, expiries)
    except Exception:  # noqa: BLE001 - a malformed chain is "no chain", never an error
        return _result(None, None, REASON_NO_CHAIN, [])
