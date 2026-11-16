"""Scanner history answers for the Ask chat (2026-11-11).

His "instead of going and search in scanner history, chatbot will help and analysis for us" and
"user can use the chatbot ask like 'from the scanner NVDA at 9:30 give me the list of tickers'".

Three kinds of question, read from the saved boards in artifacts/momx_history (momx/history.py):
  scanner_list    who was on a board at a time, ranked like his board by H/L
  scanner_ticker  what one ticker showed at a time
  scanner_top     who stayed in the top through the day
  scanner_day     one ticker's whole day: a short line per board, and a reference the screen uses to draw the real rows

The history keeps only scan matches and only when a cell changes, so there is no full board to look
up. A ticker's state at a time is its latest saved row at or before that time, and it counts as on
the board only if that row is within FRESH_SECONDS and the ticker had not left for the day. Carrying
an old row forward instead is wrong: a ticker whose last match was 9:15 would sit in the top all day.

A row is saved only when a trigger cell changes (H/L is not one), and history stops saving a ticker
at 120 rows a day, while lastSeenAt keeps advancing. lastSeenAt is only the last time it matched: it
says nothing about any moment before that. So with no fresh row at a time, between the ticker's first
row and its lastSeenAt, an answer says it is NOT KNOWN whether the ticker was on the board. It never
says "still on" and never says "not on", unless a fresh row on the other side (calls vs puts) shows it.

Only reads. A Watchlist day file is 100-180 MB and api_server is short of CPU, so a file is boiled
down to a small per-ticker index in a short-lived child process (momx/chat_scanner_index.py), one at
a time and below normal priority; only the index comes back and only that is kept. One background
builder runs those children, the owner's first, each to its own time limit; a question only waits
for it, so a question that runs out of time says the board is still loading and the next one finds it.

TOOLS is what the Ask chat's registry (momx/chat_tools.py) offers: every handler takes (args, ctx), re-checks
the session's scope and never raises. top_now reads the newest finished board without starting a build.
"""
from __future__ import annotations

import heapq
import itertools
import json
import os
import re
import subprocess
import sys
import threading
import time
from bisect import bisect_right
from collections import OrderedDict
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Mapping, NamedTuple
from zoneinfo import ZoneInfo

from momx import chat_limits
from momx.history import HISTORY_DIRNAME, RETENTION_DAYS, board_dir
from momx.option_track import TOP_N, hl_ranking

ET = ZoneInfo("America/New_York")
#: A saved row speaks for the board for this long after it was saved.
FRESH_SECONDS = 600
#: lastSeenAt advances only when the file is written, so a ticker is given this much past it.
SEEN_GRACE_SECONDS = 60
#: Today's file changes all day: its index is built again at most this often.
TODAY_RELOAD_SECONDS = 300
#: Calls and puts of the two default boards over two days is 8 files.
CACHE_FILES = 12
#: What regular users' reads keep in memory: its own small store, so they can never push out the owner's.
REGULAR_CACHE_FILES = 6
#: The child building an index gets this long; it waits this long for its turn; a question spends this long in all.
CHILD_TIMEOUT_SECONDS = 120
CHILD_WAIT_SECONDS = 130
QUESTION_SECONDS = 240
#: A regular user comes through the tunnel, and Cloudflare drops a request at 100 s. The build carries on after that.
REGULAR_QUESTION_SECONDS = 85
#: After a child runs out of time the PC is busy: no new child for this long.
CHILD_REST_SECONDS = 60
#: One regular question reads at most this many boards.
REGULAR_BOARDS = 2
#: Regular users' reads waiting in line, across the whole app. Past this, the next one is told the app is busy.
REGULAR_QUEUE = 2
#: A file this big is what costs a minute of the PC (Watchlist, Liquid Movers), so it counts against the account's reads.
HEAVY_READ_BYTES = 16 * 1024 * 1024
SHOW_TOP = 10
TOP_NAMES = 8
HOLD_MINUTES = 5
DEFAULT_TOP = 7
OPEN_MIN, CLOSE_MIN, SESSION_END_MIN = 9 * 60 + 30, 15 * 60 + 59, 16 * 60
DEFAULT_BOARDS = ("Mag7", "Watchlist")
#: Liquid Movers before Movers: the longer name is read first.
BOARDS = (("Mag7", r"\bmag\s?(?:7|seven)\b"), ("Watchlist", r"\bwatch\s?list\b"), ("Liquid Movers", r"\bliquid(?:\s+movers?)?\b"),
          ("Movers", r"(?<!liquid )\bmovers?\b"), ("21 EMA Touch Weekly", r"\b21\s*ema(?:\s+touch)?(?:\s+weekly)?\b"),
          ("50 EMA Touch Monthly", r"\b50\s*ema(?:\s+touch)?(?:\s+monthly)?\b"))
BOARD_NAMES = tuple(name for name, _ in BOARDS)
SOURCE = "From the saved scanner history: a ticker's latest saved row within 10 minutes, so times are approximate."
DAY_SOURCE = "From the saved scanner history. A scan is each time it matched; a row is saved only when a column changes, so saved rows are not every scan."
BUSY_LINE = "Another question is reading the scanner history right now. Please try again in about a minute."
#: The whole-day answer never waits for a big file: it gives a read this long, then answers with what it has.
DAY_WAIT_SECONDS = 3.0
#: The premarket scanner starts at 06:00 ET.
FIRST_SCAN_MIN = 6 * 60
#: A day with no file yet ("today" before the first scan) falls back at most this many days.
DAY_FALLBACK_DAYS = 7
#: A finished board older than this is not "now".
BOARD_MAX_AGE_SECONDS = 4 * 86400
SCANNER_VIEW = "MomX Scanner"
MAX_LINKS = 6
MAX_TABLE_ROWS = 40

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_SCANNER = re.compile(r"\b(?:scan(?:ner|ners|s|ned)?|boards?)\b")
_COLUMN = re.compile(r"\b(?:rvol|sqz|squeeze|skittles|h/l)\b")
_LIST = re.compile(r"\b(?:list|tickers|names|symbols|stocks)\b")
_HOLD = re.compile(r"\b(?:stay\w*|repeat\w*|held|hold\w*|longest|how\s+long|most\s+time|kept|remain\w*)\b")
_TOP_WORD = re.compile(r"\btop\b")
#: Words of a question about the bot's trades. Unless it says "scanner", such a question is not a scanner question.
BOT_WORDS = re.compile(r"\b(?:bots?|trades?|traded|trading|signals?|wins?|winn\w+|won|profit\w*|loss\w*|lost|p\s?[&/]\s?l|pnl|paper|scorecard"
                       r"|accounts?|positions?|fills?|filled|orders?)\b")
#: What the bot did to a ticker. Unless the sentence says "scanner" or names a board, a list of board tickers answers a different question.
#: "Holding" counts only away from "top": "which tickers kept holding the top" is about the board.
TRADE_WORDS = re.compile(r"\b(?:take|took|taken|taking|buy|buying|bought|sold|enter\w*|entries|entry|picked)\b|\bholding\b(?!.*\btop\b)")
#: The numbers are never the two halves of a clock time: "10:30 setups" has no setup count, "top 10:30" no top-N.
_TOP_N = re.compile(r"\btop\s*(\d{1,2}|" + "|".join(_NUMBER_WORDS) + r")\b(?!:\d)|\b(?:first|best)\s+(\d{1,2})\b(?!:\d)|(?<![\d:])\b(\d{1,2})\s+(?:tickers|names|stocks|symbols)\b")
_LEVEL = re.compile(r"\blevel\s*([0-3])\b(\s*\+|\s+or\s+(?:better|higher|more|above))?")
_SETUP = re.compile(r"\bsetups?\s*(\d{1,2})\b(?!:\d)|(?<![\d:])\b(\d{1,2})\+?\s*setups?\b")
_HOW_LONG = re.compile(r"\bhow\s+long\b")
_POINTER = re.compile(r"\b(?:it|its|this\s+one|that\s+one|this\s+ticker|that\s+ticker|same\s+one|where)\b")
#: Words of a scanner question that are never its ticker, unless typed in capitals or as $WORD.
_NOT_TICKERS = set("""from give list lists ticker tickers name names symbol symbols stock stocks scan scans scanner scanners scanned board boards
    history top level levels setup setups stayed stay stays repeated repeat repeats held hold holds longest long minutes minute latest then
    first last were was had have has who which what where when show showed shown rank ranked bear bearish bull bullish side open opening close
    closing morning today yesterday monday tuesday wednesday thursday friday january february march april june july august september october
    november december jan feb mar apr jun jul aug sep sept oct nov dec most time kept remain remained best on at in the of for me""".split())
#: Column names: never a ticker however they are typed.
_JARGON = {"rvol", "sqz", "skittles", "ema", "hl"}


# ---------------------------------------------------------------- reading the question
@dataclass(frozen=True)
class Asked:
    boards: tuple
    side: str
    side_named: bool
    minute: int | None
    top: int | None
    level: int | None
    level_up: bool
    setups: int | None


#: An hour after "at": "at 11". Never the start of "at 9 30", "at 10 oclock" or a clock time.
_AT_HOUR = re.compile(r"(?<![\d:])\bat\s+(\d{1,2})\b(?!\s*[:/-]\d)(?!\s+[0-5]\d\b)(?!\s*o\s*['\u2019]?\s*clock)")
_AT_COMPACT = re.compile(r"(?<![\d:])\bat\s+(0?[1-9]|1[0-2])([0-5]\d)\b(?![\d:/%.-])")
_COMPACT = re.compile(r"(?<![\d:$.,/-])\b(0?[6-9]|1[0-2])([0-5]\d)\b(?![\d:/%.-])")
_SPACED = re.compile(r"(?<![\d:$.,/-])\b(\d{1,2})\s+([0-5]\d)\b(?![\d:/%.-])")
_OCLOCK = re.compile(r"\b(\d{1,2})\s*o\s*['\u2019]?\s*clock\b")
_MONTH_DAY = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d")
#: A number after one of these, or before a word of counting, is a count or a price and not a clock time.
_NOT_A_TIME_BEFORE = re.compile(r"\b(?:above|over|under|below|top|first|best|level|setups?|strike|price)\s*$")
_NOT_A_TIME_AFTER = re.compile(r"\s*(?:tickers?|names|stocks|symbols|setups?|strikes?|calls?|puts?|shares?|dollars?|k\b)")
_AT_OPEN = re.compile(r"\b(?:at|the)\s+(?:market\s+)?open(?:ing)?\b(?!\s+(?:interest|range|price|chart|page))")
_AT_CLOSE = re.compile(r"\b(?:at|the)\s+(?:market\s+)?clos(?:e|ing)\b")


