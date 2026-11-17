"""Ticker trader journal and AI review (2026-11-13).

His words (2026-11-12): "Ui looks good, but need AI model to improve also tradezella journal also so I know
how I see improve". A trading-journal layout (the pattern only: no other product's name, art or colours) on
the Ticker trader's SIMULATED trades (momx/ticker_trader.py), per run and for all runs together.

Reused from the Scorecard (momx/bot_desk.py): the trade numbers (_trade_stats: win rate, average win and loss,
profit factor, expectancy, best and worst), the day-by-day P&L with its running total (_days) and the week key
(_week_of). The screen reuses the Scorecard's tiles, daily bars and month calendar (frontend/src/botDesk.js).

Added here:
- an equity curve from the account's starting balance, trade by trade;
- breakdowns by Call / Put, time of day bought, which target was reached and hold time (and ticker, for all);
- "What to improve": numbers only, no AI, each with the trades it rests on and "Too few trades to judge"
  under MIN_JUDGE;
- a notes box per trade (plain text, NOTE_MAX characters, saved on the server; the routes are owner only);
- the AI review: one short paragraph per run (and one for all runs) on what the journal's own numbers show,
  labelled REVIEW_LABEL. The owner summary's guarded path (momx/chat_summary.py): chat_ai._bounded with
  rest=False under the owner's share of the day's AI allowance, then chat_summary.guarded() (every number in the
  facts with its sign and %, no link, no e-mail address, no ticker the facts do not name, the advice filter as a
  backstop). The AI is asked AT MOST ONCE per run per ET day, success or not: the attempt is recorded before
  the answer is used. Nothing calls it on a timer; the screen asks when he opens the journal and the cached
  paragraph answers every later ask that day. AI off, resting or over the day's allowance: no review, no
  error, and no attempt recorded (the AI was not asked). The AI never changes a rule or a trade.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from momx import bot_desk

ET = ZoneInfo("America/New_York")
ALL = "all"
NOTES_FILE = "notes.json"
REVIEWS_FILE = "reviews.json"
#: The longest note one trade keeps, in characters.
NOTE_MAX = 2000
#: Fewer trades than this behind a "What to improve" line: it says so instead of judging.
MIN_JUDGE = 10
TOO_FEW = "Too few trades to judge"
#: A stop this soon after the buy counts as "hit within 5 minutes of entry".
FAST_STOP_SECONDS = 5 * 60
REVIEW_LABEL = "AI review, can be wrong. Only you see this."
#: The AI gets this long for the review (the owner summary's own budget).
REVIEW_SECONDS = 8.0
MAX_FACTS = 60

#: Bought between these ET minutes (from 9:30). The last one holds anything later.
TIME_BUCKETS = (("open", "9:30-10:00", 9 * 60 + 30, 10 * 60), ("morning", "10:00-11:00", 10 * 60, 11 * 60),
                ("midday", "11:00-13:00", 11 * 60, 13 * 60), ("afternoon", "13:00-15:00", 13 * 60, 15 * 60),
                ("late", "15:00-16:00", 15 * 60, 24 * 60))
HOLD_BUCKETS = (("under5", "Under 5 min", 0, 5), ("to15", "5-15 min", 5, 15), ("to60", "15-60 min", 15, 60),
                ("to180", "1-3 hours", 60, 180), ("over180", "Over 3 hours", 180, None))
#: Which target the trade reached before it closed. "stopped" = the first stop, before T1.
OUTCOMES = (("stopped", "Stopped before T1"), ("T1", "Reached T1"), ("T2", "Reached T2"), ("T3", "Reached T3"),
            ("early", "Closed before T1, no stop"))
SIDES = (("CALL", "Calls"), ("PUT", "Puts"))
_STOP_TEXT = ("Stop:", "Break-even stop:", "Stop at T1:")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩﻿]")


# ---------------------------------------------------------------- small helpers
def _num(value: Any) -> float | None:
    return float(value) if bot_desk._num(value) else None


def _when(stamp: Any) -> datetime | None:
    try:
        at = datetime.fromisoformat(str(stamp or ""))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=ET)).astimezone(ET)


def money(value: float | None) -> str:
    """"+$1,234.50" / "-$80.00" / "$0.00": the sign written, so the number check holds it."""
    if value is None:
        return "n/a"
    v = round(float(value), 2)
    return ("+" if v > 0 else "-" if v < 0 else "") + f"${abs(v):,.2f}"


def _pct(part: float, whole: float) -> float | None:
    return round(part / whole * 100, 1) if whole else None


def read_json(path: Path, default: Any) -> Any:
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default
    return doc if isinstance(doc, type(default)) else default


def write_json(path: Path, doc: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8")
    os.replace(tmp, path)


# ---------------------------------------------------------------- notes
def clean_note(text: Any) -> str:
    """His note as plain text: control and direction characters out, line ends kept, spaces trimmed.
    Raises ValueError (in plain words) for anything that is not text or is too long."""
    if text is None:
        return ""
    if not isinstance(text, str):
        raise ValueError("A note is plain text.")
    out = _CONTROL.sub("", text.replace("\r\n", "\n").replace("\r", "\n")).strip()
    if len(out) > NOTE_MAX:
        raise ValueError(f"A note can be at most {NOTE_MAX:,} characters.")
    return out


# ---------------------------------------------------------------- one trade
def _minute(at: datetime | None) -> int | None:
    return at.hour * 60 + at.minute if at else None


def _bucket(value: float | None, buckets: tuple) -> str | None:
    if value is None:
        return None
    for key, _label, low, high in buckets:
        if value >= low and (high is None or value < high):
            return key
    return None


def outcome(trade: Mapping) -> str:
    hits = [h for h in trade.get("hits") or [] if isinstance(h, Mapping)]
    if hits:
        return str(hits[-1].get("target") or "T1")
    events = [str(e.get("text") or "") for e in trade.get("events") or [] if isinstance(e, Mapping)]
    return "stopped" if any(t.startswith("Stop:") for t in events) else "early"


def _fast_stop(trade: Mapping, bought: datetime | None) -> bool:
    """A stop (any of the three) closed this trade within FAST_STOP_SECONDS of the buy."""
    if not bought:
        return False
    for event in trade.get("events") or []:
        if isinstance(event, Mapping) and str(event.get("text") or "").startswith(_STOP_TEXT):
            at = _when(event.get("at"))
            if at and 0 <= (at - bought).total_seconds() <= FAST_STOP_SECONDS:
                return True
    return False


def trade_card(trade: Mapping, run: Mapping) -> dict | None:
    """One closed trade as the journal shows it, with what it was bucketed under. None = not a usable closed trade."""
    entry, pl, qty = _num(trade.get("entry")), _num(trade.get("pl")), _num(trade.get("qty"))
    if trade.get("status") != "closed" or entry is None or pl is None or not qty:
        return None
    bought, closed = _when(trade.get("boughtAt")), _when(trade.get("closedAt"))
    hold = round((closed - bought).total_seconds() / 60, 1) if bought and closed and closed >= bought else None
    keep = ("id", "day", "ticker", "side", "contract", "strike", "expiry", "qty", "entry", "entrySpot", "cost", "boughtAt",
            "closedAt", "stopStart", "stopStock", "stopKind", "t1", "t2", "t3", "targets", "hits", "sells", "events", "reason",
            "pl", "plPct", "balanceAfter")
    card = {k: trade.get(k) for k in keep}
    card.update(runId=run.get("id"), side=str(trade.get("side") or run.get("side") or "CALL").upper(),
                ticker=trade.get("ticker") or run.get("ticker"), outcome=outcome(trade),
                timeBucket=_bucket(_minute(bought), TIME_BUCKETS) or TIME_BUCKETS[0][0],
                holdMinutes=hold, holdBucket=_bucket(hold, HOLD_BUCKETS), fastStop=_fast_stop(trade, bought),
                reachedT1=any(isinstance(h, Mapping) and h.get("target") == "T1" for h in trade.get("hits") or []))
    return card


def _desk_row(card: Mapping) -> dict:
    """A trade in the shape the Scorecard's own maths reads (momx/bot_desk.py)."""
    return {"buyFill": float(card["entry"]), "pl": float(card["pl"]), "qty": float(card["qty"]), "status": "closed",
            "closedAt": card.get("closedAt"), "at": card.get("boughtAt"), "symbol": card.get("ticker"),
            "side": "bear" if card.get("side") == "PUT" else "bull"}


