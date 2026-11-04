"""Integration: the hot sector (sector rotation, momx/sectors.py) reaching the
board over HTTP, and surviving a worker restart.

The rotation book is the one service._build_once runs on every build; here it
runs on a fixture board with FAKE daily bars (no Alpaca), the stamped board is
published the way a build publishes it, and the real worker route serves it.
The 🔥 latch is persisted to a scratch folder: a restart after 11:00 ET, when
no sector may turn hot any more, must still show the morning's hot sector.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import sectors, service
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
TODAY = datetime(2026, 11, 3, tzinfo=ET)
SESSIONS = [TODAY - timedelta(days=n) for n in range(7, 0, -1)]   # seven closes before today

QUANTUM = ["QA", "QB", "QC", "QD"]
BANKS = ["BA", "BB", "BC", "BD"]


def _daily(closes: list[float]) -> list[dict]:
    return [{"time": day.replace(hour=16).timestamp(), "close": close} for day, close in zip(SESSIONS, closes)]


def fake_daily_fetch(calls: list):
    def fetch(symbols):
        calls.append(sorted(symbols))
        out = {"SPY": _daily([100.0] * 7)}
        for symbol in QUANTUM:
            out[symbol] = _daily([10, 10, 10, 10, 11, 12, 14])      # 3-day lead over SPY, and growing
        for symbol in BANKS:
            out[symbol] = _daily([50.0] * 7)
        return out
    return fetch


def _board(quantum_pct=(5.0, 4.0, 3.0, 2.0), quantum_above_vwap=True) -> dict:
    rows = []
    for symbol, pct in zip(QUANTUM, quantum_pct):
        last = 11.0 if quantum_above_vwap else 9.0
        rows.append({"symbol": symbol, "industry": "Quantum", "pctChange": pct, "last": last, "m5": {"vwap": 10.0}, "scanPass": True})
    for symbol, pct in zip(BANKS, (1.0, -1.0, -1.0, -1.0)):
        rows.append({"symbol": symbol, "industry": "Banks", "pctChange": pct, "last": 50.0, "m5": {"vwap": 50.5}, "scanPass": False})
    return {"list": "Watchlist", "generatedAt": "g1", "direction": "bull", "universe": QUANTUM + BANKS,
            "rows": rows, "rest": [], "errors": {}}


def _publish(payload: dict) -> None:
    state = service._state("Watchlist")
    with service._LOCK:
        state.payload, state.bear, state.error = payload, {**payload, "direction": "bear"}, ""


def _by_symbol(body: dict) -> dict:
    return {row["symbol"]: row for row in body["rows"]}


def test_the_hot_sector_and_its_two_leaders_reach_the_board_and_survive_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_SECTOR_ROTATION", "1")
    calls: list = []
    latch_dir = tmp_path / "grade"
    book = sectors.RotationBook(fetch=fake_daily_fetch(calls), background=False, directory=latch_dir)
    monkeypatch.setattr(service, "_SECTORS", book)

    morning = _board()
    service._SECTORS.apply("Watchlist", morning, TODAY.replace(hour=9, minute=40))
    assert calls and "SPY" in calls[0]                           # one daily fetch, benchmark included

    with running_worker(tmp_path, monkeypatch) as worker:
        _publish(morning)
        status, body = worker.get("/api/momx-scanner?list=Watchlist")
        assert status == 200, body
        rows = _by_symbol(body)
        assert rows["QA"]["sectorRotation"]["hot"] is True
        assert rows["QA"]["sectorRotation"]["breadth"] == 1.0
        assert rows["QA"]["sectorRotation"]["rs3"] == 40.0 and rows["QA"]["sectorRotation"]["rs3Prev"] == 20.0
        assert rows["BA"]["sectorRotation"]["hot"] is False
        assert [s for s, r in rows.items() if r.get("hotLeader")] == ["QA", "QB"]
        assert rows["QA"]["hotLeader"]["rank"] == 1 and rows["QB"]["hotLeader"]["rank"] == 2
        assert rows["QA"]["hotLeader"]["sector"] == "Quantum"

        saved = json.loads((latch_dir / "momx_sector_hot" / "2026-11-03.json").read_text(encoding="utf-8"))
        assert saved["boards"]["Watchlist"]["hot"] == ["Quantum"]

        # Worker restart at 11:20 ET: a fresh book, Alpaca now failing. No sector
        # may TURN hot after 11:00, so only the saved latch can keep the flame.
        def dead_fetch(symbols):
            raise ConnectionError("alpaca down")

        restarted = sectors.RotationBook(fetch=dead_fetch, background=False, directory=latch_dir)
        monkeypatch.setattr(service, "_SECTORS", restarted)
        later = _board(quantum_pct=(1.0, 0.8, 0.5, 0.2))
        service._SECTORS.apply("Watchlist", later, TODAY.replace(hour=11, minute=20))
        _publish({**later, "generatedAt": "g2"})
        status, body = worker.get("/api/momx-scanner?list=Watchlist")
    assert status == 200, body
    rows = _by_symbol(body)
    assert rows["QC"]["sectorRotation"]["hot"] is True
    assert [s for s, r in rows.items() if r.get("hotLeader")] == ["QA", "QB"]    # 🔥#1 / 🔥#2 fixed for the day


def test_a_sector_cannot_turn_hot_after_eleven(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_SECTOR_ROTATION", "1")
    book = sectors.RotationBook(fetch=fake_daily_fetch([]), background=False, directory=tmp_path / "grade")
    payload = _board()
    book.apply("Watchlist", payload, TODAY.replace(hour=11, minute=5))
    quantum = payload["rows"][0]["sectorRotation"]
    assert quantum["hot"] is False
    assert quantum["late"] is True                               # shown as moving now, never as 🔥
    assert all(row["hotLeader"] is None for row in payload["rows"])


def test_the_suite_switch_keeps_fixture_boards_off_real_daily_bars(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_SECTOR_ROTATION", "0")
    calls: list = []
    book = sectors.RotationBook(fetch=fake_daily_fetch(calls), background=False, directory=tmp_path / "grade")
    payload = _board()
    book.apply("Watchlist", payload, TODAY.replace(hour=9, minute=40))
    assert calls == []
    assert all("sectorRotation" not in row for row in payload["rows"])
