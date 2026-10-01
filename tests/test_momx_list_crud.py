"""Named-list CRUD: add/remove tickers, create/rename/delete lists.

NOTHING HERE TOUCHES THE REAL UNIVERSE FILE. Every test passes an explicit
``path=tmp_path/...``, so a bug in resolution cannot clobber the trader's
357-name Watchlist -- which is exactly what these functions exist to prevent.

The case this file guards hardest is the one that nearly shipped on
2026-09-02: ``reset_universe`` on a USER-created list popped the key, wrote the
document, and only then raised ``UnknownListError`` from ``load_universe`` --
so the list was destroyed on disk BEFORE the caller was told, in a 400 that
said the list did not exist. See ``test_reset_refuses_user_list_without_deleting``.
"""

from __future__ import annotations

import json

import pytest

from momx import board


@pytest.fixture()
def universe(tmp_path):
    """A scratch universe document with both seeded lists still tracking."""
    path = tmp_path / "momx_universe.json"
    path.write_text(
        json.dumps({"schemaVersion": 2, "active": "Watchlist", "lists": {}}),
        encoding="utf-8",
    )
    return path


def _saved_lists(path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")).get("lists") or {}


# ---------------------------------------------------------------------------
# the regression this file exists for
# ---------------------------------------------------------------------------

def test_reset_refuses_user_list_without_deleting(universe):
    """Restore on a user list must refuse BEFORE writing, not after."""
    board.save_universe(["NVDA", "AMD"], "Semis", path=universe, create=True)

    with pytest.raises(board.UnknownListError):
        board.reset_universe("Semis", path=universe)

    # The whole point: the refusal must not have cost him the list.
    assert "Semis" in _saved_lists(universe)
    assert board.load_universe("Semis", path=universe) == ["NVDA", "AMD"]


def test_reset_still_works_on_a_seeded_list(universe):
    board.save_universe(["AVGO"], "Watchlist", path=universe)
    assert board.load_universe("Watchlist", path=universe) == ["AVGO"]

    restored = board.reset_universe("Watchlist", path=universe)

    assert len(restored) > 1
    assert "Watchlist" not in _saved_lists(universe), "must go back to tracking, not be re-saved"


def test_delete_refuses_a_built_in_list(universe):
    with pytest.raises(board.UnknownListError):
        board.delete_list("Mag7", path=universe)
    assert "Mag7" in board.list_names(path=universe)


def test_delete_removes_a_user_list_and_moves_active_off_it(universe):
    board.save_universe(["NVDA"], "Semis", path=universe, create=True)
    board.set_active_list("Semis", path=universe)

    result = board.delete_list("Semis", path=universe)

    assert result["deleted"] == "Semis"
    assert "Semis" not in result["names"]
    assert result["active"] != "Semis", "the panel must not open on a tab that is gone"
    assert board.list_names(path=universe) == ["Mag7", "Watchlist"]


# ---------------------------------------------------------------------------
# add / remove
# ---------------------------------------------------------------------------

def test_add_appends_without_replacing(universe):
    before = board.load_universe("Watchlist", path=universe)

    result = board.add_symbols("zzzq yyyw", "Watchlist", path=universe)

    assert result["added"] == ["ZZZQ", "YYYW"], "lowercase input must be accepted"
    assert result["after"] == result["before"] + 2
    after = board.load_universe("Watchlist", path=universe)
    assert after[: len(before)] == before, "existing symbols keep their order"
    assert after[-2:] == ["ZZZQ", "YYYW"]


def test_add_reports_duplicates_and_changes_nothing(universe):
    existing = board.load_universe("Watchlist", path=universe)
    already_there = existing[0]

    result = board.add_symbols(already_there, "Watchlist", path=universe)

    assert result["added"] == []
    assert result["already"] == [already_there]
    assert result["before"] == result["after"]
    assert result["materialised"] is False
    assert "Watchlist" not in _saved_lists(universe), (
        "an add of nothing must leave a tracking list tracking"
    )


def test_add_to_a_tracking_list_reports_that_it_materialised(universe):
    result = board.add_symbols("zzzq", "Watchlist", path=universe)

    assert result["materialised"] is True, (
        "the trader has to be told his list stopped following My Watchlist"
    )
    assert "Watchlist" in _saved_lists(universe)


def test_add_to_a_user_list_never_materialises(universe):
    board.save_universe(["NVDA"], "Semis", path=universe, create=True)

    result = board.add_symbols("amd", "Semis", path=universe)

    assert result["added"] == ["AMD"]
    assert result["materialised"] is False, "a user list was never tracking anything"


def test_add_touches_only_the_named_list(universe):
    watchlist_before = board.load_universe("Watchlist", path=universe)

    board.add_symbols("zzzq", "Mag7", path=universe)

    assert board.load_universe("Watchlist", path=universe) == watchlist_before


def test_remove_drops_only_what_is_there(universe):
    existing = board.load_universe("Watchlist", path=universe)
    victim = existing[0]

    result = board.remove_symbols(f"{victim} zzzq", "Watchlist", path=universe)

    assert result["removed"] == [victim]
    assert result["missing"] == ["ZZZQ"]
    assert victim not in board.load_universe("Watchlist", path=universe)


def test_add_then_reset_returns_a_tracking_list(universe):
    seed_count = len(board.load_universe("Watchlist", path=universe))
    board.add_symbols("zzzq yyyw", "Watchlist", path=universe)
    assert len(board.load_universe("Watchlist", path=universe)) == seed_count + 2

    board.reset_universe("Watchlist", path=universe)

    assert len(board.load_universe("Watchlist", path=universe)) == seed_count
    assert "Watchlist" not in _saved_lists(universe)


# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------

def test_rename_is_one_write_and_cannot_leave_both_names(universe):
    """The live 2026-09-02 failure: save+delete+set-active left Semis AND Chips."""
    board.save_universe(["NVDA", "AMD"], "Semis", path=universe, create=True)

    result = board.rename_list("Semis", "Chips", path=universe)

    assert result["renamedFrom"] == "Semis"
    names = board.list_names(path=universe)
    assert "Chips" in names
    assert "Semis" not in names, "the old name must not survive a rename"
    assert board.load_universe("Chips", path=universe) == ["NVDA", "AMD"]


def test_rename_keeps_the_list_in_place(universe):
    board.save_universe(["NVDA"], "AAA", path=universe, create=True)
    board.save_universe(["AMD"], "BBB", path=universe, create=True)

    board.rename_list("AAA", "ZZZ", path=universe)

    names = board.list_names(path=universe)
    assert names.index("ZZZ") < names.index("BBB"), "renaming must not reorder the picker"


def test_rename_refuses_a_built_in_list(universe):
    with pytest.raises(board.UnknownListError):
        board.rename_list("Mag7", "Megas", path=universe)
    assert "Mag7" in board.list_names(path=universe)


def test_rename_refuses_a_name_already_taken(universe):
    board.save_universe(["NVDA"], "Semis", path=universe, create=True)
    with pytest.raises(board.UnknownListError):
        board.rename_list("Semis", "Mag7", path=universe)
    assert board.load_universe("Semis", path=universe) == ["NVDA"]


def test_rename_moves_active_with_the_list(universe):
    board.save_universe(["NVDA"], "Semis", path=universe, create=True)
    board.set_active_list("Semis", path=universe)

    board.rename_list("Semis", "Chips", path=universe)

    assert board.active_list(path=universe) == "Chips"
