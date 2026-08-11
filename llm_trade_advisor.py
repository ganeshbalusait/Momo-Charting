from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import os
import time
from typing import Any
from urllib import error, request

import pandas as pd

from config import settings


@dataclass(slots=True)
class LLMTradeAdvice:
    llm_agent_mode: str
    llm_agent_model: str
    llm_agent_non_blocking: bool
    llm_advice: str
    llm_summary: str
    llm_strengths: list[str]
    llm_cautions: list[str]
    llm_tags: list[str]
    llm_confidence: float
    llm_rank_score: float
    llm_latency_ms: int
    llm_error: str


class LLMTradeAdvisor:
    """Non-blocking trade reviewer.

    This class can annotate trades, but it must never decide approval, sizing,
    stops, targets, exits, or order routing. Those stay in scanner/strategy/risk.
    """

    def __init__(self) -> None:
        self.enabled = settings.ai.llm_agent_enabled
        self.external_enabled = settings.ai.llm_agent_external_enabled
        self.provider = settings.ai.llm_agent_provider
        self.model = settings.ai.llm_agent_model
        self.max_candidates = max(int(settings.ai.llm_agent_max_candidates), 0)
        self.timeout_seconds = max(float(settings.ai.llm_agent_timeout_seconds), 0.2)

    def enrich_frame(self, frame: pd.DataFrame | None) -> pd.DataFrame:
        if frame is None or frame.empty or not self.enabled:
            return frame

        enriched = frame.copy()
        rows: list[dict[str, Any]] = []
        for index, row in enumerate(enriched.to_dict("records")):
            advice = self.review(row, allow_external=index < self.max_candidates)
            rows.append({**row, **asdict(advice)})
        return pd.DataFrame(rows)

    def review(self, row: dict[str, Any], allow_external: bool = True) -> LLMTradeAdvice:
        start = time.perf_counter()
        local_advice = self._local_review(row)
        if not (allow_external and self._can_call_external()):
            return local_advice

        try:
            external = self._openai_review(row)
            if external is None:
                return local_advice
            external.llm_latency_ms = int((time.perf_counter() - start) * 1000)
            return external
        except Exception as exc:
            fallback = self._local_review(row)
            fallback.llm_error = str(exc)[:240]
            fallback.llm_latency_ms = int((time.perf_counter() - start) * 1000)
            return fallback

    def _can_call_external(self) -> bool:
        return bool(
            self.external_enabled
            and self.provider == "openai"
            and os.getenv("OPENAI_API_KEY")
        )

    def _local_review(self, row: dict[str, Any]) -> LLMTradeAdvice:
        strengths: list[str] = []
        cautions: list[str] = []
        tags: list[str] = []

        if row.get("ema_stack"):
            strengths.append("EMA 9/21/50 bullish stack")
        if row.get("above_vwap"):
            strengths.append("price above VWAP")
        if row.get("volume_trend"):
            strengths.append("volume increasing")
        if row.get("ema9_retest_5m"):
            strengths.append("5m EMA9 retest held")
        if row.get("close_near_high"):
            strengths.append("candle closed near high")
        if row.get("breakout_close_confirmed"):
            strengths.append(f"breakout confirmed via {row.get('trigger_source') or 'trigger'}")
        if row.get("market_trend"):
            strengths.append("market confirmation supportive")
        if row.get("stock_all_conditions_pass"):
            strengths.append("1H close and 4H volume gates passed")

        expected_r = self._float_or_none(row.get("model_expected_r"))
        win_probability = self._float_or_none(row.get("model_win_probability"))
        intraday_change = self._float_or_none(row.get("intraday_change_pct")) or 0.0
        extension = self._float_or_none(row.get("extension_above_ema9_pct"))

        if expected_r is not None and expected_r < 0:
            cautions.append("model expected R is negative")
        elif expected_r is not None and expected_r < 0.05:
            cautions.append("model expected R is low")
        if win_probability is not None and win_probability < 0.55:
            cautions.append("model win probability is below 55%")
        if extension is not None and extension > 5:
            cautions.append("price is extended from EMA9; watch chase risk")
        if intraday_change > 8:
            cautions.append("large intraday move; entry may be late")
        if not row.get("market_trend"):
            cautions.append("market confirmation is weak")

        setup_name = str(row.get("setup_name") or "")
        if "ORB" in setup_name:
            tags.append("orb_breakout")
        if "Premarket High" in setup_name:
            tags.append("premarket_high_break")
        if "Previous Day High" in setup_name:
            tags.append("previous_day_high_break")
        if row.get("ema9_retest_5m"):
            tags.append("ema9_retest")
        if row.get("volume_trend"):
            tags.append("volume_confirmation")
        if cautions:
            tags.append("review_risk")

        final_score = self._float_or_none(row.get("final_score")) or self._float_or_none(row.get("score")) or 0.0
        confidence = max(0.0, min(100.0, final_score))
        rank_score = max(0.0, min(100.0, final_score + min(len(strengths) * 2, 12) - min(len(cautions) * 4, 20)))
        advice = "Strong context" if rank_score >= 80 else "Qualified context" if rank_score >= 60 else "Mixed context"
        summary = (
            f"{row.get('symbol', 'Symbol')} {advice.lower()}: "
            f"{setup_name or 'momentum setup'} with {len(strengths)} strength(s)"
            f" and {len(cautions)} caution(s). Advisor is informational only."
        )

        return LLMTradeAdvice(
            llm_agent_mode="local_non_blocking",
            llm_agent_model="LocalTradeAdvisor-v1",
            llm_agent_non_blocking=True,
            llm_advice=advice,
            llm_summary=summary,
            llm_strengths=strengths[:8],
            llm_cautions=cautions[:6],
            llm_tags=tags[:8],
            llm_confidence=round(confidence, 2),
            llm_rank_score=round(rank_score, 2),
            llm_latency_ms=0,
            llm_error="",
        )

    def _openai_review(self, row: dict[str, Any]) -> LLMTradeAdvice | None:
        payload = {
            "model": self.model,
            "temperature": 0.1,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a non-blocking trading journal advisor. "
                        "Never approve, reject, size, enter, exit, or override risk. "
                        "Return only compact JSON with advice, summary, strengths, cautions, tags, confidence, rank_score."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(self._trade_packet(row), default=str),
                },
            ],
            "response_format": {"type": "json_object"},
        }
        base_url = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
        req = request.Request(
            f"{base_url}/chat/completions",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as response:
                raw = json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            raise RuntimeError(f"LLM advisor HTTP {exc.code}") from exc

        content = raw.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        parsed = json.loads(content)
        local = self._local_review(row)
        return LLMTradeAdvice(
            llm_agent_mode="openai_non_blocking",
            llm_agent_model=self.model,
            llm_agent_non_blocking=True,
            llm_advice=str(parsed.get("advice") or local.llm_advice)[:80],
            llm_summary=str(parsed.get("summary") or local.llm_summary)[:500],
            llm_strengths=self._string_list(parsed.get("strengths"))[:8] or local.llm_strengths,
            llm_cautions=self._string_list(parsed.get("cautions"))[:6] or local.llm_cautions,
            llm_tags=self._string_list(parsed.get("tags"))[:8] or local.llm_tags,
            llm_confidence=round(self._bounded_float(parsed.get("confidence"), local.llm_confidence), 2),
            llm_rank_score=round(self._bounded_float(parsed.get("rank_score"), local.llm_rank_score), 2),
            llm_latency_ms=0,
            llm_error="",
        )

    def _trade_packet(self, row: dict[str, Any]) -> dict[str, Any]:
        keys = [
            "symbol",
            "setup_name",
            "strategy_family",
            "trigger_source",
            "entry",
            "stop_loss",
            "target",
            "score",
            "final_score",
            "model_win_probability",
            "model_expected_r",
            "rvol",
            "intraday_change_pct",
            "above_vwap",
            "ema_stack",
            "volume_trend",
            "ema9_retest_5m",
            "close_near_high",
            "breakout_close_confirmed",
            "one_hour_close_pass",
            "four_hour_volume_pass",
            "stock_all_conditions_pass",
            "market_trend",
            "policy_status",
            "rejection_reason",
            "rationale",
        ]
        return {key: row.get(key) for key in keys}

    @staticmethod
    def _float_or_none(value: Any) -> float | None:
        try:
            if value is None or value == "":
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bounded_float(value: Any, fallback: float) -> float:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            parsed = fallback
        return max(0.0, min(100.0, parsed))

    @staticmethod
    def _string_list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []
