from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timedelta
import json
import threading
from uuid import uuid4

import pandas as pd
from zoneinfo import ZoneInfo

from ai_ensemble import HeavyTradingModel
from config import EASTERN_TZ, settings
from data.alpaca_client import AlpacaClient
from database.repository import TradingRepository
from indicators import ema
from llm_trade_advisor import LLMTradeAdvisor
from risk_manager import RiskManager
from scanner import MomentumScanner
from strategy import StrategyEngine


@dataclass(slots=True)
class TraderStatus:
    account_equity: float
    cash: float
    last_equity: float
    daily_change: float
    daily_change_pct: float
    buying_power: float
    deployed_capital: float
    daily_pnl: float
    trades_today: int
    open_positions: int
    open_orders: int
    account_mode: str


class AlpacaPaperTrader:
    def __init__(
        self,
        client: AlpacaClient | None = None,
        scanner: MomentumScanner | None = None,
        strategy: StrategyEngine | None = None,
        risk_manager: RiskManager | None = None,
        repository: TradingRepository | None = None,
    ) -> None:
        self.client = client or AlpacaClient()
        self.scanner = scanner or MomentumScanner(client=self.client)
        self.strategy = strategy or StrategyEngine()
        self.risk_manager = risk_manager or RiskManager()
        self.repository = repository or TradingRepository()
        self.ai_model = HeavyTradingModel(repository=self.repository)
        self.llm_advisor = LLMTradeAdvisor()
        self.last_scan_results = pd.DataFrame()
        self.last_candidate_frame = pd.DataFrame()
        self._entry_execution_lock = threading.RLock()
        self._manage_open_trades_lock = threading.Lock()
        self._tz = ZoneInfo(EASTERN_TZ)

    def get_status(self) -> TraderStatus:
        profile_id = self.client.credentials.profile_id
        account = None
        positions = []
        open_orders = []
        try:
            account = self.client.get_account()
        except Exception:
            account = None
        try:
            positions = self.client.get_positions()
        except Exception:
            positions = []
        try:
            open_orders = self.client.get_open_orders()
        except Exception:
            open_orders = []

        equity = self._safe_float(getattr(account, "equity", None), 0.0)
        cash = self._safe_float(
            getattr(account, "cash", None),
            self._safe_float(getattr(account, "non_marginable_buying_power", None), 0.0),
        )
        last_equity = self._safe_float(getattr(account, "last_equity", None), equity)
        daily_change = round(equity - last_equity, 2)
        daily_change_pct = round((daily_change / last_equity) * 100, 2) if last_equity else 0.0
        deployed_capital = round(
            sum(
                self._safe_float(getattr(position, "avg_entry_price", None), 0.0)
                * abs(self._safe_float(getattr(position, "qty", None), 0.0))
                for position in positions
            ),
            2,
        )
        return TraderStatus(
            account_equity=equity,
            cash=cash,
            last_equity=last_equity,
            daily_change=daily_change,
            daily_change_pct=daily_change_pct,
            buying_power=self._safe_float(getattr(account, "buying_power", None), 0.0),
            deployed_capital=deployed_capital,
            daily_pnl=self.repository.daily_pnl(profile_id=profile_id),
            trades_today=self.repository.trades_today_count(profile_id=profile_id),
            open_positions=len(positions),
            open_orders=len(open_orders),
            account_mode="paper" if self.client.is_paper else "live",
        )

    def _safe_float(self, value, fallback: float = 0.0) -> float:
        try:
            if value is None:
                return float(fallback)
            text = str(value).strip()
            if not text or text.lower() == "none":
                return float(fallback)
            numeric = float(value)
            if pd.isna(numeric):
                return float(fallback)
            return numeric
        except Exception:
            return float(fallback)

    def fetch_open_positions_frame(self) -> pd.DataFrame:
        try:
            positions = self.client.get_positions()
        except Exception:
            return pd.DataFrame()
        if not positions:
            return pd.DataFrame()

        open_trades = self.repository.get_trade_history(limit=500, profile_id=self.client.credentials.profile_id)
        trade_map: dict[str, dict] = {}
        if not open_trades.empty:
            normalized = open_trades.copy()
            normalized["closed_at"] = normalized.get("closed_at")
            normalized = normalized[normalized["closed_at"].isna()]
            ordered = normalized.sort_values("opened_at", ascending=False)
            for row in ordered.to_dict("records"):
                symbol = str(row.get("symbol", "")).upper()
                if not symbol or symbol in trade_map:
                    continue
                analysis = self._trade_plan_state(row)
                trade_map[symbol] = {
                    "setup_name": row.get("setup_name"),
                    "strategy_family": row.get("strategy_family"),
                    "trigger_source": row.get("trigger_source"),
                    "entry_price_logged": row.get("entry_price"),
                    "stop_price_logged": row.get("stop_price"),
                    "target_price_logged": analysis.get("take_profit_1") or row.get("target_price"),
                    "entry_time": row.get("opened_at"),
                    "exit_time": row.get("closed_at"),
                    "model_win_probability": analysis.get("model_win_probability"),
                    "model_expected_r": analysis.get("model_expected_r"),
                    "llm_advice": analysis.get("llm_advice"),
                    "llm_summary": analysis.get("llm_summary"),
                    "llm_rank_score": analysis.get("llm_rank_score"),
                    "llm_agent_mode": analysis.get("llm_agent_mode"),
                    "llm_agent_non_blocking": analysis.get("llm_agent_non_blocking", True),
                }

        rows = []
        for position in positions:
            symbol = str(position.symbol).upper()
            trade_meta = trade_map.get(symbol, {})
            rows.append(
                {
                    "symbol": symbol,
                    "qty": float(position.qty),
                    "avg_entry_price": float(position.avg_entry_price),
                    "current_price": float(position.current_price),
                    "market_value": float(position.market_value),
                    "unrealized_pl": float(position.unrealized_pl),
                    "unrealized_plpc": float(position.unrealized_plpc) * 100,
                    "setup_name": trade_meta.get("setup_name"),
                    "strategy_family": trade_meta.get("strategy_family"),
                    "model_win_probability": trade_meta.get("model_win_probability"),
                    "model_expected_r": trade_meta.get("model_expected_r"),
                    "llm_advice": trade_meta.get("llm_advice"),
                    "llm_summary": trade_meta.get("llm_summary"),
                    "llm_rank_score": trade_meta.get("llm_rank_score"),
                    "llm_agent_mode": trade_meta.get("llm_agent_mode"),
                    "llm_agent_non_blocking": trade_meta.get("llm_agent_non_blocking", True),
                    "trigger_source": trade_meta.get("trigger_source"),
                    "entry_price_logged": trade_meta.get("entry_price_logged"),
                    "stop_price_logged": trade_meta.get("stop_price_logged"),
                    "target_price_logged": trade_meta.get("target_price_logged"),
                    "entry_time": trade_meta.get("entry_time"),
                    "exit_time": trade_meta.get("exit_time"),
                }
            )
        return pd.DataFrame(rows)

    def close_position(self, symbol: str) -> dict:
        target_symbol = str(symbol or "").strip().upper()
        if not target_symbol:
            return {"status": "error", "message": "Symbol is required."}

        try:
            positions = {position.symbol.upper(): position for position in self.client.get_positions()}
        except Exception as exc:
            return {"status": "error", "message": f"Unable to reach Alpaca right now: {exc}"}
        position = positions.get(target_symbol)
        if not position:
            return {"status": "missing", "message": f"No open position found for {target_symbol}."}

        qty = float(position.qty)
        current_price = float(position.current_price)
        session_name = self._session_name()
        exit_order_id = f"manual-exit-{target_symbol}-{uuid4().hex[:8]}"

        if session_name == "Core":
            self.client.submit_market_exit_order(
                symbol=target_symbol,
                qty=qty,
                client_order_id=exit_order_id,
            )
        else:
            self.client.submit_extended_hours_limit_order(
                symbol=target_symbol,
                qty=qty,
                limit_price=self._marketable_extended_exit_price(current_price),
                side="sell",
                client_order_id=exit_order_id,
            )

        open_trades = self.repository.get_open_trades(profile_id=self.client.credentials.profile_id)
        if not open_trades.empty and "symbol" in open_trades.columns:
            matching = open_trades[open_trades["symbol"].astype(str).str.upper() == target_symbol]
            for _, row in matching.iterrows():
                pnl = round((current_price - float(row["entry_price"])) * qty, 2)
                row_dict = row.to_dict()
                plan = self._trade_plan_state(row_dict)
                entry_price = float(row_dict.get("entry_price") or 0)
                stop_price = float(row_dict.get("stop_price") or 0)
                self.repository.update_trade_plan(
                    client_order_id=row["client_order_id"],
                    notes="manual_close_requested",
                    analysis_json=json.dumps(
                        {
                            **plan,
                            "runner_exit_price": round(current_price, 2),
                            "runner_exit_reason": "manual_close_requested",
                            "runner_stop_locked_pct": round(((stop_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 0.0,
                        },
                        default=str,
                    ),
                )
                self.repository.update_trade_status(
                    client_order_id=row["client_order_id"],
                    status="manual_exit_pending",
                    pnl=pnl,
                    notes="manual_close_requested",
                )

        self.repository.log_bot_event(
            "manual_close",
            f"Manual close submitted for {target_symbol} in {session_name}.",
            json.dumps(
                {
                    "symbol": target_symbol,
                    "session": session_name,
                    "qty": qty,
                    "current_price": current_price,
                    "client_order_id": exit_order_id,
                }
            ),
        )
        return {
            "status": "submitted",
            "message": f"Manual close submitted for {target_symbol}.",
            "symbol": target_symbol,
            "qty": qty,
            "route": "market" if session_name == "Core" else "extended_hours_limit",
        }

    def close_all_positions(self) -> dict:
        try:
            positions = list(self.client.get_positions())
        except Exception as exc:
            return {"status": "error", "message": f"Unable to reach Alpaca right now: {exc}", "submitted": []}

        if not positions:
            return {"status": "missing", "message": "No open positions found.", "submitted": []}

        submitted: list[dict] = []
        errors: list[dict] = []
        for position in positions:
            symbol = str(position.symbol).upper()
            try:
                result = self.close_position(symbol)
                if result.get("status") == "submitted":
                    submitted.append(
                        {
                            "symbol": symbol,
                            "qty": result.get("qty"),
                            "route": result.get("route"),
                        }
                    )
                else:
                    errors.append({"symbol": symbol, "message": result.get("message", "Unable to close position.")})
            except Exception as exc:
                errors.append({"symbol": symbol, "message": str(exc)})

        if submitted:
            return {
                "status": "submitted",
                "message": f"Submitted close requests for {len(submitted)} positions.",
                "submitted": submitted,
                "errors": errors,
            }
        return {
            "status": "error",
            "message": errors[0]["message"] if errors else "No close requests were submitted.",
            "submitted": [],
            "errors": errors,
        }

    def run_scan_and_prepare_trades(
        self,
        symbols: list[str] | None = None,
        rvol_confirmation_thresholds: dict[str, float] | None = None,
        trade_overrides: dict[str, dict] | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        scan_results = self.scanner.run(
            symbols=symbols,
            ignore_one_hour_price_change=True,
            ignore_four_hour_price_change=True,
            ignore_four_hour_volume=True,
            rvol_confirmation_thresholds=rvol_confirmation_thresholds,
        )
        scan_results = self.ai_model.score_frame(scan_results)
        existing_symbols = self._existing_symbols()
        symbol_memory = self.repository.get_symbol_memory(limit=500)
        try:
            catalysts = self.repository.get_recent_catalysts(limit=500)
        except Exception:
            # Catalyst/news is information-only. A stale or unavailable news
            # store must never block the technical scan or order path.
            catalysts = pd.DataFrame()
        candidates = self.strategy.build_trade_candidates(
            scan_results,
            existing_symbols=existing_symbols,
            symbol_memory=symbol_memory,
            catalysts=catalysts,
        )
        candidate_frame = self.strategy.candidates_to_frame(candidates)
        if not candidate_frame.empty and not scan_results.empty:
            extra_columns = [column for column in scan_results.columns if column not in candidate_frame.columns and column != "symbol"]
            if extra_columns:
                candidate_frame = candidate_frame.merge(
                    scan_results[["symbol", *extra_columns]],
                    on="symbol",
                    how="left",
                )
        if not candidate_frame.empty and trade_overrides:
            for symbol, overrides in trade_overrides.items():
                symbol_mask = candidate_frame["symbol"].astype(str).str.upper() == str(symbol).upper()
                for field, value in overrides.items():
                    candidate_frame.loc[symbol_mask, field] = value
        candidate_frame = self.llm_advisor.enrich_frame(candidate_frame)
        self.last_scan_results = scan_results.copy()
        self.last_candidate_frame = candidate_frame.copy()
        self._record_learning_observations(
            scan_results,
            source="stock_bot_scanner",
            product="stock",
        )
        self._record_learning_observations(
            candidate_frame,
            source="stock_bot_candidate",
            product="stock",
        )
        self.repository.log_scan_run(
            symbols_scanned=len(self.scanner.settings.default_universe),
            candidates_found=len(candidate_frame),
            top_symbol=scan_results.iloc[0]["symbol"] if not scan_results.empty else None,
            notes="phase2_scan",
        )
        return scan_results, candidate_frame

    def execute_best_candidate(
        self,
        symbols: list[str] | None = None,
        rvol_confirmation_thresholds: dict[str, float] | None = None,
        trade_overrides: dict[str, dict] | None = None,
    ) -> dict:
        scan_results, candidate_frame = self.run_scan_and_prepare_trades(
            symbols,
            rvol_confirmation_thresholds,
            trade_overrides,
        )
        if candidate_frame.empty:
            return {"status": "skipped", "message": "No eligible trade candidates found."}

        eligible = candidate_frame[candidate_frame["allowed"]]
        if eligible.empty:
            top_blocked = candidate_frame.iloc[0]
            return {"status": "blocked", "message": top_blocked["rejection_reason"] or "No candidates passed strategy rules."}

        top = eligible.sort_values(["final_score", "entry"], ascending=[False, False]).iloc[0]
        status = self.get_status()
        decision = self.risk_manager.can_open_new_trade(
            equity=status.account_equity,
            daily_pnl=status.daily_pnl,
            trades_today=status.trades_today,
            risk_per_share=float(top["risk_per_share"]),
            entry_price=float(top["entry"]),
            buying_power=status.buying_power,
            deployed_capital=status.deployed_capital,
        )
        if not decision.allowed:
            return {"status": "blocked", "message": decision.reason}

        client_order_id = f"momentum-{top['symbol']}-{uuid4().hex[:8]}"
        if not self.client.is_paper and not settings.allow_live_trading:
            return {"status": "blocked", "message": "Live mode is configured but ALLOW_LIVE_TRADING is false. No live orders were sent."}
        session_name = self._session_name()
        try:
            order = self._submit_entry_order(top, decision.quantity, client_order_id, session_name)
            self._log_trade_submission(top, decision.quantity, decision.risk_amount, order, client_order_id, session_name)
        except Exception as exc:
            message = str(exc)
            if "insufficient buying power" in message.lower():
                return {
                    "status": "blocked",
                    "message": "Insufficient buying power for new entries. Managing existing trades only.",
                    "order_id": None,
                    "client_order_id": client_order_id,
                    "quantity": 0,
                    "trade_ticket": None,
                }
            return {
                "status": "error",
                "message": message,
                "order_id": None,
                "client_order_id": client_order_id,
                "quantity": 0,
                "trade_ticket": None,
            }
        trade_ticket = self._trade_ticket(top, decision.quantity, decision.risk_amount, session_name, "submitted")
        return {
            "status": "submitted",
            "message": f"Submitted {'paper' if self.client.is_paper else 'live'} trade for {top['symbol']} in {session_name}.",
            "order_id": str(order.id),
            "client_order_id": client_order_id,
            "quantity": decision.quantity,
            "trade_ticket": trade_ticket,
        }

    def execute_all_eligible_candidates(
        self,
        symbols: list[str] | None = None,
        rvol_confirmation_thresholds: dict[str, float] | None = None,
        trade_overrides: dict[str, dict] | None = None,
    ) -> dict:
        entry_lock = getattr(self, "_entry_execution_lock", None)
        if entry_lock is None:
            entry_lock = threading.RLock()
            self._entry_execution_lock = entry_lock
        with entry_lock:
            return self._execute_all_eligible_candidates_locked(
                symbols=symbols,
                rvol_confirmation_thresholds=rvol_confirmation_thresholds,
                trade_overrides=trade_overrides,
            )

    def _execute_all_eligible_candidates_locked(
        self,
        symbols: list[str] | None = None,
        rvol_confirmation_thresholds: dict[str, float] | None = None,
        trade_overrides: dict[str, dict] | None = None,
    ) -> dict:
        scan_results, candidate_frame = self.run_scan_and_prepare_trades(
            symbols,
            rvol_confirmation_thresholds,
            trade_overrides,
        )
        if candidate_frame.empty:
            return {"status": "skipped", "message": "No eligible trade candidates found.", "submitted": []}

        eligible = candidate_frame[candidate_frame["allowed"]].sort_values(["final_score", "entry"], ascending=[False, False])
        if eligible.empty:
            top_blocked = candidate_frame.iloc[0]
            return {
                "status": "blocked",
                "message": top_blocked["rejection_reason"] or "No candidates passed strategy rules.",
                "submitted": [],
            }

        submitted: list[dict] = []
        blocked: list[dict] = []
        active_symbols = self._existing_symbols()

        for _, row in eligible.iterrows():
            symbol = str(row["symbol"]).upper()
            if symbol in active_symbols:
                blocked.append({"symbol": row["symbol"], "reason": "Symbol already has an open position or pending order"})
                continue

            status = self.get_status()
            decision = self.risk_manager.can_open_new_trade(
                equity=status.account_equity,
                daily_pnl=status.daily_pnl,
                trades_today=status.trades_today,
                risk_per_share=float(row["risk_per_share"]),
                entry_price=float(row["entry"]),
                buying_power=status.buying_power,
                deployed_capital=status.deployed_capital,
            )
            if not decision.allowed:
                blocked.append({"symbol": row["symbol"], "reason": decision.reason})
                if "Maximum trades reached" in decision.reason or "Daily loss limit reached" in decision.reason:
                    break
                continue

            client_order_id = f"momentum-{row['symbol']}-{uuid4().hex[:8]}"
            if not self.client.is_paper and not settings.allow_live_trading:
                blocked.append({"symbol": row["symbol"], "reason": "Live mode disabled by config"})
                continue

            session_name = self._session_name()
            try:
                order = self._submit_entry_order(row, decision.quantity, client_order_id, session_name)
                self._log_trade_submission(row, decision.quantity, decision.risk_amount, order, client_order_id, session_name)
            except Exception as exc:
                message = str(exc)
                blocked.append({"symbol": row["symbol"], "reason": message})
                if "insufficient buying power" in message.lower():
                    blocked.append({"symbol": "SYSTEM", "reason": "Insufficient buying power for new entries. Managing existing trades only."})
                    break
                continue
            active_symbols.add(symbol)
            submitted.append(
                {
                    "symbol": row["symbol"],
                    "order_id": str(order.id),
                    "client_order_id": client_order_id,
                    "quantity": decision.quantity,
                    "score": int(row.get("final_score", row["score"])),
                    "trade_ticket": self._trade_ticket(row, decision.quantity, decision.risk_amount, session_name, "submitted"),
                }
            )

        if submitted:
            return {
                "status": "submitted",
                "message": f"Submitted {len(submitted)} paper trades automatically across {self._session_name()}.",
                "submitted": submitted,
                "blocked": blocked,
            }

        return {
            "status": "blocked",
            "message": blocked[0]["reason"] if blocked else "No orders were submitted.",
            "submitted": [],
            "blocked": blocked,
        }

    def sync_order_statuses(self) -> None:
        open_trades = self.repository.get_open_trades(profile_id=self.client.credentials.profile_id)
        if open_trades.empty:
            return
        open_trades = self._latest_open_rows(open_trades)
        live_orders = self.client.get_open_orders(symbols=open_trades["symbol"].unique().tolist())
        live_map = {order.client_order_id: order for order in live_orders}
        positions = {position.symbol.upper(): position for position in self.client.get_positions()}

        for row in open_trades.to_dict("records"):
            live_order = live_map.get(row["client_order_id"])
            symbol = str(row["symbol"]).upper()
            position = positions.get(symbol)
            if not live_order:
                if position:
                    if str(row.get("status") or "").lower() in {"exit_pending", "manual_exit_pending"}:
                        self.repository.update_trade_status(
                            client_order_id=row["client_order_id"],
                            status="position_open",
                            notes="exit_retry_required",
                        )
                        self.repository.log_bot_event(
                            "exit_retry",
                            f"{symbol} exit order cleared but position is still open. Re-arming exit management.",
                        )
                    else:
                        self.repository.update_trade_status(
                            client_order_id=row["client_order_id"],
                            status="position_open",
                        )
                    continue
                self.repository.update_trade_status(
                    client_order_id=row["client_order_id"],
                    status="closed_or_filled",
                    closed_at=datetime.utcnow().isoformat(),
                )
                continue
            self.repository.update_trade_status(
                client_order_id=row["client_order_id"],
                status=str(live_order.status),
            )

    def manage_open_trades(self) -> None:
        if not self._manage_open_trades_lock.acquire(blocking=False):
            return
        try:
            self._manage_open_trades_unlocked()
        finally:
            self._manage_open_trades_lock.release()

    def _manage_open_trades_unlocked(self) -> None:
        open_trades = self.repository.get_open_trades(profile_id=self.client.credentials.profile_id)
        if open_trades.empty:
            return
        open_trades = self._latest_open_rows(open_trades)

        session_name = self._session_name()
        now = datetime.now(tz=self._tz)
        positions = {position.symbol.upper(): position for position in self.client.get_positions()}
        live_orders = self.client.get_open_orders(symbols=open_trades["symbol"].unique().tolist())
        open_sell_symbols = {
            str(order.symbol).upper()
            for order in live_orders
            if str(getattr(order, "side", "")).lower().endswith("sell")
        }

        for row in open_trades.to_dict("records"):
            symbol = str(row["symbol"]).upper()
            position = positions.get(symbol)
            if not position or symbol in open_sell_symbols:
                continue

            current_price = float(position.current_price)
            entry_price = float(row.get("entry_price") or 0)
            stop_price = float(row.get("stop_price") or 0)
            target_price = float(row.get("target_price") or 0)
            if current_price <= 0 or stop_price <= 0:
                continue

            if self._maybe_take_partial_profit(row, position, current_price, session_name):
                continue

            self._maybe_raise_stop(row, current_price)
            refreshed_stop = self._latest_stop_price(row, fallback=stop_price)
            plan = self._trade_plan_state(row)
            partial_exit_taken = bool(plan.get("partial_exit_taken"))

            exit_reason = ""
            if target_price > 0 and current_price >= target_price and not partial_exit_taken:
                exit_reason = "target_hit"
            elif current_price <= refreshed_stop:
                exit_reason = "stop_loss_hit"
            else:
                candle_break = self._ema20_live_break(symbol, current_price)
                if candle_break is not None:
                    exit_reason = candle_break
                else:
                    exit_reason = self._session_exit_reason(row, now)

            if not exit_reason:
                continue

            exit_order_id = self._submit_exit_order(symbol, float(position.qty), current_price, session_name)
            self.repository.update_trade_plan(
                client_order_id=row["client_order_id"],
                notes=exit_reason,
                analysis_json=json.dumps(
                    {
                        **plan,
                        "runner_exit_price": round(current_price, 2),
                        "runner_exit_reason": exit_reason,
                        "runner_stop_locked_pct": round(((refreshed_stop - entry_price) / entry_price) * 100, 2) if entry_price > 0 else 0.0,
                    },
                    default=str,
                ),
            )

            pnl = round((current_price - entry_price) * float(position.qty), 2)
            self.repository.update_trade_status(
                client_order_id=row["client_order_id"],
                status="exit_pending",
                pnl=pnl,
                notes=exit_reason,
            )
            self.repository.log_bot_event(
                "exit_signal",
                f"{symbol} exit submitted in {session_name}: {exit_reason}",
                json.dumps(
                    {
                        "symbol": symbol,
                        "session": session_name,
                        "exit_reason": exit_reason,
                        "current_price": current_price,
                        "stop_price": refreshed_stop,
                        "target_price": row.get("target_price"),
                        "client_order_id": exit_order_id,
                    }
                ),
            )

    def _submit_exit_order(self, symbol: str, quantity: float, current_price: float, session_name: str) -> str:
        exit_order_id = f"exit-{symbol}-{uuid4().hex[:8]}"
        if session_name == "Core":
            self.client.submit_market_exit_order(
                symbol=symbol,
                qty=quantity,
                client_order_id=exit_order_id,
            )
        else:
            self.client.submit_extended_hours_limit_order(
                symbol=symbol,
                qty=quantity,
                limit_price=self._marketable_extended_exit_price(current_price),
                side="sell",
                client_order_id=exit_order_id,
            )
        return exit_order_id

    def _default_trade_plan_from_row(self, row: dict) -> dict:
        entry_price = float(row.get("entry_price") or row.get("entry") or 0)
        stop_price = float(row.get("stop_price") or row.get("stop_loss") or 0)
        return {
            "partial_exit_taken": False,
            "take_profit_1": round(entry_price * (1 + (settings.trading.take_profit_1_pct / 100)), 2) if entry_price > 0 else None,
            "take_profit_1_sell_pct": settings.trading.take_profit_1_sell_pct,
            "runner_stop": stop_price if stop_price > 0 else None,
            "runner_stop_locked_pct": round(((stop_price - entry_price) / entry_price) * 100, 2) if entry_price > 0 and stop_price > 0 else None,
            "model_win_probability": row.get("model_win_probability"),
            "model_expected_r": row.get("model_expected_r"),
            "ai_confidence": row.get("ai_confidence"),
            "ai_model_name": row.get("ai_model_name"),
            "risk_per_share": max(entry_price - stop_price, 0.01) if entry_price > 0 and stop_price > 0 else 0.01,
        }

    def _trade_plan_state(self, row: dict) -> dict:
        raw_analysis = row.get("analysis_json")
        if not raw_analysis:
            fallback = self._default_trade_plan_from_row(row)
            client_order_id = row.get("client_order_id")
            if client_order_id:
                try:
                    self.repository.update_trade_plan(
                        client_order_id=client_order_id,
                        analysis_json=json.dumps(fallback, default=str),
                    )
                except Exception:
                    pass
            return fallback
        try:
            return json.loads(raw_analysis)
        except Exception:
            return self._default_trade_plan_from_row(row)

    def _partial_exit_quantity(self, total_quantity: float) -> float:
        if total_quantity <= 1:
            return 0.0
        sell_fraction = max(min(settings.trading.take_profit_1_sell_pct / 100, 0.99), 0.01)
        if settings.trading.allow_fractional_shares:
            quantity = round(total_quantity * sell_fraction, 4)
            return max(min(quantity, total_quantity - 0.0001), 0.0001)
        quantity = int(total_quantity * sell_fraction)
        quantity = max(quantity, 1)
        quantity = min(quantity, int(total_quantity) - 1)
        return float(max(quantity, 0))

    def _maybe_take_partial_profit(self, row: dict, position, current_price: float, session_name: str) -> bool:
        plan = self._trade_plan_state(row)
        if plan.get("partial_exit_taken"):
            return False

        entry_price = float(row.get("entry_price") or 0)
        if entry_price <= 0:
            return False

        first_target = float(plan.get("take_profit_1") or round(entry_price * (1 + settings.trading.take_profit_1_pct / 100), 2))
        if current_price < first_target:
            return False

        total_qty = float(position.qty)
        partial_qty = self._partial_exit_quantity(total_qty)
        if partial_qty <= 0 or partial_qty >= total_qty:
            return False

        exit_order_id = self._submit_exit_order(str(row["symbol"]).upper(), partial_qty, current_price, session_name)
        updated_plan = {
            **plan,
            "partial_exit_taken": True,
            "partial_exit_price": round(current_price, 2),
            "partial_exit_qty": partial_qty,
            "take_profit_1": first_target,
            "runner_stop": round(entry_price, 2),
            "runner_stop_locked_pct": 0.0,
        }
        self.repository.update_trade_plan(
            client_order_id=row["client_order_id"],
            stop_price=round(entry_price, 2),
            target_price=round(entry_price, 2),
            notes="partial_take_profit_1",
            analysis_json=json.dumps(updated_plan, default=str),
        )
        self.repository.log_bot_event(
            "partial_exit",
            f"{row['symbol']} scaled out 80% at target 1 and moved runner to break-even.",
            json.dumps(
                {
                    "symbol": row["symbol"],
                    "client_order_id": row["client_order_id"],
                    "partial_qty": partial_qty,
                    "current_price": current_price,
                    "new_stop": entry_price,
                    "exit_order_id": exit_order_id,
                }
            ),
            )
        return True

    def _latest_open_rows(self, frame: pd.DataFrame) -> pd.DataFrame:
        if frame.empty or "symbol" not in frame.columns:
            return frame
        normalized = frame.copy()
        normalized["opened_at"] = pd.to_datetime(normalized["opened_at"], errors="coerce")
        normalized["symbol_key"] = normalized["symbol"].astype(str).str.upper()
        normalized = normalized.sort_values("opened_at", ascending=False)
        normalized = normalized.drop_duplicates(subset=["symbol_key"], keep="first")
        return normalized.drop(columns=["symbol_key"])

    def _latest_stop_price(self, row: dict, fallback: float) -> float:
        open_trades = self.repository.get_open_trades(profile_id=self.client.credentials.profile_id)
        if open_trades.empty:
            return fallback
        matching = open_trades[open_trades["client_order_id"] == row["client_order_id"]]
        if matching.empty:
            return fallback
        return float(matching.iloc[0].get("stop_price") or fallback)

    def _trade_risk_per_share(self, row: dict) -> float:
        raw_analysis = row.get("analysis_json")
        if raw_analysis:
            try:
                analysis = json.loads(raw_analysis)
                risk = float(analysis.get("risk_per_share") or 0)
                if risk > 0:
                    return risk
            except Exception:
                pass
        entry = float(row.get("entry_price") or 0)
        target = float(row.get("target_price") or 0)
        stop = float(row.get("stop_price") or 0)
        if target > entry:
            return max((target - entry) / 2, 0.01)
        return max(entry - stop, 0.01)

    def _maybe_raise_stop(self, row: dict, current_price: float) -> None:
        entry = float(row.get("entry_price") or 0)
        stop = float(row.get("stop_price") or 0)
        if entry <= 0 or stop <= 0:
            return
        plan = self._trade_plan_state(row)
        desired_stop = stop
        notes = None
        if plan.get("partial_exit_taken"):
            desired_stop = max(desired_stop, round(entry, 2))
            gain_pct = ((current_price - entry) / entry) * 100 if entry > 0 else 0
            if gain_pct >= settings.trading.take_profit_1_pct:
                trailed_gain_pct = max(gain_pct - settings.trading.take_profit_1_pct, 0)
                desired_stop = max(desired_stop, round(entry * (1 + (trailed_gain_pct / 100)), 2))
                notes = "runner_stop_step_up"
            else:
                notes = "runner_stop_breakeven"
        else:
            risk = self._trade_risk_per_share(row)
            if current_price >= entry + (2 * risk):
                desired_stop = max(desired_stop, round(entry + risk, 2))
                notes = "trail_stop_2r"
            elif current_price >= entry + risk:
                desired_stop = max(desired_stop, round(entry, 2))
                notes = "trail_stop_breakeven"

        if desired_stop <= stop + 0.009:
            return

        self.repository.update_trade_plan(
            client_order_id=row["client_order_id"],
            stop_price=desired_stop,
            notes=notes,
            analysis_json=json.dumps(
                {
                    **plan,
                    "runner_stop": desired_stop,
                    "runner_stop_locked_pct": round(((desired_stop - entry) / entry) * 100, 2) if entry > 0 else 0.0,
                },
                default=str,
            ),
        )
        self.repository.log_bot_event(
            "stop_update",
            f"{row['symbol']} stop raised to {desired_stop:.2f}.",
            json.dumps(
                {
                    "symbol": row["symbol"],
                    "client_order_id": row["client_order_id"],
                    "old_stop": stop,
                    "new_stop": desired_stop,
                    "reason": notes,
                }
            ),
        )

    def _session_exit_reason(self, row: dict, now: datetime) -> str:
        session_name = str(row.get("session_name") or "").strip()
        current_time = now.time().replace(tzinfo=None)
        weekday = now.weekday()
        if session_name == "Core" and weekday < 5 and current_time >= time(15, 55):
            return "core_session_end"
        if session_name == "Pre-Market" and weekday < 5 and current_time >= time(9, 25):
            return "premarket_session_end"
        if session_name == "After-Hours" and weekday < 5 and current_time >= time(19, 55):
            return "afterhours_session_end"
        if session_name == "Overnight":
            overnight_window_end = datetime.combine(now.date(), time(3, 55), tzinfo=self._tz)
            if now >= overnight_window_end and now <= overnight_window_end + timedelta(minutes=35):
                return "overnight_session_end"
        return ""

    def _ema20_live_break(self, symbol: str, current_price: float) -> str | None:
        frame = self.client.get_chart_bars(symbol, timeframe="5Min", days_back=3)
        if frame is None or frame.empty or len(frame) < 20:
            return None
        bars = frame.copy().sort_values("timestamp").reset_index(drop=True)
        bars["ema_20"] = ema(bars["close"], 20)
        latest = bars.iloc[-1]
        if pd.isna(latest["ema_20"]):
            return None
        if float(current_price) < float(latest["ema_20"]):
            return "ema20_break_live"
        return None

    def _existing_symbols(self) -> set[str]:
        symbols = set()
        try:
            symbols.update(position.symbol.upper() for position in self.client.get_positions())
        except Exception:
            pass
        try:
            symbols.update(order.symbol.upper() for order in self.client.get_open_orders())
        except Exception:
            pass
        try:
            open_trades = self.repository.get_open_trades(profile_id=self.client.credentials.profile_id)
            if not open_trades.empty and "symbol" in open_trades.columns:
                symbols.update(open_trades["symbol"].astype(str).str.upper().tolist())
        except Exception:
            pass
        return symbols

    def _execution_route_for_session(self, session_name: str) -> str:
        return "Market Entry / Core Session" if session_name == "Core" else "Extended-Hours Limit / All Sessions"

    def _trade_blueprint_for_session(self, row, session_name: str, entry: float, stop_loss: float, target: float) -> str:
        return (
            f"Setup {row.get('setup_name', 'Momentum')} -> "
            f"Policy {row.get('policy_status', 'Approved')} -> "
            f"Entry {entry:.2f} / Stop {stop_loss:.2f} / Target {target:.2f} -> "
            f"Route {self._execution_route_for_session(session_name)}"
        )

    def _marketable_extended_entry_price(self, entry_price: float) -> float:
        if entry_price <= 1:
            return round(entry_price + 0.01, 2)
        return round(entry_price * 1.01, 2)

    def _marketable_extended_exit_price(self, current_price: float) -> float:
        if current_price <= 1:
            return round(max(current_price - 0.01, 0.01), 2)
        return round(max(current_price * 0.99, 0.01), 2)

    def _analysis_snapshot(self, row) -> dict:
        keys = [
            "symbol",
            "strategy_family",
            "setup_name",
            "score",
            "rule_score",
            "ai_trade_score",
            "ml_score",
            "catalyst_score",
            "regime_score",
            "memory_score",
            "anomaly_score",
            "setup_fit_score",
            "trend_quality_score",
            "ai_confidence",
            "ai_model_name",
            "model_win_probability",
            "model_expected_r",
            "model_edge_score",
            "model_confidence",
            "ai_decision",
            "ai_reasoning",
            "llm_agent_mode",
            "llm_agent_model",
            "llm_agent_non_blocking",
            "llm_advice",
            "llm_summary",
            "llm_strengths",
            "llm_cautions",
            "llm_tags",
            "llm_confidence",
            "llm_rank_score",
            "llm_latency_ms",
            "llm_error",
            "liquidity_score",
            "final_score",
            "entry",
            "stop_loss",
            "target",
            "risk_per_share",
            "rvol",
            "one_hour_close_change_pct",
            "four_hour_volume_change_pct",
            "one_hour_close_pass",
            "four_hour_volume_pass",
            "stock_signal_gate_active",
            "stock_all_conditions_pass",
            "trigger_source",
            "policy_name",
            "policy_status",
            "execution_route",
            "trade_blueprint",
            "intraday_change_pct",
            "session_change_pct",
            "session_change_pass",
            "above_vwap",
            "ema_stack",
            "breakout_close_confirmed",
            "volume_trend",
            "market_trend",
            "close_near_high",
            "not_overextended",
            "ema9_retest_5m",
            "first_5m_bullish",
            "first_5m_close_above_vwap",
            "orb_breakout",
            "rationale",
            "liquidity_target_price",
            "liquidity_target_contract",
            "target_source",
        ]
        return {key: row.get(key) for key in keys if hasattr(row, "get")}

    def _trade_ticket(self, row, quantity: float, risk_amount: float, session_name: str, status: str) -> dict:
        execution_route = self._execution_route_for_session(session_name)
        entry = round(float(row["entry"]), 2)
        stop_loss = round(float(row["stop_loss"]), 2)
        target = self._first_target_for_row(row, entry)
        return {
            "symbol": row["symbol"],
            "strategyFamily": row.get("strategy_family", "Momentum + Price Action Trend"),
            "setupName": row.get("setup_name", ""),
            "policyName": row.get("policy_name", ""),
            "policyStatus": row.get("policy_status", "Approved"),
            "executionRoute": execution_route,
            "sessionName": session_name,
            "status": status,
            "entry": entry,
            "stopLoss": stop_loss,
            "target": target,
            "targetSource": row.get("target_source", "+2% stock target"),
            "liquidityTargetContract": row.get("liquidity_target_contract", ""),
            "riskPerShare": round(float(row["risk_per_share"]), 2),
            "quantity": quantity,
            "riskAmount": round(float(risk_amount), 2),
            "aiScore": int(row.get("final_score", row.get("score", 0))),
            "aiConfidence": round(float(row.get("ai_confidence", 0)), 2),
            "aiModelName": row.get("ai_model_name", "HeavyTradingModel-v2"),
            "modelWinProbability": round(float(row.get("model_win_probability", 0)), 4) if row.get("model_win_probability") is not None else None,
            "modelExpectedR": round(float(row.get("model_expected_r", 0)), 4) if row.get("model_expected_r") is not None else None,
            "ruleScore": int(row.get("rule_score", row.get("score", 0))),
            "mlScore": round(float(row.get("ml_score", 0)), 2),
            "catalystScore": round(float(row.get("catalyst_score", 0)), 2),
            "regimeScore": round(float(row.get("regime_score", 0)), 2),
            "triggerSource": row.get("trigger_source", ""),
            "blueprint": self._trade_blueprint_for_session(row, session_name, entry, stop_loss, target),
            "rationale": row.get("rationale", ""),
            "llmAdvice": row.get("llm_advice", ""),
            "llmSummary": row.get("llm_summary", ""),
            "llmTags": row.get("llm_tags", []),
            "llmNonBlocking": bool(row.get("llm_agent_non_blocking", True)),
        }

    def _submit_entry_order(self, row, quantity: float, client_order_id: str, session_name: str):
        if session_name == "Core":
            return self.client.submit_market_entry_order(
                symbol=row["symbol"],
                qty=quantity,
                client_order_id=client_order_id,
            )

        return self.client.submit_extended_hours_limit_order(
            symbol=row["symbol"],
            qty=quantity,
            limit_price=self._marketable_extended_entry_price(float(row["entry"])),
            side="buy",
            client_order_id=client_order_id,
        )

    def _record_learning_observations(
        self,
        frame: pd.DataFrame,
        source: str,
        product: str,
        traded: bool = False,
        trade_reference: str | None = None,
    ) -> None:
        """Learning capture is best-effort and can never block scanning or execution."""
        try:
            self.repository.log_learning_observations(
                frame,
                source=source,
                product=product,
                traded=traded,
                trade_reference=trade_reference,
            )
        except Exception as exc:
            try:
                self.repository.log_bot_event(
                    "learning_capture_error",
                    f"{source} learning capture failed: {exc}",
                )
            except Exception:
                pass
    def _log_trade_submission(self, row, quantity: float, risk_amount: float, order, client_order_id: str, session_name: str) -> None:
        entry_price = float(row["entry"])
        first_target = self._first_target_for_row(row, entry_price)
        execution_route = self._execution_route_for_session(session_name)
        trade_blueprint = self._trade_blueprint_for_session(
            row,
            session_name,
            round(entry_price, 2),
            round(float(row["stop_loss"]), 2),
            round(first_target, 2),
        )
        analysis_snapshot = {
            **self._analysis_snapshot(row),
            "execution_route": execution_route,
            "trade_blueprint": trade_blueprint,
            "partial_exit_taken": False,
            "take_profit_1": first_target,
            "take_profit_1_sell_pct": settings.trading.take_profit_1_sell_pct,
            "runner_stop": float(row["stop_loss"]),
            "target_source": row.get("target_source", "+2% stock target"),
            "liquidity_target_price": row.get("liquidity_target_price"),
            "liquidity_target_contract": row.get("liquidity_target_contract", ""),
        }
        self.repository.log_trade(
            client_order_id=client_order_id,
            symbol=row["symbol"],
            account_profile_id=self.client.credentials.profile_id,
            account_label=self.client.credentials.label,
            side="buy",
            quantity=quantity,
            entry_price=entry_price,
            stop_price=float(row["stop_loss"]),
            target_price=first_target,
            risk_amount=risk_amount,
            status=str(order.status),
            setup_name=str(row.get("setup_name", "")),
            strategy_family=str(row.get("strategy_family", "Momentum + Price Action Trend")),
            score=float(row.get("score", 0)),
            trigger_source=str(row.get("trigger_source", "")),
            policy_status=str(row.get("policy_status", "")),
            execution_route=execution_route,
            trade_blueprint=trade_blueprint,
            session_name=session_name,
            entry_reason=str(row.get("rationale", "")),
            analysis_json=json.dumps(analysis_snapshot, default=str),
            notes=row["rationale"],
        )
        snapshot = row.to_dict() if hasattr(row, "to_dict") else dict(row)
        snapshot.update(
            {
                "entry": entry_price,
                "stop_loss": float(row["stop_loss"]),
                "target": first_target,
                "session_name": session_name,
                "trade_status": str(order.status),
                "quantity": quantity,
                "risk_amount": risk_amount,
            }
        )
        self._record_learning_observations(
            pd.DataFrame([snapshot]),
            source="stock_bot_trade",
            product="stock",
            traded=True,
            trade_reference=client_order_id,
        )

    def _first_target_for_row(self, row, entry_price: float) -> float:
        liquidity_target = float(row.get("liquidity_target_price") or 0)
        if liquidity_target > float(entry_price):
            return round(liquidity_target, 2)
        return round(float(entry_price) * (1 + (settings.trading.take_profit_1_pct / 100)), 2)

    def _session_name(self) -> str:
        now = datetime.now(tz=self._tz)
        current_time = now.time().replace(tzinfo=None)
        weekday = now.weekday()
        if weekday >= 5 and not (weekday == 6 and current_time >= time(20, 0)):
            return "Closed"
        if time(9, 30) <= current_time < time(16, 0) and weekday < 5:
            return "Core"
        if time(4, 0) <= current_time < time(9, 30) and weekday < 5:
            return "Pre-Market"
        if time(16, 0) <= current_time < time(20, 0) and weekday < 5:
            return "After-Hours"
        if weekday == 6 and current_time >= time(20, 0):
            return "Overnight"
        if weekday in {0, 1, 2, 3} and (current_time < time(4, 0) or current_time >= time(20, 0)):
            return "Overnight"
        if weekday == 4 and current_time < time(4, 0):
            return "Overnight"
        return "Closed"
