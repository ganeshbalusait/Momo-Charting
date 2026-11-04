"""Unit: chart_profile_store limits (2026-11-04 coverage).

The store backs "Save <tf> for all tickers" for every device. Its limits are
what keep one bad save from corrupting or ballooning the account's file.
Everything runs in tmp_path (root=), never the live artifacts folder.
"""
from __future__ import annotations

import json

import pytest

import chart_profile_store as store

EMAIL = "trader@example.com"


def profile(**options) -> dict:
    return {"indicatorSettings": {"ema9": True}, "indicatorOptions": {"len": 9, **options}, "savedAt": "2026-11-04T15:00:00Z"}


@pytest.mark.parametrize("key", ["D/x", "..\\D", "5m 2", "", "k" * 65, None, 5])
def test_keys_outside_the_allowed_shape_are_refused(tmp_path, key):
    with pytest.raises(ValueError, match="bad profile key"):
        store.save_profile(EMAIL, key, profile(), root=tmp_path)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key", ["D", "5m", "4h", "k" * 64, "oiChart:5m", "layout-5m_v2.1"])
def test_every_timeframe_key_the_app_uses_is_accepted(tmp_path, key):
    assert key in store.save_profile(EMAIL, key, profile(), root=tmp_path)


def test_a_profile_over_256_kb_is_refused_and_the_old_copy_kept(tmp_path):
    store.save_profile(EMAIL, "D", profile(), root=tmp_path)
    with pytest.raises(ValueError, match="profile too large"):
        store.save_profile(EMAIL, "D", profile(blob="x" * (store.MAX_PROFILE_BYTES + 1)), root=tmp_path)
    assert store.load_profiles(EMAIL, root=tmp_path)["D"]["indicatorOptions"] == {"len": 9}


def test_an_account_file_over_2_mb_is_refused_without_losing_what_was_saved(tmp_path):
    chunk = "x" * (store.MAX_PROFILE_BYTES - 1_000)
    saved = 0
    with pytest.raises(ValueError, match="too many profiles"):
        for index in range(20):
            store.save_profile(EMAIL, f"tf{index}", profile(blob=chunk), root=tmp_path)
            saved += 1
    assert 0 < saved < 20
    kept = store.load_profiles(EMAIL, root=tmp_path)
    assert sorted(kept) == sorted(f"tf{index}" for index in range(saved))
    assert store._path(EMAIL, tmp_path).stat().st_size <= store.MAX_FILE_BYTES


def test_the_write_is_atomic_and_leaves_no_temp_file(tmp_path):
    store.save_profile(EMAIL, "D", profile(), root=tmp_path)
    store.save_profile(EMAIL, "5m", profile(), root=tmp_path)
    files = list(tmp_path.iterdir())
    assert len(files) == 1 and files[0].suffix == ".json"
    assert set(json.loads(files[0].read_text(encoding="utf-8"))["profiles"]) == {"D", "5m"}


def test_only_the_three_known_fields_are_stored(tmp_path):
    store.save_profile(EMAIL, "D", {**profile(), "evil": "<script>", "savedAt": None}, root=tmp_path)
    stored = store.load_profiles(EMAIL, root=tmp_path)["D"]
    assert set(stored) == {"indicatorSettings", "indicatorOptions", "savedAt"}
    assert stored["savedAt"] == ""


def test_a_corrupt_account_file_reads_as_no_profiles_and_heals_on_the_next_save(tmp_path):
    path = store._path(EMAIL, tmp_path)
    path.write_text("{not json", encoding="utf-8")
    assert store.load_profiles(EMAIL, root=tmp_path) == {}
    store.save_profile(EMAIL, "D", profile(), root=tmp_path)
    assert set(store.load_profiles(EMAIL, root=tmp_path)) == {"D"}


def test_the_account_is_case_insensitive_and_trimmed(tmp_path):
    store.save_profile("  Trader@Example.COM ", "D", profile(), root=tmp_path)
    assert set(store.load_profiles(EMAIL, root=tmp_path)) == {"D"}
