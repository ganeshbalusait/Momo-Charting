"""A dead refresh token must cost ZERO network calls.

2026-09-03/04: the market-data refresh token expired at 19:51 ET. From then
on every MomX volume call (357 symbols x 2 timeframes, 12 threads, every
build) still went out to Schwab, where authlib tried to refresh the dead
token and was rejected - measured at 39 connections per 20 seconds, all
night. The next morning Akamai answered EVERYTHING from this address with
Access Denied, including the OAuth token exchange the trader needed to
recover. Whether the flood caused the block is not proven; that it kept
running after a re-auth would have (the client holds the dead token in
memory) is.
"""
from __future__ import annotations

import json
import os
import sys
import time
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings  # noqa: E402
from data import schwab_client as sc  # noqa: E402


def _write_token(path: Path, age_days: float) -> None:
    created = time.time() - age_days * 86400
    path.write_text(
        json.dumps({
            "creation_timestamp": created,
            "token": {"access_token": "a", "refresh_token": "r", "expires_at": created + 1800},
        }),
        encoding="utf-8",
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    token_path = tmp_path / "market_data.json"
    _write_token(token_path, age_days=8)  # one day past the 7-day lifetime

    def must_not_be_called(*args, **kwargs):
        raise AssertionError("client_from_token_file was called - that is a network client")

    monkeypatch.setattr(sc.schwab_auth, "client_from_token_file", must_not_be_called)
    config = replace(settings.schwab, client_id="KEY", client_secret="SECRET", token_path=str(token_path))
    return sc.SchwabClient(config), token_path


def test_a_dead_refresh_token_never_reaches_the_network(client):
    schwab, _ = client
    with pytest.raises(RuntimeError, match="refresh token expired"):
        schwab._get_json("https://api.schwabapi.com/marketdata/v1/pricehistory?symbol=NVDA")
    with pytest.raises(RuntimeError, match="refresh token expired"):
        schwab.library_client()


def test_the_message_names_the_expiry_so_settings_can_show_it(client):
    schwab, _ = client
    with pytest.raises(RuntimeError) as info:
        schwab._get_json("https://api.schwabapi.com/x")
    assert "Re-authenticate" in str(info.value)


def test_a_client_held_in_memory_heals_when_the_token_file_is_replaced(client, monkeypatch):
    """The worker caches one client per process. After the trader re-auths,
    the token FILE is new but the object is old; it must notice, not hammer
    (dead token) or stay dark (guard stuck on the old stamp) until a restart.
    """
    schwab, token_path = client
    with pytest.raises(RuntimeError):
        schwab._get_json("https://api.schwabapi.com/x")

    time.sleep(0.01)
    _write_token(token_path, age_days=0)
    os.utime(token_path, None)

    calls = []

    class _Session:
        def get(self, url, **kwargs):
            calls.append(url)

            class _Resp:
                def raise_for_status(self):
                    pass

                def json(self):
                    return {"ok": True}

            return _Resp()

    class _Lib:
        session = _Session()

    monkeypatch.setattr(sc.schwab_auth, "client_from_token_file", lambda *a, **k: _Lib())
    assert schwab._get_json("https://api.schwabapi.com/x") == {"ok": True}
    assert calls == ["https://api.schwabapi.com/x"]
    assert schwab.connection_status()["refreshTokenValid"] is True
