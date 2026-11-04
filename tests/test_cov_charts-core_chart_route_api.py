"""Integration: GET /api/oi-finder-chart over real HTTP (2026-11-04 coverage).

The chart a trader opens goes through four request shapes: the slim first
paint (initial=true[&initialStudy=true]), the one-time deep pull (deep=true),
the ~30 s tail reconcile (since=) and the tiny readiness poll
(historyStatus=true). Each test below speaks to the real api_server handler on
a scratch port, with the chart cache seeded in memory or on a scratch disk
folder. Nothing reaches Schwab: every background build/extend/label job is a
recorder, so the tests also prove the request thread never builds a chart.
"""
from __future__ import annotations

import gzip
import json
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from api_harness import RELAYED, api_server, running_api
from http_client import ApiClient

DashboardState = api_server.DashboardState
ET = ZoneInfo("America/New_York")
# A fixed tape end (Tue 2026-11-03 15:59 ET) - nothing below reads the clock.
NEWEST = int(datetime(2026, 11, 3, 15, 59, tzinfo=ET).timestamp())
DAY = 86_400
OWNER = "owner@example.com"


def _bar(epoch: int, price: float) -> dict:
    return {"time": int(epoch), "open": price, "high": price + 0.5, "low": price - 0.5,
            "close": price + 0.1, "volume": 1_000}


def chart_payload(symbol: str, base: float = 100.0) -> dict:
    """A complete, ready chart payload shaped like a real full build."""
    minute = [_bar(NEWEST - (1_199 - i) * 60, base + i * 0.001) for i in range(1_200)]
    fine_end = NEWEST - NEWEST % 300
    fine = [_bar(fine_end - (2_999 - i) * 300, base) for i in range(3_000)]
    day0 = int(datetime(2026, 11, 3, tzinfo=ET).timestamp())
    daily = [_bar(day0 - (399 - i) * DAY, base) for i in range(400)]
    # The real study tape: a daily archive, then a 30-minute tail.
    thirty_end = NEWEST - NEWEST % 1_800
    thirty = [_bar(thirty_end - (299 - i) * 1_800, base) for i in range(300)]
    archive = [_bar(thirty[0]["time"] - (60 - i) * DAY, base) for i in range(60)]
    signal = {"key": f"{symbol}-ganesh48-D-CALL", "time": minute[10]["time"], "label": "CALLD"}
    mtf = {"key": f"{symbol}-48-5m-CALL", "time": minute[-30]["time"], "label": "CALL", "timeframe": "5m"}
    return {
        "symbol": symbol,
        "source": "Schwab/TOS API",
        "bars": minute,
        "fineStudyBars": fine,
        "studyBars": archive + thirty,
        "dailyBars": daily,
        "fourHourCoverage": {"requestedYears": 20},
        "historyLoading": False,
        "ganeshHigherTimeframeSignals": {
            "schemaVersion": api_server.GANESH_SCHEMA_VERSION,
            "mode": api_server.GANESH_SIGNAL_MODE,
            "sourceAggregationMinutes": api_server.GANESH_SOURCE_AGGREGATION_MINUTES,
            "historyReady": True,
            "signals": [signal],
        },
        "mtfSignalMode": DashboardState.OI_CHART_MTF_ENGINE_MODE,
        "mtfSourceSessions": api_server.TOS_CHART_SESSION_DAYS,
        "mtfSignals": [mtf],
        "mtfSignalsByTimeframe": {"5": [mtf]},
        "mtfLiveSignalContexts": [{"timeframe": "5m", "label": "CALL", "time": minute[-1]["time"]}],
        "mtfSignalStates": {"5": {"trend": "bull"}},
        "updatedAt": "2026-11-03T15:59:30-05:00",
    }


