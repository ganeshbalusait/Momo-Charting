from __future__ import annotations

"""A Tradier token pasted in Settings has to actually be used.

The vault accepts the slot, encrypts it, and reports it "configured". Every
Tradier call then constructs a bare TradierClient(), which reads
TRADIER_ACCESS_TOKEN from .env and never looks at the vault. So pasting a new
token in Settings looks like it worked and changes nothing.

That ordering matters right now: Tradier is genuinely refused
("Access Token not approved" from BOTH the production and sandbox endpoints, so
not a config mistake), and the owner is about to go and get a replacement. If
he pastes it into a void he will conclude the new token is bad too, and the
real failure - that nothing reads it - stays invisible behind a second dead
credential.

Resolving the token is therefore its own tested unit, rather than an inline
`or` at three call sites that each get to be subtly different.
"""

import pytest

from handler_source import code_only

from house_credentials import resolve_tradier_token


def test_a_saved_token_is_preferred_over_the_env_file():
    """The whole point: pasting in Settings must take effect."""
    assert resolve_tradier_token(saved="SAVED", env="FROM_ENV") == "SAVED"


def test_the_env_token_is_used_when_nothing_is_saved():
    # Existing installs keep working with no migration and no re-paste.
    assert resolve_tradier_token(saved="", env="FROM_ENV") == "FROM_ENV"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_a_blank_saved_token_does_not_erase_a_working_env_token(blank):
    """Clearing the Settings field must not silently disable Tradier.

    The card's placeholder says "leave blank to keep the saved token", so blank
    means "unchanged", not "delete".
    """
    assert resolve_tradier_token(saved=blank, env="FROM_ENV") == "FROM_ENV"


def test_whitespace_around_a_pasted_token_is_stripped():
    # Copy-paste from a web page routinely carries a trailing newline, and the
    # Authorization header would then be malformed for a token that is fine.
    assert resolve_tradier_token(saved="  SAVED\n", env="") == "SAVED"


@pytest.mark.parametrize("both_blank", [("", ""), (None, None), ("  ", "\n")])
def test_no_token_anywhere_is_an_empty_string_not_a_crash(both_blank):
    saved, env = both_blank
    assert resolve_tradier_token(saved=saved, env=env) == ""


def test_a_non_string_does_not_raise():
    # The vault returns whatever was stored; a bad row must not take down every
    # Tradier call site.
    assert isinstance(resolve_tradier_token(saved=123, env=None), str)


# --------------------------------------------------------------------------
# Wiring. Source-read: importing api_server boots every scheduler and opens the
# live database (tests/test_gateway.py).
# --------------------------------------------------------------------------

from pathlib import Path  # noqa: E402

API_SOURCE = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")


def test_no_call_site_builds_a_bare_tradier_client() -> None:
    """One door, so a pasted token cannot be honoured in some places only.

    Three separate call sites each constructing TradierClient() is how the
    Settings field ended up decorative: the vault stored the token and not one
    of them looked at it.
    """
    # Reads CODE, not prose: the helper's own docstring names the thing it
    # exists to replace, and a guard that trips on its own documentation is
    # noise rather than protection. See handler_source.code_only.
    code = code_only(API_SOURCE)
    outside = [
        line.strip()
        for line in code.splitlines()
        if "TradierClient()" in line
        and not line.strip().startswith("#")
        and "client = TradierClient()" not in line
    ]
    assert not outside, f"bare TradierClient() outside the helper: {outside}"


def test_the_helper_never_mutates_the_shared_settings_object() -> None:
    """The .env route broke every user at once by doing exactly this.

    settings.tradier is process-wide. Assigning access_token onto it would make
    one request's credential the whole server's, which is the same class of bug
    as the Schwab .env write.
    """
    start = API_SOURCE.find("def _house_tradier_client(")
    assert start != -1, "_house_tradier_client not found"
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]

    assert "settings.tradier.access_token =" not in body
    assert "replace(" in body, "must build a per-call copy of the config"


def test_a_vault_failure_falls_back_rather_than_breaking_tradier() -> None:
    start = API_SOURCE.find("def _house_tradier_client(")
    body = API_SOURCE[start : API_SOURCE.find("\ndef ", start + 10)]
    assert "except Exception" in body, (
        "a vault read failing must fall back to .env, not take every Tradier "
        "call down with it"
    )
