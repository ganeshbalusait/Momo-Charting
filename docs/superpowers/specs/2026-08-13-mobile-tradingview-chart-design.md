# Mobile TradingView-style chart — design

**Date:** 2026-08-13
**Tree:** `AgenticAI-Trading 2` (the tree served to phones via :4173 → app.agxtrade.com)
**Status:** awaiting review

## Problem

On a phone, `app.agxtrade.com` cannot be scrolled, and the chart cannot be
panned the way TradingView's can. The two problems are the same problem: the
chart and the page are fighting over one finger.

Three symptoms were reported together — nothing moves; the page scrolls but the
chart does not pan; the chart pans but the page is stuck. All three are
reachable from the current code depending on which pane is mounted and where
the finger lands.

## Current state (audited, not assumed)

- A phone shell already exists: `index.css:13106`, `@media (max-width: 760px)`
  sets `--mobile-nav-h: 96px` and
  `.app-shell.charts-workstation .workspace { height: calc(100dvh - var(--mobile-nav-safe)) }`.
  The bottom nav is 10 destinations on two rows.
- `.charts-workstation .workspace` is `overflow: hidden` unconditionally, so the
  workspace itself never scrolls. The scroll container is
  `.scanner-results-view` (`overflow-y: scroll`).
- `c527da9` set `handleScroll.vertTouchDrag: true` so the chart owns vertical
  swipes. Because the chart sits *inside* `.scanner-results-view`, that
  container can no longer be scrolled by dragging over the chart — and on a
  phone the chart is most of the screen. **This is why the app does not
  scroll.**
- Phone chart height is fixed at `min(57dvh, 500px); min-height: 330px`
  (`index.css:1256`), independent of how much viewport is actually left.
- The indicator editor is a desktop-shaped `<details class="oi-finder-indicators-menu">`
  popover (`App.jsx:19301`) listing 30+ studies.
- `fitVisibleCandlesToPriceScale` fits the price range to more than the candles,
  leaving a large empty band on a phone (measured ~24% of chart height in the
  reference screenshot: axis spans 302.60→300.80 while every candle sits between
  301.40 and 302.50).
- The custom body pan is mouse-only (`pointerType !== "touch"`), so a touch pan
  never sets `manualTimeNavigationRef` / `manualPriceNavigationRef`, and the
  autoscale pass can snap candles back under a moving finger.

## Design

### 1. Shell — the chart owns the viewport

Below 760px the charts view becomes a fixed, non-scrolling column. Nothing
behind the chart scrolls, so there is no gesture to arbitrate.

```
[status strip]     auto     MARKET OVERNIGHT · time · TOS · ALPACA · bell
[ticker row]       auto     AAPL ▾ · APPLE INC · $301.40 · −1.15% · ★
[segmented tabs]   auto     Overview | Chart | Options
[timeframe strip]  auto     3m 5m 10m 15m 30m 1h 2h 4h D   (scrolls sideways)
[chart controls]   auto     LIVE · Schwab/TOS ▾ · clock · − · + · ⛶
[CHART]            1fr      flex: 1 1 auto; min-height: 0   ← owns all gestures
[legend chips]     auto     EMA · VWAP · Cloud
[toolbar]          auto     Indicators | Draw | Alerts | More
─────────────────────────
[bottom nav]       ~64px    Home · Chart · Options · Watchlist · More
```

Two decisions carry this section:

**The chart is `flex: 1 1 auto; min-height: 0`, not a fixed height.** Replacing
`min(57dvh, 500px)` means the chart absorbs whatever the chrome does not use, so
every row trimmed becomes chart. A fixed height cannot do that and is what
forces the current sandwich.

**The Options tab replaces scrolling down to the chain.** This is what makes
"the page does not scroll" a non-issue rather than a regression: you switch
tabs instead of scrolling. On the Chart tab, `.scanner-results-view` becomes
`overflow: hidden`; on Overview and Options it keeps scrolling normally.

