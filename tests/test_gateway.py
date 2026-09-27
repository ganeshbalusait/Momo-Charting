from __future__ import annotations

import gzip
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


class GatewayImportGuardTests(unittest.TestCase):
    """The gateway's whole safety story is what it does NOT import.

    api_server creates STATE = DashboardState() at module level, which boots
    every scheduler and touches the live database (verified 2026-08-19: pytest
    collection died with `database is locked` because of it). And exactly one
    process may touch Schwab tokens - the pipeline. If either lands in
    sys.modules when the gateway loads, the split is broken by construction.
    """

    def test_importing_the_gateway_boots_nothing(self) -> None:
        # In a SUBPROCESS, deliberately: inside the shared pytest process other
        # suites may already have imported api_server, which would fail this
        # assertion for reasons that have nothing to do with the gateway
        # (observed the first time this ran beside test_oi_finder_chart_cache).
        script = (
            "import sys; import gateway; "
            "assert 'api_server' not in sys.modules, 'gateway imported api_server'; "
            "bad = [m for m in sys.modules if 'schwab' in m.lower()]; "
            "assert not bad, f'gateway imported {bad}'"
        )
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=str(Path(__file__).resolve().parent.parent),
            capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr[-400:])


class SessionStartTests(unittest.TestCase):
    def setUp(self) -> None:
        import gateway
        self.gateway = gateway

    def test_a_weekday_afternoon_belongs_to_that_morning(self) -> None:
        now = datetime(2026, 8, 19, 15, 30, tzinfo=ET)  # Wed
        start = self.gateway.most_recent_session_start(now)
        self.assertEqual((start.month, start.day, start.hour), (8, 19, 4))

    def test_before_premarket_belongs_to_the_previous_day(self) -> None:
        now = datetime(2026, 8, 19, 2, 0, tzinfo=ET)
        start = self.gateway.most_recent_session_start(now)
        self.assertEqual((start.month, start.day), (8, 18))

    def test_weekend_reaches_back_to_friday(self) -> None:
        now = datetime(2026, 8, 22, 12, 0, tzinfo=ET)  # Sat
        start = self.gateway.most_recent_session_start(now)
        self.assertEqual((start.month, start.day), (8, 21))
        self.assertEqual(start.weekday(), 4)


class ChartBlobFreshnessTests(unittest.TestCase):
    """The SPY defect (137758a) as a rule: inside the window is not enough -
    the tape must reach the current session."""

    def setUp(self) -> None:
        import gateway
        self.gateway = gateway
        self.now = datetime(2026, 8, 19, 15, 30, tzinfo=ET)

    def blob(self, newest: datetime) -> dict:
        return {"bars": [{"time": int(newest.timestamp())}]}

    def test_a_tape_from_this_session_is_fresh(self) -> None:
        self.assertTrue(self.gateway.chart_blob_is_fresh(
            self.blob(self.now - timedelta(minutes=3)), self.now))

    def test_yesterdays_evening_tape_is_stale_even_inside_the_window(self) -> None:
        # 19:59 the previous evening: 19.5h old - inside 26h, before session.
        newest = self.now.replace(hour=19, minute=59) - timedelta(days=1)
        self.assertFalse(self.gateway.chart_blob_is_fresh(self.blob(newest), self.now))

    def test_a_tape_older_than_the_window_is_stale(self) -> None:
        self.assertFalse(self.gateway.chart_blob_is_fresh(
            self.blob(self.now - timedelta(hours=27)), self.now))

    def test_empty_or_damaged_blobs_are_never_fresh(self) -> None:
        for bad in (None, {}, {"bars": []}, {"bars": [{"time": "x"}]}, {"bars": [{}]}):
            self.assertFalse(self.gateway.chart_blob_is_fresh(bad, self.now))

    def test_a_mid_rebuild_blob_with_current_bars_is_fresh(self) -> None:
        payload = self.blob(self.now - timedelta(minutes=3))
        payload["historyLoading"] = True
        self.assertTrue(self.gateway.chart_blob_is_fresh(payload, self.now))


