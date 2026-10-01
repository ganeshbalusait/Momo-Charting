# TradingView-Smoothness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: use superpowers:executing-plans or
> superpowers:subagent-driven-development. Steps use `- [ ]` for tracking.
> **The trader is not a developer. Do not ask them to run commands, edit files,
> or choose between technical options. Execute, verify, and report outcomes.**

**Goal:** Make the chart feel like TradingView — no shaking, no freeze while
indicators load, fast ticker switches.

**Why the chart is not smooth today (measured 2026-08-13, not assumed):**

| Measured | Value |
|---|---|
| Payload per symbol | 4.5 MB — 26,320 1-min bars + 11,885 study bars + 3,587 dailies |
| 4h aggregation | browser buckets 26,320 bars → 1,105 candles, on every load |
| Toggling one indicator | destroys and rebuilds the ENTIRE Lightweight Charts tree |
| Study activation | staged one slice per idle frame — a workaround for main-thread freezes |

The staged ladder is the tell: it exists because doing the work at once froze
the browser. It is also the direct cause of the shaking — each stage rebuilds
the tree.

## Global constraints

- Project root: `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`.
- Python is `./.venv/Scripts/python.exe`. Frontend tests: `node --test src/*.test.js` from `frontend/`.
- Git is **local-only**; do not add remotes. Commit with **explicit paths** (`.venv` is tracked).
- A concurrent session edits `App.jsx`/`index.css`. Re-read regions immediately before editing and commit only your own hunks.
- Backend changes need a restart: **`Stop-ScheduledTask` does NOT restart it** — stop the api_server parent PID and the watchdog respawns it (~20-30s).
- Verify with `node scripts/chart_sweep.mjs --all --pace=1200` before and after. Never bulk-hit chart endpoints during market hours.

---

### Task 1: Toggling an indicator must not rebuild the chart

Highest value, most contained. Fixes the shaking, removes the need for the
staged ladder, and eliminates a class of viewport bugs.

The chart-creation effect (`frontend/src/App.jsx`, deps array near the end of
the `createChart` effect) lists ~20 indicator settings and colours. React tears
down and rebuilds the whole tree when any changes.

- [x] **Step 1: Prove the rebuild count before changing anything.**
  Instrument by wrapping `createChart` to increment `document.body.dataset.chartBuilds`
  (page `window.*` globals are invisible to the browser tool; `document.body.dataset` is not).
  Open `http://127.0.0.1:5173/?popout=chart&symbol=NVDA&timeframe=5m`, toggle three
  indicators, record the count. Expect one build per toggle. **The tab must be
  VISIBLE** — hidden tabs throttle timers and produced two false "renderer froze"
  conclusions on 2026-08-13.

- [x] **Step 2: Move colours off the rebuild path.**
  Remove the colour deps (`ema9Color`, `ema21Color`, `ema50Color`, `sma200Color`,
  `vwapColor`, and every `personsPivots*` colour) from the effect. Add a separate
  effect that calls `applyOptions({ color })` on the existing series refs when a
  colour changes. Changing a line from cyan to magenta must never destroy a chart.

- [x] **Step 3: Move visibility toggles off the rebuild path.**
  Same treatment for `indicatorSettings.*` booleans: create the series once and
  toggle `applyOptions({ visible })`, or add/remove that one series, instead of
  rebuilding the tree.

- [x] **Step 4: Re-run Step 1's measurement.** Toggling an indicator must produce
  **zero** new chart builds. Record before/after numbers in the commit message.

- [ ] **Step 5: Confirm the shake is gone** on a visible, foreground window:
  open a chart with indicators on and maximize it. If it still shakes, the
  remaining suspects are in `memory/chart-maximize-shaking-open.md` — the
  viewport-refit sites are already RULED OUT by measurement; do not re-investigate them.

- [x] **Step 6:** `node --test src/*.test.js`, `npm run build`, commit.

**Task 1 outcome (2026-08-14, commit `6874543`)** — measured on NVDA 5m via
`document.body.dataset.chartBuilds`:

| Action | Rebuilds before | after |
|---|---|---|
| EMA Clouds toggle | 1 | **0** |
| Persons Pivots timeframe checkbox | 1 | **0** |
| EMA 9 line colour | 1 | **0** |
| Ichimoku toggle (control, never a dep) | 0 | 0 |
| MTF Squeeze 4/10 toggle | 1 | 1 (deliberate) |

