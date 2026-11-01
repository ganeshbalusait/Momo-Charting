"""Prospective, frozen-model probability research; never an order gate."""
from datetime import datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

from momx import rule_record, scanner_learning, chart_learning

TARGET = 'virtual_strategy_exit_gross_chart_v3'


def moment(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt if dt.tzinfo else None
    except (ValueError, TypeError):
        return None


def freeze(directory, candidate, report):
    """Freeze one research model until its fixed prospective trial ends."""
    from momx.ai_filter import _write_atomic
    directory = Path(directory)
    path = directory/'forward-model.json'
    try:
        previous = json.loads(path.read_text(encoding='utf-8'))
        proof = json.loads((directory/'forward-validation.json').read_text(encoding='utf-8'))
        if previous.get('targetVersion') == TARGET and (proof.get('modelId') != previous.get('modelId') or proof.get('state') not in ('rejected', 'withdrawn')):
            return previous
    except FileNotFoundError:
        if path.exists():
            previous = json.loads(path.read_text(encoding='utf-8'))
            if previous.get('targetVersion') == TARGET:
                return previous
    identity = hashlib.sha256(json.dumps(candidate, sort_keys=True).encode()).hexdigest()[:24]
    if path.exists() and previous.get('targetVersion') == TARGET and previous.get('modelId') == identity:
        return previous
    frozen = {**candidate, 'modelId':identity, 'targetVersion':TARGET, 'builtAt':report['builtAt'],
              'trainedThrough':report['trainDays'][-1], 'threshold':.55, 'baselineRate':report['baselineRate']}
    _write_atomic(path, json.dumps(frozen))
    return frozen


class Forecaster:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.model = self.calibrator = None
        self.frozen = self.proof = {}
        self.signature = None

    def refresh(self):
        """Disk/model loading happens on the scorer thread, never on entry."""
        from momx.ai_filter import NeuralModel, Model
        try:
            path = self.directory/'forward-model.json'
            signature = path.stat().st_mtime_ns
            if signature != self.signature:
                frozen = json.loads(path.read_text(encoding='utf-8'))
                if frozen.get('targetVersion') != TARGET:
                    raise ValueError('Wrong probability target')
                model = NeuralModel.from_json(frozen['model'])
                if not set(scanner_learning.SCANNER_NUMERIC + chart_learning.NUMERIC).issubset(model.enc.numeric):
                    raise ValueError('Missing Level/Setup/chart model inputs')
                self.model, self.calibrator = model, Model.from_json(frozen['calibrator'])
                self.frozen, self.signature = frozen, signature
            self.proof = json.loads((self.directory/'forward-validation.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError, TypeError):
            self.proof = {}

    def validated(self, model_id, now):
        tested = moment(self.proof.get('validatedAt'))
        through = self.proof.get('throughDay')
        return bool(self.model is not None and self.frozen.get('modelId') == model_id
                    and self.proof.get('modelId') == model_id and self.proof.get('state') == 'validated'
                    and self.proof.get('validatedTrades',0) >= 100 and self.proof.get('validatedDays',0) >= 10
                    and tested and 0 <= (now-tested).total_seconds() <= 14*86400
                    and isinstance(through,str) and (now-timedelta(days=14)).date().isoformat() <= through <= now.date().isoformat())

    def predict(self, side, trade, timestamp):
        """Cached, constant-sized local inference before entry; unavailable is neutral."""
        from momx import ai_filter as ai
        unavailable = {'available':False, 'reason':'Learning: frozen Level/Setup neural model not ready'}
        if self.model is None:
            return unavailable
        now = datetime.fromtimestamp(timestamp, ai.ET)
        built, signal = moment(self.frozen.get('builtAt')), moment(trade.get('at'))
        x = scanner_learning.features(trade, side)
        if (not built or not signal or not built <= signal <= now or x['confirmLevel'] not in (1,2,3)
                or x['setupCount'] < 3 or not trade.get('contract')):
            return unavailable
        e, b, a = (trade.get(k) for k in ('entry','entryBid','entryAsk'))
        if any(type(v) not in (int,float) or not math.isfinite(v) for v in (e,b,a)) or not e > 0 or not 0 <= b <= a or abs(e-(b+a)/2) > .005001:
            return unavailable
        try:
            p = min(1-1e-6, max(1e-6, float(self.model.predict(ai.features(trade, side)))))
            calibrated = float(self.calibrator.predict({'logit':math.log(p/(1-p))}))
            if not math.isfinite(calibrated) or not 0 <= calibrated <= 1:
                return unavailable
            return {'available':True, 'probability':calibrated, 'modelId':self.frozen['modelId'],
                    'targetVersion':TARGET, 'phase':'before_entry', 'scoredAt':now.isoformat(),
                    'contract':trade['contract'], 'scannerInputs':scanner_learning.snapshot(trade, side),
                    'validated':self.validated(self.frozen['modelId'],now)}
        except (ValueError, TypeError, KeyError, OverflowError):
            return unavailable


def validate_forward(directory, roots, before_day):
    """One fixed future cohort, then disjoint monitoring cohorts. No repeated peeking."""
    from momx.ai_filter import _write_atomic, ET
    from momx.paper_experiments import paired, qualified
    directory = Path(directory)
    try:
        frozen = json.loads((directory/'forward-model.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return {'state':'collecting', 'probabilityValidated':False}
    try:
        previous = json.loads((directory/'forward-validation.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    if frozen.get('targetVersion') != TARGET:
        return {'state':'collecting', 'probabilityValidated':False, 'reason':'Previous target requires retraining'}
    if previous.get('modelId') != frozen['modelId']:
        previous = {}
    if previous.get('state') in ('rejected','withdrawn'):
        return previous
    after = previous.get('monitorAfterDay', frozen['builtAt'][:10])
    records, seen = [], set()
    for side, root in roots.items():
        for path in sorted(Path(root).glob('20*.json'), reverse=True):
            for t in json.loads(path.read_text(encoding='utf-8')).get('trades',[]):
                f = (t.get('aiDecision') or {}).get('scannerForecast') or {}
                entered, predicted, exited = moment(t.get('entryAt')), moment(f.get('scoredAt')), moment(t.get('exitAt'))
                if (not entered or entered.astimezone(ET).date().isoformat() <= after
                        or entered.astimezone(ET).date().isoformat() >= before_day or f.get('modelId') != frozen['modelId']):
                    continue
                key = (side,t.get('symbol'),t.get('contract'),t.get('at'),t.get('entryAt'))
                if key in seen:
                    continue
                seen.add(key)
                day = entered.astimezone(ET).date().isoformat()
                p = f.get('probability')
                live_now = datetime.now(ET)
                valid = (f.get('phase') == 'before_entry' and f.get('targetVersion') == TARGET
                         and predicted and moment(frozen['builtAt']) <= predicted <= entered
                         and exited and entered <= exited <= live_now and predicted <= exited
                         and exited.astimezone(ET).date().isoformat() < before_day
                         and moment(t.get('exitPriceAt')) and moment(t['exitPriceAt']).astimezone(ET).date().isoformat() < before_day
                         and type(p) in (int,float) and math.isfinite(p) and 0 <= p <= 1
                         and rule_record.exclusion(t) is None and f.get('contract') == t.get('contract')
                         and scanner_learning.snapshot(t,side) == f.get('scannerInputs'))
                records.append((day, t, p, bool(valid)))
    days = sorted({r[0] for r in records})[:10]
    cohort = [r for r in records if r[0] in days]
    usable = [r for r in cohort if r[3]]
    result = {**previous, 'modelId':frozen['modelId'], 'state':previous.get('state','forward'),
              'observedDays':len(days), 'trades':len(usable), 'unpriced':len(cohort)-len(usable),
              'probabilityValidated':previous.get('state') == 'validated'}
    if len(days) >= 10 and len(usable) >= 100 and len(usable) == len(cohort):
        rows, probabilities, labels = [], [], []
        for day, t, p, _ in usable:
            gross = (t['exitPrice']-t['entry'])*100
            rows.append({'day':day,'outcomeDay':moment(t['exitAt']).astimezone(ET).date().isoformat(),
                         'gross':gross,'qty':1,'cost':t['entry']*100,'take':p >= frozen['threshold']})
            probabilities.append(p); labels.append(int(gross > 0))
        report = paired(rows, fee_per_contract=0)
        report.update(decisionCoverage=1, outcomeCoverage=1)
        brier = sum((p-y)**2 for p,y in zip(probabilities,labels))/len(labels)
        base = sum((frozen['baselineRate']-y)**2 for y in labels)/len(labels)
        ece = 0
        for i in range(10):
            idx = [n for n,p in enumerate(probabilities) if i/10 <= p < (i+1)/10 or i == 9 and p == 1]
            if idx:
                ece += abs(sum(probabilities[n]-labels[n] for n in idx))/len(labels)
        passed = qualified(report) and ece <= .1 and brier < base
        result.update(evaluation=report, brier=brier, baselineBrier=base, calibrationError=ece,
                      state='validated' if passed else 'withdrawn' if previous.get('state') == 'validated' else 'rejected',
                      probabilityValidated=passed)
        if passed:
            result.update(validatedAt=datetime.now(ET).isoformat(), monitorAfterDay=days[-1], throughDay=days[-1],
                          validatedTrades=len(usable), validatedDays=len(days))
    _write_atomic(directory/'forward-validation.json', json.dumps(result,indent=2))
    return result
