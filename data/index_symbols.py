"""Cash-index symbols (his ask 2026-10-04: "I have added SPX ticker, I don't see charts and
options").

The app and the trader write an index the way TOS shows it - SPX, NDX, RUT, VIX - but
Schwab's market-data API only answers to its ``$`` form: ``$SPX`` returns a quote
(7722.72 on 2026-10-02), 5m / daily price history and the option chain (isIndex=True),
while plain ``SPX`` returns an error. Alpaca carries no index bars at all, so an index
must be served from Schwab.

One translation, used at the Schwab boundary only: ``to_schwab`` on every symbol going
out, ``from_schwab`` on every symbol coming back, so the rest of the app keeps "SPX".
"""
from __future__ import annotations

#: Cash indexes with listed options or a common chart (SPXW / NDXP are option ROOTS of
#: SPX / NDX, not tradable underlyings, so they are not here).
INDEX_ROOTS: frozenset[str] = frozenset({"SPX", "NDX", "RUT", "VIX", "XSP", "DJX", "OEX"})


def is_index(symbol: str) -> bool:
    s = str(symbol or "").strip().upper().lstrip("$")
    return s in INDEX_ROOTS


def to_schwab(symbol: str) -> str:
    """'SPX' -> '$SPX'; every other symbol (and an already-$ one) unchanged."""
    s = str(symbol or "").strip().upper()
    return "$" + s if s in INDEX_ROOTS else s


def from_schwab(symbol: str) -> str:
    """'$SPX' -> 'SPX' for the indexes above; anything else unchanged."""
    s = str(symbol or "").strip().upper()
    return s[1:] if s.startswith("$") and s[1:] in INDEX_ROOTS else s
