# MomoX Parity Quick Wins Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port four MomoX changelog items: pane sizes that persist across reloads, a price-lock (follow-the-tape) toggle, chain-poll rate-limit backoff, and a Schwab app-activation help note.

**Architecture:** All logic lands in the existing pure-helper modules next to `App.jsx` (`chartViewport.js`, `chartWorkspaceState.js`, `oiFinderRequestPolicy.js`) with unit tests, then thin wiring inside `App.jsx`. No backend changes.

**Tech Stack:** React 18 + Vite, lightweight-charts v5, `node --test` for unit tests.

## Global Constraints

- Project root: `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2`. Frontend work happens in `frontend/`.
- **`App.jsx` is ~28k lines and a concurrent agent session may be editing it. NEVER edit by remembered line number — re-read the target region (Grep for the anchor snippet) immediately before every Edit.**
- **Ratchet-fix invariant:** non-explicit auto-saves must NEVER write time span or price range to localStorage. Only pane stretch factors may auto-persist. Do not touch the `explicit: true` gate in `readChartLayoutProfile`.
- Tests: `node --test src/*.test.js` run from `frontend/` (baseline: all currently passing, ~204+).
- Do not run `npx esbuild` (not installed); syntax-check `App.jsx` with the pnpm-nested esbuild (see Task 7).
- Commit after each task with a conventional message; end commit messages with the Claude co-author trailer.

---

### Task 1: Pane-factor persistence helpers (pure module)

**Files:**
- Modify: `frontend/src/chartViewport.js` (append new exports)
- Test: `frontend/src/chartViewport.test.js` (append)

**Interfaces:**
- Consumes: existing `CHART_PANE_SIZING_VERSION` in the same file.
- Produces (Task 2 relies on these exact names):
  - `OI_CHART_PANE_FACTORS_STORAGE_KEY = "oiFinderChartPaneFactors"`
  - `readStoredChartPaneFactors(storage, profileKey)` → `{ paneFactors: number[], paneSizingVersion: number } | null`
  - `storeChartPaneFactors(storage, profileKey, paneFactors)` → `boolean`
  - `paneFactorsMateriallyDiffer(current, reference, relativeTolerance = 0.01)` → `boolean`

- [ ] **Step 1: Write the failing tests** — append to `frontend/src/chartViewport.test.js`, matching its existing `node:test` + `assert` import style (re-read the top of the file to copy the exact import lines; do not duplicate imports):

```js
test("storeChartPaneFactors round-trips through readStoredChartPaneFactors", () => {
  const memory = new Map();
  const storage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, value),
  };
  assert.equal(storeChartPaneFactors(storage, "standard:5m", [6.4, 1.1, 1.05]), true);
  const stored = readStoredChartPaneFactors(storage, "standard:5m");
  assert.deepEqual(stored.paneFactors, [6.4, 1.1, 1.05]);
  assert.equal(stored.paneSizingVersion, CHART_PANE_SIZING_VERSION);
  assert.equal(readStoredChartPaneFactors(storage, "fullscreen:5m"), null);
});

test("storeChartPaneFactors rejects invalid input without writing", () => {
  const memory = new Map();
  const storage = {
    getItem: (key) => (memory.has(key) ? memory.get(key) : null),
    setItem: (key, value) => memory.set(key, value),
  };
  assert.equal(storeChartPaneFactors(storage, "", [6, 1, 1]), false);
  assert.equal(storeChartPaneFactors(storage, "standard:5m", []), false);
  assert.equal(storeChartPaneFactors(storage, "standard:5m", [6, Number.NaN, 1]), false);
  assert.equal(storeChartPaneFactors(null, "standard:5m", [6, 1, 1]), false);
  assert.equal(memory.size, 0);
});

test("readStoredChartPaneFactors survives corrupted storage JSON", () => {
  const storage = { getItem: () => "{not json", setItem: () => {} };
  assert.equal(readStoredChartPaneFactors(storage, "standard:5m"), null);
});

test("paneFactorsMateriallyDiffer uses a relative tolerance", () => {
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2, 1.2], [6, 1.2, 1.2]), false);
  assert.equal(paneFactorsMateriallyDiffer([6.001, 1.2, 1.2], [6, 1.2, 1.2]), false);
  assert.equal(paneFactorsMateriallyDiffer([6.5, 1.2, 1.2], [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2], [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer(null, [6, 1.2, 1.2]), true);
  assert.equal(paneFactorsMateriallyDiffer([6, 1.2, 1.2], null), true);
});
```

