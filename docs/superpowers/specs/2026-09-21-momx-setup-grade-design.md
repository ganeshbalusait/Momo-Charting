# MomX scanner grade (A+ / A / B) — an experiment on the scanner and the chart

**Date:** 2026-09-21
**Status:** approved 2026-09-22 — not yet implemented
**What this is:** a *scanner* grade under test. It is not a confirmed chart-entry
signal and not a trading system. Its job is to (1) mark agreement on screen and
(2) collect clean evidence about whether that agreement means anything.
**"A+" means "matches the experimental criteria"** — not "best ticker" and not
"will make money". The hover text and the release note say so in those words.

## The request

> "can we build like this in chart A/A+?" (MomoX screenshot: Call labels with
> A+ / A / B circles) … "in watchlist also we can do which one is A/A+ setup?"
> … "lets build in scanner and chart also."

Origin: META ran +11.3% on 2026-09-21. The scanner History showed the setup
(4h + Weekly squeeze ON premarket, both fired 09:33 with RVOL 5m 7.0), but
nothing on screen said "this row is the one".

## Decisions taken with the trader

| Question | Decision |
|---|---|
| How the grade is decided | Agreement now, proven later: a **fixed** rule marks agreement; recorded outcomes decide later whether it is worth anything. |
| How the letter is shown | Letter **+ its real track record** beside it, refreshed every evening. |
| Which rule | **Draft 2 ("the trader's rule"), unchanged, as the baseline** — exactly as it was back-tested. Its known ambiguities are logged separately (below), not silently fixed, so the baseline stays comparable. |
| Name on screen | **"Scanner grade"** — the chart is not consulted when grading. |
| Where | Watchlist + Mag7 scanner (column, sort, filter, History) and the chart (circle on bullish CALL labels). |
| Direction | Calls / BULL only in this phase. |

## Evidence so far, and how much weight it bears

Back-test over the History archive, 13 trading days (2026-08-31 … 09-21),
first A+ of each ticker per day between 09:30 and 15:30, measured to that
day's close:

| Rule | A+ per day | Closed higher | Avg to close |
|---|---|---|---|
| Draft 1 (squeeze fire required, SKIT 2h/4h + D/2D/3D) | 5 | 45% | −0.18% |
| Draft 2 (this spec's baseline) | 26 | 46% | +0.22% |

Weight: **low.** (a) It was run by an ad-hoc script and has not been
independently reproduced — step 1 of the build commits that script as
`scripts/momx_grade_backtest.py` so anyone can re-run it and check it. (b) The
archive only holds tickers while they matched the scan and caps at 120
snapshots/ticker/day. (c) "Closed higher" is not profitability: it ignores the
exit, the size of losses, option pricing and costs. This build exists to
replace these numbers with properly recorded ones.

## The baseline rule (Draft 2, unchanged)

Inputs are scanner row cells as the board already paints them.

| Check | Passes when (as tested) | Exact meaning of those colours (from `momx/columns.py`) |
|---|---|---|
| **SQZ OK** | any of SQZ 4h / D / W has bg `cyan`, **or** none of them has bg `orange` | `cyan` = medium squeeze released with momentum rising and high > close two bars back; lights for **exactly 2 bars of that timeframe** (8 h on 4h, 2 days on D, 2 weeks on W). `orange` = medium squeeze ON **or** a high-compression squeeze that just fired. `white` = high-compression squeeze ON — **not** treated as coiling by the baseline. |
| **Push** | RVOL bg `cyan`/`green` on any of 5m, 15m, 30m, 1h, 2h, 4h **at that scan**, **or** the row's news headline is ≤ 12 h old | RVOL colour is per the current bar of that timeframe (a 4h cell can reflect volume from hours ago). "News" = any headline the news feed attached; its relevance is not judged. |
| **SKIT count** | how many of 2h, 4h, D, 2D, 3D, 4D, W, M are bullish, where bullish = fg `cyan`/`dark_green` **or** bg `cyan`/`green`/`lime`/`dark_green` | **fg** = trend state (cyan: EMA9 > EMA20; dark green: that and FastD ≥ 90). **bg** = a bullish cross on *this* bar (cyan 9/20 EMA, green MACD with trend up, lime 4/8 EMA with trend up, dark green MACD alone). The baseline counts either; they are logged separately. |
| **Above 16 h midpoint** (was "near high") | H/L bg `green` | H/L degree > 0: close above the midpoint of the last 8 two-hour bars (≈16 h, extended session). |

- **A+** = SQZ OK + Push + SKIT ≥ 7 + H/L green
- **A** = SQZ OK + Push + SKIT ≥ 6
- **B** = SQZ OK + Push + SKIT ≥ 5
- otherwise blank

No hold, no smoothing: the rule is evaluated on each scan exactly as tested.
Because the live grade flickers (INTC switched A ↔ A+ nine times in an hour on
09-21), the screen also shows a **latched** fact: "first A+ today 09:33 @
$689.85". Scoring uses only that first time.

"Already +X% today" is shown as information. It does not raise or lower the
grade; nothing in the evidence supports a "too extended" rule (META was
+3.7% at its A+ and ran another +7.5%).

## What gets recorded (the experiment's real output)

Collected by the **MomX worker process** (`momx_worker.py`, supervised by
`scripts/scanner_watchdog.ps1`) — not by any chat session. It runs whenever
the worker runs.

**1. Grade tape** — every 5 minutes, every Watchlist/Mag7 symbol:
`artifacts/momx_grade_tape/<Board>/<YYYY-MM-DD>.json`, 30-day prune.
Per entry: time, last price, grade, each check's pass/fail, momentum state, 5m chart state and trigger level.

**2. Grade events** — the **first** time each symbol reaches each letter each
day: `artifacts/momx_grade_events/<Board>/<YYYY-MM-DD>.json`. Per event:

- timestamp, price, grade, and the exact reasons it qualified;
- **Push, split:** RVOL value / colour / bar time for each of 5m–4h, and news
  headline + publish time + age, recorded separately (which one supplied the push);
- **SQZ, raw:** for 4h / D / W — the colour, the squeeze count, and the
  underlying states (medium ON, high-compression ON, medium fired count,
  high-compression fired count, momentum rising), plus the time of the last
  release bar;
- **SKIT, split:** for each of the 8 timeframes — fg and bg separately, and the
  time each **bg last changed** (first-observed time, from the worker's
  memory of consecutive scans; blank after a worker restart until the next
  change is seen);
- H/L degree value;
- the latest **completed** 5-minute candle (time, OHLCV) at the event;
- momentum state, 5m chart state, trigger level and signal ages at the event.

**3. Outcomes** — filled in by the worker as time passes: price at +5, +15,
+30, +60 minutes, at 16:00, and the maximum favourable and maximum adverse
move from the event price to the close (from 5-minute bar highs/lows — the worker has no 1-minute tape). Stock prices
only — no option prices, no costs.

**Verification that collection really runs:** `/api/momx-scanner/status`
reports the tape's last write time and today's entry/event counts; on the
first live session after deploy these are checked during market hours and at
the close before the build is called done.

## Track record (shown beside the letter)

After 16:15 ET the worker aggregates all recorded events: per letter —
count, % higher at +15 m / +60 m / close, average and median to close,
average max favourable and max adverse move — also split by momentum state at the event (e.g. A+ & Building vs A+ & Fading) and by "fresh SKIT bg change in the last 15 min" vs not. Until recorded days exist it
shows the back-test, labelled "back-test from History archive (13 days)".
Win definitions for option trades (e.g. "+2% before −1%") can be added later
from the stored fields without re-recording.

## Architecture

One formula, computed once, read everywhere, so the chart and the scanner can
never disagree about the same ticker at the same minute.

```
momx/grade.py   pure: row cells + now -> {grade, checks, reasons}
      |
momx/board.py   attaches row["grade"] after news is attached (board.py ~729)
      |
momx/service.py (worker)   tape (5 min) + events (first per letter) + outcomes
      |                     + nightly track record; bg-change memory
      +--> scanner payload: row.grade, row.gradeFirstToday, top-level gradeRecord
      +--> GET /api/momx-scanner/grade-tape?symbol=&day=   (proxied by api_server)
                 |
             chart: circle on bullish CALL labels, looked up at the label's time
```

The chart reads the recorded tape instead of recomputing: recomputing
RVOL/SQZ/SKIT at past times inside `api_server` would be a second copy of the
formula and extra load on the process that already saturates its CPU. Accepted
consequence: circles exist only for Watchlist/Mag7 symbols and only from the
day recording starts; a label with no tape entry within the 5 minutes at or
before its time gets no circle.

## Momentum now (separate from the setup grade)

The grade says whether conditions **align**; momentum says whether the ticker
is **accelerating now** or showing conditions left over from earlier. Shown
side by side, never merged: `A+ | Fading ↓` means the rule still matches but
the push has changed. Draft definitions — fixed in advance, recorded and
tested like the grade, not trusted as entries. All from the worker's own 5m
tape; RVOL is the column's z-score (`momx/columns.py rvol_cell`: bullish bg
cyan ≥ 3, green ≥ 2, with close in the upper half of the bar).

