"""30-day archive of what the MomX scan matched, one JSON file per board per day.

The trader's request (2026-08-31): history tabs for Mag7 & Watchlist -- "if any
tickers scan, add it in the history, save up to 30 days ... time also, so I
know when it came to the scanner", and (revised the same evening) "if any
changes again add in history because it's live changes". The live board
repaints every 15-35s and keeps no past; this archive freezes, per ET day and
per symbol, the arrival snapshot plus one snapshot per visible SIGNAL change,
so an evening review can answer "what fired, when, what did it look like, and
did it go or fade".

A SIGNAL change is deliberately narrower than "the row changed": continuous
per-tick numbers (price, %Chg, sparkline, and -- since 2026-09-01 -- highLow)
are stored but never trigger. See ``CELL_TRIGGER_FIELDS`` for why highLow in
particular must not be put back.

Shape and hazards copied deliberately from ``premarket_scanner_history.py``
(in service since 2026-08-23): atomic temp-file + ``os.replace`` (a reader or
a kill hitting a half-written file must never cost a day), 30-day prune,
crash-orphan ``.tmp`` sweep. NOT sharing code with it: the two row schemas are
unrelated and coupling them would make each harder to change.

Layout::

    artifacts/momx_history/<Board>/<YYYY-MM-DD>.json

One directory per board -- the two boards are built by independent warmer
cycles, and a shared file would make two writers race for one target.

Stdlib-only; directory and clock injected so tests never touch the real
archive or the real clock.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

RETENTION_DAYS = 30

#: Most snapshot rows returned for a single day. One session of the 357-name
#: Watchlist reached 2509 rows / 5.0 MB by 13:52 ET on 2026-09-01 and froze the
#: phone: the browser parses and renders every row on the main thread, so even
#: after gzip cut the transfer, ~100,000 cells still had to be built and the
#: whole UI stopped responding -- tabs included. The newest rows are kept
#: (``_day_rows`` sorts oldest-first, so this takes the tail) because the most
#: recent snapshots are the ones worth seeing. Never silently: the response
#: carries ``truncated``/``totalRows``/``returnedRows`` so the table can say it.
HISTORY_MAX_DAY_ROWS = 400

#: After the arrival, snapshots for one ticker are at least this far apart.
#: A change seen sooner is FOLDED into the next eligible snapshot -- change
#: detection always compares against the last STORED snapshot, so a change
#: inside the gap is re-detected (against that same baseline) once the gap
#: has passed, and the row stored is always the current one.
SNAPSHOT_MIN_GAP_SECONDS = 60

#: Hard per-ticker per-day cap. At the cap recording stops for that ticker
#: for the day and ``truncated`` is set -- a silent cap would read as "the
#: move went quiet" when it did not.
SNAPSHOTS_PER_SYMBOL_PER_DAY = 120

#: The scan repaints every 15-35s; without coalescing, ``lastSeenAt``/``hits``
#: advancing would rewrite ~140 KB every cycle. The file is written when a
#: symbol arrives, a snapshot is appended, or this many seconds have passed
#: since the last write for the board (which is what eventually persists the
#: quiet lastSeenAt/hits advances).
WRITE_COALESCE_SECONDS = 60

#: Leaf directory name, exported so callers (service, worker) anchor it under
#: their own artifacts root without re-spelling it.
HISTORY_DIRNAME = "momx_history"
DEFAULT_HISTORY_DIR = Path("artifacts") / HISTORY_DIRNAME

#: Trigger fields: a visible change in any of these appends a snapshot.
#: Price / %Chg / sparkline move on every cycle and are deliberately NOT
#: triggers (they would bury the search in ~2,000 near-identical entries per
#: ticker per day). The fingerprint covers each cell's value, bg AND fg --
#: a cell keeping its number but flipping colour is a state change on this
#: board, because the colours encode state.
#:
#: ``highLow`` is NOT a trigger and must not be restored as one (trader,
#: 2026-09-01: "any changes of high/low don't add in history"). It is the
#: continuous float ``(close - mid) / (hh - mid)``, so it moves on
#: essentially every tick -- it is the one field guaranteed to differ every
#: cycle. Measured by replaying the real 2026-09-01 Watchlist archive at
#: ~07:30 ET: highLow was implicated in 81 of the 164 stored snapshots (49%)
#: and was the SOLE cause of 7 of them. It is still
#: STORED in every snapshot row (it is a column the trader reads in the
#: history table); only its power to CAUSE a row is removed, so it also never
#: appears in ``changed[]``.
TIMEFRAME_TRIGGER_FIELDS = ("rvol", "sqz", "skittles")
CELL_TRIGGER_FIELDS = ("color",)

#: Stored on every snapshot row but never compared: continuous per-tick floats
#: whose change is not news. Named so a reader sees the deliberate gap between
#: "kept" and "compared" rather than assuming an omission.
NON_TRIGGER_STORED_FIELDS = ("highLow",)

#: Kept on the arrival snapshot only; ~60% of a row's bytes, changing every
#: cycle, historically meaningful only at arrival.
STRIPPED_FIELDS = ("sparkline", "quoteTrend")

#: Scanner-grade detail kept off EVERY snapshot row, arrival included. The
#: grade recorder's per-row bulk (raw squeeze states, the freshness
#: timeline, the last completed 5m bar) roughly doubled each stored row, and
#: History re-parses the whole day file every build. ``grade``, ``m5``
#: state/pattern and ``gradeFresh`` icons/ageMinutes/firstToday are kept.
#: Stripped on the COPY History stores; the live payload is never touched.
#:
#: ``adx`` joined them 2026-09-22: the two cells are ~336 B per stored row,
#: about +8.5 MB a day and +15% on a file the worker RE-PARSES on every
#: build - to save a reading whose durable copy is already written on the
#: grade / pattern events (grade_log.py ``_event``), which is what the track
#: record actually reads. A History row therefore has no ADX, and the why
#: panel says so rather than showing a blank section.
GRADE_STRIPPED_FIELDS = ("sqzRaw", "adx")
GRADE_STRIPPED_SUBFIELDS = (("m5", "lastCompleted"), ("m5", "pillars"), ("gradeFresh", "timeline"))

#: ...but the Setup cell is worked out in the browser FROM those fields
#: (2026-10-02, "live setup column has so much information but history it's
#: not there"): GO / GO+ / OPT read adx["30m"] +DI/-DI, the ADX tag reads
#: adx["5m"], SQZ-fire and Skittles-break tags read the gradeFresh timeline,
#: ZS reads m5.pillars.zs. A History row keeps ONLY those pieces:
#: four numbers per ADX timeframe (not the ~336 B cells), the timeline items
#: the Setup tags read as ``gradeFresh.setupTimeline`` (a different key, so
#: the why panel never shows a filtered list as THE timeline), and pillars.zs.
SETUP_ADX_KEYS = ("plus", "minus", "adx", "prevAdx", "rising")
_SETUP_TIMELINE_WHAT = re.compile(r"^(SQZ (2h|4h|D|Wk) released|SKIT \S+ bg \S+)$")

#: RVOL timeframes scanned for peakRvol. Extra timeframes on a row are
#: included too; missing ones are simply skipped.
_RVOL_TIMEFRAMES = ("5m", "15m", "30m", "1h", "2h", "4h", "D")

#: Last persisted write per (directory, board), in the injected clock's time.
#: Keyed by directory as well as board so tests (and any second archive root)
#: never share coalescing state with the live archive.
_LAST_WRITE: dict[tuple[str, str], datetime] = {}


# ---------------------------------------------------------------------------
# paths
# ---------------------------------------------------------------------------

def board_dir(directory: Path | str, name: str) -> Path:
    """The per-board directory. Name sanitised the same way the board cache
    sanitises its filenames, so ``Mag7``/``Watchlist`` map predictably."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", str(name or "")).strip("._") or "_"
    return Path(directory) / safe


