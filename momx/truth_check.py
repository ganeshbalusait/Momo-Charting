"""BOT truth check (2026-11-04): what the virtual BOT claims vs what the real
Alpaca PAPER account got on the same signals.

His "BOT trading still im not happy the result not sure is correct". The BOT
(momx/option_track.py) scores every trade at the mid as if it filled. The paper
mirror (momx/paper_mirror.py) sends the same signals as real mid-price limit
orders, and many never fill (AAPL 11-04: the BOT's +232% was a $3.15 limit that
did not fill). This joins the two, keyed (side, rule, symbol, signal time), and
reports the fill rate and the BOT-vs-paper P/L on the trades that did fill.

Read-only: saved day files only, never the broker, never a write.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
ACCOUNTS = ("paper3", "paper5")
LIVE = ("buying", "open", "selling", "exit_due")
BIGGEST = 5


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _mid(bid, ask):
    if _num(bid) and _num(ask) and bid >= 0 and ask > 0 and ask >= bid:
        return round((bid + ask) / 2, 4)
    return None


def bot_entry_value(t: dict) -> float | None:
    """The BOT's entry: the recorded bid/ask mid, else its stored entry."""
    mid = _mid(t.get("entryBid"), t.get("entryAsk"))
    if mid is not None:
        return mid
    return float(t["entry"]) if _num(t.get("entry")) else None


def bot_exit_value(t: dict) -> float | None:
    """The BOT's exit. A $0 bid means nobody would buy: worth $0, not the mid."""
    bid = t.get("exitBid")
    if _num(bid) and bid == 0:
        return 0.0
    mid = _mid(bid, t.get("exitAsk"))
    if mid is not None:
        return mid
    return float(t["exitPrice"]) if _num(t.get("exitPrice")) else None


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else None


def _bot_pnl(t, qty):
    if not t or t.get("badQuote"):
        return None
    entry, exit_ = bot_entry_value(t), bot_exit_value(t)
    if entry is None or entry <= 0 or exit_ is None:
        return None
    return (exit_ - entry) * 100 * qty, entry * 100 * qty, (exit_ - entry) / entry * 100


def _stats(rows: list[dict], bot: dict[tuple, dict]) -> dict:
    a = {"orders": 0, "filled": 0, "missed": 0, "open": 0, "unmatched": 0}
    bot_pnl = bot_cost = paper_pnl = paper_cost = missed_pnl = 0.0
    matched = 0
    biggest = []
    rules = defaultdict(lambda: {"orders": 0, "missed": 0, "filled": 0, "bp": 0.0, "bc": 0.0, "pp": 0.0, "pc": 0.0})
    for r in rows:
        status = r.get("status")
        qty = r.get("qty") if _num(r.get("qty")) and r.get("qty") > 0 else 1
        side = r.get("side") or "bull"
        t = bot.get((side, r.get("rule"), r.get("symbol"), r.get("at")))
        scored = _bot_pnl(t, qty)
        rule = rules[(side, r.get("rule"))]
        a["orders"] += 1
        rule["orders"] += 1
        filled = _num(r.get("buyFill"))
        if status == "missed":
            a["missed"] += 1
            rule["missed"] += 1
        elif filled:
            a["filled"] += 1
            rule["filled"] += 1
        if status in LIVE:
            a["open"] += 1
        if scored is None:
            a["unmatched"] += 1
            continue
        pnl, cost, pct = scored
        if status == "missed":
            missed_pnl += pnl
            biggest.append({"day": str(r.get("at", ""))[:10], "symbol": r.get("symbol"), "side": side,
                            "rule": r.get("rule"), "botEntry": round(cost / 100 / qty, 4),
                            "limit": r.get("buyLimit"), "botPct": round(pct, 1)})
        elif status == "closed" and filled and _num(r.get("pl")):
            matched += 1
            fill_cost = r["buyFill"] * 100 * qty
            bot_pnl += pnl
            bot_cost += cost
            paper_pnl += r["pl"]
            paper_cost += fill_cost
            rule["bp"] += pnl
            rule["bc"] += cost
            rule["pp"] += r["pl"]
            rule["pc"] += fill_cost
    a["fillRatePct"] = _pct(a["filled"], a["filled"] + a["missed"])
    a["matched"] = {"n": matched, "botPnl": round(bot_pnl, 2), "botRoi": _pct(bot_pnl, bot_cost),
                    "paperPnl": round(paper_pnl, 2), "paperRoi": _pct(paper_pnl, paper_cost)}
    a["missedBotPnl"] = round(missed_pnl, 2)
    a["biggestMissed"] = sorted(biggest, key=lambda b: -b["botPct"])[:BIGGEST]
    a["byRule"] = sorted(
        ({"side": s, "rule": ru, "orders": v["orders"], "missed": v["missed"],
          "fillRatePct": _pct(v["filled"], v["filled"] + v["missed"]),
          "botRoi": _pct(v["bp"], v["bc"]), "paperRoi": _pct(v["pp"], v["pc"])}
         for (s, ru), v in rules.items()),
        key=lambda x: (-x["orders"], x["side"], str(x["rule"])))
    return a


