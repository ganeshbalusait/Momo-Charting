import json
from datetime import datetime, timedelta
from types import SimpleNamespace
from momx import scanner_probability as sp
from momx.ai_filter import ET
from test_momx_scanner_neural_research import audited


def trial(tmp_path, mutate=None):
    directory=tmp_path/'research'; directory.mkdir()
    root=tmp_path/'raw'; root.mkdir()
    frozen={'modelId':'frozen-model-id-123','builtAt':'2026-08-30T10:00:00-04:00',
            'threshold':.55,'baselineRate':.5,'targetVersion':sp.TARGET}
    (directory/'forward-model.json').write_text(json.dumps(frozen))
    for n in range(10):
        day=datetime(2026,9,1,10,tzinfo=ET)+timedelta(days=n)
        rows=[]
        for i in range(10):
            at=day+timedelta(minutes=i)
            t=audited(); win=i<5
            t.update(symbol='X'+str(i),contract='X'+str(i),at=at.isoformat(),entryAt=at.isoformat(),
                exitAt=(at+timedelta(hours=1)).isoformat(),exitPriceAt=(at+timedelta(hours=1)).isoformat(),
                exitPrice=1.5 if win else .5,exitBid=1.4 if win else .4,exitAsk=1.6 if win else .6)
            t['scannerInputs']['publicationAt']=(at-timedelta(seconds=15)).isoformat()
            t['aiDecision']={'scannerForecast':{'available':True,'phase':'before_entry','modelId':frozen['modelId'],
                'targetVersion':sp.TARGET,'scoredAt':at.isoformat(),'probability':.95 if win else .05,
                'contract':t['contract'],'scannerInputs':t['scannerInputs']}}
            if mutate: mutate(t,n,i)
            rows.append(t)
        (root/(day.date().isoformat()+'.json')).write_text(json.dumps({'trades':rows}))
    return directory,{'bull':root}


def test_only_prospective_calibrated_profitable_fixed_window_validates(tmp_path):
    directory,roots=trial(tmp_path)
    report=sp.validate_forward(directory,roots,'2026-10-01')
    assert report['state']=='validated' and report['trades']==100 and report['observedDays']==10
    assert report['evaluation']['candidateWinRate'] > report['evaluation']['baselineWinRate']
    assert report['calibrationError'] < .1 and report['evaluation']['candidateNet'] > 0
    repeated=sp.validate_forward(directory,roots,'2026-10-01')
    assert repeated['validatedAt']==report['validatedAt']  # never reuse that window as new evidence


def test_post_outcome_forecast_and_missing_quotes_cannot_validate(tmp_path):
    def late(t,n,i):
        if n==0 and i==0:t['aiDecision']['scannerForecast']['scoredAt']=t['exitAt']
    directory,roots=trial(tmp_path,late)
    report=sp.validate_forward(directory,roots,'2026-10-01')
    assert not report['probabilityValidated'] and report['unpriced']==1


def test_fewer_days_or_bad_calibration_does_not_publish_probability(tmp_path):
    directory,roots=trial(tmp_path,lambda t,n,i:t['aiDecision']['scannerForecast'].update(probability=.6 if i<5 else .4))
    short=sp.validate_forward(directory,roots,'2026-09-08')
    assert short['state']=='forward' and not short['probabilityValidated']
    complete=sp.validate_forward(directory,roots,'2026-10-01')
    assert complete['state']=='rejected' and not complete['probabilityValidated']


def test_entry_forecast_is_cached_neutral_and_not_relabelled_as_validated(tmp_path,monkeypatch):
    forecaster=sp.Forecaster(tmp_path)
    t=audited()
    forecaster.model=SimpleNamespace(predict=lambda values:.8)
    forecaster.calibrator=SimpleNamespace(predict=lambda values:.62)
    forecaster.frozen={'modelId':'unit-frozen-model','builtAt':'2026-10-29T10:00:00-04:00'}
    at=datetime.fromisoformat(t['entryAt'])
    result=forecaster.predict('bull',t,at.timestamp())
    assert result['available'] and result['probability']==.62 and not result['validated']
    assert result['phase']=='before_entry' and 'entry' not in result
    forecaster.frozen['builtAt']=(at+timedelta(seconds=1)).isoformat()
    assert not forecaster.predict('bull',t,at.timestamp())['available']
    assert not forecaster.predict('bear',t,at.timestamp())['available']


def test_later_bad_monitoring_withdraws_a_previously_validated_probability(tmp_path):
    directory,roots=trial(tmp_path)
    assert sp.validate_forward(directory,roots,'2026-10-01')['state']=='validated'
    root=roots['bull']
    for n in range(10):
        source=json.loads((root/((datetime(2026,9,1)+timedelta(days=n)).date().isoformat()+'.json')).read_text())
        for t in source['trades']:
            for key in ('at','entryAt','exitAt','exitPriceAt'):
                t[key]=(datetime.fromisoformat(t[key])+timedelta(days=10)).isoformat()
            t['scannerInputs']['publicationAt']=(datetime.fromisoformat(t['at'])-timedelta(seconds=15)).isoformat()
            t.update(exitPrice=.5,exitBid=.4,exitAsk=.6)
            f=t['aiDecision']['scannerForecast']
            f.update(scoredAt=t['entryAt'],probability=.95,scannerInputs=t['scannerInputs'])
        day=(datetime(2026,9,11)+timedelta(days=n)).date().isoformat()
        (root/(day+'.json')).write_text(json.dumps(source))
    monitored=sp.validate_forward(directory,roots,'2026-10-01')
    assert monitored['state']=='withdrawn' and not monitored['probabilityValidated']
