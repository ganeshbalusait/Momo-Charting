"""Integration: the Scorecard's Pause / Resume / Remove buttons (2026-11-09).

POST /api/momx-scanner/paper-control through the real api_server proxy to the
real worker handler and the real paper mirror, then the Scorecard read
(GET /api/momx-scanner/bot-desk) shows the result. The broker is a fake; the
paper files live in a scratch folder.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import paper_control, paper_mirror
from worker_harness import running_worker

OWNER = "owner-paper-control@example.com"
VIEWER = "viewer-paper-control@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
ROUTE = "/api/momx-scanner/paper-control"
DESK = "/api/momx-scanner/bot-desk?days=30"
STUCK_KEY = "bear|call2h|CCJ|old"


class Broker:
    """Has no order on file unless one is put in `orders`."""

    def __init__(self):
        self.credentials = SimpleNamespace(paper=True)
        self.orders = {}
        self.cancelled = []

    def get_order_by_client_id(self, cid):
        if cid not in self.orders:
            raise RuntimeError('{"code":40410000,"message":"order not found for %s"}' % cid)
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.orders[oid].status = "canceled"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def paper(tmp_path, monkeypatch):
    """One real PaperMirror for paper3 in a scratch folder, registered as the worker's."""
    base = tmp_path / "grade"
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    # Judge every seeded order whatever today's date is (the fresh-start day has its own tests).
    from momx import bot_desk
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    # The clock is read here, not at import: a suite that crosses midnight ET
    # must not leave these rows on "yesterday".
    NOW = datetime.now(paper_mirror.ET)
    DAY = NOW.date().isoformat()
    broker = Broker()
    mirror = paper_mirror.PaperMirror({}, base / "momx_paper_mirror" / "paper3", client_factory=lambda: broker,
                                      off=lambda: False, account="paper3")
    sent = (NOW - timedelta(days=1)).replace(microsecond=0).isoformat()
    mirror._load(DAY)
    # Test correction 2026-11-14: every order the mirror sends since the chart plan began
    # (bot_desk.CHART_PLAN_DAY) carries exitPlan "chart"; one without it is the OLD plan's and
    # is left out of the Scorecard's counts (bot_desk.old_plan_after_switch). The stuck order
    # had no marker, so once removed it was no longer counted as missed. It is marked like a
    # real order of today's plan.
    mirror._rows = {
        STUCK_KEY: {"side": "bear", "rule": "call2h", "symbol": "CCJ", "at": sent, "contract": "CCJ   261106P00072000",
                    "qty": 3, "buyLimit": 1.0, "buyId": "mxp-b0-ccj", "buyAt": sent, "status": "buying", "carried": True,
                    "exitPlan": paper_mirror.EXIT_PLAN},
        "bull|rvol|AAPL|today": {"side": "bull", "rule": "rvol", "symbol": "AAPL", "at": DAY + "T09:31:00-05:00", "qty": 3,
                                 "buyLimit": 1.0, "buyFill": 1.0, "buyId": "mxp-b0-aapl", "status": "closed", "pl": -60.0,
                                 "closedAt": DAY + "T10:00:00-05:00"},
    }
    mirror._save()
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [mirror])
    return SimpleNamespace(base=base, mirror=mirror, broker=broker, root=base / "momx_paper_mirror", day=DAY)


def both(tmp_path, monkeypatch, paper):
    """Worker + api_server; the Scorecard reads the same scratch folder the mirror writes."""
    worker_cm = running_worker(tmp_path, monkeypatch)
    worker = worker_cm.__enter__()
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(paper.base))       # after running_worker set its own
    worker.as_admin(monkeypatch, email=OWNER)
    return worker_cm, worker


def test_pause_then_resume_through_the_proxy_shows_on_the_scorecard(tmp_path, monkeypatch, paper):
    worker_cm, worker = both(tmp_path, monkeypatch, paper)
    try:
        with running_api(worker_port=worker.port) as api:
            _, before = api.get(DESK, headers=ADMIN)
            status, body = api.post(ROUTE, {"action": "pause", "account": "paper3"}, headers=ADMIN)
            _, after = api.get(DESK, headers=ADMIN)                  # not the 30 s cached copy from `before`
            resume_status, resumed = api.post(ROUTE, {"action": "resume", "account": "paper3"}, headers=ADMIN)
            _, final = api.get(DESK, headers=ADMIN)
    finally:
        worker_cm.__exit__(None, None, None)
    assert before["accounts"]["paper3"]["control"]["paused"] is False
    assert status == 200 and body["ok"] is True and body["paused"] is True
    control = after["accounts"]["paper3"]["control"]
    assert control["paused"] is True and control["pausedAt"] == body["at"]
    assert control["dailyStop"] == {"on": False, "limit": 500, "hit": False, "hitAt": None, "todayPnl": -60.0}
    assert resume_status == 200 and resumed["paused"] is False
    assert final["accounts"]["paper3"]["control"]["paused"] is False
    assert paper_control.paused(paper.root, "paper3") is None


