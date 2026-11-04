"""The PC's Schwab login catcher (2026-11-04, "renew both").

It used to finish every redirect with the default Market Data client, so the
Accounts & Trading login always failed there and was pasted by hand weekly.
"""
from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import schwab_oauth_callback as cb

API_SOURCE = (Path(__file__).resolve().parents[1] / "api_server.py").read_text(encoding="utf-8")


class FakeClient:
    def __init__(self, name, fail=None, token_path=None):
        self.name = name
        self.fail = fail
        self.config = SimpleNamespace(redirect_uri="https://127.0.0.1", token_path=token_path or f"tokens/{name}.json")
        self.received = []

    def exchange_authorization_response(self, url):
        self.received.append(url)
        if self.fail:
            raise RuntimeError(self.fail)
        return {"hasAccessToken": True, "hasRefreshToken": True}


AUTH = "https://api.schwabapi.com/v1/oauth/authorize?client_id=x&redirect_uri=https%3A%2F%2F127.0.0.1&state={}"


@pytest.fixture(autouse=True)
def clean_registry(monkeypatch):
    cb._PENDING.clear()
    cb._RESULTS.clear()
    monkeypatch.setattr(cb, "_CLI_MODE", False)
    yield
    cb._PENDING.clear()
    cb._RESULTS.clear()


def test_each_redirect_is_finished_by_the_client_that_started_it_then_walks_to_the_next():
    market, trading = FakeClient("market"), FakeClient("trading")
    trading_url = AUTH.format("S2") + "&scope=readonly"
    cb.register_pending(market, AUTH.format("S1"), "market_data", next_url=trading_url)
    cb.register_pending(trading, trading_url, "trading")

    status, page, stop = cb.handle_callback("/?code=C1%40&state=S1")
    assert (status, stop) == (200, False)
    assert market.received == ["https://127.0.0.1/?code=C1%40&state=S1"] and trading.received == []
    assert "Market Data renewed" in page
    assert "http-equiv='refresh'" in page and "state=S2&amp;scope=readonly" in page   # escaped, from the registry

    status, page, stop = cb.handle_callback("/?code=C2&state=S2&extra=1")
    assert (status, stop) == (200, True)                     # nothing left waiting: stop listening
    assert trading.received == ["https://127.0.0.1/?code=C2&state=S2"]   # rebuilt: only code + state
    assert "Accounts &amp; Trading renewed" in page and "refresh" not in page
    assert cb.renewal_result_for(market)["ok"] and cb.renewal_result_for(trading)["ok"]


def test_results_belong_to_one_token_file_only():
    mine, theirs = FakeClient("mine"), FakeClient("theirs", fail="invalid_grant")
    cb.register_pending(theirs, AUTH.format("T1"), "market_data")
    cb.handle_callback("/?code=C&state=T1")
    assert cb.renewal_result_for(theirs)["ok"] is False
    assert cb.renewal_result_for(mine) is None              # another user's failure is not mine


def test_a_newer_login_for_the_same_token_file_replaces_the_older_one():
    first = FakeClient("market")
    cb.register_pending(first, AUTH.format("OLD"), "market_data")
    cb.register_pending(FakeClient("market"), AUTH.format("NEW"), "market_data")
    assert set(cb._PENDING) == {"NEW"}


def test_a_stray_request_is_answered_and_the_login_keeps_waiting():
    market = FakeClient("market")
    cb.register_pending(market, AUTH.format("S1"), "market_data")
    status, _page, stop = cb.handle_callback("/favicon.ico")
    assert (status, stop) == (404, False)
    assert cb.pending_count() == 1 and market.received == []


def test_a_declined_login_is_reported_and_dropped():
    market = FakeClient("market")
    cb.register_pending(market, AUTH.format("S1"), "market_data")
    status, page, stop = cb.handle_callback("/?error=access_denied&state=S1")
    assert (status, stop) == (400, True)
    assert "access_denied" in page
    assert cb.renewal_result_for(market)["ok"] is False


def test_inside_the_server_an_unknown_code_is_never_exchanged(monkeypatch):
    """Not even with the house client when nothing is registered - only the
    command-line flow may do that."""
    market = FakeClient("market")
    cb.register_pending(market, AUTH.format("S1"), "market_data")
    status, _page, stop = cb.handle_callback("/?code=OLD&state=STALE&next=https://evil.example")
    assert (status, stop) == (400, False)
    assert market.received == [] and cb.pending_count() == 1
    cb._PENDING.clear()
    house = []
    monkeypatch.setattr(cb, "SchwabClient", lambda: house.append(1) or FakeClient("house"))
    assert cb.handle_callback("/?code=C&state=ANY")[0] == 400 and house == []
    monkeypatch.setattr(cb, "_CLI_MODE", True)
    assert cb.handle_callback("/?code=C&state=ANY")[0] == 200 and house == [1]


def test_a_fragment_is_refused_before_any_exchange():
    market = FakeClient("market")
    cb.register_pending(market, AUTH.format("S1"), "market_data")
    assert cb.handle_callback("/?code=C&state=S1#access_token=PLANTED")[0] == 400
    assert market.received == [] and cb.pending_count() == 1


