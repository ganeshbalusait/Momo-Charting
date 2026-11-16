"""BOT desk scorecard: the paper accounts judged like a prop-firm evaluation (2026-11-09)."""
import json
import time

from momx import bot_desk as bd

import pytest

TODAY = "2026-11-09"


@pytest.fixture(autouse=True)
def _judge_everything(monkeypatch):
    """These tests use days before the 2026-11-10 fresh start; the fresh start has its own tests below."""
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-01-01")


def at(day, clock="09:30:03"):
    return f"{day}T{clock}-05:00"


def order(status, symbol="AAPL", day=TODAY, rule="rvol", side="bull", **extra):
    return {"side": side, "rule": rule, "symbol": symbol, "at": at(day), "qty": 3, "botEntry": 1.0,
            "buyLimit": 1.0, "status": status, **extra}


def closed(pl, symbol="AAPL", day=TODAY, close_day=None, fill=1.0, **extra):
    return order("closed", symbol=symbol, day=day, buyFill=fill, pl=pl,
                 closedAt=at(close_day or day, "10:15:00"), **extra)


def objective(report, key):
    return next(o for o in report["objectives"] if o["key"] == key)


def test_orders_never_sent_are_not_counted_as_orders():
    rep = bd.account_report([closed(30.0), order("missed", "B"), order("skipped", "C")], TODAY)
    assert (rep["orders"]["sent"], rep["orders"]["filled"], rep["orders"]["missed"], rep["orders"]["skipped"]) == (2, 1, 1, 1)
    assert rep["orders"]["fillRatePct"] == 50.0


def test_trade_stats_are_the_trader_numbers():
    rep = bd.account_report([closed(300.0, "A"), closed(-100.0, "B"), closed(-100.0, "C"), closed(0.0, "D")], TODAY)
    s = rep["stats"]
    assert (s["n"], s["wins"], s["losses"], s["flat"]) == (4, 1, 2, 1)
    assert s["winRatePct"] == 25.0 and s["netPnl"] == 100.0
    assert (s["avgWin"], s["avgLoss"], s["profitFactor"], s["expectancy"]) == (300.0, -100.0, 1.5, 25.0)
    assert s["roiPct"] == 8.3          # $100 on 4 x 3 contracts x $1.00
    assert (s["bestTrade"], s["worstTrade"]) == (300.0, -100.0)


def test_cost_uses_the_contracts_that_really_filled():
    rep = bd.account_report([closed(50.0, buyFilledQty=1)], TODAY)
    assert rep["stats"]["cost"] == 100.0 and rep["stats"]["roiPct"] == 50.0


def test_no_losing_trade_means_no_profit_factor_and_that_check_is_not_passed():
    rep = bd.account_report([closed(40.0)], TODAY)
    assert rep["stats"]["profitFactor"] is None
    assert objective(rep, "profitFactor")["pass"] is False


def test_profit_lands_on_the_day_the_trade_closed():
    rows = [closed(-200.0, "A", day="2026-11-05", close_day="2026-11-06"), closed(50.0, "B", day="2026-11-06")]
    rep = bd.account_report(rows, TODAY)
    days = {d["day"]: d for d in rep["days"]}
    assert days["2026-11-05"]["sent"] == 1 and days["2026-11-05"]["pnl"] == 0
    assert days["2026-11-06"]["pnl"] == -150.0 and days["2026-11-06"]["cum"] == -150.0
    assert rep["daySummary"]["tradingDays"] == 1


def test_drawdown_is_the_fall_from_the_highest_point():
    rows = [closed(400.0, "A", day="2026-11-03"), closed(-700.0, "B", day="2026-11-04"),
            closed(200.0, "C", day="2026-11-05")]
    rep = bd.account_report(rows, TODAY)
    assert [d["cum"] for d in rep["days"]] == [400.0, -300.0, -100.0]
    assert rep["daySummary"]["maxDrawdown"] == 700.0
    assert (rep["daySummary"]["greenDays"], rep["daySummary"]["redDays"]) == (2, 1)
    assert rep["daySummary"]["worstDay"]["day"] == "2026-11-04"


def test_one_day_over_the_daily_loss_limit_fails_that_check():
    ok = bd.account_report([closed(-500.0, "A", day="2026-11-04")], TODAY)
    assert objective(ok, "dailyLoss")["pass"] is True and objective(ok, "dailyLoss")["breaks"] == 0
    bad = bd.account_report([closed(-300.0, "A", day="2026-11-04"), closed(-201.0, "B", day="2026-11-04")], TODAY)
    daily = objective(bad, "dailyLoss")
    assert (daily["pass"], daily["breaks"], daily["actual"]) == (False, 1, -501.0)


def test_profit_from_three_lucky_trades_does_not_pass_the_luck_check():
    rows = [closed(900.0, "A"), closed(800.0, "B"), closed(700.0, "C"), closed(-50.0, "D"), closed(-60.0, "E")]
    rep = bd.account_report(rows, TODAY)
    assert rep["stats"]["netPnl"] == 2290.0 and rep["stats"]["pnlWithoutBest"] == -110.0
    assert objective(rep, "luck")["pass"] is False


def test_a_record_that_meets_every_rule_is_ready():
    days = [f"2026-10-{d:02d}" for d in range(1, 21)]
    rows = [closed(60.0 if i % 5 else -80.0, f"S{d}{i}", day=day) for d, day in enumerate(days) for i in range(5)]
    rep = bd.account_report(rows, TODAY)
    assert rep["stats"]["n"] == 100 and rep["stats"]["netPnl"] == 3200.0
    assert {o["key"] for o in rep["objectives"] if not o["pass"]} == set()
    assert rep["verdict"] == {"ready": True, "passed": 8, "total": 8}


def test_an_empty_account_passes_nothing():
    rep = bd.account_report([order("missed")], TODAY)
    assert rep["verdict"]["ready"] is False and rep["verdict"]["passed"] == 0
    assert objective(rep, "profit")["actual"] is None and objective(rep, "drawdown")["actual"] is None


def test_stuck_buys_and_overnight_positions_are_called_out():
    rows = [order("buying", "CCJ", day="2026-11-04"), order("buying", "NEW"),
            order("selling", "ZETA", day="2026-11-06", buyFill=0.9), order("open", "TSLA", buyFill=2.0)]
    o = bd.account_report(rows, TODAY)["orders"]
    assert (o["stuck"], o["stuckSymbols"]) == (1, ["CCJ"])
    assert (o["holding"], o["holdingOvernight"]) == (2, 1)


def test_rules_are_ranked_by_real_dollars_and_small_samples_are_marked():
    rows = [closed(-90.0, "A", rule="rvol"), order("missed", "B", rule="rvol"), closed(40.0, "C", rule="skit", side="bear")]
    rules = bd.account_report(rows, TODAY)["byRule"]
    assert [(r["side"], r["rule"], r["pnl"]) for r in rules] == [("bear", "skit", 40.0), ("bull", "rvol", -90.0)]
    assert rules[1]["sent"] == 2 and rules[1]["fillRatePct"] == 50.0 and rules[1]["smallSample"] is True


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def test_build_reads_both_accounts_keeps_the_newest_copy_and_the_window(tmp_path):
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    key = "bull|rvol|AAPL|" + at("2026-11-06")
    write(folder / "2026-11-06.json", {"rows": {key: order("selling", day="2026-11-06", buyFill=1.0)}})
    write(folder / "2026-11-09.json", {"rows": {key: closed(-45.0, day="2026-11-06", close_day="2026-11-09"),
                                                 "bull|rvol|OLD|" + at("2026-01-02"): closed(999.0, "OLD", day="2026-01-02")}})
    write(tmp_path / "momx_paper_mirror" / "paper5" / "2026-11-09.json", {"rows": {}})
    doc = bd.build(tmp_path, days=30, today=TODAY)
    assert doc["available"] is True and list(doc["accounts"]) == ["paper3"]
    p3 = doc["accounts"]["paper3"]
    assert p3["stats"]["n"] == 1 and p3["stats"]["netPnl"] == -45.0
    assert (p3["from"], p3["through"]) == ("2026-11-06", "2026-11-06")
    assert doc["limits"]["dailyLossLimit"] == 500 and doc["paused"] is False


