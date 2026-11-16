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
import threading
import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momx import fill_chance, loss_lessons, paper_control

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
# 2026-11-12: a NEW evaluation from the next session. After the paper bots lost
# $4,621 and $6,399 in three days he moved them to the chart's Entry / Target /
# Stop plan ("use that entry the position and target 1,2,3 ... and stop loss";
# momx/chart_plan.py). The new plan's results must not be mixed with the old
# plan's, so the Scorecard starts again here; 2026-11-10..12 stay visible as the
# earlier period (EARLIER_PERIODS).
EVALUATION_START = "2026-11-13"
#: The first day of the chart plan. A fact about the bot, not the Scorecard's start: an order
#: dated from this day on WITHOUT the chart-plan marker is the old plan's (old_plan_after_switch).
#: (EVALUATION_START is patched by tests; this is not.)
CHART_PLAN_DAY = "2026-11-13"
#: The day the paper accounts began trading the Level strategies (the first evaluation).
LEVELS_START = "2026-11-10"
# 2026-11-10 (his rule, "Yes change it rule or confirm"): the entry is Setup 3+
# with a confirm (Level 1 = one of RVOL / SQZ / Skittles, Level 2 = two, Level 3 =
# all three), then the 5m chart check (momx/chart_check.py), 3+ contracts.
EVALUATION_LABEL = ("Level 1 / 2 / 3 (Setup 3+ with RVOL, SQZ or Skittles) after the 5m chart check, "
                    "bought at the ask from 9:30 on the chart's Entry / Target / Stop plan "
                    "(stock stop, T1 / T2 / T3), 3+ contracts on the 0.20-delta contract")
#: Evaluations that ended, newest first: shown on the Scorecard as "the earlier period", never
#: added to the current one.
EARLIER_PERIODS = (
    {"startDay": "2026-11-10", "endDay": "2026-11-12",
     "strategy": "Level 1 / 2 / 3 after the 5m chart check, bought at the mid from 9:30, sold 2 of 3 at +100% or the "
                 "high-OI level, option stop at -50%",
     "why": "Ended 11-12: both paper accounts lost money in three days, most of it on 9:30-10:00 buys, and several "
            "-50% stops closed at -78% to -93%. He replaced the entry, stop and targets with the chart's plan."},
)
#: What the page says the bots trade while the running paper bot is still on the plan before
#: the chart plan (no plan.json marker from momx/paper_mirror.py yet: the servers have not
#: restarted since the chart plan was built).
OLD_PLAN_LABEL = EARLIER_PERIODS[0]["strategy"]


def plan_running(root: Path) -> dict | None:
    """Which exit plan the RUNNING paper bots follow, from the marker each one writes when it
    starts (momx/paper_mirror.PaperMirror._plan_mark): {"plan": "chart", "since": ...} once
    every account folder that exists carries the chart-plan marker, else None (still the
    older plan). The Bot page shows the chart plan's text and evaluation only on this."""
    from momx.paper_mirror import EXIT_PLAN, PLAN_MARK_FILE
    since = []
    for account in ACCOUNTS:
        folder = Path(root) / account
        if not folder.is_dir():
            continue
        mark = _read(folder / PLAN_MARK_FILE)
        if not isinstance(mark, dict) or mark.get("plan") != EXIT_PLAN:
            return None
        since.append(str(mark.get("since") or ""))
    return {"plan": EXIT_PLAN, "since": max(since)} if since else None


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _value(v):
    """The number itself (or None); _num only answers whether it is one."""
    return float(v) if _num(v) else None


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


#: Closed trades listed in the journal, newest first.
JOURNAL_TRADES = 150


def _week_of(day: str) -> str:
    """The Monday of a day's week, as its key ('2026-11-09')."""
    d = date.fromisoformat(day)
    return (d - timedelta(days=d.weekday())).isoformat()


