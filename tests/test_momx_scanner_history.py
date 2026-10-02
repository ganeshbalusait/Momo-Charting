"""The MomX 30-day scan-match archive (momx/history.py).

Mirrors tests/test_premarket_scanner_history.py: real files in a temp dir,
an injected clock, never the real archive. The spec is
docs/superpowers/specs/2026-08-31-momx-scanner-history-design.md; every rule
it pins for storage has a test here, including the 30-day prune boundary --
a path that has NEVER run in production anywhere in this codebase.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from momx import history
from momx.history import (
    CELL_TRIGGER_FIELDS,
    NON_TRIGGER_STORED_FIELDS,
    RETENTION_DAYS,
    SNAPSHOT_MIN_GAP_SECONDS,
    SNAPSHOTS_PER_SYMBOL_PER_DAY,
    STRIPPED_FIELDS,
    TIMEFRAME_TRIGGER_FIELDS,
    history_response,
    record_board,
)

ET = ZoneInfo("America/New_York")
T0 = datetime(2026, 8, 31, 8, 1, 12, tzinfo=ET)

LIST = "Watchlist"


def _row(symbol: str = "SNOW", **overrides: object) -> dict:
    """A board row shaped like the live payloads in artifacts/momx_board_cache."""
    row = {
        "symbol": symbol,
        "industry": "Software",
        "last": 60.4,
        "pctChange": 4.83,
        "scanPass": True,
        "scanReasons": ["rvol:D"],
        "rvol": {
            "1h": {"value": 1.4, "bg": "black", "fg": "yellow"},
            "D": {"value": 2.6, "bg": "green", "fg": "black"},
        },
        "sqz": {"2h": {"value": "-", "bg": "black", "fg": "black"}},
        "skittles": {"2h": {"value": 82, "bg": "black", "fg": "cyan"}},
        "highLow": {"value": 0.96, "bg": "green", "fg": "black"},
        "color": {"value": None, "bg": "white", "fg": None},
        "badge": {"on": True, "reasons": ["weeklies"], "tooltip": "Weekly options"},
        "news": {"headline": "SNOW wins a deal", "at": "2026-08-31T11:00:00+00:00"},
        "sparkline": [60.1, 60.2, 60.3],
        "quoteTrend": [{"value": 1, "bg": "dark_green", "fg": "green", "price": 60.2}],
        "matchedSince": "2026-08-31T12:01:00+00:00",
    }
    row.update(overrides)
    return row


def _payload(*rows: dict) -> dict:
    return {"list": LIST, "generatedAt": T0.isoformat(), "rows": list(rows)}


def _skittles(value: object, fg: str = "cyan") -> dict:
    return {"2h": {"value": value, "bg": "black", "fg": fg}}


def _high_low(value: object, bg: str = "green") -> dict:
    return {"value": value, "bg": bg, "fg": "black"}


def _entry(directory: Path, symbol: str = "SNOW", day: str | None = None) -> dict:
    day = day or T0.date().isoformat()
    path = history.board_dir(directory, LIST) / f"{day}.json"
    return json.loads(path.read_text(encoding="utf-8"))["rows"][symbol]


class ArrivalStampTests(unittest.TestCase):
    def test_first_seen_is_stamped_once_and_never_moves(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assertTrue(record_board(LIST, _payload(_row()), T0, directory))
            later = T0 + timedelta(minutes=30)
            record_board(
                LIST, _payload(_row(skittles=_skittles(95))), later, directory
            )
            entry = _entry(directory)
            self.assertEqual(entry["firstSeenAt"], T0.isoformat())
            self.assertEqual(entry["lastSeenAt"], later.isoformat())

    def test_symbol_that_drops_off_and_returns_keeps_its_stamp(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            # Two builds with SNOW gone, then it returns: same day, same stamp.
            record_board(LIST, _payload(_row("DG")), T0 + timedelta(minutes=5), directory)
            record_board(LIST, _payload(), T0 + timedelta(minutes=10), directory)
            record_board(LIST, _payload(_row()), T0 + timedelta(hours=2), directory)
            self.assertEqual(_entry(directory)["firstSeenAt"], T0.isoformat())

    def test_arrival_snapshot_is_never_overwritten(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            record_board(
                LIST,
                _payload(_row(skittles=_skittles(95), sparkline=[99.0])),
                T0 + timedelta(minutes=5),
                directory,
            )
            arrival = _entry(directory)["snapshots"][0]
            self.assertEqual(arrival["at"], T0.isoformat())
            self.assertEqual(arrival["changed"], [])
            self.assertEqual(arrival["row"]["skittles"]["2h"]["value"], 82)
            self.assertEqual(arrival["row"]["sparkline"], [60.1, 60.2, 60.3])


class ChangeDetectionTests(unittest.TestCase):
    def test_identical_row_appends_nothing_changed_trigger_appends_one(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            record_board(LIST, _payload(_row()), T0 + timedelta(minutes=2), directory)
            self.assertEqual(len(_entry(directory)["snapshots"]), 1)
            record_board(
                LIST, _payload(_row(skittles=_skittles(95))), T0 + timedelta(minutes=4), directory
            )
            entry = _entry(directory)
            self.assertEqual(len(entry["snapshots"]), 2)
            self.assertEqual(entry["snapshots"][1]["changed"], ["skittles.2h"])
            # hits/lastSeenAt kept advancing across the identical build too.
            self.assertEqual(entry["hits"], 3)

    def test_colour_flip_is_a_change_price_and_sparkline_are_not(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            # Same number, flipped colour: the colours encode state.
            record_board(
                LIST,
                _payload(_row(skittles=_skittles(82, fg="magenta"))),
                T0 + timedelta(minutes=2),
                directory,
            )
            self.assertEqual(len(_entry(directory)["snapshots"]), 2)
            # Price, %Chg and sparkline move every cycle: never a trigger.
            record_board(
                LIST,
                _payload(
                    _row(
                        skittles=_skittles(82, fg="magenta"),
                        last=61.9,
                        pctChange=7.4,
                        sparkline=[61.0, 61.9],
                        quoteTrend=[],
                    )
                ),
                T0 + timedelta(minutes=4),
                directory,
            )
            self.assertEqual(len(_entry(directory)["snapshots"]), 2)

    def test_grade_only_change_is_not_a_snapshot_trigger(self) -> None:
        """Scanner grade (spec 2026-09-21, Task 5) is not a History trigger:
        it is derived from cells the fingerprint already covers (rvol, sqz,
        skittles, highLow, news), so recomputing it every cycle must never by
        itself append a snapshot."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(
                LIST,
                _payload(_row(grade={"letter": "B", "reasons": []})),
                T0,
                directory,
            )
            # >60s later (past SNAPSHOT_MIN_GAP_SECONDS) so a real trigger
            # would be free to land; only "grade" differs.
            record_board(
                LIST,
                _payload(_row(grade={"letter": "A+", "reasons": ["SKIT 8/8"]})),
                T0 + timedelta(seconds=90),
                directory,
            )
            self.assertEqual(len(_entry(directory)["snapshots"]), 1)

    def test_a_field_the_board_adds_later_is_ignored_not_fatal(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            grown = _row(futureField={"value": 1, "bg": "red", "fg": "white"})
            self.assertFalse(
                record_board(LIST, _payload(grown), T0 + timedelta(seconds=30), directory)
            )
            self.assertEqual(len(_entry(directory)["snapshots"]), 1)

    def test_change_inside_the_gap_is_folded_not_lost(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            # 30s later: a real change, inside SNAPSHOT_MIN_GAP_SECONDS.
            record_board(
                LIST, _payload(_row(skittles=_skittles(90))), T0 + timedelta(seconds=30), directory
            )
            self.assertEqual(len(_entry(directory)["snapshots"]), 1)
            # Next eligible build: the snapshot lands and carries the CURRENT
            # row, so the folded change is captured, not lost.
            record_board(
                LIST,
                _payload(_row(skittles=_skittles(97), rvol={"1h": {"value": 3.1, "bg": "green", "fg": "black"}, "D": {"value": 2.6, "bg": "green", "fg": "black"}})),
                T0 + timedelta(seconds=30 + SNAPSHOT_MIN_GAP_SECONDS),
                directory,
            )
            entry = _entry(directory)
            self.assertEqual(len(entry["snapshots"]), 2)
            self.assertEqual(entry["snapshots"][1]["row"]["skittles"]["2h"]["value"], 97)
            self.assertEqual(entry["snapshots"][1]["changed"], ["rvol.1h", "skittles.2h"])

    def test_cap_stops_recording_sets_truncated_and_never_throws(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            moment = T0
            for turn in range(SNAPSHOTS_PER_SYMBOL_PER_DAY + 5):
                record_board(
                    LIST, _payload(_row(skittles=_skittles(turn))), moment, directory
                )
                moment += timedelta(seconds=SNAPSHOT_MIN_GAP_SECONDS + 1)
            entry = _entry(directory)
            self.assertEqual(len(entry["snapshots"]), SNAPSHOTS_PER_SYMBOL_PER_DAY)
            self.assertTrue(entry["truncated"])
            # lastSeenAt still tracks presence after the cap.
            self.assertEqual(
                entry["lastSeenAt"],
                (T0 + timedelta(seconds=(SNAPSHOTS_PER_SYMBOL_PER_DAY + 4) * (SNAPSHOT_MIN_GAP_SECONDS + 1))).isoformat(),
            )

    def test_change_snapshots_strip_sparkline_and_quote_trend(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            record_board(
                LIST, _payload(_row(skittles=_skittles(95))), T0 + timedelta(minutes=2), directory
            )
            snapshots = _entry(directory)["snapshots"]
            self.assertIn("sparkline", snapshots[0]["row"])
            self.assertIn("quoteTrend", snapshots[0]["row"])
            self.assertNotIn("sparkline", snapshots[1]["row"])
            self.assertNotIn("quoteTrend", snapshots[1]["row"])

    def test_unparseable_rvol_is_still_recorded_with_zero_peak(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            broken = _row(rvol={"1h": {"value": "N/A", "bg": "black", "fg": "black"}})
            self.assertTrue(record_board(LIST, _payload(broken), T0, directory))
            self.assertEqual(_entry(directory)["snapshots"][0]["peakRvol"], 0.0)

    def test_only_scan_pass_rows_are_recorded(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(
                LIST, _payload(_row("SNOW"), _row("DG", scanPass=False)), T0, directory
            )
            path = history.board_dir(directory, LIST) / f"{T0.date().isoformat()}.json"
            stored = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(sorted(stored["rows"]), ["SNOW"])
            # A build where NOTHING passed archives an EMPTY day -- "a quiet
            # day is a fact worth seeing" (spec, empty states) -- with no
            # symbols recorded.
            with tempfile.TemporaryDirectory() as other:
                self.assertTrue(
                    record_board(LIST, _payload(_row(scanPass=False)), T0, Path(other))
                )
                quiet_path = (
                    history.board_dir(Path(other), LIST)
                    / f"{T0.date().isoformat()}.json"
                )
                self.assertEqual(
                    json.loads(quiet_path.read_text(encoding="utf-8"))["rows"], {}
                )

    def test_quiet_day_is_archived_once_not_skipped(self) -> None:
        """Spec, empty states: a day with no matches is shown as an archived
        day with zero rows, NOT skipped from the day nav."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            self.assertTrue(record_board(LIST, _payload(), T0, directory))
            # The next quiet cycle has nothing new to say: no rewrite.
            self.assertFalse(
                record_board(LIST, _payload(), T0 + timedelta(seconds=15), directory)
            )
            answer = history_response(LIST, directory=directory)
            self.assertEqual(answer["days"], [T0.date().isoformat()])
            self.assertEqual(answer["date"], T0.date().isoformat())
            self.assertEqual(answer["rows"], [])
            # A later arrival folds into the SAME day file.
            self.assertTrue(
                record_board(LIST, _payload(_row("SNOW")), T0 + timedelta(hours=1), directory)
            )
            self.assertEqual(sorted(_entry(directory)), sorted(
                ["firstSeenAt", "lastSeenAt", "hits", "truncated", "snapshots"]
            ))
            # Garbage payloads still archive NOTHING: only a completed build
            # (a rows LIST) proves the day happened.
            with tempfile.TemporaryDirectory() as other:
                for junk in (None, "x", [], {"rows": "nope"}, {"rows": {}}):
                    self.assertFalse(record_board(LIST, junk, T0, Path(other)))
                self.assertFalse(history.board_dir(Path(other), LIST).exists())

    def test_date_traversal_cannot_escape_the_archive(self) -> None:
        """?date= is a filename ingredient: before validation, a crafted
        ../../x read any {"rows": {...}}-shaped .json on disk (found in the
        2026-08-31 adversarial review). An invalid date answers like an
        absent day -- empty payload, never an error, never a path."""
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw) / "archive"
            record_board(LIST, _payload(_row("SNOW")), T0, directory)
            secret = Path(raw) / "secret.json"
            secret.write_text(
                json.dumps({"rows": {"LEAK": {"snapshots": [
                    {"at": "2026-08-31T09:00:00-04:00", "changed": [],
                     "peakRvol": 9.9, "row": {"symbol": "LEAK", "token": "SUPER-SECRET"}}
                ]}}}),
                encoding="utf-8",
            )
            for evil in ("../../secret", r"..\..\secret", "2026-08-31x", "../secret"):
                answer = history_response(LIST, date=evil, directory=directory)
                self.assertEqual(answer["rows"], [])
                self.assertIsNone(answer["date"])
                self.assertNotIn("SUPER-SECRET", json.dumps(answer))
            # And the honest day still answers.
            good = history_response(LIST, date=T0.date().isoformat(), directory=directory)
            self.assertEqual([r["symbol"] for r in good["rows"]], ["SNOW"])


class HighLowIsStoredButNeverTriggersTests(unittest.TestCase):
    """highLow is the continuous float (close - mid) / (hh - mid): it moves on
    essentially every tick, so it is stored on every snapshot row but is never
    allowed to CAUSE one (trader, 2026-09-01: "any changes of high/low don't
    add in history"). Replaying the real 2026-09-01 Watchlist archive, it was
    implicated in 81 of 164 stored snapshots and was the sole cause of 7.
    Deleting it
    from the ROW instead would silently blank a column the trader reads, so
    the two halves -- not a trigger, still stored -- are pinned separately.
    """

    def test_high_low_is_not_a_trigger_field(self) -> None:
        self.assertNotIn("highLow", CELL_TRIGGER_FIELDS)
        self.assertNotIn("highLow", TIMEFRAME_TRIGGER_FIELDS)
        self.assertIn("highLow", NON_TRIGGER_STORED_FIELDS)
        # ... and NOT by having been stripped from the row instead.
        self.assertNotIn("highLow", STRIPPED_FIELDS)

    def test_high_low_moving_alone_appends_no_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            moment = T0
            # Six builds, each with a different highLow value AND a different
            # colour band -- the whole cell state moves, well past the gap.
            for turn, (value, bg) in enumerate(
                [(0.91, "green"), (0.42, "black"), (-0.30, "red"),
                 (0.05, "black"), (0.99, "green"), (0.61, "green")]
            ):
                moment += timedelta(seconds=SNAPSHOT_MIN_GAP_SECONDS + 5)
                record_board(
                    LIST, _payload(_row(highLow=_high_low(value, bg))), moment, directory
                )
            entry = _entry(directory)
            self.assertEqual(len(entry["snapshots"]), 1)  # arrival only

    def test_high_low_is_still_stored_on_every_snapshot_row(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            record_board(
                LIST,
                _payload(_row(highLow=_high_low(-0.44, "red"), skittles=_skittles(95))),
                T0 + timedelta(minutes=2),
                directory,
            )
            snapshots = _entry(directory)["snapshots"]
            self.assertEqual(len(snapshots), 2)
            # Arrival keeps its highLow; the change snapshot carries the
            # CURRENT one -- the column is populated on every history row.
            self.assertEqual(snapshots[0]["row"]["highLow"], _high_low(0.96))
            self.assertEqual(snapshots[1]["row"]["highLow"], _high_low(-0.44, "red"))

    def test_high_low_never_appears_in_changed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            record_board(
                LIST,
                _payload(_row(highLow=_high_low(0.11, "black"), skittles=_skittles(95))),
                T0 + timedelta(minutes=2),
                directory,
            )
            answer = history_response(LIST, directory=directory)
            for entry in answer["rows"]:
                self.assertNotIn("highLow", entry["changed"])
            self.assertEqual(answer["rows"][1]["changed"], ["skittles.2h"])


def _graded(**overrides: object) -> dict:
    """A row carrying the scanner-grade fields the recorder adds."""
    return _row(
        grade={"letter": "A+", "reasons": ["SKIT 7/8"], "checks": {"skit": 7}},
        m5={"state": "building", "pattern": "steady", "chart": "holding", "trigger": 60.5,
            "lastCompleted": {"time": 1790000000, "open": 60, "high": 61, "low": 59.8,
                              "close": 60.4, "volume": 1234}},
        sqzRaw={"4h": {"ms": False, "msf": 1, "lastReleaseAt": 1789990000}},
        adx={"5m": {"plus": 48.6, "minus": 13.0, "adx": 35.0, "prevPlus": 12.0,
                    "prevMinus": 20.0, "prevAdx": 12.0, "cross": "bull",
                    "rising": True, "strongPlus": True, "strongMinus": False,
                    "barAt": 1790000000},
             "30m": {"plus": 21.0, "minus": 18.0, "adx": 15.0, "prevPlus": 20.0,
                     "prevMinus": 19.0, "prevAdx": 15.5, "cross": None,
                     "rising": False, "strongPlus": False, "strongMinus": False,
                     "barAt": 1789998000}},
        gradeFresh={"icons": ["SKIT"], "ageMinutes": 3,
                    "firstToday": {"A+": {"at": T0.isoformat(), "price": 60.4}},
                    "timeline": [{"at": T0.isoformat(), "what": "SKIT 4h bg green"}]},
        **overrides,
    )


class GradeFieldsStayOutOfHistoryTests(unittest.TestCase):
    """Final review #2: the grade recorder's bulk (sqzRaw, gradeFresh.timeline,
    m5.lastCompleted) roughly doubled every History snapshot row."""

    def _assert_slim(self, stored: dict) -> None:
        self.assertNotIn("sqzRaw", stored)
        # ADX: ~336 B of every row, for a reading whose durable copy is
        # written on the recorded grade / pattern event instead. Only the four
        # numbers the Setup tags read stay (GO / OPT / the ADX tag).
        self.assertEqual(stored["adx"]["5m"], {"plus": 48.6, "minus": 13.0, "adx": 35.0, "prevAdx": 12.0, "rising": True})
        self.assertNotIn("cross", stored["adx"]["30m"])
        self.assertNotIn("timeline", stored["gradeFresh"])
        # The SKIT / SQZ items the Setup tags read, under their own key.
        self.assertEqual(stored["gradeFresh"]["setupTimeline"], [{"at": T0.isoformat(), "what": "SKIT 4h bg green"}])
        self.assertNotIn("lastCompleted", stored["m5"])
        self.assertEqual(stored["grade"]["letter"], "A+")
        self.assertEqual(stored["grade"]["checks"], {"skit": 7})
        self.assertEqual(stored["m5"]["state"], "building")
        self.assertEqual(stored["m5"]["pattern"], "steady")
        self.assertEqual(stored["gradeFresh"]["icons"], ["SKIT"])
        self.assertEqual(stored["gradeFresh"]["ageMinutes"], 3)
        self.assertEqual(stored["gradeFresh"]["firstToday"]["A+"]["price"], 60.4)

    def test_arrival_and_change_snapshots_drop_the_grade_bulk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            live = _graded()
            record_board(LIST, _payload(live), T0, directory)
            record_board(
                LIST, _payload(_graded(skittles=_skittles(95))), T0 + timedelta(minutes=2),
                directory,
            )
            arrival, change = _entry(directory)["snapshots"]
            self._assert_slim(arrival["row"])
            self._assert_slim(change["row"])
            # The arrival keeps the sparkline as before; the change row does not.
            self.assertIn("sparkline", arrival["row"])
            self.assertNotIn("sparkline", change["row"])
            # The live payload row is not mutated by the copy: the board still
            # ships ADX, only the stored History copy goes without it.
            self.assertIn("sqzRaw", live)
            self.assertIn("adx", live)
            self.assertEqual(live["adx"]["5m"]["cross"], "bull")
            self.assertIn("timeline", live["gradeFresh"])
            self.assertIn("lastCompleted", live["m5"])


class SetupInputsTests(unittest.TestCase):
    """2026-10-02 "live setup column has so much information but history it's
    not there": a History row must carry what the Setup tags are worked out
    from, and a setup appearing must get its own snapshot."""

    def test_only_setup_timeline_items_and_the_zs_pillar_are_kept(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            row = _graded()
            row["gradeFresh"]["timeline"] = [
                {"at": T0.isoformat(), "what": "SQZ 2h released", "extra": 1},
                {"at": T0.isoformat(), "what": "RVOL 5m bg cyan"},
                {"at": T0.isoformat(), "what": "SKIT 2D bg cyan"},
            ]
            row["m5"]["pillars"] = {"zs": {"above": True, "clear2": True, "barAt": 1790000000}, "vwap": {"x": 1}}
            record_board(LIST, _payload(row), T0, directory)
            stored = _entry(directory)["snapshots"][0]["row"]
            self.assertEqual([e["what"] for e in stored["gradeFresh"]["setupTimeline"]],
                             ["SQZ 2h released", "SKIT 2D bg cyan"])
            self.assertEqual(stored["gradeFresh"]["setupTimeline"][0], {"at": T0.isoformat(), "what": "SQZ 2h released"})
            self.assertEqual(stored["m5"]["pillars"], {"zs": {"above": True, "clear2": True, "barAt": 1790000000}})
            self.assertEqual(len(row["gradeFresh"]["timeline"]), 3, "live row untouched")
            self.assertIn("vwap", row["m5"]["pillars"])

    def test_a_chart_arrow_appearing_gets_its_own_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            arrow = {"label": "CALL2H", "family": "4x8", "timeframe": "2H", "at": "2026-08-31T08:00:00-04:00"}
            record_board(LIST, _payload(_row(chartSignals=[arrow])), T0 + timedelta(minutes=2), directory)
            snapshots = _entry(directory)["snapshots"]
            self.assertEqual(len(snapshots), 2)
            self.assertEqual(snapshots[1]["changed"], ["setup.chartSignals"])
            # The same arrow on the next cycle is not news.
            record_board(LIST, _payload(_row(chartSignals=[arrow])), T0 + timedelta(minutes=4), directory)
            self.assertEqual(len(_entry(directory)["snapshots"]), 2)

    def test_day_line_go_and_momox_aplus_are_triggers(self) -> None:
        cases = [
            ({"dayLines": {"line": "2026-08-31T09:40:00-04:00"}}, "setup.dayLines"),
            ({"m5": {"state": "holding", "gapGo": {"gap": 2.2, "goAt": 1790000000}}}, "setup.goAt"),
            ({"momoxAPlus": {"at": "2026-08-31T09:50:00-04:00"}}, "setup.momoxAPlus"),
        ]
        for extra, name in cases:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as raw:
                directory = Path(raw)
                record_board(LIST, _payload(_row(m5={"state": "holding"})), T0, directory)
                record_board(LIST, _payload(_row(**{"m5": {"state": "holding"}, **extra})), T0 + timedelta(minutes=2), directory)
                self.assertEqual(_entry(directory)["snapshots"][-1]["changed"], [name])

    def test_empty_setup_fields_add_no_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            quiet = _row(chartSignals=[], dayLines={}, momoxAPlus=None, m5={"gapGo": {"goAt": None}})
            record_board(LIST, _payload(quiet), T0 + timedelta(minutes=2), directory)
            gone = _row(chartSignals=[{"label": "CALL2H", "at": "x", "goneAt": "y"}])
            record_board(LIST, _payload(gone), T0 + timedelta(minutes=4), directory)
            self.assertEqual(len(_entry(directory)["snapshots"]), 1)


class DurabilityTests(unittest.TestCase):
    def test_thirty_day_boundary_day_30_survives_day_31_is_pruned(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row("OLD31")), T0 - timedelta(days=RETENTION_DAYS + 1), directory)
            record_board(LIST, _payload(_row("OLD30")), T0 - timedelta(days=RETENTION_DAYS), directory)
            record_board(LIST, _payload(_row("NEW")), T0, directory)
            names = {p.stem for p in history.board_dir(directory, LIST).glob("*.json")}
            self.assertNotIn((T0.date() - timedelta(days=RETENTION_DAYS + 1)).isoformat(), names)
            self.assertIn((T0.date() - timedelta(days=RETENTION_DAYS)).isoformat(), names)
            self.assertIn(T0.date().isoformat(), names)

    def test_garbage_day_file_is_treated_as_absent_never_raises(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            board_directory = history.board_dir(directory, LIST)
            board_directory.mkdir(parents=True)
            day = T0.date().isoformat()
            (board_directory / f"{day}.json").write_text("{torn garbag", encoding="utf-8")
            self.assertEqual(history_response(LIST, date=day, directory=directory)["rows"], [])
            self.assertTrue(record_board(LIST, _payload(_row()), T0, directory))
            self.assertEqual(_entry(directory)["firstSeenAt"], T0.isoformat())

    def test_write_failure_returns_false_and_does_not_propagate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            # A FILE where the board directory must go: mkdir fails, the scan
            # must survive with "no history written this cycle".
            history.board_dir(directory, LIST).parent.mkdir(parents=True, exist_ok=True)
            history.board_dir(directory, LIST).write_text("not a directory", encoding="utf-8")
            self.assertFalse(record_board(LIST, _payload(_row()), T0, directory))

    def test_the_service_hook_survives_a_raising_recorder(self) -> None:
        """momx.service._record_history must degrade, never propagate: a
        history bug can never cost a scan."""
        from momx import service

        original = history.record_board
        calls: list[str] = []

        def _explode(*args: object, **kwargs: object) -> bool:
            calls.append("called")
            raise RuntimeError("history is broken today")

        history.record_board = _explode  # type: ignore[assignment]
        try:
            service._record_history(LIST, _payload(_row()))
        finally:
            history.record_board = original  # type: ignore[assignment]
        self.assertEqual(calls, ["called"])

    def test_tmp_orphans_older_than_a_day_are_swept(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            board_directory = history.board_dir(directory, LIST)
            board_directory.mkdir(parents=True)
            stale = board_directory / ".2026-08-01.json.999.tmp"
            fresh = board_directory / ".2026-08-31.json.999.tmp"
            stale.write_text("{}", encoding="utf-8")
            fresh.write_text("{}", encoding="utf-8")
            two_days_ago = os.path.getmtime(stale) - 2 * 86400
            os.utime(stale, (two_days_ago, two_days_ago))
            record_board(LIST, _payload(_row()), T0, directory)
            self.assertFalse(stale.exists())
            self.assertTrue(fresh.exists())


class QueryTests(unittest.TestCase):
    def test_day_form_is_one_row_per_snapshot_ascending(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row("SNOW")), T0, directory)
            record_board(
                LIST,
                _payload(_row("SNOW", skittles=_skittles(95))),
                T0 + timedelta(minutes=10),
                directory,
            )
            record_board(
                LIST,
                _payload(_row("SNOW", skittles=_skittles(95)), _row("DG")),
                T0 + timedelta(minutes=20),
                directory,
            )
            answer = history_response(LIST, directory=directory)
            self.assertEqual(answer["date"], T0.date().isoformat())
            self.assertEqual(answer["days"], [T0.date().isoformat()])
            self.assertEqual(answer["retentionDays"], RETENTION_DAYS)
            self.assertEqual(
                [(r["symbol"], r["isArrival"]) for r in answer["rows"]],
                [("SNOW", True), ("SNOW", False), ("DG", True)],
            )
            self.assertEqual(answer["rows"][1]["changed"], ["skittles.2h"])

    def test_absent_day_is_an_empty_payload_never_an_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            record_board(LIST, _payload(_row()), T0, directory)
            answer = history_response(LIST, date="2020-01-01", directory=directory)
            self.assertEqual(answer["rows"], [])
            self.assertEqual(answer["date"], "2020-01-01")
            empty = history_response("Mag7", directory=directory)
            self.assertEqual(empty["rows"], [])
            self.assertIsNone(empty["date"])

    def test_symbol_search_returns_days_newest_first(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            earlier = T0 - timedelta(days=3)
            record_board(LIST, _payload(_row("SNOW")), earlier, directory)
            record_board(LIST, _payload(_row("SNOW"), _row("DG")), T0, directory)
            answer = history_response(LIST, symbol="snow", directory=directory)
            self.assertIsNone(answer["date"])
            self.assertEqual(
                [entry["date"] for entry in answer["rows"]],
                [T0.date().isoformat(), earlier.date().isoformat()],
            )
            self.assertEqual({entry["symbol"] for entry in answer["rows"]}, {"SNOW"})
            self.assertEqual(
                answer["rows"][0]["firstSeenAt"], T0.isoformat()
            )

    def test_symbol_search_with_a_date_stays_on_that_day(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            directory = Path(raw)
            earlier = T0 - timedelta(days=3)
            record_board(LIST, _payload(_row("SNOW")), earlier, directory)
            record_board(LIST, _payload(_row("SNOW"), _row("DG")), T0, directory)
            answer = history_response(
                LIST, symbol="snow", date=earlier.date().isoformat(), directory=directory
            )
            self.assertEqual(answer["date"], earlier.date().isoformat())
            self.assertEqual(
                [entry["date"] for entry in answer["rows"]], [earlier.date().isoformat()]
            )
            # A day the ticker never matched: empty, and still that day.
            quiet = history_response(
                LIST, symbol="dg", date=earlier.date().isoformat(), directory=directory
            )
            self.assertEqual(quiet["rows"], [])
            self.assertEqual(quiet["date"], earlier.date().isoformat())
            # A non-date never touches the filesystem.
            bad = history_response(LIST, symbol="snow", date="../x", directory=directory)
            self.assertEqual(bad["rows"], [])
            self.assertIsNone(bad["date"])


if __name__ == "__main__":
    unittest.main()
