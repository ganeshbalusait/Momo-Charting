# MomX BEAR scanner — the bull scanner mirrored for puts

**Date:** 2026-09-24 (approved in chat 2026-09-25 00:35 ET)
**Status:** approved — implementation follows immediately
**Origin:** "Lets build 'bear' scanner?" … "No, mirror the bull scan." The
trader has no TOS bear scan to port; every bear rule below is the exact flip of
the bull rule that already ships (`docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md`
and the strategy notes in memory). He trades short-dated options, so the bear
side is for **puts**.

**What "A+" means on the bear side:** the defined bearish conditions align. It
is not a recommendation and does not guarantee profit. Same wording as bull.

## 1. Product shape

- The `BULL` label on the scanner toolbar becomes a **BULL ▲ / BEAR ▼ switch**,
  remembered per device (`localStorage` `momx.scanner.direction`, default bull).
- Bear is a **second view of the same ticker lists**, not a new list. Lists,
  Columns manager, Filters, History, NEWS, sector strip, SOLO all work in both.
- Bear boards rank by `% change` **ascending** (biggest losers first) — the
  order a TOS bear scan would give. Scan matches still come first, never dropped
  (`scan.rank_board` contract unchanged).
- Everything the screen reads from a row (`scanPass`, `scanReasons`, `grade`,
  `m5`, `strategy`, `chartSignals`) keeps its **name**; on a bear board its
  **value** is the bear one. This is what keeps the panel change small.

## 2. Architecture — one build, two boards

Fetching bars and painting the columns (`columns.build_row`) is ~88% of a build.
That is done **once per list**. Each row is then scored twice.

```
board._scan_one(symbol)
    tapes -> columns.build_row(...)               once   (unchanged)
    scan.scan_symbol(tapes, last, "bull")         -> scanPass / scanReasons
    scan.scan_symbol(tapes, last, "bear")         -> row["bear"]["scanPass"/"scanReasons"]
    grade.grade_row(row, now, "bull"/"bear")      -> grade / row["bear"]["grade"]
    momentum.summarize(bars5m, rvol, direction)   -> m5   / row["bear"]["m5"]
board.build_board(...)                            -> bull payload (as today) with
                                                     row["bear"] carried on rows+rest
board.as_direction(payload, "bear")               -> bear payload: shallow row copies
                                                     with bear fields promoted, bear
                                                     fields removed from the copy,
                                                     re-ranked ascending by pctChange
service._build_once(name)
    bull payload -> the existing books (unchanged)
    bear payload -> the BEAR books (own instances, own root dir)
    both published under _LOCK; snapshot(name, direction) serves either
```

- `board.cached_board` key gains nothing: one cached build carries both. The
  disk cache (`momx_board_cache/<list>.json`) stores the bull payload with
  `row["bear"]` on the rows, so a restart restores both views.
- `row["bear"]` is stripped from the **bull** payload before publish (so the
  bull wire format and History archive are byte-identical to today) and the
  bear payload never carries a `bear` key either. The disk cache is the only
  place both live together.
- `momx_worker.py` routes: `?dir=bear` (default `bull`) on `/api/momx-scanner`,
  `/momentum`, `/history`, `/history-query`, `/grade-tape`, `/grade-record`,
  and `POST /rebuild`. Unknown values fall back to bull.

## 3. The bear rules (each the exact flip of the bull rule)

### 3.1 Scan — which rows match (`momx/scan.py`, `direction` argument)

