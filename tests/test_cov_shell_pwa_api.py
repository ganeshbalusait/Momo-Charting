"""Integration: the BUILT app as the installed PWA receives it - frontend/dist
served by the real `vite preview` (the same server and cache-header plugin
that answers app.agxtrade.com on :4173), on a free port of its own (coverage
2026-11-04, group "shell").

Read-only: dist/ is served, never rebuilt (the live repo's `vite build --watch`
owns it), and the config is loaded natively so Vite writes no temp bundle.
Skipped when this checkout has no build or no node.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest

FRONTEND = Path(__file__).resolve().parents[1] / "frontend"
DIST = FRONTEND / "dist"
VITE = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _get(url):
    try:
        with urlopen(Request(url, headers={"Accept-Encoding": "identity"}), timeout=10) as response:
            return response.status, response.headers, response.read()
    except HTTPError as error:
        return error.code, error.headers, error.read()


@pytest.fixture(scope="module")
def preview():
    if not (DIST / "index.html").exists() or not VITE.exists() or shutil.which("node") is None:
        pytest.skip("no built frontend/dist, vite or node in this checkout")
    port = _free_port()
    process = subprocess.Popen(
        ["node", str(VITE), "preview", "--port", str(port), "--strictPort", "--host", "127.0.0.1", "--configLoader", "native"],
        cwd=FRONTEND, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 45
        while True:
            try:
                if _get(base + "/")[0] == 200:
                    break
            except (URLError, OSError):
                pass
            if process.poll() is not None or time.monotonic() > deadline:
                output = process.stdout.read().decode(errors="replace") if process.poll() is not None else ""
                pytest.fail(f"vite preview did not come up on :{port}: {output[-800:]}")
            time.sleep(0.3)
        yield base
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()


def test_the_service_worker_is_served_fresh_and_matches_the_source(preview):
    status, headers, body = _get(preview + "/sw.js")
    assert status == 200
    assert "javascript" in headers["Content-Type"]
    assert headers["Cache-Control"] == "no-cache", "a cached sw.js is how a phone gets pinned to an old build"
    assert body == (FRONTEND / "public" / "sw.js").read_bytes(), "dist/sw.js drifted from public/sw.js"


def test_the_manifest_and_every_icon_it_names_are_served(preview):
    status, headers, body = _get(preview + "/manifest.webmanifest")
    assert status == 200
    manifest = json.loads(body)
    assert manifest["start_url"] == "/" and manifest["display"] == "standalone"
    for icon in manifest["icons"] + [{"src": "/icons/apple-touch-icon.png"}]:
        icon_status, icon_headers, icon_body = _get(preview + icon["src"])
        assert icon_status == 200, icon["src"]
        assert icon_headers["Content-Type"].startswith("image/png"), icon["src"]
        assert icon_body[:8] == b"\x89PNG\r\n\x1a\n", f"{icon['src']} is not a PNG"


def test_the_page_links_the_manifest_with_credentials_and_its_bundle_exists(preview):
    status, headers, body = _get(preview + "/")
    html = body.decode("utf-8")
    assert status == 200 and headers["Cache-Control"] == "no-cache"
    assert '<link rel="manifest" href="/manifest.webmanifest" crossorigin="use-credentials"' in html
    import re
    entry = re.search(r'<script type="module"[^>]*src="(/assets/[^"]+\.js)"', html)
    assert entry, "the built page names no hashed entry bundle"
    bundle_status, bundle_headers, _ = _get(preview + entry.group(1))
    assert bundle_status == 200
    assert bundle_headers["Cache-Control"] == "public, max-age=31536000, immutable"
    assert "charset=utf-8" in bundle_headers["Content-Type"]


def test_the_release_notes_ride_beside_the_bundle(preview):
    status, _, body = _get(preview + "/release-notes.json")
    assert status == 200
    assert json.loads(body)["releases"], "the installed app would show an empty Release Notes page"
