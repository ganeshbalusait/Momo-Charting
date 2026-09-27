# MomX September 1–25, 2026: independent review

## Conclusion

The available evidence does not establish a best-performing live filter. GO is a clear opening-breakout hypothesis worth testing; Claude's quoted 64% / +0.88% result was not independently reproduced. The current MomoX A+ rule includes an open-interest gate added September 26 that its source explicitly says was not backtested. Do not transfer the old rule's performance to the new rule.

This is an audit with a partial independent replay, not a complete reproduction of the quoted report. The cloud UI was inspected at https://app.agxtrade.com/. Calculations used the local archive under `C:/GANESH/AgenticAI-Trading 7/AgenticAI-Trading 2/artifacts`; equivalence of local and cloud historical datasets was not established.

## What was verified in the cloud UI

- Watchlist displays 358 symbols, with 356 optionable and 2 excluded in the observed view.
- The chart-link colour control exists and ticker buttons identify their linked chart colour. Its click-through operation was not tested.
- Filters include Best setups, GO only, OPT only, Daily 2, MomoX-related tags elsewhere, and other experimental strategies. A MomoX A+ dropdown was not present in the inspected filter menu.
- Filters were OFF. Saved configuration was ANY of RVOL (1h/2h/4h >=2.5) OR SQZ (2h/4h cyan or blank). Skittles filtering was OFF; starring was ON. The displayed filter count was 13 of 14 scan matches, with RVOL passing 0 and SQZ passing 13. This illustrates how permissive the saved OR configuration is; it is an after-hours observation, not a market-hours performance test.
- Several RVOL cells explicitly identify closing-auction volume and are dimmed. An auction flag is a classification, not proof that the price cannot subsequently rise.
- No trading settings or orders were changed.

## Data and reproducibility

The Watchlist archive contains 25 calendar-date files in the requested range, including thin weekend/holiday records and 18 substantial session files. All snapshot offsets counted in this audit were UTC-04:00. An initial concern about local-time filtering therefore did not affect this particular archive. The existing backtest's string slicing remains less robust than explicit timezone conversion.

History contains scan-matched snapshots and caps individual symbol histories. September 21 has 129 truncated symbol records, so late signals cannot be assumed fully observed. Dedicated Watchlist grade-event files exist only for September 22–25.

The weekly-options membership used here is a September 26 census, not a point-in-time September 1 list. The current grade formula excludes dark-green Skittles backgrounds as standalone bullish crosses; its code documents that this changed September 22. Replaying the current formula does not recreate every historical displayed grade.

I found 462 first A+ and 366 first A symbol/day candidates between 09:30 and 15:30 ET among the current weekly-options names. Only 113 A+ and 98 A candidates had cached intraday data that met this audit's minimum scoring requirements. The remaining 349 A+ and 268 A candidates were excluded, not treated as losses or wins. Coverage is too incomplete and uneven to generalize the subset to the full period.

## Partial independent replay

Each letter is scored separately; an individual ticker can appear once in A and once in A+ on the same day. Entries use the next available one-minute bar opening strictly after the archived signal timestamp. These are simulated stock entries. No option fills or returns were calculated.

| Rule | Scored / identified | Positive to close | Mean to close | Median to close |
|---|---:|---:|---:|---:|
| Current A+ formula | 113 / 462 | 48.67% | +0.121% | -0.064% |
| Current A formula | 98 / 366 | 44.90% | +0.045% | -0.141% |

On the same 113 A+ observations, illustrative target/stop simulations gave:

| Stock target | Stock stop | Positive outcomes | Mean outcome |
|---|---:|---:|---:|
| +1% | -1.5% | 56.64% | -0.042% |
| +2% | -1.5% | 46.90% | +0.011% |
| +4% | -1.5% | 43.36% | +0.154% |

If neither level is reached, the trade exits at the last regular-session close. If both occur inside the same minute, the stop is assigned first. These simplified fills omit fees, spreads, slippage, and adverse gaps through stops. Missing internal bars were not independently repaired or certified; the scorer requires a 15:59 final bar but does not certify uninterrupted tape. Consequently these figures are diagnostics, not executable-strategy performance estimates. A larger target had a higher sample mean but a lower positive-outcome rate; that alone does not establish an optimal exit.

## Assessment of Claude's claims

| Claim | Audit assessment |
|---|---|
| GO: 64% win, +0.88% per trade | Unverified. The exact trade list, scoring implementation, and random-pick control were not found in the scoped local source/data. Archived rows did not yield reconstructible GO qualifications in this audit; this is missing evidence, not zero historical GO signals. |
| Star #1: only 24% losses | Unverified. Requires the full eligible universe and ranking at each decision time. Re-ranking surviving history after the day is not equivalent. Also clarify whether flat trades explain the rest. |
| MomoX A+: 58%, +0.73% last week | Unverified for the quoted population. More importantly, September 26's required OI gate changes the strategy. Its source explicitly acknowledges absent historical OI validation. |
| Daily 2: 54% | Unverified for September 1–25. Local strategy records start September 23. Define whether only the first two names count: the UI implementation retains and numbers additional qualifying names. |
| MACD up plus cyan short-timeframe RVOL doubles wins | Needs numerator, denominator, matched baseline, and signal-time inputs. Not established by this audit. |
| All bear tags have no edge | Not established by this bullish replay. A separate bearish dataset and identical execution assumptions are required. |
| GO, etc. beat random picks | Requires an explicit control matched by session, signal time, eligible universe, and holding/exit rules. The quotation supplies no such control. |
| +4% target is better | Weak directional agreement in the incomplete A+ subset's average, but this does not reproduce the quoted GO/MomoX/Daily 2 study or validate a future exit. |

The existing stored recorded-grade summary covers September 22–25: A+ count 193, 48.2% higher at close, mean +0.337%, median -0.056%. This is an app-produced aggregate across its recorded population, not a weekly-only independent result, and must not be merged with the replay above.

## Which filter to investigate first

For the user's goal of catching a META-like opening move, GO has the clearest price-based definition: A/A+, gap >=2%, a completed early five-minute candle above VWAP/open/first-five-minute high, and directional 30-minute DI alignment. This is a fit-to-purpose assessment, not a verified profitability ranking. It will miss gradual movers that lack the gap.

Best setups is GO OR early OPT. OPT is remembered after first qualification, so remaining on that list does not establish momentum is still strong. Strategy selection bypasses the standalone Momentum/Pattern/RVOL/SQZ/Skittles gates. A user cannot assume that selecting Best setups plus Building creates an AND filter.

News + momentum checks current momentum but depends on AI headline classification and has no independently reproduced full-period result here. SOLO and hot-sector leaders are supplementary discovery views; high volume alone does not identify the buyer or prove institutional accumulation.

Before choosing a winner, obtain the original per-trade September 1–25 scorecard, freeze rule versions, fill the missing price coverage, use signal-time rankings/membership, and score all strategies plus a matched control with the same entry/exit model. Evaluate future sessions separately from the period used to invent the rules.

## Reproduction

Research script: `artifacts/audit_momx_sep1_25.py`.
Machine-readable coverage, results, and per-event rows: `artifacts/momx-sep1-25-audit.json`.
No application code was modified. The script completed successfully; this was a read-only source/data audit plus report generation, not a production build or browser regression test.
