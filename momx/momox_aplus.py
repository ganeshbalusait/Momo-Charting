"""MOMOX A+: the competitor's A+ setup, on the scanner row (2026-09-25).

Ganesh, after their ScannerX3 videos: A+ = "catalyst + high OI + RVOL + sqz
fires + skittles D above cyan or green". Back-tested on the History archive
(Sep 1-25, 18 days): all pillars together fired ~2.7x a day, 39% hit +2%
first / 35% hit -1.5% first, and 37% ran +4% - the highest ran+4 of every rule
tested, mostly before 10:00; the week of Sep 21-25 went 9 W / 5 L / 2 flat
(META 09:35 on 9/21, MRNA 09:48 on 9/22). Weekly-options names lost less.

The rule, per symbol, first time it holds 09:35-15:30 ET (latched for the day):
  * CATALYST - a news headline for the symbol within 24h (row.news);
  * RVOL     - ANY 15m / 30m / 1h / 2h / 4h / D RVOL cell CYAN (z >= 3 on a
               bullish bar; 4h / D added 2026-09-29, his update);
  * SQZ FIRE - a 30m / 1h / 2h / 4h / D / Wk squeeze cell CYAN (fired up) now
               (30m / 1h added 2026-09-28, untested), or seen
               cyan within the last 30 minutes (it lights for ~2 bars). The
               Weekly counts: META 9/24 fired only there;
  * SKITTLES - ANY of D, 2D, 3D, 4D, Wk or M with a CYAN or GREEN
               background (ALL six until 2026-09-28; lime and the fg trend
               read dropped 2026-09-29, his update);
  * no ETFs (QQQ / TQQQ fired and went nowhere in the test).
  * HIGH OI  - REMOVED 2026-09-29 (his update); was required from 2026-09-26: the
               biggest call wall ABOVE the price and within +2x the expected
               move - the same walls the ticker card draws (EM2, top 3).
               "High" is RELATIVE to the stock's own chain, no fixed floor:
               the first cut demanded 1,000 contracts and would have dropped
               QMCO (35C, 739 OI - its biggest wall; it ran +14% on 9/24).
               Ganesh 2026-09-26: "Don't force 1000 high oi". Checked only for rows
               already passing the other four (a handful a day), through the
               main server's chain endpoint, one chain every OI_FETCH_GAP
               seconds - never for the whole board (the CPU-saturation
               incident, momx/flags.py). Open interest has no history, so this
               gate was never back-tested; AGX_MOMOX_APLUS_OI=0 turns it off.
``weeklies`` (the stock has weekly options) still rides on the stamp.

Stamps row["momoxAPlus"] = {"at", "price", "sqz", "weeklies"}; persisted per ET
day so a worker restart keeps it. Never raises.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

import oi_auto_alerts

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_momox_aplus"
FROM_MIN, UNTIL_MIN = 9 * 60 + 35, 15 * 60 + 30
#: 2026-09-29 (his update): ANY of 15m..D with a CYAN cell (RVOL z >= 3 on a
#: bullish bar) - e.g. 15m = 5 cyan while 30m = 1 still counts. Was 15m-2h.
RVOL_TFS = ("15m", "30m", "1h", "2h", "4h", "D")
#: Any of these firing counts (2026-09-28, his ask: "not only 4H sqz - 2Hr or
#: 4h or D or 1hr or 30min"). 30m / 1h come from row["sqzFast"]; Wk kept (META
#: 9/24 fired only there). The 30m / 1h widening is NOT back-tested.
SQZ_TFS = ("30m", "1h", "2h", "4h", "D", "Wk")
SQZ_FAST_TFS = ("30m", "1h")
SKIT_TFS = ("D", "2D", "3D", "4D", "Wk", "M")
SQZ_LOOKBACK_SECONDS = 30 * 60
NEWS_WINDOW_SECONDS = 24 * 3600
# Same sets as momx/grade.py (SKIT_CROSS_BG / SKIT_TREND_FG): bg dark_green is
# a MACD cross against the EMA trend and is deliberately NOT bullish.
#: 2026-09-29 (his update, "Skittles bullish on any of D-Mo (cyan/Green)"): the
#: cell's background CYAN or GREEN only - lime and the fg-only trend read no
#: longer count.
SKIT_BULL_BG = frozenset({"cyan", "green"})
SKIT_BULL_FG: frozenset = frozenset()
#: BEAR mirror (2026-09-28, "build it same as rule for bear"; grade.py's bear
#: sets): Skittles bg magenta/red/light_red or fg magenta/plum, RVOL magenta,
#: squeeze fired DOWN (magenta), the biggest PUT wall BELOW the price within
#: 2x EM. Not back-tested - no bear mirror has shown an edge yet.
SKIT_BEAR_BG = frozenset({"magenta", "red"})      # mirror of cyan / green (2026-09-29)
SKIT_BEAR_FG: frozenset = frozenset()

#: REMOVED 2026-09-29 (his update: "Remove - the stock's biggest call wall
#: above price within 2x"). Off by default; AGX_MOMOX_APLUS_OI=1 brings it back.
OI_REQUIRED = os.environ.get("AGX_MOMOX_APLUS_OI", "0") == "1"
CHAIN_URL = os.environ.get("AGX_MOMX_CHAIN_URL", "http://127.0.0.1:3001/api/oi-finder-chain?symbol=")
MIN_WALL_OI = 1           # any real open interest: the stock's own biggest wall is its magnet
WALL_EM_MULTIPLE = 2.0    # the ticker card's EM2 band
WALLS_PER_SIDE = 3        # the card's three a side
OI_TTL_SECONDS = 600      # a verdict is re-checked at most every 10 min
OI_FETCH_GAP = 15         # seconds between two chain fetches from this worker


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def skittles_bullish(row: Mapping, bear: bool = False) -> bool:
    """ANY of D..M bullish (Ganesh 2026-09-28: "skittle any D to M"; it was
    ALL six until then - the Sep 1-25 back-test numbers are for ALL)."""
    cells = _m(row.get("skittles"))
    bg_set, fg_set = (SKIT_BEAR_BG, SKIT_BEAR_FG) if bear else (SKIT_BULL_BG, SKIT_BULL_FG)
    for tf in SKIT_TFS:
        cell = _m(cells.get(tf))
        if cell.get("bg") in bg_set or cell.get("fg") in fg_set:
            return True
    return False


def rvol_cyan(row: Mapping, bear: bool = False) -> bool:
    cells = _m(row.get("rvol"))
    return any(_m(cells.get(tf)).get("bg") == ("magenta" if bear else "cyan") for tf in RVOL_TFS)


def sqz_cyan_now(row: Mapping, bear: bool = False) -> list[str]:
    cells = {**_m(row.get("sqz")), **{tf: v for tf, v in _m(row.get("sqzFast")).items() if tf in SQZ_FAST_TFS}}
    fired = "magenta" if bear else "cyan"
    return [tf for tf in SQZ_TFS if _m(cells.get(tf)).get("bg") == fired]


def has_catalyst(row: Mapping, now: datetime) -> bool:
    news = _m(row.get("news"))
    if not str(news.get("headline") or "").strip():
        return False
    stamp = news.get("publishedAt") or news.get("at")
    try:
        at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True  # the worker already applies its own 24h window
    age = now.timestamp() - at.timestamp()
    return -300 <= age <= NEWS_WINDOW_SECONDS


def is_etf(row: Mapping) -> bool:
    # The official listing flag first (momx/etf_list.py stamps row["etf"]):
    # the label alone missed IBIT ("Crypto") and DRAM ("Semis").
    return row.get("etf") is True or "ETF" in str(row.get("industry") or "")


def weeklies(row: Mapping) -> bool:
    return "weeklies" in (_m(row.get("badge")).get("reasons") or [])


def _http_chain(symbol: str) -> Any:
    with urllib.request.urlopen(CHAIN_URL + symbol, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


def call_wall(payload: Any, as_of: datetime, bear: bool = False) -> dict | None:
    """The High-OI verdict for one chain: {"ok", "strike", "oi"}, or None when
    the chain is unusable (no rows / no spot / stale) - "unknown", never "no"."""
    chain = _m(payload)
    # Age by the chain's own scannedAt, NOT the "stale" flag (2026-09-28): the
    # gateway and api_server stamp stale=True on ANY copy older than ~15s while
    # a refresh runs, so the flag was true on every read and this gate blocked
    # every MomoX A+ from 09-26 to 09-28. Open interest changes once a day, so
    # a chain scanned today is good; one from an earlier day is "unknown".
    scanned = str(chain.get("scannedAt") or "").strip()
    if scanned:
        try:
            at = datetime.fromisoformat(scanned.replace("Z", "+00:00"))
            day = (as_of.astimezone(ET) if as_of.tzinfo else as_of).date()
            if (at.astimezone(ET) if at.tzinfo else at).date() != day:
                return None
        except ValueError:
            return None
    elif chain.get("stale") is True:
        return None
    rows = chain.get("selectedExpiryChainRows") or [
        *(chain.get("callRows") or []), *(chain.get("putRows") or [])]
    spot = _num(chain.get("underlyingPrice")) or 0.0
    if not rows or spot <= 0:
        return None
    em = _num(_m(chain.get("currentAtm")).get("expectedMove")) or 0.0
    walls = oi_auto_alerts.build_high_oi_walls(rows, spot, as_of=as_of.date(),
                                               top_per_side=WALLS_PER_SIDE, expected_move=em)
    if bear:   # the biggest PUT wall BELOW the price, within -2x EM
        floor = spot - WALL_EM_MULTIPLE * em if em > 0 else float("-inf")
        near = [w for w in walls.get("puts") or [] if floor <= float(w["strike"]) < spot]
    else:
        reach = spot + WALL_EM_MULTIPLE * em if em > 0 else float("inf")
        near = [w for w in walls.get("calls") or [] if spot < float(w["strike"]) <= reach]
    if not near:
        return {"ok": False, "strike": None, "oi": 0}
    best = max(near, key=lambda w: w["openInterest"])
    return {"ok": best["openInterest"] >= MIN_WALL_OI, "strike": best["strike"], "oi": best["openInterest"]}


class MomoxAPlusBook:
    def __init__(self, directory: Path, *, fetch: Callable[[str], Any] | None = None,
                 background: bool = True, oi_required: bool | None = None,
                 clock: Callable[[], float] = time.monotonic, direction: str = "bull") -> None:
        self.directory = Path(directory)
        self.bear = str(direction or "").strip().lower() == "bear"
        self._lock = threading.Lock()
        self._day = ""
        self._boards: dict[str, dict] = {}   # board -> {"hits": {...}, "sqzSeen": {sym: {tf: ts}}}
        self.oi_required = OI_REQUIRED if oi_required is None else oi_required
        self._fetch = fetch or _http_chain
        self._background = background
        self._clock = clock
        self._oi: dict[str, dict] = {}       # symbol -> {"at": clock, "verdict": dict | None}
        self._queue: queue.Queue = queue.Queue()
        self._queued: set[str] = set()
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------ High OI

    def _oi_verdict(self, symbol: str, now: datetime) -> dict | None:
        """The cached verdict when fresh; otherwise asks for one and returns
        what it has (None = not known yet). Caller holds the lock."""
        held = self._oi.get(symbol)
        if held is not None and self._clock() - held["at"] <= OI_TTL_SECONDS:
            return held["verdict"]
        if not self._background:
            self._oi[symbol] = {"at": self._clock(), "verdict": self._judge(symbol, now)}
            return self._oi[symbol]["verdict"]
        if symbol not in self._queued:
            self._queued.add(symbol)
            self._queue.put((symbol, now))
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._oi_worker, name="momx-momox-aplus-oi", daemon=True)
                self._thread.start()
        return held["verdict"] if held is not None else None

    def _judge(self, symbol: str, now: datetime) -> dict | None:
        try:
            return call_wall(self._fetch(symbol), now, self.bear)
        except Exception:  # noqa: BLE001 - a failed chain is "not known yet", never a crash
            return None

    def _oi_worker(self) -> None:
        while True:
            symbol, now = self._queue.get()
            verdict = self._judge(symbol, now)
            with self._lock:
                self._queued.discard(symbol)
                # An unusable chain is retried on the next build, not held 10 min.
                if verdict is not None:
                    self._oi[symbol] = {"at": self._clock(), "verdict": verdict}
            time.sleep(OI_FETCH_GAP)

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            minute = local.hour * 60 + local.minute
            ts = local.timestamp()
            rows = [r for section in ("rows", "rest") for r in (_m(payload).get(section) or [])
                    if isinstance(r, dict)]
            with self._lock:
                self._load(local.date().isoformat())
                state = self._boards.setdefault(board, {"hits": {}, "sqzSeen": {}})
                hits, seen = state["hits"], state["sqzSeen"]
                changed = False
                for row in rows:
                    symbol = row.get("symbol")
                    if not isinstance(symbol, str):
                        continue
                    for tf in sqz_cyan_now(row, self.bear):
                        seen.setdefault(symbol, {})[tf] = ts
                    if (symbol in hits or local.weekday() >= 5 or not (FROM_MIN <= minute <= UNTIL_MIN)
                            or is_etf(row)):
                        continue
                    recent = [tf for tf, at in (seen.get(symbol) or {}).items() if ts - at <= SQZ_LOOKBACK_SECONDS]
                    if recent and rvol_cyan(row, self.bear) and skittles_bullish(row, self.bear) and has_catalyst(row, local):
                        wall = self._oi_verdict(symbol, local) if self.oi_required else None
                        if self.oi_required and not (wall and wall["ok"]):
                            continue   # no magnet above (or not known yet): re-judged next build
                        hits[symbol] = {"at": local.replace(microsecond=0).isoformat(),
                                        "price": _num(row.get("last")), "sqz": sorted(recent),
                                        "weeklies": weeklies(row),
                                        "oiStrike": wall["strike"] if wall else None,
                                        "oi": wall["oi"] if wall else None}
                        changed = True
                if changed:
                    self._save()
                for row in rows:
                    hit = hits.get(row.get("symbol"))
                    row["momoxAPlus"] = dict(hit) if hit else None
        except Exception:  # noqa: BLE001 - a MomoX A+ bug never costs a scan
            return

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._boards = day, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            for board, held in (doc.get("boards") or {}).items():
                if isinstance(held, dict):
                    self._boards[board] = {"hits": dict(held.get("hits") or {}), "sqzSeen": dict(held.get("sqzSeen") or {})}
        except (OSError, ValueError, UnicodeDecodeError, AttributeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"date": self._day, "boards": self._boards}), encoding="utf-8")
            os.replace(tmp, path)
        except OSError:
            pass
