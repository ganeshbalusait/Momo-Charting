from __future__ import annotations

import asyncio
from unittest.mock import patch

from schwab_stream import SchwabMarketStream, event_stream_cursor


def test_wait_for_events_multiplexes_equities_and_option_underlyings() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._option_underlyings["AAPL  260101C00200000"] = "AAPL"
    stream._publish("equity", "NVDA", {"last": 180.0})
    stream._publish("equity", "AAPL", {"last": 205.0})
    stream._publish("option", "AAPL  260101C00200000", {"mark": 2.1})
    assert stream.latest_sequence() == 3

    cursor, events = stream.wait_for_events(0, ["AAPL", "MSFT"], timeout=0)

    assert cursor == 3
    assert [(event["type"], event["symbol"]) for event in events] == [
        ("equity", "AAPL"),
        ("option", "AAPL  260101C00200000"),
    ]


def test_stream_status_reports_receive_and_market_timing_without_quote_values() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._publish("equity", "AAPL", {
        "last": 205.25,
        "tradeTime": 1_786_373_880_000,
    })

    status = stream.status()
    latest = status["lastEvents"]["equity"]

    assert latest["symbol"] == "AAPL"
    assert latest["marketTimeMillis"] == 1_786_373_880_000
    assert latest["receivedAt"]
    assert "last" not in latest


def test_wait_for_events_advances_over_irrelevant_packets_without_replaying_buffer() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._publish("equity", "AAPL", {"last": 205.0})
    cursor, events = stream.wait_for_events(0, "AAPL", timeout=0)
    assert [event["symbol"] for event in events] == ["AAPL"]

    stream._publish("equity", "NVDA", {"last": 180.0})
    cursor, events = stream.wait_for_events(cursor, "AAPL", timeout=0)
    assert cursor == 2
    assert events == []

    stream._publish("chart", "AAPL", {"time": 1, "close": 206.0})
    cursor, events = stream.wait_for_events(cursor, "AAPL", timeout=0)
    assert cursor == 3
    assert [(event["type"], event["symbol"]) for event in events] == [("chart", "AAPL")]


def test_equity_leases_release_only_unpinned_symbols() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    with patch.object(stream, "start"):
        stream.watch("AAPL")
        first = stream.acquire_equities(["AAPL", "NVDA"])
        second = stream.acquire_equities(["NVDA", "MSFT"])

    stream.release_equities(first)
    assert stream._desired_equities == {"AAPL", "NVDA", "MSFT"}
    stream.release_equities(second)
    assert stream._desired_equities == {"AAPL"}


def test_replacing_option_chain_drops_the_previous_unpinned_underlying() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    with patch.object(stream, "start"):
        stream.watch("AAPL", ["AAPL  260101C00200000"], replace_options=True)
        stream.watch("NVDA", ["NVDA  260101C00200000"], replace_options=True)

    assert stream._desired_equities == {"NVDA"}
    assert stream._option_equity == "NVDA"


def test_apply_subscriptions_unsubscribes_released_equities() -> None:
    calls: list[tuple[str, tuple[str, ...]]] = []

    class FakeStream:
        async def level_one_equity_unsubs(self, symbols) -> None:
            calls.append(("quote", tuple(symbols)))

        async def chart_equity_unsubs(self, symbols) -> None:
            calls.append(("chart", tuple(symbols)))

    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._desired_equities = {"AAPL"}
    equities = {"AAPL", "NVDA"}
    asyncio.run(stream._apply_subscriptions(FakeStream(), equities, set()))

    assert calls == [("quote", ("NVDA",)), ("chart", ("NVDA",))]
    assert equities == {"AAPL"}


def test_event_stream_cursor_clamps_a_pre_restart_event_id() -> None:
    assert event_stream_cursor(None, 7) == 7
    assert event_stream_cursor("99", 7) == 7
    assert event_stream_cursor("4", 7) == 4
    assert event_stream_cursor("invalid", 7) == 7


