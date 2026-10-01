from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from typing import Any


class OptionLLMSupervisor:
    """Advisory-only option supervisor.

    The supervisor can explain and audit option-engine decisions, but it never
    authorizes or submits orders. The deterministic engine remains the only
    execution path.
    """

    def __init__(
        self,
        enabled: bool = True,
        model: str = "gpt-4.1-mini",
        timeout_seconds: float = 2.5,
        api_key: str | None = None,
    ) -> None:
        self.enabled = enabled
        self.model = model
        self.timeout_seconds = max(float(timeout_seconds or 2.5), 0.5)
        self.api_key = api_key if api_key is not None else os.getenv("OPENAI_API_KEY", "")

    def review(self, context: dict[str, Any]) -> dict[str, Any]:
        started = time.perf_counter()
        fallback = self._rule_based_report(context)
        if not self.enabled:
            fallback["llm"] = self._llm_status("disabled", started, "OPTION_LLM_SUPERVISOR_ENABLED is false")
            return fallback
        if not self.api_key:
            fallback["llm"] = self._llm_status("rules_fallback", started, "OPENAI_API_KEY is not configured")
            return fallback

        try:
            llm_payload = self._call_openai(context, fallback)
            merged = self._merge_llm_payload(fallback, llm_payload)
            merged["llm"] = self._llm_status("openai", started, "LLM review completed")
            return merged
        except Exception as exc:
            fallback["llm"] = self._llm_status("rules_fallback", started, f"LLM review unavailable: {exc}")
            return fallback

    def _llm_status(self, mode: str, started: float, note: str) -> dict[str, Any]:
        return {
            "mode": mode,
            "model": self.model,
            "latencyMs": round((time.perf_counter() - started) * 1000.0, 2),
            "note": note,
        }

    def _rule_based_report(self, context: dict[str, Any]) -> dict[str, Any]:
        coverage = context.get("scanCoverage") or {}
        candidates = list(context.get("candidates") or [])
        plan_blocks = list(context.get("planBlocks") or [])
        no_candidate_rows = list(context.get("noCandidateRows") or [])
        trades = list(context.get("tradeHistory") or [])
        catalysts = list(context.get("catalysts") or [])
        positions = list(context.get("positions") or [])
        config = context.get("botConfig") or {}
        risk = context.get("riskConfig") or {}

        qualified = [row for row in candidates if bool(row.get("allowed") or row.get("option_rule_passed"))]
        entry_blocks = [row for row in candidates if row.get("option_rule_passed") is False or row.get("option_rule_rejection_reason")]
        recent_trades = sorted(
            trades,
            key=lambda row: str(row.get("opened_at") or row.get("entry_time") or row.get("created_at") or ""),
            reverse=True,
        )[:8]

        taken = [
            {
                "symbol": row.get("underlying_symbol") or row.get("symbol"),
                "contract": row.get("option_symbol") or row.get("selected_option_symbol"),
                "status": row.get("status"),
                "reason": self._short_reason(row.get("notes") or "Rule engine submitted the trade after all option gates passed."),
            }
            for row in recent_trades
        ]

        skipped = [
            {
                "symbol": row.get("symbol") or row.get("underlying") or "--",
                "stage": "Entry Rules",
                "reason": self._short_reason(row.get("option_rule_rejection_reason") or row.get("rejection_reason") or "Option entry logic blocked."),
            }
            for row in entry_blocks[:8]
        ]
        skipped.extend(
            {
                "symbol": row.get("symbol") or row.get("underlying") or "--",
                "stage": "Planner",
                "reason": self._short_reason(row.get("reason") or "Planner blocked this setup."),
            }
            for row in plan_blocks[:8]
        )
        skipped.extend(
            {
                "symbol": row.get("symbol") or "--",
                "stage": "Scanner",
                "reason": "No current setup row from the 5-minute EMA/VWAP scan.",
            }
            for row in no_candidate_rows[:8]
        )

        unusual = self._unusual_conditions(context, recent_trades, positions, plan_blocks)
        catalysts_summary = self._catalyst_summary(catalysts)
        weak_trades = self._weak_trade_review(trades)
        suggestions = self._suggestions(context, unusual, weak_trades)

        summary = (
            f"{coverage.get('watchlistCount', 0)} watched, "
            f"{coverage.get('candidateCount', len(candidates))} setup rows, "
            f"{coverage.get('qualifiedCount', len(qualified))} qualified, "
            f"{len(plan_blocks)} planner block(s), "
            f"{coverage.get('noCandidateCount', len(no_candidate_rows))} no-trigger symbol(s)."
        )

        return {
            "name": "LLM Supervisor Agent",
            "status": "Review Ready",
            "mode": "advisory_only",
            "updatedAt": datetime.now(timezone.utc).isoformat(),
            "summary": summary,
            "authority": {
                "canPlaceOrders": False,
                "canOverrideRules": False,
                "executionPath": "Rules engine only",
                "note": "LLM output is explanation and audit only. Orders still require deterministic option gates.",
            },
            "engineChecklist": [
                "Rules engine handles EMA/VWAP/ORB and approved trigger checks.",
                "Contract agent enforces long calls, delta cap, mid price, bid/ask, spread, and expected move.",
                "Risk agent enforces buying power, quantity, stop, target 1, and runner state.",
                "LLM supervisor explains, flags anomalies, summarizes catalysts, and suggests tuning only.",
            ],
            "taken": taken,
            "skipped": skipped,
            "unusual": unusual,
            "catalysts": catalysts_summary,
            "weakTrades": weak_trades,
            "suggestions": suggestions,
            "settingsSnapshot": {
                "approvalMode": config.get("approvalMode"),
                "contractPolicy": config.get("contractPolicy"),
                "deltaTarget": config.get("deltaTarget") or "default < 0.20",
                "expectedMove": config.get("expectedMove") or "default >= 3",
                "spreadFilter": config.get("spreadFilter") or "adaptive default",
                "contracts": risk.get("contractQuantity") or "amount based",
                "runnerLockStepPercent": risk.get("runnerLockStepPercent") or "default 50",
            },
        }

    def _unusual_conditions(
        self,
        context: dict[str, Any],
        recent_trades: list[dict[str, Any]],
        positions: list[dict[str, Any]],
        plan_blocks: list[dict[str, Any]],
    ) -> list[str]:
        coverage = context.get("scanCoverage") or {}
        bot_state = str((context.get("bot") or {}).get("state") or "").lower()
        unusual: list[str] = []
        if bot_state and bot_state != "running":
            unusual.append("Option bot is not running, so the supervisor is reviewing only the latest saved scan.")
        if coverage.get("watchlistCount") and coverage.get("noCandidateCount", 0) >= coverage.get("watchlistCount", 0) * 0.8:
            unusual.append("Most watched symbols have no live setup row; verify market data, session, and scan filters.")
        if coverage.get("qualifiedCount", 0) == 0 and coverage.get("candidateCount", 0) > 0:
            unusual.append("Scanner found setups, but none passed all option entry gates.")
        if any("chain" in str(row.get("reason", "")).lower() or "schwab" in str(row.get("reason", "")).lower() for row in plan_blocks):
            unusual.append("Option chain/data issue detected in planner blocks; check Schwab/TOS token and chain response.")
        if any("buying power" in str(row.get("reason", "")).lower() for row in plan_blocks):
            unusual.append("Buying power blocked at least one option ticket.")
        slow_submit = [
            row for row in recent_trades
            if self._float(row.get("broker_submit_ms")) and self._float(row.get("broker_submit_ms")) > 1000
        ]
        if slow_submit:
            unusual.append("At least one Alpaca submit call took over 1000 ms.")
        if any(self._float(row.get("pnl") or row.get("marked_pnl")) < 0 for row in positions):
            unusual.append("One or more open option positions are currently red.")
        return unusual or ["No unusual supervisor alerts from the latest scan context."]

    def _catalyst_summary(self, catalysts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        rows = []
        for item in catalysts[:6]:
            rows.append(
                {
                    "symbol": item.get("symbol") or "--",
                    "headline": self._short_reason(item.get("headline") or "No headline"),
                    "sentiment": item.get("sentiment") or "--",
                    "score": item.get("score"),
                }
            )
        return rows

    def _weak_trade_review(self, trades: list[dict[str, Any]]) -> list[dict[str, Any]]:
        weak = []
        for row in trades:
            pnl = self._float(row.get("pnl") if row.get("pnl") is not None else row.get("marked_pnl"))
            if pnl >= 0:
                continue
            weak.append(
                {
                    "symbol": row.get("underlying_symbol") or row.get("symbol"),
                    "contract": row.get("option_symbol") or row.get("selected_option_symbol"),
                    "pnl": round(pnl, 2),
                    "review": "Negative P/L trade. Check entry timing, spread, delta, and whether target/stop was followed.",
                }
            )
        return sorted(weak, key=lambda row: row["pnl"])[:5]

    def _suggestions(self, context: dict[str, Any], unusual: list[str], weak_trades: list[dict[str, Any]]) -> list[str]:
        coverage = context.get("scanCoverage") or {}
        suggestions: list[str] = []
        if coverage.get("qualifiedCount", 0) == 0:
            suggestions.append("Keep execution strict; review blocking reasons before lowering thresholds.")
        if coverage.get("noCandidateCount", 0) > coverage.get("candidateCount", 0):
            suggestions.append("Use the coverage panel to confirm symbols are being scanned before changing strategy rules.")
        if any("spread" in str(item).lower() for item in unusual):
            suggestions.append("Prefer percent spread filters for high-premium names and avoid hard dollar spread caps.")
        if weak_trades:
            suggestions.append("Review losing contracts for late entries, wide spreads, and whether stop/runner rules were followed.")
        suggestions.append("Do not let the LLM approve orders; keep it as an audit layer after deterministic gates.")
        return suggestions

    def _call_openai(self, context: dict[str, Any], fallback: dict[str, Any]) -> dict[str, Any]:
        prompt = {
            "role": "option_llm_supervisor",
            "instruction": (
                "You are an advisory-only option trading supervisor. You cannot place, approve, "
                "or override trades. Review the deterministic option engine context and return "
                "strict JSON with keys summary, unusual, catalysts, weakTrades, suggestions. "
                "Keep suggestions operational and never recommend bypassing rule gates."
            ),
            "fallback_report": fallback,
            "context": context,
        }
        body = json.dumps(
            {
                "model": self.model,
                "input": json.dumps(prompt, default=str),
                "max_output_tokens": 900,
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        text = self._extract_response_text(payload)
        return json.loads(text)

    def _merge_llm_payload(self, fallback: dict[str, Any], llm_payload: dict[str, Any]) -> dict[str, Any]:
        merged = dict(fallback)
        for key in ["summary", "unusual", "catalysts", "weakTrades", "suggestions"]:
            value = llm_payload.get(key)
            if value:
                merged[key] = value
        merged["authority"] = fallback["authority"]
        merged["mode"] = "advisory_only"
        return merged

    def _extract_response_text(self, payload: dict[str, Any]) -> str:
        if payload.get("output_text"):
            return str(payload["output_text"])
        for item in payload.get("output", []) or []:
            for content in item.get("content", []) or []:
                if content.get("type") in {"output_text", "text"} and content.get("text"):
                    return str(content["text"])
        raise ValueError("OpenAI response did not contain text output")

    def _short_reason(self, value: Any, limit: int = 180) -> str:
        text = " ".join(str(value or "").split())
        if len(text) <= limit:
            return text
        return f"{text[: limit - 3]}..."

    def _float(self, value: Any) -> float:
        try:
            if value is None or value == "":
                return 0.0
            return float(value)
        except Exception:
            return 0.0
