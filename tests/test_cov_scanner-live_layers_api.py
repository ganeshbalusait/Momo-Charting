"""Integration: the board's live layers over real HTTP.

  * Live RVOL - the worker rescores the forming bar from the Schwab stream
    minutes (GET /api/momx-scanner/live-rvol) and serves a cell ONLY for a
    symbol whose minutes fully cover the bucket; anything else keeps the
    board's value.
  * Spread - the worker classes option bid/ask width from chain files api_server
    already saved (GET /api/momx-scanner/option-spreads); a class whose chain is
    no longer fresh is left out, so the column prints "-".
  * Live price - api_server leases at most _MOMX_FASTLANE_MAX symbols on the one
    shared Schwab stream (GET /api/momx-live-quotes) and says serving:false when
    the stream is down, so the board keeps its build prices quietly.
  * Stream tail - the worker's minute feed from api_server
    (GET /api/momx-stream-bars) never reaches back more than 6 hours.

The Schwab stream and the clock are fakes; no chain is fetched.
"""
from __future__ import annotations

import gzip
import json
import random
import time
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from api_harness import api_server, running_api
from momx import columns, feed, live_rvol, option_spread
from worker_harness import running_worker

ET = ZoneInfo("America/New_York")

# --------------------------------------------------------------------------- live RVOL

T0 = 1_793_721_600          # a 5-minute boundary: 2026-11-03 10:40 ET
SPAN = 300


def _minutes(seed: int, n_buckets: int = 60, forming: int = 3) -> list[dict]:
    rng = random.Random(seed)
    start = T0 - (n_buckets - 1) * SPAN
    out = []
    for b in range(n_buckets):
        for m in range(5 if b < n_buckets - 1 else forming):
            t = start + b * SPAN + m * 60
            o = 10 + rng.random()
            c = o + rng.uniform(-0.3, 0.3)
            out.append({"time": t, "open": o, "high": max(o, c) + 0.05, "low": min(o, c) - 0.05, "close": c,
                        "volume": float(rng.randint(100, 5000))})
    return out


def _fold(minutes: list[dict]) -> list[dict]:
    out: dict[int, dict] = {}
    for bar in minutes:
        k = bar["time"] - bar["time"] % SPAN
        held = out.get(k)
        if held is None:
            out[k] = dict(bar, time=k)
        else:
            held.update(high=max(held["high"], bar["high"]), low=min(held["low"], bar["low"]), close=bar["close"],
                        volume=held["volume"] + bar["volume"])
    return [out[k] for k in sorted(out)]


@pytest.fixture
def stream(monkeypatch):
    """Two symbols on the stream: AAA with every minute of the forming bucket,
    BBB missing one (a released-and-re-leased symbol)."""
    aaa, bbb = _minutes(3), _minutes(5)
    live_rvol._STORE.clear()
    live_rvol._CACHE.clear()
    # What the build saw: the tape up to the first minute of the forming bucket.
    live_rvol.remember("AAA", {"5m": columns.rvol_live_base(_fold([m for m in aaa if m["time"] < T0 + 60]))})
    live_rvol.remember("BBB", {"5m": columns.rvol_live_base(_fold([m for m in bbb if m["time"] < T0 + 60]))})
    held_a = [m for m in aaa if m["time"] >= T0 - 60]
    held_b = [m for m in bbb if m["time"] >= T0 - 60 and m["time"] != T0 + 60]
    newest = max(m["time"] for m in held_a)
    monkeypatch.setattr(feed, "STREAM_LIVE_ENABLED", True)
    monkeypatch.setattr(feed, "_stream_live_refresh", lambda: True)   # never polls the live api_server
    monkeypatch.setattr(feed, "_STREAM_LIVE", {"bars": {"AAA": {m["time"]: m for m in held_a},
                                                        "BBB": {m["time"]: m for m in held_b}},
                                               "dead": [], "at": 0, "ok_at": 1, "max_t": newest})
    monkeypatch.setattr(live_rvol, "time", SimpleNamespace(time=lambda: newest + 30, monotonic=time.monotonic))
    yield {"full_aaa": _fold(aaa)}
    live_rvol._STORE.clear()
    live_rvol._CACHE.clear()


