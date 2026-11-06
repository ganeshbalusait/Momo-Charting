"""Fair-share AI review (2026-11-05): every Setup rule gets review turns."""
from momx import paper_learning as pl


def trade(side, rule, n, day='2026-11-04'):
    return {'key': f'{side}|{rule}|S{n}|{day}T10:{n:02d}', 'side': side, 'rule': rule,
            'entryAt': f'{day}T10:{n:02d}:00-05:00', 'pnlPerContract': 1}


def done(side, rule, n):
    return {'state': 'complete', 'evidence': [trade(side, rule, i) for i in range(n)]}


def test_batches_are_one_rule_least_reviewed_first_newest_trades_first():
    rows = [trade('bull', 'mom', i) for i in range(30)] + [trade('bull', 'level1', i) for i in range(10)]
    batches = pl.fair_batches(rows, [done('bull', 'mom', 300)], capacity=10)
    assert batches[0][0]['rule'] == 'level1', 'the never-reviewed rule goes first'
    assert all(len({r['rule'] for r in b}) == 1 for b in batches)
    assert [r['key'] for r in batches[0]] == sorted([r['key'] for r in batches[0]], reverse=True)
    per_rule = {}
    for b in batches:
        per_rule[b[0]['rule']] = per_rule.get(b[0]['rule'], 0) + 1
    assert per_rule == {'level1': 2, 'mom': 2}, 'at most MAX_PENDING_PER_RULE waiting per rule'


def test_rules_already_waiting_are_skipped_and_capacity_respected():
    rows = [trade('bull', 'mom', i) for i in range(30)] + [trade('bear', 'sqz', i) for i in range(30)]
    waiting = [{'state': 'pending', 'evidence': [trade('bull', 'mom', 99)]}] * 2
    batches = pl.fair_batches(rows, waiting, capacity=1)
    assert len(batches) == 1 and batches[0][0]['rule'] == 'sqz'
    assert pl.fair_batches(rows, waiting, capacity=0) == []


class FakeReviewer:
    def enabled(self):
        return True

    def request(self, prompt, parse):
        return {'summary': 's', 'hypothesis': 'h', 'validation': 'v', 'forecast': 'unclear'}


def test_review_runs_the_least_reviewed_rule_first(tmp_path):
    team = pl.LearningTeam({}, directory=tmp_path, reviewer=FakeReviewer(), hermes=type('H', (), {'enabled': lambda s: False})())
    team.save('batch-old-mom.json', {'state': 'pending', 'attempts': 0, 'createdAt': 1, 'evidence': [trade('bull', 'mom', 1)]})
    team.save('batch-new-level.json', {'state': 'pending', 'attempts': 0, 'createdAt': 2, 'evidence': [trade('bull', 'level2', 1)]})
    team.save('batch-done.json', done('bull', 'mom', 50) | {'completedAt': 1})
    assert team.run_once()
    assert team.jobs['batch-new-level.json']['state'] == 'complete'
    assert team.jobs['batch-old-mom.json']['state'] == 'pending'
