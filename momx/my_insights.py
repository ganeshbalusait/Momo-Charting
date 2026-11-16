"""My Trading (2026-11-11, owner only): the owner's REAL Schwab option trades turned into plain numbers.

The fills come from momx/schwab_history.py (read once a day after the close, read-only). Everything
here works on saved files only: it never calls Schwab, never places, changes or cancels an order.
Lines DESCRIBE what happened; nothing here tells the trader what to do.

GET /api/my-insights?days=7|30|90 (owner only) replies with build(days):

{
  "available": bool,                    # at least one stored fill
  "days": 7 | 30 | 90,
  "period": {"days", "from": "YYYY-MM-DD", "to": "YYYY-MM-DD", "label": "last 30 days (10-13 to 11-11)"},
  "asOf": ET ISO,
  "basis": str,                          # how trades are counted (FIFO, P/L on the closing day, after fees)
  "summary": Stats + {"medianHoldMinutes", "fees", "openPositions",
                      "unscored" (exercised/assigned trades whose value Schwab's row lacks: listed, left out of every number),
                      "closedBeforeWindow" (closing orders whose opening is older than the read window: not trades)},
  "breakdowns": {                        # each a list of Stats rows with "key" and "label", in display order
      "setup", "timeOfDay", "dte", "delta", "callPut", "weekday", "ticker"},
  "habits": [{"key": "revenge"|"overtrading"|"sizeUpAfterLoss"|"heldPast50"|"cutWinnersEarly",
              "label", "what", "count", "cost" ($ >= 0: money lost / left on those trades), "pnl",
              "approx": bool, "note", "trades": [TradeRow + {"note"}]}],
  "holdTime": {"all", "wins", "losses"}   # median minutes, or None
  "execution": {"fills", "trades" (= fills priced), "unpriced", "approxCount", "spreadCost" ($, + = paid above mid),
                "avgSpreadDollars", "avgSpreadPct" (fraction), "avgWidthPct" (fraction), "vsMid", "note",
                "rows": [{"id", "at", "contract", "side", "quantity", "price", "mid", "bid", "ask", "paidVsMid",
                          "gapMinutes", "approx"}]},
  "paperBots": [{"account", "label", "trades", "wins", "losses", "winRate", "avgWin", "avgLoss", "expectancy",
                 "profitFactor", "pnl", "enough", "note", "sameDays": Stats}],
  "events": {"items": [{"at", "date", "title", "kind": "economic"|"earnings", "impact", "symbol"}], "note", "refreshedAt", "source"},
  "schwab": schwab_history.status(),     # state: ok | never | reading | signin_needed | stopped; lastReadAt; nextManualAt; canRefresh
  "trades": [TradeRow],                  # the period's closed trades, newest first (at most 200)
  "openPositions": [{"optionSymbol", "underlying", "putCall", "strike", "expiry", "quantity", "price", "cost", "entryAt", "account"}]
}

Stats = {"trades", "wins", "losses", "flat", "winRate" (fraction 0-1), "avgWin", "avgLoss" (negative $),
         "expectancy" ($ per trade), "profitFactor" (number, "inf" when no loss, None when no trades), "pnl",
         "enough": trades >= 10, "note": "" | "too few trades to judge", "period": label}
TradeRow = {"id", "account", "underlying", "optionSymbol", "putCall", "strike", "expiry", "dte", "direction",
            "quantity", "entryAt", "entryPrice", "exitAt", "exitPrice", "fees", "cost", "pnl", "pnlPct",
            "holdMinutes", "expired", "setup": {...} | None}
All money in dollars after fees; times in ET.
"""
from __future__ import annotations

import gzip
import json
import math
from collections import defaultdict, deque
from datetime import date, datetime, timedelta
from datetime import time as dtime
from pathlib import Path
from statistics import median
from typing import Any, Callable, Iterable

from momx import schwab_history

ET = schwab_history.ET
PERIODS = (7, 30, 90)
TOO_FEW = 10
TOO_FEW_LINE = "too few trades to judge"
REVENGE_MINUTES = 15
OVERTRADE_FACTOR = 1.5
OVERTRADE_LOOKBACK = 20
OVERTRADE_MIN_DAYS = 5
SIZE_UP_FACTOR = 1.5
APPROX_MINUTES = 5
#: A saved chain counts for a fill only from the same ET day and at most one save interval (30 min) + slack before it.
MAX_QUOTE_GAP_MINUTES = 35
#: Schwab's position-removal rows (RECEIVE_AND_DELIVER): no order id of their own, never open a lot.
EVENT_KINDS = ("expired", "exercised", "assigned")
MAX_TRADE_ROWS = 200
SESSION_END = 16 * 60 + 5
TIME_BUCKETS = (("09:30-10:00", 9 * 60 + 30, 10 * 60), ("10:00-11:00", 10 * 60, 11 * 60),
                ("11:00-14:00", 11 * 60, 14 * 60), ("14:00-16:00", 14 * 60, 16 * 60))
DTE_BUCKETS = (("0 DTE", 0, 0), ("1-2 DTE", 1, 2), ("3-7 DTE", 3, 7), ("8-30 DTE", 8, 30), ("30+ DTE", 31, 10 ** 6))
DELTA_BUCKETS = (("under 0.20", 0.0, 0.20), ("0.20-0.35", 0.20, 0.35), ("0.35-0.50", 0.35, 0.50), ("0.50+", 0.50, 10.0))
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
HABIT_TEXT = {
    "revenge": ("New trade soon after a loss", f"A new entry within {REVENGE_MINUTES} minutes of closing a losing trade."),
    "overtrading": ("Busier days than usual", f"More trades in a day than {OVERTRADE_FACTOR}x the median of the {OVERTRADE_LOOKBACK} trading days before."),
    "sizeUpAfterLoss": ("Bigger size after a loss", f"The next trade after a loss cost more than {SIZE_UP_FACTOR}x the losing one."),
    "heldPast50": ("Held past -50%", "The option was 50% or more below the entry price before it was sold."),
    "cutWinnersEarly": ("Sold before +100%", "Sold below +100% on a contract the saved chains show at +100% or more later that day."),
}


