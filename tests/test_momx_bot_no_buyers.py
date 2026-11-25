"""The BOT does not "buy" an option nobody is bidding for (2026-11-24 loss audit, bug 2).

Before: a $0 bid was priced at the mid, and the 0.20-delta pick trusted Schwab's
delta on strikes with no real market. That delta is worked out from the quote's
mid; with a $0 bid, or a penny bid under a wide ask, the mid is fiction and so
is the delta. Real chains saved on disk:

* EOSE 2026-11-24, stock $1.24, calls of 11-27: strikes 1.5 to 4.0 read delta
  0.076 falling to 0.024 (all bid $0 / ask $0.01), then the 4.5 strike reads
  0.167 because its ask is $0.14. The BOT took such strikes: on 2026-11-17 the
  $18 call with the stock at $1.735.
* ABNB 2026-11-24, stock $173.39: the 165 put, bid $0.01 / ask $0.71, "delta 0.105".

Round 2 (2026-11-25, after review):
* the 0.20-delta pick needs bid > $0 AND ask <= 3x bid on the quote it will trade on;
* each cause has its own reason (no strike / not quoted / no buyers / no sellers /
  quote not usable); a missing bid is not "$0";
* the final reason says how the signal was really checked (once, each time, or
  through its 10 minutes).

Which tests prove the fix: every test here fails on the code before the fix EXCEPT
the ones marked "regression guard" (the six placeholder-delta cases and the
open-trade case) and the "nearer strike reads a lower delta" case, which guards
against round 1's dropped rule. Those pass with or without the fix and only keep
old behaviour from breaking.
"""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from momx import option_track as ot
from momx import paper_mirror as pm

ET = ZoneInfo("America/New_York")
MON = datetime(2026, 9, 28, 9, 50, tzinfo=ET)
EXPIRY = "2026-10-02"
WAITING = "Waiting for a usable OTM option quote; retrying within the signal deadline"


class Clock:
    def __init__(self, t):
        self.t = t

    def __call__(self):
        return self.t


def leg(strike, delta, bid, ask, side="CALL", sym="X"):
    return {"symbol": f"{sym}_{side[0]}{strike}", "side": side, "strike": strike, "expiry": EXPIRY,
            "days_to_expiration": 4, "bid": bid, "ask": ask, "delta": delta}


def eose_chain():
    """The saved EOSE chain's shape: nobody bids for any out-of-the-money call."""
    rows = [leg(1.0, 0.71, 0.24, 0.26), leg(1.5, 0.076, 0.0, 0.01), leg(2.0, 0.042, 0.0, 0.01),
            leg(2.5, 0.033, 0.0, 0.01), leg(3.0, 0.029, 0.0, 0.01), leg(4.0, 0.024, 0.0, 0.01),
            leg(4.5, 0.167, 0.0, 0.14), leg(5.0, 0.022, 0.0, 0.01), leg(6.0, 0.156, 0.0, 0.14)]
    return {"underlyingPrice": 1.24, "selectedExpiryChainRows": rows}


def healthy_chain(spot=100.0, near=(1.0, 1.1)):
    rows = [leg(101.0, 0.42, *near), leg(102.5, 0.31, 0.6, 0.7), leg(105.0, 0.18, 0.3, 0.35),
            leg(107.5, 0.09, 0.1, 0.15), leg(110.0, 0.04, 0.02, 0.05),
            leg(99.0, -0.4, 0.9, 1.0, "PUT"), leg(95.0, -0.17, 0.3, 0.35, "PUT"), leg(92.5, -0.08, 0.1, 0.15, "PUT")]
    return {"underlyingPrice": spot, "selectedExpiryChainRows": rows}


def set_calls(chain, min_strike, **fields):
    for r in chain["selectedExpiryChainRows"]:
        if r["side"] == "CALL" and r["strike"] >= min_strike:
            r.update(fields)
    return chain


def book(tmp_path, chain, direction="bull", clock=None, quote_fetch=None):
    b = ot.OptionTrackBook(tmp_path, direction=direction, fetch=lambda s: chain, healthy=lambda: True,
                           background=False, clock=clock or Clock(MON.timestamp()), sleep=lambda s: None,
                           quote_fetch=quote_fetch or (lambda q: None))
    b._day = MON.date().isoformat()
    return b