def test_live_rvol_serves_only_the_fully_covered_symbol_and_matches_the_tos_script(tmp_path, monkeypatch, stream):
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/live-rvol?symbols=aaa,BBB,ZZZ")
    assert status == 200, body
    assert body["stream"] is True
    assert sorted(body["cells"]) == ["AAA"]                      # BBB has a hole, ZZZ has no base
    got = body["cells"]["AAA"]["5m"]
    want = columns.rvol_cell(stream["full_aaa"])                 # his column script on the full tape
    assert got["live"] is True
    assert (got["value"], got["bg"], got["fg"]) == (want["value"], want["bg"], want["fg"])
    assert got["bucketAt"] == T0


def test_a_frozen_stream_serves_no_live_cell_so_the_board_value_stays(tmp_path, monkeypatch, stream):
    newest = feed._STREAM_LIVE["max_t"]
    monkeypatch.setattr(live_rvol, "time", SimpleNamespace(
        time=lambda: newest + live_rvol.STREAM_STALE_SECONDS + 60, monotonic=time.monotonic))
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/live-rvol?symbols=AAA")
    assert status == 200, body
    assert body["stream"] is False and body["cells"] == {}


# --------------------------------------------------------------------------- Spread

def _chain(scanned: datetime, spot: float, half_spread: float) -> bytes:
    """A saved OI-finder chain: weekly (Fri 11-06) + monthly (Fri 11-20) expiries,
    five strikes either side, calls and puts, every quote 2 x half_spread wide."""
    rows = []
    for expiry in ("2026-11-06", "2026-11-20"):
        for side in ("CALL", "PUT"):
            for k in range(-5, 6):
                strike = spot + k
                mid = 5.0
                rows.append({"side": side, "expiry": expiry, "strike": strike,
                             "bid": round(mid - half_spread, 2), "ask": round(mid + half_spread, 2)})
    doc = {"optionQuoteSchemaVersion": option_spread.CHAIN_SCHEMA, "scannedAt": scanned.isoformat(),
           "underlyingPrice": spot, "selectedExpiryChainRows": rows}
    return gzip.compress(json.dumps(doc).encode("utf-8"))


def test_spreads_are_classed_from_saved_chains_and_a_stale_class_is_left_out(tmp_path, monkeypatch):
    chains = tmp_path / "oi_chain_cache"
    chains.mkdir()
    # 10:30 ET: both tickers' chains were just read.
    (chains / "AAA.json.gz").write_bytes(_chain(datetime(2026, 11, 3, 10, 30, tzinfo=ET), 100.0, 0.05))   # 2% -> Tight
    (chains / "BBB.json.gz").write_bytes(_chain(datetime(2026, 11, 3, 10, 30, tzinfo=ET), 50.0, 0.50))    # 20% -> Avoid
    book = option_spread.SpreadBook(chains, tmp_path / "momx_option_spread.json")
    assert book.refresh(now=datetime(2026, 11, 3, 10, 40, tzinfo=ET).timestamp()) == 2
    # 11:35 ET: AAA's chain is read again; BBB's is not.
    (chains / "AAA.json.gz").write_bytes(_chain(datetime(2026, 11, 3, 11, 35, tzinfo=ET), 100.0, 0.05))
    book._seen_mtime.pop("AAA", None)
    book.refresh(now=datetime(2026, 11, 3, 11, 40, tzinfo=ET).timestamp())
    monkeypatch.setattr(option_spread, "_BOOK", book)
    looked_at = datetime(2026, 11, 3, 11, 45, tzinfo=ET).timestamp()
    monkeypatch.setattr(option_spread, "time", SimpleNamespace(time=lambda: looked_at, sleep=time.sleep))

    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/option-spreads?symbols=aaa,BBB")
        assert status == 200, body
        assert sorted(body["spreads"]) == ["AAA"]                # BBB's chain is 75 min old: "-" on the board
        aaa = body["spreads"]["AAA"]
        assert aaa["cls"] == "Tight" and aaa["medPct"] == 2.0
        assert aaa["note"] == option_spread.CLASS_NOTE["Tight"]
        assert aaa["reads"] == 2                                 # median of the day's two reads
        assert "recent" not in aaa
        assert body["inWindow"] is True and body["held"] == 2

        # 10:41 - both fresh, the Avoid class reaches the screen too.
        monkeypatch.setattr(option_spread, "time", SimpleNamespace(
            time=lambda: datetime(2026, 11, 3, 10, 41, tzinfo=ET).timestamp(), sleep=time.sleep))
        status, body = worker.get("/api/momx-scanner/option-spreads")
    assert status == 200
    assert body["spreads"]["BBB"]["cls"] == "Avoid"


