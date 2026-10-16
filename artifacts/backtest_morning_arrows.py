"""Read-only Sep29 chart-arrow replay; outputs research files only."""
import csv,gzip,json,math,sys,time,hashlib
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from collections import Counter
ROOT=Path(__file__).resolve().parents[1]/'AgenticAI-Trading 2'
OUT=Path(__file__).resolve().parent/'chart-arrows-2026-09-29'
OUT.mkdir(exist_ok=True);(OUT/'prices').mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT))
ET=ZoneInfo('America/New_York');DAY='2026-09-29'
def stamp(s):return datetime.fromisoformat(s).timestamp()
START=stamp(DAY+'T04:00:00-04:00');END=stamp(DAY+'T10:00:00-04:00');CLOSE=stamp(DAY+'T20:00:00-04:00')
def iso(t):return datetime.fromtimestamp(t,ET).isoformat()
def write_csv(name,rows):
    if not rows:return
    fields=list(dict.fromkeys(k for r in rows for k in r))
    with (OUT/name).open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(rows)
def signals():
    source=ROOT/'artifacts/momx_chart_signals'/f'{DAY}.json'
    raw=source.read_bytes();(OUT/'signal-source.json').write_bytes(raw)
    book=json.loads(raw)['signals'];out={}
    for sym,items in book.items():
        selected=[]
        for a in items:
            if a.get('family') not in ('4x8','9x20') or a.get('label') not in ('CALL2H','CALL4H','C2H','C4H'):continue
            if not a.get('seenAt'):continue
            seen=stamp(a['seenAt']);at=stamp(a['at'])
            if not START<=seen<END or not START<=at<END:continue
            life=7200 if a['timeframe']=='2H' else 14400
            active_at=seen if seen-at>life else at
            expires=min(stamp(a['goneAt']) if a.get('goneAt') else math.inf,active_at+life)
            if expires<=seen:continue
            selected.append(dict(a,seen=seen,drawn=at,expires=expires))
        if selected:out[sym]=sorted(selected,key=lambda x:x['seen'])
    (OUT/'source-metadata.json').write_text(json.dumps(dict(date=DAY,sourceSHA256=hashlib.sha256(raw).hexdigest(),monitoredSymbols=len(book),morningSymbols=len(out),arrows=sum(map(len,out.values())),capturedAt=iso(time.time())),indent=2))
    return out
def recover(book):
    from data.schwab_client import SchwabClient
    client=SchwabClient();failed=[]
    for n,sym in enumerate(sorted(book),1):
        dest=OUT/'prices'/f'{sym}.json'
        if dest.exists():continue
        try:
            f=client.get_chart_bars(sym,timeframe='5Min',days_back=20)
            bars=[]
            for r in f.to_dict('records'):
                t=r.get('timestamp',r.get('time'));t=int(t.timestamp()) if hasattr(t,'timestamp') else int(t)
                bars.append(dict(time=t,**{k:float(r[k]) for k in ('open','high','low','close','volume')}))
            dest.write_text(json.dumps(dict(symbol=sym,source='Schwab configured read-only client',fetchedAt=iso(time.time()),bars=bars)))
            print('Fetched',n,len(book),sym,len(bars),flush=True)
        except Exception as e:
            failed.append(sym);print('Fetch failed',sym,type(e).__name__,flush=True)
            if len(failed)>=3:break
        time.sleep(.25)
    return failed
RULES={'1 CALL2H OR CALL4H':('CALL2H','CALL4H'),'2 C2H OR C4H':('C2H','C4H'),'3 CALL2H AND CALL4H':('CALL2H','CALL4H'),'4 C2H AND C4H':('C2H','C4H')}
def triggers(arrows,labels,both,family):
    a=[s for s in arrows if s['label'] in labels and (family=='either' or s['family']==family)]
    if not both:return [(s['seen'],[s]) for s in a]
    result=[]
    for t in sorted(set(s['seen'] for s in a)):
        live=[s for s in a if s['seen']<=t<s['expires']]
        if all(any(s['label']==label for s in live) for label in labels):
            chosen=[max((s for s in live if s['label']==label),key=lambda s:s['seen']) for label in labels]
            result.append((t,chosen))
    return result
def loadbars(sym):
    p=OUT/'prices'/f'{sym}.json'
    if not p.exists():return [],'no recovered prices'
    doc=json.loads(p.read_text());by={}
    for b in doc['bars']:
        o,h,l,c,v=[b[k] for k in ('open','high','low','close','volume')]
        if all(math.isfinite(x) for x in (o,h,l,c,v)) and 0<l<=min(o,c)<=max(o,c)<=h and v>=0 and b['time']%300==0:by[b['time']]=b
    bars=sorted(by.values(),key=lambda b:b['time']);ema=None
    for b in bars:
        ema=b['close'] if ema is None else ema+(b['close']-ema)*2/22
        b['ema21']=ema
    return bars,doc['source']
