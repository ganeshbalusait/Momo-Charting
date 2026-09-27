"""The chart endpoint used to ship 4.5 MB of uncompressed JSON per symbol.

It is almost entirely numeric OHLCV, which deflates to roughly a fifth, so the
saving is large and free — but only if the negotiation is right. Compressing
for a client that never asked, or mislabelling the body, breaks the response
outright rather than merely making it slow.
"""

from __future__ import annotations

import gzip
import json

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
