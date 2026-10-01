"""One day's history must stay small enough for a phone to render.

WHY (measured live 2026-09-01 at 13:52 ET, mid-session)

    Mag7 history       325 rows   0.7 MB   loaded fine
    Watchlist history 2509 rows   5.0 MB   froze his phone

Earlier the same day, gzip cut the WIRE cost of that payload from 5.0 MB to
468 KB -- and the phone still froze, because the browser must decompress,
parse and render every row on the main thread: ~2509 rows of ~40 columns is
roughly 100,000 cells. The symptom was not merely a slow table; the whole UI
thread stalled and the TABS stopped responding. And that was one day, still
growing for the rest of the session.

The cap keeps the NEWEST rows, because the newest snapshots are the ones he is
looking at. It reports what it dropped rather than quietly looking complete --
a truncated table that presents itself as the whole truth is the same failure
shape this repo keeps hitting (a value true for a narrower thing, believed by
a wider reader).
"""

from __future__ import annotations

import json

import pytest

from momx import history


def build(tmp_path, day, count, name="Watchlist"):
    """Archive ``count`` snapshots for one ticker on one day, in time order."""
    board = history.board_dir(tmp_path, name)
    board.mkdir(parents=True, exist_ok=True)
    entries = {
        "AAPL": {
            "snapshots": [
                {"at": "%sT%02d:%02d:00-04:00" % (day, 9 + i // 60, i % 60),
                 "changePct": float(i)}
                for i in range(count)
            ]
        }
    }
    # Write the day file directly, the way the existing history tests do:
    # the archive format is the contract under test, not a private helper.
    (board / ("%s.json" % day)).write_text(
        json.dumps({"rows": entries}), encoding="utf-8"
    )


@pytest.fixture
def archive(tmp_path):
    return tmp_path


def test_a_normal_day_is_returned_whole_and_says_so(archive):
    build(archive, "2026-09-01", 12)
    out = history.history_response("Watchlist", directory=archive)
    assert len(out["rows"]) == 12
    assert out["truncated"] is False
    assert out["totalRows"] == 12 and out["returnedRows"] == 12


def test_a_huge_day_is_capped(archive):
    build(archive, "2026-09-01", history.HISTORY_MAX_DAY_ROWS + 900)
    out = history.history_response("Watchlist", directory=archive)
    assert len(out["rows"]) == history.HISTORY_MAX_DAY_ROWS


def test_the_cap_keeps_the_NEWEST_rows_not_the_oldest(archive):
    """The regression that would matter most.

    _day_rows sorts ascending, so a naive head-slice would hand him the
    START of the session and silently hide everything since -- on a scanner
    whose entire job is telling him what just happened.
    """
    total = history.HISTORY_MAX_DAY_ROWS + 50
    build(archive, "2026-09-01", total)
    out = history.history_response("Watchlist", directory=archive)
    stamps = [r.get("at") for r in out["rows"]]
    assert stamps == sorted(stamps), "rows should stay in time order"
    # The last row of the capped set must be the last row of the whole day.
    everything = history._day_rows("Watchlist", "2026-09-01", archive)
    assert out["rows"][-1]["at"] == everything[-1]["at"]
    assert out["rows"][0]["at"] != everything[0]["at"], "kept the oldest, not the newest"


def test_truncation_is_reported_never_silent(archive):
    total = history.HISTORY_MAX_DAY_ROWS + 129
    build(archive, "2026-09-01", total)
    out = history.history_response("Watchlist", directory=archive)
    assert out["truncated"] is True
    assert out["totalRows"] == total
    assert out["returnedRows"] == history.HISTORY_MAX_DAY_ROWS
    # The UI can only be honest if it can compute the gap.
    assert out["totalRows"] - out["returnedRows"] == 129


def test_an_empty_day_still_answers_with_the_counters(archive):
    out = history.history_response("Watchlist", directory=archive)
    assert out["rows"] == []
    assert out.get("truncated") in (False, None)


def test_a_single_ticker_query_is_capped_too(archive):
    # A name that matches every scan of every day is not small.
    build(archive, "2026-09-01", history.HISTORY_MAX_DAY_ROWS + 200)
    out = history.history_response("Watchlist", symbol="AAPL", directory=archive)
    assert len(out["rows"]) == history.HISTORY_MAX_DAY_ROWS
    assert out["truncated"] is True
    assert out["totalRows"] == history.HISTORY_MAX_DAY_ROWS + 200


def test_the_cap_is_big_enough_to_be_useful(archive):
    # Small enough for a phone, large enough to be a real day's worth: the
    # Mag7 board archived 325 rows in a session and must never be truncated.
    assert 200 <= history.HISTORY_MAX_DAY_ROWS <= 1000
