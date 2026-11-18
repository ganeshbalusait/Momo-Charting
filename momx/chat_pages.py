"""The app's pages, for the Ask chat (2026-11-11): his "It will take me that page or link".

One row per page in the left rail: its view key, what a trader calls it, whether only the owner may
open it, and which details a link to it may carry. Every link the chat sends is built and checked
here, from values the server has already validated; the AI never writes one. A link is a button that
moves the screen, so nothing here can place, change or cancel a trade.

A parity test holds this list to `optionNavItems` in frontend/src/App.jsx.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Mapping, NamedTuple


class Page(NamedTuple):
    view: str
    label: str
    aliases: tuple[str, ...]
    deep: tuple[str, ...] = ()
    admin_only: bool = False


PAGES: tuple[Page, ...] = (
    Page("Charts & OI", "Charts & OI", ("chart", "charts", "levels"), ("ticker", "timeframe")),
    Page("Quick Options", "Options", ("options", "option chain", "chain", "high oi", "walls"), ("ticker", "sub")),
    Page("Auto Alert", "Alerts", ("alerts", "oi alerts", "auto alert")),
    Page("Premarket Scanner", "Premarket", ("premarket", "pre market", "pre-market", "premarket scanner"), ("day",)),
    Page("MomX Scanner", "MomX Scanner", ("scanner", "momx", "momx scanner", "mag7 board", "watchlist board", "history", "history find", "find", "filters"),
         ("ticker", "list", "side", "tab", "day", "time")),
    Page("Bot", "Bot", ("bot", "desk", "scorecard", "ask tab"), ("sub",), True),
    Page("My Trading", "My Trading", ("my trading", "my trades", "my insights", "my real trades"), (), True),
    Page("Long Term", "Long Term", ("buffett", "value", "long term", "value screen"), ("ticker",)),
    Page("Option Paper Trading", "Option Paper Trading", ("option paper", "option paper trading"), (), True),
    Page("Paper Trading", "Stock Paper Trading", ("stock paper", "paper", "paper trading"), (), True),
    Page("Learn + Setup", "Learn + Setup", ("learn", "help", "how to", "setup page"), ()),
    Page("Watchlist", "Watchlist", ("watchlist", "my tickers"), ()),
    Page("Sectors", "Sectors", ("sectors", "heatmap", "heat map", "sector"), ()),
    Page("LEAP OI Scanner", "LEAP OI Scanner", ("leap oi scanner", "leap scanner", "leap oi", "leaps"), ()),
    Page("AGX Flow", "AGX Flow", ("agx flow", "options flow", "option flow", "unusual options", "unusual flow", "whale flow"), ()),
    Page("News Feed", "News Feed", ("news", "headlines", "news feed"), ("ticker",)),
    Page("Earnings Calendar", "Earnings Calendar", ("earnings", "earnings calendar"), ()),
    Page("Mag7 Scanner", "Mag7 Watchlist", ("mag7 watchlist", "mag7 scanner"), ()),
    Page("ROI Calc", "ROI Calc", ("roi", "roi calc", "roi calculator"), ()),
    Page("OI Level Script TOS", "OI Level Scripts", ("tos script", "oi script", "oi level script", "oi level scripts"), ("ticker",)),
    Page("Release Notes", "Release Notes", ("release notes", "what changed", "what's new", "whats new"), ()),
    Page("Settings", "Settings", ("settings", "password", "account"), ()),
)
BY_VIEW = {page.view: page for page in PAGES}
ADMIN_VIEWS = frozenset(page.view for page in PAGES if page.admin_only)
#: Aliases that only count for a page when the question plainly asks to go there; a bare word means too many things.
_LOOSE = {"find", "value", "help", "history", "account"}
#: History tab of the scanner, when the trader asks for it by name.
_DEEP_ALIASES = {"history": {"tab": "history"}, "history find": {"tab": "history"}, "find": {"tab": "history"},
                 "high oi": {"sub": "high-oi"}, "walls": {"sub": "high-oi"},
                 "mag7 board": {"list": "Mag7"}, "watchlist board": {"list": "Watchlist"},
                 "desk": {"sub": "desk"}, "scorecard": {"sub": "desk"}, "ask tab": {"sub": "ask"}}

TIMEFRAMES = ("3m", "5m", "10m", "15m", "30m", "1h", "2h", "4h", "D", "W", "M")
TICKER = re.compile(r"\$?[A-Z][A-Z0-9.\-]{0,11}")
_LIST = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &+.\-_/()#%:]{0,47}")
_SUB = re.compile(r"[A-Za-z0-9][A-Za-z0-9_\-]{0,31}")
_CLOCK = re.compile(r"(?:[01]\d|2[0-3]):[0-5]\d")
_LABEL_MAX = 80
MAX_LINKS = 6


def visible(scope: str) -> tuple[Page, ...]:
    """The pages this session may be told about."""
    return PAGES if scope == "owner" else tuple(page for page in PAGES if not page.admin_only)


def _real_day(value: Any) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def clean_link(raw: Any, scope: str = "scanner") -> dict | None:
    """A link as the app may follow it, with nothing but the known fields; None if any field is wrong
    or the page is not this session's. Same rules as cleanLink in frontend/src/appLinks.js."""
    if not isinstance(raw, Mapping) or raw.get("view") not in BY_VIEW:
        return None
    if raw["view"] in ADMIN_VIEWS and scope != "owner":
        return None
    label = raw.get("label") if raw.get("label") not in (None, "") else raw["view"]
    if not isinstance(label, str):
        return None
    shown = " ".join(re.sub(r"[\x00-\x1f\x7f]", " ", label).split())[:_LABEL_MAX]
    if not shown:
        return None
    link: dict = {"label": shown, "view": raw["view"]}
    checks = (("timeframe", lambda v: v in ("1m", "2m", *TIMEFRAMES)), ("list", lambda v: isinstance(v, str) and _LIST.fullmatch(v)),
              ("side", lambda v: v in ("bull", "bear")), ("tab", lambda v: v in ("live", "history")), ("day", _real_day),
              ("time", lambda v: isinstance(v, str) and _CLOCK.fullmatch(v)), ("sub", lambda v: isinstance(v, str) and _SUB.fullmatch(v)))
    ticker = raw.get("ticker")
    if ticker not in (None, ""):
        ticker = ticker.strip().upper() if isinstance(ticker, str) else ""
        if not TICKER.fullmatch(ticker):
            return None
        link["ticker"] = ticker
    for field, valid in checks:
        value = raw.get(field)
        if value in (None, ""):
            continue
        if not valid(value):
            return None
        link[field] = value
    return link


