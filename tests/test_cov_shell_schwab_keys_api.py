"""Integration: the "My Schwab Market & Optional Trading APIs" card through the
real /api/schwab/settings, /status and /test-connection routes over HTTP
(coverage 2026-11-04, group "shell").

Schwab itself is faked: every client hands back a canned status and a canned
test result, so nothing leaves the PC. running_api() already moves .env and the
credential-health file to a scratch folder; the process environment and the
shared settings object are restored by monkeypatch after every test.
"""
from __future__ import annotations

import json
import time
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from dotenv import dotenv_values

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService

ADMIN = "admin-schwab@example.com"
MEMBER = "member-schwab@example.com"
AS_ADMIN = {**RELAYED, "X-AGX-Access-Email": ADMIN}
AS_MEMBER = {**RELAYED, "X-AGX-Access-Email": MEMBER}
HOUSE_SECRET = "house-secret-value-0001"


class FakeSchwab:
    """Stands in for SchwabClient: a status, a test result, no network."""

    def __init__(self, profile, *, configured=True, result=None, error=None, owner="house"):
        self.profile = profile
        self.owner = owner
        self.config = SimpleNamespace(client_id="key-" + profile, client_secret="secret", redirect_uri="https://127.0.0.1",
                                      token_path=f"cov-tokens/{owner}-{profile}.json")
        self.configured = configured
        self.result = result or {"connected": True, "verifiedAt": "2026-11-04T15:00:00+00:00", "symbol": "SPY"}
        self.error = error
        self.tested = 0

    def connection_status(self):
        return {"configured": self.configured, "credentialsConfigured": self.configured,
                "clientIdMasked": "key-...masked" if self.configured else "", "hasRefreshToken": self.configured,
                "refreshTokenExpiresAt": "2026-11-09T15:00:00+00:00" if self.configured else None,
                "tokenPath": self.config.token_path}

    def test_connection(self):
        self.tested += 1
        if self.error is not None:
            raise self.error
        return dict(self.result)


def _build_world(tmp_path, monkeypatch, *, fake_resolver=True, fake_probe=True):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)
    for email, role in ((ADMIN, "admin"), (MEMBER, "user")):
        service._create_user_record(email, TEST_PASSWORD, display_name=role, role=role, must_change_password=False)
    # The house save writes os.environ and the shared settings object: pin
    # them so monkeypatch restores the trader's real values afterwards.
    for name in ("SCHWAB_CLIENT_ID", "SCHWAB_CLIENT_SECRET", "SCHWAB_TRADING_CLIENT_ID", "SCHWAB_TRADING_CLIENT_SECRET"):
        monkeypatch.setenv(name, "before-" + name.lower())
    monkeypatch.setattr(api_server.settings.schwab, "client_id", "before-house-id")
    monkeypatch.setattr(api_server.settings.schwab, "client_secret", "before-house-secret")
    house = {p: FakeSchwab(p) for p in ("market_data", "trading")}
    personal = {}
    probes = []
    monkeypatch.setattr(api_server, "_schwab_client_for_profile", lambda profile: house["trading" if profile == "trading" else "market_data"])
    if fake_resolver:
        monkeypatch.setattr(api_server, "_schwab_oauth_client",
                            lambda user, profile="market_data": house[profile] if (user or {}).get("isAdmin") else personal.get(profile))
    if fake_probe:
        monkeypatch.setattr(api_server, "_probe_credential_in_background", lambda client, scope, profile: probes.append((scope, profile)))
    monkeypatch.setattr(api_server, "certificate_trusted", lambda: True)
    return SimpleNamespace(auth=service, house=house, personal=personal, probes=probes)


@pytest.fixture
def world(tmp_path, monkeypatch):
    return _build_world(tmp_path, monkeypatch)


def test_an_admin_save_writes_the_house_env_and_never_echoes_the_secret(world):
    with running_api() as api:
        status, body = api.post("/api/schwab/settings", {"clientId": "house-key-0001", "clientSecret": HOUSE_SECRET}, headers=AS_ADMIN)
        written = dotenv_values(api_server.ENV_PATH)
    assert status == 200, body
    assert body["saved"] is True
    assert (written["SCHWAB_CLIENT_ID"], written["SCHWAB_CLIENT_SECRET"]) == ("house-key-0001", HOUSE_SECRET)
    assert api_server.settings.schwab.client_id == "house-key-0001"
    assert HOUSE_SECRET not in json.dumps(body)


def test_blank_trading_fields_leave_the_stored_trading_pair_alone(world):
    with running_api() as api:
        api_server.ENV_PATH.write_text("SCHWAB_TRADING_CLIENT_ID=old-trading-id\nSCHWAB_TRADING_CLIENT_SECRET=old-trading-secret\n")
        status, _ = api.post("/api/schwab/settings", {"clientId": "house-key-0002", "clientSecret": HOUSE_SECRET,
                                                      "tradingClientId": "", "tradingClientSecret": "  "}, headers=AS_ADMIN)
        written = dotenv_values(api_server.ENV_PATH)
    assert status == 200
    assert (written["SCHWAB_TRADING_CLIENT_ID"], written["SCHWAB_TRADING_CLIENT_SECRET"]) == ("old-trading-id", "old-trading-secret")