# ---------------------------------------------------------------- the journal
def _group(cards: list[dict], field: str, order: tuple) -> list[dict]:
    """Trades, wins, win rate and net for each bucket, in the given order; empty buckets are kept (0 trades)."""
    out = []
    for key, label, *_ in order:
        mine = [c for c in cards if c.get(field) == key]
        pls = [float(c["pl"]) for c in mine]
        wins = sum(1 for p in pls if p > 0)
        out.append({"key": key, "label": label, "trades": len(mine), "wins": wins, "losses": sum(1 for p in pls if p < 0),
                    "winRatePct": _pct(wins, len(mine)), "pnl": round(sum(pls), 2),
                    "avg": round(sum(pls) / len(pls), 2) if pls else None})
    return out


def _periods(cards: list[dict]) -> dict:
    buckets: dict = {"day": defaultdict(list), "week": defaultdict(list), "month": defaultdict(list)}
    for card in cards:
        day = str(card.get("closedAt") or card.get("boughtAt") or "")[:10]
        try:
            week = bot_desk._week_of(day)
        except ValueError:
            continue
        buckets["day"][day].append(card)
        buckets["week"][week].append(card)
        buckets["month"][day[:7]].append(card)

    def period(key: str, mine: list[dict]) -> dict:
        s = bot_desk._trade_stats([_desk_row(c) for c in mine])
        return {"key": key, "trades": s["n"], "wins": s["wins"], "losses": s["losses"], "winRatePct": s["winRatePct"],
                "pnl": s["netPnl"], "calls": round(sum(c["pl"] for c in mine if c["side"] != "PUT"), 2),
                "puts": round(sum(c["pl"] for c in mine if c["side"] == "PUT"), 2)}

    return {name: [period(key, mine) for key, mine in sorted(group.items(), reverse=True)] for name, group in buckets.items()}


