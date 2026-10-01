"""The LIVE ⚡ - a per-second signal state, the ScannerX3 model (2026-09-25).

Ganesh: "Build it, i want every second live not 4 mins". The ScannerX3 guide
(his PDF) describes the bolt as a STATE, not a badge: a name earns it by
passing gates and then firing any signal family; it is full colour when fresh,
fades toward a quarter over two hours, and is evicted the moment price loses
the upper half of its last-hour range. Re-checked every second.

Two layers, exactly like theirs:

* the DEEP STUDY (momx worker, every build): :func:`seed_from_bars` turns a
  row's completed 5m bars into a small "seed" - the indicator state at the
  last completed bar (EMA 4/8/9/20/12/26 + MACD signal, the last 19 closes /
  true ranges for the 20-bar squeeze, volume mean/std, the last hour's
  highs/lows, the average hour range, the 1-hour close leg);
* the LIVE layer (api_server, every second): :class:`LiveBoltBook` builds the
  FORMING 5m bar from the Schwab stream (last price, cumulative volume) and
  advances every indicator by one step from the seed - O(20) per symbol.

Gates (published only loosely; our approximation, back-tested separately):
price >= $5, the last completed 1-hour bar closed up, the 4-hour RVOL >= 1.
Families (bull): EMA9 x EMA20 up, EMA4 x EMA8 up, MACD x signal up, squeeze
fire up (BB(20,2) back outside KC(20,1.5 ATR) with the close rising), RVOL
spike (forming volume z >= 2) on a green body.

Pure and deterministic: no clock, no I/O except the optional persistence
path; never raises into a caller.
"""
from __future__ import annotations

import json
import math
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
BAR = 300
SEED_VERSION = 1
MIN_PRICE = 5.0
FADE_SECONDS = 2 * 3600
MIN_OPACITY = 0.25
VOL_Z_SPIKE = 2.0
RVOL4H_GATE = 1.0
KC_MULT = 1.5
BB_MULT = 2.0
SQZ_LEN = 20


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _alpha(length: int) -> float:
    return 2.0 / (length + 1.0)


def _ema_run(values: list[float], length: int) -> float | None:
    """thinkScript ExpAverage seeded by the first value (momx.indicators.ema)."""
    out = None
    a = _alpha(length)
    for v in values:
        out = v if out is None else out + a * (v - out)
    return out


def _t(bar: Mapping) -> int:
    raw = bar.get("time", bar.get("timestamp"))
    return int(raw.timestamp()) if hasattr(raw, "timestamp") else int(raw)


# ---------------------------------------------------------------------------
# deep study -> seed
# ---------------------------------------------------------------------------

def seed_from_bars(completed: list[Mapping]) -> dict | None:
    """The live seed from COMPLETED 5m bars (oldest first), or None."""
    try:
        if len(completed) < 60:
            return None
        closes = [float(b["close"]) for b in completed]
        highs = [float(b["high"]) for b in completed]
        lows = [float(b["low"]) for b in completed]
        vols = [float(b.get("volume") or 0.0) for b in completed]
        e12, e26 = [], []
        a12, a26 = _alpha(12), _alpha(26)
        x12 = x26 = None
        for c in closes:
            x12 = c if x12 is None else x12 + a12 * (c - x12)
            x26 = c if x26 is None else x26 + a26 * (c - x26)
            e12.append(x12)
            e26.append(x26)
        macd = [a - b for a, b in zip(e12, e26)]
        trs = [highs[0] - lows[0]] + [
            max(h - l, abs(h - pc), abs(l - pc)) for h, l, pc in zip(highs[1:], lows[1:], closes[:-1])
        ]
        # was the squeeze ON at the last completed bar?
        win = closes[-SQZ_LEN:]
        mean = sum(win) / SQZ_LEN
        sd = math.sqrt(sum((c - mean) ** 2 for c in win) / SQZ_LEN)
        atr = sum(trs[-SQZ_LEN:]) / SQZ_LEN
        sqz_on = (mean + BB_MULT * sd) < (mean + KC_MULT * atr) and (mean - BB_MULT * sd) > (mean - KC_MULT * atr)
        recent_vols = vols[-20:]
        vmean = sum(recent_vols) / len(recent_vols)
        vsd = math.sqrt(sum((v - vmean) ** 2 for v in recent_vols) / len(recent_vols))
        # the last completed CLOCK hour: close > open
        last_t = _t(completed[-1])
        hour_of = lambda b: _t(b) // 3600  # noqa: E731
        current_hour = (last_t + BAR) // 3600
        prior = [b for b in completed if hour_of(b) == current_hour - 1]
        leg_1h = bool(prior) and float(prior[-1]["close"]) > float(prior[0]["open"])
        # average rolling-hour range over the tape (12-bar windows)
        spans = [max(highs[i - 11:i + 1]) - min(lows[i - 11:i + 1]) for i in range(11, len(highs), 6)]
        avg_hour = sum(spans) / len(spans) if spans else None
        return {
            "v": SEED_VERSION,
            "t": last_t,
            "close": closes[-1],
            "ema4": _ema_run(closes, 4), "ema8": _ema_run(closes, 8),
            "ema9": _ema_run(closes, 9), "ema20": _ema_run(closes, 20),
            "ema12": e12[-1], "ema26": e26[-1], "macdSignal": _ema_run(macd, 9), "macd": macd[-1],
            "closes19": closes[-(SQZ_LEN - 1):], "trs19": trs[-(SQZ_LEN - 1):],
            "sqzOn": bool(sqz_on),
            "volMean": vmean, "volSd": vsd,
            "hourHighs": highs[-11:], "hourLows": lows[-11:],
            "avgHourRange": avg_hour,
            "leg1h": leg_1h,
        }
    except Exception:  # noqa: BLE001 - no seed is always safe
        return None


