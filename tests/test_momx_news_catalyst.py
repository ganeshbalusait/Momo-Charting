"""The AI news reader on the scanner row (momx/news_catalyst.py) and the
Claude-first, Gemini-fallback provider chain it runs on."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from agents import ai_provider, catalyst_explainer
from momx import news, news_catalyst

ET = ZoneInfo("America/New_York")
AT_940 = datetime(2026, 9, 24, 9, 40, tzinfo=ET)  # a Thursday


def row(symbol="META", letter="A+", headline="Meta beats on revenue", pct=2.1):
    return {"symbol": symbol, "grade": {"letter": letter}, "pctChange": pct,
            "news": {"headline": headline} if headline else None}


class Fakes:
    def __init__(self, verdict=None):
        self.head_calls, self.explain_calls = [], []
        self.verdict = verdict or {"available": True, "category": "EARNINGS", "direction": "bullish",
                                   "confidence": "high", "summary": "Revenue beat lifted the stock.",
                                   "headline": {"title": "Meta beats on revenue"}, "provider": "claude"}

    def headlines(self, symbol):
        self.head_calls.append(symbol)
        return [{"headline": "Meta beats on revenue"}]

    def explain(self, symbol, pct, headlines):
        self.explain_calls.append((symbol, pct))
        return self.verdict


def book(tmp_path, fakes):
    return news_catalyst.CatalystBook(tmp_path, headlines=fakes.headlines, explain=fakes.explain,
                                      background=False, always_on=True)


def test_reads_an_a_plus_with_news_and_stamps_the_row(tmp_path):
    fakes = Fakes()
    payload = {"rows": [row()]}
    book(tmp_path, fakes).apply("Watchlist", payload, AT_940)
    cat = payload["rows"][0]["catalyst"]
    assert cat["category"] == "EARNINGS" and cat["direction"] == "bullish"
    assert cat["headline"] == "Meta beats on revenue" and cat["provider"] == "claude"
    assert fakes.explain_calls == [("META", 2.1)]


def test_only_a_plus_or_a_with_a_headline_is_read(tmp_path):
    fakes = Fakes()
    payload = {"rows": [row("B1", letter="B"), row("NONEWS", headline=None), row("NOGRADE", letter=None)]}
    b = book(tmp_path, fakes)
    for _ in range(3):
        b.apply("Watchlist", payload, AT_940)
    assert fakes.explain_calls == []
    assert all(r["catalyst"] is None for r in payload["rows"])


def test_one_read_per_headline_and_a_new_headline_waits_20_minutes(tmp_path):
    fakes = Fakes()
    b = book(tmp_path, fakes)
    b.apply("Watchlist", {"rows": [row()]}, AT_940)
    b.apply("Watchlist", {"rows": [row()]}, AT_940 + timedelta(minutes=5))
    assert len(fakes.explain_calls) == 1, "same headline: never re-read"
    b.apply("Watchlist", {"rows": [row(headline="Meta raises guidance")]}, AT_940 + timedelta(minutes=10))
    assert len(fakes.explain_calls) == 1, "new headline too soon"
    b.apply("Watchlist", {"rows": [row(headline="Meta raises guidance")]}, AT_940 + timedelta(minutes=21))
    assert len(fakes.explain_calls) == 2


def test_daily_cap_stops_spending(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_CATALYST_MAX_CALLS", "2")
    fakes = Fakes()
    b = book(tmp_path, fakes)
    for i, sym in enumerate(["A1", "A2", "A3", "A4"]):
        b.apply("Watchlist", {"rows": [row(sym)]}, AT_940 + timedelta(minutes=i))
    assert len(fakes.explain_calls) == 2


def test_the_cap_survives_a_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_CATALYST_MAX_CALLS", "1")
    book(tmp_path, Fakes()).apply("Watchlist", {"rows": [row("A1")]}, AT_940)
    again = Fakes()
    payload = {"rows": [row("A1"), row("A2")]}
    book(tmp_path, again).apply("Watchlist", payload, AT_940 + timedelta(minutes=1))
    assert again.explain_calls == [], "a restarted worker must not re-spend the day"
    assert payload["rows"][0]["catalyst"]["category"] == "EARNINGS", "the saved read is still shown"


def test_outside_the_window_and_on_weekends_nothing_is_read(tmp_path):
    fakes = Fakes()
    b = book(tmp_path, fakes)
    b.apply("Watchlist", {"rows": [row()]}, datetime(2026, 9, 24, 17, 0, tzinfo=ET))
    b.apply("Watchlist", {"rows": [row()]}, datetime(2026, 9, 26, 10, 0, tzinfo=ET))  # Saturday
    assert fakes.explain_calls == []


def test_a_failed_read_stamps_nothing_and_retries_later(tmp_path):
    fakes = Fakes(verdict={"available": False, "reason": "Claude HTTP 400: credit balance is too low"})
    b = book(tmp_path, fakes)
    payload = {"rows": [row()]}
    b.apply("Watchlist", payload, AT_940)
    assert payload["rows"][0]["catalyst"] is None, "a failure must never look like 'no news'"
    b.apply("Watchlist", {"rows": [row()]}, AT_940 + timedelta(minutes=5))
    assert len(fakes.explain_calls) == 1
    b.apply("Watchlist", {"rows": [row()]}, AT_940 + timedelta(minutes=21))
    assert len(fakes.explain_calls) == 2


def test_the_env_switch_turns_the_default_reader_off(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_CATALYST_READER", "0")
    fakes = Fakes()
    b = news_catalyst.CatalystBook(tmp_path, headlines=fakes.headlines, explain=fakes.explain, background=False)
    b.apply("Watchlist", {"rows": [row()]}, AT_940)
    assert fakes.explain_calls == []


# ------------------------------------------------------------ provider chain


class Stub:
    def __init__(self, name, ok=True, available=True):
        self.name, self._ok, self._available, self.calls = name, ok, available, 0

    def available(self):
        return self._available

    def complete(self, **kwargs):
        self.calls += 1
        if self._ok:
            return {"ok": True, "text": "{}", "data": {}, "error": "", "provider": self.name, "model": "m"}
        return {"ok": False, "text": "", "data": None, "error": f"{self.name} HTTP 400: credit balance is too low",
                "provider": self.name, "model": "m"}


def test_claude_answers_when_it_can():
    claude, gemini = Stub("claude"), Stub("openai")
    result = ai_provider.FallbackProvider([claude, gemini]).complete(system="s", user="u")
    assert result["provider"] == "claude" and gemini.calls == 0


def test_out_of_credit_claude_falls_back_to_gemini():
    claude, gemini = Stub("claude", ok=False), Stub("openai")
    result = ai_provider.FallbackProvider([claude, gemini]).complete(system="s", user="u")
    assert result["ok"] and result["provider"] == "openai"
    assert "credit balance" in result["fellBackFrom"][0]


def test_no_claude_key_goes_straight_to_gemini_without_a_call():
    claude, gemini = Stub("claude", available=False), Stub("openai")
    chain = ai_provider.FallbackProvider([claude, gemini])
    assert chain.name == "openai"
    assert chain.complete(system="s", user="u")["provider"] == "openai" and claude.calls == 0


def test_both_failing_returns_the_last_error():
    chain = ai_provider.FallbackProvider([Stub("claude", ok=False), Stub("openai", ok=False)])
    result = chain.complete(system="s", user="u")
    assert not result["ok"] and "openai" in result["error"]


def test_opus_5_requests_carry_the_refusal_fallback_and_the_beta_header():
    p = ai_provider.ClaudeProvider(api_key="k", model="claude-opus-5")
    payload = p.build_payload(system="s", user="u", max_tokens=100, schema=None)
    assert payload["fallbacks"] == "default"
    assert p._headers()["anthropic-beta"] == ai_provider.CLAUDE_FALLBACK_BETA
    assert "fallbacks" not in p.build_payload(system="s", user="u", max_tokens=100, schema=None, compat=True)
    other = ai_provider.ClaudeProvider(api_key="k", model="claude-sonnet-5")
    assert "fallbacks" not in other.build_payload(system="s", user="u", max_tokens=100, schema=None)
    assert "anthropic-beta" not in other._headers()


def test_a_refusal_is_a_failure_so_the_chain_moves_on():
    body = '{"stop_reason": "refusal", "content": []}'
    p = ai_provider.ClaudeProvider(api_key="k", model="claude-opus-5",
                                   transport=lambda url, **kw: (200, body))
    assert p.complete(system="s", user="u")["ok"] is False


# ------------------------------------------------------------ explainer direction


def test_explainer_reports_direction_and_the_provider_that_answered():
    class Chain:
        name = "claude"

        def available(self):
            return True

        def complete(self, **kw):
            return {"ok": True, "provider": "openai", "data": {
                "summary": "The company priced a stock offering.", "category": "OFFERING",
                "direction": "bearish", "confidence": "high", "headlineIndex": 1}}

    out = catalyst_explainer.explain("XYZ", 5.0, [{"title": "XYZ prices $200M stock offering"}], provider=Chain())
    assert out["category"] == "OFFERING" and out["direction"] == "bearish" and out["provider"] == "openai"


def test_unknown_is_always_neutral():
    class Chain:
        name = "claude"

        def available(self):
            return True

        def complete(self, **kw):
            return {"ok": True, "data": {"summary": "No news explains this move.", "category": "UNKNOWN",
                                         "direction": "bullish", "confidence": "high"}}

    out = catalyst_explainer.explain("XYZ", 5.0, [{"title": "Market wrap"}], provider=Chain())
    assert out["direction"] == "neutral" and out["confidence"] == "low"


# ------------------------------------------------------------ per-ticker headlines


def test_recent_headlines_specific_first_then_newest(monkeypatch):
    now = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    articles = [
        {"headline": "10 stocks moving", "symbols": ["A", "B", "C", "D", "XYZ"], "created_at": "2026-09-24T13:50:00Z"},
        {"headline": "XYZ wins contract", "symbols": ["XYZ"], "created_at": "2026-09-24T12:00:00Z"},
        {"headline": "XYZ upgraded", "symbols": ["XYZ", "ABC"], "created_at": "2026-09-24T13:00:00Z"},
        {"headline": "Old news", "symbols": ["XYZ"], "created_at": "2026-09-22T12:00:00Z"},
        {"headline": "Other ticker", "symbols": ["ABC"], "created_at": "2026-09-24T13:30:00Z"},
    ]
    monkeypatch.setenv("MOMX_NEWS_NETWORK", "1")
    monkeypatch.setattr(news, "_fetch_articles", lambda symbols, start, get: articles)
    got = news.recent_headlines("xyz", now=now, get=lambda *a, **k: None)
    assert [g["headline"] for g in got] == ["XYZ upgraded", "XYZ wins contract", "10 stocks moving"]


def test_recent_headlines_never_raises(monkeypatch):
    monkeypatch.setenv("MOMX_NEWS_NETWORK", "1")

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr(news, "_fetch_articles", boom)
    assert news.recent_headlines("XYZ", get=lambda *a, **k: None) == []


# ------------------------------------------------------------ news first


def news_row(symbol, *, scope="specific", hours_old=2.0, letter=None, headline=None):
    at = (AT_940 - timedelta(hours=hours_old)).astimezone(timezone.utc).isoformat()
    return {"symbol": symbol, "grade": {"letter": letter}, "pctChange": 1.0,
            "news": {"headline": headline or f"{symbol} signs a deal", "at": at, "scope": scope}}


def test_news_first_an_ungraded_ticker_with_fresh_specific_news_is_read(tmp_path):
    fakes = Fakes()
    payload = {"rows": [], "rest": [news_row("GLND")]}
    book(tmp_path, fakes).apply("Watchlist", payload, AT_940)
    assert fakes.explain_calls == [("GLND", 1.0)]
    assert payload["rest"][0]["catalyst"]["category"] == "EARNINGS"


def test_round_ups_and_stale_news_are_not_read_on_their_own(tmp_path):
    fakes = Fakes()
    b = book(tmp_path, fakes)
    payload = {"rows": [news_row("WIDE", scope="market-wide"), news_row("OLD", hours_old=30)]}
    for i in range(3):
        b.apply("Watchlist", payload, AT_940 + timedelta(minutes=i))
    assert fakes.explain_calls == []


def test_a_plus_first_then_the_newest_specific_news(tmp_path):
    fakes = Fakes()
    b = book(tmp_path, fakes)
    payload = {"rows": [news_row("OLDER", hours_old=5), news_row("NEWER", hours_old=1),
                        news_row("GRADED", scope="market-wide", hours_old=10, letter="A+")]}
    for i in range(3):
        b.apply("Watchlist", payload, AT_940 + timedelta(minutes=i))
    assert [s for s, _ in fakes.explain_calls] == ["GRADED", "NEWER", "OLDER"]