Also extend the file's import from `./chartViewport` with the four new names.

- [ ] **Step 2: Run tests to verify they fail**

Run (from `frontend/`): `node --test src/chartViewport.test.js`
Expected: FAIL — the new names are not exported.

- [ ] **Step 3: Implement** — append to `frontend/src/chartViewport.js`:

```js
export const OI_CHART_PANE_FACTORS_STORAGE_KEY = "oiFinderChartPaneFactors";

// Pane stretch factors are the one viewport property that auto-persists.
// Framing (time span / price range) stays explicit-save-only: cycling those
// through storage is what caused the 2026-08-05 layout ratchet. Factors are
// absolute values with no clamp-and-re-expand loop, so they are safe.
export function readStoredChartPaneFactors(storage, profileKey) {
  const key = String(profileKey || "").trim();
  if (!key) return null;
  try {
    const raw = storage?.getItem?.(OI_CHART_PANE_FACTORS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    const entry = parsed && typeof parsed === "object" ? parsed[key] : null;
    const factors = Array.isArray(entry?.paneFactors) ? entry.paneFactors.map(Number) : [];
    if (!factors.length || !factors.every((value) => Number.isFinite(value) && value > 0)) {
      return null;
    }
    return {
      paneFactors: factors,
      paneSizingVersion: Number(entry?.paneSizingVersion) || 0,
    };
  } catch {
    return null;
  }
}

export function storeChartPaneFactors(storage, profileKey, paneFactors) {
  const key = String(profileKey || "").trim();
  const factors = Array.isArray(paneFactors) ? paneFactors.map(Number) : [];
  if (
    !key
    || typeof storage?.setItem !== "function"
    || !factors.length
    || !factors.every((value) => Number.isFinite(value) && value > 0)
  ) return false;
  try {
    const raw = storage.getItem?.(OI_CHART_PANE_FACTORS_STORAGE_KEY);
    const parsed = typeof raw === "string" && raw.trim() ? JSON.parse(raw) : null;
    const entries = parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
    entries[key] = {
      paneFactors: factors,
      paneSizingVersion: CHART_PANE_SIZING_VERSION,
      savedAt: new Date().toISOString(),
    };
    storage.setItem(OI_CHART_PANE_FACTORS_STORAGE_KEY, JSON.stringify(entries));
    return true;
  } catch {
    return false;
  }
}

export function paneFactorsMateriallyDiffer(current, reference, relativeTolerance = 0.01) {
  const left = Array.isArray(current) ? current.map(Number) : null;
  const right = Array.isArray(reference) ? reference.map(Number) : null;
  if (!left || !right || left.length !== right.length) return true;
  return left.some((value, index) => {
    const other = right[index];
    if (!Number.isFinite(value) || !Number.isFinite(other)) return true;
    const scale = Math.max(Math.abs(value), Math.abs(other), 1e-9);
    return Math.abs(value - other) / scale > relativeTolerance;
  });
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `node --test src/chartViewport.test.js` — expected PASS (all, old and new).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/chartViewport.js frontend/src/chartViewport.test.js
git commit -m "feat: pane-factor persistence helpers for auto-saving pane sizes"
```

---

### Task 2: Wire pane-size persistence into App.jsx

**Files:**
- Modify: `frontend/src/App.jsx` (three regions, all located by Grep anchors)

**Interfaces:**
- Consumes: Task 1 exports (`readStoredChartPaneFactors`, `storeChartPaneFactors`, `paneFactorsMateriallyDiffer`, `OI_CHART_PANE_FACTORS_STORAGE_KEY` — import the first three; the key constant only if needed).
- Produces: user-visible behavior — dragging a pane separator persists sizes per `screen:scope:timeframe` profile key; reload restores them.

- [ ] **Step 1: Add imports.** Grep `from "./chartViewport"` in `App.jsx`; add `readStoredChartPaneFactors`, `storeChartPaneFactors`, `paneFactorsMateriallyDiffer` to that import list.

- [ ] **Step 2: Add the applied-factors ref.** Grep `sessionChartViewportRef` declaration (a `useRef`) and add nearby (same ref block):

```js
const lastAppliedPaneFactorsRef = useRef(null);
```

- [ ] **Step 3: Restore from the dedicated store first.** Grep `const restoreChartLayoutProfile = ` in `App.jsx`. Inside it, the current code computes `paneFactors` via `restoreChartPaneFactors({ paneFactors: profile?.paneFactors, ... })`. Change so the dedicated store wins over the explicit profile, and record what was applied:

