from __future__ import annotations

"""Per-user Schwab identity: credentials from the vault, token per user.

Today one .env holds one Schwab key pair and one token file serves the whole
server, which is why the settings card had to be admin-only - a second person
saving keys there overwrites everyone's. (Proved the hard way on 2026-08-26,
when a test write of "EVIL" took market data down.)

SchwabClient already accepts a full config object with its own token_path, so
a per-user client needs no change to that class. This module builds that
config from the encrypted vault.

The safety property that matters most: building a user's config must NEVER
mutate the shared settings object. That mutation is exactly how the .env route
breaks everyone, and it is the thing being replaced.
"""

import re

import pytest

from handler_source import code_only

from user_schwab import (
    VAULT_PROVIDERS,
    schwab_settings_for_user,
    user_token_path,
    vault_provider_for,
)


class FakeBaseSettings:
    """Stands in for config.settings.schwab."""

    def __init__(self):
        self.client_id = "HOUSE_ID"
        self.client_secret = "HOUSE_SECRET"
        self.redirect_uri = "https://127.0.0.1/"
        self.token_path = "artifacts/schwab_token.json"
        self.include_extended_hours = True
        self.timeout_seconds = 15


@pytest.fixture
def base():
    return FakeBaseSettings()


# --------------------------------------------------------------------------
# Which vault slot
# --------------------------------------------------------------------------


def test_the_two_profiles_map_to_the_slots_the_vault_already_has():
    assert vault_provider_for("market_data") == "schwab_market_data"
    assert vault_provider_for("trading") == "schwab_trading"
    assert set(VAULT_PROVIDERS) == {"market_data", "trading"}


@pytest.mark.parametrize("junk", ["", None, "nonsense", "admin", "../../etc"])
def test_an_unknown_profile_is_refused(junk):
    with pytest.raises(ValueError):
        vault_provider_for(junk)


# --------------------------------------------------------------------------
# Where the token lives
# --------------------------------------------------------------------------


def test_each_user_and_profile_gets_its_own_token_file(tmp_path):
    a = user_token_path(tmp_path, "user-a", "market_data")
    b = user_token_path(tmp_path, "user-b", "market_data")
    a_trading = user_token_path(tmp_path, "user-a", "trading")

    assert a != b
    assert a != a_trading
    assert len({str(a), str(b), str(a_trading)}) == 3


def test_a_user_token_never_lands_on_the_shared_house_token(tmp_path):
    path = user_token_path(tmp_path, "user-a", "market_data")

    assert path.name != "schwab_token.json"
    assert path.name != "schwab_trading_token.json"


@pytest.mark.parametrize("hostile", ["../escape", r"..\escape", "a/b", r"a\b", "....//x"])
def test_a_crafted_user_id_cannot_escape_the_token_directory(tmp_path, hostile):
    # Ids are uuid4 from the database, so this is belt and braces - but a token
    # file written outside its directory could overwrite the house token.
    path = user_token_path(tmp_path, hostile, "market_data")

    assert tmp_path.resolve() in path.resolve().parents


def test_an_empty_user_id_is_refused(tmp_path):
    for blank in ["", None, "   "]:
        with pytest.raises(ValueError):
            user_token_path(tmp_path, blank, "market_data")


# --------------------------------------------------------------------------
# Building the config
# --------------------------------------------------------------------------


def test_a_users_own_keys_drive_the_config(base, tmp_path):
    creds = {"client_id": "USER_ID", "client_secret": "USER_SECRET"}

    cfg = schwab_settings_for_user(base, creds, user_token_path(tmp_path, "u1", "market_data"))

    assert cfg.client_id == "USER_ID"
    assert cfg.client_secret == "USER_SECRET"


def test_the_shared_settings_object_is_never_mutated(base, tmp_path):
    """The whole point. Mutating the global is how the .env route breaks everyone."""
    creds = {"client_id": "USER_ID", "client_secret": "USER_SECRET"}

    schwab_settings_for_user(base, creds, user_token_path(tmp_path, "u1", "market_data"))

    assert base.client_id == "HOUSE_ID"
    assert base.client_secret == "HOUSE_SECRET"
    assert base.token_path == "artifacts/schwab_token.json"


