"""agx-api gateway - A1 scaffold. NOT in service yet.

Spec: docs/superpowers/specs/2026-08-19-process-split-design.md

This process serves pre-built payload blobs and reverse-proxies everything
else to the pipeline (today's api_server). It exists so the serving GIL never
shares a process with the study replays.

SAFETY CONTRACT while this is a scaffold:
- Nothing imports this module and nothing launches it. It binds :3005 by
  default - deliberately NOT :3001 - so an accidental launch cannot shadow
  the live server.
- This module must never import api_server (module-level STATE boots the
  entire app - verified 2026-08-19) or any Schwab client (exactly one process
  may touch the token; it is not this one). tests/test_gateway.py enforces
  both with sys.modules assertions.

Cutover (A1, not tonight): pipeline moves to :3002, this binds :3001,
watchdog gains a second service entry. Rollback is the reverse.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import queue
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, time as clock_time, timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from cf_access_auth import ACCESS_EMAIL_HEADER, verified_email
from cf_access_keys import AccessKeyCache
from request_trust import ORIGIN_HEADER, ORIGIN_RELAYED, origin_stamp

# Cloudflare Access identity. This is the only hop that sees the signed token -
# the pipeline never does - so verification has to happen here. Unset settings
# switch the whole thing off and the ordinary password login takes over.
load_dotenv(Path(__file__).resolve().parent / ".env")
CF_ACCESS_TEAM_DOMAIN = str(os.getenv("CF_ACCESS_TEAM_DOMAIN", "") or "").strip()
CF_ACCESS_AUD = str(os.getenv("CF_ACCESS_AUD", "") or "").strip()
ACCESS_KEYS = AccessKeyCache(CF_ACCESS_TEAM_DOMAIN)

EASTERN = ZoneInfo("America/New_York")
ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
CHART_BLOBS = ARTIFACTS / "oi_chart_cache"
CHAIN_BLOBS = ARTIFACTS / "oi_chain_cache"

# Mirrors OI_FINDER_CHART_STALE_TAPE_SECONDS in the pipeline. Duplicated on
# purpose: importing api_server is forbidden here (see module docstring), and
# A3 unifies the constants into a shared leaf module.
STALE_TAPE_SECONDS = 26 * 60 * 60

# How old a chain blob may be and still answer a live request without the
# pipeline. The payload's own refreshSeconds is 15, so 30s is one missed
# rebuild: still enough to absorb a phone-wake burst, far too short to serve
# a premarket snapshot at 10:00 ET.
#
# WHY THIS EXISTS (2026-08-31, "High OI board Vol column blank on every row"):
# chain_blob_is_servable had no age rule at all - unlike its sibling
# chart_blob_is_fresh - so ANY blob served forever. The 09:15 ET
# _oi_auto_alert_refresh_levels(reason="morning") run persists a PRE-OPEN
# chain, and Schwab reports totalVolume == 0 for every contract before the
# bell. Measured 10:24 ET on NFLX through :3001: scannedAt 09:15:25, 454
# rows, 0 with volume > 0, 392 with open_interest > 0. Health counters that
# session read chainBlob 37790 vs chainProxy 12580 - three quarters of chain
# reads never reached the pipeline that could have refreshed them.
CHAIN_BLOB_MAX_AGE_SECONDS = 30.0

# A blob stamped in the future is a clock problem, not freshness. Small skew
# is tolerated; anything past this is treated as unusable and proxied.
CHAIN_BLOB_MAX_SKEW_SECONDS = 60.0
SYMBOL_RE = re.compile(r"[A-Z][A-Z0-9.-]{0,9}")

# Endpoints the gateway may answer from blobs. Everything else - auth,
# dashboard, mutations, SSE - is proxied verbatim in A1.
HOT_CHART_PATH = "/api/oi-finder-chart"
HOT_CHAIN_PATHS = {"/api/oi-finder", "/api/oi-finder-chain"}


# --------------------------------------------------------------------------- #
# Pure rules - unit-tested, no I/O
# --------------------------------------------------------------------------- #
def most_recent_session_start(now: datetime) -> datetime:
    """4:00 AM ET of the most recent trading day (premarket open).

    Same intent as the pipeline's _most_recent_session_start: a tape that
    does not reach the CURRENT session is stale however new the 26h window
    says it looks (the SPY defect, commit 137758a).
    """
    eastern = now.astimezone(EASTERN)
    candidate = eastern.replace(hour=4, minute=0, second=0, microsecond=0)
    if eastern < candidate:
        candidate -= timedelta(days=1)
    while candidate.weekday() >= 5:  # Sat/Sun -> back to Friday
        candidate -= timedelta(days=1)
    return candidate


def chart_blob_is_fresh(payload: dict | None, now: datetime) -> bool:
    """Can this chart blob answer a live request without the pipeline?

    Fresh means BOTH: newest bar inside the 26h window AND reaching the
    current session. A blob mid-rebuild (historyLoading) is still servable -
    the client renders the warming states honestly - but an EMPTY one is not.
    """
    if not isinstance(payload, dict):
        return False
    bars = payload.get("bars")
    if not isinstance(bars, list) or not bars:
        return False
    try:
        newest = int(bars[-1].get("time") or 0)
    except (TypeError, ValueError, AttributeError):
        return False
    if newest <= 0:
        return False
    age = now.timestamp() - newest
    if age > STALE_TAPE_SECONDS:
        return False
    return newest >= most_recent_session_start(now).timestamp()


def chain_blob_age_seconds(payload: dict | None, now: datetime) -> float | None:
    """Seconds since the blob's own scannedAt stamp, or None if it has none.

    None is NOT "fresh" - it is "cannot be proven fresh", and every caller
    must proxy on it. An unparseable stamp is the same answer as a missing
    one: the gateway has no second source of truth for this file.
    """
    if not isinstance(payload, dict):
        return None
    raw = payload.get("scannedAt")
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        stamped = float(raw)
        # Tolerate millisecond epochs from any future writer.
        if stamped > 1e11:
            stamped /= 1000.0
        return now.timestamp() - stamped
    text = str(raw or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=EASTERN)
    return now.timestamp() - stamp.timestamp()


def chain_blob_is_servable(
    payload: dict | None,
    compact: bool,
    research_section: str = "",
    now: datetime | None = None,
) -> bool:
    """Same rule as the pipeline's _oi_finder_chain_disk_is_servable (3a549b4):
    compact requests accept any non-empty blob; a FULL request additionally
    needs the expiry rows the options panel renders; and a RESEARCH request
    (section=heatmap/flow) accepts neither.

    The blob is the compact chain payload, so its dailyLiquidityHeatmap is
    always {}. Serving it answered section=heatmap with an empty heatmap and
    the request never reached the pipeline that can build one.

    PLUS an age rule (2026-08-31). Non-empty is not the same as current: an
    option chain is a now-feed, and a blob older than
    CHAIN_BLOB_MAX_AGE_SECONDS must be proxied so the pipeline can rebuild
    it. Without this the premarket snapshot served volume 0 all session.
    `now` defaults to the wall clock ON PURPOSE - a caller that forgets to
    pass it gets the strict rule, never the old serve-anything one."""
    if not isinstance(payload, dict) or not payload:
        return False
    if str(research_section or "").strip().lower():
        return False
    # Only the compact chain endpoint may be answered from the blob. A FULL
    # /api/oi-finder request wants the Finder analytics the blob does not
    # carry, and nothing behind the gateway will fill them in later.
    if not compact:
        return False
    age = chain_blob_age_seconds(payload, now or datetime.now(EASTERN))
    if age is None:
        return False
    if age < -CHAIN_BLOB_MAX_SKEW_SECONDS:
        return False
    return age <= CHAIN_BLOB_MAX_AGE_SECONDS


def normalize_symbol(raw: str) -> str:
    symbol = str(raw or "").strip().upper()
    return symbol if SYMBOL_RE.fullmatch(symbol) else ""


def load_blob(directory: Path, symbol: str) -> dict | None:
    """Read one payload blob. Returns None on any miss or damage - the
    caller falls back to proxying, never to an error page."""
    target = normalize_symbol(symbol)
    if not target:
        return None
    path = directory / f"{target}.json.gz"
    try:
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            payload = json.load(handle)
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, json.JSONDecodeError):
        return None


SSE_PATHS = {"/api/live-market-stream", "/api/live-option-stream"}


def sse_feed_key(path: str, query: dict) -> str:
    """One upstream per (endpoint, symbol set). Symbols are sorted so two
    browsers asking for "AAPL,SPY" and "SPY,AAPL" share a feed."""
    symbols = sorted({
        s.strip().upper()
        for raw in query.get("symbols", [])
        for s in str(raw).split(",") if s.strip()
    })
    return path + "?symbols=" + ",".join(symbols)


class SseHub:
    """A2 (spec 2026-08-19): N browser SSE connections share ONE upstream
    subscription to the pipeline per distinct symbol set, instead of one
    pipeline connection per browser tab. The pipeline's SSE load stops
    scaling with open tabs, and a gateway restart only costs browsers their
    EventSource auto-retry.

    Slow clients get a bounded queue with drop-oldest: live bars are a
    now-feed, and three-minute-old ticks are worse than missing ones.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._feeds = {}
        self.stats = {"sseClients": 0, "sseUpstreams": 0}

    def attach(self, key: str, upstream_url: str, handler) -> None:
        client = queue.Queue(maxsize=200)
        with self._lock:
            feed = self._feeds.get(key)
            if feed is None:
                feed = {"clients": [], "url": upstream_url}
                self._feeds[key] = feed
                thread = threading.Thread(
                    target=self._pump, args=(key,),
                    name="sse-hub-" + str(len(self._feeds)), daemon=True,
                )
                feed["thread"] = thread
                thread.start()
                self.stats["sseUpstreams"] = len(self._feeds)
            feed["clients"].append(client)
            self.stats["sseClients"] += 1
        try:
            handler.send_response(200)
            handler.send_header("Content-Type", "text/event-stream")
            handler.send_header("Cache-Control", "no-cache")
            handler.send_header("Connection", "close")
            handler.end_headers()
            handler.wfile.write(b": gateway sse hub\n\n")
            handler.wfile.flush()
            while True:
                try:
                    chunk = client.get(timeout=20.0)
                except queue.Empty:
                    chunk = b": keepalive\n\n"
                handler.wfile.write(chunk)
                handler.wfile.flush()
        except (ConnectionError, OSError):
            pass
        finally:
            with self._lock:
                feed = self._feeds.get(key)
                if feed and client in feed["clients"]:
                    feed["clients"].remove(client)
                self.stats["sseClients"] = max(0, self.stats["sseClients"] - 1)

    def _pump(self, key: str) -> None:
        """Read the pipeline's event stream and broadcast whole event blocks."""
        while True:
            with self._lock:
                feed = self._feeds.get(key)
                if not feed or not feed["clients"]:
                    self._feeds.pop(key, None)
                    self.stats["sseUpstreams"] = len(self._feeds)
                    return
                url = feed["url"]
            try:
                request = urllib.request.Request(url, headers={"Accept": "text/event-stream"})
                with urllib.request.urlopen(request, timeout=310) as upstream:
                    block = b""
                    while True:
                        line = upstream.readline()
                        if not line:
                            break
                        block += line
                        if line in (b"\n", b"\r\n"):
                            self._broadcast(key, block)
                            block = b""
                        with self._lock:
                            feed = self._feeds.get(key)
                            if not feed or not feed["clients"]:
                                break
            except (urllib.error.URLError, TimeoutError, OSError):
                # Pipeline restarting: tell browsers to be patient, then retry.
                self._broadcast(key, b": pipeline reconnecting\n\n")
                time.sleep(2.0)

    def _broadcast(self, key: str, chunk: bytes) -> None:
        with self._lock:
            feed = self._feeds.get(key)
            clients = list(feed["clients"]) if feed else []
        for client in clients:
            try:
                client.put_nowait(chunk)
            except queue.Full:
                try:  # drop-oldest: stale ticks are worse than missing ones
                    client.get_nowait()
                    client.put_nowait(chunk)
                except (queue.Empty, queue.Full):
                    pass


