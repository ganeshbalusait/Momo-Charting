"""Unit: DashboardState.update_scanner_storage_settings - the SCANNER STORAGE
card's "keep scanner history this many days" (coverage 2026-11-04, group
"shell"). Called on a stand-in object, so no live state or database is used.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import api_server


class _Repo:
    def __init__(self):
        self.settings = {}
        self.events = []

    def set_app_setting(self, key, value):
        self.settings[key] = value

    def log_bot_event(self, kind, message, payload):
        self.events.append((kind, message))


def _state():
    return SimpleNamespace(
        repository=_Repo(), action_message="", invalidated=[],
        _scanner_storage_payload=lambda: {},
        _invalidate_dashboard_cache=lambda: None,
        dashboard_payload=lambda: {"scannerStorage": {"historyRetentionDays": api_server.settings.scanner.history_retention_days}},
    )


@pytest.fixture(autouse=True)
def _keep_real_retention(monkeypatch):
    monkeypatch.setattr(api_server.settings.scanner, "history_retention_days", 30)


def _update(state, value):
    return api_server.DashboardState.update_scanner_storage_settings(state, history_retention_days=value)


@pytest.mark.parametrize("value, kept", [(45, 45), ("60", 60), (7.9, 7), (1, 1)])
def test_a_valid_number_of_days_is_stored_and_reported(value, kept):
    state = _state()
    payload = _update(state, value)
    assert api_server.settings.scanner.history_retention_days == kept
    assert state.repository.settings == {"scanner_history_retention_days": str(kept)}
    assert payload["scannerStorage"]["historyRetentionDays"] == kept
    assert state.action_message == f"Scanner history retention updated to {kept} day{'' if kept == 1 else 's'}."


@pytest.mark.parametrize("value", [0, -5, "-1"])
def test_zero_or_negative_is_clamped_to_one_day_never_stored_as_is(value):
    state = _state()
    _update(state, value)
    assert api_server.settings.scanner.history_retention_days == 1
    assert state.repository.settings == {"scanner_history_retention_days": "1"}


@pytest.mark.parametrize("value", ["abc", "", "nan", "inf"])
def test_garbage_is_rejected_and_nothing_is_stored(value):
    state = _state()
    with pytest.raises((ValueError, OverflowError)):
        _update(state, value)
    assert api_server.settings.scanner.history_retention_days == 30
    assert state.repository.settings == {}


def test_no_value_keeps_the_setting_and_only_reports_it():
    state = _state()
    _update(state, None)
    assert api_server.settings.scanner.history_retention_days == 30
    assert state.repository.settings == {}
    assert state.repository.events and state.repository.events[0][0] == "scanner_storage_settings"