def clean_links(raw: Any, scope: str = "scanner", most: int = MAX_LINKS) -> list[dict]:
    """The links of a reply that are safe for this session, at most `most`, without repeats."""
    out, seen = [], set()
    for item in raw if isinstance(raw, (list, tuple)) else []:
        link = clean_link(item, scope)
        if link is None:
            continue
        key = tuple(sorted((k, v) for k, v in link.items() if k != "label"))
        if key in seen:
            continue
        seen.add(key)
        out.append(link)
        if len(out) >= most:
            break
    return out


def link(view: str, label: str | None = None, **deep: Any) -> dict:
    """A link to a page. Details the page cannot use are left off."""
    page = BY_VIEW[view]
    out = {"label": label or page.label, "view": view}
    out.update({key: value for key, value in deep.items() if key in page.deep and value not in (None, "")})
    return out


def find(text: str, scope: str = "owner", loose: bool = False) -> tuple[Page, dict] | None:
    """The page a sentence names, longest name first, and any detail its name implies. `loose` also
    accepts words that mean several things ("find", "help"). A page this session may not see is not found."""
    low = " ".join(str(text or "").lower().split())
    hits = []
    for page in visible(scope):
        for alias in (page.view.lower(), page.label.lower(), *page.aliases):
            if alias in _LOOSE and not loose:
                continue
            if re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(alias), low):
                hits.append((len(alias), alias, page))
    if not hits:
        return None
    _, alias, page = max(hits, key=lambda hit: hit[0])
    return page, dict(_DEEP_ALIASES.get(alias, {}))


def owner_page_named(text: str) -> bool:
    """Does a sentence name a page only the owner may open? (Asked for a regular user, to say no without a link.)"""
    low = " ".join(str(text or "").lower().split())
    return any(re.search(r"(?<![a-z0-9])%s(?![a-z0-9])" % re.escape(alias), low)
               for page in PAGES if page.admin_only for alias in (page.view.lower(), page.label.lower(), *page.aliases))


def valid_page_name(value: Any) -> str | None:
    """A page name sent by the screen, only if it is one of ours."""
    return value if isinstance(value, str) and value in BY_VIEW else None


def chips_for(page_name: str | None, ticker: str | None, scope: str = "scanner") -> list[str]:
    """Question chips for the page on screen."""
    t = ticker or ""
    by_page = {
        "Charts & OI": [f"{t} chart levels", f"{t} signals on 5m", f"call wall on {t}", f"news on {t}"] if t else ["What level we have in chart", "which sector is hot"],
        "Quick Options": [f"Option chain {t}", f"expected move {t}", f"max pain {t}", f"call wall on {t}"] if t else ["Option chain", "which sector is hot"],
        "MomX Scanner": [f"{t} today scanner", "which tickers stayed on top today", "top 5 mag7 at 10:15"] if t else ["which tickers stayed on top today", "top 5 mag7 at 10:15", "what was hot at noon"],
        "Premarket Scanner": ["premarket today", "which sector is hot"],
        "Sectors": ["which sector is hot", "premarket today"],
        "News Feed": [f"news on {t}", "which sector is hot"] if t else ["which sector is hot", "premarket today"],
        "Earnings Calendar": [f"when is {t} earnings"] if t else ["premarket today"],
        "Long Term": [f"long term {t}"] if t else ["premarket today"],
    }
    chips = list(by_page.get(page_name or "", []))
    if not chips:
        chips = [f"{t} chart levels", f"{t} today scanner", f"news on {t}"] if t else ["which tickers stayed on top today", "which sector is hot", "premarket today"]
    if scope == "owner":
        chips.append("what is the bot doing")
    return chips[:6]
