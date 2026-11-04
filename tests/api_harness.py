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
  * While a test server runs, tests/live_guard.py refuses every connection
    that leaves the PC or reaches the live servers (fake what the route
    calls instead), and refuses every write under artifacts/, database/ or
    the live .env. The files below that routes are known to write are moved
    to a scratch folder for the test, so those routes work.
  * The scanner worker the proxy routes call (:3010) is never the live one:
    pass worker_port= from tests/worker_harness.py, or it is refused.
"""
from __future__ import annotations

import os
import tempfile
import threading
from contextlib import ExitStack, contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path

os.environ.setdefault("AUTO_START_BOT", "false")
os.environ.setdefault("ALPACA_STREAMING_ENABLED", "false")
os.environ.setdefault("ALPACA_TRADE_UPDATES_ENABLED", "false")

import api_server  # noqa: E402 - the env flags above must be set first
import chart_profile_store  # noqa: E402
from http_client import ApiClient  # noqa: E402,F401 - re-exported for tests
from live_guard import network_guard, write_guard  # noqa: E402
from momx import momo_alert  # noqa: E402

TEST_PASSWORD = "Test-Only-Password-1"   # satisfies the length/case/digit policy
RELAYED = {"X-AGX-Request-Origin": "relayed"}   # what the gateway stamps on a tunnel visitor


def seed_auto_login_user() -> dict | None:
    """The scratch database has no users, so loopback auto-login would 401.
    Create the LOCAL_AUTO_LOGIN_EMAIL user once, exactly as it exists live."""
    email = str(os.getenv("LOCAL_AUTO_LOGIN_EMAIL", "") or "").strip()
    if not email:
        return None
    seed_user(email, role="admin")
    return {"email": email}


def seed_user(email: str, role: str = "user", password: str = TEST_PASSWORD) -> str:
    """Create a scratch-database user (ignored if it already exists)."""
    try:
        api_server.auth_service_instance()._create_user_record(
            email, password, display_name="Test Trader", role=role, must_change_password=False,
        )
    except Exception as exc:  # already there from an earlier test in this process
        if "already" not in str(exc).lower():
            raise
    return email


@contextmanager
def _scratch_attributes(scratch: Path):
    """Point the live files that routes write at the scratch folder."""
    swaps = [
        (api_server, "ENV_PATH", scratch / ".env"),
        (api_server, "CREDENTIAL_HEALTH", api_server.CredentialHealth(path=scratch / "credential_health.json")),
        (api_server, "EARNINGS_MANUAL_IMPORT_PATH", scratch / "earnings_manual_imports.json"),
        (chart_profile_store, "ROOT", scratch / "user_chart_profiles"),
        (momo_alert, "_CONFIG_PATH", scratch / "momx_momo_alert.json"),
        (momo_alert, "_USERS_PATH", scratch / "momx_momo_alert_users.json"),
    ]
    env = {"MOMX_UNIVERSE_PATH": scratch / "momx_universe.json", "MOMX_INDUSTRY_CACHE_PATH": scratch / "momx_industry_cache.json"}
    (scratch / ".env").touch()
    saved = [(owner, name, getattr(owner, name)) for owner, name, _value in swaps]
    saved_env = {key: os.environ.get(key) for key in env}
    for owner, name, value in swaps:
        setattr(owner, name, value)
    os.environ.update({key: str(value) for key, value in env.items()})
    try:
        yield
    finally:
        for owner, name, value in saved:
            setattr(owner, name, value)
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


@contextmanager
def running_api(state=None, worker_port: int | None = None):
    """Serve api_server.ApiHandler on 127.0.0.1:<free port> for one test.

    state:       an optional stand-in for api_server.STATE (restored afterwards).
    worker_port: a running_worker() port; the scanner proxy routes reach it
                 instead of the live :3010.
    """
    seed_auto_login_user()
    original = api_server.STATE
    if state is not None:
        api_server.STATE = state
    with ExitStack() as stack:
        scratch = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix="agx-api-")))
        stack.enter_context(_scratch_attributes(scratch))
        server = ThreadingHTTPServer(("127.0.0.1", 0), api_server.ApiHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        stack.enter_context(network_guard({3010: worker_port} if worker_port else None))
        stack.enter_context(write_guard())
        try:
            yield ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
            api_server.STATE = original