def _journal(rows: list[dict]) -> dict:
    """The Scorecard as a trading journal (his "how I see the d/w/m pnl with which strategy is
    good for real money. Scoreboard will be like a journal", 2026-11-10).

    Real closed fills only, on the day each trade closed. For every day, week and month: the
    trades, wins, P/L, and the same P/L split by strategy, with the best strategy of that
    period named when it made money. One trade counts under every Level it qualified for
    (as everywhere on the Scorecard); the period's own total counts it once."""
    closed = [r for r in rows if _closed(r)]
    buckets = {"day": defaultdict(list), "week": defaultdict(list), "month": defaultdict(list)}
    for r in closed:
        day = _day(r.get("closedAt")) or _day(r.get("at"))
        try:
            week = _week_of(day)
        except ValueError:
            continue
        buckets["day"][day].append(r)
        buckets["week"][week].append(r)
        buckets["month"][day[:7]].append(r)

    def period(key: str, mine: list[dict]) -> dict:
        s = _trade_stats(mine)
        split = []
        for rule in STRATEGY_RULES:
            took = [r for r in mine if rule in strategies(r)]
            split.append({"rule": rule, "trades": len(took), "pnl": round(sum(r["pl"] for r in took), 2)})
        named = [x for x in split if x["trades"]]
        best = max(named, key=lambda x: x["pnl"], default=None)
        return {"key": key, "trades": s["n"], "wins": s["wins"], "losses": s["losses"], "winRatePct": s["winRatePct"],
                "pnl": s["netPnl"], "roiPct": s["roiPct"], "strategies": split,
                "calls": round(sum(r["pl"] for r in mine if (r.get("side") or "bull") != "bear"), 2),
                "puts": round(sum(r["pl"] for r in mine if r.get("side") == "bear"), 2),
                # The strategy that made the most that period - only when it made money.
                "best": best["rule"] if best and best["pnl"] > 0 else None}

    def last_exit(r: dict) -> str:
        sells = [x for x in (r.get("sells") or []) if isinstance(x, dict)]
        return str(sells[-1].get("why") or "")[:60] if sells else ""

    trades = []
    for r in sorted(closed, key=lambda r: str(r.get("closedAt") or ""), reverse=True)[:JOURNAL_TRADES]:
        cost = r["buyFill"] * 100 * _filled_qty(r)
        trades.append({"symbol": r.get("symbol"), "side": r.get("side") or "bull", "rule": r.get("rule"), "levels": strategies(r),
                       "day": _day(r.get("closedAt")) or _day(r.get("at")), "boughtAt": r.get("buyFillAt") or r.get("buyAt"),
                       "closedAt": r.get("closedAt"), "qty": _filled_qty(r), "buy": r["buyFill"], "pnl": round(r["pl"], 2),
                       "roiPct": _pct(r["pl"], cost), "exit": last_exit(r), "contract": r.get("contract"),
                       "aiPicked": isinstance(r.get("aiPick"), dict), "plan": _order_plan(r)})
    return {"periods": {name: [period(key, mine) for key, mine in sorted(group.items(), reverse=True)]
                        for name, group in buckets.items()},
            "trades": trades, "tradeCount": len(closed)}


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


#: Not-filled and skipped rows the "All trades" card carries (newest first). Trades that
#: filled - open or closed - are never cut: they are the journal.
TRADE_LOG_ROWS = 300
LIVE_STATUSES = ("buying", "open", "selling", "exit_due")


#: The day's Level signals, kept: reading a day's Bot files takes about 10 s of CPU (two ~9 MB files),
#: far too slow to do on every Scorecard build. Re-read in the background at most every 10 minutes, and
#: only while someone has the Scorecard open (a build is what asks for it).
SIGNAL_SECONDS = 600.0
#: Files smaller than this are read inline (a quiet day, the tests); bigger ones only in the background.
SIGNAL_INLINE_BYTES = 2_000_000
_SIGNALS: dict[tuple, tuple] = {}
_SIGNALS_LOADING: set = set()
_SIGNALS_LOCK = threading.Lock()


def _load_signals(artifacts: Path, day: str) -> list[tuple[str, dict]]:
    from momx import chatbot
    records = chatbot.records_for(Path(artifacts), day, datetime.now(ET).date().isoformat())
    # One signal can stand at Level 1, 2 and 3 at the same moment (the levels stack); like the paper
    # bot, keep it once, at its highest level.
    top: dict[tuple, tuple] = {}
    for side, t in records.signals:
        rule = str(t.get("rule") or "")
        if rule not in STRATEGY_RULES:
            continue
        key = (side, t.get("symbol"), t.get("at"))
        if key not in top or rule > str(top[key][1].get("rule")):
            top[key] = (side, t)
    return list(top.values())


