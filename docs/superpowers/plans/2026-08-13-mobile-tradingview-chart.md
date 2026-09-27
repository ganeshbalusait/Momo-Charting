# Mobile TradingView-style Chart Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the phone charts page work like TradingView — the chart owns the viewport and every gesture, the option chain moves behind a tab instead of a scroll, the chart's wasted vertical space is reclaimed, and indicators are editable from the phone.

**Architecture:** The chart and the page currently fight over one finger, because `c527da9` gave the chart every vertical swipe while the chart still lives inside `.scanner-results-view`, the page's scroll container. Rather than arbitrating the gesture, we remove the contest: on the Chart tab nothing behind the chart scrolls, and the option chain is reached by switching tabs. The chart then becomes `flex: 1 1 auto` so it absorbs all remaining viewport.

**Tech Stack:** React 19, Vite 7, lightweight-charts 5.x, plain CSS in one 13k-line `index.css`. Tests are Node's built-in runner over small pure-logic modules beside `App.jsx`.

**Spec:** `docs/superpowers/specs/2026-08-13-mobile-tradingview-chart-design.md`

## Global Constraints

- Tree: `C:\GANESH\AgenticAI-Trading 7\AgenticAI-Trading 2` — the tree served to phones. Do **not** edit `AgenticAI-Trading 7/frontend`; it is not what runs.
- Tests: `node --test src/*.test.js` from `frontend/`. Not Vitest.
- Build: `npx vite build` from `frontend/`. The phone is served `frontend/dist` via :4173, so **no frontend change reaches the phone without a build**. :5173 is desktop dev only.
- Syntax-check `App.jsx` via esbuild at `node_modules/.pnpm/esbuild@<ver>/node_modules/esbuild` (`transformSync`, loader `jsx`). Bare `npx esbuild` is not installed.
- **All new CSS goes in ONE new `@media (max-width: 760px)` block appended at the END of `index.css`**, with `.app-shell.charts-workstation …` prefixes. The mid-file mobile block is outranked by later unconditional `.charts-workstation` rules; editing it silently does nothing. Verify **computed** styles at a real 390px viewport, never source inspection.
- Everything is scoped to `max-width: 760px`. Desktop layout must not change.
- Git is **local-only**; both remotes were deliberately removed and must NOT be re-added. Commit with explicit paths — `.venv` is tracked, so never `git add -A`.
- A second agent session may be editing `App.jsx`/`index.css` concurrently. **Re-read every region immediately before editing it**; line numbers in this plan will drift.

---

### Task 1: Phone price framing — reclaim the dead band

Candles occupy only ~63% of the pane: the fit pads 10% above and below, and the price scale then maps that into the middle 76%. Both are desktop-tuned. Extract the framing numbers into a testable module and make them viewport-aware.

**Files:**
- Create: `frontend/src/chartPriceFraming.js`
- Create: `frontend/src/chartPriceFraming.test.js`
- Modify: `frontend/src/App.jsx` — `OI_CHART_PRICE_SCALE_MARGINS` (~line 333) and `pricePadding` inside `fitVisibleCandlesToPriceScale` (~line 16869)

**Interfaces:**
- Produces:
  - `priceScaleMargins(isPhone: boolean) -> { top: number, bottom: number }`
  - `pricePaddingFor(candleSpan: number, isPhone: boolean) -> number`

- [ ] **Step 1: Write the failing test**

