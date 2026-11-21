"""Integration: Monthly Breakout / Monthly Breakdown lists over HTTP.

The real monthly refresh (momx.ema_touch.refresh -> momx.monthly_breakout)
runs on a faked Alpaca reply, and the lists are read and edited through the
real scanner worker (momx_worker.Handler).
Only the Alpaca call is faked. Three tickers:
  UPB  - long rise, then higher lows into a flat top, broken this month -> Breakout
  DNB  - long fall, then a range, broken below this month              -> Breakdown
  FLAT - long rise, then a range it has NOT broken                      -> neither
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from momx import ema_touch, feed
from worker_harness import running_worker

LISTS = "/api/momx-scanner/lists"
RISE = [30.0 + i for i in range(62)]
FALL = [150.0 - i for i in range(62)]
CLOSES = {
    "UPB": RISE + [100, 92, 85, 92, 99, 93, 89, 95, 100, 96, 93, 97, 99, 108],
    "DNB": FALL + [80, 88, 95, 88, 80, 88, 95, 88, 80, 88, 95, 88, 81, 70],
    "FLAT": RISE + [100, 92, 85, 92, 100, 93, 85, 93, 100, 92, 85, 93, 99, 96],
}


def _alpaca_month_bars(closes):
    bars = []
    for i, close in enumerate(closes):
        year, month = 2015 + i // 12, i % 12 + 1
        bars.append({"t": f"{year:04d}-{month:02d}-01T05:00:00Z", "l": close * 0.98, "h": close * 1.02, "c": close})
    return bars


class _Reply:
    status_code = 200

    def __init__(self, body):
        self._body = body

    def raise_for_status(self):
        return None

    def json(self):
        return self._body


@pytest.fixture
def breakout_scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_EMA_LISTS", "1")
    monkeypatch.setenv("AGX_MOMX_EMA_TOUCH_PATH", str(tmp_path / "ema_touch.json"))
    monkeypatch.setenv("AGX_MOMX_EMA_TF_PATH", str(tmp_path / "ema_tf.json"))
    monkeypatch.setattr(ema_touch, "start_refresher", lambda universe: None)
    monkeypatch.setattr(feed, "resolve_credentials", lambda: [SimpleNamespace(headers={})])
    ema_touch._CACHE.clear()
    ema_touch._CACHE.update({"symbols": {}})
    ema_touch._CACHE_MTIME = None
    ema_touch._WANTED.clear()
    monthly = {sym: _alpaca_month_bars(closes) for sym, closes in CLOSES.items()}

    def fake_get(url, params=None, headers=None, timeout=None):
        assert url == ema_touch.BARS_URL
        wanted = set((params or {}).get("symbols", "").split(","))
        bars = {s: b for s, b in monthly.items() if s in wanted} if params["timeframe"] == "1Month" else {}
        return _Reply({"bars": bars})

    monkeypatch.setattr("requests.get", fake_get)
    yield
    ema_touch._CACHE.clear()
    ema_touch._CACHE_MTIME = None


def _seed_watchlist(tmp_path, extra=None):
    lists = {"Watchlist": list(CLOSES)}
    lists.update(extra or {})
    (tmp_path / "worker" / "universe.json").write_text(json.dumps({"lists": lists}))


def test_refresh_fills_breakout_and_breakdown_lists_over_http(tmp_path, monkeypatch, breakout_scratch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _seed_watchlist(tmp_path)
        summary = ema_touch.refresh(list(CLOSES))
        assert (summary["breakout:up"], summary["breakout:down"]) == (1, 1)
        status, body = worker.get(LISTS)
        assert status == 200, body
        by_name = {item["name"]: item for item in body["lists"]}
        board_status, board = worker.get("/api/momx-scanner?list=Monthly%20Breakout")
    bull, bear = by_name["Monthly Breakout"], by_name["Monthly Breakdown"]
    assert (bull["count"], bear["count"]) == (1, 1)
    assert list(bull["breakouts"]) == ["UPB"] and list(bear["breakouts"]) == ["DNB"]
    up = bull["breakouts"]["UPB"]
    assert (up["pattern"], up["status"], up["direction"]) == ("BOTTOM", "live", "up")
    assert up["level"] == pytest.approx(102.0) and up["close"] == pytest.approx(108.0)
    assert bear["breakouts"]["DNB"]["pattern"] == "MIDDLE"
    assert bull["derived"] is True and bull["builtIn"] is True
    assert by_name["Watchlist"]["derived"] is False
    assert board_status == 200 and board["list"] == "Monthly Breakout"


def test_breakout_lists_refuse_edits_over_http(tmp_path, monkeypatch, breakout_scratch):
    with running_worker(tmp_path, monkeypatch) as worker:
        _seed_watchlist(tmp_path)
        ema_touch.refresh(list(CLOSES))
        add_status, add = worker.post("/api/momx-scanner/universe/add", {"text": "ZZZ", "list": "Monthly Breakout"})
        del_status, deleted = worker.post("/api/momx-scanner/lists/delete", {"list": "Monthly Breakdown"})
        status, body = worker.get(LISTS)
    # Refused, with the reason the panel shows.
    assert add_status == 400 and "chart-pattern break" in add.get("error", ""), add
    assert del_status == 400 and "chart-pattern break" in deleted.get("error", ""), deleted
    names = [item["name"] for item in body["lists"]]
    assert "Monthly Breakout" in names and "Monthly Breakdown" in names


def test_a_new_list_can_be_added_with_six_lists_showing(tmp_path, monkeypatch, breakout_scratch):
    # His screen: Mag7, Watchlist, 2 EMA lists, 2 own lists said "limit
    # reached". The computed lists are free, so a seventh-on-screen list saves.
    with running_worker(tmp_path, monkeypatch) as worker:
        _seed_watchlist(tmp_path, {"Liquid Movers": ["AAPL"], "Movers": ["NVDA"]})
        ema_touch.refresh(list(CLOSES))
        status, created = worker.post("/api/momx-scanner/lists/create", {"name": "Breakouts To Watch"})
        list_status, body = worker.get(LISTS)
    assert status == 200 and created.get("ok") is True, created
    assert list_status == 200
    own = [item for item in body["lists"] if not item["derived"]]
    assert [item["name"] for item in own] == ["Mag7", "Watchlist", "Liquid Movers", "Movers", "Breakouts To Watch"]
    assert body["maxLists"] == 6


def _alpaca_week_bars(closes):
    from datetime import date, timedelta

    start = date(2023, 1, 2)
    return [{"t": f"{start + timedelta(weeks=i)}T05:00:00Z", "l": c * 0.98, "h": c * 1.02, "c": c}
            for i, c in enumerate(closes)]


def test_weekly_dropdown_switches_the_list_over_http(tmp_path, monkeypatch, breakout_scratch):
    # 2026-11-20 "I see only monthly where is D/W": the same dropdown as the
    # EMA lists. Weekly bars carry the break; monthly ones are flat.
    weekly = {"WKB": _alpaca_week_bars(CLOSES["UPB"]), "UPB": _alpaca_week_bars(CLOSES["FLAT"])}
    monthly = {"UPB": _alpaca_month_bars(CLOSES["UPB"]), "WKB": _alpaca_month_bars(CLOSES["FLAT"])}

    def fake_get(url, params=None, headers=None, timeout=None):
        wanted = set((params or {}).get("symbols", "").split(","))
        source = {"1Month": monthly, "1Week": weekly}.get(params["timeframe"], {})
        return _Reply({"bars": {s: b for s, b in source.items() if s in wanted}})

    monkeypatch.setattr("requests.get", fake_get)
    with running_worker(tmp_path, monkeypatch) as worker:
        (tmp_path / "worker" / "universe.json").write_text(json.dumps({"lists": {"Watchlist": ["UPB", "WKB"]}}))
        ema_touch.refresh(["UPB", "WKB"])
        _, before = worker.get(LISTS)
        worker.post("/api/momx-scanner/lists/active", {"list": "Monthly Breakout"})
        status, switched = worker.post("/api/momx-scanner/ema-timeframe", {"breakout": "up", "timeframe": "Weekly"})
        _, after = worker.get(LISTS)
        bad_status, bad = worker.post("/api/momx-scanner/ema-timeframe", {"breakout": "up", "timeframe": "Hourly"})
        ema_status, ema = worker.post("/api/momx-scanner/ema-timeframe", {"length": 21, "timeframe": "Weekly"})
    first = {item["name"]: item for item in before["lists"]}
    assert list(first["Monthly Breakout"]["breakouts"]) == ["UPB"]
    assert first["Monthly Breakout"]["timeframes"] == ["Monthly", "Weekly", "Daily"]
    assert status == 200 and switched["ok"] is True, switched
    assert (switched["list"], switched["previous"], switched["active"]) == ("Weekly Breakout", "Monthly Breakout", "Weekly Breakout")
    by_name = {item["name"]: item for item in after["lists"]}
    assert "Monthly Breakout" not in by_name and "Monthly Breakdown" in by_name
    weekly_list = by_name["Weekly Breakout"]
    assert weekly_list["timeframe"] == "Weekly" and list(weekly_list["breakouts"]) == ["WKB"]
    assert weekly_list["breakouts"]["WKB"]["pattern"] == "BOTTOM"
    assert bad_status == 200 and bad["ok"] is False and "Timeframe" in bad["error"]
    # The EMA lists' dropdown still works through the same route.
    assert ema_status == 200 and ema["list"] == "21 EMA Touch Weekly"


def test_chart_pattern_route_answers_any_ticker_over_http(tmp_path, monkeypatch, breakout_scratch):
    # 2026-11-20: open any ticker's D/W/M chart and see which pattern broke.
    from momx import monthly_breakout

    monthly_breakout._ON_DEMAND.clear()
    asked = []

    def fake_get(url, params=None, headers=None, timeout=None):
        asked.append((params["symbols"], params["timeframe"]))
        if params["timeframe"] == "1Month":
            return _Reply({"bars": {"OFFL": _alpaca_month_bars(CLOSES["UPB"])}})
        if params["timeframe"] == "1Week":
            return _Reply({"bars": {"OFFL": _alpaca_week_bars(CLOSES["DNB"])}})
        return _Reply({"bars": {}})

    monkeypatch.setattr("requests.get", fake_get)
    with running_worker(tmp_path, monkeypatch) as worker:
        _seed_watchlist(tmp_path)
        status, body = worker.get("/api/momx-scanner/chart-pattern?symbol=offl")
        again_status, again = worker.get("/api/momx-scanner/chart-pattern?symbol=OFFL")
        bad_status, bad = worker.get("/api/momx-scanner/chart-pattern?symbol=")
    assert status == 200 and body["ok"] is True and body["symbol"] == "OFFL", body
    monthly, weekly = body["patterns"]["Monthly"], body["patterns"]["Weekly"]
    assert monthly["up"]["pattern"] == "BOTTOM" and monthly["up"]["status"] == "live"
    assert [line["role"] for line in monthly["up"]["lines"]] == ["resistance", "rising lows"]
    assert monthly["up"]["lines"][0]["toPrice"] == pytest.approx(102.0)
    # Each line's touch points come over HTTP for the chart's circles.
    assert all(len(line["touches"]) >= 2 for line in monthly["up"]["lines"])
    assert all(t["price"] == pytest.approx(102.0, rel=0.07) for t in monthly["up"]["lines"][0]["touches"])
    assert weekly["down"]["pattern"] == "MIDDLE" and weekly["down"]["lines"][0]["to"] == weekly["down"]["period"]
    assert body["patterns"]["Daily"] is None
    # Fetched once (3 timeframes), then kept.
    assert again_status == 200 and again == body and len(asked) == 3
    assert bad_status == 200 and bad["ok"] is False
    monthly_breakout._ON_DEMAND.clear()
