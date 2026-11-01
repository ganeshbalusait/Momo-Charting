# Scanner freshness correction — October 30, 2026

The reported `Updated 07:30:09 ET / Data 07:25 ET · 5m old` mixed two different clocks. The scanner's five-minute candle timestamp is its **start**: 07:25 represents the 07:25–07:30 window. Age measured from that start does not establish five minutes of feed latency. The scheduled window end also does not prove when a final update reached the provider or app.

## Confirmed defects and changes

1. The frontend's live-view and local-storage field whitelists dropped `freshness`, `scanStartedAt`, `scanCompletedAt` and `dataSource`. The visible line consequently fell back to the older stamp-age label. The metadata now survives API → live view and API → cache write → cache read → warming view. Cached rows retain their own completion time rather than inheriting a newer empty response's clock. Browser receipt and window-end numeric timestamps now format correctly.
2. The backend stamped row source time after saving shared scan records. A borrowing list could lose the actual candle timestamp and substitute `rvol[5m].barAt`, which describes when volume arrived. Source time is now attached before scan sharing and retained through the row contract. Volume timing is never substituted for unknown source time.
3. The TOS request allocator retained old positive demand for up to three minutes after an entire tape had nothing due. Live diagnostics initially showed only 60 of 90 available calls used while intraday tapes were waiting. Only the central refresher, which sees the whole shared symbol store, may now clear an idle tape's reservation immediately. Small-list zero requests still cannot starve a larger list. The cap remains 90 requests per rolling minute.

The header now identifies `5m bar 07:25–07:30 ET`, with older row starts still disclosed. Old windows remain visibly identified; the tooltip preserves raw start, scan, API and browser times and the provider limitations. The line wraps on narrow screens.

## Evidence and limits

- Before: at 07:31:21 ET, Watchlist scan completion was 07:30:10 and its newest candle start 07:25. The scheduled window ended 07:30. That is about 1 minute 21 seconds since the window ended, not a measured 6 minute 21 second provider delay. Actual source arrival time was not available.
- Before budget sample: 30 five-minute calls + 30 thirty-minute calls; old daily demand still reserved a share.
- After budget sample: 45 five-minute calls + 32 thirty-minute calls + 13 daily calls = 90. When daily work became due again, all three tapes correctly returned to 30 each. This verifies better use of the existing cap, not a guarantee of instantaneous updates for every ticker.
- A deterministic browser replay reproduced the screenshot clock, advanced the next window through the normal poll without reload, then reloaded against the actual worker. Desktop and 390px phone checks passed with no console/runtime errors or freshness-line clipping.
- Actual live screenshots showed Watchlist newest windows of 07:45–07:50 on desktop and 07:40–07:45 on the phone check taken earlier. Individual rows still vary; some inactive/illiquid names retain prior-session candles. Source refreshes remain rate-paced, and large scan builds can add processing time. These fixes do not certify every row as live.

## Validation

- Focused backend: 140 passed, including borrowed-source-time preservation, RVOL-time exclusion, request-budget reuse, the existing rate cap and small-list fairness.
- Frontend live/cache/clock focused checks: 146 passed.
- Complete relevant backend suite: 1,704 passed, 1 retired module skipped, 22 existing UTC deprecation warnings.
- Complete frontend suite: 1,551 passed.
- Production build: passed in 49.12 seconds; newest release note is stamped 07:45:12 ET and the built note matches source.
- Browser checks: desktop 1440×1000 and phone width 390×844, including normal polling and actual-worker replies. No physical iPhone was used.
- The scanner worker was reloaded; its new replies expose `barTimestampRole: start` and `barIntervalSeconds: 300`. Source formulas, trading rules, chart settings and the 90-call cap are unchanged.

Evidence files: `artifacts/scanner-audit-morning-delay.json`, `scanner-audit-morning-after.json`, `scanner-audit-morning-final.json`, `scanner-morning-{desktop,phone}-smoke.json`, and the corresponding focused/full-suite/build logs.
