"""Pure logic for the automatic open-interest ladder alerts.

Every trading morning the server builds, per ticker, the daily-sheet High-OI
wall board (MomoX / Trading Alphas convention, same rule as the chart's High
OI list): the board splits AT SPOT — strikes above it are CALL walls showing
the call contract's OI, strikes at/below it are PUT walls showing the put
contract's OI — top 8 per side by OI at each strike's dominant expiry. The
ladder then advances only when a COMPLETED 5-minute regular-hours candle
closes through the active level.  A wick through the level ("touch") is
reported as an early warning but never advances the ladder.  Nothing in this
module does I/O; ``api_server.DashboardState`` owns the schedule, the
option-chain fetch, the bar fetch and persistence.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

EASTERN = ZoneInfo("America/New_York")
MAG7_SYMBOLS = ("AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA")

DEFAULT_REFRESH_TIME = time(9, 15)
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)
FIVE_MINUTES = timedelta(minutes=5)

# The daily sheet prints 8 walls per side; the ladder arms exactly those, so
# the chart, the High OI list and the alerts can never disagree on a level.
DEFAULT_LEVELS_PER_SIDE = 8
# Candidate strikes stay within ±(6 × 1-day expected move) of spot — the
# band that reproduces the sheet's panels once sides are split at spot.
EM_BAND_MULTIPLE = 2.0
MAX_MANUAL_SYMBOLS = 30

_SIDE_KEYS = {
    "CALL": {
        "levels": "callLevels",
        "confirmed": "confirmedCallStrikes",
        "touched": "touchedCallStrikes",
        "last_event": "lastCallEvent",
        "state": "callState",
        "active": "activeCall",
        "next": "nextCall",
        "fired": "callFiredAt",
    },
    "PUT": {
        "levels": "putLevels",
        "confirmed": "confirmedPutStrikes",
        "touched": "touchedPutStrikes",
        "last_event": "lastPutEvent",
        "state": "putState",
        "active": "activePut",
        "next": "nextPut",
        "fired": "putFiredAt",
    },
}


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #
def _number(value: object, fallback: float = 0.0) -> float:
    try:
        parsed = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return fallback
    return parsed if parsed == parsed else fallback  # NaN guard


def _strike_key(value: object) -> float:
    return round(_number(value), 4)


def _to_eastern(value: datetime | str) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        return value.replace(tzinfo=EASTERN)
    return value.astimezone(EASTERN)


def _iso(value: datetime) -> str:
    return _to_eastern(value).isoformat()


def compact_number(value: object) -> str:
    """6541 -> '6.5K', 17753 -> '17.8K', 1_250_000 -> '1.3M'."""
    numeric = abs(_number(value))
    if numeric >= 1_000_000:
        text = f"{numeric / 1_000_000:.{0 if numeric >= 10_000_000 else 1}f}"
        return f"{text.rstrip('0').rstrip('.') if '.' in text else text}M"
    if numeric >= 1_000:
        text = f"{numeric / 1_000:.{0 if numeric >= 100_000 else 1}f}"
        return f"{text.rstrip('0').rstrip('.') if '.' in text else text}K"
    return str(round(numeric))


def strike_label(value: object) -> str:
    numeric = _number(value)
    if numeric == int(numeric):
        return str(int(numeric))
    return f"{numeric:.2f}".rstrip("0").rstrip(".")


def normalize_symbol(value: object) -> str:
    symbol = str(value or "").strip().upper()
    if not symbol or len(symbol) > 10 or not symbol[0].isalpha():
        return ""
    if any(not (character.isalnum() or character in ".-") for character in symbol):
        return ""
    return symbol


def normalize_symbols(values: Iterable[object], limit: int = MAX_MANUAL_SYMBOLS) -> list[str]:
    output: list[str] = []
    cap = max(1, int(limit))
    for value in values or []:
        symbol = normalize_symbol(value)
        if symbol and symbol not in output:
            output.append(symbol)
        if len(output) >= cap:
            break
    return output


def next_monthly_opex(from_date: date | datetime) -> date:
    """Third Friday of ``from_date``'s month, rolling forward once it has passed."""
    current = from_date.date() if isinstance(from_date, datetime) else from_date

    def third_friday(year: int, month: int) -> date:
        first = date(year, month, 1)
        return first + timedelta(days=(4 - first.weekday()) % 7 + 14)

    this_month = third_friday(current.year, current.month)
    if this_month >= current:
        return this_month
    following = (current.replace(day=28) + timedelta(days=4)).replace(day=1)
    return third_friday(following.year, following.month)


