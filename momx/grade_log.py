"""Worker-side recorder for the MomX scanner grade (experimental).

Spec: docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md
("What gets recorded", "Momentum now" -> Freshness, "Error handling").

One :class:`GradeLog` lives in the MomX worker (``momx/service.py``) and is
handed every completed board build. :meth:`GradeLog.apply` does three things:

1. FRESHNESS. Adds ``row["gradeFresh"]`` to every row in ``payload["rows"]``
   and ``payload["rest"]``::

       {"icons": ["SKIT", "RVOL", "SQZ", "ADX", "NEWS"],  # seen in the last 15 min
        "ageMinutes": int | None,                    # since firstToday[current letter];
                                                     #   None outside the session
        "firstToday": {"A+": {"at": iso, "price": float} | None, "A": ..., "B": ...,
                       "patterns": {"explosive": {"at", "price"} | None, "steady": ...}},
        "timeline": [{"at": iso, "what": str}]}      # today's observed changes,
                                                     #   newest PAYLOAD_TIMELINE_CAP only

   A timeline item is only ever an OBSERVED change between two consecutive
   scans of the same symbol (15-35 s apart), so its time is accurate to one
   scan. After a worker restart the timeline is blank until the next change
   is seen -- never back-filled with a guess. ``firstToday`` DOES survive a
   restart: it is reloaded from today's event file.

2. GRADE TAPE -- every ``TAPE_EVERY_SECONDS``, one sample of every symbol,
   for ``TAPE_BOARDS`` (Watchlist, Mag7) only and only on weekdays
   04:00-19:59 ET (:func:`_in_tape_hours`) -- nothing reads other boards or
   overnight/weekend samples, and 24/7 sampling was ~51 MB/day per board. Append-only JSON Lines, one line per 5-minute sample::

       <dir>/momx_grade_tape/<Board>/<YYYY-MM-DD>.jsonl   (<Board> sanitised as history.board_dir)
       {"t": iso, "board": "Watchlist", "n": <symbols in this sample>,
        "entries": {"NVDA": {"t": iso, "last": float|None, "letter": "A+"|"A"|"B"|None,
                             "checks": {...}, "reasons": [...], "m5state": str|None,
                             "chart": str|None, "trigger": float|None,
                             "pattern": str|None}, ...}}

   WHY JSONL and not one JSON document: rewriting a whole day's document
   (358 symbols x 192 samples ~= 33 MB) measured ~480 ms at the close, over
   the 300 ms budget and growing all day; one appended line is ~175 KB. A
   crash mid-append can leave a torn LAST line; readers must skip lines that
   do not parse. :func:`read_tape` does exactly that and returns the
   per-symbol shape ``{"board","date","entries":{SYMBOL:[entry, ...]}}`` --
   use it rather than parsing the file by hand.

3. GRADE EVENTS -- two kinds, both latched per ET day and reloaded from the
   day's file after a restart. Events are logged, and ``firstToday``
   latched, ONLY in the regular session: weekdays 09:30:00-15:59:59 ET
   (:func:`_in_session`). An overnight build must not latch "first A+ today
   00:00" and hide the session's real first A+ (which is what the back-test
   measured). Outside the session ``ageMinutes`` is None; freshness icons
   and the timeline keep working at any hour.

   * ``"kind": "letter"`` -- the FIRST time each symbol reaches each letter
     (A+, A and B are latched independently).
   * ``"kind": "pattern"`` -- the FIRST time each symbol's ``m5.pattern`` is
     "explosive" or "steady" while ``m5.state == "building"``, once per
     (symbol, pattern): an Explosive and a later Steady are two events.
     ``letter`` is the row's letter at that moment, or None -- these exist
     to record moves the letter missed (NFLX 2026-09-21: no letter,
     "Steady - Building") so they can be compared with lettered setups.

   A file written before "kind" existed holds letter events only; readers
   treat a missing "kind" as "letter". Whole JSON document, rewritten
   (temp file + os.replace) only on a scan that adds an event::

       <dir>/momx_grade_events/<Board>/<YYYY-MM-DD>.json
       {"board": ..., "date": ..., "events": [
         {"board","symbol","kind","letter","at","price","reasons","checks",
          "push": {"rvol": {tf: {"value","bg","barAt"}},      # 5m..4h
                   "news": {"headline","at","ageHours"}},
          "sqzRaw", "skittles": {tf: {"fg","bg","bgChangedAt"}},  # all 8 tfs
          "hlDegree", "lastCompleted5m", "m5", "pctChange",
          "pattern", "momentum",                              # m5.pattern / m5.state
          "adx": {"5m": {...}, "30m": {...}},   # columns.adx_cell, RECORDED ONLY
          "session": "second30m"}]}             # see session_window()

   ``bgChangedAt`` is the last time this worker SAW that timeframe's SKIT bg
   change between consecutive scans (any colour to any colour), None if no
   change has been seen since the worker started.

Files older than ``RETENTION_DAYS`` are pruned once per ET day.

4. OUTCOMES + TRACK RECORD -- :meth:`GradeLog.nightly` fills
   ``event["outcome"]`` from 5-minute bars (fetched with a depth reaching the
   oldest pending date, at most ``CATCHUP_MAX_DAYS``)::

       {"p5","p15","p30","p60",   # % from event price to the close of the LAST
                                  #   bar whose END (start + 5 min) is <= event +
                                  #   N min, among bars ending after the event.
                                  #   None when the bars do not reach that far
                                  #   yet, incl. a horizon past the 16:00 end.
        "close",                  # % to the close of the last bar starting at
                                  #   or before 15:55
        "maxFav", "maxAdv"}       # % to the max high / min low of bars starting
                                  #   >= the event, up to and including 15:55

   e.g. an event at 10:02:30: p5 = the 10:00 bar (ends 10:05), p15 = the 10:10
   bar (ends 10:15), p60 = the 10:55 bar (ends 11:00).

   It scores every unscored event (letter and pattern alike) in files dated within
   ``CATCHUP_MAX_DAYS``: past dates at any time (a missed evening is caught up
   the next day), today's only at/after 16:15. A symbol whose fetch returned
   no bar on the event's date keeps no ``outcome`` and is retried after
   ``NIGHTLY_RETRY_MINUTES`` -- a partial fetch never marks it done.
   An event at/after 16:00 gets all-None (there is no session left to score)
   and is not counted. Whenever an outcome is added it rebuilds
   ``<dir>/momx_grade_record.json``; ``"builtFor"`` = today is stamped only when
   today's evening run left nothing unscored (the once-per-day guard across
   threads and restarts). The record covers every event file still on disk --
   so the track record is a rolling ``RETENTION_DAYS`` window. One event per
   (day, symbol, letter) -- or (day, symbol, pattern) for a pattern event: a
   symbol on both Watchlist and Mag7 is one trade, not two (Watchlist's copy
   wins). Letter events feed "letters"/"byMomentum"/"byPattern"/"byFresh"/
   "unscored"/"days"; pattern events feed ONLY "patternEvents"/
   "patternUnscored"/"patternDays" (``{"explosive"|"steady":
   {"A+"|"A"|"B"|"none": ...}}``, bucketed by the letter the row had at the
   time), so nothing is counted twice and a pattern-only day never masquerades
   as a day with letter data. :func:`record_response` reads the two halves
   independently: it falls back to the History back-test for letters (see its
   own docstring) whenever "days" is empty, but still surfaces the recorded
   patternEvents/patternUnscored/patternDays whenever "patternDays" is
   non-empty, tagging that half ``"patternSource": "recorded"``.
   :func:`record_response` and :func:`tape_response` are the worker's GET
   payloads.

   THE REAL UPSTREAM COST OF A RETRY, AND THE CAP ON IT. Each retry is not
   free: ``fetch_5m`` runs the Schwab volume correction unconditionally by
   default -- one uncached Schwab price-history call per pending symbol, on
   every retry, every ``NIGHTLY_RETRY_MINUTES``, all day, for up to
   ``CATCHUP_MAX_DAYS`` days -- even though this fetch only reads OHLC and
   never reads volume. That is a sustained burst of uncached Schwab calls
   this worker never needed, and this app's home IP has been blocked by
   Schwab's CDN before for far smaller bursts. Two independent fixes:

   * The fetch passes ``schwab_volume=False`` (``momx/feed.py::fetch_5m``,
     forwarded to ``_fetch_tape``), which skips that Schwab leg entirely.
     Every other caller of ``fetch_5m`` keeps the default ``True``.
   * A (symbol, event date) pair is retried at most ``MAX_FETCH_ATTEMPTS``
     (4) times. On the 4th consecutive miss it is given up on:
     ``event["outcome"] = {"unavailable": True}``, which stops it being
     pending (so it is never fetched again) and excludes it from every
     :func:`build_record` stats block; it is counted separately, per letter,
     as ``record["unscored"]`` (pattern events: ``record["patternUnscored"]``).
     The attempt counter (``self._fetch_attempts``) is in-memory only, so a worker restart resets it to zero for any symbol
     still mid-count -- accepted, since that only costs a few more of the
     same small, volume-free fetches, never an unbounded burst.

:meth:`GradeLog.apply` NEVER raises: a recorder bug costs a recording, never
a scan. A failed disk write loses that write only. :meth:`GradeLog.nightly`
never raises either; a failure returns False and is retried after
``NIGHTLY_RETRY_MINUTES``.
"""
from __future__ import annotations

