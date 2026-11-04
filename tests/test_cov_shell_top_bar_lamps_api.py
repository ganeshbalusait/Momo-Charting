"""Integration: what the header's TOS MARKET / TOS TRADING lamps read from
GET /api/schwab/status (coverage 2026-11-04, group "shell").

A lamp is green only when Schwab ACCEPTED the key (a recorded outcome, not the
presence of a token) AND the wire is not blocked. Only an IP-level refusal
(Akamai block or rate limit) may darken it; one bad token must not. Schwab is
faked; the process-wide gate is the real one (conftest resets it per test).
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from api_harness import api_server, running_api
from auth_service import AuthService
from data.schwab_rate_limit import GATE


class FakeSchwab:
    def __init__(self, profile):
        self.config = SimpleNamespace(client_id="k", client_secret="s", redirect_uri="https://127.0.0.1",
                                      token_path=f"cov-tokens/lamp-{profile}.json")

    def connection_status(self):
        return {"configured": True, "credentialsConfigured": True, "hasRefreshToken": True,
                "refreshTokenExpiresAt": "2026-11-09T15:00:00+00:00", "accessTokenValid": True}


@pytest.fixture
def house(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", "owner-lamps@example.com")   # loopback = the admin
    clients = {p: FakeSchwab(p) for p in ("market_data", "trading")}
    monkeypatch.setattr(api_server, "_schwab_oauth_client", lambda user, profile="market_data": clients[profile])
    monkeypatch.setattr(api_server, "_probe_credential_in_background", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "certificate_trusted", lambda: True)
    return clients


def _lamps(api):
    status, body = api.get("/api/schwab/status")
    assert status == 200, body
    return body


def test_an_accepted_key_on_an_open_wire_is_green(house):
    with running_api() as api:
        api_server.CREDENTIAL_HEALTH.record_success("house", "market_data")
        body = _lamps(api)
    assert (body["marketData"]["healthy"], body["marketData"]["transportBlocked"]) == (True, False)
    assert body["trading"]["healthy"] is False, "never verified is not healthy"
    assert body["trading"]["accepted"] is None


def test_an_akamai_block_darkens_an_accepted_key(house):
    with running_api() as api:
        api_server.CREDENTIAL_HEALTH.record_success("house", "market_data")
        GATE.note_edge_block("Access Denied for this IP (test)")
        body = _lamps(api)
    assert body["marketData"]["accepted"] is True
    assert (body["marketData"]["healthy"], body["marketData"]["transportBlocked"]) == (False, True)
    assert "Access Denied" in body["marketData"]["transportDetail"]
    assert body["transport"]["coolingDown"] is True


def test_a_rate_limit_cooldown_also_darkens_it(house):
    with running_api() as api:
        api_server.CREDENTIAL_HEALTH.record_success("house", "market_data")
        GATE.note_rate_limited(30)          # Schwab said 429 with Retry-After: 30
        body = _lamps(api)
    assert body["transport"]["coolingDown"] is True
    assert body["marketData"]["healthy"] is False and body["marketData"]["transportBlocked"] is True


def test_an_auth_failure_elsewhere_does_not_darken_the_house_lamp(house):
    with running_api() as api:
        api_server.CREDENTIAL_HEALTH.record_success("house", "market_data")
        GATE.note_failure("auth", "401 Unauthorized for some user's token")
        body = _lamps(api)
    assert (body["marketData"]["healthy"], body["marketData"]["transportBlocked"]) == (True, False)


def test_a_refused_key_is_never_green_whatever_the_dates_say(house):
    with running_api() as api:
        api_server.CREDENTIAL_HEALTH.record_failure("house", "market_data", "401 Unauthorized: invalid_client")
        body = _lamps(api)
    section = body["marketData"]
    assert section["accepted"] is False and section["healthy"] is False
    assert "invalid_client" in section["acceptedError"]
    assert (section["configured"], section["refreshTokenValid"], section["accessTokenValid"]) == (False, False, False)
