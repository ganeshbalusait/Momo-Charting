"""MomX headlines in near real time (2026-10-02, his ask "check other free news ... yes do it").

The 8-source store (news_feed_store.py) reads 60 tickers every 2 minutes, so a story
waits its turn: measured 2026-10-02, the median headline reached the app 38 minutes
after it was published (Benzinga 32, SEC filings 30). This module PUSHES stories
into the same table (catalyst_items, via repository.log_catalysts - same dedupe by
URL / headline) as soon as they appear, for every ticker at once:

* Alpaca news websocket (wss://stream.data.alpaca.markets/v1beta1/news, free with
  the Alpaca account): every Benzinga story the moment it is published, tickers
  tagged by Benzinga. Several key pairs are configured and some are dead, so each
  is tried in turn until one authenticates.
* Press-release wires, polled every WIRE_POLL_SECONDS: PR Newswire and GlobeNewswire
  RSS (Business Wire publishes only per-industry feeds - the one tried was "Networks"
  news in six languages - so it is left to the Benzinga stream). A release is kept only when it names a listed ticker
  "(NASDAQ: XXX)" / "(NYSE: XXX)" - that tag is the company's own.
* SEC EDGAR "latest filings" Atom feed (action=getcurrent), polled every
  EDGAR_POLL_SECONDS: a filing shows within a minute of acceptance. CIK -> ticker
  from SEC's company_tickers.json; same forms and headline shape as the per-ticker
  SEC source (SecEdgarSource), so both dedupe to one row.

When a universe is known (the MomX Watchlist the auto-refresh loop reads) only its
tickers are stored. Never raises into the server; status() says what each feed did
and how late its stories arrived.
"""
from __future__ import annotations

import asyncio
import html
import json
import re
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from collections import deque
from datetime import datetime, timezone
from typing import Any, Callable, Iterable

from catalyst_engine import SecEdgarSource, _parse_iso, _parse_rfc822, _strip_html

ALPACA_NEWS_WS = "wss://stream.data.alpaca.markets/v1beta1/news"
WIRE_FEEDS = (
    ("PR Newswire", "https://www.prnewswire.com/rss/news-releases-list.rss"),
    ("GlobeNewswire",
     "https://rss.globenewswire.com/RssFeed/orgclass/1/feedTitle/GlobeNewswire%20-%20News%20about%20Public%20Companies"),
)
# Market news sites that do not tag tickers (added 2026-10-02 on his ask, after a
# measured gap: CNBC carried 42 stories naming his tickers that no tagged source had).
# Each headline is matched to Watchlist tickers by news_names (strict: company named
# in the HEADLINE); stories naming more than MEDIA_MAX_TICKERS are round-ups, skipped.
MEDIA_FEEDS = (
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_marketpulse"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines"),
    ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_bulletins"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10001147"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839069"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=20910258"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=10000664"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=19854910"),
    ("CNBC", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=15839135"),
    ("Investing.com", "https://www.investing.com/rss/news_25.rss"),
    ("Investing.com", "https://www.investing.com/rss/news_356.rss"),
    ("Investing.com", "https://www.investing.com/rss/news_1062.rss"),
    ("Investing.com", "https://www.investing.com/rss/stock_Stocks.rss"),
)
MEDIA_POLL_SECONDS = 120
MEDIA_MAX_TICKERS = 3
MEDIA_MAX_AGE_HOURS = 72
EDGAR_CURRENT = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=&company=&dateb="
                 "&owner=include&count=100&start={start}&output=atom")
EDGAR_TICKERS = "https://www.sec.gov/files/company_tickers.json"
WIRE_POLL_SECONDS = 60
EDGAR_POLL_SECONDS = 30
EDGAR_MAX_PAGES = 3
BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/128 Safari/537.36"

