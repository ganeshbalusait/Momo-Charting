from __future__ import annotations

"""LOCAL_AUTO_LOGIN_EMAIL signs a request in with no password. It may only
fire for requests that genuinely originated on this machine.

The original guard trusted the peer IP alone. That was true when the app was
laptop-only and became false the day cloudflared was put in front of it:
hosting/config.yml forwards app.agxtrade.com to http://127.0.0.1:3001, so
every visitor from the public internet arrives with client_address 127.0.0.1
and passed a loopback-only check. These tests pin the corrected contract.

Deliberately imports ONLY request_trust - importing api_server boots every
scheduler and touches the live database (see tests/test_gateway.py).
"""

import pytest

from request_trust import (
    ORIGIN_HEADER,
    ORIGIN_LOCAL,
    ORIGIN_RELAYED,
    is_local_request,
    is_trusted_local,
    origin_stamp,
)


LOCAL_HOSTS = ["localhost:3001", "127.0.0.1:3001", "localhost:5173", "127.0.0.1:4173", "[::1]:3001"]


def test_local_browser_request_is_trusted() -> None:
    assert is_local_request("127.0.0.1", {"Host": "localhost:3001"}) is True


@pytest.mark.parametrize("host", LOCAL_HOSTS)
def test_every_port_the_trader_browses_is_trusted(host: str) -> None:
    # The trader browses :5173 (Vite), the PWA is served from :3001/dist and
    # :4173 is the built preview. All three must keep auto-login working.
    assert is_local_request("127.0.0.1", {"Host": host}) is True


def test_ipv6_loopback_is_trusted() -> None:
    assert is_local_request("::1", {"Host": "localhost:3001"}) is True


def test_cloudflare_tunnel_request_is_not_trusted() -> None:
    # The exact shape of a real app.agxtrade.com visitor: cloudflared connects
    # from loopback and forwards the origin's identity in headers.
    headers = {
        "Host": "app.agxtrade.com",
        "X-Forwarded-For": "203.0.113.7",
        "X-Forwarded-Proto": "https",
        "Cf-Ray": "8f2a1b3c4d5e6f70-EWR",
        "Cf-Connecting-Ip": "203.0.113.7",
    }
    assert is_local_request("127.0.0.1", headers) is False


def test_forwarded_header_alone_blocks_trust() -> None:
    # Even if the Host looks local, a proxy header means someone else is
    # relaying this request. Fail closed.
    assert is_local_request("127.0.0.1", {"Host": "localhost:3001", "X-Forwarded-For": "203.0.113.7"}) is False


def test_cf_connecting_ip_alone_blocks_trust() -> None:
    assert is_local_request("127.0.0.1", {"Host": "localhost:3001", "CF-Connecting-IP": "203.0.113.7"}) is False


def test_cf_access_assertion_blocks_trust() -> None:
    assert is_local_request("127.0.0.1", {"Host": "localhost:3001", "Cf-Access-Jwt-Assertion": "ey.J.x"}) is False


def test_public_hostname_alone_blocks_trust() -> None:
    # Belt and braces: a proxy that strips its own headers still cannot
    # disguise which hostname the visitor asked for.
    assert is_local_request("127.0.0.1", {"Host": "app.agxtrade.com"}) is False


def test_proxy_header_check_is_case_insensitive() -> None:
    # HTTP header names are case-insensitive; a check that only matched one
    # spelling would be trivially bypassable.
    assert is_local_request("127.0.0.1", {"Host": "localhost:3001", "x-forwarded-for": "203.0.113.7"}) is False


def test_remote_peer_is_not_trusted() -> None:
    assert is_local_request("203.0.113.7", {"Host": "localhost:3001"}) is False


def test_missing_host_header_is_not_trusted() -> None:
    # Fail closed on anything unexpected rather than guessing.
    assert is_local_request("127.0.0.1", {}) is False


def test_missing_client_ip_is_not_trusted() -> None:
    assert is_local_request("", {"Host": "localhost:3001"}) is False


