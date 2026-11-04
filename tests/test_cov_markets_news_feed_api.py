"""Integration: the News Feed page's 'Refresh View' and the stored rows it
reads, through the real /api/news-feed routes over HTTP (2026-11-04 coverage).

The news sites are a fake CatalystEngine on STATE.catalysts, so nothing is
scraped; the headlines land in the scratch database (tests/conftest.py) and
come back out through the same reads the page polls. Jev's labelling is a stub
(it would otherwise load a model or call an AI).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api


def _row(symbol: str, headline: str, minutes_ago: float = 10, **extra) -> dict:
    published = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "symbol": symbol, "headline": headline, "source": "Reuters",
        "url": f"https://example.invalid/{symbol}/{abs(hash(headline))}",
        "published_at": published.isoformat(), "score": 3, "sentiment": "Strong",
        "tags": "Upgrade", "via": "Yahoo Finance", "summary": "", "related_symbols": symbol, "article_id": "",
        **extra,
    }


class FakeEngine:
    """CatalystEngine's surface for scrape_symbols: canned rows, recorded calls."""

    lookback_days = 7
    per_symbol_limit = 25

    def __init__(self, rows, statuses=None, delay: float = 0.0):
        self.rows = rows
        self.delay = delay
        self.calls: list[list[str]] = []
        self.statuses = statuses or [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": len(rows), "error": ""}]

    def load_watchlist_news(self, symbols, limit: int = 40):
        self.calls.append(list(symbols))
        if self.delay:
            time.sleep(self.delay)
        wanted = set(symbols)
        return [row for row in self.rows if row["symbol"] in wanted]

    def source_status(self):
        return self.statuses

    def available_sources(self):
        return [{"name": s["name"], "label": s["label"], "enabled": True, "reason": ""} for s in self.statuses]


class StubJudge:
    def annotate(self, payload):
        return payload

    def status(self):
        return {"provider": "stub", "enabled": False}


@pytest.fixture
def news(monkeypatch):
    state = api_server.STATE
    stamp = f"{time.time_ns()}"
    engine = FakeEngine([_row("AAPL", f"Apple raised to Buy at Example Securities {stamp}")])
    monkeypatch.setattr(state, "catalysts", engine)
    monkeypatch.setattr(state, "momx_jev_judge", StubJudge(), raising=False)
    monkeypatch.setattr(state, "_news_read_cache", {}, raising=False)
    monkeypatch.setattr(state, "_news_feed_last_refresh", None, raising=False)
    monkeypatch.setattr(state, "_news_feed_last_refresh_at", 0.0, raising=False)
    monkeypatch.setattr(state, "_news_feed_bg_running", False, raising=False)
    return SimpleNamespace(state=state, engine=engine, headline=engine.rows[0]["headline"])


def _wait_background_scrape(state, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while getattr(state, "_news_feed_bg_running", False) and time.monotonic() < deadline:
        time.sleep(0.05)


def test_refresh_view_scrapes_the_chosen_symbols_and_returns_the_stored_headline(news):
    with running_api() as api:
        status, body = api.post("/api/news-feed", {"symbols": ["aapl"]})
    assert status == 200, body
    assert news.engine.calls == [["AAPL"]], "the browser's symbols are upper-cased and scraped once"
    stored = [row for row in body["catalysts"] if row.get("headline") == news.headline]
    assert stored and stored[0]["symbol"] == "AAPL", "the scraped headline is back in the page's rows"
    assert any(row.get("headline") == news.headline for row in body["catalystIndex"]), "the per-ticker index has it too"
    assert body["actionMessage"].startswith("News scan completed for 1 symbols")
    assert body["newsFeedMeta"]["sources"][0]["status"] == "ok"
    assert body["newsFeedMeta"]["symbols"] == ["AAPL"]
    assert "message" not in body["newsFeedMeta"]


def test_the_polled_read_shows_the_refreshed_headline_without_scraping_again(news):
    with running_api() as api:
        api.post("/api/news-feed", {"symbols": ["AAPL"]})
        news.engine.delay = 2.0                       # any further scrape would be slow
        started = time.monotonic()
        status, body = api.get("/api/news-feed")
        elapsed = time.monotonic() - started
    _wait_background_scrape(news.state)
    assert status == 200, body
    assert elapsed < 1.0, f"the read waited {elapsed:.2f}s on a scrape"
    assert any(row.get("headline") == news.headline for row in body["catalysts"])


def test_a_blocked_source_is_named_in_the_message_and_never_a_500(news):
    news.engine.statuses = [
        {"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": 1, "error": ""},
        {"name": "finviz", "label": "Finviz", "status": "blocked", "items": 0, "error": "403"},
    ]
    with running_api() as api:
        status, body = api.post("/api/news-feed", {"symbols": ["AAPL"]})
    assert status == 200, body
    assert "Sources ok: 1/2" in body["actionMessage"]
    assert body["actionMessage"].endswith("Unavailable: Finviz.")


def test_no_symbols_scrapes_the_first_40_watchlist_names(news, monkeypatch):
    watchlist = [f"T{index:02d}" for index in range(45)]
    monkeypatch.setattr(api_server.settings.scanner, "default_universe", watchlist)
    with running_api() as api:
        status, body = api.post("/api/news-feed", {})
    assert status == 200, body
    assert news.engine.calls == [watchlist[:40]]
    assert body["newsFeedMeta"]["symbolsScanned"] == 40