# "(NASDAQ: ABCD)", "(NYSE: X)", "(Nasdaq: ABC; TSX: ABC)", "(NYSE American: XYZ)". OTC names have no options.
WIRE_TICKER_RE = re.compile(
    r"\b(?:NASDAQ|Nasdaq|NasdaqGS|NasdaqGM|NasdaqCM|NYSE American|NYSE Arca|NYSE MKT|NYSE|Cboe|CBOE)\s*:\s*"
    r"([A-Z]{1,5}(?:\.[A-Z])?)\b"
)
# Law-firm solicitations ("... Investors Have Opportunity to Lead ... Securities Fraud
# Lawsuit") flood the wires and Benzinga; they are ads, not company news.
LAW_FIRM_AD_RE = re.compile(
    r"securities fraud|class action|investors have opportunity|shareholder alert|investor alert|lead plaintiff"
    r"|investors who lost|shareholders who lost|investigation on behalf of|encourages .{0,40}investors|deadline alert",
    re.IGNORECASE,
)
EDGAR_TITLE_RE = re.compile(r"^\s*(?P<form>.+?)\s+-\s+(?P<name>.+?)\s+\((?P<cik>\d{10})\)\s+\((?P<role>[^)]+)\)\s*$")
# getcurrent titles carry no description; these match the per-ticker feed's wording
# so Jev reads a live Form 4 the same way it reads the stored one.
FORM_DESCRIPTIONS = {
    "8-K": "Current report",
    "6-K": "Report of foreign issuer",
    "10-Q": "Quarterly report",
    "10-K": "Annual report",
    "3": "Initial statement of beneficial ownership of securities",
    "4": "Statement of changes in beneficial ownership of securities",
    "S-1": "General form for registration of securities",
    "S-3": "Registration statement",
    "DEF 14A": "Definitive proxy statement",
}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _item(symbol: str, headline: str, source: str, url: str, published: datetime, via: str,
          summary: str, related: Iterable[str], article_id: str, score: Callable, tags: Iterable[str] = ()) -> dict:
    points, sentiment, found = score(headline, summary)
    all_tags = list(dict.fromkeys([*tags, *found]))
    return {
        "symbol": symbol, "headline": headline[:400], "source": source, "url": url,
        "published_at": published.astimezone(timezone.utc).isoformat(), "score": points,
        "sentiment": sentiment, "tags": ", ".join(all_tags) if all_tags else "News", "via": via,
        "summary": (summary or "")[:600], "related_symbols": ",".join(sorted(set(related) | {symbol})),
        "article_id": str(article_id or "")[:120],
    }


def alpaca_items(message: dict, score: Callable) -> list[dict]:
    """One Alpaca stream news message -> one stored row per tagged ticker."""
    if not isinstance(message, dict) or message.get("T") != "n":
        return []
    headline = _strip_html(message.get("headline") or "")
    url = str(message.get("url") or "").strip()
    published = _parse_iso(message.get("created_at") or message.get("updated_at"))
    symbols = [str(s).upper().strip() for s in message.get("symbols") or [] if str(s).strip()]
    if not headline or not url or published is None or not symbols or LAW_FIRM_AD_RE.search(headline):
        return []
    publisher = str(message.get("source") or "benzinga").strip()
    publisher = publisher.title() if publisher.islower() else publisher
    summary = _strip_html(message.get("summary") or "")
    return [_item(s, headline, publisher, url, published, "Alpaca live", summary, symbols,
                  str(message.get("id") or ""), score) for s in symbols]


def wire_items(text: str, publisher: str, score: Callable) -> list[dict]:
    """A press-release RSS page -> rows for the releases that name a listed ticker."""
    try:
        root = ET.fromstring(text.lstrip())
    except ET.ParseError:
        return []
    out: list[dict] = []
    for node in root.iter("item"):
        headline = _strip_html(node.findtext("title") or "")
        url = (node.findtext("link") or "").strip()
        published = _parse_rfc822(node.findtext("pubDate"))
        description = _strip_html(node.findtext("description") or "")
        if not headline or not url or published is None or LAW_FIRM_AD_RE.search(headline):
            continue
        tickers = list(dict.fromkeys(WIRE_TICKER_RE.findall(html.unescape(headline + " " + description))))
        tickers = [t for t in tickers if "." not in t]
        for symbol in tickers:
            out.append(_item(symbol, headline, publisher, url, published, publisher + " live", description,
                             tickers, node.findtext("guid") or url, score, tags=("Press release",)))
    return out


