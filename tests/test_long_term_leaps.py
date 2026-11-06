"""LEAP to buy: ``pick_leap`` (pure, over a Schwab call chain) and ``LongTermService.leap_payload``.

No network. The chain fixtures are built here with fixed numbers; "today" is Friday 2026-11-06 (the
same frozen clock as tests/test_long_term_service.py), so every DTE is exact.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import pytest

from long_term.leaps import (
    FALLBACK_MONEYNESS, LEAP_MAX_DTE, LEAP_MIN_DTE, MAX_SPREAD, REASON_CHAIN_UNAVAILABLE, REASON_NO_CHAIN,
    REASON_NO_CONTRACT, REASON_NO_EXPIRY, REASON_NO_PRICE, REASON_NOT_FLAGGED, TARGET_DELTA, pick_leap,
)
from long_term.service import CHAIN_TTL_S
from test_long_term_service import NOW, NOW_MS, fixture_price, make_svc

TODAY = date(2026, 11, 6)
PRICE = 240.0
OMIT = object()                      # "leave the field out of the contract"
CONTRACT_KEYS = {
    "symbol", "expiry", "dte", "strike", "bid", "ask", "mid", "delta", "openInterest", "volume",
    "breakeven", "breakevenPct", "intrinsic", "extrinsic", "cost", "wideSpread", "thin",
}
PAYLOAD_KEYS = {"symbol", "name", "leaps", "price", "leapsBelow", "contract", "reason", "expiries",
                "fetchedAt", "refreshedAt"}


# ---------- fixture builders (Schwab /chains shape, see data/schwab_client.py::get_option_chain) ----------

def expiry_of(dte: int, today: date = TODAY) -> str:
    return (today + timedelta(days=dte)).isoformat()


def auto_delta(strike: float, spot: float = PRICE) -> float:
    """A deterministic call delta by moneyness: 0.75 lands exactly on strike 210 for spot 240."""
    return round(min(0.95, max(0.05, 0.5 + (spot - strike) / spot * 2.0)), 2)


def contract(dte: int, strike: float, *, bid=None, ask=None, delta=OMIT, oi=1234, volume=56,
             today: date = TODAY, symbol=None, put_call="CALL", expiry=None, spot: float = PRICE) -> dict:
    """One Schwab contract. ``bid``/``ask`` default to a tight live quote around intrinsic + time."""
    exp = expiry or expiry_of(dte, today)
    mid = max(0.15, spot - strike + 20.0)
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


STRIKES = (160.0, 180.0, 200.0, 210.0, 220.0, 240.0, 260.0, 280.0)        # for spot 240
FRACTIONS = tuple(s / PRICE for s in STRIKES)                                # the same ladder for any spot


def std_chain(dtes=(30, 400, 600, 800), *, spot: float = PRICE, deltas: bool = True, today: date = TODAY) -> dict:
    """Every strike at every DTE, live two-sided quotes, deltas by moneyness (or none at all).
    Strikes are the 240-spot ladder scaled to ``spot`` (2/3 .. 7/6 of the price)."""
    strikes = [round(spot * f, 1) for f in FRACTIONS]
    return make_chain([contract(d, s, delta=OMIT if deltas else None, today=today, spot=spot)
                       for d in dtes for s in strikes], spot=spot, today=today)


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
    assert (LEAP_MIN_DTE, LEAP_MAX_DTE, TARGET_DELTA, FALLBACK_MONEYNESS, MAX_SPREAD) == (365, 760, 0.75, 0.80, 0.25)


def test_farthest_expiry_in_window_wins():
    out = pick_leap(std_chain((30, 400, 600, 800)), price=PRICE, today=TODAY)
    c = out["contract"]
    assert c is not None and out["reason"] == ""
    assert c["expiry"] == expiry_of(600) and c["dte"] == 600
    assert out["expiries"] == [expiry_of(400), expiry_of(600)]            # eligible only, nearest first


def test_window_edges_are_inclusive():
    assert pick_leap(std_chain((365,)), price=PRICE, today=TODAY)["contract"]["dte"] == 365
    assert pick_leap(std_chain((760,)), price=PRICE, today=TODAY)["contract"]["dte"] == 760
    assert pick_leap(std_chain((364,)), price=PRICE, today=TODAY)["contract"] is None
    assert pick_leap(std_chain((761,)), price=PRICE, today=TODAY)["contract"] is None


def test_nothing_in_window_is_a_reason():
    out = pick_leap(std_chain((30, 900)), price=PRICE, today=TODAY)
    assert out == {"contract": None, "reason": REASON_NO_EXPIRY, "expiries": []}


def test_dte_from_today_when_given_else_from_the_contract():
    # key date 2 years out but the contract says 500 days: without `today` the contract's number counts
    ch = make_chain([contract(500, 200.0, expiry=(TODAY + timedelta(days=900)).isoformat())])
    assert pick_leap(ch, price=PRICE)["contract"]["dte"] == 500
    assert pick_leap(ch, price=PRICE, today=TODAY)["contract"] is None            # 900 days from today


def test_farther_expiry_without_a_usable_contract_falls_back_to_the_nearer_one():
    far = [contract(700, s, bid=0.0, ask=1.0) for s in STRIKES]                   # no live bids out there
    near = [contract(400, s) for s in STRIKES]
    out = pick_leap(make_chain(far + near), price=PRICE, today=TODAY)
    assert out["contract"]["dte"] == 400
    assert out["expiries"] == [expiry_of(400), expiry_of(700)]


# ---------- pick_leap: the contract ----------

def test_delta_closest_to_075_wins():
    c = pick_leap(std_chain((500,)), price=PRICE, today=TODAY)["contract"]
    assert c["strike"] == 210.0 and c["delta"] == 0.75


def test_delta_tie_goes_to_the_lower_strike():
    ch = make_chain([contract(500, 200.0, delta=0.80), contract(500, 220.0, delta=0.70),
                     contract(500, 180.0, delta=0.90)])
    c = pick_leap(ch, price=PRICE, today=TODAY)["contract"]
    assert c["strike"] == 200.0 and c["delta"] == 0.80


def test_math_on_fixed_numbers():
    ch = make_chain([contract(434, 200.0, bid=61.90, ask=62.40, delta=0.75, oi=1234, volume=56,
                              symbol="AAPL  280115C00200000")])
    out = pick_leap(ch, price=240.0, today=TODAY)
    c = out["contract"]
    assert set(c) == CONTRACT_KEYS
    assert c["symbol"] == "AAPL  280115C00200000" and c["expiry"] == expiry_of(434) and c["dte"] == 434
    assert (c["strike"], c["bid"], c["ask"], c["delta"]) == (200.0, 61.90, 62.40, 0.75)
    assert c["mid"] == pytest.approx(62.15)
    assert c["breakeven"] == pytest.approx(262.40)
    assert c["breakevenPct"] == pytest.approx(262.40 / 240.0 - 1)
    assert c["intrinsic"] == pytest.approx(40.0)
    assert c["extrinsic"] == pytest.approx(22.40)
    assert c["cost"] == pytest.approx(6240.0)
    assert (c["openInterest"], c["volume"], c["wideSpread"], c["thin"]) == (1234, 56, False, False)
    assert all(not (isinstance(v, float) and not math.isfinite(v)) for v in c.values())


def test_thin_market_flag_warns_but_never_hides():
    """QCOM's real June-2028 $125 call had open interest 1 on 2026-11-06: still the contract, with a warning."""
    from long_term.leaps import THIN_OI
    assert THIN_OI == 50
    for oi, thin in ((1, True), (49, True), (50, False), (1234, False), (None, True)):
        ch = make_chain([contract(500, 200.0, delta=0.75, oi=oi)])
        c = pick_leap(ch, price=PRICE, today=TODAY)["contract"]
        assert c is not None and c["thin"] is thin and c["openInterest"] == oi, (oi, c)


