from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from catalyst_engine import (
    DEFAULT_SOURCES,
    CatalystEngine,
    FinvizSource,
    HttpResponse,
    canonical_url,
    normalize_headline,
    parse_source_list,
)


NOW = datetime(2026, 9, 26, 15, 0, tzinfo=timezone.utc)


def _epoch(hours_ago: float) -> int:
    return int((NOW - timedelta(hours=hours_ago)).timestamp())


def _rfc822(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%a, %d %b %Y %H:%M:%S +0000")


def _iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat().replace("+00:00", "Z")


YAHOO_SEARCH = {
    "news": [
        {
            "uuid": "aaa-1",
            "title": "Apple beats estimates as iPhone demand surges",
            "publisher": "Reuters",
            "link": "https://finance.yahoo.com/news/apple-beats-123.html?.tsrc=rss&guccounter=1",
            "providerPublishTime": _epoch(2),
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
        {
            "uuid": "aaa-2",
            "title": "Microsoft cloud growth slows",
            "publisher": "Bloomberg",
            "link": "https://finance.yahoo.com/news/msft-cloud.html",
            "providerPublishTime": _epoch(3),
            "type": "STORY",
            "relatedTickers": ["MSFT"],
        },
        {
            "uuid": "aaa-3",
            "title": "Old story about Apple from last month",
            "publisher": "Reuters",
            "link": "https://finance.yahoo.com/news/apple-old.html",
            "providerPublishTime": _epoch(24 * 30),
            "type": "STORY",
            "relatedTickers": ["AAPL"],
        },
    ]
}

YAHOO_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Yahoo! Finance: AAPL News</title>
<item><title>Apple beats estimates as iPhone demand surges</title>
<link>https://finance.yahoo.com/news/apple-beats-123.html</link>
<pubDate>{_rfc822(2)}</pubDate><guid>apple-beats-123</guid>
<description>Apple reported quarterly revenue above expectations.</description></item>
<item><title>Apple faces EU investigation over App Store</title>
<link>https://finance.yahoo.com/news/apple-eu-probe.html</link>
<pubDate>{_rfc822(5)}</pubDate><guid>apple-eu-probe</guid></item>
</channel></rss>"""

ALPACA_NEWS = {
    "news": [
        {
            "id": 991,
            "headline": "Apple Raises Price Target At Morgan Stanley",
            "author": "Benzinga Newsdesk",
            "created_at": _iso(1),
            "updated_at": _iso(1),
            "summary": "Morgan Stanley raises its price target on Apple.",
            "url": "https://www.benzinga.com/news/apple-pt",
            "symbols": ["AAPL"],
            "source": "benzinga",
        },
        {
            "id": 992,
            "headline": "Tesla story tagged elsewhere",
            "created_at": _iso(1),
            "url": "https://www.benzinga.com/news/tsla",
            "symbols": ["TSLA"],
            "source": "benzinga",
        },
    ],
    "next_page_token": None,
}

FINVIZ_HTML = """<html><body>
<table class="fullview-news-outer" id="news-table">
<tr class="cursor-pointer has-label"><td align="right" width="130">Sep-26-26 09:45AM</td>
<td align="left"><div class="news-link-container"><div class="news-link-left">
<a href="https://www.wsj.com/tech/apple-chip-deal" target="_blank" class="tab-link-news">Apple signs chip supply contract with TSMC</a>
</div><div class="news-link-right"><span>(WSJ)</span></div></div></td></tr>
<tr class="cursor-pointer has-label"><td align="right" width="130">08:10AM</td>
<td align="left"><div class="news-link-container"><div class="news-link-left">
<a class="tab-link-news" href="/news/12345/apple-note" target="_blank">Apple faces EU investigation over App Store</a>
</div><div class="news-link-right"><span>(Reuters)</span></div></div></td></tr>
<tr class="cursor-pointer has-label"><td align="right" width="130">Sep-25-26 04:30PM</td>
<td align="left"><div class="news-link-container"><div class="news-link-left">
<a href="https://www.marketwatch.com/story/apple-prev" target="_blank" class="tab-link-news">Apple stock slips ahead of event</a>
</div><div class="news-link-right"><span>(MarketWatch)</span></div></div></td></tr>
</table></body></html>"""

NASDAQ_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:nasdaq="http://nasdaq.com/rss/" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
<item><title>3 Reasons Apple Is a Buy Right Now</title>
<link>https://www.nasdaq.com/articles/apple-buy</link>
<pubDate>{_rfc822(6)}</pubDate><guid>nasdaq-apple-buy</guid>
<dc:creator>The Motley Fool</dc:creator>
<nasdaq:tickers>AAPL,MSFT</nasdaq:tickers>
<description>Apple keeps growing services.</description></item>
<item><title>Nvidia article that mentions Apple in body</title>
<link>https://www.nasdaq.com/articles/nvda-thing</link>
<pubDate>{_rfc822(6)}</pubDate>
<nasdaq:tickers>NVDA</nasdaq:tickers></item>
</channel></rss>"""


class FakeHttp:
    """Routes engine requests to fixtures by host and records every call."""

    def __init__(self, blocked: set[str] | None = None, status_override: dict[str, int] | None = None) -> None:
        self.calls: list[str] = []
        self.blocked = blocked or set()
        self.status_override = status_override or {}

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append(url)
        host = urlsplit(url).netloc
        if host in self.blocked:
            return HttpResponse(status=403, body=b"blocked")
        if host in self.status_override:
            return HttpResponse(status=self.status_override[host], body=b"")
        if host == "query1.finance.yahoo.com" and "/v1/finance/search" in url:
            return HttpResponse(status=200, body=json.dumps(YAHOO_SEARCH).encode())
        if host == "feeds.finance.yahoo.com":
            return HttpResponse(status=200, body=YAHOO_RSS.encode())
        if host == "data.alpaca.markets":
            assert headers.get("APCA-API-KEY-ID") == "key", headers
            assert headers.get("APCA-API-SECRET-KEY") == "secret", headers
            return HttpResponse(status=200, body=json.dumps(ALPACA_NEWS).encode())
        if host == "finviz.com":
            return HttpResponse(status=200, body=FINVIZ_HTML.encode())
        if host == "www.nasdaq.com":
            return HttpResponse(status=200, body=NASDAQ_RSS.encode())
        return HttpResponse(status=404, body=b"")


def _engine(http: FakeHttp, **overrides) -> CatalystEngine:
    options = dict(alpaca_credentials=("key", "secret"), http_get=http, cache_ttl_seconds=0, per_symbol_limit=25)
    options.update(overrides)
    return CatalystEngine(**options)


class ParserTests(unittest.TestCase):
    def test_default_sources_are_ticker_tagged_and_google_is_opt_in(self) -> None:
        self.assertEqual(parse_source_list(None), DEFAULT_SOURCES)
        self.assertNotIn("google_news", DEFAULT_SOURCES)
        self.assertEqual(parse_source_list("finviz, google_news ,bogus"), ("finviz", "google_news"))
        self.assertEqual(parse_source_list("bogus"), DEFAULT_SOURCES)

    def test_canonical_url_strips_tracking_and_normalizes_host(self) -> None:
        self.assertEqual(
            canonical_url("https://www.finance.yahoo.com/news/abc-123.html?utm_source=x&guccounter=1&.tsrc=rss#frag"),
            "https://finance.yahoo.com/news/abc-123.html",
        )
        self.assertEqual(normalize_headline("Apple &amp; Co. beats!  Estimates"), "apple co beats estimates")

    def test_yahoo_search_keeps_only_articles_tagged_with_the_symbol(self) -> None:
        engine = _engine(FakeHttp(), sources=("yahoo_search",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Apple beats estimates as iPhone demand surges"])
        item = items[0]
        self.assertEqual(item.source, "Reuters")
        self.assertEqual(item.via, "Yahoo Finance")
        self.assertEqual(item.related_symbols, "AAPL")
        self.assertEqual(item.article_id, "aaa-1")
        self.assertEqual(item.score, 3)
        self.assertEqual(item.sentiment, "Strong")
        self.assertIn("Earnings", item.tags)

    def test_yahoo_search_falls_back_to_a_crumb_when_first_call_is_rejected(self) -> None:
        class CrumbHttp(FakeHttp):
            def __call__(self, url, headers, timeout):
                host = urlsplit(url).netloc
                if host == "fc.yahoo.com":
                    self.calls.append(url)
                    return HttpResponse(status=404, body=b"")
                if "/v1/test/getcrumb" in url:
                    self.calls.append(url)
                    return HttpResponse(status=200, body=b"Xy9crumb")
                if "/v1/finance/search" in url and "crumb=" not in url:
                    self.calls.append(url)
                    return HttpResponse(status=401, body=b"")
                return super().__call__(url, headers, timeout)

        http = CrumbHttp()
        engine = _engine(http, sources=("yahoo_search",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(len(items), 1)
        search_calls = [call for call in http.calls if "/v1/finance/search" in call]
        self.assertEqual(len(search_calls), 2)
        self.assertEqual(parse_qs(urlsplit(search_calls[1]).query)["crumb"], ["Xy9crumb"])
        self.assertEqual(engine.source_status()[0]["status"], "ok")

    def test_yahoo_rss_items_are_per_ticker(self) -> None:
        engine = _engine(FakeHttp(), sources=("yahoo_rss",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].via, "Yahoo Finance RSS")
        self.assertEqual(items[0].summary, "Apple reported quarterly revenue above expectations.")
        self.assertEqual(items[1].sentiment, "Negative")

    def test_alpaca_news_uses_configured_keys_and_symbol_tags(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("alpaca",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Apple Raises Price Target At Morgan Stanley"])
        self.assertEqual(items[0].source, "Benzinga")
        self.assertEqual(items[0].via, "Alpaca News (Benzinga)")
        self.assertIn("Analyst", items[0].tags)
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["symbols"], ["AAPL"])
        self.assertEqual(query["sort"], ["desc"])

    def test_alpaca_without_keys_is_reported_not_guessed(self) -> None:
        engine = _engine(FakeHttp(), sources=("alpaca",), alpaca_credentials=("", ""))
        self.assertEqual(engine.load_symbol_news("AAPL"), [])
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "blocked")
        self.assertIn("not configured", status["error"])

    def test_finviz_table_parses_dates_that_carry_over_between_rows(self) -> None:
        articles = FinvizSource(_engine(FakeHttp())).parse(FINVIZ_HTML, "AAPL", 10, now=NOW)
        self.assertEqual(len(articles), 3)
        self.assertEqual(articles[0].publisher, "WSJ")
        self.assertEqual(articles[0].url, "https://www.wsj.com/tech/apple-chip-deal")
        # 09:45 ET on Sep-26 is 13:45 UTC during daylight time.
        self.assertEqual(articles[0].published_at, datetime(2026, 9, 26, 13, 45, tzinfo=timezone.utc))
        # A bare time inherits the previous row's date.
        self.assertEqual(articles[1].published_at, datetime(2026, 9, 26, 12, 10, tzinfo=timezone.utc))
        self.assertEqual(articles[1].url, "https://finviz.com/news/12345/apple-note")
        self.assertEqual(articles[1].publisher, "Reuters")
        self.assertEqual(articles[2].published_at, datetime(2026, 9, 25, 20, 30, tzinfo=timezone.utc))

    def test_finviz_layout_change_is_an_error_not_silent_empty(self) -> None:
        engine = _engine(FakeHttp(), sources=("finviz",))
        with self.assertRaises(OSError):
            FinvizSource(engine).parse("<html>no table here</html>", "AAPL", 10)

    def test_nasdaq_rss_honours_ticker_tags(self) -> None:
        engine = _engine(FakeHttp(), sources=("nasdaq",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["3 Reasons Apple Is a Buy Right Now"])
        self.assertEqual(items[0].source, "The Motley Fool")
        self.assertEqual(items[0].related_symbols, "AAPL,MSFT")


class MergeAndStatusTests(unittest.TestCase):
    def test_all_sources_merge_dedupe_and_report_status(self) -> None:
        http = FakeHttp()
        engine = _engine(http)
        items = engine.load_symbol_news("AAPL")
        headlines = [item.headline for item in items]
        # Same story from Yahoo search + Yahoo RSS dedupes by canonical URL;
        # same story from Yahoo RSS + Finviz dedupes by headline.
        self.assertEqual(headlines.count("Apple beats estimates as iPhone demand surges"), 1)
        self.assertEqual(headlines.count("Apple faces EU investigation over App Store"), 1)
        self.assertEqual(
            sorted(headlines),
            sorted([
                "Apple beats estimates as iPhone demand surges",
                "Apple faces EU investigation over App Store",
                "Apple Raises Price Target At Morgan Stanley",
                "Apple signs chip supply contract with TSMC",
                "Apple stock slips ahead of event",
                "3 Reasons Apple Is a Buy Right Now",
            ]),
        )
        beats = next(item for item in items if item.headline.startswith("Apple beats"))
        # The RSS copy enriched the search copy with a summary.
        self.assertEqual(beats.summary, "Apple reported quarterly revenue above expectations.")
        self.assertEqual(beats.source, "Reuters")
        probe = next(item for item in items if "EU investigation" in item.headline)
        # Finviz named the real publisher; the Yahoo RSS copy only knew "Yahoo Finance".
        self.assertEqual(probe.source, "Reuters")
        # Newest first.
        stamps = [item.published_at for item in items]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual({name: entry["status"] for name, entry in status.items()}, {name: "ok" for name in DEFAULT_SOURCES})
        self.assertEqual(status["yahoo_search"]["items"], 1)
        self.assertEqual(status["finviz"]["items"], 3)

    def test_blocked_source_is_reported_and_others_still_load(self) -> None:
        engine = _engine(FakeHttp(blocked={"finviz.com"}, status_override={"www.nasdaq.com": 500}))
        items = engine.load_symbol_news("AAPL")
        self.assertTrue(items)
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual(status["finviz"]["status"], "blocked")
        self.assertIn("HTTP 403", status["finviz"]["error"])
        self.assertEqual(status["nasdaq"]["status"], "error")
        self.assertIn("HTTP 500", status["nasdaq"]["error"])
        self.assertEqual(status["yahoo_search"]["status"], "ok")

    def test_lookback_window_drops_stale_articles(self) -> None:
        engine = _engine(FakeHttp(), sources=("yahoo_search",), lookback_days=7)
        self.assertNotIn("Old story", " ".join(item.headline for item in engine.load_symbol_news("AAPL")))
        wide = _engine(FakeHttp(), sources=("yahoo_search",), lookback_days=60)
        self.assertIn("Old story", " ".join(item.headline for item in wide.load_symbol_news("AAPL")))

    def test_watchlist_rows_carry_source_fields_and_are_not_capped_at_80(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("finviz",), per_symbol_limit=3)
        rows = engine.load_watchlist_news(["AAPL", "MSFT"])
        self.assertEqual(len(rows), 6)
        self.assertEqual(set(rows[0]), {
            "symbol", "headline", "source", "url", "published_at", "score", "sentiment", "tags",
            "via", "summary", "related_symbols", "article_id",
        })
        status = engine.source_status()[0]
        self.assertEqual(status["symbols"], 2)
        self.assertEqual(status["items"], 6)

    def test_symbol_cache_avoids_refetching_within_ttl(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("yahoo_rss",), cache_ttl_seconds=300)
        engine.load_symbol_news("AAPL")
        engine.load_symbol_news("AAPL")
        self.assertEqual(len(http.calls), 1)


class RepositoryStorageTests(unittest.TestCase):
    def test_log_catalysts_stores_new_columns_and_dedupes_across_sources(self) -> None:
        from database.repository import TradingRepository

        with tempfile.TemporaryDirectory() as tmp:
            repository = TradingRepository(Path(tmp) / "news.db")
            base = {
                "symbol": "AAPL",
                "headline": "Apple beats estimates as iPhone demand surges",
                "source": "Reuters",
                "url": "https://finance.yahoo.com/news/apple-beats-123.html",
                "published_at": "2026-09-26T13:00:00+00:00",
                "score": 3,
                "sentiment": "Strong",
                "tags": "Positive Catalyst, Earnings",
                "via": "Yahoo Finance",
                "summary": "Apple reported quarterly revenue above expectations.",
                "related_symbols": "AAPL",
                "article_id": "aaa-1",
            }
            self.assertEqual(repository.log_catalysts([base]), 1)
            # Same URL, different second-level timestamp -> not stored twice.
            self.assertEqual(repository.log_catalysts([{**base, "published_at": "2026-09-26T13:00:37+00:00"}]), 0)
            # Same headline from another publisher URL -> not stored twice.
            self.assertEqual(
                repository.log_catalysts([{**base, "url": "https://www.wsj.com/apple-beats", "published_at": "2026-09-26T13:01:00+00:00", "via": "Finviz"}]),
                0,
            )
            # Different symbol is a separate row.
            self.assertEqual(repository.log_catalysts([{**base, "symbol": "MSFT"}]), 1)
            frame = repository.get_recent_catalysts(limit=10)
            self.assertEqual(len(frame), 2)
            row = frame[frame["symbol"] == "AAPL"].iloc[0]
            self.assertEqual(row["via"], "Yahoo Finance")
            self.assertEqual(row["summary"], base["summary"])
            self.assertEqual(row["related_symbols"], "AAPL")
            self.assertEqual(row["article_id"], "aaa-1")
            latest = repository.get_latest_catalysts_by_symbol()
            self.assertIn("via", latest.columns)
            self.assertIn("summary", latest.columns)

    def test_existing_database_gains_the_new_columns(self) -> None:
        from database.repository import TradingRepository

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "legacy.db"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    """
                    CREATE TABLE catalyst_items (
                        id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT NOT NULL, symbol TEXT NOT NULL,
                        headline TEXT NOT NULL, source TEXT, url TEXT, published_at TEXT, score INTEGER DEFAULT 0,
                        sentiment TEXT, tags TEXT, UNIQUE(symbol, headline, published_at)
                    )
                    """
                )
                connection.execute(
                    "INSERT INTO catalyst_items (created_at, symbol, headline, source, url, published_at, score, sentiment, tags) "
                    "VALUES ('2026-09-01T00:00:00', 'AAPL', 'Legacy headline', 'Google News', 'https://x/y', '2026-09-01T00:00:00+00:00', 1, 'Neutral', 'News')"
                )
            repository = TradingRepository(path)
            frame = repository.get_recent_catalysts(limit=5)
            self.assertEqual(len(frame), 1)
            for column in ("via", "summary", "related_symbols", "article_id"):
                self.assertIn(column, frame.columns)


if __name__ == "__main__":
    unittest.main()
