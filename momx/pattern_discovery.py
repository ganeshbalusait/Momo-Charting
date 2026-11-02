"""Bounded unsupervised entry-pattern discovery, with no outcome labels or authority.

Clusters describe similarity, never profitability. Only already validated frozen
snapshots supplied by paper_learning are used. No current chart reconstruction.
"""
import hashlib
import json
import math
from datetime import datetime
from momx.scanner_learning import SETUP_RULE_KEYS
from momx.chart_learning import LEVELS

MAX_ROWS = 512
MIN_ROWS = 30


def _vector(row):
    scanner = row.get('scannerInputsAtSignal')
    if not isinstance(scanner, dict) or scanner.get('highestLevel') not in (1, 2, 3):
        return None
    types = scanner.get('setupTypes')
    if not isinstance(types, list):
        return None
    keys = {t.get('key') for t in types if isinstance(t, dict)}
    if len(keys) != len(types) or not keys <= set(SETUP_RULE_KEYS):
        return None
    vector = [float(scanner['highestLevel'] == level) for level in (1, 2, 3)]
    vector += [float(key in keys) for key in SETUP_RULE_KEYS]
    chart = row.get('chartContextAtSignal') or {}
    price = chart.get('referencePrice')
    for key in LEVELS:
        value = (chart.get('levels') or {}).get(key)
        valid = all(type(v) in (int, float) and math.isfinite(v) and v > 0 for v in (price, value))
        # Missing is explicit, not an invented zero distance. Clip outliers.
        vector += [min(1., max(-1., (price / value - 1) * 20)) if valid else 0., float(valid)]
    return vector


def discover(rows):
    """Deterministic, capped k-means; outcome changes cannot change clusters."""
    unique = {}
    for row in rows:
        vector = _vector(row)
        if vector is not None and isinstance(row.get('key'), str):
            # Collapse identical ticker/time inputs from nested strategy records.
            identity = (row.get('symbol'), row.get('signalAt', row.get('entryAt')), tuple(vector))
            unique[identity] = vector
    def recent(item):
        try:
            stamp = datetime.fromisoformat(str(item[0][1]).replace('Z', '+00:00'))
            at = stamp.timestamp() if stamp.tzinfo else 0
        except (ValueError, TypeError, OverflowError):
            at = 0
        return at, repr(item[0])
    # A rolling recent window avoids retaining only the lowest-valued vectors.
    vectors = sorted(v for _, v in sorted(unique.items(), key=recent)[-MAX_ROWS:])
    report = {'method': 'unsupervised_entry_kmeans_v1', 'state': 'insufficient_data',
              'observations': len(vectors), 'availableObservations': len(unique),
              'minimum': MIN_ROWS, 'maximum': MAX_ROWS,
              'probabilityValidated': False, 'entryBlocking': False,
              'note': 'Entry similarity only; clusters do not predict wins. Outcomes are excluded.'}
    report['fingerprint'] = hashlib.sha256(json.dumps(vectors).encode()).hexdigest()
    if len(vectors) < MIN_ROWS:
        return report
    def distance(a, b):
        return sum((x-y)**2 for x, y in zip(a, b))
    centers = [vectors[0]]
    for _ in range(2):
        candidate = max(vectors, key=lambda v: min(distance(v, c) for c in centers))
        if candidate in centers:
            break
        centers.append(candidate)
    for _ in range(12):
        groups = [[] for _ in centers]
        for vector in vectors:
            groups[min(range(len(centers)), key=lambda i: distance(vector, centers[i]))].append(vector)
        updated = [[sum(col)/len(group) for col in zip(*group)] if group else centers[i]
                   for i, group in enumerate(groups)]
        if updated == centers:
            break
        centers = updated
    # Reassign against final centroids, including when iteration cap is reached.
    groups = [[] for _ in centers]
    for vector in vectors:
        groups[min(range(len(centers)), key=lambda i: distance(vector, centers[i]))].append(vector)
    report.update(state='research_only', clusters=[{
        'id': i+1, 'observations': len(group),
        'commonSetupRules': [key for j, key in enumerate(SETUP_RULE_KEYS)
                             if group and sum(v[3+j] for v in group)/len(group) >= .6],
        'levelCounts': {str(level): sum(v[level-1] == 1 for v in group) for level in (1, 2, 3)}
    } for i, group in enumerate(groups) if group])
    return report