def _share(part: int, whole: int) -> str:
    pct = _pct(part, whole)
    return f"{part} of {whole} trades ({pct:g}%)" if pct is not None else f"{part} of {whole} trades"


def improve(cards: list[dict], multi: bool) -> list[dict]:
    """"What to improve" from the numbers alone (no AI). Each line carries the trades it rests on; under
    MIN_JUDGE it says "Too few trades to judge" instead of passing judgement."""
    out: list[dict] = []
    n = len(cards)
    if not n:
        return out
    losers = [c for c in cards if c["pl"] < 0]

    def line(key: str, title: str, text: str, sample: int) -> None:
        enough = sample >= MIN_JUDGE
        out.append({"key": key, "title": title, "text": text, "trades": sample, "enough": enough,
                    "judge": "" if enough else TOO_FEW})

    def most_losses(key: str, title: str, field: str, order: tuple, word: Callable[[str], str]) -> None:
        if not losers:
            line(key, title, "No losing trades yet.", 0)
            return
        rows = [r for r in _group(losers, field, order) if r["trades"]]
        top = max(rows, key=lambda r: (r["trades"], -r["pnl"]))
        line(key, title, f"Most losing trades were {word(top['label'])}: {top['trades']} of {len(losers)} losing trades, "
                         f"net {money(top['pnl'])}.", len(losers))

    most_losses("lossTime", "Losses by time of day", "timeBucket", TIME_BUCKETS, lambda label: f"bought {label} ET")
    most_losses("lossSide", "Losses by side", "side", SIDES, lambda label: label)
    if multi:
        tickers = tuple((t, t) for t in sorted({c["ticker"] for c in cards}))
        most_losses("lossTicker", "Losses by ticker", "ticker", tickers, lambda label: f"on {label}")
    if n:
        line("t1First", "T1 before the stop", f"T1 was reached before the stop on {_share(sum(1 for c in cards if c['reachedT1']), n)}.", n)
        line("fastStop", "Stopped within 5 minutes",
             f"The stop was hit within 5 minutes of the buy on {_share(sum(1 for c in cards if c['fastStop']), n)}.", n)
        sides = {r["key"]: r for r in _group(cards, "side", SIDES)}
        c, p = sides["CALL"], sides["PUT"]
        line("callsPuts", "Calls against puts",
             f"Calls: {c['trades']} trades, {c['wins']} won, net {money(c['pnl'])}. "
             f"Puts: {p['trades']} trades, {p['wins']} won, net {money(p['pnl'])}.", n)
    return out


def scope_runs(runs: list[Mapping], scope: Any) -> tuple[str, list[Mapping]]:
    """("all", every run) or (run id, [that run]). KeyError for a run that is not there."""
    key = str(scope or ALL).strip() or ALL
    if key.lower() == ALL:
        return ALL, list(runs)
    chosen = [r for r in runs if str(r.get("id")) == key]
    if not chosen:
        raise KeyError(key)
    return key, chosen


