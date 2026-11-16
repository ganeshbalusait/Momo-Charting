"""The Scorecard and the chart plan (momx/bot_desk.py, 2026-11-12): the new evaluation, the
earlier period, the new buy windows in "Not ordered", and Entry / Stop / T1-T3 on every trade."""
import json

from momx import bot_desk as bd


def at(day, clock):
    return f"{day}T{clock}-05:00"


def paper_row(symbol="AAPL", day="2026-11-13", clock="10:05:00", **extra):
    """A paper order on the chart plan, as momx/paper_mirror.py saves it."""
    row = {"side": "bull", "rule": "level2", "symbol": symbol, "at": at(day, clock), "qty": 3, "buyLimit": 1.25,
           "buyId": "b-" + symbol, "buyAt": at(day, clock), "status": "open", "buyFill": 1.25, "buyFilledQty": 3, "held": 3,
           "exitPlan": "chart", "entryBid": 1.15, "entryAsk": 1.25, "entryMid": 1.2, "entryStock": 100.0,
           "plan": {"side": "CALL", "strike": 104.0, "entrySpot": 100.0, "expectedMove": 4.0, "delta": 0.2, "gamma": 0.03,
                    "t1": 102.0, "t2": 104.0, "t3": 108.0},
           "stopStock0": 97.26, "stopStock": 97.26, "stopKind": "stop", "hits": [], "sells": [], "markMid": 1.3}
    row.update(extra)
    return row


def signal(symbol, clock, day="2026-11-13", **extra):
    t = {"rule": "level3", "symbol": symbol, "board": "Watchlist", "at": at(day, clock), "status": "open",
         "eContract": symbol + "_C", "eEntry": 2.0, "eEntryBid": 1.9, "eEntryAsk": 2.1, "eLast": 2.4, "eBest": 3.0}
    t.update(extra)
    return t


def test_the_new_evaluation_starts_on_the_next_session_with_the_chart_plan_in_its_name():
    assert bd.EVALUATION_START == "2026-11-13" == bd.CHART_PLAN_DAY and bd.LEVELS_START == "2026-11-10"
    assert bd.EVALUATION_LABEL.startswith("Level 1 / 2 / 3") and "chart check" in bd.EVALUATION_LABEL
    # Was "at the ask from 10:00" (the 10:00 rule, removed 2026-11-13).
    assert "at the ask from 9:30" in bd.EVALUATION_LABEL and "T1 / T2 / T3" in bd.EVALUATION_LABEL
    assert "10:00" not in bd.EVALUATION_LABEL
    (period,) = bd.EARLIER_PERIODS
    assert (period["startDay"], period["endDay"]) == ("2026-11-10", "2026-11-12")
    assert "+100%" in period["strategy"] and "9:30" in period["strategy"]


def test_a_signal_before_ten_is_not_turned_away_for_the_time_and_midday_names_the_windows():
    """Was test_a_signal_before_ten_says_before_ten_and_midday_names_the_new_windows (the 10:00
    rule, removed 2026-11-13): 9:30-10:00 is a buy window again on every day."""
    why = lambda clock, day="2026-11-13": bd._not_ordered_why("bull", signal("AAPL", clock, day), [], None)
    for clock in ("09:30:03", "09:59:59", "10:00:00", "13:00:00"):
        assert not why(clock).startswith(("before", "outside")), clock
    assert why("11:30:00") == "outside the buy windows (9:30-11:00, 1:00-3:30)"
    assert why("15:45:00") == "outside the buy windows (9:30-11:00, 1:00-3:30)"
    assert not why("09:35:00", "2026-11-12").startswith(("before", "outside"))
    assert why("11:30:00", "2026-11-12") == "outside the buy windows (9:30-11:00, 1:00-3:30)"


