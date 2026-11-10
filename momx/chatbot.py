"""Ask the app (2026-11-10): a chatbot that needs no AI.

His "Our app can we add features chatbot? ... if I ask see aapl it find in
scanner history and I will ask why this stocks fails what is wrong and why it's
missed", "Spy if I take how much it went up" and then "Free AI ... How chatbot
is working before AI build same way".

So this is the classic kind: it recognises a fixed set of questions from their
words, finds the ticker, day and time in the sentence, and answers from the
app's own records - the Bot's saved signals, the paper accounts' real orders,
the Scorecard and the saved option chains. No model, no quota, no network.
Every number in an answer is read from a file; when a record is missing it
says so instead of guessing. It only reads: it can never place, change or
cancel a trade.

Questions it understands (see INTENTS):
  what-if     "SPY if I take how much it went up"   "what if I took NVDA call at 10:30"
  why-failed  "why AAPL failed"   "what went wrong with TSLA"
  why-missed  "why was AMD missed"   "why no trade on META"
  show        "see AAPL"   "show SPY yesterday"
  status      "what is the bot doing"   "which bot is ahead"
  help        anything else
"""
from __future__ import annotations

import json
import math
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ACCOUNTS = (("paper3", "Paper 3 (Rules bot)"), ("paper5", "Paper 5 (AI bot)"))
LEVELS = ("level1", "level2", "level3")
RULE_NAMES = {"level1": "Level 1", "level2": "Level 2", "level3": "Level 3"}
#: Words that are never a ticker unless typed in capitals or with a $.
COMMON = {"A", "I", "IF", "AI", "SEE", "WHY", "HOW", "THE", "IT", "AND", "OR", "ON", "AT", "MY", "ME", "WAS", "DID", "UP", "GO",
          "IS", "TO", "OF", "FOR", "BOT", "PUT", "PUTS", "CALL", "CALLS", "TAKE", "TOOK", "WHAT", "WENT", "MUCH", "SHOW", "FAIL",
          "LOST", "LOSS", "MISS", "DAY", "NOW", "ALL", "ARE", "CAN", "DO", "HAS", "IN", "NO", "NOT", "ONE", "OUT", "SO", "BE",
          "BY", "AN", "AS", "WE", "US", "AM", "PM", "ET", "OI", "ANY", "BEST", "GOOD", "BAD", "OPEN", "REAL", "WELL", "LOW", "HIGH",
          # Everyday words that are also listed tickers: they count as a ticker only in capitals or as $WORD.
          "YOU", "MAX", "COST", "RUN", "NET", "SPOT", "TEAM", "FUN", "MAR", "APP", "PLAY", "LOVE", "LIFE", "FAST", "CASH", "MOVE",
          "NEXT", "LAST", "PICK", "PLAN", "RIDE", "SAVE", "SHOP", "STEP", "TRUE", "TURN", "WISH", "WORK", "ZONE", "EVER", "HEAR",
          "KEY", "KEYS", "LINE", "LIVE", "MAIN", "MAN", "MET", "NICE", "PAY", "POST", "RARE", "ROAD", "ROOT", "SAFE", "SEE", "SO",
          "TELL", "TWO", "VERY", "WAY", "YEAR", "FLY", "FOUR", "FREE", "GAIN", "GLAD", "HOPE", "JOB", "KIND", "LAND", "MIND", "ONCE",
          "PEAK", "RACE", "REAL", "SITE", "SUM", "TAP", "TECH", "TILE", "USE", "VIEW", "WEEK", "WOW", "BIG", "BILL", "CAR", "CARS",
          "DAY", "EAT", "EYE", "FOR", "FORM", "FUND", "HAS", "HE", "HOME", "ICE", "JACK", "LOW", "NEW", "OLD", "OUT", "PER", "PET",
          "TASK", "TEN", "TOP", "TOWN", "TREE", "WOLF", "BOX", "BOOT", "COLD", "COOK", "DOCS", "EDIT", "FIVE", "FOLD", "GOLD", "GROW"}
MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4}

#: "Profit or loss?", "result?", "how did it do" - the outcome of a trade. With a ticker (typed, or the one the
#: conversation is about) it is the what-if answer; with none it is the bot's own result.
RESULT = r"\b(profit\s+(or|and)\s+loss\w*|win\s+or\s+los\w*|won\s+or\s+lost|results?|outcome|p\s?[&/]\s?l|pnl|how\s+did\s+(it|that)\s+(do|go|end))\b"

INTENTS = (
    ("why_missed", r"\b(miss(ed|ing)?|skipp?ed|why\s+not|no\s+trade|not\s+(take|taken|tak\w+|buy|bought|trade|traded)"
                   r"|(didn.?t|did\s+not|wasn.?t|was\s+not|don.?t|never)\b.{0,40}\b(take|taken|buy|bought|trade|traded|order\w*))\b"),
    ("why_failed", r"\b(fail\w*|wrong|against|los[st]\w*|lose|bad\s+trade|stopp?ed\s+out|went\s+down|didn.?t\s+work)\b"),
    ("what_if", r"\b(what\s+if|if\s+i\s+(take|took|buy|bought|had)|how\s+(much|far|high)|went\s+up|would\s+(it|i)\s+have|how\s+did"
                r"|max|cost|best\s+price|what\s+did\s+it\s+do)\b"),
    ("status", r"\b(status|doing|ahead|winning|positions?|which\s+(bot|strategy|one)|p\W?l|profit|today.?s\s+result|scorecard|paused?)\b"),
    ("show", r"\b(see|show|look|find|open|check|tell\s+me\s+about)\b"),
)
EXAMPLES = ["SPY if I take how much it went up", "why AAPL failed", "why was NVDA missed", "see TSLA yesterday", "which bot is ahead"]


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _money(v: float | None) -> str:
    if v is None:
        return "not recorded"
    return ("-" if v < 0 else "+") + "$" + f"{abs(v):,.0f}"


