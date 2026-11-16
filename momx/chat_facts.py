"""Small cached reads the chat's answers share (2026-11-11). Read only; never starts a build, a refresh or a Schwab call.

* board rows: the finished MomX boards on disk (momx_board_cache/<List>[.bear].json), re-read only when the file changes.
* a ticker's move today: its board row (pctChange, refreshed by every scan), else the saved option chain's
  todayChangePercent with the chain's save time. Each with its time.
* the market (SPY, QQQ) and the ticker's sector: its sector card's group (NVDA -> Semiconductors) and ETF.
* outside 09:30-16:00 ET, the move split by session (2026-11-11, the trader at 23:05 ET: "I asked why nvda down after
  market hours it's not giving information"; the answer said "+2.87% today ... up today, not down" while NVDA was
  about -8% after hours): the regular session (16:00 close against the session before's), after hours (16:00-20:00),
  overnight (20:00-04:00) and premarket (04:00-09:30), each against that 16:00 close, from the chart's saved minute
  bars with off-market bad prints repaired as the chart does (chat_chart.session_tape).

Every answer carries the time it is from. A value that is not there is left out, never guessed.
"""
from __future__ import annotations

import gzip
import json
import os
import re
import threading
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BOARDS = ("Mag7", "Watchlist")
#: A board row older than this is not "now" (a Watchlist build takes about 2.5 minutes).
BOARD_FRESH_SECONDS = 20 * 60
#: A chain saved longer ago than this is not "today's move".
CHAIN_FRESH_SECONDS = 3 * 3600
MAX_PEERS = 4
_TICKER = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")

_lock = threading.Lock()
_boards: dict[str, tuple] = {}
_chains: dict[str, tuple] = {}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def when(value: Any) -> datetime | None:
    """An ISO time or epoch seconds, in ET."""
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)) and value > 1e8:
        return datetime.fromtimestamp(float(value), ET)
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=ET)).astimezone(ET)


def clock(at: datetime | None, now: datetime | None = None) -> str:
    if at is None:
        return "time unknown"
    at = at.astimezone(ET)
    if now is not None and at.date() != now.date():
        return at.strftime("%a %b %d %H:%M ET").replace(" 0", " ")
    return at.strftime("%H:%M ET")


def artifacts_of(ctx: Mapping) -> Path:
    from momx import chat_scanner
    return chat_scanner.artifacts_dir(ctx)


# ---------------------------------------------------------------- the boards
def board(ctx: Mapping, list_name: str, bear: bool) -> tuple[dict, list, datetime | None]:
    """(rows by symbol, symbols on the board in H/L order, when the board's data is from). Empty when missing."""
    from momx import chat_scanner
    from momx.option_track import hl_ranking
    path = chat_scanner._board_file(artifacts_of(ctx), list_name, bear)
    try:
        info = path.stat()
    except OSError:
        return {}, [], None
    stamp = (info.st_mtime_ns, info.st_size)
    with _lock:
        held = _boards.get(str(path))
    if held and held[0] == stamp:
        return held[1], held[2], held[3]
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return {}, [], None
    rows = doc.get("rows") if isinstance(doc, dict) else None
    if not isinstance(rows, list):
        return {}, [], None
    by_symbol = {str(r.get("symbol") or "").upper(): r for r in rows if isinstance(r, dict) and r.get("symbol")}
    ranked = [symbol for symbol, _ in hl_ranking(rows, bear)]
    at = next((t for t in (when(doc.get(k)) for k in ("tapeAsOf", "scanCompletedAt", "generatedAt")) if t), None) or datetime.fromtimestamp(info.st_mtime, ET)
    with _lock:
        _boards[str(path)] = (stamp, by_symbol, ranked, at)
    return by_symbol, ranked, at


def fresh(at: datetime | None, now: datetime) -> bool:
    """Is a board "now"? Within BOARD_FRESH_SECONDS; after the close, the closing board of the same day still is."""
    if at is None:
        return False
    if (now - at).total_seconds() <= BOARD_FRESH_SECONDS:
        return True
    return at.date() == now.date() and now.hour >= 16 and (at.hour, at.minute) >= (15, 30)


def board_row(ticker: str, ctx: Mapping, now: datetime) -> tuple[dict, datetime] | None:
    """The ticker's newest row on any of the main boards (calls side; it carries the bear view too)."""
    best = None
    for name in BOARDS:
        rows, _, at = board(ctx, name, False)
        row = rows.get(ticker)
        if row and fresh(at, now) and (best is None or at > best[1]):
            best = (row, at)
    return best


def held_board_row(ticker: str, now: datetime) -> dict | None:
    """board_row from the boards already read into memory only: no file is opened or parsed (a board file can hold
    the interpreter for tens of ms). For callers that must stay quick, such as the Long Term page's Technical card."""
    best = None
    with _lock:
        held = [value for path, value in _boards.items() if Path(path).name in {f"{name}.json" for name in BOARDS}]
    for _, rows, _, at in held:
        row = rows.get(ticker)
        if row and fresh(at, now) and (best is None or at > best[1]):
            best = (row, at)
    return best[0] if best else None


