"""The Ask chat's tools (2026-11-11): every lookup the chat can make, and who may make it.

Privacy lives here. A session is offered only the tools of its scope, each handler checks the scope
again, and every link is built and checked by the server. A regular user's session never sees an
owner tool's name, let alone its answer.

The tools only read: caches and saved files, never a page route, so a question cannot start a Schwab
call, a chart build, a refresh or a phone push, and nothing here can place, change or cancel a trade.

Each module's TOOLS maps a name to {"scope", "description", "args" (JSON schema), "handler"}, and a
handler is fn(args, ctx) -> {"lines", "facts", "links", "table", "asOf"}. ctx is {"scope", "account",
"pageTicker", "pageName", "ticker", "day", "side", "now", "artifacts"}.
"""
from __future__ import annotations

import importlib
import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping

from momx import chat_pages

#: Modules that bring their own tools. Loaded when first needed, and one that is not there just adds none.
_MODULES = ("momx.chat_scanner", "momx.chat_market", "momx.chat_chart", "momx.chat_rag", "momx.chat_why", "momx.chat_report", "momx.chat_glossary")
TICKER = re.compile(r"[A-Z][A-Z.]{0,5}")
MAX_TABLE_ROWS = 40
_CELL_MAX = 160
_MAG_BOARDS = ("Mag7", "Watchlist")
#: Where a tool's answer lives, for the "which ticker?" reply.
_TOOL_PAGE = {"chain": "Quick Options", "walls": "Quick Options", "expected_move": "Quick Options", "price_outlook": "Quick Options", "max_pain": "Quick Options",
              "chart_levels": "Charts & OI", "chart_signals": "Charts & OI", "quote": "Charts & OI", "open_chart": "Charts & OI",
              "news": "News Feed", "latest_news": "News Feed", "why_move": "Charts & OI", "earnings": "Earnings Calendar", "long_term": "Long Term", "sectors": "Sectors", "premarket": "Premarket Scanner",
              "scanner_day": "MomX Scanner", "scanner_ticker": "MomX Scanner", "scanner_list": "MomX Scanner", "scanner_top": "MomX Scanner"}
#: Large, liquid names every session can type in lower case without any file saying so.
STATIC_TICKERS = frozenset("""SPY QQQ IWM DIA AAPL MSFT NVDA AMZN GOOG GOOGL META TSLA AMD AVGO NFLX ORCL CRM ADBE INTC MU QCOM TXN PLTR
COIN HOOD SOFI UBER SHOP SNOW ZETA CMCSA BAC JPM GS WFC XOM CVX BA DIS WMT TGT COST HD LOW NKE SBUX MCD PFE MRK LLY UNH""".split())


def empty(line: str, *links: dict) -> dict:
    return {"lines": [line], "facts": {}, "links": list(links), "table": None, "asOf": None, "suggest": []}


# ---------------------------------------------------------------- the registry
def _external() -> dict:
    found: dict = {}
    for name in _MODULES:
        try:
            module = importlib.import_module(name)
        except Exception:  # noqa: BLE001 - a builder's module that is missing or broken must not stop the chat
            continue
        tools = getattr(module, "TOOLS", None)
        if isinstance(tools, Mapping):
            found.update(tools)
    return found


def registry(scope: str) -> dict:
    """The tools this session may use. Anything but "owner" gets the "all" tools only."""
    tools = {**_external(), **OWN_TOOLS}
    return {name: entry for name, entry in tools.items()
            if isinstance(entry, Mapping) and callable(entry.get("handler")) and (entry.get("scope") == "all" or (scope == "owner" and entry.get("scope") == "owner"))}


def planner_catalog(scope: str) -> list[dict]:
    """The tool list as the planner sees it."""
    return [{"tool": name, "does": str(entry.get("description") or "")[:240], "args": (entry.get("args") or {}).get("properties", {})}
            for name, entry in sorted(registry(scope).items())]


# ---------------------------------------------------------------- checking what comes in and out
_DAY_WORD = re.compile(r"\d{1,2}/\d{1,2}|today|tomorrow|yesterday|(?:last\s+|next\s+)?(?:mon|tues|wednes|thurs|fri)day")


