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

ET = ZoneInfo("America/New_York")
ACCOUNTS = ("paper3", "paper5")
HOLDING = ("open", "selling", "exit_due")

# The evaluation, sized to how the paper bot trades (about $300 a position, 8
# open at most) and to the $500 daily loss limit in the 2026-11-04 agentic-AI
# spec. My call (Claude, 2026-11-09), one place to change them.
LIMITS = {
    "accountSize": 10_000,      # notional evaluation account
    "profitTarget": 1_000,      # +10%
    "dailyLossLimit": 500,      # 5% in one day
    "maxDrawdown": 1_000,       # 10% from the highest point
    "minDays": 20,              # about 4 trading weeks
    "minTrades": 100,           # closed, really filled
    "minProfitFactor": 1.3,     # dollars won per dollar lost
    "minFillPct": 50,           # orders that actually fill
    "luckTrades": 3,            # still green without the best N trades
}
SMALL_SAMPLE = 20               # a rule with fewer closed fills proves nothing


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
                rows.append(r)
    return rows


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


def _rules(rows: list[dict]) -> list[dict]:
    by = defaultdict(lambda: {"sent": 0, "filled": 0, "missed": 0, "closed": []})
    for r in rows:
        if r.get("status") == "skipped":
            continue
        v = by[(r.get("side") or "bull", r.get("rule"))]
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


def account_report(rows: list[dict], today: str) -> dict:
    sent = [r for r in rows if r.get("status") != "skipped"]
    closed = [r for r in sent if _closed(r)]
    filled = sum(1 for r in sent if _num(r.get("buyFill")))
    missed = sum(1 for r in sent if r.get("status") == "missed")
    holding = [r for r in sent if _num(r.get("buyFill")) and r.get("status") in HOLDING]
    # An unfilled buy from an earlier day that never resolved: it holds a slot.
    stuck = [r for r in sent if r.get("status") == "buying" and not _num(r.get("buyFill")) and _day(r.get("at")) < today]
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
                   "holdingOvernight": sum(1 for r in holding if _day(r.get("at")) < today)},
        "stats": stats, "days": days, "daySummary": day_summary, "byRule": _rules(rows),
        "objectives": objectives,
        "verdict": {"ready": all(o["pass"] for o in objectives),
                    "passed": sum(1 for o in objectives if o["pass"]), "total": len(objectives)},
        "from": signal_days[0] if signal_days else None, "through": signal_days[-1] if signal_days else None,
        "lastActivity": stamps[-1] if stamps else None,
    }


def build(artifacts: Path, days: int = 90, today: str | None = None) -> dict:
    artifacts = Path(artifacts)
    end = date.fromisoformat(today) if today else datetime.now(ET).date()
    start = (end - timedelta(days=max(1, days) - 1)).isoformat()
    end_s = end.isoformat()
    accounts = {}
    for account in ACCOUNTS:
        folder = artifacts / "momx_paper_mirror" / account
        if not folder.is_dir():
            continue
        rows = load_rows(folder, start, end_s)
        if rows:
            accounts[account] = account_report(rows, end_s)
    return {
        "available": bool(accounts), "days": days, "asOf": datetime.now(ET).isoformat(timespec="seconds"),
        "limits": dict(LIMITS), "smallSample": SMALL_SAMPLE,
        # The paper bot's off switch (momx/paper_mirror.py): this file present = paused.
        "paused": (artifacts / "paper_mirror_off").exists(),
        "accounts": accounts,
        "basis": "Real Alpaca paper fills only. Orders that never filled earn nothing. "
                 "P/L lands on the day the trade closed.",
    }
