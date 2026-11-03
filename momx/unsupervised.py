"""Unsupervised research learners for the PAPER bot. Never used by entries or exits.

Two learners, both fit only on EARLIER days and judged on LATER, untouched days
(the same chronological discipline as ai_filter.walk_forward):

* Market regimes - k-means over the SPY/QQQ/breadth context each trade saw at
  entry (ai_filter.MARKET_KEYS, attached by add_market). Clusters are learned
  without labels; outcomes are only used afterwards to ask "did the regime the
  training days found still separate winners from losers later?".
* Anomalies - a small numpy autoencoder (a neural network that learns to
  reconstruct normal entry-time inputs). A trade whose inputs it cannot
  reconstruct is unusual relative to everything seen before. Again no labels in
  training; outcomes only grade the held-out split.

Results are research evidence for the agent team and the BOT page. Nothing
here changes a trade, a model champion or a promotion gate.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

import numpy as np

REGIME_K = 4
MIN_TRADES = 60
TEST_FRACTION = 0.3
ANOMALY_TOP = 0.2
MIN_CLUSTER_TEST = 15


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return float(value)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _matrix(rows: list[Mapping], keys: Iterable[str]) -> tuple[np.ndarray, list[str]]:
    keys = list(keys)
    data = np.array([[(_finite(r.get(k)) if _finite(r.get(k)) is not None else np.nan) for k in keys] for r in rows],
                    dtype=float).reshape(len(rows), len(keys))
    return data, keys


class Scaler:
    """Median impute + mean/std scale, fit on training rows only."""

    def fit(self, data: np.ndarray) -> "Scaler":
        with np.errstate(all="ignore"):
            self.fill = np.nan_to_num(np.nanmedian(data, axis=0), nan=0.0)
            filled = np.where(np.isnan(data), self.fill, data)
            self.mean = filled.mean(axis=0)
            std = filled.std(axis=0)
        self.std = np.where(std > 1e-9, std, 1.0)
        return self

    def transform(self, data: np.ndarray) -> np.ndarray:
        return (np.where(np.isnan(data), self.fill, data) - self.mean) / self.std

    def to_json(self) -> dict:
        return {"fill": self.fill.tolist(), "mean": self.mean.tolist(), "std": self.std.tolist()}


def kmeans(data: np.ndarray, k: int, seed: int = 7, iters: int = 100) -> np.ndarray:
    """Plain k-means++ / Lloyd. Deterministic for a seed. Returns centroids."""
    rng = np.random.default_rng(seed)
    k = max(1, min(k, len(data)))
    centroids = [data[rng.integers(len(data))]]
    for _ in range(1, k):
        dist = np.min(((data[:, None, :] - np.array(centroids)[None]) ** 2).sum(-1), axis=1)
        total = dist.sum()
        pick = rng.choice(len(data), p=dist / total) if total > 0 else rng.integers(len(data))
        centroids.append(data[pick])
    centroids = np.array(centroids)
    for _ in range(iters):
        labels = assign(data, centroids)
        moved = np.array([data[labels == j].mean(axis=0) if np.any(labels == j) else centroids[j] for j in range(k)])
        if np.allclose(moved, centroids):
            break
        centroids = moved
    return centroids


def assign(data: np.ndarray, centroids: np.ndarray) -> np.ndarray:
    return np.argmin(((data[:, None, :] - centroids[None]) ** 2).sum(-1), axis=1)


class Autoencoder:
    """inputs -> tanh bottleneck -> linear reconstruction, trained with Adam."""

    def __init__(self, width: int, hidden: int, seed: int = 11) -> None:
        rng = np.random.default_rng(seed)
        self.w1 = rng.normal(0, 1 / math.sqrt(width), (width, hidden))
        self.b1 = np.zeros(hidden)
        self.w2 = rng.normal(0, 1 / math.sqrt(hidden), (hidden, width))
        self.b2 = np.zeros(width)

    def _forward(self, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h = np.tanh(x @ self.w1 + self.b1)
        return h, h @ self.w2 + self.b2

    def fit(self, x: np.ndarray, epochs: int = 300, lr: float = 0.01) -> "Autoencoder":
        params = [self.w1, self.b1, self.w2, self.b2]
        m = [np.zeros_like(p) for p in params]
        v = [np.zeros_like(p) for p in params]
        for step in range(1, epochs + 1):
            h, out = self._forward(x)
            grad_out = 2 * (out - x) / len(x)
            g_w2, g_b2 = h.T @ grad_out, grad_out.sum(0)
            grad_h = (grad_out @ self.w2.T) * (1 - h ** 2)
            g_w1, g_b1 = x.T @ grad_h, grad_h.sum(0)
            for i, g in enumerate((g_w1, g_b1, g_w2, g_b2)):
                m[i] = 0.9 * m[i] + 0.1 * g
                v[i] = 0.999 * v[i] + 0.001 * g * g
                params[i] -= lr * (m[i] / (1 - 0.9 ** step)) / (np.sqrt(v[i] / (1 - 0.999 ** step)) + 1e-8)
        return self

    def error(self, x: np.ndarray) -> np.ndarray:
        return ((self._forward(x)[1] - x) ** 2).mean(axis=1)


def _rows(done: Mapping[str, list], features, label) -> list[tuple[str, dict, int]]:
    out = []
    for day in sorted(done):
        for side, trade in done[day]:
            if "_features" in trade:     # a dataset row / compacted trade: its stored entry-time inputs
                y, feats = trade.get("_win"), trade["_features"] or {}
            else:
                y, feats = label(trade), features(trade, side)
            if y is None:
                continue
            out.append((day, feats, int(y)))
    return out


def _split(rows: list) -> tuple[list, list]:
    days = sorted({r[0] for r in rows})
    if len(days) < 4:
        return [], []
    cut = days[max(1, int(len(days) * (1 - TEST_FRACTION)))]
    return [r for r in rows if r[0] < cut], [r for r in rows if r[0] >= cut]


def _rate(ys: list[int]) -> float | None:
    return round(100.0 * sum(ys) / len(ys), 1) if ys else None


def regime_report(rows: list[tuple[str, dict, int]], keys: Iterable[str], k: int = REGIME_K) -> dict:
    train, test = _split(rows)
    if len(train) < MIN_TRADES or not test:
        return {"state": "collecting", "trades": len(rows), "needed": MIN_TRADES}
    tr, keys = _matrix([r[1] for r in train], keys)
    usable = ~np.all(np.isnan(tr), axis=0)
    if not usable.any():
        return {"state": "no market context", "trades": len(rows)}
    keys = [key for key, ok in zip(keys, usable) if ok]
    scaler = Scaler().fit(tr[:, usable])
    xtr = scaler.transform(tr[:, usable])
    centroids = kmeans(xtr, k)
    te, _ = _matrix([r[1] for r in test], keys)
    lab_tr, lab_te = assign(xtr, centroids), assign(scaler.transform(te), centroids)
    ytr, yte = [r[2] for r in train], [r[2] for r in test]
    clusters = []
    for j in range(len(centroids)):
        a = [y for y, l in zip(ytr, lab_tr) if l == j]
        b = [y for y, l in zip(yte, lab_te) if l == j]
        center = centroids[j] * scaler.std[usable] + scaler.mean[usable]
        clusters.append({"id": j, "trainTrades": len(a), "trainWinRate": _rate(a), "testTrades": len(b),
                         "testWinRate": _rate(b), "center": {key: round(float(c), 3) for key, c in zip(keys, center)}})
    # Did the regime training called best (or worst) stay that way on later days?
    judged = [c for c in clusters if c["testTrades"] >= MIN_CLUSTER_TEST and c["trainWinRate"] is not None]
    base = _rate(yte)
    persistence = None
    if len(judged) >= 2:
        best = max(judged, key=lambda c: c["trainWinRate"])
        worst = min(judged, key=lambda c: c["trainWinRate"])
        persistence = {"bestRegime": best["id"], "worstRegime": worst["id"],
                       "testSpread": round(best["testWinRate"] - worst["testWinRate"], 1),
                       "held": best["testWinRate"] > worst["testWinRate"]}
    return {"state": "ready", "k": len(centroids), "features": keys, "trainTrades": len(train), "testTrades": len(test),
            "testFrom": test[0][0], "testWinRate": base, "clusters": clusters, "persistence": persistence,
            "scaler": {key: val for key, val in zip(("fill", "mean", "std"), (scaler.fill.tolist(), scaler.mean.tolist(), scaler.std.tolist()))},
            "centroids": centroids.tolist(),
            "use": "research only - never filters or sizes a trade"}


def anomaly_report(rows: list[tuple[str, dict, int]], keys: Iterable[str]) -> dict:
    train, test = _split(rows)
    if len(train) < MIN_TRADES or not test:
        return {"state": "collecting", "trades": len(rows), "needed": MIN_TRADES}
    tr, keys = _matrix([r[1] for r in train], keys)
    usable = ~np.all(np.isnan(tr), axis=0)
    keys = [key for key, ok in zip(keys, usable) if ok]
    if len(keys) < 3:
        return {"state": "too few inputs", "trades": len(rows)}
    scaler = Scaler().fit(tr[:, usable])
    xtr = np.clip(scaler.transform(tr[:, usable]), -8, 8)
    net = Autoencoder(len(keys), max(2, min(8, len(keys) // 3))).fit(xtr)
    te, _ = _matrix([r[1] for r in test], keys)
    xte = np.clip(scaler.transform(te), -8, 8)
    threshold = float(np.quantile(net.error(xtr), 1 - ANOMALY_TOP))
    err = net.error(xte)
    yte = [r[2] for r in test]
    odd = [y for y, e in zip(yte, err) if e > threshold]
    normal = [y for y, e in zip(yte, err) if e <= threshold]
    # Which inputs the unusual trades differ on most (mean |z|), for the agents.
    flagged = xte[err > threshold]
    drivers = []
    if len(flagged):
        dev = np.abs(flagged).mean(axis=0)
        drivers = [keys[i] for i in np.argsort(dev)[::-1][:5]]
    return {"state": "ready", "inputs": len(keys), "trainTrades": len(train), "testTrades": len(test),
            "testFrom": test[0][0], "threshold": round(threshold, 4),
            "unusualTrades": len(odd), "unusualWinRate": _rate(odd),
            "normalTrades": len(normal), "normalWinRate": _rate(normal), "drivers": drivers,
            "use": "research only - never filters or sizes a trade"}


def train_report(done: Mapping[str, list], features, label, numeric: Iterable[str], market_keys: Iterable[str]) -> dict:
    """Both learners on one night's finished days. Never raises."""
    rows = _rows(done, features, label)
    out: dict = {"trades": len(rows)}
    for name, run in (("regimes", lambda: regime_report(rows, market_keys)),
                      ("anomalies", lambda: anomaly_report(rows, numeric))):
        try:
            out[name] = run()
        except Exception as exc:  # noqa: BLE001 - research must never cost the nightly model
            out[name] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"[:200]}
    return out


def brief(report: Mapping | None) -> dict:
    """The small, prompt-safe summary the agent team reads."""
    report = report or {}
    reg, ano = report.get("regimes") or {}, report.get("anomalies") or {}
    return {"regimes": {"state": reg.get("state"), "testWinRate": reg.get("testWinRate"), "persistence": reg.get("persistence"),
                        "clusters": [{k: c.get(k) for k in ("id", "trainWinRate", "testWinRate", "testTrades", "center")}
                                     for c in reg.get("clusters") or []]},
            "anomalies": {k: ano.get(k) for k in ("state", "unusualTrades", "unusualWinRate", "normalTrades", "normalWinRate", "drivers")}}
