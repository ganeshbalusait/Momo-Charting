# MomX scanner history — 30-day archive of what the scan matched

**Date:** 2026-08-31
**Status:** approved design, not yet implemented

## The request

> "MomScanner — we have tabs for Mag7 & Watchlist, can we add another 2 tabs for
> History - Mag7 & Watchlist. If any tickers scan, add it in the history, save
> up to 30 days. See the image — 'cal' like that view. Search tickers in
> history, columns should be the same. Time also, so I know when it came to the
> scanner."

The live MomX board repaints continuously (every 15–35s). A ticker can hit the
scan at 10:14, be gone by 10:40, and leave no trace. This archives what the
scan matched, per day, for 30 days, so an evening or weekend review can answer:

- **what fired today, and at what time**
- **what did it look like when it fired** (the entry conditions)
- **did it actually go, or fade** (its best state that day)
- **how often does this ticker fire** (search across all 30 days)

## Decisions taken with the trader

| Question | Decision |
|---|---|
| Which state to keep when a row changes during the day | **Every change** (revised 2026-08-31 evening, superseding "arrival + best"): "if any changes again add in history because it's live changes... duplicate entry is good if any changes like rvol, squeeze, skittles any column" |
| Which rows to record | **Only rows that PASSED the scan** (`scanPass: true`) |
| Search scope | **Across all 30 days**, not just the selected day |
| Tab layout | **Four tabs**: `Mag7 \| Watchlist \| Mag7 History \| Watchlist History` |

## Precedent this follows

`premarket_scanner_history.py` (in service since 2026-08-23) already solves the
same problem for the premarket scanner: one JSON file per ET trading day,
`firstSeenAt` stamped once and never moved, atomic temp-file + `os.replace`,
30-day prune, crash-orphan `.tmp` sweep. `momx/service.py:196 _write_disk`
already uses that identical write pattern and its comments cite that module as
the source.

**This design copies that module's shape deliberately.** Not sharing code with
it: the premarket row schema and the MomX row schema are unrelated and coupling
them would make each harder to change. Copying a proven 249-line module is the
cheaper mistake than an abstraction over two things that only look alike.

The `‹ Aug 31, 2026 ›` day-nav control the trader pointed at already exists as
`.scanner-day-nav` / `.scanner-day-navbtn` / `.scanner-day-label` in
`index.css`, rendered in `App.jsx` for the premarket history calendar view.
The MomX history reuses those class names so the two views cannot drift apart
visually.

---

## 1. Storage

New module: **`momx/history.py`**. Stdlib only; directory and clock injected so
tests never touch the real archive or the real clock.

```
artifacts/momx_history/<Board>/<YYYY-MM-DD>.json
  e.g. artifacts/momx_history/Watchlist/2026-08-31.json
       artifacts/momx_history/Mag7/2026-08-31.json
```

One directory per board. Separate files rather than one file holding both
boards, because the two boards are built by independent cycles and a shared
file would make two writers race for one target.

### File shape

```json
{
  "list": "Watchlist",
  "date": "2026-08-31",
  "rows": {
    "SNOW": {
      "firstSeenAt": "2026-08-31T08:01:12-04:00",
      "lastSeenAt":  "2026-08-31T15:52:07-04:00",
      "hits": 118,
      "truncated": false,
      "snapshots": [
        { "at": "2026-08-31T08:01:12-04:00", "changed": [],
          "peakRvol": 1.1, "row": { ...full board row, at first match... } },
        { "at": "2026-08-31T09:14:40-04:00", "changed": ["rvol.1h", "skittles.2h"],
          "peakRvol": 2.8, "row": { ...row minus sparkline/quoteTrend... } }
      ]
    }
  }
}
```

The first snapshot is the arrival; each later one records a detected change,
with `changed` naming exactly which cells moved - that list is what lets the
UI highlight what changed between consecutive entries, which is the question
the search exists to answer. "Best of day" is no longer stored separately: it
is derivable (the snapshot with the highest `peakRvol`), and storing it twice
would be a second copy that could disagree.

### Rules

