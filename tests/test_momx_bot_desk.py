"""BOT desk scorecard: the paper accounts judged like a prop-firm evaluation (2026-11-09)."""
import json

from momx import bot_desk as bd

TODAY = "2026-11-09"


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
