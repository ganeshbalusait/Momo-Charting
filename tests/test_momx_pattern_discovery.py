import copy
from momx.pattern_discovery import discover


def rows():
    return [{'key': str(i), 'symbol': 'SPY', 'signalAt': str(i), 'pnlPerContract': i-20,
             'scannerInputsAtSignal': {'highestLevel': i%3+1,
                 'setupTypes': [{'key': 'tagRvol'}, {'key': 'tagMomStrong' if i%2 else 'tagSkit'}]},
             'chartContextAtSignal': {'referencePrice': 100+i, 'levels': {'ema21': 110}}}
            for i in range(60)]


def test_clusters_are_outcome_blind_and_order_independent():
    original = rows()
    result = discover(original)
    changed = copy.deepcopy(original)
    for row in changed:
        row.update(pnlPerContract=999, exitPrice=999, returnPct=999)
    assert discover(list(reversed(changed))) == result
    assert result['state'] == 'research_only'
    assert sum(c['observations'] for c in result['clusters']) == 60
    assert not result['probabilityValidated'] and not result['entryBlocking']
    assert original == rows()


def test_missing_inputs_and_nested_duplicates_do_not_invent_samples():
    original = rows()
    duplicates = [{**r, 'key': 'duplicate'+r['key']} for r in original]
    assert discover(original+duplicates) == discover(original)
    result = discover([{'key': 'missing'}]+original[:4])
    assert result['state'] == 'insufficient_data' and result['observations'] == 4
    assert 'clusters' not in result


def test_history_is_capped_and_malformed_or_missing_levels_are_unknown():
    original = rows()
    original[0]['scannerInputsAtSignal']['setupTypes'] = [{'key': 'invented'}]
    original[1]['chartContextAtSignal']['levels']['ema21'] = float('nan')
    assert discover(original)['observations'] == 59
    many = [{**rows()[i%60], 'signalAt': str(i), 'key': str(i)} for i in range(700)]
    assert discover(many)['observations'] == 512
