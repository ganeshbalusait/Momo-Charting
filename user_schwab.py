from __future__ import annotations

"""A Schwab identity that belongs to one user instead of to the server.

Today the app has exactly one Schwab identity: a key pair in .env and a token
file in artifacts/. That is why the settings card is admin-only - a second
person saving keys there does not add their own, it replaces everyone's. On
2026-08-26 a single write of the string "EVIL" to those fields took market data
down for the whole app, which is the same failure a user would cause by
filling the form in honestly.

SchwabClient already accepts a config object carrying its own client_id,
client_secret and token_path, so a per-user client needs no change to that
class. This module builds that config from the encrypted per-user vault, whose
schwab_market_data / schwab_trading slots already expect exactly these fields.

Two invariants hold the whole design up:

  * Building a user's config NEVER mutates the shared settings object. That
    mutation is precisely how the .env route breaks everyone.
  * A user with no keys of their own gets None, never a config carrying the
    house keys. A half-built config would quietly authenticate as the owner
    while looking per-user, which is worse than plainly having no connection.
"""

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

# The vault slots that already exist for these, from auth_service.provider_summary.
VAULT_PROVIDERS = {
    "market_data": "schwab_market_data",
    "trading": "schwab_trading",
}

TOKEN_DIRECTORY = "user_tokens"

# Where a saved Schwab credential is allowed to land.
HOUSE = "house"   # the shared .env pair the background jobs run on
VAULT = "vault"   # the caller's own encrypted row, touching nobody else


def credential_target(user) -> str:
    """HOUSE only for a provably-identified administrator; VAULT for everyone.

    The house path rewrites the whole server's Schwab keys, so this fails
    closed: an absent flag, a truthy string, a different case, or a missing id
    all resolve to VAULT. Both role and isAdmin must agree, because each is
    populated by a different code path and a single one being wrong should not
    be enough to reach the shared credential.

    The owner is deliberately still the house: the warmers, scanners and alert
    engine fire on a clock with no user in scope, and they have to keep running.
    """
    record = user if isinstance(user, dict) else {}
    if not str(record.get("id") or "").strip():
        return VAULT
    if record.get("isAdmin") is not True:
        return VAULT
    if str(record.get("role") or "").strip().lower() != "admin":
        return VAULT
    return HOUSE


def serves_own_providers(user) -> bool:
    """Should this caller's data come from their own keys rather than the house?

    The same rule as credential_target, deliberately expressed once: where a
    caller's credentials are WRITTEN and where their data is READ from must
    agree. Two independent rules would eventually disagree, and the
    disagreement shows up as a data-quality problem rather than a routing bug.

    The administrator is the house. Their alpaca_market_data vault row IS the
    house Alpaca key - _owner_alpaca_chart_client reads exactly that row - so
    treating it as a personal key double counts it and quietly diverts the
    owner off the house Schwab quote path onto a thinner Alpaca tape.
    """
    return credential_target(user) != HOUSE


def vault_provider_for(profile) -> str:
    """The vault slot backing a Schwab profile."""
    key = str(profile or "").strip().lower()
    if key not in VAULT_PROVIDERS:
        raise ValueError(f"Unknown Schwab profile: {profile!r}")
    return VAULT_PROVIDERS[key]


def _safe_component(value) -> str:
    """A single filename component, with no way out of the directory.

    User ids are uuid4 from the database, so this is belt and braces - but a
    token written outside its directory could overwrite the house token, and
    that is not a failure worth leaving to the id generator's good behaviour.
    """
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("A user id is required.")
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    if not cleaned:
        raise ValueError(f"Unusable user id: {value!r}")
    return cleaned


def user_token_path(artifacts_dir, user_id, profile) -> Path:
    """Where this user's OAuth token for this profile lives.

    Deliberately its own directory and its own filename shape, so a user token
    can never collide with schwab_token.json or schwab_trading_token.json.
    """
    provider = vault_provider_for(profile)
    return Path(artifacts_dir) / TOKEN_DIRECTORY / f"{_safe_component(user_id)}.{provider}.json"


@dataclass
class UserSchwabSettings:
    """Config for one user's Schwab app, shaped like config.SchwabSettings."""

    client_id: str
    client_secret: str
    redirect_uri: str
    token_path: str
    include_extended_hours: bool
    timeout_seconds: int
    refresh_token: str = ""
    access_token: str = ""


