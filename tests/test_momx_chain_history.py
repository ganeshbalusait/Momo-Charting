"""30 days of option chains (2026-11-10): momx/chain_history.py. Scratch folders only."""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta

from momx import chain_history as ch

ET = ch.ET
MON = datetime(2026, 11, 9, 10, 7, tzinfo=ET)


def chain(folder, symbol, mark, when):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{symbol}.json.gz"
    with gzip.open(path, "wt", encoding="utf-8") as handle:
        json.dump({"symbol": symbol, "selectedExpiryChainRows": [
            {"strike": 100, "side": "call", "bid": mark - 0.02, "ask": mark + 0.02, "mark": mark}]}, handle)
    os.utime(path, (when.timestamp(),) * 2)
    return path


def book(tmp_path):
    return ch.ChainHistory(tmp_path / "cache", tmp_path / "history")


def test_slots_run_every_thirty_minutes_of_a_weekday_session():
    at = lambda h, m, day=9: datetime(2026, 11, day, h, m, tzinfo=ET)
    times = ((9, 34), (9, 35), (10, 4), (10, 5), (15, 59), (16, 5), (16, 34), (16, 35))
    assert [ch.slot_of(at(*t)) for t in times] == [None, "0935", "0935", "1005", "1535", "1605", "1605", None]
    assert ch.slot_of(at(11, 0, day=14)) is None                     # Saturday
    assert ch.slot_of(datetime(2026, 11, 9, 9, 7, tzinfo=ch.ZoneInfo("America/Chicago"))) == "1005"     # any time zone


def test_a_slot_is_saved_once_and_only_chains_that_changed_are_copied_again(tmp_path):
    b = book(tmp_path)
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    chain(tmp_path / "cache", "AAPL", 2.00, MON - timedelta(minutes=9))
    first = b.snapshot(MON)
    assert (first["saved"], first["unchanged"], first["day"], first["slot"]) == (2, 0, "2026-11-09", "1005")
    assert sorted(p.name for p in (tmp_path / "history" / "2026-11-09" / "1005").iterdir()) == ["AAPL.json.gz", "SPY.json.gz"]
    assert b.snapshot(MON + timedelta(minutes=5))["reason"] == "slot already saved"
    chain(tmp_path / "cache", "SPY", 1.40, MON + timedelta(minutes=25))                   # only SPY refreshed
    second = b.snapshot(MON + timedelta(minutes=30))
    assert (second["saved"], second["unchanged"], second["slot"]) == (1, 1, "1035")
    assert [p.name for p in (tmp_path / "history" / "2026-11-09" / "1035").iterdir()] == ["SPY.json.gz"]
    third = b.snapshot(MON + timedelta(minutes=60))                                       # nothing moved
    assert third["saved"] == 0 and (tmp_path / "history" / "2026-11-09" / "1105").is_dir()
    assert b.snapshot(datetime(2026, 11, 9, 18, 0, tzinfo=ET)) == {"saved": 0, "reason": "outside the session"}


def test_after_a_restart_chains_already_saved_today_are_not_copied_again(tmp_path):
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    book(tmp_path).snapshot(MON)
    again = book(tmp_path).snapshot(MON + timedelta(minutes=30))                          # a new process
    assert (again["saved"], again["unchanged"]) == (0, 1)


def test_the_chain_at_a_moment_is_the_newest_copy_at_or_before_it(tmp_path):
    b = book(tmp_path)
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    b.snapshot(MON)                                                                       # 10:05 slot
    chain(tmp_path / "cache", "SPY", 1.40, MON + timedelta(minutes=25))
    b.snapshot(MON + timedelta(minutes=30))                                               # 10:35 slot
    mark = lambda doc: doc["selectedExpiryChainRows"][0]["mark"]
    at_1020 = b.chain_at("spy", datetime(2026, 11, 9, 10, 20, tzinfo=ET))
    assert mark(at_1020) == 1.00 and at_1020["_saved"]["slot"] == "1005" and at_1020["_saved"]["day"] == "2026-11-09"
    assert at_1020["_saved"]["fileTime"] == "2026-11-09T10:04:00-05:00"
    assert mark(b.chain_at("SPY", datetime(2026, 11, 9, 15, 0, tzinfo=ET))) == 1.40
    assert mark(b.chain_at("SPY", datetime(2026, 11, 10, 9, 0, tzinfo=ET))) == 1.40      # next morning: yesterday's last
    assert b.chain_at("SPY", datetime(2026, 11, 9, 9, 50, tzinfo=ET)) is None            # nothing that early
    assert b.chain_at("TSLA", MON) is None and b.chain_at("", MON) is None


