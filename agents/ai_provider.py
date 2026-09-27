"""One interface in front of every LLM this app talks to.

The trader has free OpenAI access and pays for Claude, so OpenAI is the default
and Claude is the switch. Feature modules never import an SDK: they ask for a
provider here, call ``complete()``, and render whatever comes back.

Three rules this module exists to enforce:

* **No SDK at import time.** Importing this module must not import ``openai`` or
  ``anthropic``. Both are optional and neither is installed today. The OpenAI
  adapter falls back to a plain urllib call (the same shape as the house idiom
  in ``llm_trade_advisor._openai_review``); the Claude adapter does too.
* **Never raise.** A missing key, a dead network, an HTTP 500, or a model that
  answers with prose instead of JSON all come back as ``ok=False`` with an error
  a human can read. A trading UI must not blow up because a model hiccuped.
* **Never hang.** 20 second timeout, and at most two HTTP calls per
  ``complete()``. Timeouts are deliberately *not* retried - waiting 40 seconds
  is worse than saying "timed out" in 20.

Environment:

===========================  ==================================================
``OPENAI_API_KEY``           enables the OpenAI adapter
``OPENAI_BASE_URL``          default ``https://api.openai.com/v1``
``OPENAI_MODEL``             default ``gpt-4.1-mini``
``ANTHROPIC_API_KEY``        enables the Claude adapter
``ANTHROPIC_MODEL``          default ``claude-opus-5``
``AGX_AI_PROVIDER``          ``openai`` | ``claude``; overrides the default order
``AGX_AI_TIMEOUT_SECONDS``   default ``20``
``AGX_AI_EFFORT``            Claude reasoning effort, default ``medium``
===========================  ==================================================
"""

from __future__ import annotations

import json
import os
import socket
from datetime import datetime, timezone
from typing import Any, Callable, NamedTuple, Protocol, runtime_checkable
from urllib import error as urllib_error
from urllib import request as urllib_request

try:  # pragma: no cover - .env loading is environmental, not logic
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass


DEFAULT_TIMEOUT_SECONDS = 20.0

OPENAI_DEFAULT_BASE_URL = "https://api.openai.com/v1"
OPENAI_DEFAULT_MODEL = "gpt-4.1-mini"

CLAUDE_DEFAULT_MODEL = "claude-opus-5"
CLAUDE_MIN_MAX_TOKENS = 16000
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"

#: Server-side refusal fallback: if the model declines (a safety classifier
#: false positive on, say, a biotech headline), the API re-runs the request on
#: a fallback model inside the same call instead of returning nothing. Only the
#: models documented to take it get it; everything else is sent unchanged.
CLAUDE_FALLBACK_BETA = "server-side-fallback-2026-07-01"
_CLAUDE_FALLBACK_MODELS = frozenset({"claude-opus-5", "claude-fable-5-1"})

#: Free before paid: OpenAI costs this trader nothing, so it wins ties.
DEFAULT_PROVIDER_ORDER = ("openai", "claude")

_PROVIDER_ALIASES = {
    "openai": "openai",
    "oai": "openai",
    "gpt": "openai",
    "chatgpt": "openai",
    "claude": "claude",
    "anthropic": "claude",
    "opus": "claude",
}

_ENV_KEY_BY_PROVIDER = {"openai": "OPENAI_API_KEY", "claude": "ANTHROPIC_API_KEY"}
_LABEL_BY_PROVIDER = {"openai": "OpenAI", "claude": "Claude"}

#: Worth one more shot. Timeouts are absent on purpose - see module docstring.
_TRANSIENT_STATUS = frozenset({409, 425, 429, 500, 502, 503, 504})

_MAX_CALLS_PER_COMPLETION = 2


# ---------------------------------------------------------------------------
# public shapes
# ---------------------------------------------------------------------------


@runtime_checkable
class AIProvider(Protocol):
    """What every feature module is allowed to assume about a model."""

    name: str

    def available(self) -> bool:
        """True when a usable key is configured for this provider."""

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2000,
        schema: dict | None = None,
    ) -> dict:
        """Run one completion. Returns the result dict; never raises."""


class _Failure(NamedTuple):
    """A transport-level failure (no HTTP status was ever received)."""

    message: str
    transient: bool


