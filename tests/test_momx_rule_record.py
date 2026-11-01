"""Recorded quotes, completed exits, costs and coverage behind scanner history."""
from datetime import datetime
import pytest
from momx.rule_record import build_record, summarize, exclusion

def trade(**patch):
    return dict(rule='dayLine', symbol='TEST', contract='TEST_CALL', status='closed',
        at='2026-10-09T09:45:00-04:00', entryAt='2026-10-09T09:45:20-04:00',
        exitAt='2026-10-09T10:00:00-04:00', exitPriceAt='2026-10-09T10:00:30-04:00',
        entry=1., entryBid=.9, entryAsk=1.1, exitPrice=2., exitBid=1.9, exitAsk=2.1) | patch

def test_realized_record_counts_losses_costs_and_no_open_mark_fallback():
    rows=[trade(), trade(symbol='LOSS', exitPrice=.5, exitBid=.4, exitAsk=.6),
          trade(symbol='OPEN', status='open', exitPrice=None, last=20),
          trade(symbol='MISSING', exitPrice=None, last=10)]
    result=summarize([{'day':'2026-10-09','trades':rows}])['dayLine']
    assert result['n']==2 and result['wins']==1 and result['losses']==1
    assert result['winRate']==50 and result['roi']==25
    assert result['netPnl']==50 and result['estimatedFees']==0
    assert result['avgNet']==25 and result['avgWin']==100 and result['avgLoss']==-50
    assert result['maxClosedDrawdown']==50
    assert result['excluded']=={'open':1,'missing_strategy_exit':1}
    assert result['coveragePct']==50

@pytest.mark.parametrize('patch,reason',[
    ({'entryBid':None},'missing_bid_ask'), ({'exitBid':None},'missing_bid_ask'),
    ({'entry':True},'missing_entry'), ({'exitPrice':float('nan')},'missing_strategy_exit'),
    ({'entryAsk':.8},'invalid_midpoint'), ({'exitPrice':3},'invalid_midpoint'),
    ({'badQuote':True},'bad_quote'), ({'exitPriceAt':None},'missing_timestamps'),
    ({'exitPriceAt':'2026-10-09T10:02:01-04:00'},'late_exit_quote'),
    ({'entryAt':'2026-10-09T09:55:01-04:00'},'late_entry'),
    ({'exitAt':'2026-10-09T09:40:00-04:00'},'invalid_time_order'),
    ({'exitPriceAt':'2026-10-09T09:59:59-04:00'},'invalid_time_order'),
    ({'contract':None},'missing_identity'), ({'status':'pending'},'not_closed'),
])
def test_unverifiable_records_are_excluded_with_reason(patch,reason):
    assert exclusion(trade(**patch))==reason

def test_zero_midpoint_is_a_full_loss_and_flat_gross_remains_flat():
    rows=[trade(exitPrice=0,exitBid=0,exitAsk=0),trade(symbol='FLAT',exitPrice=1,exitBid=.9,exitAsk=1.1)]
    result=summarize([{'day':'2026-10-09','trades':rows}])['dayLine']
    assert result['losses']==1 and result['breakEven']==1 and result['netPnl']==-100

def test_duplicate_records_do_not_inflate_results_and_early_rule_uses_et():
    t=trade(at='2026-10-09T13:45:00Z')
    result=summarize([{'day':'2026-10-09','trades':[t,t]}])
    assert result['dayLine']['n']==1 and result['dayLine']['excluded']=={'duplicate':1}
    assert result['dayLineEarly']['n']==1
    assert 'dayLineEarly' not in summarize([{'day':'2026-10-09','trades':[trade(at='2026-10-09T08:00:00-04:00')]}])

def test_only_completed_sessions_with_records_and_separate_side_ranges():
    previous={'day':'2026-10-09','trades':[trade()]}
    today={'day':'2026-10-12','trades':[trade(symbol='TODAY')]}
    record=build_record([previous,{'day':'2026-10-10','trades':[]},today],[],10,datetime.fromisoformat('2026-10-12T12:00:00-04:00'))
    assert record['version']==2 and record['bull']['dayLine']['n']==1
    assert record['ranges']['bull']=={'days':1,'from':'2026-10-09','through':'2026-10-09'}
    assert record['ranges']['bear']['days']==0 and record['bear']=={}
    assert record['winProbability']['available'] is False
    assert 'fees excluded' in record['winProbability']['target']

def test_api_uses_same_audit_and_summary_preserves_quote_time():
    import api_server
    from momx.option_track import OptionTrackBook
    days=[{'day':'2026-10-09','trades':[trade()]}]
    assert api_server._rule_record_side(days)==summarize(days)
    assert 'exitPriceAt' in OptionTrackBook.SUMMARY_KEYS

def test_closing_time_records_exit_stamp_without_erasing_earlier_signal(tmp_path):
    from momx.option_track import OptionTrackBook
    book=OptionTrackBook(directory=tmp_path)
    book._day='2026-10-09'
    for prior in (None,'2026-10-09T11:00:00-04:00'):
        t={'symbol':'TEST','entry':1,'expiry':'2026-10-09','best':1,'exitAt':prior}
        book._close_trade(t,.5,.4,.6,'2026-10-09T15:50:00-04:00',0)
        assert t['exitAt']==(prior or '2026-10-09T15:50:00-04:00')
        assert t['exitPriceAt']=='2026-10-09T15:50:00-04:00'


def test_strategy_exit_quotes_bypass_chain_backlog_and_preserve_losses(tmp_path):
    from momx.option_track import OptionTrackBook
    now=datetime.fromisoformat('2026-10-09T10:00:30-04:00').timestamp()
    calls=[]
    def quotes(contracts):
        calls.append(contracts)
        return {'quotes': {'MAIN': {'bid':.4,'ask':.6}, 'DELTA': {'bid':0,'ask':0}, 'ZERO': {'bid':.1,'ask':.3}}}
    book=OptionTrackBook(tmp_path, clock=lambda:now, fetch=lambda _:None, quotes_fetch=quotes, background=False)
    book._day='2026-10-09'
    t=trade(status='open',contract='MAIN',exitPrice=None,eContract='DELTA',eStatus='open',zContract='ZERO',zStatus='open')
    book._trades=[t]
    book._fast_price(['TEST'])
    assert calls==[['DELTA','MAIN','ZERO']]
    assert t['exitPrice']==.5 and t['eExit']==0 and t['zExit']==.2
    assert t['exitPriceAt']=='2026-10-09T10:00:30-04:00'
    assert t['exitAt']=='2026-10-09T10:00:00-04:00'
    book._fast_price(['TEST'])
    assert len(calls)==1  # A frozen strategy exit cannot be replaced by a later quote.


def test_strategy_exit_failed_quote_retry_and_day_rollover(tmp_path):
    from momx.option_track import OptionTrackBook
    now=datetime.fromisoformat('2026-10-09T10:00:30-04:00').timestamp()
    book=OptionTrackBook(tmp_path, clock=lambda:now, fetch=lambda _:None, quotes_fetch=lambda _:None, background=False)
    book._day='2026-10-09'
    t=trade(status='open',exitPrice=None)
    book._trades=[t]
    book._fast_price(['TEST'])
    assert t['exitPrice'] is None and book._fast_exit_next['TEST']==now+30
    def rollover(_):
        book._day='2026-10-12'
        return {'quotes':{'TEST_CALL':{'bid':.4,'ask':.6}}}
    book._quotes_fetch=rollover
    book._fast_price(['TEST'])
    assert t['exitPrice'] is None