def pending(rule="go", sym="X", at=MON):
    return {"rule": rule, "symbol": sym, "at": at.isoformat(), "status": "pending", "stockPrice": 100.0}


# ---------------------------------------------------------------- the quote test
@pytest.mark.parametrize("bid,ask,ok", [(1.0, 1.1, True), (0.01, 0.02, True), (0.0, 0.05, False), (None, 0.05, False),
                                        (0.5, None, False), (0.5, 0.0, False), (0.6, 0.5, False), (-1, 0.2, False),
                                        (float("nan"), 0.2, False), ("x", 0.2, False)])
def test_a_quote_needs_a_buyer_and_a_seller(bid, ask, ok):
    assert ot.two_sided(bid, ask) is ok


def test_each_thing_wrong_with_a_quote_has_its_own_name():
    """Changed on purpose from round 1, which called a MISSING bid "buyers" and so
    showed "bid $0" for quotes that carried no bid at all (review 2026-11-25)."""
    assert ot.quote_gap(0.0, 0.05) == "buyers" and ot.quote_gap(0, None) == "buyers"       # the bid really is $0
    for no_bid in (None, "", "x", float("nan"), -1):
        assert ot.quote_gap(no_bid, 0.05) == "missing"                                      # not quoted: NOT "$0"
    assert ot.quote_gap(0.5, None) == "sellers" and ot.quote_gap(0.5, 0) == "sellers"
    assert ot.quote_gap(0.6, 0.5) == "unusable"                                             # crossed
    assert ot.quote_gap(0.5, 0.6) is None and ot.quote_gap(0.01, 0.71) is None              # wide is fine for the main contract


@pytest.mark.parametrize("bid,ask,gap", [(0.3, 0.35, None), (0.1, 0.3, None), (0.1, 0.31, "unusable"),
                                         (0.01, 0.71, "unusable"), (0.07, 0.79, "unusable"), (0.1, 4.3, "unusable"),
                                         (0.0, 0.05, "buyers"), (None, 0.05, "missing"), (0.5, None, "sellers"),
                                         (0.6, 0.5, "unusable")])
def test_the_delta_pick_needs_buyers_within_3x_of_the_sellers(bid, ask, gap):
    """ask <= 3x bid = the spread is no more than 100% of the mid. The 0.01/0.71,
    0.07/0.79 and 0.10/4.30 quotes are the ABNB, AMGN and SNPS picks round 1 still made."""
    assert ot.delta_pick_gap(bid, ask) == gap


def test_one_reason_from_all_the_strikes_looked_at():
    assert ot.entry_leg_gap_of([]) == "none"                                # no strike under 0.20 delta
    assert ot.entry_leg_gap_of(["missing", "missing"]) == "missing"
    assert ot.entry_leg_gap_of(["buyers", "missing"]) == "buyers"
    # "No buyers (bid $0)" is only said when NO strike looked at had a bid.
    assert ot.entry_leg_gap_of(["buyers", "unusable", "buyers"]) == "unusable"
    assert ot.entry_leg_gap_of(["buyers", "sellers"]) == "sellers"


def test_the_final_reason_wording():
    assert ot.NO_BUYERS_ONCE == "No buyers when it was checked (bid $0): no trade taken"
    assert ot.quote_gap_final("buyers", "each") == "No buyers each time it was checked (bid $0): no trade taken"
    assert ot.quote_gap_final("buyers", "last") == "No buyers at the last check (bid $0): no trade taken"
    assert ot.NO_BUYERS_FINAL == "No buyers for this option (bid $0) in the 10 minutes after the signal: no trade taken"
    assert ot.quote_gap_final("missing", "once") == "No quote for this option when it was checked: no trade taken"
    assert "$0" not in "".join(ot.quote_gap_final("missing", m) for m in ("once", "last", "each", "window"))
    assert "$0" not in ot.QUOTE_GAP_WAIT["missing"] + ot.ENTRY_LEG_NOTE["missing"] + ot.ENTRY_LEG_NOTE["none"]


# ---------------------------------------------------------------- the 0.20-delta pick
def test_a_delta_on_a_strike_with_no_buyers_is_not_trusted():
    # Before the fix this returned the 4.5 strike (delta 0.167), 3.6x the stock price.
    assert ot.pick_entry_strike(eose_chain(), False, EXPIRY) is None
    assert ot.entry_leg_gap(eose_chain(), False, EXPIRY) == "buyers"


