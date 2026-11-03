"""Overall Record: how past trades that LOOKED LIKE this scanner row turned out.

2026-11-02, his ask: "Record column, I need overall win percentage, not each
rule - it should use charts, indicators, levels, Level 1/2/3, Setup columns".

Not a model's guess. For each row, the K recorded virtual trades whose
entry-time picture was closest - Setup tags, Level 1-3 confirmations, RVOL
per timeframe, Skittles, squeeze, ADX/DI, high/low, grade, sector, and the
chart levels (VWAP, EMAs, prior/premarket high-low, pivots) - and how many of
them actually won. Shown next to the all-trades base rate, plus a hold-out
check: the same method scored on the newest days it was not built from, so
the trader can see whether "looks like" has ever meant "wins more".

Read-only over artifacts/momx_ai_filter/dataset/*.jsonl.gz (written by the
nightly retrain). Loaded once per new dataset in a background thread; a build
never waits for it. Never raises.
"""
from __future__ import annotations

import glob
import gzip
import json
import math
import os
import threading
from typing import Any, Mapping, Sequence

import numpy as np

from momx import ai_filter, chart_learning, scanner_learning

#: The row-derived inputs only: what the scanner row shows (no option quote,
#: Jev or market fields - a scanner row has no contract yet).
ROW_KEYS = tuple(
    ["s_" + k for k in ai_filter.SETUP_KEYS]
    + list(scanner_learning.SCANNER_NUMERIC)
    + list(chart_learning.NUMERIC)
)
K = 100
REFERENCE_DAYS = 20
MAX_REFERENCE = 20000      # newest trades per side; bounds the per-build cost (~1 s per Watchlist)
HOLDOUT_DAYS = 3
HOLDOUT_SAMPLE = 3000

_LOCK = threading.Lock()
_STATE: dict = {"key": None, "sides": {}, "loading": False, "error": None}


def _dataset_files() -> list[str]:
    return sorted(glob.glob(str(ai_filter._dir() / "dataset" / "*.jsonl.gz")))


def _vector(feats: Mapping[str, Any]) -> list[float]:
    out = []
    for key in ROW_KEYS:
        v = feats.get(key)
        out.append(float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) else math.nan)
    return out


def _read(files: Sequence[str]) -> dict:
    by_side: dict = {}
    for path in files:
        day = os.path.basename(path)[:10]
        try:
            with gzip.open(path, "rt", encoding="utf-8") as fh:
                for line in fh:
                    row = json.loads(line)
                    if row.get("win") is None:
                        continue
                    side = row.get("side") or "bull"
                    bucket = by_side.setdefault(side, {"x": [], "y": [], "day": []})
                    bucket["x"].append(_vector(row.get("features") or {}))
                    bucket["y"].append(1.0 if row["win"] else 0.0)
                    bucket["day"].append(day)
        except (OSError, ValueError):
            continue
    return by_side


class _Side:
    """One side's reference set, standardised; missing inputs sit at the mean."""

    def __init__(self, x: np.ndarray, y: np.ndarray):
        self.mean = np.nanmean(x, axis=0)
        self.mean = np.where(np.isfinite(self.mean), self.mean, 0.0)
        std = np.nanstd(x, axis=0)
        self.std = np.where(np.isfinite(std) & (std > 1e-9), std, 1.0)
        self.x = self._scale(x)
        self.sq = (self.x ** 2).sum(axis=1)
        self.y = y
        self.base = float(y.mean() * 100) if len(y) else None

    def _scale(self, x: np.ndarray) -> np.ndarray:
        x = np.where(np.isfinite(x), x, self.mean)
        return ((x - self.mean) / self.std).astype(np.float32)

    def win_rates(self, x: np.ndarray, k: int = K) -> np.ndarray:
        q = self._scale(x)
        d = (q ** 2).sum(axis=1)[:, None] + self.sq[None, :] - 2.0 * (q @ self.x.T)
        k = min(k, len(self.y))
        idx = np.argpartition(d, k - 1, axis=1)[:, :k]
        return self.y[idx].mean(axis=1) * 100.0


