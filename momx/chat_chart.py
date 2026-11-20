"""The Ask chat's chart tools (2026-11-11): what the chart shows, without touching the chart.

chart_levels and chart_signals answer from a chart tape that is already held:
the copy in the app's memory if there is one, otherwise the saved file in
oi_chart_cache. A read never adds to any cache and never asks for a build, a
refresh or a Schwab call. The numbers come from momx/chart_levels.py, a port of
the chart's own formulas, run in a child process (it measured 1-6 s of CPU on a
busy PC, far too long to share api_server's interpreter lock with the live app).

The child runs at normal priority: it is its own process, so it never holds the
app's interpreter lock, and below normal it starved behind the desktop apps
(SPY 5m: 9.6 s normal, 29.3 s below normal, 2026-11-11). A question waits for it
CHILD_TIMEOUT_SECONDS; if it is not done by then the question says "busy" and the
child carries on, so asking again a moment later reads the finished answer. Only
that ticker and timeframe rest after a child that ran out of its full time.
A tape held in memory is turned into the child's input in small pieces, so the
app's other threads get the interpreter between them (one dumps of a 30k-bar
tape held it for up to 1.4 s).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from momx import chart_levels, chat_state

ET = ZoneInfo("America/New_York")
CHART_VIEW = "Charts & OI"
DEFAULT_TIMEFRAME = "5m"
#: The app itself ignores a saved chart older than this (OI_FINDER_CHART_DISK_CACHE_MAX_AGE_SECONDS).
DISK_MAX_AGE_SECONDS = 5 * 24 * 60 * 60
SCRIPT = Path(chart_levels.__file__)
#: How long a question waits for the child before saying "busy". The child itself may carry on.
CHILD_TIMEOUT_SECONDS = 20
CHILD_WAIT_SECONDS = 10
#: The most a child may run in all; past this it is stopped and that ticker and timeframe rest.
CHILD_HARD_SECONDS = 120
CHILD_REST_SECONDS = 30
#: Items of a list turned into text in one go: small enough that no single step holds the interpreter for long.
SLICE_ITEMS = 400
#: A second question inside this window reuses the last answer even if a new minute landed.
MEMO_FRESH_SECONDS = 20
MEMO_LIMIT = 64
#: Daily rows read from a memory tape for a past-session question (about a year and a half of sessions).
DAILY_ROWS = 400
CREATE_NO_WINDOW = 0x08000000

_SYMBOL = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")    # the app's own rule for a saved chart's file name
_SCREEN_WORDS = re.compile(r"\b(?:chart|this|here)\b", re.I)
_TIMEFRAME_ALIASES = {
    "3": "3m", "5": "5m", "10": "10m", "15": "15m", "30": "30m", "60": "1h", "120": "2h", "240": "4h",
    "1440": "D", "10080": "W", "43200": "M", "1H": "1h", "2H": "2h", "4H": "4h", "d": "D", "w": "W", "m": "M",
    "day": "D", "daily": "D", "week": "W", "weekly": "W", "month": "M", "monthly": "M",
}
_QUESTION_TIMEFRAME = re.compile(r"\b(?:(3|5|10|15|30)\s?m(?:in(?:ute)?s?)?|(1|2|4)\s?h(?:(?:ou)?rs?)?|(daily|weekly|monthly))\b", re.I)
_LABEL_TIMEFRAME = re.compile(r"^(?:CALL|PUT|C|P)(5|15|30|1H|2H|4H|D|2D|3D|4D|W|M)$")
_QUESTION_LABEL = re.compile(r"\b(?:CALL|PUT|C|P)(?:5|15|30|1H|2H|4H|D|2D|3D|4D|W|M)\b", re.I)
_FOCUS_WORDS = (("vwap", r"\bvwap\b"), ("pivots", r"\bpivots?\b"), ("ema", r"\bemas?\b"), ("sma", r"\bsma\b"),
                ("premarket", r"\b(?:pre ?market|pmh|pml)\b"), ("previous", r"\b(?:previous|prior|yesterday'?s?)\b"))
_FAMILY_WORD = {"4x8": "4x8", "9x20": "9x20", "ganesh48": "4x8", "ganesh920": "9x20", "ganeshMacd": "MACD"}
_TAPE_KEYS = ("bars", "dailyBars", "studyBars", "fineStudyBars", "mtfSignals", "mtfSignalsByTimeframe", "mtfSignalsPending",
              "ganeshHigherTimeframeSignals", "studiesPending", "historyLoading")

_slot = threading.Lock()                  # one chart computation at a time, app-wide
_memo: OrderedDict[tuple, tuple[float, tuple, dict]] = OrderedDict()
_memo_lock = threading.Lock()
#: (symbol, timeframe) -> monotonic time until which that one tape is not tried again.
_rest: dict = {}
#: (symbol, timeframe) -> the computation running for it, so a second question waits on it rather than starting another.
_jobs: dict = {}
_jobs_lock = threading.Lock()


class Busy(Exception):
    """The child could not answer in time. The reply says so; nothing is retried."""


def reset() -> None:
    """Forget saved answers (tests)."""
    with _memo_lock:
        _memo.clear()
    _rest.clear()
    _session_memo.clear()
    _window_memo.clear()


# ---------------------------------------------------------------- what was asked
def _clean_ticker(value: Any) -> str | None:
    text = str(value or "").strip().lstrip("$").upper()
    return text if _SYMBOL.fullmatch(text) else None


def resolve_ticker(args: Mapping, ctx: Mapping) -> str | None:
    """Which ticker this means. A question about "the chart", "this" or "here" is the one on screen,
    whatever the conversation was about; with no question to read, these are chart tools, so the
    screen comes first too."""
    explicit, page, spoken = _clean_ticker(args.get("ticker")), _clean_ticker(ctx.get("pageTicker")), _clean_ticker(ctx.get("ticker"))
    question = str(args.get("question") or "")
    about_screen = bool(_SCREEN_WORDS.search(question))
    if explicit:
        typed = bool(re.search(rf"(?<![A-Za-z0-9]){re.escape(explicit)}(?![A-Za-z0-9])", question, re.I))
        if about_screen and page and explicit != page and explicit == spoken and not typed:
            return page
        return explicit
    if page and (about_screen or not question):
        return page
    return spoken or page


def resolve_timeframe(args: Mapping, label: str | None = None) -> str:
    raw = str(args.get("timeframe") or "").strip()
    if raw in chart_levels.TIMEFRAMES:
        return raw
    if _TIMEFRAME_ALIASES.get(raw):
        return _TIMEFRAME_ALIASES[raw]
    match = _LABEL_TIMEFRAME.match(label or "")
    if match:
        suffix = match.group(1)
        return {"5": "5m", "15": "15m", "30": "30m", "1H": "1h", "2H": "2h", "4H": "4h", "W": "W", "M": "M"}.get(suffix, "D")
    found = _QUESTION_TIMEFRAME.search(str(args.get("question") or ""))
    if found:
        if found.group(1):
            return f"{found.group(1)}m"
        if found.group(2):
            return f"{found.group(2)}h"
        return _TIMEFRAME_ALIASES[found.group(3).lower()]
    return DEFAULT_TIMEFRAME


def _label(args: Mapping) -> str | None:
    raw = str(args.get("label") or "").strip().upper()
    if raw:
        return raw
    found = _QUESTION_LABEL.search(str(args.get("question") or ""))
    return found.group(0).upper() if found else None


def _focus(args: Mapping) -> str | None:
    raw = str(args.get("focus") or "").strip().lower()
    if raw:
        return raw
    question = str(args.get("question") or "").lower()
    return next((name for name, pattern in _FOCUS_WORDS if re.search(pattern, question)), None)


# ---------------------------------------------------------------- reading a held tape
def _memory_tape(symbol: str) -> dict | None:
    """The app's in-memory chart payload, peeked at. Never added to, and a busy lock just means 'use the file'."""
    state = chat_state.state()
    held = getattr(state, "oi_finder_chart_cache", None)
    if not isinstance(held, dict):
        return None
    lock = getattr(state, "oi_finder_chart_lock", None)
    if lock is not None and not lock.acquire(timeout=0.25):
        return None
    try:
        entry = held.get(symbol)
        payload = entry.get("payload") if isinstance(entry, dict) else None
        ready = entry.get("history_ready") if isinstance(entry, dict) else None
    finally:
        if lock is not None:
            lock.release()
    if not isinstance(payload, dict) or not payload.get("bars"):
        return None
    if ready is False and not payload.get("dailyBars"):
        return None    # a bare fast-start tape; the saved file has the depth
    return payload


