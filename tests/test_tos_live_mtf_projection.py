from __future__ import annotations

import pandas as pd

from scanner import _tos_mtf_ema_signal_payload


def _flat_then_move_frame(move_time: str, move_close: float) -> pd.DataFrame:
    timestamps = pd.date_range(
        "2026-07-20 04:00",
        "2026-07-22 16:00",
        freq="5min",
        tz="America/New_York",
    )
    closes = [100.0 if timestamp < pd.Timestamp(move_time, tz="America/New_York") else move_close for timestamp in timestamps]
    return pd.DataFrame({"timestamp": timestamps, "close": closes})


def test_tos_projection_backfills_higher_timeframe_call_to_bucket_start() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 10:00", 110.0))

    matching = [
        signal
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["timeframe"] == "1H"
        and signal["direction"] == "CALL"
    ]

    assert payload["mode"] == "tos_final_secondary_5m"
    assert matching
    signal_time = pd.to_datetime(matching[-1]["time"], unit="s", utc=True).tz_convert("America/New_York")
    # The move starts on the bucket's first candle, so the developing cross
    # fires there: same candle as the old bucket-start projection.
    assert signal_time.strftime("%Y-%m-%d %H:%M") == "2026-07-22 10:00"
    assert matching[-1]["label"] == "CALL1H"
    assert matching[-1]["compact"] is False
    assert matching[-1]["candleTimestamp"] == matching[-1]["time"]
    assert matching[-1]["secondaryBucketStart"] is True


def test_tos_projection_keeps_call30_and_call1h_on_same_5m_candle() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 10:00", 110.0))
    expected_time = int(pd.Timestamp("2026-07-22 10:00", tz="America/New_York").timestamp())

    simultaneous_labels = {
        signal["label"]
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["direction"] == "CALL"
        and signal["time"] == expected_time
    }

    assert {"CALL30", "CALL1H"}.issubset(simultaneous_labels)


def test_tos_projection_detects_cross_from_equal_ema_state() -> None:
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 13:00", 90.0))

    put_signals = [
        signal
        for signal in payload["signals"]
        if signal["family"] == "9x20"
        and signal["direction"] == "PUT"
        and signal["time"] == int(pd.Timestamp("2026-07-22 13:00", tz="America/New_York").timestamp())
    ]

    assert put_signals
    # liveForming is no longer a hardcoded True: it marks a cross inside the
    # still-forming higher bar (the only one that can still repaint).
    assert all(isinstance(signal["liveForming"], bool) for signal in put_signals)
    # Every cross sits on its higher bucket's first candle: a completed bucket
    # carries its final close on every 5m bar, and TOS repaints the forming
    # bucket's bars with the current developing value.
    assert all(signal["secondaryBucketStart"] is True for signal in put_signals)


