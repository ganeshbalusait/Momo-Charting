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
    # Judge every seeded order whatever today's date is (the fresh-start day has its own tests).
    from momx import bot_desk
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    return base


def seed(base):
    order = lambda sym, status, **x: {"side": "bull", "rule": "rvol", "symbol": sym, "at": AT, "qty": 3,
                                      "buyLimit": 1.0, "status": status,
                                      # Today's orders are opened under the plan being judged (2026-11-13: an
                                      # order without this marker is the older plan's and is not judged).
                                      "exitPlan": "chart", **x}
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
    # The fresh evaluation, its strategy, the account balance and the loss study (none yet) are served too.
    assert body["startDay"] == "2026-01-01" and body["strategy"].startswith("Level 1 / 2 / 3")
    assert acct["balance"] == 8900.0 and body["lessons"] is None and body["limits"]["accountSize"] == 10000
    # The three strategies are always served, even though this account traded another rule.
    assert [r["rule"] for r in acct["strategies"]["rows"]] == ["level1", "level2", "level3"]
    assert acct["strategies"]["best"] is None and acct["strategies"]["need"] == 20
    assert all(r["closed"] == 0 for r in acct["strategies"]["rows"])


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


def test_the_journal_is_served_with_the_scorecard(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    journal = body["accounts"]["paper3"]["journal"]
    assert status == 200 and journal["tradeCount"] == 1
    day = journal["periods"]["day"][0]
    assert (day["key"], day["trades"], day["pnl"], day["best"]) == (DAY, 1, -1100.0, None)
    assert [p["trades"] for p in journal["periods"]["week"]] == [1] and journal["periods"]["month"][0]["key"] == DAY[:7]
    assert journal["trades"][0]["symbol"] == "ZETA" and journal["trades"][0]["pnl"] == -1100.0


def test_a_saved_scorecard_is_served_at_once_and_refreshed_behind_it(grade, monkeypatch):
    """2026-11-10, "Taking too much time": the page must not wait for a rebuild on a busy server."""
    import threading
    import time as _time
    from momx import bot_desk
    release = threading.Event()
    built = []

    def build(base, days):
        built.append(days)
        if len(built) > 1:
            release.wait(5)                              # the background rebuild is slow
        return {"copy": len(built)}

    monkeypatch.setattr(bot_desk, "build", build)
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_BUILDING", set())
    assert api_server._momx_bot_desk(90) == {"copy": 1}          # the very first read builds
    assert api_server._momx_bot_desk(90) == {"copy": 1} and built == [90]            # fresh: no rebuild
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_FRESH_SECONDS", 0.0)             # now the copy counts as old
    started = _time.monotonic()
    assert api_server._momx_bot_desk(90) == {"copy": 1}          # the old copy, immediately
    assert api_server._momx_bot_desk(90) == {"copy": 1}          # and no second rebuild while one is running
    assert _time.monotonic() - started < 1
    for _ in range(100):
        if len(built) == 2:
            break
        _time.sleep(0.02)
    assert built == [90, 90]
    release.set()
    for _ in range(100):
        if api_server._MOMX_BOT_DESK.get(90, (0, {}))[1] == {"copy": 2}:
            break
        _time.sleep(0.02)
    assert api_server._MOMX_BOT_DESK[90][1] == {"copy": 2}       # the next read gets the new numbers
    api_server._momx_bot_desk_clear()                            # a Pause / Remove press: never the old state
    release.set()
    assert api_server._momx_bot_desk(90)["copy"] >= 3


def test_all_trades_are_served_with_the_scorecard(grade):
    """2026-11-10, "Scoreboard I don't see all trade": every signal, whatever became of it."""
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
        refused, _ = api.get("/api/momx-scanner/bot-desk?days=30", headers=USER)
    log = body["accounts"]["paper3"]["tradeLog"]
    assert status == 200 and refused in (401, 403)
    assert log["total"] == 3 and log["counts"] == {"open": 0, "closed": 1, "missed": 1, "waiting": 0, "skipped": 1, "signal": 0}
    by = {t["symbol"]: t for t in log["rows"]}
    assert (by["ZETA"]["status"], by["ZETA"]["pnl"], by["ZETA"]["live"]) == ("closed", -1100.0, False)
    assert (by["AAPL"]["status"], by["AAPL"]["pnl"]) == ("missed", None) and by["AVGO"]["status"] == "skipped"
    assert by["ZETA"]["key"] == "bull|rvol|ZETA|" + AT


def test_a_saved_scorecard_that_is_too_old_is_never_shown(grade, monkeypatch):
    """Review 2026-11-10: a first open after a long break must wait for new numbers, not show hours-old ones."""
    from momx import bot_desk
    built = []
    monkeypatch.setattr(bot_desk, "build", lambda base, days: built.append(days) or {"copy": len(built)})
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_BUILDING", set())
    assert api_server._momx_bot_desk(90) == {"copy": 1}
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_MAX_STALE_SECONDS", 0.0)
    assert api_server._momx_bot_desk(90) == {"copy": 2}          # built now, not the old copy

    # No thread to spare for the background refresh: the saved copy is still served and the next read tries again.
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_MAX_STALE_SECONDS", 150.0)
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK_FRESH_SECONDS", 0.0)

    class NoThread:
        def __init__(self, *a, **k):
            pass

        def start(self):
            raise RuntimeError("can't start new thread")

    monkeypatch.setattr(api_server.threading, "Thread", NoThread)
    assert api_server._momx_bot_desk(90) == {"copy": 2}
    assert api_server._MOMX_BOT_DESK_BUILDING == set()


def test_the_alpaca_account_is_served_with_the_scorecard(grade):
    seed(grade)
    path = grade / "momx_paper_mirror" / "paper3" / f"{DAY}.json"
    doc = json.loads(path.read_text(encoding="utf-8"))
    doc["broker"] = {"at": DAY + "T12:29:00-05:00", "cash": 517.93, "optionsBuyingPower": 517.93, "buyingPower": 2071.72,
                     "equity": 967.93, "lastEquity": 2809.79}
    path.write_text(json.dumps(doc), encoding="utf-8")
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    b = body["accounts"]["paper3"]["broker"]
    assert status == 200 and (b["optionsBuyingPower"], b["equity"], b["lastEquity"]) == (517.93, 967.93, 2809.79)


def test_all_trades_over_the_route_includes_the_bot_signals_not_ordered(grade):
    seed(grade)
    sig = {"rule": "level2", "symbol": "AVGO", "board": "Mag7", "at": DAY + "T09:44:00-05:00", "status": "open",
           "eContract": "AVGO261113C00340000", "eEntry": 3.58, "eEntryBid": 3.5, "eEntryAsk": 3.65, "eLast": 5.2, "eBest": 8.35}
    (grade / "momx_option_track").mkdir(parents=True, exist_ok=True)
    (grade / "momx_option_track" / f"{DAY}.json").write_text(json.dumps({"trades": [sig]}), encoding="utf-8")
    from momx import bot_desk, chatbot
    chatbot._CACHE.clear()
    bot_desk._SIGNALS.clear()
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    log = body["accounts"]["paper3"]["tradeLog"]
    row = next(t for t in log["rows"] if t["status"] == "signal")
    assert status == 200 and log["counts"]["signal"] == 1
    assert (row["symbol"], row["board"], row["pnl"], row["roiPct"], row["bestPct"]) == ("AVGO", "Mag7", None, 45.3, 133.2)
    assert row["note"].startswith("Not ordered: ")


def test_fill_labels_are_served_with_the_scorecard(grade):
    seed(grade)
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=30", headers=ADMIN)
    acct = body["accounts"]["paper3"]
    assert status == 200 and set(acct["fillChance"]) == {"high", "medium", "low"}
    assert all("fillChance" in t for t in acct["tradeLog"]["rows"])
