"""Unit: api_server._push_phone_notification - the one fan-out every morning
brief, preflight verdict and Schwab-expiry alert goes through (coverage
2026-11-04, group "shell"). Topics live in a scratch Momo Alert store; ntfy.sh
is a fake that records each POST.
"""
from __future__ import annotations

import urllib.request

import pytest

import api_server
from momx import momo_alert


@pytest.fixture
def ntfy(tmp_path, monkeypatch):
    monkeypatch.setattr(momo_alert, "_USERS_PATH", tmp_path / "momo_users.json")
    monkeypatch.setattr(momo_alert, "_CONFIG_PATH", tmp_path / "momo.json")
    posts = []
    failing = set()

    def fake_urlopen(request, timeout=None):
        topic = request.full_url.rsplit("/", 1)[-1]
        posts.append({"topic": topic, "title": request.headers.get("Title"), "tags": request.headers.get("Tags"),
                      "priority": request.headers.get("Priority"), "body": request.data, "timeout": timeout})
        if topic in failing:
            raise OSError("ntfy unreachable")

        class _Done:
            def close(self):
                pass
        return _Done()

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    return type("Ntfy", (), {"posts": posts, "failing": failing})


def _topics(**by_email):
    for email, topic in by_email.items():
        momo_alert.save_user_config(email.replace("_at_", "@"), {"ntfyTopic": topic})


def test_only_emails_restricts_the_fanout_case_insensitively(ntfy):
    _topics(a_at_x="agx-momo-aaaaaaaaaaaa", b_at_x="agx-momo-bbbbbbbbbbbb")
    api_server._push_phone_notification("Preflight", "14/14", tags="white_check_mark", only_emails={"A@X"})
    assert [p["topic"] for p in ntfy.posts] == ["agx-momo-aaaaaaaaaaaa"]
    assert (ntfy.posts[0]["title"], ntfy.posts[0]["tags"], ntfy.posts[0]["priority"]) == ("Preflight", "white_check_mark", "high")


def test_an_empty_recipient_set_sends_to_nobody(ntfy):
    _topics(a_at_x="agx-momo-aaaaaaaaaaaa")
    api_server._push_phone_notification("Admin only", "x", only_emails=set())
    assert ntfy.posts == []


def test_no_filter_reaches_every_topic_once_even_when_shared(ntfy):
    _topics(a_at_x="agx-momo-shared000000", b_at_x="agx-momo-shared000000", c_at_x="agx-momo-cccccccccccc", d_at_x="")
    api_server._push_phone_notification("Morning brief", "SPY gap up")
    assert sorted(p["topic"] for p in ntfy.posts) == ["agx-momo-cccccccccccc", "agx-momo-shared000000"]


def test_the_body_is_capped_for_ntfy(ntfy):
    _topics(a_at_x="agx-momo-aaaaaaaaaaaa")
    api_server._push_phone_notification("Long", "→" * 5000)   # 3 bytes per character
    assert len(ntfy.posts[0]["body"]) == 3800


def test_one_dead_topic_does_not_stop_the_others(ntfy):
    _topics(a_at_x="agx-momo-aaaaaaaaaaaa", b_at_x="agx-momo-bbbbbbbbbbbb")
    ntfy.failing.add("agx-momo-aaaaaaaaaaaa")
    api_server._push_phone_notification("t", "b")     # must not raise
    assert sorted(p["topic"] for p in ntfy.posts) == ["agx-momo-aaaaaaaaaaaa", "agx-momo-bbbbbbbbbbbb"]


def test_a_broken_topic_store_returns_quietly(ntfy, monkeypatch):
    def broken(*_a, **_k):
        raise OSError("disk gone")
    monkeypatch.setattr(momo_alert, "all_user_configs", broken)
    api_server._push_phone_notification("t", "b")
    assert ntfy.posts == []
