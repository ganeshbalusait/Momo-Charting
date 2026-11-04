"""Integration: gateway.py in front of a pipeline, over real HTTP (coverage
2026-11-04, group "shell").

The gateway runs on a free port (never :3001 or :3005) in front of a FAKE
pipeline that records exactly which headers and bodies reach it, so these
tests can prove what a tunnel visitor can and cannot smuggle through. One test
puts the gateway in front of the real api_server (running_api) to prove the
stamp is honoured end to end. Cloudflare Access is a locally generated RSA key.
"""
from __future__ import annotations

import base64
import gzip
import http.client
import json
import socket
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

import gateway
from api_harness import api_server, running_api
from auth_service import AuthService

TEAM = "https://cov-team.cloudflareaccess.com"
AUD = "cov-audience-0123456789abcdef"


# --------------------------------------------------------------------------- fake pipeline
class Pipeline:
    def __init__(self):
        self.seen = []                      # (method, path, headers{lower: value}, body)
        self.health_status = 200
        self.sse_release = threading.Event()
        self.sse_done = threading.Event()
        self.sse_connections = 0
        self.lock = threading.Lock()


def _pipeline_handler(pipe: Pipeline):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            return

        def _record(self, body=b""):
            with pipe.lock:
                pipe.seen.append((self.command, self.path, {k.lower(): v for k, v in self.headers.items()}, body))

        def _json(self, status, payload, extra=()):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            for name, value in extra:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            self._record()
            if self.path.startswith("/api/health"):
                return self._json(pipe.health_status, {"ok": pipe.health_status == 200})
            if self.path.startswith("/api/forbidden"):
                return self._json(403, {"error": "This device is waiting for admin approval.", "devicePending": True},
                                  [("Set-Cookie", "agentic_device=dev-token; Path=/; HttpOnly; SameSite=Lax")])
            if self.path.startswith("/api/live-market-stream"):
                with pipe.lock:
                    pipe.sse_connections += 1
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.end_headers()
                self.wfile.write(b"data: one\n\n")
                self.wfile.flush()
                pipe.sse_release.wait(10)
                self.wfile.write(b"data: two\n\n")
                self.wfile.flush()
                pipe.sse_done.wait(10)        # keep the stream open until the test ends (no reconnect storm)
                return
            return self._json(200, {"source": "pipeline", "path": self.path})

        def _mutation(self):
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length) if length else b""
            self._record(body)
            return self._json(200, {"method": self.command, "echo": body.decode("utf-8")})

        do_POST = do_PUT = do_DELETE = _mutation

    return Handler


@contextmanager
def _serve(server):
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


@contextmanager
def stack(pipeline_base=None):
    """A fake pipeline plus a gateway in front of it (or of pipeline_base)."""
    pipe = Pipeline()
    with _serve(ThreadingHTTPServer(("127.0.0.1", 0), _pipeline_handler(pipe))) as fake:
        base = pipeline_base or f"http://127.0.0.1:{fake.server_address[1]}"
        handler = type("CovGatewayHandler", (gateway.GatewayHandler,), {"pipeline_base": base})
        with _serve(gateway.GatewayServer(("127.0.0.1", 0), handler)) as front:
            try:
                yield SimpleNamespace(pipe=pipe, url=f"http://127.0.0.1:{front.server_address[1]}", port=front.server_address[1])
            finally:
                pipe.sse_release.set()
                pipe.sse_done.set()


def _get(url, headers=None, data=None, method=None):
    request = Request(url, data=data, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=15) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


def _last(pipe, path_prefix):
    matches = [entry for entry in pipe.seen if entry[1].startswith(path_prefix)]
    assert matches, f"the pipeline never saw {path_prefix}"
    return matches[-1]


