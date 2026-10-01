"""The trading-strategy rules behind the FILTERS "A/A+ Setup" strategy choices.

Ganesh asked on 2026-09-23 for one switch instead of reading every column:
"i can trade only V2 or V3 or Daily2". The rules come from the back-test of
his own scanner grade (2026-09-01..09-22, 470 A+ signals, real 5m bars):

    V2      A+ or A, from 09:35 ET to the close, up less than 10% on the day,
            5m momentum "extended", RVOL push on at least one timeframe
    V3      V2 + a squeeze fired (4h / D / Wk)
    Daily 2 V2 + RVOL push on 2 or more timeframes, numbered #1, #2, ...
            across every board in the order they qualified

JUDGED AT THE SIGNAL, THEN KEPT. A ticker is judged exactly once per letter:
on the build where the grade recorder latches its FIRST A (or first A+) of
the day (``gradeFresh.firstToday[letter].at`` equals this build's clock -
grade_log and this module are handed the same ``now`` by service.py). If it
passes then, it is on that strategy's list for the rest of the day even if
its momentum later turns - the list is the day's entries. That is what the
back-test measured. Judging every build instead was tried first and replayed
against the 2026-09-22 archive: 21 tickers on Daily 2 by 10:02, #1 HOOD at
09:35 (its A+ fired at 09:34), because in the first minutes RVOL pins high
on most of the board. Not the tested rule.

Back-test, +2% target / -1.5% stop, stock moves: all A+ +0.07% per trade, V2
+0.63%, V3 +0.58%, Daily 2 +1.07% - the rules were picked after seeing that
data, so the live scorecard (a scheduled task outside this repo) is what
decides whether any of them is real.

The rules live HERE, on the worker, so the filter, the board tags and any
future alert read one answer. The browser only reads ``row["strategy"]``.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import urllib.request
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Mapping
from zoneinfo import ZoneInfo

from momx import strategy_g

_ET = ZoneInfo("America/New_York")

# Strategy G option chains. Fetched ONLY for tickers passing G rules 1-4 (a
# handful a day) and for open G trades, through the main server's chain
# endpoint (Schwab first) - never for the whole board: a chain per symbol per
# build is the CPU-saturation incident (momx/flags.py). Spacing below keeps it
# to at most one chain every GAP seconds from this worker.
G_CHAIN_URL = os.environ.get("AGX_MOMX_CHAIN_URL", "http://127.0.0.1:3001/api/oi-finder-chain?symbol=")
G_PLAN_TTL = 600        # a candidate's plan is re-checked at most every 10 min
G_TRACK_EVERY = 300     # an open G trade is re-priced every 5 min
G_FETCH_GAP = 15        # seconds between any two chain fetches
G_SESSION = (dtime(9, 30), dtime(16, 0))
G_CLOSE_EXIT = dtime(15, 55)
#: What a board row carries of its G trade (row["strategy"]["g"]).
G_ROW_FIELDS = ("rank", "at", "price", "expiry", "target", "targetOi", "entrySymbol", "entryStrike",
                "entryPrice", "entryDelta", "roi", "gammaRoi", "status", "lastPrice", "exitAt",
                "exitPrice", "returnPct", "side")


def _http_chain(symbol: str) -> Any:
    with urllib.request.urlopen(G_CHAIN_URL + symbol, timeout=40) as response:
        return json.loads(response.read().decode("utf-8"))


WINDOW_OPEN = dtime(9, 35)
WINDOW_CLOSE = dtime(16, 0)
MAX_UP_PCT = 10.0
DAILY2_MIN_RVOL_TFS = 2
LETTERS = ("A+", "A")
DIRNAME = "momx_strategy"
KEEP_DAYS = 45

_NONE = {"v2": False, "v3": False, "daily2": False}


def _map(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _local(now: datetime) -> datetime:
    return now.astimezone(_ET) if now.tzinfo else now.replace(tzinfo=_ET)


def _in_window(now: datetime) -> bool:
    local = _local(now)
    if local.weekday() >= 5:
        return False
    return WINDOW_OPEN <= local.time() < WINDOW_CLOSE


def _is_bear(direction) -> bool:
    return str(direction or "").strip().lower() == "bear"


def evaluate(row: Any, now: datetime, direction: str = "bull") -> dict:
    """{"v2", "v3", "daily2"} booleans for the row as it reads at ``now``.

    A missing cell never passes: no grade, no momentum, no % change -> False.
    Pure; :class:`StrategyBook` decides WHEN a row is judged.
    ``direction="bear"`` (spec 2026-09-24): "down less than 10%" on a bear
    row whose grade / momentum / RVOL push are already the bear readings.
    """
    try:
        if not isinstance(row, Mapping) or not _in_window(now) or row.get("etf"):  # ETFs never fire a rule (momx/etf_list.py, 2026-09-27 "use only stocks")
            return dict(_NONE)
        grade = _map(row.get("grade"))
        checks = _map(grade.get("checks"))
        pct = row.get("pctChange")
        rvol = checks.get("pushRvol")
        rvol = [tf for tf in rvol if tf] if isinstance(rvol, (list, tuple)) else []
        sqz = checks.get("sqzFired")
        sqz = [tf for tf in sqz if tf] if isinstance(sqz, (list, tuple)) else []
        v2 = (
            grade.get("letter") in LETTERS
            and isinstance(pct, (int, float)) and not isinstance(pct, bool)
            and ((pct > -MAX_UP_PCT) if _is_bear(direction) else (pct < MAX_UP_PCT))
            and _map(row.get("m5")).get("state") == "extended"
            and len(rvol) >= 1
        )
        return {
            "v2": bool(v2),
            "v3": bool(v2 and sqz),
            "daily2": bool(v2 and len(rvol) >= DAILY2_MIN_RVOL_TFS),
        }
    except Exception:  # noqa: BLE001 - a bad row never passes, never raises
        return dict(_NONE)


def _signal_now(row: Mapping, now_iso: str) -> bool:
    """Did this build latch the row's first A or A+ of the day?"""
    first = _map(_map(row.get("gradeFresh")).get("firstToday"))
    return any(_map(first.get(letter)).get("at") == now_iso for letter in LETTERS)


