"""The chat's page index and link rules (2026-11-11): momx/chat_pages.py."""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from momx import chat_pages as pages

ROOT = Path(__file__).resolve().parents[1]


def nav_items():
    source = (ROOT / "frontend" / "src" / "App.jsx").read_text(encoding="utf-8")
    block = source[source.index("const optionNavItems = ["):]
    block = block[:block.index("\n];")]
    block = "\n".join(line for line in block.splitlines() if not line.strip().startswith("//"))
    return re.findall(r'\{\s*label:\s*"([^"]+)"([^}]*)\}', block)


def test_every_page_in_the_left_rail_is_in_the_index_and_nothing_else():
    assert sorted(label for label, _ in nav_items()) == sorted(page.view for page in pages.PAGES)


def test_owner_only_pages_match_the_left_rail():
    assert {label for label, rest in nav_items() if "adminOnly: true" in rest} == set(pages.ADMIN_VIEWS)


def test_the_views_match_the_screens_own_allow_list():
    source = (ROOT / "frontend" / "src" / "appLinks.js").read_text(encoding="utf-8")
    listed = re.search(r"LINK_VIEWS = \[(.*?)\];", source, re.S).group(1)
    admin = re.search(r"ADMIN_VIEWS = \[(.*?)\];", source, re.S).group(1)
    assert set(re.findall(r'"([^"]+)"', listed + admin)) == set(pages.BY_VIEW)
    assert set(re.findall(r'"([^"]+)"', admin)) == set(pages.ADMIN_VIEWS)


def test_dead_branches_are_never_linked():
    for view in ("Dashboard", "Pre Market Mag7", "Option Watchlist"):
        assert pages.clean_link({"view": view, "label": "x"}, "owner") is None


@pytest.mark.parametrize("text,view", [
    ("where is the scanner", "MomX Scanner"), ("charts", "Charts & OI"), ("where are the headlines", "News Feed"), ("open release notes", "Release Notes"),
    ("change my password", "Settings"), ("where is the heatmap", "Sectors"), ("my tickers", "Watchlist"), ("take me to premarket", "Premarket Scanner"),
    ("oi script", "OI Level Script TOS"), ("mag7 watchlist", "Mag7 Scanner"), ("roi", "ROI Calc")])
def test_a_page_is_found_by_what_a_trader_calls_it(text, view):
    assert pages.find(text, "owner", loose=True)[0].view == view


def test_longest_name_wins_and_implies_its_details():
    page, deep = pages.find("where is history find", "scanner", loose=True)
    assert page.view == "MomX Scanner" and deep == {"tab": "history"}
    page, deep = pages.find("open the high oi walls", "scanner", loose=True)
    assert page.view == "Quick Options" and deep == {"sub": "high-oi"}


@pytest.mark.parametrize("text", ["open the bot", "scorecard page", "open paper trading", "option paper"])
def test_an_owner_page_is_not_found_for_a_regular_user(text):
    assert pages.find(text, "scanner", loose=True) is None
    assert pages.owner_page_named(text)
    assert pages.find(text, "owner", loose=True) is not None


def test_a_word_inside_a_longer_word_is_not_a_page():
    assert pages.find("newsletter", "owner", loose=True) is None


GOOD = {"label": "Open NVDA on Mag7 History, 09:30", "view": "MomX Scanner", "list": "Mag7", "side": "bull", "tab": "history", "day": "2026-11-10", "ticker": "NVDA", "time": "09:30"}


def test_a_good_link_passes_whole():
    assert pages.clean_link(GOOD) == GOOD


@pytest.mark.parametrize("bad", [
    {"view": "javascript:alert(1)"}, {"view": "Bot", "ticker": "<script>"}, {"view": "Charts & OI", "timeframe": "7m"}, {"view": "MomX Scanner", "side": "up"},
    {"view": "MomX Scanner", "tab": "secret"}, {"view": "MomX Scanner", "day": "2026-13-40"}, {"view": "MomX Scanner", "time": "25:00"}, {"view": "MomX Scanner", "list": "../etc"},
    {"view": "MomX Scanner", "sub": "a b"}, {"view": "Charts & OI", "label": 5}, "not a mapping", None])
def test_a_bad_link_is_refused(bad):
    assert pages.clean_link(bad, "owner") is None


def test_an_unknown_field_is_dropped():
    assert pages.clean_link({"view": "Settings", "href": "https://evil.example", "onclick": "x"}) == {"label": "Settings", "view": "Settings"}


@pytest.mark.parametrize("view", sorted(pages.ADMIN_VIEWS))
def test_an_owner_page_link_is_dropped_for_a_regular_user(view):
    assert pages.clean_link({"view": view}, "scanner") is None and pages.clean_link({"view": view}, "owner")["view"] == view


def test_links_are_capped_and_not_repeated():
    many = [{"view": "Charts & OI", "ticker": f"T{chr(65 + i)}"} for i in range(10)]
    assert len(pages.clean_links(many)) == pages.MAX_LINKS
    assert len(pages.clean_links([GOOD, {**GOOD, "label": "again"}])) == 1


def test_a_link_only_carries_details_its_page_can_use():
    assert pages.link("Settings", ticker="NVDA") == {"label": "Settings", "view": "Settings"}
    assert pages.link("Charts & OI", "x", ticker="NVDA", timeframe="1h", day="2026-11-10") == {"label": "x", "view": "Charts & OI", "ticker": "NVDA", "timeframe": "1h"}


def test_chips_for_a_regular_user_never_mention_the_owner_side():
    for page in pages.PAGES:
        for ticker in (None, "NVDA"):
            assert not any(re.search(r"\b(bot|paper|scorecard|trades?)\b", chip.lower()) for chip in pages.chips_for(page.view, ticker, "scanner"))
    assert any("bot" in chip for chip in pages.chips_for("Bot", None, "owner"))
