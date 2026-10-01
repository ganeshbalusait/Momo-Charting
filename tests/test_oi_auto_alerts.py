from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from oi_auto_alerts import (
    EASTERN,
    MAG7_SYMBOLS,
    apply_completed_five_minute_bar,
    apply_intrabar_touch,
    augment_ladder,
    build_high_oi_walls,
    build_oi_ladder,
    decorate_alert_row,
    five_minute_bar_from_minute_bars,
    format_event_message,
    latest_completed_five_minute_bar_end,
    new_alert_row,
    next_monthly_opex,
    next_refresh_at,
    normalize_symbol,
    normalize_symbols,
    rearm_row,
    side_has_fired,
)


ET = ZoneInfo("America/New_York")


def et(year, month, day, hour, minute, second=0):
    return datetime(year, month, day, hour, minute, second, tzinfo=ET)


# TSLA-style chain around a 341.63 spot; the numbers come from the
# 2026-08-16 snapshot the trader looked at (rounded).
TSLA_ROWS = [
    {"side": "CALL", "strike": 345, "delta": 0.44, "volume": 9_000, "open_interest": 6_541, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 350, "delta": 0.36, "volume": 12_000, "open_interest": 17_753, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 350, "delta": 0.40, "volume": 100, "open_interest": 4_000, "expiry": "2026-09-18", "days_to_expiration": 33},
    {"side": "CALL", "strike": 355, "delta": 0.30, "volume": 4_100, "open_interest": 8_200, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 357.5, "delta": 0.27, "volume": 900, "open_interest": 3_900, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 360, "delta": 0.24, "volume": 7_000, "open_interest": 12_844, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 400, "delta": 0.05, "volume": 1_000, "open_interest": 19_883, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "CALL", "strike": 420, "delta": 0.03, "volume": 800, "open_interest": 22_570, "expiry": "2026-08-21", "days_to_expiration": 5},
    # Lottery strike: tiny delta AND tiny OI must not rank.
    {"side": "CALL", "strike": 500, "delta": 0.01, "volume": 10, "open_interest": 900, "expiry": "2026-08-21", "days_to_expiration": 5},
    # Past the monthly OPEX window: ignored while nearer cycles exist.
    {"side": "CALL", "strike": 380, "delta": 0.30, "volume": 10, "open_interest": 90_000, "expiry": "2026-12-18", "days_to_expiration": 124},
    # In-the-money call (strike below spot) never becomes an upside target.
    {"side": "CALL", "strike": 330, "delta": 0.70, "volume": 100, "open_interest": 15_000, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "PUT", "strike": 340, "delta": -0.45, "volume": 5_000, "open_interest": 5_910, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "PUT", "strike": 330, "delta": -0.28, "volume": 3_000, "open_interest": 7_094, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "PUT", "strike": 320, "delta": -0.18, "volume": 2_000, "open_interest": 7_708, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "PUT", "strike": 300, "delta": -0.06, "volume": 900, "open_interest": 8_943, "expiry": "2026-08-21", "days_to_expiration": 5},
    {"side": "PUT", "strike": 310, "delta": -0.11, "volume": 400, "open_interest": 3_500, "expiry": "2026-08-21", "days_to_expiration": 5},
    # Above spot put: never a downside target.
    {"side": "PUT", "strike": 350, "delta": -0.60, "volume": 300, "open_interest": 9_000, "expiry": "2026-08-21", "days_to_expiration": 5},
]


def tsla_row():
    ladder = build_oi_ladder(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17))
    return new_alert_row(
        "TSLA",
        ladder,
        source="Schwab",
        source_group="mag7",
        session_date="2026-08-17",
        levels_updated_at="2026-08-17T09:15:00-04:00",
    )


class SymbolTests(unittest.TestCase):
    def test_mag7_constant(self) -> None:
        self.assertEqual(MAG7_SYMBOLS, ("AAPL", "MSFT", "AMZN", "GOOGL", "META", "NVDA", "TSLA"))

    def test_normalize_symbol(self) -> None:
        self.assertEqual(normalize_symbol(" tsla "), "TSLA")
        self.assertEqual(normalize_symbol("brk.b"), "BRK.B")
        self.assertEqual(normalize_symbol(""), "")
        self.assertEqual(normalize_symbol("1abc"), "")
        self.assertEqual(normalize_symbol("TOO_LONG_SYMBOL"), "")
        self.assertEqual(normalize_symbol("a b"), "")

    def test_normalize_symbols_dedupes_and_limits(self) -> None:
        self.assertEqual(normalize_symbols(["tsla", "TSLA", "amd", "", None, "nvda"], limit=2), ["TSLA", "AMD"])


