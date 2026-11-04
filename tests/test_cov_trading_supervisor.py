"""Unit: agents/option_llm_supervisor.py - the advisory-only LLM SUPERVISOR card.

With the LLM switched on and a fake transport (urllib.request.urlopen inside
the module), the model's JSON is merged into the report; a malformed or slow
answer falls back to the rules report; and the authority block can never be
changed by the model. No request leaves the PC.
"""
from __future__ import annotations

import io
import json

import pytest

from agents import option_llm_supervisor as module
from agents.option_llm_supervisor import OptionLLMSupervisor

CONTEXT = {
    "planBlocks": [{"symbol": "NVDA", "stage": "planner", "reason": "spread 18% > 10%"}],
    "tradeHistory": [{"underlying_symbol": "PLTR", "pnl": -45.0, "status": "closed", "option_symbol": "PLTR260702C00129000"}],
    "scanCoverage": {"watchlistCount": 12, "candidateCount": 3},
}
LOCKED = {"canPlaceOrders": False, "canOverrideRules": False, "executionPath": "Rules engine only"}


class Reply(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def transport(monkeypatch, body=None, error=None):
    sent = []

    def urlopen(request, timeout=None):
        sent.append({"url": request.full_url, "timeout": timeout, "auth": request.get_header("Authorization")})
        if error is not None:
            raise error
        return Reply(json.dumps(body).encode("utf-8"))

    monkeypatch.setattr(module.urllib.request, "urlopen", urlopen)
    return sent


def authority(report):
    return {k: report["authority"][k] for k in LOCKED}


def test_a_json_answer_is_merged_but_the_authority_stays_locked(monkeypatch):
    model = {"summary": "NVDA blocked on spread; PLTR lost on a late entry.", "suggestions": ["Keep the 10% spread cap."],
             "unusual": ["Spread widened at the open."], "authority": {"canPlaceOrders": True}, "mode": "autonomous"}
    sent = transport(monkeypatch, {"output": [{"content": [{"type": "output_text", "text": json.dumps(model)}]}]})
    report = OptionLLMSupervisor(enabled=True, api_key="test-only", timeout_seconds=1).review(CONTEXT)
    assert sent and sent[0]["url"] == "https://api.openai.com/v1/responses" and sent[0]["timeout"] == 1.0
    assert report["summary"] == model["summary"]
    assert report["suggestions"] == ["Keep the 10% spread cap."]
    assert authority(report) == LOCKED
    assert report["mode"] == "advisory_only" and report["llm"]["mode"] == "openai"


@pytest.mark.parametrize("body,error", [
    ({"output_text": "this is not json"}, None),
    ({"output": []}, None),
    (None, TimeoutError("timed out")),
])
def test_a_bad_or_slow_answer_falls_back_to_the_rules_report(monkeypatch, body, error):
    transport(monkeypatch, body, error)
    rules = OptionLLMSupervisor(enabled=False).review(CONTEXT)
    report = OptionLLMSupervisor(enabled=True, api_key="test-only").review(CONTEXT)
    assert report["llm"]["mode"] == "rules_fallback"
    assert report["llm"]["note"].startswith("LLM review unavailable:")
    assert report["summary"] == rules["summary"]
    assert authority(report) == LOCKED


def test_no_key_or_switched_off_never_calls_the_model(monkeypatch):
    sent = transport(monkeypatch, {"output_text": "{}"})
    no_key = OptionLLMSupervisor(enabled=True, api_key="").review(CONTEXT)
    off = OptionLLMSupervisor(enabled=False, api_key="test-only").review(CONTEXT)
    assert sent == []
    assert (no_key["llm"]["mode"], off["llm"]["mode"]) == ("rules_fallback", "disabled")
    assert authority(no_key) == authority(off) == LOCKED
