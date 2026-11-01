import copy
import json
from datetime import datetime, timedelta
import pytest
from momx import chart_learning as chart, momentum, ai_filter
from momx.option_track import setup_snapshot
from momx.paper_learning import evidence


def tape():
    start = datetime.fromisoformat('2026-10-29T04:00:00-04:00')
    bars = []
    for day in range(2):
        for i in range(144 if day == 0 else 72):
            price = 100 + (len(bars) % 20) / 10
            bars.append(dict(time=int((start+timedelta(days=day, minutes=i*5)).timestamp()),
                             open=price, high=price+1, low=price-1, close=price, volume=100))
    return bars


def trade():
    bars = tape()
    now = bars[-1]['time'] + 300
    m5 = momentum.summarize(bars, {}, now_epoch=now)
    at = datetime.fromtimestamp(now, chart.ET)
    row = {'symbol': 'AMZN', 'm5': m5, 'last': bars[-1]['close']}
    return {'at': at.isoformat(), 'symbol': 'AMZN', 'setup': setup_snapshot(row, at)}, row


def test_full_chart_capture_formulas_training_and_agent_evidence():
    t, row = trade()
    context = chart.snapshot(t)
    assert set(context['levels']) == set(chart.LEVELS)
    assert all(v is not None for v in context['levels'].values())
    levels = context['levels']
    assert levels['vwap'] == pytest.approx(momentum._session_vwap(tape()))
    assert levels['ema21'] == pytest.approx(momentum.ema([b['close'] for b in tape()], 21)[-1])
    assert levels['R1'] == pytest.approx(2*levels['P']-levels['prevLow'])
    assert levels['S2'] == pytest.approx(levels['P']-levels['prevHigh']+levels['prevLow'])
    f = ai_filter.features(t, 'bull')
    assert f['chartDistance_vwap'] == pytest.approx((context['referencePrice']/levels['vwap']-1)*100)
    assert set(chart.NUMERIC) <= set(ai_filter.NUMERIC)
    row['m5']['chartContext']['levels']['vwap'] = 999
    assert chart.snapshot(t) == context  # frozen, not a reference to the live row
    t.update(status='closed', entry=1, exitPrice=2, best=999)
    assert evidence('bull', t)['chartContextAtSignal'] == context
    assert ai_filter.features(t, 'bull') == f
    assert chart.snapshot(json.loads(json.dumps(t))) == context


@pytest.mark.parametrize('change', ['future', 'stale', 'ticker', 'missing', 'naive'])
def test_invalid_context_stays_unknown(change):
    t, _ = trade()
    if change == 'future': t['setup']['chartContext']['capturedAt'] += 1
    if change == 'stale': t['at'] = (datetime.fromisoformat(t['at'])+timedelta(seconds=421)).isoformat()
    if change == 'ticker': t['symbol'] = 'REGN'
    if change == 'missing': t['setup'] = {}
    if change == 'naive': t['at'] = '2026-10-30T10:00:00'
    assert all(v is None for v in chart.features(t).values())


def test_no_future_bars_duplicate_conflicts_or_partial_session_levels():
    bars = tape(); now = bars[-1]['time'] + 300
    baseline = chart.build(bars, now)
    future = dict(bars[-1], time=now, close=999)
    assert chart.build(bars+[future], now) == baseline
    assert chart.build(bars+[bars[-1]], now) == baseline
    assert chart.build(bars+[dict(bars[-1], close=999)], now) is None
    partial = chart.build(bars[-20:], now)
    assert partial['levels']['ema200'] is None
    assert partial['levels']['prevHigh'] is None
    assert chart.build(bars, now+421) is None


def test_paper_mirror_preserves_chart_context_without_live_orders(tmp_path):
    from test_momx_paper_mirror import FakeClient, Book, NOW, _trade
    from momx.paper_mirror import PaperMirror
    t = _trade()
    at = datetime.fromisoformat(t['at']).timestamp()
    context = {'version': 1, 'timeframe': '5m', 'symbol': t['symbol'],
               'barAt': at-300, 'availableAt': at, 'capturedAt': at,
               'referencePrice': 100, 'levels': {'vwap': 99}}
    t['setup'] = {'chartContext': context}
    mirror = PaperMirror({'bull': Book([t])}, tmp_path, client_factory=FakeClient, off=lambda: False)
    mirror.tick(NOW)
    saved = mirror.status()['rows'][0]
    assert chart.snapshot(saved) == context
    context['levels']['vwap'] = 500
    assert chart.snapshot(saved)['levels']['vwap'] == 99
