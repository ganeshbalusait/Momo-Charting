"""Unit: scripts/option_spread_scan.py - the --csv file carries exactly the
rows and verdicts the printed table shows. Uses the fake-repo helpers of
tests/test_option_spread_scan.py (scratch chain cache, no broker)."""
from __future__ import annotations

import csv

import pytest

from test_option_spread_scan import NOW, _write_chain, oss


@pytest.fixture
def repo(tmp_path, monkeypatch):
    monkeypatch.setattr(oss, "_is_etf", lambda symbol: False)     # the real ETF list calls TEST a fund
    _write_chain(tmp_path, "TEST")
    (tmp_path / "artifacts" / "momx_board_cache").mkdir(parents=True, exist_ok=True)
    return tmp_path


def table_rows(printed: str) -> list[list[str]]:
    return [line.split() for line in printed.splitlines() if line.startswith("TEST ")]


def test_the_csv_matches_the_printed_table_row_for_row(repo, capsys):
    out = repo / "spreads.csv"
    assert oss.main(["--symbols", "TEST", "--csv", str(out)], root=repo, now_ts=NOW) == 0
    printed = capsys.readouterr().out
    table = table_rows(printed)
    with open(out, newline="", encoding="ascii") as handle:
        records = list(csv.DictReader(handle))
    assert len(table) == len(records) == 2                          # the Bot's call and put
    for cells, record in zip(table, records):
        # TICKER SIDE MON DD DTE STRIKE BID ASK MID SPRD$ SPRD% COST$ OI VOL AGEm VERDICT
        assert (cells[0], cells[1]) == (record["ticker"], record["side"])
        assert float(cells[5]) == float(record["strike"])
        assert (float(cells[6]), float(cells[7])) == (float(record["bid"]), float(record["ask"]))
        assert abs(float(cells[10]) - float(record["spread_pct"])) <= 0.05
        assert cells[15] == record["verdict"]
        assert record["pick"] == "MAIN" and record["contract"].startswith("TEST  ")
    assert f"CSV: 2 rows written to {out}" in printed


def test_an_unwritable_csv_path_is_reported_and_exits_1(repo, capsys):
    blocked = repo / "no_such_dir" / "spreads.csv"
    assert oss.main(["--symbols", "TEST", "--csv", str(blocked)], root=repo, now_ts=NOW) == 1
    printed = capsys.readouterr().out
    assert f"CSV NOT written ({blocked})" in printed
    assert table_rows(printed), "the table still prints"