def test_with_no_key_anywhere_the_save_is_refused(world, monkeypatch):
    monkeypatch.setattr(api_server.settings.schwab, "client_id", "")
    monkeypatch.setattr(api_server.settings.schwab, "client_secret", "")
    with running_api() as api:
        status, body = api.post("/api/schwab/settings", {"clientId": "", "clientSecret": ""}, headers=AS_ADMIN)
        written = dotenv_values(api_server.ENV_PATH)
    assert (status, body) == (400, {"error": "Schwab app key and secret are required."})
    assert "SCHWAB_CLIENT_ID" not in written


def test_a_members_save_goes_to_their_vault_and_never_touches_the_house_env(world, monkeypatch):
    member_id = world.auth.get_user_by_email(MEMBER)["id"]
    dropped = []
    monkeypatch.setattr(api_server.USER_SCHWAB_CLIENTS, "invalidate", lambda uid, *a, **k: dropped.append(uid))
    with running_api() as api:
        status, body = api.post("/api/schwab/settings", {"clientId": "member-key-000111222", "clientSecret": "member-secret-1"},
                                headers=AS_MEMBER)
        written = dotenv_values(api_server.ENV_PATH)
    assert status == 200, body
    assert (body["saved"], body["scope"]) == (["market_data"], "user")
    assert body["providers"]["schwabMarketData"] == {"configured": True, "clientIdMasked": "memb...111222", "authenticated": False}
    assert "member-secret-1" not in json.dumps(body)
    assert written == {}, "a member's save must never reach the house .env"
    assert api_server.settings.schwab.client_id == "before-house-id"
    assert world.auth.get_provider_credentials(member_id, "schwab_market_data")["client_secret"] == "member-secret-1"
    assert dropped == [member_id]


def test_a_member_with_no_app_is_not_shown_the_house_connection(tmp_path, monkeypatch):
    _build_world(tmp_path, monkeypatch, fake_resolver=False)   # the real, non-falling-back resolver
    with running_api() as api:
        status, body = api.get("/api/schwab/status", headers=AS_MEMBER)
    assert status == 200
    for section in (body["marketData"], body["trading"]):
        assert section["credentialsConfigured"] is False
        assert section["refreshTokenExpiresAt"] is None and section["clientIdMasked"] == ""
        assert section["healthy"] is False


def test_test_connection_marks_the_tested_profile_connected(world):
    with running_api() as api:
        market, market_body = api.post("/api/schwab/test-connection?profile=market_data", headers=AS_ADMIN)
        trading, trading_body = api.post("/api/schwab/test-connection?profile=trading", headers=AS_ADMIN)
    assert market == 200 and trading == 200
    assert market_body["marketData"]["connected"] is True and market_body["marketData"]["verifiedAt"]
    assert market_body["marketData"]["accepted"] is True, "a passing test is recorded as an accepted key"
    assert trading_body["trading"]["connected"] is True
    assert (world.house["market_data"].tested, world.house["trading"].tested) == (1, 1)


def test_an_akamai_html_refusal_is_reduced_to_plain_text(world):
    page = ("<html><head><title>Access Denied</title></head><body><h1>Access Denied</h1>"
            "You don't have permission to access this server. Reference #18.6f2e1c17.1730730000.abc</body></html>")
    world.house["market_data"].error = RuntimeError("403 Forbidden: " + page)
    with running_api() as api:
        status, body = api.post("/api/schwab/test-connection?profile=market_data", headers=AS_ADMIN)
    assert status == 502
    assert "<html" not in body["error"].lower() and "<h1>" not in body["error"]
    assert "Access Denied" in body["error"] and "Reference #18.6f2e1c17" in body["error"]
    assert body["code"] == "schwab_connection_failed"
    assert body["marketData"]["connected"] is False


@pytest.mark.xfail(strict=True, reason=(
    "BUG: /api/schwab/test-connection always tests the HOUSE Schwab app "
    "(_schwab_client_for_profile) but records the verdict under the caller's own scope, "
    "so a member's lamp can turn green on the owner's key."))
def test_a_members_test_uses_their_own_app(world, monkeypatch):
    member_app = FakeSchwab("market_data", owner="member")
    world.personal["market_data"] = member_app
    monkeypatch.setattr(api_server.USER_SCHWAB_CLIENTS, "for_user", lambda user, profile="market_data": world.personal.get(profile))
    with running_api() as api:
        api.post("/api/schwab/test-connection?profile=market_data", headers=AS_MEMBER)
    assert world.house["market_data"].tested == 0, "the owner's key was tested on a member's behalf"
    assert member_app.tested == 1


def test_status_polls_ask_for_one_background_probe_per_unverified_profile(world):
    with running_api() as api:
        for _ in range(3):
            status, body = api.get("/api/schwab/status", headers=AS_ADMIN)
            assert status == 200
    # The real probe claims once per profile; the faked launcher records each claim attempt.
    assert set(world.probes) == {("house", "market_data"), ("house", "trading")}
    assert body["marketData"]["accepted"] is None and body["marketData"]["healthy"] is False


def test_the_real_probe_runs_once_off_the_request_thread(tmp_path, monkeypatch):
    world = _build_world(tmp_path, monkeypatch, fake_probe=False)
    house = world.house
    with running_api() as api:
        for _ in range(4):
            api.get("/api/schwab/status", headers=AS_ADMIN)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (house["market_data"].tested, house["trading"].tested) != (1, 1):
            time.sleep(0.05)
        time.sleep(0.2)   # let the probe threads record their verdicts
        _, after = api.get("/api/schwab/status", headers=AS_ADMIN)
    assert (house["market_data"].tested, house["trading"].tested) == (1, 1)
    assert after["marketData"]["accepted"] is True and after["marketData"]["healthy"] is True