# ---------------------------------------------------------------- small helpers
def _num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _at(stamp) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(stamp))
    except (TypeError, ValueError):
        return None
    return (moment if moment.tzinfo else moment.replace(tzinfo=ET)).astimezone(ET)


def _r(v, digits: int = 2):
    return round(v, digits) if _num(v) else None


def _key(symbol: str) -> str:
    return "".join(str(symbol or "").split()).upper()


# ---------------------------------------------------------------- fills -> orders -> round trips
def orders_from_fills(fills: Iterable[dict]) -> list[dict]:
    """Partial fills of one order (same account, order id, contract and side) become one order at the
    quantity-weighted price. Expiry / exercise / assignment rows stay their own order."""
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in fills:
        if not isinstance(f, dict) or not _num(f.get("quantity")) or f["quantity"] <= 0 or not _num(f.get("price")):
            continue
        order = f.get("orderId") if f.get("kind") not in EVENT_KINDS else None
        groups[(f.get("account"), order or f.get("id"), _key(f.get("optionSymbol")), f.get("side"))].append(f)
    out = []
    for parts in groups.values():
        parts.sort(key=lambda f: f.get("at") or "")
        qty = sum(f["quantity"] for f in parts)
        first = parts[0]
        out.append({**{k: first.get(k) for k in ("account", "underlying", "optionSymbol", "putCall", "strike", "expiry", "dte",
                                                  "side", "kind", "orderId", "assetType", "positionEffect")},
                    "valueKnown": all(f.get("valueKnown", True) is not False for f in parts),
                    "id": first["id"], "fillIds": [f["id"] for f in parts], "at": first.get("at"), "lastAt": parts[-1].get("at"),
                    "quantity": qty, "price": sum(f["price"] * f["quantity"] for f in parts) / qty,
                    "fees": sum(f.get("fees") or 0.0 for f in parts if _num(f.get("fees"))),
                    "multiplier": first.get("multiplier") if _num(first.get("multiplier")) else 100.0})
    out.sort(key=lambda o: (o.get("at") or "", o["id"]))
    return out


def pair_trades(fills: Iterable[dict], today: str | None = None, info: dict | None = None) -> tuple[list[dict], list[dict]]:
    """(closed round trips, open positions). FIFO per account and contract: an exit closes the oldest
    open lot first. A lot is one opening order; partial exits stay on that lot until it is fully closed
    (one trade per entry order). A lot still open after its expiry day is closed at $0 ("expired").

    Only an OPENING order (or a fill with no positionEffect) starts a lot. What a CLOSING order (or an
    expiry/exercise row) closes beyond the open lots closed a position opened before the read window:
    it is dropped, and `info["closedBeforeWindow"]` counts those orders. An exercise/assignment whose
    value Schwab's row does not show makes the trade "valueUnknown" (left out of the numbers)."""
    today = today or datetime.now(ET).date().isoformat()
    if info is not None:
        info.setdefault("closedBeforeWindow", 0)
    lots_by: dict[tuple, deque] = defaultdict(deque)
    trades: list[dict] = []

    def close(lot: dict) -> None:
        exits = lot["exits"]
        qty = sum(q for _, q, _, _ in exits)
        exit_price = sum(p * q for _, q, p, _ in exits) / qty if qty else 0.0
        mult, direction = lot["multiplier"], lot["direction"]
        fees = lot["fees"] + sum(f for _, _, _, f in exits)
        pnl = direction * (exit_price - lot["price"]) * qty * mult - fees
        cost = lot["price"] * qty * mult
        entry_at, exit_at = _at(lot["at"]), _at(exits[-1][0])
        trades.append({
            "id": f"{lot['account']}|{lot['id']}", "entryId": lot["id"], "account": lot["account"],
            "underlying": lot["underlying"], "optionSymbol": lot["optionSymbol"], "putCall": lot["putCall"],
            "strike": lot["strike"], "expiry": lot["expiry"], "dte": lot["dte"], "direction": "long" if direction > 0 else "short",
            "quantity": qty, "entryAt": lot["at"], "entryPrice": round(lot["price"], 4), "exitAt": exits[-1][0],
            "exitPrice": round(exit_price, 4), "fees": round(fees, 2), "cost": round(cost, 2), "pnl": round(pnl, 2),
            "pnlPct": round(pnl / cost, 4) if cost else None, "multiplier": mult,
            "holdMinutes": round((exit_at - entry_at).total_seconds() / 60, 1) if entry_at and exit_at else None,
            "expired": any(kind == "expired" for kind in lot["exitKinds"]), "exitFillIds": lot["exitFillIds"],
            "event": next((kind for kind in lot["exitKinds"] if kind in ("exercised", "assigned")), None),
            "valueUnknown": bool(lot.get("valueUnknown")),
            "entryFillIds": list(lot.get("fillIds") or [lot["id"]]),
        })

    for order in orders_from_fills(fills):
        key = (order["account"], _key(order["optionSymbol"]))
        lots = lots_by[key]
        direction = 1 if order["side"] == "BUY" else -1
        remaining = order["quantity"]
        while remaining > 1e-9 and lots and lots[0]["direction"] != direction:
            lot = lots[0]
            take = min(remaining, lot["open"])
            lot["exits"].append((order["at"], take, order["price"], order["fees"] * take / order["quantity"]))
            lot["exitKinds"].append(order.get("kind"))
            if order.get("valueKnown") is False:
                lot["valueUnknown"] = True
            lot["exitFillIds"].extend(order["fillIds"])
            lot["open"] -= take
            remaining -= take
            if lot["open"] <= 1e-9:
                close(lots.popleft())
        if remaining > 1e-9:
            if order.get("kind") in EVENT_KINDS or str(order.get("positionEffect") or "").upper() == "CLOSING":
                if info is not None:                    # it closed a position opened before the read window
                    info["closedBeforeWindow"] += 1
                continue
            share = remaining / order["quantity"]
            lots.append({**order, "direction": direction, "open": remaining, "qty0": remaining,
                         "fees": order["fees"] * share, "exits": [], "exitKinds": [], "exitFillIds": []})

    open_positions = []
    for lots in lots_by.values():
        for lot in lots:
            if lot["expiry"] and lot["expiry"] < today and lot.get("assetType", "OPTION") == "OPTION":
                stamp = datetime.combine(date.fromisoformat(lot["expiry"]), dtime(16, 0), ET)
                lot["exits"].append((stamp.isoformat(timespec="seconds"), lot["open"], 0.0, 0.0))
                lot["exitKinds"].append("expired")
                close(lot)
                trades[-1]["assumedExpired"] = True
                continue
            open_positions.append({"optionSymbol": lot["optionSymbol"], "underlying": lot["underlying"], "putCall": lot["putCall"],
                                   "strike": lot["strike"], "expiry": lot["expiry"], "quantity": lot["open"],
                                   "direction": "long" if lot["direction"] > 0 else "short",
                                   "price": round(lot["price"], 4), "cost": round(lot["price"] * lot["open"] * lot["multiplier"], 2),
                                   "entryAt": lot["at"], "account": lot["account"],
                                   "closedSoFar": round(lot["qty0"] - lot["open"], 4)})
    trades.sort(key=lambda t: (t["exitAt"] or "", t["id"]))
    open_positions.sort(key=lambda p: p["entryAt"] or "")
    return trades, open_positions