def test_the_config_points_at_the_users_own_token_file(base, tmp_path):
    path = user_token_path(tmp_path, "u1", "market_data")

    cfg = schwab_settings_for_user(base, {"client_id": "A", "client_secret": "B"}, path)

    assert str(cfg.token_path) == str(path)


def test_settings_that_are_not_credentials_are_inherited(base, tmp_path):
    cfg = schwab_settings_for_user(base, {"client_id": "A", "client_secret": "B"},
                                   user_token_path(tmp_path, "u1", "market_data"))

    assert cfg.redirect_uri == "https://127.0.0.1/"
    assert cfg.include_extended_hours is True
    assert cfg.timeout_seconds == 15


@pytest.mark.parametrize("creds", [
    {},
    None,
    {"client_id": "A"},
    {"client_secret": "B"},
    {"client_id": "", "client_secret": "B"},
    {"client_id": "A", "client_secret": "   "},
])
def test_a_user_without_their_own_keys_gets_nothing(base, tmp_path, creds):
    """None means "fall back to the house credential" - never a half-built
    config carrying the house keys under a user's token path, which would
    quietly authenticate as the owner."""
    assert schwab_settings_for_user(base, creds, user_token_path(tmp_path, "u1", "market_data")) is None


# --------------------------------------------------------------------------
# Where a save is allowed to land
#
# This is the security-critical decision. The .env path rewrites the whole
# server's Schwab keys; the vault path touches only the caller. Getting it
# wrong once means a user filling the form in honestly takes market data down
# for everybody - which already happened on 2026-08-26.
# --------------------------------------------------------------------------

from user_schwab import HOUSE, VAULT, credential_target  # noqa: E402


def test_an_administrator_writes_the_house_credential():
    # The owner IS the house: background jobs (warmers, scanners, alerts) fire
    # on a clock with no user in scope and must keep working.
    assert credential_target({"id": "a", "role": "admin", "isAdmin": True}) == HOUSE


@pytest.mark.parametrize("user", [
    {"id": "u", "role": "user", "isAdmin": False},
    {"id": "u", "role": "user"},
    {"id": "u"},
    {"id": "u", "role": "USER", "isAdmin": False},
    {"id": "u", "isAdmin": "yes"},
    {"id": "u", "role": "admin"},
])
def test_anyone_not_provably_an_administrator_writes_only_their_own_vault(user):
    """Fails closed on purpose.

    Note the last case: role says admin but isAdmin is absent. Both must agree
    before a caller is allowed near the shared .env - a truthy string, a
    missing flag or a case difference must never be enough to reach it.
    """
    assert credential_target(user) == VAULT


@pytest.mark.parametrize("nobody", [None, {}, {"role": "admin", "isAdmin": True}])
def test_no_identity_never_reaches_the_house_credential(nobody):
    # Includes an admin-looking dict with NO id: there is no account to attribute
    # the write to, so it must not be treated as the owner.
    assert credential_target(nobody) == VAULT


# --------------------------------------------------------------------------
# Wiring. Source-read: importing api_server boots every scheduler and opens the
# live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path as _Path  # noqa: E402