```js
const storedPaneFactors = readStoredChartPaneFactors(window.localStorage, chartLayoutProfileKey);
const paneFactors = restoreChartPaneFactors({
  paneFactors: storedPaneFactors?.paneFactors ?? profile?.paneFactors,
  paneSizingVersion: storedPaneFactors?.paneSizingVersion ?? profile?.paneSizingVersion,
  isBigScreen: usesBigScreenProfile,
});
paneFactors.forEach((factor, index) => {
  chart.panes()[index]?.setStretchFactor(factor);
});
lastAppliedPaneFactorsRef.current = chart.panes().map((pane) => pane.getStretchFactor());
```

- [ ] **Step 4: Record programmatic applications everywhere else.** Grep ALL `setStretchFactor` call sites in `App.jsx` (there is at least one more inside `restoreSessionChartViewport`, and possibly one at chart creation). After each programmatic loop that applies factors, add the same line:

```js
lastAppliedPaneFactorsRef.current = chart.panes().map((pane) => pane.getStretchFactor());
```

(Use the local `chart` variable in scope at each site; in `restoreSessionChartViewport` it is `chart`.)

- [ ] **Step 5: Save on user separator drags.** Grep `mainPaneResizeObserver` in `App.jsx`. The observer callback currently only calls `scheduleOverlayGeometry()`. Extend the effect (the observer lives inside the big chart-creation effect, so `chart` is in scope) with a debounced user-drag detector:

```js
let paneFactorSaveTimer = 0;
const persistUserPaneResize = () => {
  paneFactorSaveTimer = 0;
  if (chartDisposed) return;
  const factors = chart.panes().map((pane) => pane.getStretchFactor());
  if (factors.length < 2) return;
  // Programmatic restores update lastAppliedPaneFactorsRef themselves, so a
  // material difference here can only come from a user separator drag.
  if (!paneFactorsMateriallyDiffer(factors, lastAppliedPaneFactorsRef.current)) return;
  lastAppliedPaneFactorsRef.current = factors;
  storeChartPaneFactors(window.localStorage, chartLayoutProfileKeyRef.current, factors);
};
```

and inside the observer callback, after `scheduleOverlayGeometry()`:

```js
if (paneFactorSaveTimer) window.clearTimeout(paneFactorSaveTimer);
paneFactorSaveTimer = window.setTimeout(persistUserPaneResize, 400);
```

In the effect's cleanup (where `mainPaneResizeObserver?.disconnect?.()` or equivalent teardown runs — grep it), add:

```js
if (paneFactorSaveTimer) window.clearTimeout(paneFactorSaveTimer);
```

Note: `chartLayoutProfileKeyRef` already exists (used by `queueChartLayoutSave`); use the ref, not the closure value, so timeframe switches don't save under a stale key. `chartDisposed` is an existing flag in that effect — verify the name when reading the region and use whatever the effect actually calls it.

- [ ] **Step 6: Make sure the explicit-save path still records.** Grep `const saveChartLayoutProfile = `; no change needed to its localStorage writes (framing stays explicit-only), but confirm the non-announce branch was NOT modified — this task must not add framing writes there.

- [ ] **Step 7: Syntax-check App.jsx** (see Task 7 Step 2 for the exact command) and run the full unit suite: `node --test src/*.test.js` — expected PASS.

- [ ] **Step 8: Manual verification on :5173.** Drag the separator between the candle pane and the squeeze stack, wait ~1 s, check DevTools → Application → localStorage for `oiFinderChartPaneFactors`, reload, confirm the pane sizes stick. Also confirm switching timeframes still restores that timeframe's own sizes.

