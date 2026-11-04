"""Integration (2026-11-04 coverage pass, group charts-studies, feature
charts-tape-freshness): the gateway (:3001 in production) in front of the real
api_server route, both over real HTTP on scratch ports.

The gateway answers a plain chart GET from its disk blob only when the blob is
fresh and the symbol is not one of the nine scanner symbols; everything shaped
(initial/since/deep/historyStatus/refresh), every scanner symbol and every
stale blob must reach the pipeline - otherwise the chart shows yesterday's
candles or misses the scanner's signal overlay. The blob folder is a scratch
folder and the gateway's clock is pinned, so nothing here depends on the live
artifacts/oi_chart_cache or on what time it is.
"""
from __future__ import annotations

import gzip
import importlib
import json
import socket
import threading
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import gateway
from api_harness import running_api
from http_client import ApiClient

payload_tests = importlib.import_module("test_cov_charts-studies_chart_payload_api")
ServeState = payload_tests.ServeState
_walk = payload_tests._walk

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 8, 13, 15, 0, tzinfo=ET)          # a Thursday, mid-session
FRESH_LAST = int(datetime(2026, 8, 13, 14, 59, tzinfo=ET).timestamp())
STALE_LAST = int(datetime(2026, 8, 12, 15, 59, tzinfo=ET).timestamp())  # yesterday's close


class PinnedClock(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.astimezone(tz)


def _tape(last_epoch: int, count: int = 20) -> list[dict]:
    return _walk(last_epoch - (count - 1) * 60, count, 60)


def _pipeline_payload(symbol: str, last_epoch: int) -> dict:
    return {"symbol": symbol, "bars": _tape(last_epoch), "historyLoading": False, "error": ""}


def _write_blob(folder, symbol: str, last_epoch: int) -> dict:
    blob = {"symbol": symbol, "servedBy": "disk-blob", "bars": _tape(last_epoch), "historyLoading": False}
    with gzip.open(folder / f"{symbol}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(blob, handle)
    return blob


@contextmanager
def running_gateway(pipeline_base: str):
    server = gateway.GatewayServer(("127.0.0.1", 0), gateway.GatewayHandler)
    original = gateway.GatewayHandler.pipeline_base
    gateway.GatewayHandler.pipeline_base = pipeline_base
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield ApiClient(f"http://127.0.0.1:{server.server_address[1]}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        gateway.GatewayHandler.pipeline_base = original


@pytest.fixture
def blobs(tmp_path, monkeypatch):
    folder = tmp_path / "oi_chart_cache"
    folder.mkdir()
    monkeypatch.setattr(gateway, "CHART_BLOBS", folder)
    monkeypatch.setattr(gateway, "datetime", PinnedClock)
    return folder


def _stats():
    with gateway.SERVE_STATS_LOCK:
        return dict(gateway.SERVE_STATS)


def _delta(before, after, key):
    return after.get(key, 0) - before.get(key, 0)


def test_a_fresh_blob_answers_a_plain_get_without_the_pipeline(blobs):
    blob = _write_blob(blobs, "EA", FRESH_LAST)
    state = ServeState({"EA": _pipeline_payload("EA", FRESH_LAST)}, market_newest_epoch=FRESH_LAST)
    with running_api(state) as api, running_gateway(api.base_url) as front:
        before = _stats()
        status, body = front.get("/api/oi-finder-chart?symbol=ea")
        after = _stats()
    assert status == 200, body
    assert body == blob
    assert _delta(before, after, "chartBlob") == 1 and _delta(before, after, "chartProxy") == 0
    assert state.refresh_calls == [], "the pipeline was never asked"


def test_a_scanner_symbol_is_always_proxied_even_with_a_fresh_blob(blobs):
    _write_blob(blobs, "NVDA", FRESH_LAST)
    state = ServeState({"NVDA": _pipeline_payload("NVDA", FRESH_LAST)}, market_newest_epoch=FRESH_LAST)
    with running_api(state) as api, running_gateway(api.base_url) as front:
        before = _stats()
        status, body = front.get("/api/oi-finder-chart?symbol=NVDA")
        after = _stats()
    assert status == 200, body
    assert "servedBy" not in body and body["symbol"] == "NVDA"
    assert "tapeStale" in body, "only the pipeline stamps the staleness verdict"
    assert _delta(before, after, "chartProxy") == 1 and _delta(before, after, "chartBlob") == 0


@pytest.mark.parametrize("shape", ["initial=true", f"since={FRESH_LAST - 600}", "deep=true", "historyStatus=true", "refresh=true"])
def test_every_shaped_chart_request_reaches_the_pipeline(blobs, shape):
    _write_blob(blobs, "EA", FRESH_LAST)
    state = ServeState({"EA": _pipeline_payload("EA", FRESH_LAST)}, market_newest_epoch=FRESH_LAST)
    with running_api(state) as api, running_gateway(api.base_url) as front:
        before = _stats()
        status, body = front.get(f"/api/oi-finder-chart?symbol=EA&{shape}")
        after = _stats()
    assert status == 200, body
    assert "servedBy" not in body, f"{shape} was answered from the full disk blob"
    assert _delta(before, after, "chartProxy") == 1 and _delta(before, after, "chartBlob") == 0


def test_a_blob_from_yesterday_is_proxied_and_the_pipeline_flags_the_stale_tape(blobs):
    _write_blob(blobs, "EA", STALE_LAST)
    # The pipeline holds the same old tape while the market printed today.
    state = ServeState({"EA": _pipeline_payload("EA", STALE_LAST)}, market_newest_epoch=FRESH_LAST)
    with running_api(state) as api, running_gateway(api.base_url) as front:
        before = _stats()
        status, body = front.get("/api/oi-finder-chart?symbol=EA")
        after = _stats()
    assert status == 200, body
    assert "servedBy" not in body, "a blob that does not reach today's session must not be served"
    assert _delta(before, after, "chartProxy") == 1
    assert body["tapeStale"] is True
    assert body["tapeNewestSession"] == "2026-08-12" and body["marketNewestSession"] == "2026-08-13"


def test_a_signed_out_tunnel_visitor_gets_the_pipelines_401_not_a_warming_answer(blobs):
    state = ServeState({"EA": _pipeline_payload("EA", FRESH_LAST)}, market_newest_epoch=FRESH_LAST)
    with running_api(state) as api, running_gateway(api.base_url) as front:
        status, body = front.get("/api/oi-finder-chart?symbol=EA&initial=true", headers={"X-Forwarded-For": "203.0.113.9"})
    assert status == 401
    assert body == {"error": "Sign in to continue."}


def test_pipeline_down_answers_the_warming_shape_the_chart_already_shows(blobs):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]
    with running_gateway(f"http://127.0.0.1:{dead_port}") as front:
        status, body = front.get("/api/oi-finder-chart?symbol=EA&initial=true")
    assert status == 200
    assert body == {"warming": True, "refreshing": True, "pipelineDown": True, "error": ""}