def trade(bars,at):
    entryat=(int(at)//300+1)*300
    if entryat>=END:return None,'entry after morning cutoff'
    index={b['time']:i for i,b in enumerate(bars)}
    if entryat not in index:return None,'missing exact next entry bar'
    i=index[entryat]
    if i<105:return None,'insufficient EMA warmup'
    future=[b for b in bars[i:] if b['time']<CLOSE and b['time']+300<=time.time()]
    if not future:return None,'no completed bars after entry'
    entry=future[0]['open'];exitprice=None;exitAt=None;exitSignal=None;emaExit=None;maxprice=entry;maxtime=entryat;gaps=0
    # Standard-hours gaps invalidate an executable replay; extended-hours
    # missing bars are flagged because they may be no-trade intervals.
    for j,b in enumerate(future):
        if j and b['time']-future[j-1]['time']>300:
            if future[j-1]['time']>=stamp(DAY+'T09:30:00-04:00'):return None,'regular-session price gap before exit'
            gaps+=1
        if b['high']>maxprice:maxprice=b['high'];maxtime=b['time']
        if b['close']<b['ema21']:
            exitSignal=b['time']+300;emaExit=b['ema21']
            if j+1<len(future) and future[j+1]['time']==exitSignal:
                exitprice=future[j+1]['open'];exitAt=exitSignal
                if exitprice>maxprice:maxprice=exitprice;maxtime=exitAt
            elif future[-1]['time']>exitSignal:
                return None,'missing exact EMA exit bar'
            break
    closed=exitprice is not None;mark=exitprice if closed else future[-1]['close']
    gross=(mark/entry-1)*100;net=gross-.10
    return dict(entryET=iso(entryat),entry=entry,exitSignalET=iso(exitSignal) if exitSignal else '',exitET=iso(exitAt) if exitAt else '',exit=exitprice,emaAtExitSignal=emaExit,status=('WIN' if net>0 else 'LOSS' if net<0 else 'FLAT') if closed else 'OPEN',roiGrossPct=gross,roiNetPct=net,pnlPer1000=net*10,maxGainPct=(maxprice/entry-1)*100,maxGainET=iso(maxtime),markET=iso(future[-1]['time']+300) if not closed else iso(exitAt),premarketGaps=gaps),''
def tests():
    a=[dict(label='CALL2H',family='4x8',seen=10,expires=20),dict(label='CALL4H',family='9x20',seen=21,expires=30)]
    assert not triggers(a,('CALL2H','CALL4H'),True,'either')
    a[1]['seen']=19
    assert triggers(a,('CALL2H','CALL4H'),True,'either')[0][0]==19
    assert not triggers(a,('CALL2H','CALL4H'),True,'4x8')
    # Completed-candle exit: never fill at the signal close or use the exit
    # bar's later high as a maximum achievable gain before exit.
    t=stamp(DAY+'T09:35:00-04:00')
    warm=[dict(time=t-(105-i)*300,open=100,high=100,low=100,close=100,ema21=100) for i in range(105)]
    bars=warm+[dict(time=t,open=100,high=103,low=98,close=99,ema21=100),dict(time=t+300,open=98,high=200,low=97,close=150,ema21=110)]
    r,reason=trade(bars,t-10)
    assert not reason and r['exit']==98 and abs(r['maxGainPct']-3)<1e-8 and r['status']=='LOSS'
def main():
    tests();book=signals()
    if '--recover' in sys.argv:recover(book)
    ledger=[];audit=[]
    for sym,arrows in sorted(book.items()):
        bars,source=loadbars(sym)
        for family in ('either','4x8','9x20'):
            for rule,labels in RULES.items():
                ts=triggers(arrows,labels,'AND' in rule,family)
                if not ts:continue
                at,chosen=ts[0]  # one first signal per ticker/rule/day, no cherry picking
                result,reason=trade(bars,at) if bars else (None,source)
                base=dict(rule=rule,color={'either':'Either color','4x8':'Yellow','9x20':'Cyan'}[family],ticker=sym,signalET=iso(at),labels=' + '.join(s['label'] for s in chosen),families=' + '.join(s['family'] for s in chosen),drawnET=' + '.join(s['at'] for s in chosen),goneET=' + '.join(s.get('goneAt','') for s in chosen))
                audit.append(dict(base,coverage=reason or 'priced'))
                if result:ledger.append(dict(base,**result))
    summary=[]
    for color in ('Either color','Yellow','Cyan'):
        for rule in RULES:
            rows=[r for r in ledger if r['rule']==rule and r['color']==color];closed=[r for r in rows if r['status']!='OPEN'];wins=sum(r['status']=='WIN' for r in closed)
            summary.append(dict(rule=rule,color=color,trades=len(rows),closed=len(closed),wins=wins,losses=sum(r['status']=='LOSS' for r in closed),open=len(rows)-len(closed),winRatePct=100*wins/len(closed) if closed else None,totalPnlPer1000Each=sum(r['pnlPer1000'] for r in closed),meanNetRoiPct=sum(r['roiNetPct'] for r in closed)/len(closed) if closed else None))
    write_csv('trades.csv',ledger);write_csv('candidate-audit.csv',audit);write_csv('summary.csv',summary)
    (OUT/'results.json').write_text(json.dumps(dict(date=DAY,summary=summary,trades=ledger,audit=audit),indent=2))
    print(json.dumps(dict(summary=summary[:4],coverage=Counter(r['coverage'] for r in audit)),indent=2),flush=True)
if __name__=='__main__':main()
