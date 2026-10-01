"""Tests for the preflight checklist engine.

The point of the module under test is that it never reports green for something
it did not measure, so these tests are mostly about the NEGATIVE cases: a check
that throws, a window that has not happened yet, a broker that did not answer, a
fix that was attempted and did not work. Every outside call is injected, so
nothing here touches the real archive, the real clock or a live broker.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import preflight
from preflight import CheckSpec, Context, HealAction, HealUnavailable

ET = preflight.EASTERN


def at(year=2026, month=9, day=1, hour=8, minute=30) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=ET)


def ctx_for(tmp_path: Path, **overrides) -> Context:
    """A Context wired entirely to fakes, with real paths under tmp_path."""
    base = dict(
        now_et=at(),
        artifacts_dir=tmp_path / "artifacts",
        history_dir=tmp_path / "preflight",
        http_get=lambda url, timeout=20.0, headers=None: {
            "ok": False, "status": 0, "seconds": 0.0, "body": "", "json": None,
            "error": "fake: nothing configured",
        },
        http_post_json=lambda url, payload, timeout=20.0: {"ok": True, "status": 200,
                                                           "seconds": 0.0, "body": "{}"},
        schwab_quotes=lambda symbols: {},
        schwab_newest_bar_epoch=lambda symbol: None,
        tradier_probe=lambda: {"ok": False, "status": 0, "message": "", "configured": False},
        alpaca_probe=lambda key, secret: {"ok": False, "status": 0, "message": "not set",
                                          "present": bool(key)},
        alpaca_env_credentials=lambda: ("", ""),
        alpaca_vault_credentials=lambda: ("", ""),
        backup_directory=lambda: None,
        api_server_state=lambda: None,
        premarket_symbols=lambda: ("AAPL", "NVDA"),
        latency_samples=4,
    )
    base.update(overrides)
    (base["artifacts_dir"]).mkdir(parents=True, exist_ok=True)
    return Context(**base)


def bars_payload(times) -> dict:
    return {"bars": [{"time": int(t), "open": 1, "high": 1, "low": 1, "close": 1,
                      "volume": 1} for t in times]}


# ----------------------------------------------------------------------
# unknown is a first-class status and NEVER becomes pass
# ----------------------------------------------------------------------


def test_worst_status_ranks_unknown_above_pass_and_below_warn():
    assert preflight.worst_status("pass", "unknown") == "unknown"
    assert preflight.worst_status("unknown", "warn") == "warn"
    assert preflight.worst_status("warn", "fail") == "fail"
    assert preflight.worst_status() == "unknown"


def test_unknown_result_is_never_counted_as_passed(tmp_path):
    spec = CheckSpec("nothing", "Nothing", lambda ctx: preflight._result(
        "nothing", "Nothing", "unknown", "did not look", "unknown, not healthy"))
    summary = preflight.run_preflight(ctx=ctx_for(tmp_path), checks=(spec,), heal=False)
    assert summary["unknown"] == 1
    assert summary["passed"] == 0
    assert summary["overall"] == "unknown"
    assert summary["checks"][0]["status"] == "unknown"


def test_unrecognised_status_string_degrades_to_unknown_not_pass(tmp_path):
    spec = CheckSpec("weird", "Weird", lambda ctx: {
        "id": "weird", "label": "Weird", "status": "OK", "measured": "", "detail": ""})
    summary = preflight.run_preflight(ctx=ctx_for(tmp_path), checks=(spec,), heal=False)
    assert summary["checks"][0]["status"] == "unknown"
    assert summary["passed"] == 0


def test_a_throwing_check_degrades_to_unknown_and_the_run_survives(tmp_path):
    def boom(ctx):
        raise RuntimeError("kaboom")

    good = CheckSpec("good", "Good", lambda ctx: preflight._result(
        "good", "Good", "pass", "1/1", "fine"))
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path), checks=(CheckSpec("bad", "Bad", boom), good), heal=False)
    assert summary["checks"][0]["status"] == "unknown"
    assert "kaboom" in summary["checks"][0]["detail"]
    assert summary["checks"][1]["status"] == "pass"
    assert summary["unknown"] == 1 and summary["passed"] == 1


def test_a_check_returning_garbage_degrades_to_unknown(tmp_path):
    spec = CheckSpec("junk", "Junk", lambda ctx: "not a dict")
    summary = preflight.run_preflight(ctx=ctx_for(tmp_path), checks=(spec,), heal=False)
    assert summary["checks"][0]["status"] == "unknown"


# ----------------------------------------------------------------------
# Day file: latest result + run count + WORST status seen that day
# ----------------------------------------------------------------------


def _summary(status: str, when: datetime, check_id: str = "demo") -> dict:
    return {
        "at": when.isoformat(),
        "date": when.date().isoformat(),
        "checks": [preflight._result(check_id, "Demo", status, "n=1", "detail")],
        "healed": [],
    }


def test_day_file_keeps_the_worst_status_even_after_a_clean_rerun(tmp_path):
    directory = tmp_path / "preflight"
    morning = at(hour=9, minute=5)
    afternoon = at(hour=16, minute=0)
    preflight.record_run(_summary("fail", morning), morning, directory)
    stored = preflight.record_run(_summary("pass", afternoon), afternoon, directory)
    assert stored["runs"] == 2
    assert stored["worst"] == "fail"
    assert stored["checkWorst"]["demo"] == "fail"
    # the LATEST result is still the clean one, carried at the top level
    assert stored["checks"][0]["status"] == "pass"
    assert stored["at"] == afternoon.isoformat()


def test_day_file_worst_treats_unknown_as_worse_than_pass(tmp_path):
    directory = tmp_path / "preflight"
    first = at(hour=7)
    second = at(hour=8)
    preflight.record_run(_summary("pass", first), first, directory)
    stored = preflight.record_run(_summary("unknown", second), second, directory)
    assert stored["worst"] == "unknown"


def test_a_history_entry_carries_its_own_measurement_stamp_and_rows(tmp_path):
    """The admin endpoint attributes an archived day by reading `at` off the
    entry. A nested shape made every archived day read as undated, so the UI
    strip would have shown thirty blank days and called that fine."""
    directory = tmp_path / "preflight"
    when = at(hour=8, minute=45)
    stored = preflight.record_run(_summary("warn", when), when, directory)
    entry = preflight.load_preflight_history(1, directory)[0]
    assert entry["at"][:10] == "2026-09-01"
    assert entry["date"] == "2026-09-01"
    assert entry["checks"][0]["id"] == "demo"
    assert entry["worst"] == "warn"
    assert entry["runs"] == stored["runs"] == 1


def test_load_preflight_history_is_newest_first(tmp_path):
    directory = tmp_path / "preflight"
    for day in (1, 2, 3):
        when = at(day=day)
        preflight.record_run(_summary("pass", when), when, directory)
    history = preflight.load_preflight_history(10, directory)
    assert [record["date"] for record in history] == ["2026-09-03", "2026-09-02", "2026-09-01"]


def test_retention_prune_boundary_keeps_the_oldest_kept_day_and_drops_the_next(tmp_path):
    directory = tmp_path / "preflight"
    directory.mkdir(parents=True)
    today = at()
    edge = (today.date() - timedelta(days=preflight.RETENTION_DAYS)).isoformat()
    older = (today.date() - timedelta(days=preflight.RETENTION_DAYS + 1)).isoformat()
    for day in (edge, older):
        (directory / (day + ".json")).write_text(json.dumps({"date": day}), encoding="utf-8")
    preflight.record_run(_summary("pass", today), today, directory)
    remaining = sorted(p.stem for p in directory.glob("*.json"))
    assert edge in remaining, "a file exactly RETENTION_DAYS old must be kept"
    assert older not in remaining, "a file one day past the window must be dropped"


def test_archiving_failure_never_kills_the_run(tmp_path, monkeypatch):
    def explode(*args, **kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(preflight, "record_run", explode)
    spec = CheckSpec("good", "Good", lambda ctx: preflight._result(
        "good", "Good", "pass", "1/1", "fine"))
    summary = preflight.run_preflight(ctx=ctx_for(tmp_path), checks=(spec,), heal=False)
    assert summary["passed"] == 1
    assert "disk gone" in summary["recordError"]


# ----------------------------------------------------------------------
# Auto-heal: the two tiers, the market-hours gate, re-verify, chronic escalation
# ----------------------------------------------------------------------


class _Flipper:
    """A check that fails until its heal is run, then passes."""

    def __init__(self, check_id="flip", healable=True):
        self.check_id = check_id
        self.healable = healable
        self.fixed = False
        self.runs = 0

    def check(self, ctx):
        self.runs += 1
        status = "pass" if self.fixed else "fail"
        return preflight._result(self.check_id, "Flip", status,
                                 "measured run %d" % self.runs, "detail",
                                 healable=self.healable)

    def heal(self, ctx):
        self.fixed = True
        return "flipped it"

    def spec(self):
        return CheckSpec(self.check_id, "Flip", self.check)


def test_a_safe_heal_runs_and_the_check_is_rerun_for_the_real_outcome(tmp_path):
    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path),
        checks=(flip.spec(),),
        healers=(HealAction("flip", "flip it", True, flip.heal),),
    )
    assert flip.runs == 2, "the check must be RE-RUN after the fix, not assumed fixed"
    assert summary["checks"][0]["status"] == "pass"
    assert summary["checks"][0]["measured"] == "measured run 2"
    assert summary["healed"][0]["statusBefore"] == "fail"
    assert summary["healed"][0]["statusAfter"] == "pass"


def test_a_heal_that_does_not_fix_it_reports_the_real_failure_not_green(tmp_path):
    calls = {"n": 0}

    def always_broken(ctx):
        calls["n"] += 1
        return preflight._result("stuck", "Stuck", "fail", "still broken (run %d)" % calls["n"],
                                 "detail", healable=True)

    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path),
        checks=(CheckSpec("stuck", "Stuck", always_broken),),
        healers=(HealAction("stuck", "try the fix", True, lambda ctx: "tried"),),
    )
    assert calls["n"] == 2
    assert summary["checks"][0]["status"] == "fail"
    assert summary["failed"] == 1
    assert summary["healed"][0]["statusAfter"] == "fail"


def test_a_non_healable_check_is_never_touched_even_if_a_healer_exists(tmp_path):
    """Tier boundary: restart / credential / data / saved-grid fixes are never
    automatic, and the runner honours the per-check healable flag."""
    ran = {"heal": False}

    def heal(ctx):
        ran["heal"] = True
        return "should never happen"

    flip = _Flipper(check_id="manual", healable=False)
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path),
        checks=(flip.spec(),),
        healers=(HealAction("manual", "restart something", True, heal),),
    )
    assert ran["heal"] is False
    assert flip.runs == 1
    assert summary["checks"][0]["status"] == "fail"
    assert summary["healed"] == []


def test_the_shipped_never_automatic_checks_have_no_healer():
    """The forbidden tier, asserted on the real registries: nothing that would
    restart a process, write a credential, delete data or edit the trader's
    saved grids may have an automatic fix wired to it."""
    healer_ids = {action.check_id for action in preflight.HEALERS}
    forbidden = {"processes_endpoints", "schwab_token", "invalid_grid_symbols",
                 "backups", "scanner_history", "chart_prewarm"}
    assert healer_ids & forbidden == set()
    by_id = {check.id: check for check in preflight.CHECKS}
    assert set(healer_ids) <= set(by_id)


def test_a_market_hours_unsafe_heal_is_deferred_and_says_so(tmp_path):
    ran = {"heal": False}

    def heal(ctx):
        ran["heal"] = True
        return "rebuilt"

    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=10, minute=15)),  # market open
        checks=(flip.spec(),),
        healers=(HealAction("flip", "queue a scanner rebuild", False, heal),),
    )
    assert ran["heal"] is False
    assert summary["checks"][0]["status"] == "fail"
    assert "was NOT run" in summary["checks"][0]["detail"]


def test_the_same_unsafe_heal_runs_outside_market_hours(tmp_path):
    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=17, minute=0)),  # after the close
        checks=(flip.spec(),),
        healers=(HealAction("flip", "queue a scanner rebuild", False, flip.heal),),
    )
    assert summary["checks"][0]["status"] == "pass"
    assert summary["healed"][0]["action"] == "flipped it"


def test_a_heal_that_cannot_run_is_reported_not_swallowed(tmp_path):
    def unavailable(ctx):
        raise HealUnavailable("only works inside the backend process")

    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path),
        checks=(flip.spec(),),
        healers=(HealAction("flip", "clear the cache", True, unavailable),),
    )
    assert flip.runs == 1
    assert summary["checks"][0]["status"] == "fail"
    assert "could not run" in summary["checks"][0]["detail"]
    assert summary["healed"] == []


def test_chronic_healing_escalates_to_warn_even_when_the_fix_worked(tmp_path):
    directory = tmp_path / "preflight"
    # Two previous run-days where the same check needed the same fix.
    for day in (30, 31):
        when = at(month=8, day=day)
        preflight.record_run(
            {"at": when.isoformat(), "checks": [], "healed": [{"id": "flip"}]}, when, directory)
    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, history_dir=directory),
        directory=directory,
        checks=(flip.spec(),),
        healers=(HealAction("flip", "flip it", True, flip.heal),),
    )
    assert summary["healed"][0]["daysRunning"] == preflight.CHRONIC_HEAL_DAYS
    assert summary["checks"][0]["status"] == "warn", "a fix needed daily is masking a defect"
    assert "3 days running" in summary["checks"][0]["detail"]


def test_a_first_time_heal_is_not_escalated(tmp_path):
    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path), checks=(flip.spec(),),
        healers=(HealAction("flip", "flip it", True, flip.heal),))
    assert summary["healed"][0]["daysRunning"] == 1
    assert summary["checks"][0]["status"] == "pass"


def test_healed_streak_ignores_days_with_no_run_rather_than_resetting(tmp_path):
    directory = tmp_path / "preflight"
    for day in (28, 31):  # Friday and Monday; the weekend has no file at all
        when = at(month=8, day=day)
        preflight.record_run(
            {"at": when.isoformat(), "checks": [], "healed": [{"id": "flip"}]}, when, directory)
    assert preflight.healed_streak("flip", at(), directory) == 2


# ----------------------------------------------------------------------
# Individual checks
# ----------------------------------------------------------------------


def test_premarket_before_seven_is_unknown_not_fail(tmp_path):
    ctx = ctx_for(tmp_path, now_et=at(hour=6, minute=30))
    outcome = preflight.check_premarket_window(ctx)
    assert outcome["status"] == "unknown"
    assert "06:30" in outcome["measured"]
    assert outcome["status"] != "fail"


def test_premarket_on_a_weekend_is_unknown_not_fail(tmp_path):
    ctx = ctx_for(tmp_path, now_et=at(day=5, hour=9))  # 2026-09-05 is a Saturday
    outcome = preflight.check_premarket_window(ctx)
    assert outcome["status"] == "unknown"
    assert "weekend" in outcome["measured"]


def test_premarket_counts_real_bars_in_the_window(tmp_path):
    window = datetime(2026, 9, 1, 4, 0, tzinfo=ET)
    full = [int((window + timedelta(minutes=i)).timestamp()) for i in range(180)]

    def fake_get(url, timeout=20.0, headers=None):
        symbol = url.split("symbol=")[1].split("&")[0]
        times = full if symbol == "AAPL" else full[:5]
        return {"ok": True, "status": 200, "seconds": 0.1, "json": bars_payload(times),
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, now_et=at(hour=8), http_get=fake_get)
    outcome = preflight.check_premarket_window(ctx)
    assert outcome["status"] == "warn"          # NVDA has only 5 bars
    assert "2/2 symbols have 04:00-07:00 bars" in outcome["measured"]
    assert "thinnest NVDA 5" in outcome["measured"]


def test_premarket_with_no_bars_fails_and_names_the_symbol(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.1, "json": bars_payload([]),
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, now_et=at(hour=8), http_get=fake_get)
    outcome = preflight.check_premarket_window(ctx)
    assert outcome["status"] == "fail"
    assert ctx.scratch["premarket_missing_symbols"] == ["AAPL", "NVDA"]


def test_tradier_revoked_is_warn_not_fail(tmp_path):
    probe = {
        "ok": False, "status": 401, "configured": True,
        "message": '{"fault":{"faultstring":"Invalid API call as no apiproduct match found",'
                   '"detail":{"errorcode":"keymanagement.service.access_token_not_approved"}}}',
    }
    outcome = preflight.check_tradier(ctx_for(tmp_path, tradier_probe=lambda: probe))
    assert outcome["status"] == "warn"
    assert "EXPECTED" in outcome["detail"]
    assert "Alpaca fallback" in outcome["detail"]


def test_tradier_never_fails_even_on_an_unrecognised_error(tmp_path):
    probe = {"ok": False, "status": 500, "configured": True, "message": "boom"}
    assert preflight.check_tradier(ctx_for(tmp_path, tradier_probe=lambda: probe))["status"] == "warn"


def test_tradier_working_again_is_a_pass(tmp_path):
    probe = {"ok": True, "status": 200, "configured": True, "message": "{}"}
    assert preflight.check_tradier(ctx_for(tmp_path, tradier_probe=lambda: probe))["status"] == "pass"


def test_alpaca_reports_which_key_actually_authenticates(tmp_path):
    def probe(key, secret):
        return {"ok": key == "VAULTKEY", "status": 200 if key == "VAULTKEY" else 401,
                "message": "", "present": True, "error": "" if key == "VAULTKEY" else "HTTP 401"}

    ctx = ctx_for(
        tmp_path,
        alpaca_env_credentials=lambda: ("DEADKEY1", "s"),
        alpaca_vault_credentials=lambda: ("VAULTKEY", "s"),
        alpaca_probe=probe,
    )
    outcome = preflight.check_alpaca_credentials(ctx)
    assert outcome["status"] == "pass"
    assert "Settings key VAULTK... authenticates" in outcome["measured"]
    assert "DEADKE... rejected" in outcome["measured"]


def test_alpaca_warns_when_only_the_env_key_works(tmp_path):
    def probe(key, secret):
        return {"ok": key == "ENVKEY", "status": 200 if key == "ENVKEY" else 401,
                "message": "", "present": True, "error": ""}

    ctx = ctx_for(tmp_path, alpaca_env_credentials=lambda: ("ENVKEY", "s"),
                  alpaca_vault_credentials=lambda: ("VAULTKEY", "s"), alpaca_probe=probe)
    assert preflight.check_alpaca_credentials(ctx)["status"] == "warn"


def test_alpaca_unreachable_is_unknown_not_pass(tmp_path):
    ctx = ctx_for(tmp_path, alpaca_env_credentials=lambda: ("ENVKEY", "s"),
                  alpaca_vault_credentials=lambda: ("VAULTKEY", "s"),
                  alpaca_probe=lambda key, secret: {"ok": False, "status": 0,
                                                    "message": "", "present": True,
                                                    "error": "URLError"})
    assert preflight.check_alpaca_credentials(ctx)["status"] == "unknown"


def test_chart_freshness_measures_the_lag_against_the_broker(tmp_path):
    served = int(at(hour=8, minute=20).timestamp())
    broker = int(at(hour=8, minute=29).timestamp())

    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([served - 60, served]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL",), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: float(broker))
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "warn"
    assert "AAPL 9.0 min" in outcome["measured"]
    assert ctx.scratch["stale_chart_symbols"] == ["AAPL"]


def test_chart_freshness_does_not_scream_when_the_market_is_shut(tmp_path):
    """Outside market hours both sides sit on the same last bar, so comparing
    served-vs-broker (never served-vs-wall-clock) measures zero lag."""
    close = int(datetime(2026, 8, 29, 15, 59, tzinfo=ET).timestamp())

    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2, "json": bars_payload([close]),
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, now_et=at(day=5, hour=22), freshness_symbols=("AAPL",),
                  http_get=fake_get, schwab_newest_bar_epoch=lambda symbol: float(close))
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "pass"
    assert "AAPL 0.0 min" in outcome["measured"]


def test_chart_freshness_is_unknown_when_the_broker_does_not_answer(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL",), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: None)
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "unknown"
    assert "the broker stayed silent" in outcome["measured"]


def test_chart_open_speed_uses_the_traders_threshold_of_fifteen_seconds(tmp_path):
    timings = {"XOM": 16.0, "SNAP": 0.2, "IBKR": 0.2}

    def fake_get(url, timeout=20.0, headers=None):
        symbol = url.split("symbol=")[1].split("&")[0]
        return {"ok": True, "status": 200, "seconds": timings[symbol],
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, chart_open_symbols=tuple(timings), http_get=fake_get)
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "fail"
    assert outcome["measured"].startswith("XOM 16.0s, SNAP 0.2s, IBKR 0.2s")
    assert "worst 16.0s" in outcome["measured"]

    timings["XOM"] = 6.0
    ctx = ctx_for(tmp_path, chart_open_symbols=tuple(timings), http_get=fake_get)
    assert preflight.check_chart_open_speed(ctx)["status"] == "warn"

    timings["XOM"] = 0.5
    ctx = ctx_for(tmp_path, chart_open_symbols=tuple(timings), http_get=fake_get)
    fast = preflight.check_chart_open_speed(ctx)
    assert fast["status"] == "pass"
    assert fast["measured"] == "XOM 0.5s, SNAP 0.2s, IBKR 0.2s (worst 0.5s)"


def test_a_fast_chart_with_no_candles_is_a_fail_not_a_pass(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.1, "json": bars_payload([]),
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, chart_open_symbols=("AAPL",), http_get=fake_get)
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "fail"
    assert "0 candles" in outcome["measured"]


def test_a_typod_board_ticker_does_not_make_the_speed_row_fail(tmp_path):
    """Found on the first live run: a saved layout held GOOGLE, so the chart-open
    row failed with "0 candles" while charts were opening in 0.4s. That is the
    saved-layouts row's finding; the speed row must report the seconds."""
    def fake_get(url, timeout=20.0, headers=None):
        symbol = url.split("symbol=")[1].split("&")[0]
        payload = bars_payload([] if symbol == "GOOGLE" else [int(time.time())])
        return {"ok": True, "status": 200, "seconds": 0.4, "json": payload,
                "body": "", "error": ""}

    ctx = ctx_for(
        tmp_path,
        chart_open_symbols=("GOOGLE", "AAPL", "NVDA", "TSLA"),
        http_get=fake_get,
        schwab_quotes=lambda symbols: {s: {"last_price": 1.0} for s in symbols
                                       if s != "GOOGLE"},
    )
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "pass"
    assert outcome["measured"].startswith("AAPL 0.4s, NVDA 0.4s, TSLA 0.4s")
    assert "skipped GOOGLE" in outcome["measured"]