# ---------------------------------------------------------------------------
# live step
# ---------------------------------------------------------------------------

def evaluate(seed: Mapping, bar: Mapping, rvol4h: float | None) -> dict:
    """One evaluation of the FORMING bar against the seed.

    ``bar`` = {"open", "high", "low", "close", "volume"} of the forming 5m bar.
    Returns {"gates": {...}, "families": [...], "hlPos", "hlMomentum"}.
    """
    close = float(bar["close"])
    step = lambda prev, n: prev + _alpha(n) * (close - prev)  # noqa: E731
    e4, e8 = step(seed["ema4"], 4), step(seed["ema8"], 8)
    e9, e20 = step(seed["ema9"], 9), step(seed["ema20"], 20)
    e12, e26 = step(seed["ema12"], 12), step(seed["ema26"], 26)
    macd = e12 - e26
    sig = seed["macdSignal"] + _alpha(9) * (macd - seed["macdSignal"])
    fam = []
    if seed["ema9"] <= seed["ema20"] and e9 > e20:
        fam.append("9x20")
    if seed["ema4"] <= seed["ema8"] and e4 > e8:
        fam.append("4x8")
    if seed["macd"] <= seed["macdSignal"] and macd > sig:
        fam.append("MACD")
    closes = list(seed["closes19"]) + [close]
    trs = list(seed["trs19"]) + [max(float(bar["high"]) - float(bar["low"]),
                                     abs(float(bar["high"]) - seed["close"]),
                                     abs(float(bar["low"]) - seed["close"]))]
    mean = sum(closes) / len(closes)
    sd = math.sqrt(sum((c - mean) ** 2 for c in closes) / len(closes))
    atr = sum(trs) / len(trs)
    sqz_now = (BB_MULT * sd) < (KC_MULT * atr)
    if seed["sqzOn"] and not sqz_now and close > seed["close"]:
        fam.append("SQZ fire")
    if seed["volSd"] and seed["volSd"] > 0:
        z = (float(bar.get("volume") or 0.0) - seed["volMean"]) / seed["volSd"]
        if z >= VOL_Z_SPIKE and close > float(bar["open"]):
            fam.append("RVOL %.1f" % z)
    hi = max(list(seed["hourHighs"]) + [float(bar["high"])])
    lo = min(list(seed["hourLows"]) + [float(bar["low"])])
    pos = 0.5 if hi <= lo else (close - lo) / (hi - lo)
    ratio = ((hi - lo) / seed["avgHourRange"]) if seed.get("avgHourRange") else 1.0
    gates = {
        "price": close >= MIN_PRICE,
        "leg1h": bool(seed.get("leg1h")),
        "vol4h": rvol4h is not None and rvol4h >= RVOL4H_GATE,
    }
    return {"gates": gates, "families": fam, "hlPos": round(pos, 3), "hlMomentum": round(pos * ratio, 3)}


