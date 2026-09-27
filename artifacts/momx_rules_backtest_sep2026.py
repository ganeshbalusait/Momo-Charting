"""Independent historical rule comparison. Research only; no service writes/network."""
from __future__ import annotations
import bisect, csv, gzip, hashlib, json, math, random, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]/'AgenticAI-Trading 2'
OUT=Path(__file__).resolve().parent/'momx-sep1-25-rules'
sys.path.insert(0,str(ROOT))
from momx.grade import grade_row
from momx.indicators import ema, adx_lines, macd
ET=ZoneInfo('America/New_York')
DAYS=[f'2026-09-{d:02}' for d in range(1,26) if date(2026,9,d).weekday()<5 and d!=7]
BULL={'cyan','green','lime'}
RULES=['A+','A or A+','GO','Early OPT','Best GO or early OPT','V2','V3','Daily 2 first two','MomoX A+ without OI','A/A+ + short RVOL + MACD','GO + short RVOL + MACD']

def dt(t):return datetime.fromtimestamp(t,ET)
def minute(t):
    d=dt(t);return d.hour*60+d.minute
def valid(b):
    try:
        o,h,l,c,v=[float(b[k]) for k in ['open','high','low','close','volume']]
        return all(math.isfinite(x) for x in [o,h,l,c,v]) and 0<l<=min(o,c)<=max(o,c)<=h and v>=0
    except (ValueError,TypeError,KeyError):return False

