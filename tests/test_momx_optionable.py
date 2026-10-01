"""momx/optionable.py + the service's options-only gate (2026-09-26).

"In scanner only show which ticker have option only. Don't scan which have no
option - no guess work." Every answer comes from a real option chain read;
anything that is not a clean read stays UNKNOWN and is never stored as "no".
"""
from __future__ import annotations

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from momx import optionable as opt

ET = ZoneInfo("America/New_York")
TODAY = date(2026, 9, 26)
SATURDAY = datetime(2026, 9, 26, 12, 0, tzinfo=ET)


def chain(expiries, **extra):
    rows = [{"expiry": e, "strike": 10, "side": "CALL"} for e in expiries]
    return {"live": True, "source": "Schwab/TOS option chain", "errors": [], "selectedExpiryChainRows": rows,
            "expiries": expiries[:1], **extra}


NO_OPTIONS = {"live": False, "cached": True, "refreshing": False, "source": "Schwab/TOS option chain",
              "errors": [], "callRows": [], "putRows": [], "selectedExpiryChainRows": []}
WARMING = {"live": False, "warming": True, "refreshing": True, "source": "Schwab/TOS option chain",
           "errors": [], "callRows": [], "putRows": [], "selectedExpiryChainRows": []}
WEEKLY = ["2026-10-02", "2026-10-09", "2026-10-16", "2026-10-23"]
MONTHLY = ["2026-10-16"]


# ---------------------------------------------------------------- classify

def test_rows_mean_options_and_expiries_come_from_the_rows_not_the_expiries_field():
    verdict = opt.classify(chain(WEEKLY), TODAY)
    assert verdict == {"hasOptions": True, "expiries": WEEKLY, "weeklies": True}


def test_monthly_only_chain_is_options_without_weeklies():
    assert opt.classify(chain(MONTHLY), TODAY) == {"hasOptions": True, "expiries": MONTHLY, "weeklies": False}


def test_a_one_expiry_first_paint_payload_says_options_but_not_weeklies():
    verdict = opt.classify(chain(["2026-10-02"], frontExpiryOnly=True), TODAY)
    assert verdict == {"hasOptions": True, "expiries": None, "weeklies": None}


def test_a_clean_schwab_answer_with_no_contracts_is_no_options():
    assert opt.classify(NO_OPTIONS, TODAY) == {"hasOptions": False}


@pytest.mark.parametrize("payload", [
    WARMING,                                                    # cold / outage / invalid symbol
    {**NO_OPTIONS, "errors": [{"error": "boom"}]},             # a failed build
    {**NO_OPTIONS, "source": "Tradier option chain (Schwab/TOS fallback)"},
    {**NO_OPTIONS, "errors": None},
    {},
    None,
    "not json",
])
def test_anything_but_a_clean_read_is_unknown_never_no(payload):
    assert opt.classify(payload, TODAY) is None


def test_weekly_rule_two_expiries_within_21_days():
    assert opt.weekly_from_expiries(["2026-10-02", "2026-10-16"], TODAY) is True
    assert opt.weekly_from_expiries(["2026-10-16"], TODAY) is False
    assert opt.weekly_from_expiries(["2026-10-16", "2026-11-20"], TODAY) is False   # 2nd is 55 days out
    assert opt.weekly_from_expiries(["2026-09-25", "2026-10-16"], TODAY) is False   # expired one ignored
    assert opt.weekly_from_expiries(["junk"], TODAY) is False


# ---------------------------------------------------------------- the book

class Clock:
    def __init__(self):
        self.t = 1_000_000.0

    def __call__(self):
        return self.t


def book(tmp_path, answers, *, healthy=True, clock=None):
    calls = []

    def fetch(symbol):
        calls.append(symbol)
        value = answers[symbol]
        return value.pop(0) if isinstance(value, list) else value

    b = opt.OptionableBook(tmp_path / "store.json", fetch=fetch, healthy=lambda: healthy, background=False,
                           clock=clock or Clock(), sleep=lambda s: None, now=lambda: SATURDAY)
    return b, calls


def test_split_checks_unknowns_and_sorts_the_list(tmp_path):
    b, calls = book(tmp_path, {"NVDA": chain(WEEKLY), "JAGX": NO_OPTIONS, "ELPW": WARMING})
    kept, hidden, pending = b.split(["nvda", "JAGX", "ELPW"])
    # background=False: the queue is drained inside split, before it returns
    assert calls == ["NVDA", "JAGX", "ELPW"]
    kept, hidden, pending = b.split(["NVDA", "JAGX", "ELPW"], request=False)
    assert (kept, hidden, pending) == (["NVDA"], ["JAGX"], ["ELPW"])
    doc = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))["symbols"]
    assert doc["NVDA"]["weeklies"] is True and doc["JAGX"]["hasOptions"] is False
    assert "ELPW" not in doc                       # unknown is never stored


