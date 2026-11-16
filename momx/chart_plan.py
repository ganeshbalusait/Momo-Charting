"""The chart's Project ROI plan, in Python (2026-11-12).

His decision after the paper bots lost $4,621 and $6,399 in three days: "BOT we
can do the entry and exit and profit target, we have in chart 'entry, exit and
target' use that entry the position and target 1,2,3 ... and stop loss". So the
paper bots (momx/paper_mirror.py) and the Bot's own tracked trades
(momx/option_track.py) follow the plan the chart draws.

This module is a line-for-line port of the chart's own arithmetic:

    frontend/src/chartProjectRoi.js   buildProjectRoi, projectRoiStopStock,
                                      projectRoiChainEntry, projectRoiExpiries
    frontend/src/roiCalcAutofill.js   roiAutoTargets, roiExpiryExpectedMove

It is a PORT, not a second opinion: tests/test_momx_chart_plan.py runs the real
JavaScript (tests/chart_plan_truth.mjs) over saved option chains and requires
every number here to match it exactly. A change to a chart formula therefore
shows up as a failing test, never as a silent difference between the chart and
the bot. Nothing here is invented: where the chart has no number (no expected
move, no delta), this returns none either.

Pure arithmetic. No file, network or broker access.
"""
from __future__ import annotations

import math
from typing import Any, Mapping

#: The chart's stop: the stock price where the option is down this share of its entry.
STOP_LOSS_SHARE = 0.5
#: The chart's ENTRY contract: OTM, |delta| under this and closest to it.
ENTRY_DELTA = 0.2


def _number(value: Any) -> float:
    """JavaScript Number(value || 0): 0 for anything empty or not a finite number."""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _positive(value: Any) -> float:
    out = _number(value)
    return out if out > 0 else 0.0


def js_round(value: float) -> float:
    """JavaScript Math.round: to the nearest whole number, a tie goes UP (Python's
    round() sends a tie to the even number, which moves prices by a cent)."""
    low = math.floor(value)
    return low + 1 if value - low >= 0.5 else low


def round2(value: Any) -> float:
    """The chart's round2: Math.round(value * 100) / 100."""
    return js_round(_number(value) * 100) / 100


def side_of(side: Any) -> str:
    return "PUT" if str(side or "CALL").upper() == "PUT" else "CALL"


def _expiry(row: Any) -> str:
    return str((row.get("expiry") if isinstance(row, Mapping) else "") or "")[:10]


def _rows(rows: Any) -> list:
    return [r for r in rows if isinstance(r, Mapping)] if isinstance(rows, (list, tuple)) else []


