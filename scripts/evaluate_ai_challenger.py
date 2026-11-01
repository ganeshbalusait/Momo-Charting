"""Compare local models on chronological validation/test days; no orders or live writes."""
import os
os.environ["OPENBLAS_NUM_THREADS"] = "1"
os.environ["OMP_NUM_THREADS"] = "1"
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
from momx import ai_filter as ai


def metrics(model, items):
    rows, labels, kept = ai._xy(items)
    if isinstance(model, ai.NeuralModel):
        probabilities = model.net(model.enc._matrix(rows))
    else:
        probabilities = model.predict_matrix(model._matrix(rows))
    p = np.asarray(probabilities)
    y = np.asarray(labels)
    return {"trades": len(y), "auc": ai._auc(list(p), labels), "brier": float(np.mean((p-y)**2)),
            "bySide": {side: {"auc": ai._auc([float(p[i]) for i,t in enumerate(kept) if t[0] == side],
                                             [labels[i] for i,t in enumerate(kept) if t[0] == side])}
                       for side in ("bull", "bear")}}


def main():
    started = time.monotonic()
    today = ai.datetime.now(ai.ET).date().isoformat()
    loaded = ai.load_days(ai._roots(), dataset=ai._dir() / "dataset")
    days = {day: loaded[day] for day in sorted(loaded) if day < today}
    ai.add_records(days)
    ai.add_market(days, ai.MarketTape(ai._dir()))
    ai.compact(days)
    days = {d: items for d, items in days.items() if items}
    names = sorted(days)
    if len(names) < 12:
        raise RuntimeError("Need at least 12 completed days")
    train_days, validation_days, test_days = names[:-6], names[-6:-3], names[-3:]
    training = [item for d in train_days for item in days[d]]
    training_day = [d for d in train_days for _ in days[d]]
    local_numeric = tuple(n for n in ai.NUMERIC if not n.startswith("j_") and n not in ("jevP", "jevMissing"))
    output = Path("artifacts/ai_challenger_evaluation")
    output.mkdir(parents=True, exist_ok=True)
    report = {"trainDays": train_days, "validationDays": validation_days, "testDays": test_days,
              "localInputsExcludeJev": True, "automaticTrading": False, "models": {},
              "scannerLearning": ai.scanner_learning.coverage(days), "probabilityValidated": False,
              "target": "Legacy gross Strategy-exit midpoint classification; not validated net-profit probability"}
    for name, numeric in (("baseline", ai.NUMERIC), ("local-logistic", local_numeric), ("deep-neural", local_numeric)):
        print(f"Training {name}: {len(training):,} trades", flush=True)
        if name == "deep-neural":
            rows, labels, _ = ai._xy(training)
            enc = ai.Model(numeric).prepare(rows)
            net = ai.nn_fit(enc._matrix(rows), np.asarray(labels, dtype=float), hidden=(64,32,16), epochs=50, lr=.001, l2=.001, seed=42,
                            input_clip=6., activation="leaky_relu")
            model = ai.NeuralModel(enc, net)
        else:
            model = ai.fit_learning(training, training_day, train_days, numeric)
        report["models"][name] = {"validation": metrics(model, [item for d in validation_days for item in days[d]]),
                                  "test": metrics(model, [item for d in test_days for item in days[d]])}
        (output / f"{name}.json").write_text(json.dumps(model.to_json()), encoding="utf-8")
        print(name, json.dumps(report["models"][name]), flush=True)
    report["elapsedSeconds"] = round(time.monotonic() - started, 1)
    (output / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print("Evaluation complete; active model unchanged.", flush=True)


if __name__ == "__main__":
    main()