def test_build_reports_the_off_switch_and_no_data(tmp_path):
    (tmp_path / "paper_mirror_off").write_text("", encoding="utf-8")
    doc = bd.build(tmp_path, today=TODAY)
    assert doc["available"] is False and doc["accounts"] == {} and doc["paused"] is True


# ---- safety controls on the Scorecard (2026-11-09): stuck rows, pause, daily loss stop
def test_a_buy_from_today_is_stuck_only_once_it_is_older_than_ten_minutes():
    from datetime import datetime
    now = datetime.fromisoformat(at(TODAY, "10:00:00")).timestamp()
    rows = [order("buying", "OLD", buyAt=at(TODAY, "09:45:00")), order("buying", "NEW", buyAt=at(TODAY, "09:55:00"))]
    assert bd.account_report(rows, TODAY, now)["orders"]["stuckSymbols"] == ["OLD"]
    assert bd.account_report(rows, TODAY)["orders"]["stuck"] == 0            # no clock: only earlier days count


def test_build_names_each_stuck_order_so_it_can_be_removed(tmp_path):
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    key = "bear|call2h|CCJ|" + at("2026-11-04")
    write(folder / "2026-11-09.json", {"rows": {
        key: order("buying", "CCJ", day="2026-11-04", side="bear", rule="call2h", contract="CCJ   261106P00072000",
                   buyAt=at("2026-11-04", "09:33:19"), carried=True, note="Awaiting broker reconciliation: order not found"),
        "bull|rvol|AAPL|" + at(TODAY): closed(-45.0)}})
    stuck = bd.build(tmp_path, days=30, today=TODAY)["accounts"]["paper3"]["orders"]["stuckRows"]
    assert stuck == [{"key": key, "symbol": "CCJ", "side": "bear", "rule": "call2h", "contract": "CCJ   261106P00072000",
                      "buyAt": at("2026-11-04", "09:33:19"), "note": "Awaiting broker reconciliation: order not found"}]


def test_build_reports_pause_and_the_daily_loss_stop_per_account(tmp_path, monkeypatch):
    from momx import paper_control
    monkeypatch.setattr(paper_control, "DAILY_STOP_ON", True)       # as it was built; it is off in the app since 2026-11-10
    root = tmp_path / "momx_paper_mirror"
    write(root / "paper3" / f"{TODAY}.json", {"rows": {"a": closed(-300.0, "A"), "b": closed(-260.0, "B")},
                                               "dailyStop": {"at": at(TODAY, "10:42:00"), "pnl": -560.0, "limit": 500.0}})
    write(root / "paper5" / f"{TODAY}.json", {"rows": {"c": closed(120.0, "C")}})
    paper_control.set_paused(root, "paper5", True, "owner@example.com", at(TODAY, "11:00:00"))
    accounts = bd.build(tmp_path, days=30, today=TODAY)["accounts"]
    assert accounts["paper3"]["control"] == {"paused": False, "pausedAt": None, "dailyStop": {
        "on": True, "limit": 500, "hit": True, "hitAt": at(TODAY, "10:42:00"), "todayPnl": -560.0}}
    assert accounts["paper5"]["control"] == {"paused": True, "pausedAt": at(TODAY, "11:00:00"), "dailyStop": {
        "on": True, "limit": 500, "hit": False, "hitAt": None, "todayPnl": 120.0}}


def test_before_todays_file_exists_an_older_position_is_not_counted_as_todays_loss(tmp_path):
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    write(folder / "2026-11-06.json", {"rows": {"k": order("open", "ZETA", day="2026-11-06", buyFill=2.0, held=3, markMid=0.1,
                                                         buyAt=at("2026-11-06", "10:00:00"), sells=[])}})
    control = bd.build(tmp_path, days=30, today=TODAY)["accounts"]["paper3"]["control"]
    assert control["dailyStop"] == {"on": False, "limit": 500, "hit": False, "hitAt": None, "todayPnl": 0.0}