def contract_price(row: Mapping | None) -> float:
    """roiContractPrice: the mark, else the bid/ask middle, else the last trade."""
    if not row:
        return 0.0
    mark = _number(row.get("mark"))
    if mark > 0:
        return mark
    bid, ask = _number(row.get("bid")), _number(row.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    last = _number(row.get("last"))
    return last if last > 0 else 0.0


def chain_price(row: Mapping | None) -> float:
    """projectRoiChainPrice: the bid/ask middle, else the mark, else the last trade."""
    if not row:
        return 0.0
    bid, ask = _number(row.get("bid")), _number(row.get("ask"))
    if bid > 0 and ask > 0:
        return (bid + ask) / 2
    mark = _number(row.get("mark"))
    if mark > 0:
        return mark
    last = _number(row.get("last"))
    return last if last > 0 else 0.0


def expiry_expected_move(rows: Any, expiry: str, expiry_expected_moves: Mapping | None = None) -> float:
    """roiExpiryExpectedMove: the per-expiry map when it has the expiry, else the
    first row of that expiry that carries an expected move."""
    key = str(expiry or "")[:10]
    from_map = _number(expiry_expected_moves.get(key)) if isinstance(expiry_expected_moves, Mapping) else 0.0
    if from_map > 0:
        return from_map
    for row in _rows(rows):
        if _expiry(row) == key and _number(row.get("expected_move")) > 0:
            return _number(row.get("expected_move"))
    return 0.0


def _third_wall(rows: Any, price: float, move: float, side: Any, expiry: str) -> Mapping | None:
    """The chart's third-target strike: same side and expiry, open interest above
    zero, past the full move and within two moves; the BIGGEST open interest wins
    (on a tie the earlier chain row: the chart's sort is stable)."""
    is_call = side_of(side) == "CALL"
    sign = 1 if is_call else -1
    key = str(expiry or "")[:10]
    full = round2(price + sign * move)
    wall = None
    for row in _rows(rows):
        strike = _number(row.get("strike"))
        if _expiry(row) != key or side_of(row.get("side")) != side_of(side) or not _number(row.get("open_interest")) > 0:
            continue
        inside = (strike > full and strike <= price + 2 * move) if is_call else (strike < full and strike >= price - 2 * move)
        if inside and (wall is None or _number(row.get("open_interest")) > _number(wall.get("open_interest"))):
            wall = row
    return wall


def auto_targets(rows: Any, spot: Any, side: Any, expiry: str, expected_move: Any) -> list[float] | None:
    """roiAutoTargets: [half the expected move, the full move, the strike with the
    biggest open interest past the full move and within two moves - else 1.5x the
    move]. None when there is no stock price or no expected move (the chart
    returns three blanks)."""
    price = max(0.0, _number(spot))
    move = max(0.0, _number(expected_move))
    if not price or not move:
        return None
    sign = 1 if side_of(side) == "CALL" else -1
    half = round2(price + sign * move / 2)
    full = round2(price + sign * move)
    wall = _third_wall(rows, price, move, side, expiry)
    third = round2(_number(wall.get("strike"))) if wall is not None else round2(price + sign * move * 1.5)
    return [half, full, third]


def option_value_raw(side: Any, spot: Any, price: Any, delta: Any, gamma: Any, strike: Any, target: Any) -> float:
    """optionValueRaw: today's price plus the average of the start and end delta
    times the move, never under intrinsic value at the target, never under a cent."""
    start, to, now = _positive(spot), _positive(target), _positive(price)
    if not start or not to or not now:
        return 0.0
    is_call = side_of(side) == "CALL"
    favourable = to - start if is_call else start - to
    d = min(abs(_number(delta)), 1.0)
    g = abs(_number(gamma))
    end_delta = min(max(d + g * favourable, 0.0), 1.0)
    modelled = now + ((d + end_delta) / 2) * favourable
    intrinsic = max(to - _number(strike) if is_call else _number(strike) - to, 0.0)
    return max(modelled, intrinsic, 0.01)


def option_value(side: Any, spot: Any, price: Any, delta: Any, gamma: Any, strike: Any, target: Any) -> float:
    """projectRoiOptionValue: the value above, to the cent."""
    value = option_value_raw(side, spot, price, delta, gamma, strike, target)
    return round2(value) if value > 0 else 0.0


def stop_stock(side: Any, spot: Any, price: Any, delta: Any, gamma: Any, strike: Any, stop_premium: Any) -> float | None:
    """projectRoiStopStock: the stock price where the contract is worth
    `stop_premium`, walking against the trade from `spot` (60 halvings). None when
    no adverse move gets there."""
    start, now = _positive(spot), _positive(price)
    premium = _number(stop_premium)
    if not start or not now or not premium > 0:
        return None
    if now <= premium:
        return round2(start)
    is_call = side_of(side) == "CALL"

    def value_at(adverse: float) -> float:
        return option_value_raw(side, start, now, delta, gamma, strike, start - adverse if is_call else start + adverse)

    low, high = 0.0, (start if is_call else start * 2)
    if value_at(high) > premium:
        return None
    for _ in range(60):
        mid = (low + high) / 2
        if value_at(mid) > premium:
            low = mid
        else:
            high = mid
    adverse = (low + high) / 2
    return round2(start - adverse if is_call else start + adverse)


def expiries(rows: Any, first_live: str) -> list[dict]:
    """projectRoiExpiries: the expiries still trading (on or after `first_live`,
    'YYYY-MM-DD'), nearest first."""
    seen: dict[str, float] = {}
    for row in _rows(rows):
        key = _expiry(row)
        if not key or key < first_live or key in seen:
            continue
        seen[key] = _number(row.get("days_to_expiration"))
    return [{"expiry": k, "dte": v} for k, v in sorted(seen.items(), key=lambda kv: (kv[1], kv[0]))]


def chain_entry(rows: Any, spot: Any, side: Any, expiry: str, threshold: float = ENTRY_DELTA) -> dict | None:
    """projectRoiChainEntry without a server mark: the OTM contract of `expiry`
    with 0 < |delta| < 0.20 closest to 0.20, ties to the strike nearest the stock."""
    price = _positive(spot)
    if not price:
        return None
    is_call = side_of(side) == "CALL"
    key = str(expiry or "")[:10]
    best = None
    for row in _rows(rows):
        strike = _number(row.get("strike"))
        if (_expiry(row) != key or side_of(row.get("side")) != side_of(side)
                or not (strike > price if is_call else strike < price) or not chain_price(row) > 0):
            continue
        delta = abs(_number(row.get("delta")))
        if not 0 < delta < threshold:
            continue
        rank = (abs(threshold - delta), abs(strike - price))
        if best is None or rank < best[0]:        # strictly better only: the first row wins a tie
            best = (rank, row)
    if best is None:
        return None
    row = best[1]
    return {"strike": _number(row.get("strike")), "price": round2(chain_price(row)), "delta": abs(_number(row.get("delta")))}


def find_row(rows: Any, side: Any, expiry: str, strike: Any) -> Mapping | None:
    key, wanted = str(expiry or "")[:10], _number(strike)
    for row in _rows(rows):
        if _expiry(row) == key and side_of(row.get("side")) == side_of(side) and _number(row.get("strike")) == wanted:
            return row
    return None


def build(rows: Any, *, side: Any, expiry: str, strike: Any, entry: Any, spot: Any,
          expiry_expected_moves: Mapping | None = None, first_live: str | None = None,
          default_expiry: str = "") -> dict | None:
    """The chart's plan for ONE contract the bot has chosen, as buildProjectRoi
    gives it when the trader has typed a fill: plan = {side, expiry, strike,
    entry, entrySpot: spot} with the stock at `spot`.

    Returns {side, expiry, strike, entry, entrySpot, expectedMove, delta, gamma,
    stopPremium, stopStock, targets: [{key, label, stock, reason}], t1, t2, t3,
    t3FromOi} (+ chartExpiry / chartStrike when `first_live` is given), or None
    when the contract is not in the chain or there is no stock price. Any single
    number may be None (no expected move -> no targets; no way down to half -> no
    stop): the caller must not invent one.
    """
    price = _positive(spot)
    name = side_of(side)
    key = str(expiry or "")[:10]
    row = find_row(rows, name, key, strike)
    if not price or row is None:
        return None
    sign = 1 if name == "CALL" else -1
    move = expiry_expected_move(rows, key, expiry_expected_moves)
    paid = _positive(entry) or round2(chain_price(row))
    delta, gamma = _number(row.get("delta")), _number(row.get("gamma"))
    strike_value = _number(strike)

    auto = auto_targets(rows, price, name, key, move)
    half, wall = (auto[0], auto[2]) if auto else (0.0, 0.0)
    target = round2(price + sign * move) if move else 0.0
    candidates = [c for c in (("t1", round2(_positive(half)), "half the expected move"),
                              ("target", round2(_positive(target)), "full expected move"),
                              ("t3", round2(_positive(wall)), "OI level past the move")) if c[1] > 0]
    near = []
    for index, level in enumerate(candidates):
        first = next(i for i, other in enumerate(candidates) if abs(other[1] - level[1]) < 0.005)
        if first == index:
            near.append(level)
    near.sort(key=lambda level: sign * level[1])
    # The chart always shows three: when two of them collapse into one, it adds
    # runner levels past the last (2x, 2.5x, 3x the move, at least half a move on).
    wanted = 3
    runners = []
    if wanted > len(near) and move > 0 and near:
        farthest = near[-1][1]
        for moves, runner_key in ((2, "t4"), (2.5, "t5"), (3, "t6"))[:wanted - len(near)]:
            by_move = round2(price + sign * moves * move)
            minimum = round2(farthest + sign * move * 0.5)
            stock = by_move if sign * (by_move - minimum) >= 0 else minimum
            if not stock > 0:
                continue
            runners.append((runner_key, round2(stock), f"runner: {moves:g}x the expected move"))
            farthest = stock
    targets = [{"key": k, "label": f"T{i + 1}", "stock": stock, "reason": reason}
               for i, (k, stock, reason) in enumerate((near + runners)[:wanted])]

    premium = round2(paid * STOP_LOSS_SHARE) if paid > 0 else 0.0
    stop = stop_stock(name, price, paid, delta, gamma, strike_value, premium) if premium else None

    out = {"side": name, "expiry": key, "strike": strike_value, "entry": round2(paid), "entrySpot": round2(price),
           "expectedMove": move or None, "delta": delta, "gamma": gamma,
           "stopPremium": premium or None, "stopStock": stop, "targets": targets,
           "t1": targets[0]["stock"] if len(targets) > 0 else None,
           "t2": targets[1]["stock"] if len(targets) > 1 else None,
           "t3": targets[2]["stock"] if len(targets) > 2 else None,
           "t3FromOi": bool(move) and _third_wall(rows, price, move, name, key) is not None}
    # What the chart itself would have picked, for the record only (the bot's
    # contract rule is not changed by it).
    if first_live:
        live = expiries(rows, first_live)
        wanted_expiry = str(default_expiry or "")[:10]
        chart_expiry = wanted_expiry if any(e["expiry"] == wanted_expiry for e in live) else (live[0]["expiry"] if live else "")
        picked = chain_entry(rows, price, name, chart_expiry) if chart_expiry else None
        out["chartExpiry"] = chart_expiry or None
        out["chartStrike"] = picked["strike"] if picked else None
    return out


def from_chain(chain: Mapping | None, *, bear: bool, expiry: str, strike: Any, entry: Any, spot: Any,
               today: str | None = None) -> dict | None:
    """build() from a chain payload as /api/oi-finder-chain serves it and as the
    chart card receives it: the per-expiry expected-move map holds only the
    chain's current ATM expiry (ChartProjectRoiCard.jsx), every other expiry uses
    its own rows' expected_move."""
    if not isinstance(chain, Mapping):
        return None
    atm = chain.get("currentAtm") if isinstance(chain.get("currentAtm"), Mapping) else {}
    atm_expiry, atm_move = str(atm.get("expiry") or "")[:10], _number(atm.get("expectedMove"))
    moves = {atm_expiry: atm_move} if atm_expiry and atm_move > 0 else None
    plan = build(chain.get("selectedExpiryChainRows"), side="PUT" if bear else "CALL", expiry=expiry, strike=strike,
                 entry=entry, spot=spot, expiry_expected_moves=moves, first_live=today, default_expiry=atm_expiry)
    if plan is not None:
        plan["chainAt"] = chain.get("scannedAt")
        plan["walls"] = walls(chain.get("selectedExpiryChainRows"), plan["side"], plan["expiry"], spot, plan.get("expectedMove"))
    return plan


#: How far each side of the signal's stock price the open-interest strikes are kept, in expected moves.
WALL_REACH = 4


def walls(rows: Any, side: Any, expiry: str, spot: Any, expected_move: Any) -> list[list[float]]:
    """[[strike, open interest], ...] of the plan's own side and expiry, in chain order,
    within WALL_REACH expected moves of the stock price: what the chart's third-target
    rule reads, kept on the plan so rebase() can apply the SAME rule from another stock
    price without the chain."""
    price, move = _positive(spot), _positive(expected_move)
    if not price or not move:
        return []
    key, name = str(expiry or "")[:10], side_of(side)
    out = []
    for row in _rows(rows):
        strike, oi = _number(row.get("strike")), _number(row.get("open_interest"))
        if _expiry(row) != key or side_of(row.get("side")) != name or not oi > 0:
            continue
        if abs(strike - price) <= WALL_REACH * move:
            out.append([strike, oi])
    return out


def rebase(plan: Mapping, entry: Any, spot: Any) -> dict | None:
    """The plan for the SAME contract bought at `entry` with the stock at `spot`: the
    paper order goes out a little after the signal (median about 2 minutes, up to 10),
    and entry, stop and targets must start from ONE stock price, as they do on the
    chart when a fill is typed. This is build() itself on the plan's own numbers (the
    contract's delta and gamma, the expected move, and the open-interest strikes kept
    by walls()), so T1 / T2 / T3 and the stop are the chart's for that stock price.
    A plan from before walls were kept re-uses its own T3 strike as the only one.
    None when there is no stock price."""
    if not isinstance(plan, Mapping):
        return None
    name, key, strike = side_of(plan.get("side")), str(plan.get("expiry") or "")[:10], _number(plan.get("strike"))
    move = _positive(plan.get("expectedMove"))
    kept = plan.get("walls")
    if not isinstance(kept, (list, tuple)):
        kept = [[plan.get("t3"), 1]] if plan.get("t3FromOi") and _positive(plan.get("t3")) else []
    rows = [{"expiry": key, "side": name, "strike": strike, "delta": plan.get("delta"), "gamma": plan.get("gamma"),
             "open_interest": 0}]
    for wall in kept:
        if isinstance(wall, (list, tuple)) and len(wall) >= 2:
            rows.append({"expiry": key, "side": name, "strike": _number(wall[0]), "open_interest": _number(wall[1])})
    return build(rows, side=name, expiry=key, strike=strike, entry=entry, spot=spot,
                 expiry_expected_moves={key: move} if move else None)


def restop(plan: Mapping, entry: Any, spot: Any) -> tuple[float | None, float | None]:
    """(stop premium, stop stock price) for the SAME contract bought at another
    price with the stock at another price: the paper fill a little after the
    signal. The chart does exactly this when a fill is typed on the card."""
    paid = _positive(entry)
    if not paid:
        return None, None
    premium = round2(paid * STOP_LOSS_SHARE)
    return premium, stop_stock(plan.get("side"), spot, paid, plan.get("delta"), plan.get("gamma"), plan.get("strike"), premium)


def split_contracts(qty: Any) -> list[int]:
    """How many contracts come off at T1, T2 and T3 (his plan, 2026-11-12: "With 3
    contracts: sell 1 at T1 ... sell 1 at T2 ... sell the last at T3. With 2: T1
    and T2. With 1: T2"). More than 3 (a cheap option buys up to 12) are split
    the same way in thirds, any remainder riding with the last lot."""
    try:
        qty = max(0, int(qty or 0))
    except (TypeError, ValueError):
        qty = 0
    if qty <= 0:
        return [0, 0, 0]
    if qty == 1:
        return [0, 1, 0]
    if qty == 2:
        return [1, 1, 0]
    third = qty // 3
    return [third, third, qty - 2 * third]
