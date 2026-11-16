"""Ask the app, local search (2026-11-11): momx/chat_rag.py and its index builder. Scratch folders only."""
from __future__ import annotations

import json
import re
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from momx import chat_rag as rag
from momx import chat_rag_index as ix

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 11, 10, 20, 0, tzinfo=timezone.utc)
LEARN = {"label": "Open Learn + Setup", "view": "Learn + Setup"}
OWNER_WORDS = re.compile(r"\b(?:bots?|paper|scorecard|profits?|hermes|openclaw)\b", re.I)


def doc(source, title, text, scope="all", link=None):
    return {"source": source, "title": title, "text": text, "scope": scope, "link": link or LEARN}


HELP = [
    doc("glossary", "RVOL", "Relative volume: how much is trading now compared with normal."),
    doc("glossary", "Squeeze", "A period where the range tightens and pressure builds."),
    doc("glossary", "Skittles", "A fast momentum reading between 0 and 100."),
    doc("column", "H/L column", "Where the price sits in the recent range."),
    doc("page", "Sectors page", "Sector heatmap of the top stocks.", link={"label": "Open Sectors", "view": "Sectors"}),
    doc("page", "Bot page", "Virtual bot secretword every rule", "owner", {"label": "Open Bot", "view": "Bot"}),
    doc("rule", "Hidden rule", "secretword the rule the owner keeps", "owner", {"label": "Open Bot", "view": "Bot"}),
    doc("learn", "Careless help line", "Explains the paper account in public help", "all"),
    doc("learn", "Line with a name", "Ganesh asked for this line", "all"),
]


def notes(*releases):
    return {"releases": list(releases) or [
        {"at": "2026-11-10T20:00:00-05:00", "heading": "Scanner: squeeze column fixed", "items": ["The squeeze column now shows after hours.", "The paper bots ignore this secretword change."]},
        {"at": "2026-11-09T20:00:00-05:00", "heading": "Bot: new Scorecard", "items": ["A heading about the owner taints its lines."]},
    ]}


