from __future__ import annotations

import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from datetime import datetime, timezone


POSITIVE_PATTERNS = re.compile(
    r"earnings beat|raises guidance|guidance raised|upgrade|price target raised|"
    r"partnership|contract|award|fda approval|buyback|ai deal|launch|expansion",
    re.IGNORECASE,
)
NEGATIVE_PATTERNS = re.compile(
    r"downgrade|guidance cut|offering|dilution|investigation|lawsuit|"
    r"missed earnings|misses earnings|cuts guidance|sec probe",
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


class CatalystEngine:
    def __init__(self, timeout_seconds: int = 8, max_workers: int = 8, cache_ttl_seconds: int = 300) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_workers = max(1, int(max_workers))
        self.cache_ttl_seconds = max(0, int(cache_ttl_seconds))
        self._cache: dict[str, tuple[float, list[CatalystItem]]] = {}
        self._cache_lock = threading.Lock()

    def score_headline(self, headline: str) -> tuple[int, str, list[str]]:
        score = 1
        tags: list[str] = []
        if POSITIVE_PATTERNS.search(headline):
            score = 3
            tags.append("Positive Catalyst")
        if NEGATIVE_PATTERNS.search(headline):
            score = 0
            tags.append("Risk Headline")
        if re.search(r"\bai\b|artificial intelligence", headline, re.IGNORECASE):
            tags.append("AI")
        if re.search(r"earnings|guidance", headline, re.IGNORECASE):
            tags.append("Earnings")
        if re.search(r"contract|partnership|deal", headline, re.IGNORECASE):
            tags.append("Deal")

        sentiment = "Strong" if score >= 3 else "Positive" if score == 2 else "Neutral" if score == 1 else "Negative"
        return score, sentiment, tags

    def load_watchlist_news(self, symbols: list[str], limit: int = 40) -> list[dict]:
        normalized_symbols = list(dict.fromkeys(str(symbol or "").strip().upper() for symbol in symbols if str(symbol or "").strip()))[:limit]
        rows: list[CatalystItem] = []
        if not normalized_symbols:
            return []
        worker_count = min(self.max_workers, len(normalized_symbols))
        with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="news") as executor:
            futures = {executor.submit(self.load_symbol_news, symbol, 3): symbol for symbol in normalized_symbols}
            for future in as_completed(futures):
                try:
                    rows.extend(future.result())
                except Exception:
                    continue
        return sorted(
            [asdict(row) for row in rows],
            key=lambda item: item.get("published_at") or "",
            reverse=True,
        )[:80]

    def load_symbol_news(self, symbol: str, limit: int = 5) -> list[CatalystItem]:
        normalized_symbol = str(symbol or "").strip().upper()
        cache_key = f"{normalized_symbol}:{int(limit)}"
        now = time.monotonic()
        with self._cache_lock:
            cached = self._cache.get(cache_key)
            if cached and now - cached[0] < self.cache_ttl_seconds:
                return list(cached[1])

        items: list[CatalystItem] = []
        seen_links: set[str] = set()
        # The broad query finds ticker-specific coverage. The additional
        # Barron's query allows a broad-market headline that names the company
        # (for example, Coinbase) to appear in the same ticker feed.
        for query in self._queries_for_symbol(normalized_symbol):
            url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl=en-US&gl=US&ceid=US:en"
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "AgenticAI-Trading/1.0"})
                with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                    root = ET.fromstring(response.read().decode("utf-8", errors="ignore"))
            except (OSError, ET.ParseError):
                continue

            for item in root.findall(".//item")[: limit * 3]:
                headline = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                if not headline or not self._matches_symbol(normalized_symbol, headline):
                    continue
                item_key = link or headline.lower()
                if item_key in seen_links:
                    continue
                seen_links.add(item_key)
                source_node = item.find("source")
                source = source_node.text.strip() if source_node is not None and source_node.text else "Google News"
                score, sentiment, tags = self.score_headline(headline)
                items.append(
                    CatalystItem(
                        symbol=normalized_symbol,
                        headline=headline,
                        source=source,
                        url=link,
                        published_at=self._normalize_date(item.findtext("pubDate")),
                        score=score,
                        sentiment=sentiment,
                        tags=", ".join(tags) if tags else "News",
                    )
                )
        items.sort(key=lambda item: item.published_at, reverse=True)
        items = items[:limit]
        with self._cache_lock:
            self._cache[cache_key] = (now, list(items))
        return items

    def _query_for_symbol(self, symbol: str) -> str:
        aliases = TICKER_ALIASES.get(symbol.upper(), [])
        company_hint = f" OR {' OR '.join(aliases)}" if aliases else ""
        return f"{symbol} stock{company_hint} when:7d"

    def _queries_for_symbol(self, symbol: str) -> list[str]:
        aliases = TICKER_ALIASES.get(symbol.upper(), [])
        company_name = aliases[0] if aliases else symbol
        queries = [self._query_for_symbol(symbol), f'"{company_name}" site:barrons.com when:7d']
        if symbol.upper() == "DRAM":
            queries.append('("memory stocks" OR Micron OR SanDisk) site:benzinga.com when:7d')
        return queries

    def _matches_symbol(self, symbol: str, headline: str) -> bool:
        upper = symbol.upper()
        aliases = TICKER_ALIASES.get(upper, [])
        haystack = headline.lower()
        if any(alias.lower() in haystack for alias in aliases):
            return True
        return bool(re.search(rf"\b{re.escape(upper)}\b", headline, re.IGNORECASE))

    def _normalize_date(self, value: str | None) -> str:
        if not value:
            return datetime.now(timezone.utc).isoformat()
        try:
            from email.utils import parsedate_to_datetime

            parsed = parsedate_to_datetime(value)
            return parsed.astimezone(timezone.utc).isoformat()
        except Exception:
            return datetime.now(timezone.utc).isoformat()
