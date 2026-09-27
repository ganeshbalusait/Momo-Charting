from __future__ import annotations

"""One Schwab client per user, built from their own keys.

Today there is one client per PROFILE for the whole server. This registry adds
one per (user, profile), built from the encrypted vault, so a user's requests
go out on their key and their connection status shows their own expiry date.

The invariant that matters: a client is never shared between users. Handing
user B a client built from user A's config would send B's requests on A's
quota while every badge in the UI claimed otherwise - undetectable from the
outside and exactly the failure this whole change exists to prevent.
"""

import threading

import pytest

from handler_source import route_handler

from user_schwab import UserSchwabClients


class FakeBaseSettings:
    redirect_uri = "https://127.0.0.1/"
    include_extended_hours = True
    timeout_seconds = 15
    client_id = "HOUSE_ID"
    client_secret = "HOUSE_SECRET"
    token_path = "artifacts/schwab_token.json"


class FakeClient:
    def __init__(self, config):
        self.config = config


class FakeVault:
    """Stands in for auth_service.get_provider_credentials."""

    def __init__(self, rows=None):
        self.rows = dict(rows or {})
        self.reads = []

    def __call__(self, user_id, provider):
        self.reads.append((user_id, provider))
        return dict(self.rows.get((user_id, provider), {}))


def _registry(tmp_path, vault, factory=None):
    calls = []

    def build(config):
        calls.append(config)
        return FakeClient(config)

    registry = UserSchwabClients(
        artifacts_dir=tmp_path,
        base_settings=FakeBaseSettings(),
        credentials_reader=vault,
        client_factory=factory or build,
    )
    registry.build_calls = calls
    return registry


ALICE = {"id": "alice", "role": "user", "isAdmin": False}
BOB = {"id": "bob", "role": "user", "isAdmin": False}
KEYS_A = {"client_id": "ALICE_ID", "client_secret": "ALICE_SECRET"}
KEYS_B = {"client_id": "BOB_ID", "client_secret": "BOB_SECRET"}


# --------------------------------------------------------------------------
# No keys means no client
# --------------------------------------------------------------------------


def test_a_user_without_their_own_keys_gets_no_client(tmp_path):
    registry = _registry(tmp_path, FakeVault())

    assert registry.for_user(ALICE) is None
    assert registry.build_calls == []


def test_a_half_filled_credential_is_not_a_client(tmp_path):
    vault = FakeVault({("alice", "schwab_market_data"): {"client_id": "ALICE_ID"}})

    assert _registry(tmp_path, vault).for_user(ALICE) is None


# --------------------------------------------------------------------------
# The user's own keys
# --------------------------------------------------------------------------


def test_the_client_is_built_from_the_users_own_keys(tmp_path):
    vault = FakeVault({("alice", "schwab_market_data"): KEYS_A})
    registry = _registry(tmp_path, vault)

    client = registry.for_user(ALICE)

    assert client is not None
    assert client.config.client_id == "ALICE_ID"
    assert client.config.client_secret == "ALICE_SECRET"


def test_each_user_gets_their_own_token_file(tmp_path):
    vault = FakeVault({
        ("alice", "schwab_market_data"): KEYS_A,
        ("bob", "schwab_market_data"): KEYS_B,
    })
    registry = _registry(tmp_path, vault)

    assert registry.for_user(ALICE).config.token_path != registry.for_user(BOB).config.token_path


def test_a_client_is_never_shared_between_users(tmp_path):
    vault = FakeVault({
        ("alice", "schwab_market_data"): KEYS_A,
        ("bob", "schwab_market_data"): KEYS_B,
    })
    registry = _registry(tmp_path, vault)

    alice = registry.for_user(ALICE)
    bob = registry.for_user(BOB)

    assert alice is not bob
    assert alice.config.client_id == "ALICE_ID"
    assert bob.config.client_id == "BOB_ID"


def test_the_two_profiles_do_not_share_a_client(tmp_path):
    vault = FakeVault({
        ("alice", "schwab_market_data"): KEYS_A,
        ("alice", "schwab_trading"): KEYS_B,
    })
    registry = _registry(tmp_path, vault)

    assert registry.for_user(ALICE, "market_data").config.client_id == "ALICE_ID"
    assert registry.for_user(ALICE, "trading").config.client_id == "BOB_ID"


# --------------------------------------------------------------------------
# Caching, and dropping it when it goes stale
# --------------------------------------------------------------------------