def test_the_store_survives_a_restart(tmp_path):
    b, _ = book(tmp_path, {"NVDA": chain(WEEKLY)})
    b.split(["NVDA"])
    again, calls = book(tmp_path, {})
    assert again.split(["NVDA"], request=False)[0] == ["NVDA"] and calls == []


def test_yes_is_sticky_an_empty_read_never_hides_a_ticker_that_had_options(tmp_path):
    clock = Clock()
    b, _ = book(tmp_path, {"QMCO": [chain(MONTHLY), NO_OPTIONS]}, clock=clock)
    b.split(["QMCO"])
    clock.t += opt.YES_RECHECK_SECONDS + 1
    b.split(["QMCO"])                              # due re-check, reads empty (31-day window gap)
    assert b.status("QMCO") is True


def test_a_first_no_is_re_read_after_an_hour(tmp_path):
    clock = Clock()
    b, calls = book(tmp_path, {"JAGX": [NO_OPTIONS, NO_OPTIONS]}, clock=clock)
    b.split(["JAGX"])
    b.split(["JAGX"])
    assert calls == ["JAGX"]                       # not due yet
    clock.t += opt.NO_CONFIRM_SECONDS + 1
    b.split(["JAGX"])
    assert calls == ["JAGX", "JAGX"]
    entry = json.loads((tmp_path / "store.json").read_text(encoding="utf-8"))["symbols"]["JAGX"]
    assert entry["noReads"] == 2 and entry["nextCheck"] >= clock.t + opt.NO_RECHECK_SECONDS - 1


def test_a_ticker_that_lists_options_later_is_picked_up(tmp_path):
    clock = Clock()
    b, _ = book(tmp_path, {"NEWCO": [NO_OPTIONS, chain(WEEKLY)]}, clock=clock)
    b.split(["NEWCO"])
    assert b.status("NEWCO") is False
    clock.t += opt.NO_CONFIRM_SECONDS + 1
    b.split(["NEWCO"])
    assert b.status("NEWCO") is True


def test_nothing_is_read_while_schwab_is_unhealthy(tmp_path):
    b, calls = book(tmp_path, {"NVDA": chain(WEEKLY)}, healthy=False)
    assert b.split(["NVDA"]) == ([], [], ["NVDA"])
    assert calls == [] and b.status("NVDA") is None


def test_a_raising_fetch_is_unknown(tmp_path):
    def fetch(symbol):
        raise OSError("connection refused")

    b = opt.OptionableBook(tmp_path / "s.json", fetch=fetch, healthy=lambda: True, background=False,
                           sleep=lambda s: None, now=lambda: SATURDAY)
    b.split(["NVDA"])
    assert b.status("NVDA") is None


def test_tries_are_capped_per_day_for_a_symbol_that_never_answers(tmp_path):
    b, calls = book(tmp_path, {"BRK.A": WARMING})
    for _ in range(opt.MAX_TRIES_PER_DAY + 3):
        b.split(["BRK.A"])
    assert len(calls) == opt.MAX_TRIES_PER_DAY


def test_market_hours_skip_re_checks_but_not_unknowns(tmp_path):
    monday = datetime(2026, 9, 28, 10, 0, tzinfo=ET)
    clock = Clock()
    calls = []
    answers = {"NVDA": chain(WEEKLY), "NEWCO": chain(WEEKLY)}

    def fetch(symbol):
        calls.append(symbol)
        return answers[symbol]

    b = opt.OptionableBook(tmp_path / "s.json", fetch=fetch, healthy=lambda: True, background=False,
                           clock=clock, sleep=lambda s: None, now=lambda: monday)
    b.split(["NVDA"])
    clock.t += opt.YES_RECHECK_SECONDS + 1
    b.split(["NVDA", "NEWCO"])
    assert calls == ["NVDA", "NEWCO"]              # NVDA's re-check waits for off-hours


def test_weekly_table_reads_the_store_and_follows_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_GRADE_DIR", str(tmp_path))
    path = tmp_path / opt.STORE_NAME
    path.write_text(json.dumps({"symbols": {"A": {"hasOptions": True, "weeklies": True},
                                            "B": {"hasOptions": True, "weeklies": False},
                                            "C": {"hasOptions": False}}}), encoding="utf-8")
    assert opt.weekly_table() == {"A": True, "B": False, "C": False}
    path.write_text(json.dumps({"symbols": {"A": {"hasOptions": True, "weeklies": False, "x": 1}}}), encoding="utf-8")
    assert opt.weekly_table() == {"A": False}


