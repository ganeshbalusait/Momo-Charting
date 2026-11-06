"""LEAP to buy v2: ``pick_leap`` (pure, over a Schwab call chain) and ``LongTermService.leap_payload``.

Two picks on the SAME expiry: ``contract`` is the ENTRY-style pick (out-of-the-money call, delta from
0.10 up to but not including 0.20, highest open interest, like the chain page's ENTRY) and ``deep`` is the
Rule #1 stock substitute (in-the-money call near 0.75 delta, highest open interest inside 0.70..0.80).

No network. The chain fixtures are built here with fixed numbers; "today" is Friday 2026-11-06 (the
same frozen clock as tests/test_long_term_service.py), so every DTE is exact.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from long_term.leaps import (
    DEEP_DELTA_BAND, ENTRY_DELTA_HI, ENTRY_DELTA_LO, ENTRY_TARGET, FALLBACK_MONEYNESS, LEAP_MAX_DTE, LEAP_MIN_DTE,
    MAX_SPREAD, REASON_CHAIN_UNAVAILABLE, REASON_NO_CHAIN, REASON_NO_CONTRACT, REASON_NO_DELTA, REASON_NO_ENTRY,
    REASON_NO_EXPIRY, REASON_NO_PRICE, REASON_NOT_FLAGGED, TARGET_DELTA, THIN_OI, pick_leap,
)
from long_term.service import CHAIN_TTL_S
from test_long_term_service import NOW, NOW_MS, fixture_price, make_svc

TODAY = date(2026, 11, 6)
PRICE = 240.0
OMIT = object()                      # "leave the field out of the contract"
CONTRACT_KEYS = {
    "kind", "symbol", "expiry", "dte", "strike", "bid", "ask", "mid", "delta", "openInterest", "volume",
    "breakeven", "breakevenPct", "intrinsic", "extrinsic", "cost", "wideSpread", "thin",
}
PAYLOAD_KEYS = {"symbol", "name", "leaps", "price", "leapsBelow", "contract", "deep", "reason", "expiries",
                "fetchedAt", "refreshedAt"}
EMPTY = {"contract": None, "deep": None}


# ---------- fixture builders (Schwab /chains shape, see data/schwab_client.py::get_option_chain) ----------

def expiry_of(dte: int, today: date = TODAY) -> str:
    return (today + timedelta(days=dte)).isoformat()


def auto_delta(strike: float, spot: float = PRICE) -> float:
    """A deterministic call delta by moneyness for spot 240: 210 -> 0.75 (the deep pick), 220 -> 0.67,
    200 -> 0.83, 260 -> 0.33, 280 -> 0.17 (the ENTRY pick), 300 -> 0.05 (under the ENTRY band)."""
    return round(min(0.95, max(0.05, 0.5 + (spot - strike) / spot * 2.0)), 2)


def contract(dte: int, strike: float, *, bid=None, ask=None, delta=OMIT, oi=1234, volume=56,
             today: date = TODAY, symbol=None, put_call="CALL", expiry=None, spot: float = PRICE) -> dict:
    """One Schwab contract. ``bid``/``ask`` default to a tight live quote: intrinsic + $20 of time value
    (so an out-of-the-money call is quoted 19.75 / 20.25, never a negative bid)."""
    exp = expiry or expiry_of(dte, today)
    mid = max(spot - strike, 0.0) + 20.0
    c = {
        "symbol": symbol or f"AAPL  {exp.replace('-', '')[2:]}C{int(strike * 1000):08d}",
        "putCall": put_call,
        "strikePrice": strike,
        "totalVolume": volume,
        "openInterest": oi,
        "daysToExpiration": dte,
        "expirationDate": exp,
        "bid": round(mid - 0.25, 2) if bid is None else bid,
        "ask": round(mid + 0.25, 2) if ask is None else ask,
        "mark": mid,
        "last": mid,
    }
    c["delta"] = auto_delta(strike, spot) if delta is OMIT else delta
    if delta is None:
        c.pop("delta")
    return c


def make_chain(contracts: list[dict], *, spot: float = PRICE, today: date = TODAY) -> dict:
    calls: dict = {}
    for c in contracts:
        key = f"{c['expirationDate']}:{c['daysToExpiration']}"
        calls.setdefault(key, {}).setdefault(f"{float(c['strikePrice']):.1f}", []).append(c)
    return {"symbol": "AAPL", "status": "SUCCESS", "underlyingPrice": spot, "callExpDateMap": calls,
            "putExpDateMap": {}}


STRIKES = (160.0, 180.0, 200.0, 210.0, 220.0, 240.0, 260.0, 280.0, 300.0)   # for spot 240
ITM_STRIKES = tuple(s for s in STRIKES if s < PRICE)
FRACTIONS = tuple(s / PRICE for s in STRIKES)                                # the same ladder for any spot
ENTRY_FRACTION = 280.0 / PRICE                                               # delta 0.17 on the ladder
DEEP_FRACTION = 210.0 / PRICE                                                # delta 0.75 on the ladder


def std_chain(dtes=(30, 250, 340, 800),*, spot: float = PRICE, deltas: bool = True, today: date = TODAY) -> dict:
    """Every strike at every DTE, live two-sided quotes, deltas by moneyness (or none at all), equal OI.
    Strikes are the 240-spot ladder scaled to ``spot`` (2/3 .. 5/4 of the price). With deltas the
    ENTRY pick is the 7/6 strike (delta 0.17) and the deep pick the 7/8 strike (delta 0.75)."""
    strikes = [round(spot * f, 1) for f in FRACTIONS]
    return make_chain([contract(d, s, delta=OMIT if deltas else None, today=today, spot=spot)
                       for d in dtes for s in strikes], spot=spot, today=today)


def entry_of(out: dict) -> dict:
    assert out["contract"] is not None, out
    return out["contract"]


def deep_of(out: dict) -> dict:
    assert out["deep"] is not None, out
    return out["deep"]


class FakeChain:
    """Stands in for LongTermService.chain_fn: records (schwab symbol, from_date, to_date) per call."""

    def __init__(self, chain=None, *, error: str | None = None):
        self.chain = chain
        self.error = error
        self.calls: list[tuple] = []

    def __call__(self, symbol, from_date, to_date):
        self.calls.append((symbol, from_date, to_date))
        if self.error:
            raise RuntimeError(self.error)
        return self.chain


# ---------- pick_leap: expiry window ----------

def test_constants_are_the_brief_values():
    # window 90..365 days: Ganesh 11-06 15:25, "Leap I will take 3 months to 12 months"
    assert (LEAP_MIN_DTE, LEAP_MAX_DTE, TARGET_DELTA, FALLBACK_MONEYNESS, MAX_SPREAD) == (90, 365, 0.75, 0.80, 0.25)
    assert (ENTRY_DELTA_LO, ENTRY_DELTA_HI, ENTRY_TARGET, DEEP_DELTA_BAND, THIN_OI) == (0.10, 0.20, 0.20, 0.05, 50)


def test_farthest_expiry_in_window_wins_for_both_picks():
    out = pick_leap(std_chain((30, 250, 340, 800)), price=PRICE, today=TODAY)
    c, d = entry_of(out), deep_of(out)
    assert out["reason"] == ""
    assert c["expiry"] == expiry_of(340) and c["dte"] == 340
    assert d["expiry"] == expiry_of(340) and d["dte"] == 340
    assert out["expiries"] == [expiry_of(250), expiry_of(340)]            # eligible only, nearest first


def test_window_edges_are_inclusive():
    """3 months (90 days) and 12 months (365 days) are both IN; 89 and 366 are out."""
    assert entry_of(pick_leap(std_chain((90,)), price=PRICE, today=TODAY))["dte"] == 90
    assert entry_of(pick_leap(std_chain((365,)), price=PRICE, today=TODAY))["dte"] == 365
    assert {k: pick_leap(std_chain((89,)), price=PRICE, today=TODAY)[k] for k in EMPTY} == EMPTY
    assert {k: pick_leap(std_chain((366,)), price=PRICE, today=TODAY)[k] for k in EMPTY} == EMPTY
    # the old 12-25 month window is gone: a 2-year expiry alone is "no expiry" now
    assert pick_leap(std_chain((730,)), price=PRICE, today=TODAY)["reason"] == REASON_NO_EXPIRY


def test_nothing_in_window_is_a_reason():
    out = pick_leap(std_chain((30, 500, 900)), price=PRICE, today=TODAY)       # too near, too far, too far
    assert out == {"contract": None, "deep": None, "reason": REASON_NO_EXPIRY, "expiries": []}


def test_dte_from_today_when_given_else_from_the_contract():
    # key date 900 days out but the contract says 250 days: without `today` the contract's number counts
    ch = make_chain([contract(250, 280.0, expiry=(TODAY + timedelta(days=900)).isoformat())])
    assert entry_of(pick_leap(ch, price=PRICE))["dte"] == 250
    assert pick_leap(ch, price=PRICE, today=TODAY)["contract"] is None            # 900 days from today


def test_farther_expiry_without_a_usable_contract_falls_back_to_the_nearer_one():
    far = [contract(360,s, bid=0.0, ask=1.0) for s in STRIKES]                   # no live bids out there
    near = [contract(250,s) for s in STRIKES]
    out = pick_leap(make_chain(far + near), price=PRICE, today=TODAY)
    assert entry_of(out)["dte"] == 250 and deep_of(out)["dte"] == 250
    assert out["expiries"] == [expiry_of(250), expiry_of(360)]


def test_farther_expiry_with_only_a_deep_pick_loses_to_a_nearer_one_with_an_entry_pick():
    """Both picks share one expiry, chosen for the ENTRY pick: the deep call moves with it."""
    far = [contract(360,s) for s in ITM_STRIKES]                                  # nothing out of the money
    near = [contract(250,s) for s in STRIKES]
    out = pick_leap(make_chain(far + near), price=PRICE, today=TODAY)
    assert entry_of(out)["dte"] == 250 and deep_of(out)["dte"] == 250
    assert out["reason"] == ""


def test_no_entry_anywhere_gives_the_farthest_deep_pick_and_the_entry_reason():
    far = [contract(360,s) for s in ITM_STRIKES]
    near = [contract(250,s) for s in ITM_STRIKES]
    out = pick_leap(make_chain(far + near), price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_ENTRY
    assert deep_of(out)["dte"] == 360 and out["deep"]["kind"] == "deep"
    assert out["expiries"] == [expiry_of(250), expiry_of(360)]


# ---------- pick_leap: the ENTRY pick (``contract``) ----------

def test_entry_is_the_otm_call_with_the_highest_open_interest_from_010_to_under_020_delta():
    """Adobe on 11-06 (the Jan-2028 numbers, re-dated 330 days out): ENTRY = $450 C (delta 0.16, OI 1,687). Strikes at 0.24, 0.22 and 0.20
    delta with more OI, and a 0.09 strike with even more, are all outside the band and never chosen."""
    adbe = 360.0
    ch = make_chain([contract(330, 420.0, delta=0.24, oi=9000, spot=adbe),         # over the band, most OI
                     contract(330, 440.0, delta=0.22, oi=2000, spot=adbe),         # the v2 brief's top edge: out
                     contract(330, 445.0, delta=0.20, oi=1800, spot=adbe),         # 0.20 is not "less than 0.2"
                     contract(330, 450.0, delta=0.16, oi=1687, spot=adbe),         # the ENTRY
                     contract(330, 470.0, delta=0.12, oi=900, spot=adbe),
                     contract(330, 500.0, delta=0.09, oi=25000, spot=adbe),        # under the band
                     contract(330, 200.0, delta=0.75, oi=20672, spot=adbe)], spot=adbe)   # deep, not ENTRY
    out = pick_leap(ch, price=adbe, today=TODAY)
    c = entry_of(out)
    assert (c["strike"], c["delta"], c["openInterest"], c["kind"]) == (450.0, 0.16, 1687, "entry")
    assert deep_of(out)["strike"] == 200.0 and out["reason"] == ""


def test_entry_band_is_010_inclusive_to_020_exclusive():
    ch = make_chain([contract(250, 280.0, delta=0.199, oi=100), contract(250, 300.0, delta=0.10, oi=500)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 300.0        # 0.10 counts, more OI
    ch = make_chain([contract(250, 280.0, delta=0.199, oi=100), contract(250, 300.0, delta=0.099, oi=500)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 280.0        # 0.099 out, 0.199 in
    ch = make_chain([contract(250, 270.0, delta=0.20, oi=9000), contract(250, 280.0, delta=0.15, oi=50)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 280.0        # 0.20 itself is out
    ch = make_chain([contract(250, 270.0, delta=0.22, oi=9000), contract(250, 280.0, delta=0.15, oi=50)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 280.0        # 0.22 is out
    out = pick_leap(make_chain([contract(250, 270.0, delta=0.20, oi=9000)]), price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_ENTRY             # 0.20 alone: no ENTRY


def test_entry_band_top_is_the_chain_page_entry_rule():
    """Review 11-06 (finding 1): the drawer promises a pick "like the chain page's ENTRY", and that page
    (momx/option_track.py::pick_entry_strike, App.jsx::buildOptionRoiEstimates) takes 0 < delta < 0.20
    strictly - Ganesh: "high oi less than 0.2 delta". The v2 brief's 0.22 top edge let a $440 C at
    delta 0.22 with more open interest beat the $450 C at 0.16; it never may."""
    from momx.option_track import ENTRY_DELTA
    assert ENTRY_DELTA_HI == ENTRY_DELTA == 0.20
    ch = make_chain([contract(330, 440.0, delta=0.22, oi=2000), contract(330, 450.0, delta=0.16, oi=1687)])
    c = entry_of(pick_leap(ch, price=PRICE, today=TODAY))
    assert (c["strike"], c["delta"], c["openInterest"]) == (450.0, 0.16, 1687)


def test_entry_open_interest_tie_goes_to_the_delta_closest_to_020_then_the_lower_strike():
    ch = make_chain([contract(250, 270.0, delta=0.19, oi=800), contract(250, 280.0, delta=0.17, oi=800),
                     contract(250, 290.0, delta=0.12, oi=800)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 270.0        # 0.19 is 0.01 from 0.20
    ch = make_chain([contract(250, 280.0, delta=0.18, oi=800), contract(250, 270.0, delta=0.18, oi=800),
                     contract(250, 290.0, delta=0.14, oi=800)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 270.0        # equal delta: lower strike


def test_entry_is_out_of_the_money_only():
    """An in-the-money or at-the-money call with a 0.18 delta (nonsense from a stale greek) is never ENTRY."""
    ch = make_chain([contract(250, 230.0, delta=0.18, oi=99999), contract(250, 240.0, delta=0.18, oi=99999),
                     contract(250, 260.0, delta=0.18, oi=10)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 260.0


def test_entry_missing_open_interest_counts_as_zero():
    ch = make_chain([contract(250, 280.0, delta=0.19, oi=None), contract(250, 290.0, delta=0.14, oi=1)])
    assert entry_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 290.0
    only = make_chain([contract(250, 280.0, delta=0.19, oi=None)])
    c = entry_of(pick_leap(only, price=PRICE, today=TODAY))
    assert c["strike"] == 280.0 and c["openInterest"] is None and c["thin"] is True


def test_no_delta_on_the_expiry_means_no_entry_pick_but_a_fallback_deep_pick():
    """Never guess an out-of-the-money strike without a delta; the deep pick still uses 0.80 x price.
    The reason names the missing deltas (a feed state), not a missing call."""
    out = pick_leap(std_chain((250,), deltas=False), price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_DELTA
    d = deep_of(out)
    assert d["strike"] == 200.0 and d["delta"] is None and d["kind"] == "deep"      # 0.80 x 240 = 192 -> 200
    assert out["expiries"] == [expiry_of(250)]


def test_only_itm_quotes_means_no_entry_pick_but_a_deep_pick():
    out = pick_leap(make_chain([contract(250,s) for s in ITM_STRIKES]), price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_ENTRY
    assert deep_of(out)["strike"] == 210.0


def test_entry_math_for_an_out_of_the_money_call():
    """Adobe: $450 C, ask 8.85 at spot 360 -> intrinsic 0, time value = ask, breakeven 458.85 (+27%)."""
    ch = make_chain([contract(330, 450.0, bid=8.55, ask=8.85, delta=0.16, oi=1687, volume=12,
                              symbol="ADBE  271002C00450000")], spot=360.0)
    c = entry_of(pick_leap(ch, price=360.0, today=TODAY))
    assert set(c) == CONTRACT_KEYS and c["kind"] == "entry"
    assert c["symbol"] == "ADBE  271002C00450000" and c["expiry"] == expiry_of(330) and c["dte"] == 330
    assert (c["strike"], c["bid"], c["ask"], c["delta"]) == (450.0, 8.55, 8.85, 0.16)
    assert c["mid"] == pytest.approx(8.70)
    assert c["breakeven"] == pytest.approx(458.85)
    assert c["breakevenPct"] == pytest.approx(458.85 / 360.0 - 1)
    assert c["intrinsic"] == 0.0 and c["extrinsic"] == pytest.approx(8.85)
    assert c["cost"] == pytest.approx(885.0)
    assert (c["openInterest"], c["volume"], c["wideSpread"], c["thin"]) == (1687, 12, False, False)
    assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in c.values())


# ---------- pick_leap: the deep pick (``deep``) ----------

def test_deep_is_the_highest_open_interest_inside_070_to_080_delta():
    """Adobe: 0.77 / OI 55 loses to 0.75 / OI 20,672 even though 0.77 is also in the band."""
    adbe = 360.0
    ch = make_chain([contract(330, 190.0, delta=0.77, oi=55, spot=adbe),
                     contract(330, 200.0, delta=0.75, oi=20672, spot=adbe),
                     contract(330, 450.0, delta=0.16, oi=1687, spot=adbe)], spot=adbe)
    d = deep_of(pick_leap(ch, price=adbe, today=TODAY))
    assert (d["strike"], d["delta"], d["openInterest"], d["kind"]) == (200.0, 0.75, 20672, "deep")
    # open interest decides inside the band, not closeness to 0.75: a 0.71 with more OI beats the 0.75
    ch = make_chain([contract(330, 190.0, delta=0.77, oi=55, spot=adbe),
                     contract(330, 200.0, delta=0.75, oi=20672, spot=adbe),
                     contract(330, 220.0, delta=0.71, oi=30000, spot=adbe),
                     contract(330, 450.0, delta=0.16, oi=1687, spot=adbe)], spot=adbe)
    d = deep_of(pick_leap(ch, price=adbe, today=TODAY))
    assert (d["strike"], d["delta"], d["openInterest"]) == (220.0, 0.71, 30000)


def test_deep_band_edges_are_inclusive_and_more_oi_outside_the_band_loses():
    ch = make_chain([contract(250, 200.0, delta=0.80, oi=100), contract(250, 180.0, delta=0.85, oi=90000),
                     contract(250, 220.0, delta=0.70, oi=500), contract(250, 230.0, delta=0.69, oi=90000)])
    assert deep_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 220.0       # 0.70 in, most OI in band
    ch = make_chain([contract(250, 200.0, delta=0.80, oi=100), contract(250, 180.0, delta=0.85, oi=90000)])
    assert deep_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 200.0       # 0.80 in


def test_deep_band_upper_edge_080_is_inside_so_open_interest_decides():
    """Review 11-06: pin the band's top edge. $200 C at delta 0.80 / OI 100 vs $210 C at 0.76 / OI 10,
    same expiry, price 240: 0.80 is INSIDE 0.70..0.80, so open interest decides and the $200 C wins.
    Were 0.80 excluded (a float-edge slip), the band would hold only the $210 C and it would win."""
    ch = make_chain([contract(250, 200.0, delta=0.80, oi=100), contract(250, 210.0, delta=0.76, oi=10)])
    d = deep_of(pick_leap(ch, price=240.0, today=TODAY))
    assert (d["strike"], d["delta"], d["openInterest"], d["kind"]) == (200.0, 0.80, 100, "deep")
    # the same two strikes the other way round: OI still decides, so the $210 C wins when IT has the OI
    ch = make_chain([contract(250, 200.0, delta=0.80, oi=10), contract(250, 210.0, delta=0.76, oi=100)])
    assert deep_of(pick_leap(ch, price=240.0, today=TODAY))["strike"] == 210.0


def test_deep_open_interest_tie_goes_to_the_delta_closest_to_075_then_the_lower_strike():
    ch = make_chain([contract(250, 200.0, delta=0.80, oi=700), contract(250, 210.0, delta=0.76, oi=700),
                     contract(250, 220.0, delta=0.70, oi=700)])
    assert deep_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 210.0
    ch = make_chain([contract(250, 200.0, delta=0.74, oi=700), contract(250, 210.0, delta=0.74, oi=700)])
    assert deep_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 200.0


def test_deep_falls_back_to_the_delta_closest_to_075_when_none_lands_in_the_band():
    ch = make_chain([contract(250, 160.0, delta=0.92, oi=50000), contract(250, 200.0, delta=0.84, oi=10),
                     contract(250, 230.0, delta=0.55, oi=50000)])
    d = deep_of(pick_leap(ch, price=PRICE, today=TODAY))
    assert d["strike"] == 200.0 and d["delta"] == 0.84                                # closest, OI ignored


def test_deep_fallback_tie_goes_to_the_lower_strike():
    ch = make_chain([contract(250, 200.0, delta=0.83), contract(250, 220.0, delta=0.67),
                     contract(250, 180.0, delta=0.90)])
    d = deep_of(pick_leap(ch, price=PRICE, today=TODAY))
    assert d["strike"] == 200.0 and d["delta"] == 0.83


def test_deep_math_on_fixed_numbers():
    ch = make_chain([contract(300, 200.0, bid=61.90, ask=62.40, delta=0.75, oi=1234, volume=56,
                              symbol="AAPL  270902C00200000")])
    out = pick_leap(ch, price=240.0, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_ENTRY
    c = deep_of(out)
    assert set(c) == CONTRACT_KEYS and c["kind"] == "deep"
    assert c["symbol"] == "AAPL  270902C00200000" and c["expiry"] == expiry_of(300) and c["dte"] == 300
    assert (c["strike"], c["bid"], c["ask"], c["delta"]) == (200.0, 61.90, 62.40, 0.75)
    assert c["mid"] == pytest.approx(62.15)
    assert c["breakeven"] == pytest.approx(262.40)
    assert c["breakevenPct"] == pytest.approx(262.40 / 240.0 - 1)
    assert c["intrinsic"] == pytest.approx(40.0)
    assert c["extrinsic"] == pytest.approx(22.40)
    assert c["cost"] == pytest.approx(6240.0)
    assert (c["openInterest"], c["volume"], c["wideSpread"], c["thin"]) == (1234, 56, False, False)
    assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in c.values())


def test_thin_market_flag_warns_but_never_hides_on_both_picks():
    """QCOM's real June-2028 $125 call had open interest 1 on 2026-11-06: still a pick, with a warning."""
    for oi, thin in ((1, True), (49, True), (50, False), (1234, False), (None, True)):
        ch = make_chain([contract(250, 200.0, delta=0.75, oi=oi), contract(250, 280.0, delta=0.17, oi=oi)])
        out = pick_leap(ch, price=PRICE, today=TODAY)
        for pick in (entry_of(out), deep_of(out)):
            assert pick["thin"] is thin and pick["openInterest"] == oi, (oi, pick)


