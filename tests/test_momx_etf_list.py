"""momx/etf_list.py - "use only stocks": no rule fires on an ETF (2026-09-27)."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from momx import etf_list, market_turn, momox_aplus, option_track, strategy

NASDAQ = "Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares\n" + "\n".join(
    [f"S{i}|Stock {i}|Q|N|N|100|N|N" for i in range(1200)]
    + ["IBIT|iShares Bitcoin Trust ETF|G|N|N|100|Y|N", "TQQQ|ProShares UltraPro QQQ|G|N|N|100|Y|N",
       "AAPL|Apple Inc. - Common Stock|Q|N|N|40|N|N"]) + "\nFile Creation Time: x||||||\n"
OTHER = "ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol\n" + "\n".join(
    [f"O{i}|Other {i}|N|O{i}|N|100|N|O{i}" for i in range(1200)]
    + ["DRAM|Roundhill Memory ETF|Z|DRAM|Y|100|N|DRAM", "UMAC|Unusual Machines, Inc. Common Stock|A|UMAC|N|100|N|UMAC"])


def seeded(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_ETF_PATH", str(tmp_path / "etf.json"))
    monkeypatch.setenv("AGX_MOMX_ETF_REFRESH", "0")
    assert etf_list.refresh(fetch=lambda url: NASDAQ if "nasdaqlisted" in url else OTHER)


def test_official_flag_not_the_industry_label(tmp_path, monkeypatch):
    seeded(tmp_path, monkeypatch)
    assert etf_list.is_etf("IBIT") is True and etf_list.is_etf("DRAM") is True and etf_list.is_etf("tqqq") is True
    assert etf_list.is_etf("AAPL") is False and etf_list.is_etf("UMAC") is False
    assert etf_list.is_etf("ZZZQ") is None          # not listed = unknown, not excluded


def test_a_bad_download_keeps_the_old_copy(tmp_path, monkeypatch):
    seeded(tmp_path, monkeypatch)
    assert etf_list.refresh(fetch=lambda url: "<html>error</html>") is False
    assert etf_list.is_etf("IBIT") is True


def test_mark_stamps_only_etf_rows(tmp_path, monkeypatch):
    seeded(tmp_path, monkeypatch)
    payload = {"rows": [{"symbol": "IBIT", "industry": "Crypto"}, {"symbol": "AAPL"}], "rest": [{"symbol": "DRAM", "industry": "Semis"}]}
    etf_list.mark(payload)
    assert payload["rows"][0]["etf"] is True and "etf" not in payload["rows"][1] and payload["rest"][0]["etf"] is True


def test_the_rules_skip_etf_rows():
    now = datetime(2026, 9, 28, 10, 0, tzinfo=ZoneInfo("America/New_York"))
    etf = {"symbol": "DRAM", "etf": True, "industry": "Semis", "grade": {"letter": "A+"},
           "m5": {"gapGo": {"goAt": now.timestamp() - 1200}}, "adx": {"30m": {"plus": 30, "minus": 10}},
           "momoxAPlus": {"at": "x"}}
    assert strategy.evaluate(etf, now) == {"v2": False, "v3": False, "daily2": False}
    assert momox_aplus.is_etf(etf) is True
    assert option_track.fired(etf, False, now) == []
    assert option_track.fired(dict(etf, etf=False), False, now)[:1] == ["go"]
