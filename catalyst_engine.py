"""Ticker-tagged news scraping for the information-only News Feed.

Every headline stored by this engine comes from a feed where the *publisher*
tagged the ticker, not from a keyword match on the headline:

* Yahoo Finance search API  - JSON, ``relatedTickers`` per article
* Yahoo Finance RSS         - per-ticker headline feed
* Alpaca News (Benzinga)    - JSON, ``symbols`` per article (needs Alpaca keys)
* Finviz quote page         - per-ticker news table
* Nasdaq RSS                - per-ticker feed with ``nasdaq:tickers``
* Benzinga public RSS       - site-wide feed, kept only when the item carries an
                              explicit ``(NASDAQ:AAPL)`` style exchange tag or a
                              ``<category>`` equal to the ticker
* SEC EDGAR Atom            - per-ticker filing feed (8-K, 10-Q, 10-K, insider 3/4 ...)
* Finnhub / Polygon / Alpha Vantage / Tiingo
                            - free API tiers, each enabled only when its key is set;
                              every one returns a publisher-supplied ticker list

Google News keyword search is still available as an opt-in source because
it is the only one that covers instruments Yahoo does not tag well, but it is
labelled as a headline match and is off by default.

Every source runs in isolation.  A blocked or broken source is reported in
``source_status()`` instead of silently thinning the feed, so the UI can say
exactly where each headline came from and which source failed.
"""

from __future__ import annotations

import gzip
import html
import http.cookiejar
import json
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Callable
from zoneinfo import ZoneInfo


POSITIVE_PATTERNS = re.compile(
    r"earnings beat|beats estimates|beat estimates|tops estimates|raises guidance|guidance raised|"
    r"raises outlook|upgrade[sd]?\b|price target raised|raises price target|hikes price target|"
    r"partnership|contract|award|fda approval|approves|buyback|ai deal|launch|expansion|"
    r"record revenue|record quarter|surges|soars|jumps|rallies|outperform|"
    r"acquisition|to acquire|dividend increase|raises dividend",
    re.IGNORECASE,
)
NEGATIVE_PATTERNS = re.compile(
    r"downgrade[sd]?\b|guidance cut|cuts guidance|lowers guidance|cuts outlook|offering|dilution|"
    r"investigation|lawsuit|class action|missed earnings|misses earnings|misses estimates|"
    r"sec probe|recall|layoffs|plunges|tumbles|slumps|sinks|falls short|"
    r"price target cut|lowers price target|cuts price target|delisting|bankruptcy|halted",
    re.IGNORECASE,
)

TICKER_ALIASES = {
    "AI": ["c3.ai", "c3 ai"],
    "AAPL": ["apple", "iphone", "mac"],
    "AMD": ["advanced micro devices", "amd"],
    "AMZN": ["amazon", "aws"],
    "COIN": ["coinbase", "coinbase global"],
    # DRAM is a memory-sector instrument.  Its useful catalyst context is
    # reported under the component companies and the memory market, not always
    # under the literal DRAM ticker.
    "DRAM": ["memory chips", "memory chip", "micron", "sandisk", "sanDisk", "sk hynix"],
    "GOOG": ["google", "alphabet"],
    "GOOGL": ["google", "alphabet"],
    "META": ["meta", "facebook", "instagram"],
    "MSFT": ["microsoft", "azure"],
    "NVDA": ["nvidia", "cuda"],
    "PLTR": ["palantir"],
    "TSLA": ["tesla", "elon musk"],
}

DEFAULT_SOURCES = ("yahoo_search", "yahoo_rss", "alpaca", "benzinga", "finviz", "nasdaq", "sec_edgar")
# Free API tiers that need a key.  They are enabled only when the key is configured
# and are never reported as blocked/failed when it is missing.
KEY_SOURCES = ("finnhub", "polygon", "alphavantage", "tiingo")
ALL_SOURCES = DEFAULT_SOURCES + KEY_SOURCES + ("google_news",)
DEFAULT_CONTACT_EMAIL = "noreply@example.com"

BROWSER_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
EASTERN = ZoneInfo("America/New_York")
TRACKING_QUERY_KEYS = ("utm_", "guccounter", "guce_referrer", "ref", "src", "fbclid", "gclid", ".tsrc")


@dataclass(slots=True)
class CatalystItem:
    symbol: str
    headline: str
    source: str
    url: str
    published_at: str
    score: int
    sentiment: str
    tags: str
    via: str = ""
    summary: str = ""
    related_symbols: str = ""
    article_id: str = ""


@dataclass(slots=True)
class RawArticle:
    """One article as reported by a single source, before scoring/dedupe."""

    headline: str
    url: str
    published_at: datetime
    publisher: str
    summary: str = ""
    related_symbols: tuple[str, ...] = ()
    article_id: str = ""
    tags: tuple[str, ...] = ()


class SourceBlocked(Exception):
    """The remote site rejected the request (403/429/401) - not a parse issue."""


@dataclass(slots=True)
class HttpResponse:
    status: int
    body: bytes
    headers: dict[str, str] = field(default_factory=dict)

    def text(self) -> str:
        body = self.body
        if body[:2] == b"\x1f\x8b":
            try:
                body = gzip.decompress(body)
            except OSError:
                pass
        return body.decode("utf-8", errors="ignore")


HttpGetter = Callable[[str, dict[str, str], float], HttpResponse]


def _urllib_http_get(cookie_jar: http.cookiejar.CookieJar) -> HttpGetter:
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def getter(url: str, headers: dict[str, str], timeout: float) -> HttpResponse:
        request = urllib.request.Request(url, headers=headers)
        try:
            with opener.open(request, timeout=timeout) as response:
                return HttpResponse(
                    status=int(response.status),
                    body=response.read(),
                    headers={key.lower(): value for key, value in response.headers.items()},
                )
        except urllib.error.HTTPError as exc:
            body = b""
            try:
                body = exc.read()
            except Exception:
                pass
            return HttpResponse(status=int(exc.code), body=body, headers={key.lower(): value for key, value in exc.headers.items()} if exc.headers else {})

    return getter


def canonical_url(url: str) -> str:
    """Strip tracking parameters and fragments so the same article dedupes."""
    raw = str(url or "").strip()
    if not raw:
        return ""
    try:
        parsed = urllib.parse.urlsplit(raw)
    except ValueError:
        return raw.lower()
    query_pairs = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=False)
        if not any(key.lower().startswith(prefix) or key.lower() == prefix.strip(".") for prefix in TRACKING_QUERY_KEYS)
    ]
    path = parsed.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((
        parsed.scheme.lower() or "https",
        parsed.netloc.lower().removeprefix("www."),
        path,
        urllib.parse.urlencode(sorted(query_pairs)),
        "",
    ))


def normalize_headline(headline: str) -> str:
    text = html.unescape(str(headline or "")).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _strip_html(text: str) -> str:
    cleaned = re.sub(r"<[^>]+>", " ", html.unescape(str(text or "")))
    return " ".join(cleaned.split())


