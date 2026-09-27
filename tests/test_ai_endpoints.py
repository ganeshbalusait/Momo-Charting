"""The /api/ai/* serving layer: cached, non-blocking, and never a 500.

The state that matters here is the one the trader is actually in TODAY - no
OpenAI key, no Anthropic key, neither SDK installed. Every one of these routes
has to come back with a readable sentence in that state, instantly, without
raising. These tests run with both keys explicitly removed from the
environment so they keep testing that even on a machine where a key is set.
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import sys
import threading
import time

import pytest

from agents import ai_endpoints

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]

AI_ROUTES = (
    "/api/ai/status",
    "/api/ai/triage",
    "/api/ai/brief",
    "/api/ai/journal-lessons",
    "/api/ai/catalyst",
)


@pytest.fixture(autouse=True)
def clean_cache_and_env(monkeypatch):
    """No keys, no cached answers, no builds left running between tests."""
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "AGX_AI_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    ai_endpoints.invalidate()
    with ai_endpoints._LOCK:
        ai_endpoints._BUILDING.clear()
    yield
    ai_endpoints.invalidate()
    with ai_endpoints._LOCK:
        ai_endpoints._BUILDING.clear()


def _read(name: str) -> str:
    with io.open(REPO_ROOT / name, "r", encoding="utf-8", newline="") as handle:
        return handle.read()


# ------------------------------------------------------------------ status --


def test_status_with_no_key_says_what_to_do_about_it():
    payload = ai_endpoints.status()
    assert payload["configured"] is False
    assert payload["provider"] is None
    assert "OPENAI_API_KEY" in payload["message"]
    assert ".env" in payload["message"]


def test_status_survives_a_broken_ai_provider_import(monkeypatch):
    # A None entry in sys.modules makes `from agents.ai_provider import ...`
    # raise ImportError, which is the shape a half-installed tree has.
    monkeypatch.setitem(sys.modules, "agents.ai_provider", None)
    payload = ai_endpoints.status()
    assert payload["configured"] is False
    assert "could not be loaded" in payload["message"]


def test_status_survives_provider_status_blowing_up(monkeypatch):
    import agents.ai_provider as provider_module

    def explode(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(provider_module, "provider_status", explode)
    payload = ai_endpoints.status()
    assert payload["configured"] is False
    assert "boom" in payload["message"]


# ------------------------------------------------------------------- cache --


def test_a_second_call_inside_the_ttl_does_not_run_the_builder_again():
    calls = []

    def builder():
        calls.append(1)
        return {"available": True, "reason": "", "provider": None}

    first = ai_endpoints.cached("unit:reuse", builder)
    second = ai_endpoints.cached("unit:reuse", builder)
    assert first["available"] is True
    assert second["available"] is True
    assert len(calls) == 1, "a poll must not buy a second model call"


def test_the_cached_copy_says_how_old_it_is():
    payload = ai_endpoints.cached("unit:age", lambda: {"available": True, "reason": ""})
    assert "cacheAgeSeconds" in payload
    assert payload["cacheAgeSeconds"] >= 0.0
    assert "stale" not in payload


def test_an_expired_copy_is_still_served_and_marked_stale():
    calls = []

    def builder():
        calls.append(1)
        # The refresh never finishes during the test, so the only thing the
        # route can return is last time's answer.
        if len(calls) > 1:
            time.sleep(5.0)
        return {"available": True, "reason": "first"}

    ai_endpoints.cached("unit:stale", builder, ttl=300.0)
    payload = ai_endpoints.cached("unit:stale", builder, ttl=0.0, wait=0.05)
    assert payload["reason"] == "first"
    assert payload["stale"] is True


def test_a_builder_that_raises_becomes_a_readable_sentence_not_a_500():
    def builder():
        raise ValueError("no data")

    payload = ai_endpoints.cached("unit:boom", builder)
    assert payload["available"] is False
    assert "ValueError" in payload["reason"]
    assert "no data" in payload["reason"]


def test_a_builder_that_returns_junk_becomes_unavailable():
    payload = ai_endpoints.cached("unit:junk", lambda: "not a dict")
    assert payload["available"] is False
    assert payload["reason"]


def test_a_slow_builder_does_not_hold_the_request_thread():
    started = threading.Event()

    def builder():
        started.set()
        time.sleep(3.0)
        return {"available": True, "reason": ""}

    began = time.monotonic()
    payload = ai_endpoints.cached("unit:slow", builder, wait=0.05)
    elapsed = time.monotonic() - began
    assert elapsed < 1.0, f"request thread was held for {elapsed:.2f}s"
    assert started.wait(2.0), "the build should still be running in the background"
    assert payload["available"] is False
    assert payload["pending"] is True
    assert "moment" in payload["reason"] or "seconds" in payload["reason"]


def test_only_one_build_runs_per_key_even_under_concurrent_polls():
    calls = []
    release = threading.Event()

    def builder():
        calls.append(1)
        release.wait(2.0)
        return {"available": True, "reason": ""}

    threads = [
        threading.Thread(target=ai_endpoints.cached, args=("unit:once", builder), kwargs={"wait": 0.0})
        for _ in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(3.0)
    release.set()
    time.sleep(0.2)
    assert len(calls) == 1


# ---------------------------------------------------------------- features --


def test_triage_with_no_key_and_a_real_board_is_unavailable_not_broken():
    board = {"rows": [{"symbol": "NVDA", "scanPass": True, "pctChange": 3.1}]}
    payload = ai_endpoints.triage(lambda: board)
    assert payload["available"] is False
    assert "OPENAI_API_KEY" in payload["reason"]


def test_triage_with_a_board_loader_that_raises_is_still_a_sentence():
    def loader():
        raise RuntimeError("scanner is down")

    payload = ai_endpoints.triage(loader)
    assert payload["available"] is False
    assert "scanner is down" in payload["reason"]


def test_brief_with_no_key_is_unavailable_and_names_the_reason():
    briefing = {"status": "READY", "lines": ["**NVDA** is up 3.1%."]}
    payload = ai_endpoints.brief(lambda: briefing, lambda: {"rows": []})
    assert payload["available"] is False
    assert payload["reason"]


def test_brief_does_not_touch_the_provider_when_the_briefing_is_waiting():
    payload = ai_endpoints.brief(lambda: {"status": "WAITING", "lines": []}, lambda: {})
    assert payload["available"] is False
    assert "briefing" in payload["reason"].lower()


def test_journal_lessons_with_no_key_still_returns_the_measured_stats_shape():
    payload = ai_endpoints.journal_lessons()
    assert payload["available"] in (True, False)
    assert isinstance(payload["reason"], str) and payload["reason"]


def test_journal_lessons_with_a_missing_database_does_not_raise():
    payload = ai_endpoints.journal_lessons(db_path=str(REPO_ROOT / "no-such-file.db"))
    assert isinstance(payload, dict)
    assert isinstance(payload["reason"], str) and payload["reason"]


def test_catalyst_without_a_symbol_says_how_to_call_it():
    payload = ai_endpoints.catalyst("")
    assert payload["available"] is False
    assert "symbol" in payload["reason"].lower()


def test_catalyst_with_no_key_is_unavailable_and_never_fetches_news():
    fetched = []

    def loader(symbol):
        fetched.append(symbol)
        return [{"title": "NVDA beats", "url": "https://x", "ageMinutes": 5}]

    payload = ai_endpoints.catalyst("nvda", change_pct=4.2, headlines_loader=loader)
    assert payload["available"] is False
    assert "OPENAI_API_KEY" in payload["reason"]
    assert fetched == [], "no key means there is nothing to explain; do not call the feed"


def test_every_no_key_body_is_json_serialisable_and_carries_a_reason():
    bodies = [
        ai_endpoints.triage(lambda: {"rows": []}),
        ai_endpoints.brief(lambda: {"status": "READY", "lines": ["x"]}, lambda: {}),
        ai_endpoints.journal_lessons(),
        ai_endpoints.catalyst("NVDA", change_pct=1.0, headlines_loader=lambda _s: []),
    ]
    for body in bodies:
        json.dumps(body)  # must not raise
        assert isinstance(body["available"], bool)
        assert isinstance(body["reason"], str) and body["reason"].strip()
        assert "generatedAt" in body


# ------------------------------------------------------------------ wiring --


@pytest.mark.parametrize("route", AI_ROUTES)
def test_api_server_serves_every_ai_route(route):
    source = _read("api_server.py")
    assert f'"{route}"' in source, f"{route} is not wired into api_server.py"


def test_api_server_imports_the_ai_layer_lazily_and_guarded():
    source = _read("api_server.py")
    # Never at module scope: a broken agents/ import must not stop the server
    # booting, and no SDK may be imported before a request asks for one.
    assert not re.search(r"(?m)^from agents import ai_endpoints", source)
    assert not re.search(r"(?m)^import agents\.ai_endpoints", source)
    assert "from agents import ai_endpoints as _ai" in source
    block = source.split("if parsed.path.startswith(\"/api/ai/\"):", 1)[1][:800]
    assert "except Exception as exc" in block


# momx_dev_server.py is gone. It was a deliberately temporary side server that
# let the MomX board run without a market-hours restart of api_server, and it
# was deleted once api_server picked up the real /api/momx-scanner and /api/ai
# routes. There is no second server to keep in sync any more.


def test_ai_env_vars_are_documented_for_the_trader():
    source = _read(".env.example")
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "AGX_AI_PROVIDER",
    ):
        assert name in source


def test_the_ai_sdks_stay_optional_in_requirements():
    source = _read("requirements.txt")
    # Present as documentation, commented out: `pip install -r` must not start
    # pulling vendor SDKs in for an app that talks plain HTTPS.
    assert re.search(r"(?m)^#\s*openai", source)
    assert re.search(r"(?m)^#\s*anthropic", source)
    assert not re.search(r"(?m)^openai", source)
    assert not re.search(r"(?m)^anthropic", source)
