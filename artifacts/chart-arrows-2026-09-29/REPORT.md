# September 29, 2026 — morning chart-arrow replay

**Partial-coverage historical simulation, not actual executions or an exhaustive chart backtest.** Entries are from 04:00 to before 10:00 New York time (EDT, UTC−4), with exits allowed later. Results use stock prices, not option premiums.

## Rules and fills

- Rules 1 and 2 use OR. Rules 3 and 4 require both labels to overlap while active; they need not print on the identical candle. Either-color AND allows mixed families. Separate same-color variants are below.
- Cyan = 9/20 EMA signal family; yellow = 4/8. These signal EMAs are distinct from the 5-minute EMA21 used for exits.
- First qualifying recorded signal per ticker, per rule, per day. No re-entry and no choosing the most profitable signal afterward. Different rules are separate hypothetical strategies and may overlap in tickers.
- Entry: next exact 5-minute open strictly after first-seen time, before 10:00. Drawn-on-chart time is retained separately because an arrow may only appear later. Disappeared/repainted arrows remain eligible if observed at the time.
- Exit: first completed 5-minute close below EMA21 after entry, filled at the following bar open. No profit target or percentage stop; no forced 10:00 exit. A candle already below EMA21 triggers an exit; a fresh cross from above is not required. No additional entry-above-EMA filter was added.
- EMA21 uses alpha 2/22, first-close seed and at least 105 preceding bars, including available extended-hours bars. Observations end at the latest completed retrieved bar, no later than 20:00 that day. Unexited trades would remain OPEN.
- Dollar P&L assumes an independent $1,000 fractional-share stock position per trade. Deduct 0.10 percentage point ($1 per $1,000) round-trip cost. This is an assumption, not measured execution cost; premarket costs may be greater. Average net ROI is per trade, not return on a continuously funded portfolio.
- Maximum gain is the highest stock high from entry through the holding period, excluding price action after the exit open. It is gross, hindsight-only, and is not a target or a realizable guaranteed fill.

## Summary by rule and color

| Rule | Color | Closed | Win | Loss | Win rate | Net P&L ($1,000 each) | Average net ROI |
|---|---|---:|---:|---:|---:|---:|---:|
| 1 CALL2H OR CALL4H | Either color | 52 | 13 | 39 | 25.00% | $-228.82 | -0.440% |
| 2 C2H OR C4H | Either color | 39 | 15 | 24 | 38.46% | $-32.05 | -0.082% |
| 3 CALL2H AND CALL4H | Either color | 13 | 3 | 10 | 23.08% | $-82.31 | -0.633% |
| 4 C2H AND C4H | Either color | 2 | 0 | 2 | 0.00% | $-20.93 | -1.046% |
| 1 CALL2H OR CALL4H | Yellow | 51 | 13 | 38 | 25.49% | $-210.29 | -0.412% |
| 2 C2H OR C4H | Yellow | 24 | 11 | 13 | 45.83% | $+49.54 | +0.206% |
| 3 CALL2H AND CALL4H | Yellow | 8 | 1 | 7 | 12.50% | $-71.24 | -0.891% |
| 4 C2H AND C4H | Yellow | 1 | 0 | 1 | 0.00% | $-4.80 | -0.480% |
| 1 CALL2H OR CALL4H | Cyan | 18 | 7 | 11 | 38.89% | $-98.22 | -0.546% |
| 2 C2H OR C4H | Cyan | 15 | 4 | 11 | 26.67% | $-81.59 | -0.544% |
| 3 CALL2H AND CALL4H | Cyan | 7 | 2 | 5 | 28.57% | $-32.85 | -0.469% |
| 4 C2H AND C4H | Cyan | 1 | 0 | 1 | 0.00% | $-16.12 | -1.612% |

## Interpretation

All four either-color variants lost money in this recorded sample after the modeled cost. Yellow C2H OR C4H was the strongest color-specific variant: 24 trades, 11 wins, 13 losses, +$49.54 total on $1,000 per trade, mean net ROI +0.206%. That one-day observation does not establish a repeatable edge. A higher peak gain is not the gain the EMA rule captured.

