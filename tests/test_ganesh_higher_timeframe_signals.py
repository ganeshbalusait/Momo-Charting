from __future__ import annotations

import pandas as pd

import ganesh_higher_timeframe_signals as ganesh
from ganesh_higher_timeframe_signals import (
    GANESH_MACD_TIMEFRAMES,
    SCHEMA_VERSION,
    SIGNAL_MODE,
    build_ganesh_higher_timeframe_signal_payload,
    build_ganesh_primary_bars,
    calculate_ganesh_higher_timeframe_signals,
)


def _unix(value: str) -> int:
    return int(pd.Timestamp(value).timestamp())


def _falling_daily_history(count: int = 120, start: str = "2025-09-01") -> list[dict]:
    start_date = pd.Timestamp(f"{start}T00:00:00Z")
    bars = []
    for index in range(count):
        stamp = start_date + pd.Timedelta(days=index)
        close = 150.0 - index
        bars.append(
            {
                "time": int(stamp.timestamp()) + 20 * 60 * 60,
                "date": stamp.strftime("%Y-%m-%d"),
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100,
            }
        )
    return bars


def _trading_daily_history(
    count: int = 2000,
    end: str = "2026-07-30",
    *,
    close: float = 3_000.0,
) -> list[dict]:
    sessions = pd.bdate_range(end=end, periods=count, tz="UTC")
    bars = []
    for index, stamp in enumerate(sessions):
        bars.append(
            {
                "time": int(stamp.timestamp()) + 20 * 60 * 60,
                "date": stamp.strftime("%Y-%m-%d"),
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100,
            }
        )
    return bars


def _minute_bars(closes: list[float], date: str = "2026-03-02") -> list[dict]:
    start = pd.Timestamp(f"{date}T14:30:00Z")
    return [
        {
            "time": int((start + pd.Timedelta(minutes=index)).timestamp()),
            "open": close - 1.0,
            "high": close + 1.0,
            "low": close - 2.0,
            "close": close,
            "volume": 100 + index,
        }
        for index, close in enumerate(closes)
    ]


def _frame(rows: list[dict]) -> pd.DataFrame:
    frame = pd.DataFrame(rows).copy()
    if "time" in frame:
        frame["timestamp"] = pd.to_datetime(frame.pop("time"), unit="s", utc=True).dt.tz_convert(
            "America/New_York"
        )
    return frame


def test_payload_has_a_stable_versioned_empty_contract() -> None:
    payload = build_ganesh_higher_timeframe_signal_payload(
        pd.DataFrame(),
        pd.DataFrame(),
        pd.DataFrame(),
    )

    assert payload == {
        "schemaVersion": SCHEMA_VERSION,
        "mode": SIGNAL_MODE,
        "sourceAggregationMinutes": 240,
        "historyReady": False,
        "signals": [],
    }


def test_primary_bars_use_tos_midnight_central_boundaries_and_live_cutover() -> None:
    study = _frame(
        [
            {
                "time": _unix("2026-07-31T12:55:00Z"),
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100.5,
                "volume": 10,
            },
            # The cached row at the live start is discarded in favor of live.
            {
                "time": _unix("2026-07-31T13:00:00Z"),
                "open": 1,
                "high": 999,
                "low": 1,
                "close": 500,
                "volume": 5000,
            },
        ]
    )
    live = _frame(
        [
            {
                "time": _unix("2026-07-31T13:00:00Z"),
                "open": 100.5,
                "high": 103,
                "low": 100,
                "close": 102,
                "volume": 20,
            },
            {
                "time": _unix("2026-07-31T16:59:00Z"),
                "open": 102,
                "high": 104,
                "low": 101,
                "close": 103,
                "volume": 30,
            },
            {
                "time": _unix("2026-07-31T17:00:00Z"),
                "open": 103,
                "high": 105,
                "low": 102,
                "close": 104,
                "volume": 40,
            },
        ]
    )

    primary = build_ganesh_primary_bars(study, live)

    assert [bar["time"] for bar in primary] == [
        _unix("2026-07-31T09:00:00Z"),  # 05:00 ET
        _unix("2026-07-31T13:00:00Z"),  # 09:00 ET
        _unix("2026-07-31T17:00:00Z"),  # 13:00 ET
    ]
    assert primary[1]["high"] == 104
    assert primary[1]["low"] == 100
    assert primary[1]["close"] == 103
    assert primary[1]["volume"] == 50