def test_a_penny_bid_under_a_wide_ask_is_not_trusted_even_on_the_nearest_strike():
    """Round 1 still returned this strike (ABNB 165 put, bid $0.01 / ask $0.71)."""
    chain = healthy_chain()
    set_calls(chain, 105.0, bid=0.01, ask=0.71)
    assert ot.pick_entry_strike(chain, False, EXPIRY) is None
    assert ot.entry_leg_gap(chain, False, EXPIRY) == "unusable"
    chain["selectedExpiryChainRows"][3].update(bid=0.1, ask=0.15)          # the 107.5 call has a real market
    e = ot.pick_entry_strike(chain, False, EXPIRY)
    assert (e["eStrike"], e["eDelta"], e["eEntry"]) == (107.5, 0.09, 0.125)


def test_the_real_under_20_delta_strike_wins_over_a_far_strike_with_a_made_up_delta():
    chain = healthy_chain()
    chain["selectedExpiryChainRows"] += [leg(140.0, 0.19, 0.0, 0.9), leg(150.0, 0.195, 0.05, 1.6)]
    e = ot.pick_entry_strike(chain, False, EXPIRY)
    # 140 has no buyers; 150 has a penny bid under a $1.60 ask.
    assert (e["eStrike"], e["eDelta"], e["eEntryBid"], e["eEntryAsk"]) == (105.0, 0.18, 0.3, 0.35)


def test_a_real_strike_is_not_dropped_because_a_nearer_strike_reads_a_lower_delta():
    """Round 1's extra "delta must fall away from the stock" guard is gone: on the
    saved CME chain it dropped the real 320 call (0.175) for the 317.5 (0.118).
    Fails on round 1 only; the code before round 1 never had that guard."""
    chain = healthy_chain()
    chain["selectedExpiryChainRows"][2:4] = [leg(104.0, 0.118, 0.5, 0.6), leg(105.0, 0.175, 0.4, 0.5)]
    assert ot.pick_entry_strike(chain, False, EXPIRY)["eStrike"] == 105.0


def test_put_side_uses_the_same_rules():
    chain = healthy_chain()
    chain["selectedExpiryChainRows"] += [leg(40.0, -0.19, 0.0, 0.8, "PUT"), leg(30.0, -0.199, 0.05, 1.2, "PUT")]
    assert ot.pick_entry_strike(chain, True, EXPIRY)["eStrike"] == 95.0


def test_the_candidates_are_ranked_by_delta_only_so_the_caller_can_test_its_own_quote():
    chain = healthy_chain()
    chain["selectedExpiryChainRows"] += [leg(140.0, 0.19, 0.0, 0.9)]
    set_calls(chain, 105.0, bid=0.0)                                        # the held chain shows no buyers at all
    assert [r["strike"] for r in ot.entry_strike_candidates(chain, False, EXPIRY)] == [140.0, 105.0, 107.5, 110.0]
    assert ot.entry_strike_candidates(chain, False, "2026-10-09") == []     # another expiry
    assert ot.entry_strike_fields(leg(105.0, 0.18, 0.0, 0.0), 0.4, 0.5) == {
        "eContract": "X_C105.0", "eStrike": 105.0, "eDelta": 0.18, "eEntry": 0.45, "eLast": 0.45, "eBest": 0.45,
        "eStatus": "open", "eEntryBid": 0.4, "eEntryAsk": 0.5}


@pytest.mark.parametrize("placeholder", [999.0, -999.0, 1.24, float("nan"), None, 0.0])
def test_regression_guard_a_placeholder_delta_never_picks_a_contract(placeholder):
    """Regression guard, NOT proof of the fix: the old rule already needed 0 < |delta| < 0.20,
    so this passes with the fix reverted."""
    chain = healthy_chain()
    for r in chain["selectedExpiryChainRows"]:
        if r["side"] == "CALL":
            r["delta"] = placeholder
    assert ot.pick_entry_strike(chain, False, EXPIRY) is None


