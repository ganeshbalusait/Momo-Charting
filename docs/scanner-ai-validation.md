# Scanner, Gamma and local AI validation — October 30, 2026

The running project is `AgenticAI-Trading 2` inside the outer `AgenticAI-Trading 7` directory. Changes preserve existing trading thresholds and unrelated work. No live order was placed by this investigation.

## Scanner-to-chart loading

Unfinished scanner board/news downloads could continue after navigation and occupy browser connections. Rank-only changes also changed the news request identity unnecessarily. Chart candle requests then waited, and large history replies incurred avoidable serialization and chart replacement work.

Leaving the scanner now aborts its pending read requests, including lists, momentum, grades and rule records as well as board/news. A component lifetime signal also aborts a response body after its headers arrive. User-initiated writes retain their completion semantics. Requests have deadlines and news requests share one canonical ticker-set identity. Scanner ticker links warm a bounded candle request on hover/focus/touch; the opened chart joins that request. Candle requests have high browser priority. Data remains owned by its ticker, and the previous ticker's candles are not displayed while switching. Ordinary JSON avoids an unnecessary recursive normalization pass; chart series apply bounded historical revisions incrementally with an authoritative replacement fallback.

Additional profiling found a post-opening pause: desktop charts automatically promoted the full research tape, causing a 21.155-second settle time in one AAPL run despite first candles at 2.183 seconds. Scanner launches now defer that promotion until actual earlier-history navigation. A cached full payload also supplies a bounded initial view without modifying its prices or the original cached arrays. Normal desktop launches retain automatic history promotion. Higher-timeframe seeds are preserved. Desktop scrollbar navigation was verified to request and display older history; that larger requested history can still take longer than the opening window.

Measured on this installation, with actual chart responses:

| Measurement | Before | After |
| --- | ---: | ---: |
| Desktop scanner → TSLA first candles | 6.048 s | 2.204 s |
| Desktop scanner → AAPL first candles | 2.570 s | 3.225 s |
| Phone-width scanner → TSLA first candles | earlier stalled request: 14.7 s | 0.916 s |
| Phone-width scanner → AAPL first candles | no comparable baseline | 2.755 s |
| AAPL initial chart HTTP response | 899 ms | 515 ms |
| SPY initial chart HTTP response | 822 ms | 180 ms |
| AAPL deeper-history HTTP response | 4,288 ms | 1,401 ms |
| SPY deeper-history HTTP response | 6,675 ms | 1,261 ms |

These are individual measurements, not latency percentiles or a promise for every open. AAPL's first-candle browser measurement did not improve. Server/test load caused HTTP measurements to vary. The final desktop stress test deliberately held four scanner reads plus news unfinished, verified that all four reads and news were canceled on each navigation, then opened actual AAPL/TSLA chart data. TSLA's browser connection wait was about 1.1 ms, versus a 4.8-second wait observed before full read cleanup. The desktop studies were ready in 5.180 s for AAPL and 4.719 s for TSLA; phone-width measurements were 3.187 s and 2.974 s. No automatic deep pull occurred during the nine-second post-opening check. Older history was separately requested and displayed through desktop scrollbar navigation, taking 9–21 seconds in that larger-history test. Initial loading is improved; research-history computation is still materially heavier, and long tasks from the existing overlays remain observable.

No stale-ticker candle frames, browser console errors, HTTP 5xx replies or runtime errors were observed in the final desktop stress and phone tests. The LWC update microbenchmark fell from 1.7–5.1 s to 164–233 ms over 40 revisions of a 12,000-point series, with identical final values; that microbenchmark is not the total navigation time.

Evidence: `artifacts/scanner-chart-before-desktop.json`, `scanner-chart-handoff-desktop.json`, `scanner-chart-released-phone.json`, `scanner-chart-on-demand-verified-desktop.json`, and `chart-http-{before,after}.json`.

## Scanner freshness

The old Updated timestamp described a scan, not necessarily the underlying market bars. At the initial Watchlist capture, the scan was about 40 seconds old but the newest source bar was about 21.2 minutes old. The frontend already polled every 15 seconds. Source diagnostics showed a 90-call/minute TOS history budget shared across approximately 507 tickers and multiple resolutions. Further source tracing confirmed that TOS mode can fill missing overnight candles from BOATS history, explicitly requested with a 16-minute delay. Newer Schwab/stream bars take precedence when available. The freshness tooltip now discloses this conditional overnight fill as well as rate pacing; it does not claim every TOS bar has a fixed entitlement delay.

