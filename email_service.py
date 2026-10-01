"""Outbound invite email for newly created accounts.

Two providers, tried in order: Resend (HTTP API) first, then SMTP — which
defaults to Gmail. Both are optional; with neither configured the caller is
told the invite was skipped rather than being handed an exception.

Deliberately dependency-free: Resend's send endpoint is a single JSON POST, so
stdlib urllib covers it and nothing new lands in requirements.txt.
"""

from __future__ import annotations

import json
import os
import smtplib
import ssl
import urllib.request
from email.message import EmailMessage
from html import escape

RESEND_ENDPOINT = "https://api.resend.com/emails"
DEFAULT_SMTP_HOST = "smtp.gmail.com"
DEFAULT_SMTP_PORT = 587
DEFAULT_LOGIN_URL = "https://app.agxtrade.com"
DEFAULT_RESEND_SENDER = "AGX <onboarding@resend.dev>"
REQUEST_TIMEOUT_SECONDS = 15
SUBJECT = "Your AGX account is ready"


def _clean(value) -> str:
    return str(value or "").strip()


def _int(value, default: int) -> int:
    try:
        return int(_clean(value))
    except (TypeError, ValueError):
        return default


def _result(sent: bool, provider: str, reason: str) -> dict:
    return {"sent": sent, "provider": provider, "reason": reason}


def _sender(settings, *, fallback: str) -> str:
    return _clean(settings.get("INVITE_FROM_EMAIL")) or fallback


def _text_body(display_name: str, recipient: str, temp_password: str, login_url: str) -> str:
    greeting = f"Hi {display_name}," if display_name else "Hi,"
    return (
        f"{greeting}\n\n"
        "An AGX account has been created for you.\n\n"
        f"Sign in at: {login_url}\n"
        f"Email: {recipient}\n"
        f"Temporary password: {temp_password}\n\n"
        "You will be asked to choose your own password the first time you sign in.\n\n"
        "If you were not expecting this email, you can ignore it.\n"
    )


def _html_body(display_name: str, recipient: str, temp_password: str, login_url: str) -> str:
    greeting = f"Hi {escape(display_name)}," if display_name else "Hi,"
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.6;color:#1a1a1a">'
        f"<p>{greeting}</p>"
        "<p>An AGX account has been created for you.</p>"
        '<table cellpadding="6" style="border-collapse:collapse;margin:16px 0">'
        f'<tr><td style="color:#666">Sign in at</td>'
        f'<td><a href="{escape(login_url)}">{escape(login_url)}</a></td></tr>'
        f'<tr><td style="color:#666">Email</td><td>{escape(recipient)}</td></tr>'
        f'<tr><td style="color:#666">Temporary password</td>'
        f'<td><code>{escape(temp_password)}</code></td></tr>'
        "</table>"
        "<p>You will be asked to choose your own password the first time you sign in.</p>"
        '<p style="color:#666;font-size:13px">If you were not expecting this email, '
        "you can ignore it.</p>"
        "</div>"
    )


def _post_json(url: str, *, headers: dict, payload: dict) -> dict:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", **headers},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read().decode("utf-8", "replace")
    return json.loads(raw) if raw.strip() else {}


def _smtp_send(
    *,
    host: str,
    port: int,
    username: str,
    password: str,
    sender: str,
    recipient: str,
    subject: str,
    body: str,
) -> None:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = recipient
    message["Subject"] = subject
    message.set_content(body)
    with smtplib.SMTP(host, port, timeout=REQUEST_TIMEOUT_SECONDS) as server:
        server.starttls(context=ssl.create_default_context())
        server.login(username, password)
        server.send_message(message)


def send_user_invite(
    email: str,
    temp_password: str,
    *,
    display_name: str = "",
    env=None,
    http_post=None,
    smtp_send=None,
) -> dict:
    """Email a new user their temporary password.

    Returns ``{"sent": bool, "provider": str, "reason": str}`` and never raises:
    the account exists by the time this is called, so a delivery failure must
    not be allowed to unwind account creation.
    """
    settings = os.environ if env is None else env

    recipient = _clean(email)
    if not recipient or "@" not in recipient:
        return _result(False, "", "No valid recipient address.")

    password = _clean(temp_password)
    if not password:
        return _result(False, "", "No temporary password to send.")

    name = _clean(display_name)
    login_url = _clean(settings.get("APP_LOGIN_URL")) or DEFAULT_LOGIN_URL
    text = _text_body(name, recipient, password, login_url)
    html = _html_body(name, recipient, password, login_url)

    failures: list[str] = []

    resend_key = _clean(settings.get("RESEND_API_KEY"))
    if resend_key:
        try:
            (http_post or _post_json)(
                RESEND_ENDPOINT,
                headers={"Authorization": f"Bearer {resend_key}"},
                payload={
                    "from": _sender(settings, fallback=DEFAULT_RESEND_SENDER),
                    "to": [recipient],
                    "subject": SUBJECT,
                    "text": text,
                    "html": html,
                },
            )
            return _result(True, "resend", "")
        except Exception as error:  # noqa: BLE001 - delivery must never raise
            failures.append(f"resend: {error}")

    smtp_user = _clean(settings.get("SMTP_USER"))
    smtp_password = _clean(settings.get("SMTP_PASSWORD"))
    if smtp_user and smtp_password:
        try:
            (smtp_send or _smtp_send)(
                host=_clean(settings.get("SMTP_HOST")) or DEFAULT_SMTP_HOST,
                port=_int(settings.get("SMTP_PORT"), DEFAULT_SMTP_PORT),
                username=smtp_user,
                password=smtp_password,
                sender=_sender(settings, fallback=smtp_user),
                recipient=recipient,
                subject=SUBJECT,
                body=text,
            )
            return _result(True, "smtp", "")
        except Exception as error:  # noqa: BLE001 - delivery must never raise
            failures.append(f"smtp: {error}")

    if failures:
        return _result(False, "", "; ".join(failures))
    return _result(False, "", "Email delivery is not configured.")
