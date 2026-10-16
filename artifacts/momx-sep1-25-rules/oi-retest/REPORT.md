# GO + short RVOL + MACD: historical OI break/retest experiment

Period: September 1–25, 2026. Times: America/New_York (EDT, UTC−4). Research replay of stored application data, not live trading. No app code changed.

## Result

This particular retest rule did not demonstrate an improvement. Only two of the original 41 bull candidates qualified: one winner and one loser. That is far too small a sample to estimate a dependable win rate.

| Variant | Trades | Wins | Losses | Win rate | Mean net stock return/trade |
|---|---:|---:|---:|---:|---:|
| Original GO + short RVOL + MACD, +2% target / −1.5% stop | 41 | 27 | 14 | 65.9% | +0.702% |
| OI break/retest, structural stop / next known level | 2 | 1 | 1 | 50.0% | +0.697% |
| Original GO entries for those same two ticker-days | 2 | 2 | 0 | 100.0% | +1.900% |
| Retest entries with original +2% / −1.5% exits | 2 | 1 | 1 | 50.0% | +0.150% |

These are per-trade returns, not portfolio returns. All deduct 0.10 percentage point round-trip cost. Win means positive net return. The 41-trade baseline and two-trade subset are not equivalent samples; the matched comparison is supplied to expose that selection effect.

| Date | Ticker | Broken OI strike | Confirmation ET | Entry ET | Entry | Stop | Target | Outcome | Net |
|---|---|---:|---|---|---:|---:|---:|---|---:|
| Sep 17 | MRNA | 160 | 10:15 | 10:20 | 161.37 | 159.4068 | 165 | Stop, 10:40 | −1.317% |
| Sep 18 | MSTR | 145 | 10:25 | 10:30 | 145.90 | 144.8448 | 150 | Target, 12:20 | +2.710% |

Of 41 candidates, 37 had no qualifying break/retest confirmation. Two more had a retest but failed the target/entry/room checks. Historical prior-day OI was available for all 41.

## Exact experimental definition

1. Start with the original 41 first-per-ticker/day bull GO + short RVOL + MACD signals. This is not the separate additional 0.5%-from-open experiment or a fresh search across every stock.
2. Use the latest previous-dated option-chain snapshot within seven calendar days, captured before that session opened. Never use same-day closing OI. Aggregate CALL open interest by strike for expirations from the trade date through 31 calendar days later.
3. Consider strikes within 10% of the opening stock price. Strong OI means at least 66% of the largest aggregate CALL OI in that window. This is an explicit exploratory definition, not proof that those strikes act as support/resistance or that OI is bullish.
4. Require a completed 5-minute close crossing above a strong strike from a prior close at/below it. Opening above a strike alone does not qualify.
5. Require a later retest within 60 minutes of the break and no later than 60 minutes after the original GO signal (also before 15:30). Its low must touch the level within max($0.01, 10% of 14-bar average true range), then close above the level and above its own open. An intervening close below the level invalidates that break.
6. Recheck positive, increasing 5-minute MACD histogram at confirmation. The original scanner grade/RVOL qualification is not refreshed at the retest; this is a GO-qualified watchlist followed by a price/MACD trigger.
7. Enter at the first 5-minute bar open strictly after confirmation. Because confirmation is itself a bar boundary, this deliberately imposes a five-minute execution delay, matching the baseline replay convention. Results can be sensitive to that delay; they do not measure immediate execution at the confirmation close.
8. Stop below the lower of the retest low and strike by the tolerance above. Target the closest known level above entry from strong OI strikes, prior session high, premarket high, and opening five-minute high. Require reward/risk at least 1:1 at entry, and entry above the broken level. Select the first eligible trade for each original candidate.
9. Exit at target, stop, or session close. Stop gaps fill at the bar open; target gaps receive no positive price improvement. If both levels occur in one bar, stop takes precedence. Subtract 0.10 percentage point cost.

## Scope and limitations

- This tests one precise interpretation of the image, not every listed consideration. It does not add daily/weekly EMA, pivots, expected move, discretionary alternate-lower-OI entries, or arbitrary $1/$2.50/$5 level spacing.
- Stock returns do not establish option returns: premiums, spread, IV, theta, and contract selection were not modeled.
- Bear scanner history is incomplete for this period. A comparable bear win rate remains unavailable, not 0%.
- Historical data is the application's saved/recovered data, not independently reconciled exchange data. The starting universe is the earlier weekly-options replay and inherits its coverage/selection limitations.
- The parameters were not optimized across alternative thresholds. Two trades cannot justify adopting or rejecting retests generally. A separate forward/out-of-sample test is needed before claiming an edge.
- Checks: baseline fill self-tests, structural same-bar stop-first test, source capture timestamps before the session, entry after confirmation, unique ticker-day trades, ordered stop/entry/target, and aggregate arithmetic. No application build/browser checks were needed because this is an isolated research script and report.

## Reproduction and audit files

- Script: `artifacts/backtest_go_oi_retest.py` (imports `momx_rules_backtest_sep2026.py`).
- Read-only input: `AgenticAI-Trading 2/database/trades.db`, `option_chain_daily_snapshots`; original price caches and recovered prices used by the baseline.
- `trades.csv`: every executed trade, stop, target, timestamps and matched baseline return.
- `candidate-audit.csv`: all 41 candidate outcomes, including skipped entries.
- `historical-levels.csv`: OI strikes and snapshot/capture provenance.
- `results.json`: machine-readable results.