def _bot_signals(artifacts: Path, day: str) -> list[tuple[str, dict]]:
    """Every Level signal the Bot gave on a day - the Research page's "All trades" (his "i dont see all
    trade from research", 2026-11-10). Answers from what is kept at once; a stale or missing copy is
    re-read in the background, so a Scorecard build never waits ~10 s for it."""
    key = (str(artifacts), day)
    with _SIGNALS_LOCK:
        held = _SIGNALS.get(key)
        fresh = held is not None and time.monotonic() - held[0] < SIGNAL_SECONDS
        start = not fresh and key not in _SIGNALS_LOADING
        if start:
            _SIGNALS_LOADING.add(key)
    if not start:
        return held[1] if held else []

    def load() -> list:
        try:
            found = _load_signals(artifacts, day)
            with _SIGNALS_LOCK:
                _SIGNALS[key] = (time.monotonic(), found)
                while len(_SIGNALS) > 4:
                    _SIGNALS.pop(next(iter(_SIGNALS)))
            return found
        except Exception:  # noqa: BLE001 - the Scorecard must open without them
            return held[1] if held else []
        finally:
            with _SIGNALS_LOCK:
                _SIGNALS_LOADING.discard(key)

    size = 0
    for folder in ("momx_option_track", Path("bear") / "momx_option_track"):
        try:
            size += (Path(artifacts) / folder / f"{day}.json").stat().st_size
        except OSError:
            pass
    if held is None and size < SIGNAL_INLINE_BYTES:
        return load()
    try:
        threading.Thread(target=load, name="bot-desk-signals", daemon=True).start()
    except RuntimeError:
        with _SIGNALS_LOCK:
            _SIGNALS_LOADING.discard(key)
    return held[1] if held else []


def _chart_reason(side: str, t: dict, chart_skips: dict | None) -> str | None:
    """The chart check's reason for a signal (his rule 2026-11-10): from the paper bot's own saved
    skips for this account and day, matched by side, ticker and signal time (any Level of it)."""
    for v in (chart_skips or {}).values():
        if (isinstance(v, dict) and (v.get("side") or "bull") == side and v.get("symbol") == t.get("symbol")
                and str(v.get("at") or "") == str(t.get("at") or "") and v.get("why")):
            return str(v["why"])
    return None


def _top_passed(side: str, t: dict, top_skips: dict | None) -> bool:
    """True when the paper bot saved that this signal PASSED its top-of-board check at buy time
    (a topSkips entry marked "passed", no "why"), matched by side, ticker and signal time."""
    for v in (top_skips or {}).values():
        if (isinstance(v, dict) and (v.get("side") or "bull") == side and v.get("symbol") == t.get("symbol")
                and str(v.get("at") or "") == str(t.get("at") or "") and v.get("passed") and not v.get("why")):
            return True
    return False


def _chart_recheck(side: str, t: dict) -> str | None:
    """No saved skip (an older day file, or the bot was not running): run the same chart check on the
    signal's frozen chart. None when it passes or there is no chart to check."""
    try:
        from momx import chart_check
        from momx.chart_learning import snapshot
        return chart_check.check(side, snapshot(t))
    except Exception:  # noqa: BLE001 - a reason is a nicety; the Scorecard must still build
        return None


