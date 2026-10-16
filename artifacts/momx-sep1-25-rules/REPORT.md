# MomX rule backtest: September 1–25, 2026

Research reconstruction, completed 2026-09-26T23:59:04.721347-04:00. This report supersedes the earlier partial one-minute-cache audit. The cloud MomX UI was inspected; the backtest uses the local Watchlist archive plus cached/retrieved Schwab five-minute prices. Cloud/local historical equivalence was not independently certified.

## Findings

The strongest historical candidates in this run are GO, GO plus short-timeframe cyan RVOL/MACD confirmation, and the older MomoX A+ formula without the new OI gate. GO plus confirmation has the highest full-period mean, but deteriorated in the final week. MomoX A+ without OI was stronger in the final week. Basic A+ and A/A+ were negative after the stated cost assumption. Early OPT and V2 were negative; V3 and Daily 2 had small positive full-period means and negative final-week means. Combining GO with OPT diluted GO in this sample. These are exploratory rankings of reconstructed stock trades, not proof of a profitable live options strategy.

## Scope and scoring

All 18 trading sessions were requested. The current weekly-options census contains 334 symbols; 320 appeared in the scoped archive. Historical membership was not available. Missing price inputs were retrieved for 101 symbols through the configured read-only data client.

Signals are the first observed qualifying snapshot per ticker/day/rule, from 09:30 to 15:30 ET. Entry is the next five-minute bar opening strictly after the signal. Only complete 78-bar regular sessions are scored. The main exit is a +2% stock target or −1.5% stock stop; otherwise exit at 16:00. A bar hitting both levels is scored stop-first. Gaps through stops fill at the worse opening price; gaps above a target get only the target price. An illustrative 0.10 percentage point round-trip cost is deducted. These are stock returns, not option returns or account returns. Concurrent positions are not capital-constrained.

“Positive gross” means an outcome above zero before assumed costs. “Positive net” means above zero after those costs. “Target hit” is narrower: it excludes positive exits at the close that never reached +2%. Stop-hit percentages do not count negative close exits.

## Main results

| Rule | Scored | Identified* | Positive gross | Positive net | Mean gross | Mean after cost | Target hit | Stop hit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| GO | 63 | 63 | 63.5% | 61.9% | +0.701% | +0.601% | 52.4% | 23.8% |
| MomoX A+ without OI | 50 | 50 | 60.0% | 60.0% | +0.563% | +0.463% | 48.0% | 28.0% |
| GO + short RVOL + MACD | 41 | 41 | 65.9% | 65.9% | +0.802% | +0.702% | 61.0% | 29.3% |
| Best GO or early OPT | 95 | 95 | 54.7% | 52.6% | +0.339% | +0.239% | 42.1% | 34.7% |
| A/A+ + short RVOL + MACD | 290 | 291 | 49.0% | 47.9% | +0.134% | +0.034% | 32.8% | 38.6% |
| A+ | 461 | 462 | 47.5% | 45.1% | +0.081% | -0.019% | 25.2% | 30.4% |
| A or A+ | 660 | 662 | 43.9% | 41.8% | +0.016% | -0.084% | 23.5% | 31.2% |
| V3 | 38 | 38 | 52.6% | 52.6% | +0.172% | +0.072% | 34.2% | 39.5% |
| Daily 2 first two | 27 | 27 | 48.1% | 48.1% | +0.160% | +0.060% | 37.0% | 40.7% |
| Early OPT | 37 | 37 | 45.9% | 43.2% | -0.052% | -0.152% | 32.4% | 48.6% |
| V2 | 111 | 111 | 43.2% | 41.4% | -0.075% | -0.175% | 25.2% | 43.2% |

*Identified means signals reconstructible with available inputs. Some signals cannot be identified without sufficient indicator history. Scored can be smaller where a complete price session is missing. Rule samples overlap and must not be summed as independent trades. General A/A+ counts include signals that do not need reconstructed momentum; coverage is not identical across rules.

## Earlier period versus final week

