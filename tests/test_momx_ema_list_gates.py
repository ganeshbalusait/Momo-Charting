"""Unit: the EMA Touch lists' stricter Price_Change ALL-gates (2026-11-05).

His TOS "Momox_2NEW!" scan (the 50 EMA Monthly one) has, in "All of the
following": VOLUME 4h EXT at least 1.0% and CLOSE 1h EXT at least 0.5%
greater than 2 bars ago. The main scan keeps 0.5% / 0.3%. He chose "EMA
lists only".
"""
from __future__ import annotations

import random

import pytest

from momx import board, scan, service


def test_ema_list_thresholds_are_his_tos_numbers_and_the_main_scan_is_unchanged():
    assert scan.EMA_LIST_GATES == {"volume": 1.0, "close": 0.5}
    assert (scan.VOLUME_CHANGE_MIN_PCT, scan.CLOSE_CHANGE_MIN_PCT) == (0.5, 0.3)


@pytest.mark.parametrize("seed", range(30))
def test_the_reading_gives_the_gates_own_answer_at_any_threshold(seed):
    rng = random.Random(seed)
    tape = [{"close": rng.uniform(1, 50), "volume": rng.choice([0, rng.uniform(1, 1e6)])}
            for _ in range(rng.randint(0, 6))]
    for source in ("close", "volume"):
        pct = scan.price_change_pct(tape, source, 2)
        for threshold in (0.3, 0.5, 1.0, 5.0):
            want = scan.price_change_gate(tape, source, threshold, 2)
            assert (pct is not None and pct >= threshold) is want


@pytest.mark.parametrize("volume, close, blocked", [
    (1.0, 0.5, []),                                            # exactly at the minimum passes ("at least")
    (0.99, 0.5, ["blocked:pricechange:volume:4h"]),
    (1.0, 0.49, ["blocked:pricechange:close:1h"]),
    (0.7, 0.4, ["blocked:pricechange:volume:4h", "blocked:pricechange:close:1h"]),
    (None, 0.6, ["blocked:pricechange:volume:4h"]),           # no reading blocks, as in TOS
    (2.0, None, ["blocked:pricechange:close:1h"]),
])
def test_bull_gates(volume, close, blocked):
    assert scan.stricter_gates(volume, close, scan.EMA_LIST_GATES) == blocked


def test_bear_mirrors_only_the_close_row():
    gates = scan.EMA_LIST_GATES
    assert scan.stricter_gates(1.2, -0.5, gates, "bear") == []
    assert scan.stricter_gates(1.2, -0.4, gates, "bear") == ["blocked:pricechange:close:1h"]
    assert scan.stricter_gates(0.8, -0.9, gates, "bear") == ["blocked:pricechange:volume:4h"]


def test_a_record_is_rejudged_on_a_copy():
    record = {"symbol": "AAA", "scanPass": True, "_reasons": ["macd:D"], "bearPass": True,
              "_bearReasons": ["macd:Wk"], "_volChg4h": 0.7, "_closeChg1h": -0.8}
    out = board._apply_gate_min_pct(record, scan.EMA_LIST_GATES)
    assert out["scanPass"] is False and out["bearPass"] is False
    assert out["_reasons"][:2] == ["blocked:pricechange:volume:4h", "blocked:pricechange:close:1h"]
    assert out["_reasons"][-1] == "macd:D"
    assert record["scanPass"] is True and record["_reasons"] == ["macd:D"]      # the lent record is untouched
    strong = dict(record, _volChg4h=1.5, _closeChg1h=0.6)
    kept = board._apply_gate_min_pct(strong, scan.EMA_LIST_GATES)
    assert kept["scanPass"] is True and kept["_reasons"] == ["macd:D"]
    assert kept["bearPass"] is False                                            # +0.6% is not a 0.5% drop


def test_a_failed_match_never_turns_into_a_pass():
    record = {"scanPass": False, "_reasons": ["blocked:price"], "bearPass": False, "_bearReasons": [],
              "_volChg4h": 9.0, "_closeChg1h": 9.0}
    out = board._apply_gate_min_pct(record, scan.EMA_LIST_GATES)
    assert out["scanPass"] is False and out["bearPass"] is False


@pytest.mark.parametrize("name, gated", [
    ("21 EMA Touch Daily", True), ("50 EMA Touch Monthly", True), ("21 EMA Touch Weekly", True),
    ("Watchlist", False), ("Mag7", False), ("Liquid Movers", False),
])
def test_only_the_ema_lists_get_the_stricter_gates(name, gated):
    assert service._gate_kwargs(name) == ({"gate_min_pct": scan.EMA_LIST_GATES} if gated else {})
