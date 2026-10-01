"""The Momo Alert's four gates, each pinned: crossing, scan-pass, cooldown, live tape."""
from __future__ import annotations

import json

from momx import momo_alert


NOW = 1_900_000_000.0  # fixed epoch so cooldown math is deterministic
LIVE_STAMP = "2030-03-14T15:00:00+00:00"


def _epoch(iso: str) -> float:
    from datetime import datetime
    return datetime.fromisoformat(iso).timestamp()


def board(rows, tape_as_of=LIVE_STAMP, name="Watchlist"):
    return {"list": name, "tapeAsOf": tape_as_of, "rows": rows}


def row(symbol, rvol_5m=None, rvol_15m=None, scan_pass=True):
    rvol = {}
    if rvol_5m is not None:
        rvol["5m"] = {"value": rvol_5m}
    if rvol_15m is not None:
        rvol["15m"] = {"value": rvol_15m}
    return {"symbol": symbol, "scanPass": scan_pass, "rvol": rvol, "pctChange": 4.2}


def cfg(**overrides):
    base = json.loads(json.dumps(momo_alert.DEFAULT_CONFIG))
    base.update(overrides)
    return base


def test_fires_on_a_5m_crossing_for_a_scan_pass_symbol():
    prev = board([row("DKNG", rvol_5m=1.2)])
    curr = board([row("DKNG", rvol_5m=12.3)])
    ledger = {}
    alerts = momo_alert.detect(curr, prev, cfg(), ledger, now_epoch=_epoch(LIVE_STAMP))
    assert len(alerts) == 1
    assert alerts[0]["symbol"] == "DKNG"
    assert alerts[0]["timeframe"] == "5m"
    assert alerts[0]["value"] == 12.3
    assert "DKNG" in ledger


def test_needs_the_scan_to_pass_volume_alone_is_not_the_signature():
    prev = board([row("CLF", rvol_5m=1.0, scan_pass=False)])
    curr = board([row("CLF", rvol_5m=9.0, scan_pass=False)])
    assert momo_alert.detect(curr, prev, cfg(), {}, now_epoch=_epoch(LIVE_STAMP)) == []


def test_edge_triggered_a_symbol_staying_elevated_does_not_refire():
    prev = board([row("DKNG", rvol_5m=12.3)])
    curr = board([row("DKNG", rvol_5m=12.5)])
    assert momo_alert.detect(curr, prev, cfg(), {}, now_epoch=_epoch(LIVE_STAMP)) == []


def test_cooldown_swallows_a_second_crossing_within_fifteen_minutes():
    base = _epoch(LIVE_STAMP)
    ledger = {"DKNG": base - 300.0}  # fired 5 minutes ago
    prev = board([row("DKNG", rvol_5m=1.0)])
    curr = board([row("DKNG", rvol_5m=8.0)])
    assert momo_alert.detect(curr, prev, cfg(), ledger, now_epoch=base) == []
    # ...but after the cooldown expires the same crossing fires again.
    ledger = {"DKNG": base - 16 * 60.0}
    assert len(momo_alert.detect(curr, prev, cfg(), ledger, now_epoch=base)) == 1


def test_a_frozen_tape_never_alarms():
    prev = board([row("DKNG", rvol_5m=1.0)], tape_as_of="2030-03-14T15:00:00+00:00")
    curr = board([row("DKNG", rvol_5m=12.3)], tape_as_of="2030-03-14T15:00:00+00:00")
    weekend = _epoch("2030-03-16T15:00:00+00:00")  # two days later
    assert momo_alert.detect(curr, prev, cfg(), {}, now_epoch=weekend) == []


def test_cold_start_emits_nothing():
    curr = board([row("DKNG", rvol_5m=12.3)])
    assert momo_alert.detect(curr, None, cfg(), {}, now_epoch=_epoch(LIVE_STAMP)) == []


def test_per_timeframe_thresholds_are_independent():
    config = cfg()
    config["timeframes"]["5m"] = {"armed": True, "threshold": 5.0}
    config["timeframes"]["15m"] = {"armed": True, "threshold": 3.0}
    prev = board([row("A", rvol_5m=1.0, rvol_15m=1.0)])
    # 5m hits 4.0 (below its 5x bar); 15m hits 3.5 (above its 3x bar).
    curr = board([row("A", rvol_5m=4.0, rvol_15m=3.5)])
    alerts = momo_alert.detect(curr, prev, config, {}, now_epoch=_epoch(LIVE_STAMP))
    assert [a["timeframe"] for a in alerts] == ["15m"]
    assert alerts[0]["threshold"] == 3.0


def test_default_config_arms_only_5m_at_3x():
    config = momo_alert.DEFAULT_CONFIG
    armed = [name for name, tf in config["timeframes"].items() if tf["armed"]]
    assert armed == ["5m"]
    assert config["timeframes"]["5m"]["threshold"] == 3.0


