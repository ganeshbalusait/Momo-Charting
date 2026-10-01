"""The history query: "which tickers ever matched this condition".

Every fixture below mirrors the real archive shape written by momx.history -
``{"list", "date", "rows": {SYM: {"snapshots": [{"at", "row"}]}}}`` - because a
query that works on an invented shape is worth nothing.
"""
from __future__ import annotations

import json

import pytest

from momx import query


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def cell(value=None, bg=None, fg="black"):
    return {"value": value, "bg": bg, "fg": fg}


def snapshot(at, *, rvol=None, sqz=None, skittles=None, industry="Semis", pct=1.0):
    return {
        "at": at,
        "changed": [],
        "row": {
            "symbol": "X",
            "industry": industry,
            "pctChange": pct,
            "rvol": rvol or {},
            "sqz": sqz or {},
            "skittles": skittles or {},
        },
    }


def day_document(date, rows):
    return {"list": "Watchlist", "date": date, "rows": rows}


def write_day(directory, board, date, rows):
    folder = directory / board
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{date}.json"
    path.write_text(json.dumps(day_document(date, rows)), encoding="utf-8")
    return path


RVOL_3 = {"section": "rvol", "timeframe": "1h", "min": 3.0, "bg": ("cyan",), "fg": ()}


# ---------------------------------------------------------------------------
# coerce_condition - a query that cannot be understood must fail LOUDLY
# ---------------------------------------------------------------------------


def test_coerce_condition_accepts_a_full_condition():
    out = query.coerce_condition(
        {"section": "rvol", "timeframe": "1h", "min": "2.5", "bg": ["cyan", "green"]}
    )
    assert out["section"] == "rvol"
    assert out["timeframe"] == "1h"
    assert out["min"] == 2.5
    assert out["bg"] == ("cyan", "green")
    assert out["fg"] == ()


def test_coerce_condition_accepts_comma_separated_colours():
    # The browser sends these as query-string params, so a bare string arrives.
    out = query.coerce_condition(
        {"section": "skittles", "timeframe": "2h", "bg": "cyan, magenta"}
    )
    assert out["bg"] == ("cyan", "magenta")


@pytest.mark.parametrize(
    "raw",
    [
        {"section": "nope", "timeframe": "1h", "min": 3},
        {"section": "", "timeframe": "1h", "min": 3},
        {"timeframe": "1h", "min": 3},
    ],
)
def test_coerce_condition_rejects_an_unknown_section(raw):
    with pytest.raises(query.ConditionError):
        query.coerce_condition(raw)


def test_coerce_condition_requires_a_timeframe():
    with pytest.raises(query.ConditionError):
        query.coerce_condition({"section": "rvol", "min": 3})


# An empty condition matches every snapshot of every day - a 39,000-row answer
# wearing the clothes of a search result.
def test_coerce_condition_rejects_an_empty_condition():
    with pytest.raises(query.ConditionError):
        query.coerce_condition({"section": "rvol", "timeframe": "1h"})


def test_condition_key_is_order_independent():
    a = query.coerce_condition({"section": "sqz", "timeframe": "2h", "bg": ["cyan", "black"]})
    b = query.coerce_condition({"section": "sqz", "timeframe": "2h", "bg": ["black", "cyan"]})
    assert query.condition_key(a) == query.condition_key(b)


def test_condition_key_separates_different_thresholds():
    a = query.coerce_condition({"section": "rvol", "timeframe": "1h", "min": 2.5})
    b = query.coerce_condition({"section": "rvol", "timeframe": "1h", "min": 3.0})
    assert query.condition_key(a) != query.condition_key(b)


# ---------------------------------------------------------------------------
# cell_matches
# ---------------------------------------------------------------------------


def test_min_is_inclusive_like_the_live_board():
    assert query.cell_matches(cell(3.0, "cyan"), RVOL_3) is True
    assert query.cell_matches(cell(2.9, "cyan"), RVOL_3) is False


