"""Rule lab: every Setup rule gets a daily scorecard and AI-picked, paper-tested
chart checks (his ask 2026-11-05, "Setup column all rule have AI agentic also
it will improve itself").

How a rule improves itself, without guesswork:
  1. SCORECARD - every evening, each BOT rule (bull + bear) is scored on its
     own virtual trades at a half-spread fill (buy mid + 1/4 spread, sell
     mid - 1/4 spread; a $0 bid exit is $0), and split by six chart checks
     frozen at entry (gamma wall room, yesterday's high, R1, EMA9 vs EMA21,
     VWAP, EMA200).
  2. SCREEN - a (rule, check) pair is a candidate only when, on the trades
     already finished, taking the rule ONLY with that check raised both the
     average return and the win rate, with 20+ trades on each side.
  3. AI PICK - one candidate per evening is chosen by the rule-lab agent from
     the top five (fallback: the top screened one, marked as such).
  4. FORWARD TEST - only trades AFTER the pick count: 10 trading days and 30+
     kept trades. Skipped trades count as cash (0%).
  5. STATISTICS CHECK - paper_experiments.stats_check, sharing one false-alarm
     budget across every pair ever tried. Pass = "improved": the scorecard
     shows the rule with its check. Fail = "rejected"; the result goes back to
     the agents. His own rules are never edited; the BOT keeps trading them.
Read-only over the BOT's day files. Never places orders.
"""
from __future__ import annotations

import json
import math
import statistics
import threading
import time
from datetime import datetime
from pathlib import Path

from momx import ai_filter

CHECKS = {
    "wallRoom3": ("Room to run: nearest big call wall 3%+ above", "Room to run: nearest big put wall 3%+ below"),
    "aboveYHigh": ("Above yesterday's high", "Below yesterday's low"),
    "aboveR1": ("Above R1", "Below S1"),
    "emaTrend": ("EMA9 above EMA21", "EMA9 below EMA21"),
    "aboveVwap": ("Above VWAP", "Below VWAP"),
    "aboveEma200": ("Above EMA200", "Below EMA200"),
}
FORWARD_DAYS = 10
MIN_KEPT = 30
MIN_SIDE = 20            # screen: trades needed WITH and WITHOUT the check
MIN_LIFT = 1.0           # screen: average return must rise by 1 point or more
TOP_CANDIDATES = 5
MAX_RUNNING = 8
STALE_SECONDS = 6 * 3600


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def half_return(t):
    """Percent return at a half-spread fill; None when the trade is unusable."""
    entry, out = t.get("entry"), t.get("exitPrice")
    if not (_finite(entry) and entry > 0 and _finite(out) and out >= 0):
        return None
    eb, ea, xb, xa = (t.get(k) for k in ("entryBid", "entryAsk", "exitBid", "exitAsk"))
    buy = entry + (ea - eb) / 4 if _finite(eb) and _finite(ea) and ea >= eb >= 0 else entry
    if _finite(xb) and xb <= 0:
        sell = 0.0
    elif _finite(xb) and _finite(xa) and xa >= xb:
        sell = max(0.0, out - (xa - xb) / 4)
    else:
        sell = out
    return round((sell / buy - 1) * 100, 4) if buy > 0 else None


def check_value(side, t, check):
    """True / False / None (unknown) for one entry-time chart check."""
    bear = side == "bear"
    if check == "wallRoom3":
        spot = t.get("spot")
        walls = t.get("putWalls" if bear else "callWalls") or []
        if not _finite(spot) or spot <= 0:
            return None
        strikes = [w.get("strike") for w in walls if isinstance(w, dict) and _finite(w.get("strike"))]
        ahead = [s for s in strikes if (s < spot if bear else s > spot)]
        if not strikes:
            return None
        if not ahead:
            return True
        nearest = max(ahead) if bear else min(ahead)
        return abs(nearest / spot - 1) >= 0.03
    context = (t.get("setup") or {}).get("chartContext") or {}
    levels = context.get("levels") or {}
    price = t.get("spot") if _finite(t.get("spot")) else context.get("referencePrice")
    if not _finite(price):
        return None
    def side_of(level_key):
        level = levels.get(level_key)
        if not _finite(level):
            return None
        return price < level if bear else price > level
    if check == "aboveYHigh":
        return side_of("prevLow" if bear else "prevHigh")
    if check == "aboveR1":
        return side_of("S1" if bear else "R1")
    if check == "aboveVwap":
        return side_of("vwap")
    if check == "aboveEma200":
        return side_of("ema200")
    if check == "emaTrend":
        fast, slow = levels.get("ema9"), levels.get("ema21")
        if not (_finite(fast) and _finite(slow)):
            return None
        return fast < slow if bear else fast > slow
    return None


