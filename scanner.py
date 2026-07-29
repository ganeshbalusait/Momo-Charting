from __future__ import annotations

from dataclasses import asdict, dataclass

import pandas as pd
from pandas import Timedelta

from config import EASTERN_TZ, settings
from data.alpaca_client import AlpacaClient
from data.market_data import create_market_data_client
from indicators import atr, cumulative_vwap, ema, regular_session, relative_volume, session_progress, volume_acceleration
from risk_manager import RiskManager


def tos_price_change_value(current_value: float, value_bars_ago: float) -> float | None:
    reference = float(value_bars_ago or 0.0)
    if reference <= 0:
        return None
    return 100.0 * ((float(current_value or 0.0) / reference) - 1.0)


def tos_price_change_scan(current_value: float, value_bars_ago: float, percent: float = 0.5, choice: str = "greater") -> bool:
    change_pct = tos_price_change_value(current_value, value_bars_ago)
    if change_pct is None:
        return False
    tolerance = 1e-9
    if str(choice or "greater").strip().lower() == "less":
        return change_pct <= (-float(percent) + tolerance)
    return change_pct >= (float(percent) - tolerance)


def volume_scan(current_volume: float, volume_2_bars_ago: float, threshold_pct: float = 0.5) -> bool:
    return tos_price_change_scan(current_volume, volume_2_bars_ago, percent=threshold_pct, choice="greater")


def price_change_scan(current_price: float, price_2_bars_ago: float, threshold_pct: float = 0.5) -> bool:
    return tos_price_change_scan(current_price, price_2_bars_ago, percent=threshold_pct, choice="greater")


def session_change_scan(change_pct: float | None, threshold_pct: float = 1.0) -> bool:
    if change_pct is None:
        return False
    return float(change_pct) >= float(threshold_pct)


def _aggregate_mtf_bars(
    frame: pd.DataFrame,
    minutes: int,
    *,
    regular_session_only: bool = False,
    origin_offset: str | None = None,
) -> pd.DataFrame:
    """Build fixed Eastern-time aggregation buckets for TOS-style MTF studies."""
    required = ["timestamp", "open", "high", "low", "close", "volume"]
    if frame is None or frame.empty or not set(required).issubset(frame.columns):
        return pd.DataFrame()

    working = frame[required].copy()
    timestamps = pd.to_datetime(working["timestamp"], errors="coerce")
    if getattr(timestamps.dt, "tz", None) is None:
        working["timestamp"] = timestamps.dt.tz_localize(
            EASTERN_TZ,
            nonexistent="shift_forward",
            ambiguous="NaT",
        )
    else:
        working["timestamp"] = timestamps.dt.tz_convert(EASTERN_TZ)
    for column in ["open", "high", "low", "close", "volume"]:
        working[column] = pd.to_numeric(working[column], errors="coerce")
    working = working.dropna(subset=["timestamp", "open", "high", "low", "close"])
    if regular_session_only and not working.empty:
        minute_of_day = (working["timestamp"].dt.hour * 60) + working["timestamp"].dt.minute
        working = working[
            (working["timestamp"].dt.weekday < 5)
            & (minute_of_day >= 570)
            & (minute_of_day < 960)
        ]
    if working.empty:
        return pd.DataFrame()

    working = working.sort_values("timestamp").set_index("timestamp")
    frequency = f"{max(int(minutes), 1)}min"
    resample_options = {
        "origin": "start_day",
        "label": "left",
        "closed": "left",
    }
    if origin_offset:
        resample_options["offset"] = origin_offset
    bucket = working.resample(
        frequency,
        **resample_options,
    ).agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
    )
    signal_times = working["close"].resample(
        frequency,
        **resample_options,
    ).apply(lambda values: values.index.max() if len(values) else pd.NaT)
    bucket["signal_time"] = signal_times
    return bucket.dropna(subset=["open", "high", "low", "close", "signal_time"]).reset_index()


def _tos_watchlist_mtf_signal_payload(frame: pd.DataFrame) -> dict:
    """Match the supplied TOS custom-quote state on 2H and 4H.

    This is intentionally separate from the 4x8 / 9x20 *chart marker* studies.
    The ThinkScript supplied for the watchlist colors a custom quote and displays
    the FastD value; it does not emit C2H/CALL4H chart bubbles.
    """
    aggregates = {
        "2H": _aggregate_mtf_bars(
            frame,
            120,
            regular_session_only=True,
            origin_offset="5h",
        ),
        "4H": _aggregate_mtf_bars(
            frame,
            240,
            regular_session_only=True,
            origin_offset="5h",
        ),
        "1D": _aggregate_mtf_bars(
            frame,
            1440,
            regular_session_only=True,
            origin_offset="5h",
        ),
    }
    higher_timeframe = {"2H": "4H", "4H": "1D"}
    calculated: dict[str, pd.DataFrame] = {}
    for timeframe, aggregate in aggregates.items():
        values = aggregate.copy()
        if values.empty:
            calculated[timeframe] = values
            continue
        for length in (4, 6, 8, 9, 12, 20):
            values[f"ema_{length}"] = values["close"].ewm(span=length, adjust=False).mean()
        values["macd_value"] = values["ema_6"] - values["ema_12"]
        values["macd_avg"] = values["macd_value"].ewm(span=8, adjust=False).mean()
        lowest = values["low"].rolling(8, min_periods=1).min()
        highest = values["high"].rolling(8, min_periods=1).max()
        fast_k = (100.0 * (values["close"] - lowest) / (highest - lowest).replace(0, pd.NA)).fillna(0.0)
        # ThinkScript StochasticFast FastD uses a weighted moving average.
        values["fast_d"] = fast_k.rolling(8, min_periods=1).apply(
            lambda series: float(sum(value * (index + 1) for index, value in enumerate(series)) / sum(range(1, len(series) + 1))),
            raw=True,
        )
        calculated[timeframe] = values

    signals: list[dict] = []
    states: list[dict] = []
    for timeframe in ("2H", "4H"):
        values = calculated[timeframe]
        if len(values) < 2:
            continue
        latest = values.iloc[-1]
        prior = values.iloc[-2]
        # `within 1 bars` means the cross may happen on the current completed
        # bar or the immediately previous completed bar.  Keep three rows so
        # both cross candidates retain their preceding comparison bar.
        recent = values.tail(3)
        exu = bool(latest["ema_9"] > latest["ema_20"])
        exd = bool(latest["ema_9"] < latest["ema_20"])
        # ThinkScript `crosses ... within 1 bars` is true for a cross on the
        # current bar or the immediately preceding bar.
        def crossed_above(left: str, right: str) -> bool:
            flags = (recent[left] > recent[right]) & (recent[left].shift(1) <= recent[right].shift(1))
            return bool(flags.tail(2).fillna(False).any())

        def crossed_below(left: str, right: str) -> bool:
            flags = (recent[left] < recent[right]) & (recent[left].shift(1) >= recent[right].shift(1))
            return bool(flags.tail(2).fillna(False).any())

        exu1 = crossed_above("ema_9", "ema_20")
        exd1 = crossed_below("ema_9", "ema_20")
        exu2 = crossed_above("ema_4", "ema_8")
        exd2 = crossed_below("ema_4", "ema_8")
        macd_up = crossed_above("macd_value", "macd_avg")
        macd_down = crossed_below("macd_value", "macd_avg")

        if exd1:
            background = "magenta"
        elif macd_down and exd:
            background = "red"
        elif exd2 and exd:
            background = "light_red"
        elif macd_down:
            background = "plum"
        elif exu1:
            background = "cyan"
        elif macd_up and exu:
            background = "green"
        elif exu2 and exu:
            background = "lime"
        elif macd_up:
            background = "dark_green"
        else:
            background = "black"

        fast_d = float(latest.get("fast_d", 0.0))
        if macd_down and exu:
            label_color = "violet"
        elif macd_up and exd:
            label_color = "downtick"
        elif exd1 or exu1 or (exd2 and exd) or (exu2 and exu) or (macd_down and exd) or (macd_up and exu):
            label_color = "black"
        elif exu and fast_d >= 90:
            label_color = "dark_green"
        elif exd and fast_d <= 10:
            label_color = "plum"
        elif exu:
            label_color = "cyan"
        elif exd:
            label_color = "magenta"
        else:
            label_color = "orange"

        states.append(
            {
                "timeframe": timeframe,
                "background": background,
                "labelColor": label_color,
                "stochasticFastD": int(round(fast_d)),
                "ema4Above8": bool(latest["ema_4"] > latest["ema_8"]),
                "ema9Above20": exu,
                "updatedAt": int(pd.Timestamp(latest["signal_time"]).timestamp()),
            }
        )
        if background not in {"cyan", "yellow"}:
            continue
        family = "9x20" if background == "cyan" else "4x8"
        higher = calculated[higher_timeframe[timeframe]]
        if higher.empty:
            higher_bullish = False
        else:
            higher_latest = higher.iloc[-1]
            higher_bullish = bool(
                higher_latest["ema_9"] >= higher_latest["ema_20"]
                if family == "9x20"
                else higher_latest["ema_4"] >= higher_latest["ema_8"]
            )
        label = f"{'CALL' if higher_bullish else 'C'}{timeframe}"
        signals.append(
            {
                "time": int(pd.Timestamp(latest["signal_time"]).timestamp()),
                "family": family,
                "color": background,
                "timeframe": timeframe,
                "direction": "CALL",
                "label": label,
                "liveForming": True,
            }
        )

    timeframes = sorted({signal["timeframe"] for signal in signals})
    families = sorted({signal["family"] for signal in signals})
    return {
        "signals": signals,
        "states": states,
        "bullishSignals": signals,
        "bullishSignalPass": bool(signals),
        "bullishSignalLabels": [f"{signal['label']} {signal['color']}" for signal in signals],
        "bullishTimeframes": timeframes,
        "bullishFamilies": families,
        "bullishBoth2H4H": "2H" in timeframes and "4H" in timeframes,
        "mode": "current_tos_watchlist_background",
        "sourceTimeframe": "5Min",
    }


def _group_mtf_call_signals(signals: list[dict]) -> dict:
    """Collapse raw MTF CALL crosses into the five scanner groups shown in the UI."""
    color_order = {"yellow": 0, "cyan": 1}

    def colors_for(label: str) -> list[str]:
        colors = {
            str(signal.get("color") or "").lower()
            for signal in signals
            if str(signal.get("label") or "").upper() == label
            and str(signal.get("color") or "").lower() in color_order
        }
        return sorted(colors, key=color_order.get)

    call_2h = colors_for("CALL2H")
    call_4h = colors_for("CALL4H")
    c_2h = colors_for("C2H")
    c_4h = colors_for("C4H")
    groups: list[dict] = []

    if call_2h and call_4h:
        both_colors = sorted(set(call_2h + call_4h), key=color_order.get)
        groups.append({"group": "BOTH CALL2H & CALL4H", "colors": both_colors})
    if set(call_2h) == {"yellow", "cyan"}:
        groups.append({"group": "CALL2H", "colors": call_2h})
    if set(call_4h) == {"yellow", "cyan"}:
        groups.append({"group": "CALL4H", "colors": call_4h})
    if c_2h:
        groups.append({"group": "C2H", "colors": c_2h})
    if c_4h:
        groups.append({"group": "C4H", "colors": c_4h})

    return {
        "groups": groups,
        "labels": [f"{item['group']} {'+'.join(item['colors'])}" for item in groups],
        "bothCall2H4H": bool(call_2h and call_4h),
    }