Replies now expose per-row market-data time plus scan start, scan completion, API served time, and browser received time. The visible freshness line distinguishes the newest and oldest known source bars, with source limitations in its tooltip. All 368 Watchlist rows had known timestamps in the final audit.

At the post-rollout capture (02:10 ET), scan age was about 14 seconds and newest-bar age about 20.5 minutes. The API measurement was 2,000 ms while other validation work was running; earlier measurements were 541 and 768 ms. These snapshots do **not** establish an API or source-data latency improvement. The app now reports that age accurately; it cannot make old source bars live. Some oldest bars were from the previous regular session. Regular-session source behavior and any account entitlement delay remain unverified in this overnight session.

Evidence: `artifacts/scanner-audit-{before,final,released}.json`.

The 02:42 ET handoff audit still had about 22 minutes of newest-bar age. Its unviewed Watchlist scan snapshot was about 101 seconds old, with a 1,234 ms response while the requested refresh built. This confirms that neither source-data delay nor every scanner rebuild delay has been eliminated. All 368 source times remained known; the updated conditional BOATS disclosure was present. Evidence: `artifacts/scanner-audit-handoff.json`.

## Record-column AI and JEV audit

The Record win/ROI values are historical rule aggregates. The AI mark identifies the highest-scored recorded paper trade for this ticker and side today; it is not the probability that the current chart setup will win.

Confirmed defect: all 4,578 keys in the new day's score file came from the prior day. The scorer reset its day after potentially blocking model work and scored old book records into the new day's map. This produced 203 stale AI marks in a captured Watchlist. Valid score/rule selection otherwise agreed with the stored records.

The scorer now resets first and accepts only the current ET day's trades. Backend and frontend reject wrong-day, wrong-ticker, wrong-side, invalid-probability and future records. Selection compares unrounded probabilities, avoiding a false threshold pass. Each new score retains its actual model family and scoring time in a sidecar file; changing the champion does not relabel previous scores. A historical view rejects scores calculated after its observation time. Unattributed old scores say “saved local score.”

JEV display marks now require the existing accepted TAKE record and matching actual paper entry time. This does not introduce a new trading rule. The sampled October 29 book contained no JEV scores; a single read-only connection probe returned HTTP 402. Paid JEV bot and news requests are disabled as requested. No provider call is used as a silent fallback from local AI.

Evidence: `artifacts/scanner-record-audit.json`, `jev-connection-probe.json`, focused backend/frontend tests.

## Local trading model

Labels use the existing paper trade's actual exit/closed last mark versus entry, at the mid before fees. They do not use the day's best price as a winning label. Features are restricted to information available at entry. Local challengers exclude JEV inputs.

The chronological comparison used 56,752 training trades over September 28–October 21, validation October 22/23/26 (13,943 trades), and test October 27–29 (13,559 trades). Encoder statistics are fitted on training rows only. The deeper neural candidate scored worse than the baseline and was not promoted.

| Model | Validation AUC | Test AUC | Test Brier error (lower is better) |
| --- | ---: | ---: | ---: |
| Prior logistic baseline | 0.5167 | 0.5406 | 0.25852 |
| Deeper neural candidate | 0.5077 | 0.5302 | 0.3192 |
| Local boosted trees | 0.5616 | 0.5505 | 0.24027 |

The boosted model initially met the AUC/probability-error comparison gate. The exported ONNX probabilities differed from the original model by at most 0.000000191. **It was subsequently withdrawn after the execution-cost audit below.** The active scorer is again the earlier experimental local logistic model. `AGX_AI_BOOSTED=0` disables automatic boosted promotion on this installation. The candidate and its reports remain available for research. Rollback files were saved under `artifacts/momx_ai_filter/backup-20261030-020532`.

This is a modest retrospective ranking improvement, not strong predictive accuracy or proof of profit. Multiple candidates were compared on these held-out days, so a future untouched paper period is still needed. Final live status verified `model: lr`, `loaded: true`, `modelWarning: null`, `placesOrders: false`; paid JEV remains disabled. A healthy model load is not a profitability certification.

### Execution-cost audit

The existing .55 selection threshold was audited without changing the trading rules. The boosted candidate selected 465 completed test records: 297 wins, 60 losses and 108 break-even at paper mid prices. Its apparent mean mid return was +55.5%. Using each same record's entry ask and exit bid instead, 31 won, 429 lost and 5 broke even; the mean per-record return was **−80.6% before fees**. All 465 had the required quotes. The baseline also failed this conservative quote scenario. These are correlated signal records, not a funded portfolio return or actual fills.