def _day_file(board_directory: Path, day: str) -> Path:
    return board_directory / f"{day}.json"


def _load_doc(path: Path) -> dict:
    """A day document, or ``{}``. A torn / truncated / garbage file is
    treated as absent, never raised."""
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return stored if isinstance(stored, dict) else {}


# ---------------------------------------------------------------------------
# change fingerprint
# ---------------------------------------------------------------------------

def _cell_state(cell: Any) -> list:
    """The DISPLAYED state of one cell: value, bg, fg. A bare (non-dict)
    value is treated as a valueless cell so a shape drift degrades to
    comparing the raw value rather than crashing."""
    if isinstance(cell, dict):
        return [cell.get("value"), cell.get("bg"), cell.get("fg")]
    return [cell, None, None]


def _fingerprint(row: dict) -> dict[str, list]:
    """Cell-path -> displayed state, over the TRIGGER fields only.

    Built by enumerating the named trigger fields, so a field the board adds
    later is ignored rather than crashed on; within a timeframe field, any
    timeframe present is covered. JSON-safe values only, so a fingerprint
    computed from a freshly built row compares equal to one recomputed from
    the same row after a disk round-trip.
    """
    fingerprint: dict[str, list] = {}
    if not isinstance(row, dict):
        return fingerprint
    for field in TIMEFRAME_TRIGGER_FIELDS:
        cells = row.get(field)
        if isinstance(cells, dict):
            for timeframe, cell in cells.items():
                fingerprint[f"{field}.{timeframe}"] = _cell_state(cell)
    for field in CELL_TRIGGER_FIELDS:
        if field in row:
            fingerprint[field] = _cell_state(row.get(field))
    if "scanReasons" in row:
        reasons = row.get("scanReasons")
        fingerprint["scanReasons"] = [
            list(reasons) if isinstance(reasons, list) else reasons, None, None
        ]
    badge = row.get("badge")
    if isinstance(badge, dict):
        fingerprint["badge.on"] = [bool(badge.get("on")), None, None]
    news = row.get("news")
    if isinstance(news, dict):
        fingerprint["news.headline"] = [news.get("headline"), None, None]
    # Setup-cell events that change no cell above (2026-10-02): a chart
    # arrow, a daily-line fire, a GO candle or MOMOX A+ appearing must get its
    # own snapshot, or History never shows the setup the live board showed.
    # Added only when present, so rows stored before this change (which
    # carry the same fields) compare equal and get no extra snapshot.
    signals = row.get("chartSignals")
    if isinstance(signals, list):
        arrows = sorted(
            "|".join(str(s.get(key) or "") for key in ("label", "family", "at"))
            for s in signals
            if isinstance(s, dict) and s.get("label") and not s.get("goneAt")
        )
        if arrows:
            fingerprint["setup.chartSignals"] = [arrows, None, None]
    day_lines = row.get("dayLines")
    fired = sorted(str(k) for k, v in day_lines.items() if v) if isinstance(day_lines, dict) else []
    if fired:
        fingerprint["setup.dayLines"] = [fired, None, None]
    m5 = row.get("m5")
    gap_go = m5.get("gapGo") if isinstance(m5, dict) else None
    if isinstance(gap_go, dict) and gap_go.get("goAt"):
        fingerprint["setup.goAt"] = [gap_go.get("goAt"), None, None]
    aplus = row.get("momoxAPlus")
    if isinstance(aplus, dict) and aplus.get("at"):
        fingerprint["setup.momoxAPlus"] = [aplus.get("at"), None, None]
    return fingerprint


