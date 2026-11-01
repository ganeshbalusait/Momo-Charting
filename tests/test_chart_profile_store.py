"""2026-10-08: "Save D for all tickers" must reach his phone and desktop."""
import pytest

import chart_profile_store as store


def profile(saved_at="2026-10-08T21:30:00Z", **options):
    return {"indicatorSettings": {"woodiesPivots": True}, "indicatorOptions": {"x": 1, **options}, "savedAt": saved_at}


def test_profiles_are_per_account_and_per_timeframe(tmp_path) -> None:
    store.save_profile("Ganesh@Example.com", "D", profile(), root=tmp_path)
    store.save_profile("ganesh@example.com", "5m", profile(y=2), root=tmp_path)   # same account, any case
    got = store.load_profiles("ganesh@example.com", root=tmp_path)
    assert set(got) == {"D", "5m"} and got["5m"]["indicatorOptions"]["y"] == 2
    assert store.load_profiles("someone@else.com", root=tmp_path) == {}


def test_bad_input_is_refused(tmp_path) -> None:
    with pytest.raises(ValueError):
        store.save_profile("", "D", profile(), root=tmp_path)
    with pytest.raises(ValueError):
        store.save_profile("a@b.c", "../x", profile(), root=tmp_path)
    with pytest.raises(ValueError):
        store.save_profile("a@b.c", "D", {"indicatorSettings": {}}, root=tmp_path)
