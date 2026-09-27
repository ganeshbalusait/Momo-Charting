"""Slice a route handler or function body out of api_server.py by BOUNDARY.

Importing api_server boots every scheduler and opens the live database (see
tests/test_gateway.py), so wiring is checked by reading the source. The
tempting way to do that is a fixed byte window:

    handler = SOURCE[start : start + 900]
    assert "_require_admin_user" in handler

which works until someone adds a comment inside the handler. The assertion
then falls outside the window, a correct change reads as a regression, and
whoever sees it red concludes something is broken that is not. That happened
three times in one week here - twice to security tests, where a false alarm is
expensive because the honest response is to stop and investigate.

The window was never the point. The assertion is the point. These helpers
slice to the handler's real end, so the span cannot drift out from under it.
"""

import re

# Every route dispatch in api_server sits at this indentation inside do_GET /
# do_POST / do_PUT / do_DELETE.
DISPATCH = '\n            if parsed.path == '


def route_handler(source: str, route: str) -> str:
    """Everything from a route's dispatch line to the next route's.

    Raises rather than returning empty if the route is gone: a silently empty
    handler makes every `in` assertion fail with a confusing message, when the
    real problem is that the route was renamed or removed.
    """
    marker = f'if parsed.path == "{route}":'
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"{route} is not routed in api_server.py")

    candidates = [
        source.find(DISPATCH, start + len(marker)),
        # The last handler in a block is followed by the unknown-route reply
        # and the except clauses; either ends it.
        source.find('\n            self._send_json(HTTPStatus.NOT_FOUND', start + len(marker)),
        source.find('\n        except ', start + len(marker)),
    ]
    ends = [i for i in candidates if i != -1]
    return source[start : min(ends)] if ends else source[start:]


def function_body(source: str, name: str, indent: str = "    ") -> str:
    """Everything from a def line to the next def at the same indentation.

    `indent` is four spaces for a method, empty for a module-level function.
    """
    marker = f"\n{indent}def {name}("
    start = source.find(marker)
    if start == -1:
        raise AssertionError(f"{name} is not defined in api_server.py")
    start += 1  # step over the leading newline
    end = source.find(f"\n{indent}def ", start + len(marker))
    return source[start:end] if end != -1 else source[start:]


def code_only(source: str) -> str:
    """Source with docstrings and comment lines removed.

    A test that greps source for a forbidden call eventually fails on a
    SENTENCE describing the thing it forbids - because the clearest place to
    explain why `set_key` must not appear here is a docstring saying exactly
    that. It happened three times in one week:

      * the per-user Schwab save, whose docstring names .env / os.environ /
        settings.schwab precisely to promise it does not touch them,
      * the Tradier helper, whose docstring names TradierClient() as the thing
        it replaces,
      * and a comment referencing an endpoint the same test forbade.

    Each looked like a real regression for a few minutes. Strip the prose and
    the assertion means what it says.

    Deliberately blunt - regex, not a parser. It runs against known files in
    this repo, and a parse of a 19,000-line module to answer "does this line
    call set_key" is the wrong trade.
    """
    without_docstrings = re.sub(r'"""[\s\S]*?"""', "", source)
    without_docstrings = re.sub(r"'''[\s\S]*?'''", "", without_docstrings)
    return "\n".join(
        line for line in without_docstrings.splitlines() if not line.strip().startswith("#")
    )
