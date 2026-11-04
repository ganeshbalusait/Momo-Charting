"""Integration: the AI layer behind the trading pages, over real HTTP.

/api/ai/status (which provider is on, plain English), /api/ai/journal-lessons
(what his closed trades say - the model only interprets the statistics), and
the option LLM supervisor's report as the dashboard serves it (advisory only:
it can never be given order authority, whatever the model answers).

Every AI call is a fake: no key reaches a provider and no request leaves the
PC (the live guard would refuse it).
"""
from __future__ import annotations

import io
import json
import time

import pytest

from agents import ai_endpoints, journal_lessons
# Imported now, not inside the first request: agents.ai_provider runs load_dotenv() on import,
# which would put the keys this file removes straight back into the environment.
from agents import ai_provider  # noqa: F401
from agents import option_llm_supervisor as supervisor_module
from agents.option_llm_supervisor import OptionLLMSupervisor
from api_harness import running_api, seed_user
from cf_access_auth import ACCESS_EMAIL_HEADER

OWNER = "owner-cov-ai@example.com"
VIEWER = "viewer-cov-ai@example.com"
ADMIN = {ACCESS_EMAIL_HEADER: OWNER}
USER = {ACCESS_EMAIL_HEADER: VIEWER}
AI_ENV = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGX_AI_PROVIDER", "OPENAI_BASE_URL", "OPENAI_MODEL", "ANTHROPIC_MODEL")


@pytest.fixture(autouse=True, scope="module")
def _accounts():
    seed_user(OWNER, role="admin")
    seed_user(VIEWER, role="user")


@pytest.fixture(autouse=True)
def no_ai_keys(monkeypatch):
    for name in AI_ENV:
        monkeypatch.delenv(name, raising=False)
    ai_endpoints.invalidate()
    yield
    ai_endpoints.invalidate()


def settled(api, path, headers):
    """The AI routes answer 'writing it now' while a build runs; read until it lands."""
    for _ in range(40):
        status, body = api.get(path, headers=headers)
        if not body.get("pending"):
            return status, body
        time.sleep(0.25)
    raise AssertionError(f"{path} never finished building")


# ----------------------------------------------------------------- status

def test_with_no_key_the_status_says_ai_is_off_and_names_the_key_to_add():
    with running_api() as api:
        status, body = api.get("/api/ai/status", headers=ADMIN)
    assert status == 200
    assert (body["configured"], body["provider"], body["model"]) == (False, None, None)
    assert "OPENAI_API_KEY" in body["message"]
    assert {c["name"] for c in body["candidates"]} == {"openai", "claude"}


def test_an_openai_compatible_gemini_key_is_named_as_the_active_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only-not-a-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta/openai")
    monkeypatch.setenv("OPENAI_MODEL", "gemini-3.6-flash")
    with running_api() as api:
        status, body = api.get("/api/ai/status", headers=ADMIN)
    assert status == 200
    assert (body["configured"], body["provider"], body["model"]) == (True, "openai", "gemini-3.6-flash")
    assert body["message"].startswith("AI is on, using OpenAI (gemini-3.6-flash).")
    assert "ANTHROPIC_API_KEY" in body["message"]


# --------------------------------------------------------- journal lessons

def closed_trades(n=12):
    rows = []
    for i in range(n):
        day = 10 + i % 10
        rows.append({"id": f"t{i}", "symbol": "AAPL" if i % 2 else "NVDA", "setup_name": "EMA + VWAP + ORB",
                     "status": "closed", "opened_at": f"2026-10-{day:02d}T14:00:00Z",
                     "closed_at": f"2026-10-{day:02d}T14:40:00Z", "pnl": 25.0 if i % 3 else -40.0,
                     "notes": "runner stop"})
    return rows


class FakeProvider:
    name = "fake"

    def __init__(self, answer):
        self.answer = answer
        self.calls = []

    def available(self):
        return True

    def complete(self, *, system, user, max_tokens=2000, schema=None):
        self.calls.append({"system": system, "user": user, "schema": schema})
        return self.answer


