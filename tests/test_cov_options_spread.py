"""Unit: the board's option Spread column book (momx/option_spread.py) -
what snapshot() hands the worker route. Chains are scratch files measured at
pinned ET times; the book is never the live artifacts one."""
from __future__ import annotations

import gzip
import json
import os
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from momx import option_spread

ET = ZoneInfo("America/New_York")
DAY = date(2026, 11, 4)                    # a Wednesday
WEEKLY, MONTHLY = "2026-11-06", "2026-11-20"   # nearest Friday, third Friday


def at(hour: int, minute: int) -> float:
    return datetime(DAY.year, DAY.month, DAY.day, hour, minute, tzinfo=ET).timestamp()


def chain(symbol: str, scanned: float, half_spread: float, spot: float = 100.0) -> dict:
    rows = []
    for expiry in (WEEKLY, MONTHLY):
        for side in ("CALL", "PUT"):
            for strike in (96, 98, 100, 102, 104):
                mid = 4.0
                rows.append({"side": side, "expiry": expiry, "strike": strike,
                             "bid": round(mid - half_spread, 4), "ask": round(mid + half_spread, 4)})
    return {"symbol": symbol, "optionQuoteSchemaVersion": 1, "underlyingPrice": spot,
            "scannedAt": datetime.fromtimestamp(scanned, ET).isoformat(), "selectedExpiryChainRows": rows}


def write_chain(directory: Path, symbol: str, scanned: float, half_spread: float) -> None:
    path = directory / f"{symbol}.json.gz"
    path.write_bytes(gzip.compress(json.dumps(chain(symbol, scanned, half_spread)).encode("utf-8")))
    os.utime(path, (scanned, scanned))


def book_with(tmp_path: Path) -> option_spread.SpreadBook:
    chains = tmp_path / "oi_chain_cache"
    chains.mkdir()
    book = option_spread.SpreadBook(chains, tmp_path / "momx_option_spread.json")
    write_chain(chains, "BBB", at(10, 5), half_spread=0.20)      # read early: stale by 11:30
    assert book.refresh(now=at(10, 6)) == 1
    write_chain(chains, "AAA", at(11, 20), half_spread=0.04)     # 2% of the mid: Tight
    write_chain(chains, "CCC", at(11, 22), half_spread=0.20)     # 10%: Wide
    assert book.refresh(now=at(11, 25)) == 2
    return book


def test_snapshot_returns_only_the_requested_fresh_symbols(tmp_path):
    book = book_with(tmp_path)
    snap = book.snapshot(["aaa", "BBB", "ZZZ"], now=at(11, 30))
    assert list(snap["spreads"]) == ["AAA"]                       # BBB stale, ZZZ unknown
    entry = snap["spreads"]["AAA"]
    assert (entry["cls"], entry["medPct"], entry["contracts"]) == ("Tight", 2.0, 20)
    assert entry["expiries"] == {"weekly": WEEKLY, "monthly": MONTHLY}
    assert entry["note"].startswith("Mid orders usually fill")
    assert "recent" not in entry                                  # the per-read history stays server-side
    assert (snap["held"], snap["inWindow"]) == (3, True)


def test_snapshot_without_symbols_lists_every_fresh_class(tmp_path):
    snap = book_with(tmp_path).snapshot(None, now=at(11, 30))
    assert {symbol: entry["cls"] for symbol, entry in snap["spreads"].items()} == {"AAA": "Tight", "CCC": "Wide"}


def test_after_the_window_the_days_last_class_stays_until_midnight_and_never_before_ten(tmp_path):
    book = book_with(tmp_path)
    after_close = book.snapshot(None, now=at(15, 45))
    assert set(after_close["spreads"]) == {"AAA", "BBB", "CCC"} and after_close["inWindow"] is False
    next_morning = datetime(2026, 11, 5, 9, 45, tzinfo=ET).timestamp()
    assert book.snapshot(None, now=next_morning)["spreads"] == {}


def test_a_restarted_worker_keeps_the_days_classes(tmp_path):
    book_with(tmp_path)
    reloaded = option_spread.SpreadBook(tmp_path / "oi_chain_cache", tmp_path / "momx_option_spread.json")
    assert reloaded.snapshot(["AAA"], now=at(11, 30))["spreads"]["AAA"]["cls"] == "Tight"
