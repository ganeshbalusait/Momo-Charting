from __future__ import annotations

"""Deleting an account, and everything that must go with it.

Disable keeps the row and refuses sign-in. Delete removes the person entirely -
which is what you want when an address was entered wrongly, or someone is gone
for good and you want their email freed for re-use.

The danger in a delete is not the row you meant to remove; it is the rows you
forgot. A session or device surviving its user, a cached identity still
resolving a deleted email, or an email that stays unusable because something
still references it. Each of those turns "deleted" into "half deleted", which
is worse than not offering the button.
"""

import sqlite3

import pytest
from cryptography.fernet import Fernet

from auth_service import AuthenticationError, AuthorizationError, AuthService


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "auth.db"


@pytest.fixture
def auth(db_path):
    return AuthService(db_path=db_path, encryption_key=Fernet.generate_key())


@pytest.fixture
def owner(auth):
    return auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")


@pytest.fixture
def member(auth, owner):
    return auth.create_user(email="member@example.com", actor=owner, display_name="Member")


def _rows(db_path, table, user_id):
    connection = sqlite3.connect(db_path)
    try:
        return connection.execute(
            f"SELECT COUNT(*) FROM {table} WHERE user_id = ?", (user_id,)
        ).fetchone()[0]
    finally:
        connection.close()


# --------------------------------------------------------------------------
# The account itself
# --------------------------------------------------------------------------


def test_a_deleted_account_is_gone(auth, owner, member):
    auth.delete_user(member["id"], actor=owner)

    assert auth.get_user_by_email("member@example.com") is None
    assert auth.find_account_by_email("member@example.com") is None
    assert all(u["id"] != member["id"] for u in auth.list_users(owner))


def test_deleting_is_not_the_same_as_disabling(auth, owner, member):
    # Disable leaves a row that find_account_by_email still returns, which is
    # how the UI says "switched off". Delete must leave nothing.
    auth.set_user_active(member["id"], False, actor=owner)
    assert auth.find_account_by_email("member@example.com") is not None

    auth.delete_user(member["id"], actor=owner)
    assert auth.find_account_by_email("member@example.com") is None


def test_the_email_can_be_used_again_afterwards(auth, owner, member):
    # The likeliest reason to delete: the address was entered wrongly and the
    # person needs re-adding. A UNIQUE email that stays taken defeats that.
    auth.delete_user(member["id"], actor=owner)

    recreated = auth.create_user(email="member@example.com", actor=owner, display_name="Member Again")
    assert recreated["email"] == "member@example.com"
    assert recreated["id"] != member["id"]


def test_a_deleted_email_is_not_still_resolved_from_cache(auth, owner, member):
    # find_account_by_email is cached. If a delete does not invalidate it, the
    # Cloudflare path keeps signing in a person who no longer exists.
    assert auth.find_account_by_email("member@example.com") is not None

    auth.delete_user(member["id"], actor=owner)

    assert auth.find_account_by_email("member@example.com") is None


# --------------------------------------------------------------------------
# Everything that hangs off the account
# --------------------------------------------------------------------------


def test_their_sessions_do_not_survive_them(auth, db_path, owner, member):
    token = auth.create_session(member)
    assert auth.user_for_session(token) is not None

    auth.delete_user(member["id"], actor=owner)

    assert _rows(db_path, "app_user_sessions", member["id"]) == 0
    assert auth.user_for_session(token) is None, "a session outliving its user is a ghost login"


def test_their_devices_do_not_survive_them(auth, db_path, owner, member):
    auth.create_session(member)
    auth.authorize_login_device(member, device_token="", user_agent="Chrome", ip_address="1.2.3.4")

    auth.delete_user(member["id"], actor=owner)

    assert _rows(db_path, "app_user_devices", member["id"]) == 0


def test_their_stored_api_keys_do_not_survive_them(auth, db_path, owner, member):
    auth.save_provider_credentials(member, "alpaca_market_data", {"key_id": "K", "secret_key": "S"})
    assert _rows(db_path, "app_user_provider_credentials", member["id"]) == 1

    auth.delete_user(member["id"], actor=owner)

    assert _rows(db_path, "app_user_provider_credentials", member["id"]) == 0, (
        "leaving encrypted broker credentials behind for a person who no "
        "longer exists is the worst kind of leftover"
    )


def test_other_peoples_data_is_untouched(auth, db_path, owner, member):
    other = auth.create_user(email="other@example.com", actor=owner)
    other_token = auth.create_session(other)
    auth.save_provider_credentials(other, "alpaca_market_data", {"key_id": "K", "secret_key": "S"})

    auth.delete_user(member["id"], actor=owner)

    assert auth.user_for_session(other_token) is not None
    assert _rows(db_path, "app_user_provider_credentials", other["id"]) == 1