@pytest.fixture(autouse=True)
def _isolated_gateway(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway, "SSE_HUB", gateway.SseHub())
    monkeypatch.setattr(gateway, "CHART_BLOBS", tmp_path / "chart_blobs")
    monkeypatch.setattr(gateway, "CHAIN_BLOBS", tmp_path / "chain_blobs")
    monkeypatch.setattr(gateway, "CF_ACCESS_AUD", "")
    monkeypatch.setattr(gateway, "CF_ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setattr(gateway, "ACCESS_KEYS", SimpleNamespace(keys=lambda: {"keys": []}))


# --------------------------------------------------------------------------- origin stamp
def test_the_trader_s_own_browser_is_stamped_local_and_host_is_not_forwarded():
    with stack() as s:
        status, _, _ = _get(f"{s.url}/api/echo")
        _, _, headers, _ = _last(s.pipe, "/api/echo")
    assert status == 200
    assert headers["x-agx-request-origin"] == "local"
    assert headers["host"] != f"127.0.0.1:{s.port}", "the client's Host must not reach the pipeline"


@pytest.mark.parametrize("tunnel_header", [
    {"Cf-Connecting-Ip": "203.0.113.9"},
    {"Forwarded": "for=203.0.113.9;proto=https"},
    {"X-Forwarded-For": "203.0.113.9"},
    {"Host": "app.agxtrade.com"},
])
def test_any_tunnel_sign_makes_the_request_relayed(tunnel_header):
    with stack() as s:
        _get(f"{s.url}/api/echo", tunnel_header)
        _, _, headers, _ = _last(s.pipe, "/api/echo")
    assert headers["x-agx-request-origin"] == "relayed"


def test_a_visitor_cannot_smuggle_the_local_stamp_or_an_email():
    smuggled = {"Cf-Connecting-Ip": "203.0.113.9", "X-AGX-Request-Origin": "local",
                "X-AGX-Access-Email": "owner@example.com", "Cookie": "agentic_session=abc"}
    with stack() as s:
        _get(f"{s.url}/api/echo", smuggled)
        _, _, headers, _ = _last(s.pipe, "/api/echo")
    assert headers["x-agx-request-origin"] == "relayed"
    assert "x-agx-access-email" not in headers
    assert "cf-connecting-ip" not in headers, "relay headers are not forwarded inward"
    assert headers["cookie"] == "agentic_session=abc", "allow-listed headers still pass"


def test_a_local_browser_cannot_downgrade_itself_to_relayed_either():
    with stack() as s:
        _get(f"{s.url}/api/echo", {"X-AGX-Request-Origin": "relayed"})
        _, _, headers, _ = _last(s.pipe, "/api/echo")
    assert headers["x-agx-request-origin"] == "local"


# --------------------------------------------------------------------------- Cloudflare Access
def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_int(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@pytest.fixture(scope="module")
def keys():
    good = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = good.public_key().public_numbers()
    jwks = {"keys": [{"kid": "k1", "kty": "RSA", "alg": "RS256", "n": _b64_int(numbers.n), "e": _b64_int(numbers.e)}]}
    return SimpleNamespace(good=good, other=other, jwks=jwks)


def _token(private_key, **claims):
    now = int(time.time())
    payload = {"iss": TEAM, "aud": [AUD], "email": "Member.Cov@Example.com", "exp": now + 3600, "iat": now - 10, "nbf": now - 10}
    payload.update(claims)
    header = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(payload).encode())
    signature = private_key.sign(f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64(signature)}"


@pytest.fixture
def access(keys, monkeypatch):
    monkeypatch.setattr(gateway, "CF_ACCESS_AUD", AUD)
    monkeypatch.setattr(gateway, "CF_ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(gateway, "ACCESS_KEYS", SimpleNamespace(keys=lambda: keys.jwks))
    return keys


def _email_seen(token):
    with stack() as s:
        _get(f"{s.url}/api/echo", {"Cf-Connecting-Ip": "203.0.113.9", "Cf-Access-Jwt-Assertion": token})
        _, _, headers, _ = _last(s.pipe, "/api/echo")
    assert "cf-access-jwt-assertion" not in headers, "the token itself never goes further"
    return headers.get("x-agx-access-email")


def test_a_valid_access_token_becomes_a_lower_cased_email(access):
    assert _email_seen(_token(access.good)) == "member.cov@example.com"


@pytest.mark.parametrize("make", [
    lambda k: _token(k.good, exp=int(time.time()) - 5),          # expired
    lambda k: _token(k.good, aud=["someone-elses-app"]),          # wrong audience
    lambda k: _token(k.other),                                    # signed by a key Cloudflare never published
    lambda k: _token(k.good, iss="https://evil.cloudflareaccess.com"),
    lambda k: "not-a-jwt",
])
def test_a_bad_access_token_yields_no_email(access, make):
    assert _email_seen(make(access)) is None


# --------------------------------------------------------------------------- relaying
def test_pipeline_errors_and_cookies_are_relayed_faithfully():
    with stack() as s:
        status, headers, body = _get(f"{s.url}/api/forbidden")
    assert status == 403
    assert json.loads(body) == {"error": "This device is waiting for admin approval.", "devicePending": True}
    assert headers.get_all("Set-Cookie") == ["agentic_device=dev-token; Path=/; HttpOnly; SameSite=Lax"]


def test_a_dead_pipeline_answers_warming_not_an_error_page():
    probe = socket.socket()
    probe.bind(("127.0.0.1", 0))
    closed_port = probe.getsockname()[1]
    probe.close()
    with stack(pipeline_base=f"http://127.0.0.1:{closed_port}") as s:
        status, _, body = _get(f"{s.url}/api/dashboard")
        _, _, health = _get(f"{s.url}/api/gateway-health")
    assert status == 200
    assert json.loads(body) == {"warming": True, "refreshing": True, "pipelineDown": True, "error": ""}
    assert json.loads(health)["pipeline"] == "down" and json.loads(health)["gateway"] == "ok"


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE"])
def test_mutation_bodies_reach_the_pipeline_byte_for_byte(method):
    raw = json.dumps({"ticker": "NVDA", "note": "café → ok"}).encode("utf-8")
    with stack() as s:
        status, _, body = _get(f"{s.url}/api/watchlist/NVDA", {"Content-Type": "application/json; charset=utf-8"},
                               data=raw, method=method)
        seen_method, _, headers, seen_body = _last(s.pipe, "/api/watchlist/NVDA")
    assert status == 200 and json.loads(body)["method"] == method
    assert (seen_method, seen_body) == (method, raw)
    assert headers["content-type"] == "application/json; charset=utf-8"


def test_gateway_health_follows_the_pipeline_health():
    with stack() as s:
        _, _, up = _get(f"{s.url}/api/gateway-health")
        s.pipe.health_status = 500
        _, _, down = _get(f"{s.url}/api/gateway-health")
    up, down = json.loads(up), json.loads(down)
    assert (up["gateway"], up["pipeline"]) == ("ok", "ok")
    assert (down["gateway"], down["pipeline"]) == ("ok", "down")
    assert {"blobs", "serves", "sse"} <= set(up)


# --------------------------------------------------------------------------- SSE fan-out
def _open_stream(port, symbols):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", f"/api/live-market-stream?symbols={symbols}")
    response = conn.getresponse()
    return conn, response


def _read_until(response, needle: bytes):
    seen = b""
    while needle not in seen:
        line = response.readline()
        if not line:
            break
        seen += line
    return seen


def test_one_upstream_feeds_every_browser_and_events_arrive_live():
    with stack() as s:
        first_conn, first = _open_stream(s.port, "SPY,AAPL")
        got_one = _read_until(first, b"data: one")            # before the upstream has finished
        second_conn, second = _open_stream(s.port, "aapl,SPY")  # same set, other order and case
        _read_until(second, b"gateway sse hub")
        connections = s.pipe.sse_connections
        s.pipe.sse_release.set()
        got_two_first = _read_until(first, b"data: two")
        got_two_second = _read_until(second, b"data: two")
        first_conn.close()
        second_conn.close()
    assert first.status == 200 and first.getheader("Content-Type") == "text/event-stream"
    assert b"data: one" in got_one
    assert b"data: two" in got_two_first and b"data: two" in got_two_second
    assert connections == 1, "two browsers on one symbol set share one pipeline stream"


# --------------------------------------------------------------------------- disk blobs
def _write_blob(directory, symbol, payload):
    directory.mkdir(parents=True, exist_ok=True)
    with gzip.open(directory / f"{symbol}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def test_a_fresh_chart_blob_is_served_without_waking_the_pipeline():
    blob = {"symbol": "SPY", "bars": [{"time": int(time.time()) - 60, "close": 500.0}], "from": "blob"}
    _write_blob(gateway.CHART_BLOBS, "SPY", blob)
    with stack() as s:
        status, _, body = _get(f"{s.url}/api/oi-finder-chart?symbol=SPY")
        served_without_pipeline = not [e for e in s.pipe.seen if e[1].startswith("/api/oi-finder-chart")]
        _, _, shaped = _get(f"{s.url}/api/oi-finder-chart?symbol=SPY&initial=1")
    assert status == 200 and json.loads(body) == blob
    assert served_without_pipeline
    assert json.loads(shaped)["source"] == "pipeline", "a shaped request always goes to the pipeline"


def test_a_stale_chart_blob_is_proxied():
    _write_blob(gateway.CHART_BLOBS, "SPY", {"bars": [{"time": int(time.time()) - 3 * 86400}]})
    with stack() as s:
        _, _, body = _get(f"{s.url}/api/oi-finder-chart?symbol=SPY")
    assert json.loads(body)["source"] == "pipeline"


# --------------------------------------------------------------------------- real api_server behind it
def test_in_front_of_the_real_api_the_stamp_decides_who_is_signed_in(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", "owner-gw@example.com")
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    with running_api() as api, stack(pipeline_base=api.base_url) as s:
        local, _, _ = _get(f"{s.url}/api/user/api-keys")
        remote, _, body = _get(f"{s.url}/api/user/api-keys", {"Cf-Connecting-Ip": "203.0.113.9"})
        spoof, _, _ = _get(f"{s.url}/api/user/api-keys", {"Cf-Connecting-Ip": "203.0.113.9", "X-AGX-Request-Origin": "local"})
    assert local == 200, "the trader's own browser is signed in through the gateway"
    assert (remote, json.loads(body)) == (401, {"error": "Sign in to continue."})
    assert spoof == 401