def _not_ordered_why(side: str, t: dict, orders: list[dict], stop: dict | None,
                     chart_skips: dict | None = None, fund_skips: dict | None = None,
                     top_skips: dict | None = None) -> str:
    """Why this account sent no order for a Bot signal, from what the records show. Plain and honest:
    when nothing on file explains it, it says so."""
    from momx.paper_mirror import MAX_PRICE, MIN_PRICE, TOP_OK, stamp_verdict
    from momx.option_track import PLAN_OUTSIDE_WHY, plan_window_note
    at = str(t.get("at") or "")
    try:
        when = datetime.fromisoformat(at).astimezone(ET)
        minute = when.hour * 60 + when.minute
    except ValueError:
        minute = None
    # The buy windows (9:30-11:00 and 1:00-3:30 ET; the 10:00 start of 2026-11-12 was taken back
    # the next morning, before it ever ran). The same clock as the Bot's plan and the paper bot's
    # buy (option_track.plan_window_note, on the later of the signal and entry times): a signal
    # in the last minutes before a window opens is still bought when it opens, so "outside the
    # buy windows" would be untrue for it.
    if plan_window_note(at, t.get("entryAt")) is not None:
        return PLAN_OUTSIDE_WHY
    # The paper bot never buys a Level signal that has no chart plan (no stop, no targets).
    plan_note = str(t.get("planNote") or "")
    if plan_note.startswith("No chart plan") and not isinstance(t.get("plan"), dict):
        return "no chart plan (" + plan_note.split(": ", 1)[-1] + "); the bot does not buy without a stop and targets"
    if isinstance(stop, dict) and str(stop.get("at") or "") <= at and (not stop.get("liftedAt") or at < str(stop["liftedAt"])):
        return "the daily loss stop was holding buys"
    entry, bid = _value(t.get("eEntry")), _value(t.get("eEntryBid"))
    if entry is None or not t.get("eContract"):
        return "no 0.20-delta option was found"
    if entry < MIN_PRICE or (bid is not None and bid <= 0):
        return "the 0.20-delta option was under $0.05 or had no bid"
    # His top-of-board rule (2026-11-10): the paper bot recorded why the ticker was not in its
    # board's top N by H/L when it tried to buy (out of the top, or the 5-min hold from 9:45 not
    # reached while the signal was fresh - the latest rank / hold it saw). It runs before the
    # chart check, so it is the reason first.
    not_top = _chart_reason(side, t, top_skips)
    if not_top:
        return not_top
    # The paper bot recorded that it turned this signal down on the chart: that is the reason.
    saved = _chart_reason(side, t, chart_skips)
    if saved:
        return f"the chart check failed: {saved}"
    # The paper account could not pay for the 3+ contracts (saved by the bot, same shape as a chart skip).
    short = _chart_reason(side, t, fund_skips)
    if short:
        return short

    def live_then(r: dict) -> bool:
        """An order in this ticker (either side - the bot holds one position per ticker) that was being
        bought or held at the signal's moment: sent before it, not closed before it, never a miss."""
        sent_at = str(r.get("buyAt") or r.get("at") or "")
        if r.get("symbol") != t.get("symbol") or not r.get("buyId") or not sent_at or sent_at > at:
            return False
        if r.get("status") in ("missed", "skipped"):
            return False
        closed_at = str(r.get("closedAt") or "")
        return not closed_at or closed_at > at

    if any(live_then(r) for r in orders):
        return f"a {t.get('symbol')} position or buy was already open (one position per ticker)"
    # No saved skip: the bot checks only once a ticker is free, so a held ticker (above) explains it
    # first; otherwise the same top-of-board verdict (paper_mirror.top_verdict) on the record's own
    # "topHL" stamp at the record's time, then the same chart check on the signal's frozen chart.
    # A "passed" topSkips entry: the bot's top check PASSED at buy time, so the record's frozen stamp
    # (which may still read as a short hold) is not the reason - go on to the other reasons.
    if not _top_passed(side, t, top_skips):
        top_state, top_why = stamp_verdict(t)
        if top_state != TOP_OK and top_why:
            return top_why
    # Only now the signal-time price: the bot sizes on the LIVE mid after its other checks, so a
    # saved reason above always wins over this estimate.
    if entry > MAX_PRICE:
        return f"the option was over ${MAX_PRICE:.2f} (the bot skips those)"
    again = _chart_recheck(side, t)
    if again:
        return f"the chart check failed: {again}"
    return "not ordered by this bot (the records do not say why)"


def _hits(found) -> list[dict]:
    return [{"target": h.get("target"), "at": h.get("at")} for h in (found or []) if isinstance(h, dict) and h.get("target")]


