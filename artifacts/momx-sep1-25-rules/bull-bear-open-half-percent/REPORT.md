# GO + short RVOL + MACD: September 1–25, 2026

Scope: current weekly-options Watchlist universe, 18 regular trading sessions. All times Eastern. Bull archive covers the full period; actual bear Watchlist archive exists only September 25. Earlier bear rows below are an INCOMPLETE proxy reconstructed from snapshots selected and retained by the bull scanner, not a full bear backtest. Missing bear dates must not be counted as zero-signal days.

Entry: next five-minute opening strictly after the first qualifying snapshot per ticker/day/direction/variant. GO requires a 2% gap in the trade direction and an opening-range/VWAP break by 10:30; A/A+, cyan (bull) or magenta (bear) RVOL on any 5m/15m/30m, completed 5m MACD positive/rising or negative/falling, completed 30m DI directional control. This is the existing GO rule, not a new 0.5% gap rule.

The original variant preserves the rule. The open-move variant additionally requires the last completed price at signal to be at least +0.5% from the 09:30 open for bull, or -0.5% for bear; it can first qualify later. Both variants are reported because the requested 0.5% meaning was ambiguous.

Each target is simulated separately with a 1.5% adverse stop and market-close fallback. Entry/stop gaps respected; when target and stop both occur in one five-minute bar, stop wins. WIN means positive return after 0.10 percentage-point assumed round-trip cost. Bear returns model short STOCK positions without borrow availability/fees, not put options. No portfolio capital constraint.

Best move after entry is the favorable high (bull) or low (bear) anywhere before the close, even if a stop had already occurred. It is hindsight opportunity, not realized profit or an achievable exit. Day high/low may have occurred BEFORE entry. Only complete 78-bar regular sessions are scored. Original bull +2% outcomes reproduce the earlier 41-trade baseline exactly.

| Variant | Direction / source | Target | Trades | Wins | Losses | Win rate | Average net |
|---|---|---:|---:|---:|---:|---:|---:|
| original | bull / bull archive | 0.5% | 41 | 34 | 7 | 82.9% | +0.086% |
| original | bull / bull archive | 1% | 41 | 31 | 10 | 75.6% | +0.318% |
| original | bull / bull archive | 2% | 41 | 27 | 14 | 65.9% | +0.702% |
| original | bear / bear archive | 0.5% | 0 | 0 | 0 | N/A | N/A |
| original | bear / bear archive | 1% | 0 | 0 | 0 | N/A | N/A |
| original | bear / bear archive | 2% | 0 | 0 | 0 | N/A | N/A |
| original | bear / bull-archive proxy | 0.5% | 0 | 0 | 0 | N/A | N/A |
| original | bear / bull-archive proxy | 1% | 0 | 0 | 0 | N/A | N/A |
| original | bear / bull-archive proxy | 2% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bull / bull archive | 0.5% | 40 | 34 | 6 | 85.0% | +0.128% |
| open_move_at_least_0.5 | bull / bull archive | 1% | 40 | 31 | 9 | 77.5% | +0.366% |
| open_move_at_least_0.5 | bull / bull archive | 2% | 40 | 27 | 13 | 67.5% | +0.759% |
| open_move_at_least_0.5 | bear / bear archive | 0.5% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bear / bear archive | 1% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bear / bear archive | 2% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bear / bull-archive proxy | 0.5% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bear / bull-archive proxy | 1% | 0 | 0 | 0 | N/A | N/A |
| open_move_at_least_0.5 | bear / bull-archive proxy | 2% | 0 | 0 | 0 | N/A | N/A |

## Stock list by date

W/L columns correspond to target +0.5%, +1%, +2%, each with the 1.5% stop. Percentages in Max favorable are measured from entry in the trade direction. The full ledger also supplies session open, day high/low, price at the favorable extreme, maximum adverse move, and the move from the open at signal.

### original

