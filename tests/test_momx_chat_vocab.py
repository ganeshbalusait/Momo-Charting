"""More ways to ask "why did it move" (2026-11-14, the trader: "Our bot is not very smart, I need our bot very smart.
Add all words that will be good"). Every phrasing here was tried on the router first: the ones marked new were not
understood before this change (they fell through to the AI planner or got the whole day)."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import chat_ai, chat_facts

ET = ZoneInfo("America/New_York")
SATURDAY = datetime(2026, 11, 14, 7, 52, tzinfo=ET)
FRIDAY_LIVE = datetime(2026, 11, 13, 14, 10, tzinfo=ET)
KNOWN = frozenset({"NVDA", "SPY", "QQQ", "TSLA", "AAPL", "AMD"})


def _route(question, now=SATURDAY, scope="scanner", page="SPY"):
    ctx = {"scope": scope, "account": "x", "pageTicker": page, "pageName": None, "ticker": None, "day": None, "side": None, "now": now}
    plan = chat_ai.sure_calls(question, ctx, KNOWN) or {}
    calls = plan.get("calls") or []
    return calls[0] if calls else None


def _why(call):
    return call and (call.get("tool") or call.get("name")) == "why_move"


@pytest.mark.parametrize("question, ticker", [
    # new: slang and plain words
    ("why spy bleeding", "SPY"), ("why spy sell off", "SPY"), ("why nvda pumped", "NVDA"), ("why is tsla mooning", "TSLA"),
    ("why tsla ripping", "TSLA"), ("why qqq weak", "QQQ"), ("why spy weak today", "SPY"), ("why did nvda crater", "NVDA"),
    ("why is amd squeezing", "AMD"), ("why did spy nosedive", "SPY"), ("why spy under pressure", "SPY"),
    # new: other ways to ask why
    ("what's up with nvda", "NVDA"), ("whats wrong with nvda", "NVDA"), ("whats with nvda today", "NVDA"),
    ("how come spy is down", "SPY"), ("how come nvda dropped", "NVDA"), ("reason spy fell", "SPY"), ("reason for spy drop", "SPY"),
    ("what caused the drop in spy", "SPY"), ("what made spy fall", "SPY"), ("what's driving spy lower", "SPY"),
    ("explain spy drop", "SPY"), ("spy drop reason", "SPY"), ("why'd nvda tank", "NVDA"),
    # already understood, kept
    ("Why spy down yesterday morning?", "SPY"), ("why spy tanked", "SPY"), ("why is spy red", "SPY"), ("spy dumping why?", "SPY"),
    ("what happened to spy", "SPY"), ("whats going on with nvda", "NVDA"), ("why did amd pop", "AMD"), ("why nvidia down", "NVDA"),
])
def test_a_why_question_in_the_traders_words_is_the_why_answer_for_that_ticker(question, ticker):
    call = _route(question)
    assert _why(call) and call["args"].get("ticker") == ticker, (question, call)


@pytest.mark.parametrize("question", ["why the market sold off", "what is driving the market", "why market down",
                                      "how come stocks are red today", "what caused the market selloff"])
def test_a_market_why_question_in_the_traders_words_is_the_markets_answer(question):
    call = _route(question, page=None)
    assert _why(call) and call["args"].get("market") is True, (question, call)


@pytest.mark.parametrize("question", ["explain rvol", "explain the red color", "explain what sqz means", "what is a call wall"])
def test_a_help_question_is_not_a_why_question(question):
    assert not _why(_route(question)), question


def test_the_owners_bot_question_with_a_clock_time_stays_with_the_bot():
    # "lose" is about the Bot's trade, not the stock's move (the owner's original rule).
    assert not _why(_route("why did AMD lose at 3:45", now=FRIDAY_LIVE, scope="owner"))


def test_the_owners_market_question_with_a_clock_time_is_the_why_answer():
    call = _route("why spy dropped at 10am", now=FRIDAY_LIVE, scope="owner")
    assert _why(call) and call["args"].get("ticker") == "SPY"


@pytest.mark.parametrize("question, asked", [
    ("why did spy drop at the open", "morning"), ("why spy down in the first hour", "morning"),
    ("why spy down at lunch", "midday"), ("why spy fell midday", "midday"), ("why spy down around noon", "midday"),
    ("why spy down after lunch", "afternoon"), ("why spy down this afternoon", "afternoon"),
    ("why spy dumped into the close", "close"), ("why spy down at power hour", "close"), ("why spy rallied end of day", "close"),
    ("why spy sold off in the last hour", "close"), ("why spy down eod", "close"),
    ("why spy gap down", "gap"), ("why did spy gap up", "gap"), ("why did nvda open lower", "gap"),
    ("why spy dropped at 10am", "clock:600"), ("why spy fell around 2:30", "clock:870"), ("why spy down at 3pm", "clock:900"),
    ("why spy spiked at 10:15", "clock:615"),
    # sessions still win
    ("why spy down premarket", "pre"), ("why spy down after hours", "after"), ("why spy down at the close", "regular"),
    ("why spy down this morning before the open", "pre"),
    # a percent or a price is not a clock time
    ("why spy down 1%", None), ("why nvda up 5% today", None), ("why spy down to 750", None),
])
def test_the_part_of_the_day_a_question_names(question, asked):
    assert chat_facts.asked_session(question) == asked, question


@pytest.mark.parametrize("asked, label, text", [
    ("morning", "Friday morning", "9:30 AM-12:00 PM ET, Fri Nov 13"),
    ("midday", "Friday midday", "11:30 AM-1:30 PM ET, Fri Nov 13"),
    ("afternoon", "Friday afternoon", "12:00 PM-4:00 PM ET, Fri Nov 13"),
    ("close", "Friday into the close", "3:00 PM-4:00 PM ET, Fri Nov 13"),
    ("gap", "Friday at the open", "9:30 AM open, Fri Nov 13"),
    ("clock:600", "Friday around 10:00 AM", "9:45 AM-10:15 AM ET, Fri Nov 13"),
])
def test_on_the_weekend_each_part_is_fridays_and_says_friday(asked, label, text):
    spec = chat_facts.ask_window(asked, SATURDAY, "2026-11-13")
    assert (spec["label"], spec["text"], spec["day"]) == (label, text, "2026-11-13")
    assert spec["words"] == label and not spec["soFar"]


def test_during_the_session_a_part_not_reached_yet_is_today_so_far():
    spec = chat_facts.ask_window("close", FRIDAY_LIVE, None)                # power hour asked at 2:10 PM
    assert spec["kind"] == "today" and spec["label"] == "Today so far"
    spec = chat_facts.ask_window("midday", FRIDAY_LIVE, None)               # lunch is over: the whole of it
    assert (spec["kind"], spec["start"], spec["end"], spec["label"]) == ("midday", 690, 810, "Midday")


def test_a_named_past_day_gets_its_part_not_the_whole_day():
    spec = chat_facts.part_window("morning", "2026-11-12", FRIDAY_LIVE)     # "yesterday morning" asked on Friday
    assert (spec["day"], spec["start"], spec["end"], spec["label"]) == ("2026-11-12", 570, 720, "Thursday morning")
    assert chat_facts.part_window(None, "2026-11-12", FRIDAY_LIVE) is None


def test_the_gap_is_the_open_against_the_previous_close():
    view = {"ticker": "SPY", "open": 760.0, "prevClose": 757.12, "pctClose": -0.5, "pctOpen": -1.0, "prevDay": "2026-11-12"}
    spec = chat_facts.ask_window("gap", SATURDAY, "2026-11-13")
    assert chat_facts.window_pct(view, spec) == pytest.approx(0.38, abs=0.01)
    assert chat_facts.window_line([view], spec) == "Friday at the open (9:30 AM open, Fri Nov 13): SPY opened +0.38% from Thursday's close."


def test_a_clock_window_is_measured_from_its_start():
    view = {"ticker": "SPY", "pctStart": -0.42, "pctClose": -0.8, "pctOpen": -1.1, "prevDay": "2026-11-12"}
    spec = chat_facts.ask_window("clock:600", SATURDAY, "2026-11-13")
    assert chat_facts.window_pct(view, spec) == -0.42
    assert chat_facts.window_line([view], spec) == ("Friday around 10:00 AM (9:45 AM-10:15 AM ET, Fri Nov 13): SPY -0.42% from 9:45 AM, "
                                                     "-0.80% from Thursday's close.")


def test_the_answer_names_the_base_for_each_part():
    from momx import chat_why_card
    close = chat_facts.ask_window("close", SATURDAY, "2026-11-13")
    assert chat_why_card.code_answer("SPY", -0.6, close, [], True, []).startswith(
        "SPY fell 0.60% Friday into the close (3:00 PM-4:00 PM ET, Fri Nov 13).")
    clock = chat_facts.ask_window("clock:600", SATURDAY, "2026-11-13")
    assert chat_why_card.code_answer("SPY", -0.8, clock, [], True, []).startswith(
        "SPY fell 0.80% Friday around 10:00 AM (9:45 AM-10:15 AM ET, Fri Nov 13).")


def test_a_morning_that_gapped_and_turned_tells_both_halves():
    from momx import chat_why_card
    morning = chat_facts.part_window("morning", "2026-11-12", FRIDAY_LIVE)
    text = chat_why_card.turned_answer("NVDA", -5.10, -13.15, 9.27, morning, "Wednesday's close")
    assert text == ("NVDA was down 5.10% from Wednesday's close Thursday morning (9:30 AM-12:00 PM ET, Thu Nov 12). "
                    "It opened 13.15% lower, then rose 9.27% from the open.")
    assert len(text) <= chat_why_card.ANSWER_MAX
    gap = chat_facts.ask_window("gap", SATURDAY, "2026-11-13")
    assert chat_why_card.code_answer("SPY", -0.4, gap, [], True, [], "Thursday's close").startswith(
        "SPY opened 0.40% below Thursday's close (9:30 AM open, Fri Nov 13).")
