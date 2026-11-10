"""Loss lessons (2026-11-09): the AI studies the paper bot's losing real fills per strategy.

momx/loss_lessons.py. The AI is a fake here; nothing leaves the PC.
"""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from momx import loss_lessons as ll

START = "2026-11-10"
EVENING = datetime(2026, 11, 10, 17, 30, tzinfo=ll.ET)        # a Tuesday


def fill(pl, symbol="AAPL", rule="level1", side="bull", day="2026-11-10", clock="09:40:00", **kw):
    at = f"{day}T{clock}-05:00"
    row = {"side": side, "rule": rule, "symbol": symbol, "at": at, "status": "closed", "qty": 5, "buyFill": 0.6, "botEntry": 0.6,
           "buyAt": at, "buyFillAt": at, "closedAt": f"{day}T10:10:00-05:00", "pl": pl, "spreadAtOrder": 0.08,
           "sells": [{"qty": 5, "price": 0.4, "why": "Bot exit: stop", "at": f"{day}T10:10:00-05:00"}], "delta": 0.18}
    row.update(kw)
    return row


def write(tmp_path, account, day, rows):
    path = tmp_path / "momx_paper_mirror" / account / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": {f"k{i}": r for i, r in enumerate(rows)}}), encoding="utf-8")


def good_answer(count=9):
    return lambda system, user: {"ok": True, "provider": "openai", "data": {
        "lesson": "Most losing trades were bought in the first 30 minutes on a wide spread.",
        "proposedChange": "Try waiting until 10:00 before buying.", "howToTest": "Compare the next 30 trades before and after 10:00.",
        "bucket": "exit", "group": "Bot exit: stop", "evidenceCount": count}}


def test_only_real_closed_fills_since_the_start_day_are_studied(tmp_path):
    write(tmp_path, "paper3", "2026-11-10", [
        fill(-50.0), fill(40.0, "MSFT"),
        fill(-70.0, "OLD", day="2026-11-06"),                                  # before the fresh start
        {**fill(-10.0, "OPEN"), "status": "open", "pl": None},                 # not closed
        {**fill(-10.0, "MISS"), "status": "missed", "buyFill": None},          # never filled
        {**fill(-10.0, "NOPL"), "pl": None}])                                   # closed but unpriced
    write(tmp_path, "paper5", "2026-11-10", [fill(-20.0, "NVDA", side="bear")])
    rows = ll.load_fills(tmp_path, START)
    assert sorted((r["account"], r["symbol"]) for r in rows) == [("paper3", "AAPL"), ("paper3", "MSFT"), ("paper5", "NVDA")]


def test_a_carried_orders_newest_state_is_the_one_studied(tmp_path):
    held = {**fill(-1.0), "status": "open", "pl": None}
    write(tmp_path, "paper3", "2026-11-10", [held])
    write(tmp_path, "paper3", "2026-11-11", [fill(-80.0)])                    # same key k0, closed the next day
    rows = ll.load_fills(tmp_path, START)
    assert len(rows) == 1 and rows[0]["pl"] == -80.0


def test_the_counts_show_where_the_losses_cluster():
    losers = [fill(-60.0, clock="09:35:00", spreadAtOrder=0.30) for _ in range(4)] + [fill(-30.0, clock="13:20:00")]
    winners = [fill(90.0, clock="13:10:00"), fill(50.0, clock="09:36:00", spreadAtOrder=0.30)]
    f = ll.facts(losers, winners)
    assert (f["losses"], f["wins"], f["netPnl"], f["avgLoss"], f["avgWin"]) == (5, 2, -130.0, -54.0, 70.0)
    assert f["when"][0] == {"group": "first 30 minutes", "losses": 4, "wins": 1}
    assert {"group": "spread over 25%", "losses": 4, "wins": 1} in f["spread"]
    assert f["exit"] == [{"group": "Bot exit: stop", "losses": 5, "wins": 2}]
    assert f["medianMinutesHeldLosers"] == 35           # 09:35 -> 10:10; a close before the buy is ignored
    assert ll.biggest_cluster(f)["group"] == "Bot exit: stop"


