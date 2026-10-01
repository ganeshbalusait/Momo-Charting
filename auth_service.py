from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator
from uuid import uuid4

from cryptography.fernet import Fernet, InvalidToken

from config import ARTIFACTS_DIR, DATABASE_PATH


PASSWORD_ITERATIONS = 600_000
SESSION_LIFETIME_DAYS = 30
# The API gate resolves identity on EVERY /api request. Re-reading SQLite
# there cost 13-36s per request under load (measured 2026-08-21 RTH), so the
# resolved identity is memoized in-process for this long. Writes invalidate it
# immediately, so the window only ever delays an out-of-band change to a row
# nothing in this process touched.
IDENTITY_CACHE_SECONDS = 15.0
IDENTITY_CACHE_MAX_ENTRIES = 512
SESSION_ACTIVITY_TOUCH_SECONDS = 60.0
SESSION_ACTIVITY_BUSY_TIMEOUT_MS = 250
LOCAL_ENCRYPTION_KEY_PATH = ARTIFACTS_DIR / "user_credentials.key"


class AuthenticationError(ValueError):
    pass


class AuthorizationError(PermissionError):
    pass


class AuthService:
    """User, session, and encrypted provider-credential storage."""

    def __init__(self, db_path=DATABASE_PATH, encryption_key: str | bytes | None = None) -> None:
        self.db_path = Path(db_path)
        self._connection_lock = threading.RLock()
        self._identity_cache: dict[tuple, tuple[float, int, object]] = {}
        self._identity_cache_lock = threading.Lock()
        self._identity_generation = 0
        self._session_touch_lock = threading.Lock()
        self._session_touch_times: dict[str, float] = {}
        self._cipher = Fernet(self._resolve_encryption_key(encryption_key))
        self.initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with self._connection_lock:
            connection = sqlite3.connect(self.db_path, timeout=30.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA busy_timeout = 30000")
            connection.execute("PRAGMA foreign_keys = ON")
            try:
                yield connection
                # Any INSERT/UPDATE/DELETE here can change who a token or an
                # email resolves to. total_changes is per-connection and every
                # block opens its own, so this reads exactly as "did this block
                # write?" - which beats hunting each mutating call site and
                # silently missing one. On an exception the connection closes
                # without committing, so skipping invalidation is correct.
                if connection.total_changes:
                    self._invalidate_identity_cache()
                connection.commit()
            finally:
                connection.close()

    def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_salt TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'user',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(app_users)").fetchall()
            }
            if "must_change_password" not in columns:
                connection.execute(
                    "ALTER TABLE app_users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 0"
                )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_user_devices (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL,
                    label TEXT NOT NULL,
                    user_agent TEXT NOT NULL DEFAULT '',
                    ip_address TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'pending',
                    requested_at TEXT NOT NULL,
                    approved_at TEXT,
                    approved_by TEXT,
                    last_seen_at TEXT,
                    revoked_at TEXT,
                    UNIQUE(user_id, token_hash),
                    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE,
                    FOREIGN KEY(approved_by) REFERENCES app_users(id) ON DELETE SET NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_user_sessions (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_hash TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    device_id TEXT,
                    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
                )
                """
            )
            session_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(app_user_sessions)").fetchall()
            }
            if "device_id" not in session_columns:
                connection.execute("ALTER TABLE app_user_sessions ADD COLUMN device_id TEXT")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS app_user_provider_credentials (
                    user_id TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    encrypted_payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(user_id, provider),
                    FOREIGN KEY(user_id) REFERENCES app_users(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_sessions_user_id ON app_user_sessions(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_sessions_expires_at ON app_user_sessions(expires_at)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_sessions_device_id ON app_user_sessions(device_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_devices_user_id ON app_user_devices(user_id)"
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_app_devices_status ON app_user_devices(status)"
            )

    def bootstrap_required(self) -> bool:
        # /api/auth/status calls this on every poll alongside _session_user,
        # and py-spy caught three request threads queued on it at once.
        cache_key = ("bootstrap_required",)
        cached = self._identity_cached(cache_key)
        if cached is not None:
            return bool(cached)
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) AS count FROM app_users").fetchone()
        required = int(row["count"] or 0) == 0
        self._remember_identity(cache_key, required)
        return required

    def is_bootstrap_required(self) -> bool:
        return self.bootstrap_required()

    def create_owner(self, email: str, password: str, display_name: str = "") -> dict:
        return self._create_user_record(
            email,
            password,
            display_name=display_name,
            role="admin",
            must_change_password=False,
            require_empty=True,
        )

    def bootstrap_owner(self, email: str, password: str, display_name: str = "") -> dict:
        return self.create_owner(email, password, display_name)

    def create_user(
        self,
        email: str = "",
        password: str = "",
        *,
        actor: dict | None = None,
        display_name: str = "",
        role: str = "user",
    ) -> dict:
        self.require_admin(actor)
        # No password is the normal case now: adding someone is entering their
        # email, and Cloudflare Access proves who they are. A password is only
        # set when one is explicitly supplied (bootstrap, break-glass).
        wants_password = bool(str(password or "").strip())
        return self._create_user_record(
            email,
            password,
            display_name=display_name,
            role=role,
            must_change_password=wants_password,
        )

    def _create_user_record(
        self,
        email: str,
        password: str,
        *,
        display_name: str,
        role: str,
        must_change_password: bool,
        require_empty: bool = False,
    ) -> dict:
        normalized_email = self._normalize_email(email)
        clean_name = str(display_name or "").strip() or normalized_email.split("@", 1)[0]
        normalized_role = str(role or "user").strip().lower()
        if normalized_role not in {"admin", "user"}:
            raise AuthenticationError("Role must be admin or user.")
        # An empty password means "this account has no password" - not "the
        # password is the empty string". require_empty marks the very first
        # account, which must keep one: it is the break-glass path if
        # Cloudflare is ever misconfigured or unreachable.
        wants_password = bool(str(password or "").strip())
        if require_empty and not wants_password:
            raise AuthenticationError(
                "The first account needs a password - it is the way back in "
                "if Cloudflare sign-in ever fails."
            )
        if wants_password:
            self._validate_password(password)
        salt = secrets.token_bytes(24)
        # Stored empty, never as a digest of "": a digest of the empty string
        # is a real digest, and anyone who knows the email could then sign in
        # by submitting nothing. Empty means "no password can ever match".
        password_hash = self._password_digest(password, salt) if wants_password else b""
        now = self._now()
        user_id = str(uuid4())
        try:
            with self._connect() as connection:
                if require_empty:
                    connection.execute("BEGIN IMMEDIATE")
                    existing = connection.execute(
                        "SELECT 1 FROM app_users LIMIT 1"
                    ).fetchone()
                    if existing is not None:
                        raise AuthenticationError(
                            "The owner account has already been created."
                        )
                connection.execute(
                    """
                    INSERT INTO app_users (
                        id, email, display_name, password_salt, password_hash,
                        role, is_active, must_change_password, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized_email,
                        clean_name[:120],
                        self._encode(salt),
                        self._encode(password_hash),
                        normalized_role,
                        int(bool(must_change_password)),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise AuthenticationError("A user with this email already exists.") from exc
        return self.get_user(user_id)

    def reset_password(self, user_id: str, new_password: str, *, actor: dict | None = None) -> dict:
        """Give an account a new password without knowing the old one.

        change_password deliberately requires the current password, which left
        a forgotten temporary password unrecoverable - the app could create a
        user it could then never help. Flagged must_change_password so the
        administrator's chosen password is a handover, not a shared secret,
        and every existing session is dropped: a reset is how you take an
        account back, so leaving its logins running would defeat it.
        """
        self.require_admin(actor)
        self._validate_password(new_password)
        target_id = str(user_id or "")
        salt = secrets.token_bytes(24)
        password_hash = self._password_digest(new_password, salt)
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM app_users WHERE id = ?", (target_id,)
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            connection.execute(
                """
                UPDATE app_users
                SET password_salt = ?, password_hash = ?, must_change_password = 1,
                    updated_at = ?
                WHERE id = ?
                """,
                (self._encode(salt), self._encode(password_hash), now, target_id),
            )
            connection.execute("DELETE FROM app_user_sessions WHERE user_id = ?", (target_id,))
        return self.get_user(target_id)

    def delete_user(self, user_id: str, *, actor: dict | None = None) -> None:
        """Remove an account and everything hanging off it.

        Distinct from set_user_active: disabling keeps the row so the person is
        told their access was switched off, while deleting frees the email for
        re-use - which is the usual reason to reach for it, an address entered
        wrongly and needing to be re-added.

        The child rows are deleted explicitly even though every foreign key
        declares ON DELETE CASCADE. A declared cascade does nothing unless
        PRAGMA foreign_keys is ON for that connection, so relying on it alone
        would leave a delete that LOOKS handled and silently orphans sessions,
        devices and encrypted broker credentials. Being explicit costs three
        statements and cannot quietly stop working.
        """
        self.require_admin(actor)
        target_id = str(user_id or "")
        if target_id and target_id == str((actor or {}).get("id") or ""):
            # Recoverable only by hand-editing the database.
            raise AuthenticationError("You cannot delete your own account.")
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, role FROM app_users WHERE id = ?", (target_id,)
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            if str(row["role"]) == "admin":
                # Counts ACTIVE admins only: a disabled one cannot sign in, so
                # it is not cover for removing the last one who can.
                remaining = connection.execute(
                    "SELECT COUNT(*) AS count FROM app_users "
                    "WHERE role = 'admin' AND is_active = 1 AND id != ?",
                    (target_id,),
                ).fetchone()
                if int(remaining["count"] or 0) == 0:
                    raise AuthenticationError(
                        "The last active administrator cannot be deleted."
                    )
            # approved_by is a SECOND reference to a user, on rows that belong
            # to somebody else. It declares ON DELETE SET NULL, which fires only
            # because _connect turns foreign keys on - the same dependency this
            # method refuses to rely on for the children below, so it is cleared
            # explicitly for the same reason.
            connection.execute(
                "UPDATE app_user_devices SET approved_by = NULL WHERE approved_by = ?",
                (target_id,),
            )
            connection.execute("DELETE FROM app_user_sessions WHERE user_id = ?", (target_id,))
            connection.execute("DELETE FROM app_user_devices WHERE user_id = ?", (target_id,))
            connection.execute(
                "DELETE FROM app_user_provider_credentials WHERE user_id = ?", (target_id,)
            )
            connection.execute("DELETE FROM app_users WHERE id = ?", (target_id,))
        # The write above bumps the identity-cache generation via _connect's
        # total_changes hook, so a deleted email stops resolving immediately -
        # otherwise the Cloudflare path would keep signing in a ghost.

    def remove_password(self, user_id: str, *, actor: dict | None = None) -> dict:
        """Take an account's password away; it signs in through Cloudflare only.

        Refuses on the last active administrator: with no password anywhere
        and Cloudflare misconfigured, nobody could ever get back in.
        """
        self.require_admin(actor)
        target_id = str(user_id or "")
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, role FROM app_users WHERE id = ?", (target_id,)
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            if str(row["role"]) == "admin":
                remaining = connection.execute(
                    "SELECT COUNT(*) AS count FROM app_users "
                    "WHERE role = 'admin' AND is_active = 1 AND id != ? "
                    "AND password_hash IS NOT NULL AND TRIM(password_hash) != ''",
                    (target_id,),
                ).fetchone()
                if int(remaining["count"] or 0) == 0:
                    raise AuthenticationError(
                        "The last administrator with a password cannot have it "
                        "removed - it is the way back in if Cloudflare sign-in fails."
                    )
            connection.execute(
                "UPDATE app_users SET password_hash = '', must_change_password = 0, "
                "updated_at = ? WHERE id = ?",
                (now, target_id),
            )
            connection.execute("DELETE FROM app_user_sessions WHERE user_id = ?", (target_id,))
        return self.get_user(target_id)

    def set_user_active(self, user_id: str, is_active: bool, *, actor: dict | None = None) -> dict:
        """Switch an account off (or back on) without editing the database.

        Disabling drops live sessions so it takes effect now rather than
        whenever a 30-day session cookie happens to expire. Two things are
        refused because they cannot be undone from inside the app: disabling
        yourself, and disabling the last active administrator.
        """
        self.require_admin(actor)
        target_id = str(user_id or "")
        active = bool(is_active)
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, role FROM app_users WHERE id = ?", (target_id,)
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            if not active:
                if target_id == str((actor or {}).get("id") or ""):
                    raise AuthenticationError("You cannot disable your own account.")
                if str(row["role"]) == "admin":
                    remaining = connection.execute(
                        "SELECT COUNT(*) AS count FROM app_users "
                        "WHERE role = 'admin' AND is_active = 1 AND id != ?",
                        (target_id,),
                    ).fetchone()
                    if int(remaining["count"] or 0) == 0:
                        raise AuthenticationError(
                            "The last active administrator cannot be disabled."
                        )
            connection.execute(
                "UPDATE app_users SET is_active = ?, updated_at = ? WHERE id = ?",
                (int(active), now, target_id),
            )
            if not active:
                connection.execute("DELETE FROM app_user_sessions WHERE user_id = ?", (target_id,))
        return self.get_user(target_id)

    def verify_credentials(self, email: str, password: str) -> dict:
        normalized_email = self._normalize_email(email)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_users WHERE email = ? COLLATE NOCASE",
                (normalized_email,),
            ).fetchone()
            if row is None or not bool(row["is_active"]):
                raise AuthenticationError("Invalid email or password.")
            if not str(row["password_hash"] or "").strip():
                # A passwordless account. Refuse before comparing anything -
                # there is no secret here, so no submission may match.
                raise AuthenticationError(
                    "This account signs in through Cloudflare, not with a password."
                )
            salt = self._decode(row["password_salt"])
            expected = self._decode(row["password_hash"])
            actual = self._password_digest(password, salt)
            if not hmac.compare_digest(expected, actual):
                raise AuthenticationError("Invalid email or password.")
        return self._public_user(row)

    def create_session(self, user: dict | str, device_id: str | None = None) -> str:
        user_id = str(user.get("id") if isinstance(user, dict) else user)
        if not user_id:
            raise AuthenticationError("A user is required to create a session.")
        now = self._now()
        token = secrets.token_urlsafe(48)
        token_hash = self._session_token_hash(token)
        expires_at = (datetime.now(timezone.utc) + timedelta(days=SESSION_LIFETIME_DAYS)).isoformat()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM app_users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            connection.execute(
                "UPDATE app_users SET last_login_at = ?, updated_at = ? WHERE id = ?",
                (now, now, user_id),
            )
            connection.execute(
                """
                INSERT INTO app_user_sessions (
                    id, user_id, token_hash, created_at, expires_at, last_seen_at, device_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (str(uuid4()), user_id, token_hash, now, expires_at, now, device_id),
            )
            connection.execute("DELETE FROM app_user_sessions WHERE expires_at <= ?", (now,))
        return token

    def authenticate(self, email: str, password: str) -> tuple[dict, str]:
        user = self.verify_credentials(email, password)
        return user, self.create_session(user)

    def authorize_login_device(
        self,
        user: dict,
        *,
        device_token: str = "",
        user_agent: str = "",
        ip_address: str = "",
    ) -> dict:
        user_id = str((user or {}).get("id") or "")
        if not user_id:
            raise AuthenticationError("A valid user is required for device approval.")
        clean_token = str(device_token or "").strip()
        if not clean_token or len(clean_token) > 512:
            clean_token = secrets.token_urlsafe(48)
        token_hash = self._device_token_hash(clean_token)
        clean_user_agent = str(user_agent or "").strip()[:500]
        clean_ip = str(ip_address or "").strip()[:100]
        label = self._device_label(clean_user_agent)
        now = self._now()

        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            device_count = int(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM app_user_devices WHERE user_id = ?",
                    (user_id,),
                ).fetchone()["count"]
                or 0
            )
            existing = connection.execute(
                "SELECT * FROM app_user_devices WHERE user_id = ? AND token_hash = ?",
                (user_id, token_hash),
            ).fetchone()
            first_device = device_count == 0
            if existing is None:
                device_id = str(uuid4())
                status = "approved" if first_device else "pending"
                connection.execute(
                    """
                    INSERT INTO app_user_devices (
                        id, user_id, token_hash, label, user_agent, ip_address,
                        status, requested_at, approved_at, approved_by, last_seen_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        device_id,
                        user_id,
                        token_hash,
                        label,
                        clean_user_agent,
                        clean_ip,
                        status,
                        now,
                        now if status == "approved" else None,
                        user_id if status == "approved" else None,
                        now if status == "approved" else None,
                    ),
                )
            else:
                device_id = str(existing["id"])
                status = str(existing["status"])
                if first_device and status != "approved":
                    status = "approved"
                    connection.execute(
                        """
                        UPDATE app_user_devices
                        SET status = 'approved', label = ?, user_agent = ?, ip_address = ?,
                            approved_at = ?, approved_by = ?, last_seen_at = ?, revoked_at = NULL
                        WHERE id = ?
                        """,
                        (label, clean_user_agent, clean_ip, now, user_id, now, device_id),
                    )
                elif status == "approved":
                    connection.execute(
                        """
                        UPDATE app_user_devices
                        SET label = ?, user_agent = ?, ip_address = ?, last_seen_at = ?
                        WHERE id = ?
                        """,
                        (label, clean_user_agent, clean_ip, now, device_id),
                    )
                elif status == "pending":
                    connection.execute(
                        """
                        UPDATE app_user_devices
                        SET label = ?, user_agent = ?, ip_address = ?
                        WHERE id = ?
                        """,
                        (label, clean_user_agent, clean_ip, device_id),
                    )
                elif status in {"rejected", "revoked"}:
                    status = "pending"
                    connection.execute(
                        """
                        UPDATE app_user_devices
                        SET status = 'pending', label = ?, user_agent = ?, ip_address = ?,
                            requested_at = ?, approved_at = NULL, approved_by = NULL,
                            last_seen_at = NULL, revoked_at = NULL
                        WHERE id = ?
                        """,
                        (label, clean_user_agent, clean_ip, now, device_id),
                    )
            row = connection.execute(
                """
                SELECT d.*, u.email AS user_email, u.display_name AS user_display_name,
                       approver.email AS approved_by_email
                FROM app_user_devices d
                JOIN app_users u ON u.id = d.user_id
                LEFT JOIN app_users approver ON approver.id = d.approved_by
                WHERE d.id = ?
                """,
                (device_id,),
            ).fetchone()
        return {
            "approved": status == "approved",
            "status": status,
            "firstDevice": first_device,
            "deviceToken": clean_token,
            "device": self._public_device(row),
        }

    def list_devices(self, actor: dict | None, current_device_token: str = "") -> dict:
        self.require_admin(actor)
        current_hash = self._device_token_hash(current_device_token) if current_device_token else ""
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT d.*, u.email AS user_email, u.display_name AS user_display_name,
                       approver.email AS approved_by_email
                FROM app_user_devices d
                JOIN app_users u ON u.id = d.user_id
                LEFT JOIN app_users approver ON approver.id = d.approved_by
                WHERE d.status IN ('pending', 'approved')
                ORDER BY CASE d.status WHEN 'pending' THEN 0 ELSE 1 END,
                         d.requested_at DESC
                """
            ).fetchall()
        devices = []
        for row in rows:
            item = self._public_device(row)
            item["isCurrentDevice"] = bool(
                current_hash
                and str(row["user_id"]) == str(actor["id"])
                and hmac.compare_digest(str(row["token_hash"]), current_hash)
            )
            devices.append(item)
        return {
            "pendingRequests": [item for item in devices if item["status"] == "pending"],
            "approvedDevices": [item for item in devices if item["status"] == "approved"],
        }

    def decide_device_request(self, actor: dict | None, device_id: str, action: str) -> dict:
        self.require_admin(actor)
        normalized_action = str(action or "").strip().lower()
        if normalized_action not in {"approve", "reject"}:
            raise AuthenticationError("Choose approve or reject.")
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_user_devices WHERE id = ? AND status = 'pending'",
                (str(device_id),),
            ).fetchone()
            if row is None:
                raise AuthenticationError("This device request is no longer pending.")
            if normalized_action == "approve":
                connection.execute(
                    """
                    UPDATE app_user_devices
                    SET status = 'approved', approved_at = ?, approved_by = ?,
                        last_seen_at = ?, revoked_at = NULL
                    WHERE id = ?
                    """,
                    (now, str(actor["id"]), now, str(device_id)),
                )
            else:
                connection.execute(
                    """
                    UPDATE app_user_devices
                    SET status = 'rejected', approved_at = NULL, approved_by = ?, revoked_at = ?
                    WHERE id = ?
                    """,
                    (str(actor["id"]), now, str(device_id)),
                )
        return {"deviceId": str(device_id), "status": "approved" if normalized_action == "approve" else "rejected"}

    def revoke_device(
        self,
        actor: dict | None,
        device_id: str,
        current_device_token: str = "",
    ) -> dict:
        self.require_admin(actor)
        now = self._now()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, user_id, token_hash FROM app_user_devices WHERE id = ? AND status = 'approved'",
                (str(device_id),),
            ).fetchone()
            if row is None:
                raise AuthenticationError("Approved device not found.")
            current_hash = self._device_token_hash(current_device_token) if current_device_token else ""
            if (
                current_hash
                and str(row["user_id"]) == str(actor["id"])
                and hmac.compare_digest(str(row["token_hash"]), current_hash)
            ):
                raise AuthenticationError("You cannot revoke the device you are currently using.")
            connection.execute(
                """
                UPDATE app_user_devices
                SET status = 'revoked', revoked_at = ?
                WHERE id = ?
                """,
                (now, str(device_id)),
            )
            connection.execute(
                "DELETE FROM app_user_sessions WHERE device_id = ?",
                (str(device_id),),
            )
        return {"deviceId": str(device_id), "status": "revoked"}

    def change_password(
        self,
        user: dict,
        *,
        current_password: str,
        new_password: str,
    ) -> None:
        user_id = str((user or {}).get("id") or "")
        if not user_id:
            raise AuthenticationError("Sign in to change your password.")
        self._validate_password(new_password)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_salt, password_hash FROM app_users WHERE id = ? AND is_active = 1",
                (user_id,),
            ).fetchone()
            if row is None:
                raise AuthenticationError("User not found.")
            actual = self._password_digest(current_password, self._decode(row["password_salt"]))
            if not hmac.compare_digest(self._decode(row["password_hash"]), actual):
                raise AuthenticationError("Current password is incorrect.")
            salt = secrets.token_bytes(24)
            password_hash = self._password_digest(new_password, salt)
            now = self._now()
            connection.execute(
                """
                UPDATE app_users
                SET password_salt = ?, password_hash = ?, must_change_password = 0, updated_at = ?
                WHERE id = ?
                """,
                (self._encode(salt), self._encode(password_hash), now, user_id),
            )
            connection.execute("DELETE FROM app_user_sessions WHERE user_id = ?", (user_id,))

    def _invalidate_identity_cache(self) -> None:
        with self._identity_cache_lock:
            self._identity_generation += 1
            self._identity_cache.clear()

    def _identity_cached(self, key: tuple):
        with self._identity_cache_lock:
            entry = self._identity_cache.get(key)
            if entry is None:
                return None
            cached_at, generation, value = entry
            if generation != self._identity_generation:
                return None
            if time.monotonic() - cached_at >= IDENTITY_CACHE_SECONDS:
                self._identity_cache.pop(key, None)
                return None
            return value

    def _remember_identity(self, key: tuple, value) -> None:
        with self._identity_cache_lock:
            if len(self._identity_cache) >= IDENTITY_CACHE_MAX_ENTRIES:
                self._identity_cache.clear()
            self._identity_cache[key] = (time.monotonic(), self._identity_generation, value)

    def user_for_session(self, token: str | None, device_token: str | None = None) -> dict | None:
        if not token:
            return None
        now = self._now()
        token_hash = self._session_token_hash(token)
        cache_key = ("session", token_hash, self._device_token_hash(device_token) if device_token else "")
        cached = self._identity_cached(cache_key)
        if cached is not None:
            cached_user, cached_expires_at, cached_device_id = cached
            # The memo must never outlive the session it describes, so expiry
            # is re-checked here rather than left to the TTL.
            if cached_expires_at > now:
                self._schedule_session_activity_touch(token_hash, cached_device_id, now)
                return dict(cached_user)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT u.*, s.device_id AS session_device_id, s.expires_at AS session_expires_at
                FROM app_user_sessions s
                JOIN app_users u ON u.id = s.user_id
                WHERE s.token_hash = ? AND s.expires_at > ? AND u.is_active = 1
                """,
                (token_hash, now),
            ).fetchone()
            if row is None:
                return None
            session_device_id = str(row["session_device_id"] or "")
            if session_device_id:
                supplied_device_hash = self._device_token_hash(device_token) if device_token else ""
                device = connection.execute(
                    """
                    SELECT id, token_hash
                    FROM app_user_devices
                    WHERE id = ? AND user_id = ? AND status = 'approved'
                    """,
                    (session_device_id, str(row["id"])),
                ).fetchone()
                if (
                    device is None
                    or not supplied_device_hash
                    or not hmac.compare_digest(str(device["token_hash"]), supplied_device_hash)
                ):
                    connection.execute(
                        "DELETE FROM app_user_sessions WHERE token_hash = ?",
                        (token_hash,),
                    )
                    return None
        # Session validation is on every API request. Scanner/database writers
        # can hold SQLite's single writer slot for many seconds, so telemetry
        # updates must never make chart and option-chain reads wait behind
        # them. Touch at most once per minute on a short-timeout daemon while
        # the authenticated request continues immediately.
        resolved = self._public_user(row)
        self._remember_identity(
            cache_key,
            (resolved, str(row["session_expires_at"] or ""), session_device_id),
        )
        self._schedule_session_activity_touch(token_hash, session_device_id, now)
        return resolved

    def _schedule_session_activity_touch(
        self,
        token_hash: str,
        device_id: str,
        touched_at: str,
    ) -> None:
        current = time.monotonic()
        with self._session_touch_lock:
            previous = float(self._session_touch_times.get(token_hash) or 0.0)
            if current - previous < SESSION_ACTIVITY_TOUCH_SECONDS:
                return
            self._session_touch_times[token_hash] = current
            if len(self._session_touch_times) > 2048:
                cutoff = current - SESSION_ACTIVITY_TOUCH_SECONDS * 2
                self._session_touch_times = {
                    key: value
                    for key, value in self._session_touch_times.items()
                    if value >= cutoff
                }

        def touch() -> None:
            connection: sqlite3.Connection | None = None
            try:
                connection = sqlite3.connect(
                    self.db_path,
                    timeout=SESSION_ACTIVITY_BUSY_TIMEOUT_MS / 1000.0,
                )
                connection.execute(
                    f"PRAGMA busy_timeout = {SESSION_ACTIVITY_BUSY_TIMEOUT_MS}"
                )
                if device_id:
                    connection.execute(
                        "UPDATE app_user_devices SET last_seen_at = ? WHERE id = ?",
                        (touched_at, device_id),
                    )
                connection.execute(
                    "UPDATE app_user_sessions SET last_seen_at = ? WHERE token_hash = ?",
                    (touched_at, token_hash),
                )
                connection.commit()
            except sqlite3.Error:
                # Last-seen timestamps are operational telemetry. A later
                # request retries after the throttle interval; authentication
                # validity never depends on this best-effort write.
                pass
            finally:
                if connection is not None:
                    connection.close()

        threading.Thread(
            target=touch,
            name="auth-session-activity-touch",
            daemon=True,
        ).start()

    def logout(self, token: str | None) -> None:
        if not token:
            return
        token_hash = self._session_token_hash(token)
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM app_user_sessions WHERE token_hash = ?",
                (token_hash,),
            )
        with self._session_touch_lock:
            self._session_touch_times.pop(token_hash, None)

    def list_users(self, actor: dict | None = None) -> list[dict]:
        self.require_admin(actor)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, email, display_name, role, is_active, must_change_password,
                       created_at, updated_at, last_login_at
                FROM app_users
                ORDER BY created_at ASC
                """
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def get_user_by_email(self, email: str) -> dict | None:
        normalized = str(email or "").strip().lower()
        if not normalized:
            return None
        cache_key = ("email", normalized)
        cached = self._identity_cached(cache_key)
        if cached is not None:
            return dict(cached)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_users WHERE email = ? COLLATE NOCASE AND is_active = 1",
                (normalized,),
            ).fetchone()
        if row is None:
            return None
        resolved = self._public_user(row)
        self._remember_identity(cache_key, resolved)
        return dict(resolved)

    def find_account_by_email(self, email: str) -> dict | None:
        """Like get_user_by_email, but finds disabled accounts too.

        Sign-in paths want get_user_by_email, which refuses a disabled account
        by returning nothing. The Cloudflare path needs to tell the two cases
        apart: "we have never heard of you, ask your administrator" and "your
        access was switched off" are different messages, and telling a disabled
        colleague the former sends them chasing the wrong problem.
        """
        normalized = str(email or "").strip().lower()
        if not normalized:
            return None
        # Cached, and under its OWN key. The Cloudflare path calls this on
        # every request carrying a verified address, and _connect opens a
        # fresh sqlite connection while holding a process-wide lock - uncached,
        # that serialised every request in the app behind one database read
        # (observed 2026-08-26: 25 request threads parked here at once, the
        # gateway backed up behind them, and :3001 stopped answering).
        #
        # The key differs from get_user_by_email's on purpose: that one filters
        # to active accounts and this one deliberately finds disabled ones, so
        # a shared entry would let one meaning answer the other. Writes bump
        # the cache generation (see _connect), so disabling someone still takes
        # effect immediately.
        cache_key = ("account", normalized)
        cached = self._identity_cached(cache_key)
        if cached is not None:
            return dict(cached)
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM app_users WHERE email = ? COLLATE NOCASE",
                (normalized,),
            ).fetchone()
        if row is None:
            # Deliberately not cached: an admin creates an account and the
            # person signs in seconds later.
            return None
        resolved = self._public_user(row)
        self._remember_identity(cache_key, resolved)
        return dict(resolved)

    def get_user(self, user_id: str) -> dict:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM app_users WHERE id = ?", (str(user_id),)).fetchone()
        if row is None:
            raise AuthenticationError("User not found.")
        return self._public_user(row)

    def save_provider_credentials(self, user: dict | str, provider: str, values: dict) -> dict:
        user_id = str(user.get("id") if isinstance(user, dict) else user)
        provider_key = self._provider_key(provider)
        current = self.get_provider_credentials(user_id, provider_key)
        merged = {**current}
        for key, value in dict(values or {}).items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            merged[str(key)] = value.strip() if isinstance(value, str) else value
        now = self._now()
        encrypted = self._cipher.encrypt(
            json.dumps(merged, separators=(",", ":"), sort_keys=True).encode("utf-8")
        ).decode("ascii")
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_user_provider_credentials (
                    user_id, provider, encrypted_payload, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(user_id, provider) DO UPDATE SET
                    encrypted_payload = excluded.encrypted_payload,
                    updated_at = excluded.updated_at
                """,
                (str(user_id), provider_key, encrypted, now, now),
            )
        return self.provider_status(user_id, provider_key)

    def get_provider_credentials(self, user_id: str, provider: str) -> dict:
        provider_key = self._provider_key(provider)
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT encrypted_payload
                FROM app_user_provider_credentials
                WHERE user_id = ? AND provider = ?
                """,
                (str(user_id), provider_key),
            ).fetchone()
        if row is None:
            return {}
        try:
            raw = self._cipher.decrypt(str(row["encrypted_payload"]).encode("ascii"))
            payload = json.loads(raw.decode("utf-8"))
        except (InvalidToken, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AuthenticationError(
                f"Stored {provider_key} credentials cannot be decrypted with the configured key."
            ) from exc
        return payload if isinstance(payload, dict) else {}

    def provider_credentials(self, user_id: str, provider: str) -> dict:
        return self.get_provider_credentials(user_id, provider)

    def delete_provider_credentials(self, user: dict | str, provider: str) -> None:
        user_id = str(user.get("id") if isinstance(user, dict) else user)
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM app_user_provider_credentials
                WHERE user_id = ? AND provider = ?
                """,
                (str(user_id), self._provider_key(provider)),
            )

    def provider_status(self, user_id: str, provider: str) -> dict:
        provider_key = self._provider_key(provider)
        values = self.get_provider_credentials(user_id, provider_key)
        client_id = str(values.get("client_id") or values.get("key_id") or "")
        return {
            "provider": provider_key,
            "configured": bool(
                (values.get("client_id") and values.get("client_secret"))
                or (values.get("key_id") and values.get("secret_key"))
                or values.get("access_token")
            ),
            "clientIdMasked": self._mask(client_id),
            "hasClientSecret": bool(values.get("client_secret") or values.get("secret_key")),
            "hasToken": bool(values.get("token")),
            "updatedAt": self._provider_updated_at(user_id, provider_key),
        }

    def provider_summary(self, user: dict | str) -> dict:
        user_id = str(user.get("id") if isinstance(user, dict) else user)
        alpaca = self.get_provider_credentials(user_id, "alpaca_market_data")
        schwab = self.get_provider_credentials(user_id, "schwab_market_data")
        schwab_trading = self.get_provider_credentials(user_id, "schwab_trading")
        tradier = self.get_provider_credentials(user_id, "tradier")
        return {
            "alpaca": {
                "configured": bool(alpaca.get("key_id") and alpaca.get("secret_key")),
                "keyIdMasked": self._mask(str(alpaca.get("key_id") or "")),
            },
            "schwabMarketData": {
                "configured": bool(schwab.get("client_id") and schwab.get("client_secret")),
                "clientIdMasked": self._mask(str(schwab.get("client_id") or "")),
                "authenticated": bool(schwab.get("token")),
            },
            "schwabTrading": {
                "configured": bool(
                    schwab_trading.get("client_id") and schwab_trading.get("client_secret")
                ),
                "clientIdMasked": self._mask(str(schwab_trading.get("client_id") or "")),
                "authenticated": bool(schwab_trading.get("token")),
            },
            "tradier": {
                "configured": bool(tradier.get("access_token")),
                "tokenMasked": self._mask(str(tradier.get("access_token") or "")),
            },
        }

    def _provider_updated_at(self, user_id: str, provider: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT updated_at FROM app_user_provider_credentials
                WHERE user_id = ? AND provider = ?
                """,
                (str(user_id), provider),
            ).fetchone()
        return str(row["updated_at"]) if row is not None else None

    @staticmethod
    def require_admin(user: dict | None) -> None:
        if not user or user.get("role") != "admin":
            raise AuthorizationError("Administrator access is required.")

    @staticmethod
    def _public_device(row) -> dict:
        return {
            "id": str(row["id"]),
            "userId": str(row["user_id"]),
            "userEmail": str(row["user_email"]),
            "userDisplayName": str(row["user_display_name"]),
            "label": str(row["label"]),
            "userAgent": str(row["user_agent"] or ""),
            "ipAddress": str(row["ip_address"] or ""),
            "status": str(row["status"]),
            "requestedAt": row["requested_at"],
            "approvedAt": row["approved_at"],
            "approvedByEmail": row["approved_by_email"],
            "lastSeenAt": row["last_seen_at"],
            "revokedAt": row["revoked_at"],
        }

    @staticmethod
    def _device_label(user_agent: str) -> str:
        value = str(user_agent or "")
        if "Edg/" in value:
            browser = "Edge"
        elif "Chrome/" in value or "CriOS/" in value:
            browser = "Chrome"
        elif "Firefox/" in value or "FxiOS/" in value:
            browser = "Firefox"
        elif "Safari/" in value:
            browser = "Safari"
        else:
            browser = "Browser"
        if "iPhone" in value:
            platform = "iPhone"
        elif "iPad" in value:
            platform = "iPad"
        elif "Android" in value:
            platform = "Android"
        elif "Macintosh" in value:
            platform = "Mac"
        elif "Windows" in value:
            platform = "Windows"
        elif "Linux" in value:
            platform = "Linux"
        else:
            platform = "device"
        return f"{browser} on {platform}"

    @staticmethod
    def _public_user(row) -> dict:
        keys = set(row.keys()) if hasattr(row, "keys") else set()
        return {
            "id": str(row["id"]),
            "email": str(row["email"]),
            "displayName": str(row["display_name"]),
            "role": str(row["role"]),
            "isAdmin": str(row["role"]) == "admin",
            "isActive": bool(row["is_active"]),
            "hasPassword": bool(str(row["password_hash"] or "").strip()) if "password_hash" in keys else False,
            "mustChangePassword": bool(row["must_change_password"]) if "must_change_password" in keys else False,
            "createdAt": row["created_at"] if "created_at" in keys else None,
            "updatedAt": row["updated_at"] if "updated_at" in keys else None,
            "lastLoginAt": row["last_login_at"] if "last_login_at" in keys else None,
        }

    @staticmethod
    def _normalize_email(email: str) -> str:
        normalized = str(email or "").strip().lower()
        if (
            not normalized
            or "@" not in normalized
            or normalized.startswith("@")
            or normalized.endswith("@")
            or len(normalized) > 254
        ):
            raise AuthenticationError("Enter a valid email address.")
        return normalized

    @staticmethod
    def _validate_password(password: str) -> None:
        value = str(password or "")
        if len(value) < 10:
            raise AuthenticationError("Password must contain at least 10 characters.")
        if len(value) > 256:
            raise AuthenticationError("Password is too long.")
        if not any(character.islower() for character in value):
            raise AuthenticationError("Password must include a lower-case letter.")
        if not any(character.isupper() for character in value):
            raise AuthenticationError("Password must include an upper-case letter.")
        if not any(character.isdigit() for character in value):
            raise AuthenticationError("Password must include a number.")

    @staticmethod
    def _password_digest(password: str, salt: bytes) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256",
            str(password).encode("utf-8"),
            salt,
            PASSWORD_ITERATIONS,
            dklen=32,
        )

    @staticmethod
    def _session_token_hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @staticmethod
    def _device_token_hash(token: str) -> str:
        return hashlib.sha256(str(token).encode("utf-8")).hexdigest()

    @staticmethod
    def _provider_key(provider: str) -> str:
        normalized = str(provider or "").strip().lower()
        if normalized == "alpaca":
            normalized = "alpaca_market_data"
        if normalized not in {
            "alpaca_market_data",
            "schwab_market_data",
            "schwab_trading",
            "tradier",
        }:
            raise AuthenticationError("Unsupported API provider.")
        return normalized

    @staticmethod
    def _mask(value: str) -> str:
        if not value:
            return ""
        if len(value) <= 10:
            return "configured"
        return f"{value[:4]}...{value[-6:]}"

    @staticmethod
    def _encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode("ascii")

    @staticmethod
    def _decode(value: str) -> bytes:
        return base64.urlsafe_b64decode(str(value).encode("ascii"))

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _resolve_encryption_key(encryption_key: str | bytes | None) -> bytes:
        candidate = encryption_key or os.getenv("USER_CREDENTIALS_ENCRYPTION_KEY", "")
        if candidate:
            return candidate.encode("ascii") if isinstance(candidate, str) else candidate

        LOCAL_ENCRYPTION_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        if LOCAL_ENCRYPTION_KEY_PATH.exists():
            return LOCAL_ENCRYPTION_KEY_PATH.read_bytes().strip()
        key = Fernet.generate_key()
        try:
            descriptor = os.open(
                LOCAL_ENCRYPTION_KEY_PATH,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                0o600,
            )
        except FileExistsError:
            return LOCAL_ENCRYPTION_KEY_PATH.read_bytes().strip()
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(key)
        return key
