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

## Every feature gets unit, integration and end-to-end tests

Ganesh's rule (2026-11-04): every new feature or fix ships with all three layers of tests, in the same commit. The how is in the `feature-testing` skill (`AgenticAI-Trading 2/.claude/skills/feature-testing/SKILL.md`).

- **Unit**: the logic on its own. Python in `tests/test_*.py` (pytest), screen logic in `frontend/src/*.test.js` (node --test).
- **Integration**: the real route over HTTP with the real modules behind it, using `tests/api_harness.py` (`running_api()`) for api_server and `momx_worker.Handler` for the scanner worker. Fake only what leaves the PC (Schwab, Alpaca, the broker, AI, news sites).
- **End to end**: the real app in a hidden browser, clicking what the trader clicks, at desktop and phone width. Specs go in `tests/e2e/*.e2e.test.mjs` and use `tests/e2e/harness.mjs`, which blocks every write unless the spec answers it. Run with `node --test --test-concurrency=1 "tests/e2e/*.e2e.test.mjs"`.
- Between 09:30 and 16:00 ET on weekdays, run only the focused files for the change; end-to-end specs skip themselves then. Run full suites outside those hours.
- Add the feature and its three test files to `tests/FEATURE_TESTS.md`.
- A test that finds a real bug stays. Fix the bug, or mark the test as a known failure with the reason and tell Ganesh. Never weaken the test.
- The handoff names the layers that ran and any that could not.

## Release notes are mandatory

Every fix or feature that ships gets an entry at the TOP of `frontend/public/release-notes.json`, in the same commit, stamped in ET, written as what the trader would notice (not code detail). The pre-commit hook (`scripts/check_release_notes.py`) refuses a commit that changes app code without a new entry. Do not bypass it with `--no-verify` unless Ganesh asks. Rebuild the frontend afterwards so the installed app sees the entry.
