"""Read-only gate comparison against the user's Sep29 TOS screenshot."""
import json,sys,csv
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]/'AgenticAI-Trading 2'
sys.path.insert(0,str(ROOT))
from data.schwab_client import SchwabClient
from momx.board import build_tapes
ET=ZoneInfo('America/New_York');OUT=Path(__file__).resolve().parent/'tos-parity-sep29';OUT.mkdir(exist_ok=True)
client=SchwabClient();rows=[]
for sym in ['LASR','SHEL','NTRA','PM','WELL','AMGN','CRM','CHPT','META','TEAM','CLS','GME','RXRX']:
    path=OUT/(sym+'.json')
    if path.exists():bars=json.loads(path.read_text())
    else:
        f=client.get_chart_bars(sym,timeframe='5Min',days_back=20)
        bars=[dict(time=int(r['timestamp'].timestamp()),**{k:float(r[k]) for k in ['open','high','low','close','volume']}) for r in f.to_dict('records')]
        path.write_text(json.dumps(bars))
    bars=[b for b in bars if datetime.fromtimestamp(b['time'],ET).strftime('%Y-%m-%d %H:%M')<'2026-09-29 20:00']
    tapes=build_tapes(bars,bars,[])
    def change(tf,key,offset):
        a=tapes[tf][-1];b=tapes[tf][-1-offset]
        return dict(pct=100*(a[key]/b[key]-1) if b[key] else None,current=a[key],baseline=b[key],currentAt=datetime.fromtimestamp(a['time'],ET).isoformat(),baselineAt=datetime.fromtimestamp(b['time'],ET).isoformat())
    row=dict(symbol=sym,inTos=sym in ['LASR','SHEL','NTRA','PM','WELL','AMGN','CRM'],close1h=change('1h','close',2),volume4h2=change('4h','volume',2),volume4h3=change('4h','volume',3))
    rows.append(row);print(sym,json.dumps(row),flush=True)
(OUT/'gate-comparison.json').write_text(json.dumps(rows,indent=2))
