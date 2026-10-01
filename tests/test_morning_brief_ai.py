"""Pins for the AI layer that sits on top of the deterministic briefing.

Everything here runs against a FAKE provider. No key exists in this repo and no
test may ever reach a live API: the fake is what lets us assert the exact text
sent to the model, which is where the guard rails live.
"""
from __future__ import annotations

import json
import unittest
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from agents import morning_brief_ai
from agents.morning_brief_ai import (
    MAX_WATCHLIST,
    SYSTEM_PROMPT,
    build_prompt,
    strip_markers,
    synthesize,
)
from morning_briefing import build_briefing

NOW = datetime(2026, 8, 24, 6, 5, tzinfo=ZoneInfo("America/New_York"))


class FakeProvider:
    """Records every call so a test can assert what the model was shown."""

    name = "fake"

    def __init__(self, payload=None, *, ok=True, error="", available=True,
                 text=None, raises=None):
        self._payload = payload if payload is not None else {
            "paragraph": "Quiet tape.", "watchlist": [],
        }
        self._ok = ok
        self._error = error
        self._available = available
        self._text = text
        self._raises = raises
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._available

    def complete(self, *, system, user, max_tokens=2000, schema=None):
        self.calls.append({
            "system": system, "user": user, "max_tokens": max_tokens, "schema": schema,
        })
        if self._raises is not None:
            raise self._raises
        return {
            "ok": self._ok,
            "text": self._text if self._text is not None else json.dumps(self._payload),
            "data": self._payload if self._ok else None,
            "error": self._error,
            "provider": "fake",
            "model": "fake-1",
        }


def _row(symbol, strength="STRONG", score=5, *, cyan=None, yellow=None,
         fires=None, forming=False, catalyst=None) -> dict:
    return {
        "symbol": symbol, "strength": strength, "score": score,
        "signals920": cyan or [], "signals48": yellow or [],
        "fires": fires or [], "forming": forming, "catalyst": catalyst,
    }


SCANNER = {"rows": [
    _row("NVDA", cyan=["CALL2H"], yellow=["CALL4H"], fires=["1h"],
         catalyst={"tag": "AI/PRODUCT", "headline": "Price Hikes Above 15%", "ageMinutes": 180}),
    _row("META", "MODERATE", 2, cyan=["CALL2H"], forming=True),
]}
QUOTES = {
    "SPY": {"change_pct": 0.4}, "QQQ": {"change_pct": 0.6},
    "TSLA": {"change_pct": 3.1}, "AMD": {"change_pct": -2.4},
}


def _briefing() -> dict:
    payload = build_briefing(SCANNER, QUOTES, NOW)
    payload["status"] = "READY"
    return payload


class NoProviderTests(unittest.TestCase):
    def test_missing_provider_is_a_normal_unavailable_result(self) -> None:
        with mock.patch.object(morning_brief_ai, "_default_provider", return_value=None):
            result = synthesize(_briefing(), SCANNER)
        self.assertFalse(result["available"])
        self.assertTrue(result["reason"].strip())
        self.assertEqual(result["paragraph"], "")
        self.assertEqual(result["watchlist"], [])
        self.assertIsNone(result["provider"])
        self.assertTrue(result["generatedAt"])

    def test_provider_that_reports_no_key_makes_no_model_call(self) -> None:
        provider = FakeProvider(available=False)
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertFalse(result["available"])
        self.assertEqual(provider.calls, [])

    def test_a_raising_provider_is_still_a_result_not_an_exception(self) -> None:
        provider = FakeProvider(raises=RuntimeError("socket died"))
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("socket died", result["reason"])

    def test_failed_call_reports_the_provider_error(self) -> None:
        provider = FakeProvider(ok=False, error="401 bad key")
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("401 bad key", result["reason"])


class NoBriefingMeansNoCallTests(unittest.TestCase):
    def test_waiting_briefing_never_reaches_the_model(self) -> None:
        waiting = {
            "status": "WAITING", "lines": [],
            "message": "The briefing builds weekday mornings 5:30-9:35 AM ET.",
        }
        provider = FakeProvider()
        result = synthesize(waiting, {"rows": []}, provider=provider)
        self.assertEqual(provider.calls, [], "a WAITING briefing must not spend a model call")
        self.assertFalse(result["available"])
        self.assertIn("not been built", result["reason"])

    def test_empty_lines_never_reach_the_model(self) -> None:
        provider = FakeProvider()
        for briefing in ({"lines": []}, {"lines": ["   "]}, {}, None):
            with self.subTest(briefing=briefing):
                result = synthesize(briefing, {"rows": []}, provider=provider)
                self.assertFalse(result["available"])
        self.assertEqual(provider.calls, [])

    def test_a_waiting_status_wins_even_with_stale_lines(self) -> None:
        provider = FakeProvider()
        stale = {"status": "WAITING", "lines": ["Strongest setup: NVDA - STRONG (5)."]}
        synthesize(stale, SCANNER, provider=provider)
        self.assertEqual(provider.calls, [])