class ChartServer:
    """Seeds the chart cache and records every job the route would start."""

    def __init__(self, monkeypatch, tmp_path):
        self.refreshes: list[tuple[str, bool]] = []
        self.extends: list[str] = []
        self.label_heals: list[str] = []
        self.disk_dir = tmp_path / "oi_chart_cache"
        self.disk_dir.mkdir()
        state = api_server.STATE
        self.state = state
        monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
        recorder = self

        def start_refresh(_self, target, full_history):
            recorder.refreshes.append((target, bool(full_history)))

        def submit_extend(_self, target):
            recorder.extends.append(target)
            done: Future = Future()
            done.set_result(False)
            return done

        def submit_heal(_self, target, source_payload):
            recorder.label_heals.append(target)
            done: Future = Future()
            done.set_result(False)
            return done

        monkeypatch.setattr(DashboardState, "_start_oi_finder_chart_refresh", start_refresh)
        monkeypatch.setattr(DashboardState, "_submit_chart_tail_extend", submit_extend)
        monkeypatch.setattr(DashboardState, "_submit_chart_label_heal", submit_heal)
        monkeypatch.setattr(state, "oi_finder_chart_cache", {})
        monkeypatch.setattr(state, "oi_finder_chart_disk_cache_dir", self.disk_dir)
        monkeypatch.setattr(state, "oi_finder_chart_refreshes", {})
        monkeypatch.setattr(state, "oi_finder_chart_recency_refreshes", {}, raising=False)
        monkeypatch.setattr(state, "oi_finder_recent_chart_symbols", OrderedDict(), raising=False)
        monkeypatch.setattr(state, "_premarket_ganesh_cache", {}, raising=False)
        monkeypatch.setattr(state, "chart_market_stream", None, raising=False)
        monkeypatch.setattr(state, "_chart_market_newest_bar_epoch", 0.0, raising=False)
        monkeypatch.setattr(state, "_chart_forced_extend_at", {}, raising=False)

    def seed(self, symbol: str, payload: dict | None = None, ready: bool = True) -> dict:
        payload = payload or chart_payload(symbol)
        self.state.oi_finder_chart_cache[symbol] = {
            "cached_at": time.monotonic(),
            "payload": payload,
            "history_ready": ready,
            # A full build finished this session: the entry is current
            # whatever the wall clock says.
            "built_at_epoch": time.time() if ready else 0.0,
            "disk_rescue_checked": True,
        }
        return payload

    def write_disk(self, symbol: str, payload: dict) -> None:
        stamped = {**payload, "payloadSchema": api_server.OI_FINDER_CHART_PAYLOAD_SCHEMA}
        (self.disk_dir / f"{symbol}.json.gz").write_bytes(gzip.compress(json.dumps(stamped).encode("utf-8")))


@pytest.fixture
def charts(monkeypatch, tmp_path):
    return ChartServer(monkeypatch, tmp_path)


def times(rows) -> list[int]:
    return [int(row["time"]) for row in rows]


# --------------------------------------------------------------------------
# charts-open-load
# --------------------------------------------------------------------------

def test_first_paint_is_slim_and_says_history_is_still_loading(charts):
    full = charts.seed("CRWD")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=crwd&initial=true")
    assert status == 200, body
    assert body["symbol"] == "CRWD"
    assert body["initialSlim"] is True and body["historyLoading"] is True
    assert len(body["bars"]) == DashboardState.OI_CHART_INITIAL_BAR_COUNT
    assert times(body["bars"]) == times(full["bars"][-DashboardState.OI_CHART_INITIAL_BAR_COUNT:])
    assert len(body["dailyBars"]) == DashboardState.OI_CHART_INITIAL_DAILY_BAR_COUNT
    assert len(body["fineStudyBars"]) == DashboardState.OI_CHART_INITIAL_FINE_BAR_COUNT
    # The deep tapes and the 20-year signal replay never ride the first paint.
    assert "studyBars" not in body and "ganeshHigherTimeframeSignals" not in body
    # ...but the few-KB MTF labels do (phones never pull the deep payload).
    assert body["mtfSignals"] and body["mtfSignalsByTimeframe"]
    # A ready, freshly cached chart answers from memory: no rebuild queued.
    assert charts.refreshes == []


def test_first_paint_with_study_seed_carries_only_the_intraday_study_tail(charts):
    full = charts.seed("CRWD")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD&initial=true&initialStudy=true")
    assert status == 200, body
    assert body["initialStudySeed"] is True
    seeded = times(body["studyBars"])
    # The daily archive prefix is dropped; only the 30-minute tail ships.
    assert seeded == times(full["studyBars"][60:])
    assert all(b - a == 1_800 for a, b in zip(seeded, seeded[1:]))


def test_the_30s_reconcile_is_a_tail_only_delta(charts):
    full = charts.seed("CRWD")
    since = full["bars"][-1]["time"] - 600
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol=CRWD&since={since}")
    assert status == 200, body
    cutoff = since - DashboardState.OI_CHART_DELTA_OVERLAP_SECONDS
    assert body["delta"] is True and body["deltaSince"] == cutoff
    assert times(body["bars"]) == [t for t in times(full["bars"]) if t >= cutoff]
    assert len(body["bars"]) == 14            # 10 new minutes + 3 overlap + the edge
    assert all(t >= cutoff for t in times(body["fineStudyBars"]))
    assert all(t >= cutoff for t in times(body["studyBars"]))
    assert len(body["dailyBars"]) == 3
    assert body["historyLoading"] is False


