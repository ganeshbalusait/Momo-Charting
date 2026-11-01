# Scanner refresh recovery and compact gamma — October 30, 2026

## What the trader reported

The mobile scanner showed `Fetch is aborted`, zero tickers and `UPDATED never`;
a later screenshot showed `Lists` instead of the selected Watchlist. The trader
also requested that white/orange gamma clouds stop extending across candles.

## Scanner causes and fixes

1. **A read deadline was incorrectly tied to the 15-second poll interval.**
   A response taking longer than that was canceled, including while reading
   its JSON body. The catch block displayed Safari's raw cancellation text.
   `scannerBoardRead.js` now allows 45 seconds per attempt, covering headers
   and JSON; retries one timed-out/unexpectedly aborted attempt using a fresh
   signal; and gives a plain retry message. Ordinary network drops retain
   the existing bounded retry policy. Polling remains 15 seconds, with the
   existing single-request-in-flight guard.
2. **The main board read bypassed the shared navigation read wrapper.** It
   now uses that wrapper too. Intentional navigation, list or direction
   cancellations are silent. Request-key checks reject obsolete responses
   and obsolete queued reads. The actual start time of each fetch, including
   retries, still reaches the data-source-switch acceptance check.
3. **Empty list metadata cleared the selected list.** The code kept the
   displayed list names but still set the active selection to `null`, which
   made the current board/cache key stop matching. Empty/incomplete metadata
   now preserves the current selection. A genuinely removed named list still
   follows the existing HTTP 400 recovery behavior.
4. **A valid board with only `rest` rows was treated as empty.** When no
   stocks passed the scan, the server could still have the full Watchlist in
   `rest`. The live view and cache ignored it, showing either an empty board
   or obsolete cached matches. Those snapshots now retain their actual data
   and timestamps, including through storage/reload. With **Scan matches
   only** enabled the visible table can legitimately have zero rows; disabling
   it shows the nonmatching Watchlist names. This does not latch old setups
   or invent new scan rules.

Last good rows are retained on interrupted reads. Only a complete successful
snapshot replaces them. Retried/cached rows do not receive a fabricated fresh
market-data timestamp. The read timeout improvement is not a claim that the
underlying network, broad scan or main API has become fast.

## Runtime evidence

At about 09:47 ET, the actual Watchlist service returned **89 matches and 279
other rows**. Direct worker reads took 2.7 seconds; the same board through the
main API took 9.2 seconds in that capture. Lists took 2.3/7.8 seconds through
those paths. A phone download, queued connections and UI load could exceed
the old 15-second deadline. See `artifacts/scanner-disappearing-probe.json`.
This establishes that the server had tickers while the phone was empty; it
does not prove the precise network cause of every screenshot.

The requested actual market-hours audit ran at **09:53:06, 09:53:45 and
09:54:10 ET**. In each capture, Watchlist BULL/BEAR and Mag7 BULL/BEAR contained
368/368/10/10 rows, with no grade-check disagreement, duplicate symbol or
out-of-range H/L. The separate AXP/CBOE/AAPL/TSLA capture includes actual source
times, full-precision H/L and quotes. Evidence is in
`scanner-setup-regular-session-0953.json` and `scanner-regular-AXP-CBOE.json`.
These checks do not establish exact independent live TOS parity or future
trade profitability; source freshness and broad-scan latency remain relevant.

The actual production phone-width browser check completed at **10:07 ET**
without intercepted market-data replies: 77 visible rows, no duplicate
symbols or console/runtime errors, a bounded quote lease containing the
top H/L name, working H/L sort, and a readable freshness line. Cold entry
took **74.0 seconds**, including an automatic saved-layout reload about
26 seconds after the first navigation. A repeat load took **13.5 seconds**.
The first board request in the reloaded page took 23.3 seconds, mostly
waiting for headers. These measurements do **not** establish a speedup.
Source times still varied across rows (09:30–10:00 ET in the captured
payload), so overall cold loading and broad-scan freshness remain open.
See `artifacts/scanner-setup-live-handoff-phone-smoke.json`.

## Compact gamma rendering

White/orange gamma clouds now remain around short bars at the right edge.
Bars use 20% of the pane width, capped at 72 px on phone-sized panes or 120 px
on larger panes. Halos add 12 px and are 12–18 px high, replacing full-pane
24–40 px bands. Colours, exposure formulas, relative concentration ratios,
labels, cyan strike guides, saved settings and data requests are unchanged.
See `chart-gamma.md` for the model and data limitations.

## Verification

- Focused scanner/cache/read checks: **160 passed**. Gamma checks: **13 passed**.
- Complete frontend suite: **1,568 passed**. The production build succeeded
  after the new top ET release entry, and built release notes match source.
- Scanner regression browser tests: **6 checks each at desktop and 390 px**.
  They cover an 18-second response; repeated Safari-style `AbortError` plus
  empty list metadata; preservation of last good rows; foreground recovery;
  cancellation/stale ownership during BULL/BEAR switching; leaving/returning;
  and a healthy zero-match snapshot with Scan matches only disabled.
- Gamma browser matrix: **36 desktop and 34 phone checks**, all passed,
  including all 11 supported timeframes on SPY/AAPL, native drawing bounds,
  expiry/basis controls, zoom/pan/crosshair, single/two-chart, big-screen,
  fullscreen, saved layouts and indicator options/save/reset/persistence.
- The gamma matrix replays captured provider data. Its zero-volume case
  explicitly zeroes volume to exercise the empty-state control and suppresses
  live streams. It is a functional regression test, not live market proof.
- Separate desktop/phone replay checks append a new REST candle with gamma
  active and repeat the same tail. Each adds exactly one candle, keeps AAPL
  ownership and gamma levels, and produces no duplicate timestamp or runtime
  error. These use the chart's canonical aggregation for the baseline and
  suppress real streams; they do not validate the physical provider stream.
- Scanner interruption tests inject Safari's error in Chromium and replay
  board/list replies. No physical iPhone/WebKit device was available. Actual
  market API captures are separate from these fixtures.
- Backend trading code was not changed in this update; the earlier complete
  relevant backend suite passed 1,725 tests with one retired-module skip.
  This update does not claim to resolve all existing cold-start latency or
  the previously documented Monthly Skittles TOS comparison.

No trades, server lists, saved trader profiles or historical observations
were altered by the browser fixtures.

The subsequent one-time market-hours follow-up found and fixed an additional
late saved-grid navigation race. See
[scanner-market-hours-followup.md](scanner-market-hours-followup.md) for the
new actual-session samples, desktop/phone timings, validation and remaining
latency/data limitations.