def test_a_chain_of_placeholder_deltas_is_no_strike_under_020_delta_not_no_buyers():
    chain = healthy_chain()
    for r in chain["selectedExpiryChainRows"]:
        r["delta"] = 999.0 if r["side"] == "CALL" else -999.0
    assert ot.entry_leg_gap(chain, False, EXPIRY) == "none" and ot.entry_leg_gap(chain, True, EXPIRY) == "none"


def test_a_placeholder_delta_is_not_saved_on_the_main_contract():
    chain = healthy_chain()
    chain["selectedExpiryChainRows"][0]["delta"] = -999.0
    pick = ot.pick_contract(chain, False)
    assert pick["strike"] == 101.0 and pick["delta"] is None     # the strike rule never used delta
    assert ot.pick_contract(healthy_chain(), False)["delta"] == 0.42


# ---------------------------------------------------------------- the book: chain path
def test_no_trade_opens_on_a_zero_bid_and_a_signal_checked_once_says_once(tmp_path):
    """Changed on purpose from round 1, which wrote "in the 10 minutes after the
    signal" after a single $0-bid read (review 2026-11-25)."""
    clock = Clock(MON.timestamp())
    b = book(tmp_path, healthy_chain(near=(0.0, 0.05)), clock=clock)
    b._trades = [pending()]
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert t["status"] == "pending" and "entry" not in t and "contract" not in t
    assert t["why"] == ot.NO_BUYERS_WAIT
    b._fetch = lambda s: None                       # no fresh chain again: never re-checked
    clock.t += 601                                  # the signal's 10 minutes are over
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert t["status"] == "no_contract" and "entry" not in t and "quoteGap" not in t
    assert t["why"] == ot.NO_BUYERS_ONCE == "No buyers when it was checked (bid $0): no trade taken"
    c = b.capture_status()
    assert (c["signals"], c["entered"], c["notEntered"]) == (1, 0, 1)
    assert c["reasons"] == {ot.NO_BUYERS_ONCE: 1}


def test_only_a_signal_rechecked_to_its_deadline_says_the_10_minutes(tmp_path):
    clock = Clock(MON.timestamp())
    b = book(tmp_path, healthy_chain(near=(0.0, 0.05)), clock=clock)
    b._trades = [pending()]
    for wait in (0, 180, 180, 150):                 # checked at 0, 3, 6 and 8.5 minutes
        clock.t += wait
        b._price_symbol("X", False)
        assert b.trades()[0]["why"] == ot.NO_BUYERS_WAIT
    clock.t = MON.timestamp() + 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {ot.NO_BUYERS_FINAL: 1}


def test_checked_twice_early_and_never_again_does_not_claim_the_10_minutes(tmp_path):
    clock = Clock(MON.timestamp())
    b = book(tmp_path, healthy_chain(near=(0.0, 0.05)), clock=clock)
    b._trades = [pending()]
    b._price_symbol("X", False)
    clock.t += 60
    b._price_symbol("X", False)
    b._fetch = lambda s: None
    clock.t = MON.timestamp() + 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {"No buyers each time it was checked (bid $0): no trade taken": 1}


def test_entries_closing_first_does_not_claim_the_10_minutes(tmp_path):
    late = MON.replace(hour=15, minute=25)
    clock = Clock(late.timestamp())
    b = book(tmp_path, healthy_chain(near=(0.0, 0.05)), clock=clock)
    b._trades = [pending(at=late)]
    for wait in (0, 180, 150):                      # 15:25, 15:28, 15:30:30
        clock.t += wait
        b._price_symbol("X", False)
    clock.t = late.timestamp() + 6 * 60 + 5         # 15:31: entry hours are over, 6 minutes after the signal
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert t["status"] == "no_contract"
    assert t["why"] == "No buyers each time it was checked (bid $0): no trade taken"


def test_a_later_failure_for_another_cause_does_not_keep_the_no_buyers_reason(tmp_path):
    """Round 1 kept "No buyers ... in the 10 minutes" after ONE $0-bid read followed
    by passes that failed for a different cause (review 2026-11-25)."""
    clock = Clock(MON.timestamp())
    chain = healthy_chain(near=(0.0, 0.05))
    b = book(tmp_path, chain, clock=clock)
    b._trades = [pending()]
    b._price_symbol("X", False)
    assert b.trades()[0]["why"] == ot.NO_BUYERS_WAIT
    for r in chain["selectedExpiryChainRows"]:      # the next chain has no priced call at all
        r.update(bid=None, ask=None)
    clock.t += 60
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert t["why"] == WAITING and "quoteGap" not in t
    clock.t = MON.timestamp() + 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {ot.GENERIC_NOT_ENTERED: 1}
    assert "buyers" not in ot.GENERIC_NOT_ENTERED.lower()


