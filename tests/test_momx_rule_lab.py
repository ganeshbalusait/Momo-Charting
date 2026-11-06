"""Rule lab (2026-11-05): per-Setup-rule scorecard, AI-picked chart checks,
10-day forward test and the shared statistics check."""
import json
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from momx import rule_lab as rl

ET = ZoneInfo('America/New_York')


def trade(rule, ret, *, spot=100.0, wall=110.0, side_bull=True, ema9=101.0, ema21=100.0):
    """A finished BOT trade whose half-spread return is about `ret` percent."""
    entry = 1.0
    out = entry * (1 + ret / 100)
    return {'rule': rule, 'symbol': 'X', 'entry': entry, 'exitPrice': out, 'spot': spot,
            'callWalls': [{'strike': wall, 'oi': 9000}], 'putWalls': [{'strike': spot * 0.9, 'oi': 9000}],
            'setup': {'chartContext': {'referencePrice': spot, 'levels': {
                'ema9': ema9, 'ema21': ema21, 'vwap': spot - 1, 'prevHigh': spot - 2, 'R1': spot + 5, 'ema200': spot - 10}}}}


def test_half_return_pays_a_quarter_spread_each_way_and_zero_bid_is_zero():
    t = {'entry': 1.0, 'entryBid': 0.9, 'entryAsk': 1.1, 'exitPrice': 1.5, 'exitBid': 1.4, 'exitAsk': 1.6}
    assert rl.half_return(t) == pytest.approx((1.45 / 1.05 - 1) * 100, abs=1e-3)
    assert rl.half_return({**t, 'exitBid': 0}) == -100.0
    assert rl.half_return({'entry': 0, 'exitPrice': 1}) is None


def test_checks_bull_and_bear_and_unknown():
    t = trade('mom', 5, wall=104.0)
    assert rl.check_value('bull', t, 'wallRoom3') is True
    assert rl.check_value('bull', trade('mom', 5, wall=101.0), 'wallRoom3') is False
    assert rl.check_value('bull', t, 'emaTrend') is True
    assert rl.check_value('bear', t, 'emaTrend') is False
    assert rl.check_value('bull', t, 'aboveR1') is False
    assert rl.check_value('bull', {'rule': 'mom'}, 'aboveVwap') is None, 'missing levels are unknown, never guessed'


def days(n, start='2026-10-01'):
    d = date.fromisoformat(start)
    out = []
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def write_days(root, per_day):
    root.mkdir(parents=True, exist_ok=True)
    for day, trades in per_day.items():
        (root / f'{day}.json').write_text(json.dumps({'date': day, 'trades': trades}), encoding='utf-8')


def good_rule_day():
    """mom: wall-room trades win +20%, wall-overhead trades lose -20%."""
    return [trade('mom', 20, wall=110.0) for _ in range(3)] + [trade('mom', -20, wall=100.5) for _ in range(3)]


class Picker:
    def __init__(self, pick):
        self.pick, self.prompts = pick, []

    def enabled(self):
        return True

    def request(self, prompt, parse):
        self.prompts.append(prompt)
        return parse(json.dumps({'pick': self.pick, 'reason': 'Room under the call wall'}))


def test_scorecard_screen_ai_pick_forward_test_and_statistics_check(tmp_path):
    root = tmp_path / 'bull'
    train, forward = days(10), days(12, '2026-10-20')
    write_days(root, {d: good_rule_day() for d in train})
    picker = Picker(0)
    clock = [0.0]
    lab = rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=picker, clock=lambda: clock[0])
    lab.tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    card = lab.status()['scorecard']
    mom = next(r for r in card['rules'] if r['rule'] == 'mom')
    assert mom['trades'] == 60 and mom['checks']['wallRoom3']['with']['winRate'] == 100.0
    exps = lab.status()['experiments']
    assert len(exps) == 1 and exps[0]['state'] == 'running' and exps[0]['check'] == 'wallRoom3'
    assert exps[0]['pickedBy'] == 'AI rule-lab agent' and lab.status()['trials'] == 1
    assert 'wallRoom3' in picker.prompts[0]
    # Same day: no second test starts.
    lab.tick(datetime(2026, 10, 16, 20, 0, tzinfo=ET))
    assert len(lab.status()['experiments']) == 1
    # Forward days only count after the pick; noisy-but-better days pass.
    per_day = {}
    for i, d in enumerate(forward):
        per_day[d] = [trade('mom', 15 + i % 3, wall=110.0) for _ in range(3)] + [trade('mom', -25 + i % 4, wall=100.5) for _ in range(3)]
    write_days(root, per_day)
    lab.tick(datetime(2026, 11, 5, 18, 0, tzinfo=ET))
    first = lab.status()['experiments'][0]
    assert first['state'] == 'improved', first
    assert first['forward']['days'] == 12 and first['forward']['kept'] == 36
    assert first['statistics']['passed'] is True
    mom = next(r for r in lab.status()['scorecard']['rules'] if r['rule'] == 'mom')
    assert mom['aiImproved']['check'] == 'wallRoom3'
    # The result is fed back to the agents.
    assert lab.feedback()[0]['state'] == 'improved'


def test_a_check_that_stops_helping_is_rejected(tmp_path):
    root = tmp_path / 'bull'
    write_days(root, {d: good_rule_day() for d in days(10)})
    lab = rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=Picker(0))
    lab.tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    flipped = {d: [trade('mom', -20 + i % 3, wall=110.0) for _ in range(3)] + [trade('mom', 20, wall=100.5) for _ in range(3)]
               for i, d in enumerate(days(12, '2026-10-20'))}
    write_days(root, flipped)
    lab.tick(datetime(2026, 11, 5, 18, 0, tzinfo=ET))
    assert lab.status()['experiments'][0]['state'] == 'rejected'


def test_ai_down_still_tests_the_top_candidate_and_says_so(tmp_path):
    class Down:
        def enabled(self):
            return True

        def request(self, prompt, parse):
            raise RuntimeError('model offline')
    root = tmp_path / 'bull'
    write_days(root, {d: good_rule_day() for d in days(10)})
    lab = rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=Down())
    lab.tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    exp = lab.status()['experiments'][0]
    assert exp['pickedBy'] == 'rules (AI unavailable)' and 'unavailable' in lab.status()['error']


def test_too_few_trades_start_nothing(tmp_path):
    root = tmp_path / 'bull'
    write_days(root, {d: [trade('go', 10)] for d in days(5)})
    lab = rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=Picker(0))
    lab.tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    assert lab.status()['experiments'] == []
    assert lab.status()['scorecard']['rules'][0]['rule'] == 'go'


def test_state_survives_a_restart(tmp_path):
    root = tmp_path / 'bull'
    write_days(root, {d: good_rule_day() for d in days(10)})
    rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=Picker(0)).tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    again = rl.RuleLab(roots={'bull': root}, directory=tmp_path / 'lab', reviewer=None)
    assert again.status()['trials'] == 1 and again.status()['experiments'][0]['state'] == 'running'