def classify_oi_strength(open_interest: object, leading_open_interest: object) -> str:
    ratio = max(_number(open_interest), 0.0) / max(_number(leading_open_interest), 1.0)
    if ratio >= 0.66:
        return "strong"
    if ratio >= 0.33:
        return "moderate"
    return "weak"


# --------------------------------------------------------------------------- #
# Ladder construction
# --------------------------------------------------------------------------- #
# The sheet's expiry window rolls forward once the near monthly OPEX is under
# a week away: the 2026-08-20 sheets (OPEX 8/21) list 9/18 walls beside the
# weeklies, because an expiring monthly is spent positioning.
NEAR_OPEX_ROLL_DAYS = 7


def high_oi_window_end(from_date: date | datetime) -> date:
    """End of the daily-sheet expiry window (see NEAR_OPEX_ROLL_DAYS)."""
    current = from_date.date() if isinstance(from_date, datetime) else from_date
    opex = next_monthly_opex(current)
    if (opex - current).days >= NEAR_OPEX_ROLL_DAYS:
        return opex
    return next_monthly_opex(opex + timedelta(days=1))


# Importance 1-5 vs the side's leading OI (MomoX's "Imp" column). Solved from
# the 2026-08-20 sheets: these breaks reproduce their Imp column exactly for all
# 16 MSFT rows and all 16 SPY rows. Keep in step with HIGH_OI_IMPORTANCE_BREAKS
# in frontend/src/highOiContractList.js.
IMPORTANCE_BREAKS = (0.125, 0.225, 0.35, 0.50)


def _oi_importance(open_interest: object, leading_open_interest: object) -> int:
    ratio = max(_number(open_interest), 0.0) / max(_number(leading_open_interest), 1.0)
    return 1 + sum(1 for threshold in IMPORTANCE_BREAKS if ratio >= threshold)


