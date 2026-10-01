"""30-day archive of premarket scanner rows, stamped when each row FIRST appeared.

The trader's request (2026-08-23): "history to save for 30 days with
timestamp same as alerts, so I know when it came". The scanner's live table
repaints all morning; this archive freezes, per day and per symbol, the
moment a row first showed (firstSeenAt) plus the latest state of that row
(signals, strength, catalyst), so an evening review can answer both "what
fired" and "when did the scanner actually show it to me".

One JSON file per ET trading day under artifacts/premarket_scanner_history/.
Files, not the DB: the payload is the scanner row as served (schema drifts
freely), a day is the natural unit of both retention and reading, and the
store must survive restarts without a migration.

Stdlib-only; directory and clock injected for tests.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path

RETENTION_DAYS = 30
DEFAULT_HISTORY_DIR = Path("artifacts") / "premarket_scanner_history"


def _day_file(directory: Path, day: str) -> Path:
    return directory / f"{day}.json"


def record_rows(
    rows: object,
    now_et: datetime,
    directory: Path | str = DEFAULT_HISTORY_DIR,
    *,
    new_out: list | None = None,
) -> bool:
    """Fold today's served rows into today's archive. Returns True if written.

    Per symbol: firstSeenAt is set once and never moves (that is the "when
    it came" the trader asked for); the row snapshot and lastSeenAt track
    the latest state, so a row that strengthens during the morning keeps
    its original arrival stamp but shows its final strength.

    ``new_out``: pass a list and the symbols whose firstSeenAt was stamped BY
    THIS CALL are appended - the natural once-per-day edge the phone push
    rides on (trader, 2026-08-30: "mag7 scanner alert after 9:00am"). The
    bool return stays exactly as documented for existing callers.
    """
    live = [row for row in (rows or []) if isinstance(row, dict) and row.get("symbol")]
    if not live:
        return False
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = now_et.date().isoformat()
    path = _day_file(directory, day)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    entries = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
    stamp = now_et.isoformat()
    changed = False
    for row in live:
        symbol = str(row["symbol"]).upper()
        existing = entries.get(symbol) if isinstance(entries.get(symbol), dict) else None
        snapshot = {key: value for key, value in row.items()}
        if existing is None:
            entries[symbol] = {"firstSeenAt": stamp, "lastSeenAt": stamp, "row": snapshot}
            changed = True
            if new_out is not None:
                new_out.append(symbol)
        else:
            if existing.get("row") != snapshot:
                existing["row"] = snapshot
                existing["lastSeenAt"] = stamp
                changed = True
            existing.setdefault("firstSeenAt", stamp)
    if not changed:
        return False
    payload = {"date": day, "rows": entries}
    # Carry any sibling keys forward. This function used to rebuild the document
    # from scratch, so the morning briefing stored alongside the rows was wiped
    # on the very next row write - the archive would have looked fine and lost
    # the briefing every few minutes.
    existing_briefing = stored.get("briefing")
    if isinstance(existing_briefing, dict):
        payload["briefing"] = existing_briefing
    # Atomic replace, never truncate-in-place: a reader (or a kill) hitting a
    # half-written file made the whole day vanish and, worse, the next write
    # re-minted every firstSeenAt - destroying the exact "when it came"
    # stamp this archive exists for (adversarial review 2026-08-23).
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    os.replace(temporary, path)
    _prune(directory, now_et)
    return True


def _prune(directory: Path, now_et: datetime) -> None:
    """Drop day files older than RETENTION_DAYS (name-sorted ISO dates)."""
    try:
        files = sorted(directory.glob("*.json"))
    except OSError:
        return
    cutoff = None
    try:
        from datetime import timedelta
        cutoff = (now_et.date() - timedelta(days=RETENTION_DAYS)).isoformat()
    except Exception:
        return
    for path in files:
        if path.stem < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
    # A crash between the tmp write and os.replace leaves an orphan the
    # *.json glob never matches; sweep any .tmp older than a day.
    try:
        import time as _time
        for orphan in directory.glob(".*.tmp"):
            if (_time.time() - orphan.stat().st_mtime) > 86400:
                orphan.unlink()
    except OSError:
        pass


def record_briefing(
    lines: object, now_et: datetime, directory: Path | str = DEFAULT_HISTORY_DIR
) -> bool:
    """Store today's morning-briefing lines beside today's rows.

    The briefing is rebuilt every few minutes all morning; each write REPLACES
    the previous one, so the archive keeps the final version of the day rather
    than a pile of drafts. Lines already carry the ** ** ticker markers, which
    is what lets the history view bold them the same way the live card does.
    """
    text = [str(line) for line in (lines or []) if str(line or "").strip()]
    if not text:
        return False
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    day = now_et.date().isoformat()
    path = _day_file(directory, day)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stored = {}
    if not isinstance(stored, dict):
        stored = {}
    briefing = {"lines": text, "generatedAt": now_et.isoformat()}
    if stored.get("briefing") == briefing:
        return False
    stored["briefing"] = briefing
    stored.setdefault("date", day)
    if not isinstance(stored.get("rows"), dict):
        stored["rows"] = {}
    # Same atomic replace as record_rows: a half-written file made a whole day
    # vanish once already (adversarial review 2026-08-23).
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(json.dumps(stored), encoding="utf-8")
    os.replace(temporary, path)
    _prune(directory, now_et)
    return True


def load_briefing(
    now_et: datetime, directory: Path | str = DEFAULT_HISTORY_DIR
) -> dict | None:
    """Today's archived morning briefing, or None.

    Exists because the live briefing lives ONLY in memory
    (``STATE._morning_briefing_payload``) and is only built 05:30-09:35 ET. Any
    backend restart after that window wipes it and ``/api/morning-briefing``
    falls back to WAITING - so the card the trader reads every single morning
    silently disappears for the rest of the day. Restarts are routine here
    (watchdog, concurrent sessions), and it was in exactly that state at 16:30
    on 2026-08-27, which is how this was noticed.

    Returns the stored ``{"lines": [...], "generatedAt": ...}`` unchanged, so the
    ** ** ticker markers survive and the UI bolds them the same way.
    """
    day = now_et.date().isoformat()
    path = _day_file(Path(directory), day)
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict):
        return None
    briefing = stored.get("briefing")
    if not isinstance(briefing, dict):
        return None
    lines = briefing.get("lines")
    if not isinstance(lines, list) or not lines:
        return None
    return briefing


def load_history(directory: Path | str = DEFAULT_HISTORY_DIR, days: int = RETENTION_DAYS) -> list[dict]:
    """Newest-first day archives: [{date, rows: [entry...]}, ...].

    Each entry is {symbol, firstSeenAt, lastSeenAt, row}; rows within a day
    are ordered by firstSeenAt so the day reads as a timeline of arrivals.
    """
    directory = Path(directory)
    try:
        files = sorted(directory.glob("*.json"), reverse=True)[: max(int(days), 0)]
    except OSError:
        return []
    archives: list[dict] = []
    for path in files:
        try:
            stored = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        entries = stored.get("rows") if isinstance(stored, dict) else None
        briefing = stored.get("briefing") if isinstance(stored, dict) else None
        if not isinstance(briefing, dict):
            briefing = None
        if not isinstance(entries, dict):
            # A day can hold a briefing and no rows - a quiet morning where the
            # scan matched nothing still produced a brief. Keep that day rather
            # than dropping it, or the briefing silently never appears on
            # exactly the mornings it is most worth reading.
            if briefing is None:
                continue
            entries = {}
        day_rows = []
        for symbol, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            day_rows.append({
                "symbol": str(symbol).upper(),
                "firstSeenAt": entry.get("firstSeenAt"),
                "lastSeenAt": entry.get("lastSeenAt"),
                "row": entry.get("row") if isinstance(entry.get("row"), dict) else {},
            })
        day_rows.sort(key=lambda item: str(item.get("firstSeenAt") or ""))
        archives.append({
            "date": str(stored.get("date") or path.stem),
            "rows": day_rows,
            "briefing": briefing,
        })
    return archives


def first_seen_map(now_et: datetime, directory: Path | str = DEFAULT_HISTORY_DIR) -> dict:
    """{symbol: firstSeenAt} for TODAY, for the live table to show.

    The archive has always known when each row arrived - that is the whole
    "so I know when it came" requirement - but the SERVED rows never carried
    it, so the live table could only show the signal's bar time. On
    2026-09-01 that read as a contradiction: the trader checked at 09:00, saw
    no AAPL, and later found an AAPL row stamped 09:00. The row was right (the
    signal sits on the 09:00 two-hour bucket) and the archive agreed it first
    appeared at 09:39:47 - only the table had no way to say so.
    """
    path = _day_file(Path(directory), now_et.date().isoformat())
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rows = stored.get("rows") if isinstance(stored, dict) else None
    if not isinstance(rows, dict):
        return {}
    out = {}
    for symbol, entry in rows.items():
        if isinstance(entry, dict) and entry.get("firstSeenAt"):
            out[str(symbol).upper()] = entry["firstSeenAt"]
    return out
