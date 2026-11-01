from unittest.mock import Mock
from momx import scan_reuse as reuse


def test_old_identical_data_reuses_but_changes_and_day_boundary_invalidate():
    reuse._CACHE.clear()
    args = ('AMZN', {'close': 100}, {}, {}, 'anchor', 'industry', True)
    calc = Mock(return_value=('AMZN', {'m5': {'value': 1}}, 0, True, [], None, 100, False, []))
    newest = lambda frame: 100
    result = reuse.run(args, calc, newest, 200000)
    result[1]['m5']['value'] = 99
    assert reuse.run(args, calc, newest, 200001)[1]['m5']['value'] == 1
    assert calc.call_count == 1
    reuse.run((args[0], {'close': 101}, *args[2:]), calc, newest, 200002)
    assert calc.call_count == 2
    reuse.run(args, calc, newest, 300000)
    assert calc.call_count == 3


def test_live_missing_and_failed_data_are_never_cached():
    reuse._CACHE.clear()
    args = ('ULTA', {}, {}, {})
    calc = Mock(return_value=('ULTA', {}, 0, False, [], None, 100, False, []))
    for stamp in (None, 200000, 200001):
        for _ in range(2): reuse.run(args, calc, lambda frame: stamp, 200000)
    assert calc.call_count == 6
    calc.return_value = ('ULTA', None, 0, False, [], 'failed', 100, False, [])
    for _ in range(2): reuse.run(args, calc, lambda frame: 1, 200000)
    assert calc.call_count == 8


def test_each_tape_must_be_old_and_ticker_and_mode_are_isolated():
    reuse._CACHE.clear()
    old = {'stamp': 1, 'close': 100}
    args = ('AMZN', old, old, old, 'anchor', 'industry', True)
    calc = Mock(return_value=('AMZN', {}, 0, True, [], None, 100, False, []))
    newest = lambda frame: frame['stamp']
    for index in (1, 2, 3):
        recent = list(args)
        recent[index] = {'stamp': 199999}
        for _ in range(2): reuse.run(tuple(recent), calc, newest, 200000)
    assert calc.call_count == 6
    reuse.run(args, calc, newest, 200000)
    reuse.run(args, calc, newest, 200001)
    assert calc.call_count == 7
    reuse.run(('GE', *args[1:]), calc, newest, 200002)
    reuse.run((*args[:-1], False), calc, newest, 200003)
    assert calc.call_count == 9


def test_successful_nonmatch_is_reused_and_modes_do_not_evict_each_other():
    reuse._CACHE.clear()
    calc = Mock(return_value=('GE', None, 0, False, [], None, 100, False, []))
    args = ('GE', {}, {}, {}, 'anchor', 'industry', False)
    reuse.run(args, calc, lambda _: 1, 200000)
    reuse.run((*args[:-1], True), calc, lambda _: 1, 200000)
    reuse.run(args, calc, lambda _: 1, 200001)
    assert calc.call_count == 2