def test_chart_history_persists_overnight_bars_across_stream_restart(tmp_path) -> None:
    history_path = tmp_path / "chart-history.json.gz"
    stream = SchwabMarketStream(
        client_factory=lambda: None,
        chart_history_path=history_path,
        chart_history_flush_seconds=0,
    )
    stream._handle_chart({
        "content": [
            {
                "key": "META",
                "CHART_TIME_MILLIS": 1_785_701_600_000,  # Sunday 21:00 ET
                "OPEN_PRICE": 561.0,
                "HIGH_PRICE": 562.0,
                "LOW_PRICE": 560.5,
                "CLOSE_PRICE": 561.5,
                "VOLUME": 10,
            },
            {
                "key": "META",
                "CHART_TIME_MILLIS": 1_785_716_000_000,  # Monday 01:00 ET
                "OPEN_PRICE": 562.0,
                "HIGH_PRICE": 563.0,
                "LOW_PRICE": 561.5,
                "CLOSE_PRICE": 562.5,
                "VOLUME": 20,
            },
        ]
    })
    stream.stop()

    restored = SchwabMarketStream(
        client_factory=lambda: None,
        chart_history_path=history_path,
    )

    assert [bar["time"] for bar in restored.chart_history("META")] == [
        1_785_701_600,
        1_785_716_000,
    ]
    assert restored.chart_history("META")[-1]["close"] == 562.5


def test_chart_history_replaces_a_forming_minute_without_double_counting_volume() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    first = {
        "content": [{
            "key": "META",
            "CHART_TIME_MILLIS": 1_785_701_600_000,
            "OPEN_PRICE": 561.0,
            "HIGH_PRICE": 562.0,
            "LOW_PRICE": 560.5,
            "CLOSE_PRICE": 561.5,
            "VOLUME": 10,
        }]
    }
    update = {
        "content": [{
            "key": "META",
            "CHART_TIME_MILLIS": 1_785_701_600_000,
            "OPEN_PRICE": 561.0,
            "HIGH_PRICE": 563.0,
            "LOW_PRICE": 560.0,
            "CLOSE_PRICE": 562.5,
            "VOLUME": 18,
        }]
    }

    stream._handle_chart(first)
    stream._handle_chart(update)

    assert stream.chart_history("META") == [{
        "time": 1_785_701_600,
        "open": 561.0,
        "high": 563.0,
        "low": 560.0,
        "close": 562.5,
        "volume": 18.0,
    }]


def test_latest_equities_returns_newest_quote_per_symbol_without_blocking() -> None:
    """The MomX fastlane polls this snapshot instead of tailing the deque."""
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._publish("equity", "NVDA", {"last": 180.0, "source": "schwab"})
    stream._publish("equity", "NVDA", {"last": 181.5, "source": "schwab"})
    stream._publish("equity", "AAPL", {"last": 205.0, "source": "schwab"})
    stream._publish("option", "AAPL  260101C00200000", {"mark": 2.1})  # never leaks in

    held = stream.latest_equities(["nvda", "AAPL", "MISSING"])
    assert set(held) == {"NVDA", "AAPL"}          # unknown symbol simply absent
    assert held["NVDA"]["last"] == 181.5           # newest quote wins
    assert "receivedAt" in held["NVDA"]

    # A copy, not a live reference: mutating the result cannot poison the cache.
    held["NVDA"]["last"] = 0.0
    assert stream.latest_equities(["NVDA"])["NVDA"]["last"] == 181.5

    # None -> everything cached; option events never created an equity entry.
    assert set(stream.latest_equities(None)) == {"NVDA", "AAPL"}


def test_apply_subscriptions_never_exceeds_schwabs_equity_limit() -> None:
    """345 requested froze the whole feed on 2026-09-30 (limit 300). Stream
    what fits, the chart's pinned symbol first, and report the cap."""
    import schwab_stream

    added: list[str] = []

    class FakeStream:
        async def level_one_equity_subs(self, symbols) -> None:
            added.extend(symbols)

        async def chart_equity_subs(self, symbols) -> None:
            pass

        async def level_one_equity_add(self, symbols) -> None:
            added.extend(symbols)

        async def chart_equity_add(self, symbols) -> None:
            pass

    stream = SchwabMarketStream(client_factory=lambda: None)
    wanted = {f"S{index:03d}" for index in range(345)} | {"ZZZ"}
    stream._desired_equities = set(wanted)
    stream._pinned_equities = {"ZZZ"}          # sorts last, must still be kept
    equities: set[str] = set()
    asyncio.run(stream._apply_subscriptions(FakeStream(), equities, set()))
    assert len(equities) == schwab_stream._MAX_EQUITY_SYMBOLS == len(added)
    assert "ZZZ" in equities
    assert "equity cap" in stream.status()["lastError"]