# ---- fresh evaluation (2026-11-09: only orders from the start day are judged)
def test_orders_before_the_fresh_start_are_not_judged_but_still_shown_when_they_need_a_hand(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    stuck_key = "bear|call2h|CCJ|" + at("2026-11-04")
    write(folder / "2026-11-10.json", {"rows": {
        "old-loss": closed(-900.0, "OLD", day="2026-11-06", close_day="2026-11-10"),          # old rules: not judged
        stuck_key: order("buying", "CCJ", day="2026-11-04", side="bear", rule="call2h", buyAt=at("2026-11-04", "09:33:19")),
        "old-open": order("selling", "MRK", day="2026-11-09", buyFill=1.0, held=3),
        "new-win": closed(120.0, "AAPL", day="2026-11-10", rule="level1"),
        "new-miss": order("missed", "MSFT", day="2026-11-10", rule="level2")}})
    doc = bd.build(tmp_path, days=30, today="2026-11-10")
    assert doc["startDay"] == "2026-11-10" and doc["strategy"].startswith("Level 1 / 2 / 3")
    p3 = doc["accounts"]["paper3"]
    assert (p3["orders"]["sent"], p3["orders"]["filled"], p3["orders"]["missed"]) == (2, 1, 1)
    assert p3["stats"]["netPnl"] == 120.0 and p3["balance"] == 10120.0
    assert [r["rule"] for r in p3["byRule"]] == ["level1", "level2"]
    assert (p3["from"], p3["through"]) == ("2026-11-10", "2026-11-10")
    assert p3["orders"]["stuck"] == 1 and p3["orders"]["stuckRows"][0]["key"] == stuck_key
    assert p3["orders"]["holdingOvernight"] == 1
    assert p3["control"]["dailyStop"]["todayPnl"] == -780.0        # the stop still counts every trade closed today


def test_an_account_with_only_older_orders_starts_the_evaluation_empty_at_ten_thousand(tmp_path, monkeypatch):
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    write(tmp_path / "momx_paper_mirror" / "paper5" / "2026-11-09.json", {"rows": {"a": closed(458.0, "A", day="2026-11-09")}})
    doc = bd.build(tmp_path, days=30, today="2026-11-10")
    p5 = doc["accounts"]["paper5"]
    assert doc["available"] is True and p5["orders"]["sent"] == 0 and p5["stats"]["n"] == 0
    assert p5["balance"] == 10000.0 and p5["verdict"]["passed"] == 0 and p5["from"] is None
    assert p5["control"]["paused"] is False


def test_one_level_order_counts_under_every_level_it_qualified_for_but_once_in_the_account(tmp_path, monkeypatch):
    """Levels stack (a Level 3 signal is also Level 2 and 1) and one signal is one order."""
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    day = "2026-11-10"
    write(tmp_path / "momx_paper_mirror" / "paper3" / f"{day}.json", {"rows": {
        "a": closed(100.0, "AAPL", day=day, rule="level3", levels=["level1", "level2", "level3"]),
        "b": closed(-40.0, "MSFT", day=day, rule="level2", levels=["level1", "level2"]),
        "c": order("missed", "NVDA", day=day, rule="level3", levels=["level1", "level2", "level3"])}})
    p3 = bd.build(tmp_path, days=30, today=day)["accounts"]["paper3"]
    by = {r["rule"]: r for r in p3["byRule"]}
    assert (by["level1"]["sent"], by["level1"]["closed"], by["level1"]["pnl"]) == (3, 2, 60.0)
    assert (by["level2"]["sent"], by["level2"]["closed"], by["level2"]["pnl"]) == (3, 2, 60.0)
    assert (by["level3"]["sent"], by["level3"]["closed"], by["level3"]["pnl"]) == (2, 1, 100.0)
    assert (p3["orders"]["sent"], p3["stats"]["n"], p3["stats"]["netPnl"], p3["balance"]) == (3, 2, 60.0, 10060.0)
    assert bd.strategies({"rule": "rvol"}) == ["rvol"] and bd.strategies({"rule": "level1", "levels": []}) == ["level1"]
    # A damaged day file must not take the Scorecard down.
    assert bd.strategies({"rule": "level2", "levels": ["level1", {"x": 1}, None, 3]}) == ["level1"]
    assert bd.strategies({"rule": "level2", "levels": [{"x": 1}]}) == ["level2"]
    assert bd._rules([{**closed(5.0), "levels": ["level1", ["bad"]]}])[0]["rule"] == "level1"


# ---- the three strategies side by side (2026-11-10: "How I know which one is good")
def lvl(pl, symbol, top, side="bull", day="2026-11-10"):
    names = ["level1", "level2", "level3"][:top]
    return {**closed(pl, symbol, day=day, rule=names[-1], side=side), "levels": names, "_key": f"{side}|{symbol}"}


def test_the_three_strategies_are_always_listed_even_before_any_trade():
    s = bd.account_report([], TODAY)["strategies"]
    assert [r["rule"] for r in s["rows"]] == ["level1", "level2", "level3"]
    assert all(r["closed"] == 0 and r["pnl"] == 0 and r["smallSample"] and r["sameAs"] is None for r in s["rows"])
    assert (s["best"], s["allLosing"], s["need"], s["mostClosed"]["closed"]) == (None, False, 20, 0)


def test_each_strategy_shows_its_own_result_with_calls_and_puts_apart():
    rows = [lvl(100.0, "A", 3), lvl(-40.0, "B", 2), lvl(-30.0, "C", 1, side="bear"), lvl(60.0, "D", 3, side="bear")]
    rows.append({**order("missed", "E", day="2026-11-10", rule="level3"), "levels": ["level1", "level2", "level3"], "_key": "bull|E"})
    by = {r["rule"]: r for r in bd.account_report(rows, "2026-11-10")["strategies"]["rows"]}
    assert (by["level1"]["sent"], by["level1"]["closed"], by["level1"]["pnl"], by["level1"]["winRatePct"]) == (5, 4, 90.0, 50.0)
    assert (by["level2"]["closed"], by["level2"]["pnl"]) == (3, 120.0)
    assert (by["level3"]["closed"], by["level3"]["pnl"], by["level3"]["fillRatePct"]) == (2, 160.0, 66.7)
    assert by["level1"]["calls"] == {"closed": 2, "wins": 1, "winRatePct": 50.0, "pnl": 60.0, "roiPct": 10.0}
    assert (by["level1"]["puts"]["closed"], by["level1"]["puts"]["pnl"]) == (2, 30.0)
    assert by["level1"]["perTrade"] == 22.5


def test_two_levels_that_took_the_same_orders_are_flagged_as_the_same():
    rows = [lvl(10.0, f"S{i}", 2) for i in range(3)]                       # every signal reached Level 2, none Level 3
    s = bd.account_report(rows, "2026-11-10")["strategies"]
    by = {r["rule"]: r for r in s["rows"]}
    assert by["level2"]["sameAs"] == "level1" and by["level1"]["sameAs"] is None and by["level3"]["sameAs"] is None
    assert by["level3"]["closed"] == 0


def test_a_best_strategy_is_named_only_with_twenty_closed_trades_and_a_profit():
    few = [lvl(50.0, f"F{i}", 3) for i in range(19)]
    assert bd.account_report(few, "2026-11-10")["strategies"]["best"] is None
    # Level 3: 20 trades, +$1,000. Level 1 also has 20 small losers on top: lower return on the money used.
    rows = [lvl(50.0, f"T{i}", 3) for i in range(20)] + [lvl(-5.0, f"L{i}", 1) for i in range(20)]
    s = bd.account_report(rows, "2026-11-10")["strategies"]
    by = {r["rule"]: r for r in s["rows"]}
    assert s["best"] == "level2" and by["level3"]["sameAs"] == "level2"    # Level 2 and 3 took the same 20: one strategy
    assert by["level1"]["roiPct"] < by["level2"]["roiPct"] and not s["allLosing"]
    losing = [lvl(-10.0, f"X{i}", 3) for i in range(20)]
    s = bd.account_report(losing, "2026-11-10")["strategies"]
    assert s["best"] is None and s["allLosing"] is True


# ---- rules bot against AI bot (2026-11-10: "see which strategy works good then we will use same for real money")
def two_accounts(tmp_path, monkeypatch, p3, p5, day="2026-11-10"):
    monkeypatch.setattr(bd, "EVALUATION_START", day)
    for account, rows in (("paper3", p3), ("paper5", p5)):
        write(tmp_path / "momx_paper_mirror" / account / f"{day}.json", {"rows": {f"k{i}": r for i, r in enumerate(rows)}})
    return bd.build(tmp_path, days=30, today=day)


def test_the_two_accounts_are_named_by_how_they_choose_and_compared(tmp_path, monkeypatch):
    doc = two_accounts(tmp_path, monkeypatch, [closed(50.0, "A", day="2026-11-10")], [closed(-20.0, "B", day="2026-11-10")])
    assert doc["accounts"]["paper3"]["mode"]["name"] == "Rules bot" and doc["accounts"]["paper5"]["mode"]["key"] == "ai"
    v = doc["versus"]
    assert [(r["account"], r["name"], r["closed"], r["pnl"], r["balance"]) for r in v["rows"]] == [
        ("paper3", "Rules bot", 1, 50.0, 10050.0), ("paper5", "AI bot", 1, -20.0, 9980.0)]
    assert (v["ready"], v["leader"], v["need"]) == (False, None, 20)          # one trade each proves nothing


def picked(row, rank=1):
    return {**row, "aiPick": {"rank": rank, "of": 5, "why": "strong"}}


def test_a_leader_is_named_only_when_both_have_twenty_trades_the_ai_really_chose_and_the_gap_is_real(tmp_path, monkeypatch):
    rules = [closed(10.0, f"R{i}", day="2026-11-10") for i in range(20)]
    ai = [picked(closed(30.0, f"A{i}", day="2026-11-10")) for i in range(20)]
    v = two_accounts(tmp_path, monkeypatch, rules, ai)["versus"]
    assert (v["ready"], v["leader"], v["allLosing"], v["aiProven"], v["aiChosenClosed"]) == (True, "paper5", False, True, 20)
    v = two_accounts(tmp_path / "b", monkeypatch, rules, ai[:19])["versus"]
    assert (v["enough"], v["ready"], v["leader"]) == (False, False, None)
    losing = [closed(-10.0, f"L{i}", day="2026-11-10") for i in range(20)]
    v = two_accounts(tmp_path / "c", monkeypatch, losing, [picked(dict(r, pl=-30.0)) for r in losing])["versus"]
    assert (v["leader"], v["allLosing"], v["tooClose"]) == (None, True, False)
    v = two_accounts(tmp_path / "d", monkeypatch, rules, [picked(dict(r)) for r in rules])["versus"]
    assert v["leader"] is None and v["tooClose"] is True                      # a tie names nobody


def test_the_ai_bot_cannot_be_called_ahead_when_the_ai_did_not_choose_its_trades(tmp_path, monkeypatch):
    """Picker off, no AI answers, or orders from before it started: both accounts were on the rules order."""
    rules = [closed(10.0, f"R{i}", day="2026-11-10") for i in range(20)]
    unpicked = [{**closed(30.0, f"A{i}", day="2026-11-10"), "aiPick": None} for i in range(20)]
    v = two_accounts(tmp_path, monkeypatch, rules, unpicked)["versus"]
    assert (v["enough"], v["aiProven"], v["ready"], v["leader"], v["aiChosenClosed"], v["aiClosed"]) == (True, False, False, None, 0, 20)
    no_key = [closed(30.0, f"A{i}", day="2026-11-10") for i in range(20)]     # rows from before the AI bot existed
    assert two_accounts(tmp_path / "b", monkeypatch, rules, no_key)["versus"]["leader"] is None
    half = [picked(closed(30.0, f"P{i}", day="2026-11-10")) for i in range(20)] + unpicked + [{**closed(1.0, "X", day="2026-11-10"), "aiPick": None}]
    assert two_accounts(tmp_path / "c", monkeypatch, rules, half)["versus"]["aiProven"] is False       # 20 of 41: under half


def test_a_lead_smaller_than_one_trade_is_too_close_to_call(tmp_path, monkeypatch):
    rules = [closed(10.0, f"R{i}", day="2026-11-10") for i in range(20)]                               # +$200, biggest trade $10
    ai = [picked(closed(10.0, f"A{i}", day="2026-11-10")) for i in range(19)] + [picked(closed(15.0, "A19", day="2026-11-10"))]   # +$205
    v = two_accounts(tmp_path, monkeypatch, rules, ai)["versus"]
    assert (v["ready"], v["leader"], v["tooClose"]) == (True, None, True)                              # $5 ahead, one trade moves $15
    ai2 = [picked(closed(12.0, f"A{i}", day="2026-11-10")) for i in range(20)]                         # +$240: $40 ahead, biggest trade $12
    assert two_accounts(tmp_path / "b", monkeypatch, rules, ai2)["versus"]["leader"] == "paper5"


def test_the_ai_bot_shows_how_many_orders_the_ai_really_chose(tmp_path, monkeypatch):
    ai = [{**closed(40.0, "A", day="2026-11-10"), "aiPick": {"rank": 1, "of": 5, "why": "strong move"}},
          {**closed(-10.0, "B", day="2026-11-10"), "aiPick": {"rank": 2, "of": 5, "why": "ok"}},
          {**closed(-25.0, "C", day="2026-11-10"), "aiPick": None},            # no ranking in time: rules order used
          {**order("missed", "D", day="2026-11-10"), "aiPick": None},
          closed(5.0, "OLD", day="2026-11-10")]                                # an order from before the AI bot: not counted either way
    doc = two_accounts(tmp_path, monkeypatch, [closed(1.0, "Z", day="2026-11-10")], ai)
    split = doc["accounts"]["paper5"]["aiPicks"]
    assert (split["picked"]["sent"], split["picked"]["closed"], split["picked"]["pnl"]) == (2, 2, 30.0)
    assert (split["fallback"]["sent"], split["fallback"]["closed"], split["fallback"]["pnl"]) == (2, 1, -25.0)
    assert "aiPicks" not in doc["accounts"]["paper3"]


# ---- the Scorecard as a journal (2026-11-10: "how I see the d/w/m pnl with which strategy is good for real money")
def jr(pl, symbol, close_day, top=1, side="bull", **extra):
    names = ["level1", "level2", "level3"][:top]
    return {**closed(pl, symbol, day=close_day, rule=names[-1], side=side), "levels": names, **extra}


def test_the_journal_groups_real_profit_by_day_week_and_month():
    rows = [jr(100.0, "A", "2026-11-10", top=3), jr(-40.0, "B", "2026-11-10", top=1), jr(60.0, "C", "2026-11-12", top=2, side="bear"),
            jr(-25.0, "D", "2026-11-17", top=1), jr(10.0, "E", "2026-12-01", top=3), order("missed", "F", day="2026-11-10")]
    j = bd.account_report(rows, "2026-12-01")["journal"]
    days = j["periods"]["day"]
    assert [(d["key"], d["trades"], d["pnl"]) for d in days] == [
        ("2026-12-01", 1, 10.0), ("2026-11-17", 1, -25.0), ("2026-11-12", 1, 60.0), ("2026-11-10", 2, 60.0)]      # newest first
    assert [(w["key"], w["trades"], w["pnl"]) for w in j["periods"]["week"]] == [
        ("2026-11-30", 1, 10.0), ("2026-11-16", 1, -25.0), ("2026-11-09", 3, 120.0)]                                # keyed by the Monday
    assert [(m["key"], m["trades"], m["pnl"], m["winRatePct"]) for m in j["periods"]["month"]] == [
        ("2026-12", 1, 10.0, 100.0), ("2026-11", 4, 95.0, 50.0)]
    assert j["tradeCount"] == 5


def test_each_period_splits_its_profit_by_strategy_and_names_the_best_one_only_when_it_made_money():
    rows = [jr(100.0, "A", "2026-11-10", top=3), jr(-40.0, "B", "2026-11-10", top=1), jr(60.0, "C", "2026-11-10", top=2, side="bear")]
    day = bd.account_report(rows, "2026-11-10")["journal"]["periods"]["day"][0]
    assert day["strategies"] == [{"rule": "level1", "trades": 3, "pnl": 120.0}, {"rule": "level2", "trades": 2, "pnl": 160.0},
                                 {"rule": "level3", "trades": 1, "pnl": 100.0}]
    assert (day["pnl"], day["best"], day["calls"], day["puts"]) == (120.0, "level2", 60.0, 60.0)      # the day's total counts each trade once
    red = bd.account_report([jr(-10.0, "A", "2026-11-10", top=2)], "2026-11-10")["journal"]["periods"]["day"][0]
    assert red["best"] is None and red["strategies"][2] == {"rule": "level3", "trades": 0, "pnl": 0}


def test_the_journal_lists_each_closed_trade_newest_first_with_how_it_ended():
    rows = [jr(100.0, "AAPL", "2026-11-10", top=3, contract="AAPL261113C00285000", buyFillAt=at("2026-11-10", "09:41:00"),
               sells=[{"qty": 2, "price": 2.0, "why": "+100%: sell 2 of 3"}, {"qty": 1, "price": 1.0, "why": "stop at break-even"}],
               aiPick={"rank": 1, "of": 4, "why": "strong"}),
            jr(-40.0, "MSFT", "2026-11-09", top=1)]
    trades = bd.account_report(rows, "2026-11-10")["journal"]["trades"]
    assert [t["symbol"] for t in trades] == ["AAPL", "MSFT"]
    first = trades[0]
    assert (first["rule"], first["levels"], first["pnl"], first["roiPct"], first["exit"], first["aiPicked"]) == (
        "level3", ["level1", "level2", "level3"], 100.0, 33.3, "stop at break-even", True)
    assert first["boughtAt"] == at("2026-11-10", "09:41:00") and first["day"] == "2026-11-10" and trades[1]["aiPicked"] is False
    assert bd.account_report([], "2026-11-10")["journal"] == {"periods": {"day": [], "week": [], "month": []}, "trades": [], "tradeCount": 0}


def test_the_journal_keeps_only_the_newest_trades_but_counts_them_all(monkeypatch):
    monkeypatch.setattr(bd, "JOURNAL_TRADES", 3)
    rows = [jr(1.0, f"T{i}", "2026-11-10") for i in range(5)]
    j = bd.account_report(rows, "2026-11-10")["journal"]
    assert len(j["trades"]) == 3 and j["tradeCount"] == 5 and j["periods"]["day"][0]["trades"] == 5


# ---- "All trades" (2026-11-10: "Scoreboard I don't see all trade")
def test_all_trades_lists_every_signal_newest_first_with_what_became_of_it():
    rows = [
        closed(120.0, "AAPL", "2026-11-10", rule="level3", levels=["level1", "level2", "level3"], qty=3, fill=1.0,
               sells=[{"qty": 2, "price": 2.0, "why": "+100%: sell 2 of 3"}, {"qty": 1, "price": 0.2, "why": "stop loss -50%"}]),
        order("missed", "MSFT", "2026-11-10", rule="level1"),
        order("skipped", "IOT", "2026-11-10", rule="level2", note="Spread 71% of the mid is over 15%"),
        order("buying", "NVDA", "2026-11-10", rule="level2", side="bear"),
        order("open", "TSLA", "2026-11-10", rule="level2", buyFill=1.0, markMid=1.5, buyFillAt=at("2026-11-10", "09:41:00"),
              sells=[{"qty": 1, "price": 2.0, "why": "+100%: sell 1 of 3"}], aiPick={"rank": 1}),
    ]
    for i, r in enumerate(rows):
        r["at"] = at("2026-11-10", f"09:3{i}:00")
    log = bd.account_report(rows, "2026-11-10")["tradeLog"]
    assert log["counts"] == {"open": 1, "closed": 1, "missed": 1, "waiting": 1, "skipped": 1, "signal": 0} and log["total"] == 5
    assert [(t["symbol"], t["status"]) for t in log["rows"]] == [
        ("TSLA", "open"), ("NVDA", "waiting"), ("IOT", "skipped"), ("MSFT", "missed"), ("AAPL", "closed")]
    by = {t["symbol"]: t for t in log["rows"]}
    assert (by["AAPL"]["pnl"], by["AAPL"]["roiPct"], by["AAPL"]["live"], by["AAPL"]["note"], by["AAPL"]["levels"]) == (
        120.0, 40.0, False, "stop loss -50%", ["level1", "level2", "level3"])
    # Open: +$100 banked on the one sold, the two still held marked at 1.50 against 1.00 = +$100 more. An estimate.
    assert (by["TSLA"]["pnl"], by["TSLA"]["roiPct"], by["TSLA"]["live"], by["TSLA"]["aiPicked"]) == (200.0, 66.7, True, True)
    assert by["TSLA"]["note"] == "part sold: +100%: sell 1 of 3" and by["TSLA"]["boughtAt"] == at("2026-11-10", "09:41:00")
    assert (by["MSFT"]["pnl"], by["MSFT"]["buy"], by["MSFT"]["note"]) == (None, None, "the order did not fill")
    assert (by["NVDA"]["side"], by["NVDA"]["pnl"], by["NVDA"]["note"]) == ("bear", None, "order sent, waiting for a fill")
    assert (by["IOT"]["pnl"], by["IOT"]["note"]) == (None, "Spread 71% of the mid is over 15%")


def test_all_trades_never_invents_a_result_and_is_capped(monkeypatch):
    # Open with no price yet: no P/L shown. A "closed" row with no fill is not a closed trade.
    log = bd.account_report([order("open", "A", buyFill=1.0), order("closed", "B", pl=50.0)], TODAY)["tradeLog"]
    by = {t["symbol"]: t for t in log["rows"]}
    assert (by["A"]["pnl"], by["A"]["live"], by["A"]["note"]) == (None, False, "holding")
    # A closed order whose result could not be priced is closed, never "open" with a running estimate.
    assert (by["B"]["status"], by["B"]["pnl"]) == ("closed", None)
    gone = bd.account_report([order("closed", "C", buyFill=1.0, buyFilledQty=3, pl=None, markMid=1.5, sells=[{"qty": 2, "price": 2.0, "why": "x"}])], TODAY)["tradeLog"]
    assert (gone["counts"]["open"], gone["rows"][0]["status"], gone["rows"][0]["pnl"], gone["rows"][0]["live"], gone["rows"][0]["note"]) == (
        0, "closed", None, False, "closed, result not priced")
    assert bd.account_report([], TODAY)["tradeLog"] == {"rows": [], "counts": {"open": 0, "closed": 0, "missed": 0, "waiting": 0, "skipped": 0, "signal": 0}, "total": 0}
    monkeypatch.setattr(bd, "TRADE_LOG_ROWS", 2)
    many = [order("missed", f"S{i}") for i in range(5)]
    capped = bd.account_report(many, TODAY)["tradeLog"]
    assert len(capped["rows"]) == 2 and capped["counts"]["missed"] == 5 and capped["total"] == 5
    # Only not-filled and skipped rows are cut: a trade that filled is never pushed out of the list.
    busy = [order("missed", f"S{i}") for i in range(5)] + [closed(10.0, "WIN", day="2026-11-02"), order("open", "HOLD", day="2026-11-02", buyFill=1.0)]
    kept = bd.account_report(busy, TODAY)["tradeLog"]
    assert [t["symbol"] for t in kept["rows"]] == ["S0", "S1", "WIN", "HOLD"] or sorted(t["status"] for t in kept["rows"]) == ["closed", "missed", "missed", "open"]
    assert kept["counts"] == {"open": 1, "closed": 1, "missed": 5, "waiting": 0, "skipped": 0, "signal": 0}
    # The open estimate is not part of the real totals.
    rep = bd.account_report([order("open", "A", buyFill=1.0, markMid=3.0)], TODAY)
    assert rep["stats"]["n"] == 0 and rep["stats"]["netPnl"] == 0 and rep["tradeLog"]["rows"][0]["pnl"] == 600.0


def test_all_trades_open_estimate_uses_what_is_really_held_and_says_when_it_was_priced():
    # The bot says 1 is still held (2 sold): the estimate is on 1 contract, with the time of that price.
    held = order("open", "A", buyFill=1.0, buyFilledQty=3, held=1, markMid=2.0, markAt=at(TODAY, "15:59:00"),
                 sells=[{"qty": 2, "price": 2.0, "why": "+100%: sell 2 of 3"}])
    t = bd.account_report([held], TODAY)["tradeLog"]["rows"][0]
    assert (t["pnl"], t["live"], t["markAt"]) == (300.0, True, at(TODAY, "15:59:00"))
    # A part-sale with no price: the result so far is unknown, so none is shown.
    unknown = order("open", "B", buyFill=1.0, buyFilledQty=3, held=1, markMid=2.0, sells=[{"qty": 2, "price": None, "why": "partial"}])
    u = bd.account_report([unknown], TODAY)["tradeLog"]["rows"][0]
    assert (u["pnl"], u["live"], u["markAt"], u["status"]) == (None, False, None, "open")


def test_all_trades_also_lists_what_is_still_live_from_before_the_fresh_start(tmp_path, monkeypatch):
    """A position bought before the fresh start and not sold yet is real: listed, flagged, never scored."""
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    folder = tmp_path / "momx_paper_mirror" / "paper5"
    folder.mkdir(parents=True)
    old_open = order("selling", "MVIS", "2026-11-09", buyFill=0.75, buyFilledQty=7, held=7, markMid=0.03, markAt=at("2026-11-09", "16:00:48"))
    old_closed = closed(-90.0, "OLD", "2026-11-09")
    old_missed = order("missed", "QQQ", "2026-11-09")
    new_closed = closed(40.0, "AAPL", "2026-11-10", rule="level1")
    (folder / "2026-11-10.json").write_text(json.dumps({"rows": {
        "k|MVIS": old_open, "k|OLD": old_closed, "k|QQQ": old_missed, "k|AAPL": new_closed}}), encoding="utf-8")
    acct = bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper5"]
    log = acct["tradeLog"]
    assert [(t["symbol"], t["status"], t["scored"]) for t in log["rows"]] == [("AAPL", "closed", True), ("MVIS", "open", False)]
    mvis = log["rows"][1]
    assert mvis["note"] == "from before the fresh start, not scored · holding" and mvis["pnl"] == -504.0 and mvis["markAt"] == at("2026-11-09", "16:00:48")
    assert log["counts"] == {"open": 1, "closed": 1, "missed": 0, "waiting": 0, "skipped": 0, "signal": 0} and log["total"] == 2
    assert (acct["stats"]["n"], acct["stats"]["netPnl"]) == (1, 40.0)          # the old position is not in the real totals


def test_the_scorecard_says_the_daily_stop_is_off_and_never_holding(tmp_path, monkeypatch):
    """2026-11-10: the stop is switched off; one that tripped this morning must not read as still holding buys."""
    from momx import paper_control
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"k|TTWO": closed(-574.0, "TTWO", "2026-11-10", rule="level3")},
        "dailyStop": {"at": at("2026-11-10", "09:36:41"), "pnl": -574.0, "limit": 500.0}}), encoding="utf-8")
    assert paper_control.DAILY_STOP_ON is False
    stop = bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["control"]["dailyStop"]
    assert (stop["on"], stop["hit"], stop["hitAt"], stop["todayPnl"]) == (False, False, at("2026-11-10", "09:36:41"), -574.0)
    monkeypatch.setattr(paper_control, "DAILY_STOP_ON", True)
    assert bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["control"]["dailyStop"]["hit"] is True