import json
import os
import re
import statistics
import threading
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping
from zoneinfo import ZoneInfo

from momx.grade import PUSH_RVOL_TFS, RVOL_BULL_BG, SKIT_CROSS_BG, SKIT_TFS, sets_for  # noqa: F401
from momx.history import board_dir

FRESH_MINUTES = 15
TAPE_EVERY_SECONDS = 300
RETENTION_DAYS = 30

TAPE_DIRNAME = "momx_grade_tape"
EVENTS_DIRNAME = "momx_grade_events"
LETTERS = ("A+", "A", "B")
#: m5.pattern values that make a PATTERN event (with m5.state == "building").
PATTERNS = ("explosive", "steady")
PATTERN_STATE = "building"
#: Letter buckets of the pattern-event track record: the row's letter, or none.
PATTERN_LETTERS = LETTERS + ("none",)
#: "ADX" sits after SQZ; frontend/src/momxGrade.js FRESH_ICON_ORDER mirrors this.
ICON_ORDER = ("SKIT", "RVOL", "SQZ", "ADX", "NEWS")
#: The timeframes columns.adx_cell records, in the order the timeline reads them.
ADX_TFS = ("5m", "30m")
TIMELINE_CAP = 40
#: Timeline items sent in the live payload (the newest N). The in-memory
#: timeline keeps TIMELINE_CAP for the 15-minute icon window; each item is
#: ~70 bytes on every row of every 15 s poll, so the payload gets fewer.
PAYLOAD_TIMELINE_CAP = 10

#: Letter/pattern events and the firstToday latch only happen in the regular
#: session (weekdays, ET): the back-test measured 09:30-15:30, and a latch at
#: 00:00 from an overnight build would hide the session's real first A+.
SESSION_OPEN = dtime(9, 30)
SESSION_CLOSE = dtime(16, 0)   # exclusive: 15:59:59 is the last latching second
#: The grade tape is sampled on weekdays 04:00-19:59 ET (pre + regular + post);
#: nothing reads an overnight or weekend sample.
TAPE_OPEN = dtime(4, 0)
TAPE_CLOSE = dtime(20, 0)      # exclusive
#: tape_response looks back at most this many calendar days before today.
TAPE_LOOKBACK_DAYS = 14
#: Past-day (symbol, file) results kept by tape_response, LRU.
TAPE_CACHE_SIZE = 256

RECORD_NAME = "momx_grade_record.json"
#: Written by scripts/momx_grade_backtest.py; shown until a recorded day exists.
BACKTEST_NAME = "momx_grade_record_backtest.json"
NIGHTLY_AFTER = dtime(16, 15)
NIGHTLY_RETRY_MINUTES = 15
#: How far back nightly() looks for unscored events, and the deepest bar
#: fetch it will ask for (calendar days).
CATCHUP_MAX_DAYS = 10
#: A (symbol, date) that comes back without a covering bar this many nightly
#: runs in a row is given up on: marked outcome={"unavailable": True} instead
#: of being retried forever. Counted in memory only (self._fetch_attempts),
#: so a worker restart resets the count -- acceptable, since it only costs a
#: few more of the same small fetches, never an unbounded one.
MAX_FETCH_ATTEMPTS = 4
CLOSE_BAR = dtime(15, 55)
OUTCOME_MINUTES = (5, 15, 30, 60)
OUTCOME_KEYS = ("p5", "p15", "p30", "p60", "close", "maxFav", "maxAdv")
#: Boards the chart's grade tape is read from, in precedence order.
TAPE_BOARDS = ("Watchlist", "Mag7")

_ET = ZoneInfo("America/New_York")


def _et(now: datetime) -> datetime:
    """``now`` in ET; a naive time is taken to be ET already."""
    return now.astimezone(_ET) if now.tzinfo else now.replace(tzinfo=_ET)


def _in_session(now: datetime) -> bool:
    """True on a weekday between 09:30:00 and 15:59:59 America/New_York.

    Only then are letter/pattern events logged and firstToday latched
    (market holidays are not special-cased: the board is quiet then)."""
    local = _et(now)
    return local.weekday() < 5 and SESSION_OPEN <= local.time() < SESSION_CLOSE


#: ET minute-of-day boundaries of the trading day, taken from the CHART's own
#: session lines so a recorded event's window is the window the trader sees
#: drawn on the chart (frontend/src/App.jsx:10755-10765):
#:
#:   minute 570 = 09:30 "Open (Retail)"
#:   minute 600 = 10:00 "First 30m (Smart)"
#:   minute 690 = 11:30 "First 2h (Retracement)"
#:   minute 810 = 13:30 "Second 30m (Smart)"
#:   minute 900 = 15:00 "Power Hour"
#:   minute 960 = 16:00 "Close"
#:
#: Each entry is (first minute of the window, name); the list is scanned from
#: the bottom, so a minute at or after a boundary belongs to THAT window
#: (09:30:00 is first30m, 09:29:59 is premarket).
SESSION_OPEN_MINUTE = 570        # 09:30 Open (Retail)
SESSION_FIRST30_END = 600        # 10:00 First 30m (Smart)
SESSION_MORNING_END = 690        # 11:30 First 2h (Retracement)
SESSION_MIDDAY_END = 810         # 13:30 Second 30m (Smart)
SESSION_SECOND30_END = 900       # 15:00 Power Hour
SESSION_CLOSE_MINUTE = 960       # 16:00 Close
SESSION_WINDOWS = (
    (0, "premarket"),
    (SESSION_OPEN_MINUTE, "first30m"),
    (SESSION_FIRST30_END, "morning"),
    (SESSION_MORNING_END, "midday"),
    (SESSION_MIDDAY_END, "second30m"),
    (SESSION_SECOND30_END, "powerHour"),
    (SESSION_CLOSE_MINUTE, "after"),
)


def session_window(moment: datetime | None) -> str | None:
    """Which chart session window ``moment`` (ET) falls in, or None.

    The reason every recorded event carries this: the trader's question about
    ADX is not "does a +DI cross work" but "does it work in the Second 30m
    (Smart) window" - and that question cannot be asked afterwards unless the
    window was stamped on the event when it happened.

    This is a CLOCK reading, not a calendar one: on a market holiday it still
    names a real window. Display-only and deliberately left alone - events
    only ever log through ``_in_session`` (see ``_row``), so a holiday
    produces no events for a holiday window to mislabel.
    """
    if not isinstance(moment, datetime):
        return None
    local = _et(moment)
    minute = local.hour * 60 + local.minute
    name = SESSION_WINDOWS[0][1]
    for start, window in SESSION_WINDOWS:
        if minute >= start:
            name = window
    return name


def _in_tape_hours(now: datetime) -> bool:
    """True on a weekday between 04:00:00 and 19:59:59 America/New_York."""
    local = _et(now)
    return local.weekday() < 5 and TAPE_OPEN <= local.time() < TAPE_CLOSE


def _today_et() -> str:
    """The current ET calendar date, as an isoformat string.

    A thin, monkeypatchable seam so ``status()`` can be tested without
    patching the ``datetime`` builtin. Production always calls this with
    no override -- the real wall clock -- which is the point of the fix:
    ``status()`` must reflect "today" right now, not whatever ET day the
    last scan happened to land on.
    """
    return datetime.now(_ET).date().isoformat()


