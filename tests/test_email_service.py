from __future__ import annotations

from email_service import send_user_invite


RESEND_ENV = {
    "RESEND_API_KEY": "re_test_key",
    "INVITE_FROM_EMAIL": "AGX <noreply@agxtrade.com>",
    "APP_LOGIN_URL": "https://app.agxtrade.com",
}

SMTP_ENV = {
    "SMTP_USER": "sender@gmail.com",
    "SMTP_PASSWORD": "app-password",
    "INVITE_FROM_EMAIL": "AGX <sender@gmail.com>",
    "APP_LOGIN_URL": "https://app.agxtrade.com",
}


class RecordingHttp:
    """Stands in for the Resend HTTP call so tests never touch the network."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    def __call__(self, url: str, *, headers: dict, payload: dict) -> dict:
        self.calls.append({"url": url, "headers": headers, "payload": payload})
        if self.error is not None:
            raise self.error
        return {"id": "resend-message-id"}


class RecordingSmtp:
    """Stands in for the smtplib send so tests never touch the network."""

    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.error = error

    def __call__(self, **kwargs) -> None:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error


def test_invite_is_skipped_when_no_provider_is_configured():
    result = send_user_invite("new@example.com", "Temporary123", env={})

    assert result["sent"] is False
    assert result["provider"] == ""
    assert "not configured" in result["reason"].lower()


def test_resend_is_preferred_and_carries_the_temporary_password():
    http = RecordingHttp()

    result = send_user_invite(
        "new@example.com",
        "Temporary123",
        display_name="New User",
        env=RESEND_ENV,
        http_post=http,
    )

    assert result["sent"] is True
    assert result["provider"] == "resend"
    assert len(http.calls) == 1

    call = http.calls[0]
    assert call["url"] == "https://api.resend.com/emails"
    assert call["headers"]["Authorization"] == "Bearer re_test_key"
    assert call["payload"]["to"] == ["new@example.com"]
    assert call["payload"]["from"] == "AGX <noreply@agxtrade.com>"

    body = f"{call['payload']['html']}{call['payload']['text']}"
    assert "Temporary123" in body
    assert "https://app.agxtrade.com" in body
    assert "new@example.com" in body


def test_smtp_is_used_when_resend_is_not_configured():
    smtp = RecordingSmtp()

    result = send_user_invite(
        "new@example.com",
        "Temporary123",
        env=SMTP_ENV,
        smtp_send=smtp,
    )

    assert result["sent"] is True
    assert result["provider"] == "smtp"
    assert len(smtp.calls) == 1

    call = smtp.calls[0]
    assert call["host"] == "smtp.gmail.com"
    assert call["port"] == 587
    assert call["username"] == "sender@gmail.com"
    assert call["recipient"] == "new@example.com"
    assert "Temporary123" in call["body"]


def test_resend_failure_falls_back_to_smtp():
    http = RecordingHttp(error=RuntimeError("resend returned 500"))
    smtp = RecordingSmtp()

    result = send_user_invite(
        "new@example.com",
        "Temporary123",
        env={**RESEND_ENV, **SMTP_ENV},
        http_post=http,
        smtp_send=smtp,
    )

    assert result["sent"] is True
    assert result["provider"] == "smtp"
    assert len(http.calls) == 1
    assert len(smtp.calls) == 1


def test_every_provider_failing_reports_not_sent_without_raising():
    http = RecordingHttp(error=RuntimeError("resend returned 500"))
    smtp = RecordingSmtp(error=RuntimeError("smtp auth failed"))

    result = send_user_invite(
        "new@example.com",
        "Temporary123",
        env={**RESEND_ENV, **SMTP_ENV},
        http_post=http,
        smtp_send=smtp,
    )

    assert result["sent"] is False
    assert result["provider"] == ""
    assert "smtp auth failed" in result["reason"]


def test_blank_recipient_is_rejected_before_any_provider_is_called():
    http = RecordingHttp()

    result = send_user_invite("   ", "Temporary123", env=RESEND_ENV, http_post=http)

    assert result["sent"] is False
    assert http.calls == []
    assert "recipient" in result["reason"].lower()
