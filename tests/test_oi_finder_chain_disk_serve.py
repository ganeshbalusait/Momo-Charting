from __future__ import annotations

import unittest

from api_server import DashboardState


class ChainDiskServeTests(unittest.TestCase):
    """The options panel must paint from cache, not wait on Schwab.

    Measured 2026-08-18: /api/oi-finder?symbol=CRWD took 19.97s cold and 2.40s
    warm, which is the "Loading live option chain" spinner. The disk cache that
    exists to prevent exactly this was gated on `compact and not force`, so the
    FULL request the panel makes skipped it and blocked on the broker.
    """

    def test_a_compact_request_can_always_use_the_cache(self) -> None:
        self.assertTrue(
            DashboardState._oi_finder_chain_disk_is_servable({"callRows": []}, True)
        )

    def test_a_full_request_needs_the_expiry_rows(self) -> None:
        # A payload slimmed for first paint must not answer a full request.
        slim = {"callRows": [], "putRows": []}
        self.assertFalse(DashboardState._oi_finder_chain_disk_is_servable(slim, False))
        full = {"callRows": [], "putRows": [], "selectedExpiryChainRows": [{"strike": 210}]}
        self.assertTrue(DashboardState._oi_finder_chain_disk_is_servable(full, False))

    def test_a_research_request_is_never_servable_from_disk(self) -> None:
        """Heatmap/Flow need analytics the disk chain payload does not carry.

        The disk copy is built by the compact/chain path, so its
        dailyLiquidityHeatmap is always {}. Answering section=heatmap from it
        returned an empty heatmap stamped stale:true forever: the shortcut also
        wrote the research cache under the wrong key and dropped
        research_section from its background refresh, so the cache that would
        have healed it was never filled. Verified 2026-08-19 on the live
        backend - NFLX section=heatmap returned 0 rows across 6 polls / 36s,
        while the same request with force=true returned 248 calls + 248 puts
        in 1.4s.
        """
        full = {"callRows": [], "putRows": [], "selectedExpiryChainRows": [{"strike": 210}]}
        for section in ("heatmap", "flow"):
            self.assertFalse(
                DashboardState._oi_finder_chain_disk_is_servable(full, False, section)
            )
        # A section-less full request still uses the disk copy.
        self.assertTrue(
            DashboardState._oi_finder_chain_disk_is_servable(full, False, "")
        )

    def test_nothing_cached_is_not_servable(self) -> None:
        for empty in (None, {}, "not-a-dict"):
            self.assertFalse(DashboardState._oi_finder_chain_disk_is_servable(empty, True))
            self.assertFalse(DashboardState._oi_finder_chain_disk_is_servable(empty, False))


if __name__ == "__main__":
    unittest.main()
