"""Integration: live option-chain ticks and live quotes, through the real
api_server routes over HTTP.

* /api/live-option-stream - the open chain leases its VISIBLE contracts onto
  the Schwab option stream.
* /api/live-market-stream - the one SSE pump that carries equity ticks (the
  live ATM price) and option ticks (the chain's Mark/Vol/OI) to the browser.
* /api/watchlist-quotes - the chain column's docked Watchlist table and the
  Auto Alert cards' live price.

The Schwab streamer is never started: the routes get either a recording fake
or a real SchwabMarketStream whose connect thread is switched off, fed one
Schwab LEVELONE_OPTIONS message by hand. Schwab quotes are a fake client.
"""
from __future__ import annotations

import threading
import time
from types import SimpleNamespace
from urllib.parse import quote
from urllib.request import Request, urlopen

import pytest

from api_harness import api_server, running_api
from schwab_stream import SchwabMarketStream

CALL = "AAPL  261120C00230000"   # Schwab's padded OCC key, exactly as the chain rows carry it
PUT = "AAPL  261120P00225000"


class RecordingStream:
    """The calls the routes make on MARKET_STREAM, nothing more."""

    def __init__(self, events=(), status=None):
        self.events = list(events)
        self.status_payload = status or {"connected": False, "optionSymbols": 0, "lastError": ""}
        self.watch_calls: list[tuple] = []
        self.acquired: list[tuple] = []
        self.released: list[tuple] = []
        self.waits = 0

    def watch(self, equity, options=(), replace_options=False):
        self.watch_calls.append((equity, list(options), replace_options))

    def status(self):
        return dict(self.status_payload)

    def acquire_equities(self, symbols):
        held = tuple(str(s).upper() for s in symbols)
        self.acquired.append(held)
        return held

    def release_equities(self, symbols):
        self.released.append(tuple(symbols))

    def latest_sequence(self):
        return 0

    def wait_for_events(self, cursor, symbols, timeout=15.0):
        self.waits += 1
        if self.waits == 1:
            return len(self.events), list(self.events)
        raise BrokenPipeError("the test has read what it needs")    # ends the pump cleanly


class QuietSchwabStream(SchwabMarketStream):
    """The real stream bookkeeping (leases, option->underlying map, event
    buffer) with the Schwab connection thread switched off. Its pump answers
    once and then ends, like a browser closing the tab."""

    def __init__(self, tmp_path):
        super().__init__(client_factory=self._no_schwab, chart_history_path=tmp_path / "stream_history.json.gz")
        self.waits = 0

    @staticmethod
    def _no_schwab():
        raise RuntimeError("no Schwab connection in tests")

    def start(self) -> None:
        return None

    def wait_for_events(self, after_sequence, symbols, timeout=15.0):
        self.waits += 1
        if self.waits > 1:
            raise BrokenPipeError("the test has read what it needs")
        return super().wait_for_events(after_sequence, symbols, timeout=0.5)


@pytest.fixture
def quiet_alpaca(monkeypatch):
    """The SSE route also drives the Alpaca fallback socket's symbol set."""
    calls: list[set] = []
    monkeypatch.setattr(api_server, "ALPACA_STREAM", SimpleNamespace(set_symbols=lambda active: calls.append(set(active))))
    return calls


def read_first_event(api, path: str, headers: dict | None = None) -> tuple[int, str, str]:
    """(status, content type, text of the first complete event block). The
    pump keeps the connection open like a real EventSource, so read one
    event and hang up - which is also how the route learns the browser left."""
    with urlopen(Request(api.base_url + path, headers=headers or {}), timeout=10) as response:
        lines: list[str] = []
        while True:
            line = response.readline().decode("utf-8")
            if not line:
                break
            if line.startswith(":"):
                continue                               # keepalive comment
            lines.append(line)
            if line == "\n" and any(item.startswith("data:") for item in lines):
                break
        return response.status, response.headers.get("Content-Type", ""), "".join(lines)


