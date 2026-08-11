from __future__ import annotations

import base64
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from io import BytesIO

from data.schwab_client import SchwabClient


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self) -> bytes:
        return json.dumps({"access_token": "new-token", "refresh_token": "refresh"}).encode("utf-8")


class SchwabOAuthTests(unittest.TestCase):
    def test_token_exchange_uses_single_basic_auth_request(self) -> None:
        client = SchwabClient()
        client.config.client_id = "app-key"
        client.config.client_secret = "app-secret"

        with patch("data.schwab_client.urlopen", return_value=_Response()) as mocked_urlopen:
            token = client._post_token(
                {
                    "grant_type": "authorization_code",
                    "code": "fresh-code",
                    "redirect_uri": "https://127.0.0.1/",
                }
            )

        self.assertEqual(token["access_token"], "new-token")
        mocked_urlopen.assert_called_once()
        request = mocked_urlopen.call_args.args[0]
        expected = base64.b64encode(b"app-key:app-secret").decode("ascii")
        self.assertEqual(request.get_header("Authorization"), f"Basic {expected}")
        self.assertIn("AgenticAI-Trading", request.get_header("User-agent"))
        body = request.data.decode("utf-8")
        self.assertIn("grant_type=authorization_code", body)
        self.assertNotIn("client_secret", body)
        self.assertNotIn("client_id", body)

    def test_akamai_html_error_is_reduced_to_safe_actionable_message(self) -> None:
        client = SchwabClient()
        error = HTTPError(
            "https://api.schwabapi.com/v1/oauth/token",
            403,
            "Forbidden",
            {},
            BytesIO(
                b"<HTML><BODY><H1>Access Denied</H1>"
                b"<P>Reference&#32;&#35;18&#46;3417dd17&#46;1784172225&#46;f8de3e</P>"
                b"</BODY></HTML>"
            ),
        )

        message = client._decode_http_error(error)

        self.assertIn("Schwab's API gateway denied", message)
        self.assertIn("Reference #18.3417dd17.1784172225.f8de3e", message)
        self.assertNotIn("<HTML>", message)


if __name__ == "__main__":
    unittest.main()
