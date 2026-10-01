from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from config import settings


def _sigmoid(values: np.ndarray) -> np.ndarray:
    clipped = np.clip(values, -30.0, 30.0)
    return 1.0 / (1.0 + np.exp(-clipped))


@dataclass(slots=True)
class PredictiveModelPrediction:
    win_probability: float
    expected_r: float
    edge_score: float
    confidence: float
    model_name: str


class PredictiveTradeModel:
    MODEL_NAME = "PredictiveTradeModel-v1"
    FEATURE_COLUMNS = [
        "rvol",
        "intraday_change_pct",
        "extension_above_ema9_pct",
        "risk_per_share",
        "last_price",
        "average_volume",
        "atr_value",
        "above_vwap",
        "ema_stack",
        "breakout_close_confirmed",
        "volume_trend",
        "market_trend",
        "close_near_high",
        "not_overextended",
        "first_5m_bullish",
        "first_5m_close_above_vwap",
        "orb_breakout",
        "setup_orb",
        "setup_previous_day_high",
        "setup_premarket_high",
        "setup_ema_vwap",
        "trigger_premarket_high",
        "trigger_previous_day_high",
        "trigger_opening_range_high",
    ]

    def __init__(self, artifact_path: str | None = None) -> None:
        self.artifact_path = Path(artifact_path or settings.ai.predictive_model_path)
        self.is_trained = False
        self.feature_means: dict[str, float] = {}
        self.feature_stds: dict[str, float] = {}
        self.logistic_weights: list[float] = []
        self.logistic_bias: float = 0.0
        self.regression_weights: list[float] = []
        self.regression_bias: float = 0.0
        self.training_rows: int = 0
        self.win_rate: float = 0.0
        self._load_if_available()

    def fit(self, samples: pd.DataFrame, epochs: int = 600, learning_rate: float = 0.08) -> dict:
        dataset = self._prepare_samples(samples)
        if dataset.empty:
            raise ValueError("No training samples available for predictive model.")

        features = dataset[self.FEATURE_COLUMNS].astype(float)
        self.feature_means = {column: float(features[column].mean()) for column in self.FEATURE_COLUMNS}
        self.feature_stds = {
            column: max(float(features[column].std(ddof=0)), 1e-6)
            for column in self.FEATURE_COLUMNS
        }

        x = self._normalize(features)
        y_win = dataset["label_win"].astype(float).to_numpy()
        y_r = dataset["label_r_multiple"].astype(float).to_numpy()

        weights = np.zeros(x.shape[1], dtype=float)
        bias = 0.0
        sample_count = max(len(x), 1)
        for _ in range(epochs):
            logits = np.dot(x, weights) + bias
            predictions = _sigmoid(logits)
            error = predictions - y_win
            weights -= learning_rate * (np.dot(x.T, error) / sample_count)
            bias -= learning_rate * float(error.mean())

        ridge = 0.15
        xtx = np.dot(x.T, x)
        xty = np.dot(x.T, y_r)
        regression_weights = np.linalg.solve(
            xtx + (ridge * np.eye(x.shape[1])),
            xty,
        )
        regression_bias = float(y_r.mean() - np.dot(x.mean(axis=0), regression_weights))

        self.logistic_weights = weights.tolist()
        self.logistic_bias = float(bias)
        self.regression_weights = regression_weights.tolist()
        self.regression_bias = regression_bias
        self.training_rows = int(len(dataset))
        self.win_rate = float(dataset["label_win"].mean()) if len(dataset) else 0.0
        self.is_trained = True

        metrics = self.training_metrics(dataset)
        self.save()
        return metrics

    def predict_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame()

        enriched = frame.copy()
        if not self.is_trained:
            enriched["model_win_probability"] = np.nan
            enriched["model_expected_r"] = np.nan
            enriched["model_edge_score"] = np.nan
            enriched["model_confidence"] = np.nan
            return enriched

        feature_frame = self._feature_frame(enriched)
        x = self._normalize(feature_frame)
        logistic = _sigmoid(np.dot(x, np.array(self.logistic_weights)) + self.logistic_bias)
        expected_r = np.dot(x, np.array(self.regression_weights)) + self.regression_bias
        prob_score = logistic * 100.0
        expected_r_score = np.clip(((expected_r + 1.0) / 3.0) * 100.0, 0.0, 100.0)
        edge_score = np.clip((0.65 * prob_score) + (0.35 * expected_r_score), 0.0, 100.0)
        confidence = np.clip((np.abs(logistic - 0.5) * 200.0) + (np.minimum(expected_r, 2.5) * 10.0), 0.0, 100.0)

        enriched["model_win_probability"] = np.round(logistic, 4)
        enriched["model_expected_r"] = np.round(expected_r, 4)
        enriched["model_edge_score"] = np.round(edge_score, 2)
        enriched["model_confidence"] = np.round(confidence, 2)
        enriched["predictive_model_name"] = self.MODEL_NAME
        return enriched

    def predict_row(self, row: dict) -> PredictiveModelPrediction | None:
        if not self.is_trained:
            return None
        frame = pd.DataFrame([row])
        predicted = self.predict_frame(frame).iloc[0]
        return PredictiveModelPrediction(
            win_probability=float(predicted["model_win_probability"]),
            expected_r=float(predicted["model_expected_r"]),
            edge_score=float(predicted["model_edge_score"]),
            confidence=float(predicted["model_confidence"]),
            model_name=self.MODEL_NAME,
        )

    def training_metrics(self, dataset: pd.DataFrame) -> dict:
        predicted = self.predict_frame(dataset)
        if predicted.empty:
            return {}
        labels = dataset["label_win"].astype(int).to_numpy()
        probs = predicted["model_win_probability"].fillna(0.5).to_numpy()
        preds = (probs >= 0.5).astype(int)
        accuracy = float((preds == labels).mean()) if len(labels) else 0.0
        mae_r = float(np.abs(predicted["model_expected_r"].to_numpy() - dataset["label_r_multiple"].to_numpy()).mean()) if len(dataset) else 0.0
        return {
            "rows": int(len(dataset)),
            "accuracy": round(accuracy, 4),
            "mae_r": round(mae_r, 4),
            "win_rate": round(float(dataset["label_win"].mean()), 4) if len(dataset) else 0.0,
        }

    def save(self) -> None:
        if not self.is_trained:
            return
        self.artifact_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "model_name": self.MODEL_NAME,
            "trained_at": datetime.utcnow().isoformat(),
            "feature_columns": self.FEATURE_COLUMNS,
            "feature_means": self.feature_means,
            "feature_stds": self.feature_stds,
            "logistic_weights": self.logistic_weights,
            "logistic_bias": self.logistic_bias,
            "regression_weights": self.regression_weights,
            "regression_bias": self.regression_bias,
            "training_rows": self.training_rows,
            "win_rate": self.win_rate,
        }
        self.artifact_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _load_if_available(self) -> None:
        if not self.artifact_path.exists():
            return
        try:
            payload = json.loads(self.artifact_path.read_text(encoding="utf-8"))
        except Exception:
            return
        self.feature_means = {key: float(value) for key, value in payload.get("feature_means", {}).items()}
        self.feature_stds = {key: max(float(value), 1e-6) for key, value in payload.get("feature_stds", {}).items()}
        self.logistic_weights = [float(value) for value in payload.get("logistic_weights", [])]
        self.logistic_bias = float(payload.get("logistic_bias", 0.0))
        self.regression_weights = [float(value) for value in payload.get("regression_weights", [])]
        self.regression_bias = float(payload.get("regression_bias", 0.0))
        self.training_rows = int(payload.get("training_rows", 0))
        self.win_rate = float(payload.get("win_rate", 0.0))
        self.is_trained = bool(self.logistic_weights and self.regression_weights)

    def _prepare_samples(self, samples: pd.DataFrame) -> pd.DataFrame:
        if samples is None or samples.empty:
            return pd.DataFrame()
        dataset = samples.copy()
        if "pnl" not in dataset.columns or "r_multiple" not in dataset.columns:
            return pd.DataFrame()
        dataset["label_win"] = (pd.to_numeric(dataset["pnl"], errors="coerce").fillna(0.0) > 0).astype(float)
        dataset["label_r_multiple"] = pd.to_numeric(dataset["r_multiple"], errors="coerce").fillna(0.0)
        feature_frame = self._feature_frame(dataset)
        for column in feature_frame.columns:
            dataset[column] = feature_frame[column]
        dataset = dataset.dropna(subset=["label_win", "label_r_multiple"])
        return dataset

    def _feature_frame(self, frame: pd.DataFrame) -> pd.DataFrame:
        features = pd.DataFrame(index=frame.index)
        features["rvol"] = pd.to_numeric(self._series(frame, "rvol", 0.0), errors="coerce").fillna(0.0)
        features["intraday_change_pct"] = pd.to_numeric(self._series(frame, "intraday_change_pct", 0.0), errors="coerce").fillna(0.0)
        features["extension_above_ema9_pct"] = pd.to_numeric(self._series(frame, "extension_above_ema9_pct", 0.0), errors="coerce").fillna(0.0)
        features["risk_per_share"] = pd.to_numeric(self._series(frame, "risk_per_share", 0.0), errors="coerce").fillna(0.0)
        features["last_price"] = pd.to_numeric(self._series(frame, "last_price", self._series(frame, "entry", 0.0)), errors="coerce").fillna(0.0)
        features["average_volume"] = pd.to_numeric(self._series(frame, "average_volume", 0.0), errors="coerce").fillna(0.0)
        features["atr_value"] = pd.to_numeric(self._series(frame, "atr_value", 0.0), errors="coerce").fillna(0.0)

        bool_columns = [
            "above_vwap",
            "ema_stack",
            "breakout_close_confirmed",
            "volume_trend",
            "market_trend",
            "close_near_high",
            "not_overextended",
            "first_5m_bullish",
            "first_5m_close_above_vwap",
            "orb_breakout",
        ]
        for column in bool_columns:
            features[column] = self._series(frame, column, False).fillna(False).astype(bool).astype(float)

        setup_series = self._series(frame, "setup_name", "").astype(str).str.lower()
        trigger_series = self._series(frame, "trigger_source", "").astype(str).str.lower()
        features["setup_orb"] = setup_series.str.contains("orb", regex=False).astype(float)
        features["setup_previous_day_high"] = setup_series.str.contains("previous day high", regex=False).astype(float)
        features["setup_premarket_high"] = setup_series.str.contains("premarket high", regex=False).astype(float)
        features["setup_ema_vwap"] = setup_series.str.fullmatch(r"ema \+ vwap").fillna(False).astype(float)
        features["trigger_premarket_high"] = trigger_series.eq("premarket_high").astype(float)
        features["trigger_previous_day_high"] = trigger_series.eq("previous_day_high").astype(float)
        features["trigger_opening_range_high"] = trigger_series.eq("opening_range_high").astype(float)
        return features.reindex(columns=self.FEATURE_COLUMNS, fill_value=0.0)

    def _series(self, frame: pd.DataFrame, column: str, default) -> pd.Series:
        if column in frame.columns:
            return frame[column]
        if isinstance(default, pd.Series):
            return default
        return pd.Series([default] * len(frame), index=frame.index)

    def _normalize(self, features: pd.DataFrame) -> np.ndarray:
        normalized = features.copy().astype(float)
        for column in self.FEATURE_COLUMNS:
            mean = self.feature_means.get(column, 0.0)
            std = self.feature_stds.get(column, 1.0)
            normalized[column] = (normalized[column] - mean) / max(std, 1e-6)
        return normalized.to_numpy(dtype=float)