class ChainBlobServabilityTests(unittest.TestCase):
    def setUp(self) -> None:
        import gateway
        self.gateway = gateway
        self.now = datetime(2026, 8, 31, 10, 24, tzinfo=ET)

    def stamped(self, payload: dict, age_seconds: float = 5.0) -> dict:
        """The same blob, stamped as written `age_seconds` ago.

        Every servability case below now needs a stamp, because an unstamped
        blob is by rule unservable.
        """
        fresh = dict(payload)
        fresh["scannedAt"] = (
            self.now - timedelta(seconds=age_seconds)
        ).isoformat()
        return fresh

    def test_full_requests_are_proxied_not_served_from_the_blob(self) -> None:
        # Superseded 2026-08-20: expiry rows are no longer enough. See
        # test_the_full_finder_endpoint_is_never_served_from_a_blob.
        self.assertFalse(self.gateway.chain_blob_is_servable(
            self.stamped({"callRows": []}), False, "", self.now))
        self.assertFalse(self.gateway.chain_blob_is_servable(
            self.stamped({"selectedExpiryChainRows": [{"strike": 100}]}),
            False, "", self.now))

    def test_the_full_finder_endpoint_is_never_served_from_a_blob(self) -> None:
        """The blob is the COMPACT chain payload; it carries no analytics.

        The pipeline may answer a full request from its disk copy because a
        background rebuild then fills the real analytics in behind it. The
        gateway has no such rebuild - the blob is all it has - so serving
        /api/oi-finder from it is terminal: volumeMomentum, heatmap and the
        unusual-activity panels stay empty forever. Measured 2026-08-20 10:45
        ET on AMZN: volumeMomentum had 7 keys direct from :3002 and 0 through
        the gateway. Only /api/oi-finder-chain may use the blob.
        """
        full = self.stamped(
            {"callRows": [], "selectedExpiryChainRows": [{"strike": 210}]})
        self.assertFalse(
            self.gateway.chain_blob_is_servable(full, False, "", self.now))
        self.assertTrue(
            self.gateway.chain_blob_is_servable(full, True, "", self.now))

    def test_a_research_request_is_never_served_from_a_blob(self) -> None:
        """section=heatmap/flow need analytics the chain blob does not carry.

        The blob is the compact chain payload, so its dailyLiquidityHeatmap is
        always {}. Serving it answered /api/oi-finder?section=heatmap with an
        empty heatmap stamped researchSection "" and the request never reached
        the pipeline. Verified 2026-08-20 10:38 ET, AMZN section=heatmap:
        :3002 returned 994 rows, :3001 through the gateway returned 0.

        This is the same rule the pipeline learned in 3a549b4; the two
        implementations must stay in lockstep.
        """
        full = self.stamped(
            {"callRows": [], "selectedExpiryChainRows": [{"strike": 210}]})
        for section in ("heatmap", "flow"):
            self.assertFalse(
                self.gateway.chain_blob_is_servable(
                    full, False, section, self.now)
            )
            self.assertFalse(
                self.gateway.chain_blob_is_servable(
                    full, True, section, self.now)
            )
        # A section-less COMPACT request still uses the blob.
        self.assertTrue(
            self.gateway.chain_blob_is_servable(full, True, "", self.now))

    def test_compact_requests_accept_any_RECENT_blob(self) -> None:
        self.assertTrue(self.gateway.chain_blob_is_servable(
            self.stamped({"callRows": []}), True, "", self.now))

    def test_nothing_is_never_servable(self) -> None:
        for bad in (None, {}, "x"):
            self.assertFalse(
                self.gateway.chain_blob_is_servable(bad, True, "", self.now))
            self.assertFalse(
                self.gateway.chain_blob_is_servable(bad, False, "", self.now))

    # -- the age rule (2026-08-31 volume-column outage) ---------------- #

    def test_a_premarket_blob_is_not_servable_after_the_bell(self) -> None:
        """THE REGRESSION. Reproduced live 10:24 ET on NFLX through :3001.

        The 09:15 ET morning auto-OI refresh persists a chain built
        BEFORE the open, and Schwab reports totalVolume == 0 for every
        contract premarket. With no age rule the gateway served that
        snapshot for the rest of the day, so the High OI board's Vol
        column was blank on every row while open_interest, delta and
        mark all looked perfectly healthy - which is exactly why this
        read as a field-drop bug and was not.

        The blob is non-empty, compact, section-less and internally
        consistent. Only its AGE disqualifies it.
        """
        premarket = {
            "symbol": "NFLX",
            "scannedAt": datetime(2026, 8, 31, 9, 15, 25, tzinfo=ET).isoformat(),
            "selectedExpiryChainRows": [
                {"strike": 1200.0, "volume": 0, "open_interest": 392},
            ],
        }
        self.assertFalse(self.gateway.chain_blob_is_servable(
            premarket, True, "", self.now))

    def test_a_blob_inside_the_ttl_still_absorbs_a_burst(self) -> None:
        """The blob exists to absorb phone-wake bursts; keep that."""
        for age in (0.0, 5.0, self.gateway.CHAIN_BLOB_MAX_AGE_SECONDS):
            with self.subTest(age=age):
                self.assertTrue(self.gateway.chain_blob_is_servable(
                    self.stamped({"callRows": []}, age), True, "", self.now))

    def test_one_second_past_the_ttl_is_proxied(self) -> None:
        stale = self.stamped(
            {"callRows": []}, self.gateway.CHAIN_BLOB_MAX_AGE_SECONDS + 1)
        self.assertFalse(
            self.gateway.chain_blob_is_servable(stale, True, "", self.now))

    def test_an_unstamped_blob_cannot_be_proven_fresh(self) -> None:
        """No scannedAt means no evidence, and no evidence means proxy.

        Defaulting an unstamped blob to servable would restore the exact
        bug for any writer that forgets the stamp.
        """
        for raw in ({}, {"scannedAt": ""}, {"scannedAt": "not-a-date"},
                    {"scannedAt": None}):
            payload = {"callRows": [], **raw}
            with self.subTest(raw=raw):
                self.assertFalse(self.gateway.chain_blob_is_servable(
                    payload, True, "", self.now))

    def test_the_default_now_is_the_strict_rule_not_the_old_one(self) -> None:
        """A caller that omits `now` must NOT get serve-anything back."""
        premarket = {
            "callRows": [],
            "scannedAt": datetime(2020, 1, 2, 9, 15, tzinfo=ET).isoformat(),
        }
        self.assertFalse(self.gateway.chain_blob_is_servable(premarket, True))

    def test_epoch_and_utc_stamps_are_understood(self) -> None:
        recent = self.now - timedelta(seconds=10)
        for stamp in (recent.timestamp(),
                      recent.timestamp() * 1000.0,
                      recent.astimezone(ZoneInfo("UTC"))
                      .isoformat().replace("+00:00", "Z"),
                      recent.replace(tzinfo=None).isoformat()):
            with self.subTest(stamp=stamp):
                self.assertTrue(self.gateway.chain_blob_is_servable(
                    {"callRows": [], "scannedAt": stamp}, True, "", self.now))

    def test_a_blob_stamped_far_in_the_future_is_proxied(self) -> None:
        future = self.stamped({"callRows": []}, -3600)
        self.assertFalse(
            self.gateway.chain_blob_is_servable(future, True, "", self.now))

    def test_the_stale_fallthrough_is_counted_not_silent(self) -> None:
        """Staleness has to be observable from outside the process."""
        self.assertIn("chainBlobStale", self.gateway.SERVE_STATS)


