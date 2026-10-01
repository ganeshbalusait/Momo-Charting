"""The keeper must not be able to outrank the trader.

Importing api_server boots every scheduler and opens the live database, so the
wiring is checked by reading the source. code_only() strips prose first -
these guards are all explained in comments that name the very calls being
forbidden, which is how this style of test cries wolf.
"""
import re
from pathlib import Path

from handler_source import code_only, function_body

ROOT = Path(__file__).resolve().parent.parent
API = code_only((ROOT / "api_server.py").read_text(encoding="utf-8"))
KEEPER = (ROOT / "scripts" / "agx_keeper.py").read_text(encoding="utf-8")


def test_every_get_is_classified_before_it_is_served():
    body = function_body(API, "do_GET")
    assert "background_scope(is_background_request(self.headers))" in body, (
        "the handler is the only frame that can see request headers; if the "
        "scope is not opened here nothing downstream can tell chore from human"
    )


def test_the_interactive_pause_ignores_background_callers():
    body = function_body(API, "touch_oi_finder_interactive_window")
    guard = body.index("in_background_request()")
    setter = body.index("oi_finder_interactive_until")
    assert guard < setter, (
        "the guard must come BEFORE the window is extended, or the keeper "
        "still holds the 45s pause open on every one of ~100 touches a cycle"
    )


def test_build_lane_priority_ignores_background_callers():
    body = function_body(API, "_note_chart_symbol_requested")
    guard = body.index("in_background_request()")
    record = body.index("_chart_symbol_last_requested")
    assert guard < record, (
        "recording the touch is what grants interactive priority in "
        "ChartBuildLane - guarding after it would be no guard at all"
    )


def test_a_real_browser_request_is_still_treated_as_interactive():
    # The costly direction of this change is demoting the trader. Nothing may
    # gate these on anything except the background flag.
    for name in ("touch_oi_finder_interactive_window", "_note_chart_symbol_requested"):
        body = function_body(API, name)
        returns = re.findall(r"^\s+return\s*$", body, re.M)
        assert len(returns) == 1, f"{name}: expected exactly one early return, got {len(returns)}"


def test_the_keeper_and_the_server_agree_on_the_header_name():
    # Two constants in two files that must match exactly. If they drift, every
    # keeper touch silently goes back to impersonating the trader - no error,
    # no failed request, just the old behaviour quietly restored. That is the
    # only way this fix can break, so it is the one thing pinned hardest.
    from request_context import BACKGROUND_HEADER

    found = re.search(r'^BACKGROUND_HEADER = "([^"]+)"', KEEPER, re.M)
    assert found, "the keeper must declare the header it sends"
    assert found.group(1) == BACKGROUND_HEADER


def test_the_keeper_actually_sends_the_header():
    body = code_only(KEEPER)
    touch = body[body.index("def touch("):]
    touch = touch[: touch.index("\ndef ")]
    assert "BACKGROUND_HEADER" in touch, "the touch must carry the header, not just define it"
    assert "headers=" in touch
