"""Contract tests for agents/ai_provider.py.

Nothing here touches the network. Every HTTP call is served by a fake transport
with the signature the module injects:

    transport(url, *, headers, body, timeout) -> (status_code, response_text)

Raising from that callable simulates a timeout or a dead socket.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from agents import ai_provider

REPO_ROOT = Path(__file__).resolve().parents[1]

AI_ENV_VARS = (
    "OPENAI_API_KEY",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL",
    "ANTHROPIC_BASE_URL",
    "AGX_AI_PROVIDER",
    "AGX_AI_TIMEOUT_SECONDS",
    "AGX_AI_EFFORT",
)


@pytest.fixture(autouse=True)
def clean_ai_env(monkeypatch):
    """A developer with a real key in .env must not change these outcomes."""

    for name in AI_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


class FakeTransport:
    """Replays canned (status, text) responses and records every request."""

    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, url, *, headers, body, timeout):
        self.calls.append(
            {
                "url": url,
                "headers": headers,
                "payload": json.loads(body.decode("utf-8")),
                "timeout": timeout,
            }
        )
        outcome = self.responses.pop(0) if self.responses else self.responses_exhausted()
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome

    def responses_exhausted(self):  # pragma: no cover - a test bug, not a code path
        raise AssertionError("fake transport called more times than the test allows")


def openai_body(content: str) -> str:
    return json.dumps({"choices": [{"message": {"role": "assistant", "content": content}}]})


def claude_body(text: str) -> str:
    return json.dumps(
        {
            "id": "msg_1",
            "content": [
                {"type": "thinking", "thinking": "internal reasoning, not the answer"},
                {"type": "text", "text": text},
            ],
        }
    )


def openai(transport, **kwargs):
    kwargs.setdefault("api_key", "sk-test")
    return ai_provider.OpenAIProvider(transport=transport, **kwargs)


def claude(transport, **kwargs):
    kwargs.setdefault("api_key", "sk-ant-test")
    return ai_provider.ClaudeProvider(transport=transport, **kwargs)


# ---------------------------------------------------------------------------
# import hygiene
# ---------------------------------------------------------------------------


def test_import_pulls_in_neither_sdk():
    """A fresh interpreter must import the module without any SDK loading."""

    script = (
        "import sys;"
        "import agents.ai_provider;"
        "assert 'openai' not in sys.modules, 'openai was imported at module import';"
        "assert 'anthropic' not in sys.modules, 'anthropic was imported at module import';"
        "print('clean')"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert "clean" in result.stdout


def test_module_object_has_not_loaded_an_sdk():
    assert "openai" not in sys.modules
    assert "anthropic" not in sys.modules


# ---------------------------------------------------------------------------
# provider selection
# ---------------------------------------------------------------------------


def test_get_provider_is_none_when_no_key_is_set():
    assert ai_provider.get_provider() is None
    assert ai_provider.get_provider("claude") is None


def test_openai_wins_when_both_keys_exist(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    provider = ai_provider.get_provider()

    assert provider is not None
    assert provider.name == "openai"


def test_claude_is_used_when_it_is_the_only_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    provider = ai_provider.get_provider()

    assert provider is not None
    assert provider.name == "claude"
    assert provider.model == "claude-opus-5"


def test_env_override_beats_the_default_order(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AGX_AI_PROVIDER", "claude")

    assert ai_provider.get_provider().name == "claude"


def test_explicit_preference_beats_the_env_override(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AGX_AI_PROVIDER", "claude")

    assert ai_provider.get_provider("openai").name == "openai"


def test_anthropic_alias_resolves_to_claude(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("AGX_AI_PROVIDER", "Anthropic")

    assert ai_provider.get_provider().name == "claude"


def test_unknown_env_value_falls_back_instead_of_crashing(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("AGX_AI_PROVIDER", "llama-on-a-toaster")

    provider = ai_provider.get_provider()

    assert provider is not None
    assert provider.name == "openai"
    assert ai_provider.provider_order("nonsense") == ["openai", "claude"]


def test_preferred_but_unconfigured_falls_through_to_the_configured_one(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    assert ai_provider.get_provider("claude").name == "openai"


# ---------------------------------------------------------------------------
# provider_status
# ---------------------------------------------------------------------------


def test_status_with_no_key_is_plain_english_and_names_the_env_var():
    status = ai_provider.provider_status()

    assert status["configured"] is False
    assert status["provider"] is None
    assert status["model"] is None
    assert "OPENAI_API_KEY" in status["message"]
    assert "ANTHROPIC_API_KEY" in status["message"]
    assert ".env" in status["message"]
    # No developer jargon in something a trader reads.
    lowered = status["message"].lower()
    assert "traceback" not in lowered
    assert "exception" not in lowered
    assert {item["name"] for item in status["candidates"]} == {"openai", "claude"}
    assert all(item["configured"] is False for item in status["candidates"])


def test_status_when_configured_names_the_provider_and_the_switch(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    status = ai_provider.provider_status()

    assert status["configured"] is True
    assert status["provider"] == "openai"
    assert status["model"] == "gpt-4.1-mini"
    assert "OpenAI" in status["message"]
    assert "ANTHROPIC_API_KEY" in status["message"]
    candidates = {item["name"]: item for item in status["candidates"]}
    assert candidates["openai"]["configured"] is True
    assert candidates["claude"]["configured"] is False


def test_status_offers_the_switch_when_both_keys_exist(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

    message = ai_provider.provider_status()["message"]

    assert "AGX_AI_PROVIDER=claude" in message


# ---------------------------------------------------------------------------
# OpenAI adapter - happy path and request shape
# ---------------------------------------------------------------------------


def test_openai_success_returns_text():
    transport = FakeTransport((200, openai_body("Momentum is intact.")))

    result = openai(transport).complete(system="be terse", user="what now?")

    assert result["ok"] is True
    assert result["text"] == "Momentum is intact."
    assert result["data"] is None
    assert result["error"] == ""
    assert result["provider"] == "openai"
    assert result["model"] == "gpt-4.1-mini"


def test_openai_request_shape_matches_the_house_idiom():
    transport = FakeTransport((200, openai_body("ok")))

    openai(transport).complete(system="system text", user="user text", max_tokens=1234)

    call = transport.calls[0]
    assert call["url"] == "https://api.openai.com/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer sk-test"
    assert call["payload"]["messages"][0] == {"role": "system", "content": "system text"}
    assert call["payload"]["messages"][1] == {"role": "user", "content": "user text"}
    assert call["payload"]["max_tokens"] == 1234
    assert call["timeout"] == pytest.approx(20.0)


def test_openai_base_url_env_is_honoured(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:11434/v1/")
    monkeypatch.setenv("OPENAI_MODEL", "local-model")
    transport = FakeTransport((200, openai_body("ok")))

    provider = openai(transport)
    provider.complete(system="s", user="u")

    assert provider.model == "local-model"
    assert transport.calls[0]["url"] == "http://localhost:11434/v1/chat/completions"


def test_openai_reasoning_models_use_max_completion_tokens():
    transport = FakeTransport((200, openai_body("ok")))

    openai(transport, model="o3-mini").complete(system="s", user="u", max_tokens=900)

    payload = transport.calls[0]["payload"]
    assert payload["max_completion_tokens"] == 900
    assert "max_tokens" not in payload
    assert "temperature" not in payload


# ---------------------------------------------------------------------------
# OpenAI adapter - failure paths
# ---------------------------------------------------------------------------


def test_openai_without_a_key_is_a_readable_result_not_an_exception():
    result = ai_provider.OpenAIProvider(api_key="").complete(system="s", user="u")

    assert result["ok"] is False
    assert "OPENAI_API_KEY" in result["error"]
    assert result["data"] is None


def test_openai_non_200_returns_the_api_message():
    transport = FakeTransport((401, json.dumps({"error": {"message": "Incorrect API key provided"}})))

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is False
    assert "401" in result["error"]
    assert "Incorrect API key provided" in result["error"]


def test_openai_timeout_is_reported_and_never_retried():
    transport = FakeTransport(TimeoutError("timed out"))

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is False
    assert "timed out" in result["error"]
    # A trading UI must not wait out a second full timeout.
    assert len(transport.calls) == 1


def test_openai_connection_error_is_retried_once_then_reported():
    transport = FakeTransport(
        ConnectionResetError("connection reset by peer"),
        ConnectionResetError("connection reset by peer"),
    )

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is False
    assert "could not reach the API" in result["error"]
    assert len(transport.calls) == 2


def test_openai_retries_a_429_once_and_succeeds():
    transport = FakeTransport(
        (429, json.dumps({"error": {"message": "rate limited"}})),
        (200, openai_body("second time lucky")),
    )

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is True
    assert result["text"] == "second time lucky"
    assert len(transport.calls) == 2


def test_openai_malformed_envelope_is_a_readable_failure():
    transport = FakeTransport((200, "<html>502 Bad Gateway</html>"))

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is False
    assert "not JSON" in result["error"]
    assert result["text"] == "<html>502 Bad Gateway</html>"


def test_openai_empty_choices_is_a_readable_failure():
    transport = FakeTransport((200, json.dumps({"choices": []})))

    result = openai(transport).complete(system="s", user="u")

    assert result["ok"] is False
    assert "empty" in result["error"].lower()


def test_openai_never_raises_when_the_transport_explodes():
    def hostile(url, *, headers, body, timeout):
        raise RuntimeError("kaboom")

    result = openai(hostile).complete(system="s", user="u")

    assert result["ok"] is False
    assert "kaboom" in result["error"]


# ---------------------------------------------------------------------------
# schema handling
# ---------------------------------------------------------------------------

SCHEMA = {
    "type": "object",
    "properties": {"bias": {"type": "string"}, "confidence": {"type": "number"}},
    "required": ["bias"],
}


def test_openai_schema_requests_json_and_parses_it():
    transport = FakeTransport((200, openai_body('{"bias": "long", "confidence": 0.71}')))

    result = openai(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is True
    assert result["data"] == {"bias": "long", "confidence": 0.71}
    payload = transport.calls[0]["payload"]
    assert payload["response_format"]["type"] == "json_schema"
    assert payload["response_format"]["json_schema"]["schema"] == SCHEMA
    # The schema is also spelled out in the system prompt so json_object-only
    # endpoints still get shaped output.
    assert "JSON Schema" in payload["messages"][0]["content"]


def test_openai_schema_survives_markdown_fences():
    fenced = '```json\n{"bias": "short"}\n```'
    transport = FakeTransport((200, openai_body(fenced)))

    result = openai(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is True
    assert result["data"] == {"bias": "short"}


def test_unparseable_json_fails_but_keeps_the_raw_text():
    transport = FakeTransport((200, openai_body("I cannot help with that.")))

    result = openai(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is False
    assert result["data"] is None
    assert "valid JSON" in result["error"]
    assert result["text"] == "I cannot help with that."


def test_openai_400_on_json_schema_falls_back_to_json_object_once():
    transport = FakeTransport(
        (400, json.dumps({"error": {"message": "Invalid parameter: response_format"}})),
        (200, openai_body('{"bias": "flat"}')),
    )

    result = openai(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is True
    assert result["data"] == {"bias": "flat"}
    assert transport.calls[0]["payload"]["response_format"]["type"] == "json_schema"
    assert transport.calls[1]["payload"]["response_format"] == {"type": "json_object"}
    assert len(transport.calls) == 2


def test_openai_gives_up_after_the_single_compatibility_retry():
    transport = FakeTransport(
        (400, json.dumps({"error": {"message": "Invalid parameter: response_format"}})),
        (400, json.dumps({"error": {"message": "still invalid"}})),
    )

    result = openai(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is False
    assert "400" in result["error"]
    assert "still invalid" in result["error"]
    assert len(transport.calls) == 2


def test_parse_json_payload_wraps_a_top_level_array():
    assert ai_provider.parse_json_payload('[{"a": 1}]') == {"items": [{"a": 1}]}
    assert ai_provider.parse_json_payload("not json at all") is None
    assert ai_provider.parse_json_payload("") is None


# ---------------------------------------------------------------------------
# Claude adapter
# ---------------------------------------------------------------------------


def test_claude_success_ignores_thinking_blocks():
    transport = FakeTransport((200, claude_body("Range day, stay small.")))

    result = claude(transport).complete(system="s", user="u")

    assert result["ok"] is True
    assert result["text"] == "Range day, stay small."
    assert result["provider"] == "claude"
    assert result["model"] == "claude-opus-5"


def test_claude_request_uses_the_current_api_shape():
    transport = FakeTransport((200, claude_body("ok")))

    claude(transport).complete(system="system text", user="user text", max_tokens=2000)

    call = transport.calls[0]
    payload = call["payload"]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"]["x-api-key"] == "sk-ant-test"
    assert call["headers"]["anthropic-version"] == "2023-06-01"
    assert payload["model"] == "claude-opus-5"
    assert payload["system"] == "system text"
    assert payload["messages"] == [{"role": "user", "content": "user text"}]
    # Adaptive thinking; the old enabled/budget_tokens shape is a 400 on this model.
    assert payload["thinking"] == {"type": "adaptive"}
    assert "budget_tokens" not in json.dumps(payload)
    # Effort lives inside output_config, not at the top level.
    assert payload["output_config"]["effort"] == "medium"
    assert "effort" not in {key for key in payload if key != "output_config"}
    # max_tokens also pays for thinking tokens, so it is floored.
    assert payload["max_tokens"] == 16000


def test_claude_effort_env_is_honoured(monkeypatch):
    monkeypatch.setenv("AGX_AI_EFFORT", "high")
    transport = FakeTransport((200, claude_body("ok")))

    claude(transport).complete(system="s", user="u")

    assert transport.calls[0]["payload"]["output_config"]["effort"] == "high"


def test_claude_schema_uses_output_config_format_not_output_format():
    transport = FakeTransport((200, claude_body('{"bias": "long"}')))

    result = claude(transport).complete(system="s", user="u", schema=SCHEMA)

    assert result["ok"] is True
    assert result["data"] == {"bias": "long"}
    payload = transport.calls[0]["payload"]
    assert payload["output_config"]["format"]["schema"] == SCHEMA
    assert "output_format" not in payload


def test_claude_without_a_key_is_a_readable_result():
    result = ai_provider.ClaudeProvider(api_key="").complete(system="s", user="u")

    assert result["ok"] is False
    assert "ANTHROPIC_API_KEY" in result["error"]


def test_claude_non_200_timeout_and_malformed_json_all_degrade():
    overload = FakeTransport((529, json.dumps({"error": {"message": "Overloaded"}})))
    result = claude(overload).complete(system="s", user="u")
    assert result["ok"] is False
    assert "529" in result["error"]
    assert "Overloaded" in result["error"]

    timed_out = FakeTransport(TimeoutError("timed out"))
    result = claude(timed_out).complete(system="s", user="u")
    assert result["ok"] is False
    assert "timed out" in result["error"]
    assert len(timed_out.calls) == 1

    garbage = FakeTransport((200, "not json"))
    result = claude(garbage).complete(system="s", user="u")
    assert result["ok"] is False
    assert "not JSON" in result["error"]
    assert result["text"] == "not json"


def test_claude_400_drops_thinking_and_output_config_once():
    transport = FakeTransport(
        (400, json.dumps({"error": {"message": "thinking.type: unexpected value"}})),
        (200, claude_body("plain answer")),
    )

    result = claude(transport).complete(system="s", user="u")

    assert result["ok"] is True
    assert result["text"] == "plain answer"
    assert "thinking" in transport.calls[0]["payload"]
    assert "thinking" not in transport.calls[1]["payload"]
    assert "output_config" not in transport.calls[1]["payload"]


# ---------------------------------------------------------------------------
# SDK path (faked - no SDK is installed and none is imported)
# ---------------------------------------------------------------------------


class FakeOpenAIClient:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = self
        self.completions = self

    def create(self, **payload):
        self.payload = payload
        return {"choices": [{"message": {"content": "from the sdk"}}]}


def test_openai_uses_an_injected_sdk_client_when_one_exists():
    transport = FakeTransport()  # must never be called
    provider = openai(transport, sdk_client_factory=FakeOpenAIClient)

    result = provider.complete(system="s", user="u")

    assert result["ok"] is True
    assert result["text"] == "from the sdk"
    assert transport.calls == []


def test_sdk_errors_degrade_instead_of_raising():
    class Exploding:
        def __init__(self, **kwargs):
            self.chat = self
            self.completions = self

        def create(self, **payload):
            raise RuntimeError("sdk exploded")

    provider = openai(FakeTransport(), sdk_client_factory=Exploding)

    result = provider.complete(system="s", user="u")

    assert result["ok"] is False
    assert "sdk exploded" in result["error"]


# ---------------------------------------------------------------------------
# feature helper
# ---------------------------------------------------------------------------


def test_feature_unavailable_is_the_shared_no_key_body():
    body = ai_provider.feature_unavailable()

    assert body["available"] is False
    assert "OPENAI_API_KEY" in body["reason"]
    assert body["provider"] is None
    assert body["generatedAt"].endswith("Z")
