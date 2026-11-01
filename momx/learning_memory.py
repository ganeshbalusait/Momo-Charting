"""Deterministic loss-pattern memory. Observations are research, never entry vetoes."""
from collections import defaultdict
import hashlib
import json

from momx.paper_experiments import FEE_PER_CONTRACT, finite, when


def summarize(rows):
    """Deduplicate views, separate execution bases, and retain winning counterexamples.

    A recurring loss needs negative results in both chronological halves of at
    least four entry days. This is a descriptive screen, not a significance test.
    """
    observations = {}
    invalid = 0
    for r in rows:
        key = r.get("key")
        at = when(r.get("entryAt") or r.get("signalAt"))
        if not isinstance(key, str) or not at or r.get("side") not in ("bull", "bear") or not r.get("rule"):
            invalid += 1
            continue
        scope = key.split("|", 1)[0]
        if scope in ("paper3", "paper5"):
            sells = r.get("sells") or []
            buy = r.get("entryFill")
            if not finite(buy) or buy <= 0 or not sells or any(
                    not finite(s.get("qty")) or s["qty"] <= 0 or not finite(s.get("price")) or s["price"] < 0 for s in sells):
                invalid += 1
                continue
            qty = sum(s["qty"] for s in sells)
            if r.get("filledQty") is not None and r["filledQty"] != qty:
                invalid += 1
                continue
            gross = sum((s["price"]-buy)*s["qty"]*100 for s in sells)
        elif scope in ("bull", "bear") and r.get("basis", "").startswith("virtual strategy exit"):
            scope, qty = "virtual", 1
            buy, out = r.get("entryMid"), r.get("exitMid")
            if not finite(buy) or buy <= 0 or not finite(out) or out < 0:
                invalid += 1
                continue
            gross = (out-buy)*100
        else:
            invalid += 1
            continue
        observations[key] = {"scope": scope, "side": r["side"], "rule": str(r["rule"])[:80],
                             "day": at.date().isoformat(), "net": gross-FEE_PER_CONTRACT*qty}
    groups = defaultdict(list)
    scopes = {}
    for scope in ("virtual", "paper3", "paper5"):
        data = [r for r in observations.values() if r["scope"] == scope]
        days = sorted({r["day"] for r in data})[-20:]
        data = [r for r in data if r["day"] in days]
        scopes[scope] = {"trades": len(data), "days": len(days), "throughDay": days[-1] if days else None}
        for r in data:
            groups[scope, r["side"], r["rule"]].append(r)
    patterns = []
    for (scope, side, rule), data in sorted(groups.items()):
        days = sorted({r["day"] for r in data})
        earlier = set(days[:len(days)//2])
        blocks = [sum(r["net"] for r in data if (r["day"] in earlier) == old) for old in (True, False)]
        losses = sum(r["net"] < 0 for r in data)
        recurring = len(data) >= 6 and len(days) >= 4 and losses >= 3 and all(n < 0 for n in blocks)
        if recurring:
            patterns.append({"scope": scope, "side": side, "rule": rule, "trades": len(data), "days": len(days),
                             "wins": sum(r["net"] > 0 for r in data), "losses": losses,
                             "net": round(sum(blocks), 2), "earlierNet": round(blocks[0], 2), "laterNet": round(blocks[1], 2),
                             "state": "research_needed"})
    # Each basis gets its own list and priority; paper dollars and virtual one-contract dollars never compete.
    patterns = [p for scope in scopes for p in sorted(
        (p for p in patterns if p["scope"] == scope), key=lambda p: (p["net"], p["side"], p["rule"]))[:4]]
    report = {"version": 1, "scopes": scopes, "patterns": patterns, "excludedRows": invalid,
              "capturedTrades": len(observations), "windowEntryDays": 20,
              "feePerContract": FEE_PER_CONTRACT, "entryBlocking": False,
              "basis": "Virtual: one-contract strategy midpoint estimates. Paper accounts: recorded fill quantities. Fees excluded; no hindsight best prices.",
              "meaning": "Recurring losses are research hypotheses, not proven mistakes or permission to skip trades. Winners remain in every comparison."}
    report["fingerprint"] = hashlib.sha256(json.dumps(report, sort_keys=True).encode()).hexdigest()[:20]
    return report


def context(memory):
    """Bound provider context while representing each execution basis separately."""
    result = {k: memory[k] for k in ("fingerprint", "scopes", "basis", "meaning") if k in memory}
    result["patterns"] = [p for scope in ("virtual", "paper3", "paper5")
                          for p in [p for p in memory.get("patterns", []) if p["scope"] == scope][:2]]
    return result
