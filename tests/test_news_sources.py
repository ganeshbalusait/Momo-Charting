from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from catalyst_engine import (
    ALL_SOURCES,
    DEFAULT_SOURCES,
    KEY_SOURCES,
    BenzingaRssSource,
    CatalystEngine,
    FinvizSource,
    HttpResponse,
    SecEdgarSource,
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


def _av_stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%S")


# Benzinga's feed is site-wide.  Only items Benzinga tagged with an exchange:ticker
# (or a <category> from its ticker taxonomy, i.e. with a quote/ticker domain) may
# be attributed to a symbol.  A bare topic category is never a ticker tag.
BENZINGA_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/"><channel>
<title>Benzinga</title>
<item><title>Apple (NASDAQ:AAPL) Unveils New Chips</title>
<link>https://www.benzinga.com/news/26/09/apple-chips?utm_source=rss</link>
<pubDate>{_rfc822(1)}</pubDate><guid>bz-1</guid><dc:creator>Benzinga Newsdesk</dc:creator>
<category>News</category><category>Tech</category>
<description>Apple Inc. (NASDAQ:AAPL) introduced new chips at its event.</description></item>
<item><title>Tesla Cuts Prices Again</title>
<link>https://www.benzinga.com/news/26/09/tesla-prices</link>
<pubDate>{_rfc822(2)}</pubDate><guid>bz-2</guid>
<description>Tesla (NASDAQ: TSLA) trimmed prices; rivals such as Apple were not mentioned by the company.</description></item>
<item><title>Apple Services Growth Continues</title>
<link>https://www.benzinga.com/news/26/09/apple-services</link>
<pubDate>{_rfc822(3)}</pubDate><guid>bz-3</guid>
<category domain="https://www.benzinga.com/quote/AAPL">AAPL</category><category>News</category>
<description>Services revenue keeps climbing.</description></item>
<item><title>Fund Adds Position In NASDAQ:AAPLX Tracker</title>
<link>https://www.benzinga.com/news/26/09/aaplx</link>
<pubDate>{_rfc822(4)}</pubDate><guid>bz-4</guid>
<description>An ETF, not Apple.</description></item>
<item><title>Markets Open Higher</title>
<link>https://www.benzinga.com/news/26/09/markets-open</link>
<pubDate>{_rfc822(4)}</pubDate><guid>bz-5</guid>
<description>Broad rally with no ticker tags.</description></item>
<item><title>Microsoft Raises Dividend</title>
<link>https://www.benzinga.com/news/26/09/msft-dividend</link>
<pubDate>{_rfc822(5)}</pubDate><guid>bz-6</guid>
<content:encoded><![CDATA[<p>Microsoft Corp (NYSE: MSFT) raised its dividend.</p>]]></content:encoded>
<description>Microsoft raised its quarterly dividend.</description></item>
</channel></rss>"""

BENZINGA_NEWS_RSS = f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Benzinga News</title>
<item><title>Apple (NASDAQ:AAPL) Unveils New Chips</title>
<link>https://www.benzinga.com/news/26/09/apple-chips</link>
<pubDate>{_rfc822(1)}</pubDate><guid>bz-1</guid>
<description>Apple Inc. (NASDAQ:AAPL) introduced new chips at its event.</description></item>
<item><title>Analyst Sees Upside For Apple And Nvidia</title>
<link>https://www.benzinga.com/analyst-ratings/26/09/apple-nvidia</link>
<pubDate>{_rfc822(2.5)}</pubDate><guid>bz-7</guid>
<description>The note covers Apple (NASDAQ:AAPL) and Nvidia (NASDAQ:NVDA).</description></item>
</channel></rss>"""

SEC_ATOM = f"""<?xml version="1.0" encoding="ISO-8859-1" ?>
<feed xmlns="http://www.w3.org/2005/Atom">
<author><email>webmaster@sec.gov</email><name>Webmaster</name></author>
<company-info><cik>0000320193</cik><conformed-name>Apple Inc.</conformed-name></company-info>
<title>AAPL (0000320193) - EDGAR filings</title>
<updated>{_iso(0.5)}</updated>
<entry><title>8-K - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-26 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000001 &lt;b&gt;Size:&lt;/b&gt; 1 MB&lt;br&gt;Item 2.02: Results of Operations and Financial Condition</summary>
<updated>{_iso(1)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000001</id></entry>
<entry><title>4 - Cook Timothy D (0001214156) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000002/xslF345X05/wk-form4.xml"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-25 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000002 &lt;b&gt;Size:&lt;/b&gt; 10 KB</summary>
<updated>{_iso(20)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="4"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000002</id></entry>
<entry><title>8-K - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000007/0000320193-26-000007-index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-25 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000007 &lt;b&gt;Size:&lt;/b&gt; 120 KB&lt;br&gt;Item 5.02: Departure of Directors or Certain Officers</summary>
<updated>{_iso(25)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000007</id></entry>
<entry><title>4 - Cook Timothy D (0001214156) (Reporting)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000008/xslF345X05/wk-form4.xml"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000008 &lt;b&gt;Size:&lt;/b&gt; 9 KB</summary>
<updated>{_iso(28)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="4"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000008</id></entry>
<entry><title>UPLOAD - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000000000026000003/filename1.pdf"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-24</summary>
<updated>{_iso(30)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="UPLOAD"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000000-26-000003</id></entry>
<entry><title>SC 13G/A - Apple Inc. (0000320193) (Subject)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000000000026000004/index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-24</summary>
<updated>{_iso(40)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="SC 13G/A"/>
<id>urn:tag:sec.gov,2008:accession-number=0000000000-26-000004</id></entry>
<entry><title>424B2 - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000005/index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-23</summary>
<updated>{_iso(50)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="424B2"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000005</id></entry>
<entry><title>CORRESP - Apple Inc. (0000320193) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/index.htm"/>
<summary type="html">&lt;b&gt;Filed:&lt;/b&gt; 2026-09-22</summary>
<updated>{_iso(60)}</updated>
<category scheme="https://www.sec.gov/" label="form type" term="CORRESP"/>
<id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000006</id></entry>
</feed>"""

SEC_NO_TICKER_HTML = """<!DOCTYPE html><html><head><title>EDGAR Search Results</title></head>
<body><h1>No matching Ticker Symbol.</h1><p>Please try a different symbol.</p></body></html>"""

FINNHUB_NEWS = [
    {
        "category": "company",
        "datetime": _epoch(1.5),
        "headline": "Apple expands AI partnership with OpenAI",
        "id": 7001,
        "image": "",
        "related": "AAPL",
        "source": "MarketWatch",
        "summary": "Apple deepens its AI deal.",
        "url": "https://www.marketwatch.com/story/apple-openai",
    },
    {
        "category": "company",
        "datetime": _epoch(2.5),
        "headline": "Apple and Microsoft top the market cap table",
        "id": 7002,
        "related": "MSFT,AAPL",
        "source": "Reuters",
        "summary": "",
        "url": "https://www.reuters.com/apple-msft-caps",
    },
    {
        "category": "company",
        "datetime": _epoch(3),
        "headline": "Tesla story Finnhub filed under the wrong symbol",
        "id": 7003,
        "related": "TSLA",
        "source": "Reuters",
        "summary": "",
        "url": "https://www.reuters.com/tsla-wrong",
    },
    {
        "category": "company",
        "datetime": _epoch(3.5),
        "headline": "Untagged Finnhub item from the per-symbol endpoint",
        "id": 7004,
        "related": "",
        "source": "Reuters",
        "summary": "",
        "url": "https://www.reuters.com/aapl-untagged",
    },
]

POLYGON_NEWS = {
    "results": [
        {
            "id": "poly-1",
            "publisher": {"name": "The Motley Fool", "homepage_url": "https://www.fool.com/"},
            "title": "Is Apple Stock a Buy After Its Event?",
            "author": "Fool Staff",
            "published_utc": _iso(2),
            "article_url": "https://www.fool.com/investing/apple-event",
            "tickers": ["AAPL", "MSFT"],
            "description": "Apple hosted its fall event.",
            "keywords": ["apple", "event"],
        },
        {
            "id": "poly-2",
            "publisher": {"name": "Benzinga"},
            "title": "Nvidia story that mentions Apple in body",
            "published_utc": _iso(2),
            "article_url": "https://www.benzinga.com/nvda-story",
            "tickers": ["NVDA"],
            "description": "",
        },
    ],
    "status": "OK",
    "count": 2,
}

ALPHA_VANTAGE_NEWS = {
    "items": "3",
    "feed": [
        {
            "title": "Apple Supplier Wins Big Contract",
            "url": "https://www.example-news.com/apple-supplier",
            "time_published": _av_stamp(1),
            "summary": "A key Apple supplier won a contract.",
            "source": "Example News",
            "ticker_sentiment": [
                {"ticker": "AAPL", "relevance_score": "0.55", "ticker_sentiment_label": "Somewhat-Bullish"},
                {"ticker": "TSM", "relevance_score": "0.30", "ticker_sentiment_label": "Bullish"},
            ],
        },
        {
            "title": "Broad Tech Roundup Barely Mentions Apple",
            "url": "https://www.example-news.com/tech-roundup",
            "time_published": _av_stamp(2),
            "summary": "Roundup.",
            "source": "Example News",
            "ticker_sentiment": [
                {"ticker": "AAPL", "relevance_score": "0.05", "ticker_sentiment_label": "Neutral"},
                {"ticker": "MSFT", "relevance_score": "0.60", "ticker_sentiment_label": "Neutral"},
            ],
        },
        {
            "title": "Nothing to do with Apple",
            "url": "https://www.example-news.com/other",
            "time_published": _av_stamp(3),
            "summary": "",
            "source": "Example News",
            "ticker_sentiment": [{"ticker": "NVDA", "relevance_score": "0.9", "ticker_sentiment_label": "Bullish"}],
        },
    ],
}

ALPHA_VANTAGE_RATE_LIMIT = {
    "Information": "We have detected your API key as demo and our standard API rate limit is 25 requests per day."
}

TIINGO_NEWS = [
    {
        "id": 55001,
        "title": "Apple Shares Rise On Upgrade",
        "url": "https://www.example-wire.com/apple-upgrade",
        "description": "An analyst upgraded Apple.",
        "publishedDate": _iso(1.2),
        "source": "example-wire.com",
        "tickers": ["aapl", "msft"],
    },
    {
        "id": 55002,
        "title": "Tesla item Tiingo tagged to TSLA only",
        "url": "https://www.example-wire.com/tsla",
        "description": "",
        "publishedDate": _iso(1.5),
        "source": "example-wire.com",
        "tickers": ["tsla"],
    },
]


class FakeHttp:
    """Routes engine requests to fixtures by host and records every call."""

    def __init__(
        self,
        blocked: set[str] | None = None,
        status_override: dict[str, int] | None = None,
        path_status: dict[str, int] | None = None,
        bodies: dict[str, str] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.blocked = blocked or set()
        self.status_override = status_override or {}
        self.path_status = path_status or {}  # "host/path" -> HTTP status
        self.bodies = bodies or {}  # "host/path" -> response body override (mutable between calls)

    def __call__(self, url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        self.calls.append(url)
        host = urlsplit(url).netloc
        if host in self.blocked:
            return HttpResponse(status=403, body=b"blocked")
        if host in self.status_override:
            return HttpResponse(status=self.status_override[host], body=b"")
        route = f"{host}{urlsplit(url).path}"
        if route in self.path_status:
            return HttpResponse(status=self.path_status[route], body=b"")
        if route in self.bodies:
            return HttpResponse(status=200, body=self.bodies[route].encode())
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
        path = urlsplit(url).path
        query = parse_qs(urlsplit(url).query)
        if host == "www.benzinga.com":
            if path == "/feed":
                return HttpResponse(status=200, body=BENZINGA_RSS.encode())
            if path == "/news/feed":
                return HttpResponse(status=200, body=BENZINGA_NEWS_RSS.encode())
        if host == "www.sec.gov":
            assert "contact:" in headers.get("User-Agent", ""), headers
            assert headers.get("Accept", "").startswith("application/atom+xml"), headers
            if query.get("CIK") == ["ZZZZ"]:
                return HttpResponse(status=200, body=SEC_NO_TICKER_HTML.encode())
            return HttpResponse(status=200, body=SEC_ATOM.encode())
        if host == "finnhub.io":
            assert query.get("token") == ["fh-key"], url
            return HttpResponse(status=200, body=json.dumps(FINNHUB_NEWS).encode())
        if host == "api.polygon.io":
            assert query.get("apiKey") == ["pg-key"], url
            return HttpResponse(status=200, body=json.dumps(POLYGON_NEWS).encode())
        if host == "www.alphavantage.co":
            if query.get("apikey") == ["av-limited"]:
                return HttpResponse(status=200, body=json.dumps(ALPHA_VANTAGE_RATE_LIMIT).encode())
            assert query.get("apikey") == ["av-key"], url
            return HttpResponse(status=200, body=json.dumps(ALPHA_VANTAGE_NEWS).encode())
        if host == "api.tiingo.com":
            assert query.get("token") == ["tg-key"], url
            return HttpResponse(status=200, body=json.dumps(TIINGO_NEWS).encode())
        return HttpResponse(status=404, body=b"")


def _engine(http: FakeHttp, **overrides) -> CatalystEngine:
    options = dict(alpaca_credentials=("key", "secret"), http_get=http, cache_ttl_seconds=0, per_symbol_limit=25)
    options.update(overrides)
    return CatalystEngine(**options)


ALL_KEYS = {"finnhub": "fh-key", "polygon": "pg-key", "alphavantage": "av-key", "tiingo": "tg-key"}


class ParserTests(unittest.TestCase):
    def test_default_sources_are_ticker_tagged_and_google_is_opt_in(self) -> None:
        self.assertEqual(parse_source_list(None), DEFAULT_SOURCES)
        self.assertEqual(DEFAULT_SOURCES, ("yahoo_search", "yahoo_rss", "alpaca", "benzinga", "finviz", "nasdaq", "sec_edgar"))
        self.assertNotIn("google_news", DEFAULT_SOURCES)
        for name in KEY_SOURCES:
            self.assertNotIn(name, DEFAULT_SOURCES)
            self.assertIn(name, ALL_SOURCES)
        self.assertEqual(parse_source_list("finviz, google_news ,bogus"), ("finviz", "google_news"))
        self.assertEqual(parse_source_list("benzinga;sec_edgar,finnhub,polygon,alphavantage,tiingo"),
                         ("benzinga", "sec_edgar", "finnhub", "polygon", "alphavantage", "tiingo"))
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


class BenzingaTests(unittest.TestCase):
    def test_exchange_tag_regex_is_explicit_and_word_bounded(self) -> None:
        tags = BenzingaRssSource.exchange_tags
        self.assertEqual(tags("Apple (NASDAQ:AAPL) and Tesla (NASDAQ: TSLA)"), ("AAPL", "TSLA"))
        self.assertEqual(tags("NYSE:BRK.B, AMEX:GLD, ARCA:SPY, OTC:TCEHY, BATS:CBOE, TSX:SHOP"),
                         ("BRK.B", "GLD", "SPY", "TCEHY", "CBOE", "SHOP"))
        # A longer token is a different ticker; a bare company name is not a tag.
        self.assertEqual(tags("NASDAQ:AAPLX tracker"), ("AAPLX",))
        self.assertEqual(tags("Apple beats estimates; AAPL up"), ())
        self.assertEqual(tags("XNASDAQ:AAPL"), ())

    def test_benzinga_attributes_only_exchange_tagged_or_category_tagged_items(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("benzinga",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(
            [item.headline for item in items],
            [
                "Apple (NASDAQ:AAPL) Unveils New Chips",
                "Analyst Sees Upside For Apple And Nvidia",
                "Apple Services Growth Continues",
            ],
        )
        self.assertTrue(all(item.source == "Benzinga" and item.via == "Benzinga" for item in items))
        self.assertEqual(items[1].related_symbols, "AAPL,NVDA")
        # The duplicate of bz-1 from the second feed is stored once, tracking params stripped.
        self.assertEqual(sum(1 for item in items if item.article_id == "bz-1"), 1)
        # Both site-wide feeds were fetched exactly once for this symbol.
        self.assertEqual(sorted(http.calls), ["https://www.benzinga.com/feed", "https://www.benzinga.com/news/feed"])
        # Negative cases: keyword mention (bz-2), NASDAQ:AAPLX (bz-4), untagged (bz-5), MSFT-only (bz-6).
        headlines = " ".join(item.headline for item in items)
        for missing in ("Tesla Cuts Prices", "AAPLX", "Markets Open Higher", "Microsoft Raises Dividend"):
            self.assertNotIn(missing, headlines)
        msft = _engine(FakeHttp(), sources=("benzinga",)).load_symbol_news("MSFT")
        self.assertEqual([item.headline for item in msft], ["Microsoft Raises Dividend"])

    def test_benzinga_feed_is_fetched_once_per_run_across_symbols(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("benzinga",))
        rows = engine.load_watchlist_news(["AAPL", "MSFT", "TSLA", "NVDA"])
        benzinga_calls = [call for call in http.calls if "benzinga.com" in call]
        self.assertEqual(len(benzinga_calls), 2)  # two feed URLs, one fetch each for the whole run
        self.assertEqual({row["symbol"] for row in rows}, {"AAPL", "MSFT", "TSLA", "NVDA"})
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["symbols"], 4)
        # A new run fetches again (cache_ttl_seconds=0 in this fixture).
        engine.load_watchlist_news(["AAPL"])
        self.assertEqual(len([call for call in http.calls if "benzinga.com" in call]), 4)

    def test_benzinga_blocked_feed_is_reported_per_symbol_without_refetching(self) -> None:
        http = FakeHttp(blocked={"www.benzinga.com"})
        engine = _engine(http, sources=("benzinga",))
        engine.load_watchlist_news(["AAPL", "MSFT"])
        # The first feed was rejected before anything was fetched: the second URL is not hammered.
        self.assertEqual(len(http.calls), 1)
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "blocked")
        self.assertEqual(status["failures"], 2)

    def test_benzinga_second_feed_blocked_keeps_first_feed_items(self) -> None:
        http = FakeHttp(path_status={"www.benzinga.com/news/feed": 429})
        engine = _engine(http, sources=("benzinga",))
        items = engine.load_symbol_news("AAPL")
        # Everything parsed from /feed survives; only the /news/feed-only story (bz-7) is missing.
        self.assertEqual(
            [item.headline for item in items],
            ["Apple (NASDAQ:AAPL) Unveils New Chips", "Apple Services Growth Continues"],
        )
        self.assertEqual(sorted(http.calls), ["https://www.benzinga.com/feed", "https://www.benzinga.com/news/feed"])
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "ok")
        self.assertEqual(status["items"], 2)
        self.assertEqual(status["failures"], 0)
        # Both feeds rejected -> still reported as blocked, not as a generic error.
        both = _engine(FakeHttp(path_status={"www.benzinga.com/feed": 404, "www.benzinga.com/news/feed": 429}), sources=("benzinga",))
        self.assertEqual(both.load_symbol_news("AAPL"), [])
        self.assertEqual(both.source_status()[0]["status"], "blocked")
        self.assertIn("HTTP 429", both.source_status()[0]["error"])

    def test_benzinga_category_tags_ignore_section_names_and_long_tokens(self) -> None:
        looks = BenzingaRssSource._looks_like_ticker
        # A category from Benzinga's ticker taxonomy (quote/ticker domain) is a ticker tag.
        for ticker in ("AAPL", "GOOGL", "BRK.B", "PBR-A", "F", "X", "TSLA"):
            self.assertTrue(looks(ticker, "https://www.benzinga.com/quote/" + ticker), ticker)
        self.assertTrue(looks("AI", "https://www.benzinga.com/quote/AI"))
        self.assertTrue(looks("aapl", "ticker"))
        # A bare category, even one that looks exactly like a ticker, is a topic label: never a guess.
        for bare in ("AAPL", "TSLA", "AI", "IPO", "FDA", "ESG", "SPAC", "ETF", "REIT", "EARNINGS", "M&A", "SEC",
                     "FOMC", "EV", "OPTIONS", "MARKETS", "News", "Tech", "Trading Ideas", "", "aapl"):
            self.assertFalse(looks(bare), bare)
        self.assertFalse(looks("Trading Ideas", "https://www.benzinga.com/topic"))
        self.assertFalse(looks("AAPL", "https://www.benzinga.com/topic"))
        feed = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>
<item><title>How AI Is Reshaping Retail</title><link>https://www.benzinga.com/news/26/09/ai-retail</link>
<pubDate>{_rfc822(1)}</pubDate><guid>bz-ai</guid><category>AI</category><category>News</category>
<description>A generic AI topic story, no exchange tag anywhere.</description></item>
<item><title>C3.ai Wins Contract</title><link>https://www.benzinga.com/news/26/09/c3ai</link>
<pubDate>{_rfc822(2)}</pubDate><guid>bz-c3</guid><category>AI</category>
<description>C3.ai (NYSE: AI) signed a new deal.</description></item>
</channel></rss>"""
        http = FakeHttp(bodies={"www.benzinga.com/feed": feed, "www.benzinga.com/news/feed": feed})
        engine = _engine(http, sources=("benzinga",))
        self.assertEqual([item.headline for item in engine.load_symbol_news("AI")], ["C3.ai Wins Contract"])

    def test_benzinga_direct_calls_refetch_after_ttl(self) -> None:
        first = BENZINGA_RSS
        second = BENZINGA_RSS.replace("Apple Services Growth Continues", "Apple Services Growth Accelerates")
        http = FakeHttp(bodies={"www.benzinga.com/feed": first, "www.benzinga.com/news/feed": BENZINGA_NEWS_RSS})
        engine = _engine(http, sources=("benzinga",))  # cache_ttl_seconds=0
        self.assertIn("Apple Services Growth Continues", [item.headline for item in engine.load_symbol_news("AAPL")])
        http.bodies["www.benzinga.com/feed"] = second
        headlines = [item.headline for item in engine.load_symbol_news("AAPL")]
        self.assertIn("Apple Services Growth Accelerates", headlines)
        self.assertNotIn("Apple Services Growth Continues", headlines)
        self.assertEqual(len([call for call in http.calls if "benzinga.com" in call]), 4)
        # A transient failure is not cached forever either.
        flaky = FakeHttp(path_status={"www.benzinga.com/feed": 500, "www.benzinga.com/news/feed": 500})
        engine = _engine(flaky, sources=("benzinga",))
        self.assertEqual(engine.load_symbol_news("AAPL"), [])
        self.assertEqual(engine.source_status()[0]["status"], "error")
        flaky.path_status.clear()
        self.assertTrue(engine.load_symbol_news("MSFT"))
        self.assertEqual(engine.source_status()[0]["status"], "partial")
        # Within the TTL, direct calls for different symbols share one fetch of the site-wide feed.
        http = FakeHttp()
        engine = _engine(http, sources=("benzinga",), cache_ttl_seconds=300)
        engine.load_symbol_news("AAPL")
        engine.load_symbol_news("MSFT")
        self.assertEqual(len([call for call in http.calls if "benzinga.com" in call]), 2)


class SecEdgarTests(unittest.TestCase):
    def test_edgar_keeps_catalyst_forms_and_labels_them_as_filings(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("sec_edgar",), lookback_days=7)
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(
            [item.headline for item in items],
            [
                "8-K filing: Apple Inc. (2026-09-26, Item 2.02: Results of Operations and Financial Condition, AccNo 0000320193-26-000001)",
                "4 filing: Cook Timothy D (2026-09-25, AccNo 0000320193-26-000002)",
                "8-K filing: Apple Inc. (2026-09-25, Item 5.02: Departure of Directors or Certain Officers, AccNo 0000320193-26-000007)",
                "4 filing: Cook Timothy D (2026-09-24, AccNo 0000320193-26-000008)",
                "SC 13G/A filing: Apple Inc. (2026-09-24, AccNo 0000000000-26-000004)",
                "424B2 filing: Apple Inc. (2026-09-23, AccNo 0000320193-26-000005)",
            ],
        )
        self.assertTrue(all(item.source == "SEC EDGAR" and item.via == "SEC EDGAR" for item in items))
        self.assertTrue(all(item.tags.startswith("Filing") for item in items))
        self.assertEqual(items[0].summary, "Filed: 2026-09-26 AccNo: 0000320193-26-000001 Size: 1 MB Item 2.02: Results of Operations and Financial Condition")
        self.assertEqual(items[0].article_id, "urn:tag:sec.gov,2008:accession-number=0000320193-26-000001")
        self.assertTrue(items[0].url.startswith("https://www.sec.gov/Archives/edgar/data/320193/"))
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["CIK"], ["AAPL"])
        self.assertEqual(query["output"], ["atom"])
        self.assertEqual(query["owner"], ["include"])
        self.assertEqual(engine.source_status()[0]["status"], "ok")

    def test_edgar_repeated_forms_by_the_same_filer_are_distinct_filings(self) -> None:
        from database.repository import TradingRepository

        http = FakeHttp()
        engine = _engine(http, sources=("sec_edgar",), lookback_days=7)
        items = engine.load_symbol_news("AAPL")
        eight_ks = [item for item in items if item.headline.startswith("8-K filing")]
        form4s = [item for item in items if item.headline.startswith("4 filing")]
        self.assertEqual(len(eight_ks), 2)
        self.assertEqual(len(form4s), 2)
        self.assertEqual(len({item.headline for item in items}), len(items))
        self.assertEqual(engine.source_status()[0]["items"], 6)
        # Watchlist rows keep all four too, and the repository stores each filing once.
        rows = [row for row in engine.load_watchlist_news(["AAPL"]) if row["symbol"] == "AAPL"]
        self.assertEqual(len(rows), 6)
        with tempfile.TemporaryDirectory() as tmp:
            repository = TradingRepository(Path(tmp) / "filings.db")
            self.assertEqual(repository.log_catalysts(rows), 6)
            self.assertEqual(repository.log_catalysts(rows), 0)
            # A later 8-K by the same company (new accession, six days later) is a new row.
            later = dict(rows[0])
            later.update(
                headline="8-K filing: Apple Inc. (2026-10-02, Item 8.01: Other Events, AccNo 0000320193-26-000009)",
                url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000009/0000320193-26-000009-index.htm",
                published_at="2026-10-02T13:00:00+00:00",
                article_id="urn:tag:sec.gov,2008:accession-number=0000320193-26-000009",
            )
            self.assertEqual(repository.log_catalysts([later]), 1)

    def test_edgar_headline_carries_date_items_and_accession(self) -> None:
        published = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            SecEdgarSource.headline_for(
                "8-K", "Apple Inc.", published, "urn:tag:sec.gov,2008:accession-number=0000320193-26-000001",
                "Filed: 2026-09-26 AccNo: 0000320193-26-000001 Size: 1 MB Item 2.02: Results of Operations and Financial Condition",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm",
            ),
            "8-K filing: Apple Inc. (2026-09-26, Item 2.02: Results of Operations and Financial Condition, AccNo 0000320193-26-000001)",
        )
        # Accession falls back to the summary, then the URL folder.
        self.assertEqual(SecEdgarSource.accession_for("", "AccNo: 0000320193-26-000002 Size: 9 KB", ""), "0000320193-26-000002")
        self.assertEqual(
            SecEdgarSource.accession_for("", "", "https://www.sec.gov/Archives/edgar/data/320193/000032019326000003/index.htm"),
            "0000320193-26-000003",
        )
        self.assertEqual(SecEdgarSource.headline_for("10-Q", "Apple Inc.", published, "", "", ""), "10-Q filing: Apple Inc. (2026-09-26)")
        # EDGAR's own "Filed:" date wins over the entry timestamp when both are present.
        self.assertEqual(
            SecEdgarSource.headline_for("4", "Cook Timothy D", published, "", "Filed: 2026-09-24 AccNo: 0000320193-26-000008 Size: 9 KB", ""),
            "4 filing: Cook Timothy D (2026-09-24, AccNo 0000320193-26-000008)",
        )

    def test_edgar_form_filter(self) -> None:
        keep = SecEdgarSource.keep_form
        for form in ("8-K", "8-K/A", "10-Q", "10-K", "6-K", "S-1", "S-3", "424B2", "424B5", "SC 13D", "SC 13G/A", "DEF 14A", "3", "4", "4/A"):
            self.assertTrue(keep(form), form)
        for form in ("UPLOAD", "CORRESP", "13F-HR", "SD", "11-K", "ARS", "144", "", "S-8", "10-K405X"):
            self.assertFalse(keep(form), form)

    def test_edgar_unknown_ticker_is_an_error_not_silent_empty(self) -> None:
        engine = _engine(FakeHttp(), sources=("sec_edgar",))
        self.assertEqual(engine.load_symbol_news("ZZZZ"), [])
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "error")
        self.assertIn("No matching Ticker Symbol", status["error"])
        with self.assertRaises(OSError):
            SecEdgarSource(engine).parse("<html><body>maintenance</body></html>", "AAPL", 10)

    def test_edgar_user_agent_carries_contact(self) -> None:
        engine = _engine(FakeHttp(), sources=("sec_edgar",), contact_email="trader@example.org")
        self.assertEqual(engine.sec_user_agent, "AgenticAI-Trading/1.0 (contact: trader@example.org)")
        self.assertIn("noreply@example.com", _engine(FakeHttp()).sec_user_agent)


class KeySourceTests(unittest.TestCase):
    def test_finnhub_uses_related_ticker_list(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("finnhub",), api_keys={"finnhub": "fh-key"})
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(
            [item.headline for item in items],
            [
                "Apple expands AI partnership with OpenAI",
                "Apple and Microsoft top the market cap table",
                "Untagged Finnhub item from the per-symbol endpoint",
            ],
        )
        self.assertEqual(items[0].source, "MarketWatch")
        self.assertEqual(items[0].via, "Finnhub")
        self.assertEqual(items[1].related_symbols, "AAPL,MSFT")
        self.assertEqual(items[0].article_id, "7001")
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["symbol"], ["AAPL"])
        self.assertRegex(query["from"][0], r"^\d{4}-\d{2}-\d{2}$")
        self.assertRegex(query["to"][0], r"^\d{4}-\d{2}-\d{2}$")

    def test_polygon_uses_tickers_list(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("polygon",), api_keys={"polygon": "pg-key"})
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Is Apple Stock a Buy After Its Event?"])
        self.assertEqual(items[0].source, "The Motley Fool")
        self.assertEqual(items[0].via, "Polygon")
        self.assertEqual(items[0].related_symbols, "AAPL,MSFT")
        self.assertEqual(items[0].article_id, "poly-1")
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["ticker"], ["AAPL"])
        self.assertEqual(query["sort"], ["published_utc"])
        self.assertEqual(query["order"], ["desc"])

    def test_alphavantage_uses_publisher_relevance_threshold(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("alphavantage",), api_keys={"alphavantage": "av-key"})
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Apple Supplier Wins Big Contract"])
        self.assertEqual(items[0].source, "Example News")
        self.assertEqual(items[0].via, "Alpha Vantage")
        self.assertEqual(items[0].related_symbols, "AAPL,TSM")
        self.assertEqual(items[0].published_at, (NOW - timedelta(hours=1)).isoformat())
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["function"], ["NEWS_SENTIMENT"])
        self.assertEqual(query["tickers"], ["AAPL"])

    def test_alphavantage_rate_limit_note_is_reported_as_blocked(self) -> None:
        engine = _engine(FakeHttp(), sources=("alphavantage",), api_keys={"alphavantage": "av-limited"})
        self.assertEqual(engine.load_symbol_news("AAPL"), [])
        status = engine.source_status()[0]
        self.assertEqual(status["status"], "blocked")
        self.assertIn("rate limited", status["error"])

    def test_tiingo_uses_lowercase_tickers_list(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("tiingo",), api_keys={"tiingo": "tg-key"})
        items = engine.load_symbol_news("AAPL")
        self.assertEqual([item.headline for item in items], ["Apple Shares Rise On Upgrade"])
        self.assertEqual(items[0].via, "Tiingo")
        self.assertEqual(items[0].related_symbols, "AAPL,MSFT")
        self.assertEqual(items[0].article_id, "55001")
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["tickers"], ["aapl"])
        self.assertEqual(query["sortBy"], ["publishedDate"])

    def test_key_sources_without_a_key_are_skipped_not_failed(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("yahoo_rss", "finnhub", "polygon", "alphavantage", "tiingo"), api_keys={"tiingo": "tg-key"})
        self.assertEqual([source.name for source in engine.sources], ["yahoo_rss", "tiingo"])
        items = engine.load_symbol_news("AAPL")
        self.assertTrue(items)
        self.assertEqual({entry["name"] for entry in engine.source_status()}, {"yahoo_rss", "tiingo"})
        self.assertTrue(all(entry["status"] == "ok" for entry in engine.source_status()))
        available = {entry["name"]: entry for entry in engine.available_sources()}
        self.assertEqual(list(available), list(ALL_SOURCES))
        self.assertTrue(available["tiingo"]["enabled"])
        self.assertEqual(available["tiingo"]["reason"], "")
        for name, env in (("finnhub", "FINNHUB_API_KEY"), ("polygon", "POLYGON_API_KEY"), ("alphavantage", "ALPHA_VANTAGE_API_KEY")):
            self.assertFalse(available[name]["enabled"])
            self.assertEqual(available[name]["reason"], f"{env} is not set")
            self.assertEqual(available[name]["requiresKey"], env)
        self.assertFalse(available["google_news"]["enabled"])
        self.assertIn("opt-in", available["google_news"]["reason"])
        self.assertFalse(available["benzinga"]["enabled"])
        self.assertEqual(available["benzinga"]["reason"], "not listed in NEWS_SOURCES")
        self.assertTrue(available["yahoo_rss"]["enabled"])

    def test_configured_key_switches_its_source_on_without_editing_news_sources(self) -> None:
        engine = _engine(FakeHttp(), api_keys=ALL_KEYS)
        self.assertEqual([source.name for source in engine.sources], list(DEFAULT_SOURCES) + list(KEY_SOURCES))
        explicit = _engine(FakeHttp(), sources=("finnhub", "yahoo_rss"), api_keys=ALL_KEYS, auto_enable_key_sources=False)
        self.assertEqual([source.name for source in explicit.sources], ["finnhub", "yahoo_rss"])


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
                "Apple (NASDAQ:AAPL) Unveils New Chips",
                "Analyst Sees Upside For Apple And Nvidia",
                "Apple Services Growth Continues",
                "8-K filing: Apple Inc. (2026-09-26, Item 2.02: Results of Operations and Financial Condition, AccNo 0000320193-26-000001)",
                "4 filing: Cook Timothy D (2026-09-25, AccNo 0000320193-26-000002)",
                "8-K filing: Apple Inc. (2026-09-25, Item 5.02: Departure of Directors or Certain Officers, AccNo 0000320193-26-000007)",
                "4 filing: Cook Timothy D (2026-09-24, AccNo 0000320193-26-000008)",
                "SC 13G/A filing: Apple Inc. (2026-09-24, AccNo 0000000000-26-000004)",
                "424B2 filing: Apple Inc. (2026-09-23, AccNo 0000320193-26-000005)",
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
        self.assertEqual(status["benzinga"]["items"], 3)
        self.assertEqual(status["sec_edgar"]["items"], 6)

    def test_every_key_source_merges_with_the_defaults(self) -> None:
        engine = _engine(FakeHttp(), api_keys=ALL_KEYS)
        items = engine.load_symbol_news("AAPL")
        status = {entry["name"]: entry for entry in engine.source_status()}
        self.assertEqual({name: entry["status"] for name, entry in status.items()},
                         {name: "ok" for name in DEFAULT_SOURCES + KEY_SOURCES})
        vias = {item.via for item in items}
        for label in ("Finnhub", "Polygon", "Alpha Vantage", "Tiingo", "Benzinga", "SEC EDGAR"):
            self.assertIn(label, vias)
        self.assertTrue(all(len(item.summary) <= 600 and len(item.headline) <= 400 for item in items))

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


class ApiServerNewsMetaTests(unittest.TestCase):
    def test_news_feed_meta_carries_available_sources(self) -> None:
        import types

        import api_server

        state = api_server.DashboardState.__new__(api_server.DashboardState)
        state.catalysts = _engine(FakeHttp(), sources=("yahoo_rss",), api_keys={"tiingo": "tg-key"})
        state.repository = types.SimpleNamespace(log_catalysts=lambda items: 0, log_bot_event=lambda kind, message: None)
        meta = state._refresh_catalyst_information(["AAPL"])
        self.assertEqual({entry["name"] for entry in meta["sources"]}, {"yahoo_rss", "tiingo"})
        available = {entry["name"]: entry for entry in meta["availableSources"]}
        self.assertEqual(list(available), list(ALL_SOURCES))
        self.assertTrue(available["tiingo"]["enabled"])
        self.assertFalse(available["finnhub"]["enabled"])
        self.assertEqual(available["finnhub"]["reason"], "FINNHUB_API_KEY is not set")

    def test_build_catalyst_engine_passes_news_keys_and_contact(self) -> None:
        import api_server

        news = api_server.settings.news
        saved = (news.finnhub_api_key, news.polygon_api_key, news.alpha_vantage_api_key, news.tiingo_api_key, news.contact_email)
        try:
            news.finnhub_api_key, news.polygon_api_key, news.alpha_vantage_api_key, news.tiingo_api_key = "a", "", "c", ""
            news.contact_email = "ops@example.org"
            engine = api_server.DashboardState._build_catalyst_engine()
        finally:
            news.finnhub_api_key, news.polygon_api_key, news.alpha_vantage_api_key, news.tiingo_api_key, news.contact_email = saved
        enabled = [source.name for source in engine.sources]
        self.assertIn("finnhub", enabled)
        self.assertIn("alphavantage", enabled)
        self.assertNotIn("polygon", enabled)
        self.assertNotIn("tiingo", enabled)
        self.assertEqual(engine.api_key("finnhub"), "a")
        self.assertEqual(engine.contact_email, "ops@example.org")


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
