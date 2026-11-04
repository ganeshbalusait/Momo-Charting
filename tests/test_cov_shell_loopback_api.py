"""Integration: loopback auto-login and the admin-on-this-PC device bypass
(coverage 2026-11-04, group "shell").

"From this PC" is decided by the gateway's X-AGX-Request-Origin stamp, or for
an unstamped request by the loopback address and Host. These tests prove the
grants fire for the trader's own browser and never for a tunnel visitor - the
whole reason the stamp exists. Every test runs on its own empty user database.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
from http_client import ApiClient

OWNER = "owner-loop@example.com"


@pytest.fixture
def auth(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)   # running_api() seeds it as an admin
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    return service


def _status_user(api, headers=None):
    status, body = api.get("/api/auth/status", headers=headers)
    assert status == 200, body
    return body["user"]


def test_an_unstamped_loopback_request_is_the_owner(auth):
    with running_api() as api:
        user = _status_user(api)
        keys, _ = api.get("/api/user/api-keys")
    assert user["email"] == OWNER and user["isAdmin"] is True
    assert keys == 200


def test_the_gateways_local_stamp_is_the_owner(auth):
    with running_api() as api:
        user = _status_user(api, {"X-AGX-Request-Origin": "local"})
    assert user["email"] == OWNER


def test_the_relayed_stamp_is_nobody(auth):
    with running_api() as api:
        user = _status_user(api, RELAYED)
        status, body = api.get("/api/user/api-keys", headers=RELAYED)
    assert user is None
    assert (status, body) == (401, {"error": "Sign in to continue."})


@pytest.mark.parametrize("stamp", ["banana", "LOCALHOST", "relayed,local"])
def test_an_unknown_stamp_is_nobody(auth, stamp):
    with running_api() as api:
        status, _ = api.get("/api/user/api-keys", headers={"X-AGX-Request-Origin": stamp})
    assert status == 401


def test_without_the_env_setting_there_is_no_grant(auth, monkeypatch):
    with running_api() as api:   # seeds the owner first...
        monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL")   # ...then the grant is switched off
        status, body = api.get("/api/admin/users")
    assert (status, body) == (401, {"error": "Sign in to continue."})


def test_an_auto_login_address_with_no_account_is_401_not_500(auth, monkeypatch):
    with running_api() as api:
        monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", "ghost@example.com")
        status, body = api.get("/api/user/api-keys")
        user = _status_user(api)
    assert (status, body) == (401, {"error": "Sign in to continue."})
    assert user is None


def test_a_broken_auto_login_lookup_degrades_to_sign_in_not_an_outage(auth, monkeypatch):
    with running_api() as api:
        def broken(_email):
            raise RuntimeError("database is locked")
        monkeypatch.setattr(auth, "get_user_by_email", broken)
        status, body = api.get("/api/user/api-keys")
    assert (status, body) == (401, {"error": "Sign in to continue."})


def test_admin_new_device_is_waved_through_on_this_pc_and_held_through_the_tunnel(auth):
    with running_api() as api:
        # Device A: the admin's first device, approved automatically.
        first, _ = ApiClient(api.base_url).post(
            "/api/auth/login", {"email": OWNER, "password": TEST_PASSWORD}, headers=RELAYED)
        # Device B through the tunnel: held for approval, gets a device cookie only.
        tunnel = ApiClient(api.base_url)
        held, held_body = tunnel.post("/api/auth/login", {"email": OWNER, "password": TEST_PASSWORD}, headers=RELAYED)
        held_cookies = [c.split("=", 1)[0] for c in tunnel.last_headers.get_all("Set-Cookie") or []]
        # Device C on this PC: no cookie either, but the bypass approves it.
        local = ApiClient(api.base_url)
        here, here_body = local.post("/api/auth/login", {"email": OWNER, "password": TEST_PASSWORD},
                                     headers={"X-AGX-Request-Origin": "local"})
    assert first == 200
    assert held == 403 and held_body["devicePending"] is True
    assert "waiting for admin approval" in held_body["error"]
    assert held_cookies == ["agentic_device"]
    assert here == 200, here_body
    assert {"agentic_session", "agentic_device"} <= set(local.cookies)
    pending = auth.list_devices(here_body["user"])["pendingRequests"]
    assert len(pending) == 1, "only the tunnel device is still waiting"


@pytest.mark.xfail(strict=True, reason=(
    "BUG: /api/auth/status caches its answer by (session cookie, Access email) only, "
    "not by where the request came from. For 8 s after the owner's own browser asks, "
    "a cookie-less tunnel visitor is answered with the OWNER's account."))
def test_a_tunnel_visitor_never_gets_the_owners_cached_status(auth):
    with running_api() as api:
        assert _status_user(api)["email"] == OWNER          # the trader's own browser
        stranger = _status_user(ApiClient(api.base_url), RELAYED)   # a visitor, same second
    assert stranger is None