def test_buyers_showing_up_inside_the_deadline_open_the_trade_at_the_real_quote(tmp_path):
    clock = Clock(MON.timestamp())
    chain = healthy_chain(near=(0.0, 0.05))
    b = book(tmp_path, chain, clock=clock)
    b._trades = [pending()]
    b._price_symbol("X", False)
    chain["selectedExpiryChainRows"][0].update(bid=1.0, ask=1.1)
    clock.t += 60
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert (t["status"], t["entry"], t["entryBid"], t["entryAsk"]) == ("open", 1.05, 1.0, 1.1)
    assert "why" not in t and "quoteGap" not in t and b.capture_status()["notEntered"] == 0


def test_the_bear_book_skips_a_put_with_no_buyers_too(tmp_path):
    chain = healthy_chain()
    chain["selectedExpiryChainRows"][5].update(bid=0.0, ask=0.1)        # the 99 put
    b = book(tmp_path, chain, direction="bear")
    b._trades = [pending()]
    b._price_symbol("X", False)
    assert b.trades()[0]["status"] == "pending" and b.trades()[0]["why"] == ot.NO_BUYERS_WAIT


def open_one(tmp_path, chain):
    b = book(tmp_path, chain)
    b._trades = [pending()]
    b._price_symbol("X", False)
    return b, b.trades()[0]


def entry_leg(b):
    return {k: n for k, n in b.capture_status()["entryLeg"].items() if n}


def test_an_open_trade_whose_020_delta_strikes_have_no_buyers_says_so_and_is_counted(tmp_path):
    b, t = open_one(tmp_path, set_calls(healthy_chain(), 105.0, bid=0.0))
    assert t["status"] == "open" and t["eStatus"] == "none" and "eContract" not in t
    assert (t["eGap"], t["eNote"]) == ("buyers", ot.ENTRY_LEG_NOTE["buyers"])
    assert entry_leg(b) == {"buyers": 1}
    # ...so the paper accounts, which buy the 0.20-delta contract, have nothing to buy.
    assert pm.still_open(t, "entry") is False
    assert pm.eligible({**t, "rule": "level1"}, MON.timestamp() + 5, None, "entry") is False


def test_no_strike_under_020_delta_is_not_called_no_buyers(tmp_path):
    chain = healthy_chain()
    chain["selectedExpiryChainRows"] = [r for r in chain["selectedExpiryChainRows"]
                                        if r["side"] == "PUT" or r["strike"] < 105.0]
    b, t = open_one(tmp_path, chain)
    assert t["status"] == "open" and (t["eGap"], t["eNote"]) == ("none", ot.ENTRY_LEG_NOTE["none"])
    assert "bid" not in t["eNote"] and entry_leg(b) == {"none": 1}


def test_a_wide_quote_on_the_020_delta_strikes_is_quote_not_usable(tmp_path):
    b, t = open_one(tmp_path, set_calls(healthy_chain(), 105.0, bid=0.05, ask=0.9))
    assert t["status"] == "open" and "eContract" not in t and t["eGap"] == "unusable"
    assert entry_leg(b) == {"unusable": 1}


def test_no_ask_on_the_020_delta_strikes_is_no_sellers(tmp_path):
    b, t = open_one(tmp_path, set_calls(healthy_chain(), 105.0, bid=0.2, ask=None))
    assert t["status"] == "open" and "eContract" not in t and t["eGap"] == "sellers"
    assert entry_leg(b) == {"sellers": 1}


def test_a_healthy_chain_has_a_020_delta_contract_and_nothing_is_counted(tmp_path):
    b, t = open_one(tmp_path, healthy_chain())
    assert (t["eContract"], t["eEntry"]) == ("X_C105.0", 0.325) and "eGap" not in t and "eNote" not in t
    assert entry_leg(b) == {} and set(b.capture_status()["entryLeg"]) == set(ot.ENTRY_LEG_GAP_KINDS)


