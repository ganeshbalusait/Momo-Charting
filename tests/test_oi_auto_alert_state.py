from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from api_server import DashboardState


ET = ZoneInfo("America/New_York")


class FakeRepository:
    def __init__(self, initial: dict | None = None) -> None:
        self.settings = dict(initial or {})
        self.events: list[tuple[str, str]] = []

    def get_app_settings(self) -> dict[str, str]:
        return dict(self.settings)

    def set_app_setting(self, key: str, value: str) -> None:
        self.settings[key] = value

    def log_bot_event(self, event_type: str, message: str, payload_json: str = "") -> None:
        self.events.append((event_type, message))


class FakeClock:
    def __init__(self, is_open: bool, next_open: datetime) -> None:
        self.is_open = is_open
        self.next_open = next_open
        self.timestamp = None
        self.next_close = None


class FakeBrokerClient:
    def __init__(self, clock: FakeClock) -> None:
        self.clock = clock

    def get_clock(self) -> FakeClock:
        return self.clock


class FakeMarketData:
    def __init__(self, frames: dict[str, pd.DataFrame]) -> None:
        self.frames = frames
        self.calls: list[tuple[str, str, int]] = []

    def get_chart_bars(self, symbol: str, timeframe: str = "1Min", days_back: int = 2) -> pd.DataFrame:
        self.calls.append((symbol, timeframe, days_back))
        return self.frames.get(symbol.upper(), pd.DataFrame())


# Every ladder assertion below is pinned to a fixed ON-TIME build. The build
# clock decides the plan price, and the sheet board splits at that anchor
# (call contracts above, put contracts at/below), so an unpinned build
# asserts one board before 09:20 ET and a different one after: chain_rows()'s
# 340/330 PUT walls simply drop off a late build anchored below them (they
# never flip sides). That is correct behaviour - the test was simply reading
# the wall clock.
def on_time_build() -> datetime:
    """TODAY at 09:15:20 ET - an on-time build, whatever hour the suite runs.

    Only the time of day is pinned, not the date: the plan price depends on
    how late the build is, while the session date, midnight roll and history
    rows all key off today. Freezing the whole timestamp to a past date fixed
    the ladder and broke those instead.
    """
    return datetime.now(ET).replace(hour=9, minute=15, second=20, microsecond=0)


def chain_rows() -> list[dict]:
    return [
        {"side": "CALL", "strike": 345, "delta": 0.44, "volume": 9_000, "open_interest": 6_541, "expiry": "2026-08-21", "days_to_expiration": 4},
        {"side": "CALL", "strike": 350, "delta": 0.36, "volume": 12_000, "open_interest": 17_753, "expiry": "2026-08-21", "days_to_expiration": 4},
        {"side": "CALL", "strike": 360, "delta": 0.24, "volume": 7_000, "open_interest": 12_844, "expiry": "2026-08-21", "days_to_expiration": 4},
        {"side": "PUT", "strike": 340, "delta": 0.45, "volume": 5_000, "open_interest": 5_910, "expiry": "2026-08-21", "days_to_expiration": 4},
        {"side": "PUT", "strike": 330, "delta": 0.28, "volume": 3_000, "open_interest": 7_094, "expiry": "2026-08-21", "days_to_expiration": 4},
    ]


def minute_frame(day: datetime, prices: list[tuple[int, int, float, float, float, float]]) -> pd.DataFrame:
    rows = []
    for hour, minute, open_, high, low, close in prices:
        stamp = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        rows.append({"timestamp": stamp, "open": open_, "high": high, "low": low, "close": close, "volume": 100, "trade_count": 1, "session_vwap": close})
    return pd.DataFrame(rows)


def make_state(repository: FakeRepository, *, frames: dict[str, pd.DataFrame] | None = None, clock: FakeClock | None = None) -> DashboardState:
    state = DashboardState.__new__(DashboardState)
    state.repository = repository
    state.market_data_client = FakeMarketData(frames or {})
    state.client = FakeBrokerClient(clock or FakeClock(False, datetime(2026, 8, 17, 9, 30, tzinfo=ET)))
    state.payloads: dict[str, dict] = {}
    state.payload_calls: list[str] = []

    def fake_payload(symbol: str, force: bool = False, compact: bool = False, initial_paint: bool = False, mobile_fast: bool = False, background_snapshot: bool = False, research_section: str = ""):
        state.payload_calls.append(symbol)
        if symbol in state.payloads:
            return state.payloads[symbol]
        return {"live": False, "symbol": symbol, "errors": [{"error": f"{symbol} chain unavailable"}], "callRows": [], "putRows": []}

    state.oi_finder_payload = fake_payload  # type: ignore[method-assign]
    state.OI_AUTO_ALERT_SYMBOL_PAUSE_SECONDS = 0.0
    state._init_oi_auto_alert_state()
    return state


