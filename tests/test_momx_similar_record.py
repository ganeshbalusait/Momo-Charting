"""The Record column's overall win % (2026-11-02): similar past trades, counted."""
import numpy as np

from momx import similar_record as sr


def test_similar_trades_win_rate_follows_the_neighbours():
    rng = np.random.default_rng(1)
    n = len(sr.ROW_KEYS)
    x = np.zeros((2000, n)); x[:, 0] = rng.normal(size=2000)
    y = (x[:, 0] > 0).astype(float)          # winners all sit on one side of input 0
    side = sr._Side(x, y)
    high = np.zeros((1, n)); high[0, 0] = 2.0
    low = np.zeros((1, n)); low[0, 0] = -2.0
    assert side.win_rates(high)[0] > 90
    assert side.win_rates(low)[0] < 10
    assert abs(side.base - 50) < 5


def test_missing_inputs_sit_at_the_average_instead_of_breaking():
    n = len(sr.ROW_KEYS)
    x = np.full((600, n), np.nan); x[:, 0] = np.arange(600)
    side = sr._Side(x, (np.arange(600) >= 300).astype(float))
    q = np.full((1, n), np.nan); q[0, 0] = 590
    assert side.win_rates(q, k=50)[0] == 100.0


def test_apply_never_raises_and_skips_when_not_loaded(monkeypatch):
    monkeypatch.setattr(sr, "_ensure_loaded", lambda: None)
    monkeypatch.setitem(sr._STATE, "sides", {})
    payload = {"rows": [{"symbol": "A"}]}
    sr.apply(payload, "bull")
    assert "similarRecord" not in payload["rows"][0]
