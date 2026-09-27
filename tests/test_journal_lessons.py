"""Journal lessons: the statistics are ours, the interpretation is the model's.

Every test here runs against a fake provider. No SDK, no key and no network is
ever touched - the point of the provider contract is that this file can pin the
whole feature offline.

The tests that matter most are the noise guards. A model handed twelve trades
will happily tell a trader he loses on Tuesdays; these tests prove the code
throws that away rather than printing it.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from agents import journal_lessons


# --------------------------------------------------------------------------
# fakes and fixtures
# --------------------------------------------------------------------------
class FakeProvider:
    """Records what it was asked, answers with whatever the test wants."""

    def __init__(self, payload=None, ok: bool = True, error: str = "", raises: bool = False):
        self.name = "fake"
        self.model = "fake-model-1"
        self.payload = payload
        self.ok = ok
        self.error = error
        self.raises = raises
        self.calls: list[dict] = []

    def available(self) -> bool:
        return True

    def complete(self, *, system: str, user: str, max_tokens: int = 2000, schema=None) -> dict:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens, "schema": schema})
        if self.raises:
            raise RuntimeError("socket exploded")
        return {
            "ok": self.ok,
            "text": json.dumps(self.payload) if self.payload is not None else "",
            "data": self.payload,
            "error": self.error,
            "provider": self.name,
            "model": self.model,
        }


def make_trade(
    *,
    pnl: float,
    hour_et: int = 10,
    day: str = "2026-07-06",
    setup: str = "EMA + VWAP + ORB",
    side: str = "buy",
    symbol: str = "AAPL",
    order_id: str = "order-1",
    hold_minutes: int = 20,
) -> dict:
    """One equity journal row, in the shape database/repository.py stores.

    July is EDT, so ET + 4 = UTC.
    """
    opened = f"{day}T{hour_et + 4:02d}:00:00+00:00"
    closed_hour = hour_et + 4
    closed = f"{day}T{closed_hour:02d}:{hold_minutes:02d}:00+00:00"
    return {
        "id": order_id,
        "client_order_id": order_id,
        "symbol": symbol,
        "side": side,
        "quantity": 10.0,
        "entry_price": 100.0,
        "status": "closed_or_filled",
        "opened_at": opened,
        "closed_at": closed,
        "pnl": pnl,
        "setup_name": setup,
        "strategy_family": "Momentum + Price Action Trend",
        "notes": "afterhours_session_end",
        "account_mode": "paper",
    }


def hand_checked_book() -> list[dict]:
    """Twelve closed trades with arithmetic a human can verify in their head.

    Wins   +100 +200 +300 +400 +500 +600  -> 6 wins,  gross +2100, avg +350
    Losses -100 -200 -300 -400 -500       -> 5 losses, gross -1500, avg -300
    Scratch 0                             -> 1 scratch
    Total P&L +600 over 12 trades; win rate 6/11 of the decided trades.

    Nine trades are Monday 10:00 ET, three are Tuesday 14:00 ET - so
    "Tuesday" and "14:00 ET" are three-trade groups, below the five-trade
    floor, which is exactly the noise a model wants to tell stories about.
    """
    pnls = [100, 200, 300, 400, 500, 600, -100, -200, -300, -400, -500, 0]
    book = []
    for index, pnl in enumerate(pnls):
        thin_group = index >= 9  # the last three
        book.append(
            make_trade(
                pnl=float(pnl),
                hour_et=14 if thin_group else 10,
                day="2026-07-07" if thin_group else "2026-07-06",
                setup="Gap Fade" if thin_group else "EMA + VWAP + ORB",
                symbol="MSFT" if thin_group else "AAPL",
                order_id=f"order-{index}",
            )
        )
    return book


def find_group(result: dict, dimension: str, label: str) -> dict:
    rows = result["stats"]["groups"][dimension]
    matches = [row for row in rows if row["group"] == label]
    assert matches, f"no {dimension} group named {label!r} in {[row['group'] for row in rows]}"
    return matches[0]


# --------------------------------------------------------------------------
# below the minimum: no model call at all
# --------------------------------------------------------------------------
def test_below_min_trades_never_calls_the_model():
    provider = FakeProvider(payload={"lessons": [{"finding": "x", "evidence": "y", "suggestion": "z"}]})

    result = journal_lessons.lessons(hand_checked_book()[:9], provider=provider, min_trades=10)

    assert provider.calls == []
    assert result["available"] is True
    assert result["lessons"] == []
    assert result["sampleSize"] == 9
    assert "9 closed trades" in result["reason"]
    assert "at least 10" in result["reason"]


def test_below_min_trades_still_returns_the_statistics():
    result = journal_lessons.lessons(hand_checked_book()[:9], provider=FakeProvider(), min_trades=10)

    assert result["stats"]["overall"]["trades"] == 9


def test_no_trades_at_all_is_available_with_a_plain_reason():
    provider = FakeProvider()

    result = journal_lessons.lessons([], provider=provider, min_trades=10)

    assert provider.calls == []
    assert result["available"] is True
    assert result["sampleSize"] == 0
    assert "No closed trades" in result["reason"]


def test_open_positions_are_not_closed_trades():
    book = hand_checked_book()
    book.append({
        "id": "still-open",
        "symbol": "NVDA",
        "side": "buy",
        "status": "position_open",
        "opened_at": "2026-07-06T14:00:00+00:00",
        "closed_at": None,
        "pnl": 9999.0,
    })

    result = journal_lessons.lessons(book, provider=FakeProvider(payload={"lessons": []}), min_trades=10)

    assert result["sampleSize"] == 12
    assert result["stats"]["overall"]["totalPnl"] == 600.0


# --------------------------------------------------------------------------
# the arithmetic (hand-checked)
# --------------------------------------------------------------------------
def test_overall_statistics_are_hand_checked():
    result = journal_lessons.lessons(
        hand_checked_book(), provider=FakeProvider(payload={"lessons": []}), min_trades=10
    )
    overall = result["stats"]["overall"]

    assert overall["trades"] == 12
    assert overall["wins"] == 6
    assert overall["losses"] == 5
    assert overall["scratches"] == 1
    assert overall["winRatePct"] == 54.5  # 6 of the 11 decided trades
    assert overall["avgWin"] == 350.0  # 2100 / 6
    assert overall["avgLoss"] == -300.0  # -1500 / 5
    assert overall["totalPnl"] == 600.0
    assert overall["avgPnl"] == 50.0  # 600 / 12
    assert overall["profitFactor"] == 1.4  # 2100 / 1500
    assert overall["expectancy"] == 50.0


def test_groupings_split_the_book_the_way_a_trader_would():
    result = journal_lessons.lessons(
        hand_checked_book(), provider=FakeProvider(payload={"lessons": []}), min_trades=10
    )

    morning = find_group(result, "hourOfDay", "10:00 ET")
    assert morning["trades"] == 9
    assert morning["reliable"] is True
    assert morning["totalPnl"] == 1500.0  # +2100 winners, -600 from the first three losers

    afternoon = find_group(result, "hourOfDay", "14:00 ET")
    assert afternoon["trades"] == 3
    assert afternoon["reliable"] is False  # three trades is noise
    assert afternoon["totalPnl"] == -900.0

    monday = find_group(result, "dayOfWeek", "Monday")
    assert monday["trades"] == 9
    tuesday = find_group(result, "dayOfWeek", "Tuesday")
    assert tuesday["trades"] == 3

    orb = find_group(result, "setup", "EMA + VWAP + ORB")
    assert orb["trades"] == 9
    assert orb["wins"] == 6
    assert orb["losses"] == 3
    assert orb["avgLoss"] == -200.0  # -100, -200, -300

    assert find_group(result, "side", "buy")["trades"] == 12
    assert find_group(result, "symbol", "AAPL")["trades"] == 9
    assert find_group(result, "holdTime", "5-30 min")["trades"] == 12


def test_reliable_flag_marks_every_thin_group():
    aggregates = journal_lessons.compute_aggregates(
        journal_lessons.normalize_trades(hand_checked_book())
    )
    for dimension, rows in aggregates["groups"].items():
        for row in rows:
            assert row["reliable"] == (row["trades"] >= journal_lessons.MIN_GROUP_TRADES), (
                f"{dimension}/{row['group']}"
            )


# --------------------------------------------------------------------------
# the prompt carries aggregates, never rows
# --------------------------------------------------------------------------
def test_raw_trade_rows_are_not_in_the_prompt():
    book = hand_checked_book()
    book[0]["id"] = "TRADE-ID-ZZQ-4242"
    book[0]["client_order_id"] = "momentum-ZZQ-deadbeefcafe"
    book[0]["entry_price"] = 987.6543
    book[0]["notes"] = (
        "Planned from option bot scan. Approval=automatic. Trigger=EMA + VWAP + ORB. "
        "Selected 3 ZZQ260724C00023000 at mid 0.38."
    )
    provider = FakeProvider(payload={"lessons": []})

    journal_lessons.lessons(book, provider=provider, min_trades=10)

    assert len(provider.calls) == 1
    prompt = provider.calls[0]["system"] + provider.calls[0]["user"]
    assert "ZZQ" not in prompt
    assert "TRADE-ID" not in prompt
    assert "deadbeefcafe" not in prompt
    assert "987.6543" not in prompt
    assert "Approval=automatic" not in prompt
    # what IS there: the computed aggregates
    assert "winRatePct" in prompt
    assert "EMA + VWAP + ORB" in prompt


def test_the_model_is_asked_for_json_and_given_a_schema():
    provider = FakeProvider(payload={"lessons": []})

    journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    schema = provider.calls[0]["schema"]
    assert schema["properties"]["lessons"]["items"]["required"] == [
        "finding",
        "evidence",
        "suggestion",
        "sampleSize",
    ]


def test_system_prompt_states_the_sample_size_rules():
    prompt = journal_lessons.SYSTEM_PROMPT
    assert "MUST state the number of trades" in prompt
    assert f"NEVER draw a lesson from fewer than {journal_lessons.MIN_GROUP_TRADES} trades" in prompt
    assert "empty lessons list" in prompt


# --------------------------------------------------------------------------
# the noise guard
# --------------------------------------------------------------------------
def test_lesson_drawn_from_fewer_than_five_trades_is_dropped():
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": "You lose money on Tuesdays.",
            "evidence": "Tuesday: 3 trades, 0 wins, -$900.",
            "suggestion": "Stop trading on Tuesdays.",
            "sampleSize": 3,
        },
        {
            "finding": "Your 10:00 ET entries carry the account.",
            "evidence": "10:00 ET: 9 trades, 6 wins, +$1,500.",
            "suggestion": "Keep the size on the morning window.",
            "sampleSize": 9,
        },
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["available"] is True
    findings = [lesson["finding"] for lesson in result["lessons"]]
    assert findings == ["Your 10:00 ET entries carry the account."]
    assert len(result["dropped"]) == 1
    assert "3 trades" in result["dropped"][0]["droppedBecause"]
    assert "dropped" in result["reason"]


def test_lesson_citing_a_thin_group_is_dropped_even_when_it_claims_a_big_sample():
    """The model quotes the whole book's size while the claim is about three trades."""
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": "Tuesday is your worst day.",
            "evidence": "Across 12 trades, Tuesday is the only losing day.",
            "suggestion": "Skip Tuesdays.",
            "sampleSize": 12,
        },
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["lessons"] == []
    assert "cites a group with only 3 trades" in result["dropped"][0]["droppedBecause"]


