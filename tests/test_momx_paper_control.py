"""Paper bot safety controls (2026-11-09): pause, daily loss stop, stuck orders.

momx/paper_control.py and the paper mirror's use of it. Fake broker only.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from momx import paper_control as pc
from momx import paper_mirror as pm

ET = pm.ET
NOW = datetime(2026, 10, 19, 10, 0, tzinfo=ET)      # a Monday, inside the first buy window
DAY = NOW.date().isoformat()
NOT_FOUND = '{"code":40410000,"message":"order not found for mxp-b0-x"}'


class FakeClient:
    def __init__(self, fill=True):
        self.credentials = SimpleNamespace(paper=True)
        self.fill = fill
        self.orders = {}
        self.calls = []
        self.cancelled = []
        self.lookup_error = None

    def submit_option_limit_order(self, symbol, qty, limit, cid, intent):
        self.calls.append((intent, symbol, qty, limit))
        self.orders[cid] = SimpleNamespace(id=cid, status="filled" if self.fill else "new",
                                           filled_avg_price=limit if self.fill else None,
                                           filled_qty=qty if self.fill else 0)

    def get_order_by_client_id(self, cid):
        if self.lookup_error:
            raise RuntimeError(self.lookup_error)
        if cid not in self.orders:
            raise RuntimeError(NOT_FOUND.replace("mxp-b0-x", cid))
        return self.orders[cid]

    def cancel_order_by_id(self, oid):
        self.cancelled.append(oid)
        self.orders[oid].status = "canceled"


class Book:
    def __init__(self, trades):
        self.items = list(trades)

    def trades(self):
        return [dict(t) for t in self.items]


def trade(now=NOW, **kw):
    t = {"rule": "call2h", "symbol": "AAPL", "at": (now - timedelta(minutes=2)).isoformat(),
         "entryAt": (now - timedelta(minutes=2)).isoformat(), "contract": "AAPL261023C00250000",
         "status": "open", "entry": 1.20, "jevP": 0.25,
         "lastAt": (now - timedelta(seconds=20)).isoformat(), "lastBid": 1.15, "lastAsk": 1.25}
    t.update(kw)
    return t


def mirror(tmp_path, trades, client):
    book = Book(trades)
    m = pm.PaperMirror({"bull": book}, tmp_path / "paper3", client_factory=lambda: client, off=lambda: False,
                       account="paper3")
    return m, book


def buys(client):
    return [c for c in client.calls if c[0] == "buy_to_open"]


def sells(client):
    return [c for c in client.calls if c[0] == "sell_to_close"]


def closed_today(pl, key="x"):
    return {"side": "bull", "rule": "rvol", "symbol": key, "at": NOW.isoformat(), "status": "closed", "buyFill": 1.0,
            "buyId": "b-" + key, "pl": pl, "closedAt": DAY + "T09:50:00-04:00"}


def stuck_row(age=timedelta(days=1), **kw):
    row = {"side": "bear", "rule": "call2h", "symbol": "CCJ", "at": (NOW - age).isoformat(),
           "contract": "CCJ   261106P00072000", "qty": 3, "buyLimit": 1.0, "buyId": "mxp-b0-ccj",
           "buyAt": (NOW - age).isoformat(), "status": "buying", "exec": pm.EXEC_VERSION, "buyTries": 0}
    row.update(kw)
    return row


# ---------------------------------------------------------------- the pause file
def test_pause_is_per_account_and_survives_a_reread(tmp_path):
    assert pc.paused(tmp_path, "paper3") is None
    pc.set_paused(tmp_path, "paper3", True, "owner@example.com", "2026-10-19T10:00:00-04:00")
    assert pc.paused(tmp_path, "paper3") == {"at": "2026-10-19T10:00:00-04:00", "by": "owner@example.com"}
    assert pc.paused(tmp_path, "paper5") is None
    pc.set_paused(tmp_path, "paper3", False, "owner@example.com", "2026-10-19T10:05:00-04:00")
    assert pc.paused(tmp_path, "paper3") is None


def test_a_broken_pause_file_means_not_paused_and_can_be_rewritten(tmp_path):
    (tmp_path / pc.FILE).write_text("{not json", encoding="utf-8")
    assert pc.load(tmp_path) == {"paused": {}}
    pc.set_paused(tmp_path, "paper5", True, "", "2026-10-19T10:00:00-04:00")
    assert json.loads((tmp_path / pc.FILE).read_text(encoding="utf-8"))["paused"].keys() == {"paper5"}


# ---------------------------------------------------------------- the day's P/L
def test_day_pnl_counts_trades_closed_today_and_todays_marked_positions():
    rows = [
        closed_today(-200.0, "A"),
        {**closed_today(-999.0, "B"), "closedAt": "2026-10-16T15:00:00-04:00"},     # closed on an earlier day
        {"status": "open", "buyFill": 1.0, "held": 3, "markMid": 0.5, "sells": []},             # down $150 right now
        {"status": "open", "buyFill": 1.0, "held": 2, "markMid": None, "sells": [                # no mark: never a guess
            {"qty": 1, "price": 2.0, "at": DAY + "T09:45:00-04:00"}]},                           # but it sold 1 for +$100
        {"status": "open", "buyFill": 1.0, "held": 3, "markMid": 0.1, "carried": True, "sells": []},   # yesterday's position
        {"status": "missed", "buyLimit": 1.0},
        {"status": "closed", "buyFill": 1.0, "pl": None, "closedAt": DAY + "T09:50:00-04:00"},   # unpriced close
    ]
    assert pc.day_pnl(rows, DAY) == -250.0
    assert pc.day_pnl([], DAY) == 0.0


def test_not_found_recognises_the_brokers_answer_only():
    assert pc.not_found(RuntimeError(NOT_FOUND))
    assert not pc.not_found(TimeoutError("read timed out"))


# ---------------------------------------------------------------- pause in the mirror
def test_a_paused_account_places_no_new_buy_and_buys_again_after_resume(tmp_path):
    client = FakeClient()
    m, _ = mirror(tmp_path, [trade()], client)
    pc.set_paused(tmp_path, "paper3", True, "me", NOW.isoformat())
    m.tick(NOW)
    assert buys(client) == [] and m.status()["holdBuys"] == "paused"
    pc.set_paused(tmp_path, "paper3", False, "me", NOW.isoformat())
    m.tick(NOW + timedelta(seconds=10))
    assert len(buys(client)) == 1 and m.status()["holdBuys"] == ""


def test_pausing_one_account_leaves_the_other_buying(tmp_path):
    client = FakeClient()
    other = pm.PaperMirror({"bull": Book([trade()])}, tmp_path / "paper5", client_factory=lambda: client,
                           off=lambda: False, account="paper5", ai_policy=lambda side, t, ts: {"enabled": True, "approved": True})
    pc.set_paused(tmp_path, "paper3", True, "me", NOW.isoformat())
    other.tick(NOW)
    assert len(buys(client)) == 1


def test_a_paused_account_still_sells_what_it_holds_when_the_bot_exits(tmp_path):
    client = FakeClient()
    m, book = mirror(tmp_path, [trade()], client)
    m.tick(NOW)                                   # buy sent, filled at once by the fake broker
    m.tick(NOW + timedelta(seconds=10))           # mirror sees the fill
    assert m.status()["totals"]["open"] == 1
    pc.set_paused(tmp_path, "paper3", True, "me", NOW.isoformat())
    later = NOW + timedelta(seconds=20)
    book.items = [trade(status="closed", exitPrice=1.5, exitBid=1.45, exitAsk=1.55, exitReason="target",
                        lastAt=(later - timedelta(seconds=5)).isoformat())]
    m.tick(later)
    assert len(sells(client)) == 1 and len(buys(client)) == 1


def test_a_paused_account_does_not_resend_an_unfilled_buy_at_the_ask(tmp_path):
    client = FakeClient(fill=False)
    m, _ = mirror(tmp_path, [trade()], client)
    m.tick(NOW)
    assert len(buys(client)) == 1
    pc.set_paused(tmp_path, "paper3", True, "me", NOW.isoformat())
    m.tick(NOW + timedelta(seconds=pm.BUY_TTL_SECONDS + 10))
    row = m.status()["rows"][0]
    assert len(buys(client)) == 1                 # no second order at the ask
    assert row["status"] == "missed" and "paused" in row["note"]


# ---------------------------------------------------------------- the daily loss stop
def test_the_daily_loss_stop_holds_new_buys_and_survives_a_restart(tmp_path):
    client = FakeClient()
    m, book = mirror(tmp_path, [], client)
    m._load(DAY)
    m._rows = {"a": closed_today(-300.0, "A"), "b": closed_today(-250.0, "B")}       # down $550 on the day
    for i in range(pm.STOP_TICKS):
        m.tick(NOW + timedelta(seconds=10 * i))
    stop = m.status()["dailyStop"]
    assert stop["pnl"] == -550.0 and stop["limit"] == pc.DAILY_LOSS_STOP and m.status()["holdBuys"] == "daily loss stop"
    later = NOW + timedelta(minutes=5)
    book.items = [trade(later)]
    m.tick(later)
    assert buys(client) == []
    saved = json.loads((tmp_path / "paper3" / f"{DAY}.json").read_text(encoding="utf-8"))
    assert saved["dailyStop"]["pnl"] == -550.0

    restarted, _ = mirror(tmp_path, [trade(later)], client)
    restarted.tick(later + timedelta(seconds=10))
    assert buys(client) == [] and restarted.status()["holdBuys"] == "daily loss stop"

    restarted._load((NOW + timedelta(days=1)).date().isoformat())                    # a new day starts clean
    assert restarted._daily_stop is None


def test_a_loss_inside_the_limit_keeps_buying(tmp_path):
    client = FakeClient()
    m, book = mirror(tmp_path, [], client)
    m._load(DAY)
    m._rows = {"a": closed_today(-499.0, "A")}
    for i in range(pm.STOP_TICKS + 1):
        m.tick(NOW + timedelta(seconds=10 * i))
    later = NOW + timedelta(minutes=1)
    book.items = [trade(later)]
    m.tick(later)
    assert m.status()["dailyStop"] is None and len(buys(client)) == 1


def test_one_bad_quote_does_not_trip_the_stop(tmp_path):
    client = FakeClient()
    m, _ = mirror(tmp_path, [], client)
    m._load(DAY)
    held = {"side": "bull", "rule": "rvol", "symbol": "A", "at": NOW.isoformat(), "status": "open", "buyFill": 2.0,
            "buyId": "b-a", "held": 3, "markMid": 0.01, "sells": []}                  # marked down $597 for a moment
    m._rows = {"a": held}
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False
    held["markMid"] = 1.9                                                              # the quote comes back
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False
    assert m._daily_stop is None and m._hold_buys == "" and m._stop_ticks == 0


# ---------------------------------------------------------------- stuck orders
def with_row(tmp_path, client, row, key="bear|call2h|CCJ|x"):
    m, _ = mirror(tmp_path, [], client)
    m._load(DAY)
    m._rows = {key: row}
    return m, key


def test_remove_closes_a_buy_the_broker_never_had_and_frees_the_slot(tmp_path):
    client = FakeClient()
    m, key = with_row(tmp_path, client, stuck_row())
    result = m.remove_stuck(key, "owner@example.com", NOW)
    assert result == {"ok": True, "account": "paper3", "key": key, "symbol": "CCJ"}
    row = m._rows[key]
    assert row["status"] == "missed" and row["removedBy"] == "owner@example.com" and "Removed by you" in row["note"]
    assert client.calls == [] and client.cancelled == []
    saved = json.loads((tmp_path / "paper3" / f"{DAY}.json").read_text(encoding="utf-8"))
    assert saved["rows"][key]["status"] == "missed"
    tomorrow, _ = mirror(tmp_path, [], client)
    tomorrow._load((NOW + timedelta(days=1)).date().isoformat())
    assert key not in tomorrow._rows                                                   # no longer carried forward


def test_remove_cancels_an_order_still_resting_at_the_broker(tmp_path):
    client = FakeClient()
    client.orders["mxp-b0-ccj"] = SimpleNamespace(id="mxp-b0-ccj", status="new", filled_avg_price=None, filled_qty=0)
    m, key = with_row(tmp_path, client, stuck_row())
    assert m.remove_stuck(key, "me", NOW)["ok"] is True
    assert client.cancelled == ["mxp-b0-ccj"] and m._rows[key]["status"] == "missed"


def test_remove_refuses_an_order_that_really_filled(tmp_path):
    client = FakeClient()
    client.orders["mxp-b0-ccj"] = SimpleNamespace(id="mxp-b0-ccj", status="filled", filled_avg_price=1.0, filled_qty=3)
    m, key = with_row(tmp_path, client, stuck_row())
    result = m.remove_stuck(key, "me", NOW)
    assert result["ok"] is False and "real position" in result["error"]
    assert m._rows[key]["status"] == "buying"


def test_remove_refuses_when_an_earlier_try_of_the_same_buy_filled(tmp_path):
    client = FakeClient()
    client.orders["mxp-b0-old"] = SimpleNamespace(id="mxp-b0-old", status="canceled", filled_avg_price=1.0, filled_qty=1)
    m, key = with_row(tmp_path, client, stuck_row(buyHistory=[{"id": "mxp-b0-old", "limit": 1.0, "at": NOW.isoformat()}]))
    result = m.remove_stuck(key, "me", NOW)
    assert result["ok"] is False and m._rows[key]["status"] == "buying"


def test_remove_changes_nothing_when_the_broker_cannot_be_asked(tmp_path):
    client = FakeClient()
    client.lookup_error = "read timed out"
    m, key = with_row(tmp_path, client, stuck_row())
    result = m.remove_stuck(key, "me", NOW)
    assert result["ok"] is False and "nothing was removed" in result["error"]
    assert m._rows[key]["status"] == "buying"
    assert not (tmp_path / "paper3" / f"{DAY}.json").exists()


def test_remove_refuses_a_young_order_a_held_position_and_an_unknown_key(tmp_path):
    client = FakeClient()
    m, key = with_row(tmp_path, client, stuck_row(age=timedelta(minutes=3)))
    assert "still being worked" in m.remove_stuck(key, "me", NOW)["error"]
    m._rows[key] = stuck_row(status="open", buyFill=1.0, held=3)
    assert "not waiting to buy" in m.remove_stuck(key, "me", NOW)["error"]
    assert m._rows[key]["status"] == "open"
    assert "not in today's list" in m.remove_stuck("nope", "me", NOW)["error"]


def test_a_buy_the_broker_has_no_record_of_stops_holding_a_slot_by_itself(tmp_path):
    client = FakeClient()
    old = stuck_row(age=timedelta(seconds=pm.ORPHAN_SECONDS + 30))
    fresh = stuck_row(age=timedelta(seconds=60), symbol="CEG", buyId="mxp-b0-ceg")
    m, _ = mirror(tmp_path, [], client)
    m._load(DAY)
    m._rows = {"old": old, "fresh": fresh}
    m.tick(NOW)
    assert old["status"] == "missed" and "no record" in old["note"]
    assert fresh["status"] == "buying"            # too soon to call: the broker may still be catching up
    assert client.calls == []


def test_another_broker_error_keeps_the_buy_waiting(tmp_path):
    client = FakeClient()
    client.lookup_error = "read timed out"
    row = stuck_row(age=timedelta(hours=2))
    m, _ = mirror(tmp_path, [], client)
    m._load(DAY)
    m._rows = {"k": row}
    m.tick(NOW)
    assert row["status"] == "buying" and "Awaiting broker reconciliation" in row["note"]


# ---------------------------------------------------------------- review fixes (2026-11-09)
def held_today(mark, **kw):
    row = {"side": "bull", "rule": "rvol", "symbol": "A", "at": NOW.isoformat(), "buyAt": NOW.isoformat(),
           "status": "open", "buyFill": 2.0, "buyId": "b-a", "held": 3, "markMid": mark, "sells": []}
    row.update(kw)
    return row


def test_a_quote_that_drops_out_does_not_hide_the_loss_from_the_stop(tmp_path):
    m, _ = mirror(tmp_path, [], FakeClient())
    m._load(DAY)
    row = held_today(0.01, stopMark=0.01)                      # down $597, last good price kept
    m._rows = {"a": row}
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False
    row["markMid"] = None                                      # the quote read fails on the third tick
    assert m._check_buy_hold(DAY, NOW.isoformat()) is True
    assert m._daily_stop["pnl"] == -597.0 and m._hold_buys == "daily loss stop"


def test_a_position_with_no_price_at_all_does_not_clear_the_count_toward_the_stop(tmp_path):
    m, _ = mirror(tmp_path, [], FakeClient())
    m._load(DAY)
    row = held_today(0.01)
    m._rows = {"a": row}
    m._check_buy_hold(DAY, NOW.isoformat())
    m._check_buy_hold(DAY, NOW.isoformat())
    row["markMid"] = None                                      # no live price and no last good price: unknown
    assert m._check_buy_hold(DAY, NOW.isoformat()) is False and m._stop_ticks == 2
    row["markMid"] = 0.01
    assert m._check_buy_hold(DAY, NOW.isoformat()) is True


def test_the_mirror_keeps_the_last_good_price_when_the_quote_goes_dead(tmp_path):
    client = FakeClient()
    m, book = mirror(tmp_path, [trade()], client)
    m.tick(NOW)
    m.tick(NOW + timedelta(seconds=10))                        # filled; marked from the Bot's fresh quote
    row = m._rows[next(iter(m._rows))]
    assert row["markMid"] == 1.2 and row["stopMark"] == 1.2
    later = NOW + timedelta(seconds=20)
    book.items = [trade(lastBid=0, lastAsk=0, lastAt=(later - timedelta(seconds=5)).isoformat())]
    m.tick(later)
    assert row["markMid"] is None and row["stopMark"] == 1.2


def test_a_position_bought_on_an_earlier_day_is_not_todays_loss_even_without_the_carried_flag():
    friday = {"status": "open", "buyFill": 2.0, "held": 3, "markMid": 0.1, "sells": [],
              "buyAt": "2026-10-16T10:00:00-04:00", "buyFillAt": "2026-10-16T10:00:20-04:00"}
    assert pc.carried(friday, DAY) is True and pc.day_pnl([friday], DAY) == 0.0
    assert pc.carried({"buyAt": DAY + "T09:40:00-04:00"}, DAY) is False
    assert pc.carried({}, DAY) is False


def test_remove_refuses_while_the_broker_still_shows_the_order_working(tmp_path):
    client = FakeClient()
    client.orders["mxp-b0-ccj"] = SimpleNamespace(id="mxp-b0-ccj", status="new", filled_avg_price=None, filled_qty=0)

    def cancel_fails(oid):
        raise RuntimeError("cancel rejected")

    client.cancel_order_by_id = cancel_fails
    m, key = with_row(tmp_path, client, stuck_row())
    result = m.remove_stuck(key, "me", NOW)
    assert result["ok"] is False and "still shows this order as working" in result["error"]
    assert m._rows[key]["status"] == "buying"
    assert not (tmp_path / "paper3" / f"{DAY}.json").exists()