class LadderTests(unittest.TestCase):
    def test_next_monthly_opex(self) -> None:
        self.assertEqual(next_monthly_opex(date(2026, 8, 17)), date(2026, 8, 21))
        self.assertEqual(next_monthly_opex(date(2026, 8, 21)), date(2026, 8, 21))
        self.assertEqual(next_monthly_opex(date(2026, 8, 22)), date(2026, 9, 18))
        self.assertEqual(next_monthly_opex(datetime(2026, 12, 20, 10, 0)), date(2027, 1, 15))

    def test_ladder_is_directional_and_ranked_like_the_sheet(self) -> None:
        ladder = build_oi_ladder(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17))
        self.assertEqual(ladder["spot"], 341.63)
        # 8/17 is 4 days from the 8/21 OPEX, so the sheet window rolls to the
        # September monthly — the 8/20 sheets list 9/18 walls beside 8/21.
        self.assertEqual(ladder["monthlyExpiry"], "2026-09-18")
        call_strikes = [level["strike"] for level in ladder["callLevels"]]
        put_strikes = [level["strike"] for level in ladder["putLevels"]]
        # Calls ascend above spot — call contracts only, no delta filter at
        # all (the sheet has none), so the 500 wall rides along in the top 8.
        # The ITM 330 call is below spot and gone; the December 380 wall is
        # outside the sheet window.
        self.assertEqual(call_strikes, [345, 350, 355, 357.5, 360, 400, 420, 500])
        # Puts descend at/below spot — put contracts only; the ITM 350 put
        # (above spot) never appears.
        self.assertEqual(put_strikes, [340, 330, 320, 310, 300])

    def test_ladder_keeps_dominant_expiry_per_strike(self) -> None:
        ladder = build_oi_ladder(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17))
        level_350 = next(level for level in ladder["callLevels"] if level["strike"] == 350)
        self.assertEqual(level_350["openInterest"], 17_753)
        self.assertEqual(level_350["expiry"], "2026-08-21")
        # Strength is relative to the side leader (420 = 22.6k):
        # 17.8k -> 0.79 strong, 12.8k -> 0.57 moderate, 6.5k -> 0.29 weak.
        self.assertEqual(level_350["strength"], "strong")
        level_420 = next(level for level in ladder["callLevels"] if level["strike"] == 420)
        self.assertEqual(level_420["strength"], "strong")
        level_360 = next(level for level in ladder["callLevels"] if level["strike"] == 360)
        self.assertEqual(level_360["strength"], "moderate")
        level_345 = next(level for level in ladder["callLevels"] if level["strike"] == 345)
        self.assertEqual(level_345["strength"], "weak")

    def test_unknown_delta_sentinels_do_not_hide_walls(self) -> None:
        # Schwab reports delta = 999 (no greeks) outside market hours. Those
        # rows must rank by OI like the chart does, not fall out of the band:
        # TSLA 345 (6.5K, below the 30% exception) stayed on the chart but
        # vanished from the ladder on 2026-08-17 00:00 ET.
        rows = [
            {**row, "delta": 999.0 if row["side"] == "CALL" else row["delta"]}
            for row in TSLA_ROWS
        ]
        ladder = build_oi_ladder(rows, 341.63, as_of=date(2026, 8, 17))
        call_strikes = [level["strike"] for level in ladder["callLevels"]]
        self.assertIn(345, call_strikes)
        self.assertEqual(call_strikes[:3], [345, 350, 355])
        # The lottery strike (900 OI) still drops out once real walls fill the ranking.
        ladder_small = build_oi_ladder(rows, 341.63, as_of=date(2026, 8, 17), levels_per_side=7)
        self.assertNotIn(500, [level["strike"] for level in ladder_small["callLevels"]])
        # A missing/zero delta is unknown too (never used to exclude).
        rows_zero = [{**row, "delta": 0} for row in TSLA_ROWS]
        ladder_zero = build_oi_ladder(rows_zero, 341.63, as_of=date(2026, 8, 17))
        self.assertIn(345, [level["strike"] for level in ladder_zero["callLevels"]])
        self.assertIn(340, [level["strike"] for level in ladder_zero["putLevels"]])

    def test_walls_split_at_spot_with_side_correct_contracts(self) -> None:
        # The sheet's board: above spot only call contracts count, at/below
        # spot only put contracts. The ITM 330 call (15K) sits below spot and
        # is positioning history — the 330 STRIKE still shows, but with the
        # put contract's 7.1K. The ITM 350 put (9K) likewise never appears;
        # 350 belongs to the call side with the call contract's 17.8K.
        walls = build_high_oi_walls(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17))
        self.assertEqual(
            [wall["strike"] for wall in walls["calls"]],
            [420, 400, 350, 360, 355, 345, 357.5, 500],
        )
        self.assertEqual([wall["strike"] for wall in walls["puts"]], [300, 320, 330, 340, 310])
        self.assertEqual(next(w for w in walls["calls"] if w["strike"] == 350)["openInterest"], 17_753)
        self.assertEqual(next(w for w in walls["puts"] if w["strike"] == 330)["openInterest"], 7_094)
        ladder = build_oi_ladder(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17))
        self.assertEqual(
            [w["strike"] for w in ladder["universe"]["calls"]],
            [420, 400, 350, 360, 355, 345, 357.5, 500],
        )
        self.assertNotIn(330, [level["strike"] for level in ladder["callLevels"]])
        self.assertNotIn(350, [level["strike"] for level in ladder["putLevels"]])

    def test_a_call_contract_below_spot_is_never_a_put_trigger(self) -> None:
        # Inverse of the old pooling rule, by the trader's 2026-08-20 decision:
        # MSFT's 9:35 PUT alert fired at 482.5 off a deep-ITM CALL contract's
        # 24K and lost money; the sheet's put board (put contracts only) had
        # its first wall at 480. A call wall below price must stay off the put
        # ladder entirely.
        rows = [{"side": "CALL", "strike": 337.5, "delta": 999, "open_interest": 7600, "volume": 1, "expiry": "2026-08-21"}]
        for index, strike in enumerate(range(342, 372, 2)):
            rows.append({"side": "CALL", "strike": float(strike), "delta": 999, "open_interest": 20000 - index * 100, "volume": 1, "expiry": "2026-08-21"})
        for index, strike in enumerate(range(305, 336, 2)):
            rows.append({"side": "PUT", "strike": float(strike), "delta": 999, "open_interest": 19000 - index * 100, "volume": 1, "expiry": "2026-08-21"})
        ladder = build_oi_ladder(rows, 339.85, as_of=date(2026, 8, 17), expected_move=6.45)
        self.assertNotIn(337.5, [wall["strike"] for wall in ladder["universe"]["calls"]])
        self.assertNotIn(337.5, [level["strike"] for level in ladder["putLevels"]])
        row = decorate_alert_row(new_alert_row("TSLA", ladder))
        # The put trigger is the NEAREST put wall below price. The near-money
        # ladder keeps 335 (nearest below 339.85) on the board, so it arms
        # there instead of skipping ~12 strikes down to the biggest-OI 319 -
        # the whole point of the near-money fix. A call wall below spot (337.5)
        # still never enters the put ladder.
        self.assertEqual(row["activePut"]["strike"], 335.0)
        self.assertEqual(row["activeCall"]["strike"], 342.0)

    def test_active_target_is_the_nearest_wall_above_and_below_price(self) -> None:
        # NVDA at 224.72 (the live price on 2026-08-17): 225 sits ABOVE price so
        # it is the CALL trigger, 222.5 below it is the PUT trigger. The 245
        # 8/28 weekly stays in the call ladder.
        rows = [
            {"side": "CALL", "strike": 245, "delta": 999, "open_interest": 15700, "volume": 1, "expiry": "2026-08-28"},
            {"side": "CALL", "strike": 240, "delta": 999, "open_interest": 44800, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 235, "delta": 999, "open_interest": 33700, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 230, "delta": 999, "open_interest": 84700, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 227.5, "delta": 999, "open_interest": 15900, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 225, "delta": 999, "open_interest": 46800, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 222.5, "delta": 999, "open_interest": 4000, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 220, "delta": 999, "open_interest": 13300, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 215, "delta": 999, "open_interest": 21400, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 210, "delta": 999, "open_interest": 25500, "volume": 1, "expiry": "2026-08-21"},
        ]
        row = decorate_alert_row(
            new_alert_row("NVDA", build_oi_ladder(rows, 224.72, as_of=date(2026, 8, 17), expected_move=3.65))
        )
        self.assertEqual(row["activeCall"]["strike"], 225)
        self.assertEqual(row["nextCall"]["strike"], 227.5)
        self.assertEqual(row["activePut"]["strike"], 222.5)
        self.assertEqual(row["nextPut"]["strike"], 220)
        self.assertEqual([level["strike"] for level in row["callLevels"]], [225, 227.5, 230, 235, 240, 245])
        self.assertEqual([level["strike"] for level in row["putLevels"]], [222.5, 220, 215, 210])

    def test_far_wall_is_excluded_by_distance_not_by_oi(self) -> None:
        # MomoX rule: the board is the N strikes NEAREST spot per side, so a
        # far wall drops for being far - NOT for being outside an EM band and
        # NOT because its OI is small. Here 400 has the LARGEST OI but sits
        # past the 8th-nearest call, so it never shows (spot 342.35).
        rows = [
            {"side": "CALL", "strike": float(s), "delta": 999, "open_interest": oi, "volume": 1, "expiry": "2026-08-21"}
            for s, oi in [
                (345, 6500), (347.5, 3000), (350, 17800), (352.5, 2500), (355, 4000),
                (357.5, 1500), (360, 12800), (362.5, 1200), (365, 3000), (400, 19900),
            ]
        ]
        rows.append({"side": "PUT", "strike": 340, "delta": 999, "open_interest": 5900, "volume": 1, "expiry": "2026-08-21"})
        walls = build_high_oi_walls(rows, 342.35, as_of=date(2026, 8, 17), expected_move=7.04)
        self.assertEqual(walls["emUp"], 349.39)
        self.assertEqual(walls["emDown"], 335.31)
        call_strikes = [wall["strike"] for wall in walls["calls"]]
        # 8 nearest calls above 342.35 are 345..365; 400 is far, dropped despite
        # the biggest OI. The near strikes are all kept regardless of OI rank.
        self.assertEqual(sorted(call_strikes), [345, 347.5, 350, 352.5, 355, 357.5, 360, 362.5])
        self.assertNotIn(400, call_strikes)
        # Importance still ranks by OI relative to the side leader (350 leads).
        self.assertEqual(next(w for w in walls["calls"] if w["strike"] == 350)["importance"], 5)
        ladder = build_oi_ladder(rows, 342.35, as_of=date(2026, 8, 17), expected_move=7.04)
        self.assertEqual([level["strike"] for level in ladder["callLevels"]][:2], [345, 347.5])
        self.assertNotIn(400, [level["strike"] for level in ladder["callLevels"]])

    def test_near_money_ladder_keeps_low_oi_strikes_the_top_n_would_drop(self) -> None:
        # NVDA 2026-08-24: 212.5 (10.8k OI) ranked ~18th, so the old top-8-by-OI
        # board dropped it and the alert engine never armed it - price fell
        # through 212.5 with nothing firing. The near-money ladder must keep it
        # (and 207.5/205/202.5/195) on the board and in the armed ladder while
        # the big far walls still show.
        spot = 213.5  # so 212.5 and 210 sit on the put side, like the trader's chart
        rows = [
            {"side": "CALL", "strike": 250, "open_interest": 84890, "volume": 100, "expiry": "2026-09-18"},
            {"side": "CALL", "strike": 240, "open_interest": 64031, "volume": 100, "expiry": "2026-09-18"},
            {"side": "CALL", "strike": 215, "open_interest": 33800, "volume": 100, "expiry": "2026-09-18"},
        ] + [
            {"side": "PUT", "strike": strike, "open_interest": oi, "volume": 100, "expiry": "2026-09-18"}
            for strike, oi in [
                (212.5, 10800), (210, 46523), (207.5, 4868), (205, 17902), (202.5, 9279),
                (200, 44623), (197.5, 1817), (195, 30869), (190, 67256), (185, 40071),
                (180, 66299), (170, 42032), (160, 36631), (150, 39878), (140, 53583),
            ]
        ]
        walls = build_high_oi_walls(rows, spot, as_of=date(2026, 8, 24), expected_move=13.77)
        put_strikes = {wall["strike"] for wall in walls["puts"]}
        # Top-8 by OI within +/-2*EM of spot (213.5). 212.5 is in-band and
        # carries real OI, so it is on the board and arms - the whole point.
        for near in (212.5, 210, 205, 202.5, 200, 195, 190):
            self.assertIn(near, put_strikes, f"{near} missing from the near-money board")
        # Far walls with big OI are excluded by the +/-2*EM band (180 = 33.5
        # below spot, > 2*13.77), even though 180/140 have huge OI.
        for far in (180, 170, 160, 150, 140):
            self.assertNotIn(far, put_strikes)
        # The armed ladder arms 212.5, so it fires an alert.
        self.assertIn(212.5, [level["strike"] for level in build_oi_ladder(rows, spot, as_of=date(2026, 8, 24), expected_move=13.77)["putLevels"]])

    def test_ladder_ranks_regardless_of_delta(self) -> None:
        # The sheet has no delta filter of any kind.
        rows = [
            {"side": "CALL", "strike": 105, "delta": 0.0, "volume": 1, "open_interest": 100, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 110, "delta": 0.0, "volume": 1, "open_interest": 90, "expiry": "2026-08-21"},
        ]
        ladder = build_oi_ladder(rows, 100, as_of=date(2026, 8, 17))
        self.assertEqual([level["strike"] for level in ladder["callLevels"]], [105, 110])
        self.assertEqual(ladder["putLevels"], [])

    def test_ladder_respects_levels_per_side(self) -> None:
        # No expected_move -> no band -> top-N by OI (the fallback). levels=3.
        ladder = build_oi_ladder(TSLA_ROWS, 341.63, as_of=date(2026, 8, 17), levels_per_side=3)
        self.assertEqual([level["strike"] for level in ladder["callLevels"]], [350, 400, 420])
        self.assertEqual([level["strike"] for level in ladder["putLevels"]], [330, 320, 300])