def test_four_hour_rth_source_uses_official_daily_close_at_1600() -> None:
    daily_close = {"2026-07-30": 235.5}
    source = lambda value, close: ganesh._higher_timeframe_source_bar(
        {
            "time": _unix(value),
            "open": close,
            "high": close,
            "low": close,
            "close": close,
        },
        daily_close,
        240,
    )

    assert source("2026-07-30T05:00:00Z", 234) is None  # 01:00 ET
    assert source("2026-07-30T13:00:00Z", 238.2)["sourceClose"] == 238.2  # 09:00 ET
    assert source("2026-07-30T17:00:00Z", 254.54)["sourceClose"] == 235.5  # 13:00 ET
    assert source("2026-07-30T21:00:00Z", 258) is None  # 17:00 ET


def test_4x8_keeps_only_a_confirmed_persistent_source_candle_cross() -> None:
    daily = _trading_daily_history(end="2026-07-30", close=3_000.0)
    calls = calculate_ganesh_higher_timeframe_signals(
        _minute_bars([150, 180, 210, 250, 1_000_000], date="2026-07-31"),
        daily,
        aggregation_minutes=5,
    )
    reversed_cross = calculate_ganesh_higher_timeframe_signals(
        _minute_bars([300, 10], date="2026-07-31"),
        daily,
        aggregation_minutes=5,
    )

    matching = [
        signal
        for signal in calls
        if signal["family"] == "ganesh48"
        and signal["timeframe"] == "D"
        and signal["direction"] == "CALL"
    ]
    assert [(signal["label"], signal["time"]) for signal in matching] == [
        ("CALLD", _minute_bars([150], date="2026-07-31")[0]["time"])
    ]
    assert not any(
        signal["family"] == "ganesh48"
        and signal["timeframe"] == "D"
        and signal["direction"] == "CALL"
        for signal in reversed_cross
    )


def test_9x20_uses_first_chart_bar_gate_and_daily_atr_multiplier() -> None:
    bars = _minute_bars([250, 40, 260, 1_000_000])
    signals = calculate_ganesh_higher_timeframe_signals(
        bars,
        _falling_daily_history(),
        aggregation_minutes=5,
    )
    matching = [
        signal
        for signal in signals
        if signal["family"] == "ganesh920"
        and signal["timeframe"] == "D"
        and signal["direction"] == "CALL"
    ]

    assert len(matching) == 1
    assert matching[0]["time"] == bars[0]["time"]
    assert matching[0]["label"] == "CALLD"
    assert matching[0]["atrMultiplier"] == 0.2


def test_macd_multiday_groups_are_right_aligned_to_the_latest_chart_session() -> None:
    daily = [
        {"date": date_key, "close": 100 + index}
        for index, date_key in enumerate(
            ("2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31")
        )
    ]
    groups = ganesh._macd_group_keys_by_date(daily, ["2026-07-31"])

    assert groups["2D"]["2026-07-30"] == groups["2D"]["2026-07-31"]
    assert len({groups["3D"][value] for value in ("2026-07-29", "2026-07-30", "2026-07-31")}) == 1
    assert len({groups["4D"][value] for value in ("2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31")}) == 1

    weekend = ganesh._macd_group_keys_by_date(
        [{"date": "2026-07-31", "close": 100}, {"date": "2026-08-03", "close": 101}],
        ["2026-08-03"],
    )
    assert weekend["2D"]["2026-07-31"] == weekend["2D"]["2026-08-03"]


def test_tos_live_calendar_aggregation_uses_the_1969_12_30_phase() -> None:
    friday = {
        timeframe: ganesh._timeframe_group_key(timeframe, "2026-07-31")
        for timeframe in ("2D", "3D", "4D")
    }
    sunday = {
        timeframe: ganesh._timeframe_group_key(timeframe, "2026-08-02")
        for timeframe in ("2D", "3D", "4D")
    }
    monday = {
        timeframe: ganesh._timeframe_group_key(timeframe, "2026-08-03")
        for timeframe in ("2D", "3D", "4D")
    }

    assert friday["2D"] != sunday["2D"] != monday["2D"]
    assert friday["3D"] == sunday["3D"]
    assert sunday["3D"] != monday["3D"]
    assert friday["4D"] != sunday["4D"]
    assert sunday["4D"] == monday["4D"]


