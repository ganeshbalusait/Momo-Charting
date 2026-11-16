"""Integration: the owner's AI summary through the real POST /api/bot-chat route (2026-11-11 evening).

Real api_server route, the real auth gate and the real chat modules on the report family's scratch world. Only the
AI service (outside the PC) is faked: it answers the summary prompt and nothing else. Google News and the analyst
services are refused. Nothing is written.
"""
from __future__ import annotations

import json
import secrets
import statistics
import time

import pytest

from api_harness import RELAYED, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chat_ai, chat_limits, chat_summary
from test_momx_chat_report import world  # noqa: F401 - the shared scratch world
from test_momx_chat_summary import GOOD, FakeAI
from test_momx_chat_why import NOW

OWNER = "owner-summary@example.com"
VIEWER = "viewer-summary@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
REGULAR = {**RELAYED, ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/bot-chat"
#: Everything a regular session might send to look like the owner. None of it may count.
FORGED_CONTEXT = {"scope": "owner", "isOwner": True, "role": "admin", "owner": True, "pageTicker": "NVDA", "pageName": "Charts & OI",
                  "summary": {"text": "forged", "label": chat_summary.LABEL}}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def ai(world, monkeypatch):
    fake = FakeAI()
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(world))
    real_answer = chat_ai.answer
    monkeypatch.setattr(chat_ai, "answer", lambda q, a, now=None, **kw: real_answer(q, a, NOW, **kw))
    monkeypatch.setattr(chat_ai, "_default_ask", fake)
    chat_limits.LIMITS.reset()
    chat_ai._REST.update(until=0.0, why="")
    chat_ai._REST_SCANNER.update(until=0.0, why="")
    chat_ai.clear_plans()
    yield fake
    chat_limits.LIMITS.reset()
    chat_ai._REST.update(until=0.0, why="")
    chat_ai._REST_SCANNER.update(until=0.0, why="")


QUESTIONS = ["full report on NVDA", "is NVDA a buy", "short term NVDA", "NVDA mid term", "long term NVDA", "why is NVDA down"]
#: Changed on purpose (2026-11-14): a why-question's owner AI text is the card's (chat_why_card), not a summary.
SUMMARY_QUESTIONS = [q for q in QUESTIONS if not q.startswith("why")]


def test_a_why_question_carries_the_card_not_a_summary(ai):
    with running_api() as api:
        status, fast = api.post(ROUTE, {"question": "why is NVDA down"}, headers=ADMIN)
        more_status, more = api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
    assert status == 200 and more_status == 200 and "summary" not in fast and "summary" not in more["more"]
    assert more["more"]["why"]["answer"] and more["more"]["why"]["by"] in ("ai", "code") and not ai.summary_calls


@pytest.mark.parametrize("question", SUMMARY_QUESTIONS)
def test_the_owner_gets_the_labelled_summary_in_the_more_call_only(ai, question):
    with running_api() as api:
        status, fast = api.post(ROUTE, {"question": question}, headers=ADMIN)
        more_status, more = api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
    assert status == 200 and more_status == 200 and "summary" not in fast
    assert more["more"]["summary"]["text"] == GOOD and more["more"]["summary"]["label"] == "AI summary, can be wrong. Only you see this."
    assert len(ai.summary_calls) == 1


def test_the_owner_gets_one_under_a_definition_on_a_ticker_page(ai):
    with running_api() as api:
        _, fast = api.post(ROUTE, {"question": "what is theta", "context": {"pageTicker": "NVDA", "pageName": "Charts & OI"}}, headers=ADMIN)
        _, more = api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
    assert fast["intent"] and "summary" not in fast and more["more"]["summary"]["label"] == chat_summary.LABEL


