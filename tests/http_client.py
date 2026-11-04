"""JSON-over-HTTP test client with a cookie jar (shared by api_harness and
worker_harness). Light on purpose: importing it must not import api_server."""
from __future__ import annotations

import gzip
import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ApiClient:
    """JSON over HTTP with a cookie jar, so sign-in flows carry their session.
    Every call returns (status, body); the response headers are in last_headers."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self.cookies: dict[str, str] = {}
        self.last_headers = None

    def request(self, method: str, path: str, payload=None, headers: dict | None = None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        sent = {"Content-Type": "application/json", **(headers or {})}
        if self.cookies and "Cookie" not in sent:
            sent["Cookie"] = "; ".join(f"{name}={value}" for name, value in self.cookies.items())
        request = Request(f"{self.base_url}{path}", data=data, method=method, headers=sent)
        try:
            with urlopen(request, timeout=20) as response:
                return response.status, self._read(response)
        except HTTPError as error:
            return error.code, self._read(error)

    def _read(self, response):
        self.last_headers = response.headers
        for cookie in response.headers.get_all("Set-Cookie") or []:
            name, _, rest = cookie.partition("=")
            value = rest.split(";", 1)[0]
            if value and "max-age=0" not in cookie.lower():
                self.cookies[name.strip()] = value
            else:
                self.cookies.pop(name.strip(), None)
        return decode(response.read(), response.headers.get("Content-Encoding"))

    def get(self, path: str, headers: dict | None = None):
        return self.request("GET", path, headers=headers)

    def post(self, path: str, payload=None, headers: dict | None = None):
        return self.request("POST", path, {} if payload is None else payload, headers=headers)


def decode(raw: bytes, encoding: str | None = None):
    if encoding == "gzip":
        raw = gzip.decompress(raw)
    text = raw.decode("utf-8", errors="replace")
    try:
        return json.loads(text) if text else None
    except ValueError:
        return text
