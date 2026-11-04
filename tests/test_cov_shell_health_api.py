"""Integration: the health probes the watchdog and tooling poll (coverage
2026-11-04, group "shell"): api_server's public /api/health and the scanner
worker's /healthz, each served by its real handler over HTTP. The gateway's
/api/gateway-health is covered in test_cov_shell_gateway_tunnel_api.py.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, api_server, running_api
from auth_service import AuthService
from worker_harness import running_worker

SECRETS = {
    "SCHWAB_CLIENT_SECRET": "cov-schwab-secret-should-never-show",
    "SCHWAB_TRADING_CLIENT_SECRET": "cov-trading-secret-should-never-show",
    "ALPACA_SECRET_KEY": "cov-alpaca-secret-should-never-show",
    "TRADIER_ACCESS_TOKEN": "cov-tradier-token-should-never-show",
}


@pytest.fixture(autouse=True)
def _scratch_auth(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)


class DegradedState:
    def _runtime_health_payload(self):
        return {"enabled": True, "status": "Degraded", "components": {"scanner": "stalled"}}

    def _board_prewarm_status_payload(self):
        return {"runs": 3, "lastRunAt": "2026-11-04T14:00:00Z"}


def test_health_is_public_and_carries_every_section(monkeypatch):
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    with running_api() as api:
        status, body = api.get("/api/health", headers=RELAYED)
    assert status == 200
    assert isinstance(body["ok"], bool)
    assert {"ok", "runtime", "marketStream", "alpacaStream", "threads", "chartPrewarm"} <= set(body)
    assert body["threads"]["total"] >= 1 and isinstance(body["threads"]["byName"], dict)
    text = json.dumps(body).lower()
    for value in SECRETS.values():
        assert value not in text, "a secret leaked into the public health probe"
    assert "password" not in text


def test_a_degraded_runtime_reports_ok_false_but_still_answers_200():
    with running_api(state=DegradedState()) as api:
        status, body = api.get("/api/health", headers=RELAYED)
    assert status == 200, "the watchdog must get an answer, not an error page"
    assert body["ok"] is False
    assert body["runtime"]["status"] == "Degraded"
    assert body["chartPrewarm"] == {"runs": 3, "lastRunAt": "2026-11-04T14:00:00Z"}


def test_an_error_runtime_is_also_not_ok():
    state = DegradedState()
    state._runtime_health_payload = lambda: {"status": "Error", "components": {}}
    with running_api(state=state) as api:
        _, body = api.get("/api/health")
    assert body["ok"] is False


def test_the_scanner_worker_answers_its_own_health_probe(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/healthz")
        missing, _ = worker.get("/no-such-route")
    assert (status, body) == (200, {"ok": True})
    assert missing == 404
