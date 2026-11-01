import json
from datetime import datetime, timedelta
from momx import scanner_neural_research as research
from test_momx_scanner_learning import trade


def audited():
    t = trade()
    return {**t, 'symbol':'AMZN', 'contract':'AMZN_CALL', 'rule':'level3', 'status':'closed',
        'entry':1, 'entryBid':.9, 'entryAsk':1.1, 'entryAt':t['at'],
        'exitPrice':1.01, 'exitBid':1., 'exitAsk':1.02,
        'exitAt':'2026-10-30T11:00:00-04:00', 'exitPriceAt':'2026-10-30T11:00:00-04:00'}


def test_audited_net_target_deduplicates_nested_levels_and_excludes_future_days(tmp_path):
    root=tmp_path/'raw'; root.mkdir()
    t=audited()
    (root/'2026-10-30.json').write_text(json.dumps({'trades':[t,{**t,'rule':'level1'}]}))
    (root/'2026-11-02.json').write_text(json.dumps({'trades':[t]}))
    days, excluded=research.collect({'bull':root},'2026-11-01')
    assert list(days)==['2026-10-30'] and len(days['2026-10-30'])==1
    assert days['2026-10-30'][0]['win']==1  # gross +$1 qualifies; fees are excluded
    assert excluded['duplicate_economic_outcome']==1


def test_missing_old_inputs_do_not_get_backfilled_or_create_a_model(tmp_path):
    root=tmp_path/'raw'; root.mkdir()
    t=audited(); t.pop('scannerInputs')
    (root/'2026-10-30.json').write_text(json.dumps({'trades':[t]}))
    report=research.train(tmp_path/'out', {'bull':root}, '2026-11-01')
    assert report['state']=='collecting' and not report['probabilityValidated']
    assert report['excluded']['missing_frozen_level_setup']==1
    assert not (tmp_path/'out'/'neural-candidate.json').exists()


def test_late_exit_quotes_cannot_train_the_win_probability(tmp_path):
    root=tmp_path/'raw'; root.mkdir()
    t=audited(); t['exitPriceAt']='2026-10-30T15:50:00-04:00'
    (root/'2026-10-30.json').write_text(json.dumps({'trades':[t]}))
    days, excluded=research.collect({'bull':root},'2026-11-01')
    assert not days and excluded['late_exit_quote']==1


def test_future_outcome_in_an_old_signal_file_cannot_leak_into_training(tmp_path):
    root=tmp_path/'raw'; root.mkdir()
    t=audited()
    t.update(exitAt='2026-11-02T11:00:00-05:00',exitPriceAt='2026-11-02T11:00:00-05:00')
    (root/'2026-10-30.json').write_text(json.dumps({'trades':[t]}))
    days,excluded=research.collect({'bull':root},'2026-11-01')
    assert not days and excluded['outcome_after_cutoff']==1


def test_neural_training_keeps_calibration_and_test_days_separate_without_promotion(tmp_path, monkeypatch):
    days={}
    first=datetime(2026,9,1)
    for n in range(30):
        day=(first+timedelta(days=n)).date().isoformat()
        days[day]=[{'features': {'confirmLevel': 1+i%3, 'setupCount':3, 'setup_tagRvol':1},
                    'net': 20 if i%2 else -10, 'win': i%2, 'cost':100,
                    'exitAt': day+'T11:00:00-04:00'} for i in range(30)]
    monkeypatch.setattr(research,'collect',lambda *args:(days,{}))
    report=research.train(tmp_path, {'bull':tmp_path/'raw'}, '2026-11-01')
    assert report['state']=='research_only' and not report['probabilityValidated']
    assert max(report['trainDays']) < min(report['calibrationDays'])
    assert max(report['calibrationDays']) < min(report['testDays'])
    assert report['test']['trades']==180 and report['test']['wins']==90
    assert (tmp_path/'neural-candidate.json').exists()
    assert not (tmp_path/'champion.json').exists()