def _tos_mtf_ema_signal_payload(frame: pd.DataFrame) -> dict:
    """Return the two supplied TOS MTF EMA studies projected onto intraday bars.

    The 4x8 study runs at 15m, 30m, 1h, 2h and 4h.  The 9x20 study begins
    at 30m, then uses 1h, 2h and 4h.  Each cross is labelled with the same
    C/CALL or P/PUT confirmation convention used by the source TOS studies.
    """
    aggregates = {
        "15": _aggregate_mtf_bars(frame, 15),
        "30": _aggregate_mtf_bars(frame, 30),
        "1H": _aggregate_mtf_bars(frame, 60),
        "2H": _aggregate_mtf_bars(frame, 120),
        "4H": _aggregate_mtf_bars(frame, 240),
        "1D": _aggregate_mtf_bars(frame, 1440),
    }
    families = [
        {"family": "4x8", "fast": 4, "slow": 8, "color": "yellow"},
        {"family": "9x20", "fast": 9, "slow": 20, "color": "cyan"},
    ]
    study_timeframes = {
        "4x8": [("15", "30"), ("30", "1H"), ("1H", "2H"), ("2H", "4H"), ("4H", "1D")],
        "9x20": [("30", "1H"), ("1H", "2H"), ("2H", "4H"), ("4H", "1D")],
    }
    signals: list[dict] = []
    states: list[dict] = []

    for family in families:
        family_frames: dict[str, pd.DataFrame] = {}
        for timeframe, aggregate in aggregates.items():
            calculated = aggregate.copy()
            if calculated.empty:
                family_frames[timeframe] = calculated
                continue
            calculated["fast_ema"] = calculated["close"].ewm(span=family["fast"], adjust=False).mean()
            calculated["slow_ema"] = calculated["close"].ewm(span=family["slow"], adjust=False).mean()
            calculated["bullish"] = calculated["fast_ema"] >= calculated["slow_ema"]
            family_frames[timeframe] = calculated

        for timeframe, higher_timeframe in study_timeframes[family["family"]]:
            calculated = family_frames[timeframe]
            if calculated.empty:
                continue
            latest = calculated.iloc[-1]
            states.append(
                {
                    "family": family["family"],
                    "color": family["color"],
                    "timeframe": timeframe,
                    "direction": "CALL" if bool(latest["bullish"]) else "PUT",
                    "fastEma": round(float(latest["fast_ema"]), 4),
                    "slowEma": round(float(latest["slow_ema"]), 4),
                    "updatedAt": int(pd.Timestamp(latest["signal_time"]).timestamp()),
                }
            )

            prior_bullish = calculated["bullish"].shift(1)
            cross_up = calculated["bullish"] & prior_bullish.eq(False)
            cross_down = ~calculated["bullish"] & prior_bullish.eq(True)
            higher = family_frames[higher_timeframe]
            for row_index in calculated.index[cross_up | cross_down]:
                row = calculated.loc[row_index]
                direction = "CALL" if bool(cross_up.loc[row_index]) else "PUT"
                # Align confirmation to the last *completed* higher-timeframe
                # candle.  Filtering by bucket start time could accidentally
                # read a higher candle whose final close happened later than
                # this source cross (look-ahead), producing the wrong C/CALL
                # or P/PUT label on the chart.
                higher_rows = higher[higher["signal_time"] <= row["signal_time"]] if not higher.empty else higher
                higher_aligned = (
                    bool(higher_rows.iloc[-1]["bullish"]) == (direction == "CALL")
                    if not higher_rows.empty
                    else False
                )
                label = direction if higher_aligned else ("C" if direction == "CALL" else "P")
                signals.append(
                    {
                        "time": int(pd.Timestamp(row["signal_time"]).timestamp()),
                        "family": family["family"],
                        "color": family["color"],
                        "timeframe": timeframe,
                        "direction": direction,
                        "label": f"{label}{timeframe}",
                        "fastEma": round(float(row["fast_ema"]), 4),
                        "slowEma": round(float(row["slow_ema"]), 4),
                        "liveForming": True,
                    }
                )

    ordered_signals = sorted(signals, key=lambda item: (item["time"], item["family"], item["timeframe"]))
    latest_market_date = None
    if frame is not None and not frame.empty and "timestamp" in frame.columns:
        latest_timestamp = pd.Timestamp(frame["timestamp"].dropna().max())
        if latest_timestamp.tzinfo is None:
            latest_timestamp = latest_timestamp.tz_localize(EASTERN_TZ)
        else:
            latest_timestamp = latest_timestamp.tz_convert(EASTERN_TZ)
        latest_market_date = latest_timestamp.date()
    session_bullish_signals = [
        signal
        for signal in ordered_signals
        if signal["direction"] == "CALL"
        and (
            latest_market_date is None
            or pd.to_datetime(signal["time"], unit="s", utc=True).tz_convert(EASTERN_TZ).date() == latest_market_date
        )
    ]
    latest_state_times = {
        (state["family"], state["timeframe"]): int(state["updatedAt"])
        for state in states
    }
    bullish_signals = [
        signal
        for signal in session_bullish_signals
        if int(signal["time"]) == latest_state_times.get((signal["family"], signal["timeframe"]))
    ]
    grouped_signals = _group_mtf_call_signals(bullish_signals)
    bullish_timeframes = sorted({signal["timeframe"] for signal in bullish_signals})
    bullish_families = sorted({signal["family"] for signal in bullish_signals})
    return {
        "signals": ordered_signals,
        "states": states,
        "mode": "live_forming_5m_projection",
        "sourceTimeframe": "5Min",
        "bullishSignals": bullish_signals,
        "sessionBullishSignals": session_bullish_signals,
        "bullishSignalPass": bool(grouped_signals["groups"]),
        "bullishSignalLabels": grouped_signals["labels"],
        "bullishSignalGroups": grouped_signals["groups"],
        "bullishTimeframes": bullish_timeframes,
        "bullishFamilies": bullish_families,
        "bullishBoth2H4H": grouped_signals["bothCall2H4H"],
    }


def _tos_live_mtf_projection(frame: pd.DataFrame) -> dict:
    """Project TOS secondary-aggregation signals onto their 5-minute chart bars.

    On a historical 5-minute TOS chart, a secondary aggregation's final value is
    repeated across every primary bar in that aggregation bucket.  Consequently,
    a 30-minute and a 1-hour cross can both be drawn on the 10:00 ET five-minute
    candle.  Calculating the EMA recursively on every five-minute close shifts
    those events later and makes the UI disagree with TOS.

    This implementation first creates the real 30m/1h/2h/4h secondary candles,
    calculates each study independently, and then back-projects a cross to the
    opening five-minute candle of its secondary bucket.  The current incomplete
    bucket uses its latest available close, matching the repainting behaviour of
    an open TOS secondary candle.
    """
    required = {"timestamp", "close"}
    if frame is None or frame.empty or not required.issubset(frame.columns):
        return {"signals": [], "states": [], "mode": "tos_secondary_bucket_projection", "sourceTimeframe": "5Min", "bullishSignals": [], "sessionBullishSignals": [], "bullishSignalPass": False, "bullishSignalLabels": [], "bullishSignalGroups": [], "bullishTimeframes": [], "bullishFamilies": [], "bullishBoth2H4H": False}

    source = frame[["timestamp", "close"]].copy()
    source["timestamp"] = pd.to_datetime(source["timestamp"], errors="coerce")
    if getattr(source["timestamp"].dt, "tz", None) is None:
        source["timestamp"] = source["timestamp"].dt.tz_localize(EASTERN_TZ, nonexistent="shift_forward", ambiguous="NaT")
    else:
        source["timestamp"] = source["timestamp"].dt.tz_convert(EASTERN_TZ)
    source["close"] = pd.to_numeric(source["close"], errors="coerce")
    source = source.dropna().sort_values("timestamp").drop_duplicates("timestamp", keep="last")
    if source.empty:
        return _tos_live_mtf_projection(pd.DataFrame())

    # The long-history request is already 5m.  If a caller supplies 1m data,
    # turn it into 5m first so the chart and the MTF source share timestamps.
    spacing = source["timestamp"].diff().dt.total_seconds().dropna().median()
    if pd.notna(spacing) and spacing < 240:
        source = _aggregate_mtf_bars(source.assign(open=source["close"], high=source["close"], low=source["close"], volume=0), 5)[["signal_time", "close"]].rename(columns={"signal_time": "timestamp"})

    def secondary_series(minutes: int, fast_length: int, slow_length: int) -> pd.DataFrame:
        values = (
            source.set_index("timestamp")["close"]
            .resample(f"{minutes}min", label="left", closed="left")
            .last()
            .dropna()
            .rename("close")
            .reset_index()
        )
        if values.empty:
            return values
        values["fast_ema"] = values["close"].ewm(span=fast_length, adjust=False).mean()
        values["slow_ema"] = values["close"].ewm(span=slow_length, adjust=False).mean()
        values["difference"] = values["fast_ema"] - values["slow_ema"]
        values["bullish"] = values["difference"] >= 0
        return values

    family_specs = (
        ("4x8", 4, 8, "yellow", (("15", 15, "30", 30), ("30", 30, "1H", 60), ("1H", 60, "2H", 120), ("2H", 120, "4H", 240), ("4H", 240, "1D", 1440))),
        ("9x20", 9, 20, "cyan", (("30", 30, "1H", 60), ("1H", 60, "2H", 120), ("2H", 120, "4H", 240), ("4H", 240, "1D", 1440))),
    )
    signals: list[dict] = []
    states: list[dict] = []
    for family, fast_length, slow_length, color, pairs in family_specs:
        required_minutes = sorted({value for pair in pairs for value in (pair[1], pair[3])})
        projected = {minutes: secondary_series(minutes, fast_length, slow_length) for minutes in required_minutes}
        for timeframe, minutes, higher_timeframe, higher_minutes in pairs:
            values = projected[minutes]
            higher = projected[higher_minutes]
            if values.empty:
                continue
            latest = values.iloc[-1]
            states.append({"family": family, "color": color, "timeframe": timeframe, "direction": "CALL" if bool(latest.bullish) else "PUT", "fastEma": round(float(latest.fast_ema), 4), "slowEma": round(float(latest.slow_ema), 4), "updatedAt": int(pd.Timestamp(latest.timestamp).timestamp())})
            prior_difference = values["difference"].shift(1)
            cross_up = values["difference"].gt(0) & prior_difference.le(0)
            cross_down = values["difference"].lt(0) & prior_difference.ge(0)
            change_rows = values[cross_up | cross_down]
            for row in change_rows.itertuples(index=False):
                direction = "CALL" if float(row.difference) > 0 else "PUT"
                # The higher secondary study is also backfilled over its whole
                # bucket, so use the bucket containing the signal time.  This
                # is what changes C30 into CALL30 (and C1H into CALL1H) when
                # the higher-timeframe trend confirms at the same 5m candle.
                higher_rows = higher[higher["timestamp"] <= row.timestamp]
                higher_bullish = bool(higher_rows.iloc[-1]["bullish"]) if not higher_rows.empty else False
                confirmed = higher_bullish == (direction == "CALL")
                short_label = direction if confirmed else ("C" if direction == "CALL" else "P")
                signals.append({
                    "time": int(pd.Timestamp(row.timestamp).timestamp()),
                    "family": family,
                    "color": color,
                    "timeframe": timeframe,
                    "direction": direction,
                    "label": f"{short_label}{timeframe}",
                    "compact": short_label in {"C", "P"},
                    "fastEma": round(float(row.fast_ema), 4),
                    "slowEma": round(float(row.slow_ema), 4),
                    "liveForming": True,
                    "secondaryBucketStart": True,
                })

    timeframe_rank = {"15": 0, "30": 1, "1H": 2, "2H": 3, "4H": 4}
    family_rank = {"4x8": 0, "9x20": 1}
    ordered = sorted(
        signals,
        key=lambda item: (
            item["time"],
            family_rank.get(item["family"], 99),
            timeframe_rank.get(item["timeframe"], 99),
        ),
    )
    latest_date = pd.Timestamp(source.iloc[-1]["timestamp"]).tz_convert(EASTERN_TZ).date()
    session_bullish = [item for item in ordered if item["direction"] == "CALL" and pd.to_datetime(item["time"], unit="s", utc=True).tz_convert(EASTERN_TZ).date() == latest_date]
    grouped = _group_mtf_call_signals(session_bullish)
    return {"signals": ordered, "states": states, "mode": "tos_secondary_bucket_projection", "sourceTimeframe": "5Min", "bullishSignals": session_bullish, "sessionBullishSignals": session_bullish, "bullishSignalPass": bool(grouped["groups"]), "bullishSignalLabels": grouped["labels"], "bullishSignalGroups": grouped["groups"], "bullishTimeframes": sorted({item["timeframe"] for item in session_bullish}), "bullishFamilies": sorted({item["family"] for item in session_bullish}), "bullishBoth2H4H": grouped["bothCall2H4H"]}


