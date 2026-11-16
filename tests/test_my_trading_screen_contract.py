"""My Trading: the screen reads what the real route sends (2026-11-11, owner only).

Fixed fills are stored in a scratch insights folder, the real GET /api/my-insights is asked over HTTP
(tests/api_harness.py; nothing leaves the PC, Schwab is never called by this route), and the reply is run
through the screen's own helpers (frontend/src/myTrading.js) in node. This catches the screen reading a
field the server never sends (tiles of dashes, an empty habit list, a missing sign-in line).
"""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import market_events, schwab_history

ET = ZoneInfo("America/New_York")
OWNER = "owner-mytrading@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
NODE = shutil.which("node")

SCRIPT = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const m = await import(pathToFileURL(process.argv[1] + "/myTrading.js").href);
const doc = JSON.parse(readFileSync(0, "utf8"));
process.stdout.write(JSON.stringify({
  tiles: Object.fromEntries(m.myTiles(doc).map((t) => [t.key, t.value])),
  dim: m.myTiles(doc).find((t) => t.key === "winRate").dim,
  period: m.periodLabel(doc),
  habits: m.myHabits(doc).map((h) => ({ key: h.key, count: h.count, trades: h.trades.map((t) => t.contract), line: h.line })),
  tables: Object.fromEntries(m.MY_TABLES.map((t) => [t.key, m.myTable(doc, t.key).map((r) => [r.label, r.trades])])),
  schwab: m.mySchwab(doc),
  events: m.myEvents(doc),
  execution: m.myExecution(doc),
  versus: m.myVersus(doc).map((v) => v.label),
  has: m.hasTrades(doc),
  empty: m.emptyLine(doc),
}));
"""

SPY = "SPY   261231C00700000"


def _fill(n, at, side, price, qty=1):
    return {"id": f"f{n}", "activityId": f"a{n}", "orderId": f"o{n}", "account": "…5678", "at": at.isoformat(),
            "day": at.date().isoformat(), "underlying": "SPY", "optionSymbol": SPY, "assetType": "OPTION", "putCall": "CALL",
            "strike": 700.0, "expiry": "2026-12-31", "dte": (datetime(2026, 12, 31).date() - at.date()).days, "side": side,
            "positionEffect": None, "quantity": float(qty), "price": float(price), "fees": 0.66, "multiplier": 100.0, "kind": "trade"}


def _last_weekday(now):
    day = now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _fills(now):
    """Four round trips on one recent weekday: a loss, a re-entry 5 minutes later (a revenge trade), two wins."""
    day = _last_weekday(now)
    at = lambda h, m: datetime(day.year, day.month, day.day, h, m, tzinfo=ET)  # noqa: E731
    rows = [(at(9, 45), "BUY", 2.00), (at(10, 5), "SELL", 1.20), (at(10, 10), "BUY", 1.30), (at(10, 40), "SELL", 1.00),
            (at(11, 30), "BUY", 1.00), (at(12, 0), "SELL", 1.60), (at(14, 10), "BUY", 1.10), (at(15, 0), "SELL", 1.50)]
    return {f"f{i}": _fill(i, when, side, price) for i, (when, side, price) in enumerate(rows, 1)}


def _screen(doc):
    out = subprocess.run([NODE, "--input-type=module", "-e", SCRIPT, str(FRONTEND)], input=json.dumps(doc),
                         capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@pytest.fixture(autouse=True, scope="module")
def _owner():
    seed_user(OWNER, role="admin")


@pytest.fixture
def stored(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "insights"))
    monkeypatch.setattr(api_server, "_my_insights_artifacts", lambda: tmp_path / "artifacts")
    monkeypatch.setattr(market_events, "read", lambda now=None: {
        "items": [{"at": "2026-11-13T08:30:00-05:00", "date": "2026-11-13", "title": "CPI", "kind": "economic", "impact": "high"}],
        "note": "", "refreshedAt": None, "source": "test"})
    return tmp_path


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_the_screen_reads_the_real_reply(stored):
    now = datetime.now(ET)
    schwab_history.save_store({"fills": _fills(now)})
    schwab_history.save_state({"lastReadAt": now.replace(hour=16, minute=31, second=0, microsecond=0).isoformat(timespec="seconds"),
                               "lastReadDay": now.date().isoformat()})
    with running_api() as api:
        status, doc = api.get("/api/my-insights?days=30", headers=ADMIN)
    assert status == 200
    screen = _screen(doc)
    s = doc["summary"]
    assert s["trades"] == 4
    assert screen["has"] is True
    assert screen["tiles"]["trades"] == "4"
    assert screen["tiles"]["pnl"] not in ("–", "$0"), "the P/L tile must show the server's number"
    assert screen["tiles"]["winRate"] == "50%"
    assert screen["dim"] is True, "4 trades: the averages are greyed as too few to judge"
    assert screen["period"] == doc["period"]["label"]
    revenge = next(h for h in screen["habits"] if h["key"] == "revenge")
    assert revenge["count"] == 1 and revenge["trades"] == ["SPY 12-31 700C"]
    assert [label for label, _ in screen["tables"]["callPut"]] and sum(n for _, n in screen["tables"]["callPut"]) == 4
    assert sum(n for _, n in screen["tables"]["timeOfDay"]) == 4
    assert screen["schwab"]["title"].startswith("Last read from Schwab at ")
    assert screen["events"]["items"][0]["title"] == "CPI"
    assert screen["execution"] is not None
    assert "you should" not in json.dumps(screen).lower()


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_nothing_read_yet_and_a_stopped_read(stored):
    now = datetime.now(ET)
    with running_api() as api:
        status, doc = api.get("/api/my-insights?days=7", headers=ADMIN)
    assert status == 200
    screen = _screen(doc)
    assert screen["has"] is False
    assert screen["empty"].startswith("No trades read from Schwab yet")
    assert screen["schwab"]["title"] == "Not read from Schwab yet"

    schwab_history.save_state({"lastReadAt": (now - timedelta(days=1)).isoformat(timespec="seconds"), "stoppedDay": now.date().isoformat(),
                               "stoppedKind": "signin", "stoppedReason": "HTTP 401"})
    with running_api() as api:
        status, doc = api.get("/api/my-insights?days=7", headers=ADMIN)
    assert status == 200
    screen = _screen(doc)
    assert screen["schwab"]["title"] == "Schwab sign-in needed"
    assert screen["schwab"]["canRefresh"] is False


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_a_read_that_found_no_trades_says_so_not_not_read_yet(stored):
    """Real result 11-12: Schwab answered for account …1867 with no option trades. The page and the
    chat must say the read happened and found nothing, not tell the owner to wait for a first read."""
    now = datetime.now(ET)
    schwab_history.save_store({"fills": {}})
    schwab_history.save_state({"lastReadAt": "2026-11-12T01:09:00-05:00", "lastReadDay": now.date().isoformat(),
                               "accounts": ["…1867"], "firstDay": None, "throughDay": now.date().isoformat()})
    with running_api() as api:
        status, doc = api.get("/api/my-insights?days=90", headers=ADMIN)
    assert status == 200 and doc["available"] is False
    screen = _screen(doc)
    assert screen["has"] is False
    assert screen["empty"] == "Read from Schwab at 11-12 01:09 ET: no option trades found in the last 90 days on account …1867."
    assert screen["schwab"]["title"] == "Last read from Schwab at 11-12 01:09 ET"
    from momx import my_insights
    lines = my_insights.chat_lines(doc)
    assert lines == ["Read from Schwab at 11-12 01:09 ET: no option trades found in the last 90 days on account …1867."]
    assert "not been read" not in " ".join(lines)
