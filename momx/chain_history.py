"""30 days of option chains (2026-11-10, his "Start saving 30days option chain also").

The app already keeps ONE row a day per strike of open interest, volume, gamma
and delta (database table option_chain_daily_snapshots). It did not keep option
PRICES over time: artifacts/oi_chain_cache/<SYMBOL>.json.gz holds the full chain
(bid, ask, last, mark, greeks, volume, OI for every strike and expiry the app
follows) and is overwritten on each refresh.

This keeps a copy of those chain files every SLOT_MINUTES during the session:

    artifacts/oi_chain_history/<YYYY-MM-DD>/<HHMM>/<SYMBOL>.json.gz

* A chain is copied into a slot only when its file changed since the last copy
  of that symbol, so a slot holds just the chains that moved.
* KEEP_DAYS calendar days are kept; older day folders are deleted.
* chain_at(symbol, when) returns the newest saved chain at or before a moment -
  the answer to "what was this option worth at 10:30 on the 6th", for the AI
  agents, back-tests and the chatbot.

Plain file copies on a quiet thread: no parsing, no network, no database.
Off with AGX_CHAIN_HISTORY=0.
"""
from __future__ import annotations

import gzip
import json
import os
import shutil
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
KEEP_DAYS = 30
SLOT_MINUTES = 30
#: First and last slot of a session, ET (minutes after midnight): 09:35 ... 16:05.
FIRST_SLOT, LAST_SLOT = 9 * 60 + 35, 16 * 60 + 5
CHECK_SECONDS = 60.0
SUFFIX = ".json.gz"


def slot_of(now: datetime) -> str | None:
    """The slot a moment belongs to ("0935", "1005", ... "1605"), or None outside
    a weekday session. A slot covers the SLOT_MINUTES that follow its time."""
    now = now.astimezone(ET)
    minute = now.hour * 60 + now.minute
    if now.weekday() >= 5 or minute < FIRST_SLOT or minute >= LAST_SLOT + SLOT_MINUTES:
        return None
    start = FIRST_SLOT + (minute - FIRST_SLOT) // SLOT_MINUTES * SLOT_MINUTES
    return f"{start // 60:02d}{start % 60:02d}"


