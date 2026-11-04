"""Integration: live candles over SSE and the REST quote fallback
(2026-11-04 coverage), through the real api_server handler and the real
gateway SSE hub over real sockets.

The Schwab/Alpaca streamers are fakes: FakeMarketStream stands in for
api_server.MARKET_STREAM (the SSE pump's only source) and FakeAlpaca for the
fallback socket the route arms. The SSE handler loops forever, so each test
reads what it needs from a raw socket, closes it, and waits for the handler to
release its subscription AND disarm the fallback BEFORE the fakes are taken
away - a handler left running would otherwise reach the real streamers.
"""
from __future__ import annotations

import json
import socket
import threading
import time

import pytest

import gateway
from api_harness import api_server, running_api

OWNER = "owner@example.com"
RECEIVED_AT = "2026-11-04T15:00:01Z"
FRAME_END = ('"receivedAt": "' + RECEIVED_AT + '"}\n\n').encode("ascii")


class FakeMarketStream:
    def __init__(self, latest: int = 0):
        self.latest = latest
        self.pending: list[dict] = []
        self.acquired: list[list[str]] = []
        self.released: list[list[str]] = []
        self.cursors: list[int] = []
        self.gate = threading.Event()          # events are held while the gate is shut
        self.gate.set()
        self.stop = threading.Event()
        self.lock = threading.Lock()
        self.active = 0
        self.alpaca = None

    def acquire_equities(self, symbols):
        with self.lock:
            self.acquired.append(list(symbols))
            self.active += 1
        return list(symbols)

    def release_equities(self, acquired):
        with self.lock:
            self.released.append(list(acquired))
            self.active -= 1

    def latest_sequence(self):
        return self.latest

    def wait_for_events(self, cursor, symbols, timeout=15.0):
        self.cursors.append(cursor)
        if self.stop.is_set():
            raise OSError("test finished")     # the handler treats this as a hang-up
        if self.gate.wait(0.05):
            with self.lock:
                batch, self.pending = self.pending, []
            if batch:
                return max(event["sequence"] for event in batch), batch
        time.sleep(0.05)
        return cursor, []

    def status(self):
        return {"connected": True}


class FakeAlpaca:
    def __init__(self):
        self.symbol_sets: list[set[str]] = []

    def set_symbols(self, symbols):
        self.symbol_sets.append(set(symbols))

    def idle(self) -> bool:
        return not self.symbol_sets or self.symbol_sets[-1] == set()


def settle(fake: FakeMarketStream, limit: float = 10.0) -> bool:
    """Every SSE handler released its symbols and disarmed the Alpaca fallback
    (the handler does the second after the first)."""
    deadline = time.monotonic() + limit
    while time.monotonic() < deadline:
        with fake.lock:
            released = fake.active <= 0
        if released and fake.alpaca.idle():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def stream(monkeypatch):
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
    fake = FakeMarketStream(latest=40)
    fake.alpaca = FakeAlpaca()
    monkeypatch.setattr(api_server, "MARKET_STREAM", fake)
    monkeypatch.setattr(api_server, "ALPACA_STREAM", fake.alpaca)
    yield fake
    # Hang up every handler while the fakes are still installed.
    fake.stop.set()
    assert settle(fake), "an SSE handler never released its symbols"


def open_sse(port: int, path: str, headers: dict | None = None) -> socket.socket:
    sock = socket.create_connection(("127.0.0.1", port), timeout=10)
    lines = [f"GET {path} HTTP/1.1", "Host: 127.0.0.1", "Accept: text/event-stream"]
    lines += [f"{name}: {value}" for name, value in (headers or {}).items()]
    sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode("ascii"))
    return sock


def read_until(sock: socket.socket, marker: bytes, limit: float = 10.0) -> bytes:
    buffer = b""
    deadline = time.monotonic() + limit
    while marker not in buffer:
        if time.monotonic() > deadline:
            raise AssertionError(f"never saw {marker!r} in {buffer[-400:]!r}")
        chunk = sock.recv(65_536)
        if not chunk:
            raise AssertionError(f"stream closed before {marker!r}: {buffer[-400:]!r}")
        buffer += chunk
    return buffer


def frames(raw: bytes) -> list[dict]:
    """Parse 'id/event/data' blocks the way the browser's EventSource does."""
    body = raw.split(b"\r\n\r\n", 1)[-1].decode("utf-8")
    parsed = []
    for block in body.split("\n\n"):
        fields = {}
        for line in block.splitlines():
            if line.startswith(":") or ": " not in line:
                continue
            name, value = line.split(": ", 1)
            fields[name] = value
        if "event" in fields:
            parsed.append({**fields, "data": json.loads(fields["data"])})
    return parsed


def equity(sequence: int, symbol: str, last: float) -> dict:
    return {"sequence": sequence, "type": "equity", "symbol": symbol,
            "data": {"symbol": symbol, "lastPrice": last, "chartTime": 1_793_800_800, "volume": 1_200},
            "receivedAt": RECEIVED_AT}


def port_of(api) -> int:
    return int(api.base_url.rsplit(":", 1)[1])


