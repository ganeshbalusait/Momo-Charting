"""AI for the Ask chat (2026-11-10): momx/chat_ai.py. The AI is a fake; nothing leaves the PC."""
from __future__ import annotations

import time

import pytest

from momx import bot_desk
from momx import chat_ai as ai
from momx import chatbot as cb
from test_momx_chatbot import DAY, NOW, loser, signal, write

LINES = ["SPY on 2026-11-09: Level 2 call signal at 9:38 AM with the stock at $743.91.",
         "The 0.20-delta call ($753) cost $1.15 at the mid.",
         "The Bot's exit (below EMA21) at 2:21 PM was $2.76: +139%.",
         "Three contracts at the mid would have been +$482 at the Bot's exit."]


def says(text, provider="openai"):
    return lambda system, user, schema: {"ok": True, "provider": provider, "data": {"reply": text}}


def routes(**fields):
    base = {"intent": "what_if", "ticker": "", "day": "", "time": "", "side": ""}
    return lambda system, user, schema: {"ok": True, "provider": "openai", "data": {**base, **fields}}


# ---------------------------------------------------------------- the reply may only use numbers from the records
def test_a_reply_that_uses_only_the_records_numbers_is_shown():
    out = ai.reply("spy profit or loss", LINES, says("Profit: 3 contracts would have made +$482 at the Bot's exit at 2:21 PM, up 139% from $1.15."))
    assert out == {"text": "Profit: 3 contracts would have made +$482 at the Bot's exit at 2:21 PM, up 139% from $1.15.", "source": "openai"}


@pytest.mark.parametrize("text", [
    "Profit of $500.",                                     # not in the records
    "It made +$482, about 140%.",                          # rounded differently from the record's 139%
    "Up 139% - and it should reach $760 tomorrow.",        # an invented price
    "Profit +$482 on 5 contracts.",                        # an invented count
    "", "   ", "```json {}```", "x" * 900])
def test_a_reply_with_a_number_that_is_not_in_the_records_is_thrown_away(text):
    assert ai.reply("spy profit or loss", LINES, says(text)) is None


def test_numbers_are_compared_by_value_not_by_how_they_are_written():
    lines = ["It closed for +$1,350.50 on 12 contracts, 9:05 AM.", "Three of them were sold first."]
    assert ai.checked("Up $1350.5 on 12 contracts; 3 came off first at 9:05.", lines) is not None
    assert ai.checked("It was day 2 of the move.", lines) is None
    assert ai.checked("You asked about 10:30; the records show 12 contracts.", lines, "what if I took it at 10:30") is not None     # a number from the question
    assert ai.checked("A plain sentence with no figures.", lines) == "A plain sentence with no figures."


@pytest.mark.parametrize("reply", [{"ok": False, "error": "429 quota"}, {"ok": True, "data": None}, {"ok": True, "data": {"reply": 5}}, None, "text"])
def test_a_failed_ai_means_no_reply_not_an_error(reply):
    assert ai.reply("spy profit or loss", LINES, lambda *a: reply) is None
    assert ai.reply("spy", [], says("anything")) is None                     # nothing to write from


def test_a_slow_or_crashing_ai_does_not_hold_the_chat():
    def slow(*a):
        time.sleep(5)
        return {"ok": True, "data": {"reply": "late"}}
    started = time.monotonic()
    assert ai.reply("spy", LINES, slow, seconds=0.2) is None
    assert time.monotonic() - started < 2

    def boom(*a):
        raise RuntimeError("provider down")
    assert ai.reply("spy", LINES, boom) is None and ai.understand("anything", DAY, None, boom) is None


def test_the_ai_is_told_to_use_only_the_facts_and_sees_only_them():
    seen = []
    ai.reply("spy profit or loss", LINES, lambda system, user, schema: seen.append((system, user)) or {"ok": False})
    assert "Use ONLY the facts given" in seen[0][0] and "never add a number" in seen[0][0] and "No advice to buy or sell" in seen[0][0]
    assert '"facts":[' in seen[0][1] and "$743.91" in seen[0][1] and "artifacts" not in seen[0][1]


# ---------------------------------------------------------------- understanding a question the chatbot does not know
def test_an_understood_question_is_put_in_the_chatbots_own_words():
    assert ai.rephrase({"intent": "what_if", "ticker": "NVDA", "day": "2026-11-09", "time": "10:30", "side": "put"}) == "$NVDA put how much did it go 2026-11-09 at 10:30"
    assert ai.rephrase({"intent": "why_missed", "ticker": "T", "day": "", "time": "", "side": ""}) == "$T why was it missed"
    assert ai.rephrase({"intent": "status", "ticker": "", "day": "", "time": "", "side": ""}) == "what is the bot doing"
    for intent, want in (("what_if", "what_if"), ("why_failed", "why_failed"), ("why_missed", "why_missed"), ("show", "show")):
        assert cb.parse_intent(ai.rephrase({"intent": intent, "ticker": "AMD", "day": "", "time": "", "side": ""})) == want


def test_what_the_ai_returns_is_cleaned_before_it_is_used():
    good = ai.understand("did that nvidia trade make money", DAY, None, routes(intent="what_if", ticker="nvda", day="2026-11-09", time="10:30", side="put"))
    assert good == {"intent": "what_if", "ticker": "NVDA", "day": "2026-11-09", "time": "10:30", "side": "put"}
    odd = ai.understand("x", DAY, None, routes(intent="why_failed", ticker="../etc/passwd", day="tomorrow", time="soon", side="sideways"))
    assert odd == {"intent": "why_failed", "ticker": "", "day": "", "time": "", "side": ""}
    assert ai.understand("tell me a joke", DAY, None, routes(intent="none")) is None
    assert ai.understand("x", DAY, None, lambda *a: {"ok": True, "data": {"intent": "delete everything"}}) is None


