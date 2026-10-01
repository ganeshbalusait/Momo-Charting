# MomoX parity quick wins — design

Date: 2026-08-09
Status: approved by user (chat), scope confirmed via multi-select

## Background

The user shared the MomoX changelog (v3.204.75–.169) and asked which updates suit
this app. A full inventory (Explore agent, this session) found most changelog
items already covered (view-changes-land-on-today, axis-scale safety, overlays
tracking during gestures, axis-label decluttering) or not applicable
(licensing/devices, shared multi-member EM captures, What's New pill, OS-level
pinned floats). The wall-label "ladders" item is also already covered: wall
label texts are DOM axis chips laid out by the `layoutChartAxisLabels` collision
solver (`chartAxisLabels.js`); the canvas primitive draws only line segments.

Four gaps remain and are in scope for this pass. Bigger items (EM opening
capture + sanity gate, OI demand-green-on-defended-bounce, web-worker study
rebuilds) were explicitly deferred by the user.

## 1. Pane sizes that stay put

**Problem.** Panes are resizable (`panes.enableResize`), but a separator drag
never triggers any save: the pane-0 ResizeObserver (App.jsx ~16412) only
re-syncs overlays, and non-explicit `saveChartLayoutProfile(false)` writes pane
factors only to the in-memory `sessionChartViewportRef`. Reload loses the
resize unless the explicit Save-layout button was clicked.

**Hard constraint** (2026-08-05 ratchet fix): non-explicit saves must never
write framing (time span / price range) to localStorage; only the explicit
Save button may. The `explicit: true` read gate in `readChartLayoutProfile`
must not be relaxed. Pane stretch factors are safe to auto-persist — absolute
values, no clamp/re-expand cycle, and `restoreChartLayoutProfile` already
applies only pane factors from saved profiles.

**Design.**
- New localStorage store `oiFinderChartPaneFactors`, keyed by the same
  `screen:scope:timeframe` profile key (`chartLayoutProfileStorageKey`).
- New pure helpers in `chartViewport.js` (+ unit tests):
  `loadChartPaneFactors(storageJson, key)` / `savePaneFactors`-style merge
  helper, carrying `paneSizingVersion` like the profile store, and a
  `paneFactorsMateriallyDiffer(a, b)` comparator (relative tolerance ~1%).
- **Save trigger:** a `lastAppliedPaneFactorsRef` updated by every programmatic
  `setStretchFactor` path (restore, defaults). The pane-0 ResizeObserver
  callback debounces ~400 ms, reads `chart.panes().map(getStretchFactor)`, and
  persists only when factors differ materially from the ref (i.e. a user
  separator drag). Host resizes and programmatic restores match the ref and
  never save, so startup/rebuild churn cannot clobber the store with defaults.
- **Restore:** `restoreChartPaneFactors` gains the dedicated store as its first
  source, then the explicit profile's factors, then defaults. Applied at chart
  creation and rebuild restore (both already call it).
- Storage stays local (same as explicit layouts); no server sync.

## 2. Price lock (follow-the-tape toggle)

**Today:** follow-latest is implicit and dies on any pan/wheel
(`autoFollowLatestSymbolRef` cleared at App.jsx ~16279/~16313); vertical never
re-snaps. `returnToLatestCandle` does both axes but is a one-shot button.

**Design.**
- A pin-style toggle button in the chart actions row next to Latest/Reset,
  `aria-pressed`, tooltip "Price lock".
- Toggle-on → run existing `returnToLatestCandle` (horizontal: keep zoom width
  via `followLatestTimeScale`; vertical: `enableCandleAutoScale` + candle fit).
- While on, at every bar close — detected by the last bar's `time` changing in
  a dedicated effect — clear manual-nav flags and re-run the same snap.
  Between closes the user can pan/zoom freely; the lock reasserts at the next
  close (matches MomoX semantics "snaps at every close").
- **Persistence:** `priceLock` boolean per panel in the chart workspace
  `panels[]` (`normalizeChartWorkspace` in `chartWorkspaceState.js`, + test),
  restored through the existing workspace autosave. No new storage key.
- Off = today's behavior exactly; the snap reuses the exact routines the
  existing buttons call, so pan/freeze invariants are untouched.

## 3. Chain-poll rate-limit backoff

**Today:** the 15 s options poll re-fires at fixed cadence even when every
request fails; `scheduleChainPoll` cannot distinguish failure, and HTTP status
(e.g. 429) is lost inside a thrown Error message.

**Design.**
- `loadOiFinderFeed` returns `failed: true` and numeric `status` on non-abort
  errors (capture `response.status` before throwing).
- New pure helper in `oiFinderRequestPolicy.js` (+ tests):
  `nextOiChainPollDelay({ ready, warming, failed, rateLimited, failureCount })`
  → warming 700 ms (unchanged); normal 15 s; failure doubles 15→30→60→cap
  120 s; `rateLimited` (429) jumps to ≥45 s immediately. Success resets.
- `scheduleChainPoll` keeps a consecutive-failure counter and feeds the helper.

## 4. Schwab activation-delay note

One `<small>` in the `schwab-auth-steps` block (App.jsx ~27931): a freshly
created Schwab developer app can take hours (sometimes a day) to reach "Ready
For Use"; until then authentication fails even with correct keys. Link to
https://developer.schwab.com. No backend change.

## Testing / verification

- Unit tests for all new pure helpers; `node --test src/*.test.js` (baseline
  ~204 passing) from `frontend/`.
- App.jsx syntax check via the pnpm-nested esbuild `transformSync` (no bare
  npx esbuild).
- `npx vite build` (PWA on :3001 serves dist; user browses :5173 dev).
- Browser smoke test on :5173; note the known limitation that hidden in-app
  browser panes never fire rAF, so overlay chips not rendering headless is not
  a failure.
- Re-read every App.jsx region immediately before each edit (concurrent
  session hazard).
