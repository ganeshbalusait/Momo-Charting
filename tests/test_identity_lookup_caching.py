from __future__ import annotations

"""find_account_by_email must not be a per-request database hit.

Regression, 2026-08-26: the Cloudflare sign-in path calls this on EVERY
request that carries a verified address - which is every request from
app.agxtrade.com. It went straight to _connect(), which opens a fresh sqlite
connection while holding a process-wide RLock, so all those lookups serialised
on one lock. Under normal chart-poll load the queue never drained: py-spy
found 25 request threads parked here at once, the gateway's proxy threads
piled up behind them, and :3001 stopped answering. The app looked dead.

This is the same tax that app-slowness-root-causes-2026-08-21 records, and
_identity_cache is the machinery that fixed it last time. The new path walked
straight past it.

The cache must NOT be shared with get_user_by_email: that one filters to
active accounts, this one deliberately finds disabled ones too, so one cache
entry serving both would make a disabled account look signed-in-able, or an
active one invisible.
"""

import pytest
from cryptography.fernet import Fernet

from auth_service import AuthService


class CountingAuthService(AuthService):
    """Counts how many times a database connection is actually opened."""

    def __init__(self, *args, **kwargs):
        self.connect_calls = 0
        super().__init__(*args, **kwargs)

    def _connect(self):
        self.connect_calls += 1
        return super()._connect()


@pytest.fixture
def auth(tmp_path):
    return CountingAuthService(db_path=tmp_path / "auth.db", encryption_key=Fernet.generate_key())


@pytest.fixture
def owner(auth):
    return auth.bootstrap_owner("owner@example.com", "SecurePass123", "Owner")


@pytest.fixture
def member(auth, owner):
    return auth.create_user(email="member@example.com", actor=owner, display_name="Member")


def test_repeated_lookups_do_not_reopen_the_database(auth, member):
    auth.find_account_by_email("member@example.com")
    baseline = auth.connect_calls

    for _ in range(50):
        auth.find_account_by_email("member@example.com")

    assert auth.connect_calls == baseline, (
        f"opened {auth.connect_calls - baseline} extra connections for 50 lookups - "
        "_connect holds a process-wide lock, so this serialises every request"
    )


def test_the_cached_answer_is_still_correct(auth, member):
    first = auth.find_account_by_email("member@example.com")
    second = auth.find_account_by_email("member@example.com")

    assert first["id"] == member["id"]
    assert second["id"] == member["id"]


def test_the_two_lookups_do_not_share_a_cache_entry(auth, owner, member):
    """get_user_by_email filters disabled accounts; this one must not.

    Sharing a key would let a lookup by one meaning answer the other - a
    disabled account appearing signed-in-able, or an active one invisible.
    """
    auth.set_user_active(member["id"], False, actor=owner)

    # Warm one, then read the other. The answers must still differ.
    assert auth.get_user_by_email("member@example.com") is None
    found = auth.find_account_by_email("member@example.com")
    assert found is not None and found["isActive"] is False

    # And in the opposite order.
    assert auth.find_account_by_email("member@example.com")["isActive"] is False
    assert auth.get_user_by_email("member@example.com") is None


def test_disabling_takes_effect_immediately_despite_the_cache(auth, owner, member):
    # Disable is the only revocation inside the app; a stale cache entry
    # would keep a removed colleague working.
    assert auth.find_account_by_email("member@example.com")["isActive"] is True

    auth.set_user_active(member["id"], False, actor=owner)

    assert auth.find_account_by_email("member@example.com")["isActive"] is False


def test_a_new_account_is_visible_immediately(auth, owner):
    # A miss must not be cached as "no such account" - the admin creates
    # someone and they sign in seconds later.
    assert auth.find_account_by_email("late@example.com") is None

    auth.create_user(email="late@example.com", actor=owner)

    assert auth.find_account_by_email("late@example.com") is not None


def test_removing_a_password_is_reflected_immediately(auth, owner):
    created = auth.create_user(email="pw@example.com", password="Temporary123", actor=owner)
    assert auth.find_account_by_email("pw@example.com")["hasPassword"] is True

    auth.remove_password(created["id"], actor=owner)

    assert auth.find_account_by_email("pw@example.com")["hasPassword"] is False


def test_an_empty_address_never_touches_the_database(auth):
    baseline = auth.connect_calls
    for blank in ["", None, "   "]:
        assert auth.find_account_by_email(blank) is None
    assert auth.connect_calls == baseline