```js
import assert from "node:assert/strict";
import { test } from "node:test";
import { priceScaleMargins, pricePaddingFor } from "./chartPriceFraming.js";

test("desktop framing is unchanged", () => {
  assert.deepEqual(priceScaleMargins(false), { top: 0.12, bottom: 0.12 });
  // 10% of span, floored at 0.03
  assert.equal(pricePaddingFor(10, false), 1);
  assert.equal(pricePaddingFor(0.01, false), 0.03);
});

test("phone framing gives candles more of the pane", () => {
  assert.deepEqual(priceScaleMargins(true), { top: 0.06, bottom: 0.16 });
  // 4% of span, same 0.03 floor
  assert.equal(pricePaddingFor(10, true), 0.4);
  assert.equal(pricePaddingFor(0.01, true), 0.03);
});

test("phone bottom margin clears the volume band", () => {
  // Volume sits at { top: 0.78 }, so the price bottom margin must not reach
  // into it far enough to overlap candles with bars.
  const { bottom } = priceScaleMargins(true);
  assert.ok(bottom <= 1 - 0.78 + 0.02, "bottom margin should sit near the volume band edge");
});

test("candle occupancy improves on a phone", () => {
  const occupancy = (isPhone) => {
    const { top, bottom } = priceScaleMargins(isPhone);
    const pad = isPhone ? 0.04 : 0.10;
    return (1 / (1 + 2 * pad)) * (1 - top - bottom);
  };
  assert.ok(occupancy(true) > occupancy(false) + 0.05,
    "phone framing should reclaim at least 5 points of pane height");
});
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd frontend && node --test src/chartPriceFraming.test.js`
Expected: FAIL — cannot find module `./chartPriceFraming.js`.

- [ ] **Step 3: Write the module**

```js
// Desktop framing pads the price range 10% each way and then maps it into the
// middle 76% of the pane, so candles get ~63% of the height. That reads as
// breathing room on a wide pane; on a phone it is a third of the screen showing
// nothing. Phones get tighter padding and an asymmetric margin - a bigger
// bottom margin keeps the volume histogram (top: 0.78) clear of the candles.
const DESKTOP_MARGINS = Object.freeze({ top: 0.12, bottom: 0.12 });
const PHONE_MARGINS = Object.freeze({ top: 0.06, bottom: 0.16 });

const DESKTOP_PAD_RATIO = 0.10;
const PHONE_PAD_RATIO = 0.04;
const MINIMUM_PAD = 0.03;

export function priceScaleMargins(isPhone) {
  return isPhone ? PHONE_MARGINS : DESKTOP_MARGINS;
}

export function pricePaddingFor(candleSpan, isPhone) {
  const ratio = isPhone ? PHONE_PAD_RATIO : DESKTOP_PAD_RATIO;
  return Math.max(Number(candleSpan || 0) * ratio, MINIMUM_PAD);
}
```

- [ ] **Step 4: Run it to verify it passes**

Run: `cd frontend && node --test src/chartPriceFraming.test.js`
Expected: PASS.

- [ ] **Step 5: Wire it into App.jsx**

Re-read both regions first. Add the import beside the other local module imports, then:

Replace the constant at ~line 333:

```js
const OI_CHART_PRICE_SCALE_MARGINS = Object.freeze({ top: 0.12, bottom: 0.12 });
```

with a phone-aware helper used at the two `rightPriceScale` sites (~15625 and the overlay sites at 14410/14414 keep desktop values — only the main price scale changes):

```js
const isPhoneViewport = () =>
  typeof window !== "undefined" && window.matchMedia?.("(max-width: 760px)").matches;
```

and use `priceScaleMargins(isPhoneViewport())` where `OI_CHART_PRICE_SCALE_MARGINS` was passed to `rightPriceScale`.

Replace inside `fitVisibleCandlesToPriceScale`:

```js
      const pricePadding = Math.max(candleSpan * 0.10, 0.03);
```

with:

```js
      const pricePadding = pricePaddingFor(candleSpan, isPhoneViewport());
```

- [ ] **Step 6: Syntax-check and run all tests**

