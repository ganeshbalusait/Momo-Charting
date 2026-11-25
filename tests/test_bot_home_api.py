"""Integration: everything the Bot home page reads from GET /api/momx-scanner/bot-desk (2026-11-25).

The page (frontend/src/BotHome.jsx, botHome.js) is one read of the Scorecard route. These tests
serve the real route over HTTP on scratch paper-mirror files and check every field the page
reads is there with the type it expects: for an account that has traded, for one whose only
order never filled, and for a PC with no paper files at all. Nothing leaves the PC.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER

OWNER = "owner-bothome@example.com"
VIEWER = "viewer-bothome@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
NOW = datetime.now(ZoneInfo("America/New_York"))
DAY = NOW.date().isoformat()
EARLIER = (NOW.date() - timedelta(days=3)).isoformat()      # a trade bought on an earlier day than it closed
URL = "/api/momx-scanner/bot-desk?days=90"                   # exactly what the page asks (botDesk.js DESK_URL)
REFUSED = "The broker refused this buy: not enough buying power in the paper account"
NUMBER = (int, float)


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def grade(tmp_path, monkeypatch):
    base = tmp_path / "grade"
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    from momx import bot_desk
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")   # judge the seeded orders whatever today is
    return base


def order(symbol, status, at, side="bull", **extra):
    return {"side": side, "rule": "level2", "levels": ["level1", "level2"], "symbol": symbol, "at": at, "qty": 3, "buyLimit": 1.0,
            "status": status, "exitPlan": "chart", "contract": f"{symbol:<6}261127C00019000",
            "entryAsk": 1.0, "entryStock": 21.38, "stopStock0": 20.1, "stopStock": 20.1, "stopKind": "stop",
            "plan": {"t1": 22.4, "t2": 23.5, "t3": 25.0}, **extra}


def write_day(base, account, day, rows, broker=None):
    path = base / "momx_paper_mirror" / account / f"{day}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = {"rows": rows}
    if broker:
        doc["broker"] = broker
    path.write_text(json.dumps(doc), encoding="utf-8")


def seed(base):
    at = DAY + "T09:49:05-05:00"
    old = EARLIER + "T13:53:32-05:00"
    write_day(base, "paper3", EARLIER, {
        "bull|level2|MCD|" + old: order("MCD", "closed", old, buyFill=1.0, buyFillAt=old, pl=-180.0, closedAt=DAY + "T09:54:34-05:00",
                                         sells=[{"qty": 3, "price": 0.4, "why": "Stop: option down 50% (backstop)"}]),
    })
    write_day(base, "paper3", DAY, {
        "bull|level2|NVDA|" + at: order("NVDA", "closed", at, buyFill=1.0, buyFillAt=at, pl=300.0, closedAt=DAY + "T15:50:00-05:00",
                                         hits=[{"target": "T1", "at": DAY + "T10:00:16-05:00"}],
                                         sells=[{"qty": 1, "price": 2.0, "why": "T1"}, {"qty": 2, "price": 2.0, "why": "15:50 close"}]),
        "bear|level2|KSS|" + at: order("KSS", "closed", at, side="bear", buyFill=1.0, buyFillAt=at, pl=-120.0, closedAt=DAY + "T14:22:00-05:00",
                                        sells=[{"qty": 3, "price": 0.6, "why": "5-minute candle closed above the 21 EMA"}]),
        "bear|level2|COP|" + at: order("COP", "open", at, side="bear", buyFill=0.76, buyFillAt=DAY + "T09:51:00-05:00",
                                        markMid=0.6, markAt=DAY + "T10:30:00-05:00"),
        "bear|level2|M|" + at: order("M", "missed", at, side="bear", qty=15, buyLimit=0.19, note=REFUSED),
    }, broker={"at": DAY + "T10:31:00-05:00", "cash": 9.85, "optionsBuyingPower": 9.85, "buyingPower": 9.85, "equity": 27.85, "lastEquity": 159.85})
    # The second account sent one order and the broker never filled it: no closed trade at all.
    write_day(base, "paper5", DAY, {"bull|level2|AAPL|" + at: order("AAPL", "missed", at)},
              broker={"at": DAY + "T10:31:00-05:00", "cash": 9000.0, "optionsBuyingPower": 9000.0, "buyingPower": 9000.0, "equity": 9000.0, "lastEquity": 9000.0})
    lessons = base / "momx_ai_filter" / "paper" / "lessons.json"
    lessons.parent.mkdir(parents=True, exist_ok=True)
    lessons.write_text(json.dumps({"updatedAt": DAY + "T17:02:27-05:00", "startDay": "2026-01-01", "minLosses": 10, "lessons": [
        {"side": "bull", "rule": "level1", "losses": 31, "wins": 4, "netPnl": -4740.0, "status": "lesson", "source": "openai",
         "lesson": "Most losing trades were filled 10%+ above the signal price.", "proposedChange": "Skip the trade when the fill is 10%+ above the signal price.",
         "howToTest": "Track 20 paper trades.", "buckets": {"bulky": True}},
        {"side": "bear", "rule": "level3", "losses": 6, "wins": 6, "netPnl": -756.0, "status": "collecting", "source": "none",
         "lesson": "Not enough losing trades yet (6 of 10).", "proposedChange": "", "howToTest": ""},
    ]}), encoding="utf-8")


def read(grade_dir, headers=ADMIN):
    with running_api() as api:
        return api.get(URL, headers=headers)


def test_top_level_fields_the_page_reads(grade):
    seed(grade)
    status, body = read(grade)
    assert status == 200 and body["available"] is True
    assert isinstance(body["asOf"], str) and body["asOf"][:10] == DAY and body["asOf"][10] == "T"      # "Read Nov 25, 1:19 AM ET"
    assert body["startDay"] == "2026-01-01" and body["paused"] is False and body["smallSample"] == 20
    for key in ("accountSize", "minProfitFactor", "luckTrades", "dailyLossLimit", "profitTarget"):
        assert isinstance(body["limits"][key], NUMBER)
    assert body["limits"]["accountSize"] == 10000
    assert set(body["accounts"]) == {"paper3", "paper5"}
    assert body["accounts"]["paper3"]["mode"]["name"] == "Rules bot" and body["accounts"]["paper5"]["mode"]["name"] == "AI bot"


def test_home_state_and_result_fields_for_an_account_with_trades(grade):
    seed(grade)
    _, body = read(grade)
    acct = body["accounts"]["paper3"]
    # The state line: the real Alpaca money, the pause switches, the last order.
    assert acct["broker"]["at"][:10] == DAY and (acct["broker"]["optionsBuyingPower"], acct["broker"]["cash"]) == (9.85, 9.85)
    assert acct["control"]["paused"] is False and acct["control"]["dailyStop"]["hit"] is False
    assert isinstance(acct["lastActivity"], str) and acct["lastActivity"][:10] == DAY
    # The one money story: the real result, the trade count, the line by day.
    s = acct["stats"]
    assert (s["n"], s["wins"], s["losses"], s["netPnl"]) == (3, 1, 2, 0.0)
    for key in ("winRatePct", "expectancy", "avgWin", "avgLoss", "profitFactor", "bestTrade", "worstTrade"):
        assert isinstance(s[key], NUMBER), key
    # One point per day with an order (the day MCD was bought closed nothing), oldest first, ending on the result.
    assert [d["day"] for d in acct["days"]] == [EARLIER, DAY] and [d["cum"] for d in acct["days"]] == [0.0, 0.0]
    assert acct["days"][-1]["pnl"] == 0.0 and acct["days"][-1]["closed"] == 3
    d = acct["daySummary"]
    assert (d["tradingDays"], d["greenDays"]) == (1, 0) and isinstance(d["maxDrawdown"], NUMBER)
    assert acct["verdict"]["total"] == 8 and isinstance(acct["verdict"]["passed"], int) and acct["verdict"]["ready"] is False
    assert acct["orders"]["sent"] == 5


def test_journal_fields_for_an_account_with_trades(grade):
    seed(grade)
    _, body = read(grade)
    journal = body["accounts"]["paper3"]["journal"]
    assert journal["tradeCount"] == 3 and set(journal["periods"]) == {"day", "week", "month"}
    day = journal["periods"]["day"][0]
    assert (day["key"], day["trades"], day["pnl"], day["calls"], day["puts"]) == (DAY, 3, 0.0, 120.0, -120.0)
    assert isinstance(day["winRatePct"], NUMBER)
    assert journal["periods"]["month"][0]["key"] == DAY[:7] and len(journal["periods"]["week"][0]["key"]) == 10
    by = {t["symbol"]: t for t in journal["trades"]}
    assert [t["symbol"] for t in journal["trades"]] == ["NVDA", "KSS", "MCD"]                 # newest close first
    nvda, mcd, kss = by["NVDA"], by["MCD"], by["KSS"]
    assert (nvda["side"], nvda["rule"], nvda["levels"], nvda["day"], nvda["qty"], nvda["buy"], nvda["pnl"], nvda["roiPct"]) == \
        ("bull", "level2", ["level1", "level2"], DAY, 3, 1.0, 300.0, 100.0)
    assert nvda["exit"] == "15:50 close" and nvda["closedAt"] == DAY + "T15:50:00-05:00" and nvda["boughtAt"][:10] == DAY
    assert nvda["plan"] == {"entryAsk": 1.0, "entryStock": 21.38, "stop": 20.1, "stopNow": 20.1, "stopKind": "stop", "t1": 22.4, "t2": 23.5, "t3": 25.0,
                            "hits": [{"target": "T1", "at": DAY + "T10:00:16-05:00"}], "ready": True}
    # Bought on an earlier day than it closed: both stamps are served, so the row can say "bought Fri 1:53 PM".
    assert mcd["boughtAt"][:10] == EARLIER and mcd["day"] == DAY and mcd["closedAt"][:10] == DAY
    # The −50% stop that sold at −60%: the saved sentence and the real return are both there for the plain note.
    assert mcd["exit"] == "Stop: option down 50% (backstop)" and mcd["roiPct"] == -60.0
    assert kss["side"] == "bear" and kss["exit"] == "5-minute candle closed above the 21 EMA"


def test_trades_fields_open_and_not_bought(grade):
    seed(grade)
    _, body = read(grade)
    log = body["accounts"]["paper3"]["tradeLog"]
    assert log["counts"] == {"open": 1, "closed": 3, "missed": 1, "waiting": 0, "skipped": 0, "signal": 0} and log["total"] == 5
    by = {t["symbol"]: t for t in log["rows"]}
    cop = by["COP"]
    assert (cop["status"], cop["side"], cop["qty"], cop["buy"], cop["live"], cop["note"]) == ("open", "bear", 3, 0.76, True, "holding")
    assert cop["pnl"] == -48.0 and isinstance(cop["roiPct"], NUMBER) and cop["markAt"] == DAY + "T10:30:00-05:00"
    assert cop["boughtAt"] == DAY + "T09:51:00-05:00" and cop["plan"]["t1"] == 22.4 and isinstance(cop["key"], str)
    missed = by["M"]
    assert (missed["status"], missed["buy"], missed["pnl"], missed["qty"], missed["limit"], missed["note"]) == ("missed", None, None, 15, 0.19, REFUSED)
    assert missed["at"] == DAY + "T09:49:05-05:00" and missed["levels"] == ["level1", "level2"]


def test_learned_and_ready_fields(grade):
    seed(grade)
    _, body = read(grade)
    study = body["lessons"]
    assert study["updatedAt"] == DAY + "T17:02:27-05:00" and study["minLosses"] == 10 and len(study["lessons"]) == 2
    first = study["lessons"][0]
    assert (first["side"], first["rule"], first["losses"], first["wins"], first["netPnl"], first["status"]) == ("bull", "level1", 31, 4, -4740.0, "lesson")
    assert first["lesson"].startswith("Most losing trades") and first["proposedChange"].startswith("Skip the trade")
    assert "buckets" not in first and study["lessons"][1]["status"] == "collecting"
    checks = body["accounts"]["paper3"]["objectives"]
    assert [o["key"] for o in checks] == ["profit", "dailyLoss", "drawdown", "days", "trades", "profitFactor", "luck", "fill"]
    assert all(isinstance(o["target"], NUMBER) and isinstance(o["pass"], bool) for o in checks)
    by = {o["key"]: o for o in checks}
    assert by["dailyLoss"]["breaks"] == 0 and by["luck"]["trades"] == 3 and by["trades"]["actual"] == 3
    assert by["fill"]["actual"] == 80.0 and by["fill"]["pass"] is True


def test_an_account_with_no_closed_trade_still_has_every_field(grade):
    seed(grade)
    _, body = read(grade)
    acct = body["accounts"]["paper5"]
    s = acct["stats"]
    assert (s["n"], s["wins"], s["losses"], s["netPnl"]) == (0, 0, 0, 0.0)
    # Nothing to average yet: the server says null, and the page shows a dash, never $0 or 0%.
    assert s["winRatePct"] is None and s["expectancy"] is None and s["avgWin"] is None and s["profitFactor"] is None
    assert acct["journal"] == {"periods": {"day": [], "week": [], "month": []}, "trades": [], "tradeCount": 0}
    assert acct["days"] == [] or all(d["closed"] == 0 for d in acct["days"])
    assert acct["daySummary"]["tradingDays"] == 0 and acct["daySummary"]["greenDays"] == 0
    assert acct["tradeLog"]["counts"]["missed"] == 1 and acct["tradeLog"]["rows"][0]["note"] == "the order did not fill"
    by = {o["key"]: o for o in acct["objectives"]}
    assert len(by) == 8 and by["profit"]["actual"] is None and by["dailyLoss"]["actual"] is None and by["profitFactor"]["actual"] is None
    assert by["trades"]["actual"] == 0 and by["fill"]["actual"] == 0.0
    assert acct["verdict"]["ready"] is False and acct["verdict"]["total"] == 8
    assert acct["broker"]["optionsBuyingPower"] == 9000.0 and acct["control"]["paused"] is False
    assert acct["lastActivity"] is None or isinstance(acct["lastActivity"], str)


def test_the_pause_switches_reach_the_page(grade):
    seed(grade)
    (grade / "paper_mirror_off").write_text("", encoding="utf-8")
    _, body = read(grade)
    assert body["paused"] is True


def test_no_paper_files_and_no_review(grade):
    status, body = read(grade)
    assert status == 200 and body["available"] is False and body["accounts"] == {} and body["lessons"] is None
    assert isinstance(body["asOf"], str) and body["limits"]["accountSize"] == 10000


def test_only_the_owner_reads_it(grade):
    seed(grade)
    status, body = read(grade, headers=USER)
    assert status == 403 and "accounts" not in body