def test_the_colour_must_match_too():
    # Same reading, wrong side of the bar: magenta is bearish and not asked for.
    assert query.cell_matches(cell(4.0, "magenta"), RVOL_3) is False


def test_a_foreground_colour_can_carry_the_match():
    # RVOL sends `fg` because below 2.0 the background is black and the
    # direction lives in the text colour.
    condition = query.coerce_condition(
        {"section": "rvol", "timeframe": "1h", "min": 1.5, "fg": ["cyan", "green"]}
    )
    assert query.cell_matches(cell(1.7, "black", "cyan"), condition) is True
    assert query.cell_matches(cell(1.7, "black", "magenta"), condition) is False


# THE DOCUMENTED DIVERGENCE, pinned so it is a decision and not a surprise.
# momxFilters' "bullish only" lets a black-on-black cell through because the
# colours genuinely cannot say which side won. Here it simply fails the colour
# test. It cannot arise at a threshold of 2.0+, which is where he works.
def test_query_black_cell_fails_a_colour_condition():
    condition = query.coerce_condition(
        {"section": "rvol", "timeframe": "1h", "min": 0.1, "bg": ["cyan", "green"]}
    )
    assert query.cell_matches(cell(0.2, "black", "black"), condition) is False


def test_a_colour_only_condition_ignores_the_value():
    condition = query.coerce_condition(
        {"section": "skittles", "timeframe": "2h", "bg": ["cyan", "magenta"]}
    )
    assert query.cell_matches(cell(88, "cyan"), condition) is True
    assert query.cell_matches(cell(12, "magenta"), condition) is True
    assert query.cell_matches(cell(99, "green"), condition) is False


# A Skittles query asks about the BLOCK. A cyan NUMBER means "9 already above
# 20", which is a state and not the cross - so bg-only conditions must not be
# rescued by the text colour.
def test_a_bg_only_condition_is_not_rescued_by_the_text_colour():
    condition = query.coerce_condition(
        {"section": "skittles", "timeframe": "2h", "bg": ["cyan"]}
    )
    assert query.cell_matches(cell(88, "black", "cyan"), condition) is False


def test_a_missing_or_junk_cell_never_matches():
    assert query.cell_matches(None, RVOL_3) is False
    assert query.cell_matches("nope", RVOL_3) is False
    assert query.cell_matches({}, RVOL_3) is False


# PARITY WITH THE BROWSER, pinned deliberately. momxFilters had exactly this
# bug: a study that has not warmed up ships as {"value": null, "bg": null,
# "fg": null}, and in JS `Number(null)` is 0, so the cell cleared a threshold
# of 0 and put a ticker with NO DATA on the board. Python never had the flaw -
# float(None) raises rather than returning 0, and _number guards "" as well -
# but a future "simplification" to float(value or 0) would import it, and FIND
# would then answer with names the live board would not show. The two matchers
# have to agree about blankness or the history stops being a record of what he
# actually saw.
def test_a_cold_cell_is_not_a_reading_of_zero():
    condition = query.coerce_condition({"section": "rvol", "timeframe": "4h", "min": 0})
    assert query.cell_matches(cell(None, "cyan"), condition) is False
    assert query.cell_matches({"value": None, "bg": None, "fg": None}, condition) is False
    assert query.cell_matches(cell("", "cyan"), condition) is False


# The other half of the same rule: a real zero IS a reading. The fix rejects
# blankness, not small numbers.
def test_a_genuine_zero_still_clears_a_zero_threshold():
    condition = query.coerce_condition({"section": "rvol", "timeframe": "4h", "min": 0})
    assert query.cell_matches(cell(0, "cyan"), condition) is True
    assert query.cell_matches(cell(0.0, "cyan"), condition) is True
    assert query.cell_matches(cell("0.0", "cyan"), condition) is True


# ---------------------------------------------------------------------------
# query_day_rows - the collapse to one record per ticker
# ---------------------------------------------------------------------------