def _tos_session_mtf_ema_signal_payload(frame: pd.DataFrame) -> dict:
    """Match the supplied TOS studies: completed secondary bars from 05:00 ET.

    The source script's 05:00 session anchor makes 2H signals settle at roughly
    11:00 ET and 4H signals at roughly 13:00 ET.  Projecting a still-forming
    higher candle made those same labels drift to later points on the UI chart.
    """
    aggregates = {
        "15": _aggregate_mtf_bars(frame, 15, origin_offset="5h"),
        "30": _aggregate_mtf_bars(frame, 30, origin_offset="5h"),
        "1H": _aggregate_mtf_bars(frame, 60, origin_offset="5h"),
        "2H": _aggregate_mtf_bars(frame, 120, origin_offset="5h"),
        "4H": _aggregate_mtf_bars(frame, 240, origin_offset="5h"),
        "1D": _aggregate_mtf_bars(frame, 1440, origin_offset="5h"),
    }
    specs = (
        ("4x8", 4, 8, "yellow", (("15", "30"), ("30", "1H"), ("1H", "2H"), ("2H", "4H"), ("4H", "1D"))),
        ("9x20", 9, 20, "cyan", (("30", "1H"), ("1H", "2H"), ("2H", "4H"), ("4H", "1D"))),
    )
    signals: list[dict] = []
    states: list[dict] = []
    for family, fast, slow, color, pairs in specs:
        series: dict[str, pd.DataFrame] = {}
        for timeframe, raw in aggregates.items():
            values = raw.copy()
            if not values.empty:
                values["fast_ema"] = values["close"].ewm(span=fast, adjust=False).mean()
                values["slow_ema"] = values["close"].ewm(span=slow, adjust=False).mean()
                values["bullish"] = values["fast_ema"] >= values["slow_ema"]
            series[timeframe] = values
        for timeframe, higher_timeframe in pairs:
            values = series[timeframe]
            if values.empty:
                continue
            latest = values.iloc[-1]
            states.append({"family": family, "color": color, "timeframe": timeframe, "direction": "CALL" if bool(latest.bullish) else "PUT", "fastEma": round(float(latest.fast_ema), 4), "slowEma": round(float(latest.slow_ema), 4), "updatedAt": int(pd.Timestamp(latest.signal_time).timestamp())})
            prior = values["bullish"].shift(1)
            crossings = values[(values["bullish"] & prior.eq(False)) | (~values["bullish"] & prior.eq(True))]
            higher = series[higher_timeframe]
            for row in crossings.itertuples(index=False):
                direction = "CALL" if bool(row.bullish) else "PUT"
                available_higher = higher[higher["signal_time"] <= row.signal_time] if not higher.empty else higher
                confirmed = (bool(available_higher.iloc[-1]["bullish"]) == (direction == "CALL")) if not available_higher.empty else False
                prefix = direction if confirmed else ("C" if direction == "CALL" else "P")
                signals.append({"time": int(pd.Timestamp(row.signal_time).timestamp()), "family": family, "color": color, "timeframe": timeframe, "direction": direction, "label": f"{prefix}{timeframe}", "fastEma": round(float(row.fast_ema), 4), "slowEma": round(float(row.slow_ema), 4), "liveForming": False})
    ordered = sorted(signals, key=lambda item: (item["time"], item["family"], item["timeframe"]))
    latest_date = None
    if frame is not None and not frame.empty and "timestamp" in frame:
        stamp = pd.Timestamp(pd.to_datetime(frame["timestamp"], errors="coerce").dropna().max())
        latest_date = (stamp.tz_localize(EASTERN_TZ) if stamp.tzinfo is None else stamp.tz_convert(EASTERN_TZ)).date()
    session_bullish = [item for item in ordered if item["direction"] == "CALL" and (latest_date is None or pd.to_datetime(item["time"], unit="s", utc=True).tz_convert(EASTERN_TZ).date() == latest_date)]
    grouped = _group_mtf_call_signals(session_bullish)
    return {"signals": ordered, "states": states, "mode": "tos_completed_secondary_5am_et", "sourceTimeframe": "5Min", "bullishSignals": session_bullish, "sessionBullishSignals": session_bullish, "bullishSignalPass": bool(grouped["groups"]), "bullishSignalLabels": grouped["labels"], "bullishSignalGroups": grouped["groups"], "bullishTimeframes": sorted({item["timeframe"] for item in session_bullish}), "bullishFamilies": sorted({item["family"] for item in session_bullish}), "bullishBoth2H4H": grouped["bothCall2H4H"]}


# Public chart API: calculate each TOS secondary aggregation independently and
# project its result to the opening primary bar of that aggregation bucket.
def _tos_mtf_ema_signal_payload(frame: pd.DataFrame) -> dict:
    return _tos_live_mtf_projection(frame)


def scan_live_price_change(ticker: str, bars, threshold_pct: float = 0.5) -> dict:
    """
    bars should be candles for the target timeframe.
    bars[-1] = current live candle
    bars[-3] = 2 bars ago
    """
    if isinstance(bars, pd.DataFrame):
        rows = bars.to_dict("records")
    else:
        rows = list(bars or [])
    if len(rows) < 3:
        return {
            "ticker": str(ticker or "").upper(),
            "current_price": None,
            "price_2_bars_ago": None,
            "price_change_pct": None,
            "signal": False,
        }

    current_price = float(rows[-1].get("close") or 0.0)
    price_2_bars_ago = float(rows[-3].get("close") or 0.0)
    price_change_pct = tos_price_change_value(current_price, price_2_bars_ago)
    if price_change_pct is None:
        price_change_pct = None
        signal = False
    else:
        signal = price_change_scan(current_price, price_2_bars_ago, threshold_pct=threshold_pct)

    return {
        "ticker": str(ticker or "").upper(),
        "current_price": round(current_price, 4),
        "price_2_bars_ago": round(price_2_bars_ago, 4),
        "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
        "signal": signal,
    }


def scan_live_4h_volume(ticker: str, bars, threshold_pct: float = 0.5) -> dict:
    """
    bars should be 4H candles.
    bars[-1] = current live 4H candle
    bars[-3] = 2 bars ago
    """
    if isinstance(bars, pd.DataFrame):
        rows = bars.to_dict("records")
    else:
        rows = list(bars or [])
    if len(rows) < 3:
        return {
            "ticker": str(ticker or "").upper(),
            "current_volume": None,
            "volume_2_bars_ago": None,
            "volume_change_pct": None,
            "signal": False,
        }

    current_volume = float(rows[-1].get("volume") or 0.0)
    volume_2_bars_ago = float(rows[-3].get("volume") or 0.0)
    volume_change_pct = tos_price_change_value(current_volume, volume_2_bars_ago)
    if volume_change_pct is None:
        volume_change_pct = None
        signal = False
    else:
        signal = volume_scan(current_volume, volume_2_bars_ago, threshold_pct=threshold_pct)

    return {
        "ticker": str(ticker or "").upper(),
        "current_volume": int(current_volume) if current_volume.is_integer() else current_volume,
        "volume_2_bars_ago": int(volume_2_bars_ago) if volume_2_bars_ago.is_integer() else volume_2_bars_ago,
        "volume_change_pct": round(volume_change_pct, 2) if volume_change_pct is not None else None,
        "signal": signal,
    }


def ema_cloud_snapshot(bars: pd.DataFrame) -> dict:
    empty = {
        "state": "UNKNOWN",
        "bullish": False,
        "bearish": False,
        "ema_9": None,
        "ema_21": None,
        "ema_50": None,
    }
    if bars is None or bars.empty or "close" not in bars.columns:
        return empty
    frame = bars.dropna(subset=["close"]).copy()
    if frame.empty:
        return empty
    frame["ema_9"] = ema(frame["close"], 9)
    frame["ema_21"] = ema(frame["close"], 21)
    frame["ema_50"] = ema(frame["close"], 50)
    latest = frame.iloc[-1]
    ema_9 = float(latest["ema_9"])
    ema_21 = float(latest["ema_21"])
    ema_50 = float(latest["ema_50"])
    bullish = bool(ema_9 > ema_21 > ema_50)
    bearish = bool(ema_9 < ema_21 < ema_50)
    return {
        "state": "BULLISH" if bullish else "BEARISH" if bearish else "MIXED",
        "bullish": bullish,
        "bearish": bearish,
        "ema_9": round(ema_9, 4),
        "ema_21": round(ema_21, 4),
        "ema_50": round(ema_50, 4),
    }


def tos_relative_volume_scan(
    bars: pd.DataFrame,
    rel_vol_length: int = 50,
    num_dev: float = 3.0,
) -> dict:
    if bars is None or bars.empty or len(bars) < max(int(rel_vol_length), 1):
        return {
            "raw_rel_vol": None,
            "avg_volume": None,
            "stdev_volume": None,
            "buying": None,
            "selling": None,
            "buying_gt_selling": False,
            "is_above_threshold": False,
            "signal": False,
        }
    frame = bars.dropna(subset=["open", "high", "low", "close", "volume"]).copy()
    if frame.empty or len(frame) < max(int(rel_vol_length), 1):
        return {
            "raw_rel_vol": None,
            "avg_volume": None,
            "stdev_volume": None,
            "buying": None,
            "selling": None,
            "buying_gt_selling": False,
            "is_above_threshold": False,
            "signal": False,
        }
    length = max(int(rel_vol_length), 1)
    latest = frame.iloc[-1]
    window = frame["volume"].tail(length)
    avg_volume = float(window.mean()) if not window.empty else 0.0
    stdev_volume = float(window.std(ddof=0)) if not window.empty else 0.0
    current_volume = float(latest.get("volume") or 0.0)
    raw_rel_vol = None if stdev_volume <= 0 else (current_volume - avg_volume) / stdev_volume
    high = float(latest.get("high") or 0.0)
    low = float(latest.get("low") or 0.0)
    close = float(latest.get("close") or 0.0)
    spread = high - low
    if spread <= 0:
        buying = current_volume / 2.0
        selling = current_volume / 2.0
    else:
        buying = current_volume * ((close - low) / spread)
        selling = current_volume * ((high - close) / spread)
    buying_gt_selling = bool(buying > selling)
    is_above_threshold = bool(raw_rel_vol is not None and raw_rel_vol >= float(num_dev))
    return {
        "raw_rel_vol": round(raw_rel_vol, 2) if raw_rel_vol is not None else None,
        "avg_volume": round(avg_volume, 2),
        "stdev_volume": round(stdev_volume, 2),
        "buying": round(buying, 2),
        "selling": round(selling, 2),
        "buying_gt_selling": buying_gt_selling,
        "is_above_threshold": is_above_threshold,
        "signal": bool(is_above_threshold and buying_gt_selling),
    }


def fast_momentum_confirmation(
    bars: pd.DataFrame,
    now: object | None = None,
    volume_ratio_threshold: float = 1.25,
    buying_pressure_threshold_pct: float = 55.0,
    breakout_buffer_pct: float = 0.05,
    minimum_elapsed_seconds: float = 30.0,
) -> dict:
    """Score the current live 5-minute candle without blocking an entry."""
    if bars is None or bars.empty or len(bars) < 2:
        return {"score": 0, "status": "UNAVAILABLE", "projected_volume": None, "volume_ratio": None, "buying_pressure_pct": None, "previous_high": None, "volume_pass": False, "buying_pressure_pass": False, "previous_high_break_pass": False, "elapsed_seconds": None}
    ordered = bars.dropna(subset=["timestamp", "high", "low", "close", "volume"]).sort_values("timestamp")
    if len(ordered) < 2:
        return fast_momentum_confirmation(pd.DataFrame())
    latest = ordered.iloc[-1]
    previous = ordered.iloc[-2]
    current_volume = max(float(latest.get("volume") or 0.0), 0.0)
    previous_volume = max(float(previous.get("volume") or 0.0), 0.0)
    latest_timestamp = pd.Timestamp(latest["timestamp"])
    now_timestamp = pd.Timestamp(now) if now is not None else pd.Timestamp.now(tz=latest_timestamp.tz)
    if latest_timestamp.tz is not None and now_timestamp.tz is None:
        now_timestamp = now_timestamp.tz_localize(latest_timestamp.tz)
    elif latest_timestamp.tz is None and now_timestamp.tz is not None:
        now_timestamp = now_timestamp.tz_localize(None)
    elif latest_timestamp.tz is not None and now_timestamp.tz is not None:
        now_timestamp = now_timestamp.tz_convert(latest_timestamp.tz)
    age_seconds = float((now_timestamp - latest_timestamp).total_seconds())
    if -300.0 <= age_seconds <= 0.0:
        elapsed_seconds = 300.0 + age_seconds
    elif 0.0 <= age_seconds < 300.0:
        elapsed_seconds = age_seconds
    else:
        elapsed_seconds = 300.0
    elapsed_seconds = max(float(minimum_elapsed_seconds), min(elapsed_seconds, 300.0))
    elapsed_fraction = elapsed_seconds / 300.0
    projected_volume = current_volume / elapsed_fraction if elapsed_fraction > 0 else current_volume
    volume_ratio = projected_volume / previous_volume if previous_volume > 0 else None
    high = float(latest.get("high") or 0.0)
    low = float(latest.get("low") or 0.0)
    close = float(latest.get("close") or 0.0)
    candle_range = high - low
    buying_pressure_pct = 50.0 if candle_range <= 0 else ((close - low) / candle_range) * 100.0
    previous_high = float(previous.get("high") or 0.0)
    required_break_price = previous_high * (1.0 + (float(breakout_buffer_pct) / 100.0))
    volume_pass = bool(volume_ratio is not None and volume_ratio >= float(volume_ratio_threshold))
    buying_pressure_pass = bool(buying_pressure_pct >= float(buying_pressure_threshold_pct))
    previous_high_break_pass = bool(previous_high > 0 and close >= required_break_price)
    score = int(volume_pass) + int(buying_pressure_pass) + int(previous_high_break_pass)
    status = "STRONG" if score == 3 else "CONFIRMED" if score == 2 else "DEVELOPING" if score == 1 else "WEAK"
    return {
        "score": score,
        "status": status,
        "projected_volume": round(projected_volume, 2),
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "buying_pressure_pct": round(max(min(buying_pressure_pct, 100.0), 0.0), 2),
        "previous_high": round(previous_high, 4),
        "volume_pass": volume_pass,
        "buying_pressure_pass": buying_pressure_pass,
        "previous_high_break_pass": previous_high_break_pass,
        "elapsed_seconds": round(elapsed_seconds, 2),
    }

