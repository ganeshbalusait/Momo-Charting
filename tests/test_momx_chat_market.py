"""Unit: momx/chat_market.py, the Ask chat's market tools, on fixed scratch files.

Every rule the plan set for these tools has a test: dead and spent expiries, the
5-day file limit, ET times, IV per expiry (never the root value), the max pain
label, the High OI board name, "not tracked" earnings, an empty cache said
plainly, news with no cause claim, Long Term with no buy call.
"""
from __future__ import annotations

import gzip
import json
import math
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from momx import chat_market as market
from momx import chat_state

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import build_chat_sector_cards as cards_builder  # noqa: E402

ET = ZoneInfo("America/New_York")
# Tuesday, mid-session.
NOW = datetime(2026, 11, 10, 11, 0, tzinfo=ET)
SAVED = "2026-11-10T09:45:00-06:00"          # the machine writes Central: 10:45 ET
ROOT_IV = 29.0


def contract(side, strike, expiry, *, oi=100, bid=1.0, ask=1.2, delta=0.5, volume=0):
    return {"side": side, "strike": strike, "expiry": expiry, "open_interest": oi, "bid": bid, "ask": ask,
            "delta": delta, "volume": volume, "days_to_expiration": 99, "mark": (bid + ask) / 2}


def strikes_for(expiry, strikes=(90, 95, 100, 105, 110)):
    return [contract(side, strike, expiry) for strike in strikes for side in ("CALL", "PUT")]


def chain_payload(**over):
    payload = {
        "symbol": "AAA", "optionQuoteSchemaVersion": 1, "live": True, "scannedAt": SAVED, "underlyingPrice": 100.0,
        "todayChangePercent": 1.25, "impliedVolatility": ROOT_IV,
        "expiryExpectedMoves": {"2026-11-09": 9.0, "2026-11-10": 1.0, "2026-11-13": 3.0, "2026-11-20": 6.0},
        "selectedExpiryChainRows": (strikes_for("2026-11-09") + strikes_for("2026-11-10")
                                    + strikes_for("2026-11-13") + strikes_for("2026-11-20")),
        "currentAtm": {"expiry": "2026-11-10", "expectedMove": 1.0},
    }
    payload.update(over)
    return payload


@pytest.fixture
def base(tmp_path):
    (tmp_path / "oi_chain_cache").mkdir()
    return tmp_path


@pytest.fixture(autouse=True)
def _empty_app():
    """No app memory unless a test binds some; never the real STATE."""
    chat_state.bind(state=SimpleNamespace(), stream=SimpleNamespace(latest_equities=lambda symbols: {}))
    yield
    chat_state.unbind()


def write_chain(base, payload, symbol="AAA"):
    with gzip.open(base / "oi_chain_cache" / f"{symbol}.json.gz", "wt", encoding="utf-8") as handle:
        json.dump(payload, handle)


def ctx(base, **over):
    return {"scope": "scanner", "account": "a@example.com", "pageTicker": None, "pageName": None, "ticker": None,
            "day": None, "side": None, "now": NOW, "artifacts": str(base), **over}


def run(tool, base, args=None, **over):
    return market.TOOLS[tool]["handler"](args or {}, ctx(base, **over))


def text(reply):
    return " ".join(reply["lines"])


# ---------------------------------------------------------------- the contract

def test_every_tool_has_the_contract_shape(base):
    assert sorted(market.TOOLS) == sorted(["chain", "walls", "expected_move", "max_pain", "sectors", "premarket",
                                           "news", "earnings", "long_term", "morning_brief", "quote", "price_outlook"])
    for name, tool in market.TOOLS.items():
        assert tool["scope"] == "all" and tool["description"] and tool["args"]["type"] == "object", name
        reply = tool["handler"]({"ticker": "ZZZ"}, ctx(base))
        assert set(reply) == {"lines", "facts", "links", "table", "asOf"}, name
        assert reply["lines"] and all(isinstance(line, str) for line in reply["lines"]), name


def test_a_session_with_no_scope_is_refused(base):
    write_chain(base, chain_payload())
    reply = market.TOOLS["chain"]["handler"]({"ticker": "AAA"}, {**ctx(base), "scope": "nobody"})
    assert reply["facts"].get("refused") and reply["table"] is None and not reply["links"]


