import copy
import time
from datetime import datetime, timezone

import pytest
import news_jev
from news_neural import NeuralNewsJudge, MODEL_ID

NOW = datetime(2026, 10, 30, 13, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def relevance(monkeypatch):
    monkeypatch.setattr(news_jev, "off_topic", lambda item: None)
    monkeypatch.setattr(news_jev, "shown_reading", lambda reading, item: reading)


def item(headline="Microsoft reports record profit"):
    return {"symbol": "MSFT", "headline": headline, "url": "https://example.test/story", "publishedAt": NOW.isoformat()}


@pytest.mark.parametrize("sentiment,label", [("positive", "bullish"), ("negative", "bearish"), ("neutral", "not_important")])
def test_local_sentiment_replaces_paid_annotations_and_survives_restart(tmp_path, sentiment, label):
    calls = []
    def infer(text):
        calls.append(text)
        return {key: 0.98 if key == sentiment else 0.01 for key in ("positive", "negative", "neutral")}
    j = NeuralNewsJudge(tmp_path / "local.json", inference=infer, background=False, clock=lambda: NOW.timestamp())
    original = item()
    p = {"feed": [copy.deepcopy(original)], "latest": {"MSFT": copy.deepcopy(original)}}
    j.annotate(p)
    j.annotate(p)
    assert len(calls) == 1
    assert p["feed"][0]["aiNews"]["label"] == label
    assert p["feed"][0]["aiNews"]["provider"] == "local-neural"
    assert "jev" not in p["feed"][0]
    restored = NeuralNewsJudge(tmp_path / "local.json", inference=lambda text: pytest.fail("cached story re-read"),
                               background=False, clock=lambda: NOW.timestamp())
    next_payload = {"feed": [original]}
    restored.annotate(next_payload)
    assert next_payload["feed"][0]["aiNews"]["model"] == MODEL_ID
    assert restored.status()["paidApi"] is False


def test_changed_headline_with_the_same_url_is_reclassified(tmp_path):
    calls = []
    def infer(text):
        calls.append(text)
        return {"positive": .98, "negative": .01, "neutral": .01}
    j = NeuralNewsJudge(tmp_path / "local.json", inference=infer, background=False, clock=lambda: NOW.timestamp())
    j.annotate({"feed": [item()]})
    j.annotate({"feed": [item("Microsoft revises its earlier earnings announcement")]})
    assert len(calls) == 2


def test_relevance_gate_does_not_discard_model_provenance(monkeypatch, tmp_path):
    monkeypatch.setattr(news_jev, "shown_reading", lambda reading, item: {"label": reading["label"], "p": reading["p"]})
    j = NeuralNewsJudge(tmp_path / "local.json", inference=lambda text: {"positive": .98, "negative": .01, "neutral": .01},
                        background=False, clock=lambda: NOW.timestamp())
    p = {"feed": [item()]}
    j.annotate(p)
    j.annotate(p)
    assert p["feed"][0]["aiNews"]["judgedAt"] == NOW.isoformat()
    assert p["feed"][0]["aiNews"]["revision"]


def test_failure_is_unknown_and_never_falls_back_to_a_paid_api(tmp_path):
    def failed(text):
        raise RuntimeError("model unavailable")
    j = NeuralNewsJudge(tmp_path / "local.json", inference=failed, background=False, clock=lambda: NOW.timestamp())
    p = {"feed": [item()]}
    j.annotate(p)
    assert "aiNews" not in p["feed"][0] and "jev" not in p["feed"][0]
    assert j.status()["errors"] == 1 and j.status()["paidApi"] is False


def test_multi_company_stories_do_not_invent_a_ticker_specific_direction(tmp_path):
    j = NeuralNewsJudge(tmp_path / "local.json", inference=lambda text: pytest.fail("ambiguous story must not be scored"),
                        background=False, clock=lambda: NOW.timestamp())
    p = {"feed": [{**item("Microsoft wins while Apple falls"), "relatedSymbols": ["MSFT", "AAPL"]}]}
    j.annotate(p)
    assert p["feed"][0]["aiNews"]["mixed"] is True
    assert p["feed"][0]["aiNews"]["p"] is None


def test_background_classification_does_not_block_the_news_reply(tmp_path):
    def slow(text):
        time.sleep(.25)
        return {"positive": .98, "negative": .01, "neutral": .01}
    j = NeuralNewsJudge(tmp_path / "local.json", inference=slow, clock=lambda: NOW.timestamp())
    start = time.monotonic()
    j.annotate({"feed": [item()]})
    assert time.monotonic() - start < .15


def test_queue_is_bounded_and_old_news_is_not_requeued(tmp_path):
    j = NeuralNewsJudge(tmp_path / "local.json", inference=lambda text: {}, clock=lambda: NOW.timestamp())
    j._start = lambda: None
    stories = [{**item(f"Microsoft headline {i}"), "url": f"https://example.test/{i}"} for i in range(250)]
    j.annotate({"feed": stories})
    assert j.status()["queued"] == 200