def _real_day(value: Any) -> str | None:
    try:
        return date.fromisoformat(value).isoformat() if isinstance(value, str) and re.fullmatch(r"20\d\d-\d\d-\d\d", value) else None
    except ValueError:
        return None


def _clean_value(key: str, spec: Mapping, value: Any) -> Any:
    """One argument after checking, or None if it is not acceptable."""
    kind = spec.get("type")
    if kind == "boolean":
        return value if isinstance(value, bool) else None
    if kind in ("integer", "number"):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or (kind == "integer" and not isinstance(value, int)):
            return None
        if ("minimum" in spec and value < spec["minimum"]) or ("maximum" in spec and value > spec["maximum"]):
            return None
        return value
    if not isinstance(value, str):
        return None
    text = " ".join(value.split())[: int(spec.get("maxLength") or 64)]
    if not text:
        return None
    if key == "ticker":
        text = text.lstrip("$").upper()
        if not TICKER.fullmatch(text):
            return None
    elif key in ("day", "expiry"):
        if not _real_day(text) and (spec.get("format") == "date" or not _DAY_WORD.fullmatch(text.lower())):
            return None
    elif key == "time":
        match = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
        if not match or int(match.group(1)) > 23 or int(match.group(2)) > 59:
            return None
        text = f"{int(match.group(1)):02d}:{match.group(2)}"
    elif key == "timeframe":
        if text not in chat_pages.TIMEFRAMES:
            return None
    elif key == "side" and text not in spec.get("enum", ()):
        text = {"call": "bull", "calls": "bull", "put": "bear", "puts": "bear"}.get(text.lower(), text.lower())
        if text not in ("bull", "bear"):
            return None
    if "enum" in spec and text not in spec["enum"]:
        return None
    if "pattern" in spec and not re.fullmatch(spec["pattern"], text):
        return None
    return text


def validate(schema: Any, args: Any) -> tuple[dict, list[str]]:
    """(the arguments the tool declares, each checked; the required ones that are missing). Anything else is dropped."""
    schema = schema if isinstance(schema, Mapping) else {}
    args = args if isinstance(args, Mapping) else {}
    clean = {}
    for key, spec in (schema.get("properties") or {}).items():
        if args.get(key) in (None, ""):
            continue
        value = _clean_value(key, spec if isinstance(spec, Mapping) else {}, args[key])
        if value is not None:
            clean[key] = value
    return clean, [key for key in schema.get("required") or () if key not in clean]


def clean_table(raw: Any, scope: str) -> dict | None:
    """A table as the screen may draw it, or None."""
    if not isinstance(raw, Mapping):
        return None
    title = " ".join(str(raw.get("title") or "").split())[:120]
    if raw.get("kind") == "scanner_day":
        ticker, day = str(raw.get("ticker") or "").upper(), _real_day(raw.get("day"))
        sections = [{"list": s["list"], "side": s["side"]} for s in raw.get("sections") or []
                    if isinstance(s, Mapping) and isinstance(s.get("list"), str) and s.get("side") in ("bull", "bear")]
        if not (TICKER.fullmatch(ticker) and day and sections):
            return None
        return {"kind": "scanner_day", "title": title or f"{ticker} on the scanner", "ticker": ticker, "day": day, "sections": sections[:8]}
    if raw.get("kind") != "rows":
        return None
    columns = [{k: str(c[k])[:40] for k in ("key", "label", "align") if c.get(k) not in (None, "")}
               for c in raw.get("columns") or [] if isinstance(c, Mapping) and c.get("key") and c.get("label")][:20]
    if not columns:
        return None
    keys = [c["key"] for c in columns]
    rows = [{k: ("" if row.get(k) is None else str(row.get(k))[:_CELL_MAX]) for k in keys}
            for row in (raw.get("rows") or [])[:MAX_TABLE_ROWS] if isinstance(row, Mapping)]
    out: dict = {"kind": "rows", "title": title, "columns": columns, "rows": rows}
    if raw.get("note"):
        out["note"] = " ".join(str(raw["note"]).split())[:240]
    link = chat_pages.clean_link(raw.get("link"), scope) if raw.get("link") else None
    if link:
        out["link"] = link
    return out


