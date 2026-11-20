"""Cyclical industries for the Long Term page's Peter view (2026-11-19).

Peter Lynch's warning about cyclicals: their P/E is lowest when earnings are at the top of the cycle,
so "cheap for its growth" is a trap there. The Peter view therefore never calls a cyclical CHEAP; it
shows it as CYCLICAL and leaves the price star out.

The mark comes from the company's SEC industry code (SIC) alone. The list is Lynch's own examples
(autos, airlines, tires, steel, chemicals) plus the commodity and capital-equipment industries that
behave the same way (mining, oil and gas, chips and memory, heavy machinery, homebuilders, cruise
lines). It is a list of industries, not a judgement on any one company: QCOM files under radio
equipment (3663) and is not marked; NVDA files under semiconductors (3674) and is.
"""
from __future__ import annotations

from datetime import date

# (first SIC, last SIC, group shown on the page), inclusive
_RANGES: tuple[tuple[int, int, str], ...] = (
    (1000, 1299, "metals & mining"),
    (1300, 1399, "oil & gas"),
    (1400, 1499, "metals & mining"),
    (1520, 1531, "homebuilders"),
    (2810, 2829, "chemicals"),
    (2860, 2879, "chemicals"),
    (2900, 2999, "oil & gas"),
    (3011, 3011, "tires"),
    (3300, 3399, "metals & mining"),
    (3523, 3523, "heavy machinery"),
    (3531, 3537, "heavy machinery"),
    (3559, 3559, "chips"),            # chip-making equipment (LRCX, ASML)
    (3572, 3572, "chips"),            # memory and drives (WDC, STX, SNDK)
    (3674, 3674, "chips"),
    (3711, 3716, "autos"),
    (4400, 4499, "airlines & cruise lines"),
    (4512, 4512, "airlines & cruise lines"),
)


MAX_YIELD_PCT = 25.0        # above this the "yield" is a data error (a special payout, a price glitch)
MAX_YEAR_GAP_DAYS = 400     # the two inventory-turnover years must be consecutive fiscal years


def _good(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def dividend_yield(finnhub: dict | None) -> float | None:
    """Dividend yield as a fraction of the price (0.0031 = 0.31%) from the stored Finnhub extract,
    whose `dividend_yield_ttm` is a percent. None when missing, negative or absurd."""
    v = (finnhub or {}).get("dividend_yield_ttm") if isinstance(finnhub, dict) else None
    if not _good(v) or v < 0 or v > MAX_YIELD_PCT:
        return None
    return float(v) / 100.0


def inventory_turn_change(finnhub: dict | None) -> float | None:
    """Change of the yearly inventory turnover against the year before (-0.25 = inventory turns over
    25% slower: it grew faster than what was sold). None without two good consecutive years."""
    series = (finnhub or {}).get("inventory_turnover_annual") if isinstance(finnhub, dict) else None
    if not isinstance(series, dict) or len(series) < 2:
        return None
    newest, before = sorted(series, reverse=True)[:2]
    now, prev = series[newest], series[before]
    if not _good(now) or not _good(prev) or now <= 0 or prev <= 0:
        return None
    try:
        gap = (date.fromisoformat(str(newest)[:10]) - date.fromisoformat(str(before)[:10])).days
    except ValueError:
        return None
    if gap <= 0 or gap > MAX_YEAR_GAP_DAYS:
        return None
    return now / prev - 1.0


def cyclical_group(sic) -> str | None:
    """The cyclical industry a SIC code belongs to, or None. Junk and missing codes are None."""
    if isinstance(sic, bool):
        return None
    if isinstance(sic, str):
        sic = sic.strip()
        if not sic.isdigit():
            return None
        sic = int(sic)
    if isinstance(sic, float):
        if sic != sic or sic != int(sic):
            return None
        sic = int(sic)
    if not isinstance(sic, int) or sic <= 0:
        return None
    for first, last, group in _RANGES:
        if first <= sic <= last:
            return group
    return None
