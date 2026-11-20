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