def _normal(out: Any, scope: str) -> dict:
    """A handler's answer in the shape the chat uses, whatever came back."""
    out = out if isinstance(out, Mapping) else {}
    lines = [str(line) for line in out.get("lines") or [] if isinstance(line, str) and line.strip()]
    facts = out.get("facts") if isinstance(out.get("facts"), Mapping) else {}
    as_of = out.get("asOf") if isinstance(out.get("asOf"), str) and out.get("asOf") else None
    suggest = [str(x)[:120] for x in out.get("suggest") or [] if isinstance(x, str) and x.strip()]
    normal = {"lines": lines, "facts": dict(facts), "links": chat_pages.clean_links(out.get("links"), scope),
              "table": clean_table(out.get("table"), scope), "asOf": as_of, "suggest": suggest}
    return {**normal, **extras(out, scope)}


#: Reply parts beyond lines and links (2026-11-11): the "more" token, article links, report sections, verdict views;
#: headline rows with their tone (2026-11-14).
EXTRA_KEYS = ("more", "articles", "sections", "views", "miniChart", "headlines")


def extras(out: Mapping, scope: str) -> dict:
    """The extra parts a tool may return, each checked: a token of plain characters, https article links,
    report sections with clean links and tables, verdict views and mini-chart data of plain values."""
    from momx import chat_report, chat_why
    found: dict = {}
    if isinstance(out.get("more"), str) and re.fullmatch(r"[A-Za-z0-9_-]{16,64}", out["more"]):
        found["more"] = out["more"]
    if out.get("articles"):
        found["articles"] = chat_why.clean_articles(out.get("articles"))
    if out.get("headlines"):                          # headline rows with a tone chip (2026-11-14)
        found["headlines"] = chat_why.clean_headlines(out.get("headlines"))
    if out.get("sections"):
        found["sections"] = chat_report.clean_sections(out.get("sections"), scope)
    if out.get("views"):
        found["views"] = chat_report.clean_views(out.get("views"))
    if isinstance(out.get("miniChart"), Mapping):
        found["miniChart"] = chat_report.clean_mini_chart(out.get("miniChart"))
    return {key: value for key, value in found.items() if value}


def run(name: str, args: Any, ctx: Mapping) -> dict:
    """Run one tool for this session. A tool it may not use reads nothing and says so without naming it."""
    entry = registry(str(ctx.get("scope") or "")).get(name)
    if entry is None:
        return empty("I cannot look that up here.")
    clean, missing = validate(entry.get("args"), args)
    if missing:
        view = _TOOL_PAGE.get(name)
        asked = "Which ticker do you want? Type one, like NVDA." if "ticker" in missing else f"I need the {missing[0]} for that."
        return empty(asked, *([chat_pages.link(view)] if view else []))
    try:
        return _normal(entry["handler"](clean, ctx), str(ctx.get("scope") or ""))
    except Exception as exc:  # noqa: BLE001 - one bad file must never fail the chat
        return empty(f"I could not read that ({type(exc).__name__}). Nothing was changed.")


# ---------------------------------------------------------------- which words are tickers
_OPTIONABLE: dict[str, tuple] = {}


def _optionable(artifacts: Path) -> frozenset:
    """Every symbol with a real option chain (a public list, read again only when the file changes)."""
    path = Path(artifacts) / "momx_optionable.json"
    try:
        stamp = path.stat().st_mtime_ns
    except OSError:
        return frozenset()
    held = _OPTIONABLE.get(str(path))
    if held and held[0] == stamp:
        return held[1]
    try:
        names = frozenset(str(s).upper() for s in (json.loads(path.read_text(encoding="utf-8")).get("symbols") or {}))
    except (OSError, ValueError, AttributeError):
        names = frozenset()
    _OPTIONABLE[str(path)] = (stamp, names)
    return names


def market_tickers(artifacts: Path) -> frozenset:
    """Tickers any session may have recognised in lower case. Reads nothing private."""
    return STATIC_TICKERS | _optionable(artifacts)


# ---------------------------------------------------------------- the session's day
def session_day(now: datetime, asked: date | None, named: bool, minute: int | None, today_word: bool = False) -> date:
    """The day to read. "Today" before the scanner has saved anything, or at a time not reached yet, means the last session."""
    from momx import chat_scanner
    day = asked or now.date()
    if named and not (today_word and day == now.date()):
        return day
    return chat_scanner.last_session(now.date(), False, minute if minute is not None else 6 * 60, now)


