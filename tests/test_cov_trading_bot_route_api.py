"""Integration: the Bot page's data route, GET /api/momx-scanner/bot, over HTTP.

api_server serves it straight from the bot's day files (it is NOT proxied to
the busy worker - 2026-10-08 the proxied read timed out and the page showed
zeros). These tests write day files to a scratch grade folder and read them
back the way the Bot page does: every field the page's maths reads must
survive (contract x exit legs, 2xEM inputs, AI at entry, Jev / News rules,
Daily 2), nothing else may leak, the gzip cache must refresh when a day file
changes, and bad input must never break the page.
"""
from __future__ import annotations

import gzip
import json

import pytest

from api_harness import api_server, running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER
from momx import option_track as ot
from momx import trade_news

OWNER = "owner-cov-bot@example.com"
VIEWER = "viewer-cov-bot@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
GZIP = {**ADMIN, "Accept-Encoding": "gzip"}


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture
def grade(tmp_path, monkeypatch):
    """A scratch grade folder; the AI scores and news files point at scratch too."""
    base = tmp_path / "grade"
    (base / ot.DIRNAME).mkdir(parents=True)
    (base / "bear" / ot.DIRNAME).mkdir(parents=True)
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(base))
    monkeypatch.setenv("AGX_MOMX_JEV_NEWS_PATH", str(tmp_path / "jev.json"))
    monkeypatch.setenv("AGX_MOMX_TRADE_NEWS_DIR", str(tmp_path / "trade_news"))
    monkeypatch.setattr(api_server, "ARTIFACTS_DIR", tmp_path / "artifacts")
    monkeypatch.setattr(api_server, "_MOMX_BOT_RESPONSES", {})
    return base


def write_day(base, day: str, trades: list, side: str = "bull", raw: str | None = None):
    folder = (base / "bear" if side == "bear" else base) / ot.DIRNAME
    path = folder / f"{day}.json"
    path.write_text(raw if raw is not None else json.dumps({"trades": trades}), encoding="utf-8")
    return path


def trade(rule="go", symbol="AAPL", at="2026-10-28T10:05:00-04:00", **extra):
    return {"rule": rule, "symbol": symbol, "at": at, "status": "closed", "entry": 1.0, "exitPrice": 1.4, **extra}


def every_summary_field():
    """One trade carrying EVERY field the page reads (OptionTrackBook.SUMMARY_KEYS)."""
    values = {key: f"v-{key}" for key in ot.OptionTrackBook.SUMMARY_KEYS}
    values.update({
        "rule": "daily2", "symbol": "NVDA", "at": "2026-10-28T10:05:00-04:00", "status": "closed",
        "entry": 1.2, "exitPrice": 2.0, "zEntry": 0.5, "zExit": 0.1, "eEntry": 0.3, "eExit": 0.9,
        "close": 1.8, "spot": 190.0, "expectedMove": 4.0, "strike": 195.0, "roi2em": 1.5, "riskOk": True,
        "badQuote": False, "holdDone": True, "jevP": 0.42,
        "aiDecision": {"prediction": {"phase": "before_entry", "probability": 0.61, "family": "logistic"}},
        "levelSignal": {"setupCount": 3, "setupTypes": [{"text": "RVOL"}]},
    })
    return values


def test_two_days_served_newest_first_with_the_bear_book_and_keep_days(grade):
    write_day(grade, "2026-10-27", [trade(symbol="AAA")])
    write_day(grade, "2026-10-28", [trade(symbol="BBB"), trade(rule="news", symbol="CCC", jevP=0.8)])
    write_day(grade, "2026-10-26", [trade(symbol="OLD")])
    write_day(grade, "2026-10-28", [trade(rule="news", symbol="PUT1", jevP=0.7)], side="bear")
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot?days=2", headers=ADMIN)
    assert status == 200, body
    assert [day["day"] for day in body["bull"]] == ["2026-10-28", "2026-10-27"]
    assert [t["symbol"] for t in body["bull"][0]["trades"]] == ["BBB", "CCC"]
    assert body["bear"][0]["trades"][0] == {**trade(rule="news", symbol="PUT1", jevP=0.7)}
    assert body["keepDays"] == ot.KEEP_DAYS


def test_every_field_the_page_reads_survives_and_nothing_else_leaks(grade):
    full = every_summary_field()
    write_day(grade, "2026-10-28", [{**full, "secret": "broker-token", "internalQueue": [1, 2]}])
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot?days=1", headers=ADMIN)
    assert status == 200, body
    served = body["bull"][0]["trades"][0]
    assert served == full
    assert "secret" not in served and "internalQueue" not in served


def test_the_shadow_ai_score_is_stamped_per_side_for_the_ai_pick_book(grade, tmp_path):
    at = "2026-10-28T10:05:00-04:00"
    write_day(grade, "2026-10-28", [trade(symbol="AAPL", at=at), trade(symbol="MSFT", at=at)])
    write_day(grade, "2026-10-28", [trade(symbol="AAPL", at=at)], side="bear")
    scores = tmp_path / "artifacts" / "momx_ai_filter" / "scores"
    scores.mkdir(parents=True)
    (scores / "2026-10-28.json").write_text(json.dumps({
        f"bull|go|AAPL|{at}": 0.61, f"bear|go|AAPL|{at}": 0.22,
    }), encoding="utf-8")
    with running_api() as api:
        _status, body = api.get("/api/momx-scanner/bot?days=1", headers=ADMIN)
    bull = {t["symbol"]: t for t in body["bull"][0]["trades"]}
    assert bull["AAPL"]["aiP"] == 0.61
    assert "aiP" not in bull["MSFT"]
    assert body["bear"][0]["trades"][0]["aiP"] == 0.22


