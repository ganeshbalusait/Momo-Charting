# MAG7 premarket scanner (05:00–09:30 ET)

**Date:** 2026-08-20 · **Status:** Approved, not yet implemented

## Problem

The 5-minute chart already paints everything needed to judge a premarket
setup: `CALL2H` / `CALL4H` boxes from the 4x8 (yellow) and 9x20 (cyan)
families, and 🔥 bubbles when a squeeze releases on 15m/30m/1h/2h/4h/D. But
reading it means opening each of the seven Mag7 charts by hand, every
morning, and re-checking them as premarket moves. That does not scale to
seven symbols across a three-and-a-half hour window.

The user's requirement, stated three times and treated as the governing
constraint of this design: **if a signal shows on the 5m chart, it must show
in the scanner.** The table is a mirror, never a second opinion.

## What already exists

Most of this is a wiring job, not a new engine.

- `scanner.py:315` `_tos_mtf_ema_signal_payload` is the 4x8 / 9x20 engine.
  It emits `{time, family, color, timeframe, direction, label, liveForming}`
  with labels `CALL2H` / `C2H` / `CALL4H` / `C4H`, mode
  `live_forming_5m_projection`.
- `api_server.py:9686` ships that array as `mtfSignals` on the chart payload.
- `App.jsx:15090` draws the chart's boxes from that array. The comment at
  `App.jsx:15084` is explicit: *"The server computes TOS studies from the full
  one-minute history. Do not recompute 30m–4h signals from the short visible
  chart tail."*
- `api_server.py:11005` `_mag7_chart_signal_row` **already reads that same
  cached array**, memoized on `(symbol, session, latest_bar_time)`.
- `api_server.py:11244` `mag7_chart_signals_payload("premarket")` already
  produces a premarket Mag7 table, surfaced as `mag7PremarketChartSignals`.
- A premarket table UI with sortable columns exists at `App.jsx:23611`.

### Why CALL2H parity is structural

Chart bubble and scanner row read one array from one cache. There is no
second calculation to keep in sync, so no drift is possible.

One reconciler could have broken this: `mtfLiveSignalState.js`
`reconcileLiveMtfSignals` can synthesise a marker from a live tick that the
server's array does not yet contain. It feeds on `mtfLiveSignalContexts` —
and **nothing in the Python emits that key** (`api_server.py:9949` only lists
it as a payload-trim key). With no contexts it returns its input untouched.
Verified 2026-08-20. If a future change starts emitting that key, this
guarantee needs revisiting; see Risks.

## The actual gap: 🔥 lives only in the browser

`App.jsx:8606` `calculateMtfSqueezeReleaseClouds` computes squeeze release
client-side from `studyBars`. The backend cannot see it. That is the entire
reason each chart must currently be opened by hand, and it is the one piece
of real new work.

### Faithful-port contract

The port must reproduce these exactly. Each was read from source, not
assumed:

**Fire condition** — at bar `i`, with `i >= 20`:
`in_squeeze[i] == False and in_squeeze[i-1] == True`, where
`in_squeeze[i] = (avg[i] + 2*stdev[i]) - (avg[i] + 1.5*atr[i]) <= 0`.
The `avg` term cancels, so this is `2*stdev <= 1.5*atr`. Keep the unreduced
form in code so it stays legible against the JS.

**Rolling helpers** (`App.jsx:7297-7327`) — note both use a *partial*
window before period 20 fills rather than emitting NaN:
- `calculateRollingAverage(values, 20)` — mean of `values[max(0,i-19)..i]`,
  divided by the actual window length.
- `calculateRollingStdDev(values, 20)` — **population** standard deviation
  (divide by N, not N-1), same partial window.
- `calculateTrueRanges(bars)` — index 0 is `high - low`; thereafter
  `max(h-l, |h-prev_close|, |l-prev_close|)`.

