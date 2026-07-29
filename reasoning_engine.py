from __future__ import annotations

from config import settings


class TradeReasoningEngine:
    MODEL_NAME = "ReasoningModel-v1"

    def explain(self, row: dict) -> str:
        setup = str(row.get("setup_name", "Momentum setup"))
        symbol = str(row.get("symbol", ""))
        prob_raw = row.get("model_win_probability")
        expected_r_raw = row.get("model_expected_r")
        prob = float(prob_raw) if prob_raw is not None else None
        expected_r = float(expected_r_raw) if expected_r_raw is not None else None
        trigger = str(row.get("trigger_source", "level")).replace("_", " ")
        catalyst_score = float(row.get("catalyst_score", 0.0) or 0.0)
        clauses: list[str] = [f"{symbol} matches {setup}"]

        if row.get("ema_stack"):
            clauses.append("EMA 9/21/50 stack is aligned")
        if row.get("above_vwap"):
            clauses.append("price is holding above VWAP")
        if row.get("volume_trend"):
            clauses.append("volume is expanding")
        if row.get("market_trend"):
            clauses.append("SPY regime is supportive")
        if row.get("breakout_close_confirmed"):
            clauses.append(f"breakout is confirmed through {trigger}")
        if catalyst_score >= 60:
            clauses.append("news/catalyst context is supportive")

        if prob is None or expected_r is None:
            decision = "Predictive model artifact is not loaded, so heuristic AI is being used"
        elif prob >= settings.ai.model_min_win_probability and expected_r >= settings.ai.model_min_expected_r:
            decision = "Model strongly supports the trade"
        elif prob < settings.ai.model_min_win_probability:
            decision = "Model shows lower win probability, but the setup can still trade"
        else:
            decision = "Model shows lower expected R, but the setup can still trade"

        if prob is not None:
            clauses.append(f"predicted win probability {prob:.0%}")
        if expected_r is not None:
            clauses.append(f"expected return {expected_r:.2f}R")
        clauses.append(decision)
        return ". ".join(clauses) + "."
