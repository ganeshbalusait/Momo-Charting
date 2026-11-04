"""Integration: MY ACCOUNT password change through POST /api/auth/change-password
over HTTP (coverage 2026-11-04, group "shell").

A change must sign every session out (the old cookie stops working) and the
new password must then work; a refused change must leave the session alive.
All requests are relayed tunnel traffic with a real session cookie.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
from http_client import ApiClient

MEMBER = "member-pw@example.com"
NEW_PASSWORD = "Brand-New-Pass-42"
CHANGE = "/api/auth/change-password"


@pytest.fixture
def auth(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    service._create_user_record(MEMBER, TEST_PASSWORD, display_name="Member", role="user", must_change_password=True)
    return service


def _signed_in(api):
    client = ApiClient(api.base_url)
    status, body = client.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
    assert status == 200, body
    return client, body["user"]


def test_a_change_signs_out_every_session_and_the_new_password_works(auth):
    with running_api() as api:
        client, user = _signed_in(api)
        old_cookie = "; ".join(f"{k}={v}" for k, v in client.cookies.items())
        status, body = client.post(CHANGE, {"currentPassword": TEST_PASSWORD, "newPassword": NEW_PASSWORD}, headers=RELAYED)
        cleared = [c for c in client.last_headers.get_all("Set-Cookie") or [] if c.startswith("agentic_session=")]
        replay, _ = ApiClient(api.base_url).get("/api/user/api-keys", headers={**RELAYED, "Cookie": old_cookie})
        old_login, _ = ApiClient(api.base_url).post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        # Same browser (device cookie kept), new password.
        client.cookies.pop("agentic_session", None)
        new_login, new_body = client.post("/api/auth/login", {"email": MEMBER, "password": NEW_PASSWORD}, headers=RELAYED)
    assert user["mustChangePassword"] is True, "the temporary-password hold starts set"
    assert (status, body) == (200, {"ok": True})
    assert cleared and "Max-Age=0" in cleared[0]
    assert replay == 401
    assert old_login == 400
    assert new_login == 200, new_body
    assert new_body["user"]["mustChangePassword"] is False, "changing it lifts the hold"


def test_a_wrong_current_password_is_400_and_the_session_survives(auth):
    with running_api() as api:
        client, _ = _signed_in(api)
        status, body = client.post(CHANGE, {"currentPassword": "Not-The-One-1", "newPassword": NEW_PASSWORD}, headers=RELAYED)
        still, _ = client.get("/api/user/api-keys", headers=RELAYED)
    assert (status, body) == (400, {"error": "Current password is incorrect."})
    assert still == 200


@pytest.mark.parametrize("weak, rule", [
    ("short1A", "at least 10 characters"),
    ("alllowercase123", "upper-case letter"),
    ("ALLUPPERCASE123", "lower-case letter"),
    ("NoDigitsAtAllHere", "include a number"),
])
def test_a_weak_new_password_is_refused_with_the_rule(auth, weak, rule):
    with running_api() as api:
        client, _ = _signed_in(api)
        status, body = client.post(CHANGE, {"currentPassword": TEST_PASSWORD, "newPassword": weak}, headers=RELAYED)
        still, _ = client.get("/api/user/api-keys", headers=RELAYED)
    assert status == 400 and rule in body["error"]
    assert still == 200


def test_signed_out_change_is_401(auth):
    with running_api() as api:
        status, body = ApiClient(api.base_url).post(
            CHANGE, {"currentPassword": TEST_PASSWORD, "newPassword": NEW_PASSWORD}, headers=RELAYED)
    assert (status, body) == (401, {"error": "Sign in to continue."})


def test_a_cloudflare_only_account_has_no_password_to_change(auth):
    auth._create_user_record("cf-only@example.com", "", display_name="CF", role="user", must_change_password=False)
    with running_api() as api:
        headers = {**RELAYED, "X-AGX-Access-Email": "cf-only@example.com"}
        _, status_body = api.get("/api/auth/status", headers=headers)
        status, body = api.post(CHANGE, {"currentPassword": "", "newPassword": NEW_PASSWORD}, headers=headers)
    assert status_body["user"]["hasPassword"] is False
    assert status == 400 and body["error"] == "Current password is incorrect."
