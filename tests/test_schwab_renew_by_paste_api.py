"""Integration: renewing Schwab by pasting from app.agxtrade.com (2026-11-04),
through the real routes over HTTP. One Renew both click begins both logins;
each pasted address finishes its own login, a paste in the wrong box or from an
older attempt is refused in plain words, and the status shows the full app key
(never the secret). Schwab itself is a fake: no network call."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from api_harness import RELAYED, api_server, running_api
import schwab_oauth_callback as cb

START = "/api/schwab/oauth/start-both"
TOKEN = "/api/schwab/token"
KEYS = {"market_data": "wvo0-FULL-MARKET-DATA-KEY-kOjMc5", "trading": "iERd-FULL-TRADING-KEY-FvNaS3"}


class FakeOAuthClient:
    """Like data.schwab_client.SchwabClient: each new login replaces the one
    this token file waits on; a code is exchanged only once."""

    def __init__(self, profile: str):
        self.profile = profile
        self.config = SimpleNamespace(client_id=KEYS[profile], client_secret=f"SECRET-{profile}",
                                      redirect_uri="https://127.0.0.1", token_path=f"tokens/paste-{profile}.json")
        self.attempt = 0
        self.waiting: str | None = None
        self.received: list[str] = []
        self.refuse_with: str | None = None

    def begin_authorization(self) -> str:
        self.attempt += 1
        self.waiting = f"{self.profile.upper()}-{self.attempt}"
        return f"https://api.schwabapi.com/v1/oauth/authorize?client_id={self.config.client_id}&state={self.waiting}"

    def pending_state(self):
        return self.waiting

    def exchange_authorization_response(self, url: str) -> dict:
        if self.refuse_with:
            raise RuntimeError(self.refuse_with)
        self.received.append(url)
        self.waiting = None
        return {"hasAccessToken": True, "hasRefreshToken": True}

    def connection_status(self) -> dict:
        return {"configured": True, "credentialsConfigured": True, "clientIdMasked": "masked",
                "hasRefreshToken": True, "refreshTokenExpiresAt": None, "tokenPath": self.config.token_path}


@pytest.fixture
def schwab(monkeypatch):
    cb._PENDING.clear()
    cb._RESULTS.clear()
    fake = SimpleNamespace(clients={p: FakeOAuthClient(p) for p in ("market_data", "trading")}, listener_starts=[])
    monkeypatch.setattr(api_server, "_schwab_oauth_client", lambda user, profile="market_data": fake.clients[profile])
    monkeypatch.setattr(api_server, "start_callback_listener_safely", lambda: fake.listener_starts.append(1) or True)
    monkeypatch.setattr(api_server, "_probe_credential_in_background", lambda *a, **k: None)
    monkeypatch.setattr(api_server, "certificate_trusted", lambda: True)
    # A visitor through the tunnel, signed in.
    monkeypatch.setattr(api_server.ApiHandler, "_require_session_user", lambda self: {"email": "owner@example.com"})
    yield fake
    cb._PENDING.clear()
    cb._RESULTS.clear()


def pasted(state: str, code: str = "C0.code%40") -> str:
    return f"https://127.0.0.1/?code={code}&session=s1&state={state}"


def test_from_agxtrade_renew_both_gives_both_links_and_each_paste_finishes_its_own_login(schwab):
    with running_api() as api:
        status, body = api.post(START, headers=RELAYED)
        assert status == 200, body
        assert (body["manualCallbackRequired"], body["callbackListening"]) == (True, False)
        assert body["authorizationUrls"] == {
            "market_data": body["authorizationUrl"],
            "trading": f"https://api.schwabapi.com/v1/oauth/authorize?client_id={KEYS['trading']}&state=TRADING-1",
        }
        assert schwab.listener_starts == [] and cb.pending_count() == 0   # nothing waits on this PC

        status, body = api.post(TOKEN, {"profile": "market_data", "authorizationResponseUrl": pasted("MARKET_DATA-1")}, headers=RELAYED)
        assert status == 200, body
        status, body = api.post(TOKEN, {"profile": "trading", "authorizationResponseUrl": pasted("TRADING-1")}, headers=RELAYED)
        assert status == 200, body
    assert schwab.clients["market_data"].received == [pasted("MARKET_DATA-1")]
    assert schwab.clients["trading"].received == [pasted("TRADING-1")]


def test_a_market_data_address_in_the_trading_box_is_refused_and_names_the_right_box(schwab):
    with running_api() as api:
        api.post(START, headers=RELAYED)
        status, body = api.post(TOKEN, {"profile": "trading", "authorizationResponseUrl": pasted("MARKET_DATA-1")}, headers=RELAYED)
        assert status == 400
        assert body["error"] == "This address is from the Market Data sign-in. Paste it into the Market Data callback URL box instead."
        # Nothing was spent: the same address still finishes Market Data.
        status, _ = api.post(TOKEN, {"profile": "market_data", "authorizationResponseUrl": pasted("MARKET_DATA-1")}, headers=RELAYED)
    assert status == 200
    assert schwab.clients["trading"].received == []


def test_an_address_from_an_earlier_click_is_refused_without_reaching_schwab(schwab):
    with running_api() as api:
        api.post(START, headers=RELAYED)
        api.post(START, headers=RELAYED)                         # clicked again: attempt 2 is the live one
        status, body = api.post(TOKEN, {"profile": "market_data", "authorizationResponseUrl": pasted("MARKET_DATA-1")}, headers=RELAYED)
    assert status == 400
    assert body["error"].startswith("This address is from an older Market Data sign-in.")
    assert schwab.clients["market_data"].received == []


def test_pasting_before_any_renew_says_to_click_renew_both(schwab):
    with running_api() as api:
        status, body = api.post(TOKEN, {"profile": "market_data", "authorizationResponseUrl": pasted("ANY")}, headers=RELAYED)
    assert status == 400
    assert body["error"].startswith("No Market Data sign-in is waiting. Click Renew both")


def test_schwab_refusing_the_code_is_explained_in_plain_words(schwab):
    schwab.clients["market_data"].refuse_with = "invalid_grant: authorization code expired"
    with running_api() as api:
        api.post(START, headers=RELAYED)
        status, body = api.post(TOKEN, {"profile": "market_data", "authorizationResponseUrl": pasted("MARKET_DATA-1")}, headers=RELAYED)
    assert status == 400
    assert body["error"].startswith("Schwab did not accept this address (")
    assert "Each address works once and for only about 30 seconds" in body["error"]


def test_the_status_shows_the_full_app_key_and_never_the_secret(schwab):
    with running_api() as api:
        status, body = api.get("/api/schwab/status", headers=RELAYED)
    assert status == 200, body
    assert body["marketData"]["clientId"] == KEYS["market_data"]
    assert body["trading"]["clientId"] == KEYS["trading"]
    assert "SECRET-" not in json.dumps(body)
