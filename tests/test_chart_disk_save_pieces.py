"""The chart disk save must encode in small pieces, once, byte-identical.

Measured 2026-10-06 19:18 ET (py-spy --gil, 40 s): 20.6% of all GIL time was
_save_oi_finder_chart_disk_payload running json.dumps over the whole 4-9 MB
payload twice per save (fingerprint + file), each one long C call that froze
every chart request landing on it (NVDA open 1.87 s).
"""

from __future__ import annotations

import inspect
import json
import unittest

import api_server
from api_server import DashboardState, _json_dict_pieces


def payload() -> dict:
    bars = [{"time": 1_700_000_000 + i * 60, "open": 1.5, "close": i / 7, "label": "é"} for i in range(4000)]
    return {
        "symbol": "NVDA",
        "bars": bars,
        "studyBars": bars[:1499],
        "empty": [],
        "nested": {"a": [1, 2, {"b": None}], "c": True},
        "odd": object.__name__,
        "updatedAt": "2026-10-06T19:20:00",
    }


class ChartDiskSavePieces(unittest.TestCase):
    def test_pieces_join_to_exactly_json_dumps(self) -> None:
        data = payload()
        pieces = _json_dict_pieces(data, separators=(",", ":"), default=str)
        joined = "{" + ",".join(piece for _, piece in pieces) + "}"
        self.assertEqual(joined, json.dumps(data, separators=(",", ":"), default=str))

    def test_fingerprint_body_matches_dumps_without_updated_at(self) -> None:
        data = payload()
        pieces = _json_dict_pieces(data, separators=(",", ":"), default=str)
        body = dict(data)
        body.pop("updatedAt")
        self.assertEqual(
            "{" + ",".join(piece for key, piece in pieces if key != "updatedAt") + "}",
            json.dumps(body, separators=(",", ":"), default=str),
        )

    def test_save_does_not_dump_the_whole_payload(self) -> None:
        source = inspect.getsource(DashboardState._save_oi_finder_chart_disk_payload)
        self.assertNotIn("json.dumps(stamped", source)
        self.assertNotIn("json.dumps(body", source)
        self.assertIn("_json_dict_pieces(stamped", source)


class ResponseBodyPieces(unittest.TestCase):
    def test_response_body_matches_one_json_dumps(self) -> None:
        handler = api_server.ApiHandler.__new__(api_server.ApiHandler)
        handler.headers = {}
        data = payload()
        body, encoding = handler._json_response_body(data)
        self.assertEqual(encoding, "")
        self.assertEqual(body, json.dumps(api_server._serialize_value(data)).encode("utf-8"))

    def test_response_body_is_not_one_call(self) -> None:
        source = inspect.getsource(api_server.ApiHandler._json_response_body)
        self.assertIn("_json_dict_pieces(serialized", source)


if __name__ == "__main__":
    unittest.main()