# ---------------------------------------------------------------- the numbers
def stats(trades: list[dict], period: str = "") -> dict:
    pls = [t["pnl"] for t in trades if _num(t.get("pnl"))]
    wins = [p for p in pls if p > 0]
    losses = [p for p in pls if p < 0]
    gross_win, gross_loss = sum(wins), -sum(losses)
    n = len(pls)
    if not n:
        factor = None
    elif gross_loss:
        factor = round(gross_win / gross_loss, 2)
    else:
        factor = "inf" if gross_win else None
    return {
        "trades": n, "wins": len(wins), "losses": len(losses), "flat": n - len(wins) - len(losses),
        "winRate": round(len(wins) / n, 4) if n else None,
        "avgWin": round(gross_win / len(wins), 2) if wins else None,
        "avgLoss": round(-gross_loss / len(losses), 2) if losses else None,
        "expectancy": round(sum(pls) / n, 2) if n else None,
        "profitFactor": factor, "pnl": round(sum(pls), 2),
        "enough": n >= TOO_FEW, "note": "" if n >= TOO_FEW else TOO_FEW_LINE, "period": period,
    }


def time_bucket(at: datetime | None) -> str:
    if at is None:
        return "unknown"
    minute = at.hour * 60 + at.minute
    for label, lo, hi in TIME_BUCKETS:
        if lo <= minute < hi:
            return label
    return "outside 09:30-16:00"


def dte_bucket(dte) -> str:
    if not _num(dte):
        return "unknown"
    for label, lo, hi in DTE_BUCKETS:
        if lo <= dte <= hi:
            return label
    return "unknown"


def delta_bucket(delta) -> str:
    if not _num(delta):
        return "no saved chain"
    d = abs(delta)
    for label, lo, hi in DELTA_BUCKETS:
        if lo <= d < hi:
            return label
    return "no saved chain"


def setup_label(setup: dict | None) -> str:
    if not isinstance(setup, dict):
        return "not checked yet"
    if setup.get("noHistory"):
        return "no scanner history that day"
    if setup.get("unknown"):
        return "not known (no saved scanner row then)"
    if not setup.get("onBoard"):
        return "not on the board"
    level = setup.get("level")
    return f"Level {level}" if isinstance(level, int) and level > 0 else "on the board, no Level"


def _group(trades: list[dict], keyfn: Callable[[dict], str], order: Iterable[str] | None, period: str) -> list[dict]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[keyfn(t)].append(t)
    names = [name for name in (order or ()) if name in groups] + sorted(k for k in groups if k not in set(order or ()))
    return [{"key": name, "label": name, **stats(groups[name], period)} for name in names]


def breakdowns(trades: list[dict], period: str) -> dict:
    return {
        "setup": sorted(_group(trades, lambda t: setup_label(t.get("setup")), None, period), key=lambda r: -abs(r["pnl"])),
        "timeOfDay": _group(trades, lambda t: time_bucket(_at(t["entryAt"])), [b[0] for b in TIME_BUCKETS] + ["outside 09:30-16:00"], period),
        "dte": _group(trades, lambda t: dte_bucket(t.get("dte")), [b[0] for b in DTE_BUCKETS], period),
        "delta": _group(trades, lambda t: delta_bucket(t.get("delta")), [b[0] for b in DELTA_BUCKETS] + ["no saved chain"], period),
        "callPut": _group(trades, lambda t: {"CALL": "Calls", "PUT": "Puts"}.get(t.get("putCall") or "", "Stock"), ["Calls", "Puts", "Stock"], period),
        "weekday": _group(trades, lambda t: WEEKDAYS[_at(t["entryAt"]).weekday()] if _at(t["entryAt"]) else "unknown", WEEKDAYS, period),
        "ticker": sorted(_group(trades, lambda t: t.get("underlying") or "?", None, period), key=lambda r: -abs(r["pnl"])),
    }


