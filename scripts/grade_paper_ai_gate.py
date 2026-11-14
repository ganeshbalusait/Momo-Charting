"""Grade the paper AI gate (ChatGPT / OpenClaw / Ollama approve-or-skip) against real outcomes.

Read-only: it reads the gate's audit log (artifacts/momx_ai_filter/paper/YYYY-MM-DD.jsonl)
and the Bot's tracked trades (the same files and permanent dataset the AI filter trains on),
joins them on the AI filter's trade key, and prints:

* every Bot trade                         (the baseline)
* trades the AI filter sent to review     (score >= 0.55, i.e. "AI filter alone")
* LLM approved   vs   LLM skipped         (the question: does the LLM add anything?)
* reviews that never got an answer        (unavailable / expired)

A trade "wins" exactly as the AI filter labels it: Strategy exit at the mid above entry.
Trades with no priced exit yet are left out, never guessed. Nothing is written anywhere.

    python scripts/grade_paper_ai_gate.py                 # all days
    python scripts/grade_paper_ai_gate.py --since 2026-10-20
    python scripts/grade_paper_ai_gate.py --json          # machine-readable
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable, Mapping

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from momx import ai_filter as ai  # noqa: E402


def load_reviews(paper_dir: Path, since: str | None = None) -> dict[str, dict]:
    """{trade key: its LAST recorded review}. A key reviewed twice (approval expired,
    re-asked) is graded on the final answer, so no trade is counted twice."""
    out: dict[str, dict] = {}
    for f in sorted(Path(paper_dir).glob("20*.jsonl")):
        if since and f.stem < since:
            continue
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                row = json.loads(line)
            except ValueError:          # a torn last line is skipped, never fatal
                continue
            if isinstance(row, dict) and isinstance(row.get("key"), str):
                out[row["key"]] = row
    return out


def outcome(t: Mapping) -> tuple[int, float] | None:
    """(win, return per $1) for a priced trade; None while it has no result."""
    win = t.get("_win")
    if win is None:
        win = ai.label(t)
    if win is None:
        return None
    e, x = ai._num(t.get("entry")), ai._num(t.get("exitPrice"))
    if not e or e <= 0 or x is None:
        return None
    return int(bool(win)), x / e - 1.0


def summarize(results: Iterable[tuple[int, float]]) -> dict:
    rows = list(results)
    n = len(rows)
    if not n:
        return {"trades": 0, "winPct": None, "winLow": None, "winHigh": None, "avgReturnPct": None}
    wins = sum(w for w, _ in rows)
    # Wilson 95% interval: how sure the win rate is, given how few trades there are.
    z, p = 1.96, wins / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return {"trades": n, "winPct": round(p * 100, 1),
            "winLow": round((centre - half) * 100, 1), "winHigh": round((centre + half) * 100, 1),
            "avgReturnPct": round(sum(r for _, r in rows) / n * 100, 2)}


def difference(a: Mapping, b: Mapping) -> dict | None:
    """Approved minus skipped win rate, with a two-proportion z test."""
    na, nb = a["trades"], b["trades"]
    if not na or not nb:
        return None
    pa, pb = a["winPct"] / 100, b["winPct"] / 100
    pooled = (pa * na + pb * nb) / (na + nb)
    se = math.sqrt(pooled * (1 - pooled) * (1 / na + 1 / nb))
    zscore = (pa - pb) / se if se else 0.0
    pvalue = math.erfc(abs(zscore) / math.sqrt(2))
    return {"winPctGap": round((pa - pb) * 100, 1), "z": round(zscore, 2), "p": round(pvalue, 3),
            "real": pvalue < 0.05 and min(na, nb) >= 30}


def grade(days: Mapping[str, list[tuple[str, dict]]], reviews: Mapping[str, dict],
          since: str | None = None) -> dict:
    buckets: dict[str, list[tuple[int, float]]] = {k: [] for k in
                                                   ("all", "reviewed", "approved", "skipped", "noAnswer")}
    by_provider: dict[str, dict[str, list]] = {}
    seen: set[str] = set()
    for day, items in days.items():
        if since and day < since:
            continue
        for side, t in items:
            key = ai.trade_key(side, t)
            if key in seen:             # the same signal twice is one trade
                continue
            seen.add(key)
            res = outcome(t)
            if res is None:
                continue
            buckets["all"].append(res)
            r = reviews.get(key)
            if r is None:
                continue
            buckets["reviewed"].append(res)
            provider = str(r.get("provider") or "none")
            if provider in ("unavailable", "none"):
                buckets["noAnswer"].append(res)
                continue
            verdict = "approved" if r.get("approved") is True else "skipped"
            buckets[verdict].append(res)
            by_provider.setdefault(provider, {"approved": [], "skipped": []})[verdict].append(res)
    unmatched = sum(1 for k in reviews if k not in seen)
    out = {name: summarize(rows) for name, rows in buckets.items()}
    return {"since": since, "groups": out,
            "llmEdge": difference(out["approved"], out["skipped"]),
            "byProvider": {p: {"approved": summarize(v["approved"]), "skipped": summarize(v["skipped"])}
                           for p, v in sorted(by_provider.items())},
            "reviewsWithoutTrade": unmatched}


def verdict_text(report: Mapping) -> str:
    g, edge = report["groups"], report["llmEdge"]
    if not g["reviewed"]["trades"]:
        return "No reviewed trade has a result yet - nothing to grade."
    if edge is None:
        return "Only one side (approved or skipped) has results - cannot compare yet."
    small = min(g["approved"]["trades"], g["skipped"]["trades"]) < 30
    if small:
        return (f"Too few trades to tell (need 30+ on each side). Gap so far: "
                f"{edge['winPctGap']:+.1f} win-% points, p={edge['p']}.")
    if edge["real"] and edge["winPctGap"] > 0:
        return f"The LLM adds a real edge: approved trades win {edge['winPctGap']:+.1f} points more (p={edge['p']})."
    if edge["real"]:
        return f"The LLM HURTS: approved trades win {edge['winPctGap']:+.1f} points less (p={edge['p']})."
    return (f"No proven LLM edge: gap {edge['winPctGap']:+.1f} win-% points is within luck (p={edge['p']}). "
            "The AI filter score alone does the work.")


def _row(name: str, s: Mapping) -> str:
    if not s["trades"]:
        return f"  {name:<34} {0:>6}        -                  -"
    return (f"  {name:<34} {s['trades']:>6}   {s['winPct']:>5.1f}%  ({s['winLow']:>4.1f}-{s['winHigh']:>4.1f})"
            f"   {s['avgReturnPct']:>+7.2f}%")


def render(report: Mapping) -> str:
    g = report["groups"]
    lines = [f"Paper AI gate - graded on real outcomes{' since ' + report['since'] if report['since'] else ''}",
             "", f"  {'Group':<34} {'Trades':>6}   {'Win %':>6}  (95% range)   Avg return", "  " + "-" * 78,
             _row("Every Bot trade (baseline)", g["all"]),
             _row("AI filter >= 0.55 (sent to LLM)", g["reviewed"]),
             _row("  LLM approved", g["approved"]),
             _row("  LLM skipped", g["skipped"]),
             _row("  LLM never answered", g["noAnswer"]), ""]
    for provider, s in report["byProvider"].items():
        lines += [_row(f"{provider}: approved", s["approved"]), _row(f"{provider}: skipped", s["skipped"])]
    if report["byProvider"]:
        lines.append("")
    if report["reviewsWithoutTrade"]:
        lines.append(f"  ({report['reviewsWithoutTrade']} reviews had no matching Bot trade - pruned or still open.)")
    lines.append("Verdict: " + verdict_text(report))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--since", help="first day to grade, YYYY-MM-DD")
    ap.add_argument("--json", action="store_true", help="print JSON instead of a table")
    args = ap.parse_args(argv)
    directory = ai._dir()
    days = ai.load_days(ai._roots(), first=args.since, dataset=directory / "dataset")
    report = grade(days, load_reviews(directory / "paper", args.since), args.since)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
