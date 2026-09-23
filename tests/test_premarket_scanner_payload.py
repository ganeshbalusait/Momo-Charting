import threading
from types import SimpleNamespace

import api_server
from api_server import DashboardState
from premarket_scanner import PREMARKET_SCAN_SYMBOLS


class _FakeStream:
    def __init__(self, history=None, fail=False):
        self.history = history or {}
        self.fail = fail
        self.watched = []

    def chart_history(self, symbol):
        if self.fail:
            raise RuntimeError("stream down")
        return list(self.history.get(symbol, []))

    def watch(self, symbol, option_symbols=(), replace_options=False):
        self.watched.append(symbol)


def _state(cache):
    return SimpleNamespace(oi_finder_chart_lock=threading.RLock(), oi_finder_chart_cache=cache)


def _payload(state):
    return DashboardState.mag7_premarket_scanner_payload(state)


def test_cold_symbols_are_pending_not_zero_rows(monkeypatch):
    monkeypatch.setattr(api_server, "MARKET_STREAM", _FakeStream())
    bars = [{"time": 1_790_000_000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}]
    payload = _payload(_state({"NVDA": {"payload": {"bars": bars, "mtfSignals": []}}}))
    assert payload["readySymbols"] == ["NVDA"]
    assert set(payload["pendingSymbols"]) == set(PREMARKET_SCAN_SYMBOLS) - {"NVDA"}
    assert payload["status"] == "READY"
    assert payload["windowLabel"] == "6:00 AM - 9:30 AM ET"
    assert payload["rows"] == [] and payload["matchCount"] == 0
    for key in ("date", "timezone", "generatedAt", "message"):
        assert key in payload


def test_everything_cold_reports_warming_and_survives_a_dead_stream(monkeypatch):
    monkeypatch.setattr(api_server, "MARKET_STREAM", _FakeStream(fail=True))
    payload = _payload(_state({}))
    assert payload["status"] == "WARMING"
    assert payload["readySymbols"] == []
    assert payload["pendingSymbols"] == list(PREMARKET_SCAN_SYMBOLS)


def test_rows_sort_strongest_first(monkeypatch):
    monkeypatch.setattr(api_server, "MARKET_STREAM", _FakeStream())
    bars = [{"time": 1_790_000_000, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0}]
    rows = {
        "AAPL": {"symbol": "AAPL", "score": 1},
        "MSFT": {"symbol": "MSFT", "score": 4},
    }
    monkeypatch.setattr(api_server, "premarket_scan_row", lambda symbol, payload, now_et: rows.get(symbol))
    cache = {symbol: {"payload": {"bars": bars}} for symbol in ("AAPL", "MSFT")}
    payload = _payload(_state(cache))
    assert [row["symbol"] for row in payload["rows"]] == ["MSFT", "AAPL"]
    assert payload["matchCount"] == 2