def _hour24(raw: str) -> int:
    """1-5 is the afternoon; 6 and 7 are premarket, which the scanner saves; "07" is a 24-hour clock."""
    hour = int(raw)
    return hour + 12 if 1 <= hour <= 5 and not raw.startswith("0") else hour


def _is_bare_clock(low: str, m: re.Match) -> bool:
    """A number with no "at": only a morning-to-noon hour, and only when nothing says it is a count or a price."""
    return 6 <= int(m.group(1)) <= 12 and not _NOT_A_TIME_BEFORE.search(low[:m.start()]) and not _NOT_A_TIME_AFTER.match(low, m.end())


def _spaced_clock(low: str) -> re.Match | None:
    """"9 30" or "at 2 15", the way it is typed on a phone. Never the day of a month ("nov 6 30")."""
    if _MONTH_DAY.search(low):
        return None
    for m in _SPACED.finditer(low):
        before = low[:m.start()].rstrip()
        after_at = before == "at" or before.endswith(" at")
        if (after_at and 1 <= int(m.group(1)) <= 12) or _is_bare_clock(low, m):
            return m
    return None


def read_minute(text: str, minute: int | None) -> int | None:
    """The time asked: `minute` is chatbot.parse_time's answer; this adds 'at 11', '930', '9 30', '10 oclock',
    'noon', and 'open' or 'close' only after "at" or "the" ("open nvda chart" is not 9:30)."""
    if minute is not None:
        return minute
    low = text.lower()
    m = _AT_COMPACT.search(low) or _spaced_clock(low)
    if m:
        return _hour24(m.group(1)) * 60 + int(m.group(2))
    m = _OCLOCK.search(low)
    if m and int(m.group(1)) <= 23:
        return _hour24(m.group(1)) * 60
    m = _AT_HOUR.search(low)
    if m and int(m.group(1)) <= 23:
        return _hour24(m.group(1)) * 60
    m = next((c for c in _COMPACT.finditer(low) if _is_bare_clock(low, c)), None)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    if re.search(r"\bnoon\b", low):
        return 12 * 60
    if _AT_OPEN.search(low):
        return OPEN_MIN
    if _AT_CLOSE.search(low):
        return CLOSE_MIN
    return None


def _without_clock(low: str) -> str:
    """The sentence with an hour after "at" and a spaced clock taken out, so neither is read as a count:
    "scanner at 10 setups" asks about 10:00, not about tickers with 10 setups."""
    m = _spaced_clock(low)
    if m:
        low = low[:m.start()] + " " + low[m.end():]
    return _AT_HOUR.sub(" at ", low)


def _top_of(low: str) -> int | None:
    m = _TOP_N.search(_without_clock(low))
    if not m:
        return None
    raw = next(g for g in m.groups() if g)
    n = _NUMBER_WORDS.get(raw) or int(raw)
    return n if 1 <= n <= 50 else None


def read(text: str, minute: int | None, side: str | None) -> Asked:
    low = text.lower()
    level, setup = _LEVEL.search(low), _SETUP.search(_without_clock(low))
    return Asked(boards=tuple(name for name, pattern in BOARDS if re.search(pattern, low)) or DEFAULT_BOARDS,
                 side=side or "bull", side_named=side is not None, minute=minute, top=_top_of(low),
                 level=int(level.group(1)) if level else None, level_up=bool(level and level.group(2)),
                 setups=int(next(g for g in setup.groups() if g)) if setup else None)


def says_scanner(text: str) -> bool:
    return bool(_SCANNER.search(text.lower()))


def _names_board(low: str) -> bool:
    return any(re.search(pattern, low) for _, pattern in BOARDS)


def about_trades(text: str) -> bool:
    """Is this about what the bot took ("which tickers did level 2 take"), with no scanner or board named?"""
    low = text.lower()
    return bool(TRADE_WORDS.search(low)) and not _SCANNER.search(low) and not _names_board(low)


def may_name_a_board_ticker(text: str) -> bool:
    """A column asked about on the scanner: its ticker may be one only the boards know ("rvol of nvda on the scanner")."""
    low = text.lower()
    return bool(_COLUMN.search(low) and (_SCANNER.search(low) or _names_board(low)))


def asks_time_on_board(text: str) -> bool:
    """"How long was NVDA on the scanner": the time on the board, not the time in its top."""
    low = text.lower()
    return bool(_HOW_LONG.search(low) and (_SCANNER.search(low) or _names_board(low)) and not _TOP_WORD.search(low))


def needs_its_ticker(text: str) -> bool:
    """A question that means nothing without a ticker, so finding one is worth reading the other side's boards."""
    return bool(_HOW_LONG.search(text.lower())) or may_name_a_board_ticker(text)


def other_side_of(asked: Asked) -> Asked:
    return replace(asked, side="bull" if asked.side == "bear" else "bear")


def route(text: str, has_ticker: bool, has_follow_up: bool, minute: int | None) -> str | None:
    """Which scanner question this is, or None when it is not one."""
    low = text.lower()
    scanner = bool(_SCANNER.search(low))
    board = _names_board(low)
    column = bool(_COLUMN.search(low))
    listy = bool(_LIST.search(low))
    if (BOT_WORDS.search(low) and not scanner) or about_trades(low):
        return None                                   # "top 3 trades today", "which tickers did level 2 take": about the bot
    if (_HOLD.search(low) and _TOP_WORD.search(low)) or asks_time_on_board(low):
        return "scanner_top"
    # "what was on top at 10:30": a bare "top" with a time and no ticker asks who led the board then.
    narrowed = (_top_of(low) is not None or bool(_LEVEL.search(low) or _SETUP.search(_without_clock(low)))
                or (bool(_TOP_WORD.search(low)) and minute is not None and not has_ticker))
    named_scanner = scanner or board or listy
    if (narrowed and (named_scanner or (minute is not None and not has_ticker))) or (listy and (scanner or board or minute is not None)):
        return "scanner_list"
    if not (scanner or board or column):
        return None
    if has_ticker and minute is None and not column:
        return "scanner_day"                          # "Nvda today scanner": the whole day, both boards
    if has_ticker or (has_follow_up and (column or _POINTER.search(low))):
        return "scanner_ticker"
    return None if column else "scanner_list"


def is_top_question(text: str) -> bool:
    low = text.lower()
    return bool((_HOLD.search(low) and _TOP_WORD.search(low)) or asks_time_on_board(low))


def limit_boards(asked: Asked) -> tuple[Asked, str | None]:
    """A regular question reads at most REGULAR_BOARDS boards; the line says which were left out."""
    if len(asked.boards) <= REGULAR_BOARDS:
        return asked, None
    kept, left = asked.boards[:REGULAR_BOARDS], asked.boards[REGULAR_BOARDS:]
    return replace(asked, boards=kept), f"I read {' and '.join(kept)} only. Ask about {', '.join(left)} in a separate question."


def wants_follow_up(text: str) -> bool:
    """Does a top question with no ticker mean the one the conversation is about ("how long was it in the top")?"""
    low = text.lower()
    return bool(_POINTER.search(low) or re.search(r"\bhow\s+long\b", low))


