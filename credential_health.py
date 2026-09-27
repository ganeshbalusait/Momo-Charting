from __future__ import annotations

"""Whether a broker credential WORKS, recorded from what actually happened.

Every existing health field in this app answers a different question. Schwab's
`configured` is `client_id and client_secret and refresh_token` - three strings
being non-empty. `refreshTokenValid` adds arithmetic on an expiry date we wrote
down ourselves. Neither has ever asked the provider anything.

Live proof, 2026-08-27: the owner's market-data profile reported
configured=true, credentialsConfigured=true, refreshTokenValid=true - a green
lamp on screen - while every call returned "invalid_client: Unauthorized" and
no chart had loaded for a day. Present, correctly shaped, locally unexpired,
and rejected.

Third instance of one pattern in a week, after `analyticsDeferred` meaning
"complete for the narrower thing I was built for" and `configured` meaning "a
value is present". So this records OUTCOMES: what happened the last time we
actually called.

The distinction that keeps it worth having: an auth rejection is the provider
saying no, and turns the lamp red. A timeout is the network, and does not - a
lamp that goes red on a Wi-Fi hiccup and stays red is a lamp people learn to
ignore, which is worse than no lamp at all.
"""

import json
import os
import tempfile
import threading
import time
from pathlib import Path

from provider_errors import safe_provider_message

# Substrings that mean the PROVIDER refused the credential, as opposed to the
# call not reaching it. Matched case-insensitively against the error text.
REFUSAL_MARKERS = (
    "invalid_client",
    "invalid_grant",
    "invalid_token",
    "unauthorized",
    "forbidden",
    "access denied",
    "401",
    "403",
    "expired",
)


def looks_like_refusal(reason: str) -> bool:
    """Did the provider reject us, or did we simply fail to reach it?"""
    text = str(reason or "").lower()
    return any(marker in text for marker in REFUSAL_MARKERS)


class CredentialHealth:
    """Last observed outcome per (scope, profile).

    `scope` is a user id, or "house" for the shared credential. Tracked
    separately because one user's dead key says nothing about anyone else's,
    and market_data being refused says nothing about the trading app - they are
    separate Schwab applications with separate keys and tokens.
    """

    def __init__(self, clock=time.time, path=None):
        self._clock = clock
        self._entries: dict[tuple, dict] = {}
        self._lock = threading.RLock()
        # Persisted because a verdict that dies with the process is barely a
        # verdict: there were six restarts on 2026-08-27, and each one reset a
        # REFUSED credential to unknown, which the status projection then
        # rendered green until the next call happened to fail.
        self._path = Path(path) if path else None
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            # A damaged file must degrade to "nothing observed", never to a
            # crash on startup - this runs during module import.
            return
        if not isinstance(raw, dict):
            return
        for key, entry in raw.items():
            scope, _, profile = str(key).partition("|")
            if profile and isinstance(entry, dict) and "accepted" in entry:
                self._entries[(scope, profile)] = {
                    "accepted": entry.get("accepted"),
                    "lastError": str(entry.get("lastError") or ""),
                    "checkedAt": entry.get("checkedAt"),
                }

    def _save(self) -> None:
        """Called with the lock held. Atomic, so a crash mid-write cannot
        leave a half-file that the next start silently discards."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {f"{scope}|{profile}": entry for (scope, profile), entry in self._entries.items()}
            handle, tmp = tempfile.mkstemp(dir=str(self._path.parent), suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh)
            os.replace(tmp, self._path)
        except Exception:
            # Losing the record is survivable; taking a broker call down
            # because we could not write a status file is not.
            return

    @staticmethod
    def _key(scope, profile) -> tuple:
        return (str(scope or "").strip(), str(profile or "").strip())

    def record_success(self, scope, profile) -> None:
        """A call went through. Clears any previous refusal AND its reason -
        a stale error sitting beside a green lamp is its own confusion."""
        with self._lock:
            self._entries[self._key(scope, profile)] = {
                "accepted": True,
                "lastError": "",
                "checkedAt": self._clock(),
            }
            self._save()

    def record_failure(self, scope, profile, reason) -> None:
        """A call failed. Only a provider REFUSAL changes the verdict."""
        message = safe_provider_message(reason)
        if not looks_like_refusal(message):
            # The network, not the credential. Leave the verdict alone: it was
            # working a moment ago, or we never knew.
            return
        with self._lock:
            self._entries[self._key(scope, profile)] = {
                "accepted": False,
                "lastError": message,
                "checkedAt": self._clock(),
            }
            self._save()

    def status(self, scope, profile) -> dict:
        """accepted is True, False, or None for never-observed.

        None must not read as healthy: a fresh process has made no calls, and
        reporting that as working is how a restart makes a dead credential look
        alive again.
        """
        with self._lock:
            entry = self._entries.get(self._key(scope, profile))
            if entry is None:
                return {"accepted": None, "lastError": "", "checkedAt": None}
            return dict(entry)

    def claim_probe(self, scope, profile, cooldown: float = 600.0) -> bool:
        """Should this caller go and actively ask the provider, right now?

        Needed because the live quote path walks ("trading", "") and stops on
        the first success, so a working trading profile means market_data is
        never called and therefore never observed - and market_data is exactly
        the one Schwab was refusing all day. A verdict that can only be learned
        by accident is not a monitor.

        A CLAIM, not a question: the status endpoint is polled by every open
        page, so without atomically marking the attempt twenty concurrent polls
        would each launch a probe. The claim expires after `cooldown` so a
        probe that crashed or never returned does not block retries forever.
        """
        with self._lock:
            key = self._key(scope, profile)
            entry = self._entries.get(key)
            if entry is not None and entry.get("accepted") is not None:
                # Already known, either way. Re-probing a refused credential
                # every few minutes only makes noise at the provider - the
                # owner has to fix it, and a real success will clear it.
                return False
            now = self._clock()
            claimed_at = (entry or {}).get("probeClaimedAt")
            if claimed_at is not None and (now - float(claimed_at)) < float(cooldown):
                return False
            self._entries[key] = {
                **(entry or {"accepted": None, "lastError": "", "checkedAt": None}),
                "probeClaimedAt": now,
            }
            return True

    def forget(self, scope) -> None:
        """Drop every entry for one scope - used when a user is deleted or
        saves new keys, so a previous key's verdict does not haunt a new one."""
        target = str(scope or "").strip()
        if not target:
            return
        with self._lock:
            for key in [k for k in self._entries if k[0] == target]:
                self._entries.pop(key, None)
            self._save()


def health_scope(user) -> str:
    """Which bucket this caller's credential verdict lives in.

    "house" for the shared credential, otherwise the user's own id. A MISSING
    user resolves to the house, because an absent caller is the server asking
    about its own credential - not a phantom user with an empty id.

    That distinction is not academic. On 2026-08-27 the owner had already
    pasted a working Schwab secret and the lamp stayed dark: four of the five
    status call sites omitted the user, health was looked up under the scope
    "", and a credential the server had just used successfully reported "never
    verified". Failing toward the bucket that is actually maintained means
    forgetting to pass the caller degrades to the right answer rather than to
    silence.
    """
    from user_schwab import serves_own_providers

    record = user if isinstance(user, dict) else {}
    user_id = str(record.get("id") or "").strip()
    if not user_id or not serves_own_providers(record):
        return "house"
    return user_id