Run: `cd frontend && node --test src/*.test.js`
Expected: all pass, no regressions.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/chartPriceFraming.js frontend/src/chartPriceFraming.test.js frontend/src/App.jsx
git commit -m "fix(mobile): reclaim the chart's dead vertical band on phones"
```

---

### Task 2: A touch pan claims the view

The custom body pan is mouse-only (`pointerType !== "touch"`), so a touch pan is applied by lightweight-charts and never reaches the code that sets `manualTimeNavigationRef` / `manualPriceNavigationRef`. Without those claims the idle autoscale in `scheduleVisibleCandlePriceRange` treats the pan as an idle view and snaps candles back **under the moving finger**.

**Files:**
- Modify: `frontend/src/App.jsx` — the effect that registers `releaseAutomaticTimeFrame` on `interactionHost` (search for `releaseAutomaticTimeFrame`)

**Interfaces:**
- Consumes: `manualTimeNavigationRef`, `manualPriceNavigationRef`, `interactionHost` — all existing.
- Produces: nothing new; behavioural change only.

- [ ] **Step 1: Add a touch claim alongside the existing pointer handler**

Re-read the region. Beside the existing `pointerdown` registration, add a `touchstart` handler that claims both axes for a single-finger drag:

```js
    // Lightweight Charts applies touch pans itself, so the mouse-only body pan
    // never runs and nothing sets the manual-navigation flags. Without them the
    // idle autoscale re-fits mid-drag and the candles snap back under the
    // finger. A one-finger drag is a deliberate pan on both axes; a pinch is a
    // zoom and is left to the library.
    const claimTouchNavigation = (event) => {
      if (event.touches?.length !== 1) return;
      manualTimeNavigationRef.current = true;
      manualPriceNavigationRef.current = true;
    };
    interactionHost?.addEventListener("touchstart", claimTouchNavigation, { passive: true });
```

and mirror it in the cleanup:

```js
      interactionHost?.removeEventListener("touchstart", claimTouchNavigation);
```

- [ ] **Step 2: Syntax-check**

Run the esbuild `transformSync` check on `App.jsx` (loader `jsx`).
Expected: no syntax errors.

- [ ] **Step 3: Run all tests**

Run: `cd frontend && node --test src/*.test.js`
Expected: no regressions.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/App.jsx
git commit -m "fix(mobile): a touch pan claims the view so autoscale stops fighting it"
```

---

### Task 3: The chart owns the viewport

Replace the fixed phone chart height with a flex fill, and stop the page behind the chart from scrolling on the Chart tab.

**Files:**
- Modify: `frontend/src/index.css` — append a new `@media (max-width: 760px)` block at the **end of the file**

**Interfaces:**
- Consumes: `--mobile-nav-h` / `--mobile-nav-safe` (existing, defined in the mid-file mobile block).
- Produces: the CSS contract `.charts-workstation.is-chart-tab .scanner-results-view { overflow: hidden }` which Task 4 sets `is-chart-tab` for.

- [ ] **Step 1: Append the block at the end of index.css**

```css
/* =====================================================================
   PHONE CHART SHELL (2026-08-13)
   Appended at end of file deliberately: the mid-file mobile block is
   outranked by later unconditional .charts-workstation rules, so a rule
   written there silently does nothing. Source order wins here instead of
   fighting specificity.
   ===================================================================== */

@media (max-width: 760px) {
  /* On the Chart tab nothing behind the chart scrolls, so the chart can own
     every vertical swipe without the page and the finger fighting. The other
     tabs keep their normal scrolling. */
  .app-shell.charts-workstation.is-chart-tab .scanner-results-view {
    overflow: hidden;
    overscroll-behavior: none;
  }

  /* The chart absorbs whatever the chrome leaves instead of a fixed height,
     so trimming a toolbar row directly grows the chart. */
  .app-shell.charts-workstation.is-chart-tab .charts-oi-page-full {
    display: flex;
    height: 100%;
    min-height: 0;
    flex-direction: column;
  }

  .app-shell.charts-workstation.is-chart-tab .charts-oi-full-chart {
    display: flex;
    min-height: 0;
    flex: 1 1 auto;
    flex-direction: column;
  }

  .app-shell.charts-workstation.is-chart-tab .charts-oi-full-chart .oi-chart-multilayout,
  .app-shell.charts-workstation.is-chart-tab .charts-oi-full-chart .oi-chart-layout-grid,
  .app-shell.charts-workstation.is-chart-tab .charts-oi-full-chart .oi-finder-chart-card {
    display: flex;
    min-height: 0;
    flex: 1 1 auto;
    flex-direction: column;
  }

  /* Beats the fixed `min(57dvh, 500px)` set mid-file. */
  .app-shell.charts-workstation.is-chart-tab .charts-oi-full-chart .oi-finder-candle-chart {
    height: auto;
    min-height: 0;
    flex: 1 1 auto;
  }
}
```

- [ ] **Step 2: Verify computed styles at 390px**

Open the app at a 390px viewport and check the **computed** style of
`.oi-finder-candle-chart` — `flex-basis: auto`, `height` resolving to the
remaining space, not `500px`. Source inspection is not sufficient here.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(mobile): give the chart the viewport instead of a fixed height"
```