def test_a_broken_file_is_a_plain_line_not_an_exception(base):
    write_chain(base, chain_payload(selectedExpiryChainRows=["junk", None], expiryExpectedMoves="junk"))
    assert "no live expiries" in text(run("chain", base, {"ticker": "AAA"}))
    (base / "oi_chain_cache" / "BBB.json.gz").write_bytes(b"not gzip")
    assert "no saved" in text(run("chain", base, {"ticker": "BBB"}))


def test_a_ticker_cannot_walk_out_of_the_cache_folder(base):
    reply = run("chain", base, {"ticker": "../../secret"})
    assert reply["facts"].get("needsTicker")


# ---------------------------------------------------------------- chain rules

def test_option_chain_uses_the_page_ticker_and_stays_near_the_money(base):
    strikes = tuple(range(50, 151, 5))
    write_chain(base, chain_payload(selectedExpiryChainRows=strikes_for("2026-11-13", strikes),
                                    expiryExpectedMoves={"2026-11-13": 3.0}))
    reply = run("chain", base, pageTicker="AAA", ticker="ZETA")
    table = reply["table"]
    assert table["kind"] == "rows" and reply["facts"]["ticker"] == "AAA"
    assert [row["strike"] for row in table["rows"]] == [str(s) for s in range(70, 131, 5)]
    assert reply["links"][0] == {"label": "Open AAA on Quick Options", "view": "Quick Options", "ticker": "AAA"}
    assert all(isinstance(value, str) for row in table["rows"] for value in row.values())


def test_option_chain_with_no_ticker_links_and_asks_which(base):
    reply = run("chain", base)
    assert "Which ticker" in text(reply) and reply["table"] is None
    assert reply["links"] == [{"label": "Open Quick Options", "view": "Quick Options"}]


def test_expired_expiry_is_dropped_and_days_are_recounted(base):
    write_chain(base, chain_payload())
    reply = run("chain", base, {"ticker": "AAA"})
    assert reply["facts"]["expiry"] == "2026-11-13"        # 11-10 is spent, 11-09 is gone
    assert "2026-11-09" not in reply["facts"]["expiries"]
    assert "(3 days)" in text(reply)


def test_today_expiry_counts_until_the_close_and_not_after(base):
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-10": 2.0, "2026-11-13": 3.0}))
    assert run("chain", base, {"ticker": "AAA"})["facts"]["expiry"] == "2026-11-10"
    at_close = datetime(2026, 11, 10, 16, 0, tzinfo=ET)
    assert run("chain", base, {"ticker": "AAA"}, now=at_close)["facts"]["expiry"] == "2026-11-13"


def test_spent_expiry_is_skipped(base):
    # 2.0 < 6.0 * 0.35: spent. 3.0 is not spent against 6.0.
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-10": 2.0, "2026-11-13": 6.0, "2026-11-20": 8.0}))
    reply = run("expected_move", base, {"ticker": "AAA"})
    assert reply["facts"]["expiry"] == "2026-11-13"
    assert [row["expiry"] for row in reply["table"]["rows"]] == ["2026-11-13", "2026-11-20"]


def test_asking_for_a_spent_expiry_says_so(base):
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-10": 2.0, "2026-11-13": 6.0, "2026-11-20": 8.0}))
    reply = run("expected_move", base, {"ticker": "AAA", "expiry": "today"})
    assert reply["facts"]["expiry"] == "2026-11-13" and "spent" in text(reply)


def test_expected_move_friday(base):
    write_chain(base, chain_payload())
    reply = run("expected_move", base, {"ticker": "AAA", "expiry": "friday"})
    assert reply["facts"]["expiry"] == "2026-11-13" and reply["facts"]["expectedMove"] == 3.0
    assert "97.00" in text(reply) and "103.00" in text(reply)


@pytest.mark.parametrize("raw,expected", [
    ("2026-11-20", "2026-11-20"), ("11/20", "2026-11-20"), ("friday", "2026-11-13"), ("next friday", "2026-11-20"),
    ("today", "2026-11-10"), ("tomorrow", "2026-11-11"), ("1/15", "2027-01-15"),
])
def test_expiry_words(raw, expected):
    assert market._expiry_wanted(raw, NOW).isoformat() == expected


def test_friday_asked_on_friday_evening_is_next_friday():
    assert market._expiry_wanted("friday", datetime(2026, 11, 13, 17, 0, tzinfo=ET)).isoformat() == "2026-11-20"