API_SOURCE = (_Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")


def test_the_schwab_settings_route_branches_on_who_is_asking() -> None:
    handler = route_handler(API_SOURCE, '/api/schwab/settings')
    assert "credential_target" in handler, (
        "the route must decide house-vs-vault from the caller's identity"
    )
    assert handler.index("credential_target") < handler.index("set_key("), (
        "the identity check must come BEFORE any write to .env"
    )


def test_the_per_user_save_cannot_reach_the_shared_config() -> None:
    """The whole safety story in one assertion.

    _save_user_schwab_credentials is the branch a non-admin takes. If it ever
    gains a set_key, os.environ or settings.schwab write, a user filling in the
    form takes market data down for everyone - which is exactly what happened
    on 2026-08-26.
    """
    start = API_SOURCE.find("    def _save_user_schwab_credentials(self")
    assert start != -1, "_save_user_schwab_credentials not found"
    body = API_SOURCE[start : API_SOURCE.find("\n    def ", start + 10)]

    # Read CODE, not prose: the docstring names these symbols precisely to
    # say it does not use them, and a guard that trips on its own
    # documentation is noise rather than protection.
    code = code_only(body)

    for forbidden in ("set_key(", "os.environ", "settings.schwab", "ENV_PATH"):
        assert forbidden not in code, f"the per-user save must never touch {forbidden}"
    assert "save_provider_credentials" in code


# --------------------------------------------------------------------------
# Status must describe the CALLER, never the house
#
# Reported 2026-08-26 by the owner, signed in as a user with no keys at all:
# the card showed an expiry date and the lamps were green. The status endpoint
# was falling back to the house client, so a user with nothing connected was
# shown the OWNER's connection under a card headed "My Schwab APIs".
#
# The distinction the first attempt missed: falling back for DATA is a
# fallback - their charts still draw. Falling back for STATUS is a false
# statement about their own setup.
# --------------------------------------------------------------------------

from user_schwab import unconfigured_status  # noqa: E402
from handler_source import function_body, route_handler


def test_an_unconnected_user_is_reported_as_not_connected():
    status = unconfigured_status()

    assert status["configured"] is False
    assert status["credentialsConfigured"] is False
    assert status["refreshTokenValid"] is False
    assert status["accessTokenValid"] is False


def test_an_unconnected_user_is_shown_no_expiry_date():
    """The specific thing the owner saw: somebody else's countdown."""
    status = unconfigured_status()

    assert status["refreshTokenExpiresAt"] is None
    assert status["tokenExpiresAt"] is None
    assert status["refreshTokenRemainingSeconds"] is None
    assert status["accessTokenRemainingSeconds"] is None


def test_no_part_of_the_house_credential_leaks_into_it():
    status = unconfigured_status()

    assert status["clientIdMasked"] == ""
    assert status["hasClientId"] is False
    assert status["hasClientSecret"] is False
    assert status["hasAccessToken"] is False
    assert status["hasRefreshToken"] is False
    assert status["tokenPath"] == ""


def test_it_carries_the_keys_the_settings_card_reads():
    # The card renders schwabStatus.marketData.*; a missing key would render
    # "undefined" rather than a clean not-connected state.
    status = unconfigured_status()

    for field in (
        "configured", "credentialsConfigured", "hasClientId", "hasClientSecret",
        "clientIdMasked", "hasAccessToken", "hasRefreshToken", "accessTokenValid",
        "refreshTokenValid", "accessTokenRemainingSeconds", "refreshTokenRemainingSeconds",
        "tokenExpiresAt", "refreshTokenExpiresAt", "tokenSavedAt", "tokenFileExists",
        "tokenPath", "redirectUri", "tokenManager", "tokenLoadError",
    ):
        assert field in status, f"the settings card reads {field}"


# --------------------------------------------------------------------------
# The administrator IS the house
#
# Found 2026-08-27, in the live quote path. The owner has an alpaca_market_data
# row in the vault - and that row IS the house Alpaca key, the one
# _owner_alpaca_chart_client reads. Treating it as a "personal key" double
# counts it and diverts the owner off the house Schwab quote path onto Alpaca
# IEX, which is a thinner tape whose prints sit cents away from Schwab's.
#
# It was invisible because another session's previous_closes backfilled the
# change/percent fields Schwab would have supplied, so the rails looked right
# while the underlying quote source had silently changed.
# --------------------------------------------------------------------------

from user_schwab import serves_own_providers  # noqa: E402


def test_an_administrator_is_served_by_the_house_not_their_own_vault_row():
    assert serves_own_providers({"id": "a", "role": "admin", "isAdmin": True}) is False


@pytest.mark.parametrize("member", [
    {"id": "u", "role": "user", "isAdmin": False},
    {"id": "u", "role": "user"},
    {"id": "u"},
])
def test_a_normal_user_is_served_by_their_own_providers(member):
    assert serves_own_providers(member) is True


def test_it_agrees_with_where_that_users_credentials_are_written():
    """One rule, not two.

    Whether a caller reads from the house or their own vault must match where
    their saves land. Two independent rules would eventually disagree, and the
    disagreement would look like a data-quality problem rather than a routing
    bug - which is exactly how this one hid.
    """
    for user in (
        {"id": "a", "role": "admin", "isAdmin": True},
        {"id": "u", "role": "user", "isAdmin": False},
        {"id": "u", "isAdmin": "yes"},
        None,
    ):
        assert serves_own_providers(user) is (credential_target(user) != HOUSE)
