from __future__ import annotations

"""Trading actions belong to the account owner, not to everyone signed in.

AGX now has real non-admin users who reach it through Cloudflare. Every route
below mutates the owner's trading state - closing positions, running the bots,
rewriting risk sizing, switching broker profile - and each sat behind a plain
session check, so any signed-in user could call them directly.

Paper trading today (ALLOW_LIVE_TRADING is unset), so the blast radius is the
owner's paper book and bot configuration rather than real money. That is the
reason this is a same-day fix and not an emergency; it is not a reason to leave
it, because the flag is one edit away from being true.

Read from source: importing api_server boots every scheduler and opens the live
database (tests/test_gateway.py).
"""

from pathlib import Path

import pytest
from handler_source import function_body, route_handler

API_SOURCE = (Path(__file__).resolve().parent.parent / "api_server.py").read_text(encoding="utf-8")

# Every route that changes the owner's money, positions or automation.
OWNER_ONLY_ROUTES = [
    "/api/execute-best-trade",
    "/api/execute-all-trades",
    "/api/close-position",
    "/api/close-all-positions",
    "/api/close-option-position",
    "/api/close-all-option-positions",
    "/api/account-select",
    "/api/risk-settings",
    "/api/option-risk-settings",
    "/api/option-bot-settings",
    "/api/bot-control",
    "/api/option-bot-control",
    "/api/option-paper-trades",
]


@pytest.mark.parametrize("route", OWNER_ONLY_ROUTES)
def test_trading_route_rejects_non_admin_sessions(route: str) -> None:
    handler = route_handler(API_SOURCE, route)
    assert "_require_admin_user" in handler, (
        f"{route} mutates the owner's trading state and must reject non-admin "
        "sessions before doing anything"
    )


def test_the_gate_comes_before_the_work() -> None:
    """A check after the side effect is not a check.

    Pins that for each route the admin gate appears before the first STATE.
    call, rather than merely somewhere in the handler.
    """
    for route in OWNER_ONLY_ROUTES:
        start = API_SOURCE.find(f'if parsed.path == "{route}":')
        handler = API_SOURCE[start : start + 900]
        gate_at = handler.find("_require_admin_user")
        state_at = handler.find("STATE.")
        assert gate_at != -1, f"{route} has no admin gate"
        if state_at != -1:
            assert gate_at < state_at, f"{route} touches STATE before checking who is asking"
