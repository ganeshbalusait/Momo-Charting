"""Frozen scanner inputs for learning. Never reconstruct old levels from outcomes."""
from datetime import datetime
from collections.abc import Mapping

SETUP_RULE_KEYS = ('tagBolt', 'go', 'goRvolMacd', 'mxaplus', 'tagStar1', 'tagSector',
                  'dayLine', 'dayLine100', 'dayReclaim', 'tagZs', 'tagAbvx', 'tagMomStrong',
                  'tagFlip', 'tagRvol', 'tagSkit', 'tagSqz', 'tagChartSqz', 'tagAdx',
                  'chart', 'chartShort', 'tagWarn', 'tagExplosive', 'tagSteady')
SCANNER_NUMERIC = ('confirmLevel', 'setupCount', 'rvolIncreasingCount', 'confirmSqz',
                   'confirmFire', 'confirmSkittles', *("setup_" + k for k in SETUP_RULE_KEYS))


def snapshot(trade, side):
    value = trade.get('scannerInputs') or trade.get('levelSignal')
    if side != 'bull' or not isinstance(value, Mapping) or value.get('version') != 1:
        return None
    try:
        published = datetime.fromisoformat(str(value['publicationAt']).replace('Z', '+00:00'))
        signal = datetime.fromisoformat(str(trade['at']).replace('Z', '+00:00'))
        if published.tzinfo is None or signal.tzinfo is None or not 0 <= (signal-published).total_seconds() <= 120:
            return None
    except (ValueError, TypeError, KeyError):
        return None
    level, types = value.get('highestLevel'), value.get('setupTypes')
    if type(level) is not int or level not in (0, 1, 2, 3) or not isinstance(types, list):
        return None
    keys = [t.get('key') if isinstance(t, Mapping) else None for t in types]
    if any(k not in SETUP_RULE_KEYS for k in keys) or len(set(keys)) != len(keys) or value.get('setupCount') != len(keys):
        return None
    return {k: value[k] for k in ('version', 'highestLevel', 'setupTypes', 'setupCount',
            'publicationAt', 'historyReady', 'increasing', 'sqz', 'fire', 'skittles') if k in value}


def features(trade, side):
    out = dict.fromkeys(SCANNER_NUMERIC)
    value = snapshot(trade, side)
    if value is None:
        return out
    keys = {t['key'] for t in value['setupTypes']}
    out.update({'setup_' + key: float(key in keys) for key in SETUP_RULE_KEYS})
    out['setupCount'] = len(keys)
    # First observations are unknown, not negative examples of Level 1.
    if value.get('historyReady') is True or value['highestLevel'] > 0:
        out['confirmLevel'] = value['highestLevel']
        increasing = value.get('increasing')
        if isinstance(increasing, list):
            out['rvolIncreasingCount'] = len(set(increasing))
    # scannerConfirmation evaluates confirmations only once RVOL reaches L1.
    if value['highestLevel'] > 0:
        for source, target in [('sqz', 'confirmSqz'), ('fire', 'confirmFire'), ('skittles', 'confirmSkittles')]:
            if type(value.get(source)) is bool:
                out[target] = float(value[source])
    return out


def coverage(days):
    total = captured = 0
    levels = {str(i): 0 for i in range(4)}
    rules = dict.fromkeys(SETUP_RULE_KEYS, 0)
    for items in days.values():
        for side, trade in items:
            total += 1
            values = trade.get('_features') or features(trade, side)
            if values.get('setupCount') is None:
                continue
            captured += 1
            level = values.get('confirmLevel')
            if level in (0, 1, 2, 3):
                levels[str(int(level))] += 1
            for key in rules:
                rules[key] += values.get('setup_' + key) == 1
    return {'version': 1, 'trades': total, 'captured': captured, 'missing': total-captured,
            'levels': levels, 'setupRules': rules, 'probabilityValidated': False,
            'note': 'Frozen signal-time Level and Setup inputs only; missing history remains unknown. Scores are not calibrated net-profit probabilities.'}
