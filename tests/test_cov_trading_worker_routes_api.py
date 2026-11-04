"""Integration: the scanner worker's Bot-page reads over real HTTP.

Paper 3 / Paper 5 (Alpaca PAPER mirror), the post-trade learning team, the
shadow AI model report, /healthz, and the board route that carries the
Strategy V2 / V3 / Daily 2 stamps - all through the real momx_worker.Handler
(tests/worker_harness.py), with scratch folders and fake broker / AI clients.
Nothing here may reach Alpaca or an AI provider: the fakes raise if touched.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

import momx_worker
from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import ai_filter, paper_learning, paper_mirror, strategy
from momx.paper_learning import LearningTeam
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")
OWNER = "owner-cov-worker@example.com"
AT = "2026-10-28T10:05:00-04:00"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """Nothing from the live worker leaks in: no mirrors, no team, no live AI folder."""
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [])
    monkeypatch.setattr(paper_learning, "_TEAM", None)
    monkeypatch.setattr(ai_filter, "_dir", lambda: tmp_path / "momx_ai_filter")


def no_broker():
    raise AssertionError("a read of the paper book must never open a broker client")


def mirror(tmp_path, account: str, days: dict, load: str | None = None):
    folder = tmp_path / "mirror" / account
    folder.mkdir(parents=True)
    for day, rows in days.items():
        (folder / f"{day}.json").write_text(json.dumps({"day": day, "account": account, "rows": rows}), encoding="utf-8")
    m = paper_mirror.PaperMirror({}, folder, client_factory=no_broker, off=lambda: False, account=account,
                                 book=paper_mirror.ACCOUNTS[account][0], rules=paper_mirror.ACCOUNT_RULES[account],
                                 quotes_fetch=lambda symbols: {})
    if load:
        m._load(load)
    return m


def paper_row(buy_id, day, symbol, status="closed", pl=None, rule="go", side="bull", at=AT):
    return {"buyId": buy_id, "buyAt": f"{day}T10:00:00-04:00", "side": side, "rule": rule, "symbol": symbol,
            "at": at, "status": status, "pl": pl, "buyFill": 1.0, "qty": 3}


# ----------------------------------------------------------------- Paper 3 / 5

def test_both_paper_accounts_are_served_with_history_newest_first(tmp_path, monkeypatch):
    p3 = mirror(tmp_path, "paper3", {
        "2026-10-27": {"k1": paper_row("b1", "2026-10-27", "AAPL", pl=12.5)},
        "2026-10-28": {"k2": paper_row("b2", "2026-10-28", "NVDA", status="open")},
    }, load="2026-10-28")
    p5 = mirror(tmp_path, "paper5", {"2026-10-28": {"k3": paper_row("b3", "2026-10-28", "SPY", pl=-4.0)}}, load="2026-10-28")
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [p3, p5])
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/paper-mirror?days=2", headers=worker.as_admin(monkeypatch))
    assert status == 200, body
    docs = {doc["account"]: doc for doc in body["mirrors"]}
    assert list(docs) == ["paper3", "paper5"]
    paper3 = docs["paper3"]
    assert (paper3["snapshotSource"], paper3["reconciling"], paper3["enabled"]) == ("memory", False, True)
    assert [row["buyId"] for row in paper3["rows"]] == ["b2"]
    assert [day["day"] for day in paper3["history"]] == ["2026-10-28", "2026-10-27"]
    assert paper3["history"][1]["rows"][0]["pl"] == 12.5
    assert paper3["rules"]["orders"] == "limit at mid"
    assert docs["paper5"]["totals"]["closed"] == 1 and docs["paper5"]["totals"]["pl"] == -4.0


def test_while_the_broker_reconciles_the_read_answers_from_the_saved_day_file_at_once(tmp_path, monkeypatch):
    p3 = mirror(tmp_path, "paper3", {"2026-10-28": {"k2": paper_row("b2", "2026-10-28", "NVDA", pl=8.0)}}, load="2026-10-28")
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [p3])
    with running_worker(tmp_path, monkeypatch) as worker:
        admin = worker.as_admin(monkeypatch)
        with p3._lock:                                   # an order reconciliation holds the mirror
            started = time.monotonic()
            status, body = worker.get("/api/momx-scanner/paper-mirror?days=1", headers=admin)
            elapsed = time.monotonic() - started
    assert status == 200, body
    doc = body["mirrors"][0]
    assert elapsed < 1.0, f"the read waited {elapsed:.2f}s on the broker lock"
    assert (doc["snapshotSource"], doc["reconciling"]) == ("saved", True)
    assert [row["buyId"] for row in doc["rows"]] == ["b2"]
    assert doc["history"][0]["rows"][0]["pl"] == 8.0


def test_busy_with_no_saved_file_says_unavailable_instead_of_an_empty_account(tmp_path, monkeypatch):
    p5 = mirror(tmp_path, "paper5", {})
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [p5])
    with running_worker(tmp_path, monkeypatch) as worker:
        admin = worker.as_admin(monkeypatch)
        with p5._lock:
            status, body = worker.get("/api/momx-scanner/paper-mirror?days=1", headers=admin)
    assert status == 200
    doc = body["mirrors"][0]
    assert (doc["snapshotSource"], doc["rows"], doc["snapshotAt"]) == ("unavailable", [], None)


def test_the_api_proxy_turns_a_dead_worker_into_a_warming_body(monkeypatch):
    """Pins the shape the Bot page must treat as 'unavailable' (paperBotHistory.cov.test.js)."""
    seed_user(OWNER, role="admin")
    with running_api() as api:                            # no worker: the guard refuses :3010
        status, body = api.get("/api/momx-scanner/paper-mirror?days=2", headers={ACCESS_EMAIL_HEADER: OWNER})
    assert status == 200
    assert body["warming"] is True and body["rows"] == [] and "mirrors" not in body
    assert body["message"].startswith("MomX worker unavailable")


# ---------------------------------------------------------- learning team

class Agents:
    """A fake reviewer: answers with valid analysis, or waits on `gate` first."""

    def __init__(self, gate: threading.Event | None = None):
        self.gate = gate
        self.prompts = []

    def enabled(self):
        return True

    def request(self, prompt, parse):
        self.prompts.append(prompt)
        if self.gate is not None:
            self.gate.wait(10)
        return {**parse(json.dumps({"summary": "Loss after a late entry", "hypothesis": "Compare spread groups",
                                    "validation": "Chronological held-out days with fees"})), "provider": "ollama"}


class Book:
    def __init__(self, trades):
        self._trades = trades

    def trades(self):
        return [dict(t) for t in self._trades]


def closed_trade(**over):
    return {"symbol": "SPY", "side": "bull", "rule": "go", "at": "2026-10-30T10:00:00-04:00",
            "status": "closed", "entry": 1.1, "exitPrice": 0.8, **over}


def team(tmp_path, agents):
    no_hermes = SimpleNamespace(enabled=lambda: False)
    return LearningTeam({"bull": Book([closed_trade()])}, tmp_path / "learning", agents, hermes=no_hermes)


def test_before_the_team_starts_the_status_says_so_in_the_shape_the_page_accepts(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/paper-learning", headers=worker.as_admin(monkeypatch))
    assert status == 200
    assert body == {"enabled": False, "mode": "post_trade", "entryBlocking": False, "error": "Learning team not started"}


def test_a_reviewed_batch_reaches_the_page_as_counts_scoreboard_and_latest_discussion(tmp_path, monkeypatch):
    agents = Agents()
    learning = team(tmp_path, agents)
    learning.discover()
    assert learning.run_once()
    monkeypatch.setattr(paper_learning, "_TEAM", learning)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/paper-learning", headers=worker.as_admin(monkeypatch))
    assert status == 200, body
    assert (body["mode"], body["enabled"], body["entryBlocking"]) == ("post_trade", True, False)
    assert (body["reviewedTrades"], body["pendingTrades"], body["failedTrades"]) == (1, 0, 0)
    assert body["latest"]["critic"]["summary"] == "Loss after a late entry"
    assert isinstance(body["scoreboard"], dict) and body["experiments"] is None
    assert agents.prompts, "the fake reviewer was asked"


def test_a_review_waiting_on_its_ai_never_holds_up_the_status_read(tmp_path, monkeypatch):
    gate = threading.Event()
    learning = team(tmp_path, Agents(gate))
    learning.discover()
    review = threading.Thread(target=learning.run_once, daemon=True)
    review.start()
    monkeypatch.setattr(paper_learning, "_TEAM", learning)
    try:
        with running_worker(tmp_path, monkeypatch) as worker:
            admin = worker.as_admin(monkeypatch)
            started = time.monotonic()
            status, body = worker.get("/api/momx-scanner/paper-learning", headers=admin)
            elapsed = time.monotonic() - started
    finally:
        gate.set()
        review.join(timeout=20)
    assert status == 200 and elapsed < 1.0, f"status waited {elapsed:.2f}s behind a review"
    assert body["pendingTrades"] == 1 and body["reviewedTrades"] == 0


# ------------------------------------------------------------- AI model

def ai_folder(tmp_path):
    folder = tmp_path / "momx_ai_filter"
    (folder / "scores").mkdir(parents=True)
    (folder / "report.json").write_text(json.dumps({
        "auc": 0.57, "bySide": {"bull": {"auc": 0.58}, "bear": {"auc": 0.51}}, "trainDays": 20,
        "perDay": [{"day": "2026-10-27", "aucToDate": 0.55, "bullToDate": 0.56, "bearToDate": 0.5,
                    "trades": [{"symbol": "NOT-FOR-THE-PAGE"}], "rows": 999}],
    }), encoding="utf-8")
    (folder / "history.jsonl").write_text(
        json.dumps({"trainedAt": "2026-10-26T16:45", "auc": 0.53}) + "\n"
        + "not json\n"
        + json.dumps({"trainedAt": "2026-10-27T16:45", "auc": 0.55}) + "\n", encoding="utf-8")
    (folder / "scores" / "2026-10-28.json").write_text(json.dumps({
        f"bull|go|AAPL|{AT}": 0.71, f"bull|go|MSFT|{AT}": 0.31,
    }), encoding="utf-8")
    return folder


def test_the_ai_report_trims_per_day_lists_history_newest_first_and_splits_real_paper_fills(tmp_path, monkeypatch):
    ai_folder(tmp_path)
    p3 = mirror(tmp_path, "paper3", {"2026-10-28": {
        "a": paper_row("b1", "2026-10-28", "AAPL", pl=50.0),
        "m": paper_row("b2", "2026-10-28", "MSFT", pl=-20.0),
        "t": paper_row("b3", "2026-10-28", "TSLA", pl=10.0),
        "o": paper_row("b4", "2026-10-28", "AMD", status="open", pl=None),
    }})
    monkeypatch.setattr(paper_mirror, "_MIRRORS", [p3])
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/ai-report", headers=worker.as_admin(monkeypatch))
    assert status == 200, body
    assert body["latest"]["auc"] == 0.57
    assert body["latest"]["perDay"] == [{"day": "2026-10-27", "aucToDate": 0.55, "bullToDate": 0.56, "bearToDate": 0.5}]
    assert [h["trainedAt"] for h in body["history"]] == ["2026-10-27T16:45", "2026-10-26T16:45"]
    assert body["paper"] == {"paper3": {
        "aiPick": {"trades": 1, "wins": 1, "pl": 50.0},
        "notPick": {"trades": 1, "wins": 0, "pl": -20.0},
        "unscored": {"trades": 1, "wins": 1, "pl": 10.0},
    }}


def test_no_trained_model_yet_answers_empty_not_an_error(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/ai-report", headers=worker.as_admin(monkeypatch))
    assert status == 200
    assert body == {"latest": {"perDay": []}, "history": [], "paper": {}}


def test_a_broken_paper_mirror_never_breaks_the_ai_report(tmp_path, monkeypatch):
    ai_folder(tmp_path)

    def explode(days):
        raise OSError("disk unplugged")

    monkeypatch.setattr(paper_mirror, "_MIRRORS", [SimpleNamespace(account="paper3", history=explode)])
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/ai-report", headers=worker.as_admin(monkeypatch))
    assert status == 200
    assert body["latest"]["auc"] == 0.57 and body["paper"] == {}


# ----------------------------------------------------- boot / research routes

def test_healthz_answers_while_the_learning_team_start_is_still_blocked(tmp_path, monkeypatch):
    gate = threading.Event()
    started = threading.Event()

    def slow_start(books):
        started.set()
        gate.wait(10)

    monkeypatch.setattr(paper_learning, "start", slow_start)
    starter = momx_worker.start_learning_background({})
    try:
        assert started.wait(5)
        with running_worker(tmp_path, monkeypatch) as worker:
            begun = time.monotonic()
            status, body = worker.get("/healthz")
            elapsed = time.monotonic() - begun
    finally:
        gate.set()
        starter.join(timeout=10)
    assert (status, body) == (200, {"ok": True})
    assert elapsed < 1.0


def test_research_cannot_be_started_over_http(tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        admin = worker.as_admin(monkeypatch)
        post_status, post_body = worker.post("/api/momx-scanner/research/start", {}, headers=admin)
        get_status, _get_body = worker.get("/api/momx-scanner/research/start", headers=admin)
    assert (post_status, post_body) == (404, {"error": "no route /api/momx-scanner/research/start"})
    assert get_status == 404
    assert paper_learning._TEAM is None


# ------------------------------------------------------------ Strategy stamps

def strategy_row(symbol, signal_iso, last=100.0, rvol=("5m", "15m")):
    return {
        "symbol": symbol, "last": last, "pctChange": 1.0,
        "grade": {"letter": "A+", "checks": {"pushRvol": list(rvol), "sqzFired": ["D"]}},
        "m5": {"state": "extended"},
        "gradeFresh": {"firstToday": {"A+": {"at": signal_iso, "price": last}, "A": None, "B": None}},
    }


def test_the_board_route_serves_the_strategy_stamps_the_real_book_put_on_each_row(tmp_path, monkeypatch):
    now = datetime(2026, 10, 28, 9, 40, tzinfo=ET)
    book = strategy.StrategyBook(tmp_path / "strategy", chain_fetcher=lambda symbol: None, background=False)
    payload = {"rows": [strategy_row("ARM", now.isoformat(), last=140.0), strategy_row("MU", now.isoformat(), rvol=("5m",))],
               "rest": [], "generatedAt": now.isoformat(), "universe": ["ARM", "MU"]}
    book.apply("Mag7", payload, now)
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.seed_board("Mag7", payload)
        status, body = worker.get("/api/momx-scanner?list=Mag7")
    assert status == 200, body
    stamps = {row["symbol"]: row["strategy"] for row in body["rows"]}
    assert stamps["ARM"]["v2"] is True and stamps["ARM"]["v3"] is True
    assert stamps["ARM"]["daily2"] == {"rank": 1, "at": "2026-10-28T09:40:00-04:00", "price": 140.0}
    assert stamps["MU"]["v2"] is True and stamps["MU"]["daily2"] is None
    assert [entry["symbol"] for entry in body["strategyDaily2"]] == ["ARM"]
