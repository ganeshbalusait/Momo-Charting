"""Sector rotation: which industries money is flowing INTO, on the scanner row.

Ganesh 2026-09-24: "hedge funds rotate sectors - can we catch that sector and
trade its stocks?" We cannot see their orders; we can see the footprints.
Back-test 2026-09-01..23 (38 industry groups, the scanner's own industry
names - the ones his "Sector ETFs Top Holdings" sheet uses):

    a sector is HOT at/after 09:45 ET when
      * its members beat SPY over the last 3 sessions (median, rs3 > 0),
      * that lead is GROWING (rs3 today > rs3 one session earlier),
      * at least 60% of its members are up on the day AND above VWAP;
    at most the top 2 (by breadth, then rs3).

    stocks up + above VWAP at 9:45 in a HOT sector: 43% hit +2% first
    (avg +0.35%) vs 26% (-0.03%) in every other sector - a real direction
    edge, but only 16% ran +4%, so it is CONTEXT, not a trade signal.
    The sector's top gainer did worse (37% win): do not chase the leader.

What this stamps on EVERY row with an industry (rows and rest):
    row["sectorRotation"] = {"hot", "rs3", "rs3Prev", "breadth", "up", "total"}
so the panel's existing sector strip and the row's industry pill can show it
with no extra endpoint. rs3 comes from daily closes fetched ONCE per ET day
in a background thread (never on the build path); breadth is recomputed from
the rows on every build. Never raises.
"""
from __future__ import annotations

import json
import os
import statistics
import threading
from datetime import datetime
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
EXCLUDED = frozenset({"Index ETF", "ETF-Lev"})   # baskets, not a sector
MIN_MEMBERS = 4
#: 2026-09-24 v2 - ONE combined score instead of two yes/no gates (his ask:
#: "use both and come with a good one"). score = breadth + 5 x (rs3 growth
#: since yesterday); the single best sector with >= HOT_BREADTH up is hot.
#: Back-test Sep 1-23 at 09:45: 45% win / 27% loss / +0.55% avg / 24% ran
#: +4% on 14 of 16 days, vs the two-gate rule 43% / 37% / +0.35% / 16%.
HOT_BREADTH = 0.5
GROWTH_WEIGHT = 5.0
#: 09:35 (his ask, 2026-09-25: "some tickers start going up at the open").
#: Tested every 5m Sep 1-23, combined score, up to 2 sectors: start 09:35 ->
#: rising members won 45%, lost 25%, avg +0.59%; start 09:45 -> 27% / 47% /
#: -0.08%. The first candle's close (09:35) is the earliest honest read.
HOT_FROM_MIN = 9 * 60 + 35
#: A sector may only TURN hot before 11:00 ET; once lit it stays lit for the
#: day. Back-test Sep 1-23 checked on every candle: sectors that turned hot
#: before 11:00 -> 38% win; at 11:00 or later -> 0 of 17 wins, avg -0.62%.
HOT_UNTIL_MIN = 11 * 60
MOVING_BREADTH = 0.6
MOVING_MIN_PCT = 0.5
MAX_MOVING = 3
#: Two a day: Thursday 2026-09-24 the one slot went to Cybersecurity at
#: 09:45 (its leaders closed red) while Quantum / Space, the real movers,
#: only broadened at 10:25 and never got the flame.
MAX_HOT = 2
RS_SESSIONS = 3
BENCHMARK = "SPY"
#: "Unusual volume" on a member = an RVOL cell (15m / 30m / 1h - his RVOL
#: columns, the z-score of that bar's volume) at or above this. SHOWN, not
#: part of the hot rule: not back-tested yet (2026-09-24).
VOL_TIMEFRAMES = ("15m", "30m", "1h")
VOL_Z = 1.0
LEADERS = 5


def unusual_volume(row: Mapping) -> bool:
    cells = _m(row.get("rvol"))
    for tf in VOL_TIMEFRAMES:
        value = _num(_m(cells.get(tf)).get("value"))
        if value is not None and value >= VOL_Z:
            return True
    return False


