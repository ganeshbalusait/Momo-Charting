"""The chart endpoint used to ship 4.5 MB of uncompressed JSON per symbol.

It is almost entirely numeric OHLCV, which deflates to roughly a fifth, so the
saving is large and free — but only if the negotiation is right. Compressing
for a client that never asked, or mislabelling the body, breaks the response
outright rather than merely making it slow.
"""

from __future__ import annotations

import gzip
import json
from datetime import datetime, date, timezone
from unittest.mock import patch
import numpy as np
import pandas as pd
import pytest
from unittest.mock import Mock
from http import HTTPStatus

from api_server import ApiHandler


def _handler(accept_encoding: str | None):
    # BaseHTTPRequestHandler's __init__ wants a live socket; the body builder
    # only reads self.headers, so bypass construction.
    handler = ApiHandler.__new__(ApiHandler)
    handler.headers = {} if accept_encoding is None else {"Accept-Encoding": accept_encoding}
    return handler


def _big_payload() -> dict:
    return {"bars": [{"time": 1785502800 + i * 60, "open": 1.5, "high": 2.5, "low": 0.5,
                      "close": 2.0, "volume": 100} for i in range(500)]}


def test_large_payload_is_gzipped_when_the_client_offers_gzip() -> None:
    payload = _big_payload()
    body, encoding = _handler("gzip, deflate, br")._json_response_body(payload)
    assert encoding == "gzip"
    assert len(body) < len(json.dumps(payload).encode("utf-8")) / 2
    # The client must get back exactly what it would have got uncompressed.
    assert json.loads(gzip.decompress(body).decode("utf-8")) == payload


def test_payload_is_untouched_when_the_client_does_not_offer_gzip() -> None:
    payload = _big_payload()
    body, encoding = _handler(None)._json_response_body(payload)
    assert encoding == ""
    assert json.loads(body.decode("utf-8")) == payload


def test_gzip_with_q_zero_is_a_refusal_not_an_offer() -> None:
    _, encoding = _handler("gzip;q=0, identity")._json_response_body(_big_payload())
    assert encoding == ""


def test_small_payloads_skip_compression_entirely() -> None:
    # Below the threshold the header and the CPU cost more than the saving.
    body, encoding = _handler("gzip")._json_response_body({"ok": True})
    assert encoding == ""
    assert json.loads(body.decode("utf-8")) == {"ok": True}


def test_threshold_is_measured_on_the_encoded_body() -> None:
    just_over = {"pad": "x" * (ApiHandler.GZIP_MIN_BYTES + 100)}
    _, encoding = _handler("gzip")._json_response_body(just_over)
    assert encoding == "gzip"


def test_native_chart_reply_does_not_clone_every_bar_before_encoding() -> None:
    payload = _big_payload()
    # The converter should only see special values, not every dict/number.
    with patch("api_server._serialize_value", side_effect=AssertionError("walked native chart data")):
        body, encoding = _handler(None)._json_response_body(payload)
    assert encoding == ""
    assert body == json.dumps(payload).encode("utf-8")


def test_special_scalar_reply_matches_existing_conversion_byte_for_byte() -> None:
    from api_server import _serialize_value
    payload = {"when": datetime(2026, 10, 29, tzinfo=timezone.utc), "day": date(2026, 10, 29),
               "nested": [(np.int64(3), np.float32(1.25), pd.NA, None)], "text": "Γ café"}
    body, encoding = _handler(None)._json_response_body(payload)
    assert encoding == ""
    assert body == json.dumps(_serialize_value(payload)).encode("utf-8")


def test_nonfinite_chart_values_keep_the_legacy_na_conversion() -> None:
    from api_server import _serialize_value
    payload = {"bars": [{"time": 123, "close": float("nan")}], "inf": float("inf")}
    body, encoding = _handler(None)._json_response_body(payload)
    assert encoding == ""
    assert body == json.dumps(_serialize_value(payload)).encode("utf-8")
    assert json.loads(body)["bars"][0]["close"] is None


def test_chart_reply_keeps_bounded_encoding_without_forced_scheduler_pauses() -> None:
    payload = {"bars": [{"time": i, "close": i / 7} for i in range(3001)]}
    with patch("api_server.time.sleep", side_effect=AssertionError("reply yielded to background jobs")):
        body, _ = _handler(None)._json_response_body(payload)
    assert body == json.dumps(payload).encode("utf-8")


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
@pytest.mark.parametrize("stage", ["headers", "body"])
def test_cancelled_json_response_closes_without_a_second_reply(failure, stage):
    handler = _handler("gzip")
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock(side_effect=failure() if stage == "headers" else None)
    handler.wfile = Mock()
    if stage == "body":
        handler.wfile.write.side_effect = failure()
    handler._send_json(HTTPStatus.OK, _big_payload())
    assert handler.close_connection is True
    handler.send_response.assert_called_once_with(200)
    if stage == "headers":
        handler.wfile.write.assert_not_called()


def test_json_response_still_propagates_unrelated_io_errors():
    handler = _handler(None)
    handler.send_response = Mock()
    handler.send_header = Mock()
    handler.end_headers = Mock(side_effect=PermissionError("fixture"))
    with pytest.raises(PermissionError):
        handler._send_json(HTTPStatus.OK, {"ok": True})


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
def test_cancelled_keepalive_read_closes_cleanly(failure):
    handler = _handler(None)
    with patch("http.server.BaseHTTPRequestHandler.handle_one_request", side_effect=failure()):
        handler.handle_one_request()
    assert handler.close_connection is True