| Gate / trigger | Bull (as shipped) | Bear |
|---|---|---|
| Price floor | last ≥ 3.00 | same |
| 4h VOLUME change | ≥ +0.5% vs 3 bars ago | same (direction-neutral) |
| 1h CLOSE change | ≥ +0.3% vs 2 bars ago | ≤ −0.3% vs 2 bars ago |
| Momentum cross (2h,4h,D,2D,3D,4D,Wk,M) | MACD / EMA9-20 / EMA4-8 `_crossed_above` | same pairs, `ganesh_higher_timeframe_signals._crossed_below` (already used by the engine's PUT families) |
| SqzFired scanner (2h,4h,D,Wk) | medium squeeze released, momentum **rising** latch, `high > close[2]` | released, momentum **falling** latch, `low < close[2]` |
| RVOL scanner (5m…D) | z ≥ num_dev and `buying > selling` | z ≥ num_dev and `selling > buying` |

`scanReasons` strings are unchanged in form (`"macd:4h"`, `"rvol:5m"`,
`"blocked:..."`).

### 3.2 Grade (`momx/grade.py`, `direction` argument)

| Check | Bull | Bear |
|---|---|---|
| SQZ OK | any of 4h/D/W bg `cyan`, or none bg `orange` | any of 4h/D/W bg `magenta`, or none bg `orange` |
| Push | RVOL bg `cyan`/`green` on 5m…4h, or news ≤ 12 h | RVOL bg `magenta`/`red` on 5m…4h, or news ≤ 12 h |
| SKIT count | fg `cyan`/`dark_green` or bg `cyan`/`green`/`lime` | fg `magenta`/`plum` or bg `magenta`/`red`/`light_red` |
| Location | H/L bg `green` | H/L bg `red` |

Letters A+ (≥7 + location), A (≥6), B (≥5) unchanged. **bg** `plum` (the bare
`elif macd21d:` branch of `columns.skittles_cell` — MACD crossed down while
EMA9 is NOT below EMA20) is **excluded** from the bear cross set for the same
reason bg `dark_green` was removed from the bull set on 2026-09-22 — it is a
cross against the prevailing EMA state. **fg** `plum` is a different thing:
EMA9 < EMA20 and FastD ≤ 10, the exact mirror of fg `dark_green`, so it counts
as bear trend. Bear crosses `magenta` (9x20 down), `red` (MACD down with EMA9
< EMA20) and `light_red` (4x8 down with EMA9 < EMA20) all require the bear EMA
state, mirroring `cyan`/`green`/`lime`.
Reason strings flip wording: "below 16h midpoint" is the bear pass.

### 3.3 Momentum now (`momx/momentum.py`, `direction` argument)

| Field | Bull | Bear |
|---|---|---|
| Trigger | highest high of last 6 completed 5m | lowest low of last 6 completed 5m |
| 5m chart | breakout_* / holding / failed / below | same state names, comparisons flipped (`close < trigger`); the "no breakout" state is `above` |
| Building | close in top third + bullish RVOL 5m/15m | close in bottom third + bearish RVOL (`magenta`/`red`) |
| Fading | 2 closes in lower half, RVOL z < 1 | 2 closes in upper half, RVOL z < 1 |
| Extended | price > EMA20 + 2·ATR | price < EMA20 − 2·ATR |
| Explosive | z ≥ 3 bar closing in upper half | closing in lower half |
| Steady | ≥4/5 higher highs and higher lows, close > EMA20 | lower highs and lower lows, close < EMA20 |
| crossUpAt | 9 crossed above 20 today | `crossDownAt`: 9 crossed below 20 today (chart PUT5 / P5) |
| gapGo | gap ≥ +2%, first bar 09:35–10:25 closing above VWAP, open, orHigh | gap ≤ −2%, closing below VWAP, open, orLow (`orLow` field) |
| vwap, todVol | | unchanged (neutral) |

### 3.4 Strategies

- `strategy.evaluate(row, now, direction)`: V2 = A+/A, ≥09:35, `pctChange > −10`
  (bear), `m5.state == "extended"` (bear-extended), push RVOL (bear colours). V3
  adds SQZ fired (magenta). Daily 2 unchanged in mechanics. These are legacy /
  hidden filter values; kept for parity and recorded.
- **Strategy G bear** (`momx/strategy_g.py`, `direction`): rule 1 RVOL bg
  `magenta` and falling; rule 2 SQZ ok (bear set); rule 3 Skittles 2h/4h/D bg
  `magenta`; rule 4 `close < ema20 and close < vwap`, or `m5.crossDownAt`;
  rule 5 `option_plan(chain, "bear")` — **puts**: strikes below spot, ENTRY =
  put with |delta| closest under 0.20, target = nearest put wall (≥ 66% of the
  max put OI **below** spot and **above** the ENTRY strike), ROI = target-strike
  put price / ENTRY price ≥ 150%. Tracking: target hit when `last <= target`;
  option −50% and 15:55 exits unchanged.
- **Best setups (frontend `momxFilters.js`, direction argument):**
  GO bear = A+/A + `m5.gapGo.goAt` (bear gapGo) + `sellers30` (30m −DI > +DI).
  OPT bear = A+/A + Skittles 2h and 4h bg in the bear cross set + 30m −DI > +DI
  + `m5.state == "extended"`; early latch before 10:00 ET in
  `momx-opt-latch-bear-v1`.

### 3.5 Chart arrows on the row (`momx/chart_signals.py`)

`today_calls` becomes `today_signals(payload, day, direction)`: bear keeps
`direction == "PUT"` and labels starting with `PUT` (PUT2H / PUT4H). Cache file
per direction (`<root>/momx_chart_signals/<day>.json` under the bear root).

## 4. Recording and proof — separate roots

```
artifacts/                         bull (unchanged)
  momx_grade_tape/<Board>/…
  momx_grade_events/<Board>/…
  momx_grade_record.json
  momx_strategy/<day>.json
  momx_chart_signals/<day>.json
  momx_history/<Board>/…
  momx_board_cache/<list>.json      (carries row["bear"])
artifacts/bear/                    bear (new; AGX_MOMX_GRADE_DIR/bear in tests)
  momx_grade_tape/…  momx_grade_events/…  momx_grade_record.json
  momx_strategy/…    momx_chart_signals/…  momx_history/<Board>/…
```

- `grade_log.build_record` walks every board folder under its root and the
  scheduled bull scorecard globs `artifacts/momx_grade_events/*/<day>.json`.
  A shared folder would count bear A+ events as **long** trades. The separate
  root is what keeps both track records honest. No bull file moves.
- `GradeLog(root, direction)`: bear colour sets for the freshness timeline;
  outcomes are scored **in the trade's favour**: `pct = −(price_t/price_0 − 1)`
  so `pctHigher*` keeps its meaning of "went your way"; `maxFav` = lowest low,
  `maxAdv` = highest high; `byAdx` keys on `"bear"` crosses. The record carries
  `"direction": "bear"` and the frontend line says **"closed lower"**.
- Bear has no History archive yet, so the track record starts empty and fills
  from the first recorded day. `scripts/momx_grade_backtest.py --direction bear`
  writes `artifacts/bear/momx_grade_record_backtest.json` and can be run once
  bear History exists.
- Strategy G bear day lists: `artifacts/bear/momx_strategy/<day>.json`.
- The scheduled `momx-strategy-scorecard` task gets a bear run (short-side
  simulate, bear root) as a **follow-up** after the first bear events exist —
  not part of this build.

## 5. Screen

- **Toggle:** `BULL ▲ / BEAR ▼` in the toolbar where the BULL label sits; the
  panel header, SCAN button title and empty-state text name the direction.
- **Endpoints:** every fetch the panel makes for the board, momentum, history,
  grade tape and grade record carries `&dir=bear` in bear mode. A direction
  switch clears the in-memory board and refetches (no stale bull rows under a
  bear header).
- **Palette:** bear letters A+ `#ff4d6d`-class bright red, A red, B grey
  (`is-aplus-bear` / `is-a-bear` tones in `momxGrade.js` + `index.css`);
  momentum arrow: Building ↓ red, Fading ↑ green-grey. Setup wording flips:
  "breakdown confirmed", "below 16h midpoint", "PUT5".
- **Filters:** RVOL "Bullish only" checkbox reads "Bearish only" in bear mode
  and tests the bearish RVOL backgrounds. Setup dropdown labels unchanged;
  `STRATEGY_RULES` help text has a bear variant.
- **Chart:** `scannerGradeForBubble` decorates **PUT** labels from the bear
  tape (`grade-tape?…&dir=bear`), red circles `#ef4444` / `#fca5a5`,
  `position: aboveBar`; CALL bubbles unchanged.
- **Momo phone alerts:** `momx_worker._momo_evaluate` also evaluates the bear
  boards; alert text carries `▼` and `list` reads `"<Board> BEAR"`. The
  momo-config thresholds are shared.
- **Release notes:** one entry at the top of `frontend/public/release-notes.json`.

## 6. Verification (AGENTS.md checklist applies)

- `tests/test_momx_scan.py`, `test_momx_grade.py`, `test_momx_momentum.py`,
  `test_momx_strategy*.py`, `test_momx_chart_signals.py`, `test_momx_grade_log.py`,
  `test_momx_board.py`, `test_momx_service.py`: mirrored fixtures — a bear tape
  built by negating the bull fixture's moves must match/grade exactly where the
  bull one does; the bull assertions are untouched.
- **Bull unchanged:** `artifacts/momx_parallel_frozen.py`-style diff of the
  bull payload before/after on frozen tapes = 0 field differences.
- Frontend `node --test src/*.test.js` (filters, grade tones, gradeMarkers),
  `npm run build`, browser smoke on :5173 at desktop and 375 px: toggle, scan,
  Filters "Best setups", History in bear mode, chart PUT bubbles.
- Worker: `/api/momx-scanner/status` reports bear tape writes on the first live
  session; bull tape counts unchanged.

## 7. Out of scope

- A dedicated bear ticker list, a TOS "Bear Momo" port, bear AI news direction
  weighting, the scheduled scorecard's bear run (follow-up), option-priced
  outcomes.