def test_chart_freshness_also_skips_a_ticker_the_broker_does_not_know(tmp_path):
    now = int(at(hour=8, minute=29).timestamp())

    def fake_get(url, timeout=20.0, headers=None):
        symbol = url.split("symbol=")[1].split("&")[0]
        payload = bars_payload([] if symbol == "GOOGLE" else [now])
        return {"ok": True, "status": 200, "seconds": 0.2, "json": payload,
                "body": "", "error": ""}

    ctx = ctx_for(
        tmp_path, freshness_symbols=("GOOGLE", "AAPL"), http_get=fake_get,
        schwab_quotes=lambda symbols: {s: {} for s in symbols if s != "GOOGLE"},
        schwab_newest_bar_epoch=lambda symbol: float(now),
    )
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "pass"
    assert "GOOGLE" not in outcome["measured"]


def test_an_unreachable_broker_never_shrinks_what_gets_measured(tmp_path):
    """broker_known falls back to "assume all known" rather than silently
    dropping symbols from the measurement."""
    ctx = ctx_for(tmp_path, schwab_quotes=lambda symbols: {})
    known, resolved = ctx.broker_known(["AAPL", "GOOGLE"])
    assert known == {"AAPL", "GOOGLE"}
    assert resolved is False


def test_latency_spikes_fail_on_a_bad_max_even_with_a_healthy_median(tmp_path):
    """The 2026-09-01 08:31 measurement: median 0.06s, max 7.73s. A median-only
    check called that green; this is why check 14 exists separately."""
    samples = iter([0.06, 0.05, 7.73, 0.07])

    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": next(samples), "json": {},
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, http_get=fake_get)
    median_row = preflight.check_response_latency(ctx)
    spike_row = preflight.check_latency_spikes(ctx)
    assert median_row["status"] == "pass"
    assert "median 0.07s" in median_row["measured"]
    assert spike_row["status"] == "fail"
    assert "max 7.73s" in spike_row["measured"]