def _disk_folder(ctx: Mapping) -> Path | None:
    folder = getattr(chat_state.state(), "oi_finder_chart_disk_cache_dir", None)
    if folder:
        return Path(folder)
    artifacts = ctx.get("artifacts")
    return Path(artifacts) / "oi_chart_cache" if artifacts else None


def _disk_file(symbol: str, ctx: Mapping) -> tuple[Path, os.stat_result] | None:
    folder = _disk_folder(ctx)
    if folder is None:
        return None
    path = folder / f"{symbol}.json.gz"
    try:
        info = path.stat()
    except OSError:
        return None
    return (path, info) if info.st_size and time.time() - info.st_mtime <= DISK_MAX_AGE_SECONDS else None


def _signature(payload: dict) -> tuple:
    """Changes when the tape does, including a last bar restated in place."""
    bars = payload.get("bars") or []
    last = bars[-1] if bars and isinstance(bars[-1], dict) else {}
    return ("memory", len(bars), last.get("time"), last.get("close"), last.get("high"), last.get("low"), last.get("volume"),
            len(payload.get("studyBars") or ()), len(payload.get("fineStudyBars") or ()), len(payload.get("dailyBars") or ()),
            len(payload.get("mtfSignals") or ()), payload.get("updatedAt"), bool(payload.get("mtfSignalsPending")), bool(payload.get("studiesPending")))


def _child_flags() -> int:
    """No console window. Normal priority: the child shares no interpreter lock with the app."""
    return CREATE_NO_WINDOW if os.name == "nt" else 0


def _pieces(value: Any, out: list, depth: int = 0) -> None:
    """JSON text for `value`, appended in small pieces so no single dumps holds the interpreter
    for long; the interpreter's own switch interval hands other threads a turn between pieces.
    No zero-length sleep here: on CPython 3.12 it drops and re-takes the lock at once, which resets
    a waiting thread's switch request and starved the serving threads for 100-300 ms. Lists and dicts are copied first (one quick C call)
    because the live app may add a bar while this runs."""
    if isinstance(value, list) and depth < 4:
        items = value[:]
        if all(not isinstance(item, (list, dict)) or len(item) <= SLICE_ITEMS for item in items[:3]):
            out.append("[")
            for start in range(0, len(items), SLICE_ITEMS):
                if start:
                    out.append(",")
                out.append(json.dumps(items[start:start + SLICE_ITEMS], separators=(",", ":"))[1:-1])
            out.append("]")
            return
        out.append("[")
        for index, item in enumerate(items):
            if index:
                out.append(",")
            _pieces(item, out, depth + 1)
        out.append("]")
        return
    if isinstance(value, dict) and depth < 4:
        out.append("{")
        for index, (key, item) in enumerate(list(value.items())):
            if index:
                out.append(",")
            out.append(json.dumps(str(key)) + ":")
            _pieces(item, out, depth + 1)
        out.append("}")
        return
    out.append(json.dumps(value, separators=(",", ":")))


def _tape_bytes(payload: dict, timeframe: str) -> bytes:
    """Only the tapes this timeframe reads, in small pieces so other threads get a turn between them."""
    minutes = chart_levels.TIMEFRAMES.get(timeframe, 1)      # "ema:5m,...,M" reads every tape
    skip: set = set()
    if timeframe == "sessions" or timeframe.startswith("window:"):   # session closes and windows read the intraday tapes only
        skip = set(_TAPE_KEYS) - {"bars", "fineStudyBars"}
    elif minutes >= 1440 and payload.get("dailyBars"):
        skip = {"studyBars", "fineStudyBars"}
    elif minutes >= 240 and payload.get("studyBars"):
        skip = {"fineStudyBars"}
    out: list = ["{"]
    first = True
    for key in _TAPE_KEYS:
        if key in payload and key not in skip:
            if not first:
                out.append(",")
            first = False
            out.append(json.dumps(key) + ":")
            _pieces(payload[key], out)
    out.append("}")
    return "".join(out).encode("utf-8")


def _run_child(timeframe: str, source: str, stdin: bytes | None) -> dict | None:
    """Run the chart maths in its own process. Waiting on it costs this process nothing."""
    try:
        child = subprocess.Popen([sys.executable, "-I", str(SCRIPT), timeframe, source],
                                 stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                                 stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, creationflags=_child_flags())
    except OSError as exc:
        raise Busy(type(exc).__name__) from None
    try:
        out, _ = child.communicate(stdin, timeout=max(CHILD_HARD_SECONDS, CHILD_TIMEOUT_SECONDS))
    except subprocess.TimeoutExpired:
        child.kill()
        child.communicate()
        raise Busy("out of time") from None
    if child.returncode != 0:
        raise Busy(f"child exited {child.returncode}")
    try:
        return json.loads(out)
    except ValueError:
        raise Busy("unreadable answer") from None


def _memo_get(key: tuple, signature: tuple | None) -> dict | None:
    """The saved answer if the tape has not changed, or if it was worked out moments ago."""
    with _memo_lock:
        held = _memo.get(key)
        if held is None:
            return None
        computed_at, held_signature, result = held
        # "Moments ago" counts only for the same source: never another saved file's answer (2026-11-14: a test's
        # scratch chart answered for another folder's file of the same ticker).
        same_source = bool(signature) and bool(held_signature) and held_signature[0] == signature[0] and (
            signature[0] != "disk" or held_signature[1] == signature[1])
        if held_signature == signature or (same_source and time.monotonic() - computed_at < MEMO_FRESH_SECONDS):
            _memo.move_to_end(key)
            return result
    return None