@dataclass(slots=True)
class ScanResult:
    symbol: str
    strategy_name: str
    setup_name: str
    score: int
    last_price: float
    average_volume: int
    today_volume: int
    rvol: float
    above_vwap: bool
    ema_stack: bool
    five_min_cloud_state: str
    five_min_ema_9: float | None
    five_min_ema_21: float | None
    five_min_ema_50: float | None
    four_hour_cloud_state: str
    four_hour_cloud_bullish: bool
    four_hour_ema_9: float | None
    four_hour_ema_21: float | None
    four_hour_ema_50: float | None
    cloud_alignment_pass: bool
    cloud_alignment_action: str
    breakout: bool
    breakout_close_confirmed: bool
    volume_trend: bool
    fast_momentum_score: int
    fast_momentum_status: str
    projected_5m_volume: float | None
    projected_5m_volume_ratio: float | None
    buying_pressure_pct: float | None
    previous_5m_high: float | None
    fast_volume_pass: bool
    fast_buying_pressure_pass: bool
    fast_previous_high_break_pass: bool
    price_action_pass: bool
    one_hour_close_change_pct: float | None
    one_hour_price_change_pct: float | None
    one_hour_current_price: float | None
    one_hour_price_2_bars_ago: float | None
    four_hour_volume_change_pct: float | None
    four_hour_price_change_pct: float | None
    four_hour_current_price: float | None
    four_hour_price_2_bars_ago: float | None
    four_hour_current_volume: int | float | None
    four_hour_volume_2_bars_ago: int | float | None
    four_hour_volume_signal: bool
    one_hour_close_pass: bool
    one_hour_price_change_pass: bool
    four_hour_price_change_pass: bool
    four_hour_volume_pass: bool
    stock_signal_gate_active: bool
    stock_all_conditions_pass: bool
    mtf_bullish_signal_pass: bool
    mtf_bullish_signal_labels: str
    mtf_bullish_signal_families: str
    mtf_bullish_signal_timeframes: str
    mtf_bullish_signal_both_2h_4h: bool
    market_trend: bool
    intraday_change_pct: float
    session_change_pct: float
    session_change_pass: bool
    close_near_high: bool
    not_overextended: bool
    ema9_retest_5m: bool
    extension_above_ema9_pct: float
    first_5m_bullish: bool
    first_5m_close_above_vwap: bool
    orb_breakout: bool
    catalyst_ready: bool
    atr_value: float
    premarket_high: float
    previous_day_high: float
    previous_day_low: float
    opening_range_high: float
    opening_range_low: float
    trigger_level: float
    trigger_source: str
    entry: float
    stop_loss: float
    target: float
    risk_per_share: float
    tos_rvol_timeframes: str
    tos_rvol_5m: float | None
    tos_rvol_15m: float | None
    tos_rvol_30m: float | None
    tos_rvol_1h: float | None
    tos_rvol_2h: float | None
    tos_rvol_4h: float | None
    tos_rvol_1d: float | None
    tos_rvol_any_pass: bool
    tos_rvol_5m_early_alert: bool


