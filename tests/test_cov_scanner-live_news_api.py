"""Integration: the MomX news layer through api_server's real routes.

  * GET /api/news-feed/latest - the stored headlines for the board's tickers,
    read from the database only (it must never start a scrape: every MomX
    screen polls it every 15 s).
  * POST /api/news-feed/refresh - the NEWS button's background scrape: answers
    at once, one scrape at a time, at most 60 tickers.
  * GET /api/ai/catalyst - the news popover's "Why is it moving?". The model
    call runs off the request thread: the first answer is a placeholder, the
    next poll gets the cached answer, and one question costs one AI call.

The news sources, Jev and the AI provider are fakes. The database is a scratch
file (tests/conftest.py).
"""
from __future__ import annotations

import tempfile
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from api_harness import api_server, running_api
from news_feed_store import MomxNewsStore

NOW = datetime.now(timezone.utc).replace(microsecond=0)


def _item(symbol: str, headline: str, hours_ago: float) -> dict:
    return {
        "symbol": symbol, "headline": headline, "source": "Reuters",
        "url": f"https://example.com/{symbol}/{abs(hash(headline))}",
        "published_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        "score": 3, "sentiment": "Strong", "tags": "Positive Catalyst", "via": "Yahoo Finance",
        "summary": "teaser", "related_symbols": f"{symbol}", "article_id": f"a-{symbol}-{hours_ago}",
    }


class FakeEngine:
    """The seven news scrapers, as one fake: canned rows, a call log, and a
    gate so a test can hold a scrape 'running'."""

    lookback_days = 7
    per_symbol_limit = 25

    def __init__(self, rows=(), gate: threading.Event | None = None):
        self.rows = list(rows)
        self.gate = gate
        self.calls: list[list[str]] = []

    def load_watchlist_news(self, symbols, limit: int = 40):
        self.calls.append(list(symbols))
        if self.gate is not None:
            self.gate.wait(10)
        return [row for row in self.rows if row["symbol"] in set(symbols)]

    def source_status(self):
        return [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "status": "ok", "items": len(self.rows), "error": ""}]

    def available_sources(self):
        return [{"name": "yahoo_rss", "label": "Yahoo Finance RSS", "enabled": True, "reason": ""}]


class FakeJev:
    """TypeSafe Jev's headline reader: tags each stored row, never a network call."""

    def __init__(self):
        self.seen = 0

    def annotate(self, payload):
        for row in payload.get("feed") or []:
            self.seen += 1
            row["jev"] = {"label": "bullish", "tone": "up", "text": "Bullish"}

    def status(self):
        return {"provider": "fake-jev", "enabled": True, "queued": 0}


@pytest.fixture
def news(monkeypatch):
    from database.repository import TradingRepository

    with tempfile.TemporaryDirectory() as tmp:
        repository = TradingRepository(Path(tmp) / "news.db")
        engine = FakeEngine()
        store = MomxNewsStore(repository, engine, api_server.settings.news)
        jev = FakeJev()
        monkeypatch.setattr(api_server.STATE, "momx_news_store", store, raising=False)
        monkeypatch.setattr(api_server.STATE, "momx_jev_judge", jev, raising=False)
        monkeypatch.setattr(api_server.STATE, "_momx_news_cache", {}, raising=False)
        yield type("News", (), {"repository": repository, "engine": engine, "store": store, "jev": jev})
        store.wait(10)


def test_latest_returns_only_stored_rows_for_the_asked_tickers_newest_first_and_never_scrapes(news):
    news.repository.log_catalysts([
        _item("AAA", "AAA older story", 30),
        _item("AAA", "AAA newest story", 2),
        _item("BBB", "BBB story", 5),
        _item("CCC", "CCC story not asked for", 1),
    ])
    with running_api() as api:
        status, body = api.get("/api/news-feed/latest?symbols=aaa,BBB,AAA,ZZZ")
    assert status == 200, body
    assert body["symbols"] == ["AAA", "BBB", "ZZZ"]
    assert sorted(body["latest"]) == ["AAA", "BBB"]
    assert body["latest"]["AAA"]["headline"] == "AAA newest story"
    assert [row["headline"] for row in body["feed"] if row["symbol"] == "AAA"] == ["AAA newest story", "AAA older story"]
    assert all(row["symbol"] != "CCC" for row in body["feed"])
    assert news.engine.calls == []                               # DB only
    assert body["refreshing"] is False
    assert body["feed"][0]["jev"]["label"] == "bullish"          # Jev's tag rides along
    assert body["newsAi"]["provider"] == "fake-jev"


