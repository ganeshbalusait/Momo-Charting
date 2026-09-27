"""Momentum strip: removed tickers leave the strip, and there is a Clear.

"I added the ticker in watchlist and removed all the tickers list
(watchlist) i dont see the clear" (2026-09-01). His Watchlist tab said
TICKERS 1 while the strip still showed LOST ULTA, LOST SPOT, LOST KSS, ...,
RVOL 5m 6.8 EBAY -- ten chips for tickers he had just deleted.

Three things made that happen:

1. diff_matches treated "row gone" as "dropped off the scan" and emitted a
   LOST for every ticker he removed. Removing a ticker from the list is not
   a momentum event. The payload carries the universe, so the diff can now
   tell the two apart: a symbol outside the current universe is silent.
2. merge_events keeps the newest 50 events for the list, so old NEW/RVOL
   chips for removed tickers stayed until 50 newer events pushed them out --
   on a one-ticker list, effectively forever. The ledger is now pruned to
   the current universe on every read (the warming placeholder after a
   paste carries the NEW universe, so the chips clear at once, not after
   the 30s rebuild).
3. There was no Clear at all.

Also the strip's fetch carried no ?list=, so it showed the worker's active
list on every tab. It is scoped to the open tab now, like the board fetch.
"""
import io

# --- fastlane: universe-aware diff, plus a pruner ---------------------------
p = "momx/fastlane.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '__all__ = ["diff_matches", "rvol_spikes", "merge_events"]'
NEW = '__all__ = ["diff_matches", "rvol_spikes", "merge_events", "prune_events", "universe_of"]'
assert s.count(OLD) == 1, "all anchor"
s = s.replace(OLD, NEW)

OLD = "def _stamp(now: Any) -> str:"
NEW = '''def universe_of(payload: Any) -> frozenset[str] | None:
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


def _stamp(now: Any) -> str:'''
assert s.count(OLD) == 1, "stamp anchor"
s = s.replace(OLD, NEW)

OLD = '''    A ``previous_payload`` of ``None``, a warming placeholder, or anything
    malformed emits NOTHING -- the first build of the day is a cold start,
    not a burst of momentum events. Same for a warming/malformed
    ``current_payload`` (its empty rows mean "unknown", not "everything
    just dropped out").
'''
NEW = '''    A ``previous_payload`` of ``None``, a warming placeholder, or anything
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
'''
assert s.count(OLD) == 1, "docstring anchor"
s = s.replace(OLD, NEW)

OLD = '''    for symbol in previous:
        if symbol not in current:
            events.append({"type": "lost_match", "symbol": symbol, "at": at})
    return events
'''
NEW = '''    universe = universe_of(current_payload)
    for symbol in previous:
        if symbol in current:
            continue
        if universe is not None and symbol not in universe:
            continue
        events.append({"type": "lost_match", "symbol": symbol, "at": at})
    return events
'''
assert s.count(OLD) == 1, "lost anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("fastlane.py: universe-aware diff + prune_events")

# --- worker: prune on every read, and a clear route -------------------------
p = "momx_worker.py"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''        held["events"] = fastlane.merge_events(held["events"], fresh, cap=50)
        held["prev"] = current
        held["stamp"] = stamp
        _LEDGERS[name] = held
    return {"events": held["events"], "list": name, "generatedAt": stamp}
'''
NEW = '''        held["events"] = fastlane.merge_events(held["events"], fresh, cap=50)
        held["prev"] = current
        held["stamp"] = stamp
        _LEDGERS[name] = held
    # On EVERY read, not just on a new build: right after a paste the
    # snapshot is a warming placeholder carrying the new universe, and the
    # chips for the tickers he just removed must go now, not 30s later.
    pruned = fastlane.prune_events(held["events"], current)
    if len(pruned) != len(held["events"]):
        held["events"] = pruned
        _LEDGERS[name] = held
    return {"events": held["events"], "list": name, "generatedAt": stamp}


