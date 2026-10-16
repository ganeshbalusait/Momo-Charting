import csv,json
from pathlib import Path
from datetime import datetime
P=Path(__file__).resolve().parent/'chart-arrows-2026-09-29'
d=json.loads((P/'results.json').read_text());meta=json.loads((P/'source-metadata.json').read_text())
t=d['trades'];primary=[x for x in t if x['color']=='Either color']
assert len({(x['rule'],x['color'],x['ticker']) for x in t})==len(t)
for x in t:
    assert datetime.fromisoformat(x['entryET'])>datetime.fromisoformat(x['signalET'])
    if x['status']!='OPEN':
        assert datetime.fromisoformat(x['exitET'])>datetime.fromisoformat(x['entryET'])
        assert abs(x['roiNetPct']-((x['exit']/x['entry']-1)*100-.1))<1e-9
        assert abs(x['pnlPer1000']-10*x['roiNetPct'])<1e-9
        assert x['maxGainPct']+1e-8>=max(0,x['roiGrossPct'])
for s in d['summary']:
    a=[x for x in t if x['rule']==s['rule'] and x['color']==s['color'] and x['status']!='OPEN']
    assert s['closed']==s['wins']+s['losses']
    assert abs(sum(x['pnlPer1000'] for x in a)-s['totalPnlPer1000Each'])<1e-8
with (P/'trades-either-color.csv').open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=list(primary[0]));w.writeheader();w.writerows(primary)
lines=['# September 29, 2026 — morning chart-arrow replay',
'', '**Partial-coverage historical simulation, not actual executions or an exhaustive chart backtest.** Entries are from 04:00 to before 10:00 New York time (EDT, UTC−4), with exits allowed later. Results use stock prices, not option premiums.',
'', '## Rules and fills',
'', '- Rules 1 and 2 use OR. Rules 3 and 4 require both labels to overlap while active; they need not print on the identical candle. Either-color AND allows mixed families. Separate same-color variants are below.',
'- Cyan = 9/20 EMA signal family; yellow = 4/8. These signal EMAs are distinct from the 5-minute EMA21 used for exits.',
'- First qualifying recorded signal per ticker, per rule, per day. No re-entry and no choosing the most profitable signal afterward. Different rules are separate hypothetical strategies and may overlap in tickers.',
'- Entry: next exact 5-minute open strictly after first-seen time, before 10:00. Drawn-on-chart time is retained separately because an arrow may only appear later. Disappeared/repainted arrows remain eligible if observed at the time.',
'- Exit: first completed 5-minute close below EMA21 after entry, filled at the following bar open. No profit target or percentage stop; no forced 10:00 exit. A candle already below EMA21 triggers an exit; a fresh cross from above is not required. No additional entry-above-EMA filter was added.',
'- EMA21 uses alpha 2/22, first-close seed and at least 105 preceding bars, including available extended-hours bars. Observations end at the latest completed retrieved bar, no later than 20:00 that day. Unexited trades would remain OPEN.',
'- Dollar P&L assumes an independent $1,000 fractional-share stock position per trade. Deduct 0.10 percentage point ($1 per $1,000) round-trip cost. This is an assumption, not measured execution cost; premarket costs may be greater. Average net ROI is per trade, not return on a continuously funded portfolio.',
'- Maximum gain is the highest stock high from entry through the holding period, excluding price action after the exit open. It is gross, hindsight-only, and is not a target or a realizable guaranteed fill.',
'', '## Summary by rule and color', '', '| Rule | Color | Closed | Win | Loss | Win rate | Net P&L ($1,000 each) | Average net ROI |', '|---|---|---:|---:|---:|---:|---:|---:|']
for s in d['summary']:
    lines.append(f"| {s['rule']} | {s['color']} | {s['closed']} | {s['wins']} | {s['losses']} | {s['winRatePct']:.2f}% | ${s['totalPnlPer1000Each']:+.2f} | {s['meanNetRoiPct']:+.3f}% |")
