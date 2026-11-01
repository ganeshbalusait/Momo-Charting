"""Which ticker is a headline about? Strict name matching for feeds that do not tag tickers.

Used by the Google News source (catalyst_engine.GoogleNewsStrictSource) and the
MarketWatch / CNBC / Investing.com feeds (news_live.py), added 2026-10-02 after a
measured gap: those feeds carried catalysts no tagged source had ("JPMorgan cuts
Sigma Lithium stock rating", "Applied Optoelectronics soars on big 800G AI deal").

A loose match is worse than none - the first test matched "Duolingo ... price
target" to Target (TGT) and every "quantum computing" story to Quantum Corp
(QMCO). So a headline names a ticker only when the HEADLINE (not the teaser) has:

* the ticker in a form that cannot be an ordinary word: "(NVDA)", "$NVDA",
  "NASDAQ: NVDA", or a bare upper-case NVDA when the ticker is not an English word
  or acronym (AI, CAR, NOW, ALL, BULL, PL ... are skipped bare), or
* the company's short name (SEC company_tickers.json: "SIGMA LITHIUM CORP" ->
  "sigma lithium", plus catalyst_engine.TICKER_ALIASES) as whole words, unless the
  short name is a single ordinary word (target, block, quantum, applied ...).

Plus junk titles (quote / chart / forecast pages, option-contract pages) are
never news. Names load once a day from SEC (cached on disk), never per headline.
"""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request
from pathlib import Path
from typing import Iterable

SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
CACHE_SECONDS = 86400

# Tickers that are words / acronyms in headlines: never matched bare (still matched as "(CAR)" or "$CAR").
WORD_TICKERS = {
    "A", "AI", "ALL", "AM", "ARE", "BE", "BIG", "BULL", "CAN", "CAR", "CASH", "CEO", "CFO", "DOC", "EAT", "EU", "EV",
    "FAST", "FDA", "FED", "FOR", "FUN", "GO", "GDP", "HAS", "HOME", "IPO", "IT", "KEY", "LIFE", "LOVE", "LOW", "MAN",
    "NASA", "NEW", "NICE", "NOW", "ON", "ONE", "OPEN", "OUT", "PL", "PLAY", "PM", "REAL", "RUN", "SAFE", "SEE", "SHOP",
    "SNOW", "SO", "TV", "TWO", "UK", "UP", "US", "USA", "WELL", "WORK", "YOU", "ARM", "BOX", "RH", "OR", "SEC", "ETF",
    "CPI", "API", "AGI", "ESG", "IQ", "ID", "MA", "MO", "MS", "NET", "PAY", "PEN", "PINS", "ROK", "SAVE", "TRIP", "TEAM",
    "WAY", "WISH", "ZIP", "ANY", "AIR", "ALLY", "BILL", "CART", "DASH", "DELL", "DOCS", "EAR", "EYE", "FIVE", "FLY",
    "FUND", "GAP", "GOLD", "HOOD", "HUT", "INFO", "JOBY", "LAZR", "LUNR", "MAIN", "MARA", "MET", "MOD", "NOV", "OKE",
    "PATH", "POST", "RACE", "RARE", "RENT", "RIDE", "ROAD", "SKY", "SPOT", "STEM", "SUN", "TAP", "TECH", "TRUE",
    "TURN", "VERB", "WOLF", "X",
}
# Short names that are ordinary words on their own.
GENERIC_NAMES = {
    "target", "block", "quantum", "applied", "global", "united", "american", "general", "first", "national",
    "international", "energy", "digital", "advanced", "power", "data", "solar", "health", "capital", "financial",
    "real", "next", "bank", "trust", "core", "live", "sea", "box", "match", "snap", "gap", "visa", "ford", "apple",
    "meta", "oracle", "intel", "shell", "unity", "palantir", "amazon", "uber", "lyft", "zoom", "coherent", "ciena",
    "rambus", "celestica", "credo", "nebius", "freeport", "talen", "lemonade", "macy", "ouster", "planet", "dropbox",
    "enphase", "wolfspeed", "progressive", "public", "service", "standard", "southern", "western", "eastern",
    "northern", "pacific", "atlantic", "liberty", "pioneer", "frontier", "summit", "crown", "eagle", "phoenix",
    "carrier", "corning", "lumen", "vistra", "constellation", "dominion", "duke", "entergy", "edison", "exelon",
    "sigma", "arch", "alpha", "beta", "delta", "gamma", "omega", "vertex", "nexus", "matrix", "fusion", "spirit",
    "oscar", "rocket", "strategy", "booking", "carnival", "celsius", "microchip", "caterpillar", "upstart",
    "snowflake", "proshares", "samsara", "ondas", "nike", "boeing", "target", "compass", "harmony", "genesis", "insight", "align", "check", "point", "square", "circle",
    "toast", "affirm", "chewy", "peloton", "roku", "etsy", "carvana", "opendoor", "rivian", "lucid",
}
# A common-word name ("Target", "Apple", "Intel", "Quantum") counts only with company
# context right next to it: "Target stock", "Apple's", "Intel CEO", "shares of Oracle".
# "Duolingo ... price target" and "quantum computing stocks" have none.
_AFTER = (r"(?:'s)?\s+(?:stock|stocks|shares|inc|corp|ceo|cfo|chief|earnings|revenue|sales|q[1-4]|quarter|guidance|"
          r"outlook|deal|unveils|launches|announces|reports|beats|misses|soars|surges|jumps|plunges|tumbles|falls|"
          r"drops|rises|climbs|slides|sinks|gains|says|plans|hires|names|wins|signs|cuts|raises|lays|to buy|"
          r"to acquire|agrees|confirms|faces|sues|is sued|shareholders|investors|board|delays|recalls)\b")
