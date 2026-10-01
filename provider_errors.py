from __future__ import annotations

"""Make a broker failure readable where the trader actually sees it.

api_server surfaces provider errors as str(exc) straight into the Settings card
and the connection banner. That was safe while data/schwab_client owned the
HTTP call and reduced Akamai's HTML error pages to one actionable line. When
that module moved to schwab-py the reduction went with it, and nothing replaced
it - so a 403 from Akamai, which is an HTML page rather than JSON, would render
as a wall of markup.

This is the replacement, placed at the boundary where the message is shown
rather than inside a library we do not own.

The bar is not tidiness. A truncated message that still says "invalid_client"
is useful, because it tells the owner their app key is wrong; a tidy message
that has thrown that away is worse than the raw blob.
"""

import re

MAX_LENGTH = 400

# The bits that tell someone what to DO. Kept even when the rest is cut.
ACTIONABLE = re.compile(
    r"(invalid_client|invalid_grant|invalid_request|unauthorized|forbidden|"
    r"expired|Access Denied|Reference #[\w.]+|\b[45]\d\d\b)",
    re.IGNORECASE,
)

TAG = re.compile(r"<[^>]+>")
WHITESPACE = re.compile(r"\s+")

EMPTY_FALLBACK = "The broker returned an error with no message. Try again, then re-authenticate in Settings."


def safe_provider_message(error) -> str:
    """One readable line from whatever the provider threw at us."""
    raw = error if isinstance(error, str) else ("" if error is None else str(error))
    text = WHITESPACE.sub(" ", TAG.sub(" ", raw)).strip()
    if not text:
        return EMPTY_FALLBACK
    if len(text) <= MAX_LENGTH:
        return text

    # Too long. Keep the actionable fragments rather than the first 400
    # characters, which on an HTML page is boilerplate and on a stack trace is
    # the preamble - in both cases the part that says nothing.
    found = []
    for match in ACTIONABLE.finditer(text):
        fragment = text[max(0, match.start() - 60) : match.end() + 60].strip()
        if fragment not in found:
            found.append(fragment)
    if found:
        joined = " ... ".join(found)
        return joined if len(joined) <= MAX_LENGTH else joined[: MAX_LENGTH - 3] + "..."

    # Nothing recognisable. Truncate visibly - a silent cut hides that there
    # was more, and someone will report the half-message as the whole error.
    return text[: MAX_LENGTH - 3] + "..."