def enabled() -> bool:
    """``AGX_SECTOR_ROTATION=0`` turns it off (the test suite sets it: fixture
    boards must never fetch real daily bars)."""
    return str(os.getenv("AGX_SECTOR_ROTATION", "1")).strip().lower() not in {"0", "false", "no", "off"}


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def _daily_closes(frame: Any) -> dict[str, float]:
    """{YYYY-MM-DD ET: close} from a feed DataFrame or a bar list."""
    out: dict[str, float] = {}
    try:
        import pandas as pd
        if isinstance(frame, pd.DataFrame):
            d = frame.reset_index()
            cols = {str(c).lower(): c for c in d.columns}
            tcol = cols.get("time") or cols.get("timestamp") or cols.get("date") or d.columns[0]
            stamps = d[tcol]
            if pd.api.types.is_numeric_dtype(stamps):
                stamps = pd.to_datetime(stamps, unit="s", utc=True)
            stamps = pd.to_datetime(stamps, utc=True).dt.tz_convert(ET)
            for when, close in zip(stamps, d[cols["close"]]):
                out[when.date().isoformat()] = float(close)
            return out
    except Exception:  # noqa: BLE001
        return {}
    for bar in frame or []:
        if isinstance(bar, Mapping) and bar.get("time") is not None:
            out[datetime.fromtimestamp(float(bar["time"]), ET).date().isoformat()] = float(bar["close"])
    return out


def _default_fetch(symbols: list[str]) -> dict:
    from momx import feed
    return dict(feed.fetch_daily(symbols).bars)


def relative_strength(closes: dict[str, dict[str, float]], members: list[str], today: str,
                      shift: int = 0, absolute: bool = False) -> float | None:
    """Median member return over the RS_SESSIONS sessions that closed before
    ``today`` (moved back ``shift`` sessions), minus SPY's over the same span."""
    spy = closes.get(BENCHMARK) or {}
    days = sorted(d for d in spy if d < today)
    if len(days) < RS_SESSIONS + 1 + shift:
        return None
    end = days[-1 - shift]
    start = days[-1 - shift - RS_SESSIONS]

    def ret(series: dict[str, float]) -> float | None:
        a, b = series.get(start), series.get(end)
        return b / a - 1 if a and b else None

    base = ret(spy)
    moves = [r for r in (ret(closes.get(s) or {}) for s in members) if r is not None]
    if base is None or len(moves) < 3:
        return None
    # absolute=True: the group's own 3-day move, the way his "Sector ETFs"
    # sheet reads (no SPY). Ranking is the same either way - at one moment
    # SPY's move is one number subtracted from every sector.
    return statistics.median(moves) if absolute else statistics.median(moves) - base