#: ``transport(url, headers=..., body=..., timeout=...) -> (status, text)``.
#: Injected by tests so nothing in this module ever opens a socket under pytest.
Transport = Callable[..., "tuple[int, str]"]


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------


def utc_now_iso() -> str:
    """The ``generatedAt`` stamp every feature module reports."""

    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _result(
    *,
    ok: bool,
    provider: str,
    model: str,
    text: str = "",
    data: dict | None = None,
    error: str = "",
) -> dict:
    return {
        "ok": bool(ok),
        "text": text or "",
        "data": data,
        "error": error or "",
        "provider": provider,
        "model": model,
    }


def _clip(value: Any, limit: int = 400) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _timeout_seconds(explicit: float | None = None) -> float:
    if explicit is not None:
        try:
            return max(float(explicit), 0.5)
        except (TypeError, ValueError):
            pass
    try:
        return max(float(_env("AGX_AI_TIMEOUT_SECONDS") or DEFAULT_TIMEOUT_SECONDS), 0.5)
    except (TypeError, ValueError):
        return DEFAULT_TIMEOUT_SECONDS


def _describe_exception(exc: BaseException, timeout: float) -> _Failure:
    if isinstance(exc, (socket.timeout, TimeoutError)):
        return _Failure(f"timed out after {timeout:g}s", transient=False)
    if isinstance(exc, urllib_error.URLError):
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            return _Failure(f"timed out after {timeout:g}s", transient=False)
        return _Failure(f"could not reach the API ({_clip(reason, 160)})", transient=True)
    if isinstance(exc, (ConnectionError, OSError)):
        return _Failure(f"could not reach the API ({_clip(exc, 160)})", transient=True)
    return _Failure(f"{type(exc).__name__}: {_clip(exc, 160)}", transient=False)


def _api_error_message(body_text: str) -> str:
    """Pull the human-readable bit out of an OpenAI/Anthropic error envelope."""

    try:
        parsed = json.loads(body_text)
    except Exception:
        return _clip(body_text, 240)
    if isinstance(parsed, dict):
        err = parsed.get("error")
        if isinstance(err, dict):
            return _clip(err.get("message") or err.get("type") or body_text, 240)
        if isinstance(err, str):
            return _clip(err, 240)
        if isinstance(parsed.get("message"), str):
            return _clip(parsed["message"], 240)
    return _clip(body_text, 240)


def _schema_instruction(schema: dict) -> str:
    return (
        "Respond with a single JSON object and nothing else: no prose, no markdown "
        "fences, no commentary. It must satisfy this JSON Schema:\n"
        + _clip(json.dumps(schema, ensure_ascii=False, default=str), 4000)
    )


def _system_prompt(system: str, schema: dict | None) -> str:
    base = (system or "").strip()
    if not schema:
        return base
    return (base + "\n\n" + _schema_instruction(schema)).strip()


def parse_json_payload(text: str) -> dict | None:
    """Best-effort JSON out of a model answer. ``None`` when it is not JSON.

    Models fence their JSON, or prefix it with "Here you go:". A trading panel
    should not go blank over that, so we strip fences and fall back to the
    outermost brace/bracket span. A top-level array comes back wrapped as
    ``{"items": [...]}`` so callers always get a dict.
    """

    raw = (text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1] if raw.count("```") >= 2 else raw.lstrip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    candidates = [raw]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = raw.find(opener), raw.rfind(closer)
        if 0 <= start < end:
            candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception:
            continue
        if isinstance(parsed, dict):
            return parsed
        if isinstance(parsed, list):
            return {"items": parsed}
    return None


def _urllib_transport(url: str, *, headers: dict, body: bytes, timeout: float) -> tuple[int, str]:
    """Default transport: the same urllib shape ``llm_trade_advisor`` uses."""

    req = urllib_request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            status = int(getattr(response, "status", None) or getattr(response, "code", 200) or 200)
            return status, response.read().decode("utf-8", "replace")
    except urllib_error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", "replace")
        except Exception:
            payload = ""
        return int(exc.code), payload


# ---------------------------------------------------------------------------
# base adapter
# ---------------------------------------------------------------------------