**Bucket clocks** (`chartAggregation.js:137`) — three different rules, and
they are **deliberately not** the backend MTF engine's clocks. The comment at
`chartAggregation.js:110` says so directly: *"Keep this primary-chart
contract separate from the native secondary-study aggregation clocks used by
the backend CALL1H/CALL2H signal engine."* Using the MTF clocks for 🔥 would
silently misplace every fire.
- **1h (60)** — plain UTC floor: `floor(t/3600)*3600`.
- **2h (120)** — anchored to Eastern midnight, 2h steps (so 04:00 ET stays
  04:00 across DST).
- **4h (240)** — TOS equity clock, anchored to midnight *Central*: boundaries
  land at 01:00, 05:00, 09:00, 13:00, 17:00, 21:00 ET.
- **D (1440)** — Eastern midnight.

Bar aggregation itself (`aggregateChartBars`) takes first-open, max-high,
min-low, last-close, summed volume per bucket.

Only 1h / 2h / 4h / D are scanned. 15m and 30m are excluded per the user's
"1hr to D" rule — they fire near-continuously and would drown the score.

## Scan window and match rule

**Window:** 05:00:00 – 09:30:00 ET, weekdays. Session key `premarket5`, added
alongside the existing `premarket` rather than replacing it.

Two different qualifying tests, because the two signal types timestamp
differently:

- **CALL2H / CALL4H** qualify on `signal.time` falling inside the window.
  These are cross-detection times projected onto 5m candles, so they already
  land where the chart draws them.
- **🔥 fires** qualify on **bucket close**, not bucket start and not overlap.
  As of live commit `461d4af` (2026-08-20 16:13), a release is only drawn
  once its bucket has closed — the forming bucket's bands and ATR move with
  every tick, so its "release" flickered and the flame badge wandered. The
  guard is `bar.time + minutes*60 > last_source_bar_time → skip`. A fire is
  therefore a bucket-close fact, and the window test must be on close time.

  Worked through against the 05:00–09:30 window:
  - **1h** buckets close 06:00, 07:00, 08:00, 09:00 → all in window.
  - **2h** buckets (Eastern-midnight anchored) close 06:00 and 08:00 → in
    window.
  - **4h** bucket 05:00–09:00 closes 09:00 → in window, one chance per day.
  - **D** closes at Eastern midnight, so **today's daily bucket cannot close
    during premarket at all.**

**Daily fire special case.** Because a daily candle does not close until
midnight, a 🔥D visible during premarket is always the *previous* session's
release. The chart draws it (it is a closed bucket inside the session
cutoff), so parity requires including it. Rule: include the **most recent
closed daily bucket** if it released, and label the row with that fire's own
date so it is never mistaken for a fresh premarket event. Excluding it would
have made the user's explicit "1hr to D" request silently impossible.

All four fire timeframes count independently: **1h, 2h, 4h and D**, any one
of which alone qualifies a ticker for a row (and adds one point). 15m and 30m
remain excluded per the user's "1hr to D" rule.

**Match (rule 3):** a ticker earns a row if it has *any* of —
- a `CALL2H` or `CALL4H` with `family == "4x8"` (yellow), or
- a `CALL2H` or `CALL4H` with `family == "9x20"` (cyan), or
- a 🔥 release on 1h, 2h, 4h, or D.

`C2H` / `C4H` (higher timeframe not confirming) render dimmed for context and
never score — the user asked for CALL specifically.

**Strength (rule 4):** one point per hit, max 8. `>3 STRONG · =3 MODERATE ·
<3 WEAK`. Table sorts strongest-first so the names that matter never require
scrolling.

```
MSTR  4/8 CALL2H + 4/8 CALL4H + 9/20 CALL2H + 🔥1h + 🔥2h + 🔥4h = 6  STRONG
NVDA  4/8 CALL4H + 9/20 CALL4H + 🔥4h + 🔥D                       = 4  STRONG
META  4/8 CALL2H + 🔥1h + 🔥2h                                     = 3  MODERATE
AAPL  4/8 CALL2H + 🔥1h                                            = 2  WEAK
```

**Repaint honesty.** A forming 2H cross can genuinely vanish if premarket
price reverses. Rows carry the signal's own `liveForming` flag as a FORMING /
CONFIRMED tag, so a row disappearing is explained rather than mysterious.

**Cold symbols** list under `pendingSymbols` (already distinguished by the
existing payload) instead of showing a misleading zero.