- [ ] **Step 9: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "feat: pane sizes persist across reloads without explicit save"
```

---

### Task 3: Price-lock flag in workspace state (pure module)

**Files:**
- Modify: `frontend/src/chartWorkspaceState.js`
- Test: `frontend/src/chartWorkspaceState.test.js` (append)

**Interfaces:**
- Consumes: existing `normalizeChartWorkspace`.
- Produces: every normalized panel gains `priceLock: boolean` (default `false`). Task 4 reads `workspace.panels[index].priceLock` and writes it back through the existing save path.

- [ ] **Step 1: Write the failing test** — append to `chartWorkspaceState.test.js` (match its existing import style):

```js
test("normalizeChartWorkspace carries per-panel priceLock with a false default", () => {
  const normalized = normalizeChartWorkspace({
    panels: [
      { symbol: "TSLA", priceLock: true },
      { symbol: "AAPL", priceLock: "yes" },
      { symbol: "MSFT" },
    ],
  });
  assert.equal(normalized.panels[0].priceLock, true);
  assert.equal(normalized.panels[1].priceLock, false);
  assert.equal(normalized.panels[2].priceLock, false);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `node --test src/chartWorkspaceState.test.js` — expected FAIL (`priceLock` is `undefined`).

- [ ] **Step 3: Implement.** In `normalizeChartWorkspace`, the panel mapper currently returns `{ symbol, linkGroup, timeframe }`. Add one line:

```js
    return {
      symbol: normalizeSymbol(panel.symbol, resolved.fallbackSymbol),
      linkGroup: clampInteger(panel.linkGroup, 1, 9, (index % 9) + 1),
      timeframe,
      priceLock: panel.priceLock === true,
    };
```

- [ ] **Step 4: Run to verify it passes** — `node --test src/chartWorkspaceState.test.js`, then the full suite `node --test src/*.test.js` (grid snapshot tests must still pass).

- [ ] **Step 5: Commit**

```bash
git add frontend/src/chartWorkspaceState.js frontend/src/chartWorkspaceState.test.js
git commit -m "feat: per-panel priceLock flag in chart workspace state"
```

---

### Task 4: Price-lock toggle UI + snap-at-close behavior (App.jsx)

**Files:**
- Modify: `frontend/src/App.jsx` (chart component props, actions row, one new effect, parent wiring)

**Interfaces:**
- Consumes: Task 3's `panels[].priceLock`; existing `returnToLatestCandle` (keeps horizontal zoom width, re-enables candle auto-scale vertically — exactly the snap the lock needs).
- Produces: a toggled-on chart re-snaps both axes at every bar close.

- [ ] **Step 1: Find the chart component's prop seam.** Grep `onTimeframeChange` in `App.jsx`. The chart panel component receives it as a prop; the workspace parent passes it per panel. Note the component's prop destructuring line and the parent call site(s) — including maximized/popout variants if they render the same component.

- [ ] **Step 2: Thread the props.** Add `priceLockEnabled = false` and `onPriceLockChange` to the chart component's props. At each parent call site that renders a workspace panel, pass:

```jsx
priceLockEnabled={workspace.panels[panelIndex]?.priceLock === true}
onPriceLockChange={(next) => updateWorkspacePanel(panelIndex, { priceLock: next === true })}
```

If no `updateWorkspacePanel`-style helper exists, follow the pattern the parent already uses to write a panel's `timeframe` (grep `updateWorkspacePanelTimeframe` usage) and write the panels array the same way — a small inline updater that maps panels and flips `priceLock` on the target index, then goes through the existing workspace save path. Popout/maximized chart surfaces that don't map to a workspace panel may keep a local `useState` fallback: `const [localPriceLock, setLocalPriceLock] = useState(false)` with the same prop names.

- [ ] **Step 3: Add the toggle button.** Grep `data-tooltip="Latest candle"` in `App.jsx`. Insert BEFORE that button:

```jsx
<button
  className={`oi-finder-chart-latest chart-icon-action${priceLockEnabled ? " is-active" : ""}`}
  type="button"
  onClick={() => {
    const next = !priceLockEnabled;
    onPriceLockChange?.(next);
    if (next) returnToLatestCandle();
  }}
  title={priceLockEnabled
    ? "Price lock on: the chart re-snaps to the newest bar at every close"
    : "Price lock: keep your zoom and snap to the newest bar at every close"}
  data-tooltip="Price lock"
  aria-label="Toggle price lock (follow the newest bar at every close)"
  aria-pressed={priceLockEnabled}
>
  <Pin size={14} />
</button>
```

Add `Pin` to the existing `lucide-react` import (grep `RotateCcw` to find it). If `.is-active` produces no visible state on this button class, grep `.chart-icon-action.is-active` in `index.css`; if absent, add alongside the existing `.oi-finder-chart-latest` rules:

```css
.oi-finder-chart-latest.is-active {
  color: #18f0ff;
  border-color: rgba(24, 240, 255, 0.55);
}
```

- [ ] **Step 4: Snap at bar close.** Inside the chart component (near the other chart effects — grep `focusLatestVersion` for a good neighborhood), add:

```jsx
const priceLockBarTimeRef = useRef(0);
const latestChartBarTime = Number(chartBars.at(-1)?.time || 0);
useEffect(() => {
  if (!priceLockEnabled || !latestChartBarTime) {
    priceLockBarTimeRef.current = latestChartBarTime;
    return;
  }
  if (priceLockBarTimeRef.current === latestChartBarTime) return;
  const isFirstObservation = priceLockBarTimeRef.current === 0;
  priceLockBarTimeRef.current = latestChartBarTime;
  // A new latest-bar time means the previous bar closed. Re-snap both axes
  // while keeping the trader's zoom width — same routine as the Latest button.
  // The first observation after mount/ticker change is not a close; the
  // opening framing already landed on today.
  if (isFirstObservation) return;
  returnToLatestCandle();
}, [priceLockEnabled, latestChartBarTime]);
```

Important: `returnToLatestCandle` already clears `manualTimeNavigationRef`/`manualPriceNavigationRef` and re-arms `autoFollowLatestSymbolRef` — do not duplicate that here. Do NOT snap on every tick (the dependency is the latest bar's `time`, which only changes at a bar boundary). Ticker changes reset `priceLockBarTimeRef` naturally because the new symbol's first observation returns early.

- [ ] **Step 5: Syntax-check + full test suite** (same commands as Task 7 Steps 1–2). Expected: PASS.

- [ ] **Step 6: Manual verification on :5173.** Toggle the pin on a 1m/5m chart: it should snap to the latest bar immediately. Pan away horizontally and vertically; at the next bar close the chart should glide back with the same zoom width. Toggle off; panning must stay put across closes (today's behavior). Reload; the pin state should be restored per panel.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/App.jsx frontend/src/index.css
git commit -m "feat: price lock toggle - snap to newest bar at every close"
```

---

### Task 5: Chain-poll failure/429 backoff

**Files:**
- Modify: `frontend/src/oiFinderRequestPolicy.js`
- Modify: `frontend/src/App.jsx` (`loadOiFinderFeed` error return + `scheduleChainPoll`)
- Test: `frontend/src/oiFinderRequestPolicy.test.js` (append)

**Interfaces:**
- Produces: `nextOiChainPollDelay({ ready, warming, failed, rateLimited, failureCount })` → milliseconds. `loadOiFinderFeed` result gains `failed: true` and numeric `status` on non-abort errors.

- [ ] **Step 1: Write the failing tests** — append to `oiFinderRequestPolicy.test.js` (match existing import style; add `nextOiChainPollDelay` to the import):

```js
test("nextOiChainPollDelay keeps the healthy cadence", () => {
  assert.equal(nextOiChainPollDelay({ ready: true }), 15_000);
  assert.equal(nextOiChainPollDelay({}), 15_000);
  assert.equal(nextOiChainPollDelay({ ready: false, warming: true }), 700);
});

test("nextOiChainPollDelay doubles on consecutive failures and caps at 120s", () => {
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 1 }), 30_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 2 }), 60_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 3 }), 120_000);
  assert.equal(nextOiChainPollDelay({ failed: true, failureCount: 9 }), 120_000);
});

