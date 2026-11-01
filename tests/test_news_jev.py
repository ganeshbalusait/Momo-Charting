"""news_jev.py - TypeSafe Jev reads each new MomX headline (fake API, no network, no key)."""
from __future__ import annotations

import urllib.error
import pytest
from datetime import datetime, timedelta, timezone

from news_jev import JevNewsJudge, item_key, parse_answer

NOW = datetime(2026, 10, 2, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def fake_api_enabled(monkeypatch):
    # These tests inject FakeApi; the trader's paid-news switch is unrelated.
    monkeypatch.setenv("AGX_JEV_NEWS", "1")


def item(symbol="INOD", headline="Innodata upgraded", hours_old=1, url="https://x/1"):
    return {"symbol": symbol, "headline": headline, "url": url, "source": "SA",
            "publishedAt": (NOW - timedelta(hours=hours_old)).isoformat(), "summary": ""}


class FakeApi:
    def __init__(self, choice="bullish", fail=None):
        self.calls, self.choice, self.fail = [], choice, list(fail or [])

    def __call__(self, body, key):
        self.calls.append(body)
        assert key == "test-key" and body["model"] == "jev-latest"
        assert body["questions"]["impact"]["type"] == "choice"
        if self.fail:
            code = self.fail.pop(0)
            raise urllib.error.HTTPError("u", code, "x", None, None)
        return {"model": "jev-1.13.0", "answers": {"impact": {
            "type": "choice", "choice": self.choice, "confidence": 0.9,
            "probabilities": {"bullish": 0.95, "bearish": 0.01, "not_important": 0.04}}}}


def judge(tmp_path, api, **kw):
    return JevNewsJudge(tmp_path / "jev.json", key="test-key", post=api, background=False,
                        clock=lambda: NOW.timestamp(), sleep=lambda s: None, **kw)


def test_new_headline_is_judged_once_and_attached(tmp_path):
    api = FakeApi()
    j = judge(tmp_path, api)
    payload = {"feed": [item()], "latest": {"INOD": item()}}
    j.annotate(payload)                       # queues + judges (background=False drains)
    assert len(api.calls) == 1                # the same headline in feed and latest = one call
    again = {"feed": [item()], "latest": {"INOD": item()}}
    j.annotate(again)
    assert again["feed"][0]["jev"]["label"] == "bullish" and again["latest"]["INOD"]["jev"]["p"] == 0.95
    assert len(api.calls) == 1                # never paid twice
    # survives a restart: the file holds it
    j2 = judge(tmp_path, FakeApi())
    third = {"feed": [item()]}
    j2.annotate(third)
    assert third["feed"][0]["jev"]["label"] == "bullish"


def test_old_headlines_and_no_key_send_nothing(tmp_path):
    api = FakeApi()
    j = judge(tmp_path, api)
    j.annotate({"feed": [item(hours_old=72)]})
    assert api.calls == []
    off = JevNewsJudge(tmp_path / "x.json", key="", post=api, background=False)
    off.annotate({"feed": [item()]})
    assert api.calls == [] and off.enabled is False


def test_rate_limit_backs_off_then_succeeds_and_errors_never_raise(tmp_path):
    api = FakeApi(fail=[429, 529])
    j = judge(tmp_path, api)
    p = {"feed": [item()]}
    j.annotate(p)
    j.annotate(p)
    assert p["feed"][0]["jev"]["label"] == "bullish" and len(api.calls) == 3
    bad = FakeApi(fail=[401])
    j2 = judge(tmp_path / "b", bad)
    q = {"feed": [item(url="https://x/2")]}
    j2.annotate(q)
    assert "jev" not in q["feed"][0] and j2.status()["errors"] == 1
    assert "test-key" not in str(j2.status())
    assert j2.annotate("not a payload") == "not a payload"


def test_parse_answer_and_key():
    assert parse_answer({"choice": "bearish", "confidence": 0.8, "probabilities": {"bearish": 0.9}})["label"] == "bearish"
    assert parse_answer({"choice": "maybe"}) is None
    assert item_key(item()) == item_key(item(headline="other text"))     # same URL = same story
    assert item_key(item(url="")) != item_key(item(url="", headline="other"))


def test_answers_to_an_older_wording_are_asked_again(tmp_path):
    import news_jev
    api = FakeApi()
    j = judge(tmp_path, api)
    j.annotate({"feed": [item()]})
    assert len(api.calls) == 1
    with j._lock:
        for v in j._cache.values():
            v["qv"] = news_jev.QUESTION_VERSION - 1
    p = {"feed": [item()]}
    j.annotate(p)
    assert len(api.calls) == 2                        # re-asked with the current wording
    old = {"feed": [item(hours_old=72)]}
    with j._lock:
        for v in j._cache.values():
            v["qv"] = news_jev.QUESTION_VERSION - 1
    j.annotate(old)
    assert old["feed"][0]["jev"]["label"] == "bullish" and len(api.calls) == 2   # too old to re-ask: shown as is
