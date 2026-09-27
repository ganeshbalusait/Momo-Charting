"""One phone alert a day before each Schwab login dies, one when it has."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import schwab_expiry_alert as sea  # noqa: E402

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


def _status(expires_in: timedelta, saved="2026-09-04T04:51:36+00:00"):
    return {"refreshTokenExpiresAt": (NOW + expires_in).isoformat(), "tokenSavedAt": saved}


def test_nothing_is_due_with_days_left():
    assert sea.due({"market_data": _status(timedelta(days=3))}, {}, NOW) == []


def test_the_day_before_warning_fires_once_per_login():
    statuses = {"market_data": _status(timedelta(hours=23, minutes=30))}
    first = sea.due(statuses, {}, NOW)
    assert len(first) == 1
    assert first[0]["title"] == "Schwab login expires in 23h"
    assert "TOS MARKET" in first[0]["body"] and "Re-authenticate" in first[0]["body"]
    # Recorded after the push -> never again for this login...
    ledger = {first[0]["key"]: True}
    assert sea.due(statuses, ledger, NOW + timedelta(hours=1)) == []
    # ...but a fresh login arms a fresh warning.
    renewed = {"market_data": _status(timedelta(hours=20), saved="2026-09-11T00:00:00+00:00")}
    assert len(sea.due(renewed, ledger, NOW)) == 1


def test_expiry_itself_fires_once_and_names_the_consequence():
    statuses = {"trading": _status(timedelta(hours=-2))}
    alerts = sea.due(statuses, {}, NOW)
    assert len(alerts) == 1
    assert alerts[0]["title"] == "Schwab login EXPIRED"
    assert "TOS TRADING" in alerts[0]["body"] and "down until you re-authenticate" in alerts[0]["body"]
    assert sea.due(statuses, {alerts[0]["key"]: True}, NOW) == []


def test_both_profiles_are_independent():
    statuses = {"market_data": _status(timedelta(hours=5)), "trading": _status(timedelta(days=4))}
    alerts = sea.due(statuses, {}, NOW)
    assert [a["key"].split(":")[0] for a in alerts] == ["market_data"]


def test_missing_or_garbage_stamps_never_alert_or_crash():
    assert sea.due({"market_data": {"refreshTokenExpiresAt": None, "tokenSavedAt": None}}, {}, NOW) == []
    assert sea.due({"market_data": {"refreshTokenExpiresAt": "not a date", "tokenSavedAt": "x"}}, {}, NOW) == []
    assert sea.due({"market_data": "nope"}, {}, NOW) == []


def test_the_time_is_shown_in_eastern():
    statuses = {"market_data": {"refreshTokenExpiresAt": "2026-09-11T04:51:36+00:00", "tokenSavedAt": "s"}}
    alert = sea.due(statuses, {}, datetime(2026, 9, 10, 10, 0, tzinfo=timezone.utc))[0]
    assert "Fri Sep 11, 12:51 AM ET" in alert["body"]