def _order_plan(r: dict) -> dict | None:
    """The chart plan of one paper order (momx/paper_mirror.py, EXIT_PLAN): what it paid (the ask),
    the stock price at entry, the stock stop as first set and as it stands now, T1 / T2 / T3 and
    which were hit when. None for an order from before the chart plan."""
    plan = r.get("plan") if isinstance(r.get("plan"), dict) else {}
    if r.get("exitPlan") != "chart" and not plan:
        return None
    return {"entryAsk": _value(r.get("entryAsk")), "entryStock": _value(r.get("entryStock")),
            "stop": _value(r.get("stopStock0")), "stopNow": _value(r.get("stopStock")), "stopKind": r.get("stopKind"),
            "t1": _value(plan.get("t1")), "t2": _value(plan.get("t2")), "t3": _value(plan.get("t3")),
            "hits": _hits(r.get("hits")), "ready": bool(plan)}


def _signal_plan(t: dict) -> dict | None:
    """The chart plan of a Bot signal no order was sent for (momx/option_track.py): the same
    numbers, and how three contracts would have done on it (an estimate at the mid, never dollars
    in the real totals)."""
    plan = t.get("plan") if isinstance(t.get("plan"), dict) else None
    if not plan:
        return None
    st = t.get("planState") if isinstance(t.get("planState"), dict) else {}
    return {"entryAsk": _value(plan.get("entry")), "entryStock": _value(plan.get("entrySpot")),
            "stop": _value(plan.get("stopStock")), "stopNow": _value(st.get("stopStock")), "stopKind": st.get("stopKind"),
            "t1": _value(plan.get("t1")), "t2": _value(plan.get("t2")), "t3": _value(plan.get("t3")),
            "hits": _hits(st.get("hits")), "ready": True,
            "status": st.get("status"), "pct": _value(st.get("pct")), "reason": st.get("reason")}


def old_plan_after_switch(r: dict) -> bool:
    """True for an ORDER dated on or after the chart plan's first day that was NOT opened under
    it (no ``exitPlan: "chart"`` marker from momx/paper_mirror.py). The paper worker trades the
    older plan until it is restarted, so such an order can be sent on the morning of the switch,
    or sent the day before and filled after it. It is a real order, but it is the OLD plan's
    result: it is never judged in the chart plan's evaluation (2026-11-13 review: one such
    order, -$591, made the new plan's clean record open at $9,409). A "skipped" row is not an
    order and has no plan."""
    return (_day(r.get("at")) >= CHART_PLAN_DAY and r.get("status") != "skipped"
            and r.get("exitPlan") != "chart")


def _earlier(rows: list[dict], start_day: str) -> list[dict]:
    """The evaluations that ended (EARLIER_PERIODS), from this account's own orders: kept visible,
    never mixed into the current one. Only days before the current start count - plus, in the
    newest period (the plan the chart plan replaced), the older-plan orders sent after the
    switch (old_plan_after_switch), counted in ``afterSwitch``."""
    out = []
    late = [r for r in rows if old_plan_after_switch(r) and _day(r.get("at")) >= start_day]
    for i, period in enumerate(EARLIER_PERIODS):
        end = min(period["endDay"], (date.fromisoformat(start_day) - timedelta(days=1)).isoformat())
        mine = [r for r in rows if period["startDay"] <= _day(r.get("at")) <= end]
        after = late if i == 0 else []
        mine = mine + after
        if not mine:
            continue
        closed = [r for r in mine if _closed(r)]
        s = _trade_stats(closed)
        out.append({"startDay": period["startDay"], "endDay": period["endDay"], "strategy": period["strategy"],
                    "why": period["why"], "orders": sum(1 for r in mine if r.get("status") != "skipped"),
                    "trades": s["n"], "wins": s["wins"], "losses": s["losses"], "winRatePct": s["winRatePct"],
                    "netPnl": s["netPnl"], "worstTrade": s["worstTrade"], "bestTrade": s["bestTrade"],
                    "stillOpen": sum(1 for r in mine if _num(r.get("buyFill")) and r.get("status") in HOLDING),
                    "afterSwitch": len(after),
                    "rows": _journal(mine)["trades"]})
    return out