def _parse_rfc822(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _parse_epoch(value) -> datetime | None:
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    if seconds <= 0:
        return None
    if seconds > 10_000_000_000:  # milliseconds
        seconds /= 1000.0
    return datetime.fromtimestamp(seconds, tz=timezone.utc)


def _parse_iso(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


# --------------------------------------------------------------------------- sources


class NewsSource:
    name = "base"
    label = "Base"
    homepage = ""
    ticker_tagged = True
    # Environment variable that holds the API key for key-based sources ("" = no key needed).
    requires_key = ""

    def __init__(self, engine: "CatalystEngine") -> None:
        self.engine = engine

    @property
    def api_key(self) -> str:
        return self.engine.api_key(self.name)

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:  # pragma: no cover - abstract
        raise NotImplementedError

    # helpers -----------------------------------------------------------
    def _get(self, url: str, headers: dict[str, str] | None = None) -> HttpResponse:
        request_headers = {
            "User-Agent": BROWSER_USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept": "*/*",
        }
        if headers:
            request_headers.update(headers)
        response = self.engine.http_get(url, request_headers, self.engine.timeout_seconds)
        if response.status in (401, 403, 429):
            raise SourceBlocked(f"HTTP {response.status}")
        if response.status >= 400:
            raise OSError(f"HTTP {response.status}")
        return response

    @staticmethod
    def _rss_items(text: str) -> list[ET.Element]:
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            raise OSError(f"invalid RSS: {exc}") from exc
        return root.findall(".//item")

    @staticmethod
    def _json(response: HttpResponse):
        try:
            return json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise OSError(f"invalid JSON: {exc}") from exc


class YahooSearchSource(NewsSource):
    name = "yahoo_search"
    label = "Yahoo Finance"
    homepage = "https://finance.yahoo.com"

    SEARCH_URL = "https://query1.finance.yahoo.com/v1/finance/search"
    CRUMB_URL = "https://query1.finance.yahoo.com/v1/test/getcrumb"
    COOKIE_URL = "https://fc.yahoo.com"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        params = {
            "q": symbol,
            "quotesCount": 0,
            "newsCount": max(limit, 1),
            "listsCount": 0,
            "enableFuzzyQuery": "false",
            "quotesQueryId": "tss_match_phrase_query",
            "newsQueryId": "news_cie_vespa",
            "enableNavLinks": "false",
            "enableEnhancedTrivialQuery": "false",
        }
        headers = {"Accept": "application/json"}
        url = f"{self.SEARCH_URL}?{urllib.parse.urlencode(params)}"
        try:
            response = self._get(url, headers)
        except SourceBlocked:
            crumb = self.engine.yahoo_crumb(self)
            if not crumb:
                raise
            response = self._get(f"{url}&crumb={urllib.parse.quote(crumb)}", headers)
        try:
            payload = json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise OSError(f"invalid JSON: {exc}") from exc
        articles: list[RawArticle] = []
        for item in payload.get("news") or []:
            if not isinstance(item, dict):
                continue
            related = tuple(str(ticker).upper() for ticker in item.get("relatedTickers") or [] if str(ticker).strip())
            if related and symbol not in related:
                continue
            published = _parse_epoch(item.get("providerPublishTime"))
            headline = _strip_html(item.get("title"))
            link = str(item.get("link") or "").strip()
            if not headline or not link or published is None:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=str(item.get("publisher") or "Yahoo Finance").strip(),
                    summary=_strip_html(item.get("summary") or ""),
                    related_symbols=related,
                    article_id=str(item.get("uuid") or ""),
                )
            )
        return articles


class YahooRssSource(NewsSource):
    name = "yahoo_rss"
    label = "Yahoo Finance RSS"
    homepage = "https://finance.yahoo.com"

    FEED_URL = "https://feeds.finance.yahoo.com/rss/2.0/headline"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        url = f"{self.FEED_URL}?{urllib.parse.urlencode({'s': symbol, 'region': 'US', 'lang': 'en-US'})}"
        response = self._get(url, {"Accept": "application/rss+xml, application/xml, text/xml, */*"})
        articles: list[RawArticle] = []
        for item in self._rss_items(response.text())[: limit * 2]:
            headline = _strip_html(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            published = _parse_rfc822(item.findtext("pubDate"))
            if not headline or not link or published is None:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher="Yahoo Finance",
                    summary=_strip_html(item.findtext("description") or ""),
                    related_symbols=(symbol,),
                    article_id=(item.findtext("guid") or "").strip(),
                )
            )
        return articles


class AlpacaNewsSource(NewsSource):
    name = "alpaca"
    label = "Alpaca News (Benzinga)"
    homepage = "https://alpaca.markets"

    NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        key, secret = self.engine.alpaca_credentials
        if not key or not secret:
            raise SourceBlocked("Alpaca API keys are not configured")
        start = (datetime.now(timezone.utc) - timedelta(days=self.engine.lookback_days)).replace(microsecond=0)
        params = {
            "symbols": symbol,
            "limit": max(1, min(int(limit), 50)),
            "sort": "desc",
            "include_content": "false",
            "exclude_contentless": "false",
            "start": start.isoformat().replace("+00:00", "Z"),
        }
        url = f"{self.NEWS_URL}?{urllib.parse.urlencode(params)}"
        response = self._get(
            url,
            {"Accept": "application/json", "APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret},
        )
        try:
            payload = json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise OSError(f"invalid JSON: {exc}") from exc
        articles: list[RawArticle] = []
        for item in payload.get("news") or []:
            if not isinstance(item, dict):
                continue
            related = tuple(str(ticker).upper() for ticker in item.get("symbols") or [] if str(ticker).strip())
            if related and symbol not in related:
                continue
            published = _parse_iso(item.get("created_at") or item.get("updated_at"))
            headline = _strip_html(item.get("headline"))
            link = str(item.get("url") or "").strip()
            if not headline or not link or published is None:
                continue
            publisher = str(item.get("source") or "Benzinga").strip()
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=publisher.title() if publisher.islower() else publisher,
                    summary=_strip_html(item.get("summary") or ""),
                    related_symbols=related,
                    article_id=str(item.get("id") or ""),
                )
            )
        return articles


class FinvizSource(NewsSource):
    name = "finviz"
    label = "Finviz"
    homepage = "https://finviz.com"

    QUOTE_URL = "https://finviz.com/quote.ashx"
    TABLE_RE = re.compile(r'<table[^>]*id="news-table"[^>]*>(.*?)</table>', re.IGNORECASE | re.DOTALL)
    ROW_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.IGNORECASE | re.DOTALL)
    DATE_CELL_RE = re.compile(r"<td[^>]*>\s*([A-Za-z]{3}-\d{2}-\d{2}\s+\d{1,2}:\d{2}[AP]M|Today\s+\d{1,2}:\d{2}[AP]M|\d{1,2}:\d{2}[AP]M)\s*</td>", re.IGNORECASE | re.DOTALL)
    LINK_RE = re.compile(r'<a\b(?=[^>]*class="[^"]*tab-link-news[^"]*")[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    LINK_ALT_RE = re.compile(r'<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*tab-link-news[^"]*"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    PUBLISHER_RE = re.compile(r"<span[^>]*>\s*\(([^)]+)\)\s*</span>", re.IGNORECASE | re.DOTALL)

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        url = f"{self.QUOTE_URL}?{urllib.parse.urlencode({'t': symbol, 'p': 'd'})}"
        response = self._get(url, {"Accept": "text/html,application/xhtml+xml"})
        return self.parse(response.text(), symbol, limit)

    def parse(self, page: str, symbol: str, limit: int, now: datetime | None = None) -> list[RawArticle]:
        table = self.TABLE_RE.search(page)
        if not table:
            if "news-table" in page:
                raise OSError("Finviz news table changed shape")
            raise OSError("Finviz news table not found")
        current_date = (now or datetime.now(EASTERN)).astimezone(EASTERN).date()
        articles: list[RawArticle] = []
        for row in self.ROW_RE.findall(table.group(1)):
            date_match = self.DATE_CELL_RE.search(row)
            link_match = self.LINK_RE.search(row) or self.LINK_ALT_RE.search(row)
            if not date_match or not link_match:
                continue
            stamp = " ".join(date_match.group(1).split())
            parts = stamp.split(" ")
            try:
                if len(parts) == 2:
                    if parts[0].lower() == "today":
                        clock = datetime.strptime(parts[1].upper(), "%I:%M%p")
                    else:
                        current_date = datetime.strptime(parts[0], "%b-%d-%y").date()
                        clock = datetime.strptime(parts[1].upper(), "%I:%M%p")
                else:
                    clock = datetime.strptime(parts[0].upper(), "%I:%M%p")
            except ValueError:
                continue
            published = datetime.combine(current_date, clock.time(), tzinfo=EASTERN).astimezone(timezone.utc)
            href = html.unescape(link_match.group(1)).strip()
            if href.startswith("/"):
                href = f"https://finviz.com{href}"
            headline = _strip_html(link_match.group(2))
            publisher_match = self.PUBLISHER_RE.search(row)
            publisher = publisher_match.group(1).strip() if publisher_match else "Finviz"
            if not headline or not href:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=href,
                    published_at=published,
                    publisher=publisher,
                    related_symbols=(symbol,),
                )
            )
            if len(articles) >= limit:
                break
        return articles


