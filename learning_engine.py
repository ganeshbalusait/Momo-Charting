from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from config import BASE_DIR
from predictive_model import PredictiveTradeModel


class TradingLearningAgent:
    """Collects labels and trains shadow challengers without touching execution gates."""

    MIN_CLOSED_TRADES = 50
    MIN_SCANNER_60M_OUTCOMES = 100
    MIN_TRAINING_ROWS = 100
    TRAIN_INTERVAL_DAYS = 7

    def __init__(self, repository, market_client, allowed_symbols_provider=None, symbol_cohort_provider=None) -> None:
        self.repository = repository
        self.market_client = market_client
        self.allowed_symbols_provider = allowed_symbols_provider
        self.symbol_cohort_provider = symbol_cohort_provider
        self.status = "Idle"
        self.message = "Learning agent is ready."
        self.last_run: datetime | None = None
        self.last_error = ""
        self._lock = threading.Lock()

    def run_cycle(self, force_training: bool = False) -> dict:
        if not self._lock.acquire(blocking=False):
            return {"started": False, "busy": True, "status": self.status, "message": self.message}
        cycle_id = self.repository.start_learning_cycle()
        self.status = "Running"
        self.message = "Resolving scanner outcomes and syncing paper trades."
        metrics: dict = {}
        try:
            symbol_cohorts = self._symbol_cohorts()
            allowed_symbols = sorted(symbol_cohorts) if symbol_cohorts else self._allowed_symbols()
            removed = self.repository.prune_learning_to_symbols(allowed_symbols) if allowed_symbols else 0
            new_trade_outcomes = self.repository.sync_learning_trade_outcomes(
                allowed_symbols=allowed_symbols,
                symbol_cohorts=symbol_cohorts,
            )
            due = self.repository.get_due_learning_outcomes(limit=1000)
            resolved = 0
            if not due.empty:
                symbols = sorted(
                    {
                        str(value).upper()
                        for value in due["symbol"].dropna().tolist()
                        if str(value).strip()
                    }
                )
                prices = self._latest_prices(symbols)
                for row in due.to_dict("records"):
                    price_value = prices.get(str(row.get("symbol") or "").upper())
                    if price_value is None or price_value <= 0:
                        continue
                    self.repository.resolve_learning_outcome(int(row["outcome_id"]), price_value)
                    resolved += 1

            status_payload = self.repository.learning_status()
            model_payload = self._maybe_train_shadow(status_payload, force_training=force_training)
            refreshed = self.repository.learning_status()
            metrics = {
                "observations": refreshed["observations"],
                "resolvedOutcomes": refreshed["resolvedOutcomes"],
                "tradeOutcomes": refreshed["tradeOutcomes"],
                "resolvedThisCycle": resolved,
                "newTradeOutcomes": new_trade_outcomes,
                "removedOutOfScope": removed,
                "model": model_payload,
            }
            self.status = "Monitoring"
            self.message = (
                f"Learning cycle complete: {resolved} forward labels and "
                f"{new_trade_outcomes} new trade outcomes resolved."
            )
            self.last_error = ""
            self.last_run = datetime.now(timezone.utc)
            self.repository.finish_learning_cycle(cycle_id, "completed", self.message, metrics)
            return {"started": True, "busy": False, **metrics, "status": self.status, "message": self.message}
        except Exception as exc:
            self.status = "Error"
            self.last_error = str(exc)
            self.message = f"Learning cycle failed: {exc}"
            self.last_run = datetime.now(timezone.utc)
            self.repository.finish_learning_cycle(cycle_id, "error", self.message, metrics)
            return {"started": True, "busy": False, "status": self.status, "message": self.message, "error": str(exc)}
        finally:
            self._lock.release()

    def _allowed_symbols(self) -> list[str] | None:
        if not callable(self.allowed_symbols_provider):
            return None
        return sorted({
            str(symbol or "").strip().upper()
            for symbol in (self.allowed_symbols_provider() or [])
            if str(symbol or "").strip()
        })

    def _symbol_cohorts(self) -> dict[str, str]:
        if not callable(self.symbol_cohort_provider):
            return {}
        return {
            str(symbol or "").strip().upper(): str(cohort or "watchlist").strip().lower()
            for symbol, cohort in (self.symbol_cohort_provider() or {}).items()
            if str(symbol or "").strip()
        }

    def _latest_prices(self, symbols: list[str]) -> dict[str, float]:
        if not symbols:
            return {}
        prices: dict[str, float] = {}
        if hasattr(self.market_client, "get_quotes"):
            try:
                quotes = self.market_client.get_quotes(symbols) or {}
            except Exception:
                quotes = {}
            for symbol, quote in quotes.items():
                if not isinstance(quote, dict):
                    continue
                raw_price = quote.get("last_price") or quote.get("price") or quote.get("last")
                try:
                    price = float(raw_price)
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices[str(symbol).upper()] = price

        missing = [symbol for symbol in symbols if symbol not in prices]
        if missing and hasattr(self.market_client, "get_stock_bars"):
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=5)
            try:
                frames = self.market_client.get_stock_bars(missing, "5Min", start, end) or {}
            except Exception:
                frames = {}
            for symbol, frame in frames.items():
                if frame is None or frame.empty or "close" not in frame.columns:
                    continue
                try:
                    price = float(frame.iloc[-1]["close"])
                except (TypeError, ValueError):
                    continue
                if price > 0:
                    prices[str(symbol).upper()] = price
        return prices

    def _maybe_train_shadow(self, status_payload: dict, force_training: bool = False) -> dict | None:
        if int(status_payload.get("tradeOutcomes") or 0) < self.MIN_CLOSED_TRADES:
            return None
        if int(status_payload.get("resolved60mOutcomes") or 0) < self.MIN_SCANNER_60M_OUTCOMES:
            return None
        models = status_payload.get("models") or []
        if models and not force_training:
            try:
                last_trained = datetime.fromisoformat(str(models[0]["trained_at"]).replace("Z", "+00:00"))
                if datetime.now(timezone.utc) - last_trained.astimezone(timezone.utc) < timedelta(days=self.TRAIN_INTERVAL_DAYS):
                    return None
            except Exception:
                pass

        dataset = self.repository.learning_training_frame(horizon_minutes=60)
        if len(dataset) < self.MIN_TRAINING_ROWS:
            return None
        split_index = max(int(len(dataset) * 0.8), 1)
        train = dataset.iloc[:split_index].copy()
        validation = dataset.iloc[split_index:].copy()
        if validation.empty:
            return None
        version = datetime.now(timezone.utc).strftime("shadow-%Y%m%d-%H%M%S")
        artifact = Path(BASE_DIR) / "artifacts" / "learning" / f"{version}.json"
        model = PredictiveTradeModel(str(artifact))
        train_metrics = model.fit(train)
        predicted = model.predict_frame(validation)
        probabilities = predicted["model_win_probability"].fillna(0.5).to_numpy(dtype=float)
        labels = validation["label_win"].astype(int).to_numpy()
        predicted_labels = (probabilities >= 0.5).astype(int)
        accuracy = float((predicted_labels == labels).mean()) if len(labels) else 0.0
        brier = float(np.mean((probabilities - labels) ** 2)) if len(labels) else 0.0
        label_sources = (
            dataset["label_source"].value_counts().to_dict()
            if "label_source" in dataset.columns
            else {}
        )
        payload = {
            "model_name": model.MODEL_NAME,
            "version": version,
            "status": "shadow",
            "trained_at": datetime.now(timezone.utc).isoformat(),
            "training_rows": len(train),
            "validation_rows": len(validation),
            "accuracy": round(accuracy, 4),
            "brier_score": round(brier, 4),
            "win_rate": round(float(labels.mean()), 4) if len(labels) else 0.0,
            "artifact_path": str(artifact),
            "metrics": {"train": train_metrics, "validationAccuracy": accuracy, "brierScore": brier, "labelSources": label_sources},
            "is_active": False,
        }
        self.repository.register_learning_model(payload)
        return payload
