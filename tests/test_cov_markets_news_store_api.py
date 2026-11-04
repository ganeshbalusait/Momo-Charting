"""Integration: the ticker-tagged news store behind News Feed, the chart's
News dock and MomX NEWS, through GET /api/news-feed/latest and POST
/api/news-feed/refresh over HTTP (2026-11-04 coverage).

Three ways a headline reaches the store are exercised against the scratch
database: already stored, a refresh scrape (fake engine), and the live push
path (a real LiveNewsIngest fed a fake Alpaca websocket message - its
transports are never started).
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

import news_live
from api_harness import api_server, running_api


def _row(symbol: str, headline: str, minutes_ago: float) -> dict:
    published = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "symbol": symbol, "headline": headline, "source": "Reuters",
        "url": f"https://example.invalid/store/{symbol}/{abs(hash(headline))}", "published_at": published.isoformat(),
        "score": 2, "sentiment": "Positive", "tags": "Guidance", "via": "Yahoo Finance", "summary": "",
        "related_symbols": symbol, "article_id": "",
    }


class FakeEngine:
    lookback_days = 7
    per_symbol_limit = 25

    def __init__(self, rows):
        self.rows = rows
        self.calls: list[list[str]] = []

    def load_watchlist_news(self, symbols, limit: int = 40):
        self.calls.append(list(symbols))
        return [row for row in self.rows if row["symbol"] in set(symbols)]

    def source_status(self):
        return [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": len(self.rows), "error": ""}]

    def available_sources(self):
        return []

    @staticmethod
    def score_headline(headline, summary=""):
        return 2, "Positive", ["Wire"]


class StubJudge:
    def annotate(self, payload):
        return payload

    def status(self):
        return {"provider": "stub"}


class StubIngest:
    def status(self):
        return {"alpaca": {"status": "connected", "received": 3, "stored": 2, "lastAt": "2026-11-04T14:00:00+00:00",
                           "error": None, "medianLagSec": 4.0}}


@pytest.fixture
def store(monkeypatch):
    state = api_server.STATE
    tag = time.time_ns()
    # Two tickers no other test writes, so the scratch DB's other rows cannot interfere.
    first, second = f"ZQA{tag % 1000:03d}", f"ZQB{tag % 1000:03d}"
    state.repository.log_catalysts([
        _row(first, f"{first} older guidance {tag}", 120),
        _row(first, f"{first} newest guidance {tag}", 5),
        _row(second, f"{second} contract win {tag}", 30),
    ])
    engine = FakeEngine([_row(first, f"{first} scraped just now {tag}", 1)])
    monkeypatch.setattr(state, "catalysts", engine)
    monkeypatch.setattr(state, "momx_jev_judge", StubJudge(), raising=False)
    monkeypatch.setattr(state, "momx_news_store", None, raising=False)
    monkeypatch.setattr(state, "_momx_news_cache", {}, raising=False)
    monkeypatch.setattr(state, "_news_read_cache", {}, raising=False)
    monkeypatch.setitem(api_server._NEWS_LIVE, "ingest", None)
    yield SimpleNamespace(state=state, engine=engine, first=first, second=second, tag=tag)
    if state.__dict__.get("momx_news_store") is not None:
        state.momx_news_store.wait(10)


def test_latest_groups_stored_headlines_per_ticker_newest_first_without_scraping(store):
    with running_api() as api:
        status, body = api.get(f"/api/news-feed/latest?symbols={store.first.lower()},{store.second},UNSEEN")
    assert status == 200, body
    assert body["symbols"] == [store.first, store.second, "UNSEEN"]
    assert body["latest"][store.first]["headline"] == f"{store.first} newest guidance {store.tag}"
    assert body["latest"][store.second]["headline"] == f"{store.second} contract win {store.tag}"
    assert "UNSEEN" not in body["latest"]
    first_feed = [item["headline"] for item in body["feed"] if item["symbol"] == store.first]
    assert first_feed == [f"{store.first} newest guidance {store.tag}", f"{store.first} older guidance {store.tag}"]
    assert body["refreshing"] is False
    assert store.engine.calls == [], "the board's read never scrapes"
    assert "live" not in body, "no live ingest registered -> no live status"


def test_a_finished_refresh_shows_on_the_very_next_poll_not_15s_later(store):
    with running_api() as api:
        status, before = api.get(f"/api/news-feed/latest?symbols={store.first}")
        assert status == 200
        status, started = api.post("/api/news-feed/refresh", {"symbols": [store.first]})
        assert status == 200 and started["started"] is True
        assert store.state.momx_news_store.wait(10)
        status, after = api.get(f"/api/news-feed/latest?symbols={store.first}")
    assert status == 200
    assert store.engine.calls == [[store.first]]
    assert after["latest"][store.first]["headline"] == f"{store.first} scraped just now {store.tag}", \
        "the 15 s answer cache must expire the moment a scrape finishes"
    assert after["refreshFinishedAt"] and after["refreshing"] is False
    assert after["newsFeedMeta"]["headlinesRefreshed"] == 1


def test_a_live_alpaca_push_appears_with_no_scrape(store):
    ingest = news_live.LiveNewsIngest(store.state.repository, FakeEngine.score_headline, lambda: [])
    message = {
        "T": "n", "id": 991, "headline": f"{store.second} wins a federal contract (live) {store.tag}",
        "summary": "", "url": f"https://example.invalid/live/{store.tag}",
        "created_at": (datetime.now(timezone.utc) - timedelta(seconds=20)).isoformat(),
        "symbols": [store.second], "source": "benzinga",
    }
    stored = ingest.store("alpaca", news_live.alpaca_items(message, FakeEngine.score_headline))
    assert stored == 1
    with running_api() as api:
        status, body = api.get(f"/api/news-feed/latest?symbols={store.second}")
    assert status == 200, body
    latest = body["latest"][store.second]
    assert latest["headline"] == message["headline"] and latest["via"] == "Alpaca live"
    assert latest["source"] == "Benzinga"
    assert store.engine.calls == []


def test_live_status_rides_on_latest_only_when_an_ingest_is_registered(store, monkeypatch):
    monkeypatch.setitem(api_server._NEWS_LIVE, "ingest", StubIngest())
    with running_api() as api:
        status, body = api.get(f"/api/news-feed/latest?symbols={store.first}")
    assert status == 200, body
    assert body["live"]["alpaca"]["status"] == "connected"
    assert body["live"]["alpaca"]["medianLagSec"] == 4.0