def test_latency_is_unknown_when_nothing_answered(tmp_path):
    ctx = ctx_for(tmp_path)  # default fake http_get never answers
    assert preflight.check_response_latency(ctx)["status"] == "unknown"
    assert preflight.check_latency_spikes(ctx)["status"] == "unknown"


def test_invalid_grid_symbols_finds_the_google_typo(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "chart_grids.json").write_text(json.dumps({
        "version": 2,
        "users": {"trader@example.com": {"My layout": {
            "savedAt": "2026-08-25T13:32:49.151Z",
            "workspace": {"panels": [{"symbol": "AAPL"}, {"symbol": "GOOGLE"}]},
        }}},
    }), encoding="utf-8")
    ctx = ctx_for(tmp_path, artifacts_dir=artifacts,
                  schwab_quotes=lambda symbols: {"AAPL": {"last_price": 1.0}})
    outcome = preflight.check_invalid_grid_symbols(ctx)
    # FAIL, not warn: a ticker the broker does not know burns a rebuild every
    # 30 seconds for as long as it stays saved, and the fix is one human edit.
    assert outcome["status"] == "fail"
    assert "GOOGLE (My layout panel 2)" in outcome["measured"]
    assert outcome["healable"] is False, "the trader's saved grids are never edited automatically"


def test_invalid_grid_symbols_is_unknown_when_the_broker_is_silent(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "chart_grids.json").write_text(json.dumps({
        "users": {"t@e.com": {"L": {"workspace": {"panels": [{"symbol": "AAPL"}]}}}},
    }), encoding="utf-8")
    ctx = ctx_for(tmp_path, artifacts_dir=artifacts, schwab_quotes=lambda symbols: {})
    assert preflight.check_invalid_grid_symbols(ctx)["status"] == "unknown"