def test_the_client_is_reused_rather_than_rebuilt_per_request(tmp_path):
    vault = FakeVault({("alice", "schwab_market_data"): KEYS_A})
    registry = _registry(tmp_path, vault)

    first = registry.for_user(ALICE)
    for _ in range(20):
        assert registry.for_user(ALICE) is first

    assert len(registry.build_calls) == 1


def test_saving_new_keys_invalidates_the_cached_client(tmp_path):
    # Otherwise a user updates their key and keeps calling on the old one.
    vault = FakeVault({("alice", "schwab_market_data"): KEYS_A})
    registry = _registry(tmp_path, vault)
    assert registry.for_user(ALICE).config.client_id == "ALICE_ID"

    vault.rows[("alice", "schwab_market_data")] = {"client_id": "NEW", "client_secret": "NEWSEC"}
    registry.invalidate("alice")

    assert registry.for_user(ALICE).config.client_id == "NEW"


def test_invalidating_one_user_leaves_the_others_alone(tmp_path):
    vault = FakeVault({
        ("alice", "schwab_market_data"): KEYS_A,
        ("bob", "schwab_market_data"): KEYS_B,
    })
    registry = _registry(tmp_path, vault)
    alice, bob = registry.for_user(ALICE), registry.for_user(BOB)

    registry.invalidate("alice")

    assert registry.for_user(BOB) is bob
    assert registry.for_user(ALICE) is not alice


def test_invalidating_a_user_drops_both_their_profiles(tmp_path):
    vault = FakeVault({
        ("alice", "schwab_market_data"): KEYS_A,
        ("alice", "schwab_trading"): KEYS_B,
    })
    registry = _registry(tmp_path, vault)
    md, trading = registry.for_user(ALICE, "market_data"), registry.for_user(ALICE, "trading")

    registry.invalidate("alice")

    assert registry.for_user(ALICE, "market_data") is not md
    assert registry.for_user(ALICE, "trading") is not trading


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------


def test_concurrent_first_requests_build_one_client(tmp_path):
    vault = FakeVault({("alice", "schwab_market_data"): KEYS_A})
    registry = _registry(tmp_path, vault)
    seen = []
    start = threading.Event()

    def grab():
        start.wait(5)
        seen.append(registry.for_user(ALICE))

    threads = [threading.Thread(target=grab, daemon=True) for _ in range(8)]
    for t in threads:
        t.start()
    start.set()
    for t in threads:
        t.join(10)

    assert len(registry.build_calls) == 1, "eight requests built eight Schwab clients"
    assert len({id(c) for c in seen}) == 1


def test_a_broken_vault_read_is_not_a_crash(tmp_path):
    def explode(user_id, provider):
        raise RuntimeError("database is locked")

    registry = _registry(tmp_path, explode)

    assert registry.for_user(ALICE) is None


def test_a_user_with_no_id_gets_nothing(tmp_path):
    registry = _registry(tmp_path, FakeVault())

    for nobody in [None, {}, {"role": "user"}]:
        assert registry.for_user(nobody) is None


# --------------------------------------------------------------------------
# Wiring. Source-read: importing api_server boots every scheduler and opens
# the live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

API_SOURCE = (_Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")


def _handler(route: str) -> str:
    """Sliced to the handler's real end, never a byte count.

    A fixed window sent this very test red when a comment was added to the
    handler - a correct handler reading as a regression. See handler_source.
    """
    return route_handler(API_SOURCE, route)


def test_reads_may_fall_back_to_the_house_client_but_oauth_may_not() -> None:
    """The single most dangerous confusion in this change.

    A read falling back to the house client is fine - a user without their own
    app still needs charts. An OAuth completion falling back is not: it would
    write the user's freshly-minted token over the HOUSE token file, handing
    the server their Schwab identity and logging the owner out.
    """
    for route in ("/api/schwab/oauth/start", "/api/schwab/token"):
        handler = _handler(route)
        assert "_schwab_oauth_client" in handler, f"{route} must use the non-falling-back client"
        assert "_schwab_client_for_profile(" not in handler, (
            f"{route} must not reach for the house client directly"
        )
        assert "_schwab_client_for(" not in handler, (
            f"{route} must not use the read path, which falls back to the house client"
        )


def test_the_oauth_client_refuses_to_fall_back() -> None:
    start = API_SOURCE.find("def _schwab_oauth_client(")
    assert start != -1
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]
    # Admin -> house is intended; anyone else gets the vault client or None.
    assert "credential_target" in body
    assert "USER_SCHWAB_CLIENTS.for_user" in body