def scope_label(key: str, runs: list[Mapping]) -> str:
    if key == ALL:
        return "All tickers"
    run = runs[0]
    return f"{run.get('ticker')} {'Put' if str(run.get('side')).upper() == 'PUT' else 'Call'}"


def build(runs: list[Mapping], scope: Any = ALL, notes: Mapping | None = None, reviews: Mapping | None = None,
          now: datetime | None = None, account_start: float | None = None) -> dict:
    """The journal for one run or all runs. Raises KeyError for an unknown run."""
    now = now or datetime.now(ET)
    key, chosen = scope_runs(runs, scope)
    notes = notes if isinstance(notes, Mapping) else {}
    cards = []
    for run in chosen:
        for trade in run.get("trades") or []:
            card = trade_card(trade, run) if isinstance(trade, Mapping) else None
            if card:
                note = notes.get(str(card.get("id")))
                card["note"] = note.get("text", "") if isinstance(note, Mapping) else ""
                card["noteAt"] = note.get("at") if isinstance(note, Mapping) else None
                cards.append(card)
    cards.sort(key=lambda c: str(c.get("closedAt") or ""))
    rows = [_desk_row(c) for c in cards]
    stats = bot_desk._trade_stats(rows)
    days, summary = bot_desk._days(rows, rows)
    summary.pop("dailyLimitBreaks", None)
    # One account for every ticker (2026-11-16): `account_start` is its starting size, and "now" is that start plus
    # the chosen trades' profit and loss. Without it (an older caller), each run's own figures are added up.
    shared = _num(account_start)
    start = round(shared if shared is not None else sum(_num(r.get("startBalance")) or 0.0 for r in chosen), 2)
    balance, equity = start, []
    for c in cards:
        balance = round(balance + float(c["pl"]), 2)
        equity.append({"id": c.get("id"), "at": c.get("closedAt"), "pl": round(float(c["pl"]), 2), "balance": balance})
    best = max(cards, key=lambda c: c["pl"], default=None)
    worst = min(cards, key=lambda c: c["pl"], default=None)
    tickers = sorted({str(c["ticker"]) for c in cards})
    breakdowns = {"side": _group(cards, "side", SIDES), "time": _group(cards, "timeBucket", TIME_BUCKETS),
                  "outcome": _group(cards, "outcome", OUTCOMES), "hold": _group(cards, "holdBucket", HOLD_BUCKETS)}
    if key == ALL:
        breakdowns["ticker"] = _group(cards, "ticker", tuple((t, t) for t in tickers))
    day = now.date().isoformat()
    held = reviews.get(key) if isinstance(reviews, Mapping) else None
    return {
        "scope": key, "label": scope_label(key, chosen), "simulated": True, "asOf": now.replace(microsecond=0).isoformat(),
        "runs": [{"id": r.get("id"), "ticker": r.get("ticker"), "side": r.get("side")} for r in runs],
        "startBalance": start, "balance": round(start + sum(float(c["pl"]) for c in cards), 2) if shared is not None
        else round(sum(_num(r.get("balance")) or 0.0 for r in chosen), 2),
        "openTrades": sum(1 for r in chosen if r.get("open")),
        "stats": stats, "daySummary": summary, "days": days, "journal": {"periods": _periods(cards)},
        "equity": equity, "bestId": best.get("id") if best else None, "worstId": worst.get("id") if worst else None,
        "breakdowns": breakdowns, "improve": improve(cards, len(tickers) > 1), "minJudge": MIN_JUDGE,
        "trades": list(reversed(cards)), "noteMax": NOTE_MAX, "thisWeek": bot_desk._week_of(day),
        "review": public_review(held), "reviewStatus": (held or {}).get("status") if isinstance(held, Mapping) else None,
        "reviewLabel": REVIEW_LABEL,
    }


# ---------------------------------------------------------------- the AI review
REVIEW_SYSTEM = (
    "You write one short factual review of a trader's own SIMULATED trades, from his trading journal's numbers. Use only "
    "FACTS. Write 2 to 4 plain sentences, at most 600 characters, on what the numbers show: where the wins and the losses "
    "came from (time of day, Call or Put, which target was reached, how long trades were held) and how this week compares "
    "with the whole record. Copy every number exactly as the facts write it, with its sign and %. Write every number in "
    "digits, never in words. Name no stock or company that the facts do not name. No links, no e-mail addresses. Give no "
    "advice and no forecast: never say to buy, sell, hold, enter, exit or wait, never tell him what to do, and never say "
    "what a price will do. Reply with JSON only: {\"summary\": \"...\"}.")


