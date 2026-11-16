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
        # His rule 2026-11-10: RVOL colour counts without a rise, so the first
        # publication already confirms; rising is reported in `increasing` only.
        first=call(1,0)['X']
        assert first['highestLevel']==3 and first['confirms']==['rvol','sqz','skittles']
        assert first['increasing']==[] and first['rvol'] is True
        assert engine.contexts['X']['historyReady'] is False
        result=call(2,15)['X'];assert result['highestLevel']==3 and result['setupCount']>=3
        assert result['increasing']==['5m','15m','30m']
        assert engine.contexts['X']['highestLevel'] == 3
        assert engine.contexts['X']['confirms'] == ['rvol','sqz','skittles']
        assert result['momMandatory'] is False and not any(t['key']=='tagMomStrong' for t in result['setupTypes'])
        assert call(2,15)['X']['highestLevel']==3
        # His "Last change" rule (2026-11-06) still drives `increasing`.
        assert call(2,30)['X']['increasing']==['5m','15m','30m']
        fell=call(1.5,40)['X']
        assert fell['increasing']==[] and fell['highestLevel']==3, 'a falling cyan RVOL still counts'
        assert call(3,45,'Other')['X']['increasing']==[]
    finally:engine.close()

@pytest.mark.skipif(not shutil.which('node'),reason='Node required for production shared-rule integration')
def test_stale_candles_never_become_a_bot_level(monkeypatch):
    # 2026-11-10: the Live board shows a stale row's level from its last candle.
    # That is display only: the same board fields must give the bot nothing.
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1');engine=LevelEvaluator()
    old=NOW-timedelta(hours=1);stale=row(2);stale['marketDataAt']=old.isoformat()
    stale['m5']['sqzMom']['barAt']=old.timestamp()
    try:
        assert engine.evaluate('Watchlist',{'generatedAt':NOW.isoformat(),'rows':[stale]},NOW)=={}
        assert 'X' not in engine.contexts
    finally:engine.close()

def test_missing_and_future_publication_never_start_worker(monkeypatch):
    e=LevelEvaluator();monkeypatch.setattr(e,'_start',lambda:pytest.fail('must not start'))
    # None = not evaluated (the bot must not read it as "every Level setup went away").
    assert e.evaluate('W',{},NOW) is None
    assert e.contexts == {}
    assert e.evaluate('W',{'generatedAt':(NOW+timedelta(seconds=1)).isoformat()},NOW) is None
    assert e.evaluate('W',{'generatedAt':(NOW-timedelta(seconds=121)).isoformat()},NOW) is None

def test_disabled_or_failed_worker_returns_none_not_an_empty_evaluation(monkeypatch):
    e=LevelEvaluator()
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','0')
    assert e.evaluate('W',{'generatedAt':NOW.isoformat()},NOW) is None and e.status['state']=='disabled'
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1')
    def boom(): raise RuntimeError('Node runtime unavailable; level entries paused')
    monkeypatch.setattr(e,'_start',boom)
    assert e.evaluate('W',{'generatedAt':NOW.isoformat()},NOW) is None and e.status['state']=='unavailable'

def test_capture_three_separate_virtual_rules_once_and_preserve_evidence(tmp_path,monkeypatch):
    signal={'highestLevel':3,'setupCount':3,'setupTypes':[{'key':x,'text':x} for x in ('tagRvol','tagSqz','tagExplosive')], 'momMandatory':False}
    book=ot.OptionTrackBook(tmp_path,fetch=lambda s:None,background=False,healthy=lambda:False)
    book._levels=SimpleNamespace(evaluate=lambda *a,**k:{'X':signal},status={'state':'ready'})
    monkeypatch.setattr(ot,'fired',lambda *a:[]);monkeypatch.setattr(ot,'live_today',lambda *a:True)
    book.apply('W',{'rows':[row(2)]},NOW);book.apply('W',{'rows':[row(3)]},NOW)
    trades=[t for t in book.trades() if t['rule'].startswith('level')]
    assert [t['rule'] for t in trades]==['level1','level2','level3']
    assert all(t['virtualOnly'] and t['levelSignal']['setupCount']==3 for t in trades)
    assert all('levelSignal' in t for t in book.summary())
    for t in trades:
        # 2026-11-09: the paper accounts trade exactly these three strategies, on the 0.20-delta contract.
        live={**t,'status':'open','contract':'X','entryAt':NOW.isoformat()}
        assert eligible(live,NOW.timestamp(),None)
        assert not eligible(live,NOW.timestamp(),None,'entry')              # no 0.20-delta contract recorded
        assert eligible({**live,'eContract':'XE'},NOW.timestamp(),None,'entry')
        reviewed=evidence('bull',{**t,'status':'closed','entry':1,'exitPrice':.8})
        assert reviewed['levelAtSignal']['highestLevel']==3 and reviewed['rule']==t['rule']
    assert trade_first()['approved'] is True
    decision=trade_first('bull',{'rule':'level1'},NOW.timestamp())
    assert decision['approved'] is True and 'probability' not in decision['prediction']
    assert specialist_task({'patterns':[{'rule':'level2','scope':'virtual','side':'bull'}]}, {})['role']=='squeeze'

