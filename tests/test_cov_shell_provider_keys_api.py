"""Integration: MY DATA PROVIDER KEYS (Alpaca, Tradier) through the real
/api/user/api-keys routes over HTTP (coverage 2026-11-04, group "shell").

Saving makes no Alpaca or Tradier call. Each user acts through the tunnel with
a Cloudflare-verified address, so the keys land in their own encrypted vault.
"""
from __future__ import annotations

import json

import pytest
from cryptography.fernet import Fernet

from api_harness import RELAYED, TEST_PASSWORD, api_server, running_api
from auth_service import AuthService

ALICE = "alice-keys@example.com"
BOB = "bob-keys@example.com"
AS_ALICE = {**RELAYED, "X-AGX-Access-Email": ALICE}
AS_BOB = {**RELAYED, "X-AGX-Access-Email": BOB}
KEY_ID = "PKCOVTEST1234567890"
SECRET = "cov-secret-value-abcdef-123456"
ROUTE = "/api/user/api-keys"


@pytest.fixture
def auth(tmp_path, monkeypatch):
    service = AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())
    # Never let teardown restore the singleton to None: a server thread from the
    # previous test calling auth_service_instance() in that gap would build a new
    # service and could overwrite this one mid-test (seen 1 in ~30 runs).
    monkeypatch.setenv("USER_CREDENTIALS_ENCRYPTION_KEY", Fernet.generate_key().decode())
    api_server.auth_service_instance()  # pinned
    monkeypatch.setattr(api_server, "_AUTH_SERVICE", service)
    monkeypatch.delenv("LOCAL_AUTO_LOGIN_EMAIL", raising=False)
    monkeypatch.setattr(api_server.STATE, "_auth_status_cache", {}, raising=False)
    for email in (ALICE, BOB):
        service._create_user_record(email, TEST_PASSWORD, display_name="User", role="user", must_change_password=False)
    return service


def test_alpaca_keys_round_trip_masked_and_never_echo_the_secret(auth):
    with running_api() as api:
        status, body = api.post(ROUTE, {"provider": "alpaca_market_data", "keyId": KEY_ID, "secretKey": SECRET}, headers=AS_ALICE)
        _, read_back = api.get(ROUTE, headers=AS_ALICE)
    assert status == 200, body
    assert body["alpaca"] == {"configured": True, "keyIdMasked": "PKCO...567890"}
    assert SECRET not in json.dumps(body) and KEY_ID not in json.dumps(body)
    assert read_back == body


def test_the_tradier_token_is_stored_and_shown_masked(auth):
    token = "tradier-cov-token-0123456789"
    with running_api() as api:
        status, body = api.post(ROUTE, {"provider": "tradier", "accessToken": token}, headers=AS_ALICE)
    assert status == 200
    assert body["tradier"] == {"configured": True, "tokenMasked": "trad...456789"}
    assert token not in json.dumps(body)


def test_an_unknown_provider_is_refused(auth):
    with running_api() as api:
        status, body = api.post(ROUTE, {"provider": "robinhood", "accessToken": "x"}, headers=AS_ALICE)
    assert (status, body) == (400, {"error": "Unsupported API provider."})


def test_keys_are_private_to_the_user_who_saved_them(auth):
    with running_api() as api:
        api.post(ROUTE, {"provider": "alpaca_market_data", "keyId": KEY_ID, "secretKey": SECRET}, headers=AS_ALICE)
        _, bobs = api.get(ROUTE, headers=AS_BOB)
    assert bobs["alpaca"] == {"configured": False, "keyIdMasked": ""}


def test_saving_drops_cached_clients_and_any_stored_refusal(auth, monkeypatch):
    alice_id = auth.get_user_by_email(ALICE)["id"]
    dropped = []
    monkeypatch.setattr(api_server.USER_ALPACA_CLIENTS, "invalidate", lambda uid, *a, **k: dropped.append(("alpaca", uid)))
    monkeypatch.setattr(api_server.USER_SCHWAB_CLIENTS, "invalidate", lambda uid, *a, **k: dropped.append(("schwab", uid)))
    monkeypatch.setattr(api_server.STATE, "_owner_alpaca_client_cache", object(), raising=False)
    with running_api() as api:
        health = api_server.CREDENTIAL_HEALTH            # the scratch copy running_api installed
        health.record_success(alice_id, "alpaca")
        health.record_success("house", "alpaca")
        status, _ = api.post(ROUTE, {"provider": "alpaca_market_data", "keyId": KEY_ID, "secretKey": SECRET}, headers=AS_ALICE)
        alice_verdict = health.status(alice_id, "alpaca")["accepted"]
        house_verdict = health.status("house", "alpaca")["accepted"]
        owner_cache = api_server.STATE._owner_alpaca_client_cache
    assert status == 200
    assert ("alpaca", alice_id) in dropped and ("schwab", alice_id) in dropped
    assert alice_verdict is None, "the old key's verdict must not haunt the new one"
    assert house_verdict is True, "only the saver's verdicts are forgotten"
    assert owner_cache is None


def test_a_blank_secret_on_resave_keeps_the_stored_one(auth):
    alice_id = auth.get_user_by_email(ALICE)["id"]
    with running_api() as api:
        api.post(ROUTE, {"provider": "alpaca_market_data", "keyId": KEY_ID, "secretKey": SECRET}, headers=AS_ALICE)
        status, body = api.post(ROUTE, {"provider": "alpaca_market_data", "keyId": "PKNEWKEY0000999999", "secretKey": ""},
                                headers=AS_ALICE)
    stored = auth.get_provider_credentials(alice_id, "alpaca_market_data")
    assert status == 200 and body["alpaca"]["configured"] is True
    assert stored == {"key_id": "PKNEWKEY0000999999", "secret_key": SECRET}


def test_signed_out_is_401_for_read_and_save(auth):
    with running_api() as api:
        read, _ = api.get(ROUTE, headers=RELAYED)
        save, body = api.post(ROUTE, {"provider": "tradier", "accessToken": "x"}, headers=RELAYED)
    assert read == 401
    assert (save, body) == (401, {"error": "Sign in to continue."})
