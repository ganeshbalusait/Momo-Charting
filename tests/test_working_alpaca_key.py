"""The premarket fill / overnight candles / failover stream use the first Alpaca key that
Alpaca actually serves (2026-10-04: the Settings key answered 401, paper3/paper5 200)."""
from __future__ import annotations

import api_server as a


def _reset(monkeypatch):
    monkeypatch.setattr(a, "_ALPACA_KEY_VERDICTS", {})


def test_a_refused_settings_key_falls_through_to_a_working_env_key(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(a, "_owner_alpaca_key_candidates",
                        lambda: [("settings", "dead", "x"), ("paper5", "good", "y"), ("paper3", "good3", "z")])
    status = {"dead": 401, "good": 200, "good3": 200}
    monkeypatch.setattr(a, "_alpaca_data_key_works",
                        lambda key, secret, **kw: {200: True, 401: False}[status[key]])
    assert a._working_owner_alpaca_key() == ("paper5", "good", "y")


def test_probe_caches_verdicts_and_never_caches_a_network_error(monkeypatch):
    _reset(monkeypatch)
    calls = []

    def get(token):
        calls.append(token)
        return {"ok": 200, "bad": 401}[token[0]]

    assert a._alpaca_data_key_works("ok", "s", get=get, now=0.0) is True
    assert a._alpaca_data_key_works("ok", "s", get=get, now=10.0) is True
    assert len(calls) == 1                                                    # cached 30 min
    assert a._alpaca_data_key_works("bad", "s", get=get, now=0.0) is False
    assert a._alpaca_data_key_works("ok", "s", get=get, now=a.ALPACA_KEY_GOOD_TTL_SECONDS + 1) is True
    assert len(calls) == 3                                                    # re-tested after the TTL

    def down(token):
        raise OSError("no network")
    assert a._alpaca_data_key_works("net", "s", get=down, now=0.0) is None
    assert ("net", "s") not in a._ALPACA_KEY_VERDICTS
    assert a._alpaca_data_key_works("", "", get=get) is False


def test_an_untestable_key_is_used_only_when_none_tested_good(monkeypatch):
    _reset(monkeypatch)
    monkeypatch.setattr(a, "_owner_alpaca_key_candidates", lambda: [("settings", "net", "x"), ("paper5", "dead", "y")])
    monkeypatch.setattr(a, "_alpaca_data_key_works", lambda key, secret, **kw: None if key == "net" else False)
    assert a._working_owner_alpaca_key() == ("settings", "net", "x")