def without_scanner_words(text: str) -> str:
    """The sentence with board names and scanner words taken out, for finding its ticker."""
    shouting = text == text.upper()
    for _, pattern in BOARDS:
        text = re.sub(pattern, " ", text, flags=re.I)

    def keep(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        return " " if low in _JARGON or (low in _NOT_TICKERS and (shouting or word != word.upper())) else word

    text = re.sub(r"(?<![A-Za-z$.'])[A-Za-z]+(?![A-Za-z'])", keep, text)
    return text if shouting else text + " x"      # a capital ticker left alone must not read as an all-caps sentence


# ---------------------------------------------------------------- the compact index of one file
class Point(NamedTuple):
    ts: float
    hl: float | None
    shown: float | None
    pct: float | None
    price: float | None
    level: int | None
    setups: int | None
    names: tuple
    rvol: tuple
    sqz: tuple
    blank: tuple
    skittles: tuple


class Ticker:
    __slots__ = ("symbol", "last", "times", "points", "capped", "hits", "saved")

    def __init__(self, symbol: str, last: float, points: list[Point], truncated: bool = False, hits: int | None = None, saved: int | None = None) -> None:
        self.symbol, self.last, self.points = symbol, last, points
        self.hits = hits                              # how many scans matched it that day
        self.saved = len(points) if saved is None else saved      # how many rows were kept: only when a column changed
        self.times = [p.ts for p in points]
        #: When the day's row cap stopped saving this ticker (its last saved row), or None.
        self.capped = points[-1].ts if truncated else None


class Board:
    __slots__ = ("name", "bear", "day", "tickers", "broken", "retry", "waiting", "saved_at", "tops")

    def __init__(self, name: str, bear: bool, day: str) -> None:
        self.name, self.bear, self.day = name, bear, day
        self.tickers: dict[str, Ticker] = {}
        self.broken = False
        self.retry = False                            # the read itself failed, not the file: worth trying again
        self.waiting = ""                             # "loading" or "limited": nothing wrong with the file, it is just not read yet
        self.saved_at: float | None = None            # when the file this index came from was last written
        self.tops: dict[tuple, dict] = {}

    @property
    def label(self) -> str:
        return f"{self.name} {'puts' if self.bear else 'calls'}"

    def first_at(self) -> float | None:
        return min((t.times[0] for t in self.tickers.values()), default=None)

    def last_at(self) -> float | None:
        return max((t.last for t in self.tickers.values()), default=None)


class Busy(Exception):
    """A regular user's question needs a file read while another one is already reading."""


class BuildFailed(Exception):
    """The child could not build the index: out of time, crashed, or no turn came."""


_CHILD_SLOT = threading.Lock()
_CHILD_REST = {"until": 0.0}
#: Regular users' questions: one at a time may start a heavy read, across the whole app.
_REGULAR_SLOT = threading.Lock()
_TURN = threading.local()
INDEX_SCRIPT = Path(__file__).with_name("chat_scanner_index.py")
CREATE_NO_WINDOW, BELOW_NORMAL_PRIORITY_CLASS = 0x08000000, 0x00004000


def _child_flags() -> int:
    return CREATE_NO_WINDOW | BELOW_NORMAL_PRIORITY_CLASS if os.name == "nt" else 0


@contextmanager
def turn(regular: bool = False, account: str | None = None):
    """One question's turn at the files. A regular user who finds another regular question reading
    may use only indexes already in memory; a question that would need a read then gets Busy.
    `account` is who pays for a heavy read; None (a direct call) pays nothing."""
    mine = _REGULAR_SLOT.acquire(blocking=False) if regular else False
    _TURN.held_only = regular and not mine
    _TURN.regular, _TURN.account, _TURN.tried = regular, account, {}
    _TURN.deadline = time.monotonic() + (REGULAR_QUESTION_SECONDS if regular else QUESTION_SECONDS)
    try:
        yield
    finally:
        _TURN.held_only, _TURN.deadline, _TURN.regular, _TURN.account, _TURN.tried = False, None, False, None, None
        if mine:
            _REGULAR_SLOT.release()


def _child_lines(path: Path, bear: bool) -> list[str]:
    """Run the index builder as a child process and return its lines, within its own time limit.
    At most one runs at a time. The wait in communicate() costs this process nothing."""
    if time.monotonic() < _CHILD_REST["until"]:
        raise BuildFailed("the PC is busy")
    if not _CHILD_SLOT.acquire(timeout=CHILD_WAIT_SECONDS):
        raise BuildFailed("no turn came")
    try:
        child = subprocess.Popen([sys.executable, "-I", str(INDEX_SCRIPT), str(path), "bear" if bear else "bull"],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_child_flags())
        try:
            out, _ = child.communicate(timeout=CHILD_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            _CHILD_REST["until"] = time.monotonic() + CHILD_REST_SECONDS      # whoever asked: the PC is busy
            raise BuildFailed("out of time") from None
        if child.returncode != 0:
            raise BuildFailed(f"child exited {child.returncode}")
        return out.decode("ascii").splitlines()
    except OSError as exc:
        raise BuildFailed(type(exc).__name__) from None
    finally:
        _CHILD_SLOT.release()


#: How the index of a file is built. Tests swap this for an in-process build.
_run_builder = _child_lines


def _take(board: Board, item: list, shared: dict) -> None:
    symbol, last, truncated, rows = item[:4]
    hits, saved = (list(item[4:6]) + [None, None])[:2]

    def one(value: list) -> tuple:
        value = tuple(value)
        return shared.setdefault(value, value)        # the same few tuples repeat thousands of times

    points = [Point(r[0], r[1], r[2], r[3], r[4], r[5], r[6], one(r[7]), one(r[8]), one(r[9]), one(r[10]), one(r[11])) for r in rows]
    board.tickers[symbol] = Ticker(symbol, last, points, truncated, hits, saved)


def _parse(path: Path, name: str, bear: bool, day: str) -> Board:
    """One file's compact index, built outside this process and decoded a ticker at a time so the
    chart threads get a turn in between."""
    board, shared, done = Board(name, bear, day), {}, False
    try:
        for line in _run_builder(path, bear):
            item = json.loads(line)
            if isinstance(item, dict):
                if item.get("done"):
                    done, board.broken, board.retry = True, bool(item.get("broken")), bool(item.get("retry"))
                continue
            _take(board, item, shared)
        if not done:
            raise BuildFailed("the read stopped early")
    except (BuildFailed, ValueError, TypeError, IndexError):
        board.broken = board.retry = True
    if board.broken:
        board.tickers.clear()
    return board


#: path -> [(mtime_ns, size), Board, when last checked]. Newest used last.
_CACHE: OrderedDict[str, list] = OrderedDict()
#: The same, for what only regular users' reads built. Either store can answer anyone.
_REGULAR_CACHE: OrderedDict[str, list] = OrderedDict()
_CACHE_LOCK = threading.Lock()
_FILE_LOCKS: dict[str, threading.Lock] = {}


def _file_lock(key: str) -> threading.Lock:
    with _CACHE_LOCK:
        return _FILE_LOCKS.setdefault(key, threading.Lock())


def _trim(cache: OrderedDict, most: int) -> None:
    while len(cache) > most:
        cache.popitem(last=False)


def _held(key: str, promote: bool = True) -> list | None:
    """The copy in memory, if any. The owner using a copy a regular read built moves it into the owner's store."""
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry:
            _CACHE.move_to_end(key)
            return entry
        entry = _REGULAR_CACHE.get(key)
        if entry:
            _REGULAR_CACHE.move_to_end(key)
            if promote:
                _CACHE[key] = _REGULAR_CACHE.pop(key)
                _trim(_CACHE, CACHE_FILES)
        return entry


def _store(key: str, entry: list, regular: bool) -> None:
    with _CACHE_LOCK:
        if regular and key not in _CACHE:             # replacing the owner's copy pushes nothing out, so that one stays put
            _REGULAR_CACHE[key] = entry
            _REGULAR_CACHE.move_to_end(key)
            _trim(_REGULAR_CACHE, REGULAR_CACHE_FILES)
            return
        _REGULAR_CACHE.pop(key, None)
        _CACHE[key] = entry
        _CACHE.move_to_end(key)
        _trim(_CACHE, CACHE_FILES)


class _Job:
    """One file being read in the background. Questions wait on `done`; a later one joins it."""
    __slots__ = ("key", "path", "name", "bear", "day", "stamp", "saved_at", "size", "regular", "account", "taken", "done", "board", "error")

    def __init__(self, key: str, path: Path, name: str, bear: bool, day: str, info: os.stat_result, regular: bool, account: str | None) -> None:
        self.key, self.path, self.name, self.bear, self.day = key, path, name, bear, day
        self.stamp, self.saved_at, self.size = (info.st_mtime_ns, info.st_size), info.st_mtime, info.st_size
        self.regular, self.account, self.taken = regular, account, False
        self.done = threading.Event()
        self.board: Board | None = None
        self.error: BaseException | None = None


#: The file being read or waiting, by path, and the line: the owner's first, then oldest first.
_JOBS: dict[str, _Job] = {}
_LINE: list = []
_LINE_LOCK = threading.Lock()
_ORDER = itertools.count()
_BUILDER = {"running": False}


def _not_ready(name: str, bear: bool, day: str, why: str) -> Board:
    board = Board(name, bear, day)
    board.broken = board.retry = True
    board.waiting = why
    return board


def _build(job: _Job) -> None:
    """Read one file, to the child's own time limit. A regular account pays for a heavy read here,
    when a child is really about to start."""
    resting = time.monotonic() < _CHILD_REST["until"]
    if job.regular and job.account is not None and job.size >= HEAVY_READ_BYTES and not resting:
        if not chat_limits.LIMITS.take_read(job.account):
            job.board = _not_ready(job.name, job.bear, job.day, "limited")
            return
    board = _parse(job.path, job.name, job.bear, job.day)
    board.saved_at = job.saved_at
    job.board = board
    if not board.retry:                               # never remember a failed read: a past day would stay failed
        _store(job.key, [job.stamp, board, time.monotonic()], job.regular)


def _builder() -> None:
    """Works through the line, one file at a time, and stops when it is empty."""
    while True:
        with _LINE_LOCK:
            job = None
            while _LINE and job is None:
                _, _, candidate = heapq.heappop(_LINE)
                job = None if candidate.taken else candidate
            if job is None:
                _BUILDER["running"] = False
                return
            job.taken = True
        try:
            _build(job)
        except BaseException as exc:  # noqa: BLE001 - handed to whoever is waiting; this thread must keep working
            job.error = exc
        with _LINE_LOCK:
            _JOBS.pop(job.key, None)
        job.done.set()


def _enqueue(job: _Job) -> None:
    """Under _LINE_LOCK."""
    heapq.heappush(_LINE, (1 if job.regular else 0, next(_ORDER), job))
    if not _BUILDER["running"]:
        _BUILDER["running"] = True
        threading.Thread(target=_builder, name="chat-scanner-build", daemon=True).start()


def _time_left() -> float:
    deadline = getattr(_TURN, "deadline", None)
    return CHILD_TIMEOUT_SECONDS + CHILD_WAIT_SECONDS if deadline is None else deadline - time.monotonic()


def _start_or_join(key: str, path: Path, name: str, bear: bool, day: str, info: os.stat_result) -> tuple[_Job | None, bool]:
    """(the read of this file, did this question start it). No read when this question has run out of time."""
    regular = getattr(_TURN, "regular", False)
    with _LINE_LOCK:
        job = _JOBS.get(key)
        if job is not None:
            if job.regular and not regular and not job.taken:     # the owner wants it too: it moves up, and he does not pay
                job.regular = False
                heapq.heappush(_LINE, (0, next(_ORDER), job))
            return job, False
        if _time_left() <= 0:
            return None, False
        if regular and sum(1 for j in _JOBS.values() if j.regular and not j.taken) >= REGULAR_QUEUE:
            raise Busy()
        job = _Job(key, path, name, bear, day, info, regular, getattr(_TURN, "account", None))
        _JOBS[key] = job
        _enqueue(job)
        return job, True


def _load(path: Path, name: str, bear: bool, day: str, is_today: bool) -> Board | None:
    """One file's index, or None when the file is not there. Today's file changes all day, so it is
    looked at no more than every TODAY_RELOAD_SECONDS; while it is being read again, others get the copy in hand."""
    key = str(path)
    regular = getattr(_TURN, "regular", False)
    tried = getattr(_TURN, "tried", None)
    if tried and key in tried:                        # this question already found it unreadable: once is enough
        return tried[key]
    held = _held(key, promote=not regular)
    if held and is_today and time.monotonic() - held[2] < TODAY_RELOAD_SECONDS:
        return held[1]
    only_held = getattr(_TURN, "held_only", False)
    lock = _file_lock(key)
    if not lock.acquire(blocking=held is None and not only_held):
        if held:
            return held[1]
        raise Busy()
    try:
        held = _held(key, promote=not regular)        # another thread may have built it while this one waited
        try:
            info = path.stat()
        except OSError:
            with _CACHE_LOCK:
                _CACHE.pop(key, None)
                _REGULAR_CACHE.pop(key, None)
            return None
        stamp = (info.st_mtime_ns, info.st_size)
        if held and held[0] == stamp:
            held[2] = time.monotonic()
            return held[1]
        if only_held:
            if held:
                return held[1]
            raise Busy()
        job, mine = _start_or_join(key, path, name, bear, day, info)
    finally:
        lock.release()
    if held and not mine:
        return held[1]
    if job is None or not job.done.wait(max(_time_left(), 0)):
        return held[1] if held else _not_ready(name, bear, day, "loading")
    if job.error is not None:
        raise job.error
    if job.board.retry:
        if tried is not None:
            tried[key] = held[1] if held else job.board
        return held[1] if held else job.board
    return job.board


def _read_in_background(path: Path, name: str, bear: bool, day: str) -> _Job | None:
    """Start reading a file nobody will wait for, or join the read already going. Never raises and never blocks."""
    if getattr(_TURN, "held_only", False):
        return None
    try:
        job, _ = _start_or_join(str(path), path, name, bear, day, path.stat())
    except (OSError, Busy):
        return None
    return job


def _path(artifacts: Path, name: str, bear: bool, day: str) -> Path:
    root = Path(artifacts) / HISTORY_DIRNAME
    return board_dir(root / "bear" if bear else root, name) / f"{day}.json"


def _board(artifacts: Path, name: str, bear: bool, day: str, today: str) -> Board | None:
    return _load(_path(artifacts, name, bear, day), name, bear, day, day >= today)


def symbols(artifacts: Path, day: str, today: str, asked: Asked) -> set[str]:
    """Every ticker on the boards the question is about, so a name only the scanner knows is recognised."""
    out: set[str] = set()
    for name in asked.boards:
        board = _board(artifacts, name, asked.side == "bear", day, today)
        if board:
            out |= set(board.tickers)
    return out


# ---------------------------------------------------------------- the state of a board at a time
def _state(ticker: Ticker, t: float) -> Point | None:
    i = bisect_right(ticker.times, t) - 1
    if i < 0 or t > ticker.last + SEEN_GRACE_SECONDS:
        return None
    point = ticker.points[i]
    return point if t - point.ts <= FRESH_SECONDS else None


def _unknown_then(ticker: Ticker, t: float) -> bool:
    """No saved row speaks for t, yet it was seen before and matched again after. Rows are saved only
    when a column changes, and stop at the row cap, so whether it was on the board at t is not known."""
    return ticker.times[0] <= t <= ticker.last + SEEN_GRACE_SECONDS and _state(ticker, t) is None


def _stopped_by_cap(ticker: Ticker, t: float) -> bool:
    return ticker.capped is not None and t > ticker.capped


def _on_other_side(other: Board | None, symbol: str, t: float) -> Point | None:
    """A fresh row on the other side at t: then it was there, not on this side."""
    held = other.tickers.get(symbol) if other is not None else None
    return _state(held, t) if held is not None else None


def _unknown_at(board: Board, t: float, other: Board | None) -> list[Ticker]:
    return sorted((tk for tk in board.tickers.values() if _unknown_then(tk, t) and _on_other_side(other, tk.symbol, t) is None), key=lambda tk: tk.symbol)


def _other_side(artifacts: Path, board: Board, today: str, t: float, ticker: Ticker | None = None) -> Board | None:
    """The same board on the other side, to tell a capped ticker's side at t. A copy in memory is
    always used. Otherwise a read is started in the background when it would settle something, and
    this answer does not wait for it: it says "not known" now and the next question has the copy.
    Never for a regular user, whose answer simply stays "not known"."""
    path = _path(artifacts, board.name, not board.bear, board.day)
    held = _held(str(path), promote=False)
    if held:
        return None if held[1].broken else held[1]
    wanted = (ticker is not None and _unknown_then(ticker, t)) or any(_stopped_by_cap(tk, t) and _unknown_then(tk, t) for tk in board.tickers.values())
    if wanted and not getattr(_TURN, "regular", False):
        _read_in_background(path, board.name, not board.bear, board.day)
    return None


def ranking(board: Board, t: float) -> list[tuple[str, Point]]:
    """Who was on the board at t, ordered like his H/L-sorted board (option_track.hl_ranking)."""
    rows = [(s, p) for s, tk in board.tickers.items() if (p := _state(tk, t)) is not None and p.hl is not None]
    rows.sort(key=lambda r: (r[1].hl if board.bear else -r[1].hl, r[0].casefold(), r[0]))
    return rows


def _moves(ticker: Ticker, t: float, price: float | None, bear: bool) -> tuple[float | None, float | None, float | None, float | None]:
    """(best %, last %, best price, last price) after t, in the trade's direction."""
    later = [p.price for p in ticker.points[bisect_right(ticker.times, t):] if p.price]
    if not price or not later:
        return None, None, None, None
    best = min(later) if bear else max(later)

    def move(x: float) -> float:
        return (price - x) / price * 100 if bear else (x / price - 1) * 100

    return move(best), move(later[-1]), best, later[-1]


def _nearest(ticker: Ticker, t: float) -> float:
    """The closest time to t that this ticker counted as on the board."""
    i = bisect_right(ticker.times, t) - 1
    before = max(ticker.times[i], min(ticker.times[i] + FRESH_SECONDS, ticker.last + SEEN_GRACE_SECONDS)) if i >= 0 else None
    after = ticker.times[i + 1] if i + 1 < len(ticker.times) else None
    if before is None or (after is not None and after - t < t - before):
        return after
    return before


def _at(day: str, minute: int) -> float:
    """The end of that ET minute: a row saved any time during 9:30 counts for "at 9:30"."""
    d = date.fromisoformat(day)
    return datetime(d.year, d.month, d.day, minute // 60, minute % 60, 59, tzinfo=ET).timestamp()


def _clock(ts: float) -> str:
    return datetime.fromtimestamp(ts, ET).strftime("%I:%M %p").lstrip("0")


def _minute_clock(minute: int) -> str:
    return datetime(2000, 1, 1, minute // 60, minute % 60).strftime("%I:%M %p").lstrip("0")


def _signed(v: float | None, digits: int = 1, suffix: str = "%") -> str:
    return "not saved" if v is None else f"{round(v, digits) + 0.0:+.{digits}f}{suffix}"       # + 0.0: never "-0.0%"


def _cap_note(ticker: Ticker) -> str:
    return "" if ticker.capped is None else f" (saved prices stop at the row-saving cap, {_clock(ticker.capped)})"


def _then(ticker: Ticker, best: float | None, last: float | None) -> str:
    return ("no later saved price" if best is None else f"then best {_signed(best)}, last {_signed(last)}") + _cap_note(ticker)


def _behind(board: Board, t: float, now_ts: float) -> bool:
    """Today's index is at most a few minutes old. A time that has come but is newer than the copy
    must not read as "nothing on the board": it is answered as of the copy, and says so."""
    today = datetime.fromtimestamp(now_ts, ET).date().isoformat()
    return board.day == today and board.saved_at is not None and board.saved_at + SEEN_GRACE_SECONDS < t and t - 59 <= now_ts


def _behind_note(board: Board, asked: Asked, now_ts: float) -> list[str]:
    if asked.minute is None or not _behind(board, _at(board.day, asked.minute), now_ts):
        return []
    return [f"{board.label}: the saved history I have ends at {_clock(board.saved_at)}, so this is as of then. It is read again every few minutes."]


def _time_of(board: Board, asked: Asked, now_ts: float) -> float | None:
    if asked.minute is not None:
        t = _at(board.day, asked.minute)
        return board.saved_at if _behind(board, t, now_ts) else t
    last = board.last_at()
    return None if last is None else min(now_ts, last)


def _unreadable(board: Board | None, name: str, bear: bool, day: str) -> str | None:
    label = f"{name} {'puts' if bear else 'calls'}"
    if board is None:
        return f"{label}: {day} has no scanner history (it is kept {RETENTION_DAYS} days)."
    if board.waiting == "loading":
        return f"{label}: the saved history for {day} is still loading. Please ask again in about a minute."
    if board.waiting == "limited":
        return f"{label}: you have read a lot of saved history lately, so this board was not read. Please try again in a few minutes."
    if board.broken and board.retry:
        return f"{label}: the saved history for {day} could not be read just now. Please try again in a minute."
    if board.broken:
        return f"{label}: the saved history for {day} could not be read."
    if not board.tickers:
        return f"{label}: nothing was saved on {day}."
    return None


def _empty_board_line(board: Board, t: float) -> str:
    first, last = board.first_at(), board.last_at()
    if t < first:
        return f"Nothing was on {board.label} yet at {_clock(t)} on {board.day}. The first ticker came on at {_clock(first)}."
    if t > last + SEEN_GRACE_SECONDS:
        return f"Nothing was on {board.label} at {_clock(t)} on {board.day}. The last saved time that day was {_clock(last)}."
    return f"No ticker on {board.label} had a saved row in the 10 minutes before {_clock(t)} on {board.day}."


def _matches(point: Point, asked: Asked) -> bool:
    if asked.level is not None and (point.level is None or (point.level < asked.level if asked.level_up else point.level != asked.level)):
        return False
    return asked.setups is None or (point.setups is not None and point.setups >= asked.setups)


def _filter_words(asked: Asked) -> str:
    parts = []
    if asked.level is not None:
        parts.append(f"Level {asked.level}" + (" or higher" if asked.level_up else ""))
    if asked.setups is not None:
        parts.append(f"{asked.setups}+ setups")
    return " and ".join(parts)


def _level_text(point: Point) -> str:
    return "Level not saved" if point.level is None else f"Level {point.level}"


def _setups_text(point: Point) -> str:
    if point.setups is None:
        return "setups not saved"
    return f"{point.setups} setup{'' if point.setups == 1 else 's'}"


def _bullet(board: Board, rank: int, symbol: str, point: Point, t: float) -> str:
    held = board.tickers[symbol]
    best, last, _, _ = _moves(held, t, point.price, board.bear)
    return (f"- #{rank} {symbol} H/L {_signed(point.shown, 2, '')} · {_signed(point.pct)} · {_level_text(point)} · "
            f"{_setups_text(point)} · {_then(held, best, last)}")


def _names(tickers: list[Ticker], most: int = 10) -> str:
    shown = ", ".join(f"{tk.symbol} ({_clock(tk.capped)})" for tk in tickers[:most])
    return shown + (f" and {len(tickers) - most} more" if len(tickers) > most else "")


def _plain_names(tickers: list[Ticker], most: int = 10) -> str:
    shown = ", ".join(tk.symbol for tk in tickers[:most])
    return shown + (f" and {len(tickers) - most} more" if len(tickers) > most else "")


def _split_unknown(unknown: list[Ticker], t: float) -> tuple[list[Ticker], list[Ticker]]:
    """(stopped by the row cap, simply no row near t)."""
    return [tk for tk in unknown if _stopped_by_cap(tk, t)], [tk for tk in unknown if not _stopped_by_cap(tk, t)]


def _unknown_lines(board: Board, t: float, unknown: list[Ticker]) -> list[str]:
    capped, gaps = _split_unknown(unknown, t)
    lines = []
    if capped:
        lines.append(f"Not counted on {board.label} at {_clock(t)}: row saving stopped (time in brackets), so it is not known whether these were on the board then: "
                     f"{_names(capped)}. Ranks could be off.")
    if gaps:
        lines.append(f"Not counted on {board.label} at {_clock(t)}: no saved row near that time, so it is not known whether these were on the board then: "
                     f"{_plain_names(gaps)}. Ranks could be off.")
    return lines


def _stood(board: Board, ticker: str, t: float, ranked: list, other: Board | None = None) -> str:
    """Where one ticker stood on one board at t."""
    held = board.tickers.get(ticker)
    if held is None:
        return f"{board.name}: not on the board that day"
    for rank, (symbol, point) in enumerate(ranked, 1):
        if symbol == ticker:
            return f"{board.name}: #{rank} of {len(ranked)}, H/L {_signed(point.shown, 2, '')}"
    if _state(held, t) is not None:
        return f"{board.name}: on the board, but no H/L was saved so it has no rank"
    if _unknown_then(held, t):
        there = _on_other_side(other, ticker, t)
        if there is not None:
            return f"{board.name}: not on the board then (a saved row at {_clock(there.ts)} has it on {other.label})"
        if _stopped_by_cap(held, t):
            return f"{board.name}: not known, row saving stopped at {_clock(held.capped)}, last seen {_clock(held.last)}"
        return f"{board.name}: no saved row near that time, so not known (nearest time it was: {_clock(_nearest(held, t))})"
    return f"{board.name}: not on the board then (nearest time it was: {_clock(_nearest(held, t))})"


# ---------------------------------------------------------------- links and times for the screen
_TICKER_OK = re.compile(r"[A-Z][A-Z0-9.\-]{0,9}")
_DAY_OK = re.compile(r"\d{4}-\d{2}-\d{2}")


def _hhmm(minute: int | None) -> str | None:
    return None if minute is None else f"{minute // 60:02d}:{minute % 60:02d}"


def history_link(board: str, side: str, day: str, ticker: str | None = None, minute: int | None = None) -> dict | None:
    """A button to that board's History, built only from checked values; None when one is not valid."""
    if board not in BOARD_NAMES or side not in ("bull", "bear") or not _DAY_OK.fullmatch(str(day)):
        return None
    if ticker is not None and not _TICKER_OK.fullmatch(ticker):
        return None
    if minute is not None and not 0 <= minute < 24 * 60:
        return None
    name = f"Open {ticker + ' on ' if ticker else ''}{board}{' puts' if side == 'bear' else ''} History" + (f", {_hhmm(minute)}" if minute is not None else "")
    link = {"label": name, "view": SCANNER_VIEW, "list": board, "side": side, "tab": "history", "day": day}
    if ticker:
        link["ticker"] = ticker
    if minute is not None:
        link["time"] = _hhmm(minute)
    return link


def _links(*links: dict | None) -> list[dict]:
    seen, out = set(), []
    for link in links:
        key = json.dumps(link, sort_keys=True)
        if link and key not in seen:
            seen.add(key)
            out.append(link)
    return out[:MAX_LINKS]


def _as_of(ts: float | None, today: str) -> str | None:
    """"as of 15:56 ET", with the date when it is not today."""
    if ts is None:
        return None
    at = datetime.fromtimestamp(ts, ET)
    day = at.date().isoformat()
    return f"as of {at:%H:%M} ET" + ("" if day == today else f" on {day}")


def _rows_table(title: str, columns: list[tuple], rows: list[dict], link: dict | None) -> dict | None:
    if not rows:
        return None
    shown = rows[:MAX_TABLE_ROWS]
    table = {"kind": "rows", "title": title, "columns": [{"key": k, "label": label, **({"align": "right"} if right else {})} for k, label, right in columns], "rows": shown}
    if len(rows) > len(shown):
        table["note"] = f"Showing the first {len(shown)} of {len(rows)}."
    if link:
        table["link"] = link
    return table


_LIST_COLUMNS = [("board", "Board", False), ("rank", "#", True), ("symbol", "Symbol", False), ("hl", "H/L", True), ("pct", "% Chg", True), ("price", "Price", True), ("level", "Level", True), ("setups", "Setups", True)]
_TOP_COLUMNS = [("board", "Board", False), ("symbol", "Symbol", False), ("minutes", "Min in top", True), ("longest", "Longest", True), ("holds", "Holds", True), ("first", "First in", False)]


def _list_row(board: Board, rank: int, symbol: str, point: Point) -> dict:
    return {"board": board.name, "rank": str(rank), "symbol": symbol, "hl": _signed(point.shown, 2, ""), "pct": _signed(point.pct),
            "price": "" if point.price is None else f"${point.price:.2f}", "level": "" if point.level is None else str(point.level),
            "setups": "" if point.setups is None else str(point.setups)}


# ---------------------------------------------------------------- the answers
def answer_list(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str | None = None) -> dict:
    lines: list[str] = []
    facts: list[dict] = []
    stood: list[str] = []
    bear = asked.side == "bear"
    first_seen = None
    filtered = _filter_words(asked)
    when = None
    links: list[dict | None] = []
    rows: list[dict] = []
    for name in asked.boards:
        board = _board(artifacts, name, bear, day, today)
        problem = _unreadable(board, name, bear, day)
        if problem:
            lines.append(problem)
            facts.append({"board": name, "count": 0, "tickers": []})
            if ticker:
                stood.append(f"{name}: no history that day")
            continue
        t = _time_of(board, asked, now_ts)
        when = when or t
        links.append(history_link(name, asked.side, day, ticker, asked.minute))
        ranked = ranking(board, t)
        other = _other_side(artifacts, board, today, t, board.tickers.get(ticker) if ticker else None)
        unknown = _unknown_at(board, t, other)
        capped, gaps = _split_unknown(unknown, t)
        counts = {"capped": [tk.symbol for tk in capped], "unknown": [tk.symbol for tk in gaps]}
        lines += _behind_note(board, asked, now_ts)
        if ticker:
            stood.append(_stood(board, ticker, t, ranked, other))
        if not ranked:
            lines.append(_empty_board_line(board, t))
            lines += _unknown_lines(board, t, unknown)
            facts.append({"board": name, "count": 0, "tickers": [], **counts})
            continue
        label = _clock(t) if asked.minute is not None else f"the latest saved time, {_clock(t)},"
        picked = [(rank, s, p) for rank, (s, p) in enumerate(ranked, 1) if _matches(p, asked)]
        size = f"{len(ranked)} ticker{'' if len(ranked) == 1 else 's'} on the board"
        lines.append(f"{board.label} at {label} on {day}: " + (f"{len(picked)} of {size} had {filtered}." if filtered else f"{size}."))
        show = picked[:asked.top or SHOW_TOP]
        lines += [_bullet(board, rank, s, p, t) for rank, s, p in show]
        rows += [_list_row(board, rank, s, p) for rank, s, p in show]
        if len(picked) > len(show):
            lines.append(f"... and {len(picked) - len(show)} more.")
        first_seen = first_seen or (show[0][1] if show else None)
        lines += _unknown_lines(board, t, unknown)
        facts.append({"board": name, "count": len(ranked), "tickers": [s for _, s, _ in show], **counts})
    if ticker:
        clock = _minute_clock(asked.minute) if asked.minute is not None else "the latest saved time"
        hint = "" if asked.side_named or not any("not on the board" in place or "not known" in place for place in stood) else ' Add "puts" to check the bear scanner.'
        lines.append(f"{ticker} at {clock}, {'puts' if bear else 'calls'} side: " + "; ".join(stood) + "." + hint)
    suggest = ["which tickers stayed on top"]
    if ticker or first_seen:
        clock = _minute_clock(asked.minute) if asked.minute is not None else (_clock(when) if when else "")
        suggest.insert(0, f"{ticker or first_seen} at {clock} in the scanner".replace("  ", " "))
    links = _links(*links)
    at = _hhmm(asked.minute) or "latest saved time"
    return {"lines": lines + [SOURCE], "facts": {"boards": facts, "side": asked.side}, "suggest": suggest, "links": links, "asOf": _as_of(when, today),
            "table": _rows_table(f"{'Puts' if bear else 'Calls'} on the scanner, {day} at {at}", _LIST_COLUMNS, rows, links[0] if links else None)}


def _ticker_lines(board: Board, ticker: str, t: float, other: Board | None = None) -> tuple[list[str], bool | None]:
    """(lines, was it on the board at t: True, False, or None for not known) for one ticker on a board that had it that day."""
    held = board.tickers[ticker]
    clock = _clock(t)
    point = _state(held, t)
    if point is None and _unknown_then(held, t):
        there = _on_other_side(other, ticker, t)
        if there is not None:
            return [f"{ticker} was not on {board.label} at {clock} on {board.day}: a saved row at {_clock(there.ts)} has it on {other.label}."], False
        if _stopped_by_cap(held, t):
            saved = held.points[-1]
            return [f"{ticker} on {board.label} at {clock} on {board.day}: row saving stopped at {_clock(held.capped)}; last seen at {_clock(held.last)}. "
                    f"Whether it was on the board at {clock} is not known.",
                    f"- Last saved row, {_clock(saved.ts)}: H/L {_signed(saved.shown, 2, '')}, {_signed(saved.pct)}"], None
        return [f"{ticker} on {board.label} at {clock} on {board.day}: no saved row near that time, so it is not known whether it was on the board. "
                f"The nearest time it was on: {_clock(_nearest(held, t))}."], None
    if point is None:
        return [f"{ticker} was not on {board.label} at {clock} on {board.day}. The nearest time it was on: {_clock(_nearest(held, t))}."], False
    ranked = ranking(board, t)
    rank = next((i for i, (s, _) in enumerate(ranked, 1) if s == ticker), None)
    place = f"#{rank} of {len(ranked)}" if rank else "no rank (no H/L was saved)"
    price = "" if point.price is None else f", ${point.price:.2f}"
    lines = [f"{ticker} on {board.label} at {clock} on {board.day}: {place}, H/L {_signed(point.shown, 2, '')}, {_signed(point.pct)}{price} (row saved {_clock(point.ts)})."]
    blank = f" (blank: {', '.join(point.blank)})" if point.blank else ""
    names = f" ({', '.join(point.names)})" if point.names else ""
    lines += [f"- RVOL lit: {', '.join(point.rvol) or 'none'}", f"- SQZ lit: {', '.join(point.sqz) or 'none'}{blank}",
              f"- Skittles lit: {', '.join(point.skittles) or 'none'}", f"- {_setups_text(point).capitalize()}{names} · {_level_text(point)}, as saved"]
    best, last, best_price, last_price = _moves(held, t, point.price, board.bear)
    lines.append(("- After that: no later saved price" if best is None else
                  f"- After that: best {_signed(best)} (${best_price:.2f}), last {_signed(last)} (${last_price:.2f})") + _cap_note(held))
    capped, gaps = _split_unknown([tk for tk in _unknown_at(board, t, other) if tk.symbol != ticker], t)
    if capped:
        lines.append(f"- Rank could be off: row saving stopped, so it is not known whether these were on the board then: {_names(capped)}.")
    if gaps:
        lines.append(f"- Rank could be off: no saved row near that time, so it is not known whether these were on the board then: {_plain_names(gaps)}.")
    return lines, True


def answer_ticker(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str) -> dict:
    """One ticker at one time. With no side named: the calls, or the puts when it was only on those then."""
    results = []
    for bear in ([asked.side == "bear"] if asked.side_named else [False, True]):
        lines, absent, on, had_it, unknown, places, seen_at = [], [], False, False, False, [], None
        for name in asked.boards:
            board = _board(artifacts, name, bear, day, today)
            problem = _unreadable(board, name, bear, day)
            if problem:
                lines.append(problem)
            elif ticker not in board.tickers:
                absent.append(f"{ticker} was not on {board.label} at all on {day}.")
            else:
                t = _time_of(board, asked, now_ts)
                found, here = _ticker_lines(board, ticker, t, _other_side(artifacts, board, today, t, board.tickers[ticker]))
                lines += _behind_note(board, asked, now_ts) + found
                on, had_it, unknown = on or here is True, True, unknown or here is None
                places.append(name)
                seen_at = seen_at or t
        results.append((bear, lines if had_it else lines + absent, on, had_it, unknown, places, seen_at))
        if on:
            break
    # On the board beats "had it that day", which beats not having it at all.
    bear, lines, on, _, unknown, places, seen_at = next((r for r in results if r[2]), None) or next((r for r in results if r[3]), results[0])
    hint = ['Add "puts" to check the bear scanner.'] if not on and not bear and not asked.side_named else []
    return {"lines": lines + hint + [SOURCE], "facts": {"onBoard": True if on else None if unknown else False, "side": "bear" if bear else "bull"},
            "suggest": [f"why was {ticker} missed", f"how long was {ticker} in the top", "which tickers stayed on top"],
            "links": _links(*(history_link(name, "bear" if bear else "bull", day, ticker, asked.minute) for name in places or asked.boards)),
            "table": None, "asOf": _as_of(seen_at, today)}


def _covered_minutes(ticker: Ticker) -> int:
    """Minutes the saved rows speak for: each row for FRESH_SECONDS, never past the time it was last seen."""
    total, covered_to, limit = 0.0, 0.0, ticker.last + SEEN_GRACE_SECONDS
    for ts in ticker.times:
        stop = min(ts + FRESH_SECONDS, limit)
        total += max(0.0, stop - max(ts, covered_to))
        covered_to = max(covered_to, stop)
    return round(total / 60)


def _has_gap(ticker: Ticker, start: float = 0.0, end: float = float("inf")) -> bool:
    """A stretch between `start` and `end` with no saved row, before it was last seen."""
    stops = ticker.times[1:] + [ticker.last + SEEN_GRACE_SECONDS]
    return any(max(ts + FRESH_SECONDS, start) < min(stop, end) for ts, stop in zip(ticker.times, stops))


def answer_on_board(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str) -> dict:
    """"How long was NVDA on the scanner": its time on each board, on both sides when none is named."""
    lines: list[str] = []
    absent: list[str] = []
    facts: list[dict] = []
    for bear in ([asked.side == "bear"] if asked.side_named else [False, True]):
        for name in asked.boards:
            board = _board(artifacts, name, bear, day, today)
            problem = _unreadable(board, name, bear, day)
            if problem:
                lines.append(problem)
                continue
            held = board.tickers.get(ticker)
            if held is None:
                absent.append(f"{ticker} was not on {board.label} at all on {day}.")
                continue
            minutes = _covered_minutes(held)
            facts.append({"board": name, "side": "bear" if bear else "bull", "minutes": minutes})
            why = (f" Row saving stopped at {_clock(held.capped)}, so that is a minimum." if held.capped is not None and held.last > held.capped + FRESH_SECONDS
                   else " Between saved rows it is not known whether it stayed on the board, so that is a minimum." if _has_gap(held) else "")
            lines.append(f"{ticker} on {board.label} on {day}: first saved row {_clock(held.times[0])}, last seen {_clock(held.last)}. Saved rows cover about {minutes} min.{why}")
    if not facts:
        lines += absent
    longest = max(facts, key=lambda f: f["minutes"], default=None)
    side = longest["side"] if longest else asked.side
    return {"lines": lines + [SOURCE], "facts": {"boards": facts, "side": side},
            "suggest": [f"how long was {ticker} in the top", "which tickers stayed on top"],
            "links": _links(*(history_link(f["board"], f["side"], day, ticker) for f in facts)), "table": None, "asOf": None}


def top_stats(board: Board, n: int, end_minute: int) -> dict[str, dict]:
    """Minute by minute, 9:30 to the close: who was in the top n, for how long and in how many holds."""
    key = (n, end_minute)
    if key in board.tops:
        return board.tops[key]
    stats: dict[str, dict] = {}
    runs: dict[str, int] = {}
    for minute in range(OPEN_MIN, end_minute):
        t = _at(board.day, minute)
        now_runs = {}
        for symbol, point in ranking(board, t)[:n]:
            run = now_runs[symbol] = runs.get(symbol, 0) + 1
            s = stats.setdefault(symbol, {"minutes": 0, "longest": 0, "holds": 0, "firstAt": t, "firstPrice": point.price})
            s["minutes"] += 1
            s["longest"] = max(s["longest"], run)
            s["holds"] += run == HOLD_MINUTES
            if s["firstPrice"] is None:
                s["firstAt"], s["firstPrice"] = t, point.price
        runs = now_runs
    board.tops[key] = stats
    return stats


def _cut_short(ticker: Ticker) -> bool:
    """Did the row cap hide part of this ticker's day?"""
    return ticker.capped is not None and ticker.last > ticker.capped + FRESH_SECONDS


def _gapped(board: Board, ticker: Ticker) -> bool:
    """Is there a stretch of the session with no saved row for this ticker, though it was seen later?"""
    return _has_gap(ticker, _at(board.day, OPEN_MIN) - 59, _at(board.day, SESSION_END_MIN - 1))


def _top_text(board: Board, symbol: str, s: Mapping, n: int) -> str:
    held = board.tickers[symbol]
    best, last, _, _ = _moves(held, s["firstAt"], s["firstPrice"], board.bear)
    short = _cut_short(held) or _gapped(board, held)
    return (f"{'at least ' if short else ''}{s['minutes']} min in the top {n} · longest {'saved ' if short else ''}run {s['longest']} min · {s['holds']} hold{'' if s['holds'] == 1 else 's'} of "
            f"{HOLD_MINUTES}+ min · first in at {_clock(s['firstAt'])} · {_then(held, best, last)}")


def _capped_top_line(board: Board, capped: list[Ticker]) -> str:
    return (f"{board.label}: saved rows stopped at the row-saving cap for {_names(capped)}. Their minutes are a minimum, "
            "and other tickers' minutes in the top could be too high.")


def _gapped_top_line(board: Board, gapped: list[Ticker]) -> str:
    return (f"{board.label}: part of the session has no saved row for {_plain_names(gapped)}, so it is not known whether they were on the board then. "
            "Their minutes are a minimum, and other tickers' minutes in the top could be too high.")


def _top_side(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str | None, bear: bool) -> dict:
    lines: list[str] = []
    facts: list[dict] = []
    now = datetime.fromtimestamp(now_ts, ET)
    end = min(SESSION_END_MIN, now.hour * 60 + now.minute + 1) if day >= today else SESSION_END_MIN
    span = f"9:30 AM to {_minute_clock(end)} on {day}"
    leader = None
    absent: list[str] = []
    had_it = False
    minutes = 0
    rows: list[dict] = []
    read: list[str] = []
    newest = None
    for name in asked.boards:
        board = _board(artifacts, name, bear, day, today)
        problem = _unreadable(board, name, bear, day)
        if problem:
            lines.append(problem)
            continue
        read.append(name)
        newest = max(newest or 0.0, min(board.last_at() or 0.0, now_ts)) or None
        n = asked.top or TOP_N.get(name, DEFAULT_TOP)
        stats = top_stats(board, n, end)
        capped = sorted((tk for tk in board.tickers.values() if _cut_short(tk)), key=lambda tk: tk.symbol)
        gapped = sorted((tk for tk in board.tickers.values() if not _cut_short(tk) and _gapped(board, tk)), key=lambda tk: tk.symbol)
        if ticker:
            s = stats.get(ticker)
            minutes += s["minutes"] if s else 0
            facts.append({"board": name, "top": n, "minutes": s["minutes"] if s else 0})
            held = board.tickers.get(ticker)
            if held is None:
                absent.append(f"{ticker} was not on {board.label} at all on {day}.")
                continue
            had_it = True
            if s:
                lines.append(f"{ticker} on {board.label}, {span}: {_top_text(board, ticker, s, n)}.")
            elif _cut_short(held):
                lines.append(f"{ticker} on {board.label}, {span}: not in the top {n} while its rows were saved. The row-saving cap was reached at {_clock(held.capped)}, so after that is not known.")
            elif _gapped(board, held):
                lines.append(f"{ticker} on {board.label}, {span}: not in the top {n} while it had saved rows. Between saved rows is not known.")
            else:
                lines.append(f"{ticker} on {board.label}, {span}: never in the top {n}.")
            others = [tk for tk in capped if tk.symbol != ticker]
            if others:
                lines.append(_capped_top_line(board, others))
            others = [tk for tk in gapped if tk.symbol != ticker]
            if others:
                lines.append(_gapped_top_line(board, others))
            continue
        order = sorted(stats.items(), key=lambda kv: (-kv[1]["minutes"], -kv[1]["longest"], kv[0]))
        if not order:
            lines.append(f"{board.label}: nothing was in the top {n} from {span}.")
            facts.append({"board": name, "top": n, "tickers": []})
            continue
        lines.append(f"{board.label}, {span}: {len(order)} ticker{'' if len(order) == 1 else 's'} reached the top {n}.")
        lines += [f"- {symbol}: {_top_text(board, symbol, s, n)}" for symbol, s in order[:TOP_NAMES]]
        rows += [{"board": name, "symbol": symbol, "minutes": str(s["minutes"]), "longest": str(s["longest"]), "holds": str(s["holds"]), "first": _clock(s["firstAt"])} for symbol, s in order[:TOP_NAMES]]
        if len(order) > TOP_NAMES:
            lines.append(f"... and {len(order) - TOP_NAMES} more.")
        if capped:
            lines.append(_capped_top_line(board, capped))
        if gapped:
            lines.append(_gapped_top_line(board, gapped))
        leader = leader or order[0][0]
        facts.append({"board": name, "top": n, "tickers": [s for s, _ in order[:TOP_NAMES]]})
    if ticker and not had_it:
        lines += absent
    return {"lines": lines, "facts": facts, "leader": leader, "minutes": minutes, "bear": bear, "rows": rows, "read": read, "newest": newest}


def answer_top(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str | None = None) -> dict:
    """Who stayed in the top. For one ticker with no side named: the calls, or the puts when it was only in the top on those."""
    sides = [False, True] if ticker and not asked.side_named else [asked.side == "bear"]
    results = []
    for bear in sides:
        results.append(_top_side(artifacts, day, today, now_ts, asked, ticker, bear))
        if results[-1]["minutes"]:
            break
    picked = results[-1] if results[-1]["minutes"] else results[0]
    lines = list(picked["lines"])
    if not ticker and not asked.side_named:
        lines.append('That is the calls side. Add "puts" to see the bear scanner.')
    subject = ticker or picked["leader"]
    suggest = ["from the scanner at 9:30 give me the list of tickers"]
    if subject:
        suggest.insert(0, f"why was {subject} missed")
    side = "bear" if picked["bear"] else "bull"
    links = _links(*(history_link(name, side, day, ticker) for name in picked["read"]))
    return {"lines": lines + [SOURCE], "facts": {"boards": picked["facts"], "side": side}, "suggest": suggest, "links": links, "asOf": _as_of(picked["newest"], today),
            "table": _rows_table(f"Who stayed in the top, {'puts' if picked['bear'] else 'calls'}, {day}", _TOP_COLUMNS, picked["rows"], links[0] if links else None)}


def last_session(day: date, named: bool, minute: int | None, now: datetime) -> date:
    """With no day named: the day to read. On a weekend, or for a time that has not come yet today, that is the last weekday."""
    if named or day != now.date() or (day.weekday() < 5 and (minute is None or minute <= now.hour * 60 + now.minute)):
        return day
    day -= timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


# ---------------------------------------------------------------- one ticker's whole day
def _peek(artifacts: Path, name: str, bear: bool, day: str, today: str, start: bool) -> tuple[Board | None, _Job | None]:
    """(what is in memory now, the read under way). Never waits. No board and no read means the file
    is not there; a file not in memory comes back as "loading" and, with `start`, begins reading."""
    path = _path(artifacts, name, bear, day)
    key = str(path)
    held = _held(key, promote=not getattr(_TURN, "regular", False))
    try:
        info = path.stat()
    except OSError:
        return (held[1] if held else None), None
    stamp = (info.st_mtime_ns, info.st_size)
    if held and held[0] == stamp:
        return held[1], None
    if held and day >= today and time.monotonic() - held[2] < TODAY_RELOAD_SECONDS:
        return held[1], None
    job = _read_in_background(path, name, bear, day) if start else _JOBS.get(key)
    return (held[1] if held else _not_ready(name, bear, day, "loading")), job


def _has_file(artifacts: Path, boards: tuple, day: str) -> bool:
    return any(_path(artifacts, name, bear, day).exists() for name in boards for bear in (False, True))


def _newest_day(artifacts: Path, boards: tuple, day: str, today: str, now_ts: float) -> str:
    """Before the first premarket scan today is not a session yet, even though the overnight scan has
    already started today's file: the day to read is then the last weekday that has one."""
    started = datetime.fromtimestamp(now_ts, ET).hour * 60 >= FIRST_SCAN_MIN
    if day < today or (started and _has_file(artifacts, boards, day)):
        return day
    at = date.fromisoformat(day)
    for _ in range(DAY_FALLBACK_DAYS):
        at -= timedelta(days=1)
        if at.weekday() < 5 and _has_file(artifacts, boards, at.isoformat()):
            return at.isoformat()
    return day


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


def _day_line(board: Board, held: Ticker) -> str:
    saved = held.points[-1]
    counts = (f"{_plural(held.hits, 'scan')}, " if held.hits is not None else "") + _plural(held.saved, "saved row")
    price = "" if saved.price is None else f", ${saved.price:.2f}"
    return (f"{held.symbol} on {board.label}: first {_clock(held.times[0])}, last {_clock(held.last)}, {counts}. "
            f"Last saved row {_clock(saved.ts)}: H/L {_signed(saved.shown, 2, '')}, {_signed(saved.pct)}{price}.")


def answer_day(artifacts: Path, day: str, today: str, now_ts: float, asked: Asked, ticker: str) -> dict:
    """"Nvda today scanner": both boards, both sides. The rows themselves are not read here: `table` is
    only a reference, and the screen draws the board's own columns from the board's own History route.
    The line per board comes from the index, when it is in memory or arrives within DAY_WAIT_SECONDS.
    Only the side asked about starts a read; the other side is shown if it is already in memory."""
    day = _newest_day(artifacts, asked.boards, day, today, now_ts)
    first = asked.side == "bear"
    sections = [(name, bear) for bear in (first, not first) for name in asked.boards]
    found = {section: _peek(artifacts, section[0], section[1], day, today, section[1] == first) for section in sections}
    limit = time.monotonic() + DAY_WAIT_SECONDS
    for _, job in found.values():
        if job is not None:
            job.done.wait(max(limit - time.monotonic(), 0))
    lines: list[str] = []
    facts: list[dict] = []
    links: list[dict | None] = []
    newest = None
    for name, bear in sections:
        board, job = found[(name, bear)]
        if job is not None and job.done.is_set() and job.board is not None and (board.waiting or not job.board.retry):
            board = job.board
        side = "bear" if bear else "bull"
        label = f"{name} {'puts' if bear else 'calls'}"
        fact = {"board": name, "side": side, "state": "none"}
        facts.append(fact)
        if board is None:
            if bear == first:
                lines.append(f"Not on {label} that day: nothing was saved.")
            continue
        if board.waiting == "loading":
            fact["state"] = "loading"
            if bear == first:
                lines.append(f"{label}: the summary is still being read. The table has the whole day.")
                links.append(history_link(name, side, day, ticker))
            continue
        if board.broken:
            fact["state"] = "unreadable"
            lines.append(_unreadable(board, name, bear, day))
            continue
        held = board.tickers.get(ticker)
        if held is None:
            fact["state"] = "absent"
            lines.append(f"Not on {label} that day.")
            continue
        fact.update(state="ok", firstAt=_clock(held.times[0]), lastAt=_clock(held.last), scans=held.hits, savedRows=held.saved)
        newest = max(newest or 0.0, held.last)
        lines.append(_day_line(board, held))
        links.append(history_link(name, side, day, ticker))
    table = {"kind": "scanner_day", "title": f"{ticker} on the scanner, {day}", "ticker": ticker, "day": day,
             "sections": [{"list": name, "side": "bear" if bear else "bull"} for name, bear in sections]}
    return {"lines": lines + [DAY_SOURCE], "facts": {"boards": facts, "side": asked.side, "day": day, "ticker": ticker}, "day": day,
            "suggest": [f"{ticker} at 9:30 in the scanner", "which tickers stayed on top"],
            "links": _links(*links), "table": table, "asOf": _as_of(newest, today)}


# ---------------------------------------------------------------- the top of the board now
#: path -> ((mtime_ns, size), compact rows, epoch of the data). Only the few fields shown are kept.
_NOW_CACHE: dict[str, tuple] = {}
_NOW_LOCK = threading.Lock()
_ARTIFACTS = Path(__file__).resolve().parents[1] / "artifacts"


def artifacts_dir(ctx: Mapping | None = None) -> Path:
    """Where the saved files are: the session's, then the same test override the chat route honours."""
    given = (ctx or {}).get("artifacts")
    override = os.environ.get("AGX_MOMX_GRADE_DIR", "").strip()
    return Path(given) if given else Path(override) if override else _ARTIFACTS


def _board_file(artifacts: Path, name: str, bear: bool) -> Path:
    """The finished board momx/service.py keeps on disk. Same name rule and same override as that file."""
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name).strip("._") or "_"
    override = os.environ.get("MOMX_BOARD_CACHE_DIR", "").strip()
    return (Path(override) if override else Path(artifacts) / "momx_board_cache") / f"{safe}{'.bear' if bear else ''}.json"


def _stamp_epoch(value) -> float | None:
    try:
        at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=ET)).timestamp()


def _read_board_now(path: Path, bear: bool) -> tuple[list[dict], float] | None:
    """The scan matches of one finished board in H/L order, and when its data is from. Re-read only when the file changed."""
    try:
        info = path.stat()
    except OSError:
        return None
    stamp = (info.st_mtime_ns, info.st_size)
    with _NOW_LOCK:
        held = _NOW_CACHE.get(str(path))
    if held and held[0] == stamp:
        return held[1], held[2]
    try:
        doc = json.loads(path.read_bytes().decode("utf-8"))   # read_bytes: the handle is held for a moment, never for the parse
    except (OSError, ValueError):
        return None
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        return None
    by_symbol = {str(r.get("symbol") or "").upper(): r for r in doc["rows"] if isinstance(r, dict)}
    rows = []
    for symbol, shown in hl_ranking(doc["rows"], bear)[:50]:
        row = by_symbol.get(symbol, {})
        rows.append({"symbol": symbol, "hl": shown, "pct": row.get("pctChange"), "price": row.get("last")})
    at = next((t for t in map(_stamp_epoch, (doc.get("tapeAsOf"), doc.get("scanCompletedAt"), doc.get("generatedAt"))) if t), info.st_mtime)
    with _NOW_LOCK:
        _NOW_CACHE[str(path)] = (stamp, rows, at)
    return rows, at


def _top_from_memory(artifacts: Path, name: str, bear: bool, now: datetime) -> tuple[list[dict], float] | None:
    """After hours the board has no matches: the last saved moment of the latest day, if its index is already in memory."""
    at = now.date()
    for _ in range(DAY_FALLBACK_DAYS):
        held = _held(str(_path(artifacts, name, bear, at.isoformat())), promote=False)
        if held and not held[1].broken and held[1].tickers:
            t = held[1].last_at()
            return [{"symbol": s, "hl": p.shown, "pct": p.pct, "price": p.price} for s, p in ranking(held[1], t)[:50]], t
        at -= timedelta(days=1)
    return None


def top_now(list_name: str, side: str = "bull", n: int | None = None, artifacts: Path | None = None, now: datetime | None = None) -> dict | None:
    """The top of a board now, from the newest finished board on disk; never starts a build and never
    reads a day file. None when there is nothing to show, so a card built on it is left out.
    {"list", "side", "rows": [{"rank", "symbol", "hl", "pct", "price"}], "count", "asOf", "asOfEpoch", "source": "board"|"history", "links"}."""
    if list_name not in BOARD_NAMES or side not in ("bull", "bear"):
        return None
    now = (now or datetime.now(ET)).astimezone(ET)
    base = Path(artifacts) if artifacts is not None else artifacts_dir()
    bear = side == "bear"
    source = "board"
    found = _read_board_now(_board_file(base, list_name, bear), bear)
    if found and now.timestamp() - found[1] > BOARD_MAX_AGE_SECONDS:
        found = None
    if not found or not found[0]:
        source = "history"
        found = _top_from_memory(base, list_name, bear, now)
    if not found or not found[0]:
        return None
    rows, at = found
    count = n if isinstance(n, int) and n > 0 else TOP_N.get(list_name, DEFAULT_TOP)
    day = datetime.fromtimestamp(at, ET).date().isoformat()
    return {"list": list_name, "side": side, "rows": [{"rank": i, **row} for i, row in enumerate(rows[:count], 1)], "count": len(rows),
            "asOf": _as_of(at, now.date().isoformat()), "asOfEpoch": at, "source": source,
            "links": _links(history_link(list_name, side, day) if source == "history" else
                            {"label": f"Open {list_name}{' puts' if bear else ''} board", "view": SCANNER_VIEW, "list": list_name, "side": side, "tab": "live"})}


# ---------------------------------------------------------------- what the chat's registry offers
_SCANNER_PAGE_LINK = {"label": "Open MomX Scanner", "view": SCANNER_VIEW}
_OWNER_SUGGESTION = re.compile(r"\bmissed\b|\bwhy\b")


def _reply(lines: list[str], facts: Mapping | None = None, links: list | None = None, table: dict | None = None, as_of: str | None = None, suggest: list | None = None) -> dict:
    return {"lines": list(lines), "facts": dict(facts or {}), "links": list(links or []), "table": table, "asOf": as_of, "suggest": list(suggest or [])}


def _clean_ticker(value) -> str | None:
    word = str(value or "").strip().lstrip("$").upper()
    return word if _TICKER_OK.fullmatch(word) else None


def _tool_side(value) -> str | None:
    word = str(value or "").strip().lower()
    return {"bull": "bull", "call": "bull", "calls": "bull", "bear": "bear", "put": "bear", "puts": "bear"}.get(word)


def _tool_boards(args: Mapping) -> tuple | None:
    """The boards asked by name, or the two defaults. None when a name is not a board."""
    raw = args.get("lists", args.get("list"))
    if raw in (None, "", []):
        return DEFAULT_BOARDS
    wanted = [raw] if isinstance(raw, str) else raw if isinstance(raw, list) else [None]
    by_low = {name.lower(): name for name in BOARD_NAMES}
    picked = [by_low.get(str(name or "").strip().lower()) for name in wanted]
    return None if None in picked else tuple(dict.fromkeys(picked))


def _tool_minute(value) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m and int(m.group(1)) <= 23 and int(m.group(2)) <= 59:
        return int(m.group(1)) * 60 + int(m.group(2))
    from momx.chatbot import parse_time                # chatbot imports this module
    return read_minute(f"at {text}" if text[0].isdigit() else text, parse_time(text, scanner=True))


def _tool_day(value, minute: int | None, now: datetime) -> str:
    from momx.chatbot import parse_day
    day, named = parse_day(str(value or ""), now.date())
    return last_session(day, named, minute, now).isoformat()


def _count(value, low: int, high: int) -> int | None:
    return value if type(value) is int and low <= value <= high else None


def _tool(answer, needs_ticker: bool):
    """A registry handler around one answer: re-checks the scope, reads the arguments, never raises."""
    def handler(args: Mapping, ctx: Mapping) -> dict:
        args = args if isinstance(args, Mapping) else {}
        ctx = ctx if isinstance(ctx, Mapping) else {}
        scope = ctx.get("scope")
        if scope not in ("owner", "scanner"):
            return _reply(["That is not available in this chat."])
        regular = scope != "owner"
        try:
            now = ctx.get("now")
            now = (now if isinstance(now, datetime) else datetime.now(ET)).astimezone(ET)
            ticker = _clean_ticker(args.get("ticker"))
            if needs_ticker:
                ticker = ticker or _clean_ticker(ctx.get("ticker")) or _clean_ticker(ctx.get("pageTicker"))
            if needs_ticker and not ticker:
                return _reply(["Which ticker? Add one to the question, for example NVDA."], links=[_SCANNER_PAGE_LINK])
            boards = _tool_boards(args)
            if boards is None:
                return _reply([f"I do not know a board called {str(args.get('lists', args.get('list')))[:40]}. The boards I can read are {', '.join(BOARD_NAMES)}."], links=[_SCANNER_PAGE_LINK])
            minute = _tool_minute(args.get("time"))
            side = _tool_side(args.get("side"))
            asked = Asked(boards=boards, side=side or "bull", side_named=side is not None, minute=minute, top=_count(args.get("top"), 1, 50),
                          level=_count(args.get("level"), 0, 3), level_up=bool(args.get("levelUp")), setups=_count(args.get("setups"), 0, 99))
            left_out = None
            if regular:
                asked, left_out = limit_boards(asked)
            day = _tool_day(args.get("day"), OPEN_MIN if minute is None and answer is answer_top else minute, now)
            today = now.date().isoformat()
            with turn(regular, str(ctx.get("account") or "") or None):
                out = answer(artifacts_dir(ctx), day, today, now.timestamp(), asked, ticker)
        except Busy:
            return _reply([BUSY_LINE], links=[_SCANNER_PAGE_LINK])
        except Exception as exc:  # noqa: BLE001 - a question about a file must never fail the chat
            return _reply([f"I could not read the scanner history for that question ({type(exc).__name__}). Nothing was changed."], links=[_SCANNER_PAGE_LINK])
        lines = out["lines"] if not left_out else [*out["lines"][:-1], left_out, out["lines"][-1]]
        suggest = [s for s in out.get("suggest") or [] if not regular or not (BOT_WORDS.search(s.lower()) or _OWNER_SUGGESTION.search(s.lower()))]
        facts = {"day": out.get("day", day), "ticker": ticker, **out.get("facts", {})}
        return _reply(lines, facts, out.get("links"), out.get("table"), out.get("asOf"), suggest)
    return handler


def _schema(properties: dict, required: list | None = None) -> dict:
    return {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}


_TICKER_ARG = {"type": "string", "description": "Stock ticker, for example NVDA"}
_DAY_ARG = {"type": "string", "description": "YYYY-MM-DD, today, yesterday or a weekday. Leave out for the latest session"}
_SIDE_ARG = {"type": "string", "enum": ["bull", "bear"], "description": "bull is the calls board, bear the puts board"}
_LIST_ARG = {"type": "string", "enum": list(BOARD_NAMES), "description": "One board. Leave out for Mag7 and Watchlist"}
_TIME_ARG = {"type": "string", "description": "Clock time in ET, HH:MM on a 24-hour clock"}
_TOP_ARG = {"type": "integer", "minimum": 1, "maximum": 50, "description": "How many from the top"}


TOOLS = {
    "scanner_day": {"scope": "all", "description": "One ticker's whole day on the scanner, on Mag7 and Watchlist, calls and puts. Use for a ticker and a scanner word with no clock time.",
                    "args": _schema({"ticker": _TICKER_ARG, "day": _DAY_ARG, "side": _SIDE_ARG, "list": _LIST_ARG}, ["ticker"]), "handler": _tool(answer_day, True)},
    "scanner_ticker": {"scope": "all", "description": "What one ticker showed on the scanner at one clock time.",
                       "args": _schema({"ticker": _TICKER_ARG, "time": _TIME_ARG, "day": _DAY_ARG, "side": _SIDE_ARG, "list": _LIST_ARG}, ["ticker"]), "handler": _tool(answer_ticker, True)},
    "scanner_list": {"scope": "all", "description": "Who was on a scanner board at a time, ranked by H/L.",
                     "args": _schema({"time": _TIME_ARG, "day": _DAY_ARG, "side": _SIDE_ARG, "list": _LIST_ARG, "ticker": _TICKER_ARG, "top": _TOP_ARG,
                                      "level": {"type": "integer", "minimum": 0, "maximum": 3}, "levelUp": {"type": "boolean"}, "setups": {"type": "integer", "minimum": 0}}), "handler": _tool(answer_list, False)},
    "scanner_top": {"scope": "all", "description": "Who stayed in the top of a board through the day, or how long one ticker did.",
                    "args": _schema({"day": _DAY_ARG, "side": _SIDE_ARG, "list": _LIST_ARG, "ticker": _TICKER_ARG, "top": _TOP_ARG}), "handler": _tool(answer_top, False)},
}
