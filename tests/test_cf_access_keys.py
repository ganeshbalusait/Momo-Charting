from __future__ import annotations

"""Cloudflare's signing keys: fetched once, cached, and survivable.

Verification needs Cloudflare's public keys. Fetching them per request would
put a network round trip in front of every API call, and treating a failed
refresh as "no keys" would sign everyone out the moment the network hiccuped -
so the last good set is kept. Having never fetched them successfully is
different from a failed refresh, and must refuse.
"""

import pytest

from cf_access_keys import AccessKeyCache


TEAM = "https://agxtrade.cloudflareaccess.com"
KEYS_A = {"keys": [{"kid": "a", "kty": "RSA", "n": "x", "e": "AQAB"}]}
KEYS_B = {"keys": [{"kid": "b", "kty": "RSA", "n": "y", "e": "AQAB"}]}


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


class FakeFetcher:
    def __init__(self, *results):
        self.results = list(results)
        self.calls = []

    def __call__(self, url):
        self.calls.append(url)
        result = self.results.pop(0) if len(self.results) > 1 else self.results[0]
        if isinstance(result, Exception):
            raise result
        return result


def _cache(fetcher, clock=None, ttl=3600):
    return AccessKeyCache(TEAM, fetcher=fetcher, ttl_seconds=ttl, clock=clock or FakeClock())


def test_keys_are_fetched_from_the_teams_certificate_endpoint():
    fetcher = FakeFetcher(KEYS_A)

    assert _cache(fetcher).keys() == KEYS_A
    assert fetcher.calls == ["https://agxtrade.cloudflareaccess.com/cdn-cgi/access/certs"]


def test_a_trailing_slash_on_the_team_domain_does_not_double_up():
    fetcher = FakeFetcher(KEYS_A)
    AccessKeyCache(TEAM + "/", fetcher=fetcher, ttl_seconds=3600, clock=FakeClock()).keys()

    assert fetcher.calls == ["https://agxtrade.cloudflareaccess.com/cdn-cgi/access/certs"]


def test_repeated_reads_do_not_refetch():
    fetcher = FakeFetcher(KEYS_A)
    cache = _cache(fetcher)

    for _ in range(50):
        cache.keys()

    assert len(fetcher.calls) == 1, "a network round trip per API call is not acceptable"


def test_keys_are_refreshed_once_the_cache_goes_stale():
    fetcher = FakeFetcher(KEYS_A, KEYS_B)
    clock = FakeClock()
    cache = _cache(fetcher, clock=clock, ttl=3600)

    assert cache.keys() == KEYS_A
    clock.advance(3601)

    assert cache.keys() == KEYS_B, "Cloudflare rotates keys; a stale set stops verifying"


def test_a_failed_refresh_keeps_serving_the_last_good_keys():
    fetcher = FakeFetcher(KEYS_A, OSError("network down"))
    clock = FakeClock()
    cache = _cache(fetcher, clock=clock)

    assert cache.keys() == KEYS_A
    clock.advance(3601)

    assert cache.keys() == KEYS_A, (
        "a transient network failure must not sign everyone out"
    )


def test_never_having_fetched_successfully_refuses():
    # Different from a failed refresh: there is nothing trustworthy to serve,
    # so verification must refuse and fall back to the password login.
    assert _cache(FakeFetcher(OSError("network down"))).keys() is None


@pytest.mark.parametrize("junk", [None, "", [], {"no_keys_here": 1}, "not-json"])
def test_a_nonsense_response_is_not_cached_as_if_it_were_keys(junk):
    assert _cache(FakeFetcher(junk)).keys() is None


def test_an_empty_team_domain_never_fetches():
    fetcher = FakeFetcher(KEYS_A)
    assert AccessKeyCache("", fetcher=fetcher, ttl_seconds=3600, clock=FakeClock()).keys() is None
    assert fetcher.calls == []


# ---------------------------------------------------------------------------
# Failure must not amplify.
#
# The first version retried on EVERY request after a failure (a failed fetch
# left _fetched_at untouched) and held the lock across the network call. A
# Cloudflare blip would therefore have turned into an app-wide stall: every
# tunnelled request paying the 10s timeout, one at a time, in a queue.
# ---------------------------------------------------------------------------

import threading  # noqa: E402


def test_a_failed_fetch_is_not_retried_on_the_very_next_request():
    fetcher = FakeFetcher(OSError("network down"))
    cache = _cache(fetcher)

    for _ in range(20):
        cache.keys()

    assert len(fetcher.calls) == 1, (
        f"retried {len(fetcher.calls)} times - a failing endpoint would cost "
        "every request the full timeout"
    )


def test_a_failed_fetch_is_retried_once_the_backoff_expires():
    # Backing off must not mean giving up: Cloudflare coming back has to be
    # noticed without a restart.
    fetcher = FakeFetcher(OSError("network down"), KEYS_A)
    clock = FakeClock()
    cache = _cache(fetcher, clock=clock)

    assert cache.keys() is None
    clock.advance(3600)

    assert cache.keys() == KEYS_A


def test_a_nonsense_response_also_backs_off():
    fetcher = FakeFetcher({"not": "keys"})
    cache = _cache(fetcher)

    for _ in range(10):
        cache.keys()

    assert len(fetcher.calls) == 1


def test_callers_do_not_queue_behind_an_in_flight_fetch():
    """One slow fetch must not stall every other request.

    Uses real threads because the property under test IS the locking: a
    single-threaded test would pass against the broken version.
    """
    started = threading.Event()
    release = threading.Event()

    def slow_fetcher(url):
        started.set()
        release.wait(10)
        return KEYS_A

    cache = _cache(slow_fetcher)
    first = threading.Thread(target=cache.keys, daemon=True)
    first.start()
    assert started.wait(5), "the first fetch never started"

    finished = threading.Event()

    def second_caller():
        cache.keys()
        finished.set()

    second = threading.Thread(target=second_caller, daemon=True)
    second.start()
    try:
        assert finished.wait(5), (
            "a second caller blocked behind the in-flight fetch - with the "
            "10s urlopen timeout this is how one slow endpoint stalls the app"
        )
    finally:
        release.set()
        first.join(10)
        second.join(10)


def test_only_one_thread_fetches_at_a_time():
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow_fetcher(url):
        calls.append(url)
        started.set()
        release.wait(10)
        return KEYS_A

    cache = _cache(slow_fetcher)
    threads = [threading.Thread(target=cache.keys, daemon=True) for _ in range(5)]
    threads[0].start()
    assert started.wait(5)
    for t in threads[1:]:
        t.start()
    for t in threads[1:]:
        t.join(5)
    release.set()
    for t in threads:
        t.join(10)

    assert len(calls) == 1, f"{len(calls)} concurrent fetches for the same keys"