def media_items(text: str, publisher: str, match: Callable[[str], list[str]], score: Callable) -> list[dict]:
    """A market-news RSS page -> rows for the stories whose HEADLINE names a ticker (``match``)."""
    try:
        root = ET.fromstring(text.lstrip())
    except ET.ParseError:
        return []
    out: list[dict] = []
    for node in root.iter("item"):
        headline = _strip_html(node.findtext("title") or "")
        url = (node.findtext("link") or "").strip()
        published = _parse_rfc822(node.findtext("pubDate"))
        if not headline or not url or published is None or LAW_FIRM_AD_RE.search(headline):
            continue
        if (_now() - published).total_seconds() > MEDIA_MAX_AGE_HOURS * 3600:   # section feeds keep year-old stories
            continue
        tickers = match(headline)
        if not tickers or len(tickers) > MEDIA_MAX_TICKERS:
            continue
        summary = _strip_html(node.findtext("description") or "")
        for symbol in tickers:
            out.append(_item(symbol, headline, publisher, url, published, publisher + " live", summary,
                             tickers, node.findtext("guid") or url, score))
    return out


def edgar_items(text: str, cik_tickers: dict[str, list[str]], score: Callable) -> tuple[list[dict], datetime | None]:
    """A getcurrent Atom page -> filing rows for listed companies (+ the page's oldest time)."""
    try:
        root = ET.fromstring(text.lstrip())
    except ET.ParseError:
        return [], None
    out: list[dict] = []
    oldest: datetime | None = None
    for entry in root.iter():
        if entry.tag.rsplit("}", 1)[-1] != "entry":
            continue
        get = {c.tag.rsplit("}", 1)[-1]: c for c in entry}
        title = " ".join(((get.get("title").text if get.get("title") is not None else "") or "").split())
        published = _parse_iso((get.get("updated").text if get.get("updated") is not None else "") or "")
        if published is not None and (oldest is None or published < oldest):
            oldest = published
        match = EDGAR_TITLE_RE.match(title)
        link = (get["link"].attrib.get("href") or "").strip() if "link" in get else ""
        if not match or published is None or not link:
            continue
        if match.group("role").strip().lower() == "reporting":   # the insider, not the company
            continue
        form = SecEdgarSource.AMEND_SUFFIX_RE.sub("", match.group("form")).strip()
        if not SecEdgarSource.keep_form(form):
            continue
        tickers = cik_tickers.get(str(int(match.group("cik"))), [])
        if not tickers:
            continue
        summary = _strip_html((get.get("summary").text if get.get("summary") is not None else "") or "")
        article_id = ((get.get("id").text if get.get("id") is not None else "") or "").strip()
        base = re.sub(r"/A$", "", form.upper())
        description = FORM_DESCRIPTIONS.get(base) or ("Prospectus" if base.startswith("424B") else
                                                     "Beneficial ownership report" if "13" in base else "")
        company = " ".join(match.group("name").split())
        headline = SecEdgarSource.headline_for(form, company, published, article_id, summary, link, description)
        for symbol in tickers:
            out.append(_item(symbol, headline, "SEC EDGAR", link, published, "SEC EDGAR live", summary,
                             tickers, article_id, score, tags=("Filing",)))
    return out, oldest