class MomentumScanner:
    STRATEGY_NAME = "Momentum Price Action Trend"
    BATCH_SIZE = 50
    TOS_RVOL_TIMEFRAMES = [
        ("5m", 5, 15),
        ("15m", 15, 15),
        ("30m", 30, 15),
        ("1h", 60, 15),
        ("2h", 120, 15),
        ("4h", 240, 20),
    ]
    TOS_RVOL_NEGATIVE_VETO_TIMEFRAMES = ()

    def __init__(self, client: AlpacaClient | None = None) -> None:
        self.client = client or create_market_data_client()
        self.settings = settings.scanner
        self.risk_manager = RiskManager()

    def run(
        self,
        symbols: list[str] | None = None,
        max_results: int | None = None,
        ignore_one_hour_price_change: bool = False,
        ignore_four_hour_price_change: bool = False,
        ignore_four_hour_volume: bool = False,
        ignore_ema9_retest: bool = True,
        rvol_confirmation_threshold: float | None = None,
        rvol_confirmation_thresholds: dict[str, float] | None = None,
        allow_mtf_signal_setup: bool = False,
        require_rvol_confirmation: bool = True,
    ) -> pd.DataFrame:
        universe = symbols or self.settings.default_universe
        universe, quote_map = self._fast_prefilter_universe(universe)
        spy_above_vwap = self._safe_spy_above_vwap()

        rows: list[dict] = []
        for start in range(0, len(universe), self.BATCH_SIZE):
            batch = universe[start:start + self.BATCH_SIZE]
            intraday = self.client.get_intraday_bars(
                batch,
                timeframe=self.settings.intraday_timeframe,
                days_back=max(self.settings.intraday_signal_lookback_days, 15),
            )
            daily = self.client.get_daily_bars(batch, lookback_days=self.settings.lookback_daily_bars)
            for symbol in batch:
                intraday_frame = intraday.get(symbol)
                daily_frame = daily.get(symbol)
                if intraday_frame is None or daily_frame is None or intraday_frame.empty or daily_frame.empty:
                    continue

                result = self._score_symbol(
                    symbol,
                    intraday_frame,
                    daily_frame,
                    spy_above_vwap,
                    ignore_one_hour_price_change=ignore_one_hour_price_change,
                    ignore_four_hour_price_change=ignore_four_hour_price_change,
                    ignore_four_hour_volume=ignore_four_hour_volume,
                    ignore_ema9_retest=ignore_ema9_retest,
                    rvol_confirmation_threshold=(
                        rvol_confirmation_thresholds.get(str(symbol).upper(), rvol_confirmation_threshold)
                        if rvol_confirmation_thresholds
                        else rvol_confirmation_threshold
                    ),
                    session_change_pct=(quote_map.get(str(symbol).upper()) or {}).get("change_pct"),
                    allow_mtf_signal_setup=allow_mtf_signal_setup,
                    require_rvol_confirmation=require_rvol_confirmation,
                )
                if result:
                    rows.append(asdict(result))

        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(rows).sort_values(["score", "rvol"], ascending=[False, False]).reset_index(drop=True)
        limit = self.settings.max_results if max_results is None else int(max_results)
        return frame if limit <= 0 else frame.head(limit)

    def run_tos_scan(
        self,
        symbols: list[str] | None = None,
        max_results: int | None = None,
        rvol_confirmation_threshold: float | None = None,
    ) -> pd.DataFrame:
        universe = symbols or self.settings.default_universe
        universe = [str(symbol).strip().upper() for symbol in universe if str(symbol).strip()]
        daily_map = self.client.get_daily_bars(universe, lookback_days=self.settings.lookback_daily_bars)
        rows: list[dict] = []

        for symbol in universe:
            signal_bars = self._tos_signal_source_bars(symbol)
            daily_frame = daily_map.get(symbol)
            if signal_bars.empty:
                continue
            if daily_frame is None or daily_frame.empty:
                continue
            one_hour = self._aggregate_signal_bars(signal_bars, 60)
            four_hour = self._aggregate_signal_bars(signal_bars, 240)
            five_min = self._aggregate_signal_bars(signal_bars, 5)
            if len(one_hour) < 3 or len(four_hour) < 3:
                continue

            five_min_cloud = ema_cloud_snapshot(five_min)
            four_hour_cloud = ema_cloud_snapshot(four_hour)
            cloud_alignment_pass = bool(five_min_cloud["bullish"] and four_hour_cloud["bullish"])
            cloud_alignment_action = (
                "TRADE - 4H + 5M BULLISH"
                if cloud_alignment_pass
                else "WAIT 5M RECLAIM"
                if four_hour_cloud["bullish"]
                else "NO TRADE - 4H NOT BULLISH"
                if five_min_cloud["bullish"]
                else "NO TRADE - CLOUDS NOT ALIGNED"
            )

            last_price = float(signal_bars.iloc[-1]["close"] or 0.0)
            price_pass = last_price >= float(self.settings.min_price)
            current_five_min = self._current_trading_day_frame(five_min)
            if len(current_five_min) < 2:
                continue
            current_five_min["calc_vwap"] = cumulative_vwap(current_five_min)
            latest_five_min = current_five_min.iloc[-1]
            fast_momentum = fast_momentum_confirmation(current_five_min)
            ema_stack = bool(five_min_cloud["bullish"])
            above_vwap = bool(float(latest_five_min["close"]) > float(latest_five_min["calc_vwap"]))
            ema_vwap_5m_pass = bool(ema_stack and above_vwap)
            fast_momentum_pass = bool(int(fast_momentum["score"]) >= 2)
            price_action_pass = bool(
                float(latest_five_min["close"]) > float(latest_five_min["open"])
                and fast_momentum["previous_high_break_pass"]
            )
            one_hour_result = scan_live_price_change(
                symbol,
                one_hour,
                threshold_pct=self.settings.min_one_hour_close_change_pct,
            )
            volume_result = scan_live_4h_volume(
                symbol,
                four_hour,
                threshold_pct=self.settings.min_four_hour_volume_change_pct,
            )
            tos_rvol_info = self._tos_rvol_timeframe_map(
                symbol,
                signal_bars,
                daily_frame,
                confirmation_threshold=rvol_confirmation_threshold,
            )
            tos_rvol_map = tos_rvol_info["map"]
            mtf_signal = _tos_mtf_ema_signal_payload(five_min)
            mtf_bullish_signal_pass = bool(mtf_signal.get("bullishSignalPass"))
            mtf_bullish_signal_labels = ", ".join(mtf_signal.get("bullishSignalLabels") or [])
            rvol_any_timeframe_pass = bool(
                tos_rvol_info["any_pass"] or tos_rvol_info["five_min_early_alert"]
            )
            all_tos_conditions_pass = bool(
                price_pass
                and mtf_bullish_signal_pass
                and one_hour_result["signal"]
                and volume_result["signal"]
                and ema_vwap_5m_pass
                and cloud_alignment_pass
                and rvol_any_timeframe_pass
                and fast_momentum_pass
                and price_action_pass
            )
            if not all_tos_conditions_pass:
                continue

            one_hour_change_pct = one_hour_result["price_change_pct"]
            volume_change_pct = volume_result["volume_change_pct"]
            rows.append(
                {
                    "symbol": str(symbol).upper(),
                    "strategy_family": "TOS Scanner",
                    "setup_name": "MTF EMA C/CALL 2H/4H",
                    "policy_status": "Matched",
                    "execution_route": "Scanner only",
                    "rejection_reason": "",
                    "score": round(float(one_hour_change_pct or 0.0), 2),
                    "final_score": round(float(one_hour_change_pct or 0.0), 2),
                    "rule_score": 100,
                    "last_price": round(float(last_price), 2),
                    "price_pass": True,
                    "candle_data_available": True,
                    "ema_stack": ema_stack,
                    "above_vwap": above_vwap,
                    "ema_vwap_5m_pass": ema_vwap_5m_pass,
                    "five_min_cloud_state": five_min_cloud["state"],
                    "five_min_ema_9": five_min_cloud["ema_9"],
                    "five_min_ema_21": five_min_cloud["ema_21"],
                    "five_min_ema_50": five_min_cloud["ema_50"],
                    "four_hour_cloud_state": four_hour_cloud["state"],
                    "four_hour_cloud_bullish": four_hour_cloud["bullish"],
                    "four_hour_ema_9": four_hour_cloud["ema_9"],
                    "four_hour_ema_21": four_hour_cloud["ema_21"],
                    "four_hour_ema_50": four_hour_cloud["ema_50"],
                    "cloud_alignment_pass": cloud_alignment_pass,
                    "cloud_alignment_action": cloud_alignment_action,
                    "one_hour_close_change_pct": one_hour_change_pct,
                    "one_hour_price_change_pct": one_hour_change_pct,
                    "one_hour_current_price": one_hour_result["current_price"],
                    "one_hour_price_2_bars_ago": one_hour_result["price_2_bars_ago"],
                    "one_hour_close_pass": bool(one_hour_result["signal"]),
                    "one_hour_price_change_pass": bool(one_hour_result["signal"]),
                    "tos_rvol_timeframes": tos_rvol_info["summary"],
                    "tos_rvol_5m": tos_rvol_map.get("5m", {}).get("raw_rel_vol"),
                    "tos_rvol_15m": tos_rvol_map.get("15m", {}).get("raw_rel_vol"),
                    "tos_rvol_30m": tos_rvol_map.get("30m", {}).get("raw_rel_vol"),
                    "tos_rvol_1h": tos_rvol_map.get("1h", {}).get("raw_rel_vol"),
                    "tos_rvol_2h": tos_rvol_map.get("2h", {}).get("raw_rel_vol"),
                    "tos_rvol_4h": tos_rvol_map.get("4h", {}).get("raw_rel_vol"),
                    "tos_rvol_1d": tos_rvol_map.get("1d", {}).get("raw_rel_vol"),
                    "tos_rvol_any_pass": tos_rvol_info["any_pass"],
                    "tos_rvol_5m_early_alert": tos_rvol_info["five_min_early_alert"],
                    "rvol_any_timeframe_pass": rvol_any_timeframe_pass,
                    "fast_momentum_score": int(fast_momentum["score"]),
                    "fast_momentum_status": fast_momentum["status"],
                    "fast_momentum_pass": fast_momentum_pass,
                    "fast_previous_high_break_pass": bool(fast_momentum["previous_high_break_pass"]),
                    "price_action_pass": price_action_pass,
                    "four_hour_current_volume": volume_result["current_volume"],
                    "four_hour_volume_2_bars_ago": volume_result["volume_2_bars_ago"],
                    "four_hour_volume_change_pct": volume_change_pct,
                    "four_hour_volume_signal": bool(volume_result["signal"]),
                    "four_hour_volume_pass": bool(volume_result["signal"]),
                    "stock_signal_gate_active": True,
                    "stock_all_conditions_pass": all_tos_conditions_pass,
                    "mtf_bullish_signal_pass": True,
                    "mtf_bullish_signal_labels": mtf_bullish_signal_labels,
                    "mtf_bullish_signal_families": ", ".join(mtf_signal.get("bullishFamilies") or []),
                    "mtf_bullish_signal_timeframes": ", ".join(mtf_signal.get("bullishTimeframes") or []),
                    "mtf_bullish_signal_both_2h_4h": bool(mtf_signal.get("bullishBoth2H4H")),
                    "trigger_source": (
                        f"TOS All: Last >= {self.settings.min_price:.2f}; "
                        f"1H close >= {self.settings.min_one_hour_close_change_pct:.2f}% vs 2 bars ago; "
                        f"4H volume >= {self.settings.min_four_hour_volume_change_pct:.2f}% vs 2 bars ago; "
                        f"5m MTF: {mtf_bullish_signal_labels}; EMA+VWAP; 5M+4H cloud; "
                        "RVOL any timeframe; fast momentum >= 2/3; bullish previous-high break"
                    ),
                    "entry": round(float(last_price), 2),
                    "stop_loss": None,
                    "target": None,
                    "risk_per_share": None,
                    "allowed": True,
                }
            )

        if not rows:
            return pd.DataFrame()

        frame = pd.DataFrame(rows).sort_values(
            ["one_hour_price_change_pct", "four_hour_volume_change_pct"],
            ascending=[False, False],
        ).reset_index(drop=True)
        limit = max(int(self.settings.max_results), 50) if max_results is None else int(max_results)
        return frame if limit <= 0 else frame.head(limit)

    def _tos_signal_source_bars(self, symbol: str) -> pd.DataFrame:
        target = str(symbol or "").strip().upper()
        if not target:
            return pd.DataFrame()
        get_chart_bars = getattr(self.client, "get_chart_bars", None)
        try:
            if callable(get_chart_bars):
                frame = get_chart_bars(target, timeframe="1Min", days_back=5)
            else:
                frame = self.client.get_intraday_bars([target], timeframe="1Min", days_back=5).get(target, pd.DataFrame())
        except Exception:
            return pd.DataFrame()
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame()
        bars = frame.copy()
        timestamps = pd.to_datetime(bars["timestamp"], errors="coerce")
        if getattr(timestamps.dt, "tz", None) is None:
            bars["timestamp"] = timestamps.dt.tz_localize(EASTERN_TZ, nonexistent="shift_forward", ambiguous="NaT")
        else:
            bars["timestamp"] = timestamps.dt.tz_convert(EASTERN_TZ)
        bars = bars.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
        if bars.empty:
            return pd.DataFrame()
        if not self.settings.signal_include_extended_hours:
            return self._regular_session_signal_bars(bars)
        minutes = (bars["timestamp"].dt.hour * 60) + bars["timestamp"].dt.minute
        mask = (
            (bars["timestamp"].dt.weekday < 5)
            & (minutes >= (4 * 60))
            & (minutes <= (20 * 60))
        )
        return bars.loc[mask].copy().reset_index(drop=True)

    def _signal_bars_per_period(self, period_minutes: int) -> int:
        timeframe = str(self.settings.intraday_timeframe or "5Min").strip().lower()
        if timeframe.endswith("min"):
            minutes = int(timeframe[:-3] or 5)
        else:
            minutes = 5
        return max(int(period_minutes // max(minutes, 1)), 1)

    def _rolling_price_change_scan(
        self,
        ticker: str,
        bars: pd.DataFrame,
        period_minutes: int,
        threshold_pct: float,
    ) -> dict:
        period_bars = self._signal_bars_per_period(period_minutes)
        bars_back = period_bars * max(int(self.settings.signal_lookback_bars), 1)
        if bars is None or bars.empty or len(bars) <= bars_back:
            return {
                "ticker": str(ticker or "").upper(),
                "current_price": None,
                "price_2_bars_ago": None,
                "price_change_pct": None,
                "signal": False,
            }
        current_price = float(bars.iloc[-1]["close"] or 0.0)
        reference_price = float(bars.iloc[-(bars_back + 1)]["close"] or 0.0)
        if reference_price <= 0:
            price_change_pct = None
            signal = False
        else:
            price_change_pct = ((current_price - reference_price) / reference_price) * 100.0
            signal = price_change_scan(current_price, reference_price, threshold_pct=threshold_pct)
        return {
            "ticker": str(ticker or "").upper(),
            "current_price": round(current_price, 4),
            "price_2_bars_ago": round(reference_price, 4),
            "price_change_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
            "signal": signal,
        }

    def _rolling_volume_change_scan(
        self,
        ticker: str,
        bars: pd.DataFrame,
        period_minutes: int,
        threshold_pct: float,
    ) -> dict:
        period_bars = self._signal_bars_per_period(period_minutes)
        lookback_bars = period_bars * max(int(self.settings.signal_lookback_bars), 1)
        needed = period_bars + lookback_bars
        if bars is None or bars.empty or len(bars) < needed:
            return {
                "ticker": str(ticker or "").upper(),
                "current_volume": None,
                "volume_2_bars_ago": None,
                "volume_change_pct": None,
                "signal": False,
            }
        current_window = bars.tail(period_bars)
        reference_start = -(lookback_bars + period_bars)
        reference_end = -lookback_bars
        reference_window = bars.iloc[reference_start:reference_end]
        current_volume = float(current_window["volume"].sum())
        reference_volume = float(reference_window["volume"].sum())
        if reference_volume <= 0:
            volume_change_pct = None
            signal = False
        else:
            volume_change_pct = ((current_volume - reference_volume) / reference_volume) * 100.0
            signal = volume_scan(current_volume, reference_volume, threshold_pct=threshold_pct)
        return {
            "ticker": str(ticker or "").upper(),
            "current_volume": int(current_volume) if current_volume.is_integer() else current_volume,
            "volume_2_bars_ago": int(reference_volume) if reference_volume.is_integer() else reference_volume,
            "volume_change_pct": round(volume_change_pct, 2) if volume_change_pct is not None else None,
            "signal": signal,
        }

    def _fast_prefilter_universe(self, universe: list[str]) -> tuple[list[str], dict[str, dict]]:
        symbol_list = [str(symbol).strip().upper() for symbol in universe if str(symbol).strip()]
        if not symbol_list:
            return symbol_list, {}
        get_quotes = getattr(self.client, "get_quotes", None)
        if not callable(get_quotes):
            return symbol_list, {}

        try:
            quotes = get_quotes(symbol_list)
        except Exception:
            return symbol_list, {}
        if not quotes:
            return symbol_list, {}
        if not self.settings.fast_quote_prefilter_enabled:
            return symbol_list, quotes

        movers: list[tuple[str, float, float]] = []
        for symbol in symbol_list:
            quote = quotes.get(symbol) or {}
            try:
                price = float(quote.get("last_price") or 0.0)
                change_pct = float(quote.get("change_pct") or 0.0)
                volume = float(quote.get("volume") or 0.0)
            except (TypeError, ValueError):
                continue
            if price >= self.settings.min_price and change_pct >= self.settings.fast_quote_prefilter_min_change_pct:
                movers.append((symbol, change_pct, volume))

        if not movers:
            # If the market is flat or the quote fields differ, keep the old behavior instead of hiding opportunities.
            return symbol_list, quotes

        movers.sort(key=lambda item: (item[1], item[2]), reverse=True)
        max_symbols = int(self.settings.fast_quote_prefilter_max_symbols)
        selected = [symbol for symbol, _, _ in movers]
        selected = selected if max_symbols <= 0 else selected[:max_symbols]
        return selected, quotes

    def diagnose_symbol(self, symbol: str) -> dict:
        target = str(symbol or "").strip().upper()
        if not target:
            return {"symbol": "", "status": "invalid", "reason": "Symbol is required."}

        spy_above_vwap = self._safe_spy_above_vwap()
        intraday_map = self.client.get_intraday_bars(
            [target],
            timeframe=self.settings.intraday_timeframe,
            days_back=self.settings.intraday_signal_lookback_days,
        )
        daily_map = self.client.get_daily_bars([target], lookback_days=self.settings.lookback_daily_bars)
        intraday_frame = intraday_map.get(target)
        daily_frame = daily_map.get(target)

        if intraday_frame is None or daily_frame is None or intraday_frame.empty or daily_frame.empty:
            return {
                "symbol": target,
                "status": "insufficient_data",
                "reason": "Intraday or daily data unavailable.",
                "scannerEligible": False,
                "checks": [],
                "activeSetups": [],
            }

        try:
            quote_map = self.client.get_quotes([target]) if callable(getattr(self.client, "get_quotes", None)) else {}
        except Exception:
            quote_map = {}
        session_change_pct = (quote_map.get(target) or {}).get("change_pct")
        analysis = self._analyze_symbol(
            target,
            intraday_frame,
            daily_frame,
            spy_above_vwap,
            session_change_pct=session_change_pct,
        )
        if analysis is None:
            return {
                "symbol": target,
                "status": "insufficient_data",
                "reason": "Not enough regular-session data to evaluate the setup.",
                "scannerEligible": False,
                "checks": [],
                "activeSetups": [],
            }

        checks = [
            {
                "label": f"Underlying price >= ${self.settings.min_price:g}",
                "passed": analysis["price"] > self.settings.min_price,
                "value": round(analysis["price"], 2),
            },
            {"label": "Above VWAP", "passed": analysis["above_vwap"], "value": analysis["above_vwap"]},
            {"label": "EMA 9 > 21 > 50", "passed": analysis["ema_stack"], "value": analysis["ema_stack"]},
            {
                "label": f"Live Change % >= {self.settings.min_session_change_pct:.2f}% (all sessions)",
                "passed": analysis["session_change_pass"],
                "value": round(analysis["session_change_pct"], 2),
            },
            {"label": "Volume accelerating (info only)", "passed": True, "value": analysis["volume_trend"]},
            {
                "label": "Fast momentum 2-of-3 (info only)",
                "passed": True,
                "value": f"{analysis['fast_momentum_score']}/3 {analysis['fast_momentum_status']}",
            },
            {
                "label": f"Live 1H price_change >= {self.settings.min_one_hour_close_change_pct:.2f}% vs close[2]",
                "passed": analysis["one_hour_price_change_pass"],
                "value": "--" if analysis["one_hour_price_change_pct"] is None else round(analysis["one_hour_price_change_pct"], 2),
            },
            {
                "label": f"Live 4H price_change >= {self.settings.min_four_hour_price_change_pct:.2f}% vs close[2]",
                "passed": analysis["four_hour_price_change_pass"],
                "value": "--" if analysis["four_hour_price_change_pct"] is None else round(analysis["four_hour_price_change_pct"], 2),
            },
            {
                "label": "Live 4H volume > volume[2] * 1.005",
                "passed": analysis["four_hour_volume_pass"],
                "value": (
                    "--" if analysis["four_hour_volume_change_pct"] is None else round(analysis["four_hour_volume_change_pct"], 2)
                ),
            },
            {"label": "Breakout close confirmed", "passed": analysis["breakout_close_confirmed"], "value": analysis["breakout_close_confirmed"]},
            {"label": "First 5m bullish (info only)", "passed": True, "value": analysis["first_5m_bullish"]},
            {"label": "First 5m above VWAP (info only)", "passed": True, "value": analysis["first_5m_close_above_vwap"]},
            {"label": "ORB breakout", "passed": analysis["orb_breakout"], "value": analysis["orb_breakout"]},
            {"label": "5m EMA9 retest hold (info only)", "passed": True, "value": analysis["ema9_retest_5m"]},
            {
                "label": "RVOL confirmed timeframe",
                "passed": analysis["tos_rvol_any_pass"],
                "value": analysis["tos_rvol_timeframes"],
            },
        ]
        failed = [item["label"] for item in checks if not item["passed"]]
        reason = "Scanner setup matched." if analysis["active_setups"] else "No approved setup matched current candle state."

        result = analysis["scan_result"]
        return {
            "symbol": target,
            "status": "scanner_pass" if result is not None else "scanner_rejected",
            "reason": reason,
            "scannerEligible": result is not None,
            "scanResult": asdict(result) if result is not None else None,
            "checks": checks,
            "activeSetups": analysis["active_setups"],
            "failedChecks": failed,
            "fastMomentum": {
                "score": analysis["fast_momentum_score"],
                "status": analysis["fast_momentum_status"],
                "projected5mVolume": analysis["projected_5m_volume"],
                "projected5mVolumeRatio": analysis["projected_5m_volume_ratio"],
                "buyingPressurePct": analysis["buying_pressure_pct"],
                "previous5mHigh": analysis["previous_5m_high"],
                "volumePass": analysis["fast_volume_pass"],
                "buyingPressurePass": analysis["fast_buying_pressure_pass"],
                "previousHighBreakPass": analysis["fast_previous_high_break_pass"],
                "blocking": False,
            },
            "rvolTimeframes": {
                "5m": analysis["tos_rvol_5m"],
                "15m": analysis["tos_rvol_15m"],
                "30m": analysis["tos_rvol_30m"],
                "1h": analysis["tos_rvol_1h"],
                "2h": analysis["tos_rvol_2h"],
                "4h": analysis["tos_rvol_4h"],
                "1d": analysis["tos_rvol_1d"],
            },
        }

    def _score_symbol(
        self,
        symbol: str,
        intraday_frame: pd.DataFrame,
        daily_frame: pd.DataFrame,
        spy_above_vwap: bool,
        ignore_one_hour_price_change: bool = False,
        ignore_four_hour_price_change: bool = False,
        ignore_four_hour_volume: bool = False,
        ignore_ema9_retest: bool = True,
        rvol_confirmation_threshold: float | None = None,
        session_change_pct: float | None = None,
        allow_mtf_signal_setup: bool = False,
        require_rvol_confirmation: bool = True,
    ) -> ScanResult | None:
        analysis = self._analyze_symbol(
            symbol,
            intraday_frame,
            daily_frame,
            spy_above_vwap,
            ignore_one_hour_price_change=ignore_one_hour_price_change,
            ignore_four_hour_price_change=ignore_four_hour_price_change,
            ignore_four_hour_volume=ignore_four_hour_volume,
            ignore_ema9_retest=ignore_ema9_retest,
            rvol_confirmation_threshold=rvol_confirmation_threshold,
            session_change_pct=session_change_pct,
            allow_mtf_signal_setup=allow_mtf_signal_setup,
            require_rvol_confirmation=require_rvol_confirmation,
        )
        if analysis is None:
            return None
        return analysis["scan_result"]

    def _analyze_symbol(
        self,
        symbol: str,
        intraday_frame: pd.DataFrame,
        daily_frame: pd.DataFrame,
        spy_above_vwap: bool,
        ignore_one_hour_price_change: bool = False,
        ignore_four_hour_price_change: bool = False,
        ignore_four_hour_volume: bool = False,
        ignore_ema9_retest: bool = True,
        rvol_confirmation_threshold: float | None = None,
        session_change_pct: float | None = None,
        allow_mtf_signal_setup: bool = False,
        require_rvol_confirmation: bool = True,
    ) -> dict | None:
        intraday = intraday_frame.copy()
        intraday["ema_9"] = ema(intraday["close"], 9)
        intraday["ema_21"] = ema(intraday["close"], 21)
        intraday["ema_50"] = ema(intraday["close"], 50)
        current_day = self._current_trading_day_frame(intraday)
        if current_day.empty:
            return None
        current_day["calc_vwap"] = cumulative_vwap(current_day)
        session = current_day.copy() if self.settings.signal_include_extended_hours else regular_session(current_day)
        if session.empty:
            return None

        five_min = self._resample_to_five_min(session)
        if five_min.empty:
            return None
        five_min["ema_9"] = ema(five_min["close"], 9)
        first_5m = five_min.iloc[0]
        latest_5m = five_min.iloc[-1]
        fast_momentum = fast_momentum_confirmation(five_min)

        daily = daily_frame.copy()
        daily["atr_14"] = atr(daily, 14)

        signal_changes = self._stock_signal_changes(symbol, intraday)
        one_hour_price_scan = signal_changes["one_hour_price"]
        four_hour_price_scan = signal_changes["four_hour_price"]
        four_hour_volume_scan = signal_changes["four_hour_volume"]
        one_hour_close_change_pct = one_hour_price_scan["price_change_pct"]
        one_hour_price_change_pct = one_hour_price_scan["price_change_pct"]
        one_hour_current_price = one_hour_price_scan["current_price"]
        one_hour_price_2_bars_ago = one_hour_price_scan["price_2_bars_ago"]
        four_hour_price_change_pct = four_hour_price_scan["price_change_pct"]
        four_hour_current_price = four_hour_price_scan["current_price"]
        four_hour_price_2_bars_ago = four_hour_price_scan["price_2_bars_ago"]
        four_hour_volume_change_pct = four_hour_volume_scan["volume_change_pct"]
        four_hour_current_volume = four_hour_volume_scan["current_volume"]
        four_hour_volume_2_bars_ago = four_hour_volume_scan["volume_2_bars_ago"]
        four_hour_volume_signal = bool(four_hour_volume_scan["signal"])
        five_min_cloud = ema_cloud_snapshot(intraday)
        four_hour_cloud = ema_cloud_snapshot(signal_changes["four_hour_bars"])
        cloud_alignment_pass = bool(five_min_cloud["bullish"] and four_hour_cloud["bullish"])
        if cloud_alignment_pass:
            cloud_alignment_action = "TRADE - 4H + 5M BULLISH"
        elif four_hour_cloud["bullish"]:
            cloud_alignment_action = "WAIT 5M RECLAIM"
        elif five_min_cloud["bullish"]:
            cloud_alignment_action = "NO TRADE - 4H NOT BULLISH"
        else:
            cloud_alignment_action = "NO TRADE - CLOUDS NOT ALIGNED"
        stock_signal_gate_active = True
        one_hour_price_change_pass = bool(one_hour_price_scan["signal"])
        one_hour_close_pass = one_hour_price_change_pass
        four_hour_price_change_pass = bool(four_hour_price_scan["signal"])
        four_hour_volume_pass = four_hour_volume_signal
        mtf_signal_payload = _tos_mtf_ema_signal_payload(intraday)
        mtf_bullish_signal_pass = bool(mtf_signal_payload.get("bullishSignalPass"))
        mtf_bullish_signal_labels = ", ".join(mtf_signal_payload.get("bullishSignalLabels") or [])
        mtf_bullish_signal_families = ", ".join(mtf_signal_payload.get("bullishFamilies") or [])
        mtf_bullish_signal_timeframes = ", ".join(mtf_signal_payload.get("bullishTimeframes") or [])
        mtf_bullish_signal_both_2h_4h = bool(mtf_signal_payload.get("bullishBoth2H4H"))
        stock_all_conditions_pass = bool(
            (one_hour_price_change_pass or ignore_one_hour_price_change)
            and (four_hour_price_change_pass or ignore_four_hour_price_change)
            and (four_hour_volume_pass or ignore_four_hour_volume)
        )
        tos_rvol_info = self._tos_rvol_timeframe_map(
            symbol,
            intraday,
            daily,
            confirmation_threshold=rvol_confirmation_threshold,
        )
        tos_rvol_map = tos_rvol_info["map"]
        session_change_value = float(session_change_pct) if session_change_pct is not None else -999.0
        session_change_pass = session_change_scan(
            session_change_pct,
            threshold_pct=self.settings.min_session_change_pct,
        )
        fast_momentum_entry_pass = bool(fast_momentum["score"] >= 2)
        rvol_any_timeframe_pass = bool(
            tos_rvol_info["any_pass"] or tos_rvol_info["five_min_early_alert"]
        )
        standard_stock_conditions_pass = bool(
            stock_all_conditions_pass
            and (
                not require_rvol_confirmation
                or tos_rvol_info["gate_pass"]
                or fast_momentum_entry_pass
            )
            and session_change_pass
            and cloud_alignment_pass
        )
        mtf_entry_gate_pass = bool(
            allow_mtf_signal_setup
            and mtf_bullish_signal_pass
            and session_change_pass
        )
        stock_all_conditions_pass = bool(standard_stock_conditions_pass or mtf_entry_gate_pass)

        last_bar = current_day.iloc[-1]
        price = float(last_bar["close"])
        avg_volume = int(daily["volume"].tail(20).mean())
        if price <= self.settings.min_price:
            return {
                "scan_result": None,
                "price": price,
                "average_volume": avg_volume,
                "today_volume": 0,
                "rvol": 0.0,
                "above_vwap": False,
                "ema_stack": False,
                "five_min_cloud_state": five_min_cloud["state"],
                "five_min_ema_9": five_min_cloud["ema_9"],
                "five_min_ema_21": five_min_cloud["ema_21"],
                "five_min_ema_50": five_min_cloud["ema_50"],
                "four_hour_cloud_state": four_hour_cloud["state"],
                "four_hour_cloud_bullish": four_hour_cloud["bullish"],
                "four_hour_ema_9": four_hour_cloud["ema_9"],
                "four_hour_ema_21": four_hour_cloud["ema_21"],
                "four_hour_ema_50": four_hour_cloud["ema_50"],
                "cloud_alignment_pass": cloud_alignment_pass,
                "cloud_alignment_action": cloud_alignment_action,
                "breakout_close_confirmed": False,
                "volume_trend": False,
                "one_hour_close_change_pct": one_hour_close_change_pct,
                "one_hour_price_change_pct": one_hour_price_change_pct,
                "one_hour_current_price": one_hour_current_price,
                "one_hour_price_2_bars_ago": one_hour_price_2_bars_ago,
                "four_hour_price_change_pct": four_hour_price_change_pct,
                "four_hour_current_price": four_hour_current_price,
                "four_hour_price_2_bars_ago": four_hour_price_2_bars_ago,
                "four_hour_volume_change_pct": four_hour_volume_change_pct,
                "four_hour_current_volume": four_hour_current_volume,
                "four_hour_volume_2_bars_ago": four_hour_volume_2_bars_ago,
                "four_hour_volume_signal": four_hour_volume_signal,
                "one_hour_close_pass": one_hour_close_pass,
                "one_hour_price_change_pass": one_hour_price_change_pass,
                "four_hour_price_change_pass": four_hour_price_change_pass,
                "four_hour_volume_pass": four_hour_volume_pass,
                "stock_signal_gate_active": stock_signal_gate_active,
                "stock_all_conditions_pass": stock_all_conditions_pass,
                "mtf_bullish_signal_pass": mtf_bullish_signal_pass,
                "mtf_bullish_signal_labels": mtf_bullish_signal_labels,
                "mtf_bullish_signal_families": mtf_bullish_signal_families,
                "mtf_bullish_signal_timeframes": mtf_bullish_signal_timeframes,
                "mtf_bullish_signal_both_2h_4h": mtf_bullish_signal_both_2h_4h,
                "market_trend": spy_above_vwap,
                "intraday_change_pct": 0.0,
                "close_near_high": False,
                "not_overextended": False,
                "ema9_retest_5m": False,
                "first_5m_bullish": False,
                "first_5m_close_above_vwap": False,
                "orb_breakout": False,
                "active_setups": [],
                "tos_rvol_timeframes": tos_rvol_info["summary"],
                "tos_rvol_5m": tos_rvol_map.get("5m", {}).get("raw_rel_vol"),
                "tos_rvol_15m": tos_rvol_map.get("15m", {}).get("raw_rel_vol"),
                "tos_rvol_30m": tos_rvol_map.get("30m", {}).get("raw_rel_vol"),
                "tos_rvol_1h": tos_rvol_map.get("1h", {}).get("raw_rel_vol"),
                "tos_rvol_2h": tos_rvol_map.get("2h", {}).get("raw_rel_vol"),
                "tos_rvol_4h": tos_rvol_map.get("4h", {}).get("raw_rel_vol"),
                "tos_rvol_1d": tos_rvol_map.get("1d", {}).get("raw_rel_vol"),
                "tos_rvol_any_pass": tos_rvol_info["any_pass"],
                "tos_rvol_5m_early_alert": tos_rvol_info["five_min_early_alert"],
            }

        today_volume = int(current_day["volume"].sum())
        elapsed_fraction = session_progress(last_bar["timestamp"])
        rvol = float(relative_volume(today_volume, avg_volume, elapsed_fraction))
        above_vwap = price > float(last_bar["calc_vwap"])
        ema_stack = bool(last_bar["ema_9"] > last_bar["ema_21"] > last_bar["ema_50"])
        premarket_high = self._premarket_high(current_day)
        premarket_low = self._premarket_low(current_day)
        previous_day_high = float(daily["high"].iloc[-2]) if len(daily) > 1 else float(daily["high"].iloc[-1])
        previous_day_low = float(daily["low"].iloc[-2]) if len(daily) > 1 else float(daily["low"].iloc[-1])
        opening_range_high = float(first_5m["high"])
        opening_range_low = float(first_5m["low"])
        first_5m_bullish = bool(first_5m["close"] > first_5m["open"])
        first_5m_close_above_vwap = bool(first_5m["close"] > first_5m["vwap"])
        orb_breakout = bool(latest_5m["close"] > opening_range_high)
        premarket_low_reclaim = bool(price > premarket_low and latest_5m["close"] > premarket_low)
        previous_day_low_reclaim = bool(price > previous_day_low and latest_5m["close"] > previous_day_low)
        breakout_source, trigger_level = self._trigger_level(
            price,
            premarket_high,
            previous_day_high,
            premarket_low,
            previous_day_low,
            premarket_low_reclaim,
            previous_day_low_reclaim,
        )
        breakout = breakout_source != "none"
        breakout_close_confirmed = bool(latest_5m["close"] > trigger_level) if breakout else False
        price_action_pass = bool(
            float(latest_5m["close"]) > float(latest_5m["open"])
            and fast_momentum["previous_high_break_pass"]
        )
        volume_trend = volume_acceleration(session, self.settings.volume_acceleration_bars) or self._five_min_volume_trend(session)
        intraday_change_pct = float(((price - float(first_5m["open"])) / max(float(first_5m["open"]), 0.01)) * 100)
        latest_range = max(float(latest_5m["high"]) - float(latest_5m["low"]), 0.01)
        close_off_high_pct = ((float(latest_5m["high"]) - float(latest_5m["close"])) / latest_range) * 100
        close_near_high = bool(close_off_high_pct <= self.settings.max_close_off_high_pct)
        extension_above_ema9_pct = float(((price - float(last_bar["ema_9"])) / max(float(last_bar["ema_9"]), 0.01)) * 100)
        not_overextended = bool(extension_above_ema9_pct <= self.settings.max_extension_above_ema9_pct)
        ema9_retest_5m = self._ema9_retest_hold(five_min)
        ema9_retest_gate_pass = bool(ema9_retest_5m or ignore_ema9_retest)
        aggressive_morning = self._aggressive_morning_active(last_bar["timestamp"])
        aggressive_volume_ok = bool(volume_trend or rvol >= self.settings.aggressive_morning_min_rvol)
        aggressive_price_action = bool(
            aggressive_morning
            and ema_stack
            and above_vwap
            and intraday_change_pct >= self.settings.min_intraday_change_pct
            and close_near_high
            and (breakout_close_confirmed or orb_breakout)
            and aggressive_volume_ok
            and stock_all_conditions_pass
        )
        catalyst_ready = False

        score = 0
        if rvol >= self.settings.min_rvol:
            score += 20
        if volume_trend:
            score += 20
        if fast_momentum["score"] == 3:
            score += 8
        elif fast_momentum["score"] == 2:
            score += 4
        if ema_stack:
            score += 15
        if cloud_alignment_pass:
            score += 10
        if above_vwap:
            score += 10
        if breakout_close_confirmed:
            score += 15
        if orb_breakout:
            score += 10
        if close_near_high:
            score += 5
        if aggressive_price_action:
            score += 10
        if mtf_bullish_signal_pass:
            score += 25 if mtf_bullish_signal_both_2h_4h else 15

        setup_checks = [
            (
                "MTF EMA C/CALL 2H/4H",
                mtf_entry_gate_pass,
            ),
            (
                "EMA + VWAP + ORB",
                ema_stack
                and above_vwap
                and orb_breakout
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "EMA + VWAP + Previous Day High",
                ema_stack
                and above_vwap
                and breakout_source == "previous_day_high"
                and breakout_close_confirmed
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "EMA + VWAP + Premarket High",
                ema_stack
                and above_vwap
                and breakout_source == "premarket_high"
                and breakout_close_confirmed
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "EMA + VWAP",
                ema_stack
                and above_vwap
                and intraday_change_pct >= self.settings.min_intraday_change_pct
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "EMA + VWAP + Premarket Low Above Candle",
                ema_stack
                and above_vwap
                and premarket_low_reclaim
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "EMA + VWAP + Previous Day Low Above Candle",
                ema_stack
                and above_vwap
                and previous_day_low_reclaim
                and ema9_retest_gate_pass
                and stock_all_conditions_pass,
            ),
            (
                "Aggressive Morning EMA + VWAP + Price Action",
                aggressive_price_action,
            ),
        ]
        active_setups = [name for name, enabled in setup_checks if enabled]
        setup_name = active_setups[0] if active_setups else ""
        resolved_trigger_source = (
            f"MTF EMA {mtf_bullish_signal_labels}"
            if setup_name == "MTF EMA C/CALL 2H/4H"
            else breakout_source
        )

        atr_value = float(daily["atr_14"].iloc[-1]) if pd.notna(daily["atr_14"].iloc[-1]) else 0.0
        stop_reference = min(opening_range_low, previous_day_low, price - atr_value)
        projected_qty = self.risk_manager.quantity_for_entry(price)
        stop_loss = self.risk_manager.effective_stop_price(price, stop_reference, projected_qty)
        risk_per_share = max(price - stop_loss, 0.01)
        target = self.risk_manager.effective_target_price(price, stop_loss)
        scan_result = None
        if active_setups:
            scan_result = ScanResult(
                symbol=symbol,
                strategy_name=self.STRATEGY_NAME,
                setup_name=setup_name,
                score=score,
                last_price=round(price, 2),
                average_volume=avg_volume,
                today_volume=today_volume,
                rvol=round(rvol, 2),
                above_vwap=above_vwap,
                ema_stack=ema_stack,
                five_min_cloud_state=five_min_cloud["state"],
                five_min_ema_9=five_min_cloud["ema_9"],
                five_min_ema_21=five_min_cloud["ema_21"],
                five_min_ema_50=five_min_cloud["ema_50"],
                four_hour_cloud_state=four_hour_cloud["state"],
                four_hour_cloud_bullish=four_hour_cloud["bullish"],
                four_hour_ema_9=four_hour_cloud["ema_9"],
                four_hour_ema_21=four_hour_cloud["ema_21"],
                four_hour_ema_50=four_hour_cloud["ema_50"],
                cloud_alignment_pass=cloud_alignment_pass,
                cloud_alignment_action=cloud_alignment_action,
                breakout=breakout,
                breakout_close_confirmed=breakout_close_confirmed,
                volume_trend=volume_trend,
                fast_momentum_score=fast_momentum["score"],
                fast_momentum_status=fast_momentum["status"],
                projected_5m_volume=fast_momentum["projected_volume"],
                projected_5m_volume_ratio=fast_momentum["volume_ratio"],
                buying_pressure_pct=fast_momentum["buying_pressure_pct"],
                previous_5m_high=fast_momentum["previous_high"],
                fast_volume_pass=fast_momentum["volume_pass"],
                fast_buying_pressure_pass=fast_momentum["buying_pressure_pass"],
                fast_previous_high_break_pass=fast_momentum["previous_high_break_pass"],
                price_action_pass=price_action_pass,
                one_hour_close_change_pct=round(one_hour_close_change_pct, 2) if one_hour_close_change_pct is not None else None,
                one_hour_price_change_pct=round(one_hour_price_change_pct, 2) if one_hour_price_change_pct is not None else None,
                one_hour_current_price=one_hour_current_price,
                one_hour_price_2_bars_ago=one_hour_price_2_bars_ago,
                four_hour_volume_change_pct=round(four_hour_volume_change_pct, 2) if four_hour_volume_change_pct is not None else None,
                four_hour_price_change_pct=round(four_hour_price_change_pct, 2) if four_hour_price_change_pct is not None else None,
                four_hour_current_price=four_hour_current_price,
                four_hour_price_2_bars_ago=four_hour_price_2_bars_ago,
                four_hour_current_volume=four_hour_current_volume,
                four_hour_volume_2_bars_ago=four_hour_volume_2_bars_ago,
                four_hour_volume_signal=four_hour_volume_signal,
                one_hour_close_pass=one_hour_close_pass,
                one_hour_price_change_pass=one_hour_price_change_pass,
                four_hour_price_change_pass=four_hour_price_change_pass,
                four_hour_volume_pass=four_hour_volume_pass,
                stock_signal_gate_active=stock_signal_gate_active,
                stock_all_conditions_pass=stock_all_conditions_pass,
                mtf_bullish_signal_pass=mtf_bullish_signal_pass,
                mtf_bullish_signal_labels=mtf_bullish_signal_labels,
                mtf_bullish_signal_families=mtf_bullish_signal_families,
                mtf_bullish_signal_timeframes=mtf_bullish_signal_timeframes,
                mtf_bullish_signal_both_2h_4h=mtf_bullish_signal_both_2h_4h,
                market_trend=spy_above_vwap,
                intraday_change_pct=round(intraday_change_pct, 2),
                session_change_pct=round(session_change_value, 2),
                session_change_pass=session_change_pass,
                close_near_high=close_near_high,
                not_overextended=not_overextended,
                ema9_retest_5m=ema9_retest_5m,
                extension_above_ema9_pct=round(extension_above_ema9_pct, 2),
                first_5m_bullish=first_5m_bullish,
                first_5m_close_above_vwap=first_5m_close_above_vwap,
                orb_breakout=orb_breakout,
                catalyst_ready=catalyst_ready,
                atr_value=round(atr_value, 2),
                premarket_high=round(premarket_high, 2),
                previous_day_high=round(previous_day_high, 2),
                previous_day_low=round(previous_day_low, 2),
                opening_range_high=round(opening_range_high, 2),
                opening_range_low=round(opening_range_low, 2),
                trigger_level=round(trigger_level, 2),
                trigger_source=resolved_trigger_source,
                entry=round(price, 2),
                stop_loss=round(stop_loss, 2),
                target=round(target, 2),
                risk_per_share=round(risk_per_share, 2),
                tos_rvol_timeframes=tos_rvol_info["summary"],
                tos_rvol_5m=tos_rvol_map.get("5m", {}).get("raw_rel_vol"),
                tos_rvol_15m=tos_rvol_map.get("15m", {}).get("raw_rel_vol"),
                tos_rvol_30m=tos_rvol_map.get("30m", {}).get("raw_rel_vol"),
                tos_rvol_1h=tos_rvol_map.get("1h", {}).get("raw_rel_vol"),
                tos_rvol_2h=tos_rvol_map.get("2h", {}).get("raw_rel_vol"),
                tos_rvol_4h=tos_rvol_map.get("4h", {}).get("raw_rel_vol"),
                tos_rvol_1d=tos_rvol_map.get("1d", {}).get("raw_rel_vol"),
                tos_rvol_any_pass=tos_rvol_info["any_pass"],
                tos_rvol_5m_early_alert=tos_rvol_info["five_min_early_alert"],
            )
        return {
            "scan_result": scan_result,
            "price": price,
            "average_volume": avg_volume,
            "today_volume": today_volume,
            "rvol": rvol,
            "above_vwap": above_vwap,
            "ema_stack": ema_stack,
            "five_min_cloud_state": five_min_cloud["state"],
            "five_min_ema_9": five_min_cloud["ema_9"],
            "five_min_ema_21": five_min_cloud["ema_21"],
            "five_min_ema_50": five_min_cloud["ema_50"],
            "four_hour_cloud_state": four_hour_cloud["state"],
            "four_hour_cloud_bullish": four_hour_cloud["bullish"],
            "four_hour_ema_9": four_hour_cloud["ema_9"],
            "four_hour_ema_21": four_hour_cloud["ema_21"],
            "four_hour_ema_50": four_hour_cloud["ema_50"],
            "cloud_alignment_pass": cloud_alignment_pass,
            "cloud_alignment_action": cloud_alignment_action,
            "breakout_close_confirmed": breakout_close_confirmed,
            "volume_trend": volume_trend,
            "fast_momentum_score": fast_momentum["score"],
            "fast_momentum_status": fast_momentum["status"],
            "projected_5m_volume": fast_momentum["projected_volume"],
            "projected_5m_volume_ratio": fast_momentum["volume_ratio"],
            "buying_pressure_pct": fast_momentum["buying_pressure_pct"],
            "previous_5m_high": fast_momentum["previous_high"],
            "fast_volume_pass": fast_momentum["volume_pass"],
            "fast_buying_pressure_pass": fast_momentum["buying_pressure_pass"],
            "fast_previous_high_break_pass": fast_momentum["previous_high_break_pass"],
            "fast_momentum_pass": fast_momentum_entry_pass,
            "price_action_pass": price_action_pass,
            "one_hour_close_change_pct": one_hour_close_change_pct,
            "one_hour_price_change_pct": one_hour_price_change_pct,
            "one_hour_current_price": one_hour_current_price,
            "one_hour_price_2_bars_ago": one_hour_price_2_bars_ago,
            "four_hour_price_change_pct": four_hour_price_change_pct,
            "four_hour_current_price": four_hour_current_price,
            "four_hour_price_2_bars_ago": four_hour_price_2_bars_ago,
            "four_hour_volume_change_pct": four_hour_volume_change_pct,
            "four_hour_current_volume": four_hour_current_volume,
            "four_hour_volume_2_bars_ago": four_hour_volume_2_bars_ago,
            "four_hour_volume_signal": four_hour_volume_signal,
            "one_hour_close_pass": one_hour_close_pass,
            "one_hour_price_change_pass": one_hour_price_change_pass,
            "four_hour_price_change_pass": four_hour_price_change_pass,
            "four_hour_volume_pass": four_hour_volume_pass,
            "stock_signal_gate_active": stock_signal_gate_active,
            "stock_all_conditions_pass": stock_all_conditions_pass,
            "market_trend": spy_above_vwap,
            "intraday_change_pct": intraday_change_pct,
            "session_change_pct": session_change_value,
            "session_change_pass": session_change_pass,
            "close_near_high": close_near_high,
            "not_overextended": not_overextended,
            "ema9_retest_5m": ema9_retest_5m,
            "aggressive_morning": aggressive_morning,
            "aggressive_volume_ok": aggressive_volume_ok,
            "first_5m_bullish": first_5m_bullish,
            "first_5m_close_above_vwap": first_5m_close_above_vwap,
            "orb_breakout": orb_breakout,
            "active_setups": active_setups,
            "tos_rvol_timeframes": tos_rvol_info["summary"],
            "tos_rvol_5m": tos_rvol_map.get("5m", {}).get("raw_rel_vol"),
            "tos_rvol_15m": tos_rvol_map.get("15m", {}).get("raw_rel_vol"),
            "tos_rvol_30m": tos_rvol_map.get("30m", {}).get("raw_rel_vol"),
            "tos_rvol_1h": tos_rvol_map.get("1h", {}).get("raw_rel_vol"),
            "tos_rvol_2h": tos_rvol_map.get("2h", {}).get("raw_rel_vol"),
            "tos_rvol_4h": tos_rvol_map.get("4h", {}).get("raw_rel_vol"),
            "tos_rvol_1d": tos_rvol_map.get("1d", {}).get("raw_rel_vol"),
            "tos_rvol_any_pass": tos_rvol_info["any_pass"],
            "tos_rvol_5m_early_alert": tos_rvol_info["five_min_early_alert"],
            "rvol_any_timeframe_pass": rvol_any_timeframe_pass,
            "tos_rvol_negative_timeframes": tos_rvol_info["negative_timeframes"],
            "tos_rvol_negative_veto_pass": tos_rvol_info["negative_veto_pass"],
        }

    def _spy_above_vwap(self, spy_frame: pd.DataFrame) -> bool:
        if spy_frame.empty:
            return False
        frame = self._current_trading_day_frame(spy_frame.copy())
        if frame.empty:
            return False
        frame["calc_vwap"] = cumulative_vwap(frame)
        last_bar = frame.iloc[-1]
        return bool(last_bar["close"] > last_bar["calc_vwap"])

    def _safe_spy_above_vwap(self) -> bool:
        try:
            spy_frame = self.client.get_spy_context()
        except Exception:
            # Keep the scanner and bot running even if Alpaca blocks the SPY confirmation request.
            return False
        return self._spy_above_vwap(spy_frame)

    def _current_trading_day_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame()
        ordered = frame.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
        if ordered.empty:
            return ordered
        latest_date = ordered["timestamp"].dt.date.max()
        return ordered[ordered["timestamp"].dt.date == latest_date].copy().reset_index(drop=True)

    def _stock_signal_changes(self, symbol: str, frame: pd.DataFrame) -> dict:
        signal_bars = self._stock_volume_signal_bars(frame)
        one_hour = self._aggregate_signal_bars(signal_bars, 60)
        four_hour = self._aggregate_signal_bars(signal_bars, 240)
        one_hour_price_scan = scan_live_price_change(
            symbol,
            one_hour,
            threshold_pct=self.settings.min_one_hour_close_change_pct,
        )
        four_hour_price_scan = scan_live_price_change(
            symbol,
            four_hour,
            threshold_pct=self.settings.min_four_hour_price_change_pct,
        )
        four_hour_volume_scan = scan_live_4h_volume(
            symbol,
            four_hour,
            threshold_pct=self.settings.min_four_hour_volume_change_pct,
        )
        return {
            "one_hour_price": one_hour_price_scan,
            "four_hour_price": four_hour_price_scan,
            "four_hour_volume": four_hour_volume_scan,
            "four_hour_bars": four_hour,
        }

    def _timeframe_days_back(self, bucket_minutes: int) -> int:
        if bucket_minutes >= 240:
            return 20
        return 15

    def _extended_or_session_bars(self, bars: pd.DataFrame) -> pd.DataFrame:
        if bars is None or bars.empty:
            return pd.DataFrame()
        ordered = bars.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
        if ordered.empty:
            return ordered
        if not self.settings.signal_include_extended_hours:
            return self._regular_session_signal_bars(ordered)
        return ordered.reset_index(drop=True)

    def _tos_rvol_timeframe_map(
        self,
        symbol: str,
        intraday_frame: pd.DataFrame,
        daily_frame: pd.DataFrame,
        confirmation_threshold: float | None = None,
    ) -> dict:
        base_intraday = self._extended_or_session_bars(intraday_frame)
        results: dict[str, dict] = {}
        for label, bucket_minutes, _ in self.TOS_RVOL_TIMEFRAMES:
            aggregated = self._aggregate_signal_bars(base_intraday, bucket_minutes)
            results[label] = tos_relative_volume_scan(
                aggregated,
                rel_vol_length=self.settings.tos_rvol_length,
                num_dev=self.settings.tos_rvol_num_dev,
            )
        daily = daily_frame.copy() if daily_frame is not None else pd.DataFrame()
        if daily is not None and not daily.empty and "timestamp" in daily.columns:
            ordered_daily = daily.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
            results["1d"] = tos_relative_volume_scan(
                ordered_daily,
                rel_vol_length=self.settings.tos_rvol_length,
                num_dev=self.settings.tos_rvol_num_dev,
            )
        else:
            results["1d"] = tos_relative_volume_scan(pd.DataFrame())
        threshold = float(
            self.settings.tos_rvol_num_dev
            if confirmation_threshold is None
            else confirmation_threshold
        )
        confirmed_thresholds = {label: threshold for label in ("15m", "30m", "1h", "2h", "4h", "1d")}

        def passes_threshold(payload: dict, threshold: float) -> bool:
            raw_value = payload.get("raw_rel_vol")
            return bool(
                raw_value is not None
                and float(raw_value) >= float(threshold)
                and payload.get("buying_gt_selling")
            )

        passing = [
            label
            for label, threshold in confirmed_thresholds.items()
            if passes_threshold(results.get(label, {}), threshold)
        ]
        negative_timeframes = [
            label
            for label in self.TOS_RVOL_NEGATIVE_VETO_TIMEFRAMES
            if results.get(label, {}).get("raw_rel_vol") is not None
            and float(results[label]["raw_rel_vol"]) < 0.0
        ]
        negative_veto_pass = not negative_timeframes
        five_min_early_alert = passes_threshold(
            results.get("5m", {}),
            self.settings.tos_rvol_five_min_early_num_dev,
        ) and negative_veto_pass
        summary_parts = list(passing)
        if five_min_early_alert:
            summary_parts.insert(0, "5m early")
        return {
            "map": results,
            "passing": passing,
            "confirmation_threshold": threshold,
            "summary": ", ".join(summary_parts) if summary_parts else "--",
            "any_pass": bool(passing and negative_veto_pass),
            "five_min_early_alert": five_min_early_alert,
            "negative_timeframes": negative_timeframes,
            "negative_veto_pass": negative_veto_pass,
            "gate_pass": bool(negative_veto_pass and (passing or five_min_early_alert)),
        }

    def _ema9_retest_hold(self, five_min: pd.DataFrame) -> bool:
        if five_min is None or five_min.empty or "ema_9" not in five_min.columns:
            return False
        lookback = max(int(self.settings.ema9_retest_lookback_bars), 1)
        recent = five_min.tail(lookback)
        for _, bar in recent.iterrows():
            ema9_value = float(bar["ema_9"]) if pd.notna(bar["ema_9"]) else 0.0
            if ema9_value <= 0:
                continue
            if float(bar["low"]) <= ema9_value and float(bar["close"]) >= ema9_value:
                return True
        return False

    def _regular_session_gate_active(self, timestamp: pd.Timestamp) -> bool:
        if pd.isna(timestamp):
            return False
        minutes = (timestamp.hour * 60) + timestamp.minute
        return bool(timestamp.weekday() < 5 and (9 * 60 + 30) <= minutes <= (16 * 60))

    def _aggressive_morning_active(self, timestamp: pd.Timestamp) -> bool:
        if not self.settings.aggressive_morning_enabled or pd.isna(timestamp):
            return False
        minutes = (timestamp.hour * 60) + timestamp.minute
        open_minutes = (9 * 60) + 30
        elapsed = minutes - open_minutes
        return bool(timestamp.weekday() < 5 and 0 <= elapsed <= int(self.settings.aggressive_morning_minutes))

    def _regular_session_signal_bars(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame()
        bars = frame.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
        if bars.empty:
            return bars
        minutes = (bars["timestamp"].dt.hour * 60) + bars["timestamp"].dt.minute
        mask = (
            (bars["timestamp"].dt.weekday < 5)
            & (minutes >= (9 * 60 + 30))
            & (minutes <= (16 * 60))
        )
        return bars.loc[mask].copy().reset_index(drop=True)

    def _stock_volume_signal_bars(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty or "timestamp" not in frame.columns:
            return pd.DataFrame()
        bars = frame.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
        if bars.empty:
            return bars
        if not self.settings.signal_include_extended_hours:
            return self._regular_session_signal_bars(bars)
        return bars.reset_index(drop=True)

    def _aggregate_signal_bars(self, bars: pd.DataFrame, bucket_minutes: int) -> pd.DataFrame:
        if bars is None or bars.empty or "timestamp" not in bars.columns:
            return pd.DataFrame()
        frame = bars.dropna(subset=["timestamp"]).sort_values("timestamp").copy()
        if frame.empty:
            return pd.DataFrame()
        frequency = f"{int(bucket_minutes)}min"
        return (
            frame.set_index("timestamp")
            .resample(frequency, label="right", closed="right")
            .agg(
                {
                    "open": "first",
                    "high": "max",
                    "low": "min",
                    "close": "last",
                    "volume": "sum",
                }
            )
            .dropna(subset=["open", "high", "low", "close"])
            .reset_index()
        )

    def _pct_change_vs_bars_back(self, frame: pd.DataFrame, column: str, length: int) -> float | None:
        bars_back = max(int(length), 1)
        if frame is None or frame.empty or column not in frame.columns or len(frame) <= bars_back:
            return None
        latest = float(frame.iloc[-1][column] or 0.0)
        reference = float(frame.iloc[-(bars_back + 1)][column] or 0.0)
        if reference <= 0:
            return None
        return round((100.0 * ((latest / reference) - 1.0)), 4)

    def _premarket_high(self, frame: pd.DataFrame) -> float:
        premarket = frame[
            (frame["timestamp"].dt.hour < 9)
            | ((frame["timestamp"].dt.hour == 9) & (frame["timestamp"].dt.minute < 30))
        ]
        if premarket.empty:
            return float(frame["high"].max())
        return float(premarket["high"].max())

    def _premarket_low(self, frame: pd.DataFrame) -> float:
        premarket = frame[
            (frame["timestamp"].dt.hour < 9)
            | ((frame["timestamp"].dt.hour == 9) & (frame["timestamp"].dt.minute < 30))
        ]
        if premarket.empty:
            return float(frame["low"].min())
        return float(premarket["low"].min())

    def _five_min_volume_trend(self, frame: pd.DataFrame) -> bool:
        if frame.empty:
            return False
        if self._is_five_minute_or_higher(frame):
            aggregated = frame[["timestamp", "volume"]].copy().reset_index(drop=True)
            return volume_acceleration(aggregated, 2)
        aggregated = (
            frame.set_index("timestamp")
            .resample("5min", label="right", closed="right")
            .agg({"volume": "sum"})
            .dropna()
            .reset_index()
        )
        return volume_acceleration(aggregated, 2)

    def _first_five_minute_candle(self, session: pd.DataFrame) -> pd.Series | None:
        five_min = self._resample_to_five_min(session)
        if five_min.empty:
            return None
        return five_min.iloc[0]

    def _latest_five_minute_candle(self, session: pd.DataFrame) -> pd.Series | None:
        five_min = self._resample_to_five_min(session)
        if five_min.empty:
            return None
        return five_min.iloc[-1]

    def _resample_to_five_min(self, session: pd.DataFrame) -> pd.DataFrame:
        frame = session.set_index("timestamp")[["open", "high", "low", "close", "volume", "calc_vwap"]].copy()
        if self._is_five_minute_or_higher(session):
            five_min = frame.dropna().reset_index()
            five_min = five_min[
                (five_min["timestamp"].dt.hour > 9)
                | ((five_min["timestamp"].dt.hour == 9) & (five_min["timestamp"].dt.minute >= 35))
            ].copy()
            five_min = five_min.rename(columns={"calc_vwap": "vwap"})
            return five_min.reset_index(drop=True)
        frame["bar_count"] = 1
        five_min = frame.resample("5min", label="right", closed="right").agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
                "calc_vwap": "last",
                "bar_count": "sum",
            }
        ).dropna().reset_index()
        if five_min.empty:
            return five_min
        five_min = five_min[five_min["bar_count"] >= 5].copy()
        five_min = five_min[
            (five_min["timestamp"].dt.hour > 9)
            | ((five_min["timestamp"].dt.hour == 9) & (five_min["timestamp"].dt.minute >= 35))
        ].copy()
        five_min = five_min.rename(columns={"calc_vwap": "vwap"})
        return five_min.reset_index(drop=True)

    def _is_five_minute_or_higher(self, frame: pd.DataFrame) -> bool:
        if frame is None or frame.empty or "timestamp" not in frame.columns or len(frame) < 2:
            return False
        ordered = frame["timestamp"].sort_values().dropna()
        if len(ordered) < 2:
            return False
        median_gap = ordered.diff().dropna().median()
        if pd.isna(median_gap):
            return False
        return median_gap >= Timedelta(minutes=5)

    def _trigger_level(
        self,
        price: float,
        premarket_high: float,
        previous_day_high: float,
        premarket_low: float,
        previous_day_low: float,
        premarket_low_reclaim: bool,
        previous_day_low_reclaim: bool,
    ) -> tuple[str, float]:
        if price > premarket_high:
            return "premarket_high", premarket_high
        if price > previous_day_high:
            return "previous_day_high", previous_day_high
        if premarket_low_reclaim:
            return "premarket_low_reclaim", premarket_low
        if previous_day_low_reclaim:
            return "previous_day_low_reclaim", previous_day_low
        return "none", max(premarket_high, previous_day_high)