def test_backups_measure_the_age_of_the_newest_archive(tmp_path):
    drive = tmp_path / "AGX-Backups"
    drive.mkdir()
    recent = drive / "trades-20260901.db.zip"
    recent.write_text("x", encoding="utf-8")
    ctx = ctx_for(tmp_path, backup_directory=lambda: drive)
    outcome = preflight.check_backups(ctx)
    assert outcome["status"] == "pass"
    assert "newest trades-20260901.db.zip 0.0 h old" in outcome["measured"]

    old = time.time() - (70 * 3600)
    import os as _os
    _os.utime(recent, (old, old))
    assert preflight.check_backups(ctx_for(tmp_path, backup_directory=lambda: drive))["status"] == "fail"


def test_a_file_written_a_moment_ago_never_reports_a_negative_age(tmp_path):
    """Filesystem mtimes round a hair ahead of time.time(); the archive age
    printed "-0.0 h old" on a real run, which reads as a broken measurement."""
    import os as _os

    drive = tmp_path / "AGX-Backups"
    drive.mkdir()
    fresh = drive / "trades-20260901.db.zip"
    fresh.write_text("x", encoding="utf-8")
    ahead = time.time() + 5
    _os.utime(fresh, (ahead, ahead))
    measured = preflight.check_backups(ctx_for(tmp_path, backup_directory=lambda: drive))["measured"]
    assert measured.endswith("0.0 h old")
    assert "-0.0 h old" not in measured


