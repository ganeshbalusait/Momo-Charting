from __future__ import annotations

from config import SchwabSettings, SchwabTradingSettings, settings
from data.schwab_client import SchwabClient
from data.tradier_client import TradierClient
from data.user_provider_context import (
    UserProviderContext,
    reset_user_provider_context,
    set_user_provider_context,
)


def test_admin_context_inherits_existing_server_provider_credentials():
    token = set_user_provider_context(
        UserProviderContext(
            user_id="owner",
            inherit_server_credentials=True,
            schwab_config=SchwabSettings(client_id="unused-user-key"),
            tradier_access_token="unused-user-token",
        )
    )
    try:
        assert SchwabClient().config is settings.schwab
        assert SchwabClient("trading").config is settings.schwab_trading
        assert TradierClient().config is settings.tradier
    finally:
        reset_user_provider_context(token)


def test_regular_user_context_never_inherits_server_provider_credentials():
    user_schwab = SchwabSettings(
        client_id="user-key",
        client_secret="user-secret",
        token_path="",
    )
    user_trading = SchwabTradingSettings(
        client_id="user-trading-key",
        client_secret="user-trading-secret",
        token_path="",
    )
    token = set_user_provider_context(
        UserProviderContext(
            user_id="workspace-user",
            schwab_config=user_schwab,
            schwab_token_reader=lambda: {},
            schwab_token_writer=lambda value: None,
            schwab_trading_config=user_trading,
            schwab_trading_token_reader=lambda: {},
            schwab_trading_token_writer=lambda value: None,
            tradier_access_token="user-tradier-token",
        )
    )
    try:
        assert SchwabClient().config is user_schwab
        assert SchwabClient("trading").config is user_trading
        assert TradierClient().config.access_token == "user-tradier-token"
        assert TradierClient().config is not settings.tradier
    finally:
        reset_user_provider_context(token)