### 2. Reclaiming the dead band inside the chart

**Correction (2026-08-13, after reading the code):** an earlier draft of this
spec blamed OI levels for the empty band. That was wrong.
`fitVisibleCandlesToPriceScale` already fits to visible candle high/low only and
deliberately excludes walls, bubbles and levels — there is an explicit comment
saying so. The real cause is two compounding paddings:

1. `pricePadding = Math.max(candleSpan * 0.10, 0.03)` (`App.jsx:16869`) pads the
   range by 10% above **and** below, making the visible range 120% of the
   candle span.
2. `OI_CHART_PRICE_SCALE_MARGINS = { top: 0.12, bottom: 0.12 }` (`App.jsx:333`)
   then maps that range into only the middle **76%** of the pane.

Together the candles occupy about `(1 / 1.20) × 0.76 ≈ 63%` of the pane. The
remaining ~37% is structural whitespace. On a desktop pane that is breathing
room; on a phone it is the void visible in the reference screenshot.

The fix is phone-only, leaving desktop framing untouched:

- Reduce the fit padding to **4%** of candle span below 760px.
- Reduce the price scale margins to `{ top: 0.06, bottom: 0.16 }` below 760px —
  a larger bottom margin than top, so the volume histogram (already at
  `{ top: 0.78, bottom: 0 }`, `App.jsx:15785`) keeps a clear band instead of
  overlapping the candles.

That lifts candle occupancy from ~63% to about `(1 / 1.08) × 0.78 ≈ 72%`.

### 3. Touch ownership

Add a touch path that claims `manualTimeNavigationRef` / `manualPriceNavigationRef`
when a touch pan begins, so the autoscale pass treats it as a deliberate view
and stops snapping candles back under the finger. Double-tap returns to auto.

This is the known second half of `c527da9` and is expected to be the cause of
residual jumpiness.

### 4. Indicators editor on mobile

Below 760px the `<details>` popover becomes a full-height bottom sheet opened
from the toolbar's **Indicators** button:

- sticky header: filter field, `Save <tf> for all tickers`, `Reset <tf>`
- the study list as a scrollable list of 44px rows with switches
- `overscroll-behavior: contain` so its scrolling never leaks to the page
- dismissed by swipe-down or an explicit close control

The underlying state (`indicatorSettings`, the profile save/reset handlers) is
reused as-is; this is a presentation change, not a new settings model.

### 5. Bottom nav

10 destinations on two rows collapse to 5 on one row — Home, Chart, Options,
Watchlist, More — with **More** opening the remaining six in a sheet.
`--mobile-nav-h` goes from `96px` to `64px`, which is the single source of truth
the shell already subtracts, so this returns 32px to the chart with no other
edit.

## Implementation constraints

- **CSS goes in one new `@media (max-width: 760px)` block appended at the end of
  `index.css`,** with `.app-shell.charts-workstation …` prefixes. The mid-file
  mobile block is outranked by later unconditional `.charts-workstation` rules;
  editing it silently does nothing.
- Everything is scoped to `max-width: 760px` + `charts-workstation`. Desktop
  layout must not change.
- `.scanner-results-view { overflow: hidden }` must apply **only** on the Chart
  tab, or Overview and Options become unscrollable.
- The phone is served `frontend/dist` via :4173, so any phone-visible change
  needs `npx vite build`. :5173 is desktop dev only.

## Testing

- Vitest for the pure logic: visible-candle price-range fit, including the
  bounded OI-level expansion.
- Computed-style verification at a real 390px viewport — not source inspection,
  given the cascade trap.
- Phone verification against :4173 after a build.

## Out of scope

The API latency work (`/api/oi-finder-chart` returning in 0.76–34.5s, causing
Cloudflare 5xx and the "API is unavailable right now (server error)" banner) is
tracked separately. It is a prerequisite for verifying any of this on a phone,
because the chart cannot render without data, but it is not part of this design.
