"""Unit: momx/jev_trade.py - the TypeSafe Jev shadow that reads each Bot trade.

Jev is a shadow: its P(take) is saved as jevP and the Bot still takes every
trade. So a Jev failure (no key, 401, outage, timeout, nonsense answer) must
never raise and never block, and the text Jev reads must be stable and never
crash on a half-filled scanner row. The transport is always a fake.
"""
from __future__ import annotations

import io
import re
import time
import urllib.error
from pathlib import Path

import pytest

from momx import jev_trade, option_track

FRONTEND_BOT = Path(__file__).resolve().parents[1] / "frontend" / "src" / "momxBot.js"
DAY = "2026-10-28"


@pytest.fixture(autouse=True)
def _no_news_file(monkeypatch):
    """The describer may look up Jev's news labels; never from the live artifacts/.
    The PC's own on/off switch (AGX_JEV_BOT) is pinned on so the judge is tested."""
    monkeypatch.setattr(jev_trade, "_NEWS_CACHE", {"at": time.time() + 10**9, "items": []})
    monkeypatch.setenv("AGX_JEV_BOT", "1")


def judge(post, **kwargs):
    sleeps = []
    j = jev_trade.JevTradeJudge(key="test-only-key", post=post, background=False, sleep=sleeps.append, **kwargs)
    return j, sleeps


def answer(take=0.42, model="jev-1.13.0"):
    return {"model": model, "answers": {
        "action": {"probabilities": {"take": take, "stand_aside": 1 - take}},
        "regime": {"noul": "yes"}, "direction": {"noul": "unclear"}, "pressure": {"noul": "no"},
        "news": {"noul": "yes"}, "quality": {"score": 2}, "risk": {"score": 1},
    }}


def test_the_three_cutoffs_agree_scanner_bot_and_page():
    page = re.search(r"export const BOT_JEV_CUTOFF = ([0-9.]+);", FRONTEND_BOT.read_text(encoding="utf-8"))
    assert page, "BOT_JEV_CUTOFF not found in momxBot.js"
    assert jev_trade.JEV_CUTOFF == option_track.JEV_PICK_CUTOFF == float(page.group(1)) == 0.30


def test_a_good_answer_is_handed_back_with_the_model_that_answered():
    posts, results = [], []
    j, sleeps = judge(lambda payload, key: posts.append(payload) or answer())
    assert j.submit("state text", "bull", results.append) is True
    assert len(posts) == 1 and posts[0]["model"] == jev_trade.MODEL
    assert results[0]["jevP"] == 0.42 and results[0]["jevModel"] == "jev-1.13.0"
    assert (results[0]["regime"], results[0]["quality"]) == ("yes", 2)
    assert results[0]["jevAt"] and sleeps == []


def test_without_a_key_jev_is_off_and_never_called(monkeypatch):
    monkeypatch.delenv("TYPESAFE_API_KEY", raising=False)
    calls = []
    j = jev_trade.JevTradeJudge(key="", post=lambda p, k: calls.append(p), background=False)
    assert j.enabled is False
    assert j.submit("state", "bull", lambda r: calls.append(r)) is False
    monkeypatch.setenv("AGX_JEV_BOT", "0")
    off = jev_trade.JevTradeJudge(key="k", post=lambda p, k: calls.append(p), background=False)
    assert off.enabled is False and off.submit("state", "bull", calls.append) is False
    assert calls == []


def test_a_refused_key_is_one_call_one_recorded_error_and_no_result():
    calls, results = [], []

    def refused(payload, key):
        calls.append(1)
        raise urllib.error.HTTPError(jev_trade.API_URL, 401, "Unauthorized", {}, io.BytesIO(b""))

    j, sleeps = judge(refused)
    j.submit("state", "bull", results.append)
    assert (len(calls), results, sleeps) == (1, [], [])
    assert j.stats["errors"] == 1 and j.stats["lastError"] == "HTTP 401"


@pytest.mark.parametrize("failure", [
    urllib.error.HTTPError(jev_trade.API_URL, 503, "busy", {}, io.BytesIO(b"")),
    TimeoutError("read timed out"),
    ConnectionResetError("reset"),
])
def test_an_outage_is_retried_with_backoff_then_given_up_without_raising(failure):
    calls, results = [], []

    def failing(payload, key):
        calls.append(1)
        raise failure

    j, sleeps = judge(failing)
    j.submit("state", "bear", results.append)
    assert len(calls) == 5 and results == []
    assert sleeps == [1.0, 2.0, 4.0, 8.0, 16.0]


def test_an_answer_without_a_take_probability_is_never_recorded():
    results = []
    j, _sleeps = judge(lambda payload, key: {"answers": {"action": {}}})
    j.submit("state", "bull", results.append)
    assert results == []


def test_a_failing_callback_never_escapes_the_judge():
    def explode(result):
        raise RuntimeError("book closed")

    j, _ = judge(lambda payload, key: answer())
    j.submit("state", "bull", explode)          # must not raise
    assert j.stats["asked"] == 1


def test_the_daily_cap_stops_calls():
    calls = []
    j, _ = judge(lambda payload, key: calls.append(1) or answer())
    j._day = time.strftime("%Y-%m-%d")
    j._calls = jev_trade.MAX_PER_DAY
    j.submit("state", "bull", lambda r: None)
    assert calls == []


def test_the_bear_question_mirrors_the_bull_question():
    bull, bear = jev_trade.questions("bull"), jev_trade.questions("bear")
    assert set(bull) == set(bear)
    assert "rise" in bull["action"]["criteria"]["take"] and "call" in bull["action"]["criteria"]["take"]
    assert "fall" in bear["action"]["criteria"]["take"] and "put" in bear["action"]["criteria"]["take"]
    assert bull["pressure"]["instructions"].startswith("Real buying") and bear["pressure"]["instructions"].startswith("Real selling")


@pytest.mark.parametrize("side", ["bull", "bear"])
def test_a_half_filled_row_is_described_without_raising_and_the_text_is_stable(side):
    trade = {"rule": "go", "symbol": "AAPL", "at": f"{DAY}T10:05:00-04:00", "entry": 1.2, "strike": 250}
    row = {"symbol": "AAPL", "last": 245.0, "pctChange": None, "rvol": {"5m": None}, "grade": {}}
    first = jev_trade.describe(trade, row, None, side, DAY)
    assert isinstance(first, str) and "AAPL" in first
    assert jev_trade.describe(trade, row, None, side, DAY) == first
    assert jev_trade.setup_tags({}, side, DAY) == []
    assert isinstance(jev_trade.levels_line(trade, {}, side, DAY), str)
    # The ticker dropped off the board (no scanner row at all) - still described.
    assert "AAPL" in jev_trade.describe(trade, {}, {}, side, DAY)
