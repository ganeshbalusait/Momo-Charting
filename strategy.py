from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, time
from typing import Iterable

import pandas as pd
from zoneinfo import ZoneInfo

from config import EASTERN_TZ, settings


@dataclass(slots=True)
class StrategyCandidate:
    symbol: str
    strategy_family: str
    setup_name: str
    trigger_source: str
    last_price: float
    rvol: float
    score: int
    rule_score: int
    ml_score: int
    catalyst_score: int
    regime_score: int
    liquidity_score: int
    final_score: int
    ai_confidence: int
    ai_model_name: str
    model_win_probability: float | None
    model_expected_r: float | None
    ai_decision: str
    entry: float
    stop_loss: float
    target: float
    risk_per_share: float
    rationale: str
    policy_name: str
    policy_status: str
    execution_route: str
    trade_blueprint: str
    allowed: bool
    rejection_reason: str = ""


class StrategyEngine:
    _elite_setups = {
        "EMA + VWAP + ORB",
        "EMA + VWAP + Previous Day High",
        "EMA + VWAP + Premarket High",
        "EMA + VWAP",
        "EMA + VWAP + Premarket Low Above Candle",
        "EMA + VWAP + Previous Day Low Above Candle",
    }

    def __init__(self) -> None:
        self._tz = ZoneInfo(EASTERN_TZ)
        start_hour, start_minute = [int(part) for part in settings.trading.trade_start_after_et.split(":", maxsplit=1)]
        hour, minute = [int(part) for part in settings.trading.no_new_trades_after_et.split(":", maxsplit=1)]
        self._start = time(hour=start_hour, minute=start_minute)
        self._cutoff = time(hour=hour, minute=minute)

    def build_trade_candidates(
        self,
        scan_results: pd.DataFrame,
        existing_symbols: Iterable[str] | None = None,
        symbol_memory: pd.DataFrame | None = None,
        catalysts: pd.DataFrame | None = None,
        now: datetime | None = None,
    ) -> list[StrategyCandidate]:
        existing = {symbol.upper() for symbol in (existing_symbols or [])}
        excluded = {symbol.upper() for symbol in settings.trading.excluded_symbols}
        current = now.astimezone(self._tz) if now else datetime.now(tz=self._tz)
        memory_map = self._symbol_memory_map(symbol_memory)
        catalyst_map = self._catalyst_map(catalysts)
        candidates: list[StrategyCandidate] = []
        execution_route = self._execution_route(current)

        for row in scan_results.to_dict("records"):
            allowed = True
            reason = ""
            rule_score = int(row["score"])
            ml_score = int(round(float(row.get("ml_score", self._ml_score(row, memory_map.get(str(row["symbol"]).upper()))))))
            catalyst_score = int(round(float(row.get("catalyst_score", self._catalyst_score(catalyst_map.get(str(row["symbol"]).upper()))))))
            regime_score = int(round(float(row.get("regime_score", self._regime_score(row)))))
            liquidity_score = self._liquidity_score(row)
            final_score = int(round(float(row.get("ai_trade_score", self._final_score(
                rule_score=rule_score,
                ml_score=ml_score,
                catalyst_score=catalyst_score,
                regime_score=regime_score,
                liquidity_score=liquidity_score,
            )))))
            ai_confidence = int(round(float(row.get("ai_confidence", final_score))))
            model_win_probability = float(row["model_win_probability"]) if row.get("model_win_probability") is not None else None
            model_expected_r = float(row["model_expected_r"]) if row.get("model_expected_r") is not None else None
            win_probability_text = (
                f"Win probability {model_win_probability:.0%}"
                if model_win_probability is not None
                else ""
            )
            expected_r_text = (
                f"Expected R {model_expected_r:.2f}"
                if model_expected_r is not None
                else ""
            )
            setup_name = str(row.get("setup_name", ""))

            if not bool(row.get("session_change_pass", False)):
                allowed = False
                reason = f"Live Change % below {settings.scanner.min_session_change_pct:.2f}%"
            elif not bool(row.get("cloud_alignment_pass", False)):
                allowed = False
                reason = str(row.get("cloud_alignment_action") or "Live 4H and 5M clouds are not bullish")
            elif (
                row.get("tos_rvol_5m_early_alert")
                and not row.get("tos_rvol_any_pass")
                and int(row.get("fast_momentum_score") or 0) < 2
            ):
                allowed = False
                reason = "5m RVOL early alert only; 15m or higher confirmation required"
            elif str(row["symbol"]).upper() in excluded:
                allowed = False
                reason = "ETF / excluded symbol trading disabled"
            elif not settings.trading.trade_all_sessions and current.time() < self._start:
                allowed = False
                reason = f"New entries enabled after {settings.trading.trade_start_after_et} ET"
            elif not settings.trading.trade_all_sessions and current.time() >= self._cutoff:
                allowed = False
                reason = f"New entries disabled after {settings.trading.no_new_trades_after_et} ET"
            elif row["symbol"].upper() in existing:
                allowed = False
                reason = "Symbol already has an open position or pending order"

            rationale = ", ".join(
                feature
                for feature, active in [
                    ("RVOL strong", row["rvol"] >= settings.scanner.min_rvol),
                    ("Volume accelerating", row["volume_trend"]),
                    (
                        f"1H close change {float(row.get('one_hour_close_change_pct') or 0.0):.2f}%",
                        bool(row.get("stock_signal_gate_active", True) and row.get("one_hour_close_pass", False)),
                    ),
                    (
                        f"4H volume change {float(row.get('four_hour_volume_change_pct') or 0.0):.2f}%",
                        bool(row.get("stock_signal_gate_active", True) and row.get("four_hour_volume_pass", False)),
                    ),
                    (
                        "1H/4H stock gates skipped outside regular market hours",
                        not bool(row.get("stock_signal_gate_active", True)),
                    ),
                    ("EMA stack intact", row["ema_stack"]),
                    ("Live 4H cloud bullish", row.get("four_hour_cloud_bullish", False)),
                    ("Live 4H + 5M bullish alignment", row.get("cloud_alignment_pass", False)),
                    ("Above VWAP", row["above_vwap"]),
                    ("Intraday expansion", row.get("intraday_change_pct", 0) >= settings.scanner.min_intraday_change_pct),
                    (
                        f"Live Change % {float(row.get('session_change_pct') or 0.0):.2f}%",
                        bool(row.get("session_change_pass", False)),
                    ),
                    ("Close near high", row.get("close_near_high", False)),
                    ("5m EMA9 retest held", row.get("ema9_retest_5m", False)),
                    ("First 5m bullish", row.get("first_5m_bullish", False)),
                    ("First 5m above VWAP", row.get("first_5m_close_above_vwap", False)),
                    ("ORB confirmed", row.get("orb_breakout", False)),
                    (f"Setup: {row.get('setup_name', 'Momentum')}", bool(row.get("setup_name"))),
                    (f"Breakout via {row.get('trigger_source', 'level')}", row["breakout_close_confirmed"]),
                    (f"ML score {ml_score}", True),
                    (f"Catalyst score {catalyst_score} (information only)", True),
                    (f"Rule score {rule_score} (advisory)", True),
                    (f"Regime score {regime_score} (advisory)", True),
                    (f"Liquidity score {liquidity_score}", True),
                    (f"Final score {final_score}", True),
                    (f"AI confidence {ai_confidence}", True),
                    (win_probability_text, model_win_probability is not None),
                    (expected_r_text, model_expected_r is not None),
                    (str(row.get("ai_reasoning", "")), bool(row.get("ai_reasoning"))),
                    (f"AI decision {row.get('ai_decision', 'Unranked')}", True),
                ]
                if active
            )
            policy_status = "Approved" if allowed else "Rejected"
            trade_blueprint = (
                f"Setup {row.get('setup_name', 'Momentum')} -> "
                f"Policy {policy_status} -> "
                f"Entry {float(row['entry']):.2f} / Stop {float(row['stop_loss']):.2f} / Target {float(row['target']):.2f} -> "
                f"Route {execution_route}"
            )

            candidates.append(
                StrategyCandidate(
                    symbol=row["symbol"],
                    strategy_family="Momentum + Price Action Trend",
                    setup_name=setup_name,
                    trigger_source=str(row.get("trigger_source", "")),
                    last_price=float(row.get("last_price", row.get("entry", 0))),
                    rvol=float(row.get("rvol", 0)),
                    score=final_score,
                    rule_score=rule_score,
                    ml_score=ml_score,
                    catalyst_score=catalyst_score,
                    regime_score=regime_score,
                    liquidity_score=liquidity_score,
                    final_score=final_score,
                    ai_confidence=ai_confidence,
                    ai_model_name=str(row.get("ai_model_name", "HeavyTradingModel-v2")),
                    model_win_probability=model_win_probability,
                    model_expected_r=model_expected_r,
                    ai_decision=str(row.get("ai_decision", "Unranked")),
                    entry=float(row["entry"]),
                    stop_loss=float(row["stop_loss"]),
                    target=float(row["target"]),
                    risk_per_share=float(row["risk_per_share"]),
                    rationale=rationale or "No confirming factors",
                    policy_name="Signal Selector -> Policy Critic -> Risk Governor -> Execution Router",
                    policy_status=policy_status,
                    execution_route=execution_route,
                    trade_blueprint=trade_blueprint,
                    allowed=allowed,
                    rejection_reason=reason,
                )
            )
        return candidates

    def candidates_to_frame(self, candidates: list[StrategyCandidate]) -> pd.DataFrame:
        if not candidates:
            return pd.DataFrame()
        return pd.DataFrame([asdict(candidate) for candidate in candidates])

    def _symbol_memory_map(self, symbol_memory: pd.DataFrame | None) -> dict[str, dict]:
        if symbol_memory is None or symbol_memory.empty:
            return {}
        records = {}
        for row in symbol_memory.to_dict("records"):
            records[str(row.get("symbol", "")).upper()] = row
        return records

    def _catalyst_map(self, catalysts: pd.DataFrame | None) -> dict[str, dict]:
        if catalysts is None or catalysts.empty:
            return {}
        grouped: dict[str, dict] = {}
        for row in catalysts.to_dict("records"):
            symbol = str(row.get("symbol", "")).upper()
            if not symbol:
                continue
            if symbol not in grouped or float(row.get("score", 0)) > float(grouped[symbol].get("score", 0)):
                grouped[symbol] = row
        return grouped

    def _ml_score(self, row: dict, memory_row: dict | None) -> int:
        base = 50
        if memory_row:
            confidence = float(memory_row.get("confidence", 50))
            observations = min(int(memory_row.get("observations", 0)), 20)
            base = int((confidence * 0.8) + (observations * 1.0))

        feature_boost = 0
        if row.get("rvol", 0) >= 3:
            feature_boost += 8
        if row.get("close_near_high"):
            feature_boost += 6
        if row.get("volume_trend"):
            feature_boost += 5
        return max(30, min(95, base + feature_boost))

    def _catalyst_score(self, catalyst_row: dict | None) -> int:
        if not catalyst_row:
            return 45
        base = int(float(catalyst_row.get("score", 1)) * 25)
        sentiment = str(catalyst_row.get("sentiment", "")).lower()
        if sentiment == "strong":
            base += 15
        elif sentiment == "positive":
            base += 8
        elif sentiment == "negative":
            base -= 15
        return max(20, min(95, base))

    def _regime_score(self, row: dict) -> int:
        score = 35
        if row.get("above_vwap"):
            score += 10
        if row.get("ema_stack"):
            score += 10
        if row.get("intraday_change_pct", 0) >= settings.scanner.min_intraday_change_pct:
            score += 10
        if row.get("breakout_close_confirmed"):
            score += 10
        return max(20, min(95, score))

    def _liquidity_score(self, row: dict) -> int:
        avg_volume = float(row.get("average_volume", 0))
        price = float(row.get("last_price", 0))
        rvol = float(row.get("rvol", 0))
        score = 30
        if avg_volume >= 1_000_000:
            score += 20
        if avg_volume >= 5_000_000:
            score += 15
        if rvol >= 2.5:
            score += 15
        if rvol >= 4.0:
            score += 10
        if price >= 20:
            score += 5
        return max(20, min(95, score))

    def _final_score(
        self,
        rule_score: int,
        ml_score: int,
        catalyst_score: int,
        regime_score: int,
        liquidity_score: int,
    ) -> int:
        # Catalyst/news remains available in the candidate explanation, but it
        # must never change qualification, ranking, sizing, or execution.
        _ = catalyst_score
        execution_weight = 0.35 + 0.25 + 0.15 + 0.10
        final_score = (
            (0.35 * rule_score)
            + (0.25 * ml_score)
            + (0.15 * regime_score)
            + (0.10 * liquidity_score)
        ) / execution_weight
        return int(round(max(0, min(100, final_score))))

    def _allow_low_expected_r(
        self,
        row: dict,
        setup_name: str,
        model_win_probability: float | None,
    ) -> bool:
        if model_win_probability is None or model_win_probability < 0.55:
            return False
        if setup_name not in self._elite_setups:
            return False
        confirmations = [
            bool(row.get("above_vwap")),
            bool(row.get("ema_stack")),
            bool(row.get("volume_trend")),
            bool(row.get("close_near_high")),
            bool(row.get("ema9_retest_5m")),
        ]
        breakout_ready = bool(
            row.get("orb_breakout")
            or row.get("breakout_close_confirmed")
            or row.get("trigger_source") in {
                "previous_day_high",
                "premarket_high",
                "premarket_low_reclaim",
                "previous_day_low_reclaim",
            }
        )
        return sum(confirmations) >= 4 and breakout_ready

    def _execution_route(self, current: datetime) -> str:
        if settings.trading.trade_all_sessions:
            if time(9, 30) <= current.time() < time(16, 0) and current.weekday() < 5:
                return "Market Entry / Core Session"
            return "Extended-Hours Limit / All Sessions"
        return "Market Entry / Core Session"