## Coverage and limitations

- Source book contains 363 monitored symbols. Filtering drawn and first-seen times to this morning leaves 223 eligible recorded arrows in 94 tickers. Prices were retrieved for these tickers through the configured Schwab client; input copies are saved in prices/.
- The recorder suppresses compact C2H/C4H arrows before 09:30. Consequently rules 2 and 4 do NOT include all possible premarket chart signals. This is a material limitation, not evidence that no such signals existed.
- Candidate-based monitoring and periodic scans can delay first-seen times or miss transient arrows. The slow monitoring lane was added on September 29. Recorded timing measures scanner observation, not the earliest time an independently open chart could have shown an arrow.
- The daily signal book retains first-seen and disappearance times but not every disappear/reappear interval. AND overlap reconstruction can therefore be imperfect. No current end-of-day arrows were backdated as executable signals.
- Exact missing entry or exit bars are excluded, never filled using a later convenient price. The audit includes every excluded first candidate. Missing regular-session bars before exit invalidate a trade. Gaps before 09:30 are flagged in the ledger and may reflect no-trade periods or incomplete data.
- EMA results depend on the vendor tape and extended-hours settings; a chart using a different overnight feed can differ. Price history was retrieved later and may include vendor corrections.
- This is a one-day test with incomplete signal coverage, not evidence to select a profitable strategy. Option contract selection, spreads, IV and time decay were not modeled.

## Complete either-color trade lists

Times below are ET. Net P&L is per $1,000 position; peak gain is gross. Full precision and chart-drawn/seen/disappeared timestamps are in the CSV.

### 1 CALL2H OR CALL4H