def test_before_ten_no_spread_class_is_shown_at_all(tmp_path, monkeypatch):
    book = option_spread.SpreadBook(tmp_path / "none")
    book._entries = {"AAA": {"cls": "Tight", "chainTs": datetime(2026, 11, 2, 15, 0, tzinfo=ET).timestamp(), "medPct": 1.0}}
    monkeypatch.setattr(option_spread, "_BOOK", book)
    monkeypatch.setattr(option_spread, "time", SimpleNamespace(
        time=lambda: datetime(2026, 11, 3, 9, 50, tzinfo=ET).timestamp(), sleep=time.sleep))
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get("/api/momx-scanner/option-spreads?symbols=AAA")
    assert status == 200 and body["spreads"] == {} and body["inWindow"] is False


# --------------------------------------------------------------------------- live quotes / stream tail

class FakeStream:
    def __init__(self):
        self.acquired: list[list[str]] = []
        self.released: list[list[str]] = []
        self.since: list[float] = []

    def acquire_equities(self, symbols):
        self.acquired.append(list(symbols))

    def release_equities(self, symbols):
        self.released.append(list(symbols))

    def latest_equities(self, symbols):
        return {s: {"last": 100.0 + i, "mark": 100.0 + i} for i, s in enumerate(symbols)}

    def chart_history_since(self, since):
        self.since.append(since)
        return [{"symbol": "AAA", "t": int(since) + 60, "c": 10.0}]


@pytest.fixture
def market_stream(monkeypatch):
    fake = FakeStream()
    monkeypatch.setattr(api_server, "MARKET_STREAM", fake)
    monkeypatch.setitem(api_server._MOMX_FASTLANE, "symbols", frozenset())
    return fake


def test_live_quotes_lease_at_most_the_cap_and_say_when_the_stream_is_not_serving(market_stream, monkeypatch):
    monkeypatch.setattr(api_server, "_schwab_market_data_healthy", lambda: False)
    wanted = [f"S{i:03d}" for i in range(api_server._MOMX_FASTLANE_MAX + 15)]
    with running_api() as api:
        status, body = api.get("/api/momx-live-quotes?symbols=" + ",".join(w.lower() for w in wanted))
    assert status == 200, body
    assert body["leased"] == api_server._MOMX_FASTLANE_MAX
    assert body["serving"] is False                              # the board keeps its build prices quietly
    assert len(market_stream.acquired) == 1 and len(market_stream.acquired[0]) == api_server._MOMX_FASTLANE_MAX
    assert set(body["quotes"]) == set(market_stream.acquired[0])
    assert set(body["quotes"]) <= set(wanted)                    # upper-cased, never a stray symbol


def test_a_smaller_match_set_releases_only_the_dropped_symbols(market_stream, monkeypatch):
    monkeypatch.setattr(api_server, "_schwab_market_data_healthy", lambda: True)
    with running_api() as api:
        api.get("/api/momx-live-quotes?symbols=AAA,BBB,CCC")
        status, body = api.get("/api/momx-live-quotes?symbols=AAA,BBB")
        again_status, again = api.get("/api/momx-live-quotes?symbols=AAA,BBB")
    assert status == 200 and body["serving"] is True and body["leased"] == 2
    assert market_stream.acquired == [["AAA", "BBB", "CCC"]]     # the stable repeat poll acquires nothing
    assert market_stream.released == [["CCC"]]
    assert again_status == 200 and sorted(again["quotes"]) == ["AAA", "BBB"]


def test_stream_bars_never_reach_back_more_than_six_hours(market_stream):
    before = time.time()
    with running_api() as api:
        status, body = api.get("/api/momx-stream-bars?since=0")
        junk_status, junk = api.get("/api/momx-stream-bars?since=yesterday")
        recent_status, recent = api.get(f"/api/momx-stream-bars?since={before - 120:.0f}")
    after = time.time()
    six_hours = 6 * 3600
    for answer in (body, junk):
        assert before - six_hours - 1 <= answer["since"] <= after - six_hours + 1
    assert status == junk_status == recent_status == 200
    assert recent["since"] == pytest.approx(round(before - 120), abs=1)     # a recent ask is kept as asked
    assert market_stream.since[0] == body["since"]
    assert body["bars"] == [{"symbol": "AAA", "t": int(body["since"]) + 60, "c": 10.0}]