def test_schwab_status_reports_the_caller_not_the_server() -> None:
    handler = _handler("/api/schwab/status")
    assert "_schwab_status_payload(user=" in handler, (
        "the connection badge and expiry date must be the signed-in user's own"
    )


def test_saving_keys_drops_the_cached_client() -> None:
    start = API_SOURCE.find("    def _save_user_schwab_credentials(self")
    body = API_SOURCE[start : API_SOURCE.find("\n    def ", start + 10)]
    assert "USER_SCHWAB_CLIENTS.invalidate" in body, (
        "without this a user updates their key and keeps calling on the old one"
    )


def test_a_user_token_directory_is_created_before_the_client_is_built() -> None:
    start = API_SOURCE.find("def _build_user_schwab_client(")
    assert start != -1
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]
    assert "mkdir" in body, "schwab-py writes the token file and will not create its directory"


def test_the_live_feed_runs_on_the_callers_key_and_never_borrows_the_house() -> None:
    """The acceptance property, pinned in source.

    If a user's own Schwab app fails and the code quietly reissues on the house
    client, their price line keeps ticking and nothing looks wrong - while the
    owner's quota pays for it. That failure is invisible from the outside, so
    it has to be prevented structurally rather than noticed later.
    """
    start = API_SOURCE.find("def _live_chart_quote_rows(")
    assert start != -1, "_live_chart_quote_rows not found"
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]

    assert "user=None" in body, "the live feed must know who is asking"
    assert "USER_SCHWAB_CLIENTS.for_user" in body

    # The house path must be reachable ONLY when there is no personal client.
    personal_branch = body[body.index("if personal is not None:") : body.index("else:")]
    assert "_live_quote_client(" not in personal_branch, (
        "a user with their own key must never be served by the house client"
    )


def test_the_quote_cache_is_scoped_to_whose_key_fetched_it() -> None:
    start = API_SOURCE.find("def _live_chart_quote_rows(")
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]
    assert "key = (scope, tuple(symbols))" in body, (
        "a symbol-only cache key would let one user's quotes be answered by a "
        "fetch made on another user's key"
    )


def test_the_live_quote_route_passes_the_signed_in_user() -> None:
    # Window widened from 1100: the route gained an explanatory comment on the
    # symbol cap and the call slid past the old slice, so a correct handler
    # read as a regression. The assertion below is the property that matters -
    # the signed-in user reaches the quote path - and the span only has to be
    # wide enough to contain it.
    handler = _handler("/api/live-chart-quotes")
    assert "_live_chart_quote_rows(symbols, self._session_user())" in handler


def test_status_never_borrows_the_house_connection() -> None:
    """Reads may fall back; status may not.

    _schwab_client_for falls back to the house client so a user without their
    own app still gets charts. If status used it, a user who had connected
    nothing would be shown the OWNER's expiry date under a card headed "My
    Schwab APIs", with green lamps - which is what happened.
    """
    start = API_SOURCE.find("def _schwab_status_payload(")
    assert start != -1
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]

    assert "_schwab_oauth_client" in body, (
        "status must use the resolver that does NOT fall back to the house"
    )
    assert "_schwab_client_for(" not in body, (
        "status must not use the read resolver - it borrows the house client"
    )
    assert "unconfigured_status" in body, (
        "a user with no keys must be reported as not connected, not omitted"
    )


def test_the_quote_path_uses_the_same_rule_as_credential_writes() -> None:
    """The owner must not be treated as their own tenant.

    The owner has an alpaca_market_data vault row, and that row IS the house
    Alpaca key. Without this the quote path saw it as a personal key, found no
    personal SCHWAB, and served the owner Alpaca IEX instead of the house
    Schwab path - a quieter, thinner tape - while the rails still looked
    correct because another change backfilled the missing fields.
    """
    from handler_source import function_body

    body = function_body(API_SOURCE, "_live_chart_quote_rows", indent="")

    assert "serves_own_providers" in body, (
        "the read path must use the same house-vs-own rule as the write path"
    )
    # And the personal clients must not even be built for a house caller.
    assert "if own_providers else None" in body, (
        "a house caller must not construct personal clients at all"
    )