def test_a_940_signal_is_explained_by_the_real_reason_not_by_the_time():
    """Was test_before_ten_wins_over_every_other_reason (the 10:00 rule, removed 2026-11-13)."""
    t = signal("AAPL", "09:40:00", eEntry=0.02, eEntryBid=0.0)
    stop = {"at": at("2026-11-13", "09:31:00")}
    skips = {"k": {"symbol": "AAPL", "side": "bull", "at": t["at"], "why": "price under the 21 EMA"}}
    assert bd._not_ordered_why("bull", t, [], stop, skips, skips, skips) == "the daily loss stop was holding buys"
    assert bd._not_ordered_why("bull", t, [], None, skips, skips, skips) == "the 0.20-delta option was under $0.05 or had no bid"


def test_a_signal_with_no_chart_plan_says_so_and_one_clock_names_the_windows():
    """2026-11-13 review: the paper bot never buys a Level signal that has no chart plan, and the
    Scorecard gives that as the reason. The window is judged on the later of the signal and entry
    times, the same clock as the Bot's plan and the paper bot's buy."""
    bare = signal("AAPL", "10:12:00", planNote="No chart plan: the option chain was not available at the signal")
    assert bd._not_ordered_why("bull", bare, [], None) == (
        "no chart plan (the option chain was not available at the signal); the bot does not buy without a stop and targets")
    planned = signal("AAPL", "10:12:00", planNote="No chart plan: stale", plan={"t1": 1.0})
    assert not bd._not_ordered_why("bull", planned, [], None).startswith("no chart plan")
    lead = signal("AMD", "12:49:55", entryAt=at("2026-11-13", "12:50:20"))
    assert not bd._not_ordered_why("bull", lead, [], None).startswith("outside")
    late = signal("AMD", "10:59:50", entryAt=at("2026-11-13", "11:00:10"))
    assert bd._not_ordered_why("bull", late, [], None) == "outside the buy windows (9:30-11:00, 1:00-3:30)"


def test_the_page_is_told_which_plan_the_running_bots_follow(tmp_path):
    """The Bot page is live in the browser before the servers restart. The payload says "chart"
    only once EVERY paper account's running bot has written its chart-plan marker
    (paper_mirror.PaperMirror._plan_mark); until then "old", with the old plan's name."""
    from momx import paper_mirror as pm
    root = tmp_path / "momx_paper_mirror"
    for account in bd.ACCOUNTS:
        (root / account).mkdir(parents=True)
        (root / account / "2026-11-13.json").write_text(json.dumps({"rows": {"k": paper_row()}}), encoding="utf-8")
    doc = bd.build(tmp_path, 30, today="2026-11-13")
    assert doc["plan"] == "old" and doc["planSince"] is None and bd.plan_running(root) is None
    assert doc["oldStrategy"] == bd.EARLIER_PERIODS[0]["strategy"] and "+100%" in doc["oldStrategy"]
    (root / "paper3" / pm.PLAN_MARK_FILE).write_text(json.dumps({"plan": "chart", "since": at("2026-11-13", "08:05:00")}), encoding="utf-8")
    assert bd.build(tmp_path, 30, today="2026-11-13")["plan"] == "old"           # paper5 has not restarted
    (root / "paper5" / pm.PLAN_MARK_FILE).write_text(json.dumps({"plan": "chart", "since": at("2026-11-13", "08:05:10")}), encoding="utf-8")
    doc = bd.build(tmp_path, 30, today="2026-11-13")
    assert doc["plan"] == "chart" and doc["planSince"] == at("2026-11-13", "08:05:10")
    assert "plan.json" not in [p["day"] for p in []]                             # the marker is not a day file
    assert set(doc["accounts"]) == set(bd.ACCOUNTS)


def test_the_running_paper_bot_writes_the_marker_itself(tmp_path):
    from datetime import datetime
    from types import SimpleNamespace
    from momx import paper_mirror as pm
    client = SimpleNamespace(credentials=SimpleNamespace(paper=True))
    book = SimpleNamespace(trades=lambda: [])
    for account in bd.ACCOUNTS:
        m = pm.PaperMirror({"bull": book}, tmp_path / "momx_paper_mirror" / account, client_factory=lambda: client,
                           off=lambda: False, account=account)
        m.tick(datetime(2026, 11, 13, 8, 5, tzinfo=pm.ET))                       # before the open: still marked
    said = bd.plan_running(tmp_path / "momx_paper_mirror")
    assert said == {"plan": "chart", "since": "2026-11-13T08:05:00-05:00"}
    # A later day does not move "since".
    m.tick(datetime(2026, 11, 16, 8, 5, tzinfo=pm.ET))
    assert bd.plan_running(tmp_path / "momx_paper_mirror")["since"] == "2026-11-13T08:05:00-05:00"


