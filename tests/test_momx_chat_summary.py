"""The owner's AI summary (2026-11-11 evening, momx/chat_summary.py): one labelled paragraph, owner only, made in the "more" job.

Unit tests on the report family's scratch world (tests/test_momx_chat_report.py): the AI is a stand-in that answers
only the summary prompt, Google News and the analyst services are refused, nothing leaves the PC and nothing is written.
"""
from __future__ import annotations

import json
import statistics
import threading
import time

import pytest

from momx import chat_ai, chat_report, chat_summary, chat_why
from test_momx_chat_report import world  # noqa: F401 - the shared scratch world
from test_momx_chat_why import NOW, ctx

GOOD = "NVDA is -3.20% today at 170.00 while SPY is +0.40% and QQQ is +0.60%. The Long Term rule says AVOID."


class FakeAI:
    """Answers the summary prompt with `reply`; every other prompt (the why-answer's categories) gets an empty, valid
    answer, so the why part never rests the AI before the summary is asked (a failed call rests it, see chat_ai._rest)."""

    def __init__(self, reply=GOOD, ok=True, delay=0.0):
        self.reply, self.ok, self.delay = reply, ok, delay
        self.summary_calls: list[dict] = []
        self.lock = threading.Lock()

    def __call__(self, system, user, schema):
        if system != chat_summary.SYSTEM:
            return {"ok": True, "data": {"reasons": [], "explained": False}}
        with self.lock:
            self.summary_calls.append(json.loads(user))
        if self.delay:
            time.sleep(self.delay)
        return {"ok": True, "data": {"summary": self.reply}} if self.ok else {"ok": False, "error": "AI down"}


def _report(world, scope, ai, kind="report", account="v@x"):
    out = chat_report.TOOLS[kind]["handler"]({"ticker": "NVDA"}, ctx(world, scope, ask=ai, account=account))
    return out, chat_why.collect(out["more"], account, scope)


# ---------------------------------------------------------------- the owner gets it, labelled
@pytest.mark.parametrize("kind", ["report", "rules", "outlook_short", "outlook_mid", "outlook_long"])
def test_the_owner_gets_the_summary_labelled_in_the_more_part_only(world, kind):
    ai = FakeAI()
    fast, more = _report(world, "owner", ai, kind)
    assert "summary" not in fast                                         # never in the fast part
    assert more["summary"] == {"text": GOOD, "label": "AI summary, can be wrong. Only you see this.", "asOf": more["summary"]["asOf"]}
    assert more["summary"]["asOf"].startswith("written ")
    assert len(ai.summary_calls) == 1                                    # the report's own why part makes none of its own


def test_the_summary_is_given_every_section_with_its_time_and_the_headlines_as_quoted_json(world):
    ai = FakeAI()
    _report(world, "owner", ai)
    sent = ai.summary_calls[0]
    facts = "\n".join(sent["FACTS"])
    assert sent["stock"] == "NVDA" and isinstance(sent["HEADLINES"], list)
    for piece in ("SPY +0.40%", "QQQ +0.60%", "Semiconductors", "Confirm Level 2", "expected move", "Biggest call wall", "Put/call ratio",
                  "IV about", "Long Term rule: AVOID", "Fair value at a 50% margin of safety", "Earnings", "Short term (today to about 2 weeks)",
                  "Mid term", "Long term", "Bot and paper trades today (owner)", "(as of 14:2"):
        assert piece in facts, piece
    assert "vwap" not in facts.lower()


def test_the_why_answer_and_a_definition_carry_it_for_the_owner(world):
    ai = FakeAI()
    why = chat_why.why_move({"ticker": "NVDA", "question": "why is NVDA down"}, ctx(world, "owner", ask=ai))
    # Changed on purpose (2026-11-14): on a why-question the owner's AI writes the card's answer and reasons instead
    # (chat_why_card), so no separate summary is made; this fake writes no card, so the code-built card stands.
    got = chat_why.collect(why["more"], "v@x", "owner", wait=30)
    assert "summary" not in got and got["why"]["by"] == "code" and not ai.summary_calls
    owner = {**ctx(world, "owner", ask=ai, page="NVDA")}
    out = chat_ai._run_calls({"calls": [{"tool": "define", "args": {"query": "what is theta"}}]}, "what is theta", owner)
    assert out["more"] and chat_why.collect(out["more"], "v@x", "owner")["summary"]["text"] == GOOD


