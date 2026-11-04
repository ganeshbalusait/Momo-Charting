"""Unit: owner-only guards that sit beside the trading routes.

1. The handler of every route that acts on the owner's Alpaca account asks for
   an admin before it does anything (source check, like
   tests/test_trading_routes_are_admin_only.py, for the two routes that list
   leaves out). The behavioural proof is tests/test_cov_trading_owner_gate_api.py.
2. No end-to-end spec may let an order, bot or settings route through to the
   live server: those must be answered with fakeWrites, never allowWrites.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from handler_source import route_handler

REPO = Path(__file__).resolve().parents[1]
API_SOURCE = (REPO / "api_server.py").read_text(encoding="utf-8")

MISSING_FROM_THE_OWNER_LIST = ["/api/manage-option-positions", "/api/cancel-stale-option-buy-orders"]

# Anything that places / closes / cancels orders, starts a bot, changes settings or logs in.
NEVER_ALLOWED_THROUGH = re.compile(
    r"/api/(close|execute|manage-option|cancel|option-paper-trades|bot-control|option-bot|risk-settings|"
    r"option-risk|account-select|schwab|settings|preflight/run|momx-scanner/(data-source|universe|momo-config)|"
    r"watchlist|admin|user/api-keys|auth)"
)


@pytest.mark.xfail(strict=True, reason=(
    "manage-option-positions and cancel-stale-option-buy-orders call STATE without _require_admin_user"))
@pytest.mark.parametrize("route", MISSING_FROM_THE_OWNER_LIST)
def test_alpaca_option_order_routes_ask_for_an_admin_before_acting(route):
    handler = route_handler(API_SOURCE, route)
    assert "STATE." in handler, f"could not find the {route} handler"
    assert "_require_admin_user" in handler, f"{route} acts on the owner's Alpaca orders with no admin check"
    assert handler.index("_require_admin_user") < handler.index("STATE.")


def allow_writes_lists(text: str):
    for match in re.finditer(r"allowWrites\s*:\s*\[(.*?)\]", text, re.S):
        yield re.findall(r"[\"'`]([A-Z]+ [^\"'`]+)[\"'`]", match.group(1))


def test_no_end_to_end_spec_lets_a_trading_or_settings_write_reach_the_live_server():
    offenders = []
    specs = sorted((REPO / "tests" / "e2e").glob("*.mjs"))
    assert specs, "no end-to-end specs found"
    for spec in specs:
        if spec.name == "harness.mjs":
            continue
        for entries in allow_writes_lists(spec.read_text(encoding="utf-8")):
            offenders += [f"{spec.name}: {entry}" for entry in entries if NEVER_ALLOWED_THROUGH.search(entry)]
    assert offenders == []


def test_the_lint_itself_catches_a_dangerous_allow_list():
    sample = 'withApp({ allowWrites: ["POST /api/close-position", "POST /api/news-feed"] }, fn)'
    entries = [e for lst in allow_writes_lists(sample) for e in lst]
    assert entries == ["POST /api/close-position", "POST /api/news-feed"]
    assert [e for e in entries if NEVER_ALLOWED_THROUGH.search(e)] == ["POST /api/close-position"]