class BlobLoadTests(unittest.TestCase):
    def setUp(self) -> None:
        import gateway
        self.gateway = gateway
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def write(self, name: str, data) -> None:
        (self.root / name).write_bytes(gzip.compress(json.dumps(data).encode()))

    def test_round_trip(self) -> None:
        self.write("AAPL.json.gz", {"bars": [1, 2]})
        self.assertEqual(self.gateway.load_blob(self.root, "aapl"), {"bars": [1, 2]})

    def test_missing_damaged_or_invalid_symbols_return_none(self) -> None:
        (self.root / "BAD.json.gz").write_bytes(b"not gzip")
        self.assertIsNone(self.gateway.load_blob(self.root, "BAD"))
        self.assertIsNone(self.gateway.load_blob(self.root, "NOPE"))
        self.assertIsNone(self.gateway.load_blob(self.root, "../../etc/passwd"))
        self.assertIsNone(self.gateway.load_blob(self.root, ""))


class SseFeedKeyTests(unittest.TestCase):
    """One upstream per (endpoint, symbol set) - the key IS that contract."""

    def setUp(self) -> None:
        import gateway
        self.gateway = gateway

    def test_order_case_dupes_and_whitespace_collapse_to_one_key(self) -> None:
        variants = (
            {"symbols": ["AAPL,SPY"]},
            {"symbols": ["spy", "aapl"]},
            {"symbols": [" SPY , AAPL ", "aapl"]},
        )
        keys = {self.gateway.sse_feed_key("/api/live-market-stream", q) for q in variants}
        self.assertEqual(keys, {"/api/live-market-stream?symbols=AAPL,SPY"})

    def test_different_endpoints_never_share_a_feed(self) -> None:
        query = {"symbols": ["AAPL"]}
        self.assertNotEqual(
            self.gateway.sse_feed_key("/api/live-market-stream", query),
            self.gateway.sse_feed_key("/api/live-option-stream", query),
        )

    def test_no_symbols_is_still_a_stable_key(self) -> None:
        self.assertEqual(
            self.gateway.sse_feed_key("/api/live-market-stream", {}),
            "/api/live-market-stream?symbols=",
        )


