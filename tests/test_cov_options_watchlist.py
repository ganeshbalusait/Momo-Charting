"""Unit: the watchlist persisters in DashboardState (My Watchlist and the Mag7
Watchlist). A bare DashboardState with a fake repository; watchlist.txt is a
scratch file - the real one lives in the repo root and is never touched."""
from __future__ import annotations

import pytest

import api_server
from api_server import DashboardState


class FakeRepository:
    def __init__(self):
        self.settings: dict[str, str] = {}
        self.events: list[tuple[str, str]] = []

    def set_app_setting(self, key, value):
        self.settings[key] = value

    def log_bot_event(self, event_type, message, payload_json=""):
        self.events.append((event_type, message))


@pytest.fixture
def state(monkeypatch, tmp_path):
    path = tmp_path / "watchlist.txt"
    path.write_text("AAPL\n", encoding="utf-8")
    monkeypatch.setattr(api_server, "WATCHLIST_PATH", path)
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", ["AAPL"])
    bare = DashboardState.__new__(DashboardState)
    bare.repository = FakeRepository()
    bare.watchlist_path = path
    return bare


def test_normalize_uppercases_trims_dedupes_and_keeps_the_traders_order(state):
    assert state._normalize_watchlist(["aapl", " AAPL", "", None, "nvda ", "spy", "NVDA"]) == ["AAPL", "NVDA", "SPY"]


def test_removing_the_last_ticker_is_refused_and_nothing_changes(state):
    with pytest.raises(ValueError, match="Watchlist cannot be empty."):
        state.remove_watchlist_symbol("aapl")
    assert state.watchlist_path.read_text(encoding="utf-8") == "AAPL\n"
    assert api_server.settings.scanner.default_universe == ["AAPL"]
    assert state.repository.events == []


def test_removing_the_last_mag7_ticker_is_refused_and_nothing_is_saved(state):
    state.mag7_scanner_watchlist = ["METU"]
    with pytest.raises(ValueError, match="Mag7 scanner watchlist cannot be empty."):
        state.remove_mag7_scanner_symbol("metu")
    assert state.mag7_scanner_watchlist == ["METU"]
    assert state.repository.settings == {}


def test_mag7_leveraged_tickers_map_to_their_option_underlyings(state):
    state.mag7_scanner_watchlist = ["METU", "AMDL", "AAPL", "META"]
    # METU and META both scan META once; AMDL scans AMD.
    assert state._mag7_oi_underlyings() == ["META", "AMD", "AAPL"]
