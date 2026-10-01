"""Broker errors reach the trader's screen. They must be readable there.

api_server surfaces provider failures as str(exc) straight into the Settings
card and the connection banner. That was safe while data/schwab_client owned
the HTTP call and reduced Akamai's HTML error pages to one actionable line -
tests/test_schwab_oauth.py had a case for exactly that. When the module moved
to schwab-py, both the reduction and its test stopped applying, and nothing
replaced the reduction.

So an Akamai 403 - which is an HTML page, not JSON - would now render as a wall
of markup in the UI. This is the replacement, at the boundary where the message
is actually shown rather than inside a library we do not own.

The bar is: never lose the actionable part. A truncated message that still says
"invalid_client" is useful; a tidy message that has thrown that away is not.
"""

import pytest

from provider_errors import safe_provider_message


def test_an_ordinary_message_is_passed_through_unchanged():
    assert safe_provider_message("invalid_client: Unauthorized") == "invalid_client: Unauthorized"


def test_an_html_error_page_is_reduced_to_its_text():
    html = (
        "<html><head><title>Access Denied</title></head>"
        "<body><h1>Access Denied</h1><p>You don't have permission to access "
        '"http://api.schwabapi.com/v1/oauth/token" on this server.</p>'
        "<p>Reference #18.abcd1234.9876543210</p></body></html>"
    )

    message = safe_provider_message(html)

    assert "<" not in message and ">" not in message
    assert "Access Denied" in message


def test_the_akamai_reference_survives_because_support_asks_for_it():
    html = "<html><body><p>Access Denied</p><p>Reference #18.abcd1234.9876</p></body></html>"

    assert "18.abcd1234.9876" in safe_provider_message(html)


def test_a_very_long_message_is_truncated_visibly():
    message = safe_provider_message("x" * 5000)

    assert len(message) < 500
    assert message.endswith("..."), "silent truncation hides that there was more"


def test_the_actionable_part_is_never_truncated_away():
    """Truncation must not eat the reason.

    A message that says "invalid_client" tells the owner their app key is
    wrong. One truncated to a stack-trace preamble tells them nothing, and
    that is worse than the raw blob.
    """
    noisy = "HTTPError: " + ("padding " * 200) + "invalid_client: Unauthorized"

    message = safe_provider_message(noisy)

    assert "invalid_client" in message


@pytest.mark.parametrize("blank", ["", None, "   ", "\n\n"])
def test_an_empty_error_becomes_something_a_person_can_act_on(blank):
    message = safe_provider_message(blank)

    assert message, "an empty error banner tells the trader nothing at all"
    assert len(message) > 10


def test_repeated_whitespace_from_html_collapses():
    assert safe_provider_message("<p>Access\n\n\n   Denied</p>") == "Access Denied"


def test_a_non_string_does_not_raise():
    # str(exc) is the normal caller, but an exception object or None must not
    # take down the endpoint that is already reporting a failure.
    for value in (ValueError("boom"), 42, None):
        assert isinstance(safe_provider_message(value), str)
