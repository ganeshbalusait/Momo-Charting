"""One pre-specified local tree challenger. No live model or order changes."""
import os
os.environ["OPENBLAS_NUM_THREADS"] = os.environ["OMP_NUM_THREADS"] = "1"
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import time
import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from threadpoolctl import threadpool_limits
from momx import ai_filter as ai
from momx.boosted_ai import export_model
from evaluate_ai_challenger import metrics


def main():
    started = time.monotonic()
    root = Path("artifacts/ai_challenger_evaluation")
    prior = json.loads((root / "report.json").read_text(encoding="utf-8"))
    loaded = ai.load_days(ai._roots(), dataset=ai._dir() / "dataset")
    today = ai.datetime.now(ai.ET).date().isoformat()
    days = {day: loaded[day] for day in sorted(loaded) if day < today}
    ai.add_records(days)
    ai.add_market(days, ai.MarketTape(ai._dir()))
    ai.compact(days)
    train_days, validation_days, test_days = (prior[k] for k in ("trainDays", "validationDays", "testDays"))
    numeric = tuple(n for n in ai.NUMERIC if not n.startswith("j_") and n not in ("jevP", "jevMissing"))
    rows, labels, _ = ai._xy(item for day in train_days for item in days[day])
    encoder = ai.Model(numeric).prepare(rows)
    matrix = encoder._matrix(rows).astype(np.float32)
    print(f"Training local boosted trees on {len(rows):,} trades; all JEV inputs excluded", flush=True)
    estimator = HistGradientBoostingClassifier(learning_rate=.05, max_iter=150, max_leaf_nodes=15,
        max_depth=5, min_samples_leaf=64, l2_regularization=2., max_bins=128, early_stopping=False, random_state=42)
    with threadpool_limits(limits=1):
        estimator.fit(matrix, labels)
        model = export_model(estimator, encoder, matrix[:1], root / "boosted", {
            "trainedAt": ai.datetime.now(ai.ET).isoformat(), "trainDays": train_days,
            "validationDays": validation_days, "testDays": test_days, "independentOfJev": True})
        result = {"validation": metrics(model, [item for day in validation_days for item in days[day]]),
                  "test": metrics(model, [item for day in test_days for item in days[day]])}
        check_rows, _, _ = ai._xy(item for day in test_days for item in days[day])
        test_matrix = encoder._matrix(check_rows).astype(np.float32)
        original = estimator.predict_proba(test_matrix)[:, list(estimator.classes_).index(1)]
        difference = float(np.max(np.abs(original - model.predict_matrix(test_matrix))))
    baseline = prior["models"]["baseline"]
    eligible = (difference < 1e-4 and result["validation"]["auc"] > baseline["validation"]["auc"] + .005
                and result["test"]["auc"] > baseline["test"]["auc"] + .005
                and result["test"]["brier"] < baseline["test"]["brier"])
    report = {"model": "local-gradient-boosted-trees", "results": result, "baseline": baseline,
              "onnxMaxProbabilityDifference": difference, "eligibleForPaperScoring": eligible,
              "elapsedSeconds": round(time.monotonic()-started, 1), "activeModelChanged": False}
    (root / "boosted-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report), flush=True)


if __name__ == "__main__":
    main()