def _changed_cells(previous_row: Any, current_row: dict) -> list[str]:
    """Which trigger cells visibly differ, as sorted dotted paths."""
    before = _fingerprint(previous_row if isinstance(previous_row, dict) else {})
    after = _fingerprint(current_row)
    moved = [
        path
        for path in set(before) | set(after)
        if before.get(path) != after.get(path)
    ]
    return sorted(moved)


def _peak_rvol(row: dict) -> float:
    """Max numeric ``rvol[tf].value`` across every timeframe on the row.
    Unparseable RVOL gives 0.0 -- a match with unreadable RVOL is still a
    match, and the snapshot is still recorded."""
    peak = 0.0
    cells = row.get("rvol")
    if not isinstance(cells, dict):
        return peak
    for cell in cells.values():
        value = cell.get("value") if isinstance(cell, dict) else cell
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        if number > peak:
            peak = number
    return peak


def _archive_row(row: dict, *, strip_bulk: bool) -> dict:
    """The copy of a board row a snapshot stores. Never mutates ``row``.

    Always drops the scanner-grade bulk (GRADE_STRIPPED_FIELDS and
    GRADE_STRIPPED_SUBFIELDS); ``strip_bulk`` also drops the per-cycle
    STRIPPED_FIELDS (change snapshots -- the arrival keeps its sparkline)."""
    dropped = GRADE_STRIPPED_FIELDS + (STRIPPED_FIELDS if strip_bulk else ())
    out = {key: value for key, value in row.items() if key not in dropped}
    for field, sub in GRADE_STRIPPED_SUBFIELDS:
        inner = out.get(field)
        if isinstance(inner, dict) and sub in inner:
            out[field] = {key: value for key, value in inner.items() if key != sub}
    _keep_setup_inputs(row, out)
    return out


