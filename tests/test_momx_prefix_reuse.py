"""momx.indicators prefix reuse: a warm call equals a cold call bit for bit (2026-09-25)."""
from __future__ import annotations

import math
import random

from momx import indicators as ind


def series(n, seed):
    rng = random.Random(seed)
    v, out = 100.0, []
    for _ in range(n):
        v *= 1 + rng.uniform(-0.01, 0.01)
        out.append(v)
    return out


def same(a, b):
    return len(a) == len(b) and all((math.isnan(x) and math.isnan(y)) or x == y for x, y in zip(a, b))


def test_warm_equals_cold_for_every_reused_primitive():
    base = series(400, 1)
    nxt = base[:-2] + [base[-3] * 1.004, base[-3] * 1.006, base[-3] * 1.001]   # last bars changed + one new
    for fn, args in ((ind.sma, (20,)), (ind.stdev_pop, (20,)), (ind.linreg_endpoint, (20,)), (ind.ema, (9,))):
        ind.memo_clear()
        cold = fn(nxt, *args)
        ind.memo_clear()
        fn(base, *args)                      # the "previous build"
        warm = fn(nxt, *args)
        assert same(cold, warm), fn.__name__
    ind.memo_clear()
    hi = [x * 1.01 for x in nxt]; lo = [x * 0.99 for x in nxt]
    cold = ind.ttm_squeeze(hi, lo, nxt)
    ind.memo_clear()
    ind.ttm_squeeze([x * 1.01 for x in base], [x * 0.99 for x in base], base)
    warm = ind.ttm_squeeze(hi, lo, nxt)
    assert cold.squeeze_alert == warm.squeeze_alert and same(cold.histogram, warm.histogram)


def test_lane_routing_is_stable():
    from momx import board
    assert board._lane_of("NVDA", 5) == board._lane_of("NVDA", 5)
    assert {board._lane_of(s, 5) for s in ("A", "B", "C", "D", "E", "F", "G", "H", "I", "J")} <= set(range(5))