def test_one_equity_packet_is_framed_exactly_and_the_subscription_released_on_hang_up(stream):
    stream.pending = [equity(41, "AAPL", 230.25)]
    with running_api() as api:
        sock = open_sse(port_of(api), "/api/live-market-stream?symbols=aapl,%20SPY")
        raw = read_until(sock, FRAME_END)
        sock.close()
        assert settle(stream), "closing the tab must release the symbols"
    head = raw.split(b"\r\n\r\n", 1)[0]
    assert b" 200 " in head.split(b"\r\n", 1)[0]
    assert b"Content-Type: text/event-stream" in head and b"Cache-Control: no-store" in head
    assert b'id: 41\nevent: equity\ndata: {"symbol": "AAPL"' in raw
    [frame] = frames(raw)
    assert frame["id"] == "41" and frame["event"] == "equity"
    assert frame["data"] == {"symbol": "AAPL", "data": equity(41, "AAPL", 230.25)["data"], "receivedAt": RECEIVED_AT}
    assert stream.acquired == [["AAPL", "SPY"]] and stream.released == [["AAPL", "SPY"]]
    # The Alpaca fallback socket was armed for the tab and disarmed after it.
    assert stream.alpaca.symbol_sets[0] >= {"AAPL", "SPY"}
    assert stream.alpaca.symbol_sets[-1] == set()


def test_a_quiet_stream_sends_keepalives_and_stays_open(stream):
    with running_api() as api:
        sock = open_sse(port_of(api), "/api/live-market-stream?symbols=SPY")
        raw = read_until(sock, b": keepalive\n\n")
        raw += read_until(sock, b": keepalive\n\n")          # still open after the first
        sock.close()
        assert settle(stream)
    assert frames(raw) == []
    assert stream.cursors[0] == 40                          # starts at the newest event, no replay


@pytest.mark.parametrize(("last_event_id", "cursor"), [("33", 33), ("999", 40), ("junk", 40)])
def test_a_reconnect_resumes_from_last_event_id_never_past_the_newest(stream, last_event_id, cursor):
    with running_api() as api:
        sock = open_sse(port_of(api), "/api/live-market-stream?symbols=SPY", {"Last-Event-ID": last_event_id})
        read_until(sock, b": keepalive")
        sock.close()
        assert settle(stream)
    assert stream.cursors[0] == cursor


def test_rest_fallback_quotes_are_capped_deduped_and_carry_the_signed_in_user(stream, monkeypatch):
    calls = []

    def fake_rows(symbols, user=None):
        calls.append((list(symbols), (user or {}).get("email")))
        return [{"symbol": symbol, "lastPrice": 100.0} for symbol in symbols]

    monkeypatch.setattr(api_server, "_live_chart_quote_rows", fake_rows)
    asked = ",".join(["spy", "SPY", "%20aapl%20"] + [f"T{index}" for index in range(30)])
    with running_api() as api:
        status, body = api.get(f"/api/live-chart-quotes?symbols={asked}")
        empty_status, empty = api.get("/api/live-chart-quotes?symbols=")
    assert status == 200
    [(symbols, email)] = calls                               # the empty ask never reaches the source
    assert symbols == ["SPY", "AAPL"] + [f"T{index}" for index in range(22)]
    assert len(symbols) == 24 and email == OWNER
    assert [row["symbol"] for row in body["rows"]] == symbols
    assert (empty_status, empty) == (200, {"rows": []})


def test_the_gateway_hub_fans_one_upstream_feed_out_to_two_tabs(stream, monkeypatch):
    stream.gate.clear()                                      # hold the packet until both tabs listen
    hub = gateway.SseHub()
    monkeypatch.setattr(gateway, "SSE_HUB", hub)
    with running_api() as api:
        monkeypatch.setattr(gateway.GatewayHandler, "pipeline_base", api.base_url)
        server = gateway.GatewayServer(("127.0.0.1", 0), gateway.GatewayHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            port = server.server_address[1]
            first = open_sse(port, "/api/live-market-stream?symbols=AAPL,SPY")
            second = open_sse(port, "/api/live-market-stream?symbols=spy,aapl")
            for sock in (first, second):
                read_until(sock, b": gateway sse hub")
            deadline = time.monotonic() + 10
            while hub.stats["sseClients"] < 2 and time.monotonic() < deadline:
                time.sleep(0.02)
            with stream.lock:
                stream.pending = [equity(41, "SPY", 580.5)]
            stream.gate.set()
            got = [read_until(sock, FRAME_END) for sock in (first, second)]
            upstreams = hub.stats["sseUpstreams"]
            acquired = list(stream.acquired)
            first.close()
            second.close()
            # Let the hub notice both tabs are gone; its pump then hangs up on
            # the pipeline, which releases the one upstream subscription.
            key = gateway.sse_feed_key("/api/live-market-stream", {"symbols": ["AAPL,SPY"]})
            deadline = time.monotonic() + 15
            while (hub.stats["sseClients"] or hub.stats["sseUpstreams"]) and time.monotonic() < deadline:
                hub._broadcast(key, b": test over\n\n")
                time.sleep(0.1)
            assert hub.stats == {"sseClients": 0, "sseUpstreams": 0}
            assert settle(stream)
        finally:
            server.shutdown()
            server.server_close()
    assert upstreams == 1
    assert acquired == [["AAPL", "SPY"]], "two tabs with the same symbols must share one pipeline feed"
    for raw in got:
        assert b"id: 41\nevent: equity\n" in raw
        assert b'"lastPrice": 580.5' in raw