def _keep_setup_inputs(row: dict, out: dict) -> None:
    """Put back the slim pieces of the stripped fields the Setup tags read."""
    adx = row.get("adx")
    if isinstance(adx, dict):
        slim = {
            tf: {key: cell.get(key) for key in SETUP_ADX_KEYS if key in cell}
            for tf, cell in adx.items()
            if isinstance(cell, dict)
        }
        if slim:
            out["adx"] = slim
    fresh = row.get("gradeFresh")
    timeline = fresh.get("timeline") if isinstance(fresh, dict) else None
    if isinstance(timeline, list) and isinstance(out.get("gradeFresh"), dict):
        events = [
            {"at": item.get("at"), "what": item.get("what")}
            for item in timeline
            if isinstance(item, dict) and _SETUP_TIMELINE_WHAT.match(str(item.get("what") or ""))
        ]
        if events:
            out["gradeFresh"] = {**out["gradeFresh"], "setupTimeline": events}
    m5 = row.get("m5")
    pillars = m5.get("pillars") if isinstance(m5, dict) else None
    if isinstance(pillars, dict) and pillars.get("zs") is not None and isinstance(out.get("m5"), dict):
        out["m5"] = {**out["m5"], "pillars": {"zs": pillars.get("zs")}}


def _change_row(row: dict) -> dict:
    """A change snapshot's row: the full row minus the per-cycle bulk."""
    return _archive_row(row, strip_bulk=True)


def _parse_iso(stamp: Any) -> datetime | None:
    if not isinstance(stamp, str) or not stamp.strip():
        return None
    try:
        return datetime.fromisoformat(stamp.strip())
    except ValueError:
        return None


def _seconds_between(later: datetime, earlier: datetime | None) -> float | None:
    if earlier is None:
        return None
    try:
        return (later - earlier).total_seconds()
    except TypeError:  # aware vs naive mix -- treat the gap as unknown
        return None


# ---------------------------------------------------------------------------
# recording
# ---------------------------------------------------------------------------