def schwab_settings_for_user(base_settings, credentials: Mapping | None, token_path):
    """A config for this user's own Schwab app, or None if they have none.

    `base_settings` supplies only the non-credential values (redirect URI,
    extended hours, timeout) and is copied from, never written to.
    """
    creds = dict(credentials or {})
    client_id = str(creds.get("client_id") or "").strip()
    client_secret = str(creds.get("client_secret") or "").strip()
    if not client_id or not client_secret:
        # No keys of their own. The caller falls back to the house credential
        # rather than receiving a config that would sign in as the owner.
        return None

    return UserSchwabSettings(
        client_id=client_id,
        client_secret=client_secret,
        redirect_uri=str(getattr(base_settings, "redirect_uri", "") or "https://127.0.0.1/"),
        token_path=str(token_path),
        include_extended_hours=bool(getattr(base_settings, "include_extended_hours", True)),
        timeout_seconds=int(getattr(base_settings, "timeout_seconds", 15) or 15),
    )


def unconfigured_status() -> dict:
    """The connection status of someone who has connected nothing.

    Reads may fall back to the house client - a user without their own app
    still needs charts to draw. STATUS may not. Reporting the house's expiry
    date under a card headed "My Schwab APIs" tells a user who has connected
    nothing that they are connected until 8/30, which is how the owner found
    this: signed in as a user with no keys and shown someone else's countdown
    and three green lamps.

    Same keys as SchwabClient.connection_status so the settings card renders a
    clean not-connected state instead of "undefined".
    """
    return {
        "configured": False,
        "credentialsConfigured": False,
        "hasClientId": False,
        "hasClientSecret": False,
        "clientIdMasked": "",
        "hasAccessToken": False,
        "hasRefreshToken": False,
        "accessTokenValid": False,
        "refreshTokenValid": False,
        "accessTokenRemainingSeconds": None,
        "refreshTokenRemainingSeconds": None,
        "tokenFileExists": False,
        "tokenPath": "",
        "redirectUri": "",
        "includeExtendedHours": False,
        "tokenExpiresAt": None,
        "refreshTokenExpiresAt": None,
        "tokenSavedAt": None,
        "tokenManager": "schwab-py",
        "tokenLoadError": "",
        "healthy": False,
    }


class UserSchwabClients:
    """One SchwabClient per (user, profile), built from the encrypted vault.

    Clients are cached because building one reads a token file from disk, and
    the live price endpoint runs about once a second per open chart.

    The invariant: a client is never shared between users. Handing user B a
    client built from user A's config would send B's requests on A's quota
    while every badge in the UI claimed otherwise - invisible from the outside,
    and the exact failure per-user keys exist to prevent. The cache key is
    therefore (user_id, provider), never just the provider.
    """

    def __init__(self, artifacts_dir, base_settings, credentials_reader, client_factory):
        self._artifacts_dir = artifacts_dir
        self._base_settings = base_settings
        self._read_credentials = credentials_reader
        self._build_client = client_factory
        self._clients: dict[tuple, object] = {}
        self._lock = threading.RLock()

    def config_for(self, user, profile: str = "market_data"):
        """This user's own Schwab config, or None if they have no keys."""
        user_id = str((user or {}).get("id") or "").strip() if isinstance(user, dict) else str(user or "").strip()
        if not user_id:
            return None
        provider = vault_provider_for(profile)
        try:
            credentials = self._read_credentials(user_id, provider)
        except Exception:
            # A vault read failing must degrade to "no personal client", so the
            # caller falls back to the house credential rather than 500ing.
            return None
        return schwab_settings_for_user(
            self._base_settings,
            credentials,
            user_token_path(self._artifacts_dir, user_id, profile),
        )

    def for_user(self, user, profile: str = "market_data"):
        """This user's own Schwab client, or None if they have no keys."""
        user_id = str((user or {}).get("id") or "").strip() if isinstance(user, dict) else str(user or "").strip()
        if not user_id:
            return None
        key = (user_id, vault_provider_for(profile))
        with self._lock:
            cached = self._clients.get(key)
            if cached is not None:
                return cached
            config = self.config_for(user, profile)
            if config is None:
                return None
            try:
                client = self._build_client(config)
            except Exception:
                return None
            self._clients[key] = client
            return client

    def invalidate(self, user_id, profile: str | None = None) -> None:
        """Drop cached clients so the next request picks up new keys.

        Called whenever a user saves credentials or their account changes -
        without it they update a key and keep calling on the old one.
        """
        target = str(user_id or "").strip()
        if not target:
            return
        with self._lock:
            if profile is None:
                for key in [k for k in self._clients if k[0] == target]:
                    self._clients.pop(key, None)
                return
            self._clients.pop((target, vault_provider_for(profile)), None)