def test_a_past_session_and_the_whole_market_get_no_summary(world):
    ai = FakeAI()
    market = chat_why.why_move({"market": True, "question": "why is the market down"}, ctx(world, "owner", ask=ai))
    assert "summary" not in chat_why.collect(market["more"], "v@x", "owner") and not ai.summary_calls


# ---------------------------------------------------------------- a regular user never gets it, on any path
@pytest.mark.parametrize("kind", ["report", "rules", "outlook_short", "outlook_mid", "outlook_long"])
def test_a_regular_user_never_gets_it_and_the_ai_is_never_asked_for_one(world, kind):
    ai = FakeAI()
    fast, more = _report(world, "scanner", ai, kind)
    assert "summary" not in fast and "summary" not in more and not ai.summary_calls
    assert chat_summary.LABEL not in json.dumps([fast, more])


def test_a_regular_users_why_answer_and_definition_make_none(world):
    ai = FakeAI()
    why = chat_why.why_move({"ticker": "NVDA", "question": "why is NVDA down"}, ctx(world, "scanner", ask=ai))
    assert "summary" not in chat_why.collect(why["more"], "v@x", "scanner")
    out = chat_ai._run_calls({"calls": [{"tool": "define", "args": {"query": "what is theta"}}]}, "what is theta", ctx(world, "scanner", ask=ai, page="NVDA"))
    assert "more" not in out and not ai.summary_calls


def test_an_owner_token_read_by_a_regular_session_gives_nothing(world):
    ai = FakeAI()
    out = chat_report.TOOLS["report"]["handler"]({"ticker": "NVDA"}, ctx(world, "owner", ask=ai, account="owner@x"))
    stolen = chat_why.collect(out["more"], "viewer@x", "scanner")
    assert "summary" not in stolen and stolen["lines"] == ["That answer has expired. Ask again for the latest."]
    same_account = chat_why.collect(out["more"], "owner@x", "scanner")   # the right account in a regular session
    assert "summary" not in same_account


def test_a_forged_summary_in_a_regular_job_never_leaves_the_server(world):
    """A summary that somehow sat in a regular session's job, or arrived in a forged reply, is dropped on every way out."""
    forged = {"text": "Buy NVDA now", "label": chat_summary.LABEL}
    token = chat_why.start(lambda: {"lines": ["x"], "summary": forged}, ctx(world, "scanner"), wait=1.0)
    assert "summary" not in chat_why.collect(token, "v@x", "scanner")
    token = chat_why.start(lambda: {"lines": ["x"], "summary": forged}, ctx(world, "scanner"), wait=1.0)
    assert "summary" not in chat_ai.more_for_session(token, False, "v@x")["more"]
    owner_token = chat_why.start(lambda: {"lines": ["x"], "summary": forged}, ctx(world, "owner"), wait=1.0)
    assert "summary" not in chat_ai.more_for_session(owner_token, False, "v@x")["more"]
    for scope in ("owner", "scanner"):                                  # the fast part never carries one, for anyone
        assert "summary" not in chat_ai.finish({"intent": "help", "lines": ["x"], "summary": forged}, scope)


def test_summarize_refuses_anything_but_the_owner_scope(world):
    ai = FakeAI()
    for scope in ("scanner", "", "admin", None):
        assert chat_summary.summarize("NVDA", {**ctx(world, "owner", ask=ai), "scope": scope}) is None
    assert not ai.summary_calls


# ---------------------------------------------------------------- the guards
def test_a_made_up_price_is_dropped(world):
    ai = FakeAI("NVDA is -3.20% today at 999.99 while SPY is +0.40%.")
    _, more = _report(world, "owner", ai, "rules")
    assert "summary" not in more and len(ai.summary_calls) == 1


def test_a_wrong_sign_is_dropped(world):
    ai = FakeAI("NVDA is +3.20% today at 170.00.")
    _, more = _report(world, "owner", ai, "rules")
    assert "summary" not in more


FACTS = ["NVDA -3.20% today at 170.00 (scanner board, 14:28 ET).", "Market: SPY +0.40% (14:28 ET), QQQ +0.60% (14:28 ET)."]
HEADLINES = [{"headline": "TSLA and NVDA slide as chip stocks fall 4%", "source": "CNBC", "time": "13:00 ET"}]