---

### Task 4: Overview / Chart / Options tabs

The tab bar is what replaces scrolling down to the option chain — it is load-bearing, not decoration. It also sets the `is-chart-tab` class Task 3 depends on.

**Files:**
- Modify: `frontend/src/App.jsx` — the charts view container and `.charts-oi-page-full` render
- Modify: `frontend/src/index.css` — the same appended block

**Interfaces:**
- Consumes: `.charts-oi-page-full` markup, the existing option-chain section.
- Produces: state `mobileChartTab` with values `"overview" | "chart" | "options"`, default `"chart"`, and the `is-chart-tab` class on `.app-shell.charts-workstation` when it equals `"chart"`.

- [ ] **Step 1: Add the tab state and the class**

Re-read the region. Add beside the other charts-view state:

```js
  // Phones reach the option chain by switching tabs rather than scrolling past
  // the chart. That is what lets the chart own every vertical swipe.
  const [mobileChartTab, setMobileChartTab] = useState("chart");
```

and include `mobileChartTab === "chart" ? "is-chart-tab" : ""` in the
`app-shell charts-workstation` className.

- [ ] **Step 2: Render the segmented control above the chart**

```jsx
<div className="mobile-chart-tabs" role="tablist" aria-label="Chart sections">
  {[["overview", "Overview"], ["chart", "Chart"], ["options", "Options"]].map(([key, label]) => (
    <button
      key={key}
      type="button"
      role="tab"
      aria-selected={mobileChartTab === key}
      className={mobileChartTab === key ? "is-active" : ""}
      onClick={() => setMobileChartTab(key)}
    >
      {label}
    </button>
  ))}
</div>
```

- [ ] **Step 3: Gate the panes on the active tab**

Show the chart section only when `mobileChartTab === "chart"`, the option chain
only when `mobileChartTab === "options"`, and the overview content only when
`mobileChartTab === "overview"`. Gate with CSS `display` (not unmounting) for
the chart, so switching tabs does not tear down and rebuild the
lightweight-charts instance and lose the trader's zoom.

- [ ] **Step 4: Style the tabs in the appended block**

```css
  .app-shell.charts-workstation .mobile-chart-tabs {
    display: grid;
    grid-template-columns: repeat(3, minmax(0, 1fr));
    gap: 6px;
    padding: 6px 8px;
  }

  .app-shell.charts-workstation .mobile-chart-tabs button {
    min-height: 40px;
    border: 1px solid rgba(97, 97, 109, .3);
    border-radius: 8px;
    background: transparent;
    color: #b6b6bf;
    font: inherit;
    font-size: .8rem;
  }

  .app-shell.charts-workstation .mobile-chart-tabs button.is-active {
    border-color: var(--terminal-cyan);
    color: #ffffff;
  }
```

Hide the tab bar above 760px so desktop is untouched.

- [ ] **Step 5: Syntax-check, test, verify at 390px**

