"""momx/option_spread.py - the board's Spread column (2026-11-03)."""
from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from momx import option_spread as osp

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 3)  # Tuesday; weekly Fri 11-06, monthly Fri 11-20


def ts(hour, minute, day=TODAY):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=ET).timestamp()


def leg(expiry, side, strike, bid, ask):
    return {"expiry": expiry, "side": side, "strike": strike, "bid": bid, "ask": ask, "symbol": f"X{expiry}{side}{strike}"}


def chain(spot=100.0, width=0.10, mid=2.0, expiries=("2026-11-04", "2026-11-06", "2026-11-13", "2026-11-20"), **over):
    rows = []
    for exp in expiries:
        for side in ("CALL", "PUT"):
            for strike in range(90, 111, 1):
                rows.append(leg(exp, side, float(strike), round(mid - width / 2, 4), round(mid + width / 2, 4)))
    return {"underlyingPrice": spot, "selectedExpiryChainRows": rows, "optionQuoteSchemaVersion": 1, **over}


def test_pick_expiries_weekly_and_monthly():
    exps = [date(2026, 11, d) for d in (3, 4, 6, 13, 20, 27)]
    assert osp.pick_expiries(exps, TODAY) == {"weekly": date(2026, 11, 6), "monthly": date(2026, 11, 20)}
    # Monthly week: the nearest Friday IS the monthly, so the weekly is the next Friday.
    assert osp.pick_expiries([date(2026, 11, 20), date(2026, 11, 27)], date(2026, 11, 17)) == {
        "weekly": date(2026, 11, 27), "monthly": date(2026, 11, 20)}
    # 0DTE never counts; a monthly-only chain has no weekly.
    assert osp.pick_expiries([date(2026, 11, 3), date(2026, 11, 20)], TODAY) == {"monthly": date(2026, 11, 20)}


def test_classes_and_cut_offs():
    assert osp.classify(0.029, False) == "Tight"
    assert osp.classify(0.03, False) == "OK"
    assert osp.classify(0.079, False) == "OK"
    assert osp.classify(0.08, False) == "Wide"
    assert osp.classify(0.15, False) == "Wide"
    assert osp.classify(0.151, False) == "Avoid"
    assert osp.classify(0.01, True) == "Avoid"


def test_measure_uses_weekly_monthly_atm_plus_minus_two_both_sides():
    got = osp.measure_chain(chain(width=0.10, mid=5.0), TODAY)  # 2% on a $5 option
    assert got["cls"] == "Tight"
    assert got["medPct"] == 2.0 and got["medUsd"] == 0.1
    assert got["contracts"] == 2 * 2 * 5  # two expiries x calls/puts x 5 strikes
    assert got["expiries"] == {"weekly": "2026-11-06", "monthly": "2026-11-20"}


def test_bid_zero_quotes_are_skipped():
    c = chain(width=0.10, mid=5.0)
    for r in c["selectedExpiryChainRows"]:
        if r["strike"] == 100.0:
            r["bid"] = 0.0
    got = osp.measure_chain(c, TODAY)
    assert got["skipped"] == 4 and got["contracts"] == 16
    # Every quote one-sided -> no class at all, never "Tight".
    for r in c["selectedExpiryChainRows"]:
        r["bid"] = 0
    assert osp.measure_chain(c, TODAY) is None


def test_cheap_atm_with_wide_dollar_spread_is_avoid():
    # $0.30 wide on a $2.50 option = 12% -> Wide by percent, Avoid by the ATM rule.
    got = osp.measure_chain(chain(width=0.30, mid=2.5), TODAY)
    assert got["cls"] == "Avoid" and got["atmRule"] is True
    # Same 12% on a $5 option: no ATM rule, just Wide.
    got = osp.measure_chain(chain(width=0.60, mid=5.0), TODAY)
    assert got["cls"] == "Wide" and got["atmRule"] is False


