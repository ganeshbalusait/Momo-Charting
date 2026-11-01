import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from momx import paper_experiments as pe
from momx import ai_filter

ET = ai_filter.ET


class Book:
    def __init__(self, rows): self.rows = rows
    def trades(self): return self.rows


class Mirror:
    def __init__(self, rows, account="paper3"): self.rows, self.account = rows, account
    def history(self, days): return [{"day": "2026-11-02", "rows": self.rows}]


def experiment():
    return {"id": "candidate", "createdAt": datetime(2026, 10, 30, 18, tzinfo=ET).timestamp(),
            "frozenAfterDay": "2026-10-30", "state": "forward", "decisions": {},
            "screen": {"spec": {"kind": "spread_ceiling", "value": .1}}, "reason": "future test"}


def cohort():
    rows, decisions = [], {}
    for d in range(10):
        day = datetime(2026, 11, 2, 10, tzinfo=ET)+timedelta(days=d)
        for i in range(10):
            key = f"{d}-{i}"
            take = i < 5
            rows.append({"buyId": key, "buyAt": day.isoformat(), "closedAt": (day+timedelta(hours=1)).isoformat(), "status": "closed", "buyFill": 1,
                         "sells": [{"qty": 1, "price": 1.5 if take else .5}]})
            decisions[key] = {"day": day.date().isoformat(), "take": take, "observedAt": day.timestamp(), "inputs": {"spread": .01 if take else .5}}
    return rows, decisions


def test_planner_rejects_arbitrary_code_tools_and_unbounded_specs():
    for value in [{"kind": "run_shell", "reason": "x"}, {"kind": "spread_ceiling", "reason": 1}, []]:
        with pytest.raises(ValueError): pe.plan(json.dumps(value))
    with pytest.raises(ValueError): pe.accepts({"kind": "spread_ceiling", "value": 999}, {})
    assert pe.accepts({"kind": "spread_ceiling", "value": .1}, {"spread": None})


def test_inputs_exclude_outcomes_and_use_only_entry_quote():
    t = {"entry": 1, "entryBid": .9, "entryAsk": 1.1, "at": "2026-11-02T10:00:00-05:00", "exitPrice": 1000, "best": 999}
    x = pe.inputs(t)
    assert set(x) == {"spread", "premium", "minute"}
    t.update(exitPrice=0, best=0, lastBid=99, lastAsk=100)
    assert pe.inputs(t) == x
    assert pe.inputs({**t, "entryAsk": .8})["spread"] is None


def test_screen_selects_on_old_days_and_validation_can_reject():
    days = {}
    for d in range(10):
        day = f"2026-10-{d+1:02}"
        days[day] = [("bull", {"rule": "rvol", "symbol": f"S{i}", "at": day+"T10:00:00-04:00", "entry": 1,
                                "exitPrice": 1.5 if i < 10 else .5, "entryBid": .99 if i < 10 else .5,
                                "entryAsk": 1.01 if i < 10 else 1.5}) for i in range(20)]
    first = pe.screen("spread_ceiling", days)
    assert first["passed"] and max(first["trainDays"]) < min(first["validationDays"])
    for d in first["validationDays"]:
        for _, t in days[d]: t["exitPrice"] = .5 if t["entryBid"] == .99 else 1.5
    second = pe.screen("spread_ceiling", days)
    assert second["spec"] == first["spec"] and not second["passed"]


def test_fill_accounting_uses_actual_sells_not_requested_quantity():
    result = pe.fill_result({"status": "closed", "qty": 10, "buyFill": 2, "sells": [{"qty": 2, "price": 1}]})
    assert result == {"qty": 2, "cost": 400, "gross": -200}
    assert pe.fill_result({"status": "open", "buyFill": 2, "sells": [{"qty": 2, "price": 1}]}) is None


def test_fixed_future_window_promotes_and_survives_restart(tmp_path):
    rows, decisions = cohort()
    e = experiment(); e["decisions"] = decisions
    lab = pe.ExperimentLab({}, directory=tmp_path, roots={})
    lab.state["experiments"] = [e]
    lab.evaluate(e, rows[:90], datetime(2026, 11, 20, 18, tzinfo=ET))
    assert e["state"] == "forward" and lab.state["active"] is None
    lab.evaluate(e, rows, datetime(2026, 11, 20, 18, tzinfo=ET))
    assert e["state"] == "active" and lab.state["active"] == "candidate"
    assert e["evaluation"]["avoidedLosses"] == 50 and e["evaluation"]["missedWinners"] == 0
    restored = pe.ExperimentLab({}, directory=tmp_path, roots={})
    assert restored.decision("bull", {"entry": 1, "entryBid": .5, "entryAsk": 1.5}, 1)["approved"] is True
    restored.mirrors = [Mirror([])]
    restored.observe()
    assert restored.decision("bull", {"entry": 1, "entryBid": .5, "entryAsk": 1.5}, 1)["approved"] is False
    assert restored.decision("bull", {"entry": 1, "entryBid": .99, "entryAsk": 1.01}, 1)["approved"] is True