def score(bars,at,target=2,stop=1.5):
    """Next five-minute OPEN, exact next boundary; stop-first ambiguity, gap fills."""
    entry_t=(int(at)//300+1)*300
    lookup={int(b['time']):i for i,b in enumerate(bars)}
    if entry_t not in lookup:return None
    i=lookup[entry_t];future=bars[i:];entry=future[0]['open']
    hi=entry*(1+target/100);lo=entry*(1-stop/100)
    result=None;ambiguous=False
    for b in future:
        if b['open']<=lo:result=(100*(b['open']/entry-1),'stop_gap',b['time']);break
        if b['open']>=hi:result=(target,'target_gap',b['time']);break
        hitstop=b['low']<=lo;hittarget=b['high']>=hi
        if hitstop or hittarget:
            ambiguous=hitstop and hittarget
            result=(-stop if hitstop else target,'stop' if hitstop else 'target',b['time']);break
    if result is None:result=(100*(future[-1]['close']/entry-1),'close',future[-1]['time']+300)
    return {'entryAt':entry_t,'entry':entry,'gross':result[0],'net':result[0]-.10,'exit':result[1],'exitAt':result[2],'ambiguous':ambiguous}

def selftest():
    def b(t,o,h,l,c):return dict(time=t,open=o,high=h,low=l,close=c,volume=1)
    assert score([b(300,100,104,97,101)],1)['gross']==-1.5
    assert score([b(300,100,104,99,101)],1)['gross']==2
    assert score([b(300,100,101,99,100),b(600,97,99,96,98)],1)['gross']<-2.99
    assert score([b(300,100,101,99,100),b(600,104,105,103,104)],1)['gross']==2
    assert score([b(600,100,101,99,100)],1) is None
    assert score([b(300,100,101,99,100),b(600,110,111,109,110)],300)['entry']==110
    assert score([b(300,100,101,99,100.5)],1)['exit']=='close'
    assert valid(b(300,100,99,98,100)) is False

class Tape:
    def __init__(self,bars):
        self.bars=bars;self.times=[int(b['time']) for b in bars]; self.days=defaultdict(list)
        c=[b['close'] for b in bars];self.e20=ema(c,20);ml,ms=macd(c,12,26,9)
        self.macd_up=[i>0 and ml[i]>ms[i] and ml[i]-ms[i]>ml[i-1]-ms[i-1] for i in range(len(c))]
        tr=[bars[0]['high']-bars[0]['low']]+[max(b['high']-b['low'],abs(b['high']-p['close']),abs(b['low']-p['close'])) for p,b in zip(bars,bars[1:])]
        self.extended=[];self.chart=[];break_level=None;lastday=None;break_i=None;held=True
        for i,b in enumerate(bars):
            day=dt(b['time']).strftime('%Y-%m-%d')
            if day!=lastday:break_level=break_i=None;lastday=day;held=True
            if i>=6 and b['close']>max(x['high'] for x in bars[i-6:i]):break_level=max(x['high'] for x in bars[i-6:i]);break_i=i;held=True
            elif break_level is not None:held=held and b['close']>=break_level
            if break_i==i:state='confirmed'
            elif break_level is not None and held:state='holding'
            elif break_level is not None:state='failed'
            else:state='below'
            self.chart.append(state)
            atr=sum(tr[max(0,i-13):i+1])/14 if i>=14 else math.inf
            self.extended.append(state!='failed' and b['close']>self.e20[i]+2*atr)
            if 570<=minute(b['time'])<960:self.days[day].append(b)
        buckets={}
        for b in bars:
            t=b['time']//1800*1800
            if t not in buckets:buckets[t]=dict(b,time=t,count=1)
            else:
                q=buckets[t];q['high']=max(q['high'],b['high']);q['low']=min(q['low'],b['low']);q['close']=b['close'];q['volume']+=b['volume'];q['count']+=1
        thirty=list(buckets.values());self.t30=[b['time']+1800 for b in thirty]
        di=adx_lines([b['high'] for b in thirty],[b['low'] for b in thirty],[b['close'] for b in thirty])
        self.buyers=[p is not None and m is not None and p>m for p,m in zip(di['plus'],di['minus'])]
        self.go={};self.complete={}
        ordered=sorted(self.days)
        for day in ordered:
            session=self.days[day];start=int(datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ET).timestamp())
            self.complete[day]=len(session)==78 and [b['time'] for b in session]==list(range(start,start+23400,300))
            prior=[d for d in ordered if d<day]
            if not prior or not session or session[0]['time']!=start:continue
            prev=self.days[prior[-1]][-1]
            if minute(prev['time'])!=955:continue
            pi=bisect.bisect_left(self.times,start)-1
            op=bars[pi]['close'] if pi>=0 and dt(bars[pi]['time']).strftime('%Y-%m-%d')==day else session[0]['open']
            gap=op/prev['close']-1
            if gap<.02:continue
            pv=vol=0
            for j,b in enumerate(session):
                if minute(b['time'])>625:break
                pv+=(b['high']+b['low']+b['close'])/3*b['volume'];vol+=b['volume']
                if j and vol>0 and b['close']>max(op,session[0]['high'],pv/vol):
                    self.go[day]=b['time']+300;break
    def state(self,at):
        i=bisect.bisect_right(self.times,at-300)-1
        k=bisect.bisect_right(self.t30,at)-1
        if i<50 or at-(self.times[i]+300)>300:return None
        return {'extended':self.extended[i],'macd':self.macd_up[i],'buyers':k>=0 and self.buyers[k],'barAt':self.times[i]}

