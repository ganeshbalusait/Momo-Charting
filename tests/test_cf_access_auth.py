from __future__ import annotations

"""Verifying Cloudflare Access tokens.

This decides who is signed in without a password, so every test here is an
attack as much as a feature check. Real RSA keys are generated and real tokens
signed - a mocked verifier would prove nothing about whether a forged token is
actually rejected.

The rule the implementation must honour: return an email ONLY for a token this
Access application signed, addressed to it, and still valid. Everything else
returns None. It never raises, because it runs in the gateway's request path.
"""

import base64
import json
import time

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from cf_access_auth import verify_access_token


TEAM_DOMAIN = "https://agxtrade.cloudflareaccess.com"
AUDIENCE = "774f335383b1e397e3add28873be2d558255666538856b1b35ee32fc39392ca1"
EMAIL = "member@example.com"


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64_int(value: int) -> str:
    return _b64(value.to_bytes((value.bit_length() + 7) // 8, "big"))


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(private_key, kid: str = "key-1") -> dict:
    numbers = private_key.public_key().public_numbers()
    return {
        "keys": [
            {
                "kid": kid,
                "kty": "RSA",
                "alg": "RS256",
                "use": "sig",
                "n": _b64_int(numbers.n),
                "e": _b64_int(numbers.e),
            }
        ]
    }


def _token(private_key, *, kid="key-1", alg="RS256", claims=None, tamper=False) -> str:
    now = int(time.time())
    payload = {
        "iss": TEAM_DOMAIN,
        "aud": [AUDIENCE],
        "email": EMAIL,
        "exp": now + 3600,
        "iat": now - 10,
        "nbf": now - 10,
    }
    payload.update(claims or {})
    header = _b64(json.dumps({"alg": alg, "kid": kid}).encode())
    body = _b64(json.dumps(payload).encode())
    signature = private_key.sign(
        f"{header}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )
    if tamper:
        evil = dict(payload, email="attacker@example.com")
        body = _b64(json.dumps(evil).encode())
    return f"{header}.{body}.{_b64(signature)}"


def _verify(token, jwks, **overrides):
    kwargs = {"audience": AUDIENCE, "issuer": TEAM_DOMAIN}
    kwargs.update(overrides)
    return verify_access_token(token, jwks, **kwargs)


# --------------------------------------------------------------------------
# The happy path
# --------------------------------------------------------------------------


def test_a_properly_signed_token_yields_the_email(keypair):
    assert _verify(_token(keypair), _jwks(keypair)) == EMAIL


def test_the_email_is_normalised_to_lower_case(keypair):
    token = _token(keypair, claims={"email": "Member@Example.COM"})
    assert _verify(token, _jwks(keypair)) == EMAIL


def test_the_right_key_is_selected_when_cloudflare_publishes_several(keypair, other_keypair):
    rotating = {"keys": _jwks(other_keypair, kid="old-key")["keys"] + _jwks(keypair)["keys"]}
    assert _verify(_token(keypair), rotating) == EMAIL


# --------------------------------------------------------------------------
# Forgery
# --------------------------------------------------------------------------


def test_a_token_signed_by_another_key_is_rejected(keypair, other_keypair):
    # The whole point: anyone can mint a token, only Cloudflare can sign one.
    forged = _token(other_keypair)
    assert _verify(forged, _jwks(keypair)) is None


def test_editing_the_payload_after_signing_is_rejected(keypair):
    assert _verify(_token(keypair, tamper=True), _jwks(keypair)) is None


def test_an_unsigned_token_is_rejected(keypair):
    # alg=none is the classic JWT bypass; only RS256 may be accepted.
    now = int(time.time())
    header = _b64(json.dumps({"alg": "none", "kid": "key-1"}).encode())
    body = _b64(json.dumps({"iss": TEAM_DOMAIN, "aud": [AUDIENCE], "email": EMAIL, "exp": now + 60}).encode())
    assert _verify(f"{header}.{body}.", _jwks(keypair)) is None


def test_a_symmetric_algorithm_is_rejected(keypair):
    # HS256 signed with the PUBLIC key is the other classic bypass.
    assert _verify(_token(keypair, alg="HS256"), _jwks(keypair)) is None


def test_a_token_for_an_unknown_key_id_is_rejected(keypair):
    assert _verify(_token(keypair, kid="not-published"), _jwks(keypair)) is None


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


def test_a_token_for_a_different_application_is_rejected(keypair):
    # A valid Cloudflare token for someone ELSE's app must not open ours.
    token = _token(keypair, claims={"aud": ["a-different-application"]})
    assert _verify(token, _jwks(keypair)) is None


def test_a_token_from_a_different_team_is_rejected(keypair):
    token = _token(keypair, claims={"iss": "https://someone-else.cloudflareaccess.com"})
    assert _verify(token, _jwks(keypair)) is None


def test_an_expired_token_is_rejected(keypair):
    now = int(time.time())
    token = _token(keypair, claims={"exp": now - 5, "iat": now - 600, "nbf": now - 600})
    assert _verify(token, _jwks(keypair)) is None


def test_a_token_that_is_not_valid_yet_is_rejected(keypair):
    now = int(time.time())
    token = _token(keypair, claims={"nbf": now + 600, "exp": now + 1200})
    assert _verify(token, _jwks(keypair)) is None


def test_a_token_with_no_email_is_rejected(keypair):
    token = _token(keypair, claims={"email": ""})
    assert _verify(token, _jwks(keypair)) is None


def test_a_token_with_no_expiry_is_rejected(keypair):
    token = _token(keypair, claims={"exp": None})
    assert _verify(token, _jwks(keypair)) is None


# --------------------------------------------------------------------------
# Robustness - this runs in the gateway's request path
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "junk", ["", None, "not-a-token", "a.b", "a.b.c.d", "!!!.???.***", "a." + "b" * 5000 + ".c"]
)
def test_malformed_input_returns_none_without_raising(junk, keypair):
    assert _verify(junk, _jwks(keypair)) is None


@pytest.mark.parametrize("broken_jwks", [None, {}, {"keys": []}, {"keys": "nonsense"}, "nope"])
def test_unusable_keys_return_none_without_raising(broken_jwks, keypair):
    assert _verify(_token(keypair), broken_jwks) is None


def test_missing_configuration_refuses_rather_than_waving_through(keypair):
    jwks = _jwks(keypair)
    assert _verify(_token(keypair), jwks, audience="") is None
    assert _verify(_token(keypair), jwks, issuer="") is None


# --------------------------------------------------------------------------
# Don't do work for a request that cannot possibly be verified.
#
# Most relayed requests carry no Access token at all. Asking the key cache for
# keys before checking that is pointless work on the hot path, and on the very
# first request of the process it is a network fetch nobody needed.
# --------------------------------------------------------------------------


class CountingProvider:
    def __init__(self, jwks):
        self.jwks = jwks
        self.calls = 0

    def __call__(self):
        self.calls += 1
        return self.jwks


def test_no_keys_are_fetched_when_there_is_no_token(keypair):
    from cf_access_auth import verified_email

    provider = CountingProvider(_jwks(keypair))
    for blank in ["", None, "   "]:
        assert verified_email(blank, provider, audience=AUDIENCE, issuer=TEAM_DOMAIN) == ""

    assert provider.calls == 0, "asked for signing keys with nothing to verify"


def test_keys_are_fetched_when_there_is_a_token(keypair):
    from cf_access_auth import verified_email

    provider = CountingProvider(_jwks(keypair))
    assert verified_email(_token(keypair), provider, audience=AUDIENCE, issuer=TEAM_DOMAIN) == EMAIL
    assert provider.calls == 1


def test_a_plain_keys_dict_still_works(keypair):
    # The provider form is an optimisation, not a new requirement.
    from cf_access_auth import verified_email

    assert verified_email(_token(keypair), _jwks(keypair), audience=AUDIENCE, issuer=TEAM_DOMAIN) == EMAIL


def test_a_provider_that_blows_up_refuses_rather_than_raising(keypair):
    from cf_access_auth import verified_email

    def explode():
        raise OSError("cloudflare unreachable")

    assert verified_email(_token(keypair), explode, audience=AUDIENCE, issuer=TEAM_DOMAIN) == ""
