"""Reset the local app password for your own account.

Run it yourself in a terminal; it prompts for the new password securely
(nothing is echoed or logged) and updates the auth database in place.
All existing sessions are signed out so every browser re-authenticates.
"""

import base64
import getpass
import hashlib
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "database" / "trades.db"
EMAIL = "ganeshbalusait@gmail.com"
PASSWORD_ITERATIONS = 600_000


def validate(password: str) -> str | None:
    if len(password) < 10:
        return "Password must contain at least 10 characters."
    if len(password) > 256:
        return "Password is too long."
    if not any(c.islower() for c in password):
        return "Password must include a lower-case letter."
    if not any(c.isupper() for c in password):
        return "Password must include an upper-case letter."
    if not any(c.isdigit() for c in password):
        return "Password must include a number."
    return None


def main() -> int:
    new_password = getpass.getpass("New password: ")
    error = validate(new_password)
    if error:
        print(error)
        return 1
    if getpass.getpass("Confirm new password: ") != new_password:
        print("Passwords do not match.")
        return 1

    salt = secrets.token_bytes(24)
    digest = hashlib.pbkdf2_hmac(
        "sha256", new_password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32
    )
    now = datetime.now(timezone.utc).isoformat()

    connection = sqlite3.connect(DB_PATH, timeout=30)
    try:
        connection.execute("PRAGMA busy_timeout = 30000")
        cursor = connection.execute(
            """
            UPDATE app_users
            SET password_salt = ?, password_hash = ?, must_change_password = 0, updated_at = ?
            WHERE email = ? COLLATE NOCASE
            """,
            (
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(digest).decode("ascii"),
                now,
                EMAIL,
            ),
        )
        if cursor.rowcount != 1:
            print(f"No user found for {EMAIL}; nothing changed.")
            return 1
        connection.execute(
            "DELETE FROM app_user_sessions WHERE user_id = "
            "(SELECT id FROM app_users WHERE email = ? COLLATE NOCASE)",
            (EMAIL,),
        )
        connection.commit()
    finally:
        connection.close()

    print(f"Password updated for {EMAIL}. Sign in again in every browser.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