def _clear_momentum(list_name: str | None) -> dict:
    """Empty the strip for one list. The diff baseline is kept, so the next
    build reports only what changes after the clear, not a replay."""
    current = service.snapshot(list_name)
    name = str(current.get("list") or "")
    held = _LEDGERS.get(name)
    if held is not None:
        held["events"] = []
        _LEDGERS[name] = held
    return {"events": [], "list": name, "generatedAt": current.get("generatedAt"), "cleared": True}
'''
assert s.count(OLD) == 1, "momentum anchor"
s = s.replace(OLD, NEW)

OLD = '''            if path == "/api/momx-scanner/rebuild":'''
NEW = '''            if path == "/api/momx-scanner/momentum/clear":
                self._json(HTTPStatus.OK, _clear_momentum(body.get("list")))
                return
            if path == "/api/momx-scanner/rebuild":'''
assert s.count(OLD) == 1, "route anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("momx_worker.py: prune on read + POST /api/momx-scanner/momentum/clear")

# --- panel: list-scoped fetch, Clear button --------------------------------
p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = "const MomentumStrip = memo(function MomentumStrip({ events, overflow, nowMs, focusSymbol, onPick }) {"
NEW = "const MomentumStrip = memo(function MomentumStrip({ events, overflow, nowMs, focusSymbol, onPick, onClear }) {"
assert s.count(OLD) == 1, "strip signature anchor"
s = s.replace(OLD, NEW)

OLD = '''      {overflow > 0 ? <span className="momx-mo-more">+{overflow} more</span> : null}
    </div>
  );
});'''
NEW = '''      {overflow > 0 ? <span className="momx-mo-more">+{overflow} more</span> : null}
      {typeof onClear === "function" ? (
        <button
          type="button"
          className="momx-chip-clear momx-mo-clear"
          title="Clear every momentum chip for this list. New events keep arriving."
          onClick={onClear}
        >
          clear
        </button>
      ) : null}
    </div>
  );
});'''
assert s.count(OLD) == 1, "strip tail anchor"
s = s.replace(OLD, NEW)

OLD = '''  const loadMomentum = useCallback(async () => {
    if (momentumInFlightRef.current) return;
    momentumInFlightRef.current = true;
    let events = EMPTY_ROWS;
    try {
      const response = await fetch(MOMX_MOMENTUM_ENDPOINT, { cache: "no-store" });'''
NEW = '''  // Scoped to the OPEN tab, like the board fetch. Without ?list= the worker
  // answered for its own active list, so the Watchlist tab showed the scan
  // list's chips (2026-09-01: ten LOST chips over a one-ticker watchlist).
  const loadMomentum = useCallback(async () => {
    if (momentumInFlightRef.current) return;
    momentumInFlightRef.current = true;
    let events = EMPTY_ROWS;
    try {
      const url = activeList
        ? MOMX_MOMENTUM_ENDPOINT + "?list=" + encodeURIComponent(activeList)
        : MOMX_MOMENTUM_ENDPOINT;
      const response = await fetch(url, { cache: "no-store" });'''
assert s.count(OLD) == 1, "loadMomentum anchor"
s = s.replace(OLD, NEW)

OLD = '''        momentumAliveRef.current = events.length > 0;
      }
    }
  }, []);
'''
NEW = '''        momentumAliveRef.current = events.length > 0;
      }
    }
  }, [activeList]);

  // Clear is optimistic: the chips go now, and the worker forgets them so
  // the next 15s poll does not bring them back. Silent on failure, like
  // the poll itself.
  const clearMomentum = useCallback(async () => {
    setMomentumEvents(EMPTY_ROWS);
    setFocusSymbol(null);
    try {
      await fetch(MOMX_MOMENTUM_ENDPOINT + "/clear", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(activeList ? { list: activeList } : {}),
      });
    } catch {
      /* the strip is already empty on screen; the poll will re-sync */
    }
  }, [activeList]);
'''
assert s.count(OLD) == 1, "loadMomentum tail anchor"
s = s.replace(OLD, NEW)

OLD = '''        focusSymbol={focusSymbol}
        onPick={onPickMomentum}
      />'''
NEW = '''        focusSymbol={focusSymbol}
        onPick={onPickMomentum}
        onClear={clearMomentum}
      />'''
assert s.count(OLD) == 1, "strip render anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: list-scoped momentum + clear")
