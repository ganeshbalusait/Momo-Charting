import copy
import json
from momx import ai_filter as ai
from momx import scanner_learning as sl


def trade():
    return {'at': '2026-10-30T10:00:00-04:00', 'scannerInputs': {
        'version': 1, 'publicationAt': '2026-10-30T09:59:45-04:00',
        'highestLevel': 3, 'historyReady': True, 'setupCount': 3,
        'setupTypes': [{'key': k, 'text': k} for k in ['tagRvol', 'tagSqz', 'tagSkit']],
        'increasing': ['5m', '15m', '30m'], 'sqz': True, 'fire': True, 'skittles': True}}


def test_all_23_flags_and_nested_level_are_entry_features_without_mandatory_mom():
    t = trade()
    f = ai.features(t, 'bull')
    assert len(sl.SETUP_RULE_KEYS) == 23 and len(set(sl.SETUP_RULE_KEYS)) == 23
    assert f['confirmLevel'] == 3 and f['setupCount'] == 3 and f['rvolIncreasingCount'] == 3
    assert f['setup_tagRvol'] == f['setup_tagSqz'] == f['setup_tagSkit'] == 1
    assert f['setup_tagMomStrong'] == 0
    assert all(k in ai.NUMERIC for k in sl.SCANNER_NUMERIC)
    t.update(exitPrice=999, best=1000, close=-10)
    assert ai.features(t, 'bull') == f
    for level in (1, 2, 3):
        t['scannerInputs']['highestLevel'] = level
        assert ai.features(t, 'bull')['confirmLevel'] == level


def test_missing_stale_future_or_bear_inputs_are_unknown_not_negative_examples():
    for change in [{'publicationAt': '2026-10-30T10:00:01-04:00'},
                   {'publicationAt': '2026-10-30T09:00:00-04:00'},
                   {'setupCount': 4}, {'version': 2}, {'highestLevel': True}]:
        t = trade(); t['scannerInputs'].update(change)
        assert all(v is None for v in sl.features(t, 'bull').values())
    assert all(v is None for v in sl.features(trade(), 'bear').values())
    assert all(v is None for v in sl.features({}, 'bull').values())
    t = trade(); t['scannerInputs'].update(highestLevel=0, historyReady=False, increasing=[])
    assert sl.features(t, 'bull')['confirmLevel'] is None
    assert sl.features(t, 'bull')['setupCount'] == 3


def test_frozen_inputs_survive_disk_loading_compaction_and_permanent_dataset(tmp_path):
    t = trade(); t.update(symbol='AMZN', rule='level3', status='closed', entry=1, exitPrice=1.5)
    root = tmp_path/'bot'; root.mkdir()
    (root/'2026-10-30.json').write_text(json.dumps({'trades': [t]}))
    days = ai.load_days({'bull': root})
    assert days['2026-10-30'][0][1]['scannerInputs'] == t['scannerInputs']
    ai.compact(days)
    assert sl.coverage(days)['levels']['3'] == 1
    ai.save_dataset(days, tmp_path/'ai')
    restored = ai.load_days({}, dataset=tmp_path/'ai'/'dataset')
    assert restored['2026-10-30'][0][1]['_features']['setup_tagSkit'] == 1
    report = sl.coverage(restored)
    assert report['captured'] == 1 and not report['probabilityValidated']


def test_duplicate_or_unknown_setup_types_cannot_inflate_count():
    for keys in [['tagRvol', 'tagRvol', 'tagSqz'], ['tagRvol', 'futureProfit', 'tagSqz']]:
        t = trade(); t['scannerInputs']['setupTypes'] = [{'key': k} for k in keys]
        assert sl.snapshot(t, 'bull') is None
