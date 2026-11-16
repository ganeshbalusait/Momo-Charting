"""Integration: the Ticker trader journal routes (2026-11-13) through the real api_server proxy to the real
worker handler, the real engine and the real journal module. Quotes, the saved chain and the AI are fakes;
the state files live in a scratch folder. Owner only."""
from __future__ import annotations

import json
from datetime import datetime

import pytest

from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import chat_ai, ticker_trader
from test_momx_ticker_trader import CALL, chain
from worker_harness import running_worker

OWNER = "owner-ticker-journal@example.com"
VIEWER = "viewer-ticker-journal@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
ROUTE = "/api/momx-scanner/ticker-trader"
REVIEW = "NVDA Call has 1 closed trades with net -$30.00."


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def trader(tmp_path, monkeypatch):
    """One closed NVDA Call trade (bought at the ask $1.10, stopped by him at the bid $1.00: -$30)."""
    clock = {"now": datetime(2026, 11, 13, 10, 0, 0, tzinfo=ticker_trader.ET)}
    quotes = {"NVDA": {"last": 100.0}, CALL: {"bid": 1.0, "ask": 1.1}}

    def fetch(symbols):
        at = clock["now"].timestamp() - 1
        return {"asOf": at, "quotes": {s: {**quotes[s], "quoteAt": at} for s in symbols if s in quotes}}

    made = ticker_trader.TickerTrader(tmp_path / "momx_ticker_trader", quotes_fetch=fetch, chain_fetch=lambda s: chain(),
                                      clock=lambda: clock["now"])
    made.start("NVDA", "CALL")
    made.tick()
    made.tick()
    made.stop("NVDA-CALL")
    made.tick()
    assert made.snapshot()["runs"][0]["trades"][0]["pl"] == -30.0
    monkeypatch.setattr(ticker_trader, "_TRADERS", [made])
    monkeypatch.setenv("AGX_CHAT_AI", "1")
    return made


@pytest.fixture
def fake_ai(monkeypatch):
    """The app's own AI entry point, replaced: nothing leaves the PC. Counts each ask."""
    calls = []

    def ask(system, user, schema):
        calls.append(json.loads(user))
        return {"ok": True, "data": {"summary": REVIEW}}

    monkeypatch.setattr(chat_ai, "_default_ask", ask)
    monkeypatch.setitem(chat_ai._REST, "until", 0.0)
    return calls


def test_owner_reads_the_journal_writes_a_note_and_gets_one_review_a_day(tmp_path, monkeypatch, trader, fake_ai):
    trade_id = trader.snapshot()["runs"][0]["trades"][0]["id"]
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            all_status, every = api.get(ROUTE + "/journal?id=all", headers=ADMIN)
            run_status, one = api.get(ROUTE + "/journal?id=NVDA-CALL", headers=ADMIN)
            missing = api.get(ROUTE + "/journal?id=AAPL-CALL", headers=ADMIN)
            note_status, note = api.post(ROUTE + "/note", {"id": trade_id, "text": "Stopped it myself\x07 too early."}, headers=ADMIN)
            long_note = api.post(ROUTE + "/note", {"id": trade_id, "text": "x" * 2001}, headers=ADMIN)
            wrong_trade = api.post(ROUTE + "/note", {"id": "NVDA-CALL-7-1", "text": "hi"}, headers=ADMIN)
            reviews = [api.post(ROUTE + "/review", {"id": "NVDA-CALL"}, headers=ADMIN) for _ in range(3)]
            _, after = api.get(ROUTE + "/journal?id=NVDA-CALL", headers=ADMIN)
    assert all_status == 200 and every["scope"] == "all" and every["stats"]["n"] == 1 and every["simulated"] is True
    assert run_status == 200 and one["label"] == "NVDA Call" and one["stats"]["netPnl"] == -30.0
    assert one["trades"][0]["outcome"] == "early" and one["equity"] == [{"id": trade_id, "at": one["trades"][0]["closedAt"],
                                                                        "pl": -30.0, "balance": 9970.0}]
    assert one["improve"][0]["judge"] == "Too few trades to judge" and one["review"] is None
    assert missing[0] == 404
    assert note_status == 200 and note["note"]["text"] == "Stopped it myself too early."
    assert long_note[0] == 400 and "at most 2,000 characters" in long_note[1]["error"]
    assert wrong_trade[0] == 404
    assert [r[0] for r in reviews] == [200, 200, 200] and len(fake_ai) == 1
    first, cached = reviews[0][1], reviews[2][1]
    assert first["status"] == "done" and first["review"]["text"] == REVIEW and first["cached"] is False
    assert cached["cached"] is True and cached["review"]["label"] == "AI review, can be wrong. Only you see this."
    assert after["review"]["text"] == REVIEW and after["trades"][0]["note"] == "Stopped it myself too early."
    saved = json.loads((tmp_path / "momx_ticker_trader" / "notes.json").read_text(encoding="utf-8"))
    assert saved[trade_id]["text"] == "Stopped it myself too early."
    kept = json.loads((tmp_path / "momx_ticker_trader" / "reviews.json").read_text(encoding="utf-8"))
    assert kept["day"] == "2026-11-13" and kept["entries"]["NVDA-CALL"]["status"] == "done"


def test_the_review_is_skipped_quietly_when_the_ai_is_off(tmp_path, monkeypatch, trader, fake_ai):
    monkeypatch.setenv("AGX_CHAT_AI", "0")
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            status, out = api.post(ROUTE + "/review", {"id": "all"}, headers=ADMIN)
    assert status == 200 and out == {"ok": True, "review": None, "status": "skipped", "why": "The AI is off.", "cached": False}
    assert fake_ai == []


def test_a_regular_user_gets_no_journal_no_notes_and_no_review(tmp_path, monkeypatch, trader, fake_ai):
    trade_id = trader.snapshot()["runs"][0]["trades"][0]["id"]
    with running_worker(tmp_path, monkeypatch) as worker:
        worker.as_admin(monkeypatch, email=OWNER)
        with running_api(worker_port=worker.port) as api:
            journal = api.get(ROUTE + "/journal?id=all", headers=USER)[0]
            note = api.post(ROUTE + "/note", {"id": trade_id, "text": "mine"}, headers=USER)[0]
            review = api.post(ROUTE + "/review", {"id": "all"}, headers=USER)[0]
        # The worker refuses by itself too (no admin header reaches it).
        worker_journal = worker.get(ROUTE + "/journal?id=all")[0]
        worker_note = worker.post(ROUTE + "/note", {"id": trade_id, "text": "mine"})[0]
        worker_review = worker.post(ROUTE + "/review", {"id": "all"})[0]
    assert (journal, note, review) == (403, 403, 403)
    assert (worker_journal, worker_note, worker_review) == (403, 403, 403)
    assert fake_ai == [] and not (tmp_path / "momx_ticker_trader" / "notes.json").exists()