def scanner_sides(ticker: str, ctx: Mapping, now: datetime) -> list[dict]:
    """Where the ticker is on the main boards now: [{list, side, rank, count, level, setups, at}]."""
    out = []
    for name in BOARDS:
        for bear in (False, True):
            rows, ranked, at = board(ctx, name, bear)
            if not fresh(at, now) or ticker not in ranked:
                continue
            row = rows.get(ticker) or {}
            inputs = row.get("scannerInputs") if isinstance(row.get("scannerInputs"), dict) else {}
            out.append({"list": name, "side": "bear" if bear else "bull", "rank": ranked.index(ticker) + 1, "count": len(ranked),
                        "level": int(_num(inputs.get("highestLevel")) or 0), "setups": [str(s.get("text") or s.get("key")) if isinstance(s, dict) else str(s) for s in inputs.get("setupTypes") or []][:8],
                        "at": at, "row": row})
    return out


def side_text(found: Mapping) -> str:
    side = "calls" if found["side"] == "bull" else "puts"
    level = f", Confirm Level {found['level']}" if found.get("level") else ""
    return f"#{found['rank']} of {found['count']} on the {found['list']} {side} board{level}"


# ---------------------------------------------------------------- moves
def _chain_snapshot(ticker: str, ctx: Mapping) -> dict | None:
    path = artifacts_of(ctx) / "oi_chain_cache" / f"{ticker}.json.gz"
    try:
        info = path.stat()
    except OSError:
        return None
    stamp = (info.st_mtime_ns, info.st_size)
    with _lock:
        held = _chains.get(ticker)
    if held and held[0] == stamp:
        return held[1]
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            doc = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    if not isinstance(doc, dict):
        return None
    small = {"price": _num(doc.get("underlyingPrice")), "pct": _num(doc.get("todayChangePercent")), "at": when(doc.get("scannedAt"))}
    with _lock:
        _chains[ticker] = (stamp, small)
    return small


def move(ticker: str, ctx: Mapping, now: datetime) -> dict | None:
    """{ticker, pct, price, at, source} for today's session, or None. Never a previous session's change called today's."""
    ticker = str(ticker or "").upper()
    if not _TICKER.fullmatch(ticker):
        return None
    found = board_row(ticker, ctx, now)
    if found:
        row, at = found
        pct, price = _num(row.get("pctChange")), _num(row.get("last"))
        stamp = when(row.get("marketDataAt")) or at
        if pct is not None and stamp.date() == now.date():
            return {"ticker": ticker, "pct": round(pct, 2), "price": price, "at": stamp, "source": "scanner board"}
    chain = _chain_snapshot(ticker, ctx)
    if chain and chain["pct"] is not None and chain["at"] and chain["at"].date() == now.date() \
            and (now - chain["at"]).total_seconds() <= CHAIN_FRESH_SECONDS and (chain["at"].hour, chain["at"].minute) >= (9, 30):
        return {"ticker": ticker, "pct": round(chain["pct"], 2), "price": chain["price"], "at": chain["at"], "source": "option chain"}
    return None


def move_text(found: Mapping, now: datetime) -> str:
    price = f" at {found['price']:,.2f}" if found.get("price") else ""
    return f"{found['ticker']} {found['pct']:+.2f}% today{price} ({found['source']}, {clock(found['at'], now)})"


def sector_of(ticker: str) -> dict | None:
    """{sector, etf, group, peers} from the app's sector cards, or None."""
    from momx import chat_market
    for card in chat_market.sector_cards():
        group = next((g for g in card.get("groups") or [] if ticker in g.get("symbols") or []), None)
        members = {m.get("symbol") for m in (card.get("leaders") or []) + (card.get("more") or [])}
        if group or ticker in members or ticker == card.get("etf"):
            peers = [s for s in (group.get("symbols") if group else [m.get("symbol") for m in card.get("leaders") or []]) if s and s != ticker]
            return {"sector": card.get("name"), "etf": card.get("etf"), "group": group.get("label") if group else None, "peers": peers}
    return None


def backdrop(ticker: str, ctx: Mapping, now: datetime) -> dict:
    """The market and the sector next to the ticker: {"market": [moves], "sector": info, "peers": [moves], "etf": move|None}."""
    market = [m for m in (move(s, ctx, now) for s in ("SPY", "QQQ")) if m]
    info = sector_of(ticker) if ticker not in ("SPY", "QQQ") else None
    peers, etf = [], None
    if info:
        for symbol in info["peers"]:
            if len(peers) >= MAX_PEERS:
                break
            found = move(symbol, ctx, now)
            if found:
                peers.append(found)
        etf = move(info["etf"], ctx, now) if info.get("etf") else None
    return {"market": market, "sector": info, "peers": peers, "etf": etf}


