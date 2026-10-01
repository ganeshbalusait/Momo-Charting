from __future__ import annotations

"""What happens once Cloudflare's token has been verified.

Verification says WHO they are. This says whether that person may in.
Ganesh's decision (2026-08-25): no auto-creation - someone Cloudflare lets
through but who has no AGX account is stopped, with a message naming the
address, so a mismatch between the two lists is visible instead of being the
silent dead end it used to be.
"""

import pytest

from cf_access_auth import resolve_access_identity


MEMBER = {
    "id": "u1",
    "email": "member@example.com",
    "displayName": "Member",
    "role": "user",
    "isAdmin": False,
    "isActive": True,
    "mustChangePassword": True,
}


def _lookup(*users):
    table = {u["email"]: u for u in users}
    return lambda email: table.get(email)


def test_a_known_active_account_is_signed_in():
    user, status = resolve_access_identity("member@example.com", _lookup(MEMBER))

    assert status == "ok"
    assert user["id"] == "u1"


def test_an_unknown_address_is_refused_and_says_so():
    user, status = resolve_access_identity("stranger@example.com", _lookup(MEMBER))

    assert user is None
    assert status == "no_account", (
        "the frontend needs to tell them which address has no account, rather "
        "than showing a login screen they cannot pass"
    )


def test_a_disabled_account_is_refused_even_though_cloudflare_allowed_it():
    # The Disable button must not be silently overridden by the Cloudflare list.
    disabled = {**MEMBER, "isActive": False}
    user, status = resolve_access_identity("member@example.com", _lookup(disabled))

    assert user is None
    assert status == "disabled"


def test_a_cloudflare_sign_in_is_not_forced_to_change_a_password():
    # They never used a password, so the forced-change screen would trap them
    # in Settings with nothing to do.
    user, _ = resolve_access_identity("member@example.com", _lookup(MEMBER))

    assert user["mustChangePassword"] is False


def test_the_stored_record_is_not_mutated():
    resolve_access_identity("member@example.com", _lookup(MEMBER))

    assert MEMBER["mustChangePassword"] is True, "must not edit the caller's dict"


def test_the_sign_in_route_is_recorded():
    user, _ = resolve_access_identity("member@example.com", _lookup(MEMBER))

    assert user["signedInVia"] == "cloudflare"


def test_an_admin_keeps_admin_rights():
    admin = {**MEMBER, "role": "admin", "isAdmin": True}
    user, status = resolve_access_identity("member@example.com", _lookup(admin))

    assert status == "ok"
    assert user["isAdmin"] is True


@pytest.mark.parametrize("blank", ["", None, "   "])
def test_no_verified_email_means_no_opinion(blank):
    user, status = resolve_access_identity(blank, _lookup(MEMBER))

    assert (user, status) == (None, "")


def test_a_lookup_that_blows_up_refuses_rather_than_crashing():
    def explode(email):
        raise RuntimeError("database is locked")

    user, status = resolve_access_identity("member@example.com", explode)

    assert user is None
    assert status == "error"


# --------------------------------------------------------------------------
# Wiring, read from source: importing api_server boots every scheduler and
# opens the live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402
from handler_source import function_body, route_handler

ROOT = Path(__file__).resolve().parent.parent
API_SOURCE = (ROOT / "api_server.py").read_text(encoding="utf-8")
GATEWAY_SOURCE = (ROOT / "gateway.py").read_text(encoding="utf-8")


def test_the_gateway_never_forwards_a_client_supplied_access_email() -> None:
    """The header must be unspoofable by construction.

    gateway._proxy copies only FORWARD_REQUEST_HEADERS, so a client's own copy
    is dropped before the gateway writes the verified one. Adding this name to
    that allowlist would let any visitor assert they are the owner.
    """
    from cf_access_auth import ACCESS_EMAIL_HEADER

    start = GATEWAY_SOURCE.find("FORWARD_REQUEST_HEADERS = (")
    allowlist = GATEWAY_SOURCE[start : GATEWAY_SOURCE.find(")", start)]
    assert ACCESS_EMAIL_HEADER.lower() not in allowlist.lower()


def test_the_gateway_verifies_before_passing_the_address_inward() -> None:
    start = GATEWAY_SOURCE.find("    def _proxy(self)")
    body = GATEWAY_SOURCE[start : GATEWAY_SOURCE.find("\n    def ", start + 10)]
    assert "verified_email" in body, (
        "the raw Cf-Access-Authenticated-User-Email header must never be "
        "trusted - only the signed token proves anything"
    )
    assert "Cf-Access-Authenticated-User-Email" not in body


def _api_function(name: str) -> str:
    start = API_SOURCE.find(f"    def {name}(self)")
    assert start != -1, f"{name} not found in api_server.py"
    return API_SOURCE[start : API_SOURCE.find("\n    def ", start + 10)]


def test_api_server_resolves_the_identity_rather_than_trusting_the_header() -> None:
    from cf_access_auth import ACCESS_EMAIL_HEADER

    session_user = _api_function("_session_user")
    assert "_access_identity" in session_user, (
        "_session_user must go through the resolver so a disabled account and "
        "an unknown address are both refused"
    )
    assert ACCESS_EMAIL_HEADER not in session_user, (
        "_session_user must not read the address header directly - that would "
        "skip the disabled-account and unknown-address checks"
    )
    assert "resolve_access_identity" in _api_function("_access_identity")


def test_auth_status_tells_the_visitor_why_they_were_refused() -> None:
    handler = route_handler(API_SOURCE, '/api/auth/status')
    assert "accessStatus" in handler, (
        "the login screen needs the refusal reason, otherwise it is the same "
        "silent dead end as before"
    )
