"""Upcoming market events for the next 7 days (2026-11-11), for My Trading and the chat.

Neither data key the app holds offers an economic calendar (FMP answered 402 and Finnhub 403 on
2026-11-11). The app already reads two calendars, and this keeps a small copy of both:

* economic: the Forex Factory week feed the News Feed uses (USD events, this Monday-Friday only,
  so events after Friday are not known until the next week's feed);
* earnings: the Earnings Calendar's rows for the tickers on the watchlist.

The copy is refreshed at most every 6 hours by api_server's insights thread, from the app's own
cached calendar functions. read() and the chat only read the saved copy, never the network.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Callable

from momx import schwab_history

ET = schwab_history.ET
REFRESH_SECONDS = 6 * 3600
HORIZON_DAYS = 7
#: What counts as high impact even when the feed rates it "Medium".
KEY_EVENTS = re.compile(r"\b(?:FOMC|Federal Funds|Fed\b|Powell|CPI|PPI|Non-?Farm|Employment|Unemployment|Jobless|GDP|Retail Sales|Minutes|PCE)", re.I)
ECONOMIC_NOTE = "Economic events come from the Forex Factory week feed, which covers Monday to Friday of this week only."
ECONOMIC_DOWN = "economic calendar not available (Forex Factory did not answer)"
#: Words a trader asks with -> the words Forex Factory titles use ("jobs report" is "Non-Farm Employment Change").
ALIASES = (
    (re.compile(r"\b(?:jobs?|nfp|non-?farm|payrolls?|employment|unemployment|jobless)\b", re.I),
     re.compile(r"Non-?Farm|Employment|Unemployment|Jobless", re.I)),
    (re.compile(r"\b(?:fed|fomc|rate\s+decisions?|interest\s+rates?|powell)\b", re.I),
     re.compile(r"FOMC|Federal Funds|Powell", re.I)),
    (re.compile(r"\binflation\b", re.I), re.compile(r"CPI|PCE", re.I)),
)


def economic_failed(payload) -> bool:
    """The News Feed's Forex Factory function does not raise when the fetch fails: it answers
    {"weeklyEvents": [], "errors": [...], "live": False}. That is "not available", never "no events"."""
    if not isinstance(payload, dict):
        return True
    return bool(payload.get("errors")) or payload.get("live") is False


def path() -> Path:
    return schwab_history.root() / "market_events.json"


def read(now: datetime | None = None) -> dict:
    """The saved copy, trimmed to the next 7 days. Never calls the network."""
    now = (now or datetime.now(ET)).astimezone(ET)
    doc = schwab_history._read_json(path(), {})
    today, end = now.date().isoformat(), (now.date() + timedelta(days=HORIZON_DAYS)).isoformat()
    items = [e for e in doc.get("items") or [] if isinstance(e, dict) and today <= str(e.get("date") or "") <= end]
    if not doc:
        note = "Market events have not been read yet."
    else:
        note = " ".join(n for n in (doc.get("note"), ECONOMIC_NOTE if doc.get("economic") else "") if n)
    out = {"items": items, "note": note, "refreshedAt": doc.get("refreshedAt"), "source": doc.get("source") or ""}
    if doc.get("economicError"):
        out["economicError"] = doc["economicError"]
    return out


def due(now: datetime | None = None) -> bool:
    now = (now or datetime.now(ET)).astimezone(ET)
    stamp = schwab_history._read_json(path(), {}).get("refreshedAt")
    try:
        return (now - datetime.fromisoformat(stamp)).total_seconds() >= REFRESH_SECONDS
    except (TypeError, ValueError):
        return True


def economic_items(payload: dict | None) -> list[dict]:
    out = []
    for e in (payload or {}).get("weeklyEvents") or []:
        if not isinstance(e, dict):
            continue
        title = str(e.get("title") or "").strip()
        impact = str(e.get("impact") or "").title()
        if not title or not (impact == "High" or KEY_EVENTS.search(title)):
            continue
        out.append({"at": e.get("time"), "date": str(e.get("date") or str(e.get("time") or "")[:10]), "title": title,
                    "kind": "economic", "impact": "High" if KEY_EVENTS.search(title) else impact, "symbol": None,
                    "forecast": e.get("forecast") or "", "previous": e.get("previous") or ""})
    return out


def earnings_items(payload: dict | None) -> list[dict]:
    out = []
    for r in (payload or {}).get("rows") or []:
        if not isinstance(r, dict) or not r.get("symbol") or not r.get("date"):
            continue
        timing = str(r.get("timing") or "").strip()
        out.append({"at": str(r["date"]), "date": str(r["date"]), "title": f"{r['symbol']} earnings" + (f" ({timing})" if timing else ""),
                    "kind": "earnings", "impact": "High", "symbol": str(r["symbol"]).upper()})
    return out


def refresh(economic: Callable[[], dict | None], earnings: Callable[[], dict | None], now: datetime | None = None) -> dict:
    """Read the app's two calendars (each through its own cache) and save the next 7 days."""
    now = (now or datetime.now(ET)).astimezone(ET)
    errors, econ, earn = [], [], []
    econ_ok = False
    try:
        payload = economic()
        if economic_failed(payload):
            errors.append(ECONOMIC_DOWN)
        else:
            econ, econ_ok = economic_items(payload), True
    except Exception as exc:  # noqa: BLE001
        errors.append(f"economic calendar unavailable ({type(exc).__name__})")
    previous = schwab_history._read_json(path(), {})
    kept_from = None
    if not econ_ok and (previous.get("economic") or previous.get("economicAt")):
        # Keep the last good copy's economic events (still dated ahead) rather than saving over them with nothing.
        econ = [e for e in previous.get("items") or [] if isinstance(e, dict) and e.get("kind") == "economic"]
        kept_from = previous.get("economicAt") or previous.get("refreshedAt")
    try:
        earn = earnings_items(earnings())
    except Exception as exc:  # noqa: BLE001
        errors.append(f"earnings calendar unavailable ({type(exc).__name__})")
    end = (now.date() + timedelta(days=HORIZON_DAYS)).isoformat()
    items = sorted((e for e in econ + earn if now.date().isoformat() <= e["date"] <= end), key=lambda e: (e["date"], str(e["at"]), e["title"]))
    joined = "; ".join(errors)
    note = joined[:1].upper() + joined[1:] + "." if errors else ""
    if kept_from and econ:
        note += f" Economic events shown are from the copy read at {str(kept_from)[5:16].replace('T', ' ')} ET."
    doc = {"refreshedAt": now.isoformat(timespec="seconds"), "items": items, "economic": econ_ok,
           "economicAt": now.isoformat(timespec="seconds") if econ_ok else kept_from,
           "source": "Forex Factory (economic) + the app's Earnings Calendar", "note": note.strip()}
    if not econ_ok:
        doc["economicError"] = errors[0] if errors else ECONOMIC_DOWN
    schwab_history._write_json(path(), doc)
    return doc


