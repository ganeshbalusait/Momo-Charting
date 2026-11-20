"""Market answers for the Ask chat: option chain, walls, expected move, max pain,
sectors, premarket, news, earnings, Long Term, the morning brief and a quote.

Every tool here only READS what the app already holds in memory or on disk. It
never starts a Schwab call, a chart build, a refresh or a phone push, so asking
the chat can never load the app the way opening a page does. In particular it
never calls ``oi_finder_payload``, ``oi_finder_chart_payload``,
``premarket_scanner_payload`` or ``earnings_calendar_payload``; those paths
reach Schwab or Finnhub, or send a push. No tool places or changes a trade.

Reply shape (see TOOLS): ``{"lines", "facts", "links", "table", "asOf"}``.
Every number carries the time it was saved. A tool with nothing to say returns a
plain line saying what is missing and a link to the page, never an exception.

Rules kept from the plan:
* Chain files older than five days are refused.
* Expired expiries are dropped, and today's expiry after 16:00 ET.
* An expiry whose expected move is under 35% of the next one is "spent" and is
  skipped, the same rule as highOiContractList.js#expectedMoveFromExpiries.
* The chain's root ``impliedVolatility`` is never quoted (AAPL and SPY both read
  29.0). IV is backed out of one expiry's expected move and labelled approximate.
* Max pain is not an app number: it is labelled as worked out from saved OI.
* Off the earnings watchlist means "not tracked", never "no earnings".
* News gives headlines only and claims no cause. Long Term gives the page's
  worked-out values only: never its buy signal.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from momx import chat_state

ET = ZoneInfo("America/New_York")
MAX_CHAIN_AGE_DAYS = 5
MAX_SECTOR_AGE_DAYS = 5
SPENT_EXPIRY_RATIO = 0.35
MAX_TABLE_ROWS = 40
DEFAULT_CHAIN_STRIKES = 13
WALLS_PER_SIDE = 8                # the High OI board's own default
LONG_TERM_MOS = 50                # the Long Term page's default margin of safety
MAX_POSSIBLE_IV_PERCENT = 500.0
MAX_HEADLINE = 200
CHAIN_SCHEMA_VERSION = 1

_TICKER = re.compile(r"^[A-Z][A-Z0-9.-]{0,9}$")
_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
_SECTOR_CARDS_PATH = Path(__file__).with_name("sector_cards.json")

MAX_PAIN_LABEL = "worked out from saved OI, 80 strikes only"
IV_LABEL = "approximate, backed out of this expiry's expected move"


# ---------------------------------------------------------------- reading the app

def _root(ctx: Mapping) -> Path:
    """The artifacts folder. Same override as the /api/bot-chat route."""
    if ctx.get("artifacts"):
        return Path(ctx["artifacts"])
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    if override:
        return Path(override)
    from config import ARTIFACTS_DIR
    return Path(ARTIFACTS_DIR)


# ---------------------------------------------------------------- small helpers

def _reply(lines: list[str], facts: dict | None = None, links: list[dict] | None = None,
           table: dict | None = None, as_of: str | None = None) -> dict:
    return {"lines": lines, "facts": facts or {}, "links": links or [], "table": table, "asOf": as_of}


def _now(ctx: Mapping) -> datetime:
    now = ctx.get("now")
    if not isinstance(now, datetime):
        return datetime.now(ET)
    return now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)


def _when(value: Any) -> datetime | None:
    """Any saved timestamp, in ET. A timestamp with no zone is taken as UTC."""
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return (parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)).astimezone(ET)


def _day(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _num(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _clock(when: datetime, now: datetime) -> str:
    return when.strftime("%H:%M ET") if when.date() == now.date() else when.strftime("%b %d %H:%M ET")


def _as_of(when: datetime | None, now: datetime) -> str | None:
    return f"as of {_clock(when, now)}" if when else None


def _age(seconds: float) -> str:
    seconds = max(0.0, seconds)
    for size, name in ((86400, "day"), (3600, "hour"), (60, "minute")):
        if seconds >= size:
            count = int(seconds // size)
            return f"{count} {name}{'' if count == 1 else 's'} old"
    count = int(seconds)
    return f"{count} second{'' if count == 1 else 's'} old"


def _long_day(day: date) -> str:
    return day.strftime("%a %Y-%m-%d")


def _money(value: Any) -> str:
    number = _num(value)
    return "-" if number is None else f"{number:,.2f}"


def _strike(value: Any) -> str:
    number = _num(value)
    return "-" if number is None else f"{number:g}"


def _whole(value: Any) -> str:
    number = _num(value)
    return "-" if number is None or number <= 0 else f"{int(round(number)):,}"


def _quote_cell(value: Any) -> str:
    """Bid, ask and delta. A zero prints "-", as on the page: overnight feeds
    report 0 until the market wakes, and a printed 0.00 would read as a quote."""
    number = _num(value)
    return "-" if not number else f"{number:.2f}"


def _clean_ticker(value: Any) -> str | None:
    text = str(value or "").strip().upper().lstrip("$")
    return text if _TICKER.match(text) else None


def _ticker(args: Mapping, ctx: Mapping, page_first: bool = False) -> str | None:
    """The question's ticker, else the conversation's or the one on screen."""
    order = ("pageTicker", "ticker") if page_first else ("ticker", "pageTicker")
    return _clean_ticker(args.get("ticker")) or next(
        (found for found in (_clean_ticker(ctx.get(key)) for key in order) if found), None)


def _bounded(value: Any, default: int, low: int, high: int) -> int:
    number = _num(value)
    return default if number is None else max(low, min(high, int(number)))


def _rows_table(title: str, columns: list[tuple], rows: list[dict], note: str | None = None,
                link: dict | None = None) -> dict:
    table: dict = {
        "kind": "rows",
        "title": title,
        "columns": [{"key": c[0], "label": c[1], **({"align": c[2]} if len(c) > 2 else {})} for c in columns],
        "rows": [{key: str(value) for key, value in row.items()} for row in rows[:MAX_TABLE_ROWS]],
    }
    if note:
        table["note"] = note
    if link:
        table["link"] = link
    return table


def _quick(ticker: str | None = None, sub: str | None = None) -> dict:
    link: dict = {"label": f"Open {ticker} on Quick Options" if ticker else "Open Quick Options", "view": "Quick Options"}
    if ticker:
        link["ticker"] = ticker
    if sub:
        link["sub"] = sub
        link["label"] = f"Open {ticker} High OI board" if ticker else "Open the High OI board"
    return link


def _chart(ticker: str) -> dict:
    return {"label": f"Open {ticker} chart", "view": "Charts & OI", "ticker": ticker, "timeframe": "5m"}


def _news_link(ticker: str | None = None) -> dict:
    link: dict = {"label": f"Open {ticker} news" if ticker else "Open News Feed", "view": "News Feed"}
    if ticker:
        link["ticker"] = ticker
    return link


def _long_link(ticker: str | None = None) -> dict:
    link: dict = {"label": f"Open {ticker} on Long Term" if ticker else "Open Long Term", "view": "Long Term"}
    if ticker:
        link["ticker"] = ticker
    return link


def _sectors_link() -> dict:
    return {"label": "Open Sectors", "view": "Sectors"}


def _earnings_link() -> dict:
    return {"label": "Open Earnings Calendar", "view": "Earnings Calendar"}


def _premarket_link(day: date | None = None) -> dict:
    link: dict = {"label": f"Open Premarket Scanner, {day.isoformat()}" if day else "Open Premarket Scanner",
                  "view": "Premarket Scanner"}
    if day:
        link["day"] = day.isoformat()
    return link


# ---------------------------------------------------------------- the saved chain

def _alive(expiry: Any, now: datetime) -> bool:
    day = _day(expiry)
    if day is None or day < now.date():
        return False
    return not (day == now.date() and now.hour >= 16)


def _disk_chain(ticker: str, ctx: Mapping) -> dict | None:
    try:
        with gzip.open(_root(ctx) / "oi_chain_cache" / f"{ticker}.json.gz", "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, EOFError):
        return None
    return payload if isinstance(payload, dict) and payload.get("symbol") else None


def _memory_chain(ticker: str) -> dict | None:
    # A plain dict read, without the app's chain lock: a slow chain build can
    # hold that lock, and the chat must never queue behind one.
    hit = chat_state.chain_cache(ticker)
    payload = hit[1] if isinstance(hit, (tuple, list)) and len(hit) > 1 else None
    return payload if isinstance(payload, dict) and payload.get("symbol") else None


class _Chain:
    """One saved chain with the rules applied: dead expiries gone, days recounted."""

    def __init__(self, ticker: str, payload: dict, at: datetime | None, now: datetime) -> None:
        self.ticker, self.payload, self.at, self.now = ticker, payload, at, now
        self.front_only = bool(payload.get("frontExpiryOnly") or payload.get("mobileFast"))
        self.spot = _num(payload.get("underlyingPrice")) or 0.0
        self.rows: list[dict] = []
        for row in payload.get("selectedExpiryChainRows") or []:
            if isinstance(row, dict) and _alive(row.get("expiry"), now):
                self.rows.append({**row, "days_to_expiration": (_day(row["expiry"]) - now.date()).days})
        saved = payload.get("expiryExpectedMoves")
        moves = [(key[:10], _num(value)) for key, value in (saved.items() if isinstance(saved, dict) else [])]
        self.moves = sorted((key, value) for key, value in moves if value and value > 0 and _alive(key, now))

    def is_spent(self, expiry: str) -> bool:
        for index, (key, move) in enumerate(self.moves):
            if key == expiry:
                following = self.moves[index + 1][1] if index + 1 < len(self.moves) else 0.0
                return following > 0 and move < following * SPENT_EXPIRY_RATIO
        return False

    def move(self, expiry: str) -> float | None:
        return dict(self.moves).get(expiry)

    def live_moves(self) -> list[tuple[str, float]]:
        return [(key, move) for key, move in self.moves if not self.is_spent(key)]

    def row_expiries(self) -> list[str]:
        return sorted({str(row["expiry"])[:10] for row in self.rows})

    def expiries(self) -> list[str]:
        """Expiries still worth quoting: expiryExpectedMoves first, as the plan
        says, then any expiry that has rows but no saved move."""
        return sorted({key for key, _ in self.live_moves()} | {
            key for key in self.row_expiries() if self.move(key) is None})

    def quotable_expiries(self) -> list[str]:
        """The expiries above that have chain rows to show."""
        rows = set(self.row_expiries())
        return [key for key in self.expiries() if key in rows]

    def front_move(self) -> float:
        """The 1-day move the High OI board uses to bound its walls."""
        live = self.live_moves()
        if live:
            return live[0][1]
        atm = self.payload.get("currentAtm") if isinstance(self.payload.get("currentAtm"), dict) else {}
        return (_num(atm.get("expectedMove")) or 0.0) if _alive(atm.get("expiry"), self.now) else 0.0

    def implied_vol_percent(self, expiry: str) -> float | None:
        """IV for ONE expiry, from its expected move. The inverse of
        api_server._expected_move_for_expiry: past one day that move is
        spot * IV * sqrt((days - 1) / 365); at 0 or 1 day it is the ATM
        straddle, which is about 0.8 * spot * IV * sqrt(T)."""
        move, expiry_day = self.move(expiry), _day(expiry)
        if not move or not self.spot or not expiry_day or not self.at:
            return None
        days = (expiry_day - self.at.date()).days
        if days <= 1:
            iv = move / (0.8 * self.spot * math.sqrt(1 / 365))
        else:
            iv = move / (self.spot * math.sqrt((days - 1) / 365))
        percent = iv * 100
        return percent if 0 < percent <= MAX_POSSIBLE_IV_PERCENT else None


def _open_chain(ticker: str, ctx: Mapping, what: str, link: dict) -> tuple[_Chain | None, dict | None]:
    """The newest usable saved chain, or a plain reply saying why there is none."""
    now = _now(ctx)
    found = []
    for payload in (_memory_chain(ticker), _disk_chain(ticker, ctx)):
        if payload and payload.get("optionQuoteSchemaVersion") == CHAIN_SCHEMA_VERSION:
            found.append((payload, _when(payload.get("scannedAt"))))
    if not found:
        return None, _reply([f"I have no saved {what} for {ticker}. Open it on Quick Options to load one."],
                            {"ticker": ticker, "missing": True}, [link])
    earliest = datetime.min.replace(tzinfo=ET)
    found.sort(key=lambda item: (not (item[0].get("frontExpiryOnly") or item[0].get("mobileFast")),
                                 item[1] or earliest), reverse=True)
    payload, at = found[0]
    if at is None:
        return None, _reply([f"The saved {what} for {ticker} has no time on it, so I will not quote it."],
                            {"ticker": ticker, "missing": True}, [link])
    if now - at > timedelta(days=MAX_CHAIN_AGE_DAYS):
        days = int((now - at).total_seconds() // 86400)
        return None, _reply(
            [f"The saved {what} for {ticker} is {days} days old (from {at.date().isoformat()}), too old to quote. "
             "Open it on Quick Options for a fresh one."],
            {"ticker": ticker, "tooOld": True}, [link])
    return _Chain(ticker, payload, at, now), None


def _expiry_wanted(raw: Any, now: datetime) -> date | None:
    """A date from "2026-11-20", "11/20", "friday", "next friday", "today"."""
    text = str(raw or "").strip().lower()
    if not text:
        return None
    if text in ("today", "0dte"):
        return now.date()
    if text == "tomorrow":
        return now.date() + timedelta(days=1)
    found = re.fullmatch(r"(next\s+)?([a-z]+)", text)
    if found and found.group(2) in _WEEKDAYS:
        ahead = (_WEEKDAYS.index(found.group(2)) - now.weekday()) % 7
        if ahead == 0 and now.hour >= 16:      # "friday", asked on a Friday after the close
            ahead = 7
        return now.date() + timedelta(days=ahead + (7 if found.group(1) else 0))
    if _day(text):
        return _day(text)
    found = re.fullmatch(r"(\d{1,2})[/-](\d{1,2})", text)
    if found:
        try:
            picked = date(now.year, int(found.group(1)), int(found.group(2)))
        except ValueError:
            return None
        return picked if picked >= now.date() else picked.replace(year=now.year + 1)
    return None


def _choose_expiry(chain: _Chain, raw: Any, usable: list[str]) -> tuple[str | None, str | None]:
    """(expiry to use from ``usable``, a note when it is not what was asked for)."""
    if not usable:
        return None, None
    wanted = _expiry_wanted(raw, chain.now)
    if raw and wanted is None:
        return usable[0], f"I could not read the expiry \"{str(raw)[:20]}\", so this is the nearest one."
    if wanted is None:
        return usable[0], None
    key = wanted.isoformat()
    if key in usable:
        return key, None
    if chain.is_spent(key):
        later = next((item for item in usable if item > key), usable[-1])
        return later, f"The {key} expiry is spent (its expected move has almost gone), so this is {later}."
    if not _alive(key, chain.now):
        return usable[0], f"The {key} expiry has already passed, so this is the nearest one, {usable[0]}."
    later = next((item for item in usable if item > key), usable[-1])
    return later, f"There is no saved {key} expiry, so this is the next one, {later}."


#: What a saved chain's underlyingPrice really is: Schwab's last print when the chain was saved. Overnight and before
#: the open that is the previous evening's extended-hours price, so it is never dated by the save time alone.
SPOT_WORDS = "Schwab's last quote when the chain was saved"
SPOT_NOTE = "it does not include overnight trading"


def _spot_text(chain: "_Chain") -> str:
    return f"{_money(chain.spot)} ({SPOT_WORDS}, {_clock(chain.at, chain.now)})"


def _off_session(now: datetime) -> bool:
    """Between 20:00 and 09:30 ET, and at the weekend: a "today" change is the previous session's."""
    return now.weekday() >= 5 or now.hour >= 20 or (now.hour, now.minute) < (9, 30)


