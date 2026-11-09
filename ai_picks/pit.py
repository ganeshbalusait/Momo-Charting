"""Point-in-time company finances: what was public on a given day.

A company's numbers are turned into one "snapshot" per filing day. Each snapshot
uses only facts filed on or before that day, so a back-test that picks the latest
snapshot before its rebalance date never sees the future.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable

FLOW = ("revenue", "op_income", "dna", "net_income", "eps", "cfo", "capex")
INSTANT = ("debt_total", "debt_noncurrent", "debt_current", "short_borrow", "cash", "shares",
           "equity", "assets_current", "liab_current", "ppe")


def annual_history(facts: dict[tuple, tuple], years: int = 6) -> list[tuple[date, float]]:
    """Full-year values, newest first, one per fiscal year."""
    annual = sorted((f for f in facts.values() if f[1] is not None and 350 <= (f[2] - f[1]).days <= 380),
                    key=lambda f: f[2], reverse=True)
    out: list[tuple[date, float]] = []
    for f in annual:
        if not out or (out[-1][0] - f[2]).days >= 300:
            out.append((f[2], f[3]))
        if len(out) >= years:
            break
    return out


def value_near(facts: dict[tuple, tuple], end: date, days: int = 20) -> float | None:
    hits = [f for f in facts.values() if f[1] is None and _near(f[2], end, days)]
    return min(hits, key=lambda f: (abs((f[2] - end).days), f[0]))[3] if hits else None
STALE_DAYS = 270  # a TTM whose period ended more than this long before the filing is too old


# A fact is (rank, start, end, val, filed); start is None for balance-sheet items.


def _dedupe(facts: Iterable[tuple]) -> dict[tuple, tuple]:
    """Keep one value per (start, end): best tag rank, then the latest filing."""
    best: dict[tuple, tuple] = {}
    for rank, start, end, val, filed in facts:
        key = (start, end)
        cur = best.get(key)
        if cur is None or (rank, -filed.toordinal()) < (cur[0], -cur[4].toordinal()):
            best[key] = (rank, start, end, val, filed)
    return best


def _near(a: date, b: date, days: int) -> bool:
    return abs((a - b).days) <= days


def ttm_at(facts: dict[tuple, tuple], end: date) -> float | None:
    """Trailing-twelve-month value for the period ending ``end``.

    Uses an annual figure if one ends there, else annual + year-to-date − prior year-to-date.
    """
    annuals, partials = [], []
    for f in facts.values():
        if f[1] is None:
            continue
        span = (f[2] - f[1]).days
        if 350 <= span <= 380:
            annuals.append(f)
        elif 80 <= span <= 300:
            partials.append((span, f))
    for f in annuals:
        if _near(f[2], end, 7):
            return f[3]
    here = sorted(((s, f) for s, f in partials if _near(f[2], end, 7)), key=lambda x: -x[0])
    for span, f in here:
        annual = next((g[3] for g in annuals if _near(g[2], f[1] - timedelta(days=1), 20)), None)
        if annual is None:
            continue
        target = f[2] - timedelta(days=365)
        prior = next((g[3] for s, g in partials if abs(s - span) <= 15 and _near(g[2], target, 12)), None)
        if prior is not None:
            return annual + f[3] - prior
    return None


def latest_end(facts: dict[tuple, tuple]) -> date | None:
    ends = [f[2] for f in facts.values() if f[1] is not None]
    return max(ends) if ends else None


def snapshot(concepts: dict[str, list[tuple]], asof: date) -> dict | None:
    """All numbers known on ``asof`` for one company, or None if nothing current."""
    known = {c: _dedupe(f for f in rows if f[4] <= asof) for c, rows in concepts.items()}
    rev = known.get("revenue") or {}
    end = latest_end(rev)
    if end is None or (asof - end).days > STALE_DAYS:
        return None
    out: dict = {"period_end": end}
    for c in FLOW:
        facts = known.get(c) or {}
        out[c] = ttm_at(facts, end) if facts else None
        out[c + "_prev"] = ttm_at(facts, end - timedelta(days=365)) if facts else None
        if out[c + "_prev"] is None and facts:
            # 52/53-week years: the prior period may end a few days off a calendar year
            for e in sorted({f[2] for f in facts.values()}):
                if _near(e, end - timedelta(days=365), 12):
                    out[c + "_prev"] = ttm_at(facts, e)
                    break
    for c in INSTANT:
        facts = [f for f in (known.get(c) or {}).values() if f[1] is None or c == "shares"]
        if facts:
            f = max(facts, key=lambda x: (x[2], -x[0], x[4]))
            if (asof - f[2]).days <= 400:
                out[c] = f[3]
                if c == "shares":
                    out["shares_date"] = f[2]
    ni_years = annual_history(known.get("net_income") or {})
    out["ni_years"] = [v for _, v in ni_years]
    equity_facts = known.get("equity") or {}
    roes = []
    for end_y, ni in ni_years[:5]:
        eq = value_near(equity_facts, end_y)
        if eq and eq > 0:
            roes.append(ni / eq)
    out["roe_5y"] = sum(roes) / len(roes) if len(roes) >= 3 else None
    if "shares" not in out:
        wavg = [f for f in (known.get("shares_wavg") or {}).values() if f[3] > 0]
        if wavg:
            f = max(wavg, key=lambda x: (x[2], -x[0], x[4]))
            if (asof - f[2]).days <= 400:
                out["shares"], out["shares_date"] = f[3], f[2]
    debt = out.get("debt_total")
    if debt is None and (out.get("debt_noncurrent") is not None or out.get("debt_current") is not None):
        debt = (out.get("debt_noncurrent") or 0.0) + (out.get("debt_current") or 0.0)
    out["debt"] = (debt or 0.0) + (out.get("short_borrow") or 0.0)
    return out


def company_snapshots(concepts: dict[str, list[tuple]]) -> list[tuple[date, dict]]:
    """One snapshot per distinct filing day, oldest first."""
    days = sorted({f[4] for rows in concepts.values() for f in rows})
    out = []
    for d in days:
        snap = snapshot(concepts, d)
        if snap is not None:
            out.append((d, snap))
    return out