| Date | Side / source | Ticker | Entry ET | Entry price | W/L 0.5 / 1 / 2% | Net at 0.5% target | Max favorable after entry |
|---|---|---|---|---:|---|---:|---:|
| 2026-09-01 | bull / bull archive | USO | 12:25 | 139.5000 | WIN / WIN / WIN | +0.400% | +1.104% |
| 2026-09-03 | bull / bull archive | BULL | 09:45 | 9.6224 | WIN / WIN / WIN | +0.400% | +5.899% |
| 2026-09-03 | bull / bull archive | HOOD | 09:45 | 120.6700 | LOSS / LOSS / LOSS | -1.600% | +3.489% |
| 2026-09-03 | bull / bull archive | PATH | 09:45 | 18.7150 | WIN / LOSS / LOSS | +0.400% | +0.614% |
| 2026-09-03 | bull / bull archive | SBET | 09:45 | 8.5900 | WIN / WIN / WIN | +0.400% | +4.948% |
| 2026-09-03 | bull / bull archive | BMNR | 10:05 | 24.5450 | WIN / WIN / WIN | +0.400% | +8.372% |
| 2026-09-04 | bull / bull archive | SNDK | 10:05 | 1660.4400 | WIN / WIN / WIN | +0.400% | +4.792% |
| 2026-09-08 | bull / bull archive | FCX | 09:50 | 77.2800 | WIN / WIN / LOSS | +0.400% | +1.760% |
| 2026-09-08 | bull / bull archive | BE | 09:55 | 276.0000 | WIN / WIN / LOSS | +0.400% | +2.837% |
| 2026-09-09 | bull / bull archive | NET | 09:45 | 304.7600 | WIN / WIN / WIN | +0.400% | +4.197% |
| 2026-09-09 | bull / bull archive | FSLY | 10:00 | 23.8400 | WIN / WIN / WIN | +0.400% | +3.146% |
| 2026-09-11 | bull / bull archive | HPE | 09:45 | 59.7200 | WIN / WIN / WIN | +0.400% | +4.069% |
| 2026-09-11 | bull / bull archive | DELL | 09:50 | 548.0100 | WIN / WIN / WIN | +0.400% | +3.602% |
| 2026-09-11 | bull / bull archive | SBET | 09:55 | 9.0200 | WIN / WIN / WIN | +0.400% | +5.155% |
| 2026-09-14 | bull / bull archive | NOW | 09:45 | 139.7800 | LOSS / LOSS / LOSS | -1.600% | +2.439% |
| 2026-09-14 | bull / bull archive | ZS | 09:45 | 175.7300 | WIN / WIN / WIN | +0.400% | +10.465% |
| 2026-09-14 | bull / bull archive | OKTA | 09:50 | 175.7600 | WIN / WIN / WIN | +0.400% | +7.943% |
| 2026-09-14 | bull / bull archive | PANW | 09:55 | 352.4700 | WIN / WIN / WIN | +0.400% | +7.413% |
| 2026-09-14 | bull / bull archive | CRWD | 10:00 | 227.1800 | WIN / WIN / WIN | +0.400% | +5.366% |
| 2026-09-14 | bull / bull archive | PATH | 10:00 | 14.3200 | WIN / LOSS / LOSS | +0.400% | +4.330% |
| 2026-09-14 | bull / bull archive | RBRK | 11:00 | 97.6350 | WIN / WIN / WIN | +0.400% | +2.940% |
| 2026-09-16 | bull / bull archive | AMD | 09:45 | 520.3800 | WIN / WIN / LOSS | +0.400% | +1.320% |
| 2026-09-16 | bull / bull archive | LITE | 09:45 | 884.5000 | WIN / WIN / WIN | +0.400% | +6.035% |
| 2026-09-16 | bull / bull archive | MRVL | 09:45 | 229.1400 | WIN / WIN / WIN | +0.400% | +2.021% |
| 2026-09-17 | bull / bull archive | UMAC | 09:45 | 23.6700 | WIN / WIN / WIN | +0.400% | +3.253% |
| 2026-09-17 | bull / bull archive | MRVL | 09:50 | 246.0000 | WIN / LOSS / LOSS | +0.400% | +0.768% |
| 2026-09-17 | bull / bull archive | TEM | 09:50 | 76.9500 | WIN / WIN / WIN | +0.400% | +5.627% |
| 2026-09-17 | bull / bull archive | DRAM | 09:55 | 58.0250 | LOSS / LOSS / LOSS | -0.462% | +0.112% |
| 2026-09-17 | bull / bull archive | MRNA | 09:55 | 156.0100 | WIN / WIN / WIN | +0.400% | +3.814% |
| 2026-09-17 | bull / bull archive | AMD | 10:00 | 539.4800 | WIN / WIN / WIN | +0.400% | +2.213% |
| 2026-09-17 | bull / bull archive | HL | 12:00 | 19.1300 | LOSS / LOSS / LOSS | -1.600% | +0.105% |
| 2026-09-18 | bull / bull archive | IBIT | 09:45 | 44.9150 | WIN / WIN / WIN | +0.400% | +2.705% |
| 2026-09-18 | bull / bull archive | MSTR | 09:45 | 141.5700 | WIN / WIN / WIN | +0.400% | +8.794% |
| 2026-09-18 | bull / bull archive | HOOD | 10:05 | 115.0000 | WIN / WIN / WIN | +0.400% | +4.843% |
| 2026-09-21 | bull / bull archive | MU | 09:45 | 1060.1800 | LOSS / LOSS / LOSS | -1.600% | +0.184% |
| 2026-09-21 | bull / bull archive | TQQQ | 09:45 | 75.7300 | WIN / WIN / WIN | +0.400% | +4.965% |
| 2026-09-21 | bull / bull archive | ARM | 10:00 | 308.7349 | WIN / WIN / WIN | +0.400% | +4.996% |
| 2026-09-21 | bull / bull archive | WBD | 10:05 | 30.4650 | WIN / WIN / WIN | +0.400% | +1.494% |
| 2026-09-22 | bull / bull archive | SHOP | 09:45 | 148.6500 | WIN / WIN / LOSS | +0.400% | +1.029% |
| 2026-09-22 | bull / bull archive | VKTX | 09:55 | 40.2200 | LOSS / LOSS / LOSS | -1.600% | +3.742% |
| 2026-09-25 | bull / bull archive | DELL | 09:45 | 559.0700 | LOSS / LOSS / LOSS | -1.600% | +2.635% |

