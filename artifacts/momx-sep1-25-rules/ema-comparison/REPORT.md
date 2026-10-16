# GO: MACD versus EMA confirmation — September 1–25, 2026

Same archived weekly-options Watchlist universe, GO reconstruction and cyan RVOL background on any of 5m/15m/30m. EMA means fast EMA strictly above slow EMA on the last completed five-minute bar, not a fresh crossover. EMA uses the full available tape, including extended hours, exactly as the MACD input does. Each rule takes its own first qualifying snapshot per ticker/day. Entry next five-minute open; +2% stock target, -1.5% stop, otherwise close; stop-first for ambiguous bars; 0.10 percentage-point roundtrip cost.

| Rule | Trades | Wins | Losses | Win rate | Mean net | Final-week N | Final-week win rate | Final-week mean net |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| GO | 63 | 39 | 24 | 61.9% | +0.601% | 15 | 46.7% | -0.062% |
| GO + short RVOL | 44 | 28 | 16 | 63.6% | +0.629% | 9 | 44.4% | -0.235% |
| GO + short RVOL + MACD | 41 | 27 | 14 | 65.9% | +0.702% | 7 | 42.9% | -0.233% |
| GO + short RVOL + EMA 4/8 | 44 | 28 | 16 | 63.6% | +0.629% | 9 | 44.4% | -0.235% |
| GO + short RVOL + EMA 9/21 | 44 | 28 | 16 | 63.6% | +0.629% | 9 | 44.4% | -0.235% |

Validation: eight execution/data-integrity assertions; exact full-ledger parity for the original GO and MACD baselines; 236 historical-prefix EMA checks. No production application code changed.

Exploratory comparison on the same period used to develop the rules; overlapping trades and different entry times prevent interpreting small percentage differences as established superiority. All original report coverage and reconstruction limitations apply. These are stock returns; options and account returns were not simulated. No app filter was added.