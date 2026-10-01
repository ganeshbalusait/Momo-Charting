"""Tests for agents/scanner_triage.py -- the scanner's AI triage layer.

NO NETWORK. Every test drives a fake provider that records the prompt it was handed
and returns whatever the test tells it to. Nothing here imports an SDK, needs a key,
or would behave differently on a machine that has one.

The load-bearing test is ``test_pick_for_symbol_not_on_the_board_is_dropped``: the
whole reason this module exists as code rather than as one prompt is that a model
asked to rank tickers will sometimes return one that was never on the board.
"""

from __future__ import annotations

import json

import pytest

from agents import scanner_triage


# ---------------------------------------------------------------------------
# Fakes and fixtures
# ---------------------------------------------------------------------------

class FakeProvider:
    """An AIProvider that never touches a network.

    Records the exact ``system`` / ``user`` strings so tests can assert on what
    was (and was not) sent.
    """

    def __init__(self, result=None, *, name="fake", is_available=True, raises=None):
        self.name = name
        self._result = result
        self._available = is_available
        self._raises = raises
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def complete(self, *, system, user, max_tokens=2000, schema=None):
        self.calls.append(
            {"system": system, "user": user, "max_tokens": max_tokens, "schema": schema}
        )
        if self._raises is not None:
            raise self._raises
        return self._result

    # convenience for the assertions below
    @property
    def prompt(self) -> str:
        assert self.calls, "provider was never called"
        return self.calls[-1]["user"]


def ok_result(picks, *, provider="fake", model="fake-1"):
    return {
        "ok": True,
        "text": json.dumps({"picks": picks}),
        "data": {"picks": picks},
        "error": "",
        "provider": provider,
        "model": model,
    }


def cell(value, bg="black", fg="white"):
    """A board cell in ``momx.columns`` shape -- value plus its two colour keys."""
    return {"value": value, "bg": bg, "fg": fg}


def board_row(symbol, *, scan_pass=True, industry="Semiconductors", pct=3.4, reasons=None):
    """One row in ``momx.board`` payload-contract shape, colours and all."""
    return {
        "symbol": symbol,
        "industry": industry,
        "last": 123.45,
        "pctChange": pct,
        "scanPass": scan_pass,
        "scanReasons": list(reasons if reasons is not None else ["macd:4h", "rvol:30m"]),
        "rvol": {"5m": cell(1.2), "30m": cell(2.6), "D": cell(0.4)},
        "sqz": {"2h": cell("*2"), "4h": cell("7"), "D": cell("-"), "Wk": cell(None, None, None)},
        "skittles": {"2h": cell(88), "D": cell(41), "M": cell(None, None, None)},
        "highLow": cell(62),
        "color": {"value": None, "bg": "white", "fg": None},
        "quoteTrend": [1, -1, 1, 1, 0, -1, 1, 1, 1, -1],
        "sparkline": [101.1, 101.4, 100.9, 102.2, 103.8, 104.1, 103.3, 104.9],
        "badge": {"on": True, "reasons": ["earnings"], "tooltip": "Earnings in 2 days"},
    }


def board(rows):
    return {
        "generatedAt": "2026-08-27T13:31:00+00:00",
        "universe": [row["symbol"] for row in rows],
        "universeCount": len(rows),
        "rows": rows,
        "errors": {},
    }


@pytest.fixture
def three_match_board():
    """Three matches (AVGO, NVDA, CRWD) and two rows the scan rejected."""
    return board(
        [
            board_row("AVGO", pct=4.1, reasons=["macd:4h", "sqzfired:D", "rvol:30m"]),
            board_row("NVDA", pct=2.2, reasons=["ema9x20:D"]),
            board_row("CRWD", pct=1.8, reasons=["rvol:15m", "macd:2h"]),
            board_row("MSFT", scan_pass=False, pct=0.27),
            board_row("TSLA", scan_pass=False, pct=-1.1),
        ]
    )


# ---------------------------------------------------------------------------
# The no-key path is a normal result, not an error
# ---------------------------------------------------------------------------

def test_no_provider_is_unavailable_with_a_plain_reason(three_match_board, monkeypatch):
    """No key configured: available False, readable reason, and NO exception."""
    # Make sure a real ai_provider module on this machine cannot influence the test.
    monkeypatch.setattr(
        scanner_triage, "_resolve_provider", lambda p: (None, scanner_triage._NO_PROVIDER_MESSAGE)
    )

    result = scanner_triage.triage(three_match_board)

    assert result["available"] is False
    assert result["picks"] == []
    assert "OPENAI_API_KEY" in result["reason"]
    assert result["reason"] == result["reason"].strip()
    assert "Traceback" not in result["reason"]
    assert result["generatedAt"]
    # the matches were still counted, so the panel can say what was skipped
    assert result["matches"] == 3
    assert result["skipped"] == 3


