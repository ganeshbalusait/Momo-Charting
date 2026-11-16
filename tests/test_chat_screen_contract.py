"""The chat screen reads what the real route sends (2026-11-11 review fix).

The screen's report code (frontend/src/chatReport.js, botChat.js) once read a reply shape the server never sent:
the second part ({"more": {...}}) added nothing, articles and reason objects were dropped and the report's views
never showed. This asks the real POST /api/bot-chat (tests/api_harness.py via the acceptance fixtures, fake AI,
nothing leaves the PC), then runs each reply through the screen's own chatAnswer / chatMergeMore in node and checks
that headlines, reasons and the Technical card come through.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from test_momx_chat_search_acceptance import ADMIN, ROUTE, _ask, ai, app  # noqa: F401 - pytest fixtures
from momx import chat_ai, chat_limits

FRONTEND = Path(__file__).resolve().parents[1] / "frontend" / "src"
NODE = shutil.which("node")

SCRIPT = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.argv[2];
const { chatAnswer } = await import(pathToFileURL(src + "/botChat.js").href);
const { chatMergeMore, chatSections, FAST_HEADER, PENDING_LINES } = await import(pathToFileURL(src + "/chatReport.js").href);
const { technicalCard } = await import(pathToFileURL(src + "/miniEventChart.js").href);
const replies = JSON.parse(readFileSync(0, "utf8"));
const out = {};
for (const [q, { fast, more }] of Object.entries(replies)) {
  const a = chatAnswer(fast, true);
  const m = chatMergeMore(a, more, true);
  const headlines = m.headlines.length + m.sections.reduce((n, s) => n + (s.headlines?.length || 0), 0);
  const said = [...m.lines.map((l) => l.text), ...m.sections.flatMap((s) => s.lines)].join("\\n");
  const card = technicalCard(m.technical);
  // The cards the second part sent, as the screen cleans them, with any pending line taken off.
  const fresh = chatSections(more.more.sections).map((s) => [s.key, JSON.stringify(s.lines.filter((l) => !PENDING_LINES.includes(l)))]);
  out[q] = { moreState: m.moreState, added: m.lines.length - a.lines.length, headlines,
             fastLines: a.lines.map((l) => l.text), lines: m.lines.map((l) => l.text),
             fastHeaders: a.lines.filter((l) => FAST_HEADER.test(l.text)).length, fastHeaderLeft: m.lines.some((l) => FAST_HEADER.test(l.text)),
             freshSections: fresh.length,
             sectionsFromMore: fresh.every(([key, lines]) => JSON.stringify(m.sections.find((s) => s.key === key)?.lines) === lines),
             reasonShown: m.reasons.length + m.sections.reduce((n, s) => n + (s.reasons?.length || 0), 0) > 0 || /Product or business news: "/.test(said),
             aiProse: /load up|reach 200/.test(JSON.stringify(m)),
             pills: card ? card.pills.map((p) => p.key) : [], dailyBars: card?.daily?.bars.length || 0 };
}
process.stdout.write(JSON.stringify(out));
"""


