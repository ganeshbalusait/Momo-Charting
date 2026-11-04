"""Integration: the Momo Alert phone channel - the Send test route on
api_server and the per-account topic store on the scanner worker, each over
real HTTP (coverage 2026-11-04, group "shell"). ntfy.sh is a fake poster; the
topic files are the harnesses' scratch copies.
"""
from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
from momx import momo_alert
from worker_harness import running_worker

OWNER = "owner-ntfy@example.com"
CONFIG = "/api/momx-scanner/momo-config"


@pytest.fixture
def poster(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
    sent = []
    monkeypatch.setattr(momo_alert, "_default_poster", lambda topic, title, body, tags: sent.append((topic, title, tags)))
    return sent


def test_send_test_pushes_once_to_the_signed_in_accounts_own_topic(poster):
    with running_api() as api:
        momo_alert.save_user_config(OWNER, {"ntfyTopic": "agx-momo-0123456789ab"})
        momo_alert.save_user_config("someone-else@example.com", {"ntfyTopic": "agx-momo-ffffffffffff"})
        status, body = api.post("/api/momo-alert/test-push")
    assert (status, body) == (200, {"ok": True, "topic": "agx-momo-0123456789ab"})
    assert poster == [("agx-momo-0123456789ab", "AGX test notification", "white_check_mark")]


def test_send_test_without_a_topic_says_so_and_sends_nothing(poster):
    with running_api() as api:
        status, body = api.post("/api/momo-alert/test-push")
    assert status == 200
    assert body["ok"] is False and body["error"].startswith("No phone channel saved yet.")
    assert poster == []


def test_a_failed_push_is_an_honest_verdict(poster, monkeypatch):
    def down(*_args):
        raise OSError("ntfy.sh unreachable")
    monkeypatch.setattr(momo_alert, "_default_poster", down)
    with running_api() as api:
        momo_alert.save_user_config(OWNER, {"ntfyTopic": "agx-momo-0123456789ab"})
        _, body = api.post("/api/momo-alert/test-push")
    assert body["ok"] is False and "did not go through (OSError)" in body["error"]


def test_the_worker_keeps_each_accounts_topic_separate(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, saved = worker.post(CONFIG, {"ntfyTopic": "agx-momo-aaaaaaaaaaaa"}, headers={"X-AGX-User": "A@Example.com"})
        _, as_a = worker.get(CONFIG, headers={"X-AGX-User": "a@example.com"})
        _, as_b = worker.get(CONFIG, headers={"X-AGX-User": "b@example.com"})
    assert status == 200 and saved["ntfyTopic"] == "agx-momo-aaaaaaaaaaaa"
    assert as_a["ntfyTopic"] == "agx-momo-aaaaaaaaaaaa"
    assert as_b["ntfyTopic"] == "", "another account never sees (or pushes to) A's channel"


def test_the_worker_saves_garbage_as_defaults_instead_of_failing(tmp_path, monkeypatch):
    garbage = {"timeframes": {"5m": {"armed": "yes", "threshold": 999}, "15m": "nope"},
               "pushWindow": {"start": True, "end": 5000}, "cooldownMinutes": "soon",
               "ntfyTopic": "bad topic!/../x", "sound": "loud"}
    with running_worker(tmp_path, monkeypatch) as worker:
        status, saved = worker.post(CONFIG, garbage, headers={"X-AGX-User": "a@example.com"})
    assert status == 200
    assert saved["timeframes"]["5m"] == {"armed": True, "threshold": 3.0}
    assert saved["pushWindow"] == {"start": 555, "end": 930}
    assert (saved["cooldownMinutes"], saved["ntfyTopic"], saved["sound"]) == (15, "", True)


def test_a_save_through_api_server_lands_under_the_signed_in_account(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)
    service._create_user_record("member-ntfy@example.com", TEST_PASSWORD, display_name="M", role="user", must_change_password=False)
    as_member = {**RELAYED, "X-AGX-Access-Email": "member-ntfy@example.com"}
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, _ = api.post(CONFIG, {"ntfyTopic": "agx-momo-123412341234"}, headers=as_member)
        _, read_back = api.get(CONFIG, headers=as_member)
        _, as_default = worker.get(CONFIG)
    assert status == 200
    assert read_back["ntfyTopic"] == "agx-momo-123412341234"
    assert as_default["ntfyTopic"] == "", "the proxy forwarded who was asking"