| Field | Draft definition |
|---|---|
| **Trigger level** | highest high of the last 6 **completed** 5m candles (30 min) |
| **5m chart** | `Below trigger` · `Breakout – provisional` (developing candle above trigger) · `Breakout confirmed` (a completed candle closed above trigger) · `Holding` (no completed close back below since) · `Failed` (a completed close back below) |
| **Momentum** | `Building ↑` = breakout confirmed/holding **and** the latest completed candle closed in the top third of its range **and** bullish RVOL 5m or 15m ≥ 2 (`provisional` while the candle is open) · `Fading ↓` = after Building, a failed breakout, or 2 consecutive completed candles closing in their lower half with RVOL < 1 · `Extended` = price > 2 × ATR(14, 5m) above EMA20 (5m) — volatility-based, so a large day % alone never marks a stock extended · `Quiet` = none |
| **Fresh signals** | icons for changes seen in the last 15 min: SKIT bg turned bullish (which timeframes), bullish RVOL appeared (which timeframe), SQZ released (release bar is the current or previous bar of that timeframe) |
| **Signal age** | minutes since the ticker first reached its current grade today; the click-through also lists the age of the SQZ release bar and of each SKIT bg change |

**Pattern** (added 2026-09-22 — "the watchlist should surface both patterns"):
`⚡ Explosive` = a 5m bar in the last 30 min with RVOL z ≥ 3 closing in its upper
half, and the 5m chart breakout confirmed/holding (META-type). `📈 Steady` =
of the last six 15-minute candles today, at least 4 of 5 steps make a higher
high and a higher low, with the last 5m close above EMA20 (QCOM/SPY-type).
Shown inside the Setup column even when there is no grade letter; filterable;
recorded with every event; the track record is split by pattern.

Freshness times come from the worker comparing consecutive scans (15–35 s
apart), so they are accurate to one scan; after a worker restart they are
blank until the next change is observed — never back-filled with a guess.

## Scanner (Watchlist + Mag7)

**Two new columns, after Symbol** (approved 2026-09-22, chosen over five
separate columns to save width):

```
| Industry | Symbol | Setup           | Fresh            | Time  | % Chg | ... unchanged ...
| ...      | META   | A+ · Building ↑ | SKIT RVOL SQZ 0m | 09:33 | +3.67%|
| ...      | QCOM   | A+ · Holding    | SKIT 📰 · 0m      | 10:41 | +5.44%|
```

- **Setup** = the scanner grade + the momentum state, always together (a grade
  alone is what misled on INTC's flicker and IBIT's late A+). Letter coloured
  (A+ bright green, A green, B grey); arrow green for Building, red for Fading.
  Hover: 5m chart state + trigger price ("Breakout confirmed · trigger $695.20").
  **Click opens the "why" panel:**
  - *Alignment*: SKIT n/8 bullish under Draft 2, each timeframe listed with fg/bg.
  - *Push*: the RVOL timeframe(s) and z-score, and/or the news headline and its age — shown separately.
  - *Squeeze*: which timeframe met the SQZ condition, raw state, release-bar time.
  - *Location*: H/L degree (above/below the 16 h midpoint).
  - *Freshness timeline*, e.g. "4h SKIT bg turned green 09:32 · 5m bullish RVOL 09:33 · 5m breakout confirmed 09:35".
  - "First A+ today 09:33 @ $689.85", the track-record line, and: "A+ means the
    defined conditions align. It is not a recommendation and does not guarantee profit."
