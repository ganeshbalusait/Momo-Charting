# Named chart grids (MomoX-style "Save grid as…")

**Date:** 2026-08-09 · **Status:** Implemented and verified

## Problem

The workspace persisted exactly one unnamed state. The user wanted MomoX's model:
save the current grid under a name ("mag7", "main"), list saved grids, load one
into the current window on demand. Two entangled bugs shipped with this design:
the "Save layout" tooltip promised zoom restore that the Aug 7 default-zoom
decision deliberately removed, and a corrupted captured price range could
survive session restore almost intact (4h pane reopening with a 240-470 axis on
a $313 symbol — the 12x clamp allowance).

## Design

- A grid = named snapshot of `{ workspace, indicators }`:
  layout id, all panel symbol/timeframe/link-groups, active/wide panel,
  geometry (splits, column widths, heights), plus the indicator toggle set.
  Zoom is intentionally NOT captured: charts always reopen at the default
  ~72-candle framing (Aug 7 user decision, reaffirmed).
- Storage: one localStorage key `oiFinderChartGrids` mapping name → grid.
  Same-name saves overwrite. Cap 24 grids (oldest dropped). Names trimmed,
  max 40 chars.
- Load path: write the grid's workspace to the live workspace key (plus legacy
  layout/panel keys and the indicator toggles), then `location.reload()` — the
  normal boot path applies everything. No second sync path that can drift.
- UI: "Grids" button beside "Save layout" opens a popover: name input +
  "Save grid", then a "Load a saved grid into this window" list with a × per
  entry. Outside-click and Escape close it.

## Files

- `frontend/src/chartWorkspaceState.js` — `listChartGrids`, `saveChartGridAs`,
  `loadChartGrid`, `deleteChartGrid`, `normalizeChartGridName`,
  `OI_CHART_GRIDS_STORAGE_KEY`.
- `frontend/src/chartWorkspaceState.test.js` — round-trip, overwrite,
  rejection/corrupt-storage tests.
- `frontend/src/App.jsx` — toolbar UI + handlers; Save-layout tooltip corrected
  (no zoom promise); session-restore price clamp tightened 12x → 3x.

## Follow-up same day: server sync + MomoX layout parity

- Grids are server-backed (user request: "I want everywhere, not only in the
  browser"). `GET/POST /api/chart-grids` persist the full grid set as one JSON
  document in the `app_settings` table (`chart_grids` key) — the same store the
  watchlists use. Validation server-side: name ≤40 chars, workspace required,
  cap 24 grids / 500KB.
- Sync model: server is authoritative at boot (local replaced when the server
  set is non-empty; a non-empty local set seeds an empty server). Every local
  save/delete POSTs the full local set — last write wins. Offline keeps
  working from localStorage.
- Layout parity with MomoX: added `three-rows` (3 stacked), `four-rows`
  (4 stacked), `six-columns` (6 across/parallel) to `OI_CHART_LAYOUTS` and the
  workspace validator. Picker icons derive from count+columns automatically;
  `six-columns`/`four-columns` get draggable column dividers via the existing
  single-row resizable path.

## Verification

258/258 frontend unit tests; `npx vite build` green; live browser check on
:5173 — saved a grid, mutated the workspace, loaded the grid (layout/panels
restored through reload), deleted it; zero console errors.
