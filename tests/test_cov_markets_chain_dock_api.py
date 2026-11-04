"""Integration: what the Charts & OI chain column's News and Sector dock
panels read, through the real routes over HTTP (2026-11-04 coverage).

On a ticker switch the chart reads GET /api/news-feed (stored headlines, at
once) and posts /api/news-feed/refresh (one background scrape). Neither may
make the chart wait on the news sites, and rapid ticker switches must not
stack scrapes. The Sector dock reads the same /api/watchlist-quotes as the
Sectors page (covered in test_cov_markets_sectors_api.py); here only its
dock-specific promise is checked: a ticker it does not know still answers.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api


def _row(symbol: str, headline: str, minutes_ago: float) -> dict:
    published = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "symbol": symbol, "headline": headline, "source": "Benzinga",
        "url": f"https://example.invalid/dock/{abs(hash(headline))}", "published_at": published.isoformat(),
        "score": 2, "sentiment": "Positive", "tags": "Earnings", "via": "Alpaca", "summary": "",
        "related_symbols": symbol, "article_id": "",
    }


class SlowEngine:
    """The news sites: every scrape takes `delay` seconds; calls are recorded."""

    lookback_days = 7
    per_symbol_limit = 25

    def __init__(self, rows, delay: float):
        self.rows, self.delay = rows, delay
        self.calls: list[list[str]] = []
        self.release = threading.Event()

    def load_watchlist_news(self, symbols, limit: int = 40):
        self.calls.append(list(symbols))
        self.release.wait(self.delay)
        return [row for row in self.rows if row["symbol"] in set(symbols)]

    def source_status(self):
        return [{"name": "alpaca", "label": "Alpaca news", "status": "ok", "items": len(self.rows), "error": ""}]

    def available_sources(self):
        return [{"name": "alpaca", "label": "Alpaca news", "enabled": True, "reason": ""}]


class StubJudge:
    def annotate(self, payload):
        return payload

    def status(self):
        return {"provider": "stub"}


@pytest.fixture
def dock(monkeypatch):
    state = api_server.STATE
    tag = time.time_ns()
    stored = [_row("NVDA", f"Nvidia beats on data-center revenue {tag}", 30)]
    engine = SlowEngine([_row("NVDA", f"Nvidia fresh scrape headline {tag}", 1)], delay=2.0)
    state.repository.log_catalysts(stored)                   # already in the scratch DB before the switch
    monkeypatch.setattr(state, "catalysts", engine)
    monkeypatch.setattr(state, "momx_jev_judge", StubJudge(), raising=False)
    monkeypatch.setattr(state, "momx_news_store", None, raising=False)
    monkeypatch.setattr(state, "_momx_news_cache", {}, raising=False)
    monkeypatch.setattr(state, "_news_read_cache", {}, raising=False)
    monkeypatch.setattr(state, "_news_feed_last_refresh", None, raising=False)
    monkeypatch.setattr(state, "_news_feed_last_refresh_at", 0.0, raising=False)
    monkeypatch.setattr(state, "_news_feed_bg_running", False, raising=False)
    yield SimpleNamespace(state=state, engine=engine, stored=stored[0]["headline"])
    engine.release.set()                                     # let any scrape still running finish now
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and (
        getattr(state, "_news_feed_bg_running", False)
        or (state.__dict__.get("momx_news_store") and state.momx_news_store.state.get("running"))
    ):
        time.sleep(0.05)


def test_the_dock_reads_stored_headlines_at_once_while_a_scrape_runs_behind(dock):
    with running_api() as api:
        started = time.monotonic()
        status, body = api.get("/api/news-feed")
        elapsed = time.monotonic() - started
    assert status == 200, body
    assert elapsed < 1.0, f"the dock waited {elapsed:.2f}s on the news sites"
    assert any(row["symbol"] == "NVDA" and row["headline"] == dock.stored for row in body["catalysts"])
    assert any(row["symbol"] == "NVDA" and row["headline"] == dock.stored for row in body["catalystIndex"])
    deadline = time.monotonic() + 2.0
    while not dock.engine.calls and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(dock.engine.calls) == 1, "a stale feed starts exactly one background scrape"


def test_a_ticker_switch_refresh_returns_at_once_and_never_stacks_scrapes(dock):
    with running_api() as api:
        started = time.monotonic()
        status, first = api.post("/api/news-feed/refresh", {"symbols": ["nvda"]})
        elapsed = time.monotonic() - started
        status_two, second = api.post("/api/news-feed/refresh", {"symbols": ["AAPL"]})
    assert status == 200 and status_two == 200
    assert elapsed < 1.0, f"refresh waited {elapsed:.2f}s"
    assert first == {"started": True, "running": True, "symbols": ["NVDA"]}
    assert second["started"] is False and second["reason"] == "already running"
    assert second["symbols"] == ["NVDA"], "the second switch is told which scrape is in flight"
    time.sleep(0.2)
    assert dock.engine.calls == [["NVDA"]], "only one scrape reached the news sites"


def test_a_refresh_with_no_symbols_starts_nothing(dock):
    with running_api() as api:
        status, body = api.post("/api/news-feed/refresh", {"symbols": []})
    assert status == 200
    assert body == {"started": False, "reason": "no symbols", "running": False}
    assert dock.engine.calls == []
