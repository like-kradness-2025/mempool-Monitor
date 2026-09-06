"""Tests for mempool_monitor.sanity (stale-CDN snapshot filter)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mempool_monitor.models import CongestionLevel, Snapshot  # noqa: E402
from mempool_monitor.sanity import is_plausible  # noqa: E402


def _mk(ts: int, count: int, vsize: int, height: int = 965200) -> Snapshot:
    return Snapshot(
        collected_at=ts,
        fastest_fee=1,
        half_hour_fee=1,
        hour_fee=1,
        economy_fee=1,
        minimum_fee=1,
        mempool_count=count,
        mempool_vsize=vsize,
        mempool_total_fee=1,
        backlog_1=1,
        backlog_2=1,
        backlog_5=1,
        backlog_10=1,
        backlog_20=1,
        backlog_50=1,
        latest_block_height=height,
        latest_block_timestamp=1,
        block_age_seconds=10,
        avg_block_interval_seconds=600,
        latest_block_tx_count=1000,
        latest_block_size=1_500_000,
        latest_block_weight=3_900_000,
        congestion_level=CongestionLevel.LOW,
        provider="test",
        api_latency_ms=10,
        btc_price_usd=77000,
        current_difficulty=1e14,
        current_hashrate=6e20,
    )


RECENT = [_mk(100 + i, 87_000, 43_000_000) for i in range(7)]
PREV = _mk(200, 87_000, 43_000_000)


class SanityFilterTest(unittest.TestCase):
    def test_small_change_accepted(self):
        snap = _mk(300, 86_500, 42_800_000)
        self.assertTrue(is_plausible(snap, PREV, RECENT))

    def test_stale_cdn_drop_rejected(self):
        # -28% count / -16% vsize with no height change = stale CDN read
        snap = _mk(300, 62_000, 36_000_000)
        self.assertFalse(is_plausible(snap, PREV, RECENT))

    def test_chain_tip_advance_allows_real_drain(self):
        # Block confirmed -> a genuine ~11% drain passes (wider band)
        snap = _mk(300, 76_000, 38_000_000, height=965_201)
        self.assertTrue(is_plausible(snap, PREV, RECENT))

    def test_chain_tip_advance_oversized_drop_rejected(self):
        # One block cannot drain >15%; an 18% drop is still a CDN read
        snap = _mk(300, 60_000, 35_000_000, height=965_201)
        self.assertFalse(is_plausible(snap, PREV, RECENT))

    def test_first_snapshot_always_accepted(self):
        snap = _mk(1, 88_000, 43_000_000)
        self.assertTrue(is_plausible(snap, None, []))

    def test_no_recent_uses_previous(self):
        snap = _mk(300, 65_000, 37_000_000)
        self.assertFalse(is_plausible(snap, PREV, []))

    def test_height_change_within_recent_accepted(self):
        # current height matches recent max (block confirmed between ticks)
        mixed = [_mk(100 + i, 87_000, 43_000_000, height=965_200 + (i % 2)) for i in range(7)]
        # recent max = 965201; current at 965202 with a real drain after a block
        snap = _mk(300, 76_000, 38_000_000, height=965_202)
        self.assertTrue(is_plausible(snap, mixed[-1], mixed))


if __name__ == "__main__":
    unittest.main()


class ChartSpikeFilterTest(unittest.TestCase):
    """Display-time spike filter in chart.py (does not touch the DB)."""

    def _mk(self, ts: int, vsize: int, count: int = 85000) -> Snapshot:
        return _mk(ts, count, vsize)  # reuse helper

    def test_isolated_spike_masked_not_removed(self):
        from mempool_monitor.chart import _remove_isolated_spikes
        import math
        # 13 samples with the spike mid-series (window 11 engages, and the
        # spike is not the newest sample so it is eligible for masking).
        base = [self._mk(100 + i * 60, 42_000_000) for i in range(12)]
        snaps = base[:6] + [self._mk(430, 37_000_000)] + base[6:]
        out = _remove_isolated_spikes(snaps)
        # New contract: objects are never dropped, only the vsize is NaN-masked.
        self.assertEqual(len(out), len(snaps))
        spike = next(s for s in out if s.collected_at == 430)
        self.assertTrue(math.isnan(spike.mempool_vsize))

    def test_consecutive_drop_kept(self):
        from mempool_monitor.chart import _remove_isolated_spikes
        snaps = [self._mk(100, 42_000_000), self._mk(200, 37_000_000),
                 self._mk(300, 37_000_000), self._mk(400, 42_000_000)]
        out = _remove_isolated_spikes(snaps)
        self.assertEqual(len(out), 4)  # real drain: keep all

    def test_smooth_change_kept(self):
        from mempool_monitor.chart import _remove_isolated_spikes
        snaps = [self._mk(100, 43_000_000), self._mk(200, 42_500_000), self._mk(300, 42_000_000)]
        out = _remove_isolated_spikes(snaps)
        self.assertEqual(len(out), 3)

    def test_short_series_untouched(self):
        from mempool_monitor.chart import _remove_isolated_spikes
        snaps = [self._mk(100, 42_000_000), self._mk(200, 37_000_000)]
        out = _remove_isolated_spikes(snaps)
        self.assertEqual(len(out), 2)

    def test_newest_never_masked(self):
        from mempool_monitor.chart import _remove_isolated_spikes
        import math
        # A genuine latest dip (newest sample is the low one) must survive as-is.
        base = [self._mk(100 + i * 60, 42_000_000) for i in range(12)]
        snaps = base + [self._mk(820, 37_000_000)]
        out = _remove_isolated_spikes(snaps)
        newest = out[-1]
        self.assertEqual(newest.collected_at, 820)
        self.assertFalse(math.isnan(newest.mempool_vsize))

    def test_other_fields_survive_mask(self):
        from dataclasses import replace
        from mempool_monitor.chart import _remove_isolated_spikes
        import math
        # vsize spike must not erase the snapshot's other panel fields.
        base = [self._mk(100 + i * 60, 42_000_000) for i in range(12)]
        snaps = [replace(s, btc_price_usd=67_000.0) for s in base]
        spike_snap = replace(self._mk(430, 37_000_000), btc_price_usd=67_000.0)
        snaps = snaps[:6] + [spike_snap] + snaps[6:]
        out = _remove_isolated_spikes(snaps)
        spike = next(s for s in out if s.collected_at == 430)
        self.assertTrue(math.isnan(spike.mempool_vsize))
        self.assertEqual(spike.mempool_count, 85000)  # other fields intact