_BEFORE = r"\b(?:shares of|upgrades|downgrades|initiates|starts|buy|sell|hold on|sues|acquires|buys)\s+"


_TAGGED_RE = re.compile(r"(?:\(|\$|(?:NASDAQ|NYSE|Nasdaq)\s*:\s*)([A-Z]{1,5})\b")
_BARE_RE = re.compile(r"(?<![A-Za-z$])([A-Z]{1,5})(?![A-Za-z])")
_CTX_CACHE: dict = {}


def _in_context(name: str, title_low: str) -> bool:
    pat = _CTX_CACHE.get(name)
    if pat is None:
        n = re.escape(name)
        pat = re.compile(r"\b" + n + _AFTER + "|" + _BEFORE + n + r"\b|\b" + n + r"'s\b")
        _CTX_CACHE[name] = pat
    return bool(pat.search(title_low))


JUNK_TITLE = re.compile(
    r"stock (price|chart|quote|forecast)s?\b|interactive stock chart|advanced charts|stock forecasts|"
    r"latest news$|price, news, quote|historical prices|\b[A-Z]{1,6}\d{6}[CP]\d{8}\b|:[A-Z]{2,4} stock latest|"
    r"\boptions? chain\b|\bstock quote\b|"
    # round-ups and newsletters name several companies but are about none of them
    r"biggest moves|biggest movers|biggest analyst calls|stocks to watch|morning squawk|"
    r"stocks moving (premarket|midday|after)|top (gainers|losers)|\bmovers:|"
    # Investing.com auto-pages ("Tesla earnings missed by $0.01") restate an OLD quarter under today's date
    r"earnings (beat|missed|matched|in line) by \$|"
    r"8-k & sec filings|sec filings$|"
    # law-firm lawsuit ads (Pomerantz, Rosen, Hagens Berman ...) - ads, not company news
    r"securities fraud|class action|investor alert|shareholder alert|lead plaintiff|investors (who|with) losses|"
    r"investors have opportunity|law firm|investigation on behalf of|reminds (investors|shareholders)",
    re.IGNORECASE,
)
_SUFFIX = re.compile(r"\s*\b(inc|corp|corporation|co|company|ltd|limited|plc|holdings?|group|class [a-z]|n\.?v|s\.?a|"
                     r"ag|se|lp|llc|pbc|trust|bancorp|technologies|technology|ord|adr|/[a-z]+/?)\b.*$", re.IGNORECASE)


def short_name(sec_title: str) -> str:
    """"SIGMA LITHIUM CORP" -> "sigma lithium"; "Alphabet Inc." -> "alphabet"."""
    text = re.sub(r"[,.]", " ", str(sec_title or "")).strip()
    text = _SUFFIX.sub("", text)
    return " ".join(text.lower().split())


def is_junk(headline: str) -> bool:
    return bool(JUNK_TITLE.search(str(headline or "")))


