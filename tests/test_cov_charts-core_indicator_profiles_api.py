"""Integration: "Save <tf> for all tickers" through /api/chart-indicator-profiles
over real HTTP (2026-11-04 coverage).

The profile a trader saves on the desktop must come back on his phone (same
account), must never leak to another account, and a bad or oversized save
must be refused with a readable reason. running_api() points
chart_profile_store.ROOT at a scratch folder, so the trader's real
artifacts/user_chart_profiles file is never touched (the write guard would
refuse it anyway).
"""
from __future__ import annotations

import pytest

import chart_profile_store
from api_harness import RELAYED, running_api, seed_user

ROUTE = "/api/chart-indicator-profiles"
OWNER = "owner@example.com"
SECOND = "second-trader@example.com"


def profile(saved_at: str = "2026-11-04T15:10:00Z", **settings) -> dict:
    return {
        "indicatorSettings": {"ema9": False, "signals48": True, **settings},
        "indicatorOptions": {"ema9Length": 13, "mtf48Timeframes": {"D": True, "W": False}},
        "savedAt": saved_at,
    }


@pytest.fixture(autouse=True)
def owner(monkeypatch):
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)


def as_second_trader() -> dict:
    """A different signed-in account arriving through the tunnel."""
    # role="user": the harness default ("member") is not a role auth_service accepts.
    seed_user(SECOND, role="user")
    return {**RELAYED, "X-AGX-Access-Email": SECOND}


def test_a_saved_profile_comes_back_for_the_same_account():
    with running_api() as api:
        status, body = api.post(ROUTE, {"key": "5m", "profile": profile()})
        assert (status, body) == (200, {"ok": True})
        status, body = api.get(ROUTE)
        # The file landed in the scratch folder, never the live artifacts/.
        stored = chart_profile_store._path(OWNER)
        assert stored is not None and stored.exists()
        assert "artifacts" not in stored.parts or "agx-api-" in str(stored)
    assert status == 200
    assert body["profiles"] == {"5m": profile()}


def test_saving_one_timeframe_keeps_the_others_and_a_newer_save_wins():
    with running_api() as api:
        api.post(ROUTE, {"key": "5m", "profile": profile()})
        api.post(ROUTE, {"key": "D", "profile": profile(woodiesPivots=True)})
        api.post(ROUTE, {"key": "5m", "profile": profile("2026-11-04T15:20:00Z", ema9=True)})
        status, body = api.get(ROUTE)
    assert status == 200
    assert set(body["profiles"]) == {"5m", "D"}
    assert body["profiles"]["5m"]["indicatorSettings"]["ema9"] is True
    assert body["profiles"]["5m"]["savedAt"] == "2026-11-04T15:20:00Z"
    assert body["profiles"]["D"]["indicatorSettings"]["woodiesPivots"] is True


@pytest.mark.parametrize(("payload", "reason"), [
    ({"key": "D/../x", "profile": profile()}, "bad profile key"),
    ({"key": "x" * 65, "profile": profile()}, "bad profile key"),
    ({"key": "D", "profile": {"indicatorSettings": {"ema9": True}}}, "profile needs indicatorSettings and indicatorOptions"),
    ({"key": "D", "profile": {**profile(), "indicatorOptions": {"blob": "x" * 300_000}}}, "profile too large"),
])
def test_a_bad_save_is_refused_with_the_reason_and_nothing_is_stored(payload, reason):
    with running_api() as api:
        status, body = api.post(ROUTE, payload)
        _, after = api.get(ROUTE)
    assert status == 400
    assert body == {"error": reason}
    assert after["profiles"] == {}


def test_a_tunnel_visitor_without_a_session_can_neither_read_nor_save():
    with running_api() as api:
        get_status, get_body = api.get(ROUTE, headers=RELAYED)
        post_status, post_body = api.post(ROUTE, {"key": "D", "profile": profile()}, headers=RELAYED)
        _, owner_view = api.get(ROUTE)
    assert (get_status, post_status) == (401, 401)
    assert get_body == post_body == {"error": "Sign in to continue."}
    assert owner_view["profiles"] == {}


def test_two_accounts_never_see_each_others_profiles():
    with running_api() as api:
        api.post(ROUTE, {"key": "D", "profile": profile()})
        second = as_second_trader()
        status, body = api.get(ROUTE, headers=second)
        assert (status, body) == (200, {"profiles": {}})
        status, _ = api.post(ROUTE, {"key": "D", "profile": profile(ema9=True)}, headers=second)
        assert status == 200
        _, owner_view = api.get(ROUTE)
        _, second_view = api.get(ROUTE, headers=second)
    assert owner_view["profiles"]["D"]["indicatorSettings"]["ema9"] is False
    assert second_view["profiles"]["D"]["indicatorSettings"]["ema9"] is True