def test_too_few_losses_means_no_ai_call_and_says_how_many_are_needed():
    def never(*a):
        pytest.fail("the AI must not be asked about a handful of trades")
    out = ll.study("bull", "level1", [fill(-10.0)] * 3, [fill(10.0)], never)
    assert out["status"] == "collecting" and out["lesson"] == "Not enough losing trades yet (3 of 10)."
    assert (out["losses"], out["wins"], out["proposedChange"]) == (3, 1, "")


def test_a_good_ai_answer_becomes_the_lesson_with_its_source():
    seen = []
    def ask(system, user):
        seen.append((system, json.loads(user)))
        return good_answer()(system, user)
    out = ll.study("bear", "level2", [fill(-40.0, side="bear", rule="level2")] * 12, [fill(30.0)] * 2, ask)
    assert out["status"] == "lesson" and out["source"] == "openai" and out["evidenceCount"] == 9
    assert out["basis"] == "12 of 12 losing trades and 2 of 2 winning trades were 'Bot exit: stop'"
    assert "copy that table's exact group text" in seen[0][0]
    assert out["lesson"].startswith("Most losing trades") and out["proposedChange"].startswith("Try waiting")
    assert "cannot block" in seen[0][0] and "Never invent a number" in seen[0][0]
    sent = seen[0][1]
    assert sent["strategy"] == "level2" and sent["side"] == "puts" and sent["counts"]["losses"] == 12
    assert len(sent["worstLosingTrades"]) == ll.SAMPLE


@pytest.mark.parametrize("answer", [
    {"ok": False, "error": "rate limited"},
    {"ok": True, "data": {"lesson": "x", "proposedChange": "y", "howToTest": "z", "bucket": "exit", "group": "Bot exit: stop", "evidenceCount": 99}},   # more than that group has
    {"ok": True, "data": {"lesson": "", "proposedChange": "y", "howToTest": "z", "bucket": "exit", "group": "Bot exit: stop", "evidenceCount": 3}},
    {"ok": True, "data": {"lesson": "x", "proposedChange": "y", "howToTest": "z", "bucket": "exit", "group": "Bot exit: stop", "evidenceCount": "many"}},
    {"ok": True, "data": {"lesson": "9 of 11 losers had spreads over 25%", "proposedChange": "y", "howToTest": "z", "bucket": "spread", "group": "spread over 25%", "evidenceCount": 9}},   # a group that is not in the counts
    {"ok": True, "data": {"lesson": "x", "proposedChange": "y", "howToTest": "z", "bucket": "weather", "group": "rain", "evidenceCount": 3}},
    {"ok": True, "data": {"lesson": "x", "proposedChange": "y", "howToTest": "z", "evidenceCount": 3}},              # names no group at all
    {"ok": True, "data": {"lesson": "x", "proposedChange": "y", "howToTest": "z", "bucket": "exit", "group": "Bot exit: stop", "evidenceCount": 0}},
    {"ok": True, "data": {"lesson": "no pattern", "proposedChange": "y", "howToTest": "z", "bucket": "none", "group": "", "evidenceCount": 4}},
    {"ok": True, "data": None}, None, "text"])
def test_a_failed_or_made_up_ai_answer_falls_back_to_the_plain_counts(answer):
    losers = [fill(-40.0, clock="09:35:00") for _ in range(11)]
    out = ll.study("bull", "level1", losers, [fill(30.0, clock="13:30:00")], lambda *a: answer)
    assert out["status"] == "numbers" and out["source"] == "numbers only" and out["aiError"]
    assert out["lesson"].startswith("11 of the 11 losing trades were in the group") and out["proposedChange"] == ""


