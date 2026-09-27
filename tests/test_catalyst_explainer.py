"""Tests for the mover-to-catalyst explainer.

Everything here runs against a FAKE provider: no key is configured on this
box, no SDK is installed, and the point of the provider contract is that
neither is needed to test a feature. The fake also counts its calls, because
one of the requirements is about a call NOT being made.
"""
from __future__ import annotations

import json
import unittest

from agents import catalyst_explainer
from agents.catalyst_explainer import (
    CATEGORIES,
    NO_NEWS_SUMMARY,
    cached_headlines,
    explain,
    explain_from_cache,
    normalize_headlines,
)

HEADLINES = [
    {
        "title": "CrowdStrike Beats Q2 Estimates And Raises Full-Year Guidance",
        "url": "https://example.com/crwd-earnings",
        "ageMinutes": 40,
    },
    {
        "title": "Analysts Weigh In On Cybersecurity Spending",
        "url": "https://example.com/cyber",
        "ageMinutes": 300,
    },
]


class FakeProvider:
    """A stand-in for an AIProvider that records how it was used."""

    def __init__(self, response=None, *, configured: bool = True, name: str = "openai",
                 raises: Exception | None = None) -> None:
        self.name = name
        self.model = "fake-model"
        self._configured = configured
        self._response = response
        self._raises = raises
        self.calls: list[dict] = []

    def available(self) -> bool:
        return self._configured

    def complete(self, *, system: str, user: str, max_tokens: int = 2000, schema=None) -> dict:
        self.calls.append({"system": system, "user": user, "max_tokens": max_tokens, "schema": schema})
        if self._raises is not None:
            raise self._raises
        return self._response


def ok(payload: dict | None = None, *, text: str | None = None) -> dict:
    return {
        "ok": True,
        "text": text if text is not None else json.dumps(payload or {}),
        "data": payload,
        "error": "",
        "provider": "openai",
        "model": "fake-model",
    }