def _price(v: float | None) -> str:
    return "not recorded" if v is None else f"${v:.2f}"


def _pct(v: float | None) -> str:
    return "not recorded" if v is None else f"{v:+.0f}%"


def _clock(stamp: Any) -> str:
    """'10:32 AM' from an ISO stamp, in ET."""
    try:
        at = datetime.fromisoformat(str(stamp)).astimezone(ET)
    except (TypeError, ValueError):
        return "time not recorded"
    return at.strftime("%I:%M %p").lstrip("0")


def _read(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# ---------------------------------------------------------------- reading the question
def parse_day(text: str, today: date) -> tuple[date, bool]:
    """(day, was it named). 'today' is the default. Understood: 2026-11-06, 11-06, 11/6, 'nov 6',
    'yesterday' (the last weekday before today) and a weekday name ('friday', 'last friday' =
    the most recent one before today)."""
    low = text.lower()

    def past(year: int, month: int, dayno: int, year_named: bool):
        try:
            found = date(year, month, dayno)
        except ValueError:
            return None
        if not year_named and found > today:
            found = date(year - 1, month, dayno)
        return found

    m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", low)
    if m:
        found = past(int(m.group(1)), int(m.group(2)), int(m.group(3)), True)
        if found:
            return found, True
    m = re.search(r"(?<![\d:.$])(\d{1,2})[-/](\d{1,2})(?![\d:])", low)
    if m:
        found = past(today.year, int(m.group(1)), int(m.group(2)), False)
        if found:
            return found, True
    m = re.search(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?\b", low)
    if m:
        found = past(today.year, MONTHS[m.group(1)], int(m.group(2)), False)
        if found:
            return found, True
    if "yesterday" in low:
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d, True
    m = re.search(r"\b(monday|tuesday|wednesday|thursday|friday)\b", low)
    if m:
        d = today - timedelta(days=1)
        while d.weekday() != WEEKDAYS[m.group(1)]:
            d -= timedelta(days=1)
        return d, True
    return today, bool(re.search(r"\btoday\b", low))


def parse_time(text: str) -> int | None:
    """Minutes after midnight ET for '10:30', '9:45am', '1:15 pm', '2pm', '10 am'. With no am/pm an
    hour of 1-7 means the afternoon (the market is not open at 1-7 in the morning)."""
    low = text.lower()
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)?\b", low) or re.search(r"\b(\d{1,2})()\s*(am|pm)\b", low)
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if hour > 23 or minute > 59 or (half and not 1 <= hour <= 12):
        return None
    if half == "pm" and hour < 12:
        hour += 12
    elif half == "am" and hour == 12:
        hour = 0
    elif half is None and 1 <= hour <= 7:
        hour += 12
    return hour * 60 + minute


def parse_side(text: str) -> str | None:
    """'put' / 'call' when the sentence says so. "went up" / "how high" means the calls and "went down" /
    "dropped" the puts - only a hint, used when no side is named."""
    low = text.lower()
    if re.search(r"\b(puts?|bear\w*|short)\b", low):
        return "bear"
    if re.search(r"\b(calls?|bull\w*|long)\b", low):
        return "bull"
    return None


def hinted_side(text: str) -> str | None:
    low = text.lower()
    if re.search(r"\b(went|go|goes|gone|going|ran|run|move[ds]?)\s+up\b|\bhow\s+high\b|\brall(y|ied)\b", low):
        return "bull"
    if re.search(r"\b(went|go|goes|gone|going|move[ds]?)\s+down\b|\bdropp?ed\b|\bfell\b|\bhow\s+low\b", low):
        return "bear"
    return None


def parse_ticker(text: str, known: set[str], with_records: set[str] | None = None) -> str | None:
    """The ticker a sentence is about. Whole words only (never a piece of a longer word or of
    "didn't"). A word in capitals or written $LIKE this wins wherever it sits. Among ordinary
    lower-case words, one- and two-letter words and everyday words that happen to be tickers
    (you, cost, max, app ...) are never taken; a word the app has trades on that day is
    preferred to one it merely follows the option chain of."""
    words = re.findall(r"(?<![A-Za-z$.'])\$?[A-Za-z][A-Za-z.']*", text)
    shouting = text == text.upper()                  # an ALL-CAPS sentence proves nothing about any one word
    plain = []
    for raw in words:
        if "'" in raw:                               # didn't, wasn't, bot's
            continue
        word = raw.lstrip("$").rstrip(".").upper()
        if word not in known:
            continue
        if raw.startswith("$") or (raw.lstrip("$").rstrip(".") == word and not shouting and len(word) > 1 and word not in ("AI", "OI", "ET", "AM", "PM", "BOT")):
            return word
        if len(word) >= 3 and word not in COMMON:
            plain.append(word)
    for word in plain:
        if with_records and word in with_records:
            return word
    return plain[0] if plain else None


def parse_intent(text: str) -> str:
    low = text.lower()
    for name, pattern in INTENTS:
        if re.search(pattern, low):
            return name
    return "help"


# ---------------------------------------------------------------- the records
#: Bulky parts of a Bot row the answers never read. Dropping them keeps a day in memory at a few megabytes.
_HEAVY = ("scannerInputs", "aiDecision", "aiCandidate", "levelSignal", "path", "jevV", "capture")
_SYMBOL = re.compile(rb'"symbol":\s*"([A-Z.]{1,6})"')
_INDEX: dict[tuple, tuple] = {}


def _slim(t: dict) -> dict:
    out = {k: v for k, v in t.items() if k not in _HEAVY}
    setup = out.get("setup")
    if isinstance(setup, dict):
        chart = setup.get("chartContext") if isinstance(setup.get("chartContext"), dict) else {}
        out["setup"] = {**{k: v for k, v in setup.items() if isinstance(v, (int, float))},
                        "chartContext": {"levels": chart.get("levels")}}
    return out


def day_tickers(artifacts: Path, day: str, today: str) -> frozenset:
    """The tickers with a Bot signal on a day, found by scanning the files' text (a fraction of a
    second) instead of parsing them. A past day never changes, so it is scanned once."""
    key = (str(artifacts), day)
    held = _INDEX.get(key)
    if held and (day < today or time.monotonic() - held[0] < 60):
        return held[1]
    found: set = set()
    for folder in ("momx_option_track", Path("bear") / "momx_option_track"):
        try:
            found |= {m.decode() for m in _SYMBOL.findall((Path(artifacts) / folder / f"{day}.json").read_bytes())}
        except OSError:
            continue
    out = frozenset(found)
    _INDEX[key] = (time.monotonic(), out)
    while len(_INDEX) > 40:
        _INDEX.pop(next(iter(_INDEX)))
    return out


class Records:
    """One day's Bot signals and paper orders, read once from the saved files."""

    def __init__(self, artifacts: Path, day: str) -> None:
        self.artifacts, self.day = Path(artifacts), day
        self.signals: list[tuple[str, dict]] = []          # (side, trade)
        for side, folder in (("bull", "momx_option_track"), ("bear", Path("bear") / "momx_option_track")):
            doc = _read(self.artifacts / folder / f"{day}.json")
            for t in (doc.get("trades") if isinstance(doc, dict) else None) or []:
                if isinstance(t, dict) and t.get("symbol"):
                    self.signals.append((side, _slim(t)))
            del doc                                           # a day file is tens of megabytes: keep only the slim rows
        self.orders: dict[str, list[dict]] = {}
        self.day_docs: dict[str, dict] = {}
        for account, _ in ACCOUNTS:
            doc = _read(self.artifacts / "momx_paper_mirror" / account / f"{day}.json")
            doc = doc if isinstance(doc, dict) else {}
            self.day_docs[account] = doc
            rows = doc.get("rows") if isinstance(doc.get("rows"), dict) else {}
            self.orders[account] = [r for r in rows.values() if isinstance(r, dict)]

    def tickers(self) -> set[str]:
        out = {str(t.get("symbol")).upper() for _, t in self.signals}
        for rows in self.orders.values():
            out |= {str(r.get("symbol")).upper() for r in rows if r.get("symbol")}
        return out

    def for_ticker(self, ticker: str, side: str | None = None) -> list[tuple[str, dict]]:
        rows = [(s, t) for s, t in self.signals if str(t.get("symbol")).upper() == ticker and (side is None or s == side)]
        return sorted(rows, key=lambda st: str(st[1].get("at") or ""))


#: The few days most recently asked about, kept in memory: a day's Bot file is
#: several megabytes, and a conversation asks about the same day again and again.
_CACHE: dict[tuple, tuple] = {}
_CACHE_DAYS = 4


def records_for(artifacts: Path, day: str, today: str | None = None) -> Records:
    """One day's records. A past day is read once and kept. Today's files are rewritten by the Bot
    every half minute, so today is re-read at most once a minute, and only when a file changed."""
    artifacts = Path(artifacts)
    key = (str(artifacts), day)
    held = _CACHE.get(key)
    if held and today and day < today:
        return held[1]
    if held and time.monotonic() - held[2] < 60:
        return held[1]
    paths = [artifacts / "momx_option_track" / f"{day}.json", artifacts / "bear" / "momx_option_track" / f"{day}.json"]
    paths += [artifacts / "momx_paper_mirror" / account / f"{day}.json" for account, _ in ACCOUNTS]
    stamp = []
    for path in paths:
        try:
            stamp.append(path.stat().st_mtime)
        except OSError:
            stamp.append(None)
    if held and held[0] == stamp:
        _CACHE[key] = (stamp, held[1], time.monotonic())
        return held[1]
    records = Records(artifacts, day)
    _CACHE.pop(key, None)
    _CACHE[key] = (stamp, records, time.monotonic())
    while len(_CACHE) > _CACHE_DAYS:
        _CACHE.pop(next(iter(_CACHE)))
    return records


def known_tickers(artifacts: Path, day: str, today: str | None = None) -> set[str]:
    """Tickers the app has anything on: that day's Bot signals, plus every option chain it follows."""
    out = set(day_tickers(artifacts, day, today or day))
    try:
        out |= {p.name[: -len(".json.gz")].upper() for p in (Path(artifacts) / "oi_chain_cache").glob("*.json.gz")}
    except OSError:
        pass
    return out


def pick_signal(rows: list[tuple[str, dict]], minute: int | None) -> tuple[str, dict] | None:
    """The signal a question is about: a Level signal before any other rule, the
    highest level first; at a named time the first one at or after it, else the first of the day."""
    if not rows:
        return None

    def rank(st):
        rule = str(st[1].get("rule") or "")
        return (0 if rule in LEVELS else 1, -(LEVELS.index(rule) if rule in LEVELS else 0), str(st[1].get("at") or ""))

    def minute_of(st):
        try:
            at = datetime.fromisoformat(str(st[1].get("at"))).astimezone(ET)
            return at.hour * 60 + at.minute
        except (TypeError, ValueError):
            return None

    pool = rows
    if minute is not None:
        later = [st for st in rows if minute_of(st) is not None and minute_of(st) >= minute]
        if not later:
            return None                    # nothing at or after the time asked: never answer with an earlier one
        first = min(minute_of(st) for st in later)
        pool = [st for st in later if minute_of(st) <= first + 5]                  # the signals of that moment
    else:
        levels = [st for st in rows if st[1].get("rule") in LEVELS]
        pool = levels or rows
        first = min(str(st[1].get("at") or "") for st in pool)
        pool = [st for st in pool if str(st[1].get("at") or "") == first]
    return sorted(pool, key=rank)[0]


def _mid(bid: Any, ask: Any) -> float | None:
    b, a = _num(bid), _num(ask)
    return (a + b) / 2 if b is not None and a is not None and a > 0 and a >= b >= 0 else None


def contract_facts(t: Mapping) -> dict:
    """The 0.20-delta contract of a signal (what the paper bots buy), or the main one when it has none."""
    if t.get("eContract"):
        entry = _mid(t.get("eEntryBid"), t.get("eEntryAsk")) or _num(t.get("eEntry"))
        exit_bid = _num(t.get("eExitBid"))
        exit_ = 0.0 if exit_bid == 0 else (_mid(t.get("eExitBid"), t.get("eExitAsk")) or _num(t.get("eExit")))
        return {"which": "0.20-delta", "strike": _num(t.get("eStrike")), "delta": _num(t.get("eDelta")), "entry": entry,
                "best": _num(t.get("eBest")), "exit": exit_, "close": _num(t.get("eClose")), "last": _num(t.get("eLast")),
                "bid": _num(t.get("eEntryBid")), "ask": _num(t.get("eEntryAsk"))}
    entry = _mid(t.get("entryBid"), t.get("entryAsk")) or _num(t.get("entry"))
    return {"which": "nearest-strike", "strike": _num(t.get("strike")), "delta": _num(t.get("delta")), "entry": entry,
            "best": _num(t.get("best")), "exit": _num(t.get("exitPrice")), "close": _num(t.get("close")), "last": _num(t.get("last")),
            "bid": _num(t.get("entryBid")), "ask": _num(t.get("entryAsk"))}


def _gain(now: float | None, entry: float | None) -> float | None:
    return (now / entry - 1) * 100 if now is not None and entry else None


def _signal_line(side: str, t: Mapping) -> str:
    rule = RULE_NAMES.get(str(t.get("rule")), str(t.get("rule")))
    return f"{rule} {'put' if side == 'bear' else 'call'} signal at {_clock(t.get('at'))} with the stock at {_price(_num(t.get('stockPrice')))}"


def _strike_of(contract: Any) -> str:
    m = re.search(r"([CP])(\d{8})$", str(contract or "").replace(" ", ""))
    return f"the ${int(m.group(2)) / 1000:g} {'put' if m.group(1) == 'P' else 'call'}" if m else "contract not recorded"


def _order_text(r: Mapping) -> str:
    """What one paper order's record says happened. A buy still waiting on the broker is not called a miss."""
    fill, status = _num(r.get("buyFill")), r.get("status")
    sent = f"sent a buy at {_clock(r.get('buyAt'))} for {_price(_num(r.get('buyLimit')))}"
    if status == "missed":
        return sent + " that never filled."
    if fill is None:
        return sent + ("; no fill is recorded yet (still waiting on the broker)." if status == "buying"
                       else f"; the order is marked '{status}' with no fill recorded.")
    if status == "closed":
        return (f"bought {int(_num(r.get('buyFilledQty')) or _num(r.get('qty')) or 0)} at {_price(fill)}, "
                f"closed for {_money(_num(r.get('pl')))}.")
    return f"bought at {_price(fill)} and still holds {int(_num(r.get('held')) or 0)}."


def paper_orders(records: Records, side: str, t: Mapping) -> tuple[list[str], bool]:
    """(lines, was THIS signal ordered). An order belongs to a signal when it has the same rule and
    signal time; any other order on the ticker that day is listed apart, named by its own rule,
    time and contract, so a fill on another contract is never read as this signal's."""
    lines, taken = [], False
    for account, label in ACCOUNTS:
        # Orders signalled that day only: the day file also carries older, unresolved orders.
        same_ticker = [r for r in records.orders.get(account, []) if str(r.get("symbol")).upper() == str(t.get("symbol")).upper()
                       and (r.get("side") or "bull") == side and str(r.get("at") or "")[:10] == records.day]
        mine = [r for r in same_ticker if r.get("rule") == t.get("rule") and str(r.get("at")) == str(t.get("at"))]
        # A Level order is stored under its highest level; the other levels of the same moment are the same order.
        if not mine and t.get("rule") in LEVELS:
            mine = [r for r in same_ticker if r.get("rule") in LEVELS and str(r.get("at")) == str(t.get("at"))]
        others = [r for r in same_ticker if r not in mine]
        if mine:
            taken = True
            lines += [f"{label}: {_order_text(r)}" for r in sorted(mine, key=lambda r: str(r.get("buyAt") or ""))]
        else:
            lines.append(f"{label}: no order on this signal.")
        for r in sorted(others, key=lambda r: str(r.get("buyAt") or "")):
            rule = RULE_NAMES.get(str(r.get("rule")), str(r.get("rule")))
            lines.append(f"{label}, on a different signal ({rule} at {_clock(r.get('at'))}, {_strike_of(r.get('contract'))}): {_order_text(r)}")
    return lines, taken


def _paper_lines(records: Records, side: str, t: Mapping) -> list[str]:
    return paper_orders(records, side, t)[0]


# ---------------------------------------------------------------- the answers
def _none_after(ticker: str, day: str, minute: int, rows: list) -> str:
    asked = datetime(2000, 1, 1, minute // 60, minute % 60).strftime("%I:%M %p").lstrip("0")
    return (f"The Bot recorded no {ticker} signal at or after {asked} on {day}; its last one that day was at "
            f"{_clock(rows[-1][1].get('at'))}. Nothing to measure for that time.")


def answer_what_if(records: Records, ticker: str, minute: int | None, side: str | None) -> dict:
    rows = records.for_ticker(ticker, side)
    found = pick_signal(rows, minute)
    if not found:
        if rows and minute is not None:
            return {"lines": [_none_after(ticker, records.day, minute, rows)], "facts": {}}
        return {"lines": [f"The Bot recorded no {ticker} signal on {records.day}, so there is no trade to measure.",
                          "Ask about a day it did signal, or ask \"see " + ticker + "\" to list what is on file."], "facts": {}}
    s, t = found
    c = contract_facts(t)
    entry = c["entry"]
    lines = [f"{ticker} on {records.day}: {_signal_line(s, t)}."]
    if entry is None:
        return {"lines": lines + ["No option price was recorded for that signal, so the move cannot be measured."], "facts": {}}
    best, exit_, close = _gain(c["best"], entry), _gain(c["exit"], entry), _gain(c["close"], entry)
    strike = f"${c['strike']:g}" if c["strike"] is not None else "strike not recorded"
    lines.append(f"The {c['which']} {'put' if s == 'bear' else 'call'} ({strike}) cost {_price(entry)} at the mid.")
    if best is not None:
        lines.append(f"Its best price of the day was {_price(c['best'])}: {_pct(best)} from the entry.")
    if t.get("exitAt") and exit_ is not None:
        lines.append(f"The Bot's exit ({t.get('exitReason') or 'reason not recorded'}) at {_clock(t.get('exitAt'))} was {_price(c['exit'])}: {_pct(exit_)}.")
    elif t.get("status") == "open":
        lines.append(f"It is still open; the last price is {_price(c['last'])}: {_pct(_gain(c['last'], entry))}.")
    if close is not None:
        lines.append(f"Held to the close it was {_price(c['close'])}: {_pct(close)}.")
    if best is not None:
        lines.append("Your first target (+100%) was " + ("reached." if best >= 100 else f"not reached; the most it showed was {_pct(best)}."))
    at_exit = c["exit"] if c["exit"] is not None else c["close"]
    if at_exit is not None:
        lines.append(f"Three contracts at the mid would have been {_money((at_exit - entry) * 300)} at "
                     + ("the Bot's exit." if c["exit"] is not None else "the close."))
    lines.append("These are mid prices, not fills. The lowest price of the day is not recorded for this contract, so whether a -50% stop was hit first is unknown.")
    lines += _paper_lines(records, s, t)
    return {"lines": lines, "facts": {"entry": entry, "bestPct": best, "exitPct": exit_, "closePct": close, "rule": t.get("rule"), "side": s}}


def reasons_against(side: str, t: Mapping) -> list[str]:
    """What the chart and the option showed against the trade at the signal. Facts only."""
    from momx.paper_picker import chart_facts
    spot = _num(t.get("spot")) or _num(t.get("stockPrice"))
    f = chart_facts(side, t, spot)
    way = "below" if side == "bear" else "above"
    out = []
    vwap = f.get("vsVwapPct")
    if vwap is not None and (vwap > 0 if side == "bear" else vwap < 0):
        out.append(f"Price was on the wrong side of VWAP ({abs(vwap):.2f}% {'above' if side == 'bear' else 'below'} it; a {('put' if side == 'bear' else 'call')} wants it {way}).")
    emas = str(f.get("emasOnRightSide") or "")
    if emas and int(emas.split(" of ")[0]) * 2 < int(emas.split(" of ")[1]):
        out.append(f"Price was on the right side of only {emas} of the EMAs.")
    ahead = f.get("nextChartLevelAhead") or {}
    if ahead and ahead.get("awayPct", 9) < 0.5:
        out.append(f"A chart level sat right ahead: {ahead['level']}, only {ahead['awayPct']:.2f}% away.")
    walls = f.get("highOiWallsAhead") or []
    if walls and walls[0]["awayPct"] < 0.75:
        out.append(f"A high OI wall was just ahead at ${walls[0]['strike']:g} ({walls[0]['awayPct']:.2f}% away).")
    if str(t.get("momentum") or "").lower() == "fading":
        out.append("The 5-minute momentum was already fading at the signal.")
    c = contract_facts(t)
    if c["bid"] is not None and c["ask"] and c["entry"]:
        spread = (c["ask"] - c["bid"]) / c["entry"] * 100
        if spread >= 15:
            out.append(f"The option's spread was wide: {spread:.0f}% of its price, which a real fill has to pay.")
    spy = _num((t.get("market") or {}).get("spyPct")) if isinstance(t.get("market"), Mapping) else None
    if spy is not None and (spy > 0.3 if side == "bear" else spy < -0.3):
        out.append(f"The market was moving the other way (SPY {spy:+.2f}% on the day).")
    return out


def answer_why_failed(records: Records, ticker: str, minute: int | None, side: str | None) -> dict:
    rows = records.for_ticker(ticker, side)
    found = pick_signal(rows, minute)
    if not found:
        if rows and minute is not None:
            return {"lines": [_none_after(ticker, records.day, minute, rows)], "facts": {}}
        return {"lines": [f"The Bot recorded no {ticker} signal on {records.day}, so nothing failed that day."], "facts": {}}
    s, t = found
    c = contract_facts(t)
    result = _gain(c["exit"] if c["exit"] is not None else c["close"], c["entry"])
    lines = [f"{ticker} on {records.day}: {_signal_line(s, t)}."]
    if result is None:
        lines.append("No exit price is recorded for it yet, so it cannot be called a win or a loss.")
    elif result >= 0:
        lines.append(f"On the Bot's record this one did not fail: the {c['which']} contract was {_pct(result)} at the exit (mid prices).")
    else:
        held = None
        try:
            held = (datetime.fromisoformat(str(t.get("exitAt"))) - datetime.fromisoformat(str(t.get("entryAt") or t.get("at")))).total_seconds() / 60
        except (TypeError, ValueError):
            pass
        lines.append(f"It lost: the {c['which']} contract went from {_price(c['entry'])} to {_price(c['exit'] if c['exit'] is not None else c['close'])} ({_pct(result)})"
                     + (f", out after {held:.0f} minutes on \"{t.get('exitReason')}\"." if held is not None and t.get("exitReason") else "."))
        best = _gain(c["best"], c["entry"])
        if best is not None and best >= 30:
            lines.append(f"Its best price of the day was {_price(c['best'])} ({_pct(best)} from the entry). The time of that best is "
                         "not recorded for this contract, so it may have come after the Bot's exit.")
    against = reasons_against(s, t)
    if against:
        lines.append("What was against it at the signal:" if result is not None and result < 0 else "Not ideal at the signal, all the same:")
        lines += ["- " + a for a in against]
    else:
        lines.append("Nothing on the saved chart was clearly against it at the signal (VWAP, EMAs, levels, walls, spread and market were all in order or not recorded).")
    lines += _paper_lines(records, s, t)
    return {"lines": lines, "facts": {"resultPct": result, "against": len(against), "rule": t.get("rule"), "side": s}}


def _minute(stamp: Any) -> int | None:
    try:
        at = datetime.fromisoformat(str(stamp)).astimezone(ET)
        return at.hour * 60 + at.minute
    except (TypeError, ValueError):
        return None


def answer_why_missed(records: Records, ticker: str, minute: int | None, side: str | None) -> dict:
    rows = records.for_ticker(ticker, side)
    if not rows:
        return {"lines": [f"No rule fired on {ticker} on {records.day}: the Bot recorded no signal for it, so there was nothing to take."],
                "facts": {"step": "no signal"}}
    levels = [st for st in rows if st[1].get("rule") in LEVELS]
    if not levels:
        fired = sorted({str(t.get("rule")) for _, t in rows})
        return {"lines": [f"{ticker} had signals on {records.day}, but none was a Level signal (it fired: {', '.join(fired)}).",
                          "The paper accounts trade only Level 1, 2 and 3, so it was not taken."], "facts": {"step": "no level signal"}}
    picked = pick_signal(levels, minute)
    if picked is None:
        return {"lines": [_none_after(ticker, records.day, minute, levels).replace(" signal at or after", " Level signal at or after")],
                "facts": {"step": "no level signal at that time"}}
    s, t = picked
    lines = [f"{ticker} on {records.day}: {_signal_line(s, t)}."]
    order_lines, taken = paper_orders(records, s, t)
    others = [line for line in order_lines if "on a different signal" in line]
    if taken:
        return {"lines": lines + ["It was not skipped. This is what each paper account did:"] + order_lines, "facts": {"step": "ordered"}}
    from momx import bot_desk
    if records.day < bot_desk.EVALUATION_START:
        return {"lines": lines + [f"Neither paper account ordered this signal. On {records.day} the paper accounts were still on the older "
                                  f"rules and did not trade Level signals at all; they started on {bot_desk.EVALUATION_START}."] + others,
                "facts": {"step": "before the Level rules"}}
    at = _minute(t.get("at"))
    c = contract_facts(t)
    why, step = [], ""
    if at is not None and not (570 <= at < 660 or 780 <= at < 930):
        why.append(f"The signal came at {_clock(t.get('at'))}, outside the buy windows (9:30-11:00 AM and 1:00-3:30 PM ET).")
        step = "outside the buy windows"
    if not t.get("eContract"):
        recorded = " ".join(str(t.get("why") or "").split())[:160]
        why.append("The Bot recorded no 0.20-delta option for this signal, and the paper accounts buy only that contract. "
                   + (f"Its recorded reason: \"{recorded}\"." if recorded else "The reason is not recorded."))
        step = step or "no 0.20-delta contract"
    elif c["bid"] is not None and c["bid"] <= 0:
        why.append(f"Nobody was bidding for the 0.20-delta option (bid $0.00, ask {_price(c['ask'])}); it could not have been sold back.")
        step = step or "no bid"
    elif c["entry"] is not None and c["entry"] < 0.05:
        why.append(f"The 0.20-delta option was under $0.05 ({_price(c['entry'])}), below the minimum price.")
        step = step or "too cheap"
    elif c["entry"] is not None and c["entry"] > 8:
        why.append(f"The 0.20-delta option cost {_price(c['entry'])}, over the $8.00 maximum.")
        step = step or "too expensive"
    for account, label in ACCOUNTS:
        orders = records.orders.get(account, [])
        stop = records.day_docs.get(account, {}).get("dailyStop")
        if isinstance(stop, dict) and at is not None and (_minute(stop.get("at")) or 10 ** 6) <= at:
            why.append(f"{label} had hit its $500 daily loss stop at {_clock(stop.get('at'))}: no new buys after that.")
            step = step or "daily loss stop"
            continue
        if at is None:
            continue
        live = sum(1 for r in orders if (_minute(r.get("buyAt")) or 10 ** 6) <= at and r.get("status") != "missed"
                   and ((_minute(r.get("closedAt")) or 10 ** 6) > at or r.get("carried")))
        sent = sum(1 for r in orders if r.get("buyId") and not r.get("carried") and (_minute(r.get("buyAt")) or 10 ** 6) <= at)
        if sent >= 25:
            why.append(f"{label} had already used its 25 buys for the day by then.")
            step = step or "daily buy limit"
        elif live >= 8:
            why.append(f"{label} had all 8 slots in use at that moment ({live} positions open).")
            step = step or "slots full"
    if not why:
        why.append("The records do not show a clear reason. The usual ones are a quote that was not fresh at that second, "
                   "or another signal on the same moment taking the slot.")
        step = "not clear from the records"
    return {"lines": lines + ["Neither paper account ordered this signal. Why:"] + ["- " + w for w in why] + others, "facts": {"step": step}}


def answer_show(records: Records, ticker: str, side: str | None) -> dict:
    rows = records.for_ticker(ticker, side)
    if not rows:
        return {"lines": [f"Nothing on file for {ticker} on {records.day}: the Bot recorded no signal and the paper accounts sent no order."],
                "facts": {"signals": 0}}
    lines = [f"{ticker} on {records.day}: {len(rows)} signal{'s' if len(rows) != 1 else ''} on file."]
    for s, t in rows[:12]:
        c = contract_facts(t)
        result = _gain(c["exit"] if c["exit"] is not None else c["last"], c["entry"])
        state = "open" if t.get("status") == "open" else str(t.get("exitReason") or t.get("status") or "")
        lines.append(f"- {_clock(t.get('at'))} · {RULE_NAMES.get(str(t.get('rule')), str(t.get('rule')))} · {'put' if s == 'bear' else 'call'} · "
                     f"entry {_price(c['entry'])} · {_pct(result)} · {state}")
    if len(rows) > 12:
        lines.append(f"... and {len(rows) - 12} more.")
    first = pick_signal(rows, None)
    lines += _paper_lines(records, first[0], first[1])
    return {"lines": lines, "facts": {"signals": len(rows)}}


def answer_status(artifacts: Path, today: str) -> dict:
    from momx import bot_desk
    doc = bot_desk.build(artifacts, 90, today)
    lines = [f"Evaluation since {doc.get('startDay')}: the paper accounts trade only {doc.get('strategy')}."]
    for row in (doc.get("versus") or {}).get("rows") or []:
        acct = (doc.get("accounts") or {}).get(row["account"]) or {}
        control = acct.get("control") or {}
        state = "PAUSED" if control.get("paused") else "daily loss stop hit" if (control.get("dailyStop") or {}).get("hit") else "buying"
        lines.append(f"{row['name']}: {row['closed']} closed trades, {_money(_num(row['pnl']) if row['closed'] else None) if row['closed'] else 'no result yet'}, "
                     f"balance ${_num(row['balance']) or 0:,.0f}, {state}; today {_money(_num((control.get('dailyStop') or {}).get('todayPnl')))}; "
                     f"{(acct.get('orders') or {}).get('holding', 0)} open, {(acct.get('orders') or {}).get('stuck', 0)} stuck.")
    v = doc.get("versus") or {}
    if v.get("leader"):
        name = next(r["name"] for r in v["rows"] if r["account"] == v["leader"])
        lines.append(f"{name} is ahead on real fills.")
    else:
        lines.append(f"Neither bot is called ahead yet: each needs {v.get('need', 20)} closed real trades, with the AI choosing at least half of the AI bot's.")
    if not doc.get("available"):
        lines = ["No paper orders are on file yet, so there is nothing to report."]
    return {"lines": lines, "facts": {"leader": v.get("leader")}}


def _other_side_note(records: "Records", ticker: str, side: str, named: bool) -> str | None:
    """When the answer is about one side and the other side also signalled that day, say so."""
    if named:
        return None
    other = "bear" if side == "bull" else "bull"
    rows = records.for_ticker(ticker, other)
    if not rows:
        return None
    word = "put" if other == "bear" else "call"
    return (f"{ticker} also had {len(rows)} {word} signal{'s' if len(rows) != 1 else ''} that day, the first at {_clock(rows[0][1].get('at'))}. "
            f"Add \"{word}\" to ask about those.")


def ask(question: str, artifacts: Path, now: datetime | None = None, context: Mapping | None = None) -> dict:
    """One question in, one answer out. Never raises on odd input; never writes.

    `context` is what the conversation was last about ({"ticker", "day"}, sent back by the screen), so a
    follow-up such as "profit or loss?" or "why did it fail" continues with the same ticker and day."""
    now = (now or datetime.now(ET)).astimezone(ET)
    text = " ".join(str(question or "").split())[:300]
    if not text:
        return {"intent": "help", "ticker": None, "day": None, "lines": ["Ask me about a ticker or about the bot."], "suggest": EXAMPLES}
    intent = parse_intent(text)
    wants_result = bool(re.search(RESULT, text.lower()))
    day, named = parse_day(text, now.date())
    last = context if isinstance(context, Mapping) else {}
    last_ticker = str(last.get("ticker") or "").upper()
    last_ticker = last_ticker if re.fullmatch(r"[A-Z.]{1,6}", last_ticker) else ""
    last_day = str(last.get("day") or "")
    try:
        last_day = date.fromisoformat(last_day).isoformat() if last_day else ""
    except ValueError:
        last_day = ""
    # A ticker is recognised if the app has it on the asked day OR today (or follows its option chain),
    # so "AMD on 11-06" is understood even when 11-06 has nothing on AMD.
    today = now.date().isoformat()
    on_file = set(day_tickers(artifacts, day.isoformat(), today)) | set(day_tickers(artifacts, today, today))
    known = known_tickers(artifacts, day.isoformat(), today) | on_file
    ticker = parse_ticker(text, known, on_file)
    carried = False
    # A follow-up with no ticker continues with the one the conversation is about - unless the
    # question is plainly about the bot itself ("what is the bot doing", "which bot is ahead").
    about_bot = bool(re.search(r"\b(bots?|strategy|strategies|scorecard|account|accounts|positions?|ahead|paused?)\b", text.lower()))
    if ticker is None and last_ticker and not about_bot:
        ticker, carried = last_ticker, True
        if not named and last_day:
            day, named = date.fromisoformat(last_day), True
    if wants_result:
        intent = "what_if" if ticker else "status"
    elif ticker and carried and intent in ("help", "status"):
        intent = "show"
    if ticker is None and intent != "status":
        intent = "status" if intent == "status" else "help"
    base = {"intent": intent, "ticker": ticker, "day": day.isoformat()}
    if intent == "status" or (intent == "help" and ticker is None and re.search(r"\b(bot|strategy|scorecard)\b", text.lower())):
        return {**base, "intent": "status", **answer_status(Path(artifacts), now.date().isoformat()), "suggest": EXAMPLES[:3]}
    if ticker is None:
        return {**base, "lines": ["I did not find a ticker I have records for in that question.",
                                  "I can answer: how far a signal went, why a trade failed, why a ticker was missed, what is on file for a ticker, and what the bot is doing.",
                                  "Type a one- or two-letter ticker, or one that is also an everyday word, in capitals or with a $ (T, GS, $COST)."],
                "suggest": EXAMPLES}
    # No day was named and today has nothing on this ticker: use the last day that has. The search
    # reads each day's ticker list only (cheap); a whole day is loaded once it is known to have it.
    use = day.isoformat()
    if not named and ticker not in day_tickers(artifacts, use, today):
        for back in range(1, 8):
            earlier = (day - timedelta(days=back)).isoformat()
            if ticker in day_tickers(artifacts, earlier, today):
                use = earlier
                break
    records = records_for(Path(artifacts), use, today)
    minute, side = parse_time(text), parse_side(text)
    side_named = side is not None
    last_side = last.get("side") if last.get("side") in ("bull", "bear") else None
    if side is None and carried and last_side and records.for_ticker(ticker, last_side):
        side, side_named = last_side, True      # the follow-up stays on the calls (or puts) the conversation was about
    if side is None:
        # "how much it went up" means the calls - when there are call signals to answer with.
        hint = hinted_side(text)
        if hint and records.for_ticker(ticker, hint):
            side = hint
    if intent == "what_if":
        out = answer_what_if(records, ticker, minute, side)
    elif intent == "why_failed":
        out = answer_why_failed(records, ticker, minute, side)
    elif intent == "why_missed":
        out = answer_why_missed(records, ticker, minute, side)
    else:
        intent = "show"
        out = answer_show(records, ticker, side)
    answered = (out.get("facts") or {}).get("side")
    if answered and intent != "show":
        note = _other_side_note(records, ticker, answered, side_named)
        if note:
            out = {**out, "lines": out["lines"] + [note]}
    follow = {"what_if": [f"why {ticker} failed", f"why was {ticker} missed"], "why_failed": [f"{ticker} if I take how much it went up", f"see {ticker}"],
              "why_missed": [f"{ticker} if I take how much it went up", "what is the bot doing"],
              "show": [f"{ticker} if I take how much it went up", f"why {ticker} failed", f"why was {ticker} missed"]}[intent]
    return {**base, "intent": intent, "day": records.day, "side": answered if intent != "show" else side, **out, "suggest": follow}