def test_an_ai_that_raises_does_not_break_the_study():
    def boom(*a):
        raise RuntimeError("provider down")
    out = ll.study("bull", "level1", [fill(-40.0)] * 10, [], boom)
    assert out["status"] == "numbers" and out["aiError"] == "RuntimeError"


def test_run_saves_one_entry_per_strategy_and_side_and_read_serves_the_short_form(tmp_path):
    write(tmp_path, "paper3", "2026-11-10", [fill(-40.0, f"L{i}") for i in range(10)] + [fill(60.0, "W")]
          + [fill(-15.0, "P", rule="level3", side="bear")])
    calls = []
    doc = ll.run(tmp_path, START, ask=lambda s, u: calls.append(u) or good_answer()(s, u), now=EVENING)
    assert len(calls) == 1                                                     # level3 puts has 1 loss: not asked
    assert [(x["side"], x["rule"], x["status"]) for x in doc["lessons"]] == [("bull", "level1", "lesson"), ("bear", "level3", "collecting")]
    saved = ll.read(tmp_path)
    assert saved["updatedAt"] == "2026-11-10T17:30:00-05:00" and saved["startDay"] == START and saved["minLosses"] == 10
    assert "Nothing here blocks" in saved["note"]
    first = saved["lessons"][0]
    assert first["losses"] == 10 and first["wins"] == 1 and first["netPnl"] == -340.0 and "facts" not in first
    assert ll.read(tmp_path / "nowhere") is None


def test_no_fills_yet_still_saves_an_empty_study(tmp_path):
    doc = ll.run(tmp_path, START, ask=lambda *a: pytest.fail("nothing to ask about"), now=EVENING)
    assert doc["lessons"] == [] and ll.read(tmp_path)["lessons"] == []


def test_the_study_is_due_once_per_trading_evening_and_a_weekend_asks_nothing_new(tmp_path):
    import os
    at = lambda day, hour, minute=0: datetime(2026, 11, day, hour, minute, tzinfo=ll.ET)

    def traded(day, hour=15):
        """A paper day file last written during that day's session."""
        write(tmp_path, "paper3", f"2026-11-{day}", [fill(-20.0, day=f"2026-11-{day}")])
        os.utime(tmp_path / "momx_paper_mirror" / "paper3" / f"2026-11-{day}.json", (at(day, hour).timestamp(),) * 2)

    assert ll.study_point(at(10, 16, 59)) == at(9, 17)          # Tuesday before 17:00 -> Monday's close
    assert ll.study_point(at(14, 9)) == at(13, 17)              # Saturday -> Friday's close
    assert ll.study_point(at(16, 16)) == at(13, 17)             # Monday before 17:00 -> Friday's close
    assert ll.due(tmp_path, at(10, 3)) is True                  # never studied: run once, whatever the time
    traded(10)
    ll.run(tmp_path, START, ask=good_answer(), now=EVENING)     # Tuesday 17:30
    assert ll.due(tmp_path, at(10, 22)) is False                # already done this evening
    traded(11)
    assert ll.due(tmp_path, at(11, 16, 59)) is False            # Wednesday, market still open
    assert ll.due(tmp_path, at(11, 17, 5)) is True
    traded(13)
    ll.run(tmp_path, START, ask=good_answer(), now=at(13, 17, 5))            # Friday evening
    assert ll.due(tmp_path, at(14, 0, 30)) is False             # Saturday: no new trades, no new AI calls
    assert ll.due(tmp_path, at(15, 12)) is False                # Sunday
    assert ll.due(tmp_path, at(16, 16)) is False                # Monday before the close
    traded(16)
    assert ll.due(tmp_path, at(16, 17, 1)) is True
    ll.run(tmp_path, START, ask=good_answer(), now=at(12, 17, 30))           # pretend only Thursday's study exists
    assert ll.due(tmp_path, at(14, 9)) is True                  # Saturday catches up the missed Friday study


