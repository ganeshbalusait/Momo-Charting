"""Unit: "can you predict the price" in the Ask chat (2026-11-15).

The trader asked "Can we predict the prize of app?" on the SPY chart and got two release notes that mention
"predictions". The chat never forecasts. A question about where a price is going now gets one plain line, then the
range the options market is pricing and the biggest walls, for the ticker named or the one on screen. No AI."""
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import chat_ai

ET = ZoneInfo("America/New_York")
SUNDAY = datetime(2026, 11, 15, 2, 26, tzinfo=ET)
KNOWN = frozenset({"NVDA", "SPY", "QQQ", "TSLA", "AAPL", "AMD", "APP"})


def _plan(question, scope="scanner", page="SPY"):
    ctx = {"scope": scope, "account": "x", "pageTicker": page, "pageName": None, "ticker": None, "day": None, "side": None, "now": SUNDAY}
    return chat_ai.sure_calls(question, ctx, KNOWN)


def _tools(plan):
    return [call["tool"] for call in (plan or {}).get("calls") or []]


@pytest.mark.parametrize("scope", ["owner", "scanner"])
@pytest.mark.parametrize("question, ticker", [
    ("Can we predict the prize of app?", "SPY"),          # his words, typos and all: the ticker on the screen
    ("Can we predict the price of app?", "SPY"), ("predict the price", "SPY"), ("can you predict the market", "SPY"),
    ("predict spy price", "SPY"), ("spy price prediction", "SPY"), ("predict aapl", "AAPL"), ("nvda forecast", "NVDA"),
    ("where will nvda go tomorrow", "NVDA"), ("where is spy headed", "SPY"), ("will spy go up tomorrow", "SPY"),
    ("is spy going up or down", "SPY"), ("will nvda hit 200", "NVDA"), ("what will apple do next week", "AAPL"),
    ("how high can tsla go", "TSLA"), ("is nvda going to crash", "NVDA"), ("will the market crash", "SPY"),
    # his next try, in his own words: it opened the SPY chart
    ("Where spy go tomorrow?", "SPY"), ("where nvda going", "NVDA"), ("where spy headed", "SPY"), ("spy tomorrow?", "SPY"),
    ("spy up tomorrow?", "SPY"), ("nvda next week", "NVDA"), ("what next for spy", "SPY"), ("spy direction", "SPY"),
    ("which way is nvda going", "NVDA"), ("tsla go up next week?", "TSLA"), ("what happen to spy tomorrow", "SPY"),
])
def test_a_where_is_the_price_going_question_gets_the_plain_line_then_the_range_and_the_walls(scope, question, ticker):
    plan = _plan(question, scope)
    assert _tools(plan) == ["price_outlook"], (question, plan)
    assert plan["calls"][0]["args"]["ticker"] == ticker, (question, plan)


def test_the_plain_line_is_the_same_one_the_lookup_writes():
    from momx import chat_market
    assert chat_market.NO_PREDICT_LINE == chat_ai.NO_PREDICT_LINE


def test_the_plain_line_makes_no_forecast_and_gives_no_advice():
    for line in (chat_ai.NO_PREDICT_LINE, chat_ai.NO_PREDICT_TICKER_LINE):
        assert line.startswith("No one can predict a price")
        assert not chat_ai._ADVICE.search(line.lower()) and not chat_ai._FORECAST.search(line)


def test_tomorrow_is_passed_on_as_the_expiry_asked_for():
    plan = _plan("will spy go up tomorrow")
    assert plan["calls"][0]["args"].get("expiry") == "tomorrow"


def test_with_no_ticker_anywhere_it_says_so_and_asks_for_one():
    plan = _plan("can you predict prices", page=None)
    assert plan == {"calls": [], "lines": [chat_ai.NO_PREDICT_TICKER_LINE]}


@pytest.mark.parametrize("question, tool", [
    ("what is the price target for nvda", "analyst"),     # analysts' targets keep their own answer
    ("why did spy drop", "why_move"),                      # a why-question stays the why-answer
    ("spy price", "quote"), ("expected move spy", "expected_move"), ("call wall on spy", "walls"),
    ("expected move spy tomorrow", "expected_move"), ("where is the call wall on spy", "walls"), ("where is spy vwap", "chart_levels"),
    ("open spy chart", "open_chart"), ("nvda earnings tomorrow", "earnings"), ("what happened to spy", "why_move"),
])
def test_other_questions_keep_their_own_answer(question, tool):
    assert _tools(_plan(question))[:1] == [tool], question


@pytest.mark.parametrize("question", ["did the bot predict nvda", "what will the bot do", "open the spy chart", "go to the scanner",
                                      "will my nvda calls go up"])
def test_bot_trade_and_go_somewhere_questions_are_not_a_prediction_answer(question):
    assert _tools(_plan(question, "owner")) != ["price_outlook"], question