def wait_until(predicate, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def lease_path(symbol: str, *contracts: str) -> str:
    return f"/api/live-option-stream?symbol={quote(symbol)}&contracts={quote(','.join(contracts))}"


# --------------------------------------------------------------------------- the lease

def test_the_chain_leases_its_visible_contracts_and_replaces_the_previous_set(monkeypatch):
    stream = RecordingStream(status={"connected": False, "optionSymbols": 2,
                                     "lastError": "401 Unauthorized: trading token expired"})
    monkeypatch.setattr(api_server, "MARKET_STREAM", stream)
    with running_api() as api:
        status, body = api.get(lease_path("aapl", CALL.lower(), PUT))
    assert status == 200, body
    assert stream.watch_calls == [("AAPL", [CALL, PUT], True)]       # replace_options: never accumulates
    assert body == {
        "symbol": "AAPL", "requested": 2, "subscribed": 2, "streamConnected": False,
        # The browser shows the data-only notice from this when the trading token is dead.
        "streamError": "401 Unauthorized: trading token expired",
    }


def test_a_bad_ticker_lease_is_refused_and_nothing_is_subscribed(monkeypatch):
    stream = RecordingStream()
    monkeypatch.setattr(api_server, "MARKET_STREAM", stream)
    with running_api() as api:
        status, body = api.get(lease_path("bad!!", CALL))
    assert (status, body) == (400, {"error": "Enter a valid ticker symbol."})
    assert stream.watch_calls == []


# --------------------------------------------------------------------------- the SSE pump

def test_the_pump_sends_an_equity_tick_with_its_id_and_lets_the_symbol_go(monkeypatch, quiet_alpaca):
    tick = {"sequence": 41, "type": "equity", "symbol": "AAPL", "receivedAt": "2026-11-04T15:00:00+00:00",
            "data": {"last": 231.42, "bid": 231.4, "ask": 231.44}}
    stream = RecordingStream(events=[tick])
    monkeypatch.setattr(api_server, "MARKET_STREAM", stream)
    with running_api() as api:
        status, content_type, text = read_first_event(api, "/api/live-market-stream?symbols=aapl")
        assert wait_until(lambda: stream.released), "the pump never let the symbol go"
    assert (status, content_type) == (200, "text/event-stream")
    assert text.startswith("id: 41\nevent: equity\ndata: ")
    assert '"last": 231.42' in text and '"symbol": "AAPL"' in text
    assert stream.acquired == [("AAPL",)] and stream.released == [("AAPL",)]
    assert quiet_alpaca[0] == {"AAPL"} and quiet_alpaca[-1] == set()   # the fallback socket follows


def test_a_leased_contract_tick_reaches_the_browser_keyed_like_the_chain_row(monkeypatch, tmp_path, quiet_alpaca):
    """End to end inside api_server: lease the padded OCC key, let Schwab send
    a LEVELONE_OPTIONS packet for it, read it off the SSE pump. The packet's
    symbol must equal the chain row's symbol (that is what the browser merges
    on) and carry the underlying (that is how it is routed to the AAPL chain)."""
    stream = QuietSchwabStream(tmp_path)
    monkeypatch.setattr(api_server, "MARKET_STREAM", stream)
    with running_api() as api:
        status, body = api.get(lease_path("AAPL", CALL, PUT))
        assert (status, body["requested"], body["subscribed"]) == (200, 2, 2)
        stream._handle_option({"service": "LEVELONE_OPTIONS", "content": [{
            "key": CALL, "BID_PRICE": 4.05, "ASK_PRICE": 4.15, "MARK": 4.1, "LAST_PRICE": 4.08,
            "TOTAL_VOLUME": 1250, "OPEN_INTEREST": 9100, "DELTA": 0.52, "GAMMA": 0.031,
        }]})
        status, _type, text = read_first_event(api, "/api/live-market-stream?symbols=AAPL", {"Last-Event-ID": "0"})
        released = wait_until(lambda: stream._equity_leases.get("AAPL") is None)
    assert status == 200
    assert "event: option" in text, text
    assert f'"symbol": "{CALL}"' in text and '"underlying": "AAPL"' in text
    assert '"mark": 4.1' in text and '"openInterest": 9100.0' in text
    assert released, "the SSE lease is released after the browser leaves"


# --------------------------------------------------------------------------- through the gateway

@pytest.mark.xfail(strict=True, reason=(
    "gateway.py lists /api/live-option-stream in SSE_PATHS, so the chain's lease (a plain JSON "
    "GET) is answered as a never-ending event stream; every lease shares one hub key "
    "('?symbols=' is empty for it), so a later ticker's lease is never forwarded while the "
    "first is open, and the hub re-requests the first lease URL in a loop."))
def test_a_lease_through_the_gateway_is_answered_once_as_json(monkeypatch):
    import gateway

    stream = RecordingStream(status={"connected": True, "optionSymbols": 1, "lastError": ""})
    monkeypatch.setattr(api_server, "MARKET_STREAM", stream)
    monkeypatch.setattr(gateway, "SSE_HUB", gateway.SseHub())
    with running_api() as api:
        handler = type("TestGatewayHandler", (gateway.GatewayHandler,), {"pipeline_base": api.base_url})
        server = gateway.GatewayServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}{lease_path('AAPL', CALL)}"
            with urlopen(Request(url), timeout=5) as response:
                content_type = response.headers.get("Content-Type", "")
                time.sleep(1.0)
                leases_sent = len(stream.watch_calls)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
    assert content_type.startswith("application/json") and leases_sent == 1, (
        f"the gateway answered {content_type!r} and sent the lease to api_server {leases_sent} times in 1 s")


