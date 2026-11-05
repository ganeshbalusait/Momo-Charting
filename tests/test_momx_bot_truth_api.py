"""Integration: GET /api/momx-scanner/bot-truth over HTTP (2026-11-04).

The BOT page's Truth check card: the BOT's virtual mid trades vs the real
Alpaca paper orders on the same signals, read from saved day files only.
"""
from __future__ import annotations

import json

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER

OWNER = "owner-truth@example.com"
VIEWER = "viewer-truth@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
from datetime import datetime
from zoneinfo import ZoneInfo

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
    monkeypatch.setattr(api_server, "_MOMX_BOT_TRUTH", {})
    return base


def write(path, doc):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")


def seed(base):
    bot = lambda sym, exit_: {"rule": "rvol", "symbol": sym, "at": AT, "entry": 1.0, "entryBid": 0.95,
                              "entryAsk": 1.05, "exitPrice": exit_, "exitBid": exit_ - 0.05,
                              "exitAsk": exit_ + 0.05, "status": "closed"}
    write(base / "momx_option_track" / f"{DAY}.json", {"trades": [bot("AAPL", 3.0), bot("ZETA", 1.5)]})
    order = lambda sym, status, **x: {"side": "bull", "rule": "rvol", "symbol": sym, "at": AT, "qty": 3,
                                      "buyLimit": 0.95, "status": status, **x}
    write(base / "momx_paper_mirror" / "paper3" / f"{DAY}.json", {"rows": {
        "bull|rvol|AAPL|" + AT: order("AAPL", "missed"),
        "bull|rvol|ZETA|" + AT: order("ZETA", "closed", buyFill=1.05, pl=90.0),
    }})


def test_admin_sees_bot_vs_paper(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-truth?days=60", headers=ADMIN)
    assert status == 200
    acct = body["accounts"]["paper3"]
    assert (acct["orders"], acct["filled"], acct["missed"], acct["fillRatePct"]) == (2, 1, 1, 50.0)
    assert acct["matched"] == {"n": 1, "botPnl": 150.0, "botRoi": 50.0, "paperPnl": 90.0, "paperRoi": 28.6}
    assert acct["missedBotPnl"] == 600.0
    assert acct["biggestMissed"][0]["symbol"] == "AAPL"
    assert body["available"] is True


def test_non_admin_is_refused(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-truth", headers=USER)
    assert status == 403 and body == {"error": "admin only"}


def test_no_paper_files_is_unavailable_not_an_error(grade):
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-truth?days=abc", headers=ADMIN)
    assert status == 200
    assert body["available"] is False and body["days"] == 30
