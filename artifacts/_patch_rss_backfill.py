"""Fill the coverage gap Benzinga leaves, without becoming the next rate incident.

Benzinga (via Alpaca) answered 123 of 358 watchlist rows on 2026-09-04. The
trader compared us to a competitor showing "News 5h" on NBIS while we showed
nothing, and he was right: NBIS's newest BENZINGA headline was ~53h old, so the
24h window correctly dropped it. Measured the same afternoon, Yahoo's keyless
per-ticker RSS carried a 6.1h Nebius headline. A COVERAGE gap, not a logic bug.

THE DANGER, stated because it is what nearly stopped this shipping. Benzinga's
?symbols= bulk query delivers ~44.75 symbols per request (358 in 8 requests);
RSS is ONE REQUEST PER TICKER. Backfilling all 235 missing symbols every build
is a 44x multiplier - 74-144 req/min against a measured 2.4/min today. This
machine was IP-banned twice this week for exactly that shape, and
momx/board.py's own performance rule says its network "may never become
per-symbol".

So the backfill is BOUNDED, not complete: 24 symbols per pass, rotating, each
answer cached 15 minutes. Steady state 24 req / 300s = 4.8/min; the full missing
set is covered in roughly ten passes. A symbol gains news within about an hour
of first being seen, not instantly. That is the deliberate trade.
"""
import io

p = "momx/news.py"
s = io.open(p, encoding="utf-8", newline="").read()

# --- imports ---------------------------------------------------------------
OLD = "import os\nimport threading\nimport time\n"
NEW = ("import email.utils\nimport os\nimport re\nimport threading\nimport time\n"
       "import xml.etree.ElementTree as ElementTree\n")
assert s.count(OLD) == 1, "imports anchor"
s = s.replace(OLD, NEW)

# --- the backfill, above the public API banner ------------------------------
ANCHOR = ("# ---------------------------------------------------------------------------\n"
          "# Public API\n"
          "# ---------------------------------------------------------------------------\n")
assert s.count(ANCHOR) == 1, "public api banner"