def test_regression_guard_a_trade_already_open_keeps_its_mid_marks_and_exits(tmp_path):
    """Regression guard, NOT proof of the fix: open trades are unchanged, a $0 bid later
    on is still marked at the mid. Passes with the fix reverted."""
    chain = healthy_chain()
    b = book(tmp_path, chain)
    b._trades = [pending()]
    b._price_symbol("X", False)
    chain["selectedExpiryChainRows"][0].update(bid=0.0, ask=0.4)
    b._price_symbol("X", False)
    t = b.trades()[0]
    assert (t["status"], t["entry"], t["last"], t["lastBid"]) == ("open", 1.05, 0.2, 0.0)


# ---------------------------------------------------------------- the book: live-quote path
def quote_reply(pick_bid, pick_ask, e_bid=0.3, e_ask=0.35, z_bid=0.2, z_ask=0.3, **extra):
    def mid(b, a):
        return round(((b or 0) + (a or 0)) / 2, 4)
    return {"results": [{"ok": True, "symbol": "X", "chainAt": "c",
                         "pick": {"contract": "X_C101.0", "strike": 101.0, "expiry": EXPIRY, "delta": 0.42,
                                  "spot": 100.0, "entry": mid(pick_bid, pick_ask), "entryBid": pick_bid,
                                  "entryAsk": pick_ask},
                         "e": {"eContract": "X_C105.0", "eStrike": 105.0, "eDelta": 0.18, "eEntry": mid(e_bid, e_ask),
                               "eLast": mid(e_bid, e_ask), "eBest": mid(e_bid, e_ask), "eStatus": "open",
                               "eEntryBid": e_bid, "eEntryAsk": e_ask},
                         "z": {"contract": "X_C101.0_0928", "strike": 101.0, "entry": mid(z_bid, z_ask),
                               "entryBid": z_bid, "entryAsk": z_ask},
                         "levels": {"expectedMove": 2.0}, **extra}]}


def not_ok(why, side=None):
    return {"results": [{"ok": False, "symbol": "X", "why": why, **({"noQuoteSide": side} if side else {})}]}


def live_book(tmp_path, replies, clock=None):
    """A book whose live-quote answers are ``replies``, one per check."""
    queue = list(replies)
    b = book(tmp_path, healthy_chain(), clock=clock, quote_fetch=lambda q: queue.pop(0))
    b._fetch = lambda s: None                       # no chain: only the live-quote path prices
    b._trades = [pending()]
    return b


def test_the_live_quote_path_refuses_a_zero_bid_even_from_an_older_server(tmp_path):
    """An api_server not yet restarted still answers a $0-bid pick priced at the mark."""
    b = live_book(tmp_path, [quote_reply(0.0, 0.05)])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "pending" and "entry" not in t and t["why"] == ot.NO_BUYERS_WAIT


def test_a_live_quote_with_no_bid_at_all_is_not_called_bid_zero(tmp_path):
    """Round 1 showed "No buyers (bid $0)" for a quote that carried no bid."""
    clock = Clock(MON.timestamp())
    b = live_book(tmp_path, [quote_reply(None, 0.05)], clock)
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "pending" and t["why"] == ot.QUOTE_GAP_WAIT["missing"] and "$0" not in t["why"]
    clock.t += 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {"No quote for this option when it was checked: no trade taken": 1}


@pytest.mark.parametrize("side,final", [("buyers", "No buyers when it was checked (bid $0): no trade taken"),
                                        ("sellers", "No sellers when it was checked (no ask): no trade taken"),
                                        ("unusable", "Quote not usable when it was checked (buyers priced above sellers): no trade taken"),
                                        ("missing", "No quote for this option when it was checked: no trade taken")])
def test_the_live_quote_path_reads_each_of_the_servers_reasons(tmp_path, side, final):
    clock = Clock(MON.timestamp())
    b = live_book(tmp_path, [not_ok("x", side)], clock)
    b._fast_price(["X"])
    assert b.trades()[0]["status"] == "pending" and b.trades()[0]["why"] == ot.QUOTE_GAP_WAIT[side]
    clock.t += 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {final: 1}


