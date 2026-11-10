"""BOT desk scorecard (2026-11-09): the paper accounts judged like a prop-firm
evaluation, on REAL Alpaca paper fills only.

His "i dont trust to turn into real money ... see how prop fund trading bot
will be". A prop firm does not ask what a trader's signals might have made; it
gives an account, a profit target, a daily loss limit and a drawdown limit, and
reads the real record. This does the same for Paper 3 / Paper 5: every number
comes from the saved paper-mirror order rows (momx/paper_mirror.py), never from
the BOT's virtual mid-price trades (those stay on the Research view and in
momx/truth_check.py).

Read-only: the small paper day files only, never the broker, never a write.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momx import loss_lessons, paper_control

ET = ZoneInfo("America/New_York")
ACCOUNTS = ("paper3", "paper5")
HOLDING = ("open", "selling", "exit_due")

# The evaluation, sized to how the paper bot trades (about $300 a position, 8
# open at most) and to the $500 daily loss limit in the 2026-11-04 agentic-AI
# spec. My call (Claude, 2026-11-09), one place to change them.
LIMITS = {
    "accountSize": 10_000,      # notional evaluation account
    "profitTarget": 1_000,      # +10%
    "dailyLossLimit": int(paper_control.DAILY_LOSS_STOP),   # 5% in one day; the paper bot's own stop uses the same number
    "maxDrawdown": 1_000,       # 10% from the highest point
    "minDays": 20,              # about 4 trading weeks
    "minTrades": 100,           # closed, really filled
    "minProfitFactor": 1.3,     # dollars won per dollar lost
    "minFillPct": 50,           # orders that actually fill
    "luckTrades": 3,            # still green without the best N trades
}
SMALL_SAMPLE = 20               # a rule with fewer closed fills proves nothing
# His fresh start (2026-11-09: "use only 3 strategies trades and 0.2 delta from
# tomorrow and start fresh scoreboard with 10k account size"). Orders signalled
# before this day are not judged: they were other rules on another contract.
EVALUATION_START = "2026-11-10"
EVALUATION_LABEL = "Level 1 / 2 / 3 on the 0.20-delta contract"


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pct(part, whole):
    return round(part / whole * 100, 1) if whole else None


def _read(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _day(stamp) -> str:
    return str(stamp or "")[:10]


def load_rows(folder: Path, start: str, end: str) -> list[dict]:
    """One row per order, newest file first so a carried order's latest status
    wins. Kept when its signal day is inside the window."""
    rows, seen = [], set()
    for path in sorted(folder.glob("20*.json"), reverse=True):
        doc = _read(path)
        found = doc.get("rows") if isinstance(doc, dict) else None
        for key, r in (found.items() if isinstance(found, dict) else ()):
            if key in seen or not isinstance(r, dict):
                continue
            seen.add(key)
            if start <= _day(r.get("at")) <= end:
                r["_key"] = key          # what the Remove button sends back for a stuck order
                rows.append(r)
    return rows


def _stamp_ts(stamp):
    try:
        return datetime.fromisoformat(str(stamp)).timestamp()
    except (TypeError, ValueError):
        return None


def _is_stuck(r, today: str, now_ts) -> bool:
    """An unfilled buy that never resolved: from an earlier day, or (when the
    time is known) older than the paper bot's own 10 minute allowance."""
    if r.get("status") != "buying" or _num(r.get("buyFill")):
        return False
    if _day(r.get("at")) < today:
        return True
    sent = _stamp_ts(r.get("buyAt"))
    return now_ts is not None and sent is not None and now_ts - sent > paper_control.STUCK_SECONDS


def _filled_qty(r) -> float:
    for key in ("buyFilledQty", "qty"):
        if _num(r.get(key)) and r[key] > 0:
            return r[key]
    return 1


def _closed(r) -> bool:
    return r.get("status") == "closed" and _num(r.get("buyFill")) and _num(r.get("pl"))


