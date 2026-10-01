from __future__ import annotations

"""Cloudflare's Access signing keys, fetched once and kept.

Verifying a token needs the public keys Cloudflare publishes for the team.
Two things this must get right:

  * Not a round trip per request. The keys change rarely; API calls do not.
  * A failed refresh is not the same as having no keys. Cloudflare being
    briefly unreachable must not sign everyone out, so the last good set keeps
    working. Never having fetched successfully is different - there is nothing
    trustworthy to serve, so it refuses and the password login takes over.

Fetches with urllib rather than requests: this is imported by the gateway,
whose safety story is what it does not import.
"""

import json
import threading
import time
import urllib.request

CERTS_PATH = "/cdn-cgi/access/certs"
DEFAULT_TTL_SECONDS = 3600
FETCH_TIMEOUT_SECONDS = 10

# After a failure, wait before trying again. Without this a failing endpoint
# costs EVERY request the full timeout - the failure amplifies instead of
# degrading. Short enough that Cloudflare coming back is noticed without a
# restart; long enough that being down is cheap.
FAILURE_BACKOFF_SECONDS = 60


def _fetch_json(url: str):
    with urllib.request.urlopen(url, timeout=FETCH_TIMEOUT_SECONDS) as response:
        return json.loads(response.read().decode("utf-8"))


def _looks_like_keys(payload) -> bool:
    return isinstance(payload, dict) and isinstance(payload.get("keys"), list) and bool(payload["keys"])


class AccessKeyCache:
    def __init__(self, team_domain: str, *, fetcher=None, ttl_seconds: int = DEFAULT_TTL_SECONDS, clock=time.time):
        self._url = f"{str(team_domain or '').rstrip('/')}{CERTS_PATH}" if team_domain else ""
        self._fetcher = fetcher or _fetch_json
        self._ttl = float(ttl_seconds)
        self._clock = clock
        self._lock = threading.Lock()
        self._keys = None
        self._fetched_at = 0.0
        self._failed_at = None
        self._fetching = False

    def keys(self):
        """The current signing keys, or None if none have ever been fetched.

        Never blocks on someone else's fetch and never retries a failure
        immediately: this is called on every tunnelled request, so anything
        slow here is slow for every visitor at once.
        """
        if not self._url:
            return None

        with self._lock:
            if self._keys is not None and (self._clock() - self._fetched_at) < self._ttl:
                return self._keys
            if self._fetching:
                # Another thread is already on it. Serve what we have rather
                # than queue - a queue behind a 10s timeout is how one slow
                # endpoint stalls every request in the app.
                return self._keys
            if self._failed_at is not None and (self._clock() - self._failed_at) < FAILURE_BACKOFF_SECONDS:
                return self._keys
            self._fetching = True

        # Deliberately OUTSIDE the lock: this is network I/O.
        try:
            payload = self._fetcher(self._url)
        except Exception:
            payload = None

        with self._lock:
            self._fetching = False
            if _looks_like_keys(payload):
                self._keys = payload
                self._fetched_at = self._clock()
                self._failed_at = None
            else:
                # Covers both a raised error and a response that is not keys.
                # Keep serving what we have; refuse only if we never had any.
                self._failed_at = self._clock()
            return self._keys