def test_refresh_answers_at_once_runs_one_scrape_at_a_time_and_caps_the_tickers(news):
    gate = threading.Event()
    news.engine.gate = gate
    news.engine.rows = [_item("S001", "S001 beats", 1)]
    asked = [f"S{i:03d}" for i in range(70)]
    try:
        with running_api() as api:
            started_at = time.monotonic()
            status, first = api.post("/api/news-feed/refresh", {"symbols": [s.lower() for s in asked]})
            took = time.monotonic() - started_at
            again_status, again = api.post("/api/news-feed/refresh", {"symbols": ["AAA"]})
            _, during = api.get("/api/news-feed/latest?symbols=S001")
            gate.set()
            assert news.store.wait(10)
            _, after = api.get("/api/news-feed/latest?symbols=S001,S002")
    finally:
        gate.set()
    assert status == 200, first
    assert took < 5                                               # the scrape never holds the request
    assert first["started"] is True and first["symbols"] == asked[:60]
    assert again_status == 200 and again["started"] is False and again["reason"] == "already running"
    assert during["refreshing"] is True
    assert news.engine.calls == [asked[:60]]                     # ONE scrape, capped at 60
    assert after["refreshing"] is False
    assert after["latest"]["S001"]["headline"] == "S001 beats"
    assert after["newsFeedMeta"]["symbolsScanned"] == 60


def test_refresh_with_no_tickers_starts_nothing(news):
    with running_api() as api:
        status, body = api.post("/api/news-feed/refresh", {"symbols": []})
    assert status == 200 and body["started"] is False
    assert news.engine.calls == []


# --------------------------------------------------------------------------- AI "why"

class FakeAI:
    """The Claude/Gemini provider, as one fake whose call can be held open."""

    name = "fake-claude"

    def __init__(self):
        self.calls = 0
        self.release = threading.Event()

    def available(self):
        return True

    def complete(self, system, user, max_tokens, schema):
        self.calls += 1
        self.release.wait(10)
        return {"ok": True, "provider": "fake-claude", "data": {
            "summary": "Apple beat on iPhone sales.", "category": "EARNINGS", "confidence": "high",
            "direction": "bullish", "headline": "Apple beats on iPhone sales"}}


@pytest.fixture
def ai(monkeypatch):
    from agents import ai_endpoints, catalyst_explainer

    fake = FakeAI()
    ai_endpoints.invalidate("catalyst:")
    monkeypatch.setattr(ai_endpoints, "_provider_configured", lambda: True)
    monkeypatch.setattr(catalyst_explainer, "_resolve_provider", lambda provider: fake)
    monkeypatch.setattr(catalyst_explainer, "cached_headlines",
                        lambda symbol, cache: [{"title": "Apple beats on iPhone sales", "url": "https://example.com/a"}])
    yield fake
    fake.release.set()
    ai_endpoints.invalidate("catalyst:")


def _wait_cached(key: str, seconds: float = 10.0) -> None:
    from agents import ai_endpoints

    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if key in ai_endpoints._CACHE:
            return
        time.sleep(0.05)
    raise AssertionError(f"{key} never cached")


def test_the_first_ask_is_a_placeholder_then_the_cached_answer_for_one_ai_call(ai):
    with running_api() as api:
        status, first = api.get("/api/ai/catalyst?symbol=aapl")
        ai.release.set()
        _wait_cached("catalyst:AAPL")
        status2, second = api.get("/api/ai/catalyst?symbol=AAPL")
        _, third = api.get("/api/ai/catalyst?symbol=AAPL")
    assert status == 200, first
    assert first.get("summary") is None and first["pending"] is True
    assert status2 == 200, second
    assert second["summary"] == "Apple beat on iPhone sales."
    assert (second["category"], second["confidence"], second["direction"]) == ("EARNINGS", "high", "bullish")
    assert second["headline"]["title"] == "Apple beats on iPhone sales"
    assert second["headlinesConsidered"] == 1
    assert third["summary"] == second["summary"]
    assert ai.calls == 1                                         # one question, one paid call


@pytest.mark.xfail(strict=True, reason=(
    "BUG: the news popover keeps waiting only while summary is null AND available is not false "
    "(MomxScannerPanel.jsx askWhy). The server's 'writing it now' placeholder is built by "
    "agents/ai_endpoints._unavailable and says available:false, so the popover stops at once and "
    "prints 'No answer.' under 'The AI is writing this now...' - and never asks again."))
def test_the_placeholder_reads_as_still_working_to_the_news_popover(ai):
    with running_api() as api:
        status, first = api.get("/api/ai/catalyst?symbol=NVDA")
    assert status == 200
    panel_keeps_waiting = first.get("summary") is None and first.get("available") is not False
    assert panel_keeps_waiting, first


def test_no_symbol_is_answered_plainly_without_an_ai_call(ai):
    with running_api() as api:
        status, body = api.get("/api/ai/catalyst?symbol=")
    assert status == 200
    assert body["available"] is False and "Name a symbol" in body["reason"]
    assert ai.calls == 0