class ContractShapeTests(unittest.TestCase):
    def test_result_always_carries_the_shared_contract_keys(self) -> None:
        provider = FakeProvider(ok({
            "summary": "CrowdStrike beat and raised guidance.",
            "category": "EARNINGS",
            "confidence": "high",
            "headline": HEADLINES[0]["title"],
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        for key in ("available", "reason", "generatedAt", "provider", "summary", "category",
                    "confidence", "headline"):
            self.assertIn(key, result)
        self.assertTrue(result["available"])
        self.assertEqual(result["provider"], "openai")
        self.assertEqual(result["symbol"], "CRWD")
        self.assertEqual(result["changePct"], 20.0)
        self.assertEqual(result["category"], "EARNINGS")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["headline"]["url"], "https://example.com/crwd-earnings")
        self.assertEqual(result["headline"]["ageMinutes"], 40)


class NoProviderTests(unittest.TestCase):
    def test_no_provider_configured_is_available_false_not_an_exception(self) -> None:
        original = catalyst_explainer._resolve_provider
        catalyst_explainer._resolve_provider = lambda provider: None
        try:
            result = explain("CRWD", 20.0, HEADLINES)
        finally:
            catalyst_explainer._resolve_provider = original
        self.assertFalse(result["available"])
        self.assertIn("key", result["reason"].lower())
        self.assertIsNone(result["provider"])
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertIsNone(result["headline"])

    def test_provider_present_but_unconfigured_is_also_available_false(self) -> None:
        provider = FakeProvider(ok({}), configured=False)
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertFalse(result["available"])
        self.assertEqual(provider.calls, [], "an unconfigured provider must never be called")

    def test_module_import_pulls_in_no_sdk(self) -> None:
        # Importing a feature module must never import an SDK: the box has no
        # key and no SDK installed, and the import must not be what breaks.
        # Checked at module scope rather than via sys.modules so the test
        # stays true once an SDK IS installed and some other test imports it.
        import inspect
        import re as _re

        source = inspect.getsource(catalyst_explainer)
        top_level = _re.findall(r"^(?:from|import)\s+([\w.]+)", source, _re.MULTILINE)
        roots = [module.split(".")[0] for module in top_level]
        self.assertNotIn("openai", roots)
        self.assertNotIn("anthropic", roots)
        self.assertNotIn("agents", roots, "the provider itself is resolved lazily too")


class NoHeadlinesTests(unittest.TestCase):
    def test_empty_headlines_answer_unknown_without_calling_the_model(self) -> None:
        provider = FakeProvider(ok({"summary": "should never be used", "category": "EARNINGS",
                                    "confidence": "high"}))
        result = explain("CRWD", 20.0, [], provider=provider)
        self.assertEqual(provider.calls, [], "do not pay a model to be told there is no news")
        self.assertTrue(result["available"])
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertEqual(result["confidence"], "low")
        self.assertEqual(result["summary"], NO_NEWS_SUMMARY)
        self.assertIsNone(result["headline"])

    def test_headlines_that_are_all_junk_count_as_none(self) -> None:
        provider = FakeProvider(ok({"summary": "x", "category": "EARNINGS", "confidence": "high"}))
        result = explain("CRWD", 20.0, [{"url": "https://example.com"}, None, "", 7], provider=provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertEqual(result["summary"], NO_NEWS_SUMMARY)


class HonestyTests(unittest.TestCase):
    def test_system_prompt_demands_the_unknown_answer(self) -> None:
        prompt = catalyst_explainer.SYSTEM_PROMPT
        self.assertIn(NO_NEWS_SUMMARY, prompt)
        self.assertIn("UNKNOWN", prompt)
        self.assertIn("ONLY credit a headline from that list", prompt)
        self.assertIn("invent", prompt.lower())

    def test_unknown_verdict_is_never_confident_and_never_credits_a_source(self) -> None:
        # The model hedges: UNKNOWN but still claims high confidence and cites
        # a real headline. The verdict is clamped back to the honest answer.
        provider = FakeProvider(ok({
            "summary": NO_NEWS_SUMMARY,
            "category": "UNKNOWN",
            "confidence": "high",
            "headline": HEADLINES[1]["title"],
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertTrue(result["available"])
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["headline"])
        self.assertEqual(result["summary"], NO_NEWS_SUMMARY)

    def test_a_headline_we_never_passed_in_is_dropped(self) -> None:
        provider = FakeProvider(ok({
            "summary": "CrowdStrike won a $2B federal contract.",
            "category": "CONTRACT",
            "confidence": "high",
            "headline": "CrowdStrike Wins $2 Billion Federal Contract",  # never passed in
            "headlineIndex": 1,  # and the index must not launder it back in
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertIsNone(result["headline"], "an uncited claim must not be given a fake source")
        self.assertEqual(result["confidence"], "low")
        self.assertIn("not in the news", result["reason"])

    def test_a_headline_that_was_passed_in_is_credited(self) -> None:
        provider = FakeProvider(ok({
            "summary": "CrowdStrike beat estimates and raised guidance.",
            "category": "EARNINGS",
            "confidence": "high",
            "headline": HEADLINES[0]["title"],
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertEqual(result["headline"]["title"], HEADLINES[0]["title"])
        self.assertEqual(result["confidence"], "high")

    def test_headline_index_credits_the_matching_row(self) -> None:
        provider = FakeProvider(ok({
            "summary": "Sector spending chatter.",
            "category": "MACRO",
            "confidence": "medium",
            "headlineIndex": 2,
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertEqual(result["headline"]["title"], HEADLINES[1]["title"])

    def test_out_of_range_index_credits_nothing(self) -> None:
        provider = FakeProvider(ok({
            "summary": "Something happened.",
            "category": "PRODUCT",
            "confidence": "high",
            "headlineIndex": 9,
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertIsNone(result["headline"])
        self.assertEqual(result["confidence"], "low")


class ClampingTests(unittest.TestCase):
    def test_unknown_category_becomes_UNKNOWN(self) -> None:
        provider = FakeProvider(ok({
            "summary": "Short interest squeezed the stock.",
            "category": "SHORT_SQUEEZE",
            "confidence": "high",
            "headline": HEADLINES[0]["title"],
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertIn(result["category"], CATEGORIES)
        self.assertEqual(result["confidence"], "low")
        self.assertIsNone(result["headline"])

    def test_category_case_and_spacing_are_tolerated(self) -> None:
        provider = FakeProvider(ok({
            "summary": "Upgraded at Morgan Stanley.",
            "category": " upgrade ",
            "confidence": "MEDIUM",
            "headlineIndex": 1,
        }))
        result = explain("CRWD", 5.0, HEADLINES, provider=provider)
        self.assertEqual(result["category"], "UPGRADE")
        self.assertEqual(result["confidence"], "medium")

    def test_junk_confidence_falls_back_to_low(self) -> None:
        provider = FakeProvider(ok({
            "summary": "Earnings beat.",
            "category": "EARNINGS",
            "confidence": "very high indeed",
            "headlineIndex": 1,
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertEqual(result["confidence"], "low")

    def test_long_summary_is_trimmed_to_one_line(self) -> None:
        provider = FakeProvider(ok({
            "summary": "word " * 200,
            "category": "EARNINGS",
            "confidence": "high",
            "headlineIndex": 1,
        }))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertLessEqual(len(result["summary"]), catalyst_explainer.MAX_SUMMARY_CHARS)
        self.assertNotIn("\n", result["summary"])


class BadModelResponseTests(unittest.TestCase):
    def test_unparseable_response_degrades_to_available_false(self) -> None:
        provider = FakeProvider({
            "ok": True,
            "text": "I think it went up because of vibes.",
            "data": None,
            "error": "",
            "provider": "openai",
            "model": "fake-model",
        })
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("could not read", result["reason"])
        self.assertEqual(result["category"], "UNKNOWN")
        self.assertIsNone(result["headline"])

    def test_json_in_a_code_fence_is_still_read(self) -> None:
        payload = {"summary": "Earnings beat drove it.", "category": "EARNINGS",
                   "confidence": "high", "headlineIndex": 1}
        provider = FakeProvider({
            "ok": True,
            "text": "```json\n" + json.dumps(payload) + "\n```",
            "data": None,
            "error": "",
            "provider": "openai",
            "model": "fake-model",
        })
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertTrue(result["available"])
        self.assertEqual(result["category"], "EARNINGS")

    def test_missing_summary_is_treated_as_unreadable(self) -> None:
        provider = FakeProvider(ok({"category": "EARNINGS", "confidence": "high"}))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertFalse(result["available"])

    def test_provider_failure_is_reported_not_raised(self) -> None:
        provider = FakeProvider({"ok": False, "text": "", "data": None,
                                 "error": "rate limited", "provider": "openai", "model": "fake-model"})
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("rate limited", result["reason"])

    def test_a_provider_that_raises_is_still_contained(self) -> None:
        provider = FakeProvider(None, raises=RuntimeError("socket exploded"))
        result = explain("CRWD", 20.0, HEADLINES, provider=provider)
        self.assertFalse(result["available"])
        self.assertIn("socket exploded", result["reason"])


class PromptTests(unittest.TestCase):
    def test_prompt_lists_only_the_headlines_passed_in(self) -> None:
        provider = FakeProvider(ok({"summary": "s", "category": "UNKNOWN", "confidence": "low"}))
        explain("CRWD", 20.3, HEADLINES, provider=provider)
        user = provider.calls[0]["user"]
        self.assertIn("CRWD", user)
        self.assertIn("+20.3%", user)
        for row in HEADLINES:
            self.assertIn(row["title"], user)
        self.assertEqual(provider.calls[0]["schema"], catalyst_explainer.RESPONSE_SCHEMA)


class HeadlineNormalizationTests(unittest.TestCase):
    def test_the_pipelines_own_shapes_are_accepted(self) -> None:
        rows = normalize_headlines([
            {"headline": "CatalystCache verdict", "publishedAt": "2026-08-26T12:00:00+00:00",
             "ageMinutes": 12},
            {"headline": "CatalystEngine row", "url": "https://example.com/e",
             "published_at": "2026-08-26T12:00:00+00:00"},
            {"title": "Plain UI row", "link": "https://example.com/u"},
            "bare string headline",
        ])
        self.assertEqual([row["title"] for row in rows],
                         ["CatalystCache verdict", "CatalystEngine row", "Plain UI row",
                          "bare string headline"])
        self.assertEqual(rows[0]["ageMinutes"], 12)
        self.assertEqual(rows[2]["url"], "https://example.com/u")

    def test_duplicates_are_collapsed_and_the_list_is_capped(self) -> None:
        rows = normalize_headlines([{"title": "Same Story"}, {"title": "same story!"}])
        self.assertEqual(len(rows), 1)
        many = [{"title": f"Story {index}"} for index in range(40)]
        self.assertEqual(len(normalize_headlines(many)), catalyst_explainer.MAX_HEADLINES)

    def test_non_list_input_is_survivable(self) -> None:
        self.assertEqual(normalize_headlines(None), [])
        self.assertEqual(normalize_headlines("nope"), [])


class _StubCache:
    def __init__(self, entry) -> None:
        self.entry = entry
        self.asked: list[str] = []

    def get(self, symbol: str):
        self.asked.append(symbol)
        return self.entry


class _AngryCache:
    def get(self, symbol: str):
        raise RuntimeError("cache is on fire")


class CacheHelperTests(unittest.TestCase):
    def test_cached_headlines_reads_the_existing_catalyst_cache(self) -> None:
        cache = _StubCache({"headline": "CrowdStrike Beats", "ageMinutes": 5,
                            "publishedAt": "2026-08-26T12:00:00+00:00", "source": "benzinga"})
        rows = cached_headlines("crwd", cache)
        self.assertEqual(cache.asked, ["CRWD"], "the cache is keyed by upper-case symbol")
        self.assertEqual(rows, [{"title": "CrowdStrike Beats", "url": None, "ageMinutes": 5}])

    def test_empty_or_broken_cache_yields_no_headlines(self) -> None:
        self.assertEqual(cached_headlines("CRWD", _StubCache(None)), [])
        self.assertEqual(cached_headlines("CRWD", None), [])
        self.assertEqual(cached_headlines("", _StubCache({"headline": "x"})), [])
        self.assertEqual(cached_headlines("CRWD", _AngryCache()), [])

    def test_explain_from_cache_reaches_the_same_verdict(self) -> None:
        provider = FakeProvider(ok({"summary": "Earnings beat drove it.", "category": "EARNINGS",
                                    "confidence": "high", "headlineIndex": 1}))
        cache = _StubCache({"headline": "CrowdStrike Beats Q2 Estimates", "ageMinutes": 30})
        result = explain_from_cache("CRWD", 20.0, cache, provider=provider)
        self.assertTrue(result["available"])
        self.assertEqual(result["headline"]["title"], "CrowdStrike Beats Q2 Estimates")

    def test_explain_from_cache_with_an_empty_cache_makes_no_model_call(self) -> None:
        provider = FakeProvider(ok({"summary": "s", "category": "EARNINGS", "confidence": "high"}))
        result = explain_from_cache("CRWD", 20.0, _StubCache(None), provider=provider)
        self.assertEqual(provider.calls, [])
        self.assertEqual(result["summary"], NO_NEWS_SUMMARY)


if __name__ == "__main__":
    unittest.main()
