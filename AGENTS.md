# Project change validation

Treat this checklist as a standing requirement for every code change in this repository.

## Required verification

- Run the focused tests for the changed feature and the complete relevant test suite.
- Run the production build; do not hand off code that only works in development mode.
- Smoke-test the affected workflow in the browser at desktop and reduced widths.
- Verify single-chart, multi-chart, big-screen, and fullscreen modes for chart changes.
- Verify all supported chart timeframes and at least two materially different tickers.
- Verify ticker/timeframe switching, live updates, zoom, pan, crosshair, and saved layouts do not regress.
- Verify indicator controls, options, profile save/reset, and cross-ticker persistence for indicator changes.
- Check console/runtime errors, duplicate timestamps/signals, stale data after switching, layout clipping, and interaction lag.
- Compare translated TOS/TradingView studies against their source formulas, timing, labels, colors, and visibility rules.
- Report what was tested and any test limitation in the final handoff.

Do not consider a visual change complete from a screenshot or successful compilation alone.