@pytest.mark.parametrize("reply", [
    "NVDA is -3.20% today at 170.00. More at https://agx-verify.example/login.",
    "NVDA is -3.20% today at 170.00. More at www.example.com today.",
    "NVDA is -3.20% today at 170.00. Write to help@example.com for more.",
    "NVDA is -3.20% today at 170.00 and AMD is weaker.",                      # a ticker the facts never name
    "NVDA is -3.20% today at 170.00 and TSLA slid too.",                      # named only by a headline
    "NVDA is -3.20% today at 170.00 and Microsoft is lower.",                 # a company the facts never name
    "NVDA is -3.20% today at 170.00. You should buy calls now.",              # the advice backstop
])
def test_links_emails_stranger_tickers_and_advice_are_dropped(reply):
    assert chat_summary.guarded(reply, FACTS, HEADLINES, "NVDA") is None


def test_a_headline_number_may_be_quoted_and_a_clean_reply_passes():
    assert chat_summary.guarded("NVDA is -3.20% today at 170.00 while SPY is +0.40%.", FACTS, HEADLINES, "NVDA")
    assert chat_summary.guarded("NVDA is -3.20% today; CNBC says chip stocks fall 4%.", FACTS, HEADLINES, "NVDA")


def test_the_summary_is_cut_to_four_sentences():
    reply = "NVDA is -3.20% today. SPY is +0.40%. QQQ is +0.60%. NVDA is at 170.00. SPY is +0.40% again."
    kept = chat_summary.guarded(reply, FACTS, HEADLINES, "NVDA")
    assert kept == "NVDA is -3.20% today. SPY is +0.40%. QQQ is +0.60%. NVDA is at 170.00."


def test_clean_keeps_only_the_text_and_puts_the_fixed_label_on():
    assert chat_summary.clean({"text": " a  b ", "label": "something else", "asOf": "x"}) == {"text": "a b", "label": chat_summary.LABEL, "asOf": "x"}
    assert chat_summary.clean({"text": ""}) is None and chat_summary.clean("text") is None and chat_summary.clean(None) is None


# ---------------------------------------------------------------- AI down, and the fast part's speed
def test_ai_down_gives_no_box_and_no_error(world):
    ai = FakeAI(ok=False)
    _, more = _report(world, "owner", ai)
    assert "summary" not in more and more["done"] is True and more["sections"] and "error" not in json.dumps(more).lower()


def test_ai_switched_off_asks_nothing(world, monkeypatch):
    monkeypatch.setenv("AGX_CHAT_AI", "0")
    ai = FakeAI()
    _, more = _report(world, "owner", ai)
    assert "summary" not in more and not ai.summary_calls


def test_an_ai_that_throws_gives_no_box(world):
    def boom(system, user, schema):
        raise RuntimeError("down")
    _, more = _report(world, "owner", boom)
    assert "summary" not in more and more["done"] is True


def _owner_rules_times(world, ai, runs=3):
    """The owner's fast "rules" times, each followed by collecting its more part; (times, mores)."""
    times, mores = [], []
    for _ in range(runs):
        started = time.monotonic()
        out = chat_report.TOOLS["rules"]["handler"]({"ticker": "NVDA"}, ctx(world, "owner", ask=ai))
        times.append(time.monotonic() - started)
        mores.append(chat_why.collect(out["more"], "v@x", "owner"))
    return times, mores


def test_the_fast_part_is_not_slowed_by_the_summary(world, monkeypatch):
    """The summary's AI is made to take 3 seconds: the owner's warm fast answer with the summary on is no slower than
    his own warm fast answer with it switched off (median of 3 each), and the more call carries it. Compared owner with
    owner, warmed, so a cold first owner call on a busy PC is not mistaken for the summary (review rv8)."""
    slow = FakeAI(delay=3.0)
    _owner_rules_times(world, slow, runs=1)                                         # warm the owner path
    with_summary, mores = _owner_rules_times(world, slow)
    real_start = chat_why.start
    monkeypatch.setattr(chat_why, "start", lambda work, ctx, wait=chat_why.MORE_WAIT_SECONDS, summary=None: real_start(work, ctx, wait))
    without_summary, _ = _owner_rules_times(world, slow)
    on, off = statistics.median(with_summary), statistics.median(without_summary)
    assert on < 2.0 and on < off + 0.5, (with_summary, without_summary)
    assert all(more["summary"]["text"] == GOOD for more in mores)


