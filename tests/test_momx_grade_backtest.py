import json
from pathlib import Path

import scripts.momx_grade_backtest as bt


def _snap(at, last, pct, letter_row):
    return {"at": at, "row": dict(letter_row, last=last, pctChange=pct)}


A_PLUS_ROW = {
    "sqz": {"Wk": {"bg": "cyan"}},
    "rvol": {"5m": {"bg": "cyan", "value": 7.0}},
    "skittles": {tf: {"fg": "cyan", "bg": "black"} for tf in ("2h", "4h", "D", "2D", "3D", "4D", "Wk", "M")},
    "highLow": {"bg": "green", "value": 0.9},
}


def test_backtest_scores_first_a_plus_to_next_day_close(tmp_path: Path):
    wl = tmp_path / "Watchlist"
    wl.mkdir()
    # A real trading day's file always has hundreds of snapshots (many
    # tickers x many polls); pad day1 past MIN_SNAPSHOTS_FOR_CLOSE so it
    # qualifies as a graded trading day under that same invariant.
    day1_filler = [_snap("2026-09-21T09:55:00-04:00", 100.0, 1.0, {})] * 500
    day1 = {"rows": {"AAA": {"snapshots": [
        _snap("2026-09-21T09:40:00-04:00", 100.0, 1.0, A_PLUS_ROW),
        _snap("2026-09-21T09:50:00-04:00", 101.0, 2.0, A_PLUS_ROW),   # not first -> ignored
    ] + day1_filler}}}
    filler = [_snap("2026-09-22T04:00:00-04:00", 110.0, 10.0, {})] * 500  # close = 100
    day2 = {"rows": {"AAA": {"snapshots": filler}}}
    (wl / "2026-09-21.json").write_text(json.dumps(day1), encoding="utf-8")
    (wl / "2026-09-22.json").write_text(json.dumps(day2), encoding="utf-8")

    out = bt.run(wl)
    a_plus = out["letters"]["A+"]
    assert a_plus["count"] == 1
    assert out["events"][0]["price"] == 100.0
    assert round(out["events"][0]["close"], 6) == 100.0
    assert a_plus["pctHigherClose"] == 0.0


def test_backtest_skips_thin_non_trading_day_file(tmp_path: Path):
    """A weekend/holiday file (< 500 total snapshots) must not be graded, even
    when it sits between two full trading days and holds an A+ row."""
    wl = tmp_path / "Watchlist"
    wl.mkdir()
    full_before = [_snap("2026-09-19T04:00:00-04:00", 100.0, 0.0, {})] * 500
    day_before = {"rows": {"AAA": {"snapshots": full_before}}}
    thin_day = {"rows": {"AAA": {"snapshots": [
        _snap("2026-09-20T09:40:00-04:00", 100.0, 1.0, A_PLUS_ROW),
    ]}}}
    full_after = [_snap("2026-09-21T04:00:00-04:00", 110.0, 10.0, {})] * 500
    day_after = {"rows": {"AAA": {"snapshots": full_after}}}
    (wl / "2026-09-19.json").write_text(json.dumps(day_before), encoding="utf-8")
    (wl / "2026-09-20.json").write_text(json.dumps(thin_day), encoding="utf-8")
    (wl / "2026-09-21.json").write_text(json.dumps(day_after), encoding="utf-8")

    out = bt.run(wl)

    assert out["events"] == []
    assert "2026-09-20" not in out["days"]