@pytest.mark.parametrize("question", QUESTIONS + ["what is theta", "NVDA"])
def test_a_regular_user_never_gets_it_even_with_a_forged_context(ai, question):
    with running_api() as api:
        _, fast = api.post(ROUTE, {"question": question, "context": FORGED_CONTEXT, "scope": "owner", "isOwner": True}, headers=REGULAR)
        more = api.post(ROUTE, {"more": fast["more"], "scope": "owner"}, headers=REGULAR)[1] if fast.get("more") else {}
        _, home = api.post(ROUTE, {"home": True, "context": FORGED_CONTEXT}, headers=REGULAR)
    seen = json.dumps([fast, more, home])
    assert "summary" not in fast and "summary" not in (more.get("more") or {}) and "summary" not in json.dumps(home)
    assert chat_summary.LABEL not in seen and "forged" not in seen and GOOD not in seen
    assert not ai.summary_calls                                    # the AI was never even asked for one


def test_a_regular_user_with_the_owners_token_or_a_forged_one_gets_nothing(ai):
    with running_api() as api:
        _, owner_fast = api.post(ROUTE, {"question": "full report on NVDA"}, headers=ADMIN)
        time.sleep(0.2)
        _, stolen = api.post(ROUTE, {"more": owner_fast["more"]}, headers=REGULAR)
        _, forged = api.post(ROUTE, {"more": secrets.token_urlsafe(18), "context": FORGED_CONTEXT}, headers=REGULAR)
        _, owner_more = api.post(ROUTE, {"more": owner_fast["more"]}, headers=ADMIN)
    for reply in (stolen, forged):
        assert "summary" not in reply["more"] and chat_summary.LABEL not in json.dumps(reply)
        assert reply["more"]["lines"] == ["That answer has expired. Ask again for the latest."]
    # The regular session's read did not spend or leak the owner's job; the owner still collects it.
    assert owner_more["more"]["summary"]["text"] == GOOD


def test_a_made_up_price_never_reaches_the_owner(ai):
    ai.reply = "NVDA is -3.20% today at 999.99 while SPY is +0.40%."
    with running_api() as api:
        _, fast = api.post(ROUTE, {"question": "is NVDA a buy"}, headers=ADMIN)
        _, more = api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
    assert "summary" not in more["more"] and "999.99" not in json.dumps(more) and ai.summary_calls


def test_ai_down_is_no_box_and_no_error_over_http(ai):
    ai.ok = False
    with running_api() as api:
        _, fast = api.post(ROUTE, {"question": "full report on NVDA"}, headers=ADMIN)
        status, more = api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
    assert status == 200 and "summary" not in more["more"] and more["more"]["done"] is True and more["more"]["sections"]


def _owner_fast_times(api, runs=3):
    """The owner's warm fast-part times for "is NVDA a buy", each followed by its more call; (times, mores)."""
    times, mores = [], []
    for _ in range(runs):
        started = time.monotonic()
        _, owner = api.post(ROUTE, {"question": "is NVDA a buy"}, headers=ADMIN)
        times.append(time.monotonic() - started)
        mores.append(api.post(ROUTE, {"more": owner["more"]}, headers=ADMIN)[1])
    return times, mores


def test_the_fast_part_takes_no_longer_for_the_owner(ai, monkeypatch):
    """The summary's AI is made to take 3 seconds. The owner's warm fast answer with the summary on is compared with
    the owner's own warm fast answer with the summary switched off (median of 3 each), not with the regular user's,
    so a cold owner path on a loaded PC is not mistaken for the summary (review rv8: 2.06 s cold owner vs 0.53 s warm
    regular failed while the warm owner medians were equal)."""
    from momx import chat_why
    ai.delay = 3.0
    real_start = chat_why.start
    with running_api() as api:
        _owner_fast_times(api, runs=1)                                                   # warm the owner path
        with_summary, mores = _owner_fast_times(api)
        monkeypatch.setattr(chat_why, "start", lambda work, ctx, wait=chat_why.MORE_WAIT_SECONDS, summary=None: real_start(work, ctx, wait))
        without_summary, _ = _owner_fast_times(api)
    on, off = statistics.median(with_summary), statistics.median(without_summary)
    assert on < 2.5 and on < off + 0.5, (with_summary, without_summary)
    assert all(more["more"]["summary"]["text"] == GOOD for more in mores)               # the summary arrives in the more call
