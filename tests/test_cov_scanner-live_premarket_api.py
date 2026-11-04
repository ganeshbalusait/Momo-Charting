"""Integration: the Premarket page's scanner route, GET /api/premarket-scanner.

The real route and the real row builder (premarket_scanner.premarket_scan_row)
run over fixture chart tapes handed out by a fake DashboardState._touch_chart_tail,
with the ET clock frozen, the Schwab stream empty, the 30-day archive in a
scratch folder and the phone push recorded instead of sent.

What the page relies on:
  * rows sorted by %Chg descending (TOS order), each with its arrival time;
  * readySymbols / pendingSymbols / staleSymbols that drive the LIVE TAPES card;
  * one build at a time - concurrent polls share it;
  * no tail refreshes after 10:00 ET (a polling tab must not drive the CPU).
"""
from __future__ import annotations

import functools
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import premarket_scanner_history as archive
from api_harness import api_server, running_api

ET = ZoneInfo("America/New_York")
DAY = datetime(2026, 11, 3, tzinfo=ET)                           # a Tuesday


def _frozen(at: datetime):
    class Frozen(datetime):
        @classmethod
        def now(cls, tz=None):
            return at.astimezone(tz) if tz is not None else at.replace(tzinfo=None)
    return Frozen


def _tape(change_pct: float, *, today: bool = True) -> dict:
    """Yesterday's 15:55 close at 100, then (optionally) today's 04:00-07:25 bars
    ending at 100 * (1 + change_pct%), plus a cyan CALLD printed at 05:00 ET."""
    bars = [{"time": int(datetime(2026, 11, 2, 15, 55, tzinfo=ET).timestamp()),
             "open": 100.0, "high": 100.2, "low": 99.8, "close": 100.0, "volume": 5_000}]
    if today:
        last = round(100.0 * (1 + change_pct / 100), 2)
        for i in range(42):
            t = DAY.replace(hour=4).timestamp() + i * 300
            price = 100.0 + (last - 100.0) * (i + 1) / 42
            bars.append({"time": int(t), "open": price, "high": price + 0.1, "low": price - 0.1, "close": price, "volume": 1_000})
        bars[-1]["close"] = last
    signals = [{"family": "ganesh920", "direction": "CALL", "label": "CALLD",
                "time": int(DAY.replace(hour=5).timestamp())}] if today else []
    return {"bars": bars, "studyBars": list(bars), "dailyBars": [],
            "ganeshHigherTimeframeSignals": {"signals": signals, "historyReady": True}}


@pytest.fixture
def premarket(monkeypatch, tmp_path):
    tapes = {"NVDA": _tape(8.0), "AAPL": _tape(5.0), "MSFT": _tape(-2.0), "TSLA": _tape(0.0, today=False),
             "META": None, "AMZN": {"bars": []}, "AVGO": {"bars": []}, "GOOGL": {"bars": []}, "NFLX": {"bars": []}}
    touched: list[tuple[str, bool]] = []
    pushes: list[tuple[str, str]] = []
    builds = {"count": 0}

    def touch(symbol, allow_refresh=True):
        touched.append((symbol, allow_refresh))
        return tapes.get(symbol)

    state = api_server.STATE
    real_locked = type(state)._premarket_scanner_payload_locked

    def counted(self):
        builds["count"] += 1
        time.sleep(0.3)                                          # long enough for a second poll to arrive
        return real_locked(self)

    monkeypatch.setattr(state, "_touch_chart_tail", touch, raising=False)
    monkeypatch.setattr(state, "_ganesh_signal_payload_for_chart", lambda *a, **k: {"historyReady": False}, raising=False)
    monkeypatch.setattr(type(state), "_premarket_scanner_payload_locked", counted)
    for name, value in (("_premarket_scanner_response", None), ("_premarket_scan_memo", {}),
                        ("_premarket_ganesh_cache", {}), ("_premarket_scanner_build_gate", None),
                        ("_premarket_backfill_error", ""), ("_premarket_backfill_state", "")):
        monkeypatch.setattr(state, name, value, raising=False)
    monkeypatch.setattr(api_server, "MARKET_STREAM", SimpleNamespace(chart_history=lambda symbol: []))
    monkeypatch.setattr(api_server, "premarket_history_record", functools.partial(archive.record_rows, directory=tmp_path / "pm"))
    monkeypatch.setattr(api_server, "premarket_history_first_seen", functools.partial(archive.first_seen_map, directory=tmp_path / "pm"))
    monkeypatch.setattr(api_server, "_push_phone_notification", lambda title, body, **kw: pushes.append((title, body)))

    def at(hour, minute):
        monkeypatch.setattr(api_server, "datetime", _frozen(DAY.replace(hour=hour, minute=minute)))

    return SimpleNamespace(touched=touched, pushes=pushes, builds=builds, at=at, archive=tmp_path / "pm")