def _front_only_note(chain: _Chain) -> list[str]:
    return [f"Only the nearest expiry is saved for {chain.ticker} right now."] if chain.front_only else []


def _iv_line(chain: _Chain, expiry: str) -> list[str]:
    iv = chain.implied_vol_percent(expiry)
    return [f"IV about {iv:.0f}% ({IV_LABEL})."] if iv else []


def _ask_ticker(what: str, link: dict) -> dict:
    return _reply([f"Which ticker do you want the {what} for?"], {"needsTicker": True}, [link])


def _chain_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx, page_first=True)
    if not ticker:
        return _ask_ticker("option chain", _quick())
    link = _quick(ticker)
    chain, problem = _open_chain(ticker, ctx, "option chain", link)
    if problem:
        return problem
    expiry, note = _choose_expiry(chain, args.get("expiry"), chain.quotable_expiries())
    if not expiry or chain.spot <= 0:
        return _reply([f"The saved chain for {ticker} has no live expiries left. Open Quick Options for a fresh one."],
                      {"ticker": ticker, "missing": True}, [link], as_of=_as_of(chain.at, chain.now))
    by_strike: dict[float, dict] = {}
    for row in chain.rows:
        strike = _num(row.get("strike"))
        if str(row["expiry"])[:10] == expiry and strike:
            by_strike.setdefault(strike, {})[str(row.get("side") or "").upper()] = row
    count = _bounded(args.get("strikes"), DEFAULT_CHAIN_STRIKES, 3, MAX_TABLE_ROWS)
    near = sorted(sorted(by_strike, key=lambda strike: abs(strike - chain.spot))[:count])
    table_rows = []
    for strike in near:
        call, put = by_strike[strike].get("CALL") or {}, by_strike[strike].get("PUT") or {}
        table_rows.append({
            "callDelta": _quote_cell(call.get("delta")), "callOi": _whole(call.get("open_interest")),
            "callBid": _quote_cell(call.get("bid")), "callAsk": _quote_cell(call.get("ask")),
            "strike": _strike(strike),
            "putBid": _quote_cell(put.get("bid")), "putAsk": _quote_cell(put.get("ask")),
            "putOi": _whole(put.get("open_interest")), "putDelta": _quote_cell(put.get("delta")),
        })
    expiry_day = _day(expiry)
    days = (expiry_day - chain.now.date()).days
    lines = ([note] if note else []) + [
        f"{ticker} option chain near the money, expiry {_long_day(expiry_day)} ({days} days), "
        f"price {_spot_text(chain)}.",
    ] + _iv_line(chain, expiry) + _front_only_note(chain)
    table = _rows_table(
        f"{ticker} chain, {expiry}",
        [("callDelta", "C Delta", "right"), ("callOi", "C OI", "right"), ("callBid", "C Bid", "right"),
         ("callAsk", "C Ask", "right"), ("strike", "Strike", "center"), ("putBid", "P Bid", "right"),
         ("putAsk", "P Ask", "right"), ("putOi", "P OI", "right"), ("putDelta", "P Delta", "right")],
        table_rows, note=f"Saved {_clock(chain.at, chain.now)}. Prices are from that save, not live.", link=link)
    facts = {"ticker": ticker, "expiry": expiry, "spot": chain.spot, "strikes": len(near),
             "scannedAt": chain.at.isoformat(), "expiries": chain.expiries()}
    return _reply(lines, facts, [link], table, _as_of(chain.at, chain.now))