def test_a_damaged_copy_is_skipped_for_the_one_before_it(tmp_path):
    b = book(tmp_path)
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    b.snapshot(MON)
    bad = tmp_path / "history" / "2026-11-09" / "1035"
    bad.mkdir(parents=True)
    (bad / "SPY.json.gz").write_bytes(b"not gzip")
    assert b.chain_at("SPY", datetime(2026, 11, 9, 11, 0, tzinfo=ET))["_saved"]["slot"] == "1005"


def test_thirty_days_are_kept_and_older_days_are_deleted(tmp_path):
    b = book(tmp_path)
    for day in ("2026-10-09", "2026-10-10", "2026-10-11", "2026-11-06"):
        (tmp_path / "history" / day / "1005").mkdir(parents=True)
    (tmp_path / "history" / "notes").mkdir()                                              # not a day folder: left alone
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    out = b.snapshot(MON)                                                                 # 2026-11-09: the cutoff is 2026-10-10
    assert out["pruned"] == 1
    kept = sorted(p.name for p in (tmp_path / "history").iterdir())
    assert kept == ["2026-10-10", "2026-10-11", "2026-11-06", "2026-11-09", "notes"]


def test_status_says_what_is_on_file(tmp_path):
    b = book(tmp_path)
    assert b.status()["days"] == 0 and b.status()["through"] is None
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    chain(tmp_path / "cache", "AAPL", 2.00, MON - timedelta(minutes=3))
    b.snapshot(MON)
    s = b.status()
    assert (s["keepDays"], s["everyMinutes"], s["days"], s["from"], s["through"]) == (30, 30, 1, "2026-11-09", "2026-11-09")
    assert (s["snapshots"], s["chains"], s["last"]["saved"]) == (1, 2, 2) and "bid, ask" in s["holds"]


def test_a_missing_chain_folder_saves_nothing_and_does_not_fail(tmp_path):
    out = book(tmp_path).snapshot(MON)
    assert out["saved"] == 0 and out["slot"] == "1005"


# ---- review fixes (2026-11-10)
def test_a_slot_that_could_not_be_finished_is_not_marked_saved_and_is_copied_on_the_next_try(tmp_path, monkeypatch):
    b = book(tmp_path)
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    real = ch.os.replace
    monkeypatch.setattr(ch.os, "replace", lambda *a: (_ for _ in ()).throw(PermissionError("in use")))
    monkeypatch.setattr(ch.time, "sleep", lambda s: None)
    out = b.snapshot(MON)
    assert out["saved"] == 0 and "will retry" in out["reason"]
    assert not (tmp_path / "history" / "2026-11-09" / "1005").exists() and not (tmp_path / "history" / "2026-11-09" / "1005.tmp").exists()
    monkeypatch.setattr(ch.os, "replace", real)
    again = b.snapshot(MON + timedelta(minutes=1))                                        # the same slot, a minute later
    assert again["saved"] == 1 and (tmp_path / "history" / "2026-11-09" / "1005" / "SPY.json.gz").is_file()


def test_a_half_made_slot_left_by_a_crash_is_cleared_and_its_chains_are_copied_again(tmp_path):
    src = chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    crashed = tmp_path / "history" / "2026-11-09" / "1005.tmp"
    crashed.mkdir(parents=True)
    ch.shutil.copy2(src, crashed / "SPY.json.gz")                                         # the worker died before the rename
    out = book(tmp_path).snapshot(MON)
    assert out["saved"] == 1 and not crashed.exists()
    assert book(tmp_path).status()["snapshots"] == 1


def test_a_slot_saved_late_is_not_handed_back_for_an_earlier_moment(tmp_path):
    b = book(tmp_path)
    chain(tmp_path / "cache", "SPY", 1.00, datetime(2026, 11, 9, 9, 40, tzinfo=ET))
    b.snapshot(datetime(2026, 11, 9, 9, 41, tzinfo=ET))                                   # slot 0935
    chain(tmp_path / "cache", "SPY", 1.40, datetime(2026, 11, 9, 10, 29, tzinfo=ET))      # the worker was down; this lands in slot 1005
    b.snapshot(datetime(2026, 11, 9, 10, 31, tzinfo=ET))
    mark = lambda doc: doc["selectedExpiryChainRows"][0]["mark"]
    assert mark(b.chain_at("SPY", datetime(2026, 11, 9, 10, 10, tzinfo=ET))) == 1.00      # not the 10:29 chain
    assert mark(b.chain_at("SPY", datetime(2026, 11, 9, 10, 30, tzinfo=ET))) == 1.40