class _BaseProvider:
    name = ""
    _env_key = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        timeout: float | None = None,
        transport: Transport | None = None,
        prefer_sdk: bool = True,
        sdk_client_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.api_key = (api_key if api_key is not None else _env(self._env_key)) or ""
        self.timeout = _timeout_seconds(timeout)
        self._transport = transport
        self._sdk_client_factory = sdk_client_factory
        # An injected transport means "use HTTP and only HTTP" - that is how the
        # tests keep every code path off the network.
        self._prefer_sdk = bool(prefer_sdk) and (transport is None or sdk_client_factory is not None)
        self.model = (model or "").strip() or self._default_model()

    def _default_model(self) -> str:  # pragma: no cover - overridden
        return ""

    def available(self) -> bool:
        return bool(self.api_key)

    @property
    def label(self) -> str:
        return _LABEL_BY_PROVIDER.get(self.name, self.name)

    def _not_configured(self) -> dict:
        return _result(
            ok=False,
            provider=self.name,
            model=self.model,
            error=f"No {self.label} key set. Add {self._env_key} to .env.",
        )

    def _post(self, url: str, headers: dict, payload: dict) -> tuple[int, str, _Failure | None]:
        transport = self._transport or _urllib_transport
        body = json.dumps(payload, default=str).encode("utf-8")
        try:
            status, text = transport(url, headers=headers, body=body, timeout=self.timeout)
        except Exception as exc:
            return 0, "", _describe_exception(exc, self.timeout)
        return int(status or 0), text or "", None

    def _finish(self, text: str, schema: dict | None) -> dict:
        """Common tail: succeed, or fail with the raw text kept for debugging."""

        if not (text or "").strip():
            return _result(
                ok=False,
                provider=self.name,
                model=self.model,
                text=text,
                error=f"{self.label} returned an empty response.",
            )
        if schema is None:
            return _result(ok=True, provider=self.name, model=self.model, text=text)
        data = parse_json_payload(text)
        if data is None:
            return _result(
                ok=False,
                provider=self.name,
                model=self.model,
                text=text,
                error=f"{self.label} did not return valid JSON. Raw reply kept in 'text'.",
            )
        return _result(ok=True, provider=self.name, model=self.model, text=text, data=data)

    def _envelope_error(self, body_text: str) -> dict:
        return _result(
            ok=False,
            provider=self.name,
            model=self.model,
            text=body_text,
            error=f"{self.label} returned a response that was not JSON: {_clip(body_text, 160)}",
        )

    def _http_error(self, status: int, body_text: str) -> dict:
        return _result(
            ok=False,
            provider=self.name,
            model=self.model,
            text=body_text,
            error=f"{self.label} HTTP {status}: {_api_error_message(body_text)}",
        )

    def _transport_error(self, failure: _Failure) -> dict:
        return _result(
            ok=False,
            provider=self.name,
            model=self.model,
            error=f"{self.label} request failed: {failure.message}",
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------

#: Reasoning-family models reject ``max_tokens`` and ``temperature``.
_OPENAI_REASONING_PREFIXES = ("o1", "o3", "o4", "gpt-5")


class OpenAIProvider(_BaseProvider):
    """The default. Uses the ``openai`` package when installed, urllib when not."""

    name = "openai"
    _env_key = "OPENAI_API_KEY"

    def __init__(self, *, base_url: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        raw_base = (base_url or _env("OPENAI_BASE_URL") or OPENAI_DEFAULT_BASE_URL).rstrip("/")
        self.base_url = raw_base or OPENAI_DEFAULT_BASE_URL

    def _default_model(self) -> str:
        return _env("OPENAI_MODEL") or OPENAI_DEFAULT_MODEL

    # -- request building ---------------------------------------------------

    def _is_reasoning_model(self) -> bool:
        model = (self.model or "").lower()
        return any(model.startswith(prefix) for prefix in _OPENAI_REASONING_PREFIXES)

    def build_payload(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict | None,
        compat: bool = False,
    ) -> dict:
        """``compat=True`` is the one-retry shape for a 400 on an odd model/proxy."""

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": _system_prompt(system, schema)},
                {"role": "user", "content": user},
            ],
        }
        token_key = "max_completion_tokens" if self._is_reasoning_model() else "max_tokens"
        if compat:
            token_key = "max_tokens" if token_key == "max_completion_tokens" else "max_completion_tokens"
        payload[token_key] = max(int(max_tokens or 0), 256)
        # Hidden-thinking models (Gemini 3.x via the OpenAI-compatible
        # endpoint) burn the completion budget on internal reasoning and then
        # ship "{}" - measured live 2026-08-30 with gemini-3.6-flash: 2000
        # max_tokens produced an empty object and a 20s+ latency. Their compat
        # layer accepts reasoning_effort; "low" answers our structured prompts
        # directly. Env-tunable, harmless off (unset sends nothing), and
        # dropped on the compat retry in case a strict proxy 400s on it.
        effort = _env("OPENAI_REASONING_EFFORT")
        if effort and not compat:
            payload["reasoning_effort"] = effort
        if not self._is_reasoning_model() and not compat:
            payload["temperature"] = 0.1
        if schema is not None:
            if compat:
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "agx_response", "strict": False, "schema": schema},
                }
        return payload

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    # -- transport ----------------------------------------------------------

    def _sdk_call(self, payload: dict) -> tuple[int, str, _Failure | None] | None:
        """``None`` means "SDK not usable here, fall back to HTTP"."""

        factory = self._sdk_client_factory
        if factory is None:
            try:
                import openai  # noqa: PLC0415 - lazy on purpose
            except Exception:
                return None
            factory = openai.OpenAI
        try:
            client = factory(
                api_key=self.api_key, base_url=self.base_url, timeout=self.timeout, max_retries=0
            )
        except TypeError:
            try:
                client = factory(api_key=self.api_key)
            except Exception as exc:
                return 0, "", _describe_exception(exc, self.timeout)
        except Exception as exc:
            return 0, "", _describe_exception(exc, self.timeout)
        try:
            response = client.chat.completions.create(**payload)
        except TypeError:
            return None
        except Exception as exc:
            return _sdk_exception(exc, self.timeout)
        return 200, json.dumps(_as_plain_dict(response), default=str), None

    def _send(self, payload: dict) -> tuple[int, str, _Failure | None]:
        if self._prefer_sdk:
            sdk_result = self._sdk_call(payload)
            if sdk_result is not None:
                return sdk_result
        return self._post(f"{self.base_url}/chat/completions", self._headers(), payload)

    # -- the contract -------------------------------------------------------

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2000,
        schema: dict | None = None,
    ) -> dict:
        if not self.available():
            return self._not_configured()

        compat = False
        calls = 0
        body_text = ""
        while calls < _MAX_CALLS_PER_COMPLETION:
            calls += 1
            last_call = calls >= _MAX_CALLS_PER_COMPLETION
            try:
                payload = self.build_payload(
                    system=system, user=user, max_tokens=max_tokens, schema=schema, compat=compat
                )
                status, body_text, failure = self._send(payload)
            except Exception as exc:  # defence in depth: complete() never raises
                return self._transport_error(_describe_exception(exc, self.timeout))

            if failure is not None:
                if failure.transient and not last_call:
                    continue
                return self._transport_error(failure)
            if status == 200:
                break
            if status == 400 and not compat and not last_call:
                compat = True  # bad response_format / wrong token param / no temperature
                continue
            if status in _TRANSIENT_STATUS and not last_call:
                continue
            return self._http_error(status, body_text)

        try:
            raw = json.loads(body_text)
        except Exception:
            return self._envelope_error(body_text)
        if not isinstance(raw, dict):
            return self._envelope_error(body_text)
        return self._finish(_openai_text(raw), schema)


