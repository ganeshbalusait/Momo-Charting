# MomX Scanner - build spec

Source of truth for the MomX Scanner board. Everything here came from the trader's
thinkorswim setup (screenshots + pasted thinkScript) on 2026-08-26/27. When this file
and the code disagree, THIS FILE WINS - the whole point is TOS parity.

## What it is

A re-creation of the trader's thinkorswim workflow, in AGX:

- **Scanner**: the TOS Stock Hacker scan `AlertX Bull Momo ALL_Jan26_MyWatchlist`
- **Watchlist board**: the scan's result set rendered as the dense multi-column
  board (the layout MomoX/MomoScan renders from the same TOS watchlist)

Left-nav entry `MomX Scanner`, placed directly below `Mag7 Scanner`. Icon: `Radar`
(free; `ScanSearch` is Premarket Scanner and `Trophy` is Mag7 Scanner).

## Universe

TOS `Scan in:` is set to the watchlist `aa_MOSTWATCHLIST` (~384 names pass Last>=3.00).
Futures excluded. Output: `Show: 50`, `Sorted by: % change`, `Descending`.

**STILL MISSING: the aa_MOSTWATCHLIST symbol list.** The trader exports it from TOS
(right-click watchlist tab -> Export) and pastes it into the panel's `Tickers:` box.
The panel's ticker box is the permanent mechanism, not a stopgap.

**Seed universe = the trader's TOS `001_Mega7` watchlist** (given 2026-08-27):

    AAPL AMZN GOOGL META MSFT NFLX NVDA TSLA AVGO USO

This is exactly `premarket_scanner.PREMARKET_SCAN_SYMBOLS` (9 names) **plus USO**.
It is the list the trader's scanner screenshot was pointed at (`Scan in: 001_Mega7`,
18 matches). Use this as `momx.board` default universe - NOT the earlier ad-hoc seed.

DECIDED 2026-08-27: USO goes in the **MomX board only**. The existing premarket scanner
and the chart warm-set are NOT changed - adding a symbol there costs CPU on the engine
that feeds the live charts, and MomX has no such constraint (own bulk feed). `PREMARKET_SCAN_SYMBOLS` must stay in sync with
`api_server.QUICK_STRIP_WARM_SYMBOLS` or the new symbol silently produces no rows.
MomX does not have that constraint - it runs on its own bulk feed.

## The scan

ALL of the following:
1. `Last >= 3.00`
2. `Price_Change(VOLUME)` on **4h, EXT** - at least **0.5%** greater than 2 bars ago
3. `Price_Change(CLOSE)`  on **1h, EXT** - at least **0.3%** greater than 2 bars ago

ANY of the following:
- **A. Momentum cross** on 2h(EXT), 4h(EXT), D, 2D, 3D, 4D, Wk, M - any of:
  - `MACD(6,12,8)."Value"` crosses above `MACD(6,12,8)."Avg"`
  - `MovAvgExponential()."AvgExp"` (len 9) crosses above `MovAvgExponential(20)."AvgExp"`
  - `MovAvgExponential(4)."AvgExp"` crosses above `MovAvgExponential(8)."AvgExp"`
- **B. Squeeze fired** (scanner variant) on 2h(EXT), 4h(EXT), D, Wk
- **C. RVOL** (scanner variant) on 5m, 15m, 30m, 1h, 2h, 4h, D