def test_each_paper_trade_carries_entry_stop_and_the_three_targets():
    held = paper_row()
    hit = paper_row("MSFT", stopStock=100.0, stopKind="break-even", held=2,
                    hits=[{"target": "T1", "at": at("2026-11-13", "10:20:00"), "stock": 102.1, "level": 102.0}],
                    sells=[{"qty": 1, "price": 1.75, "why": "T1 hit: sold 1, stop to break-even", "at": at("2026-11-13", "10:20:10")}])
    done = paper_row("NVDA", status="closed", held=0, pl=-195.0, closedAt=at("2026-11-13", "10:31:00"),
                     sells=[{"qty": 3, "price": 0.6, "why": "Stop: stock traded through 97.26", "at": at("2026-11-13", "10:31:00")}])
    old = {"side": "bull", "rule": "level2", "symbol": "OLD", "at": at("2026-11-13", "10:06:00"), "qty": 3, "buyLimit": 1.0,
           "status": "open", "buyFill": 1.0, "buyId": "o"}
    log = bd._trade_log([held, hit, done, old])
    by = {r["symbol"]: r for r in log["rows"]}
    assert by["AAPL"]["plan"] == {"entryAsk": 1.25, "entryStock": 100.0, "stop": 97.26, "stopNow": 97.26, "stopKind": "stop",
                                  "t1": 102.0, "t2": 104.0, "t3": 108.0, "hits": [], "ready": True}
    assert by["MSFT"]["plan"]["stopNow"] == 100.0 and by["MSFT"]["plan"]["stop"] == 97.26
    assert by["MSFT"]["plan"]["hits"] == [{"target": "T1", "at": at("2026-11-13", "10:20:00")}]
    assert by["MSFT"]["note"] == "part sold: T1 hit: sold 1, stop to break-even"
    assert by["NVDA"]["note"] == "Stop: stock traded through 97.26" and by["NVDA"]["pnl"] == -195.0
    assert by["OLD"]["plan"] is None                                     # an order from before the chart plan
    # The journal's closed trades carry it too.
    assert bd._journal([done])["trades"][0]["plan"]["t3"] == 108.0


def test_an_order_whose_plan_has_not_arrived_yet_says_so():
    waiting = paper_row(plan=None, stopStock0=None, stopStock=None)
    plan = bd._trade_log([waiting])["rows"][0]["plan"]
    assert plan["ready"] is False and plan["entryAsk"] == 1.25 and plan["t1"] is None and plan["stop"] is None