def test_config_roundtrip_and_garbage_tolerance(tmp_path):
    path = tmp_path / "momo.json"
    saved = momo_alert.save_config(
        {"timeframes": {"15m": {"armed": True, "threshold": 5}}, "sound": False,
         "cooldownMinutes": 30, "junk": 1},
        path=path,
    )
    assert saved["timeframes"]["15m"] == {"armed": True, "threshold": 5.0}
    assert saved["timeframes"]["5m"]["armed"] is True  # untouched default
    assert saved["sound"] is False and saved["cooldownMinutes"] == 30.0
    assert "junk" not in saved
    assert momo_alert.load_config(path=path) == saved
    # a truncated file degrades to defaults, never raises
    path.write_text("{corrupt", encoding="utf-8")
    assert momo_alert.load_config(path=path) == momo_alert._coerce_config(None)
    # absurd threshold rejected, kept at default
    bad = momo_alert.save_config(
        {"timeframes": {"5m": {"threshold": 900}}}, path=path)
    assert bad["timeframes"]["5m"]["threshold"] == 3.0


def test_user_configs_are_isolated_per_account(tmp_path):
    """One user's change never touches another account (trader, 2026-08-30)."""
    path = tmp_path / "users.json"
    momo_alert.save_user_config("alice@x.com",
        {"timeframes": {"5m": {"threshold": 5}}}, path=path)
    momo_alert.save_user_config("bob@x.com",
        {"timeframes": {"5m": {"armed": False}}, "sound": False}, path=path)
    alice = momo_alert.load_user_config("alice@x.com", path=path)
    bob = momo_alert.load_user_config("bob@x.com", path=path)
    assert alice["timeframes"]["5m"] == {"armed": True, "threshold": 5.0}
    assert bob["timeframes"]["5m"]["armed"] is False
    assert bob["sound"] is False and alice["sound"] is True
    # unknown account -> pure defaults, and email case is normalised
    assert momo_alert.load_user_config("carol@x.com", path=path) == momo_alert._coerce_config(None)
    assert momo_alert.load_user_config("ALICE@X.COM", path=path) == alice


def test_all_user_configs_always_includes_the_default_profile(tmp_path):
    path = tmp_path / "users.json"
    momo_alert.save_user_config("alice@x.com", {}, path=path)
    everyone = momo_alert.all_user_configs(path=path)
    assert set(everyone) == {"alice@x.com", momo_alert.DEFAULT_USER}


# ---------------------------------------------------------------------------
# push window (trader, 2026-08-31: "ntfy alert momo scanner from 9:15am to
# 3:30pm est") -- gates ONLY the phone push, never detection
# ---------------------------------------------------------------------------

def _et(hour, minute):
    from datetime import datetime
    return datetime(2026, 8, 31, hour, minute, tzinfo=momo_alert.PUSH_WINDOW_ZONE)


def test_push_window_defaults_to_915_through_1530_et():
    assert momo_alert.DEFAULT_CONFIG["pushWindow"] == {"start": 555, "end": 930}
    assert momo_alert._coerce_config(None)["pushWindow"] == {"start": 555, "end": 930}


def test_push_window_coerces_garbage_to_defaults_and_keeps_partials():
    assert momo_alert._coerce_config(
        {"pushWindow": {"start": 600, "end": 900}}
    )["pushWindow"] == {"start": 600, "end": 900}
    # A partial window keeps the valid half and defaults the other.
    assert momo_alert._coerce_config(
        {"pushWindow": {"start": 600}}
    )["pushWindow"] == {"start": 600, "end": 930}
    for junk in ("9:15-15:30", None, [], 555,
                 {"start": "abc", "end": 99999},
                 {"start": -1, "end": 1440},
                 {"start": True, "end": False}):
        assert momo_alert._coerce_config({"pushWindow": junk})["pushWindow"] == {
            "start": 555, "end": 930,
        }, junk


def test_an_existing_config_gains_the_window_without_losing_its_tuning(tmp_path):
    """The trader's stored account config predates pushWindow: coercion must
    add the 9:15-15:30 defaults while keeping his topic and timeframes."""
    saved = momo_alert.save_user_config(
        "ganeshbalusait@gmail.com",
        {"ntfyTopic": "agx-secret-topic",
         "timeframes": {"5m": {"armed": True, "threshold": 4}}},
        path=tmp_path / "users.json",
    )
    assert saved["pushWindow"] == {"start": 555, "end": 930}
    assert saved["ntfyTopic"] == "agx-secret-topic"
    assert saved["timeframes"]["5m"] == {"armed": True, "threshold": 4.0}


def test_push_window_allows_inside_blocks_outside_and_pins_the_boundaries():
    config = cfg()
    assert momo_alert.push_window_allows(config, _et(12, 0)) is True
    assert momo_alert.push_window_allows(config, _et(9, 0)) is False   # before start
    assert momo_alert.push_window_allows(config, _et(16, 0)) is False  # after end
    assert momo_alert.push_window_allows(config, _et(9, 15)) is True   # start is IN
    assert momo_alert.push_window_allows(config, _et(9, 14)) is False
    assert momo_alert.push_window_allows(config, _et(15, 30)) is False  # end is OUT
    assert momo_alert.push_window_allows(config, _et(15, 29)) is True


def test_push_window_allows_survives_a_malformed_window():
    # A hand-edited config with a broken window degrades to the default
    # window rather than raising or going silent forever.
    assert momo_alert.push_window_allows({"pushWindow": "junk"}, _et(12, 0)) is True
    assert momo_alert.push_window_allows({"pushWindow": "junk"}, _et(8, 0)) is False
    assert momo_alert.push_window_allows(
        {"pushWindow": {"start": "x", "end": None}}, _et(12, 0)
    ) is True
    assert momo_alert.push_window_allows({}, _et(12, 0)) is True