def _wall_text(wall: Mapping) -> str:
    return f"{_strike(wall.get('strike'))} (OI {_whole(wall.get('openInterest'))}, expiry {wall.get('expiry')})"


def _walls_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("High OI board", _quick(sub="high-oi"))
    link = _quick(ticker, "high-oi")
    chain, problem = _open_chain(ticker, ctx, "High OI board", link)
    if problem:
        return problem
    from oi_auto_alerts import build_high_oi_walls
    shown = _bounded(args.get("n"), WALLS_PER_SIDE, 1, 20)
    # The whole board is always built at its own size: a smaller build can name
    # a different leader, and the chat must agree with the page.
    walls = build_high_oi_walls(chain.rows, chain.spot, as_of=chain.now, expected_move=chain.front_move(),
                                top_per_side=max(WALLS_PER_SIDE, shown))
    calls, puts = walls.get("calls") or [], walls.get("puts") or []
    as_of = _as_of(chain.at, chain.now)
    if not calls and not puts:
        return _reply([f"The saved chain for {ticker} has no open interest on the High OI board."],
                      {"ticker": ticker, "missing": True}, [link], as_of=as_of)
    side = str(args.get("side") or "").strip().lower()
    want_calls, want_puts = side not in ("put", "puts", "bear"), side not in ("call", "calls", "bull")
    lines = [f"{ticker} High OI board, price {_spot_text(chain)}."]
    if want_calls:
        lines.append(f"Biggest call wall: {_wall_text(calls[0])}." if calls else "No call walls saved.")
    if want_puts:
        lines.append(f"Biggest put wall: {_wall_text(puts[0])}." if puts else "No put walls saved.")
    lines += _front_only_note(chain)
    # The app's bought-or-sold estimate (momx/oi_sides.py), where the saved chain carries one for a wall.
    from momx import oi_sides
    estimates = {oi_sides.contract_key(row.get("side"), row.get("expiry"), row.get("strike")): row["sideEst"]
                 for row in chain.rows if isinstance(row.get("sideEst"), dict)}

    def estimate(label: str, wall: Mapping) -> dict | None:
        return estimates.get(oi_sides.contract_key(label, wall.get("expiry"), wall.get("strike")))

    def short(est: Mapping | None) -> str:
        if not est or not est.get("label"):
            return "-"
        share = float(est["share"])
        return f"{round((1 - share if est['label'] == 'mostly sold' else share) * 100)}% {'sold' if est['label'] == 'mostly sold' else 'bought'}"

    said = []
    for label, wanted, levels in (("call", want_calls, calls), ("put", want_puts, puts)):
        line = oi_sides.text(f"{_strike(levels[0].get('strike'))} {label}", estimate(label, levels[0])) if wanted and levels else None
        if line:
            said.append(line)
    if said:
        day = next((est.get("day") for est in estimates.values() if est.get("day")), None)
        lines.append(f"Estimate{f' for {day}' if day else ''}, not exchange data. {' '.join(said)}")
    rows = []
    for label, wanted, levels in (("Call", want_calls, calls), ("Put", want_puts, puts)):
        for wall in (levels[:shown] if wanted else []):
            rows.append({"side": label, "rank": wall.get("rank"), "strike": _strike(wall.get("strike")),
                         "oi": _whole(wall.get("openInterest")), "expiry": wall.get("expiry"),
                         "strength": wall.get("strength") or "-", "est": short(estimate(label, wall))})
    any_estimate = any(row["est"] != "-" for row in rows)
    table = _rows_table(f"{ticker} High OI board",
                        [("side", "Side"), ("rank", "#", "right"), ("strike", "Strike", "right"),
                         ("oi", "OI", "right"), ("expiry", "Expiry"), ("strength", "Strength"),
                         *([("est", "Est. traded")] if any_estimate else [])],
                        rows if any_estimate else [{k: v for k, v in row.items() if k != "est"} for row in rows],
                        note=f"High OI board, saved {_clock(chain.at, chain.now)}." + (f" {oi_sides.NOTE}" if any_estimate else ""), link=link)
    facts = {"ticker": ticker, "source": "High OI board", "spot": chain.spot, "scannedAt": chain.at.isoformat(),
             "calls": calls[:shown], "puts": puts[:shown]}
    return _reply(lines, facts, [link], table, as_of)


def _expected_move_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("expected move", _quick())
    link = _quick(ticker)
    chain, problem = _open_chain(ticker, ctx, "expected move", link)
    if problem:
        return problem
    as_of = _as_of(chain.at, chain.now)
    live = chain.live_moves()
    if not live or chain.spot <= 0:
        return _reply([f"The saved chain for {ticker} has no expected move for any live expiry."],
                      {"ticker": ticker, "missing": True}, [link], as_of=as_of)
    # Only expiries with a saved move can answer, whatever has rows.
    expiry, note = _choose_expiry(chain, args.get("expiry"), [key for key, _ in live])
    move = chain.move(expiry)
    expiry_day = _day(expiry)
    lines = ([note] if note else []) + [
        f"{ticker} expected move for {_long_day(expiry_day)}: +/-{_money(move)}, "
        f"from {_money(chain.spot - move)} to {_money(chain.spot + move)} around the price {_spot_text(chain)}.",
    ] + _iv_line(chain, expiry) + _front_only_note(chain)
    rows = []
    for key, value in live[:12]:
        iv = chain.implied_vol_percent(key)
        rows.append({"expiry": key, "days": (_day(key) - chain.now.date()).days, "move": _money(value),
                     "low": _money(chain.spot - value), "high": _money(chain.spot + value),
                     "iv": f"~{iv:.0f}%" if iv else "-"})
    table = _rows_table(f"{ticker} expected moves",
                        [("expiry", "Expiry"), ("days", "Days", "right"), ("move", "Move", "right"),
                         ("low", "Low", "right"), ("high", "High", "right"), ("iv", "IV (approx)", "right")],
                        rows, note=f"Saved {_clock(chain.at, chain.now)}. IV is {IV_LABEL}.", link=link)
    iv = chain.implied_vol_percent(expiry)
    facts = {"ticker": ticker, "expiry": expiry, "expectedMove": move, "spot": chain.spot,
             "ivApproxPercent": round(iv, 1) if iv else None, "scannedAt": chain.at.isoformat()}
    return _reply(lines, facts, [link], table, as_of)


NO_PREDICT_LINE = ("No one can predict a price, and this app does not try. "
                   "Here is the range the options market is pricing and the biggest walls.")