class NasdaqRssSource(NewsSource):
    name = "nasdaq"
    label = "Nasdaq"
    homepage = "https://www.nasdaq.com"

    FEED_URL = "https://www.nasdaq.com/feed/rssoutbound"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        url = f"{self.FEED_URL}?{urllib.parse.urlencode({'symbol': symbol})}"
        response = self._get(url, {"Accept": "application/rss+xml, application/xml, text/xml, */*"})
        articles: list[RawArticle] = []
        for item in self._rss_items(response.text())[: limit * 2]:
            tickers_text = ""
            for child in item:
                if child.tag.endswith("tickers") and child.text:
                    tickers_text = child.text
                    break
            related = tuple(token.strip().upper() for token in tickers_text.split(",") if token.strip())
            if related and symbol not in related:
                continue
            headline = _strip_html(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            published = _parse_rfc822(item.findtext("pubDate"))
            if not headline or not link or published is None:
                continue
            creator = ""
            for child in item:
                if child.tag.endswith("creator") and child.text:
                    creator = child.text.strip()
                    break
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=creator or "Nasdaq",
                    summary=_strip_html(item.findtext("description") or ""),
                    related_symbols=related or (symbol,),
                    article_id=(item.findtext("guid") or "").strip(),
                )
            )
        return articles


class GoogleNewsSource(NewsSource):
    """Keyword search; kept opt-in because ticker attribution is a headline match."""

    name = "google_news"
    label = "Google News (headline match)"
    homepage = "https://news.google.com"
    ticker_tagged = False

    FEED_URL = "https://news.google.com/rss/search"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        articles: list[RawArticle] = []
        for query in self._queries_for_symbol(symbol):
            url = f"{self.FEED_URL}?{urllib.parse.urlencode({'q': query, 'hl': 'en-US', 'gl': 'US', 'ceid': 'US:en'})}"
            response = self._get(url, {"Accept": "application/rss+xml, application/xml, text/xml, */*"})
            for item in self._rss_items(response.text())[: limit * 3]:
                headline = _strip_html(item.findtext("title"))
                link = (item.findtext("link") or "").strip()
                published = _parse_rfc822(item.findtext("pubDate"))
                if not headline or not link or published is None or not self._matches_symbol(symbol, headline):
                    continue
                source_node = item.find("source")
                publisher = source_node.text.strip() if source_node is not None and source_node.text else "Google News"
                articles.append(
                    RawArticle(
                        headline=headline,
                        url=link,
                        published_at=published,
                        publisher=publisher,
                        related_symbols=(symbol,),
                    )
                )
        return articles

    def _queries_for_symbol(self, symbol: str) -> list[str]:
        aliases = TICKER_ALIASES.get(symbol.upper(), [])
        company_hint = f" OR {' OR '.join(aliases)}" if aliases else ""
        company_name = aliases[0] if aliases else symbol
        queries = [f"{symbol} stock{company_hint} when:7d", f'"{company_name}" site:barrons.com when:7d']
        if symbol.upper() == "DRAM":
            queries.append('("memory stocks" OR Micron OR SanDisk) site:benzinga.com when:7d')
        return queries

    @staticmethod
    def _matches_symbol(symbol: str, headline: str) -> bool:
        upper = symbol.upper()
        aliases = TICKER_ALIASES.get(upper, [])
        haystack = headline.lower()
        if any(alias.lower() in haystack for alias in aliases):
            return True
        return bool(re.search(rf"\b{re.escape(upper)}\b", headline, re.IGNORECASE))


def _local_tag(element: ET.Element) -> str:
    tag = element.tag
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _child_text(element: ET.Element, name: str) -> str:
    for child in element:
        if _local_tag(child) == name and child.text:
            return child.text
    return ""


