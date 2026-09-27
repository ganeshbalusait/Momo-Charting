"""Research-only EMA substitution; preserve original backtest artifacts."""
import bisect, csv, gzip, json, math
from collections import Counter
import momx_rules_backtest_sep2026 as base

OUT = base.OUT / 'ema-comparison'
RULES = ['GO', 'GO + short RVOL', 'GO + short RVOL + MACD',
         'GO + short RVOL + EMA 4/8', 'GO + short RVOL + EMA 9/21']

def main():
    base.selftest()
    census = json.loads((base.ROOT/'artifacts/momx_optionable.json').read_text())
    weekly = {s for s,v in census['symbols'].items() if v.get('weeklies') is True}
    raw, coverage = base.load_history(weekly)
    trades = []; missing = Counter(); identified = Counter(); checks = 0
    for count, (sym, snaps) in enumerate(sorted(raw.items()), 1):
        p = base.ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')
        data = json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
        source = data.get('fineStudyBars', [])
        p = base.OUT/'recovered-prices'/(sym+'.json')
        if p.exists(): source += json.loads(p.read_text()).get('bars', [])
        bars = sorted({int(b['time']):b for b in source if base.valid(b)}.values(), key=lambda b:b['time'])
        if len(bars)<100: continue
        tape = base.Tape(bars)
        closes = [b['close'] for b in bars]
        averages = {n:base.ema(closes,n) for n in (4,8,9,21)}
        seen = set()
        for r in sorted(snaps,key=lambda r:r['at']):
            at=r['at']; day=r['day']
            if base.minute(at)<570 or r['letter'] not in ('A','A+'): continue
            state=tape.state(at)
            if state is None or not state['buyers'] or tape.go.get(day,math.inf)>at: continue
            i=bisect.bisect_right(tape.times,at-300)-1
            assert tape.times[i]+300<=at
            flags=[True, r['shortRvol'], r['shortRvol'] and state['macd'],
                   r['shortRvol'] and averages[4][i]>averages[8][i],
                   r['shortRvol'] and averages[9][i]>averages[21][i]]
            for rule, flag in zip(RULES,flags):
                key=(day,rule)
                if not flag or key in seen: continue
                seen.add(key); identified[rule]+=1
                if not tape.complete.get(day): missing[rule]+=1; continue
                result=base.score(tape.days[day],at)
                if result is None: missing[rule]+=1; continue
                # Recompute EMA from the historical prefix to verify no future input.
                for n in (4,8,9,21):
                    assert base.ema(closes[:i+1],n)[-1]==averages[n][i]
                checks+=1
                trades.append(dict(result,rule=rule,day=day,symbol=sym,signalAt=at,
                                   ema4=averages[4][i],ema8=averages[8][i],
                                   ema9=averages[9][i],ema21=averages[21][i]))
        if count%40==0: print('Replayed',count,'symbols',flush=True)
    # Existing GO and MACD outputs must match exactly, including entry and P&L.
    old=json.loads((base.OUT/'results.json').read_text())['trades']
    def key(e): return tuple(e[k] for k in ('rule','day','symbol','signalAt','entryAt','entry','net'))
    for rule in (RULES[0],RULES[2]):
        assert sorted(key(e) for e in old if e['rule']==rule)==sorted(key(e) for e in trades if e['rule']==rule), rule+' baseline changed'
    summaries={}
    for rule in RULES:
        es=[e for e in trades if e['rule']==rule]
        def stats(entries):
            return dict(base.stats(entries), wins=sum(e['net']>0 for e in entries),losses=sum(e['net']<0 for e in entries))
        summaries[rule]=dict(stats(es),identified=identified[rule],missing=missing[rule],
                             lastWeek=stats([e for e in es if e['day']>='2026-09-21']))
    OUT.mkdir(exist_ok=True)
    (OUT/'results.json').write_text(json.dumps(dict(results=summaries,trades=trades,coverage=coverage,prefixChecks=checks),indent=2))
    with (OUT/'trades.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(trades[0]));writer.writeheader()
        for e in trades:
            e=dict(e)
            for k in ('signalAt','entryAt','exitAt'):e[k]=base.dt(e[k]).isoformat()
            writer.writerow(e)
    lines=['# GO: MACD versus EMA confirmation — September 1–25, 2026','',
           'Same archived weekly-options Watchlist universe, GO reconstruction and cyan RVOL background on any of 5m/15m/30m. EMA means fast EMA strictly above slow EMA on the last completed five-minute bar, not a fresh crossover. EMA uses the full available tape, including extended hours, exactly as the MACD input does. Each rule takes its own first qualifying snapshot per ticker/day. Entry next five-minute open; +2% stock target, -1.5% stop, otherwise close; stop-first for ambiguous bars; 0.10 percentage-point roundtrip cost.','',
           '| Rule | Trades | Wins | Losses | Win rate | Mean net | Final-week N | Final-week win rate | Final-week mean net |',
           '|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for rule,s in summaries.items():
        w=s['lastWeek']
        lines.append(f"| {rule} | {s['n']} | {s['wins']} | {s['losses']} | {s['positiveNetPct']}% | {s['meanNet']:+.3f}% | {w['n']} | {w['positiveNetPct']}% | {w['meanNet']:+.3f}% |")
    lines+=['','Validation: eight execution/data-integrity assertions; exact full-ledger parity for the original GO and MACD baselines; '+str(checks)+' historical-prefix EMA checks. No production application code changed.','',
            'Exploratory comparison on the same period used to develop the rules; overlapping trades and different entry times prevent interpreting small percentage differences as established superiority. All original report coverage and reconstruction limitations apply. These are stock returns; options and account returns were not simulated. No app filter was added.']
    (OUT/'REPORT.md').write_text('\n'.join(lines))
    print(json.dumps(summaries,indent=2),flush=True)

if __name__=='__main__':main()