def test_a_failed_exchange_shows_the_reason_and_records_it():
    trading = FakeClient("trading", fail="invalid_grant")
    cb.register_pending(trading, AUTH.format("S9"), "trading")
    status, page, stop = cb.handle_callback("/?code=C&state=S9")
    assert (status, stop) == (500, True)
    assert "invalid_grant" in page
    assert cb.renewal_result_for(trading)["error"] == "invalid_grant"


def test_a_pasted_login_stops_the_wait_and_an_abandoned_one_ages_out(monkeypatch):
    cb.register_pending(FakeClient("market"), AUTH.format("S1"), "market_data")
    cb.forget_pending("https://127.0.0.1/?code=C&state=S1")
    assert cb.pending_count() == 0
    clock = [1000.0]
    monkeypatch.setattr(cb.time, "monotonic", lambda: clock[0])
    cb.register_pending(FakeClient("trading"), AUTH.format("S2"), "trading")
    clock[0] += cb.PENDING_TTL_SECONDS + 1
    assert cb.pending_count() == 0


def test_logins_register_only_for_a_browser_on_this_pc():
    for route in ('"/api/schwab/oauth/start"', '"/api/schwab/oauth/start-both"'):
        start = API_SOURCE.find(f"if parsed.path == {route}:")
        body = API_SOURCE[start: API_SOURCE.find("if parsed.path ==", start + 10)]
        assert body.index("self._is_local_request()") < body.index("register_pending("), route
        assert body.index("self._is_local_request()") < body.index("start_callback_listener_safely()"), route


def _plain_server(monkeypatch):
    """The real listener lifecycle on a free port, without TLS."""
    port = []

    def build():
        httpd = cb._CallbackServer(("127.0.0.1", 0), cb.SchwabCallbackHandler)
        port.append(httpd.server_address[1])
        return httpd

    monkeypatch.setattr(cb, "_build_callback_server", build)
    return port


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def _wait_until(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def test_the_listener_stops_when_done_and_a_new_login_gets_a_fresh_one(monkeypatch):
    ports = _plain_server(monkeypatch)
    cb.register_pending(FakeClient("market"), AUTH.format("S1"), "market_data")
    assert cb.start_callback_listener()
    silent = socket.create_connection(("127.0.0.1", ports[-1]))   # a socket that never speaks
    try:
        assert _get(ports[-1], "/favicon.ico") == 404             # not blocked by the silent one
        assert _get(ports[-1], "/?code=C&state=S1") == 200
        assert _wait_until(lambda: not cb.callback_listener_status())
        cb.register_pending(FakeClient("trading"), AUTH.format("S2"), "trading")
        assert cb.start_callback_listener() and len(ports) == 2
        assert _get(ports[-1], "/?code=C&state=S2") == 200
        assert _wait_until(lambda: not cb.callback_listener_status())
    finally:
        silent.close()


def test_the_port_is_never_shared_with_a_second_listener():
    first = cb._CallbackServer(("127.0.0.1", 0), cb.SchwabCallbackHandler)
    try:
        with pytest.raises(OSError):
            cb._CallbackServer(("127.0.0.1", first.server_address[1]), cb.SchwabCallbackHandler)
    finally:
        first.server_close()


def _old_style_pair(ca=False):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    import ipaddress

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.now(timezone.utc)
    builder = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
               .serial_number(1).not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=200)))
    if ca:   # a planted certificate with CA powers, otherwise shaped like ours
        builder = (builder
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost"),
                                                               x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
                   .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
                   .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False))
    cert = builder.sign(key, hashes.SHA256())
    return (cert.public_bytes(serialization.Encoding.PEM),
            key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))


@pytest.mark.parametrize("ca", [False, True])
def test_an_old_or_planted_certificate_is_replaced_with_a_server_only_one(tmp_path, monkeypatch, ca):
    from cryptography import x509

    monkeypatch.setattr(cb, "CERT_PATH", tmp_path / "cert.pem")
    monkeypatch.setattr(cb, "KEY_PATH", tmp_path / "key.pem")
    monkeypatch.setattr(cb, "_owner_only", lambda path: None)
    cert_pem, key_pem = _old_style_pair(ca=ca)
    cb.CERT_PATH.write_bytes(cert_pem)
    cb.KEY_PATH.write_bytes(key_pem)
    cb.ensure_localhost_certificate()
    fresh = x509.load_pem_x509_certificate(cb.CERT_PATH.read_bytes())
    assert fresh.serial_number != 1
    assert fresh.extensions.get_extension_for_class(x509.BasicConstraints).value.ca is False
    assert fresh.not_valid_after_utc - datetime.now(timezone.utc) > timedelta(days=3 * 365 - 2)
    kept = cb.CERT_PATH.read_bytes()
    cb.ensure_localhost_certificate()
    assert cb.CERT_PATH.read_bytes() == kept                 # a good one is left alone
    assert not list(tmp_path.glob("*.tmp"))