def test_a_question_the_chatbot_does_not_recognise_is_answered_from_the_records_once_the_ai_understands_it(tmp_path):
    write(tmp_path, bull=[signal("NVDA")])
    plain = cb.ask("did that nvidia trade make money", tmp_path, NOW)
    assert plain["intent"] == "help"                                          # the no-AI chatbot alone gives up here

    def fake(system, user, schema):
        if "route a trader's question" in system:
            return routes(intent="what_if", ticker="NVDA")(system, user, schema)
        return says("Profit: the 0.20-delta call went from $1.15 to $2.76 at the Bot's exit.")(system, user, schema)

    out = ai.answer("did that nvidia trade make money", tmp_path, NOW, None, fake)
    assert (out["intent"], out["ticker"], out["understoodBy"]) == ("what_if", "NVDA", "AI")
    assert any("The 0.20-delta call ($110) cost $1.15 at the mid." in line for line in out["lines"])         # the facts are the chatbot's
    assert out["ai"] == {"text": "Profit: the 0.20-delta call went from $1.15 to $2.76 at the Bot's exit.", "source": "openai"}


def test_a_recognised_question_gets_a_reply_on_top_and_keeps_its_record_lines(tmp_path):
    write(tmp_path, bull=[loser("AAPL")])
    plain = cb.ask("why aapl failed", tmp_path, NOW)
    out = ai.answer("why aapl failed", tmp_path, NOW, None, says("It lost 40%: price was below VWAP and a wall sat just ahead."))
    assert out["lines"] == plain["lines"] and out["intent"] == "why_failed" and "understoodBy" not in out
    assert out["ai"]["text"].startswith("It lost 40%")


def test_every_ai_failure_leaves_the_plain_records_answer(tmp_path, monkeypatch):
    monkeypatch.setattr(bot_desk, "EVALUATION_START", DAY)
    write(tmp_path, bull=[signal("SPY")])
    plain = cb.ask("spy how much", tmp_path, NOW)
    for broken in (lambda *a: {"ok": False, "error": "quota"}, lambda *a: (_ for _ in ()).throw(RuntimeError("down")),
                   says("It made exactly $99,999."), lambda *a: None):
        assert ai.answer("spy how much", tmp_path, NOW, None, broken) == plain
    # An unrecognised question the AI cannot place stays the help answer, with no AI reply on top.
    out = ai.answer("tell me a joke", tmp_path, NOW, None, routes(intent="none"))
    assert out["intent"] == "help" and "ai" not in out
    # The AI names a ticker the app has nothing on: still no invented answer.
    out = ai.answer("anything on zzzz lately", tmp_path, NOW, None, routes(intent="show", ticker="ZZZZ"))
    assert out["intent"] == "help" and "ai" not in out


def test_the_ai_can_be_switched_off(tmp_path, monkeypatch):
    write(tmp_path, bull=[signal("SPY")])
    monkeypatch.setenv("AGX_CHAT_AI", "0")
    out = ai.answer("spy how much", tmp_path, NOW, None, lambda *a: pytest.fail("the AI must not be called when it is switched off"))
    assert "ai" not in out and out["intent"] == "what_if"


# ---- a refused AI is left alone for a while, so the chat stays fast
@pytest.fixture(autouse=True)
def _ai_not_resting():
    ai._REST.update(until=0.0, why="")
    yield
    ai._REST.update(until=0.0, why="")


def test_after_a_quota_refusal_the_ai_is_not_asked_again_for_a_while(tmp_path, monkeypatch):
    write(tmp_path, bull=[signal("SPY")])
    calls = []
    clock = [1000.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ai, "_default_ask", lambda system, user, schema: calls.append(1) or {"ok": False, "error": "OpenAI HTTP 429: You exceeded your current quota"})
    first = ai.answer("spy how much", tmp_path, NOW)
    assert len(calls) == 1 and "ai" not in first and first["aiNote"] == "the AI service is over its free limit"
    for _ in range(5):
        again = ai.answer("spy how much", tmp_path, NOW)
    assert len(calls) == 1 and again["lines"] == first["lines"] and again["aiNote"] == "the AI service is over its free limit"
    clock[0] += ai.REST_QUOTA_SECONDS + 1
    monkeypatch.setattr(ai, "_default_ask", lambda system, user, schema: calls.append(1) or {"ok": True, "provider": "openai", "data": {"reply": "Up 140%."}})
    back = ai.answer("spy how much", tmp_path, NOW)
    assert len(calls) == 2 and back["ai"]["text"] == "Up 140%." and "aiNote" not in back


def test_another_kind_of_failure_rests_for_a_shorter_time_and_a_stand_in_ai_is_never_rested(tmp_path, monkeypatch):
    clock = [50.0]
    monkeypatch.setattr(ai.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(ai, "_default_ask", lambda *a: {"ok": False, "error": "connection reset"})
    assert ai.reply("spy", LINES) is None and ai.resting() == "the AI service did not answer"
    clock[0] += ai.REST_SECONDS + 1
    assert ai.resting() == ""
    for _ in range(3):                                                        # a test's own AI stand-in is always asked
        assert ai.reply("spy", LINES, lambda *a: {"ok": False, "error": "429 quota"}) is None
    assert ai.resting() == ""