def test_bear_book_trades_its_own_mirrored_levels(tmp_path,monkeypatch):
    """Bear Level 1/2/3 (his "Yes" 2026-11-05): the bear book asks its own
    evaluator with bear=True and records put Level trades."""
    seen=[]
    signal={'highestLevel':2,'setupCount':3,'setupTypes':[{'key':x,'text':x} for x in ('tagRvol','tagSqz','tagExplosive')],'momMandatory':False}
    book=ot.OptionTrackBook(tmp_path,direction='bear',fetch=lambda s:None,background=False,healthy=lambda:False)
    book._levels=SimpleNamespace(evaluate=lambda *a,**k:(seen.append(k.get('bear')) or {'X':signal}),status={'state':'ready'},contexts={})
    monkeypatch.setattr(ot,'fired',lambda *a:[]);monkeypatch.setattr(ot,'live_today',lambda *a:True)
    book.apply('W',{'rows':[row(2)]},NOW)
    assert seen and all(seen)
    assert [t['rule'] for t in book.trades() if t['rule'].startswith('level')]==['level1','level2']


@pytest.mark.skipif(not shutil.which('node'),reason='Node required for production shared-rule integration')
def test_worker_mirrors_colors_for_bear_rows(monkeypatch):
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1');engine=LevelEvaluator()
    def bear_row(value):
        r=row(value);r['bestRank']=1
        r['rvol']={tf:{'value':value,'bg':'magenta','fg':'black','barAt':NOW.timestamp()} for tf in ('5m','15m','30m')}
        r['sqz']={tf:{'value':5,'bg':'orange','fg':'black'} for tf in ('2h','4h','D')};r['sqz']['D']={'value':2,'bg':'magenta','fg':'black'}
        r['skittles']={'M':{'value':40,'bg':'red','fg':'black'}};r['gradeFresh']['timeline']=[]
        return r
    def call(value,seconds):
        at=NOW+timedelta(seconds=seconds)
        return engine.evaluate('Watchlist',{'generatedAt':at.isoformat(),'rows':[bear_row(value)]},at,bear=True)
    try:
        assert call(1,0)['X']['highestLevel']==3   # colour only since 2026-11-10
        got=call(2,15)['X']
        assert got['highestLevel']==3 and got['confirms']==['rvol','sqz','skittles']
    finally:engine.close()


def test_signal_snapshot_is_frozen_for_normal_and_star_entries(tmp_path, monkeypatch):
    from momx.scanner_learning import snapshot
    source=row(2)
    context={'version':1,'publicationAt':NOW.isoformat(),'highestLevel':0,'historyReady':False,
             'setupTypes':[{'key':'tagRvol'}],'setupCount':1,'increasing':[]}
    source['m5']['chartContext']={'version':1,'timeframe':'5m','barAt':int(NOW.timestamp())-300,
        'availableAt':int(NOW.timestamp()),'capturedAt':int(NOW.timestamp()),'referencePrice':100,'levels':{'ema21':99}}
    book=ot.OptionTrackBook(tmp_path,fetch=lambda s:None,background=False,healthy=lambda:False)
    book._levels=SimpleNamespace(evaluate=lambda *a,**k:{},contexts={'X':context},status={'state':'ready'})
    monkeypatch.setattr(ot,'fired',lambda *a:['go'])
    monkeypatch.setattr(ot,'live_today',lambda *a:True)
    monkeypatch.setattr(ot,'star_one',lambda *a:'X')
    book.apply('W',{'rows':[source]},NOW)
    trades=[t for t in book.trades() if t['rule'] in ('go','star1')]
    assert {t['rule'] for t in trades}=={'go','star1'}
    context['setupTypes'][0]['key']='tagSqz'
    source['m5']['chartContext']['levels']['ema21']=500
    for t in trades:
        assert snapshot(t,'bull')['setupTypes']==[{'key':'tagRvol'}]
        assert t['setup']['chartContext']['levels']['ema21']==99
        changed=snapshot(t,'bull');changed['setupTypes'].clear()
        assert snapshot(t,'bull')['setupCount']==1


