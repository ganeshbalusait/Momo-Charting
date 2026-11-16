"""Integration: "can you predict the price" through the real POST /api/bot-chat route (2026-11-15).

Real api_server route, the real auth gate and the real chat modules on the report family's scratch world. The AI
service is faked and must never be asked: the words are certain, so the answer is the app's own. Nothing is written.
Set CHAT_PREDICT_CAPTURE to a file path to save the regular user's reply for the browser spec."""
from __future__ import annotations

import json
import os

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chat_ai, chat_limits
from test_momx_chat_report import world  # noqa: F401 - the shared scratch world
from test_momx_chat_why import NOW

OWNER = "owner-predict@example.com"
VIEWER = "viewer-predict@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/bot-chat"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def asked(world, monkeypatch):
    calls: list = []

    def no_ai(system, user, schema):
        calls.append(system)
        return {"ok": False, "error": "the test allows no AI call"}

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    real_answer = chat_ai.answer
    monkeypatch.setattr(chat_ai, "answer", lambda q, a, now=None, **kw: real_answer(q, a, NOW, **kw))
    monkeypatch.setattr(chat_ai, "_default_ask", no_ai)
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    yield calls
    chat_limits.LIMITS.reset()


@pytest.mark.parametrize("headers", [ADMIN, REGULAR], ids=["owner", "regular user"])
@pytest.mark.parametrize("question", ["Can we predict the prize of NVDA?", "where will NVDA go tomorrow", "will NVDA go up"])
def test_a_prediction_question_is_answered_by_the_app_with_the_range_and_walls(asked, headers, question):
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": question, "context": {"pageTicker": "SPY", "pageName": "Charts & OI"}}, headers=headers)
    assert status == 200, body
    text = "\n".join(body["lines"])
    assert body["lines"][0] == chat_ai.NO_PREDICT_LINE
    assert body["ticker"] == "NVDA"
    assert "NVDA expected move" in text and "+/-" in text and "High OI board" in text, text
    assert "Release note" not in text and "release-notes" not in json.dumps(body.get("links") or []).lower()
    assert asked == []                                                   # no AI was asked for a certain question


def test_capture_the_real_reply_for_the_browser_spec(asked):
    out = os.environ.get("CHAT_PREDICT_CAPTURE")
    if not out:
        pytest.skip("only when the browser spec asks for the real reply")
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "Can we predict the prize of NVDA?", "context": {"pageTicker": "SPY"}}, headers=REGULAR)
    assert status == 200
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(body, handle)


# ---------------------------------------------------------------- the AI's second opinion on a rule that only guessed
# "where NVDA" has only the word "where" to go on: the rule guesses "open the chart" and marks it unsure, so the AI
# reads the meaning first (2026-11-15, "use LLM ... to understand the chat"). The AI service itself is faked.

@pytest.fixture
def planner(world, monkeypatch):
    state = {"reply": None, "asked": []}

    def fake(system, user, schema):
        state["asked"].append(json.loads(user)["question"])
        return state["reply"] if state["reply"] is not None else {"ok": False, "error": "timeout"}

    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    real_answer = chat_ai.answer
    monkeypatch.setattr(chat_ai, "answer", lambda q, a, now=None, **kw: real_answer(q, a, NOW, **kw))
    monkeypatch.setattr(chat_ai, "_default_ask", fake)
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    for rest in (chat_ai._REST, chat_ai._REST_SCANNER):
        rest.update(until=0.0, why="")
    yield state
    chat_limits.LIMITS.reset()
    for rest in (chat_ai._REST, chat_ai._REST_SCANNER):
        rest.update(until=0.0, why="")


@pytest.mark.parametrize("headers", [ADMIN, REGULAR], ids=["owner", "regular user"])
def test_a_guessing_rule_lets_the_ai_read_the_meaning_and_its_lookup_wins(planner, headers):
    planner["reply"] = {"ok": True, "data": {"calls": [{"tool": "price_outlook", "args": {"ticker": "NVDA"}}], "general": False}}
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "where NVDA", "context": {"pageTicker": "SPY"}}, headers=headers)
    assert status == 200 and planner["asked"] == ["where NVDA"]
    assert body["lines"][0] == chat_ai.NO_PREDICT_LINE and body["understoodBy"] == "AI" and body["ticker"] == "NVDA"


@pytest.mark.parametrize("reply", [None, {"ok": True, "data": {"calls": [], "general": False}}], ids=["the AI did not answer", "the AI named nothing"])
def test_when_the_ai_cannot_say_the_rules_own_answer_stands(planner, reply):
    planner["reply"] = reply
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": "where NVDA", "context": {"pageTicker": "SPY"}}, headers=ADMIN)
    assert status == 200 and planner["asked"] == ["where NVDA"]
    assert "Opening the NVDA 5m chart." in "\n".join(body["lines"]) and "understoodBy" not in body


@pytest.mark.parametrize("question", ["open the NVDA chart", "go to the NVDA chart", "where is the NVDA chart", "NVDA expected move", "Where NVDA go tomorrow?"])
def test_a_certain_question_never_asks_the_ai(planner, question):
    planner["reply"] = {"ok": True, "data": {"calls": [{"tool": "quote", "args": {"ticker": "NVDA"}}], "general": False}}
    with running_api() as api:
        status, body = api.post(ROUTE, {"question": question, "context": {"pageTicker": "SPY"}}, headers=ADMIN)
    assert status == 200 and planner["asked"] == [] and "understoodBy" not in body