def _atomic_write_json(path: Path, doc: Any) -> bool:
    return _atomic_write_text(path, json.dumps(doc, separators=(",", ":")))


def _atomic_write_text(path: Path, text: str) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Thread id too: nightly() and apply() may write from different threads.
        tmp = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        return True
    except OSError:
        return False  # a lost write costs that write, never the build


def _event_json(event: Mapping) -> str:
    # Readable separators on purpose: the event file is small enough to be
    # opened by hand, and '"letter": "A+"' is what a person greps for.
    return json.dumps(event, separators=(", ", ": "))


def _append_line(path: Path, doc: Any) -> bool:
    """Append one JSON line. If an earlier append was torn by a crash (no
    trailing newline), end that line first, so the torn fragment stays its own
    unreadable line instead of swallowing this good one."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = json.dumps(doc, separators=(",", ":")).encode("utf-8") + b"\n"
        with path.open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() > 0:
                handle.seek(-1, os.SEEK_END)
                if handle.read(1) != b"\n":
                    data = b"\n" + data
            handle.seek(0, os.SEEK_END)
            handle.write(data)
        return True
    except OSError:
        return False


# Board directory names are sanitised exactly as the History archive does
# (history.board_dir): a user-named list may hold characters Windows refuses.
def _tape_path(directory: Path, board: str, day: str) -> Path:
    return board_dir(Path(directory) / TAPE_DIRNAME, board) / f"{day}.jsonl"


def _events_path(directory: Path, board: str, day: str) -> Path:
    return board_dir(Path(directory) / EVENTS_DIRNAME, board) / f"{day}.json"


# Header of a COMPLETE tape line (the writer puts "t", "board", "n" first and
# every line ends with "}"); a torn line fails the [^\n]*\}$ tail.
_TAPE_LINE = re.compile(
    rb'^\{"t":"([^"]+)","board":"(?:[^"\\\n]|\\.)*","n":(\d+),"entries":\{[^\n]*\}\r?$',
    re.MULTILINE,
)


def _tape_summary(path: Path) -> tuple[int, datetime | None]:
    """(symbol-samples, latest sample time) of a tape file WITHOUT json-parsing it.

    A full json.loads of a day's tape (~33 MB at the close) measured ~450 ms,
    which a restarted worker would pay inside its first build. Every line
    written by this module starts with a fixed header carrying its time and
    entry count, so one C-level regex scan over the bytes is enough. A torn
    line is not counted, matching :func:`read_tape`, which skips it.
    """
    try:
        data = path.read_bytes()
    except OSError:
        return 0, None
    count, last = 0, None
    for match in _TAPE_LINE.finditer(data):
        count += int(match.group(2))
        at = _parse_iso(match.group(1).decode("ascii", "replace"))
        if at is not None and (last is None or at > last):
            last = at
    return count, last


def _tape_lines(path: Path) -> list[dict]:
    """Every parseable line of a tape file; a torn or garbage line is skipped."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    lines = []
    for raw in text.splitlines():
        try:
            doc = json.loads(raw)
        except ValueError:
            continue
        if isinstance(doc, dict) and isinstance(doc.get("entries"), dict):
            lines.append(doc)
    return lines


def read_tape(directory: Path, board: str, day: str) -> dict | None:
    """One board-day of the grade tape as ``{"board","date","entries":{SYMBOL:[entry]}}``.

    Entries per symbol are in time order. None when the file is missing or
    holds no readable sample -- callers must show "unavailable", never a guess.
    """
    lines = _tape_lines(_tape_path(directory, board, day))
    if not lines:
        return None
    entries: dict[str, list] = {}
    for doc in lines:
        for symbol, entry in doc["entries"].items():
            entries.setdefault(symbol, []).append(entry)
    return {"board": board, "date": day, "entries": entries}


def _map(value: Any) -> Mapping:
    return value if isinstance(value, Mapping) else {}


def _cell(row: Mapping, section: str, tf: str) -> Mapping:
    return _map(_map(row.get(section)).get(tf))


def _bgs(row: Mapping, section: str) -> dict[str, Any]:
    return {tf: _map(c).get("bg") for tf, c in _map(row.get(section)).items()}


