"""Unit: momx/schwab_history.py, the read-only Schwab trade-history reader (2026-11-11).

The safety rules are what these tests are mostly about: only three GET paths, never a
retry, a sign-in failure stops reading for the day, the day is claimed before the request,
at most one manual refresh an hour, and the account number is never stored.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from momx import schwab_history as sh

ET = ZoneInfo("America/New_York")
SOURCE = Path(sh.__file__).read_text(encoding="utf-8")
AFTER_CLOSE = datetime(2026, 11, 11, 16, 45, tzinfo=ET)       # a Wednesday
HASH = "A1B2C3D4E5F6A7B8C9D0"


@pytest.fixture(autouse=True)
def scratch(tmp_path, monkeypatch):
    monkeypatch.setenv("AGX_INSIGHTS_DIR", str(tmp_path / "insights"))
    sh._READING.update(on=False, trigger=None, startedAt=None)
    return tmp_path / "insights"


def tx(activity, at, symbol, amount, price, effect="OPENING", order=None, kind="TRADE", fee=0.66):
    return {"activityId": activity, "time": at, "type": kind, "orderId": order or activity, "accountNumber": "12345678",
            "transferItems": [
                {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"}, "amount": 0, "cost": -fee, "feeType": "COMMISSION"},
                {"instrument": {"assetType": "OPTION", "symbol": symbol, "underlyingSymbol": symbol.split()[0], "putCall": "CALL"},
                 "amount": amount, "cost": -amount * price * 100, "price": price, "positionEffect": effect}]}


class FakeSchwab:
    """Answers the allow-listed paths; records every URL. `fail` = {path-fragment: SchwabRefused}."""

    def __init__(self, transactions=None, fail=None):
        self.urls: list[str] = []
        self.transactions = transactions if transactions is not None else []
        self.fail = fail or {}

    def __call__(self, url):
        self.urls.append(url)
        for fragment, exc in self.fail.items():
            if fragment in url:
                raise exc
        if "accountNumbers" in url:
            return [{"accountNumber": "12345678", "hashValue": HASH}]
        if "/transactions" in url:
            return self.transactions
        raise AssertionError(url)


# ---------------------------------------------------------------- the allow-list
@pytest.mark.parametrize("method,path", [
    ("POST", "/trader/v1/accounts/accountNumbers"),
    ("PUT", f"/trader/v1/accounts/{HASH}/orders"),
    ("DELETE", f"/trader/v1/accounts/{HASH}/orders"),
    ("PATCH", f"/trader/v1/accounts/{HASH}/transactions"),
    ("GET", f"/trader/v1/accounts/{HASH}/orders/12345"),          # one order (the cancel/replace path)
    ("GET", f"/trader/v1/accounts/{HASH}/previewOrder"),
    ("GET", "/trader/v1/userPreference"),
    ("GET", "/trader/v1/accounts"),
    ("GET", f"/trader/v1/accounts/{HASH}"),
    ("GET", "/marketdata/v1/quotes"),
    ("GET", "/trader/v1/accounts/accountNumbers/../orders"),
    ("GET", f"/trader/v1/accounts/{HASH}/transactions/../orders/1"),
])
def test_the_allow_list_refuses_every_other_method_and_path(method, path):
    with pytest.raises(sh.NotAllowed):
        sh.check_request(method, path)


def test_the_allow_list_takes_the_three_read_paths_only():
    sh.check_request("GET", "/trader/v1/accounts/accountNumbers")
    sh.check_request("GET", f"/trader/v1/accounts/{HASH}/transactions", {"types": "TRADE", "startDate": "x", "endDate": "y"})
    sh.check_request("GET", f"/trader/v1/accounts/{HASH}/orders", {"fromEnteredTime": "x", "toEnteredTime": "y", "status": "FILLED"})
    with pytest.raises(sh.NotAllowed):
        sh.check_request("GET", f"/trader/v1/accounts/{HASH}/transactions", {"orderType": "MARKET"})


def test_a_refused_request_is_never_sent():
    fake = FakeSchwab()
    reader = sh.Reader(fake, {}, "2026-11-11")
    with pytest.raises(sh.NotAllowed):
        reader.get(f"/trader/v1/accounts/{HASH}/orders/1")
    assert fake.urls == []


def test_the_source_cannot_place_change_or_cancel_anything():
    banned = re.compile(r"place_order|cancel_order|replace_order|preview_order|\.post\(|\.put\(|\.delete\(|\.patch\(|"
                        r"(?<!_)library_client\(|session\.request|requests\.|urlopen|httpx")
    assert banned.findall(SOURCE) == []
    # The one network call in the file is a GET on the signed session.
    assert re.findall(r"session\.\w+\(", SOURCE) == ["session.get("]


def test_the_transport_turns_every_token_problem_into_a_sign_in_stop(monkeypatch):
    class DeadToken:
        def _refuse_if_refresh_token_dead(self):
            raise RuntimeError("Schwab refresh token expired at 2026-11-10; no request was sent.")
    get = sh.schwab_transport(lambda: DeadToken())
    with pytest.raises(sh.SchwabRefused) as caught:
        get(sh.BASE_URL + "/trader/v1/accounts/accountNumbers")
    assert caught.value.kind == "signin"


def test_the_transport_reads_401_403_edge_block_and_429(monkeypatch):
    from data import schwab_rate_limit
    monkeypatch.setattr(schwab_rate_limit.GATE, "acquire", lambda *a, **k: None)
    for name in ("note_failure", "note_edge_block", "note_rate_limited", "note_success"):
        monkeypatch.setattr(schwab_rate_limit.GATE, name, lambda *a, **k: None)

    class Response:
        def __init__(self, code, text="", body=None):
            self.status_code, self.text, self._body = code, text, body

        def json(self):
            return self._body

    class Session:
        def __init__(self, response):
            self.response, self.calls = response, []

        def get(self, url, **kw):
            self.calls.append(url)
            return self.response

    def client_with(response):
        session = Session(response)

        class Client:
            def _refuse_if_refresh_token_dead(self):
                return None

            def _library_client(self):
                return type("L", (), {"session": session})()
        return Client(), session

    cases = [(Response(401, '{"error":"invalid_token"}'), "signin"), (Response(403, "{}"), "signin"),
             (Response(403, "<HTML><HEAD><TITLE>Access Denied</TITLE></HEAD><BODY>Reference #18</BODY></HTML>"), "blocked"),
             (Response(429, "{}"), "rate_limit"), (Response(500, "oops"), "bad_reply")]
    for response, kind in cases:
        client, session = client_with(response)
        with pytest.raises(sh.SchwabRefused) as caught:
            sh.schwab_transport(lambda: client)(sh.BASE_URL + "/trader/v1/accounts/accountNumbers")
        assert caught.value.kind == kind, (response.status_code, response.text)
        assert len(session.calls) == 1                     # one GET, no retry
    client, session = client_with(Response(200, "", [{"hashValue": "x"}]))
    assert sh.schwab_transport(lambda: client)("u") == [{"hashValue": "x"}]


# ---------------------------------------------------------------- reading the reply
def test_transactions_become_fills_with_only_the_last_four_digits():
    fills = sh.parse_transactions([tx("1", "2026-11-10T15:05:52+0000", "SPY   261110C00750000", 2, 1.25)], sh.last4("12345678"))
    assert len(fills) == 1
    f = fills[0]
    assert f["account"] == "…5678" and "12345678" not in json.dumps(f)
    assert (f["underlying"], f["putCall"], f["strike"], f["expiry"], f["dte"]) == ("SPY", "CALL", 750.0, "2026-11-10", 0)
    assert (f["side"], f["quantity"], f["price"], f["fees"], f["positionEffect"]) == ("BUY", 2.0, 1.25, 0.66, "OPENING")
    assert f["at"] == "2026-11-10T10:05:52-05:00"            # ET


def test_a_sell_and_an_expiry_row():
    sell = tx("2", "2026-11-10T16:00:00+0000", "NVDA  261114P00190000", -1, 2.0, effect="CLOSING")
    expiry = tx("3", "2026-11-15T05:00:00+0000", "NVDA  261114P00190000", -1, 0.0, effect="CLOSING", kind="RECEIVE_AND_DELIVER", fee=0)
    other = {"activityId": "4", "type": "ACH_RECEIPT", "time": "2026-11-10T16:00:00+0000", "transferItems": []}
    fills = sh.parse_transactions([sell, expiry, other], "…5678")
    assert [(f["side"], f["kind"], f["price"], f["putCall"]) for f in fills] == [("SELL", "trade", 2.0, "PUT"), ("SELL", "expired", 0.0, "PUT")]


# ---------------------------------------------------------------- when a read may happen
def test_daily_due_weekdays_after_1630_once():
    assert sh.daily_due({}, AFTER_CLOSE)
    assert not sh.daily_due({}, AFTER_CLOSE.replace(hour=16, minute=29))
    assert not sh.daily_due({}, datetime(2026, 11, 14, 17, 0, tzinfo=ET))           # Saturday
    assert not sh.daily_due({"lastReadDay": "2026-11-11"}, AFTER_CLOSE)
    assert not sh.daily_due({"stoppedDay": "2026-11-11"}, AFTER_CLOSE)
    assert sh.daily_due({"lastReadDay": "2026-11-10", "stoppedDay": "2026-11-10"}, AFTER_CLOSE)   # a new day starts fresh


def test_one_read_stores_fills_and_claims_the_day_first(scratch):
    seen_state = {}

    class Spy(FakeSchwab):
        def __call__(self, url):
            seen_state.setdefault("first", json.loads((scratch / "schwab_read.json").read_text(encoding="utf-8")))
            return super().__call__(url)
    fake = Spy([tx("1", "2026-11-10T15:05:52+0000", "SPY   261110C00750000", 1, 1.0)])
    out = sh.run_daily_if_due(fake, AFTER_CLOSE)
    assert out["ok"] and out["newFills"] == 1
    assert seen_state["first"]["lastReadDay"] == "2026-11-11"           # written before the first GET
    # 90 days on the first read = accountNumbers + two transaction windows (Schwab allows 60 days a request).
    assert len(fake.urls) == 3 and "accountNumbers" in fake.urls[0]
    assert all(HASH in u and "types=TRADE%2CRECEIVE_AND_DELIVER" in u for u in fake.urls[1:])
    stored = (scratch / "schwab_fills.json").read_text(encoding="utf-8") + (scratch / "schwab_read.json").read_text(encoding="utf-8")
    assert "12345678" not in stored and HASH not in stored
    assert sh.run_daily_if_due(fake, AFTER_CLOSE.replace(hour=20)) is None and len(fake.urls) == 3     # once a day
    # The next day reads only from the last read on (one window).
    sh.run_daily_if_due(fake, datetime(2026, 11, 12, 16, 40, tzinfo=ET))
    assert len(fake.urls) == 5


@pytest.mark.parametrize("kind", ["signin", "blocked", "rate_limit"])
def test_a_sign_in_failure_stops_reading_for_the_day(kind):
    fake = FakeSchwab(fail={"accountNumbers": sh.SchwabRefused("401", kind, 401)})
    out = sh.run_daily_if_due(fake, AFTER_CLOSE)
    assert out == {"ok": False, "kind": kind, "calls": 1}
    status = sh.status(AFTER_CLOSE)
    assert status["stopped"] and status["signInNeeded"] and status["state"] in ("signin_needed", "stopped")
    # Nothing more today: not the scheduler, not the button.
    assert sh.run_daily_if_due(fake, AFTER_CLOSE.replace(hour=18)) is None
    code, body = sh.request_manual(fake, AFTER_CLOSE.replace(hour=19))
    assert code == 409 and "sign-in" in body["message"].lower()
    assert len(fake.urls) == 1
    # The next weekday reads again.
    sh.run_daily_if_due(FakeSchwab(), datetime(2026, 11, 12, 16, 31, tzinfo=ET))
    assert sh.status(datetime(2026, 11, 12, 16, 50, tzinfo=ET))["stopped"] is False


def test_a_network_error_is_not_retried():
    fake = FakeSchwab(fail={"/transactions": sh.SchwabRefused("timeout", "network")})
    out = sh.run_daily_if_due(fake, AFTER_CLOSE)
    assert out["kind"] == "network" and len(fake.urls) == 2
    assert sh.run_daily_if_due(fake, AFTER_CLOSE.replace(hour=17)) is None and len(fake.urls) == 2


def test_an_older_window_refused_keeps_the_newer_one():
    class OldRefused(FakeSchwab):
        def __call__(self, url):
            if "/transactions" in url and len([u for u in self.urls if "/transactions" in u]) == 1:
                self.urls.append(url)
                raise sh.SchwabRefused("400", "bad_reply", 400)
            return super().__call__(url)
    fake = OldRefused([tx("1", "2026-11-10T15:05:52+0000", "SPY   261110C00750000", 1, 1.0)])
    out = sh.run_daily_if_due(fake, AFTER_CLOSE)
    assert out["ok"] and out["newFills"] == 1 and len(fake.urls) == 3
    assert "older than 60 days" in sh.load_state()["notes"][0]


def test_manual_refresh_at_most_once_an_hour(monkeypatch):
    started = []
    monkeypatch.setattr(sh.threading, "Thread", lambda target, args, kwargs, name, daemon: type("T", (), {"start": lambda self: started.append((args, kwargs))})())
    code, body = sh.request_manual(FakeSchwab(), AFTER_CLOSE)
    assert code == 202 and body["ok"] and len(started) == 1
    sh._READING.update(on=False)
    code, body = sh.request_manual(FakeSchwab(), AFTER_CLOSE.replace(minute=59))
    assert code == 429 and "hour" in body["message"] and len(started) == 1
    code, _ = sh.request_manual(FakeSchwab(), AFTER_CLOSE.replace(hour=17, minute=46))
    assert code == 202 and len(started) == 2


def test_a_read_in_progress_refuses_a_second(monkeypatch):
    sh._READING.update(on=True)
    code, body = sh.request_manual(FakeSchwab(), AFTER_CLOSE)
    assert code == 409 and "Already reading" in body["message"]
    fake = FakeSchwab()
    assert sh.run_daily_if_due(fake, AFTER_CLOSE) is None and fake.urls == []


def test_the_daily_budget_caps_gets(monkeypatch):
    monkeypatch.setattr(sh, "MAX_GETS_PER_DAY", 2)
    fake = FakeSchwab()
    out = sh.run_daily_if_due(fake, AFTER_CLOSE)
    assert out["kind"] == "budget" and len(fake.urls) == 2
    assert sh.status(AFTER_CLOSE)["stopped"]


def test_merge_keeps_one_copy_per_fill():
    store = {"fills": {}}
    fills = sh.parse_transactions([tx("1", "2026-11-10T15:05:52+0000", "SPY   261110C00750000", 1, 1.0)], "…5678")
    assert sh.merge(store, fills) == 1 and sh.merge(store, fills) == 0 and len(store["fills"]) == 1


# ---------------------------------------------------------------- expiry vs exercise vs assignment (2026-11-12)
def delivery(activity, symbol, option_amount, description="", stock_amount=None, stock_price=None):
    items = [{"instrument": {"assetType": "OPTION", "symbol": symbol}, "amount": option_amount, "cost": 0, "price": 0.0,
              "positionEffect": "CLOSING"}]
    if stock_amount is not None:
        items.append({"instrument": {"assetType": "EQUITY", "symbol": symbol.split()[0]}, "amount": stock_amount,
                      "price": stock_price, "cost": -stock_amount * stock_price})
    return {"activityId": activity, "type": "RECEIVE_AND_DELIVER", "time": "2026-11-07T05:00:00+0000",
            "description": description, "transferItems": items}


def test_a_plain_expiry_closes_at_zero():
    rows = sh.parse_transactions([delivery("9", "AAPL  261106C00250000", -1, "REMOVAL OF OPTION DUE TO EXPIRATION")], "…5678")
    assert [(f["kind"], f["price"], f["valueKnown"], f["side"]) for f in rows] == [("expired", 0.0, True, "SELL")]


def test_an_exercise_is_not_an_expiry_and_its_value_is_not_made_up():
    """Schwab delivers the stock at the strike, so the row does not show what the call was worth: no $0 close."""
    rows = sh.parse_transactions([delivery("9", "AAPL  261106C00250000", -1, "OPTION EXERCISE", 100, 250.0)], "…5678")
    assert [(f["kind"], f["valueKnown"], f["assetType"]) for f in rows] == [("exercised", False, "OPTION")]
    # Without the description, the stock leg alone still tells an exercise from an expiry.
    rows = sh.parse_transactions([delivery("9", "AAPL  261106C00250000", -1, "", 100, 250.0)], "…5678")
    assert rows[0]["kind"] == "exercised" and rows[0]["valueKnown"] is False
    # When the delivered stock's price is the market price, the call closes at its intrinsic value.
    rows = sh.parse_transactions([delivery("9", "AAPL  261106C00250000", -1, "OPTION EXERCISE", 100, 254.5)], "…5678")
    assert (rows[0]["kind"], rows[0]["price"], rows[0]["valueKnown"]) == ("exercised", 4.5, True)


def test_an_assignment_of_a_short_put():
    rows = sh.parse_transactions([delivery("9", "NVDA  261114P00190000", 1, "OPTION ASSIGNMENT", 100, 190.0)], "…5678")
    assert [(f["kind"], f["side"], f["valueKnown"]) for f in rows] == [("assigned", "BUY", False)]
    rows = sh.parse_transactions([delivery("9", "NVDA  261114P00190000", 1, "", 100, 186.0)], "…5678")
    assert (rows[0]["kind"], rows[0]["price"], rows[0]["valueKnown"]) == ("assigned", 4.0, True)


def test_an_exercised_call_is_not_a_total_loss_end_to_end():
    from momx import my_insights as mi
    buy = tx("2", "2026-11-06T14:00:00+0000", "AAPL  261106C00250000", 1, 1.00)
    fills = sh.parse_transactions([buy, delivery("3", "AAPL  261106C00250000", -1, "OPTION EXERCISE", 100, 250.0)], "…5678")
    trades, open_ = mi.pair_trades(fills, "2026-11-12")
    assert open_ == [] and len(trades) == 1
    assert trades[0]["valueUnknown"] and not trades[0]["expired"] and trades[0]["event"] == "exercised"
    assert mi.habit_held_past_50([x for x in trades if not x["valueUnknown"]], {})["count"] == 0


# ---------------------------------------------------------------- the account hash never leaks
class _HashURLSession:
    """A real requests Session whose adapter answers accountNumbers and then fails the
    /transactions GET with the real ConnectionError(MaxRetryError(...)) requests raises when the
    PC loses its connection. Its message carries the full URL, account hash and query included.
    Nothing is sent over the network."""

    def __new__(cls):
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.exceptions import MaxRetryError, NewConnectionError

        class Adapter(HTTPAdapter):
            def send(self, request, **kwargs):
                if "accountNumbers" in request.url:
                    response = requests.Response()
                    response.status_code, response.url, response.request = 200, request.url, request
                    response._content = json.dumps([{"accountNumber": "12345678", "hashValue": HASH}]).encode()
                    return response
                path = request.url.split("api.schwabapi.com", 1)[1]
                reason = NewConnectionError(None, "Failed to establish a new connection: [Errno 11001] getaddrinfo failed")
                raise requests.exceptions.ConnectionError(MaxRetryError(None, path, reason), request=request)

        session = requests.Session()
        session.mount("https://", Adapter())
        return session


def test_a_network_error_never_stores_or_shares_the_account_hash(scratch):
    from data import schwab_rate_limit
    gate = schwab_rate_limit.GATE
    gate.reset()
    session = _HashURLSession()

    class Client:
        def _refuse_if_refresh_token_dead(self):
            return None

        def _library_client(self):
            return type("L", (), {"session": session})()
    try:
        out = sh.run_daily_if_due(sh.schwab_transport(lambda: Client()), AFTER_CLOSE)
        assert out["ok"] is False and out["kind"] == "network"
        state = sh.load_state()
        shared = gate.state()
        raw = (scratch / "schwab_read.json").read_text(encoding="utf-8")
        for where, text in (("stored lastError", str(state.get("lastError"))), ("status()", json.dumps(sh.status(AFTER_CLOSE))),
                            ("GATE state", json.dumps(shared)), ("schwab_read.json", raw)):
            assert HASH not in text, where
            assert "startDate" not in text and "types=" not in text, where      # nor the query (dates)
        assert shared["lastFailureKind"] == "network" and "ConnectionError" in shared["lastFailureDetail"]
        assert "ConnectionError" in state["lastError"]
    finally:
        gate.reset()


def test_scrub_removes_hashes_and_queries():
    text = f"Max retries exceeded with url: /trader/v1/accounts/{HASH}/transactions?types=TRADE&startDate=2026-08-01 (x)"
    out = sh._scrub(text)
    assert HASH not in out and "startDate" not in out and "/accounts/<account>/transactions" in out