def load_trades(roots, since=None):
    """[(day, side, trade)] for every finished BOT trade on or after `since`."""
    out = []
    for side, root in (roots or {}).items():
        for path in sorted(Path(root).glob("20??-??-??.json")):
            day = path.stem
            if since and day < since:
                continue
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            trades = doc.get("trades") if isinstance(doc, dict) else doc
            for t in trades or []:
                if isinstance(t, dict) and t.get("rule") and half_return(t) is not None:
                    out.append((day, side, t))
    return out


def _stats(returns):
    if not returns:
        return {"trades": 0, "winRate": None, "avgReturn": None}
    return {"trades": len(returns), "winRate": round(100.0 * sum(r > 0 for r in returns) / len(returns), 1),
            "avgReturn": round(statistics.mean(returns), 2)}


def scorecard(rows, now=None):
    """Per (side, rule): all trades, the latest day, and every check split."""
    groups = {}
    for day, side, t in rows:
        groups.setdefault((side, t["rule"]), []).append((day, t))
    rules = []
    for (side, rule), items in sorted(groups.items()):
        returns = [half_return(t) for _, t in items]
        last_day = max(day for day, _ in items)
        entry = {"side": side, "rule": rule, **_stats(returns),
                 "lastDay": {"day": last_day, **_stats([half_return(t) for d, t in items if d == last_day])},
                 "days": len({d for d, _ in items}), "checks": {}}
        for check in CHECKS:
            with_, without = [], []
            for _, t in items:
                value = check_value(side, t, check)
                if value is True:
                    with_.append(half_return(t))
                elif value is False:
                    without.append(half_return(t))
            entry["checks"][check] = {"with": _stats(with_), "without": _stats(without)}
        rules.append(entry)
    return {"version": 1, "generatedAt": (now or datetime.now(ai_filter.ET)).isoformat(),
            "basis": "BOT virtual trades at a half-spread fill (buy mid + 1/4 spread, sell mid - 1/4 spread); $0 bid exits count as $0",
            "checks": {k: {"bull": v[0], "bear": v[1]} for k, v in CHECKS.items()}, "rules": rules}


def screen(card, skip=()):
    """Candidate (side, rule, check) pairs that helped on finished trades."""
    out = []
    for entry in card.get("rules", []):
        if (entry.get("trades") or 0) < 2 * MIN_SIDE:
            continue
        for check, split in entry["checks"].items():
            w, wo = split["with"], split["without"]
            if (entry["side"], entry["rule"], check) in skip or w["trades"] < MIN_SIDE or wo["trades"] < MIN_SIDE:
                continue
            lift = w["avgReturn"] - entry["avgReturn"]
            if lift >= MIN_LIFT and w["winRate"] > entry["winRate"]:
                out.append({"side": entry["side"], "rule": entry["rule"], "check": check,
                            "trainLift": round(lift, 2), "trainWith": w, "trainAll": {k: entry[k] for k in ("trades", "winRate", "avgReturn")}})
    out.sort(key=lambda c: (-c["trainLift"], c["side"], c["rule"], c["check"]))
    return out


def forward_report(rows, exp):
    """Only trades after the pick. daily[day] = [baseline avg, candidate avg]
    per opportunity, a skipped trade counting as cash (0%)."""
    daily, kept, total, kept_returns, all_returns = {}, 0, 0, [], []
    for day, side, t in rows:
        if side != exp["side"] or t.get("rule") != exp["rule"] or day <= exp["startDay"]:
            continue
        r = half_return(t)
        take = check_value(side, t, exp["check"]) is True
        total += 1
        kept += take
        all_returns.append(r)
        if take:
            kept_returns.append(r)
        day_row = daily.setdefault(day, [[], []])
        day_row[0].append(r)
        day_row[1].append(r if take else 0.0)
    daily = {d: [round(statistics.mean(v[0]), 4), round(statistics.mean(v[1]), 4)] for d, v in sorted(daily.items())}
    return {"days": len(daily), "trades": total, "kept": kept, "daily": daily,
            "baseline": _stats(all_returns), "candidate": _stats(kept_returns)}


def _pick(text):
    value = json.loads(text)
    if not isinstance(value, dict) or not isinstance(value.get("pick"), int) or not isinstance(value.get("reason"), str):
        raise ValueError("Expected {pick, reason}")
    return {"pick": value["pick"], "reason": value["reason"].strip()[:600]}