def _ctx_now(ctx: Mapping) -> datetime:
    from momx import chatbot
    now = ctx.get("now")
    return now if isinstance(now, datetime) else datetime.now(chatbot.ET)


# ---------------------------------------------------------------- pages
_TICKER_ARG = {"type": "string", "maxLength": 8, "description": "Stock ticker in capitals"}
_DAY_ARG = {"type": "string", "maxLength": 10, "format": "date", "description": "YYYY-MM-DD"}
_TIME_ARG = {"type": "string", "maxLength": 5, "description": "HH:MM, 24-hour ET"}
_SIDE_ARG = {"type": "string", "enum": ["bull", "bear"], "description": "bull = calls, bear = puts"}
_FRAME_ARG = {"type": "string", "enum": list(chat_pages.TIMEFRAMES)}


def _open_page(args: dict, ctx: Mapping) -> dict:
    scope = str(ctx.get("scope") or "")
    asked = args.get("page", "")
    found = chat_pages.find(asked, scope, loose=True)
    if found is None:
        if chat_pages.owner_page_named(asked):
            return empty("That page is for the owner.")
        return empty("I could not find a page called that.", chat_pages.link("Learn + Setup"))
    page, implied = found
    deep = {**implied, **{key: args[key] for key in ("ticker", "timeframe", "day", "tab", "sub", "list", "side", "time") if args.get(key)}}
    label = f"Open {page.label}" + (f" for {args['ticker']}" if args.get("ticker") and "ticker" in page.deep else "")
    return {"lines": [f"{page.label} is one tap away."], "facts": {"view": page.view},
            "links": [chat_pages.link(page.view, label, **deep)], "table": None, "asOf": None}


def _open_chart(args: dict, ctx: Mapping) -> dict:
    ticker, frame = args["ticker"], args.get("timeframe") or "5m"
    return {"lines": [f"Opening the {ticker} {frame} chart."], "facts": {"ticker": ticker, "timeframe": frame},
            "links": [chat_pages.link("Charts & OI", f"Open {ticker} {frame} chart", ticker=ticker, timeframe=frame)], "table": None, "asOf": None}


def _board_name(asked: str | None) -> str | None:
    from momx import chat_scanner
    low = str(asked or "").strip().lower()
    return next((name for name in chat_scanner.BOARD_NAMES if name.lower() == low), None)


def _open_scanner(args: dict, ctx: Mapping) -> dict:
    now = _ctx_now(ctx)
    ticker, clock = args.get("ticker"), args.get("time")
    board = _board_name(args.get("list"))
    tab = args.get("tab") or ("history" if ticker or clock or args.get("day") else "live")
    if tab == "live" and not board:
        return {"lines": ["The scanner is one tap away."], "facts": {"tab": "live"},
                "links": [chat_pages.link("MomX Scanner", "Open MomX Scanner", tab="live")], "table": None, "asOf": None}
    minute = int(clock[:2]) * 60 + int(clock[3:]) if clock else None
    day = args.get("day") or session_day(now, None, False, minute).isoformat()
    side = args.get("side") or "bull"
    links = []
    for name in ((board,) if board else _MAG_BOARDS):
        label = f"Open {ticker + ' on ' if ticker else ''}{name} History" + (f", {clock}" if clock else "") + (" puts" if side == "bear" else "")
        links.append(chat_pages.link("MomX Scanner", label, list=name, side=side, tab=tab, day=day, ticker=ticker, time=clock))
    what = f"{ticker} on the scanner" if ticker else "The scanner"
    return {"lines": [f"{what}, {day}" + (f" at {clock} ET." if clock else ".")], "facts": {"ticker": ticker, "day": day, "time": clock, "side": side},
            "links": links, "table": None, "asOf": None}


# ---------------------------------------------------------------- the owner's Bot lookups
def _owner_only(ctx: Mapping) -> dict | None:
    return None if ctx.get("scope") == "owner" else empty("I cannot look that up here.")


def _bot_link(label: str = "Open Bot", sub: str | None = None) -> dict:
    return chat_pages.link("Bot", label, sub=sub)