class BenzingaRssSource(NewsSource):
    """Benzinga public RSS.  The feed is site-wide (not per ticker), so a headline
    is attributed to a symbol only when Benzinga itself tagged it: an explicit
    ``(NASDAQ:AAPL)`` / ``NYSE: AAPL`` exchange tag in the title, description or
    content, or a ``<category>`` element equal to the ticker.  The feed is fetched
    once per engine run and filtered per symbol."""

    name = "benzinga"
    label = "Benzinga"
    homepage = "https://www.benzinga.com"

    FEED_URLS = ("https://www.benzinga.com/feed", "https://www.benzinga.com/news/feed")
    EXCHANGES = (
        "NASDAQ", "NYSE", "NYSEAMERICAN", "NYSEARCA", "AMEX", "ARCA", "OTC", "OTCQB", "OTCQX", "OTCPK",
        "BATS", "CBOE", "TSX", "TSXV",
    )
    EXCHANGE_TAG_RE = re.compile(
        r"(?<![A-Za-z0-9])(?:" + "|".join(EXCHANGES) + r")\s*:\s*([A-Z][A-Z0-9]{0,9}(?:[.\-][A-Z0-9]{1,4})?)(?![A-Za-z0-9])"
    )

    @dataclass(slots=True)
    class FeedItem:
        article: RawArticle
        tagged: frozenset[str]

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        feed: list[BenzingaRssSource.FeedItem] = self.engine.shared_fetch(self.name, self._fetch_feed)
        articles: list[RawArticle] = []
        for entry in feed:
            if symbol not in entry.tagged:
                continue
            articles.append(entry.article)
            if len(articles) >= limit:
                break
        return articles

    def _fetch_feed(self) -> list["BenzingaRssSource.FeedItem"]:
        items: list[BenzingaRssSource.FeedItem] = []
        seen: set[str] = set()
        errors: list[Exception] = []
        fetched = 0
        for url in self.FEED_URLS:
            try:
                response = self._get(url, {"Accept": "application/rss+xml, application/xml, text/xml, */*"})
                parsed = self.parse(response.text())
            except SourceBlocked as exc:
                if not fetched:
                    # Nothing fetched yet: stop hammering a host that is rejecting us.
                    raise
                # One feed already succeeded; a 403/429 on the other one is a per-URL
                # failure, not a reason to throw away the good items for the whole run.
                errors.append(exc)
                continue
            except Exception as exc:
                errors.append(exc)
                continue
            fetched += 1
            for entry in parsed:
                key = canonical_url(entry.article.url) or entry.article.article_id or normalize_headline(entry.article.headline)
                if key in seen:
                    continue
                seen.add(key)
                items.append(entry)
        if not items and errors:
            # Everything failed: report "blocked" if any feed rejected us, else the first error.
            raise next((error for error in errors if isinstance(error, SourceBlocked)), errors[0])
        return items

    def parse(self, text: str) -> list["BenzingaRssSource.FeedItem"]:
        entries: list[BenzingaRssSource.FeedItem] = []
        for item in self._rss_items(text):
            title = _strip_html(item.findtext("title"))
            link = (item.findtext("link") or "").strip()
            published = _parse_rfc822(item.findtext("pubDate"))
            if not title or not link or published is None:
                continue
            description = item.findtext("description") or ""
            encoded = _child_text(item, "encoded")
            categories = tuple(
                ((child.text or "").strip(), str(child.attrib.get("domain") or ""))
                for child in item
                if _local_tag(child) == "category" and (child.text or "").strip()
            )
            tagged = set(self.exchange_tags(f"{title}\n{html.unescape(description)}\n{html.unescape(encoded)}"))
            tagged.update(category.upper() for category, domain in categories if self._looks_like_ticker(category, domain))
            if not tagged:
                continue
            creator = _child_text(item, "creator").strip()
            entries.append(
                self.FeedItem(
                    article=RawArticle(
                        headline=title,
                        url=link,
                        published_at=published,
                        publisher="Benzinga",
                        summary=_strip_html(description)[:600],
                        related_symbols=tuple(sorted(tagged)),
                        article_id=(item.findtext("guid") or "").strip() or (f"benzinga:{creator}:{link}" if creator else ""),
                    ),
                    tagged=frozenset(tagged),
                )
            )
        return entries

    @classmethod
    def exchange_tags(cls, text: str) -> tuple[str, ...]:
        """Tickers Benzinga tagged in the text with an explicit exchange prefix."""
        return tuple(dict.fromkeys(match.group(1).upper() for match in cls.EXCHANGE_TAG_RE.finditer(str(text or ""))))

    # Benzinga section/topic names that are all-caps but collide with real tickers
    # (AI = C3.ai, IPO = Renaissance IPO ETF, FDA, ESG, ...).  A bare <category> equal
    # to one of these is a section label, never a ticker tag.
    SECTION_CATEGORIES = frozenset({
        "AI", "IPO", "IPOS", "FDA", "ESG", "SPAC", "SPACS", "ETF", "ETFS", "REIT", "REITS", "EARNINGS",
        "M&A", "SEC", "FOMC", "FED", "GDP", "CPI", "PPI", "EPS", "CEO", "CFO", "EV", "EVS", "NFT", "NFTS",
        "DEFI", "BTC", "ETH", "CRYPTO", "US", "USA", "UK", "EU", "ECB", "OPEC", "GLP-1", "NEWS", "TECH",
    })
    TICKER_CATEGORY_RE = re.compile(r"[A-Z][A-Z0-9]{0,4}(?:[.\-][A-Z0-9]{1,4})?")
    TICKER_DOMAIN_RE = re.compile(r"ticker|quote|symbol|stock", re.IGNORECASE)

    @classmethod
    def _looks_like_ticker(cls, category: str, domain: str = "") -> bool:
        """A <category> counts as a ticker tag ONLY when Benzinga marks it as one:
        the element's ``domain`` attribute must name a ticker/quote taxonomy and
        the token must be shaped like a ticker (all-caps, at most five characters
        plus an optional share-class suffix, e.g. BRK.B, GOOGL).  A bare category
        with no such domain is a topic label (AI, IPO, EV, ...) and is never used
        for attribution, so a topic that collides with a ticker cannot leak
        unrelated stories into that symbol's feed."""
        token = str(category or "").strip()
        if not token or not domain or not cls.TICKER_DOMAIN_RE.search(domain):
            return False
        if token.upper() in cls.SECTION_CATEGORIES and not re.fullmatch(r"[A-Z][A-Z0-9]{0,4}", token):
            return False
        return bool(cls.TICKER_CATEGORY_RE.fullmatch(token.upper()))