def test_wide_spread_flag_on_both_picks():
    wide = make_chain([contract(250, 200.0, bid=40.0, ask=60.0, delta=0.75),          # 20 / 50 = 0.40
                       contract(250, 280.0, bid=3.0, ask=5.0, delta=0.17)])           # 2 / 4 = 0.50
    tight = make_chain([contract(250, 200.0, bid=55.0, ask=60.0, delta=0.75),         # 5 / 57.5 = 0.087
                        contract(250, 280.0, bid=4.8, ask=5.0, delta=0.17)])          # 0.2 / 4.9 = 0.04
    out = pick_leap(wide, price=PRICE, today=TODAY)
    assert entry_of(out)["wideSpread"] is True and deep_of(out)["wideSpread"] is True
    out = pick_leap(tight, price=PRICE, today=TODAY)
    assert entry_of(out)["wideSpread"] is False and deep_of(out)["wideSpread"] is False


def test_zero_bid_and_crossed_quotes_are_excluded_from_both_picks():
    ch = make_chain([contract(250, 210.0, bid=0.0, ask=50.0, delta=0.75),           # the best delta, no bid
                     contract(250, 200.0, bid=60.0, ask=0.0, delta=0.76),            # no ask
                     contract(250, 190.0, bid=70.0, ask=69.0, delta=0.78),           # ask < bid
                     contract(250, 180.0, bid=80.0, ask=80.5, delta=0.85),
                     contract(250, 280.0, bid=0.0, ask=5.0, delta=0.19, oi=9000),    # most OI, no bid
                     contract(250, 290.0, bid=4.0, ask=3.0, delta=0.15, oi=9000),    # crossed
                     contract(250, 300.0, bid=2.0, ask=2.2, delta=0.11, oi=5)])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert entry_of(out)["strike"] == 300.0 and deep_of(out)["strike"] == 180.0