def test_the_deep_pull_returns_only_the_tapes_and_ends_history_loading(charts):
    full = charts.seed("CRWD")
    full["refreshing"] = True
    full["someLargeDiagnostic"] = ["x"] * 50
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD&deep=true")
    assert status == 200, body
    assert body["deep"] is True and body["historyLoading"] is False
    allowed = set(DashboardState.OI_CHART_DEEP_TAPE_KEYS) | {"deep", "historyLoading"}
    assert set(body) <= allowed, sorted(set(body) - allowed)
    assert len(body["studyBars"]) == len(full["studyBars"])
    assert len(body["bars"]) == len(full["bars"])
    # The staleness verdict rides every serve variant, including this whitelist.
    assert "tapeStale" in body


def test_a_cold_ticker_answers_warming_at_once_and_builds_off_the_request_thread(charts):
    started = time.perf_counter()
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=ZZTOP&initial=true")
    assert status == 200, body
    assert time.perf_counter() - started < 5.0
    assert body["warming"] is True and body["bars"] == [] and body["historyLoading"] is True
    assert charts.refreshes == [("ZZTOP", True)]   # queued once, in the background


def test_history_status_for_a_cold_ticker_is_tiny_and_starts_one_build(charts):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=ZZTOP&historyStatus=true")
    assert status == 200, body
    assert body == {"symbol": "ZZTOP", "historyReady": False, "refreshing": True,
                    "hasBars": False, "warming": True}
    assert charts.refreshes == [("ZZTOP", False)]


def test_history_status_for_a_ready_ticker_reports_ready_without_a_tape(charts):
    charts.seed("CRWD")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD&historyStatus=true")
    assert status == 200, body
    assert body["historyReady"] is True and body["hasBars"] is True and body["warming"] is False
    assert "bars" not in body
    assert charts.refreshes == []


def test_an_invalid_symbol_gets_a_readable_error_and_no_build(charts):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=BAD%24&initial=true")
    assert status == 200, body
    assert body["bars"] == [] and body["error"] == "Enter a valid ticker symbol."
    assert charts.refreshes == [] and charts.extends == []


def test_a_tunnel_visitor_without_a_session_is_refused(charts):
    charts.seed("CRWD")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD&initial=true", headers=RELAYED)
    assert status == 401
    assert body == {"error": "Sign in to continue."}


def test_a_large_chart_is_gzipped_over_the_wire_and_decodes_to_the_same_json(charts):
    charts.seed("CRWD")
    with running_api() as api:
        status, zipped = api.get("/api/oi-finder-chart?symbol=CRWD&deep=true",
                                 headers={"Accept-Encoding": "gzip"})
        encoding = api.last_headers.get("Content-Encoding")
        status_plain, plain = api.get("/api/oi-finder-chart?symbol=CRWD&deep=true",
                                      headers={"Accept-Encoding": "identity"})
        plain_encoding = api.last_headers.get("Content-Encoding")
    assert (status, status_plain) == (200, 200)
    assert encoding == "gzip" and plain_encoding is None
    for volatile in ("cacheAgeSeconds",):
        zipped.pop(volatile, None)
        plain.pop(volatile, None)
    assert zipped == plain