def _trade_log(rows: list[dict], earlier: list[dict] | None = None, signals: list | None = None,
               stop: dict | None = None, chart_skips: dict | None = None, fund_skips: dict | None = None,
               top_skips: dict | None = None) -> dict:
    """Every signal of the evaluation, newest first (his "Scoreboard I don't see all trade", 2026-11-10):
    trades still open, closed ones, orders that never filled, orders still waiting, and signals the
    bot passed over (with its reason). A closed trade carries its real P/L. An open one carries
    what it has banked from part-sales plus the rest at the last mid price, marked ``live`` with
    the time of that price - an estimate, never added to the real totals.

    ``earlier``: orders from before the fresh start that are still live (a position not sold yet,
    a buy still waiting). They are real, so they are listed, flagged ``scored: False``."""
    def kind(r: dict) -> str:
        status = r.get("status")
        if status == "skipped":
            return "skipped"
        if status == "closed":          # also when the result could not be priced: it is not held any more
            return "closed"
        if status == "missed":
            return "missed"
        if _num(r.get("buyFill")):
            return "open"
        return "waiting"

    def row(r: dict, status: str, scored: bool) -> dict:
        fill = r.get("buyFill") if _num(r.get("buyFill")) else None
        qty = _filled_qty(r) if fill is not None else (r.get("qty") if _num(r.get("qty")) else None)
        sells = [x for x in (r.get("sells") or []) if isinstance(x, dict)]
        priced = [x for x in sells if _num(x.get("qty")) and _num(x.get("price"))]
        pnl = roi = None
        live = False
        if status == "closed" and _closed(r):
            pnl = round(r["pl"], 2)
            roi = _pct(r["pl"], fill * 100 * qty)
        elif status == "open" and _num(r.get("markMid")) and len(priced) == len(sells):
            gone = sum(x["qty"] for x in priced)
            # The bot's own count of what is still held wins over the arithmetic.
            held = r["held"] if _num(r.get("held")) and r["held"] >= 0 else max(qty - gone, 0)
            banked = sum((x["price"] - fill) * 100 * x["qty"] for x in priced)
            pnl = round(banked + (r["markMid"] - fill) * 100 * held, 2)
            roi = _pct(pnl, fill * 100 * qty)
            live = True
        why = str(sells[-1].get("why") or "")[:80] if sells else ""
        if status == "closed":
            note = why if _closed(r) else "closed, result not priced"
        elif status == "open":
            note = ("part sold: " + why[:60]) if sells else "holding"
            # A sale the paper broker keeps refusing: say so, instead of a silent "holding".
            said = str(r.get("note") or "")
            if said.startswith("The broker refused this sale"):
                note = said[:120]
        elif status == "missed":
            # A buy the broker refused (no buying power) is not the same as one nobody sold into.
            said = str(r.get("note") or "")
            note = said[:120] if said.startswith("The broker refused") else "the order did not fill"
        elif status == "waiting":
            note = "order sent, waiting for a fill"
        else:
            note = str(r.get("note") or "")[:120]
        if not scored and old_plan_after_switch(r):
            note = "older plan (sent before the bot was restarted), not scored · " + note
        elif not scored:
            note = "from before the fresh start, not scored · " + note
        return {"key": r.get("_key") or f"{r.get('side')}|{r.get('rule')}|{r.get('symbol')}|{r.get('at')}",
                "status": status, "symbol": r.get("symbol"), "side": r.get("side") or "bull", "rule": r.get("rule"),
                "levels": strategies(r), "contract": r.get("contract"), "at": r.get("at"),
                "boughtAt": r.get("buyFillAt") or r.get("buyAt"), "closedAt": r.get("closedAt"),
                "qty": qty, "buy": fill, "limit": r.get("buyLimit") if _num(r.get("buyLimit")) else None,
                "pnl": pnl, "roiPct": roi, "live": live, "markAt": r.get("markAt") if live else None, "note": note,
                "scored": scored, "aiPicked": isinstance(r.get("aiPick"), dict),
                "fillChance": fill_chance.label_of(r),
                # The chart plan: entry at the ask, the stock stop, T1 / T2 / T3 and which were hit.
                "plan": _order_plan(r)}

    def signal_row(side: str, t: dict) -> dict:
        """A Bot signal this account sent no order for: the 0.20-delta option's own result at the
        mid (what Research shows), never a dollar figure - nothing was bought."""
        entry, last, best = _value(t.get("eEntry")), _value(t.get("eLast")), _value(t.get("eBest"))
        now_pct = _pct(last - entry, entry) if entry and last is not None else None
        best_pct = _pct(best - entry, entry) if entry and best is not None else None
        price = f"0.20-delta option ${entry:.2f}" if entry else "no 0.20-delta option"
        moved = (f", now {'+' if now_pct > 0 else ''}{now_pct:.0f}%" if now_pct is not None else "") + \
                (f", best {'+' if best_pct > 0 else ''}{best_pct:.0f}%" if best_pct is not None and entry else "")
        return {"key": f"{side}|{t.get('rule')}|{t.get('symbol')}|{t.get('at')}", "status": "signal",
                "symbol": t.get("symbol"), "side": side, "rule": t.get("rule"), "levels": [t.get("rule")],
                "contract": t.get("eContract"), "at": t.get("at"), "boughtAt": None, "closedAt": None,
                "qty": None, "buy": None, "limit": None, "pnl": None, "roiPct": now_pct, "bestPct": best_pct,
                "live": False, "markAt": None, "board": t.get("board"), "scored": False, "aiPicked": False,
                "plan": _signal_plan(t),
                "note": f"Not ordered: {_not_ordered_why(side, t, rows, stop, chart_skips, fund_skips, top_skips)} · {price}{moved}"[:200]}

    tagged = [(r, True) for r in rows] + [(r, False) for r in (earlier or []) if r.get("status") in LIVE_STATUSES]
    # A signal counts as ordered when this account has an order for the same ticker, side and moment
    # (the order carries the signal's highest level).
    sent = {(r.get("side") or "bull", r.get("symbol"), r.get("at")) for r in list(rows) + list(earlier or [])}
    waiting_signals = [(s, t) for s, t in (signals or []) if (s, t.get("symbol"), t.get("at")) not in sent]
    out, counts, unfilled = [], {"open": 0, "closed": 0, "missed": 0, "waiting": 0, "skipped": 0, "signal": 0}, 0
    items = [("row", r, scored) for r, scored in tagged] + [("signal", t, s) for s, t in waiting_signals]
    for kind_of, r, extra in sorted(items, key=lambda x: str(x[1].get("at") or ""), reverse=True):
        status = "signal" if kind_of == "signal" else kind(r)
        counts[status] += 1
        if status in ("missed", "skipped", "signal"):
            unfilled += 1
            if unfilled > TRADE_LOG_ROWS:
                continue
        out.append(signal_row(extra, r) if kind_of == "signal" else row(r, status, extra))
    return {"rows": out, "counts": counts, "total": len(items)}


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
        "journal": _journal(rows),
        # How the Low / Medium / High fill labels really did (momx/fill_chance.py).
        "fillChance": fill_chance.measured(sent),
        "tradeLog": _trade_log(rows),
        "objectives": objectives,
        "verdict": {"ready": all(o["pass"] for o in objectives),
                    "passed": sum(1 for o in objectives if o["pass"]), "total": len(objectives)},
        "from": signal_days[0] if signal_days else None, "through": signal_days[-1] if signal_days else None,
        "lastActivity": stamps[-1] if stamps else None,
    }


