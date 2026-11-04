"""Integration: the Movers list through the real scanner worker route.

POST /api/momx-scanner/movers/refresh runs one pass of the worker's own
refresh (the loop calls the same function every 5 minutes in the session);
afterwards the list picker's GET /lists shows "Movers" and its board route
answers. The Alpaca screener and asset lookup are fakes.
"""
from __future__ import annotations

import pytest

import momx_worker
from momx import movers, service
from worker_harness import running_worker

GAINERS = [
    {"symbol": "CHPT", "price": 9.03, "percent_change": 74.0},
    {"symbol": "TANH", "price": 0.36, "percent_change": 55.0},     # under the $3 floor
    {"symbol": "HOOG", "price": 30.0, "percent_change": 25.0},     # leveraged wrapper
    {"symbol": "AAPL", "price": 230.0, "percent_change": 5.0},
]


@pytest.fixture
def fake_screener(monkeypatch):
    calls = {"rebuild": []}
    monkeypatch.setattr(momx_worker, "_movers_credentials", lambda: ("key", "secret"))
    monkeypatch.setattr(movers, "fetch_gainers", lambda key, secret, **kw: list(GAINERS))
    monkeypatch.setattr(movers, "fetch_asset_names",
                        lambda symbols, key, secret, **kw: {"HOOG": "Leverage Shares 2X Long HOOD Daily ETF"})
    monkeypatch.setattr(service, "drop_no_options", lambda symbols: list(symbols))
    monkeypatch.setattr(service, "refresh_now", lambda name=None: calls["rebuild"].append(name))
    return calls


def test_a_refresh_puts_movers_in_the_picker_without_switching_his_tab(tmp_path, monkeypatch, fake_screener):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post("/api/momx-scanner/movers/refresh")
        lists_status, lists = worker.get("/api/momx-scanner/lists")
        board_status, board = worker.get("/api/momx-scanner?list=Movers")
        again = worker.post("/api/momx-scanner/movers/refresh")[1]
    assert status == 200 and body == {"ok": True, "wrote": True, "count": 2, "symbols": ["CHPT", "AAPL"]}
    by_name = {item["name"]: item for item in lists["lists"]}
    assert lists_status == 200 and by_name["Movers"]["count"] == 2
    assert by_name["Movers"]["builtIn"] is False
    assert lists["active"] == "Mag7"
    assert board_status == 200 and board["list"] == "Movers" and board["universe"] == ["CHPT", "AAPL"]
    assert again["reason"] == "unchanged" and fake_screener["rebuild"] == ["Movers"]


def test_a_screener_outage_answers_cleanly_and_creates_nothing(tmp_path, monkeypatch, fake_screener):
    monkeypatch.setattr(movers, "fetch_gainers", lambda key, secret, **kw: [])
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.post("/api/momx-scanner/movers/refresh")
        names = [item["name"] for item in worker.get("/api/momx-scanner/lists")[1]["lists"]]
    assert status == 200 and body == {"ok": False, "reason": "no gainers returned", "wrote": False}
    assert "Movers" not in names
