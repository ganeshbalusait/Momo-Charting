"""BOT rules for the last two Setup tags it never traded (2026-11-05):
chartSqz = a live chart 🔥 today; warn = a GO-family setup with a ⚠ warning."""
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from momx.option_track import fired, INFO_RULES

ET = ZoneInfo('America/New_York')
NOW = datetime(2026, 11, 5, 10, 30, tzinfo=ET)


def flame(tf, minutes_ago, **extra):
    at = NOW - timedelta(minutes=minutes_ago)
    return {'timeframe': tf, 'at': at.isoformat(), 'seenAt': at.isoformat(), **extra}


def test_both_rules_are_tracked_info_rules():
    assert 'chartSqz' in INFO_RULES and 'warn' in INFO_RULES


def test_chart_squeeze_live_flame_fires_and_expires_like_the_screen():
    row = {'symbol': 'AMD', 'chartSqueeze': [flame('1h', 30)]}
    assert 'chartSqz' in fired(row, False, NOW)
    assert 'chartSqz' in fired(row, True, NOW), 'bear board reads its own flames too'
    assert 'chartSqz' not in fired({'symbol': 'AMD', 'chartSqueeze': [flame('1h', 61)]}, False, NOW), 'one 1h candle only'
    assert 'chartSqz' in fired({'symbol': 'AMD', 'chartSqueeze': [flame('2h', 90)]}, False, NOW)
    gone = flame('1h', 10, goneAt=NOW.isoformat())
    assert 'chartSqz' not in fired({'symbol': 'AMD', 'chartSqueeze': [gone]}, False, NOW), 'removed by the chart'
    yesterday = flame('D', 60 * 20)
    assert 'chartSqz' not in fired({'symbol': 'AMD', 'chartSqueeze': [yesterday]}, False, NOW)
    assert 'chartSqz' not in fired({'symbol': 'AMD', 'chartSqueeze': [flame('15m', 5)]}, False, NOW), 'unknown timeframe'


def test_warning_needs_a_go_family_setup_and_a_warning():
    turn = {'symbol': 'AMD', 'marketTurn': {'at': NOW.isoformat()}, 'pctChange': 9.5}
    assert {'turn', 'warn'} <= set(fired(turn, False, NOW))
    calm = {**turn, 'pctChange': 2.0}
    assert 'warn' not in fired(calm, False, NOW), 'no warning'
    plain = {'symbol': 'AMD', 'pctChange': 9.5, 'm5': {'pattern': 'explosive'}}
    assert 'warn' not in fired(plain, False, NOW), 'a warning alone, without a GO-family setup'


def test_etf_still_fires_nothing():
    row = {'symbol': 'XLK', 'etf': True, 'chartSqueeze': [flame('1h', 5)], 'marketTurn': {'at': NOW.isoformat()}, 'pctChange': 9}
    assert fired(row, False, NOW) == []
