from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable


@dataclass(slots=True)
class UserProviderContext:
    user_id: str
    inherit_server_credentials: bool = False
    schwab_config: Any | None = None
    schwab_token_reader: Callable[[], dict] | None = None
    schwab_token_writer: Callable[..., None] | None = None
    schwab_trading_config: Any | None = None
    schwab_trading_token_reader: Callable[[], dict] | None = None
    schwab_trading_token_writer: Callable[..., None] | None = None
    alpaca_key_id: str = ""
    alpaca_secret_key: str = ""
    alpaca_paper: bool = True
    tradier_access_token: str = ""


CURRENT_USER_PROVIDER_CONTEXT: ContextVar[UserProviderContext | None] = ContextVar(
    "current_user_provider_context",
    default=None,
)


def set_user_provider_context(context: UserProviderContext | None):
    return CURRENT_USER_PROVIDER_CONTEXT.set(context)


def reset_user_provider_context(token) -> None:
    CURRENT_USER_PROVIDER_CONTEXT.reset(token)


def current_user_provider_context() -> UserProviderContext | None:
    return CURRENT_USER_PROVIDER_CONTEXT.get()