def test_expiry_that_does_not_exist_names_the_one_used(base):
    write_chain(base, chain_payload())
    reply = run("expected_move", base, {"ticker": "AAA", "expiry": "2026-11-16"})
    assert reply["facts"]["expiry"] == "2026-11-20" and "no saved 2026-11-16" in text(reply)
    reply = run("expected_move", base, {"ticker": "AAA", "expiry": "2026-11-02"})
    assert reply["facts"]["expiry"] == "2026-11-13" and "already passed" in text(reply)


def test_a_file_over_five_days_old_is_refused(base):
    write_chain(base, chain_payload(scannedAt="2026-11-04T10:00:00-05:00"))
    for tool in ("chain", "walls", "expected_move", "max_pain", "quote"):
        reply = run(tool, base, {"ticker": "AAA"})
        assert reply["facts"].get("tooOld") and reply["table"] is None, tool
        assert "too old" in text(reply) and reply["links"], tool


def test_a_file_just_inside_five_days_is_quoted(base):
    write_chain(base, chain_payload(scannedAt="2026-11-05T11:30:00-05:00"))
    assert run("quote", base, {"ticker": "AAA"})["facts"]["price"] == 100.0


def test_saved_time_is_converted_to_et(base):
    write_chain(base, chain_payload())
    reply = run("chain", base, {"ticker": "AAA"})
    assert reply["asOf"] == "as of 10:45 ET" and "10:45 ET" in text(reply)
    tomorrow = datetime(2026, 11, 11, 9, 0, tzinfo=ET)
    assert run("chain", base, {"ticker": "AAA"}, now=tomorrow)["asOf"] == "as of Nov 10 10:45 ET"


def test_missing_cache_says_so_and_links(base):
    reply = run("walls", base, {"ticker": "NOPE"})
    assert "no saved" in text(reply) and reply["links"][0]["sub"] == "high-oi"


def test_old_schema_file_is_not_read(base):
    write_chain(base, chain_payload(optionQuoteSchemaVersion=0))
    assert run("chain", base, {"ticker": "AAA"})["facts"].get("missing")


def test_full_file_beats_a_front_only_copy_in_memory(base):
    write_chain(base, chain_payload())
    slim = chain_payload(frontExpiryOnly=True, scannedAt="2026-11-10T10:59:00-05:00",
                         expiryExpectedMoves={"2026-11-10": 2.0},
                         selectedExpiryChainRows=strikes_for("2026-11-10"))
    chat_state.bind(state=SimpleNamespace(oi_finder_chain_cache={"AAA": (NOW, slim)}))
    reply = run("chain", base, {"ticker": "AAA"})
    assert reply["facts"]["expiry"] == "2026-11-13" and "Only the nearest" not in text(reply)


def test_front_only_copy_is_flagged(base):
    slim = chain_payload(frontExpiryOnly=True, expiryExpectedMoves={"2026-11-13": 3.0},
                         selectedExpiryChainRows=strikes_for("2026-11-13"))
    chat_state.bind(state=SimpleNamespace(oi_finder_chain_cache={"AAA": (NOW, slim)}))
    assert "Only the nearest expiry is saved" in text(run("chain", base, {"ticker": "AAA"}))


def test_newest_full_copy_wins(base):
    write_chain(base, chain_payload(underlyingPrice=100.0))
    newer = chain_payload(underlyingPrice=101.0, scannedAt="2026-11-10T10:58:00-05:00")
    chat_state.bind(state=SimpleNamespace(oi_finder_chain_cache={"AAA": (NOW, newer)}))
    assert run("chain", base, {"ticker": "AAA"})["facts"]["spot"] == 101.0


def test_no_unusual_flow_words_from_disk(base):
    write_chain(base, chain_payload(unusualOtmActivity={"strongestBullish": {"strike": 105}}))
    for tool in ("chain", "walls", "expected_move", "max_pain"):
        assert "unusual" not in json.dumps(run(tool, base, {"ticker": "AAA"})).lower(), tool


# ---------------------------------------------------------------- IV

def test_iv_is_backed_out_per_expiry_and_never_the_root(base):
    write_chain(base, chain_payload())
    reply = run("expected_move", base, {"ticker": "AAA", "expiry": "2026-11-20"})
    expected = 6.0 / (100.0 * math.sqrt(9 / 365)) * 100          # 10 days from the save, less today
    assert reply["facts"]["ivApproxPercent"] == round(expected, 1)
    assert f"IV about {expected:.0f}%" in text(reply) and "approximate" in text(reply)
    assert "29" not in " ".join(line for line in reply["lines"] if "IV" in line)
    assert "impliedVolatility" not in json.dumps(reply)