def test_tos_macd_3d_uses_its_own_literal_source_anchor() -> None:
    groups = ganesh._macd_group_keys_by_date(
        [
            {"date": "2026-08-03", "close": 100},
            {"date": "2026-08-04", "close": 101},
            {"date": "2026-08-05", "close": 102},
        ],
        ["2026-08-03", "2026-08-04", "2026-08-05"],
        literal_timeframes=frozenset({"3D"}),
    )

    # The INTC TOS 4H reference rolls MACD's 3D carrier on Tuesday, rather
    # than keeping Monday/Tuesday/Wednesday in one EMA-style 3D bucket.
    assert groups["3D"]["2026-08-03"] != groups["3D"]["2026-08-04"]
    assert groups["3D"]["2026-08-04"] == groups["3D"]["2026-08-05"]


def test_tos_sunday_exto_only_continues_a_period_that_existed_friday() -> None:
    assert not ganesh._tos_overnight_group_continues_from_prior_session(
        "D", "2026-08-02"
    )
    assert not ganesh._tos_overnight_group_continues_from_prior_session(
        "2D", "2026-08-02"
    )
    assert ganesh._tos_overnight_group_continues_from_prior_session(
        "3D", "2026-08-02"
    )
    assert not ganesh._tos_overnight_group_continues_from_prior_session(
        "4D", "2026-08-02"
    )
    assert ganesh._tos_overnight_group_continues_from_prior_session(
        "W", "2026-08-02"
    )
    assert not ganesh._tos_overnight_group_continues_from_prior_session(
        "M", "2026-08-02"
    )


def test_live_session_does_not_repaint_completed_multiday_groups() -> None:
    daily = [
        {"date": date_key, "close": 100 + index}
        for index, date_key in enumerate(
            (
                "2026-07-27",
                "2026-07-28",
                "2026-07-29",
                "2026-07-30",
                "2026-07-31",
                "2026-08-03",
            )
        )
    ]
    live_groups = ganesh._macd_group_keys_by_date(
        daily,
        ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03"],
        "2026-08-03",
    )
    closed_groups = ganesh._macd_group_keys_by_date(
        daily,
        ["2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31", "2026-08-03"],
    )

    assert live_groups == closed_groups


def test_market_close_never_relocates_signals_from_prior_sessions(monkeypatch) -> None:
    monkeypatch.setattr(
        ganesh,
        "_FAMILY_PREFETCH_BUCKETS",
        {"ganesh48": 0, "ganesh920": 0, "ganeshMacd": 0},
    )
    dates = pd.bdate_range("2026-06-01", "2026-08-03")
    daily = [
        {
            "time": _unix(f"{stamp.date().isoformat()}T20:00:00Z"),
            "date": stamp.date().isoformat(),
            "open": 100 + ((index % 9) - 4) * 3,
            "high": 104 + ((index % 9) - 4) * 3,
            "low": 96 + ((index % 9) - 4) * 3,
            "close": 100 + ((index % 9) - 4) * 3,
            "volume": 100,
        }
        for index, stamp in enumerate(dates)
    ]
    primary_dates = [
        stamp.date().isoformat()
        for stamp in pd.bdate_range("2026-07-13", "2026-08-03")
    ]
    primary = [
        {
            "time": _unix(f"{date_key}T{hour}:00:00Z"),
            "open": close,
            "high": close + 1,
            "low": close - 1,
            "close": close,
            "volume": 100,
        }
        for index, date_key in enumerate(primary_dates)
        for hour, close in (
            ("05", 100 + ((index % 7) - 3) * 4),
            ("09", 102 + ((index % 7) - 3) * 4),
        )
    ]

    live, _ = ganesh._calculate_signal_result(
        primary,
        daily,
        incomplete_session_date="2026-08-03",
    )
    closed, _ = ganesh._calculate_signal_result(
        primary,
        daily,
        incomplete_session_date="",
    )
    monday_open = _unix("2026-08-03T05:00:00Z")
    identity = lambda signal: (
        signal["family"],
        signal["timeframe"],
        signal["direction"],
        signal["time"],
    )

    assert {
        identity(signal) for signal in live if signal["time"] < monday_open
    } == {
        identity(signal) for signal in closed if signal["time"] < monday_open
    }


