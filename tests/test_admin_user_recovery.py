from __future__ import annotations

"""Admin recovery actions: reset a forgotten password, switch an account off.

Neither existed, and their absence stranded two real accounts. change_password
demands the CURRENT password, so a lost temporary password was a dead end: the
app could create a user it could never help again, and could not revoke access
without hand-editing the database.
"""

import pytest
from cryptography.fernet import Fernet

from auth_service import AuthenticationError, AuthorizationError, AuthService


@pytest.fixture
def auth(tmp_path):
    return AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())


@pytest.fixture
def owner(auth):
    return auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")


@pytest.fixture
def member(auth, owner):
    return auth.create_user(
        email="member@example.com",
        password="Temporary123",
        actor=owner,
        display_name="Member",
    )


# --------------------------------------------------------------------------
# reset_password
# --------------------------------------------------------------------------


def test_admin_resets_a_forgotten_password_and_the_new_one_works(auth, owner, member):
    auth.reset_password(member["id"], "FreshStart456", actor=owner)

    assert auth.verify_credentials("member@example.com", "FreshStart456")["id"] == member["id"]


def test_the_old_password_stops_working_after_a_reset(auth, owner, member):
    auth.reset_password(member["id"], "FreshStart456", actor=owner)

    with pytest.raises(AuthenticationError):
        auth.verify_credentials("member@example.com", "Temporary123")


def test_a_reset_password_must_be_changed_on_next_sign_in(auth, owner, member):
    auth.reset_password(member["id"], "FreshStart456", actor=owner)

    assert auth.get_user(member["id"])["mustChangePassword"] is True


def test_resetting_a_password_ends_that_users_existing_sessions(auth, owner, member):
    token = auth.create_session(member)
    assert auth.user_for_session(token) is not None

    auth.reset_password(member["id"], "FreshStart456", actor=owner)

    assert auth.user_for_session(token) is None, (
        "a reset is how you take an account back; leaving live sessions "
        "running would defeat it"
    )


def test_a_reset_leaves_other_users_sessions_alone(auth, owner, member):
    owner_token = auth.create_session(owner)

    auth.reset_password(member["id"], "FreshStart456", actor=owner)

    assert auth.user_for_session(owner_token) is not None


def test_a_non_admin_cannot_reset_a_password(auth, owner, member):
    with pytest.raises(AuthorizationError):
        auth.reset_password(owner["id"], "TakenOver789", actor=member)


def test_a_reset_password_must_satisfy_the_password_rules(auth, owner, member):
    with pytest.raises(AuthenticationError):
        auth.reset_password(member["id"], "short", actor=owner)
    # and the old password must still work, i.e. nothing was half-applied
    assert auth.verify_credentials("member@example.com", "Temporary123")["id"] == member["id"]


def test_resetting_an_unknown_user_is_an_error(auth, owner):
    with pytest.raises(AuthenticationError):
        auth.reset_password("no-such-id", "FreshStart456", actor=owner)


# --------------------------------------------------------------------------
# set_user_active
# --------------------------------------------------------------------------


def test_admin_disables_an_account_and_the_password_stops_working(auth, owner, member):
    auth.set_user_active(member["id"], False, actor=owner)

    assert auth.get_user(member["id"])["isActive"] is False
    with pytest.raises(AuthenticationError):
        auth.verify_credentials("member@example.com", "Temporary123")


def test_disabling_an_account_ends_its_live_sessions(auth, owner, member):
    token = auth.create_session(member)

    auth.set_user_active(member["id"], False, actor=owner)

    assert auth.user_for_session(token) is None, (
        "switching someone off must take effect now, not whenever their "
        "30-day session cookie happens to expire"
    )


def test_a_disabled_account_can_be_switched_back_on(auth, owner, member):
    auth.set_user_active(member["id"], False, actor=owner)
    auth.set_user_active(member["id"], True, actor=owner)

    assert auth.verify_credentials("member@example.com", "Temporary123")["id"] == member["id"]


def test_an_admin_cannot_disable_themselves(auth, owner):
    with pytest.raises(AuthenticationError):
        auth.set_user_active(owner["id"], False, actor=owner)


def test_the_last_active_admin_cannot_be_disabled(auth, owner):
    second = auth.create_user(
        email="admin2@example.com", password="Temporary123", actor=owner, role="admin",
    )
    # Two admins: disabling one is fine.
    auth.set_user_active(owner["id"], False, actor=second)

    # One left: it must not be possible to lock the app out of administration.
    with pytest.raises(AuthenticationError):
        auth.set_user_active(second["id"], False, actor=second)


def test_a_non_admin_cannot_disable_anyone(auth, owner, member):
    with pytest.raises(AuthorizationError):
        auth.set_user_active(owner["id"], False, actor=member)


# --------------------------------------------------------------------------
# HTTP surface
#
# Read from source rather than imported: importing api_server boots every
# scheduler and opens the live database (see tests/test_gateway.py). The
# behaviour itself is covered above; this pins that the routes exist, are
# admin-gated, and pass the caller through as the acting administrator.
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from handler_source import function_body, route_handler

API_SOURCE = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")

RECOVERY_ROUTES = [
    ("/api/admin/users/reset-password", "reset_password"),
    ("/api/admin/users/set-active", "set_user_active"),
]


@pytest.mark.parametrize("route,method", RECOVERY_ROUTES, ids=[r for r, _ in RECOVERY_ROUTES])
def test_recovery_route_exists_and_is_admin_gated(route: str, method: str) -> None:
    handler = route_handler(API_SOURCE, route)
    assert "_require_admin_user" in handler, f"{route} must reject non-admin sessions"
    assert method in handler, f"{route} must call auth_service.{method}"
    assert "actor=" in handler, (
        f"{route} must pass the caller as actor - require_admin is enforced on "
        "the acting user, not on the target"
    )
