"""SPY and QQQ fire every Setup rule like a stock (2026-11-05 "Yes, open them").

The BOT already traded them since 2026-10-11; MX A+ (strategy), Market turn,
SOLO, MomoX A+ and the chart arrows still skipped every ETF. Now one shared
list (momx/etf_list.RULE_ETFS) lets SPY/QQQ through all of them, and every
other ETF stays out."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import etf_list, market_turn, option_track, strategy

ET = ZoneInfo("America/New_York")
NOW = datetime(2026, 11, 5, 10, 30, tzinfo=ET)


def test_only_spy_and_qqq_are_let_through():
    assert etf_list.RULE_ETFS == {"SPY", "QQQ"}
    assert option_track.BOT_TRADED_ETFS is etf_list.RULE_ETFS, "the BOT and the rules share one list"
    for symbol in ("SPY", "QQQ", "spy"):
        assert etf_list.rule_excluded({"symbol": symbol, "etf": True, "industry": "ETF"}, industry_label=True) is False
    assert etf_list.rule_excluded({"symbol": "IWM", "etf": True}) is True
    assert etf_list.rule_excluded({"symbol": "XYZ", "industry": "ETF"}) is False
    assert etf_list.rule_excluded({"symbol": "XYZ", "industry": "ETF"}, industry_label=True) is True
    assert etf_list.rule_excluded({"symbol": "AAPL"}) is False


def v2_row(symbol, **extra):
    return {"symbol": symbol, "etf": True, "industry": "ETF", "pctChange": 1.5,
            "grade": {"letter": "A+", "checks": {"pushRvol": ["15m", "1h"], "sqzFired": ["1h"]}},
            "m5": {"state": "extended"}, **extra}


def test_mx_a_plus_strategy_judges_spy_like_a_stock_but_not_iwm():
    assert strategy.evaluate(v2_row("SPY"), NOW) == {"v2": True, "v3": True, "daily2": True}
    assert strategy.evaluate(v2_row("QQQ"), NOW, "bear")["v2"] is True
    assert strategy.evaluate(v2_row("IWM"), NOW) == {"v2": False, "v3": False, "daily2": False}


def turn_row(symbol):
    return {"symbol": symbol, "etf": True, "industry": "ETF", "last": 101.0, "pctChange": 2.0,
            "m5": {"vwap": 100.0}, "adx": {"30m": {"plus": 30, "minus": 10, "adx": 30}}}


def test_market_turn_can_pick_spy_and_qqq_but_not_other_etfs():
    picked = [p["symbol"] for p in market_turn.pick([turn_row("SPY"), turn_row("QQQ"), turn_row("IWM")])]
    assert sorted(picked) == ["QQQ", "SPY"]


def test_the_bot_still_fires_on_spy_and_never_on_other_etfs():
    go_at = NOW.timestamp() - 1200
    row = {"grade": {"letter": "A+"}, "adx": {"30m": {"plus": 30, "minus": 10}},
           "m5": {"gapGo": {"goAt": go_at}}, "etf": True}
    assert option_track.fired({**row, "symbol": "SPY"}, False, NOW)[:1] == ["go"]
    assert option_track.fired({**row, "symbol": "DRAM"}, False, NOW) == []