# ---------------------------------------------------------------- the service gate

@pytest.fixture
def gated(tmp_path, monkeypatch):
    from momx import service

    monkeypatch.setenv("AGX_MOMX_OPTIONS_ONLY", "1")
    store = {"NVDA": {"hasOptions": True, "weeklies": True, "nextCheck": 9e18},
             "QMCO": {"hasOptions": True, "weeklies": False, "nextCheck": 9e18},
             "JAGX": {"hasOptions": False, "nextCheck": 9e18}}
    (tmp_path / "store.json").write_text(json.dumps({"symbols": store}), encoding="utf-8")
    b = opt.OptionableBook(tmp_path / "store.json", fetch=lambda s: WARMING, healthy=lambda: True,
                           background=False, sleep=lambda s: None, now=lambda: SATURDAY)
    monkeypatch.setattr(service, "_OPTIONABLE", b)
    return service


def test_gate_keeps_only_confirmed_options_in_list_order(gated):
    symbols, gate = gated._options_gate(["JAGX", "QMCO", "NEWCO", "NVDA"])
    assert symbols == ["QMCO", "NVDA"]
    assert gate == {"listCount": 4, "hidden": ["JAGX"], "pending": ["NEWCO"]}


def test_gate_off_switch_scans_everything(gated, monkeypatch):
    monkeypatch.setenv("AGX_MOMX_OPTIONS_ONLY", "0")
    assert gated._options_gate(["JAGX", "NVDA"]) == (["JAGX", "NVDA"], None)


def test_build_scans_only_the_kept_names_and_carries_the_gate(gated, monkeypatch):
    seen = {}

    def fake_cached_board(symbols, ttl_seconds=0.0, **kwargs):
        seen["symbols"] = list(symbols)
        return {"generatedAt": "2026-09-26T12:00:00Z", "universe": list(symbols), "universeCount": len(symbols),
                "rows": [], "rest": [], "errors": {}}

    monkeypatch.setattr(gated.board, "cached_board", fake_cached_board)
    monkeypatch.setattr(gated.board, "load_universe", lambda name=None, **kw: ["JAGX", "NVDA", "NEWCO"])
    monkeypatch.setattr(gated, "_write_disk", lambda *a, **k: None)
    monkeypatch.setattr(gated, "_record_history", lambda *a, **k: None)
    gated._build_once("GateTest")
    assert seen["symbols"] == ["NVDA"]
    state = gated._state("GateTest")
    assert state.payload["optionsGate"] == {"listCount": 3, "hidden": ["JAGX"], "pending": ["NEWCO"]}
    assert state.bear["optionsGate"] == state.payload["optionsGate"]     # BEAR_PAYLOAD_KEYS


def test_warming_payload_counts_only_scannable_names(gated, monkeypatch):
    monkeypatch.setattr(gated.board, "load_universe", lambda name=None, **kw: ["JAGX", "NVDA"])
    out = gated._warming_payload("GateTest")
    assert out["universe"] == ["NVDA"] and out["universeCount"] == 1
    assert out["optionsGate"]["hidden"] == ["JAGX"]


def test_a_disk_board_saved_before_the_gate_drops_hidden_rows(gated, monkeypatch, tmp_path):
    board_file = tmp_path / "board.json"
    board_file.write_text(json.dumps({"generatedAt": "2026-09-25T20:00:00Z", "universe": ["JAGX", "NVDA"],
                                      "rows": [{"symbol": "JAGX"}, {"symbol": "NVDA"}], "rest": []}),
                          encoding="utf-8")
    monkeypatch.setattr(gated, "_cache_file", lambda name, direction="bull": board_file)
    out = gated._load_disk("GateTest")
    assert [r["symbol"] for r in out["rows"]] == ["NVDA"] and out["universeCount"] == 1


def test_live_seeds_leave_with_the_ticker(gated):
    gated._harvest_live_seeds("SeedTest", {"rows": [{"symbol": "NVDA", "m5": {"liveSeed": {"t": 1}}},
                                                     {"symbol": "JAGX", "m5": {"liveSeed": {"t": 1}}}]})
    assert set(gated.live_seeds("SeedTest")) == {"NVDA", "JAGX"}
    gated._harvest_live_seeds("SeedTest", {"rows": [{"symbol": "NVDA", "m5": {"liveSeed": {"t": 2}}}]})
    assert set(gated.live_seeds("SeedTest")) == {"NVDA"}


def test_movers_drop_confirmed_no_options_but_keep_unknowns(gated):
    assert gated.drop_no_options(["JAGX", "NEWCO", "NVDA"]) == ["NEWCO", "NVDA"]