def _auc(scores: np.ndarray, labels: np.ndarray) -> float | None:
    pos, neg = labels == 1, labels == 0
    if pos.sum() < 20 or neg.sum() < 20:
        return None
    order = scores.argsort()
    ranks = np.empty(len(scores))
    ranks[order] = np.arange(1, len(scores) + 1)
    return float((ranks[pos].sum() - pos.sum() * (pos.sum() + 1) / 2) / (pos.sum() * neg.sum()))


def _build(files: Sequence[str]) -> dict:
    raw = _read(files[-(REFERENCE_DAYS + HOLDOUT_DAYS):])
    sides = {}
    for side, b in raw.items():
        x, y, days = np.array(b["x"], dtype=np.float64), np.array(b["y"]), np.array(b["day"])
        if len(y) < 500:
            continue
        cut = sorted(set(days))[-HOLDOUT_DAYS] if len(set(days)) > HOLDOUT_DAYS else None
        check = None
        if cut is not None:
            train, test = days < cut, days >= cut
            if train.sum() >= 500 and test.sum() >= 100:
                ref = _Side(x[train][-MAX_REFERENCE:], y[train][-MAX_REFERENCE:])
                pick = np.flatnonzero(test)
                if len(pick) > HOLDOUT_SAMPLE:
                    pick = np.random.default_rng(0).choice(pick, HOLDOUT_SAMPLE, replace=False)
                est = np.concatenate([ref.win_rates(x[pick[i:i + 500]]) for i in range(0, len(pick), 500)])
                truth = y[pick]
                top = est >= np.quantile(est, 0.8)
                check = {"auc": None if (a := _auc(est, truth)) is None else round(a, 3),
                         "days": int(len(set(days[test]))), "trades": int(len(pick)),
                         "topFifthWin": round(float(truth[top].mean() * 100), 1) if top.any() else None,
                         "allWin": round(float(truth.mean() * 100), 1)}
        sides[side] = {"ref": _Side(x[-MAX_REFERENCE:], y[-MAX_REFERENCE:]), "check": check,
                       "trades": int(min(len(y), MAX_REFERENCE)),
                       "from": str(min(days)), "through": str(max(days))}
    return sides


def _ensure_loaded() -> None:
    files = _dataset_files()
    key = tuple((f, os.path.getmtime(f)) for f in files[-(REFERENCE_DAYS + HOLDOUT_DAYS):]) if files else None
    with _LOCK:
        if not key or key == _STATE["key"] or _STATE["loading"]:
            return
        _STATE["loading"] = True

    def load():
        try:
            sides = _build(files)
            with _LOCK:
                _STATE.update(key=key, sides=sides, error=None)
        except Exception as exc:  # noqa: BLE001 - the Record cell just stays per-rule
            with _LOCK:
                _STATE["error"] = f"{type(exc).__name__}: {exc}"
        finally:
            with _LOCK:
                _STATE["loading"] = False

    threading.Thread(target=load, name="momx-similar-record", daemon=True).start()


def _row_vector(row: Mapping[str, Any], side: str) -> list[float]:
    from momx.option_track import setup_snapshot
    pseudo = {"setup": setup_snapshot(row), "at": None}
    try:
        return _vector(ai_filter.features(pseudo, side))
    except Exception:  # noqa: BLE001
        return [math.nan] * len(ROW_KEYS)


def apply(payload: Mapping[str, Any], side: str) -> None:
    """Stamp row["similarRecord"] on every shipped row (rows and rest). Never raises."""
    try:
        _ensure_loaded()
        with _LOCK:
            entry = _STATE["sides"].get(side)
        if entry is None:
            return
        rows = [r for key in ("rows", "rest") for r in (payload.get(key) or []) if isinstance(r, dict)]
        if not rows:
            return
        x = np.array([_row_vector(r, side) for r in rows], dtype=np.float64)
        ref: _Side = entry["ref"]
        rates = np.concatenate([ref.win_rates(x[i:i + 200]) for i in range(0, len(x), 200)])
        known = np.isfinite(x).sum(axis=1)
        for row, pct, n_known in zip(rows, rates, known):
            row["similarRecord"] = {
                "winPct": round(float(pct), 1), "k": min(K, len(ref.y)), "base": round(ref.base, 1),
                "inputs": int(n_known), "of": len(ROW_KEYS), "check": entry["check"],
                "from": entry["from"], "through": entry["through"], "trades": entry["trades"]}
    except Exception:  # noqa: BLE001 - never costs the board
        return