def test_at_0730_rows_come_sorted_by_change_with_the_tape_states_the_cards_show(premarket):
    premarket.at(7, 30)
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner")
    assert status == 200, body
    assert [r["symbol"] for r in body["rows"]] == ["NVDA", "AAPL", "MSFT"]
    assert [r["changePct"] for r in body["rows"]] == [8.0, 5.0, -2.0]
    assert body["rows"][0]["signalsCyanHigher"] == ["CALLD"]
    assert body["matchCount"] == 3 and body["status"] == "READY"
    assert sorted(body["readySymbols"]) == ["AAPL", "MSFT", "NVDA"]
    assert body["staleSymbols"] == ["TSLA"]                      # yesterday only: never scanned as "no setups"
    assert sorted(body["pendingSymbols"]) == ["AMZN", "AVGO", "GOOGL", "META", "NFLX"]
    assert body["date"] == "2026-11-03" and "No data yet today for TSLA" in body["message"]
    assert all(r["firstSeenAt"].startswith("2026-11-03T07:30") for r in body["rows"])   # stamped by the archive
    assert all(refresh is True for _, refresh in premarket.touched)
    assert premarket.pushes == []                                # before 09:00 no phone buzz
    assert archive.load_history(premarket.archive)[0]["date"] == "2026-11-03"


def test_two_polls_at_once_share_one_build_and_get_the_same_answer(premarket):
    premarket.at(7, 30)
    answers: list = []
    with running_api() as api:
        threads = [threading.Thread(target=lambda: answers.append(api.get("/api/premarket-scanner"))) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    assert len(answers) == 2 and all(status == 200 for status, _ in answers)
    assert premarket.builds["count"] == 1
    assert answers[0][1]["generatedAt"] == answers[1][1]["generatedAt"]
    assert [r["symbol"] for r in answers[0][1]["rows"]] == [r["symbol"] for r in answers[1][1]["rows"]]


def test_after_ten_the_table_is_served_without_kicking_any_tail_refresh(premarket):
    premarket.at(10, 30)
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner")
    assert status == 200, body
    assert premarket.touched and all(refresh is False for _, refresh in premarket.touched)
    assert [r["symbol"] for r in body["rows"]] == ["NVDA", "AAPL", "MSFT"]
    # First seen after 09:00 ET: one phone push naming the new signals.
    assert len(premarket.pushes) == 1
    title, text = premarket.pushes[0]
    assert title == "Premarket: new signal" and "NVDA +8.0%" in text and "10:30 ET" in text


def test_on_a_saturday_nothing_is_refreshed_either(premarket, monkeypatch):
    monkeypatch.setattr(api_server, "datetime", _frozen(datetime(2026, 11, 7, 7, 30, tzinfo=ET)))
    with running_api() as api:
        status, body = api.get("/api/premarket-scanner")
    assert status == 200, body
    assert premarket.touched and all(refresh is False for _, refresh in premarket.touched)