lines+=['','## Interpretation','', 'All four either-color variants lost money in this recorded sample after the modeled cost. Yellow C2H OR C4H was the strongest color-specific variant: 24 trades, 11 wins, 13 losses, +$49.54 total on $1,000 per trade, mean net ROI +0.206%. That one-day observation does not establish a repeatable edge. A higher peak gain is not the gain the EMA rule captured.',
'', '## Coverage and limitations', '',
f"- Source book contains {meta['monitoredSymbols']} monitored symbols. Filtering drawn and first-seen times to this morning leaves {meta['arrows']} eligible recorded arrows in {meta['morningSymbols']} tickers. Prices were retrieved for these tickers through the configured Schwab client; input copies are saved in prices/.",
'- The recorder suppresses compact C2H/C4H arrows before 09:30. Consequently rules 2 and 4 do NOT include all possible premarket chart signals. This is a material limitation, not evidence that no such signals existed.',
'- Candidate-based monitoring and periodic scans can delay first-seen times or miss transient arrows. The slow monitoring lane was added on September 29. Recorded timing measures scanner observation, not the earliest time an independently open chart could have shown an arrow.',
'- The daily signal book retains first-seen and disappearance times but not every disappear/reappear interval. AND overlap reconstruction can therefore be imperfect. No current end-of-day arrows were backdated as executable signals.',
'- Exact missing entry or exit bars are excluded, never filled using a later convenient price. The audit includes every excluded first candidate. Missing regular-session bars before exit invalidate a trade. Gaps before 09:30 are flagged in the ledger and may reflect no-trade periods or incomplete data.',
'- EMA results depend on the vendor tape and extended-hours settings; a chart using a different overnight feed can differ. Price history was retrieved later and may include vendor corrections.',
'- This is a one-day test with incomplete signal coverage, not evidence to select a profitable strategy. Option contract selection, spreads, IV and time decay were not modeled.',
'', '## Complete either-color trade lists', '', 'Times below are ET. Net P&L is per $1,000 position; peak gain is gross. Full precision and chart-drawn/seen/disappeared timestamps are in the CSV.']
for s in d['summary'][:4]:
    lines+=['',f"### {s['rule']}",'','| Ticker | Entry ET | Entry $ | Exit ET | Exit $ | Result | Net P&L $ | Net ROI | Max gain |','|---|---|---:|---|---:|---|---:|---:|---:|']
    for x in sorted((x for x in primary if x['rule']==s['rule']),key=lambda x:x['entryET']):
        lines.append(f"| {x['ticker']} | {x['entryET'][11:16]} | {x['entry']:.4f} | {x['exitET'][11:16]} | {x['exit']:.4f} | {x['status']} | {x['pnlPer1000']:+.2f} | {x['roiNetPct']:+.2f}% | {x['maxGainPct']:+.2f}% |")
lines+=['','## Excluded first candidates — either-color rules','','| Ticker | Rule | Reason |','|---|---|---|']
for x in d['audit']:
    if x['color']=='Either color' and x['coverage']!='priced':lines.append(f"| {x['ticker']} | {x['rule']} | {x['coverage']} |")
lines+=['','## Validation and reproduction','', '- Checked simultaneous versus non-overlapping AND signals and same-color filtering; completed-candle next-open exits; exclusion of the exit bar’s later high from maximum gain; unique ticker/rule/color rows; entry/exit chronology; return/P&L arithmetic; summary reconciliation.', '- Research scripts: artifacts/backtest_morning_arrows.py and artifacts/write_morning_arrow_report.py. Inputs: signal-source.json, source-metadata.json, and prices/. Outputs: results.json, summary.csv, trades.csv (all color variants), trades-either-color.csv, candidate-audit.csv.', '- No trading orders or app code changes. Production build and browser smoke tests do not apply to these standalone research outputs.']
(P/'REPORT.md').write_text('\n'.join(lines),encoding='utf-8')
print('Validated',len(t),'trade rows;',len(primary),'either-color rows. Report written.')
