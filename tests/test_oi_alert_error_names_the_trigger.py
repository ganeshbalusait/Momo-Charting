"""An OI-ladder card must name whatever broke FIRST.

2026-08-28 00:53: the AAPL auto-alert card read

    AAPL: Tradier request failed (HTTP 401): {"fault":{"faultstring":
    "Access Token not approved" ...

which sends the trader to renew a Tradier key. But Tradier is only the
FALLBACK. oi_finder_payload tries Schwab/TOS first and drops to Tradier when
Schwab fails, and the Schwab exception was swallowed by a bare `except
Exception:`. So the trigger was a Schwab blink and the card blamed the backup.
A forced rebuild minutes later succeeded on Schwab, which is the proof.
"""

from __future__ import annotations

import api_server


BOTH = (
    "Schwab/TOS failed (HTTPSConnectionPool: Read timed out); the Tradier "
    "fallback also failed (Tradier request failed (HTTP 401): "
    '{"fault":{"faultstring":"Access Token not approved",'
    '"detail":{"errorcode":"keymanagement.service.access_token_not_approved"}}}).'
)


def test_a_double_failure_names_schwab_before_tradier():
    plain = api_server._oi_auto_alert_plain_error(BOTH)
    assert plain.index("Schwab") < plain.index("Tradier")


def test_a_double_failure_still_tells_him_where_to_fix_the_token():
    assert "Settings" in api_server._oi_auto_alert_plain_error(BOTH)


def test_a_double_failure_still_reports_whether_levels_survived():
    assert "still armed" in api_server._oi_auto_alert_plain_error(BOTH, kept=True)
    assert "still armed" not in api_server._oi_auto_alert_plain_error(BOTH)


def test_a_tradier_only_failure_is_unchanged():
    # When Tradier really is the only thing that failed, keep the old wording.
    plain = api_server._oi_auto_alert_plain_error(
        "Tradier request failed (HTTP 401): access_token_not_approved"
    )
    assert plain == "Tradier's access token is not approved - renew it in Settings."


def test_the_fallback_path_keeps_the_schwab_exception():
    # The bare `except Exception:` that discarded it is what made the card lie.
    import inspect

    source = inspect.getsource(api_server.DashboardState._oi_finder_payload_impl)
    assert "except Exception as schwab_exc:" in source
    assert "the Tradier fallback also" in source


def test_an_empty_error_is_still_readable():
    assert api_server._oi_auto_alert_plain_error("") == "The OI ladder could not be rebuilt."