def test_wide_spread_flag():
    wide = make_chain([contract(500, 200.0, bid=40.0, ask=60.0, delta=0.75)])         # 20 / 50 = 0.40
    tight = make_chain([contract(500, 200.0, bid=55.0, ask=60.0, delta=0.75)])        # 5 / 57.5 = 0.087
    assert pick_leap(wide, price=PRICE, today=TODAY)["contract"]["wideSpread"] is True
    assert pick_leap(tight, price=PRICE, today=TODAY)["contract"]["wideSpread"] is False


def test_zero_bid_and_crossed_quotes_are_excluded():
    ch = make_chain([contract(500, 210.0, bid=0.0, ask=50.0, delta=0.75),           # the best delta, no bid
                     contract(500, 200.0, bid=60.0, ask=0.0, delta=0.76),            # no ask
                     contract(500, 190.0, bid=70.0, ask=69.0, delta=0.78),           # ask < bid
                     contract(500, 180.0, bid=80.0, ask=80.5, delta=0.85)])
    c = pick_leap(ch, price=PRICE, today=TODAY)["contract"]
    assert c["strike"] == 180.0


def test_out_of_the_money_and_at_the_money_are_excluded():
    ch = make_chain([contract(500, 240.0, delta=0.75), contract(500, 260.0, delta=0.75),
                     contract(500, 230.0, delta=0.55)])
    c = pick_leap(ch, price=PRICE, today=TODAY)["contract"]
    assert c["strike"] == 230.0