def test_all_trades_says_when_the_broker_refused_a_buy():
    refused = order("missed", "MU", note="The broker refused this buy: not enough buying power in the paper account")
    plain = order("missed", "HPE", note="buy canceled (not filled at the mid)")
    by = {t["symbol"]: t for t in bd.account_report([refused, plain], TODAY)["tradeLog"]["rows"]}
    assert by["MU"]["note"] == "The broker refused this buy: not enough buying power in the paper account"
    assert by["HPE"]["note"] == "the order did not fill"


def test_the_scorecard_carries_the_alpaca_account_from_the_newest_day_file(tmp_path):
    """2026-11-10, "scoreboard should show alpaca buying power"."""
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    broker = {"at": at(TODAY, "12:29:00"), "cash": 517.93, "optionsBuyingPower": 517.93, "buyingPower": 2071.72, "equity": 967.93, "lastEquity": 2809.79}
    (folder / "2026-11-06.json").write_text(json.dumps({"rows": {"k": closed(10.0, "A", "2026-11-06")}, "broker": {**broker, "at": at("2026-11-06"), "cash": 1.0}}), encoding="utf-8")
    (folder / f"{TODAY}.json").write_text(json.dumps({"rows": {"k2": closed(10.0, "B")}, "broker": broker}), encoding="utf-8")
    acct = bd.build(tmp_path, 30, today=TODAY)["accounts"]["paper3"]
    assert acct["broker"] == {**broker, "error": None}
    # Before today's file has a read, the newest earlier read is shown; never a later day's.
    (folder / f"{TODAY}.json").write_text(json.dumps({"rows": {"k2": closed(10.0, "B")}}), encoding="utf-8")
    assert bd.build(tmp_path, 30, today=TODAY)["accounts"]["paper3"]["broker"]["cash"] == 1.0
    assert bd.build(tmp_path, 30, today="2026-11-05")["accounts"].get("paper3", {}).get("broker") is None