def compare(mine: Mapping | None, back: Mapping) -> str | None:
    """One plain line: did the whole market, the sector, or the ticker alone move? The rule: a move counts as
    shared when the other moved the same way by at least half as much."""
    if not mine:
        return None
    pct = mine["pct"]

    def shared(other: float) -> bool:
        return pct != 0 and other * pct > 0 and abs(other) >= abs(pct) / 2

    # Never a ticker against itself (2026-11-14: "why spy went down" printed "The whole market moved the same way (SPY
    # -0.62%)", SPY next to SPY): SPY's own move is not the market's verdict on SPY.
    spy = next((m for m in back.get("market") or [] if m["ticker"] == "SPY" and m["ticker"] != mine.get("ticker")), None)
    group = [m["pct"] for m in back.get("peers") or []] + ([back["etf"]["pct"]] if back.get("etf") else [])
    average = sum(group) / len(group) if group else None
    if spy and shared(spy["pct"]):
        return f"The whole market moved the same way (SPY {spy['pct']:+.2f}%)."
    sector = back.get("sector") or {}
    name = sector.get("group") or sector.get("sector") or "its sector"
    if average is not None and shared(average):
        return f"Its sector moved with it ({name} average {average:+.2f}%), while SPY did not move as much."
    if spy is not None:
        tail = f", {name} average {average:+.2f}%" if average is not None else ""
        return f"{mine['ticker']} moved mostly on its own (SPY {spy['pct']:+.2f}%{tail})."
    if average is not None:
        return f"{mine['ticker']} moved mostly on its own ({name} average {average:+.2f}%)."
    return None


# ---------------------------------------------------------------- the move by session
REGULAR_OPEN_MINUTE, REGULAR_CLOSE_MINUTE = 9 * 60 + 30, 16 * 60
PREMARKET_MINUTE = 4 * 60
#: The most the fast part waits, all symbols together, for a saved chart file to be read in the child.
SESSION_WAIT_SECONDS = 6.0
WINDOWS = ("regular", "after", "overnight", "pre")
SESSION_LABELS = {"regular": "Regular session", "after": "After hours", "overnight": "Overnight", "pre": "Premarket"}
SESSION_WORDS = {"regular": "in the regular session", "after": "after hours", "overnight": "overnight", "pre": "in premarket"}
_OVERNIGHT_ASK = re.compile(r"\bover[\s-]?night\b")
_PRE_ASK = re.compile(r"\bpre[\s-]?(?:market|mkt|open)\b|\bbefore\s+the\s+(?:open|opening|bell)\b")
_AFTER_ASK = re.compile(r"\bafter[\s-]*(?:hours?|market|mkt)\b|\bafter\s+(?:the\s+)?(?:close|closing|bell)\b|\bpost[\s-]*(?:market|close)\b"
                        r"|\btonight\b|\bthis\s+evening\b|\bah\b")
_EXTENDED_ASK = re.compile(r"\bextended(?:[\s-]*hours?)?\b|\bext\s+hours\b")
_REGULAR_ASK = re.compile(r"\bregular\s+(?:session|hours|trading)\b|\bduring\s+(?:the\s+)?(?:day|session|market\s+hours)\b|\bat\s+the\s+close\b")
#: Part of the regular session (2026-11-14, "Why spy went down this morning?" answered with the whole day and after
#: hours): the morning is 09:30-12:00 ET, the afternoon 12:00 to the close.
_MORNING_ASK = re.compile(r"\bmorning\b|\b(?:at|after|from|since|off)\s+(?:the\s+)?open(?:ing)?(?:\s+bell)?\b|\bopening\b|\bfirst\s+hour\b")
_AFTERNOON_ASK = re.compile(r"\bafternoon\b|\bafter\s+(?:lunch|noon|midday)\b|\bpm\s+session\b")
#: More parts of the session (2026-11-14, "add all words that will be good"): the gap at the open, midday / lunch,
#: the last hour into the close, and a clock time ("at 10am" = 15 minutes either side).
_GAP_ASK = re.compile(r"\bgap(?:s|ped|ping)?(?:\s+(?:up|down|higher|lower))?\b|\bopen(?:ed|s)?\s+(?:up|down|higher|lower|red|green)\b"
                      r"|\b(?:at|on)\s+the\s+opening\s+bell\b")
_MIDDAY_ASK = re.compile(r"\bmid[\s-]?day\b|\blunch(?:\s*time|\s+hour)?\b|\b(?:at|around|near|by)\s+noon\b")
_CLOSE_PART_ASK = re.compile(r"\bpower\s+hour\b|\b(?:last|final)\s+(?:hour|30\s+min\w*|half\s+hour)\b|\b(?:into|near|toward|towards|before)\s+(?:the\s+)?close\b"
                             r"|\bend\s+of\s+(?:the\s+)?day\b|\beod\b|\blate\s+(?:in\s+the\s+)?(?:day|session)\b|\bclosing\s+(?:hour|minutes)\b")
_CLOCK_ASK = re.compile(r"\b(?:at|around|near|about|by|after|before)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)?(?![\w:%.])"
                        r"|\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|a\.m\.|p\.m\.)(?!\w)|\b(\d{1,2}):(\d{2})\b(?!\s*%)")
MORNING_END_MINUTE = 12 * 60
#: A clock time asked about covers this many minutes either side of it.
CLOCK_SPAN_MINUTES = 15
#: The asked part of a session: what the screen calls it.
WINDOW_LABELS = {"morning": "This morning", "afternoon": "This afternoon", "today": "Today so far", "midday": "Midday",
                 "close": "Into the close", "gap": "At the open", "clock": "Around {clock}"}