class StrategyBook:
    """Stamps ``row["strategy"]`` on every build and keeps the day's lists.

    One list per strategy per ET day, shared by every board (a symbol that
    qualified on the Watchlist is the same Daily 2 #1 on Mag7), written to
    ``<directory>/momx_strategy/<day>.json`` so a worker restart keeps them.
    Never raises: a strategy bug must never cost a scan.
    """

    def __init__(self, directory: Path, chain_fetcher: Callable[[str], Any] | None = None,
                 background: bool = True, direction: str = "bull") -> None:
        # No I/O here: constructed at service import time.
        self.directory = Path(directory)
        # BEAR book (spec 2026-09-24): G on the sell side with PUT plans, a
        # target that is hit when price FALLS to the wall; V2/V3 mirrored.
        self.direction = "bear" if _is_bear(direction) else "bull"
        self._lock = threading.Lock()
        self._day = ""
        self._lists: dict[str, list[dict]] = {"v2": [], "v3": [], "daily2": [], "g": []}
        self._pruned = ""
        # Strategy G state (in memory; the G day list itself is persisted).
        self._fetch = chain_fetcher or _http_chain
        self._background = background
        self._rvol_now: dict[str, dict[str, float]] = {}
        self._rvol_prev: dict[str, dict[str, float]] = {}
        self._chains: dict[str, dict] = {}       # symbol -> {"at", "plan", "spot", "marks"}
        self._wanted: dict[str, set] = {}        # symbol -> contract symbols to price
        self._queued: set[str] = set()
        self._queue: "queue.Queue[str]" = queue.Queue()
        self._thread: threading.Thread | None = None
        # The build's clock (epoch s): chain ages are measured on it, so the
        # tests' simulated day and the live worker use one timeline. In the
        # background thread a fetch is stamped with the wall clock, which is
        # the same thing live.
        self._tick = 0.0

    def _clock(self) -> float:
        return time.time() if self._background else self._tick

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            with self._lock:
                self._apply(str(board), payload, now)
        except Exception:  # noqa: BLE001
            return

    def today(self, now: datetime) -> list[dict]:
        """Today's Daily 2 list, in rank order (copies)."""
        try:
            with self._lock:
                self._load(_local(now).date().isoformat())
                return [dict(entry) for entry in self._lists["daily2"]]
        except Exception:  # noqa: BLE001
            return []

    # ---------------------------------------------------------------- internal

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day = day
        self._lists = {"v2": [], "v3": [], "daily2": [], "g": []}
        self._rvol_now, self._rvol_prev = {}, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            return
        for name in self._lists:
            entries = doc.get(name) if isinstance(doc, dict) else None
            for entry in entries if isinstance(entries, list) else []:
                if isinstance(entry, dict) and isinstance(entry.get("symbol"), str):
                    self._lists[name].append(entry)
        self._lists["daily2"].sort(key=lambda e: e.get("rank") or 0)
        self._lists["g"].sort(key=lambda e: e.get("rank") or 0)

    # ---------------------------------------------------------- G: chains

    def _request_chain(self, symbol: str, contracts: set | None = None) -> None:
        """Ask for a fresh chain of ``symbol`` (called under the lock)."""
        if contracts:
            self._wanted.setdefault(symbol, set()).update(contracts)
        if not self._background:
            self._store_chain(symbol, self._safe_fetch(symbol))
            return
        if symbol in self._queued:
            return
        self._queued.add(symbol)
        self._queue.put(symbol)
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._chain_worker, name="momx-strategy-g-chains", daemon=True)
            self._thread.start()

    def _safe_fetch(self, symbol: str) -> Any:
        try:
            return self._fetch(symbol)
        except Exception:  # noqa: BLE001 - a failed chain is "no plan yet", never a crash
            return None

    def _chain_worker(self) -> None:
        while True:
            symbol = self._queue.get()
            payload = self._safe_fetch(symbol)
            with self._lock:
                self._queued.discard(symbol)
                self._store_chain(symbol, payload)
            time.sleep(G_FETCH_GAP)

    def _store_chain(self, symbol: str, payload: Any) -> None:
        """Keep only what G needs from a ~270 KB chain: the plan, spot, marks."""
        if payload is None:
            self._chains[symbol] = {"at": self._clock(), "plan": {"ok": False, "why": "chain unavailable"},
                                    "spot": None, "marks": {}}
            return
        marks = {c: strategy_g.contract_mark(payload, c) for c in self._wanted.get(symbol, ())}
        spot = _map(payload).get("underlyingPrice")
        self._chains[symbol] = {"at": self._clock(), "plan": strategy_g.option_plan(payload, self.direction),
                                "spot": spot if isinstance(spot, (int, float)) else None, "marks": marks}

    # ---------------------------------------------------------- G: day list

    def _g_row(self, row: dict, symbol: str, now: datetime, local: datetime, board: str) -> bool:
        """Judge one row for Strategy G; returns True when the day list changed."""
        if row.get("etf"):  # ETFs never fire a rule (momx/etf_list.py, 2026-09-27 "use only stocks")
            return False
        readings = strategy_g.rvol_readings(row)
        now_vals = self._rvol_now.setdefault(symbol, {})
        prev_vals = self._rvol_prev.setdefault(symbol, {})
        for tf, value in readings.items():
            if tf in now_vals and now_vals[tf] != value:
                prev_vals[tf] = now_vals[tf]
            now_vals[tf] = value
        if any(e["symbol"] == symbol for e in self._lists["g"]):
            return False
        verdict = strategy_g.rules(row, now, prev_vals, self.direction)
        if not verdict["pass"]:
            return False
        info = self._chains.get(symbol)
        fresh = info is not None and self._tick - info["at"] <= G_PLAN_TTL
        if not fresh:
            self._request_chain(symbol)
            info = self._chains.get(symbol)
            fresh = info is not None and self._tick - info["at"] <= G_PLAN_TTL
        plan = info["plan"] if fresh else None
        if not plan or not plan.get("pass"):
            return False
        last = row.get("last")
        entry = {
            "symbol": symbol, "rank": len(self._lists["g"]) + 1,
            "at": local.replace(microsecond=0).isoformat(), "board": board,
            "price": float(last) if isinstance(last, (int, float)) and not isinstance(last, bool) else None,
            "expiry": plan["expiry"], "target": plan["target"], "targetOi": plan["targetOi"],
            "entrySymbol": plan["entrySymbol"], "entryStrike": plan["entryStrike"],
            "entryPrice": plan["entryPrice"], "entryDelta": plan["entryDelta"],
            "roi": plan["roi"], "gammaRoi": plan["gammaRoi"], "side": plan.get("side", "C"),
            "rising": verdict.get("rising"), "arrow": verdict.get("arrow"),
            "status": "open", "lastPrice": plan["entryPrice"], "lastCheck": local.replace(microsecond=0).isoformat(),
            "exitAt": None, "exitPrice": None, "returnPct": None, "targetHit": False,
        }
        self._lists["g"].append(entry)
        self._wanted.setdefault(symbol, set()).add(plan["entrySymbol"])
        return True

    def _g_track(self, rows_by_symbol: dict, local: datetime) -> bool:
        """Re-price open G trades and apply his exits: target / -50% / 15:55."""
        changed = False
        for entry in self._lists["g"]:
            if entry.get("status") != "open":
                continue
            symbol, contract = entry["symbol"], entry["entrySymbol"]
            row = rows_by_symbol.get(symbol)
            last = row.get("last") if isinstance(row, Mapping) else None
            bear = self.direction == "bear"
            hit = (isinstance(last, (int, float)) and entry.get("target")
                   and ((last <= entry["target"]) if bear else (last >= entry["target"])))
            info = self._chains.get(symbol)
            checked = info["at"] if info else 0
            due = (self._tick - checked >= G_TRACK_EVERY or (hit and not entry.get("targetHit"))
                   or local.time() >= G_CLOSE_EXIT)
            if hit and not entry.get("targetHit"):
                entry["targetHit"] = True
                changed = True
            if due and G_SESSION[0] <= local.time() <= G_SESSION[1]:
                self._request_chain(symbol, {contract})
                info = self._chains.get(symbol)
            mark = _map(info["marks"] if info else {}).get(contract)
            if not isinstance(mark, (int, float)) or mark <= 0:
                continue
            if mark != entry.get("lastPrice"):
                entry["lastPrice"] = round(mark, 4)
                entry["lastCheck"] = local.replace(microsecond=0).isoformat()
                changed = True
            spot = info.get("spot") if info else None
            reached = entry.get("targetHit") or (
                isinstance(spot, (int, float))
                and ((spot <= entry["target"]) if bear else (spot >= entry["target"])))
            status = None
            if reached:
                status = "target"
            elif mark <= entry["entryPrice"] * (1 + strategy_g.STOP_PCT / 100.0):
                status = "stop"
            elif local.time() >= G_CLOSE_EXIT:
                status = "close"
            if status:
                entry.update(status=status, exitAt=local.replace(microsecond=0).isoformat(),
                             exitPrice=round(mark, 4),
                             returnPct=round((mark / entry["entryPrice"] - 1) * 100, 1))
                changed = True
        return changed

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, **self._lists}), encoding="utf-8")
        os.replace(tmp, path)

    def _prune(self, today: date) -> None:
        if self._pruned == today.isoformat():
            return
        self._pruned = today.isoformat()
        folder = self.directory / DIRNAME
        if not folder.is_dir():
            return
        for path in folder.glob("*.json"):
            try:
                if (today - date.fromisoformat(path.stem)).days > KEEP_DAYS:
                    path.unlink()
            except (ValueError, OSError):
                continue

    def _apply(self, board: str, payload: Any, now: datetime) -> None:
        if not isinstance(payload, dict):
            return
        local = _local(now)
        self._load(local.date().isoformat())
        now_iso = now.isoformat()
        rows: list[dict] = []
        for section in ("rows", "rest"):
            part = payload.get(section)
            if isinstance(part, list):
                rows.extend(r for r in part if isinstance(r, dict))

        self._tick = now.timestamp()
        held = {name: {e["symbol"]: e for e in entries} for name, entries in self._lists.items()}
        changed = False
        rows_by_symbol: dict[str, dict] = {}
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            rows_by_symbol.setdefault(symbol, row)
            # Strategy G is judged on every build (his method watches the
            # columns change), unlike V2/V3/Daily 2 below. A G bug must not
            # cost the other strategies their stamps.
            try:
                if self._g_row(row, symbol, now, local, board):
                    changed = True
            except Exception:  # noqa: BLE001
                pass
            if _signal_now(row, now_iso):
                verdict = evaluate(row, now, self.direction)
                price = row.get("last")
                price = float(price) if isinstance(price, (int, float)) and not isinstance(price, bool) else None
                for name in ("v2", "v3", "daily2"):
                    if verdict[name] and symbol not in held[name]:
                        entry = {
                            "symbol": symbol,
                            "at": local.replace(microsecond=0).isoformat(),
                            "price": price,
                            "letter": _map(row.get("grade")).get("letter"),
                            "board": board,
                        }
                        if name == "daily2":
                            entry["rank"] = len(self._lists["daily2"]) + 1
                        self._lists[name].append(entry)
                        held[name][symbol] = entry
                        changed = True
            d2 = held["daily2"].get(symbol)
            row["strategy"] = {
                "v2": symbol in held["v2"],
                "v3": symbol in held["v3"],
                "daily2": {"rank": d2["rank"], "at": d2["at"], "price": d2["price"]} if d2 else None,
                "g": None,
            }
        try:
            if self._g_track(rows_by_symbol, local):
                changed = True
        except Exception:  # noqa: BLE001
            pass
        g_by_symbol = {e["symbol"]: e for e in self._lists["g"]}
        for symbol, row in rows_by_symbol.items():
            entry = g_by_symbol.get(symbol)
            if entry is not None:
                row["strategy"]["g"] = {k: entry.get(k) for k in G_ROW_FIELDS}
        for row in rows:  # a symbol listed twice in one payload shares the stamp
            first = rows_by_symbol.get(row.get("symbol"))
            if first is not None and row is not first:
                row["strategy"] = dict(first["strategy"])
        payload["strategyDaily2"] = [
            {k: entry.get(k) for k in ("symbol", "rank", "at", "price", "letter", "board")}
            for entry in self._lists["daily2"]
        ]
        payload["strategyG"] = [{"symbol": e["symbol"], **{k: e.get(k) for k in G_ROW_FIELDS}}
                                for e in self._lists["g"]]
        if changed:
            self._save()
            self._prune(local.date())
