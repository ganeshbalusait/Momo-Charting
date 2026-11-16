"""Fill likelihood for a paper buy at the mid, shown like Robinhood's order screen (2026-11-10).

His ask, with a Robinhood screenshot: "See robinhood option how it filling the price same we need
for our bot", then "yes same like robinhood" - the bot keeps buying at the MID (his "Keep the mid"
the same day) and each order gets a Low / Medium / High fill-likelihood label. The label informs;
it never changes the price or stops a buy.

Robinhood's label comes from its own model (fill within 3 seconds). Ours starts as a rule on the
quote's spread - the one thing in our records that clearly tracked fills (545 past buys: under 3%
spread, 71% filled at the mid; most wide-spread orders never filled) - and the Scorecard measures
how each label really did (filled at the mid inside the bot's 2-minute wait), so the rule can be
replaced by measured rates once there are enough orders.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping

#: Spread as a share of the mid. Under HIGH_BELOW -> High; under LOW_FROM -> Medium; else Low.
HIGH_BELOW = 0.05
LOW_FROM = 0.15
LABELS = ("high", "medium", "low")
#: A label's measured rate is called reliable from this many finished orders.
MIN_MEASURED = 20
#: Order states that are not finished yet: their outcome is unknown.
_UNFINISHED = ("buying",)


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def spread_share(bid: Any, ask: Any) -> float | None:
    """(ask - bid) / mid, or None for an unusable quote."""
    b, a = _num(bid), _num(ask)
    if b is None or a is None or a <= 0 or b < 0 or a < b:
        return None
    return (a - b) / ((a + b) / 2)


def label(share: Any) -> str | None:
    s = _num(share)
    if s is None or s < 0:
        return None
    return "high" if s < HIGH_BELOW else "medium" if s < LOW_FROM else "low"


def stamp(bid: Any, ask: Any) -> dict | None:
    """What goes on a new order: the label, the spread it rests on, and how it was decided."""
    share = spread_share(bid, ask)
    found = label(share)
    if found is None:
        return None
    return {"label": found, "spreadPct": round(share * 100, 1), "by": "spread"}


def label_of(row: Mapping) -> str | None:
    """An order's label: the one stamped when it was sent, else worked out from the spread saved on it
    (orders sent before the labels existed but after the spread was recorded)."""
    chance = row.get("fillChance")
    if isinstance(chance, Mapping) and chance.get("label") in LABELS:
        return chance["label"]
    return label(row.get("spreadAtOrder"))


def measured(rows: Iterable[Mapping]) -> dict:
    """How each label really did: finished orders, filled at the mid on the first try, filled at all."""
    out = {name: {"orders": 0, "filledAtMid": 0, "filled": 0} for name in LABELS}
    for r in rows:
        if r.get("status") in _UNFINISHED or r.get("status") == "skipped" or not r.get("buyId"):
            continue
        note = str(r.get("note") or "")
        never_there = note.startswith("The broker refused") or "never reached the broker" in note or note.startswith("Removed by you")
        if never_there and int(r.get("buyTries") or 0) == 0:
            continue                                  # the mid order itself never reached the market: says nothing about fills
        name = label_of(r)
        if name is None:
            continue
        got = _num(r.get("buyFill")) is not None
        out[name]["orders"] += 1
        out[name]["filled"] += int(got)
        out[name]["filledAtMid"] += int(got and int(r.get("buyTries") or 0) == 0)
    for s in out.values():
        n = s["orders"]
        s["midPct"] = round(s["filledAtMid"] / n * 100) if n else None
        s["filledPct"] = round(s["filled"] / n * 100) if n else None
        s["enough"] = n >= MIN_MEASURED
    return out