WINDOW_WORDS = {"morning": "this morning", "afternoon": "this afternoon", "today": "today so far", "midday": "at midday",
                "close": "into the close", "gap": "at the open", "clock": "around {clock}"}
#: An earlier day's part: "Friday morning", "Friday midday", "Friday into the close".
_DAY_LABELS = {"morning": "{wd} morning", "afternoon": "{wd} afternoon", "midday": "{wd} midday", "close": "{wd} into the close",
               "gap": "{wd} at the open", "clock": "{wd} around {clock}"}
#: Every part of a regular session a why-question can name ("today" is the whole of it so far).
PART_KINDS = ("morning", "afternoon", "midday", "close", "gap", "clock")
#: What each part's move is measured from, in words, for the fast line and the answer.
_PART_FROM = {"morning": "from the open", "midday": "from 11:30 AM", "afternoon": "from 12:00 PM", "close": "from 3:00 PM"}


def part_kind(asked: str | None) -> str | None:
    """The part-of-session kind of an asked_session answer ("clock:600" is "clock"), or None for a whole session."""
    kind = str(asked or "").split(":", 1)[0]
    return kind if kind in PART_KINDS else None


def is_part(asked: str | None) -> bool:
    return part_kind(asked) is not None


def clock_minute(question: str | None) -> int | None:
    """The ET minute of a clock time inside the regular session ("at 10am", "around 2:30", "10:15"), or None. A bare
    1-4 is the afternoon, 9-11 the morning, 12 noon."""
    for found in _CLOCK_ASK.finditer(str(question or "").lower()):
        hour, mins, half = next(((found.group(i), found.group(i + 1), found.group(i + 2) if i < 7 else None)
                                 for i in (1, 4, 7) if found.group(i)), (None, None, None))
        if hour is None:
            continue
        hour, mins = int(hour), int(mins or 0)
        half = (half or "").replace(".", "")
        if hour > 12 or mins > 59:
            if hour <= 23 and mins <= 59 and not half:
                minute = hour * 60 + mins                     # 24-hour clock "14:30"
            else:
                continue
        elif half == "pm" and hour < 12:
            minute = (hour + 12) * 60 + mins
        elif half == "am" and hour == 12:
            minute = mins
        elif half:
            minute = hour * 60 + mins
        else:
            minute = (hour + 12 if 1 <= hour <= 4 else hour) * 60 + mins
        if REGULAR_OPEN_MINUTE <= minute <= REGULAR_CLOSE_MINUTE:
            return minute
    return None


def _part_bounds(kind: str, clock: int | None, closing: int) -> tuple[int, int]:
    if kind == "morning":
        return REGULAR_OPEN_MINUTE, min(MORNING_END_MINUTE, closing)
    if kind == "afternoon":
        return MORNING_END_MINUTE, closing
    if kind == "midday":
        return 11 * 60 + 30, min(13 * 60 + 30, closing)
    if kind == "close":
        return max(closing - 60, REGULAR_OPEN_MINUTE), closing
    if kind == "gap":
        return REGULAR_OPEN_MINUTE, REGULAR_OPEN_MINUTE
    centre = int(clock or REGULAR_OPEN_MINUTE)
    return max(centre - CLOCK_SPAN_MINUTES, REGULAR_OPEN_MINUTE), min(centre + CLOCK_SPAN_MINUTES, closing)


def close_minute(day: Any) -> int:
    """The ET minute a day's regular session ends: 13:00 on an NYSE early-close day (chart_levels.EARLY_CLOSE_DAYS,
    the one shared list), else 16:00."""
    from momx import chart_levels
    return chart_levels.close_minute(day.isoformat() if hasattr(day, "isoformat") else str(day))


