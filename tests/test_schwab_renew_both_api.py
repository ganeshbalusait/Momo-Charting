"""Integration: "Renew both" (2026-11-04) through the real route over HTTP and
the real login catcher behind it. Schwab itself is a fake: the clients hand
out login links and accept codes without any network call."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from api_harness import api_server, running_api
import schwab_oauth_callback as cb

START = "/api/schwab/oauth/start-both"
RELAYED = {"X-AGX-Request-Origin": "relayed"}   # what the gateway stamps on a tunnel visitor


class FakeOAuthClient:
    def __init__(self, profile: str, configured: bool = True):
        self.profile = profile
        self.config = SimpleNamespace(
            client_id="app-key" if configured else "",
            client_secret="app-secret" if configured else "",
            redirect_uri="https://127.0.0.1",
            token_path=f"tokens/integration-{profile}.json",
        )
        self.received: list[str] = []

    def begin_authorization(self) -> str:
        return ("https://api.schwabapi.com/v1/oauth/authorize?client_id=app-key"
                f"&redirect_uri=https%3A%2F%2F127.0.0.1&state=STATE-{self.profile}")

    def exchange_authorization_response(self, url: str) -> dict:
        self.received.append(url)
        return {"hasAccessToken": True, "hasRefreshToken": True}


@pytest.fixture
def schwab(monkeypatch):
    cb._PENDING.clear()
    cb._RESULTS.clear()
    fake = SimpleNamespace(clients={p: FakeOAuthClient(p) for p in ("market_data", "trading")}, listener_starts=[])
    monkeypatch.setattr(api_server, "_schwab_oauth_client", lambda user, profile="market_data": fake.clients[profile])
    monkeypatch.setattr(api_server, "start_callback_listener_safely", lambda: fake.listener_starts.append(1) or True)
    yield fake
    cb._PENDING.clear()
    cb._RESULTS.clear()


def test_on_this_pc_one_click_queues_both_logins_and_the_catcher_finishes_each(schwab):
    with running_api() as api:
        status, body = api.post(START)
    assert status == 200, body
    assert body["profiles"] == ["market_data", "trading"]
    assert body["authorizationUrl"].endswith("state=STATE-market_data")
    assert (body["callbackListening"], body["manualCallbackRequired"]) == (True, False)
    assert schwab.listener_starts == [1] and cb.pending_count() == 2

    status, page, stop = cb.handle_callback("/?code=CODE1&state=STATE-market_data")
    assert (status, stop) == (200, False)
    assert "state=STATE-trading" in page                      # the same window walks on to step 2
    status, page, stop = cb.handle_callback("/?code=CODE2&state=STATE-trading")
    assert (status, stop) == (200, True)
    assert schwab.clients["market_data"].received == ["https://127.0.0.1/?code=CODE1&state=STATE-market_data"]
    assert schwab.clients["trading"].received == ["https://127.0.0.1/?code=CODE2&state=STATE-trading"]
    assert cb.renewal_result_for(schwab.clients["trading"])["ok"] is True


def test_through_the_tunnel_nothing_waits_on_this_pc_and_the_screen_says_paste(schwab, monkeypatch):
    monkeypatch.setattr(api_server.ApiHandler, "_require_session_user", lambda self: {"email": "remote@example.com"})
    with running_api() as api:
        status, body = api.post(START, headers=RELAYED)
    assert status == 200, body
    assert (body["callbackListening"], body["manualCallbackRequired"]) == (False, True)
    assert schwab.listener_starts == [] and cb.pending_count() == 0


def test_signed_out_through_the_tunnel_is_refused(schwab):
    with running_api() as api:
        status, _body = api.post(START, headers=RELAYED)
    assert status == 401
    assert cb.pending_count() == 0


def test_without_saved_keys_the_screen_is_told_to_save_them_first(schwab):
    schwab.clients.update({p: FakeOAuthClient(p, configured=False) for p in ("market_data", "trading")})
    with running_api() as api:
        status, body = api.post(START)
    assert status == 400
    assert body["error"] == "Save the Schwab app key and secret first."
    assert cb.pending_count() == 0