# --------------------------------------------------------------------------- live quotes

class FakeQuoteClient:
    calls: list[list[str]] = []
    fail = False

    def __init__(self, profile: str = ""):
        self.profile = profile

    def get_quotes(self, symbols):
        FakeQuoteClient.calls.append(list(symbols))
        if FakeQuoteClient.fail:
            raise RuntimeError("Schwab quotes 503")
        book = {"AAPL": (231.42, 1.12, 0.49, "APPLE INC"), "SPY": (571.3, -2.1, -0.37, "SPDR S&P 500")}
        return {symbol: {"last_price": book[symbol][0], "change": book[symbol][1], "change_pct": book[symbol][2],
                         "volume": 1_000_000, "description": book[symbol][3]} for symbol in symbols if symbol in book}


@pytest.fixture
def quotes(monkeypatch):
    FakeQuoteClient.calls = []
    FakeQuoteClient.fail = False
    monkeypatch.setattr(api_server, "SchwabClient", FakeQuoteClient)
    monkeypatch.setattr(api_server.STATE, "_watchlist_quote_cache", {}, raising=False)
    return FakeQuoteClient


def test_watchlist_quotes_round_trip_and_share_one_broker_call(quotes):
    with running_api() as api:
        status, body = api.get("/api/watchlist-quotes?symbols=aapl,SPY")
        _, again = api.get("/api/watchlist-quotes?symbols=AAPL,SPY")
    assert status == 200, body
    assert body["rows"] == [
        {"symbol": "AAPL", "lastPrice": 231.42, "change": 1.12, "changePct": 0.49, "volume": 1_000_000, "name": "APPLE INC"},
        {"symbol": "SPY", "lastPrice": 571.3, "change": -2.1, "changePct": -0.37, "volume": 1_000_000, "name": "SPDR S&P 500"},
    ]
    assert again == body
    assert quotes.calls == [["AAPL", "SPY"]], "the 8 s poll of every open panel costs one Schwab call per 5 s"


def test_a_failed_quote_read_is_not_cached(quotes):
    quotes.fail = True
    with running_api() as api:
        status, body = api.get("/api/watchlist-quotes?symbols=AAPL")
        quotes.fail = False
        _, recovered = api.get("/api/watchlist-quotes?symbols=AAPL")
    assert status == 200
    assert body["rows"][0]["symbol"] == "AAPL" and body["rows"][0]["lastPrice"] is None
    assert recovered["rows"][0]["lastPrice"] == 231.42, "a provider blip must not pin an empty table"
    assert len(quotes.calls) == 2