def build_high_oi_walls(
    rows: Iterable[dict],
    underlying_price: object,
    *,
    as_of: date | datetime,
    top_per_side: int = DEFAULT_LEVELS_PER_SIDE,
    expected_move: object = 0.0,
) -> dict:
    """The daily-sheet High-OI walls (MomoX / Trading Alphas convention).

    Port of ``frontend/src/highOiContractList.js#buildHighOiContractList``,
    validated panel-by-panel against the 2026-08-20 sheets: the board splits
    AT SPOT — a strike above spot is a CALL wall showing the call contract's
    OI, a strike at/below spot is a PUT wall showing the put contract's OI.
    ITM contracts are positioning history and never appear (MSFT's 480 call
    held 24K while the sheet printed the 480 PUT's 8.4K). One row per strike
    at its dominant expiry (largest OI, then volume — never summed), scope
    through ``high_oi_window_end`` (every weekly up to the working monthly
    OPEX; fallback: all expiries), candidates bounded to ±(6 × the 1-day
    expected move) of spot, then top N per side by OI. With the sheet's own
    ExMo 8.21 the band reproduces its MSFT panel exactly (530 in / 535 out,
    440 in / 430 out); without a band, top-8-by-OI drifts to far monthly
    mega-walls (550/570 calls) and starves the near zone the trader trades.
    No delta filter of any kind. Strength/importance/rank are relative to
    the side's leader, and lists return in rank (OI) order.
    """
    spot = max(_number(underlying_price), 0.0)
    window_key = high_oi_window_end(as_of).isoformat()
    normalized: list[dict] = []
    for raw in rows or []:
        if not isinstance(raw, dict):
            continue
        strike = _number(raw.get("strike"))
        open_interest = max(_number(raw.get("open_interest", raw.get("openInterest"))), 0.0)
        if strike <= 0 or open_interest <= 0:
            continue
        normalized.append(
            {
                "side": "PUT" if str(raw.get("side") or "").upper() == "PUT" else "CALL",
                "strike": _strike_key(strike),
                "openInterest": int(round(open_interest)),
                "volume": int(round(max(_number(raw.get("volume")), 0.0))),
                "delta": round(abs(_number(raw.get("delta"))), 4),
                "expiry": str(raw.get("expiry") or raw.get("expiry_date") or "")[:10],
                "daysToExpiration": int(_number(raw.get("days_to_expiration", raw.get("daysToExpiration")))),
            }
        )

    scoped = [row for row in normalized if row["expiry"] and row["expiry"] <= window_key]
    if not scoped:
        scoped = normalized

    # The sheet's side rule: above spot only the call contract counts, at or
    # below spot only the put contract (a strike being stood on is support).
    # Without a spot the split is impossible — keep contract-type grouping so
    # a build never returns an empty board.
    def _side_correct(row: dict) -> bool:
        if row["side"] == "CALL":
            return row["strike"] > spot
        return row["strike"] <= spot

    sided = [row for row in scoped if _side_correct(row)] if spot > 0 else scoped

    em = max(0.0, _number(expected_move))
    limit = max(1, min(60, int(_number(top_per_side, DEFAULT_LEVELS_PER_SIDE))))
    walls: dict = {
        "spot": round(spot, 4),
        "monthlyExpiry": window_key,
        "expectedMove": round(em, 4),
        "emUp": round(spot + em, 4) if em > 0 and spot > 0 else 0.0,
        "emDown": round(spot - em, 4) if em > 0 and spot > 0 else 0.0,
        "calls": [],
        "puts": [],
    }
    for side, key in (("CALL", "calls"), ("PUT", "puts")):
        dominant_by_strike: dict[float, dict] = {}
        for row in sided:
            if row["side"] != side:
                continue
            existing = dominant_by_strike.get(row["strike"])
            if existing is None or (row["openInterest"], row["volume"]) > (existing["openInterest"], existing["volume"]):
                dominant_by_strike[row["strike"]] = row
        # MomoX / Trading Alphas "Plot Highest OI Levels" (verified against the
        # trader's 2026-08-25 sheet): the top-N strikes by OI on this side
        # WITHIN a +/-2*EM near-money band. The band is what excludes far
        # mega-walls (NVDA 180/170/140 puts, big OI but ~2.3*EM out) and the
        # tiny 2.5-wide fillers between real walls (NVDA 212.5/217.5) never make
        # the top-N. When a side has fewer than N strikes in the band (a tight
        # EM or sparse 5-wide names), fall back to the full side so the board
        # still fills to N and reaches the real walls (GOOGL 375, MSFT 525).
        # Importance/strength stay OI-relative to the side leader (the Imp 1-5
        # column). Kept in step with frontend/src/highOiContractList.js.
        candidates = list(dominant_by_strike.values())
        em_band = em * EM_BAND_MULTIPLE if em > 0 and spot > 0 else 0.0
        if em_band > 0:
            in_band = [row for row in candidates if abs(row["strike"] - spot) <= em_band]
            if len(in_band) >= limit:
                candidates = in_band
            else:
                # Sparse near band (tight EM / 5-wide names): keep every in-band
                # strike and FILL to `limit` with the NEAREST out-of-band ones -
                # never the biggest far walls. GOOGL reaches 375 and MSFT 525
                # this way, while NVDA's far 180/170/140 (dense band, no fill)
                # stay off.
                out_of_band = sorted(
                    (row for row in candidates if abs(row["strike"] - spot) > em_band),
                    key=lambda row: (abs(row["strike"] - spot), row["strike"]),
                )
                candidates = in_band + out_of_band[: max(0, limit - len(in_band))]
        ranked = sorted(
            candidates,
            key=lambda row: (-row["openInterest"], -row["volume"], row["strike"]),
        )[:limit]
        leading_oi = ranked[0]["openInterest"] if ranked else 0
        walls[key] = [
            {
                "strike": row["strike"],
                "openInterest": row["openInterest"],
                "volume": row["volume"],
                "delta": row["delta"],
                "expiry": row["expiry"],
                "daysToExpiration": row["daysToExpiration"],
                "strength": classify_oi_strength(row["openInterest"], leading_oi),
                "importance": _oi_importance(row["openInterest"], leading_oi),
                "rank": rank,
            }
            for rank, row in enumerate(ranked, start=1)
        ]
    return walls


