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
    BenzingaSource,
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


def _iso_ns(hours_ago: float) -> str:
    # Benzinga's site API stamps with nanoseconds: 2026-09-25T17:35:11.067620084Z
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%S") + ".067620084Z"


def _edgar_stamp(hours_ago: float) -> str:
    # EDGAR's <updated> is Eastern with an offset: 2026-09-24T18:30:07-04:00
    return (NOW - timedelta(hours=hours_ago)).astimezone(timezone(timedelta(hours=-4))).isoformat()


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
        {
            # Seen live (query META, 2026-09-26): a keyword hit Yahoo did not tag with any ticker.
            "uuid": "aaa-4",
            "title": 'The "Magnificent Seven" Stocks Explained: Apple, Microsoft, Nvidia, Alphabet, Amazon, Meta',
            "publisher": "Motley Fool",
            "link": "https://finance.yahoo.com/markets/stocks/articles/magnificent-seven-stocks-explained-095500123.html",
            "providerPublishTime": _epoch(4),
            "type": "STORY",
            "relatedTickers": [],
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

NASDAQ_RSS = f"""<?xml version="1.0" encoding="utf-8"?>
<rss xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:content="http://purl.org/rss/1.0/modules/content/"
  xmlns:nasdaq="http://nasdaq.com/reference/feeds/1.0" version="2.0" xml:base="https://www.nasdaq.com/feed/rssoutbound">
 <channel>
  <title>AAPL Feed</title>
  <link>https://www.nasdaq.com/feed/rssoutbound</link>
  <description>This feed is responsible for generating the rss feed related to the topic AAPL</description>
  <language>en</language>
  <item>
   <title>3 Reasons Apple Is a Buy Right Now</title>
   <link>https://www.nasdaq.com/articles/apple-buy</link>
   <description>
        Key PointsApple keeps growing services.
    </description>
   <pubDate>{_rfc822(6)}</pubDate>
   <guid isPermaLink="true">https://www.nasdaq.com/articles/apple-buy?time=1790436420</guid>
   <dc:creator>The Motley Fool</dc:creator>
   <category>Markets</category>
   <nasdaq:tickers>AAPL,AAPL,MSFT</nasdaq:tickers>
  </item>
  <item>
   <title>Nvidia article that mentions Apple in body</title>
   <link>https://www.nasdaq.com/articles/nvda-thing</link>
   <description>
        Key PointsNvidia keeps growing.
    </description>
   <pubDate>{_rfc822(6)}</pubDate>
   <guid isPermaLink="true">https://www.nasdaq.com/articles/nvda-thing?time=1790436000</guid>
   <dc:creator>The Motley Fool</dc:creator>
   <category>Markets</category>
   <nasdaq:tickers>NVDA,NVDA</nasdaq:tickers>
  </item>
 </channel>
</rss>"""

def _av_stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y%m%dT%H%M%S")


# Benzinga's site news API answers per ticker with a bare JSON list; every story carries
# Benzinga's own ``stocks`` / ``tickers`` lists.  Trimmed from the live AAPL response
# (2026-09-26); ``meta``/``assets``/``image`` blobs dropped.
BENZINGA_API = {
    "AAPL": [
        {
            "id": 62002905,
            "nodeId": 62002905,
            "storyId": "6ab6a8de20a62c00013f3a20",
            "title": "What Is Going on With Qualcomm Stock on Friday?",
            "url": "https://www.benzinga.com/markets/tech/26/09/62002905/what-is-going-on-with-qualcomm-stock-on-friday",
            "author": "Anusuya Lahiri",
            "created": _iso_ns(1),
            "createdAt": _iso_ns(1),
            "updated": _iso_ns(1),
            "updatedAt": _iso_ns(1),
            "teaser": "<p>Qualcomm stock surged following new Snapdragon 8 Elite chip rollouts.</p>",
            "teaserText": "Qualcomm stock surged following new Snapdragon 8 Elite chip rollouts.",
            "stocks": [{"name": "QCOM"}, {"name": "AAPL"}, {"name": "TSM"}],
            "tickers": [
                {"tid": 12675, "vid": 2, "name": "QCOM", "primary": True},
                {"tid": 10394, "vid": 2, "name": "AAPL"},
                {"tid": 16475, "vid": 2, "name": "TSM"},
            ],
            "channels": [{"tid": 16, "vid": 1, "name": "Tech"}, {"tid": 17, "vid": 1, "name": "News"}],
            "tags": ["Why It's Moving", "benzai"],
            "isBzPost": True,
            "isBzProPost": False,
        },
        {
            "id": 61990122,
            "nodeId": 61990122,
            "storyId": "6ab645ce20a62c00013f04f8",
            "title": "Understanding Apple&#39;s Position In Technology Hardware, Storage &amp; Peripherals Industry",
            "url": "https://www.benzinga.com/news/26/09/61990122/understanding-apple-s-position-technology-hardware-storage-amp-peripherals-industry-compared-competi",
            "author": "Benzinga Insights",
            "created": _iso_ns(3),
            "createdAt": _iso_ns(3),
            "updated": _iso_ns(3),
            "updatedAt": _iso_ns(3),
            "teaser": "<p>In today&#39;s rapidly changing and highly competitive business world, it is imperative for investors to compare.</p>",
            "teaserText": "In today&#39;s rapidly changing and highly competitive business world, it is imperative for investors to compare.",
            "stocks": [{"name": "AAPL"}],
            "tickers": [{"tid": 10394, "vid": 2, "name": "AAPL", "primary": True}],
            "channels": [{"tid": 17, "vid": 1, "name": "News"}, {"tid": 63, "vid": 1, "name": "Trading Ideas"}],
            "tags": None,
            "isBzPost": True,
            "isBzProPost": False,
        },
        {
            # Guard: a story in the response that Benzinga did not tag with the requested symbol.
            "id": 61998944,
            "nodeId": 61998944,
            "title": "9 Of 11 Sectors Fall In Friday Trading As Cyclicals Lead",
            "url": "https://www.benzinga.com/trading-ideas/movers/26/09/61998944/9-of-11-sectors-fall-in-friday-trading-as-cyclicals-lead",
            "author": "Benzinga Insights",
            "created": _iso_ns(5),
            "updated": _iso_ns(5),
            "teaserText": "Friday's regular session has two sectors higher and nine lower.",
            "stocks": [{"name": "SPY"}, {"name": "QQQ"}, {"name": "MSFT"}],
            "tickers": [{"tid": 10098, "vid": 2, "name": "SPY"}, {"tid": 37163, "vid": 2, "name": "QQQ"}, {"tid": 12200, "vid": 2, "name": "MSFT"}],
            "channels": [{"tid": 44, "vid": 1, "name": "Movers"}],
            "tags": ["BZI-ETFMOVERS"],
        },
    ],
    "MSFT": [
        {
            "id": 62001000,
            "nodeId": 62001000,
            "title": "Microsoft Raises Dividend",
            "url": "https://www.benzinga.com/news/26/09/62001000/microsoft-raises-dividend",
            "author": "Benzinga Newsdesk",
            "created": _iso_ns(2),
            "updated": _iso_ns(2),
            "teaserText": "Microsoft lifts its quarterly dividend.",
            "stocks": [{"name": "MSFT"}],
            "tickers": [{"tid": 12200, "vid": 2, "name": "MSFT", "primary": True}],
            "channels": [{"tid": 17, "vid": 1, "name": "News"}],
            "tags": [],
        },
    ],
}

SEC_ATOM = f"""<?xml version="1.0" encoding="ISO-8859-1" ?>
  <feed xmlns="http://www.w3.org/2005/Atom">
    <author>
      <email>webmaster@sec.gov</email>
      <name>Webmaster</name>
    </author>
    <company-info>
      <assigned-sic>3571</assigned-sic>
      <assigned-sic-desc>ELECTRONIC COMPUTERS</assigned-sic-desc>
      <cik>0000320193</cik>
      <cik-href>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=0000320193&amp;owner=include&amp;count=40</cik-href>
      <conformed-name>Apple Inc.</conformed-name>
      <fiscal-year-end>0926</fiscal-year-end>
      <formerly-names count="1">
        <names>
          <date>2007-01-10</date>
          <name>APPLE COMPUTER INC</name>
        </names>
      </formerly-names>
      <state-location>CA</state-location>
    </company-info>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="8-K" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000001</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-26 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000001 &lt;b&gt;Size:&lt;/b&gt; 1 MB</summary>
      <title>8-K  - Current report</title>
      <updated>{_edgar_stamp(1)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="4" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000002</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000002/0000320193-26-000002-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-25 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000002 &lt;b&gt;Size:&lt;/b&gt; 10 KB</summary>
      <title>4  - Statement of changes in beneficial ownership of securities</title>
      <updated>{_edgar_stamp(20)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="8-K" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000007</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000007/0000320193-26-000007-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-25 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000007 &lt;b&gt;Size:&lt;/b&gt; 120 KB</summary>
      <title>8-K  - Current report</title>
      <updated>{_edgar_stamp(25)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="4" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000008</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000008/0000320193-26-000008-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000008 &lt;b&gt;Size:&lt;/b&gt; 9 KB</summary>
      <title>4  - Statement of changes in beneficial ownership of securities</title>
      <updated>{_edgar_stamp(28)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="144" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0001950047-26-009738</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000195004726009738/0001950047-26-009738-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0001950047-26-009738 &lt;b&gt;Size:&lt;/b&gt; 9 KB</summary>
      <title>144  - Report of proposed sale of securities</title>
      <updated>{_edgar_stamp(30)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="SCHEDULE 13G/A" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000000000-26-000004</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000000000026000004/0000000000-26-000004-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-24 &lt;b&gt;AccNo:&lt;/b&gt; 0000000000-26-000004 &lt;b&gt;Size:&lt;/b&gt; 25 KB</summary>
      <title>SCHEDULE 13G/A [Amend]  - Statement of Beneficial Ownership by Certain Investors</title>
      <updated>{_edgar_stamp(40)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="424B5" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000005</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000005/0000320193-26-000005-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-23 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000005 &lt;b&gt;Size:&lt;/b&gt; 2 MB</summary>
      <title>424B5  - Prospectus [Rule 424(b)(5)]</title>
      <updated>{_edgar_stamp(50)}</updated>
    </entry>
    <entry>
      <category label="form type" scheme="https://www.sec.gov/" term="SD" />
      <content type="text/xml"></content>
      <id>urn:tag:sec.gov,2008:accession-number=0000320193-26-000006</id>
      <link href="https://www.sec.gov/Archives/edgar/data/320193/000032019326000006/0000320193-26-000006-index.htm" rel="alternate" type="text/html" />
      <summary type="html"> &lt;b&gt;Filed:&lt;/b&gt; 2026-09-22 &lt;b&gt;AccNo:&lt;/b&gt; 0000320193-26-000006 &lt;b&gt;Size:&lt;/b&gt; 300 KB</summary>
      <title>SD  - Specialized disclosure report</title>
      <updated>{_edgar_stamp(60)}</updated>
    </entry>
    <id>https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=AAPL&amp;type=&amp;dateb=&amp;owner=include&amp;count=40&amp;output=atom</id>
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=AAPL&amp;type=&amp;dateb=&amp;owner=include&amp;count=40" rel="alternate" type="text/html" />
    <link href="https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&amp;CIK=AAPL&amp;type=&amp;dateb=&amp;owner=include&amp;count=40&amp;output=atom" rel="self" type="application/atom+xml" />
    <title>Apple Inc.  (0000320193)</title>
    <updated>{_edgar_stamp(1)}</updated>
  </feed>"""

# An unknown ticker is HTTP 200 with this HTML page (trimmed from the live response).
SEC_NO_TICKER_HTML = """
<!DOCTYPE HTML PUBLIC "-//W3C//DTD HTML 4.0 Transitional//EN">
<html lang="ENG">
<head>
<title>Company Information: </title>
</head>
<body style="margin: 0">
<div style="margin-left: 10px">
<p><center><h1>No matching Ticker Symbol.</h1></center></p>
</table>"""

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
        if host == "www.benzinga.com" and path == "/api/news":
            assert "application/json" in headers.get("Accept", ""), headers
            requested = (query.get("tickers") or [""])[0].upper()
            return HttpResponse(status=200, body=json.dumps(BENZINGA_API.get(requested, [])).encode())
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
        # aaa-2 is tagged MSFT only, aaa-3 is stale, aaa-4 has no relatedTickers at all (a keyword hit).
        self.assertEqual([item.headline for item in items], ["Apple beats estimates as iPhone demand surges"])
        self.assertEqual(engine.source_status()[0]["items"], 1)
        meta = _engine(FakeHttp(), sources=("yahoo_search",)).load_symbol_news("META")
        self.assertEqual(meta, [])
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
        self.assertEqual(items[0].related_symbols, "AAPL,MSFT")  # "AAPL,AAPL,MSFT" in the feed
        self.assertEqual(items[0].summary, "Key PointsApple keeps growing services.")
        self.assertEqual(items[0].article_id, "https://www.nasdaq.com/articles/apple-buy?time=1790436420")


class BenzingaTests(unittest.TestCase):
    def test_benzinga_attributes_by_publisher_stock_tags(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("benzinga",))
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(
            [item.headline for item in items],
            [
                "What Is Going on With Qualcomm Stock on Friday?",
                "Understanding Apple's Position In Technology Hardware, Storage & Peripherals Industry",
            ],
        )
        self.assertTrue(all(item.source == "Benzinga" and item.via == "Benzinga" for item in items))
        self.assertEqual(items[0].related_symbols, "AAPL,QCOM,TSM")
        self.assertEqual(items[0].article_id, "62002905")
        self.assertEqual(items[0].summary, "Qualcomm stock surged following new Snapdragon 8 Elite chip rollouts.")
        self.assertEqual(items[0].published_at, "2026-09-26T14:00:00.067620+00:00")
        self.assertEqual(items[1].summary, "In today's rapidly changing and highly competitive business world, it is imperative for investors to compare.")
        # The sector story came back in the response but Benzinga did not tag AAPL on it: dropped, never guessed.
        self.assertNotIn("9 Of 11 Sectors", " ".join(item.headline for item in items))
        self.assertEqual(urlsplit(http.calls[0]).path, "/api/news")
        query = parse_qs(urlsplit(http.calls[0]).query)
        self.assertEqual(query["tickers"], ["AAPL"])
        self.assertEqual(query["limit"], ["25"])
        status = engine.source_status()[0]
        self.assertEqual((status["status"], status["items"]), ("ok", 2))
        msft = _engine(FakeHttp(), sources=("benzinga",)).load_symbol_news("MSFT")
        self.assertEqual([item.headline for item in msft], ["Microsoft Raises Dividend"])

    def test_benzinga_unknown_ticker_is_empty_not_an_error(self) -> None:
        engine = _engine(FakeHttp(), sources=("benzinga",))
        self.assertEqual(engine.load_symbol_news("ZZZZQQ"), [])
        status = engine.source_status()[0]
        self.assertEqual((status["status"], status["items"], status["failures"]), ("ok", 0, 0))

    def test_benzinga_is_queried_per_symbol(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("benzinga",))
        rows = engine.load_watchlist_news(["AAPL", "MSFT", "TSLA", "NVDA"])
        calls = [call for call in http.calls if "benzinga.com" in call]
        self.assertEqual(len(calls), 4)
        self.assertEqual({parse_qs(urlsplit(call).query)["tickers"][0] for call in calls}, {"AAPL", "MSFT", "TSLA", "NVDA"})
        self.assertEqual({row["symbol"] for row in rows}, {"AAPL", "MSFT"})
        status = engine.source_status()[0]
        self.assertEqual((status["status"], status["symbols"], status["items"]), ("ok", 4, 3))

    def test_benzinga_blocked_or_broken_is_reported(self) -> None:
        blocked = _engine(FakeHttp(blocked={"www.benzinga.com"}), sources=("benzinga",))
        self.assertEqual(blocked.load_symbol_news("AAPL"), [])
        self.assertEqual(blocked.source_status()[0]["status"], "blocked")
        self.assertIn("HTTP 403", blocked.source_status()[0]["error"])
        limited = _engine(FakeHttp(path_status={"www.benzinga.com/api/news": 429}), sources=("benzinga",))
        self.assertEqual(limited.load_symbol_news("AAPL"), [])
        self.assertEqual(limited.source_status()[0]["status"], "blocked")
        self.assertIn("HTTP 429", limited.source_status()[0]["error"])
        # The old RSS paths answer with a Next.js "Page Not Found" HTML document: an error, not silence.
        html_page = "<!DOCTYPE html><html id=\"__next_error__\"><head><title>Page Not Found - Benzinga</title></head></html>"
        broken = _engine(FakeHttp(bodies={"www.benzinga.com/api/news": html_page}), sources=("benzinga",))
        self.assertEqual(broken.load_symbol_news("AAPL"), [])
        self.assertEqual(broken.source_status()[0]["status"], "error")
        self.assertIn("invalid JSON", broken.source_status()[0]["error"])
        envelope = _engine(FakeHttp(bodies={"www.benzinga.com/api/news": json.dumps({"error": "invalid tickers"})}), sources=("benzinga",))
        self.assertEqual(envelope.load_symbol_news("AAPL"), [])
        self.assertIn("Benzinga error: invalid tickers", envelope.source_status()[0]["error"])

    def test_benzinga_tagged_symbols_reads_stocks_and_tickers(self) -> None:
        tagged = BenzingaSource.tagged_symbols
        self.assertEqual(
            tagged({"stocks": [{"name": "QCOM"}, {"name": "AAPL"}], "tickers": [{"name": "AAPL", "primary": True}, {"name": "TSM"}]}),
            ("QCOM", "AAPL", "TSM"),
        )
        self.assertEqual(tagged({"stocks": [], "tickers": None}), ())
        self.assertEqual(tagged({"stocks": ["aapl", ""], "tags": None}), ("AAPL",))
        self.assertEqual(tagged({}), ())


class SecEdgarTests(unittest.TestCase):
    def test_edgar_keeps_catalyst_forms_and_labels_them_as_filings(self) -> None:
        http = FakeHttp()
        engine = _engine(http, sources=("sec_edgar",), lookback_days=7)
        items = engine.load_symbol_news("AAPL")
        self.assertEqual(
            [item.headline for item in items],
            [
                "8-K filing: Apple Inc. - Current report (2026-09-26, AccNo 0000320193-26-000001)",
                "4 filing: Apple Inc. - Statement of changes in beneficial ownership of securities (2026-09-25, AccNo 0000320193-26-000002)",
                "8-K filing: Apple Inc. - Current report (2026-09-25, AccNo 0000320193-26-000007)",
                "4 filing: Apple Inc. - Statement of changes in beneficial ownership of securities (2026-09-24, AccNo 0000320193-26-000008)",
                "SCHEDULE 13G/A filing: Apple Inc. - Statement of Beneficial Ownership by Certain Investors (2026-09-24, AccNo 0000000000-26-000004)",
                "424B5 filing: Apple Inc. - Prospectus [Rule 424(b)(5)] (2026-09-23, AccNo 0000320193-26-000005)",
            ],
        )
        # Form 144 and SD are in the feed but are not catalyst forms.
        self.assertTrue(all(item.source == "SEC EDGAR" and item.via == "SEC EDGAR" for item in items))
        self.assertTrue(all(item.tags.startswith("Filing") for item in items))
        self.assertEqual(items[0].summary, "Filed: 2026-09-26 AccNo: 0000320193-26-000001 Size: 1 MB")
        self.assertEqual(items[0].published_at, "2026-09-26T14:00:00+00:00")
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
                headline="8-K filing: Apple Inc. - Current report (2026-10-02, AccNo 0000320193-26-000009)",
                url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000009/0000320193-26-000009-index.htm",
                published_at="2026-10-02T13:00:00+00:00",
                article_id="urn:tag:sec.gov,2008:accession-number=0000320193-26-000009",
            )
            self.assertEqual(repository.log_catalysts([later]), 1)

    def test_edgar_headline_carries_description_date_and_accession(self) -> None:
        published = datetime(2026, 9, 26, 13, 0, tzinfo=timezone.utc)
        self.assertEqual(
            SecEdgarSource.headline_for(
                "8-K", "Apple Inc.", published, "urn:tag:sec.gov,2008:accession-number=0000320193-26-000001",
                "Filed: 2026-09-26 AccNo: 0000320193-26-000001 Size: 1 MB",
                "https://www.sec.gov/Archives/edgar/data/320193/000032019326000001/0000320193-26-000001-index.htm",
                "Current report",
            ),
            "8-K filing: Apple Inc. - Current report (2026-09-26, AccNo 0000320193-26-000001)",
        )
        # Real titles are "<form>  - <description>"; the company is not in the entry title.
        self.assertEqual(SecEdgarSource._split_title("8-K  - Current report"), ("8-K", "Current report"))
        self.assertEqual(
            SecEdgarSource._split_title("SCHEDULE 13G/A [Amend]  - Statement of Beneficial Ownership by Certain Investors"),
            ("SCHEDULE 13G/A", "Statement of Beneficial Ownership by Certain Investors"),
        )
        self.assertEqual(SecEdgarSource._split_title("Apple Inc.  (0000320193)"), ("", ""))
        # The feed declares ISO-8859-1; a Latin-1 company name survives.
        latin = '<?xml version="1.0" encoding="ISO-8859-1" ?><feed><title>Soci\u00e9t\u00e9 (0000000001)</title></feed>'
        self.assertIn("Soci\u00e9t\u00e9", SecEdgarSource._decode(HttpResponse(status=200, body=latin.encode("iso-8859-1"))))
        # Accession falls back to the summary, then the URL folder.
        self.assertEqual(SecEdgarSource.accession_for("", "AccNo: 0000320193-26-000002 Size: 9 KB", ""), "0000320193-26-000002")
        self.assertEqual(
            SecEdgarSource.accession_for("", "", "https://www.sec.gov/Archives/edgar/data/320193/000032019326000003/index.htm"),
            "0000320193-26-000003",
        )
        self.assertEqual(SecEdgarSource.headline_for("10-Q", "Apple Inc.", published, "", "", ""), "10-Q filing: Apple Inc. (2026-09-26)")
        # EDGAR's own "Filed:" date wins over the entry timestamp when both are present.
        self.assertEqual(
            SecEdgarSource.headline_for(
                "4", "Apple Inc.", published, "", "Filed: 2026-09-24 AccNo: 0000320193-26-000008 Size: 9 KB", "",
                "Statement of changes in beneficial ownership of securities",
            ),
            "4 filing: Apple Inc. - Statement of changes in beneficial ownership of securities (2026-09-24, AccNo 0000320193-26-000008)",
        )

    def test_edgar_form_filter(self) -> None:
        keep = SecEdgarSource.keep_form
        for form in ("8-K", "8-K/A", "10-Q", "10-K", "6-K", "S-1", "S-3", "424B2", "424B5", "SC 13D", "SC 13G/A",
                     "SCHEDULE 13G", "SCHEDULE 13D/A", "SCHEDULE 13G/A [Amend]", "DEF 14A", "3", "4", "4/A"):
            self.assertTrue(keep(form), form)
        for form in ("UPLOAD", "CORRESP", "13F-HR", "SD", "11-K", "ARS", "144", "", "S-8", "10-K405X", "NPORT-P", "N-30D",
                     "497", "485BPOS", "N-CEN", "24F-2NT", "S-3ASR"):
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
                "What Is Going on With Qualcomm Stock on Friday?",
                "Understanding Apple's Position In Technology Hardware, Storage & Peripherals Industry",
                "8-K filing: Apple Inc. - Current report (2026-09-26, AccNo 0000320193-26-000001)",
                "4 filing: Apple Inc. - Statement of changes in beneficial ownership of securities (2026-09-25, AccNo 0000320193-26-000002)",
                "8-K filing: Apple Inc. - Current report (2026-09-25, AccNo 0000320193-26-000007)",
                "4 filing: Apple Inc. - Statement of changes in beneficial ownership of securities (2026-09-24, AccNo 0000320193-26-000008)",
                "SCHEDULE 13G/A filing: Apple Inc. - Statement of Beneficial Ownership by Certain Investors (2026-09-24, AccNo 0000000000-26-000004)",
                "424B5 filing: Apple Inc. - Prospectus [Rule 424(b)(5)] (2026-09-23, AccNo 0000320193-26-000005)",
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
        self.assertEqual(status["benzinga"]["items"], 2)
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