def test_front_expiry_iv_uses_the_straddle_form(base):
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-10": 2.0, "2026-11-13": 3.0}))
    chain, _ = market._open_chain("AAA", ctx(base), "chain", {})
    assert chain.implied_vol_percent("2026-11-10") == pytest.approx(2.0 / (0.8 * 100.0 * math.sqrt(1 / 365)) * 100)


def test_no_move_means_no_iv_line(base):
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-13": None}, selectedExpiryChainRows=strikes_for("2026-11-13")))
    reply = run("chain", base, {"ticker": "AAA"})
    assert reply["facts"]["expiry"] == "2026-11-13" and "IV" not in text(reply)
    assert "no expected move" in text(run("expected_move", base, {"ticker": "AAA"}))


# ---------------------------------------------------------------- walls

def wall_rows(expiry="2026-11-13"):
    return [contract("CALL", 105, expiry, oi=500), contract("CALL", 110, expiry, oi=900),
            contract("CALL", 95, expiry, oi=99999),   # in the money: never a wall
            contract("PUT", 95, expiry, oi=700), contract("PUT", 90, expiry, oi=300)]


def test_walls_are_the_high_oi_board(base):
    write_chain(base, chain_payload(selectedExpiryChainRows=wall_rows(), expiryExpectedMoves={"2026-11-13": 10.0}))
    reply = run("walls", base, {"ticker": "AAA"})
    assert "High OI board" in text(reply) and reply["facts"]["source"] == "High OI board"
    assert reply["facts"]["calls"][0]["strike"] == 110 and reply["facts"]["puts"][0]["strike"] == 95
    assert "Biggest call wall: 110 (OI 900" in text(reply)
    assert reply["links"][0]["sub"] == "high-oi" and reply["links"][0]["view"] == "Quick Options"


def test_put_wall_only(base):
    write_chain(base, chain_payload(selectedExpiryChainRows=wall_rows(), expiryExpectedMoves={"2026-11-13": 10.0}))
    reply = run("walls", base, {"ticker": "AAA", "side": "put"})
    assert "put wall: 95" in text(reply) and "call wall" not in text(reply)
    assert {row["side"] for row in reply["table"]["rows"]} == {"Put"}


@pytest.mark.parametrize("side,shown,hidden", [("bear", "put wall", "call wall"), ("bull", "call wall", "put wall")])
def test_side_words_the_registry_sends(base, side, shown, hidden):
    write_chain(base, chain_payload(selectedExpiryChainRows=wall_rows(), expiryExpectedMoves={"2026-11-13": 10.0}))
    said = text(run("walls", base, {"ticker": "AAA", "side": side}))
    assert shown in said and hidden not in said


def test_walls_ignore_an_expired_giant(base):
    rows = wall_rows() + [contract("CALL", 120, "2026-11-09", oi=999999)]
    write_chain(base, chain_payload(selectedExpiryChainRows=rows, expiryExpectedMoves={"2026-11-13": 10.0}))
    assert run("walls", base, {"ticker": "AAA"})["facts"]["calls"][0]["strike"] == 110


def test_walls_match_a_direct_board_build(base):
    from oi_auto_alerts import build_high_oi_walls
    rows = wall_rows()
    write_chain(base, chain_payload(selectedExpiryChainRows=rows, expiryExpectedMoves={"2026-11-13": 10.0}))
    fresh = [{**row, "days_to_expiration": 3} for row in rows]
    board = build_high_oi_walls(fresh, 100.0, as_of=NOW, expected_move=10.0)
    reply = run("walls", base, {"ticker": "AAA"})
    assert reply["facts"]["calls"] == board["calls"] and reply["facts"]["puts"] == board["puts"]


def test_no_open_interest_says_so(base):
    write_chain(base, chain_payload(selectedExpiryChainRows=[contract("CALL", 105, "2026-11-13", oi=0)]))
    assert "no open interest" in text(run("walls", base, {"ticker": "AAA"}))
    assert "no open interest" in text(run("max_pain", base, {"ticker": "AAA"}))


# ---------------------------------------------------------------- max pain

def test_max_pain_arithmetic():
    rows = [{"side": "CALL", "strike": 100, "open_interest": 10}, {"side": "PUT", "strike": 100, "open_interest": 10},
            {"side": "CALL", "strike": 110, "open_interest": 100}, {"side": "PUT", "strike": 90, "open_interest": 100}]
    assert market.max_pain_strike(rows) == 100
    # All the OI on one call strike: holders collect nothing at or below it.
    assert market.max_pain_strike([{"side": "CALL", "strike": 100, "open_interest": 5},
                                   {"side": "PUT", "strike": 80, "open_interest": 1}]) == 80


