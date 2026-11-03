"""Spread column (option_spreads.py): which watchlist names won't fill at mid.

2026-11-03, Ganesh: "which tickers have more wide option spread - when we fill
mid price it won't fill". The column must be honest: a ticker with no quote is
blank (never "Tight"), opening/closing quotes are never read, and the sweep
never hammers Schwab.
"""
from __future__ import annotations

from datetime import date, datetime

import pytest

import option_spreads as osp

ET = osp.ET


def _chain(spot, quote, expiries=("2026-11-06:3", "2026-11-13:10", "2026-11-20:17", "2026-12-18:45")):
    """quote(expiry_key, strike) -> (bid, ask). Strikes spot-6 .. spot+6 in $1."""
    side_map = {}
    for key in expiries:
        side_map[key] = {}
        for k in range(-6, 7):
            strike = spot + k
            bid, ask = quote(key, strike)
            side_map[key][f"{strike:.1f}"] = [{
                "bid": bid, "ask": ask, "strikePrice": strike, "openInterest": 100, "totalVolume": 7,
            }]
    return {"underlyingPrice": spot, "callExpDateMap": side_map, "putExpDateMap": side_map}


TODAY = date(2026, 11, 3)


def test_samples_front_expiry_and_next_monthly_atm_plus_minus_two():
    reading = osp.analyse(_chain(100, lambda e, s: (5.0, 5.1)), TODAY)
    assert reading["expiries"] == ["2026-11-06", "2026-11-20"]
    # 5 strikes x call/put x 2 expiries
    assert reading["sampled"] == 20
    assert reading["skipped"] == 0
    assert reading["pct"] == pytest.approx(0.1 / 5.05 * 100, abs=0.01)
    assert reading["usd"] == pytest.approx(0.1)
    # ATM OI = front expiry ATM call + put
    assert reading["atmOi"] == 200


def test_skips_zero_dte_on_its_own_expiry_day():
    chain = _chain(100, lambda e, s: (5.0, 5.1), expiries=("2026-11-03:0", "2026-11-06:3", "2026-11-20:17"))
    assert osp.analyse(chain, TODAY)["expiries"] == ["2026-11-06", "2026-11-20"]


def test_far_strikes_do_not_count():
    # Only strikes 98..102 are sampled; the far ones are absurdly wide.
    def quote(e, s):
        return (5.0, 5.1) if 98 <= s <= 102 else (0.05, 9.0)
    reading = osp.analyse(_chain(100, quote), TODAY)
    assert reading["pct"] < 3


def test_no_bid_contracts_are_skipped_and_counted_not_treated_as_tight():
    reading = osp.analyse(_chain(10, lambda e, s: (0.0, 0.05)), TODAY)
    assert "error" in reading
    assert osp.summarise([]) is None


@pytest.mark.parametrize("pct,cheap,expected", [
    (1.0, None, "Tight"),
    (2.99, None, "Tight"),
    (3.0, None, "OK"),
    (7.9, None, "OK"),
    (8.0, None, "Wide"),
    (15.0, None, "Wide"),
    (15.1, None, "Avoid"),
    (5.0, 0.30, "Avoid"),     # $1-ish option with a 30c spread
    (5.0, 0.25, "OK"),
    (None, None, None),
])
def test_classes(pct, cheap, expected):
    assert osp.classify(pct, cheap) == expected


def test_cheap_atm_wide_dollar_spread_is_avoid_even_if_percent_is_moderate():
    reading = osp.analyse(_chain(10, lambda e, s: (2.40, 2.75)), TODAY)
    assert reading["cheapAtmUsd"] == pytest.approx(0.35)
    assert osp.classify(reading["pct"], reading["cheapAtmUsd"]) == "Avoid"