The candidate favored extremely cheap, wide-spread/zero-bid options. One actual GRAB record bought on paper at a .005 mid with bid 0/ask .01 and ended at a .01 paper mark with exit bid 0/ask .01. The mid-price label is a “win”; selling at the recorded bid does not realize that mark. This explains why a better mid-price AUC cannot establish reliable profits. No result from the day's best price was used in this audit.

`artifacts/ai_challenger_evaluation/profit-audit.json` includes validation/test days, losses, daily variation, quote coverage and a separately labeled hypothetical $1.30 round-trip fee scenario. That fee is an assumption, not the user's broker schedule. Snapshot quotes do not prove executable fills; slippage, overlapping signals, capital limits and an untouched future paper period remain unresolved. **Reliable profit prediction is not established.** See also [CFTC guidance on AI trading claims](https://www.cftc.gov/LearnAndProtect/AdvisoriesAndArticles/AITradingBots.html).

## Local neural news

News uses a separate six-layer financial DistilRoBERTa language model, locally, with positive/negative/neutral text sentiment. It runs asynchronously with bounded work, cached story identities, explicit model/revision/read time, and no paid inference fallback. Mixed/multi-company, irrelevant, unavailable or uncertain readings are identified rather than presented as confident ticker direction. Text-classification confidence is not the probability of a stock move. High confidence is displayed as ≥99%, avoiding rounded 100% certainty.

Pinned model revision: `e6953e30167c7fc3d1ad62f3df31617dd712fbc1`. Real model functionality probes classified clear positive, negative and neutral example texts, taking about 2.9 s cold and 24–34 ms warm. Those examples are not a news accuracy evaluation. Real stored headlines were also classified; paid requests remained disabled. See [local news installation and limitations](local-news-ai.md).

## Gamma

Gamma number boxes have an independent visibility switch. Gray-white negative and deeper-orange positive clouds span left across the candle pane, with a broader body and dark backing for visibility. Phone text is compact. Settings start collapsed on desktop/mobile and reset closed on ticker/timeframe navigation; the arrow opens them without disabling the overlay. Indicator options use existing save, reset and cross-ticker profiles.

The supplied proprietary indicator's source was unavailable. This is a documented current-chain exposure estimate, not an exact reproduction of its formula or dealer positioning. The chosen call-positive/put-negative convention and P% definition are disclosed. Full-width clouds do not imply historical exposure replay. See [Gamma formula, coverage and visibility](chart-gamma.md).

## Release validation

- Focused checks include 22 backend response/freshness tests, 44 chart tests, and 46 scanner-scope/chart/Record tests; all passed.
- Complete relevant backend suite: **1,701 passed, 1 skipped**, with 22 existing UTC deprecation warnings. The skipped chart pivot module is retired.
- Complete frontend suite: **1,548 passed**. Final production build passed in 45.74 seconds after the 02:40:56 ET release note, and its built notes match the source. Existing bundle-size warning remains. Evidence: `artifacts/scanner-scope-full-js.log` and `scanner-scope-production-build.log`.
- Dependency validation: `pip check` passed.
- Browser validation and final service evidence are recorded in the `artifacts/*-smoke.json` and final logs. Desktop and reduced-width checks use the production build. Some Gamma control scenarios replay actual saved provider snapshots to keep both expiries available; they are functional tests, not proof of live historical Gamma.
- Gamma/chart regression: 35 desktop checks and 33 phone-width checks passed across all 11 supported timeframes, SPY/AAPL ownership, single/two-chart, big-screen/fullscreen, zoom/pan/crosshair, indicator controls, layouts and profile save/reset/persistence. A controlled browser REST update appended exactly one AAPL candle and repeated-tail reconciliation did not duplicate it on desktop or phone. This simulates a transport update; it is not an RTH streaming observation.
- No physical iPhone or regular-market-session streaming test was available overnight. Proprietary study parity and future paper profitability cannot be validated from these screenshots/data.

The combined-suite failure was traced to the watchdog killing valid Python multiprocessing children. It now checks the declared parent's identity and process age before reaping true orphans. A synchronous-persistence test assumption was isolated, and tests now keep scanner board writes in scratch storage so late fixture writers cannot contaminate live caches. The original real-process chart tests pass without replacing them with an inline stub. Expected canceled-response and canceled keep-alive socket errors now close cleanly; unrelated I/O errors still propagate.
