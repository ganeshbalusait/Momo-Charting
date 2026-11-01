"""Open interest for INDEX options from CBOE's public delayed-quotes feed.

His ask 2026-10-04: "I have added SPX ticker, I don't see charts and options". Schwab's
chain for $SPX has prices, volume, bid/ask and greeks but openInterest = 0 on EVERY
contract (checked 2026-10-04: 60 SPX contracts, total OI 0; SPY in the same request
89,884). CBOE - the exchange that lists SPX - publishes the whole chain with open
interest at cdn.cboe.com/api/global/delayed_quotes/options/_SPX.json (29,282 contracts,
21,484 with OI). Open interest is a once-a-day number, so the feed is cached for an hour.

``fill_open_interest(symbol, chain)`` patches a Schwab chain dict in place: every leg whose
openInterest is 0/missing gets CBOE's number for the same contract. Never raises; when
CBOE is unreachable the chain is returned unchanged.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any, Callable

from data.index_symbols import is_index

CBOE_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/_{root}.json"
CACHE_TTL_SECONDS = 3600.0
FAILURE_TTL_SECONDS = 300.0

_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, dict[str, float]]] = {}
_FAILED: dict[str, float] = {}


def _contract_key(symbol: Any) -> str:
    """'SPXW  261005C07700000' (Schwab) and 'SPXW261005C07700000' (CBOE) -> one key."""
    return "".join(str(symbol or "").upper().split())


def _default_get(url: str) -> dict:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (AGX MomX)"})
    with urllib.request.urlopen(req, timeout=60) as resp:     # follows CBOE's 307 to cdn-api
        return json.loads(resp.read().decode("utf-8"))


def oi_map(root: str, get: Callable[[str], dict] | None = None, now: float | None = None) -> dict[str, float]:
    """{contract_key: open_interest} for one index root (SPX, NDX ...). {} on failure."""
    root = str(root or "").strip().upper().lstrip("$")
    clock = time.monotonic() if now is None else now
    with _LOCK:
        held = _CACHE.get(root)
        if held and clock - held[0] < CACHE_TTL_SECONDS:
            return held[1]
        if clock - _FAILED.get(root, -1e18) < FAILURE_TTL_SECONDS:
            return {}
    try:
        payload = (get or _default_get)(CBOE_URL.format(root=root))
        options = ((payload or {}).get("data") or {}).get("options") or []
        out = {}
        for o in options:
            oi = o.get("open_interest")
            if isinstance(oi, (int, float)) and oi > 0:
                out[_contract_key(o.get("option"))] = float(oi)
    except Exception:  # noqa: BLE001 - CBOE down = no index OI, never a broken chain
        with _LOCK:
            _FAILED[root] = clock
        return {}
    with _LOCK:
        _CACHE[root] = (clock, out)
    return out


def fill_open_interest(symbol: str, chain: Any, get: Callable[[str], dict] | None = None) -> Any:
    """Patch an index chain's zero open interest from CBOE, in place; returns ``chain``."""
    if not is_index(symbol) or not isinstance(chain, dict):
        return chain
    oi = oi_map(symbol, get=get)
    if not oi:
        return chain
    for side in ("callExpDateMap", "putExpDateMap"):
        for strikes in (chain.get(side) or {}).values():
            for legs in (strikes or {}).values():
                for leg in legs or []:
                    if isinstance(leg, dict) and not leg.get("openInterest"):
                        value = oi.get(_contract_key(leg.get("symbol")))
                        if value:
                            leg["openInterest"] = int(value)
    return chain
