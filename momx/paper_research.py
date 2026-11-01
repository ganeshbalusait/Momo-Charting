"""Chronological candidate selection on actual paper-account outcomes.

This is a historical screen, never promotion evidence. Parameter and family
selection use only training days; the later segment is checked once.
"""
import hashlib
import json

from momx.paper_experiments import GRIDS, fill_result, finite, paired, when, win_loss_improved

VERSION = 3
FEATURES = {"spread_ceiling": "spread", "premium_floor": "premium", "entry_after": "minute"}


def dataset(history, through_day):
    rows, seen = [], set()
    counts = {"orders": 0, "missed": 0, "open": 0, "closed": 0, "invalidClosed": 0, "legacyInputs": 0}
    for day in history:  # mirror history is newest-state-first and deduplicated
        for r in day["rows"]:
            key, at = r.get("buyId"), when(r.get("buyAt"))
            if not key or key in seen or not at or at.date().isoformat() > through_day:
                continue
            seen.add(key)
            counts["orders"] += 1
            if r.get("status") == "missed":
                counts["missed"] += 1
                continue
            if r.get("status") != "closed":
                counts["open"] += 1
                continue
            counts["closed"] += 1
            result, closed = fill_result(r), when(r.get("closedAt"))
            if not result or not closed or closed < at or closed.date().isoformat() > through_day:
                counts["invalidClosed"] += 1
                continue
            snapshot = r.get("entryInputs")
            if not isinstance(snapshot, dict):
                # The submitted limit and order time are known before outcome.
                # Never substitute the later buy fill or reconstruct old spreads.
                counts["legacyInputs"] += 1
                snapshot = {"premium": r.get("buyLimit"), "spread": None, "minute": at.hour*60+at.minute}
            x = {k: snapshot.get(k) if finite(snapshot.get(k)) else None for k in ("premium", "spread", "minute")}
            rows.append({"key": key, "day": at.date().isoformat(), "outcomeDay": closed.date().isoformat(),
                         "inputs": x, **result})
    rows.sort(key=lambda r: (r["day"], r["key"]))
    counts["usableFills"] = len(rows)
    counts["entryDays"] = len({r["day"] for r in rows})
    fingerprint = hashlib.sha256(json.dumps({"rows": rows, "counts": counts}, sort_keys=True, allow_nan=False).encode()).hexdigest()[:20]
    return rows, counts, fingerprint


def score(rows, spec):
    report = paired(rows, spec)
    missing = sum(not finite(r["inputs"].get(FEATURES[spec["kind"]])) for r in rows)
    report.update(missingInput=missing, inputCoverage=1-missing/len(rows) if rows else 0)
    return report


def passes(report):
    return (report["inputCoverage"] >= .95 and .5 <= report["retention"] < 1
            and win_loss_improved(report)
            and report["improvement"] > 0 and report["candidateNet"] > 0
            and report["candidateStressNet"] > 0
            and report["candidateDrawdown"] <= report["baselineDrawdown"])


def screen_fills(history, through_day, preferred_kind=None):
    rows, counts, fingerprint = dataset(history, through_day)
    days = sorted({r["day"] for r in rows})
    cut = max(1, int(len(days)*.7))
    train_days, validation_days = days[:cut], days[cut:]
    boundary = validation_days[0] if validation_days else "9999"
    # Purge training labels that were not known before validation began.
    train = [r for r in rows if r["day"] in train_days and r["outcomeDay"] < boundary]
    validation = [r for r in rows if r["day"] in validation_days]
    result = {"version": VERSION, "passed": False, "dataset": counts, "dataFingerprint": fingerprint,
              "basis": "Historical Paper 3 broker fills and confirmed expirations, actual quantities, fees excluded; paired selection holds unused cash",
              "trainDays": train_days, "validationDays": validation_days, "trainingFills": len(train),
              "validationFills": len(validation), "purgedTrainingFills": sum(r["day"] in train_days for r in rows)-len(train),
              "requirements": {"entryDays": 6, "trainingFills": 40, "validationFills": 20, "inputCoverage": .95},
              "candidates": []}
    if counts["invalidClosed"]:
        return {**result, "reason": "Historical screen blocked by incomplete closed-fill evidence"}
    if len(days) < 6 or len(train) < 40 or len(validation) < 20:
        return {**result, "reason": "Collecting actual paper fills for chronological training and validation"}
    split = max(1, len(train_days)//2)
    blocks = [[r for r in train if r["day"] in names] for names in (train_days[:split], train_days[split:])]
    eligible = []
    for kind, values in GRIDS.items():
        for value in values:
            spec = {"kind": kind, "value": value}
            training = score(train, spec)
            halves = [score(block, spec) for block in blocks]
            stable = passes(training) and all(passes(block) for block in halves)
            result["candidates"].append({"spec": spec, "training": training,
                "stableTraining": stable, "blockImprovements": [r["improvement"] for r in halves],
                "reason": "Eligible on both training periods" if stable else
                          "Insufficient recorded entry inputs" if training["inputCoverage"] < .95 else
                          "Did not improve net results consistently on both training periods"})
            if stable:
                # The LLM's hypothesis only breaks an exact training-score tie.
                rank = (min(r["improvement"]/max(1,r["days"]) for r in halves),
                        training["improvement"], kind == preferred_kind)
                eligible.append((rank, spec, training))
    if not eligible:
        return {**result, "reason": "No supported candidate improved actual-fill training consistently after costs"}
    _, spec, training = max(eligible, key=lambda item: item[0])
    validation_report = score(validation, spec)
    passed = passes(validation_report)
    return {**result, "spec": spec, "training": training, "validation": validation_report,
            "passed": passed, "reason": "Advance to future-fill evaluation" if passed else
            "Selected candidate failed later actual-fill validation; no runner-up substitution"}