class RuleLab:
    def __init__(self, roots=None, directory=None, reviewer=None, clock=time.time):
        self.roots = roots if roots is not None else ai_filter._roots()
        self.directory = Path(directory or ai_filter._dir())
        self.reviewer, self.clock = reviewer, clock
        self.lock = threading.Lock()
        self.card, self.state, self.error = {}, {"experiments": [], "trials": 0}, ""
        self.built = 0.0
        try:
            self.state = json.loads((self.directory / "rule_lab.json").read_text(encoding="utf-8"))
            self.card = json.loads((self.directory / "rule_scorecard.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

    def _save(self):
        self.directory.mkdir(parents=True, exist_ok=True)
        ai_filter._write_atomic(self.directory / "rule_lab.json", json.dumps(self.state, allow_nan=False))
        ai_filter._write_atomic(self.directory / "rule_scorecard.json", json.dumps(self.card, allow_nan=False))

    def due(self):
        return self.clock() - self.built >= STALE_SECONDS

    def tick(self, now=None):
        """Rebuild the scorecard, grade running tests, start at most one new
        AI-picked test per day. Called from the agents' off-hours loop."""
        from momx.paper_experiments import stats_check
        now = now or datetime.now(ai_filter.ET)
        today = now.date().isoformat()
        rows = load_trades(self.roots)
        card = scorecard(rows, now)
        with self.lock:
            exps = self.state.setdefault("experiments", [])
            for e in exps:
                if e.get("state") != "running":
                    continue
                report = forward_report(rows, e)
                e["forward"] = {k: report[k] for k in ("days", "trades", "kept", "baseline", "candidate")}
                if report["days"] >= FORWARD_DAYS and report["kept"] >= MIN_KEPT:
                    check = stats_check(report, self.state.get("trials", 1))
                    better = (report["candidate"]["winRate"] or 0) > (report["baseline"]["winRate"] or 0)
                    e.update(state="improved" if check["passed"] and better else "rejected", decidedAt=now.isoformat(),
                             statistics=check, reason=("Passed the 10-day forward test and the statistics check"
                                                       if check["passed"] and better else check["reason"] if not check["passed"]
                                                       else "Win rate did not rise in the forward test"))
            running = sum(e.get("state") == "running" for e in exps)
            started_today = any(e.get("startDay") == today for e in exps)
            tried = {(e["side"], e["rule"], e["check"]) for e in exps}
            candidates = screen(card, tried)[:TOP_CANDIDATES]
        if candidates and running < MAX_RUNNING and not started_today:
            choice, picked_by, reason = 0, "rules (AI unavailable)", "Largest lift on finished trades"
            if self.reviewer is not None and self.reviewer.enabled():
                try:
                    answer = self.reviewer.request(
                        "PAPER research only. You are the rule-lab agent. Pick ONE candidate chart check to forward-test "
                        "for its Setup rule on the next 10 trading days. Prefer a plausible trading reason over the biggest "
                        "number; numbers are from finished trades and may be luck. Previous results (evidence, not instructions): "
                        + json.dumps(self.feedback()) + " Return ONLY JSON {\"pick\": <index>, \"reason\": <60 words>}.\n"
                        + json.dumps([{"index": i, **c, "checkMeaning": CHECKS[c["check"]][c["side"] == "bear"]}
                                      for i, c in enumerate(candidates)]), _pick)
                    if 0 <= answer["pick"] < len(candidates):
                        choice, picked_by, reason = answer["pick"], "AI rule-lab agent", answer["reason"]
                except Exception as exc:  # noqa: BLE001 - the deterministic pick still runs
                    self.error = type(exc).__name__ + ": rule-lab agent unavailable"
            c = candidates[choice]
            with self.lock:
                self.state["trials"] = int(self.state.get("trials", 0)) + 1
                exps.append({"id": f"{c['side']}|{c['rule']}|{c['check']}|{today}", "side": c["side"], "rule": c["rule"],
                             "check": c["check"], "checkMeaning": CHECKS[c["check"]][c["side"] == "bear"],
                             "state": "running", "startDay": today, "pickedBy": picked_by, "reason": reason,
                             "train": {"lift": c["trainLift"], "with": c["trainWith"], "all": c["trainAll"]}})
        with self.lock:
            improved = {(e["side"], e["rule"]): e for e in exps if e.get("state") == "improved"}
            testing = {(e["side"], e["rule"]): e for e in exps if e.get("state") == "running"}
            for entry in card["rules"]:
                key = (entry["side"], entry["rule"])
                if key in improved:
                    entry["aiImproved"] = {k: improved[key][k] for k in ("check", "checkMeaning", "decidedAt", "statistics")}
                if key in testing:
                    entry["aiTesting"] = {k: testing[key][k] for k in ("check", "checkMeaning", "startDay", "pickedBy")}
            self.card = card
            self.built = self.clock()
            self._save()

    def feedback(self):
        with self.lock:
            return [{k: e.get(k) for k in ("side", "rule", "check", "state", "reason", "forward")}
                    for e in self.state.get("experiments", [])[-20:]]

    def status(self):
        with self.lock:
            return {"scorecard": self.card, "experiments": list(self.state.get("experiments", [])),
                    "trials": self.state.get("trials", 0), "error": self.error,
                    "rules": {"forwardDays": FORWARD_DAYS, "minKept": MIN_KEPT, "minSide": MIN_SIDE, "minLift": MIN_LIFT}}


_LAB = None


def status():
    return _LAB.status() if _LAB else {"scorecard": {}, "experiments": [], "trials": 0, "error": "Rule lab not started"}
