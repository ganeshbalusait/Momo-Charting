"""AI picker for the AI bot paper account (2026-11-10).

His "Use Agentic AI ... Just plug and play ... want to see which strategy works
good then we will use same for real money". There are far more Level signals
than slots (176 on 2026-11-09 against 8), so something has to choose. The two
paper accounts are now a side-by-side test of how to choose:

* Paper 3, the RULES bot: the newest signal gets the free slot.
* Paper 5, the AI bot: an AI model looks at the signals waiting for a slot and
  ranks them; the best-ranked one gets the slot.

Same signals, same contract, same sizing, same exits. The Scorecard shows which
account does better; that way of choosing is the one to take to real money.

The AI never blocks a trade (his rule): it only puts the waiting signals in
order. If it is slow, wrong or down, the AI bot falls back to the rules bot's
order, so a slot is never left empty waiting for an answer. This module runs on
its own thread and is never called from inside the order loop.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
#: The account whose slots the AI fills.
AI_ACCOUNT = "paper5"
#: Signals shown to the AI at once (newest first).
MAX_CANDIDATES = 12
#: The AI is asked again only when the waiting signals changed, and no more often than this.
MIN_GAP_SECONDS = 20.0
LOOP_SECONDS = 10.0
#: An unchanged set of waiting signals is ranked again after this long, so the
#: ranking is still fresh (the mirror uses one up to 120 s old) when a slot frees.
RERANK_SECONDS = 75.0
#: A hard ceiling on AI calls per day (each is one short request). The two buy
#: windows are 4 hours: one call per RERANK_SECONDS is about 190, plus changes.
MAX_CALLS_PER_DAY = 400

SCHEMA = {"type": "object", "properties": {"ranking": {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "integer"}, "why": {"type": "string"}}, "required": ["id", "why"]}}}, "required": ["ranking"]}
SYSTEM = ("You help a momentum options trader decide which signals to trade first. Every signal below already passed "
          "his rules and will be traded if a slot is free; there are more signals than slots, so put them in order, "
          "best first. He buys short-dated out-of-the-money options (about 0.20 delta) and wants the move to start "
          "now: favour a strong and fresh move, more confirming setups, a higher level, a tight spread, and a market "
          "moving the same way; be wary of a fading move, a wide spread and a very cheap option. Each signal also "
          "carries its 5-minute chart at the signal: where price sits against VWAP and the 9/21/50/200 EMAs, the "
          "nearest chart level AHEAD of the trade (pivot R1/R2 or S1/S2, yesterday's high or low, the premarket high "
          "or low) and the nearest one BEHIND it, relative volume, ADX and squeezes, and the high open-interest "
          "option walls ahead with their size. For calls 'ahead' is above the price, for puts below. Favour price on "
          "the right side of VWAP and the EMAs with room to the next level and wall; be wary when a chart level or a "
          "big wall sits just ahead (under about 0.5% away), because moves often stall there. Use only the facts "
          "given. Return every id exactly once in 'ranking', best first, each with a reason of at most 12 words in "
          "plain trader language that names the chart level or wall when it decided the rank.")

#: Chart levels read from the signal's saved 5m chart (momx/chart_learning.py snapshot), by the name the AI is given.
_CHART_LEVELS = (("R1", "pivot R1"), ("R2", "pivot R2"), ("S1", "pivot S1"), ("S2", "pivot S2"), ("P", "pivot P"),
                 ("prevHigh", "yesterday's high"), ("prevLow", "yesterday's low"),
                 ("premarketHigh", "premarket high"), ("premarketLow", "premarket low"))


def chart_facts(side: str, t: Mapping, spot: float | None) -> dict:
    """What the chart showed at the signal, as small facts: the side of VWAP and
    the EMAs, the nearest level ahead of the trade and behind it, volume and
    trend strength, and the high-OI walls ahead. Missing data is left out, never guessed."""
    setup = t.get("setup") if isinstance(t.get("setup"), Mapping) else {}
    chart = setup.get("chartContext") if isinstance(setup.get("chartContext"), Mapping) else {}
    levels = chart.get("levels") if isinstance(chart.get("levels"), Mapping) else {}
    bear = side == "bear"
    out: dict = {}
    if spot:
        vwap = _num(levels.get("vwap"))
        if vwap:
            out["vsVwapPct"] = _pct(spot - vwap, vwap, 2)
        emas = [(_num(levels.get(k)), k) for k in ("ema9", "ema21", "ema50", "ema200")]
        known = [(v, k) for v, k in emas if v]
        if known:
            right = [k for v, k in known if (spot < v if bear else spot > v)]
            out["emasOnRightSide"] = f"{len(right)} of {len(known)}"
        ahead, behind = [], []
        for key, name in _CHART_LEVELS:
            v = _num(levels.get(key))
            if not v:
                continue
            away = (spot - v if bear else v - spot) / spot * 100          # positive = ahead of the trade
            (ahead if away > 0 else behind).append((abs(away), name))
        if ahead:
            away, name = min(ahead)
            out["nextChartLevelAhead"] = {"level": name, "awayPct": round(away, 2)}
        if behind:
            away, name = min(behind)
            out["nearestChartLevelBehind"] = {"level": name, "awayPct": round(away, 2)}
        walls = []
        ahead_walls = t.get("putWalls" if bear else "callWalls")
        for w in (ahead_walls if isinstance(ahead_walls, list) else []):
            strike, oi = (_num(w.get("strike")), _num(w.get("oi"))) if isinstance(w, Mapping) else (None, None)
            if strike and (strike < spot if bear else strike > spot):
                walls.append({"strike": strike, "awayPct": round(abs(strike - spot) / spot * 100, 2), "openInterest": int(oi) if oi else None})
        if walls:
            out["highOiWallsAhead"] = sorted(walls, key=lambda w: w["awayPct"])[:3]
        behind_walls = t.get("callWalls" if bear else "putWalls")
        back = [_num(w.get("strike")) for w in (behind_walls if isinstance(behind_walls, list) else []) if isinstance(w, Mapping)]
        back = [s for s in back if s and (s > spot if bear else s < spot)]
        if back:
            nearest = min(back, key=lambda s: abs(s - spot))
            out["highOiWallBehindPct"] = round(abs(nearest - spot) / spot * 100, 2)
    for key, name in (("pct", "dayMovePct"), ("rvol5m", "rvol5m"), ("rvol15m", "rvol15m"), ("rvol30m", "rvol30m"),
                      ("rvol1h", "rvol1h"), ("adx5m", "adx5m"), ("adx30m", "adx30m"), ("sqzOn", "squeezesOn"),
                      ("hiLo", "placeInDayRange")):
        v = _num(setup.get(key))
        if v is not None:
            out[name] = round(v, 2)
    return out


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def _pct(part: float | None, whole: float | None, digits: int = 1) -> float | None:
    return round(part / whole * 100, digits) if part is not None and whole else None


def summarize(n: int, side: str, t: Mapping, now_ts: float, leg: str = "entry") -> dict:
    """One waiting signal as the AI sees it: small, factual, no free text from outside."""
    from momx.paper_mirror import leg_get
    bid, ask = _num(leg_get(t, "lastBid", leg)), _num(leg_get(t, "lastAsk", leg))
    mid = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None
    spot, strike = _num(t.get("spot") or t.get("stockPrice")), _num(leg_get(t, "strike", leg))
    signal = t.get("levelSignal") if isinstance(t.get("levelSignal"), Mapping) else {}
    walls = t.get("putWalls" if side == "bear" else "callWalls")
    strikes = [_num(w.get("strike")) for w in (walls if isinstance(walls, list) else []) if isinstance(w, Mapping)]
    ahead = [s for s in strikes if s is not None and spot and (s < spot if side == "bear" else s > spot)]
    types = signal.get("setupTypes") if isinstance(signal.get("setupTypes"), list) else []
    wall = (max(ahead) if side == "bear" else min(ahead)) if ahead else None
    market = t.get("market") if isinstance(t.get("market"), Mapping) else {}
    try:
        age = round(now_ts - datetime.fromisoformat(str(t.get("at"))).timestamp())
    except (TypeError, ValueError):
        age = None
    return {
        "id": n, "ticker": str(t.get("symbol") or "")[:8], "side": "puts" if side == "bear" else "calls",
        "level": signal.get("highestLevel"),
        "setups": [str(s.get("text"))[:24] for s in types if isinstance(s, Mapping)][:8],
        "momentum": str(t.get("momentum") or "")[:16], "secondsOld": age,
        "optionPrice": round(mid, 2) if mid is not None else None,
        "spreadPct": _pct(ask - bid, mid) if mid else None, "delta": _num(leg_get(t, "delta", leg)),
        "strikeAwayPct": _pct(abs(strike - spot), spot, 2) if strike is not None and spot else None,
        "expectedMovePct": _pct(_num(t.get("expectedMove")), spot, 2) if spot else None,
        "roomToWallPct": _pct(abs(wall - spot), spot, 2) if wall is not None and spot else None,
        "marketSpyPct": round(_num(market.get("spyPct")), 2) if _num(market.get("spyPct")) is not None else None,
        "marketBreadth": _num(market.get("breadth")),
        # His "You use any chart to see the levels, pivots, indicators? High oi levels" (2026-11-10).
        "chart": chart_facts(side, t, spot),
    }


def _default_ask(system: str, user: str) -> dict:
    from agents.ai_provider import claude_first_provider
    return claude_first_provider(effort="low").complete(system=system, user=user, max_tokens=900, schema=SCHEMA)


def rank(candidates: list[tuple[str, str, Mapping]], ask: Callable[[str, str], dict], now_ts: float, leg: str = "entry") -> dict:
    """Ask the AI to order the waiting signals. Returns {"order": [keys], "why": {key: text}, "source"}
    or {"error": text}. An answer that leaves signals out keeps them, in their given order, after the ranked ones;
    an id it invented is ignored. Never raises."""
    try:
        facts = [summarize(i + 1, side, t, now_ts, leg) for i, (_, side, t) in enumerate(candidates)]
        answer = ask(SYSTEM, json.dumps({"signals": facts}, separators=(",", ":"), default=str))
    except Exception as exc:  # noqa: BLE001 - the picker must never break the worker
        return {"error": type(exc).__name__}
    data = answer.get("data") if isinstance(answer, Mapping) else None
    listed = data.get("ranking") if isinstance(data, Mapping) else None
    if not (isinstance(answer, Mapping) and answer.get("ok") and isinstance(listed, list)):
        return {"error": " ".join(str((answer or {}).get("error") if isinstance(answer, Mapping) else "no answer").split())[:120] or "no answer"}
    order, why = [], {}
    for item in listed:
        n = item.get("id") if isinstance(item, Mapping) else None
        if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= len(candidates):
            continue
        key = candidates[n - 1][0]
        if key in why:
            continue
        order.append(key)
        why[key] = " ".join(str(item.get("why") or "").split())[:120]
    if not order:
        return {"error": "the AI's answer named none of the signals"}
    order += [key for key, _, _ in candidates if key not in why]
    return {"order": order, "why": why, "source": " ".join(str(answer.get("provider") or "AI").split())[:40]}


class Picker:
    """Keeps one account's AI ranking fresh. `mirror` is a PaperMirror."""

    def __init__(self, mirror, ask: Callable[[str, str], dict] | None = None) -> None:
        self.mirror = mirror
        self._ask = ask or _default_ask
        self._last_keys: tuple = ()
        self._last_at = 0.0
        self._day = ""
        self.calls = 0
        self.error = ""

    def refresh(self, now: datetime | None = None) -> bool:
        """One pass: when the AI bot has a free slot and two or more signals are waiting, and they are not the
        ones already ranked, ask the AI and hand the mirror the order. True when a new ranking was stored."""
        now = (now or datetime.now(ET)).astimezone(ET)
        now_ts = now.timestamp()
        day = now.date().isoformat()
        if day != self._day:
            self._day, self.calls = day, 0
        found = self.mirror.candidates(now)
        if found is None:
            return False                    # the order loop is busy: keep what we know, try the next pass
        candidates = found[:MAX_CANDIDATES]
        keys = tuple(sorted(key for key, _, _ in candidates))
        if len(candidates) < 2:
            self._last_keys = keys          # one signal or none: nothing to choose between
            return False
        since = now_ts - self._last_at
        # Ask when the waiting signals changed, or when the same ones were last
        # ranked long enough ago that the ranking is about to go stale.
        if since < MIN_GAP_SECONDS or (keys == self._last_keys and since < RERANK_SECONDS):
            return False
        if self.calls >= MAX_CALLS_PER_DAY:
            self.error = "daily AI call limit reached; using the rules bot's order"
            return False
        self.calls += 1
        self._last_keys, self._last_at = keys, now_ts
        result = rank(candidates, self._ask, now_ts, self.mirror.leg)
        if "error" in result:
            self.error = result["error"]
            self._last_keys = ()            # ask again for the same signals after the minimum gap
            return False
        self.error = ""
        self.mirror.set_ai_rank({**result, "at": now_ts, "of": len(candidates)})
        return True

    def status(self) -> dict:
        return {"on": True, "calls": self.calls, "error": self.error}


_PICKERS: list[Picker] = []


def start(mirrors, account: str = AI_ACCOUNT) -> list[Picker]:
    """One background thread for the AI bot account. Off with AGX_PAPER_AI_PICKER=0. Idempotent."""
    if _PICKERS or os.environ.get("AGX_PAPER_AI_PICKER", "1").strip() == "0":
        return _PICKERS
    for mirror in mirrors:
        if mirror.account == account:
            picker = Picker(mirror)
            mirror.picker = picker
            _PICKERS.append(picker)
    if not _PICKERS:
        return _PICKERS

    def loop() -> None:
        while True:
            for picker in _PICKERS:
                try:
                    picker.refresh()
                except Exception as exc:  # noqa: BLE001 - never take the worker down
                    picker.error = type(exc).__name__
            time.sleep(LOOP_SECONDS)

    threading.Thread(target=loop, name="paper-ai-picker", daemon=True).start()
    return _PICKERS
