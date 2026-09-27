"""Tests for momx.movers - the self-refreshing "day's gainers" scan list.

The behaviour that justifies this module existing: on 2026-09-03 the raw
gainers feed's top fourteen were nine warrants/preferreds/sub-$1 names and
CHPT. If the filter is wrong, the board fills with things he cannot trade and
the one name that mattered is buried. Every case below is taken from that
live payload.

No network: fetch_gainers is the only function that touches one, and the
selection is tested on the data instead.
"""

from __future__ import annotations

from momx import movers


# The real top-14 from the screener on 2026-09-03 17:17Z.
LIVE_GAINERS = [
    {"symbol": "FSHPR", "price": 0.13, "percent_change": 159.48},
    {"symbol": "CHPT", "price": 9.03, "percent_change": 73.89},
    {"symbol": "SHFSW", "price": 0.02, "percent_change": 58.20},
    {"symbol": "TANH", "price": 0.36, "percent_change": 54.72},
    {"symbol": "SPWR", "price": 0.39, "percent_change": 53.76},
    {"symbol": "BRLSW", "price": 0.05, "percent_change": 51.30},
    {"symbol": "GIPR", "price": 0.57, "percent_change": 43.32},
    {"symbol": "MIMI", "price": 0.91, "percent_change": 41.69},
    {"symbol": "SKYAW", "price": 0.03, "percent_change": 40.73},
    {"symbol": "SNOU", "price": 79.92, "percent_change": 39.70},
    {"symbol": "DSS", "price": 0.77, "percent_change": 39.51},
    {"symbol": "BIAFW", "price": 0.29, "percent_change": 34.09},
    {"symbol": "AEHL", "price": 7.28, "percent_change": 34.07},
    {"symbol": "CUBWW", "price": 0.22, "percent_change": 33.50},
]


def test_the_live_payload_reduces_to_the_tradeable_names():
    """The whole point: CHPT survives, the warrants and penny stocks do not."""
    assert movers.select_movers(LIVE_GAINERS) == ["CHPT", "SNOU", "AEHL"]


def test_chpt_is_kept_because_it_is_the_case_this_exists_for():
    # It ran +72% on 19x volume and the watchlist scanner never saw it.
    assert "CHPT" in movers.select_movers(LIVE_GAINERS)


def test_warrants_units_rights_and_preferreds_are_dropped():
    for symbol in ("SHFSW", "BRLSW", "SKYAW", "BIAFW", "CUBWW", "FSHPR"):
        assert movers.is_tradeable_symbol(symbol) is False, symbol


def test_ordinary_tickers_survive_the_suffix_filter():
    for symbol in ("CHPT", "AAPL", "F", "SNOU", "GOOGL", "AEHL"):
        assert movers.is_tradeable_symbol(symbol) is True, symbol


def test_malformed_symbols_are_rejected():
    for symbol in ("", None, "BRK.B", "ABC-D", "TOOLONG", "123", "AA PL"):
        assert movers.is_tradeable_symbol(symbol) is False, symbol


def test_the_price_floor_is_the_scans_own_three_dollars():
    # Exactly 3.00 passes, matching scan.price_floor's inclusive boundary.
    rows = [
        {"symbol": "AAA", "price": 3.00, "percent_change": 20.0},
        {"symbol": "BBB", "price": 2.99, "percent_change": 99.0},
    ]
    assert movers.select_movers(rows) == ["AAA"]


def test_small_movers_are_trimmed():
    rows = [{"symbol": "AAA", "price": 50.0, "percent_change": 3.9}]
    assert movers.select_movers(rows) == []
    assert movers.select_movers(rows, min_percent=3.0) == ["AAA"]


def test_biggest_mover_first_and_capped():
    rows = [
        {"symbol": "AAA", "price": 10.0, "percent_change": 10.0},
        {"symbol": "BBB", "price": 10.0, "percent_change": 30.0},
        {"symbol": "CCC", "price": 10.0, "percent_change": 20.0},
    ]
    assert movers.select_movers(rows) == ["BBB", "CCC", "AAA"]
    assert movers.select_movers(rows, limit=2) == ["BBB", "CCC"]


