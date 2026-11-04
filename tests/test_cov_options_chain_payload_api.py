"""Integration: what each option screen reads out of the chain feed, through
the real /api/oi-finder-chain and /api/oi-finder routes and the real chain
builder, with Schwab faked (see test_cov_options_chain_feed_api.py for the
fakes and why background collectors are refused).

One test per screen: the chain column beside the chart, the High OI board,
the live ATM strip, the chain pop-out, Quick Options (phone first paint,
Heatmap, Flow), the OI Level Script page and the ROI calculator.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from api_harness import api_server, running_api
from test_cov_options_chain_feed_api import (  # noqa: F401 - chain_env is a fixture
    CALL_OI,
    PUT_OI,
    SPOT,
    FakeSchwab,
    chain_env,
    occ,
    schwab_chain,
)

ANALYTICS = ("volumeMomentum", "dailyLiquidityHeatmap", "unusualOtmActivity",
             "unusualOtmDashboard", "persistentActivity", "liveWallTrend")


def expiry_in(days: int) -> str:
    return (datetime.now().date() + timedelta(days=days)).isoformat()


def rows_for(body: dict, expiry: str, side: str) -> dict[float, dict]:
    return {row["strike"]: row for row in body["selectedExpiryChainRows"]
            if row["expiry"] == expiry and row["side"] == side}


# --------------------------------------------------------------------------- chain column (Charts & OI)

def test_first_paint_is_the_nearest_expiry_with_every_quote_field(chain_env):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=aapl&initial=true&force=true")
    assert status == 200, body
    front = expiry_in(2)
    assert body["frontExpiryOnly"] is True and body["expiries"] == [front]
    assert {row["expiry"] for row in body["selectedExpiryChainRows"]} == {front}
    assert list(body["expiryExpectedMoves"]) == [front]
    calls, puts = rows_for(body, front, "CALL"), rows_for(body, front, "PUT")
    assert sorted(calls) == sorted(puts) == [190.0, 195.0, 200.0, 205.0, 210.0]
    atm_call = calls[200.0]
    assert atm_call["symbol"] == occ("AAPL", front, "C", 200.0)     # the padded OCC key the stream leases
    assert (atm_call["bid"], atm_call["ask"], atm_call["mark"]) == (4.05, 4.15, 4.1)
    assert (atm_call["delta"], atm_call["gamma"]) == (0.5, 0.031)
    assert (atm_call["volume"], atm_call["open_interest"], atm_call["is_atm"]) == (1200, CALL_OI[200.0], True)
    assert puts[190.0]["delta"] == 0.1, "put deltas arrive as magnitudes"
    # The first paint asks Schwab for one week only, so the phone/desktop chain answers fast.
    assert chain_env.schwab[0].calls[0]["days"] == 7


def test_the_full_chain_spans_every_expiry_inside_31_days_with_tos_levels(chain_env):
    chain_env.schwab = [FakeSchwab(chain=lambda symbol: schwab_chain(symbol, dtes=(2, 9, 23, 40)))]
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    inside = [expiry_in(days) for days in (2, 9, 23)]
    assert sorted({row["expiry"] for row in body["selectedExpiryChainRows"]}) == inside
    assert [level["expiry"] for level in body["tosScriptLevels"]] == inside
    front_levels = body["tosScriptLevels"][0]
    assert front_levels["daysToExpiration"] == 2
    # Levels are the biggest OI per side for that exact expiry, largest first.
    assert [level["strike"] for level in front_levels["callLevels"]][:2] == [190.0, 210.0]
    assert [level["strike"] for level in front_levels["putLevels"]][:2] == [210.0, 190.0]
    assert set(front_levels["callLevels"][0]) >= {"strike", "openInterest", "volume", "delta"}
    assert chain_env.schwab[0].calls[0]["days"] == 31


def test_a_monthly_only_ticker_still_gets_a_first_paint(chain_env):
    """A name with no expiry in the first week answered 'no chain' until the
    first paint retried across 31 DTE."""
    monthly_only = FakeSchwab(chain=lambda symbol: schwab_chain(symbol, dtes=(20,)), empty_narrow=True)
    chain_env.schwab = [monthly_only]
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&initial=true&force=true")
    assert status == 200, body
    assert [call["days"] for call in monthly_only.calls] == [7, 31]
    assert body["live"] is True and body["expiries"] == [expiry_in(20)]
    assert body["tosScriptLevels"][0]["callLevels"], "the OI Level Script page has levels to draw"


# --------------------------------------------------------------------------- High OI board

def test_the_high_oi_board_gets_walls_on_the_right_side_of_spot_and_its_header_stats(chain_env):
    chain_env.schwab = [FakeSchwab(chain=lambda symbol: schwab_chain(symbol, volatility=32.5))]
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    # Stats row: IV, the day's move and one expected move per expiry ('to monthly' scope).
    assert body["impliedVolatility"] == 32.5
    assert (body["todayChange"], body["todayChangePercent"]) == (2.5, 1.27)
    assert sorted(body["expiryExpectedMoves"]) == [expiry_in(days) for days in (2, 9, 23)]
    assert body["expiryExpectedMoves"][expiry_in(2)] == 8.2           # ATM call + put mid
    front = expiry_in(2)
    calls, puts = rows_for(body, front, "CALL"), rows_for(body, front, "PUT")
    # Daily-sheet convention: a call wall only ABOVE spot, a put wall only at/below.
    assert {strike for strike, row in calls.items() if row["is_high_open_interest"]} == {205.0, 210.0}
    assert {strike for strike, row in puts.items() if row["is_high_open_interest"]} == {190.0, 195.0, 200.0}
    assert CALL_OI[190.0] > CALL_OI[210.0] and PUT_OI[210.0] > PUT_OI[190.0]   # the ITM traps were biggest


# --------------------------------------------------------------------------- live ATM strip

def test_the_current_atm_strip_reads_the_nearest_expiry(chain_env):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
    assert status == 200, body
    atm = body["currentAtm"]
    assert (atm["expiry"], atm["daysToExpiration"], atm["expectedMove"]) == (expiry_in(2), 2, 8.2)
    assert atm["call"] == {"strike": 200.0, "volume": 1200, "openInterest": CALL_OI[200.0]}
    assert atm["put"] == {"strike": 200.0, "volume": 1200, "openInterest": PUT_OI[200.0]}
    assert body["underlyingPrice"] == SPOT
    assert (body["todayChange"], body["todayChangePercent"]) == (2.5, 1.27)
    assert chain_env.schwab[0].quote_calls == [["AAPL"]]


# --------------------------------------------------------------------------- pop-out + ROI calc

def test_the_compact_feed_the_popout_reads_never_touches_the_snapshot_history(chain_env, monkeypatch):
    def history_read(*_args, **_kwargs):
        raise AssertionError("the compact chain must not read 183 days of snapshots")

    monkeypatch.setattr(api_server.STATE.repository, "option_chain_daily_snapshots", history_read)
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=SPY&force=true")
    assert status == 200, body
    assert body["live"] is True and body["symbol"] == "SPY"
    assert all(body[key] == {} for key in ANALYTICS), {key: bool(body[key]) for key in ANALYTICS}


def test_roi_calc_inputs_and_the_server_estimate_stub(chain_env):
    with running_api() as api:
        _, body = api.get("/api/oi-finder-chain?symbol=AAPL&force=true")
        status, estimate = api.post("/api/option-roi-estimate", {
            "model": "gamma-only", "side": "CALL", "spotPrice": 200, "strike": 205,
            "entryPrice": 1.6, "delta": 0.3, "gamma": 0.031, "targets": [{"stockTarget": "203"}],
        })
    rows = body["selectedExpiryChainRows"]
    assert rows and all(row["mark"] > 0 and row["delta"] > 0 and row["gamma"] > 0 for row in rows)
    # The calculator works from the local delta+gamma estimate when the server returns none.
    assert (status, estimate) == (200, {"results": []})


# --------------------------------------------------------------------------- Quick Options

def test_the_phone_first_paint_is_a_small_window_with_no_second_broker_call(chain_env):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chain?symbol=AAPL&fast=true&force=true")
    assert status == 200, body
    assert body["mobileFast"] is True
    assert len({row["strike"] for row in body["selectedExpiryChainRows"]}) <= 13
    assert (body["callRows"], body["putRows"], body["tosScriptLevels"]) == ([], [], [])
    assert chain_env.schwab[0].quote_calls == [], "the chain already carries the price"
    assert chain_env.schwab[0].calls[0]["strikeCount"] == 26


def test_quick_options_flow_section_builds_flow_without_the_desktop_history(chain_env, monkeypatch):
    def history_read(*_args, **_kwargs):
        raise AssertionError("the phone's Flow section must not read 183 days of snapshots")

    monkeypatch.setattr(api_server.STATE.repository, "option_chain_daily_snapshots", history_read)
    with running_api() as api:
        status, body = api.get("/api/oi-finder?symbol=AAPL&section=flow")
    assert status == 200, body
    assert body["researchSection"] == "flow" and body["live"] is True
    assert body["volumeMomentum"] and body["unusualOtmActivity"]
    assert (body["unusualOtmDashboard"], body["persistentActivity"], body["liveWallTrend"]) == ({}, {}, {})


def test_quick_options_heatmap_section_is_only_the_heatmap(chain_env):
    with running_api() as api:
        status, body = api.get("/api/oi-finder?symbol=AAPL&section=heatmap")
    assert status == 200, body
    assert body["researchSection"] == "heatmap"
    heatmap = body["dailyLiquidityHeatmap"]
    assert heatmap["liveOnly"] is True
    assert sorted(heatmap["expiries"]) == [expiry_in(days) for days in (2, 9, 23)]
    assert (body["volumeMomentum"], body["unusualOtmActivity"]) == ({}, {})