def _trade_stats(closed: list[dict]) -> dict:
    pls = [r["pl"] for r in closed]
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p < 0]
    cost = sum(r["buyFill"] * 100 * _filled_qty(r) for r in closed)
    gross_win, gross_loss = sum(wins), -sum(losses)
    net = sum(pls)
    best = sorted(pls, reverse=True)[:LIMITS["luckTrades"]]
    return {
        "n": len(pls), "wins": len(wins), "losses": len(losses), "flat": len(pls) - len(wins) - len(losses),
        "winRatePct": _pct(len(wins), len(pls)),
        "netPnl": round(net, 2), "roiPct": _pct(net, cost), "cost": round(cost, 2),
        "avgWin": round(gross_win / len(wins), 2) if wins else None,
        "avgLoss": round(-gross_loss / len(losses), 2) if losses else None,
        # No losing trade yet: a factor cannot be worked out, so it is not shown.
        "profitFactor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "expectancy": round(net / len(pls), 2) if pls else None,
        "bestTrade": round(max(pls), 2) if pls else None,
        "worstTrade": round(min(pls), 2) if pls else None,
        "pnlWithoutBest": round(net - sum(best), 2) if pls else None,
    }


def _days(rows: list[dict], closed: list[dict]) -> tuple[list[dict], dict]:
    """Day by day: orders by signal day, P/L on the day the trade closed (that is
    the day the account felt it). Running total and the fall from its peak."""
    days = defaultdict(lambda: {"sent": 0, "filled": 0, "closed": 0, "pnl": 0.0})
    for r in rows:
        if r.get("status") == "skipped":
            continue
        d = days[_day(r.get("at"))]
        d["sent"] += 1
        if _num(r.get("buyFill")):
            d["filled"] += 1
    for r in closed:
        d = days[_day(r.get("closedAt")) or _day(r.get("at"))]
        d["closed"] += 1
        d["pnl"] += r["pl"]
    out, cum, peak, drawdown = [], 0.0, 0.0, 0.0
    for day in sorted(days):
        d = days[day]
        cum += d["pnl"]
        peak = max(peak, cum)
        drawdown = max(drawdown, peak - cum)
        out.append({"day": day, "sent": d["sent"], "filled": d["filled"], "closed": d["closed"],
                    "pnl": round(d["pnl"], 2), "cum": round(cum, 2)})
    traded = [d for d in out if d["closed"]]
    limit = LIMITS["dailyLossLimit"]
    summary = {
        "tradingDays": len(traded),
        "greenDays": sum(1 for d in traded if d["pnl"] > 0),
        "redDays": sum(1 for d in traded if d["pnl"] < 0),
        "bestDay": max(traded, key=lambda d: d["pnl"], default=None),
        "worstDay": min(traded, key=lambda d: d["pnl"], default=None),
        "maxDrawdown": round(drawdown, 2),
        "dailyLimitBreaks": sum(1 for d in traded if d["pnl"] < -limit),
    }
    return out, summary


def strategies(r) -> list:
    """Every strategy one paper order counts for (momx/paper_mirror.py row["levels"])."""
    levels = r.get("levels")
    named = [x for x in levels if isinstance(x, str)] if isinstance(levels, list) else []
    return named or [r.get("rule")]


#: How each paper account chooses which waiting signal gets a free slot
#: (momx/paper_mirror.py ACCOUNTS, momx/paper_picker.py). Same signals, same
#: contract, same sizing and exits: the Scorecard compares the two ways of
#: choosing (his "see which strategy works good then we will use same for real
#: money", 2026-11-10).
ACCOUNT_MODES = {
    "paper3": {"key": "rules", "name": "Rules bot", "how": "the newest Level signal gets a free slot"},
    "paper5": {"key": "ai", "name": "AI bot", "how": "an AI ranks the waiting Level signals and the best one gets a free slot"},
}


def _pick_split(rows: list[dict]) -> dict:
    """On the AI bot: orders the AI chose against orders it had no ranking for
    (the rules order was used). Shows whether the AI is really doing the choosing."""
    sent = [r for r in rows if r.get("status") != "skipped" and "aiPick" in r]
    picked = [r for r in sent if isinstance(r.get("aiPick"), dict)]
    fallback = [r for r in sent if not isinstance(r.get("aiPick"), dict)]
    return {"picked": {"sent": len(picked), **_side_stats([r for r in picked if _closed(r)])},
            "fallback": {"sent": len(fallback), **_side_stats([r for r in fallback if _closed(r)])}}


