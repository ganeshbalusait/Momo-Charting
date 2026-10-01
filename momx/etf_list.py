"""Which symbols are ETFs - from the official US listing directory (2026-09-27).

Ganesh: "use only stocks" -> no ETFs in the rules. The industry label cannot
decide it: IBIT reads "Crypto" and DRAM "Semis" but both are ETFs (iShares
Bitcoin Trust ETF, Roundhill Memory ETF). The authority is Nasdaq Trader's
symbol directory, which carries an ETF Y/N column for EVERY US-listed security:
  nasdaqlisted.txt  Symbol|Security Name|...|ETF|NextShares      (ETF = col 6)
  otherlisted.txt   ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|...  (col 4)
The worker keeps a copy (artifacts/momx_etf_symbols.json) and refreshes it in
the background when older than REFRESH_DAYS; a failed download keeps the copy.
A symbol missing from the directory is UNKNOWN (None) and is not excluded.

``mark(payload)`` stamps row["etf"] = True on ETF rows before the books run,
so every rule reads one flag: the worker's books (strategy, MX A+, market
turn, SOLO, option tracker), the frontend tags (GO / OPT / Best / Star /
GO+RVOL+MACD) and the scorecard. Never raises. No thread at import.
"""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from config import ARTIFACTS_DIR

URLS = (
    ("https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt", 0, 6),
    ("https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt", 0, 4),
)
REFRESH_DAYS = 7
_LOCK = threading.Lock()
_CACHE: dict = {"key": None, "etfs": frozenset(), "known": frozenset()}
_REFRESHING = {"on": False}


def store_path() -> Path:
    override = os.environ.get("AGX_MOMX_ETF_PATH", "").strip()
    return Path(override) if override else ARTIFACTS_DIR / "momx_etf_symbols.json"


def parse_directory(text: str, symbol_col: int, etf_col: int) -> dict[str, bool]:
    out: dict[str, bool] = {}
    for line in (text or "").splitlines()[1:]:
        parts = line.split("|")
        if len(parts) <= max(symbol_col, etf_col) or line.startswith("File Creation Time"):
            continue
        sym = parts[symbol_col].strip().upper()
        flag = parts[etf_col].strip().upper()
        if sym and flag in ("Y", "N"):
            out[sym] = flag == "Y"
    return out


def _load() -> tuple[frozenset, frozenset]:
    path = store_path()
    try:
        stat = path.stat()
        key = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        return frozenset(), frozenset()
    with _LOCK:
        if _CACHE["key"] == key:
            return _CACHE["etfs"], _CACHE["known"]
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        etfs = frozenset(str(s).upper() for s in doc.get("etfs") or [])
        known = frozenset(str(s).upper() for s in doc.get("stocks") or []) | etfs
    except (OSError, ValueError, UnicodeDecodeError):
        return frozenset(), frozenset()
    with _LOCK:
        _CACHE.update(key=key, etfs=etfs, known=known)
    return etfs, known


def is_etf(symbol: Any) -> bool | None:
    """True / False from the directory; None when the symbol is not listed."""
    sym = str(symbol or "").strip().upper()
    if not sym:
        return None
    etfs, known = _load()
    if sym in etfs:
        return True
    return False if sym in known else None


def refresh(fetch=None) -> bool:
    """Download both directories and write the store. False (store kept) on any failure."""
    def _get(url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 AGX"})
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.read().decode("utf-8", "replace")
    fetch = fetch or _get
    merged: dict[str, bool] = {}
    try:
        for url, s_col, e_col in URLS:
            part = parse_directory(fetch(url), s_col, e_col)
            if len(part) < 1000:          # a truncated / error page is not a directory
                return False
            merged.update(part)
    except Exception:  # noqa: BLE001
        return False
    path = store_path()
    doc = {"source": "nasdaqtrader.com SymDir nasdaqlisted.txt + otherlisted.txt (ETF column)",
           "savedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "etfs": sorted(s for s, e in merged.items() if e), "stocks": sorted(s for s, e in merged.items() if not e)}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        tmp.write_text(json.dumps(doc), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False


def _stale() -> bool:
    try:
        return time.time() - store_path().stat().st_mtime > REFRESH_DAYS * 86400
    except OSError:
        return True


def refresh_in_background_if_stale() -> None:
    if os.environ.get("AGX_MOMX_ETF_REFRESH", "1").strip() == "0" or not _stale():
        return
    with _LOCK:
        if _REFRESHING["on"]:
            return
        _REFRESHING["on"] = True

    def _run() -> None:
        try:
            refresh()
        finally:
            _REFRESHING["on"] = False
    threading.Thread(target=_run, name="momx-etf-list", daemon=True).start()


def mark(payload: Any) -> None:
    """Stamp row["etf"] = True on ETF rows (rows + rest). Never raises."""
    try:
        refresh_in_background_if_stale()
        for section in ("rows", "rest"):
            for row in (payload or {}).get(section) or []:
                if isinstance(row, dict) and is_etf(row.get("symbol")) is True:
                    row["etf"] = True
    except Exception:  # noqa: BLE001
        return