def _bot_records(args: dict, ctx: Mapping):
    from momx import chatbot
    now = _ctx_now(ctx).astimezone(chatbot.ET)
    today = now.date().isoformat()
    ticker = args["ticker"]
    use = chatbot.best_record_day(Path(ctx["artifacts"]), ticker, date.fromisoformat(args["day"]) if args.get("day") else now.date(), bool(args.get("day")), today)
    minute = int(args["time"][:2]) * 60 + int(args["time"][3:]) if args.get("time") else None
    return chatbot.records_for(Path(ctx["artifacts"]), use, today), ticker, minute, args.get("side")


def _bot_tool(pick: str):
    def handler(args: dict, ctx: Mapping) -> dict:
        refused = _owner_only(ctx)
        if refused:
            return refused
        from momx import chatbot
        records, ticker, minute, side = _bot_records(args, ctx)
        fn = getattr(chatbot, pick)
        out = fn(records, ticker, side) if pick == "answer_show" else fn(records, ticker, minute, side)
        return {**out, "facts": {**(out.get("facts") or {}), "day": records.day, "ticker": ticker},
                "links": [_bot_link(f"Open Bot for {ticker}")], "table": None, "asOf": None}
    return handler


def _status_tool(link: dict):
    def handler(args: dict, ctx: Mapping) -> dict:
        refused = _owner_only(ctx)
        if refused:
            return refused
        from momx import chatbot
        out = chatbot.answer_status(Path(ctx["artifacts"]), _ctx_now(ctx).astimezone(chatbot.ET).date().isoformat())
        return {**out, "links": [link], "table": None, "asOf": None}
    return handler


def _my_trades(args: dict, ctx: Mapping) -> dict:
    """The owner's real Schwab trades (momx/my_insights.py), from the saved read only: never calls Schwab."""
    refused = _owner_only(ctx)
    if refused:
        return refused
    from momx import my_insights
    days = args.get("days") or 7
    days = min(my_insights.PERIODS, key=lambda p: (abs(p - days), p))
    doc = my_insights.build(days, now=_ctx_now(ctx), artifacts=Path(ctx["artifacts"]), events={"items": [], "note": ""})
    lines = my_insights.chat_lines(doc, args.get("topic") or "summary")
    s = doc.get("summary") or {}
    return {"lines": lines, "facts": {"days": days, "trades": s.get("trades"), "pnl": s.get("pnl"), "winRate": s.get("winRate")},
            "links": [chat_pages.link("My Trading", "Open My Trading")], "table": None, "asOf": (doc.get("schwab") or {}).get("lastReadAt")}


def _market_events(args: dict, ctx: Mapping) -> dict:
    """The next 7 days of high-impact US events and watchlist earnings, from the saved copy only."""
    from momx import market_events
    doc = market_events.read(_ctx_now(ctx))
    return {"lines": market_events.chat_lines(doc, args.get("event")), "facts": {"events": len(doc.get("items") or [])},
            "links": [chat_pages.link("News Feed", "Open News Feed"), chat_pages.link("Earnings Calendar", "Open Earnings Calendar")],
            "table": None, "asOf": doc.get("refreshedAt")}


_BOT_ARGS = {"type": "object", "properties": {"ticker": _TICKER_ARG, "day": _DAY_ARG, "time": _TIME_ARG, "side": _SIDE_ARG}, "required": ["ticker"]}
_NO_ARGS = {"type": "object", "properties": {}}