def test_the_owner_more_job_waits_for_the_summary_budget():
    token = chat_why.start(lambda: {"lines": []}, {"scope": "owner", "account": "a"}, wait=1.0, summary="NVDA")
    with chat_why._lock:
        job = chat_why._jobs[token]
    assert job.wait == 1.0 + chat_summary.BUDGET_SECONDS
    token = chat_why.start(lambda: {"lines": []}, {"scope": "scanner", "account": "a"}, wait=1.0, summary="NVDA")
    with chat_why._lock:
        assert chat_why._jobs[token].wait == 1.0                       # a regular job is not even marked for one


def test_the_screen_shows_the_same_label_word_for_word():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "frontend" / "src" / "chatReport.js").read_text(encoding="utf-8")
    assert f'export const OWNER_SUMMARY_LABEL = "{chat_summary.LABEL}";' in source


def test_a_failed_summary_never_switches_the_ai_off_for_the_owners_other_answers(world, monkeypatch):
    """Found by tests/test_chat_screen_contract.py: a summary refused as "down" rested the owner's AI, so the next
    question's headline reasons were skipped. It still honours a rest and the day's cap that are already on."""
    fake = FakeAI(ok=False)
    monkeypatch.setattr(chat_ai, "_default_ask", fake)
    chat_ai._REST.update(until=0.0, why="")
    _, more = _report(world, "owner", None, "rules")
    assert "summary" not in more and len(fake.summary_calls) == 1 and chat_ai.resting("owner") == ""
    chat_ai._REST.update(until=time.monotonic() + 60, why="the AI service did not answer")
    try:
        fake.ok = True
        _, more = _report(world, "owner", None, "rules")
        assert "summary" not in more and len(fake.summary_calls) == 1          # resting: not asked at all
    finally:
        chat_ai._REST.update(until=0.0, why="")


# ---------------------------------------------------------------- a slow summary never costs the answer (review rv8)
@pytest.fixture
def slow_gather(monkeypatch):
    """Gathering the facts takes a second, as on a cold or loaded PC; the summary's time must cover it."""
    real = chat_summary.gather

    def gather(*args, **kwargs):
        time.sleep(1.0)
        return real(*args, **kwargs)
    monkeypatch.setattr(chat_summary, "gather", gather)


@pytest.mark.parametrize("delay", [7.9, 30.0])
def test_an_owner_definition_with_a_slow_or_hung_ai_is_done_with_no_error_line(world, slow_gather, delay):
    """Review rv8: a definition's more job waited 8 s for gather + an 8 s AI call, timed out and put the
    out-of-time line under a plain definition. A slow AI means no box and no error, inside about the 8 s wait."""
    owner = ctx(world, "owner", ask=FakeAI(delay=delay), page="NVDA")
    out = chat_ai._run_calls({"calls": [{"tool": "define", "args": {"query": "what is theta"}}]}, "what is theta", owner)
    started = time.monotonic()
    more = chat_why.collect(out["more"], "v@x", "owner")
    took = time.monotonic() - started
    assert more["done"] is True and "summary" not in more and chat_why.OUT_OF_TIME not in more["lines"], more
    # The follow-up waits at most the job's 8 s; the slack is for a below-normal test run on a busy PC.
    assert more["lines"] == [] and took < chat_summary.BUDGET_SECONDS + 2.0, took


def test_a_why_job_that_finished_keeps_its_reasons_under_a_hung_summary(world, slow_gather):
    """Review rv8: work done in 6.5 s (inside its 7 s) was thrown away when gather + a hung summary AI ran past
    the job's wait; the owner saw only the out-of-time line. The finished work now always comes back."""
    reason = 'Earnings or guidance: "NVDA beats" (CNBC, 9:00 AM ET)'

    def work():
        time.sleep(6.5)
        return {"lines": ["news reasons here"], "reasons": [reason]}
    owner = ctx(world, "owner", ask=FakeAI(delay=30.0))
    more = chat_why.collect(chat_why.start(work, owner, summary="NVDA"), "v@x", "owner")
    assert more["done"] is True and more["reasons"] == [reason] and more["lines"] == ["news reasons here"], more
    assert "summary" not in more