def test_four_hour_secondary_buckets_use_the_tos_central_clock() -> None:
    """TOS 4h bars (extended hours) start at 01/05/09/13/17 ET, not 00/04/08/12.
    A move that starts at 09:00 must land on a 09:00 4H bucket; the old
    midnight-anchored resample reported it on an 08:00 bucket TOS never has
    (META, 2026-08-24: a cyan CALL4H that the TOS chart did not show)."""
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 09:00", 110.0))
    four_hour = [
        signal for signal in payload["signals"]
        if signal["timeframe"] == "4H" and signal["direction"] == "CALL"
    ]
    assert four_hour
    stamps = {
        pd.to_datetime(signal["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M")
        for signal in four_hour
    }
    assert stamps == {"09:00"}
    # Every intraday aggregation is anchored at midnight Central (01:00 ET),
    # so the 2h bucket here is 09:00-11:00: the label and the bucket start
    # coincide at 09:00 (TOS 2h bars run 01/03/05/07/09..., not even hours).
    two_hour = [
        signal for signal in payload["signals"]
        if signal["timeframe"] == "2H" and signal["direction"] == "CALL"
    ]
    assert two_hour
    assert {
        pd.to_datetime(signal["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M")
        for signal in two_hour
    } == {"09:00"}
    assert {
        pd.to_datetime(signal["candleTimestamp"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M")
        for signal in two_hour
    } == {"09:00"}


def test_engine_evaluates_once_per_chart_bar_and_drops_pairs_shorter_than_the_chart() -> None:
    """TOS runs the study on the CHART's bars: a 1H chart samples the
    developing secondary values once an hour and cannot plot a 15m or 30m
    pair at all (secondary period < chart period is refused)."""
    frame = _flat_then_move_frame("2026-07-22 10:20", 110.0)
    hourly = _tos_mtf_ema_signal_payload(frame, bar_minutes=60)
    assert hourly["barMinutes"] == 60
    assert hourly["signals"]
    assert {signal["timeframe"] for signal in hourly["signals"]} <= {"1H", "2H", "4H"}
    stamps = {
        pd.to_datetime(signal["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%M")
        for signal in hourly["signals"]
    }
    assert stamps == {"00"}
    # The 5m chart sees the 15m pair's cross on ITS bucket start (10:15), a
    # candle the 1H chart does not have.
    five = _tos_mtf_ema_signal_payload(frame)
    assert any(
        signal["timeframe"] == "15"
        and pd.to_datetime(signal["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M") == "10:15"
        for signal in five["signals"]
    )


def test_historical_crosses_sit_on_bucket_starts_and_two_hour_bars_use_odd_hours() -> None:
    """A move that starts at 10:20 lands, for the 2h pair, on the 09:00-11:00
    bucket: TOS repaints that whole bucket with its final close once it
    closes, so the cross prints on 09:00 - not on 10:20 - and never flickers
    inside the bucket (the 09:40/09:45 PUT2H/CALL2H pair TOS never showed)."""
    payload = _tos_mtf_ema_signal_payload(_flat_then_move_frame("2026-07-22 10:20", 110.0))
    two_hour = [s for s in payload["signals"] if s["timeframe"] == "2H" and s["direction"] == "CALL"]
    assert two_hour
    stamps = {
        pd.to_datetime(s["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M")
        for s in two_hour
    }
    assert stamps == {"09:00"}
    assert all(s["secondaryBucketStart"] for s in two_hour)
    assert payload["mode"] == "tos_final_secondary_5m"
    # No 2H PUT flicker anywhere on that day.
    assert not [s for s in payload["signals"] if s["timeframe"] == "2H" and s["direction"] == "PUT"
                and pd.to_datetime(s["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%Y-%m-%d") == "2026-07-22"]


def test_overnight_bars_belong_to_the_next_session() -> None:
    """Sunday 20:00 -> Monday 04:00 bars are part of MONDAY's session for the
    5-session window and the DAY bucket (TOS EXTO)."""
    timestamps = pd.date_range("2026-07-19 20:00", "2026-07-20 16:00", freq="5min", tz="America/New_York")
    frame = pd.DataFrame({"timestamp": timestamps, "close": [100.0] * len(timestamps)})
    payload = _tos_mtf_ema_signal_payload(frame)
    assert payload["mode"] == "tos_final_secondary_5m"
    # Flat tape: no crosses, but the engine must accept the night without
    # trimming it away as a separate (sixth) session.
    assert payload["signals"] == []


def test_forming_bucket_shows_only_its_current_cross_like_a_tos_repaint() -> None:
    """Inside the still-forming higher bar TOS repaints on every tick: a
    developing value that flips up, down and up again leaves ONE bubble (the
    current relation vs the last completed bar), not three."""
    timestamps = pd.date_range("2026-07-20 04:00", "2026-07-22 10:40", freq="5min", tz="America/New_York")
    closes = []
    for stamp in timestamps:
        if stamp < pd.Timestamp("2026-07-22 09:00", tz="America/New_York"):
            closes.append(100.0)
        else:
            # forming 09:00-11:00 2h bucket: up, back down, up again, ends up
            minute = (stamp - pd.Timestamp("2026-07-22 09:00", tz="America/New_York")).total_seconds() / 60
            closes.append(110.0 if minute < 20 else 90.0 if minute < 40 else 110.0)
    frame = pd.DataFrame({"timestamp": timestamps, "close": closes})
    payload = _tos_mtf_ema_signal_payload(frame)
    two_hour = [s for s in payload["signals"] if s["timeframe"] == "2H" and s["family"] == "4x8"]
    forming = [s for s in two_hour if s["liveForming"]]
    assert len(forming) == 1
    assert forming[0]["direction"] == "CALL"
    # TOS repaints the whole forming bucket, so the single bubble prints on
    # the bucket's first candle (COHR 2026-10-01: CALL4H on 09:00 at 12:25).
    assert pd.to_datetime(forming[0]["time"], unit="s", utc=True).tz_convert("America/New_York").strftime("%H:%M") == "09:00"


def test_seeding_uses_the_whole_tape_not_only_five_sessions() -> None:
    """thinkScript prefetches history so an EMA is settled at the first visible
    bar; the engine must seed on the whole supplied tape, not the last five
    sessions. MSFT 2026-08-25: with a 5-session window the 9x20 4H EMAs never
    crossed and the CALL4H TOS printed was missing; ten+ sessions fixed it and
    were identical at 10/20/40 days."""
    stamps = pd.date_range("2026-07-06 04:00", "2026-07-22 16:00", freq="5min", tz="America/New_York")
    # A slow drift over three weeks: the 9x20 4H relation only settles with
    # more than five sessions of seeding.
    closes = [100.0 + (t - stamps[0]).total_seconds() / 86400.0 for t in stamps]
    frame = pd.DataFrame({"timestamp": stamps, "close": closes})
    payload = _tos_mtf_ema_signal_payload(frame)
    # More than five sessions of source must survive into the states (a
    # truncating window would keep only the last five days of the tape).
    span_days = (
        pd.to_datetime(payload["states"][0]["updatedAt"], unit="s", utc=True)
        - pd.Timestamp("2026-07-06 04:00", tz="America/New_York")
    ).days
    assert span_days >= 15
    # The 9x20 4H EMA is fully warmed (a settled positive spread), not the
    # seed-equals-first-close artifact a five-day window produced.
    day4h = next(s for s in payload["states"] if s["family"] == "9x20" and s["timeframe"] == "4H")
    assert day4h["fastEma"] > day4h["slowEma"]


# 2026-10-01 "it should not be delayed": the chart's per-tick live reconcile
# (frontend/src/mtfLiveSignalState.js) needs liveSignalContexts, which the
# server never produced, so every live bubble waited for a full rebuild.
def _client_live_label(context: dict, price: float) -> str | None:
    """Python replica of reconcileLiveMtfSignals' forming-candle formula."""
    fast_alpha = 2 / (context["fastLength"] + 1)
    slow_alpha = 2 / (context["slowLength"] + 1)
    fast = fast_alpha * price + (1 - fast_alpha) * context["baseFastEma"]
    slow = slow_alpha * price + (1 - slow_alpha) * context["baseSlowEma"]
    base_difference = context["baseFastEma"] - context["baseSlowEma"]
    difference = fast - slow
    direction = None
    if base_difference <= 0 and difference > 0:
        direction = "CALL"
    if base_difference >= 0 and difference < 0:
        direction = "PUT"
    if direction is None:
        return None
    higher_fast = fast_alpha * price + (1 - fast_alpha) * context["higherBaseFastEma"]
    higher_slow = slow_alpha * price + (1 - slow_alpha) * context["higherBaseSlowEma"]
    confirmed = (higher_fast >= higher_slow) == (direction == "CALL")
    prefix = direction if confirmed else ("C" if direction == "CALL" else "P")
    return f"{prefix}{context['timeframe']}"


def _wave_frame(end: str) -> pd.DataFrame:
    import math

    timestamps = pd.date_range("2026-07-14 04:00", end, freq="5min", tz="America/New_York")
    closes = [100 + 3 * math.sin(index / 9.0) + 1.5 * math.sin(index / 2.3) for index in range(len(timestamps))]
    return pd.DataFrame({"timestamp": timestamps, "close": closes})


def test_live_contexts_cover_every_pair_and_point_at_the_forming_bucket() -> None:
    payload = _tos_mtf_ema_signal_payload(_wave_frame("2026-07-22 10:20"))
    contexts = payload["liveSignalContexts"]
    pairs = {(item["family"], item["timeframe"]) for item in contexts}
    assert pairs == {
        ("4x8", "15"), ("4x8", "30"), ("4x8", "1H"), ("4x8", "2H"), ("4x8", "4H"),
        ("9x20", "30"), ("9x20", "1H"), ("9x20", "2H"), ("9x20", "4H"),
    }
    last = int(pd.Timestamp("2026-07-22 10:20", tz="America/New_York").timestamp())
    for context in contexts:
        start = context["candleTimestamp"]
        assert start <= last < start + context["signalMinutes"] * 60
        assert {"fastLength", "slowLength", "baseFastEma", "baseSlowEma", "higherBaseFastEma", "higherBaseSlowEma"} <= set(context)


def test_client_live_formula_on_contexts_equals_a_full_server_rebuild() -> None:
    # Contexts taken at 10:20, applied to the 10:25 / 10:30 / 10:35 prices
    # inside the same buckets, must give exactly the forming-bucket bubble the
    # server prints after rebuilding with those bars.
    early = _tos_mtf_ema_signal_payload(_wave_frame("2026-07-22 10:20"))
    checked = 0
    for later_end in ("2026-07-22 10:25", "2026-07-22 10:30", "2026-07-22 10:35"):
        later_frame = _wave_frame(later_end)
        later = _tos_mtf_ema_signal_payload(later_frame)
        later_time = int(pd.Timestamp(later_end, tz="America/New_York").timestamp())
        price = float(later_frame["close"].iloc[-1])
        for context in early["liveSignalContexts"]:
            if later_time >= context["candleTimestamp"] + context["signalMinutes"] * 60:
                continue  # the bucket closed; the next rebuild sends new contexts
            expected = [
                signal["label"] for signal in later["signals"]
                if signal["family"] == context["family"]
                and signal["timeframe"] == context["timeframe"]
                and signal["liveForming"]
            ]
            got = _client_live_label(context, price)
            assert (expected[-1] if expected else None) == got, (context["family"], context["timeframe"], later_end)
            checked += 1
    assert checked >= 15


def test_forming_bubbles_are_flagged_live_so_the_client_can_repaint_them() -> None:
    payload = _tos_mtf_ema_signal_payload(_wave_frame("2026-07-22 10:20"))
    for signal in payload["signals"]:
        assert bool(signal.get("isLiveSignal")) == bool(signal["liveForming"])


def test_day_confirmation_uses_the_real_daily_closes_inside_the_tape() -> None:
    # 2026-10-01 parity audit: daily closes were keyed by midnight ET while the
    # DAY buckets are keyed by the 20:00 ET session start, so in-tape days fell
    # back to the ~19:55 extended-hours 5m close (MSFT CALL4H/C4H flipped).
    frame = _wave_frame("2026-07-22 10:20")
    # Real tapes have no Saturday/Sunday sessions (no daily close exists).
    stamps = pd.to_datetime(frame["timestamp"])
    session_weekday = (stamps + pd.Timedelta(hours=4)).dt.weekday
    frame = frame[session_weekday < 5].reset_index(drop=True)
    days = pd.date_range("2026-06-01", "2026-07-21", freq="B", tz="America/New_York")
    # Daily closes deliberately far from any 5m close so a fallback is obvious.
    daily = pd.DataFrame({"timestamp": days, "close": [500.0 + i for i in range(len(days))]})
    payload = _tos_mtf_ema_signal_payload(frame, daily_frame=daily)
    context = next(c for c in payload["liveSignalContexts"] if c["family"] == "4x8" and c["timeframe"] == "4H")
    closes = list(daily["close"])
    ema4 = closes[0]
    for close in closes[1:]:
        ema4 = ema4 + (2 / 5) * (close - ema4)
    assert abs(context["higherBaseFastEma"] - ema4) < 1e-6


def test_forming_4h_cross_prints_on_the_open_bucket_first_candle_like_tos() -> None:
    """COHR 2026-10-01, TOS 5m at 12:25: CALL1H and CALL4H both on the 09:00
    candle while the 09:00-13:00 4H bar was still forming. The developing 4H
    value only crossed once price ran at 09:35; TOS still draws it at 09:00."""
    stamps = pd.date_range("2026-09-14 04:00", "2026-10-01 12:25", freq="5min", tz="America/New_York")
    stamps = stamps[(stamps.weekday < 5) & ((stamps.hour * 60 + stamps.minute) >= 240) & (stamps.hour < 20)]
    move = pd.Timestamp("2026-10-01 09:35", tz="America/New_York")
    frame = pd.DataFrame({"timestamp": stamps, "close": [130.0 if t >= move else 100.0 for t in stamps]})
    payload = _tos_mtf_ema_signal_payload(frame)
    nine = int(pd.Timestamp("2026-10-01 09:00", tz="America/New_York").timestamp())
    four_hour = [s for s in payload["signals"] if s["timeframe"] == "4H" and s["direction"] == "CALL"]
    assert four_hour and all(s["time"] == nine for s in four_hour)
    assert all(s["liveForming"] for s in four_hour)


def test_confirmation_reads_the_forming_day_as_of_now_like_a_tos_repaint() -> None:
    """COHR 2026-10-01 at 13:15: the 09:00-13:00 4H bar had closed, and TOS
    labelled its 9x20 cross CALL4H because today's still-forming DAY bar had
    EMA9 >= EMA20 as of now. Read at the 09:00 bar's own pre-rally price the
    day was still bearish, which printed the compact C4H."""
    eastern = "America/New_York"
    stamps = pd.date_range("2026-08-17 04:00", "2026-10-01 13:15", freq="5min", tz=eastern)
    stamps = stamps[(stamps.weekday < 5) & ((stamps.hour * 60 + stamps.minute) >= 240) & (stamps.hour < 20)]
    today = pd.Timestamp("2026-10-01", tz=eastern)
    rally = pd.Timestamp("2026-10-01 09:35", tz=eastern)
    first, last_prior = stamps[0], today - pd.Timedelta(minutes=1)
    span = (last_prior - first).total_seconds()

    def close_at(stamp):
        if stamp >= rally:
            return 140.0
        if stamp >= today:
            return 100.0
        # Steady decline 120 -> 100: daily EMA9 < EMA20 going into today.
        return 120.0 - 20.0 * (stamp - first).total_seconds() / span

    frame = pd.DataFrame({"timestamp": stamps, "close": [close_at(t) for t in stamps]})
    payload = _tos_mtf_ema_signal_payload(frame)
    nine = int(pd.Timestamp("2026-10-01 09:00", tz=eastern).timestamp())
    cross = [s for s in payload["signals"] if s["family"] == "9x20" and s["timeframe"] == "4H" and s["time"] == nine]
    assert cross and cross[-1]["direction"] == "CALL"
    assert cross[-1]["label"] == "CALL4H"