| Ticker | Entry ET | Entry $ | Exit ET | Exit $ | Result | Net P&L $ | Net ROI | Max gain |
|---|---|---:|---|---:|---|---:|---:|---:|
| GEV | 07:25 | 957.0000 | 10:05 | 961.3900 | WIN | +3.59 | +0.36% | +1.36% |
| NBIS | 07:25 | 235.4225 | 08:30 | 234.5000 | LOSS | -4.92 | -0.49% | +0.08% |
| SOXL | 07:25 | 146.1100 | 08:55 | 146.2700 | WIN | +0.10 | +0.01% | +1.35% |
| MRNA | 07:45 | 195.6500 | 07:50 | 195.6800 | LOSS | -0.85 | -0.08% | +0.02% |
| BMNR | 07:55 | 27.6200 | 09:35 | 27.1700 | LOSS | -17.29 | -1.73% | +1.34% |
| COIN | 07:55 | 195.8750 | 09:35 | 193.6750 | LOSS | -12.23 | -1.22% | +0.90% |
| DRAM | 07:55 | 61.1700 | 09:35 | 60.6900 | LOSS | -8.85 | -0.88% | +0.61% |
| HOOD | 07:55 | 118.7100 | 09:35 | 117.6300 | LOSS | -10.10 | -1.01% | +0.45% |
| MSTR | 07:55 | 161.1500 | 09:35 | 157.6478 | LOSS | -22.73 | -2.27% | +0.39% |
| UMAC | 07:55 | 24.2100 | 09:30 | 24.1200 | LOSS | -4.72 | -0.47% | +1.01% |
| HPE | 08:00 | 63.0815 | 08:30 | 63.1481 | WIN | +0.06 | +0.01% | +0.11% |
| AAL | 08:05 | 13.7500 | 09:35 | 13.6600 | LOSS | -7.55 | -0.75% | +1.24% |
| LITE | 08:05 | 932.5500 | 11:25 | 968.9900 | WIN | +38.08 | +3.81% | +7.11% |
| MSTY | 08:25 | 16.7277 | 09:35 | 16.4490 | LOSS | -17.66 | -1.77% | +0.61% |
| QXO | 08:30 | 12.2700 | 09:05 | 12.2600 | LOSS | -1.81 | -0.18% | +0.24% |
| CELH | 08:55 | 28.1484 | 09:15 | 28.0300 | LOSS | -5.21 | -0.52% | +0.11% |
| NASA | 08:55 | 24.4200 | 09:35 | 24.3200 | LOSS | -5.10 | -0.51% | +0.94% |
| NOK | 09:10 | 10.3300 | 09:35 | 10.2850 | LOSS | -5.36 | -0.54% | +0.58% |
| NCLH | 09:20 | 14.9500 | 11:25 | 14.8850 | LOSS | -5.35 | -0.53% | +2.17% |
| AA | 09:25 | 42.4000 | 09:30 | 42.3300 | LOSS | -2.65 | -0.27% | +0.00% |
| ASTS | 09:25 | 64.4400 | 09:35 | 63.3100 | LOSS | -18.54 | -1.85% | +1.60% |
| CBOE | 09:25 | 278.8700 | 09:40 | 266.1050 | LOSS | -46.77 | -4.68% | +1.12% |
| CCL | 09:25 | 24.5100 | 12:10 | 24.6850 | WIN | +6.14 | +0.61% | +2.65% |
| CEG | 09:25 | 261.8200 | 09:30 | 261.7250 | LOSS | -1.36 | -0.14% | +0.00% |
| DAL | 09:25 | 85.6800 | 09:35 | 84.6300 | LOSS | -13.25 | -1.33% | +0.14% |
| FCEL | 09:25 | 17.7400 | 09:30 | 17.5150 | LOSS | -13.68 | -1.37% | +0.85% |
| FLY | 09:25 | 24.5900 | 09:40 | 24.2028 | LOSS | -16.75 | -1.67% | +2.36% |
| MRVL | 09:25 | 257.5000 | 12:45 | 261.4700 | WIN | +14.42 | +1.44% | +2.40% |
| RKLB | 09:25 | 73.1800 | 09:35 | 72.6400 | LOSS | -8.38 | -0.84% | +1.46% |
| RXRX | 09:25 | 3.7490 | 09:30 | 3.7300 | LOSS | -6.07 | -0.61% | +0.03% |
| TMC | 09:25 | 3.9200 | 09:30 | 3.9700 | WIN | +11.76 | +1.18% | +1.28% |
| UAL | 09:25 | 113.6900 | 09:35 | 113.0900 | LOSS | -6.28 | -0.63% | +0.57% |
| UEC | 09:25 | 9.7200 | 09:35 | 9.3900 | LOSS | -34.95 | -3.50% | +2.26% |
| HIMS | 09:30 | 29.3900 | 10:10 | 29.0500 | LOSS | -12.57 | -1.26% | +1.51% |
| BE | 09:35 | 282.9550 | 12:55 | 295.6700 | WIN | +43.94 | +4.39% | +6.86% |
| BKNG | 09:35 | 162.1400 | 09:40 | 162.2700 | LOSS | -0.20 | -0.02% | +0.18% |
| GRAB | 09:35 | 3.1400 | 09:50 | 3.1200 | LOSS | -7.37 | -0.74% | +0.32% |
| MU | 09:35 | 1062.5100 | 09:40 | 1064.9100 | WIN | +1.26 | +0.13% | +0.30% |
| ONON | 09:35 | 29.7000 | 09:40 | 29.5782 | LOSS | -5.10 | -0.51% | +0.07% |
| RDW | 09:35 | 11.2600 | 09:40 | 11.1250 | LOSS | -12.99 | -1.30% | +0.89% |
| RMBS | 09:35 | 103.5800 | 11:10 | 103.4350 | LOSS | -2.40 | -0.24% | +0.97% |
| SMCI | 09:35 | 42.1500 | 09:45 | 42.0800 | LOSS | -2.66 | -0.27% | +0.62% |
| SNDK | 09:35 | 1709.7521 | 09:40 | 1709.0600 | LOSS | -1.40 | -0.14% | +0.27% |
| SPCE | 09:35 | 3.1699 | 09:40 | 3.1677 | LOSS | -1.69 | -0.17% | +0.00% |
| SSTI | 09:35 | 8.3750 | 12:25 | 8.2400 | LOSS | -17.12 | -1.71% | +0.06% |
| KMX | 09:40 | 60.2000 | 10:30 | 60.6500 | WIN | +6.48 | +0.65% | +3.60% |
| SNOW | 09:40 | 330.7301 | 09:45 | 327.4000 | LOSS | -11.07 | -1.11% | +0.25% |
| CRWV | 09:45 | 86.1600 | 11:15 | 87.9531 | WIN | +19.81 | +1.98% | +5.00% |
| COST | 09:50 | 919.3000 | 10:00 | 920.0000 | LOSS | -0.24 | -0.02% | +0.19% |
| ALAB | 09:55 | 362.3400 | 12:35 | 363.7450 | WIN | +2.88 | +0.29% | +1.20% |
| AMD | 09:55 | 621.4600 | 10:20 | 615.5400 | LOSS | -10.53 | -1.05% | +0.43% |
| QMCO | 09:55 | 33.0050 | 11:25 | 33.2500 | WIN | +6.42 | +0.64% | +5.07% |