def test_lesson_with_no_stated_sample_size_is_dropped():
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": "You are an impatient trader.",
            "evidence": "Your losers are cut fast.",
            "suggestion": "Give the trade room.",
        },
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["lessons"] == []
    assert result["dropped"][0]["droppedBecause"] == "no sample size stated"
    assert "clears the evidence bar" in result["reason"]


def test_sample_size_read_from_the_sentence_when_the_field_is_missing():
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": "Your morning setup is the edge.",
            "evidence": "The 10:00 ET window is 9 trades at a 66% win rate.",
            "suggestion": "Trade the open, not the afternoon.",
        },
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert len(result["lessons"]) == 1


def test_each_lesson_is_trimmed_to_one_sentence():
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": "Your morning trades win. Your afternoon trades do not. Also you overtrade.",
            "evidence": "10:00 ET: 9 trades, 6 wins.",
            "suggestion": "Trade the open. Then walk away. Really.",
            "sampleSize": 9,
        },
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)
    lesson = result["lessons"][0]

    assert lesson["finding"] == "Your morning trades win."
    assert lesson["suggestion"] == "Trade the open."
    assert set(lesson) == {"finding", "evidence", "suggestion"}


def test_no_more_than_max_lessons_are_returned():
    provider = FakeProvider(payload={"lessons": [
        {
            "finding": f"Observation number {index}.",
            "evidence": "Based on 9 trades.",
            "suggestion": "Keep doing it.",
            "sampleSize": 9,
        }
        for index in range(12)
    ]})

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert len(result["lessons"]) == journal_lessons.MAX_LESSONS