def test_live_monday_uses_the_same_three_day_phase_as_the_closed_chart() -> None:
    daily = _trading_daily_history(end="2026-07-28", close=3_000.0)
    daily.extend(
        [
            {
                "time": _unix(f"{date_key}T20:00:00Z"),
                "date": date_key,
                "open": close,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
                "volume": 100,
            }
            for date_key, close in (
                ("2026-07-29", 1.0),
                ("2026-07-30", 1.0),
                ("2026-07-31", 1.0),
                # This is Monday's partial official daily value. It must begin
                # the next open 3D group instead of erasing Thu/Fri bubbles.
                ("2026-08-03", 3_000.0),
            )
        ]
    )
    historical_source = [
        {
            "time": _unix(f"{date_key}T{hour}:00:00Z"),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100,
        }
        for date_key, close in (
            ("2026-07-29", 1.0),
            ("2026-07-30", 1.0),
            ("2026-07-31", 1.0),
        )
        for hour in ("05", "09")  # 01:00 and 05:00 ET
    ]
    live_source = [
        {
            "time": _unix("2026-08-03T14:00:00Z"),  # 10:00 ET; still forming
            "open": 3_000.0,
            "high": 3_001.0,
            "low": 2_999.0,
            "close": 3_000.0,
            "volume": 100,
        }
    ]

    payload = build_ganesh_higher_timeframe_signal_payload(
        _frame(historical_source),
        _frame(live_source),
        _frame(daily),
    )
    put_3d_times = [
        signal["time"]
        for signal in payload["signals"]
        if signal["family"] == "ganesh920"
        and signal["timeframe"] == "3D"
        and signal["direction"] == "PUT"
    ]

    assert put_3d_times == [_unix("2026-07-29T05:00:00Z")]


def test_sunday_exto_candle_uses_mondays_tos_trading_date() -> None:
    sunday_21_et = {"time": _unix("2026-08-03T01:00:00Z")}
    sunday_17_et = {"time": _unix("2026-08-02T21:00:00Z")}
    sunday_13_et = {"time": _unix("2026-08-02T17:00:00Z")}
    sunday_19_et = {"time": _unix("2026-08-02T23:00:00Z")}
    monday_01_et = {"time": _unix("2026-08-03T05:00:00Z")}

    assert ganesh._trading_date_key_for_bar(sunday_21_et) == "2026-08-03"
    assert ganesh._trading_date_key_for_bar(sunday_17_et) == "2026-08-03"
    assert ganesh._trading_date_key_for_bar(sunday_13_et) == "2026-08-02"
    assert ganesh._trading_date_key_for_bar(sunday_19_et) == "2026-08-03"
    assert ganesh._trading_date_key_for_bar(monday_01_et) == "2026-08-03"


