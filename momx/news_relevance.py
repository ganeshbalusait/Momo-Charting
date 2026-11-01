"""Is a headline really about this ticker, and is Jev sure enough? (2026-10-10)

Ganesh paid for Jev to read the news. A blind check of 75 of Jev's reads
(Oct 5-9) agreed with a human read 75% of the time and called the direction
backwards only 2 times - but most of its misses were headlines about ANOTHER
company that the news scraper had tagged to the ticker ("ERock, HD
Construction Equipment ..." tagged HD = Home Depot -> Bullish 87%; an IonQ
contract tagged NVTS -> Bullish 82%), plus routine items read with low
conviction. Two plain-code gates fix both, before a ▲ / ▼ is shown or traded:

1. about_ticker(): the HEADLINE must name the company (its name from the
   official Nasdaq Trader directory, momx/etf_list.py) or show the ticker in an
   unmistakable form ($HD, (HD), NYSE: HD; a bare 3+ letter ticker that is not
   an English word). Index ETFs (SPY, QQQ, USO ...) carry market news and are
   not checked. Unknown symbols (no directory name yet) pass - never a guess.
2. routine(): law-firm adverts, opinion / valuation essays, clickbait questions,
   list articles and a repeated rating are Not important (Jev's own question
   says so, but it reads them as news) - unless the headline names a real move.
3. effective(): Bullish / Bearish only at Jev's MIN_P (90%) or more.

Blind checks (2026-10-10, stocks, a human read vs the ▲ / ▼ shown): before
66% / 64% correct; with all three gates 91% / 89% on the samples used to set
them and 85% on a fresh third sample - never the opposite direction.

Everything that shows or uses Jev's news read goes through effective(): the
News column ▲ / ▼, the NEWS ▲ list and the bot's News rule.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

MIN_P = 0.90

#: Ticker-looking words that are ordinary English / finance words - a bare
#: match on these proves nothing (they need $X or (X)).
COMMON_WORDS = frozenset("""
A AN AND ARE AS AT BE BIG BY CAN CAR CEO CFO DO FOR GO HAS HE IF IN IS IT ITS ME MY NO NOT NOW OF ON ONE OR OUT SO THE TO
UP US WE ALL ANY ARM BEST BILL BOX CASH COST DAY EAT EV EVER FAST FIVE FLY FUN GOOD HOPE HUGE JOB KEY KIDS LIFE LOW LOVE
MAIN MAN MORE MOVE NEW NEXT OPEN PAY PLAY REAL RUN SAFE SEE SHOP SNOW TECH TRUE TWO WELL WIN WORK YOU AI ETF IPO SEC FDA
USA GDP CPI FED EPS AM PM Q1 Q2 Q3 Q4 TV
""".split())

#: Brand names a headline uses instead of the legal name.
ALIASES = {
    "GOOGL": ("Google", "Alphabet", "YouTube", "Waymo"), "GOOG": ("Google", "Alphabet", "YouTube", "Waymo"),
    "META": ("Meta", "Facebook", "Instagram", "WhatsApp"), "BRK.B": ("Berkshire",), "BRK.A": ("Berkshire",),
    "TSM": ("TSMC", "Taiwan Semiconductor"), "SPCX": ("SpaceX",), "BABA": ("Alibaba",), "UNH": ("UnitedHealth",),
    "JPM": ("JPMorgan", "JP Morgan"), "GS": ("Goldman",), "MS": ("Morgan Stanley",), "BAC": ("Bank of America", "BofA"),
    "WFC": ("Wells Fargo",), "KO": ("Coca-Cola", "Coke"), "MCD": ("McDonald",), "DIS": ("Disney",), "NKE": ("Nike",),
    "HD": ("Home Depot",), "LOW": ("Lowe's", "Lowes"), "COST": ("Costco",), "WMT": ("Walmart",), "TGT": ("Target Corp",),
}

#: Words that never identify a company on their own.
_GENERIC = frozenset("""
the inc incorporated corp corporation co company holdings holding group ltd limited plc sa nv ag se lp llc trust
class common stock ordinary shares share american depositary units unit warrants new international global national
united first general digital energy financial technologies technology systems solutions industries resources
therapeutics pharmaceuticals bio sciences partners capital bancorp bank of and & de
""".split())

_SPLIT = re.compile(r"\s+-\s+|\s+Common Stock|\s+Class [A-Z]\b|\s+Ordinary Shares|\s+American Depositary", re.I)
_SUFFIX = re.compile(r",?\s+(Inc\.?|Incorporated|Corp\.?|Corporation|Co\.?|Company|Holdings?|Group|Ltd\.?|Limited|plc|"
                     r"N\.?V\.?|S\.?A\.?|AG|SE|L\.?P\.?|LLC|Trust)\b.*$", re.I)


def name_keys(symbol: str, security_name: str | None) -> list[str]:
    """Phrases that identify the company in a headline (longest first)."""
    keys = list(ALIASES.get(symbol, ()))
    name = str(security_name or "").strip()
    if name:
        core = _SPLIT.split(name)[0]
        core = re.sub(r"^The\s+", "", core, flags=re.I).split(",")[0]
        core = _SUFFIX.sub("", core).strip(" .,&")
        core = re.sub(r"\.com$", "", core, flags=re.I)       # "Amazon.com" -> "Amazon"
        if core:
            keys.append(core)
            words = [w for w in re.split(r"[\s/]+", core) if w]
            first = words[0].strip(".,'") if words else ""
            if len(words) > 1 and len(first) >= 4 and first.lower() not in _GENERIC:
                keys.append(first)
    seen, out = set(), []
    for k in sorted(keys, key=len, reverse=True):
        if k.lower() not in seen and len(k) >= 3:
            seen.add(k.lower())
            out.append(k)
    return out


def about_ticker(symbol: Any, headline: Any, security_name: str | None, *, is_etf: bool | None = None) -> bool | None:
    """True when the headline names this company or ticker; False when it
    clearly does not; None when it cannot be told (no name on file)."""
    sym = str(symbol or "").strip().upper()
    text = " ".join(str(headline or "").split())
    if not sym or not text:
        return None
    if is_etf:
        return None                     # index / commodity ETFs carry market news
    t = re.escape(sym)
    if re.search(rf"(\${t}\b|\({t}\)|\b(NYSE|NASDAQ|Nasdaq|NYSEAMERICAN|AMEX|NYSE American)\s*:\s*{t}\b)", text):
        return True
    if len(sym) >= 3 and sym not in COMMON_WORDS and re.search(rf"(?<![A-Za-z]){t}(?![A-Za-z])", text):
        return True
    keys = name_keys(sym, security_name)
    if not keys:
        return None
    for k in keys:
        if re.search(rf"(?<![A-Za-z]){re.escape(k)}(?![a-z])", text, re.I):
            return True
    return False


#: A headline that reports a real price move ("Why FuelCell Stock Is Plummeting
#: Today", "Gains 14% - Here's Why") is news even when it is phrased as a question.
_MOVE = re.compile(r"\b(surg|soar|jump|plung|plummet|fall|falls|fell|drop|sink|sank|rall|tumbl|slid|slip|spik|crash|rise|rises|"
                   r"rose|gain|climb|sink|skyrocket|rocket|tank|dip|up \d|down \d|\d+(\.\d+)?%)", re.I)
#: Items Jev's own question calls "not important" but often reads as news
#: (blind checks 2026-10-10): law-firm adverts, opinion / valuation essays,
#: clickbait questions, list articles, a rating merely repeated.
_ROUTINE = (
    ("law-firm advert", re.compile(r"law (group|firm|offices)|contact the .{0,40}(law|attorney)|class action|securities (law|fraud) "
                                   r"(violation|investigation)|shareholder alert|investor alert|deadline reminder|"
                                   r"investors? (who|with) (lost|losses)|encourages .{0,40}investors", re.I)),
    ("opinion / valuation piece", re.compile(r"fair value|valuation (check|update)|stock forecast|worth in \d+ years|\$[\d,]+ invested|"
                                             r"isn['’]t an?\b|better buy|\bvs\.? .{0,40}which|reasons? to (buy|sell|avoid|own)|"
                                             r"\d+ reasons?|\btop \d+\b|\d+ (ai )?stocks\b", re.I)),
    ("rating repeated", re.compile(r"\breiterat(e|es|ed|ing)\b", re.I)),
    ("question headline", re.compile(r"(\?\s*$)|^(what['’]s going on|what will|should you|is it|is now|can |will |does |how (much|high|low))",
                                     re.I)),
)


def routine(headline: Any) -> str | None:
    """Why this headline is routine (no ▲ / ▼), or None. A headline that names
    a real price move is never routine."""
    text = " ".join(str(headline or "").split())
    if not text:
        return None
    for why, pattern in _ROUTINE:
        if pattern.search(text):
            if why in ("question headline", "opinion / valuation piece") and _MOVE.search(text):
                return None
            return why
    return None


def effective(reading: Mapping | None, symbol: Any, headline: Any, security_name: str | None,
              *, is_etf: bool | None = None) -> dict | None:
    """Jev's reading as shown and traded: off-topic or low-confidence
    Bullish / Bearish become not_important (the raw label is kept)."""
    if not isinstance(reading, Mapping) or not reading.get("label"):
        return None
    out = {"label": reading.get("label"), "p": reading.get("p"), "confidence": reading.get("confidence")}
    if out["label"] in ("bullish", "bearish"):
        about = about_ticker(symbol, headline, security_name, is_etf=is_etf)
        p = reading.get("p")
        kind = routine(headline)
        if about is False:
            out.update(label="not_important", raw=reading.get("label"), why="not about " + str(symbol).upper())
        elif kind:
            out.update(label="not_important", raw=reading.get("label"), why=kind)
        elif not isinstance(p, (int, float)) or p < MIN_P:
            out.update(label="not_important", raw=reading.get("label"), why=f"Jev only {round((p or 0) * 100)}% sure")
    return out
