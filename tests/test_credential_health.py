from __future__ import annotations

"""Does the credential WORK, or is it merely present?

Every health field in this app answers the second question. SchwabClient's
`configured` is `client_id and client_secret and refresh_token` - three values
being non-empty. `refreshTokenValid` adds local arithmetic on an expiry date we
wrote down ourselves. Neither has ever asked Schwab anything.

Live proof, 2026-08-27: the owner's market-data profile reports
configured=true, credentialsConfigured=true, refreshTokenValid=true - a green
TOS DATA lamp - while every call returns "invalid_client: Unauthorized" and no
chart data has loaded for a day. The credential is present, correctly shaped,
locally unexpired, and rejected.

This is the third instance of one pattern in a week: `analyticsDeferred` meaning
"complete for the narrower thing I was built for", `configured` meaning "a value
is present", and now a green lamp meaning "we have a string". So health is
recorded from OUTCOMES here - what happened when we last actually called.

The distinction that keeps it usable: an auth rejection is the provider saying
no, and must turn the lamp red. A timeout is the network, and must not - a lamp
that goes red on a Wi-Fi hiccup and stays red is one people learn to ignore.
"""

import threading

import pytest

from credential_health import CredentialHealth


@pytest.fixture
def health():
    return CredentialHealth()


OWNER = ("house", "market_data")


# --------------------------------------------------------------------------
# Nothing observed yet
# --------------------------------------------------------------------------


def test_before_any_call_the_answer_is_unknown_not_healthy(health):
    """Unknown must not read as working.

    A fresh process has made no calls. Reporting that as healthy is how a
    restart makes a dead credential look alive again.
    """
    assert health.status(*OWNER)["accepted"] is None


def test_unknown_carries_no_error_text(health):
    assert health.status(*OWNER)["lastError"] == ""


# --------------------------------------------------------------------------
# Outcomes
# --------------------------------------------------------------------------


def test_a_successful_call_marks_the_credential_accepted(health):
    health.record_success(*OWNER)

    assert health.status(*OWNER)["accepted"] is True


def test_an_auth_rejection_marks_it_refused_and_keeps_the_reason(health):
    health.record_failure(*OWNER, "invalid_client: Unauthorized")

    status = health.status(*OWNER)
    assert status["accepted"] is False
    assert "invalid_client" in status["lastError"], (
        "the reason is what tells the owner whether to fix the key or the token"
    )


@pytest.mark.parametrize("reason", [
    "invalid_client: Unauthorized",
    "invalid_grant",
    "401 Client Error: Unauthorized",
    "HTTP 403 Forbidden",
    "Access Denied",
    "refresh token expired",
])
def test_provider_refusals_all_turn_the_lamp_red(health, reason):
    health.record_failure(*OWNER, reason)

    assert health.status(*OWNER)["accepted"] is False


@pytest.mark.parametrize("blip", [
    "timed out",
    "Connection reset by peer",
    "Temporary failure in name resolution",
    "[Errno 11001] getaddrinfo failed",
    "503 Server Error: Service Unavailable",
    "Read timeout on endpoint",
])
def test_a_network_blip_does_not_condemn_the_credential(health, blip):
    """The credential is not what failed.

    A lamp that goes red because the Wi-Fi dropped, and stays red, is a lamp
    people stop believing - which costs more than having no lamp.
    """
    health.record_success(*OWNER)
    health.record_failure(*OWNER, blip)

    assert health.status(*OWNER)["accepted"] is True


def test_a_blip_before_any_success_still_leaves_it_unknown(health):
    health.record_failure(*OWNER, "timed out")

    assert health.status(*OWNER)["accepted"] is None


def test_recovery_clears_a_previous_refusal(health):
    health.record_failure(*OWNER, "invalid_client: Unauthorized")
    health.record_success(*OWNER)

    status = health.status(*OWNER)
    assert status["accepted"] is True
    assert status["lastError"] == "", "a stale reason next to a green lamp is its own confusion"


# --------------------------------------------------------------------------
# Scoping - one user's broken key must not condemn another's
# --------------------------------------------------------------------------


