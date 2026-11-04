"""Unit: the Movers list's refresh window and its write-only-on-change pass
(momx_worker._movers_window_open / _movers_refresh).

The screener, the asset-name lookup and the options check are fakes, the
universe file is a scratch path, and no board is rebuilt.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import momx_worker
from momx import board, movers, service

ET = ZoneInfo("America/New_York")

GAINERS = [
    {"symbol": "FSHPR", "price": 0.13, "percent_change": 159.0},    # preferred, sub-$1
    {"symbol": "CHPT", "price": 9.03, "percent_change": 74.0},
    {"symbol": "SHFSW", "price": 0.02, "percent_change": 58.0},     # warrant
    {"symbol": "MSTX", "price": 41.0, "percent_change": 20.0},      # 2x wrapper (by its NAME)
    {"symbol": "NOOPT", "price": 12.0, "percent_change": 9.0},      # confirmed: no options
    {"symbol": "AAPL", "price": 230.0, "percent_change": 5.0},
]
NAMES = {"MSTX": "Tidal Trust II Defiance Daily Target 2x Long MSTR ETF", "CHPT": "ChargePoint Holdings"}


@pytest.mark.parametrize("when, open_", [
    (datetime(2026, 11, 4, 9, 24, tzinfo=ET), False),
    (datetime(2026, 11, 4, 9, 25, tzinfo=ET), True),
    (datetime(2026, 11, 4, 12, 0, tzinfo=ET), True),
    (datetime(2026, 11, 4, 16, 5, tzinfo=ET), True),      # 09:25-16:05 ET inclusive
    (datetime(2026, 11, 4, 16, 6, tzinfo=ET), False),
    (datetime(2026, 11, 6, 10, 0, tzinfo=ET), True),      # Friday
    (datetime(2026, 11, 7, 10, 0, tzinfo=ET), False),     # Saturday
    (datetime(2026, 11, 8, 10, 0, tzinfo=ET), False),     # Sunday
])
def test_the_movers_list_refreshes_only_in_the_regular_session(when, open_):
    assert momx_worker._movers_window_open(when) is open_


@pytest.fixture
def screener(tmp_path, monkeypatch):
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(tmp_path / "universe.json"))
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["SPY", "QQQ"])
    state = {"gainers": list(GAINERS), "rebuilds": []}
    monkeypatch.setattr(momx_worker, "_movers_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(movers, "fetch_gainers", lambda key, secret, **kw: list(state["gainers"]))
    monkeypatch.setattr(movers, "fetch_asset_names", lambda symbols, key, secret, **kw: {s: NAMES[s] for s in symbols if s in NAMES})
    monkeypatch.setattr(service, "drop_no_options", lambda symbols: [s for s in symbols if s != "NOOPT"])
    monkeypatch.setattr(service, "refresh_now", lambda name=None: state["rebuilds"].append(name))
    return state


def test_the_first_pass_writes_the_tradeable_gainers_best_first(screener):
    result = momx_worker._movers_refresh()
    assert result == {"ok": True, "wrote": True, "count": 2, "symbols": ["CHPT", "AAPL"]}
    assert board.load_universe("Movers") == ["CHPT", "AAPL"]
    assert screener["rebuilds"] == ["Movers"]
    # Refreshing membership never yanks the tab he is reading.
    assert board.active_list() == "Mag7"


def test_the_same_names_in_a_new_order_are_not_rewritten(screener):
    momx_worker._movers_refresh()
    before = board.universe_path().read_bytes()
    screener["gainers"] = list(reversed(GAINERS))
    result = momx_worker._movers_refresh()
    assert result == {"ok": True, "reason": "unchanged", "wrote": False, "count": 2}
    assert board.universe_path().read_bytes() == before
    assert screener["rebuilds"] == ["Movers"]                # still only the first write


def test_an_empty_screener_leaves_the_list_exactly_as_it_was(screener):
    momx_worker._movers_refresh()
    before = board.universe_path().read_bytes()
    screener["gainers"] = []
    assert momx_worker._movers_refresh() == {"ok": False, "reason": "no gainers returned", "wrote": False}
    assert board.universe_path().read_bytes() == before
    assert board.load_universe("Movers") == ["CHPT", "AAPL"]


def test_when_nothing_clears_the_filters_nothing_is_written(screener):
    screener["gainers"] = [g for g in GAINERS if g["symbol"] in ("FSHPR", "SHFSW", "MSTX", "NOOPT")]
    assert momx_worker._movers_refresh() == {"ok": True, "reason": "nothing cleared the filters", "wrote": False}
    assert not board.universe_path().exists()
    assert screener["rebuilds"] == []


def test_a_membership_change_rewrites_the_list(screener):
    momx_worker._movers_refresh()
    screener["gainers"] = GAINERS + [{"symbol": "NVDA", "price": 180.0, "percent_change": 30.0}]
    result = momx_worker._movers_refresh()
    assert result["wrote"] is True and result["symbols"] == ["CHPT", "NVDA", "AAPL"]
    assert board.load_universe("Movers") == ["CHPT", "NVDA", "AAPL"]