def test_summary_is_median_of_latest_session_only():
    readings = [
        {"pct": 40.0, "usd": 1.0, "at": "2026-11-02T11:00:00-05:00"},   # yesterday: ignored
        {"pct": 4.0, "usd": 0.1, "at": "2026-11-03T10:30:00-05:00"},
        {"pct": 50.0, "usd": 0.9, "at": "2026-11-03T12:00:00-05:00"},   # one bad snapshot
        {"pct": 5.0, "usd": 0.1, "at": "2026-11-03T13:30:00-05:00"},
    ]
    summary = osp.summarise(readings)
    assert summary["session"] == "2026-11-03"
    assert summary["readings"] == 3
    assert summary["pct"] == 5.0
    assert summary["class"] == "OK"
    assert summary["asOf"] == "2026-11-03T13:30:00-05:00"


def test_window_is_ten_to_three_thirty_weekdays():
    assert not osp.in_sweep_window(datetime(2026, 11, 3, 9, 59, tzinfo=ET))
    assert osp.in_sweep_window(datetime(2026, 11, 3, 10, 0, tzinfo=ET))
    assert osp.in_sweep_window(datetime(2026, 11, 3, 15, 29, tzinfo=ET))
    assert not osp.in_sweep_window(datetime(2026, 11, 3, 15, 30, tzinfo=ET))
    assert not osp.in_sweep_window(datetime(2026, 11, 7, 12, 0, tzinfo=ET))   # Saturday


def _sweep(book, chains, *, now, clock=1_000_000.0, should_wait=lambda: False, paused=lambda: False,
           blocked=lambda e: False, sleeps=None):
    status = osp.SweepStatus()
    fetched = []

    def fetch(symbol):
        fetched.append(symbol)
        value = chains[symbol]
        if isinstance(value, Exception):
            raise value
        return value, "Schwab"

    count = osp.sweep_once(
        book, symbols=lambda: list(chains), fetch=fetch, should_wait=should_wait, paused=paused,
        status=status, now=lambda: now, clock=lambda: clock,
        sleep=(sleeps.append if sleeps is not None else (lambda s: None)), blocked=blocked,
    )
    return count, fetched, status


NOON = datetime(2026, 11, 3, 12, 0, tzinfo=ET)


def test_sweep_reads_paces_and_serves(tmp_path):
    book = osp.SpreadBook(tmp_path / "spreads.json")
    sleeps = []
    chains = {
        "AAPL": _chain(100, lambda e, s: (5.0, 5.05)),
        "BBAI": _chain(5, lambda e, s: (0.40, 0.55)),
        "DEAD": RuntimeError("Schwab: empty chain"),
    }
    count, fetched, status = _sweep(book, chains, now=NOON, sleeps=sleeps)
    assert count == 3 and fetched == ["AAPL", "BBAI", "DEAD"]
    # paced: a gap between every pair, none after the last
    assert sleeps == [osp.SWEEP_GAP_SECONDS] * 2
    assert status.done_today == 2 and status.failed_today == 1

    payload = osp.SpreadBook(tmp_path / "spreads.json").payload(["AAPL", "BBAI", "DEAD"])  # re-read from disk
    assert payload["spreads"]["AAPL"]["class"] == "Tight"
    assert payload["spreads"]["BBAI"]["class"] == "Avoid"
    assert "DEAD" not in payload["spreads"]
    assert "empty chain" in payload["errors"]["DEAD"]