# ---------------------------------------------------------------- habits
def _row(t: dict, note: str = "") -> dict:
    keep = ("id", "account", "underlying", "optionSymbol", "putCall", "strike", "expiry", "dte", "direction", "quantity",
            "entryAt", "entryPrice", "exitAt", "exitPrice", "fees", "cost", "pnl", "pnlPct", "holdMinutes", "expired", "setup",
            "event", "valueUnknown")
    return {**{k: t.get(k) for k in keep}, "note": note}


def _loss_cost(rows: list[dict]) -> float:
    return round(max(0.0, -sum(t["pnl"] for t in rows)), 2)


def _habit(key: str, flagged: list[tuple[dict, str]], cost: float, approx: bool = False, note: str = "") -> dict:
    label, what = HABIT_TEXT[key]
    return {"key": key, "label": label, "what": what, "count": len(flagged), "cost": round(cost, 2),
            "pnl": round(sum(t["pnl"] for t, _ in flagged), 2), "approx": approx, "note": note,
            "trades": [_row(t, why) for t, why in flagged]}


def _clock(t: str | None) -> str:
    at = _at(t)
    return at.strftime("%H:%M") if at else "?"


def habit_revenge(trades: list[dict], everything: list[dict]) -> dict:
    flagged = []
    for t in trades:
        start = _at(t["entryAt"])
        for loss in everything:
            end = _at(loss["exitAt"])
            if loss is t or loss["pnl"] >= 0 or start is None or end is None:
                continue
            if timedelta(0) <= start - end <= timedelta(minutes=REVENGE_MINUTES):
                flagged.append((t, f"entered {int((start - end).total_seconds() // 60)} min after a ${-loss['pnl']:,.0f} loss on {loss['underlying']} closed at {_clock(loss['exitAt'])}"))
                break
    return _habit("revenge", flagged, _loss_cost([t for t, _ in flagged]))


def habit_overtrading(trades: list[dict], everything: list[dict]) -> dict:
    per_day: dict[str, list[dict]] = defaultdict(list)
    for t in everything:
        per_day[t["entryAt"][:10]].append(t)
    days = sorted(per_day)
    mine = {t["id"] for t in trades}
    flagged, note = [], ""
    for i, day in enumerate(days):
        before = days[max(0, i - OVERTRADE_LOOKBACK):i]
        if len(before) < OVERTRADE_MIN_DAYS:
            continue
        limit = median(len(per_day[d]) for d in before) * OVERTRADE_FACTOR
        todays = sorted(per_day[day], key=lambda t: t["entryAt"])
        if len(todays) <= limit:
            continue
        for n, t in enumerate(todays[math.floor(limit):], start=math.floor(limit) + 1):
            if t["id"] in mine:
                flagged.append((t, f"trade {n} of {len(todays)} that day; the usual day had {limit / OVERTRADE_FACTOR:g}"))
    if len(days) <= OVERTRADE_MIN_DAYS:
        note = f"Needs {OVERTRADE_MIN_DAYS} earlier trading days to know a usual day."
    return _habit("overtrading", flagged, _loss_cost([t for t, _ in flagged]), note=note)


def habit_size_up(trades: list[dict], everything: list[dict]) -> dict:
    ordered = sorted(everything, key=lambda t: t["entryAt"])
    mine = {t["id"] for t in trades}
    flagged = []
    for loss in ordered:
        if loss["pnl"] >= 0 or not loss.get("cost"):
            continue
        nxt = next((t for t in ordered if t is not loss and t["entryAt"] >= loss["exitAt"]), None)
        if nxt and nxt["id"] in mine and nxt.get("cost", 0) > SIZE_UP_FACTOR * loss["cost"] and not any(nxt is f for f, _ in flagged):
            flagged.append((nxt, f"cost ${nxt['cost']:,.0f} after a ${-loss['pnl']:,.0f} loss on a ${loss['cost']:,.0f} trade"))
    return _habit("sizeUpAfterLoss", flagged, _loss_cost([t for t, _ in flagged]))


def _marks_between(t: dict, paths: dict, start: str, end: str | None) -> list[float]:
    path = paths.get(f"{_key(t['optionSymbol'])}|{start[:10]}") or {}
    return [m for at, m in path.get("points") or [] if _num(m) and start <= at and (end is None or at <= end)]


def habit_held_past_50(trades: list[dict], paths: dict) -> dict:
    flagged, approx = [], False
    for t in trades:
        if t["direction"] != "long" or not t.get("entryPrice"):
            continue
        floor = 0.5 * t["entryPrice"]
        seen = _marks_between(t, paths, t["entryAt"], t["exitAt"])
        low = min(seen + [t["exitPrice"]])
        if low <= floor + 1e-9:
            from_exit = not seen or min(seen) > floor
            approx = approx or from_exit
            where = "the exit price" if from_exit else "a saved chain"
            flagged.append((t, f"low {low:.2f} vs entry {t['entryPrice']:.2f} (from {where}); sold at {t['exitPrice']:.2f}"))
    cost = sum(max(0.0, (0.5 * t["entryPrice"] - t["exitPrice"]) * t["quantity"] * t["multiplier"]) for t, _ in flagged)
    return _habit("heldPast50", flagged, cost, approx=approx,
                  note="Cost = what was lost below the -50% point. Saved chains are 30 minutes apart, so lows between them are not seen.")


