from momx import momentum
from momx.indicators import macd


def tape():
    start = 1789992000 // 1800 * 1800
    return [dict(time=start+i*300,open=100+i*i*.01,high=101+i*i*.01,
                 low=99+i*i*.01,close=100+i*i*.01,volume=1000) for i in range(180)]


def test_macd_matches_source_and_completed_candle_only():
    bars=tape()
    at=bars[-1]['time']+300
    s=momentum.summarize(bars,None,now_epoch=at)['goConfirmation']
    line,signal=macd([b['close'] for b in bars],12,26,9)
    assert s['macdUp'] == (line[-1]>signal[-1] and line[-1]-signal[-1]>line[-2]-signal[-2])
    assert s['buyers30'] is True
    forming=dict(bars[-1],time=at,close=1,low=1)
    assert momentum.summarize(bars+[forming],None,now_epoch=at+10)['goConfirmation']==s
    assert momentum.summarize(bars,None,now_epoch=at,direction='bear')['goConfirmation'] is None


def test_forming_30m_bucket_cannot_change_buyers():
    bars=tape()
    expected=momentum._go_confirmation(bars)['buyers30']
    more=bars+[dict(bars[-1],time=bars[-1]['time']+300,high=10000,low=1,close=1)]
    assert momentum._go_confirmation(more)['buyers30']==expected
    assert momentum._go_confirmation(bars[:30]) is None
