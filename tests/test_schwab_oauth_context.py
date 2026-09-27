"""Two Schwab OAuth handshakes must not share one slot.

Until 2026-09-04 `_pending_auth_context` was a single class attribute, so
starting the Accounts & Trading login destroyed a Market Data login already in
progress. The paste-back then failed with schwab-py's raw
"Expecting value: line 1 column 1 (char 0)" - a JSON parse error standing in
for "your handshake is gone", which is what made it so hard to read.
"""
from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from data import schwab_client as sc  # noqa: E402


class _Context:
    """Stand-in for schwab_auth's AuthContext - only the URL is read."""

    def __init__(self, client_id):
        self.client_id = client_id
        self.authorization_url = f"https://api.schwabapi.com/v1/oauth/authorize?client_id={client_id}"


@pytest.fixture
def profiles(tmp_path, monkeypatch):
    """Two clients that write DIFFERENT token files, as the real profiles do."""
    monkeypatch.setattr(
        sc.schwab_auth, "get_auth_context", lambda api_key, callback_url, state=None: _Context(api_key)
    )
    sc.SchwabClient._pending_auth_contexts.clear()

    def build(name, key):
        config = replace(
            settings.schwab,
            client_id=key,
            client_secret=f"secret-{name}",
            token_path=str(tmp_path / f"{name}.json"),
        )
        return sc.SchwabClient(config)

    return build("market_data", "MARKETKEY"), build("trading", "TRADINGKEY")


def test_starting_the_second_profile_does_not_destroy_the_first(profiles):
    """The regression: the trader's Market Data login survived a Trading one."""
    market, trading = profiles
    market_url = market.begin_authorization()
    trading_url = trading.begin_authorization()

    assert "MARKETKEY" in market_url
    assert "TRADINGKEY" in trading_url

    held = sc.SchwabClient._pending_auth_contexts
    assert len(held) == 2, "each profile keeps its own handshake"
    assert {c.client_id for c in held.values()} == {"MARKETKEY", "TRADINGKEY"}

    # The market-data handshake is still the one market-data would exchange.
    assert held[market._auth_key].client_id == "MARKETKEY"
    assert held[trading._auth_key].client_id == "TRADINGKEY"


def test_a_profile_with_no_handshake_says_so_rather_than_using_another(profiles):
    """Exchanging on a profile that never started must NOT borrow the other's.

    Borrowing is how a Trading paste-back could have landed on the Market Data
    token file; the error is deliberately the plain-language one.
    """
    market, trading = profiles
    market.begin_authorization()

    with pytest.raises(RuntimeError, match="No Schwab authorization is waiting"):
        trading.exchange_authorization_response("https://127.0.0.1/?code=abc")


def test_restarting_the_same_profile_replaces_only_its_own(profiles):
    market, trading = profiles
    trading.begin_authorization()
    first = sc.SchwabClient._pending_auth_contexts[trading._auth_key]
    trading.begin_authorization()
    second = sc.SchwabClient._pending_auth_contexts[trading._auth_key]

    assert first is not second, "pressing Authenticate again starts a fresh handshake"
    assert market._auth_key not in sc.SchwabClient._pending_auth_contexts


def test_the_key_is_the_token_file_so_two_users_never_collide(tmp_path, monkeypatch):
    """Per-user clients differ only by token_path - that must be enough."""
    monkeypatch.setattr(
        sc.schwab_auth, "get_auth_context", lambda api_key, callback_url, state=None: _Context(api_key)
    )
    sc.SchwabClient._pending_auth_contexts.clear()
    same_app = dict(client_id="SHAREDKEY", client_secret="s")
    a = sc.SchwabClient(replace(settings.schwab, token_path=str(tmp_path / "user-a.json"), **same_app))
    b = sc.SchwabClient(replace(settings.schwab, token_path=str(tmp_path / "user-b.json"), **same_app))

    a.begin_authorization()
    b.begin_authorization()

    assert len(sc.SchwabClient._pending_auth_contexts) == 2
    assert a._auth_key != b._auth_key


def test_the_dead_auth_url_alias_is_gone():
    """`authorization_url()` looked like a getter and started a handshake."""
    assert not hasattr(sc.SchwabClient, "authorization_url")