def _openai_text(raw: dict) -> str:
    choices = raw.get("choices")
    if not isinstance(choices, list) or not choices:
        return ""
    first = choices[0]
    message = first.get("message") if isinstance(first, dict) else None
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):  # newer part-shaped content
        parts = [
            part.get("text", "")
            for part in content
            if isinstance(part, dict) and part.get("type") in {"text", "output_text", None}
        ]
        return "".join(str(part) for part in parts)
    return ""


# ---------------------------------------------------------------------------
# Claude
# ---------------------------------------------------------------------------


class ClaudeProvider(_BaseProvider):
    """Anthropic adapter. SDK when installed, urllib Messages API when not.

    Current-API details that are easy to get wrong and cost a 400:

    * thinking is ``{"type": "adaptive"}``; the old
      ``{"type": "enabled", "budget_tokens": N}`` is rejected on this model.
    * effort lives in ``output_config``, not at the top level.
    * structured output is ``output_config={"format": {...}}``; the old
      ``output_format`` parameter is deprecated.
    * ``max_tokens`` also covers thinking tokens, so it is floored at 16000.
    """

    name = "claude"
    _env_key = "ANTHROPIC_API_KEY"

    def __init__(self, *, base_url: str | None = None, effort: str | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.base_url = (base_url or _env("ANTHROPIC_BASE_URL") or ANTHROPIC_DEFAULT_BASE_URL).rstrip("/")
        self.effort = (effort or _env("AGX_AI_EFFORT") or "medium").lower()

    def _default_model(self) -> str:
        return _env("ANTHROPIC_MODEL") or CLAUDE_DEFAULT_MODEL

    def build_payload(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        schema: dict | None,
        compat: bool = False,
    ) -> dict:
        """``compat=True`` drops thinking/output_config after a 400."""

        payload: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max(int(max_tokens or 0), CLAUDE_MIN_MAX_TOKENS),
            "system": _system_prompt(system, schema),
            "messages": [{"role": "user", "content": user}],
        }
        if compat:
            return payload
        payload["thinking"] = {"type": "adaptive"}
        output_config: dict[str, Any] = {"effort": self.effort}
        if schema is not None:
            output_config["format"] = {
                "type": "json_schema",
                "name": "agx_response",
                "schema": schema,
            }
        payload["output_config"] = output_config
        if self._uses_fallbacks():
            payload["fallbacks"] = "default"
        return payload

    def _uses_fallbacks(self) -> bool:
        return self.model in _CLAUDE_FALLBACK_MODELS

    def _headers(self) -> dict:
        headers = {
            "x-api-key": self.api_key,
            "anthropic-version": ANTHROPIC_VERSION,
            "Content-Type": "application/json",
        }
        if self._uses_fallbacks():
            headers["anthropic-beta"] = CLAUDE_FALLBACK_BETA
        return headers

    def _sdk_call(self, payload: dict) -> tuple[int, str, _Failure | None] | None:
        factory = self._sdk_client_factory
        if factory is None:
            try:
                import anthropic  # noqa: PLC0415 - lazy on purpose
            except Exception:
                return None
            factory = anthropic.Anthropic
        try:
            client = factory(api_key=self.api_key, timeout=self.timeout, max_retries=0)
        except TypeError:
            try:
                client = factory(api_key=self.api_key)
            except Exception as exc:
                return 0, "", _describe_exception(exc, self.timeout)
        except Exception as exc:
            return 0, "", _describe_exception(exc, self.timeout)
        try:
            response = client.messages.create(**payload)
        except TypeError:
            # An older SDK that has never heard of output_config / adaptive
            # thinking. The raw HTTP path speaks the current API, so use it.
            return None
        except Exception as exc:
            return _sdk_exception(exc, self.timeout)
        return 200, json.dumps(_as_plain_dict(response), default=str), None

    def _send(self, payload: dict) -> tuple[int, str, _Failure | None]:
        # The stable SDK surface has no ``fallbacks`` argument (it is a beta
        # parameter), so a request carrying it goes straight to HTTP.
        if self._prefer_sdk and "fallbacks" not in payload:
            sdk_result = self._sdk_call(payload)
            if sdk_result is not None:
                return sdk_result
        return self._post(f"{self.base_url}/v1/messages", self._headers(), payload)

    def complete(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int = 2000,
        schema: dict | None = None,
    ) -> dict:
        if not self.available():
            return self._not_configured()

        compat = False
        calls = 0
        body_text = ""
        while calls < _MAX_CALLS_PER_COMPLETION:
            calls += 1
            last_call = calls >= _MAX_CALLS_PER_COMPLETION
            try:
                payload = self.build_payload(
                    system=system, user=user, max_tokens=max_tokens, schema=schema, compat=compat
                )
                status, body_text, failure = self._send(payload)
            except Exception as exc:
                return self._transport_error(_describe_exception(exc, self.timeout))

            if failure is not None:
                if failure.transient and not last_call:
                    continue
                return self._transport_error(failure)
            if status == 200:
                break
            if status == 400 and not compat and not last_call:
                compat = True
                continue
            if status in _TRANSIENT_STATUS and not last_call:
                continue
            return self._http_error(status, body_text)

        try:
            raw = json.loads(body_text)
        except Exception:
            return self._envelope_error(body_text)
        if not isinstance(raw, dict):
            return self._envelope_error(body_text)
        if raw.get("stop_reason") == "refusal":
            # The whole chain (fallback included) declined. Say so plainly, so
            # a caller with another provider can move on to it.
            return _result(ok=False, provider=self.name, model=self.model, error=f"{self.label} declined to answer.")
        return self._finish(_anthropic_text(raw), schema)