# --------------------------------------------------------------------------
# no key, dead provider, junk answers
# --------------------------------------------------------------------------
def test_no_provider_configured_is_available_false_with_a_readable_reason(monkeypatch):
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: None)

    result = journal_lessons.lessons(hand_checked_book(), min_trades=10)

    assert result["available"] is False
    assert result["lessons"] == []
    assert result["reason"].strip()
    assert result["sampleSize"] == 12
    assert result["stats"]["overall"]["trades"] == 12  # numbers still render with no key


def test_missing_ai_provider_module_reads_as_no_key_set(monkeypatch):
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: None)
    monkeypatch.setattr(
        journal_lessons,
        "_registry_status_message",
        lambda: "No AI key set. Add OPENAI_API_KEY to .env.",
    )

    result = journal_lessons.lessons(hand_checked_book(), min_trades=10)

    assert result["reason"] == "No AI key set. Add OPENAI_API_KEY to .env."


def test_provider_error_is_available_false_and_does_not_raise():
    provider = FakeProvider(ok=False, error="rate limited")

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["available"] is False
    assert "rate limited" in result["reason"]
    assert result["lessons"] == []


def test_provider_that_raises_is_caught():
    provider = FakeProvider(raises=True)

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["available"] is False
    assert "socket exploded" in result["reason"]


