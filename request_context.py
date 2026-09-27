from __future__ import annotations

"""Whether the request being served is a background chore or a person.

The chart engine already has good priority machinery. ChartBuildLane serves a
symbol someone is watching ahead of an abandoned kick, and
touch_oi_finder_interactive_window() pauses background scanning for 45 seconds
whenever a trader-facing payload is requested. Both decide from the same
signal: that a chart was requested over HTTP.

That was a true proxy for "a human is looking" when it was written. It stopped
being true when scripts/agx_keeper.py started touching up to 120 symbols per
cycle through the same endpoint the browser uses. Measured 2026-08-28: about 50
of 399 tapes were current, so the keeper touched ~100 symbols every cycle, and
each touch reset the 45s "a human is watching" pause and claimed interactive
priority in the build lane - against the chart actually on screen. The comment
at the dashboard call site states the invariant that had quietly become false:
"Every caller of this method is request-driven, so no background worker can
grant itself priority."

So the chore says so, in a header, and the priority code gets to ask.

Thread-local rather than a parameter: the marking happens deep inside the
serve path, several frames below the handler, and threading a flag through
every signature invites exactly one call site being missed - which is how this
class of bug arrives in the first place.
"""

import threading
from contextlib import contextmanager

BACKGROUND_HEADER = "X-AGX-Background"

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})

_local = threading.local()


def is_background_request(headers) -> bool:
    """Did this client declare itself a background chore?

    Fails toward FOREGROUND on anything unexpected. The costly mistake is
    demoting a real trader - his own chart would then queue behind the
    keeper's - whereas wrongly treating a chore as a person only means we do
    too much work, which is the behaviour we already have today.
    """
    try:
        value = headers.get(BACKGROUND_HEADER)
        if value is None:
            # http.client.HTTPMessage is case-insensitive, but a plain dict in
            # a test or a rewriting proxy is not.
            for key in (BACKGROUND_HEADER.lower(), BACKGROUND_HEADER.upper()):
                value = headers.get(key)
                if value is not None:
                    break
    except Exception:
        return False
    if value is None:
        return False
    try:
        return str(value).strip().lower() in _TRUE_VALUES
    except Exception:
        return False


def in_background_request() -> bool:
    """Is the work happening on this thread a background chore?"""
    return bool(getattr(_local, "background", False))


@contextmanager
def background_scope(background: bool):
    """Mark this thread for the duration of one request.

    Restores rather than clears, so a nested scope cannot leak. http.server
    reuses worker threads, and a scope left set by a raised request would
    silently demote whichever real request landed on that thread next.
    """
    previous = getattr(_local, "background", False)
    _local.background = bool(background)
    try:
        yield
    finally:
        _local.background = previous