def _anthropic_text(raw: dict) -> str:
    blocks = raw.get("content")
    if isinstance(blocks, str):
        return blocks
    if not isinstance(blocks, list):
        return ""
    chunks: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        kind = block.get("type")
        if kind in {"thinking", "redacted_thinking"}:
            continue  # reasoning is not the answer
        if kind == "text" and isinstance(block.get("text"), str):
            chunks.append(block["text"])
        elif kind in {"json", "output_json"} and block.get("json") is not None:
            chunks.append(json.dumps(block["json"], default=str))
    return "".join(chunks)


def _sdk_exception(exc: BaseException, timeout: float) -> tuple[int, str, _Failure | None]:
    """Map an SDK exception onto the same (status, body, failure) shape as HTTP."""

    status = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    body = getattr(exc, "body", None)
    if isinstance(status, int) and status:
        payload = body if body is not None else {"error": {"message": str(exc)}}
        return status, json.dumps(payload, default=str), None
    return 0, "", _describe_exception(exc, timeout)


def _as_plain_dict(response: Any) -> dict:
    """Normalise an SDK response object into the same dict the HTTP path returns."""

    for attr in ("model_dump", "to_dict", "dict"):
        method = getattr(response, attr, None)
        if callable(method):
            try:
                value = method()
            except Exception:
                continue
            if isinstance(value, dict):
                return value
    if isinstance(response, dict):
        return response
    return {}


