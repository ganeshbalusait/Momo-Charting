from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable


# No delta band by default: MomoX ranks pure open interest, and its far walls
# (MSFT 530 against a 484 spot) sit well under any workable delta floor. The
# band stays available for callers that want to tighten the ladder by hand.
DEFAULT_MIN_DELTA = 0.0
DEFAULT_MAX_DELTA = 1.0
DEFAULT_HIGH_OI_EXCEPTION_RATIO = 0.30
DEFAULT_LEVELS_PER_SIDE = 8
# Only the bright MomoX walls are worth an alert. Imp 1-2 are the faint
# "small OI" levels it draws as thin dashes, and alerting on them is what put
# a CALL chip on a strike sitting right on top of spot.
DEFAULT_MIN_IMPORTANCE = 3
# Ratios of a level's OI to the leading OI on its own side. These breaks
# reproduce the published MomoX MSFT and SPY Imp columns exactly.
IMPORTANCE_BREAKS = (0.125, 0.225, 0.35, 0.50)
# Once this month's OPEX is inside a week the window rolls to the following
# month: MomoX still lists the September cycle on the Friday before August
# OPEX, and stopping at an expiry one day out hides every swing-trade wall.
OPEX_ROLL_FORWARD_DAYS = 7


def _number(value: object, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed else fallback


def normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol or len(symbol) > 10 or not symbol[0].isalpha():
        return ""
    if any(not (character.isalnum() or character in ".-") for character in symbol):
        return ""
    return symbol


def normalize_symbols(values: Iterable[object], limit: int = 25) -> list[str]:
    output: list[str] = []
    for value in values:
        symbol = normalize_symbol(value)
        if symbol and symbol not in output:
            output.append(symbol)
        if len(output) >= max(1, int(limit)):
            break
    return output


def next_monthly_opex(from_date: date | datetime) -> date:
    current = from_date.date() if isinstance(from_date, datetime) else from_date

    def third_friday(year: int, month: int) -> date:
        first = date(year, month, 1)
        first_friday_offset = (4 - first.weekday()) % 7
        return first + timedelta(days=first_friday_offset + 14)

    this_month = third_friday(current.year, current.month)
    if this_month >= current + timedelta(days=OPEX_ROLL_FORWARD_DAYS):
        return this_month
    next_month = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return third_friday(next_month.year, next_month.month)


def oi_importance(open_interest: float, leading_open_interest: float) -> int:
    """Score a wall 1-5 against the leading wall on its own side.

    The side-relative denominator is why a 6.9k put can rate 5 while a 16.6k
    call rates 3 on the same ticker: the put side simply carries less size.
    """

    leader = max(_number(leading_open_interest), 0.0)
    size = max(_number(open_interest), 0.0)
    if leader <= 0 or size <= 0:
        return 1
    ratio = size / leader
    return 1 + sum(1 for threshold in IMPORTANCE_BREAKS if ratio >= threshold)


def classify_oi_strength(open_interest: float, leading_open_interest: float) -> str:
    ratio = max(_number(open_interest), 0.0) / max(_number(leading_open_interest), 1.0)
    if ratio >= 0.66:
        return "strong"
    if ratio >= 0.33:
        return "moderate"
    return "weak"


def build_oi_ladder(
    rows: Iterable[dict],
    underlying_price: float,
    *,
    as_of: date | datetime,
    anchor_price: float | None = None,
    min_delta: float = DEFAULT_MIN_DELTA,
    max_delta: float = DEFAULT_MAX_DELTA,
    high_oi_exception_ratio: float = DEFAULT_HIGH_OI_EXCEPTION_RATIO,
    levels_per_side: int = DEFAULT_LEVELS_PER_SIDE,
    min_importance: int = DEFAULT_MIN_IMPORTANCE,
) -> dict:
    """Build the same dominant-expiry OI ladder the Charts & OI High OI list shows.

    The level value is the option strike placed on the underlying chart. For a
    duplicate strike, the expiry reporting the most OI wins; OI is never summed
    across expirations.

    Selection follows the MomoX rule and is directional *before* ranking: a
    strike above the anchor is resistance and can only become a call level, a
    strike below it is support and can only become a put level. Ranking first
    and filtering afterwards is what let ITM strikes into the ladder and left
    it short of levels.
    """

    spot = max(_number(underlying_price), 0.0)
    # MomoX pins the split to BMO (the pre-open price it prints between the
    # call and put blocks) so an intraday swing cannot flip a wall from one
    # side to the other and churn the ladder mid-session.
    anchor = max(_number(anchor_price), 0.0) or spot
    opex_key = next_monthly_opex(as_of).isoformat()
    normalized: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        side = "PUT" if str(raw.get("side") or "").upper() == "PUT" else "CALL"
        strike = _number(raw.get("strike"))
        open_interest = max(_number(raw.get("open_interest", raw.get("openInterest"))), 0.0)
        expiry = str(raw.get("expiry") or "")[:10]
        if strike <= 0 or open_interest <= 0 or not expiry:
            continue
        normalized.append(
            {
                "side": side,
                "strike": round(strike, 4),
                "openInterest": round(open_interest),
                "volume": round(max(_number(raw.get("volume")), 0.0)),
                "delta": round(abs(_number(raw.get("delta"))), 4),
                "expiry": expiry,
                "daysToExpiration": int(_number(raw.get("days_to_expiration", raw.get("daysToExpiration")))),
            }
        )

    scoped = [row for row in normalized if row["expiry"] <= opex_key]
    if not scoped:
        scoped = normalized

    floor = max(0.0, _number(min_delta, DEFAULT_MIN_DELTA))
    cap = min(1.0, max(floor, _number(max_delta, DEFAULT_MAX_DELTA)))
    exception_ratio = min(1.0, max(0.0, _number(high_oi_exception_ratio, DEFAULT_HIGH_OI_EXCEPTION_RATIO)))
    limit = max(1, min(30, int(_number(levels_per_side, DEFAULT_LEVELS_PER_SIDE))))
    importance_floor = max(1, min(5, int(_number(min_importance, DEFAULT_MIN_IMPORTANCE))))

    output: dict[str, list[dict] | str | float] = {
        "monthlyExpiry": opex_key,
        "spot": round(spot, 4),
        "anchor": round(anchor, 4),
        "minImportance": importance_floor,
        "callLevels": [],
        "putLevels": [],
    }
    for side, key in (("CALL", "callLevels"), ("PUT", "putLevels")):
        side_rows = [
            row
            for row in scoped
            if row["side"] == side
            and (
                anchor <= 0
                or (row["strike"] > anchor if side == "CALL" else row["strike"] < anchor)
            )
        ]
        leading_side_oi = max((row["openInterest"] for row in side_rows), default=0)
        qualified = [
            row
            for row in side_rows
            if floor <= row["delta"] <= cap
            or row["openInterest"] >= leading_side_oi * exception_ratio
        ]
        if not qualified:
            qualified = side_rows

        dominant_by_strike: dict[float, dict] = {}
        for row in qualified:
            existing = dominant_by_strike.get(row["strike"])
            if existing is None or (row["openInterest"], row["volume"]) > (
                existing["openInterest"],
                existing["volume"],
            ):
                dominant_by_strike[row["strike"]] = row

        ranked = sorted(
            dominant_by_strike.values(),
            key=lambda row: (-row["openInterest"], -row["volume"], row["strike"]),
        )[:limit]
        leading_ranked_oi = ranked[0]["openInterest"] if ranked else 0
        for row in ranked:
            row["imp"] = oi_importance(row["openInterest"], leading_ranked_oi)
            row["strength"] = classify_oi_strength(row["openInterest"], leading_ranked_oi)

        # Alert only on the bright walls. The leading level always scores 5, so
        # a side that ranked anything at all keeps at least one target.
        alertable = [row for row in ranked if row["imp"] >= importance_floor]
        output[key] = sorted(
            alertable,
            key=lambda row: row["strike"],
            reverse=side == "PUT",
        )
    return output


def _confirmed_strikes(row: dict, side: str) -> set[float]:
    key = "confirmedCallStrikes" if side == "CALL" else "confirmedPutStrikes"
    return {round(_number(value), 4) for value in row.get(key, []) if _number(value) > 0}


def pending_level_pair(row: dict, side: str) -> tuple[dict | None, dict | None]:
    key = "callLevels" if side == "CALL" else "putLevels"
    confirmed = _confirmed_strikes(row, side)
    pending = [level for level in row.get(key, []) if round(_number(level.get("strike")), 4) not in confirmed]
    return (pending[0] if pending else None, pending[1] if len(pending) > 1 else None)


def apply_completed_five_minute_close(
    row: dict,
    *,
    close: float,
    bar_ended_at: str,
) -> tuple[dict, list[dict]]:
    """Advance OI ladders only from a completed five-minute RTH candle close."""

    updated = dict(row)
    close_value = _number(close)
    if close_value <= 0 or not bar_ended_at:
        return updated, []
    if str(updated.get("lastProcessedBar") or "") == str(bar_ended_at):
        return updated, []

    updated["lastProcessedBar"] = str(bar_ended_at)
    updated["lastClose"] = round(close_value, 4)
    events: list[dict] = []

    for side in ("CALL", "PUT"):
        levels_key = "callLevels" if side == "CALL" else "putLevels"
        confirmed_key = "confirmedCallStrikes" if side == "CALL" else "confirmedPutStrikes"
        confirmed = _confirmed_strikes(updated, side)
        crossed: list[dict] = []
        for level in updated.get(levels_key, []):
            strike = round(_number(level.get("strike")), 4)
            if strike <= 0 or strike in confirmed:
                continue
            passed = close_value > strike if side == "CALL" else close_value < strike
            if not passed:
                break
            confirmed.add(strike)
            crossed.append(level)

        updated[confirmed_key] = sorted(confirmed, reverse=side == "PUT")
        if not crossed:
            continue
        active, following = pending_level_pair(updated, side)
        events.append(
            {
                "side": side,
                "direction": "above" if side == "CALL" else "below",
                "confirmedLevel": crossed[-1],
                "crossedLevels": crossed,
                "nextTarget": active,
                "followingTarget": following,
                "close": round(close_value, 4),
                "barEndedAt": str(bar_ended_at),
            }
        )

    return updated, events


def decorate_alert_row(row: dict) -> dict:
    decorated = dict(row)
    active_call, next_call = pending_level_pair(decorated, "CALL")
    active_put, next_put = pending_level_pair(decorated, "PUT")
    decorated.update(
        {
            "activeCall": active_call,
            "nextCall": next_call,
            "activePut": active_put,
            "nextPut": next_put,
        }
    )
    return decorated