def record_board(
    name: str,
    payload: Any,
    now_et: datetime,
    directory: Path | str = DEFAULT_HISTORY_DIR,
) -> bool:
    """Fold one completed board build into today's archive. True if written.

    Only rows with ``scanPass: true`` are recorded. Per symbol:

    * ``firstSeenAt`` is stamped once and never moves -- that is the "when it
      came to the scanner" the trader asked for. A row that drops off and
      returns the same day keeps its original stamp.
    * The arrival snapshot (full row, sparkline included) is written once and
      never overwritten.
    * A later snapshot is appended when a TRIGGER cell visibly changes, at
      least ``SNAPSHOT_MIN_GAP_SECONDS`` after the previous snapshot, up to
      ``SNAPSHOTS_PER_SYMBOL_PER_DAY`` per day (then ``truncated`` is set).
    * ``lastSeenAt``/``hits`` advance in the loaded document; the write
      coalescing rule is what eventually persists them. The document is
      re-read from disk each cycle, so ``hits`` counts PERSISTED
      observations (roughly one per minute a quiet ticker stays on the
      board), not raw 15-35s scan cycles -- forcing a write per cycle just
      to count them would defeat the coalescing this module exists for.

    Never raises: any failure -- unwritable disk, garbage payload -- returns
    False, because a history bug must never cost a scan.
    """
    try:
        return _record_board(name, payload, now_et, directory)
    except Exception:  # noqa: BLE001 - degrade to "no history this cycle"
        return False


def _record_board(
    name: str, payload: Any, now_et: datetime, directory: Path | str
) -> bool:
    rows = payload.get("rows") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return False  # garbage payload, not a completed board build
    live = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("symbol") and row.get("scanPass")
    ]
    board_directory = board_dir(directory, name)
    day = now_et.date().isoformat()
    path = _day_file(board_directory, day)
    if not live:
        # A day with no matches is an ARCHIVED day with zero rows, not a
        # skipped one -- "a quiet day is a fact worth seeing" (spec, empty
        # states; restored 2026-08-31 review, both implementers had
        # narrowed this to no-file). Materialise the day file ONCE, empty,
        # so the day-nav can land on it; after that a quiet cycle has
        # nothing new to say and writes nothing. Days the warmer runs
        # through outside market days (weekends) archive as quiet too.
        if path.exists():
            return False
        return _write_document(
            board_directory, path,
            {"list": str(name), "date": day, "rows": {}},
            name, directory, now_et,
        )
    stored = _load_doc(path)
    entries = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
    stamp = now_et.isoformat()
    arrived = False
    appended = False
    truncated_flipped = False

    for row in live:
        symbol = str(row["symbol"]).strip().upper()
        entry = entries.get(symbol)
        if not isinstance(entry, dict):
            entry = None
        if entry is None:
            entries[symbol] = {
                "firstSeenAt": stamp,
                "lastSeenAt": stamp,
                "hits": 1,
                "truncated": False,
                "snapshots": [
                    {
                        "at": stamp,
                        "changed": [],
                        "peakRvol": _peak_rvol(row),
                        "row": _archive_row(row, strip_bulk=False),
                    }
                ],
            }
            arrived = True
            continue
        # A symbol that drops off and returns the same day: same entry, same
        # firstSeenAt (it is the same day's move). setdefault only repairs a
        # torn entry that lost the field.
        entry.setdefault("firstSeenAt", stamp)
        entry["lastSeenAt"] = stamp
        try:
            entry["hits"] = int(entry.get("hits") or 0) + 1
        except (TypeError, ValueError):
            entry["hits"] = 1
        snapshots = entry.get("snapshots")
        if not isinstance(snapshots, list) or not snapshots:
            # Torn entry with no snapshots: re-mint an arrival so the day
            # stays readable rather than raising.
            entry["snapshots"] = [
                {"at": stamp, "changed": [], "peakRvol": _peak_rvol(row),
                 "row": _archive_row(row, strip_bulk=False)}
            ]
            appended = True
            continue
        entry["snapshots"] = snapshots
        last = snapshots[-1] if isinstance(snapshots[-1], dict) else {}
        changed = _changed_cells(last.get("row"), row)
        if not changed:
            continue
        if entry.get("truncated"):
            continue
        if len(snapshots) >= SNAPSHOTS_PER_SYMBOL_PER_DAY:
            entry["truncated"] = True
            truncated_flipped = True
            continue
        gap = _seconds_between(now_et, _parse_iso(last.get("at")))
        if gap is not None and gap < SNAPSHOT_MIN_GAP_SECONDS:
            continue  # folded: next eligible pass compares against this same baseline
        snapshots.append(
            {
                "at": stamp,
                "changed": changed,
                "peakRvol": _peak_rvol(row),
                "row": _change_row(row),
            }
        )
        appended = True

    write_key = (os.fspath(directory), str(name))
    since_last = _seconds_between(now_et, _LAST_WRITE.get(write_key))
    write_due = since_last is None or since_last >= WRITE_COALESCE_SECONDS
    if not (arrived or appended or truncated_flipped or write_due):
        return False

    document = {"list": str(name), "date": day, "rows": entries}
    return _write_document(board_directory, path, document, name, directory, now_et)