def news_db(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE catalyst_items (id INTEGER PRIMARY KEY, created_at TEXT, symbol TEXT, headline TEXT, source TEXT, url TEXT, published_at TEXT, summary TEXT)")
    conn.executemany("INSERT INTO catalyst_items (symbol, headline, published_at, summary) VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def built(tmp_path):
    help_json = tmp_path / "help.json"
    help_json.write_text(json.dumps({"docs": HELP}), encoding="utf-8")
    release = tmp_path / "notes.json"
    release.write_text(json.dumps(notes()), encoding="utf-8")
    db = news_db(tmp_path / "news.db", [
        ("NVDA", "Nvidia jumps 5.2% to $141.30 after earnings beat", "2026-11-10T15:00:00+00:00", "Shares rose 5.2% on a $3.5 billion guide."),
        ("AAPL", "Apple supplier news from the paper trail", "2026-11-10T14:00:00+00:00", ""),
        ("OLD", "A headline far too old", "2026-09-01T14:00:00+00:00", ""),
        ("!!", "Not a ticker", "2026-11-10T14:00:00+00:00", ""),
    ])
    folder = tmp_path / "index"
    result = ix.build(folder, help_json, release, db, NOW)
    return folder, result


@pytest.fixture(autouse=True)
def _no_build(monkeypatch):
    """Searches here read a finished index; a build is started only by the tests that say so."""
    monkeypatch.setattr(rag, "refresh", lambda folder=None: None)
    yield


def titles(results):
    return [r["title"] for r in results]


# ---------------------------------------------------------------- finding things
def test_a_word_finds_its_meaning_with_source_date_and_link(built):
    top = rag.search("what is RVOL", "scanner", folder=built[0])[0]
    assert top == {"source": "glossary", "title": "RVOL", "text": "Relative volume: how much is trading now compared with normal.", "date": None, "link": LEARN}


def test_a_release_note_carries_its_date_and_the_page(built):
    hit = next(r for r in rag.search("squeeze column after hours", "scanner", folder=built[0]) if r["source"] == "release")
    assert hit["date"] == "2026-11-10" and hit["link"]["view"] == "Release Notes"


def test_a_news_hit_links_to_the_ticker(built):
    hit = rag.search("nvidia earnings", "scanner", folder=built[0])[0]
    assert hit["source"] == "news" and hit["date"] == "2026-11-10" and hit["link"] == {"label": "Open NVDA news", "view": "News Feed", "ticker": "NVDA"}


@pytest.mark.parametrize("typo,want", [("squeze", "Squeeze"), ("skitles", "Skittles"), ("rvlo", "RVOL"), ("sqeeze", "Squeeze"), ("heatmp", "Sectors page"), ("xkittles", "Skittles")])
def test_typos_are_forgiven(built, typo, want):
    assert want in titles(rag.search(typo, "scanner", folder=built[0])), typo


def test_a_stem_is_a_match_not_a_typo(built):
    assert "Squeeze" in titles(rag.search("squeezes", "scanner", folder=built[0]))


def test_nothing_near_is_nothing(built):
    assert rag.search("zzqqyyxx", "scanner", folder=built[0]) == []
    assert rag.search("what is it", "scanner", folder=built[0]) == []
    assert rag.search("", "scanner", folder=built[0]) == [] and rag.search(None, "scanner", folder=built[0]) == []


@pytest.mark.parametrize("nasty", ['" OR title:*', "NEAR(a b)", "rvol'; DROP TABLE docs; --", "a AND", "(((", "*", "title:rvol", "x" * 5000])
def test_query_syntax_cannot_reach_the_index(built, nasty):
    rag.search(nasty, "scanner", folder=built[0])
    assert sqlite3.connect(built[0] / ix.PUBLIC_FILE).execute("SELECT COUNT(*) FROM docs").fetchone()[0] > 0


def test_the_limit_is_kept(built):
    assert len(rag.search("the squeeze range price", "owner", limit=2, folder=built[0])) <= 2
    assert len(rag.search("squeeze", "owner", limit=999, folder=built[0])) <= rag.MAX_LIMIT


def test_no_index_yet_is_an_empty_answer(tmp_path):
    assert rag.search("rvol", "scanner", folder=tmp_path / "none") == []


def test_a_corrupt_index_is_an_empty_answer(tmp_path):
    (tmp_path / ix.PUBLIC_FILE).write_bytes(b"not a database")
    assert rag.search("rvol", "scanner", folder=tmp_path) == []


# ---------------------------------------------------------------- what a regular user can never see
def test_owner_only_text_is_not_in_the_public_file_at_all(built):
    raw = (built[0] / ix.PUBLIC_FILE).read_bytes().lower()
    for word in (b"secretword", b"virtual bot", b"scorecard", b"ganesh", b"careless help"):
        assert word not in raw, word


def test_the_owner_finds_owner_text_and_a_regular_user_does_not(built):
    assert "Hidden rule" in titles(rag.search("secretword", "owner", folder=built[0]))
    assert rag.search("secretword", "scanner", folder=built[0]) == []


@pytest.mark.parametrize("scope", ["scanner", "all", "", None, "OWNER", "owner ", "admin"])
def test_any_scope_but_the_exact_word_owner_reads_the_public_file(built, scope):
    assert rag._file_for(scope, built[0]).name == ix.PUBLIC_FILE
    assert rag.search("secretword", scope, folder=built[0]) == []


def test_one_line_about_the_owner_does_not_hide_the_rest_of_the_release(built):
    regular = rag.search("squeeze column", "scanner", folder=built[0])
    assert any(r["text"] == "The squeeze column now shows after hours." for r in regular)
    assert not any("secretword" in r["text"] for r in regular)


def test_a_heading_about_the_owner_keeps_its_lines_private(built):
    assert rag.search("taints", "scanner", folder=built[0]) == []
    assert rag.search("taints", "owner", folder=built[0])


def test_a_name_never_reaches_anyone(built):
    for scope in ("owner", "scanner"):
        assert rag.search("Ganesh asked", scope, folder=built[0]) == [] or not any("Ganesh" in r["text"] for r in rag.search("Ganesh asked", scope, folder=built[0]))
    assert b"ganesh" not in (built[0] / ix.OWNER_FILE).read_bytes().lower()


def test_the_second_lock_drops_owner_words_even_if_a_public_file_held_them(tmp_path):
    ix._write(tmp_path, [{"source": "learn", "title": "Leaked", "text": "the bot did this", "date": "", "link": LEARN}], [], "x")
    assert rag.search("leaked", "scanner", folder=tmp_path) == []
    assert titles(rag.search("leaked", "owner", folder=tmp_path)) == ["Leaked"]               # the owner may read it


@pytest.mark.parametrize("text,private", [("how much traded at each level", False), ("The stock trades at $3 or more", False), ("the pattern you trade", False), ("Filled or not", False),
                                          ("a trade on this ticker", True), ("my trades today", True), ("paper account", True), ("Bot 2 weeks", True), ("Scorecard", True), ("P/L by rule", True), ("+0.5% per trade", True), ("real fills", True)])
def test_what_counts_as_owner_text(text, private):
    assert bool(ix.OWNER_ONLY.search(text)) == private


def test_every_public_document_in_the_shipped_file_is_clean():
    docs = json.loads(rag.HELP_JSON.read_text(encoding="utf-8"))["docs"]
    public = [d for d in docs if d["scope"] == "all"]
    assert len(public) > 100
    for d in public:
        assert not ix.OWNER_ONLY.search(f"{d['title']} {d['text']}"), d["title"]
        assert d["link"]["view"] not in ("Bot", "My Trading", "Option Paper Trading", "Paper Trading"), d["title"]
    assert not any(ix.PERSON.search(f"{d['title']} {d['text']}") for d in docs)


# ---------------------------------------------------------------- numbers never come from search
@pytest.mark.parametrize("text,gone", [("Nvidia jumps 5.2% to $141.30", ["5.2", "141.30"]), ("up 12%", ["12"]), ("a $3.5 billion guide", ["3.5"]), ("revenue of 4 billion", ["4 billion"]), ("EPS 1.25 beat", ["1.25"])])
def test_market_numbers_are_taken_out_of_news(text, gone):
    masked = ix.mask_numbers(text)
    assert all(g not in masked for g in gone) and ix.HIDDEN_NUMBER in masked


def test_plain_counts_and_years_stay():
    assert ix.mask_numbers("5 things to watch in 2026") == "5 things to watch in 2026"


def test_the_index_holds_no_price_from_a_headline(built):
    for name in (ix.PUBLIC_FILE, ix.OWNER_FILE):
        raw = (built[0] / name).read_bytes()
        assert b"141.30" not in raw and b"5.2%" not in raw and b"3.5 billion" not in raw


def test_old_news_and_a_bad_symbol_are_left_out(built):
    assert rag.search("headline far too old", "scanner", folder=built[0]) == [] and rag.search("not a ticker", "scanner", folder=built[0]) == []


def test_news_may_use_a_word_the_app_keeps_private(built):
    assert any(r["source"] == "news" for r in rag.search("supplier paper trail", "scanner", folder=built[0]))


@pytest.mark.parametrize("question", ["price of NVDA", "what is the rvol of nvda", "NVDA price", "spy vwap", "how much is NVDA", "expected move for aapl", "call wall on spy"])
def test_a_question_for_a_number_is_not_answered_from_search(built, question):
    out = rag.help_search({"query": question}, {"scope": "owner", "searchDir": built[0]})
    assert rag.asks_for_a_number(question) and out["facts"]["numbers"] is False and "does not answer numbers" in out["lines"][0]


@pytest.mark.parametrize("question", ["what is RVOL", "what is vwap", "what does delta mean", "how does open interest work", "what is a wall", "what is wrong with the app", "where is the chart", "meaning of rvol", "levels on the chart"])
def test_a_question_about_what_a_word_means_is_answered(question):
    assert not rag.asks_for_a_number(question)


# ---------------------------------------------------------------- the tool
def test_help_search_lines_links_and_shape(built):
    out = rag.TOOLS["help_search"]["handler"]({"query": "what is RVOL"}, {"scope": "scanner", "searchDir": built[0]})
    assert set(out) == {"lines", "facts", "links", "table", "asOf"} and out["table"] is None
    assert out["lines"][0] == "Glossary: RVOL. Relative volume: how much is trading now compared with normal."
    assert out["links"][0] == LEARN and out["facts"]["results"][0] == {"source": "glossary", "title": "RVOL", "date": None}


def test_help_search_with_nothing_found_is_not_a_dead_end(built):
    out = rag.help_search({"query": "zzqqyyxx"}, {"scope": "scanner", "searchDir": built[0]})
    assert "could not find" in out["lines"][0] and out["links"] == [LEARN]


@pytest.mark.parametrize("args", [None, {}, {"query": ""}, {"query": None}, {"query": 5, "limit": "x"}, {"query": "rvol", "limit": 999}])
def test_help_search_never_raises(built, args):
    assert rag.help_search(args, {"scope": "owner", "searchDir": built[0]})["lines"]


def test_a_session_with_no_scope_searches_nothing(built, monkeypatch):
    monkeypatch.setattr(rag, "search", lambda *a, **k: pytest.fail("no scope searches nothing"))
    assert rag.help_search({"query": "rvol"}, {})["lines"] == ["That is not available in this chat."]


def test_the_tool_entry_has_the_fixed_shape():
    spec = rag.TOOLS["help_search"]
    assert spec["scope"] == "all" and spec["description"] and spec["args"]["required"] == ["query"] and callable(spec["handler"])


def test_a_search_writes_nothing_and_keeps_no_question(built):
    before = {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in built[0].iterdir()}
    question = "uniquequestionwordzz rvol"
    rag.search(question, "scanner", folder=built[0])
    rag.help_search({"query": question}, {"scope": "owner", "searchDir": built[0]})
    assert {p.name: (p.stat().st_mtime_ns, p.stat().st_size) for p in built[0].iterdir()} == before
    assert not any(b"uniquequestionwordzz" in p.read_bytes() for p in built[0].iterdir())


# ---------------------------------------------------------------- where the index lives
def test_the_folder_comes_from_the_argument_then_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(rag.INDEX_DIR_ENV, str(tmp_path / "env"))
    assert rag.index_dir() == tmp_path / "env" and rag.index_dir(tmp_path / "arg") == tmp_path / "arg"


def test_a_test_run_can_never_reach_the_live_folder_or_the_live_database(monkeypatch):
    monkeypatch.delenv(rag.INDEX_DIR_ENV, raising=False)
    monkeypatch.delenv(rag.NEWS_DB_ENV, raising=False)
    assert "artifacts" not in rag.index_dir().parts and rag.news_db() is None


def test_outside_a_test_run_it_is_the_apps_cache_folder(monkeypatch):
    monkeypatch.delenv(rag.INDEX_DIR_ENV, raising=False)
    monkeypatch.delenv(rag.NEWS_DB_ENV, raising=False)
    monkeypatch.setattr(rag, "is_test_run", lambda: False)
    assert rag.index_dir() == ROOT / "artifacts" / "chat_search_cache" and rag.news_db() == ROOT / "database" / "trades.db"


# ---------------------------------------------------------------- the rebuild
@pytest.fixture
def real_refresh(monkeypatch):
    monkeypatch.undo()
    rag._BUILD.update(running=False, startedAt=None, done=None)
    yield rag.refresh
    done = rag._BUILD["done"]
    if done is not None:
        done.wait(30)
    rag._BUILD.update(running=False, startedAt=None, done=None)


class FakeChild:
    started: list = []
    release = threading.Event()

    def __init__(self, args, **kw):
        type(self).started.append((args, kw))

    def communicate(self, timeout=None):
        type(self).release.wait(10)
        return None, None

    def kill(self):
        pass


@pytest.fixture
def fake_child(monkeypatch):
    FakeChild.started = []
    FakeChild.release = threading.Event()
    monkeypatch.setattr(rag.subprocess, "Popen", FakeChild)
    yield FakeChild
    FakeChild.release.set()


def test_a_rebuild_starts_once_never_waits_and_uses_a_quiet_low_priority_child(real_refresh, fake_child, tmp_path):
    started = time.monotonic()
    done = real_refresh(tmp_path)
    assert time.monotonic() - started < 2 and done is not None
    for _ in range(300):
        if fake_child.started:
            break
        time.sleep(0.01)
    assert real_refresh(tmp_path) is done and len(fake_child.started) == 1                    # one at a time
    args, kw = fake_child.started[0]
    assert args[0] == sys.executable and args[1] == "-I" and args[2] == str(rag.INDEX_SCRIPT) and args[args.index("--out") + 1] == str(tmp_path)
    assert "--news-db" not in args and kw["creationflags"] == rag._child_flags() and kw["stdout"] == subprocess.DEVNULL
    if rag.os.name == "nt":
        assert rag._child_flags() == 0x08000000 | 0x00004000
    fake_child.release.set()
    done.wait(5)


def test_a_rebuild_is_at_most_every_ten_minutes(real_refresh, fake_child, tmp_path, monkeypatch):
    fake_child.release.set()
    clock = [1000.0]
    monkeypatch.setattr(rag.time, "monotonic", lambda: clock[0])
    done = real_refresh(tmp_path)
    done.wait(5)
    clock[0] += rag.REBUILD_SECONDS - 1
    assert real_refresh(tmp_path) is None and len(fake_child.started) == 1                    # even though the build left no file
    clock[0] += 2
    second = real_refresh(tmp_path)
    second.wait(5)
    assert len(fake_child.started) == 2


def test_a_fresh_index_is_not_rebuilt(real_refresh, fake_child, tmp_path):
    (tmp_path / ix.PUBLIC_FILE).write_bytes(b"x")
    assert real_refresh(tmp_path) is None and not fake_child.started


def test_an_old_index_is_rebuilt(real_refresh, fake_child, tmp_path):
    fake_child.release.set()
    path = tmp_path / ix.PUBLIC_FILE
    path.write_bytes(b"x")
    old = time.time() - rag.REBUILD_SECONDS - 5
    rag.os.utime(path, (old, old))
    done = real_refresh(tmp_path)
    assert done is not None
    done.wait(5)


def test_a_child_that_runs_out_of_time_is_killed(real_refresh, monkeypatch, tmp_path):
    killed = []

    class Slow:
        def __init__(self, args, **kw):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("x", timeout)
            return None, None

        def kill(self):
            killed.append(1)
    monkeypatch.setattr(rag.subprocess, "Popen", Slow)
    real_refresh(tmp_path).wait(5)
    assert killed and not rag._BUILD["running"]


def test_a_child_that_cannot_start_leaves_search_working(real_refresh, monkeypatch, tmp_path):
    def refuse(*a, **k):
        raise OSError("no")
    monkeypatch.setattr(rag.subprocess, "Popen", refuse)
    real_refresh(tmp_path).wait(5)
    assert not rag._BUILD["running"] and rag.search("rvol", "scanner", folder=tmp_path) == []


def test_a_real_child_builds_both_files_at_below_normal_priority(tmp_path):
    help_json = tmp_path / "help.json"
    help_json.write_text(json.dumps({"docs": HELP}), encoding="utf-8")
    release = tmp_path / "notes.json"
    release.write_text(json.dumps(notes()), encoding="utf-8")
    folder = tmp_path / "out"
    done = subprocess.run([sys.executable, "-I", str(rag.INDEX_SCRIPT), "--out", str(folder), "--help-json", str(help_json), "--release-notes", str(release)],
                          capture_output=True, timeout=300, creationflags=rag._child_flags())
    report = json.loads(done.stdout)
    assert done.returncode == 0 and report["public"] < report["owner"] and (folder / ix.PUBLIC_FILE).exists() and (folder / ix.OWNER_FILE).exists()
    assert not list(folder.glob("*.tmp"))
    if rag.os.name == "nt":
        assert report["priority"] == rag.BELOW_NORMAL_PRIORITY_CLASS
    assert titles(rag.search("rvol", "scanner", folder=folder))[0] == "RVOL"


def test_a_first_search_waits_briefly_for_the_build_and_then_finds_it(monkeypatch, tmp_path):
    monkeypatch.undo()
    monkeypatch.setenv(rag.INDEX_DIR_ENV, str(tmp_path / "first"))
    rag._BUILD.update(running=False, startedAt=None, done=None)
    monkeypatch.setattr(rag, "FIRST_BUILD_WAIT_SECONDS", 240.0)
    monkeypatch.setattr(rag, "_child_flags", lambda: rag.CREATE_NO_WINDOW if rag.os.name == "nt" else 0)   # this test is about the wait: normal priority, so a busy PC cannot starve it
    try:
        assert titles(rag.search("skittles", "scanner"))[0] == "Skittles"
    finally:
        done = rag._BUILD["done"]
        if done is not None:
            done.wait(240)
        rag._BUILD.update(running=False, startedAt=None, done=None)


def test_the_child_script_imports_nothing_from_the_app():
    assert not re.search(r"^\s*(from|import)\s+(momx|api_server|agents|database|config)\b", rag.INDEX_SCRIPT.read_text(encoding="utf-8"), re.M)


def test_a_rebuild_while_a_search_has_the_file_open_still_lands(built, tmp_path, monkeypatch):
    folder = built[0]
    calls = []
    real = ix.os.replace

    def flaky(a, b):
        calls.append(a)
        if len(calls) == 1:
            raise PermissionError("held")
        return real(a, b)
    monkeypatch.setattr(ix.os, "replace", flaky)
    monkeypatch.setattr(ix.time, "sleep", lambda s: None)
    ix._write(folder, [{"source": "learn", "title": "Newer", "text": "freshly added words", "date": "", "link": LEARN}], [], "x")
    assert len(calls) == 3 and titles(rag.search("freshly", "owner", folder=folder)) == ["Newer"]


def test_a_missing_or_broken_source_builds_what_it_can(tmp_path):
    good = tmp_path / "help.json"
    good.write_text(json.dumps({"docs": HELP}), encoding="utf-8")
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    result = ix.build(tmp_path / "a", good, bad, tmp_path / "no.db", NOW)
    assert result["news"] == 0 and result["public"] > 0
    empty_db = tmp_path / "empty.db"
    sqlite3.connect(empty_db).close()
    assert ix.build(tmp_path / "b", good, tmp_path / "none.json", empty_db, NOW)["newsError"] == "OperationalError"


# ---------------------------------------------------------------- the shipped help file
def test_the_help_file_is_up_to_date_with_the_screens():
    done = subprocess.run([sys.executable, str(ROOT / "scripts" / "build_chat_help_index.py"), "--check"], capture_output=True, text=True, timeout=180, cwd=ROOT)
    assert done.returncode == 0, done.stdout + done.stderr


def test_every_scanner_column_has_a_meaning():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_chat_help_index as gen
    labels = gen.column_labels()
    assert {"Setup", "Fresh", "Record", "H/L", "RVOL", "SQZ", "SKIT", "Quote"} <= set(labels) and all(label in gen.COLUMN_MEANINGS for label in labels)


def test_the_help_file_covers_every_page_and_marks_the_owners():
    sys.path.insert(0, str(ROOT / "scripts"))
    import build_chat_help_index as gen
    docs = json.loads(rag.HELP_JSON.read_text(encoding="utf-8"))["docs"]
    pages = gen.pages()
    assert len(pages) >= 15 and {p["view"] for p in pages if p["adminOnly"]} == {"Bot", "My Trading", "Option Paper Trading", "Paper Trading"}
    for page in pages:
        found = [d for d in docs if d["source"] == "page" and d["link"]["view"] == page["view"]]
        assert found, page["view"]
        assert all(d["scope"] == ("owner" if page["adminOnly"] else "all") for d in found), page["view"]


def test_the_real_words_find_the_right_help(tmp_path):
    """The shipped help file, a scratch index, no news: the questions the trader types."""
    ix.build(tmp_path, rag.HELP_JSON, ROOT / "frontend" / "public" / "release-notes.json", None, NOW)
    for question, want in (("what is RVOL", "RVOL"), ("what is vwap", "VWAP"), ("what does the h/l column mean", "H/L column"), ("what is skitles", "Skittles"), ("where is the heatmap", "Sectors page")):
        assert want in titles(rag.search(question, "scanner", limit=3, folder=tmp_path)), question
    for question in ("scorecard", "paper trades", "bot", "openclaw"):
        for result in rag.search(question, "scanner", limit=10, folder=tmp_path):
            assert not OWNER_WORDS.search(f"{result['title']} {result['text']}"), (question, result["title"])
            assert result["link"]["view"] not in ("Bot", "My Trading", "Option Paper Trading", "Paper Trading")


def test_a_word_of_the_owners_is_not_corrected_into_something_else_for_a_regular_user(tmp_path):
    ix._write(tmp_path, [{"source": "news", "title": "JPM: Piper Sandler rating", "text": "", "date": "", "link": LEARN}], [], "x")
    assert rag.search("paper", "scanner", folder=tmp_path) == [] and rag.search("scorecard bot", "scanner", folder=tmp_path) == []


# ---------------------------------------------------------------- integration: the real route over HTTP
@pytest.fixture
def over_http(built, tmp_path, monkeypatch):
    from api_harness import RELAYED, running_api, seed_user
    from cf_access_auth import ACCESS_EMAIL_HEADER
    from momx import chat_ai, chat_limits
    seed_user("search-owner@example.com", role="admin")
    seed_user("search-viewer@example.com", role="user")
    monkeypatch.setenv(rag.INDEX_DIR_ENV, str(built[0]))
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path / "grade"))
    monkeypatch.setattr(chat_ai, "_default_ask", lambda system, user, schema: {"ok": False, "error": "no AI in tests"})
    chat_limits.LIMITS.reset()
    chat_ai.clear_plans()
    yield running_api, {ACCESS_EMAIL_HEADER: "search-owner@example.com"}, {**RELAYED, ACCESS_EMAIL_HEADER: "search-viewer@example.com"}
    chat_limits.LIMITS.reset()


def test_a_regular_user_searches_help_through_the_route(over_http):
    running_api, _, regular = over_http
    with running_api() as api:
        status, body = api.post("/api/bot-chat", {"question": "what is RVOL"}, headers=regular)
    assert status == 200 and any("Relative volume" in line for line in body["lines"]) and any(link["view"] == "Learn + Setup" for link in body["links"])


def test_owner_text_never_comes_through_the_route_to_a_regular_user(over_http):
    running_api, owner, regular = over_http
    with running_api() as api:
        _, public = api.post("/api/bot-chat", {"question": "what does secretword mean"}, headers=regular)
        _, private = api.post("/api/bot-chat", {"question": "what does secretword mean"}, headers=owner)
    assert "secretword" not in json.dumps(public).replace("what does secretword mean", "") and not any(link["view"] == "Bot" for link in public.get("links") or [])
    assert any("secretword" in line for line in private["lines"])