def test_a_ticker_collapses_to_one_record_with_first_last_and_peak():
    rows = {
        "AAA": {
            "snapshots": [
                snapshot("2026-09-04T09:30:00-04:00", rvol={"1h": cell(2.0, "green")}),
                snapshot("2026-09-04T09:45:00-04:00", rvol={"1h": cell(3.4, "cyan")}),
                snapshot("2026-09-04T10:05:00-04:00", rvol={"1h": cell(5.1, "cyan")}),
                snapshot("2026-09-04T10:30:00-04:00", rvol={"1h": cell(3.1, "cyan")}),
            ]
        }
    }
    results, scanned = query.query_day_rows(rows, RVOL_3)
    assert scanned == 4, "every snapshot is scanned, matched or not"
    assert len(results) == 1, "455 snapshots must not become 455 rows"
    hit = results[0]
    assert hit["symbol"] == "AAA"
    assert hit["first"] == "09:45", "the 09:30 reading was below the bar"
    assert hit["last"] == "10:30"
    assert hit["hits"] == 3
    assert hit["peak"] == 5.1
    assert hit["lastValue"] == 3.1


def test_a_ticker_that_never_matches_is_absent():
    rows = {
        "BBB": {"snapshots": [snapshot("2026-09-04T09:30:00-04:00", rvol={"1h": cell(1.0, "black")})]}
    }
    results, scanned = query.query_day_rows(rows, RVOL_3)
    assert results == []
    assert scanned == 1


def test_the_wrong_timeframe_is_not_consulted():
    rows = {
        "CCC": {"snapshots": [snapshot("2026-09-04T09:30:00-04:00", rvol={"2h": cell(9.0, "cyan")})]}
    }
    results, _ = query.query_day_rows(rows, RVOL_3)
    assert results == [], "a 2h spike must not answer a 1h question"


# The recorder writes an offset-aware ET stamp; slicing HH:MM out of it avoids
# any local-clock parsing. This machine runs CDT, so a getHours() here would be
# an hour out - the trap this project has hit before.
def test_the_clock_is_sliced_from_the_stamp_not_reparsed():
    rows = {
        "DDD": {"snapshots": [snapshot("2026-09-04T14:07:33-04:00", rvol={"1h": cell(3.0, "cyan")})]}
    }
    results, _ = query.query_day_rows(rows, RVOL_3)
    assert results[0]["first"] == "14:07"
    assert results[0]["firstAt"] == "2026-09-04T14:07:33-04:00"


def test_malformed_entries_are_skipped_not_raised():
    rows = {"EEE": "not a mapping", "FFF": {"snapshots": ["junk", None]}}
    results, scanned = query.query_day_rows(rows, RVOL_3)
    assert results == []
    assert scanned == 0


# ---------------------------------------------------------------------------
# query - across the archive
# ---------------------------------------------------------------------------


def test_query_spans_days_and_sorts_newest_and_strongest_first(tmp_path):
    write_day(tmp_path, "Watchlist", "2026-09-03", {
        "OLD": {"snapshots": [snapshot("2026-09-03T10:00:00-04:00", rvol={"1h": cell(6.0, "cyan")})]},
    })
    write_day(tmp_path, "Watchlist", "2026-09-04", {
        "WEAK": {"snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(3.2, "cyan")})]},
        "STRONG": {"snapshots": [snapshot("2026-09-04T11:00:00-04:00", rvol={"1h": cell(5.0, "cyan")})]},
    })
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path)
    assert [row["symbol"] for row in out["results"]] == ["STRONG", "WEAK", "OLD"]
    assert out["tickers"] == 3
    assert out["scannedDays"] == 2
    assert out["scannedSnapshots"] == 3
    assert out["days"] == ["2026-09-04", "2026-09-03"], "newest day first"
    assert out["truncated"] is False


