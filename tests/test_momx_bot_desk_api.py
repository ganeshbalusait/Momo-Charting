"""Integration: GET /api/momx-scanner/bot-desk over HTTP (2026-11-09).

The BOT page's Scorecard: the paper accounts judged like a prop-firm evaluation,
read from the saved paper-mirror day files only.
"""
from __future__ import annotations

import json
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER

OWNER = "owner-desk@example.com"
VIEWER = "viewer-desk@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
DAY = datetime.now(ZoneInfo("America/New_York")).date().isoformat()   # inside the route's window, any day
AT = DAY + "T09:30:03-05:00"


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def grade(tmp_path, monkeypatch):
    base = tmp_path / "grade"
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    return base


def seed(base):
    order = lambda sym, status, **x: {"side": "bull", "rule": "rvol", "symbol": sym, "at": AT, "qty": 3,
                                      "buyLimit": 1.0, "status": status, **x}
    path = base / "momx_paper_mirror" / "paper3" / f"{DAY}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"rows": {
        "bull|rvol|AAPL|" + AT: order("AAPL", "missed"),
        "bull|rvol|ZETA|" + AT: order("ZETA", "closed", buyFill=1.0, pl=-1100.0, closedAt=DAY + "T10:00:00-05:00"),
        "bull|rvol|AVGO|" + AT: order("AVGO", "skipped"),
    }}), encoding="utf-8")


def test_admin_gets_the_scorecard(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    assert status == 200 and body["available"] is True and body["days"] == 30
    acct = body["accounts"]["paper3"]
    assert (acct["orders"]["sent"], acct["orders"]["filled"], acct["orders"]["missed"], acct["orders"]["skipped"]) == (2, 1, 1, 1)
    assert acct["stats"]["netPnl"] == -1100.0 and acct["stats"]["winRatePct"] == 0.0
    assert acct["verdict"] == {"ready": False, "passed": 1, "total": 8}      # only "fill 50%" is met
    daily = next(o for o in acct["objectives"] if o["key"] == "dailyLoss")
    assert (daily["pass"], daily["breaks"], daily["target"]) == (False, 1, -500)
    assert body["limits"]["profitTarget"] == 1000 and body["paused"] is False


def test_non_admin_is_refused(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk", headers=USER)
    assert status == 403 and body == {"error": "admin only"}


def test_no_paper_files_is_unavailable_not_an_error(grade):
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=abc", headers=ADMIN)
    assert status == 200
    assert body["available"] is False and body["days"] == 90 and body["accounts"] == {}


def test_the_off_switch_file_shows_as_paused(grade):
    seed(grade)
    (grade / "paper_mirror_off").write_text("", encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk", headers=ADMIN)
    assert status == 200 and body["paused"] is True
