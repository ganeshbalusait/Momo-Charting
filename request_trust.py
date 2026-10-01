from __future__ import annotations

"""Does an HTTP request genuinely originate on this machine?

Kept deliberately tiny and dependency-free so tests can import it without
booting api_server (which starts every scheduler and opens the live database).

The peer IP alone cannot answer this. cloudflared runs on this host and
forwards app.agxtrade.com to http://127.0.0.1:3001 (hosting/config.yml), so a
visitor from the public internet arrives with client_address 127.0.0.1 and is
indistinguishable from the trader's own browser by IP. Two further signals
separate them, and both must agree:

  * cloudflared always stamps forwarding headers it does not let a client
    remove, so the presence of any of them means this request was relayed.
  * the Host header records which name the visitor actually asked for, which
    a relay cannot disguise as localhost.

Anything unrecognised is untrusted.
"""

from typing import Iterable, Mapping

# Set by cloudflared/Cloudflare on every tunnelled request. Compared
# lower-cased because HTTP header names are case-insensitive.
RELAY_HEADERS = frozenset(
    {
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "x-real-ip",
        "forwarded",
        "cf-connecting-ip",
        "cf-ray",
        "cf-ipcountry",
        "cf-access-jwt-assertion",
        "cf-access-authenticated-user-email",
    }
)

LOOPBACK_HOSTNAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]"})

# Written by gateway.py, read by api_server.py. The gateway is the only hop
# that can still tell a tunnel visitor from the trader's own browser: it
# forwards an allowlist of headers that excludes Host and every relay header,
# so api_server sees "127.0.0.1:3002, no proxy headers" either way.
#
# Unspoofable by construction: because the allowlist does not contain this
# name, a client-supplied copy is dropped before the gateway writes its own.
# Adding it to that allowlist would reopen the hole outright, which is why a
# test pins the allowlist's contents.
ORIGIN_HEADER = "X-AGX-Request-Origin"
ORIGIN_LOCAL = "local"
ORIGIN_RELAYED = "relayed"


def _is_loopback_ip(client_ip: str) -> bool:
    ip = str(client_ip or "").strip()
    if not ip:
        return False
    return ip in {"127.0.0.1", "::1"} or ip.startswith("127.")


def _hostname(host_header: str) -> str:
    """Strip the port from a Host header, keeping bracketed IPv6 intact."""
    host = str(host_header or "").strip().lower()
    if host.startswith("["):
        closing = host.find("]")
        return host[: closing + 1] if closing != -1 else ""
    return host.split(":", 1)[0]


def _header_names(headers: Mapping[str, str]) -> Iterable[str]:
    try:
        return [str(name).lower() for name in headers.keys()]
    except AttributeError:
        return []


def _lookup(headers: Mapping[str, str], name: str) -> str:
    """Case-insensitive fetch that works for dicts and http.client messages."""
    getter = getattr(headers, "get", None)
    if getter is not None:
        value = getter(name)
        if value:
            return str(value)
    wanted = name.lower()
    try:
        for key, value in headers.items():
            if str(key).lower() == wanted:
                return str(value)
    except AttributeError:
        pass
    return ""


def is_local_request(client_ip: str, headers: Mapping[str, str]) -> bool:
    """True only when the request was made from this machine, not relayed.

    Correct only at the hop that still sees the client's real headers - the
    gateway. Behind the gateway, use is_trusted_local instead.
    """
    if not _is_loopback_ip(client_ip):
        return False
    if RELAY_HEADERS.intersection(_header_names(headers)):
        return False
    return _hostname(_lookup(headers, "Host")) in LOOPBACK_HOSTNAMES


def origin_stamp(client_ip: str, headers: Mapping[str, str]) -> str:
    """The gateway's verdict on a request, to be forwarded to the pipeline."""
    return ORIGIN_LOCAL if is_local_request(client_ip, headers) else ORIGIN_RELAYED


def is_trusted_local(client_ip: str, headers: Mapping[str, str]) -> bool:
    """As is_local_request, but honours the gateway's stamp when present.

    Unstamped means nothing proxied this request, so it came straight to the
    pipeline's loopback-bound port - local tooling, the test suite, or the
    gateway's own SSE pump. Those are judged directly.
    """
    stamped = _lookup(headers, ORIGIN_HEADER).strip().lower()
    if stamped:
        return stamped == ORIGIN_LOCAL
    return is_local_request(client_ip, headers)