def test_remove_a_stuck_order_through_the_proxy_frees_it_on_the_scorecard(tmp_path, monkeypatch, paper):
    worker_cm, worker = both(tmp_path, monkeypatch, paper)
    try:
        with running_api(worker_port=worker.port) as api:
            _, before = api.get(DESK, headers=ADMIN)
            status, body = api.post(ROUTE, {"action": "remove", "account": "paper3", "key": STUCK_KEY}, headers=ADMIN)
            _, after = api.get(DESK, headers=ADMIN)
            again_status, again = api.post(ROUTE, {"action": "remove", "account": "paper3", "key": STUCK_KEY}, headers=ADMIN)
    finally:
        worker_cm.__exit__(None, None, None)
    orders = before["accounts"]["paper3"]["orders"]
    assert orders["stuck"] == 1 and orders["stuckRows"][0]["key"] == STUCK_KEY and orders["missed"] == 0
    assert status == 200 and body == {"ok": True, "account": "paper3", "key": STUCK_KEY, "symbol": "CCJ"}
    orders = after["accounts"]["paper3"]["orders"]
    assert orders["stuck"] == 0 and orders["stuckRows"] == [] and orders["missed"] == 1
    saved = json.loads((paper.root / "paper3" / f"{paper.day}.json").read_text(encoding="utf-8"))
    assert saved["rows"][STUCK_KEY]["status"] == "missed" and saved["rows"][STUCK_KEY]["removedBy"] == OWNER
    assert again_status == 409 and "not waiting to buy" in again["error"]


def test_an_order_that_filled_at_the_broker_is_not_removed(tmp_path, monkeypatch, paper):
    paper.broker.orders["mxp-b0-ccj"] = SimpleNamespace(id="mxp-b0-ccj", status="filled", filled_avg_price=1.0, filled_qty=3)
    worker_cm, worker = both(tmp_path, monkeypatch, paper)
    try:
        with running_api(worker_port=worker.port) as api:
            status, body = api.post(ROUTE, {"action": "remove", "account": "paper3", "key": STUCK_KEY}, headers=ADMIN)
    finally:
        worker_cm.__exit__(None, None, None)
    assert status == 409 and body["ok"] is False and "real position" in body["error"]
    assert paper.mirror._rows[STUCK_KEY]["status"] == "buying"


def test_non_admin_bad_input_and_an_unknown_account_change_nothing(tmp_path, monkeypatch, paper):
    worker_cm, worker = both(tmp_path, monkeypatch, paper)
    try:
        with running_api(worker_port=worker.port) as api:
            viewer_status, _ = api.post(ROUTE, {"action": "pause", "account": "paper3"}, headers={ACCESS_EMAIL_HEADER: VIEWER})
        direct_status, _ = worker.post(ROUTE, {"action": "pause", "account": "paper3"}, headers={"X-AGX-User": VIEWER})
        bad_status, bad = worker.post(ROUTE, {"action": "sell everything", "account": "paper3"}, headers={"X-AGX-User": OWNER})
        nokey_status, _ = worker.post(ROUTE, {"action": "remove", "account": "paper3"}, headers={"X-AGX-User": OWNER})
        list_status, listed = worker.post(ROUTE, ["pause"], headers={"X-AGX-User": OWNER})
        other_status, other = worker.post(ROUTE, {"action": "pause", "account": "paper9"}, headers={"X-AGX-User": OWNER})
    finally:
        worker_cm.__exit__(None, None, None)
    assert viewer_status == 403 and direct_status == 403
    assert bad_status == 400 and "pause, resume or remove" in bad["error"] and nokey_status == 400
    assert list_status == 400 and "pause, resume or remove" in listed["error"]      # not a 500 with a Python error
    assert other_status == 409 and "not running" in other["error"]
    assert paper_control.load(paper.root) == {"paused": {}}
    assert paper.mirror._rows[STUCK_KEY]["status"] == "buying"


def test_a_scorecard_build_that_started_before_a_button_press_is_not_cached_after_it(tmp_path, monkeypatch):
    """The 60 s poll's build can straddle a Pause: its older copy must not be
    put back into the cache after the press cleared it."""
    from momx import bot_desk
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    answers = iter([{"copy": "before the press"}, {"copy": "after the press"}])

    def build(base, days):
        doc = next(answers)
        if doc["copy"] == "before the press":
            api_server._momx_bot_desk_clear()          # the press lands while this build is running
        return doc

    monkeypatch.setattr(bot_desk, "build", build)
    assert api_server._momx_bot_desk(90) == {"copy": "before the press"}     # its own caller still gets it
    assert api_server._MOMX_BOT_DESK == {}
    assert api_server._momx_bot_desk(90) == {"copy": "after the press"}
    assert api_server._momx_bot_desk(90) == {"copy": "after the press"}      # and that one is cached