def _price_outlook_tool(args: Mapping, ctx: Mapping) -> dict:
    """Where is the price going (2026-11-15): never a forecast. One plain line, then the expected move and the walls
    from the saved chain, as their own tools give them."""
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("price range", _quick())
    move = _expected_move_tool({"ticker": ticker, "expiry": args.get("expiry")}, ctx)
    walls = _walls_tool({"ticker": ticker}, ctx)
    if (move.get("facts") or {}).get("error") or ((move.get("facts") or {}).get("missing") and (walls.get("facts") or {}).get("missing")):
        return {**move, "lines": [NO_PREDICT_LINE.split(". ")[0] + ".", *move["lines"]]}
    links = [*move["links"], *[link for link in walls["links"] if link not in move["links"]]]
    facts = {"ticker": ticker, "expected_move": move["facts"], "walls": walls["facts"]}
    return _reply([NO_PREDICT_LINE, *move["lines"], *walls["lines"]], facts, links, move.get("table") or walls.get("table"),
                  move.get("asOf") or walls.get("asOf"))


def max_pain_strike(rows: list[dict]) -> float | None:
    """The strike where option holders of one expiry would collect the least."""
    legs = []
    for row in rows:
        strike, oi = _num(row.get("strike")), _num(row.get("open_interest"))
        if strike and oi and oi > 0:
            legs.append((str(row.get("side") or "").upper() == "PUT", strike, oi))
    if not legs:
        return None

    def payout(settle: float) -> float:
        return sum(oi * (max(0.0, strike - settle) if is_put else max(0.0, settle - strike))
                   for is_put, strike, oi in legs)

    return min(sorted({strike for _, strike, _ in legs}), key=payout)


def _max_pain_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("max pain", _quick())
    link = _quick(ticker)
    chain, problem = _open_chain(ticker, ctx, "chain for max pain", link)
    if problem:
        return problem
    as_of = _as_of(chain.at, chain.now)
    expiry, note = _choose_expiry(chain, args.get("expiry"), chain.quotable_expiries())
    pain =max_pain_strike([row for row in chain.rows if str(row["expiry"])[:10] == expiry]) if expiry else None
    if pain is None:
        return _reply([f"The saved chain for {ticker} has no open interest to work out max pain from."],
                      {"ticker": ticker, "missing": True}, [link], as_of=as_of)
    lines = ([note] if note else []) + [
        f"{ticker} max pain for {_long_day(_day(expiry))}: {_strike(pain)} ({MAX_PAIN_LABEL}), "
        f"saved {_clock(chain.at, chain.now)}. The app does not show this number on any page.",
    ] + _front_only_note(chain)
    facts = {"ticker": ticker, "expiry": expiry, "maxPain": pain, "label": MAX_PAIN_LABEL, "spot": chain.spot,
             "scannedAt": chain.at.isoformat()}
    return _reply(lines, facts, [link], None, as_of)


# ---------------------------------------------------------------- quote

def _quote_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _reply(["Which ticker do you want the price for?"], {"needsTicker": True})
    now, link = _now(ctx), _chart(ticker)
    held = chat_state.latest_equity(ticker) or {}
    last, at = _num(held.get("last")), _when(held.get("lastAt") or held.get("receivedAt"))
    if last and last > 0 and at:
        age = (now - at).total_seconds()
        lines = [f"{ticker} last {_money(last)} from the live stream, {_age(age)} ({_clock(at, now)})."]
        return _reply(lines, {"ticker": ticker, "price": last, "source": "stream", "ageSeconds": round(max(age, 0.0))},
                      [link], None, _as_of(at, now))
    chain, problem = _open_chain(ticker, ctx, "price", link)
    bar = _chart_bar(ticker)
    # The chart tape's newest candle carries its own time, so it is the better price unless the chain was saved well after it.
    if bar and (chain is None or chain.spot <= 0 or bar[0] >= chain.at - CHART_BAR_SLACK):
        bar_at, close = bar
        age = (now - bar_at).total_seconds()
        lines = [f"{ticker} {_money(close)}, the close of the newest candle on the chart the app holds, {_clock(bar_at, now)} "
                 f"({_age(age)}). This is not a live price."]
        return _reply(lines, {"ticker": ticker, "price": close, "source": "chart candle", "ageSeconds": round(max(age, 0.0)),
                              "candleAt": bar_at.isoformat()}, [link], None, _as_of(bar_at, now))
    if problem:
        return problem
    if chain.spot <= 0:
        return _reply([f"I have no saved price for {ticker}."], {"ticker": ticker, "missing": True}, [link])
    age = (now - chain.at).total_seconds()
    change = _num(chain.payload.get("todayChangePercent"))
    earlier = _off_session(now) or chain.at.date() != now.date()
    moved = ((f" Its {change:+.2f}% change is the previous session's." if earlier else f" It was {change:+.2f}% on the day when saved.")
             if change is not None else "")
    lines = [f"{ticker} {_money(chain.spot)}: {SPOT_WORDS}, {_clock(chain.at, now)} ({_age(age)}); {SPOT_NOTE}.{moved} "
             "This is not a live price."]
    facts = {"ticker": ticker, "price": chain.spot, "source": "option chain", "ageSeconds": round(max(age, 0.0)),
             "changePercent": change, "previousSession": earlier}
    return _reply(lines, facts, [link], None, _as_of(chain.at, now))


#: A chart candle this much older than the chain's save still wins: the chain's price is not from its save time.
CHART_BAR_SLACK = timedelta(minutes=15)


def _chart_bar(ticker: str) -> tuple[datetime, float] | None:
    """The newest candle of a chart tape the app already holds: a peek, never a file read or a build."""
    try:
        from momx import chat_chart
        found = chat_chart.latest_bar(ticker)
    except Exception:  # noqa: BLE001 - no candle is an ordinary answer
        return None
    if not found:
        return None
    return datetime.fromtimestamp(found[0], ET), found[1]


# ---------------------------------------------------------------- sectors

_cards_memo: list | None = None


def sector_cards() -> list[dict]:
    global _cards_memo
    if _cards_memo is None:
        try:
            _cards_memo = json.loads(_SECTOR_CARDS_PATH.read_text(encoding="utf-8")).get("cards") or []
        except (OSError, ValueError):
            _cards_memo = []
    return _cards_memo


def _card_of(ticker: str) -> dict | None:
    for card in sector_cards():
        if ticker == card.get("etf") or ticker in {m["symbol"] for m in card["leaders"] + card["more"]}:
            return card
    return None