def test_names_already_on_another_list_are_skipped():
    """The movers board is what he is NOT already watching."""
    picked = movers.select_movers(LIVE_GAINERS, exclude=["chpt", "SNOU"])
    assert picked == ["AEHL"]


def test_duplicates_collapse():
    rows = [
        {"symbol": "AAA", "price": 10.0, "percent_change": 30.0},
        {"symbol": "aaa", "price": 10.0, "percent_change": 12.0},
    ]
    assert movers.select_movers(rows) == ["AAA"]


def test_select_is_total():
    assert movers.select_movers(None) == []
    assert movers.select_movers("gainers") == []
    assert movers.select_movers([None, 5, "x", {}]) == []
    assert movers.select_movers([{"symbol": "AAA", "price": "n/a", "percent_change": 9}]) == []
    assert movers.select_movers(LIVE_GAINERS, limit="oops")  # falls back to the default cap


def test_membership_changed_ignores_reordering():
    """A re-rank must NOT trigger a rebuild - the feed re-sorts constantly."""
    assert movers.membership_changed(["AAA", "BBB"], ["BBB", "AAA"]) is False
    assert movers.membership_changed(["aaa", "bbb"], ["BBB", "AAA"]) is False
    assert movers.membership_changed(["AAA"], ["AAA", "BBB"]) is True
    assert movers.membership_changed([], ["AAA"]) is True
    assert movers.membership_changed(None, []) is False


def test_fetch_without_credentials_is_empty_not_an_exception():
    # An empty result means "leave the list alone", never "empty the board".
    assert movers.fetch_gainers("", "") == []
    assert movers.fetch_gainers(None, None) == []


# ----------------------------------------------------------------------
# Leveraged wrappers - the second wave of noise (2026-09-03)
#
# After the price and suffix filters, 20 of the 26 survivors were 2x/3x
# single-stock ETFs: MSTX/MSTU/MSTP/MSTC on MSTR, HOOC/HOOX/HOOG on HOOD,
# CRCA/CRCG on CRCL. The symbol cannot tell you - only the asset name can.
# ----------------------------------------------------------------------

REAL_NAMES = {
    "CHPT": "ChargePoint Holdings, Inc.",
    "AEHL": "Antelope Enterprise Holdings Limited Class A Ordinary Shares",
    "MSTX": "Tidal Trust II Defiance Daily Target 2x Long MSTR ETF",
    "HOOG": "Themes ETF Trust Leverage Shares 2X Long HOOD Daily ETF",
    "SNOU": "T-REX 2X Long SNOW Daily Target ETF",
    "CRCA": "ProShares Ultra CRCL",
    "RIOX": "Defiance Daily Target 2X Long RIOT ETF",
}


def test_leveraged_wrappers_are_recognised_from_their_names():
    for symbol in ("MSTX", "HOOG", "SNOU", "CRCA", "RIOX"):
        assert movers.is_leveraged_name(REAL_NAMES[symbol]) is True, symbol


def test_real_companies_are_not():
    for symbol in ("CHPT", "AEHL"):
        assert movers.is_leveraged_name(REAL_NAMES[symbol]) is False, symbol


def test_plain_etfs_are_deliberately_kept():
    # A sector ETF leading the gainers says something about the day.
    assert movers.is_leveraged_name("SPDR S&P 500 ETF Trust") is False
    assert movers.is_leveraged_name("Invesco QQQ Trust") is False


def test_drop_leveraged_keeps_order_and_keeps_the_unknown():
    picked = ["CHPT", "SNOU", "AEHL", "MSTX", "WHOKNOWS"]
    kept = movers.drop_leveraged(picked, REAL_NAMES)
    # WHOKNOWS has no name looked up: a failed assets call must not delete a
    # real company from the board.
    assert kept == ["CHPT", "AEHL", "WHOKNOWS"]


def test_drop_leveraged_is_total():
    assert movers.drop_leveraged([], None) == []
    assert movers.drop_leveraged(["AAA"], None) == ["AAA"]
    assert movers.is_leveraged_name(None) is False