class SecEdgarSource(NewsSource):
    """SEC EDGAR company filings Atom feed, keyed by ticker.  Filings are catalysts
    in their own right, so every kept entry is tagged ``Filing``."""

    name = "sec_edgar"
    label = "SEC EDGAR"
    homepage = "https://www.sec.gov"

    FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
    KEEP_FORMS = ("8-K", "10-Q", "10-K", "6-K", "S-1", "S-3", "424B", "SC 13D", "SC 13G", "DEF 14A", "3", "4")
    TITLE_RE = re.compile(r"^\s*(?P<form>.+?)\s+-\s+(?P<company>.+?)\s*\((?P<cik>\d{10})\)\s*\((?P<role>[^)]*)\)\s*$")
    NO_TICKER_RE = re.compile(r"No matching Ticker Symbol", re.IGNORECASE)
    ACCESSION_RE = re.compile(r"accession-number=(\d{10}-\d{2}-\d{6})")
    ACCNO_RE = re.compile(r"AccNo:\s*(\d{10}-\d{2}-\d{6})")
    URL_ACCESSION_RE = re.compile(r"/(\d{10}-\d{2}-\d{6})-index|/(\d{18})/")
    FILING_ITEMS_RE = re.compile(r"Size:\s*[\d.,]+\s*[KMG]?B\s*(?P<items>.+)$", re.IGNORECASE)
    FILED_RE = re.compile(r"Filed:\s*(\d{4}-\d{2}-\d{2})")

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        params = {
            "action": "getcompany",
            "CIK": symbol,
            "type": "",
            "dateb": "",
            "owner": "include",
            "count": max(40, min(int(limit) * 2, 100)),
            "output": "atom",
        }
        url = f"{self.FEED_URL}?{urllib.parse.urlencode(params)}"
        response = self._get(
            url,
            {
                "User-Agent": self.engine.sec_user_agent,
                "Accept": "application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
            },
        )
        return self.parse(response.text(), symbol, limit)

    def parse(self, text: str, symbol: str, limit: int) -> list[RawArticle]:
        stripped = text.lstrip()
        if self.NO_TICKER_RE.search(text):
            raise OSError(f"SEC EDGAR has no company for ticker {symbol} (No matching Ticker Symbol)")
        if not stripped.startswith("<"):
            raise OSError("SEC EDGAR returned a non-XML response")
        try:
            root = ET.fromstring(stripped)
        except ET.ParseError as exc:
            if "<html" in stripped[:512].lower():
                raise OSError("SEC EDGAR returned an HTML page instead of the Atom feed") from exc
            raise OSError(f"invalid Atom feed: {exc}") from exc
        if _local_tag(root).lower() != "feed":
            raise OSError(f"SEC EDGAR returned <{_local_tag(root)}> instead of the Atom feed")
        company_name = ""
        for element in root.iter():
            if _local_tag(element) == "conformed-name" and element.text:
                company_name = " ".join(element.text.split())
                break
        articles: list[RawArticle] = []
        for entry in root.iter():
            if _local_tag(entry) != "entry":
                continue
            title = " ".join((_child_text(entry, "title") or "").split())
            form, company = self._split_title(title)
            category_form = ""
            link = ""
            for child in entry:
                local = _local_tag(child)
                if local == "category" and not category_form:
                    category_form = str(child.attrib.get("term") or "").strip()
                elif local == "link" and not link:
                    if child.attrib.get("rel", "alternate") == "alternate" or not link:
                        link = str(child.attrib.get("href") or "").strip()
            form = form or category_form
            if not form or not self.keep_form(form) or not link:
                continue
            published = _parse_iso(_child_text(entry, "updated").strip())
            if published is None:
                continue
            label_company = company or company_name or symbol
            summary = _strip_html(_child_text(entry, "summary"))
            article_id = (_child_text(entry, "id") or "").strip()
            articles.append(
                RawArticle(
                    headline=self.headline_for(form, label_company, published, article_id, summary, link),
                    url=link,
                    published_at=published,
                    publisher="SEC EDGAR",
                    summary=summary[:600],
                    related_symbols=(symbol,),
                    article_id=article_id,
                    tags=("Filing",),
                )
            )
            if len(articles) >= limit:
                break
        return articles

    @classmethod
    def headline_for(cls, form: str, company: str, published: datetime, article_id: str, summary: str, link: str) -> str:
        """One headline per filing.  A company files the same form many times
        (every 8-K, every insider Form 4), and both the engine merge and the
        repository dedupe by normalized headline, so the headline carries the
        filed date (EDGAR's ``Filed:`` in the summary, else the entry date),
        the 8-K item text when EDGAR lists it, and the accession number, which
        is unique per filing."""
        accession = cls.accession_for(article_id, summary, link)
        filed = cls.FILED_RE.search(summary or "")
        parts = [filed.group(1) if filed else published.astimezone(timezone.utc).date().isoformat()]
        items_match = cls.FILING_ITEMS_RE.search(summary or "")
        items = " ".join(items_match.group("items").split()) if items_match else ""
        if items:
            parts.append(items[:160])
        if accession:
            parts.append(f"AccNo {accession}")
        return f"{form} filing: {company} ({', '.join(parts)})"[:400]

    @classmethod
    def accession_for(cls, article_id: str, summary: str, link: str) -> str:
        for pattern, text in ((cls.ACCESSION_RE, article_id), (cls.ACCNO_RE, summary)):
            match = pattern.search(text or "")
            if match:
                return match.group(1)
        match = cls.URL_ACCESSION_RE.search(link or "")
        if match:
            if match.group(1):
                return match.group(1)
            digits = match.group(2)
            return f"{digits[:10]}-{digits[10:12]}-{digits[12:]}"
        return ""

    @classmethod
    def _split_title(cls, title: str) -> tuple[str, str]:
        match = cls.TITLE_RE.match(title)
        if match:
            return match.group("form").strip(), " ".join(match.group("company").split())
        if " - " in title:
            form, _, rest = title.partition(" - ")
            return form.strip(), re.sub(r"\s*\(\d{10}\).*$", "", rest).strip()
        return "", ""

    @classmethod
    def keep_form(cls, form: str) -> bool:
        base = str(form or "").strip().upper()
        base = re.sub(r"/A$", "", base)  # amendments of a kept form are kept
        for keep in cls.KEEP_FORMS:
            if keep == "424B":
                if base.startswith("424B"):
                    return True
            elif base == keep:
                return True
        return False


class FinnhubSource(NewsSource):
    name = "finnhub"
    label = "Finnhub"
    homepage = "https://finnhub.io"
    requires_key = "FINNHUB_API_KEY"

    NEWS_URL = "https://finnhub.io/api/v1/company-news"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        key = self.api_key
        if not key:
            raise SourceBlocked(f"{self.requires_key} is not configured")
        today = datetime.now(timezone.utc).date()
        params = {
            "symbol": symbol,
            "from": (today - timedelta(days=self.engine.lookback_days)).isoformat(),
            "to": today.isoformat(),
            "token": key,
        }
        response = self._get(f"{self.NEWS_URL}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        payload = self._json(response)
        if isinstance(payload, dict):
            if payload.get("error"):
                raise OSError(f"Finnhub error: {payload.get('error')}")
            payload = payload.get("news") or []
        articles: list[RawArticle] = []
        for item in payload or []:
            if not isinstance(item, dict):
                continue
            related = tuple(token.strip().upper() for token in str(item.get("related") or "").split(",") if token.strip())
            if related and symbol not in related:
                continue
            published = _parse_epoch(item.get("datetime"))
            headline = _strip_html(item.get("headline"))
            link = str(item.get("url") or "").strip()
            if not headline or not link or published is None:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=str(item.get("source") or "Finnhub").strip(),
                    summary=_strip_html(item.get("summary") or ""),
                    related_symbols=related or (symbol,),
                    article_id=str(item.get("id") or ""),
                )
            )
            if len(articles) >= limit:
                break
        return articles