def test_avgo_monday_0100_matches_tos_call2d_and_calld_without_later_put_reversal(
    monkeypatch,
) -> None:
    """Regression fixture copied from the supplied AVGO 15D/4H TOS chart."""
    daily_closes = (
        ("2026-05-07", 412.56), ("2026-05-08", 430.0),
        ("2026-05-11", 428.43), ("2026-05-12", 419.3),
        ("2026-05-13", 416.79), ("2026-05-14", 439.79),
        ("2026-05-15", 425.19), ("2026-05-18", 420.71),
        ("2026-05-19", 411.07), ("2026-05-20", 417.76),
        ("2026-05-21", 414.57), ("2026-05-22", 414.14),
        ("2026-05-26", 422.01), ("2026-05-27", 421.86),
        ("2026-05-28", 426.58), ("2026-05-29", 446.77),
        ("2026-06-01", 459.97), ("2026-06-02", 481.57),
        ("2026-06-03", 479.23), ("2026-06-04", 418.91),
        ("2026-06-05", 385.73), ("2026-06-08", 396.6),
        ("2026-06-09", 392.16), ("2026-06-10", 372.1),
        ("2026-06-11", 385.57), ("2026-06-12", 382.07),
        ("2026-06-15", 393.94), ("2026-06-16", 376.71),
        ("2026-06-17", 392.9), ("2026-06-18", 411.35),
        ("2026-06-22", 392.13), ("2026-06-23", 380.15),
        ("2026-06-24", 382.07), ("2026-06-25", 378.91),
        ("2026-06-26", 365.02), ("2026-06-29", 372.45),
        ("2026-06-30", 377.75), ("2026-07-01", 369.34),
        ("2026-07-02", 360.45), ("2026-07-06", 373.9),
        ("2026-07-07", 370.78), ("2026-07-08", 388.69),
        ("2026-07-09", 401.11), ("2026-07-10", 399.97),
        ("2026-07-13", 384.05), ("2026-07-14", 389.11),
        ("2026-07-15", 394.28), ("2026-07-16", 374.45),
        ("2026-07-17", 370.825), ("2026-07-20", 378.16),
        ("2026-07-21", 386.5), ("2026-07-22", 396.81),
        ("2026-07-23", 392.47), ("2026-07-24", 381.92),
        ("2026-07-27", 383.22), ("2026-07-28", 380.91),
        ("2026-07-29", 370.32), ("2026-07-30", 387.84),
        ("2026-07-31", 389.28), ("2026-08-03", 390.34),
    )
    daily = [
        {
            "time": _unix(f"{date_key}T20:00:00Z"),
            "date": date_key,
            "open": close,
            "high": close,
            "low": close,
            "close": close,
            "volume": 1,
        }
        for date_key, close in daily_closes
    ]
    primary_values = (
        ("2026-07-31T05:00:00Z", 392.0, 393.8, 390.31, 393.0),
        ("2026-07-31T09:00:00Z", 393.47, 394.5, 390.2, 390.83),
        ("2026-07-31T13:00:00Z", 391.0, 399.92, 379.71, 387.95),
        ("2026-07-31T17:00:00Z", 388.0, 391.25, 386.82, 387.92),
        ("2026-07-31T21:00:00Z", 388.5, 388.95, 386.59, 387.45),
        ("2026-08-02T21:00:00Z", 391.45, 393.29, 391.28, 392.58),
        ("2026-08-03T01:00:00Z", 392.58, 394.72, 392.58, 394.03),
        ("2026-08-03T05:00:00Z", 393.38, 394.65, 392.12, 392.2),
        ("2026-08-03T09:00:00Z", 389.79, 389.79, 383.0, 385.24),
        ("2026-08-03T13:00:00Z", 385.32, 389.8, 374.61, 388.3),
        ("2026-08-03T17:00:00Z", 388.22, 392.47, 388.02, 390.34),
    )
    primary = [
        {
            "time": _unix(timestamp),
            "open": open_value,
            "high": high,
            "low": low,
            "close": close,
            "volume": 1,
        }
        for timestamp, open_value, high, low, close in primary_values
    ]
    # This compact visual-regression fixture intentionally skips the production
    # warm-up gate; separate tests cover the full-history readiness contract.
    monkeypatch.setattr(
        ganesh,
        "_FAMILY_PREFETCH_BUCKETS",
        {"ganesh48": 0, "ganesh920": 0, "ganeshMacd": 999},
    )

    signals, _ = ganesh._calculate_signal_result(
        primary,
        daily,
        incomplete_session_date="2026-08-03",
    )
    recent = [signal for signal in signals if signal["time"] >= _unix("2026-08-02T21:00:00Z")]
    completed_signals, _ = ganesh._calculate_signal_result(
        primary,
        daily,
        incomplete_session_date="",
    )
    completed_recent = [
        signal
        for signal in completed_signals
        if signal["time"] >= _unix("2026-08-02T21:00:00Z")
    ]

    # TOS uses the Sunday extended-hours candle for the upcoming trading
    # session but waits until the 01:00 ET calendar candle to print the
    # bubbles. Both the 4x8 CALL2D and the 9x20 CALLD must remain visible.
    expected = [
        ("ganesh48", "CALL2D", "CALL", _unix("2026-08-03T05:00:00Z")),
        ("ganesh920", "CALLD", "CALL", _unix("2026-08-03T05:00:00Z")),
    ]
    assert [
        (signal["family"], signal["label"], signal["direction"], signal["time"])
        for signal in recent
    ] == expected
    assert [
        (signal["family"], signal["label"], signal["direction"], signal["time"])
        for signal in completed_recent
    ] == expected


