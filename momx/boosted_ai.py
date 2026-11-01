"""Portable local boosted-tree scoring; training dependencies are optional."""
import hashlib
import json
from pathlib import Path
import numpy as np


def qualifies(report):
    try:
        candidate, baseline = report["results"], report["baseline"]
        return (report["onnxMaxProbabilityDifference"] < 1e-4
                and candidate["validation"]["auc"] > baseline["validation"]["auc"] + .005
                and candidate["test"]["auc"] > baseline["test"]["auc"] + .005
                and candidate["test"]["brier"] < baseline["test"]["brier"])
    except (KeyError, TypeError):
        return False


class BoostedModel:
    def __init__(self, model_path, metadata_path):
        import onnxruntime as ort
        from momx.ai_filter import Model
        document = json.loads(Path(metadata_path).read_text(encoding="utf-8"))
        if document.get("format") != "agx-boosted-v1":
            raise ValueError("Unknown boosted model format")
        model_bytes = Path(model_path).read_bytes()
        if hashlib.sha256(model_bytes).hexdigest() != document["sha256"]:
            raise ValueError("Boosted model checksum mismatch")
        options = ort.SessionOptions()
        options.intra_op_num_threads = options.inter_op_num_threads = 1
        options.add_session_config_entry("session.intra_op.allow_spinning", "0")
        self.session = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
        self.enc = Model.from_json(document["encoder"])
        self.input_name, self.output_name = document["input"], document["output"]
        self.positive_index = int(document["positiveIndex"])
        self.metadata = document

    def _matrix(self, rows):
        return self.enc._matrix(rows).astype(np.float32)

    def predict_matrix(self, matrix):
        probabilities = self.session.run([self.output_name], {self.input_name: matrix.astype(np.float32)})[0]
        values = np.asarray(probabilities)[:, self.positive_index]
        if not np.isfinite(values).all() or ((values < 0) | (values > 1)).any():
            raise ValueError("Invalid boosted-model probabilities")
        return values

    def predict(self, features):
        return float(self.predict_matrix(self._matrix([features]))[0])


def export_model(estimator, encoder, sample, directory, provenance):
    from skl2onnx import to_onnx
    import onnxruntime as ort
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    converted = to_onnx(estimator, sample.astype(np.float32), options={id(estimator): {"zipmap": False}}, target_opset=17)
    model_bytes = converted.SerializeToString()
    temporary = directory / "boosted.onnx.tmp"
    temporary.write_bytes(model_bytes)
    temporary.replace(directory / "boosted.onnx")
    options = ort.SessionOptions()
    options.intra_op_num_threads = options.inter_op_num_threads = 1
    session = ort.InferenceSession(model_bytes, sess_options=options, providers=["CPUExecutionProvider"])
    output = next(node.name for node in session.get_outputs() if len(node.shape) == 2 and node.shape[1] == 2)
    document = {"format": "agx-boosted-v1", "encoder": encoder.to_json(), "sha256": hashlib.sha256(model_bytes).hexdigest(),
                "input": session.get_inputs()[0].name, "output": output,
                "positiveIndex": list(estimator.classes_).index(1), **provenance}
    temporary = directory / "boosted.json.tmp"
    temporary.write_text(json.dumps(document), encoding="utf-8")
    temporary.replace(directory / "boosted.json")
    return BoostedModel(directory / "boosted.onnx", directory / "boosted.json")


def train_challenger(days, numeric, directory):
    """Nightly local challenger; independent day splits, no trade execution."""
    from sklearn.ensemble import HistGradientBoostingClassifier
    from threadpoolctl import threadpool_limits
    from momx import ai_filter as ai
    import shutil
    names = sorted(day for day in days if days[day])[-(ai.TRAIN_WINDOW_DAYS + 6):]
    if len(names) < 12:
        return {"qualified": False, "reason": "Need at least 12 completed days"}
    train_days, validation_days, test_days = names[:-6], names[-6:-3], names[-3:]
    training = [item for day in train_days for item in days[day]]
    training_day = [day for day in train_days for _ in days[day]]
    validation = [item for day in validation_days for item in days[day]]
    testing = [item for day in test_days for item in days[day]]
    rows, labels, _ = ai._xy(training)
    local_numeric = tuple(n for n in numeric if not n.startswith("j_") and n not in ("jevP", "jevMissing"))
    encoder = ai.Model(local_numeric).prepare(rows)
    matrix = encoder._matrix(rows).astype(np.float32)

    def assess(model, items):
        f, y, _ = ai._xy(items)
        p = np.asarray(model.predict_matrix(model._matrix(f)))
        return {"trades": len(y), "auc": ai._auc(list(p), y), "brier": float(np.mean((p-np.asarray(y))**2))}

    with threadpool_limits(limits=1):
        baseline = ai.fit_learning(training, training_day, train_days, numeric)
        estimator = HistGradientBoostingClassifier(learning_rate=.05, max_iter=150, max_leaf_nodes=15,
            max_depth=5, min_samples_leaf=64, l2_regularization=2., max_bins=128, early_stopping=False, random_state=42)
        estimator.fit(matrix, labels)
        directory = Path(directory)
        candidate_dir = directory / "boosted-candidate"
        candidate = export_model(estimator, encoder, matrix[:1], candidate_dir, {
            "trainedAt": ai.datetime.now(ai.ET).isoformat(), "trainDays": train_days,
            "validationDays": validation_days, "testDays": test_days, "independentOfJev": True})
        test_rows, _, _ = ai._xy(testing)
        encoded_test = encoder._matrix(test_rows).astype(np.float32)
        original = estimator.predict_proba(encoded_test)[:, list(estimator.classes_).index(1)]
        difference = float(np.max(np.abs(original-candidate.predict_matrix(encoded_test))))
        report = {"results": {"validation": assess(candidate, validation), "test": assess(candidate, testing)},
                  "baseline": {"validation": assess(baseline, validation), "test": assess(baseline, testing)},
                  "onnxMaxProbabilityDifference": difference}
    report["qualified"] = qualifies(report)
    ai._write_atomic(directory / "boosted-report.json", json.dumps(report))
    if report["qualified"]:
        # Publish metadata last; its checksum makes intermediate pairs fail
        # closed while the scorer retains its previous valid model.
        for name in ("boosted.onnx", "boosted.json"):
            target = directory / name
            temporary = target.with_suffix(target.suffix + ".publish.tmp")
            shutil.copyfile(candidate_dir / name, temporary)
            temporary.replace(target)
    return report