def build_oi_ladder(
    rows: Iterable[dict],
    underlying_price: object,
    *,
    as_of: date | datetime,
    levels_per_side: int = DEFAULT_LEVELS_PER_SIDE,
    expected_move: object = 0.0,
) -> dict:
    """Daily-sheet OI ladder around ``underlying_price``.

    ``callLevels`` are the sheet's call walls ascending from price (nearest
    first) and ``putLevels`` its put walls descending — the ladder arms
    EXACTLY the walls the sheet prints, so the chart, the High OI list and
    the alerts can never disagree about which level counts. ``universe``
    mirrors the same walls (rank order) for persisted-row compatibility and
    ``augment_ladder``.
    """
    walls = build_high_oi_walls(
        rows,
        underlying_price,
        as_of=as_of,
        top_per_side=levels_per_side,
        expected_move=expected_move,
    )
    calls = sorted(walls["calls"], key=lambda level: _number(level.get("strike")))
    puts = sorted(walls["puts"], key=lambda level: _number(level.get("strike")), reverse=True)
    return {
        "spot": walls["spot"],
        "monthlyExpiry": walls["monthlyExpiry"],
        "expectedMove": walls.get("expectedMove", 0.0),
        "emUp": walls.get("emUp", 0.0),
        "emDown": walls.get("emDown", 0.0),
        "callLevels": calls,
        "putLevels": puts,
        "universe": {"calls": walls["calls"], "puts": walls["puts"]},
    }


def augment_ladder(row: dict, price: object) -> tuple[dict, list[dict]]:
    """Add universe walls missing from the ladder that are actionable at
    ``price`` — SAME SIDE ONLY. Under the sheet convention a wall's side is
    fixed by the build split: the put board only ever shows put contracts, so
    a call wall price has fallen below stays a (frozen) call level and never
    becomes a put trigger. Existing levels — confirmed or pending — are never
    removed, so gap confirmations still fire and history stays intact."""
    updated = dict(row)
    universe = updated.get("universe") or {}
    spot = max(_number(price), 0.0)
    added: list[dict] = []
    if spot <= 0 or not isinstance(universe, dict):
        return updated, added
    for side, universe_key in (("CALL", "calls"), ("PUT", "puts")):
        levels_key = _SIDE_KEYS[side]["levels"]
        current = [dict(level) for level in (updated.get(levels_key) or []) if isinstance(level, dict)]
        present = {_strike_key(level.get("strike")) for level in current}
        for wall in universe.get(universe_key) or []:
            strike = _strike_key(wall.get("strike"))
            if strike <= 0 or strike in present:
                continue
            actionable = strike > spot if side == "CALL" else strike < spot
            if not actionable:
                continue
            level = dict(wall)
            current.append(level)
            present.add(strike)
            added.append({**level, "side": side})
        updated[levels_key] = sorted(current, key=lambda level: _number(level.get("strike")), reverse=side == "PUT")
    return updated, added


def new_alert_row(
    symbol: str,
    ladder: dict,
    *,
    source: str = "",
    source_group: str = "manual",
    session_date: str = "",
    levels_updated_at: str = "",
) -> dict:
    """Fresh, un-progressed alert row for one ticker."""
    return {
        "symbol": normalize_symbol(symbol),
        "sourceGroup": source_group,
        "source": source,
        "status": "armed",
        "message": "",
        "sessionDate": session_date,
        "levelsUpdatedAt": levels_updated_at,
        "spot": _number((ladder or {}).get("spot")),
        "monthlyExpiry": (ladder or {}).get("monthlyExpiry") or "",
        "expectedMove": _number((ladder or {}).get("expectedMove")),
        "emUp": _number((ladder or {}).get("emUp")),
        "emDown": _number((ladder or {}).get("emDown")),
        "callLevels": list((ladder or {}).get("callLevels") or []),
        "putLevels": list((ladder or {}).get("putLevels") or []),
        "universe": dict((ladder or {}).get("universe") or {}),
        "confirmedCallStrikes": [],
        "confirmedPutStrikes": [],
        "touchedCallStrikes": [],
        "touchedPutStrikes": [],
        "lastCallEvent": None,
        "lastPutEvent": None,
        "lastProcessedBar": None,
        "lastClose": None,
        "lastBar": None,
        "lastTouchCheckAt": None,
        # One-shot latches. A fresh row per session build is what resets them,
        # so a new trading day always starts with both sides armed.
        "callFiredAt": None,
        "putFiredAt": None,
        "rearmedAt": "",
    }


# --------------------------------------------------------------------------- #
# Ladder progress
# --------------------------------------------------------------------------- #
def _strike_set(row: dict, key: str) -> set[float]:
    return {_strike_key(value) for value in (row.get(key) or []) if _number(value) > 0}