def test_client_timing_appends_one_capped_line_to_the_scratch_folder(monkeypatch, tmp_path):
    monkeypatch.setenv("LOCAL_AUTO_LOGIN_EMAIL", OWNER)
    monkeypatch.setattr(api_server, "ARTIFACTS_DIR", tmp_path)
    huge = {"symbol": "SPY", "kind": "open", "marks": {"m" + str(i): i for i in range(800)}, "bytes": 12}
    with running_api() as api:
        status, body = api.post("/api/client-timing", {"symbol": "SPY", "kind": "open", "marks": {"paint": 120}})
        status2, _ = api.post("/api/client-timing", huge)
    assert (status, status2) == (200, 200) and body == {"ok": True}
    lines = (tmp_path / "client_chart_timing.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["symbol"] == "SPY" and first["marks"] == {"paint": 120} and "at" in first
    assert len(lines[1]) == 2_000                  # truncated, never unbounded


# --------------------------------------------------------------------------
# charts-timeframes
# --------------------------------------------------------------------------

def test_a_disk_copy_is_served_at_30_minute_study_cadence_with_one_daily_bar_per_day(charts):
    payload = chart_payload("CRWD")
    # The 2026-09 corruption: a 1-minute study tape (a "30Min" flood) and a
    # trading day saved 2-3 times in dailyBars.
    flood_start = payload["studyBars"][-1]["time"] + 1_800
    payload["studyBars"] = payload["studyBars"] + [_bar(flood_start + i * 60, 101.0) for i in range(600)]
    last_day = payload["dailyBars"][-1]
    payload["dailyBars"] = payload["dailyBars"] + [{**last_day, "time": last_day["time"] + 360, "close": 99.0}]
    charts.write_disk("CRWD", payload)
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD")
    assert status == 200, body
    assert body["diskCached"] is True
    study = times(body["studyBars"])
    assert study == sorted(set(study)), "study tape must be ascending with no duplicate timestamps"
    tail = study[-20:]
    assert all((b - a) % 1_800 == 0 for a, b in zip(tail, tail[1:])), "the 1-minute flood was not folded"
    assert len(body["studyBars"]) < len(payload["studyBars"])
    sessions = [datetime.fromtimestamp(t, ET).date() for t in times(body["dailyBars"])]
    assert len(sessions) == len(set(sessions)), "one daily candle per trading day"
    assert len(body["dailyBars"]) == 400
    # 4H opens with real history: the 20-year coverage stamp is served.
    assert body["fourHourCoverage"]["requestedYears"] >= 20


def test_the_full_payload_feeds_every_timeframe_tape(charts):
    charts.seed("CRWD")
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD")
    assert status == 200, body
    for key in ("bars", "fineStudyBars", "studyBars", "dailyBars"):
        stamps = times(body[key])
        assert stamps and stamps == sorted(set(stamps)), key
    assert body["fourHourCoverage"]["requestedYears"] == 20
    assert body["historyLoading"] is False


# --------------------------------------------------------------------------
# charts-ticker-switch
# --------------------------------------------------------------------------

def test_back_to_back_tickers_each_get_their_own_payload(charts):
    charts.seed("AAPL", chart_payload("AAPL", base=230.0))
    charts.seed("NVDA", chart_payload("NVDA", base=190.0))
    with running_api() as api:
        _, aapl = api.get("/api/oi-finder-chart?symbol=AAPL&initial=true")
        _, nvda = api.get("/api/oi-finder-chart?symbol=NVDA&initial=true")
        _, aapl_again = api.get("/api/oi-finder-chart?symbol=AAPL&initial=true")
    assert (aapl["symbol"], nvda["symbol"], aapl_again["symbol"]) == ("AAPL", "NVDA", "AAPL")
    assert aapl["bars"][0]["open"] >= 230.0 and nvda["bars"][-1]["close"] < 200.0
    assert aapl_again["bars"] == aapl["bars"]


def test_a_hover_prefetch_never_claims_a_hot_set_slot_but_an_open_does(charts):
    charts.seed("CRWD")
    recents = charts.state.oi_finder_recent_chart_symbols
    with running_api() as api:
        status, _ = api.get("/api/oi-finder-chart?symbol=CRWD&prefetch=true")
        assert status == 200
        assert "CRWD" not in recents
        status, _ = api.get("/api/oi-finder-chart?symbol=CRWD&initial=true")
    assert status == 200
    assert list(recents) == ["CRWD"]


def test_a_cold_prefetch_starts_no_background_build(charts):
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=ZZTOP&prefetch=true")
    assert status == 200 and body["warming"] is True
    assert charts.refreshes == []


# --------------------------------------------------------------------------
# charts-layouts
# --------------------------------------------------------------------------

def test_six_panels_opening_at_once_each_get_their_own_chart(charts):
    symbols = ["SPY", "AAPL", "NVDA", "CRWD", "PLTR", "SHOP"]
    for index, symbol in enumerate(symbols):
        charts.seed(symbol, chart_payload(symbol, base=100.0 + index * 50))
    barrier = threading.Barrier(len(symbols))
    with running_api() as api:
        def open_panel(symbol):
            client = ApiClient(api.base_url)
            barrier.wait(timeout=10)
            return client.get(f"/api/oi-finder-chart?symbol={symbol}&initial=true")

        started = time.perf_counter()
        with ThreadPoolExecutor(max_workers=len(symbols)) as pool:
            replies = list(pool.map(open_panel, symbols))
        elapsed = time.perf_counter() - started
    assert [status for status, _ in replies] == [200] * 6
    assert [body["symbol"] for _, body in replies] == symbols
    for index, (_, body) in enumerate(replies):
        assert body["initialSlim"] is True
        assert 100.0 + index * 50 <= body["bars"][0]["open"] < 150.0 + index * 50
    assert elapsed < 15.0, f"six concurrent first paints took {elapsed:.1f}s"
