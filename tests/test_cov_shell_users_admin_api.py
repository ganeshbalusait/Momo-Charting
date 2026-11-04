"""Integration: USER MANAGEMENT (admin) through the real /api/admin/users*
routes over HTTP (coverage 2026-11-04, group "shell").

The admin and the member act through the tunnel with a Cloudflare-verified
address (relayed + X-AGX-Access-Email), exactly as on the phone. The invite
mailer is faked; nothing leaves the PC.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
from http_client import ApiClient

ADMIN = "admin-users@example.com"
MEMBER = "member-users@example.com"
AS_ADMIN = {**RELAYED, "X-AGX-Access-Email": ADMIN}
AS_MEMBER = {**RELAYED, "X-AGX-Access-Email": MEMBER}


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
    service._create_user_record(ADMIN, TEST_PASSWORD, display_name="Admin", role="admin", must_change_password=False)
    service._create_user_record(MEMBER, TEST_PASSWORD, display_name="Member", role="user", must_change_password=False)
    return service


@pytest.fixture
def invites(monkeypatch):
    sent = []

    def fake_invite(email, temp_password, *, display_name=""):
        sent.append((email, temp_password))
        return {"sent": True, "provider": "fake", "reason": "fake mailer accepted it"}

    monkeypatch.setattr(api_server, "send_user_invite", fake_invite)
    return sent


def _id(service, email):
    return service.get_user_by_email(email)["id"]


def test_create_is_passwordless_sends_no_mail_and_lists_the_user(auth, invites):
    with running_api() as api:
        status, body = api.post("/api/admin/users", {"email": "New.Person@Example.com", "displayName": "New", "role": "user"},
                                headers=AS_ADMIN)
        _, listing = api.get("/api/admin/users", headers=AS_ADMIN)
    assert status == 200, body
    assert body["user"]["email"] == "new.person@example.com"
    assert (body["user"]["hasPassword"], body["user"]["isAdmin"]) == (False, False)
    assert (body["emailed"], body["emailReason"]) == (False, "Passwordless account - no secret to send.")
    assert invites == [], "a passwordless account has no secret to mail"
    assert "new.person@example.com" in [u["email"] for u in listing["users"]]


def test_the_same_email_twice_is_refused(auth, invites):
    with running_api() as api:
        status, body = api.post("/api/admin/users", {"email": MEMBER.upper(), "role": "user"}, headers=AS_ADMIN)
    assert (status, body) == (400, {"error": "A user with this email already exists."})


def test_a_member_can_neither_list_nor_create_users(auth, invites):
    with running_api() as api:
        listed, listed_body = api.get("/api/admin/users", headers=AS_MEMBER)
        created, _ = api.post("/api/admin/users", {"email": "sneaky@example.com", "role": "admin"}, headers=AS_MEMBER)
    assert (listed, listed_body) == (403, {"error": "Administrator access is required."})
    assert created == 403
    assert auth.get_user_by_email("sneaky@example.com") is None


def test_disabling_ends_open_sessions_and_blocks_cloudflare_sign_in_until_enabled(auth, invites):
    member_id = _id(auth, MEMBER)
    with running_api() as api:
        phone = ApiClient(api.base_url)
        phone.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        before, _ = phone.get("/api/user/api-keys", headers=RELAYED)
        off, off_body = api.post("/api/admin/users/set-active", {"userId": member_id, "isActive": False}, headers=AS_ADMIN)
        after, _ = phone.get("/api/user/api-keys", headers=RELAYED)
        _, cf_off = api.get("/api/auth/status", headers=AS_MEMBER)
        on, _ = api.post("/api/admin/users/set-active", {"userId": member_id, "isActive": True}, headers=AS_ADMIN)
        api_server.STATE._auth_status_cache = {}
        _, cf_on = api.get("/api/auth/status", headers=AS_MEMBER)
    assert before == 200
    assert off == 200 and off_body["user"]["isActive"] is False
    assert after == 401, "the open session ended the moment the account was switched off"
    assert (cf_off["user"], cf_off["accessStatus"]) == (None, "disabled")
    assert on == 200
    assert cf_on["user"]["email"] == MEMBER and cf_on["accessStatus"] == "ok"


def test_the_admin_cannot_switch_off_or_delete_themself(auth, invites):
    admin_id = _id(auth, ADMIN)
    with running_api() as api:
        off, off_body = api.post("/api/admin/users/set-active", {"userId": admin_id, "isActive": False}, headers=AS_ADMIN)
        gone, gone_body = api.post("/api/admin/users/delete", {"userId": admin_id}, headers=AS_ADMIN)
    assert (off, off_body) == (400, {"error": "You cannot disable your own account."})
    assert (gone, gone_body) == (400, {"error": "You cannot delete your own account."})
    assert auth.get_user_by_email(ADMIN)["isActive"] is True


def test_delete_frees_the_email_and_drops_the_cached_owner_alpaca_client(auth, invites, monkeypatch):
    monkeypatch.setattr(api_server.STATE, "_owner_alpaca_client_cache", object(), raising=False)
    with running_api() as api:
        status, body = api.post("/api/admin/users/delete", {"userId": _id(auth, MEMBER)}, headers=AS_ADMIN)
        cache_after = api_server.STATE._owner_alpaca_client_cache
        _, cf = api.get("/api/auth/status", headers=AS_MEMBER)      # the ghost no longer signs in
        again, again_body = api.post("/api/admin/users", {"email": MEMBER, "role": "user"}, headers=AS_ADMIN)
    assert (status, body) == (200, {"deleted": True})
    assert cache_after is None
    assert again == 200 and again_body["user"]["email"] == MEMBER
    assert cf["accessStatus"] == "no_account"


def test_reset_password_mails_the_handover_and_forces_a_change(auth, invites):
    with running_api() as api:
        status, body = api.post("/api/admin/users/reset-password",
                                {"userId": _id(auth, MEMBER), "temporaryPassword": "Handover-Pass-77"}, headers=AS_ADMIN)
        old, _ = ApiClient(api.base_url).post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD}, headers=RELAYED)
        new, new_body = ApiClient(api.base_url).post("/api/auth/login", {"email": MEMBER, "password": "Handover-Pass-77"}, headers=RELAYED)
    assert status == 200, body
    assert (body["emailed"], body["emailReason"]) == (True, "fake mailer accepted it")
    assert invites == [(MEMBER, "Handover-Pass-77")]
    assert old == 400
    assert new == 200 and new_body["user"]["mustChangePassword"] is True


def test_the_last_admin_with_a_password_keeps_it(auth, invites):
    with running_api() as api:
        status, body = api.post("/api/admin/users/remove-password", {"userId": _id(auth, ADMIN)}, headers=AS_ADMIN)
        member, member_body = api.post("/api/admin/users/remove-password", {"userId": _id(auth, MEMBER)}, headers=AS_ADMIN)
    assert status == 400 and "last administrator with a password" in body["error"]
    assert auth.get_user_by_email(ADMIN)["hasPassword"] is True
    assert member == 200 and member_body["user"]["hasPassword"] is False


def test_a_member_cannot_reach_the_recovery_routes(auth, invites):
    admin_id = _id(auth, ADMIN)
    with running_api() as api:
        results = [api.post(path, payload, headers=AS_MEMBER)[0] for path, payload in (
            ("/api/admin/users/set-active", {"userId": admin_id, "isActive": False}),
            ("/api/admin/users/delete", {"userId": admin_id}),
            ("/api/admin/users/reset-password", {"userId": admin_id, "temporaryPassword": "Taken-Over-99"}),
            ("/api/admin/users/remove-password", {"userId": admin_id}),
        )]
    assert results == [403, 403, 403, 403]
    assert invites == []
    assert auth.get_user_by_email(ADMIN)["isActive"] is True