def test_a_signal_not_ordered_shows_the_plan_it_would_have_had(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "_SIGNALS", {})
    planned = signal("AMD", "10:12:00", plan={"entry": 2.1, "entrySpot": 250.0, "stopStock": 247.5, "t1": 253.0, "t2": 256.0, "t3": 260.0},
                     planState={"status": "closed", "stopStock": 250.0, "stopKind": "break-even", "pct": 12.5,
                                "reason": "Stop at break-even: stock traded through 250.00",
                                "hits": [{"target": "T1", "at": at("2026-11-13", "10:30:00"), "stock": 253.2}]})
    early = signal("TSLA", "11:41:00", planNote="Not ordered: outside the buy windows (9:30-11:00, 1:00-3:30)")
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-13.json").write_text(json.dumps({"trades": [planned, early]}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-13.json").write_text(json.dumps({"rows": {"k": paper_row()}}), encoding="utf-8")
    doc = bd.build(tmp_path, 30, today="2026-11-13")
    assert doc["startDay"] == "2026-11-13"
    by = {r["symbol"]: r for r in doc["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["AMD"]["status"] == "signal"
    assert by["AMD"]["plan"] == {"entryAsk": 2.1, "entryStock": 250.0, "stop": 247.5, "stopNow": 250.0, "stopKind": "break-even",
                                 "t1": 253.0, "t2": 256.0, "t3": 260.0, "ready": True, "status": "closed", "pct": 12.5,
                                 "reason": "Stop at break-even: stock traded through 250.00",
                                 "hits": [{"target": "T1", "at": at("2026-11-13", "10:30:00")}]}
    # Was a 9:41 signal with "Not ordered: before 10:00" (the 10:00 rule, removed 2026-11-13).
    assert by["TSLA"]["note"].startswith("Not ordered: outside the buy windows (9:30-11:00") and by["TSLA"]["plan"] is None
    assert by["AAPL"]["status"] == "open" and by["AAPL"]["scored"] is True


def test_the_old_plans_trades_are_not_judged_and_stay_visible_as_the_earlier_period(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "_SIGNALS", {})
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)

    def old(symbol, day, pl, why, **extra):
        return {"side": "bull", "rule": "level2", "symbol": symbol, "at": at(day, "09:41:00"), "qty": 3, "buyLimit": 1.0,
                "status": "closed", "buyFill": 1.0, "buyFilledQty": 3, "pl": pl, "closedAt": at(day, "10:15:00"),
                "sells": [{"qty": 3, "price": 1.0 + pl / 300, "why": why, "at": at(day, "10:15:00")}], **extra}
    (folder / "2026-11-10.json").write_text(json.dumps({"rows": {"a": old("AAA", "2026-11-10", 150.0, "+100%: sell 2 of 3")}}), encoding="utf-8")
    still = {"side": "bull", "rule": "level3", "symbol": "CCC", "at": at("2026-11-12", "10:30:00"), "qty": 3, "buyLimit": 1.0,
             "status": "open", "buyFill": 1.0, "buyFilledQty": 3, "held": 3, "buyId": "c"}
    (folder / "2026-11-12.json").write_text(json.dumps({"rows": {"b": old("BBB", "2026-11-12", -270.0, "stop loss -50%"),
                                                                "c": still}}), encoding="utf-8")
    (folder / "2026-11-13.json").write_text(json.dumps({"rows": {
        "n": paper_row("NEW", status="closed", held=0, pl=90.0, closedAt=at("2026-11-13", "10:40:00"),
                       sells=[{"qty": 3, "price": 1.55, "why": "T2 hit: sold the last 1", "at": at("2026-11-13", "10:40:00")}])}}),
        encoding="utf-8")
    acct = bd.build(tmp_path, 30, today="2026-11-13")["accounts"]["paper3"]
    # The new evaluation holds only the chart-plan trade.
    assert acct["stats"]["n"] == 1 and acct["stats"]["netPnl"] == 90.0 and acct["balance"] == 10090.0
    assert [d["day"] for d in acct["days"]] == ["2026-11-13"]
    # The earlier period keeps its own record.
    (earlier,) = acct["earlier"]
    assert (earlier["startDay"], earlier["endDay"], earlier["orders"]) == ("2026-11-10", "2026-11-12", 3)
    assert (earlier["trades"], earlier["wins"], earlier["losses"], earlier["winRatePct"]) == (2, 1, 1, 50.0)
    assert (earlier["netPnl"], earlier["worstTrade"], earlier["bestTrade"], earlier["stillOpen"]) == (-120.0, -270.0, 150.0, 1)
    assert [r["symbol"] for r in earlier["rows"]] == ["BBB", "AAA"] and earlier["rows"][0]["exit"] == "stop loss -50%"
    assert "9:30" in earlier["strategy"] and "chart" in earlier["why"]
    # A position from the old plan that is still open is listed in All trades, not scored.
    live = next(r for r in acct["tradeLog"]["rows"] if r["symbol"] == "CCC")
    assert live["scored"] is False and live["note"].startswith("from before the fresh start, not scored") and live["plan"] is None


def test_an_earlier_period_never_double_counts_days_inside_the_current_evaluation(tmp_path, monkeypatch):
    """With an earlier start day (the tests' own, or a later change of mind) the same order is
    judged once: days from the start on belong to the current evaluation only."""
    monkeypatch.setattr(bd, "_SIGNALS", {})
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    row = {"side": "bull", "rule": "level2", "symbol": "AAA", "at": at("2026-11-11", "09:41:00"), "qty": 3, "buyLimit": 1.0,
           "status": "closed", "buyFill": 1.0, "buyFilledQty": 3, "pl": 60.0, "closedAt": at("2026-11-11", "10:15:00")}
    (folder / "2026-11-11.json").write_text(json.dumps({"rows": {"a": row}}), encoding="utf-8")
    acct = bd.build(tmp_path, 30, today="2026-11-12", start_day="2026-11-11")["accounts"]["paper3"]
    assert acct["stats"]["n"] == 1 and acct["earlier"] == []
    acct = bd.build(tmp_path, 30, today="2026-11-12", start_day="2026-11-12")["accounts"]["paper3"]
    assert acct["stats"]["n"] == 0 and acct["earlier"][0]["trades"] == 1


def test_a_signal_in_the_ten_minutes_before_a_window_is_not_called_outside_the_windows():
    """It IS bought when the window opens (2026-11-12: AMD 12:56:22 -> bought 13:00:03), so the
    Scorecard must not say "outside the buy windows" for it; the same rule as the Bot's plan."""
    why = lambda clock: bd._not_ordered_why("bull", signal("AMD", clock), [], None)
    assert not why("12:56:22").startswith(("outside", "before"))
    assert not why("12:50:00").startswith("outside")
    assert why("12:49:59") == "outside the buy windows (9:30-11:00, 1:00-3:30)"
    assert why("11:00:00") == "outside the buy windows (9:30-11:00, 1:00-3:30)"
    assert not why("09:55:00").startswith(("before", "outside"))      # was "before 10:00" (rule removed 2026-11-13)


def test_a_sale_the_broker_keeps_refusing_is_said_on_the_scorecard():
    said = "The broker refused this sale (5 times in a row; trying again every 30 minutes)"
    stuck = paper_row("NVDA", status="exit_due", note=said, sellRejects=5)
    fine = paper_row("AAPL", clock="10:06:00")
    by = {r["symbol"]: r for r in bd._trade_log([stuck, fine])["rows"]}
    assert by["NVDA"]["status"] == "open" and by["NVDA"]["note"] == said
    assert by["AAPL"]["note"] == "holding"


# ---------------------------------------------------------------------------
# 2026-11-13 second review: the new evaluation is the chart plan's, so it is split by the PLAN an
# order was opened under, not only by its date. The paper worker trades the older plan until it
# is restarted; an order it sends on the morning of the switch is the older plan's result.

def old_order(symbol, day="2026-11-13", clock="09:37:00", **extra):
    """An order the not-yet-restarted worker sent under the older plan: no exitPlan marker."""
    row = {"side": "bull", "rule": "level2", "symbol": symbol, "at": at(day, clock), "qty": 3, "buyLimit": 1.97,
           "buyId": "o-" + symbol, "buyAt": at(day, clock), "status": "closed", "buyFill": 1.97, "buyFilledQty": 3,
           "held": 0, "pl": -591.0, "closedAt": at(day, "09:58:00"),
           "sells": [{"qty": 3, "price": 0.0, "why": "stop loss -50%", "at": at(day, "09:58:00")}]}
    row.update(extra)
    return row


def _desk(tmp_path, monkeypatch, rows, day="2026-11-13"):
    monkeypatch.setattr(bd, "_SIGNALS", {})
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    return bd.build(tmp_path, 30, today=day)["accounts"]["paper3"]


def test_an_older_plan_order_sent_after_the_switch_is_not_in_the_new_evaluation(tmp_path, monkeypatch):
    acct = _desk(tmp_path, monkeypatch, {"o": old_order("OLD")})
    # The chart plan's record is still clean: no trade, $10,000.
    assert acct["stats"]["n"] == 0 and acct["stats"]["netPnl"] == 0 and acct["balance"] == 10000.0
    assert acct["orders"]["sent"] == 0 and acct["days"] == []
    # The order is kept, in the earlier period (the plan it was opened under).
    (earlier,) = acct["earlier"]
    assert (earlier["trades"], earlier["netPnl"], earlier["worstTrade"], earlier["afterSwitch"]) == (1, -591.0, -591.0, 1)
    assert [r["symbol"] for r in earlier["rows"]] == ["OLD"] and earlier["rows"][0]["exit"] == "stop loss -50%"
    assert "9:30" in earlier["strategy"]
    assert acct["tradeLog"]["rows"] == []                                # closed: listed in the earlier period only


def test_the_two_plans_on_the_same_day_are_kept_apart(tmp_path, monkeypatch):
    new = paper_row("NEW", status="closed", held=0, pl=90.0, closedAt=at("2026-11-13", "10:40:00"),
                    sells=[{"qty": 3, "price": 1.55, "why": "T2 hit: sold the last 1", "at": at("2026-11-13", "10:40:00")}])
    held = old_order("HELD", status="open", held=3, pl=None, closedAt=None, sells=[], markMid=1.5)
    waiting = old_order("WAIT", clock="09:58:30", status="buying", buyFill=None, buyFilledQty=None, held=None, pl=None,
                        closedAt=None, sells=[])
    missed = paper_row("MISS", clock="10:07:00", status="missed", buyFill=None, buyFilledQty=None, held=None)
    acct = _desk(tmp_path, monkeypatch, {"n": new, "o": old_order("OLD"), "h": held, "w": waiting, "m": missed,
                                         "s": {"side": "bull", "rule": "level1", "symbol": "SKIP", "status": "skipped",
                                               "at": at("2026-11-13", "10:09:00"), "note": "AI did not pick it"}})
    assert acct["stats"]["n"] == 1 and acct["stats"]["netPnl"] == 90.0 and acct["balance"] == 10090.0
    assert (acct["orders"]["sent"], acct["orders"]["missed"], acct["orders"]["skipped"]) == (2, 1, 1)
    (earlier,) = acct["earlier"]
    assert (earlier["orders"], earlier["trades"], earlier["netPnl"], earlier["stillOpen"], earlier["afterSwitch"]) == (3, 1, -591.0, 1, 3)
    # All trades: the chart-plan rows are scored; what is still live from the older plan is listed, not scored.
    by = {r["symbol"]: r for r in acct["tradeLog"]["rows"]}
    assert set(by) == {"NEW", "MISS", "SKIP", "HELD", "WAIT"}
    assert by["NEW"]["scored"] is True and by["MISS"]["scored"] is True
    assert by["HELD"]["scored"] is False and by["HELD"]["status"] == "open" and by["HELD"]["plan"] is None
    assert by["HELD"]["note"] == "older plan (sent before the bot was restarted), not scored · holding"
    assert by["WAIT"]["scored"] is False and by["WAIT"]["status"] == "waiting"


def test_an_older_plan_order_is_not_listed_again_as_a_signal_not_ordered(tmp_path, monkeypatch):
    held = old_order("HELD", status="open", held=3, pl=None, closedAt=None, sells=[], markMid=1.5)
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-13.json").write_text(
        json.dumps({"trades": [signal("HELD", "09:37:00", rule="level2")]}), encoding="utf-8")
    acct = _desk(tmp_path, monkeypatch, {"h": held})
    assert [(r["symbol"], r["status"]) for r in acct["tradeLog"]["rows"]] == [("HELD", "open")]


def test_which_orders_count_as_the_older_plan_after_the_switch():
    assert bd.old_plan_after_switch(old_order("A")) is True
    assert bd.old_plan_after_switch(old_order("A", day="2026-11-16")) is True
    assert bd.old_plan_after_switch(paper_row("A")) is False                       # the chart plan's own order
    assert bd.old_plan_after_switch(old_order("A", day="2026-11-12")) is False     # before the switch: by its date
    assert bd.old_plan_after_switch({"status": "skipped", "at": at("2026-11-13", "10:00:00")}) is False