class OiAutoAlertStateTests(unittest.TestCase):
    def setUp(self) -> None:
        # These tests exercise persistence, so they run AS the serving
        # process; the ghost-writer guard (see GhostWriterGuardTests) would
        # otherwise correctly turn every persist into a no-op.
        import api_server as module
        self._was_serving = module._IS_SERVING_PROCESS
        module._IS_SERVING_PROCESS = True
        self.addCleanup(setattr, module, "_IS_SERVING_PROCESS", self._was_serving)

    def test_defaults_and_settings_round_trip(self) -> None:
        repository = FakeRepository()
        state = make_state(repository)
        self.assertTrue(state.oi_auto_alert_enabled)
        self.assertTrue(state.oi_auto_alert_include_mag7)
        self.assertEqual(state.oi_auto_alert_manual_symbols, [])
        self.assertEqual(state._oi_auto_alert_symbols(), ["AAPL", "MSFT", "NVDA", "AMZN", "META", "GOOGL", "TSLA"])

        payload = state.update_oi_auto_alert_settings({"enabled": False, "includeMag7": False, "manualSymbols": ["tsla", "amd", "tsla"]})
        self.assertFalse(payload["enabled"])
        self.assertFalse(payload["includeMag7"])
        self.assertEqual(payload["manualSymbols"], ["TSLA", "AMD"])
        self.assertEqual(repository.settings["oi_auto_alert_enabled"], "false")
        self.assertEqual(json.loads(repository.settings["oi_auto_alert_manual_symbols"]), ["TSLA", "AMD"])
        # A new added ticker queues an immediate ladder build.
        self.assertEqual(state.oi_auto_alert_refresh_requests, [(["AMD", "TSLA"], "added")])

        reloaded = make_state(FakeRepository(repository.settings))
        self.assertFalse(reloaded.oi_auto_alert_enabled)
        self.assertFalse(reloaded.oi_auto_alert_include_mag7)
        self.assertEqual(reloaded.oi_auto_alert_manual_symbols, ["TSLA", "AMD"])
        self.assertEqual(reloaded._oi_auto_alert_symbols(), ["TSLA", "AMD"])

    def test_add_and_remove_symbols(self) -> None:
        state = make_state(FakeRepository())
        status, payload = state.update_oi_auto_alert_symbols({"add": "amd"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["manualSymbols"], ["AMD"])
        status, payload = state.update_oi_auto_alert_symbols({"add": "1bad"})
        self.assertEqual(status, 400)
        status, payload = state.update_oi_auto_alert_symbols({"remove": "AMD"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["manualSymbols"], [])
        # MAG7 names are already covered while the MAG7 group is on.
        status, payload = state.update_oi_auto_alert_symbols({"add": "TSLA"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["manualSymbols"], [])

    def test_refresh_builds_ladders_and_reports_failures(self) -> None:
        repository = FakeRepository()
        state = make_state(repository)
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA", "XXXX"]})
        state.oi_auto_alert_refresh_requests = []
        state.payloads["TSLA"] = {
            "live": True,
            "symbol": "TSLA",
            "source": "Schwab/TOS option chain",
            "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63,
            "selectedExpiryChainRows": chain_rows(),
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())
        self.assertEqual(state.payload_calls, ["TSLA", "XXXX"])
        tsla = state.oi_auto_alert_rows["TSLA"]
        self.assertEqual([level["strike"] for level in tsla["callLevels"]], [345, 350, 360])
        self.assertEqual([level["strike"] for level in tsla["putLevels"]], [340, 330])
        self.assertEqual(tsla["status"], "armed")
        self.assertEqual(tsla["sourceGroup"], "manual")
        self.assertEqual(state.oi_auto_alert_rows["XXXX"]["status"], "unavailable")
        self.assertIn("XXXX chain unavailable", state.oi_auto_alert_rows["XXXX"]["message"])
        self.assertEqual(state.oi_auto_alert_status, "Partial")
        self.assertEqual(state.oi_auto_alert_retry_symbols, ["XXXX"])
        self.assertIsNotNone(state.oi_auto_alert_retry_at)
        self.assertEqual(state.oi_auto_alert_last_refresh_date, datetime.now(ET).date().isoformat())
        # Persisted rows reload on the next boot.
        reloaded = make_state(FakeRepository(repository.settings))
        self.assertEqual(reloaded.oi_auto_alert_rows["TSLA"]["callLevels"][0]["strike"], 345)
        payload = reloaded.oi_auto_alert_payload()
        self.assertEqual([row["symbol"] for row in payload["rows"]], ["TSLA", "XXXX"])
        self.assertEqual(payload["rows"][0]["activeCall"]["strike"], 345)
        self.assertEqual(payload["rows"][0]["nextCall"]["strike"], 350)
        self.assertEqual(payload["rows"][0]["callState"], "armed")

    def test_evaluate_confirms_from_completed_five_minute_close_and_touches(self) -> None:
        repository = FakeRepository()
        day = datetime(2026, 8, 17, tzinfo=ET)
        frame = minute_frame(day, [
            (9, 30, 342.0, 343.0, 341.5, 342.5),
            (9, 31, 342.5, 346.1, 342.4, 345.9),   # wick through 345
            (9, 32, 345.9, 346.0, 344.5, 344.8),
            (9, 33, 344.8, 345.0, 344.2, 344.6),
            (9, 34, 344.6, 344.9, 344.1, 344.8),   # 09:30-09:35 closes 344.8 -> touch only
            (9, 35, 344.8, 345.6, 344.6, 345.1),
            (9, 36, 345.1, 345.7, 344.9, 345.4),
            (9, 37, 345.4, 345.9, 345.0, 345.3),
            (9, 38, 345.3, 345.5, 344.9, 345.2),
            (9, 39, 345.2, 345.6, 345.0, 345.25),  # 09:35-09:40 closes 345.25 -> confirm 345
            (9, 40, 345.2, 345.5, 345.0, 345.3),   # forming
        ])
        state = make_state(repository, frames={"TSLA": frame})
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
        state.oi_auto_alert_refresh_requests = []
        state.payloads["TSLA"] = {
            "live": True, "symbol": "TSLA", "source": "Schwab", "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63, "selectedExpiryChainRows": chain_rows(),
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())

        # First pass at 09:35:20 — only the 09:30 candle is complete: touch.
        state._oi_auto_alert_evaluate(day.replace(hour=9, minute=35, second=20))
        row = state.oi_auto_alert_rows["TSLA"]
        self.assertEqual(row["lastProcessedBar"], "2026-08-17T09:35:00-04:00")
        self.assertEqual(row["touchedCallStrikes"], [345])
        self.assertEqual(row["confirmedCallStrikes"], [])
        self.assertEqual([event["kind"] for event in state.oi_auto_alert_events], ["touch"])
        self.assertIn("touching CALL OI 345", state.oi_auto_alert_events[0]["message"])

        # Second pass at 09:40:20 — the 09:35 candle closed above 345.
        state._oi_auto_alert_evaluate(day.replace(hour=9, minute=40, second=20))
        row = state.oi_auto_alert_rows["TSLA"]
        self.assertEqual(row["lastProcessedBar"], "2026-08-17T09:40:00-04:00")
        self.assertEqual(row["confirmedCallStrikes"], [345])
        kinds = [event["kind"] for event in state.oi_auto_alert_events]
        self.assertEqual(kinds, ["touch", "confirm"])
        confirm = state.oi_auto_alert_events[-1]
        self.assertEqual(confirm["nextTarget"]["strike"], 350)
        self.assertIn("CALL OI 345 CONFIRMED", confirm["message"])
        self.assertIn("Next OI target 350", confirm["message"])
        self.assertEqual(repository.events[-1][0], "oi_auto_alert")
        payload = state.oi_auto_alert_payload()
        self.assertEqual(payload["rows"][0]["activeCall"]["strike"], 350)
        # The call side has spent its one alert for the session; the UI shows a
        # Re-arm button instead of continuing to chase 350.
        self.assertEqual(payload["rows"][0]["callState"], "fired")
        self.assertTrue(payload["rows"][0]["rearmAvailable"])
        # The live feed is scoped to the CURRENT session; this fixture's bars are
        # dated to a past one, so assert against the log the feed is drawn from.
        self.assertEqual(state.oi_auto_alert_events[-1]["kind"], "confirm")
        # Persisted progress survives a reload.
        reloaded = make_state(FakeRepository(repository.settings), frames={"TSLA": frame})
        self.assertEqual(reloaded.oi_auto_alert_rows["TSLA"]["confirmedCallStrikes"], [345])
        self.assertEqual(len(reloaded.oi_auto_alert_events), 2)

        # A third pass with no new candle is idempotent.
        state._oi_auto_alert_evaluate(day.replace(hour=9, minute=40, second=50))
        self.assertEqual(len(state.oi_auto_alert_events), 2)

    def test_evaluate_replays_missed_candles_after_downtime(self) -> None:
        day = datetime(2026, 8, 17, tzinfo=ET)
        bars = []
        # 09:30-09:40 flat, 09:40-09:45 closes above 345 then falls back below by 09:50.
        for minute in range(30, 40):
            bars.append((9, minute, 342.0, 342.5, 341.8, 342.2))
        for minute in range(40, 45):
            bars.append((9, minute, 345.0, 346.0, 344.9, 345.5))
        for minute in range(45, 55):
            bars.append((9, minute, 343.0, 343.5, 342.5, 343.0))
        frame = minute_frame(day, bars)
        state = make_state(FakeRepository(), frames={"TSLA": frame})
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
        state.payloads["TSLA"] = {
            "live": True, "symbol": "TSLA", "source": "Schwab", "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63, "selectedExpiryChainRows": chain_rows(),
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())
        # The worker only wakes at 09:55 (server was down): the 09:45 close still confirms.
        state._oi_auto_alert_evaluate(day.replace(hour=9, minute=55, second=10))
        row = state.oi_auto_alert_rows["TSLA"]
        self.assertEqual(row["confirmedCallStrikes"], [345])
        self.assertEqual(row["lastProcessedBar"], "2026-08-17T09:55:00-04:00")
        # ONE confirm for the replayed downtime, not a burst. 09:45 closes above
        # 345 and spends the call side; price falling back through the same wall
        # no longer re-fires it as put support, because the put side watches the
        # levels drawn at the 9:15 build and the ladder is frozen for the session.
        kinds = [event["kind"] for event in state.oi_auto_alert_events]
        sides = [event["side"] for event in state.oi_auto_alert_events]
        self.assertEqual(kinds, ["confirm"])
        self.assertEqual(sides, ["CALL"])
        self.assertEqual(state.oi_auto_alert_events[0]["barEndedAt"], "2026-08-17T09:45:00-04:00")
        self.assertFalse(state.oi_auto_alert_rows["TSLA"]["putFiredAt"])

    def test_a_late_morning_build_anchors_the_plan_to_the_0915_print(self) -> None:
        # The server was down at 09:15 on 2026-08-17, the ladder was built at
        # 10:31 off a price 45 minutes into the session, and the trader's chart
        # stopped matching the plan. A late build must reach back to 09:15.
        day = datetime(2026, 8, 17, tzinfo=ET)
        frame = minute_frame(day, [
            (9, 10, 341.0, 341.2, 340.8, 341.10),
            (9, 15, 341.1, 341.7, 341.0, 341.63),   # the plan price
            (9, 45, 347.0, 348.0, 346.5, 347.50),   # well past it
            (10, 30, 352.0, 352.4, 351.6, 352.10),  # what a late build would see
        ])
        state = make_state(FakeRepository(), frames={"TSLA": frame})
        late = day.replace(hour=10, minute=31)
        price, source = state._oi_auto_alert_plan_price("TSLA", late, 352.10)
        self.assertAlmostEqual(price, 341.63, places=2)
        self.assertIn("09:15", source)

    def test_an_on_time_build_uses_the_live_spot(self) -> None:
        day = datetime(2026, 8, 17, tzinfo=ET)
        state = make_state(FakeRepository(), frames={"TSLA": minute_frame(day, [(9, 15, 341.1, 341.7, 341.0, 341.63)])})
        on_time = day.replace(hour=9, minute=15, second=20)
        price, source = state._oi_auto_alert_plan_price("TSLA", on_time, 341.63)
        self.assertAlmostEqual(price, 341.63, places=2)
        self.assertEqual(source, "live")

    def test_a_late_build_without_premarket_prints_falls_back_to_live(self) -> None:
        # A plan built off the wrong price still beats no plan; it just says so.
        day = datetime(2026, 8, 17, tzinfo=ET)
        state = make_state(FakeRepository(), frames={"TSLA": minute_frame(day, [(10, 30, 352.0, 352.4, 351.6, 352.10)])})
        price, source = state._oi_auto_alert_plan_price("TSLA", day.replace(hour=10, minute=31), 352.10)
        self.assertAlmostEqual(price, 352.10, places=2)
        self.assertIn("live", source)

    def test_the_build_clock_decides_which_walls_arm(self) -> None:
        """Why every ladder assertion here pins the clock.

        This is the flake that failed only after ~09:20 ET. The plan price is
        the live spot for an on-time build and the 09:15 print for a late one,
        and the sheet board splits at that anchor: call contracts above it,
        put contracts at/below it. With the premarket print well under the
        340/330 put walls, a late build arms NO puts — and, per the sheet
        convention, those put contracts are dropped rather than promoted into
        the call ladder. Different clock, different board, invisible to a test
        that reads the wall clock.
        """
        # Most recent WEEKDAY, not the wall-clock date: the arming logic has
        # weekend branches, so building this fixture on a real Saturday made
        # the late build arm [340, 330] where a weekday arms none -- the test
        # failed every weekend and only weekends (2026-08-22 triage).
        today = datetime.now(ET).date()
        while today.weekday() >= 5:
            today -= timedelta(days=1)
        day = datetime(today.year, today.month, today.day, tzinfo=ET)
        # A premarket print far below the 340 wall, so the two builds disagree.
        frame = minute_frame(day, [(9, 15, 300.0, 300.4, 299.6, 300.10)])

        def ladder(when: datetime) -> tuple[list[float], list[float]]:
            state = make_state(FakeRepository(), frames={"TSLA": frame})
            state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
            state.oi_auto_alert_refresh_requests = []
            state.payloads["TSLA"] = {
                "live": True, "symbol": "TSLA", "source": "Schwab",
                "scannedAt": f"{today.isoformat()}T09:15:00-04:00",
                "underlyingPrice": 341.63,
                "selectedExpiryChainRows": chain_rows(),
            }
            state._oi_auto_alert_refresh_levels(None, reason="morning", now=when)
            row = state.oi_auto_alert_rows["TSLA"]
            return (
                [level["strike"] for level in row["callLevels"]],
                [level["strike"] for level in row["putLevels"]],
            )

        on_time_calls, on_time_puts = ladder(day.replace(hour=9, minute=15, second=20))
        late_calls, late_puts = ladder(day.replace(hour=16, minute=30))
        self.assertEqual(on_time_calls, [345, 350, 360])
        self.assertEqual(on_time_puts, [340, 330])
        # Anchored at 300.10, every put wall sits above the anchor: none arm,
        # and none sneak into the call side either.
        self.assertEqual(late_puts, [])
        self.assertEqual(late_calls, [345, 350, 360])
        self.assertNotEqual(on_time_puts, late_puts)

    def test_history_keeps_thirty_days_and_drops_older_alerts(self) -> None:
        state = make_state(FakeRepository())
        today = datetime.now(ET).date()

        def confirm(day_offset: int, symbol: str, side: str) -> dict:
            day = (today - timedelta(days=day_offset)).isoformat()
            return {
                "id": f"{symbol}:{side}:{day}", "symbol": symbol, "side": side, "kind": "confirm",
                "level": {"strike": 100}, "price": 101.0, "at": f"{day}T10:00:00-04:00",
            }

        state._oi_auto_alert_record_events([
            confirm(0, "TSLA", "CALL"),
            confirm(29, "AAPL", "PUT"),    # inside the window
            confirm(45, "NVDA", "CALL"),   # aged out
        ])
        kept = {event["symbol"] for event in state.oi_auto_alert_events}
        self.assertEqual(kept, {"TSLA", "AAPL"})

        days = state._oi_auto_alert_session_summary()["days"]
        self.assertEqual(days[0]["date"], today.isoformat())
        self.assertEqual(days[0]["symbols"][0]["symbol"], "TSLA")
        self.assertEqual(days[0]["alertCount"], 1)
        # Newest session first, so the morning read is the top row.
        self.assertTrue(days[0]["date"] > days[-1]["date"])

    def test_each_session_is_its_own_history_row(self) -> None:
        state = make_state(FakeRepository())
        today = datetime.now(ET).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        state._oi_auto_alert_record_events([
            {"id": "a", "symbol": "TSLA", "side": "CALL", "kind": "confirm", "level": {"strike": 345},
             "price": 346.0, "at": f"{yesterday}T10:00:00-04:00"},
            {"id": "b", "symbol": "TSLA", "side": "PUT", "kind": "confirm", "level": {"strike": 340},
             "price": 339.0, "at": f"{today.isoformat()}T11:00:00-04:00"},
        ])
        days = {day["date"]: day for day in state._oi_auto_alert_session_summary()["days"]}
        self.assertIn(yesterday, days)
        self.assertIn(today.isoformat(), days)
        self.assertEqual(days[yesterday]["symbols"][0]["sides"], ["CALL"])
        self.assertEqual(days[today.isoformat()]["symbols"][0]["sides"], ["PUT"])

    def test_touches_are_not_counted_as_alerts_in_history(self) -> None:
        # History answers "which tickers fired", so an early-warning wick that
        # never confirmed must not appear as an alert for the day.
        state = make_state(FakeRepository())
        today = datetime.now(ET).date().isoformat()
        state._oi_auto_alert_record_events([
            {"id": "t", "symbol": "META", "side": "CALL", "kind": "touch", "level": {"strike": 565},
             "price": 565.2, "at": f"{today}T10:00:00-04:00"},
        ])
        self.assertEqual(state._oi_auto_alert_session_summary()["days"], [])

    # ---- dated alert history ------------------------------------------- #

    @staticmethod
    def _confirm(day: str, symbol: str, side: str, strike: float, hour: int = 10) -> dict:
        return {
            "id": f"{symbol}:{side}:{day}:{hour}", "symbol": symbol, "side": side,
            "kind": "confirm", "level": {"strike": strike},
            "crossedLevels": [{"strike": strike}], "price": strike + 1.0,
            "at": f"{day}T{hour:02d}:00:00-04:00",
        }

    def test_history_returns_one_session_and_every_date_that_has_alerts(self) -> None:
        state = make_state(FakeRepository())
        today = datetime.now(ET).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        state._oi_auto_alert_record_events([
            self._confirm(yesterday, "TSLA", "CALL", 335),
            self._confirm(yesterday, "AMD", "PUT", 485, hour=11),
            self._confirm(today.isoformat(), "AAPL", "CALL", 307.5),
        ])
        history = state.oi_auto_alert_history(yesterday)
        self.assertEqual(history["date"], yesterday)
        self.assertEqual(history["alertCount"], 2)
        self.assertEqual(history["symbols"], ["AMD", "TSLA"])
        # Newest first, so the calendar arrows and the list agree.
        self.assertEqual(history["availableDates"], [today.isoformat(), yesterday])
        self.assertEqual(history["events"][0]["symbol"], "AMD")

    def test_history_defaults_to_today_then_falls_back_to_the_last_session(self) -> None:
        """Premarket the trader wants yesterday's list, not an empty today."""
        state = make_state(FakeRepository())
        today = datetime.now(ET).date()
        yesterday = (today - timedelta(days=1)).isoformat()
        state._oi_auto_alert_record_events([self._confirm(yesterday, "TSLA", "CALL", 335)])
        self.assertEqual(state.oi_auto_alert_history()["date"], yesterday)

        state._oi_auto_alert_record_events([self._confirm(today.isoformat(), "AAPL", "CALL", 307.5)])
        self.assertEqual(state.oi_auto_alert_history()["date"], today.isoformat())

    def test_history_honours_a_quiet_day_that_was_asked_for(self) -> None:
        """An explicit date is shown even when empty - the trader asked for it."""
        state = make_state(FakeRepository())
        today = datetime.now(ET).date()
        quiet = (today - timedelta(days=2)).isoformat()
        state._oi_auto_alert_record_events([self._confirm(today.isoformat(), "AAPL", "CALL", 307.5)])
        history = state.oi_auto_alert_history(quiet)
        self.assertEqual(history["date"], quiet)
        self.assertEqual(history["alertCount"], 0)
        self.assertEqual(history["events"], [])

    def test_history_ignores_touch_warnings_and_bad_dates(self) -> None:
        state = make_state(FakeRepository())
        today = datetime.now(ET).date().isoformat()
        state._oi_auto_alert_record_events([
            self._confirm(today, "AAPL", "CALL", 307.5),
            {"id": "t", "symbol": "META", "side": "CALL", "kind": "touch", "level": {"strike": 565},
             "price": 565.2, "at": f"{today}T10:30:00-04:00"},
        ])
        self.assertEqual(state.oi_auto_alert_history(today)["alertCount"], 1)
        # Garbage in the query string falls back to the default, never raises.
        self.assertEqual(state.oi_auto_alert_history("not-a-date")["date"], today)

    def test_stored_put_messages_are_re_rendered_as_confirmed(self) -> None:
        """Messages are frozen at fire time, so history kept saying BROKEN.

        Re-rendering on serve is what makes a wording change reach the alerts
        that already fired instead of only the next ones.
        """
        state = make_state(FakeRepository())
        today = datetime.now(ET).date().isoformat()
        state._oi_auto_alert_record_events([self._confirm(today, "AMD", "PUT", 485)])
        # Simulate a record written before the rename.
        state.oi_auto_alert_events[-1]["message"] = "AMD ↓ PUT OI 485 BROKEN · 5m close 484.00"
        served = state.oi_auto_alert_history(today)["events"][0]
        self.assertIn("CONFIRMED", served["message"])
        self.assertNotIn("BROKEN", served["message"])
        # The stored record is untouched; only the served copy is re-rendered.
        self.assertIn("BROKEN", state.oi_auto_alert_events[-1]["message"])

    def test_touch_warnings_never_push_confirmations_out_of_the_payload(self) -> None:
        """The "do not miss any OI level" guarantee.

        The payload used to be a blind tail slice, so on a noisy day the
        earliest confirmations - the 9:40 ones - were the first to be dropped
        while touch warnings survived. Confirms are now kept in full.
        """
        state = make_state(FakeRepository())
        today = datetime.now(ET).date().isoformat()
        limit = state.OI_AUTO_ALERT_EVENT_PAYLOAD_LIMIT
        early = self._confirm(today, "NFLX", "CALL", 77, hour=9)
        noise = [
            {"id": f"touch-{index}", "symbol": "SPY", "side": "CALL", "kind": "touch",
             "level": {"strike": 768}, "price": 768.1,
             "at": f"{today}T10:{index % 60:02d}:00-04:00"}
            for index in range(limit + 50)
        ]
        state._oi_auto_alert_record_events([early] + noise)
        served = state.oi_auto_alert_payload()["events"]
        self.assertLessEqual(len(served), limit)
        self.assertIn("NFLX", {event["symbol"] for event in served})

    def _em_row(self, moves: dict) -> dict:
        state = make_state(FakeRepository())
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
        state.oi_auto_alert_refresh_requests = []
        state.payloads["TSLA"] = {
            "live": True, "symbol": "TSLA", "source": "Schwab",
            "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63,
            "selectedExpiryChainRows": chain_rows(),
            "expiryExpectedMoves": moves,
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())
        return state.oi_auto_alert_rows["TSLA"]

    def test_expected_move_skips_a_spent_expiry(self) -> None:
        """A 0-DTE read AFTER the close has no time value and collapses the band.

        Measured 2026-08-17 at 16:53: TSLA's 0-DTE read 0.915 against 7.525 for
        the next expiry - a ratio of 0.12. The band shrank to 338.9-340.7 and
        one wall survived out of sixteen.
        """
        row = self._em_row({"2026-08-17": 0.915, "2026-08-19": 7.525, "2026-08-21": 13.119})
        self.assertAlmostEqual(row["expectedMove"], 7.525, places=3)
        self.assertGreater(row["emUp"] - row["emDown"], 10)

    def test_expected_move_keeps_a_live_same_day_expiry(self) -> None:
        """A 0-DTE at the 9:15 build still has a full session of value.

        Measured premarket: SPY 2.87 against 4.19 for the next expiry, a ratio
        of 0.68 - the sqrt(2) time-scaling you expect, and the move the trader
        actually wants for today. Judging by DATE instead of decay would throw
        this away every morning and widen the band by ~46%.
        """
        row = self._em_row({"2026-08-17": 2.87, "2026-08-19": 4.19, "2026-08-21": 5.48})
        self.assertAlmostEqual(row["expectedMove"], 2.87, places=3)

    def test_expected_move_falls_back_when_only_one_expiry_is_priced(self) -> None:
        # Nothing to compare against: use it rather than produce no band at all.
        self.assertAlmostEqual(self._em_row({"2026-08-17": 0.915})["expectedMove"], 0.915, places=3)

    def test_midnight_roll_clears_yesterdays_progress_but_keeps_levels(self) -> None:
        """A new ET day starts clean at midnight, not at the 9:15 build.

        A card must not still read "CALL CONFIRMED 2:40 PM" at 8 a.m. from a
        session that ended the previous afternoon.
        """
        state = make_state(FakeRepository())
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
        state.oi_auto_alert_refresh_requests = []
        state.payloads["TSLA"] = {
            "live": True, "symbol": "TSLA", "source": "Schwab",
            "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63,
            "selectedExpiryChainRows": chain_rows(),
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())

        row = state.oi_auto_alert_rows["TSLA"]
        # The build stamps sessionDate from the real clock; pin it to yesterday
        # so the roll has something stale to act on.
        row["sessionDate"] = "2026-08-17"
        row["confirmedCallStrikes"] = [345]
        row["touchedPutStrikes"] = [340]
        row["callFiredAt"] = "2026-08-17T14:40:00-04:00"
        row["lastCallEvent"] = {"kind": "confirm"}
        row["lastProcessedBar"] = "2026-08-17T16:00:00-04:00"
        levels_before = [level["strike"] for level in row["callLevels"]]

        state._oi_auto_alert_roll_session("2026-08-18")
        rolled = state.oi_auto_alert_rows["TSLA"]

        self.assertEqual(rolled["sessionDate"], "2026-08-18")
        self.assertEqual(rolled["confirmedCallStrikes"], [])
        self.assertEqual(rolled["touchedPutStrikes"], [])
        self.assertIsNone(rolled["callFiredAt"])
        self.assertIsNone(rolled["putFiredAt"])
        self.assertIsNone(rolled["lastCallEvent"])
        self.assertIsNone(rolled["lastProcessedBar"])
        # Levels survive: they are still the best reference until 9:15 replaces
        # them, and dropping them would blank the chart lines overnight.
        self.assertEqual([level["strike"] for level in rolled["callLevels"]], levels_before)
        self.assertEqual(rolled["status"], "armed")


    def test_midnight_roll_is_a_no_op_within_the_same_session(self) -> None:
        state = make_state(FakeRepository())
        state.update_oi_auto_alert_settings({"includeMag7": False, "manualSymbols": ["TSLA"]})
        state.oi_auto_alert_refresh_requests = []
        state.payloads["TSLA"] = {
            "live": True, "symbol": "TSLA", "source": "Schwab",
            "scannedAt": "2026-08-17T09:15:00-04:00",
            "underlyingPrice": 341.63,
            "selectedExpiryChainRows": chain_rows(),
        }
        state._oi_auto_alert_refresh_levels(None, reason="morning", now=on_time_build())
        state.oi_auto_alert_rows["TSLA"]["confirmedCallStrikes"] = [345]
        same_day = str(state.oi_auto_alert_rows["TSLA"]["sessionDate"])

        state._oi_auto_alert_roll_session(same_day)
        self.assertEqual(state.oi_auto_alert_rows["TSLA"]["confirmedCallStrikes"], [345])

    def test_latest_alerts_feed_shows_only_todays_session(self) -> None:
        """Yesterday's alerts belong in the dated history, not the live feed.

        A 3:55 PM entry from the previous session still sitting at the top of
        the panel at 9:16 the next morning reads as if it just fired.
        """
        state = make_state(FakeRepository())
        today = datetime.now(ET).date().isoformat()
        yesterday = (datetime.now(ET).date() - timedelta(days=1)).isoformat()
        state._oi_auto_alert_record_events([
            {"id": "old", "symbol": "NVDA", "side": "PUT", "kind": "touch", "level": {"strike": 225},
             "price": 225.0, "at": f"{yesterday}T15:55:00-04:00"},
            {"id": "new", "symbol": "TSLA", "side": "CALL", "kind": "confirm", "level": {"strike": 345},
             "price": 346.0, "at": f"{today}T09:45:00-04:00"},
        ])
        payload = state.oi_auto_alert_payload()

        self.assertEqual([event["symbol"] for event in payload["events"]], ["TSLA"])
        # The full log is untouched, so history still carries yesterday.
        days = {day["date"] for day in payload["sessionAlerts"]["days"]}
        self.assertIn(today, days)
        self.assertEqual(len(state.oi_auto_alert_events), 2)

    def test_trading_day_uses_the_broker_clock(self) -> None:
        monday = datetime(2026, 8, 17, 9, 0, tzinfo=ET)
        state = make_state(FakeRepository(), clock=FakeClock(False, datetime(2026, 8, 17, 9, 30, tzinfo=ET)))
        self.assertTrue(state._oi_auto_alert_is_trading_day(monday))
        holiday_state = make_state(FakeRepository(), clock=FakeClock(False, datetime(2026, 8, 18, 9, 30, tzinfo=ET)))
        self.assertFalse(holiday_state._oi_auto_alert_is_trading_day(monday))
        self.assertFalse(holiday_state._oi_auto_alert_is_trading_day(datetime(2026, 8, 16, 9, 0, tzinfo=ET)))


if __name__ == "__main__":
    unittest.main()


class GhostWriterGuardTests(unittest.TestCase):
    """Only the serving process may persist alert state.

    Importing api_server boots a full DashboardState, so pytest runs and
    tooling scripts become ghost apps. Their periodic persist used to write a
    stale boot-time manual-symbols snapshot over the live server's - the list
    lost AMD (08-18), SPY (08-19) and SPY+MU (08-20) exactly this way.
    """

    def test_a_ghost_process_never_writes_alert_state(self) -> None:
        import api_server as module
        self.assertFalse(module._IS_SERVING_PROCESS)  # pytest IS a ghost
        repository = FakeRepository()
        state = make_state(repository)
        before = dict(repository.settings)
        state._persist_oi_auto_alert_state()
        self.assertEqual(repository.settings, before)

    def test_the_serving_process_still_persists(self) -> None:
        import api_server as module
        repository = FakeRepository()
        state = make_state(repository)
        original = module._IS_SERVING_PROCESS
        module._IS_SERVING_PROCESS = True
        try:
            state._persist_oi_auto_alert_state()
        finally:
            module._IS_SERVING_PROCESS = original
        self.assertIn("oi_auto_alert_manual_symbols", repository.settings)