def test_at_the_money_is_neither_pick():
    ch = make_chain([contract(250, 240.0, delta=0.75, oi=99999), contract(250, 240.0, delta=0.18, oi=99999),
                     contract(250, 230.0, delta=0.55), contract(250, 280.0, delta=0.17)])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert entry_of(out)["strike"] == 280.0 and deep_of(out)["strike"] == 230.0


@pytest.mark.parametrize("sentinel", [-999.0, 999.0, 0, 0.0, 1.5, -0.4, "-999", -999],
                         ids=["minus999", "plus999", "zero-int", "zero", "over-one", "negative", "str", "int"])
def test_schwab_sentinel_delta_is_no_delta_so_the_fallback_decides(sentinel):
    """Schwab stamps +/-999 or 0 on every greek after 16:00 ET. No delta -> no ENTRY pick (never guess an
    OTM strike), the reason says the feed hid the deltas, and the deep pick comes from the 0.80 x price
    fallback, not the lowest strike."""
    strikes = (100.0, 120.0, 140.0, 160.0, 180.0, 200.0, 210.0, 220.0, 260.0, 280.0, 300.0)
    ch = make_chain([contract(250,s, delta=sentinel) for s in strikes])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_DELTA
    d = deep_of(out)
    assert d["strike"] == 200.0 and d["delta"] is None          # 0.80 x 240 = 192 -> 200, not 100
    assert d["cost"] == pytest.approx(6025.0)                     # not 16,025