# ---- All trades also lists the Bot's signals from Research (2026-11-10: "i dont see all trade from research")
def _sig(symbol, clock, rule="level3", **extra):
    t = {"rule": rule, "symbol": symbol, "board": "Watchlist", "at": at("2026-11-10", clock), "status": "open",
         "eContract": f"{symbol}261113C00010000", "eEntry": 2.0, "eEntryBid": 1.95, "eEntryAsk": 2.05, "eLast": 3.0, "eBest": 4.0}
    t.update(extra)
    return t


def test_all_trades_lists_each_bot_signal_this_bot_did_not_order_and_why(tmp_path, monkeypatch):
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    bull = [_sig("SPY", "09:31:00", rule="level1"), _sig("SPY", "09:31:00", rule="level2"),     # the same signal at three levels: once
            _sig("AVGO", "09:44:00"), _sig("NVDA", "09:50:00"), _sig("NVDA", "10:05:00"), _sig("PENNY", "10:10:00", eEntry=0.03, eEntryBid=0.0),
            _sig("LATE", "11:30:00"), _sig("SPY", "09:31:00"), _sig("OLD", "09:45:00", rule="call2h")]
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": bull}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    nvda = order("open", "NVDA", "2026-11-10", rule="level3", buyFill=2.0, buyId="b1")
    nvda["at"] = at("2026-11-10", "09:50:00")
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"bull|level3|NVDA|" + nvda["at"]: nvda},
        "dailyStop": {"at": at("2026-11-10", "09:36:41"), "pnl": -574.0, "limit": 500.0, "liftedAt": at("2026-11-10", "09:59:33")}}), encoding="utf-8")
    log = bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]
    by = {(t["symbol"], t["at"][11:16]): t for t in log["rows"]}
    assert log["counts"]["signal"] == 5 and log["counts"]["open"] == 1 and log["total"] == 6       # OLD (not a Level rule) is left out
    assert ("NVDA", "09:50") in by and by[("NVDA", "09:50")]["status"] == "open"                    # the ordered one is the order, once
    avgo = by[("AVGO", "09:44")]
    assert (avgo["status"], avgo["pnl"], avgo["qty"], avgo["roiPct"], avgo["bestPct"], avgo["board"]) == ("signal", None, None, 50.0, 100.0, "Watchlist")
    assert avgo["note"] == "Not ordered: the daily loss stop was holding buys · 0.20-delta option $2.00, now +50%, best +100%"
    assert by[("NVDA", "10:05")]["note"].startswith("Not ordered: a NVDA position or buy was already open (one position per ticker)")
    assert by[("PENNY", "10:10")]["note"].startswith("Not ordered: the 0.20-delta option was under $0.05 or had no bid")
    assert by[("LATE", "11:30")]["note"].startswith("Not ordered: outside the buy windows")
    assert by[("SPY", "09:31")]["note"].startswith("Not ordered: not ordered by this bot (the records do not say why)")
    # A day before the fresh start lists no Bot signals; and the real totals never include them.
    acct = bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]
    assert acct["stats"]["n"] == 0


