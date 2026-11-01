# Scanner market-hours follow-up — October 30, 2026

## Status

| Task | Result |
| --- | --- |
| Late saved-layout sync interrupts the scanner | Fixed. An explicit sidebar/mobile navigation choice outranks the pending startup redirect. The default grid is retained and still applies on later Charts entry. |
| Setup/A+ grade consistency | Three additional actual regular-session samples passed the current grade checks across Watchlist BULL/BEAR and Mag7 BULL/BEAR: 368/368/10/10 rows in every sample. This is code/rule consistency, not proof that all studies exactly match an independent TOS terminal. |
| H/L formula | AXP, CBOE, AAPL and TSLA actual persisted 30m inputs were folded into the configured Central-clock 2h EXT bars. Independently applying the supplied formula to the last eight bars matched the column's unrounded degree within 1e-12 for all four. |
| Live quote leases and switching | Actual desktop and 390px production-browser checks passed: top visible H/L leader included in the bounded quote lease; BULL/BEAR switching; no duplicate tickers; readable freshness labels; working header sort. |
| Retained intrabar highs/lows and price reversals | Actual timestamp-valid quote captures contain reversals, including AMZN and TSLA on desktop. The deterministic AXP/CBOE replay separately checks a new high followed by a retreat, range retention, resulting order and disconnected fallback. |
| Cold/repeat performance | Still open. The extra page switch after an explicit navigation is fixed, but API latency and automatic initial grid restoration remain material. Timings below are observations, not a controlled speedup claim. |
| Uniformly fresh study data | Still open. New quotes do not repair old OHLCV/momentum inputs. Source windows and scan/serve/receipt times remain separate and visible. A fixed provider entitlement delay is unconfirmed. |
| Independent TOS parity | Unverified. In particular, the documented Monthly Skittles comparison remains unresolved. |

## New confirmed navigation defect

The root app started a chart-grid metadata read on boot. Its eventual reply
could unconditionally steer to Charts after the trader had already clicked
MomX (or reselected the remembered page). Mounting Charts then restored the
default grid and reloaded the page. This added loading and interrupted the
scanner even though its requests were healthy.

The app now records explicit main-navigation choices and rejects this late
startup redirect after such a choice. It does not remove the saved grid,
consume its applied stamp, change indicator settings or rewrite observations.
Initial default restoration keeps its prior behavior when no page has been
explicitly chosen. Manual later Charts entry still restores the pending grid.

`scanner-grid-navigation-smoke.mjs` delays the grid reply until after MomX is
chosen. Replaying the **old guard inside the browser only** reproduces the
redirect/reload. The new guard passes at desktop and phone widths, retaining
both scanner rows and later saved-grid restoration. No deployed asset was
rolled back. These are controlled fixtures, separate from actual-market tests.

## Actual-session evidence and timings

The read-only audit was rerun at **10:17:09, 10:17:31 and 10:17:49 ET** with
zero grade disagreements, duplicate symbols or invalid H/L values. The earlier
09:53 audit was preserved in `scanner-setup-regular-session-0953.json`; the new
samples are in `scanner-setup-regular-session.json`.

Production-browser checks at about **10:26–10:29 ET** used no market-data
interception, with build/test suites stopped:

| Measurement | Desktop | Phone width (390px) |
| --- | ---: | ---: |
| Cold entry until visible scanner rows | 12.945s | 11.020s |
| Repeat reload until visible scanner rows | 24.948s | 17.835s |
| BULL to BEAR, response and rows | 14.118s | 13.037s |
| BEAR to BULL, response and rows | 23.045s | 13.956s |
| H/L header interaction to ascending state | 1.616s | 0.773s |
| Initial visible rows | 51 | 45 |

The first explicit entry had no extra grid reload. The repeat reload resets
the in-memory navigation choice and can apply the still-pending default,
which remains the existing intended initial-restore behavior. Header times
include Playwright actionability/scrolling; they are not isolated React times.

Earlier phone cold entry measured 74.0s including an automatic grid reload.
The older live test could skip clicking navigation before it was visible;
the corrected test waits for and performs the actual trader choice. Different
navigation paths, snapshots and backend load prevent treating 74.0s versus
11.0s as a controlled before/after performance claim. The deterministic
late-reply regression is the specific evidence for this fix.

One phone source-time trace:

- Scan started **10:24:57.745 ET**, completed **10:27:29.901 ET** (~152s).
- Source 5m starts ranged **10:05–10:20 ET**; these mark candle windows,
  not provider arrival times.
- API served **10:28:17.378 ET**; browser completed reading at
  **10:28:18.696 ET** (~1.3s later).
- The latest displayed study window ended at 10:25 ET; some individual
  study windows were appreciably older. Newer last-price quotes are separate.

This confirms both scan/history lag and HTTP/boot latency. It does not
identify one fixed provider delay. The existing shared history budget and
safe stream-overlap requirements cannot be bypassed by fabricating bars.
The global page header is separate from scanner source/receipt metadata;
its premarket/08:51 display in the desktop capture was not validated as a
current session clock by this scanner change.

## Verification and evidence

- Focused workspace/first-visit/read/cancellation tests: **42 passed**.
- Complete frontend suite: **1,568 passed**, no failures.
- Production build succeeded after the new top ET release entry.
- Navigation-race fixture: two checks each on desktop/phone; old-guard
  reproduction fails as expected, fixed guard passes with no runtime errors.
- Scanner precision/reversal replay: **8 checks each on desktop/phone**,
  including numeric ties, sort cycle, source-age explanations, high/retreat,
  disconnected fallback, BEAR isolation and visible-leader quote leases.
- Chart matrix rerun after the UI fix: **36 desktop and 34 phone checks**,
  all 11 supported timeframes, SPY/AAPL, ticker switching, single/two-chart,
  big-screen/fullscreen, zoom/pan/crosshair, indicator controls/options,
  saved layouts and profile save/reset/cross-ticker persistence. Both
  controlled REST-update checks added exactly one candle, retained owner
  and gamma, and did not duplicate the repeated tail. These use captured
  provider data with explicit fixtures and suppressed streams, not a live
  provider-stream claim.
- No backend code changed. The prior complete relevant backend suite passed
  1,725 tests with one retired-module skip; it was not rerun for this UI guard.
- Actual-service grade audit, actual-browser captures and stored-data formula
  checks are distinct from chart/scanner replay fixtures. Phone width is
  Chromium; no physical iPhone/WebKit test was available.
- One initial desktop replay remained on the sign-in screen while other
  browser checks were running and never requested a board. A later rerun
  passed all eight checks. The driver now waits for navigation rather than
  silently skipping a click before boot has finished; no authentication
  behavior or trading rule was changed to force a pass.

Evidence lives in `artifacts/`:

- `scanner-grid-navigation-{desktop,phone,before-desktop}.json`
- `scanner-setup-live-heartbeat-{desktop,phone}-smoke.json` (including browser
  receipt times, actual quotes, source/scan times and navigation history)
- `scanner-heartbeat-source-check.json` (actual eight-bar windows and arithmetic)
- `scanner-heartbeat-real-reversals-{desktop,phone}.json`
- `scanner-grid-focused-tests.log`, `scanner-grid-full-frontend.log`, and
  `scanner-grid-production-build.log`
- `scanner-grid-setup-{desktop,phone}.log`,
  `scanner-grid-chart-{desktop,phone}.log`, and
  `scanner-grid-live-{desktop,phone}.log`

No trades, server lists, saved user profiles or historical observations were
changed. No reliable profit prediction or recommendation to trade the top
ticker follows from these checks. This was a one-time market-hours follow-up.
