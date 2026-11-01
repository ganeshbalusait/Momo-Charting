import json
from concurrent.futures import ThreadPoolExecutor

from momx.paper_mirror import PaperMirror


def test_status_and_history_remain_readable_during_broker_reconciliation(tmp_path):
    mirror = PaperMirror({}, tmp_path, off=lambda: False,
                         client_factory=lambda: (_ for _ in ()).throw(AssertionError('read cannot contact broker')))
    mirror._day = '2026-10-30'
    mirror._rows = {'loss': {'buyId': 'loss', 'side': 'bull', 'buyAt': '2026-10-30T10:00:00-04:00',
                             'status': 'closed', 'pl': -25, 'sells': [{'price': .75, 'qty': 1}]}}
    mirror._save()
    # Unsaved reconciliation work must not leak into the published snapshot.
    mirror._rows['loss']['pl'] = -30
    with ThreadPoolExecutor(max_workers=1) as pool:
        mirror._lock.acquire()
        try:
            status, history = pool.submit(lambda: (mirror.status(), mirror.history(3))).result(timeout=2)
        finally:
            mirror._lock.release()
    assert status['reconciling'] is True and status['snapshotSource'] == 'saved'
    assert status['snapshotAt'] and status['totals']['pl'] == -25
    assert history[0]['rows'][0]['pl'] == -25
    status['rows'][0]['sells'][0]['price'] = 999
    assert json.loads((tmp_path / '2026-10-30.json').read_text())['rows']['loss']['sells'][0]['price'] == .75
    assert mirror.status()['totals']['pl'] == -30


def test_busy_account_without_saved_evidence_is_unavailable_not_confirmed_zero(tmp_path):
    mirror = PaperMirror({}, tmp_path, off=lambda: False)
    with ThreadPoolExecutor(max_workers=1) as pool:
        mirror._lock.acquire()
        try:
            status = pool.submit(mirror.status).result(timeout=2)
        finally:
            mirror._lock.release()
    assert status['snapshotSource'] == 'unavailable'
    assert status['reconciling'] is True and status['snapshotAt'] is None
