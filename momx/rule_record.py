"""Auditable virtual Strategy-exit history, never broker-fill claims.

Missing/late prices and open marks cannot enter realized win-rate statistics.
Each rule keeps separate coverage counts, including losses and exclusions.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

ET = ZoneInfo('America/New_York')
ROUNDTRIP_FEE = 0.0  # User requested fee-free BOT and scanner calculations.
MAX_ENTRY_DELAY = 600
MAX_EXIT_DELAY = 120


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _time(value):
    try:
        at = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return at.astimezone(ET) if at.tzinfo else None
    except (TypeError, ValueError):
        return None


def exclusion(trade):
    if trade.get('badQuote'):
        return 'bad_quote'
    entry, exit_ = trade.get('entry'), trade.get('exitPrice')
    if not _num(entry) or entry <= 0:
        return 'missing_entry'
    if trade.get('status') != 'closed':
        return 'open' if trade.get('status') == 'open' else 'not_closed'
    if not _num(exit_) or exit_ < 0:
        return 'missing_strategy_exit'
    signal, entered, exited, priced = [_time(trade.get(k)) for k in ('at', 'entryAt', 'exitAt', 'exitPriceAt')]
    if not all((signal, entered, exited, priced)):
        return 'missing_timestamps'
    if not 0 <= (entered - signal).total_seconds() <= MAX_ENTRY_DELAY:
        return 'late_entry'
    if exited < entered or priced < exited:
        return 'invalid_time_order'
    if (priced - exited).total_seconds() > MAX_EXIT_DELAY:
        return 'late_exit_quote'
    for price, bid_key, ask_key in ((entry, 'entryBid', 'entryAsk'), (exit_, 'exitBid', 'exitAsk')):
        bid, ask = trade.get(bid_key), trade.get(ask_key)
        if not _num(bid) or not _num(ask):
            return 'missing_bid_ask'
        if bid < 0 or ask < bid or abs(price - (bid + ask) / 2) > .005001:
            return 'invalid_midpoint'
    if not trade.get('symbol') or not trade.get('contract') or not trade.get('rule'):
        return 'missing_identity'
    return None


def summarize(days_list):
    acc, seen = {}, set()
    for day in sorted(days_list, key=lambda d: d.get('day', '')):
        for trade in day.get('trades') or []:
            if not isinstance(trade, dict) or not trade.get('rule') or trade['rule'] == 'jev':
                continue
            rule = str(trade['rule'])
            rules = [rule]
            signal = _time(trade.get('at'))
            if rule == 'dayLine' and signal and 570 <= signal.hour * 60 + signal.minute < 600:
                rules.append('dayLineEarly')
            key = (rule, trade.get('symbol'), trade.get('contract'), trade.get('at'))
            reason = 'duplicate' if key in seen else exclusion(trade)
            seen.add(key)
            for group in rules:
                a = acc.setdefault(group, {'outcomes': [], 'excluded': Counter(), 'seen': 0})
                a['seen'] += 1
                if reason:
                    a['excluded'][reason] += 1
                    continue
                gross = (trade['exitPrice'] - trade['entry']) * 100
                a['outcomes'].append({'day': day['day'], 'entryAt': trade['entryAt'],
                    'exitAt': trade['exitPriceAt'], 'cost': trade['entry'] * 100,
                    'gross': gross, 'net': gross - ROUNDTRIP_FEE})
    result = {}
    for rule, a in acc.items():
        rows = sorted(a['outcomes'], key=lambda t: _time(t['exitAt']))
        n = len(rows)
        wins = [r['net'] for r in rows if r['net'] > 1e-8]
        losses = [r['net'] for r in rows if r['net'] < -1e-8]
        cost, net = sum(r['cost'] for r in rows), sum(r['net'] for r in rows)
        gross = sum(r['gross'] for r in rows)
        curve = high = drawdown = 0.
        for r in rows:
            curve += r['net']
            high = max(high, curve)
            drawdown = max(drawdown, high - curve)
        dates = sorted({r['day'] for r in rows})
        result[rule] = {'n': n, 'wins': len(wins), 'losses': len(losses),
            'breakEven': n - len(wins) - len(losses),
            'winRate': round(len(wins) / n * 100, 1) if n else None,
            'roi': round(net / cost * 100, 1) if cost else None,
            'grossPnl': round(gross, 2), 'netPnl': round(net, 2),
            'estimatedFees': round(n * ROUNDTRIP_FEE, 2), 'capitalSum': round(cost, 2),
            'avgNet': round(net / n, 2) if n else None,
            'avgWin': round(sum(wins) / len(wins), 2) if wins else None,
            'avgLoss': round(sum(losses) / len(losses), 2) if losses else None,
            'profitFactor': round(sum(wins) / -sum(losses), 3) if losses else None,
            'maxClosedDrawdown': round(drawdown, 2), 'days': len(dates),
            'from': dates[0] if dates else None, 'through': dates[-1] if dates else None,
            'seen': a['seen'], 'excluded': dict(a['excluded']),
            'excludedTotal': sum(a['excluded'].values()),
            'coveragePct': round(100 * n / a['seen'], 1) if a['seen'] else 0}
    return result


def build_record(bull, bear, requested_days=10, now=None):
    now = (now or datetime.now(ET)).astimezone(ET)
    today = now.date().isoformat()
    sides, ranges = {}, {}
    for side, history in (('bull', bull), ('bear', bear)):
        # Only completed prior sessions; a current partial day cannot inflate the record.
        done = sorted([d for d in history if d.get('day', '') < today and d.get('trades')], key=lambda d: d['day'])[-requested_days:]
        sides[side] = summarize(done)
        dates = [d['day'] for d in done]
        ranges[side] = {'days': len(dates), 'from': dates[0] if dates else None, 'through': dates[-1] if dates else None}
    return {'version': 2, 'days': requested_days, 'tradingDays': ranges['bull']['days'],
        'asOf': now.timestamp(), 'builtAt': now.isoformat(), 'ranges': ranges,
        'prices': 'mid', 'basis': 'Virtual main-option Strategy exit; recorded bid/ask midpoints, not broker fills',
        'roundTripFeeEstimate': ROUNDTRIP_FEE, 'minTrades': 20,
        'winProbability': {'available': False, 'status': 'not_validated',
            'target': 'Main option positive Strategy-exit P/L before fees; fees excluded',
            'reason': 'No model has passed calibration testing for this net-profit target on representative quote-checked trades. Local scores for earlier trades are not a current-setup win probability.'},
        'maxEntryDelaySeconds': MAX_ENTRY_DELAY, 'maxExitQuoteDelaySeconds': MAX_EXIT_DELAY,
        **sides}