def _memo_put(key: tuple, signature: tuple, result: dict) -> None:
    with _memo_lock:
        _memo[key] = (time.monotonic(), signature, result)
        _memo.move_to_end(key)
        while len(_memo) > MEMO_LIMIT:
            _memo.popitem(last=False)


class _Job:
    """One computation for one tape and timeframe, which outlives the question that started it."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.result = None
        self.error = None


def _work(job: _Job, key: tuple, signature: tuple, payload, disk) -> None:
    symbol, timeframe = key
    try:
        if not _slot.acquire(timeout=CHILD_WAIT_SECONDS):
            job.error = "no turn came"
            return
        try:
            found = _memo_get(key, signature)     # the question ahead in line may have just worked it out
            if found is not None:
                job.result = found
                return
            if payload is not None:
                result = _run_child(timeframe, "-", _tape_bytes(payload, timeframe))
            else:
                result = _run_child(timeframe, str(disk[0]), None)
        finally:
            _slot.release()
        if result is not None:
            _memo_put(key, signature, result)
        job.result = result
    except Busy as exc:
        job.error = str(exc) or "busy"
        if str(exc) == "out of time":
            _rest[key] = time.monotonic() + CHILD_REST_SECONDS
    except Exception as exc:  # noqa: BLE001 - the question gets "busy", never an error
        job.error = type(exc).__name__
    finally:
        with _jobs_lock:
            if _jobs.get(key) is job:
                del _jobs[key]
        job.done.set()


def compute(symbol: str, timeframe: str, ctx: Mapping, wait: float | None = None) -> dict | None:
    """chart_levels.analyze for one held tape, or None if the app holds no chart for this ticker.
    Levels and signals share one computation per tape and timeframe. Raises Busy when the answer
    is not ready in `wait` seconds (default CHILD_TIMEOUT_SECONDS); the computation carries on and the next question reads it."""
    payload = _memory_tape(symbol)
    disk = None if payload is not None else _disk_file(symbol, ctx)
    if payload is None and disk is None:
        return None
    signature = _signature(payload) if payload is not None else ("disk", str(disk[0]), disk[1].st_mtime_ns, disk[1].st_size)
    key = (symbol, timeframe)
    found = _memo_get(key, signature)
    if found is not None:
        return found
    if time.monotonic() < _rest.get(key, 0.0):
        raise Busy("resting")
    with _jobs_lock:
        job = _jobs.get(key)
        start = job is None
        if start:
            job = _jobs[key] = _Job()
    if start:
        threading.Thread(target=_work, args=(job, key, signature, payload, disk), name=f"chat-chart-{symbol}-{timeframe}", daemon=True).start()
    if not job.done.wait(max(CHILD_TIMEOUT_SECONDS if wait is None else wait, 0.0)):
        raise Busy("still working")
    if job.error:
        raise Busy(job.error)
    return job.result


def peek(symbol: str, timeframe: str, ctx: Mapping) -> dict | None:
    """A finished answer for this tape and timeframe if one is held now; never starts a child. For the fast part of a report."""
    payload = _memory_tape(symbol)
    disk = None if payload is not None else _disk_file(symbol, ctx)
    if payload is None and disk is None:
        return None
    signature = _signature(payload) if payload is not None else ("disk", str(disk[0]), disk[1].st_mtime_ns, disk[1].st_size)
    return _memo_get((symbol, timeframe), signature)


def daily_bars(symbol: str, ctx: Mapping) -> list[dict] | None:
    """The saved daily candles of a chart the app already holds ({time, open, high, low, close, volume}, oldest first),
    for a question about a past session. The memory tape's own daily rows when it has them (a plain list walk,
    a few ms), else the 60 daily bars of the EMA report a child works out from the saved file (compute_ema, shared
    with the report). None when the app holds no chart; raises Busy when the child is not done yet."""
    payload = _memory_tape(symbol)
    held = payload.get("dailyBars") if payload is not None else None
    if isinstance(held, list) and held:
        # The newest DAILY_ROWS only: all 5,000 rows of 20 years took ~11 ms of the interpreter (SPY, 2026-11-11).
        rows = chart_levels.dedupe_daily(chart_levels.normalize(held[-DAILY_ROWS:]))
        if rows:
            return rows
    report = compute_ema(symbol, chart_levels.EMA_TIMEFRAMES, ctx)
    return list(report.get("daily") or []) if isinstance(report, Mapping) else None


def session_closes(symbol: str, ctx: Mapping) -> dict | None:
    """Each saved weekday's regular-session close (last bar before 16:00 ET) from the chart's intraday tapes, worked
    out in the child like every other chart answer: {"sessions": [...], "oldest"} (chart_levels.session_closes).
    None when the app holds no chart; raises Busy when the child is not done yet."""
    found = compute(symbol, "sessions", ctx)
    return found if isinstance(found, Mapping) else None


#: The newest minute bars of a memory tape the extended-hours read walks: four days of 1-minute bars with overnight
#: prints is about 5,800; Thursday's close to Monday's premarket is about 3,000.
SESSION_TAIL_BARS = 6000
_session_memo: dict[str, tuple] = {}


def session_tape(symbol: str, ctx: Mapping, wait: float = 0.0, start: bool = True) -> dict | None:
    """Regular-session closes and the last print of each extended-hours window (chart_levels.session_closes, with its
    "extended" list), for the why-answer's session split (2026-11-11: "why nvda down after hours" answered with the
    day's +2.87%). Read only from what the app holds: a memory tape's newest SESSION_TAIL_BARS minute bars, walked
    here (a few ms, kept until the tape changes), else the saved chart file through the child (the same answer
    session_closes uses), waiting at most `wait` seconds; the child carries on and the next question reads it.
    start=False: only what is already worked out (no child is started).
    None when there is nothing held or it is not ready. Never a chart build, never a Schwab call."""
    payload = _memory_tape(symbol)
    if payload is not None:
        signature = _signature(payload)
        held = _session_memo.get(symbol)
        if held and held[0] == signature:
            return held[1]
        tail = list((payload.get("bars") or [])[-SESSION_TAIL_BARS:])
        found = chart_levels.session_closes({"bars": tail})
        if len(_session_memo) > MEMO_LIMIT:
            _session_memo.clear()
        _session_memo[symbol] = (signature, found)
        return found
    found = peek(symbol, "sessions", ctx)
    if isinstance(found, Mapping) and "extended" in found:
        return dict(found)
    if not start or _disk_file(symbol, ctx) is None:
        return None
    try:
        found = compute(symbol, "sessions", ctx, wait=wait)
    except Busy:
        return None
    return dict(found) if isinstance(found, Mapping) and "extended" in found else None


_window_memo: dict[tuple, tuple] = {}


def window_move(symbol: str, ctx: Mapping, day: str, start_min: int, end_min: int, wait: float = 0.0,
                start: bool = True) -> dict | None:
    """One symbol's move inside part of one regular session (chart_levels.window_move: the open, the price at the
    window's end against the previous 16:00 close and the open, the window's low and high with their times), for the
    why-answer's "this morning" / "this afternoon" / "today so far" (2026-11-14). Read only from what the app holds,
    exactly as session_tape: a memory tape's newest SESSION_TAIL_BARS minute bars walked here (kept until the tape
    changes), else the saved chart file through the child, waiting at most `wait` seconds; the child carries on and the
    next question reads it. start=False: only what is already worked out. None when nothing is held or it is not
    ready; {"busy": True} when the child is still reading it. Never a chart build, never a Schwab call."""
    asked = f"window:{day}:{int(start_min)}:{int(end_min)}"
    memory = _memory_window(symbol, day, start_min, end_min)
    if memory is not None:
        if _window_whole(memory) or _disk_file(symbol, ctx) is None:
            return memory
        # A day older than the memory tape's tail (a past day's question, a reference day after a long weekend): the
        # saved chart file, read in the child with every bar and the 5-minute ones (2026-11-14 review). The memory tape
        # itself is never sent whole (its full history is far too big to pass through this process).
        return window_moves([symbol], ctx, day, start_min, end_min, wait=wait, start=start).get(symbol)
    found = peek(symbol, asked, ctx)
    if isinstance(found, Mapping):
        return dict(found)
    if not start or _disk_file(symbol, ctx) is None:
        return None
    try:
        found = compute(symbol, asked, ctx, wait=wait)
    except Busy:
        return {"day": day, "start": int(start_min), "end": int(end_min), "busy": True}
    return dict(found) if isinstance(found, Mapping) else None


def _window_whole(found: Mapping | None) -> bool:
    """Did a memory tape's tail answer the window fully: bars in it and the previous close?"""
    return isinstance(found, Mapping) and not found.get("missing") and found.get("prevClose") is not None


