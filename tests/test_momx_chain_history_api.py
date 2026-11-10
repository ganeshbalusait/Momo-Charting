"""Integration: GET /api/momx-scanner/chain-history through the real worker handler and the api_server proxy (2026-11-10)."""
from __future__ import annotations

from datetime import timedelta

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chain_history
from test_momx_chain_history import MON, chain
from worker_harness import running_worker

OWNER = "owner-chain-history@example.com"
VIEWER = "viewer-chain-history@example.com"
ROUTE = "/api/momx-scanner/chain-history"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


def test_admin_reads_what_is_on_file_through_the_proxy_and_others_are_refused(tmp_path, monkeypatch):
    book = chain_history.ChainHistory(tmp_path / "cache", tmp_path / "history")
    chain(tmp_path / "cache", "SPY", 1.00, MON - timedelta(minutes=3))
    book.snapshot(MON)
    monkeypatch.setattr(chain_history, "_BOOK", book)
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.as_admin(monkeypatch, email=OWNER)
        status, body = api.get(ROUTE, headers={ACCESS_EMAIL_HEADER: OWNER})
        direct, _ = worker.get(ROUTE, headers={"X-AGX-User": VIEWER})
    assert status == 200 and direct == 403
    assert (body["days"], body["snapshots"], body["chains"], body["through"], body["keepDays"]) == (1, 1, 1, "2026-11-09", 30)
    assert body["last"]["slot"] == "1005"