# ---------------------------------------------------------------------------
# selection
# ---------------------------------------------------------------------------

_PROVIDER_CLASSES: dict[str, type[_BaseProvider]] = {
    "openai": OpenAIProvider,
    "claude": ClaudeProvider,
}


def normalize_provider_name(name: str | None) -> str | None:
    """``"Anthropic"`` -> ``"claude"``. Unknown names return ``None``, not an error."""

    return _PROVIDER_ALIASES.get(str(name or "").strip().lower()) or None


def provider_order(preferred: str | None = None) -> list[str]:
    """Preference order: explicit argument, then ``AGX_AI_PROVIDER``, then free-first."""

    order: list[str] = []
    for candidate in (normalize_provider_name(preferred), normalize_provider_name(_env("AGX_AI_PROVIDER"))):
        if candidate and candidate not in order:
            order.append(candidate)
    for candidate in DEFAULT_PROVIDER_ORDER:
        if candidate not in order:
            order.append(candidate)
    return order


def make_provider(name: str, **overrides: Any) -> _BaseProvider | None:
    """Build one adapter by name regardless of whether it is configured."""

    resolved = normalize_provider_name(name)
    if resolved is None:
        return None
    return _PROVIDER_CLASSES[resolved](**overrides)


def get_provider(preferred: str | None = None, **overrides: Any) -> AIProvider | None:
    """The first configured provider in preference order, or ``None``.

    An unknown ``preferred`` (or an unknown ``AGX_AI_PROVIDER``) is ignored
    rather than raising - a typo in .env must not take the app down.
    """

    for name in provider_order(preferred):
        provider = _PROVIDER_CLASSES[name](**overrides)
        if provider.available():
            return provider
    return None


