from __future__ import annotations

"""Verify a Cloudflare Access token and report who it belongs to.

Cloudflare Access checks the visitor's email with a one-time code and then
signs a token saying so. Verifying that signature is what lets AGX sign someone
in without a password: the claim is worthless on its own (anyone can write an
email into a header) and trustworthy once the signature checks out.

A token is accepted ONLY if all of these hold:
  * signed with RS256 by a key Cloudflare currently publishes for this team
  * addressed to THIS Access application (aud) - a valid token for someone
    else's app must not open ours
  * issued by the expected team domain (iss)
  * inside its validity window (nbf/exp)
  * carries an email

Anything else returns None. Nothing raises: this runs in the gateway's request
path, and a malformed token must be a refusal, not an outage. Refusing simply
falls back to the ordinary password login.

Deliberately depends only on `cryptography`, which is already pinned - the
gateway's safety story is what it does not import.
"""

import base64
import json
import time

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

# The only algorithm Cloudflare Access signs with. Pinning it is what makes the
# two classic JWT bypasses impossible: "alg": "none" (no signature at all) and
# "alg": "HS256" (symmetric, verified with the public key anyone can read).
REQUIRED_ALGORITHM = "RS256"

# Tolerance for clock skew between this machine and Cloudflare's edge, applied
# to nbf ONLY. On nbf it prevents rejecting a freshly-minted token because our
# clock runs slow; on exp the same tolerance would keep an expired token alive
# past its deadline, so expiry is checked strictly. A token rejected a few
# seconds early costs nothing - Access hands the browser a fresh one.
LEEWAY_SECONDS = 60


def _decode_segment(segment: str) -> bytes:
    padded = segment + "=" * (-len(segment) % 4)
    return base64.urlsafe_b64decode(padded)


def _decode_json(segment: str) -> dict:
    value = json.loads(_decode_segment(segment).decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("segment was not a JSON object")
    return value


def _decode_int(value: str) -> int:
    return int.from_bytes(_decode_segment(str(value)), "big")


def _public_key_for(jwks, kid: str):
    """The published key with this id, as an RSA public key."""
    try:
        entries = jwks.get("keys")
    except AttributeError:
        return None
    if not isinstance(entries, list):
        return None
    for entry in entries:
        if not isinstance(entry, dict) or str(entry.get("kid") or "") != kid:
            continue
        if str(entry.get("kty") or "") != "RSA":
            continue
        try:
            numbers = rsa.RSAPublicNumbers(
                e=_decode_int(entry["e"]), n=_decode_int(entry["n"])
            )
            return numbers.public_key()
        except Exception:
            return None
    return None


def _claim_matches(claim, expected: str) -> bool:
    """aud may be a single string or a list; iss is always a string."""
    if isinstance(claim, (list, tuple)):
        return any(str(item) == expected for item in claim)
    return str(claim) == expected


def verify_access_token(token, jwks, *, audience: str, issuer: str, now: float | None = None):
    """Return the verified email, or None if the token cannot be trusted."""
    # Unconfigured must refuse rather than wave everyone through: an empty
    # expected audience would otherwise match a token addressed to nothing.
    if not audience or not issuer:
        return None

    try:
        parts = str(token or "").split(".")
        if len(parts) != 3:
            return None
        header = _decode_json(parts[0])
        claims = _decode_json(parts[1])
        signature = _decode_segment(parts[2])
    except Exception:
        return None

    if str(header.get("alg") or "") != REQUIRED_ALGORITHM:
        return None

    public_key = _public_key_for(jwks, str(header.get("kid") or ""))
    if public_key is None:
        return None

    try:
        public_key.verify(
            signature,
            f"{parts[0]}.{parts[1]}".encode("ascii"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
    except InvalidSignature:
        return None
    except Exception:
        return None

    if not _claim_matches(claims.get("aud"), audience):
        return None
    if not _claim_matches(claims.get("iss"), issuer):
        return None

    moment = time.time() if now is None else float(now)
    try:
        expires_at = claims.get("exp")
        if expires_at is None or moment > float(expires_at):
            return None
        not_before = claims.get("nbf")
        if not_before is not None and moment < float(not_before) - LEEWAY_SECONDS:
            return None
    except (TypeError, ValueError):
        return None

    email = str(claims.get("email") or "").strip().lower()
    return email or None


def verified_email(token, keys, *, audience: str, issuer: str) -> str:
    """verify_access_token as a plain string, for header use.

    `keys` may be a JWKS dict or a callable returning one. The callable form
    exists so the signing keys are only asked for when there is actually a
    token to check: most relayed requests carry none, and on the first request
    of the process fetching them is a network round trip nobody needed.
    """
    if not str(token or "").strip():
        return ""
    if callable(keys):
        try:
            keys = keys()
        except Exception:
            return ""
    return verify_access_token(token, keys, audience=audience, issuer=issuer) or ""


# Written by the gateway once a token has been verified, read by api_server.
# Unspoofable for the same reason as the origin stamp: it is not in the
# gateway's FORWARD_REQUEST_HEADERS allowlist, so a client's own copy is
# dropped before the gateway writes its own. A test pins that allowlist.
ACCESS_EMAIL_HEADER = "X-AGX-Access-Email"


def resolve_access_identity(email, find_user):
    """Decide whether a Cloudflare-verified address may sign in.

    Returns (user, status). Status is "" when Cloudflare said nothing, "ok"
    when they may in, and otherwise the reason to show them: "no_account" (the
    address is on the Cloudflare list but nobody has created it in Settings),
    "disabled" (switched off here, which must outrank Cloudflare's opinion) or
    "error".

    No account is created here. Cloudflare deciding who reaches the door is
    deliberately not the same as deciding who has a key.
    """
    address = str(email or "").strip().lower()
    if not address:
        return None, ""
    try:
        user = find_user(address)
    except Exception:
        return None, "error"
    if not user:
        return None, "no_account"
    if not user.get("isActive"):
        return None, "disabled"
    # Copied, never mutated - the caller owns that dict. must_change_password
    # is dropped because they signed in without ever using a password; the
    # forced-change screen would strand them in Settings with nothing to do.
    return {**user, "mustChangePassword": False, "signedInVia": "cloudflare"}, "ok"