def test_journal_lessons_read_the_closed_trades_and_ask_the_model_once(monkeypatch):
    provider = FakeProvider({"ok": True, "provider": "fake", "data": {"lessons": []}})
    monkeypatch.setattr(journal_lessons, "load_closed_trades", lambda limit=500, db_path=None: closed_trades())
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: provider)
    with running_api() as api:
        status, body = settled(api, "/api/ai/journal-lessons", ADMIN)
    assert status == 200
    assert (body["available"], body["sampleSize"], body["provider"]) == (True, 12, "fake")
    assert body["reason"].startswith("Read 12 closed trades.")
    assert body["stats"] and len(provider.calls) == 1
    assert provider.calls[0]["schema"] is not None


def test_a_provider_failure_is_an_honest_unavailable_answer_not_a_500(monkeypatch):
    provider = FakeProvider({"ok": False, "provider": "fake", "error": "quota exhausted"})
    monkeypatch.setattr(journal_lessons, "load_closed_trades", lambda limit=500, db_path=None: closed_trades())
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: provider)
    with running_api() as api:
        status, body = settled(api, "/api/ai/journal-lessons", ADMIN)
    assert status == 200
    assert body["available"] is False
    assert body["reason"] == "The AI provider could not answer: quota exhausted"


def test_too_few_closed_trades_never_spends_a_model_call(monkeypatch):
    provider = FakeProvider({"ok": True, "data": {"lessons": []}})
    monkeypatch.setattr(journal_lessons, "load_closed_trades", lambda limit=500, db_path=None: closed_trades(4))
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: provider)
    with running_api() as api:
        _status, body = settled(api, "/api/ai/journal-lessons", ADMIN)
    assert body["sampleSize"] == 4 and provider.calls == []
    assert "need at least 10 closed trades" in body["reason"]


@pytest.mark.xfail(strict=True, reason=(
    "GET /api/ai/journal-lessons has no admin gate: any signed-in user reads the statistics of the owner's "
    "trade journal (and can spend his AI quota)"))
def test_a_non_admin_cannot_read_the_owners_journal_lessons(monkeypatch):
    provider = FakeProvider({"ok": True, "provider": "fake", "data": {"lessons": []}})
    monkeypatch.setattr(journal_lessons, "load_closed_trades", lambda limit=500, db_path=None: closed_trades())
    monkeypatch.setattr(journal_lessons, "_registry_provider", lambda preferred=None: provider)
    with running_api() as api:
        status, _body = settled(api, "/api/ai/journal-lessons", USER)
    assert status == 403
    assert provider.calls == []


# ------------------------------------------------------ option supervisor

class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class DashboardWith:
    def __init__(self, payload):
        self.payload = payload

    def dashboard_payload(self):
        return dict(self.payload)


def test_the_supervisor_report_served_to_the_page_never_carries_order_authority(monkeypatch):
    answer = {"summary": "Two blocks were spread-related.", "suggestions": ["Review the spread filter."],
              "authority": {"canPlaceOrders": True, "canOverrideRules": True, "executionPath": "LLM"}}
    sent = []

    def fake_urlopen(request, timeout=None):
        sent.append(request.full_url)
        return FakeResponse(json.dumps({"output_text": json.dumps(answer)}).encode("utf-8"))

    monkeypatch.setattr(supervisor_module.urllib.request, "urlopen", fake_urlopen)
    report = OptionLLMSupervisor(enabled=True, api_key="test-only-not-a-key").review({"planBlocks": []})
    with running_api(state=DashboardWith({"optionSupervisorReport": report})) as api:
        status, body = api.get("/api/dashboard?compact=true", headers=ADMIN)
    assert status == 200 and sent, "the fake transport was used"
    served = body["optionSupervisorReport"]
    assert served["summary"] == "Two blocks were spread-related."
    assert served["authority"]["canPlaceOrders"] is False
    assert served["authority"]["canOverrideRules"] is False
    assert served["authority"]["executionPath"] == "Rules engine only"
    assert (served["mode"], served["llm"]["mode"]) == ("advisory_only", "openai")
