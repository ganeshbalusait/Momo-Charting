"""Which tickers the LEAP OI Scanner and AGX Flow boards leave out (2026-11-17).

Ganesh: "No need spy, qqq and small stocks and etf". Two rules, shared by both pages:
  * ETFs (SPY, QQQ, leveraged single-stock funds like AAPU/MSTU, IBIT, ...) are never read or shown. The authority is
    momx/etf_list.py (the exchange's own ETF flag, also used by the scanner's "stocks only" rule); cash indexes count
    as ETFs there too.
  * A stock under MIN_PRICE dollars is left off the board. Market cap is missing for most of the watchlist, so share
    price is the size test; it is known only after one read of the ticker's chain.
Facts only: this only decides what the page shows.
"""
from __future__ import annotations

from typing import Any, Callable

MIN_PRICE = 10.0


def _directory_is_etf(symbol: str) -> bool | None:
    from momx import etf_list
    return etf_list.is_etf(symbol)


#: The ETF test. Tests put a fixed set here so they never depend on the downloaded directory.
is_etf: Callable[[str], bool | None] = _directory_is_etf


def drop_etfs(tickers: list[str]) -> tuple[list[str], list[dict]]:
    """(stocks kept in order, [{"ticker", "reason": "ETF"}])."""
    kept: list[str] = []
    left: list[dict] = []
    for ticker in tickers:
        try:
            etf = bool(is_etf(ticker))
        except Exception:  # noqa: BLE001 - an unknown symbol is shown, never hidden by a lookup failure
            etf = False
        if etf:
            left.append({"ticker": ticker, "reason": "ETF"})
        else:
            kept.append(ticker)
    return kept, left


def too_small(spot: Any) -> bool:
    """True when the share price is known and under MIN_PRICE."""
    try:
        price = float(spot)
    except (TypeError, ValueError):
        return False
    return 0 < price < MIN_PRICE


def small(ticker: str) -> dict:
    return {"ticker": ticker, "reason": f"under ${MIN_PRICE:.0f}"}
