"""Ticker story (2026-11-20): one answer per ticker, in words a 10-year-old or a first-time trader can follow.

Ganesh: "If I see leap oi scanner like INTC I don't know anything like buyers or seller ... then I have to search agx
flow ... I feel little confusion. How to avoid that and fix it", "Build like this 10 kid can understand", "Which is
better do it". Design: docs/superpowers/specs/2026-11-20-ticker-story-card-design.md.

It joins what the LEAP OI Scanner (momx/leap_oi_scanner.py) and AGX Flow (momx/agx_flow.py) already hold for one
ticker: today's money on Calls vs Puts and whether it looked bought or sold, the biggest bets today, the long bets most
people hold, and what is new since yesterday. It never asks Schwab itself: a ticker neither page has read is queued for
their background readers (as each page does) and the answer says it is still reading.

Facts only: every sentence is a number the app already has. Never a pick, never "it will go up". Read-only.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NEW_MIN = 1000                 # a long bet counts as "new" or "closed" at 1,000 contracts or more since yesterday
TOP_BETS = 3
NOT_ADVICE = ("This explains what the numbers show. It is not advice and does not tell you what to do with your money. "
              "Nobody knows which way a stock will go. Practice with paper trading first.")
MEANING = ("A Call is a bet that the price goes UP; a Put is a bet that it goes DOWN. But a Put can also be insurance "
           "for shares someone already owns, and a big bet can be one half of a bigger trade, so lots of money on one "
           "side does not prove where the price will go. Our test of 15,896 cases found big open interest guessed the "
           "direction no better than a coin flip.")


def _whole(value: Any) -> str:
    try:
        return f"{round(float(value)):,}"
    except (TypeError, ValueError):
        return "0"


def money(value: Any) -> str:
    n = float(value or 0)
    if n >= 1_000_000_000:
        return f"${n / 1_000_000_000:.1f} billion"
    if n >= 1_000_000:
        return f"${n / 1_000_000:.0f} million" if n >= 10_000_000 else f"${n / 1_000_000:.1f} million"
    return f"${round(n):,}"


def _day(iso: Any) -> str:
    try:
        d = datetime.fromisoformat(str(iso)[:10])
    except ValueError:
        return str(iso or "")
    return f"{d.strftime('%b')} {d.day}, {d.year}"


def _contract(row: dict) -> str:
    strike = float(row.get("strike") or 0)
    return f"{strike:g} {'Put' if row.get('side') == 'PUT' else 'Call'} ending {_day(row.get('expiry'))}"


def _fill_words(fill: Any) -> str:
    return {"ask": "looked like a buyer (paid the asking price)",
            "bid": "looked like a seller (took the bid price)"}.get(str(fill), "can't tell if a buyer or a seller led")


def _line(mark: str, text: str) -> dict:
    return {"mark": mark, "text": text}


def build(ticker: str, leap: dict | None, flow: dict | None) -> dict:
    """The story from the two boards' answers for ONE ticker (pure; story() fetches them)."""
    ticker = str(ticker or "").upper()
    leap_t = next((t for t in (leap or {}).get("tickers") or [] if t.get("ticker") == ticker), None)
    flow_t = next((t for t in (flow or {}).get("tickers") or [] if t.get("ticker") == ticker), None)
    excluded = {x.get("ticker"): x for x in [*((leap or {}).get("excluded") or []), *((flow or {}).get("excluded") or [])]}
    if ticker in excluded and not leap_t and not flow_t:
        why = excluded[ticker].get("reason") or "it is an ETF or a stock under $10"
        return {"ticker": ticker, "ready": False, "pending": False, "spot": None, "sections": [], "meaning": MEANING,
                "notAdvice": NOT_ADVICE, "verdict": f"{ticker} is not on these boards: {why}."}
    leap_ready = bool(leap_t and leap_t.get("state") == "ready")
    flow_ready = bool(flow_t and flow_t.get("fetchedAt"))
    pending = bool((leap_t and leap_t.get("state") == "pending") or (flow_t and flow_t.get("pending")))
    spot = (leap_t or {}).get("spot") or (flow_t or {}).get("spot")
    sections: list[dict] = []

    if spot:
        sections.append({"title": "Price", "lines": [_line("info", f"One share of {ticker} costs about {float(spot):.2f} (at the last read).")]})

    calls = float((flow_t or {}).get("callPremium") or 0)
    puts = float((flow_t or {}).get("putPremium") or 0)
    verdict = None
    if flow_ready:
        lines = []
        total = calls + puts
        if total > 0:
            lines.append(_line("info", f"Today about {money(calls)} went into Calls (bets the price goes up) and {money(puts)} into Puts (bets it goes down)."))
            share = max(calls, puts) / total
            if 0.45 <= calls / total <= 0.55:
                verdict = f"Today the money on {ticker} was split about evenly between Calls and Puts."
                lines.append(_line("info", "That is about even: neither side clearly led."))
            else:
                side = "Calls" if calls > puts else "Puts"
                verdict = f"Today more money went into {side} on {ticker}: {round(share * 100)}% of it."
                lines.append(_line("yes", f"{side} got {round(share * 100)}% of the money."))
            bets = [r for r in (flow or {}).get("rows") or [] if r.get("ticker") == ticker][:TOP_BETS]
            for bet in bets:
                lines.append(_line("info", f"Big bet: {_contract(bet)}, {money(bet.get('premium'))}; the last trade {_fill_words(bet.get('fill'))}."))
            unusual = int((flow_t or {}).get("unusualCount") or 0)
            if unusual:
                lines.append(_line("yes", f"{unusual} of its contracts traded more today than were open before. That is unusual."))
        else:
            lines.append(_line("info", "No option trades in the app's latest read. Trades show here during market hours."))
        part = {"title": "Today's bets", "lines": lines}
        # "Which strike is that?" (2026-11-23): the contracts that took the most money on each side, as a table.
        leap_line = next((t for t in (leap or {}).get("byTicker") or [] if t.get("ticker") == ticker), None)
        bets = (leap_line or {}).get("todayBets") or {}
        if total > 0 and (bets.get("topCalls") or bets.get("topPuts")):
            part["table"] = {"calls": bets.get("topCalls") or [], "puts": bets.get("topPuts") or []}
        sections.append(part)

    rows = [r for r in (leap or {}).get("rows") or [] if r.get("ticker") == ticker]
    if leap_ready:
        lines = []
        call_oi, put_oi = int((leap_t or {}).get("callOi") or 0), int((leap_t or {}).get("putOi") or 0)
        if call_oi + put_oi > 0:
            more = "Calls" if call_oi >= put_oi else "Puts"
            lines.append(_line("info", f"On long bets (3 to 12 months away) people hold {_whole(call_oi)} Call contracts and {_whole(put_oi)} Put contracts on the biggest strikes: more {more}."))
            if verdict is None:
                verdict = f"On long bets, more people hold {more} on {ticker}."
        for key, word in (("CALL", "Call"), ("PUT", "Put")):
            top = max((r for r in rows if r.get("side") == key), key=lambda r: int(r.get("oi") or 0), default=None)
            if top:
                lines.append(_line("info", f"The long {word} most people hold: {_contract(top)}, {_whole(top.get('oi'))} contracts."))
        if not lines:
            lines.append(_line("info", "No long bets with enough open contracts were found."))
        sections.append({"title": "Long bets (3 to 12 months)", "lines": lines})

        changed = [r for r in rows if r.get("oiChange") is not None]
        if changed:
            lines = []
            opened = sorted((r for r in changed if int(r["oiChange"]) >= NEW_MIN), key=lambda r: -int(r["oiChange"]))[:TOP_BETS]
            closed = sorted((r for r in changed if int(r["oiChange"]) <= -NEW_MIN), key=lambda r: int(r["oiChange"]))[:TOP_BETS]
            for r in opened:
                lines.append(_line("yes", f"{_whole(r['oiChange'])} new contracts opened on the {_contract(r)}."))
            for r in closed:
                lines.append(_line("no", f"{_whole(-int(r['oiChange']))} contracts closed on the {_contract(r)}."))
            if not lines:
                lines.append(_line("info", f"No long bet opened or closed {NEW_MIN:,} or more contracts since yesterday."))
            sections.append({"title": "New since yesterday", "lines": lines})

    if verdict is None:
        verdict = (f"Reading {ticker}... the card fills in when the option pages have read it." if pending or not (leap_ready or flow_ready)
                   else f"Not much option activity on {ticker} yet.")
    return {"ticker": ticker, "ready": leap_ready or flow_ready, "pending": pending, "spot": spot, "verdict": verdict,
            "sections": sections, "meaning": MEANING, "notAdvice": NOT_ADVICE,
            "readAt": {"leap": (leap_t or {}).get("readAt"), "flow": (flow_t or {}).get("fetchedAt")}}


def story(ticker: str, root: Path, now: datetime | None = None) -> dict:
    """One ticker's story from what both pages hold (queues a read when neither has it). Raises ValueError for junk."""
    from momx import agx_flow, leap_oi_scanner
    symbols = leap_oi_scanner.clean_symbols(str(ticker or ""))
    if len(symbols) != 1:
        raise ValueError("Give one ticker, like INTC.")
    t = symbols[0]
    now = now or datetime.now(timezone.utc).astimezone(agx_flow.ET)
    leap = leap_oi_scanner.board([t], Path(root), now, months_from=3, months_to=12)
    try:
        flow = agx_flow.board([t], Path(root), now)
    except ValueError:
        flow = {"tickers": [], "rows": [], "excluded": [{"ticker": t, "reason": "it is an ETF"}]}
    return build(t, leap, flow)
