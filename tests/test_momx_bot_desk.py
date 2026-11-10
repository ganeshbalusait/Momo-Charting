"""BOT desk scorecard: the paper accounts judged like a prop-firm evaluation (2026-11-09)."""
import json

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


def test_build_reports_pause_and_the_daily_loss_stop_per_account(tmp_path):
    from momx import paper_control
    root = tmp_path / "momx_paper_mirror"
    write(root / "paper3" / f"{TODAY}.json", {"rows": {"a": closed(-300.0, "A"), "b": closed(-260.0, "B")},
                                               "dailyStop": {"at": at(TODAY, "10:42:00"), "pnl": -560.0, "limit": 500.0}})
    write(root / "paper5" / f"{TODAY}.json", {"rows": {"c": closed(120.0, "C")}})
    paper_control.set_paused(root, "paper5", True, "owner@example.com", at(TODAY, "11:00:00"))
    accounts = bd.build(tmp_path, days=30, today=TODAY)["accounts"]
    assert accounts["paper3"]["control"] == {"paused": False, "pausedAt": None, "dailyStop": {
        "limit": 500, "hit": True, "hitAt": at(TODAY, "10:42:00"), "todayPnl": -560.0}}
    assert accounts["paper5"]["control"] == {"paused": True, "pausedAt": at(TODAY, "11:00:00"), "dailyStop": {
        "limit": 500, "hit": False, "hitAt": None, "todayPnl": 120.0}}


def test_before_todays_file_exists_an_older_position_is_not_counted_as_todays_loss(tmp_path):
    folder = tmp_path / "momx_paper_mirror" / "paper3"
    write(folder / "2026-11-06.json", {"rows": {"k": order("open", "ZETA", day="2026-11-06", buyFill=2.0, held=3, markMid=0.1,
                                                         buyAt=at("2026-11-06", "10:00:00"), sells=[])}})
    control = bd.build(tmp_path, days=30, today=TODAY)["accounts"]["paper3"]["control"]
    assert control["dailyStop"] == {"limit": 500, "hit": False, "hitAt": None, "todayPnl": 0.0}


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
