"""The Bot page data is served by api_server from the bot's day files (2026-10-08):
proxied to the busy worker it timed out after 20 s and showed zeros."""

from __future__ import annotations

import json

import api_server
from momx import option_track as ot


def test_history_reads_day_files_newest_first_and_reparses_only_changed_ones(tmp_path):
    folder = tmp_path / ot.DIRNAME
    folder.mkdir()
    (folder / "2026-10-07.json").write_text(json.dumps({"trades": [{"rule": "go", "symbol": "A", "secret": 1}]}), encoding="utf-8")
    (folder / "2026-10-08.json").write_text(json.dumps({"trades": [{"rule": "go", "symbol": "B"}]}), encoding="utf-8")
    first = api_server._momx_bot_history(tmp_path, 5)
    assert [d["day"] for d in first] == ["2026-10-08", "2026-10-07"]
    assert first[1]["trades"] == [{"rule": "go", "symbol": "A"}]      # only the summary keys
    first[0]["trades"][0]["newsJev"] = "bullish"                       # a caller's stamp must not leak
    (folder / "2026-10-08.json").write_text(json.dumps({"trades": [{"rule": "go", "symbol": "B"}, {"rule": "go", "symbol": "C"}]}), encoding="utf-8")
    again = api_server._momx_bot_history(tmp_path, 5)
    assert [t["symbol"] for t in again[0]["trades"]] == ["B", "C"]
    assert "newsJev" not in again[0]["trades"][0]


def test_bot_answer_version_moves_only_when_a_day_or_news_file_changes(tmp_path, monkeypatch):
    # 2026-10-16: the gzipped Bot answer is reused while this tuple stays the same.
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    monkeypatch.setenv("AGX_MOMX_JEV_NEWS_PATH", str(tmp_path / "jev.json"))
    monkeypatch.setenv("AGX_MOMX_TRADE_NEWS_DIR", str(tmp_path / "trade_news"))
    folder = tmp_path / ot.DIRNAME
    folder.mkdir()
    (tmp_path / "bear" / ot.DIRNAME).mkdir(parents=True)
    day = folder / "2026-10-15.json"
    day.write_text(json.dumps({"trades": []}), encoding="utf-8")
    first = api_server._momx_bot_version(5)
    assert api_server._momx_bot_version(5) == first
    day.write_text(json.dumps({"trades": [{"rule": "go"}]}), encoding="utf-8")
    second = api_server._momx_bot_version(5)
    assert second != first
    (tmp_path / "jev.json").write_text("{}", encoding="utf-8")
    third = api_server._momx_bot_version(5)
    assert third != second and third[0] == second[0]     # news half moved, day files did not


def test_bot_payload_does_not_block_on_news_database(monkeypatch):
    from momx import trade_news
    def forbidden(*args, **kwargs):
        raise AssertionError("BOT reads must not scan headline history")
    monkeypatch.setattr(trade_news, "annotate", forbidden)
    monkeypatch.setattr(api_server, "_momx_bot_history", lambda base, days: [{"day": "2026-10-30", "trades": []}])
    assert api_server._momx_bot_payload(1)["bull"][0]["day"] == "2026-10-30"
