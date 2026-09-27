from __future__ import annotations

"""Resolving the credential the SERVER should use, from more than one source.

Some credentials live in two places at once: a value in .env, and a value the
owner pasted into Settings which the vault encrypts. Where both exist, the
pasted one has to win - otherwise the Settings field is decoration.

That is not hypothetical. As of 2026-08-27 a Tradier token pasted into Settings
was encrypted, stored, reported "configured" on the card, and read by nothing:
every Tradier call constructs a bare TradierClient() which reads
TRADIER_ACCESS_TOKEN from .env. Tradier is refused right now ("Access Token not
approved" from BOTH the production and sandbox endpoints, so not a
misconfiguration), and the owner is about to fetch a replacement. Pasting it
into a void would leave him concluding the new token is bad too, with the real
failure hidden behind a second dead credential.

Kept as its own tested function rather than an inline `or` at each call site,
because three inline versions eventually disagree about what blank means.
"""


def _clean(value) -> str:
    """A usable token, or empty. Never raises on a surprising stored value."""
    if value is None:
        return ""
    try:
        return str(value).strip()
    except Exception:
        return ""


def resolve_tradier_token(saved=None, env=None) -> str:
    """The Tradier token to use: what was pasted, else what is in .env.

    Blank means UNCHANGED, never "delete". The Settings field's own placeholder
    says "leave blank to keep the saved token", so treating an empty box as an
    instruction to disable Tradier would disable it the first time someone
    saved any other field on that card.
    """
    return _clean(saved) or _clean(env)