def test_missing_decisions_cannot_be_backfilled_after_close_or_promoted(tmp_path):
    rows, decisions = cohort()
    e = experiment(); e["decisions"] = {k:v for k,v in decisions.items() if k.startswith("0-")}
    lab = pe.ExperimentLab({}, [Mirror(rows)], tmp_path, roots={}, clock=lambda: datetime(2026, 11, 20, 18, tzinfo=ET).timestamp())
    lab.state["experiments"] = [e]
    lab.observe()
    assert len(e["decisions"]) == 10 and e["state"] == "rejected"


def test_decision_is_frozen_before_outcome_and_never_recomputed(tmp_path):
    at = "2026-11-02T10:00:00-05:00"
    t = {"side":"bull", "symbol":"AAPL", "rule":"rvol", "at":at, "entry":1, "entryBid":.5,"entryAsk":1.5,"status":"open"}
    row = {**t, "buyId":"order", "buyAt":at, "status":"buying"}
    lab = pe.ExperimentLab({"bull":Book([t])}, [Mirror([row])], tmp_path, roots={}, clock=lambda: datetime(2026,11,2,10,0,20,tzinfo=ET).timestamp())
    e=experiment(); lab.state["experiments"]=[e]
    lab.observe()
    assert e["decisions"]["order"]["take"] is False
    t.update(entryBid=.99,entryAsk=1.01,exitPrice=9,status="closed")
    row.update(status="closed",buyFill=1,sells=[{"qty":1,"price":9}],closedAt="2026-11-02T10:01:00-05:00")
    lab.observe()
    assert e["decisions"]["order"]["take"] is False
    assert e["evaluation"]["missedWinners"] == 1


def test_monitor_deterioration_rolls_back_without_touching_exits(tmp_path):
    e=experiment(); e.update(state="active",monitorAfterDay="2026-11-01")
    rows, decisions=cohort(); e["decisions"]=decisions
    for row in rows[:50]:
        row["sells"][0]["price"] = .5 if decisions[row["buyId"]]["take"] else 1.5
    lab=pe.ExperimentLab({},directory=tmp_path,roots={})
    lab.state.update(experiments=[e],active=e["id"])
    lab.evaluate(e,rows[:50],datetime(2026,11,20,18,tzinfo=ET)); lab.refresh_policy()
    assert e["state"] == "rolled_back" and lab.state["active"] is None
    assert lab.decision("bull",{},1)["approved"] is True


def test_costs_and_retained_winners_are_required_for_promotion():
    rows, decisions=cohort()
    report=pe.paired([{**decisions[r["buyId"]], **pe.fill_result(r)} for r in rows])
    report["decisionCoverage"]=1
    report["outcomeCoverage"]=1
    assert pe.qualified(report)
    assert report['baselineWins'] == 50 and report['baselineLosses'] == 50
    assert report['candidateWins'] == 50 and report['candidateLosses'] == 0
    assert report['baselineWinRate'] == .5 and report['candidateWinRate'] == 1
    for change in [{'candidateWinRate': .5}, {'candidateLossTotal': report['baselineLossTotal']},
                   {'candidateWinRate': None}, {'baselineLossTotal': None}]:
        assert not pe.qualified({**report, **change})
    for field, value in [("candidateStressNet",-1),("candidateNet",-1),("decisionCoverage",.9),("outcomeCoverage",.99),("dailyImprovementLower95",0),("trades",99),("retention",.2)]:
        assert not pe.qualified({**report,field:value})


def test_planner_failures_are_durable_and_do_not_change_entry_policy(tmp_path):
    reviewer=SimpleNamespace(request=lambda *args: {"provider":"unavailable"})
    lab=pe.ExperimentLab({},directory=tmp_path,roots={},reviewer=reviewer,clock=lambda:1000)
    assert not lab.propose("batch",{}) and lab.status()["error"]
    assert lab.decision("bull",{},1000)["approved"]
    restored=pe.ExperimentLab({},directory=tmp_path,roots={},reviewer=reviewer,clock=lambda:1001)
    assert not restored.propose("batch",{})


def test_promoted_policy_controls_paper5_entry_without_network_and_preserves_mid_limits(tmp_path):
    from test_momx_paper_mirror import FakeClient, Book as MirrorBook, NOW, _trade
    from momx.paper_mirror import PaperMirror
    from momx.paper_learning import trade_first
    lab=pe.ExperimentLab({},directory=tmp_path/"lab",roots={})
    lab.policy={"id":"candidate","spec":{"kind":"spread_ceiling","value":.1}}
    lab.last_observed = lab.clock()
    trade=_trade(entryBid=.5,entryAsk=1.9)
    client=FakeClient()
    mirror=PaperMirror({"bull":MirrorBook([trade])},tmp_path/"paper5",account="paper5",jev_cut=None,
                       client_factory=lambda:client,off=lambda:False,ai_policy=lab.decision)
    mirror.tick(NOW)
    assert not client.calls and not lab.execution[next(iter(lab.execution))]["qualified"]
    baseline=FakeClient()
    ordinary=PaperMirror({"bull":MirrorBook([trade])},tmp_path/"paper3",account="paper3",jev_cut=None,
                        client_factory=lambda:baseline,off=lambda:False,ai_policy=trade_first)
    ordinary.tick(NOW)
    assert baseline.calls[0][3] == 1.2
    lab.policy=None
    mirror.tick(NOW)
    assert client.calls[0][3] == 1.2