def roll_seed(seed: Mapping, bar: Mapping, bar_time: int) -> dict:
    """The seed advanced by one COMPLETED bar (the live layer's own forming
    bar once its 5 minutes are over), so the state never waits for the next
    deep study. Volume stats are nudged, not recomputed (no raw history)."""
    close, high, low = float(bar["close"]), float(bar["high"]), float(bar["low"])
    volume = float(bar.get("volume") or 0.0)
    nxt = dict(seed)
    for key, n in (("ema4", 4), ("ema8", 8), ("ema9", 9), ("ema20", 20), ("ema12", 12), ("ema26", 26)):
        nxt[key] = seed[key] + _alpha(n) * (close - seed[key])
    nxt["macd"] = nxt["ema12"] - nxt["ema26"]
    nxt["macdSignal"] = seed["macdSignal"] + _alpha(9) * (nxt["macd"] - seed["macdSignal"])
    tr = max(high - low, abs(high - seed["close"]), abs(low - seed["close"]))
    closes = list(seed["closes19"]) + [close]
    trs = list(seed["trs19"]) + [tr]
    mean = sum(closes) / len(closes)
    sd = math.sqrt(sum((c - mean) ** 2 for c in closes) / len(closes))
    nxt["sqzOn"] = (BB_MULT * sd) < (KC_MULT * (sum(trs) / len(trs)))
    nxt["closes19"], nxt["trs19"] = closes[1:], trs[1:]
    nxt["hourHighs"] = (list(seed["hourHighs"]) + [high])[1:]
    nxt["hourLows"] = (list(seed["hourLows"]) + [low])[1:]
    nxt["volMean"] = seed["volMean"] + (volume - seed["volMean"]) / 20.0
    nxt["close"] = close
    nxt["t"] = int(bar_time)
    return nxt