The creation effect went from 21 dependencies to 6. Colour changes were
verified to actually reach the series (setting EMA 9 to `#ffcc00` reported
`#ffcc00` back off the live series), not merely to skip the rebuild.

`indicatorSettings.mtfSqueeze410Lower` is the one toggle still on the rebuild
path: it adds or drops a whole third pane rather than hiding a series, and
creating that pane unconditionally would leave every chart with a permanent
empty slice. Fixing it needs `chart.addSeries(..., 2)` / `chart.removePane(2)`
in a dedicated effect keyed on `chartSeriesResetVersion` — feasible
(lightweight-charts 5.2 exposes `removePane`) but a separate, riskier change.

**Sweep (2026-08-14, market closed):** `AAPL,MSFT,NVDA,EA,AMD --pace=3000` —
all 55 ticker×timeframe combinations rendered candles, no regressions. One
pre-existing failure, unrelated to this work: EA's tape is stuck at
2026-08-04 across all four tapes, with `historyLoading: true` and an empty
`error`; `&refresh=1` does not move it while other tickers stay current.

Note `--all` is not a usable signal on this backend: each payload costs 4.5 MB
and 1-2 s of CPU to build, so sweeping the whole watchlist at `--pace=1200`
saturates it and most tickers come back `fetch failed`. Those same tickers
serve fine individually. Sweep a subset at `--pace=3000`. (The saturation is
itself an argument for Task 2.)

Two findings worth carrying forward:

- `updateStudyClouds`, `updateSessionShades` and `updateSessionTimeLines`
  (~16485+, plus their three never-assigned refs at ~12613) are **dead code**
  inside the creation effect. `indicatorSettings.clouds` was a rebuild
  dependency *solely* because the dead `updateStudyClouds` read it. Live cloud
  painting happens in the update effect via `nativeCloudPairs`.
- A concurrent session was editing `App.jsx` throughout. Hunks were separated
  with a context-anchored `git apply --cached`; a zero-context patch
  (`--unidiff-zero`) silently misplaced every hunk, so do not use that here.

---

### Task 2: The server sends the timeframe that was asked for

~90% payload reduction and removes browser aggregation entirely.

**Measured payload composition (AAPL, 2026-08-14)** — 4,586,316 bytes, 2.38 s:

| key | bytes | share | rows |
|---|---|---|---|
| `studyBars` | 1,395,602 | 30.4% | 13,330 |
| `bars` (1-minute live tape) | 1,262,110 | 27.5% | 12,500 |
| `fineStudyBars` | 1,237,371 | 27.0% | 12,070 |
| `dailyBars` | 651,207 | 14.2% | 5,027 |
| everything else | ~40 KB | 0.9% | |

**This changes Task 2's shape: no single tape dominates.** Aggregating on the
server shrinks the *display* path, but three of the four tapes are also read
directly by indicators, so they cannot simply be dropped:

- `fineStudyBars` is the exception and the cleanest win. It is read in exactly
  ONE place — `buildChartDisplayBars` at `App.jsx:13246`. (The `fineStudyBars`
  memo at `App.jsx:13919` is a *different* value derived from
  `dailyBars`/`studyBars`.) Once the server sends display bars, this tape can
  leave the payload entirely: **-27%, no study touched.**
- `bars` can shrink to a short recent tail — enough for the forming candle and
  the live stream — instead of the full 12,500-row tape.
- `studyBars` feeds MTF/Ganesh signal studies; `dailyBars` feeds pivots,
  previous-OHLC, MTF MA levels, cloud bands and squeeze 4/10. Trimming these is
  a separate, riskier exercise in how much history each study actually needs.

So the plan's "~4.5 MB → ~50 KB" is not reachable by aggregation alone.
Dropping `fineStudyBars` and trimming `bars` is a realistic **~55%** cut
(≈4.5 MB → ≈2 MB) without touching a single indicator. Report actual numbers.

- [x] **Step 1a: Port the bucketing to Python.** Done, commit `04f1abb`:
  `chart_aggregation.py` + `tests/test_chart_aggregation.py`. Verified against
  the JS implementation over 217,308 bucket boundaries spanning 14 months and
  13 timeframes, crossing both DST transitions — zero mismatches. Nothing is
  wired to it yet, so the chart is unchanged.