def test_partial_level_one_update_keeps_the_last_price() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._publish("equity", "CNQ", {"last": 40.5, "bid": 40.4, "ask": None})
    stream._publish("equity", "CNQ", {"last": None, "bid": 40.45, "ask": 40.6})
    quote = stream.latest_equities(["CNQ"])["CNQ"]
    assert quote["last"] == 40.5 and quote["bid"] == 40.45 and quote["ask"] == 40.6


def test_newer_chart_bar_supplies_last_when_quotes_go_quiet() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._publish("equity", "ZETA", {"last": 20.0, "bid": 19.9})
    stream._publish("chart", "ZETA", {"time": 1, "close": 21.5})
    quote = stream.latest_equities(["ZETA"])["ZETA"]
    assert quote["last"] == 21.5 and quote["lastSource"] == "chart"
    # A fresh trade print wins back over the older bar.
    stream._publish("equity", "ZETA", {"last": 21.6})
    quote = stream.latest_equities(["ZETA"])["ZETA"]
    assert quote["last"] == 21.6 and quote.get("lastSource") != "chart"
    # A symbol with only chart bars still gets a price.
    stream._publish("chart", "GGLL", {"time": 1, "close": 55.0})
    assert stream.latest_equities(["GGLL"])["GGLL"]["last"] == 55.0


def test_chart_history_save_runs_off_the_stream_loop(tmp_path) -> None:
    """The 10 MB history save ran inline in _handle_chart and froze every
    quote while it compressed (2026-09-30). It must return immediately."""
    import threading as _threading
    import time as _time

    stream = SchwabMarketStream(
        client_factory=lambda: None,
        chart_history_path=tmp_path / "hist.json.gz",
        chart_history_flush_seconds=0.0,
    )
    release = _threading.Event()
    written: list[dict] = []

    def slow_write(path, snapshot, dirty=None):
        release.wait(5)
        written.append(snapshot)

    stream._write_chart_history = slow_write
    stream._chart_history["AAPL"] = {1: {"time": 1, "close": 1.0}}
    started = _time.perf_counter()
    stream._flush_chart_history_if_due()
    stream._flush_chart_history_if_due()          # still running: not stacked
    assert _time.perf_counter() - started < 0.5
    release.set()
    stream._chart_history_writer.join(5)
    assert len(written) == 1 and "AAPL" in written[0]