def _write_document(
    board_directory: Path,
    path: Path,
    document: dict,
    name: str,
    directory: Path | str,
    now_et: datetime,
) -> bool:
    """Atomic replace, never truncate-in-place: a reader (or a kill)
    hitting a half-written file must never lose the day or re-mint
    firstSeenAt (the premarket archive's adversarial review, 2026-08-23).
    A successful write stamps the coalescing clock and prunes."""
    try:
        board_directory.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        return False
    _LAST_WRITE[(os.fspath(directory), str(name))] = now_et
    _prune(board_directory, now_et)
    return True


def _prune(board_directory: Path, now_et: datetime) -> None:
    """Drop day files older than RETENTION_DAYS (name-sorted ISO dates).

    Day exactly RETENTION_DAYS old survives (stem == cutoff); day 31 goes.
    Also sweeps ``.tmp`` orphans older than a day -- a crash between the tmp
    write and os.replace leaves one the ``*.json`` glob never matches.
    """
    try:
        files = sorted(board_directory.glob("*.json"))
    except OSError:
        return
    try:
        cutoff = (now_et.date() - timedelta(days=RETENTION_DAYS)).isoformat()
    except Exception:  # noqa: BLE001
        return
    for path in files:
        if path.stem < cutoff:
            try:
                path.unlink()
            except OSError:
                pass
    try:
        for orphan in board_directory.glob(".*.tmp"):
            if (time.time() - orphan.stat().st_mtime) > 86400:
                orphan.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# reading -- the /api/momx-scanner/history payloads
# ---------------------------------------------------------------------------

_DAY_STEM = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def list_days(name: str, directory: Path | str = DEFAULT_HISTORY_DIR) -> list[str]:
    """Archived ISO dates for one board, newest first."""
    try:
        stems = [
            path.stem
            for path in board_dir(directory, name).glob("*.json")
            if _DAY_STEM.match(path.stem)
        ]
    except OSError:
        return []
    return sorted(stems, reverse=True)


def _snapshot_entries(symbol: str, entry: dict) -> list[dict]:
    """One API row per stored snapshot for one symbol."""
    snapshots = entry.get("snapshots") if isinstance(entry.get("snapshots"), list) else []
    rows: list[dict] = []
    for index, snapshot in enumerate(snapshots):
        if not isinstance(snapshot, dict):
            continue
        rows.append(
            {
                "symbol": symbol,
                "at": snapshot.get("at"),
                "isArrival": index == 0,
                "changed": snapshot.get("changed") if isinstance(snapshot.get("changed"), list) else [],
                "firstSeenAt": entry.get("firstSeenAt"),
                "lastSeenAt": entry.get("lastSeenAt"),
                "hits": entry.get("hits"),
                "truncated": bool(entry.get("truncated")),
                "peakRvol": snapshot.get("peakRvol"),
                "row": snapshot.get("row") if isinstance(snapshot.get("row"), dict) else {},
            }
        )
    return rows


