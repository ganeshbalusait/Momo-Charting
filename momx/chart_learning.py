"""Frozen completed-5m chart context, never reconstructed from trade outcomes."""
import copy
import math
from datetime import datetime
from zoneinfo import ZoneInfo
from momx.indicators import ema

ET = ZoneInfo('America/New_York')
LEVELS = ('vwap', 'ema9', 'ema21', 'ema50', 'ema200', 'prevHigh', 'prevLow',
          'premarketHigh', 'premarketLow', 'P', 'R1', 'R2', 'S1', 'S2')
NUMERIC = tuple('chartDistance_' + key for key in LEVELS)


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def build(bars, now):
    """Use the existing EMA seed and session VWAP; classical prior-RTH pivots.

    All prices refer to completed 5m bars, not the user's developing chart.
    Partial historical sessions are not treated as complete daily levels.
    """
    try:
        ordered = {}
        for bar in bars:
            stamp = int(bar['time'])
            if stamp + 300 > now:
                continue
            if stamp in ordered and ordered[stamp] != bar:
                return None
            if not all(number(bar.get(k)) and bar[k] > 0 for k in ('high', 'low', 'close')):
                return None
            ordered[stamp] = bar
        if not ordered:
            return None
        stamps = sorted(ordered)
        end = stamps[-1] + 300
        if not 0 <= now - end <= 420:
            return None
        closes = [ordered[t]['close'] for t in stamps]
        values = dict.fromkeys(LEVELS)
        from momx.momentum import _session_vwap
        vwap = _session_vwap([ordered[t] for t in stamps])
        values['vwap'] = vwap if number(vwap) and vwap > 0 else None
        for length in (9, 21, 50, 200):
            if len(closes) >= length:
                values['ema' + str(length)] = ema(closes, length)[-1]
        today = datetime.fromtimestamp(stamps[-1], ET).date()
        prior, pre = {}, []
        for t in stamps:
            dt = datetime.fromtimestamp(t, ET)
            minute = dt.hour * 60 + dt.minute
            if dt.date() < today and 570 <= minute < 960:
                prior.setdefault(dt.date(), []).append((minute, ordered[t]))
            if dt.date() == today and 240 <= minute < 570:
                pre.append(ordered[t])
        if pre:
            values.update(premarketHigh=max(b['high'] for b in pre), premarketLow=min(b['low'] for b in pre))
        if prior:
            session = prior[max(prior)]
            if {m for m, _ in session} == set(range(570, 960, 5)):
                high, low, close = max(b['high'] for _, b in session), min(b['low'] for _, b in session), session[-1][1]['close']
                pivot = (high + low + close) / 3
                values.update(prevHigh=high, prevLow=low, P=pivot,
                              R1=2*pivot-low, R2=pivot+high-low,
                              S1=2*pivot-high, S2=pivot-high+low)
        return {'version': 1, 'timeframe': '5m', 'barAt': stamps[-1], 'availableAt': end,
                'capturedAt': int(now), 'referencePrice': closes[-1], 'levels': values,
                'basis': 'Completed 5m bars; regular-session VWAP; prior regular-session classical pivots; observed premarket range'}
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def snapshot(trade):
    value = (trade.get('setup') or {}).get('chartContext') or trade.get('chartContext')
    try:
        dt = datetime.fromisoformat(str(trade['at']).replace('Z', '+00:00'))
        if dt.tzinfo is None or not isinstance(value, dict):
            return None
        at = dt.timestamp()
        if value.get('symbol') != trade.get('symbol') or not trade.get('symbol'):
            return None
        if value.get('version') != 1 or value.get('timeframe') != '5m':
            return None
        end, captured = value['availableAt'], value['capturedAt']
        if not number(value.get('barAt')) or end != value['barAt'] + 300:
            return None
        if not number(end) or not number(captured) or not end <= captured <= at or not 0 <= at-end <= 420:
            return None
        if not number(value['referencePrice']) or value['referencePrice'] <= 0 or not isinstance(value['levels'], dict):
            return None
        return copy.deepcopy(value)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def features(trade):
    out = dict.fromkeys(NUMERIC)
    value = snapshot(trade)
    if value:
        for key in LEVELS:
            level = value['levels'].get(key)
            if number(level) and level > 0:
                out['chartDistance_' + key] = (value['referencePrice'] / level - 1) * 100
    return out
