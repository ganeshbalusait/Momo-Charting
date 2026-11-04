"""Integration: device approval (Settings > DEVICE ACCESS) through the real
login and admin routes over HTTP (coverage 2026-11-04, group "shell").

A second device for an account must wait for an admin; approving lets it in,
rejecting keeps it out, revoking signs it out. All tunnel traffic is stamped
relayed, so the admin-on-this-PC bypass never approves anything by itself.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService, AuthenticationError
from http_client import ApiClient

ADMIN = "admin-dev@example.com"
MEMBER = "member-dev@example.com"
PHONE_UA = "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit Safari/604.1"


@pytest.fixture
def auth(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)   # no loopback grant at all
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    for email, role in ((ADMIN, "admin"), (MEMBER, "user")):
        service._create_user_record(email, TEST_PASSWORD, display_name=email.split("@")[0], role=role,
                                    must_change_password=False)
    return service


def _login(api, email, user_agent=""):
    client = ApiClient(api.base_url)
    headers = {**RELAYED, **({"User-Agent": user_agent} if user_agent else {})}
    status, body = client.post("/api/auth/login", {"email": email, "password": TEST_PASSWORD}, headers=headers)
    return client, status, body


def test_a_second_device_waits_with_a_device_cookie_and_no_session(auth):
    with running_api() as api:
        _, first, _ = _login(api, MEMBER)
        phone, status, body = _login(api, MEMBER, PHONE_UA)
    assert first == 200
    assert status == 403
    assert body == {"error": "This device is waiting for admin approval. Approve it from an already "
                             "signed-in device, then sign in again.", "devicePending": True}
    assert set(phone.cookies) == {"agentic_device"}


def test_the_admin_sees_the_request_approves_it_and_the_device_signs_in(auth):
    with running_api() as api:
        admin, _, _ = _login(api, ADMIN)
        _login(api, MEMBER)
        phone, _, _ = _login(api, MEMBER, PHONE_UA)
        _, listing = admin.get("/api/admin/devices", headers=RELAYED)
        pending = listing["pendingRequests"]
        decided, decision = admin.post("/api/admin/device-requests",
                                       {"deviceId": pending[0]["id"], "action": "approve"}, headers=RELAYED)
        retry, retry_body = phone.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD},
                                       headers={**RELAYED, "User-Agent": PHONE_UA})
        works, _ = phone.get("/api/user/api-keys", headers=RELAYED)
    assert len(pending) == 1
    row = pending[0]
    assert (row["userEmail"], row["label"], row["ipAddress"], row["status"]) == (MEMBER, "Safari on iPhone", "127.0.0.1", "pending")
    assert (decided, decision) == (200, {"deviceId": row["id"], "status": "approved"})
    assert retry == 200, retry_body
    assert works == 200


def test_a_rejected_device_stays_out_and_asks_again(auth):
    with running_api() as api:
        admin, _, _ = _login(api, ADMIN)
        _login(api, MEMBER)
        phone, _, _ = _login(api, MEMBER, PHONE_UA)
        _, listing = admin.get("/api/admin/devices", headers=RELAYED)
        device_id = listing["pendingRequests"][0]["id"]
        _, decision = admin.post("/api/admin/device-requests", {"deviceId": device_id, "action": "reject"}, headers=RELAYED)
        _, rejected_view = admin.get("/api/admin/devices", headers=RELAYED)
        again, again_body = phone.post("/api/auth/login", {"email": MEMBER, "password": TEST_PASSWORD},
                                       headers={**RELAYED, "User-Agent": PHONE_UA})
        _, requeued = admin.get("/api/admin/devices", headers=RELAYED)
    assert decision["status"] == "rejected"
    assert rejected_view["pendingRequests"] == []
    assert again == 403 and again_body["devicePending"] is True
    assert [d["id"] for d in requeued["pendingRequests"]] == [device_id]


def test_revoking_a_device_signs_its_session_out(auth):
    with running_api() as api:
        admin, _, _ = _login(api, ADMIN)
        member, _, _ = _login(api, MEMBER)
        before, _ = member.get("/api/user/api-keys", headers=RELAYED)
        _, listing = admin.get("/api/admin/devices", headers=RELAYED)
        member_device = next(d for d in listing["approvedDevices"] if d["userEmail"] == MEMBER)
        revoked, body = admin.post("/api/admin/devices/revoke", {"deviceId": member_device["id"]}, headers=RELAYED)
        after, after_body = member.get("/api/user/api-keys", headers=RELAYED)
    assert before == 200
    assert (revoked, body) == (200, {"deviceId": member_device["id"], "status": "revoked"})
    assert (after, after_body) == (401, {"error": "Sign in to continue."})


def test_the_admins_own_device_is_marked_current_and_cannot_be_revoked(auth):
    with running_api() as api:
        admin, _, _ = _login(api, ADMIN)
        _, listing = admin.get("/api/admin/devices", headers=RELAYED)
        mine = [d for d in listing["approvedDevices"] if d["isCurrentDevice"]]
        status, body = admin.post("/api/admin/devices/revoke", {"deviceId": mine[0]["id"]}, headers=RELAYED)
        still, _ = admin.get("/api/admin/devices", headers=RELAYED)
    assert [d["userEmail"] for d in mine] == [ADMIN]
    assert (status, body) == (400, {"error": "You cannot revoke the device you are currently using."})
    assert still == 200


def test_a_non_admin_can_neither_list_nor_decide(auth):
    with running_api() as api:
        member, _, _ = _login(api, MEMBER)
        listed, listed_body = member.get("/api/admin/devices", headers=RELAYED)
        decided, decided_body = member.post("/api/admin/device-requests", {"deviceId": "x", "action": "approve"}, headers=RELAYED)
        revoked, _ = member.post("/api/admin/devices/revoke", {"deviceId": "x"}, headers=RELAYED)
    forbidden = {"error": "Administrator access is required."}
    assert (listed, listed_body) == (403, forbidden)
    assert (decided, decided_body) == (403, forbidden)
    assert revoked == 403


@pytest.mark.parametrize("payload, message", [
    ({"deviceId": "no-such-device", "action": "approve"}, "This device request is no longer pending."),
    ({"deviceId": "no-such-device", "action": "maybe"}, "Choose approve or reject."),
])
def test_a_bad_device_request_is_a_clean_400(auth, payload, message):
    with running_api() as api:
        admin, _, _ = _login(api, ADMIN)
        status, body = admin.post("/api/admin/device-requests", payload, headers=RELAYED)
        revoke, revoke_body = admin.post("/api/admin/devices/revoke", {"deviceId": "no-such-device"}, headers=RELAYED)
    assert (status, body) == (400, {"error": message})
    assert (revoke, revoke_body) == (400, {"error": "Approved device not found."})


def test_service_refuses_revoking_the_current_device_and_non_admin_actors(auth):
    admin = auth.get_user_by_email(ADMIN)
    member = auth.get_user_by_email(MEMBER)
    decision = auth.authorize_login_device(admin, device_token="admin-device-token")
    with pytest.raises(AuthenticationError, match="currently using"):
        auth.revoke_device(admin, decision["device"]["id"], current_device_token="admin-device-token")
    from auth_service import AuthorizationError
    for call in (lambda: auth.list_devices(member), lambda: auth.decide_device_request(member, "x", "approve")):
        with pytest.raises(AuthorizationError):
            call()