def _latest_sector_file(ctx: Mapping, now: datetime) -> tuple[date, dict] | None:
    folder = _root(ctx) / "momx_sector_hot"
    try:
        names = sorted((path.stem for path in folder.glob("*.json")), reverse=True)
    except OSError:
        return None
    for name in names:
        day = _day(name)
        if day is None or day > now.date():
            continue
        if (now.date() - day).days > MAX_SECTOR_AGE_DAYS:
            return None
        try:
            document = json.loads((folder / f"{name}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(document, dict):
            return day, document
    return None


def _sector_about_ticker(ticker: str) -> dict:
    card = _card_of(ticker)
    industry = None
    try:
        from momx.industry_lookup import cached_profiles
        industry = (cached_profiles([ticker]).get(ticker) or {}).get("industry")
    except Exception:  # noqa: BLE001 - the card still answers
        industry = None
    if not card and not industry:
        return _reply([f"{ticker} is not on any sector card, and the app has no industry saved for it."],
                      {"ticker": ticker, "missing": True}, [_sectors_link()])
    lines = []
    if card:
        group = next((g["label"] for g in card["groups"] if ticker in g["symbols"]), None)
        lines.append(f"{ticker} is on the {card['name']} card" + (f", in {group}." if group else "."))
    if industry:
        lines.append(f"The scanner files {ticker} under {industry}.")
    facts = {"ticker": ticker, "sector": card["name"] if card else None, "etf": card["etf"] if card else None,
             "industry": industry}
    return _reply(lines, facts, [_sectors_link(), _chart(ticker)])


def _sector_card_reply(card: dict) -> dict:
    leaders = ", ".join(member["symbol"] for member in card["leaders"][:8])
    groups = ", ".join(group["label"] for group in card["groups"])
    lines = [f"{card['name']} (ETF {card['etf']}): {card['about']}", f"Biggest names: {leaders}.", f"Groups: {groups}."]
    return _reply(lines, {"sector": card["name"], "etf": card["etf"]}, [_sectors_link()])


def _sectors_tool(args: Mapping, ctx: Mapping) -> dict:
    asked = str(args.get("sector") or "").strip().lower()
    if asked:
        card = next((c for c in sector_cards() if asked in c["name"].lower()), None)
        if card:
            return _sector_card_reply(card)
    ticker = _clean_ticker(args.get("ticker"))
    if ticker:
        return _sector_about_ticker(ticker)
    now, link = _now(ctx), _sectors_link()
    latest = _latest_sector_file(ctx, now)
    if latest is None:
        return _reply(["No sector has been marked hot in the last few sessions."], {"missing": True}, [link])
    day, document = latest
    boards = document.get("boards") if isinstance(document.get("boards"), dict) else {}
    rows, lines, newest = [], [], None
    for board, held in boards.items():
        leaders = held.get("leaders") if isinstance(held, dict) and isinstance(held.get("leaders"), dict) else {}
        hot = [str(name) for name in (held.get("hot") or [])] if isinstance(held, dict) else []
        if hot:
            lines.append(f"{board}: {', '.join(hot)}.")
        for name in hot:
            members = sorted(((symbol, item) for symbol, item in leaders.items() if item.get("sector") == name),
                             key=lambda pair: pair[1].get("rank") or 99)
            times = [t for t in (_when(item.get("at")) for _, item in members) if t]
            first = min(times) if times else None
            newest = max(newest, first) if newest and first else (first or newest)
            rows.append({"board": board, "sector": name, "at": first.strftime("%H:%M ET") if first else "-",
                         "leaders": ", ".join(f"{symbol} {_num(item.get('pct')) or 0:+.2f}%" for symbol, item in members) or "-"})
    when_text = "today" if day == now.date() else f"on {_long_day(day)}"
    if not rows:
        return _reply([f"No sector was marked hot {when_text}."], {"day": day.isoformat(), "hot": []}, [link])
    lines.insert(0, f"Hot sectors {when_text}, as the scanner marked them:")
    table = _rows_table(f"Hot sectors, {day.isoformat()}",
                        [("board", "Board"), ("sector", "Sector"), ("at", "Lit at"), ("leaders", "Leaders when lit")],
                        rows, note="A sector can only turn hot before 11:00 ET, and stays hot for the day.", link=link)
    facts = {"day": day.isoformat(), "hot": rows}
    return _reply(lines, facts, [link], table, _as_of(newest, now))


# ---------------------------------------------------------------- premarket

def _premarket_day(raw: Any, now: datetime, days: list[date]) -> date | None:
    """A named day. "yesterday" means the last session on file before today."""
    text = str(raw or "").strip().lower()
    if not text or text == "today":
        return now.date()
    if text in ("yesterday", "last session"):
        return next((day for day in days if day < now.date()), now.date() - timedelta(days=1))
    found = re.fullmatch(r"(?:last\s+)?([a-z]+)", text)
    if found and found.group(1) in _WEEKDAYS:
        return now.date() - timedelta(days=(now.weekday() - _WEEKDAYS.index(found.group(1))) % 7)
    return _day(text)


def _signal_list(row: Mapping) -> list[str]:
    out: list[str] = []
    for key in ("signals48", "signals920", "signalsCyanHigher", "signalsYellowHigher", "signalsMacdHigher"):
        for name in row.get(key) or []:
            if str(name) not in out:
                out.append(str(name))
    return out


def _premarket_tool(args: Mapping, ctx: Mapping) -> dict:
    now = _now(ctx)
    folder = _root(ctx) / "premarket_scanner_history"
    try:
        days = sorted((d for d in (_day(p.stem) for p in folder.glob("*.json")) if d and d <= now.date()), reverse=True)
    except OSError:
        days = []
    raw_day = args.get("day") or ctx.get("day")
    day = _premarket_day(raw_day, now, days)
    if day is None:
        return _reply([f"I could not read the day \"{str(raw_day)[:20]}\". Try a date like 2026-11-10."],
                      {"missing": True}, [_premarket_link()])
    note = None
    if day not in days:
        if raw_day and str(raw_day).strip().lower() != "today":
            return _reply([f"There is no premarket scanner record for {_long_day(day)}. The app keeps 30 days."],
                          {"day": day.isoformat(), "missing": True}, [_premarket_link()])
        if not days:
            return _reply(["There is no premarket scanner record saved yet."], {"missing": True}, [_premarket_link()])
        note = f"Nothing is saved for today yet, so this is the last session, {_long_day(days[0])}."
        day = days[0]
    link = _premarket_link(day)
    try:
        document = json.loads((folder / f"{day.isoformat()}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _reply([f"The premarket record for {_long_day(day)} could not be read."],
                      {"day": day.isoformat(), "missing": True}, [link])
    entries = document.get("rows") if isinstance(document, dict) and isinstance(document.get("rows"), dict) else {}
    head = [note] if note else []
    ticker = _clean_ticker(args.get("ticker"))
    if ticker:
        entry = entries.get(ticker)
        if not isinstance(entry, dict):
            return _reply(head + [f"{ticker} was not on the premarket scanner on {_long_day(day)}."],
                          {"ticker": ticker, "day": day.isoformat(), "found": False}, [link])
        row = entry.get("row") if isinstance(entry.get("row"), dict) else {}
        first, last = _when(entry.get("firstSeenAt")), _when(entry.get("lastSeenAt"))
        signals = _signal_list(row)
        catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), dict) else {}
        change = _num(row.get("changePct"))
        lines = head + [
            f"{ticker} was on the premarket scanner on {_long_day(day)}: first seen "
            f"{first.strftime('%H:%M ET') if first else 'unknown'}, last changed {last.strftime('%H:%M ET') if last else 'unknown'}.",
            f"Signals: {', '.join(signals) or 'none'}{' (forming)' if row.get('forming') else ''}. "
            f"Strength {row.get('strength') or 'unknown'}. Price {_money(row.get('lastPrice'))}"
            f"{f', {change:+.2f}%' if change is not None else ''} at that last change.",
        ]
        if catalyst.get("headline"):
            lines.append(f"Headline on the row: {str(catalyst['headline'])[:MAX_HEADLINE]}")
        facts = {"ticker": ticker, "day": day.isoformat(), "found": True, "signals": signals,
                 "firstSeenAt": first.isoformat() if first else None, "price": _num(row.get("lastPrice"))}
        return _reply(lines, facts, [link], None, _as_of(last, now))
    if not entries:
        return _reply(head + [f"The premarket scanner matched nothing on {_long_day(day)}."],
                      {"day": day.isoformat(), "count": 0}, [link])
    ordered = sorted(((s, e) for s, e in entries.items() if isinstance(e, dict)),
                     key=lambda pair: str(pair[1].get("firstSeenAt") or ""))
    rows, seen = [], []
    for symbol, entry in ordered:
        row = entry.get("row") if isinstance(entry.get("row"), dict) else {}
        first, last = _when(entry.get("firstSeenAt")), _when(entry.get("lastSeenAt"))
        if last:
            seen.append(last)
        change = _num(row.get("changePct"))
        catalyst = row.get("catalyst") if isinstance(row.get("catalyst"), dict) else {}
        signals = ", ".join(_signal_list(row)) or "-"
        rows.append({"symbol": symbol, "first": first.strftime("%H:%M") if first else "-",
                     "signals": signals + (" (forming)" if row.get("forming") else ""),
                     "strength": row.get("strength") or "-", "chg": f"{change:+.2f}%" if change is not None else "-",
                     "price": _money(row.get("lastPrice")), "catalyst": catalyst.get("tag") or "-"})
    lines = head + [f"{len(rows)} tickers on the premarket scanner on {_long_day(day)}."]
    table = _rows_table(f"Premarket scanner, {day.isoformat()}",
                        [("symbol", "Symbol"), ("first", "First seen ET"), ("signals", "Signals"),
                         ("strength", "Strength"), ("chg", "% Chg", "right"), ("price", "Price", "right"),
                         ("catalyst", "Catalyst")],
                        rows, note=f"Showing {min(len(rows), MAX_TABLE_ROWS)} of {len(rows)}.", link=link)
    newest = max(seen) if seen else None
    return _reply(lines, {"day": day.isoformat(), "count": len(rows)}, [link], table, _as_of(newest, now))


def _morning_brief_tool(args: Mapping, ctx: Mapping) -> dict:
    """Answers in the chat with no link: the brief has no page of its own."""
    from premarket_scanner_history import load_briefing, load_history
    now = _now(ctx)
    folder = _root(ctx) / "premarket_scanner_history"
    brief = load_briefing(now, directory=folder)
    head: list[str] = []
    day = now.date()
    if not brief:
        older = next((item for item in load_history(directory=folder, days=7)
                      if isinstance(item.get("briefing"), dict) and item["briefing"].get("lines")
                      and (_day(item.get("date")) or now.date()) <= now.date()), None)
        if older is None:
            return _reply(["No morning brief is saved yet. It builds weekday mornings 5:30-9:35 AM ET."], {"missing": True})
        brief, day = older["briefing"], _day(older.get("date")) or now.date()
        head = [f"Today's brief is not saved yet (it builds weekday mornings 5:30-9:35 AM ET). "
                f"This is the last one, from {_long_day(day)}."]
    at = _when(brief.get("generatedAt"))
    text = [str(line).replace("**", "") for line in brief.get("lines") or []]
    return _reply(head + text, {"day": day.isoformat(), "generatedAt": at.isoformat() if at else None}, [], None, _as_of(at, now))


# ---------------------------------------------------------------- news

def _news_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("news", _news_link())
    now, link = _now(ctx), _news_link(ticker)
    payload = chat_state.news_latest([ticker])
    if payload is None:
        return _reply(["The saved headlines are not available right now. Try the News Feed page."],
                      {"ticker": ticker, "missing": True}, [link])
    if payload.get("storeError"):
        return _reply(["The saved headlines could not be read just now."], {"ticker": ticker, "missing": True}, [link])
    limit = _bounded(args.get("limit"), 5, 1, 10)
    items = [item for item in payload.get("feed") or [] if item.get("symbol") == ticker and item.get("headline")]
    items.sort(key=lambda item: _when(item.get("publishedAt")) or datetime.min.replace(tzinfo=ET), reverse=True)
    items = items[:limit]
    if not items:
        days = payload.get("lookbackDays")
        return _reply([f"No headlines saved for {ticker}" + (f" in the last {days} days." if days else ".")],
                      {"ticker": ticker, "count": 0}, [link])
    lines, rows = [f"Latest saved headlines for {ticker}:"], []
    for item in items:
        at = _when(item.get("publishedAt"))
        stamp = _clock(at, now) if at else "time unknown"
        headline = str(item["headline"])[:MAX_HEADLINE]
        source = str(item.get("source") or "unknown source")
        lines.append(f"{stamp}, {source}: {headline}")
        rows.append({"time": stamp, "source": source, "headline": headline})
    lines.append("These are headlines only. The app does not know what caused any price move.")
    newest = _when(items[0].get("publishedAt"))
    table = _rows_table(f"{ticker} headlines", [("time", "Time"), ("source", "Source"), ("headline", "Headline")],
                        rows, link=link)
    facts = {"ticker": ticker, "count": len(rows), "newestAt": newest.isoformat() if newest else None}
    return _reply(lines, facts, [link], table, _as_of(newest, now))


# ---------------------------------------------------------------- earnings

def _earnings_tool(args: Mapping, ctx: Mapping) -> dict:
    now, link = _now(ctx), _earnings_link()
    cached = chat_state.earnings_cache()
    ticker = _ticker(args, ctx)
    if not (isinstance(cached, (tuple, list)) and len(cached) >= 3 and isinstance(cached[2], dict)):
        return _reply(["The earnings calendar has not loaded since the app last restarted. "
                       "Open Earnings Calendar to load it."], {"ticker": ticker, "missing": True}, [link])
    watchlist, payload = {str(s).upper() for s in cached[1] or []}, cached[2]
    at = _when(payload.get("refreshedAt"))
    as_of = _as_of(at, now)
    rows = [row for row in payload.get("rows") or []
            if isinstance(row, dict) and (_day(row.get("date")) or date.min) >= now.date()]
    if ticker:
        if ticker not in watchlist:
            return _reply([f"{ticker} is not tracked on the earnings calendar, which covers only the saved watchlist, "
                           "so I cannot say when it reports."], {"ticker": ticker, "tracked": False}, [link], as_of=as_of)
        row = next((item for item in rows if str(item.get("symbol")).upper() == ticker), None)
        if row is None:
            end = payload.get("endDate")
            return _reply([f"{ticker} has no earnings date on the calendar" + (f" through {end}." if end else ".")],
                          {"ticker": ticker, "tracked": True, "date": None}, [link], as_of=as_of)
        day = _day(row.get("date"))
        days = (day - now.date()).days
        lines = [f"{ticker} reports {_long_day(day)} ({days} days away), {row.get('timing') or 'time not set'}."]
        facts = {"ticker": ticker, "tracked": True, "date": day.isoformat(), "timing": row.get("timing"),
                 "source": row.get("source")}
        return _reply(lines, facts, [link], None, as_of)
    if not rows:
        return _reply(["No upcoming earnings are on the calendar."], {"count": 0}, [link], as_of=as_of)
    shown = [{"symbol": row.get("symbol"), "date": row.get("date"),
              "days": (_day(row["date"]) - now.date()).days, "timing": row.get("timing") or "-"}
             for row in rows[:MAX_TABLE_ROWS]]
    table = _rows_table("Upcoming earnings", [("symbol", "Symbol"), ("date", "Date"), ("days", "Days", "right"),
                                              ("timing", "Timing")],
                        shown, note=f"Showing {len(shown)} of {len(rows)}, saved watchlist only.", link=link)
    return _reply([f"{len(rows)} watchlist tickers have earnings coming up."], {"count": len(rows)}, [link], table, as_of)


# ---------------------------------------------------------------- Long Term

_LONG_TERM_FACTS = (("sticker", "Sticker price", "money"), ("buffett", "Buffett value", "money"),
                    ("fairValue", "Fair value", "money"), ("payback", "Payback years", "plain"),
                    ("roic", "ROIC", "percent"), ("roe", "ROE", "percent"),
                    ("salesGrowth", "Sales growth", "percent"), ("epsGrowth", "EPS growth", "percent"),
                    ("fcfGrowth", "Free cash flow growth", "percent"), ("debtToEquity", "Debt to equity", "plain"))


def _long_term_value(value: float, kind: str) -> str:
    if kind == "money":
        return _money(value)
    return f"{value * 100:.1f}%" if kind == "percent" else f"{value:.2f}"


def _long_term_tool(args: Mapping, ctx: Mapping) -> dict:
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("Long Term numbers", _long_link())
    now, link = _now(ctx), _long_link(ticker)
    from long_term.service import service_instance
    # No quote is passed: the page's quote read goes to Schwab.
    payload = service_instance().detail_payload(ticker, LONG_TERM_MOS, None)
    if not payload or not isinstance(payload.get("row"), dict):
        return _reply([f"{ticker} is not on the Long Term screen."], {"ticker": ticker, "missing": True}, [link])
    row = payload["row"]
    at = _when(payload.get("fetchedAt") or payload.get("refreshedAt"))
    as_of = _as_of(at, now)
    status = row.get("signal")
    if status == "ETF":
        return _reply([f"{ticker} is an ETF. Long Term scores companies only."], {"ticker": ticker, "etf": True}, [link], as_of=as_of)
    if status in ("NO_FILINGS", "CHECK_DATA") or row.get("sticker") is None and row.get("roic") is None:
        return _reply([f"Long Term has no usable filings data for {ticker} yet."], {"ticker": ticker, "missing": True}, [link], as_of=as_of)
    name = row.get("name") or ticker
    facts: dict = {"ticker": ticker, "name": name, "fetchedAt": at.isoformat() if at else None}
    shown = []
    for key, label, kind in _LONG_TERM_FACTS:
        value = _num(row.get(key))
        if value is not None:
            facts[key] = value
            shown.append({"label": label, "value": _long_term_value(value, kind)})
    big5 = row.get("big5")
    if big5 is not None:
        facts["big5"] = big5
        shown.append({"label": "Big 5 checks passed", "value": str(big5)})
    lines = [f"{name} on Long Term, worked out from its filings"
             + (f", last updated {_clock(at, now)}." if at else ".")]
    lines += [f"{item['label']}: {item['value']}." for item in shown[:6]]
    if status == "STALE":
        lines.append(str(row.get("reason") or "This data is old."))
    lines.append("These are facts from the Long Term page, not a buy or sell call.")
    table = _rows_table(f"{ticker} Long Term", [("label", "Measure"), ("value", "Value", "right")], shown, link=link)
    return _reply(lines, facts, [link], table, as_of)


# ---------------------------------------------------------------- open interest 1 to 12 months out

LONG_OI_ROWS = 10
LONG_OI_BAND = 0.05


def _month_day(day: str) -> str:
    parsed = _day(day)
    return parsed.strftime("%b %d %Y").replace(" 0", " ") if parsed else str(day)


def _delta_text(delta: float) -> str:
    """Two decimals, but three just under 0.20: an ENTRY at 0.198 must not read "0.20", which looks outside the
    under-0.20 rule."""
    size = abs(delta)
    return f"{delta:.3f}" if 0.195 <= size < 0.20 else f"{delta:.2f}"


def _oi_row_text(row: Mapping) -> str:
    side = "call" if row["side"] == "CALL" else "put"
    delta = f"delta {_delta_text(row['delta'])}" if row.get("delta") is not None else "no delta"
    entry = " (ENTRY)" if row.get("entry") else ""
    return f"{_strike(row['strike'])} {side}{entry}, {_month_day(row['expiry'])} ({row['days']} days), OI {_whole(row['oi'])}, {delta}"


#: The biggest NEW open interest since the previous posting goes on top of the LEAP answer (Ganesh 2026-11-19: "what
#: change column do it will show new added top of that?"). At most this many, and only a real build of at least
#: LONG_OI_NEW_MIN contracts.
LONG_OI_NEW_ROWS = 3
LONG_OI_NEW_MIN = 1000


def _long_oi_changes(ticker: str, rows: list[dict], root: Path) -> tuple[dict[str, int], str | None]:
    """(contract key -> open interest change, the day of the earlier reading) from the LEAP OI Scanner's saved copy
    (momx/leap_oi_scanner.py keeps the previous posting as prevOi). A change is given only for a contract whose open
    interest in that copy is the SAME as in this answer, so both describe one posting; otherwise nothing is made up."""
    from momx import leap_oi_scanner
    saved = leap_oi_scanner._read_compact_disk(root, ticker)
    prev = saved.get("prevOi") if isinstance(saved, dict) and isinstance(saved.get("prevOi"), dict) else None
    if not prev:
        return {}, None
    current, _ = leap_oi_scanner._oi_map(saved)
    changes: dict[str, int] = {}
    for row in rows:
        key = leap_oi_scanner.contract_key(row)
        if key in prev and key in current and int(current[key]) == int(row.get("oi") or 0):
            changes[key] = int(row["oi"]) - int(prev[key] or 0)
    return changes, leap_oi_scanner._et_day(saved.get("prevAt"))


def _money_short(value: Any) -> str:
    n = float(value or 0)
    return f"${n / 1_000_000:.1f}M" if n >= 1_000_000 else f"${round(n / 1000):,}K" if n >= 1000 else f"${round(n)}"


def _today_cell(flow: Mapping | None) -> str:
    """'1,200 Bought' / '800 Sold' / '5 ?' for the chat table; '-' when the contract did not trade."""
    if not flow or not flow.get("volume"):
        return "-"
    return f"{int(flow['volume']):,} " + {"ask": "Bought", "bid": "Sold"}.get(flow.get("fill"), "?")


def _opened_closed(change: Any) -> str:
    """'+52,569 opened' / '2,166 closed' / 'no change' / '-' (no earlier reading)."""
    if change is None:
        return "-"
    change = int(change)
    return f"+{change:,} opened" if change > 0 else f"{-change:,} closed" if change < 0 else "no change"


def _signed(value: int) -> str:
    return f"+{value:,}" if value > 0 else f"{value:,}" if value < 0 else "0"


def _long_oi_tool(args: Mapping, ctx: Mapping) -> dict:
    """The strikes 1 to 12 months out with the most open interest, calls and puts, with delta. The one chat answer that
    may ask Schwab, once per ticker per 30 minutes (momx/long_oi.py)."""
    from momx import long_oi
    ticker = _ticker(args, ctx)
    if not ticker:
        return _ask_ticker("open interest 1 to 12 months out", _quick())
    now = _now(ctx)
    months_from = _bounded(args.get("months_from"), 1, 1, 12)
    months_to = max(months_from, _bounded(args.get("months_to"), 12, 1, 12))
    delta = _num(args.get("delta"))
    delta = delta if delta is not None and 0.05 <= delta <= 0.95 else None
    side = str(args.get("side") or "").lower()
    link = _chart(ticker)
    try:
        doc = long_oi.chain_for(ticker, _root(ctx), now)
    except long_oi.Unavailable as exc:
        return _reply([str(exc)], {"ticker": ticker, "missing": True}, [link])
    fetched = _when(doc.get("fetchedAt")) or now
    long_oi.mark_entries(doc["rows"], _num(doc.get("spot")))
    # 1 month = 28 days (so the monthly expiry 4 weeks out counts), 12 months = 366 days.
    min_days = 28 if months_from == 1 else months_from * 30 - 2
    max_days = 366 if months_to == 12 else months_to * 30 + 2
    sides = [("CALL", "Calls")] if side == "bull" else [("PUT", "Puts")] if side == "bear" else [("CALL", "Calls"), ("PUT", "Puts")]
    picked = {key: long_oi.top_strikes(doc["rows"], side=key, min_days=min_days, max_days=max_days, delta=delta,
                                       band=LONG_OI_BAND, n=LONG_OI_ROWS, min_delta=long_oi.MIN_DELTA) for key, _ in sides}
    window = [row for row in doc["rows"] if min_days <= row["days"] <= max_days]
    expiries = sorted({row["expiry"] for row in window})
    span = f"{months_from} to {months_to} months out" if months_from != months_to else f"about {months_to} months out"
    filter_text = f", delta near {delta:.2f} (+/- {LONG_OI_BAND:.2f})" if delta is not None else ""
    spot = _num(doc.get("spot"))
    lines = [f"{ticker} strikes with the most open interest, expiries {span}"
             + (f" ({_month_day(expiries[0])} to {_month_day(expiries[-1])}, {len(expiries)} expiries)" if expiries else "")
             + filter_text + (f". Price {_money(spot)}" if spot else "") + f" (Schwab, {_clock(fetched, now)})."]
    from momx import leap_oi_scanner
    changes, since = _long_oi_changes(ticker, window, _root(ctx))
    since_text = f"since the {_month_day(since)} reading" if since else "since the previous reading"
    wanted_sides = {key for key, _ in sides}
    new_rows = sorted((row for row in window if row["side"] in wanted_sides
                       and abs(row.get("delta") or 0) >= long_oi.MIN_DELTA - 1e-9
                       and (delta is None or abs(abs(row["delta"]) - delta) <= LONG_OI_BAND + 1e-9)
                       and changes.get(leap_oi_scanner.contract_key(row), 0) >= LONG_OI_NEW_MIN),
                      key=lambda row: (-changes[leap_oi_scanner.contract_key(row)], row["days"]))[:LONG_OI_NEW_ROWS]
    if new_rows:
        lines.append(f"Biggest new open interest {since_text}: " + "; ".join(
            f"{_strike(row['strike'])} {'call' if row['side'] == 'CALL' else 'put'} {_month_day(row['expiry'])}, "
            f"{_signed(changes[leap_oi_scanner.contract_key(row)])} (now {_whole(row['oi'])})" for row in new_rows)
            + ". New open interest means positions were opened; it cannot show whether they were bought or sold.")
    elif changes:
        lines.append(f"No strike added {LONG_OI_NEW_MIN:,} or more contracts of open interest {since_text}.")
    for key, label in sides:
        best = picked[key]
        word = "call" if key == "CALL" else "put"
        lines.append(f"Most {word} OI: {_oi_row_text(best[0])}." if best else
                     f"No {word}s{filter_text} with open interest {span}.")
    # The option chain's ENTRY (just under 0.20 delta) on each expiry in the window; the one holding the most OI.
    for key, label in sides:
        word = "call" if key == "CALL" else "put"
        entries = sorted((row for row in window if row["side"] == key and row.get("entry") and row["oi"] > 0
                          and abs(row["delta"]) >= long_oi.MIN_DELTA - 1e-9),
                         key=lambda row: (-row["oi"], row["days"]))
        if entries:
            lines.append(f"ENTRY {word} with the most OI (the option chain's ENTRY rule, delta just under 0.20): {_oi_row_text(entries[0])}.")
    lines.append(f"Only strikes with delta {long_oi.MIN_DELTA:.2f} or more: no far-away lottery strikes. "
                 "ENTRY marks the contract the option chain marks ENTRY on that expiry.")
    lines.append("Open interest = how many of these option tickets people are holding right now (it updates once a day, "
                 "before the open). Delta = roughly the chance the option ends up worth something: 0.20 is about 1 in 5. "
                 "Facts only, not a pick.")
    # Today's trades on these strikes (2026-11-20): AGX Flow's numbers for the same contracts, so the answer says
    # bought or sold without sending the trader to AGX Flow.
    flow_rows = [{**row, "ticker": ticker} for row in window]
    leap_oi_scanner.with_flow(flow_rows, _root(ctx))
    flows = {leap_oi_scanner.contract_key(row): row["flow"] for row in flow_rows if row.get("flow")}
    fill_word = {"ask": "Bought", "bid": "Sold"}
    traded = sorted((row for row in flow_rows if row.get("flow") and row["side"] in wanted_sides
                     and abs(row.get("delta") or 0) >= long_oi.MIN_DELTA - 1e-9),
                    key=lambda row: -row["flow"]["premium"])[:3]
    if traded:
        lines.append("Traded today (the app's guess from where the last trade printed: at the ask = Bought, at the bid = "
                     "Sold): " + "; ".join(
                         f"{_strike(row['strike'])} {'call' if row['side'] == 'CALL' else 'put'} {_month_day(row['expiry'])}, "
                         f"{_whole(row['flow']['volume'])} contracts, about {_money_short(row['flow']['premium'])}, "
                         f"{fill_word.get(row['flow']['fill'], 'can not tell')}" for row in traded) + ".")
    # Calls and puts take turns (1st call, 1st put, 2nd call ...): the chat shows the first few rows as cards, and both
    # sides belong in them. Each card is headed by its contract, "185 Call".
    def table_row(row: Mapping, new: bool = False) -> dict:
        change = changes.get(leap_oi_scanner.contract_key(row))
        return {"contract": f"{_strike(row['strike'])} {'Call' if row['side'] == 'CALL' else 'Put'}"
                            + (" · NEW" if new else "") + (" · ENTRY" if row.get("entry") else ""),
                "expiry": row["expiry"], "days": row["days"], "oi": _whole(row["oi"]),
                "change": _opened_closed(change),
                "today": _today_cell(flows.get(leap_oi_scanner.contract_key(row))),
                "delta": _delta_text(row["delta"]) if row.get("delta") is not None else "-",
                "bid": _quote_cell(row.get("bid")), "ask": _quote_cell(row.get("ask"))}

    # The biggest new open interest goes on top, then calls and puts take turns by open interest.
    rows = [table_row(row, new=True) for row in new_rows]
    shown = {leap_oi_scanner.contract_key(row) for row in new_rows}
    for rank in range(LONG_OI_ROWS):
        for key, _ in sides:
            if rank < len(picked[key]) and leap_oi_scanner.contract_key(picked[key][rank]) not in shown:
                rows.append(table_row(picked[key][rank]))
    if not rows:
        return _reply(lines, {"ticker": ticker, "monthsFrom": months_from, "monthsTo": months_to, "delta": delta, "rows": 0}, [link],
                      as_of=_as_of(fetched, now))
    table = _rows_table(f"{ticker} most open interest, {span}" + (f", delta ~{delta:.2f}" if delta is not None else ""),
                        [("contract", "Contract"), ("expiry", "Expiry"), ("days", "Days", "right"),
                         ("oi", "OI", "right"), *([("change", f"Opened or closed since {_month_day(since)}" if since else "Opened or closed since last reading", "right")] if changes else []),
                         *([("today", "Traded today", "right")] if flows else []),
                         ("delta", "Delta", "right"), ("bid", "Bid", "right"), ("ask", "Ask", "right")],
                        [{k: v for k, v in row.items() if (k != "change" or changes) and (k != "today" or flows)} for row in rows],
                        note=(f"NEW rows first: the biggest new open interest {since_text}. " if new_rows else "")
                             + f"Top {LONG_OI_ROWS} per side by open interest. "
                             + (f"Opened or closed: how many contracts people opened (+) or closed (-) {since_text}; '-' where the app has no earlier reading. " if changes else "")
                             + f"Schwab chain read {_clock(fetched, now)}"
                             + (", a copy under 30 minutes old." if doc.get("cached") else "."))
    facts = {"ticker": ticker, "monthsFrom": months_from, "monthsTo": months_to, "delta": delta, "spot": spot,
             "fetchedAt": doc.get("fetchedAt"), "expiries": expiries, "rows": len(rows),
             "top": {key: [{k: row[k] for k in ("strike", "expiry", "days", "oi", "delta")} for row in picked[key][:3]] for key, _ in sides},
             "newSince": since, "new": [{"side": row["side"], "strike": row["strike"], "expiry": row["expiry"], "oi": row["oi"],
                                         "change": changes[leap_oi_scanner.contract_key(row)]} for row in new_rows]}
    return _reply(lines, facts, [link], table, _as_of(fetched, now))


# ---------------------------------------------------------------- registry

def _guarded(name: str, handler: Callable[[Mapping, Mapping], dict]) -> Callable[[dict, dict], dict]:
    """Every tool here is open to any signed-in user, but the session's scope is
    still checked, and a bad file becomes a plain line instead of an exception."""

    def run(args: dict, ctx: dict) -> dict:
        args = args if isinstance(args, Mapping) else {}
        ctx = ctx if isinstance(ctx, Mapping) else {}
        if ctx.get("scope") not in ("owner", "scanner"):
            return _reply(["Please sign in to ask about the market."], {"refused": True})
        try:
            return handler(args, ctx)
        except Exception as exc:  # noqa: BLE001 - a question must never break the chat
            return _reply([f"I could not read the {name.replace('_', ' ')} data just now ({type(exc).__name__}). "
                           "Nothing was changed."], {"error": type(exc).__name__})

    run.__name__ = f"chat_{name}"
    return run


def _schema(**properties: dict) -> dict:
    return {"type": "object", "properties": properties, "additionalProperties": False}


_TICKER_ARG = {"type": "string", "description": "Ticker symbol, for example NVDA."}
# "free": these two take words like "friday" as well as dates; the handlers read them and never trust them.
_EXPIRY_ARG = {"type": "string", "format": "free", "maxLength": 20,
               "description": "Expiry: YYYY-MM-DD, M/D, today, tomorrow, or a weekday like friday."}
_DAY_ARG = {"type": "string", "format": "free", "maxLength": 20,
            "description": "Day: YYYY-MM-DD, today, yesterday, or a weekday like monday."}


def _entry(name: str, description: str, handler: Callable, **properties: dict) -> dict:
    return {"scope": "all", "description": description, "args": _schema(**properties), "handler": _guarded(name, handler)}


TOOLS: dict[str, dict] = {
    "chain": _entry("chain", "Saved option chain near the money for one ticker and expiry.", _chain_tool,
                    ticker=_TICKER_ARG, expiry=_EXPIRY_ARG,
                    strikes={"type": "integer", "minimum": 3, "maximum": MAX_TABLE_ROWS}),
    "walls": _entry("walls", "Call and put walls from the High OI board for one ticker.", _walls_tool,
                    ticker=_TICKER_ARG, side={"type": "string", "enum": ["bull", "bear"], "description": "bull for call walls, bear for put walls."},
                    n={"type": "integer", "minimum": 1, "maximum": 20}),
    "expected_move": _entry("expected_move", "Expected move for one ticker and expiry from the saved chain.",
                            _expected_move_tool, ticker=_TICKER_ARG, expiry=_EXPIRY_ARG),
    "price_outlook": _entry("price_outlook", "Where a price is going: any prediction, forecast, 'tomorrow', 'next week', up-or-down, "
                            "how-high or how-low question about a ticker or the market. Gives the options market's range and the "
                            "walls, never a forecast.", _price_outlook_tool, ticker=_TICKER_ARG, expiry=_EXPIRY_ARG),
    "max_pain": _entry("max_pain", "Max pain strike worked out from saved open interest.", _max_pain_tool,
                       ticker=_TICKER_ARG, expiry=_EXPIRY_ARG),
    "sectors": _entry("sectors", "Which sectors the scanner marked hot, what a sector holds, or a ticker's sector.",
                      _sectors_tool, sector={"type": "string"}, ticker=_TICKER_ARG),
    "premarket": _entry("premarket", "The premarket scanner's saved rows for a day, or one ticker on that day.",
                        _premarket_tool, day=_DAY_ARG, ticker=_TICKER_ARG),
    "news": _entry("news", "Saved headlines for one ticker. Facts only, no cause.", _news_tool,
                   ticker=_TICKER_ARG, limit={"type": "integer", "minimum": 1, "maximum": 10}),
    "earnings": _entry("earnings", "Next earnings date for a ticker, or upcoming earnings on the watchlist.",
                       _earnings_tool, ticker=_TICKER_ARG),
    "long_term": _entry("long_term", "Long Term fundamentals for one ticker. Facts only, no buy or sell call.",
                        _long_term_tool, ticker=_TICKER_ARG),
    "morning_brief": _entry("morning_brief", "Today's morning brief.", _morning_brief_tool),
    "quote": _entry("quote", "Latest held price for one ticker, with its age.", _quote_tool, ticker=_TICKER_ARG),
    "long_oi": _entry("long_oi", "Strikes with the most open interest on expiries 1 to 12 months out (LEAPs, long-dated options), "
                      "calls and puts, with each strike's delta. Optional: months_from/months_to (1-12), a delta to match, calls or puts.",
                      _long_oi_tool, ticker=_TICKER_ARG,
                      months_from={"type": "integer", "minimum": 1, "maximum": 12},
                      months_to={"type": "integer", "minimum": 1, "maximum": 12},
                      delta={"type": "number", "minimum": 0.05, "maximum": 0.95, "description": "e.g. 0.20 for strikes near 0.20 delta"},
                      side={"type": "string", "enum": ["bull", "bear"], "description": "bull for calls only, bear for puts only."}),
}
