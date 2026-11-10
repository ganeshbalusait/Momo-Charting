"""Integration: POST /api/bot-chat over HTTP (2026-11-10): the no-AI chatbot behind the real auth gate.

Real api_server route and the real momx/chatbot.py on scratch Bot / paper files.
No AI, nothing leaves the PC, nothing is written.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import bot_desk, chat_ai, chatbot
from test_momx_chatbot import order, signal, write

OWNER = "owner-chat@example.com"
VIEWER = "viewer-chat@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
ROUTE = "/api/bot-chat"
TODAY = datetime.now(ZoneInfo("America/New_York")).date().isoformat()


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def _no_real_ai(monkeypatch):
    """The AI service is outside the PC: every test here answers for it. By default it has no answer."""
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI in tests"})
    chat_ai._REST.update(until=0.0, why="")
    yield
    chat_ai._REST.update(until=0.0, why="")


@pytest.fixture
def records(tmp_path, monkeypatch):
    base = tmp_path / "grade"
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    stamp = lambda t: {**t, "at": t["at"].replace("2026-11-10", TODAY), "entryAt": t["entryAt"].replace("2026-11-10", TODAY),
                       "exitAt": t["exitAt"].replace("2026-11-10", TODAY)}
    paper = {**order("SPY"), "at": f"{TODAY}T09:38:00-05:00", "buyAt": f"{TODAY}T09:39:00-05:00", "closedAt": f"{TODAY}T14:22:00-05:00"}
    write(base, bull=[stamp(signal("SPY"))], paper3=[paper], day=TODAY)
    chatbot._CACHE.clear()
    chatbot._INDEX.clear()
    return base


def test_the_owner_asks_and_gets_an_answer_from_the_records(records):
    before = sorted(str(p) for p in records.rglob("*"))
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "SPY if I take how much it went up"}, headers=ADMIN)
        why_status, why = api.post(ROUTE, {"question": "why was spy missed"}, headers=ADMIN)
        bot_status, bot = api.post(ROUTE, {"question": "what is the bot doing"}, headers=ADMIN)
    assert status == 200 and (body["intent"], body["ticker"], body["day"]) == ("what_if", "SPY", TODAY)
    assert any("The 0.20-delta call ($110) cost $1.15 at the mid." in line for line in body["lines"])
    assert any("Paper 3 (Rules bot): bought 3 at $1.20, closed for +$468." in line for line in body["lines"])
    assert body["suggest"] == ["why SPY failed", "why was SPY missed"]
    assert why_status == 200 and why["intent"] == "why_missed" and any("It was not skipped." in line for line in why["lines"])
    assert bot_status == 200 and bot["intent"] == "status" and any(line.startswith("Rules bot: 1 closed trades, +$468") for line in bot["lines"])
    assert sorted(str(p) for p in records.rglob("*")) == before                # it only reads


def test_a_follow_up_sends_what_the_conversation_is_about(records):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "Profit or loss", "context": {"ticker": "SPY", "day": TODAY}}, headers=ADMIN)
        none_status, none = api.post(ROUTE, {"question": "Profit or loss"}, headers=ADMIN)
        odd_status, odd = api.post(ROUTE, {"question": "Profit or loss", "context": "SPY"}, headers=ADMIN)
    assert status == 200 and (body["intent"], body["ticker"], body["day"]) == ("what_if", "SPY", TODAY)
    assert any("Three contracts at the mid would have been +$483" in line for line in body["lines"])
    assert none_status == 200 and none["intent"] == "status"                   # nothing to continue: the bot's own result
    assert odd_status == 200 and odd["intent"] == "status"                     # a context that is not an object is ignored


def test_others_are_refused_and_an_empty_question_is_a_400(records):
    with running_api() as api:
        refused, body = api.post(ROUTE, {"question": "see spy"}, headers={ACCESS_EMAIL_HEADER: VIEWER})
        empty, _ = api.post(ROUTE, {"question": "   "}, headers=ADMIN)
        missing, _ = api.post(ROUTE, {}, headers=ADMIN)
        odd, _ = api.post(ROUTE, {"question": ["spy"]}, headers=ADMIN)
        unknown_status, unknown = api.post(ROUTE, {"question": "tell me a joke"}, headers=ADMIN)
    assert refused == 403 and "lines" not in body
    assert (empty, missing, odd) == (400, 400, 400)
    assert unknown_status == 200 and unknown["intent"] == "help" and unknown["suggest"]


def test_the_ai_reply_is_served_on_top_of_the_record_lines_and_a_made_up_number_is_dropped(records, monkeypatch):
    replies = iter(["Profit: three contracts would have made +$483 at the Bot's exit, up 140% from $1.15.",
                    "Profit of exactly $9,999."])
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": True, "provider": "openai", "data": {"reply": next(replies)}})
    chat_ai._REST.update(until=0.0, why="")
    with running_api() as api:
        status, good = api.post(ROUTE, {"question": "SPY if I take how much it went up"}, headers=ADMIN)
        _, bad = api.post(ROUTE, {"question": "SPY if I take how much it went up"}, headers=ADMIN)
    assert status == 200 and good["ai"] == {"text": "Profit: three contracts would have made +$483 at the Bot's exit, up 140% from $1.15.", "source": "openai"}
    assert any("The 0.20-delta call ($110) cost $1.15 at the mid." in line for line in good["lines"])          # the records are still there
    assert "ai" not in bad and bad["lines"] == good["lines"]                                                   # $9,999 is in no record


def test_a_question_only_the_ai_understands_is_still_answered_from_the_records(records, monkeypatch):
    def fake(system, user, schema):
        if "route a trader's question" in system:
            return {"ok": True, "provider": "openai", "data": {"intent": "what_if", "ticker": "SPY", "day": "", "time": "", "side": ""}}
        return {"ok": False, "error": "quota"}                                 # it understood, then ran out: plain lines
    monkeypatch.setattr(chat_ai, "_default_ask", fake)
    chat_ai._REST.update(until=0.0, why="")
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "did that S and P trade make any money for me"}, headers=ADMIN)
        _, viewer = api.post(ROUTE, {"question": "did that S and P trade make any money for me"}, headers={ACCESS_EMAIL_HEADER: VIEWER})
    assert status == 200 and (body["intent"], body["ticker"], body["understoodBy"]) == ("what_if", "SPY", "AI")
    assert "ai" not in body and any("Three contracts at the mid would have been +$483" in line for line in body["lines"])
    assert "lines" not in viewer                                               # still admin only, AI or not
