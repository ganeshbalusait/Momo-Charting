"""Tests for momx.fastlane -- the momentum event engine.

Every payload here is hand-built to the momx.board contract (rows with
``symbol`` / ``scanPass`` / ``scanReasons`` / ``pctChange`` / ``rvol`` cell
maps). No network, no feed, no board build.

The two behaviours that MUST stay pinned, because losing either one trains
the trader to ignore the event strip:

* a cold start (previous None or warming) emits NOTHING -- 19 matches on the
  first build of the day are not 19 momentum events;
* RVOL spikes with a baseline are an EDGE, not a level -- a row sitting at
  3.1 across two payloads fires once, on the crossing build, not on every
  refresh.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from momx import fastlane


# ----------------------------------------------------------------------
# payload builders
# ----------------------------------------------------------------------

NOW = datetime(2026, 8, 28, 14, 31, 2, tzinfo=timezone.utc)


def cell(value):
    return {"value": value, "bg": "black", "fg": "white"}


def row(symbol, *, scan_pass=False, reasons=(), pct=1.0, rvol=None):
    return {
        "symbol": symbol,
        "industry": "Semis",
        "last": 100.0,
        "pctChange": pct,
        "scanPass": scan_pass,
        "scanReasons": list(reasons),
        "rvol": rvol if rvol is not None else {"5m": cell(0.5), "15m": cell(0.5)},
        "sqz": {},
        "skittles": {},
    }


def board(*rows):
    return {
        "generatedAt": "2026-08-28T14:31:00+00:00",
        "universe": [r["symbol"] for r in rows],
        "universeCount": len(rows),
        "rows": list(rows),
        "errors": {},
    }


def warming_board():
    return {
        "generatedAt": None,
        "universe": ["NVDA", "TSLA"],
        "universeCount": 2,
        "rows": [],
        "errors": {},
        "warming": True,
        "message": "Building the first board from live bars.",
    }


# ----------------------------------------------------------------------
# diff_matches: cold start
# ----------------------------------------------------------------------

def test_cold_start_none_previous_emits_nothing():
    current = board(
        *[row(s, scan_pass=True, reasons=["macd:4h"]) for s in
          ("NVDA", "TSLA", "AMZN", "META", "MSFT")]
    )
    assert fastlane.diff_matches(None, current, now=NOW) == []


def test_cold_start_warming_previous_emits_nothing():
    # The first build of the day replacing the warming placeholder is a cold
    # start, not a burst of momentum events -- even with 19 matches at once.
    current = board(*[row(f"S{i}", scan_pass=True) for i in range(19)])
    assert fastlane.diff_matches(warming_board(), current, now=NOW) == []


def test_warming_current_does_not_fire_lost_matches():
    previous = board(row("NVDA", scan_pass=True), row("TSLA", scan_pass=True))
    assert fastlane.diff_matches(previous, warming_board(), now=NOW) == []


# ----------------------------------------------------------------------
# diff_matches: real transitions
# ----------------------------------------------------------------------

def test_new_match_fires_once_with_payload_fields():
    previous = board(row("NVDA", scan_pass=True), row("TSLA"))
    current = board(
        row("NVDA", scan_pass=True),
        row("TSLA", scan_pass=True, reasons=["macd:4h", "rvol:5m"], pct=3.2),
    )
    events = fastlane.diff_matches(previous, current, now=NOW)
    assert events == [
        {
            "type": "new_match",
            "symbol": "TSLA",
            "pctChange": 3.2,
            "scanReasons": ["macd:4h", "rvol:5m"],
            "at": NOW.isoformat(),
        }
    ]
    # ...and while it PERSISTS, it never fires again.
    assert fastlane.diff_matches(current, current, now=NOW) == []


def test_new_symbol_on_board_counts_as_new_match():
    previous = board(row("NVDA", scan_pass=True))
    current = board(row("NVDA", scan_pass=True), row("AVGO", scan_pass=True))
    events = fastlane.diff_matches(previous, current, now=NOW)
    assert [(e["type"], e["symbol"]) for e in events] == [("new_match", "AVGO")]


def test_lost_match_fires():
    previous = board(row("NVDA", scan_pass=True), row("USO", scan_pass=True))
    current = board(row("NVDA", scan_pass=True), row("USO"))
    events = fastlane.diff_matches(previous, current, now=NOW)
    assert events == [{"type": "lost_match", "symbol": "USO", "at": NOW.isoformat()}]


def test_symbol_leaving_board_but_still_in_list_is_a_lost_match():
    # Pushed off the board by the row cap, still in the list: a real loss.
    previous = board(row("NVDA", scan_pass=True), row("USO", scan_pass=True))
    current = board(row("NVDA", scan_pass=True))
    current["universe"] = ["NVDA", "USO"]
    events = fastlane.diff_matches(previous, current, now=NOW)
    assert [(e["type"], e["symbol"]) for e in events] == [("lost_match", "USO")]


def test_symbol_removed_from_the_list_is_not_a_lost_match():
    # 2026-09-01: pasting a one-ticker watchlist over a ten-ticker one showed
    # ten LOST chips. Editing the list is not a momentum event.
    previous = board(row("NVDA", scan_pass=True), row("USO", scan_pass=True))
    current = board(row("NVDA", scan_pass=True))          # universe == ["NVDA"]
    assert fastlane.diff_matches(previous, current, now=NOW) == []


def test_unknown_universe_keeps_the_old_lost_match_rule():
    previous = board(row("NVDA", scan_pass=True), row("USO", scan_pass=True))
    current = board(row("NVDA", scan_pass=True))
    del current["universe"]
    events = fastlane.diff_matches(previous, current, now=NOW)
    assert [(e["type"], e["symbol"]) for e in events] == [("lost_match", "USO")]


# ----------------------------------------------------------------------
# prune_events: chips for tickers no longer in the list go away
# ----------------------------------------------------------------------

def test_prune_drops_events_for_symbols_outside_the_universe():
    events = [
        {"type": "new_match", "symbol": "NVDA", "at": NOW.isoformat()},
        {"type": "lost_match", "symbol": "ULTA", "at": NOW.isoformat()},
        {"type": "rvol_spike", "symbol": "ebay", "timeframe": "5m", "at": NOW.isoformat()},
    ]
    kept = fastlane.prune_events(events, board(row("NVDA")))
    assert [e["symbol"] for e in kept] == ["NVDA"]


def test_prune_against_a_warming_placeholder_uses_its_universe():
    # Right after a paste the snapshot is warming but carries the NEW list,
    # so stale chips clear before the first build finishes.
    events = [
        {"type": "lost_match", "symbol": "ULTA", "at": NOW.isoformat()},
        {"type": "new_match", "symbol": "TSLA", "at": NOW.isoformat()},
    ]
    kept = fastlane.prune_events(events, warming_board())    # universe NVDA, TSLA
    assert [e["symbol"] for e in kept] == ["TSLA"]


def test_prune_keeps_everything_when_the_universe_is_unknown():
    events = [{"type": "lost_match", "symbol": "ULTA", "at": NOW.isoformat()}]
    assert fastlane.prune_events(events, {"rows": []}) == events
    assert fastlane.prune_events(events, None) == events


def test_prune_is_total_and_never_aliases():
    events = [{"type": "new_match", "symbol": "NVDA"}, "garbage", None]
    kept = fastlane.prune_events(events, board(row("NVDA")))
    assert kept == [{"type": "new_match", "symbol": "NVDA"}]
    assert kept[0] is not events[0]
    assert fastlane.prune_events("nope", board()) == []


def test_rank_and_price_churn_is_silent():
    previous = board(
        row("NVDA", scan_pass=True, reasons=["macd:4h"], pct=2.0),
        row("TSLA", scan_pass=True, reasons=["sqzfired:D"], pct=1.0),
    )
    # Same matched set: ranks swapped, pctChange moved, reasons shifted.
    current = board(
        row("TSLA", scan_pass=True, reasons=["sqzfired:D", "rvol:30m"], pct=4.5),
        row("NVDA", scan_pass=True, reasons=["macd:2D"], pct=1.7),
    )
    assert fastlane.diff_matches(previous, current, now=NOW) == []


def test_non_matching_rows_never_fire():
    previous = board(row("NVDA"))
    current = board(row("NVDA"), row("TSLA"))  # both scanPass False
    assert fastlane.diff_matches(previous, current, now=NOW) == []


def test_diff_matches_malformed_payloads_return_empty():
    good = board(row("NVDA", scan_pass=True))
    assert fastlane.diff_matches("junk", good) == []
    assert fastlane.diff_matches(good, 42) == []
    assert fastlane.diff_matches({}, good) == []           # no rows key
    assert fastlane.diff_matches(good, {"rows": "nope"}) == []
    assert fastlane.diff_matches(None, None) == []
    # rows containing garbage entries are skipped, not fatal
    dirty = {"rows": [None, "x", {"scanPass": True}, row("AMD", scan_pass=True)]}
    events = fastlane.diff_matches(board(), dirty, now=NOW)
    assert [(e["type"], e["symbol"]) for e in events] == [("new_match", "AMD")]


# ----------------------------------------------------------------------
# rvol_spikes: level mode
# ----------------------------------------------------------------------

def test_rvol_level_mode_reports_cells_at_or_above_threshold():
    payload = board(
        row("NVDA", pct=2.4, rvol={"5m": cell(3.1), "15m": cell(1.9)}),
        row("TSLA", rvol={"5m": cell(2.0), "15m": cell(0.4)}),  # >= is inclusive
        row("USO", rvol={"5m": cell(1.2), "15m": cell(0.1)}),
    )
    events = fastlane.rvol_spikes(payload, now=NOW)
    assert [(e["symbol"], e["timeframe"], e["value"]) for e in events] == [
        ("NVDA", "5m", 3.1),
        ("TSLA", "5m", 2.0),
    ]
    assert events[0] == {
        "type": "rvol_spike",
        "symbol": "NVDA",
        "timeframe": "5m",
        "value": 3.1,
        # No "pace" on these cells, so the number IS the board cell.
        "paced": False,
        "pctChange": 2.4,
        "at": NOW.isoformat(),
    }


def test_rvol_custom_threshold_and_timeframes():
    # 4h stands in for "a timeframe outside the defaults" (it used to be 30m,
    # until 30m joined the defaults on 2026-09-01 -- see the DEFAULT_RVOL_
    # TIMEFRAMES note; 4h is deliberately too slow for an options entry and
    # so stays board-only).
    payload = board(row("NVDA", rvol={"5m": cell(1.0), "4h": cell(2.6)}))
    assert fastlane.rvol_spikes(payload, now=NOW) == []  # 4h not in defaults
    events = fastlane.rvol_spikes(payload, threshold=2.5, timeframes=("4h",), now=NOW)
    assert [(e["timeframe"], e["value"]) for e in events] == [("4h", 2.6)]


def test_rvol_null_dash_and_missing_cells_skipped_silently():
    payload = board(
        row("NVDA", rvol={"5m": cell(None), "15m": cell("-")}),
        row("TSLA", rvol={"5m": None}),           # missing 15m key entirely
        row("USO", rvol="not-a-mapping"),
        row("AMD"),                                # quiet defaults
    )
    assert fastlane.rvol_spikes(payload, now=NOW) == []


# ----------------------------------------------------------------------
# rvol_spikes: edge mode (the pinned behaviour)
# ----------------------------------------------------------------------

def test_rvol_edge_fires_once_not_every_refresh():
    below = board(row("NVDA", rvol={"5m": cell(1.4), "15m": cell(0.2)}))
    spiking = board(row("NVDA", pct=2.4, rvol={"5m": cell(3.1), "15m": cell(0.2)}))
    still_spiking = board(row("NVDA", pct=2.6, rvol={"5m": cell(3.1), "15m": cell(0.3)}))

    first = fastlane.rvol_spikes(spiking, previous=below, now=NOW)
    assert [(e["symbol"], e["timeframe"], e["value"]) for e in first] == [
        ("NVDA", "5m", 3.1)
    ]
    # A row sitting at 3.1 across two payloads is a LEVEL, not a new crossing.
    assert fastlane.rvol_spikes(still_spiking, previous=spiking, now=NOW) == []


def test_rvol_edge_refires_after_dipping_below():
    spiking = board(row("NVDA", rvol={"5m": cell(2.8), "15m": cell(0.2)}))
    cooled = board(row("NVDA", rvol={"5m": cell(1.1), "15m": cell(0.2)}))
    assert fastlane.rvol_spikes(cooled, previous=spiking, now=NOW) == []
    again = fastlane.rvol_spikes(spiking, previous=cooled, now=NOW)
    assert [(e["symbol"], e["timeframe"]) for e in again] == [("NVDA", "5m")]


def test_rvol_edge_symbol_absent_from_previous_counts_as_crossing():
    previous = board(row("NVDA", rvol={"5m": cell(0.5), "15m": cell(0.5)}))
    current = board(
        row("NVDA", rvol={"5m": cell(0.5), "15m": cell(0.5)}),
        row("TSLA", rvol={"5m": cell(2.7), "15m": cell(0.5)}),
    )
    events = fastlane.rvol_spikes(current, previous=previous, now=NOW)
    assert [(e["symbol"], e["timeframe"]) for e in events] == [("TSLA", "5m")]


def test_rvol_edge_against_warming_previous_is_silent():
    # Same cold-start rule as diff_matches: no baseline, no burst of events.
    current = board(row("NVDA", rvol={"5m": cell(3.0), "15m": cell(2.5)}))
    assert fastlane.rvol_spikes(current, previous=warming_board(), now=NOW) == []


def test_rvol_malformed_inputs_return_empty():
    good = board(row("NVDA", rvol={"5m": cell(3.0), "15m": cell(0.1)}))
    assert fastlane.rvol_spikes(None) == []
    assert fastlane.rvol_spikes("junk") == []
    assert fastlane.rvol_spikes({"rows": 7}) == []
    assert fastlane.rvol_spikes(good, threshold="not-a-number") == []
    assert fastlane.rvol_spikes(good, timeframes=None) == []
    assert fastlane.rvol_spikes(good, previous="junk") == []


# ----------------------------------------------------------------------
# rvol_spikes: the timeframe set
# ----------------------------------------------------------------------

def test_default_timeframes_are_fast_first_and_include_30m():
    # 2026-09-01: 5m/15m read 0.0-0.6 for every name on the board at 10:40
    # while 1h read 1.1-3.5, so the old ("5m", "15m") pair could not fire on
    # ANY of them -- and did not, all day, while CRML printed 23.4x. Order is
    # load-bearing: events follow the timeframe order, fastest first.
    assert fastlane.DEFAULT_RVOL_TIMEFRAMES == ("5m", "15m", "30m")


def test_30m_spike_fires_on_defaults():
    # The shape that stayed silent all of 2026-09-01: fast frames flat, the
    # move visible only on the slower intraday bucket.
    payload = board(
        row("CRML", pct=6.1, rvol={"5m": cell(0.4), "15m": cell(0.6), "30m": cell(3.4)})
    )
    events = fastlane.rvol_spikes(payload, now=NOW)
    assert [(e["symbol"], e["timeframe"], e["value"]) for e in events] == [
        ("CRML", "30m", 3.4)
    ]


def test_defaults_still_exclude_the_slow_frames():
    # 1h/2h/4h/D remain board-only: by the time they cross, the options entry
    # is gone. Adding 30m must not have widened the set past that line.
    payload = board(row("NVDA", rvol={"1h": cell(9.0), "2h": cell(9.0),
                                      "4h": cell(9.0), "D": cell(9.0)}))
    assert fastlane.rvol_spikes(payload, now=NOW) == []


# ----------------------------------------------------------------------
# rvol_spikes: the pace field (the still-forming bucket)
# ----------------------------------------------------------------------

def paced_cell(value, pace):
    """A cell as momx.columns.rvol_cell stamps it: value + its pace companion."""
    held = cell(value)
    held["pace"] = pace
    return held


def test_pace_is_preferred_over_value_when_present():
    # 09:35: the 30m bucket holds 5 of 30 minutes, so its as-traded ratio is
    # 0.4 and can never reach 2.0 before the move is over -- but it is
    # RUNNING at 4.2x. That is the alert.
    payload = board(
        row("CRML", pct=6.1, rvol={"30m": paced_cell(0.4, 4.2)})
    )
    events = fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW)
    assert [(e["timeframe"], e["value"], e["paced"]) for e in events] == [
        ("30m", 4.2, True)
    ]


def test_pace_below_threshold_does_not_fire_even_when_value_is_high():
    # Pace REPLACES the read; it is not a second chance to cross. Today's
    # producer clamps the elapsed fraction at 1.0 so it cannot emit a pace
    # BELOW its value -- this pins the semantic anyway, because "whichever
    # of the two is bigger" is a different and much noisier rule, and the
    # difference must be a decision rather than an accident.
    payload = board(row("NVDA", rvol={"30m": paced_cell(2.6, 1.1)}))
    assert fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW) == []


def test_pace_absent_null_or_non_numeric_falls_back_to_value():
    # Every one of these is the producer saying "I cannot pace this bucket
    # honestly", and the contracted answer is today's behaviour: the value.
    payload = board(
        row("AAA", rvol={"30m": cell(3.0)}),                        # no pace key
        row("BBB", rvol={"30m": paced_cell(3.1, None)}),            # explicit None
        row("CCC", rvol={"30m": paced_cell(3.2, "-")}),             # dash
        row("DDD", rvol={"30m": paced_cell(3.3, float("nan"))}),    # NaN
        row("EEE", rvol={"30m": paced_cell(3.4, True)}),            # bool is not a number
        row("FFF", rvol={"30m": 3.5}),                              # bare number cell
    )
    events = fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW)
    assert [(e["symbol"], e["value"], e["paced"]) for e in events] == [
        ("AAA", 3.0, False),
        ("BBB", 3.1, False),
        ("CCC", 3.2, False),
        ("DDD", 3.3, False),
        ("EEE", 3.4, False),
        ("FFF", 3.5, False),
    ]


def test_unpaceable_low_value_still_stays_silent():
    payload = board(row("NVDA", rvol={"30m": paced_cell(0.4, None)}))
    assert fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW) == []


def test_pace_edge_fires_once_not_every_refresh():
    forming = board(row("CRML", rvol={"30m": paced_cell(0.1, 1.2)}))
    spiking = board(row("CRML", rvol={"30m": paced_cell(0.4, 4.2)}))
    faster = board(row("CRML", rvol={"30m": paced_cell(0.9, 4.9)}))

    first = fastlane.rvol_spikes(spiking, previous=forming, timeframes=("30m",), now=NOW)
    assert [(e["symbol"], e["value"], e["paced"]) for e in first] == [("CRML", 4.2, True)]
    # Still pacing above the line -- a level, not a new crossing.
    assert fastlane.rvol_spikes(faster, previous=spiking, timeframes=("30m",), now=NOW) == []


def test_pace_edge_baseline_reads_pace_not_value():
    # The trap: read pace now and value last build, and a bucket that simply
    # re-paces upward looks like a fresh crossing on EVERY refresh -- the
    # phone-spam shape the edge rule exists to prevent. Both baseline values
    # here are far below the threshold; only the paces are above it.
    previous = board(row("CRML", rvol={"30m": paced_cell(0.4, 4.2)}))
    current = board(row("CRML", rvol={"30m": paced_cell(0.5, 4.5)}))
    assert fastlane.rvol_spikes(current, previous=previous, timeframes=("30m",), now=NOW) == []


def test_pace_edge_crossing_from_a_paced_baseline_below_threshold():
    previous = board(row("CRML", rvol={"30m": paced_cell(0.4, 1.5)}))
    current = board(row("CRML", rvol={"30m": paced_cell(0.5, 2.9)}))
    events = fastlane.rvol_spikes(current, previous=previous, timeframes=("30m",), now=NOW)
    assert [(e["symbol"], e["value"], e["paced"]) for e in events] == [("CRML", 2.9, True)]


# ----------------------------------------------------------------------
# rvol_spikes: the pace sanity ceiling
# ----------------------------------------------------------------------

def test_absurd_pace_is_discarded_not_forwarded():
    # A producer whose barely-started-bucket guard broke: three seconds into
    # a 30m bucket, one odd lot pacing to 400x. We do not put that on the
    # trader's phone -- we fall back to the value that actually traded.
    payload = board(row("NVDA", rvol={"30m": paced_cell(0.9, 400.0)}))
    assert fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW) == []


def test_absurd_pace_falls_back_to_value_and_still_fires_when_value_qualifies():
    # The ceiling must never SILENCE a real mover: it only refuses the
    # inflated magnitude, and the conservative number still crosses.
    payload = board(row("CRML", rvol={"30m": paced_cell(8.0, 400.0)}))
    events = fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW)
    assert [(e["value"], e["paced"]) for e in events] == [(8.0, False)]


def test_real_extremes_pass_the_ceiling_untouched():
    # CRML's 23.4x open is the loudest print the board has shown; the ceiling
    # exists to catch a broken guard, not to cap the market.
    payload = board(row("CRML", rvol={"30m": paced_cell(3.5, 23.4)}))
    events = fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW)
    assert [(e["value"], e["paced"]) for e in events] == [(23.4, True)]


def test_pace_ceiling_is_configurable_and_total():
    payload = board(row("NVDA", rvol={"30m": paced_cell(0.9, 400.0)}))
    kw = {"timeframes": ("30m",), "now": NOW}
    # Explicit None disables the guard (a caller that WANTS the raw pace).
    assert [e["value"] for e in fastlane.rvol_spikes(payload, pace_ceiling=None, **kw)] == [400.0]
    # A tighter ceiling bites sooner.
    assert fastlane.rvol_spikes(payload, pace_ceiling=10.0, **kw) == []
    # Garbage falls back to the DEFAULT ceiling, never to "no ceiling": a
    # typo'd caller must not silently disarm the phone guard.
    assert fastlane.rvol_spikes(payload, pace_ceiling="wide-open", **kw) == []
    assert fastlane.rvol_spikes(payload, pace_ceiling=float("nan"), **kw) == []


def test_pace_ceiling_boundary_is_inclusive():
    at_ceiling = board(row("NVDA", rvol={"30m": paced_cell(1.0, fastlane.DEFAULT_PACE_CEILING)}))
    events = fastlane.rvol_spikes(at_ceiling, timeframes=("30m",), now=NOW)
    assert [(e["value"], e["paced"]) for e in events] == [
        (fastlane.DEFAULT_PACE_CEILING, True)
    ]


def test_malformed_rows_with_pace_still_skipped_silently():
    payload = board(
        row("AAA", rvol="not-a-mapping"),
        row("BBB", rvol={"30m": None}),
        row("CCC", rvol={"30m": "junk"}),
        row("DDD", rvol={"30m": paced_cell(None, None)}),
        row("EEE", rvol={}),
    )
    assert fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW) == []


def test_paced_events_serialize_to_json():
    payload = board(row("CRML", pct=6.1, rvol={"30m": paced_cell(0.4, 4.2)}))
    events = fastlane.rvol_spikes(payload, timeframes=("30m",), now=NOW)
    assert events and events[0]["paced"] is True
    json.dumps(events)  # must not raise


# ----------------------------------------------------------------------
# merge_events
# ----------------------------------------------------------------------

def ev(kind, symbol, at, timeframe=None):
    event = {"type": kind, "symbol": symbol, "at": at}
    if timeframe is not None:
        event["timeframe"] = timeframe
    return event


def test_merge_newest_first_dedupe_keeps_newest():
    existing = [
        ev("new_match", "NVDA", "2026-08-28T14:30:00+00:00"),
        ev("rvol_spike", "TSLA", "2026-08-28T14:29:00+00:00", timeframe="5m"),
    ]
    fresh = [
        ev("rvol_spike", "TSLA", "2026-08-28T14:31:00+00:00", timeframe="5m"),
        ev("lost_match", "USO", "2026-08-28T14:31:00+00:00"),
    ]
    merged = fastlane.merge_events(existing, fresh)
    assert [(e["type"], e["symbol"], e["at"]) for e in merged] == [
        ("rvol_spike", "TSLA", "2026-08-28T14:31:00+00:00"),
        ("lost_match", "USO", "2026-08-28T14:31:00+00:00"),
        ("new_match", "NVDA", "2026-08-28T14:30:00+00:00"),
    ]


def test_merge_dedupe_key_includes_timeframe():
    fresh = [
        ev("rvol_spike", "TSLA", "2026-08-28T14:31:00+00:00", timeframe="5m"),
        ev("rvol_spike", "TSLA", "2026-08-28T14:31:00+00:00", timeframe="15m"),
    ]
    merged = fastlane.merge_events([], fresh)
    assert len(merged) == 2  # different timeframes are different facts


def test_merge_same_type_different_symbols_kept():
    fresh = [
        ev("new_match", "NVDA", "2026-08-28T14:31:00+00:00"),
        ev("new_match", "TSLA", "2026-08-28T14:31:00+00:00"),
    ]
    assert len(fastlane.merge_events([], fresh)) == 2


def test_merge_cap_keeps_the_newest():
    existing = [
        ev("new_match", f"S{i}", f"2026-08-28T14:{i:02d}:00+00:00") for i in range(30)
    ]
    merged = fastlane.merge_events(existing, [], cap=5)
    assert len(merged) == 5
    assert merged[0]["symbol"] == "S29"
    assert merged[-1]["symbol"] == "S25"


def test_merge_total_on_garbage():
    assert fastlane.merge_events(None, None) == []
    assert fastlane.merge_events("junk", 42) == []
    merged = fastlane.merge_events(
        [None, "x", ev("new_match", "NVDA", "2026-08-28T14:30:00+00:00")],
        [7],
    )
    assert [e["symbol"] for e in merged] == ["NVDA"]
    assert fastlane.merge_events([], [ev("new_match", "NVDA", "t")], cap=0) == []


def test_merge_returns_copies_not_aliases():
    source = [ev("new_match", "NVDA", "2026-08-28T14:30:00+00:00")]
    merged = fastlane.merge_events([], source)
    merged[0]["symbol"] = "MUTATED"
    assert source[0]["symbol"] == "NVDA"


# ----------------------------------------------------------------------
# events are JSON-ready
# ----------------------------------------------------------------------

def test_all_events_serialize_to_json():
    previous = board(row("NVDA", scan_pass=True), row("USO", scan_pass=True))
    current = board(
        row("NVDA", scan_pass=True),
        row("TSLA", scan_pass=True, reasons=["macd:4h"], pct=3.2,
            rvol={"5m": cell(3.1), "15m": cell(0.5)}),
    )
    current["universe"] = ["NVDA", "TSLA", "USO"]   # USO fell off the board, not the list
    events = fastlane.merge_events(
        fastlane.diff_matches(previous, current, now=NOW),
        fastlane.rvol_spikes(current, previous=previous, now=NOW),
    )
    kinds = sorted(e["type"] for e in events)
    assert kinds == ["lost_match", "new_match", "rvol_spike"]
    json.dumps(events)  # must not raise
