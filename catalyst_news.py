"""Catalyst check for premarket scanner fires.

When the scanner shows a row, the trader's next question is "why is it
moving?" This module answers it with REAL headlines (Alpaca's Benzinga news
feed, reachable with the account keys the app already holds) plus a
deterministic keyword tag - deliberately NOT a language model. A wrong
guess about a catalyst is worse than no catalyst, the box holds no LLM API
key, and a scanner row must never wait on one more network dependency: the
scanner payload only ever reads a cache that a background thread fills.

Stdlib-only on purpose, like premarket_scanner.py: importable and testable
without the api_server module graph.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

ALPACA_NEWS_URL = "https://data.alpaca.markets/v1beta1/news"

# Newest-first list of profile tokens to try for news credentials. paper5 is
# first because it is the one key verified live (the 2026-08-12 MARKET CLOSED
# incident: paper3 died silently and only paper5 still answered).
_NEWS_PROFILE_TOKENS = ("PAPER5", "PAPER4", "PAPER3", "PAPER2", "PAPER1", "LIVE")

# How old a headline may be and still count as "the premarket catalyst".
# 18 hours reaches back through the prior evening's earnings calls without
# dredging up last week's noise.
MAX_CATALYST_AGE_SECONDS = 18 * 3600

# A symbol's catalyst is re-checked this often while it holds a scanner row.
CATALYST_TTL_SECONDS = 600.0

# Ordered: the first tag whose keywords match wins, so put the most
# decision-relevant (and most specific) phrases first. All matching is done
# on the lowercased headline.
_TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("EARNINGS", ("earnings", "quarterly results", "q1 results", "q2 results",
                  "q3 results", "q4 results", "beats estimates", "misses estimates",
                  "revenue beat", "eps beat", "reports first quarter", "reports second quarter",
                  "reports third quarter", "reports fourth quarter")),
    ("GUIDANCE", ("guidance", "outlook", "forecast raised", "forecast cut", "raises forecast",
                  "cuts forecast", "preannounce")),
    ("FDA", ("fda", "phase 1", "phase 2", "phase 3", "clinical trial", "drug approval",
             "breakthrough therapy")),
    ("M&A", ("acquire", "acquisition", "merger", "buyout", "takeover", "to buy ",
             "stake in", "tender offer")),
    ("UPGRADE", ("upgrade", "raises price target", "raised price target", "initiates coverage",
                 "overweight", "outperform rating", "buy rating")),
    ("DOWNGRADE", ("downgrade", "cuts price target", "lowers price target", "underweight",
                   "sell rating")),
    ("OFFERING", ("offering", "dilution", "secondary", "convertible notes", "sells shares",
                  "share sale", "atm program")),
    ("CONTRACT", ("contract", "partnership", "partners with", "deal with", "awarded",
                  "collaboration", "supply agreement")),
    ("LEGAL", ("lawsuit", "sues", "sued", "investigation", "sec probe", "doj", "settlement",
               "antitrust")),
    ("INSIDER", ("insider", "ceo buys", "ceo sells", "director buys", "director sells",
                 "stock split", "buyback", "repurchase")),
    ("CRYPTO", ("bitcoin", "crypto", "ethereum", "btc ", "mining difficulty")),
    ("MACRO", ("fed ", "fomc", "cpi", "tariff", "rate cut", "rate hike", "jobs report",
               "inflation")),
    ("AI/PRODUCT", ("ai ", " ai,", "artificial intelligence", "chip", "gpu", "launches",
                    "unveils", "new product", "price hike", "price increase")),
)


def classify_headline(headline: object) -> str | None:
    """The first matching tag for a headline, or None when nothing matches."""
    text = f" {str(headline or '').lower()} "
    if not text.strip():
        return None
    for tag, needles in _TAG_RULES:
        for needle in needles:
            if needle in text:
                return tag
    return None


def _parse_iso_utc(stamp: object) -> datetime | None:
    text = str(stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def pick_catalyst(items: object, now_utc: datetime, symbol: str) -> dict | None:
    """The single most decision-relevant recent headline for a symbol.

    Preference order, applied to headlines no older than
    MAX_CATALYST_AGE_SECONDS:
      1. a headline that classifies to a tag beats one that does not
         (a tagged story is a CATALYST; an untagged one is just coverage);
      2. a story about few tickers beats a broad market wrap-up
         (a 10-symbol listicle is rarely why THIS ticker is moving);
      3. newer beats older.
    """
    target = str(symbol or "").strip().upper()
    candidates: list[tuple[int, int, float, dict]] = []
    for item in items if isinstance(items, (list, tuple)) else []:
        if not isinstance(item, dict):
            continue
        headline = str(item.get("headline") or "").strip()
        if not headline:
            continue
        published = _parse_iso_utc(item.get("created_at") or item.get("updated_at"))
        if published is None:
            continue
        age = (now_utc - published).total_seconds()
        if age < 0 or age > MAX_CATALYST_AGE_SECONDS:
            continue
        symbols = [str(s or "").upper() for s in (item.get("symbols") or [])]
        if target and symbols and target not in symbols:
            continue
        tag = classify_headline(headline)
        candidates.append((
            0 if tag else 1,
            len(symbols) if symbols else 99,
            age,
            {
                "headline": headline[:160],
                "tag": tag or "NEWS",
                "publishedAt": published.isoformat(),
                "ageMinutes": int(age // 60),
                "source": str(item.get("source") or "").strip() or None,
            },
        ))
    if not candidates:
        return None
    candidates.sort(key=lambda entry: entry[:3])
    return candidates[0][3]


def news_credentials(getenv=os.environ.get) -> tuple[str, str] | None:
    """The first configured Alpaca key pair, paper5 first (see token order)."""
    for token in _NEWS_PROFILE_TOKENS:
        key = str(getenv(f"ALPACA_PROFILE_{token}_KEY_ID", "") or "").strip()
        secret = str(getenv(f"ALPACA_PROFILE_{token}_SECRET_KEY", "") or "").strip()
        if key and secret:
            return key, secret
    return None


def fetch_symbol_news(symbol: str, key_id: str, secret: str, *, limit: int = 12,
                      timeout: float = 8.0) -> list[dict]:
    """Recent headlines for one symbol from Alpaca's news feed."""
    params = urllib.parse.urlencode({"symbols": str(symbol or "").upper(), "limit": int(limit)})
    request = urllib.request.Request(
        f"{ALPACA_NEWS_URL}?{params}",
        headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    news = payload.get("news") if isinstance(payload, dict) else None
    return news if isinstance(news, list) else []


class CatalystCache:
    """Symbol -> latest catalyst verdict, filled by a background thread.

    get() is the only method the scanner payload may call: it never fetches,
    so a news outage can slow nothing but the background refresher. refresh()
    is the background thread's whole job; a fetch failure keeps the previous
    verdict (stale catalyst beats a blinking one) but re-stamps the check so
    a dead feed is retried on the TTL, not hammered.
    """

    def __init__(self, ttl_seconds: float = CATALYST_TTL_SECONDS) -> None:
        self._ttl = float(ttl_seconds)
        self._lock = threading.Lock()
        self._entries: dict[str, dict] = {}

    def get(self, symbol: str) -> dict | None:
        with self._lock:
            entry = self._entries.get(str(symbol or "").upper())
            return dict(entry["catalyst"]) if entry and entry.get("catalyst") else None

    def refresh(self, symbols: object, fetcher, *, now: float | None = None,
                now_utc: datetime | None = None) -> int:
        """Re-check every symbol whose entry is missing or past the TTL.

        ``fetcher(symbol) -> list[dict]`` does the network work; injecting it
        keeps this testable and keeps credentials out of this class.
        Returns how many symbols were actually fetched.
        """
        clock = time.monotonic() if now is None else float(now)
        moment = now_utc or datetime.now(timezone.utc)
        fetched = 0
        for raw in symbols if isinstance(symbols, (list, tuple, set)) else []:
            symbol = str(raw or "").strip().upper()
            if not symbol:
                continue
            with self._lock:
                entry = self._entries.get(symbol)
                if entry and (clock - float(entry["checkedAt"])) < self._ttl:
                    continue
            try:
                catalyst = pick_catalyst(fetcher(symbol), moment, symbol)
            except Exception:
                with self._lock:
                    entry = self._entries.get(symbol)
                    previous = entry.get("catalyst") if entry else None
                    self._entries[symbol] = {"checkedAt": clock, "catalyst": previous}
                continue
            fetched += 1
            with self._lock:
                self._entries[symbol] = {"checkedAt": clock, "catalyst": catalyst}
        # Symbols that drop off the scanner stop being refreshed; cap the map
        # so a long session cannot grow it without bound.
        with self._lock:
            if len(self._entries) > 64:
                for stale in sorted(self._entries, key=lambda s: self._entries[s]["checkedAt"])[:-64]:
                    self._entries.pop(stale, None)
        return fetched
