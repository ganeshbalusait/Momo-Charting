"""Unit: the three chart payload shapes the browser depends on (2026-11-04
coverage) - the slim first paint, the one-time deep pull and the 30 s delta -
called directly on a bare DashboardState (no server, no broker, no clock).
"""
from __future__ import annotations

from api_server import DashboardState


def rows(count: int, step: int = 60, start: int = 1_793_700_000) -> list[dict]:
    return [{"time": start + index * step, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}
            for index in range(count)]


def state() -> DashboardState:
    return DashboardState.__new__(DashboardState)


def test_the_deep_pull_always_ends_history_loading_and_keeps_the_staleness_verdict():
    source = {
        "symbol": "SPY", "bars": rows(5), "studyBars": rows(3, 1_800), "historyLoading": True,
        "refreshing": True, "tapeStale": True, "staleTapeMessage": "SPY has not printed since ...",
        "error": "SPY has not printed since ...", "cacheAgeSeconds": 4.2, "someDiagnostic": [1, 2, 3],
    }
    deep = state()._deep_tapes_chart_payload(source)
    assert deep["historyLoading"] is False, "a True here re-downloads the deep tape every 30 s"
    assert deep["deep"] is True
    assert (deep["tapeStale"], deep["error"]) == (True, source["error"])
    assert deep["staleTapeMessage"] == source["staleTapeMessage"]
    assert "refreshing" not in deep and "someDiagnostic" not in deep and "cacheAgeSeconds" not in deep
    assert source["historyLoading"] is True, "the cached payload itself must not change"


def test_the_slim_first_paint_trims_every_tape_to_its_cap_and_marks_itself():
    s = state()
    source = {
        "bars": rows(s.OI_CHART_INITIAL_BAR_COUNT + 50),
        "dailyBars": rows(s.OI_CHART_INITIAL_DAILY_BAR_COUNT + 10, 86_400),
        "fineStudyBars": rows(s.OI_CHART_INITIAL_FINE_BAR_COUNT + 5, 300),
        "studyBars": rows(40, 1_800),
        "watchlistMtfStates": [{"x": 1}],
        "mtfSignals": [{"label": "CALL"}],
    }
    slim = s._slim_initial_chart_payload(source)
    assert len(slim["bars"]) == s.OI_CHART_INITIAL_BAR_COUNT
    assert slim["bars"][-1] == source["bars"][-1], "the newest candles are the ones kept"
    assert len(slim["dailyBars"]) == s.OI_CHART_INITIAL_DAILY_BAR_COUNT
    assert len(slim["fineStudyBars"]) == s.OI_CHART_INITIAL_FINE_BAR_COUNT
    assert "studyBars" not in slim and "watchlistMtfStates" not in slim
    assert slim["mtfSignals"] == source["mtfSignals"]
    assert (slim["initialSlim"], slim["historyLoading"], slim["refreshing"]) == (True, True, True)
    assert "initialStudySeed" not in slim


def test_a_slim_paint_of_an_empty_tape_is_returned_untouched():
    source = {"symbol": "ZZTOP", "bars": [], "warming": True}
    assert state()._slim_initial_chart_payload(source) is source


def test_the_delta_keeps_the_overlap_window_and_only_three_daily_candles():
    s = state()
    source = {"bars": rows(100), "fineStudyBars": rows(40, 300), "studyBars": rows(20, 1_800),
              "dailyBars": rows(30, 86_400), "updatedAt": "x", "mtfSignals": [1]}
    since = source["bars"][-1]["time"] - 300
    delta = s._delta_chart_payload(source, since)
    cutoff = since - s.OI_CHART_DELTA_OVERLAP_SECONDS
    assert delta["deltaSince"] == cutoff and delta["delta"] is True
    assert [row["time"] for row in delta["bars"]] == [row["time"] for row in source["bars"] if row["time"] >= cutoff]
    assert len(delta["bars"]) == 9
    assert all(row["time"] >= cutoff for row in delta["fineStudyBars"] + delta["studyBars"])
    assert delta["dailyBars"] == source["dailyBars"][-3:]
    assert (delta["updatedAt"], delta["mtfSignals"]) == ("x", [1])


def test_a_delta_asked_from_before_the_epoch_never_goes_negative():
    delta = state()._delta_chart_payload({"bars": rows(3)}, 10)
    assert delta["deltaSince"] == 0.0 and len(delta["bars"]) == 3