class PolygonSource(NewsSource):
    name = "polygon"
    label = "Polygon"
    homepage = "https://polygon.io"
    requires_key = "POLYGON_API_KEY"

    NEWS_URL = "https://api.polygon.io/v2/reference/news"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        key = self.api_key
        if not key:
            raise SourceBlocked(f"{self.requires_key} is not configured")
        params = {
            "ticker": symbol,
            "limit": max(1, min(int(limit), 1000)),
            "order": "desc",
            "sort": "published_utc",
            "apiKey": key,
        }
        response = self._get(f"{self.NEWS_URL}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        payload = self._json(response)
        if not isinstance(payload, dict):
            raise OSError("Polygon returned an unexpected payload")
        if str(payload.get("status") or "").upper() == "ERROR":
            raise OSError(f"Polygon error: {payload.get('error') or payload.get('message') or 'unknown'}")
        articles: list[RawArticle] = []
        for item in payload.get("results") or []:
            if not isinstance(item, dict):
                continue
            related = tuple(str(ticker).upper() for ticker in item.get("tickers") or [] if str(ticker).strip())
            if symbol not in related:
                continue
            published = _parse_iso(item.get("published_utc"))
            headline = _strip_html(item.get("title"))
            link = str(item.get("article_url") or "").strip()
            if not headline or not link or published is None:
                continue
            publisher_node = item.get("publisher") if isinstance(item.get("publisher"), dict) else {}
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=str(publisher_node.get("name") or "Polygon").strip(),
                    summary=_strip_html(item.get("description") or ""),
                    related_symbols=related,
                    article_id=str(item.get("id") or ""),
                )
            )
        return articles


class AlphaVantageSource(NewsSource):
    """Alpha Vantage NEWS_SENTIMENT.  Attribution uses the publisher-supplied
    ``ticker_sentiment[].relevance_score`` (>= MIN_RELEVANCE), never a headline match."""

    name = "alphavantage"
    label = "Alpha Vantage"
    homepage = "https://www.alphavantage.co"
    requires_key = "ALPHA_VANTAGE_API_KEY"

    NEWS_URL = "https://www.alphavantage.co/query"
    MIN_RELEVANCE = 0.2

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        key = self.api_key
        if not key:
            raise SourceBlocked(f"{self.requires_key} is not configured")
        since = datetime.now(timezone.utc) - timedelta(days=self.engine.lookback_days)
        params = {
            "function": "NEWS_SENTIMENT",
            "tickers": symbol,
            "limit": max(1, min(int(limit), 1000)),
            "sort": "LATEST",
            "time_from": since.strftime("%Y%m%dT%H%M"),
            "apikey": key,
        }
        response = self._get(f"{self.NEWS_URL}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        payload = self._json(response)
        if not isinstance(payload, dict):
            raise OSError("Alpha Vantage returned an unexpected payload")
        for note_key in ("Note", "Information"):
            if payload.get(note_key):
                raise SourceBlocked(f"rate limited: {str(payload[note_key])[:160]}")
        if payload.get("Error Message"):
            raise OSError(f"Alpha Vantage error: {payload['Error Message']}")
        articles: list[RawArticle] = []
        for item in payload.get("feed") or []:
            if not isinstance(item, dict):
                continue
            related: list[str] = []
            relevant = False
            for entry in item.get("ticker_sentiment") or []:
                if not isinstance(entry, dict):
                    continue
                ticker = str(entry.get("ticker") or "").strip().upper()
                if not ticker:
                    continue
                try:
                    relevance = float(entry.get("relevance_score") or 0.0)
                except (TypeError, ValueError):
                    relevance = 0.0
                if relevance >= self.MIN_RELEVANCE:
                    related.append(ticker)
                    if ticker == symbol:
                        relevant = True
            if not relevant:
                continue
            published = self._parse_time(item.get("time_published"))
            headline = _strip_html(item.get("title"))
            link = str(item.get("url") or "").strip()
            if not headline or not link or published is None:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=str(item.get("source") or "Alpha Vantage").strip(),
                    summary=_strip_html(item.get("summary") or ""),
                    related_symbols=tuple(sorted(set(related))),
                )
            )
            if len(articles) >= limit:
                break
        return articles

    @staticmethod
    def _parse_time(value) -> datetime | None:
        raw = str(value or "").strip()
        for pattern in ("%Y%m%dT%H%M%S", "%Y%m%dT%H%M"):
            try:
                return datetime.strptime(raw, pattern).replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return _parse_iso(raw)


class TiingoSource(NewsSource):
    name = "tiingo"
    label = "Tiingo"
    homepage = "https://www.tiingo.com"
    requires_key = "TIINGO_API_KEY"

    NEWS_URL = "https://api.tiingo.com/tiingo/news"

    def fetch(self, symbol: str, limit: int) -> list[RawArticle]:
        key = self.api_key
        if not key:
            raise SourceBlocked(f"{self.requires_key} is not configured")
        params = {
            "tickers": symbol.lower(),
            "limit": max(1, min(int(limit), 1000)),
            "sortBy": "publishedDate",
            "token": key,
        }
        response = self._get(f"{self.NEWS_URL}?{urllib.parse.urlencode(params)}", {"Accept": "application/json"})
        payload = self._json(response)
        if isinstance(payload, dict):
            if payload.get("detail") or payload.get("error"):
                raise OSError(f"Tiingo error: {payload.get('detail') or payload.get('error')}")
            raise OSError("Tiingo returned an unexpected payload")
        articles: list[RawArticle] = []
        for item in payload or []:
            if not isinstance(item, dict):
                continue
            related = tuple(str(ticker).upper() for ticker in item.get("tickers") or [] if str(ticker).strip())
            if symbol not in related:
                continue
            published = _parse_iso(item.get("publishedDate"))
            headline = _strip_html(item.get("title"))
            link = str(item.get("url") or "").strip()
            if not headline or not link or published is None:
                continue
            articles.append(
                RawArticle(
                    headline=headline,
                    url=link,
                    published_at=published,
                    publisher=str(item.get("source") or "Tiingo").strip(),
                    summary=_strip_html(item.get("description") or ""),
                    related_symbols=related,
                    article_id=str(item.get("id") or ""),
                )
            )
        return articles


SOURCE_CLASSES: dict[str, type[NewsSource]] = {
    YahooSearchSource.name: YahooSearchSource,
    YahooRssSource.name: YahooRssSource,
    AlpacaNewsSource.name: AlpacaNewsSource,
    FinvizSource.name: FinvizSource,
    NasdaqRssSource.name: NasdaqRssSource,
    BenzingaRssSource.name: BenzingaRssSource,
    SecEdgarSource.name: SecEdgarSource,
    FinnhubSource.name: FinnhubSource,
    PolygonSource.name: PolygonSource,
    AlphaVantageSource.name: AlphaVantageSource,
    TiingoSource.name: TiingoSource,
    GoogleNewsSource.name: GoogleNewsSource,
}


