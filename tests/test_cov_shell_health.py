"""Unit: the pieces behind the health endpoints (coverage 2026-11-04, group
"shell"): api_server's thread census and the gateway's JSON writer, whose
gzip threshold decides what the watchdog and the phone receive."""
from __future__ import annotations

import gzip
import io
import json
import threading

import pytest

import api_server
import gateway


@pytest.fixture
def named_threads():
    stop = threading.Event()
    names = ["oi-finder-chart-AAPL", "oi-finder-chart-NVDA", "oi-finder-chart-BRK.B",
             "Thread-412 (process_request_thread)", "Thread-77 (process_request_thread)"]
    threads = [threading.Thread(target=stop.wait, name=name, daemon=True) for name in names]
    for thread in threads:
        thread.start()
    yield names
    stop.set()
    for thread in threads:
        thread.join(timeout=2)


def test_the_census_groups_threads_by_name_prefix_and_reports_counts_only(named_threads):
    census = api_server._thread_census()
    assert census["total"] == threading.active_count()
    by_name = census["byName"]
    assert by_name["oi-finder-chart-*"] >= 3, "one bucket for every per-ticker chart thread"
    assert by_name["Thread-N (process_request_thread)"] >= 2, "numeric suffixes collapse"
    assert all(isinstance(count, int) for count in by_name.values())
    assert not any("AAPL" in key or "NVDA" in key or "412" in key for key in by_name)
    assert len(by_name) <= 12
    assert list(by_name.values()) == sorted(by_name.values(), reverse=True), "worst pile-up first"


def _gateway_reply(payload, accept_encoding=""):
    handler = gateway.GatewayHandler.__new__(gateway.GatewayHandler)
    handler.headers = {"Accept-Encoding": accept_encoding} if accept_encoding else {}
    handler.wfile = io.BytesIO()
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /api/gateway-health HTTP/1.1"
    handler.command = "GET"
    handler.client_address = ("127.0.0.1", 0)
    handler._send_json(gateway.HTTPStatus.OK, payload)
    head, _, body = handler.wfile.getvalue().partition(b"\r\n\r\n")
    headers = {}
    for line in head.decode("latin-1").split("\r\n")[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return headers, body


def test_a_small_body_is_never_gzipped():
    headers, body = _gateway_reply({"gateway": "ok"}, "gzip, deflate")
    assert "content-encoding" not in headers
    assert json.loads(body) == {"gateway": "ok"}
    assert headers["content-length"] == str(len(body))


def test_a_large_body_is_gzipped_only_when_the_client_offers_gzip():
    big = {"rows": ["x" * 100] * 120}            # ~12 KB of JSON, over the 8192-byte line
    zipped_headers, zipped = _gateway_reply(big, "gzip, deflate, br")
    plain_headers, plain = _gateway_reply(big)
    assert zipped_headers["content-encoding"] == "gzip"
    assert json.loads(gzip.decompress(zipped)) == big
    assert zipped_headers["content-length"] == str(len(zipped))
    assert "content-encoding" not in plain_headers and json.loads(plain) == big


def test_exactly_at_the_threshold_stays_plain():
    filler = "y" * (8192 - len(json.dumps({"p": ""}, separators=(",", ":"))))
    payload = {"p": filler}
    assert len(json.dumps(payload, separators=(",", ":"))) == 8192
    headers, _ = _gateway_reply(payload, "gzip")
    assert "content-encoding" not in headers
