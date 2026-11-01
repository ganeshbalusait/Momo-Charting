import json
import shutil
from datetime import datetime,timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo
import pytest
from momx.bot_levels import LevelEvaluator
from momx import option_track as ot
from momx.paper_mirror import eligible
from momx.paper_learning import evidence,trade_first,specialist_task

ET=ZoneInfo('America/New_York');NOW=datetime(2026,10,30,10,0,tzinfo=ET)
def row(value):
    return {'symbol':'X','last':100,'marketDataAt':NOW.isoformat(),'grade':{'letter':'A'},
      'rvol':{tf:{'value':value,'bg':'cyan','fg':'black','barAt':NOW.timestamp()} for tf in ('5m','15m','30m')},
      'sqz':{tf:{'value':'-','bg':'black','fg':'black'} for tf in ('2h','4h','D','Wk')},
      'skittles':{'M':{'value':52,'bg':'green','fg':'black'}},
      'gradeFresh':{'timeline':[{'what':'SQZ 4h released','at':(NOW-timedelta(minutes=1)).isoformat()}]},
      'm5':{'pattern':'explosive','sqzMom':{'barAt':NOW.timestamp(),'color':'dark_blue'}}}

@pytest.mark.skipif(not shutil.which('node'),reason='Node required for production shared-rule integration')
def test_exact_worker_warmup_duplicates_equal_values_isolation_and_optional_mom(monkeypatch):
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1');engine=LevelEvaluator()
    def call(value,seconds,board='Watchlist'):
        at=NOW+timedelta(seconds=seconds);return engine.evaluate(board,{'generatedAt':at.isoformat(),'rows':[row(value)]},at)
    try:
        assert call(1,0)=={}
        assert engine.contexts['X']['highestLevel'] == 0
        assert engine.contexts['X']['historyReady'] is False
        result=call(2,15)['X'];assert result['highestLevel']==3 and result['setupCount']>=3
        assert engine.contexts['X']['highestLevel'] == 3
        assert result['momMandatory'] is False and not any(t['key']=='tagMomStrong' for t in result['setupTypes'])
        assert call(2,15)['X']['highestLevel']==3
        assert call(2,30)=={}
        assert engine.contexts['X']['highestLevel'] == 0
        assert call(3,45,'Other')=={}
    finally:engine.close()

def test_missing_and_future_publication_never_start_worker(monkeypatch):
    e=LevelEvaluator();monkeypatch.setattr(e,'_start',lambda:pytest.fail('must not start'))
    assert e.evaluate('W',{},NOW)=={}
    assert e.contexts == {}
    assert e.evaluate('W',{'generatedAt':(NOW+timedelta(seconds=1)).isoformat()},NOW)=={}

def test_capture_three_separate_virtual_rules_once_and_preserve_evidence(tmp_path,monkeypatch):
    signal={'highestLevel':3,'setupCount':3,'setupTypes':[{'key':x,'text':x} for x in ('tagRvol','tagSqz','tagExplosive')], 'momMandatory':False}
    book=ot.OptionTrackBook(tmp_path,fetch=lambda s:None,background=False,healthy=lambda:False)
    book._levels=SimpleNamespace(evaluate=lambda *a:{'X':signal},status={'state':'ready'})
    monkeypatch.setattr(ot,'fired',lambda *a:[]);monkeypatch.setattr(ot,'live_today',lambda *a:True)
    book.apply('W',{'rows':[row(2)]},NOW);book.apply('W',{'rows':[row(3)]},NOW)
    trades=[t for t in book.trades() if t['rule'].startswith('level')]
    assert [t['rule'] for t in trades]==['level1','level2','level3']
    assert all(t['virtualOnly'] and t['levelSignal']['setupCount']==3 for t in trades)
    assert all('levelSignal' in t for t in book.summary())
    for t in trades:
        assert not eligible({**t,'status':'open','contract':'X','entryAt':NOW.isoformat()},NOW.timestamp(),None)
        reviewed=evidence('bull',{**t,'status':'closed','entry':1,'exitPrice':.8})
        assert reviewed['levelAtSignal']['highestLevel']==3 and reviewed['rule']==t['rule']
    assert trade_first()['approved'] is True
    decision=trade_first('bull',{'rule':'level1'},NOW.timestamp())
    assert decision['approved'] is True and 'probability' not in decision['prediction']
    assert specialist_task({'patterns':[{'rule':'level2','scope':'virtual','side':'bull'}]}, {})['role']=='squeeze'

def test_bear_does_not_invent_mirrored_levels(tmp_path,monkeypatch):
    book=ot.OptionTrackBook(tmp_path,direction='bear',fetch=lambda s:None,background=False,healthy=lambda:False)
    book._levels=SimpleNamespace(evaluate=lambda *a:pytest.fail('bull-only definitions'),status={})
    monkeypatch.setattr(ot,'fired',lambda *a:[]);monkeypatch.setattr(ot,'live_today',lambda *a:True)
    book.apply('W',{'rows':[row(2)]},NOW)
    assert not any(t['rule'].startswith('level') for t in book.trades())
