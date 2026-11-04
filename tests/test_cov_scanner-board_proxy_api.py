"""Integration: api_server's /api/momx-scanner* proxy to the scanner worker.

Every scanner request the browser makes goes through this block (gateway ->
api_server -> worker on :3010). Here api_server runs for real (running_api)
and :3010 is pointed at a small recording stand-in, so each test can see
exactly what the worker was sent and what the browser got back. The live
worker on this PC is never reached (tests/live_guard.py).
"""
from __future__ import annotations

import gzip
import json
import socket
import threading
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from api_harness import api_server, running_api

MEMBER = {"id": "u-member", "email": "trader@example.com", "role": "member", "isAdmin": False}
ADMIN = {"id": "u-admin", "email": "owner@example.com", "role": "admin", "isAdmin": True}


class FakeWorker:
    """Records every request; answers with whatever the test queued."""

    def __init__(self):
        self.seen: list[dict] = []
        self.reply = {"status": 200, "body": {"rows": [{"symbol": "SPY"}], "generatedAt": "g1"}, "gzip": False}
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                return

            def _answer(self, body_in=b""):
                owner.seen.append({
                    "method": self.command,
                    "path": self.path,
                    "headers": {k.lower(): v for k, v in self.headers.items()},
                    "body": body_in,
                })
                reply = owner.reply
                raw = json.dumps(reply["body"]).encode("utf-8")
                zipped = reply.get("gzip") and "gzip" in (self.headers.get("Accept-Encoding") or "")
                if zipped:
                    raw = gzip.compress(raw)
                self.send_response(reply["status"])
                self.send_header("Content-Type", "application/json")
                if zipped:
                    self.send_header("Content-Encoding", "gzip")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
                owner.last_raw = raw

            def do_GET(self):
                self._answer()

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                self._answer(self.rfile.read(length) if length else b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


def signed_in(monkeypatch, user):
    monkeypatch.setattr(api_server.ApiHandler, "_session_user", lambda self: dict(user))


@pytest.fixture
def worker():
    with FakeWorker() as fake:
        yield fake


def test_the_board_comes_back_byte_for_byte_with_gzip_relayed(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    worker.reply = {"status": 200, "body": {"rows": [{"symbol": "NVDA", "pad": "x" * 500}], "generatedAt": "g7"}, "gzip": True}
    with running_api(worker_port=worker.port) as api:
        request = urllib.request.Request(api.base_url + "/api/momx-scanner?list=Mag7", headers={"Accept-Encoding": "gzip"})
        with urllib.request.urlopen(request, timeout=20) as response:
            headers, raw = response.headers, response.read()
    assert worker.seen[0]["headers"].get("accept-encoding") == "gzip"
    assert raw == worker.last_raw                       # the worker's exact bytes
    assert headers["Content-Encoding"] == "gzip" and headers["Vary"] == "Accept-Encoding"
    assert json.loads(gzip.decompress(raw))["rows"][0]["symbol"] == "NVDA"


def test_without_a_gzip_offer_the_worker_is_not_asked_for_gzip(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    worker.reply["gzip"] = True
    with running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner?list=Mag7")
        headers = api.last_headers
    assert status == 200 and body["rows"] == [{"symbol": "SPY"}]
    assert "gzip" not in (worker.seen[0]["headers"].get("accept-encoding") or "")
    assert headers.get("Content-Encoding") is None


def test_the_worker_hears_who_is_signed_in_never_a_header_the_browser_made_up(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    with running_api(worker_port=worker.port) as api:
        api.get("/api/momx-scanner/momo-config", headers={"X-AGX-User": "attacker@example.com"})
        api.post("/api/momx-scanner/momo-config", {"sound": False}, headers={"X-AGX-User": "attacker@example.com"})
    assert [seen["headers"].get("x-agx-user") for seen in worker.seen] == ["trader@example.com"] * 2


def test_the_query_string_reaches_the_worker_unchanged(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    with running_api(worker_port=worker.port) as api:
        api.get("/api/momx-scanner?list=21%20EMA%20Touch%20Monthly&dir=bear")
        api.get("/api/momx-scanner/live-rvol?symbols=SPY,AAPL,NVDA")
    assert [seen["path"] for seen in worker.seen] == [
        "/api/momx-scanner?list=21%20EMA%20Touch%20Monthly&dir=bear",
        "/api/momx-scanner/live-rvol?symbols=SPY,AAPL,NVDA",
    ]


def test_a_post_carries_its_body_to_the_worker(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    worker.reply = {"status": 200, "body": {"ok": True, "added": ["AVGO"]}}
    with running_api(worker_port=worker.port) as api:
        status, body = api.post("/api/momx-scanner/universe/add", {"text": "avgo", "list": "Watchlist"})
    assert status == 200 and body == {"ok": True, "added": ["AVGO"]}
    assert worker.seen[0]["method"] == "POST" and worker.seen[0]["path"] == "/api/momx-scanner/universe/add"
    assert json.loads(worker.seen[0]["body"]) == {"text": "avgo", "list": "Watchlist"}


def _closed_port() -> int:
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


def test_a_dead_worker_reads_as_building_not_a_red_error(monkeypatch):
    signed_in(monkeypatch, MEMBER)
    with running_api(worker_port=_closed_port()) as api:
        status, body = api.get("/api/momx-scanner?list=Mag7")
        post_status, post_body = api.post("/api/momx-scanner/rebuild", {"list": "Mag7"})
    assert status == 200
    assert body["warming"] is True and body["rows"] == [] and body["lists"] == []
    assert body["message"].startswith("MomX worker unavailable")
    # A write that could not be delivered is NOT dressed up as success.
    assert post_status == 503, post_body


def test_worker_400s_stay_400s_and_a_post_keeps_the_workers_reason(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    reason = "Unknown list 'Gone'. Known lists: Mag7, Watchlist."
    worker.reply = {"status": 400, "body": {"error": reason}}
    with running_api(worker_port=worker.port) as api:
        get_status, _get_body = api.get("/api/momx-scanner?list=Gone")
        post_status, post_body = api.post("/api/momx-scanner/lists/delete", {"list": "Gone"})
    assert get_status == 400          # the panel drops the remembered tab on a 400
    assert post_status == 400 and post_body == {"error": reason}


def test_switching_the_data_source_needs_an_admin_and_never_reaches_the_worker_otherwise(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    with running_api(worker_port=worker.port) as api:
        status, body = api.post("/api/momx-scanner/data-source", {"source": "alpaca"})
    assert status == 403, body
    assert worker.seen == []


def test_an_admin_data_source_switch_is_passed_on_with_the_admin_named(worker, monkeypatch):
    signed_in(monkeypatch, ADMIN)
    worker.reply = {"status": 200, "body": {"source": "tos"}}
    with running_api(worker_port=worker.port) as api:
        status, body = api.post("/api/momx-scanner/data-source", {"source": "tos"})
    assert status == 200 and body == {"source": "tos"}
    assert worker.seen[0]["headers"]["x-agx-user"] == "owner@example.com"


def test_the_bot_page_is_answered_here_and_refused_to_a_member(worker, monkeypatch):
    signed_in(monkeypatch, MEMBER)
    with running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner/bot?days=5")
    assert status == 403 and body == {"error": "admin only"}
    assert worker.seen == []


def test_the_record_column_is_answered_here_with_days_clamped_to_30(worker, monkeypatch, tmp_path):
    signed_in(monkeypatch, MEMBER)
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path / "grade"))
    monkeypatch.setattr(api_server, "_MOMX_RULE_RECORD", {})
    with running_api(worker_port=worker.port) as api:
        status, body = api.get("/api/momx-scanner/rule-record?days=99")
        junk_status, junk = api.get("/api/momx-scanner/rule-record?days=lots")
    assert status == 200 and body["days"] == 30
    assert junk_status == 200 and junk["days"] == 10
    assert body["bull"] == {} and body["bear"] == {}          # no trades recorded in the scratch folder
    assert worker.seen == []


def test_signed_out_through_the_tunnel_nothing_is_proxied(worker):
    with running_api(worker_port=worker.port) as api:
        status, _body = api.get("/api/momx-scanner?list=Mag7", headers={"X-AGX-Request-Origin": "relayed"})
    assert status == 401
    assert worker.seen == []