class SseHubBroadcastTests(unittest.TestCase):
    def test_a_full_client_queue_drops_oldest_not_newest(self) -> None:
        """A stalled browser must not stall the feed, and when it wakes it
        should see the most recent ticks - live bars, not history."""
        import queue

        import gateway
        hub = gateway.SseHub()
        client = queue.Queue(maxsize=2)
        with hub._lock:
            hub._feeds["k"] = {"clients": [client], "url": "http://x"}
        hub._broadcast("k", b"one")
        hub._broadcast("k", b"two")
        hub._broadcast("k", b"three")  # full: "one" is sacrificed
        self.assertEqual(client.get_nowait(), b"two")
        self.assertEqual(client.get_nowait(), b"three")

    def test_broadcast_to_a_dead_feed_is_a_no_op(self) -> None:
        import gateway
        gateway.SseHub()._broadcast("ghost", b"x")  # must not raise


if __name__ == "__main__":
    unittest.main()


class ProxiedVerbTests(unittest.TestCase):
    """Every verb the API answers must reach the pipeline.

    The gateway only wired do_GET/do_POST, so PUT and DELETE fell through to
    BaseHTTPRequestHandler's default: `501 Unsupported method` with an HTML
    body. The frontend reads HTML + status >= 500 as "API is unavailable right
    now (server error)", which is what deleting or editing a Mag7 scanner
    ticker showed (observed 2026-08-23) even though the pipeline itself
    answered those same requests with 200 on :3002.
    """

    def setUp(self) -> None:
        import gateway
        self.handler = gateway.GatewayHandler

    def test_mutating_verbs_are_forwarded(self) -> None:
        # GET is excluded on purpose: it routes blob/health paths itself
        # and only falls through to _proxy. The mutating verbs are pure
        # pass-throughs, so "reaches _proxy" is exactly their contract.
        for verb in ("POST", "PUT", "DELETE"):
            with self.subTest(verb=verb):
                method = getattr(self.handler, f"do_{verb}", None)
                self.assertIsNotNone(method, f"gateway has no do_{verb}")
                instance = object.__new__(self.handler)
                forwarded = []
                instance._proxy = lambda: forwarded.append(True)
                method(instance)
                self.assertEqual(forwarded, [True], f"do_{verb} did not proxy")