def habit_cut_winners(trades: list[dict], paths: dict) -> dict:
    flagged = []
    for t in trades:
        if t["direction"] != "long" or t.get("expired") or not t.get("entryPrice") or t["exitPrice"] >= 2 * t["entryPrice"]:
            continue
        if t["exitAt"][:10] != t["entryAt"][:10]:
            continue
        later = [m for at, m in (paths.get(f"{_key(t['optionSymbol'])}|{t['exitAt'][:10]}") or {}).get("points") or []
                 if _num(m) and at > t["exitAt"]]
        if later and max(later) >= 2 * t["entryPrice"]:
            flagged.append((t, f"sold at {t['exitPrice']:.2f}; a saved chain later that day showed {max(later):.2f} (entry {t['entryPrice']:.2f})"))
    cost = sum((2 * t["entryPrice"] - t["exitPrice"]) * t["quantity"] * t["multiplier"] for t, _ in flagged)
    return _habit("cutWinnersEarly", flagged, cost, approx=bool(flagged),
                  note="Cost = the gap between the sale and +100%. Only where a saved chain shows it (from 2026-11-10, every 30 minutes).")


def hold_time(trades: list[dict]) -> dict:
    def med(rows):
        values = [t["holdMinutes"] for t in rows if _num(t.get("holdMinutes"))]
        return round(median(values), 1) if values else None
    return {"all": med(trades), "wins": med([t for t in trades if t["pnl"] > 0]), "losses": med([t for t in trades if t["pnl"] < 0])}


def habits(trades: list[dict], everything: list[dict], paths: dict) -> list[dict]:
    found = [habit_revenge(trades, everything), habit_overtrading(trades, everything), habit_size_up(trades, everything),
             habit_held_past_50(trades, paths), habit_cut_winners(trades, paths)]
    return sorted(found, key=lambda h: (-h["cost"], -h["count"]))


# ---------------------------------------------------------------- execution (fills vs the chain's mid)
def usable_quote(q: dict | None, fill_at) -> bool:
    """A saved quote describes a fill only when it was saved the same ET day, at most
    MAX_QUOTE_GAP_MINUTES before it. An older chain (yesterday's afternoon for a morning fill)
    would turn the overnight move into "spread paid" and give the wrong delta."""
    if not isinstance(q, dict) or q.get("none"):
        return False
    gap = q.get("gapMinutes")
    if not _num(gap) or gap < -1 or gap > MAX_QUOTE_GAP_MINUTES:
        return False
    saved, at = _at(q.get("savedAt")), _at(fill_at)
    return saved is None or at is None or saved.date() == at.date()


def execution(trades: list[dict], fills: dict, quotes: dict) -> dict:
    ids = set()
    for t in trades:
        ids.update(list(t.get("entryFillIds") or [t["entryId"]]) + list(t.get("exitFillIds") or []))
    rows, unpriced = [], 0
    for fid in sorted(ids):
        fill = fills.get(fid)
        if not fill or fill.get("kind") in EVENT_KINDS:
            continue
        q = quotes.get(fid) or {}
        bid, ask = q.get("bid"), q.get("ask")
        if not (usable_quote(q, fill.get("at")) and _num(bid) and _num(ask) and ask > 0 and ask >= bid):
            unpriced += 1
            continue
        mid = (bid + ask) / 2
        sign = 1 if fill["side"] == "BUY" else -1
        paid = sign * (fill["price"] - mid) * fill["quantity"] * (fill.get("multiplier") or 100.0)
        rows.append({"id": fid, "at": fill["at"], "contract": fill.get("optionSymbol"), "side": fill["side"],
                     "quantity": fill["quantity"], "price": fill["price"], "bid": bid, "ask": ask, "mid": round(mid, 4),
                     "paidVsMid": round(paid, 2), "paidPct": round(sign * (fill["price"] - mid) / mid, 4) if mid else None,
                     "widthPct": round((ask - bid) / mid, 4) if mid else None,
                     "gapMinutes": q.get("gapMinutes"), "approx": not _num(q.get("gapMinutes")) or q["gapMinutes"] > APPROX_MINUTES})
    total = sum(r["paidVsMid"] for r in rows)
    pcts = [r["paidPct"] for r in rows if _num(r["paidPct"])]
    widths = [r["widthPct"] for r in rows if _num(r["widthPct"])]
    approx = sum(1 for r in rows if r["approx"])
    note = ("No saved option prices near these fills, so the spread cannot be measured." if not rows else
            f"Measured on {len(rows)} fills with a saved chain price" + (f"; {approx} used a chain saved more than {APPROX_MINUTES} minutes before the fill (approximate)" if approx else "") + ".")
    return {"fills": len(rows) + unpriced, "trades": len(rows), "unpriced": unpriced, "approxCount": approx,
            "spreadCost": round(total, 2), "vsMid": round(-total, 2),
            "avgSpreadDollars": round(total / len(rows), 2) if rows else None,
            "avgSpreadPct": round(sum(pcts) / len(pcts), 4) if pcts else None,
            "avgWidthPct": round(sum(widths) / len(widths), 4) if widths else None,
            "note": note, "rows": sorted(rows, key=lambda r: r["at"], reverse=True)[:MAX_TRADE_ROWS]}


# ---------------------------------------------------------------- paper bots over the same days
def paper_bots(artifacts: Path, start: str, end: str, my_days: set[str], period: str) -> list[dict]:
    """The paper accounts' closed, really-filled trades (the Scorecard's own reading), over the period
    and over just the days the owner closed a trade."""
    from momx import bot_desk
    out = []
    root = Path(artifacts) / "momx_paper_mirror"
    for account in bot_desk.ACCOUNTS:
        folder = root / account
        if not folder.is_dir():
            continue
        rows = bot_desk.load_rows(folder, (date.fromisoformat(start) - timedelta(days=10)).isoformat(), end)
        closed = []
        for r in rows:
            if not bot_desk._closed(r):
                continue
            day = bot_desk._day(r.get("closedAt")) or bot_desk._day(r.get("at"))
            if start <= day <= end:
                closed.append({"pnl": float(r["pl"]), "day": day})
        mode = bot_desk.ACCOUNT_MODES.get(account) or {}
        out.append({"account": account, "label": mode.get("name") or account, **stats(closed, period),
                    "sameDays": stats([t for t in closed if t["day"] in my_days], period + ", the days you traded"),
                    "basis": "Every closed, really filled paper trade in the period, P/L on the day it closed "
                             f"(the Scorecard judges only from {bot_desk.EVALUATION_START})."})
    return out