- **`firstSeenAt` is set once and never moves.** That is the "when it came to
  the scanner" the trader asked for. A row that drops off and returns the same
  day keeps its original stamp (it is the same day's move).
- **`lastSeenAt` and `hits`** track how long it stayed on the board — the
  difference between a one-cycle blip and a sustained move.
- **The arrival snapshot is written once** and never overwritten. It stores
  the FULL row, sparkline and quote trend included.
- **A new snapshot is appended when a SIGNAL column visibly changes** (below).

### What counts as a change

The trader said "any column", but price, % Chg and the 1D sparkline move on
every 15-35s scan cycle - triggering on them would produce ~2,000
near-identical entries per ticker per day and bury the search in noise, the
opposite of what it is for. So:

- **Trigger fields** (a visible change here appends a snapshot):
  `rvol.*`, `sqz.*`, `skittles.*`, `highLow`, `color`, `scanReasons`,
  `badge.on`, `news.headline`. The fingerprint covers each cell's **value,
  bg and fg** - a cell keeping its number but flipping colour is a state
  change on this board, because the colours encode state.
- **Ride-along fields** (stored in every snapshot, never a trigger):
  `last`, `pctChange`, `matchedSince`, `industry`.
- **Stripped from CHANGE snapshots** (kept on the arrival only):
  `sparkline`, `quoteTrend` - roughly 60% of a row's bytes, changing every
  cycle, and historically meaningful only at arrival. The UI renders those
  two cells on arrival rows and leaves them blank on change rows, which also
  makes arrivals visually distinct in the table.
- **Coalescing**: after the arrival, snapshots for one ticker are at least
  `SNAPSHOT_MIN_GAP_SECONDS = 60` apart. A change seen sooner is folded into
  the next eligible snapshot (the row stored is always the current one).
- **Cap**: `SNAPSHOTS_PER_SYMBOL_PER_DAY = 120`. At the cap, recording stops
  for that ticker for the day, `truncated: true` is set, and the UI says so -
  a silent cap would read as "the move went quiet" when it did not.

`peakRvol` per snapshot = the maximum numeric `rvol[tf].value` across every
timeframe on the row (`5m, 15m, 30m, 1h, 2h, 4h, D`). Unparseable RVOL gives
0.0 and the snapshot is still recorded - a match with unreadable RVOL is
still a match.

**Size, bounded**: a change snapshot without sparkline/quoteTrend is ~1.2 KB.
Worst case 41 matched tickers x 120 snapshots x 1.2 KB is ~6 MB/day, ~180 MB
at 30 days; realistic days should sit far below (changes cluster around the
open). The caps are stated constants, and the first live day gets measured
against them rather than trusted.

**`firstSeenAt` vs the board's `matchedSince`**: the live board shows a
holdover stamp (SNOW "8:01" may be yesterday's arrival). History is per-day:
`firstSeenAt` = first time the scan matched it TODAY. Each snapshot also
carries the row's `matchedSince`, so both readings stay available and neither
is silently redefined.

### Retention

`RETENTION_DAYS = 30`. Pruning drops day files whose ISO-date stem sorts before
the cutoff, and sweeps `.tmp` orphans older than a day. Both copied from the
precedent. **The 30-day boundary is tested explicitly** — the premarket archive
currently holds only 6 days, so that path has never actually run in production.

---

## 2. Who writes it, and when

`momx/service.py:_build_once` already: builds the board → stamps
`matchedSince` → publishes in memory → `_write_disk(name, payload)`.

Add one call immediately after `_write_disk`:

```python
history.record_board(name, payload, now_et=datetime.now(ZoneInfo(EASTERN_TZ)))
```

- **Only rows with `scanPass: true`** are recorded.
- **Wrapped so it can never break a scan.** `_build_once` already treats the
  warmer as something that must never die; a history failure must degrade to
  "no history written", never to a missing board.
- **Writes only when something changed**, so a 15-second scan cadence does not
  rewrite ~140 KB every cycle. Precisely: the file is written when (a) a symbol
  is recorded for the first time today, (b) any snapshot was appended, or
  (c) more than `WRITE_COALESCE_SECONDS = 60` have passed since the last write
  for this board. `lastSeenAt` and `hits` always advance in the loaded
  document; rule (c) is what eventually persists them. Without (c) a ticker
  that sits on the board all day with no signal changes would keep
  `lastSeenAt` from its first cycle - "how long did it stay on" would
  silently always read one cycle.
- Runs in the **momx worker process**, not api_server — the scanner already
  lives there, and api_server must not grow another disk writer.

---

## 3. The API

`api_server` proxies every path under `/api/momx-scanner*` to the worker on
:3010 (`api_server.py:20438`). Naming the endpoint under that prefix means
**no api_server change is required at all**.

New route in `momx_worker.py`, beside the existing `do_GET` routes:

```
GET /api/momx-scanner/history?list=Watchlist
GET /api/momx-scanner/history?list=Watchlist&date=2026-08-28
GET /api/momx-scanner/history?list=Watchlist&symbol=DG
```

| query | returns |
|---|---|
| `list` only | newest archived day, plus `days: [...]` for the day nav |
| `list` + `date` | that day (404-equivalent empty payload if absent, never an error) |
| `list` + `symbol` | **every day that symbol matched**, newest first, each with its `firstSeenAt` |

Response:

```json
{
  "list": "Watchlist",
  "days": ["2026-08-31", "2026-08-28", "..."],
  "date": "2026-08-31",
  "rows": [ { "symbol": "SNOW", "at": "...", "isArrival": true, "changed": [],
              "firstSeenAt": "...", "lastSeenAt": "...", "hits": 118,
              "truncated": false, "peakRvol": 2.8, "row": {...} } ],
  "retentionDays": 30
}
```

`rows` is **one entry per snapshot** - the duplicate entries the trader asked
for - ordered by `at` ascending, so a day reads as the timeline he actually
experienced: SNOW 8:01 arrives, SNOW 9:14 strengthens, DG 10:02 arrives.

For the `symbol` form, `rows` is that ticker's snapshots across every archived
day, newest day first (each entry gains a `date` field), and top-level `date`
is null.

---

## 4. The UI

`frontend/src/MomxScannerPanel.jsx`.

### Tabs

`Mag7 | Watchlist | Mag7 History | Watchlist History`

Four tabs, as asked. Under 640px (`@media (max-width: 640px)`, appended at the
END of `index.css` per the cascade rule this file has been bitten by before)
they wrap to two rows rather than shrink —
the same treatment the Learn tab bar got, and for the same reason: a clipped
label is worse than a second row.

The active tab persists in `localStorage` under the existing
`MOMX_LIST_STORAGE_KEY` pattern, so a reload returns to the tab in use.

### A history tab shows

1. **Day nav** — `‹ Mon 31 Aug 2026 ›` using the existing `.scanner-day-nav`
   classes, with a match count beside it. Arrows disabled at the ends of the
   archive rather than hidden, so the range is visible.
2. **The same dense table as the live board.** The `COLUMNS` array in
   `MomxScannerPanel.jsx` is the single definition of the board's columns; the
   history table renders from that same array. Column parity is therefore
   structural, not a promise — and a test asserts it.
3. **A `Time` column**, inserted directly after `Symbol`: the snapshot's ET
   time, with a NEW tag on arrival rows. This is the column the request is
   really about.
4. **One table row per snapshot** - the duplicates he asked for. Cells named
   in that snapshot's `changed` list are highlighted, so scanning down SNOW's
   rows reads as "what moved, when". Arrival rows carry the sparkline and
   quote cells; change rows leave them blank, which makes arrivals visually
   distinct. A ticker that hit its daily snapshot cap shows a "capped" tag on
   its last row.
5. **A search box.** Typing a ticker switches to that ticker's snapshots
   across all 30 days, grouped under day headers, each with its time and its
   highlighted changes. Clearing it returns to the selected day.

### Empty states

- No archive yet: "History starts saving the first time a ticker matches."
- A day with no matches: shown as an archived day with zero rows, not skipped.
  A quiet day is a fact worth seeing.

---

## 5. Testing

**Python — `tests/test_momx_scanner_history.py`**, mirroring
`test_premarket_scanner_history.py`:

- `firstSeenAt` is stamped once and never moves across repeated records
- a symbol that drops off and returns the same day keeps its original stamp
- the arrival snapshot is never overwritten
- an identical row appends NOTHING; a changed trigger cell appends exactly one
- a colour flip with an unchanged number IS a change; a price/%Chg/sparkline
  move alone is NOT
- coalescing: a change inside the 60s gap is folded, not lost - the next
  snapshot carries the current row
- the per-day cap stops recording, sets `truncated`, and never throws
- change snapshots carry no sparkline/quoteTrend; the arrival does
- rows with unparseable RVOL are still recorded, with `peakRvol` 0.0
- **only `scanPass: true` rows are recorded**
- **the 30-day boundary**: day 30 survives, day 31 is pruned
- a torn / truncated / garbage day file is treated as absent, never raises
- a write failure returns False and does not propagate (the scan must survive)
- `.tmp` orphans older than a day are swept
- the search form returns one entry per day, newest first

**JavaScript — `frontend/src/momxHistory.test.js`:**

- day-nav prev/next resolution, including at both ends of the archive
- search switches to cross-day mode and back
- changed-cell highlighting maps `changed` entries to the right columns
- arrival rows render sparkline/quote cells, change rows blank them
- **column parity**: the history table's columns are derived from the same
  `COLUMNS` array the live board uses

**Live verification before it is called done:** the worker writes a real day
file after a scan; the endpoint returns it through the gateway (not just
:3010); and the tab renders at desktop and iPhone widths with the same content.

---

## 6. Risks, and what they are mitigated by

| Risk | Mitigation |
|---|---|
| History write slows or breaks the scan | Runs after `_write_disk`, wrapped, write-only-on-change, ~140 KB atomic |
| Two boards racing for one file | One directory per board |
| A half-written file loses a whole day | Temp file + `os.replace`, the pattern already used here |
| History columns drift from the live board | Both render from the same `COLUMNS` array; asserted by test |
| 30-day prune has never actually run | Tested explicitly at the boundary |
| Archive grows without bound | Change snapshots strip sparkline/quoteTrend (~1.2 KB each); 60s coalescing; 120/ticker/day cap with a visible `truncated` flag; worst case ~180 MB at 30 days, realistic far lower; pruned every write; first live day measured against the caps |

## 7. Explicitly out of scope

- Per-CYCLE history (an entry every 15-35s regardless of change). The change
  log records every visible signal change, which is what the review needs;
  recording unchanged cycles as well is a database, not a JSON file.
- Statistics, win rates, or charts over the archive — get the record right first
- Backfilling history from before this ships; the archive starts empty
