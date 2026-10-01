from __future__ import annotations

from typing import Any

from config import settings
from data.alpaca_client import AlpacaClient
from data.schwab_client import SchwabClient


def create_market_data_client(alpaca_client: AlpacaClient | None = None) -> Any:
    if settings.market_data_provider == "schwab":
        return SchwabClient()
    return alpaca_client or AlpacaClient()