def close_time(day: str) -> datetime:
    """The regular session's closing moment of a YYYY-MM-DD day, in ET (13:00 on an early-close day)."""
    found = datetime.strptime(day, "%Y-%m-%d").date()
    minute = close_minute(day)
    return datetime(found.year, found.month, found.day, minute // 60, minute % 60, tzinfo=ET)


def close_words(at: datetime) -> str:
    """'4:00 PM' (or '1:00 PM' on an early-close day) for a closing moment."""
    return at.astimezone(ET).strftime("%I:%M %p").lstrip("0")


def trading_weekday(day: Any) -> bool:
    """A weekday that is not an NYSE holiday (chart_levels.MARKET_HOLIDAYS, the one shared list)."""
    from momx import chart_levels
    found = day if hasattr(day, "weekday") else datetime.strptime(str(day), "%Y-%m-%d").date()
    return found.weekday() < 5 and found.isoformat() not in chart_levels.MARKET_HOLIDAYS


def market_open(now: datetime) -> bool:
    """09:30-16:00 ET on a trading weekday, 09:30-13:00 on an early-close day. Closed all day on an NYSE holiday
    (2026-11-14 review: on Thanksgiving at 10:00 the chat explained an empty "today")."""
    now = now.astimezone(ET)
    return trading_weekday(now.date()) and REGULAR_OPEN_MINUTE <= now.hour * 60 + now.minute < close_minute(now.date())


def trading_day(views: Mapping, now: datetime) -> str | None:
    """The newest finished trading day, YYYY-MM-DD: SPY's session view is the calendar (it knows holidays); with no SPY
    view, the newest weekday whose close has passed. The reference close of any other ticker is checked against it."""
    spy = views.get("SPY") if isinstance(views, Mapping) else None
    if spy and spy.get("day"):
        return str(spy["day"])
    now = now.astimezone(ET)
    day = now.date()
    for _ in range(10):
        if trading_weekday(day) and close_time(day.isoformat()) <= now:
            return day.isoformat()
        day -= timedelta(days=1)
    return None


def stale_view(view: Mapping | None, calendar: str | None) -> bool:
    """Is a ticker's saved chart a session or more behind the market's newest finished trading day?"""
    return bool(view and calendar and str(view.get("day") or "") < calendar)


def asked_session(question: str | None) -> str | None:
    """The session a question names: "overnight", "pre", "after", "extended", "regular", "morning" (09:30-12:00 ET),
    "afternoon" (12:00 to the close), or None. A session word wins over a part of the day ("this morning before the
    open" is the premarket)."""
    low = str(question or "").lower()
    if _OVERNIGHT_ASK.search(low):
        return "overnight"
    if _PRE_ASK.search(low):
        return "pre"
    if _AFTER_ASK.search(low):
        return "after"
    if _EXTENDED_ASK.search(low):
        return "extended"
    if _REGULAR_ASK.search(low):
        return "regular"
    # The most exact part wins: a gap, the close, a clock time, then midday, the afternoon, the morning.
    if _GAP_ASK.search(low):
        return "gap"
    if _CLOSE_PART_ASK.search(low):
        return "close"
    clock = clock_minute(low)
    if clock is not None:
        return f"clock:{clock}"
    if _AFTERNOON_ASK.search(low):
        return "afternoon"
    if _MIDDAY_ASK.search(low):
        return "midday"
    if _MORNING_ASK.search(low):
        return "morning"
    return None


def _clock_words(minute: int) -> str:
    return datetime(2000, 1, 1, minute // 60, minute % 60).strftime("%I:%M %p").lstrip("0")


def day_words(day: str) -> str:
    """'Fri Nov 13' for 2026-11-13."""
    return datetime.strptime(day, "%Y-%m-%d").strftime("%a %b %d").replace(" 0", " ")


def part_window(asked: str | None, day: str, now: datetime) -> dict | None:
    """The asked part of a named past day's session ("yesterday morning" asked on a weekday, "friday at lunch"), or
    None when no part is asked. The same window ask_window gives for a finished session."""
    if not is_part(asked):
        return None
    after = close_time(day) + timedelta(hours=1)          # as if asked after that day's close, so nothing is "so far"
    spec = ask_window(asked, after, day)
    if spec and day != now.astimezone(ET).date().isoformat():
        weekday = date.fromisoformat(day).strftime("%A")
        clock = str(asked).split(":", 1)[1] if part_kind(asked) == "clock" else None
        spec["label"] = spec["words"] = _DAY_LABELS[spec["kind"]].format(wd=weekday, clock=_clock_words(int(clock)) if clock else "")
    return spec


def ask_window(asked: str | None, now: datetime, reference_day: str | None) -> dict | None:
    """The part of a regular session a why-question is about, or None when it is about a whole session (the caller's
    session split) or the premarket. {"kind": "morning" | "afternoon" | "today", "day", "start", "end" (ET minutes),
    "soFar", "label", "text", "words"}.
    The day is the session the trader means: during 09:30 to the close, today, its end clipped to now ("so far"); after
    the close, overnight, before 04:00 ET, or on a weekend or a holiday, the newest finished session (`reference_day`,
    SPY's tape knows the holidays). "This morning" between 04:00 and 09:30 on a weekday is the premarket (None).
    With no window word: during regular hours, today 09:30 to now; otherwise None."""
    now = now.astimezone(ET)
    today, minute = now.date().isoformat(), now.hour * 60 + now.minute
    asked_kind = part_kind(asked)
    if asked not in (None, "regular") and asked_kind is None:
        return None
    clock = int(str(asked).split(":", 1)[1]) if asked_kind == "clock" else None
    if market_open(now):
        day, closing = today, close_minute(today)
        start, end = _part_bounds(asked_kind, clock, closing) if asked_kind else (REGULAR_OPEN_MINUTE, minute)
        if asked_kind and (start < minute or (asked_kind == "gap" and start <= minute)):
            kind, part_end = asked_kind, end
            end = min(end, minute)
        else:
            # Not reached yet ("this afternoon" at 10:00, "power hour" at 11:00): today so far.
            kind, start, end, part_end = "today", REGULAR_OPEN_MINUTE, minute, closing
        so_far = end == minute and end < part_end
    else:
        if asked_kind is None or not reference_day:
            return None
        if asked_kind in ("morning", "gap") and trading_weekday(now.date()) and PREMARKET_MINUTE <= minute < REGULAR_OPEN_MINUTE:
            return None                                       # this morning (or the gap) before the open: the premarket
        day, closing = reference_day, close_minute(reference_day)
        kind = asked_kind
        start, end = _part_bounds(kind, clock, closing)
        so_far = False
    clock_text = _clock_words(clock) if clock is not None else ""
    label = WINDOW_LABELS[kind].format(clock=clock_text)
    words = WINDOW_WORDS[kind].format(clock=clock_text) if kind != "today" or so_far else "today"
    if day != today and kind in PART_KINDS:
        # An earlier day's part (2026-11-14 phone: "Why spy down yesterday morning?" asked Saturday must not read
        # "this morning"): name the weekday, "Friday morning", "Friday into the close".
        weekday = date.fromisoformat(day).strftime("%A")
        label = words = _DAY_LABELS[kind].format(wd=weekday, clock=clock_text)
    if kind == "gap":
        # The gap is the open against the previous close: one moment, not a span.
        return {"kind": kind, "day": day, "start": start, "end": end, "soFar": False, "label": label,
                "text": f"9:30 AM open, {day_words(day)}", "words": words}
    # "Today so far" already says it; the morning or afternoon so far says it in the text (2026-11-14 review: "today so
    # far (..., so far)" said it twice).
    text = f"{_clock_words(start)}-{_clock_words(end)} ET, {day_words(day)}" + (", so far" if so_far and kind != "today" else "")
    return {"kind": kind, "day": day, "start": start, "end": end, "soFar": so_far, "label": label, "text": text,
            "words": words}


def window_views(symbols: list | tuple, ctx: Mapping, spec: Mapping, wait: float = SESSION_WAIT_SECONDS,
                 kick: frozenset | set | None = None) -> dict:
    """{symbol: chart_levels.window_move answer or None} for the asked window (chat_chart.window_moves): a memory tape
    at once, every saved chart file still needed in ONE child, waited for at most `wait` (it carries on, and the next
    question reads it). A symbol still being read, or with no bars in the window, is None. `kick` is kept for the
    callers' shape; one batched child reads them all."""
    from momx import chat_chart
    wanted = [s for s in dict.fromkeys(symbols) if s and _TICKER.fullmatch(str(s))]
    try:
        held = chat_chart.window_moves(wanted, ctx, spec["day"], spec["start"], spec["end"], wait=wait)
    except Exception:  # noqa: BLE001 - a chart that cannot be read is left out, never an error
        held = {}
    out: dict = {}
    for symbol in wanted:
        found = held.get(symbol)
        good = isinstance(found, Mapping) and not found.get("missing") and not found.get("busy") and _num(found.get("price"))
        out[symbol] = {**found, "ticker": symbol} if good else None
    return out


def window_pct(found: Mapping | None, spec: Mapping) -> float | None:
    """The window's move: the morning from the open, the afternoon from 12:00, today so far from the previous close."""
    if not found:
        return None
    kind = str(spec.get("kind"))
    if kind == "gap":
        return gap_pct(found)
    key = "pctOpen" if kind == "morning" else "pctStart" if kind in PART_KINDS else "pctClose"
    return _num(found.get(key))


def gap_pct(found: Mapping | None) -> float | None:
    """The open against the previous close, in percent (the gap at the open)."""
    opened, prev = _num((found or {}).get("open")), _num((found or {}).get("prevClose"))
    if opened is None or not prev:
        return None
    return round((opened / prev - 1.0) * 100.0, 2)


def _prev_close_words(found: Mapping) -> str:
    day = found.get("prevDay")
    return datetime.strptime(day, "%Y-%m-%d").strftime("%A") + "'s close" if day else "the previous close"


def window_part(found: Mapping, spec: Mapping) -> str:
    """'SPY -1.36% from the open, -0.98% from Thursday's close' (the window's own measure first)."""
    ticker, kind = found["ticker"], spec.get("kind")
    close = f"{found['pctClose']:+.2f}% from {_prev_close_words(found)}" if found.get("pctClose") is not None else None
    if kind == "gap":
        gap = gap_pct(found)
        return f"{ticker} opened {gap:+.2f}% from {_prev_close_words(found)}" if gap is not None else ""
    if kind in PART_KINDS and kind != "morning":
        start = spec.get("start")
        base = _PART_FROM.get(kind) or (f"from {_clock_words(int(start))}" if isinstance(start, int) else "from the start")
        first = f"{found['pctStart']:+.2f}% {base}" if found.get("pctStart") is not None else None
    else:
        first = f"{found['pctOpen']:+.2f}% from the open" if found.get("pctOpen") is not None else None
    parts = [close, first] if kind == "today" else [first, close]
    return f"{ticker} " + ", ".join(p for p in parts if p)


def window_line(views: list, spec: Mapping) -> str | None:
    """'This morning (9:30 AM-12:00 PM ET, Fri Nov 13): SPY -1.36% from the open, -0.98% from Thursday's close.'"""
    parts = [part for part in (window_part(v, spec) for v in views if v) if part]
    if not parts:
        return None
    return f"{spec['label']} ({spec['text']}): " + "; ".join(parts) + "."


def window_move_dict(found: Mapping | None, spec: Mapping) -> dict | None:
    """The window's move as a move dict (compare() and the up/down correction read it)."""
    pct = window_pct(found, spec)
    if pct is None:
        return None
    return {"ticker": found["ticker"], "pct": pct, "price": found.get("price"), "at": when(found.get("at")),
            "source": "chart", "session": "window"}


def pick_sessions(ticker: str, tape: Mapping | None, now: datetime) -> dict | None:
    """The ticker's moves by session at `now` from chat_chart.session_tape's answer, or None when the newest finished
    regular session or the one before it is not saved complete. {"ticker", "day", "close", "closeAt", "prevClose",
    "prevDay", "regular", "after", "overnight", "pre", "source"}; each window {"pct", "price", "at"} or None.
    The reference close is the newest regular session that has finished: today's after 16:00 (13:00 on an early-close
    day), else the one before. "preOfDay": the premarket that led into the reference session, against the session
    before's close ({"pct", "price", "at", "since"}), for a premarket question asked after the close or on the weekend.
    Whether the reference day is the market's newest finished trading day is checked by the caller (trading_day)."""
    if not isinstance(tape, Mapping):
        return None
    now = now.astimezone(ET)
    today, stamp_now = now.date().isoformat(), now.timestamp()
    before_close = now.hour * 60 + now.minute < close_minute(today)
    sessions = [s for s in tape.get("sessions") or [] if isinstance(s, Mapping) and isinstance(s.get("day"), str)
                and _num(s.get("close")) and _num(s.get("time"))]
    index = next((i for i in range(len(sessions) - 1, -1, -1)
                  if sessions[i]["day"] < today or (sessions[i]["day"] == today and not before_close)), None)
    if index is None or index == 0:
        return None
    ref, prev = sessions[index], sessions[index - 1]
    if not ref.get("complete") or not prev.get("complete") or float(ref["time"]) > stamp_now:
        return None
    close, prev_close = float(ref["close"]), float(prev["close"])
    if close <= 0 or prev_close <= 0:
        return None
    close_at = close_time(ref["day"])
    out = {"ticker": ticker, "day": ref["day"], "close": close, "closeAt": close_at, "prevClose": prev_close, "prevDay": prev["day"],
           "regular": {"pct": round((close / prev_close - 1) * 100, 2), "price": close, "at": close_at},
           "after": None, "overnight": None, "pre": None, "preOfDay": None, "source": "chart"}
    prev_close_at = close_time(prev["day"])
    for item in tape.get("extended") or []:
        if not isinstance(item, Mapping):
            continue
        window, anchor, at, price = item.get("window"), str(item.get("anchor") or ""), _num(item.get("time")), _num(item.get("price"))
        if window == "pre" and anchor == ref["day"] and at and price and price > 0 and at > prev_close_at.timestamp() \
                and at < close_at.timestamp():
            # The premarket that led into the reference session, against the session before's close: for a premarket
            # question asked after the close or on the weekend, when no premarket after the reference close exists.
            held = out["preOfDay"]
            if held is None or at >= held["at"].timestamp():
                out["preOfDay"] = {"pct": round((price / prev_close - 1) * 100, 2), "price": price,
                                   "at": datetime.fromtimestamp(at, ET), "since": prev_close_at}
            continue
        if window not in ("after", "overnight", "pre") or not at or not price or price <= 0 or at <= float(ref["time"]) or at > stamp_now + 60:
            continue
        if anchor < ref["day"] or (window == "after" and anchor != ref["day"]) or (window == "pre" and not ref["day"] < anchor <= today):
            continue
        held = out[window]
        if held is None or at >= held["at"].timestamp():
            out[window] = {"pct": round((price / close - 1) * 100, 2), "price": price, "at": datetime.fromtimestamp(at, ET)}
    return out


def session_views(symbols: list | tuple, ctx: Mapping, now: datetime, wait: float = SESSION_WAIT_SECONDS,
                  kick: frozenset | set | None = None) -> dict:
    """{symbol: pick_sessions(...) or None}, in the order given (the asked ticker first). A memory tape answers at once;
    a saved chart file is read in the child, one after another, until `wait` runs out. After that, only the symbols in
    `kick` (default: all) start reading in the background for the next question, so a sector peer never takes the one
    chart slot ahead of SPY. Never a chart build, never a Schwab call."""
    import time as _time
    from momx import chat_chart
    deadline = _time.monotonic() + max(wait, 0.0)
    out: dict = {}
    for symbol in symbols:
        if not symbol or symbol in out or not _TICKER.fullmatch(str(symbol)):
            continue
        left = max(deadline - _time.monotonic(), 0.0)
        try:
            tape = chat_chart.session_tape(symbol, ctx, wait=left, start=left > 0 or kick is None or symbol in kick)
        except Exception:  # noqa: BLE001 - a chart that cannot be read is left out, never an error
            tape = None
        out[symbol] = pick_sessions(symbol, tape, now)
    return out


def judged_window(view: Mapping | None, asked: str | None, now: datetime) -> str | None:
    """Which session's move the question is about. None during 09:30-16:00 with no session named (today's live move).
    No session word outside regular hours: the most recent session (premarket in the morning, else after hours)."""
    now = now.astimezone(ET)
    morning = now.weekday() < 5 and now.hour * 60 + now.minute < REGULAR_OPEN_MINUTE

    def has(window: str) -> bool:
        return bool(view and view.get(window))
    if asked == "regular":
        return None if market_open(now) else "regular"
    if asked in ("pre", "after"):
        return asked
    if asked == "overnight":
        return "overnight" if has("overnight") else "after"
    if asked == "extended":
        return "pre" if morning and has("pre") else "after"
    if market_open(now):
        return None
    if has("pre") and view["pre"]["at"].date() == now.date():
        return "pre"
    if has("after") or not has("overnight"):
        return "after"
    return "overnight"


def shown_windows(view: Mapping | None, judged: str | None, asked: str | None, now: datetime) -> list[str]:
    """The session lines to show. Outside regular hours: the regular session and the most recent extended one, always
    both (after hours, and overnight when it has prints; before 09:30 the premarket instead). During regular hours: only
    a session the question names, next to today's move."""
    if judged is None:
        return []
    if market_open(now):
        return [judged]
    if judged == "pre" and asked not in ("after", "overnight"):
        return ["regular", "pre"]
    windows = ["regular", "after"]
    if view and view.get("overnight"):
        windows.append("overnight")
    if judged not in windows:
        windows.append(judged)
    return windows


def ampm(at: datetime, now: datetime | None = None) -> str:
    """'7:58 PM ET', or 'Fri Nov 7, 7:58 PM ET' on another day."""
    at = at.astimezone(ET)
    text = at.strftime("%I:%M %p").lstrip("0") + " ET"
    if now is not None and at.date() == now.astimezone(ET).date():
        return text
    return at.strftime("%a %b %d").replace(" 0", " ") + ", " + text


def _since(view: Mapping, now: datetime, found: Mapping | None = None) -> str:
    """'the 4:00 PM close', or 'the Tue Nov 10 4:00 PM close' on another day; the window's own close when it has one
    (the premarket that led into the reference session is measured from the session before's close)."""
    at = (found or {}).get("since") or view["closeAt"]
    if at.date() == now.astimezone(ET).date():
        return f"the {close_words(at)} close"
    return "the " + at.strftime("%a %b %d").replace(" 0", " ") + f" {close_words(at)} close"


def session_part(view: Mapping, window: str, now: datetime) -> str | None:
    found = view.get(window)
    if not found:
        return None
    if window == "regular":
        return f"{view['ticker']} {found['pct']:+.2f}% to {found['price']:,.2f} ({ampm(view['closeAt'], now)})"
    when_text = ampm(found["at"]) if found.get("since") else ampm(found["at"], now)       # that day named, always
    return f"{view['ticker']} {found['pct']:+.2f}% to {found['price']:,.2f} since {_since(view, now, found)} ({when_text})"


def session_line(views: list, window: str, now: datetime, missing: bool = True) -> str | None:
    """'After hours: NVDA -8.66% to 158.79 since the 4:00 PM close (7:58 PM ET).' for one or more tickers. With no
    print in that window for the first one, says so (missing=True) or gives nothing."""
    views = [v for v in views if v]
    parts = [part for part in (session_part(v, window, now) for v in views) if part]
    if parts:
        return f"{SESSION_LABELS[window]}: " + "; ".join(parts) + "."
    if missing and views and window != "regular":
        return f"{SESSION_LABELS[window]}: no {views[0]['ticker']} prints are saved since {_since(views[0], now)}."
    return None


def session_move(view: Mapping | None, window: str | None) -> dict | None:
    """One session's move as a move dict (compare() and the up/down correction read it)."""
    if not view or not window or not view.get(window):
        return None
    found = view[window]
    return {"ticker": view["ticker"], "pct": found["pct"], "price": found["price"], "at": found["at"], "source": "chart", "session": window}


def session_backdrop(view: Mapping, window: str, views: Mapping, info: Mapping | None) -> dict:
    """backdrop() for one session: SPY, QQQ, the peers and the ETF in that same session, against the same close day."""
    def same(symbol: str | None) -> dict | None:
        other = views.get(symbol) if symbol else None
        return session_move(other, window) if other and other.get("day") == view.get("day") else None
    market = [m for m in (same(s) for s in ("SPY", "QQQ")) if m]
    peers = [m for m in (same(s) for s in (info or {}).get("peers") or []) if m][:MAX_PEERS]
    return {"market": market, "sector": info, "peers": peers, "etf": same((info or {}).get("etf"))}


def reset() -> None:
    with _lock:
        _boards.clear()
        _chains.clear()


def env_key(name: str) -> str:
    """A key from the process environment, else the app's .env file (read in-process, never printed)."""
    value = os.environ.get(name, "").strip()
    if value:
        return value
    for folder in (Path(__file__).resolve().parents[1],):
        try:
            for line in (folder / ".env").read_text(encoding="utf-8", errors="ignore").splitlines():
                key, _, raw = line.partition("=")
                if key.strip() == name:
                    return raw.strip().strip('"').strip("'")
        except OSError:
            continue
    return ""
