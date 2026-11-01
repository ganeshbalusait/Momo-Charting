from http import HTTPStatus
from unittest.mock import Mock

import pytest

from momx_worker import Handler


def handler():
    h = Handler.__new__(Handler)
    h.headers = {}
    h.send_response = Mock()
    h.send_header = Mock()
    h.end_headers = Mock()
    h.wfile = Mock()
    h.close_connection = False
    return h


@pytest.mark.parametrize("failure", [BrokenPipeError, ConnectionResetError, ConnectionAbortedError])
@pytest.mark.parametrize("stage", ["headers", "body"])
def test_departed_scanner_client_closes_without_a_second_response(failure, stage):
    h = handler()
    (h.end_headers if stage == "headers" else h.wfile.write).side_effect = failure("client departed")
    h._json(HTTPStatus.OK, {"rows": []})
    assert h.close_connection is True
    h.send_response.assert_called_once_with(HTTPStatus.OK)


def test_unrelated_worker_io_error_remains_visible():
    h = handler()
    h.wfile.write.side_effect = OSError("unrelated I/O failure")
    with pytest.raises(OSError, match="unrelated I/O"):
        h._json(HTTPStatus.OK, {"rows": []})
