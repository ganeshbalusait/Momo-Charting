import json
from pathlib import Path

OUT=Path(__file__).resolve().parent/'momx-sep1-25-rules/bull-bear-open-half-percent'
d=json.loads((OUT/'results.json').read_text())
lines=['# GO + short RVOL + MACD: September 1–25, 2026','',
       'Scope: current weekly-options Watchlist universe, 18 regular trading sessions. All times Eastern. Bull archive covers the full period; actual bear Watchlist archive exists only September 25. Earlier bear rows below are an INCOMPLETE proxy reconstructed from snapshots selected and retained by the bull scanner, not a full bear backtest. Missing bear dates must not be counted as zero-signal days.','',
       'Entry: next five-minute opening strictly after the first qualifying snapshot per ticker/day/direction/variant. GO requires a 2% gap in the trade direction and an opening-range/VWAP break by 10:30; A/A+, cyan (bull) or magenta (bear) RVOL on any 5m/15m/30m, completed 5m MACD positive/rising or negative/falling, completed 30m DI directional control. This is the existing GO rule, not a new 0.5% gap rule.','',
       'The original variant preserves the rule. The open-move variant additionally requires the last completed price at signal to be at least +0.5% from the 09:30 open for bull, or -0.5% for bear; it can first qualify later. Both variants are reported because the requested 0.5% meaning was ambiguous.','',
       'Each target is simulated separately with a 1.5% adverse stop and market-close fallback. Entry/stop gaps respected; when target and stop both occur in one five-minute bar, stop wins. WIN means positive return after 0.10 percentage-point assumed round-trip cost. Bear returns model short STOCK positions without borrow availability/fees, not put options. No portfolio capital constraint.','',
       'Best move after entry is the favorable high (bull) or low (bear) anywhere before the close, even if a stop had already occurred. It is hindsight opportunity, not realized profit or an achievable exit. Day high/low may have occurred BEFORE entry. Only complete 78-bar regular sessions are scored. Original bull +2% outcomes reproduce the earlier 41-trade baseline exactly.','',
       '| Variant | Direction / source | Target | Trades | Wins | Losses | Win rate | Average net |',
       '|---|---|---:|---:|---:|---:|---:|---:|']
for s in d['summary']:
    rate=f"{s['wins']/s['n']*100:.1f}%" if s['n'] else 'N/A'
    avg=f"{s['meanNet']:+.3f}%" if s['n'] else 'N/A'
    lines.append(f"| {s['variant']} | {s['direction']} / {s['source']} | {s['target']}% | {s['n']} | {s['wins']} | {s['losses']} | {rate} | {avg} |")
lines+=['','## Stock list by date','',
        'W/L columns correspond to target +0.5%, +1%, +2%, each with the 1.5% stop. Percentages in Max favorable are measured from entry in the trade direction. The full ledger also supplies session open, day high/low, price at the favorable extreme, maximum adverse move, and the move from the open at signal.']
for variant in ('original','open_move_at_least_0.5'):
    lines+=['','### '+variant,'',
            '| Date | Side / source | Ticker | Entry ET | Entry price | W/L 0.5 / 1 / 2% | Net at 0.5% target | Max favorable after entry |',
            '|---|---|---|---|---:|---|---:|---:|']
    for e in sorted((x for x in d['trades'] if x['variant']==variant),key=lambda x:(x['date'],x['direction'],x['entryET'])):
        lines.append(f"| {e['date']} | {e['direction']} / {e['source']} | {e['ticker']} | {e['entryET'][11:16]} | {e['entry']:.4f} | {e['result0.5']} / {e['result1']} / {e['result2']} | {e['net0.5']:+.3f}% | {e['maxFavorablePct']:+.3f}% |")
lines+=['','## Coverage / exclusions','',json.dumps(d['missing']), '',
        'Archive snapshots are capped and signal-change sampled; first observed qualification can lag the actual first signal. Current weekly membership and present-day grade formula are used. Price and history inputs were not exchange-reconciled. Incomplete earlier bear observation cannot establish bear win rate for the requested universe. New options or stop/target choices chosen after viewing these outcomes require independent forward validation.']
(OUT/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
print('Wrote',OUT/'REPORT.md')
