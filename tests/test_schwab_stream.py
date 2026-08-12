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