# ---------------------------------------------------------------------------
# The two-hop topology.
#
# cloudflared -> :3001 gateway.py -> :3002 api_server.py. The gateway forwards
# an ALLOWLIST of request headers and deliberately drops Host, so by the time a
# tunnel visitor reaches api_server there are no relay headers left and Host is
# 127.0.0.1:3002 - indistinguishable from the trader's own browser. Only the
# gateway can still tell them apart, so it must say so explicitly and
# api_server must believe it.
# ---------------------------------------------------------------------------

TUNNEL_HEADERS = {
    "Host": "app.agxtrade.com",
    "X-Forwarded-For": "203.0.113.7",
    "Cf-Ray": "8f2a1b3c4d5e6f70-EWR",
}
BROWSER_HEADERS = {"Host": "localhost:3001"}


def test_gateway_stamps_local_for_the_traders_own_browser() -> None:
    assert origin_stamp("127.0.0.1", BROWSER_HEADERS) == ORIGIN_LOCAL


def test_gateway_stamps_relayed_for_a_tunnel_visitor() -> None:
    assert origin_stamp("127.0.0.1", TUNNEL_HEADERS) == ORIGIN_RELAYED


def test_api_server_believes_a_relayed_stamp_despite_looking_local() -> None:
    # This is the whole point: everything api_server can see says "local".
    relayed_hop = {"Host": "127.0.0.1:3002", ORIGIN_HEADER: ORIGIN_RELAYED}
    assert is_local_request("127.0.0.1", relayed_hop) is True  # what it sees
    assert is_trusted_local("127.0.0.1", relayed_hop) is False  # what it must conclude


def test_api_server_honours_a_local_stamp() -> None:
    assert is_trusted_local("127.0.0.1", {"Host": "127.0.0.1:3002", ORIGIN_HEADER: ORIGIN_LOCAL}) is True


def test_unstamped_request_falls_back_to_direct_inspection() -> None:
    # Nothing but this machine can reach :3002, so an unstamped request is
    # local tooling or the gateway's own SSE pump. Keep those working.
    assert is_trusted_local("127.0.0.1", {"Host": "127.0.0.1:3002"}) is True
    assert is_trusted_local("203.0.113.7", {"Host": "127.0.0.1:3002"}) is False


def test_unrecognised_stamp_value_is_not_trusted() -> None:
    assert is_trusted_local("127.0.0.1", {"Host": "localhost:3001", ORIGIN_HEADER: "banana"}) is False


def test_stamp_is_read_case_insensitively() -> None:
    assert is_trusted_local("127.0.0.1", {"Host": "127.0.0.1:3002", "x-agx-request-origin": "RELAYED"}) is False


def test_gateway_never_forwards_a_client_supplied_origin_stamp() -> None:
    """The stamp must be unspoofable by construction.

    gateway._proxy copies only FORWARD_REQUEST_HEADERS, so a header a client
    invents is dropped before the gateway writes its own. If the stamp were
    ever added to that allowlist, a remote visitor could simply send
    "X-AGX-Request-Origin: local" and walk straight back into the owner's
    account.
    """
    from pathlib import Path

    gateway_source = (Path(__file__).resolve().parent.parent / "gateway.py").read_text(encoding="utf-8")
    start = gateway_source.find("FORWARD_REQUEST_HEADERS = (")
    assert start != -1, "FORWARD_REQUEST_HEADERS not found in gateway.py"
    allowlist = gateway_source[start : gateway_source.find(")", start)]
    assert ORIGIN_HEADER.lower() not in allowlist.lower(), (
        f"{ORIGIN_HEADER} must never be forwarded from the client - the gateway "
        "is its only author"
    )


def test_gateway_stamps_every_proxied_request() -> None:
    from pathlib import Path

    gateway_source = (Path(__file__).resolve().parent.parent / "gateway.py").read_text(encoding="utf-8")
    start = gateway_source.find("    def _proxy(self)")
    assert start != -1, "gateway._proxy not found"
    body = gateway_source[start : gateway_source.find("\n    def ", start + 10)]
    assert "origin_stamp" in body, (
        "gateway._proxy must stamp request origin - api_server cannot work it "
        "out for itself once Host and the relay headers are gone"
    )