SSE_HUB = SseHub()
# Served-vs-proxied tallies, exposed on gateway-health. This is the number
# that decides whether shaping parity (blob-only shaped GETs) is ever worth
# building: if proxied shaped requests are a sliver of traffic, it is not.
SERVE_STATS = {
    "chartBlob": 0, "chartProxy": 0, "chainBlob": 0, "chainProxy": 0,
    # Chain requests that fell through to the pipeline because the blob
    # was too old to serve. This is the number that makes chain staleness
    # OBSERVABLE instead of silent: if chainBlobStale climbs while
    # chainBlob stays flat, the pipeline stopped rewriting blobs.
    "chainBlobStale": 0,
    "otherProxy": 0,
}
SERVE_STATS_LOCK = threading.Lock()


def _count(stat: str) -> None:
    with SERVE_STATS_LOCK:
        SERVE_STATS[stat] = SERVE_STATS.get(stat, 0) + 1


# --------------------------------------------------------------------------- #
# HTTP - skeleton. Blob serving implemented; shaping parity and the SSE
# bridge land in A1/A2 proper (see spec). Not bound to any port tonight.
# --------------------------------------------------------------------------- #
class GatewayHandler(BaseHTTPRequestHandler):
    server_version = "agx-api/0.1-scaffold"
    pipeline_base = "http://127.0.0.1:3002"
    # HTTP/1.1 so cloudflared can keep connections alive. Without this every
    # response is HTTP/1.0 close-after-send, so the tunnel must open a fresh
    # TCP connection PER REQUEST - multiplying pressure on the accept queue
    # that produced the "API is unavailable" 502s (diagnosis 2026-08-23).
    # Safe because _send_json, the _proxy JSON path, and the HTTPError relay
    # all set Content-Length, and the SSE branch sends Connection: close.
    protocol_version = "HTTP/1.1"

    # -- helpers ----------------------------------------------------------- #
    def _send_json(self, status: HTTPStatus, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":"), default=str).encode()
        accepts_gzip = "gzip" in (self.headers.get("Accept-Encoding") or "")
        encoding = ""
        if accepts_gzip and len(body) > 8192:
            body = gzip.compress(body, 1)
            encoding = "gzip"
        self.send_response(status.value)
        self.send_header("Content-Type", "application/json")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    # Request headers worth forwarding; everything else is either hop-by-hop
    # or reconstructed by urllib. Host is deliberately NOT forwarded - the
    # pipeline binds loopback and cares only about the path.
    FORWARD_REQUEST_HEADERS = (
        "Content-Type", "Cookie", "Accept", "Accept-Encoding",
        "Authorization", "Last-Event-ID", "Cache-Control", "If-None-Match",
    )
    # Response headers copied back verbatim. Content-Length / Transfer-Encoding
    # are handled explicitly per branch below.
    FORWARD_RESPONSE_HEADERS = (
        "Content-Type", "Content-Encoding", "Set-Cookie", "Cache-Control",
        "ETag", "Last-Modified",
    )

    def _proxy(self) -> None:
        """Pass-through to the pipeline.

        Two shapes on purpose:
        - text/event-stream responses are STREAMED chunk by chunk with a flush
          per chunk and no Content-Length - buffering an SSE body would hold
          live bars until the connection died, which the browser reads as a
          dead feed.
        - everything else is read fully and re-sent with Content-Length, which
          keeps ordinary JSON responses simple and keep-alive friendly.
        """
        url = f"{self.pipeline_base}{self.path}"
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        request = urllib.request.Request(url, data=body, method=self.command)
        for name in self.FORWARD_REQUEST_HEADERS:
            value = self.headers.get(name)
            if value:
                request.add_header(name, value)
        # This hop is the last one that can tell the trader's own browser from
        # a tunnel visitor: Host and every relay header are dropped above, and
        # the pipeline sees 127.0.0.1 either way. Record the verdict so the
        # pipeline's password-free grants (LOCAL_AUTO_LOGIN_EMAIL, the
        # sole-admin device bypass) cannot fire for a request off the internet.
        # Written after the allowlist loop, which cannot carry a client's own
        # copy of this header, so it is unspoofable.
        client_ip = str(self.client_address[0] if self.client_address else "")
        stamp = origin_stamp(client_ip, self.headers)
        request.add_header(ORIGIN_HEADER, stamp)
        # Cloudflare Access has already checked this visitor's email with a
        # one-time code and signed a token saying so. Verified here (the token
        # does not survive the next hop) and passed inward as a plain address.
        # Only for relayed requests: a local request has no Access token, and
        # asking for one would break the trader's own browser.
        if stamp == ORIGIN_RELAYED:
            address = verified_email(
                self.headers.get("Cf-Access-Jwt-Assertion", ""),
                ACCESS_KEYS.keys,
                audience=CF_ACCESS_AUD,
                issuer=CF_ACCESS_TEAM_DOMAIN,
            )
            if address:
                request.add_header(ACCESS_EMAIL_HEADER, address)
        # GETs get 90s: Cloudflare's edge gives up at ~100s and shows the
        # trader an HTML 524 (= the "API is unavailable" banner), so a stalled
        # GET must fail HERE first and fall through to the JSON "warming"
        # answer the frontend renders honestly. Mutations keep the long
        # timeout - a slow POST must never be double-submitted because the
        # gateway lied that it failed.
        proxy_timeout = 90 if self.command == "GET" else 310
        try:
            upstream = urllib.request.urlopen(request, timeout=proxy_timeout)
        except urllib.error.HTTPError as error:
            # Upstream answered with an error status - relay it faithfully
            # rather than dressing it up: mutations must see real failures.
            data = error.read()
            self.send_response(error.code)
            for name in self.FORWARD_RESPONSE_HEADERS:
                value = error.headers.get(name) if error.headers else None
                if value:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        except (urllib.error.URLError, TimeoutError, OSError):
            # Pipeline unreachable: the warming shape every consumer already
            # renders honestly (spec: failure model). 200 on purpose - the
            # frontend treats transport 5xx as "server broken", warming as
            # "server busy", and busy is the truthful message here.
            self._send_json(HTTPStatus.OK, {
                "warming": True,
                "refreshing": True,
                "pipelineDown": True,
                "error": "",
            })
            return
        with upstream:
            content_type = str(upstream.headers.get("Content-Type") or "")
            if "text/event-stream" in content_type:
                self.send_response(upstream.status)
                for name in self.FORWARD_RESPONSE_HEADERS:
                    value = upstream.headers.get(name)
                    if value:
                        self.send_header(name, value)
                self.send_header("Connection", "close")
                self.end_headers()
                try:
                    while True:
                        chunk = upstream.read(1024)
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        self.wfile.flush()
                except (ConnectionError, OSError):
                    pass  # browser went away; the pipeline side closes with us
                return
            data = upstream.read()
            self.send_response(upstream.status)
            for name in self.FORWARD_RESPONSE_HEADERS:
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    # -- routes ------------------------------------------------------------ #
    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        if parsed.path in SSE_PATHS:
            query = parse_qs(parsed.query)
            key = sse_feed_key(parsed.path, query)
            SSE_HUB.attach(key, self.pipeline_base + self.path, self)
            return
        if parsed.path == "/api/gateway-health":
            # The gateway's own liveness, separable from the pipeline's, so
            # the watchdog can tell WHICH process needs a restart.
            pipeline_ok = True
            try:
                probe = urllib.request.Request(f"{self.pipeline_base}/api/health")
                with urllib.request.urlopen(probe, timeout=5) as response:
                    pipeline_ok = response.status == 200
            except (urllib.error.URLError, TimeoutError, OSError):
                pipeline_ok = False
            with SERVE_STATS_LOCK:
                stats = dict(SERVE_STATS)
            self._send_json(HTTPStatus.OK, {
                "gateway": "ok",
                "pipeline": "ok" if pipeline_ok else "down",
                "blobs": {
                    "chart": CHART_BLOBS.is_dir(),
                    "chain": CHAIN_BLOBS.is_dir(),
                },
                "serves": stats,
                "sse": dict(SSE_HUB.stats),
            })
            return
        query = parse_qs(parsed.query)
        symbol = normalize_symbol(query.get("symbol", [""])[0])
        now = datetime.now(EASTERN)

        if parsed.path == HOT_CHART_PATH and symbol:
            # A1 scaffold serves the FULL blob only; the slim/delta/deep
            # shaping (initial/since/deep params) must reach fixture parity
            # with the pipeline before this handler goes in front of traffic.
            #
            # The premarket-scanner 9 ALWAYS proxy: the pipeline overlays the
            # scanner's fresh D-M signal replay at serve time
            # (apply_scanner_ganesh_parity, 2026-08-24), and the disk blob
            # never carries that overlay - blob-serving these hid today's
            # CALLD from the chart while the scanner showed it.
            scanner_hot = symbol in (
                "AAPL", "AMZN", "AVGO", "GOOGL", "TSLA", "META", "MSFT", "NVDA", "NFLX",
            )
            shaped = any(key in query for key in ("initial", "since", "deep", "historyStatus", "refresh"))
            if not shaped and not scanner_hot:
                blob = load_blob(CHART_BLOBS, symbol)
                if blob is not None and chart_blob_is_fresh(blob, now):
                    _count("chartBlob")
                    self._send_json(HTTPStatus.OK, blob)
                    return
            _count("chartProxy")
            self._proxy()
            return

        if parsed.path in HOT_CHAIN_PATHS and symbol:
            compact = parsed.path == "/api/oi-finder-chain"
            research_section = str(query.get("section", [""])[0]).strip().lower()
            if "force" not in query and "refresh" not in query:
                blob = load_blob(CHAIN_BLOBS, symbol)
                if chain_blob_is_servable(blob, compact, research_section, now):
                    _count("chainBlob")
                    age = chain_blob_age_seconds(blob, now)
                    # stale/refreshing keep the meaning the frontend already
                    # reads (this copy is provisional, revalidate it).
                    # gatewayBlobAgeSeconds is the honest addition: how old
                    # the body being returned actually is, so a stuck blob is
                    # visible in the payload instead of inferred from a column
                    # of zeros.
                    self._send_json(HTTPStatus.OK, {
                        **blob,
                        "cached": True,
                        "stale": True,
                        "refreshing": True,
                        "gatewayBlobAgeSeconds": round(age, 3) if age is not None else None,
                        "gatewayBlobMaxAgeSeconds": CHAIN_BLOB_MAX_AGE_SECONDS,
                    })
                    return
                if blob and compact and not str(research_section or "").strip():
                    _count("chainBlobStale")
            _count("chainProxy")
            self._proxy()
            return

        _count("otherProxy")
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    # PUT and DELETE are pass-throughs for the same reason POST is: every
    # mutation belongs to the pipeline, the gateway just carries it. Leaving
    # them unwired let BaseHTTPRequestHandler answer `501 Unsupported method`
    # with an HTML body, and the frontend reads HTML + status >= 500 as
    # "API is unavailable right now (server error)" - so editing or deleting a
    # ticker looked like an outage while the pipeline was answering fine.
    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def log_message(self, fmt: str, *args) -> None:  # quiet by default
        return