OWN_TOOLS: dict = {
    "open_page": {"scope": "all", "description": "A button to a page of the app, by page name.", "handler": _open_page,
                  "args": {"type": "object", "properties": {"page": {"type": "string", "maxLength": 48}, "ticker": _TICKER_ARG, "timeframe": _FRAME_ARG, "day": _DAY_ARG,
                                                            "tab": {"type": "string", "enum": ["live", "history"]},
                                                            "sub": {"type": "string", "maxLength": 32, "pattern": r"[A-Za-z0-9][A-Za-z0-9_\-]*"}}, "required": ["page"]}},
    "open_chart": {"scope": "all", "description": "A button to a ticker's chart at a timeframe.", "handler": _open_chart,
                   "args": {"type": "object", "properties": {"ticker": _TICKER_ARG, "timeframe": _FRAME_ARG}, "required": ["ticker"]}},
    "open_scanner": {"scope": "all", "description": "A button to the scanner board, optionally a ticker, day and time in its history.", "handler": _open_scanner,
                     "args": {"type": "object", "properties": {"ticker": _TICKER_ARG, "list": {"type": "string", "maxLength": 48}, "side": _SIDE_ARG,
                                                               "tab": {"type": "string", "enum": ["live", "history"]}, "day": _DAY_ARG, "time": _TIME_ARG}}},
    "bot_what_if": {"scope": "owner", "description": "How far a ticker's Bot signal went, its profit or loss.", "args": _BOT_ARGS, "handler": _bot_tool("answer_what_if")},
    "bot_why_failed": {"scope": "owner", "description": "Why a ticker's Bot trade lost.", "args": _BOT_ARGS, "handler": _bot_tool("answer_why_failed")},
    "bot_why_missed": {"scope": "owner", "description": "Why the Bot or paper accounts did not take a ticker's signal.", "args": _BOT_ARGS, "handler": _bot_tool("answer_why_missed")},
    "bot_show": {"scope": "owner", "description": "What the Bot has on file for a ticker.", "args": _BOT_ARGS, "handler": _bot_tool("answer_show")},
    "bot_status": {"scope": "owner", "description": "How the bots and paper accounts are doing.", "args": _NO_ARGS, "handler": _status_tool(_bot_link())},
    "bot_desk": {"scope": "owner", "description": "The Bot Scorecard desk: results of the paper accounts.", "args": _NO_ARGS, "handler": _status_tool(_bot_link("Open Scorecard", "desk"))},
    "paper": {"scope": "owner", "description": "The paper accounts' results.", "args": _NO_ARGS,
              "handler": _status_tool(chat_pages.link("Option Paper Trading", "Open Option Paper Trading"))},
    "my_trades": {"scope": "owner", "handler": _my_trades,
                  "description": "The owner's own real Schwab option trades: how he traded this week or month, win rate, calls vs puts, "
                                 "costliest habits, spreads paid, by setup, time of day, DTE or ticker.",
                  "args": {"type": "object", "properties": {
                      "topic": {"type": "string", "enum": ["summary", "habits", "calls", "puts", "spreads", "setup", "time", "dte", "tickers"]},
                      "days": {"type": "integer", "minimum": 1, "maximum": 90, "description": "7, 30 or 90"}}}},
    "market_events": {"scope": "all", "handler": _market_events,
                      "description": "Upcoming high-impact US market events this week (Fed, CPI, PPI, jobs, GDP, retail sales) and watchlist earnings.",
                      "args": {"type": "object", "properties": {"event": {"type": "string", "maxLength": 24, "description": "e.g. CPI, FOMC, NVDA"}}}},
}
#: A reply's intent for the Bot tools keeps the names the chat already uses.
INTENT_OF = {"latest_news": "news", "bot_what_if": "what_if", "bot_why_failed": "why_failed", "bot_why_missed": "why_missed", "bot_show": "show", "bot_status": "status"}


def history_link(ticker: str | None, day: str | None, side: str | None = None, clock: str | None = None, board: str | None = None) -> dict:
    """A button to a ticker (or a board) in the scanner's history."""
    what = f"{ticker} on " if ticker else ""
    name = board or "the scanner"
    label = f"Open {what}{name} History" + (f", {clock}" if clock else "")
    return chat_pages.link("MomX Scanner", label, list=board, side=side, tab="history", day=day, ticker=ticker, time=clock)


def ticker_links(ticker: str, day: str | None, scope: str) -> list[dict]:
    """The search card for a ticker: everywhere it can be looked at."""
    links = [chat_pages.link("Charts & OI", f"{ticker} chart", ticker=ticker, timeframe="5m"),
             chat_pages.link("Quick Options", f"{ticker} options", ticker=ticker),
             history_link(ticker, day), chat_pages.link("News Feed", f"{ticker} news", ticker=ticker),
             chat_pages.link("Long Term", f"{ticker} long term", ticker=ticker)]
    if scope == "owner":
        links.append(_bot_link(f"{ticker} in the Bot"))
    return links


def previous_weekday(day: date) -> date:
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day
