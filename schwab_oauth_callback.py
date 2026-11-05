from __future__ import annotations

import os
import socket
import ssl
import subprocess
import sys
import threading
import time
from html import escape
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from config import ARTIFACTS_DIR, settings
from data.schwab_client import SchwabClient
from provider_errors import safe_provider_message


CERT_PATH = ARTIFACTS_DIR / "localhost_schwab_cert.pem"
KEY_PATH = ARTIFACTS_DIR / "localhost_schwab_key.pem"
_CALLBACK_LOCK = threading.Lock()
_CALLBACK_SERVER: ThreadingHTTPServer | None = None
_CALLBACK_THREAD: threading.Thread | None = None

# Which login a Schwab redirect belongs to (2026-11-04, "renew both"). The
# listener used to finish EVERY redirect with the default Market Data client,
# so an Accounts & Trading login could never complete here and he pasted the
# address bar by hand every week. Each started login is now registered under
# the `state` Schwab echoes back, together with the exact client that began it
# (the right app key and token file) and, for "renew both", the next login's
# URL. Redirect targets only ever come from this registry - never from the
# request - so the success page cannot be turned into an open redirect. Only
# requests from this PC may register (api_server checks _is_local_request):
# the redirect can only ever land here, so a login started through the tunnel
# would be a login someone ELSE could get the owner to finish.
PENDING_TTL_SECONDS = 15 * 60
_PENDING_LOCK = threading.Lock()
_PENDING: dict[str, dict] = {}
# (token-file scope, profile) -> outcome, so a status reader sees only its own.
_RESULTS: dict[tuple[str, str], dict] = {}
# Set by main(): only the command-line flow may finish an unregistered login
# with the default client. Inside api_server an unknown code is refused.
_CLI_MODE = False

PROFILE_LABELS = {"market_data": "Market Data", "trading": "Accounts & Trading"}


def scope_of(client) -> str:
    """The token file a client writes - one per profile per user."""
    try:
        return str(Path(client.config.token_path).resolve())
    except Exception:  # noqa: BLE001
        return f"client:{id(client)}"


def _state_of(url: str) -> str:
    return parse_qs(urlparse(str(url or "")).query).get("state", [""])[0]


def _prune_locked(now: float) -> None:
    for state in [s for s, entry in _PENDING.items() if now - entry["at"] > PENDING_TTL_SECONDS]:
        _PENDING.pop(state, None)


def register_pending(client, authorization_url: str, profile: str, next_url: str | None = None) -> bool:
    """Remember which client began the login whose URL is ``authorization_url``.
    A newer login for the same token file replaces the older one."""
    state = _state_of(authorization_url)
    if not state:
        return False
    scope = scope_of(client)
    with _PENDING_LOCK:
        now = time.monotonic()
        _prune_locked(now)
        for old in [s for s, entry in _PENDING.items() if entry["scope"] == scope]:
            _PENDING.pop(old, None)
        _PENDING[state] = {"client": client, "profile": profile, "scope": scope, "next": next_url, "at": now}
    return True


def forget_pending(received_url: str) -> None:
    """A login finished some other way (the pasted address bar): stop waiting for it."""
    state = _state_of(received_url)
    if state:
        with _PENDING_LOCK:
            _PENDING.pop(state, None)


def pending_count() -> int:
    with _PENDING_LOCK:
        _prune_locked(time.monotonic())
        return len(_PENDING)


def renewal_result_for(client) -> dict | None:
    """The last login this process finished for this client's token file."""
    scope = scope_of(client)
    with _PENDING_LOCK:
        found = [result for (result_scope, _profile), result in _RESULTS.items() if result_scope == scope]
    return dict(found[-1]) if found else None


def _record(scope: str, profile: str, ok: bool, error: str = "") -> None:
    with _PENDING_LOCK:
        _RESULTS[(scope, profile)] = {
            "ok": ok,
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "error": error,
        }


def _page(body: str, refresh_to: str | None = None) -> str:
    head = "<meta charset='utf-8'><title>Schwab login</title>"
    if refresh_to:
        head += f"<meta http-equiv='refresh' content='2;url={escape(refresh_to, quote=True)}'>"
    return (
        f"<!doctype html><html><head>{head}</head>"
        "<body style='font-family:Segoe UI,Arial;padding:32px;background:#111;color:#eee'>"
        f"{body}</body></html>"
    )


_TRY_AGAIN = "<p>Close this tab and click <b>Renew both</b> in AGX Settings again.</p>"
_PASTE_INSTEAD = (
    "<p>Renewing from app.agxtrade.com? Copy this page's whole address from the top of the window, "
    "paste it into the matching callback URL box in AGX Settings and click Complete - right away, "
    "it works for only about 30 seconds.</p>"
)


