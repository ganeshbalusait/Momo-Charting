"""The slicer has to be right, or every test built on it is decorative.

Written against synthetic source rather than api_server.py so the cases are
visible: the point is to prove the slice ENDS where the handler ends, which is
what a fixed byte window failed to do.
"""

import pytest

from handler_source import function_body, route_handler


SOURCE = '''
    def do_POST(self) -> None:
        try:
            if parsed.path == "/api/alpha":
                # A long comment that a fixed window would have pushed the
                # interesting line past, which is exactly how a correct
                # handler starts reading as a regression.
                actor = self._require_admin_user()
                if actor is None:
                    return
                self._send_json(HTTPStatus.OK, {"alpha": True})
                return
            if parsed.path == "/api/beta":
                self._send_json(HTTPStatus.OK, {"beta": True})
                return

            self._send_json(HTTPStatus.NOT_FOUND, {"error": "Unknown route"})
        except Exception as exc:
            self._send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": str(exc)})

    def helper_one(self) -> None:
        return 1

    def helper_two(self) -> None:
        return 2
'''


def test_a_handler_slice_reaches_its_own_end_however_long_it_is():
    alpha = route_handler(SOURCE, "/api/alpha")

    assert "_require_admin_user" in alpha, "the assertion must be inside the slice"
    assert '{"alpha": True}' in alpha


def test_a_handler_slice_stops_before_the_next_route():
    alpha = route_handler(SOURCE, "/api/alpha")

    assert "/api/beta" not in alpha, (
        "bleeding into the next handler would let one route's gate satisfy "
        "an assertion about another - worse than a window that is too small"
    )
    assert '{"beta": True}' not in alpha


def test_the_last_handler_stops_at_the_unknown_route_reply():
    beta = route_handler(SOURCE, "/api/beta")

    assert '{"beta": True}' in beta
    assert "NOT_FOUND" not in beta
    assert "except Exception" not in beta


def test_a_missing_route_says_so_instead_of_returning_nothing():
    # An empty slice makes every `in` assertion fail with a message about the
    # assertion rather than about the route having been renamed.
    with pytest.raises(AssertionError, match="not routed"):
        route_handler(SOURCE, "/api/does-not-exist")


def test_a_function_body_stops_at_the_next_def():
    body = function_body(SOURCE, "helper_one")

    assert "return 1" in body
    assert "return 2" not in body
    assert "helper_two" not in body


def test_a_missing_function_says_so():
    with pytest.raises(AssertionError, match="not defined"):
        function_body(SOURCE, "helper_missing")


def test_it_works_on_the_real_api_server():
    from pathlib import Path

    source = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")

    handler = route_handler(source, "/api/schwab/settings")
    assert "credential_target" in handler
    assert 'if parsed.path == "/api/schwab/token"' not in handler

    body = function_body(source, "_save_user_schwab_credentials")
    assert "save_provider_credentials" in body


# --------------------------------------------------------------------------
# code_only - the other half of the same lesson
# --------------------------------------------------------------------------

from handler_source import code_only  # noqa: E402


def test_a_docstring_naming_a_forbidden_call_is_not_matched():
    """The exact false alarm this exists for.

    The clearest place to explain why a call must not appear is a docstring
    saying so - which is then the thing that trips the grep.
    """
    src = '''
def save_for_user(body):
    """Deliberately cannot reach set_key, os.environ or settings.schwab."""
    return vault.write(body)
'''
    assert "set_key" not in code_only(src)
    assert "vault.write" in code_only(src)


def test_a_comment_naming_a_forbidden_call_is_not_matched():
    src = "\n".join([
        "def handler():",
        "    # Never call set_key() here - it rewrites the shared .env.",
        "    return vault.write()",
    ])
    assert "set_key" not in code_only(src)
    assert "vault.write" in code_only(src)


def test_real_code_still_survives():
    """It must not be so blunt that it hides the thing being guarded."""
    src = '\n'.join([
        'def handler():',
        '    """A docstring."""',
        '    set_key(ENV_PATH, "X", "y")',
    ])
    assert "set_key" in code_only(src)


def test_single_quoted_docstrings_are_stripped_too():
    src = "def f():\n    '''mentions set_key'''\n    return 1\n"
    assert "set_key" not in code_only(src)


def test_an_inline_comment_after_code_does_not_remove_the_code():
    # Only whole comment LINES are dropped; a trailing comment must not take
    # its line's code with it.
    src = 'x = set_key(a)  # this calls set_key\n'
    assert "set_key" in code_only(src)
