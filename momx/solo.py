"""SOLO: big money going into ONE stock, on the scanner row.

Ganesh 2026-09-24: "I want to catch big money move stocks - I don't want who
is buying, I want that stock". The footprint, tested Sep 1-23 (575 tickers):

    between 09:45 and 11:00 ET, the FIRST candle where
      * today's volume so far is 5x-20x the stock's normal volume by the
        same time of day (momentum.todVol - heavier than 20x was mostly tiny
        pump-and-dumps: CPOP, ARMP, BIAF crashed 20-35%),
      * it is up at least 1% on the day, above VWAP and above the 09:30
        candle's close,
      * price >= $5,
      * its sector is NOT moving with it (fewer than half the group up and
        above VWAP, or no real group) - money aimed at this one company.
    Result: 57% ran +4% at some point that day (the most of anything
    tested), but only about half were still up at the close - a fast mover:
    take profit into strength. Stamped once per symbol per day as
    row["solo"] = {"at", "ratio", "price"}; persisted per ET day. Never raises.

Live difference from the test: the board's 5m tape holds ~3-4 prior
sessions, so "normal" is a 3-4 session average (the test used 10).
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
DIRNAME = "momx_solo"
FROM_MIN, UNTIL_MIN = 9 * 60 + 45, 11 * 60
MIN_RATIO, MAX_RATIO = 5.0, 20.0
MIN_PCT = 1.0
MIN_PRICE = 5.0
SECTOR_MOVING = 0.5
MIN_GROUP = 4


def _m(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _num(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out else None


def qualifies(row: Mapping) -> float | None:
    """The time-of-day volume ratio when the row meets SOLO right now, else None."""
    m5 = _m(row.get("m5"))
    tod = _m(m5.get("todVol"))
    ratio, open_ = _num(tod.get("ratio")), _num(tod.get("open"))
    last, vwap, pct = _num(row.get("last")), _num(m5.get("vwap")), _num(row.get("pctChange"))
    if None in (ratio, open_, last, vwap, pct):
        return None
    if not (MIN_RATIO <= ratio < MAX_RATIO and pct >= MIN_PCT and last >= MIN_PRICE and last > vwap and last > open_):
        return None
    sector = _m(row.get("sectorRotation"))
    total = _num(sector.get("total")) or 0
    breadth = _num(sector.get("breadth"))
    if total >= MIN_GROUP and breadth is not None and breadth >= SECTOR_MOVING:
        return None  # the whole group is moving: sector money, not solo
    return ratio


class SoloBook:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)
        self._lock = threading.Lock()
        self._day = ""
        self._hits: dict[str, dict] = {}

    def apply(self, board: str, payload: Any, now: datetime) -> None:
        try:
            local = now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
            minute = local.hour * 60 + local.minute
            rows = [r for section in ("rows", "rest") for r in (_m(payload).get(section) or [])
                    if isinstance(r, dict)]
            with self._lock:
                self._load(local.date().isoformat())
                changed = False
                if local.weekday() < 5 and FROM_MIN <= minute < UNTIL_MIN:
                    for row in rows:
                        symbol = row.get("symbol")
                        if not isinstance(symbol, str) or symbol in self._hits or row.get("etf"):  # ETFs never fire a rule (momx/etf_list.py, 2026-09-27 "use only stocks")
                            continue
                        ratio = qualifies(row)
                        if ratio is not None:
                            self._hits[symbol] = {"at": local.replace(microsecond=0).isoformat(),
                                                  "ratio": ratio, "price": _num(row.get("last"))}
                            changed = True
                if changed:
                    self._save()
                for row in rows:
                    hit = self._hits.get(row.get("symbol"))
                    row["solo"] = dict(hit) if hit else None
        except Exception:  # noqa: BLE001 - a SOLO bug never costs a scan
            return

    def _path(self, day: str) -> Path:
        return self.directory / DIRNAME / f"{day}.json"

    def _load(self, day: str) -> None:
        if self._day == day:
            return
        self._day, self._hits = day, {}
        try:
            doc = json.loads(self._path(day).read_text(encoding="utf-8"))
            if isinstance(doc, dict) and isinstance(doc.get("hits"), dict):
                self._hits = {k: v for k, v in doc["hits"].items() if isinstance(v, dict)}
        except (OSError, ValueError, UnicodeDecodeError):
            pass

    def _save(self) -> None:
        path = self._path(self._day)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"date": self._day, "hits": self._hits}), encoding="utf-8")
        os.replace(tmp, path)