def matches(event: dict, asked: str) -> bool:
    """Does a saved event answer the asked word? Plain words first go through ALIASES ("jobs report",
    "NFP" -> Non-Farm Employment Change; "FOMC", "Fed" -> the Federal Funds Rate decision and the minutes),
    then a plain substring of the title or the earnings ticker."""
    word = str(asked or "").strip().lower()
    title = str(event.get("title") or "")
    if not word:
        return True
    if word in title.lower() or (event.get("symbol") or "").lower() == word:
        return True
    return any(ask.search(word) and target.search(title) for ask, target in ALIASES)


def chat_lines(doc: dict, asked: str | None = None, today: date | None = None) -> list[str]:
    """Plain lines for the chat's market_events tool."""
    items = doc.get("items") or []
    if asked:
        items = [e for e in items if matches(e, asked)]
    if not items:
        asked_text = str(asked or "").strip()
        economic_ask = bool(asked_text) and (bool(KEY_EVENTS.search(asked_text)) or any(a.search(asked_text) for a, _ in ALIASES)
                                             or not re.fullmatch(r"[A-Z]{1,5}", asked_text))
        if doc.get("economicError") and (economic_ask or not asked_text):
            what = (f"{asked_text}: the economic calendar is not available right now (Forex Factory did not answer)." if asked_text
                    else "The economic calendar is not available right now (Forex Factory did not answer).")
            rest = str(doc.get("note") or "")
            if rest.lower().rstrip(".") == str(doc["economicError"]).lower():
                rest = ""                               # the first line already says it
            return [what] + ([rest] if rest else [])
        what = f"No {asked} on the saved calendar for the next 7 days." if asked else "Nothing on the saved calendar for the next 7 days."
        return [what] + ([doc["note"]] if doc.get("note") else [])
    lines = []
    for e in items[:12]:
        clock = str(e.get("at") or "")[11:16]
        lines.append(f"{e['date'][5:]} {clock + ' ET ' if clock else ''}{e['title']}" + (f" (forecast {e['forecast']})" if e.get("forecast") else ""))
    if doc.get("note"):
        lines.append(doc["note"])
    return lines
