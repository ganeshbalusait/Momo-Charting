"""The day's biggest gainers, as a self-refreshing scan list.

2026-09-03. CHPT ran +72% on 19x volume and his board never saw it until he
added the ticker by hand at 12:56 - three and a half hours after MomoX3 had
it. The scan was not at fault: CHPT was not in his 358-name watchlist, and a
watchlist scanner cannot find what it is not watching. This closes that gap by
keeping ONE list populated with the market's movers, so the same scan runs
over names he never typed.

Everything downstream is untouched. The list is written through
``board.save_universe`` like any other, so the board, the history archive, the
phone alerts, the ticker card and the momentum strip all work on it with no
changes - and crucially ``board.set_active_list`` is NOT called, so refreshing
membership can never yank the tab he is reading.

WHY THE FILTERS ARE NOT OPTIONAL. Raw "top gainers" is mostly untradeable.
The live list on the day this was written, top 14 by percent:

    FSHPR  $0.13  +159%     <- preferred, sub-penny
    CHPT   $9.03   +74%     <- the one that mattered
    SHFSW  $0.02   +58%     <- warrant
    TANH   $0.36   +55%     <- sub-$1
    SPWR   $0.39   +54%     <- sub-$1
    BRLSW  $0.05   +51%     <- warrant
    ...

Nine of the first fourteen were warrants, preferreds or sub-$1. Feeding those
to the scan would spend the build on names he cannot trade and bury the one he
can. So the same $3.00 floor his scan already enforces is applied HERE, before
the names ever reach the scan, plus a derivative-suffix filter.

Pure and total: every function takes its data as an argument, returns a value
for any input, and never raises. The fetch is the only side effect and it
lives in one small function so the selection can be tested without a network.
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Iterable, Mapping, Sequence

#: The scan's own floor (momx.scan.PRICE_FLOOR). Repeated rather than imported
#: so this module stays importable without the scan, but they mean the same
#: thing and must move together.
PRICE_FLOOR = 3.00

#: Below this the name is not "moving", it is noise near the top of a quiet
#: tape. Gainers are already sorted by percent, so this only trims the tail.
MIN_PERCENT_CHANGE = 4.0

#: How many survivors to keep. The list is scanned like any other, and ~40
#: names costs the build roughly ten seconds against the watchlist's seventy.
MAX_SYMBOLS = 40

#: NASDAQ/NYSE fifth-letter suffixes for things that are not common stock:
#: W warrant, U unit, R right. Preferreds arrive as a "PR" pair (FSHPR).
#: A five-letter common stock ending in one of these is possible but rare;
#: trading a warrant by accident is worse than missing one.
_DERIVATIVE_SUFFIXES = ("W", "U", "R")

MOVERS_ENDPOINT = "https://data.alpaca.markets/v1beta1/screener/stocks/movers?top=50"
ASSET_ENDPOINT = "https://paper-api.alpaca.markets/v2/assets/"

#: Words that mark a LEVERAGED or INVERSE wrapper rather than a company. The
#: symbol alone cannot tell you - MSTX, HOOG, SNOU and RIOX all look like
#: ordinary four-letter tickers - but the asset name always says so:
#:
#:   MSTX  Tidal Trust II Defiance Daily Target 2x Long MSTR ETF
#:   HOOG  Themes ETF Trust Leverage Shares 2X Long HOOD Daily ETF
#:   SNOU  T-REX 2X Long SNOW Daily Target ETF
#:   CRCA  ProShares Ultra CRCL
#:
#: Measured 2026-09-03: 20 of the 26 names that cleared the price and suffix
#: filters were these, nearly all of them 2x wrappers of the same handful of
#: underlyings. They are not separate opportunities - they are the same move,
#: geared - and they crowd out the names that are.
#:
#: PLAIN ETFs are deliberately NOT excluded. A sector ETF leading the gainers
#: is a real signal about the day, and he already watches several.
_LEVERAGED_MARKERS = (
    "2X", "3X", "-1X", "ULTRA", "ULTRAPRO", "LEVERAGE", "DAILY TARGET",
    "BULL", "BEAR", "INVERSE", "SHORT ",
)


def is_tradeable_symbol(symbol: Any) -> bool:
    """False for warrants, units, rights, preferreds and malformed tickers."""
    text = str(symbol or "").strip().upper()
    if not text or not text.isalpha() or len(text) > 5:
        return False
    if len(text) == 5:
        if text[-1] in _DERIVATIVE_SUFFIXES:
            return False
        if text.endswith("PR"):          # preferred, e.g. FSHPR
            return False
    return True


def is_leveraged_name(name: Any) -> bool:
    """True when an asset NAME marks a leveraged or inverse wrapper."""
    text = str(name or "").upper()
    if not text:
        return False
    return any(marker in text for marker in _LEVERAGED_MARKERS)


def drop_leveraged(symbols: Sequence[str], names: Mapping[str, Any] | None) -> list[str]:
    """Remove the leveraged wrappers, keeping order.

    A symbol with NO name looked up is KEPT: an assets call that failed must
    not silently delete a real company from the board. Being wrong towards
    showing him something is the safe direction here.
    """
    lookup = {str(k).upper(): v for k, v in (names or {}).items()}
    return [s for s in symbols if not is_leveraged_name(lookup.get(str(s).upper()))]


def select_movers(
    gainers: Any,
    *,
    price_floor: float = PRICE_FLOOR,
    min_percent: float = MIN_PERCENT_CHANGE,
    limit: int = MAX_SYMBOLS,
    exclude: Iterable[str] | None = None,
) -> list[str]:
    """The tradeable gainers, best first, de-duplicated and capped.

    ``exclude`` drops names already covered by another list, so the movers
    board is what he is NOT already watching rather than a second copy of the
    watchlist's winners.
    """
    if not isinstance(gainers, (list, tuple)):
        return []
    skip = {str(s).strip().upper() for s in (exclude or ()) if str(s).strip()}
    ranked: list[tuple[float, str]] = []
    seen: set[str] = set()
    for entry in gainers:
        if not isinstance(entry, Mapping):
            continue
        symbol = str(entry.get("symbol") or "").strip().upper()
        if not is_tradeable_symbol(symbol) or symbol in seen or symbol in skip:
            continue
        try:
            price = float(entry.get("price"))
            percent = float(entry.get("percent_change"))
        except (TypeError, ValueError):
            continue
        if price != price or percent != percent:      # NaN
            continue
        if price < price_floor or percent < min_percent:
            continue
        seen.add(symbol)
        ranked.append((percent, symbol))
    ranked.sort(key=lambda item: -item[0])
    try:
        cap = max(0, int(limit))
    except (TypeError, ValueError):
        cap = MAX_SYMBOLS
    return [symbol for _, symbol in ranked[:cap]]


def membership_changed(current: Any, proposed: Sequence[str]) -> bool:
    """True only when the SET differs.

    Order churns constantly - the gainers list re-ranks every few seconds -
    and rewriting the list on a re-rank would trigger a rebuild every cycle
    for no new names. Only a change in membership is worth a rebuild.
    """
    have = {str(s).strip().upper() for s in (current or []) if str(s).strip()}
    want = {str(s).strip().upper() for s in (proposed or []) if str(s).strip()}
    return have != want


_ASSET_NAME_CACHE: dict[str, str] = {}


def fetch_asset_names(
    symbols: Iterable[str], key_id: str, secret_key: str, *, timeout: float = 12.0
) -> dict[str, str]:
    """symbol -> asset name, cached for the process.

    Names never change, so this is asked once per symbol ever. A failure for
    one symbol yields no entry for it, which drop_leveraged reads as "keep".
    """
    out: dict[str, str] = {}
    if not key_id or not secret_key:
        return out
    for raw in symbols or ():
        symbol = str(raw or "").strip().upper()
        if not symbol:
            continue
        if symbol in _ASSET_NAME_CACHE:
            out[symbol] = _ASSET_NAME_CACHE[symbol]
            continue
        request = urllib.request.Request(
            ASSET_ENDPOINT + symbol,
            headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
            name = str((payload or {}).get("name") or "")
        except Exception:  # noqa: BLE001 - an unknown asset is not fatal
            continue
        if name:
            _ASSET_NAME_CACHE[symbol] = name
            out[symbol] = name
    return out


def fetch_gainers(key_id: str, secret_key: str, *, timeout: float = 20.0) -> list[dict]:
    """The screener's gainers, or [] for any failure at all.

    The ONLY network call in this module. Total by contract: a dead key, a
    rate limit, a timeout or a shape change all yield an empty list, and an
    empty list means "leave the list exactly as it is" to the caller - never
    "empty the board".
    """
    if not key_id or not secret_key:
        return []
    request = urllib.request.Request(
        MOVERS_ENDPOINT,
        headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - a screener outage must not touch the board
        return []
    gainers = payload.get("gainers") if isinstance(payload, Mapping) else None
    return [row for row in (gainers or []) if isinstance(row, Mapping)]