def parse_source_list(raw: str | None) -> tuple[str, ...]:
    if raw is None or not str(raw).strip():
        return DEFAULT_SOURCES
    tokens = [token.strip().lower() for token in str(raw).replace(";", ",").split(",")]
    selected = tuple(dict.fromkeys(token for token in tokens if token in SOURCE_CLASSES))
    return selected or DEFAULT_SOURCES


# --------------------------------------------------------------------------- engine


class CatalystEngine:
    def __init__(
        self,
        timeout_seconds: int = 8,
        max_workers: int = 8,
        cache_ttl_seconds: int = 300,
        sources: tuple[str, ...] | list[str] | None = None,
        per_symbol_limit: int = 25,
        lookback_days: int = 7,
        alpaca_credentials: tuple[str, str] | None = None,
        http_get: HttpGetter | None = None,
        api_keys: dict[str, str] | None = None,
        contact_email: str = "",
        auto_enable_key_sources: bool = True,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, int(max_workers))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self.per_symbol_limit = max(1, int(per_symbol_limit))
        self.lookback_days = max(1, int(lookback_days))
        self.alpaca_credentials = tuple(str(part or "").strip() for part in (alpaca_credentials or ("", "")))[:2]
        if len(self.alpaca_credentials) < 2:
            self.alpaca_credentials = ("", "")
        # Source name -> API key for the key-based free tiers (finnhub, polygon, ...).
        self.api_keys: dict[str, str] = {
            str(name).strip().lower(): str(key or "").strip() for name, key in (api_keys or {}).items()
        }
        self.contact_email = str(contact_email or "").strip() or DEFAULT_CONTACT_EMAIL
        self._cookie_jar = http.cookiejar.CookieJar()
        self.http_get: HttpGetter = http_get or _urllib_http_get(self._cookie_jar)
        requested = tuple(dict.fromkeys(str(name).strip().lower() for name in (sources or DEFAULT_SOURCES)))
        self.requested_sources: tuple[str, ...] = tuple(name for name in requested if name in SOURCE_CLASSES)
        selected = list(self.requested_sources)
        if auto_enable_key_sources:
            # A configured key switches its source on even when NEWS_SOURCES does not list it.
            selected.extend(name for name in KEY_SOURCES if name not in selected and self.api_keys.get(name))
        self.sources: list[NewsSource] = []
        self.skipped_sources: dict[str, str] = {}
        for name in selected:
            source = SOURCE_CLASSES[name](self)
            if source.requires_key and not self.api_keys.get(name):
                # Missing key: silently skipped, listed by available_sources(), never "blocked".
                self.skipped_sources[name] = f"{source.requires_key} is not set"
                continue
            self.sources.append(source)
        self._cache: dict[str, tuple[float, list[CatalystItem]]] = {}
        self._cache_lock = threading.Lock()
        self._status_lock = threading.Lock()
        self._status: dict[str, dict] = {source.name: self._blank_status(source) for source in self.sources}
        self._yahoo_crumb: str | None = None
        self._yahoo_crumb_lock = threading.Lock()
        self._yahoo_crumb_checked = False
        # Site-wide feeds (Benzinga) are fetched once per run and shared across symbols.
        self._shared_lock = threading.Lock()
        self._shared_run = 0
        self._active_runs = 0  # load_watchlist_news runs in flight (their workers share one run id)
        self._shared: dict[str, tuple[int, float, object, BaseException | None]] = {}

    # ----------------------------------------------------------------- keys / shared feeds
    def api_key(self, source_name: str) -> str:
        return self.api_keys.get(str(source_name or "").strip().lower(), "")

    @property
    def sec_user_agent(self) -> str:
        # SEC asks for a descriptive User-Agent with a contact address.
        return f"AgenticAI-Trading/1.0 (contact: {self.contact_email})"

    def shared_fetch(self, key: str, loader: Callable[[], object]) -> object:
        """Run ``loader`` once per engine run and hand every symbol the same
        parsed result.  ``load_watchlist_news`` starts one run for the whole
        watchlist; a direct ``load_symbol_news`` call outside a watchlist run
        starts its own run, so it reuses the feed only while the cache TTL is
        fresh.  Failures are cached the same way, so a blocked site-wide feed is
        reported per symbol without being re-requested per symbol, and a
        transient failure is retried once the TTL expires."""
        with self._shared_lock:
            entry = self._shared.get(key)
            now = time.monotonic()
            if entry is not None:
                run, stamp, value, error = entry
                if run == self._shared_run or (self.cache_ttl_seconds and now - stamp < self.cache_ttl_seconds):
                    if error is not None:
                        raise error
                    return value
            try:
                value = loader()
            except Exception as exc:
                self._shared[key] = (self._shared_run, now, None, exc)
                raise
            self._shared[key] = (self._shared_run, now, value, None)
            return value

    def _begin_run(self) -> None:
        with self._shared_lock:
            self._shared_run += 1

    def _in_watchlist_run(self) -> bool:
        with self._shared_lock:
            return self._active_runs > 0

    def available_sources(self) -> list[dict]:
        """Every known source with whether it is active for this engine and why not.
        Key-based sources without a key are listed here instead of failing."""
        enabled = {source.name for source in self.sources}
        rows: list[dict] = []
        for name in ALL_SOURCES:
            cls = SOURCE_CLASSES[name]
            reason = ""
            if name not in enabled:
                if name in self.skipped_sources:
                    reason = self.skipped_sources[name]
                elif cls.requires_key:
                    reason = f"{cls.requires_key} is not set"
                elif name == GoogleNewsSource.name:
                    reason = "opt-in headline match; add google_news to NEWS_SOURCES"
                else:
                    reason = "not listed in NEWS_SOURCES"
            rows.append({
                "name": name,
                "label": cls.label,
                "homepage": cls.homepage,
                "tickerTagged": bool(cls.ticker_tagged),
                "requiresKey": cls.requires_key,
                "enabled": name in enabled,
                "reason": reason,
            })
        return rows

    # ----------------------------------------------------------------- scoring
    def score_headline(self, headline: str, summary: str = "") -> tuple[int, str, list[str]]:
        score = 1
        tags: list[str] = []
        haystack = f"{headline}\n{summary or ''}"
        if POSITIVE_PATTERNS.search(headline):
            score = 3
            tags.append("Positive Catalyst")
        elif POSITIVE_PATTERNS.search(haystack):
            score = 2
            tags.append("Positive Catalyst")
        if NEGATIVE_PATTERNS.search(headline):
            score = 0
            tags.append("Risk Headline")
        if re.search(r"\bai\b|artificial intelligence", haystack, re.IGNORECASE):
            tags.append("AI")
        if re.search(r"earnings|guidance|estimates|revenue|profit|\beps\b|quarter results|q[1-4] results", haystack, re.IGNORECASE):
            tags.append("Earnings")
        if re.search(r"contract|partnership|deal|acqui", haystack, re.IGNORECASE):
            tags.append("Deal")
        if re.search(r"analyst|price target|upgrade|downgrade|rating", haystack, re.IGNORECASE):
            tags.append("Analyst")

        sentiment = "Strong" if score >= 3 else "Positive" if score == 2 else "Neutral" if score == 1 else "Negative"
        return score, sentiment, tags

    # ----------------------------------------------------------------- status
    @staticmethod
    def _blank_status(source: NewsSource) -> dict:
        return {
            "name": source.name,
            "label": source.label,
            "homepage": source.homepage,
            "tickerTagged": bool(source.ticker_tagged),
            "status": "idle",
            "items": 0,
            "symbols": 0,
            "failures": 0,
            "error": "",
            "lastRunAt": None,
        }

    def source_status(self) -> list[dict]:
        with self._status_lock:
            return [dict(self._status[source.name]) for source in self.sources]

    def _reset_status(self) -> None:
        with self._status_lock:
            for source in self.sources:
                self._status[source.name] = self._blank_status(source)

    def _record(self, source: NewsSource, items: int | None, error: str | None, blocked: bool = False) -> None:
        with self._status_lock:
            entry = self._status.setdefault(source.name, self._blank_status(source))
            entry["symbols"] += 1
            entry["lastRunAt"] = datetime.now(timezone.utc).isoformat()
            if error is None:
                entry["items"] += int(items or 0)
                if entry["status"] in {"idle", "ok"} or entry["items"]:
                    entry["status"] = "ok" if entry["failures"] == 0 else "partial"
                return
            entry["failures"] += 1
            entry["error"] = error
            if entry["items"]:
                entry["status"] = "partial"
            else:
                entry["status"] = "blocked" if blocked else "error"

    # ----------------------------------------------------------------- yahoo crumb
    def yahoo_crumb(self, source: NewsSource) -> str | None:
        with self._yahoo_crumb_lock:
            if self._yahoo_crumb_checked:
                return self._yahoo_crumb
            self._yahoo_crumb_checked = True
            headers = {"User-Agent": BROWSER_USER_AGENT, "Accept": "*/*"}
            try:
                self.http_get(YahooSearchSource.COOKIE_URL, headers, self.timeout_seconds)
                response = self.http_get(YahooSearchSource.CRUMB_URL, headers, self.timeout_seconds)
            except Exception:
                return None
            crumb = response.text().strip() if response.status == 200 else ""
            if crumb and "<" not in crumb and len(crumb) < 64:
                self._yahoo_crumb = crumb
            return self._yahoo_crumb

    # ----------------------------------------------------------------- loading
    def load_watchlist_news(self, symbols: list[str], limit: int = 40) -> list[dict]:
        normalized_symbols = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))[:limit]
        rows: list[CatalystItem] = []
        if not normalized_symbols:
            return []
        self._reset_status()
        self._begin_run()
        with self._shared_lock:
            self._active_runs += 1
        try:
            worker_count = min(self.max_workers, len(normalized_symbols))
            with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="news") as executor:
                futures = {executor.submit(self.load_symbol_news, symbol, self.per_symbol_limit): symbol for symbol in normalized_symbols}
                for future in as_completed(futures):
                    try:
                        rows.extend(future.result())
                    except Exception:
                        continue
        finally:
            with self._shared_lock:
                self._active_runs -= 1
        return sorted(
            [asdict(row) for row in rows],
            key=lambda item: item.get("published_at") or "",
            reverse=True,
        )

    def load_symbol_news(self, symbol: str, limit: int | None = None) -> list[CatalystItem]:
        normalized_symbol = str(symbol or "").strip().upper()
        effective_limit = int(limit or self.per_symbol_limit)
        cache_key = f"{normalized_symbol}:{effective_limit}"
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_ttl_seconds:
                return list(cached[1])

        if not self._in_watchlist_run():
            # A direct call is its own run: site-wide feeds are reused only within the TTL.
            self._begin_run()
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.lookback_days)
        collected: list[tuple[NewsSource, RawArticle]] = []
        for source in self.sources:
            try:
                articles = source.fetch(normalized_symbol, effective_limit)
            except SourceBlocked as exc:
                self._record(source, None, f"{source.label} blocked the request ({exc}).", blocked=True)
                continue
            except Exception as exc:  # network, parse, or shape changes
                self._record(source, None, f"{source.label} unavailable: {type(exc).__name__}: {exc}".strip(), blocked=False)
                continue
            fresh = [article for article in articles if article.published_at >= cutoff]
            self._record(source, len(fresh), None)
            collected.extend((source, article) for article in fresh)

        items = self._merge(normalized_symbol, collected)
        items.sort(key=lambda item: item.published_at, reverse=True)
        items = items[:effective_limit]
        with self._cache_lock:
            self._cache[cache_key] = (now, list(items))
        return items

    def _merge(self, symbol: str, collected: list[tuple[NewsSource, RawArticle]]) -> list[CatalystItem]:
        by_url: dict[str, CatalystItem] = {}
        by_headline: dict[str, CatalystItem] = {}
        items: list[CatalystItem] = []
        for source, article in collected:
            url_key = canonical_url(article.url)
            headline_key = normalize_headline(article.headline)
            existing = by_url.get(url_key) if url_key else None
            if existing is None and headline_key:
                existing = by_headline.get(headline_key)
            if existing is not None:
                # Enrich the first copy rather than storing the story twice.
                if not existing.summary and article.summary:
                    existing.summary = article.summary[:600]
                if existing.source in {"Yahoo Finance", "Finviz", "Nasdaq"} and article.publisher not in {"Yahoo Finance", "Finviz", "Nasdaq"}:
                    existing.source = article.publisher
                merged_related = set(filter(None, existing.related_symbols.split(","))) | set(article.related_symbols)
                existing.related_symbols = ",".join(sorted(merged_related))
                continue
            score, sentiment, tags = self.score_headline(article.headline, article.summary)
            # Publisher-declared tags (e.g. "Filing" for SEC EDGAR) come first.
            tags = list(dict.fromkeys([*article.tags, *tags]))
            item = CatalystItem(
                symbol=symbol,
                headline=article.headline[:400],
                source=article.publisher or source.label,
                url=article.url,
                published_at=article.published_at.astimezone(timezone.utc).isoformat(),
                score=score,
                sentiment=sentiment,
                tags=", ".join(tags) if tags else "News",
                via=source.label,
                summary=(article.summary or "")[:600],
                related_symbols=",".join(sorted(set(article.related_symbols) | {symbol})),
                article_id=article.article_id[:120],
            )
            items.append(item)
            if url_key:
                by_url[url_key] = item
            if headline_key:
                by_headline[headline_key] = item
        return items
