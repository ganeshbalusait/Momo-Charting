"""Boils one saved scanner day file down to a small per-ticker index (2026-11-11).

A Watchlist day file is 100-180 MB and took 30-52 s to parse on an api_server thread, a process that
is already short of CPU. So momx/chat_scanner.py runs this file as a short-lived child process, below
normal priority, and keeps only what it prints:

    python -I chat_scanner_index.py <day file> <bull|bear>

Printed one JSON value per line: one array per ticker, then {"done": true, "broken": ...}. It is run
as a plain script with -I, so it must import nothing but the standard library (never `momx`: that
would load the app in every child). It only reads.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Mapping
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

#: The timeframes and colours his Level rule reads (frontend/src/scannerConfirmation.js, momxFilters.js).
RVOL_TFS = ("5m", "15m", "30m", "1h", "2h", "4h", "D")
SQZ_TFS = ("2h", "4h", "D", "Wk")
SKITTLES_TFS = ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")
RVOL_BG = {False: {"cyan", "green"}, True: {"magenta", "red"}}
RVOL_FG = {False: {"cyan", "green", "dark_green"}, True: {"magenta", "red", "dark_red"}}
SKITTLES_BG = {False: {"cyan", "green", "dark_green"}, True: {"magenta", "red", "plum"}}
SQZ_BG = {False: "cyan", True: "magenta"}


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def hl_sort_value(cell: Any) -> float | None:
    """The H/L cell's sort value as his board sorts it: the same rule as momx/option_track._hl_sort_value
    (a test holds the two together; this file cannot import the app)."""
    c = cell if isinstance(cell, Mapping) else {}
    raw = c.get("degree")
    if raw is None or raw == "":
        raw = c.get("value")
    return _num(raw) if raw is not None and raw != "" else None


def _stamp(v: Any) -> float | None:
    """Epoch seconds from an ISO stamp or an epoch number (seconds or milliseconds)."""
    n = _num(v) if not isinstance(v, str) else None
    if n is not None:
        return n / 1000 if n > 1e11 else n
    try:
        at = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (at if at.tzinfo else at.replace(tzinfo=ET)).timestamp()


def _cells(row: Mapping, field: str) -> Mapping:
    cells = row.get(field)
    return cells if isinstance(cells, Mapping) else {}


def _cell(cells: Mapping, tf: str) -> Mapping:
    cell = cells.get(tf)
    return cell if isinstance(cell, Mapping) else {}


def _rvol_lit(cell: Mapping, bear: bool) -> bool:
    """scannerConfirmation.js: a numeric cell whose colour is on this side. The background decides, then the text colour."""
    if _num(cell.get("value")) is None:
        return False
    bg, fg = cell.get("bg"), cell.get("fg")
    if bg in RVOL_BG[bear]:
        return True
    return bg not in RVOL_BG[not bear] and fg in RVOL_FG[bear]


def _point(ts: float, row: Mapping, bear: bool) -> list:
    hl = row.get("highLow")
    shown = _num(hl.get("value")) if isinstance(hl, Mapping) else None
    key = hl_sort_value(hl)
    inputs = row.get("scannerInputs") if isinstance(row.get("scannerInputs"), Mapping) else {}
    level = inputs.get("highestLevel")
    types = inputs.get("setupTypes") if isinstance(inputs.get("setupTypes"), list) else []
    setups = inputs.get("setupCount")
    rvol, sqz, skittles = _cells(row, "rvol"), _cells(row, "sqz"), _cells(row, "skittles")
    return [ts, key, shown if shown is not None else key, _num(row.get("pctChange")), _num(row.get("last")),
            level if type(level) is int else None, setups if type(setups) is int else None,
            [str(t.get("text") or t.get("key")) for t in types if isinstance(t, Mapping)],
            [tf for tf in RVOL_TFS if _rvol_lit(_cell(rvol, tf), bear)],
            [tf for tf in SQZ_TFS if _cell(sqz, tf).get("bg") == SQZ_BG[bear]],
            [tf for tf in SQZ_TFS if _cell(sqz, tf).get("bg") == "black" and str(_cell(sqz, tf).get("value", "x")).strip() in ("", "-", "–", "—")],
            [tf for tf in SKITTLES_TFS if _cell(skittles, tf).get("bg") in SKITTLES_BG[bear]]]


def _ticker_line(symbol: Any, entry: Any, bear: bool) -> str | None:
    """[symbol, last seen, row cap reached, points, scans (hits), saved rows] as a line, or None when the entry holds nothing usable."""
    if not isinstance(entry, Mapping) or not isinstance(entry.get("snapshots"), list):
        return None
    points = []
    for snap in entry["snapshots"]:
        row = snap.get("row") if isinstance(snap, Mapping) else None
        ts = _stamp(snap.get("at")) if isinstance(row, Mapping) and row.get("scanPass") else None
        if ts is not None:
            points.append(_point(ts, row, bear))
    if not points:
        return None
    points.sort(key=lambda p: p[0])
    last = _stamp(entry.get("lastSeenAt"))
    hits = entry.get("hits")
    return json.dumps([str(symbol).upper(), max(last or 0.0, points[-1][0]), bool(entry.get("truncated")), points,
                       hits if type(hits) is int else None, len(entry["snapshots"])], separators=(",", ":"))


_DECODER = json.JSONDecoder()
_SPACE = re.compile(r"[ \t\n\r]*")


def _skip(text: str, i: int) -> int:
    return _SPACE.match(text, i).end()


def _open(text: str, i: int) -> tuple[int, bool]:
    i = _skip(text, i)
    if text[i:i + 1] != "{":
        raise ValueError("expected an object")
    i = _skip(text, i + 1)
    return (i + 1, False) if text[i:i + 1] == "}" else (i, True)


def _key(text: str, i: int) -> tuple[Any, int]:
    key, i = _DECODER.raw_decode(text, i)
    i = _skip(text, i)
    if text[i:i + 1] != ":":
        raise ValueError("expected a colon")
    return key, _skip(text, i + 1)


def _next(text: str, i: int) -> tuple[int, bool]:
    i = _skip(text, i)
    mark = text[i:i + 1]
    if mark == ",":
        return _skip(text, i + 1), True
    if mark == "}":
        return i + 1, False
    raise ValueError("expected a comma or a brace")


def build(path: Path | str, bear: bool) -> Iterator[str]:
    """The index of one file, line by line. The file is walked one ticker at a time so a gigabyte of
    rows is never held at once; a file that cannot be read ends with broken: true, and with retry: true
    when only this attempt failed (locked for a moment, out of memory) and the file itself may be fine."""
    broken = retry = False
    try:
        text = Path(path).read_text(encoding="utf-8")
        i, more = _open(text, 0)
        while more:
            key, i = _key(text, i)
            if key == "rows" and text[i:i + 1] == "{":
                i, inner = _open(text, i)
                while inner:
                    symbol, i = _key(text, i)
                    entry, i = _DECODER.raw_decode(text, i)
                    line = _ticker_line(symbol, entry, bear)
                    del entry
                    if line is not None:
                        yield line
                    i, inner = _next(text, i)
            else:
                _, i = _DECODER.raw_decode(text, i)
            i, more = _next(text, i)
    except (OSError, MemoryError):
        broken = retry = True
    except (ValueError, TypeError, AttributeError, KeyError, RecursionError):
        broken = True
    yield json.dumps({"done": True, "broken": broken, "retry": retry})


def _priority_class() -> int | None:
    """What Windows really says this process runs at (None elsewhere). The handle types must be declared:
    left as ctypes' 32-bit default the pseudo-handle is cut short and the call fails without raising."""
    if sys.platform != "win32":
        return None
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    kernel32.GetPriorityClass.argtypes = [ctypes.c_void_p]
    kernel32.GetPriorityClass.restype = ctypes.c_uint
    return int(kernel32.GetPriorityClass(kernel32.GetCurrentProcess()))


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        return 2
    out = sys.stdout.buffer
    out.write(json.dumps({"priority": _priority_class()}).encode("ascii") + b"\n")
    for line in build(argv[1], argv[2] == "bear"):
        out.write(line.encode("ascii") + b"\n")
    out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