def test_no_provider_module_at_all_does_not_raise(three_match_board, monkeypatch):
    """agents.ai_provider may not exist yet. That is a reason, not a crash."""
    import builtins

    real_import = builtins.__import__

    def blow_up(name, *args, **kwargs):
        if name == "agents.ai_provider":
            raise ImportError("No module named 'agents.ai_provider'")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", blow_up)

    result = scanner_triage.triage(three_match_board)

    assert result["available"] is False
    assert isinstance(result["reason"], str) and result["reason"]
    assert result["picks"] == []


def test_provider_that_reports_unavailable_is_not_called(three_match_board):
    provider = FakeProvider(ok_result([]), is_available=False)

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["available"] is False
    assert provider.calls == []


# ---------------------------------------------------------------------------
# What goes into the prompt -- the token bill
# ---------------------------------------------------------------------------

def test_only_scanpass_rows_are_sent(three_match_board):
    """A non-matching symbol must never appear anywhere in the prompt."""
    provider = FakeProvider(ok_result([{"symbol": "AVGO", "rank": 1, "why": "x", "risk": "y"}]))

    scanner_triage.triage(three_match_board, provider=provider)

    prompt = provider.prompt
    assert "AVGO" in prompt and "NVDA" in prompt and "CRWD" in prompt
    assert "MSFT" not in prompt
    assert "TSLA" not in prompt


def test_colour_keys_never_appear_in_the_prompt(three_match_board):
    """The model cannot see colour; carrying the colour keys triples the payload."""
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(three_match_board, provider=provider)

    prompt = provider.prompt
    # the bare two-letter keys, not just their quoted JSON form
    assert "bg" not in prompt
    assert "fg" not in prompt
    # and no colour name leaked through with them
    for colour in ("magenta", "cyan", "orange", "white", "black"):
        assert colour not in prompt.lower()


def test_arrays_and_unused_fields_are_not_sent(three_match_board):
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(three_match_board, provider=provider)

    prompt = provider.prompt
    assert "sparkline" not in prompt
    assert "quoteTrend" not in prompt
    assert "highLow" not in prompt
    assert "badge" not in prompt
    assert "104.9" not in prompt  # a sparkline price
    # the values that DO matter survived
    assert "macd:4h" in prompt
    assert "*2" in prompt


def test_compact_row_carries_exactly_the_agreed_fields(three_match_board):
    rows = scanner_triage.compact_rows(three_match_board)

    assert [row["symbol"] for row in rows] == ["AVGO", "NVDA", "CRWD"]
    assert set(rows[0]) == {"symbol", "industry", "pctChange", "scanReasons", "rvol", "sqz", "skittles"}
    # cell blocks are flattened to bare values, and null / "-" cells are dropped
    assert rows[0]["rvol"] == {"5m": 1.2, "30m": 2.6, "D": 0.4}
    assert rows[0]["sqz"] == {"2h": "*2", "4h": "7"}
    assert rows[0]["skittles"] == {"2h": 88, "D": 41}


def test_prompt_explains_what_the_signals_mean(three_match_board):
    """A model with no glossary reads the RVOL Z-score as a ratio and is wrong."""
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(three_match_board, provider=provider)

    prompt = provider.prompt.lower()
    assert "z-score" in prompt
    assert "does not mean twice normal volume" in prompt
    assert "stochastic" in prompt
    assert "extended" in prompt and "washed out" in prompt
    assert "high-compression" in prompt
    assert "bars ago" in prompt


def test_system_prompt_forbids_invention_advice_and_hedging():
    system = scanner_triage.SYSTEM_PROMPT.lower()
    assert "use only the numbers you are given" in system
    assert "no trading advice" in system
    assert "price target" in system
    assert "hedging" in system
    assert "you do not trade" in system


def test_schema_is_passed_so_picks_come_back_structured(three_match_board):
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(three_match_board, provider=provider)

    schema = provider.calls[-1]["schema"]
    assert schema is scanner_triage.PICKS_SCHEMA
    item = schema["properties"]["picks"]["items"]
    assert item["required"] == ["symbol", "rank", "why", "risk"]


# ---------------------------------------------------------------------------
# Failure of the call itself
# ---------------------------------------------------------------------------

