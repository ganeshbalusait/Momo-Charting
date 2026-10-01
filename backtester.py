from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, time

import pandas as pd
from zoneinfo import ZoneInfo

from ai_ensemble import HeavyTradingModel
from config import EASTERN_TZ, settings
from data.alpaca_client import AlpacaClient
from data.market_data import create_market_data_client
from indicators import cumulative_vwap, ema, regular_session
from scanner import MomentumScanner
from risk_manager import RiskManager
from strategy import StrategyEngine


@dataclass(slots=True)
class BacktestSummary:
    symbol: str
    strategy_name: str
    trades: int
    wins: int
    losses: int
    win_rate: float
    total_r: float
    total_pnl: float
    average_pnl: float
    average_ai_score: float
    average_ai_confidence: float
    average_rule_score: float
    average_ml_score: float
    average_catalyst_score: float
    average_regime_score: float
    data_start: str
    data_end: str


class Backtester:
    STRATEGY_NAME = "Momentum Price Action Trend"

    def __init__(self, client: AlpacaClient | None = None) -> None:
        self.client = client or create_market_data_client()
        self.scanner = MomentumScanner(client=self.client)
        self.strategy = StrategyEngine()
        self.risk_manager = RiskManager()
        self.ai_model = HeavyTradingModel()
        self._tz = ZoneInfo(EASTERN_TZ)
        start_hour, start_minute = [int(part) for part in settings.trading.trade_start_after_et.split(":", maxsplit=1)]
        end_hour, end_minute = [int(part) for part in settings.trading.no_new_trades_after_et.split(":", maxsplit=1)]
        self._entry_start = time(hour=start_hour, minute=start_minute)
        self._entry_end = time(hour=end_hour, minute=end_minute)

    def run(
        self,
        symbols: list[str],
        bars_to_fetch: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        if start is None or end is None:
            lookback_days = bars_to_fetch or settings.backtest.bars_to_fetch
            end = datetime.now(tz=self._tz)
            start = end - timedelta(days=lookback_days * 2)

        if start.tzinfo is None:
            start = start.replace(tzinfo=self._tz)
        else:
            start = start.astimezone(self._tz)
        if end.tzinfo is None:
            end = end.replace(tzinfo=self._tz)
        else:
            end = end.astimezone(self._tz)

        intraday_timeframe = settings.scanner.intraday_timeframe
        intraday_bars = self.client.get_stock_bars_range(symbols, intraday_timeframe, start=start, end=end, chunk_days=20)
        daily_bars = self.client.get_stock_bars_range(symbols, "1Day", start=start, end=end, chunk_days=365)
        spy_intraday = self.client.get_stock_bars_range(["SPY"], intraday_timeframe, start=start, end=end, chunk_days=20).get("SPY", pd.DataFrame())

        summaries: list[dict] = []
        trades: list[dict] = []

        for symbol in symbols:
            intraday_frame = intraday_bars.get(symbol)
            daily_frame = daily_bars.get(symbol)
            if intraday_frame is None or intraday_frame.empty or daily_frame is None or daily_frame.empty:
                continue
            summary, trade_rows = self._backtest_symbol(symbol, intraday_frame.copy(), daily_frame.copy(), spy_intraday.copy())
            if summary:
                summaries.append(asdict(summary))
            trades.extend(trade_rows)

        return pd.DataFrame(summaries), pd.DataFrame(trades)

    def _backtest_symbol(
        self,
        symbol: str,
        intraday_frame: pd.DataFrame,
        daily_frame: pd.DataFrame,
        spy_intraday: pd.DataFrame,
    ) -> tuple[BacktestSummary | None, list[dict]]:
        cash = settings.backtest.initial_capital
        trade_rows: list[dict] = []
        wins = 0
        losses = 0
        total_r = 0.0
        rolling_memory: dict[str, dict] = {}

        intraday_frame["session_date"] = intraday_frame["timestamp"].dt.date
        if not spy_intraday.empty:
            spy_intraday["session_date"] = spy_intraday["timestamp"].dt.date
        daily_frame["session_date"] = daily_frame["timestamp"].dt.date

        grouped_intraday = intraday_frame.groupby("session_date")
        grouped_spy = spy_intraday.groupby("session_date") if not spy_intraday.empty else {}

        for session_date, day_frame in grouped_intraday:
            if len(day_frame) < 30:
                continue

            daily_slice = daily_frame[daily_frame["session_date"] <= session_date].copy()
            if len(daily_slice) < 25:
                continue

            session = regular_session(day_frame)
            if session.empty:
                continue
            session["calc_vwap"] = cumulative_vwap(session)

            five_min = self.scanner._resample_to_five_min(session)
            if len(five_min) < 3:
                continue

            candidate_bars = five_min[
                (five_min["timestamp"].dt.time >= self._entry_start)
                & (five_min["timestamp"].dt.time <= self._entry_end)
            ].copy()
            if candidate_bars.empty:
                continue

            spy_day_frame = grouped_spy.get_group(session_date).copy() if spy_intraday is not None and not spy_intraday.empty and session_date in grouped_spy.groups else pd.DataFrame()
            trade_taken = False

            for _, candidate_bar in candidate_bars.iterrows():
                evaluation_time = candidate_bar["timestamp"]
                intraday_slice = intraday_frame[intraday_frame["timestamp"] <= evaluation_time].copy()
                if intraday_slice.empty:
                    continue

                spy_slice = spy_day_frame[spy_day_frame["timestamp"] <= evaluation_time].copy() if not spy_day_frame.empty else pd.DataFrame()
                spy_above_vwap = self.scanner._spy_above_vwap(spy_slice)
                result = self.scanner._score_symbol(symbol, intraday_slice, daily_slice, spy_above_vwap)
                if result is None or result.score < settings.scanner.score_threshold:
                    continue

                scored_signal = self._score_historical_signal(
                    result=result,
                    evaluation_time=evaluation_time,
                    rolling_memory=rolling_memory,
                )
                candidates = self.strategy.build_trade_candidates(
                    pd.DataFrame([scored_signal]),
                    existing_symbols=set(),
                    symbol_memory=pd.DataFrame([rolling_memory[symbol]]) if symbol in rolling_memory else pd.DataFrame(),
                    catalysts=pd.DataFrame(),
                    now=evaluation_time,
                )
                if not candidates:
                    continue
                candidate = candidates[0]
                if not candidate.allowed:
                    continue

                trade = self._simulate_trade(candidate, session.copy(), evaluation_time, cash)
                if trade is None:
                    continue
                trade["symbol"] = symbol
                trade["strategy_name"] = self.STRATEGY_NAME
                trade["strategy_family"] = candidate.strategy_family
                trade["setup_name"] = getattr(result, "setup_name", self.STRATEGY_NAME)
                trade["policy_name"] = candidate.policy_name
                trade["policy_status"] = candidate.policy_status
                trade["execution_route"] = candidate.execution_route
                trade["trade_blueprint"] = candidate.trade_blueprint
                trade["entry_date"] = str(session_date)
                trade["rule_score"] = candidate.rule_score
                trade["ml_score"] = candidate.ml_score
                trade["catalyst_score"] = candidate.catalyst_score
                trade["regime_score"] = candidate.regime_score
                trade["liquidity_score"] = candidate.liquidity_score
                trade["ai_trade_score"] = candidate.final_score
                trade["ai_confidence"] = candidate.ai_confidence
                trade["ai_model_name"] = candidate.ai_model_name
                trade["model_win_probability"] = candidate.model_win_probability
                trade["model_expected_r"] = candidate.model_expected_r
                trade["ai_decision"] = "Qualified" if candidate.allowed else "Filtered"
                trade["rationale"] = candidate.rationale
                for key in [
                    "rvol",
                    "one_hour_close_change_pct",
                    "four_hour_volume_change_pct",
                    "one_hour_close_pass",
                    "four_hour_volume_pass",
                    "stock_signal_gate_active",
                    "stock_all_conditions_pass",
                    "average_volume",
                    "last_price",
                    "atr_value",
                    "intraday_change_pct",
                    "extension_above_ema9_pct",
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
                    "trigger_source",
                ]:
                    if key in scored_signal:
                        trade[key] = scored_signal.get(key)
                trade_rows.append(trade)

                pnl = trade["pnl"]
                r_multiple = trade["r_multiple"]
                cash += pnl
                wins += int(pnl > 0)
                losses += int(pnl <= 0)
                total_r += r_multiple
                rolling_memory[symbol] = self._updated_memory_snapshot(
                    existing=rolling_memory.get(symbol),
                    symbol=symbol,
                    pnl=pnl,
                    r_multiple=r_multiple,
                )
                trade_taken = True
                break

            if trade_taken:
                continue

        trade_count = len(trade_rows)
        if trade_count == 0:
            return None, []

        total_pnl = sum(trade["pnl"] for trade in trade_rows)
        summary = BacktestSummary(
            symbol=symbol,
            strategy_name=self.STRATEGY_NAME,
            trades=trade_count,
            wins=wins,
            losses=losses,
            win_rate=round((wins / trade_count) * 100, 2),
            total_r=round(total_r, 2),
            total_pnl=round(total_pnl, 2),
            average_pnl=round(total_pnl / trade_count, 2),
            average_ai_score=round(sum(float(trade.get("ai_trade_score", 0)) for trade in trade_rows) / trade_count, 2),
            average_ai_confidence=round(sum(float(trade.get("ai_confidence", 0)) for trade in trade_rows) / trade_count, 2),
            average_rule_score=round(sum(float(trade.get("rule_score", 0)) for trade in trade_rows) / trade_count, 2),
            average_ml_score=round(sum(float(trade.get("ml_score", 0)) for trade in trade_rows) / trade_count, 2),
            average_catalyst_score=round(sum(float(trade.get("catalyst_score", 0)) for trade in trade_rows) / trade_count, 2),
            average_regime_score=round(sum(float(trade.get("regime_score", 0)) for trade in trade_rows) / trade_count, 2),
            data_start=str(intraday_frame["timestamp"].min().date()),
            data_end=str(intraday_frame["timestamp"].max().date()),
        )
        return summary, trade_rows

    def _simulate_trade(self, signal, session: pd.DataFrame, evaluation_time: datetime, equity: float) -> dict | None:
        entry = float(signal.entry)
        initial_stop = float(signal.stop_loss)
        stop = initial_stop
        target = round(entry * (1 + (settings.trading.take_profit_1_pct / 100)), 2)
        risk_per_share = max(entry - stop, 0.01)
        projected_quantity = self.risk_manager.quantity_for_entry(entry)
        if projected_quantity <= 0:
            return None
        quantity = projected_quantity if settings.trading.allow_fractional_shares else max(int(projected_quantity), 1)
        partial_fraction = max(min(settings.trading.take_profit_1_sell_pct / 100, 0.99), 0.01)
        if settings.trading.allow_fractional_shares:
            partial_qty = round(quantity * partial_fraction, 4)
            partial_qty = max(min(partial_qty, quantity - 0.0001), 0.0001) if quantity > 0.0001 else 0.0
        else:
            partial_qty = max(min(int(quantity * partial_fraction), int(quantity) - 1), 1) if quantity > 1 else 0
        remaining_qty = max(float(quantity) - float(partial_qty), 0.0)

        exit_price = entry
        exit_reason = "session_end"
        realized_pnl = 0.0
        exit_value = 0.0
        partial_exit_taken = False
        partial_exit_price = None
        partial_exit_qty_logged = None
        runner_exit_price = None
        runner_exit_reason = None
        runner_stop_locked_pct = round(((stop - entry) / entry) * 100, 2) if entry > 0 else 0.0
        future_session = session[session["timestamp"] > evaluation_time].copy()
        five_min = self.scanner._resample_to_five_min(session.copy())
        if not five_min.empty:
            five_min = five_min.sort_values("timestamp").reset_index(drop=True)
            five_min["ema_20"] = ema(five_min["close"], 20)
            future_five_min = five_min[five_min["timestamp"] > evaluation_time].copy()
        else:
            future_five_min = pd.DataFrame()

        if not future_five_min.empty:
            for _, bar in future_five_min.iterrows():
                bar_end = bar["timestamp"]
                raw_slice = future_session[future_session["timestamp"] <= bar_end].copy()
                if raw_slice.empty:
                    continue

                slice_low = float(raw_slice["low"].min())
                slice_high = float(raw_slice["high"].max())

                if not partial_exit_taken and slice_low <= stop:
                    exit_price = stop
                    exit_reason = "stop_loss"
                    exit_value = float(quantity) * exit_price
                    break

                if not partial_exit_taken and slice_high >= target:
                    partial_exit_taken = partial_qty > 0 and remaining_qty > 0
                    if partial_exit_taken:
                        realized_pnl += (target - entry) * float(partial_qty)
                        partial_exit_price = round(target, 2)
                        partial_exit_qty_logged = float(partial_qty)
                        stop = round(entry, 2)
                        runner_stop_locked_pct = round(((stop - entry) / entry) * 100, 2) if entry > 0 else 0.0
                        exit_reason = "partial_target_1"
                    else:
                        exit_price = target
                        exit_reason = "target_hit"
                        exit_value = float(quantity) * exit_price
                        break

                if partial_exit_taken:
                    gain_pct = ((slice_high - entry) / entry) * 100 if entry > 0 else 0
                    if gain_pct >= settings.trading.take_profit_1_pct:
                        locked_profit_pct = max(gain_pct - settings.trading.take_profit_1_pct, 0)
                        stop = max(stop, round(entry * (1 + (locked_profit_pct / 100)), 2))
                        runner_stop_locked_pct = round(((stop - entry) / entry) * 100, 2) if entry > 0 else 0.0

                    if slice_low <= stop:
                        exit_price = stop
                        exit_reason = "runner_stop_step_up"
                        runner_exit_price = round(exit_price, 2)
                        runner_exit_reason = exit_reason
                        exit_value = (float(partial_qty) * target) + (remaining_qty * exit_price)
                        break
                else:
                    if slice_high >= entry + (2 * risk_per_share):
                        stop = max(stop, entry + risk_per_share)
                    elif slice_high >= entry + risk_per_share:
                        stop = max(stop, entry)

                if pd.notna(bar.get("ema_20")) and float(raw_slice["low"].min()) < float(bar["ema_20"]):
                    exit_price = float(bar["ema_20"])
                    exit_reason = "ema20_break_live"
                    if partial_exit_taken:
                        runner_exit_price = round(exit_price, 2)
                        runner_exit_reason = exit_reason
                        exit_value = (float(partial_qty) * target) + (remaining_qty * exit_price)
                    else:
                        exit_value = float(quantity) * exit_price
                    break
            else:
                exit_price = float(future_five_min.iloc[-1]["close"])
                if partial_exit_taken:
                    runner_exit_price = round(exit_price, 2)
                    runner_exit_reason = "session_end"
                    exit_value = (float(partial_qty) * target) + (remaining_qty * exit_price)
                else:
                    exit_value = float(quantity) * exit_price
        elif not future_session.empty:
            slice_low = float(future_session["low"].min())
            slice_high = float(future_session["high"].max())
            if slice_low <= stop:
                exit_price = stop
                exit_reason = "stop_loss"
                exit_value = float(quantity) * exit_price
            elif slice_high >= target and partial_qty > 0 and remaining_qty > 0:
                partial_exit_taken = True
                realized_pnl += (target - entry) * float(partial_qty)
                partial_exit_price = round(target, 2)
                partial_exit_qty_logged = float(partial_qty)
                trailing_stop = round(entry * (1 + max((((slice_high - entry) / entry) * 100) - settings.trading.take_profit_1_pct, 0) / 100), 2)
                stop = max(round(entry, 2), trailing_stop)
                runner_stop_locked_pct = round(((stop - entry) / entry) * 100, 2) if entry > 0 else 0.0
                if slice_low <= stop:
                    exit_price = stop
                    exit_reason = "runner_stop_step_up"
                    runner_exit_price = round(exit_price, 2)
                    runner_exit_reason = exit_reason
                else:
                    exit_price = float(future_session.iloc[-1]["close"])
                    exit_reason = "session_end"
                    runner_exit_price = round(exit_price, 2)
                    runner_exit_reason = exit_reason
                exit_value = (float(partial_qty) * target) + (remaining_qty * exit_price)
            elif slice_high >= target:
                exit_price = target
                exit_reason = "target_hit"
                exit_value = float(quantity) * exit_price
            else:
                exit_price = float(future_session.iloc[-1]["close"])
                exit_value = float(quantity) * exit_price
        if not exit_value:
            exit_value = float(quantity) * exit_price
        pnl = round(realized_pnl + (exit_value - (float(quantity) * entry)), 2)
        weighted_exit_price = exit_value / float(quantity) if float(quantity) else exit_price
        r_multiple = round(pnl / max(risk_per_share * quantity, 0.01), 2)
        return {
            "entry": round(entry, 2),
            "stop": round(initial_stop, 2),
            "target": round(target, 2),
            "exit_price": round(weighted_exit_price, 2),
            "exit_reason": exit_reason,
            "quantity": quantity,
            "pnl": pnl,
            "r_multiple": r_multiple,
            "stop_loss_pct": round(((entry - initial_stop) / entry) * 100, 2),
            "profit_pct": round(((weighted_exit_price - entry) / entry) * 100, 2),
            "partial_exit_taken": partial_exit_taken,
            "partial_exit_price": partial_exit_price,
            "partial_exit_qty": partial_exit_qty_logged,
            "runner_exit_price": runner_exit_price,
            "runner_exit_reason": runner_exit_reason,
            "runner_stop_locked_pct": runner_stop_locked_pct,
        }

    def _score_historical_signal(
        self,
        result,
        evaluation_time: datetime,
        rolling_memory: dict[str, dict],
    ) -> dict:
        symbol = str(result.symbol).upper()
        catalyst_map = self.ai_model.repository.get_catalyst_snapshot([symbol], as_of=evaluation_time)
        frame = pd.DataFrame([asdict(result)])
        scored = self.ai_model.score_frame(
            frame,
            memory_map={symbol: rolling_memory[symbol]} if symbol in rolling_memory else {},
            catalyst_map=catalyst_map,
        )
        return scored.iloc[0].to_dict()

    def _updated_memory_snapshot(
        self,
        existing: dict | None,
        symbol: str,
        pnl: float,
        r_multiple: float,
    ) -> dict:
        current = dict(existing or {})
        observations = int(current.get("observations", 0)) + 1
        wins = int(current.get("wins", 0)) + int(pnl > 0)
        losses = int(current.get("losses", 0)) + int(pnl <= 0)
        total_pnl = float(current.get("total_pnl", 0.0)) + float(pnl)
        total_r = float(current.get("total_r", 0.0)) + float(r_multiple)
        win_rate = wins / observations if observations else 0.0
        confidence = max(0.0, min(100.0, 45.0 + (win_rate * 45.0) + min(total_r, 10.0)))
        return {
            "symbol": current.get("symbol", symbol),
            "observations": observations,
            "wins": wins,
            "losses": losses,
            "total_pnl": round(total_pnl, 2),
            "total_r": round(total_r, 2),
            "confidence": round(confidence, 2),
        }
