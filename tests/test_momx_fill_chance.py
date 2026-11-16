"""Fill likelihood labels for paper buys at the mid, like Robinhood (2026-11-10)."""
from momx import fill_chance as fc


def test_the_label_comes_from_the_spread_like_the_records_showed():
    assert fc.stamp(0.61, 0.62) == {"label": "high", "spreadPct": 1.6, "by": "spread"}          # AAPL put 11-10
    assert fc.stamp(8.00, 8.80)["label"] == "medium"                                            # his Robinhood APP call: 9.5%
    assert fc.stamp(1.77, 3.35)["label"] == "low"                                               # NET call 11-10: 62%
    assert [fc.label(x) for x in (0, 0.0499, 0.05, 0.1499, 0.15, 2)] == ["high", "high", "medium", "medium", "low", "low"]
    for bid, ask in ((None, 1), (1, None), (2, 1), (-1, 1), (0, 0), ("x", 1)):
        assert fc.stamp(bid, ask) is None
    assert fc.label(None) is None and fc.label(-0.1) is None


def test_an_old_order_without_a_stamp_is_labelled_from_its_saved_spread():
    assert fc.label_of({"fillChance": {"label": "low"}, "spreadAtOrder": 0.01}) == "low"
    assert fc.label_of({"spreadAtOrder": 0.03}) == "high"
    assert fc.label_of({"fillChance": {"label": "bogus"}, "spreadAtOrder": 0.2}) == "low"
    assert fc.label_of({}) is None


def order(label, filled=True, tries=0, **extra):
    r = {"buyId": "b", "status": "closed" if filled else "missed", "fillChance": {"label": label}, "buyTries": tries}
    if filled:
        r["buyFill"] = 1.0
    r.update(extra)
    return r


def test_measured_counts_only_orders_that_reached_the_market_and_finished():
    rows = [order("high"), order("high"), order("high", tries=1), order("high", filled=False),
            order("low", filled=False), order("low", status="buying", filled=False),            # unfinished: unknown yet
            order("low", filled=False, note="The broker refused this buy: not enough buying power in the paper account"),
            order("low", filled=False, note="The broker has no record of this buy: it never reached the broker"),
            {"status": "skipped", "fillChance": {"label": "low"}}, {"buyId": "b", "status": "missed"}]
    m = fc.measured(rows)
    assert m["high"] == {"orders": 4, "filledAtMid": 2, "filled": 3, "midPct": 50, "filledPct": 75, "enough": False}
    assert m["low"] == {"orders": 1, "filledAtMid": 0, "filled": 0, "midPct": 0, "filledPct": 0, "enough": False}
    assert m["medium"] == {"orders": 0, "filledAtMid": 0, "filled": 0, "midPct": None, "filledPct": None, "enough": False}
    assert fc.measured([order("medium")] * fc.MIN_MEASURED)["medium"]["enough"] is True



def test_a_mid_order_that_sat_at_the_market_counts_even_if_a_later_resend_was_refused():
    """Review 2026-11-10: dropping it made 'filled at the mid' read too high."""
    refused_resend = order("high", filled=False, tries=1, note="The broker refused this buy: not enough buying power in the paper account")
    removed_after = order("high", filled=False, tries=2, note="Removed by you: stuck buy, nothing filled at the broker")
    refused_first = order("high", filled=False, tries=0, note="The broker refused this buy: x")
    m = fc.measured([order("high"), refused_resend, removed_after, refused_first])
    assert (m["high"]["orders"], m["high"]["filledAtMid"], m["high"]["midPct"]) == (3, 1, 33)