def test_a_non_admin_gets_403_and_no_trades(grade):
    write_day(grade, "2026-10-28", [trade()])
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot?days=1", headers={ACCESS_EMAIL_HEADER: VIEWER})
    assert (status, body) == (403, {"error": "admin only"})


def test_the_gzip_answer_is_reused_until_a_day_file_changes(grade, monkeypatch):
    path = write_day(grade, "2026-10-28", [trade(symbol="AAPL")])
    monkeypatch.setattr(api_server, "MOMX_BOT_RESPONSE_MIN_AGE", 0.0)
    with running_api() as api:
        _s, first = api.get("/api/momx-scanner/bot?days=1", headers=GZIP)
        assert api.last_headers.get("Content-Encoding") == "gzip"
        cached = api_server._MOMX_BOT_RESPONSES[1][2]
        _s, again = api.get("/api/momx-scanner/bot?days=1", headers=GZIP)
        assert api_server._MOMX_BOT_RESPONSES[1][2] is cached          # same bytes reused
        path.write_text(json.dumps({"trades": [trade(symbol="AAPL"), trade(symbol="NEW1")]}), encoding="utf-8")
        _s, fresh = api.get("/api/momx-scanner/bot?days=1", headers=GZIP)
    assert first == again
    assert [t["symbol"] for t in fresh["bull"][0]["trades"]] == ["AAPL", "NEW1"]
    assert json.loads(gzip.decompress(api_server._MOMX_BOT_RESPONSES[1][2]))["bull"][0]["trades"][1]["symbol"] == "NEW1"


def test_junk_days_falls_back_to_the_default_and_a_huge_range_is_clamped(grade):
    for day in range(20, 29):
        write_day(grade, f"2026-10-{day}", [trade(symbol=f"S{day}")])
    with running_api() as api:
        junk_status, junk = api.get("/api/momx-scanner/bot?days=abc", headers=ADMIN)
        huge_status, huge = api.get("/api/momx-scanner/bot?days=99999", headers=ADMIN)
        neg_status, negative = api.get("/api/momx-scanner/bot?days=-5", headers=ADMIN)
        year_status, year = api.get("/api/momx-scanner/bot?days=180", headers=ADMIN)
    assert junk_status == huge_status == neg_status == year_status == 200
    assert len(junk["bull"]) == 5                     # the page's default range
    assert len(huge["bull"]) == 9 and len(year["bull"]) == 9 <= ot.KEEP_DAYS
    assert [d["day"] for d in negative["bull"]] == ["2026-10-28"]


@pytest.mark.xfail(strict=True, reason=(
    "the Bot answer cache (_MOMX_BOT_RESPONSES) is keyed by the raw ?days= value, so every distinct "
    "out-of-range value stores another full gzipped copy of the same clamped answer and the cache never shrinks"))
def test_out_of_range_days_values_share_one_cached_answer(grade):
    write_day(grade, "2026-10-28", [trade()])
    with running_api() as api:
        for days in (500, 501, 502, 99999):
            api.get(f"/api/momx-scanner/bot?days={days}", headers=GZIP)
    assert len(api_server._MOMX_BOT_RESPONSES) == 1


def test_a_half_written_day_file_is_skipped_and_the_other_days_still_load(grade):
    write_day(grade, "2026-10-27", [trade(symbol="GOOD")])
    write_day(grade, "2026-10-28", [], raw='{"trades": [{"rule": "go", "symb')
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot?days=5", headers=ADMIN)
    assert status == 200, body
    assert [d["day"] for d in body["bull"]] == ["2026-10-27"]
    assert body["bull"][0]["trades"][0]["symbol"] == "GOOD"


def test_the_bot_read_never_scans_the_news_database(grade, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("a Bot read must not re-judge historical headlines")

    monkeypatch.setattr(trade_news, "annotate", forbidden)
    write_day(grade, "2026-10-28", [trade(rule="news", symbol="NEWS1", jevP=0.9)])
    with running_api() as api:
        status, body = api.get("/api/momx-scanner/bot?days=1", headers=GZIP)
    assert status == 200, body
    assert body["bull"][0]["trades"][0]["rule"] == "news"


def test_jev_and_daily2_rules_reach_the_page_on_both_boards(grade):
    write_day(grade, "2026-10-28", [trade(rule="jev", symbol="J1", jevP=0.35), trade(rule="daily2", symbol="D1")])
    write_day(grade, "2026-10-28", [trade(rule="daily2", symbol="D2"), trade(rule="news", symbol="N2")], side="bear")
    with running_api() as api:
        _status, body = api.get("/api/momx-scanner/bot?days=1", headers=ADMIN)
    assert [(t["rule"], t["symbol"], t.get("jevP")) for t in body["bull"][0]["trades"]] == [("jev", "J1", 0.35), ("daily2", "D1", None)]
    assert [t["rule"] for t in body["bear"][0]["trades"]] == ["daily2", "news"]
