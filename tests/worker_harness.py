"""Integration harness for the scanner worker (2026-11-04): the real
momx_worker.Handler on a free port with every file it reads or writes in a
scratch folder, spoken to over real HTTP.

    from worker_harness import running_worker

    def test_board_route(tmp_path, monkeypatch):
        with running_worker(tmp_path, monkeypatch) as worker:
            worker.seed_board("Mag7", {"rows": [...], "generatedAt": "..."})
            status, body = worker.get("/api/momx-scanner?list=Mag7")

To test a route through api_server's proxy as well, pass the worker's port on:
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api: ...

Safe by construction: no warmer thread starts (boards are built only from
what the test seeds), the trader's lists, boards, history, grade tape and
Momo Alert settings are all scratch copies, and tests/live_guard.py refuses
any connection that leaves the PC and any write to the live artifacts/.
"""
from __future__ import annotations

import threading
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any, Mapping

import momx_worker
from http_client import ApiClient
from live_guard import network_guard, write_guard
from momx import board, momo_alert, service

ADMIN_EMAIL = "owner@example.com"   # mark as admin with worker.as_admin()


class WorkerClient(ApiClient):
    def __init__(self, base_url: str, port: int):
        super().__init__(base_url)
        self.port = port

    def seed_board(self, list_name: str, payload: Mapping[str, Any], direction: str = "bull") -> None:
        """Store a finished board on the scratch disk cache, as a build would."""
        service._write_disk(list_name, dict(payload), direction)

    def as_admin(self, monkeypatch, email: str = ADMIN_EMAIL) -> dict:
        """Headers for an admin viewer (the role check reads the user table)."""
        monkeypatch.setattr(momx_worker, "viewer_is_admin", lambda who, db_path=None: str(who or "").lower() == email)
        return {"X-AGX-User": email}


@contextmanager
def running_worker(tmp_path: Path, monkeypatch):
    scratch = Path(tmp_path) / "worker"
    scratch.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv(board.UNIVERSE_PATH_ENV, str(scratch / "universe.json"))
    monkeypatch.setenv(service.BOARD_CACHE_DIR_ENV, str(scratch / "board_cache"))
    monkeypatch.setenv("AGX_MOMX_HISTORY_DIR", str(scratch / "momx_history"))
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(scratch / "momx_grade"))
    monkeypatch.setenv("MOMX_INDUSTRY_CACHE_PATH", str(scratch / "industry_cache.json"))
    monkeypatch.setattr(board.settings.scanner, "default_universe", ["SPY", "QQQ"])
    monkeypatch.setattr(service, "start_warmer", lambda: None)
    monkeypatch.setattr(service, "ASYNC_PERSIST", False)
    monkeypatch.setattr(momo_alert, "_CONFIG_PATH", scratch / "momx_momo_alert.json")
    monkeypatch.setattr(momo_alert, "_USERS_PATH", scratch / "momx_momo_alert_users.json")
    service._STATES.clear()
    momx_worker._ADMIN_CACHE.clear()
    momx_worker._LEDGERS.clear()
    server = momx_worker.ScannerHTTPServer(("127.0.0.1", 0), momx_worker.Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    with ExitStack() as stack:
        stack.enter_context(network_guard())
        stack.enter_context(write_guard())
        try:
            yield WorkerClient(f"http://127.0.0.1:{port}", port)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            service._STATES.clear()
            momx_worker._LEDGERS.clear()