class LiveBoltBook:
    """Per-symbol forming bars + bolt state, advanced once per second."""

    def __init__(self, directory: Any = None) -> None:
        self._dir = Path(directory) if directory else None
        self._lock = threading.Lock()
        self._day = ""
        self._forming: dict[str, dict] = {}
        self._fires: dict[str, dict] = {}   # symbol -> {"firstAt", "lastAt", "families": [...], "count"}
        self._state: dict[str, dict] = {}
        self._seeds: dict[str, dict] = {}
        self._dirty = False

    def _roll_day(self, now: datetime) -> None:
        day = now.astimezone(ET).date().isoformat()
        if day != self._day:
            self._day, self._forming, self._fires, self._state = day, {}, {}, {}
            self._load()

    def step(self, seeds: Mapping[str, Mapping], quotes: Mapping[str, Mapping],
             now: datetime) -> dict[str, dict]:
        """Advance every seeded symbol with its latest quote; return the state."""
        with self._lock:
            self._roll_day(now)
            ts = now.timestamp()
            bucket = int(ts // BAR) * BAR
            local = now.astimezone(ET)
            minute = local.hour * 60 + local.minute
            session = 9 * 60 + 30 <= minute < 16 * 60
            out: dict[str, dict] = {}
            for symbol, incoming in seeds.items():
                # Own copy, rolled forward bar by bar; a NEWER deep-study seed
                # replaces it (the worker's bars are the source of truth).
                held = self._seeds.get(symbol)
                if incoming and (held is None or int(incoming.get("t") or 0) > int(held.get("t") or 0)):
                    held = {**incoming}
                    self._seeds[symbol] = held
                seed = held
                q = quotes.get(symbol) or {}
                last = _num(q.get("last")) or _num(q.get("mark"))
                total = _num(q.get("totalVolume"))
                if not seed or last is None:
                    continue
                form = self._forming.get(symbol)
                if form is not None and form["bucket"] != bucket and form["bucket"] > int(seed.get("t") or 0):
                    done_volume = ((form.get("lastTotal") or 0.0) - form["vol0"]) if form.get("vol0") is not None else 0.0
                    seed = roll_seed(seed, {**form, "volume": done_volume}, form["bucket"])
                    self._seeds[symbol] = seed
                if form is None or form["bucket"] != bucket:
                    form = {"bucket": bucket, "open": last, "high": last, "low": last,
                            "vol0": total, "close": last}
                    self._forming[symbol] = form
                form["high"] = max(form["high"], last)
                form["low"] = min(form["low"], last)
                form["close"] = last
                form["lastTotal"] = total
                if form["vol0"] is None:
                    form["vol0"] = total
                volume = (total - form["vol0"]) if (total is not None and form["vol0"] is not None) else 0.0
                # A seed older than the forming bar's own bucket start minus one
                # bar means the deep study is behind: step from it anyway (it is
                # the newest completed state we have) - the families only fire
                # on a CHANGE of relation, so a stale seed cannot invent a cross
                # that the seed itself already shows.
                ev = evaluate(seed, {**form, "volume": volume}, _num(seed.get("rvol4h")))
                gates_ok = all(ev["gates"].values())
                fired = self._fires.get(symbol)
                if session and gates_ok and ev["families"]:
                    if fired is None:
                        fired = {"firstAt": ts, "lastAt": ts, "families": list(ev["families"]), "count": 1}
                        self._fires[symbol] = fired
                        self._dirty = True
                    elif ts - fired["lastAt"] >= 60 or set(ev["families"]) - set(fired["families"]):
                        fired["lastAt"] = ts
                        fired["families"] = list(dict.fromkeys(list(ev["families"]) + fired["families"]))[:6]
                        fired["count"] += 1
                        self._dirty = True
                age = (ts - fired["lastAt"]) if fired else None
                on = bool(fired) and age is not None and age <= FADE_SECONDS and ev["hlPos"] >= 0.5
                opacity = None
                if on:
                    opacity = round(max(MIN_OPACITY, 1.0 - (1.0 - MIN_OPACITY) * (age / FADE_SECONDS)), 2)
                out[symbol] = {
                    "on": on,
                    "now": bool(on and gates_ok),
                    "firedAt": datetime.fromtimestamp(fired["lastAt"], ET).isoformat(timespec="seconds") if fired else None,
                    "firstAt": datetime.fromtimestamp(fired["firstAt"], ET).isoformat(timespec="seconds") if fired else None,
                    "families": fired["families"] if fired else [],
                    "opacity": opacity,
                    "hlPos": ev["hlPos"],
                    "hlMomentum": ev["hlMomentum"],
                    "gates": ev["gates"],
                    "last": last,
                }
            self._state = out
            if self._dirty:
                self._save()
                self._dirty = False
            return out

    def snapshot(self) -> dict[str, dict]:
        """Live state, plus every bolt already fired today that has not been
        stepped since (after a restart, or outside the session no quote has
        ticked yet) - so the day's ⚡ list never goes blank (2026-09-28: after
        an evening restart it read 0 while the file held 15)."""
        with self._lock:
            out = dict(self._state)
            for symbol, fired in self._fires.items():
                if symbol in out or not isinstance(fired, dict) or not fired.get("lastAt"):
                    continue
                try:
                    out[symbol] = {
                        "hlMomentum": None, "hlPos": None, "on": False, "now": False,
                        "firedAt": datetime.fromtimestamp(float(fired["lastAt"]), ET).isoformat(timespec="seconds"),
                        "firstAt": datetime.fromtimestamp(float(fired.get("firstAt") or fired["lastAt"]), ET).isoformat(timespec="seconds"),
                        "families": list(fired.get("families") or []), "opacity": None,
                    }
                except (TypeError, ValueError, OSError):
                    continue
            return out

    def load_day(self, now: datetime) -> None:
        """Read today's saved fires without stepping (at server start)."""
        with self._lock:
            self._roll_day(now)

    # ------------------------------------------------------------ persistence

    def _path(self) -> Path | None:
        return (self._dir / "momx_live_bolt" / f"{self._day}.json") if self._dir and self._day else None

    def _load(self) -> None:
        path = self._path()
        if not path:
            return
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(doc.get("fires"), dict):
                self._fires = {k: v for k, v in doc["fires"].items() if isinstance(v, dict)}
        except (OSError, ValueError, UnicodeDecodeError, AttributeError):
            pass

    def _save(self) -> None:
        path = self._path()
        if not path:
            return
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"date": self._day, "fires": self._fires}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass


def symbols_of(seeds: Mapping[str, Any]) -> Iterable[str]:
    return sorted(s for s, v in seeds.items() if v)