def test_max_pain_is_labelled(base):
    write_chain(base, chain_payload(expiryExpectedMoves={"2026-11-13": 3.0}))
    reply = run("max_pain", base, {"ticker": "AAA"})
    assert "worked out from saved OI, 80 strikes only" in text(reply)
    assert reply["facts"]["label"] == "worked out from saved OI, 80 strikes only" and reply["asOf"]


# ---------------------------------------------------------------- quote

def test_quote_prefers_the_stream_and_shows_its_age(base):
    write_chain(base, chain_payload())
    held = {"AAA": {"last": 101.5, "lastAt": "2026-11-10T15:59:30+00:00"}}
    chat_state.bind(state=SimpleNamespace(), stream=SimpleNamespace(latest_equities=lambda symbols: held))
    reply = run("quote", base, {"ticker": "AAA"})
    assert reply["facts"] == {"ticker": "AAA", "price": 101.5, "source": "stream", "ageSeconds": 30}
    assert "30 seconds old" in text(reply) and reply["links"][0]["view"] == "Charts & OI"


def test_quote_falls_back_to_the_chain_with_age(base):
    write_chain(base, chain_payload())
    reply = run("quote", base, {"ticker": "AAA"})
    assert reply["facts"]["source"] == "option chain" and reply["facts"]["ageSeconds"] == 900
    assert "15 minutes old" in text(reply) and "not a live price" in text(reply)


def test_the_chains_price_is_never_dated_by_its_save_time(base):
    """Finding 2026-11-11: underlyingPrice is Schwab's last print, overnight the previous evening's; 'saved 03:42 ET,
    4 minutes old, +0.36% on the day' was Tuesday's 19:59 bar and Tuesday's change."""
    write_chain(base, {**chain_payload(), "todayChangePercent": 0.36})
    overnight = datetime(2026, 11, 11, 3, 50, tzinfo=ET)
    reply = run("quote", base, {"ticker": "AAA"}, now=overnight)
    assert market.SPOT_WORDS in text(reply) and market.SPOT_NOTE in text(reply)
    assert "+0.36% change is the previous session's" in text(reply) and reply["facts"]["previousSession"] is True
    assert "on the day when saved" not in text(reply)


def test_a_held_chart_candle_beats_the_chains_price(base, monkeypatch):
    from momx import chat_chart
    write_chain(base, chain_payload())
    candle = datetime(2026, 11, 10, 10, 59, tzinfo=ET)
    monkeypatch.setattr(chat_chart, "latest_bar", lambda symbol: (int(candle.timestamp()), 104.25))
    reply = run("quote", base, {"ticker": "AAA"})
    assert reply["facts"]["source"] == "chart candle" and reply["facts"]["price"] == 104.25 and "10:59 ET" in text(reply)


def test_quote_with_nothing_held(base):
    assert "no saved price" in text(run("quote", base, {"ticker": "NOPE"}))


# ---------------------------------------------------------------- sectors

def write_hot(base, day, boards):
    folder = base / "momx_sector_hot"
    folder.mkdir(exist_ok=True)
    (folder / f"{day}.json").write_text(json.dumps({"date": day, "boards": boards}), encoding="utf-8")


HOT = {"Watchlist": {"hot": ["Telecom"], "leaders": {
    "VZ": {"rank": 2, "sector": "Telecom", "at": "2026-11-10T09:36:00-05:00", "pct": 0.09},
    "TMUS": {"rank": 1, "sector": "Telecom", "at": "2026-11-10T09:35:00-05:00", "pct": 0.2}}}}


def test_sectors_reads_the_hot_file(base):
    write_hot(base, "2026-11-10", HOT)
    reply = run("sectors", base)
    assert "Hot sectors today" in text(reply) and "Telecom" in text(reply)
    row = reply["table"]["rows"][0]
    assert row["at"] == "09:35 ET" and row["leaders"].startswith("TMUS +0.20%")
    assert reply["links"] == [{"label": "Open Sectors", "view": "Sectors"}]


def test_sectors_uses_the_last_session_and_says_which(base):
    write_hot(base, "2026-11-09", HOT)
    assert "Mon 2026-11-09" in text(run("sectors", base))