def test_unparseable_answer_is_available_false():
    provider = FakeProvider(payload=None)

    result = journal_lessons.lessons(hand_checked_book(), provider=provider, min_trades=10)

    assert result["available"] is False
    assert result["lessons"] == []


def test_json_only_in_text_is_still_read():
    class TextOnlyProvider(FakeProvider):
        def complete(self, *, system, user, max_tokens=2000, schema=None):
            self.calls.append({"system": system, "user": user})
            return {
                "ok": True,
                "text": json.dumps({"lessons": [{
                    "finding": "Mornings pay.",
                    "evidence": "9 trades in the 10:00 ET window.",
                    "suggestion": "Focus there.",
                    "sampleSize": 9,
                }]}),
                "data": None,
                "error": "",
                "provider": "fake",
                "model": "fake-model-1",
            }

    result = journal_lessons.lessons(hand_checked_book(), provider=TextOnlyProvider(), min_trades=10)

    assert result["available"] is True
    assert len(result["lessons"]) == 1


def test_every_return_path_has_the_contract_shape(monkeypatch):
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: None)
    book = hand_checked_book()
    results = [
        journal_lessons.lessons([], provider=FakeProvider(), min_trades=10),
        journal_lessons.lessons(book[:3], provider=FakeProvider(), min_trades=10),
        journal_lessons.lessons(book, min_trades=10),
        journal_lessons.lessons(book, provider=FakeProvider(ok=False, error="boom"), min_trades=10),
        journal_lessons.lessons(book, provider=FakeProvider(payload={"lessons": []}), min_trades=10),
    ]
    for result in results:
        assert isinstance(result["available"], bool)
        assert isinstance(result["reason"], str) and result["reason"].strip()
        assert isinstance(result["generatedAt"], str) and result["generatedAt"]
        assert result["provider"] is None or isinstance(result["provider"], str)
        assert isinstance(result["lessons"], list)
        assert isinstance(result["sampleSize"], int)


def test_provider_name_is_reported_on_success():
    result = journal_lessons.lessons(
        hand_checked_book(), provider=FakeProvider(payload={"lessons": []}), min_trades=10
    )

    assert result["provider"] == "fake"


