"""Integration: the MomX board's Spread column route,
GET /api/momx-scanner/option-spreads, served by the real momx_worker handler
(tests/worker_harness.py) and through api_server's proxy to it.

The book reads scratch chain files measured at a pinned Wednesday 11:30 ET
(the route reads the wall clock, so the module clock is pinned too). Any
attempt to build a Schwab client fails the test: the column must come only
from chains the app already saved.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from api_harness import running_api
from momx import option_spread
from test_cov_options_spread import at, book_with
from worker_harness import running_worker


@pytest.fixture
def spreads(monkeypatch, tmp_path):
    import data.schwab_client as schwab_client

    def no_broker(*_args, **_kwargs):
        raise AssertionError("the Spread column must never call Schwab")

    monkeypatch.setattr(schwab_client.SchwabClient, "__init__", no_broker)
    book = book_with(tmp_path)
    monkeypatch.setattr(option_spread, "_BOOK", book)
    monkeypatch.setattr(option_spread, "time", SimpleNamespace(time=lambda: at(11, 30), sleep=time.sleep))
    return book


def test_the_worker_route_returns_classes_for_the_requested_fresh_tickers(spreads, tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/option-spreads?symbols=AAA,BBB")
    assert status == 200, body
    assert list(body["spreads"]) == ["AAA"]                    # BBB's chain is an hour old: "-"
    assert body["spreads"]["AAA"]["cls"] == "Tight"
    assert (body["inWindow"], body["window"]) == (True, "10:00-15:30 ET")


def test_without_symbols_every_fresh_class_is_listed(spreads, tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/option-spreads")
    assert status == 200, body
    assert {symbol: entry["cls"] for symbol, entry in body["spreads"].items()} == {"AAA": "Tight", "CCC": "Wide"}


def test_the_browser_reaches_it_through_api_servers_proxy(spreads, tmp_path, monkeypatch):
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, proxied = api.get("/api/momx-scanner/option-spreads")
        _, direct = worker.get("/api/momx-scanner/option-spreads")
    assert status == 200, proxied
    assert proxied == direct