def test_actual_paper5_daily_loss_rolls_back_next_entries_only(tmp_path):
    e=experiment(); e.update(state="active",monitorAfterDay="2026-11-01")
    paper5=Mirror([{"buyId":"x","buyAt":"2026-11-02T10:00:00-05:00","closedAt":"2026-11-02T11:00:00-05:00",
                    "status":"closed","buyFill":5,"sells":[{"qty":2,"price":1}],"aiDecision":{"experimentId":"candidate"}}],"paper5")
    lab=pe.ExperimentLab({},[Mirror([]),paper5],tmp_path,roots={})
    lab.state.update(experiments=[e],active=e["id"])
    lab.observe()
    assert e["state"] == "rolled_back" and lab.policy is None
    assert paper5.rows[0]["sells"] == [{"qty":2,"price":1}]


def test_original_cohort_survives_history_rolloff_and_restart(tmp_path):
    rows, decisions = cohort()
    e = experiment(); e["decisions"] = decisions
    lab = pe.ExperimentLab({}, directory=tmp_path, roots={})
    lab.state["experiments"] = [e]
    lab.evaluate(e, rows[:90], datetime(2026,11,10,12,tzinfo=ET))
    lab.save()
    restored = pe.ExperimentLab({}, directory=tmp_path, roots={})
    current = restored.state["experiments"][0]
    restored.evaluate(current, rows[90:], datetime(2026,11,20,18,tzinfo=ET))
    assert current["state"] == "active"
    assert current["evaluation"]["trades"] == 100
    assert current["evaluation"]["entryDays"][0] == "2026-11-02"
    assert "cohort" not in restored.status()["latest"]


@pytest.mark.parametrize("bad", [{"sells": []}, {"closedAt": None}, {"buyFilledQty": 2}, {"held": 1}])
def test_incomplete_closed_outcomes_cannot_disappear_from_denominator(tmp_path, bad):
    rows, decisions = cohort()
    rows[-1].update(bad)
    e = experiment(); e["decisions"] = decisions
    lab = pe.ExperimentLab({}, directory=tmp_path, roots={})
    lab.evaluate(e, rows, datetime(2026,11,20,18,tzinfo=ET))
    assert e["state"] == "rejected"
    assert e["evaluation"]["unpricedClosed"] == 1
    assert e["evaluation"]["outcomeCoverage"] == .99


def test_drawdown_uses_realized_close_dates_and_counts_entry_days():
    report = pe.paired([
        {"day":"2026-11-02", "outcomeDay":"2026-11-04", "qty":1,"cost":100,"gross":100,"take":True},
        {"day":"2026-11-03", "outcomeDay":"2026-11-04", "qty":1,"cost":100,"gross":-90,"take":True}])
    assert report["days"] == 2 and report["outcomeDays"] == 1
    assert report["baselineDrawdown"] == 0
    assert report["daily"]["2026-11-04"][0] == pytest.approx(10.0)


def test_stale_or_failed_monitor_restores_baseline_without_network_in_decision(tmp_path):
    now = [1000]
    mirror = Mirror([])
    e = experiment(); e["state"] = "active"
    lab = pe.ExperimentLab({}, [mirror], tmp_path, roots={}, clock=lambda:now[0])
    lab.state.update(experiments=[e],active=e["id"])
    lab.observe()
    trade = {"entry":1,"entryBid":.5,"entryAsk":1.5}
    assert not lab.decision("bull",trade,1000)["approved"]
    now[0] += 121
    assert lab.decision("bull",trade,1121)["approved"] and lab.status()["activeId"] is None
    lab.observe()
    assert not lab.decision("bull",trade,1121)["approved"]
    mirror.history = lambda _: (_ for _ in ()).throw(OSError("private error"))
    with pytest.raises(OSError): lab.observe()
    assert lab.decision("bull",trade,1121)["approved"]
    assert "private error" not in lab.status()["error"]


def test_planner_persistence_failure_releases_busy_flag(tmp_path, monkeypatch):
    lab = pe.ExperimentLab({}, directory=tmp_path, roots={})
    monkeypatch.setattr(lab,"save",lambda: (_ for _ in ()).throw(OSError("disk")))
    assert not lab.propose("batch",{})
    assert not lab.busy and lab.status()["error"]


@pytest.mark.parametrize("state", [[], {"experiments":None}, {"experiments":[{"id":"bad","state":"active"}]}])
def test_malformed_saved_state_cannot_enable_candidate(tmp_path, state):
    (tmp_path/"state.json").write_text(json.dumps(state))
    lab = pe.ExperimentLab({},directory=tmp_path,roots={})
    assert lab.policy is None and lab.status()["error"]
