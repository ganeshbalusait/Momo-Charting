"""Integration: the APP HEALTH CHECKLIST routes (GET /api/preflight and POST
/api/preflight/run) over real HTTP (coverage 2026-11-04, group "shell").

preflight.py is replaced by a fake module, so no broker is probed, no warmer is
restarted and the live archive is never read or written. Identities are real:
an admin and a member act through the tunnel with Cloudflare-verified emails -
a plain local request would be the auto-login admin and prove nothing.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService

ADMIN = "admin-preflight@example.com"
MEMBER = "member-preflight@example.com"
AS_ADMIN = {**RELAYED, "X-AGX-Access-Email": ADMIN}
AS_MEMBER = {**RELAYED, "X-AGX-Access-Email": MEMBER}
ET = ZoneInfo("America/New_York")


def _entry(day_offset: int, failed: int = 0):
    at = (datetime.now(ET) - timedelta(days=day_offset)).replace(hour=8, minute=45, second=0, microsecond=0)
    return {"at": at.isoformat(), "passed": 13 - failed, "warned": 0, "failed": failed, "unknown": 0,
            "checks": [{"id": "schwab", "label": "Schwab", "status": "fail" if failed else "pass", "measured": "1 token"}]}


class FakePreflight:
    def __init__(self):
        self.runs = []
        self.history_days = []
        self.history = [_entry(0), _entry(1, failed=2)]

    def run_preflight(self, heal=True, now_et=None):
        self.runs.append(heal)
        return {"at": datetime.now(ET).isoformat(), "passed": 14, "warned": 0, "failed": 0, "unknown": 0,
                "checks": [{"id": "schwab", "label": "Schwab", "status": "pass", "measured": "2 tokens"}], "healed": []}

    def load_preflight_history(self, days=30):
        self.history_days.append(days)
        return list(self.history)


@pytest.fixture
def fake(tmp_path, monkeypatch):
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
    module = FakePreflight()
    monkeypatch.setattr(api_server, "_preflight_module", lambda: (module, ""))
    monkeypatch.setattr(api_server, "_PREFLIGHT_LAST_RESULT", {"result": None, "at": 0.0})
    return module


def test_an_admin_reads_todays_result_and_the_strip_without_running_anything(fake):
    with running_api() as api:
        status, body = api.get("/api/preflight", headers=AS_ADMIN)
    assert status == 200, body
    assert body["available"] is True and body["ranToday"] is True
    assert body["result"] == fake.history[0]
    assert body["historyDays"] == 2 and body["history"][1]["failed"] == 2
    assert body["schedule"]["at"] == "08:45 ET"
    assert fake.runs == [], "opening the page must never fire broker probes"


def test_a_real_member_is_refused_and_nothing_is_read(fake):
    with running_api() as api:
        got, got_body = api.get("/api/preflight", headers=AS_MEMBER)
        ran, ran_body = api.post("/api/preflight/run", {"heal": True}, headers=AS_MEMBER)
    forbidden = {"error": "Administrator access is required."}
    assert (got, got_body) == (403, forbidden)
    assert (ran, ran_body) == (403, forbidden)
    assert fake.runs == [] and fake.history_days == []


def test_run_check_now_returns_the_same_envelope_with_the_fresh_run(fake):
    with running_api() as api:
        status, body = api.post("/api/preflight/run", {"heal": False}, headers=AS_ADMIN)
    assert status == 200, body
    assert fake.runs == [False]
    assert body["ran"]["available"] is True and body["ran"]["healEnabled"] is False
    assert isinstance(body["ran"]["elapsedMs"], int)
    assert {"available", "result", "history", "schedule"} <= set(body)


def test_signed_out_cannot_run_the_checklist(fake):
    with running_api() as api:
        status, body = api.post("/api/preflight/run", {"heal": True}, headers=RELAYED)
    assert (status, body) == (401, {"error": "Sign in to continue."})
    assert fake.runs == []


@pytest.mark.parametrize("query, days", [("?days=999", 30), ("?days=abc", 30), ("?days=0", 1), ("?days=7", 7), ("", 30)])
def test_the_days_parameter_is_clamped(fake, query, days):
    with running_api() as api:
        status, _ = api.get(f"/api/preflight{query}", headers=AS_ADMIN)
    assert status == 200
    assert fake.history_days == [days]


def test_a_missing_checklist_module_is_unknown_never_a_pass(fake, monkeypatch):
    monkeypatch.setattr(api_server, "_preflight_module", lambda: (None, "ImportError: preflight is half-written"))
    with running_api() as api:
        _, read = api.get("/api/preflight", headers=AS_ADMIN)
        _, ran = api.post("/api/preflight/run", {"heal": False}, headers=AS_ADMIN)
    assert read["available"] is False and "half-written" in read["unavailableReason"]
    assert read["result"] is None and read["ranToday"] is False
    assert read["historyError"]
    assert ran["ran"]["available"] is False
    assert ran["ran"]["passed"] == 0 and ran["ran"]["checks"][0]["status"] == "unknown"


def test_a_crashing_run_is_reported_unknown_not_a_500(fake):
    def boom(heal=True, now_et=None):
        raise RuntimeError("Schwab probe exploded")
    fake.run_preflight = boom
    with running_api() as api:
        status, body = api.post("/api/preflight/run", {"heal": True}, headers=AS_ADMIN)
    assert status == 200
    assert body["ran"]["available"] is False
    assert "Schwab probe exploded" in body["ran"]["unavailableReason"]