def _better_window(memory: Mapping | None, disk: Mapping | None) -> dict | None:
    """The saved file's answer when it holds the window, else the memory tail's (a partial answer beats none)."""
    if isinstance(disk, Mapping) and not disk.get("missing") and not disk.get("busy"):
        if _window_whole(disk) or not isinstance(memory, Mapping) or memory.get("missing"):
            return dict(disk)
    if isinstance(memory, Mapping):
        return dict(memory)
    return dict(disk) if isinstance(disk, Mapping) else None


def _memory_window(symbol: str, day: str, start_min: int, end_min: int) -> dict | None:
    """window_move from a memory tape's newest SESSION_TAIL_BARS minute bars (kept until the tape changes), or None
    when the app holds no memory tape for the symbol."""
    payload = _memory_tape(symbol)
    if payload is None:
        return None
    asked = f"window:{day}:{int(start_min)}:{int(end_min)}"
    signature = _signature(payload)
    key = (symbol, asked)
    held = _window_memo.get(key)
    if held and held[0] == signature:
        return held[1]
    tail = list((payload.get("bars") or [])[-SESSION_TAIL_BARS:])
    found = chart_levels.window_move({"bars": tail}, day, start_min, end_min)
    if len(_window_memo) > MEMO_LIMIT * 4:
        _window_memo.clear()
    _window_memo[key] = (signature, found)
    return found


_batches: dict = {}
#: Children one window's batch of saved charts is split over.
WINDOW_CHILDREN = 3
#: The window batches take turns among themselves, not with the chart slot: the drivers of one why-answer (eleven saved
#: charts) must never hold up another question's own chart or session read.
_window_slot = threading.Lock()


