"""Integration: a Schwab login nearing expiry reaches ADMIN phones exactly once,
through the real pieces - schwab_expiry_alert.due, the ledger file, the admin
lookup in the user database, every account's saved ntfy topic and the push
fan-out (coverage 2026-11-04, group "shell").

Only Schwab's token status and ntfy.sh itself are faked; the ledger, the user
database and the topic store are real files in a scratch folder. A "restart"
is a fresh pass that knows nothing but what the ledger file says.
"""
from __future__ import annotations

import urllib.request
from datetime import datetime, timedelta, timezone

import pytest
from cryptography.fernet import Fernet

import api_server
from auth_service import AuthService
from momx import momo_alert

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)
TOPICS = {"admin@example.com": "agx-momo-aaaaaaaaaaaa", "member@example.com": "agx-momo-bbbbbbbbbbbb",
          "old-admin@example.com": "agx-momo-cccccccccccc"}


@pytest.fixture
def world(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    service._create_user_record("admin@example.com", "", display_name="A", role="admin", must_change_password=False)
    service._create_user_record("member@example.com", "", display_name="M", role="user", must_change_password=False)
    old = service._create_user_record("old-admin@example.com", "", display_name="O", role="admin", must_change_password=False)
    service.set_user_active(old["id"], False, actor={"id": "x", "role": "admin"})

    monkeypatch.setattr(momo_alert, "_USERS_PATH", tmp_path / "momo_users.json")
    monkeypatch.setattr(momo_alert, "_CONFIG_PATH", tmp_path / "momo.json")
    for email, topic in TOPICS.items():
        momo_alert.save_user_config(email, {"ntfyTopic": topic})

    status = {"market_data": {"refreshTokenExpiresAt": (NOW + timedelta(hours=10)).isoformat(),
                              "tokenSavedAt": "2026-10-29T01:00:00+00:00"}}

    class FakeSchwab:
        def __init__(self, profile="market_data", config=None):
            self.profile = profile
            self.configured = profile in status

        def connection_status(self):
            return dict(status[self.profile])

    posted = []

    def fake_urlopen(request, timeout=None):
        posted.append((request.full_url, request.headers.get("Title"), request.data))

        class _Done:
            def close(self):
                pass
        return _Done()

    monkeypatch.setattr(api_server, "SchwabClient", FakeSchwab)
    monkeypatch.setattr(api_server, "SCHWAB_EXPIRY_LEDGER_PATH", tmp_path / "ledger" / "schwab_expiry_pushes.json")
    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return type("World", (), {"posted": posted, "status": status})


def test_the_warning_reaches_active_admins_only_and_survives_a_restart(world):
    first = api_server._schwab_expiry_check_once(now=NOW)
    assert [a["title"] for a in first] == ["Schwab login expires in 10h"]
    assert [url for url, _, _ in world.posted] == ["https://ntfy.sh/agx-momo-aaaaaaaaaaaa"], \
        "only the active admin's phone - not the member, not the switched-off admin"
    assert world.posted[0][1] == "Schwab login expires in 10h"
    assert b"TOS MARKET" in world.posted[0][2]

    # A restart: nothing in memory, only the ledger file on disk.
    assert api_server._schwab_expiry_check_once(now=NOW + timedelta(hours=1)) == []
    assert len(world.posted) == 1, "the same login is never pushed twice"

    # It then expires: one more push, and only one.
    api_server._schwab_expiry_check_once(now=NOW + timedelta(hours=11))
    api_server._schwab_expiry_check_once(now=NOW + timedelta(hours=12))
    assert [title for _, title, _ in world.posted] == ["Schwab login expires in 10h", "Schwab login EXPIRED"]


def test_a_fresh_login_arms_a_fresh_warning(world):
    api_server._schwab_expiry_check_once(now=NOW)
    world.status["market_data"] = {"refreshTokenExpiresAt": (NOW + timedelta(hours=30)).isoformat(),
                                   "tokenSavedAt": "2026-11-04T15:30:00+00:00"}
    assert api_server._schwab_expiry_check_once(now=NOW + timedelta(hours=1)) == [], "30h left is not due yet"
    later = api_server._schwab_expiry_check_once(now=NOW + timedelta(hours=8))
    assert [a["title"] for a in later] == ["Schwab login expires in 22h"]
    assert len(world.posted) == 2