def test_sentinel_delta_never_beats_a_real_delta():
    ch = make_chain([contract(250, 100.0, delta=-999.0), contract(250, 120.0, delta=0.0),
                     contract(250, 200.0, delta=0.80), contract(250, 220.0, delta=0.70, oi=5000),
                     contract(250, 260.0, delta=999.0, oi=99999), contract(250, 280.0, delta=0.17, oi=10)])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert deep_of(out)["strike"] == 220.0 and entry_of(out)["strike"] == 280.0


def test_hidden_deltas_are_a_feed_state_not_a_verdict():
    """Review 11-06 (finding 2): from 16:00 ET to the open Schwab stamps every greek +/-999, so an
    evening read answered "no out-of-the-money call near 0.20 delta" - a verdict the page held, with no
    retry, until the next reload, on a day the ENTRY contract existed. Live quotes with no usable delta
    on ANY of them -> REASON_NO_DELTA (the page retries it); the same chain with deltas -> the ENTRY
    pick; deltas present but none inside the band -> REASON_NO_ENTRY, a real verdict."""
    hidden = pick_leap(std_chain((250, 340), deltas=False), price=PRICE, today=TODAY)
    assert hidden["contract"] is None and hidden["reason"] == REASON_NO_DELTA
    assert deep_of(hidden)["dte"] == 340 and deep_of(hidden)["delta"] is None and deep_of(hidden)["strike"] == 200.0
    sentinel = make_chain([contract(340,s, delta=999.0) for s in STRIKES] + [contract(250,s, delta=0) for s in STRIKES])
    assert pick_leap(sentinel, price=PRICE, today=TODAY)["reason"] == REASON_NO_DELTA
    live = pick_leap(std_chain((250, 340)), price=PRICE, today=TODAY)
    assert live["reason"] == "" and entry_of(live)["delta"] == 0.17 and entry_of(live)["dte"] == 340
    verdict = make_chain([contract(340, 200.0, delta=0.83), contract(340, 260.0, delta=0.33),
                          contract(340, 300.0, delta=0.05)])
    out = pick_leap(verdict, price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_ENTRY and deep_of(out)["strike"] == 200.0
    # one real delta anywhere in the window means the feed is live: no ENTRY is then a verdict
    one = make_chain([contract(340,s, delta=999.0) for s in STRIKES] + [contract(250, 200.0, delta=0.83)])
    assert pick_leap(one, price=PRICE, today=TODAY)["reason"] == REASON_NO_ENTRY
    # no live quote at all is not "hidden deltas" either
    dead = make_chain([contract(340,s, bid=0.0, ask=0.0, delta=999.0) for s in STRIKES])
    assert pick_leap(dead, price=PRICE, today=TODAY)["reason"] == REASON_NO_ENTRY


def test_delta_edges_zero_excluded_one_included():
    assert deep_of(pick_leap(make_chain([contract(250, 200.0, delta=1.0)]), price=PRICE, today=TODAY))["delta"] == 1.0
    assert deep_of(pick_leap(make_chain([contract(250, 200.0, delta=0.001)]), price=PRICE, today=TODAY))["delta"] == 0.001
    assert deep_of(pick_leap(make_chain([contract(250, 200.0, delta=1.0001)]), price=PRICE, today=TODAY))["delta"] is None


def test_a_single_delta_in_the_expiry_is_used_not_the_fallback():
    ch = make_chain([contract(250, 160.0, delta=0.9), contract(250, 200.0, delta=None)])
    assert deep_of(pick_leap(ch, price=PRICE, today=TODAY))["strike"] == 160.0


def test_puts_in_the_call_map_are_ignored():
    ch = make_chain([contract(250, 200.0, delta=-0.25, put_call="PUT"), contract(250, 180.0, delta=0.85),
                     contract(250, 280.0, delta=0.17, put_call="PUT")])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert deep_of(out)["strike"] == 180.0 and out["contract"] is None


def test_no_live_quote_anywhere_is_a_reason_with_neither_pick():
    ch = make_chain([contract(250,s, bid=0.0, ask=0.0) for s in STRIKES])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert out["contract"] is None and out["deep"] is None and out["reason"] == REASON_NO_ENTRY
    assert out["expiries"] == [expiry_of(250)]


@pytest.mark.parametrize("price", [None, 0, -5, float("nan"), float("inf"), "abc", True])
def test_no_price_is_a_reason(price):
    out = pick_leap(std_chain((250,)), price=price, today=TODAY)
    assert out["contract"] is None and out["deep"] is None and out["reason"] == REASON_NO_PRICE


@pytest.mark.parametrize("chain", [
    None, {}, [], "junk", 42,
    {"callExpDateMap": None}, {"callExpDateMap": "junk"}, {"callExpDateMap": []},
    {"putExpDateMap": {"2028-01-21:441": {}}},
], ids=["none", "empty", "list", "str", "int", "map-none", "map-str", "map-list", "puts-only"])
def test_no_chain_never_raises(chain):
    out = pick_leap(chain, price=PRICE, today=TODAY)
    assert out == {"contract": None, "deep": None, "reason": REASON_NO_CHAIN, "expiries": []}


def test_an_empty_call_map_is_a_verdict_not_an_outage():
    """Schwab answered the windowed request fine; the company lists nothing 3-12 months out.
    "no chain" is reserved for a missing/unreadable map (the page retries "no chain" on its own)."""
    out = pick_leap({"status": "SUCCESS", "callExpDateMap": {}, "putExpDateMap": {}}, price=PRICE, today=TODAY)
    assert out == {"contract": None, "deep": None, "reason": REASON_NO_EXPIRY, "expiries": []}


def test_an_ask_under_intrinsic_is_a_stale_print_not_a_live_quote():
    """After the close the option quotes are the 16:00 prints while the row price is the extended-hours
    last: an ask below (price - strike) cannot be filled, so it never becomes the deep call."""
    stale = contract(340, 200.0, bid=30.0, ask=35.0)          # intrinsic 40 at spot 240 -> ask 35 is stale
    live = contract(340, 180.0, bid=68.0, ask=69.0, delta=0.70)
    out = pick_leap(make_chain([stale, live]), price=PRICE, today=TODAY)
    assert deep_of(out)["strike"] == 180.0 and out["deep"]["extrinsic"] >= 0
    alone = pick_leap(make_chain([stale]), price=PRICE, today=TODAY)
    assert alone["contract"] is None and alone["deep"] is None and alone["reason"] == REASON_NO_ENTRY


@pytest.mark.parametrize("calls", [
    {"bad-key": {"200.0": [contract(250, 200.0, expiry="2028")]}},
    {"2027-03-17:131": "nope"},
    {"2027-03-17:131": {"200.0": "nope"}},
    {"2027-03-17:131": {"200.0": [None, 7, "x", []]}},
    {"2027-03-17:131": {"200.0": [{"bid": "x", "ask": None, "strikePrice": "wide"}]}},
    {"2027-03-17:131": {"abc": [{"bid": 1, "ask": 2}]}},
    {"junk": {"200.0": [contract(250, 200.0, expiry="not-a-date")]}},
    {"2027-03-17:131": {"280.0": [{"bid": 1, "ask": 2, "strikePrice": 280.0, "delta": "x", "openInterest": "y"}]}},
], ids=["bad-key", "strikes-str", "list-str", "junk-items", "junk-fields", "no-strike", "no-dates", "junk-oi"])
def test_garbage_contracts_never_raise(calls):
    out = pick_leap({"callExpDateMap": calls}, price=PRICE, today=TODAY)
    assert out["contract"] is None
    assert out["reason"] in {REASON_NO_EXPIRY, REASON_NO_ENTRY, REASON_NO_DELTA, REASON_NO_CHAIN}
    assert isinstance(out["expiries"], list) and "deep" in out


def test_strike_comes_from_the_map_key_when_the_contract_has_none():
    c = contract(250, 280.0, delta=0.17)
    c.pop("strikePrice")
    out = pick_leap({"callExpDateMap": {f"{expiry_of(250)}:250": {"280.0": [c]}}}, price=PRICE, today=TODAY)
    assert entry_of(out)["strike"] == 280.0


def test_expiry_from_expiration_date_when_the_key_has_none():
    c = contract(250, 280.0, delta=0.17)
    out = pick_leap({"callExpDateMap": {"junk": {"280.0": [c]}}}, price=PRICE, today=TODAY)
    assert entry_of(out)["expiry"] == expiry_of(250) and out["contract"]["dte"] == 250


def test_reason_strings_are_exact():
    assert REASON_NO_CHAIN == "no chain"
    assert REASON_NO_PRICE == "no price"
    assert REASON_NO_EXPIRY == "no expiry 3-12 months out"
    assert REASON_NO_CONTRACT == "no in-the-money call with a live quote"
    assert REASON_NO_ENTRY == "no out-of-the-money call near 0.20 delta with a live quote"
    assert REASON_NO_DELTA == "no deltas from the chain feed (hidden outside market hours)"
    assert REASON_CHAIN_UNAVAILABLE == "chain unavailable"
    assert REASON_NOT_FLAGGED == "LEAPS not flagged"


# ---------- service: leap_payload ----------

def _flag_aapl(svc) -> dict:
    """No fixture trades near its Safety Buy, so AAPL's stored copy is moved there (same move as
    test_long_term_service::test_low_price_gives_buy_for_a_good_business). Returns the live quote."""
    base = next(r for r in svc.page_payload(50, {})["rows"] if r["symbol"] == "AAPL")
    price = base["safetyBuy"] * 0.99
    with svc.store.lock:
        rec = svc.store.records["AAPL"]
        rec["ref_price"] = price
        rec["finnhub"]["pe_ttm"] = price / rec["finnhub"]["eps_ttm"]
        svc._computed.pop("AAPL")
    return {"last_price": price, "quote_time_ms": NOW_MS}


@pytest.fixture
def flagged(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    quote = _flag_aapl(svc)
    row = next(r for r in svc.page_payload(50, {"AAPL": quote})["rows"] if r["symbol"] == "AAPL")
    assert row["leaps"] is True, row
    yield svc, quote
    svc.stop_refresher()


def test_flagged_row_returns_both_picks_and_asks_for_the_schwab_window(flagged):
    svc, quote = flagged
    fake = svc._chain_fn = FakeChain(std_chain((30, 250, 340, 800), spot=quote["last_price"]))
    out = svc.leap_payload("aapl", 50, quote)
    assert set(out) == PAYLOAD_KEYS
    assert out["symbol"] == "AAPL" and out["name"] and out["leaps"] is True
    assert out["price"] == pytest.approx(quote["last_price"]) and out["leapsBelow"] >= out["price"]
    assert out["reason"] == "" and out["expiries"] == [expiry_of(250), expiry_of(340)]
    c, d = out["contract"], out["deep"]
    assert c["kind"] == "entry" and c["dte"] == 340 and c["strike"] > out["price"] and c["delta"] == 0.17
    assert d["kind"] == "deep" and d["dte"] == 340 and d["strike"] < out["price"] and d["delta"] == 0.75
    assert c["expiry"] == d["expiry"]
    assert out["fetchedAt"] and out["refreshedAt"]
    assert fake.calls == [("AAPL", NOW.date() + timedelta(days=90), NOW.date() + timedelta(days=365))]


def test_picks_are_made_against_the_row_price(flagged):
    svc, quote = flagged
    price = quote["last_price"]
    strikes = [round(price * f, 1) for f in (0.6, 0.8, 0.9, 1.1, 1.3)]
    # live quotes: intrinsic plus $1.00/$1.50 of time value (an ask under intrinsic is a stale print)
    ch = make_chain([contract(250,s, bid=round(max(price - s, 0) + 1.0, 2), ask=round(max(price - s, 0) + 1.5, 2),
                              delta=None) for s in strikes])
    svc._chain_fn = FakeChain(ch)
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["contract"] is None and out["reason"] == REASON_NO_DELTA               # no delta -> no ENTRY
    d = out["deep"]
    assert d["strike"] == strikes[1]                                   # 0.80 x price fallback, ITM only
    assert d["breakeven"] == pytest.approx(strikes[1] + round(price - strikes[1] + 1.5, 2))


def test_expiry_with_only_a_deep_pick_gives_reason_plus_deep(flagged):
    svc, quote = flagged
    price = quote["last_price"]
    ch = make_chain([contract(250,round(price * f, 1), spot=price) for f in FRACTIONS if f < 1.0])
    svc._chain_fn = FakeChain(ch)
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is True and out["contract"] is None and out["reason"] == REASON_NO_ENTRY
    assert out["deep"] is not None and out["deep"]["delta"] == 0.75 and out["expiries"] == [expiry_of(250)]


def test_after_hours_sentinel_chain_gives_the_no_delta_reason_then_the_entry_once_deltas_return(flagged):
    """Review 11-06 (finding 2), through the service: an evening chain (every greek +/-999) answers the
    no-delta reason plus the deep fallback; once the 300 s chain copy lapses and Schwab sends deltas
    again (the open), the same row gets its ENTRY pick with no reload."""
    svc, quote = flagged
    price = quote["last_price"]
    clock = {"t": 1000.0}
    svc._mono = lambda: clock["t"]
    strikes = [round(price * f, 1) for f in FRACTIONS]
    fake = svc._chain_fn = FakeChain(make_chain([contract(250,s, delta=-999.0, spot=price) for s in strikes],
                                                spot=price))
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["contract"] is None and out["reason"] == REASON_NO_DELTA
    assert out["deep"] is not None and out["deep"]["delta"] is None and out["deep"]["strike"] < price
    fake.chain = std_chain((250,), spot=price)
    clock["t"] += CHAIN_TTL_S + 1
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["reason"] == "" and out["contract"]["delta"] == 0.17 and out["deep"]["delta"] == 0.75
    assert len(fake.calls) == 2


def test_non_flagged_row_makes_no_chain_call(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    fake = svc._chain_fn = FakeChain(std_chain())
    quote = {"last_price": fixture_price("AAPL"), "quote_time_ms": NOW_MS}
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is False and out["contract"] is None and out["deep"] is None
    assert out["reason"] == REASON_NOT_FLAGGED
    assert out["symbol"] == "AAPL" and out["price"] == pytest.approx(fixture_price("AAPL"))
    assert out["expiries"] == [] and fake.calls == []
    # no quote at all: NO_PRICE row, still no chain call
    out = svc.leap_payload("AAPL", 50, None)
    assert out["leaps"] is False and out["reason"] == REASON_NOT_FLAGGED and fake.calls == []
    # an ETF never has a LEAPS flag
    out = svc.leap_payload("SPY", 50, {"last_price": 500.0, "quote_time_ms": NOW_MS})
    assert out["leaps"] is False and out["reason"] == REASON_NOT_FLAGGED and fake.calls == []
    svc.stop_refresher()


def test_chain_raising_is_chain_unavailable(flagged):
    svc, quote = flagged
    fake = svc._chain_fn = FakeChain(error="Schwab 503")
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is True and out["contract"] is None and out["deep"] is None
    assert out["reason"] == REASON_CHAIN_UNAVAILABLE
    assert out["expiries"] == [] and len(fake.calls) == 1


def test_empty_chain_is_no_chain(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain({})
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is True and out["contract"] is None and out["deep"] is None
    assert out["reason"] == REASON_NO_CHAIN


def test_chain_cached_300_seconds_per_symbol(flagged):
    svc, quote = flagged
    clock = {"t": 1000.0}
    svc._mono = lambda: clock["t"]
    fake = svc._chain_fn = FakeChain(std_chain(spot=quote["last_price"]))
    first = svc.leap_payload("AAPL", 50, quote)
    clock["t"] += CHAIN_TTL_S - 1
    second = svc.leap_payload("AAPL", 50, quote)
    assert first["contract"] == second["contract"] and first["deep"] == second["deep"] and len(fake.calls) == 1
    clock["t"] += 2
    svc.leap_payload("AAPL", 50, quote)
    assert len(fake.calls) == 2
    assert CHAIN_TTL_S == 300


def test_chain_failure_is_not_cached(flagged):
    svc, quote = flagged
    fake = svc._chain_fn = FakeChain(std_chain(spot=quote["last_price"]), error="boom")
    assert svc.leap_payload("AAPL", 50, quote)["reason"] == REASON_CHAIN_UNAVAILABLE
    fake.error = None
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["contract"] is not None and out["deep"] is not None and len(fake.calls) == 2


def test_mos_30_uses_the_same_row_path(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain(std_chain(spot=quote["last_price"]))
    out = svc.leap_payload("AAPL", 30, quote)
    assert out["leaps"] is True and out["contract"] is not None and out["deep"] is not None


def test_unknown_symbol_is_none(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain(std_chain())
    assert svc.leap_payload("ZZZQ", 50, quote) is None
    assert svc.leap_payload("", 50, quote) is None
    assert svc.leap_payload(None, 50, quote) is None


def test_leap_payload_never_raises(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain(std_chain())
    svc._row = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("row exploded"))
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["symbol"] == "AAPL" and out["contract"] is None and out["deep"] is None
    assert out["reason"] == REASON_CHAIN_UNAVAILABLE


def test_default_chain_fn_is_schwab_with_the_leap_window(tmp_path, monkeypatch):
    """The default asks SchwabClient for CALL-only, 60 strikes, from/to as datetimes."""
    svc = make_svc(tmp_path)
    seen = {}

    class Fake:
        def get_option_chain(self, symbol, contract_type="CALL", strike_count=20, from_date=None, to_date=None):
            seen.update(symbol=symbol, contract_type=contract_type, strike_count=strike_count,
                        from_date=from_date, to_date=to_date)
            return {"callExpDateMap": {}}

    svc._schwab = Fake()
    assert svc._chain_fn is None
    out = svc._schwab_chain("BRK/B", date(2027, 2, 4), date(2027, 11, 6))      # today + 90 .. today + 365
    assert out == {"callExpDateMap": {}}
    assert seen["symbol"] == "BRK/B" and seen["contract_type"] == "CALL" and seen["strike_count"] == 60
    assert seen["from_date"].date() == date(2027, 2, 4) and seen["to_date"].date() == date(2027, 11, 6)
    assert hasattr(seen["from_date"], "hour")                          # datetimes, as get_option_chain wants
    svc.stop_refresher()