def _price(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _adx_tf(row: Mapping, tf: str) -> Mapping:
    """One timeframe of ``row["adx"]`` (columns.adx_cell), or an empty map.

    ``row["adx"]`` is None on a row built before this field existed, on a
    History row replayed from the archive, and whenever the computation itself
    failed - all three must read as "no ADX", never as a crash.
    """
    return _map(_map(row.get("adx")).get(tf))


def _adx_timeline_text(tf: str, cross: str, cell: Mapping) -> str:
    """``ADX 5m bull cross (ADX 35^)`` - the arrow only while ADX is rising.

    The ADX value is the trend STRENGTH behind the direction change: a cross
    with ADX at 12 and a cross with ADX at 35 are not the same event, and the
    whole reason this is being recorded is to find out whether that difference
    pays.
    """
    text = f"ADX {tf} {cross} cross"
    value = cell.get("adx")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        arrow = "↑" if cell.get("rising") else ""
        text += f" (ADX {value:.0f}{arrow})"
    return text


def _parse_iso(value: Any) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


@dataclass
class _Symbol:
    """What this worker has seen of one (board, symbol) since it started."""

    prev: dict | None = None
    day: str = ""
    timeline: list = field(default_factory=list)  # (at: datetime, kind, what)
    bg_changed: dict = field(default_factory=dict)  # SKIT tf -> iso
    #: ADX tf -> (barAt, cross) already written to the timeline. A per-BAR
    #: latch, not a per-scan edge: see the ADX block in _row().
    adx_logged: dict = field(default_factory=dict)


@dataclass
class _Day:
    """One board's recording state for one ET date."""

    day: str
    first: dict = field(default_factory=dict)  # symbol -> letter -> {"at","price","_dt"}
    patterns: dict = field(default_factory=dict)  # symbol -> pattern -> {"at","price"}
    events: list = field(default_factory=list)
    # Each event serialized ONCE: rewriting the day's file is then a string
    # join, not a re-serialization of every earlier event (a 3 MB file took
    # ~130-230 ms to re-dump; the join is a fraction of that).
    event_json: list = field(default_factory=list)
    tape_last: datetime | None = None
    tape_entries: int = 0


class GradeLog:
    """Freshness, first-per-letter latch, 5-minute tape and grade events."""

    def __init__(self, directory: Path, direction: str = "bull") -> None:
        # No I/O here: this is constructed at service import time.
        self.directory = Path(directory)
        # BEAR recorder (spec 2026-09-24): same machinery on the bearish
        # paints, outcomes scored in the trade's favour, under its own root.
        self._sets = sets_for(direction)
        self.direction: str = self._sets["direction"]
        self._lock = threading.Lock()
        self._symbols: dict[tuple[str, str], _Symbol] = {}
        self._days: dict[str, _Day] = {}
        self._today = ""
        self._pruned_on = ""
        # nightly(): the ET date it completed for, whether a run is in flight
        # (one warm thread per list calls it), and the no-retry-before time
        # after a failed run.
        self._nightly_done = ""
        self._catchup_done = ""  # pre-16:15 past-date pass found nothing left
        self._nightly_running = False
        self._nightly_retry_at: datetime | None = None
        # (symbol, event date) -> consecutive nightly runs that asked for bars
        # covering that date and got none back. In memory only -- see
        # MAX_FETCH_ATTEMPTS.
        self._fetch_attempts: dict[tuple[str, str], int] = {}

    # ------------------------------------------------------------------ public

    def apply(self, board: str, payload: dict, now: datetime) -> None:
        try:
            with self._lock:
                self._apply(str(board), payload, now)
        except Exception:  # noqa: BLE001 - a recorder bug never costs a scan
            return

    def status(self) -> dict:
        try:
            today = _today_et()
            with self._lock:
                days = [d for d in self._days.values() if d.day == today]
                last = [d.tape_last for d in days if d.tape_last is not None]
                return {
                    "tapeLastWriteAt": max(last).isoformat() if last else None,
                    "tapeEntriesToday": sum(d.tape_entries for d in days),
                    # Letter events only: pattern events are a separate study.
                    "eventsToday": sum(sum(map(_is_letter_event, d.events)) for d in days),
                }
        except Exception:  # noqa: BLE001
            return {"tapeLastWriteAt": None, "tapeEntriesToday": 0, "eventsToday": 0}

    def nightly(self, now: datetime,
                fetch_5m: Callable[..., Mapping[str, list]]) -> bool:
        """Score unscored events and rebuild the track record. True when it
        added at least one outcome or completed today's run. NEVER raises.

        ``fetch_5m(symbols, days=N)`` returns ``{symbol: [5m bar, ...]}``
        reaching back N calendar days.

        What it scores: letter and pattern events lacking ``outcome`` in event files
        dated within ``CATCHUP_MAX_DAYS`` -- past dates at any time (a missed
        evening is caught up the next day), today's only at/after 16:15. An
        event whose symbol came back without bars on that event's date is left
        without ``outcome`` and retried after ``NIGHTLY_RETRY_MINUTES``.

        Once-per-day guards: today's run is done when nothing it covered is
        left unscored; it then stamps ``builtFor`` = today in the record file
        (which carries "done" across a restart). Before 16:15 a catch-up pass
        that finds nothing left marks ``_catchup_done`` so later builds that
        morning skip the scan.

        Called after every board build by every warm thread, so the common
        path is a cheap no-op. The run itself (a bar fetch) happens OUTSIDE
        ``self._lock`` -- holding it would stall every scan's apply() for the
        length of a network call; ``_nightly_running`` keeps it single.
        """
        try:
            local = now.astimezone(_ET) if now.tzinfo else now.replace(tzinfo=_ET)
            today = local.date().isoformat()
            evening = local.time() >= NIGHTLY_AFTER
            with self._lock:
                if self._nightly_done == today or self._nightly_running:
                    return False
                if not evening and self._catchup_done == today:
                    return False
                if self._nightly_retry_at is not None and local < self._nightly_retry_at:
                    return False
                if evening and _map(_read_json(self.directory / RECORD_NAME)).get("builtFor") == today:
                    self._nightly_done = today
                    return False
                self._nightly_running = True
        except Exception:  # noqa: BLE001
            return False
        ran = complete = False
        try:
            ran, complete = self._nightly(local, evening, fetch_5m)
        except Exception:  # noqa: BLE001 - a nightly bug costs a night, never a scan
            ran = complete = False
        finally:
            with self._lock:
                self._nightly_running = False
                if complete:
                    self._nightly_retry_at = None
                    if evening:
                        self._nightly_done = today
                    else:
                        self._catchup_done = today
                else:
                    self._nightly_retry_at = local + timedelta(minutes=NIGHTLY_RETRY_MINUTES)
        return ran

    # ---------------------------------------------------------------- internal

    def _apply(self, board: str, payload: Any, now: datetime) -> None:
        if not isinstance(payload, dict):
            return
        local = now.astimezone(_ET) if now.tzinfo else now
        today = local.date().isoformat()
        self._today = today
        if self._pruned_on != today:
            self._pruned_on = today
            self._prune(local.date())
        day = self._day(board, today)

        rows: list[dict] = []
        for section in ("rows", "rest"):
            part = payload.get(section)
            if isinstance(part, list):
                rows.extend(r for r in part if isinstance(r, dict))

        seen: dict[str, dict] = {}
        new_events: list[dict] = []
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol:
                continue
            if symbol in seen:  # same symbol twice in one payload: one reading
                row["gradeFresh"] = seen[symbol]
                continue
            try:
                fresh = self._row(board, symbol, row, day, now, new_events)
            except Exception:  # noqa: BLE001 - one bad row costs its own freshness
                fresh = None
            row["gradeFresh"] = fresh
            seen[symbol] = fresh

        if new_events:
            day.events.extend(new_events)
            day.event_json.extend(_event_json(event) for event in new_events)
            self._write_events(board, day)

        # Only the boards the chart reads, only in extended hours: an
        # all-boards 24/7 tape was ~51 MB/day per board that nothing read.
        if board in TAPE_BOARDS and _in_tape_hours(now):
            due = (day.tape_last is None
                   or (now - day.tape_last).total_seconds() >= TAPE_EVERY_SECONDS)
            if due:
                self._write_tape(board, day, rows, now)

    def _write_events(self, board: str, day: _Day) -> None:
        head = json.dumps({"board": board, "date": day.day})[:-1]
        _atomic_write_text(
            _events_path(self.directory, board, day.day),
            head + ', "events": [' + ", ".join(day.event_json) + "]}",
        )

    def _day(self, board: str, today: str) -> _Day:
        day = self._days.get(board)
        if day is not None and day.day == today:
            return day
        day = _Day(today)
        # Reload today's latch so firstToday survives a worker restart.
        try:
            doc = json.loads(_events_path(self.directory, board, today).read_text(encoding="utf-8"))
            events = doc.get("events") if isinstance(doc, dict) else None
        except (OSError, ValueError, UnicodeDecodeError):
            events = None
        for event in events if isinstance(events, list) else []:
            letter_event = _is_letter_event(event)
            if not letter_event and not _is_pattern_event(event):
                continue
            symbol, at = event.get("symbol"), _parse_iso(event.get("at"))
            if not isinstance(symbol, str) or at is None:
                continue
            day.events.append(event)
            day.event_json.append(_event_json(event))
            first = {"at": event["at"], "price": _price(event.get("price"))}
            if letter_event:
                day.first.setdefault(symbol, {}).setdefault(event["letter"], {**first, "_dt": at})
            else:
                day.patterns.setdefault(symbol, {}).setdefault(event["pattern"], first)
        day.tape_entries, day.tape_last = _tape_summary(_tape_path(self.directory, board, today))
        self._days[board] = day
        return day

    def _row(self, board: str, symbol: str, row: dict, day: _Day, now: datetime,
             new_events: list) -> dict:
        state = self._symbols.get((board, symbol))
        if state is None:
            state = self._symbols[(board, symbol)] = _Symbol()
        if state.day != day.day:
            state.day = day.day
            state.timeline = []
            state.adx_logged = {}

        at_iso = now.isoformat()
        current = {
            "skit": _bgs(row, "skittles"),
            "rvol": _bgs(row, "rvol"),
            "sqz": _bgs(row, "sqz"),
            "news": _map(row.get("news")).get("headline"),
            "chart": _map(row.get("m5")).get("chart"),
            # No "adx" here on purpose: ADX is latched per BAR in
            # ``state.adx_logged``, not diffed against the previous scan.
        }
        prev = state.prev
        if prev is not None:
            items = []
            for tf, bg in current["skit"].items():
                before = prev["skit"].get(tf)
                if bg != before:
                    state.bg_changed[tf] = at_iso
                    if bg in self._sets["skit_bg"] and before not in self._sets["skit_bg"]:
                        items.append(("SKIT", f"SKIT {tf} bg {bg}"))
            for tf, bg in current["rvol"].items():
                if bg in self._sets["rvol_bg"] and prev["rvol"].get(tf) not in self._sets["rvol_bg"]:
                    items.append(("RVOL", f"RVOL {tf} {_cell(row, 'rvol', tf).get('value')}"))
            for tf, bg in current["sqz"].items():
                if bg == self._sets["sqz_fired"] and prev["sqz"].get(tf) != self._sets["sqz_fired"]:
                    items.append(("SQZ", f"SQZ {tf} released"))
            # LATCHED PER BAR, not edge-triggered like the kinds above.
            #
            # The last bar is still FORMING, and ``cross`` is recomputed on it
            # every scan: as the high / low / close move, +DI and -DI wobble
            # around each other and the cell goes bull -> None -> bull. An
            # edge test against the previous scan sees that None as the end of
            # the cross and the next bull as a NEW one, so one forming 5m bar
            # simulated over 15 scans wrote two identical "ADX 5m bull cross"
            # lines (2026-09-22). Latching on the BAR the reading belongs to
            # (columns.adx_cell's ``barAt``) writes it at most once per bar,
            # while a genuine cross on the NEXT bar still logs.
            #
            # barAt is None only on a tape with no usable time (or a row from
            # before the field existed); the latch then degrades to "once per
            # cross value", which is still never worse than the edge test.
            for tf in ADX_TFS:
                cell = _adx_tf(row, tf)
                cross = cell.get("cross")
                if cross not in ("bull", "bear"):
                    continue
                latch = (cell.get("barAt"), cross)
                if state.adx_logged.get(tf) == latch:
                    continue
                state.adx_logged[tf] = latch
                items.append(("ADX", _adx_timeline_text(tf, cross, cell)))
            if current["news"] and current["news"] != prev["news"]:
                items.append(("NEWS", "news"))
            if current["chart"] == "breakout_confirmed" and prev["chart"] != "breakout_confirmed":
                items.append(("M5", "5m breakdown confirmed" if self.direction == "bear"
                              else "5m breakout confirmed"))
            state.timeline.extend((now, kind, what) for kind, what in items)
            del state.timeline[:-TIMELINE_CAP]
        state.prev = current

        # Events and the firstToday latch: regular session only (see
        # _in_session). Freshness above works at any hour.
        session = _in_session(now)
        grade = _map(row.get("grade"))
        letter = grade.get("letter") if grade.get("letter") in LETTERS else None
        firsts = day.first.setdefault(symbol, {})
        if session and letter is not None and letter not in firsts:
            firsts[letter] = {"at": at_iso, "price": _price(row.get("last")), "_dt": now}
            new_events.append(
                self._event("letter", board, symbol, letter, row, grade, state, at_iso))

        # Pattern events: the first Steady / Explosive while Building, per
        # (symbol, pattern) per day, whatever the letter (NFLX 2026-09-21 had none).
        m5 = _map(row.get("m5"))
        pattern = m5.get("pattern")
        pattern_firsts = day.patterns.setdefault(symbol, {})
        if (session and pattern in PATTERNS and m5.get("state") == PATTERN_STATE
                and pattern not in pattern_firsts):
            pattern_firsts[pattern] = {"at": at_iso, "price": _price(row.get("last"))}
            new_events.append(
                self._event("pattern", board, symbol, letter, row, grade, state, at_iso))

        cutoff = now - timedelta(minutes=FRESH_MINUTES)
        kinds = {kind for at, kind, _ in state.timeline if at >= cutoff}
        age = None
        if session and letter is not None and letter in firsts:
            age = max(0, int((now - firsts[letter]["_dt"]).total_seconds() // 60))
        return {
            "icons": [kind for kind in ICON_ORDER if kind in kinds],
            "ageMinutes": age,
            "firstToday": {
                name: ({"at": firsts[name]["at"], "price": firsts[name]["price"]}
                       if name in firsts else None)
                for name in LETTERS
            } | {"patterns": {name: pattern_firsts.get(name) for name in PATTERNS}},
            "timeline": [{"at": at.isoformat(), "what": what}
                         for at, _, what in state.timeline[-PAYLOAD_TIMELINE_CAP:]],
        }

    @staticmethod
    def _event(kind: str, board: str, symbol: str, letter: str | None, row: dict,
               grade: Mapping, state: _Symbol, at_iso: str) -> dict:
        """One event record. ``kind`` "letter": the first time today at
        ``letter``; "pattern": the first time today at ``m5.pattern`` while
        Building, ``letter`` = the row's letter then (None when ungraded)."""
        checks = _map(grade.get("checks"))
        news = _map(row.get("news"))
        m5 = row.get("m5") if isinstance(row.get("m5"), dict) else None
        return {
            "board": board,
            "symbol": symbol,
            "kind": kind,
            "letter": letter,
            "at": at_iso,
            "price": _price(row.get("last")),
            "reasons": list(grade.get("reasons") or []),
            "checks": dict(checks),
            "push": {
                "rvol": {
                    tf: {
                        "value": _cell(row, "rvol", tf).get("value"),
                        "bg": _cell(row, "rvol", tf).get("bg"),
                        "barAt": _cell(row, "rvol", tf).get("barAt"),
                    }
                    for tf in PUSH_RVOL_TFS
                },
                "news": {
                    "headline": news.get("headline"),
                    "at": news.get("at"),
                    "ageHours": checks.get("newsAgeHours"),
                },
            },
            "sqzRaw": row.get("sqzRaw"),
            "skittles": {
                tf: {
                    "fg": _cell(row, "skittles", tf).get("fg"),
                    "bg": _cell(row, "skittles", tf).get("bg"),
                    "bgChangedAt": state.bg_changed.get(tf),
                }
                for tf in SKIT_TFS
            },
            "hlDegree": checks.get("hlDegree"),
            "lastCompleted5m": (m5 or {}).get("lastCompleted"),
            "m5": m5,
            "pctChange": row.get("pctChange"),
            "pattern": (m5 or {}).get("pattern"),
            "momentum": (m5 or {}).get("state"),
            # RECORDED FACTS, added 2026-09-22. Neither takes part in the
            # letter; both exist so build_record can later ask questions the
            # letter cannot answer - "does a +DI cross with rising ADX pay?"
            # and "does it pay in the Second 30m (Smart) window?".
            "adx": row.get("adx") if isinstance(row.get("adx"), dict) else None,
            "session": session_window(_parse_iso(at_iso)),
        }

    def _write_tape(self, board: str, day: _Day, rows: list[dict], now: datetime) -> None:
        at_iso = now.isoformat()
        entries: dict[str, dict] = {}
        for row in rows:
            symbol = row.get("symbol")
            if not isinstance(symbol, str) or not symbol or symbol in entries:
                continue
            grade = _map(row.get("grade"))
            m5 = _map(row.get("m5"))
            entries[symbol] = {
                "t": at_iso,
                "last": _price(row.get("last")),
                "letter": grade.get("letter") if grade.get("letter") in LETTERS else None,
                "checks": grade.get("checks") if isinstance(grade.get("checks"), dict) else None,
                "reasons": grade.get("reasons") if isinstance(grade.get("reasons"), list) else [],
                "m5state": m5.get("state"),
                "chart": m5.get("chart"),
                "trigger": _price(m5.get("trigger")),
                "pattern": m5.get("pattern"),
            }
        # A sample is due whether or not the write lands: a dead disk must not
        # turn every scan into a retry.
        day.tape_last = now
        if _append_line(_tape_path(self.directory, board, day.day),
                        {"t": at_iso, "board": board, "n": len(entries), "entries": entries}):
            day.tape_entries += len(entries)

    def _nightly(self, local: datetime, evening: bool, fetch_5m: Callable) -> tuple[bool, bool]:
        """One scoring pass. Returns (ran, complete): ``ran`` = an outcome was
        added or today's run was stamped; ``complete`` = nothing it covered
        is left unscored (and, in the evening, the record was written)."""
        today_d = local.date()
        today = today_d.isoformat()
        oldest = today_d - timedelta(days=CATCHUP_MAX_DAYS)

        def in_window(day: str) -> bool:
            try:
                d = date.fromisoformat(day)
            except ValueError:
                return False
            return oldest <= d < today_d or (evening and d == today_d)

        # 1. Which events still lack an outcome. In-memory days first:
        #    apply() rewrites a day's file from memory, so an outcome that is
        #    only on disk would be erased by the next event.
        with self._lock:
            memory = {_events_path(self.directory, board, day.day): day
                      for board, day in self._days.items() if in_window(day.day)}
            pending = {(e["symbol"], day.day) for day in memory.values()
                       for e in day.events if _needs_outcome(e)}
        dates: set[str] = set()
        for folder in _board_folders(self.directory / EVENTS_DIRNAME):
            try:
                dates.update(p.name[:10] for p in folder.glob("*.json") if in_window(p.name[:10]))
            except OSError:
                continue
        disk: dict[Path, dict] = {}
        for day in sorted(dates):
            for path in _day_files(self.directory, day):
                if path in memory:
                    continue
                doc = _read_json(path)
                if isinstance(doc, dict) and isinstance(doc.get("events"), list):
                    disk[path] = doc
                    pending.update((e["symbol"], day) for e in doc["events"] if _needs_outcome(e))

        # 2. Bars, outside the lock, deep enough to reach the oldest pending
        #    date. A symbol is scorable for a date only when its bars include
        #    a session bar on that date; anything else stays pending.
        covered: dict[str, set] = {}
        bars: Mapping = {}
        if pending:
            symbols = sorted({symbol for symbol, _ in pending})
            depth = (today_d - min(date.fromisoformat(d) for _, d in pending)).days
            bars = _map(fetch_5m(symbols, days=max(1, min(CATCHUP_MAX_DAYS, depth))))
            for symbol in symbols:
                series = bars.get(symbol)
                covered[symbol] = _session_dates(series) if isinstance(series, list) else set()

        # 2b. Count this run's misses per (symbol, date). A pair still missing
        # after MAX_FETCH_ATTEMPTS consecutive runs is given up on -- see
        # MAX_FETCH_ATTEMPTS. A pair that comes back covered has its counter
        # dropped (a transient miss must not count toward a later, unrelated
        # run of misses).
        exhausted: set[tuple[str, str]] = set()
        for symbol, day in pending:
            if day in covered.get(symbol, ()):
                self._fetch_attempts.pop((symbol, day), None)
                continue
            count = self._fetch_attempts.get((symbol, day), 0) + 1
            self._fetch_attempts[(symbol, day)] = count
            if count >= MAX_FETCH_ATTEMPTS:
                exhausted.add((symbol, day))

        added = 0
        missing = False

        def fill(events: Iterable, day: str) -> bool:
            nonlocal added, missing
            changed = False
            for event in events:
                if not _needs_outcome(event):
                    continue
                symbol = event["symbol"]
                if (symbol, day) in exhausted:
                    # Given up: never a numeric outcome, never counted in the
                    # track record's stats, never retried again (_needs_outcome
                    # is false once "outcome" is set).
                    event["outcome"] = {"unavailable": True}
                    added += 1
                    changed = True
                    continue
                if day not in covered.get(symbol, ()):
                    missing = True  # no bars for that date: leave it for a retry
                    continue
                event["outcome"] = outcome_for(event, bars[symbol], self.direction)
                added += 1
                changed = True
            return changed

        # 3. Write back. Memory under the lock (apply() shares these lists).
        with self._lock:
            for board, day in list(self._days.items()):
                if in_window(day.day) and fill(day.events, day.day):
                    day.event_json = [_event_json(e) for e in day.events]
                    self._write_events(board, day)
            # A board apply() loaded from disk since step 1 is in memory now
            # and was handled above.
            loaded = {_events_path(self.directory, b, d.day)
                      for b, d in self._days.items() if in_window(d.day)}
        for path, doc in disk.items():
            day = path.name[:10]
            if path not in loaded and fill(doc["events"], day):
                head = json.dumps({"board": doc.get("board"), "date": day})[:-1]
                _atomic_write_text(path, head + ', "events": ['
                                   + ", ".join(_event_json(e) for e in doc["events"]) + "]}")

        # 4. The track record, over every event file still on disk: rebuilt
        #    whenever an outcome was added, and stamped builtFor = today only
        #    when today's evening run left nothing unscored.
        stamp = evening and not missing
        if not added and not stamp:
            return False, not missing
        previous = _map(_read_json(self.directory / RECORD_NAME)).get("builtFor")
        record = build_record(self.directory, self.direction)
        record["builtFor"] = today if stamp else previous
        record["builtAt"] = datetime.now(_ET).isoformat(timespec="seconds")
        written = _atomic_write_json(self.directory / RECORD_NAME, record)
        return (added > 0 or (stamp and written)), (not missing and written)

    def _prune(self, today: date) -> None:
        for dirname in (TAPE_DIRNAME, EVENTS_DIRNAME):
            root = self.directory / dirname
            try:
                boards = [p for p in root.iterdir() if p.is_dir()]
            except OSError:
                continue
            for folder in boards:
                try:
                    files = list(folder.iterdir())
                except OSError:
                    continue
                for path in files:
                    try:
                        stamped = date.fromisoformat(path.name[:10])
                    except ValueError:
                        continue
                    if (today - stamped).days > RETENTION_DAYS:
                        try:
                            path.unlink()
                        except OSError:
                            pass


# ------------------------------------------------------ outcomes & track record

def _read_json(path: Path) -> Any:
    """A JSON document, or None when missing / unreadable / torn."""
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError):
        return None


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if value == value else None  # NaN is not a number here


def _event_key(event: Mapping) -> tuple:
    return (event.get("symbol"), event.get("letter"), event.get("at"))


def _is_pattern_event(event: Any) -> bool:
    return (isinstance(event, dict) and event.get("kind") == "pattern"
            and event.get("pattern") in PATTERNS)


def _is_letter_event(event: Any) -> bool:
    # A file written before "kind" existed holds letter events only.
    return (isinstance(event, dict) and event.get("kind", "letter") == "letter"
            and event.get("letter") in LETTERS)


def _needs_outcome(event: Any) -> bool:
    return (isinstance(event, dict) and "outcome" not in event
            and isinstance(event.get("symbol"), str)
            and (_is_letter_event(event) or _is_pattern_event(event)))


def _board_folders(root: Path) -> list[Path]:
    """Board directories under ``root``, Watchlist then Mag7 then the rest."""
    try:
        folders = [p for p in Path(root).iterdir() if p.is_dir()]
    except OSError:
        return []
    order = {name: i for i, name in enumerate(TAPE_BOARDS)}
    return sorted(folders, key=lambda p: (order.get(p.name, len(order)), p.name))


def _day_files(directory: Path, day: str) -> list[Path]:
    """Every board's event file for ``day`` that exists."""
    folders = _board_folders(Path(directory) / EVENTS_DIRNAME)
    return [f / f"{day}.json" for f in folders if (f / f"{day}.json").is_file()]


def _bar_start(bar: Mapping) -> datetime | None:
    seconds = _num(bar.get("time"))
    if seconds is None:
        return None
    if seconds > 1e12:  # milliseconds
        seconds /= 1000.0
    return datetime.fromtimestamp(seconds, _ET)


def _session_dates(bars: Iterable) -> set[str]:
    """ET dates on which ``bars`` hold at least one bar starting at or before
    15:55 -- the dates those bars can score an event for."""
    dates: set[str] = set()
    for bar in bars:
        if not isinstance(bar, Mapping) or _num(bar.get("close")) is None:
            continue
        start = _bar_start(bar)
        if start is not None and start.time() <= CLOSE_BAR:
            dates.add(start.date().isoformat())
    return dates


def outcome_for(event: Mapping, bars: Iterable[Mapping], direction: str = "bull") -> dict:
    """The event's outcome from 5-minute bars (see the module docstring).

    Every value is a percent move from the event price, rounded to 3 places,
    or None when the session gives nothing to measure it with.

    ``direction="bear"`` scores IN THE TRADE'S FAVOUR (spec 2026-09-24): every
    percent is negated so ``pctHigher*`` keeps meaning "went your way",
    ``maxFav`` is the LOWEST low and ``maxAdv`` the HIGHEST high.
    """
    out = _outcome_bull(event, bars)
    if str(direction or "").strip().lower() != "bear":
        return out
    flipped: dict[str, float | None] = dict.fromkeys(OUTCOME_KEYS)
    for key in ("p5", "p15", "p30", "p60", "close"):
        flipped[key] = None if out[key] is None else round(-out[key], 3)
    flipped["maxFav"] = None if out["maxAdv"] is None else round(-out["maxAdv"], 3)
    flipped["maxAdv"] = None if out["maxFav"] is None else round(-out["maxFav"], 3)
    return flipped


def _outcome_bull(event: Mapping, bars: Iterable[Mapping]) -> dict:
    out: dict[str, float | None] = dict.fromkeys(OUTCOME_KEYS)
    at = _parse_iso(event.get("at"))
    price = _num(event.get("price"))
    if at is None or not price or price <= 0:
        return out
    at = at.astimezone(_ET) if at.tzinfo else at.replace(tzinfo=_ET)
    last_start = datetime.combine(at.date(), CLOSE_BAR, _ET)
    if at >= last_start + timedelta(minutes=5):
        return out  # after 16:00: no session left to score

    session: list[tuple[datetime, Mapping]] = []
    for bar in bars:
        if not isinstance(bar, Mapping) or _num(bar.get("close")) is None:
            continue
        start = _bar_start(bar)
        if start is not None and start.date() == at.date() and start <= last_start:
            session.append((start, bar))
    session.sort(key=lambda item: item[0])
    if not session:
        return out

    def pct(value: float) -> float:
        return round((value - price) / price * 100.0, 3)

    bar_len = timedelta(minutes=5)
    data_end = session[-1][0] + bar_len  # end of the newest session bar held
    for minutes in OUTCOME_MINUTES:
        target = at + timedelta(minutes=minutes)
        # The last bar ending by the target, among bars ending after the event.
        # The data must reach the target's own 5-minute boundary: a bar list
        # that stops early has no "pN yet" (a gap INSIDE the data just means
        # no trades, so the previous bar's close stands).
        boundary = target - timedelta(minutes=target.minute % 5, seconds=target.second,
                                      microseconds=target.microsecond)
        hit = None
        if data_end >= boundary:
            for start, bar in session:
                end = start + bar_len
                if end > target:
                    break
                if end > at:
                    hit = bar
        out[f"p{minutes}"] = pct(_num(hit["close"])) if hit is not None else None

    after = [(start, bar) for start, bar in session if start >= at]
    out["close"] = pct(_num(session[-1][1]["close"]))
    highs = [h for h in (_num(bar.get("high")) for _, bar in after) if h is not None]
    lows = [low for low in (_num(bar.get("low")) for _, bar in after) if low is not None]
    out["maxFav"] = pct(max(highs)) if highs else None
    out["maxAdv"] = pct(min(lows)) if lows else None
    return out


def _is_fresh(event: Mapping) -> bool:
    """Any SKIT timeframe's bg changed within FRESH_MINUTES before the event."""
    at = _parse_iso(event.get("at"))
    if at is None:
        return False
    for cell in _map(event.get("skittles")).values():
        changed = _parse_iso(_map(cell).get("bgChangedAt"))
        try:
            if changed is not None and timedelta(0) <= at - changed <= timedelta(minutes=FRESH_MINUTES):
                return True
        except TypeError:  # naive vs aware: not comparable, not fresh
            continue
    return False


def _adx_has_cross(event: Mapping, kind: str, rising: bool = False) -> bool:
    """Did the event's ADX snapshot show a ``kind`` DI cross on EITHER tape?

    Either 5m or 30m counts: the cross is one thing happening at two
    resolutions, and requiring both would mean recording almost nothing.
    ``rising=True`` additionally demands ADX itself be rising on the SAME
    timeframe as the cross - a cross on 5m with a rising 30m ADX is a
    different claim, and mixing them would make the bucket meaningless.
    """
    adx = _map(event.get("adx"))
    for tf in ADX_TFS:
        cell = _map(adx.get(tf))
        if cell.get("cross") != kind:
            continue
        if not rising or cell.get("rising") is True:
            return True
    return False


def _stats(outcomes: list[Mapping]) -> dict:
    """Stable keys always; None where there is nothing to average."""
    def values(key: str) -> list[float]:
        return [v for v in (_num(o.get(key)) for o in outcomes) if v is not None]

    def higher(key: str) -> float | None:
        vals = values(key)
        return round(100.0 * sum(v > 0 for v in vals) / len(vals), 1) if vals else None

    def mean(key: str) -> float | None:
        vals = values(key)
        return round(sum(vals) / len(vals), 3) if vals else None

    closes = values("close")
    return {
        "count": len(outcomes),
        "pctHigher15": higher("p15"),
        "pctHigher60": higher("p60"),
        "pctHigherClose": higher("close"),
        "avgToClose": mean("close"),
        "medianToClose": round(statistics.median(closes), 3) if closes else None,
        "avgMaxFav": mean("maxFav"),
        "avgMaxAdv": mean("maxAdv"),
    }


def build_record(directory: Path, direction: str = "bull") -> dict:
    """The track record over every scored event on disk (no builtFor stamp).

    ``direction="bear"`` only changes the ADX split (a -DI cross is the bear
    question) and stamps ``direction``; the events under a bear root were
    already scored in the trade's favour by outcome_for.

    An event whose symbol never came back with a bar on its date after
    MAX_FETCH_ATTEMPTS nightly runs carries ``outcome={"unavailable": True}``
    (see GradeLog._nightly). It is excluded from every stats block the same
    way an unscored event is, and counted separately per letter in
    ``unscored`` (pattern events: ``patternUnscored``) so the track record
    can say how much of the picture it is missing rather than silently going
    quiet about it.
    """
    counted: dict[tuple, dict] = {}
    unscored: dict[tuple, dict] = {}
    for folder in _board_folders(Path(directory) / EVENTS_DIRNAME):
        try:
            files = sorted(folder.glob("*.json"))
        except OSError:
            continue
        for path in files:
            day = path.name[:10]
            try:
                date.fromisoformat(day)
            except ValueError:
                continue
            events = _map(_read_json(path)).get("events")
            for event in events if isinstance(events, list) else []:
                if _is_letter_event(event):
                    key = (day, event.get("symbol"), "letter", event["letter"])
                elif _is_pattern_event(event):
                    key = (day, event.get("symbol"), "pattern", event["pattern"])
                else:
                    continue
                outcome = _map(event.get("outcome"))
                if outcome.get("unavailable") is True:
                    unscored.setdefault(key, event)
                    continue
                if _num(outcome.get("close")) is None:
                    continue  # unscored, or nothing to score (after the close)
                counted.setdefault(key, event)

    def bucket(event: Mapping) -> str:  # a pattern event's letter bucket
        return event.get("letter") if event.get("letter") in LETTERS else "none"

    by_letter: dict[str, list] = {letter: [] for letter in LETTERS}
    by_pattern_event = {p: {b: [] for b in PATTERN_LETTERS} for p in PATTERNS}
    #: The same pattern events as above, kept whole rather than reduced to
    #: their outcome, so bySession can group them too.
    pattern_full: dict[str, list] = {p: [] for p in PATTERNS}
    for (_, _, kind, name), event in counted.items():
        if kind == "letter":
            by_letter[name].append(event)
        else:
            by_pattern_event[name][bucket(event)].append(event["outcome"])
            pattern_full[name].append(event)
    unscored_by_letter = {letter: 0 for letter in LETTERS}
    unscored_patterns = {p: dict.fromkeys(PATTERN_LETTERS, 0) for p in PATTERNS}
    for (_, _, kind, name), event in unscored.items():
        if kind == "letter":
            unscored_by_letter[name] += 1
        else:
            unscored_patterns[name][bucket(event)] += 1

    def split(events: list, key: Callable[[Mapping], str]) -> dict:
        groups: dict[str, list] = {}
        for event in events:
            groups.setdefault(key(event), []).append(event["outcome"])
        return {name: _stats(outs) for name, outs in sorted(groups.items())}

    def session_of(event: Mapping) -> str:
        return str(event.get("session") or "none")

    # byAdx spans BOTH event kinds on purpose. The question it exists to answer
    # ("is a +DI cross with rising ADX worth anything?") is about the market,
    # not about the letter, and splitting it per letter would leave every
    # bucket too small to read. "bullCrossRising" is a SUBSET of "bullCross",
    # not a fourth disjoint bucket; "none" is everything with no bull cross.
    # Events recorded before this field existed carry no adx and land in
    # "none" - that is why the recorded days matter more than the count.
    adx_events = [e for evs in by_letter.values() for e in evs]
    adx_events += [e for evs in pattern_full.values() for e in evs]
    kind = "bear" if str(direction or "").strip().lower() == "bear" else "bull"
    crossed = [e for e in adx_events if _adx_has_cross(e, kind)]
    return {
        "source": "recorded",
        "direction": kind,
        "days": sorted({key[0] for key in counted if key[2] == "letter"}),
        "patternDays": sorted({key[0] for key in counted if key[2] == "pattern"}),
        "letters": {L: _stats([e["outcome"] for e in evs]) for L, evs in by_letter.items()},
        "byMomentum": {L: split(evs, lambda e: str(e.get("momentum") or "none"))
                       for L, evs in by_letter.items()},
        "byPattern": {L: split(evs, lambda e: str(e.get("pattern") or "none"))
                      for L, evs in by_letter.items()},
        "byFresh": {L: {"fresh": _stats([e["outcome"] for e in evs if _is_fresh(e)]),
                        "notFresh": _stats([e["outcome"] for e in evs if not _is_fresh(e)])}
                    for L, evs in by_letter.items()},
        "unscored": unscored_by_letter,
        "patternEvents": {p: {b: _stats(outs) for b, outs in buckets.items()}
                          for p, buckets in by_pattern_event.items()},
        "patternUnscored": unscored_patterns,
        # --- recorded facts, added 2026-09-22 (the letter rule is unchanged) ---
        "bySession": {L: split(evs, session_of) for L, evs in by_letter.items()},
        "patternBySession": {p: split(evs, session_of)
                             for p, evs in pattern_full.items()},
        "byAdx": {
            f"{kind}Cross": _stats([e["outcome"] for e in crossed]),
            f"{kind}CrossRising": _stats(
                [e["outcome"] for e in crossed if _adx_has_cross(e, kind, rising=True)]
            ),
            "none": _stats([e["outcome"] for e in adx_events
                            if not _adx_has_cross(e, kind)]),
        },
    }


def record_response(directory: Path, direction: str = "bull") -> dict:
    """GET /api/momx-scanner/grade-record (``?dir=bear`` reads the bear root).
    Every answer carries ``direction`` so the screen can say "closed lower".

    Letters and pattern events are reported from independent sources, each
    named so the scanner can show where its numbers came from:

    - ``source`` / ``letters`` (plus ``byMomentum``, ``byFresh``, ``byPattern``,
      ``unscored``): the recorded track record once it has at least one day
      with a scored LETTER event; until then the History back-test, labelled
      as such; else ``source: None`` (the scanner shows "track record
      unavailable"). A day with only pattern events (e.g. the first day after
      this feature ships) does NOT count as a recorded day here -- it would
      otherwise show all-None letter stats instead of the back-test's real
      numbers.
    - ``patternSource`` / ``patternEvents`` / ``patternUnscored`` /
      ``patternDays``: always taken from the recorded file when it has at
      least one scored pattern event, regardless of which source the letters
      came from (the back-test has no pattern data). ``patternSource`` is
      ``"recorded"`` when present, else ``None``.

    The back-test's per-event list is left out -- the scanner needs the
    letters, not ~850 rows per poll.
    """
    kind = "bear" if str(direction or "").strip().lower() == "bear" else "bull"
    result = _record_response(directory)
    result["direction"] = kind
    return result


def _record_response(directory: Path) -> dict:
    record = _read_json(Path(directory) / RECORD_NAME)
    record = record if isinstance(record, dict) else {}
    pattern_days = record.get("patternDays")
    has_patterns = isinstance(pattern_days, list) and bool(pattern_days)
    pattern_extra = {
        "patternSource": "recorded",
        "patternDays": pattern_days,
        "patternEvents": record.get("patternEvents"),
        "patternUnscored": record.get("patternUnscored"),
    } if has_patterns else {}

    if isinstance(record.get("days"), list) and record["days"]:
        result = dict(record)
        result["patternSource"] = "recorded" if has_patterns else None
        return result

    backtest = _read_json(Path(directory) / BACKTEST_NAME)
    if isinstance(backtest, dict) and isinstance(backtest.get("letters"), dict):
        days = backtest.get("days")
        count = len(days) if isinstance(days, list) else (days if isinstance(days, int) else 0)
        result = {
            "source": f"back-test from History archive ({count} days)",
            "days": days if isinstance(days, list) else [],
            "letters": backtest["letters"],
            "patternSource": "recorded" if has_patterns else None,
        }
        result.update(pattern_extra)
        return result

    if has_patterns:
        return {"source": None, **pattern_extra}
    return {"source": None, "patternSource": None}


def _tape_symbol_entries(path: Path, symbol: str) -> list[dict]:
    """One symbol's entries from a tape file WITHOUT json-parsing every line.

    A day's tape is ~33 MB at the close; parsing all of it (read_tape) for
    each chart open over 5 days x 2 boards would cost seconds. Every entry is
    written as ``"SYM":{"t":...}`` (compact separators), so find that needle
    and decode just that object. A torn line fails to decode and is skipped;
    a decode that runs past its own line is rejected.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    key = json.dumps(symbol)
    needle = key + ':{"t":'
    decoder = json.JSONDecoder()
    found: list[dict] = []
    pos = 0
    while True:
        index = text.find(needle, pos)
        if index < 0:
            break
        start = index + len(key) + 1
        line_end = text.find("\n", start)
        if line_end < 0:
            line_end = len(text)
        try:
            entry, end = decoder.raw_decode(text, start)
        except ValueError:
            entry, end = None, line_end + 1
        if isinstance(entry, dict) and end <= line_end:
            found.append(entry)
        pos = line_end  # a symbol appears once per sample line
    return found


#: (path, symbol) -> ((mtime_ns, size), entries) for PAST-day tape files.
#: A past day's file no longer changes, so repeated chart polls (every panel,
#: every 60 s) would otherwise re-read up to ~50 MB per date per request.
_TAPE_CACHE: "OrderedDict[tuple[str, str], tuple[tuple[int, int], list]]" = OrderedDict()
_TAPE_CACHE_LOCK = threading.Lock()


def _cached_symbol_entries(path: Path, symbol: str) -> list[dict]:
    """:func:`_tape_symbol_entries` for a past day, cached on (path, mtime, size).

    A changed file (a late append, a rewrite) has a new mtime or size and is
    read again. LRU-bounded at ``TAPE_CACHE_SIZE`` entries. Callers must not
    mutate the returned list or its entries (tape_response copies fields out).
    """
    try:
        stat = path.stat()
    except OSError:
        return []
    key = (os.fspath(path), symbol)
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _TAPE_CACHE_LOCK:
        hit = _TAPE_CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            _TAPE_CACHE.move_to_end(key)
            return hit[1]
    found = _tape_symbol_entries(path, symbol)
    with _TAPE_CACHE_LOCK:
        _TAPE_CACHE[key] = (stamp, found)
        _TAPE_CACHE.move_to_end(key)
        while len(_TAPE_CACHE) > max(1, int(TAPE_CACHE_SIZE)):
            _TAPE_CACHE.popitem(last=False)
    return found


def tape_response(directory: Path, symbol: str, days: int = 5,
                  today: str | None = None) -> dict:
    """GET /api/momx-scanner/grade-tape: one symbol's grade tape for the chart.

    Walks back from ``today`` (ET; default the wall clock) one calendar date
    at a time, SKIPPING dates with no tape file on either board (weekends,
    holidays), until ``days`` dates that have a file are collected -- looking
    back at most ``TAPE_LOOKBACK_DAYS`` calendar days before today. Precedence
    is per date: a date on which Watchlist sampled the symbol returns
    Watchlist's entries only; Mag7's are used only for dates Watchlist has
    none. (Each board stamps its own scan time, so a per-sample ``t`` match
    would never fire and the two boards' samples would interleave.) Entries
    in time order.

    Today's file is still growing and is scanned on every call; a past day's
    result is cached per (file, symbol) on (mtime, size).
    """
    symbol = str(symbol or "").strip().upper()
    entries: list[dict] = []
    if not symbol:
        return {"symbol": symbol, "entries": entries}
    today_d = date.fromisoformat(today or _today_et())
    wanted = max(1, int(days))
    collected: list[list[dict]] = []
    for back in range(TAPE_LOOKBACK_DAYS + 1):
        if len(collected) >= wanted:
            break
        day = (today_d - timedelta(days=back)).isoformat()
        paths = [(board, _tape_path(directory, board, day)) for board in TAPE_BOARDS]
        present = [(board, path) for board, path in paths if path.is_file()]
        if not present:
            continue
        day_entries: list[dict] = []
        for board, path in present:  # Watchlist first; the first board with entries wins
            raw = (_tape_symbol_entries(path, symbol) if back == 0
                   else _cached_symbol_entries(path, symbol))
            if not raw:
                continue
            for entry in raw:
                day_entries.append({
                    "t": entry.get("t"),
                    "letter": entry.get("letter"),
                    "reasons": entry.get("reasons") or [],
                    "m5state": entry.get("m5state"),
                    "chart": entry.get("chart"),
                    "trigger": entry.get("trigger"),
                    "pattern": entry.get("pattern"),
                    "last": entry.get("last"),
                    "board": board,
                })
            break
        day_entries.sort(key=lambda e: str(e["t"]))
        collected.append(day_entries)
    for day_entries in reversed(collected):
        entries.extend(day_entries)
    return {"symbol": symbol, "entries": entries}
