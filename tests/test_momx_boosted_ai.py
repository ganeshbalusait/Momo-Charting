import json
import numpy as np
import pytest
from momx import ai_filter as ai
from momx.boosted_ai import BoostedModel, export_model, qualifies


def test_model_must_improve_both_chronological_sets_and_portable_parity():
    report = {"onnxMaxProbabilityDifference": 1e-7,
              "baseline": {"validation": {"auc": .52}, "test": {"auc": .54, "brier": .26}},
              "results": {"validation": {"auc": .56}, "test": {"auc": .56, "brier": .24}}}
    assert qualifies(report)
    for section, key, value in [("test", "auc", .53), ("validation", "auc", .51), ("test", "brier", .27)]:
        changed = json.loads(json.dumps(report))
        changed["results"][section][key] = value
        assert not qualifies(changed)
    report["onnxMaxProbabilityDifference"] = .1
    assert not qualifies(report)
    assert not qualifies({})


@pytest.fixture
def trained(tmp_path):
    sklearn = pytest.importorskip("sklearn.ensemble")
    pytest.importorskip("skl2onnx")
    pytest.importorskip("onnxruntime")
    from threadpoolctl import threadpool_limits
    rows = [{"x": float(x)} for x in np.linspace(-2, 2, 100)]
    encoder = ai.Model(("x",)).prepare(rows)
    matrix = encoder._matrix(rows).astype(np.float32)
    labels = [int(row["x"] > 0) for row in rows]
    with threadpool_limits(limits=1):
        original = sklearn.HistGradientBoostingClassifier(max_iter=10, min_samples_leaf=5, early_stopping=False).fit(matrix, labels)
        portable = export_model(original, encoder, matrix[:1], tmp_path, {"trainedAt": "fixture"})
    return original, portable, matrix


def test_exported_local_model_reproduces_trained_probabilities(trained):
    original, portable, matrix = trained
    assert np.max(np.abs(original.predict_proba(matrix)[:, 1] - portable.predict_matrix(matrix))) < 1e-5


def test_bad_model_pair_falls_back_to_the_local_baseline(trained, tmp_path):
    (tmp_path / "champion.json").write_text(json.dumps({"champion": "boosted"}))
    baseline = ai.Model(("x",)).prepare([{"x": 0.}])
    (tmp_path / "model.json").write_text(json.dumps(baseline.to_json()))
    (tmp_path / "boosted.onnx").write_bytes(b"incomplete export")
    scorer = ai.Scorer({}, tmp_path)
    scorer._load_model()
    assert scorer.champion == "lr" and scorer.model is not None
    assert scorer.model_load_error == "ValueError"
