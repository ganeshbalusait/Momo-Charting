"""MARKET TURN: the market's morning dip is over - the strongest names now.

Ganesh 2026-09-25, after DAL / TEM / FLY / BE / PYPL / OUST ran 11:30-12:30
while his 9:40 picks faded: "build both" (this alert + the 30m-sellers
warning). The back-test that picked the rule (20 sessions, 2026-08-27 ..
09-24, 358 Watchlist names, Alpaca 5m bars, +2% / -1.5% exits):

    SPY trades BELOW its session VWAP at/after 09:45, then the first 5m bar
    starting 10:00 or later (up to 14:30) CLOSES back above VWAP = the turn.
    At that moment take the 5 strongest names that are above their own VWAP,
    up >= 1% on the day, with 30m buyers in control (+DI > -DI, ADX >= 20),
    leveraged / index ETFs left out; rank by % today.

    top 5 + 30m filter: 85 trades, 44% hit +2% first, 41% hit -1.5% first,
    20% ran +4%, +0.32% avg, green 13 of 19 days - best on DOWN days (the
    market dips then recovers). Plain VWAP / morning-low / 9-20 cloud holds
    had NO edge (~0% avg). A small edge on a small sample - shown and scored,
    not proven. On 09-25 it would have picked QMCO BE APPS DELL PYPL at 11:00.

Stamps row["marketTurn"] = {"rank", "at", "price"} on the picks and
payload["marketTurn"] = {"at", "spyAt", "picks": [...]} on the board;
latched once per board per ET day and persisted (a worker restart keeps it,
the lesson of the in-memory 🔥 flag lost on 2026-09-25). Never raises.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_market_turn"
DIP_FROM_MIN = 9 * 60 + 45
TURN_FROM_MIN, TURN_UNTIL_MIN = 10 * 60, 14 * 60 + 30
TOP_N = 5
MIN_PCT = 1.0
MIN_ADX = 20.0
EXCLUDED_INDUSTRY_WORDS = ("ETF",)
#: A turn first noticed more than this long after SPY's bar closed is logged
#: but picks nothing (see apply).
LATE_SECONDS = 15 * 60


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def spy_turn(bars: list[dict], now: datetime) -> dict | None:
    """The turn from completed SPY 5m bars of TODAY, or None.

    ``bars``: [{"time": epoch seconds (bar start), "high", "low", "close",
    "volume"}] - regular session only is used. A bar counts only once it has
    closed (start + 5 min <= now).
    """
    local = now.astimezone(ET)
    day = local.date()
    pv = vol = 0.0
    dipped = False
    for bar in sorted(bars, key=lambda b: b["time"]):
        start = datetime.fromtimestamp(bar["time"], ET)
        minute = start.hour * 60 + start.minute
        if start.date() != day or minute < 9 * 60 + 30 or minute >= 16 * 60:
            continue
        if bar["time"] + 300 > local.timestamp():
            break  # still forming
        high, low, close = _num(bar.get("high")), _num(bar.get("low")), _num(bar.get("close"))
        volume = _num(bar.get("volume")) or 0.0
        if None in (high, low, close):
            continue
        pv += (high + low + close) / 3 * volume
        vol += volume
        vwap = pv / vol if vol else None
        if vwap is None:
            continue
        if minute >= DIP_FROM_MIN and low < vwap:
            dipped = True
        if dipped and TURN_FROM_MIN <= minute <= TURN_UNTIL_MIN and close > vwap:
            closed = datetime.fromtimestamp(bar["time"] + 300, ET)
            return {"spyAt": closed.replace(microsecond=0).isoformat(), "spyClose": close, "spyVwap": round(vwap, 4)}
    return None


def pick(rows: list[dict], n: int = TOP_N) -> list[dict]:
    """The strongest names right now by the tested rule, best first."""
    out = []
    for row in rows:
        symbol = row.get("symbol")
        industry = str(row.get("industry") or "")
        if not isinstance(symbol, str) or any(word in industry for word in EXCLUDED_INDUSTRY_WORDS):
            continue
        last, pct = _num(row.get("last")), _num(row.get("pctChange"))
        vwap = _num(_m(row.get("m5")).get("vwap"))
        adx = _m(_m(row.get("adx")).get("30m"))
        plus, minus, strength = _num(adx.get("plus")), _num(adx.get("minus")), _num(adx.get("adx"))
        if None in (last, pct, vwap, plus, minus, strength):
            continue
        if last > vwap and pct >= MIN_PCT and plus > minus and strength >= MIN_ADX:
            out.append((pct, symbol, last))
    out.sort(key=lambda item: (-item[0], item[1]))
    return [{"symbol": s, "pct": round(p, 2), "price": last} for p, s, last in out[:n]]


def _default_spy_bars() -> list[dict]:
    from momx import feed
    res = feed.fetch_5m(["SPY"], days=1, schwab_volume=False)
    frame = dict(res.bars).get("SPY")
    if frame is None:
        return []
    d = frame.reset_index()
    cols = {str(c).lower(): c for c in d.columns}
    tcol = cols.get("timestamp") or cols.get("time") or d.columns[0]
    out = []
    for r in d.to_dict("records"):
        t = r[tcol]
        t = int(t.timestamp()) if hasattr(t, "timestamp") else int(t)
        if t > 1e12:
            t //= 1000
        out.append({"time": t, "high": r[cols["high"]], "low": r[cols["low"]],
                    "close": r[cols["close"]], "volume": r.get(cols.get("volume", "volume"), 0)})
    return out


class MarketTurnBook:
    def __init__(self, directory: Path, spy_bars: Callable[[], list] | None = None,
                 notify: Callable[[str, dict], None] | None = None) -> None:
        self.directory = Path(directory)
        self._spy_bars = spy_bars or _default_spy_bars
        self._notify = notify
        self._lock = threading.Lock()
        self._day = ""
        self._boards: dict[str, dict] = {}

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            minute = local.hour * 60 + local.minute
            rows = [r for section in ("rows", "rest") for r in (_m(payload).get(section) or [])
                    if isinstance(r, dict)]
            fired = None
            with self._lock:
                self._load(local.date().isoformat())
                held = self._boards.get(board)
                if (held is None and local.weekday() < 5
                        and TURN_FROM_MIN <= minute <= TURN_UNTIL_MIN + 10):
                    turn = spy_turn(self._spy_bars() or [], local)
                    if turn is not None:
                        # Seen late (a restart, a stalled worker): record the
                        # turn but pick nothing - names chosen 2 hours after
                        # the turn are not what the rule tested.
                        age = local.timestamp() - datetime.fromisoformat(turn["spyAt"]).timestamp()
                        fresh = age <= LATE_SECONDS
                        held = {"at": local.replace(microsecond=0).isoformat(), **turn,
                                "picks": pick(rows) if fresh else [], "late": not fresh}
                        self._boards[board] = held
                        self._save()
                        fired = held if fresh else None
                ranks = {p["symbol"]: (i + 1, p) for i, p in enumerate((held or {}).get("picks") or [])}
                for row in rows:
                    hit = ranks.get(row.get("symbol"))
                    row["marketTurn"] = ({"rank": hit[0], "at": held["at"], "price": hit[1]["price"]}
                                         if hit else None)
                if isinstance(payload, dict):
                    payload["marketTurn"] = dict(held) if held else None
            if fired is not None and self._notify is not None:
                try:
                    self._notify(board, fired)
                except Exception:  # noqa: BLE001 - a push never costs a scan
                    pass
        except Exception:  # noqa: BLE001 - a market-turn bug never costs a scan
            return

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._boards = day, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("boards"), dict):
                self._boards = {k: v for k, v in doc["boards"].items() if isinstance(v, dict)}
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, "boards": self._boards}), encoding="utf-8")
        os.replace(tmp, path)


def push_message(board: str, turn: Mapping) -> tuple[str, str]:
    """(title, body) for the phone."""
    at = str(turn.get("spyAt") or turn.get("at") or "")[11:16]
    picks = turn.get("picks") or []
    names = ", ".join(f"{p['symbol']} +{p['pct']:.1f}%" for p in picks) or "no name passed the filter"
    body = (f"SPY back above VWAP at {at} ET after the morning dip. Strongest now ({board}): {names}. "
            "Tested rule, small edge (44% win / 41% loss) - not a recommendation.")
    return f"Market turned up {at}", body