class RowTests(unittest.TestCase):
    def test_new_row_has_armed_targets(self) -> None:
        row = decorate_alert_row(tsla_row())
        self.assertEqual(row["symbol"], "TSLA")
        self.assertEqual(row["activeCall"]["strike"], 345)
        self.assertEqual(row["nextCall"]["strike"], 350)
        self.assertEqual(row["activePut"]["strike"], 340)
        self.assertEqual(row["nextPut"]["strike"], 330)
        self.assertEqual(row["confirmedCallStrikes"], [])
        self.assertEqual(row["confirmedPutStrikes"], [])
        self.assertEqual(row["callState"], "armed")
        self.assertEqual(row["putState"], "armed")

    def test_touch_does_not_advance_but_marks_touched(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 342.0, "high": 346.10, "low": 341.5, "close": 344.80,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        self.assertEqual([event["kind"] for event in events], ["touch"])
        self.assertEqual(events[0]["side"], "CALL")
        self.assertEqual(events[0]["level"]["strike"], 345)
        self.assertEqual(events[0]["price"], 346.10)
        decorated = decorate_alert_row(row)
        self.assertEqual(decorated["activeCall"]["strike"], 345)
        self.assertEqual(decorated["callState"], "touched")
        self.assertEqual(decorated["touchedCallStrikes"], [345])
        self.assertEqual(row["lastProcessedBar"], "2026-08-17T09:35:00-04:00")
        self.assertEqual(row["lastClose"], 344.80)
        # A second touch of the same level is silent.
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 344.8, "high": 345.5, "low": 343.0, "close": 344.0,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        self.assertEqual(events, [])

    def test_close_above_level_confirms_and_advances(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 344.8, "high": 345.6, "low": 344.1, "close": 345.25,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["kind"], "confirm")
        self.assertEqual(event["side"], "CALL")
        self.assertEqual(event["level"]["strike"], 345)
        self.assertEqual([level["strike"] for level in event["crossedLevels"]], [345])
        self.assertEqual(event["price"], 345.25)
        self.assertEqual(event["nextTarget"]["strike"], 350)
        self.assertEqual(event["nextTarget"]["openInterest"], 17_753)
        self.assertAlmostEqual(event["nextTarget"]["distance"], 4.75, places=2)
        self.assertAlmostEqual(event["nextTarget"]["distancePercent"], 1.3758, places=3)
        self.assertEqual(event["barEndedAt"], "2026-08-17T09:40:00-04:00")
        decorated = decorate_alert_row(row)
        self.assertEqual(decorated["confirmedCallStrikes"], [345])
        # The remaining walls are still reported as context, but the side has
        # spent its one alert for the session and will not fire on them.
        self.assertEqual(decorated["activeCall"]["strike"], 350)
        self.assertEqual(decorated["nextCall"]["strike"], 355)
        self.assertEqual(decorated["callState"], "fired")
        self.assertEqual(decorated["lastCallEvent"]["kind"], "confirm")

    def test_gap_confirms_multiple_levels(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 351.0, "high": 352.4, "low": 350.6, "close": 352.10,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual([level["strike"] for level in event["crossedLevels"]], [345, 350])
        self.assertEqual(event["level"]["strike"], 350)
        self.assertEqual(event["nextTarget"]["strike"], 355)
        self.assertEqual(decorate_alert_row(row)["confirmedCallStrikes"], [345, 350])

    def test_put_side_confirms_on_close_below(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 340.5, "high": 340.9, "low": 339.4, "close": 339.92,
                 "start": "2026-08-17T10:00:00-04:00", "end": "2026-08-17T10:05:00-04:00"},
        )
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event["side"], "PUT")
        self.assertEqual(event["kind"], "confirm")
        self.assertEqual(event["level"]["strike"], 340)
        self.assertEqual(event["nextTarget"]["strike"], 330)
        self.assertAlmostEqual(event["nextTarget"]["distance"], 9.92, places=2)
        decorated = decorate_alert_row(row)
        self.assertEqual(decorated["activePut"]["strike"], 330)
        self.assertEqual(decorated["nextPut"]["strike"], 320)
        self.assertEqual(decorated["putState"], "fired")

    def test_same_bar_is_never_processed_twice(self) -> None:
        row = tsla_row()
        bar = {"open": 344.8, "high": 345.6, "low": 344.1, "close": 345.25,
               "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"}
        row, first = apply_completed_five_minute_bar(row, bar=bar)
        row, second = apply_completed_five_minute_bar(row, bar=bar)
        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])

    def test_ladder_exhaustion_reports_no_next_target(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 504, "high": 506, "low": 503, "close": 505.5,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]["nextTarget"])
        decorated = decorate_alert_row(row)
        self.assertIsNone(decorated["activeCall"])
        # Spent, and nothing left in the ladder either - "fired" is what the UI
        # needs to see, because that is what the Re-arm button acts on.
        self.assertEqual(decorated["callState"], "fired")

    def test_gap_down_confirms_the_put_side_once_without_moving_the_call_plan(self) -> None:
        # A gap through several put walls is still ONE put alert. The call side
        # keeps the levels drawn at the 9:15 build: waking broken walls as new
        # mid-session targets is what made the chart stop matching the morning
        # plan, so the ladder is frozen for the session now.
        row = tsla_row()
        call_plan = [level["strike"] for level in row["callLevels"]]
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 329.0, "high": 329.5, "low": 327.5, "close": 328.0,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        put_confirms = [event for event in events if event["side"] == "PUT" and event["kind"] == "confirm"]
        self.assertEqual(len(put_confirms), 1)
        self.assertEqual([level["strike"] for level in put_confirms[0]["crossedLevels"]], [340, 330])
        self.assertEqual([event for event in events if event["side"] == "CALL" and event["kind"] == "confirm"], [])
        decorated = decorate_alert_row(row)
        self.assertEqual([level["strike"] for level in decorated["callLevels"]], call_plan)
        self.assertEqual(decorated["putState"], "fired")
        self.assertEqual(decorated["callState"], "armed")

    def test_gap_up_confirms_the_call_side_once_without_moving_the_put_plan(self) -> None:
        row = tsla_row()
        put_plan = [level["strike"] for level in row["putLevels"]]
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 351.5, "high": 352.6, "low": 351.2, "close": 352.1,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        call_confirms = [event for event in events if event["side"] == "CALL" and event["kind"] == "confirm"]
        self.assertEqual([level["strike"] for level in call_confirms[0]["crossedLevels"]], [345, 350])
        decorated = decorate_alert_row(row)
        self.assertEqual([level["strike"] for level in decorated["putLevels"]], put_plan)
        self.assertEqual([event for event in events if event["side"] == "PUT"], [])
        self.assertEqual(decorated["callState"], "fired")

    def test_augment_ladder_never_flips_sides(self) -> None:
        # Retained helper: no longer applied mid-session. Under the sheet
        # convention a wall's side is fixed at the build split, so even called
        # directly with price above the 345/350 call walls it must NOT turn
        # them into put levels — and with ladder == universe it adds nothing.
        row = tsla_row()
        widened, added = augment_ladder(row, 352.1)
        self.assertEqual(added, [])
        self.assertEqual(
            [level["strike"] for level in widened["putLevels"]],
            [level["strike"] for level in row["putLevels"]],
        )
        self.assertNotIn(350, [level["strike"] for level in widened["putLevels"]])
        # Idempotent: a second pass at the same price adds nothing.
        again, added_again = augment_ladder(widened, 352.1)
        self.assertEqual(added_again, [])
        self.assertEqual(again["putLevels"], widened["putLevels"])

    def test_the_put_board_only_arms_put_contracts(self) -> None:
        # The trader's 2026-08-20 decision, learned from the MSFT loss: the
        # 9:35 PUT alert fired at 482.5 off a deep-ITM CALL contract's 24K OI
        # while the sheet's put board started at 480 with the put's 8.4K.
        # AMZN-style fixture: the 265 call wall sits below price 265.7 — it
        # must NOT arm as a put trigger; the put board is 262.5 then 260, and
        # only a close through 262.5 fires.
        rows = [
            {"side": "CALL", "strike": 265, "delta": 999, "open_interest": 10700, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 267.5, "delta": 999, "open_interest": 2400, "volume": 1, "expiry": "2026-08-21"},
            {"side": "CALL", "strike": 270, "delta": 999, "open_interest": 44000, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 262.5, "delta": 999, "open_interest": 1700, "volume": 1, "expiry": "2026-08-21"},
            {"side": "PUT", "strike": 260, "delta": 999, "open_interest": 10800, "volume": 1, "expiry": "2026-08-21"},
        ]
        row = new_alert_row("AMZN", build_oi_ladder(rows, 265.7, as_of=date(2026, 8, 17), expected_move=3.34))
        self.assertEqual([level["strike"] for level in row["putLevels"]], [262.5, 260])
        self.assertEqual(decorate_alert_row(row)["activePut"]["strike"], 262.5)
        # A close under the old 265 call wall is silent — not a put level.
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 265.4, "high": 265.6, "low": 264.6, "close": 264.8,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        self.assertEqual([event for event in events if event["kind"] == "confirm"], [])
        # The sheet's first put wall confirming is the alert.
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 263.0, "high": 263.2, "low": 262.0, "close": 262.3,
                 "start": "2026-08-17T09:40:00-04:00", "end": "2026-08-17T09:45:00-04:00"},
        )
        put_confirms = [event for event in events if event["side"] == "PUT" and event["kind"] == "confirm"]
        self.assertEqual(len(put_confirms), 1)
        self.assertEqual(put_confirms[0]["level"]["strike"], 262.5)
        self.assertEqual(put_confirms[0]["nextTarget"]["strike"], 260)
        self.assertEqual(put_confirms[0]["nextTarget"]["openInterest"], 10_800)
        # Puts read CONFIRMED too - the arrow and side carry the direction.
        message = format_event_message(put_confirms[0])
        self.assertIn("CONFIRMED", message)
        self.assertNotIn("BROKEN", message)

    def test_rows_without_a_universe_still_evaluate(self) -> None:
        row = tsla_row()
        row.pop("universe", None)
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 344.8, "high": 345.6, "low": 344.1, "close": 345.25,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        self.assertEqual([event["kind"] for event in events], ["confirm"])

    def test_intrabar_touch_uses_high_low_only(self) -> None:
        row = tsla_row()
        row, events = apply_intrabar_touch(row, high=345.02, low=341.0, at="2026-08-17T09:31:00-04:00")
        self.assertEqual([event["kind"] for event in events], ["touch"])
        self.assertEqual(events[0]["level"]["strike"], 345)
        # No confirmation from a touch even when the wick went far through.
        self.assertEqual(decorate_alert_row(row)["activeCall"]["strike"], 345)
        # Puts touch on the low.
        row, events = apply_intrabar_touch(row, high=342.0, low=339.9, at="2026-08-17T09:32:00-04:00")
        self.assertEqual([event["side"] for event in events], ["PUT"])
        self.assertEqual(events[0]["level"]["strike"], 340)
        # Already-touched levels are silent.
        row, events = apply_intrabar_touch(row, high=346.0, low=339.0, at="2026-08-17T09:33:00-04:00")
        self.assertEqual(events, [])

    def test_confirmed_bar_touch_is_folded_into_the_confirm_event(self) -> None:
        row = tsla_row()
        row, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 344.8, "high": 346.0, "low": 344.1, "close": 345.25,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        self.assertEqual([event["kind"] for event in events], ["confirm"])

    def test_event_messages(self) -> None:
        row = tsla_row()
        _, events = apply_completed_five_minute_bar(
            row,
            bar={"open": 344.8, "high": 345.6, "low": 344.1, "close": 345.25,
                 "start": "2026-08-17T09:35:00-04:00", "end": "2026-08-17T09:40:00-04:00"},
        )
        message = format_event_message(events[0])
        self.assertIn("TSLA", message)
        self.assertIn("CALL OI 345", message)
        self.assertIn("345.25", message)
        self.assertIn("350", message)
        self.assertIn("17.8K", message)
        self.assertIn("1.38%", message)
        _, touch_events = apply_completed_five_minute_bar(
            tsla_row(),
            bar={"open": 342.0, "high": 346.10, "low": 341.5, "close": 344.80,
                 "start": "2026-08-17T09:30:00-04:00", "end": "2026-08-17T09:35:00-04:00"},
        )
        touch_message = format_event_message(touch_events[0])
        self.assertIn("touch", touch_message.lower())
        self.assertIn("not confirmed", touch_message.lower())


