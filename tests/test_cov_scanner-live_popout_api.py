"""Integration: what a pop-out scanner window reads.

Each in-app or detached window (/?popout=momx&list=<name>) polls the board of
ITS OWN list through api_server's proxy, side by side with the main board. The
windows only work if two lists polled at once each get their own board, and a
window left open on a list that was since deleted says why instead of
showing another list's rows.
"""
from __future__ import annotations

import threading

from api_harness import running_api
from worker_harness import running_worker


def _board(symbols: list[str], stamp: str) -> dict:
    return {"generatedAt": stamp, "direction": "bull", "universe": symbols,
            "rows": [{"symbol": s, "scanPass": True} for s in symbols], "rest": [], "errors": {}}


def test_two_windows_polling_two_lists_at_once_each_get_their_own_board(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.seed_board("Mag7", _board(["AAPL", "NVDA"], "2026-11-03T15:00:00+00:00"))
        worker.seed_board("Watchlist", _board(["SPY", "QQQ", "IWM"], "2026-11-03T15:00:30+00:00"))
        answers: dict[str, list] = {"Mag7": [], "Watchlist": []}

        def poll(name):
            for _ in range(5):
                answers[name].append(api.get(f"/api/momx-scanner?list={name}"))

        threads = [threading.Thread(target=poll, args=(name,)) for name in answers for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
    assert len(answers["Mag7"]) == len(answers["Watchlist"]) == 10
    for status, body in answers["Mag7"]:
        assert status == 200 and [r["symbol"] for r in body["rows"]] == ["AAPL", "NVDA"]
    for status, body in answers["Watchlist"]:
        assert status == 200 and [r["symbol"] for r in body["rows"]] == ["SPY", "QQQ", "IWM"]


def test_a_window_on_a_deleted_list_is_told_so_not_shown_another_lists_rows(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.seed_board("Mag7", _board(["AAPL"], "2026-11-03T15:00:00+00:00"))
        status, body = api.get("/api/momx-scanner?list=Gone%20List")
    assert status == 400, body
    assert body.get("error") and not body.get("rows")
