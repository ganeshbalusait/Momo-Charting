"""Unit: the Momo Alert evaluator (momx_worker._momo_evaluate).

Every ~10 s the worker diffs each list's newest COMPLETED build against the
previous one, for both directions, against every account's own settings, and
pushes each account's alerts to its own phone topic - but only inside that
account's push window. The board, the clock, the configs and ntfy are fakes.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import momx_worker
from momx import momo_alert

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc).timestamp()     # 10:00 ET
TAPE = datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat()


def _row(symbol: str, rvol5: float, passed: bool = True) -> dict:
    return {"symbol": symbol, "scanPass": passed, "pctChange": 2.0, "rvol": {"5m": {"value": rvol5}}}


def _board(stamp: str, rows: list[dict], **extra) -> dict:
    return {"generatedAt": stamp, "tapeAsOf": TAPE, "rows": rows, "rest": [], **extra}


@pytest.fixture
def world(monkeypatch, tmp_path):
    boards: dict[tuple[str, str], dict] = {}
    pushes: list[tuple[list, str]] = []
    window = {"open": True}

    def snapshot(name, direction="bull"):
        board = boards.get((name, direction))
        if board is None:
            return {"warming": True, "rows": [], "list": name}
        return board

    monkeypatch.setattr(momx_worker.service, "snapshot", snapshot)
    monkeypatch.setattr(momx_worker, "_MOMO", {"stamps": {}, "prev": {}, "users": {}})
    monkeypatch.setattr(momo_alert, "_USERS_PATH", tmp_path / "momo_users.json")
    monkeypatch.setattr(momo_alert, "_CONFIG_PATH", tmp_path / "momo.json")
    monkeypatch.setattr(momo_alert, "push_ntfy", lambda alerts, topic, **kw: pushes.append((list(alerts), topic)))
    monkeypatch.setattr(momo_alert, "push_window_allows", lambda config, now_et=None: window["open"])
    monkeypatch.setattr(momo_alert, "time", SimpleNamespace(time=lambda: NOW))
    momo_alert.save_user_config("a@x.com", {"timeframes": {"5m": {"armed": True, "threshold": 3}}, "ntfyTopic": "agx-momo-a"})
    momo_alert.save_user_config("b@x.com", {"timeframes": {"5m": {"armed": True, "threshold": 5}}, "ntfyTopic": "agx-momo-b"})
    return SimpleNamespace(boards=boards, pushes=pushes, window=window)


def _alerts(email: str) -> list[dict]:
    return momx_worker._MOMO["users"].get(email, {}).get("alerts", [])


def test_a_crossing_alerts_each_account_on_its_own_threshold_and_pushes_to_its_own_topic(world):
    world.boards[("Watchlist", "bull")] = _board("b1", [_row("AAA", 1.0), _row("BBB", 1.0)])
    momx_worker._momo_evaluate()
    assert world.pushes == [] and _alerts("a@x.com") == []        # first build = baseline only

    world.boards[("Watchlist", "bull")] = _board("b2", [_row("AAA", 3.4), _row("BBB", 6.0)])
    momx_worker._momo_evaluate()
    assert sorted(a["symbol"] for a in _alerts("a@x.com")) == ["AAA", "BBB"]
    assert [a["symbol"] for a in _alerts("b@x.com")] == ["BBB"]   # b's bar is 5x
    assert all(a["list"] == "Watchlist" for a in _alerts("a@x.com"))
    # The shared default profile has no topic: push_ntfy("") is a no-op by contract.
    sent = sorted((topic, sorted(a["symbol"] for a in alerts)) for alerts, topic in world.pushes if topic)
    assert sent == [("agx-momo-a", ["AAA", "BBB"]), ("agx-momo-b", ["BBB"])]

    # The 10 s loop runs again before the next build: nothing new, nothing re-pushed.
    pushed = len(world.pushes)
    momx_worker._momo_evaluate()
    assert len(world.pushes) == pushed and len(_alerts("a@x.com")) == 2


def test_warming_and_from_disk_boards_never_become_the_baseline(world):
    world.boards[("Watchlist", "bull")] = _board("d1", [_row("AAA", 1.0)], fromDisk=True)
    momx_worker._momo_evaluate()
    world.boards[("Watchlist", "bull")] = _board("d2", [_row("AAA", 4.0)])
    momx_worker._momo_evaluate()
    # The disk copy after a restart was not a live build, so d2 is the cold start.
    assert _alerts("a@x.com") == [] and world.pushes == []
    assert momx_worker._MOMO["stamps"] == {"Watchlist": "d2"}


def test_the_bear_board_is_keyed_and_labelled_list_bear(world):
    world.boards[("Mag7", "bear")] = _board("m1", [_row("DN", 1.0)])
    momx_worker._momo_evaluate()
    world.boards[("Mag7", "bear")] = _board("m2", [_row("DN", 3.5)])
    momx_worker._momo_evaluate()
    assert "Mag7 BEAR" in momx_worker._MOMO["stamps"]
    assert [(a["symbol"], a["list"]) for a in _alerts("a@x.com")] == [("DN", "Mag7 BEAR")]


def test_outside_the_push_window_alerts_are_kept_in_the_app_but_no_phone_buzzes(world):
    world.window["open"] = False
    world.boards[("Watchlist", "bull")] = _board("w1", [_row("AAA", 1.0)])
    momx_worker._momo_evaluate()
    world.boards[("Watchlist", "bull")] = _board("w2", [_row("AAA", 3.2)])
    momx_worker._momo_evaluate()
    assert [a["symbol"] for a in _alerts("a@x.com")] == ["AAA"]
    assert world.pushes == []


def test_an_account_keeps_at_most_twenty_recent_alerts_newest_first(world):
    names = [f"S{i:02d}" for i in range(25)]
    world.boards[("Watchlist", "bull")] = _board("c1", [_row(s, 1.0) for s in names])
    momx_worker._momo_evaluate()
    world.boards[("Watchlist", "bull")] = _board("c2", [_row(s, 4.0) for s in names])
    momx_worker._momo_evaluate()
    held = _alerts("a@x.com")
    assert len(held) == momx_worker._MOMO_ALERT_CAP == 20
    assert held[0]["symbol"] == "S24"                            # newest first
    assert len(world.pushes[0][0]) == 25                         # the phone still got every crossing


def test_a_stale_tape_never_alarms(world, monkeypatch):
    monkeypatch.setattr(momo_alert, "time", SimpleNamespace(time=lambda: NOW + momo_alert.FRESH_TAPE_SECONDS + 600))
    world.boards[("Watchlist", "bull")] = _board("s1", [_row("AAA", 1.0)])
    momx_worker._momo_evaluate()
    world.boards[("Watchlist", "bull")] = _board("s2", [_row("AAA", 9.0)])
    momx_worker._momo_evaluate()
    assert _alerts("a@x.com") == [] and world.pushes == []
