"""Ticker search across all days must find a ticker without parsing every day.

WHY (2026-10-02): "Ticker, all days" for APLD came back EMPTY although APLD
was on that day's History page. The search json-parsed all 30 archived day
files (~1 GB for Watchlist), passed the 20 s proxy timeout, and the browser
got an empty answer. ``_symbol_entry`` now locates the one entry by its key
bytes; these tests pin that it returns exactly what a full parse returns,
including the cases where the same bytes appear INSIDE a row (ticker ``D``
vs the ``rvol.D`` cell).
"""

from __future__ import annotations

import json

from momx import history


def write_day(tmp_path, day, rows, name="Watchlist"):
    board = history.board_dir(tmp_path, name)
    board.mkdir(parents=True, exist_ok=True)
    path = board / f"{day}.json"
    path.write_text(json.dumps({"list": name, "date": day, "rows": rows}), encoding="utf-8")
    return path


def entry(symbol, at, rvol_d):
    return {
        "firstSeenAt": at,
        "lastSeenAt": at,
        "hits": 1,
        "truncated": False,
        "snapshots": [{"at": at, "changed": [], "peakRvol": 1.0,
                       "row": {"symbol": symbol, "rvol": {"D": {"value": rvol_d}}}}],
    }


def test_symbol_entry_matches_full_parse_including_inner_key_collisions(tmp_path):
    rows = {
        "AAPL": entry("AAPL", "2026-10-02T09:31:00-04:00", 1.5),
        "D": entry("D", "2026-10-02T09:32:00-04:00", 2.5),
        "APLD": entry("APLD", "2026-10-02T10:00:00-04:00", 3.5),
    }
    path = write_day(tmp_path, "2026-10-02", rows)
    for symbol in ("AAPL", "D", "APLD"):
        assert history._symbol_entry(path, symbol) == rows[symbol]
    assert history._symbol_entry(path, "ZZZZ") is None
    # "value" exists only inside rows: never mistaken for an entry.
    assert history._symbol_entry(path, "value") is None


def test_symbol_entry_skips_inner_d_cell_when_ticker_d_absent(tmp_path):
    rows = {"AAPL": entry("AAPL", "2026-10-02T09:31:00-04:00", 1.5)}
    path = write_day(tmp_path, "2026-10-02", rows)
    assert history._symbol_entry(path, "D") is None


def test_all_days_search_returns_every_day_the_ticker_appeared(tmp_path):
    write_day(tmp_path, "2026-10-01", {"APLD": entry("APLD", "2026-10-01T09:40:00-04:00", 1.0)})
    write_day(tmp_path, "2026-10-02", {
        "AAPL": entry("AAPL", "2026-10-02T09:31:00-04:00", 1.0),
        "APLD": entry("APLD", "2026-10-02T10:00:00-04:00", 1.0),
    })
    response = history.history_response("Watchlist", symbol="apld", directory=tmp_path)
    assert [row["date"] for row in response["rows"]] == ["2026-10-02", "2026-10-01"]
    one_day = history.history_response("Watchlist", symbol="APLD", date="2026-10-01", directory=tmp_path)
    assert [row["date"] for row in one_day["rows"]] == ["2026-10-01"]


def test_rewritten_day_is_not_served_from_a_stale_cache(tmp_path):
    path = write_day(tmp_path, "2026-10-02", {"AAPL": entry("AAPL", "2026-10-02T09:31:00-04:00", 1.0)})
    assert history._symbol_entry(path, "APLD") is None
    write_day(tmp_path, "2026-10-02", {
        "AAPL": entry("AAPL", "2026-10-02T09:31:00-04:00", 1.0),
        "APLD": entry("APLD", "2026-10-02T10:00:00-04:00", 1.0),
    })
    assert history._symbol_entry(path, "APLD") is not None


def test_unexpected_layout_falls_back_to_full_parse(tmp_path):
    board = history.board_dir(tmp_path, "Watchlist")
    board.mkdir(parents=True)
    path = board / "2026-10-02.json"
    rows = {"APLD": entry("APLD", "2026-10-02T10:00:00-04:00", 1.0)}
    path.write_text(json.dumps({"rows": rows}, indent=2), encoding="utf-8")
    assert history._symbol_entry(path, "APLD") == rows["APLD"]
