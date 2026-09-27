from __future__ import annotations

"""Guard the two places that hand out access with no password checked.

Both grant on "the peer IP is loopback", and both were reachable from the
public internet because cloudflared forwards app.agxtrade.com to
http://127.0.0.1:3001 (hosting/config.yml):

  * _session_user  - LOCAL_AUTO_LOGIN_EMAIL signs the request in as the owner.
  * _finish_login  - an admin's brand-new device self-approves, skipping the
                     approval gate that every other device must pass.

They are two near-identical blocks, so a fix applied to one reads as done
while the other stays open. This asserts on BOTH, and on the admin gate for
the endpoint that rewrites the server's shared Schwab keys.

Reads the source rather than importing api_server: importing it boots every
scheduler and opens the live database (see tests/test_gateway.py). The
decision logic itself is covered behaviourally in test_request_trust.py.
"""

import re
from pathlib import Path

import pytest

from handler_source import route_handler

API_SERVER = Path(__file__).resolve().parent.parent / "api_server.py"
SOURCE = API_SERVER.read_text(encoding="utf-8")

# The exact check that was wrong: trusting the peer address on its own.
BARE_LOOPBACK_CHECK = re.compile(
    r"""client_ip\s*in\s*\{["']127\.0\.0\.1["']\s*,\s*["']::1["']\}"""
)


def _function_source(name: str) -> str:
    match = re.search(rf"\n    def {re.escape(name)}\(.*?(?=\n    def )", SOURCE, re.DOTALL)
    assert match, f"{name} not found in api_server.py"
    return match.group(0)


def test_no_bare_loopback_ip_check_survives_anywhere() -> None:
    found = BARE_LOOPBACK_CHECK.findall(SOURCE)
    assert not found, (
        f"{len(found)} bare loopback-IP check(s) remain. Under cloudflared every "
        "tunnel visitor has client_address 127.0.0.1, so this grants public access."
    )


@pytest.mark.parametrize("function_name", ["_session_user", "_finish_login"])
def test_password_free_grant_consults_request_trust(function_name: str) -> None:
    body = _function_source(function_name)
    assert "_is_local_request" in body, (
        f"{function_name} grants access without a password and must decide "
        "locality via the shared _is_local_request helper, not the peer IP alone."
    )


def test_api_server_imports_request_trust() -> None:
    assert re.search(r"^from request_trust import .*is_trusted_local", SOURCE, re.MULTILINE), (
        "api_server must import is_trusted_local from request_trust"
    )


# Schwab credentials are no longer one server-wide pair. Each user has their
# own encrypted vault row and their own token file (see user_schwab), so
# "admin-only" is the wrong property to pin now: a user MAY save Schwab keys,
# they simply must not be able to save them anywhere that affects anyone else.
#
# These tests therefore assert the PROPERTY rather than the mechanism. The
# earlier version asserted that the string "require_admin" appeared in each
# handler, and went red the moment the mechanism legitimately changed - which
# reads exactly like a security regression and cost another session an hour.
#
# Verified empirically on 2026-08-27, not only by reading: the original forgery
# probe ({"clientId": "EVIL"}) was replayed against a genuine non-admin
# session. It returned {"saved": ["market_data"], "scope": "user"} and left
# .env untouched, with the value landing in that user's own vault row.


def _handler(path: str) -> str:
    """Sliced to the handler's real end, never a byte count.

    A fixed window meant that adding a comment inside a handler pushed the
    assertion outside it, so a correct change read as a security regression.
    That happened three times in one week here. See tests/handler_source.py.
    """
    return route_handler(SOURCE, path)


def test_only_a_house_caller_can_reach_the_env_write() -> None:
    """.env holds the credential every clock-driven job runs on.

    A non-admin reaching set_key here is what took market data down on
    2026-08-26. The guard must come first AND the non-house branch must return,
    or the check is decorative.
    """
    handler = _handler("/api/schwab/settings")

    gate = handler.find("credential_target")
    write = handler.find("set_key(")
    assert gate != -1, "the settings route must decide house-vs-vault by identity"
    assert write != -1, "expected the .env write to still exist for the house path"
    assert gate < write, "the identity check must come before any write to .env"

    branch = handler[gate:write]
    assert "return" in branch, (
        "the non-house branch must RETURN before reaching set_key - falling "
        "through would let a user rewrite the server's Schwab keys"
    )
    assert "HOUSE" in branch


@pytest.mark.parametrize("path", ["/api/schwab/token", "/api/schwab/oauth/start"])
def test_an_oauth_flow_cannot_land_a_token_on_the_house_file(path: str) -> None:
    """A user's OAuth must write THEIR token file, never the shared one.

    _schwab_oauth_client returns the house client only for an administrator and
    otherwise the caller's own or None. Reaching for _schwab_client_for_profile
    or the falling-back _schwab_client_for here would hand the server a user's
    Schwab identity and log the owner out.
    """
    handler = _handler(path)

    assert "_schwab_oauth_client" in handler, (
        f"{path} must resolve the client through the non-falling-back helper"
    )
    assert "_schwab_client_for_profile(" not in handler, (
        f"{path} must not reach for the house client directly"
    )
    assert "_schwab_client_for(" not in handler, (
        f"{path} must not use the read resolver, which falls back to the house"
    )


def test_the_read_only_connection_test_stays_open_to_everyone() -> None:
    # Not a write, and a user needs it to check their own connection.
    handler = _handler("/api/schwab/test-connection")
    assert "_require_admin_user" not in handler