class CompanyNames:
    """ticker -> the names a headline may use for it; loaded once a day."""

    def __init__(self, cache_path: Path | None = None, *, user_agent: str = "AGX MomX news",
                 fetch=None, aliases: dict[str, list[str]] | None = None) -> None:
        self.cache_path = Path(cache_path) if cache_path else None
        self.user_agent = user_agent
        self._fetch = fetch or self._default_fetch
        self._aliases = {k.upper(): [a.lower() for a in v] for k, v in (aliases or {}).items()}
        self._lock = threading.Lock()
        self._names: dict[str, list[str]] = {}
        self._loaded = 0.0

    # ------------------------------------------------------------------ load
    def _default_fetch(self) -> dict:
        req = urllib.request.Request(SEC_TICKERS_URL, headers={"User-Agent": self.user_agent})
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _raw(self) -> dict:
        if self.cache_path and self.cache_path.exists() and time.time() - self.cache_path.stat().st_mtime < CACHE_SECONDS:
            try:
                return json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001 - a bad cache just refetches
                pass
        data = self._fetch()
        if self.cache_path:
            try:
                self.cache_path.parent.mkdir(parents=True, exist_ok=True)
                self.cache_path.write_text(json.dumps(data), encoding="utf-8")
            except Exception:  # noqa: BLE001
                pass
        return data

    def names(self) -> dict[str, list[str]]:
        with self._lock:
            if self._names and time.monotonic() - self._loaded < CACHE_SECONDS:
                return self._names
        try:
            raw = self._raw()
        except Exception:  # noqa: BLE001 - no names today: tickers still match
            raw = {}
        out: dict[str, list[str]] = {}
        for row in (raw.values() if isinstance(raw, dict) else raw):
            ticker = str(row.get("ticker") or "").upper()
            name = short_name(row.get("title") or "")
            if ticker and name and len(name) >= 4:
                out.setdefault(ticker, [])
                if name not in out[ticker]:
                    out[ticker].append(name)
        for ticker, extra in self._aliases.items():
            out.setdefault(ticker, [])
            out[ticker] = list(dict.fromkeys([*extra, *out[ticker]]))
        with self._lock:
            self._names, self._loaded = out, time.monotonic()
        return out

    # ------------------------------------------------------------------ match
    def tickers_in(self, headline: str, universe: Iterable[str]) -> list[str]:
        """The universe tickers this headline is about (strict; see the module doc)."""
        title = str(headline or "")
        if not title or is_junk(title):
            return []
        names = self.names()
        low = " " + re.sub(r"[^a-z0-9&' ]", " ", title.lower()) + " "
        title_low = " ".join(title.lower().split())
        shouting = title.isupper()            # an ALL-CAPS headline turns every word into a "ticker"
        # Read the headline ONCE (2026-10-02): a regex built per ticker per headline -
        # 368 tickers x hundreds of headlines, 61 s per 400 headlines - kept
        # api_server's CPU busy and starved every other request (news timed out).
        tagged = set(_TAGGED_RE.findall(title))
        bare = set() if shouting else set(_BARE_RE.findall(title))
        hits: list[str] = []
        for ticker in universe:
            t = str(ticker or "").upper()
            if not t or "." in t:
                continue
            if t in tagged:
                hits.append(t)
                continue
            if t in bare and t not in WORD_TICKERS and len(t) >= 2:
                hits.append(t)
                continue
            for name in names.get(t, []):
                single = " " not in name
                if single and (name in GENERIC_NAMES or len(name) < 5):
                    if name in title_low and _in_context(name, title_low):
                        hits.append(t)
                        break
                    continue
                if f" {name} " in low or f" {name}'s " in low:
                    hits.append(t)
                    break
        return hits





_DEFAULT: CompanyNames | None = None
_DEFAULT_LOCK = threading.Lock()


def default_names(user_agent: str = "AGX MomX news") -> CompanyNames:
    """One shared matcher (SEC names cached in artifacts/sec_company_tickers.json)."""
    global _DEFAULT
    with _DEFAULT_LOCK:
        if _DEFAULT is None:
            try:
                from catalyst_engine import TICKER_ALIASES
            except Exception:  # noqa: BLE001
                TICKER_ALIASES = {}
            _DEFAULT = CompanyNames(Path(__file__).resolve().parent / "artifacts" / "sec_company_tickers.json",
                                    user_agent=user_agent,
                                    # DRAM is a fund: its aliases (micron, sandisk) would file Micron's news under it
                                    aliases={k: v for k, v in TICKER_ALIASES.items() if k not in {"DRAM"}})
        return _DEFAULT
__all__ = ["CompanyNames", "default_names", "short_name", "is_junk", "WORD_TICKERS", "GENERIC_NAMES"]
