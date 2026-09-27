"""MomoX fast lane: the momentum EVENT engine over finished board payloads.

The board (:mod:`momx.board` via :mod:`momx.service`) answers "what does the
scan say right now". This module answers the only question a momentum options
trader actually trades on: **what just CHANGED, and what is spiking, in the
last refresh**. A match that has sat on the board for 40 minutes is old news;
a symbol that newly passed the scan, or whose 5m RVOL z-score just crossed
2 sigma, is the trade.

Design rules, all deliberate:

* **Pure functions only.** No threads, no timers, no endpoints, no polling,
  no imports of ``api_server``. A later wiring step feeds this module the
  payloads the service warmer already builds; this module never fetches
  anything itself.
* **Total functions.** Malformed payloads, missing keys, ``None`` cells,
  empty boards -- every path returns a (possibly empty) list and never
  raises. An event engine that can crash the warmer is worse than no engine.
* **Never recompute.** RVOL numbers are read from the cells already on the
  rows -- they carry the thinkScript-parity values from
  :func:`momx.columns.rvol_cell`, and its ``pace`` companion for the
  still-forming bucket. Recomputing either here would fork the parity, so
  this module only ever CHOOSES between fields the producer stamped.
* **Cold starts are silent.** The first build of the day putting 19 matches
  on the board is a cold start, not 19 momentum events. Firing them would
  train the trader to ignore the strip, which defeats the whole feature.

Events are plain JSON-ready dicts, newest first::

    {"type": "new_match",  "symbol": "NVDA", "pctChange": 3.2,
     "scanReasons": ["macd:4h"], "at": "2026-08-28T14:31:02+00:00"}
    {"type": "lost_match", "symbol": "USO",  "at": "..."}
    {"type": "rvol_spike", "symbol": "TSLA", "timeframe": "5m",
     "value": 3.1, "paced": False, "pctChange": 2.4, "at": "..."}

``paced`` is the honesty flag on an rvol_spike: True means ``value`` is the
rate the still-forming bucket is RUNNING at, not the number showing in the
board cell. See :func:`rvol_spikes` for the full cell contract.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Mapping

__all__ = ["diff_matches", "rvol_spikes", "merge_events", "prune_events", "universe_of"]

#: The trader's own colour ladder treats a ratio >= 2 as significant (the
#: green rung in ``momx.columns.rvol_cell``), so 2.0 is the default edge.
DEFAULT_RVOL_THRESHOLD = 2.0

#: The fast timeframes: "just started moving", not "has been elevated all day".
#:
#: 30m was added on 2026-09-01 after a live sweep at 10:40 ET over
#: AAPL/META/NFLX/CRML/NVDA/TSLA/AMZN/MSOS/AA/GRAB: 5m and 15m read 0.0-0.6
#: for EVERY name while 1h read 1.1-3.5, so the pair this module watched
#: could not fire on ANY of them -- and did not, all day, while CRML printed
#: 23.4x at the open. The immediate cause of the flat 5m/15m is a data one
#: (Alpaca SIP 403s on recent bars, so the tape's last ~20 minutes carry IEX
#: volume, ~2-4% of consolidated) and is being fixed in :mod:`momx.feed`; the
#: timeframe set is a SEPARATE defect and is fixed here. 30m is where a
#: sustained open drive shows up while it is still tradeable.
#:
#: 1h and slower stay out on purpose: by the time an hourly bucket crosses,
#: the options entry is gone (same reasoning as ``momo_alert.ALERT_TIMEFRAMES``
#: excluding 4h/D). Order is FASTEST FIRST and is load-bearing: events are
#: emitted in the given timeframe order, so the strip leads with the earliest
#: read of the same move.
DEFAULT_RVOL_TIMEFRAMES = ("5m", "15m", "30m")

#: Sanity ceiling on a pace-adjusted read (see :func:`rvol_spikes`). A pace
#: above this is not believed -- the read falls back to the plain ``value``.
#:
#: Why a ceiling at all: pace divides by the fraction of the bucket that has
#: elapsed, so a bucket three seconds into thirty minutes multiplies by 600.
#: The producer is contracted to refuse to publish that (``columns`` will not
#: pace a bucket under ``RVOL_PACE_MIN_ELAPSED``), but this module is the one
#: whose output reaches the trader's PHONE, and "the producer promised" is
#: exactly the assumption this repo has been burned by -- a field stamped
#: truthfully by a narrow writer and believed by a wider reader.
#:
#: Why 50 specifically, and why it cannot silence a real mover: with the
#: current producer guard (elapsed >= 0.15) pace is at most ~6.7x the plain
#: value, so 50 is far outside anything a guarded producer can emit -- CRML's
#: 23.4x open, the loudest print the board has shown, passes untouched.
#: And when the ceiling DOES trip, the fallback ``value`` of such a bar is
#: still >= 7.5 (50 x 0.15), i.e. still far above any sane threshold, so the
#: alert still fires -- just carrying the smaller number that actually
#: traded. The ceiling costs a real signal nothing; it only refuses to
#: forward an absurd magnitude.
DEFAULT_PACE_CEILING = 50.0

#: Hard ceiling for :func:`merge_events`.
DEFAULT_EVENT_CAP = 100


# ---------------------------------------------------------------------------
# payload plumbing (all total, all private)
# ---------------------------------------------------------------------------

def _usable(payload: Any) -> bool:
    """A payload we may DIFF against without inventing events.

    Three things disqualify a payload:

    * not a mapping at all (malformed);
    * ``warming`` truthy -- the service's "first build has not finished"
      placeholder. Its empty ``rows`` list means "unknown", not "no matches";
      diffing against it would fire the whole board as events;
    * ``rows`` missing or not a list -- indistinguishable from warming. An
      EMPTY ``rows`` list is fine: that is a real board where nothing matched.
    """
    if not isinstance(payload, Mapping):
        return False
    if payload.get("warming"):
        return False
    return isinstance(payload.get("rows"), (list, tuple))


def _rows(payload: Any) -> list[Mapping[str, Any]]:
    """The row mappings that carry a symbol, in board (rank) order."""
    if not isinstance(payload, Mapping):
        return []
    raw = payload.get("rows")
    if not isinstance(raw, (list, tuple)):
        return []
    rows: list[Mapping[str, Any]] = []
    for row in raw:
        if isinstance(row, Mapping) and str(row.get("symbol") or "").strip():
            rows.append(row)
    return rows


def _symbol(row: Mapping[str, Any]) -> str:
    return str(row.get("symbol") or "").strip().upper()


def _matched(payload: Any) -> dict[str, Mapping[str, Any]]:
    """symbol -> row for every row the scan PASSED, preserving board order."""
    matched: dict[str, Mapping[str, Any]] = {}
    for row in _rows(payload):
        if row.get("scanPass"):
            matched.setdefault(_symbol(row), row)
    return matched


def universe_of(payload: Any) -> frozenset[str] | None:
    """The list's symbols as the board reported them, or None if it did not.

    ``None`` means "unknown", which every caller treats as "keep everything":
    an older payload shape without a ``universe`` key must not make the
    strip go blank. The warming placeholder DOES carry the universe, which is
    what lets a freshly pasted list clear its stale chips before its first
    build finishes.
    """
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("universe")
    if not isinstance(raw, (list, tuple)):
        return None
    return frozenset(str(item or "").strip().upper() for item in raw if str(item or "").strip())


def prune_events(events: Any, payload: Any) -> list[dict]:
    """Drop events for symbols no longer in ``payload``'s universe.

    A ticker the trader removed from the list is not "old news", it is not
    news at all; its NEW / LOST / RVOL chips have nothing to point at. Total:
    garbage in gives an empty list, an unknown universe keeps everything.
    """
    if not isinstance(events, (list, tuple)):
        return []
    kept = [dict(event) for event in events if isinstance(event, Mapping)]
    universe = universe_of(payload)
    if universe is None:
        return kept
    return [event for event in kept if _symbol(event) in universe]


def _stamp(now: Any) -> str:
    """ISO-8601 UTC timestamp; ``now`` may be a datetime (test seam) or None."""
    if isinstance(now, datetime):
        moment = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
        return moment.isoformat()
    if isinstance(now, str) and now:
        return now
    return datetime.now(timezone.utc).isoformat()


def _cell_field(row: Any, timeframe: str, field: str) -> float | None:
    """One numeric field of one RVOL cell, or None.

    Cells are ``{"value": ..., "bg": ..., "fg": ...}`` per the columns
    contract, but a bare number is tolerated -- and a bare number IS the
    value, so it can never carry any other field. ``None``, ``"-"``, missing
    keys, NaN, booleans and anything non-numeric all collapse to None --
    skipped silently, per contract. Fields are READ, never recomputed.
    """
    if not isinstance(row, Mapping):
        return None
    cells = row.get("rvol")
    if not isinstance(cells, Mapping):
        return None
    cell = cells.get(timeframe)
    if isinstance(cell, Mapping):
        raw = cell.get(field)
    elif field == "value":
        raw = cell
    else:
        raw = None
    if raw is None or isinstance(raw, bool):
        return None
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _cell_value(row: Any, timeframe: str) -> float | None:
    """The DISPLAYED RVOL number -- exactly what the board cell shows."""
    return _cell_field(row, timeframe, "value")


def _cell_reading(row: Any, timeframe: str, ceiling: float | None) -> tuple[float | None, bool]:
    """``(number, paced)`` -- the number this module thresholds on.

    ``pace`` when the producer stamped a usable one, else ``value``. See the
    contract block in :func:`rvol_spikes`; the ceiling rationale is on
    :data:`DEFAULT_PACE_CEILING`.

    The SAME reader must be used for the current payload and for the edge
    baseline. Reading pace now and value last build would make a bucket look
    like it "crossed" every time it re-paced, which is the alert-spam shape
    the edge rule exists to prevent.
    """
    pace = _cell_field(row, timeframe, "pace")
    if pace is None:
        return _cell_value(row, timeframe), False
    if ceiling is not None and pace > ceiling:
        # An absurd pace means the producer's barely-started-bucket guard is
        # missing or broken. Degrade to TODAY's behaviour (the plain value)
        # rather than forward a number nobody can defend: this repo's rule is
        # that a wrong-but-plausible figure on the phone is worse than a
        # conservative one.
        return _cell_value(row, timeframe), False
    return pace, True


# ---------------------------------------------------------------------------
# public API
# ---------------------------------------------------------------------------

def diff_matches(previous_payload: Any, current_payload: Any, *, now: Any = None) -> list[dict]:
    """One event per symbol that ENTERED or LEFT the scan's matched set.

    ``new_match`` fires for a symbol whose ``scanPass`` is true in
    ``current_payload`` but was not true in ``previous_payload`` (whether it
    flipped on the board or is newly on the board at all). ``lost_match``
    fires for the reverse. Nothing else is an event: rank churn, price
    churn, pctChange churn and reason churn are all silent by construction,
    because membership in the matched SET is the only thing compared.

    A ``previous_payload`` of ``None``, a warming placeholder, or anything
    malformed emits NOTHING -- the first build of the day is a cold start,
    not a burst of momentum events. Same for a warming/malformed
    ``current_payload`` (its empty rows mean "unknown", not "everything
    just dropped out").

    A symbol that is absent from ``current_payload``'s *universe* was removed
    from the list by the trader. That is an edit, not a momentum event, so it
    never becomes a ``lost_match`` (2026-09-01: pasting a one-ticker
    watchlist over a ten-ticker one produced ten LOST chips). A symbol still
    in the universe but off the board -- pushed out by the row cap -- is a
    genuine loss and still fires.

    New matches come first (board order -- already ranked by the scan), then
    lost matches; every event in one call shares one ``at`` stamp.
    """
    if not _usable(current_payload) or not _usable(previous_payload):
        return []

    previous = _matched(previous_payload)
    current = _matched(current_payload)
    at = _stamp(now)

    events: list[dict] = []
    for symbol, row in current.items():
        if symbol not in previous:
            reasons = row.get("scanReasons")
            events.append(
                {
                    "type": "new_match",
                    "symbol": symbol,
                    "pctChange": row.get("pctChange"),
                    "scanReasons": list(reasons) if isinstance(reasons, (list, tuple)) else [],
                    "at": at,
                }
            )
    universe = universe_of(current_payload)
    for symbol in previous:
        if symbol in current:
            continue
        if universe is not None and symbol not in universe:
            continue
        events.append({"type": "lost_match", "symbol": symbol, "at": at})
    return events


def rvol_spikes(
    payload: Any,
    *,
    threshold: Any = DEFAULT_RVOL_THRESHOLD,
    previous: Any = None,
    timeframes: Any = DEFAULT_RVOL_TIMEFRAMES,
    pace_ceiling: Any = DEFAULT_PACE_CEILING,
    now: Any = None,
) -> list[dict]:
    """Rows whose RVOL sits at/above ``threshold`` on a listed timeframe.

    Numbers are READ from the cells -- the thinkScript-parity numbers
    :mod:`momx.columns` already computed -- never recomputed here. Null /
    ``"-"`` / missing cells are skipped silently.

    **Which number, exactly (the cell contract).** Per timeframe this reads
    ``row["rvol"][tf]`` and takes:

    * ``cell["pace"]`` when that key is present and numeric-finite;
    * otherwise ``cell["value"]`` (a bare number in place of the cell mapping
      is also accepted, and is a value).

    ``pace`` means: *this bucket's RVOL re-expressed as the full-bucket rate
    it is currently running at* -- the same ratio ``value`` carries, divided
    by the fraction of the bucket that has elapsed. It exists because a
    still-forming bucket is structurally understated: at 09:35 a 30m bucket
    holds 5 of 30 minutes of volume but is divided by an average of COMPLETE
    30m buckets, so it cannot reach the threshold until it is nearly closed
    -- i.e. after the move. Reading pace is what lets a bucket running Nx
    normal pace fire immediately.

    **What the producer must have already done.** Dividing by the elapsed
    fraction explodes when that fraction is tiny: three seconds into a 30m
    bucket multiplies by 600, and one odd lot becomes a 400x "spike". The
    producer is therefore contracted to publish ``pace`` ONLY for a bucket
    far enough in to judge (``momx.columns`` uses
    ``RVOL_PACE_MIN_ELAPSED``), to publish a complete bucket's pace as the
    value itself, and to publish ``None`` -- not a rescued number -- whenever
    the answer would be untrustworthy. A ``None``/absent/non-numeric ``pace``
    is not an error here: it falls back to ``value``, i.e. to exactly this
    module's pre-pace behaviour.

    **What this module does if the producer has NOT done it.** It does not
    trust the promise. A ``pace`` above ``pace_ceiling``
    (:data:`DEFAULT_PACE_CEILING`; pass ``None`` to disable) is discarded and
    the plain ``value`` is used instead, so one broken guard upstream cannot
    spam the trader's phone with a 400x alert. It is *fallback*, never a
    clamp: this module will not publish a number that no producer computed.

    Each event carries ``"paced": True`` when its ``value`` came from
    ``pace``. A paced number deliberately does NOT match the RVOL cell the
    trader sees on the board (that cell shows the bucket as-traded so far),
    and anything rendering the event owes him that distinction.

    Without ``previous`` this reports the LEVEL: every qualifying row/
    timeframe. With ``previous`` (the prior board payload) it reports the
    EDGE: only pairs whose reading was below the threshold -- or absent --
    last build and is at/above it now. A row sitting at 3.1 across two
    payloads therefore fires on the build that crossed, and never again while
    it just stays elevated. Both sides of that comparison use the same
    pace-preferring reader, so a bucket that is merely re-paced upward each
    build is a level, not a crossing. A warming or malformed ``previous``
    emits nothing (same cold-start rule as :func:`diff_matches`: no baseline,
    no events).

    Events follow board order, then the given timeframe order, one shared
    ``at`` stamp per call.
    """
    if not _usable(payload):
        return []
    try:
        floor = float(threshold)
    except (TypeError, ValueError):
        return []
    if isinstance(timeframes, str):
        timeframes = (timeframes,)
    try:
        frames = [str(tf) for tf in timeframes]
    except TypeError:
        return []

    # An unusable ceiling falls back to the default rather than to "no
    # ceiling": a typo'd caller must not silently disarm the phone guard.
    if pace_ceiling is None:
        ceiling: float | None = None
    else:
        try:
            ceiling = float(pace_ceiling)
        except (TypeError, ValueError):
            ceiling = DEFAULT_PACE_CEILING
        if not math.isfinite(ceiling):
            ceiling = DEFAULT_PACE_CEILING

    edge = previous is not None
    if edge and not _usable(previous):
        return []
    baseline: dict[str, Mapping[str, Any]] = {}
    if edge:
        for row in _rows(previous):
            baseline.setdefault(_symbol(row), row)

    at = _stamp(now)
    events: list[dict] = []
    for row in _rows(payload):
        symbol = _symbol(row)
        for timeframe in frames:
            value, paced = _cell_reading(row, timeframe, ceiling)
            if value is None or value < floor:
                continue
            if edge:
                prior = baseline.get(symbol)
                before = _cell_reading(prior, timeframe, ceiling)[0]
                if before is not None and before >= floor:
                    continue  # still elevated, not a new crossing
            events.append(
                {
                    "type": "rvol_spike",
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "value": value,
                    "paced": paced,
                    "pctChange": row.get("pctChange"),
                    "at": at,
                }
            )
    return events


def merge_events(existing: Any, fresh: Any, *, cap: Any = DEFAULT_EVENT_CAP) -> list[dict]:
    """Fold ``fresh`` events into ``existing``: newest first, de-duplicated, capped.

    De-duplication key is ``(type, symbol, timeframe)`` -- a repeat of the
    same event replaces the older copy rather than stacking, so the strip
    shows each live fact once. The NEWEST copy wins (by ``at``; ties go to
    ``fresh``). Ordering is newest first by ``at``; events missing a usable
    stamp sink to the end. Non-list inputs and non-dict entries are skipped;
    the result is always a fresh list of shallow copies, never an alias.
    """
    def _clean(seq: Any) -> list[Mapping[str, Any]]:
        if not isinstance(seq, (list, tuple)):
            return []
        return [event for event in seq if isinstance(event, Mapping)]

    try:
        ceiling = int(cap)
    except (TypeError, ValueError):
        ceiling = DEFAULT_EVENT_CAP
    if ceiling <= 0:
        return []

    def _at(event: Mapping[str, Any]) -> str:
        stamp = event.get("at")
        return stamp if isinstance(stamp, str) else ""

    kept: dict[tuple, dict] = {}
    for event in _clean(fresh) + _clean(existing):  # fresh first: ties keep fresh
        key = (
            event.get("type"),
            str(event.get("symbol") or "").strip().upper(),
            event.get("timeframe"),
        )
        held = kept.get(key)
        if held is None or _at(event) > _at(held):
            kept[key] = dict(event)

    merged = list(kept.values())
    merged.sort(key=_at, reverse=True)  # stable: equal stamps keep fresh-first order
    return merged[:ceiling]
