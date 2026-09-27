from __future__ import annotations

import json
import unittest

from news_sentiment_ai import AiSentiment, NewsSentimentClassifier


def _reply(items: list[dict]) -> str:
    return json.dumps({"choices": [{"message": {"content": json.dumps({"items": items})}}]})


class FakePoster:
    def __init__(self, replies: list[str] | None = None, raise_error: Exception | None = None) -> None:
        self.calls: list[dict] = []
        self.replies = list(replies or [])
        self.raise_error = raise_error

    def __call__(self, url: str, headers: dict, body: bytes, timeout: float) -> str:
        self.calls.append({"url": url, "headers": headers, "payload": json.loads(body), "timeout": timeout})
        if self.raise_error is not None:
            raise self.raise_error
        return self.replies.pop(0)


class NewsSentimentClassifierTests(unittest.TestCase):
    def test_unavailable_without_a_key_and_never_calls_out(self) -> None:
        poster = FakePoster()
        classifier = NewsSentimentClassifier(api_key="", http_post=poster)
        self.assertFalse(classifier.available)
        self.assertEqual(classifier.classify([{"key": "a", "headline": "Apple beats"}]), {})
        self.assertEqual(poster.calls, [])
        self.assertEqual(classifier.status()["reason"], "OPENAI_API_KEY not set")

        disabled = NewsSentimentClassifier(enabled=False, api_key="sk-test", http_post=poster)
        self.assertFalse(disabled.available)
        self.assertEqual(disabled.status()["reason"], "disabled")

    def test_labels_each_headline_positive_negative_or_neutral_with_a_reason(self) -> None:
        poster = FakePoster([_reply([
            {"id": 1, "sentiment": "Positive", "reason": "Earnings beat and guidance raised"},
            {"id": 2, "sentiment": "negative", "reason": "Recall over safety fault"},
            {"id": 3, "sentiment": "Neutral", "reason": "Routine conference appearance"},
        ])])
        classifier = NewsSentimentClassifier(api_key="sk-test", model="gpt-4o-mini", http_post=poster)
        verdicts = classifier.classify([
            {"key": "aapl", "headline": "Apple beats on iPhone demand, raises guidance", "summary": "Q3 beat."},
            {"key": "tsla", "headline": "Tesla recalls 300k vehicles over steering fault"},
            {"key": "nvda", "headline": "Nvidia to present at industry conference"},
        ])
        self.assertEqual(verdicts["aapl"], AiSentiment(label="Positive", reason="Earnings beat and guidance raised", model="gpt-4o-mini"))
        self.assertEqual(verdicts["tsla"].label, "Negative")
        self.assertEqual(verdicts["nvda"].label, "Neutral")
        # One OpenAI-compatible chat request with JSON mode, ids 1..n.
        self.assertEqual(len(poster.calls), 1)
        call = poster.calls[0]
        self.assertEqual(call["url"], "https://api.openai.com/v1/chat/completions")
        self.assertEqual(call["headers"]["Authorization"], "Bearer sk-test")
        self.assertEqual(call["payload"]["model"], "gpt-4o-mini")
        self.assertEqual(call["payload"]["response_format"], {"type": "json_object"})
        sent = json.loads(call["payload"]["messages"][1]["content"])["items"]
        self.assertEqual([item["id"] for item in sent], [1, 2, 3])
        self.assertEqual(sent[0]["summary"], "Q3 beat.")
        self.assertNotIn("summary", sent[1])
        self.assertEqual(classifier.status()["labeled"], 3)
        self.assertEqual(classifier.status()["lastError"], "")

    def test_batches_requests_and_reuses_cached_labels(self) -> None:
        poster = FakePoster([
            _reply([{"id": 1, "sentiment": "Positive", "reason": "a"}, {"id": 2, "sentiment": "Negative", "reason": "b"}]),
            _reply([{"id": 1, "sentiment": "Neutral", "reason": "c"}]),
        ])
        classifier = NewsSentimentClassifier(api_key="sk-test", batch_size=2, http_post=poster)
        first = classifier.classify([
            {"key": "1", "headline": "One"}, {"key": "2", "headline": "Two"}, {"key": "3", "headline": "Three"},
        ])
        self.assertEqual([first[key].label for key in ("1", "2", "3")], ["Positive", "Negative", "Neutral"])
        self.assertEqual(len(poster.calls), 2)
        # Same headline again (any whitespace/case) is answered from the cache, no new call.
        again = classifier.classify([{"key": "x", "headline": "  two "}, {"key": "y", "headline": "THREE"}])
        self.assertEqual(again["x"].label, "Negative")
        self.assertEqual(again["y"].label, "Neutral")
        self.assertEqual(len(poster.calls), 2)

    def test_api_failure_returns_nothing_and_records_the_error(self) -> None:
        poster = FakePoster(raise_error=RuntimeError("HTTP 429"))
        classifier = NewsSentimentClassifier(api_key="sk-test", http_post=poster)
        self.assertEqual(classifier.classify([{"key": "a", "headline": "Apple beats"}]), {})
        self.assertEqual(classifier.status()["lastError"], "RuntimeError: HTTP 429")

    def test_unanswered_or_unknown_labels_are_left_out(self) -> None:
        poster = FakePoster([_reply([
            {"id": 1, "sentiment": "Positive", "reason": "ok"},
            {"id": 2, "sentiment": "Purple", "reason": "nonsense"},
            # id 3 missing entirely
        ])])
        classifier = NewsSentimentClassifier(api_key="sk-test", http_post=poster)
        verdicts = classifier.classify([
            {"key": "1", "headline": "One"}, {"key": "2", "headline": "Two"}, {"key": "3", "headline": "Three"},
        ])
        self.assertEqual(set(verdicts), {"1"})

    def test_malformed_reply_is_an_error_not_a_crash(self) -> None:
        poster = FakePoster(["not json"])
        classifier = NewsSentimentClassifier(api_key="sk-test", http_post=poster)
        self.assertEqual(classifier.classify([{"key": "1", "headline": "One"}]), {})
        self.assertTrue(classifier.status()["lastError"].startswith("JSONDecodeError"))


if __name__ == "__main__":
    unittest.main()