def _day_rows(name: str, day: str, directory: Path | str) -> list[dict]:
    stored = _load_doc(_day_file(board_dir(directory, name), day))
    entries = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
    rows: list[dict] = []
    for symbol, entry in entries.items():
        if isinstance(entry, dict):
            rows.extend(_snapshot_entries(str(symbol).upper(), entry))
    # Ascending by time: the day reads as the timeline the trader experienced
    # (SNOW 8:01 arrives, SNOW 9:14 strengthens, DG 10:02 arrives).
    rows.sort(key=lambda item: str(item.get("at") or ""))
    return rows


def _page(rows: list, offset: int, *, newest_first: bool = False) -> dict:
    """One page of ``rows``, counted back from the NEWEST.

    Page 1 (offset 0) is always what just happened, which is what he opens the
    tab to see; older pages are reached by walking backwards. Offsets are in
    ROWS, not pages, so the server never has to agree with the client about a
    page size.

    ``newest_first`` says the caller already reversed the list (the per-symbol
    view groups by day, newest day first), in which case the window runs
    forward from the start instead of backwards from the end.

    Every counter the UI needs to be honest travels with the page: an offset
    past the end returns an EMPTY page rather than silently clamping to the
    last one, because a "next" button that quietly re-shows the same rows is
    worse than one that runs out.
    """
    total = len(rows)
    try:
        start = max(0, int(offset))
    except (TypeError, ValueError):
        start = 0
    size = HISTORY_MAX_DAY_ROWS

    if newest_first:
        window = rows[start:start + size]
    else:
        # Ascending by time: page 1 is the LAST `size` rows.
        end = max(0, total - start)
        window = rows[max(0, end - size):end]

    return {
        "rows": window,
        "totalRows": total,
        "returnedRows": len(window),
        "offset": start,
        "pageSize": size,
        "hasMore": start + len(window) < total,
        "truncated": total > len(window),
    }


#: Session windows for the History bar's counts, as ET minute-of-day bounds.
#: Named the way he talks about the day rather than by clock times, and shown
#: BEFORE he clicks so a quiet open is visible without paging into it.
SESSION_WINDOWS = (
    ("Overnight", 0, 4 * 60),
    ("Pre", 4 * 60, 9 * 60 + 30),
    ("Open", 9 * 60 + 30, 10 * 60),
    ("Morning", 10 * 60, 12 * 60),
    ("Midday", 12 * 60, 15 * 60),
    ("Power", 15 * 60, 16 * 60),
    ("After", 16 * 60, 24 * 60),
)


def build_time_index(rows: list) -> list:
    """``[[HH:MM, first row index, rows in that minute], ...]``, ascending.

    Lets the browser JUMP to a time - move the page - rather than FILTER to it.
    That distinction is the whole design: a day holds ~11,770 snapshots and a
    page is 400, so a filter would either lie about what it searched or force
    every counter on the bar ("44 entries", "showing 1-44 of 44", Newer/Older)
    to be redefined. Choosing an offset is exactly what the pager already does,
    so all of them stay correct with no changes.

    The clock is SLICED from the stored stamp (``at[11:16]``), never reparsed.
    Those stamps are offset-aware ET written by the recorder; re-deriving the
    hour locally would read this machine's clock, which runs an hour behind ET.

    Deduped per minute, so a busy minute is one entry rather than forty.
    """
    index: list = []
    for position, row in enumerate(rows or ()):
        stamp = str((row or {}).get("at") or "")
        clock = stamp[11:16]
        if len(clock) != 5 or clock[2] != ":":
            continue
        if index and index[-1][0] == clock:
            index[-1][2] += 1
            continue
        index.append([clock, position, 1])
    return index