test("nextOiChainPollDelay enforces a 45s floor when rate limited", () => {
  assert.equal(nextOiChainPollDelay({ failed: true, rateLimited: true, failureCount: 1 }), 45_000);
  assert.equal(nextOiChainPollDelay({ failed: true, rateLimited: true, failureCount: 3 }), 120_000);
});

test("nextOiChainPollDelay ignores stale failure counts once a poll succeeds", () => {
  assert.equal(nextOiChainPollDelay({ ready: true, failed: false, failureCount: 0 }), 15_000);
});
```

- [ ] **Step 2: Run to verify it fails** — `node --test src/oiFinderRequestPolicy.test.js`: FAIL (not exported).

- [ ] **Step 3: Implement the helper** — append to `oiFinderRequestPolicy.js`:

```js
export function nextOiChainPollDelay({
  ready = false,
  warming = false,
  failed = false,
  rateLimited = false,
  failureCount = 0,
} = {}) {
  const failures = Math.max(0, Math.floor(Number(failureCount) || 0));
  if (failed && failures > 0) {
    // 15s → 30s → 60s → 120s cap; a provider 429 never retries under 45s.
    const backoff = Math.min(120_000, 15_000 * 2 ** Math.min(failures, 3));
    return rateLimited ? Math.max(45_000, backoff) : backoff;
  }
  if (!ready && warming) return 700;
  return 15_000;
}
```

- [ ] **Step 4: Run to verify it passes** — `node --test src/oiFinderRequestPolicy.test.js`: PASS.

- [ ] **Step 5: Surface failure + status from `loadOiFinderFeed`.** Grep `const loadOiFinderFeed = ` in `App.jsx` and re-read the whole function.
  - In the `try` block, replace `if (!response.ok) throw new Error(payload.error || "OI Finder request failed.");` with:

```js
      if (!response.ok) {
        const requestFailure = new Error(payload.error || "OI Finder request failed.");
        requestFailure.httpStatus = response.status;
        throw requestFailure;
      }