def test_backups_to_a_local_only_folder_are_a_warning_not_a_pass(tmp_path):
    drive = tmp_path / "AGX-Backups-LOCAL-ONLY" / "AGX-Backups"
    drive.mkdir(parents=True)
    (drive / "trades-20260901.db.zip").write_text("x", encoding="utf-8")
    outcome = preflight.check_backups(ctx_for(tmp_path, backup_directory=lambda: drive))
    assert outcome["status"] == "warn"
    assert "not to Google Drive" in outcome["detail"]


def test_backups_with_no_folder_at_all_is_a_fail(tmp_path):
    assert preflight.check_backups(ctx_for(tmp_path))["status"] == "fail"


def test_processes_check_names_who_did_not_answer(tmp_path):
    answered = {"3002", "3010", "3001", "4173"}

    def fake_get(url, timeout=20.0, headers=None):
        port = url.split(":")[2].split("/")[0]
        if port in answered:
            return {"ok": True, "status": 200, "seconds": 0.05, "json": {}, "body": "", "error": ""}
        return {"ok": False, "status": 0, "seconds": 0.0, "json": None, "body": "",
                "error": "ConnectionRefusedError"}

    outcome = preflight.check_processes_endpoints(ctx_for(tmp_path, http_get=fake_get))
    assert outcome["status"] == "warn"
    assert "4/5 answered" in outcome["measured"]
    assert "vite :5173" in outcome["measured"]

    answered.discard("3002")
    outcome = preflight.check_processes_endpoints(ctx_for(tmp_path, http_get=fake_get))
    assert outcome["status"] == "fail"
    assert outcome["healable"] is False, "nothing here restarts a process automatically"


def test_prewarm_counters_that_do_not_reconcile_are_a_warning(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.1, "body": "", "error": "", "json": {
            "chartPrewarm": {"candidates": 58, "warm": 40, "behind": 5, "cold": 0,
                             "skipped": 0, "enabled": True,
                             "lastCycleAt": at(hour=8, minute=29).isoformat()}}}

    outcome = preflight.check_chart_prewarm(ctx_for(tmp_path, http_get=fake_get))
    assert outcome["status"] == "warn"
    assert "DOES NOT reconcile, 13 unaccounted" in outcome["measured"]


def test_ages_are_measured_at_probe_time_not_run_start(tmp_path):
    """A run takes 15-30s. Measuring ages against the run's START made the live
    run print "last cycle -2s ago", which is nonsense."""
    ctx = ctx_for(tmp_path)
    ctx._started -= 45.0  # pretend 45 seconds of checks have already happened
    assert (ctx.now() - ctx.now_et).total_seconds() == pytest.approx(45.0, abs=1.0)


def test_a_cycle_stamped_a_moment_in_the_future_does_not_print_a_negative_age(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.1, "body": "", "error": "", "json": {
            "chartPrewarm": {"candidates": 1, "warm": 1, "behind": 0, "cold": 0,
                             "skipped": 0, "enabled": True,
                             "lastCycleAt": (at() + timedelta(seconds=1)).isoformat()}}}

    outcome = preflight.check_chart_prewarm(ctx_for(tmp_path, http_get=fake_get))
    assert "-" not in outcome["measured"].split("last cycle")[1]
    assert outcome["status"] == "pass"


def test_prewarm_missing_from_health_is_unknown_not_pass(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.1, "json": {}, "body": "", "error": ""}

    assert preflight.check_chart_prewarm(ctx_for(tmp_path, http_get=fake_get))["status"] == "unknown"