Run: `cd frontend && node --test src/*.test.js`, then check that switching to
Options shows the chain and back to Chart preserves the chart's zoom.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/App.jsx frontend/src/index.css
git commit -m "feat(mobile): Overview/Chart/Options tabs replace scrolling to the chain"
```

---

### Task 5: Indicators editor as a phone sheet

The editor is a desktop-shaped `<details class="oi-finder-indicators-menu">` popover listing 30+ studies (`App.jsx:19301`). On a phone it needs to be a full-height sheet. The state and the save/reset handlers are reused unchanged — this is presentation only.

**Files:**
- Modify: `frontend/src/index.css` — the appended block

**Interfaces:**
- Consumes: the existing `.oi-finder-indicators-menu` / `.oi-finder-indicators-popover` markup and its `open` attribute.
- Produces: no JS interface change.

- [ ] **Step 1: Style the popover as a sheet below 760px**

```css
  /* The desktop popover is unusable at 390px: 30+ studies in a floating card.
     Below 760px the same markup becomes a bottom sheet - no JS change, so the
     indicator state and the save/reset handlers stay exactly as they are. */
  .app-shell.charts-workstation .oi-finder-indicators-menu[open] .oi-finder-indicators-popover {
    position: fixed;
    z-index: 60;
    right: 0;
    bottom: 0;
    left: 0;
    max-height: 82dvh;
    padding-bottom: calc(12px + env(safe-area-inset-bottom, 0px));
    border-radius: 14px 14px 0 0;
    overflow-y: auto;
    overscroll-behavior: contain;
    -webkit-overflow-scrolling: touch;
  }

  /* 44px targets - the desktop rows are ~24px and miss under a thumb. */
  .app-shell.charts-workstation .oi-finder-indicators-popover label {
    display: flex;
    min-height: 44px;
    align-items: center;
    gap: 10px;
  }

  .app-shell.charts-workstation .oi-finder-indicators-popover strong {
    position: sticky;
    top: 0;
    z-index: 1;
    display: block;
    padding: 10px 0;
    background: #111113;
  }
```

- [ ] **Step 2: Verify at 390px**

Open the Indicators sheet, confirm it fills the bottom of the screen, scrolls
internally, does not scroll the page behind it, and that toggling a study still
updates the chart and persists.

- [ ] **Step 3: Commit**

```bash
git add frontend/src/index.css
git commit -m "feat(mobile): indicators editor becomes a full-height phone sheet"
```

---

### Task 6: Bottom nav to one row

`--mobile-nav-h` is the single source of truth the shell subtracts, so shrinking it returns height to the chart with no other edit.

**Files:**
- Modify: `frontend/src/index.css` — the appended block
- Modify: `frontend/src/App.jsx` — the nav render, to collapse to 5 destinations + More

**Interfaces:**
- Consumes: the existing nav `items` array and `activeView`.
- Produces: state `moreNavOpen: boolean` for the overflow sheet.

- [ ] **Step 1: Reduce the nav height**

```css
  .app-shell {
    --mobile-nav-h: 64px;
  }

  .app-shell.charts-workstation .sidebar nav {
    grid-template-columns: repeat(5, minmax(0, 1fr));
  }
```

- [ ] **Step 2: Show five destinations plus More**

Render the first four destinations plus a **More** button; More opens the
remaining six in a sheet reusing the same `.nav-item` markup.

- [ ] **Step 3: Verify at 390px**

Confirm the nav is one row, every destination is still reachable via More, and
the chart grew by ~32px.

- [ ] **Step 4: Commit**

```bash
git add frontend/src/index.css frontend/src/App.jsx
git commit -m "feat(mobile): one-row bottom nav returns 32px to the chart"
```

---

### Task 7: Build, deploy to the phone, verify

**Files:** none modified.

- [ ] **Step 1: Run the whole frontend suite**

Run: `cd frontend && node --test src/*.test.js`
Expected: all pass.

- [ ] **Step 2: Production build**

Run: `cd frontend && npx vite build`
Expected: clean build. **Without this the phone sees none of the above** — :4173 serves `frontend/dist`.

- [ ] **Step 3: Verify on the phone**

Load app.agxtrade.com and confirm, in order:

1. The chart fills the viewport between the chrome and the nav.
2. A one-finger vertical drag **pans the chart** and does not snap back.
3. A one-finger horizontal drag pans time; pinch zooms.
4. The Options tab reaches the chain — no scrolling needed.
5. The Indicators sheet opens, scrolls internally, and toggles persist.
6. The dead band below the candles is visibly reduced.

- [ ] **Step 4: Report honestly**

Report which of the six checks passed on a real phone and which were only
verified at a 390px desktop viewport. Do not report a phone-verified result that
was only checked in a desktop browser.

**Prerequisite:** none of this is verifiable on a phone until
`docs/superpowers/plans/2026-08-13-chart-api-latency.md` lands — the chart cannot
render without data, and the API currently returns 5xx through Cloudflare.