class PromptTests(unittest.TestCase):
    def test_bold_markers_are_stripped_before_the_model_sees_them(self) -> None:
        briefing = {
            "status": "READY", "date": "2026-08-24",
            "lines": ["Watchlist gainers: **TSLA** +3.1%, **AMD** -2.4%."],
        }
        provider = FakeProvider({"paragraph": "ok", "watchlist": []})
        synthesize(briefing, SCANNER, provider=provider)
        sent = provider.calls[0]["user"]
        self.assertNotIn("**", sent)
        self.assertIn("TSLA +3.1%", sent)
        self.assertIn("AMD -2.4%", sent)

    def test_strip_markers_leaves_the_words_alone(self) -> None:
        self.assertEqual(strip_markers("**NVDA** +3.1%"), "NVDA +3.1%")
        self.assertEqual(strip_markers(None), "")

    def test_prompt_carries_the_measured_lines_and_scanner_rows(self) -> None:
        prompt = build_prompt(_briefing(), SCANNER)
        self.assertIn("Strongest setup: NVDA", prompt)
        self.assertIn("META", prompt)
        self.assertIn("Symbols you are allowed to name:", prompt)
        self.assertNotIn("**", prompt)

    def test_oi_context_is_included_when_given(self) -> None:
        prompt = build_prompt(_briefing(), SCANNER, {"symbol": "NVDA", "callWall": 180})
        self.assertIn("Open-interest context", prompt)
        self.assertIn("callWall", prompt)

    def test_system_prompt_forbids_invention_and_pins_symbols_to_the_input(self) -> None:
        lowered = SYSTEM_PROMPT.lower()
        self.assertIn("never invent a price", lowered)
        self.assertIn("level", lowered)
        self.assertIn("never predict", lowered)
        self.assertIn("must appear in the input", lowered)
        self.assertIn("no targets, no stops", lowered)

    def test_the_system_prompt_is_what_is_actually_sent(self) -> None:
        provider = FakeProvider()
        synthesize(_briefing(), SCANNER, provider=provider)
        self.assertEqual(provider.calls[0]["system"], SYSTEM_PROMPT)
        self.assertIsNotNone(provider.calls[0]["schema"])


class WatchlistGuardTests(unittest.TestCase):
    def test_a_symbol_that_is_not_in_the_input_is_dropped(self) -> None:
        provider = FakeProvider({
            "paragraph": "NVDA leads the board.",
            "watchlist": ["NVDA", "AAPL", "PLTR"],
        })
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertTrue(result["available"])
        self.assertEqual(result["watchlist"], ["NVDA"])

    def test_symbols_from_every_input_channel_survive(self) -> None:
        provider = FakeProvider({
            "paragraph": "Mixed tape.",
            "watchlist": ["tsla", "$SPY", "META"],
        })
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertEqual(result["watchlist"], ["TSLA", "SPY", "META"])

    def test_an_oi_context_symbol_counts_as_input(self) -> None:
        provider = FakeProvider({"paragraph": "p", "watchlist": ["SMCI"]})
        result = synthesize(_briefing(), SCANNER, {"rows": [{"symbol": "SMCI"}]},
                            provider=provider)
        self.assertEqual(result["watchlist"], ["SMCI"])
        self.assertEqual(
            synthesize(_briefing(), SCANNER, provider=FakeProvider(
                {"paragraph": "p", "watchlist": ["SMCI"]}))["watchlist"],
            [],
            "without the OI context SMCI is not in the input and must be dropped",
        )

    def test_watchlist_is_capped_at_three(self) -> None:
        provider = FakeProvider({
            "paragraph": "Busy board.",
            "watchlist": ["NVDA", "META", "TSLA", "AMD", "SPY"],
        })
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertEqual(len(result["watchlist"]), MAX_WATCHLIST)
        self.assertEqual(result["watchlist"], ["NVDA", "META", "TSLA"])

    def test_duplicates_and_junk_are_dropped(self) -> None:
        provider = FakeProvider({
            "paragraph": "p", "watchlist": ["NVDA", "nvda", "", None, 5, "NVDA"],
        })
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertEqual(result["watchlist"], ["NVDA"])

    def test_a_non_list_watchlist_is_survivable(self) -> None:
        provider = FakeProvider({"paragraph": "p", "watchlist": "NVDA"})
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertEqual(result["watchlist"], [])


class ResultShapeTests(unittest.TestCase):
    def test_available_result_carries_the_contract_keys(self) -> None:
        provider = FakeProvider({"paragraph": "Tape leans bullish.", "watchlist": ["NVDA"]})
        result = synthesize(_briefing(), SCANNER, provider=provider)
        for key in ("available", "reason", "generatedAt", "provider", "paragraph", "watchlist"):
            self.assertIn(key, result)
        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "fake")
        self.assertEqual(result["paragraph"], "Tape leans bullish.")

    def test_the_paragraph_is_labelled_as_ai_written(self) -> None:
        provider = FakeProvider({"paragraph": "p", "watchlist": []})
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertIn("AI", result["label"])

    def test_markers_never_come_back_out_in_the_paragraph(self) -> None:
        provider = FakeProvider({"paragraph": "**NVDA** is the lead.", "watchlist": []})
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertNotIn("**", result["paragraph"])
        self.assertEqual(result["paragraph"], "NVDA is the lead.")

    def test_an_empty_paragraph_is_not_shown(self) -> None:
        provider = FakeProvider({"paragraph": "   ", "watchlist": ["NVDA"]})
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("empty", result["reason"].lower())

    def test_json_only_in_text_is_still_parsed(self) -> None:
        provider = FakeProvider(payload=None, text=json.dumps(
            {"paragraph": "From text.", "watchlist": ["NVDA"]}))
        provider._payload = None  # force the text fallback path
        result = synthesize(_briefing(), SCANNER, provider=provider)
        self.assertTrue(result["available"])
        self.assertEqual(result["paragraph"], "From text.")
        self.assertEqual(result["watchlist"], ["NVDA"])

    def test_the_deterministic_lines_are_untouched(self) -> None:
        briefing = _briefing()
        before = list(briefing["lines"])
        synthesize(briefing, SCANNER, provider=FakeProvider())
        self.assertEqual(briefing["lines"], before)


if __name__ == "__main__":
    unittest.main()