def test_momx_tape_age_is_only_graded_during_market_hours(tmp_path):
    def board(now):
        return {"ok": True, "status": 200, "seconds": 0.1, "body": "", "error": "", "json": {
            "generatedAt": (now - timedelta(seconds=30)).isoformat(),
            "tapeAsOf": (now - timedelta(hours=6)).isoformat(),
            "universeCount": 2,
            "rows": [{"symbol": "AAPL", "last": 1.0}, {"symbol": "NVDA", "last": 2.0}],
            "errors": {},
        }}

    open_now = at(hour=10)
    shut_now = at(hour=20)
    during = preflight.check_momx_scanner(ctx_for(
        tmp_path, now_et=open_now, http_get=lambda url, timeout=20.0, headers=None: board(open_now)))
    after = preflight.check_momx_scanner(ctx_for(
        tmp_path, now_et=shut_now, http_get=lambda url, timeout=20.0, headers=None: board(shut_now)))
    assert during["status"] == "warn"
    assert after["status"] == "pass"
    assert "tape 360.0 min old" in after["measured"], "the number is still reported either way"


def test_momx_worker_silence_is_unknown_not_pass(tmp_path):
    assert preflight.check_momx_scanner(ctx_for(tmp_path))["status"] == "unknown"


def test_scanner_history_before_six_is_unknown(tmp_path):
    outcome = preflight.check_scanner_history(ctx_for(tmp_path, now_et=at(hour=5, minute=30)))
    assert outcome["status"] == "unknown"


def test_scanner_history_measures_row_counts_and_write_age(tmp_path):
    artifacts = tmp_path / "artifacts"
    day = "2026-09-01"
    premarket = artifacts / "premarket_scanner_history"
    premarket.mkdir(parents=True)
    (premarket / (day + ".json")).write_text(
        json.dumps({"date": day, "rows": {"AAPL": {}, "NVDA": {}}}), encoding="utf-8")
    momx = artifacts / "momx_history" / "Mag7"
    momx.mkdir(parents=True)
    (momx / (day + ".json")).write_text(
        json.dumps({"symbols": {"TSLA": {}}}), encoding="utf-8")
    outcome = preflight.check_scanner_history(ctx_for(tmp_path, artifacts_dir=artifacts))
    assert outcome["status"] == "pass"
    assert "premarket 2 symbols" in outcome["measured"]
    assert "momx Mag7 1 symbols" in outcome["measured"]


def test_scanner_history_missing_entirely_is_a_fail(tmp_path):
    outcome = preflight.check_scanner_history(ctx_for(tmp_path, now_et=at(hour=9)))
    assert outcome["status"] == "fail"


def test_schwab_quote_failure_is_a_fail_with_a_human_action(tmp_path):
    outcome = preflight.check_schwab_token(ctx_for(tmp_path))
    assert outcome["status"] == "fail"
    assert outcome["healable"] is False, "credentials are never written automatically"
    assert "Settings" in outcome["detail"]


def test_schwab_quote_round_trip_reports_the_price(tmp_path):
    ctx = ctx_for(tmp_path, schwab_quotes=lambda symbols: {
        "AAPL": {"last_price": 317.49}, "SPY": {"last_price": 600.0}})
    outcome = preflight.check_schwab_token(ctx)
    assert outcome["status"] == "pass"
    assert "2/2 quotes round-tripped - AAPL 317.49" in outcome["measured"]


# ----------------------------------------------------------------------
# Shape and reporting
# ----------------------------------------------------------------------


def test_every_shipped_check_returns_the_agreed_shape(tmp_path):
    ctx = ctx_for(tmp_path)
    summary = preflight.run_preflight(ctx=ctx, heal=False, record=False)
    assert len(summary["checks"]) == len(preflight.CHECKS) == 14
    for row in summary["checks"]:
        assert set(row) >= {"id", "label", "status", "measured", "detail", "healable", "critical"}
        assert row["status"] in preflight.STATUS_ORDER
        assert row["measured"], "every check must report a measurement, not just a verdict"
        assert row["detail"], "every non-pass row must say what to do about it"
    assert [c.id for c in preflight.CHECKS] == [r["id"] for r in summary["checks"]]


def test_format_report_prints_numbers_for_every_row(tmp_path):
    spec = CheckSpec("demo", "Demo", lambda ctx: preflight._result(
        "demo", "Demo", "fail", "XOM 22.0s (worst 22.0s)", "open fewer charts"))
    summary = preflight.run_preflight(ctx=ctx_for(tmp_path), checks=(spec,), heal=False)
    text = preflight.format_report(summary)
    assert "XOM 22.0s" in text
    assert "-> open fewer charts" in text
    assert "fail 1" in text


def test_healers_only_reference_real_checks():
    ids = {check.id for check in preflight.CHECKS}
    for action in preflight.HEALERS:
        assert action.check_id in ids
        assert action.description
        assert callable(action.run)


# ----------------------------------------------------------------------
# REVIEW FIXES (2026-09-01). Each test below pins one finding that was
# reported by a reviewer and confirmed by injection. They are grouped by the
# defect SHAPE rather than by function, because the shape is what recurs: a
# narrow truthful observation being graded as if it were a wide one.
# ----------------------------------------------------------------------


def test_chart_freshness_fails_when_a_chart_serves_zero_candles(tmp_path):
    """BLOCKER: this row read PASS while its own text named a blank chart."""

    def fake_get(url, timeout=20.0, headers=None):
        if "AAPL" in url:
            return {"ok": True, "status": 200, "seconds": 0.2,
                    "json": {"bars": []}, "body": "", "error": ""}
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL", "NVDA", "TSLA"),
                  http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: time.time())
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "fail"
    assert "AAPL served NO candles at all" in outcome["measured"]
    # and the level symbols are still measured, so the number survives
    assert "NVDA 0.0 min" in outcome["measured"]


def test_chart_freshness_is_unknown_when_one_symbol_cannot_be_compared(tmp_path):
    """A silent broker on ONE symbol must not be absorbed by a level other."""
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL", "NVDA"), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: None if symbol == "AAPL" else time.time())
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "unknown"
    assert "AAPL could not be compared" in outcome["measured"]
    assert "NVDA 0.0 min" in outcome["measured"]


