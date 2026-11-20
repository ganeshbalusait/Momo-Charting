"""Unit (2026-11-19): which companies the Long Term page's Peter view marks as cyclical.

Peter Lynch's warning: a cyclical looks cheapest (lowest P/E) when its earnings are at the top of the
cycle. The mark comes from the company's SEC industry code (SIC), nothing else.
"""
from __future__ import annotations

import pytest

from long_term.cyclical import cyclical_group


@pytest.mark.parametrize("sic, group", [
    (1000, "metals & mining"), (1040, "metals & mining"), (1090, "metals & mining"), (1400, "metals & mining"),
    (3334, "metals & mining"),                      # SCCO, NEM, CCJ, HL, AA
    (1311, "oil & gas"), (1389, "oil & gas"), (2911, "oil & gas"),          # OXY, SLB, MPC
    (3674, "chips"), (3559, "chips"), (3572, "chips"),                      # MU / NVDA, LRCX / ASML, WDC / STX
    (3711, "autos"), (3714, "autos"),                                       # F / GM, parts
    (3531, "heavy machinery"), (3523, "heavy machinery"),                   # CAT, farm machinery
    (4512, "airlines & cruise lines"), (4400, "airlines & cruise lines"),   # DAL, CCL
    (1531, "homebuilders"),                                                 # DHI
    (2821, "chemicals"), (2870, "chemicals"),
    (3011, "tires"),
])
def test_cyclical_industries(sic, group):
    assert cyclical_group(sic) == group


@pytest.mark.parametrize("sic", [
    7372, 7370,     # software, internet: ADBE, GOOGL
    3571, 3576,     # computers, networking: AAPL, ANET
    3663,           # radio / phone equipment: QCOM files here, not under chips
    2834, 2000,     # drugs, food
    5331, 5651,     # retail
    6021, 6211,     # banks, brokers
    7011, 4700,     # hotels, travel booking: not on Lynch's list
    3021,           # footwear (DECK) is next to tires (3011) but is not one
    2840,           # soap (PG) sits between the chemical ranges
])
def test_everything_else_is_not_marked(sic):
    assert cyclical_group(sic) is None


@pytest.mark.parametrize("sic", [None, "", "abc", 0, -5, True, 3674.5, float("nan")])
def test_a_missing_or_junk_code_is_never_cyclical(sic):
    assert cyclical_group(sic) is None


def test_a_code_stored_as_text_still_counts():
    assert cyclical_group("3674") == "chips"