def test_backtest_skips_bad_at_and_raising_grade_row(tmp_path: Path, monkeypatch):
    """One snapshot with an unparsable/missing 'at' and one symbol whose
    grading raises must not abort the run - the other events still come back."""
    wl = tmp_path / "Watchlist"
    wl.mkdir()
    boom_row = dict(A_PLUS_ROW, last=100.0, pctChange=1.0, _marker="boom")
    # Pad day1 past MIN_SNAPSHOTS_FOR_CLOSE so it qualifies as a graded
    # trading day (real archive files have hundreds of snapshots/day).
    day1_filler = [_snap("2026-09-21T09:55:00-04:00", 100.0, 1.0, {})] * 500
    day1 = {"rows": {
        "AAA": {"snapshots": [
            {"row": dict(A_PLUS_ROW, last=100.0, pctChange=1.0)},  # missing "at" -> KeyError
            _snap("not-a-timestamp", 100.0, 1.0, A_PLUS_ROW),      # malformed "at" -> ValueError
            _snap("2026-09-21T09:40:00-04:00", 100.0, 1.0, A_PLUS_ROW),  # good snapshot
        ] + day1_filler},
        "BBB": {"snapshots": [
            {"at": "2026-09-21T09:40:00-04:00", "row": boom_row},  # grading raises
        ]},
    }}
    filler = [_snap("2026-09-22T04:00:00-04:00", 110.0, 10.0, {})] * 500
    day2 = {"rows": {"AAA": {"snapshots": filler}, "BBB": {"snapshots": filler}}}
    (wl / "2026-09-21.json").write_text(json.dumps(day1), encoding="utf-8")
    (wl / "2026-09-22.json").write_text(json.dumps(day2), encoding="utf-8")

    real_grade_row = bt.grade.grade_row

    def flaky(row, now, direction="bull"):
        if isinstance(row, dict) and row.get("_marker") == "boom":
            raise RuntimeError("boom")
        return real_grade_row(row, now, direction)

    monkeypatch.setattr(bt.grade, "grade_row", flaky)

    out = bt.run(wl)

    assert out["letters"]["A+"]["count"] == 1
    assert out["events"][0]["symbol"] == "AAA"


# ------------------------------------------------------------- BEAR (spec 2026-09-24)

from momx_mirror import mirror_row  # noqa: E402


def test_bear_backtest_grades_the_mirror_row_and_scores_the_drop_as_a_win(tmp_path: Path):
    wl = tmp_path / "Watchlist"
    wl.mkdir()
    bear_row = mirror_row(A_PLUS_ROW)
    day1_filler = [_snap("2026-09-21T09:55:00-04:00", 100.0, -1.0, {})] * 500
    day1 = {"rows": {"AAA": {"snapshots": [
        _snap("2026-09-21T09:40:00-04:00", 100.0, -1.0, bear_row),
    ] + day1_filler}}}
    # Next day's first snapshot: last 95 and pctChange -5 -> yesterday's close = 100... make it 95:
    filler = [_snap("2026-09-22T04:00:00-04:00", 94.05, -1.0, {})] * 500   # close = 94.05 / 0.99 = 95
    day2 = {"rows": {"AAA": {"snapshots": filler}}}
    (wl / "2026-09-21.json").write_text(json.dumps(day1), encoding="utf-8")
    (wl / "2026-09-22.json").write_text(json.dumps(day2), encoding="utf-8")

    bear = bt.run(wl, direction="bear")
    assert bear["direction"] == "bear"
    assert bear["letters"]["A+"]["count"] == 1
    assert bear["events"][0]["toClose"] == 5.0          # fell 5%: +5% in the trade's favour
    assert bear["letters"]["A+"]["pctHigherClose"] == 100.0
    # The bull reading of the same archive finds no A+ (bearish paints).
    assert bt.run(wl)["letters"]["A+"]["count"] == 0
    assert bt.run(wl).get("direction", "bull") == "bull"


def test_bear_backtest_paths_hang_under_the_bear_root():
    assert bt.default_history_dir("Watchlist", "bear") == bt.ROOT / "artifacts" / "bear" / "momx_history" / "Watchlist"
    assert bt.default_history_dir("Watchlist") == bt.ROOT / "artifacts" / "momx_history" / "Watchlist"
    assert bt.record_target("bear") == bt.ROOT / "artifacts" / "bear" / "momx_grade_record_backtest.json"
    assert bt.record_target("bull") == bt.ROOT / "artifacts" / "momx_grade_record_backtest.json"
