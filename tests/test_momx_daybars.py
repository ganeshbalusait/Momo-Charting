"""The ticker card's 'today 4:00-20:00 ET' chart data (momx/daybars.py)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from momx.daybars import day_bars, session_closes

ET = ZoneInfo("America/New_York")


def _frame(points):
    return pd.DataFrame({"time": [datetime(*p[0], tzinfo=ET) for p in points], "close": [p[1] for p in points]}).set_index("time")


def test_newest_day_inside_4am_to_8pm_only():
    f = _frame([((2026, 10, 1, 15, 55), 230.0),
                ((2026, 10, 2, 3, 55), 231.0),       # overnight (BOATS) - not on the card chart
                ((2026, 10, 2, 4, 0), 233.0),
                ((2026, 10, 2, 10, 25), 246.99),
                ((2026, 10, 2, 19, 55), 242.95),
                ((2026, 10, 2, 20, 0), 243.5)])      # after 20:00 - out
    out = session_closes(f)
    assert out["day"] == "2026-10-02"
    assert [c for _, c in out["bars"]] == [233.0, 246.99, 242.95]
    assert (out["low"], out["high"]) == (233.0, 246.99)


def test_nothing_or_garbage_is_empty_never_raises():
    assert session_closes(pd.DataFrame({"time": [], "close": []})) == {}
    assert session_closes(None) == {}
    assert day_bars("bad symbol!")["error"] == "bad symbol"


def test_a_snapshot_day_draws_that_day_or_the_last_session_before_it():
    f = _frame([((2026, 10, 1, 10, 0), 230.0), ((2026, 10, 2, 10, 0), 240.0), ((2026, 10, 2, 11, 0), 241.0)])
    assert session_closes(f, "2026-10-01")["day"] == "2026-10-01"
    assert session_closes(f, "2026-10-04")["day"] == "2026-10-02"      # Sunday snapshot -> Friday
    assert session_closes(f, "2026-09-30") == {}
    assert day_bars("NBIS", "2025-01-01")["error"] == "day out of range"
