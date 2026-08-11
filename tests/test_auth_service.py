from __future__ import annotations

import sqlite3
import time

import pytest
from cryptography.fernet import Fernet

from auth_service import AuthenticationError, AuthorizationError, AuthService


@pytest.fixture
def auth(tmp_path):
    return AuthService(
        db_path=tmp_path / "auth.db",
        encryption_key=Fernet.generate_key(),
    )


def test_first_account_is_owner_and_public_bootstrap_closes(auth):
    assert auth.is_bootstrap_required() is True

    owner = auth.bootstrap_owner("Owner@Example.com", "SecurePass123", "Product Owner")

    assert owner["email"] == "owner@example.com"
    assert owner["isAdmin"] is True
    assert auth.is_bootstrap_required() is False
    with pytest.raises(AuthenticationError):
        auth.bootstrap_owner("other@example.com", "SecurePass123", "Other")


def test_admin_creates_user_and_temporary_password_must_change(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    created = auth.create_user(
        email="user@example.com",
        password="Temporary123",
        actor=owner,
        display_name="Workspace User",
    )

    assert created["isAdmin"] is False
    assert created["mustChangePassword"] is True
    user, session_token = auth.authenticate("user@example.com", "Temporary123")
    assert auth.user_for_session(session_token)["id"] == user["id"]

    auth.change_password(
        user,
        current_password="Temporary123",
        new_password="Replacement456",
    )

    assert auth.user_for_session(session_token) is None
    with pytest.raises(AuthenticationError):
        auth.authenticate("user@example.com", "Temporary123")
    refreshed, _ = auth.authenticate("user@example.com", "Replacement456")
    assert refreshed["mustChangePassword"] is False


def test_session_validation_does_not_wait_for_sqlite_writer(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    session_token = auth.create_session(owner)
    blocker = sqlite3.connect(auth.db_path, timeout=0.0)
    try:
        blocker.execute("BEGIN IMMEDIATE")
        blocker.execute(
            "UPDATE app_users SET updated_at = updated_at WHERE id = ?",
            (owner["id"],),
        )

        started = time.perf_counter()
        authenticated = auth.user_for_session(session_token)
        elapsed = time.perf_counter() - started

        assert authenticated["id"] == owner["id"]
        assert elapsed < 1.0
    finally:
        blocker.rollback()
        blocker.close()


def test_non_admin_cannot_create_or_list_users(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    user = auth.create_user(
        email="user@example.com",
        password="Temporary123",
        actor=owner,
        display_name="User",
    )

    with pytest.raises(AuthorizationError):
        auth.create_user(
            email="other@example.com",
            password="Temporary123",
            actor=user,
        )
    with pytest.raises(AuthorizationError):
        auth.list_users(user)


def test_additional_devices_require_admin_approval_and_sessions_are_bound(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    owner_device = auth.authorize_login_device(owner, user_agent="Admin Browser")
    user = auth.create_user(
        email="user@example.com",
        password="Temporary123",
        actor=owner,
        display_name="Workspace User",
    )
    verified = auth.verify_credentials("user@example.com", "Temporary123")

    first = auth.authorize_login_device(
        verified,
        user_agent="Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15",
        ip_address="192.0.2.10",
    )
    assert first["approved"] is True
    assert first["firstDevice"] is True
    assert first["device"]["label"] == "Safari on Mac"

    first_session = auth.create_session(verified, device_id=first["device"]["id"])
    assert auth.user_for_session(first_session, first["deviceToken"])["id"] == user["id"]

    second = auth.authorize_login_device(
        verified,
        user_agent="Mozilla/5.0 (iPhone) AppleWebKit/605.1.15 Version/18.0 Mobile Safari/604.1",
        ip_address="198.51.100.20",
    )
    assert second["approved"] is False
    assert second["status"] == "pending"
    assert second["firstDevice"] is False

    devices = auth.list_devices(owner, owner_device["deviceToken"])
    assert [item["id"] for item in devices["pendingRequests"]] == [second["device"]["id"]]
    assert [item["id"] for item in devices["approvedDevices"] if item["isCurrentDevice"]] == [owner_device["device"]["id"]]

    with pytest.raises(AuthorizationError):
        auth.list_devices(user)
    with pytest.raises(AuthorizationError):
        auth.decide_device_request(user, second["device"]["id"], "approve")

    decision = auth.decide_device_request(owner, second["device"]["id"], "approve")
    assert decision["status"] == "approved"
    approved = auth.authorize_login_device(
        verified,
        device_token=second["deviceToken"],
        user_agent="Mozilla/5.0 (iPhone) Mobile Safari/604.1",
        ip_address="198.51.100.20",
    )
    assert approved["approved"] is True

    second_session = auth.create_session(verified, device_id=approved["device"]["id"])
    assert auth.user_for_session(second_session, approved["deviceToken"])["id"] == user["id"]
    assert auth.user_for_session(second_session, "different-device-token") is None

    active_second_session = auth.create_session(verified, device_id=approved["device"]["id"])
    with pytest.raises(AuthenticationError, match="currently using"):
        auth.revoke_device(owner, owner_device["device"]["id"], owner_device["deviceToken"])
    assert auth.user_for_session(active_second_session, approved["deviceToken"])["id"] == user["id"]

    revoked = auth.revoke_device(owner, approved["device"]["id"], owner_device["deviceToken"])
    assert revoked["status"] == "revoked"
    assert auth.user_for_session(active_second_session, approved["deviceToken"]) is None

    requested_again = auth.authorize_login_device(
        verified,
        device_token=approved["deviceToken"],
        user_agent="Mozilla/5.0 (iPhone) Mobile Safari/604.1",
        ip_address="198.51.100.21",
    )
    assert requested_again["status"] == "pending"
    assert requested_again["approved"] is False


def test_rejected_device_can_request_approval_again(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    auth.authorize_login_device(owner, user_agent="Admin Browser")
    request = auth.authorize_login_device(owner, user_agent="Second Browser")

    assert auth.decide_device_request(owner, request["device"]["id"], "reject")["status"] == "rejected"
    retried = auth.authorize_login_device(
        owner,
        device_token=request["deviceToken"],
        user_agent="Second Browser",
    )
    assert retried["status"] == "pending"
    assert retried["device"]["id"] == request["device"]["id"]


def test_provider_credentials_are_encrypted_and_scoped_by_user(auth):
    owner = auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")
    other = auth.create_user(
        email="user@example.com",
        password="Temporary123",
        actor=owner,
        display_name="User",
    )
    auth.save_provider_credentials(
        owner,
        "schwab_market_data",
        {"client_id": "owner-key", "client_secret": "owner-super-secret"},
    )
    auth.save_provider_credentials(
        owner,
        "schwab_trading",
        {"client_id": "trading-key", "client_secret": "trading-super-secret"},
    )

    assert auth.provider_credentials(owner["id"], "schwab_market_data")["client_secret"] == "owner-super-secret"
    assert auth.provider_credentials(owner["id"], "schwab_trading")["client_secret"] == "trading-super-secret"
    assert auth.provider_credentials(other["id"], "schwab_market_data") == {}
    assert auth.provider_credentials(other["id"], "schwab_trading") == {}
    assert auth.provider_summary(owner)["schwabTrading"]["configured"] is True

    auth.save_provider_credentials(
        other,
        "schwab_trading",
        {"client_id": "user-trading-key", "client_secret": "user-trading-secret"},
    )
    assert auth.provider_summary(other)["schwabTrading"]["configured"] is True
    assert auth.provider_credentials(owner["id"], "schwab_trading")["client_id"] == "trading-key"

    with sqlite3.connect(auth.db_path) as connection:
        encrypted_payloads = connection.execute(
            "SELECT encrypted_payload FROM app_user_provider_credentials WHERE user_id = ?",
            (owner["id"],),
        ).fetchall()
    stored_ciphertext = " ".join(row[0] for row in encrypted_payloads)
    assert "owner-super-secret" not in stored_ciphertext
    assert "trading-super-secret" not in stored_ciphertext
