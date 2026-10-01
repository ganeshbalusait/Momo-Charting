from __future__ import annotations

"""Accounts with no password at all.

Ganesh's decision (2026-08-26): adding someone is entering their email in
Settings, nothing else. Cloudflare Access checks the address with a one-time
code and the app matches it to an account - so a password is a second secret
that has to be invented, delivered and remembered for no benefit. Two accounts
sat unusable for eleven days precisely because a password had to be delivered.

The rule these tests pin: an account with no password can never be signed in
BY password. Not with the empty string, not with any guess. The absence of a
credential must fail closed, not match loosely - a passwordless account that
accepted "" would be an open door for anyone who knows an email address.
"""

import pytest
from cryptography.fernet import Fernet

from auth_service import AuthenticationError, AuthorizationError, AuthService


@pytest.fixture
def auth(tmp_path):
    return AuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())


@pytest.fixture
def owner(auth):
    # The first account keeps a password: it is the break-glass path if
    # Cloudflare is ever misconfigured or unreachable.
    return auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")


def test_a_user_is_created_from_an_email_alone(auth, owner):
    created = auth.create_user(email="member@example.com", actor=owner, display_name="Member")

    assert created["email"] == "member@example.com"
    assert created["isActive"] is True
    assert created["role"] == "user"


def test_a_passwordless_account_is_not_asked_to_change_a_password(auth, owner):
    # There is nothing to change, so the forced-change screen would strand them.
    created = auth.create_user(email="member@example.com", actor=owner)

    assert created["mustChangePassword"] is False


@pytest.mark.parametrize("guess", ["", " ", "password", "SecurePass123", "None", "null"])
def test_a_passwordless_account_can_never_sign_in_by_password(auth, owner, guess):
    auth.create_user(email="member@example.com", actor=owner)

    with pytest.raises(AuthenticationError):
        auth.verify_credentials("member@example.com", guess)


def test_a_passwordless_account_reports_that_it_has_no_password(auth, owner):
    created = auth.create_user(email="member@example.com", actor=owner)

    assert auth.get_user(created["id"])["hasPassword"] is False
    assert auth.get_user(owner["id"])["hasPassword"] is True


def test_cloudflare_can_still_sign_a_passwordless_account_in(auth, owner):
    # The whole point: no password, but Cloudflare's verified identity works.
    from cf_access_auth import resolve_access_identity

    auth.create_user(email="member@example.com", actor=owner)
    user, status = resolve_access_identity("member@example.com", auth.get_user_by_email)

    assert status == "ok"
    assert user["email"] == "member@example.com"


def test_a_disabled_passwordless_account_is_still_refused(auth, owner):
    from cf_access_auth import resolve_access_identity

    created = auth.create_user(email="member@example.com", actor=owner)
    auth.set_user_active(created["id"], False, actor=owner)

    # find_account_by_email, not get_user_by_email: the latter refuses a
    # disabled account by returning nothing, which reads as "never heard of
    # you" and sends the person chasing the wrong problem.
    user, status = resolve_access_identity("member@example.com", auth.find_account_by_email)
    assert (user, status) == (None, "disabled")


def test_a_password_may_still_be_set_explicitly(auth, owner):
    # Back-compat: bootstrap and any break-glass account still need one.
    created = auth.create_user(email="member@example.com", password="Temporary123", actor=owner)

    assert auth.verify_credentials("member@example.com", "Temporary123")["id"] == created["id"]
    assert auth.get_user(created["id"])["hasPassword"] is True


def test_the_first_account_still_requires_a_password(auth):
    # The owner is the break-glass path; creating one with no way in is a trap.
    with pytest.raises(AuthenticationError):
        auth.bootstrap_owner("owner@example.com", "", "Owner")


def test_a_non_admin_still_cannot_create_accounts(auth, owner):
    member = auth.create_user(email="member@example.com", actor=owner)

    with pytest.raises(AuthorizationError):
        auth.create_user(email="sneaky@example.com", actor=member)


def test_removing_a_password_from_an_existing_account(auth, owner):
    created = auth.create_user(email="member@example.com", password="Temporary123", actor=owner)
    auth.remove_password(created["id"], actor=owner)

    assert auth.get_user(created["id"])["hasPassword"] is False
    with pytest.raises(AuthenticationError):
        auth.verify_credentials("member@example.com", "Temporary123")


def test_the_last_admin_cannot_have_its_password_removed(auth, owner):
    # Otherwise a Cloudflare misconfiguration locks everyone out for good.
    with pytest.raises(AuthenticationError):
        auth.remove_password(owner["id"], actor=owner)


# --------------------------------------------------------------------------
# HTTP surface. Read from source: importing api_server boots every scheduler
# and opens the live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from handler_source import function_body, route_handler

API_SOURCE = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")


def test_remove_password_route_exists_and_is_admin_gated() -> None:
    handler = route_handler(API_SOURCE, '/api/admin/users/remove-password')
    assert "_require_admin_user" in handler
    assert "remove_password" in handler
    assert "actor=" in handler


def test_creating_a_user_passes_the_password_through_without_demanding_one() -> None:
    """The POST handler, not the GET one.

    '/api/admin/users' is routed twice - once in do_GET to list users, once in
    do_POST to create one - so a plain .find() lands on the lister and asserts
    nothing about creation. This file has form: two near-identical blocks is
    exactly how the loopback fix earlier today read as done while half of it
    was still open.
    """
    handlers = [
        API_SOURCE[i : i + 1400]
        for i in range(len(API_SOURCE))
        if API_SOURCE.startswith('if parsed.path == "/api/admin/users":', i)
    ]
    creating = [h for h in handlers if "create_user(" in h]
    assert len(creating) == 1, f"expected one create handler, found {len(creating)}"

    handler = creating[0]
    # The field is still accepted - bootstrap and break-glass accounts use it -
    # but nothing may reject a request that omits it.
    assert "temporaryPassword" in handler
    assert 'body.get("temporaryPassword", "")' in handler, (
        "must default to empty rather than requiring the field"
    )
    assert "required" not in handler.lower()
