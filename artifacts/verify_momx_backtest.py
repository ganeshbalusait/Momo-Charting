"""Check reconstruction against app formulas and inspect look-ahead isolation."""
import bisect, gzip, json
from pathlib import Path
from datetime import datetime
from momx_rules_backtest_sep2026 import Tape, ROOT, ET, valid, selftest
from momx.momentum import summarize

selftest()
checks=0
for sym in ['META','SPY','QCOM']:
    doc=json.loads(gzip.decompress((ROOT/'artifacts/oi_chart_cache'/(sym+'.json.gz')).read_bytes()))
    bars=sorted({b['time']:b for b in doc['fineStudyBars'] if valid(b)}.values(),key=lambda b:b['time'])
    tape=Tape(bars)
    for clock in ['09:36','10:06','11:06','14:06']:
        at=datetime.fromisoformat('2026-09-21T'+clock+':00').replace(tzinfo=ET).timestamp()
        n=bisect.bisect_right(tape.times,at-300)
        prefix=bars[:n]
        actual=summarize(prefix,{},now_epoch=at)
        state=tape.state(at)
        assert state is not None and actual is not None
        assert state['extended']==(actual['state']=='extended'),(sym,clock,state,actual['state'])
        observed_go=(actual.get('gapGo') or {}).get('goAt')
        expected_go=observed_go+300 if observed_go else None
        reconstructed_go=tape.go.get('2026-09-21')
        if reconstructed_go is not None and reconstructed_go>at:reconstructed_go=None
        assert reconstructed_go==expected_go,(sym,clock,reconstructed_go,expected_go)
        # Re-running with no future bars must give the same reconstructed inputs.
        short=Tape(prefix).state(at)
        assert state==short,(sym,clock,state,short)
        checks+=1
print('8 fill/data checks passed; 12 momentum parity + 12 GO parity + 12 prefix/no-future checks passed')