@pytest.mark.skipif(not shutil.which('node'),reason='Node required for production shared-rule integration')
def test_worker_uses_his_2026_11_05_level_rules(monkeypatch):
    """SQZ = one of 2h/4h/D cyan, blank OR fired (Weekly ignored); Skittles = a
    box 2h-M; every level needs 3+ Setup types. Since his rule 2026-11-10 the
    level COUNTS RVOL / SQZ / Skittles (any one = L1). Real Node worker."""
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1');engine=LevelEvaluator()
    def variant(symbol,value,sqz,fire,skit):
        r=row(value);r['symbol']=symbol;r['bestRank']=1  # ⭐1 = a third Setup type
        r['sqz']={tf:{'value':5,'bg':'orange','fg':'black'} for tf in ('2h','4h','D')}
        r['sqz'].update(sqz)
        r['gradeFresh']['timeline']=[{'what':f'SQZ {fire} released','at':(NOW-timedelta(minutes=1)).isoformat()}] if fire else []
        if not skit: r['skittles']={'M':{'value':52,'bg':'black','fg':'green'}}
        return r
    cases={'ORNG':({},None,True),'WKONLY':({'Wk':{'value':3,'bg':'cyan','fg':'black'}},'Wk',True),
           'FIRE2H':({},'2h',True),'BLANKD':({'D':{'value':'-','bg':'black','fg':'black'}},None,True),
           'NOSKIT':({'4h':{'value':2,'bg':'cyan','fg':'black'}},None,False)}
    def call(value,seconds):
        at=NOW+timedelta(seconds=seconds)
        rows=[variant(s,value,*c) for s,c in cases.items()]
        return engine.evaluate('Watchlist',{'generatedAt':at.isoformat(),'rows':rows},at)
    try:
        # The first publication already counts (colour, not rise).
        assert {s:v['highestLevel'] for s,v in call(1,0).items()}=={'ORNG':2,'WKONLY':2,'FIRE2H':3,'BLANKD':3,'NOSKIT':2}
        got=call(2,15)
        levels={s:engine.contexts[s]['highestLevel'] for s in cases}
        assert levels=={'ORNG':2,'WKONLY':2,'FIRE2H':3,'BLANKD':3,'NOSKIT':2}, levels
        assert engine.contexts['ORNG']['confirms']==['rvol','skittles']
        assert engine.contexts['NOSKIT']['confirms']==['rvol','sqz']
        for s,level in levels.items():
            ctx=engine.contexts[s]
            assert ctx['setupCount']>=3, (s,ctx['setupCount'])
            assert got[s]['highestLevel']==level
    finally:engine.close()


@pytest.mark.skipif(not shutil.which('node'),reason='Node required for production shared-rule integration')
def test_worker_counts_confirms_any_one_is_level_1(monkeypatch):
    """His rule 2026-11-10 ("Setup3 with Rvol or sqz or skittles"): black RVOL
    and no SQZ, but a Skittles 2h box -> Level 1, and the signal says which
    confirm fired. Mirrors CMCSA 2026-11-10 11:23. Real Node worker."""
    monkeypatch.setenv('AGX_BOT_LEVEL_STRATEGIES','1');engine=LevelEvaluator()
    r=row(2);r['bestRank']=1
    r['rvol']={tf:{'value':.4,'bg':'black','fg':'black','barAt':NOW.timestamp()} for tf in ('5m','15m','30m','1h','2h','4h','D')}
    r['sqz']={'2h':{'value':5,'bg':'white','fg':'black'},'4h':{'value':5,'bg':'orange','fg':'black'},'D':{'value':5,'bg':'orange','fg':'black'}}
    r['skittles']={'2h':{'value':60,'bg':'cyan','fg':'black'},'4h':{'value':58,'bg':'green','fg':'black'}}
    # A Weekly fire is a Setup type (the SQZ fire tag) but not the SQZ confirm,
    # which only looks at 2h/4h/D: Star 1 + SQZ fire + Explosive = 3 types.
    r['gradeFresh']['timeline']=[{'what':'SQZ Wk released','at':(NOW-timedelta(minutes=1)).isoformat()}]
    try:
        got=engine.evaluate('Watchlist',{'generatedAt':NOW.isoformat(),'rows':[r]},NOW)
        ctx=engine.contexts['X']
        assert ctx['setupCount']==3, ctx['setupTypes']
        assert ctx['confirms']==['skittles'] and ctx['rvol'] is False and ctx['sqz'] is False and ctx['fire'] is False
        assert got['X']['highestLevel']==1 and got['X']['confirms']==['skittles'] and got['X']['skittles'] is True
        # Same row with only 2 Setup types (no Star 1) is Level 0 and sends no signal.
        r2={**r,'symbol':'Y','bestRank':5}
        got=engine.evaluate('Watchlist',{'generatedAt':NOW.isoformat(),'rows':[r2]},NOW)
        assert got=={} and engine.contexts['Y']['highestLevel']==0 and engine.contexts['Y']['setupCount']<3
    finally:engine.close()