def test_the_scorecard_opens_when_the_bot_files_are_missing_or_broken(tmp_path, monkeypatch):
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({"rows": {"k": closed(10.0, "A", "2026-11-10")}}), encoding="utf-8")
    (tmp_path / "momx_option_track").mkdir()
    monkeypatch.setattr(bd, "_SIGNALS", {})
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text("{broken", encoding="utf-8")
    log = bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]
    assert log["counts"]["signal"] == 0 and log["counts"]["closed"] == 1
    monkeypatch.setattr(chatbot, "records_for", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(bd, "_SIGNALS", {})
    assert bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]["counts"]["closed"] == 1



def test_big_bot_files_are_read_in_the_background_and_kept(tmp_path, monkeypatch):
    """A day's Bot files take ~10 s to read: a Scorecard build answers at once and the list follows."""
    import threading
    monkeypatch.setattr(bd, "_SIGNALS", {})
    monkeypatch.setattr(bd, "_SIGNALS_LOADING", set())
    monkeypatch.setattr(bd, "SIGNAL_INLINE_BYTES", 0)               # treat the test file as big
    gate, calls = threading.Event(), []

    def slow(artifacts, day):
        calls.append(day)
        gate.wait(5)
        return [("bull", _sig("AVGO", "09:44:00"))]

    monkeypatch.setattr(bd, "_load_signals", slow)
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text("{}", encoding="utf-8")
    assert bd._bot_signals(tmp_path, "2026-11-10") == []            # nothing kept yet: answers at once
    assert bd._bot_signals(tmp_path, "2026-11-10") == []            # and starts no second read
    gate.set()
    for _ in range(100):
        if bd._SIGNALS:
            break
        time.sleep(0.02)
    assert [t["symbol"] for _, t in bd._bot_signals(tmp_path, "2026-11-10")] == ["AVGO"] and calls == ["2026-11-10"]


