"""Integration harness (2026-11-04): the real api_server request handler on a
free port, spoken to over real HTTP.

A unit test calls one function. An integration test goes through the route:
auth gate, request parsing, the handler, the modules behind it and the JSON
that comes back - the path the screen actually uses. Use it like:

    from api_harness import running_api

    def test_something():
        with running_api() as api:
            status, body = api.get("/api/health")
            status, body = api.post("/api/some/route", {"key": "value"})

Safe by construction:
  * tests/conftest.py has already pointed the database, the scanner archives
    and the stream history at a scratch folder before this module loads.
  * Importing api_server builds its state but starts no scheduler or stream:
    those start only in api_server.main(), the serving process.
  * Anything that would reach Schwab, Alpaca or the broker must be replaced
    with a fake by the test (monkeypatch the function the route calls).
"""
from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

import api_server  # noqa: E402 - the env flags above must be set first

TEST_PASSWORD = "Test-Only-Password-1"   # satisfies the length/case/digit policy


def seed_auto_login_user() -> dict | None:
    """The scratch database has no users, so loopback auto-login would 401.
    Create the LOCAL_AUTO_LOGIN_EMAIL user once, exactly as it exists live."""
    email = str(os.getenv("LOCAL_AUTO_LOGIN_EMAIL", "") or "").strip()
    if not email:
        return None
    try:
        api_server.auth_service_instance()._create_user_record(
            email, TEST_PASSWORD, display_name="Test Trader", role="admin", must_change_password=False,
        )
    except Exception as exc:  # already there from an earlier test in this process
        if "already" not in str(exc).lower():
            raise
    return {"email": email}


class ApiClient:
    def __init__(self, base_url: str):
        self.base_url = base_url

    def request(self, method: str, path: str, payload=None, headers: dict | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            f"{self.base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json", **(headers or {})},
        )
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, _decode(response.read())
        except HTTPError as error:
            return error.code, _decode(error.read())

    def get(self, path: str, headers: dict | None = None):
        return self.request("GET", path, headers=headers)

    def post(self, path: str, payload=None, headers: dict | None = None):
        return self.request("POST", path, {} if payload is None else payload, headers=headers)


def _decode(raw: bytes):
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text


@contextmanager
def running_api(state=None):
    """Serve api_server.ApiHandler on 127.0.0.1:<free port> for one test.

    state: an optional stand-in for api_server.STATE (restored afterwards).
    """
    seed_auto_login_user()
    original = api_server.STATE
    if state is not None:
        api_server.STATE = state
    server = ThreadingHTTPServer(("127.0.0.1", 0), api_server.ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        api_server.STATE = original
