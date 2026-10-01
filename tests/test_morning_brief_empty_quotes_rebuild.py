"""An EMPTY quote answer must rebuild the cached Schwab client.

2026-09-24 and 09-25: the morning brief lost its SPY/QQQ and movers lines.
The long-lived client inside api_server returned {} without raising (a client
that loaded no refresh token is "not configured" and get_quotes answers {}),
so the except branch never dropped it and every 5-minute build reused the dead
client, while a freshly built client answered fine.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import api_server

ET = ZoneInfo("America/New_York")


class _Empty:
    def get_quotes(self, symbols, **k):
        return {}


class _Good:
    def get_quotes(self, symbols, **k):
        return {"SPY": {"change_pct": 0.3, "last_price": 500.0}}


def test_an_empty_answer_drops_the_client_so_the_next_build_rebuilds_it(monkeypatch):
    state = object.__new__(api_server.DashboardState)
    state._morning_briefing_payload = None
    held = {}
    built = []

    def client(profile):
        if profile not in held:
            built.append(profile)
            held[profile] = _Empty() if len(built) <= 2 else _Good()
        return held[profile]

    monkeypatch.setattr(api_server, "_live_quote_client", client)
    monkeypatch.setattr(api_server, "_drop_live_quote_client", lambda profile: held.pop(profile, None))
    monkeypatch.setattr(api_server, "_push_phone_notification", lambda *a, **k: None)
    monkeypatch.setattr(api_server.DashboardState, "premarket_scanner_payload", lambda self: {})
    monkeypatch.setattr(api_server.DashboardState, "_record_morning_movers", lambda self, *a, **k: None)
    monkeypatch.setattr(api_server, "news_credentials", lambda: None)
    monkeypatch.setattr(api_server, "premarket_history_record_briefing", lambda lines, now: None)
    seen = []
    monkeypatch.setattr(
        api_server, "morning_build_briefing",
        lambda scanner, quotes, now_et, catalysts: seen.append(dict(quotes)) or {"lines": ["x"]},
    )
    now = datetime(2026, 9, 25, 9, 0, tzinfo=ET)
    state._build_morning_briefing(now)
    assert seen[-1] == {}            # both kept clients were dead
    assert held == {}                # ...and both were dropped
    state._build_morning_briefing(now)
    assert "SPY" in seen[-1]         # the rebuilt client answers
