"""Read-only archive audit; writes only this report's outputs, never app records."""
import json, gzip, sys, statistics
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter, defaultdict

ROOT=Path(__file__).resolve().parents[1]/'AgenticAI-Trading 2'
sys.path.insert(0,str(ROOT))
from momx.grade import safe_grade_row
ET=ZoneInfo('America/New_York')
OUT=Path(__file__).resolve().parent
weekly=json.loads((ROOT/'artifacts/momx_optionable.json').read_text())['symbols']
weekly={s for s,v in weekly.items() if v.get('weeklies') is True}
coverage=[]; candidates={}; offsets=Counter(); rowfields=Counter()
for p in sorted((ROOT/'artifacts/momx_history/Watchlist').glob('2026-09-*.json')):
    if not '2026-09-01'<=p.stem<='2026-09-25':continue
    data=json.loads(p.read_text()); rows=data.get('rows',{}); ns=0; regular=0; capped=0
    for sym,rec in rows.items():
        capped+=bool(rec.get('truncated')); snaps=rec.get('snapshots',[]);ns+=len(snaps)
        for snap in sorted(snaps,key=lambda x:x['at']):
            dt=datetime.fromisoformat(snap['at']);at=dt.astimezone(ET); offsets[str(dt.utcoffset())]+=1
            if not 570<=at.hour*60+at.minute<=930:continue
            regular+=1
            if sym not in weekly:continue
            row=snap.get('row',{});g=safe_grade_row(row,dt) or {};letter=g.get('letter')
            for f in ['m5','strategy','momoxAplus']:rowfields[f]+=bool(row.get(f))
            if letter not in ('A','A+'):continue
            m=row.get('m5') or {}; ad=(row.get('adx') or {}).get('30m') or {}
            labels=[letter]
            gg=m.get('gapGo') or {}; gt=gg.get('goAt')
            if gt and datetime.fromtimestamp(gt,ET).date()==at.date() and (ad.get('plus') or 0)>(ad.get('minus') or 0):labels.append('GO observed')
            sk=row.get('skittles') or {}
            if at.hour*60+at.minute<600 and m.get('state')=='extended' and all((sk.get(tf) or {}).get('bg') in {'cyan','green','lime','dark_green'} for tf in ['2h','4h']) and (ad.get('plus') or 0)>(ad.get('minus') or 0):labels.append('OPT observed before 10')
            for label in labels:
                key=(p.stem,sym,label)
                if key not in candidates and row.get('last'):
                    candidates[key]={'day':p.stem,'symbol':sym,'rule':label,'at':at.isoformat(),'timestamp':at.timestamp(),'price':row['last']}
    coverage.append({'day':p.stem,'symbols':len(rows),'snapshots':ns,'regular_0930_1530':regular,'capped_symbols':capped})

events=[]; missing=Counter(); cache={}
for e in candidates.values():
    sym=e['symbol']
    if sym not in cache:
        p=ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')
        try:
            d=json.loads(gzip.decompress(p.read_bytes())); byday=defaultdict(list)
            for b in {b['time']:b for b in d.get('bars',[])}.values():
                at=datetime.fromtimestamp(b['time'],ET)
                if 570<=at.hour*60+at.minute<960:byday[at.strftime('%Y-%m-%d')].append(b)
            cache[sym]={k:sorted(v,key=lambda b:b['time']) for k,v in byday.items()}
        except (OSError,ValueError):cache[sym]={}
    bars=cache[sym].get(e['day'],[])
    future=[b for b in bars if b['time']>e['timestamp']]
    if not bars or datetime.fromtimestamp(bars[-1]['time'],ET).strftime('%H:%M')!='15:59' or not future:
        missing[e['rule']]+=1;continue
    # Signal quote is reference only. Use the next minute's open as a reproducible entry.
    entry=float(future[0]['open']); close=float(bars[-1]['close'])
    e=dict(e,entry=entry,entryAt=datetime.fromtimestamp(future[0]['time'],ET).isoformat(),toClose=100*(close/entry-1))
    for target in [1,2,4]:
        result=None
        for b in future:
            stop=b['low']<=entry*.985; take=b['high']>=entry*(1+target/100)
            if stop or take:
                result=-1.5 if stop else target;break
        e['target'+str(target)]=e['toClose'] if result is None else result
    events.append(e)
def stats(es):
    out={'n':len(es)}
    for field in ['toClose','target1','target2','target4']:
        vals=[e[field] for e in es]
        out[field]={'positivePct':round(sum(v>0 for v in vals)*100/len(vals),2),'meanPct':round(statistics.mean(vals),3),'medianPct':round(statistics.median(vals),3)} if vals else None
    return out
summary={r:stats([e for e in events if e['rule']==r]) for r in sorted({e['rule'] for e in candidates.values()})}
byday={d:stats([e for e in events if e['rule']=='A+' and e['day']==d]) for d in sorted({e['day'] for e in events})}
record_audit=Counter(); bad_examples=[]
for p in sorted((ROOT/'artifacts/momx_grade_events/Watchlist').glob('*.json')):
    if not '2026-09-01'<=p.stem<='2026-09-25':continue
    for e in json.loads(p.read_text()).get('events',[]):
        t=datetime.fromisoformat(e['at']).timestamp();b=e.get('lastCompleted5m') or {}
        record_audit['events']+=1
        if b.get('time') and b['time']+300>t:
            record_audit['completed_bar_not_complete_at_event']+=1
            if len(bad_examples)<4:bad_examples.append({'symbol':e['symbol'],'eventAt':e['at'],'barStartET':datetime.fromtimestamp(b['time'],ET).isoformat()})
out={'scope':'Watchlist, current Sep26 weekly membership; current grade formula replayed on historical rows','coverage':coverage,'offsets':dict(offsets),'rowfields':dict(rowfields),'candidateCounts':dict(Counter(e['rule'] for e in candidates.values())),'missingCloseCounts':dict(missing),'results':summary,'aPlusByDay':byday,'eventAudit':dict(record_audit),'examples':bad_examples,'events':events}
(OUT/'momx-sep1-25-audit.json').write_text(json.dumps(out,indent=2))
print(json.dumps({k:v for k,v in out.items() if k!='events'},indent=2))