def test_each_user_and_profile_is_tracked_separately(health):
    health.record_failure("user-a", "market_data", "invalid_client")
    health.record_success("user-b", "market_data")
    health.record_success("house", "market_data")

    assert health.status("user-a", "market_data")["accepted"] is False
    assert health.status("user-b", "market_data")["accepted"] is True
    assert health.status("house", "market_data")["accepted"] is True


def test_the_two_schwab_profiles_do_not_share_health(health):
    # market_data being rejected says nothing about the trading app: separate
    # Schwab applications, separate keys, separate tokens.
    health.record_failure("house", "market_data", "invalid_client")
    health.record_success("house", "trading")

    assert health.status("house", "market_data")["accepted"] is False
    assert health.status("house", "trading")["accepted"] is True


def test_forgetting_a_user_drops_only_their_entries(health):
    health.record_failure("user-a", "market_data", "invalid_client")
    health.record_success("user-b", "market_data")

    health.forget("user-a")

    assert health.status("user-a", "market_data")["accepted"] is None
    assert health.status("user-b", "market_data")["accepted"] is True


# --------------------------------------------------------------------------
# Robustness - this sits on the live quote path
# --------------------------------------------------------------------------


def test_concurrent_recording_does_not_corrupt_the_state(health):
    def hammer(index):
        for _ in range(50):
            if index % 2:
                health.record_success("house", "market_data")
            else:
                health.record_failure("house", "market_data", "invalid_client")

    threads = [threading.Thread(target=hammer, args=(i,), daemon=True) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert health.status(*OWNER)["accepted"] in (True, False)


def test_a_non_string_reason_does_not_raise(health):
    health.record_failure(*OWNER, ValueError("invalid_client"))

    assert health.status(*OWNER)["accepted"] is False


def test_the_stored_reason_is_readable_rather_than_raw(health):
    health.record_failure(*OWNER, "<html><body><p>Access Denied</p></body></html>")

    assert "<" not in health.status(*OWNER)["lastError"]


# --------------------------------------------------------------------------
# Surviving a restart
#
# Found 2026-08-27 by another session: accepted resets to None on every
# restart, and there were six restarts that day. Combined with the projection
# below, that meant a refused credential showed GREEN again after every one,
# until the next call happened to fail. "Unknown is never healthy" was applied
# to the field and not to what the lamp reads - the rule was right and the
# wiring did not honour it.
# --------------------------------------------------------------------------


def test_a_refusal_survives_a_restart(tmp_path):
    path = tmp_path / "health.json"
    first = CredentialHealth(path=path)
    first.record_failure("house", "market_data", "invalid_client: Unauthorized")

    revived = CredentialHealth(path=path)

    status = revived.status("house", "market_data")
    assert status["accepted"] is False, (
        "a restart must not resurrect a dead credential - six restarts a day "
        "means six windows where a refused key reads as working"
    )
    assert "invalid_client" in status["lastError"]


def test_a_success_survives_a_restart_too(tmp_path):
    path = tmp_path / "health.json"
    CredentialHealth(path=path).record_success("house", "trading")

    assert CredentialHealth(path=path).status("house", "trading")["accepted"] is True


def test_a_later_success_overwrites_the_stored_refusal(tmp_path):
    path = tmp_path / "health.json"
    first = CredentialHealth(path=path)
    first.record_failure("house", "market_data", "invalid_client")
    first.record_success("house", "market_data")

    assert CredentialHealth(path=path).status("house", "market_data")["accepted"] is True


def test_a_missing_or_corrupt_file_is_not_a_crash(tmp_path):
    missing = CredentialHealth(path=tmp_path / "nope.json")
    assert missing.status("house", "market_data")["accepted"] is None

    corrupt = tmp_path / "bad.json"
    corrupt.write_text("{not json at all", encoding="utf-8")
    assert CredentialHealth(path=corrupt).status("house", "market_data")["accepted"] is None


def test_forgetting_a_user_also_forgets_them_on_disk(tmp_path):
    path = tmp_path / "health.json"
    health = CredentialHealth(path=path)
    health.record_failure("user-a", "market_data", "invalid_client")
    health.forget("user-a")

    assert CredentialHealth(path=path).status("user-a", "market_data")["accepted"] is None


def test_health_with_no_path_still_works_in_memory():
    # The tests above all pass a path; the default must not require one.
    health = CredentialHealth()
    health.record_success("house", "trading")
    assert health.status("house", "trading")["accepted"] is True


# --------------------------------------------------------------------------
# Probing what nothing else exercises
#
# The live quote path walks ("trading", "") and stops on the first success, so
# when the trading profile works, market_data is NEVER called - and therefore
# never observed. That is the profile Schwab was refusing all day. A verdict
# that can only be learned by accident is not a monitor.
#
# claim_probe decides when to go and ask. It is a CLAIM rather than a question
# because the status endpoint is polled by every open page: without atomically
# marking the attempt, twenty concurrent polls would each launch a probe.
# --------------------------------------------------------------------------


def test_a_profile_with_no_verdict_is_worth_probing(health):
    assert health.claim_probe("house", "market_data", cooldown=600) is True


def test_a_profile_already_known_good_is_not_probed(health):
    health.record_success("house", "trading")

    assert health.claim_probe("house", "trading", cooldown=600) is False


def test_a_profile_already_known_refused_is_not_probed(health):
    # Re-probing a rejected credential every few minutes just makes noise at
    # the provider; the owner has to fix it, and a success will clear it.
    health.record_failure("house", "market_data", "invalid_client")

    assert health.claim_probe("house", "market_data", cooldown=600) is False


def test_only_one_of_many_concurrent_pollers_claims_the_probe(health):
    """Every open page polls status. Twenty pollers must not send twenty probes."""
    claims = []

    def poll():
        claims.append(health.claim_probe("house", "market_data", cooldown=600))

    threads = [threading.Thread(target=poll, daemon=True) for _ in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)

    assert sum(1 for c in claims if c) == 1, f"{sum(claims)} probes claimed, expected 1"


def test_the_claim_expires_so_a_failed_probe_is_retried():
    ticks = {"now": 1000.0}
    health = CredentialHealth(clock=lambda: ticks["now"])

    assert health.claim_probe("house", "market_data", cooldown=600) is True
    assert health.claim_probe("house", "market_data", cooldown=600) is False

    # A probe that crashed or never returned must not block forever.
    ticks["now"] += 601
    assert health.claim_probe("house", "market_data", cooldown=600) is True


# --------------------------------------------------------------------------
# Which bucket a caller's health lives in
#
# Found 2026-08-27, after the owner had ALREADY pasted a working Schwab secret
# and the lamp stayed dark. Four of the five _schwab_status_payload call sites
# omitted the user, so health was looked up under scope "" - a bucket nothing
# ever writes - and every one of them reported "never verified" for a
# credential the server had just used successfully.
#
# A missing user is the SERVER asking, not a phantom user. It must resolve to
# the house bucket, so that forgetting to pass the caller degrades to the right
# answer instead of to silence.
# --------------------------------------------------------------------------

from credential_health import health_scope  # noqa: E402


def test_no_user_means_the_house_not_an_empty_bucket():
    for nobody in (None, {}, {"role": "admin"}):
        assert health_scope(nobody) == "house", (
            "an absent caller is the server itself; resolving to '' loses the "
            "verdict silently, which is how a working credential read as dark"
        )


def test_an_administrator_is_the_house():
    assert health_scope({"id": "a", "role": "admin", "isAdmin": True}) == "house"


def test_a_normal_user_gets_their_own_bucket():
    assert health_scope({"id": "u1", "role": "user", "isAdmin": False}) == "u1"


def test_a_user_without_an_id_falls_back_to_the_house():
    # Fails toward the bucket that is actually maintained, never toward "".
    assert health_scope({"role": "user", "isAdmin": False}) == "house"


def test_it_agrees_with_the_provider_routing_rule():
    """One rule again: whoever's key serves you owns the verdict about it."""
    from user_schwab import serves_own_providers

    for user in (
        {"id": "a", "role": "admin", "isAdmin": True},
        {"id": "u1", "role": "user", "isAdmin": False},
    ):
        expected = str(user["id"]) if serves_own_providers(user) else "house"
        assert health_scope(user) == expected
