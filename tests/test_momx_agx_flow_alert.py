"""Unit: AGX Flow phone alert (momx/agx_flow_alert.py, 2026-11-17 "All six"). Schwab and ntfy are fakes; every file
goes to tmp_path."""
from __future__ import annotations

from datetime import timedelta

import pytest

from momx import agx_flow, agx_flow_alert as alert
from test_momx_agx_flow import NOW, chain

WINDOW = {"start": 9 * 60 + 15, "end": 15 * 60 + 30}
CONFIGS = {"me@example.com": {"ntfyTopic": "topic-me", "pushWindow": WINDOW},
           "other@example.com": {"ntfyTopic": "topic-other", "pushWindow": WINDOW},
           "nophone@example.com": {"ntfyTopic": "", "pushWindow": WINDOW}}


@pytest.fixture
def world(monkeypatch, tmp_path):
    replies: dict = {}

    def fake(ticker, today):
        reply = replies.get(ticker, chain)
        return reply() if callable(reply) else reply

    monkeypatch.setattr(agx_flow, "fetch", fake)
    agx_flow.reset()
    sent: list = []
    clock = [1_000_000.0]
    yield {"root": tmp_path, "replies": replies, "sent": sent, "clock": clock,
           "poster": lambda topic, title, body, tags: sent.append((topic, title, body, tags))}
    agx_flow.wait_idle(5)
    agx_flow.reset()


def _check(w, symbols, now):
    return alert.check(symbols, w["root"], now, poster=w["poster"], configs=CONFIGS, clock=lambda: w["clock"][0])


def test_only_todays_big_unusual_contracts_on_watchlist_stocks(world):
    """$1M+ and unusual (volume >= 500 and above open interest); Mag7 and ETFs left out; yesterday's copy never counts."""
    agx_flow.board(["PLTR", "NVDA"], world["root"], NOW, wait=True)
    rows = alert.big_unusual(["PLTR", "NVDA", "SPY"], world["root"], NOW)
    assert [(r["ticker"], r["side"], r["strike"], r["premium"]) for r in rows] == [("PLTR", "CALL", 185.0, 1_200_000)]
    assert alert.big_unusual(["PLTR"], world["root"], NOW + timedelta(days=1)) == []          # read yesterday
    agx_flow.board(["EARLY"], world["root"], NOW.replace(hour=9, minute=0), wait=True)      # a premarket read
    assert alert.big_unusual(["EARLY"], world["root"], NOW) == []                              # is not today's session
    world["replies"]["SMOL"] = lambda: chain(spot=4.0)
    agx_flow.board(["SMOL"], world["root"], NOW, wait=True)
    assert alert.big_unusual(["SMOL"], world["root"], NOW) == []                               # under $10


def test_the_switch_is_per_account_and_off_until_turned_on(tmp_path):
    assert alert.is_on(tmp_path, "me@example.com") is False
    assert alert.set_on(tmp_path, "Me@Example.com", True) is True
    assert alert.is_on(tmp_path, "me@example.com") is True
    assert alert.is_on(tmp_path, "other@example.com") is False
    alert.set_on(tmp_path, "other@example.com", True)
    alert.set_on(tmp_path, "me@example.com", False)
    assert alert.is_on(tmp_path, "me@example.com") is False and alert.is_on(tmp_path, "other@example.com") is True
    assert alert.set_on(tmp_path, "", True) is False and alert.is_on(tmp_path, "") is False


