"""Rule lab over real HTTP (2026-11-05): the worker route, its admin gate and
api_server's proxy, with the real RuleLab reading scratch BOT day files."""
import json
from datetime import datetime
from zoneinfo import ZoneInfo

import momx_worker
from api_harness import running_api
from momx import rule_lab
from test_momx_rule_lab import days, good_rule_day, write_days, Picker
from worker_harness import running_worker

ET = ZoneInfo('America/New_York')


def built_lab(tmp_path):
    root = tmp_path / 'bot' / 'bull'
    write_days(root, {d: good_rule_day() for d in days(10)})
    lab = rule_lab.RuleLab(roots={'bull': root}, directory=tmp_path / 'bot' / 'lab', reviewer=Picker(0))
    lab.tick(datetime(2026, 10, 16, 18, 0, tzinfo=ET))
    return lab


def test_worker_route_serves_the_scorecard_and_tests_to_admin_only(tmp_path, monkeypatch):
    monkeypatch.setattr(rule_lab, '_LAB', built_lab(tmp_path))
    with running_worker(tmp_path, monkeypatch) as worker:
        status, _ = worker.get('/api/momx-scanner/rule-lab', headers={'X-AGX-User': 'someone@example.com'})
        assert status == 403
        status, body = worker.get('/api/momx-scanner/rule-lab', headers=worker.as_admin(monkeypatch))
    assert status == 200, body
    mom = next(r for r in body['scorecard']['rules'] if r['rule'] == 'mom')
    assert mom['aiTesting']['check'] == 'wallRoom3'
    assert body['experiments'][0]['pickedBy'] == 'AI rule-lab agent' and body['trials'] == 1


def test_not_started_says_so(tmp_path, monkeypatch):
    monkeypatch.setattr(rule_lab, '_LAB', None)
    with running_worker(tmp_path, monkeypatch) as worker:
        status, body = worker.get('/api/momx-scanner/rule-lab', headers=worker.as_admin(monkeypatch))
    assert status == 200 and body['error'] == 'Rule lab not started'


def test_the_browser_reaches_it_through_api_servers_proxy(tmp_path, monkeypatch):
    monkeypatch.setattr(rule_lab, '_LAB', built_lab(tmp_path))
    monkeypatch.setattr(momx_worker, 'viewer_is_admin', lambda who, db_path=None: True)
    with running_worker(tmp_path, monkeypatch) as worker, running_api(worker_port=worker.port) as api:
        status, proxied = api.get('/api/momx-scanner/rule-lab')
        _, direct = worker.get('/api/momx-scanner/rule-lab')
    assert status == 200, proxied
    assert proxied == direct