def pasted_login_problem(received_url: str, profile: str, expected_state: str | None, other_state: str | None) -> str | None:
    """Why a pasted Schwab redirect cannot finish this profile's login, in the
    trader's words - or None when it belongs to the login this profile is
    waiting on. Checked before the code goes to Schwab, so a paste into the
    wrong box or from an older attempt says so instead of "mismatching_state"."""
    label = PROFILE_LABELS.get(profile, profile)
    other_label = PROFILE_LABELS["market_data" if profile == "trading" else "trading"]
    query = parse_qs(urlparse(str(received_url or "").strip()).query)
    state = query.get("state", [""])[0]
    if not query.get("code", [""])[0] or not state:
        return ("This is not a Schwab sign-in address. After signing in, copy the whole address of the page "
                "Schwab sends you to - it starts with https://127.0.0.1/?code=")
    if expected_state is None:
        if other_state is not None and state == other_state:
            return f"This address is from the {other_label} sign-in. Paste it into the {other_label} callback URL box instead."
        return f"No {label} sign-in is waiting. Click Renew both, sign in to Schwab, then paste the new address here."
    if state == expected_state:
        return None
    if other_state is not None and state == other_state:
        return f"This address is from the {other_label} sign-in. Paste it into the {other_label} callback URL box instead."
    return (f"This address is from an older {label} sign-in. Only the newest one works: "
            "click Renew both again and paste the address it gives you.")


def handle_callback(path: str) -> tuple[int, str, bool]:
    """One request to the callback listener -> (HTTP status, page, stop listening?).

    Pure apart from the token exchange, so the routing is testable without a socket.
    """
    if "#" in path:
        return 400, _page("<h1>Not a Schwab login</h1>"), False
    query = parse_qs(urlparse(path).query)
    code = query.get("code", [""])[0]
    state = query.get("state", [""])[0]
    with _PENDING_LOCK:
        _prune_locked(time.monotonic())
        entry = _PENDING.pop(state, None) if state else None
    if not code:
        error = query.get("error", [""])[0]
        if entry is not None:
            profile = entry["profile"]
            reason = safe_provider_message(error) if error else "Schwab returned no code."
            _record(entry["scope"], profile, False, reason)
            return 400, _page(
                f"<h1>Schwab did not approve {escape(PROFILE_LABELS.get(profile, profile))}</h1>"
                f"<p>{escape(reason)}</p>{_TRY_AGAIN}"
            ), True
        # Not a Schwab redirect (the browser's favicon request, a stray tab):
        # answer it and keep waiting for the real one.
        return 404, _page("<h1>Waiting for the Schwab login</h1><p>Nothing to do on this page.</p>"), False

    if entry is not None:
        client, profile, next_url = entry["client"], entry["profile"], entry.get("next")
    elif _CLI_MODE:
        client, profile, next_url = SchwabClient(), "market_data", None
    else:
        # A code AGX did not start or no longer waits for (an old tab, a
        # replayed URL, a login that timed out): never exchange it.
        return 400, _page(
            "<h1>This Schwab login is not the one AGX is waiting for</h1>"
            "<p>It may be from an older tab or it timed out.</p>" + _PASTE_INSTEAD + _TRY_AGAIN
        ), False
    scope = scope_of(client)
    label = escape(PROFILE_LABELS.get(profile, profile))
    callback = urlparse(client.config.redirect_uri or settings.schwab.redirect_uri)
    # Rebuilt from the parsed values only: the raw path could carry extras the
    # OAuth library would act on.
    received = f"{callback.scheme}://{callback.netloc}/?code={quote(code, safe='')}&state={quote(state, safe='')}"
    try:
        result = client.exchange_authorization_response(received)
    except Exception as exc:  # noqa: BLE001 - shown to the person logging in
        reason = safe_provider_message(exc)
        _record(scope, profile, False, reason)
        return 500, _page(
            f"<h1>Schwab {label} login did not finish</h1><p>{escape(reason)}</p>{_TRY_AGAIN}"
        ), True
    _record(scope, profile, bool(result.get("hasRefreshToken", True)))
    if next_url:
        return 200, _page(
            f"<h1>&#10003; {label} renewed</h1>"
            "<p>Next: the Accounts &amp; Trading app. Taking you there now...</p>"
            f"<p><a style='color:#7dd3fc' href='{escape(next_url, quote=True)}'>Continue</a></p>",
            refresh_to=next_url,
        ), False
    return 200, _page(
        f"<h1>&#10003; {label} renewed</h1>"
        "<p>All done. You can close this tab - AGX Settings shows the new expiry.</p>"
    ), True