BLOCK = '''# ---------------------------------------------------------------------------
# RSS backfill - the symbols Benzinga simply does not cover
# ---------------------------------------------------------------------------
#
# Benzinga answered 123 of 358 watchlist rows on 2026-09-04. NBIS's newest
# Benzinga headline was ~53h old so the 24h window correctly dropped it, while
# Yahoo's keyless per-ticker RSS had a 6.1h Nebius story for the same symbol.
# A coverage gap, not a logic bug.
#
# BOUNDED ON PURPOSE. Benzinga's bulk ?symbols= query delivers ~44.75 symbols
# per request; RSS is ONE REQUEST PER TICKER. Backfilling all 235 missing
# symbols every build would be 74-144 requests/minute against a measured
# 2.4/min today - the same shape that got this IP banned twice this week, and
# a direct breach of momx/board.py's rule that its network "may never become
# per-symbol". Hence: a fixed budget per pass, rotating, with misses cached.
# Steady state 24 requests per 300s = 4.8/min, full coverage in ~10 passes.

#: Keyless, ticker-keyed, no account required.
YAHOO_RSS_URL = (
    "https://feeds.finance.yahoo.com/rss/2.0/headline?s={symbol}&region=US&lang=en-US"
)

#: Symbols fetched from RSS per build. See the arithmetic above.
RSS_BACKFILL_PER_BUILD = 24

#: One symbol's answer is reused this long. Longer than the Benzinga cache
#: because the backfill rotates: a symbol re-fetched every pass would starve
#: the rest of the queue.
RSS_CACHE_TTL_SECONDS = 900.0

#: Shorter than the Benzinga timeout. This is a nice-to-have on a best-effort
#: path and must never be what makes a build slow.
RSS_TIMEOUT_SECONDS = 6.0

RSS_ENV = "MOMX_NEWS_RSS"

#: "10 Information Technology Stocks Whale Activity", "3 Founder Backed Growth
#: Stocks To Watch" - headlines naming many companies and explaining none. RSS
#: carries no symbols list, so the scope test used for Benzinga articles cannot
#: run; this is the cheap deterministic stand-in. Matches are DEMOTED to
#: market-wide, never dropped - the same rule as _newest_per_symbol.
_LISTICLE_PATTERNS = (
    re.compile(r"^\\s*\\d+\\s+\\S+.*\\bstocks?\\b", re.IGNORECASE),
    re.compile(r"\\bstocks?\\s+(to\\s+watch|moving|whale|that)\\b", re.IGNORECASE),
    re.compile(r"\\b(top|best)\\s+\\d+\\b", re.IGNORECASE),
)

_RSS_CACHE: dict = {}
_RSS_CURSOR = 0


def _rss_enabled() -> bool:
    return str(os.environ.get(RSS_ENV, "1")).strip().lower() not in ("0", "false", "no")


def _looks_like_a_listicle(headline) -> bool:
    text = str(headline or "")
    return any(pattern.search(text) for pattern in _LISTICLE_PATTERNS)


def _parse_rss(body, cutoff):
    """Newest item inside the window, in the shape Benzinga entries use.

    RSS dates are RFC-822, not ISO. The module's own _parse_when handles ISO
    only, so converting here is what stops every RSS item being silently
    discarded as undated.
    """
    root = ElementTree.fromstring(body)
    best = None
    best_at = None
    for item in root.findall(".//item"):
        headline = (item.findtext("title") or "").strip()
        if not headline:
            continue
        raw_date = item.findtext("pubDate")
        if not raw_date:
            continue
        try:
            at = email.utils.parsedate_to_datetime(raw_date)
        except (TypeError, ValueError):
            continue
        if at is None:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if at < cutoff:
            continue
        if best_at is not None and at <= best_at:
            continue
        best_at = at
        best = {
            "headline": headline,
            "at": at.astimezone(timezone.utc).isoformat(),
            "source": "Yahoo Finance",
            "url": (item.findtext("link") or "").strip() or None,
            "scope": "market-wide" if _looks_like_a_listicle(headline) else "specific",
            "namedCount": 0,
        }
    return best


def _rss_backfill(missing, cutoff, get) -> dict:
    """Bounded, rotating per-ticker backfill. Never raises, never blocks long."""
    global _RSS_CURSOR
    out = {}
    if not missing or not _rss_enabled():
        return out
    stamp = _monotonic()
    fetch = []
    for symbol in missing:
        held = _RSS_CACHE.get(symbol)
        if held is not None and stamp - held[0] < RSS_CACHE_TTL_SECONDS:
            if held[1]:
                out[symbol] = held[1]
            continue
        fetch.append(symbol)
    if fetch:
        # Rotate, so every missing symbol is reached over successive builds
        # rather than the same alphabetical head being retried forever.
        start = _RSS_CURSOR % len(fetch)
        ordered = fetch[start:] + fetch[:start]
        taken = ordered[:RSS_BACKFILL_PER_BUILD]
        _RSS_CURSOR = (_RSS_CURSOR + len(taken)) % max(len(fetch), 1)
        for symbol in taken:
            entry = None
            try:
                response = get(
                    YAHOO_RSS_URL.format(symbol=symbol),
                    headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                    timeout=RSS_TIMEOUT_SECONDS,
                )
                if getattr(response, "status_code", 0) == 200:
                    entry = _parse_rss(response.text, cutoff)
            except Exception:  # noqa: BLE001 - news can never break the board
                entry = None
            # Remember the MISS too, so a symbol with genuinely no news does not
            # burn a slot on every build.
            _RSS_CACHE[symbol] = (stamp, entry)
            if entry:
                out[symbol] = entry
    return out


'''
s = s.replace(ANCHOR, BLOCK + ANCHOR)

# --- wire it into fresh_news ------------------------------------------------
OLD2 = """        result = _newest_per_symbol(articles, set(wanted), cutoff)

        with _LOCK:"""
NEW2 = """        result = _newest_per_symbol(articles, set(wanted), cutoff)

        # Benzinga leaves roughly two thirds of the watchlist with nothing.
        # Fill some of that gap from a keyless per-ticker feed, bounded per
        # build - see the RSS section above for why this is capped, not
        # complete. A Benzinga entry always wins over a backfilled one.
        missing = [symbol for symbol in wanted if symbol not in result]
        if missing:
            backfilled = _rss_backfill(
                missing, cutoff, get if get is not None else _default_get(),
            )
            if backfilled:
                result = {**backfilled, **result}

        with _LOCK:"""
assert s.count(OLD2) == 1, "fresh_news wiring"
s = s.replace(OLD2, NEW2)

io.open(p, "w", encoding="utf-8", newline="").write(s)
print("patched momx/news.py: bounded Yahoo RSS backfill")