def load_history(weekly):
    bysym=defaultdict(list);coverage=[]
    for day in DAYS:
        p=ROOT/'artifacts/momx_history/Watchlist'/(day+'.json')
        if not p.exists():continue
        doc=json.loads(p.read_text());n=cap=future_count=0
        for sym,rec in doc.get('rows',{}).items():
            cap+=bool(rec.get('truncated'))
            if sym not in weekly:continue
            for snap in rec.get('snapshots',[]):
                at=datetime.fromisoformat(snap['at']).astimezone(ET);t=at.timestamp();row=snap.get('row',{})
                if at.strftime('%Y-%m-%d')!=day or not 240<=minute(t)<=930:continue
                cells=[cell for group in ['rvol','skittles','sqz'] for cell in (row.get(group) or {}).values() if isinstance(cell,dict)]
                if any(isinstance(cell.get('barAt'),(int,float)) and cell['barAt']>t+1 for cell in cells):
                    future_count+=1;continue
                g=grade_row(row,at); n+=1
                sk=row.get('skittles') or {};rv=row.get('rvol') or {};sq=row.get('sqz') or {}
                news=row.get('news') or {};stamp=news.get('publishedAt') or news.get('at');catalyst=False
                if stamp and news.get('headline'):
                    try:catalyst=0<=t-datetime.fromisoformat(stamp.replace('Z','+00:00')).timestamp()<=86400
                    except (ValueError,TypeError):pass
                bysym[sym].append({'day':day,'at':t,'letter':g['letter'],'pct':row.get('pctChange'),'pushN':len(g['checks']['pushRvol']),'fired':bool(g['checks']['sqzFired']),
                    'sqz':any((sq.get(tf) or {}).get('bg')=='cyan' for tf in ['2h','4h','D','Wk']),
                    'optCross':all((sk.get(tf) or {}).get('bg') in BULL|{'dark_green'} for tf in ['2h','4h']),
                    'mxTrend':all((sk.get(tf) or {}).get('bg') in BULL or (sk.get(tf) or {}).get('fg') in {'cyan','dark_green'} for tf in ['D','2D','3D','4D','Wk','M']),
                    'mxRvol':any((rv.get(tf) or {}).get('bg')=='cyan' for tf in ['15m','30m','1h','2h']),
                    'shortRvol':any((rv.get(tf) or {}).get('bg')=='cyan' for tf in ['5m','15m','30m']),
                    'news':catalyst,'etf':'ETF' in str(row.get('industry') or '')})
        coverage.append({'day':day,'weeklySnapshots':n,'truncatedSymbols':cap,'excludedFutureCellSnapshots':future_count})
        print('Read',day,n,flush=True)
    return bysym,coverage

def bootstrap(vals_byday,seed=101):
    days=list(vals_byday)
    if len(days)<2:return None
    rng=random.Random(seed);means=[]
    for _ in range(1500):
        vals=[v for d in rng.choices(days,k=len(days)) for v in vals_byday[d]]
        means.append(statistics.mean(vals))
    means.sort();return [round(means[int(len(means)*.025)],3),round(means[int(len(means)*.975)],3)]

def stats(es):
    if not es:return {'n':0}
    gross=[e['gross'] for e in es];net=[e['net'] for e in es];groups=defaultdict(list)
    for e in es:groups[e['day']].append(e['net'])
    loss=-sum(x for x in net if x<0)
    return {'n':len(es),'days':len(groups),'targetPct':round(100*sum(e['exit'].startswith('target') for e in es)/len(es),1),'stopPct':round(100*sum(e['exit'].startswith('stop') for e in es)/len(es),1),
        'positiveGrossPct':round(100*sum(x>0 for x in gross)/len(es),1),'positiveNetPct':round(100*sum(x>0 for x in net)/len(es),1),'meanGross':round(statistics.mean(gross),3),'meanNet':round(statistics.mean(net),3),'medianNet':round(statistics.median(net),3),'profitFactorNet':round(sum(x for x in net if x>0)/loss,3) if loss else None,'netMeanDayBootstrap95':bootstrap(groups),'ambiguous':sum(e['ambiguous'] for e in es)}

