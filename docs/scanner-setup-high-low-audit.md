# Scanner Setup and High/Low audit — October 30, 2026

## Trader workflow and scope

Keep the trader's TOS-style order: HighLowGraph descending on BULL and
ascending on BEAR, with header sorting and existing filters preserved. H/L
is price position within eight 2-hour EXT bars, not a momentum score or a
profit probability. The hourly **H/L⚡** control is a different, bull-only
ScannerX3 calculation. No new entry, exit, or profitability rules were added.

## Confirmed defects and corrections

| Defect | Evidence | Correction |
|---|---|---|
| A+ rejected dark H/L shades | `grade.py` accepted literal green/red, while the supplied Sep 25 HighLowGraph ladder uses dark green above +0.5 and dark red below −0.5. | Both shades on the required side satisfy the original midpoint check. New grade responses identify `draft2-hl-ladder-2026-10-30`. |
| False H/L ties | AXP's 325.68 inside 320–325.69 gives approximately 0.996485; CBOE at its 321 high gives 1. Both displayed 1.00 and sorted alphabetically. | Preserve unrounded `degree` for ordering, retaining compact rounded display. Exact numeric ties still use symbol; TOS's exact-tie ordering has not been independently verified. |
| New intrabar highs/lows forgotten | A 100–110 range extended to 114, then retreated to 110. Reusing the original bounds incorrectly returned +1 instead of +0.428571. | Retain observed extremes for the same forming 2h `barAt`, including hidden rows and board rebuilds. Reset on a new window; older payloads reset on row replacement. |
| Old live quotes kept overriding boards | The last-price map retained missing/disconnected symbols and discarded quote timestamps. | Validate the actual last-price time, expire after 90 seconds, reject future/out-of-order/older-than-source updates, and fall back to the board. Chart-stream fallback uses its own last-price receipt time, not an unrelated held trade timestamp. |
| Wrong names got live quotes | The fastlane leased the original %Chg-ranked `rows`, while H/L sorting could put a `rest` row on top. | Lease the actual visible leaders after sorting/filtering, capped at the same 60 symbols. |
| Hidden bull controls affected BEAR | Bolt polling stops and controls hide on BEAR, but the stored bull bolt filter and H/L⚡ state still affected `visibleRows`. | Ignore bull-only filtering, ordering and symbol bolts on BEAR; preserve the trader's saved BULL choices. |
| Setup filters disagreed with Record marks | AI filtering used the wall clock rather than the supplied observation time; JEV filtering accepted a timestamp without an accepted TAKE record. | Use `aiTags(row, nowMs)` and `jevRecordTag(row, nowMs)` for the corresponding filters. JEV remains disabled as requested. |
| Older studies looked current beside newer quotes | At 08:09 ET CBOE's source 5m study bar was 07:30, while a quote update was newer. | Show a compact, tappable **Data HH:MM** label after the study window ended at least ten minutes ago. Keep the row and its actual rules visible; do not silently turn the label into an entry veto. |
| Canceled reads caused a second failed worker reply | Browser navigation/timeout produced a Windows 10053 stack while writing the response and another while attempting a 500 reply. | Close canceled worker responses on BrokenPipe/Reset/Aborted errors. Other I/O errors still propagate. |

History files and existing paper trades were not rewritten. The read-only
replay captured 33,547 BULL and 26,135 BEAR observations across the latest
three available days in Watchlist and Mag7: 1,800 and 1,227 observations,
respectively, qualify as A+ under the corrected midpoint mapping where the
old colour test yielded A. These are repeated observations, not distinct
trades or evidence of profitability. Later captures naturally contain more
observations because the live recorder continues running.

## Setup-rule inventory

This table distinguishes source/code verification from a claim that an
indicator predicts profitable trades. Complete suites exercise the existing
thresholds, direction mirrors, missing values and timing boundaries.