def test_query_can_be_pinned_to_one_day(tmp_path):
    write_day(tmp_path, "Watchlist", "2026-09-03", {
        "OLD": {"snapshots": [snapshot("2026-09-03T10:00:00-04:00", rvol={"1h": cell(6.0, "cyan")})]},
    })
    write_day(tmp_path, "Watchlist", "2026-09-04", {
        "NEW": {"snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(6.0, "cyan")})]},
    })
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path, dates=["2026-09-04"])
    assert [row["symbol"] for row in out["results"]] == ["NEW"]
    assert out["scannedDays"] == 1


# A board whose name has a space lives in a sanitised folder ("Daily news" ->
# "Daily_news"). Re-deriving that rule here instead of reusing history.board_dir
# would read the wrong folder and answer "nothing found" forever.
def test_query_finds_a_board_whose_name_has_a_space(tmp_path):
    write_day(tmp_path, "Daily_news", "2026-09-04", {
        "SMH": {"snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(4.0, "cyan")})]},
    })
    out = query.query("Daily news", dict(RVOL_3), directory=tmp_path)
    assert [row["symbol"] for row in out["results"]] == ["SMH"]


def test_query_truncates_loudly(tmp_path):
    rows = {
        f"S{index:03d}": {
            "snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(3.0 + index / 100, "cyan")})]
        }
        for index in range(12)
    }
    write_day(tmp_path, "Watchlist", "2026-09-04", rows)
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path, limit=5)
    assert out["returnedResults"] == 5
    assert out["totalResults"] == 12
    assert out["truncated"] is True, "a silent cap reads as 'that is all there was'"


def test_a_missing_or_corrupt_day_is_absent_not_an_error(tmp_path):
    folder = tmp_path / "Watchlist"
    folder.mkdir(parents=True)
    (folder / "2026-09-04.json").write_text("{ this is not json", encoding="utf-8")
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path, dates=["2026-09-04", "2026-09-01"])
    assert out["results"] == []
    assert out["scannedDays"] == 0


def test_an_empty_archive_answers_empty(tmp_path):
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path)
    assert out["results"] == []
    assert out["days"] == []


def test_query_rejects_a_bad_condition_rather_than_scanning(tmp_path):
    with pytest.raises(query.ConditionError):
        query.query("Watchlist", {"section": "rvol"}, directory=tmp_path)


def test_query_caps_the_day_count(tmp_path):
    for index in range(1, 40):
        write_day(tmp_path, "Watchlist", f"2026-08-{index:02d}" if index <= 31 else f"2026-09-{index - 31:02d}", {})
    out = query.query("Watchlist", dict(RVOL_3), directory=tmp_path)
    assert len(out["days"]) == query.MAX_DAYS


# ---------------------------------------------------------------------------
# the cache
# ---------------------------------------------------------------------------


# A finished day never changes, so it is parsed once. Today's file changes every
# build, and its mtime is what must invalidate the answer - not a TTL, which
# would either serve a stale morning or re-read 500 MB every few seconds.
def test_a_rewritten_day_is_re_scanned(tmp_path):
    path = write_day(tmp_path, "Watchlist", "2026-09-04", {
        "AAA": {"snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(3.0, "cyan")})]},
    })
    first = query.query("Watchlist", dict(RVOL_3), directory=tmp_path)
    assert [row["symbol"] for row in first["results"]] == ["AAA"]

    document = day_document("2026-09-04", {
        "AAA": {"snapshots": [snapshot("2026-09-04T10:00:00-04:00", rvol={"1h": cell(3.0, "cyan")})]},
        "BBB": {"snapshots": [snapshot("2026-09-04T10:30:00-04:00", rvol={"1h": cell(4.0, "cyan")})]},
    })
    path.write_text(json.dumps(document), encoding="utf-8")
    # Force a distinct mtime even on a coarse filesystem clock.
    stat = path.stat()
    import os as _os

    _os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))

    second = query.query("Watchlist", dict(RVOL_3), directory=tmp_path)
    assert {row["symbol"] for row in second["results"]} == {"AAA", "BBB"}