def _certificate_ok(cert_bytes: bytes, key_bytes: bytes) -> bool:
    """Keep an existing pair only if it is ours in shape: a 127.0.0.1/localhost
    server certificate that cannot sign other certificates, matching its key,
    with more than 30 days left. Anything else (an old-style one, or one planted
    with CA powers) is replaced before anyone is asked to trust it."""
    import ipaddress

    cert = x509.load_pem_x509_certificate(cert_bytes)
    key = serialization.load_pem_private_key(key_bytes, password=None)
    if cert.public_key().public_numbers() != key.public_key().public_numbers():
        return False
    if cert.extensions.get_extension_for_class(x509.BasicConstraints).value.ca:
        return False
    usage = cert.extensions.get_extension_for_class(x509.KeyUsage).value
    if usage.key_cert_sign or usage.crl_sign:
        return False
    if ExtendedKeyUsageOID.SERVER_AUTH not in cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value:
        return False
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    if set(names.get_values_for_type(x509.DNSName)) != {"localhost"} \
            or set(names.get_values_for_type(x509.IPAddress)) != {ipaddress.ip_address("127.0.0.1")}:
        return False
    return cert.not_valid_after_utc - datetime.now(timezone.utc) > timedelta(days=30)


def _owner_only(path: Path) -> None:
    """Windows: only this user and SYSTEM may read or change the file (the
    artifacts folder otherwise lets every local account modify it)."""
    if os.name != "nt":
        return
    user = os.environ.get("USERNAME")
    if not user:
        return
    try:
        subprocess.run(
            ["icacls", str(path), "/inheritance:r", "/grant:r", f"{user}:F", "SYSTEM:F"],
            capture_output=True, timeout=20, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception:  # noqa: BLE001 - best effort; the certificate still works
        pass


def ensure_localhost_certificate() -> None:
    """The listener's certificate (3-year life). Trusting it once in Windows
    (scripts/trust_schwab_callback_cert.ps1) removes Chrome's warning page."""
    if CERT_PATH.exists() and KEY_PATH.exists():
        try:
            if _certificate_ok(CERT_PATH.read_bytes(), KEY_PATH.read_bytes()):
                return
        except Exception:  # noqa: BLE001 - unreadable or old-style: make a new one
            pass

    import ipaddress

    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "AgenticAI Trading Local"),
            x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1"),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(timezone.utc) - timedelta(days=1))
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=3 * 365))
        .add_extension(
            x509.SubjectAlternativeName([x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]),
            critical=False,
        )
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_encipherment=True, content_commitment=False,
                data_encipherment=False, key_agreement=False, key_cert_sign=False,
                crl_sign=False, encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    # Key first, certificate last, each through a temporary file: a reader never
    # sees a new certificate beside an old key.
    for path, data in (
        (KEY_PATH, key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )),
        (CERT_PATH, cert.public_bytes(serialization.Encoding.PEM)),
    ):
        temporary = path.with_name(path.name + ".tmp")
        temporary.write_bytes(data)
        _owner_only(temporary)
        os.replace(temporary, path)


def certificate_trusted() -> bool | None:
    """Is the listener's certificate in this Windows user's trusted roots?

    When it is, Chrome opens the redirect page without "Your connection is not
    private". None off Windows or when the certificate cannot be read.
    """
    global _TRUST_CACHE
    if not hasattr(ssl, "enum_certificates") or not CERT_PATH.exists():
        return None
    now = time.monotonic()
    if _TRUST_CACHE and now - _TRUST_CACHE[0] < 60:   # the status poll asks every few seconds
        return _TRUST_CACHE[1]
    try:
        ours = x509.load_pem_x509_certificate(CERT_PATH.read_bytes()).public_bytes(serialization.Encoding.DER)
        value = any(der == ours for der, _encoding, _trust in ssl.enum_certificates("ROOT"))
    except Exception:  # noqa: BLE001
        value = None
    _TRUST_CACHE = (now, value)
    return value


_TRUST_CACHE: tuple[float, bool | None] | None = None