| Setup family | Actual producer and meaning | Audit finding |
|---|---|---|
| Base scanner inclusion | `momx/scan.py`, `board._scan_one`: Last ≥ $3; 4h EXT volume +0.5% versus two bars back; directional 1h close ±0.3% versus two bars back; ANY named EMA/MACD cross, squeeze fire or RVOL scanner condition. | Uses the supplied forming-bar scan. Canonical chart EMA/cross engine is reused. Fast 5m/15m RVOL scanner deviation is 5; other scanner frames use 3. Scanner inclusion differs intentionally from column colouring. |
| A+/A/B scanner grade | `momx/grade.py`: SQZ OK, RVOL/news push and 8-frame SKIT count; A+ additionally requires the directional H/L midpoint. | Dark-shade mapping corrected. SQZ white is not treated as orange coiling by this documented experimental baseline. News push means a headline ≤12h, not verified good news. |
| MOM↑/MOM↓ | `momentum.summarize`, `columns.adx_cell`, `sqzMomentumTag`: completed 5m squeeze momentum above zero and rising/below zero and falling; “strong” additionally requires directional DI above 25 and above the opposing DI on 5m AND 30m. | Same conditions as the existing rule. Strong BULL MOM is displayed; outlined BULL MOM is hidden from the column as previously requested, but its separate filter remains available. A strong MOM tag can coexist with the different `m5.state` value “fading”; these measure different things. The strong background test does not require ADX itself above 25. |
| FLIP | `adx.closedCross`, `m5.sqzMom`: closed 5m directional DI cross plus momentum change on the same candle. | Existing same-candle and direction checks retained. |
| RVOL↑/↓ | `columns.rvol_cell`, `rvolBuilding`: directional painted RVOL on 5m through D, with existing session/auction visibility rules. | Uses actual paints. RVOL `barAt` is volume timing, not a source-candle freshness clock; source freshness must not be inferred from it. |
| SKIT | `columns.skittles_cell`, grade timeline, `skittlesBreaks`: currently painted with-trend crosses first observed within 60 minutes, grouped by time and colour. | Counter-trend dark-green/plum MACD-only backgrounds remain excluded. Only the newest group flashes. Monthly parity limitation below remains open. |
| Scanner SQZ release | Grade timeline and `sqzFires`, today during regular hours on 2h/4h/D/W. | First observed release time, not reconstructed earlier entry time. |
| Chart squeeze flame | `momx/chart_signals.py` canonical `shared_Cloud_Signal_Squeeze`, `chartSqueezeTags`: 1h/2h/4h/D. | Different source study from scanner SQZ; forming flames can disappear. Gone flames remain explicitly crossed out. |
| CALL/PUT2H/4H, short C/P2H/4H | Canonical chart signal calculator and `chartArrows`. | Family, direction, lifetime, redrawn first-seen time and gone-state handling retained. Gone arrows do not pass the active-arrow filter. Short and full arrows are different cases. |
| GO / gap-and-go | `momentum._gap_go`, `gapAndGo`: directional gap ≥2%, first completed 09:35–10:25 bar breaking the opening range, VWAP and defined open proxy, A/A+ and directional 30m DI. | Retains the documented last-premarket-close **open proxy** used in the original replay; this is not silently changed to the exchange's official open. GO can remain listed later in the day. |
| GO+ | `_go_confirmation`, `goRvolMacd`: completed 5m directional MACD/signal with improving histogram, completed 30m direction, short RVOL and GO, 09:30–15:30. | Checked each snapshot, not a day-long latch. Source/candle timing and freshness guards remain. |
| MX A+ | `momx/momox_aplus.py`: ≤24h news, cyan/magenta RVOL on any 15m–D, recent squeeze release on allowed frames, higher-frame directional SKIT background. | Separate rule from scanner A+. First qualifying observation is latched. OI-wall requirement is disabled by the existing Sep 29 specification. |
| Daily line/reclaim/$line, ZS, ABVX | `momx/option_track.py` and existing `dayLines` stamps, displayed by `dayLineTags`/`zsNow`. | UI consumes actual directional event records and ET-day stamps. No substitute formulas or new entry conditions added. |
| Sector, ⭐ rank, Best, hot/late leaders | `momx/sectors.py`, rotation metadata, `rankBestSetups`/`bestSetupRanks`. | Ranking is by the documented rule order, not by H/L or a calibrated success probability. Bull-only sector display remains bull-only. |
| V2/V3/Daily 2/G, OPT, SOLO, TURN | Worker strategy books, `earlyOpt` browser latch, existing research filters/legacy tags. | Existing experimental rules retained. OPT's browser latch cannot recover an earlier observation if the browser was first opened after the cutoff. Displayed legacy results do not establish future edge. |
| AI / JEV marks and filters | `ai_filter`, `aiTags`, `jevRecordTag`. | Actual symbol/side/day/time/accepted-record guards apply. AI is an earlier paper-trade score, not the current setup's calibrated win probability. Filter-time disagreement corrected; paid JEV remains off. |
| News explanations and News Momentum | `news_catalyst.py` → `row.catalyst`, plus grade/momentum checks. | This catalyst reader is distinct from the local neural headline sentiment feature. Do not describe all NEWS/AI features as one model or invent a positive-news signal from a mere headline. |
| Warnings / explosive / steady / bolt | Existing pattern producers and warning decorators; bull hourly bolt is `momx/live_bolt.py`. | Existing meanings retained. H/L price position does not prove RVOL, DI, news relevance, liquidity or profitability. |

## Actual AXP/CBOE observations

`artifacts/scanner-axp-cboe-check.json` was captured at 08:09 ET, before the
new build. AXP: +0.61%, H/L rounded +1.00 inside 320–325.69, 5m state fading,
5m RVOL −0.2, source 08:00. CBOE: +2.15%, H/L +1.00, state extended,
5m RVOL −0.5, source 07:30. CBOE's then-quoted bid/ask was 322.50/329.00.
Those observations explain why selecting the first name solely because it
is at a range high does not establish an executable momentum trade.

