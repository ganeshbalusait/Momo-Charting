# September 29 TOS/app scanner discrepancy

## Verified in the live TOS terminal

The ALL group has hidden rows. Scrolling it confirmed Last >= $3, 4h EXT volume at least +0.5% versus two bars ago, and 1h EXT close at least +0.30% versus two bars ago. The earlier pasted 0.25% is not the displayed saved configuration. Both Price_Change gates are enabled.

The built-in Price_Change editor uses `100 * (price / price[length] - 1)`. The configured length is 2. A refreshed TOS scan returned CRM, LASR, PM, WELL and SHEL; the earlier seven-ticker screenshot is not a fixed expected result.

## Changes

Restored VOLUME_CHANGE_BARS_AGO from 3 to 2 and matched the ratio-first percentage expression. Kept the verified 0.30% hourly threshold. The three-bar workaround must not be used to compensate for different candle aggregation or feed data.

The ANY families/timeframes and RVOL thresholds match the supplied specification. The squeeze firing state machine matches its supplied recurrence. This is source review, not proof of numerical parity for every bar. Displayed RVOL cells retain historical readings; the scan checks the latest bar.

## Replay evidence

Schwab 5-minute history was folded with the app's existing aggregation through September 29 before 20:00 ET. This is not a simultaneous capture of TOS scanner inputs. Raw bars and gate-comparison.json are retained alongside this report.

| Symbol | 4h volume change vs 2 bars back | vs 3 bars back |
|---|---:|---:|
| CHPT | -98.92% | +109.38% |
| META | -94.67% | +29.06% |
| TEAM | -75.73% | +34486.37% |
| CLS | -98.82% | +83.18% |
| GME | -87.50% | +358.19% |
| RXRX | -98.31% | +112.48% |

All six pass the old three-bar volume gate and fail the literal two-bar gate in this replay. This identifies a concrete false-match mechanism, not every discrepancy.

CRM's reconstructed hourly change is +0.288625%, below the actual 0.30% TOS setting despite CRM appearing in TOS. SHEL, PM and WELL also disagree at the gate level. These remaining differences require matching the input candles, session boundaries, and scan timestamp; weakening the threshold to make them pass is not justified. Recent app changes use Schwab intraday history with supplemental/fallback data, which is still not evidence of identical TOS scan inputs.

## Validation

- Focused scanner suite: 60 passed.
- Complete MomX suite: 1,173 passed, 12 deprecation warnings. Initial run encountered Windows temporary-directory permissions; rerun with a project-local temporary directory passed.
- Frontend production build passed; standard bundle-size warning remains.
- Scanner rendered at 1440x900 and 390x844; matches-only interaction checked and restored. No captured browser error logs. An earlier browser timeout recovered.
- No chart or indicator UI changes were made.
- Scanner worker restarted September 29 at 20:26 ET. Fresh Watchlist rebuild was still pending when this section was written.

Exact live ticker parity is not yet established. Formula corrections and passing tests must not be described as proof of identical TOS results.

Post-restart check: fresh Mag7 board at 20:28:22 ET had no scan matches; META now correctly fails the literal four-hour volume gate. The full Watchlist was still serving its flagged stale 20:20:55 ET disk snapshot at the last check. Its old 27 matches are not evidence about the corrected worker.

## Computer-use follow-up at approximately 21:39 ET

Watchlist rebuild completed and is fresh. App matches DOCU and LASR. Clicking Scan in the live TOS terminal returned WELL and LASR. Thus WELL is a missing match and DOCU an extra match; parity remains incomplete.

Opened WELL on the existing TOS charts and inspected 4h and 1h. The last displayed hourly candle is September 29 at 16:00 (open/high 233.04, low 232.90). The saved Schwab replay instead contains 16:00 close 232.90 plus nonzero-volume 17:30/17:50 records, and zero-volume 18:30/19:00 records. The additional bars alter offset comparisons. Dropping only zero-volume records still leaves the reconstructed hourly change near +0.0086%, below the saved scan threshold. This does not establish why TOS excludes the later records, and does not justify changing the global aggregation clock or stripping valid after-hours volume. Exact parity requires resolving trade eligibility/session differences in the inputs, not tuning the percentage threshold to the two observed tickers.

No additional application code was changed during this follow-up.