def _window_batch(job: _Job, asked: str, todo: list, key: tuple) -> None:
    """One child for every saved chart file a window still needs ([(symbol, path, signature)]), so eleven drivers cost
    one process start, not eleven. Each answer is saved like any other (symbol, timeframe) answer."""
    try:
        if not _window_slot.acquire(timeout=CHILD_WAIT_SECONDS):
            job.error = "no turn came"
            return
        try:
            paths = [str(path) for _, path, _ in todo]
            # Up to WINDOW_CHILDREN children side by side (separate processes, no shared interpreter lock): reading a
            # saved chart is mostly its JSON (about 0.3 s each), and eleven drivers one after another took ~5 s.
            groups = [paths[i::WINDOW_CHILDREN] for i in range(min(WINDOW_CHILDREN, len(paths)))]
            answers: list = [None] * len(groups)

            def run(index: int, group: list) -> None:
                try:
                    answers[index] = _run_child(asked, "|".join(group), None) if len(group) > 1 else {group[0]: _run_child(asked, group[0], None)}
                except Busy:
                    answers[index] = None
            threads = [threading.Thread(target=run, args=(i, g), daemon=True) for i, g in enumerate(groups)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            raw = {k: v for part in answers if isinstance(part, Mapping) for k, v in part.items()}
        finally:
            _window_slot.release()
        found = {}
        for symbol, path, signature in todo:
            answer = raw.get(str(path)) if isinstance(raw, Mapping) else None
            if isinstance(answer, Mapping):
                _memo_put((symbol, asked), signature, dict(answer))
                found[symbol] = dict(answer)
        job.result = found
    except Busy as exc:
        job.error = str(exc) or "busy"
    except Exception as exc:  # noqa: BLE001 - the drivers are "not read", never an error
        job.error = type(exc).__name__
    finally:
        with _jobs_lock:
            _batches.pop(key, None)
        job.done.set()


def window_moves(symbols: list | tuple, ctx: Mapping, day: str, start_min: int, end_min: int, wait: float = 0.0,
                 start: bool = True) -> dict:
    """window_move for several symbols at once: memory tapes walked here, finished answers reused, and every saved chart
    file still needed read in ONE child (waiting at most `wait`; it carries on, and the next question reads it).
    {symbol: answer | {"busy": True} | None}."""
    asked = f"window:{day}:{int(start_min)}:{int(end_min)}"
    out: dict = {}
    todo = []
    memory: dict = {}
    for symbol in symbols:
        if symbol in out:
            continue
        found = _memory_window(symbol, day, start_min, end_min)
        disk = _disk_file(symbol, ctx)
        if found is not None:
            if _window_whole(found) or disk is None:
                out[symbol] = found
                continue
            memory[symbol] = found                        # the tail misses the day or its previous close: the file too
        if disk is None:
            out[symbol] = None
            continue
        signature = ("disk", str(disk[0]), disk[1].st_mtime_ns, disk[1].st_size)
        held = _memo_get((symbol, asked), signature)
        if held is not None:
            out[symbol] = _better_window(memory.get(symbol), dict(held))
            continue
        out[symbol] = memory.get(symbol) if memory.get(symbol) and not memory[symbol].get("missing") else             {"day": day, "start": int(start_min), "end": int(end_min), "busy": True}
        todo.append((symbol, disk[0], signature))
    if not todo or not start:
        return out
    key = (asked, tuple(symbol for symbol, _, _ in todo))
    with _jobs_lock:
        job = _batches.get(key)
        fresh = job is None
        if fresh:
            job = _batches[key] = _Job()
    if fresh:
        threading.Thread(target=_window_batch, args=(job, asked, todo, key), name="chat-chart-windows", daemon=True).start()
    if job.done.wait(max(wait, 0.0)) and isinstance(job.result, Mapping):
        out.update({symbol: _better_window(memory.get(symbol), dict(found)) for symbol, found in job.result.items() if symbol in out})
    return out


def latest_bar(symbol: str) -> tuple | None:
    """(epoch seconds, close) of the newest candle of a chart tape the app already holds, or None.
    An O(1) peek at the memory tape, else a finished intraday answer saved above. Reads no file."""
    payload = _memory_tape(symbol)
    if payload is not None:
        bar = (payload.get("bars") or [None])[-1]
        try:
            stamp, close = int(bar.get("time")), float(bar.get("close"))
        except (AttributeError, TypeError, ValueError):
            stamp, close = 0, 0.0
        if stamp > 0 and close > 0:
            return stamp, close
    best = None
    with _memo_lock:
        held = [(key, value[2]) for key, value in _memo.items() if key[0] == symbol]
    for (_, timeframe), result in held:
        if chart_levels.TIMEFRAMES.get(timeframe, 1440) >= 1440 or not isinstance(result, dict):
            continue
        levels, tape = result.get("levels") or {}, result.get("tape") or {}
        try:
            stamp, close = int(tape.get("newestData") or levels.get("lastTime")), float(levels.get("lastClose"))
        except (TypeError, ValueError):
            continue
        if stamp > 0 and close > 0 and (best is None or stamp > best[0]):
            best = (stamp, close)
    return best


# ---------------------------------------------------------------- saying it
def _link(symbol: str | None, timeframe: str | None = None) -> dict:
    if not symbol:
        return {"label": "Open Charts & OI", "view": CHART_VIEW}
    return {"label": f"Open {symbol} chart, {timeframe}", "view": CHART_VIEW, "ticker": symbol, "timeframe": timeframe}


def _plain(line: str, *links: dict) -> dict:
    return {"lines": [line], "facts": {}, "links": list(links), "table": None, "asOf": None}


def _money(value: Any) -> str:
    return f"{value:.2f}" if isinstance(value, (int, float)) else "n/a"


def _now(ctx: Mapping) -> datetime:
    now = ctx.get("now")
    if isinstance(now, datetime):
        return now.astimezone(ET) if now.tzinfo else now.replace(tzinfo=ET)
    return datetime.now(ET)


def _as_of(stamp: int, ctx: Mapping) -> str:
    moment = datetime.fromtimestamp(stamp, ET)
    if moment.date() == _now(ctx).date():
        return f"as of {moment:%H:%M} ET"
    return f"as of {moment:%b} {moment.day} {moment:%H:%M} ET"


def _candle_words(stamp: int, timeframe: str) -> str:
    moment = datetime.fromtimestamp(stamp, ET)
    if timeframe == "D":
        return f"{moment:%a %b} {moment.day}"
    if timeframe == "W":
        return f"week of {moment:%b} {moment.day}"
    if timeframe == "M":
        return f"{moment:%b %Y}"
    return f"{moment:%a %b} {moment.day} {moment:%H:%M} ET"


def _prepare(args: Mapping, ctx: Mapping, what: str, label: str | None = None) -> tuple[Any, ...]:
    """(symbol, timeframe, result, None), or (None, None, None, reply) when there is nothing to work out."""
    if ctx.get("scope") not in ("owner", "scanner"):
        return None, None, None, _plain("I cannot look that up here.")
    symbol = resolve_ticker(args, ctx)
    if not symbol:
        return None, None, None, _plain(f"Which ticker do you want {what} for? Type one, like NVDA.", _link(None))
    timeframe = resolve_timeframe(args, label)
    try:
        result = compute(symbol, timeframe, ctx)
    except Busy:
        return None, None, None, _plain(f"The chart numbers for {symbol} are busy right now. Try again in a moment, or open the chart.", _link(symbol, timeframe))
    if not result:
        return None, None, None, _plain(f"I don't have a saved chart for {symbol} yet. Open it on Charts & OI and it will load there.", _link(symbol, timeframe))
    return symbol, timeframe, result, None


def _levels_table(symbol: str, timeframe: str, levels: dict) -> dict:
    session, previous, pivots = levels.get("session") or {}, levels.get("previous") or {}, levels.get("pivots") or {}
    mtf = {row["key"]: row["value"] for row in levels.get("mtfMa") or []}
    rows: list[dict] = []

    def add(level: str, chart: Any = None, day: Any = None, week: Any = None, month: Any = None) -> None:
        cells = {"chart": chart, "day": day, "week": week, "month": month}
        if any(isinstance(value, (int, float)) for value in cells.values()):
            rows.append({"level": level, **{key: _money(value) if isinstance(value, (int, float)) else "" for key, value in cells.items()}})

    def averaged(key: str) -> tuple:
        return tuple(mtf.get(f"{period}-{key}") for period in chart_levels.PERIODS)

    add("EMA 9", levels.get("ema9"), *averaged("ema1"))
    add("EMA 21", levels.get("ema21"), *averaged("ema2"))
    add("EMA 50", levels.get("ema50"), *averaged("ema3"))
    add("SMA 200", levels.get("sma200"), *averaged("sma4"))
    add("SMA 50", None, *averaged("sma3"))
    add("EMA 200", None, *averaged("ema4"))
    add("Premarket high", (session.get("pm") or {}).get("high"))
    add("Premarket low", (session.get("pm") or {}).get("low"))
    add("High so far", None, (session.get("day") or {}).get("high"), (session.get("week") or {}).get("high"))
    add("Low so far", None, (session.get("day") or {}).get("low"), (session.get("week") or {}).get("low"))
    add("Previous high", None, *[(previous.get(period) or {}).get("high") for period in chart_levels.PERIODS])
    add("Previous low", None, *[(previous.get(period) or {}).get("low") for period in chart_levels.PERIODS])
    for level in chart_levels.PIVOT_LEVELS:
        add(f"Pivot {level}", None, *[(pivots.get(period) or {}).get(level) for period in chart_levels.PERIODS])
    for level in chart_levels.PIVOT_LEVELS:
        add(f"Woodies {level}", (levels.get("woodies") or {}).get(level))
    add("All-time high", session.get("allTimeHigh"))
    return {"kind": "rows", "title": f"{symbol} chart levels, {timeframe}",
            "columns": [{"key": "level", "label": "Level"}, {"key": "chart", "label": "This chart", "align": "right"},
                        {"key": "day", "label": "Day", "align": "right"}, {"key": "week", "label": "Week", "align": "right"},
                        {"key": "month", "label": "Month", "align": "right"}],
            "rows": rows, "note": "The chart's default settings: EMA 9/21/50, SMA 200. A saved chart profile can differ.",
            "link": _link(symbol, timeframe)}


def _level_lines(levels: dict, focus: str | None) -> list[str]:
    session, previous, pivots = levels.get("session") or {}, levels.get("previous") or {}, levels.get("pivots") or {}
    pm, day, week = session.get("pm"), session.get("day"), session.get("week")
    prior_day, day_pivots = previous.get("DAY"), pivots.get("DAY")
    averages = (f"EMA 9 {_money(levels.get('ema9'))}, EMA 21 {_money(levels.get('ema21'))}, EMA 50 {_money(levels.get('ema50'))}, "
                f"SMA 200 {_money(levels.get('sma200'))} (chart defaults 9/21/50 and SMA 200).")
    today = " ".join(part for part in (
        f"Premarket high {_money(pm['high'])}, low {_money(pm['low'])}." if pm else "",
        f"Today's high {_money(day['high'])}, low {_money(day['low'])}." if day else "",
        f"This week's high {_money(week['high'])}, low {_money(week['low'])}." if week else "") if part)
    prior = f"Previous day high {_money(prior_day['high'])}, low {_money(prior_day['low'])}." if prior_day else ""
    pivot = "; ".join(f"{name} pivots: R1 {_money(row['R1'])}, PP {_money(row['PP'])}, S1 {_money(row['S1'])}"
                      for name, row in (("Day", day_pivots), ("Week", pivots.get("WEEK")), ("Month", pivots.get("MONTH"))) if row)
    pivot = f"{pivot}." if pivot else ""
    chosen = {"ema": [averages], "sma": [averages], "premarket": [today], "previous": [prior], "pivots": [pivot]}.get(focus or "")
    return [line for line in (chosen or [averages, today, prior, pivot]) if line]


#: Support and resistance asked by name (2026-11-20, "Why chatbot not showing support and resistance?": the answer
#: listed every level but never said which were support and which resistance).
_SR_WORDS = re.compile(r"\bsupports?\b|\bresistances?\b|\bs\s*/\s*r\b|\bsr\b")
SR_PER_SIDE = 3
#: The chart's weekly and monthly averages ("200eWk", "50eMo"), in words.
_MA_NAMES = {"200eWk": "Weekly 200 EMA", "50eWk": "Weekly 50 EMA", "21eWk": "Weekly 21 EMA",
             "200eMo": "Monthly 200 EMA", "50eMo": "Monthly 50 EMA", "21eMo": "Monthly 21 EMA"}


def break_lines(symbol: str, levels: dict, price: float, side: str) -> list[str]:
    """"If NVDA breaks below 135" (2026-11-20, "If 135 broke below then" got the help list): the next levels the chart
    has beyond that price, nearest first, and where the stock is now. Levels to watch, never a forecast."""
    below, above = support_resistance(levels, around=price)
    close = levels.get("lastClose")
    level = _money(price)
    beyond = below if side == "below" else above
    words = "; ".join(f"{_money(value)} ({name})" for name, value in beyond)
    first = (f"If {symbol} falls below {level}, the next levels below it on the chart are: {words}." if side == "below"
             else f"If {symbol} rises above {level}, the next levels above it on the chart are: {words}.")
    if not beyond:
        first = f"The chart has no level {'below' if side == 'below' else 'above'} {level}."
    lines = [first]
    if isinstance(close, (int, float)) and close > 0:
        gap = (close - price) / close * 100
        where = "above" if close > price else "below" if close < price else "at"
        lines.append(f"{symbol} is at {_money(close)} now, {abs(gap):.1f}% {where} {level}." if where != "at" else f"{symbol} is at {level} now.")
    lines.append("These are price levels traders watch, not a forecast: a level can break or hold.")
    return lines


def support_resistance(levels: dict, around: float | None = None) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """(levels below `around`, nearest first; levels above it, nearest first), up to SR_PER_SIDE each, from the chart's
    own levels: SMA 200, premarket / today / this week / previous day, week and month high and low, day / week / month
    pivots and the weekly and monthly averages. `around` defaults to the close (support and resistance); a price the
    trader names ("if 135 breaks") asks what lies beyond that price instead."""
    close = levels.get("lastClose") if around is None else around
    if not isinstance(close, (int, float)) or close <= 0:
        return [], []
    session, previous, pivots = levels.get("session") or {}, levels.get("previous") or {}, levels.get("pivots") or {}
    found: list[tuple[str, float]] = []

    def add(name: str, value: Any) -> None:
        if isinstance(value, (int, float)) and value > 0:
            found.append((name, float(value)))

    # Price levels only, plus the 200 SMA: the 9/21/50 EMAs sit cents from the price on a 5m chart and would crowd out
    # the levels that matter (they are still listed in the lines below).
    add("SMA 200", levels.get("sma200"))
    for key, name in (("pm", "premarket"), ("day", "today's"), ("week", "this week's")):
        row = session.get(key) or {}
        add(f"{name} high".capitalize(), row.get("high"))
        add(f"{name} low".capitalize(), row.get("low"))
    for frame, word in (("DAY", "day"), ("WEEK", "week"), ("MONTH", "month")):
        prior = previous.get(frame) or {}
        add(f"Previous {word} high", prior.get("high"))
        add(f"Previous {word} low", prior.get("low"))
    for frame, word in (("DAY", "Day"), ("WEEK", "Week"), ("MONTH", "Month")):
        row = pivots.get(frame) or {}
        for key in ("R3", "R2", "R1", "PP", "S1", "S2", "S3"):
            add(f"{word} pivot {key}", row.get(key))
    for average in levels.get("mtfMa") or []:
        if isinstance(average, Mapping):
            add(_MA_NAMES.get(str(average.get("label") or ""), str(average.get("label") or "Average")), average.get("value"))
    seen: set[float] = set()
    below, above = [], []
    for name, value in sorted(found, key=lambda item: abs(item[1] - close)):
        key = round(value, 2)
        if key in seen or key == round(close, 2):
            continue
        seen.add(key)
        (below if value < close else above).append((name, value))
    return below[:SR_PER_SIDE], above[:SR_PER_SIDE]


def sr_lines(symbol: str, levels: dict) -> list[str]:
    """Plain support and resistance lines, a beginner can follow (what the words mean, then the nearest prices)."""
    below, above = support_resistance(levels)
    if not below and not above:
        return []
    close = _money(levels.get("lastClose"))
    words = lambda rows: "; ".join(f"{_money(value)} ({name})" for name, value in rows)
    lines = [f"Support = price levels BELOW {symbol}'s {close} that traders watch as a floor. "
             f"Resistance = price levels ABOVE it that traders watch as a ceiling. They are levels to watch, not a forecast."]
    lines.append(f"Nearest resistance above {close}: {words(above)}." if above else f"No resistance level above {close} on this chart.")
    lines.append(f"Nearest support below {close}: {words(below)}." if below else f"No support level below {close} on this chart.")
    return lines


#: The trader's rule (2026-11-11, "Don't use vwap"): the chat's analysis never uses VWAP; the chart still draws it.
VWAP_LINE = "VWAP is not used in the app's analysis; it is drawn on the chart."


def vwap_reply(args: Mapping, ctx: Mapping) -> dict:
    """A direct VWAP question: the one line, with a chart link. Nothing is computed."""
    symbol = resolve_ticker(args, ctx)
    return {"lines": [VWAP_LINE], "facts": {"symbol": symbol, "vwapNotUsed": True}, "links": [_link(symbol, resolve_timeframe(args))],
            "table": None, "asOf": None}


def chart_levels_tool(args: Mapping, ctx: Mapping) -> dict:
    if _focus(args) == "vwap":
        return vwap_reply(args, ctx)
    symbol, timeframe, result, reply = _prepare(args, ctx, "chart levels")
    if reply:
        return reply
    levels, tape = result["levels"], result.get("tape") or {}
    lines = [f"{symbol} chart levels, {timeframe}. Newest candle {_candle_words(levels['lastTime'], timeframe)}, close {_money(levels['lastClose'])}."]
    try:
        break_price = float(args.get("break_price")) if args.get("break_price") is not None else None
    except (TypeError, ValueError):
        break_price = None
    if break_price and break_price > 0 and args.get("break_side") in ("below", "above"):
        lines += break_lines(symbol, levels, break_price, str(args["break_side"]))
    elif _SR_WORDS.search(str(args.get("question") or "").lower()):
        lines += sr_lines(symbol, levels)
    lines += _level_lines(levels, _focus(args))
    if tape.get("historyLoading"):
        lines.append("The chart's history is still loading, so these can change.")
    facts = {"symbol": symbol, "timeframe": timeframe, "newestCandle": levels["lastTime"], "close": levels["lastClose"],
             "defaults": result.get("defaults"), "ema9": levels.get("ema9"), "ema21": levels.get("ema21"), "ema50": levels.get("ema50"),
             "sma200": levels.get("sma200"), "session": levels.get("session"),
             "previous": levels.get("previous"), "pivots": levels.get("pivots"), "woodies": levels.get("woodies"), "mtfMa": levels.get("mtfMa")}
    return {"lines": lines, "facts": facts, "links": [_link(symbol, timeframe)], "table": _levels_table(symbol, timeframe, levels),
            "asOf": _as_of(int(tape.get("newestData") or levels["lastTime"]), ctx)}


def _signal_when(stamp: int, ctx: Mapping) -> str:
    moment = datetime.fromtimestamp(stamp, ET)
    if moment.date() == _now(ctx).date():
        return f"{moment:%H:%M} ET"
    return f"{moment:%b} {moment.day} {moment:%H:%M} ET"


def chart_signals_tool(args: Mapping, ctx: Mapping) -> dict:
    label = _label(args)
    symbol, timeframe, result, reply = _prepare(args, ctx, "chart signals", label)
    if reply:
        return reply
    levels, signals, tape = result["levels"], result["signals"], result.get("tape") or {}
    pending_flags = signals.get("pending") or {}
    pending = bool(pending_flags.get("mtfSignalsPending") or pending_flags.get("studiesPending") or not pending_flags.get("historyReady"))
    rows = sorted([*(signals.get("mtf") or []), *(signals.get("higher") or [])], key=lambda row: row["sourceTime"], reverse=True)
    if label:
        rows = [row for row in rows if str(row.get("label") or "").upper() == label]
    what = label or "Signals"
    lines = [f"{symbol} chart signals, {timeframe}. Newest candle {_candle_words(levels['lastTime'], timeframe)}. "
             "Intraday signals are from the newest session, as the chart shows by default."]
    if pending:
        lines.append(f"{what} on this chart are pending: the chart is still working them out, so this list may be incomplete.")
    for row in rows[:6]:
        state = "forming, can still disappear" if row.get("forming") else "closed"
        lines.append(f"{_signal_when(row['sourceTime'], ctx)}: {row.get('label')} ({state}).")
    if not rows and not pending:
        lines.append(f"No {label} on the {timeframe} chart right now." if label else f"No signals on the {timeframe} chart right now.")
    table_rows = [{"time": _signal_when(row["sourceTime"], ctx), "signal": str(row.get("label") or ""), "side": row.get("direction"),
                   "frame": str(row.get("timeframe") or ""), "kind": _FAMILY_WORD.get(str(row.get("family") or ""), ""),
                   "state": "Forming" if row.get("forming") else "Closed"} for row in rows[:40]]
    table = {"kind": "rows", "title": f"{symbol} chart signals, {timeframe}",
             "columns": [{"key": "time", "label": "Time"}, {"key": "signal", "label": "Signal"}, {"key": "side", "label": "Side"},
                         {"key": "frame", "label": "Timeframe"}, {"key": "kind", "label": "Study"}, {"key": "state", "label": "State"}],
             "rows": table_rows, "link": _link(symbol, timeframe)} if table_rows else None
    facts = {"symbol": symbol, "timeframe": timeframe, "newestCandle": levels["lastTime"], "pending": pending, "label": label,
             "count": len(rows), "forming": sum(1 for row in rows if row.get("forming")),
             "signals": [{"time": row["sourceTime"], "label": row.get("label"), "direction": row.get("direction"), "forming": bool(row.get("forming"))} for row in rows[:40]]}
    return {"lines": lines, "facts": facts, "links": [_link(symbol, timeframe)], "table": table,
            "asOf": _as_of(int(tape.get("newestData") or levels["lastTime"]), ctx)}


EMA_BOARDS = ("21 EMA Touch Weekly", "50 EMA Touch Monthly")


def compute_ema(symbol: str, timeframes: tuple | list, ctx: Mapping) -> dict | None:
    """The 21/50 EMA detail on several timeframes in one child run (chart_levels.ema_report), memoized like compute()."""
    return compute(symbol, "ema:" + ",".join(timeframes), ctx)


def _pct(value: Any) -> str:
    return f"{value:+.2f}%" if isinstance(value, (int, float)) else "?"


def _cross_words(cross: Mapping | None, timeframe: str, ctx: Mapping, what: str) -> str:
    if not cross:
        return f"no {what} cross on the bars held"
    return f"{what} crossed {cross['dir']} {_when_words(int(cross['time']), timeframe, ctx)}"


def _when_words(stamp: int, timeframe: str, ctx: Mapping) -> str:
    return _candle_words(stamp, timeframe) if timeframe in ("D", "W", "M") else _signal_when(stamp, ctx)


def ema_lines(symbol: str, report: Mapping, ctx: Mapping) -> tuple[list[str], list[dict]]:
    """One plain line per timeframe, and the table rows. Values are the chart's, at the newest bar's time."""
    lines, rows = [], []
    for timeframe, row in (report.get("ema") or {}).items():
        e21, e50 = row["ema21"], row["ema50"]
        newest = _when_words(int(row["time"]), timeframe, ctx)
        lines.append(f"{timeframe} (newest bar {newest}): {_money(row['close'])} is {'above' if e21['above'] else 'below'} the 21 EMA "
                     f"{_money(e21['value'])} ({_pct(e21['pct'])}) and {'above' if e50['above'] else 'below'} the 50 EMA {_money(e50['value'])} "
                     f"({_pct(e50['pct'])}); 21 is {'above' if row['ema21Above50'] else 'below'} 50; "
                     f"{_cross_words(row.get('cross2150'), timeframe, ctx, '21/50')}; {_cross_words(row.get('priceCross21'), timeframe, ctx, 'price/21')}; "
                     f"21 {row.get('slope21') or 'n/a'}, 50 {row.get('slope50') or 'n/a'}.")
        cross = row.get("cross2150")
        rows.append({"tf": timeframe, "bar": newest, "close": _money(row["close"]), "e21": _money(e21["value"]), "p21": _pct(e21["pct"]),
                     "e50": _money(e50["value"]), "p50": _pct(e50["pct"]), "order": "21 > 50" if row["ema21Above50"] else "21 < 50",
                     "cross": f"{cross['dir']} {_when_words(int(cross['time']), timeframe, ctx)}" if cross else "-",
                     "slope": f"{row.get('slope21') or '-'} / {row.get('slope50') or '-'}"})
    return lines, rows


def ema_boards(symbol: str, ctx: Mapping) -> list[str]:
    """Is the ticker on the 21 EMA Touch Weekly or 50 EMA Touch Monthly board now, and since when."""
    from momx import chat_facts
    now = _now(ctx)
    out = []
    for name in EMA_BOARDS:
        for bear in (False, True):
            rows, ranked, at = chat_facts.board(ctx, name, bear)
            if not chat_facts.fresh(at, now) or symbol not in ranked:
                continue
            row = rows.get(symbol) or {}
            since = chat_facts.when(row.get("matchedSince") or row.get("firedAt"))
            out.append(f"On the {name} board ({'puts' if bear else 'calls'} side) now" + (f", since {chat_facts.clock(since, now)}" if since else "")
                       + f" (board {chat_facts.clock(at, now)}).")
    return out or [f"{symbol} is not on the 21 EMA Touch Weekly or 50 EMA Touch Monthly board now."]


def ema_tool(args: Mapping, ctx: Mapping) -> dict:
    """The 21 and 50 EMA on 5m, 15m, 1h, 4h, D, W and M (or the one timeframe asked), from the chart's own formula."""
    if ctx.get("scope") not in ("owner", "scanner"):
        return _plain("I cannot look that up here.")
    symbol = resolve_ticker(args, ctx)
    if not symbol:
        return _plain("Which ticker do you want the 21 and 50 EMA for? Type one, like NVDA.", _link(None))
    asked = args.get("timeframe") if args.get("timeframe") in chart_levels.TIMEFRAMES else None
    frames = (asked,) if asked else chart_levels.EMA_TIMEFRAMES
    try:
        report = compute_ema(symbol, frames, ctx)
    except Busy:
        return _plain(f"The chart numbers for {symbol} are being worked out right now. Ask again in a moment, or open the chart.", _link(symbol, asked))
    if not report or not report.get("ema"):
        return _plain(f"I don't have a saved chart for {symbol} yet. Open it on Charts & OI and it will load there.", _link(symbol, asked))
    lines, rows = ema_lines(symbol, report, ctx)
    lines.insert(0, f"{symbol} 21 and 50 EMA, the chart's own formula (default settings; a saved chart profile can differ):")
    lines += ema_boards(symbol, ctx)
    table = {"kind": "rows", "title": f"{symbol} 21 / 50 EMA",
             "columns": [{"key": "tf", "label": "TF"}, {"key": "bar", "label": "Newest bar"}, {"key": "close", "label": "Price", "align": "right"},
                         {"key": "e21", "label": "21 EMA", "align": "right"}, {"key": "p21", "label": "vs 21", "align": "right"},
                         {"key": "e50", "label": "50 EMA", "align": "right"}, {"key": "p50", "label": "vs 50", "align": "right"},
                         {"key": "order", "label": "21 vs 50"}, {"key": "cross", "label": "Last 21/50 cross"}, {"key": "slope", "label": "Slope 21 / 50"}],
             "rows": rows, "link": _link(symbol, asked or "D")}
    newest = int(report.get("newestData") or max(int(r["time"]) for r in report["ema"].values()))
    return {"lines": lines, "facts": {"symbol": symbol, "ema": report["ema"], "timeframes": list(report["ema"])},
            "links": [_link(symbol, asked or "D")], "table": table, "asOf": _as_of(newest, ctx)}


_ARGS_COMMON = {
    "ticker": {"type": "string", "description": "The ticker. Leave out to use the one on screen or in the conversation."},
    "timeframe": {"type": "string", "enum": list(chart_levels.TIMEFRAMES), "description": "Chart timeframe; 5m if not asked."},
    "question": {"type": "string", "maxLength": 300, "description": "The trader's question, word for word."},
}
TOOLS = {
    "chart_levels": {
        "scope": "all",
        "description": "Levels on the chart for a ticker and timeframe: EMA 9/21/50, SMA 200, premarket, day and week high/low, previous day/week/month, day/week/month pivots, Woodies pivots on Daily.",
        "args": {"type": "object", "properties": {**_ARGS_COMMON, "focus": {"type": "string", "enum": ["vwap", "ema", "sma", "premarket", "previous", "pivots"], "description": "One group of levels, if that is all that was asked."},
                 "break_price": {"type": "number", "minimum": 0.01, "maximum": 1000000, "description": "A price the trader asks about breaking ('if 135 breaks below')."},
                 "break_side": {"type": "string", "enum": ["below", "above"], "description": "Which way the price breaks."}}},
        "handler": chart_levels_tool,
    },
    "ema": {
        "scope": "all",
        "description": "The 21 and 50 EMA of a ticker on 5m, 15m, 1h, 4h, D, W and M (or one timeframe): values, distance from price, crosses, slope, EMA Touch boards.",
        "args": {"type": "object", "properties": {"ticker": _ARGS_COMMON["ticker"], "timeframe": _ARGS_COMMON["timeframe"]}},
        "handler": ema_tool,
    },
    "chart_signals": {
        "scope": "all",
        "description": "CALL and PUT signals drawn on the chart for a ticker and timeframe, saying which are forming or pending.",
        "args": {"type": "object", "properties": {**_ARGS_COMMON, "label": {"type": "string", "maxLength": 12, "pattern": "[A-Za-z0-9\\-]{1,12}", "description": "One signal name, like CALL4H."}}},
        "handler": chart_signals_tool,
    },
}
