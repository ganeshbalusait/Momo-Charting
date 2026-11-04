"""Integration: the MTF CALL/PUT labels and D->M signals a chart is served
(2026-11-04 coverage), through GET /api/oi-finder-chart over real HTTP.

Three things bit before and are pinned here:
  * the server stopped sending mtfLiveSignalContexts (0/450, ac041cc);
  * a scanner CALLD missing from the chart (apply_scanner_ganesh_parity);
  * old-engine labels served as if current (they must arrive as "pending"
    while the recompute runs OFF the request thread).
The chart cache, its disk folder and every background job are scratch/fakes
(see ChartServer in test_cov_charts-core_chart_route_api.py).
"""
from __future__ import annotations

import importlib
import time

import pytest

from api_harness import api_server, running_api

_route = importlib.import_module("test_cov_charts-core_chart_route_api")
chart_payload = _route.chart_payload
NEWEST = _route.NEWEST


@pytest.fixture
def charts(monkeypatch, tmp_path):
    return _route.ChartServer(monkeypatch, tmp_path)


def test_a_ready_chart_serves_labels_by_timeframe_and_the_live_contexts(charts):
    charts.seed("CRWD")
    with running_api() as api:
        status, full = api.get("/api/oi-finder-chart?symbol=CRWD")
        status_slim, slim = api.get("/api/oi-finder-chart?symbol=CRWD&initial=true")
        status_deep, deep = api.get("/api/oi-finder-chart?symbol=CRWD&deep=true")
    assert (status, status_slim, status_deep) == (200, 200, 200)
    for body in (full, slim, deep):
        # The labels stay even in the slim first paint: a phone never pulls
        # the deep payload, and stripping them left phone charts bare.
        assert body["mtfSignals"] and body["mtfSignals"][0]["label"] == "CALL"
        assert body["mtfSignalsByTimeframe"]["5"], "per-timeframe labels missing"
        assert not body.get("mtfSignalsPending")
    # The 0/450 bug (ac041cc): the forming candle's label context must be
    # served on the full payload and the deep pull.
    for body in (full, deep):
        assert body["mtfLiveSignalContexts"], "mtfLiveSignalContexts dropped"
    # The D->M replay rides the full and deep payloads (never the slim paint).
    assert full["ganeshHigherTimeframeSignals"]["signals"][0]["label"] == "CALLD"
    assert deep["ganeshHigherTimeframeSignals"]["historyReady"] is True
    assert "ganeshHigherTimeframeSignals" not in slim


def test_the_scanner_d_to_m_call_is_overlaid_on_a_scanner_symbol(charts):
    symbol = "NVDA"
    assert symbol in api_server.PREMARKET_SCAN_SYMBOLS
    cached = chart_payload(symbol, base=190.0)
    cached["ganeshHigherTimeframeSignals"]["signals"] = []      # the chart lacks the CALLD
    charts.seed(symbol, cached)
    first_bar = cached["bars"][0]["time"]
    scanner_call = {"key": "NVDA-ganesh920-D-CALL", "time": cached["bars"][-1]["time"], "label": "CALLD"}
    ancient = {"key": "NVDA-ganesh920-D-CALL-2006", "time": first_bar - 400 * 86_400, "label": "CALLD"}
    floor = (symbol, int(time.time()) // 300)                   # a replay from the last 5 minutes
    charts.state._premarket_ganesh_cache[symbol] = (floor, {
        "schemaVersion": api_server.GANESH_SCHEMA_VERSION, "historyReady": True,
        "signals": [ancient, scanner_call],
    })
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={symbol}")
    assert status == 200, body
    served = body["ganeshHigherTimeframeSignals"]["signals"]
    assert [row["key"] for row in served] == ["NVDA-ganesh920-D-CALL"], "scanner CALLD missing or not windowed"
    # Served copy only: the cached chart payload is untouched.
    assert charts.state.oi_finder_chart_cache[symbol]["payload"]["ganeshHigherTimeframeSignals"]["signals"] == []


def test_a_stale_scanner_replay_never_outranks_the_chart(charts):
    symbol = "NVDA"
    cached = chart_payload(symbol, base=190.0)
    charts.seed(symbol, cached)
    old_floor = (symbol, (int(time.time()) - 3 * 3600) // 300)   # three hours old
    charts.state._premarket_ganesh_cache[symbol] = (old_floor, {
        "historyReady": True, "signals": [{"key": "stale", "time": cached["bars"][-1]["time"]}],
    })
    with running_api() as api:
        status, body = api.get(f"/api/oi-finder-chart?symbol={symbol}")
    assert status == 200
    assert [row["key"] for row in body["ganeshHigherTimeframeSignals"]["signals"]] == ["NVDA-ganesh48-D-CALL"]


def test_old_engine_labels_are_served_pending_and_recomputed_off_the_request_thread(charts):
    old = chart_payload("CRWD")
    old["mtfSignalMode"] = "tos_final_secondary_5m"            # the engine retired 2026-10-01
    charts.write_disk("CRWD", old)
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD&initial=true")
    assert status == 200, body
    assert body["diskCached"] is True and body["bars"], "candles must paint at once"
    assert body["mtfSignalsPending"] is True
    assert body["mtfSignals"] == [] and body["mtfSignalsByTimeframe"] == {}
    assert not body.get("mtfLiveSignalContexts")
    assert charts.label_heals == ["CRWD"]                       # one background recompute


def test_current_engine_labels_from_disk_are_served_as_is(charts):
    charts.write_disk("CRWD", chart_payload("CRWD"))
    with running_api() as api:
        status, body = api.get("/api/oi-finder-chart?symbol=CRWD")
    assert status == 200, body
    assert body["mtfSignals"] and not body.get("mtfSignalsPending")
    assert charts.label_heals == []