def test_chart_history_since_returns_only_recent_compact_rows() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    stream._chart_history["AAPL"] = __import__("collections").OrderedDict(
        (t, {"time": t, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": 10.0})
        for t in (60, 120, 180, 240))
    stream._chart_history["MSFT"] = __import__("collections").OrderedDict(
        [(60, {"time": 60, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 1})])
    out = stream.chart_history_since(180)
    assert out == {"AAPL": [[180, 1.0, 2.0, 0.5, 1.5, 10.0], [240, 1.0, 2.0, 0.5, 1.5, 10.0]]}
    assert stream.chart_history_since(0, symbols=["msft"]) == {"MSFT": [[60, 1, 1, 1, 1, 1]]}


def test_chart_history_save_encodes_in_one_go() -> None:
    """2026-10-06 09:01 ET: json.dump into a gzip text stream ran the pure-Python
    encoder in small chunks - ~11% of api_server GIL time every 30 s."""
    import inspect

    source = inspect.getsource(SchwabMarketStream._write_chart_history)
    assert 'gzip.open(temporary_path, "wt"' not in source
    assert "json.dumps(" in source


def test_chart_history_save_encodes_one_symbol_at_a_time(tmp_path) -> None:
    """2026-10-06 18:31 ET: one json.dumps of the 82.6 MB snapshot held the GIL
    ~6 s every 30 s and froze every request (TSLA open 9.7 s). The writer now
    encodes per symbol; the file must still equal json.dumps of the dict."""
    import gzip
    import inspect
    import json

    source = inspect.getsource(SchwabMarketStream._write_chart_history)
    assert '{"version": 1, "symbols": snapshot}' not in source
    stream = SchwabMarketStream(client_factory=lambda: None)
    snapshot = {
        "META": [{"time": 1, "close": 1.5}, {"time": 2, "close": 2.5}],
        "BRK/B": [{"time": 3, "close": 3.0}],
    }
    path = tmp_path / "history.json.gz"
    stream._write_chart_history(path, snapshot)
    written = gzip.decompress(path.read_bytes()).decode("utf-8")
    assert written == json.dumps({"version": 1, "symbols": snapshot}, separators=(",", ":"))


def test_recent_chart_history_keeps_late_corrections_and_preserves_archive() -> None:
    stream = SchwabMarketStream(client_factory=lambda: None)
    for minute in (120, 180, 60):
        stream._handle_chart({"content": [{"key": "AAPL", "CHART_TIME_MILLIS": minute * 1000,
            "OPEN_PRICE": 10, "HIGH_PRICE": 11, "LOW_PRICE": 9, "CLOSE_PRICE": 10,
            "VOLUME": minute}]})
    recent = stream.chart_history("aapl", since=120)
    assert [bar["time"] for bar in recent] == [120, 180]
    assert len(stream.chart_history("AAPL")) == 3
    recent[0]["close"] = 99
    assert stream.chart_history("AAPL", since=120)[0]["close"] == 10
    assert stream.chart_history("MISSING", since=120) == []


def test_chart_history_save_reencodes_only_changed_symbols(tmp_path) -> None:
    import gzip
    import json

    path = tmp_path / "history.json.gz"
    stream = SchwabMarketStream(
        client_factory=lambda: None, chart_history_path=path, chart_history_flush_seconds=0,
    )
    for key, millis, price in (("META", 1_785_701_600_000, 1.0), ("AAPL", 1_785_701_600_000, 2.0)):
        stream._handle_chart({"content": [{"key": key, "CHART_TIME_MILLIS": millis,
                                           "OPEN_PRICE": price, "HIGH_PRICE": price,
                                           "LOW_PRICE": price, "CLOSE_PRICE": price, "VOLUME": 1}]})
    stream.stop()
    encoded_meta = stream._chart_history_encoded["META"]
    stream._handle_chart({"content": [{"key": "AAPL", "CHART_TIME_MILLIS": 1_785_701_660_000,
                                       "OPEN_PRICE": 3, "HIGH_PRICE": 3, "LOW_PRICE": 3,
                                       "CLOSE_PRICE": 3, "VOLUME": 2}]})
    stream.stop()
    assert stream._chart_history_encoded["META"] is encoded_meta   # untouched symbol reused
    written = json.loads(gzip.decompress(path.read_bytes()))
    assert [bar["close"] for bar in written["symbols"]["AAPL"]] == [2.0, 3.0]
    full = {key: stream.chart_history(key) for key in ("META", "AAPL")}
    assert written["symbols"] == full


def test_chart_history_keeps_only_the_recent_days(tmp_path):
    # 2026-11-03: 2.66M bars back to August were re-encoded on every save and
    # the stream thread queued behind the writer (candles 6+ min late).
    stream = SchwabMarketStream(chart_history_path=tmp_path / "h.json.gz", chart_history_flush_seconds=1e9,
                                chart_history_max_age_seconds=2 * 86400)
    day = 86400
    for t in (1_785_000_000, 1_785_000_000 + day // 2, 1_785_000_000 + 3 * day):
        stream._record_chart_bar("META", {"time": t, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 5})
    assert [bar["time"] for bar in stream.chart_history("META")] == [1_785_000_000 + 3 * day]