def test_every_4x8_higher_timeframe_defers_hidden_overnight_cross_to_next_0100(
    monkeypatch,
) -> None:
    """D/2D/3D/4D/W/M placement is symbol- and price-scale-independent."""
    monkeypatch.setattr(
        ganesh,
        "_FAMILY_PREFETCH_BUCKETS",
        {"ganesh48": 0, "ganesh920": 999, "ganeshMacd": 999},
    )

    for baseline, breakout in ((3.0, 1_000.0), (3_000.0, 1_000_000.0)):
        daily = _trading_daily_history(end="2026-07-31", close=baseline)
        daily.append(
            {
                "time": _unix("2026-08-03T20:00:00Z"),
                "date": "2026-08-03",
                "open": breakout,
                "high": breakout + 1,
                "low": breakout - 1,
                "close": breakout,
                "volume": 100,
            }
        )
        primary = [
            {
                "time": _unix(timestamp),
                "open": close,
                "high": close + 1,
                "low": close - 1,
                "close": close,
                "volume": 100,
            }
            for timestamp, close in (
                ("2026-08-02T21:00:00Z", baseline),
                ("2026-08-03T01:00:00Z", baseline),
                ("2026-08-03T05:00:00Z", breakout),
            )
        ]

        signals, _ = ganesh._calculate_signal_result(
            primary,
            daily,
            incomplete_session_date="2026-08-03",
        )
        yellow = [
            (signal["label"], signal["time"])
            for signal in signals
            if signal["family"] == "ganesh48"
        ]

        assert yellow == [
            (label, _unix("2026-08-03T05:00:00Z"))
            for label in ("CALLD", "CALL2D", "CALL3D", "CALL4D", "CALLW", "CALLM")
        ]


def test_open_weekly_4x8_uses_one_active_tos_value_across_live_4h_candles() -> None:
    base = ganesh._source_values(None, 380.0)
    active_current = ganesh._source_values(base, 390.34)
    late_primary_current = ganesh._source_values(base, 391.22)
    snapshot = {
        "groupKey": "W-2026-08-03",
        "base": base,
        "current": late_primary_current,
        "completedBuckets": 50,
    }
    context = {"activeCurrent": active_current}

    weekly = ganesh._stable_open_long_period_48_snapshot(
        snapshot,
        context,
        "W",
        incomplete_session=True,
    )
    daily = ganesh._stable_open_long_period_48_snapshot(
        snapshot,
        context,
        "D",
        incomplete_session=True,
    )

    assert weekly["current"] == active_current
    assert daily["current"] == late_primary_current
    assert snapshot["current"] == late_primary_current


def test_macd_6_12_8_prints_every_secondary_cross_on_the_actual_premarket_candle() -> None:
    daily = _trading_daily_history()
    daily.append(
        {
            "time": _unix("2026-07-31T20:00:00Z"),
            "date": "2026-07-31",
            "open": 1_000_000,
            "high": 1_000_001,
            "low": 999_999,
            "close": 1_000_000,
            "volume": 100,
        }
    )
    primary = [
        {
            "time": _unix(f"2026-07-31T{hour}:00:00Z"),
            "open": close,
            "high": close + 10,
            "low": close - 10,
            "close": close,
            "volume": 100,
        }
        for hour, close in (("05", 3_000.0), ("09", 1_000_000.0), ("13", 1_000_000.0), ("17", 1_000_000.0))
    ]

    payload = build_ganesh_higher_timeframe_signal_payload(
        _frame(primary),
        pd.DataFrame(),
        _frame(daily),
    )
    macd = [signal for signal in payload["signals"] if signal["family"] == "ganeshMacd"]

    assert payload["historyReady"] is True
    expected_pass = [
        (f"MACD-{timeframe}", "CALL")
        for timeframe in ("D", "2D", "3D", "4D", "W", "M")
    ]
    # The MACD CompoundValue latch emits the full timeframe set at the
    # first eligible primary candle, then suppresses duplicates until reset.
    assert [(signal["label"], signal["direction"]) for signal in macd] == expected_pass
    assert {signal["time"] for signal in macd} == {
        _unix("2026-07-31T09:00:00Z"),
    }
    assert {signal["low"] for signal in macd} == {999_990}
    assert all(signal["stateSnapshot"] is False and signal["sourceEvent"] is True for signal in macd)
    assert [definition["key"] for definition in GANESH_MACD_TIMEFRAMES] == [
        "D",
        "2D",
        "3D",
        "4D",
        "W",
        "M",
    ]