def test_window_and_freshness_rules():
    assert not osp.in_window(ts(9, 59)) and osp.in_window(ts(10, 0)) and not osp.in_window(ts(15, 30))
    assert not osp.in_window(ts(12, 0, date(2026, 11, 7)))  # Saturday
    entry = {"chainTs": ts(11, 0)}
    assert osp.is_fresh(entry, ts(11, 59))
    assert not osp.is_fresh(entry, ts(12, 1))          # over an hour old in the window
    assert osp.is_fresh(entry, ts(20, 0))              # after 15:30: the day's last class
    assert not osp.is_fresh(entry, ts(9, 0, date(2026, 11, 4)))  # next morning: gone
    assert not osp.is_fresh({}, ts(11, 0))


def _write(dir_path, symbol, payload, mtime):
    p = dir_path / f"{symbol}.json.gz"
    p.write_bytes(gzip.compress(json.dumps(payload).encode()))
    os.utime(p, (mtime, mtime))
    return p


def test_book_measures_only_window_chains_and_persists(tmp_path):
    cache = tmp_path / "oi_chain_cache"
    cache.mkdir()
    now = ts(11, 0)
    scanned = datetime.fromtimestamp(now - 300, ET).isoformat()
    _write(cache, "AAA", chain(width=0.10, mid=5.0, scannedAt=scanned), now - 300)
    _write(cache, "BBB", chain(width=1.0, mid=2.0, scannedAt=scanned), now - 300)
    # Read at 09:40 (before the window) -> never measured.
    _write(cache, "CCC", chain(scannedAt=datetime.fromtimestamp(ts(9, 40), ET).isoformat()), now - 300)
    store = tmp_path / "store.json"
    book = osp.SpreadBook(cache, store)
    assert book.refresh(now=ts(9, 50)) == 0              # outside the window: no pass
    assert book.refresh(now=now) == 2
    snap = book.snapshot(now=now)["spreads"]
    assert snap["AAA"]["cls"] == "Tight" and snap["BBB"]["cls"] == "Avoid" and "CCC" not in snap
    assert snap["AAA"]["note"]
    assert book.refresh(now=now + 60) == 0               # unchanged files are not re-read
    # A restart keeps the day.
    again = osp.SpreadBook(cache, store)
    assert again.snapshot(["aaa"], now=now)["spreads"].keys() == {"AAA"}
    assert again.snapshot(now=ts(9, 0, date(2026, 11, 4)))["spreads"] == {}


def test_book_skips_stale_and_broken_files(tmp_path):
    cache = tmp_path / "c"
    cache.mkdir()
    now = ts(12, 0)
    old = datetime.fromtimestamp(now - 3600, ET).isoformat()
    _write(cache, "OLD", chain(scannedAt=old), now - 60)  # file touched, chain itself an hour old
    (cache / "BAD.json.gz").write_bytes(b"not gzip")
    os.utime(cache / "BAD.json.gz", (now - 10, now - 10))
    book = osp.SpreadBook(cache, None)
    assert book.refresh(now=now) == 0
    assert book.snapshot(now=now)["spreads"] == {}


def test_class_is_median_of_the_days_last_three_reads(tmp_path):
    def entry(at, pct):
        return {"cls": "x", "chainTs": at, "pct": pct, "usd": 0.1, "atmRule": False, "medPct": 0, "medUsd": 0}

    merge = osp.SpreadBook._merge
    e = merge(None, entry(ts(10, 5), 0.02))
    e = merge(e, entry(ts(10, 35), 0.20))           # one wide read alone does not flip Tight -> Avoid
    e = merge(e, entry(ts(11, 5), 0.025))
    assert e["cls"] == "Tight" and e["reads"] == 3 and e["medPct"] == 2.5
    e = merge(e, entry(ts(11, 35), 0.20))           # oldest drops: reads 0.20, 0.025, 0.20
    assert e["cls"] == "Avoid" and e["reads"] == 3
    # Yesterday's reads never mix into today's class.
    e = merge(e, entry(ts(10, 5, date(2026, 11, 4)), 0.05))
    assert e["reads"] == 1 and e["cls"] == "OK"
    assert "pct" not in e and "usd" not in e
