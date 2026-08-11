from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from config import settings
from database.repository import TradingRepository
from predictive_model import PredictiveTradeModel
from reasoning_engine import TradeReasoningEngine


def _clamp(value: float, lower: float = 0.0, upper: float = 100.0) -> float:
    return max(lower, min(upper, value))


def _scale(value: float, low: float, high: float) -> float:
    if high <= low:
        return 0.0
    return _clamp(((value - low) / (high - low)) * 100.0)


@dataclass(slots=True)
class HeavyModelSnapshot:
    ai_trade_score: float
    model_win_probability: float | None
    model_expected_r: float | None
    model_edge_score: float | None
    model_confidence: float | None
    ml_score: float
    catalyst_score: float
    regime_score: float
    memory_score: float
    anomaly_score: float
    setup_fit_score: float
    trend_quality_score: float
    ai_confidence: float
    ai_model_name: str
    ai_decision: str
    ai_reasoning: str


class HeavyTradingModel:
    def __init__(self, repository: TradingRepository | None = None) -> None:
        self.repository = repository or TradingRepository()
        self.predictive_model = PredictiveTradeModel()
        self.reasoner = TradeReasoningEngine()

    def score_frame(
        self,
        frame: pd.DataFrame,
        memory_map: dict[str, dict] | None = None,
        catalyst_map: dict[str, dict] | None = None,
    ) -> pd.DataFrame:
        if frame is None or frame.empty or not settings.ai.enabled:
            return frame

        enriched = frame.copy()
        symbols = enriched["symbol"].astype(str).str.upper().tolist()
        resolved_memory = memory_map if memory_map is not None else self.repository.get_symbol_memory_snapshot(symbols)
        if catalyst_map is not None:
            resolved_catalyst = catalyst_map
        else:
            try:
                resolved_catalyst = self.repository.get_catalyst_snapshot(symbols)
            except Exception:
                # News is optional context and must never interrupt scoring.
                resolved_catalyst = {}

        rows: list[dict] = []
        for row in enriched.to_dict("records"):
            symbol = str(row.get("symbol", "")).upper()
            memory = resolved_memory.get(symbol, {})
            catalyst = resolved_catalyst.get(symbol, {})
            snapshot = self._score_row(row, memory, catalyst)
            merged = {**row, **asdict(snapshot)}
            rows.append(merged)

        return pd.DataFrame(rows)

    def _score_row(self, row: dict, memory: dict, catalyst: dict) -> HeavyModelSnapshot:
        ml_score = self._ml_score(row)
        catalyst_score = self._catalyst_score(catalyst)
        regime_score = self._regime_score(row)
        memory_score = self._memory_score(memory)
        anomaly_score = self._anomaly_score(row)
        setup_fit_score = self._setup_fit_score(row)
        trend_quality_score = self._trend_quality_score(row)
        execution_components = (
            (ml_score, max(float(settings.ai.ml_weight), 0.0)),
            (regime_score, max(float(settings.ai.regime_weight), 0.0)),
            (memory_score, max(float(settings.ai.memory_weight), 0.0)),
            (anomaly_score, max(float(settings.ai.anomaly_weight), 0.0)),
        )
        execution_weight = sum(weight for _, weight in execution_components)
        heuristic_score = round(
            sum(score * weight for score, weight in execution_components) / execution_weight
            if execution_weight > 0
            else 0.0,
            2,
        )
        heuristic_confidence = round(
            _clamp(
                (heuristic_score * 0.55)
                + (setup_fit_score * 0.20)
                + (trend_quality_score * 0.15)
                + (regime_score * 0.10)
            ),
            2,
        )
        model_prediction = self.predictive_model.predict_row(
            {
                **row,
                "ml_score": ml_score,
                "regime_score": regime_score,
                "memory_score": memory_score,
                "anomaly_score": anomaly_score,
            }
        )
        model_win_probability = model_prediction.win_probability if model_prediction else None
        model_expected_r = model_prediction.expected_r if model_prediction else None
        model_edge_score = model_prediction.edge_score if model_prediction else None
        model_confidence = model_prediction.confidence if model_prediction else None

        if model_prediction:
            ai_trade_score = round(
                (model_edge_score * settings.ai.model_edge_weight)
                + (heuristic_score * settings.ai.heuristic_weight),
                2,
            )
            ai_confidence = round(
                _clamp((model_confidence * 0.65) + (heuristic_confidence * 0.35)),
                2,
            )
            ai_model_name = f"{model_prediction.model_name} + {self.reasoner.MODEL_NAME}"
        else:
            ai_trade_score = heuristic_score
            ai_confidence = heuristic_confidence
            ai_model_name = "HeavyTradingModel-v2"

        decision = "Qualified" if ai_trade_score >= settings.ai.min_trade_score else "Filtered"
        reasoning = ", ".join(
            part
            for part in [
                f"Model win {model_win_probability:.0%}" if model_win_probability is not None else "",
                f"Model expected {model_expected_r:.2f}R" if model_expected_r is not None else "",
                f"Setup fit {setup_fit_score:.0f}",
                f"Trend quality {trend_quality_score:.0f}",
                f"ML {ml_score:.0f}",
                f"Catalyst {catalyst_score:.0f} (info only)",
                f"Regime {regime_score:.0f}",
                f"Memory {memory_score:.0f}",
                f"Anomaly {anomaly_score:.0f}",
                f"Confidence {ai_confidence:.0f}",
            ]
            if part
        )
        narrative = self.reasoner.explain(
            {
                **row,
                "symbol": row.get("symbol"),
                "setup_name": row.get("setup_name"),
                "trigger_source": row.get("trigger_source"),
                "ema_stack": row.get("ema_stack"),
                "above_vwap": row.get("above_vwap"),
                "volume_trend": row.get("volume_trend"),
                "market_trend": row.get("market_trend"),
                "breakout_close_confirmed": row.get("breakout_close_confirmed"),
                "catalyst_score": catalyst_score,
                "model_win_probability": model_win_probability,
                "model_expected_r": model_expected_r,
            }
        )
        return HeavyModelSnapshot(
            ai_trade_score=ai_trade_score,
            model_win_probability=round(model_win_probability, 4) if model_win_probability is not None else None,
            model_expected_r=round(model_expected_r, 4) if model_expected_r is not None else None,
            model_edge_score=round(model_edge_score, 2) if model_edge_score is not None else None,
            model_confidence=round(model_confidence, 2) if model_confidence is not None else None,
            ml_score=round(ml_score, 2),
            catalyst_score=round(catalyst_score, 2),
            regime_score=round(regime_score, 2),
            memory_score=round(memory_score, 2),
            anomaly_score=round(anomaly_score, 2),
            setup_fit_score=round(setup_fit_score, 2),
            trend_quality_score=round(trend_quality_score, 2),
            ai_confidence=ai_confidence,
            ai_model_name=ai_model_name,
            ai_decision=decision,
            ai_reasoning=f"{reasoning}. {narrative}",
        )

    def _setup_fit_score(self, row: dict) -> float:
        setup_name = str(row.get("setup_name", "")).lower()
        if "orb" in setup_name:
            return 96.0 if row.get("orb_breakout") and row.get("first_5m_close_above_vwap") else 45.0
        if "previous day high" in setup_name:
            return 92.0 if row.get("trigger_source") == "previous_day_high" and row.get("breakout_close_confirmed") else 40.0
        if "premarket high" in setup_name:
            return 92.0 if row.get("trigger_source") == "premarket_high" and row.get("breakout_close_confirmed") else 40.0
        if "ema + vwap" in setup_name:
            return 88.0 if row.get("above_vwap") and row.get("ema_stack") else 35.0
        return 50.0

    def _trend_quality_score(self, row: dict) -> float:
        ema = 100.0 if row.get("ema_stack") else 20.0
        vwap = 100.0 if row.get("above_vwap") else 20.0
        close_quality = 100.0 if row.get("close_near_high") else 35.0
        stretch = 100.0 if row.get("not_overextended") else 30.0
        volume = 100.0 if row.get("volume_trend") else 35.0
        intraday = _scale(float(row.get("intraday_change_pct", 0)), settings.scanner.min_intraday_change_pct, 5.0)
        return _clamp(
            (ema * 0.25)
            + (vwap * 0.20)
            + (close_quality * 0.15)
            + (stretch * 0.10)
            + (volume * 0.15)
            + (intraday * 0.15)
        )

    def _ml_score(self, row: dict) -> float:
        base_rule = float(row.get("score", 0))
        rvol = _scale(float(row.get("rvol", 0)), settings.scanner.min_rvol, 6.0)
        intraday = _scale(float(row.get("intraday_change_pct", 0)), settings.scanner.min_intraday_change_pct, 6.0)
        volume = 100.0 if row.get("volume_trend") else 35.0
        ema = 100.0 if row.get("ema_stack") else 25.0
        breakout = 100.0 if row.get("breakout_close_confirmed") else 20.0
        vwap = 100.0 if row.get("above_vwap") else 20.0
        close_quality = 100.0 if row.get("close_near_high") else 30.0
        stretch = 100.0 if row.get("not_overextended") else 35.0
        return _clamp(
            (base_rule * 0.25)
            + (rvol * 0.15)
            + (intraday * 0.10)
            + (volume * 0.15)
            + (ema * 0.10)
            + (breakout * 0.10)
            + (vwap * 0.05)
            + (close_quality * 0.05)
            + (stretch * 0.05)
        )

    def _catalyst_score(self, catalyst: dict) -> float:
        if not catalyst:
            return 45.0
        score = float(catalyst.get("score", 1))
        recency_bonus = float(catalyst.get("recency_bonus", 0))
        sentiment = str(catalyst.get("sentiment", "Neutral")).lower()
        sentiment_bonus = 20 if sentiment == "strong" else 10 if sentiment == "positive" else -15 if sentiment == "negative" else 0
        return _clamp((score * 25) + recency_bonus + sentiment_bonus)

    def _regime_score(self, row: dict) -> float:
        market = 100.0 if row.get("market_trend") else 30.0
        vwap = 100.0 if row.get("above_vwap") else 30.0
        ema = 100.0 if row.get("ema_stack") else 25.0
        return _clamp((market * 0.45) + (vwap * 0.30) + (ema * 0.25))

    def _memory_score(self, memory: dict) -> float:
        if not memory:
            return 50.0
        confidence = float(memory.get("confidence", 50))
        observations = min(float(memory.get("observations", 0)), 50.0)
        sample_bonus = observations * 0.6
        pnl_bonus = _clamp(float(memory.get("total_r", 0)) * 3.0, -20.0, 20.0)
        return _clamp(confidence + sample_bonus + pnl_bonus)

    def _anomaly_score(self, row: dict) -> float:
        rvol = _scale(float(row.get("rvol", 0)), settings.scanner.min_rvol, 8.0)
        intraday = _scale(float(row.get("intraday_change_pct", 0)), 0.5, 8.0)
        orb = 100.0 if row.get("orb_breakout") else 35.0
        first_drive = 100.0 if row.get("first_5m_bullish") and row.get("first_5m_close_above_vwap") else 30.0
        return _clamp((rvol * 0.35) + (intraday * 0.25) + (orb * 0.20) + (first_drive * 0.20))
