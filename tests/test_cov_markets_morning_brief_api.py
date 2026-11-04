"""Integration: the morning briefing card and the Scanner History brief,
through the real routes over HTTP (2026-11-04 coverage).

The brief is BUILT by a background thread and only READ by the route. Here
the real builder (_build_morning_briefing + morning_briefing.build_briefing)
runs once with Schwab quotes, Alpaca news and the ntfy push faked, and the
archive pointed at a scratch folder: premarket_scanner_history's default
directory is the relative live artifacts/ path, so every archive function the
server calls is re-pointed here.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime
from functools import partial
from zoneinfo import ZoneInfo

import pytest

import premarket_scanner_history as history
from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")
BUILD_AT = datetime(2026, 11, 4, 8, 0, tzinfo=ET)


class FakeQuotes:
    """Schwab's get_quotes shape; optionally slow, like a stalled token refresh."""

    def __init__(self, delay: float = 0.0):
        self.delay = delay
        self.calls = 0

    def get_quotes(self, symbols, **_kwargs):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        return {
            "SPY": {"change_pct": 0.4, "last_price": 600.0},
            "QQQ": {"change_pct": 0.6, "last_price": 520.0},
            "NVDA": {"change_pct": 5.2, "last_price": 190.0},
            "AAPL": {"change_pct": -3.1, "last_price": 250.0},
            "MSFT": {"change_pct": 0.2, "last_price": 500.0},
        }


@pytest.fixture
def brief(monkeypatch, tmp_path):
    """Everything the builder and the two routes touch, local and scratch."""
    archive = tmp_path / "premarket_scanner_history"
    quotes = FakeQuotes()
    pushes: list[dict] = []
    state = api_server.STATE
    monkeypatch.setattr(api_server, "_live_quote_client", lambda profile: quotes)
    monkeypatch.setattr(api_server, "news_credentials", lambda: None)            # no Alpaca news
    monkeypatch.setattr(api_server, "_push_phone_notification",
                        lambda title, body, **kw: pushes.append({"title": title, "body": body}))
    monkeypatch.setattr(api_server, "premarket_history_record_briefing", partial(history.record_briefing, directory=archive))
    monkeypatch.setattr(api_server, "premarket_history_load_briefing", partial(history.load_briefing, directory=archive))
    monkeypatch.setattr(api_server, "premarket_history_record", partial(history.record_rows, directory=archive))
    monkeypatch.setattr(api_server, "premarket_history_load", partial(history.load_history, directory=archive))
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", ["NVDA", "AAPL", "MSFT"])
    monkeypatch.setattr(state, "premarket_scanner_payload", lambda: {"rows": []}, raising=False)
    monkeypatch.setattr(state, "_morning_briefing_payload", None, raising=False)
    monkeypatch.setattr(state, "_brief_pushed_slots", set(), raising=False)
    monkeypatch.setattr(state, "_premarket_history_response", None, raising=False)
    return {"state": state, "quotes": quotes, "pushes": pushes, "archive": archive}


def test_a_built_brief_reaches_the_card_with_bold_ticker_markers_and_one_push(brief):
    brief["state"]._build_morning_briefing(BUILD_AT)
    with running_api() as api:
        status, body = api.get("/api/morning-briefing")
    assert status == 200, body
    assert body["status"] == "READY"
    assert body["generatedAt"] == BUILD_AT.isoformat()
    joined = "\n".join(body["lines"])
    assert "**NVDA** +5.2%" in joined and "**AAPL** -3.1%" in joined, body["lines"]
    assert "**MSFT**" not in joined, "a 0.2% move is not a mover"
    assert len(brief["pushes"]) == 1 and "Morning Brief" in brief["pushes"][0]["title"]
    assert "**" not in brief["pushes"][0]["body"], "the phone gets plain text, not markers"
    # The same build was archived for after a restart.
    assert history.load_briefing(BUILD_AT, directory=brief["archive"])["lines"] == body["lines"]


def test_after_a_restart_the_card_reads_todays_archive(brief):
    today_0812 = datetime.now(ET).replace(hour=8, minute=12, second=0, microsecond=0)
    lines = ["SPY +0.4% premarket - tape is firm.", "Watchlist gainers: **NVDA** +5.2%."]
    history.record_briefing(lines, today_0812, directory=brief["archive"])
    with running_api() as api:                      # in-memory payload is None: a fresh process
        status, body = api.get("/api/morning-briefing")
    assert status == 200, body
    assert body == {"status": "READY", "archived": True, "lines": lines, "generatedAt": today_0812.isoformat()}


def test_nothing_built_and_an_empty_archive_answers_waiting_never_an_error(brief):
    with running_api() as api:
        status, body = api.get("/api/morning-briefing")
    assert status == 200, body
    assert body["status"] == "WAITING" and body["lines"] == []
    assert "5:30-9:35 AM ET" in body["message"]


def test_the_route_never_waits_on_a_build_in_progress(brief):
    """A phone tap must not wait on the ~4 Schwab calls of a build."""
    brief["state"]._build_morning_briefing(BUILD_AT)               # a finished brief is on screen
    brief["quotes"].delay = 3.0
    builder = threading.Thread(target=brief["state"]._build_morning_briefing,
                               args=(BUILD_AT.replace(minute=5),), daemon=True)
    with running_api() as api:
        builder.start()
        time.sleep(0.2)                                            # the build is now stuck on Schwab
        started = time.monotonic()
        status, body = api.get("/api/morning-briefing")
        elapsed = time.monotonic() - started
        builder.join(timeout=10)
    assert status == 200 and body["status"] == "READY"
    assert elapsed < 1.0, f"the route waited {elapsed:.2f}s on a build"


def test_history_shows_each_days_brief_beside_its_rows_and_is_cached(brief):
    day_one = datetime(2026, 11, 3, 8, 30, tzinfo=ET)
    day_two = datetime(2026, 11, 4, 8, 30, tzinfo=ET)
    history.record_rows([{"symbol": "NVDA", "strength": "STRONG", "score": 88}], day_one, directory=brief["archive"])
    history.record_briefing(["Day one: **NVDA** +4.0%."], day_one, directory=brief["archive"])
    history.record_briefing(["Quiet day: **AAPL** -2.5%."], day_two, directory=brief["archive"])   # a brief, no rows
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner/history")
        assert status == 200, body
        by_date = {day["date"]: day for day in body["days"]}
        assert [day["date"] for day in body["days"]] == ["2026-11-04", "2026-11-03"], "newest first"
        assert by_date["2026-11-03"]["briefing"]["lines"] == ["Day one: **NVDA** +4.0%."]
        assert [row["symbol"] for row in by_date["2026-11-03"]["rows"]] == ["NVDA"]
        assert by_date["2026-11-04"]["rows"] == [] and by_date["2026-11-04"]["briefing"]["lines"]
        assert body["retentionDays"] == 30

        # A new day lands on disk, but the next call inside 60 s is the cached answer.
        history.record_briefing(["Later: **MSFT** +3.0%."], datetime(2026, 11, 5, 8, 0, tzinfo=ET), directory=brief["archive"])
        status, again = api.get("/api/premarket-scanner/history")
    assert status == 200 and again == body