def test_one_bundled_push_to_accounts_that_turned_it_on_then_quiet(world):
    root = world["root"]
    alert.set_on(root, "me@example.com", True)
    alert.set_on(root, "nophone@example.com", True)            # on, but no phone channel: nothing to send to
    agx_flow.board(["PLTR"], root, NOW, wait=True)
    first = _check(world, ["PLTR"], NOW)
    assert first["pushed"] == 1 and first["contracts"] == 1
    (topic, title, body, tags), = world["sent"]
    assert topic == "topic-me" and title == "AGX Flow: 1 new $1M+ unusual contract" and tags == "whale"
    assert body.splitlines()[0] == "PLTR 185 Call Nov 20 · $1.2M · vol 6,000 vs OI 1,200 · Bought"
    assert "not a direction signal" in body
    # The same contract never alerts twice in a day, even after a restart (the ledger is on disk).
    assert _check(world, ["PLTR"], NOW + timedelta(minutes=1))["reason"] == "nothing new"
    # A new one inside 10 minutes waits, then goes out on its own.
    world["replies"]["HOOD"] = chain
    agx_flow.board(["HOOD"], root, NOW + timedelta(minutes=2), wait=True)
    world["clock"][0] += 120
    waiting = _check(world, ["PLTR", "HOOD"], NOW + timedelta(minutes=2))
    assert waiting["reason"] == "too soon" and waiting["waiting"] == 1 and len(world["sent"]) == 1
    world["clock"][0] += alert.PUSH_EVERY_SECONDS
    again = _check(world, ["PLTR", "HOOD"], NOW + timedelta(minutes=12))
    assert again["pushed"] == 1 and world["sent"][-1][2].startswith("HOOD 185 Call")


def test_no_push_when_the_market_is_closed_or_outside_the_phone_window(world):
    root = world["root"]
    alert.set_on(root, "me@example.com", True)
    agx_flow.board(["PLTR"], root, NOW, wait=True)
    assert _check(world, ["PLTR"], NOW.replace(hour=17))["reason"] == "market closed"
    assert _check(world, ["PLTR"], NOW + timedelta(days=4))["reason"] == "market closed"     # Saturday
    late = NOW.replace(hour=15, minute=45)                                                    # open, past 3:30 window
    result = _check(world, ["PLTR"], late)
    assert result["pushed"] == 0 and world["sent"] == []
    assert _check(world, ["PLTR"], late + timedelta(minutes=1))["reason"] == "nothing new"   # not saved for later


def test_a_new_day_starts_a_new_ledger(world):
    root = world["root"]
    alert.set_on(root, "me@example.com", True)
    agx_flow.board(["PLTR"], root, NOW, wait=True)
    assert _check(world, ["PLTR"], NOW)["pushed"] == 1
    tomorrow = NOW + timedelta(days=1)
    agx_flow.board(["PLTR"], root, tomorrow, wait=True)
    world["clock"][0] += alert.PUSH_EVERY_SECONDS
    assert _check(world, ["PLTR"], tomorrow)["pushed"] == 1 and len(world["sent"]) == 2


def test_message_bundles_at_most_six_lines():
    rows = [{"ticker": f"T{i}", "side": "PUT" if i % 2 else "CALL", "strike": 100 + i, "expiry": "2026-12-04",
             "premium": 2_500_000 - i, "volume": 9000, "oi": 100, "fill": "bid" if i % 2 else "mid"} for i in range(9)]
    title, body = alert.message(rows)
    lines = body.splitlines()
    assert title == "AGX Flow: 9 new $1M+ unusual contracts"
    assert lines[0] == "T0 100 Call Dec 4 · $2.5M · vol 9,000 vs OI 100"
    assert lines[1].endswith("· Sold") and lines[6] == "+3 more on AGX Flow" and len(lines) == 8



def test_a_failed_push_is_tried_again_not_lost(world):
    """Review 2026-11-17: when ntfy is down the contracts stay new, and the next pass sends them."""
    root = world["root"]
    alert.set_on(root, "me@example.com", True)
    agx_flow.board(["PLTR"], root, NOW, wait=True)

    def down(topic, title, body, tags):
        raise OSError("ntfy down")
    failed = alert.check(["PLTR"], root, NOW, poster=down, configs=CONFIGS, clock=lambda: world["clock"][0])
    assert failed == {"pushed": 0, "reason": "push failed", "contracts": 1}
    retry = _check(world, ["PLTR"], NOW + timedelta(minutes=1))
    assert retry["pushed"] == 1 and world["sent"][0][2].startswith("PLTR 185 Call")


def test_no_alert_on_a_market_holiday(world):
    from datetime import datetime
    root = world["root"]
    alert.set_on(root, "me@example.com", True)
    thanksgiving = datetime(2026, 11, 26, 11, 0, tzinfo=NOW.tzinfo)
    agx_flow.board(["PLTR"], root, thanksgiving, wait=True)
    assert _check(world, ["PLTR"], thanksgiving)["reason"] == "market closed" and world["sent"] == []