The worker's diagnostics showed AXP/CBOE REST reads several minutes old,
within a shared 90-request/minute budget over roughly 507 equities, with
some tapes having sparse premarket prints. Stream extension requires verified
overlap and cannot safely invent missing OHLCV. The evidence does not isolate
one fixed provider delay. A newer quote alone cannot repair older volume or
momentum history.

## Verification and remaining limits

- Focused final checks: 195 backend checks and 302 frontend checks passed
  before the last fixture additions. The complete frontend suite passes
  1,560 tests. The final complete relevant backend suite passes **1,725 tests,
  one retired-module skip**, with 22 existing warnings. Its result is in
  `artifacts/scanner-setup-full-backend-final.log`.
- Production build and rebuilt ET-stamped release notes:
  `artifacts/scanner-setup-production-build.log`.
- Desktop/reduced-width deterministic scenarios replay AXP/CBOE-based
  fixtures: numeric precision, header sort cycling, older-source label and
  tap explanation, quote high/retreat, disconnected fallback, BEAR switching
  and subscriptions for a leader supplied in `rest`: **8 checks each at
  1440×1000 and 390×844**, with zero browser/console errors. These fixtures
  isolate unrelated cloud-chart profile restoration and are clearly separate
  from actual-market observations. Actual browser runs below retain that
  restoration and use no market-data interception.
- `artifacts/scanner-setup-verified-premarket.json` reads the actual running
  Watchlist/Mag7 BULL and BEAR boards: 368/368/10/10 rows, zero grade-check
  disagreements, duplicate symbols or out-of-range H/L values at capture.
  It is premarket evidence, not regular-session verification.
- Actual production browser runs are in `scanner-setup-live-*-smoke.json`.
  Their cold-load and click timings include simultaneous validation load;
  report quiet reruns separately instead of presenting the best number as
  the only result. Under suite load, desktop/phone cold readiness was
  42.9/46.5 seconds. Phone runs with the suites stopped measured 13.3 and
  22.8 seconds cold; the repeat load with restored local preferences measured
  8.1 seconds. A cold browser restored a saved cloud chart grid and reloaded
  once. These are different load/bootstrap conditions, not a measured
  before/after speedup from the H/L fixes. **Cold startup performance remains
  open for the market-hours follow-up.** End-to-end header-click timings
  include horizontal scrolling/actionability and must not be presented as
  isolated React render latency.
- Final running-worker audit (`scanner-setup-handoff-premarket.json`) again
  found zero disagreements across 368/368/10/10 rows. Direct worker API reads
  took 430/331/143/153 ms respectively. The restarted worker loads the fixes;
  its fresh error log was empty after the audit.
- Older-session row labels include the date (for example **Data 10/29 16:00**)
  so a prior-day close is not mistaken for today's future time. Tappable
  explanations include the full ET date and distinguish study time from a
  later quote. Unknown/invalid source times do not invent a timestamp.
- No chart engine or translated study formula was changed. Existing chart
  tests are included in the complete relevant suite; previous chart-mode,
  timeframe, indicator-profile and layout checks remain documented in
  `scanner-ai-validation.md`.
- TOS's independent current values were not available for a side-by-side
  comparison. `docs/momx/SPEC.md` explicitly records an unresolved Monthly
  Skittles comparison. Do not claim all studies have exact live TOS parity.
- A one-time same-thread check is scheduled for **09:35 ET, Oct 30**. Run
  `.venv/Scripts/python.exe scripts/audit_scanner_setups.py --regular --samples 3
  --interval 15 --output artifacts/scanner-setup-regular-session.json`, then
  inspect actual browser/quote/candle behavior, particularly AXP/CBOE and two
  liquid materially different tickers. Do not call this completed before it
  runs. Keep normal scan matches, grade, H/L and hourly-bolt meanings separate.
- No reliable profit prediction or claim that the top ticker will win has
  been established. These changes correct measurements and display behavior.

## Market-hours follow-up completed, Oct 30

The scheduled audit was performed at 09:53:06, 09:53:45 and 09:54:10 ET.
All three actual-service samples passed the existing grade, duplicate-symbol
and H/L-range checks across Watchlist BULL/BEAR and Mag7 BULL/BEAR
(368/368/10/10 rows per sample). The actual phone-width browser workflow
also completed with H/L interaction and a bounded top-leader quote lease.
Cold loading and unequal source ages remain open; this is not a claim that
all scanner performance or independent TOS parity issues are resolved.
See [scanner-refresh-recovery.md](scanner-refresh-recovery.md) for the exact
timings, replay distinctions and evidence paths.

The subsequent heartbeat reran the three-sample audit at 10:17 ET and fixed
a late startup grid-sync redirect after explicit scanner navigation. Final
market-hours desktop/phone results and unresolved performance/data limits
are recorded in [scanner-market-hours-followup.md](scanner-market-hours-followup.md).
