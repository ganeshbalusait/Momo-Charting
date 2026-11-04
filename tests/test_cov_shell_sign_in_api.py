"""Integration: sign-in, sign-out and owner setup through the real auth routes
over HTTP (coverage 2026-11-04, group "shell").

Every request here is stamped as a tunnel visitor (relayed) unless the test is
about this PC, because an unstamped loopback request is auto-signed-in as
LOCAL_AUTO_LOGIN_EMAIL and would prove nothing. Each test gets its own empty
user database, so accounts and sessions never leak between tests.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
from http_client import ApiClient

OWNER = "owner-cov@example.com"
MEMBER = "member-cov@example.com"


@pytest.fixture
def auth(tmp_path, monkeypatch):
    """A fresh, empty user database behind auth_service_instance()."""
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
    monkeypatch.delenv("ADMIN_BOOTSTRAP_TOKEN", raising=False)
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    return service


def _add(service, email, role="user", password=TEST_PASSWORD, active=True):
    user = service._create_user_record(email, password, display_name="Cov Trader", role=role,
                                       must_change_password=False)
    if not active:
        service.set_user_active(user["id"], False, actor={"id": "someone-else", "role": "admin"})
    return user


def _set_cookies(api) -> dict[str, str]:
    """name -> full Set-Cookie header from the last response."""
    out = {}
    for header in (api.last_headers.get_all("Set-Cookie") or []):
        out[header.split("=", 1)[0].strip()] = header
    return out


def test_login_sets_both_cookies_httponly_and_lax(auth):
    _add(auth, MEMBER)
    with running_api() as api:
        status, body = api.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        cookies = _set_cookies(api)
    assert status == 200, body
    assert body["user"]["email"] == MEMBER and body["user"]["isAdmin"] is False
    assert set(cookies) == {"agentic_session", "agentic_device"}
    for name, max_age in (("agentic_session", 30 * 24 * 3600), ("agentic_device", 365 * 24 * 3600)):
        header = cookies[name]
        assert "HttpOnly" in header and "SameSite=Lax" in header and "Path=/" in header, header
        assert f"Max-Age={max_age}" in header, header


def test_the_session_cookie_opens_gated_routes_and_nothing_else_does(auth):
    _add(auth, MEMBER)
    with running_api() as api:
        login, login_body = api.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        signed_in, keys = api.get("/api/user/api-keys", headers=RELAYED)
        stranger = ApiClient(api.base_url)
        refused, error = stranger.get("/api/user/api-keys", headers=RELAYED)
    assert login == 200, login_body
    assert signed_in == 200, (keys, sorted(api.cookies))
    assert set(keys) >= {"alpaca", "tradier"}
    assert (refused, error) == (401, {"error": "Sign in to continue."})


def test_a_wrong_password_is_refused_and_no_session_is_issued(auth):
    _add(auth, MEMBER)
    with running_api() as api:
        status, body = api.post("/api/auth/login", {"email": MEMBER, "password": "Wrong-Password-9"}, headers=RELAYED)
        cookies = _set_cookies(api)
    assert (status, body) == (400, {"error": "Invalid email or password."})
    assert "agentic_session" not in cookies
    assert "agentic_session" not in api.cookies


def test_a_disabled_account_cannot_sign_in_with_its_password(auth):
    _add(auth, MEMBER, active=False)
    with running_api() as api:
        status, body = api.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
    assert (status, body) == (400, {"error": "Invalid email or password."})


def test_logout_clears_the_cookie_and_kills_the_session_server_side(auth):
    _add(auth, MEMBER)
    with running_api() as api:
        api.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        old_cookie = "; ".join(f"{name}={value}" for name, value in api.cookies.items())
        status, body = api.post("/api/auth/logout", headers=RELAYED)
        cleared = _set_cookies(api)["agentic_session"]
        replay, replay_body = ApiClient(api.base_url).get(
            "/api/user/api-keys", headers={**RELAYED, "Cookie": old_cookie})
    assert (status, body) == (200, {"ok": True})
    assert cleared.startswith("agentic_session=;") and "Max-Age=0" in cleared
    assert replay == 401, "a logged-out session cookie must not open anything"
    assert replay_body == {"error": "Sign in to continue."}


def test_signed_out_status_has_the_shape_the_login_screen_reads(auth):
    _add(auth, MEMBER)
    with running_api() as api:
        status, body = api.get("/api/auth/status", headers=RELAYED)
    assert status == 200
    assert body == {"bootstrapRequired": False, "bootstrapRequiresToken": False, "user": None,
                    "accessStatus": "", "accessEmail": ""}


def test_cloudflare_identity_signs_in_only_an_active_account(auth):
    _add(auth, MEMBER)
    _add(auth, "off-cov@example.com", active=False)
    with running_api() as api:
        _, active = api.get("/api/auth/status", headers={**RELAYED, "X-AGX-Access-Email": MEMBER.upper()})
        _, unknown = api.get("/api/auth/status", headers={**RELAYED, "X-AGX-Access-Email": "nobody@example.com"})
        _, disabled = api.get("/api/auth/status", headers={**RELAYED, "X-AGX-Access-Email": "off-cov@example.com"})
    assert active["user"]["email"] == MEMBER and active["user"]["signedInVia"] == "cloudflare"
    assert active["accessStatus"] == "ok"
    assert (unknown["user"], unknown["accessStatus"], unknown["accessEmail"]) == (None, "no_account", "nobody@example.com")
    assert (disabled["user"], disabled["accessStatus"]) == (None, "disabled")


def test_owner_setup_on_an_empty_database_needs_the_token_and_happens_once(auth, monkeypatch):
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL")       # nothing seeded, nobody auto-signed-in
    monkeypatch.setenv("ADMIN_BOOTSTRAP_TOKEN", "cov-setup-token")
    owner = {"email": OWNER, "password": TEST_PASSWORD, "displayName": "Owner"}
    with running_api() as api:
        _, before = api.get("/api/auth/status", headers=RELAYED)
        wrong, wrong_body = api.post("/api/auth/bootstrap", {**owner, "setupToken": "guess"}, headers=RELAYED)
        right, right_body = api.post("/api/auth/bootstrap", {**owner, "setupToken": "cov-setup-token"}, headers=RELAYED)
        cookies = _set_cookies(api)
        again, again_body = ApiClient(api.base_url).post(
            "/api/auth/bootstrap", {**owner, "email": "second@example.com", "setupToken": "cov-setup-token"},
            headers=RELAYED)
    assert (before["bootstrapRequired"], before["bootstrapRequiresToken"], before["user"]) == (True, True, None)
    assert (wrong, wrong_body) == (403, {"error": "The admin setup token is incorrect."})
    assert right == 200, right_body
    assert right_body["user"]["email"] == OWNER and right_body["user"]["isAdmin"] is True
    assert {"agentic_session", "agentic_device"} <= set(cookies)
    assert again == 400 and "already been created" in again_body["error"]
    assert [u["email"] for u in auth.list_users(actor={"role": "admin"})] == [OWNER]


@pytest.mark.parametrize("path", ["/api/dashboard", "/api/admin/users", "/api/preflight", "/api/user/api-keys"])
def test_private_routes_refuse_a_signed_out_tunnel_visitor(auth, path):
    with running_api() as api:
        status, body = api.get(path, headers=RELAYED)
    assert (status, body) == (401, {"error": "Sign in to continue."})


def test_the_health_probe_stays_public(auth):
    with running_api() as api:
        status, body = api.get("/api/health", headers=RELAYED)
    assert status == 200 and "ok" in body
