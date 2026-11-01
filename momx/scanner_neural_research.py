"""Train a separate Level/Setup neural challenger on audited net outcomes.

No active model writes or trading authority. Offline results never authorize a
Record win probability; a later paper trial is still required.
"""
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

import numpy as np
from momx import ai_filter as ai
from momx import rule_record, scanner_learning as sl


def collect(roots, before_day):
    days, excluded, seen = {}, Counter(), set()
    for side, root in roots.items():
        for path in sorted(Path(root).glob('20*.json')):
            if path.stem >= before_day:
                continue
            for t in json.loads(path.read_text(encoding='utf-8')).get('trades', []):
                reason = rule_record.exclusion(t)
                if reason:
                    excluded[reason] += 1
                    continue
                exited, priced = (rule_record._time(t[k]) for k in ('exitAt', 'exitPriceAt'))
                if (max(exited, priced).date().isoformat() >= before_day
                        or max(exited, priced) > datetime.now(ai.ET)):
                    excluded['outcome_after_cutoff'] += 1
                    continue
                if sl.snapshot(t, side) is None or sl.features(t, side)['confirmLevel'] is None:
                    excluded['missing_frozen_level_setup'] += 1
                    continue
                inputs = sl.features(t, side)
                if inputs['confirmLevel'] not in (1, 2, 3) or inputs['setupCount'] < 3:
                    excluded['outside_level_strategy'] += 1
                    continue
                # Nested Level strategies may record the same economic outcome.
                key = (side, t['symbol'], t['contract'], t['at'], t['entryAt'])
                if key in seen:
                    excluded['duplicate_economic_outcome'] += 1
                    continue
                seen.add(key)
                net = (t['exitPrice']-t['entry'])*100
                days.setdefault(path.stem, []).append({'features': ai.features(t, side),
                    'net': net, 'win': int(net > 0), 'cost': t['entry']*100,
                    'exitAt': t['exitAt']})
    return dict(sorted(days.items())), dict(excluded)


def train(directory=None, roots=None, before_day=None):
    directory = Path(directory or ai._dir()/'scanner-learning')
    before_day = before_day or datetime.now(ai.ET).date().isoformat()
    days, excluded = collect(roots or ai._roots(), before_day)
    names = sorted(days)
    a, b = int(len(names)*.6), int(len(names)*.8)
    train_days, calibration_days, test_days = names[:a], names[a:b], names[b:]
    def rows(selected, before=None):
        return [r for day in selected for r in days[day]
                if before is None or rule_record._time(r['exitAt']).date().isoformat() < before]
    training = rows(train_days, calibration_days[0] if calibration_days else before_day)
    calibration = rows(calibration_days, test_days[0] if test_days else before_day)
    testing = rows(test_days)
    report = {'version': 1, 'builtAt': datetime.now(ai.ET).isoformat(), 'state': 'collecting',
        'target': 'Positive one-contract Strategy-exit midpoint P/L before fees (fees excluded)',
        'model': 'Neural network (32,16), with separate logistic calibration',
        'setupRuleCount': 23, 'inputs': list(sl.SCANNER_NUMERIC) + list(ai.chart_learning.NUMERIC), 'excluded': excluded,
        'trainDays': train_days, 'calibrationDays': calibration_days, 'testDays': test_days,
        'trainingTrades': len(training), 'calibrationTrades': len(calibration), 'testTrades': len(testing),
        'chartContextTrades': sum(any(r['features'].get(k) is not None for k in ai.chart_learning.NUMERIC) for block in (training, calibration, testing) for r in block),
        'probabilityValidated': False, 'automaticTrading': False,
        'reason': 'Collecting frozen Level/Setup inputs with audited quotes; no fabricated historical inputs',
        'requirements': {'trainingTrades': 200, 'calibrationTrades': 100, 'testTrades': 100, 'testDays': 5}}
    if (len(training) >= 200 and len(calibration) >= 100 and len(testing) >= 100 and len(test_days) >= 5
            and all(len({r['win'] for r in block}) == 2 for block in (training, calibration, testing))):
        numeric = tuple(k for k in ai.NUMERIC if not k.startswith('j_') and k not in ('jevP', 'jevMissing'))
        enc = ai.Model(numeric).prepare([r['features'] for r in training])
        net = ai.nn_fit(enc._matrix([r['features'] for r in training]), np.array([r['win'] for r in training]),
                        hidden=(32,16), epochs=40, seed=42)
        model = ai.NeuralModel(enc, net)
        def calibration_inputs(block):
            probabilities = np.clip(net(enc._matrix([r['features'] for r in block])), 1e-6, 1-1e-6)
            return [{'logit': float(np.log(p/(1-p)))} for p in probabilities]
        values = calibration_inputs(calibration)
        calibrator = ai.Model(('logit',)).prepare(values)
        calibrator.fit_matrix(calibrator._matrix(values), [r['win'] for r in calibration])
        predicted = np.asarray(calibrator.predict_many(calibration_inputs(testing)))
        labels = np.array([r['win'] for r in testing])
        bins = [(predicted >= lo) & (predicted < lo+.1 if lo < .9 else predicted <= 1) for lo in np.arange(0,1,.1)]
        ece = sum(float(mask.mean()) * abs(float(predicted[mask].mean()-labels[mask].mean())) for mask in bins if mask.any())
        baseline = float(np.mean([r['win'] for r in training]))
        report.update(state='research_only', reason='Offline neural evaluation complete; future paper validation is still required',
            test={'trades': len(testing), 'wins': int(labels.sum()), 'losses': int(len(labels)-labels.sum()),
                  'brier': float(np.mean((predicted-labels)**2)), 'baselineBrier': float(np.mean((baseline-labels)**2)),
                  'calibrationError': ece, 'auc': ai._auc(list(predicted), list(labels)),
                  'grossPnl': round(sum(r['net'] for r in testing), 2)})
        report['baselineRate'] = baseline
        directory.mkdir(parents=True, exist_ok=True)
        candidate = {'model':model.to_json(), 'calibrator':calibrator.to_json(),
                     'target':report['target'], 'probabilityValidated':False, 'trainedThrough':train_days[-1]}
        ai._write_atomic(directory/'neural-candidate.json', json.dumps(candidate))
        # Calibration on unseen data is necessary before collecting a frozen
        # prospective trial. It is not itself permission to publish probability.
        report['offlineQualified'] = ece <= .1 and report['test']['brier'] < report['test']['baselineBrier']
        if report['offlineQualified']:
            from momx.scanner_probability import freeze
            freeze(directory, candidate, report)
    directory.mkdir(parents=True, exist_ok=True)
    from momx.scanner_probability import validate_forward
    report['forward'] = validate_forward(directory, roots or ai._roots(), before_day)
    report['probabilityValidated'] = report['forward'].get('probabilityValidated', False)
    ai._write_atomic(directory/'report.json', json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    print(json.dumps(train(), indent=2))