### open_move_at_least_0.5

| Date | Side / source | Ticker | Entry ET | Entry price | W/L 0.5 / 1 / 2% | Net at 0.5% target | Max favorable after entry |
|---|---|---|---|---:|---|---:|---:|
| 2026-09-01 | bull / bull archive | USO | 12:25 | 139.5000 | WIN / WIN / WIN | +0.400% | +1.104% |
| 2026-09-03 | bull / bull archive | BULL | 09:45 | 9.6224 | WIN / WIN / WIN | +0.400% | +5.899% |
| 2026-09-03 | bull / bull archive | HOOD | 09:45 | 120.6700 | LOSS / LOSS / LOSS | -1.600% | +3.489% |
| 2026-09-03 | bull / bull archive | PATH | 09:45 | 18.7150 | WIN / LOSS / LOSS | +0.400% | +0.614% |
| 2026-09-03 | bull / bull archive | SBET | 09:45 | 8.5900 | WIN / WIN / WIN | +0.400% | +4.948% |
| 2026-09-03 | bull / bull archive | BMNR | 10:05 | 24.5450 | WIN / WIN / WIN | +0.400% | +8.372% |
| 2026-09-04 | bull / bull archive | SNDK | 10:05 | 1660.4400 | WIN / WIN / WIN | +0.400% | +4.792% |
| 2026-09-08 | bull / bull archive | FCX | 09:50 | 77.2800 | WIN / WIN / LOSS | +0.400% | +1.760% |
| 2026-09-08 | bull / bull archive | BE | 09:55 | 276.0000 | WIN / WIN / LOSS | +0.400% | +2.837% |
| 2026-09-09 | bull / bull archive | NET | 09:45 | 304.7600 | WIN / WIN / WIN | +0.400% | +4.197% |
| 2026-09-09 | bull / bull archive | FSLY | 10:00 | 23.8400 | WIN / WIN / WIN | +0.400% | +3.146% |
| 2026-09-11 | bull / bull archive | HPE | 09:45 | 59.7200 | WIN / WIN / WIN | +0.400% | +4.069% |
| 2026-09-11 | bull / bull archive | DELL | 09:50 | 548.0100 | WIN / WIN / WIN | +0.400% | +3.602% |
| 2026-09-11 | bull / bull archive | SBET | 09:55 | 9.0200 | WIN / WIN / WIN | +0.400% | +5.155% |
| 2026-09-14 | bull / bull archive | ZS | 09:45 | 175.7300 | WIN / WIN / WIN | +0.400% | +10.465% |
| 2026-09-14 | bull / bull archive | OKTA | 09:50 | 175.7600 | WIN / WIN / WIN | +0.400% | +7.943% |
| 2026-09-14 | bull / bull archive | PANW | 09:55 | 352.4700 | WIN / WIN / WIN | +0.400% | +7.413% |
| 2026-09-14 | bull / bull archive | CRWD | 10:00 | 227.1800 | WIN / WIN / WIN | +0.400% | +5.366% |
| 2026-09-14 | bull / bull archive | PATH | 10:00 | 14.3200 | WIN / LOSS / LOSS | +0.400% | +4.330% |
| 2026-09-14 | bull / bull archive | RBRK | 11:00 | 97.6350 | WIN / WIN / WIN | +0.400% | +2.940% |
| 2026-09-16 | bull / bull archive | AMD | 09:45 | 520.3800 | WIN / WIN / LOSS | +0.400% | +1.320% |
| 2026-09-16 | bull / bull archive | LITE | 09:45 | 884.5000 | WIN / WIN / WIN | +0.400% | +6.035% |
| 2026-09-16 | bull / bull archive | MRVL | 09:45 | 229.1400 | WIN / WIN / WIN | +0.400% | +2.021% |
| 2026-09-17 | bull / bull archive | UMAC | 09:45 | 23.6700 | WIN / WIN / WIN | +0.400% | +3.253% |
| 2026-09-17 | bull / bull archive | MRVL | 09:50 | 246.0000 | WIN / LOSS / LOSS | +0.400% | +0.768% |
| 2026-09-17 | bull / bull archive | TEM | 09:50 | 76.9500 | WIN / WIN / WIN | +0.400% | +5.627% |
| 2026-09-17 | bull / bull archive | DRAM | 09:55 | 58.0250 | LOSS / LOSS / LOSS | -0.462% | +0.112% |
| 2026-09-17 | bull / bull archive | MRNA | 09:55 | 156.0100 | WIN / WIN / WIN | +0.400% | +3.814% |
| 2026-09-17 | bull / bull archive | AMD | 10:00 | 539.4800 | WIN / WIN / WIN | +0.400% | +2.213% |
| 2026-09-17 | bull / bull archive | HL | 12:00 | 19.1300 | LOSS / LOSS / LOSS | -1.600% | +0.105% |
| 2026-09-18 | bull / bull archive | IBIT | 09:45 | 44.9150 | WIN / WIN / WIN | +0.400% | +2.705% |
| 2026-09-18 | bull / bull archive | MSTR | 09:45 | 141.5700 | WIN / WIN / WIN | +0.400% | +8.794% |
| 2026-09-18 | bull / bull archive | HOOD | 10:05 | 115.0000 | WIN / WIN / WIN | +0.400% | +4.843% |
| 2026-09-21 | bull / bull archive | MU | 09:45 | 1060.1800 | LOSS / LOSS / LOSS | -1.600% | +0.184% |
| 2026-09-21 | bull / bull archive | TQQQ | 09:45 | 75.7300 | WIN / WIN / WIN | +0.400% | +4.965% |
| 2026-09-21 | bull / bull archive | ARM | 10:00 | 308.7349 | WIN / WIN / WIN | +0.400% | +4.996% |
| 2026-09-21 | bull / bull archive | WBD | 10:05 | 30.4650 | WIN / WIN / WIN | +0.400% | +1.494% |
| 2026-09-22 | bull / bull archive | SHOP | 09:45 | 148.6500 | WIN / WIN / LOSS | +0.400% | +1.029% |
| 2026-09-22 | bull / bull archive | VKTX | 09:55 | 40.2200 | LOSS / LOSS / LOSS | -1.600% | +3.742% |
| 2026-09-25 | bull / bull archive | DELL | 09:45 | 559.0700 | LOSS / LOSS / LOSS | -1.600% | +2.635% |

## Coverage / exclusions

{}

Archive snapshots are capped and signal-change sampled; first observed qualification can lag the actual first signal. Current weekly membership and present-day grade formula are used. Price and history inputs were not exchange-reconciled. Incomplete earlier bear observation cannot establish bear win rate for the requested universe. New options or stop/target choices chosen after viewing these outcomes require independent forward validation.