def test_sectors_too_old_or_absent(base):
    assert "No sector" in text(run("sectors", base))
    write_hot(base, "2026-10-20", HOT)
    assert run("sectors", base)["facts"].get("missing")


def test_sectors_with_none_hot(base):
    write_hot(base, "2026-11-10", {"Watchlist": {"hot": [], "leaders": {}}})
    assert "No sector was marked hot today" in text(run("sectors", base))


def test_ticker_sector_and_named_sector(base, monkeypatch):
    from momx import industry_lookup
    monkeypatch.setattr(industry_lookup, "cached_profiles", lambda symbols: {"NVDA": {"industry": "Semis"}})
    reply = run("sectors", base, {"ticker": "NVDA"})
    assert "Technology card, in Semiconductors" in text(reply) and "Semis" in text(reply)
    reply = run("sectors", base, {"sector": "energy"})
    assert reply["facts"] == {"sector": "Energy", "etf": "XLE"}


def test_sector_cards_json_matches_the_source_text():
    source = (ROOT / "frontend" / "src" / "sectorHeatmap.js").read_text(encoding="utf-8")
    assert (ROOT / "momx" / "sector_cards.json").read_text(encoding="utf-8") == cards_builder.render(source)


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_sector_cards_json_matches_what_the_page_runs():
    """Parity with the JS module itself, so the build script's reading of it is checked too."""
    script = ("import('./frontend/src/sectorHeatmap.js').then(m=>console.log(JSON.stringify(m.SECTOR_CARDS)))")
    done = subprocess.run(["node", "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert done.returncode == 0, done.stderr
    page = json.loads(done.stdout)
    held = json.loads((ROOT / "momx" / "sector_cards.json").read_text(encoding="utf-8"))["cards"]
    assert [card["name"] for card in held] == [card["name"] for card in page]
    for mine, theirs in zip(held, page):
        assert (mine["etf"], mine["about"]) == (theirs["etf"], theirs["about"])
        assert mine["groups"] == theirs["groups"]
        for key in ("leaders", "more"):
            assert [(m["symbol"], m["capB"] * 1e9) for m in mine[key]] == [(t["symbol"], t["cap"]) for t in theirs[key]]


# ---------------------------------------------------------------- premarket

def write_premarket(base, day, rows=None, briefing=None):
    folder = base / "premarket_scanner_history"
    folder.mkdir(exist_ok=True)
    document = {"date": day, "rows": rows or {}}
    if briefing:
        document["briefing"] = briefing
    (folder / f"{day}.json").write_text(json.dumps(document), encoding="utf-8")


def premarket_row(symbol="NVDA", first="2026-11-10T05:31:09-05:00", **row):
    return {symbol: {"firstSeenAt": first, "lastSeenAt": "2026-11-10T05:55:30-05:00",
                     "row": {"symbol": symbol, "lastPrice": 181.51, "changePct": 1.69, "signals48": ["CALL4H"],
                             "strength": "WEAK", "forming": True, "catalyst": {"tag": "LEGAL", "headline": "A headline"}, **row}}}


def test_premarket_today(base):
    write_premarket(base, "2026-11-10", premarket_row())
    reply = run("premarket", base, {"day": "today"})
    assert reply["links"][0] == {"label": "Open Premarket Scanner, 2026-11-10", "view": "Premarket Scanner", "day": "2026-11-10"}
    row = reply["table"]["rows"][0]
    assert row["symbol"] == "NVDA" and row["first"] == "05:31" and row["signals"] == "CALL4H (forming)"


def test_premarket_falls_back_to_the_last_session_and_says_so(base):
    write_premarket(base, "2026-11-09", premarket_row())
    reply = run("premarket", base)
    assert "last session, Mon 2026-11-09" in text(reply) and reply["facts"]["day"] == "2026-11-09"


def test_premarket_named_day_with_no_record_is_not_substituted(base):
    write_premarket(base, "2026-11-09", premarket_row())
    reply = run("premarket", base, {"day": "2026-11-03"})
    assert reply["facts"].get("missing") and reply["table"] is None


def test_premarket_one_ticker_found_and_not_found(base):
    write_premarket(base, "2026-11-09", premarket_row())
    reply = run("premarket", base, {"day": "monday", "ticker": "NVDA"})
    assert reply["facts"]["found"] is True and "first seen 05:31 ET" in text(reply) and "forming" in text(reply)
    reply = run("premarket", base, {"day": "monday", "ticker": "AMD"})
    assert reply["facts"]["found"] is False and "was not on the premarket scanner on Mon 2026-11-09" in text(reply)


def test_premarket_yesterday_is_the_last_session_on_file(base):
    write_premarket(base, "2026-11-06", premarket_row())
    assert run("premarket", base, {"day": "yesterday"})["facts"]["day"] == "2026-11-06"


def test_premarket_conversation_day_is_used(base):
    write_premarket(base, "2026-11-09", premarket_row())
    assert run("premarket", base, day="2026-11-09")["facts"]["day"] == "2026-11-09"


def test_premarket_nothing_saved_at_all(base):
    assert "no premarket scanner record" in text(run("premarket", base))


# ---------------------------------------------------------------- morning brief

BRIEF = {"lines": ["SPY -0.6%.", "Watchlist gainers: **TTWO** +9.2%."], "generatedAt": "2026-11-10T09:30:33-05:00"}


def test_morning_brief_today_answers_in_chat_with_no_link(base):
    write_premarket(base, "2026-11-10", briefing=BRIEF)
    reply = run("morning_brief", base)
    assert reply["lines"] == ["SPY -0.6%.", "Watchlist gainers: TTWO +9.2%."]
    assert reply["links"] == [] and reply["asOf"] == "as of 09:30 ET"


def test_morning_brief_before_it_is_built_says_which_day(base):
    write_premarket(base, "2026-11-09", briefing=BRIEF)
    reply = run("morning_brief", base)
    assert "not saved yet" in reply["lines"][0] and "Mon 2026-11-09" in reply["lines"][0]


def test_morning_brief_none_saved(base):
    assert run("morning_brief", base)["facts"].get("missing")


# ---------------------------------------------------------------- news

def bind_news(payload):
    chat_state.bind(state=SimpleNamespace(_momx_news=lambda: SimpleNamespace(latest=lambda symbols: payload)))


def test_news_lists_facts_with_no_cause_claim(base):
    bind_news({"lookbackDays": 7, "feed": [
        {"symbol": "NVDA", "headline": "Older", "source": "A", "publishedAt": "2026-11-09T12:00:00+00:00"},
        {"symbol": "NVDA", "headline": "Newest", "source": "B", "publishedAt": "2026-11-10T14:00:00+00:00"},
        {"symbol": "AMD", "headline": "Other ticker", "source": "C", "publishedAt": "2026-11-10T15:00:00+00:00"}]})
    reply = run("news", base, {"ticker": "NVDA"})
    assert reply["lines"][1] == "09:00 ET, B: Newest" and reply["facts"]["count"] == 2
    assert "Other ticker" not in text(reply) and "does not know what caused" in text(reply)
    assert reply["links"][0] == {"label": "Open NVDA news", "view": "News Feed", "ticker": "NVDA"}
    assert not re.search(r"\b(because|due to|driven by)\b", " ".join(l for l in reply["lines"] if "headlines only" in l))


def test_news_none_and_store_states(base):
    bind_news({"feed": [], "lookbackDays": 7})
    assert "No headlines saved for NVDA in the last 7 days" in text(run("news", base, {"ticker": "NVDA"}))
    bind_news({"feed": [], "storeError": "locked"})
    assert "could not be read" in text(run("news", base, {"ticker": "NVDA"}))
    chat_state.bind(state=SimpleNamespace())
    assert "not available right now" in text(run("news", base, {"ticker": "NVDA"}))


# ---------------------------------------------------------------- earnings

def bind_earnings(rows=(), watchlist=("NVDA", "AAPL")):
    payload = {"rows": list(rows), "refreshedAt": "2026-11-09T08:00:00-05:00", "endDate": "2026-12-10"}
    chat_state.bind(state=SimpleNamespace(earnings_calendar_cache=(NOW, tuple(watchlist), payload)))


def test_earnings_empty_cache_says_so(base):
    reply = run("earnings", base, {"ticker": "NVDA"})
    assert "has not loaded since the app last restarted" in text(reply)
    assert reply["links"] == [{"label": "Open Earnings Calendar", "view": "Earnings Calendar"}]
    assert "no earnings" not in text(reply).lower()


def test_earnings_off_the_watchlist_is_not_tracked(base):
    bind_earnings()
    reply = run("earnings", base, {"ticker": "ZETA"})
    assert "not tracked" in text(reply) and reply["facts"]["tracked"] is False
    assert "no earnings" not in text(reply).lower()


def test_earnings_found_at_any_cache_age(base):
    bind_earnings([{"symbol": "NVDA", "date": "2026-11-18", "timing": "After close", "source": "x"}])
    reply = run("earnings", base, {"ticker": "NVDA"})
    assert "reports Wed 2026-11-18 (8 days away), After close" in text(reply)
    assert reply["asOf"] == "as of Nov 09 08:00 ET"


def test_earnings_tracked_with_a_passed_date_is_not_quoted(base):
    bind_earnings([{"symbol": "NVDA", "date": "2026-11-09", "timing": "After close"}])
    reply = run("earnings", base, {"ticker": "NVDA"})
    assert reply["facts"]["date"] is None and "through 2026-12-10" in text(reply)


def test_earnings_list(base):
    bind_earnings([{"symbol": "NVDA", "date": "2026-11-18", "timing": "AMC"}])
    assert run("earnings", base)["table"]["rows"][0]["symbol"] == "NVDA"


# ---------------------------------------------------------------- Long Term

class FakeLongTerm:
    def __init__(self, payload):
        self.payload, self.calls = payload, []

    def detail_payload(self, symbol, mos, quote):
        self.calls.append((symbol, mos, quote))
        return self.payload


def bind_long_term(monkeypatch, payload):
    from long_term import service
    fake = FakeLongTerm(payload)
    monkeypatch.setattr(service, "service_instance", lambda: fake)
    return fake


def long_term_row(**over):
    return {"row": {"name": "Apple Inc.", "signal": "BUY", "reason": "Below buy price", "buyBelow": 123.45, "leapsBelow": 111.0,
                    "sticker": 238.88, "buffett": 207.41, "fairValue": 207.41, "roic": 0.688, "big5": 4, **over},
            "fetchedAt": "2026-11-08T09:32:00-05:00"}


def test_long_term_gives_facts_and_never_the_buy_signal(base, monkeypatch):
    fake = bind_long_term(monkeypatch, long_term_row())
    reply = run("long_term", base, {"ticker": "AAPL"})
    dump = json.dumps(reply)
    assert fake.calls == [("AAPL", 50, None)], "no quote, so no Schwab read"
    assert "Sticker price: 238.88." in text(reply) and "ROIC: 68.8%." in text(reply)
    assert "not a buy or sell call" in text(reply)
    assert "123.45" not in dump and "111.0" not in dump and "BUY" not in dump and "Below buy" not in dump
    assert reply["links"][0] == {"label": "Open AAPL on Long Term", "view": "Long Term", "ticker": "AAPL"}


@pytest.mark.parametrize("signal,phrase", [("ETF", "is an ETF"), ("NO_FILINGS", "no usable filings"), ("CHECK_DATA", "no usable filings")])
def test_long_term_special_states(base, monkeypatch, signal, phrase):
    bind_long_term(monkeypatch, long_term_row(signal=signal))
    assert phrase in text(run("long_term", base, {"ticker": "AAPL"}))


def test_long_term_stale_and_unknown(base, monkeypatch):
    bind_long_term(monkeypatch, long_term_row(signal="STALE", reason="Stale: last refresh 9 days ago"))
    assert "last refresh 9 days ago" in text(run("long_term", base, {"ticker": "AAPL"}))
    bind_long_term(monkeypatch, None)
    assert "not on the Long Term screen" in text(run("long_term", base, {"ticker": "NOPE"}))


# ---------------------------------------------------------------- wording

def test_regular_users_never_see_owner_words(base, monkeypatch):
    write_chain(base, chain_payload())
    write_hot(base, "2026-11-10", HOT)
    write_premarket(base, "2026-11-10", premarket_row(), briefing=BRIEF)
    bind_long_term(monkeypatch, long_term_row())
    for name, tool in market.TOOLS.items():
        dump = json.dumps(tool["handler"]({"ticker": "AAA"}, ctx(base)))
        assert not re.search(r"\b(bot|paper|scorecard|trades?)\b", dump, re.I), name
        assert "Ganesh" not in dump, name


def test_page_ticker_and_conversation_order(base):
    assert market._ticker({}, {"pageTicker": "AAA", "ticker": "ZETA"}) == "ZETA"
    assert market._ticker({}, {"pageTicker": "AAA", "ticker": "ZETA"}, page_first=True) == "AAA"
    assert market._ticker({"ticker": "nvda"}, {"pageTicker": "AAA", "ticker": "ZETA"}) == "NVDA"
    assert market._ticker({}, {"pageTicker": None, "ticker": None}) is None
