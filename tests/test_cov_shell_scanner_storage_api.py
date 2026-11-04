"""Integration: POST /api/scanner-storage-settings over HTTP (coverage
2026-11-04, group "shell"). A stand-in STATE records the call, so the live
retention setting and database are never touched.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService

ADMIN = "admin-storage@example.com"
MEMBER = "member-storage@example.com"
ROUTE = "/api/scanner-storage-settings"


class StubState:
    def __init__(self):
        self.calls = []

    def update_scanner_storage_settings(self, history_retention_days=None):
        self.calls.append(history_retention_days)
        return {"scannerStorage": {"historyRetentionDays": history_retention_days}, "actionMessage": "saved"}


@pytest.fixture
def stub(tmp_path, monkeypatch):
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
    return StubState()


def test_an_admin_save_reaches_the_state_with_the_number(stub):
    with running_api(state=stub) as api:
        status, body = api.post(ROUTE, {"historyRetentionDays": 45}, headers={**RELAYED, "X-AGX-Access-Email": ADMIN})
    assert status == 200, body
    assert stub.calls == [45]
    assert body["scannerStorage"]["historyRetentionDays"] == 45


def test_a_signed_out_visitor_cannot_change_it(stub):
    with running_api(state=stub) as api:
        status, _ = api.post(ROUTE, {"historyRetentionDays": 1}, headers=RELAYED)
    assert status == 401
    assert stub.calls == []


@pytest.mark.xfail(strict=True, reason=(
    "BUG: /api/scanner-storage-settings has no admin check. The Settings card is hidden from "
    "non-admins, but any signed-in user can POST and change how long scanner history is kept "
    "for the whole server."))
def test_a_non_admin_cannot_change_server_wide_retention(stub):
    with running_api(state=stub) as api:
        status, body = api.post(ROUTE, {"historyRetentionDays": 1}, headers={**RELAYED, "X-AGX-Access-Email": MEMBER})
    assert stub.calls == [], "a member changed server-wide retention"
    assert (status, body) == (403, {"error": "Administrator access is required."})
