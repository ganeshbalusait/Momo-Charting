"""Tests for momx.news - the best-effort headline fetch behind the news icon.

NOTHING HERE TOUCHES THE NETWORK. Every test injects a stub transport through
``fresh_news(get=...)`` and stubs the credential chain, so a forgotten seam
would still only ever see the fake credential's headers.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from momx import feed, news


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


def _stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _article(headline, symbols, hours_ago, **extra):
    payload = {
        "headline": headline,
        "symbols": list(symbols),
        "created_at": _stamp(hours_ago),
        "source": "benzinga",
        "url": f"https://example.com/{headline.replace(' ', '-')}",
    }
    payload.update(extra)
    return payload


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload if payload is not None else {"news": []}

    def json(self):
        return self._payload


class StubGet:
    """A transport that records every call and serves a canned article list."""

    def __init__(self, articles=None, status_code=200):
        self.articles = list(articles or [])
        self.status_code = status_code
        self.calls: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        self.calls.append(
            {"url": url, "params": dict(params or {}), "headers": dict(headers or {}),
             "timeout": timeout}
        )
        return FakeResponse(self.status_code, {"news": list(self.articles)})


class ExplodingGet:
    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise ConnectionError("alpaca news unreachable")


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    """Fresh caches, network switch ON, and a fake credential chain."""
    monkeypatch.setenv(news.NETWORK_ENV, "1")
    monkeypatch.setattr(
        news, "_credentials", lambda: [feed.FeedCredential("test", "key", "secret")]
    )
    news.reset_caches()
    yield
    news.reset_caches()


# ----------------------------------------------------------------------
# selection: newest wins, window filters, multi-symbol credit
# ----------------------------------------------------------------------

def test_newest_article_wins_per_symbol():
    get = StubGet(
        [
            _article("older NVDA story", ["NVDA"], hours_ago=10.0),
            _article("newest NVDA story", ["NVDA"], hours_ago=2.0),
            _article("middle NVDA story", ["NVDA"], hours_ago=5.0),
        ]
    )
    result = news.fresh_news(["NVDA"], now=NOW, get=get)

    assert set(result) == {"NVDA"}
    assert result["NVDA"]["headline"] == "newest NVDA story"
    assert result["NVDA"]["at"] == (NOW - timedelta(hours=2)).isoformat()
    assert result["NVDA"]["source"] == "benzinga"
    assert result["NVDA"]["url"].startswith("https://example.com/")


def test_articles_older_than_the_window_are_excluded():
    get = StubGet(
        [
            _article("stale AAPL story", ["AAPL"], hours_ago=25.0),
            _article("fresh MSFT story", ["MSFT"], hours_ago=23.0),
        ]
    )
    result = news.fresh_news(["AAPL", "MSFT"], now=NOW, get=get)

    assert "AAPL" not in result
    assert result["MSFT"]["headline"] == "fresh MSFT story"


def test_the_window_is_tunable():
    get = StubGet([_article("two days old", ["AAPL"], hours_ago=30.0)])
    assert news.fresh_news(["AAPL"], now=NOW, get=get) == {}
    news.reset_caches()
    result = news.fresh_news(["AAPL"], window_hours=48, now=NOW, get=get)
    assert result["AAPL"]["headline"] == "two days old"


def test_a_multi_symbol_article_credits_each_requested_symbol():
    get = StubGet(
        [_article("chips rally", ["NVDA", "AMD", "TSM"], hours_ago=1.0)]
    )
    result = news.fresh_news(["NVDA", "AMD", "AAPL"], now=NOW, get=get)

    # credited to both requested names it lists, not to the unrequested TSM
    # and not to the requested-but-unnamed AAPL
    assert set(result) == {"NVDA", "AMD"}
    assert result["NVDA"]["headline"] == "chips rally"
    assert result["AMD"]["headline"] == "chips rally"


def test_a_symbol_with_no_fresh_article_is_simply_absent():
    get = StubGet([_article("only NVDA", ["NVDA"], hours_ago=1.0)])
    result = news.fresh_news(["NVDA", "MSFT"], now=NOW, get=get)
    assert set(result) == {"NVDA"}


# ----------------------------------------------------------------------
# the request itself
# ----------------------------------------------------------------------

def test_the_request_carries_the_feed_credential_headers_and_a_short_timeout():
    get = StubGet([])
    news.fresh_news(["NVDA", "aapl"], now=NOW, get=get)

    (call,) = get.calls
    assert call["url"] == news.NEWS_URL
    assert call["headers"] == {"APCA-API-KEY-ID": "key", "APCA-API-SECRET-KEY": "secret"}
    assert call["timeout"] == news.HTTP_TIMEOUT_SECONDS
    assert call["params"]["symbols"] == "NVDA,AAPL"


def test_symbols_are_chunked_at_the_cap():
    get = StubGet([])
    many = [f"S{index:03d}" for index in range(news.MAX_SYMBOLS_PER_REQUEST + 5)]
    news.fresh_news(many, now=NOW, get=get)

    assert len(get.calls) == 2
    first = get.calls[0]["params"]["symbols"].split(",")
    second = get.calls[1]["params"]["symbols"].split(",")
    assert len(first) == news.MAX_SYMBOLS_PER_REQUEST
    assert first + second == many


class PagedGet:
    """A transport that hands out ``pages`` in order, chaining next_page_token."""

    def __init__(self, pages):
        self.pages = [list(page) for page in pages]
        self.calls: list[dict] = []

    def __call__(self, url, params=None, headers=None, timeout=None):
        params = dict(params or {})
        self.calls.append(params)
        index = int(params.get("page_token") or 0)
        payload = {"news": self.pages[index]}
        if index + 1 < len(self.pages):
            payload["next_page_token"] = str(index + 1)
        return FakeResponse(200, payload)


def test_pages_are_followed_so_a_busy_chunk_does_not_lose_its_older_symbols():
    """357 symbols in 4 chunks: one 50-article page per chunk missed every
    symbol whose newest story sat below the cut (2026-09-02)."""
    get = PagedGet([
        [_article("Newest AAPL", ["AAPL"], 1.0)],
        [_article("Older MSFT", ["MSFT"], 5.0)],
        [_article("Oldest NVDA", ["NVDA"], 9.0)],
    ])
    out = news.fresh_news(["AAPL", "MSFT", "NVDA"], now=NOW, get=get)

    assert set(out) == {"AAPL", "MSFT", "NVDA"}
    assert out["NVDA"]["headline"] == "Oldest NVDA"
    assert [call.get("page_token") for call in get.calls] == [None, "1", "2"]
    # every page still carries the same symbol set and window start
    assert {call["symbols"] for call in get.calls} == {"AAPL,MSFT,NVDA"}


def test_page_following_stops_at_the_cap(monkeypatch):
    monkeypatch.setattr(news, "MAX_PAGES_PER_CHUNK", 2)
    get = PagedGet([[], [], [_article("Never reached", ["AAPL"], 1.0)]])
    assert news.fresh_news(["AAPL"], now=NOW, get=get) == {}
    assert len(get.calls) == 2


# ----------------------------------------------------------------------
# failure: {} out, remembered for 60s, never raises
# ----------------------------------------------------------------------

def test_a_transport_failure_returns_an_empty_dict():
    assert news.fresh_news(["NVDA"], now=NOW, get=ExplodingGet()) == {}


def test_a_non_200_returns_an_empty_dict():
    get = StubGet([], status_code=500)
    assert news.fresh_news(["NVDA"], now=NOW, get=get) == {}


def test_no_credentials_returns_an_empty_dict(monkeypatch):
    monkeypatch.setattr(news, "_credentials", lambda: [])
    get = StubGet([])
    assert news.fresh_news(["NVDA"], now=NOW, get=get) == {}
    assert get.calls == []


def test_a_cached_failure_suppresses_the_immediate_retry(monkeypatch):
    clock = {"value": 1000.0}
    monkeypatch.setattr(news, "_monotonic", lambda: clock["value"])

    exploding = ExplodingGet()
    assert news.fresh_news(["NVDA"], now=NOW, get=exploding) == {}
    assert exploding.calls == 1

    # 30s later (a build cadence tick): the failure is remembered, no HTTP.
    clock["value"] += 30.0
    counting = StubGet([_article("fresh", ["NVDA"], hours_ago=1.0)])
    assert news.fresh_news(["NVDA"], now=NOW, get=counting) == {}
    assert counting.calls == []

    # past the 60s failure TTL: the endpoint is probed again and recovers.
    clock["value"] += news.FAILURE_TTL_SECONDS
    result = news.fresh_news(["NVDA"], now=NOW, get=counting)
    assert len(counting.calls) == 1
    assert result["NVDA"]["headline"] == "fresh"


# ----------------------------------------------------------------------
# the TTL cache
# ----------------------------------------------------------------------

def test_the_ttl_cache_returns_the_same_object_without_a_second_http_call():
    get = StubGet([_article("cached story", ["NVDA"], hours_ago=1.0)])
    first = news.fresh_news(["NVDA"], now=NOW, get=get)
    assert len(get.calls) == 1

    exploding = ExplodingGet()
    second = news.fresh_news(["NVDA"], now=NOW, get=exploding)
    assert second is first
    assert exploding.calls == 0


def test_the_cache_key_is_the_sorted_symbol_tuple():
    get = StubGet([_article("story", ["NVDA", "AAPL"], hours_ago=1.0)])
    first = news.fresh_news(["NVDA", "AAPL"], now=NOW, get=get)
    # same set, different order and case: still a hit, still one HTTP call
    second = news.fresh_news(["aapl", "NVDA"], now=NOW, get=ExplodingGet())
    assert second is first
    assert len(get.calls) == 1


def test_the_cache_expires_after_its_ttl(monkeypatch):
    clock = {"value": 1000.0}
    monkeypatch.setattr(news, "_monotonic", lambda: clock["value"])

    get = StubGet([_article("story", ["NVDA"], hours_ago=1.0)])
    news.fresh_news(["NVDA"], now=NOW, get=get)
    assert len(get.calls) == 1

    clock["value"] += news.CACHE_TTL_SECONDS + 1.0
    news.fresh_news(["NVDA"], now=NOW, get=get)
    assert len(get.calls) == 2


# ----------------------------------------------------------------------
# the kill switch
# ----------------------------------------------------------------------

def test_the_network_env_switch_disables_the_fetch(monkeypatch):
    monkeypatch.setenv(news.NETWORK_ENV, "0")
    get = StubGet([_article("story", ["NVDA"], hours_ago=1.0)])
    assert news.fresh_news(["NVDA"], now=NOW, get=get) == {}
    assert get.calls == []


# ---------------------------------------------------------------------------
# Scope: a market-wide round-up must not masquerade as this ticker's news.
# Measured on the live board 2026-09-04: 120 of 358 rows drew from just 79
# distinct articles.
# ---------------------------------------------------------------------------
def _scope_article(headline, symbols, minutes_ago, *, source="benzinga"):
    from datetime import datetime, timedelta, timezone
    at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    return {
        "headline": headline,
        "created_at": at.isoformat(),
        "symbols": symbols,
        "source": source,
        "url": "https://example.test/x",
    }


def _scope_credit(articles, requested):
    from datetime import datetime, timedelta, timezone
    from momx import news
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    return news._newest_per_symbol(articles, set(requested), cutoff)


def test_a_story_naming_a_few_tickers_is_specific():
    out = _scope_credit([_scope_article("Nebius signs AI deal", ["NBIS", "NVDA"], 30)], ["NBIS"])
    assert out["NBIS"]["scope"] == "specific"
    assert out["NBIS"]["namedCount"] == 2


def test_a_market_round_up_is_marked_market_wide_not_hidden():
    """DEMOTE, NEVER HIDE - an empty cell reads as 'nothing happened'."""
    listicle = _scope_article(
        "10 Information Technology Stocks Whale Activity In Today's Session",
        ["AAPL", "MSFT", "NVDA", "AMD", "MRVL", "INTC", "AVGO", "KLAC", "AMAT", "ASML"],
        10,
    )
    out = _scope_credit([listicle], ["MRVL"])
    assert out["MRVL"]["scope"] == "market-wide"
    assert out["MRVL"]["namedCount"] == 10
    assert out["MRVL"]["headline"].startswith("10 Information Technology")


def test_a_specific_story_beats_a_newer_market_wide_one():
    """The whole point: recency must not let a round-up bury real news."""
    out = _scope_credit([
        _scope_article("Nebius wins $1B contract", ["NBIS"], 300),
        _scope_article("Stock Market Today: SanDisk jumps, Lululemon crashes",
                 ["NBIS", "SNDK", "LULU", "AAPL", "MSFT"], 5),
    ], ["NBIS"])
    assert out["NBIS"]["headline"] == "Nebius wins $1B contract"
    assert out["NBIS"]["scope"] == "specific"


def test_among_equals_the_newest_wins():
    out = _scope_credit([
        _scope_article("Old specific", ["NBIS"], 300),
        _scope_article("New specific", ["NBIS"], 5),
    ], ["NBIS"])
    assert out["NBIS"]["headline"] == "New specific"


def test_market_wide_still_fills_a_symbol_with_nothing_specific():
    out = _scope_credit([
        _scope_article("Sector round-up", ["A", "B", "C", "D", "E", "F"], 20),
    ], ["C"])
    assert out["C"]["scope"] == "market-wide"


def test_a_symbol_not_asked_about_is_never_credited():
    out = _scope_credit([_scope_article("Nebius news", ["NBIS"], 5)], ["AAPL"])
    assert out == {}


# ---------------------------------------------------------------------------
# RSS backfill: cover the symbols Benzinga does not, without becoming the next
# rate incident. Benzinga answered 123 of 358 rows on 2026-09-04; NBIS's newest
# Benzinga headline was ~53h old while Yahoo had one 6.1h old.
# ---------------------------------------------------------------------------
def _rss_body(items):
    """Minimal RSS 2.0, RFC-822 dates - the shape Yahoo actually returns."""
    parts = ["<?xml version='1.0'?><rss version='2.0'><channel>"]
    for title, pub in items:
        parts.append(
            "<item><title>%s</title><pubDate>%s</pubDate>"
            "<link>https://example.test/a</link></item>" % (title, pub)
        )
    parts.append("</channel></rss>")
    return "".join(parts)


def _rfc822(minutes_ago):
    import email.utils
    from datetime import datetime, timedelta, timezone
    return email.utils.format_datetime(
        datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    )


class _RssResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _rss_get(bodies, calls):
    """Injected getter: records every URL so the budget can be asserted."""
    def get(url, **kwargs):
        calls.append(url)
        for symbol, body in bodies.items():
            if "s=%s&" % symbol in url or url.endswith("s=%s" % symbol):
                return _RssResponse(body)
        return _RssResponse("", status_code=404)
    return get


def test_rss_backfills_a_symbol_benzinga_missed():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"NBIS": _rss_body([("Nebius wins AI infrastructure deal", _rfc822(366))])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["NBIS"], cutoff, _rss_get(bodies, calls))
    assert out["NBIS"]["headline"] == "Nebius wins AI infrastructure deal"
    assert out["NBIS"]["source"] == "Yahoo Finance"
    assert out["NBIS"]["scope"] == "specific"
    assert len(calls) == 1


def test_rss_never_exceeds_its_per_build_budget():
    """The whole safety argument: RSS is one request per ticker, and this app
    has been IP-banned twice this week for request volume."""
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    missing = ["SYM%03d" % i for i in range(200)]
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    news._rss_backfill(missing, cutoff, _rss_get({}, calls))
    assert len(calls) == news.RSS_BACKFILL_PER_BUILD, len(calls)


def test_a_miss_is_cached_so_it_does_not_burn_a_slot_every_build():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    get = _rss_get({}, calls)
    news._rss_backfill(["AAAA"], cutoff, get)
    news._rss_backfill(["AAAA"], cutoff, get)
    assert len(calls) == 1, "a symbol with no news must not be refetched next build"


def test_an_rss_listicle_is_demoted_not_dropped():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"XYZ": _rss_body([
        ("3 Founder Backed Growth Stocks To Watch In September 2026", _rfc822(30)),
    ])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["XYZ"], cutoff, _rss_get(bodies, calls))
    assert out["XYZ"]["scope"] == "market-wide"
    assert out["XYZ"]["headline"].startswith("3 Founder Backed")


def test_rss_items_older_than_the_window_are_ignored():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"OLD": _rss_body([("Ancient story", _rfc822(60 * 48))])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["OLD"], cutoff, _rss_get(bodies, calls))
    assert out == {}


def test_rss_picks_the_newest_item_not_the_first():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    bodies = {"NEW": _rss_body([
        ("Older story", _rfc822(600)),
        ("Newest story", _rfc822(20)),
        ("Middle story", _rfc822(300)),
    ])}
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    out = news._rss_backfill(["NEW"], cutoff, _rss_get(bodies, calls))
    assert out["NEW"]["headline"] == "Newest story"


def test_a_broken_feed_can_never_break_the_board():
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()

    def exploding_get(url, **kwargs):
        raise RuntimeError("feed down")

    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    assert news._rss_backfill(["ANY"], cutoff, exploding_get) == {}


def test_rss_can_be_switched_off():
    import os
    from datetime import datetime, timedelta, timezone
    from momx import news
    news.reset_caches()
    calls = []
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
    os.environ[news.RSS_ENV] = "0"
    try:
        out = news._rss_backfill(["NBIS"], cutoff, _rss_get({}, calls))
    finally:
        os.environ.pop(news.RSS_ENV, None)
    assert out == {}
    assert calls == []