def test_the_finished_work_comes_back_when_the_wait_ends_before_the_summary(world):
    """collect's own wait running out while the summary is still being written returns the work, done, with no summary."""
    gate = threading.Event()
    owner = ctx(world, "owner", ask=lambda s, u, sc: (gate.wait(5), {"ok": True, "data": {"summary": GOOD}})[1])
    token = chat_why.start(lambda: {"lines": ["the work"]}, owner, wait=0.0, summary="NVDA")
    with chat_why._lock:
        job = chat_why._jobs[token]
    time.sleep(0.2)
    more = chat_why.collect(token, "v@x", "owner", wait=0.3)
    gate.set()
    assert job.done.wait(15)                       # the summary thread ends inside this test, never in a later one
    assert more["done"] is True and more["lines"] == ["the work"] and "summary" not in more
    assert chat_why.collect(token, "v@x", "owner")["lines"] == ["That answer has expired. Ask again for the latest."]


def test_summarize_skips_the_ai_when_gathering_used_the_time_up(world):
    ai = FakeAI()
    owner = ctx(world, "owner", ask=ai)
    assert chat_summary.summarize("NVDA", owner, None, deadline=time.monotonic() + 0.5) is None
    assert not ai.summary_calls
    assert chat_summary.summarize("NVDA", owner, None, deadline=time.monotonic() + 30)["text"] == GOOD


# ---------------------------------------------------------------- a headline's number is no price for the stock (review rv8)
PRICE_FACTS = ["NVDA +2.10% at 182.40 (10:31 ET).", "Market: SPY +0.40% (10:30 ET), QQQ +0.55% (10:30 ET)."]
PRICE_HEADLINES = [{"headline": "Nvidia price target raised to $250 at Morgan Stanley", "source": "Reuters", "time": "09:10 ET"},
                   {"headline": "Apple to cut 600 jobs", "source": "CNBC", "time": "08:00 ET"}]


@pytest.mark.parametrize("reply", [
    "NVDA trades at 250 after rising 2.10%.",              # a price target's number used as the stock's price
    "NVDA is at 600, up 2.10% on the day.",                 # another stock's headline number used as the price
    "NVDA trades at 195.",
    "Reuters says the target was raised. NVDA trades at 250.",   # named in one sentence, the number in another
])
def test_a_number_only_a_headline_has_is_dropped_outside_that_headlines_sentence(reply):
    assert chat_summary.guarded(reply, PRICE_FACTS, PRICE_HEADLINES, "NVDA") is None


def test_a_headline_number_quoted_with_its_source_still_passes():
    assert chat_summary.guarded("NVDA is +2.10% at 182.40. Reuters says the price target was raised to $250.",
                                PRICE_FACTS, PRICE_HEADLINES, "NVDA")


# ---------------------------------------------------------------- a direction word is a sign (review rv8 r2)
@pytest.mark.parametrize("reply", [
    "NVDA slid 2.10% to 182.40.",
    "NVDA sank 2.10% today.",
    "NVDA tumbled 2.10%.",
    "NVDA is off 2.10% at 182.40.",
    "NVDA posted a 2.10% loss.",                           # the direction word comes after the number
    "NVDA plunged 2.10% while SPY dipped 0.40%.",
    "NVDA is down 2.10%.",
    "NVDA is +2.10% at 182.40 while SPY slipped 0.40%.",   # the second number's word, not the first's
])
def test_a_stock_that_is_up_is_never_described_as_falling(reply):
    assert chat_summary.guarded(reply, PRICE_FACTS, PRICE_HEADLINES, "NVDA") is None


@pytest.mark.parametrize("reply", [
    "NVDA rose 2.10% to 182.40.",
    "NVDA is up 2.10% at 182.40 while SPY gained 0.40%.",
    "NVDA trades at 182.40, up 2.10%.",
    "NVDA posted a 2.10% gain; SPY is higher by 0.40%.",
    "NVDA is +2.10% at 182.40. Reuters says the price target was raised to $250.",
])
def test_the_right_direction_still_passes(reply):
    assert chat_summary.guarded(reply, PRICE_FACTS, PRICE_HEADLINES, "NVDA") == reply