def test_chart_freshness_fails_when_the_chart_request_itself_fails(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        if "AAPL" in url:
            return {"ok": False, "status": 500, "seconds": 0.1, "json": None,
                    "body": "", "error": "HTTP 500"}
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL", "NVDA"), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: time.time())
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "fail"
    assert "chart request failed for AAPL" in outcome["measured"]


def test_chart_freshness_still_passes_when_every_symbol_is_level(tmp_path):
    """The strengthening must not turn a genuinely healthy board red."""
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL", "NVDA"), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: time.time())
    outcome = preflight.check_chart_freshness(ctx)
    assert outcome["status"] == "pass"


def test_a_blank_chart_is_handed_to_the_healer(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2, "json": {"bars": []},
                "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=("AAPL",), http_get=fake_get)
    preflight.check_chart_freshness(ctx)
    assert ctx.scratch["stale_chart_symbols"] == ["AAPL"]


# ---- the sample discloses its own scope, and rotates -------------------


def test_chart_sampling_states_how_many_of_the_board_it_looked_at(tmp_path):
    board = ["S%02d" % i for i in range(12)]
    probed = []

    def fake_get(url, timeout=20.0, headers=None):
        probed.append(url.split("symbol=")[1].split("&")[0])
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, freshness_symbols=tuple(board), http_get=fake_get,
                  schwab_newest_bar_epoch=lambda symbol: time.time())
    outcome = preflight.check_chart_freshness(ctx)
    assert len(probed) == 3
    assert "sampled 3 of 12 board symbols" in outcome["measured"]
    assert "rotating daily" in outcome["measured"]


def test_the_chart_sample_rotates_so_the_whole_board_is_covered(tmp_path):
    board = ["S%02d" % i for i in range(12)]

    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.2,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    seen = set()
    for day in range(1, 5):
        ctx = ctx_for(tmp_path, now_et=at(month=9, day=day), freshness_symbols=tuple(board),
                      http_get=fake_get, schwab_newest_bar_epoch=lambda symbol: time.time())
        picked, note = preflight._board_sample(ctx, board, set(board))
        seen.update(picked)
        assert len(picked) == 3
    # four consecutive run-days cover all twelve names, so a per-symbol freeze
    # (the SMCI/MSTR shape) cannot hide behind a fixed first-three sample
    assert seen == set(board)


def test_a_short_board_reports_no_sampling_note(tmp_path):
    ctx = ctx_for(tmp_path)
    picked, note = preflight._board_sample(ctx, ["AAPL", "NVDA"], {"AAPL", "NVDA"})
    assert picked == ["AAPL", "NVDA"]
    assert note == ""


# ---- chart_open_speed -------------------------------------------------


def test_chart_open_speed_fails_when_more_charts_failed_than_opened(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        if "AAPL" in url:
            return {"ok": True, "status": 200, "seconds": 0.3,
                    "json": bars_payload([int(time.time())]), "body": "", "error": ""}
        return {"ok": False, "status": 500, "seconds": 0.1, "json": None,
                "body": "", "error": "HTTP 500"}

    ctx = ctx_for(tmp_path, chart_open_symbols=("AAPL", "NVDA", "TSLA"), http_get=fake_get)
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "fail"
    assert "2 of 3 charts would not open" in outcome["detail"]


def test_chart_open_speed_warns_when_a_minority_failed(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        if "TSLA" in url:
            return {"ok": False, "status": 500, "seconds": 0.1, "json": None,
                    "body": "", "error": "HTTP 500"}
        return {"ok": True, "status": 200, "seconds": 0.3,
                "json": bars_payload([int(time.time())]), "body": "", "error": ""}

    ctx = ctx_for(tmp_path, chart_open_symbols=("AAPL", "NVDA", "TSLA"), http_get=fake_get)
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "warn"


def test_chart_open_speed_fails_when_nothing_opened_at_all(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": False, "status": 500, "seconds": 0.1, "json": None,
                "body": "", "error": "HTTP 500"}

    ctx = ctx_for(tmp_path, chart_open_symbols=("AAPL", "NVDA"), http_get=fake_get)
    outcome = preflight.check_chart_open_speed(ctx)
    assert outcome["status"] == "fail"


# ---- prewarm / archives / layouts -------------------------------------


def test_chart_prewarm_warns_when_it_has_nothing_queued(tmp_path):
    def fake_get(url, timeout=20.0, headers=None):
        return {"ok": True, "status": 200, "seconds": 0.0, "body": "", "error": "",
                "json": {"chartPrewarm": {"candidates": 0, "warm": 0, "behind": 0,
                                          "cold": 0, "skipped": 0,
                                          "lastCycleAt": at().isoformat()}}}

    outcome = preflight.check_chart_prewarm(ctx_for(tmp_path, http_get=fake_get))
    assert outcome["status"] == "warn"
    assert "0 candidates" in outcome["measured"]
    assert "NOTHING queued" in outcome["detail"]


def test_scanner_history_says_so_when_no_momx_board_archive_exists(tmp_path):
    artifacts = tmp_path / "artifacts"
    day = at(hour=8).date().isoformat()
    root = artifacts / "premarket_scanner_history"
    root.mkdir(parents=True, exist_ok=True)
    (root / (day + ".json")).write_text(json.dumps({"rows": {"AAPL": {}, "NVDA": {}}}),
                                        encoding="utf-8")
    outcome = preflight.check_scanner_history(ctx_for(tmp_path, artifacts_dir=artifacts))
    assert outcome["status"] == "warn"
    assert "no momx board archive was found at all" in outcome["measured"]


def test_saved_layouts_that_parse_to_nothing_are_unknown_not_pass(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "chart_grids.json").write_text(json.dumps({
        "users": {"trader@example.com": {"Mags": {
            "savedAt": "2026-08-25T13:32:49.151Z",
            # the shape MOVED: charts, not panels
            "workspace": {"charts": [{"symbol": "GOOGLE"}]},
        }}},
    }), encoding="utf-8")
    outcome = preflight.check_invalid_grid_symbols(ctx_for(tmp_path, artifacts_dir=artifacts))
    assert outcome["status"] == "unknown"
    assert "no readable panel symbols" in outcome["measured"]


def test_no_saved_layouts_at_all_is_still_a_pass(tmp_path):
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / "chart_grids.json").write_text(json.dumps({"users": {}}), encoding="utf-8")
    outcome = preflight.check_invalid_grid_symbols(ctx_for(tmp_path, artifacts_dir=artifacts))
    assert outcome["status"] == "pass"


# ---- the alpaca credential CACHE, not just the key --------------------


class _FakeState:
    def __init__(self, cache):
        self._owner_alpaca_client_cache = cache
        self._owner_alpaca_client_cache_at = time.monotonic() - 120.0


def test_a_working_alpaca_key_with_a_poisoned_cache_is_not_green(tmp_path):
    """The documented incident: a WORKING key the backend refuses to retry."""
    ctx = ctx_for(
        tmp_path,
        alpaca_vault_credentials=lambda: ("PKFZAQ", "s"),
        alpaca_probe=lambda key, secret: {"ok": bool(key), "status": 200 if key else 0,
                                          "message": "", "present": bool(key)},
        api_server_state=lambda: _FakeState(False),
    )
    outcome = preflight.check_alpaca_credentials(ctx)
    assert outcome["status"] == "warn"
    assert "remembered FAILURE" in outcome["measured"]
    assert outcome["healable"] is True


def test_a_working_alpaca_key_with_a_clean_cache_passes(tmp_path):
    ctx = ctx_for(
        tmp_path,
        alpaca_vault_credentials=lambda: ("PKFZAQ", "s"),
        alpaca_probe=lambda key, secret: {"ok": bool(key), "status": 200 if key else 0,
                                          "message": "", "present": bool(key)},
        api_server_state=lambda: _FakeState(None),
    )
    outcome = preflight.check_alpaca_credentials(ctx)
    assert outcome["status"] == "pass"
    assert "holds no remembered failure" in outcome["measured"]


# ---- heals -------------------------------------------------------------


def test_the_premarket_heal_is_capped_at_three_requests(tmp_path):
    calls = []

    def fake_get(url, timeout=20.0, headers=None):
        calls.append(url)
        return {"ok": True, "status": 200, "seconds": 0.1, "json": {}, "body": "", "error": ""}

    ctx = ctx_for(tmp_path, http_get=fake_get)
    ctx.scratch["premarket_missing_symbols"] = ["A", "B", "C", "D", "E", "F", "G", "H", "I"]
    sentence = preflight.heal_premarket_window(ctx)
    assert len(calls) == 3
    assert "capped at 3 per run" in sentence
    assert "D, E, F, G, H, I" in sentence


def test_a_crashing_recheck_cannot_launder_a_fail_into_an_unknown(tmp_path):
    state = {"runs": 0}

    def flaky(ctx):
        state["runs"] += 1
        if state["runs"] == 1:
            return preflight._result("flip", "Flip", "fail", "1 broken", "broken",
                                     healable=True)
        raise ZeroDivisionError("the re-check exploded")

    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=17)),
        checks=(CheckSpec("flip", "Flip", flaky),),
        healers=(HealAction("flip", "do a thing", True, lambda ctx: "did a thing"),),
    )
    assert summary["checks"][0]["status"] == "fail"
    assert summary["failed"] == 1
    assert "could not run, so the earlier fail stands" in summary["checks"][0]["detail"]


