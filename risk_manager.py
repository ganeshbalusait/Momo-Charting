from __future__ import annotations

from dataclasses import dataclass
import math

from config import settings


@dataclass(slots=True)
class RiskDecision:
    allowed: bool
    quantity: float
    risk_amount: float
    reason: str = ""


class RiskManager:
    def quantity_for_entry(self, entry_price: float) -> float:
        if entry_price <= 0:
            return 0.0
        raw_quantity = settings.trading.fixed_trade_amount / entry_price
        if settings.trading.allow_fractional_shares:
            raw_quantity = round(raw_quantity, 4)
        else:
            raw_quantity = math.floor(raw_quantity)
        return max(float(raw_quantity), 0.0)

    def effective_stop_price(
        self,
        entry_price: float,
        base_stop_price: float,
        quantity: float,
    ) -> float:
        entry = max(float(entry_price), 0.01)
        stop_price = entry * (1 - (max(settings.trading.stop_loss_percent, 0.01) / 100))

        stop_price = max(stop_price, 0.01)
        if stop_price >= entry:
            stop_price = max(entry - 0.01, 0.01)
        return round(stop_price, 2)

    def effective_target_price(self, entry_price: float, stop_price: float) -> float:
        return round(float(entry_price) * (1 + (settings.trading.take_profit_1_pct / 100)), 2)

    def can_open_new_trade(
        self,
        equity: float,
        daily_pnl: float,
        trades_today: int,
        risk_per_share: float,
        entry_price: float,
        buying_power: float = 0.0,
        deployed_capital: float = 0.0,
    ) -> RiskDecision:
        if settings.trading.max_trades_per_day > 0 and trades_today >= settings.trading.max_trades_per_day:
            return RiskDecision(False, 0.0, 0.0, "Maximum trades reached for the day")

        max_daily_loss_amount = equity * settings.trading.max_daily_loss_pct
        if settings.trading.enforce_daily_loss_limit and daily_pnl <= -max_daily_loss_amount:
            return RiskDecision(False, 0.0, 0.0, "Daily loss limit reached")

        if entry_price <= 0:
            return RiskDecision(False, 0.0, 0.0, "Invalid entry price")

        trade_capital = settings.trading.fixed_trade_amount
        raw_quantity = self.quantity_for_entry(entry_price)

        if raw_quantity <= 0:
            return RiskDecision(False, 0.0, 0.0, f"Trade amount ${trade_capital:.2f} is smaller than share price")

        estimated_cost = float(raw_quantity) * float(entry_price)
        risk_amount = estimated_cost * (max(float(settings.trading.stop_loss_percent), 0.01) / 100)
        if buying_power > 0 and estimated_cost > buying_power + 0.01:
            return RiskDecision(
                False,
                0.0,
                risk_amount,
                "Insufficient buying power for new entries. Managing existing trades only.",
            )

        return RiskDecision(True, float(raw_quantity), round(risk_amount, 2))
