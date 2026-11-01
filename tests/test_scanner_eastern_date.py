"""_eastern_date_of_epoch must give exactly the date the per-signal pandas
conversion gave (2026-10-06: that conversion was ~0.35 s of each ~1 s MTF
projection). Checked around midnight ET and both DST changes."""

from __future__ import annotations

import pandas as pd

import scanner
from config import EASTERN_TZ


def test_matches_pandas_around_midnight_and_dst():
    epochs = []
    for day in ("2026-03-08", "2026-03-09", "2026-11-01", "2026-11-02", "2026-10-06"):
        midnight = pd.Timestamp(day, tz=EASTERN_TZ)
        for minutes in (-61, -60, -1, 0, 1, 59, 60, 61, 4 * 60, 20 * 60):
            epochs.append(int((midnight + pd.Timedelta(minutes=minutes)).timestamp()))
    for epoch in epochs:
        expected = pd.to_datetime(epoch, unit="s", utc=True).tz_convert(EASTERN_TZ).date()
        assert scanner._eastern_date_of_epoch(epoch) == expected, epoch


def test_first_index_of_matches_the_linear_scan():
    import numpy as np

    values = np.array([5, 5, 7, 7, 7, 9, 9])
    for target in (5, 7, 9):
        expected = next(i for i in range(len(values)) if values[i] == target)
        assert scanner._first_index_of(values, target) == expected