# ---------------------------------------------------------------- saved-chain details (filled in after each read)
CHAIN_LISTS = ("selectedExpiryChainRows", "callRows", "putRows")


def contract_row(chain: dict | None, option_symbol: str) -> dict | None:
    if not isinstance(chain, dict):
        return None
    want = _key(option_symbol)
    for name in CHAIN_LISTS:
        for row in chain.get(name) or []:
            if isinstance(row, dict) and _key(row.get("symbol")) == want:
                return row
    return None


def _mark(row: dict | None):
    if not row:
        return None
    for key in ("mark",):
        if _num(row.get(key)) and row[key] > 0:
            return row[key]
    if _num(row.get("bid")) and _num(row.get("ask")) and row["ask"] > 0:
        return (row["bid"] + row["ask"]) / 2
    return None


def _saved_chains_of_day(history_root: Path, underlying: str, day: str) -> list[dict]:
    out = []
    folder = Path(history_root) / day
    try:
        slots = sorted(p for p in folder.iterdir() if p.is_dir() and p.name.isdigit())
    except OSError:
        return out
    for slot in slots:
        path = slot / f"{str(underlying).upper()}.json.gz"
        if not path.is_file():
            continue
        try:
            with gzip.open(path, "rt", encoding="utf-8") as handle:
                doc = json.load(handle)
            doc["_saved"] = {"day": day, "slot": slot.name,
                             "fileTime": datetime.fromtimestamp(path.stat().st_mtime, ET).replace(microsecond=0).isoformat()}
        except (OSError, ValueError):
            continue
        out.append(doc)
    return out


def scanner_setup(artifacts: Path, symbol: str, bear: bool, at: datetime) -> dict | None:
    """The scanner row of `symbol` at `at` on Mag7 then Watchlist (calls on the bull board, puts on the bear one).
    None = the history is not readable yet (try again next read)."""
    from momx import chat_scanner as cs
    today = datetime.now(ET).date().isoformat()
    day, t = at.date().isoformat(), at.timestamp()
    any_file = False
    with cs.turn():
        for name in ("Mag7", "Watchlist"):
            board = cs._board(Path(artifacts), name, bear, day, today)
            if board is None:
                continue
            if board.waiting or board.retry:
                return None
            if board.broken:
                continue
            any_file = True
            ticker = board.tickers.get(symbol.upper())
            point = cs._state(ticker, t) if ticker is not None else None
            if point is None:
                # Seen before and matched again after, with no saved row near the entry: rows are saved only when a
                # column changes and stop at the row cap, so this is "not known", never "not on the board"
                # (chat_scanner's own rule; 2026-11-11 review).
                # A fresh row on the other side at that moment means it was there, not on this board.
                if ticker is not None and cs._unknown_then(ticker, t) and \
                        cs._on_other_side(cs._board(Path(artifacts), name, not bear, day, today), symbol.upper(), t) is None:
                    clock = datetime.fromtimestamp(ticker.capped, ET).strftime("%H:%M") if cs._stopped_by_cap(ticker, t) else None
                    reason = (f"row saving stopped at {clock} ET, so whether it was on the board then is not known" if clock
                              else "no saved row near the entry, so whether it was on the board then is not known")
                    return {"onBoard": None, "unknown": True, "board": name, "side": "bear" if bear else "bull", "reason": reason}
                continue
            rank = next((i + 1 for i, (s, _) in enumerate(cs.ranking(board, t)) if s == symbol.upper()), None)
            return {"onBoard": True, "board": name, "side": "bear" if bear else "bull", "level": point.level,
                    "setups": point.setups, "setupNames": list(point.names)[:6], "hlRank": rank,
                    "rvol": bool(point.rvol), "sqz": bool(point.sqz), "skittles": bool(point.skittles)}
    return {"onBoard": False, "noHistory": not any_file}


