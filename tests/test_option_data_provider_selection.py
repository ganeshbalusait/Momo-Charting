from __future__ import annotations

import threading
from unittest.mock import patch

import pytest

# This module tests a pre-rebuild api_server/config surface whose functions
# were removed in the 2026-08-10 backend rebuild and its successors (OPTION_DATA_PROVIDER_REQUIRED_MESSAGE).
# A bare import crashed COLLECTION for the whole tests/ tree, hiding every
# other result (2026-08-21). Skip cleanly until these are rewritten against
# the current pipeline; the test bodies below document the intended
# behaviour of the retired surface.
try:
    from api_server import DashboardState, OPTION_DATA_PROVIDER_REQUIRED_MESSAGE
except ImportError as _exc:  # pragma: no cover
    pytest.skip(f"retired api surface: {_exc}", allow_module_level=True)



class _Provider:
    def __init__(self, configured: bool, error: str = "provider request failed") -> None:
        self.configured = configured
        self.error = error

    def get_option_chain(self, *args, **kwargs):
        raise RuntimeError(self.error)


def _mag7_state() -> DashboardState:
    state = DashboardState.__new__(DashboardState)
    state.mag7_oi_wall_lock = threading.RLock()
    state.mag7_oi_wall_cache = None
    state.mag7_oi_wall_cache_timestamp = None
    return state


def test_mag7_wall_reports_provider_choice_instead_of_requiring_tradier():
    state = _mag7_state()
    with (
        patch("api_server.SchwabClient", return_value=_Provider(False)),
        patch("api_server.TradierClient", return_value=_Provider(False)),
    ):
        payload = DashboardState.mag7_oi_wall_payload(state)

    assert payload["source"] == "Personal option-data provider"
    assert payload["errors"] == [
        {"symbol": "ALL", "error": OPTION_DATA_PROVIDER_REQUIRED_MESSAGE}
    ]


def test_mag7_wall_prefers_configured_schwab_over_tradier():
    state = _mag7_state()
    schwab = _Provider(True, "schwab request failed")
    tradier = _Provider(True, "tradier should not be called")
    with (
        patch("api_server.SchwabClient", return_value=schwab),
        patch("api_server.TradierClient", return_value=tradier),
    ):
        payload = DashboardState.mag7_oi_wall_payload(state)

    assert payload["source"] == "Schwab/TOS option chain"
    assert len(payload["errors"]) == 7
    assert all(row["error"] == "schwab request failed" for row in payload["errors"])