class SchwabCallbackHandler(BaseHTTPRequestHandler):
    server_version = "SchwabOAuthCallback/1.0"
    # A connection that never speaks (Chrome holding a socket open behind its
    # certificate warning) times out in its own thread instead of blocking the
    # real redirect.
    timeout = 15

    def handle(self) -> None:
        handshake = getattr(self.connection, "do_handshake", None)
        if handshake is not None:
            try:
                handshake()                  # deferred off the accept loop (see _build_callback_server)
            except (OSError, ssl.SSLError):
                return                       # an untrusted-certificate abort or a silent socket
        super().handle()

    def do_GET(self) -> None:
        status, page, stop = handle_callback(self.path)
        try:
            self._send_html(status, page)
        finally:
            if stop:
                # The command-line server is not the registered one: just stop it.
                target = self.server.shutdown if _CLI_MODE else (lambda: _stop_if_idle(self.server))
                threading.Thread(target=target, daemon=True).start()

    def log_message(self, format: str, *args) -> None:
        return

    def _send_html(self, status: int, page: str) -> None:
        encoded = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


class _CallbackServer(ThreadingHTTPServer):
    daemon_threads = True
    # HTTPServer sets SO_REUSEADDR, and on Windows that lets a second process
    # bind 127.0.0.1:443 silently and take the redirects. Refuse to share.
    allow_reuse_address = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address) -> None:
        return  # a refused certificate or a dropped preconnect is not worth a traceback


def _build_callback_server() -> ThreadingHTTPServer:
    ensure_localhost_certificate()
    callback = urlparse(settings.schwab.redirect_uri)
    if callback.scheme != "https" or callback.hostname != "127.0.0.1":
        raise RuntimeError("SCHWAB_REDIRECT_URI must use https://127.0.0.1 and exactly match the Schwab app setting.")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(CERT_PATH), keyfile=str(KEY_PATH))
    httpd = _CallbackServer(("127.0.0.1", callback.port or 443), SchwabCallbackHandler)
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True, do_handshake_on_connect=False)
    return httpd


def callback_listener_status() -> bool:
    return bool(_CALLBACK_SERVER is not None and _CALLBACK_THREAD and _CALLBACK_THREAD.is_alive())


def _stop_if_idle(httpd) -> bool:
    """Stop listening when no login is waiting. Decided under the same lock as
    start, and the server is detached first, so a login registered meanwhile
    gets a fresh listener instead of one that is shutting down."""
    global _CALLBACK_SERVER
    with _CALLBACK_LOCK:
        if _CALLBACK_SERVER is not httpd or pending_count() != 0:
            return False
        _CALLBACK_SERVER = None
    httpd.shutdown()
    return True


def _reap_when_idle(httpd) -> None:
    """An abandoned login ages out after PENDING_TTL_SECONDS; then stop."""
    while _CALLBACK_SERVER is httpd:
        time.sleep(30)
        if _stop_if_idle(httpd):
            return


def start_callback_listener() -> bool:
    global _CALLBACK_SERVER, _CALLBACK_THREAD
    with _CALLBACK_LOCK:
        if callback_listener_status():
            return True
        stopping = _CALLBACK_THREAD
    if stopping is not None and stopping.is_alive():
        stopping.join(5)   # a detached server finishing its shutdown releases the port
    with _CALLBACK_LOCK:
        if callback_listener_status():
            return True
        httpd = _build_callback_server()
        _CALLBACK_SERVER = httpd

        def serve() -> None:
            global _CALLBACK_SERVER, _CALLBACK_THREAD
            try:
                httpd.serve_forever(poll_interval=0.5)
            finally:
                httpd.server_close()
                with _CALLBACK_LOCK:
                    if _CALLBACK_SERVER is httpd:
                        _CALLBACK_SERVER = None
                    if _CALLBACK_THREAD is threading.current_thread():
                        _CALLBACK_THREAD = None

        _CALLBACK_THREAD = threading.Thread(target=serve, name="schwab-oauth-callback", daemon=True)
        _CALLBACK_THREAD.start()
        threading.Thread(target=_reap_when_idle, args=(httpd,), name="schwab-oauth-reaper", daemon=True).start()
        return True


def start_callback_listener_safely() -> bool:
    """True when the listener is up; False (paste the address bar instead) when
    port 443 cannot be opened. Never raises into a request handler."""
    try:
        return start_callback_listener()
    except Exception as exc:  # noqa: BLE001
        print(f"[schwab-oauth] callback listener not started: {exc}", file=sys.stderr)
        return False


def main() -> None:
    global _CLI_MODE
    _CLI_MODE = True
    client = SchwabClient()
    print("Open this Schwab authorization URL:")
    print(client.begin_authorization())
    print("")
    print(f"Waiting on {settings.schwab.redirect_uri} for the Schwab redirect...")
    print("Your browser may show a certificate warning. Choose Advanced/Continue for localhost.")
    httpd = _build_callback_server()
    httpd.serve_forever()
    httpd.server_close()
    print("Callback server stopped.")


if __name__ == "__main__":
    main()