### 2 C2H OR C4H

| Ticker | Entry ET | Entry $ | Exit ET | Exit $ | Result | Net P&L $ | Net ROI | Max gain |
|---|---|---:|---|---:|---|---:|---:|---:|
| AEVA | 09:35 | 14.5300 | 09:55 | 14.5500 | WIN | +0.38 | +0.04% | +2.31% |
| AVGO | 09:35 | 357.7600 | 11:20 | 358.8700 | WIN | +2.10 | +0.21% | +1.15% |
| CAVA | 09:35 | 51.8050 | 10:50 | 52.4100 | WIN | +10.68 | +1.07% | +2.44% |
| COHR | 09:35 | 286.4550 | 11:20 | 296.1500 | WIN | +32.84 | +3.28% | +5.68% |
| GRAB | 09:35 | 3.1400 | 09:50 | 3.1200 | LOSS | -7.37 | -0.74% | +0.32% |
| HIMS | 09:35 | 29.3050 | 10:10 | 29.0500 | LOSS | -9.70 | -0.97% | +1.80% |
| NASA | 09:35 | 24.3200 | 09:40 | 24.2000 | LOSS | -5.93 | -0.59% | +0.29% |
| NFLX | 09:35 | 70.5935 | 11:20 | 70.5100 | LOSS | -2.18 | -0.22% | +0.79% |
| POET | 09:35 | 7.5800 | 11:20 | 7.7450 | WIN | +20.77 | +2.08% | +4.55% |
| SNOW | 09:35 | 333.0000 | 09:45 | 327.4000 | LOSS | -17.82 | -1.78% | +0.12% |
| VRT | 09:35 | 251.8900 | 10:50 | 251.4100 | LOSS | -2.91 | -0.29% | +0.89% |
| AFRM | 09:40 | 69.3900 | 10:50 | 68.8200 | LOSS | -9.21 | -0.92% | +1.33% |
| DELL | 09:40 | 548.7639 | 09:45 | 542.9000 | LOSS | -11.69 | -1.17% | +0.08% |
| FCEL | 09:40 | 17.2700 | 09:45 | 17.3100 | WIN | +1.32 | +0.13% | +1.48% |
| LMND | 09:40 | 44.1250 | 11:15 | 44.3700 | WIN | +4.55 | +0.46% | +1.78% |
| OKLO | 09:40 | 37.2562 | 09:55 | 37.5400 | WIN | +6.62 | +0.66% | +1.73% |
| ORCL | 09:40 | 134.1350 | 11:30 | 138.7500 | WIN | +33.41 | +3.34% | +7.11% |
| UBER | 09:40 | 69.1900 | 10:50 | 68.9650 | LOSS | -4.25 | -0.43% | +1.01% |
| VST | 09:40 | 141.1000 | 10:45 | 140.9700 | LOSS | -1.92 | -0.19% | +1.71% |
| AAOI | 09:45 | 100.1350 | 11:25 | 101.5700 | WIN | +13.33 | +1.33% | +3.69% |
| BA | 09:45 | 189.5050 | 11:05 | 188.7200 | LOSS | -5.14 | -0.51% | +0.74% |
| BE | 09:45 | 286.6700 | 12:55 | 295.6700 | WIN | +30.39 | +3.04% | +5.47% |
| CART | 09:45 | 43.7400 | 11:45 | 43.8900 | WIN | +2.43 | +0.24% | +1.10% |
| CEG | 09:45 | 268.6000 | 10:50 | 264.5375 | LOSS | -16.12 | -1.61% | +0.56% |
| CIEN | 09:45 | 350.3800 | 10:05 | 349.2451 | LOSS | -4.24 | -0.42% | +1.66% |
| GE | 09:45 | 323.1700 | 10:15 | 319.7500 | LOSS | -11.58 | -1.16% | +0.05% |
| NBIS | 09:45 | 238.8800 | 11:25 | 241.3600 | WIN | +9.38 | +0.94% | +3.39% |
| QUBT | 09:45 | 8.6700 | 09:50 | 8.6307 | LOSS | -5.53 | -0.55% | +0.12% |
| RCAT | 09:45 | 6.5450 | 09:50 | 6.5400 | LOSS | -1.76 | -0.18% | +0.31% |
| RXRX | 09:45 | 3.9550 | 10:10 | 3.7900 | LOSS | -42.72 | -4.27% | +1.14% |
| UEC | 09:45 | 9.3700 | 09:50 | 9.2850 | LOSS | -10.07 | -1.01% | +0.11% |
| BEAM | 09:50 | 24.2200 | 09:55 | 24.0650 | LOSS | -7.40 | -0.74% | +0.00% |
| CELH | 09:50 | 28.4700 | 10:20 | 28.0300 | LOSS | -16.45 | -1.65% | +0.23% |
| CVNA | 09:50 | 63.6300 | 11:40 | 63.9200 | WIN | +3.56 | +0.36% | +1.67% |
| ALAB | 09:55 | 362.3400 | 12:35 | 363.7450 | WIN | +2.88 | +0.29% | +1.20% |
| INFY | 09:55 | 10.5650 | 11:25 | 10.5700 | LOSS | -0.53 | -0.05% | +0.71% |
| MCD | 09:55 | 234.7265 | 11:55 | 234.8301 | LOSS | -0.56 | -0.06% | +0.55% |
| MRVL | 09:55 | 262.6050 | 12:45 | 261.4700 | LOSS | -5.32 | -0.53% | +0.41% |
| NOK | 09:55 | 10.4350 | 10:50 | 10.3801 | LOSS | -6.26 | -0.63% | +0.72% |