def test_payload_drops_tickers_removed_from_the_watchlist(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    _sweep(book, {"AAPL": _chain(100, lambda e, s: (5.0, 5.05)), "EA": _chain(100, lambda e, s: (5.0, 5.05))}, now=NOON)
    assert set(book.payload(["AAPL"])["spreads"]) == {"AAPL"}


def test_a_fresh_reading_is_not_re_read_until_refresh_interval(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    chains = {"AAPL": _chain(100, lambda e, s: (5.0, 5.05))}
    _sweep(book, chains, now=NOON, clock=1_000_000.0)
    count, fetched, status = _sweep(book, chains, now=NOON, clock=1_000_000.0 + osp.REFRESH_SECONDS - 1)
    assert count == 0 and status.state == "Up to date"
    count, fetched, _ = _sweep(book, chains, now=NOON, clock=1_000_000.0 + osp.REFRESH_SECONDS)
    assert fetched == ["AAPL"]
    assert book.payload()["spreads"]["AAPL"]["readings"] == 2


def test_never_read_tickers_go_first(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    _sweep(book, {"AOLD": _chain(100, lambda e, s: (5.0, 5.05))}, now=NOON, clock=100.0)
    due = book.due(["AOLD", "ZNEW"], osp.REFRESH_SECONDS * 3)
    assert due == ["ZNEW", "AOLD"]


def test_outside_window_nothing_is_fetched(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    count, fetched, status = _sweep(book, {"AAPL": _chain(100, lambda e, s: (5.0, 5.05))},
                                    now=datetime(2026, 11, 3, 9, 45, tzinfo=ET))
    assert count == 0 and fetched == [] and status.state == "Waiting"


def test_pause_marker_stops_before_any_fetch(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    count, fetched, status = _sweep(book, {"AAPL": _chain(100, lambda e, s: (5.0, 5.05))}, now=NOON,
                                    paused=lambda: True)
    assert fetched == [] and status.state == "Paused"


def test_schwab_block_backs_off_and_stops_the_pass(tmp_path):
    class Blocked(RuntimeError):
        kind = "blocked"

    book = osp.SpreadBook(tmp_path / "s.json")
    sleeps = []
    chains = {"AAPL": Blocked("akamai"), "MSFT": _chain(100, lambda e, s: (5.0, 5.05))}
    count, fetched, status = _sweep(book, chains, now=NOON, sleeps=sleeps,
                                    blocked=lambda e: getattr(e, "kind", "") == "blocked")
    assert fetched == ["AAPL"]                      # MSFT never requested
    assert sleeps == [osp.BLOCKED_PAUSE_SECONDS]
    assert status.state == "Schwab blocked"
    assert book.due(["AAPL"], 1_000_000.0) == ["AAPL"]   # a block is not recorded as a reading


def test_yields_while_trader_is_active_but_not_forever(tmp_path):
    book = osp.SpreadBook(tmp_path / "s.json")
    sleeps = []
    _sweep(book, {"AAPL": _chain(100, lambda e, s: (5.0, 5.05))}, now=NOON, sleeps=sleeps,
           should_wait=lambda: True)
    assert sum(sleeps) == pytest.approx(120.0)
    assert "AAPL" in book.payload()["spreads"]


def test_corrupt_store_reads_as_empty(tmp_path):
    path = tmp_path / "s.json"
    path.write_text("{not json", encoding="utf-8")
    assert osp.SpreadBook(path).payload()["spreads"] == {}


def test_api_server_payload_filters_to_master_watchlist(monkeypatch, tmp_path):
    import types

    import api_server
    from config import settings

    monkeypatch.setattr(settings.scanner, "default_universe", ["AAPL", "TSLA"], raising=False)
    book = osp.SpreadBook(tmp_path / "s.json")
    _sweep(book, {"AAPL": _chain(100, lambda e, s: (5.0, 5.05)), "EA": _chain(100, lambda e, s: (5.0, 5.05))}, now=NOON)
    state = types.SimpleNamespace(_option_spread_book_obj=book)
    for name in ("_option_spread_book", "_option_spread_status", "option_spreads_payload"):
        setattr(state, name, types.MethodType(getattr(api_server.DashboardState, name), state))
    state._option_spread_watchlist = api_server.DashboardState._option_spread_watchlist
    payload = state.option_spreads_payload()
    assert set(payload["spreads"]) == {"AAPL"}
    assert payload["sweep"]["state"] == "Idle"
    assert payload["thresholds"]["wideMaxPct"] == 15.0
