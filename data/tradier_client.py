from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from config import EASTERN_TZ, settings


class TradierClient:
    """Read-only Tradier adapter for live option-chain OI and volume."""

    def __init__(self) -> None:
        self.config = settings.tradier
        self._tz = ZoneInfo(EASTERN_TZ)

    @property
    def configured(self) -> bool:
        return bool(self.config.access_token)

    def get_option_chain(
        self,
        symbol: str,
        contract_type: str = "ALL",
        strike_count: int = 80,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
    ) -> dict:
        if not self.configured:
            raise RuntimeError("TRADIER_ACCESS_TOKEN is not configured.")

        underlying = str(symbol or "").strip().upper()
        today = datetime.now(self._tz).date()
        earliest = from_date.date() if from_date else today
        latest = to_date.date() if to_date else None
        expirations_payload = self._get_json(
            "/markets/options/expirations",
            {"symbol": underlying, "includeAllRoots": "false"},
        )
        expirations = self._expiration_dates(expirations_payload)
        eligible = [item for item in expirations if item >= max(today, earliest) and (latest is None or item <= latest)]
        if not eligible:
            raise RuntimeError(f"Tradier returned no current or future option expiration for {underlying}.")
        expiration = min(eligible)

        chain_payload = self._get_json(
            "/markets/options/chains",
            {"symbol": underlying, "expiration": expiration.isoformat(), "greeks": "true"},
        )
        quote_payload = self._get_json("/markets/quotes", {"symbols": underlying, "greeks": "false"})
        return self._normalize_chain(
            underlying,
            expiration,
            chain_payload,
            quote_payload,
            contract_type=contract_type,
            strike_count=strike_count,
            today=today,
        )

    def get_option_chain_range(
        self,
        symbol: str,
        contract_type: str = "ALL",
        strike_count: int = 80,
        max_days_to_expiration: int = 14,
    ) -> dict:
        """Return every available expiration from today through the requested DTE."""
        if not self.configured:
            raise RuntimeError("TRADIER_ACCESS_TOKEN is not configured.")

        underlying = str(symbol or "").strip().upper()
        today = datetime.now(self._tz).date()
        latest = today + timedelta(days=max(int(max_days_to_expiration), 0))
        expirations_payload = self._get_json(
            "/markets/options/expirations",
            {"symbol": underlying, "includeAllRoots": "false"},
        )
        expirations = [item for item in self._expiration_dates(expirations_payload) if today <= item <= latest]
        if not expirations:
            raise RuntimeError(f"Tradier returned no option expiration within {max_days_to_expiration} DTE for {underlying}.")

        quote_payload = self._get_json("/markets/quotes", {"symbols": underlying, "greeks": "false"})
        combined = {
            "symbol": underlying,
            "underlyingPrice": 0.0,
            "underlying": self._quote(quote_payload),
            "callExpDateMap": {},
            "putExpDateMap": {},
        }
        for expiration in expirations:
            chain_payload = self._get_json(
                "/markets/options/chains",
                {"symbol": underlying, "expiration": expiration.isoformat(), "greeks": "true"},
            )
            normalized = self._normalize_chain(
                underlying,
                expiration,
                chain_payload,
                quote_payload,
                contract_type=contract_type,
                strike_count=strike_count,
                today=today,
            )
            combined["underlyingPrice"] = normalized.get("underlyingPrice") or combined["underlyingPrice"]
            combined["callExpDateMap"].update(normalized.get("callExpDateMap") or {})
            combined["putExpDateMap"].update(normalized.get("putExpDateMap") or {})
        return combined

    def _get_json(self, path: str, params: dict[str, str]) -> dict:
        url = f"{self.config.base_url}{path}?{urlencode(params)}"
        request = Request(
            url,
            headers={
                "Authorization": f"Bearer {self.config.access_token}",
                "Accept": "application/json",
                "User-Agent": "AgenticAI-Trading/1.0 (Tradier Market Data)",
            },
        )
        try:
            with urlopen(request, timeout=self.config.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8") or "{}")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Tradier request failed (HTTP {exc.code}): {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"Tradier request failed: {exc.reason}") from exc

    @staticmethod
    def _expiration_dates(payload: dict) -> list[date]:
        raw = (payload.get("expirations") or {}).get("date") if isinstance(payload, dict) else None
        values = raw if isinstance(raw, list) else [raw] if raw else []
        parsed: list[date] = []
        for value in values:
            try:
                parsed.append(date.fromisoformat(str(value)))
            except ValueError:
                continue
        return sorted(set(parsed))

    @staticmethod
    def _quote(payload: dict) -> dict:
        raw = (payload.get("quotes") or {}).get("quote") if isinstance(payload, dict) else None
        if isinstance(raw, list):
            return raw[0] if raw and isinstance(raw[0], dict) else {}
        return raw if isinstance(raw, dict) else {}

    @classmethod
    def _normalize_chain(
        cls,
        symbol: str,
        expiration: date,
        chain_payload: dict,
        quote_payload: dict,
        contract_type: str,
        strike_count: int,
        today: date,
    ) -> dict:
        raw_options = (chain_payload.get("options") or {}).get("option") if isinstance(chain_payload, dict) else None
        options = raw_options if isinstance(raw_options, list) else [raw_options] if isinstance(raw_options, dict) else []
        quote = cls._quote(quote_payload)
        underlying_price = float(quote.get("last") or quote.get("close") or 0.0)

        strikes = sorted({float(item.get("strike")) for item in options if isinstance(item, dict) and item.get("strike") is not None})
        if underlying_price > 0 and strike_count > 0:
            strikes = sorted(sorted(strikes, key=lambda value: abs(value - underlying_price))[:strike_count])
        allowed_strikes = set(strikes)
        dte = max((expiration - today).days, 0)
        expiry_key = f"{expiration.isoformat()}:{dte}"
        call_map: dict[str, dict[str, list[dict]]] = {expiry_key: {}}
        put_map: dict[str, dict[str, list[dict]]] = {expiry_key: {}}
        requested_type = str(contract_type or "ALL").upper()

        for option in options:
            if not isinstance(option, dict):
                continue
            option_type = str(option.get("option_type") or "").upper()
            if option_type not in {"CALL", "PUT"} or (requested_type != "ALL" and option_type != requested_type):
                continue
            try:
                strike = float(option.get("strike"))
            except (TypeError, ValueError):
                continue
            if allowed_strikes and strike not in allowed_strikes:
                continue
            normalized = {
                **option,
                "symbol": option.get("symbol"),
                "putCall": option_type,
                "expirationDate": expiration.isoformat(),
                "daysToExpiration": dte,
                "strikePrice": strike,
                "totalVolume": float(option.get("volume") or 0.0),
                "openInterest": float(option.get("open_interest") or 0.0),
            }
            strike_key = str(strike)
            target = call_map if option_type == "CALL" else put_map
            target[expiry_key].setdefault(strike_key, []).append(normalized)

        return {
            "symbol": symbol,
            "underlyingPrice": underlying_price,
            "underlying": quote,
            "callExpDateMap": call_map,
            "putExpDateMap": put_map,
        }