class ScheduleTests(unittest.TestCase):
    def test_next_refresh_at_skips_weekends(self) -> None:
        # Sunday night -> Monday 09:15 ET.
        self.assertEqual(next_refresh_at(et(2026, 8, 16, 22, 30)), et(2026, 8, 17, 9, 15))
        # Monday 09:00 -> same day 09:15.
        self.assertEqual(next_refresh_at(et(2026, 8, 17, 9, 0)), et(2026, 8, 17, 9, 15))
        # Monday 09:15 exactly -> Tuesday (a refresh is due strictly after now).
        self.assertEqual(next_refresh_at(et(2026, 8, 17, 9, 15)), et(2026, 8, 18, 9, 15))
        # Friday afternoon -> Monday.
        self.assertEqual(next_refresh_at(et(2026, 8, 21, 15, 0)), et(2026, 8, 24, 9, 15))
        # Naive datetimes are treated as Eastern.
        self.assertEqual(next_refresh_at(datetime(2026, 8, 17, 9, 0)), et(2026, 8, 17, 9, 15))

    def test_latest_completed_five_minute_bar_end(self) -> None:
        self.assertIsNone(latest_completed_five_minute_bar_end(et(2026, 8, 17, 9, 29)))
        self.assertIsNone(latest_completed_five_minute_bar_end(et(2026, 8, 17, 9, 34, 59)))
        # First bar closes at 09:35; a few seconds of grace lets the feed settle.
        self.assertIsNone(latest_completed_five_minute_bar_end(et(2026, 8, 17, 9, 35, 2), grace_seconds=8))
        self.assertEqual(latest_completed_five_minute_bar_end(et(2026, 8, 17, 9, 35, 9), grace_seconds=8), et(2026, 8, 17, 9, 35))
        self.assertEqual(latest_completed_five_minute_bar_end(et(2026, 8, 17, 10, 7)), et(2026, 8, 17, 10, 5))
        # The 16:00 bar is the last one; after the close it stays 16:00.
        self.assertEqual(latest_completed_five_minute_bar_end(et(2026, 8, 17, 16, 0, 30)), et(2026, 8, 17, 16, 0))
        self.assertEqual(latest_completed_five_minute_bar_end(et(2026, 8, 17, 18, 0)), et(2026, 8, 17, 16, 0))
        # Weekends have no bars.
        self.assertIsNone(latest_completed_five_minute_bar_end(et(2026, 8, 16, 12, 0)))

    def test_five_minute_bar_from_minute_bars(self) -> None:
        minute_bars = [
            {"timestamp": et(2026, 8, 17, 9, 29), "open": 341, "high": 341.5, "low": 340.9, "close": 341.2, "volume": 100},
            {"timestamp": et(2026, 8, 17, 9, 30), "open": 342.0, "high": 343.0, "low": 341.5, "close": 342.5, "volume": 100},
            {"timestamp": et(2026, 8, 17, 9, 31), "open": 342.5, "high": 346.1, "low": 342.4, "close": 345.9, "volume": 100},
            {"timestamp": et(2026, 8, 17, 9, 33), "open": 345.9, "high": 346.0, "low": 344.5, "close": 344.8, "volume": 100},
            {"timestamp": et(2026, 8, 17, 9, 35), "open": 344.8, "high": 345.5, "low": 344.6, "close": 345.3, "volume": 100},
        ]
        bar = five_minute_bar_from_minute_bars(minute_bars, end=et(2026, 8, 17, 9, 35))
        self.assertEqual(bar["open"], 342.0)
        self.assertEqual(bar["high"], 346.1)
        self.assertEqual(bar["low"], 341.5)
        self.assertEqual(bar["close"], 344.8)
        self.assertEqual(bar["volume"], 300)
        self.assertEqual(bar["start"], "2026-08-17T09:30:00-04:00")
        self.assertEqual(bar["end"], "2026-08-17T09:35:00-04:00")
        self.assertIsNone(five_minute_bar_from_minute_bars(minute_bars, end=et(2026, 8, 17, 9, 45)))