class GatewayServer(ThreadingHTTPServer):
    """Front door sized for a phone-wake burst, like api_server.ApiServer.

    The pipeline server fixed the 5-slot default listen backlog in 2026-08
    (its docstring cites the exact "API is unavailable right now (server
    error)" banner), but the GATEWAY - the socket cloudflared actually dials -
    kept the default. Reproduced 2026-08-23: with proxy threads held by slow
    chart requests, a burst of new connections overflowed the backlog,
    Windows RST the extras, and cloudflared rendered each reset as a 502
    HTML page = the banner on the trader's phone.
    """

    request_queue_size = 256


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    # :3005 on purpose. The cutover to :3001 is a deliberate act, not a default.
    parser.add_argument("--port", type=int, default=3005)
    parser.add_argument("--pipeline", default="http://127.0.0.1:3002")
    args = parser.parse_args()
    GatewayHandler.pipeline_base = args.pipeline.rstrip("/")
    # Duplicate protection lives HERE, at the application layer, not in the
    # socket options. The first attempt used allow_reuse_address=False so a
    # second binder would crash - correct against silent port sharing (seen
    # 2026-08-20 when a misconfigured pipeline split :3001 with the gateway),
    # but the watchdog drill exposed the cost: after a gateway death its
    # accepted connections sit in TIME_WAIT, and on Windows a no-reuse
    # listener cannot rebind until they drain - the front door stayed down
    # ~3 minutes instead of one watchdog cycle. So: reuse stays on for
    # instant resurrection, and instead the gateway refuses to START when
    # anything is already answering its port.
    try:
        probe = urllib.request.Request(f"http://127.0.0.1:{args.port}/api/gateway-health")
        with urllib.request.urlopen(probe, timeout=2) as response:
            body = response.read(200)
        occupant = "another gateway" if b"gateway" in body else "a non-gateway server"
        print(f"REFUSING TO START: {occupant} is already serving :{args.port}", flush=True)
        raise SystemExit(2)
    except (urllib.error.URLError, TimeoutError, OSError):
        pass  # nothing answering - the port is genuinely free (or draining)

    server = GatewayServer(("127.0.0.1", args.port), GatewayHandler)
    print(f"agx-api scaffold on http://127.0.0.1:{args.port} -> {GatewayHandler.pipeline_base}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