def test_macd_put_uses_the_same_actual_candle_and_high_anchor() -> None:
    daily = _trading_daily_history(close=4_000.0)
    daily.append(
        {
            "time": _unix("2026-07-31T20:00:00Z"),
            "date": "2026-07-31",
            "open": 1,
            "high": 2,
            "low": 0,
            "close": 1,
            "volume": 100,
        }
    )
    primary = [
        {
            "time": _unix(f"2026-07-31T{hour}:00:00Z"),
            "open": close,
            "high": close + 3,
            "low": close - 1,
            "close": close,
            "volume": 100,
        }
        for hour, close in (("05", 4_000.0), ("09", 1.0), ("13", 1.0))
    ]

    signals = calculate_ganesh_higher_timeframe_signals(primary, daily)
    macd = [signal for signal in signals if signal["family"] == "ganeshMacd"]

    expected = [
        (f"MACD-{timeframe}", "PUT")
        for timeframe in ("D", "2D", "3D", "4D", "W", "M")
    ]
    assert [(signal["label"], signal["direction"]) for signal in macd] == expected * 2
    assert {signal["time"] for signal in macd} == {
        _unix("2026-07-31T05:00:00Z"),
        _unix("2026-07-31T09:00:00Z"),
    }
    assert {signal["high"] for signal in macd} == {4_003, 4}


def test_macd_preserves_tos_prior_latch_reset_order_across_chart_days() -> None:
    daily = _trading_daily_history(end="2026-07-29")
    daily.extend(
        [
            {
                "time": _unix(f"{date_key}T20:00:00Z"),
                "date": date_key,
                "open": 1_000_000,
                "high": 1_000_001,
                "low": 999_999,
                "close": 1_000_000,
                "volume": 100,
            }
            for date_key in ("2026-07-30", "2026-07-31")
        ]
    )
    primary = [
        {
            "time": _unix(f"{date_key}T{hour}:00:00Z"),
            "open": 1_000_000,
            "high": 1_000_010,
            "low": 999_990,
            "close": 1_000_000,
            "volume": 100,
        }
        for date_key in ("2026-07-30", "2026-07-31")
        for hour in ("09", "13")  # 05:00 and 09:00 ET
    ]

    signals = calculate_ganesh_higher_timeframe_signals(primary, daily)
    two_day_calls = [
        signal
        for signal in signals
        if signal["family"] == "ganeshMacd"
        and signal["timeframe"] == "2D"
        and signal["direction"] == "CALL"
    ]

    # rawCall2D stays true throughout the open 2D candle. On Jul-30 the
    # source's fired[1] quirk prints twice; Jul-31's first bar is suppressed by
    # yesterday's latch while resetting it, then the second bar prints.
    assert [signal["time"] for signal in two_day_calls] == [
        _unix("2026-07-30T09:00:00Z"),
        _unix("2026-07-30T13:00:00Z"),
        _unix("2026-07-31T13:00:00Z"),
    ]

    historical_daily_calls = [
        signal
        for signal in signals
        if signal["family"] == "ganeshMacd"
        and signal["timeframe"] == "D"
        and signal["direction"] == "CALL"
    ]
    # Jul-30 is finalized history. Its secondary cross is back-painted across
    # both contained 4H bars before the source latch is applied, preserving old
    # bubbles instead of keeping only the live/right-edge signal.
    assert [signal["time"] for signal in historical_daily_calls] == [
        _unix("2026-07-30T09:00:00Z"),
        _unix("2026-07-30T13:00:00Z"),
    ]


def test_completed_monday_keeps_weekly_macd_bubbles_on_monday_candles(
    monkeypatch,
) -> None:
    """Regression for NFLX image 3: a post-close rebuild cannot back-paint W onto Sunday."""
    monkeypatch.setattr(
        ganesh,
        "_FAMILY_PREFETCH_BUCKETS",
        {"ganesh48": 999, "ganesh920": 999, "ganeshMacd": 0},
    )
    daily = _trading_daily_history(count=120, end="2026-07-24", close=100.0)
    daily.extend(
        {
            "time": _unix(f"{date_key}T20:00:00Z"),
            "date": date_key,
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 100,
        }
        for date_key in ("2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31")
    )
    daily.append(
        {
            "time": _unix("2026-08-03T20:00:00Z"),
            "date": "2026-08-03",
            "open": 1_000_000.0,
            "high": 1_000_001.0,
            "low": 999_999.0,
            "close": 1_000_000.0,
            "volume": 100,
        }
    )
    primary = [
        {
            "time": _unix(timestamp),
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": 100,
        }
        for timestamp, close in (
            ("2026-08-02T21:00:00Z", 100.0),  # Sunday 17:00 ET
            ("2026-08-03T01:00:00Z", 100.0),  # Sunday 21:00 ET
            ("2026-08-03T05:00:00Z", 1_000_000.0),  # Monday 01:00 ET
            ("2026-08-03T09:00:00Z", 1_000_000.0),  # Monday 05:00 ET
        )
    ]

    signals, _ = ganesh._calculate_signal_result(
        primary,
        daily,
        # This is the exact post-close path that used to replace the morning
        # tape and move both weekly bubbles onto Sunday.
        incomplete_session_date="",
    )
    weekly_calls = [
        signal["time"]
        for signal in signals
        if signal["family"] == "ganeshMacd"
        and signal["timeframe"] == "W"
        and signal["direction"] == "CALL"
    ]

    assert weekly_calls == [
        _unix("2026-08-03T05:00:00Z"),
        _unix("2026-08-03T09:00:00Z"),
    ]