def _versus(accounts: dict) -> dict:
    """The two accounts side by side, and which way of choosing is ahead. A
    leader is named only when BOTH have SMALL_SAMPLE closed real trades."""
    rows = []
    for account in ACCOUNTS:
        a, mode = accounts.get(account), ACCOUNT_MODES.get(account)
        if not a or not mode:
            continue
        s = a["stats"]
        rows.append({"account": account, "mode": mode["key"], "name": mode["name"], "how": mode["how"],
                     "sent": a["orders"]["sent"], "closed": s["n"], "winRatePct": s["winRatePct"], "pnl": s["netPnl"],
                     "roiPct": s["roiPct"], "perTrade": s["expectancy"], "balance": a.get("balance"),
                     # The most one trade moved this account: a lead smaller than that is noise.
                     "swing": max(abs(s["bestTrade"] or 0), abs(s["worstTrade"] or 0))})
    enough = len(rows) == 2 and all(r["closed"] >= SMALL_SAMPLE for r in rows)
    # The "AI bot" is only an AI result when the AI really chose its orders: at
    # least SMALL_SAMPLE closed trades it picked, and at least half of all its
    # closed trades. Otherwise the two accounts were both on the rules order.
    ai = next((accounts[k] for k in accounts if (ACCOUNT_MODES.get(k) or {}).get("key") == "ai"), None) or {}
    ai_chosen = (((ai.get("aiPicks") or {}).get("picked") or {}).get("closed")) or 0
    ai_closed = (ai.get("stats") or {}).get("n") or 0
    ai_proven = ai_chosen >= SMALL_SAMPLE and ai_chosen * 2 >= ai_closed
    ready = enough and ai_proven
    leader = None
    if ready:
        best = max(rows, key=lambda r: (r["roiPct"] if r["roiPct"] is not None else -1e9, r["pnl"]))
        other = next(r for r in rows if r is not best)
        # Ahead only by more than the biggest single trade on either account.
        if best["pnl"] > 0 and best["pnl"] - other["pnl"] > max(r["swing"] for r in rows):
            leader = best["account"]
    all_losing = enough and all(r["pnl"] <= 0 for r in rows)
    return {"rows": rows, "need": SMALL_SAMPLE, "enough": enough, "ready": ready, "leader": leader,
            "allLosing": all_losing, "tooClose": ready and leader is None and not all_losing,
            "aiChosenClosed": ai_chosen, "aiClosed": ai_closed, "aiProven": ai_proven}


#: The strategies the paper accounts trade, always shown side by side on the
#: Scorecard (his "Scoreboard I don't see 3 strategy? How I know which one is
#: good", 2026-11-10) - even before any of them has a trade.
STRATEGY_RULES = ("level1", "level2", "level3")


def _side_stats(closed: list[dict]) -> dict:
    s = _trade_stats(closed)
    return {"closed": s["n"], "wins": s["wins"], "winRatePct": s["winRatePct"], "pnl": s["netPnl"], "roiPct": s["roiPct"]}


def _strategies(rows: list[dict]) -> dict:
    """Level 1 / 2 / 3 compared on real fills: all, calls only, puts only. A
    strategy is named best only with SMALL_SAMPLE closed trades and a profit;
    until then the card says how many more it needs."""
    out, members = [], {}
    for rule in STRATEGY_RULES:
        mine = [r for r in rows if r.get("status") != "skipped" and rule in strategies(r)]
        closed = [r for r in mine if _closed(r)]
        filled = sum(1 for r in mine if _num(r.get("buyFill")))
        missed = sum(1 for r in mine if r.get("status") == "missed")
        stats = _trade_stats(closed)
        members[rule] = frozenset(str(r.get("_key") or id(r)) for r in mine)
        # Two levels that took exactly the same orders are one strategy for now.
        same = next((other for other in STRATEGY_RULES[:STRATEGY_RULES.index(rule)]
                     if mine and members[other] == members[rule]), None)
        out.append({
            "rule": rule, "sent": len(mine), "filled": filled, "fillRatePct": _pct(filled, filled + missed),
            "closed": stats["n"], "wins": stats["wins"], "winRatePct": stats["winRatePct"], "pnl": stats["netPnl"],
            "roiPct": stats["roiPct"], "perTrade": stats["expectancy"], "smallSample": stats["n"] < SMALL_SAMPLE,
            "calls": _side_stats([r for r in closed if (r.get("side") or "bull") != "bear"]),
            "puts": _side_stats([r for r in closed if r.get("side") == "bear"]),
            "sameAs": same,
        })
    judged = [s for s in out if not s["smallSample"] and not s["sameAs"]]
    best = max(judged, key=lambda s: (s["roiPct"] if s["roiPct"] is not None else -1e9, s["pnl"]), default=None)
    most = max(out, key=lambda s: s["closed"])
    return {"rows": out, "need": SMALL_SAMPLE,
            # Best = the highest return on money used among the strategies with enough trades, and only if it made money.
            "best": best["rule"] if best and best["pnl"] > 0 else None,
            "allLosing": bool(judged) and all(s["pnl"] <= 0 for s in judged),
            "mostClosed": {"rule": most["rule"], "closed": most["closed"]}}