def main():
    selftest();OUT.mkdir(exist_ok=True)
    census=json.loads((ROOT/'artifacts/momx_optionable.json').read_text())
    weekly={s for s,v in census['symbols'].items() if v.get('weeklies') is True}
    raw,coverage=load_history(weekly);trades=[];cand=Counter();missing=Counter();barcov=[];sessionbars={};eligible=defaultdict(dict);mxtime=Counter();daily=[]
    rules_missing=Counter()
    for count,(sym,snaps) in enumerate(sorted(raw.items()),1):
        p=ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')
        try:
            d=json.loads(gzip.decompress(p.read_bytes())) if p.exists() else {}
            recovered=OUT/'recovered-prices'/(sym+'.json')
            source=d.get('fineStudyBars',[])
            if recovered.exists():source=source+json.loads(recovered.read_text()).get('bars',[])
            bars=sorted({int(b['time']):b for b in source if valid(b)}.values(),key=lambda b:b['time'])
        except (OSError,ValueError):bars=[]
        if len(bars)<100:
            missing['symbolsWithoutFineTape']+=1;continue
        tape=Tape(bars);snaps.sort(key=lambda r:r['at']);seen=set();firstletters=set();sqzseen={}
        for day in DAYS:
            if tape.complete.get(day):sessionbars[(day,sym)]=tape.days[day]
        barcov.append({'symbol':sym,'completeSessions':sum(tape.complete.get(d,False) for d in DAYS)})
        def add(rule,r,extra=None):
            key=(r['day'],rule)
            if key in seen:return
            seen.add(key);cand[rule]+=1
            if not tape.complete.get(r['day']):missing[rule]+=1;return
            s=score(tape.days[r['day']],r['at'])
            if s is None:missing[rule]+=1;return
            e=dict(s,symbol=sym,day=r['day'],rule=rule,signalAt=r['at'],shortRvol=r['shortRvol'],pct=r['pct'])
            for target in [1,4]:e['target'+str(target)]=score(tape.days[r['day']],r['at'],target)
            if extra:e.update(extra)
            trades.append(e)
        for r in snaps:
            day=r['day'];at=r['at'];m=minute(at)
            if r['sqz']:sqzseen[day]=at
            if m<570:continue
            eligible[day].setdefault(sym,at)
            if r['letter']=='A+':add('A+',r)
            aa=r['letter'] in ['A','A+']; state=tape.state(at)
            if aa:add('A or A+',r)
            if m>=575 and r['mxTrend'] and r['mxRvol'] and r['news'] and not r['etf'] and at-sqzseen.get(day,-math.inf)<=1800:add('MomoX A+ without OI',r)
            if not aa:continue
            first=(day,r['letter']) not in firstletters;firstletters.add((day,r['letter']))
            if state is None:rules_missing['state_at_A_Aplus']+=1;continue
            short=r['shortRvol'] and state['macd']
            if short:add('A/A+ + short RVOL + MACD',r)
            go=state['buyers'] and tape.go.get(day,math.inf)<=at
            opt=m<600 and r['optCross'] and state['buyers'] and state['extended']
            if go:
                add('GO',r)
                if short:add('GO + short RVOL + MACD',r)
            if opt:add('Early OPT',r)
            if go or opt:add('Best GO or early OPT',r)
            if first and m>=575 and isinstance(r['pct'],(int,float)) and r['pct']<10 and state['extended'] and r['pushN']>=1:
                add('V2',r)
                if r['fired']:add('V3',r)
                if r['pushN']>=2:
                    # Retain unscorable candidates when choosing the first two: no replacement with winners.
                    daily.append(dict(r,symbol=sym))
        if count%20==0:print('Replayed',count,'/',len(raw),'symbols;',len(trades),'trades',flush=True)
    for day in DAYS:
        arr=sorted([e for e in daily if e['day']==day],key=lambda e:(e['at'],e['symbol']));uniq={}
        for e in arr:uniq.setdefault(e['symbol'],e)
        for e in list(uniq.values())[:2]:
            rule='Daily 2 first two';cand[rule]+=1;bs=sessionbars.get((day,e['symbol']))
            s=score(bs,e['at']) if bs else None
            if not s:missing[rule]+=1;continue
            trades.append(dict(s,rule=rule,day=day,symbol=e['symbol'],signalAt=e['at'],pct=e['pct'],shortRvol=e['shortRvol'],target1=score(bs,e['at'],1),target4=score(bs,e['at'],4)))
    # Matched controls: same day, same entry boundary, archive-observed weekly names.
    rng=random.Random(20260925)
    control_cache={}
    for e in trades:
        key=(e['day'],e['signalAt'])
        if key not in control_cache:
            pool=[]
            for sym,at in eligible[e['day']].items():
                bs=sessionbars.get((e['day'],sym))
                if bs and at<=e['signalAt']:
                    s=score(bs,e['signalAt'])
                    if s:pool.append((sym,s['net']))
            control_cache[key]=pool
        pool=[v for s,v in control_cache[key] if s!=e['symbol']]
        e['controlN']=len(pool);e['controlMeanNet']=statistics.mean(pool) if pool else None
    summaries={}
    for rule in RULES:
        es=[e for e in trades if e['rule']==rule];s=stats(es);s['identified']=cand[rule];s['excludedPriceCoverage']=missing[rule]
        s['lastWeek']=stats([e for e in es if e['day']>='2026-09-21'])
        s['earlier']=stats([e for e in es if e['day']<'2026-09-21'])
        s['before10']=stats([e for e in es if minute(e['signalAt'])<600])
        s['after1030']=stats([e for e in es if minute(e['signalAt'])>=630])
        s['target1']=stats([dict(e,**e['target1']) for e in es]);s['target4']=stats([dict(e,**e['target4']) for e in es])
        paired=[e for e in es if e['controlMeanNet'] is not None];diff=defaultdict(list)
        for e in paired:diff[e['day']].append(e['net']-e['controlMeanNet'])
        s['matchedControlMeanNet']=round(statistics.mean(e['controlMeanNet'] for e in paired),3) if paired else None
        s['excessOverControl']=round(statistics.mean(e['net']-e['controlMeanNet'] for e in paired),3) if paired else None
        s['excessDayBootstrap95']=bootstrap(diff)
        unique_days=sorted({e['day'] for e in es})
        leave_day=[statistics.mean(e['net'] for e in es if e['day']!=day) for day in unique_days if any(e['day']!=day for e in es)]
        s['leaveOneDayOutNetRange']=[round(min(leave_day),3),round(max(leave_day),3)] if leave_day else None
        summaries[rule]=s
    report={'period':DAYS,'method':'current grade on archived Watchlist snapshots; completed-five-minute momentum and completed-30-minute DI reconstruction; next five-minute open; full 78-bar sessions only; stock target 2%, stop 1.5%, close fallback; stop-first ambiguous bar; 0.10 percentage point roundtrip cost assumption','coverage':coverage,'censusSavedAt':census.get('savedAt'),'weeklySymbols':len(weekly),'archiveWeeklySymbols':len(raw),'barCoverage':barcov,'missing':dict(missing),'stateMissing':dict(rules_missing),'results':summaries,'trades':trades}
    report['sourceHashes']={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [ROOT/'momx/grade.py',ROOT/'momx/indicators.py',ROOT/'momx/momentum.py',ROOT/'momx/momox_aplus.py',ROOT/'momx/strategy.py']}
    report['completedAt']=datetime.now(ET).isoformat()
    report['recoveredSymbols']=len(list((OUT/'recovered-prices').glob('*.json')))
    (OUT/'results.json').write_text(json.dumps(report,indent=2))
    fields=['rule','day','symbol','signalAt','entryAt','entry','exitAt','exit','gross','net','ambiguous','controlMeanNet']
    with (OUT/'trades.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader()
        for e in trades:
            r={k:e.get(k) for k in fields}
            for k in ['signalAt','entryAt','exitAt']:r[k]=dt(r[k]).isoformat()
            w.writerow(r)
    print(json.dumps({'coverageSymbols':len(barcov),'missing':dict(missing),'results':summaries},indent=2),flush=True)

if __name__=='__main__':
    if '--selftest' in sys.argv:selftest();print('8 execution/data integrity checks passed')
    else:main()