def side_has_fired(row: dict, side: str) -> bool:
    """Has this side already spent its one alert for the session?

    One confirmed close per side per session is the whole contract: the trader
    reads the 9:15 premarket plan, gets ONE call alert and ONE put alert, and
    then the side goes quiet until they re-arm it. Before this, each
    confirmation advanced the ladder to the next wall and re-armed itself, so a
    single ticker alerted all day (AMZN confirmed 260 AND 262.5 on BOTH sides on
    2026-08-17).
    """
    return bool(str(row.get(_SIDE_KEYS[side]["fired"]) or "").strip())


def reset_row_for_new_session(row: dict, session_date: str) -> dict:
    """Wipe yesterday's progress at the ET midnight roll.

    The trading day starts at midnight, not at the 9:15 build, so a card must
    not still read "CALL CONFIRMED 2:40 PM" at 8 a.m. from a session that ended
    the afternoon before. Progress goes: confirmed and touched strikes, the
    one-shot latches, the last events, and the bar cursor.

    The LEVELS are deliberately kept. They are yesterday's plan and still the
    best reference until 9:15 replaces them, and dropping them would blank the
    chart lines overnight. `status` says the row is waiting for its rebuild.
    """
    updated = dict(row)
    updated["sessionDate"] = str(session_date or "")
    for side in ("CALL", "PUT"):
        keys = _SIDE_KEYS[side]
        updated[keys["confirmed"]] = []
        updated[keys["touched"]] = []
        updated[keys["last_event"]] = None
        updated[keys["fired"]] = None
    updated["rearmedAt"] = ""
    updated["lastProcessedBar"] = None
    updated["lastClose"] = None
    updated["lastBar"] = None
    updated["lastTouchCheckAt"] = None
    if str(updated.get("status") or "") != "unavailable":
        updated["status"] = "armed"
        updated["message"] = "Waiting for the 9:15 AM ET build."
    return updated


def rearm_row(row: dict, *, at: str = "") -> dict:
    """Clear both one-shot latches so each side can fire once more.

    Deliberately keeps the confirmed/touched history: the level price already
    closed through is spent, so the re-armed side watches the NEXT wall rather
    than re-alerting on one that has already broken.
    """
    updated = dict(row)
    for side in ("CALL", "PUT"):
        updated[_SIDE_KEYS[side]["fired"]] = None
    updated["rearmedAt"] = str(at or "")
    return updated


def pending_levels(row: dict, side: str) -> list[dict]:
    keys = _SIDE_KEYS[side]
    confirmed = _strike_set(row, keys["confirmed"])
    return [
        level
        for level in (row.get(keys["levels"]) or [])
        if isinstance(level, dict) and _strike_key(level.get("strike")) not in confirmed
    ]


def active_and_next(row: dict, side: str) -> tuple[dict | None, dict | None]:
    pending = pending_levels(row, side)
    return (pending[0] if pending else None, pending[1] if len(pending) > 1 else None)


def _target_payload(level: dict | None, reference_price: float) -> dict | None:
    if not isinstance(level, dict):
        return None
    strike = _number(level.get("strike"))
    payload = dict(level)
    if reference_price > 0 and strike > 0:
        distance = abs(strike - reference_price)
        payload["distance"] = round(distance, 4)
        payload["distancePercent"] = round(distance / reference_price * 100.0, 4)
    else:
        payload["distance"] = None
        payload["distancePercent"] = None
    return payload


def _event(
    row: dict,
    *,
    side: str,
    kind: str,
    level: dict,
    price: float,
    at: str,
    crossed: list[dict] | None = None,
    bar: dict | None = None,
) -> dict:
    active, following = active_and_next(row, side)
    return {
        "id": f"{row.get('symbol')}:{side}:{kind}:{strike_label(level.get('strike'))}:{at}",
        "symbol": row.get("symbol"),
        "side": side,
        "direction": "up" if side == "CALL" else "down",
        "kind": kind,
        "level": dict(level),
        "crossedLevels": [dict(item) for item in (crossed or [level])],
        "price": round(price, 4),
        "nextTarget": _target_payload(active, price),
        "followingTarget": _target_payload(following, price),
        "barEndedAt": str((bar or {}).get("end") or at),
        "barStartedAt": str((bar or {}).get("start") or ""),
        "bar": (
            {key: (round(_number(bar.get(key)), 4) if key in {"open", "high", "low", "close"} else bar.get(key))
             for key in ("open", "high", "low", "close", "volume", "start", "end") if key in bar}
            if isinstance(bar, dict) else None
        ),
        "at": at,
    }