def test_the_tickers_box_is_told_which_typed_names_will_not_show(gated):
    assert gated.typed_options_note(["JAGX", "NEWCO", "NVDA"]) == {"noOptions": ["JAGX"], "optionsChecking": ["NEWCO"]}


# ---------------------------------------------------------------- review fixes (2026-09-26)

def test_gap_days_are_the_days_the_next_monthly_is_beyond_the_31_day_window():
    gaps = [d for d in (date(2026, 10, 1) + __import__("datetime").timedelta(days=i) for i in range(60))
            if opt.window_gap(d)]
    # Oct 16 -> Nov 20 is 35 days: Oct 17-19 cannot see Nov 20 in a 0-31 day window.
    assert gaps == [date(2026, 10, 17), date(2026, 10, 18), date(2026, 10, 19)]
    assert opt.monthly_expiry(2026, 10) == date(2026, 10, 16)


def test_an_empty_read_on_a_gap_day_is_unknown_not_no():
    assert opt.classify(NO_OPTIONS, date(2026, 10, 18)) is None
    assert opt.classify(NO_OPTIONS, date(2026, 10, 20)) == {"hasOptions": False}


@pytest.mark.parametrize("flag", ["stale", "diskCached", "refreshing"])
def test_an_old_or_still_building_empty_copy_is_not_a_no(flag):
    assert opt.classify({**NO_OPTIONS, flag: True}, TODAY) is None


def test_a_stale_copy_WITH_rows_still_proves_options():
    assert opt.classify(chain(WEEKLY, stale=True, diskCached=True), TODAY)["hasOptions"] is True


def test_health_reads_the_transport_not_the_market_data_profile(monkeypatch):
    replies = {}
    monkeypatch.setattr(opt, "_http_json", lambda url, timeout=40.0: replies["doc"])
    replies["doc"] = {"healthy": False, "transportBlocked": False, "transport": {"reachable": True, "coolingDown": False}}
    assert opt._default_healthy() is True            # market_data refused, chains still work
    replies["doc"] = {"healthy": True, "transportBlocked": True, "transport": {}}
    assert opt._default_healthy() is False           # Akamai block
    replies["doc"] = {"healthy": True, "transportBlocked": False, "transport": {"coolingDown": True}}
    assert opt._default_healthy() is False           # 429 back-off


def test_the_checker_waits_for_the_build_to_settle_before_moving_on(tmp_path):
    b, calls = book(tmp_path, {"COLD": [WARMING, WARMING, chain(WEEKLY)]})
    assert b._check("COLD", waits=True) is True          # the background checker's read
    assert calls == ["COLD", "COLD", "COLD"] and b.status("COLD") is True


def test_a_settled_build_without_an_answer_stops_polling(tmp_path):
    settled_bad = {**NO_OPTIONS, "source": "Tradier option chain (Schwab/TOS fallback)"}
    b, calls = book(tmp_path, {"ODD": [WARMING, settled_bad, chain(WEEKLY)]})
    b._check("ODD", waits=True)
    assert calls == ["ODD", "ODD"] and b.status("ODD") is None


def test_used_up_tries_are_not_re_queued(tmp_path):
    b, calls = book(tmp_path, {"BRK.A": {**NO_OPTIONS, "errors": [{"error": "400"}]}})
    for _ in range(opt.MAX_TRIES_PER_DAY):
        b.split(["BRK.A"])
    b.split(["BRK.A"])
    assert len(calls) == opt.MAX_TRIES_PER_DAY and "BRK.A" not in b._queued


def test_only_one_checker_thread_starts(tmp_path, monkeypatch):
    started = []

    class FakeThread:
        def __init__(self, target=None, name=None, daemon=None):
            started.append(name)

        def start(self):
            pass

        def is_alive(self):
            return True

    monkeypatch.setattr(opt.threading, "Thread", FakeThread)
    b = opt.OptionableBook(tmp_path / "s.json", fetch=lambda s: WARMING, healthy=lambda: True)
    b.request(["A"], urgent=True)
    b.request(["B"], urgent=True)
    assert started == ["momx-optionable"]


def test_movers_oversample_is_not_queued_for_checking(gated):
    gated.drop_no_options(["JAGX", "NEWCO", "NVDA"])
    assert "NEWCO" not in gated._OPTIONABLE._queued


def test_re_adding_a_hidden_ticker_says_why_it_is_not_shown(gated, monkeypatch):
    monkeypatch.setattr(gated.board, "add_symbols",
                        lambda text, name: {"added": [], "already": ["JAGX"], "before": 5, "after": 5,
                                            "materialised": False})
    reply = gated.add_universe("JAGX", "Watchlist")
    assert reply["noop"] is True and reply["noOptions"] == ["JAGX"]
