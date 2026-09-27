import json, gzip, sys
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, str(Path(__file__).resolve().parent / 'plot-deps'))
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'AgenticAI-Trading 2/artifacts/oi_chart_cache'
ET = ZoneInfo('America/New_York')
symbols = ['META','SPY','QCOM','QQQ','INTC','MRK','IBIT','IOVA','GEMI','RIOT']
fig, axes = plt.subplots(5,2,figsize=(15,19))
results=[]
for ax,symbol in zip(axes.flat,symbols):
    p=CACHE / (symbol+'.json.gz')
    fetched=ROOT/'artifacts/sept21-chart-inputs'/(symbol+'.json')
    if not fetched.exists() and not p.exists():
        ax.set_title(symbol+' — no cached chart');results.append({'symbol':symbol,'missing':True});continue
    d=json.loads(fetched.read_text()) if fetched.exists() else json.loads(gzip.decompress(p.read_bytes()))
    bars={int(b['time']):b for b in d.get('bars',[])}
    session=[]
    for t,b in sorted(bars.items()):
        dt=datetime.fromtimestamp(t,ET)
        if dt.strftime('%Y-%m-%d')=='2026-09-21' and 570<=dt.hour*60+dt.minute<960:
            session.append(b)
    if not session:
        ax.set_title(symbol+' — no September 21 regular-session bars');results.append({'symbol':symbol,'missing':True});continue
    buckets={}
    for b in session:
        t=int(b['time'])//300*300
        if t not in buckets:buckets[t]=dict(b,time=t)
        else:
            c=buckets[t];c['high']=max(c['high'],b['high']);c['low']=min(c['low'],b['low']);c['close']=b['close'];c['volume']+=b['volume']
    five=list(buckets.values());op=five[0]['open'];peak=max(five,key=lambda b:b['high'])
    end=five[-1]['close'];first=five[0]
    drawdown=0;running=op
    for b in five:
        drawdown=max(drawdown,(running-b['low'])/running*100);running=max(running,b['high'])
    r={'symbol':symbol,'minute_bars':len(session),'five_minute_bars':len(five),'open':op,'high':peak['high'],'high_time':datetime.fromtimestamp(peak['time'],ET).strftime('%H:%M'),'last_close':end,'last_bar':datetime.fromtimestamp(five[-1]['time'],ET).strftime('%H:%M'),'open_to_high_pct':round((peak['high']/op-1)*100,2),'open_to_close_pct':round((end/op-1)*100,2),'first_5m_close_pct':round((first['close']/op-1)*100,2),'max_prior_bar_high_to_later_low_drawdown_pct':round(drawdown,2)}
    results.append(r)
    for b in five:
        dt=datetime.fromtimestamp(b['time'],ET);x=dt.hour*60+dt.minute-570
        o,h,l,c=[(b[k]/op-1)*100 for k in ['open','high','low','close']]
        color='#008c95' if c>=o else '#bc3578'
        ax.vlines(x,l,h,color=color,linewidth=.8)
        ax.add_patch(Rectangle((x-1.8,min(o,c)),3.6,max(abs(c-o),.018),facecolor=color,edgecolor=color,linewidth=.5))
    ax.axhline(0,color='#88919a',linewidth=.7,linestyle='--')
    ax.set_xlim(-5,390);ax.set_xticks([0,90,210,330,390],['09:30','11:00','13:00','15:00','16:00'])
    ax.set_ylabel('% from 09:30 open');ax.grid(alpha=.15)
    ax.set_title(f"{symbol}  |  open ${op:g}  |  high +{r['open_to_high_pct']:.2f}%  |  close {r['open_to_close_pct']:+.2f}%",fontsize=11,loc='left')
fig.suptitle('September 21, 2026 — five-minute regular-session candles\nEach panel starts at its own opening price; vertical scales differ. Times ET.',fontsize=17,y=.998)
fig.tight_layout(rect=[0,0,1,.976])
fig.savefig(ROOT/'artifacts/sept21-five-minute-comparison.png',dpi=130)
(ROOT/'artifacts/sept21-five-minute-comparison.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
