"""Unit (2026-11-19): the two extra numbers a Long Term row carries for the Peter view.

dividendYield: the dividend as a fraction of the price (Finnhub reports a percent).
inventoryTurnChange: how the yearly inventory turnover moved against the year before; a fall means
inventory grew faster than what the company sold, Peter Lynch's warning sign.
"""
from __future__ import annotations

import pytest

from long_term.cyclical import dividend_yield, holds_inventory, inventory_turn_change


@pytest.mark.parametrize("sic", [2000, 2834, 3021, 3571, 3674, 3711, 5211, 5331, 5651, 5990, "3571"])
def test_makers_and_shops_hold_inventory(sic):
    """2026-11-20: Adobe (software, 7372) got "its shelves are filling up" from a turnover figure on
    almost no inventory. The warning is only for companies that make or sell goods."""
    assert holds_inventory(sic) is True


@pytest.mark.parametrize("sic", [7372, 7370, 7389, 6021, 6211, 4813, 4911, 7011, 1311, 8011, None, "", "x", 0, True, 3674.5])
def test_software_banks_services_and_junk_do_not(sic):
    assert holds_inventory(sic) is False


def test_dividend_yield_is_the_percent_as_a_fraction():
    assert dividend_yield({"dividend_yield_ttm": 0.3105}) == pytest.approx(0.003105)
    assert dividend_yield({"dividend_yield_ttm": 4.2}) == pytest.approx(0.042)
    assert dividend_yield({"dividend_yield_ttm": 0}) == 0.0


@pytest.mark.parametrize("fh", [None, {}, {"dividend_yield_ttm": None}, {"dividend_yield_ttm": "1.2"},
                                {"dividend_yield_ttm": -0.5}, {"dividend_yield_ttm": float("nan")},
                                {"dividend_yield_ttm": True},
                                {"dividend_yield_ttm": 40.0}])      # a 40% yield is a data error, not a dividend
def test_a_missing_or_junk_yield_is_none(fh):
    assert dividend_yield(fh) is None


def test_inventory_turn_change_compares_the_two_newest_years():
    fh = {"inventory_turnover_annual": {"2024-01-28": 5.0, "2026-01-25": 3.0, "2025-01-26": 4.0}}
    assert inventory_turn_change(fh) == pytest.approx(-0.25)        # 3.0 against 4.0, whatever the dict order
    assert inventory_turn_change({"inventory_turnover_annual": {"2025-12-31": 6.6, "2024-12-31": 6.0}}) == pytest.approx(0.1)


@pytest.mark.parametrize("fh", [
    None, {}, {"inventory_turnover_annual": {}}, {"inventory_turnover_annual": {"2025-12-31": 6.0}},   # one year only
    {"inventory_turnover_annual": {"2025-12-31": 6.0, "2024-12-31": 0}},                                # no inventory then
    {"inventory_turnover_annual": {"2025-12-31": -2.0, "2024-12-31": 4.0}},
    {"inventory_turnover_annual": {"2025-12-31": "6", "2024-12-31": 4.0}},
    {"inventory_turnover_annual": {"2025-12-31": 6.0, "2022-12-31": 4.0}},      # not consecutive years
    {"inventory_turnover_annual": [6.0, 4.0]},
])
def test_no_comparison_without_two_good_consecutive_years(fh):
    assert inventory_turn_change(fh) is None