def test_no_deltas_falls_back_to_080_moneyness():
    # 0.80 x 240 = 192: strike 200 (8 away) beats 180 (12 away); 240/260 are not in the money
    out = pick_leap(std_chain((500,), deltas=False), price=PRICE, today=TODAY)
    c = out["contract"]
    assert c["strike"] == 200.0 and c["delta"] is None


def test_fallback_tie_goes_to_the_lower_strike():
    ch = make_chain([contract(500, 184.0, delta=None), contract(500, 200.0, delta=None)])     # both 8 from 192
    assert pick_leap(ch, price=PRICE, today=TODAY)["contract"]["strike"] == 184.0


@pytest.mark.parametrize("sentinel", [-999.0, 999.0, 0, 0.0, 1.5, -0.4, "-999", -999],
                         ids=["minus999", "plus999", "zero-int", "zero", "over-one", "negative", "str", "int"])
def test_schwab_sentinel_delta_is_no_delta_so_the_fallback_decides(sentinel):
    """Schwab stamps +/-999 or 0 on every greek after 16:00 ET. Before the fix the ladder tied on
    abs(-999 - 0.75) and the tie-break bought the LOWEST strike ($100, $16,025 a contract)."""
    strikes = (100.0, 120.0, 140.0, 160.0, 180.0, 200.0, 210.0, 220.0, 260.0)
    ch = make_chain([contract(500, s, delta=sentinel) for s in strikes])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    c = out["contract"]
    assert c is not None and out["reason"] == ""
    assert c["strike"] == 200.0 and c["delta"] is None          # 0.80 x 240 = 192 -> 200, not 100
    assert c["cost"] == pytest.approx(6025.0)                     # not 16,025


def test_sentinel_delta_never_beats_a_real_delta():
    ch = make_chain([contract(500, 100.0, delta=-999.0), contract(500, 120.0, delta=0.0),
                     contract(500, 200.0, delta=0.80), contract(500, 220.0, delta=0.70)])
    c = pick_leap(ch, price=PRICE, today=TODAY)["contract"]
    assert c["strike"] == 200.0 and c["delta"] == 0.80


def test_delta_edges_zero_excluded_one_included():
    assert pick_leap(make_chain([contract(500, 200.0, delta=1.0)]), price=PRICE, today=TODAY)["contract"]["delta"] == 1.0
    assert pick_leap(make_chain([contract(500, 200.0, delta=0.001)]), price=PRICE, today=TODAY)["contract"]["delta"] == 0.001
    assert pick_leap(make_chain([contract(500, 200.0, delta=1.0001)]), price=PRICE, today=TODAY)["contract"]["delta"] is None


def test_a_single_delta_in_the_expiry_is_used_not_the_fallback():
    ch = make_chain([contract(500, 160.0, delta=0.9), contract(500, 200.0, delta=None)])
    assert pick_leap(ch, price=PRICE, today=TODAY)["contract"]["strike"] == 160.0


def test_puts_in_the_call_map_are_ignored():
    ch = make_chain([contract(500, 200.0, delta=-0.25, put_call="PUT"), contract(500, 180.0, delta=0.85)])
    assert pick_leap(ch, price=PRICE, today=TODAY)["contract"]["strike"] == 180.0


