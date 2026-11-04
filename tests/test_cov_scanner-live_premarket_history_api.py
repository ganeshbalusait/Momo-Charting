"""Integration: the Premarket page's 30-day history, GET /api/premarket-scanner/history.

The archive is written by the same recorder the live scanner uses
(premarket_scanner_history.record_rows / record_briefing) into a scratch
folder, and read back through the real route. The route caches its answer
for 60 s: a page polling every 5 minutes must not re-read 30 files each time.
"""
from __future__ import annotations

import functools
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import premarket_scanner_history as archive
from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")


def _row(symbol: str, strength: str = "STRONG") -> dict:
    return {"symbol": symbol, "changePct": 3.1, "strength": strength, "score": 4, "signalsCyanHigher": ["CALLD"]}


@pytest.fixture
def folder(monkeypatch, tmp_path):
    root = tmp_path / "premarket_scanner_history"
    monkeypatch.setattr(api_server, "premarket_history_load", functools.partial(archive.load_history, root))
    monkeypatch.setattr(api_server.STATE, "_premarket_history_response", None, raising=False)
    return root


def test_the_route_returns_newest_day_first_with_arrivals_in_order_and_the_briefing(folder):
    archive.record_rows([_row("AAPL"), _row("NVDA", "MODERATE")], datetime(2026, 11, 2, 7, 5, tzinfo=ET), directory=folder)
    archive.record_rows([_row("NVDA")], datetime(2026, 11, 3, 6, 40, tzinfo=ET), directory=folder)
    archive.record_rows([_row("NVDA"), _row("TSLA")], datetime(2026, 11, 3, 8, 15, tzinfo=ET), directory=folder)
    archive.record_briefing(["**NVDA** gapping up on earnings"], datetime(2026, 11, 3, 8, 0, tzinfo=ET), directory=folder)
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner/history")
    assert status == 200, body
    assert body["retentionDays"] == 30
    assert [day["date"] for day in body["days"]] == ["2026-11-03", "2026-11-02"]
    newest = body["days"][0]
    assert [r["symbol"] for r in newest["rows"]] == ["NVDA", "TSLA"]          # by first seen
    assert newest["rows"][0]["firstSeenAt"].startswith("2026-11-03T06:40")   # never moved by the 08:15 pass
    assert newest["briefing"]["lines"] == ["**NVDA** gapping up on earnings"]
    assert body["days"][1]["briefing"] is None


def test_a_second_ask_inside_a_minute_is_served_from_the_cache(folder):
    archive.record_rows([_row("AAPL")], datetime(2026, 11, 2, 7, 5, tzinfo=ET), directory=folder)
    with running_api() as api:
        _, first = api.get("/api/premarket-scanner/history")
        archive.record_rows([_row("MSFT")], datetime(2026, 11, 3, 7, 5, tzinfo=ET), directory=folder)
        _, cached = api.get("/api/premarket-scanner/history")
        api_server.STATE._premarket_history_response = (api_server.STATE._premarket_history_response[0] - 61,
                                                        api_server.STATE._premarket_history_response[1])
        _, expired = api.get("/api/premarket-scanner/history")
    assert [d["date"] for d in first["days"]] == ["2026-11-02"]
    assert cached == first                                       # the new day waits for the cache
    assert [d["date"] for d in expired["days"]] == ["2026-11-03", "2026-11-02"]


def test_an_empty_or_missing_archive_is_an_empty_list_not_an_error(folder):
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner/history")
    assert status == 200, body
    assert body == {"days": [], "retentionDays": 30}


def test_days_older_than_thirty_are_pruned_on_the_next_write(folder):
    archive.record_rows([_row("OLD")], datetime(2026, 9, 1, 7, 0, tzinfo=ET), directory=folder)
    archive.record_rows([_row("NEW")], datetime(2026, 11, 3, 7, 0, tzinfo=ET), directory=folder)
    with running_api() as api:
        _, body = api.get("/api/premarket-scanner/history")
    assert [d["date"] for d in body["days"]] == ["2026-11-03"]
