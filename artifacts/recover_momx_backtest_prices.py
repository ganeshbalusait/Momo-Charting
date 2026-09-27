"""Fetch missing research inputs through the app's configured read-only data client."""
import sys,json,gzip,time
from pathlib import Path
from datetime import datetime
from momx_rules_backtest_sep2026 import ROOT,OUT,DAYS,ET,valid,minute,load_history
from data.schwab_client import SchwabClient

limit=int(sys.argv[1]) if len(sys.argv)>1 else 1
skip=set(sys.argv[2].split(',')) if len(sys.argv)>2 else set()
folder=OUT/'recovered-prices';folder.mkdir(exist_ok=True)
weekly={s for s,v in json.loads((ROOT/'artifacts/momx_optionable.json').read_text())['symbols'].items() if v.get('weeklies') is True}
raw,_=load_history(weekly);targets=[]
for sym,rows in sorted(raw.items()):
    if sym in skip:continue
    if (folder/(sym+'.json')).exists():continue
    required={r['day'] for r in rows if minute(r['at'])>=570 and (r['letter'] in ['A','A+'] or (r['mxTrend'] and r['mxRvol'] and r['news']))}
    if not required:continue
    try:doc=json.loads(gzip.decompress((ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')).read_bytes()))
    except (OSError,ValueError):doc={}
    byday={}
    for b in doc.get('fineStudyBars',[]):
        if not valid(b):continue
        stamp=datetime.fromtimestamp(b['time'],ET)
        if 570<=stamp.hour*60+stamp.minute<960:byday.setdefault(stamp.strftime('%Y-%m-%d'),set()).add(int(b['time']))
    missing=[]
    for day in required:
        start=int(datetime.fromisoformat(day+'T09:30:00').replace(tzinfo=ET).timestamp())
        if byday.get(day,set())!=set(range(start,start+23400,300)):missing.append(day)
    if missing:targets.append((sym,missing))
print('Recovery targets',len(targets),'limit',limit,flush=True)
client=SchwabClient()
for n,(sym,missing) in enumerate(targets[:limit],1):
    try:
        frame=client.get_chart_bars(sym,timeframe='5Min',days_back=60)
        if frame.empty:
            print('Empty response; stopping without repeated requests',sym,flush=True);break
        bars=[]
        for r in frame.to_dict('records'):
            stamp=r.get('timestamp',r.get('time'))
            t=int(stamp.timestamp()) if hasattr(stamp,'timestamp') else int(stamp)
            bars.append(dict(time=t,**{k:float(r[k]) for k in ['open','high','low','close','volume']}))
        (folder/(sym+'.json')).write_text(json.dumps({'symbol':sym,'source':'configured Schwab read-only client','fetchedAt':datetime.now(ET).isoformat(),'bars':bars}))
        print('Recovered',n,sym,len(bars),'bars; missing sessions',len(missing),flush=True)
    except Exception as e:
        print('Recovery blocked',sym,type(e).__name__,'; stopping to avoid repeated failures',flush=True);break
    if n<min(limit,len(targets)):time.sleep(2)