def session_counts(index: list) -> list:
    """``[[name, rows], ...]`` for the windows above, zero-count ones kept.

    Built from the index rather than the rows so it costs nothing extra, and
    kept as a LIST of pairs so the order is the trading day's order and not a
    dict's.
    """
    totals = {name: 0 for name, _, _ in SESSION_WINDOWS}
    for clock, _position, count in index or ():
        try:
            minute = int(clock[:2]) * 60 + int(clock[3:])
        except ValueError:
            continue
        for name, start, end in SESSION_WINDOWS:
            if start <= minute < end:
                totals[name] += count
                break
    return [[name, totals[name]] for name, _, _ in SESSION_WINDOWS]


def history_response(
    name: str,
    date: str | None = None,
    symbol: str | None = None,
    directory: Path | str = DEFAULT_HISTORY_DIR,
    offset: int = 0,
) -> dict:
    """The payload for GET /api/momx-scanner/history.

    * ``list`` only: newest archived day, plus ``days`` for the day nav.
    * ``list`` + ``date``: that day; an absent day is an empty payload,
      never an error -- a quiet day is a fact worth seeing.
    * ``list`` + ``symbol``: that ticker's snapshots across every archived
      day, newest day first (each entry gains a ``date``); ``date`` is null.
    * ``list`` + ``symbol`` + ``date``: that ticker on that ONE day (asked
      2026-09-05: "search like time, or date or search or all three"). The
      symbol branch used to ignore ``date`` entirely, so the day picker did
      nothing while a ticker was typed. ``date`` echoes the day back.
    """
    days = list_days(name, directory)
    base = {"list": str(name), "days": days, "retentionDays": RETENTION_DAYS}
    if symbol:
        wanted = str(symbol).strip().upper()
        only_day = str(date).strip() if date is not None else ""
        if only_day and not _DAY_STEM.match(only_day):
            # Same rule as the day branch: a non-ISO date never reaches the
            # filesystem, it answers as an absent day.
            return {**base, "date": None, "symbol": wanted, "rows": []}
        rows: list[dict] = []
        for day in days:  # newest first
            if only_day and day != only_day:
                continue
            stored = _load_doc(_day_file(board_dir(directory, name), day))
            entries = stored.get("rows") if isinstance(stored.get("rows"), dict) else {}
            entry = entries.get(wanted)
            if not isinstance(entry, dict):
                continue
            for item in _snapshot_entries(wanted, entry):
                item["date"] = day
                rows.append(item)
        # A single ticker across 30 days is normally small, but a name that
        # matches on every scan of every day is not -- page it on the same
        # terms rather than discovering the exception on a bad morning. These
        # rows are already newest-first, so the page window runs FORWARD.
        return {
            **base,
            "date": only_day or None,
            "symbol": wanted,
            **_page(rows, offset, newest_first=True),
        }
    if date is not None:
        wanted_day = str(date).strip()
        if not _DAY_STEM.match(wanted_day):
            # Not an ISO day, not a path. Without this check a crafted
            # ``date=../../x`` walked OUT of the archive and read any
            # {"rows": {...}}-shaped .json on disk (review, 2026-08-31).
            # An invalid date answers like an absent day: empty, no error.
            return {**base, "date": None, "rows": []}
    else:
        wanted_day = days[0] if days else None
    if not wanted_day:
        return {**base, "date": None, "rows": []}
    day_rows = _day_rows(name, wanted_day, directory)
    # The index rides along on the DAY branch only. The per-symbol branch is
    # already across days, where a single wall-clock time means nothing.
    index = build_time_index(day_rows)
    return {
        **base,
        "date": wanted_day,
        "timeIndex": index,
        "sessions": session_counts(index),
        "pageSize": HISTORY_MAX_DAY_ROWS,
        **_page(day_rows, offset),
    }
