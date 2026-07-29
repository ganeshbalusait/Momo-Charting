from __future__ import annotations

import ssl
import threading
from html import escape
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from config import ARTIFACTS_DIR, settings
from data.schwab_client import SchwabClient


CERT_PATH = ARTIFACTS_DIR / "localhost_schwab_cert.pem"
KEY_PATH = ARTIFACTS_DIR / "localhost_schwab_key.pem"
_CALLBACK_LOCK = threading.Lock()
_CALLBACK_SERVER: HTTPServer | None = None
_CALLBACK_THREAD: threading.Thread | None = None
_CALLBACK_PROFILE = "market_data"


def _profile_config(profile: str):
    return settings.schwab_trading if profile == "trading" else settings.schwab


def ensure_localhost_certificate() -> None:
    if CERT_PATH.exists() and KEY_PATH.exists():
        return

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
        .not_valid_after(datetime.now(timezone.utc) + timedelta(days=365))
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName("localhost"),
                    x509.IPAddress(__import__("ipaddress").ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    KEY_PATH.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))


class SchwabCallbackHandler(BaseHTTPRequestHandler):
    server_version = "SchwabOAuthCallback/1.0"

    def do_GET(self) -> None:
        query = parse_qs(urlparse(self.path).query)
        code = query.get("code", [""])[0]
        if not code:
            self._send_html(400, "<h1>Missing Schwab code</h1><p>Open the authorization URL again.</p>")
            return

        try:
            profile = _CALLBACK_PROFILE
            configured_callback = urlparse(_profile_config(profile).redirect_uri)
            received_url = f"{configured_callback.scheme}://{configured_callback.netloc}{self.path}"
            result = SchwabClient(profile).exchange_authorization_response(received_url)
            self._send_html(
                200,
                f"<h1>Schwab {escape(profile.replace('_', ' '))} connected</h1>"
                f"<p>Access token: {'yes' if result.get('hasAccessToken') else 'no'}</p>"
                f"<p>Refresh token: {'yes' if result.get('hasRefreshToken') else 'no'}</p>"
                "<p>Token is now managed by schwab-py.</p>"
                "<p>You can close this tab.</p>",
            )
        except Exception as exc:
            self._send_html(
                500,
                "<h1>Schwab token exchange failed</h1>"
                f"<pre>{escape(str(exc))}</pre>"
                "<p>Close this tab and try a fresh authorization URL.</p>",
            )
        finally:
            threading.Thread(target=self.server.shutdown, daemon=True).start()

    def log_message(self, format: str, *args) -> None:
        return

    def _send_html(self, status: int, body: str) -> None:
        page = f"<!doctype html><html><body style='font-family:Segoe UI,Arial;padding:32px'>{body}</body></html>"
        encoded = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


def _build_callback_server(profile: str = "market_data") -> HTTPServer:
    ensure_localhost_certificate()
    callback = urlparse(_profile_config(profile).redirect_uri)
    if callback.scheme != "https" or callback.hostname != "127.0.0.1":
        raise RuntimeError("SCHWAB_REDIRECT_URI must use https://127.0.0.1 and exactly match the Schwab app setting.")
    httpd = HTTPServer(("127.0.0.1", callback.port or 443), SchwabCallbackHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=str(CERT_PATH), keyfile=str(KEY_PATH))
    httpd.socket = context.wrap_socket(httpd.socket, server_side=True)
    return httpd


def callback_listener_status() -> bool:
    return bool(_CALLBACK_THREAD and _CALLBACK_THREAD.is_alive())


def start_callback_listener(profile: str = "market_data") -> bool:
    global _CALLBACK_SERVER, _CALLBACK_THREAD, _CALLBACK_PROFILE
    normalized_profile = "trading" if str(profile or "").strip().lower() == "trading" else "market_data"
    with _CALLBACK_LOCK:
        if callback_listener_status():
            return _CALLBACK_PROFILE == normalized_profile
        _CALLBACK_PROFILE = normalized_profile
        httpd = _build_callback_server(normalized_profile)
        _CALLBACK_SERVER = httpd

        def serve() -> None:
            global _CALLBACK_SERVER, _CALLBACK_THREAD
            try:
                httpd.serve_forever()
            finally:
                httpd.server_close()
                with _CALLBACK_LOCK:
                    _CALLBACK_SERVER = None
                    _CALLBACK_THREAD = None

        _CALLBACK_THREAD = threading.Thread(target=serve, name="schwab-oauth-callback", daemon=True)
        _CALLBACK_THREAD.start()
        return True


def main() -> None:
    client = SchwabClient()
    print("Open this Schwab authorization URL:")
    print(client.begin_authorization())
    print("")
    print(f"Waiting on {settings.schwab.redirect_uri} for the Schwab redirect...")
    print("Your browser may show a certificate warning. Choose Advanced/Continue for localhost.")
    httpd = _build_callback_server("market_data")
    httpd.serve_forever()
    httpd.server_close()
    print("Callback server stopped.")


if __name__ == "__main__":
    main()