def enrich_store(store: dict, artifacts: Path, now: datetime | None = None,
                 chain_at: Callable[[str, datetime], dict | None] | None = None,
                 day_chains: Callable[[str, str], list[dict]] | None = None,
                 setup_at: Callable[[str, bool, datetime], dict | None] | None = None) -> dict:
    """Add, once per fill, the saved chain's bid/ask/delta at the fill; per contract-day, the saved marks
    (for the -50% / +100% habits); per entry, the scanner row. Only reads saved files. Saves the store."""
    now = (now or datetime.now(ET)).astimezone(ET)
    artifacts = Path(artifacts)
    if chain_at is None:
        from momx import chain_history
        chain_at = chain_history.ChainHistory(artifacts / "oi_chain_cache", artifacts / "oi_chain_history").chain_at
    if day_chains is None:
        day_chains = lambda und, day: _saved_chains_of_day(artifacts / "oi_chain_history", und, day)  # noqa: E731
    if setup_at is None:
        setup_at = lambda sym, bear, at: scanner_setup(artifacts, sym, bear, at)  # noqa: E731
    fills = store.get("fills") or {}
    quotes = store.setdefault("quotes", {})
    paths = store.setdefault("paths", {})
    setups = store.setdefault("setups", {})
    chain_cache: dict[tuple, dict | None] = {}
    for fid, fill in fills.items():
        if fill.get("assetType") != "OPTION" or fill.get("kind") in EVENT_KINDS:
            continue
        if fid in quotes:
            if not quotes[fid].get("none") and not usable_quote(quotes[fid], fill.get("at")):
                quotes[fid] = {"none": True}            # saved earlier from a stale chain
            continue
        at = _at(fill.get("at"))
        if at is None:
            continue
        key = (fill["underlying"], fill["at"][:16])
        if key not in chain_cache:
            chain_cache[key] = chain_at(fill["underlying"], at)
        chain = chain_cache[key]
        row = contract_row(chain, fill["optionSymbol"])
        if not row:
            quotes[fid] = {"none": True}
            continue
        saved = _at((chain.get("_saved") or {}).get("fileTime"))
        quote = {"bid": row.get("bid"), "ask": row.get("ask"), "mark": _mark(row), "delta": row.get("delta"),
                 "savedAt": saved.isoformat() if saved else None,
                 "gapMinutes": round((at - saved).total_seconds() / 60, 1) if saved else None}
        quotes[fid] = quote if usable_quote(quote, fill.get("at")) else {"none": True}
    days_seen: dict[tuple, list[dict]] = {}
    for fill in fills.values():
        if fill.get("assetType") != "OPTION":
            continue
        pkey = f"{_key(fill['optionSymbol'])}|{fill['day']}"
        if (paths.get(pkey) or {}).get("complete"):
            continue
        dkey = (fill["underlying"], fill["day"])
        if dkey not in days_seen:
            days_seen[dkey] = day_chains(fill["underlying"], fill["day"])
        points = []
        for chain in days_seen[dkey]:
            mark = _mark(contract_row(chain, fill["optionSymbol"]))
            stamp = (chain.get("_saved") or {}).get("fileTime")
            if mark is not None and stamp:
                points.append([stamp, mark])
        day_over = fill["day"] < now.date().isoformat() or now.hour * 60 + now.minute >= SESSION_END
        paths[pkey] = {"points": sorted(points), "complete": day_over}
    for order in orders_from_fills(fills.values()):
        if order["id"] in setups or order["side"] != "BUY" or order.get("assetType") != "OPTION":
            continue
        at = _at(order["at"])
        if at is None:
            continue
        try:
            found = setup_at(order["underlying"], order.get("putCall") == "PUT", at)
        except Exception:  # noqa: BLE001 - a scanner file that cannot be read leaves "not checked yet"
            found = None
        if found is not None:
            setups[order["id"]] = found
    schwab_history.save_store(store)
    return store


# ---------------------------------------------------------------- the page
def period_of(days: int, today: date) -> dict:
    start = today - timedelta(days=days - 1)
    return {"days": days, "from": start.isoformat(), "to": today.isoformat(),
            "label": f"last {days} days ({start.strftime('%m-%d')} to {today.strftime('%m-%d')})"}


def build(days: int = 30, now: datetime | None = None, artifacts: Path | None = None,
          store: dict | None = None, events: dict | None = None, schwab: dict | None = None) -> dict:
    """The GET /api/my-insights reply (see the top of this file). Reads saved files only."""
    days = days if days in PERIODS else 30
    now = (now or datetime.now(ET)).astimezone(ET)
    today = now.date()
    if artifacts is None:
        from config import ARTIFACTS_DIR
        artifacts = Path(ARTIFACTS_DIR)
    store = store if store is not None else schwab_history.load_store()
    fills = store.get("fills") or {}
    quotes, paths, setups = store.get("quotes") or {}, store.get("paths") or {}, store.get("setups") or {}
    pairing: dict = {}
    every_trade, open_positions = pair_trades(fills.values(), today.isoformat(), pairing)
    for t in every_trade:
        t["setup"] = setups.get(t["entryId"])
        q = quotes.get(t["entryId"])
        t["delta"] = q.get("delta") if usable_quote(q, t["entryAt"]) else None
    period = period_of(days, today)
    in_period = [t for t in every_trade if period["from"] <= (t["exitAt"] or "")[:10] <= period["to"]]
    # An exercise/assignment whose value is not in Schwab's row would be a made-up loss: left out of every number.
    everything = [t for t in every_trade if not t.get("valueUnknown")]
    mine = [t for t in in_period if not t.get("valueUnknown")]
    unscored = len(in_period) - len(mine)
    label = period["label"]
    summary = {**stats(mine, label), "fees": round(sum(t["fees"] for t in mine), 2),
               "medianHoldMinutes": hold_time(mine)["all"], "openPositions": len(open_positions),
               "unscored": unscored, "closedBeforeWindow": pairing.get("closedBeforeWindow", 0)}
    my_days = {t["exitAt"][:10] for t in mine}
    basis = ("Real Schwab fills, read once a day after the close. Round trips are paired first-in first-out per contract; "
             "a trade counts on the day it closed; P/L is after fees. Options left open after expiry count as expired at $0.")
    if summary["closedBeforeWindow"]:
        n = summary["closedBeforeWindow"]
        basis += (f" {n} {'sale closed a position' if n == 1 else 'sales closed positions'} opened before the read window, "
                  "so they are not counted as trades.")
    if unscored:
        basis += (f" {unscored} {'trade was' if unscored == 1 else 'trades were'} exercised or assigned; "
                  "Schwab's record does not show the option's value, so they are listed but left out of the numbers and habits.")
    try:
        bots = paper_bots(Path(artifacts), period["from"], period["to"], my_days, label)
    except Exception:  # noqa: BLE001 - the paper files never block the owner's own numbers
        bots = []
    if events is None:
        from momx import market_events
        events = market_events.read(now)
    return {
        "available": bool(fills), "days": days, "period": period, "asOf": now.isoformat(timespec="seconds"),
        "basis": basis,
        "summary": summary, "breakdowns": breakdowns(mine, label), "habits": habits(mine, everything, paths),
        "holdTime": hold_time(mine), "execution": execution(mine, fills, quotes), "paperBots": bots,
        "events": events, "schwab": schwab if schwab is not None else schwab_history.status(now),
        "trades": [_row(t, "exercised or assigned: Schwab's record does not show the option's value, left out of the numbers"
                        if t.get("valueUnknown") else "")
                   for t in sorted(in_period, key=lambda t: t["exitAt"], reverse=True)[:MAX_TRADE_ROWS]],
        "openPositions": open_positions,
    }