### 3 CALL2H AND CALL4H

| Ticker | Entry ET | Entry $ | Exit ET | Exit $ | Result | Net P&L $ | Net ROI | Max gain |
|---|---|---:|---|---:|---|---:|---:|---:|
| GEV | 07:25 | 957.0000 | 10:05 | 961.3900 | WIN | +3.59 | +0.36% | +1.36% |
| HPE | 08:00 | 63.0815 | 08:30 | 63.1481 | WIN | +0.06 | +0.01% | +0.11% |
| AAL | 08:10 | 13.7300 | 09:35 | 13.6600 | LOSS | -6.10 | -0.61% | +1.38% |
| SOXL | 09:05 | 146.6100 | 09:35 | 146.4800 | LOSS | -1.89 | -0.19% | +1.90% |
| NCLH | 09:20 | 14.9500 | 11:25 | 14.8850 | LOSS | -5.35 | -0.53% | +2.17% |
| DAL | 09:25 | 85.6800 | 09:35 | 84.6300 | LOSS | -13.25 | -1.33% | +0.14% |
| FLY | 09:25 | 24.5900 | 09:40 | 24.2028 | LOSS | -16.75 | -1.67% | +2.36% |
| INFQ | 09:25 | 14.2400 | 09:30 | 14.3800 | WIN | +8.83 | +0.88% | +0.98% |
| UAL | 09:25 | 113.6900 | 09:35 | 113.0900 | LOSS | -6.28 | -0.63% | +0.57% |
| CHPY | 09:30 | 70.0900 | 09:35 | 69.8150 | LOSS | -4.92 | -0.49% | +0.11% |
| MRNA | 09:35 | 199.5000 | 10:10 | 198.0900 | LOSS | -8.07 | -0.81% | +4.71% |
| SSTI | 09:35 | 8.3750 | 12:25 | 8.2400 | LOSS | -17.12 | -1.71% | +0.06% |
| CLS | 09:40 | 372.7400 | 11:05 | 367.5000 | LOSS | -15.06 | -1.51% | +0.47% |

