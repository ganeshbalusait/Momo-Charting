"""Unit: api_server._schwab_expiry_check_once and the watch thread - the part
that turns schwab_expiry_alert.due() into one phone push per login (coverage
2026-11-04, group "shell"). The clock is passed in, the ledger is a scratch
file, Schwab clients and the push are fakes.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timedelta, timezone

import pytest

import api_server

NOW = datetime(2026, 11, 4, 15, 0, tzinfo=timezone.utc)


def _status(expires_in, saved="2026-10-29T15:00:00+00:00"):
    return {"refreshTokenExpiresAt": (NOW + expires_in).isoformat(), "tokenSavedAt": saved}


@pytest.fixture
def world(tmp_path, monkeypatch):
    statuses = {"market_data": None, "trading": None}
    read = []

    class FakeSchwab:
        def __init__(self, profile="market_data", config=None):
            self.profile = profile
            self.configured = statuses.get(profile) is not None

        def connection_status(self):
            read.append(self.profile)
            return statuses[self.profile]

    pushes = []
    ledger = tmp_path / "schwab_expiry_pushes.json"
    monkeypatch.setattr(api_server, "SchwabClient", FakeSchwab)
    monkeypatch.setattr(api_server, "SCHWAB_EXPIRY_LEDGER_PATH", ledger)
    monkeypatch.setattr(api_server, "_preflight_admin_emails", lambda: {"admin@example.com"})
    monkeypatch.setattr(api_server, "_push_phone_notification",
                        lambda title, body, tags="bell", only_emails=None: pushes.append((title, body, tags, only_emails)))
    return type("World", (), {"statuses": statuses, "pushes": pushes, "ledger": ledger, "read": read})


def test_a_login_expiring_within_a_day_pushes_once_to_admins_only(world):
    world.statuses["market_data"] = _status(timedelta(hours=20))
    alerts = api_server._schwab_expiry_check_once(now=NOW)
    assert [a["title"] for a in alerts] == ["Schwab login expires in 20h"]
    assert len(world.pushes) == 1
    title, body, tags, only = world.pushes[0]
    assert (title, tags, only) == ("Schwab login expires in 20h", "key", {"admin@example.com"})
    assert "TOS MARKET" in body
    assert list(json.loads(world.ledger.read_text())) == [alerts[0]["key"]]


def test_a_second_pass_with_the_same_ledger_pushes_nothing(world):
    world.statuses["market_data"] = _status(timedelta(hours=20))
    api_server._schwab_expiry_check_once(now=NOW)
    assert api_server._schwab_expiry_check_once(now=NOW + timedelta(minutes=10)) == []
    assert len(world.pushes) == 1


def test_an_expired_login_gets_its_own_single_push(world):
    world.statuses["trading"] = _status(timedelta(hours=-1))
    api_server._schwab_expiry_check_once(now=NOW)
    api_server._schwab_expiry_check_once(now=NOW)
    assert [p[0] for p in world.pushes] == ["Schwab login EXPIRED"]


def test_an_unconfigured_profile_is_not_read_and_pushes_nothing(world):
    assert api_server._schwab_expiry_check_once(now=NOW) == []
    assert world.read == [] and world.pushes == []
    assert not world.ledger.exists(), "nothing due, nothing written"


def test_an_unreadable_ledger_starts_empty_and_a_failing_save_is_swallowed(world, monkeypatch, tmp_path):
    world.ledger.write_text("{not json")
    world.statuses["market_data"] = _status(timedelta(hours=5))
    assert len(api_server._schwab_expiry_check_once(now=NOW)) == 1
    # Now point the ledger at a directory: the write fails, the pass must not raise.
    as_dir = tmp_path / "ledger-is-a-folder"
    as_dir.mkdir()
    monkeypatch.setattr(api_server, "SCHWAB_EXPIRY_LEDGER_PATH", as_dir)
    world.statuses["trading"] = _status(timedelta(hours=-3))
    alerts = api_server._schwab_expiry_check_once(now=NOW)
    assert any(a["title"] == "Schwab login EXPIRED" for a in alerts)


def test_a_client_that_explodes_is_skipped_not_fatal(world, monkeypatch):
    world.statuses["trading"] = _status(timedelta(hours=2))

    class Exploding:
        def __init__(self, profile="market_data", config=None):
            if profile == "market_data":
                raise RuntimeError("token file locked")
            self.configured = True

        def connection_status(self):
            return world.statuses["trading"]

    monkeypatch.setattr(api_server, "SchwabClient", Exploding)
    alerts = api_server._schwab_expiry_check_once(now=NOW)
    assert [a["key"].split(":")[0] for a in alerts] == ["trading"]


def test_the_watch_starts_only_when_asked_and_only_once(monkeypatch):
    assert "schwab-expiry-watch" not in [t.name for t in threading.enumerate()], \
        "importing api_server must never start the watch (an import must never push)"
    release = threading.Event()
    monkeypatch.setattr(api_server, "_schwab_expiry_loop", lambda: release.wait(5))
    monkeypatch.setattr(api_server, "_SCHWAB_EXPIRY_THREAD", None)
    try:
        api_server._start_schwab_expiry_watch()
        first = api_server._SCHWAB_EXPIRY_THREAD
        api_server._start_schwab_expiry_watch()
        running = [t for t in threading.enumerate() if t.name == "schwab-expiry-watch"]
        assert api_server._SCHWAB_EXPIRY_THREAD is first
        assert len(running) == 1 and running[0].daemon
    finally:
        release.set()
        if api_server._SCHWAB_EXPIRY_THREAD is not None:
            api_server._SCHWAB_EXPIRY_THREAD.join(timeout=2)