**The biggest win was not aggregation at all: the endpoint sent no compression.**
`/api/oi-finder-chart` returned 4.5 MB of raw JSON even when the client
advertised `Accept-Encoding: gzip`. It is almost entirely numeric OHLCV:

| | bytes | share of raw | CPU |
|---|---|---|---|
| raw | 4,586,316 | 100% | — |
| gzip level 1 | 1,009,520 | 22% | 75 ms |
| gzip level 6 | 778,733 | 17% | 217 ms |
| gzip level 9 | 735,317 | 16% | 1,433 ms |

Level 1 is committed (`4dcaace`): **-78% transfer for 75 ms**, no change to the
JSON, so no client can tell the difference beyond getting it sooner. Level 6 is
the wrong trade on a server whose chart endpoints already fight for the GIL.

- [x] **Step 1c: Port `buildChartDisplayBars`.** Done in `4dcaace`. Bucketing
  alone was not enough — WHICH tape a timeframe is built from is part of the
  answer. Verified against the browser on four real payloads across all 13
  timeframes: **208,981 candles compared field by field, zero mismatches.**

- [ ] **PENDING RESTART — gzip is committed but not live.** Verified 09:18 ET:
  api_server is still the 00:44 process, and the endpoint still answers with
  `Content-Length: 4,574,796` and no `Content-Encoding`. The restart was
  deliberately NOT done: it was 12 minutes before the opening bell, and
  restarting bounces the Schwab streamer ([[multi-session-restart-moratorium]]).
  **Do it after 16:00 ET**: stop the api_server process, and the watchdog
  respawns it in 20-60 s. Then confirm with
  `curl -s --compressed -o /dev/null -D - "http://127.0.0.1:3001/api/oi-finder-chart?symbol=AAPL"`
  — expect `Content-Encoding: gzip` and roughly 1.0 MB instead of 4.5 MB.
  Same payload measured 4.54 s at 09:18 versus 0.6-2.4 s overnight, so the
  compression matters most exactly when the market is busy.

**Checking supervision before a restart — do not repeat this misdiagnosis.**
api_server's recorded ParentProcessId points at a transient shell that has
already exited, because `scripts/scanner_watchdog.ps1` starts it detached via
`Start-Process`. A dead parent PID is therefore NORMAL and is **not** evidence
that the backend is unsupervised. The scheduled task's `LastRunTime` is when the
*watchdog* started, not the backend, and `LastTaskResult 267009` (`0x41301`)
means "currently running", not an error. To actually check, look for the
`scanner_watchdog.ps1` process (ignoring your own query, which matches its own
search string) and read `artifacts/scanner_watchdog.log`.

- [ ] **Step 1b:** Add `&aggregate=<3m|5m|…|4h|D|W|M>` to `/api/oi-finder-chart`.
  Port the bucketing from `frontend/src/chartAggregation.js` —
  `chartAggregationBucketTime` — to Python, preserving its quirks exactly: 4h uses
  the TOS bucket anchored to Central midnight, 2h anchors to Eastern midnight for
  DST, D/W/M use Eastern calendar buckets, everything else is plain epoch
  bucketing. Write the tests from the JS test file first.

- [ ] **Step 2:** Have the client request its active timeframe and skip
  `buildChartDisplayBars` aggregation when the server already aggregated. Keep the
  client path working — it is the fallback.

- [ ] **Step 3:** Measure payload and time-to-first-candle before and after for
  AAPL 4h. Expect ~4.5 MB → ~50 KB. Report actual numbers.

- [ ] **Step 4:** Full sweep, both origins (`:5173` and app.agxtrade.com), commit.

---

### Task 3 (only if 1 and 2 leave it feeling slow): windowed history

Load the visible range plus a margin; page in more on pan. This is what makes a
decade of data feel instant. Larger project — do not start it before 1 and 2 are
verified, and re-measure first: they may make it unnecessary.

---

## Definition of done

- Toggling an indicator produces zero chart rebuilds (measured).
- Maximizing with indicators on does not shake (seen, on a visible window).
- AAPL 4h first candle in well under a second.
- `node scripts/chart_sweep.mjs --all --pace=1200` — no regressions.
- The trader was not asked to run anything.