### 4 C2H AND C4H

| Ticker | Entry ET | Entry $ | Exit ET | Exit $ | Result | Net P&L $ | Net ROI | Max gain |
|---|---|---:|---|---:|---|---:|---:|---:|
| CAVA | 09:45 | 52.6100 | 10:50 | 52.4100 | LOSS | -4.80 | -0.48% | +0.87% |
| CEG | 09:45 | 268.6000 | 10:50 | 264.5375 | LOSS | -16.12 | -1.61% | +0.56% |

## Excluded first candidates — either-color rules

| Ticker | Rule | Reason |
|---|---|---|
| BILI | 1 CALL2H OR CALL4H | missing exact next entry bar |
| BILI | 3 CALL2H AND CALL4H | missing exact next entry bar |
| CHPY | 1 CALL2H OR CALL4H | missing exact next entry bar |
| CLS | 1 CALL2H OR CALL4H | missing exact next entry bar |
| DJT | 1 CALL2H OR CALL4H | missing exact next entry bar |
| DJT | 3 CALL2H AND CALL4H | missing exact next entry bar |
| GME | 1 CALL2H OR CALL4H | missing exact next entry bar |
| HSBC | 1 CALL2H OR CALL4H | missing exact next entry bar |
| IBIT | 1 CALL2H OR CALL4H | missing exact next entry bar |
| INFQ | 1 CALL2H OR CALL4H | missing exact next entry bar |
| IONQ | 1 CALL2H OR CALL4H | missing exact next entry bar |
| IONQ | 3 CALL2H AND CALL4H | missing exact next entry bar |
| MRK | 1 CALL2H OR CALL4H | missing exact next entry bar |
| NET | 1 CALL2H OR CALL4H | missing exact next entry bar |
| PDD | 1 CALL2H OR CALL4H | missing exact next entry bar |
| PHM | 1 CALL2H OR CALL4H | missing exact next entry bar |
| SBET | 1 CALL2H OR CALL4H | missing exact next entry bar |
| ULTA | 1 CALL2H OR CALL4H | missing exact next entry bar |
| ULTA | 3 CALL2H AND CALL4H | missing exact next entry bar |
| WBD | 1 CALL2H OR CALL4H | missing exact EMA exit bar |
| WDC | 2 C2H OR C4H | entry after morning cutoff |

## Validation and reproduction

- Checked simultaneous versus non-overlapping AND signals and same-color filtering; completed-candle next-open exits; exclusion of the exit bar’s later high from maximum gain; unique ticker/rule/color rows; entry/exit chronology; return/P&L arithmetic; summary reconciliation.
- Research scripts: artifacts/backtest_morning_arrows.py and artifacts/write_morning_arrow_report.py. Inputs: signal-source.json, source-metadata.json, and prices/. Outputs: results.json, summary.csv, trades.csv (all color variants), trades-either-color.csv, candidate-audit.csv.
- No trading orders or app code changes. Production build and browser smoke tests do not apply to these standalone research outputs.