def test_buyers_appearing_inside_the_window_open_the_trade_on_the_live_quote(tmp_path):
    clock = Clock(MON.timestamp())
    b = live_book(tmp_path, [not_ok("no buyers", "buyers"), quote_reply(1.0, 1.1)], clock)
    b._fast_price(["X"])
    assert b.trades()[0]["why"] == ot.NO_BUYERS_WAIT
    clock.t += 120
    b._fast_price(["X"])
    t = b.trades()[0]
    assert (t["status"], t["entry"], t["entryBid"], t["entryAsk"], t["entryVia"]) == ("open", 1.05, 1.0, 1.1, "quote")
    assert t["entryAt"] == datetime.fromtimestamp(clock.t, ET).replace(microsecond=0).isoformat()     # not backdated
    assert "why" not in t and "quoteGap" not in t
    assert b.capture_status()["notEntered"] == 0 and b.capture_status()["reasons"] == {}


def test_a_later_live_failure_for_another_cause_drops_the_no_buyers_reason(tmp_path):
    clock = Clock(MON.timestamp())
    b = live_book(tmp_path, [not_ok("no buyers", "buyers"), not_ok("no chain held")], clock)
    b._fast_price(["X"])
    assert b.trades()[0]["why"] == ot.NO_BUYERS_WAIT
    clock.t += 60
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "pending" and "why" not in t and "quoteGap" not in t
    clock.t = MON.timestamp() + 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {ot.GENERIC_NOT_ENTERED: 1}


def test_a_different_quote_problem_at_the_last_check_is_the_one_reported(tmp_path):
    clock = Clock(MON.timestamp())
    b = live_book(tmp_path, [not_ok("no buyers", "buyers"), not_ok("no sellers", "sellers")], clock)
    b._fast_price(["X"])
    clock.t += 60
    b._fast_price(["X"])
    assert b.trades()[0]["why"] == ot.NO_SELLERS_WAIT
    clock.t = MON.timestamp() + 601
    b._price_symbol("X", False)
    assert b.capture_status()["reasons"] == {"No sellers at the last check (no ask): no trade taken": 1}


def test_the_live_quote_path_drops_side_contracts_nobody_bids_for(tmp_path):
    b = live_book(tmp_path, [quote_reply(1.0, 1.1, e_bid=0.0, e_ask=0.2, z_bid=0.0)])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert (t["status"], t["entry"], t["entryVia"]) == ("open", 1.05, "quote")
    assert t["eStatus"] == "none" and "eContract" not in t and "eEntry" not in t
    assert (t["eGap"], t["eNote"]) == ("buyers", ot.ENTRY_LEG_NOTE["buyers"])
    assert t["zStatus"] == "none" and "zContract" not in t


def test_the_live_quote_path_drops_a_020_delta_contract_with_a_wide_live_quote(tmp_path):
    b = live_book(tmp_path, [quote_reply(1.0, 1.1, e_bid=0.01, e_ask=0.71)])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "open" and "eContract" not in t and t["eGap"] == "unusable"
    assert entry_leg(b) == {"unusable": 1}


@pytest.mark.parametrize("kind", ot.ENTRY_LEG_GAP_KINDS)
def test_the_live_quote_path_keeps_the_servers_reason_for_no_020_delta_contract(tmp_path, kind):
    reply = quote_reply(1.0, 1.1, eGap=kind)
    reply["results"][0]["e"] = None
    b = live_book(tmp_path, [reply])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "open" and (t["eStatus"], t["eGap"], t["eNote"]) == ("none", kind, ot.ENTRY_LEG_NOTE[kind])
    assert entry_leg(b) == {kind: 1}


def test_an_older_server_that_gives_no_reason_gets_none_made_up(tmp_path):
    reply = quote_reply(1.0, 1.1)
    reply["results"][0]["e"] = None
    b = live_book(tmp_path, [reply])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert t["status"] == "open" and t["eStatus"] == "none" and "eGap" not in t and "eNote" not in t
    assert entry_leg(b) == {}


def test_the_live_quote_path_still_opens_on_a_real_quote(tmp_path):
    b = live_book(tmp_path, [quote_reply(1.0, 1.1)])
    b._fast_price(["X"])
    t = b.trades()[0]
    assert (t["status"], t["eContract"], t["eEntry"], t["zContract"]) == ("open", "X_C105.0", 0.325, "X_C101.0_0928")
    assert "eNote" not in t and entry_leg(b) == {}