def _broker(folder: Path, end: str) -> dict | None:
    """The newest Alpaca account read the paper bot saved, on or before `end` (2026-11-10: "scoreboard
    should show alpaca buying power"). None when the bot has never read it."""
    for path in sorted(folder.glob("20*.json"), reverse=True):
        if path.stem > end:
            continue
        doc = _read(path)
        found = doc.get("broker") if isinstance(doc, dict) else None
        if isinstance(found, dict) and found.get("at"):
            return {k: found.get(k) for k in ("at", "cash", "optionsBuyingPower", "buyingPower", "equity", "lastEquity", "error")}
    return None


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
            # And only orders opened under the plan being judged: an older-plan order sent after
            # the switch (the worker not restarted yet) belongs to the earlier period.
            judged = [r for r in rows if _day(r.get("at")) >= start_day and not old_plan_after_switch(r)]
            accounts[account] = account_report(judged, end_s, now_ts)
            live = account_report(rows, end_s, now_ts)["orders"]
            accounts[account]["orders"].update({k: live[k] for k in ("stuck", "stuckSymbols", "stuckRows", "holdingOvernight")})
            # All trades also lists what is still live from before the fresh start (a position not sold yet),
            # and every Level signal of the day this account sent no order for (the Research list).
            today_doc = _read(folder / f"{end_s}.json")
            stop_doc = today_doc.get("dailyStop") if isinstance(today_doc, dict) and isinstance(today_doc.get("dailyStop"), dict) else None
            # The signals the paper bot's chart check turned down today, with its reason (his rule 2026-11-10).
            skips_doc = today_doc.get("chartSkips") if isinstance(today_doc, dict) and isinstance(today_doc.get("chartSkips"), dict) else None
            # And the ones it could not pay for (not enough buying power in the paper account).
            funds_doc = today_doc.get("fundSkips") if isinstance(today_doc, dict) and isinstance(today_doc.get("fundSkips"), dict) else None
            # And the ones whose ticker was not in its board's top N by H/L for 5 min (his rule 2026-11-10).
            tops_doc = today_doc.get("topSkips") if isinstance(today_doc, dict) and isinstance(today_doc.get("topSkips"), dict) else None
            accounts[account]["tradeLog"] = _trade_log(judged, [r for r in rows if _day(r.get("at")) < start_day
                                                                or old_plan_after_switch(r)],
                                                       _bot_signals(artifacts, end_s) if end_s >= start_day else [], stop_doc,
                                                       skips_doc, funds_doc, tops_doc)
            # The evaluations that ended (the plan before the chart plan): visible, never mixed in.
            accounts[account]["earlier"] = _earlier(rows, start_day)
            accounts[account]["balance"] = round(LIMITS["accountSize"] + accounts[account]["stats"]["netPnl"], 2)
            accounts[account]["mode"] = ACCOUNT_MODES.get(account)
            if (ACCOUNT_MODES.get(account) or {}).get("key") == "ai":
                accounts[account]["aiPicks"] = _pick_split(judged)
            # The paper bot's own safety state (momx/paper_control.py): the
            # Pause button, and whether today's daily loss stop has tripped.
            day_doc = _read(folder / f"{end_s}.json")
            # The paper account's real money at Alpaca, as the paper bot last read it (newest day file that has it).
            accounts[account]["broker"] = _broker(folder, end_s)
            hit = day_doc.get("dailyStop") if isinstance(day_doc, dict) and isinstance(day_doc.get("dailyStop"), dict) else None
            pause = pauses.get(account)
            accounts[account]["control"] = {
                "paused": bool(pause), "pausedAt": (pause or {}).get("at"),
                # "hit" = buys are held by it right now; never while the stop is switched off.
                "dailyStop": {"on": paper_control.DAILY_STOP_ON, "limit": LIMITS["dailyLossLimit"],
                              "hit": bool(hit) and paper_control.DAILY_STOP_ON, "hitAt": (hit or {}).get("at"),
                              "todayPnl": paper_control.day_pnl(rows, end_s)},
            }
    running = plan_running(root)
    return {
        "available": bool(accounts), "days": days, "asOf": datetime.now(ET).isoformat(timespec="seconds"),
        "limits": dict(LIMITS), "smallSample": SMALL_SAMPLE,
        "startDay": start_day, "strategy": EVALUATION_LABEL,
        # Which plan the RUNNING paper bots follow (plan_running): "chart" only once they have
        # restarted on the chart plan. Until then the page says the old plan is still trading
        # and names it (oldStrategy); orders are split by the marker on each ORDER, never by
        # date, so an old-plan order of this morning is never in the chart plan's evaluation.
        "plan": running["plan"] if running else "old", "planSince": running["since"] if running else None,
        "oldStrategy": OLD_PLAN_LABEL,
        "versus": _versus(accounts),
        # What the AI learned from the losing real fills (momx/loss_lessons.py). None = not studied yet.
        "lessons": loss_lessons.read(artifacts),
        # The paper bot's off switch (momx/paper_mirror.py): this file present = paused.
        "paused": (artifacts / "paper_mirror_off").exists(),
        "accounts": accounts,
        "basis": "Real Alpaca paper fills only. Orders that never filled earn nothing. "
                 "P/L lands on the day the trade closed.",
    }
