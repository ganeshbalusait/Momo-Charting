"""Every tests/test_cov_*.py test runs under the live guards (tests/conftest.py),
even outside a harness. If this file passes, they are switched on."""
from __future__ import annotations

import socket

import pytest

from live_guard import REPO


def test_coverage_tests_cannot_reach_the_live_scanner():
    with pytest.raises(ConnectionRefusedError, match="live app on port 3010"):
        socket.create_connection(("127.0.0.1", 3010), timeout=2)


def test_coverage_tests_cannot_write_live_files():
    with pytest.raises(PermissionError, match="may not change live files"):
        (REPO / "artifacts" / "cov_guard_probe.txt").write_text("x")