class ChainHistory:
    def __init__(self, source: Path, root: Path, keep_days: int = KEEP_DAYS) -> None:
        self.source, self.root, self.keep_days = Path(source), Path(root), keep_days
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}      # symbol -> mtime of the copy last saved
        self._seen_day = ""
        self.last: dict = {}

    def _day_dir(self, day: str) -> Path:
        return self.root / day

    def _load_seen(self, day: str) -> None:
        """After a restart: what was already saved today, from the folders themselves."""
        self._seen, self._seen_day = {}, day
        folder = self._day_dir(day)
        if not folder.is_dir():
            return
        for slot in sorted(p for p in folder.iterdir() if p.is_dir()):
            if not slot.name.isdigit():
                shutil.rmtree(slot, ignore_errors=True)       # a half-made slot left by a crash: not saved, so not "seen"
                continue
            for path in slot.glob("*" + SUFFIX):
                try:
                    self._seen[path.name[: -len(SUFFIX)].upper()] = path.stat().st_mtime
                except OSError:
                    continue

    def snapshot(self, now: datetime | None = None) -> dict:
        """Save the current slot if it has not been saved yet. Returns what it did."""
        now = (now or datetime.now(ET)).astimezone(ET)
        slot = slot_of(now)
        if slot is None:
            return {"saved": 0, "reason": "outside the session"}
        day = now.date().isoformat()
        with self._lock:
            target = self._day_dir(day) / slot
            if target.exists():
                return {"saved": 0, "reason": "slot already saved", "day": day, "slot": slot}
            if self._seen_day != day:
                self._load_seen(day)
            try:
                files = sorted(self.source.glob("*" + SUFFIX))
            except OSError:
                files = []
            working = target.with_name(slot + ".tmp")
            shutil.rmtree(working, ignore_errors=True)
            saved = skipped = 0
            copied: dict[str, float] = {}
            for path in files:
                symbol = path.name[: -len(SUFFIX)].upper()
                try:
                    mtime = path.stat().st_mtime
                    if mtime <= self._seen.get(symbol, 0.0):
                        skipped += 1
                        continue
                    working.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, working / path.name)       # keeps the chain's own time on the copy
                    copied[symbol] = mtime
                    saved += 1
                except OSError:
                    continue                                      # being rewritten right now: next slot gets it
            if saved:
                for attempt in range(4):
                    try:
                        os.replace(working, target)                # the slot appears whole or not at all
                        break
                    except OSError:
                        # Windows refuses the rename while a scanner still has a new file open.
                        if attempt == 3:
                            shutil.rmtree(working, ignore_errors=True)
                            return {"saved": 0, "reason": "could not finish the slot, will retry", "day": day, "slot": slot}
                        time.sleep(0.5)
                self._seen.update(copied)                          # "saved" only once the slot really exists
            else:
                target.mkdir(parents=True, exist_ok=True)          # an empty slot = looked, nothing had changed
            pruned = self._prune(now)
            self.last = {"saved": saved, "unchanged": skipped, "day": day, "slot": slot, "pruned": pruned,
                         "at": now.replace(microsecond=0).isoformat()}
            return dict(self.last)

    def _prune(self, now: datetime) -> int:
        cutoff = (now.date() - timedelta(days=self.keep_days)).isoformat()
        removed = 0
        try:
            folders = [p for p in self.root.iterdir() if p.is_dir() and len(p.name) == 10 and p.name[4] == "-"]
        except OSError:
            return 0
        for folder in folders:
            if folder.name < cutoff:
                shutil.rmtree(folder, ignore_errors=True)
                removed += 1
        return removed

    def chain_at(self, symbol: str, when: datetime) -> dict | None:
        """The newest saved chain for `symbol` at or before `when`, with where it
        came from under "_saved". None when nothing that old is on file."""
        when = when.astimezone(ET)
        name = str(symbol or "").upper() + SUFFIX
        try:
            days = sorted((p.name for p in self.root.iterdir() if p.is_dir() and len(p.name) == 10), reverse=True)
        except OSError:
            return None
        day, clock = when.date().isoformat(), when.strftime("%H%M")
        for folder in days:
            if folder > day:
                continue
            try:
                slots = sorted((p.name for p in (self.root / folder).iterdir() if p.is_dir() and p.name.isdigit()), reverse=True)
            except OSError:
                continue
            for slot in slots:
                if folder == day and slot > clock:
                    continue
                path = self.root / folder / slot / name
                if path.is_file():
                    try:
                        mtime = path.stat().st_mtime
                        if mtime > when.timestamp():
                            continue          # this copy is a chain from AFTER the moment asked for: no looking ahead
                        with gzip.open(path, "rt", encoding="utf-8") as handle:
                            doc = json.load(handle)
                    except (OSError, ValueError):
                        continue
                    if isinstance(doc, dict):
                        doc["_saved"] = {"day": folder, "slot": slot,
                                         "fileTime": datetime.fromtimestamp(mtime, ET).replace(microsecond=0).isoformat()}
                        return doc
        return None

    def status(self) -> dict:
        days, slots, files, size = [], 0, 0, 0
        try:
            folders = sorted(p for p in self.root.iterdir() if p.is_dir() and len(p.name) == 10)
        except OSError:
            folders = []
        for folder in folders:
            days.append(folder.name)
            for slot in folder.iterdir():
                if not slot.is_dir() or not slot.name.isdigit():
                    continue
                slots += 1
                for path in slot.glob("*" + SUFFIX):
                    files += 1
                    try:
                        size += path.stat().st_size
                    except OSError:
                        pass
        return {"keepDays": self.keep_days, "everyMinutes": SLOT_MINUTES, "days": len(days),
                "from": days[0] if days else None, "through": days[-1] if days else None,
                "snapshots": slots, "chains": files, "megabytes": round(size / 1e6, 1), "last": dict(self.last),
                "holds": "bid, ask, last, mark, greeks, volume and open interest for every strike and expiry the app follows"}


_BOOK: ChainHistory | None = None


def book() -> ChainHistory:
    global _BOOK
    if _BOOK is None:
        from config import ARTIFACTS_DIR
        root = Path(ARTIFACTS_DIR)
        _BOOK = ChainHistory(root / "oi_chain_cache", root / "oi_chain_history")
    return _BOOK


_STARTED = False


def start() -> None:
    """One quiet thread in the scanner worker. Idempotent."""
    global _STARTED
    if _STARTED or os.environ.get("AGX_CHAIN_HISTORY", "1").strip() == "0":
        return
    _STARTED = True

    def loop() -> None:
        while True:
            try:
                book().snapshot()
            except Exception as exc:  # noqa: BLE001 - never take the worker down
                print(f"[chain-history] {type(exc).__name__}: {exc}", flush=True)
            time.sleep(CHECK_SECONDS)

    threading.Thread(target=loop, name="chain-history", daemon=True).start()