def test_official_secondary_close_prevents_a_false_extended_hours_monthly_cross() -> None:
    daily = _trading_daily_history(close=3_000.0)
    daily.append(
        {
            "time": _unix("2026-07-31T20:00:00Z"),
            "date": "2026-07-31",
            "open": 3_000.0,
            "high": 3_001.0,
            "low": 2_999.0,
            "close": 3_000.0,
            "volume": 100,
        }
    )
    primary = [
        {
            "time": _unix("2026-07-31T21:00:00Z"),
            "open": 1.0,
            "high": 2.0,
            "low": 0.0,
            "close": 1.0,
            "volume": 100,
        }
    ]

    signals = calculate_ganesh_higher_timeframe_signals(primary, daily)

    assert not any(
        signal["family"] == "ganeshMacd" and signal["timeframe"] == "M"
        for signal in signals
    )


def test_macd_secondary_latches_build_the_tos_premarket_staircase() -> None:
    daily = _trading_daily_history(end="2026-07-30", close=3_000.0)
    daily.append(
        {
            "time": _unix("2026-07-31T20:00:00Z"),
            "date": "2026-07-31",
            "open": 1_000_000.0,
            "high": 1_000_001.0,
            "low": 999_999.0,
            "close": 1_000_000.0,
            "volume": 100,
        }
    )
    primary = [
        {
            "time": _unix(f"{date_key}T{hour}:00:00Z"),
            "open": 3_000.0,
            "high": 3_001.0,
            "low": 2_999.0,
            "close": 3_000.0,
            "volume": 100,
        }
        for date_key in (
            "2026-07-27",
            "2026-07-28",
            "2026-07-29",
            "2026-07-30",
            "2026-07-31",
        )
        for hour in ("05", "09")  # 01:00 and 05:00 ET
    ]

    signals = calculate_ganesh_higher_timeframe_signals(primary, daily)
    actual = {
        pd.Timestamp(signal["time"], unit="s", tz="UTC")
        .tz_convert("America/New_York")
        .strftime("%Y-%m-%d %H:%M"): []
        for signal in signals
        if signal["family"] == "ganeshMacd" and signal["timeframe"] != "M"
    }
    for signal in signals:
        if signal["family"] != "ganeshMacd" or signal["timeframe"] == "M":
            continue
        timestamp = (
            pd.Timestamp(signal["time"], unit="s", tz="UTC")
            .tz_convert("America/New_York")
            .strftime("%Y-%m-%d %H:%M")
        )
        actual[timestamp].append(signal["timeframe"])

    assert actual == {
        "2026-07-27 01:00": ["W"],
        "2026-07-27 05:00": ["W"],
        "2026-07-28 01:00": ["4D"],
        "2026-07-28 05:00": ["4D", "W"],
        "2026-07-29 01:00": ["3D"],
        "2026-07-29 05:00": ["3D", "4D", "W"],
        "2026-07-30 01:00": ["2D"],
        "2026-07-30 05:00": ["2D", "3D", "4D", "W"],
        "2026-07-31 01:00": ["D"],
        "2026-07-31 05:00": ["D", "2D", "3D", "4D", "W"],
    }


def test_short_history_self_gates_and_reports_not_ready() -> None:
    daily = _falling_daily_history(10)
    primary = _frame(
        [
            {
                "time": _unix("2026-03-02T06:00:00Z"),  # 01:00 ET MACD-D carrier
                "open": 100,
                "high": 101,
                "low": 99,
                "close": 100,
                "volume": 100,
            }
        ]
    )

    payload = build_ganesh_higher_timeframe_signal_payload(primary, pd.DataFrame(), _frame(daily))

    assert payload["historyReady"] is False
    assert payload["signals"] == []