# --------------------------------------------------------------------------
# the loader against a real journal schema
# --------------------------------------------------------------------------
@pytest.fixture()
def journal_db(tmp_path):
    path = tmp_path / "trades.db"
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_order_id TEXT,
            symbol TEXT,
            side TEXT,
            quantity REAL,
            entry_price REAL,
            status TEXT,
            opened_at TEXT,
            closed_at TEXT,
            pnl REAL,
            setup_name TEXT,
            strategy_family TEXT,
            trigger_source TEXT,
            notes TEXT,
            account_mode TEXT
        )
        """
    )
    connection.execute(
        """
        CREATE TABLE option_trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_order_id TEXT,
            underlying_symbol TEXT,
            option_symbol TEXT,
            structure TEXT,
            side TEXT,
            quantity REAL,
            entry_price REAL,
            exit_price REAL,
            status TEXT,
            opened_at TEXT,
            closed_at TEXT,
            pnl REAL,
            trigger_source TEXT,
            notes TEXT,
            account_mode TEXT
        )
        """
    )
    connection.execute(
        "INSERT INTO trades (client_order_id, symbol, side, status, opened_at, closed_at, pnl, "
        "setup_name, notes, account_mode) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (
            "momentum-USO-1",
            "USO",
            "buy",
            "closed_or_filled",
            "2026-07-17T17:57:07.186479+00:00",
            "2026-07-17T18:57:14.116453+00:00",
            -0.6,
            "EMA + VWAP + ORB",
            "afterhours_session_end",
            "paper",
        ),
    )
    connection.execute(
        "INSERT INTO trades (client_order_id, symbol, side, status, opened_at, closed_at, pnl, "
        "setup_name, account_mode) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            "momentum-NVDA-2",
            "NVDA",
            "buy",
            "position_open",
            "2026-07-17T17:00:00+00:00",
            None,
            0.0,
            "EMA + VWAP + ORB",
            "paper",
        ),
    )
    connection.execute(
        "INSERT INTO option_trades (client_order_id, underlying_symbol, option_symbol, structure, "
        "side, status, opened_at, closed_at, pnl, notes, account_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (
            "option-USO-3",
            "USO",
            "USO260715C00123000",
            "Only Long Call",
            "buy_to_open",
            "closed",
            "2026-07-14T19:03:46.486922+00:00",
            "2026-07-15T13:49:07.960377+00:00",
            -243.0,
            "option_stop_loss_hit",
            "paper",
        ),
    )
    connection.commit()
    connection.close()
    return path


def test_loader_reads_closed_rows_from_both_books(journal_db):
    trades = journal_lessons.load_closed_trades(db_path=str(journal_db))

    assert len(trades) == 2  # the open position is not a closed trade
    by_symbol = {trade["symbol"]: trade for trade in trades}
    assert set(by_symbol) == {"USO"}

    books = sorted(trade["book"] for trade in trades)
    assert books == ["equity", "option"]

    option_trade = next(trade for trade in trades if trade["book"] == "option")
    assert option_trade["pnl"] == -243.0
    assert option_trade["setup"] == "Only Long Call"
    assert option_trade["exitReason"] == "option_stop_loss_hit"
    assert option_trade["side"] == "buy_to_open"
    assert option_trade["hour"] == 15  # 19:03 UTC is 15:03 ET in July
    assert option_trade["weekday"] == "Tuesday"

    equity_trade = next(trade for trade in trades if trade["book"] == "equity")
    assert equity_trade["setup"] == "EMA + VWAP + ORB"
    assert equity_trade["holdMinutes"] == pytest.approx(60.1, abs=0.2)


def test_normalising_an_already_normalised_row_keeps_its_fields(journal_db):
    """The loader normalises, then lessons() normalises again.

    The second pass reads its own output, so unless it recognises the
    normalised keys every option row silently becomes an "unspecified setup"
    equity trade - which is what happened the first time this ran on the real
    journal.
    """
    once = journal_lessons.load_closed_trades(db_path=str(journal_db))
    twice = journal_lessons.normalize_trades(once)

    assert twice == once


def test_lessons_keeps_the_option_book_when_fed_loader_output(journal_db):
    trades = journal_lessons.load_closed_trades(db_path=str(journal_db))
    provider = FakeProvider(payload={"lessons": []})

    result = journal_lessons.lessons(trades, provider=provider, min_trades=1)

    books = {row["group"]: row["trades"] for row in result["stats"]["groups"]["book"]}
    assert books == {"equity": 1, "option": 1}
    setups = {row["group"] for row in result["stats"]["groups"]["setup"]}
    assert setups == {"EMA + VWAP + ORB", "Only Long Call"}
    exits = {row["group"] for row in result["stats"]["groups"]["exitReason"]}
    assert exits == {"afterhours_session_end", "option_stop_loss_hit"}


def test_loader_never_writes_to_the_journal(journal_db):
    before = journal_db.stat().st_mtime_ns
    journal_lessons.load_closed_trades(db_path=str(journal_db))
    assert journal_db.stat().st_mtime_ns == before


def test_loader_on_a_missing_database_returns_nothing_and_does_not_raise(tmp_path):
    assert journal_lessons.load_closed_trades(db_path=str(tmp_path / "nope.db")) == []


def test_lessons_from_journal_uses_the_loader(journal_db):
    provider = FakeProvider(payload={"lessons": []})

    result = journal_lessons.lessons_from_journal(
        provider=provider, db_path=str(journal_db), min_trades=10
    )

    assert provider.calls == []  # only two closed trades exist
    assert result["available"] is True
    assert result["sampleSize"] == 2