def test_the_scorecard_measures_how_each_fill_label_did():
    rows = [order("closed", "A", buyId="1", buyFill=1.0, pl=10.0, closedAt=at(TODAY, "10:00:00"), fillChance={"label": "high"}, buyTries=0),
            order("missed", "B", buyId="2", spreadAtOrder=0.40),                                  # an older order: labelled from its spread
            order("missed", "C", buyId="3", note="The broker refused this buy: not enough buying power in the paper account", fillChance={"label": "high"})]
    rep = bd.account_report(rows, TODAY)
    assert (rep["fillChance"]["high"]["orders"], rep["fillChance"]["high"]["filledAtMid"]) == (1, 1)
    assert (rep["fillChance"]["low"]["orders"], rep["fillChance"]["low"]["filled"]) == (1, 0)
    by = {t["symbol"]: t for t in rep["tradeLog"]["rows"]}
    assert (by["A"]["fillChance"], by["B"]["fillChance"], by["C"]["fillChance"]) == ("high", "low", "high")



def test_the_one_position_reason_only_when_that_ticker_was_live_at_the_signal():
    """Review 2026-11-10: an earlier order that had closed or missed does not explain a later signal."""
    sig = _sig("AAPL", "10:20:00")
    def row(status, buy, closed=None, side="bull"):
        r = {"symbol": "AAPL", "side": side, "buyId": "b", "status": status, "buyAt": at("2026-11-10", buy), "at": at("2026-11-10", buy)}
        if closed:
            r["closedAt"] = at("2026-11-10", closed)
        return r
    why = lambda orders: bd._not_ordered_why("bull", sig, orders, None)
    assert why([row("open", "09:35:00")]).startswith("a AAPL position or buy was already open")
    assert why([row("selling", "09:35:00", side="bear")]).startswith("a AAPL position")                 # either side holds the ticker
    assert why([row("closed", "09:35:00", closed="10:30:00")]).startswith("a AAPL position")              # still held at 10:20
    assert why([row("closed", "09:35:00", closed="09:50:00")]) == "not ordered by this bot (the records do not say why)"
    assert why([row("missed", "09:35:00")]) == "not ordered by this bot (the records do not say why)"
    assert why([row("open", "10:25:00")]) == "not ordered by this bot (the records do not say why)"        # bought after the signal


# ---- his rule 2026-11-10: the paper bots' 5m chart check explains a signal with no order
def _chart_on(sig, price=100.0, **levels):
    from datetime import datetime
    t = datetime.fromisoformat(sig["at"]).timestamp()
    return {"version": 1, "timeframe": "5m", "symbol": sig["symbol"], "barAt": t - 360, "availableAt": t - 60,
            "capturedAt": t - 30, "referencePrice": price, "levels": {"ema21": 99.0, **levels}}


def test_not_ordered_names_the_chart_check_from_the_saved_skip_or_the_signals_own_chart():
    sig = _sig("AAPL", "10:20:00")
    skips = {"bull|level3|AAPL|" + sig["at"]: {"symbol": "AAPL", "side": "bull", "rule": "level3", "at": sig["at"],
                                              "why": "R1 $182.27 is only 0.3% ahead"}}
    assert bd._not_ordered_why("bull", sig, [], None, skips) == "the chart check failed: R1 $182.27 is only 0.3% ahead"
    # A skip for another side or moment is not this signal's reason.
    assert bd._not_ordered_why("bear", sig, [], None, skips) == "not ordered by this bot (the records do not say why)"
    # No saved skip: the same check on the signal's frozen chart - as the Scorecard really holds the
    # signal, slimmed by chatbot._slim (review 2026-11-10: it used to strip the chart to its levels).
    from momx import chatbot
    under = chatbot._slim(dict(sig, setup={"chartContext": _chart_on(sig, ema21=101.0)}))
    assert bd._not_ordered_why("bull", under, [], None) == "the chart check failed: price under the 21 EMA"
    passing = chatbot._slim(dict(sig, setup={"chartContext": _chart_on(sig)}))
    assert bd._not_ordered_why("bull", passing, [], None) == "not ordered by this bot (the records do not say why)"
    # A ticker already held is checked by the bot BEFORE the chart, so it stays the reason.
    held = [{"symbol": "AAPL", "side": "bull", "buyId": "b", "status": "open", "buyAt": at("2026-11-10", "09:35:00"),
             "at": at("2026-11-10", "09:35:00")}]
    assert bd._not_ordered_why("bull", under, held, None).startswith("a AAPL position or buy was already open")
    # The earlier checks still come first: outside the buy windows wins over a saved chart skip.
    late = _sig("AAPL", "11:30:00")
    late_skip = {"k": {"symbol": "AAPL", "side": "bull", "at": late["at"], "why": "price under the 21 EMA"}}
    assert bd._not_ordered_why("bull", late, [], None, late_skip).startswith("outside the buy windows")


