"""momx/live_rvol.py: the forming bar rescored from stream minutes must equal
his TOS column script evaluated on the full tape (2026-11-03)."""
import random

import pytest

from momx import columns, feed, live_rvol

T0 = 1_793_721_600          # a 5-minute boundary (2026-11-03 10:40 ET)
SPAN = 300


def tape_and_minutes(n_buckets=60, seed=1, forming_minutes=3, extra_buckets=0):
    """5m tape of n_buckets (+extra) built from 1-minute bars; returns (tape up
    to the build's forming bucket, all minutes from that bucket on, full tape
    as TOS would see it now)."""
    rng = random.Random(seed)
    start = T0 - (n_buckets - 1) * SPAN
    minutes = []
    total = n_buckets + extra_buckets
    for b in range(total):
        count = 5 if b < total - 1 else forming_minutes
        for m in range(count):
            t = start + b * SPAN + m * 60
            o = 10 + rng.random()
            c = o + rng.uniform(-0.3, 0.3)
            minutes.append({"time": t, "open": o, "high": max(o, c) + rng.random() * 0.1,
                            "low": min(o, c) - rng.random() * 0.1, "close": c,
                            "volume": float(rng.randint(100, 5000) * (8 if rng.random() < 0.05 else 1))})

    def fold(mins):
        out = {}
        for bar in mins:
            k = bar["time"] - bar["time"] % SPAN
            held = out.get(k)
            if held is None:
                out[k] = dict(bar, time=k)
            else:
                held["high"] = max(held["high"], bar["high"])
                held["low"] = min(held["low"], bar["low"])
                held["close"] = bar["close"]
                held["volume"] += bar["volume"]
        return [out[k] for k in sorted(out)]

    build_cut = T0 + 60          # the build saw only the first minute of the T0 bucket
    tape_at_build = fold([m for m in minutes if m["time"] < build_cut])
    live_minutes = [m for m in minutes if m["time"] >= T0]
    return tape_at_build, live_minutes, fold(minutes)


@pytest.mark.parametrize("seed", [1, 2, 3, 7])
def test_live_score_equals_the_tos_script_on_the_full_tape(seed):
    tape_at_build, live_minutes, full = tape_and_minutes(seed=seed)
    base = columns.rvol_live_base(tape_at_build)
    got = live_rvol.score(base, live_minutes, SPAN)
    want = columns.rvol_cell(full)
    assert got is not None
    assert (got["value"], got["bg"], got["fg"]) == (want["value"], want["bg"], want["fg"])
    assert got["bucketAt"] == T0


@pytest.mark.parametrize("extra", [1, 2, 3])
def test_bucket_rollovers_shift_the_window_like_tos(extra):
    tape_at_build, live_minutes, full = tape_and_minutes(seed=11, extra_buckets=extra)
    base = columns.rvol_live_base(tape_at_build)
    got = live_rvol.score(base, live_minutes, SPAN)
    want = columns.rvol_cell(full)
    assert (got["value"], got["bg"], got["fg"]) == (want["value"], want["bg"], want["fg"])
    assert got["bucketAt"] == T0 + extra * SPAN


def test_more_rollovers_than_the_base_carries_waits_for_the_build():
    tape_at_build, live_minutes, _ = tape_and_minutes(seed=5, extra_buckets=columns.RVOL_LIVE_ROLLOVERS + 1)
    assert live_rvol.score(columns.rvol_live_base(tape_at_build), live_minutes, SPAN) is None


def test_remember_merges_per_symbol_and_never_wipes():
    live_rvol._STORE.clear()
    live_rvol.remember("aaa", {"5m": [1, 2, 3, 4], "1h": [5, 6, 7, 8]})
    live_rvol.remember("AAA", {"5m": [9, 9, 9, 9]})
    assert live_rvol._STORE["AAA"]["bases"] == {"5m": [9, 9, 9, 9], "1h": [5, 6, 7, 8]}


def test_build_row_bases_never_reach_the_wire():
    from momx import board
    tape_at_build, _, _ = tape_and_minutes()
    row = columns.build_row("AAA", {"5m": tape_at_build})
    assert row["_lb"]["5m"][0] == T0
    assert "_lb" not in board._contract_row(row, True, [])


def _serve(monkeypatch, minutes, *, now=None, base_tape=None):
    tape_at_build = base_tape or tape_and_minutes(seed=3)[0]
    live_rvol._STORE.clear()
    live_rvol._CACHE.clear()
    live_rvol.remember("AAA", {"5m": columns.rvol_live_base(tape_at_build)})
    monkeypatch.setattr(feed, "STREAM_LIVE_ENABLED", True)
    monkeypatch.setattr(feed, "_stream_live_refresh", lambda: True)
    newest = max(m["time"] for m in minutes) if minutes else 0
    monkeypatch.setattr(feed, "_STREAM_LIVE", {"bars": {"AAA": {m["time"]: m for m in minutes}}, "dead": [],
                                               "at": 0, "ok_at": 1, "max_t": newest})
    return live_rvol.compute(["aaa"], now=now if now is not None else newest + 30)


def _covered():
    _, live_minutes, _ = tape_and_minutes(seed=3)
    return [dict(live_minutes[0], time=T0 - 60)] + live_minutes


def test_compute_serves_a_fully_covered_symbol_and_matches_tos(monkeypatch):
    out = _serve(monkeypatch, _covered())
    _, _, full = tape_and_minutes(seed=3)
    want = columns.rvol_cell(full)
    got = out["cells"]["AAA"]["5m"]
    assert got["live"] is True and (got["value"], got["bg"], got["fg"]) == (want["value"], want["bg"], want["fg"])


def test_a_missing_minute_keeps_the_board_value(monkeypatch):
    minutes = [m for m in _covered() if m["time"] != T0 + 60]
    assert _serve(monkeypatch, minutes)["cells"] == {}


def test_stream_counting_less_than_the_build_is_not_trusted(monkeypatch):
    # The build already saw the whole forming bucket; a stream that counts less
    # (another feed, a partial minute) must not replace it.
    full = tape_and_minutes(seed=3)[2]
    minutes = [dict(m, volume=m["volume"] * 0.5) if m["time"] == T0 else m for m in _covered()]
    assert _serve(monkeypatch, minutes, base_tape=full)["cells"] == {}
    assert _serve(monkeypatch, _covered(), base_tape=full)["cells"]["AAA"]["5m"]["live"] is True


def test_symbol_leased_mid_bucket_or_released_or_frozen_stream(monkeypatch):
    _, live_minutes, _ = tape_and_minutes(seed=3)
    assert _serve(monkeypatch, live_minutes[1:])["cells"] == {}                   # starts after t0
    covered = _covered()
    newest = max(m["time"] for m in covered)
    assert _serve(monkeypatch, covered, now=newest + live_rvol.STREAM_STALE_SECONDS + 5)["cells"] == {}