def _touch_side(row: dict, side: str, *, high: float, low: float, at: str, bar: dict | None) -> list[dict]:
    keys = _SIDE_KEYS[side]
    # A spent side is silent, warnings included. The touch exists to say "your
    # level is about to go" - once it HAS gone and the alert has fired, there is
    # nothing left to warn about until the trader re-arms.
    if side_has_fired(row, side):
        return []
    active, _ = active_and_next(row, side)
    if not active:
        return []
    strike = _strike_key(active.get("strike"))
    touched = _strike_set(row, keys["touched"])
    if strike in touched:
        return []
    # A level already confirmed on the OTHER side is a level price has just
    # traded through; re-touching it (a retest of broken resistance/support)
    # must not fire a second early-warning on the opposite side. Only a
    # completed close through it counts from here.
    other = _SIDE_KEYS["PUT" if side == "CALL" else "CALL"]
    if strike in _strike_set(row, other["confirmed"]):
        return []
    reached = high >= strike if side == "CALL" else low <= strike
    if not reached:
        return []
    touched.add(strike)
    row[keys["touched"]] = sorted(touched, reverse=side == "PUT")
    event = _event(
        row,
        side=side,
        kind="touch",
        level=active,
        price=high if side == "CALL" else low,
        at=at,
        bar=bar,
    )
    row[keys["last_event"]] = event
    return [event]


def _confirm_side(row: dict, side: str, *, close: float, at: str, bar: dict | None) -> list[dict]:
    keys = _SIDE_KEYS[side]
    # One confirmed close per side per session - see side_has_fired.
    if side_has_fired(row, side):
        return []
    confirmed = _strike_set(row, keys["confirmed"])
    crossed: list[dict] = []
    for level in pending_levels(row, side):
        strike = _strike_key(level.get("strike"))
        passed = close > strike if side == "CALL" else close < strike
        if not passed:
            break
        confirmed.add(strike)
        crossed.append(level)
    if not crossed:
        return []
    row[keys["confirmed"]] = sorted(confirmed, reverse=side == "PUT")
    # Spend the side BEFORE building the event, so the event's own next/following
    # targets are read from a row that already knows this side is done.
    row[keys["fired"]] = at
    event = _event(
        row,
        side=side,
        kind="confirm",
        level=crossed[-1],
        price=close,
        at=at,
        crossed=crossed,
        bar=bar,
    )
    row[keys["last_event"]] = event
    return [event]


def apply_completed_five_minute_bar(row: dict, *, bar: dict) -> tuple[dict, list[dict]]:
    """Advance the ladders from ONE completed 5-minute regular-hours candle.

    A close through the active level confirms it (and every further level the
    close also cleared, for gaps); the next pending level becomes the target.
    A wick through the (post-confirmation) active level without a confirming
    close is a touch.  The same bar is never processed twice.
    """
    updated = dict(row)
    close = _number((bar or {}).get("close"))
    end = str((bar or {}).get("end") or "")
    if close <= 0 or not end:
        return updated, []
    if str(updated.get("lastProcessedBar") or "") == end:
        return updated, []
    high = max(_number(bar.get("high"), close), close)
    low = min(_number(bar.get("low"), close), close)

    updated["lastProcessedBar"] = end
    updated["lastClose"] = round(close, 4)
    updated["lastBar"] = {
        "open": round(_number(bar.get("open"), close), 4),
        "high": round(high, 4),
        "low": round(low, 4),
        "close": round(close, 4),
        "volume": _number(bar.get("volume")),
        "start": str(bar.get("start") or ""),
        "end": end,
    }
    events: list[dict] = []
    for side in ("CALL", "PUT"):
        events.extend(_confirm_side(updated, side, close=close, at=end, bar=updated["lastBar"]))
    for side in ("CALL", "PUT"):
        events.extend(_touch_side(updated, side, high=high, low=low, at=end, bar=updated["lastBar"]))
    # The ladder is NOT re-split during the session any more. The two lines drawn
    # at the 9:15 premarket build are the plan the trader plans around, and
    # re-splitting moved them: each confirmation promoted the next wall and armed
    # it, so one ticker alerted all day and the chart no longer showed the
    # morning levels. A wall that turns actionable later is next session's plan.
    return updated, events