Current-bar crosses only. There is **no** `Highest(...,12)` lookback here - that
belongs to a DIFFERENT scan (the premarket scanner's AlertX Bull Momo variant).
Conflating the two is a parity bug.

## Board columns (left to right)

    Industry | Symbol | %Change
    | R_2h | R_4h | R_D | High/Low | RVol_5m | RVol_15m | RVol_30m | R_1h
    | COLOR
    | Sqz_2h | Sqz_4h | Sqz_D | Sqz_Wk
    | Skit_2h | Skit_4h
    | Quote Trend
    | Skit_D | Skit_2D | Skit_3D | Skit_4D | Skit_Wk | Skit_M

Matches the trader's TOS watchlist column list exactly, with one removal:
**Earnings-DAYS was explicitly dropped by the trader.**

## Column definitions

All ported literally from the trader's thinkScript. See the workflow script for the
verbatim source. Key points that are easy to get wrong:

- **RVOL has TWO different scripts.** The watchlist column uses
  `buying = close - low; selling = high - close`. The scanner uses
  `buying = volume*(close-low)/(high-low)`. Do not unify them.
- **Squeeze has TWO different scripts.** Column uses `high > close[2]` and
  `MFD[1]`. Scanner uses `high > close[1]` and `MFD[1] and higher`.
  A regression test asserts the two DISAGREE on a constructed tape.
- **Skittles** = `StochasticFast(k=8, d=8, average type=WEIGHTED)."FastD"`, rounded
  to 0dp. Colours come from MACD/EMA cross state with `within 1 bars`
  (= current bar OR previous bar - a classic off-by-one).
- **COLOR** is a pure white separator column. `plot blank = Double.NaN; blank.Hide();
  AssignBackgroundColor(Color.WHITE);` No value, no logic.
- **Quote Trend** (trader's replication script, received 2026-08-27):
      def isUp = close > close[1];  def isDown = close < close[1];
      plot QuoteTrendScore = if isUp then 1 else if isDown then -1 else 0;
      value colour: 1 -> GREEN, -1 -> RED, 0 -> GRAY
      background:   1 -> DARK_GREEN, -1 -> DARK_RED, 0 -> BLACK
  Renders as the mini up/down histogram over recent bars.
  NOTE: this adds `gray` to the colour palette.

## Data plan

Everything is computed from three bulk Alpaca fetches, then folded locally:

| Tape | Resolution | Depth | Feeds |
|---|---|---|---|
| intraday fine | 5m, EXT | ~5 trading days | 5m, 15m, 30m |
| intraday coarse | 30m, EXT | ~30 trading days | 1h, 2h, 4h |
| daily | 1D, RTH | ~3 years | D, 2D, 3D, 4D, Wk, M |

EXT (extended hours) comes from Alpaca's BOATS feed - the same feed that made the
chart's 2H/4H TOS labels match. Schwab and IEX do not carry those candles.

**Bucket anchoring** - intraday and D/Wk/M are SOLVED and already validated in this
repo; do not re-derive them. **2D/3D/4D are NOT** - see
"The 2D/3D Skittles gap" below, which supersedes the `_timeframe_group_key` bullet
for the watchlist columns.

`ganesh_higher_timeframe_signals.py` (SCHEMA_VERSION v17) is a backend replay of these exact
TOS studies, matched against real TOS charts. It supplies:

- `_tos_four_hour_bucket_time()` - 4h anchored at ET midnight + 1h, i.e. 01/05/09/13/17/21 ET
- `_timeframe_group_key()` - D/2D/3D/4D/W/M grouping. Thinkorswim fixed-duration equity
  aggregations use a **1969-12-30 phase** (two days before the Unix epoch):
  `floor((days_since_1970_01_01 + 2) / N)`. An ABSOLUTE calendar phase - not anchored from
  the newest bar. The module records that the Unix phase "shifts all three live signal
  candles"; the correct phase is what keeps 3D in Friday's group on Sunday, rolls 2D on
  Monday, and stops 4D emitting a spurious Sunday bubble.
  **This is the SIGNAL clock only.** It is measurably NOT the watchlist column clock for
  2D/3D/4D - see "The 2D/3D Skittles gap". `momx.buckets` still exposes it as
  `grouping="ema"` / `grouping="macd"`; the ghts module itself is untouched.
- `_macd_timeframe_group_key()` - a real TOS quirk: the MACD study's 3D series rolls one
  calendar day AHEAD of the EMA 3D series, so MACD-3D uses `floor((ordinal + 1) / 3)`.
  Found against a live INTC chart. Nobody would rediscover this by guessing.
- `_next_ema()` - EMA seeding is "first value seeds the EMA", NOT an SMA seed.
- `_source_values()` - MACD(6,12,8) as `ema6 - ema12`, avg `_next_ema(macd_value, 8)`.

The scan's OR-block A (MACD 6/12/8, EMA 9x20, EMA 4x8) on D/2D/3D/4D/W/M is therefore
**already built and TOS-matched**. Only the 2h/4h EXT intraday cases are new work.
Tests assert the new code agrees with this module rather than reimplementing it.

## Non-negotiable performance constraints

This repo has a documented history of scanners starving the chart engine of CPU
(OI Finder "WAIT FOR DATA", the six-chart freeze, the 5s-poll CPU takeover).

- `momx/` starts **no threads at import**. A test asserts `threading.active_count()`
  is unchanged after `import momx.board`.
- `momx/` never imports `api_server`, never touches its caches, never warms charts
  or option chains. A test greps for this.
- The panel polls at **60s**, guarded by an in-flight ref, paused when the tab is
  hidden, with `useMemo` on derived arrays.

## Module map

    momx/indicators.py   EMA/MACD/WMA/StochasticFast/TTM_Squeeze/crosses/RVOL z-score
    momx/buckets.py      TOS-clock aggregation (intraday + multi-day)
    momx/feed.py         bulk Alpaca fetch, network I/O only
    momx/columns.py      per-symbol cells (value + bg + fg)
    momx/scan.py         the AlertX Bull Momo scan
    momx/board.py        orchestration, cache, universe persistence
    momx/industries.py   hand-editable SYMBOL -> industry map (no sector API)
    frontend/src/momxCells.js         colour/format/sort helpers (pure)
    frontend/src/MomxScannerPanel.jsx the board

Backend resolves all thinkScript colours; the frontend re-derives none.

## Open items

- [ ] `aa_MOSTWATCHLIST` symbol list (trader export) - blocks true row-set parity
- [ ] `High/Low Graph` real formula. Trader gave params only (2h agg, length 8, EXT)
      and confirmed the RENDERING: a drawn block, no digits, like TOS. Rendering is
      now correct (kind:"bar"); the underlying 0-100 formula is still INFERRED.
- [ ] API endpoint `/api/momx-scanner` + `/api/momx-scanner/universe` (not yet wired)
- [ ] App.jsx nav entry + panel mount (not yet wired)
- [ ] Live parity check: run the board during market hours and diff its row set
      against the TOS scan's

## Phase 3 - AI triage layer (decided 2026-08-27, build AFTER the board is TOS-verified)

An AI layer that READS the finished board. It is never in the scan path: the scan stays
pure deterministic math so a disagreement with TOS is always a real, debuggable bug rather
than model variance.

Scope when built:
- rank the 50 matches - "these 5 deserve a chart, and why"
- turn scanReasons ("macd:4h, sqzfired:D, rvol:30m") into a plain sentence
- pull earnings/news context from the existing catalyst_engine.py + earnings calendar
- cross-reference the OI call/put walls the app already computes

Existing plumbing to reuse: reasoning_engine.py, llm_trade_advisor.py, ai_ensemble.py.

REJECTED: using an LLM to evaluate the scan conditions. ~50k calculations per refresh over
400 symbols x 12 timeframes; an LLM is slower, costs per refresh, and is non-deterministic.
Non-determinism destroys the parity premise - you could never tell a real bug from variance.

## Recorded assumption: the TOS Sunday-continuation rule is NOT carried into momx

`ganesh_higher_timeframe_signals._tos_overnight_group_continues_from_prior_session()`
suppresses DAY-or-higher signals on the non-trading Sunday: TOS assigns the Sunday
17:00/21:00 EXTO candles to Monday's session, but its daily-or-higher aggregation does
not open a new calendar period on Sunday. Without that rule you get synthetic Sunday
CALL2D/CALL4D bubbles.

Verified 2026-08-27: `momx/` does not carry this rule.

Why that is believed safe: the momx daily-and-slower tapes (D/2D/3D/4D/Wk/M) are
**regular-hours daily bars** - EXT is unchecked on those rows in the trader's scan - so
no Sunday bar ever enters the daily path and no Sunday group key is ever produced. EXT
applies only to the 2h/4h intraday rows, which are not multi-day aggregations.

Note the raw group keys DO roll on Sunday by calendar date (2D goes 10344 -> 10345 on
Sunday, not Monday). The module's "2D rolls on Monday" behaviour is produced by the
continuation rule, not by the group key. So if momx ever starts feeding EXTENDED-hours
bars into the daily path, this assumption breaks and the rule must be ported.

This is the "complete for the narrower thing" bug shape: a rule that is true for the
narrow writer (RTH daily bars) being relied on by a wider reader. Flagged deliberately.

## LIVE PARITY CHECK - 2026-08-27 15:40 ET (market open)

First real validation of the scan against thinkorswim, on the 10-name seed universe.

| | result |
|---|---|
| TOS `AlertX Bull Momo` | **AVGO** only |
| MomX board | **AVGO** only |

**Match.** Confirmed with the trader live.

Two false leads worth recording so nobody re-walks them:

1. He first reported "AVGO and MSFT", which made MSFT look like a false negative.
   MSFT computes +0.2668% on the 1h gate against a 0.30% threshold - a near miss,
   which is exactly what a subtly-wrong formula looks like. Three alternative
   readings of the `Price_Change` row were tested (bar0 = forming vs last-closed;
   "2 bars ago" = 2 back vs 1 back) and MSFT failed all of them. He then corrected
   himself: MSFT is NOT in the TOS results. The original reading was right.
   **Do not "fix" `price_change_gate` on the strength of a near-miss.**

2. The near miss was blamed on Alpaca's free SIP plan being ~20 minutes delayed.
   Disproved directly: fetched the live IEX price (IEX has no recency block) and
   MSFT computed +0.1914%, i.e. FURTHER from passing. The delay was not the cause.
   The 20-minute SIP lag is real and is still a genuine limitation, but it did not
   produce this discrepancy.

NVDA is the useful negative control: six OR-conditions fired (macd:D, ema4x8:D,
ema4x8:2D, macd:3D, ema4x8:3D, macd:4D) and BOTH TOS and the board still reject it,
on the 1h `Price_Change` gate. The AND gates are doing real work.

Still unvalidated: the row set over the full ~384-name `aa_MOSTWATCHLIST`, and every
watchlist COLUMN value (only the scan's pass/fail has been checked against TOS).


## The 2D/3D Skittles gap - SOLVED 2026-08-27 (weekday chunks, not calendar chunks)

### The measurement

Trader read CRWD off his thinkorswim watchlist, live, on 2026-08-27, against the board
(after the split-adjustment fix):

| span | ours | TOS | status |
|---|---|---|---|
| D | 31 | 31 | MATCH |
| 2D | 42 | 53 | WRONG |
| 3D | 55 | 68 | WRONG |
| 4D | 68 | 68 | MATCH |
| Wk | 71 | 71 | MATCH |
| M | 76 | 71 | still unexplained, see below |

Reproduced before anything was changed (`momx.feed.fetch_daily` -> `aggregate_daily` ->
`columns.skittles_cell`): 31 / 41 / 55 / 67 / 71 / 76. 2D and 4D read one point below the
trader's figures because the forming daily bar had ticked in between; every other span
reproduced exactly. **D, 4D and Wk matching proves the Skittles maths is right and the
multi-day GROUPING is wrong.**

### The finding

**Thinkorswim chunks N consecutive WEEKDAYS. The board was chunking N consecutive
CALENDAR days.** That is a units bug, not a phase bug.

Calendar chunking puts a weekend *inside* a chunk. 2D pairs Fri+Sat and then Sun+Mon, so
Friday sits alone in its candle and Monday opens a fresh one. TOS pairs Fri+Mon.

The shipped rule is now, in `momx/buckets.py`:

    group = floor((weekdays_since_1970_01_01 + 6) / N)      # N = 2, 3, 4

`D`, `Wk` and `M` are untouched and still delegate to `ganesh_higher_timeframe_signals`.
Result on the same tape: **31 / 53 / 68 / 68 / 71** - all five known TOS values match.

### The full search (this table is the evidence, not the winner)

Every candidate was scored on the live CRWD tape. `*` = matches TOS.

| grouping | 2D (TOS 53) | 3D (TOS 68) | 4D (TOS 68) |
|---|---|---|---|
| calendar ordinal, k=-3..+3 | 41, 51 only | 55, 60, 62 only | 65, 65, 67, 70 |
| **weekday (Mon-Fri) index, k=0..N-1** | **53\***, 57 | **68\***, 67, 64 | 64, 67, **68\***, 70 |
| trading-BAR index, k=0..N-1 | 53\*, 57 | 68\*, 67, 64 | 64, **68\***, 69, 70 |
| 6-day week (drop Sundays) | 44, 47 | 62, 65, 68\* | 65, 62, 68\*, 74 |
| 6-day week (drop Saturdays) | 44, 47 | 62, 65, 68\* | 65, 62, 68\*, 74 |
| chunks anchored on the NEWEST bar (trading) | 57 | 68\* | 70 |
| chunks anchored on the NEWEST bar (calendar) | 51 | 62 | 70 |
| reset each ISO week | 47 | 62 | 68\* |
| reset each calendar month (weekdays) | 51 | 67 | 68\* |
| reset each calendar month (calendar days) | 41 | 60 | 67 |
| **reset each 1 January (weekdays)** | **53\*** | **68\*** | **68\*** |
| forming (today's) bar dropped, all of the above | 30-44 | 42-59 | 49-56 |

Excluding the forming bar is decisively wrong for every span - TOS scans the current bar,
as the scan spec already says.

Three things the table decides:

1. **Calendar chunking is dead at EVERY phase.** 2D can only ever produce 41 or 51 and 3D
   only 55/60/62. No amount of tuning the 1969-12-30 constant reaches 53 or 68. This is
   why the fix changes units rather than the constant.
2. **Weekday counting beats trading-BAR counting.** Both reach 53 and 68, but bar-counting
   cannot do it with one anchor: all 753 possible anchor bars in the three-year CRWD tape
   were enumerated and NONE gives 2D=53, 3D=68 and 4D=68 together (2D needs an even
   anchor, 4D an odd one). Counting Mon-Fri - with market holidays counted as phantom days
   that carry no bar - is the only unit that admits a single consistent phase. It is also
   the only unit that keeps the key ABSOLUTE; a key derived from the bars present would
   repaint the whole history whenever the tape's depth changed.
3. **The phase is k = 6, and it is the only one.** Of every uniform offset k in 0..23 of
   `floor((weekday_index + k) / N)`, exactly k=6 and its period-12 repeat k=18 satisfy all
   three spans (12 = lcm(2,3,4)). Per-span phases were NOT needed - which matters, because
   three phases fitted to three numbers would have been a curve-fit.

### What is NOT pinned - re-check in Q1

One day of readings pins the phase only **modulo 12 weekdays**. A second model fits the
same measurement exactly: **restarting the weekday count at each 1 January**. For 2026 that
reset lands on weekday index 14610, which is 6 mod 12, so the two models are
indistinguishable for the whole of 2026 - the Skittles window reaches back at most ~64
weekdays (4D x 16 bars) and so never crosses a year boundary outside Q1. They DISAGREE in
2024 and 2025.

**If a January or February reading disagrees with TOS, try the year reset first.** Do not
start re-deriving the units; those are settled.

### Cross-check (not curve-fitting one ticker)

NVDA and AVGO were folded with the new rule on the same day. No absurd output, all values
in range, and the multi-day spans sit sensibly between D and Wk:

| symbol | D | 2D | 3D | 4D | Wk | M |
|---|---|---|---|---|---|---|
| NVDA | 30 | 62 | 75 | 74 | 74 | 60 |
| AVGO | 19 | 20 | 34 | 41 | 39 | 55 |

No TOS values exist for these, so this is a sanity check only.

### The scan was not disturbed

`momx.board.build_tapes` is the only caller of `aggregate_daily`, so the scan's OR-block A
(MACD/EMA crosses on 2D/3D/4D) moved with it. `build_board` was run over the seed universe
under both groupings on 2026-08-27: **the pass/fail set is identical for all ten symbols.**
Individual multi-day `scanReasons` do shift (e.g. NVDA `macd:3D` -> `macd:2D`), which is
expected and correct - the candles themselves changed - but no row flipped. The 15:40 ET
AVGO-only parity result above therefore still stands.

### Corroboration worth knowing

The weekday rule explains a hack the signal engine already needed. Under the calendar
phase, 2D emits an extra group start on Mondays (Fri+Sat, then Sun+Mon) and 3D/4D emit
spurious weekend groups - which is exactly what
`_tos_overnight_group_continues_from_prior_session()` exists to suppress. Under the weekday
rule those artefacts never appear. Over 2023-2026 the weekday rule's 2D group starts are a
strict SUBSET of the calendar rule's (479 of 479 shared; the calendar rule invents 95 more).
The signal engine's calendar phase plus its Sunday-continuation rule looks like a
reverse-engineering of weekday chunking from the outside.

That is a reason to suspect `ganesh_higher_timeframe_signals` has the same units bug in its
SIGNAL studies. **It was deliberately NOT changed** - it is validated against real TOS
charts for EMA/MACD crosses and is not this task's file. Flagged as an open item.

## The Monthly Skittles gap - NOT SOLVED, deliberately left open (2026-08-27)

M is the one column that still disagrees with thinkorswim. This section replaces the
earlier "Still open: M reads 76" placeholder. **The conclusion is that no principled
month definition closes the gap, and M stays the plain calendar month.**

### The measurement and the reproduction

Trader read CRWD off his watchlist, live, 2026-08-27, after BOTH the split-adjustment
fix and the weekday-chunking fix:

| span | ours | TOS | status |
|---|---|---|---|
| D | 31 | 31 | MATCH |
| 2D | 53 | 53 | MATCH |
| 3D | 68 | 68 | MATCH |
| 4D | 68 | 68 | MATCH |
| Wk | 71 | 71 | MATCH |
| M | 76 | 71 | **WRONG by +5** |

Reproduced end to end before anything was tried - `momx.feed.fetch_daily("CRWD", 3y)` ->
`buckets.aggregate_daily(bars, span)` -> `columns.skittles_cell` gives
**31 / 53 / 68 / 68 / 71 / 76**, all six exactly. Unrounded: D=31.08, 2D=52.53,
3D=67.97, 4D=68.30, Wk=71.02, M=75.51.

The calendar-month bars behind the 76 (split-adjusted, continuous):

    2025-06 h=127.51 l=111.32 c=127.33     2026-01 h=121.80 l=107.85 c=110.35
    2025-07 h=129.49 l=113.36 c=113.64     2026-02 h=111.81 l= 85.68 c= 93.00
    2025-08 h=114.45 l=102.31 c=105.93     2026-03 h=113.00 l= 90.45 c= 97.60
    2025-09 h=126.80 l=100.67 c=122.60     2026-04 h=116.99 l= 91.12 c=111.44
    2025-10 h=138.41 l=118.85 c=135.75     2026-05 h=182.87 l=111.39 c=182.75
    2025-11 h=141.73 l=119.39 c=127.29     2026-06 h=196.42 l=154.44 c=190.79
    2025-12 h=132.47 l=117.10 c=117.19     2026-07 h=217.50 l=174.14 c=190.86
                                           2026-08 h=229.08 l=181.24 c=227.96 (forming)

### Three things ruled out by construction, not by search

1. **History depth / WMA warmup.** FastD(8) of FastK(8) reads back exactly 15 bars, i.e.
   to 2025-06. Truncating the monthly series to 16, 20, 25 or all 37 bars gives
   **75.5138 every time**. A deeper tape than 3 years is arithmetically incapable of
   moving M. Candidate (e) is dead.
2. **Any single monthly bar's data.** Each of the 15 window bars was swept over
   0.25x-4x its high and its low. Not one *low* can reach 71 at any value. Every *high*
   that reaches 71 is 25-70% above what CRWD actually traded (2025-09 would need 207.6
   against an actual 126.80; 2026-01 would need 188.0 against 121.80; 2026-08 would need
   265.6). The gap is not a bad print in one month.
3. **Excluding the forming month.** The same move applied to the five spans that DO match
   destroys all five: D 31 -> 13, 2D 53 -> 44, 3D 68 -> 59, 4D 68 -> 60, Wk 71 -> 64.
   TOS demonstrably scans the current bar. Pinned by
   `test_dropping_the_forming_bar_breaks_every_span_that_matches_thinkorswim`.
   Note this also kills the plain "M without the forming month" reading on its own terms:
   that produces **65**, not 71.

### The full search - 361 candidate month definitions

Every candidate re-folded the live CRWD daily tape and re-ran `skittles_cell`. Values are
unrounded FastD / rounded. "incl" = forming bar kept (what TOS does); "excl" is shown only
to document that it was looked at. `*` = reaches 71.

| family | candidates | incl-forming range | best incl | reaches 71 incl? |
|---|---|---|---|---|
| a. calendar month (shipped) | 1 | 75.5 | 75.5 / **76** | no |
| b. N-weekday chunks, N=19..23, every phase k | 100 | 70.7 - 85.0 | N=22 k=3 -> 70.7 | 1 of 100 |
| c. N-trading-bar chunks, N=19..23, every k | 105 | 71.5 - 85.4 | N=23 k=6 -> 71.5 | 0 of 105 (rounds 72) |
| d. N-calendar-day chunks, N=28..31, every k | 118 | 70.4 - 85.0 | N=31 k=19/21 | 2 of 118 |
| e. 4-week and 5-week chunks, every k | 9 | 71.5 - 83.8 | 5wk k=0 -> 71.5 | 1 of 9 |
| f. month anchored on day-of-month 1..28 | 28 | 70.5 - 83.6 | dom 28 -> 70.5 | 1 of 28 |
| g. `OPT_EXP` (3rd Friday to 3rd Friday) | 2 | 78.0 - 78.6 | 78.6 / 79 | no |
| h. weekday count reset each 1 January, N=18..24 | 7 | 74.8 - 83.8 | 74.8 / 75 | no |
| i. 2 calendar months | 1 | 76.2 | 76.2 / 76 | no |
| j. calendar quarter | 1 | too few bars (13 < 16) | null | n/a |

Selected rows in full, because the negatives are the point:

| candidate | incl | excl |
|---|---|---|
| calendar month (shipped) | 75.5 / **76** | 65.1 / 65 |
| 20 weekdays, k=6 (the phase that fixed 2D/3D/4D) | 80.3 / 80 | 70.7 / 71 |
| 21 weekdays, k=6 | 74.8 / 75 | 64.4 / 64 |
| 22 weekdays, k=6 | 77.2 / 77 | 67.8 / 68 |
| 23 weekdays, k=6 | 72.8 / 73 | 63.2 / 63 |
| 18 / 19 / 24 weekdays, k=6 | 81.6 / 79.2 / 75.3 | 73.0 / 70.6 / 66.0 |
| OPT_EXP, bar opens ON the 3rd Friday | 78.6 / 79 | 69.5 / 70 |
| OPT_EXP, bar opens the day AFTER | 78.0 / 78 | 68.3 / 68 |
| 4-week chunks, k=0..3 | 83.8 / 80.1 / 79.7 / 77.5 | 75 / 71 / 71 / 67 |
| 22 weekdays, k=3 | 70.7 / **71** * | 59.8 / 60 |
| 31 calendar days, k=19 | 70.5 / **71** * | 59.7 / 60 |
| 31 calendar days, k=21 | 71.2 / **71** * | 60.7 / 61 |
| 5-week chunks, k=0 | 71.5 / **71** * | 62.4 / 62 |
| month anchored on the 28th | 70.5 / **71** * | 59.9 / 60 |

### Why none of the five hits is believed

**The null distribution.** Over 497 uniform-chunk candidates (weekday, trading-bar and
calendar-day units, every size and every phase), the incl-forming values run
**70.4 to 85.5, mean 78.7**. 71 sits on the extreme lower edge - only 8 of 497 (1.6%)
round to it, and each of those sits at a phase with no story. For contrast, 43 candidates
(8.7%) round to 75 and 57 (11.5%) round to 78. **71 is not a value this family reaches
naturally; it is a value a handful of arbitrary phases scrape.**

Excluding the forming bar inverts this: the excl distribution is 59.6-77.5, mean 69.6, and
**71 is its MODE - 55 of 497 candidates (11.1%) produce it.** That is why the excl column
looks so encouraging and is worth nothing: a target that half a hundred unrelated rules hit
is not evidence for any of them. And excl is independently ruled out (see above).

**No mechanism.** Each of the five incl hits requires a sentence nobody can finish:
"thinkorswim's Month is 22 weekdays offset by 3", "...31 calendar days offset by 21",
"...five ISO weeks starting on an arbitrary week", "...a month that begins on the 28th".
The 2D/3D/4D fix was accepted because it had one sentence - *TOS chunks weekdays, not
calendar days* - and because three independent targets pinned one constant. Here there is
**one** target and **two** free parameters (size and phase), so any of these is a fit, not
a finding.

**The 2D/3D/4D mechanism does not extend.** `floor((weekday_index + 6) / N)` - the exact
rule and phase now shipping for 2D/3D/4D - gives 82 / 79 / 80 / 75 / 77 / 73 / 75 for
N = 18..24. None is 71. Whatever TOS does for Month, it is not the multi-day rule with a
bigger N.

**The five hits disagree with each other elsewhere**, so they are not the same rule seen
five ways:

| candidate M rule | CRWD (TOS 71) | NVDA (no TOS) | AVGO (no TOS) |
|---|---|---|---|
| calendar month (shipped) | 76 | 60 | 55 |
| 22 weekdays, k=3 | 71 | 62 | 51 |
| 31 calendar days, k=19 | 71 | 60 | 51 |
| 31 calendar days, k=21 | 71 | 63 | 53 |
| 5-week chunks, k=0 | 71 | 65 | 55 |
| month anchored on the 28th | 71 | 60 | 51 |

### Conclusion and the recommended next step

**M is left as a documented 5-point gap.** `momx/buckets.py` still routes `M` to
`ganesh_higher_timeframe_signals._timeframe_group_key`, i.e. the plain calendar month,
which is what thinkorswim's MONTH aggregation is documented to be and what the other five
spans' agreement implies the Skittles maths is correct on.

The competing explanation remains the one the earlier note raised and this search did not
dislodge: **TOS's M cell reading of 71 is identical to TOS's Wk cell reading of 71**, our
Wk reproduces 71.02 exactly, and the two columns are adjacent on a dense watchlist. A
mis-read row produces precisely this signature - one column off, five neighbours perfect,
and no rule in 361 that explains it.

**The cheap discriminating experiment (do this before anyone rebuilds monthly grouping):**
ask the trader for the **NVDA** Skittles M and Wk cells. Our NVDA Wk is 74 and our NVDA M
is 60 - fourteen points apart, so a mis-read cannot hide there, and the five 71-hitting
candidates spread across 60/62/63/65 while the shipped calendar month says 60. One NVDA
reading separates all of them. AVGO works too (Wk 39, M 55).

Do NOT hard-code 71, and do not adopt any of the five fitted rules on the strength of one
cell.

### Where the evidence lives

- Pins: `tests/test_momx_buckets.py`, the four tests under
  "The MONTHLY Skittles gap", including the frozen `CRWD_MONTHLY_BARS_2026_08_27`.
- Search scripts: `artifacts/momx_m/` (`search.py` the 361-candidate sweep, `search2.py`
  the depth/mechanism checks, `nulldist.py` the 497-candidate null distribution,
  `sens2.py` the single-bar sensitivity, `optexp.py`, `crosscheck.py`).

## After-hours makes TOS comparison meaningless (measured 2026-08-27 ~17:30 ET)

**Do not diagnose the scanner against a TOS screenshot taken after 17:00 ET.**

The scan is a CURRENT-BAR snapshot, and TOS anchors 4h buckets at 01/05/09/13/17
ET. From 17:00 the forming 4h bucket holds only thin after-hours volume, while
"2 buckets ago" is the 09:00 morning session. The volume gate
(`Price_Change(VOLUME) >= 0.5% greater than 2 bars ago`, 4h EXT) therefore fails
for essentially every symbol:

| symbol | 4h 09:00 | 4h 13:00 | 4h 17:00 (forming) | forming vs 2-back |
|---|---|---|---|---|
| AAPL | 14,665,969 | 16,553,985 | 24,664 | -99.8% |
| TXN  |  1,779,840 |  3,011,749 |    140 | -100.0% |
| DDOG |  2,281,486 |  2,084,152 |    115 | -100.0% |
| ARQQ |    115,170 |     60,595 |  1,273 | -98.9% |

This is NOT degenerate during the trading day: at 09:00 the comparison is against
the 01:00 overnight bucket, which premarket volume clears easily.

**Rejected fix - reading bar0 as the last CLOSED bucket.** It makes all four pass
by +4,000% to +94,000%, i.e. it does not repair the gate, it deletes it. One
target, two free parameters, fitted to an after-hours artifact. Same trap the
Monthly Skittles search documents and refuses.

**Consequence for verification.** Three counts from one evening - TOS 16 (~17:29),
ours 19 (~17:05) and ours 11 (~18:00) - are not comparable to each other, and
neither is any pair. The 12-of-12 agreement that confirmed the rank_board fix held
precisely because those runs were minutes apart.

**The only valid parity check is SIMULTANEOUS and DURING MARKET HOURS**: the TOS
scan and the MomX board read within the same minute, between 09:30 and 16:00 ET.
Until that has been done once, treat every row-set difference as unexplained
rather than as a bug.

## RESOLVED 2026-09-03: the 4h volume gate was one bucket out of alignment

**The fix: `VOLUME_CHANGE_BARS_AGO = 3`, and the 17:00/21:00 workaround deleted.**

Settled by ground truth. He ran his TOS scan twice and sent the results:

    18:15 ET -> 10 matches: FDX GS CHTR TMO ABVX MS USB NEE WELL REGN
    18:21 ET ->  9 matches: FDX GS      TMO ABVX MS USB NEE WELL REGN

The shipped reading (`volume[2]`) rejected WELL and REGN in BOTH, while TOS
matched them. Sweeping bars-ago x session x forming-bar, exactly one
combination reproduces both readings - and it also correctly rejects the CHTR
that TOS dropped between them:

| reading | bars_ago = 2 | bars_ago = 3 |
|---|---|---|
| 18:15 (10 symbols) | 8/10 | **10/10** |
| 18:21 (9 symbols) | 7/9 | **9/9** |
| CHTR, dropped by TOS | rejected | **rejected** |

19 positives and one true negative, zero errors, two independent readings.

**Cause.** Our 4h tape builds a bucket wherever bars exist, so a thin overnight
window becomes a full bar and our series carries ONE MORE bucket per day than
his. "2 bars ago" therefore lands on a 102-share overnight bucket for a symbol
that traded overnight (passing by a million percent) and on a session-sized
bucket for one that did not (failing). One extra bucket is exactly one index of
offset - which is what the sweep found, and it explains why the error was
symbol-dependent and looked like noise.

**The CLOSE row is NOT shifted.** Measured on the same 129-symbol sample:
shifting it too gives 7/9 of his matches and 14 rows, against 8/9 and 12 at 2.
The offset is a property of the 4h bucket series; the 1h series does not share
it. Recorded rather than explained.

**Full-scan validation**, 129 symbols (his 9 plus 120 random):

| config | matches | of his 9 |
|---|---|---|
| 2 back + evening skip (was shipped) | 14 | 6/9 |
| 2 back, no skip | 6 | 6/9 |
| **3 back, no skip** | **12** | **8/9** |

Better on both axes: more of his real matches AND fewer total rows than what
was shipped. That is also the answer to "our app shows more than TOS" - the
skip was passing symbols the gate should have caught.

**The dead window was never real.** TOS returned 9-10 matches in the evening
WITH this gate applied, which proves the blackout was our alignment and not the
gate. b0b9f90's VOLUME_GATE_BLIND_BUCKET_HOURS has been deleted rather than
kept beside the real fix.

The original investigation, preserved because its measurements are what made
the diagnosis possible:

## (historical) OPEN DEFECT: the 4h volume gate has an 8-hour dead window

Measured 2026-08-27 on COMPLETE 4h buckets (not just the forming one), 5 symbols,
~30 days. Pass-rate of `Price_Change(VOLUME) >= 0.5% greater than 2 bars ago`
(4h, EXT) by bucket-open hour:

| bucket (ET) | tested | passed | rate |
|---|---|---|---|
| 01:00 | 105 | 36 | 34.3% |
| 05:00 | 114 | 104 | 91.2% |
| 09:00 | 115 | 114 | 99.1% |
| 13:00 | 115 | 115 | 100.0% |
| **17:00** | **126** | **0** | **0.0%** |
| 21:00 | 95 | 1 | 1.1% |

Because this is an AND gate, **the scanner can return nothing at all between
about 17:00 and 05:00 ET.** After-hours volume is always far below the 09:00
morning session two buckets back.

**The contradiction:** thinkorswim returned 16 matches at 17:29 ET on the same
day, inside that dead window. A gate that cannot pass while TOS is passing 16
symbols means this reading of `Price_Change(VOLUME)` is wrong somewhere.

**Tested and REJECTED - it is not the bucket anchor.** Re-aggregating with every
4h anchor and re-measuring the 16:00-20:00 window:

| anchor (ET) | rate in 16:00-20:00 |
|---|---|
| 01/05/09/13/17/21 (current) | 0.0% |
| 00/04/08/12/16/20 | 17.3% |
| 02/06/10/14/18/22 | 0.0% |
| 03/07/11/15/19/23 | 0.0% |
| 04/08/12/16/20/00 | 17.3% |

No anchor comes close to explaining 16 matches, and changing it would break the
2h/4h chart parity that IS validated. Left alone.

**Also rejected: reading bar0 as the last CLOSED bucket.** It makes every tested
symbol pass by +4,000% to +94,000% - that deletes the gate rather than repairing
it.

### MITIGATED 2026-09-03 (the cause is still unknown)

The formula is UNCHANGED. What changed is that the gate is no longer allowed to
be the thing that returns an empty board: it is skipped in the 17:00 and 21:00
buckets, the two the table above measured at 0.0% and 1.1%.

The argument is the table itself. A gate that rejects 0 of 126 and 1 of 95 is
not discriminating between symbols - it is switching the scanner off - so
removing it there cannot let through anything it was meaningfully keeping out.
The 01:00 bucket (34.3%) still discriminates and is left alone.

Confirmed independently against the 30-day archive the same day: of 27,375
recorded matches over four days, 2.3% fall in 17:00-05:00 ET, and the 18:00,
20:00 and 23:00 hours contain none at all.

See `momx.scan.VOLUME_GATE_BLIND_BUCKET_HOURS` and `_volume_gate_applies`.
The bucket hour is read from the newest 4h bar's OWN timestamp, never the wall
clock.

**This is a blast-radius fix, not an explanation.** It makes no claim about
what `Price_Change(VOLUME)` really computes. When the thinkScript arrives,
DELETE the window logic rather than building on it.

### 2026-09-03 EVENING: first GROUND-TRUTH falsification, and one candidate

He ran his TOS scan at ~18:15 ET and sent the result: **10 matches** - FDX, GS,
CHTR, TMO, ABVX, MS, USB, NEE, WELL, REGN. That is the first time this defect
has had a symbol-level ground truth rather than a count.

**Our reading is PROVABLY wrong.** Applied to those exact ten:

    FDX  +1326222%  GS  +38772%  CHTR +272645%  TMO   +15.3%  ABVX +2345%
    MS     +61218%  USB +45176%  NEE  +301193%  WELL   -9.7%  REGN  -44.5%
                                                 ^^^^^^^^^^^^^^^^^^^^^^^^
    8/10 pass. WELL and REGN FAIL our gate while TOS matches them.

**The mechanism is now visible.** For eight of the ten, "2 bars ago" resolves
to a near-EMPTY overnight bucket (102, 842, 2210, 3676, 6912, 2030 shares), so
they clear 0.5% by five or six orders of magnitude. For WELL and REGN it
resolves to a SESSION-sized bucket (1.85M, 484k). We skip empty 4h windows, so
"2 bars ago" means a different time of day depending on whether the symbol
traded overnight. That is the bucket-ALIGNMENT problem scan.py suspected,
finally observed directly.

**Refuted this evening, each by measurement against the ten:**

| reading | result |
|---|---|
| drop zero-volume bars before aggregating | identical to shipped (structurally: an empty window never becomes a bucket) |
| empty 4h windows filled with volume 0 | 7/10 |
| regular session only | 3/10 |
| regular session + filled | 0/10 |
| bar0 = last CLOSED bucket | 6/10 |

**One candidate fits, and is NOT yet shipped.** Sweeping (bars-ago x session x
forming-bar), exactly one combination reproduces the ten:

    bars_ago = 3, all EXT bars, live/forming bar   ->  10/10

i.e. comparing against `volume[3]`, not `volume[2]`, for a row whose wizard
text reads "2 bars ago". At its own 26% base rate the chance of 10/10 by luck
is ~1 in 700,000, so it is genuinely correlated rather than merely permissive.

**Why it was NOT adopted:** specificity got worse, 7% -> 26% false positives on
110 other sampled symbols, and our board already returns 30+ where TOS returns
10. Adopting it widens the gap. Ten observations is not enough to change the
scan's core comparison.

**Also learned:** TOS returns 10 in the evening WITH this gate applied, so the
gate is not inherently an 8-hour blackout - our alignment is. The
VOLUME_GATE_BLIND_BUCKET_HOURS mitigation is therefore a symptom fix and
should be REMOVED once alignment is settled, not kept alongside it.

**What settles it:** a second symbol-level TOS reading at a different time of
day. If bars_ago=3 reproduces that one too, it stops being a coincidence.

**Still unexplained. Do not guess a fix.** What would settle it, cheapest first:
1. The exact thinkScript behind the `Price_Change` scan row (click the pencil on
   that row in the Stock Hacker and read it).
2. A SIMULTANEOUS TOS + MomX reading during market hours, which also validates
   the row set (see the after-hours caveat above).

Practical impact today: the board is trustworthy 05:00-17:00 ET and empty by
construction outside it. That is a real limitation, not a data outage.

## The incremental feed store (2026-08-28) - why a refresh is seconds, not minutes

Before this, EVERY build re-downloaded full depth: ~30 days of 30-minute bars for
358 symbols is ~600 Alpaca pages, measured 468s, and since the warmer idles at
least as long as the last build took, the big list refreshed every ~16 minutes.
Between two builds only ~15 minutes of NEW bars exist.

`momx/feed.py` now keeps an in-process store per tape kind (`5m`/`30m`/`daily`):
`{symbol: last assembled tape}` plus a `full_built_at` wall-clock stamp.

- **First build** (or a symbol with no held tape - new to the universe, or its
  earlier full fetch failed): full depth, exactly as before.
- **Later builds**: fetch only the TAIL - from the oldest newest-held bar across
  the batch, minus `TAIL_OVERLAP_BARS` (2) bar-spans plus the feed's recency
  delay, so the previously-partial last bar is refetched complete. The tail
  REPLACES the held bars at/after its earliest stamp. Same resolution on both
  sides, so this is NOT the cross-resolution case `merge_live_tail` guards.
- A symbol whose tail fetch fails keeps its held tape unchanged and is NOT put
  in `errors` (a stale tape beats a blank board). A symbol with no held tape
  whose full fetch fails errors exactly as before.
- Symbols idle past `STORE_PRUNE_SECONDS` (15 min) are dropped. Idle-time
  pruning, not "not in this call's universe", because Mag7 and Watchlist
  alternate on one warmer thread and would otherwise evict each other.

**THE SPLIT HEAL.** Bars are fetched `adjustment=split`, and a split restates
the provider's ENTIRE history retroactively. An incremental store that appended
new-adjustment tails to old-adjustment history would rebuild exactly the CRWD
4:1 corruption above (weekly Skittles 25 vs TOS 71). Therefore a full rebuild of
each kind is FORCED once `full_built_at` is older than `FULL_REBUILD_SECONDS`
(12h). Splits take effect before the premarket, so the first build after
04:00 ET is always a fresh full build. `clear_cache()` drops the store too.
Pinned by `test_a_store_older_than_the_full_rebuild_window_forces_a_full_fetch`.

The public API is unchanged (`fetch_5m/30m/daily`, `FeedResult`, TTL cache
semantics); the incremental behaviour is internal. Cost: the store holds
~358 x 3 tapes resident - tens of MB, accepted. The parity guarantee - an
incrementally spliced tape is identical to one full fetch of the same data - is
pinned by `test_the_second_build_fetches_only_the_tail_and_matches_a_full_fetch`.

Measured 2026-08-28 ~01:45 ET (overnight session, 357-symbol Watchlist, 30m
tape): full fetch 96.1s, within-TTL 0.002s, incremental refetch 3.1s. The
full-fetch figure is far below the daytime 468s because overnight pages carry
little volume; the incremental figure is the one that holds around the clock -
its window is minutes deep regardless of session. With tail fetches this fast,
the warmer's existing ``max(60s, build time)`` idle makes the whole-list cycle
~60s with no change to ``momx/service.py``.

## The IEX live-tail merge (2026-08-28) - why the board is ~6 min stale, not ~24

The board scans STALE bars because Alpaca's free SIP feed hard-403s on any bar
newer than ~15-20 minutes (`FEED_RECENT_DELAY_MINUTES["sip"] = 20`). Measured
live 2026-08-28 13:24 ET:

| feed | newest 5m bar | age | newest-bar volume |
|---|---|---|---|
| SIP (free) | 13:10 ET | 16 min | 196,191 (full consolidated) |
| IEX | 13:20 ET | 6 min | 11,658 (IEX-only, ~6% thin) |
| our board (SIP + bucketing) | ~13:00 ET | ~24 min | - |
| thinkorswim | live | ~0 min | - |

SIP has the recency block; IEX has NONE but its volume is IEX-only. So the fix
is a HYBRID, applied to the INTRADAY tapes ONLY (5m and 30m; the daily tape is
regular-hours and unaffected):

- After the existing SIP(+BOATS) fetch, `momx/feed.py` fetches a SHORT IEX tail
  (`IEX_TAIL_MINUTES` = 30, `feed=iex`, same timeframe, `adjustment=split`). IEX
  has no recency block, so its `end` is clamped by only `IEX_TAIL_END_CLAMP_MINUTES`
  = 1 (just the still-forming minute), not SIP's 20.
- MERGE (`_merge_iex_tail`): only IEX bars STRICTLY NEWER than the newest SIP bar
  are appended. SIP always wins on overlap - its volume is the complete one; only
  the bars SIP does not have yet come from IEX. Each appended bar is stamped
  `feed="iex"` (and the SIP body `feed="sip"`) so downstream can tell the thin tail
  from the settled body.
- BEST-EFFORT: an IEX leg that fails, errors, or returns nothing serves the SIP
  tape UNCHANGED (same frame object). It can never make the board worse than the
  SIP-only board. One request per intraday kind per build, batched exactly like the
  SIP legs - never a per-symbol fetch, so the board does not slow down.

**Freshness bought: PRICE-based signals go from ~24 min stale to ~6 min** - the
EMA/MACD crosses, Skittles, High/Low, %change, and the 1h close price-change gate.

**Explicit VOLUME caveat (NOT hidden):** the newest IEX-sourced bars carry
IEX-only volume (~6% of consolidated), so any VOLUME-based reading on those bars
is approximate - RVOL and the 4h volume price-change gate especially. Those go
back to exact figures the moment SIP's recency block clears for that bar.

**The thin bar never sticks.** The incremental store caches the merged (SIP+IEX)
tape, so the next tail fetch correctly extends from the newest held bar even when
it is an IEX bar. Once SIP catches up to a timestamp `T` the IEX tail filled, the
next build's `_splice_tail` drops the thin IEX bar at `T` and re-owns it from SIP
(keep="last", the real volume wins) - the bar is upgraded `iex -> sip`. Pinned by
`test_a_thin_iex_bar_is_upgraded_to_the_real_sip_bar_when_sip_catches_up` in
`tests/test_momx_feed.py`.
