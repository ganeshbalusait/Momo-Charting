"""Ask the 30-day archive: WHICH TICKERS ever matched this condition?

The trader's ask, verbatim (2026-09-05): "1h Rvol i will get result which
tickers have rvol of 1hr". So the answer is a TICKER LIST, not a log of
moments - measured on his own archive, "Skittles 2h cyan or magenta" on
2026-09-04 is 455 snapshots but only 30 tickers, and the snapshot list is the
same name repeating every couple of minutes.

WHY THIS RUNS HERE AND NOT IN THE BROWSER
-----------------------------------------
A single day file is 8.8-25.5 MB (measured across 2026-09-01..04) and the
history endpoint only ever ships the newest 400 of a day's ~11,770 snapshots -
3.4% of it. Filtering what the browser already holds would search 3% of the day
and report the result as if it were the whole day. That is not a smaller
version of the right answer, it is a wrong one.

Sending just the one cell per snapshot (a projection) would be 0.29 MB/day, so
8.7 MB for 30 days. Evaluating here instead returns 14.3 KB for four days -
80x smaller - and 30 days of scanning is IO, not CPU (0.69s to load four days,
0.02s to match them).

THIS MODULE HOLDS NO KNOWLEDGE OF ITS OWN
-----------------------------------------
It never learns what "cyan" means, which colours are bullish, or what a good
RVOL is. The BROWSER sends the resolved condition - the timeframe, the
threshold, the exact list of backgrounds that count - derived from the same
``momxFilters.js`` that paints the live board. This module only applies it.

That is deliberate. This repository has repeatedly been burned by two copies of
one rule drifting apart (the chart engine vs the scanner's own EMA path; the
High-OI board duplicated across python and JS with a "keep in step" comment).
A condition that travels in the request cannot drift from the board that
produced it.

ONE KNOWN DIVERGENCE, recorded rather than hidden: ``momxFilters``'s
"bullish only" lets a cell through when the colours cannot say which side won
(|z| <= 0.5 paints black on black). Here such a cell simply fails the colour
test. It cannot arise at a threshold of 2.0 or more, because any reading that
size forces a coloured background - and his thresholds are 2.5 and 3.0. See
``test_query_black_cell_fails_a_colour_condition``.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

__all__ = [
    "MAX_DAYS",
    "MAX_RESULTS",
    "QUERY_SECTIONS",
    "ConditionError",
    "available_dates",
    "cell_matches",
    "coerce_condition",
    "condition_key",
    "query",
    "query_day_rows",
]

#: The archive keeps 30 days; asking for more cannot return more.
MAX_DAYS = 30

#: A hard ceiling on rows returned. Never silently: the payload carries
#: ``truncated`` and ``totalResults`` so the panel can say it dropped some.
MAX_RESULTS = 2000

#: The three cell sections a row carries. Named here so an unknown section is
#: rejected loudly instead of quietly matching nothing.
QUERY_SECTIONS = ("rvol", "sqz", "skittles")


class ConditionError(ValueError):
    """The browser sent a condition this module cannot apply."""


# ---------------------------------------------------------------------------
# the condition
# ---------------------------------------------------------------------------


def _string_list(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        parts = [piece.strip() for piece in value.split(",")]
    elif isinstance(value, (list, tuple)):
        parts = [str(piece).strip() for piece in value]
    else:
        raise ConditionError(f"expected a list of names, got {type(value).__name__}")
    return tuple(part for part in parts if part)


def _number(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def coerce_condition(raw: Mapping[str, Any] | None) -> dict:
    """Validate the browser's condition into a shape this module can apply.

    Raises :class:`ConditionError` rather than guessing. A query that cannot be
    understood must fail visibly - answering a DIFFERENT question quietly is
    exactly how a search stops being trustworthy.
    """
    data = raw or {}
    section = str(data.get("section") or "").strip()
    if section not in QUERY_SECTIONS:
        raise ConditionError(
            f"section must be one of {', '.join(QUERY_SECTIONS)}, got {section!r}"
        )
    timeframe = str(data.get("timeframe") or "").strip()
    if not timeframe:
        raise ConditionError("timeframe is required")

    minimum = _number(data.get("min"))
    backgrounds = _string_list(data.get("bg"))
    foregrounds = _string_list(data.get("fg"))
    if minimum is None and not backgrounds and not foregrounds:
        # An empty condition matches every snapshot of every day, which is a
        # 39,000-row answer dressed up as a search result.
        raise ConditionError("a condition needs a minimum, a colour, or both")

    return {
        "section": section,
        "timeframe": timeframe,
        "min": minimum,
        "bg": backgrounds,
        "fg": foregrounds,
    }


def condition_key(condition: Mapping[str, Any]) -> str:
    """A stable cache key. Sorted, so ``[cyan, green]`` and ``[green, cyan]``
    are the same query and share one cached answer."""
    return "|".join(
        [
            str(condition.get("section")),
            str(condition.get("timeframe")),
            "" if condition.get("min") is None else repr(float(condition["min"])),
            ",".join(sorted(condition.get("bg") or ())),
            ",".join(sorted(condition.get("fg") or ())),
        ]
    )


# ---------------------------------------------------------------------------
# the test, applied to one cell
# ---------------------------------------------------------------------------


def cell_matches(cell: Any, condition: Mapping[str, Any]) -> bool:
    """Does this one cell satisfy the condition?

    Mechanical by design - see the module docstring. ``min`` is inclusive, to
    match ``momxFilters``'s ``value >= threshold``. The colour test passes when
    the background is in the ``bg`` list OR the text is in the ``fg`` list;
    RVOL sends both because below 2.0 the background is black and the direction
    lives in the text colour, while SQZ and Skittles send only ``bg`` because
    for them the text colour is a different fact entirely (a cyan Skittles
    NUMBER means "9 already above 20", which is a state, not the cross).
    """
    if not isinstance(cell, Mapping):
        return False

    minimum = condition.get("min")
    if minimum is not None:
        value = _number(cell.get("value"))
        if value is None or value < minimum:
            return False

    backgrounds = condition.get("bg") or ()
    foregrounds = condition.get("fg") or ()
    if not backgrounds and not foregrounds:
        return True
    background = cell.get("bg")
    if background and background in backgrounds:
        return True
    text = cell.get("fg")
    return bool(text and text in foregrounds)


# ---------------------------------------------------------------------------
# one day
# ---------------------------------------------------------------------------


def query_day_rows(rows: Mapping[str, Any] | None, condition: Mapping[str, Any]) -> tuple[list[dict], int]:
    """Every ticker in one day file that matched, plus the snapshots scanned.

    Returns one record per SYMBOL, not per snapshot: first and last time it
    matched, how many snapshots did, and the peak reading. That collapse is the
    whole point - the raw snapshot list is the same name every two minutes.
    """
    section = condition["section"]
    timeframe = condition["timeframe"]
    found: dict[str, dict] = {}
    scanned = 0

    for symbol, entry in (rows or {}).items():
        if not isinstance(entry, Mapping):
            continue
        for snapshot in entry.get("snapshots") or ():
            if not isinstance(snapshot, Mapping):
                continue
            scanned += 1
            row = snapshot.get("row")
            if not isinstance(row, Mapping):
                continue
            cell = ((row.get(section) or {}) or {}).get(timeframe)
            if not cell_matches(cell, condition):
                continue
            # ``at`` is already an offset-aware ET stamp written by the
            # recorder, and characters 11:16 are its HH:MM. No parsing and no
            # timezone maths: this machine runs CDT and any use of a local
            # clock here would be an hour out.
            stamp = str(snapshot.get("at") or "")
            clock = stamp[11:16]
            value = _number((cell or {}).get("value"))
            hit = found.get(symbol)
            if hit is None:
                found[symbol] = {
                    "symbol": symbol,
                    "first": clock,
                    "last": clock,
                    "firstAt": stamp,
                    "hits": 1,
                    "peak": value,
                    "lastValue": value,
                    "industry": row.get("industry"),
                    "pctChange": row.get("pctChange"),
                }
                continue
            hit["last"] = clock
            hit["hits"] += 1
            hit["lastValue"] = value
            hit["pctChange"] = row.get("pctChange")
            if value is not None and (hit["peak"] is None or value > hit["peak"]):
                hit["peak"] = value

    return list(found.values()), scanned


# ---------------------------------------------------------------------------
# the archive
# ---------------------------------------------------------------------------


def _board_dir(directory: Path, board: str) -> Path:
    # REUSES momx.history.board_dir rather than re-sanitising the name here.
    # The archive's layout is <dir>/<Board>/<YYYY-MM-DD>.json and the folder
    # name is a sanitised board name ("Daily news" -> "Daily_news"); a second
    # copy of that rule would silently read the wrong folder for any board
    # whose name has a space in it.
    from momx import history as _history

    return _history.board_dir(directory, board)


def available_dates(directory: Path, board: str) -> list[str]:
    """Day files present for this board, newest first."""
    folder = _board_dir(Path(directory), board)
    try:
        names = [path.stem for path in folder.glob("*.json")]
    except OSError:
        return []
    return sorted((name for name in names if len(name) == 10), reverse=True)


#: Parsed-answer cache. Keyed on (path, mtime_ns, condition) so a finished day
#: is computed once and today's file recomputes the moment the recorder touches
#: it. The ANSWER is cached, never the parsed document: a day file is up to
#: 25 MB and thirty of them would be most of a gigabyte resident, while thirty
#: cached answers are a few hundred KB.
_RESULT_CACHE: dict[tuple[str, int, str], tuple[list[dict], int]] = {}
_RESULT_CACHE_MAX = 400


def _day_results(path: Path, condition: Mapping[str, Any]) -> tuple[list[dict], int]:
    try:
        stat = path.stat()
    except OSError:
        return [], 0
    key = (str(path), stat.st_mtime_ns, condition_key(condition))
    cached = _RESULT_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        with path.open(encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        # A half-written or corrupt day is a missing day, never a 500 on a tab
        # he only opened to look back at yesterday.
        return [], 0
    results, scanned = query_day_rows(document.get("rows"), condition)
    if len(_RESULT_CACHE) >= _RESULT_CACHE_MAX:
        _RESULT_CACHE.clear()
    _RESULT_CACHE[key] = (results, scanned)
    return results, scanned


def query(
    board: str,
    condition: Mapping[str, Any] | None,
    *,
    directory: Path,
    dates: Sequence[str] | None = None,
    limit: int = MAX_RESULTS,
) -> dict:
    """Run one condition across one day or the whole archive.

    ``dates`` is an explicit list; ``None`` means every day file this board
    has, newest first, capped at :data:`MAX_DAYS`.
    """
    resolved = coerce_condition(condition)
    folder = _board_dir(Path(directory), board)
    wanted: Iterable[str]
    if dates:
        wanted = [str(day).strip() for day in dates if str(day).strip()]
    else:
        wanted = available_dates(directory, board)
    wanted = list(wanted)[:MAX_DAYS]

    results: list[dict] = []
    scanned_days = 0
    scanned_snapshots = 0
    for day in wanted:
        day_results, scanned = _day_results(folder / f"{day}.json", resolved)
        if scanned:
            scanned_days += 1
            scanned_snapshots += scanned
        for record in day_results:
            results.append({**record, "date": day})

    # Newest day first, then the strongest reading - he is scanning for names
    # to look at, so the biggest number on the most recent day should be top.
    results.sort(
        key=lambda row: (
            row.get("date") or "",
            row.get("peak") if row.get("peak") is not None else float("-inf"),
        ),
        reverse=True,
    )
    total = len(results)
    capped = max(0, int(limit or 0)) or MAX_RESULTS
    truncated = total > capped

    return {
        "board": board,
        "condition": resolved,
        "days": wanted,
        "results": results[:capped],
        "totalResults": total,
        "returnedResults": min(total, capped),
        "tickers": len({row["symbol"] for row in results}),
        "scannedDays": scanned_days,
        "scannedSnapshots": scanned_snapshots,
        # Never a silent cap - the panel says "showing 2000 of 2431".
        "truncated": truncated,
    }