def apply_intrabar_touch(row: dict, *, high: object, low: object, at: str) -> tuple[dict, list[dict]]:
    """Early-warning touch check from live 1-minute highs/lows; never confirms."""
    updated = dict(row)
    high_value = _number(high)
    low_value = _number(low, high_value)
    if high_value <= 0 and low_value <= 0:
        return updated, []
    if high_value <= 0:
        high_value = low_value
    if low_value <= 0:
        low_value = high_value
    updated["lastTouchCheckAt"] = str(at or "")
    events: list[dict] = []
    for side in ("CALL", "PUT"):
        events.extend(_touch_side(updated, side, high=high_value, low=low_value, at=str(at or ""), bar=None))
    return updated, events


def _side_state(row: dict, side: str) -> str:
    keys = _SIDE_KEYS[side]
    # "fired" outranks every other state: the side has spent its one alert and
    # is watching nothing until re-armed. The UI shows a Re-arm button for it.
    if side_has_fired(row, side):
        return "fired"
    active, _ = active_and_next(row, side)
    if active is None:
        return "done" if row.get(keys["levels"]) else "empty"
    if _strike_key(active.get("strike")) in _strike_set(row, keys["touched"]):
        return "touched"
    if _strike_set(row, keys["confirmed"]):
        return "confirmed"
    return "armed"


def decorate_alert_row(row: dict) -> dict:
    """Row plus the derived active/next targets and per-side states for the UI."""
    decorated = dict(row)
    reference = _number(decorated.get("lastClose")) or _number(decorated.get("spot"))
    for side in ("CALL", "PUT"):
        keys = _SIDE_KEYS[side]
        active, following = active_and_next(decorated, side)
        decorated[keys["active"]] = _target_payload(active, reference)
        decorated[keys["next"]] = _target_payload(following, reference)
        decorated[keys["state"]] = _side_state(decorated, side)
        decorated.setdefault(keys["confirmed"], [])
        decorated.setdefault(keys["touched"], [])
        decorated.setdefault(keys["last_event"], None)
        decorated.setdefault(keys["fired"], None)
    # One flag for the UI: show the Re-arm button when either side is spent.
    decorated["rearmAvailable"] = any(side_has_fired(decorated, side) for side in ("CALL", "PUT"))
    return decorated


# --------------------------------------------------------------------------- #
# Messages
# --------------------------------------------------------------------------- #
def _target_text(target: dict | None) -> str:
    if not isinstance(target, dict):
        return "No further OI target in the ladder"
    text = f"Next OI target {strike_label(target.get('strike'))} ({compact_number(target.get('openInterest'))} OI)"
    if target.get("distance") is not None and target.get("distancePercent") is not None:
        text += f" · ${_number(target.get('distance')):.2f} / {_number(target.get('distancePercent')):.2f}% away"
    return text


def format_event_message(event: dict) -> str:
    symbol = str(event.get("symbol") or "")
    side = str(event.get("side") or "CALL")
    arrow = "↑" if side == "CALL" else "↓"
    level = event.get("level") or {}
    price = _number(event.get("price"))
    if event.get("kind") == "touch":
        edge = "high" if side == "CALL" else "low"
        need = "above" if side == "CALL" else "below"
        return (
            f"{symbol} {arrow} touching {side} OI {strike_label(level.get('strike'))} · {edge} {price:.2f} · "
            f"not confirmed (needs a 5m close {need} {strike_label(level.get('strike'))})"
        )
    crossed = event.get("crossedLevels") or [level]
    strikes = " → ".join(strike_label(item.get("strike")) for item in crossed)
    # One verb for both sides. A put support giving way and a call resistance
    # giving way are the SAME event - a completed 5m close through the level -
    # and the old CALL=CONFIRMED / PUT=BROKEN split read as two different grades
    # of alert. The arrow and the side word still carry the direction.
    return f"{symbol} {arrow} {side} OI {strikes} CONFIRMED · 5m close {price:.2f} · {_target_text(event.get('nextTarget'))}"


# --------------------------------------------------------------------------- #
# Schedule helpers (all Eastern)
# --------------------------------------------------------------------------- #
def next_refresh_at(now: datetime, *, refresh_time: time = DEFAULT_REFRESH_TIME) -> datetime:
    """Next weekday ``refresh_time`` strictly after ``now``."""
    current = _to_eastern(now)
    candidate = current.replace(
        hour=refresh_time.hour, minute=refresh_time.minute, second=0, microsecond=0
    )
    if candidate <= current:
        candidate += timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate += timedelta(days=1)
    return candidate