class RotationBook:
    """Daily rs3 per industry + per-build breadth; stamps row["sectorRotation"]."""

    def __init__(self, fetch: Callable | None = None, background: bool = True, always_on: bool = False,
                 directory: Any = None) -> None:
        self._fetch = fetch or _default_fetch
        # Where the day's 🔥 latch is saved (artifacts/momx_sector_hot/<day>.json).
        # 2026-09-25: a worker restart at 11:01 wiped that morning's two hot
        # sectors (AI-Data Centers 09:35, Defense 09:40) and none can light
        # after 11:00 - the latch lived only in memory. None = memory only.
        self._dir = directory
        self._background = background
        self._always_on = always_on
        self._lock = threading.Lock()
        # Per BOARD: Watchlist, Mag7 and Movers hold different members, so
        # each keeps its own day's rs3 table. board -> {"day", "rs", "loading"}
        self._state: dict[str, dict] = {}

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            if not (self._always_on or enabled()):
                return
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            rows = [r for section in ("rows", "rest") for r in (_m(payload).get(section) or [])
                    if isinstance(r, dict)]
            groups: dict[str, list[dict]] = {}
            for row in rows:
                name = str(row.get("industry") or "").strip()
                if name and name not in EXCLUDED and isinstance(row.get("symbol"), str):
                    groups.setdefault(name, []).append(row)
            day = local.date().isoformat()
            self._ensure_rs(str(board), day, groups)
            with self._lock:
                state = self._state.get(str(board)) or {}
                rs = dict(state.get("rs") or {}) if state.get("day") == day else {}
            with self._lock:
                state = self._state.setdefault(str(board), {"day": "", "rs": {}, "loading": False})
                if state.get("hotDay") != day:
                    state["hotDay"], state["hot"], state["leaders"] = day, set(), {}
                    self._load_hot(str(board), day, state)
                latched = state["hot"]
                picked = state.setdefault("leaders", {})
                before = (sorted(latched), json.dumps(picked, sort_keys=True, default=str))
            self._stamp(rows, groups, rs, local, latched, picked)
            with self._lock:
                if (sorted(latched), json.dumps(picked, sort_keys=True, default=str)) != before:
                    self._save_hot(str(board), day, latched, picked)
        except Exception:  # noqa: BLE001 - rotation context never costs a scan
            return

    # ---------------------------------------------------------------- internal

    def _hot_path(self, day: str):
        from pathlib import Path
        return Path(self._dir) / "momx_sector_hot" / f"{day}.json"

    def _load_hot(self, board: str, day: str, state: dict) -> None:
        if not self._dir:
            return
        try:
            doc = json.loads(self._hot_path(day).read_text(encoding="utf-8"))
            held = (doc.get("boards") or {}).get(board) or {}
            state["hot"] = set(held.get("hot") or [])
            state["leaders"] = dict(held.get("leaders") or {})
        except (OSError, ValueError, UnicodeDecodeError, AttributeError, TypeError):
            pass

    def _save_hot(self, board: str, day: str, latched: set, picked: dict) -> None:
        if not self._dir:
            return
        try:
            path = self._hot_path(day)
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                doc = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError, UnicodeDecodeError):
                doc = {}
            boards = doc.get("boards") if isinstance(doc.get("boards"), dict) else {}
            boards[board] = {"hot": sorted(latched), "leaders": picked}
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"date": day, "boards": boards}, default=str), encoding="utf-8")
            os.replace(tmp, path)
        except Exception:  # noqa: BLE001 - persistence never costs a scan
            pass

    def _ensure_rs(self, board: str, day: str, groups: dict[str, list[dict]]) -> None:
        with self._lock:
            state = self._state.setdefault(board, {"day": "", "rs": {}, "loading": False})
            if state["day"] == day or state["loading"]:
                return
            state["loading"] = True
        symbols = sorted({r["symbol"] for rs in groups.values() for r in rs} | {BENCHMARK})
        member_map = {name: sorted({r["symbol"] for r in rs}) for name, rs in groups.items()}

        def load() -> None:
            try:
                bars = self._fetch(symbols)
                closes = {s: _daily_closes(bars.get(s)) for s in symbols}
                out = {name: (relative_strength(closes, members, day),
                              relative_strength(closes, members, day, shift=1),
                              relative_strength(closes, members, day, absolute=True))
                       for name, members in member_map.items()}
                with self._lock:
                    state["rs"], state["day"] = out, day
            except Exception:  # noqa: BLE001 - try again on the next build
                pass
            finally:
                with self._lock:
                    state["loading"] = False

        if self._background:
            threading.Thread(target=load, name="momx-sector-rs", daemon=True).start()
        else:
            load()

    def _stamp(self, rows: list[dict], groups: dict[str, list[dict]], rs: dict, local: datetime,
               latched: set | None = None, picked: dict | None = None) -> None:
        picked = picked if picked is not None else {}
        latched = latched if latched is not None else set()
        minute = local.hour * 60 + local.minute
        after_945 = HOT_FROM_MIN <= minute < HOT_UNTIL_MIN
        info: dict[str, dict] = {}
        for name, members in groups.items():
            priced = [r for r in members if _num(r.get("pctChange")) is not None]
            up = 0
            strong = []
            for r in priced:
                last, vwap = _num(r.get("last")), _num(_m(r.get("m5")).get("vwap"))
                if _num(r.get("pctChange")) > 0 and last is not None and vwap is not None and last > vwap:
                    up += 1
                    strong.append(r)
            vol_up = sum(1 for r in priced if unusual_volume(r))
            # The stocks to look at: up and above VWAP, strongest first.
            # ETFs are never a sector LEADER to trade (they still count in breadth).
            leaders = [{"symbol": r.get("symbol"), "pct": round(_num(r.get("pctChange")), 2), "vol": unusual_volume(r)}
                       for r in sorted((r for r in strong if not r.get("etf")),
                                       key=lambda r: -_num(r.get("pctChange")))[:LEADERS]]
            rs3, rs3_prev, ret3 = (tuple(rs.get(name) or ()) + (None, None, None))[:3]
            moves_today = [_num(r.get("pctChange")) for r in priced]
            med_today = statistics.median(moves_today) if moves_today else None
            breadth = up / len(priced) if priced else 0.0
            meets = (len(priced) >= MIN_MEMBERS and rs3 is not None and rs3_prev is not None
                     and breadth >= HOT_BREADTH)
            info[name] = {"hot": False, "late": False, "rs3": None if rs3 is None else round(rs3 * 100, 2),
                          "rs3Prev": None if rs3_prev is None else round(rs3_prev * 100, 2),
                          "breadth": round(breadth, 2), "up": up, "total": len(priced),
                          "volUp": vol_up, "leaders": leaders,
                          "ret3": None if ret3 is None else round(ret3 * 100, 2),
                          "today": None if med_today is None else round(med_today, 2),
                          "_ok": meets,
                          "_score": breadth + GROWTH_WEIGHT * ((rs3 or 0) - (rs3_prev or 0))}
        best = sorted((n for n, x in info.items() if x["_ok"]), key=lambda n: -info[n]["_score"])
        fresh = [n for n in best if n not in latched]
        if after_945 and fresh and len(latched) < MAX_HOT:
            best = fresh
            latched.add(best[0])
            # Sector first, then its stock (his rule, 2026-09-25): the hot
            # sector's two strongest members AT THE MOMENT it lights are fixed
            # for the day as 🔥#1 / 🔥#2. Back-test Sep 1-23 (14 hot
            # sector-days, small): the #1 at 09:45 won 50%, 36% ran +4%, avg
            # upside +3.1% - vs 26% / 12% / +1.8% for a random member.
            for rank, leader in enumerate(info[best[0]]["leaders"][:2], start=1):
                picked.setdefault(leader["symbol"], {"rank": rank, "sector": best[0],
                                                     "at": local.replace(microsecond=0).isoformat(),
                                                     "pct": leader["pct"]})
        # After 11:00 the day's best NEW sector is shown as late rotation.
        # 🔸 "MOVING NOW" (his ask 2026-09-25: catch Quantum / Space, which on
        # Thursday only broadened ~10:25 after both 🔥 slots were taken). Any
        # time from 09:35: the three NON-hot sectors with the most of their
        # stocks up and above VWAP (>= 60%), median move today >= +0.5%.
        # Ranked by BREADTH, not size of move: Thursday morning Pharma / Oil
        # really were the biggest movers (+2%), while Quantum / Space showed
        # up first as breadth - 0% up at 09:55, 83% / 78% by 10:25. Information,
        # not a trade (later-starting sectors did not win more than they lost
        # in the Sep 1-23 test). Kept in the "late" field so the panel draws 🔸.
        if minute >= HOT_FROM_MIN:
            moving = sorted((n for n, x in info.items()
                             if n not in latched and x["total"] >= MIN_MEMBERS and x["breadth"] >= MOVING_BREADTH
                             and x["today"] is not None and x["today"] >= MOVING_MIN_PCT),
                            key=lambda n: (-info[n]["breadth"], -info[n]["today"]))[:MAX_MOVING]
            for n in moving:
                info[n]["late"] = True
        for name in latched:
            if name in info:
                info[name]["hot"] = True
                info[name]["late"] = False  # lit in time: hot, not late
        for row in rows:
            name = str(row.get("industry") or "").strip()
            entry = info.get(name)
            row["hotLeader"] = dict(picked[row.get("symbol")]) if row.get("symbol") in picked else None
            row["sectorRotation"] = ({k: v for k, v in entry.items() if not k.startswith("_")}
                                     if entry is not None else None)
