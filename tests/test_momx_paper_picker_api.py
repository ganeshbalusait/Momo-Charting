"""Integration: the AI bot over HTTP (2026-11-10).

A real PaperMirror for each account, the real AI picker with a fake AI, the
worker's paper-mirror route, and the Scorecard route's rules-against-AI
comparison. Fake broker; scratch folders; nothing leaves the PC.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import bot_desk, paper_mirror, paper_picker
from worker_harness import running_worker

OWNER = "owner-ai-bot@example.com"
VIEWER = "viewer-ai-bot@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
def _last_weekday_at_ten() -> datetime:
    """10:00 ET on today's date (or the Friday before a weekend): inside the first buy window,
    and inside the Scorecard's look-back whenever the suite runs."""
    at = datetime.now(paper_mirror.ET).replace(hour=10, minute=0, second=0, microsecond=0)
    while at.weekday() >= 5:
        at -= timedelta(days=1)
    return at


NOW = _last_weekday_at_ten()


class Broker:
    def __init__(self):
        self.credentials = SimpleNamespace(paper=True)
        self.orders = {}
        self.bought = []

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        if intent == "buy_to_open":
            self.bought.append(symbol[:3])
        self.orders[cid] = SimpleNamespace(id=cid, status="filled", filled_avg_price=limit, filled_qty=qty)

    def get_order_by_client_id(self, cid):
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.orders[oid].status = "canceled"


class Book:
    def __init__(self, trades):
        self.items = trades

    def trades(self):
        return [dict(t) for t in self.items]


def signal(symbol, seconds_old):
    at = (NOW - timedelta(seconds=seconds_old)).isoformat()
    return {"rule": "level2", "symbol": symbol, "at": at, "entryAt": at, "status": "open", "virtualOnly": True,
            "levelSignal": {"highestLevel": 2, "setupTypes": [{"key": "tagRvol", "text": "RVOL"}]},
            "contract": f"{symbol}261113C00100000", "eContract": f"{symbol}261113C00110000", "eStrike": 110.0, "eDelta": 0.18,
            "eEntry": 0.60, "eLastBid": 0.58, "eLastAsk": 0.62, "eStatus": "open", "spot": 100.0,
            "lastAt": (NOW - timedelta(seconds=10)).isoformat()}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def bots(tmp_path, monkeypatch):
    """Both accounts on the same three signals with ONE free slot each: the rules bot and the AI bot must differ."""
    base = tmp_path / "grade"
    monkeypatch.setattr(api_server, "_MOMX_BOT_DESK", {})
    monkeypatch.setattr(bot_desk, "EVALUATION_START", "2026-01-01")
    monkeypatch.setattr(paper_mirror, "MAX_OPEN", 1)
    trades = [signal("AAA", 5), signal("BBB", 20), signal("CCC", 40)]
    brokers, mirrors = {}, []
    for account in ("paper3", "paper5"):
        brokers[account] = Broker()
        mirror = paper_mirror.PaperMirror({"bull": Book(trades)}, base / "momx_paper_mirror" / account,
                                          client_factory=lambda a=account: brokers[a], off=lambda: False, account=account,
                                          jev_cut=None, rules=paper_mirror.ACCOUNT_RULES[account], leg=paper_mirror.PAPER_LEG,
                                          book=paper_mirror.ACCOUNTS[account][0])
        mirror._load(NOW.date().isoformat())
        mirrors.append(mirror)
    ai = lambda system, user: {"ok": True, "provider": "openai", "data": {"ranking": [
        {"id": 3, "why": "strongest move"}, {"id": 2, "why": "second"}, {"id": 1, "why": "weakest"}]}}
    picker = paper_picker.Picker(mirrors[1], ask=ai)
    mirrors[1].picker = picker
    picker.refresh(NOW)
    for mirror in mirrors:
        mirror.tick(NOW + timedelta(seconds=1))
        mirror._save()
    monkeypatch.setattr(paper_mirror, "_MIRRORS", mirrors)
    return SimpleNamespace(base=base, brokers=brokers, mirrors=mirrors)


def test_the_rules_bot_and_the_ai_bot_choose_differently_and_the_worker_says_how(tmp_path, monkeypatch, bots):
    assert bots.brokers["paper3"].bought == ["AAA"]                  # newest signal
    assert bots.brokers["paper5"].bought == ["CCC"]                  # the AI's first choice
    with running_worker(tmp_path, monkeypatch) as worker:
        admin = worker.as_admin(monkeypatch, email=OWNER)
        status, body = worker.get("/api/momx-scanner/paper-mirror", headers=admin)
        refused, _ = worker.get("/api/momx-scanner/paper-mirror", headers={"X-AGX-User": VIEWER})
    assert status == 200 and refused == 403
    by = {m["account"]: m for m in body["mirrors"]}
    assert by["paper3"]["picker"] == {"on": False} and by["paper3"]["rules"]["book"].startswith("Rules bot")
    assert by["paper5"]["picker"]["on"] is True and by["paper5"]["picker"]["source"] == "openai" and by["paper5"]["picker"]["calls"] == 1
    assert by["paper5"]["rules"]["book"].startswith("AI bot")
    assert by["paper5"]["rows"][0]["aiPick"] == {"rank": 1, "of": 3, "why": "strongest move", "source": "openai"}
    assert "aiPick" not in by["paper3"]["rows"][0]


def test_the_scorecard_route_compares_the_two_bots(monkeypatch, bots):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(bots.base))
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot-desk?days=400", headers=ADMIN)
        refused, _ = api.get("/api/momx-scanner/bot-desk", headers={ACCESS_EMAIL_HEADER: VIEWER})
    assert status == 200 and refused == 403
    v = body["versus"]
    assert [(r["account"], r["name"], r["mode"], r["sent"]) for r in v["rows"]] == [("paper3", "Rules bot", "rules", 1), ("paper5", "AI bot", "ai", 1)]
    assert (v["ready"], v["leader"], v["need"], v["enough"], v["aiProven"], v["tooClose"]) == (False, None, 20, False, False, False)
    assert body["accounts"]["paper5"]["mode"]["name"] == "AI bot"
    assert body["accounts"]["paper5"]["aiPicks"]["picked"]["sent"] == 1 and body["accounts"]["paper5"]["aiPicks"]["fallback"]["sent"] == 0
    assert "aiPicks" not in body["accounts"]["paper3"]