def test_the_scorecard_shows_the_chart_check_reason_the_paper_bot_saved(tmp_path, monkeypatch):
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    sig = _sig("AVGO", "09:44:00")
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": [sig]}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    other = order("closed", "A", "2026-11-10", buyId="x", buyFill=1.0, pl=5.0)
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"a": other},
        "chartSkips": {"bull|level3|AVGO|" + sig["at"]: {"symbol": "AVGO", "side": "bull", "rule": "level3", "at": sig["at"],
                                                         "why": "price under the 21 EMA"}}}), encoding="utf-8")
    doc = bd.build(tmp_path, 30, today="2026-11-10")
    by = {t["symbol"]: t for t in doc["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["AVGO"]["note"].startswith("Not ordered: the chart check failed: price under the 21 EMA · ")
    assert "chart check" in doc["strategy"] and doc["strategy"].startswith("Level 1 / 2 / 3")


def test_the_scorecard_rechecks_the_chart_on_a_signal_read_through_records_for(tmp_path, monkeypatch):
    """Review 2026-11-10: no saved skip (an older day file) -> the Scorecard re-runs the chart check on
    the signal as records_for keeps it; before the _slim fix this could never say anything."""
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    sig = _sig("AVGO", "09:44:00")
    sig["setup"] = {"chartContext": _chart_on(sig, ema21=101.0)}
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": [sig]}), encoding="utf-8")
    [(side, slim)] = chatbot.records_for(tmp_path, "2026-11-10", "2026-11-10").signals
    assert bd._not_ordered_why(side, slim, [], None) == "the chart check failed: price under the 21 EMA"
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({"rows": {"a": order("closed", "A", "2026-11-10", buyId="x", buyFill=1.0, pl=5.0)}}),
                                            encoding="utf-8")
    by = {t["symbol"]: t for t in bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["AVGO"]["note"].startswith("Not ordered: the chart check failed: price under the 21 EMA · ")


def test_not_ordered_says_when_the_paper_account_could_not_pay_for_it(tmp_path, monkeypatch):
    """2026-11-10: 3 contracts up to $8 can cost $2,400 and the paper account may hold less."""
    sig = _sig("AAPL", "10:20:00")
    why = "not enough buying power ($1,500 needed, $812 free)"
    funds = {"k": {"symbol": "AAPL", "side": "bull", "rule": "level3", "at": sig["at"], "why": why}}
    assert bd._not_ordered_why("bull", sig, [], None, None, funds) == why
    assert bd._not_ordered_why("bear", sig, [], None, None, funds) == "not ordered by this bot (the records do not say why)"
    # The chart reason comes first when both are on file.
    chart = {"c": {"symbol": "AAPL", "side": "bull", "at": sig["at"], "why": "price under the 21 EMA"}}
    assert bd._not_ordered_why("bull", sig, [], None, chart, funds) == "the chart check failed: price under the 21 EMA"
    # And the Scorecard reads it from the paper bot's day file.
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": [sig]}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"a": order("closed", "A", "2026-11-10", buyId="x", buyFill=1.0, pl=5.0)}, "fundSkips": funds}), encoding="utf-8")
    by = {t["symbol"]: t for t in bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["AAPL"]["note"].startswith(f"Not ordered: {why} · ")


# ---- his top-of-board rule 2026-11-10 ("Mag7 top 3", "use H/L column top")
def test_not_ordered_names_the_top_of_board_check_before_the_chart_and_buying_power(tmp_path, monkeypatch):
    sig = _sig("CMCSA", "10:20:00")
    why = "CMCSA was #9 on the Watchlist by H/L (needs top 7 for 5 min)"
    tops = {"k": {"symbol": "CMCSA", "side": "bull", "rule": "level3", "at": sig["at"], "why": why}}
    chart = {"c": {"symbol": "CMCSA", "side": "bull", "at": sig["at"], "why": "price under the 21 EMA"}}
    funds = {"f": {"symbol": "CMCSA", "side": "bull", "at": sig["at"], "why": "not enough buying power"}}
    assert bd._not_ordered_why("bull", sig, [], None, chart, funds, tops) == why        # top first
    assert bd._not_ordered_why("bear", sig, [], None, None, None, tops) == "not ordered by this bot (the records do not say why)"
    # The earlier checks still come first: outside the buy windows wins over a saved top skip.
    late = _sig("CMCSA", "11:30:00")
    late_top = {"k": {"symbol": "CMCSA", "side": "bull", "at": late["at"], "why": why}}
    assert bd._not_ordered_why("bull", late, [], None, None, None, late_top).startswith("outside the buy windows")
    # No saved skip: the signal's own topHL stamp answers (after a held ticker).
    stamped = dict(sig, topHL={"board": "Watchlist", "n": 7, "rank": 2, "heldSeconds": 130, "hl": 0.9})
    assert bd._not_ordered_why("bull", stamped, [], None) == (
        "CMCSA was #2 on the Watchlist by H/L for only 2 min of the 5 (needs top 7 for 5 min from 9:45)")
    # The same verdict at the record's time: 9:30-9:45 needs only the rank (his "Start trading 9:30").
    opening = dict(_sig("CMCSA", "09:40:00"), topHL={"board": "Watchlist", "n": 7, "rank": 2, "heldSeconds": 0})
    assert bd._not_ordered_why("bull", opening, [], None) == "not ordered by this bot (the records do not say why)"
    held = [{"symbol": "CMCSA", "side": "bull", "buyId": "b", "status": "open", "buyAt": at("2026-11-10", "09:35:00"),
             "at": at("2026-11-10", "09:35:00")}]
    assert bd._not_ordered_why("bull", stamped, held, None).startswith("a CMCSA position or buy was already open")
    # And the Scorecard reads it from the paper bot's day file.
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": [sig]}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"a": order("closed", "A", "2026-11-10", buyId="x", buyFill=1.0, pl=5.0)},
        "topSkips": tops, "chartSkips": chart}), encoding="utf-8")
    by = {t["symbol"]: t for t in bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["CMCSA"]["note"].startswith(f"Not ordered: {why} · ")


def test_a_passed_top_check_never_falls_back_to_the_frozen_stamp(tmp_path, monkeypatch):
    """Review fix: the bot saved that the top check PASSED at buy time ({"passed": time}, no "why");
    the record's stamp (held 0 when it fired) must not be shown as the reason - go on to the others."""
    stamped = dict(_sig("CMCSA", "10:20:00"), topHL={"board": "Watchlist", "n": 7, "rank": 2, "heldSeconds": 0, "hl": 0.9})
    passed = {"k": {"symbol": "CMCSA", "side": "bull", "rule": "level3", "at": stamped["at"],
                    "checkedAt": at("2026-11-10", "10:25:00"), "passed": at("2026-11-10", "10:25:00")}}
    assert bd._not_ordered_why("bull", stamped, [], None).startswith("CMCSA was #2 on the Watchlist by H/L for only 0 s")
    assert bd._not_ordered_why("bull", stamped, [], None, None, None, passed) == (
        "not ordered by this bot (the records do not say why)")
    # The other reasons still answer after a passed top check.
    funds = {"f": {"symbol": "CMCSA", "side": "bull", "at": stamped["at"], "why": "not enough buying power"}}
    assert bd._not_ordered_why("bull", stamped, [], None, None, funds, passed) == "not enough buying power"
    # A passed entry for another side or signal time does not count.
    other = {"k": dict(passed["k"], at=at("2026-11-10", "10:21:00"))}
    assert "for only 0 s" in bd._not_ordered_why("bull", stamped, [], None, None, None, other)
    # And the Scorecard reads it from the paper bot's day file.
    from momx import chatbot
    monkeypatch.setattr(bd, "EVALUATION_START", "2026-11-10")
    monkeypatch.setattr(chatbot, "_CACHE", {})
    monkeypatch.setattr(bd, "_SIGNALS", {})
    (tmp_path / "momx_option_track").mkdir()
    (tmp_path / "momx_option_track" / "2026-11-10.json").write_text(json.dumps({"trades": [stamped]}), encoding="utf-8")
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    folder.mkdir(parents=True)
    (folder / "2026-11-10.json").write_text(json.dumps({
        "rows": {"a": order("closed", "A", "2026-11-10", buyId="x", buyFill=1.0, pl=5.0)},
        "topSkips": passed}), encoding="utf-8")
    by = {t["symbol"]: t for t in bd.build(tmp_path, 30, today="2026-11-10")["accounts"]["paper3"]["tradeLog"]["rows"]}
    assert by["CMCSA"]["note"].startswith("Not ordered: not ordered by this bot (the records do not say why) · ")
    assert "for only" not in by["CMCSA"]["note"]


def test_not_ordered_names_an_option_over_eight_dollars():
    from momx.paper_mirror import MAX_PRICE
    assert MAX_PRICE == 8.00
    dear = _sig("NVDA", "10:20:00", eEntry=8.40, eEntryBid=8.30, eEntryAsk=8.50)
    assert bd._not_ordered_why("bull", dear, [], None) == "the option was over $8.00 (the bot skips those)"
    edge = _sig("NVDA", "10:20:00", eEntry=8.00, eEntryBid=7.95, eEntryAsk=8.05)
    assert bd._not_ordered_why("bull", edge, [], None) == "not ordered by this bot (the records do not say why)"


def test_a_saved_reason_wins_over_the_signal_time_over_8_dollar_price():
    """Review 2026-11-10: the bot sizes on the live mid AFTER its top / chart / fund checks, so a saved
    reason is the truth even when the signal-time 0.20-delta price read over $8."""
    t = {"symbol": "MSFT", "at": "2026-11-10T10:05:00-05:00", "rule": "level3", "eContract": "MSFT_C", "eEntry": 8.2, "eEntryBid": 8.1}
    top = {"k": {"symbol": "MSFT", "side": "bull", "at": t["at"], "why": "MSFT was #2 on the Mag7 board by H/L for only 3 min of the 5"}}
    assert bd._not_ordered_why("bull", t, [], None, top_skips=top).startswith("MSFT was #2 on the Mag7")
    assert bd._not_ordered_why("bull", t, [], None) == "the option was over $8.00 (the bot skips those)"
