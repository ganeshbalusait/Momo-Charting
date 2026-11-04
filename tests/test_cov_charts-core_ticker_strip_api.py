"""Integration: the ticker identity strip above every chart, GET /api/ticker-strip
over real HTTP (2026-11-04 coverage).

The strip shows the company name, last price and day move for the panel's
ticker. Schwab is a fake (api_server.SchwabClient), and a Schwab outage must
leave the strip with just the symbol - never a 500 that blanks the header.
"""
from __future__ import annotations

import pytest

from api_harness import RELAYED, api_server, running_api

OWNER = "owner@example.com"


class FakeSchwab:
    calls: list[list[str]] = []
    quotes: dict = {}
    fail = False

    def __init__(self, *args, **kwargs):
        pass

    def get_quotes(self, symbols):
        FakeSchwab.calls.append(list(symbols))
        if FakeSchwab.fail:
            raise RuntimeError("Schwab 503")
        return {symbol: FakeSchwab.quotes[symbol] for symbol in symbols if symbol in FakeSchwab.quotes}


@pytest.fixture(autouse=True)
def schwab(monkeypatch):
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
    FakeSchwab.calls = []
    FakeSchwab.fail = False
    FakeSchwab.quotes = {
        "NVDA": {"last_price": 191.25, "change": -2.5, "change_pct": -1.29, "close_price": 193.75,
                 "description": "NVIDIA CORP", "volume": 123},
        "AAPL": {"last_price": 230.1, "change": 1.1, "change_pct": 0.48, "close_price": 229.0,
                 "description": "APPLE INC"},
    }
    monkeypatch.setattr(api_server, "SchwabClient", FakeSchwab)
    return FakeSchwab


def test_the_strip_names_the_ticker_with_its_price_and_day_move(schwab):
    with running_api() as api:
        status, body = api.get("/api/ticker-strip?symbol=%20nvda%20")
    assert status == 200
    assert body == {"symbol": "NVDA", "lastPrice": 191.25, "change": -2.5, "changePct": -1.29,
                    "closePrice": 193.75, "name": "NVIDIA CORP"}
    assert schwab.calls == [["NVDA"]]


def test_switching_tickers_never_shows_the_previous_tickers_numbers(schwab):
    with running_api() as api:
        _, nvda = api.get("/api/ticker-strip?symbol=NVDA")
        _, aapl = api.get("/api/ticker-strip?symbol=AAPL")
    assert (nvda["symbol"], nvda["name"]) == ("NVDA", "NVIDIA CORP")
    assert (aapl["symbol"], aapl["name"], aapl["lastPrice"]) == ("AAPL", "APPLE INC", 230.1)


def test_a_schwab_outage_leaves_just_the_symbol(schwab):
    schwab.fail = True
    with running_api() as api:
        status, body = api.get("/api/ticker-strip?symbol=NVDA")
    assert (status, body) == (200, {"symbol": "NVDA"})


def test_an_unknown_ticker_shows_empty_fields_not_an_error(schwab):
    with running_api() as api:
        status, body = api.get("/api/ticker-strip?symbol=ZZTOP")
    assert status == 200
    assert body["symbol"] == "ZZTOP" and body["lastPrice"] is None and body["name"] == ""


def test_no_symbol_makes_no_broker_call(schwab):
    with running_api() as api:
        status, body = api.get("/api/ticker-strip")
    assert (status, body) == (200, {"symbol": ""})
    assert schwab.calls == []


def test_a_tunnel_visitor_without_a_session_gets_no_quote(schwab):
    with running_api() as api:
        status, _ = api.get("/api/ticker-strip?symbol=NVDA", headers=RELAYED)
    assert status == 401
    assert schwab.calls == []