```

  - Declare alongside the existing `let responsePayload = null;`:

```js
    let requestFailed = false;
    let requestFailureStatus = null;
```

  - In the non-abort branch of the `catch` (after the AbortError early-return), set:

```js
        requestFailed = true;
        requestFailureStatus = Number(requestError?.httpStatus) || null;
```

  (Set these regardless of `canCommitOiFinderRequest` — backoff cares that the request failed, not who owns the UI.) Note the AbortError path already `return`s a result without `failed`; leave it — a client-side timeout is the warming path, not a provider failure.
  - Find the function's final `return` statement(s) (after the `finally` block) and include the new fields, e.g. if it currently returns `{ started: true, request, payload: responsePayload }`, make it:

```js
    return {
      started: true,
      request,
      payload: responsePayload,
      failed: requestFailed,
      status: requestFailureStatus,
    };
```

- [ ] **Step 6: Use the helper in the poll loop.** Grep `nextChainPollDelay` in `App.jsx`. In that effect:
  - Add `nextOiChainPollDelay` to the `./oiFinderRequestPolicy` import.
  - Add `let chainPollFailures = 0;` beside `let compactChainReady = false;`.
  - Delete the local `const nextChainPollDelay = ...` and add:

```js
    const chainPollDelayFor = (result) => {
      const failed = Boolean(result?.failed);
      chainPollFailures = failed ? chainPollFailures + 1 : 0;
      return nextOiChainPollDelay({
        ready: compactChainReady,
        warming: compactResponseWarming(result),
        failed,
        rateLimited: Number(result?.status) === 429,
        failureCount: chainPollFailures,
      });
    };
```

  - Replace both call sites `nextChainPollDelay(result)` (the `startFeed().then(...)` kickoff and the recursive `scheduleChainPoll(...)` inside the timer) with `chainPollDelayFor(result)`.

- [ ] **Step 7: Syntax-check + full suite** — PASS expected.

- [ ] **Step 8: Commit**

```bash
git add frontend/src/oiFinderRequestPolicy.js frontend/src/oiFinderRequestPolicy.test.js frontend/src/App.jsx
git commit -m "feat: chain poll backs off on failures and provider rate limits"
```

---

### Task 6: Schwab activation-delay note

**Files:**
- Modify: `frontend/src/App.jsx` (Schwab settings card)

- [ ] **Step 1: Add the note.** Grep `The callback URL is private and single-use` in `App.jsx`. Directly after that `<small>`, add:

```jsx
                    <small>
                      Just created your Schwab developer app? A new app can take a few hours
                      (sometimes up to a day) to reach <strong>Ready For Use</strong> on Schwab's
                      side. Until then, authentication fails even with correct keys — check the
                      app status at <a href="https://developer.schwab.com" target="_blank" rel="noreferrer">developer.schwab.com</a> before retrying.
                    </small>
```

- [ ] **Step 2: Syntax-check App.jsx**, then verify visually on :5173 → Settings → Schwab card.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "docs: Schwab settings warn about developer-app activation delay"
```

---

### Task 7: Full verification pass

**Files:** none (verification only)

- [ ] **Step 1: Full unit suite** — from `frontend/`: `node --test src/*.test.js`. Expected: PASS, count ≥ baseline.

- [ ] **Step 2: App.jsx syntax check** — from `frontend/`, locate the pnpm-nested esbuild (`Get-ChildItem node_modules/.pnpm -Filter "esbuild@*"`) and run a Node one-liner using its `transformSync` with `{ loader: "jsx" }` over `src/App.jsx`; expected: no throw.

- [ ] **Step 3: Production build** — `npx vite build` from `frontend/`. Expected: success (PWA on :3001 serves `dist`, so this also refreshes the installable app).

- [ ] **Step 4: Browser smoke test** on `http://localhost:5173` (dev server usually already running): charts render, pane resize sticks across a reload, the pin toggle appears and snaps, Schwab card shows the note. Note the known limitation: a hidden in-app browser pane never fires rAF, so missing overlay chips in headless screenshots are NOT a failure.

- [ ] **Step 5: Report** — summarize results honestly, including anything skipped or unverifiable.
