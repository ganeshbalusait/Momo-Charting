"""The guards that keep integration tests off the live app (tests/live_guard.py)
and the two harnesses built on them. If one of these fails, a test could reach
Schwab, the live scanner on :3010, or the trader's real files."""
from __future__ import annotations

import os
import socket
from datetime import datetime, timezone

import pytest

from live_guard import REPO, network_guard, write_guard


def test_nothing_leaves_the_pc():
    with network_guard(), pytest.raises(ConnectionRefusedError, match="may not reach 8.8.8.8:53"):
        socket.create_connection(("8.8.8.8", 53), timeout=2)


@pytest.mark.parametrize("port", [3001, 3002, 3010, 5173])
def test_the_live_servers_on_this_pc_are_off_limits(port):
    with network_guard(), pytest.raises(ConnectionRefusedError, match=f"live app on port {port}"):
        socket.create_connection(("127.0.0.1", port), timeout=2)


def test_a_live_port_can_be_pointed_at_a_test_server():
    listener = socket.create_server(("127.0.0.1", 0))
    try:
        with network_guard({3010: listener.getsockname()[1]}):
            socket.create_connection(("127.0.0.1", 3010), timeout=2).close()
    finally:
        listener.close()


def test_the_guard_comes_off_afterwards():
    with network_guard():
        pass
    assert socket.socket.connect.__qualname__ != "<lambda>"


def test_live_files_cannot_be_written_moved_or_deleted(tmp_path):
    live = REPO / "artifacts" / "live_guard_probe.json"
    scratch = tmp_path / "ok.json"
    with write_guard():
        for attempt in (lambda: open(live, "w"), lambda: live.write_text("x"), lambda: open(REPO / ".env", "a"),
                        lambda: open(REPO / "database" / "trades.db", "r+b")):
            with pytest.raises(PermissionError, match="may not change live files"):
                attempt()
        scratch.write_text("fine")
        with pytest.raises(PermissionError):
            os.replace(scratch, live)
        with pytest.raises(PermissionError):
            os.remove(REPO / "artifacts" / "anything.json")
        assert (REPO / "AGENTS.md").read_text(encoding="utf-8")       # reading is fine
    assert not live.exists()


def _board(*symbols):
    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return {"rows": [{"symbol": s} for s in symbols], "generatedAt": stamp, "universe": list(symbols)}


def test_the_worker_harness_serves_the_seeded_board(tmp_path, monkeypatch):
    from worker_harness import running_worker

    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", _board("AAPL", "NVDA"))
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    assert [row["symbol"] for row in body["rows"]] == ["AAPL", "NVDA"]


def test_the_scanner_proxy_reaches_the_test_worker_never_the_live_one(tmp_path, monkeypatch):
    from api_harness import running_api
    from worker_harness import running_worker

    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.seed_board("Mag7", _board("SPY"))
        status, body = api.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    assert [row["symbol"] for row in body["rows"]] == ["SPY"]


def test_without_a_test_worker_the_proxy_cannot_reach_the_live_one():
    from api_harness import running_api

    with running_api() as api:
        status, body = api.get("/api/momx-scanner?list=Mag7")
    # The proxy turns an unreachable worker into its 'warming' body, never live rows.
    assert not (body or {}).get("rows"), (status, body)