- **Fresh** = icons for changes in the last 15 min (SKIT bg, RVOL, SQZ release,
  📰 news) + signal age; after 15 min only the age remains (`– · 18m`).
- Sort: Setup (A+ first, then Building ahead of Holding/Fading/Quiet, ties by %
  change); Fresh (newest first). FILTERS: Setup — Any / A and up / A+ only;
  Momentum — Any / Building only.
- History keeps Time directly after Symbol as today, so there the order is
  Symbol | Time | Setup | Fresh (the column manager can change it).

### Column manager (approved 2026-09-22)

> "make columns rearrange move left or right, hide and show option also"

- A **Columns** button in the scanner toolbar opens a list of every column in
  its current order: ◀ ▶ to move one step, a show/hide checkbox, and
  **Reset to default**.
- Group headers (RVOL, SQZ, SKIT) are recomputed from runs of adjacent columns
  of the same group, so any order renders correct spans (the board already
  emits split RVOL/SKIT spans this way).
- **Symbol cannot be hidden** (a row must keep its name); everything else can.
- One layout for Live and History (History derives its columns from the same
  list — `momxHistory.js historyColumns`).
- Saved per device in browser storage, like the panel's other preferences
  (`momx.scanner.*` keys), wrapped so a blocked storage falls back to the
  default layout. Columns added in future releases appear in their default
  position for layouts saved before they existed; unknown saved keys are dropped.

- History: `grade` is stored with each snapshot as part of the row, but a
  grade change is **not** a snapshot trigger (it would spend the 120-snapshot
  cap faster).

## Chart

- A small lettered circle beside each **bullish** CALL label from the three
  label engines (TOS MTF `CALL15…CALL4H`, CloudMax `CALL1/CALL5/C5/CALL15/C15`,
  Ganesh `CALLD…CALLM`).
- Grade looked up from the grade tape: the latest entry within the label's own
  candle (open to close, capped at now).
- Hover: "Scanner grade A+ at 09:35 — not a chart-entry confirmation", the
  reasons, and the track-record line.
- No circles on PUT labels in this phase.

## Out of scope (later, separate specs)

- Fixing the baseline's ambiguities (white squeezes, orange = ON or fired,
  stale higher-timeframe RVOL, unjudged news) — decided **from the recorded
  data**, as a Draft 3 compared against this baseline.
- "Fresh bg transitions vs counting bullish timeframes" — answered from the
  recorded bg-change times, as a comparison report.
- PUT / BEAR grade; 15m / 30m / 1h squeezes in the scanner; the evening Movers
  Study; A+ phone alerts; option Entry/Exit labels.

## Error handling

- Any exception in grading → blank grade for that row; the row still ships.
- A missing cell never passes its check.
- Tape / event / record writes use the repo's temp-file + `os.replace` pattern.
- Missing or unreadable tape/record → chart shows no circles, scanner shows
  "track record unavailable". Never a stale number presented as current.

## Testing

- `tests/test_momx_grade.py` on recorded 2026-09-21 rows: META 09:33 = A+;
  QCOM 09:42 = B → 10:41 = A+; RIOT = blank; a dark-green SKIT fg counts; a
  missing cell never passes; news age limit; the grade equals the back-test
  script's grade on the same rows (the committed script and the app share
  `momx/grade.py`).
- Board: `grade` on rows and rest; a grading failure leaves the row intact.
- Tape / events / outcomes / record with an injected clock and temp dir —
  never the real archive.
- Momentum: trigger level, breakout/holding/failed, Building/Fading/Extended on hand-built 5m candle sequences, including the provisional (developing-candle) case.
- Column manager: move/hide/reset, Symbol not hideable, group spans after reordering, a saved layout missing a new column, storage throwing.
- Frontend: columns, why-panel, sort, filter; chart lookup (label time → tape entry ≤ 5
  min before, else none).
- Parity: scanner row grade == tape grade for the same symbol and minute.
- Per AGENTS.md: full relevant suites, production build, browser check of the
  scanner (desktop + narrow) and the chart (single, multi, fullscreen; two
  different tickers; several timeframes), console errors, release-notes entry;
  then the live-collection check above.
