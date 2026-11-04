"""Integration: Momo Alert settings, alerts and the phone test over real HTTP.

Settings are per account, keyed by the X-AGX-User header that ONLY api_server
may set (it forwards the signed-in email; a browser cannot pick someone else's
settings). The worker routes are exercised directly and through the proxy;
ntfy is a fake, and the settings file is the harness's scratch copy.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import momx_worker
from api_harness import api_server, running_api
from momx import momo_alert, service
from worker_harness import running_worker

NOW = datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc).timestamp()


def _as(email: str) -> dict:
    return {"X-AGX-User": email}


def test_each_account_reads_and_writes_only_its_own_settings(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, saved = worker.post("/api/momx-scanner/momo-config",
                                    {"timeframes": {"15m": {"armed": True, "threshold": 4}}, "sound": False,
                                     "ntfyTopic": "agx-momo-aaaa"}, headers=_as("A@X.com"))
        assert status == 200, saved
        assert saved["timeframes"]["15m"] == {"armed": True, "threshold": 4.0}
        _, other = worker.get("/api/momx-scanner/momo-config", headers=_as("b@x.com"))
        _, mine = worker.get("/api/momx-scanner/momo-config", headers=_as("a@x.com"))
        _, bare = worker.get("/api/momx-scanner/momo-config")
    assert other == momo_alert.DEFAULT_CONFIG                    # b never saw a's change
    assert bare == momo_alert.DEFAULT_CONFIG                     # nor does the shared default profile
    assert mine["timeframes"]["15m"]["armed"] is True and mine["sound"] is False
    assert mine["ntfyTopic"] == "agx-momo-aaaa"                  # emails match case-insensitively


@pytest.mark.parametrize("typed, kept", [(0, 3.0), ("abc", 3.0), (51, 3.0), (-2, 3.0), (4, 4.0), (1, 1.0), (50, 50.0)])
def test_a_threshold_outside_one_to_fifty_never_saves(tmp_path, monkeypatch, typed, kept):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, echoed = worker.post("/api/momx-scanner/momo-config",
                                     {"timeframes": {"5m": {"armed": True, "threshold": typed}}}, headers=_as("a@x.com"))
        _, reread = worker.get("/api/momx-scanner/momo-config", headers=_as("a@x.com"))
    assert status == 200, echoed
    assert echoed["timeframes"]["5m"]["threshold"] == kept
    assert reread["timeframes"]["5m"]["threshold"] == kept


def test_a_junk_topic_never_becomes_a_push_url(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, echoed = worker.post("/api/momx-scanner/momo-config", {"ntfyTopic": "../evil topic?x=1"}, headers=_as("a@x.com"))
    assert status == 200 and echoed["ntfyTopic"] == ""


def _publish(stamp: str, rvol5: float) -> None:
    state = service._state("Watchlist")
    payload = {"list": "Watchlist", "generatedAt": stamp, "tapeAsOf": datetime.fromtimestamp(NOW - 60, timezone.utc).isoformat(),
               "universe": ["AAA"], "rows": [{"symbol": "AAA", "scanPass": True, "pctChange": 3.2, "rvol": {"5m": {"value": rvol5}}}],
               "rest": [], "errors": {}}
    with service._LOCK:
        state.payload, state.bear, state.error = payload, {**payload, "rows": []}, ""


def test_the_alerts_poll_returns_this_accounts_crossing_and_its_phone_gets_one_push(tmp_path, monkeypatch):
    pushes: list = []
    monkeypatch.setattr(momo_alert, "push_ntfy", lambda alerts, topic, **kw: pushes.append((topic, [a["symbol"] for a in alerts])))
    monkeypatch.setattr(momo_alert, "push_window_allows", lambda config, now_et=None: True)
    monkeypatch.setattr(momo_alert, "time", SimpleNamespace(time=lambda: NOW))
    monkeypatch.setattr(momx_worker, "_MOMO", {"stamps": {}, "prev": {}, "users": {}})
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.post("/api/momx-scanner/momo-config", {"ntfyTopic": "agx-momo-aaaa"}, headers=_as("a@x.com"))
        _publish("p1", 1.2)
        status, first = worker.get("/api/momx-scanner/momo-alerts", headers=_as("a@x.com"))
        _publish("p2", 3.6)
        status2, second = worker.get("/api/momx-scanner/momo-alerts", headers=_as("a@x.com"))
        _, again = worker.get("/api/momx-scanner/momo-alerts", headers=_as("a@x.com"))
        _, stranger = worker.get("/api/momx-scanner/momo-alerts", headers=_as("c@x.com"))
    assert status == 200 and first["alerts"] == [] and first["sound"] is True
    assert status2 == 200, second
    assert [(a["symbol"], a["timeframe"], a["value"], a["list"]) for a in second["alerts"]] == [("AAA", "5m", 3.6, "Watchlist")]
    assert again["alerts"] == second["alerts"]                   # a repeat poll replays nothing new
    assert [p for p in pushes if p[0]] == [("agx-momo-aaaa", ["AAA"])]
    assert stranger["alerts"] == []                              # an account with no settings saved had no state yet


def test_the_proxy_stamps_the_signed_in_user_over_whatever_the_browser_sends(tmp_path, monkeypatch):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: {"email": "a@x.com", "role": "member"})
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, saved = api.post("/api/momx-scanner/momo-config", {"ntfyTopic": "agx-momo-aaaa"}, headers=_as("b@x.com"))
        _, through_proxy = api.get("/api/momx-scanner/momo-config", headers=_as("b@x.com"))
        _, as_b = worker.get("/api/momx-scanner/momo-config", headers=_as("b@x.com"))
        _, as_a = worker.get("/api/momx-scanner/momo-config", headers=_as("a@x.com"))
    assert status == 200, saved
    assert through_proxy["ntfyTopic"] == "agx-momo-aaaa"         # read as a@x, not the forged b@x
    assert as_b["ntfyTopic"] == ""                               # b@x was never written
    assert as_a["ntfyTopic"] == "agx-momo-aaaa"


@pytest.fixture
def poster(monkeypatch):
    sent: list = []
    monkeypatch.setattr(momo_alert, "_default_poster", lambda topic, title, body, tags: sent.append((topic, title)))
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: {"email": "a@x.com", "role": "member"})
    return sent


def test_send_test_with_no_topic_saved_says_so_and_posts_nothing(poster):
    with running_api() as api:
        status, body = api.post("/api/momo-alert/test-push")
    assert status == 200, body
    assert body["ok"] is False and "Press Generate first" in body["error"]
    assert poster == []


def test_send_test_posts_once_to_this_accounts_topic(poster):
    with running_api() as api:
        momo_alert.save_user_config("a@x.com", {"ntfyTopic": "agx-momo-aaaa"})
        momo_alert.save_user_config("b@x.com", {"ntfyTopic": "agx-momo-bbbb"})
        status, body = api.post("/api/momo-alert/test-push")
    assert status == 200, body
    assert body == {"ok": True, "topic": "agx-momo-aaaa"}
    assert poster == [("agx-momo-aaaa", "AGX test notification")]


def test_a_failed_send_test_is_reported_honestly(monkeypatch):
    def refuse(*args):
        raise OSError("network unreachable")

    monkeypatch.setattr(momo_alert, "_default_poster", refuse)
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: {"email": "a@x.com", "role": "member"})
    with running_api() as api:
        momo_alert.save_user_config("a@x.com", {"ntfyTopic": "agx-momo-aaaa"})
        status, body = api.post("/api/momo-alert/test-push")
    assert status == 200
    assert body["ok"] is False and "did not go through (OSError)" in body["error"]