def _rules(rows: list[dict]) -> list[dict]:
    by = defaultdict(lambda: {"sent": 0, "filled": 0, "missed": 0, "closed": []})
    for r in rows:
        if r.get("status") == "skipped":
            continue
        # The Level strategies stack (a Level 3 signal is also Level 2 and 1) and
        # one signal is one paper order, so that order counts for every level it
        # qualified for. The account totals above still count it once.
        for rule in strategies(r):
            v = by[(r.get("side") or "bull", rule)]
            v["sent"] += 1
            if r.get("status") == "missed":
                v["missed"] += 1
            elif _num(r.get("buyFill")):
                v["filled"] += 1
            if _closed(r):
                v["closed"].append(r)
    out = []
    for (side, rule), v in by.items():
        s = _trade_stats(v["closed"])
        out.append({"side": side, "rule": rule, "sent": v["sent"], "filled": v["filled"],
                    "fillRatePct": _pct(v["filled"], v["filled"] + v["missed"]),
                    "closed": s["n"], "wins": s["wins"], "winRatePct": s["winRatePct"],
                    "pnl": s["netPnl"], "roiPct": s["roiPct"], "smallSample": s["n"] < SMALL_SAMPLE})
    return sorted(out, key=lambda x: (-x["pnl"], x["side"], str(x["rule"])))


def _objectives(stats: dict, day_summary: dict, fill_pct) -> list[dict]:
    """The evaluation rules. `actual` None = cannot be judged yet = not passed."""
    L = LIMITS
    worst = day_summary["worstDay"]["pnl"] if day_summary["worstDay"] else None
    n = stats["n"]

    def row(key, target, actual, passed, **extra):
        return {"key": key, "target": target, "actual": actual, "pass": bool(passed), **extra}

    return [
        row("profit", L["profitTarget"], stats["netPnl"] if n else None, n and stats["netPnl"] >= L["profitTarget"]),
        row("dailyLoss", -L["dailyLossLimit"], worst, worst is not None and day_summary["dailyLimitBreaks"] == 0,
            breaks=day_summary["dailyLimitBreaks"]),
        row("drawdown", L["maxDrawdown"], day_summary["maxDrawdown"] if n else None,
            n and day_summary["maxDrawdown"] <= L["maxDrawdown"]),
        row("days", L["minDays"], day_summary["tradingDays"], day_summary["tradingDays"] >= L["minDays"]),
        row("trades", L["minTrades"], n, n >= L["minTrades"]),
        row("profitFactor", L["minProfitFactor"], stats["profitFactor"],
            stats["profitFactor"] is not None and stats["profitFactor"] >= L["minProfitFactor"]),
        row("luck", 0, stats["pnlWithoutBest"], stats["pnlWithoutBest"] is not None and stats["pnlWithoutBest"] > 0,
            trades=L["luckTrades"]),
        row("fill", L["minFillPct"], fill_pct, fill_pct is not None and fill_pct >= L["minFillPct"]),
    ]