class OneShotAlertTests(unittest.TestCase):
    """One call alert and one put alert per ticker per session, then silence.

    Before this, every confirmation promoted the next wall and re-armed the
    side, so a single ticker alerted all day.
    """

    @staticmethod
    def _bar(end_minute, close, *, high=None, low=None):
        close = float(close)
        return {
            "open": close,
            "high": float(high if high is not None else close),
            "low": float(low if low is not None else close),
            "close": close,
            "volume": 1_000,
            "start": "",
            "end": et(2026, 8, 17, 10, end_minute).isoformat(),
        }

    def test_call_side_fires_once_then_goes_quiet(self) -> None:
        row = tsla_row()
        row, first = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        self.assertEqual([event["kind"] for event in first], ["confirm"])
        self.assertTrue(side_has_fired(row, "CALL"))

        # Closing through the NEXT wall must not alert again.
        row, second = apply_completed_five_minute_bar(row, bar=self._bar(10, 351.0))
        self.assertEqual(second, [])
        row, third = apply_completed_five_minute_bar(row, bar=self._bar(15, 361.0))
        self.assertEqual(third, [])

    def test_put_side_is_independent_of_the_call_side(self) -> None:
        row = tsla_row()
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        self.assertTrue(side_has_fired(row, "CALL"))
        self.assertFalse(side_has_fired(row, "PUT"))

        row, put_events = apply_completed_five_minute_bar(row, bar=self._bar(20, 339.0))
        self.assertEqual([event["side"] for event in put_events], ["PUT"])
        self.assertTrue(side_has_fired(row, "PUT"))

    def test_touch_warns_but_does_not_spend_the_alert(self) -> None:
        # A wick to the level warns; the side stays armed so the real close
        # still fires. This is the trader's chosen behaviour.
        row = tsla_row()
        row, touch_events = apply_completed_five_minute_bar(
            row, bar=self._bar(5, 344.0, high=345.5)
        )
        self.assertEqual([event["kind"] for event in touch_events], ["touch"])
        self.assertFalse(side_has_fired(row, "CALL"))

        row, confirm_events = apply_completed_five_minute_bar(row, bar=self._bar(10, 346.0))
        self.assertEqual([event["kind"] for event in confirm_events], ["confirm"])
        self.assertTrue(side_has_fired(row, "CALL"))

    def test_a_spent_side_stops_warning_too(self) -> None:
        row = tsla_row()
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        row, events = apply_completed_five_minute_bar(row, bar=self._bar(10, 346.5, high=350.2))
        self.assertEqual(events, [])

    def test_levels_do_not_move_during_the_session(self) -> None:
        # The 9:15 premarket lines ARE the plan; confirming one must not promote
        # a new wall onto the chart mid-session.
        row = tsla_row()
        before = [level["strike"] for level in row["callLevels"]]
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        self.assertEqual([level["strike"] for level in row["callLevels"]], before)

    def test_rearm_lets_each_side_fire_once_more(self) -> None:
        row = tsla_row()
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        self.assertTrue(side_has_fired(row, "CALL"))

        row = rearm_row(row, at="2026-08-17T10:30:00-04:00")
        self.assertFalse(side_has_fired(row, "CALL"))
        self.assertFalse(side_has_fired(row, "PUT"))
        self.assertEqual(row["rearmedAt"], "2026-08-17T10:30:00-04:00")

        # Re-armed, it watches the NEXT wall - not the one already broken.
        row, events = apply_completed_five_minute_bar(row, bar=self._bar(25, 351.0))
        self.assertEqual([event["kind"] for event in events], ["confirm"])
        self.assertEqual(events[0]["level"]["strike"], 350)

    def test_a_new_session_starts_armed(self) -> None:
        row = tsla_row()
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        self.assertTrue(side_has_fired(row, "CALL"))
        fresh = tsla_row()
        self.assertFalse(side_has_fired(fresh, "CALL"))
        self.assertFalse(side_has_fired(fresh, "PUT"))

    def test_decorated_row_exposes_fired_state_and_rearm_flag(self) -> None:
        row = tsla_row()
        self.assertFalse(decorate_alert_row(row)["rearmAvailable"])
        row, _ = apply_completed_five_minute_bar(row, bar=self._bar(5, 346.0))
        decorated = decorate_alert_row(row)
        self.assertEqual(decorated["callState"], "fired")
        self.assertTrue(decorated["rearmAvailable"])
        self.assertNotEqual(decorated["putState"], "fired")


if __name__ == "__main__":
    unittest.main()