def latest_completed_five_minute_bar_end(now: datetime, *, grace_seconds: int = 8) -> datetime | None:
    """End time of the most recent COMPLETED regular-hours 5-minute candle.

    ``None`` before the first candle closes (09:35 ET plus a short grace so
    the feed has the final print), and on weekends.  After the close it
    stays at 16:00 so a late worker can still process the final candle;
    callers dedupe by ``lastProcessedBar``.
    """
    current = _to_eastern(now)
    if current.weekday() >= 5:
        return None
    settled = current - timedelta(seconds=max(0, int(grace_seconds)))
    if settled.date() != current.date():
        return None
    floored = settled.replace(minute=(settled.minute // 5) * 5, second=0, microsecond=0)
    session_open = current.replace(hour=RTH_OPEN.hour, minute=RTH_OPEN.minute, second=0, microsecond=0)
    session_close = current.replace(hour=RTH_CLOSE.hour, minute=RTH_CLOSE.minute, second=0, microsecond=0)
    if floored > session_close:
        floored = session_close
    if floored <= session_open:
        return None
    return floored


def five_minute_bar_from_minute_bars(minute_bars: Iterable[dict], *, end: datetime) -> dict | None:
    """Aggregate 1-minute bars (timestamp = bar START) into the candle ending at ``end``."""
    end_et = _to_eastern(end)
    start_et = end_et - FIVE_MINUTES
    window: list[dict] = []
    for bar in minute_bars or []:
        if not isinstance(bar, dict):
            continue
        stamp = bar.get("timestamp", bar.get("time"))
        if stamp is None:
            continue
        try:
            if isinstance(stamp, (int, float)):
                stamp_et = datetime.fromtimestamp(float(stamp), tz=EASTERN)
            else:
                stamp_et = _to_eastern(stamp)
        except (TypeError, ValueError, OverflowError):
            continue
        if start_et <= stamp_et < end_et:
            window.append((stamp_et, bar))
    if not window:
        return None
    window.sort(key=lambda item: item[0])
    opens = [_number(bar.get("open")) for _, bar in window if _number(bar.get("open")) > 0]
    closes = [_number(bar.get("close")) for _, bar in window if _number(bar.get("close")) > 0]
    highs = [_number(bar.get("high")) for _, bar in window if _number(bar.get("high")) > 0]
    lows = [_number(bar.get("low")) for _, bar in window if _number(bar.get("low")) > 0]
    if not closes:
        return None
    return {
        "open": opens[0] if opens else closes[0],
        "high": max(highs) if highs else max(closes),
        "low": min(lows) if lows else min(closes),
        "close": closes[-1],
        "volume": sum(max(_number(bar.get("volume")), 0.0) for _, bar in window),
        "start": _iso(start_et),
        "end": _iso(end_et),
        "minuteBars": len(window),
    }


# How long to wait before retrying a ladder build that failed.
#
# Measured 2026-08-27: the 09:15 build failed while both option-chain providers
# were refused, retried six times at 120s, gave up after twelve minutes, and
# nothing tried again for twenty-four hours. The providers recovered during the
# session - a manual rebuild at 19:48 armed all nine tickers first time - but
# nothing was watching, so every Mag7 ladder sat on the previous day's open
# interest for a full session.
#
# Twelve minutes is the wrong horizon for a dependency that is down for hours.
# The answer is not MORE fast retries, which only hammer a refused credential,
# but a slow heartbeat that notices recovery.
FAST_RETRY_ATTEMPTS = 6
FAST_RETRY_DELAY = timedelta(seconds=120)
SLOW_RETRY_DELAY = timedelta(minutes=15)
MAX_RETRY_ATTEMPTS = 34  # 12 min fast + 7 h slow: covers 09:15 to well past the close


def retry_delay_for_attempt(attempt) -> timedelta | None:
    """Wait before the next rebuild attempt, or None when it is time to stop.

    Not infinite: a credential refused all day is a human problem, and calling
    a dead provider forever is noise. The schedule deliberately outlasts a
    full trading session so a provider that recovers at lunchtime is picked up.
    """
    try:
        count = int(attempt)
    except (TypeError, ValueError):
        # Runs inside the alert scheduler loop, where an exception stops every
        # ladder - far worse than one mistimed retry.
        return FAST_RETRY_DELAY
    if count < 0:
        return FAST_RETRY_DELAY
    if count < FAST_RETRY_ATTEMPTS:
        return FAST_RETRY_DELAY
    if count < MAX_RETRY_ATTEMPTS:
        return SLOW_RETRY_DELAY
    return None
