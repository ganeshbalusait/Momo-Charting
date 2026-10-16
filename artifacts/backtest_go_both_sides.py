"""Research: directional GO/RVOL/MACD and open-to-extreme diagnostics."""
import bisect, csv, gzip, json, math
from collections import defaultdict, Counter
from datetime import datetime
import momx_rules_backtest_sep2026 as b
from momx.indicators import macd, adx_lines

OUT=b.OUT/'bull-bear-open-half-percent'

def outcome(bars,at,bear,target):
    entry_at=(int(at)//300+1)*300
    idx=next((i for i,x in enumerate(bars) if x['time']==entry_at),None)
    if idx is None:return None
    future=bars[idx:];entry=future[0]['open'];sign=-1 if bear else 1
    take=entry*(1+sign*target/100);stop=entry*(1-sign*.015)
    for x in future:
        stopgap=x['open']>=stop if bear else x['open']<=stop
        targetgap=x['open']<=take if bear else x['open']>=take
        hitstop=x['high']>=stop if bear else x['low']<=stop
        hittarget=x['low']<=take if bear else x['high']>=take
        if stopgap:return dict(gross=sign*100*(x['open']/entry-1),exit='stop_gap',exitAt=x['time'],ambiguous=False)
        if targetgap:return dict(gross=target,exit='target_gap',exitAt=x['time'],ambiguous=False)
        if hitstop or hittarget:return dict(gross=-1.5 if hitstop else target,exit='stop' if hitstop else 'target',exitAt=x['time'],ambiguous=hitstop and hittarget)
    return dict(gross=sign*100*(future[-1]['close']/entry-1),exit='close',exitAt=future[-1]['time']+300,ambiguous=False)

def verify():
    for bear in (False,True):
        x=[dict(time=300,open=100,high=102,low=98,close=100)]
        assert outcome(x,1,bear,.5)['gross']==-1.5
        assert outcome(x,1,bear,.5)['ambiguous']
    x=[dict(time=300,open=100,high=100.1,low=99,close=99.2)]
    assert outcome(x,1,True,.5)['gross']==.5
    assert outcome(x,300,True,.5) is None
    b.selftest()

def main():
    verify()
    weekly={s for s,v in json.loads((b.ROOT/'artifacts/momx_optionable.json').read_text())['symbols'].items() if v.get('weeklies') is True}
    raw=defaultdict(list);coverage=[]
    for day in b.DAYS:
        for direction in ('bull','bear'):
            hist=b.ROOT/'artifacts/momx_history'
            actual=hist/('bear/Watchlist' if direction=='bear' else 'Watchlist')/(day+'.json')
            proxy=direction=='bear' and not actual.exists()
            path=hist/'Watchlist'/(day+'.json') if proxy else actual
            if not path.exists():continue
            doc=json.loads(path.read_text());count=0
            for sym,rec in doc.get('rows',{}).items():
                if sym not in weekly:continue
                for snap in rec.get('snapshots',[]):
                    at=datetime.fromisoformat(snap['at']).astimezone(b.ET);t=at.timestamp()
                    if at.strftime('%Y-%m-%d')!=day or not 570<=b.minute(t)<=930:continue
                    row=snap.get('row',{})
                    cells=[c for g in ('rvol','skittles','sqz') for c in (row.get(g) or {}).values() if isinstance(c,dict)]
                    if any(isinstance(c.get('barAt'),(float,int)) and c['barAt']>t+1 for c in cells):continue
                    grade=b.grade_row(row,at,direction)
                    if not grade or grade['letter'] not in ('A','A+'):continue
                    color='magenta' if direction=='bear' else 'cyan'
                    rv=row.get('rvol') or {}
                    if not any((rv.get(tf) or {}).get('bg')==color for tf in ('5m','15m','30m')):continue
                    raw[sym].append(dict(day=day,at=t,direction=direction,source='bull-archive proxy' if proxy else direction+' archive',grade=grade['letter']))
                    count+=1
            coverage.append(dict(day=day,direction=direction,source='bull-archive proxy' if proxy else direction+' archive',eligibleSnapshots=count))
    trades=[];missing=Counter()
    for n,(sym,snaps) in enumerate(sorted(raw.items()),1):
        p=b.ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')
        doc=json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
        source=doc.get('fineStudyBars',[])
        p=b.OUT/'recovered-prices'/(sym+'.json')
        if p.exists():source+=json.loads(p.read_text()).get('bars',[])
        bars=sorted({int(x['time']):x for x in source if b.valid(x)}.values(),key=lambda x:x['time'])
        if len(bars)<100:missing['noTapeSymbols']+=1;continue
        tape=b.Tape(bars);line,signal=macd([x['close'] for x in bars],12,26,9)
        down=[i>0 and line[i]<signal[i] and line[i]-signal[i]<line[i-1]-signal[i-1] for i in range(len(bars))]
        buckets={}
        for x in bars:
            t=x['time']//1800*1800
            if t not in buckets:buckets[t]=dict(x)
            else:
                q=buckets[t];q['high']=max(q['high'],x['high']);q['low']=min(q['low'],x['low']);q['close']=x['close']
        t30=[t+1800 for t in buckets];candles=list(buckets.values())
        di=adx_lines([x['high'] for x in candles],[x['low'] for x in candles],[x['close'] for x in candles])
        sellers=[p is not None and m is not None and m>p for p,m in zip(di['plus'],di['minus'])]
        bear_go={}
        for day in sorted(tape.days):
            session=tape.days[day];prior=[d for d in tape.days if d<day]
            if not prior or b.minute(session[0]['time'])!=570:continue
            prev=tape.days[max(prior)][-1]
            if b.minute(prev['time'])!=955:continue
            pi=bisect.bisect_left(tape.times,session[0]['time'])-1
            op=bars[pi]['close'] if pi>=0 and b.dt(bars[pi]['time']).strftime('%Y-%m-%d')==day else session[0]['open']
            if op/prev['close']-1>-.02:continue
            pv=vol=0
            for j,x in enumerate(session):
                if b.minute(x['time'])>625:break
                pv+=(x['high']+x['low']+x['close'])/3*x['volume'];vol+=x['volume']
                if j and vol>0 and x['close']<min(op,session[0]['low'],pv/vol):bear_go[day]=x['time']+300;break
        seen=set()
        for r in sorted(snaps,key=lambda x:x['at']):
            at=r['at'];day=r['day'];bear=r['direction']=='bear';state=tape.state(at)
            if state is None:continue
            i=bisect.bisect_right(tape.times,at-300)-1;k=bisect.bisect_right(t30,at)-1
            qualifies=(k>=0 and sellers[k] and down[i] and bear_go.get(day,math.inf)<=at) if bear else (state['buyers'] and state['macd'] and tape.go.get(day,math.inf)<=at)
            if not qualifies:continue
            regular=tape.days[day];sign=-1 if bear else 1
            open_move=sign*100*(bars[i]['close']/regular[0]['open']-1)
            for variant in ('original','open_move_at_least_0.5'):
                if variant!='original' and open_move<.5:continue
                key=(day,r['direction'],variant)
                if key in seen:continue
                seen.add(key)
                if not tape.complete.get(day):missing[r['direction']+' incompleteSession']+=1;continue
                result=outcome(regular,at,bear,.5)
                if result is None:missing['missingEntry']+=1;continue
                entry_at=(int(at)//300+1)*300;future=[x for x in regular if x['time']>=entry_at];entry=future[0]['open']
                extreme=min(future,key=lambda x:x['low']) if bear else max(future,key=lambda x:x['high'])
                extreme_price=extreme['low'] if bear else extreme['high']
                adverse=max(x['high'] for x in future) if bear else min(x['low'] for x in future)
                e=dict(date=day,direction=r['direction'],variant=variant,source=r['source'],ticker=sym,grade=r['grade'],signalET=b.dt(at).isoformat(),entryET=b.dt(entry_at).isoformat(),entry=entry,sessionOpen=regular[0]['open'],directionalMoveFromOpenAtSignal=open_move,dayHigh=max(x['high'] for x in regular),dayLow=min(x['low'] for x in regular),bestAfterEntryPrice=extreme_price,bestAfterEntryBarET=b.dt(extreme['time']).isoformat(),maxFavorablePct=sign*100*(extreme_price/entry-1),maxAdversePct=sign*100*(adverse/entry-1))
                for target in (.5,1,2):
                    result=outcome(regular,at,bear,target);label=str(target)
                    e['net'+label]=result['gross']-.10;e['result'+label]='WIN' if result['gross']-.10>0 else 'LOSS' if result['gross']-.10<0 else 'FLAT'
                    e['exit'+label]=result['exit'];e['hit'+label]=result['exit'].startswith('target')
                trades.append(e)
        if n%20==0:print('Replayed',n,'/',len(raw),flush=True)
    # Original bull entries must reproduce the earlier baseline.
    old=[x for x in json.loads((b.OUT/'results.json').read_text())['trades'] if x['rule']=='GO + short RVOL + MACD']
    new=[x for x in trades if x['direction']=='bull' and x['variant']=='original']
    oldkeys={(x['day'],x['symbol'],b.dt(x['signalAt']).isoformat()):x for x in old}
    newkeys={(x['date'],x['ticker'],x['signalET']):x for x in new}
    assert oldkeys.keys()==newkeys.keys(), 'Bull source coverage changed'
    for key,x in newkeys.items():assert abs(x['net2']-oldkeys[key]['net'])<1e-8
    OUT.mkdir(exist_ok=True)
    with (OUT/'trades.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(trades[0]));writer.writeheader();writer.writerows(sorted(trades,key=lambda x:(x['date'],x['direction'],x['variant'],x['signalET'])))
    summaries=[]
    for variant in ('original','open_move_at_least_0.5'):
        for direction,source in [('bull','bull archive'),('bear','bear archive'),('bear','bull-archive proxy')]:
            es=[e for e in trades if e['variant']==variant and e['direction']==direction and e['source']==source]
            for target in ('0.5','1','2'):
                summaries.append(dict(variant=variant,direction=direction,source=source,target=target,n=len(es),wins=sum(e['result'+target]=='WIN' for e in es),losses=sum(e['result'+target]=='LOSS' for e in es),targetHits=sum(e['hit'+target] for e in es),meanNet=sum(e['net'+target] for e in es)/len(es) if es else None))
    (OUT/'results.json').write_text(json.dumps(dict(summary=summaries,coverage=coverage,missing=dict(missing),trades=trades),indent=2))
    print(json.dumps(dict(summary=summaries,missing=dict(missing)),indent=2),flush=True)

if __name__=='__main__':main()