| Rule | Sep 1–18 N | Sep 1–18 mean after cost | Sep 21–25 N | Sep 21–25 mean after cost |
| --- | --- | --- | --- | --- |
| GO | 48 | +0.808% | 15 | -0.062% |
| MomoX A+ without OI | 33 | +0.188% | 17 | +0.997% |
| GO + short RVOL + MACD | 34 | +0.894% | 7 | -0.233% |
| Best GO or early OPT | 66 | +0.461% | 29 | -0.266% |
| A/A+ + short RVOL + MACD | 190 | +0.029% | 100 | +0.042% |
| A+ | 303 | -0.012% | 158 | -0.034% |
| A or A+ | 428 | -0.071% | 232 | -0.108% |
| V3 | 23 | +0.286% | 15 | -0.255% |
| Daily 2 first two | 17 | +0.529% | 10 | -0.738% |
| Early OPT | 23 | +0.051% | 14 | -0.484% |
| V2 | 76 | -0.088% | 35 | -0.364% |

This is a stability check, not an untouched out-of-sample test: these rules were developed after observing this same month. Small final-week samples cannot establish persistence.

## Exit sensitivity: same signals, same −1.5% stop

| Rule | +1% target mean after cost | +2% target mean after cost | +4% target mean after cost |
| --- | --- | --- | --- |
| GO | +0.264% | +0.601% | +1.134% |
| MomoX A+ without OI | +0.170% | +0.463% | +0.474% |
| GO + short RVOL + MACD | +0.318% | +0.702% | +1.360% |
| Best GO or early OPT | +0.078% | +0.239% | +0.683% |
| A/A+ + short RVOL + MACD | +0.002% | +0.034% | +0.181% |
| A+ | -0.065% | -0.019% | +0.122% |
| A or A+ | -0.133% | -0.084% | +0.020% |
| V3 | -0.194% | +0.072% | +0.369% |
| Daily 2 first two | -0.014% | +0.060% | +0.555% |
| Early OPT | -0.128% | -0.152% | +0.295% |
| V2 | -0.154% | -0.175% | +0.160% |

Selecting the best exit after seeing this table is another fitted choice. The +4% target has a different target-hit rate and holding time. These figures do not mean +4% is achievable on every trade.

## Timing

| Rule | Before 10 N | Before 10 mean after cost | 10:30 onward N | 10:30 onward mean after cost |
| --- | --- | --- | --- | --- |
| GO | 41 | +0.567% | 13 | +0.133% |
| MomoX A+ without OI | 43 | +0.626% | 4 | -1.247% |
| Early OPT | 37 | -0.152% | 0 | — |
| V2 | 85 | -0.304% | 18 | +0.319% |
| Daily 2 first two | 24 | +0.016% | 1 | -1.600% |

GO can first qualify in the scanner later than its opening breakout because the grade and DI conditions are also required. The app remembers the earlier breakout. This test enters at the actual later observed qualification; it never backdates the entry to the breakout.

## Matched comparison and uncertainty

For each tested entry, compare with all other price-covered weekly tickers already observed by the scanner that day, entering at the same next-five-minute opening and using the same exits/costs. The average of that pool is the expected return of a uniform random choice within that particular pool. This is a scanner-universe control, not the whole stock market. Confidence intervals resample whole signal days 1,500 times; they are descriptive, not corrected for selecting among many rules or targets.

| Rule | Matched control mean after cost | Mean excess, percentage points | Day-bootstrap 95% interval for excess |
| --- | --- | --- | --- |
| GO | +0.100% | +0.501% | [0.058, 0.91] |
| MomoX A+ without OI | -0.079% | +0.542% | [0.101, 0.957] |
| GO + short RVOL + MACD | +0.118% | +0.584% | [0.089, 1.128] |
| Best GO or early OPT | -0.029% | +0.269% | [-0.087, 0.571] |
| A/A+ + short RVOL + MACD | -0.085% | +0.119% | [-0.131, 0.344] |
| A+ | -0.097% | +0.077% | [-0.072, 0.227] |
| A or A+ | -0.094% | +0.010% | [-0.154, 0.156] |
| V3 | -0.024% | +0.096% | [-0.523, 0.513] |
| Daily 2 first two | +0.026% | +0.033% | [-0.498, 0.593] |
| Early OPT | -0.259% | +0.107% | [-0.316, 0.591] |
| V2 | -0.116% | -0.059% | [-0.37, 0.267] |

## Dependence on a single day

