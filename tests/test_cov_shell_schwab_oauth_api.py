"""Integration: single Schwab logins (Authenticate Market Data / Optional
Trading), the pasted-callback completion and the renewal result the status
read reports (coverage 2026-11-04, group "shell"). Complements
tests/test_schwab_renew_both_api.py.

Schwab is a fake: clients hand out login links and accept codes without any
network call. The real callback port is never bound (the listener start is
faked) and no token path points at artifacts/.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService
import schwab_oauth_callback as cb
from user_schwab import UserSchwabClients

ADMIN = "admin-oauth@example.com"
MEMBER = "member-oauth@example.com"
AS_ADMIN_REMOTE = {**RELAYED, "X-AGX-Access-Email": ADMIN}
AS_MEMBER = {**RELAYED, "X-AGX-Access-Email": MEMBER}
_REAL_OAUTH_CLIENT = api_server._schwab_oauth_client   # captured before any test fakes it


class FakeOAuthClient:
    def __init__(self, profile: str, token_dir, configured: bool = True):
        self.profile = profile
        self.config = SimpleNamespace(
            client_id="app-key" if configured else "", client_secret="app-secret" if configured else "",
            redirect_uri="https://127.0.0.1", token_path=str(token_dir / f"house-{profile}.json"))
        self.received: list[str] = []

    def begin_authorization(self) -> str:
        return f"https://api.schwabapi.com/v1/oauth/authorize?client_id=app-key&state=STATE-{self.profile}"

    def exchange_authorization_response(self, url: str) -> dict:
        self.received.append(url)
        return {"hasAccessToken": True, "hasRefreshToken": True}

    def connection_status(self) -> dict:
        configured = bool(self.config.client_id)
        return {"configured": configured, "credentialsConfigured": configured, "hasRefreshToken": bool(self.received),
                "refreshTokenExpiresAt": None, "tokenPath": self.config.token_path}


@pytest.fixture
def schwab(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", ADMIN)          # loopback = the admin, on this PC
    service._create_user_record(MEMBER, TEST_PASSWORD, display_name="Member", role="user", must_change_password=False)
    cb._PENDING.clear()
    cb._RESULTS.clear()
    clients = {p: FakeOAuthClient(p, tmp_path) for p in ("market_data", "trading")}
    fake = SimpleNamespace(auth=service, clients=clients, listener_starts=[], tmp=tmp_path)
    monkeypatch.setattr(api_server, "_schwab_oauth_client",
                        lambda user, profile="market_data": fake.clients.get(profile))
    monkeypatch.setattr(api_server, "start_callback_listener_safely", lambda: fake.listener_starts.append(1) or True)
    monkeypatch.setattr(api_server, "_probe_credential_in_background", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "certificate_trusted", lambda: True)
    yield fake
    cb._PENDING.clear()
    cb._RESULTS.clear()


def test_authenticate_trading_starts_one_login_for_the_trading_app(schwab):
    with running_api() as api:
        status, body = api.post("/api/schwab/oauth/start?profile=trading")
    assert status == 200, body
    assert body["authorizationUrl"].endswith("state=STATE-trading")
    assert (body["callbackListening"], body["manualCallbackRequired"], body["redirectUri"]) == (True, False, "https://127.0.0.1")
    assert [entry["profile"] for entry in cb._PENDING.values()] == ["trading"]
    assert schwab.listener_starts == [1]


def test_an_unknown_profile_falls_back_to_market_data(schwab):
    with running_api() as api:
        status, body = api.post("/api/schwab/oauth/start?profile=bogus")
    assert status == 200
    assert body["authorizationUrl"].endswith("state=STATE-market_data")


def test_through_the_tunnel_the_login_is_pasted_and_nothing_waits_here(schwab):
    with running_api() as api:
        status, body = api.post("/api/schwab/oauth/start?profile=market_data", headers=AS_ADMIN_REMOTE)
    assert status == 200
    assert (body["callbackListening"], body["manualCallbackRequired"]) == (False, True)
    assert cb.pending_count() == 0 and schwab.listener_starts == []


def test_a_pasted_redirect_completes_the_login_and_stops_the_wait(schwab):
    pasted = "https://127.0.0.1/?code=PASTED-CODE&state=STATE-market_data"
    with running_api() as api:
        api.post("/api/schwab/oauth/start?profile=market_data")
        waiting = cb.pending_count()
        status, body = api.post("/api/schwab/token", {"profile": "market_data", "authorizationResponseUrl": pasted})
    assert waiting == 1
    assert status == 200, body
    assert (body["status"], body["hasAccessToken"], body["hasRefreshToken"]) == ("connected", True, True)
    assert schwab.clients["market_data"].received == [pasted]
    assert cb.pending_count() == 0, "a pasted login is not left waiting on the listener"


def test_an_empty_paste_is_refused_with_directions(schwab):
    with running_api() as api:
        status, body = api.post("/api/schwab/token", {"profile": "market_data", "authorizationResponseUrl": "  "})
    assert status == 400
    assert body["error"].startswith("Use the Re-authenticate button")
    assert schwab.clients["market_data"].received == []


def test_a_paste_without_saved_keys_is_refused(schwab):
    schwab.clients.pop("trading")
    with running_api() as api:
        status, body = api.post("/api/schwab/token", {"profile": "trading",
                                                      "authorizationResponseUrl": "https://127.0.0.1/?code=x&state=y"})
    assert (status, body) == (400, {"error": "Save your Schwab app key and secret first."})


def test_with_only_market_data_configured_renew_is_one_step(schwab):
    schwab.clients["trading"] = FakeOAuthClient("trading", schwab.tmp, configured=False)
    with running_api() as api:
        status, body = api.post("/api/schwab/oauth/start-both")
    assert status == 200
    assert body["profiles"] == ["market_data"]
    assert cb.pending_count() == 1


def test_the_status_read_reports_each_finished_renewal(schwab):
    with running_api() as api:
        api.post("/api/schwab/oauth/start-both")
        cb.handle_callback("/?code=C1&state=STATE-market_data")
        cb.handle_callback("/?code=C2&state=STATE-trading")
        status, body = api.get("/api/schwab/status")
    assert status == 200
    assert body["marketData"]["renewalResult"]["ok"] is True
    assert body["trading"]["renewalResult"]["ok"] is True


def test_a_members_login_writes_their_own_token_file_never_the_house_one(schwab, monkeypatch):
    """The REAL resolver: a member with saved keys gets a client whose token
    file sits under user_tokens/<their id>, never the house token."""
    member = schwab.auth.get_user_by_email(MEMBER)
    schwab.auth.save_provider_credentials(member, "schwab_market_data", {"client_id": "member-key", "client_secret": "member-secret"})
    built = []

    def factory(config):
        client = FakeOAuthClient("market_data", schwab.tmp)
        client.config = config
        built.append(client)
        return client

    users = UserSchwabClients(artifacts_dir=schwab.tmp / "artifacts", base_settings=api_server.settings.schwab,
                              credentials_reader=lambda uid, provider: schwab.auth.get_provider_credentials(uid, provider),
                              client_factory=factory)
    monkeypatch.setattr(api_server, "USER_SCHWAB_CLIENTS", users)
    monkeypatch.setattr(api_server, "_schwab_oauth_client", _REAL_OAUTH_CLIENT)
    pasted = "https://127.0.0.1/?code=M1&state=STATE-member"
    with running_api() as api:
        status, body = api.post("/api/schwab/token", {"profile": "market_data", "authorizationResponseUrl": pasted}, headers=AS_MEMBER)
    assert status == 200, body
    assert len(built) == 1 and built[0].received == [pasted]
    token_path = str(built[0].config.token_path).replace("\\", "/")
    assert f"user_tokens/{member['id']}" in token_path
    assert "schwab_token.json" not in token_path