def test_the_scorecard_payload_carries_the_lessons(tmp_path, monkeypatch):
    from momx import bot_desk
    monkeypatch.setattr(bot_desk, "EVALUATION_START", START)
    write(tmp_path, "paper3", "2026-11-10", [fill(-40.0, f"L{i}") for i in range(10)])
    assert bot_desk.build(tmp_path, days=30, today="2026-11-10")["lessons"] is None       # not studied yet
    ll.run(tmp_path, START, ask=good_answer(), now=EVENING)
    lessons = bot_desk.build(tmp_path, days=30, today="2026-11-10")["lessons"]
    assert lessons["lessons"][0]["rule"] == "level1" and lessons["lessons"][0]["status"] == "lesson"


def test_a_no_clear_pattern_answer_is_accepted_when_it_cites_nothing():
    answer = {"ok": True, "provider": "openai", "data": {"lesson": "The counts show no clear pattern yet.", "proposedChange": "Keep collecting trades.",
                                                         "howToTest": "Look again after 20 more.", "bucket": "none", "group": "", "evidenceCount": 0}}
    out = ll.study("bull", "level1", [fill(-40.0)] * 10, [fill(30.0)], lambda *a: answer)
    assert out["status"] == "lesson" and out["basis"] == "no single group stands out in the counts"


def test_the_same_signal_filled_in_both_accounts_is_studied_once(tmp_path):
    losers = [fill(-40.0, f"L{i}") for i in range(5)]
    write(tmp_path, "paper3", "2026-11-10", losers)
    write(tmp_path, "paper5", "2026-11-10", [dict(r, pl=-55.0) for r in losers])       # same signals, the other account's fills
    doc = ll.run(tmp_path, START, ask=lambda *a: pytest.fail("5 losing signals are not 10"), now=EVENING)
    lesson = doc["lessons"][0]
    assert (lesson["losses"], lesson["netPnl"], lesson["status"]) == (5, -200.0, "collecting")       # paper3's fills, once


def test_one_order_counts_for_every_level_it_qualified_for(tmp_path):
    rows = [fill(-40.0, f"T{i}", rule="level3", levels=["level1", "level2", "level3"]) for i in range(4)]
    rows += [fill(-30.0, f"S{i}", rule="level2", levels=["level1", "level2"]) for i in range(3)]
    write(tmp_path, "paper3", "2026-11-10", rows)
    doc = ll.run(tmp_path, START, ask=lambda *a: pytest.fail("under 10 losses"), now=EVENING)
    assert {x["rule"]: x["losses"] for x in doc["lessons"]} == {"level1": 7, "level2": 7, "level3": 4}


def test_a_fill_in_paper5_is_still_studied_when_paper3_missed_that_signal(tmp_path):
    write(tmp_path, "paper3", "2026-11-10", [{**fill(-1.0), "status": "missed", "buyFill": None, "pl": None}])
    write(tmp_path, "paper5", "2026-11-10", [fill(-55.0)])
    doc = ll.run(tmp_path, START, ask=lambda *a: pytest.fail("one loss is not ten"), now=EVENING)
    assert [(x["losses"], x["netPnl"]) for x in doc["lessons"]] == [(1, -55.0)]


def test_a_market_holiday_with_no_new_paper_file_asks_the_ai_nothing(tmp_path):
    import os
    write(tmp_path, "paper3", "2026-11-25", [fill(-20.0, day="2026-11-25")])
    wednesday = datetime(2026, 11, 25, 17, 30, tzinfo=ll.ET)
    os.utime(tmp_path / "momx_paper_mirror" / "paper3" / "2026-11-25.json", (wednesday.timestamp() - 3600,) * 2)
    ll.run(tmp_path, "2026-11-10", ask=good_answer(), now=wednesday)
    thanksgiving = datetime(2026, 11, 26, 18, 0, tzinfo=ll.ET)
    assert ll.study_point(thanksgiving) > wednesday                                     # a weekday, so the time rule alone would say "due"
    assert ll.due(tmp_path, thanksgiving) is False