def _fake_ai(system, user, schema):
    # Changed on purpose (2026-11-11 design change): the why-answer's AI returns category JSON only; the screen line is the
    # server's. A sentence the fake also sends ("text") must never reach the screen.
    if "reasons" in json.dumps(schema):
        return {"ok": True, "provider": "fake", "data": {"reasons": [{"category": "Product or business news", "headline_index": 1,
                                                                      "text": "We expect NVDA to reach 200; time to load up."}], "explained": True}}
    return {"ok": False, "error": "down"}


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_real_route_replies_come_through_the_screen(app, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(chat_ai, "_default_ask", _fake_ai)
    replies = {}
    for question in ("latest news on nvda", "why is nvda up today", "nvda full report", "should i buy nvda"):
        chat_limits.LIMITS.reset()
        fast = _ask(app, question, ADMIN)
        assert fast.get("more"), f"{question}: the fast part carries a token"
        status, more = app.api.post(ROUTE, {"more": fast["more"]}, headers=ADMIN)
        assert status == 200
        replies[question] = {"fast": fast, "more": more}
    script = tmp_path / "contract.mjs"
    script.write_text(SCRIPT, encoding="utf-8")
    run = subprocess.run([NODE, str(script), str(FRONTEND)], input=json.dumps(replies, default=str), capture_output=True,
                         text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout)
    for question, got in seen.items():
        assert got["moreState"] == "done", question
        more_lines = [line.removeprefix("- ") for line in replies[question]["more"]["more"]["lines"]]
        assert more_lines and all(line in got["lines"] for line in more_lines), f"{question}: the second part's lines reach the screen"
    for question in ("latest news on nvda", "why is nvda up today"):
        assert seen[question]["added"] > 0, f"{question}: the second part's lines are added after the first's"
    for question in ("latest news on nvda", "why is nvda up today", "nvda full report"):
        assert seen[question]["headlines"] > 0, f"{question}: the server's articles show as headlines"
    # A report (2026-11-11 rv4, changed on purpose from "added > 0"): the second part's header takes the place of the
    # fast "follow in a moment" header, so the line count need not grow. Its header is shown, the fast one is gone, every
    # other line above the cards stays in order, and the cards are the second part's.
    for question in ("nvda full report", "should i buy nvda"):
        got = seen[question]
        header = replies[question]["more"]["more"]["lines"][0]
        assert got["fastHeaders"] == 1 and not got["fastHeaderLeft"], f"{question}: the fast header was replaced"
        assert header in got["lines"], f"{question}: the second part's header is shown"
        others = [line for line in got["fastLines"] if not line.endswith("in a moment.")]
        assert [line for line in got["lines"] if line in others] == others, f"{question}: the other lines above the cards stay"
        assert got["freshSections"] > 0 and got["sectionsFromMore"], f"{question}: the cards are the second part's"
    assert seen["should i buy nvda"]["fastLines"][0] == chat_ai.NO_ADVICE_LINE, "the plain no-advice answer is on the fast screen"
    assert seen["should i buy nvda"]["lines"][0] == chat_ai.NO_ADVICE_LINE, "and still first after the second part"
    # Changed on purpose (2026-11-14): the reasons are code-built from the headlines. This world's only headline
    # ("Chipmaker announces a thing") is no kind of news the code can name, so the screen says nothing explains it.
    assert seen["why is nvda up today"]["reasonShown"] or "No saved headline explains this move." in seen["why is nvda up today"]["lines"], \
        "the reason the news points to, or that none does, is on screen"
    assert not any(got["aiProse"] for got in seen.values()), "no AI-written sentence reaches the screen"
    assert "load up" not in json.dumps(replies), "nor the route's replies"
    # Changed on purpose (2026-11-14): as above, the report's "Why it moved" reasons are code-built and this world's
    # one headline names no kind of news.
    report = seen["nvda full report"]
    assert report["reasonShown"] or any("No saved headline explains this move." in line for line in report["lines"]) or report["freshSections"] > 0
    assert seen["nvda full report"]["pills"] == ["short", "mid", "long"], "the report's views show on the Technical card"
    assert seen["nvda full report"]["dailyBars"] > 0, "the report's mini chart has bars"


LONG_TERM_SCRIPT = """
import { readFileSync } from "node:fs";
import { pathToFileURL } from "node:url";
const src = process.argv[2];
const { technicalData } = await import(pathToFileURL(src + "/chatReport.js").href);
const { technicalCard } = await import(pathToFileURL(src + "/miniEventChart.js").href);
const reply = JSON.parse(readFileSync(0, "utf8"));
const card = technicalCard(technicalData(reply.technical));
process.stdout.write(JSON.stringify({ shown: !!card, pills: card ? card.pills.map((p) => p.key) : [],
  verdicts: card ? card.pills.map((p) => p.verdict.verdict) : [], dailyBars: card?.daily?.bars.length || 0,
  weeklyBars: card?.weekly?.bars.length || 0, lines: card ? [card.daily?.support, card.daily?.resistance].filter(Boolean).length : 0,
  source: card?.source || "" }));
"""


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_long_term_analyst_route_feeds_the_technical_card(app, monkeypatch, tmp_path):  # noqa: F811
    """GET /api/long-term/analyst (2026-11-11 review F5): the Long Term page reads its Technical card from this route's
    "technical"; the route never sent one, so the card could not appear. Analyst services faked down; nothing leaves the PC."""
    from momx import analyst
    monkeypatch.setenv("AGX_ANALYST_CACHE_DIR", str(tmp_path / "analyst"))
    monkeypatch.setattr(analyst, "_get_json", lambda url: (_ for _ in ()).throw(TimeoutError("down")))
    analyst.reset()
    status, reply = app.api.get("/api/long-term/analyst?symbol=NVDA", headers=ADMIN)
    assert status == 200 and "technical" in reply, reply
    script = tmp_path / "long_term.mjs"
    script.write_text(LONG_TERM_SCRIPT, encoding="utf-8")
    run = subprocess.run([NODE, str(script), str(FRONTEND)], input=json.dumps(reply), capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert run.returncode == 0, run.stderr
    seen = json.loads(run.stdout)
    assert seen["shown"] and seen["pills"] == ["short", "mid", "long"], seen
    assert all(v in ("bullish", "bearish", "neutral") for v in seen["verdicts"]), seen
    assert seen["dailyBars"] > 0 and seen["weeklyBars"] > 0 and seen["lines"] > 0, seen
    assert seen["source"] == "the app's own chart signals, not advice"


def test_the_screen_drops_every_pending_line_the_server_sends():
    """2026-11-11 rv4: a past day's "Reading that day's saved headlines..." never cleared, because the screen matched only
    CHECKING_LINE. The server's PENDING_LINES and the screen's PENDING_LINES must be the same lines, word for word."""
    import re
    from momx import chat_why
    source = (FRONTEND / "chatReport.js").read_text(encoding="utf-8")
    consts = dict(re.findall(r'^export const (\w+) = "([^"]*)";$', source, re.M))
    listed = re.search(r"^export const PENDING_LINES = \[([^\]]*)\];$", source, re.M)
    assert listed, "chatReport.js exports PENDING_LINES"
    screen = tuple(consts[name.strip()] for name in listed.group(1).split(","))
    assert screen == chat_why.PENDING_LINES
    assert chat_why.CHECKING_LINE in chat_why.PENDING_LINES and chat_why.READING_SAVED_LINE in chat_why.PENDING_LINES