## Columns

| Column | Source | New code |
|---|---|---|
| Ticker | `_mag7_option_underlyings()` | no |
| Date/Time (ET) | earliest qualifying `signal.time` | no |
| 4/8 | `mtfSignals` where `family=="4x8"` | no |
| 9/20 | `mtfSignals` where `family=="9x20"` | no |
| Squeeze Fire | **Python port**, 1h/2h/4h/D | yes |
| Strength | new scorer | yes |

## Which repo

**Implement in the live repo `AgenticAI-Trading 2` (branch `OI-scanner-BOT`),
then mirror the commit into the outer repo for git history.** The outer
`AgenticAI-Trading 7` is a mirror; nothing there executes, so building only
there would ship nothing the user can see. Line numbers below were read from
the mirror and **will differ in the live repo** — locate by symbol name, not
by line.

Verified in the live repo 2026-08-20: `premarket_scanner.py` does not exist,
no `mag7PremarketScanner` key, and `mtfLiveSignalContexts` still has no
Python producer (so CALL parity holds there too).

## Files

- `premarket_scanner.py` *(new, ~150 lines)* — three pure functions, no I/O:
  `squeeze_release_events(bars, minutes)`, `score_strength(row)`,
  `premarket_scan_row(payload, now_et)`. Kept out of `scanner.py`, which is
  already 111 KB and would give the new code no clean test seam.
- `tests/test_premarket_scanner.py` *(new)* — golden fixture (below), plus
  window-boundary, scoring-threshold, and cold-symbol cases.
- `api_server.py:10945` — cache warmer 08:00 → 05:00 ET. Without this every
  row is cold at 05:00.
- `api_server.py:10961` — add the 05:00–09:30 window alongside the existing
  prior-17:00→09:29 one (do not replace it; the existing premarket table
  still uses it).
- `api_server.py` ~11244 / ~12930 — assemble rows, expose
  `mag7PremarketScanner`.
- `frontend/src/App.jsx` near 23611 — column array + `<DataTable>`.

## Testing

The single real risk is Python 🔥 drifting from JavaScript 🔥 — three bucket
clocks and a population-stdev detail are easy to get subtly wrong.

**Golden fixture.** Freeze one symbol's `studyBars` to JSON. Run the existing
JS `calculateMtfSqueezeReleaseClouds` over it through the frontend test
harness and save its output. Assert the Python reproduces it event-for-event.
Drift then fails a test instead of quietly lying in the table at 07:15.

Also covered:
- CALL signals at 04:59:59 and 09:30:01 are excluded; 05:00:00 and 09:30:00
  are included.
- Fire bucket-close: 1h closes at 06:00/07:00/08:00/09:00 and the 4h close at
  09:00 all qualify; a still-forming bucket produces no fire at all
  (regression guard for live commit `461d4af`).
- The most recent closed daily release is included and carries its own
  (previous-session) date.
- Each of 1h, 2h, 4h and D alone is enough to produce a row.
- Scores of 2 / 3 / 4 map to WEAK / MODERATE / STRONG.
- `C2H` and `C4H` never contribute to the score.
- A symbol with no cached payload lands in `pendingSymbols` rather than
  producing a row.

## Risks

- **Earlier warmer start** means the paced Mag7 chain poller runs three extra
  hours each weekday (05:00 instead of 08:00) — more Schwab calls per
  morning. It is paced and Mag7-only, so this is expected to be fine, but it
  is a real change in broker load, and 05:00 is early enough that thin
  premarket tapes may leave some symbols warming for the first few minutes.
- **`mtfLiveSignalContexts`** is currently unpopulated, which is what makes
  CALL parity exact. If a future change starts emitting it, the chart will be
  able to show a tick-derived signal the scanner cannot see, and the scanner
  would need the same reconciliation server-side.
- **`App.jsx` line endings** — editing it with Python tooling flips the file
  to CRLF and breaks the tests that regex functions out of its source. Use
  LF-preserving edits.

## Out of scope

Alerts tab feed, web push, and chart bubbles for scanner matches were all
offered and declined. Dashboard table only.
