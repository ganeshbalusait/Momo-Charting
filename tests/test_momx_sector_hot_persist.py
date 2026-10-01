"""The day's 🔥 hot-sector latch survives a worker restart (2026-09-25)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import sectors

ET = ZoneInfo("America/New_York")


def test_hot_latch_is_saved_and_read_back(tmp_path):
    book = sectors.RotationBook(fetch=lambda symbols: {}, background=False, always_on=True, directory=tmp_path)
    state = {"hot": set(), "leaders": {}}
    book._save_hot("Watchlist", "2026-09-25", {"AI - Data Centers", "Defense"},
                   {"AI - Data Centers": [{"symbol": "DELL", "rank": 1}]})
    again = sectors.RotationBook(fetch=lambda symbols: {}, background=False, always_on=True, directory=tmp_path)
    again._load_hot("Watchlist", "2026-09-25", state)
    assert state["hot"] == {"AI - Data Centers", "Defense"}
    assert state["leaders"]["AI - Data Centers"][0]["symbol"] == "DELL"


def test_apply_after_restart_keeps_the_latched_sector(tmp_path):
    first = sectors.RotationBook(fetch=lambda symbols: {}, background=False, always_on=True, directory=tmp_path)
    first._save_hot("Watchlist", "2026-09-25", {"Defense"}, {})
    book = sectors.RotationBook(fetch=lambda symbols: {}, background=False, always_on=True, directory=tmp_path)
    rows = [{"symbol": s, "industry": "Defense", "pctChange": 0.1, "last": 10.0, "m5": {"vwap": 9.9}} for s in ("LHX", "AVAV", "NOC", "LMT")]
    payload = {"rows": rows, "rest": []}
    book.apply("Watchlist", payload, datetime(2026, 9, 25, 12, 0, tzinfo=ET))
    assert book._state["Watchlist"]["hot"] == {"Defense"}
