from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import paper_research as pr
from momx.paper_experiments import ExperimentLab
from momx.ai_filter import ET


def history():
    result = []
    for d in range(10):
        at = datetime(2026,10,1,10,tzinfo=ET)+timedelta(days=d)
        rows = []
        for i in range(12):
            winner = i < 8
            buy = 1.5 if winner else .25
            rows.append({"buyId": f"{d}-{i}", "buyAt": at.isoformat(),
                "closedAt": (at+timedelta(hours=1)).isoformat(), "status":"closed", "buyFill":buy,
                "buyLimit":buy, "buyFilledQty":2, "held":0, "pl":99999,
                "entryInputs":{"premium":buy,"spread":.02 if winner else .5,"minute":600},
                "sells":[{"price":buy+.5 if winner else 0,"qty":2}]})
        result.append({"day":at.date().isoformat(), "rows":rows})
    return list(reversed(result))


def test_uses_fills_actual_quantity_and_entry_limit_not_future_fill_or_cached_pl():
    h=history()
    r=h[0]["rows"][0]
    r.pop("entryInputs"); r["buyLimit"]=1.25
    rows, counts, fingerprint=pr.dataset(h,"2026-10-10")
    target=next(x for x in rows if x["key"]==r["buyId"])
    assert target["gross"] == 100 and target["qty"] == 2
    assert target["inputs"]["premium"] == 1.25 and target["inputs"]["spread"] is None
    assert counts["legacyInputs"] == 1 and fingerprint


def test_later_validation_cannot_change_family_or_threshold_selection():
    h=history()
    first=pr.screen_fills(h,"2026-10-10","premium_floor")
    assert first["passed"] and len(first["candidates"]) == 11
    for day in h:
        if day["day"] in first["validationDays"]:
            for r in day["rows"]:
                r["sells"][0]["price"] = 0 if r["buyFill"] > 1 else 1
    second=pr.screen_fills(h,"2026-10-10","premium_floor")
    assert not second["passed"] and second["spec"] == first["spec"]
    assert second["candidates"] == first["candidates"]
    assert "no runner-up" in second["reason"]


def test_missing_quotes_disqualify_spread_family_and_keep_other_inputs():
    h=history()
    for day in h:
        for r in day["rows"]: r.pop("entryInputs")
    result=pr.screen_fills(h,"2026-10-10")
    assert result["spec"]["kind"] == "premium_floor"
    spreads=[c for c in result["candidates"] if c["spec"]["kind"]=="spread_ceiling"]
    assert all(c["training"]["inputCoverage"] == 0 and not c["stableTraining"] for c in spreads)


def test_missing_closed_outcomes_block_screen_and_duplicates_use_newest_state():
    h=history()
    invalid=deepcopy(h[0]["rows"][0]); invalid["sells"]=[]
    h.insert(0,{"day":"2026-10-10","rows":[invalid]})
    result=pr.screen_fills(h,"2026-10-10")
    assert not result["passed"] and result["dataset"]["invalidClosed"] == 1
    assert result["dataset"]["orders"] == 120 and not result["candidates"]


def test_purges_training_outcomes_that_were_unknown_at_validation_start():
    h=history()
    h[-1]["rows"][0]["closedAt"]="2026-10-09T15:00:00-04:00"
    result=pr.screen_fills(h,"2026-10-10")
    assert result["purgedTrainingFills"] == 1 and result["trainingFills"] == 83
    assert max(result["trainDays"]) < min(result["validationDays"])


def test_single_good_training_period_cannot_qualify():
    h=history()
    for day in h:
        if day["day"] <= "2026-10-03":
            for r in day["rows"]: r["sells"][0]["price"]=0
    result=pr.screen_fills(h,"2026-10-10")
    assert not result["passed"] and "spec" not in result
    assert not any(c["stableTraining"] for c in result["candidates"])


def test_counts_unfilled_orders_without_fabricating_performance():
    h=history()
    h[0]["rows"].append({"buyId":"missed","buyAt":"2026-10-10T10:00:00-04:00","status":"missed","pl":10000})
    result=pr.screen_fills(h,"2026-10-10")
    assert result["dataset"]["missed"] == 1 and result["dataset"]["usableFills"] == 120


def test_order_time_observation_survives_close_before_first_poll(tmp_path):
    now=datetime(2026,10,2,10,tzinfo=ET).timestamp()+.5
    e={"id":"trial","state":"forward","createdAt":now-86400,"frozenAfterDay":"2026-10-01",
       "inputsVersion":"order_quote_v1","screen":{"spec":{"kind":"premium_floor","value":1.0}},"decisions":{}}
    lab=ExperimentLab({},directory=tmp_path,roots={},clock=lambda:now+60)
    lab.state["experiments"]=[e]; lab.refresh_policy()
    t={"entry":.25,"entryBid":.2,"entryAsk":.3,"entryAt":"2026-10-02T10:00:00-04:00"}
    verdict=lab.baseline_decision("bull",t,now)
    assert verdict["approved"] is True  # It never vetoes a Paper 3 entry.
    observation=verdict["experimentObservations"]["trial"]
    assert observation["take"] is False
    row={"buyId":"fast","buyAt":t["entryAt"],"closedAt":"2026-10-02T10:00:20-04:00",
         "status":"closed","buyFill":.25,"sells":[{"qty":1,"price":0}],
         "entryInputs":observation["inputs"],"aiDecision":verdict}
    class Mirror:
        account="paper3"
        def history(self,_): return [{"rows":[row]}]
    lab.mirrors=[Mirror()]
    lab.observe()
    assert e["decisions"]["fast"]["take"] is False
    assert e["evaluation"]["trades"] == 1 and e["evaluation"]["avoidedLosses"] == 1
    e["decisions"]={}
    observation["observedAt"]=now+30  # Retrospective claims must not be accepted.
    lab.observe()
    assert not e["decisions"] and e["evaluation"]["decisionCoverage"] == 0


def test_unchanged_evidence_does_not_retry_selection_or_call_models(tmp_path):
    h=history()
    report=pr.screen_fills(h,"2026-10-10")
    def unexpected(*args): raise AssertionError("Unchanged evidence must not call a model")
    lab=ExperimentLab({},[SimpleNamespace(account="paper3",history=lambda _:h)],tmp_path,
                      reviewer=SimpleNamespace(request=unexpected),roots={},
                      clock=lambda:datetime(2026,10,11,18,tzinfo=ET).timestamp())
    lab.state["experiments"]=[{"id":"old","state":"rejected","screen":report}]
    assert lab.propose("batch",{}) is False
    assert len(lab.state["experiments"]) == 1 and not lab.busy and not lab.error
