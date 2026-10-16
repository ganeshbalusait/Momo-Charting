"""Read-only historical OI/retest experiment on the 41 prior bull GO+ candidates."""
import bisect,csv,gzip,json,math,sqlite3
from collections import Counter,defaultdict
from datetime import datetime,timedelta
from pathlib import Path
import momx_rules_backtest_sep2026 as b

OUT=b.OUT/'oi-retest'

def exit_trade(future,entry,target,stop):
    for x in future:
        if x['open']<=stop:return (x['open'],'stop_gap',x['time'],False)
        if x['open']>=target:return (target,'target_gap',x['time'],False)
        lo=x['low']<=stop;hi=x['high']>=target
        if lo or hi:return (stop if lo else target,'stop' if lo else 'target',x['time'],lo and hi)
    return future[-1]['close'],'close',future[-1]['time']+300,False

def main():
    b.selftest()
    assert exit_trade([dict(time=0,open=100,high=104,low=97,close=101)],100,102,98)[1:] == ('stop',0,True)
    candidates=[x for x in json.loads((b.OUT/'results.json').read_text())['trades'] if x['rule']=='GO + short RVOL + MACD']
    db=sqlite3.connect((b.ROOT/'database/trades.db').as_uri()+'?mode=ro',uri=True);db.row_factory=sqlite3.Row
    tapes={};ledger=[];audit=[];levels_used=[]
    for e in sorted(candidates,key=lambda e:(e['day'],e['signalAt'],e['symbol'])):
        sym=e['symbol'];day=e['day'];at=e['signalAt']
        if sym not in tapes:
            p=b.ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')
            doc=json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
            source=doc.get('fineStudyBars',[]);p=b.OUT/'recovered-prices'/(sym+'.json')
            if p.exists():source+=json.loads(p.read_text()).get('bars',[])
            bars=sorted({int(x['time']):x for x in source if b.valid(x)}.values(),key=lambda x:x['time'])
            tapes[sym]=b.Tape(bars)
        tape=tapes[sym];session=tape.days[day];start=session[0]['time'];session_date=b.dt(start).date()
        cutoff=(session_date-timedelta(days=7)).isoformat();expiry_end=(session_date+timedelta(days=31)).isoformat()
        # Require the previous dated snapshot: no after-close same-day OI leaks.
        rows=db.execute('SELECT snapshot_date,captured_at,expiry,strike,open_interest FROM option_chain_daily_snapshots WHERE symbol=? AND side=? AND snapshot_date>=? AND snapshot_date<? AND substr(expiry,1,10)>=? AND substr(expiry,1,10)<=?',(sym,'CALL',cutoff,day,day,expiry_end)).fetchall()
        eligible=[]
        for r in rows:
            try:ts=datetime.fromisoformat(r['captured_at']).timestamp()
            except ValueError:continue
            if ts<start and r['open_interest']>0:eligible.append(r)
        a=dict(date=day,ticker=sym,goSignalET=b.dt(at).isoformat(),baselineNet=e['net'],status='no prior OI',snapshotDate=None)
        if not eligible:audit.append(a);continue
        snapday=max(r['snapshot_date'] for r in eligible);eligible=[r for r in eligible if r['snapshot_date']==snapday]
        oi=defaultdict(float)
        for r in eligible:oi[float(r['strike'])]+=r['open_interest']
        # Fixed near-price window and strength threshold, not fitted to outcomes.
        near={k:v for k,v in oi.items() if .9*session[0]['open']<=k<=1.1*session[0]['open']}
        strong=sorted(k for k,v in near.items() if v>=.66*max(near.values(),default=math.inf))
        a.update(status='no break/retest confirmation',snapshotDate=snapday,strongLevels=','.join(map(str,strong)))
        for level in strong:levels_used.append(dict(date=day,ticker=sym,snapshotDate=snapday,strike=level,callOI=oi[level],latestCapture=max(r['captured_at'] for r in eligible)))
        prior=[d for d in tape.days if d<day]
        pdh=max(x['high'] for x in tape.days[max(prior)]) if prior else None
        pre=[x for x in tape.bars if b.dt(x['time']).strftime('%Y-%m-%d')==day and 240<=b.minute(x['time'])<570]
        pmh=max((x['high'] for x in pre),default=None)
        target_levels=sorted(set(strong+[v for v in (pdh,pmh,session[0]['high']) if v is not None]))
        broken={};traded=False
        for j,x in enumerate(session):
            closed=x['time']+300
            if closed>at+3600 or b.minute(closed)>930:break
            # Break must be a completed intraday cross, not simply opening above a strike.
            if j:
                for level in strong:
                    if session[j-1]['close']<=level<x['close']:broken[level]=j
            if closed<at or j==0:continue
            i=bisect.bisect_right(tape.times,x['time'])-1
            if i<14:continue
            trs=[max(y['high']-y['low'],abs(y['high']-p['close']),abs(y['low']-p['close'])) for p,y in zip(tape.bars[i-14:i],tape.bars[i-13:i+1])]
            atr=sum(trs)/len(trs);tol=max(.01,.1*atr)
            levels=[v for v,idx in broken.items() if idx<j and closed-(session[idx]['time']+300)<=3600 and x['low']<=v+tol and x['close']>v and x['close']>x['open']]
            if not levels or not tape.macd_up[i]:continue
            level=max(levels)
            # A close below the level between break and retest invalidates this break.
            if any(y['close']<level for y in session[broken[level]+1:j]):continue
            a['status']='retest found but no next level / insufficient room'
            if j+2>=len(session):continue
            entrybar=session[j+2];entry=entrybar['open'];stop=min(x['low'],level)-tol
            targets=[v for v in target_levels if v>entry];target=min(targets) if targets else None
            if entry<=level or stop>=entry or target is None or target-entry<entry-stop:continue
            future=session[j+2:];price,reason,exitAt,ambiguous=exit_trade(future,entry,target,stop)
            fixed=b.score(session,closed)
            # Strictly after confirmation, matching the original replay's fill convention.
            assert fixed['entryAt']==entrybar['time']
            net=100*(price/entry-1)-.1
            record=dict(date=day,ticker=sym,goSignalET=b.dt(at).isoformat(),snapshotDate=snapday,
                        brokenOILevel=level,callOI=oi[level],breakET=b.dt(session[broken[level]]['time']+300).isoformat(),
                        confirmationET=b.dt(closed).isoformat(),entryET=b.dt(entrybar['time']).isoformat(),entry=entry,
                        stop=stop,target=target,rewardRisk=(target-entry)/(entry-stop),exit=price,exitReason=reason,
                        exitET=b.dt(exitAt).isoformat(),ambiguous=ambiguous,net=net,result='WIN' if net>0 else 'LOSS',
                        retestFixedNet=fixed['net'],originalGONet=e['net'])
            ledger.append(record);a['status']='traded';traded=True;break
        audit.append(a)
    OUT.mkdir(exist_ok=True)
    def write(name,rows):
        if not rows:return
        fields=list(dict.fromkeys(k for r in rows for k in r))
        with (OUT/name).open('w',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
    write('trades.csv',ledger);write('candidate-audit.csv',audit);write('historical-levels.csv',levels_used)
    result=dict(candidates=len(candidates),coverage=dict(Counter(x['status'] for x in audit)),trades=ledger,audit=audit)
    (OUT/'results.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