# ---------------------------------------------------------------- the chat's lines
def _money(v) -> str:
    if not _num(v):
        return "-"
    return ("+" if v > 0 else "-" if v < 0 else "") + f"${abs(v):,.0f}"


def _stats_line(s: dict, what: str) -> str:
    if not s["trades"]:
        return f"{what}: no closed trades ({s['period']})."
    line = (f"{what}: {s['trades']} trades, {_money(s['pnl'])} after fees, win rate {s['winRate'] * 100:.0f}%, "
            f"{_money(s['expectancy'])} per trade on average ({s['period']})")
    return line + (f", {TOO_FEW_LINE}." if not s["enough"] else ".")


def _et_clock(stamp) -> str:
    """"11-12 01:09 ET" from an ET stamp, the same as the page's clock."""
    s = str(stamp or "")
    return f"{s[5:10]} {s[11:16]} ET" if len(s) >= 16 and s[10] == "T" else s


def chat_lines(doc: dict, topic: str = "summary") -> list[str]:
    """Plain lines for the chat's my_trades tool. Describes; never advises."""
    if not doc.get("available"):
        sch = doc.get("schwab") or {}
        if sch.get("lastReadAt"):
            # A read worked and found nothing: say so, never "not read yet" (the owner would wait for a read that happened).
            names = [str(a) for a in (sch.get("accounts") or []) if a]
            where = f" on {'account' if len(names) == 1 else 'accounts'} {', '.join(names)}" if names else ""
            lines = [f"Read from Schwab at {_et_clock(sch['lastReadAt'])}: no option trades found in the last "
                     f"{schwab_history.FIRST_RUN_DAYS} days{where}."]
            if sch.get("signInNeeded"):
                lines.append("Schwab sign-in needed: the trade history could not be read today.")
            return lines
        if sch.get("signInNeeded"):
            return ["Schwab sign-in needed: the trade history could not be read today."]
        if sch.get("stopped") or sch.get("state") == "stopped":
            return ["The Schwab read failed today, so no trades have been read yet."]
        if sch.get("reading") or sch.get("state") == "reading":
            return ["Reading from Schwab now."]
        return ["No Schwab trades have been read yet. They are read once a day after 16:30 ET."]
    s = doc["summary"]
    lines: list[str] = []
    if topic in ("calls", "puts"):
        row = next((r for r in doc["breakdowns"]["callPut"] if r["key"] == ("Calls" if topic == "calls" else "Puts")), None)
        lines.append(_stats_line(row, topic.title()) if row else f"No closed {topic} ({s['period']}).")
    elif topic == "habits":
        costly = [h for h in doc["habits"] if h["count"]]
        if not costly:
            lines.append(f"None of the five habits showed up ({s['period']}, {s['trades']} trades).")
        for h in costly[:3]:
            lines.append(f"{h['label']}: {h['count']} {'time' if h['count'] == 1 else 'times'}, ${h['cost']:,.0f} on those trades ({s['period']}).")
    elif topic == "spreads":
        e = doc["execution"]
        if not e["trades"]:
            lines.append(e["note"])
        else:
            cost = e["spreadCost"]
            if cost > 0:
                lines.append(f"Fills vs the saved chain's mid: ${cost:,.0f} paid over {e['trades']} fills ({s['period']}).")
            elif cost < 0:
                lines.append(f"Fills vs the saved chain's mid: ${abs(cost):,.0f} saved over {e['trades']} fills ({s['period']}): "
                             "the fills beat the mid.")
            else:
                lines.append(f"Fills vs the saved chain's mid: filled at the mid over {e['trades']} fills ({s['period']}).")
            if e["approxCount"]:
                lines.append(f"{e['approxCount']} of them used a chain saved more than {APPROX_MINUTES} minutes before the fill, so they are approximate.")
    elif topic in ("setup", "time", "dte", "tickers"):
        key = {"setup": "setup", "time": "timeOfDay", "dte": "dte", "tickers": "ticker"}[topic]
        for r in doc["breakdowns"][key][:5]:
            lines.append(_stats_line(r, r["label"]))
    else:
        lines.append(_stats_line(s, "Your trades"))
        if _num(s.get("avgWin")) or _num(s.get("avgLoss")):
            lines.append(f"Average win {_money(s.get('avgWin'))}, average loss {_money(s.get('avgLoss'))}.")
        worst = next((h for h in doc["habits"] if h["count"]), None)
        if worst:
            lines.append(f"Costliest habit: {worst['label'].lower()}, {worst['count']} times, ${worst['cost']:,.0f}.")
        if s.get("unscored"):
            lines.append(f"{s['unscored']} exercised or assigned {'trade is' if s['unscored'] == 1 else 'trades are'} left out: "
                         "Schwab's record does not show the option's value.")
    sch = doc.get("schwab") or {}
    if sch.get("lastReadAt"):
        lines.append(f"Last read from Schwab at {str(sch['lastReadAt'])[:16].replace('T', ' ')} ET.")
    return lines