def test_provider_returning_not_ok_degrades_and_carries_the_error(three_match_board):
    provider = FakeProvider(
        {
            "ok": False,
            "text": "",
            "data": None,
            "error": "401 invalid_api_key",
            "provider": "openai",
            "model": "gpt-5",
        }
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["available"] is False
    assert "401 invalid_api_key" in result["reason"]
    assert result["picks"] == []
    assert result["provider"] == "openai"
    assert result["skipped"] == 3


def test_provider_that_raises_is_still_not_an_exception(three_match_board):
    """The contract says complete() never raises. Survive one that breaks it."""
    provider = FakeProvider(None, raises=RuntimeError("socket blew up"))

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["available"] is False
    assert "socket blew up" in result["reason"]
    assert result["picks"] == []


def test_unparseable_response_degrades(three_match_board):
    provider = FakeProvider(
        {"ok": True, "text": "Sure! Here are my top picks:", "data": None, "error": "",
         "provider": "fake", "model": "fake-1"}
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["available"] is False
    assert result["picks"] == []


def test_json_in_a_code_fence_is_still_read(three_match_board):
    """``data`` is preferred, but a model that fenced its JSON is not a failure."""
    body = json.dumps({"picks": [{"symbol": "NVDA", "rank": 1, "why": "w", "risk": "r"}]})
    provider = FakeProvider(
        {"ok": True, "text": f"```json\n{body}\n```", "data": None, "error": "",
         "provider": "fake", "model": "fake-1"}
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["available"] is True
    assert [pick["symbol"] for pick in result["picks"]] == ["NVDA"]


# ---------------------------------------------------------------------------
# THE hallucination guard -- the point of the module
# ---------------------------------------------------------------------------

def test_pick_for_symbol_not_on_the_board_is_dropped(three_match_board):
    """A confident, well-argued pick for a name the scanner never flagged is the
    worst thing this module could return. It is discarded, and counted."""
    provider = FakeProvider(
        ok_result(
            [
                {"symbol": "AVGO", "rank": 1, "why": "Four-hour MACD with 2.6 relative volume.",
                 "risk": "Two-hour stochastic at 88 is extended."},
                {"symbol": "AMD", "rank": 2, "why": "Strong semiconductor momentum.",
                 "risk": "Extended."},  # never on the board
                {"symbol": "CRWD", "rank": 3, "why": "Two-hour MACD with fresh volume.",
                 "risk": "Only two conditions fired."},
            ]
        )
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    symbols = [pick["symbol"] for pick in result["picks"]]
    assert symbols == ["AVGO", "CRWD"]
    assert "AMD" not in json.dumps(result)
    assert result["dropped"] == 1
    # ranks are re-numbered contiguously after the drop -- no gap at 2
    assert [pick["rank"] for pick in result["picks"]] == [1, 2]
    assert result["available"] is True


def test_symbol_not_on_the_board_is_dropped_even_when_it_failed_the_scan(three_match_board):
    """MSFT is on the board but did NOT pass. It was never sent, so it is not a
    legal pick either."""
    provider = FakeProvider(
        ok_result([{"symbol": "MSFT", "rank": 1, "why": "w", "risk": "r"}])
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert result["picks"] == []
    assert result["dropped"] == 1
    assert result["skipped"] == 3


def test_lowercase_and_padded_symbols_still_match(three_match_board):
    provider = FakeProvider(ok_result([{"symbol": " nvda ", "rank": 1, "why": "w", "risk": "r"}]))

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert [pick["symbol"] for pick in result["picks"]] == ["NVDA"]
    assert result["dropped"] == 0


def test_duplicate_picks_are_collapsed(three_match_board):
    provider = FakeProvider(
        ok_result(
            [
                {"symbol": "AVGO", "rank": 1, "why": "first", "risk": "r"},
                {"symbol": "AVGO", "rank": 2, "why": "again", "risk": "r"},
            ]
        )
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    assert [pick["symbol"] for pick in result["picks"]] == ["AVGO"]
    assert result["picks"][0]["why"] == "first"


# ---------------------------------------------------------------------------
# limit, skipped, and the shape of a pick
# ---------------------------------------------------------------------------

def test_limit_is_respected():
    rows = [board_row(f"SYM{index}") for index in range(18)]
    provider = FakeProvider(
        ok_result(
            [
                {"symbol": f"SYM{index}", "rank": index + 1, "why": "w", "risk": "r"}
                for index in range(18)
            ]
        )
    )

    result = scanner_triage.triage(board(rows), limit=5, provider=provider)

    assert len(result["picks"]) == 5
    assert [pick["rank"] for pick in result["picks"]] == [1, 2, 3, 4, 5]
    assert result["matches"] == 18
    assert result["skipped"] == 13
    assert "Pick the 5 " in provider.prompt


def test_limit_of_one_returns_the_models_top_rank():
    rows = [board_row("AVGO"), board_row("NVDA")]
    provider = FakeProvider(
        ok_result(
            [
                {"symbol": "NVDA", "rank": 2, "why": "w", "risk": "r"},
                {"symbol": "AVGO", "rank": 1, "why": "w", "risk": "r"},
            ]
        )
    )

    result = scanner_triage.triage(board(rows), limit=1, provider=provider)

    assert [pick["symbol"] for pick in result["picks"]] == ["AVGO"]
    assert result["skipped"] == 1


def test_default_limit_is_five(three_match_board):
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(three_match_board, provider=provider)

    assert "Pick the 5 " in provider.prompt


def test_pick_shape_and_one_sentence_clamping(three_match_board):
    long_why = "First sentence about the setup. Second sentence that should be cut."
    provider = FakeProvider(
        ok_result([{"symbol": "AVGO", "rank": 1, "why": long_why, "risk": "  Extended\n on 2h.  "}])
    )

    result = scanner_triage.triage(three_match_board, provider=provider)

    pick = result["picks"][0]
    assert set(pick) == {"symbol", "rank", "why", "risk"}
    assert pick["why"] == "First sentence about the setup."
    assert pick["risk"] == "Extended on 2h."


def test_contract_shape_is_present_on_the_success_path(three_match_board):
    provider = FakeProvider(ok_result([{"symbol": "AVGO", "rank": 1, "why": "w", "risk": "r"}]))

    result = scanner_triage.triage(three_match_board, provider=provider)

    for key in ("available", "reason", "generatedAt", "provider", "picks", "skipped"):
        assert key in result
    assert result["available"] is True
    assert result["provider"] == "fake"
    assert result["generatedAt"].startswith("20")


# ---------------------------------------------------------------------------
# Empty and malformed boards
# ---------------------------------------------------------------------------

def test_empty_board_is_available_with_zero_picks():
    """0 matches is a complete answer, not an error -- and costs no tokens."""
    provider = FakeProvider(ok_result([]))

    result = scanner_triage.triage(board([]), provider=provider)

    assert result["available"] is True
    assert result["picks"] == []
    assert result["skipped"] == 0
    assert isinstance(result["reason"], str)
    assert provider.calls == [], "an empty board must not cost a model call"


def test_board_with_rows_but_no_matches_is_available():
    rows = [board_row("MSFT", scan_pass=False), board_row("TSLA", scan_pass=False)]

    result = scanner_triage.triage(board(rows), provider=FakeProvider(ok_result([])))

    assert result["available"] is True
    assert result["picks"] == []


@pytest.mark.parametrize("payload", [None, {}, [], "nonsense", 7, {"rows": None}, {"rows": "x"}])
def test_malformed_boards_never_raise(payload):
    result = scanner_triage.triage(payload, provider=FakeProvider(ok_result([])))

    assert result["picks"] == []
    assert isinstance(result["reason"], str)


def test_bare_row_list_is_accepted(three_match_board):
    provider = FakeProvider(ok_result([{"symbol": "AVGO", "rank": 1, "why": "w", "risk": "r"}]))

    result = scanner_triage.triage(three_match_board["rows"], provider=provider)

    assert result["available"] is True
    assert [pick["symbol"] for pick in result["picks"]] == ["AVGO"]


def test_rows_with_junk_cells_do_not_break_the_prompt():
    row = board_row("AVGO")
    row["rvol"] = {"5m": {"value": float("nan"), "bg": "black", "fg": "white"}, "D": "not-a-cell"}
    row["skittles"] = None
    row["scanReasons"] = None
    row["pctChange"] = "n/a"
    provider = FakeProvider(ok_result([]))

    result = scanner_triage.triage(board([row]), provider=provider)

    assert result["available"] is True
    assert "nan" not in provider.prompt.lower()
    json.dumps(result)  # the payload stays JSON-serialisable


# ---------------------------------------------------------------------------
# Cost
# ---------------------------------------------------------------------------

def test_prompt_is_a_small_fraction_of_the_board():
    """355 rows in, 18 matches sent. The saving is the feature, so it is asserted."""
    rows = [board_row(f"SYM{index}", scan_pass=index < 18) for index in range(355)]
    payload = board(rows)
    provider = FakeProvider(ok_result([]))

    scanner_triage.triage(payload, provider=provider)

    full = len(json.dumps(payload))
    sent = len(provider.prompt)
    assert sent < full / 20, f"prompt {sent} chars vs board {full} chars"