class FallbackProvider:
    """Several providers behind one ``complete()``: the first that answers wins.

    Ganesh's rule (2026-09-24): "use Claude, and if the Claude credit is done
    use the free Gemini". An out-of-credit Claude key does not look different
    from any other failure until the call is made - Anthropic answers HTTP 400
    "credit balance is too low" - so the switch happens per call, on ANY
    ``ok=False``: no credit, rate limit, outage, refusal, unreadable reply.
    A provider without a key is skipped without a call. Never raises.
    """

    def __init__(self, providers: list) -> None:
        self.providers = [provider for provider in providers if provider is not None]
        self.last_errors: list[str] = []

    @property
    def name(self) -> str | None:
        active = next((p for p in self.providers if p.available()), None)
        return getattr(active, "name", None)

    def available(self) -> bool:
        return any(provider.available() for provider in self.providers)

    def complete(self, *, system: str, user: str, max_tokens: int = 2000, schema: dict | None = None) -> dict:
        self.last_errors = []
        last: dict | None = None
        for provider in self.providers:
            if not provider.available():
                continue
            try:
                result = provider.complete(system=system, user=user, max_tokens=max_tokens, schema=schema)
            except Exception as exc:  # a provider is contracted not to raise; trust nothing
                result = _result(ok=False, provider=getattr(provider, "name", "?"), model="", error=str(exc))
            if isinstance(result, dict) and result.get("ok"):
                if self.last_errors:
                    result = {**result, "fellBackFrom": list(self.last_errors)}
                return result
            last = result if isinstance(result, dict) else None
            self.last_errors.append(str((last or {}).get("error") or "failed"))
        if last is not None:
            return last
        return _result(ok=False, provider="none", model="", error="No AI key set.")


def claude_first_provider(**claude_overrides: Any) -> FallbackProvider:
    """Claude first, then the free OpenAI-compatible key (Gemini on this app).

    ``claude_overrides`` go to the Claude adapter only (a feature can ask for
    a lower ``effort`` without touching the app-wide AGX_AI_EFFORT)."""

    return FallbackProvider([ClaudeProvider(**claude_overrides), OpenAIProvider()])


def provider_status(preferred: str | None = None) -> dict:
    """What the UI shows a trader about AI configuration. Plain English only."""

    candidates = []
    for name in provider_order(preferred):
        provider = _PROVIDER_CLASSES[name]()
        candidates.append(
            {
                "name": name,
                "configured": provider.available(),
                "model": provider.model,
                "envVar": _ENV_KEY_BY_PROVIDER[name],
            }
        )

    active = next((item for item in candidates if item["configured"]), None)
    if active is None:
        return {
            "configured": False,
            "provider": None,
            "model": None,
            "candidates": candidates,
            "message": (
                "No AI key set. Add OPENAI_API_KEY to .env to turn on AI features "
                "(OpenAI is free on your account), or ANTHROPIC_API_KEY to use Claude "
                "instead. Restart the app after saving."
            ),
        }

    label = _LABEL_BY_PROVIDER[active["name"]]
    other = next((item for item in candidates if item["name"] != active["name"]), None)
    message = f"AI is on, using {label} ({active['model']})."
    if other is not None:
        other_label = _LABEL_BY_PROVIDER[other["name"]]
        if other["configured"]:
            message += f" Set AGX_AI_PROVIDER={other['name']} in .env to switch to {other_label}."
        else:
            message += f" Add {other['envVar']} to .env if you also want {other_label}."
    return {
        "configured": True,
        "provider": active["name"],
        "model": active["model"],
        "candidates": candidates,
        "message": message,
    }


def feature_unavailable(reason: str | None = None, provider: str | None = None) -> dict:
    """The standard 'AI not configured' body every feature module returns."""

    if not reason:
        reason = provider_status()["message"]
    return {
        "available": False,
        "reason": reason,
        "generatedAt": utc_now_iso(),
        "provider": provider,
    }


__all__ = [
    "AIProvider",
    "ClaudeProvider",
    "FallbackProvider",
    "claude_first_provider",
    "OpenAIProvider",
    "feature_unavailable",
    "get_provider",
    "make_provider",
    "normalize_provider_name",
    "parse_json_payload",
    "provider_order",
    "provider_status",
    "utc_now_iso",
]