| Rule | Range of mean net return after dropping any one signal day (%) |
| --- | --- |
| GO | [0.514, 0.711] |
| MomoX A+ without OI | [0.31, 0.622] |
| GO + short RVOL + MACD | [0.607, 0.82] |
| Best GO or early OPT | [0.143, 0.324] |
| A/A+ + short RVOL + MACD | [-0.045, 0.107] |
| A+ | [-0.072, 0.017] |
| A or A+ | [-0.122, -0.036] |
| V3 | [-0.129, 0.165] |
| Daily 2 first two | [-0.088, 0.192] |
| Early OPT | [-0.363, -0.035] |
| V2 | [-0.259, -0.103] |

This checks dependence on one day, not on one ticker or a common market factor. The strategies still share correlated positions.

## Exact rule interpretations and differences from live use

- A+ and A/A+: current momx/grade.py evaluated on the archived cell colors and news at each recorded timestamp. The September 22 change excluding dark-green backgrounds as standalone bullish crosses applies throughout this replay. This is not a replay of every earlier code version.

- GO: A/A+ plus the app’s gap-and-go price pattern, with +DI above −DI on completed 30-minute candles. The price pattern requires a gap of at least 2%, then a completed 09:35–10:25 five-minute candle above VWAP, the pre-open reference and first regular candle high. The app uses the last premarket close as its opening reference when available. Using completed DI instead of live forming DI is a deliberate reproducible variant.

- Early OPT: first qualification before 10:00; A/A+, bullish cross backgrounds on 2h and 4h (including dark green, matching the filter), completed 30-minute buyers in control, and completed-five-minute Extended state. The live app may use a developing price for Extended. Cross backgrounds can persist; this does not require a cross newly observed in the last minute.

- Best: union of GO and early OPT, one earliest entry per ticker/day. No retrospective star ranking is applied.

- V2: evaluated only on the first occurrence of each A/A+ letter in the regular session, at or after 09:35, daily gain under 10%, Extended momentum and at least one bullish RVOL push. A first letter before 09:35 is not silently moved to a later time. V3 additionally requires a cyan 4h/D/weekly squeeze. These use completed-five-minute momentum.

- Daily 2: first two distinct weekly tickers each day that meet V2 with at least two RVOL timeframes, within this Watchlist archive. The live worker combines boards and may rank non-weekly names too, so its historical #1/#2 need not match this restricted reconstruction. Alphabetical ordering resolves exact timestamp ties.

- MomoX A+ without OI: 09:35–15:30, non-ETF, dated headline within 24h, cyan RVOL on any 15m/30m/1h/2h, cyan squeeze now or observed within 30 minutes, bullish D/2D/3D/4D/W/M Skittles. Undated headlines are excluded. This is the old-rule variant; the currently required OI gate cannot be reconstructed without historical chains.

- Short RVOL + MACD: a cyan archived 5m/15m/30m RVOL cell, plus completed-five-minute MACD(12,26,9) above signal and its histogram rising. This definition is explicit; the quoted claim “MACD turning up” did not specify a formula. The resulting rules test first joint qualification, not later decoration of an earlier trade.

## What this does not establish

Star #1, chart arrows, live bolt, SOLO, hot-sector leader #1, AI News + Momentum, Strategy G and current OI-gated MomoX A+ were not assigned fabricated full-period results. They require signal-time rankings, universe/sector state, streaming data, AI verdicts, historical options data or exact historical label timing that this replay does not reconstruct. Bear rules were not tested; bullish scan-selected history is not an unbiased bearish universe.

The archive is scan-selected and capped; first observed is not necessarily first actual qualification. It can omit signals and change the Daily 2 order. Input data was not independently reconciled against exchange records. No causal claim is made about why prices moved. Spread, slippage, liquidity, option Greeks, exercise/assignment, position sizing and portfolio exposure are not modeled beyond the stated stock cost assumption.

## Verification and deliverables

Eight execution/data-integrity checks passed, covering ambiguous candles, stops, gaps, next-bar entry and invalid OHLC. Twelve reconstructed Extended-state and twelve GO checks matched the app’s formulas for META, SPY and QCOM. Twelve prefix-only checks gave identical states after future bars were removed. Archived cell timestamps were screened for bar-start times later than their snapshot; zero such snapshots were found in this run. MNKD returned an empty recovery response; unavailable price sessions remain excluded. No production app code, trading settings or orders were changed; no application build was needed for this standalone research report.

Files: results.json contains methodology, rule statistics, coverage, source hashes and trades; trades.csv is the per-trade ledger; daily-results.csv gives each rule by day. The scripts are ../momx_rules_backtest_sep2026.py and ../verify_momx_backtest.py.