def test_no_live_quote_anywhere_is_a_reason():
    ch = make_chain([contract(500, s, bid=0.0, ask=0.0) for s in STRIKES])
    out = pick_leap(ch, price=PRICE, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_CONTRACT
    assert out["expiries"] == [expiry_of(500)]


@pytest.mark.parametrize("price", [None, 0, -5, float("nan"), float("inf"), "abc", True])
def test_no_price_is_a_reason(price):
    out = pick_leap(std_chain((500,)), price=price, today=TODAY)
    assert out["contract"] is None and out["reason"] == REASON_NO_PRICE


@pytest.mark.parametrize("chain", [
    None, {}, [], "junk", 42,
    {"callExpDateMap": None}, {"callExpDateMap": "junk"}, {"callExpDateMap": []},
    {"putExpDateMap": {"2028-01-21:441": {}}},
], ids=["none", "empty", "list", "str", "int", "map-none", "map-str", "map-list", "puts-only"])
def test_no_chain_never_raises(chain):
    out = pick_leap(chain, price=PRICE, today=TODAY)
    assert out == {"contract": None, "reason": REASON_NO_CHAIN, "expiries": []}


def test_an_empty_call_map_is_a_verdict_not_an_outage():
    """Schwab answered the windowed request fine; the company lists nothing 12-25 months out.
    "no chain" is reserved for a missing/unreadable map (the page retries "no chain" on its own)."""
    out = pick_leap({"status": "SUCCESS", "callExpDateMap": {}, "putExpDateMap": {}}, price=PRICE, today=TODAY)
    assert out == {"contract": None, "reason": REASON_NO_EXPIRY, "expiries": []}


def test_an_ask_under_intrinsic_is_a_stale_print_not_a_live_quote():
    """After the close the option quotes are the 16:00 prints while the row price is the extended-hours
    last: an ask below (price - strike) cannot be filled, so it never becomes the LEAP to buy."""
    stale = contract(600, 200.0, bid=30.0, ask=35.0)          # intrinsic 40 at spot 240 -> ask 35 is stale
    live = contract(600, 180.0, bid=68.0, ask=69.0, delta=0.70)
    out = pick_leap(make_chain([stale, live]), price=PRICE, today=TODAY)
    assert out["contract"]["strike"] == 180.0 and out["contract"]["extrinsic"] >= 0
    alone = pick_leap(make_chain([stale]), price=PRICE, today=TODAY)
    assert alone["contract"] is None and alone["reason"] == REASON_NO_CONTRACT


@pytest.mark.parametrize("calls", [
    {"bad-key": {"200.0": [contract(500, 200.0, expiry="2028")]}},
    {"2028-03-17:497": "nope"},
    {"2028-03-17:497": {"200.0": "nope"}},
    {"2028-03-17:497": {"200.0": [None, 7, "x", []]}},
    {"2028-03-17:497": {"200.0": [{"bid": "x", "ask": None, "strikePrice": "wide"}]}},
    {"2028-03-17:497": {"abc": [{"bid": 1, "ask": 2}]}},
    {"junk": {"200.0": [contract(500, 200.0, expiry="not-a-date")]}},
], ids=["bad-key", "strikes-str", "list-str", "junk-items", "junk-fields", "no-strike", "no-dates"])
def test_garbage_contracts_never_raise(calls):
    out = pick_leap({"callExpDateMap": calls}, price=PRICE, today=TODAY)
    assert out["contract"] is None
    assert out["reason"] in {REASON_NO_EXPIRY, REASON_NO_CONTRACT, REASON_NO_CHAIN}
    assert isinstance(out["expiries"], list)


def test_strike_comes_from_the_map_key_when_the_contract_has_none():
    c = contract(500, 200.0, delta=0.75)
    c.pop("strikePrice")
    out = pick_leap(make_chain([contract(500, 200.0, delta=0.75)]) | {"callExpDateMap": {
        f"{expiry_of(500)}:500": {"200.0": [c]}}}, price=PRICE, today=TODAY)
    assert out["contract"]["strike"] == 200.0


def test_expiry_from_expiration_date_when_the_key_has_none():
    c = contract(500, 200.0, delta=0.75)
    out = pick_leap({"callExpDateMap": {"junk": {"200.0": [c]}}}, price=PRICE, today=TODAY)
    assert out["contract"]["expiry"] == expiry_of(500) and out["contract"]["dte"] == 500


def test_reason_strings_are_exact():
    assert REASON_NO_CHAIN == "no chain"
    assert REASON_NO_PRICE == "no price"
    assert REASON_NO_EXPIRY == "no expiry 12-25 months out"
    assert REASON_NO_CONTRACT == "no in-the-money call with a live quote"
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


def test_flagged_row_returns_a_contract_and_asks_for_the_schwab_window(flagged):
    svc, quote = flagged
    fake = svc._chain_fn = FakeChain(std_chain((30, 400, 600, 800), spot=quote["last_price"]))
    out = svc.leap_payload("aapl", 50, quote)
    assert set(out) == PAYLOAD_KEYS
    assert out["symbol"] == "AAPL" and out["name"] and out["leaps"] is True
    assert out["price"] == pytest.approx(quote["last_price"]) and out["leapsBelow"] >= out["price"]
    assert out["contract"] is not None and out["reason"] == ""
    assert out["contract"]["dte"] == 600 and out["expiries"] == [expiry_of(400), expiry_of(600)]
    assert out["contract"]["strike"] < out["price"] and out["contract"]["delta"] == 0.75
    assert out["fetchedAt"] and out["refreshedAt"]
    assert fake.calls == [("AAPL", NOW.date() + timedelta(days=365), NOW.date() + timedelta(days=760))]


def test_contract_is_picked_against_the_row_price(flagged):
    svc, quote = flagged
    price = quote["last_price"]
    strikes = [round(price * f, 1) for f in (0.6, 0.8, 0.9, 1.1)]
    # live quotes: intrinsic plus $1.00/$1.50 of time value (an ask under intrinsic is a stale print)
    ch = make_chain([contract(500, s, bid=round(max(price - s, 0) + 1.0, 2), ask=round(max(price - s, 0) + 1.5, 2),
                              delta=None) for s in strikes])
    svc._chain_fn = FakeChain(ch)
    c = svc.leap_payload("AAPL", 50, quote)["contract"]
    assert c["strike"] == strikes[1]                                   # 0.80 x price fallback, ITM only
    assert c["breakeven"] == pytest.approx(strikes[1] + round(price - strikes[1] + 1.5, 2))


def test_non_flagged_row_makes_no_chain_call(tmp_path):
    svc = make_svc(tmp_path)
    svc.refresh(force=True)
    fake = svc._chain_fn = FakeChain(std_chain())
    quote = {"last_price": fixture_price("AAPL"), "quote_time_ms": NOW_MS}
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is False and out["contract"] is None and out["reason"] == REASON_NOT_FLAGGED
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
    assert out["leaps"] is True and out["contract"] is None and out["reason"] == REASON_CHAIN_UNAVAILABLE
    assert out["expiries"] == [] and len(fake.calls) == 1


def test_empty_chain_is_no_chain(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain({})
    out = svc.leap_payload("AAPL", 50, quote)
    assert out["leaps"] is True and out["contract"] is None and out["reason"] == REASON_NO_CHAIN


def test_chain_cached_300_seconds_per_symbol(flagged):
    svc, quote = flagged
    clock = {"t": 1000.0}
    svc._mono = lambda: clock["t"]
    fake = svc._chain_fn = FakeChain(std_chain(spot=quote["last_price"]))
    first = svc.leap_payload("AAPL", 50, quote)
    clock["t"] += CHAIN_TTL_S - 1
    second = svc.leap_payload("AAPL", 50, quote)
    assert first["contract"] == second["contract"] and len(fake.calls) == 1
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
    assert out["contract"] is not None and len(fake.calls) == 2


def test_mos_30_uses_the_same_row_path(flagged):
    svc, quote = flagged
    svc._chain_fn = FakeChain(std_chain(spot=quote["last_price"]))
    out = svc.leap_payload("AAPL", 30, quote)
    assert out["leaps"] is True and out["contract"] is not None


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
    assert out["symbol"] == "AAPL" and out["contract"] is None and out["reason"] == REASON_CHAIN_UNAVAILABLE


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
    out = svc._schwab_chain("BRK/B", date(2027, 11, 6), date(2028, 12, 5))
    assert out == {"callExpDateMap": {}}
    assert seen["symbol"] == "BRK/B" and seen["contract_type"] == "CALL" and seen["strike_count"] == 60
    assert seen["from_date"].date() == date(2027, 11, 6) and seen["to_date"].date() == date(2028, 12, 5)
    assert hasattr(seen["from_date"], "hour")                          # datetimes, as get_option_chain wants
    svc.stop_refresher()