def review_facts(doc: Mapping) -> list[str]:
    """The journal's own numbers as fact lines: the only thing the AI sees."""
    s = doc.get("stats") or {}
    lines = [f"Journal: {doc.get('label')}, simulated trades.",
             f"Closed trades: {s.get('n', 0)}. Won: {s.get('wins', 0)}. Lost: {s.get('losses', 0)}. "
             f"Win rate: {s.get('winRatePct') if s.get('winRatePct') is not None else 'n/a'}%.",
             f"Net: {money(s.get('netPnl'))}. Average win: {money(s.get('avgWin'))}. Average loss: {money(s.get('avgLoss'))}.",
             f"Profit factor: {s.get('profitFactor') if s.get('profitFactor') is not None else 'n/a'}. "
             f"Expectancy per trade: {money(s.get('expectancy'))}.",
             f"Best trade: {money(s.get('bestTrade'))}. Worst trade: {money(s.get('worstTrade'))}.",
             f"Account: started at {money(doc.get('startBalance'))}, now {money(doc.get('balance'))}."]
    week = next((p for p in (doc.get("journal") or {}).get("periods", {}).get("week") or [] if p.get("key") == doc.get("thisWeek")), None)
    if week:
        lines.append(f"This week (week of {week['key']}): {week['trades']} trades, {week['wins']} won, net {money(week['pnl'])}.")
    else:
        lines.append(f"This week (week of {doc.get('thisWeek')}): no closed trades.")
    names = {"side": "Side", "time": "Bought", "outcome": "Outcome", "hold": "Held", "ticker": "Ticker"}
    for group, rows in (doc.get("breakdowns") or {}).items():
        for r in rows:
            if r.get("trades"):
                lines.append(f"{names.get(group, group)} {r['label']}: {r['trades']} trades, {r['wins']} won, net {money(r['pnl'])}.")
    for item in doc.get("improve") or []:
        lines.append(f"{item['title']}: {item['text']}" + (f" ({item['judge']}: {item['trades']} trades.)" if item.get("judge") else ""))
    return lines[:MAX_FACTS]


def public_review(entry: Any) -> dict | None:
    if not isinstance(entry, Mapping) or entry.get("status") != "done" or not isinstance(entry.get("text"), str):
        return None
    return {"text": entry["text"], "label": REVIEW_LABEL, "asOf": entry.get("asOf"), "trades": entry.get("trades")}


def skip_reason(doc: Mapping, real: bool) -> str:
    """Why the AI is not asked at all ("" = ask it). Nothing here is recorded as an attempt."""
    from momx import chat_ai
    if not (doc.get("stats") or {}).get("n"):
        return "No closed trades yet."
    if not chat_ai.enabled():
        return "The AI is off."
    if real:
        if chat_ai.resting("owner"):
            return "The AI is not answering right now."
        from momx import chat_limits
        if not chat_limits.LIMITS.ai_call_allowed("owner"):
            return "Today's AI allowance is used up."
    return ""


def write_review(doc: Mapping, ask: Callable | None, known: frozenset, now: datetime) -> dict:
    """Ask the AI ONCE and keep its paragraph only if every guard passes. Returns the entry to record for today."""
    from momx import chat_ai, chat_summary
    facts = review_facts(doc)
    user = json.dumps({"journal": doc.get("label"), "FACTS": facts}, separators=(",", ":"))
    out = chat_ai._bounded(ask or chat_ai._default_ask, REVIEW_SYSTEM, user, chat_summary.SCHEMA, REVIEW_SECONDS,
                           "owner", rest=False)
    data = out.get("data") if isinstance(out, Mapping) and out.get("ok") else None
    ticker = "" if doc.get("scope") == ALL else str((doc.get("label") or "").split(" ")[0])
    text = chat_summary.guarded(data.get("summary"), facts, [], ticker, known) if isinstance(data, Mapping) else None
    stamp = now.replace(microsecond=0).isoformat()
    n = (doc.get("stats") or {}).get("n", 0)
    if not text:
        return {"status": "tried", "at": stamp, "trades": n}
    clock = f"{now.hour % 12 or 12}:{now.minute:02d} {'AM' if now.hour < 12 else 'PM'} ET"
    return {"status": "done", "at": stamp, "trades": n, "text": text,
            "asOf": f"written {clock} from {n} closed {'trade' if n == 1 else 'trades'}"}