class LiveNewsIngest:
    """Three background feeds writing straight into the headline store."""

    def __init__(self, repository: Any, score: Callable, credential_pool: Callable[[], list[tuple[str, str]]],
                 *, universe: Callable[[], Iterable[str]] | None = None, sec_user_agent: str = BROWSER_UA,
                 http_get: Callable[[str, dict, float], str] | None = None) -> None:
        self.repository = repository
        self.score = score
        self.credential_pool = credential_pool
        self.universe = universe or (lambda: ())
        self.sec_user_agent = sec_user_agent
        self.http_get = http_get or self._http_get
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._cik_tickers: dict[str, list[str]] = {}
        self._cik_loaded = 0.0
        self.feeds: dict[str, dict] = {
            name: {"status": "idle", "received": 0, "stored": 0, "lastAt": None, "error": None, "lags": deque(maxlen=300)}
            for name in ("alpaca", "wires", "edgar", "media")
        }

    # ------------------------------------------------------------------ public
    def start(self) -> None:
        with self._lock:
            if any(t.is_alive() for t in self._threads):
                return
            self._threads = [
                threading.Thread(target=self._run_alpaca, name="news-live-alpaca", daemon=True),
                threading.Thread(target=self._run_poll, args=("wires", WIRE_POLL_SECONDS, self._poll_wires),
                                 name="news-live-wires", daemon=True),
                threading.Thread(target=self._run_poll, args=("edgar", EDGAR_POLL_SECONDS, self._poll_edgar),
                                 name="news-live-edgar", daemon=True),
                threading.Thread(target=self._run_poll, args=("media", MEDIA_POLL_SECONDS, self._poll_media),
                                 name="news-live-media", daemon=True),
            ]
            for thread in self._threads:
                thread.start()

    def status(self) -> dict:
        out = {}
        with self._lock:
            for name, feed in self.feeds.items():
                lags = sorted(feed["lags"])
                out[name] = {k: v for k, v in feed.items() if k != "lags" and not k.startswith("_")}
                out[name]["medianLagSec"] = round(lags[len(lags) // 2], 1) if lags else None
        return out

    # ------------------------------------------------------------------ store
    def store(self, feed: str, items: list[dict], part: str = "") -> int:
        """Store the stories this feed has not delivered before; returns how many rows were new."""
        wanted = {str(s).upper() for s in (self.universe() or ())}
        if wanted:
            items = [it for it in items if it["symbol"] in wanted]
        with self._lock:
            f = self.feeds[feed]
            seen = f.setdefault("_seen", set())
            order = f.setdefault("_order", deque())
            fresh = [it for it in items if (it["symbol"], it["url"]) not in seen]
            for it in fresh:
                seen.add((it["symbol"], it["url"]))
                order.append((it["symbol"], it["url"]))
            while len(order) > 5000:
                seen.discard(order.popleft())
            # Each wire site has its own first read (one may fail the first poll).
            warmed = f.setdefault("_warmed", set())
            warm = part in warmed
            warmed.add(part)
            f["_warm"] = True
        stored = 0
        if fresh:
            try:
                stored = int(self.repository.log_catalysts(fresh) or 0)
            except Exception as exc:  # noqa: BLE001
                self._note(feed, error=f"store: {type(exc).__name__}")
                return 0
        now = _now()
        with self._lock:
            f["received"] += len(fresh)
            f["stored"] += stored
            f["lastAt"] = now.isoformat(timespec="seconds")
            # Arrival delay = publish time -> this feed handed it over. The first
            # poll of a polled feed is a backlog, not a measurement.
            if warm or feed == "alpaca":
                for it in fresh:
                    published = _parse_iso(it["published_at"])
                    if published is not None:
                        f["lags"].append(max(0.0, (now - published).total_seconds()))
        return stored

    def _note(self, feed: str, **fields) -> None:
        with self._lock:
            self.feeds[feed].update(fields)

    # ------------------------------------------------------------------ alpaca
    def _run_alpaca(self) -> None:
        delay = 5.0
        while True:
            try:
                asyncio.run(self._alpaca_session())
                delay = 5.0
            except Exception as exc:  # noqa: BLE001
                self._note("alpaca", status="reconnecting", error=f"{type(exc).__name__}: {str(exc)[:120]}")
            time.sleep(delay)
            delay = min(delay * 2, 300.0)

    async def _alpaca_session(self) -> None:
        import websockets
        pool = list(self.credential_pool() or [])
        if not pool:
            self._note("alpaca", status="no key", error="no Alpaca key pair configured")
            await asyncio.sleep(600)
            return
        for key, secret in pool:
            async with websockets.connect(ALPACA_NEWS_WS, open_timeout=15, ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 22) as ws:
                await asyncio.wait_for(ws.recv(), 15)                       # "connected"
                await ws.send(json.dumps({"action": "auth", "key": key, "secret": secret}))
                reply = json.loads(await asyncio.wait_for(ws.recv(), 15))
                if not any(m.get("T") == "success" and m.get("msg") == "authenticated" for m in reply):
                    continue                                                # this pair is dead: try the next
                await ws.send(json.dumps({"action": "subscribe", "news": ["*"]}))
                self._note("alpaca", status="live", error=None)
                async for raw in ws:
                    try:
                        messages = json.loads(raw)
                    except ValueError:
                        continue
                    items = [it for m in (messages if isinstance(messages, list) else [messages])
                             for it in alpaca_items(m, self.score)]
                    if items:
                        await asyncio.to_thread(self.store, "alpaca", items)
                return                                                      # socket closed: reconnect
        self._note("alpaca", status="auth failed", error=f"every Alpaca key pair refused ({len(pool)})")
        await asyncio.sleep(1800)

    # ------------------------------------------------------------------ polls
    def _run_poll(self, feed: str, every: float, poll: Callable[[], None]) -> None:
        while True:
            started = time.monotonic()
            try:
                poll()
                self._note(feed, status="live", error=None)
            except Exception as exc:  # noqa: BLE001
                self._note(feed, status="error", error=f"{type(exc).__name__}: {str(exc)[:120]}")
            time.sleep(max(5.0, every - (time.monotonic() - started)))

    def _poll_wires(self) -> None:
        errors = []
        for publisher, url in WIRE_FEEDS:
            try:
                text = self.http_get(url, {"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, */*"}, 20)
            except Exception as exc:  # noqa: BLE001 - one wire down never stops the others
                errors.append(f"{publisher}: {type(exc).__name__}")
                continue
            self.store("wires", wire_items(text, publisher, self.score), part=publisher)
        if errors:
            self._note("wires", error="; ".join(errors))

    def _poll_media(self) -> None:
        universe = [str(s).upper() for s in (self.universe() or ())]
        if not universe:                        # names are matched against the Watchlist only
            self._note("media", status="waiting", error="Watchlist not read yet")
            return
        from news_names import default_names
        names = default_names(self.sec_user_agent)
        match = lambda headline: names.tickers_in(headline, universe)  # noqa: E731
        errors = []
        for publisher, url in MEDIA_FEEDS:
            try:
                text = self.http_get(url, {"User-Agent": BROWSER_UA, "Accept": "application/rss+xml, */*"}, 20)
            except Exception as exc:  # noqa: BLE001 - one site down never stops the others
                errors.append(f"{publisher}: {type(exc).__name__}")
                continue
            self.store("media", media_items(text, publisher, match, self.score), part=url)
        if errors:
            self._note("media", error="; ".join(sorted(set(errors))))
        if len(errors) == len(MEDIA_FEEDS):
            raise OSError("every market-news feed failed")

    def _poll_edgar(self) -> None:
        headers = {"User-Agent": self.sec_user_agent, "Accept": "application/atom+xml, */*"}
        if not self._cik_tickers or time.monotonic() - self._cik_loaded > 86400:
            data = json.loads(self.http_get(EDGAR_TICKERS, headers, 30))
            mapping: dict[str, list[str]] = {}
            for row in (data.values() if isinstance(data, dict) else data):
                mapping.setdefault(str(int(row["cik_str"])), []).append(str(row["ticker"]).upper())
            self._cik_tickers, self._cik_loaded = mapping, time.monotonic()
        for page in range(EDGAR_MAX_PAGES):
            text = self.http_get(EDGAR_CURRENT.format(start=page * 100), headers, 20)
            items, oldest = edgar_items(text, self._cik_tickers, self.score)
            with self._lock:
                seen = self.feeds["edgar"].get("_seen", set())
                first = not self.feeds["edgar"].get("_warm", False)
                repeat = any((it["symbol"], it["url"]) in seen for it in items)
            self.store("edgar", items)
            # A busy minute (the 16:00-18:00 Form 4 flood) can push more than 100
            # filings: read the next page until it reaches what the last poll saw.
            if first or repeat or oldest is None:
                break
            time.sleep(0.5)

    @staticmethod
    def _http_get(url: str, headers: dict, timeout: float) -> str:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
            body = resp.read()
        return body.decode("utf-8", errors="replace") if b"ISO-8859-1" not in body[:200] else body.decode("latin-1")


__all__ = ["LiveNewsIngest", "alpaca_items", "wire_items", "edgar_items", "media_items"]
