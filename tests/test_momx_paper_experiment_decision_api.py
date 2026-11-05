"""Integration: Paper 5 rule Approve / Reject (2026-11-05).

A rule that passed the forward test and the statistics check waits in
"awaiting_approval"; only his POST /api/momx-scanner/paper-experiment-decision
switches it on. Real worker handler + real api_server proxy; the experiment
lab lives in a scratch folder and nothing reaches the broker.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import ai_filter
from momx import paper_experiments as pe
from worker_harness import running_worker

OWNER = "owner-p5-approve@example.com"
VIEWER = "viewer-p5-approve@example.com"
ROUTE = "/api/momx-scanner/paper-experiment-decision"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def lab(tmp_path, monkeypatch):
    lab = pe.ExperimentLab({}, directory=tmp_path / "experiments", roots={})
    lab.state["experiments"] = [{
        "id": "cand1", "createdAt": datetime(2026, 10, 30, 18, tzinfo=ai_filter.ET).timestamp(),
        "frozenAfterDay": "2026-10-30", "state": "awaiting_approval", "decisions": {},
        "screen": {"spec": {"kind": "spread_ceiling", "value": .1}},
        "statsCheck": {"passed": True, "trials": 4, "lower": 3.2}, "reason": "waiting"}]
    monkeypatch.setattr(pe, "_LAB", lab)
    return lab


def test_admin_approves_through_the_proxy_and_paper5_switches_on(tmp_path, monkeypatch, lab):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.as_admin(monkeypatch, email=OWNER)
        status, body = api.post(ROUTE, {"id": "cand1", "decision": "approve"}, headers={ACCESS_EMAIL_HEADER: OWNER})
    assert status == 200 and body["ok"] is True and body["experiment"]["state"] == "active"
    assert lab.state["active"] == "cand1"
    assert lab.state["experiments"][0]["approvedBy"] == OWNER
    assert (lab.directory / "state.json").exists()


def test_reject_keeps_baseline_and_a_second_decision_is_refused(tmp_path, monkeypatch, lab):
    with running_worker(tmp_path, monkeypatch) as worker:
        admin = worker.as_admin(monkeypatch)
        status, body = worker.post(ROUTE, {"id": "cand1", "decision": "reject"}, headers=admin)
        again_status, again = worker.post(ROUTE, {"id": "cand1", "decision": "approve"}, headers=admin)
    assert status == 200 and body["experiment"]["state"] == "rejected"
    assert lab.state["active"] is None
    assert again_status == 409 and "not waiting" in again["error"]


def test_non_admin_and_bad_input_change_nothing(tmp_path, monkeypatch, lab):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        worker.as_admin(monkeypatch, email=OWNER)
        viewer_status, _ = api.post(ROUTE, {"id": "cand1", "decision": "approve"}, headers={ACCESS_EMAIL_HEADER: VIEWER})
        direct_status, _ = worker.post(ROUTE, {"id": "cand1", "decision": "approve"}, headers={"X-AGX-User": VIEWER})
        bad_status, bad = worker.post(ROUTE, {"id": "cand1", "decision": "yes"}, headers={"X-AGX-User": OWNER})
    assert viewer_status == 403 and direct_status == 403
    assert bad_status == 400 and "approve or reject" in bad["error"]
    assert lab.state["experiments"][0]["state"] == "awaiting_approval" and lab.state["active"] is None