def compare(paper: dict[str, list[dict]], bot: dict[tuple, dict]) -> dict:
    accounts = {}
    for account, rows in paper.items():
        a = _stats(rows, bot)
        # Order rules v2 (2026-11-05): live-quote mid, then re-sent at the ask so
        # trades are not missed. Shown apart so the change can be judged on its own.
        newer = [r for r in rows if isinstance(r.get("exec"), int) and r["exec"] >= 2]
        if newer:
            a["newRules"] = _stats(newer, bot)
        accounts[account] = a
    return {"accounts": accounts}


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _bot_day(artifacts: Path, side: str, day: str) -> dict:
    root = artifacts / "bear" if side == "bear" else artifacts
    doc = _read(root / "momx_option_track" / f"{day}.json")
    trades = doc.get("trades") if isinstance(doc, dict) else None
    out = {}
    for t in trades if isinstance(trades, list) else []:
        if isinstance(t, dict) and t.get("rule") and t.get("symbol"):
            out[(side, t["rule"], t["symbol"], t.get("at"))] = t
    return out


def build(artifacts: Path, days: int = 30, today: str | None = None) -> dict:
    artifacts = Path(artifacts)
    end = date.fromisoformat(today) if today else datetime.now(ET).date()
    start = (end - timedelta(days=max(1, days) - 1)).isoformat()
    end_s = end.isoformat()
    paper, needed = {}, set()
    for account in ACCOUNTS:
        folder = artifacts / "momx_paper_mirror" / account
        if not folder.is_dir():
            continue
        rows, seen = [], set()
        # Newest file first: a carried order's latest status wins.
        for path in sorted(folder.glob("20*.json"), reverse=True):
            doc = _read(path)
            found = doc.get("rows") if isinstance(doc, dict) else None
            for key, r in (found.items() if isinstance(found, dict) else ()):
                if key in seen or not isinstance(r, dict):
                    continue
                seen.add(key)
                day = str(r.get("at", ""))[:10]
                if start <= day <= end_s:
                    rows.append(r)
                    needed.add((r.get("side") or "bull", day))
        paper[account] = rows
    lookup = {}
    for side, day in needed:
        lookup.update(_bot_day(artifacts, side, day))
    report = compare(paper, lookup)
    days_seen = sorted(str(r.get("at", ""))[:10] for rows in paper.values() for r in rows)
    report.update(available=bool(days_seen), days=days, **{"from": days_seen[0] if days_seen else None},
                  through=days_seen[-1] if days_seen else None,
                  basis="BOT = virtual mid-price trades; paper = real Alpaca paper limit orders at the mid. "
                        "P/L compared only on orders that filled and closed. A $0 bid exit counts as $0.")
    return report
