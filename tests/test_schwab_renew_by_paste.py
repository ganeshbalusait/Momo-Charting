"""Unit: renewing Schwab by pasting the sign-in address (app.agxtrade.com, any
computer - 2026-11-04). Which box an address belongs to, and the page the PC's
catcher shows for a login it was not told about."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import schwab_oauth_callback as cb
from data.schwab_client import SchwabClient

URL = "https://127.0.0.1/?code=C0.abc%40&session=s1&state={state}"


def test_the_address_of_the_waiting_login_is_accepted():
    assert cb.pasted_login_problem(URL.format(state="MD"), "market_data", "MD", "TR") is None
    assert cb.pasted_login_problem(URL.format(state="TR"), "trading", "TR", "MD") is None


def test_a_market_data_address_in_the_trading_box_names_the_right_box():
    problem = cb.pasted_login_problem(URL.format(state="MD"), "trading", "TR", "MD")
    assert problem == "This address is from the Market Data sign-in. Paste it into the Market Data callback URL box instead."


def test_a_trading_address_in_the_market_data_box_names_the_right_box():
    problem = cb.pasted_login_problem(URL.format(state="TR"), "market_data", "MD", "TR")
    assert "Accounts & Trading sign-in" in problem and "Accounts & Trading callback URL box" in problem


def test_an_address_from_an_older_attempt_says_to_renew_again():
    problem = cb.pasted_login_problem(URL.format(state="OLD"), "market_data", "MD", "TR")
    assert problem.startswith("This address is from an older Market Data sign-in.")
    assert "click Renew both again" in problem


def test_nothing_waiting_says_to_start_with_renew_both():
    problem = cb.pasted_login_problem(URL.format(state="X"), "trading", None, None)
    assert problem == "No Accounts & Trading sign-in is waiting. Click Renew both, sign in to Schwab, then paste the new address here."


def test_nothing_waiting_here_but_waiting_in_the_other_box_names_that_box():
    problem = cb.pasted_login_problem(URL.format(state="MD"), "trading", None, "MD")
    assert "Paste it into the Market Data callback URL box instead." in problem


@pytest.mark.parametrize("pasted", ["", "hello", "https://127.0.0.1/", "https://127.0.0.1/?state=MD", "https://127.0.0.1/?code=abc"])
def test_something_that_is_not_a_sign_in_address_is_refused(pasted):
    assert cb.pasted_login_problem(pasted, "market_data", "MD", None).startswith("This is not a Schwab sign-in address.")


def test_the_catchers_page_for_an_unknown_login_says_how_to_paste(monkeypatch):
    monkeypatch.setattr(cb, "_PENDING", {})
    status, page, stop = cb.handle_callback("/?code=abc&state=NOT-WAITING")
    assert (status, stop) == (400, False)
    assert "not the one AGX is waiting for" in page
    assert "Renewing from app.agxtrade.com? Copy this page's whole address" in page


def test_a_client_reports_the_state_of_the_login_it_waits_on(tmp_path, monkeypatch):
    monkeypatch.setattr(SchwabClient, "_pending_auth_contexts", {})
    config = SimpleNamespace(client_id="app-key", client_secret="app-secret", redirect_uri="https://127.0.0.1",
                             token_path=str(tmp_path / "token.json"), include_extended_hours=True)
    client = SchwabClient(config)
    assert client.pending_state() is None
    url = client.begin_authorization()            # schwab-py builds the link locally: no network call
    state = client.pending_state()
    assert state and f"state={state}" in url
    other = SchwabClient(SimpleNamespace(**{**vars(config), "token_path": str(tmp_path / "other.json")}))
    assert other.pending_state() is None, "each token file waits on its own login"