# --------------------------------------------------------------------------
# What must be refused
# --------------------------------------------------------------------------


def test_you_cannot_delete_yourself(auth, owner):
    # Recoverable only by hand-editing the database.
    with pytest.raises(AuthenticationError):
        auth.delete_user(owner["id"], actor=owner)


def test_removing_one_of_two_admins_is_allowed(auth, owner):
    second = auth.create_user(email="admin2@example.com", actor=owner, role="admin")

    auth.delete_user(owner["id"], actor=second)

    assert auth.find_account_by_email("owner@example.com") is None


def test_a_non_admin_cannot_delete_anyone(auth, owner, member):
    other = auth.create_user(email="other@example.com", actor=owner)

    with pytest.raises(AuthorizationError):
        auth.delete_user(other["id"], actor=member)


def test_deleting_an_unknown_account_is_an_error(auth, owner):
    with pytest.raises(AuthenticationError):
        auth.delete_user("no-such-id", actor=owner)


def test_a_disabled_admin_does_not_count_as_cover(auth, owner):
    """The last ACTIVE admin is what matters.

    A disabled administrator cannot sign in, so it is not a way back in - and
    deleting the only enabled one while a disabled one exists would still leave
    nobody able to administer the app.
    """
    spare = auth.create_user(email="admin2@example.com", actor=owner, role="admin")
    auth.set_user_active(spare["id"], False, actor=owner)

    # Deleted BY the disabled admin, so this trips the last-active-admin guard
    # rather than the you-cannot-delete-yourself one. owner is the only admin
    # who can actually sign in, so removing it leaves nobody able to administer.
    with pytest.raises(AuthenticationError):
        auth.delete_user(owner["id"], actor=spare)


# --------------------------------------------------------------------------
# HTTP surface. Read from source: importing api_server boots every scheduler
# and opens the live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from handler_source import function_body, route_handler

API_SOURCE = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")


def test_the_delete_route_exists_and_is_admin_gated() -> None:
    handler = route_handler(API_SOURCE, '/api/admin/users/delete')
    assert "_require_admin_user" in handler, "a delete must reject non-admin sessions"
    assert "delete_user" in handler
    assert "actor=" in handler, (
        "the caller must be passed as actor - the self-delete and last-admin "
        "guards are both evaluated against who is asking"
    )


def test_a_device_approved_by_the_deleted_admin_keeps_its_row(auth, db_path, owner):
    """approved_by must be cleared explicitly, like every other reference.

    app_user_devices.approved_by declares ON DELETE SET NULL - which fires only
    because _connect turns foreign keys on. delete_user's own docstring refuses
    to rely on that for the child rows, so relying on it here is an
    inconsistency waiting to bite: run the same delete from a maintenance
    script and the device row is left pointing at a user that no longer exists.
    """
    second = auth.create_user(email="admin2@example.com", actor=owner, role="admin")
    member = auth.create_user(email="member@example.com", actor=owner)
    decision = auth.authorize_login_device(member, device_token="", user_agent="Chrome", ip_address="1.2.3.4")
    device_id = decision["device"]["id"]

    connection = sqlite3.connect(db_path)
    try:
        connection.execute("UPDATE app_user_devices SET approved_by = ? WHERE id = ?", (second["id"], device_id))
        connection.commit()
    finally:
        connection.close()

    auth.delete_user(second["id"], actor=owner)

    connection = sqlite3.connect(db_path)
    try:
        row = connection.execute(
            "SELECT user_id, approved_by FROM app_user_devices WHERE id = ?", (device_id,)
        ).fetchone()
    finally:
        connection.close()

    assert row is not None, "the member's device must survive the approver being deleted"
    assert row[0] == member["id"]
    assert row[1] is None, "approved_by must be cleared, not left dangling"


API_DELETE_HANDLER_START = 'if parsed.path == "/api/admin/users/delete":'


def test_delete_and_disable_both_reset_the_owner_alpaca_client() -> None:
    """The 'owner' Alpaca client is memoized from the first ACTIVE admin.

    api_server picks credentials with `WHERE is_active = 1 ORDER BY CASE role
    WHEN 'admin' THEN 0 ELSE 1 END, created_at LIMIT 1` and memoizes the client
    with no TTL. Deleting or disabling that admin changes who that query
    returns - so without a reset the backend keeps calling Alpaca with a
    removed person's key until the process restarts, or silently goes dark.
    """
    for route in ('"/api/admin/users/delete"', '"/api/admin/users/set-active"'):
        start = API_SOURCE.find(f"if parsed.path == {route}:")
        assert start != -1, f"{route} is not routed"
        handler = API_SOURCE[start : start + 1500]
        assert "_owner_alpaca_client_cache" in handler, (
            f"{route} changes which account is the credential owner and must "
            "drop the memoized Alpaca client, as the api-keys handler does"
        )