def test_the_unsafe_heal_gate_closes_before_the_bell(tmp_path):
    """09:29 was inside the old gate: a rebuild started then runs past 09:31."""
    assert preflight._is_market_hours(at(hour=9, minute=29)) is False
    assert preflight._is_heal_deferred(at(hour=9, minute=29)) is True
    assert preflight._is_heal_deferred(at(hour=9, minute=14)) is False
    assert preflight._is_heal_deferred(at(hour=16, minute=0)) is False

    ran = {"heal": False}

    def heal(ctx):
        ran["heal"] = True
        return "rebuilt"

    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=9, minute=29)),
        checks=(flip.spec(),),
        healers=(HealAction("flip", "queue a scanner rebuild", False, heal),),
    )
    assert ran["heal"] is False
    assert summary["checks"][0]["status"] == "fail"
    # marketHours still says what the CLOCK says - only the heal gate is wider
    assert summary["marketHours"] is False


# ---- the day's memory --------------------------------------------------


def test_a_fault_that_was_auto_fixed_is_still_recorded_for_the_day(tmp_path):
    """The 09:05 failure must survive a heal AND a clean 16:00 re-run."""
    flip = _Flipper()
    directory = tmp_path / "preflight"
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=17)),
        checks=(flip.spec(),),
        healers=(HealAction("flip", "flip it", True, flip.heal),),
        directory=directory,
    )
    assert summary["checks"][0]["status"] == "pass"     # the fix really worked
    stored = json.loads((directory / (at().date().isoformat() + ".json")).read_text("utf-8"))
    assert stored["overall"] == "pass"                  # latest run: clean
    assert stored["worst"] == "fail"                    # the day REMEMBERS
    assert stored["checkWorst"]["flip"] == "fail"
    assert stored["healedIds"] == ["flip"]


def test_prune_never_deletes_a_file_that_is_not_named_like_a_day(tmp_path):
    directory = tmp_path / "preflight"
    directory.mkdir(parents=True, exist_ok=True)
    for name in ("2026-09-01.json", "2026-07-01.json", "1-important.json",
                 "chart_grids.json", "2025-backup.json"):
        (directory / name).write_text("{}", encoding="utf-8")
    preflight._prune(directory, at())
    survivors = sorted(p.name for p in directory.glob("*.json"))
    assert survivors == ["1-important.json", "2025-backup.json", "2026-09-01.json",
                         "chart_grids.json"]


def test_history_only_reads_files_named_like_a_day(tmp_path):
    directory = tmp_path / "preflight"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "2026-09-01.json").write_text(json.dumps({"at": "2026-09-01T08:45:00"}),
                                               encoding="utf-8")
    (directory / "chart_grids.json").write_text(json.dumps({"users": {}}), encoding="utf-8")
    records = preflight.load_preflight_history(30, directory)
    assert [r["date"] for r in records] == ["2026-09-01"]


def test_a_heal_that_changed_nothing_does_not_mark_the_row_as_fixed(tmp_path):
    """`healed` on a check means REPAIRED, not "a repair was attempted"."""
    def always_fails(ctx):
        return preflight._result("stuck", "Stuck", "fail", "1 broken", "broken",
                                 healable=True)

    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=17)),
        checks=(CheckSpec("stuck", "Stuck", always_fails),),
        healers=(HealAction("stuck", "poke it", True, lambda ctx: "poked it"),),
    )
    row = summary["checks"][0]
    assert row["status"] == "fail"
    assert row["healed"] is False, "a fix that fixed nothing is not a fix"
    assert row["healAttempted"] is True, "but it must be visible that one was tried"
    assert row["healAction"] == "poked it"
    assert summary["healed"][0]["statusAfter"] == "fail"


def test_a_heal_that_worked_marks_the_row_as_fixed(tmp_path):
    flip = _Flipper()
    summary = preflight.run_preflight(
        ctx=ctx_for(tmp_path, now_et=at(hour=17)),
        checks=(flip.spec(),),
        healers=(HealAction("flip", "flip it", True, flip.heal),),
    )
    row = summary["checks"][0]
    assert row["status"] == "pass"
    assert row["healed"] is True
    assert row["healAttempted"] is True