def test_a_falling_stock_described_as_falling_passes():
    assert chat_summary.guarded("NVDA fell 3.20% to 170.00 while SPY rose 0.40%.", FACTS, HEADLINES, "NVDA")
    assert chat_summary.guarded("NVDA is -3.20% today; CNBC says chip stocks fall 4%.", FACTS, HEADLINES, "NVDA")


# ---------------------------------------------------------------- a number written in words (review rv8 r2)
@pytest.mark.parametrize("reply", [
    "NVDA is up nine percent today.",
    "NVDA is up five percent and SPY rose 0.40%.",
    "NVDA has doubled this year.",
    "NVDA is +2.10%, about twice the move of SPY.",
    "NVDA is +2.10%, a threefold jump on SPY.",
])
def test_a_number_spelled_out_in_words_is_dropped(reply):
    assert chat_summary.guarded(reply, PRICE_FACTS, PRICE_HEADLINES, "NVDA") is None


def test_a_word_number_the_facts_use_themselves_passes_and_one_is_not_a_number():
    facts = [*PRICE_FACTS, "Short (today): bullish, strength three of four."]
    assert chat_summary.guarded("NVDA is +2.10%; the short view is bullish, strength three of four.", facts, [], "NVDA")
    assert chat_summary.guarded("NVDA is +2.10%, one of the stronger names today.", PRICE_FACTS, [], "NVDA")


def test_the_ai_is_told_to_write_numbers_in_digits():
    assert "digits" in chat_summary.SYSTEM


# ---------------------------------------------------------------- 2026-11-11 final recheck: any domain, lower-case tickers
_FACTS = ["NVDA +2.10% at 182.40 (10:31 ET).", "Market: SPY +0.40% (10:30 ET), QQQ +0.55% (10:30 ET)."]
_KNOWN = frozenset({"NVDA", "AMD", "INTC", "SMCI", "TSM", "TSLA", "SPY", "QQQ", "ALL", "NOW", "KEY", "LOW", "ON", "IT"})


@pytest.mark.parametrize("tail", ["news", "finance", "trade", "money", "exchange", "markets", "fund", "capital", "chat",
                                  "bot", "in", "club", "one", "group"])
def test_a_web_address_with_any_ending_is_dropped(tail):
    reply = f"NVDA is up 2.10% at 182.40. Full notes are on nvda-desk.{tail} today."
    assert chat_summary.guarded(reply, _FACTS, [], "NVDA", _KNOWN) is None


def test_a_path_style_address_is_dropped_and_plain_numbers_pass():
    assert chat_summary.guarded("NVDA is up 2.10% at 182.40. See a.b/path for more.", _FACTS, [], "NVDA", _KNOWN) is None
    assert chat_summary.guarded("Read more at nvda-signals.news today.", _FACTS, [], "NVDA", _KNOWN) is None
    assert chat_summary.guarded("NVDA is up 2.10% at 182.40, with SPY up 0.40%.", _FACTS, [], "NVDA", _KNOWN)


@pytest.mark.parametrize("reply", ["NVDA is up 2.10% at 182.40 while amd and intc fell.", "NVDA is up 2.10% at 182.40 while smci slid.",
                                   "NVDA is up 2.10% at 182.40 and $tsm dipped.", "NVDA is up 2.10% at 182.40 while Tsla was weak."])
def test_a_ticker_written_in_lower_or_mixed_case_is_a_stranger(reply):
    assert chat_summary.guarded(reply, _FACTS, [], "NVDA", _KNOWN) is None


def test_ordinary_lower_case_words_that_are_also_tickers_still_pass():
    reply = "NVDA is up 2.10% at 182.40 now, and it is on the high side of all of today's range."
    assert chat_summary.guarded(reply, _FACTS, [], "NVDA", _KNOWN)


def test_a_source_name_never_lets_in_a_ticker():
    headlines = [{"source": "AMD Insider", "headline": "Chip stocks rise", "at": "10:00 ET"}]
    assert chat_summary.guarded("NVDA is up 2.10% at 182.40 and AMD rallied per AMD Insider.", _FACTS, headlines, "NVDA", _KNOWN) is None