def account_report(rows: list[dict], today: str, now_ts: float | None = None) -> dict:
    sent = [r for r in rows if r.get("status") != "skipped"]
    closed = [r for r in sent if _closed(r)]
    filled = sum(1 for r in sent if _num(r.get("buyFill")))
    missed = sum(1 for r in sent if r.get("status") == "missed")
    holding = [r for r in sent if _num(r.get("buyFill")) and r.get("status") in HOLDING]
    # An unfilled buy that never resolved: it holds a slot.
    stuck = sorted((r for r in sent if _is_stuck(r, today, now_ts)), key=lambda r: str(r.get("buyAt") or ""))
    stats = _trade_stats(closed)
    days, day_summary = _days(rows, closed)
    fill_pct = _pct(filled, filled + missed)
    objectives = _objectives(stats, day_summary, fill_pct)
    signal_days = sorted(_day(r.get("at")) for r in sent)
    stamps = sorted(str(r.get(k)) for r in sent for k in ("closedAt", "buyFillAt", "buyAt") if r.get(k))
    return {
        "orders": {"sent": len(sent), "filled": filled, "missed": missed, "skipped": len(rows) - len(sent),
                   "holding": len(holding), "stuck": len(stuck), "fillRatePct": fill_pct,
                   "stuckSymbols": sorted({str(r.get("symbol")) for r in stuck}),
                   "stuckRows": [{"key": r.get("_key"), "symbol": r.get("symbol"), "side": r.get("side") or "bull",
                                  "rule": r.get("rule"), "contract": r.get("contract"), "buyAt": r.get("buyAt"),
                                  "note": str(r.get("note") or "")[:200]} for r in stuck],
                   "holdingOvernight": sum(1 for r in holding if _day(r.get("at")) < today)},
        "stats": stats, "days": days, "daySummary": day_summary, "byRule": _rules(rows),
        "strategies": _strategies(rows),
        "objectives": objectives,
        "verdict": {"ready": all(o["pass"] for o in objectives),
                    "passed": sum(1 for o in objectives if o["pass"]), "total": len(objectives)},
        "from": signal_days[0] if signal_days else None, "through": signal_days[-1] if signal_days else None,
        "lastActivity": stamps[-1] if stamps else None,
    }


def build(artifacts: Path, days: int = 90, today: str | None = None, start_day: str | None = None) -> dict:
    artifacts = Path(artifacts)
    end = date.fromisoformat(today) if today else datetime.now(ET).date()
    start = (end - timedelta(days=max(1, days) - 1)).isoformat()
    end_s = end.isoformat()
    start_day = start_day or EVALUATION_START
    # A fixed `today` (tests, a past day) has no clock; the live page does.
    now_ts = None if today else datetime.now(ET).timestamp()
    root = artifacts / "momx_paper_mirror"
    pauses = paper_control.load(root)["paused"]
    accounts = {}
    for account in ACCOUNTS:
        folder = root / account
        if not folder.is_dir():
            continue
        rows = load_rows(folder, start, end_s)
        if rows:
            # Judged: only orders signalled since the fresh start. Still shown
            # from the older orders: what needs a hand today (stuck buys, a
            # position not yet sold) - those are real whatever rules opened them.
            judged = [r for r in rows if _day(r.get("at")) >= start_day]
            accounts[account] = account_report(judged, end_s, now_ts)
            live = account_report(rows, end_s, now_ts)["orders"]
            accounts[account]["orders"].update({k: live[k] for k in ("stuck", "stuckSymbols", "stuckRows", "holdingOvernight")})
            accounts[account]["balance"] = round(LIMITS["accountSize"] + accounts[account]["stats"]["netPnl"], 2)
            accounts[account]["mode"] = ACCOUNT_MODES.get(account)
            if (ACCOUNT_MODES.get(account) or {}).get("key") == "ai":
                accounts[account]["aiPicks"] = _pick_split(judged)
            # The paper bot's own safety state (momx/paper_control.py): the
            # Pause button, and whether today's daily loss stop has tripped.
            day_doc = _read(folder / f"{end_s}.json")
            hit = day_doc.get("dailyStop") if isinstance(day_doc, dict) and isinstance(day_doc.get("dailyStop"), dict) else None
            pause = pauses.get(account)
            accounts[account]["control"] = {
                "paused": bool(pause), "pausedAt": (pause or {}).get("at"),
                "dailyStop": {"limit": LIMITS["dailyLossLimit"], "hit": bool(hit), "hitAt": (hit or {}).get("at"),
                              "todayPnl": paper_control.day_pnl(rows, end_s)},
            }
    return {
        "available": bool(accounts), "days": days, "asOf": datetime.now(ET).isoformat(timespec="seconds"),
        "limits": dict(LIMITS), "smallSample": SMALL_SAMPLE,
        "startDay": start_day, "strategy": EVALUATION_LABEL,
        "versus": _versus(accounts),
        # What the AI learned from the losing real fills (momx/loss_lessons.py). None = not studied yet.
        "lessons": loss_lessons.read(artifacts),
        # The paper bot's off switch (momx/paper_mirror.py): this file present = paused.
        "paused": (artifacts / "paper_mirror_off").exists(),
        "accounts": accounts,
        "basis": "Real Alpaca paper fills only. Orders that never filled earn nothing. "
                 "P/L lands on the day the trade closed.",
    }
