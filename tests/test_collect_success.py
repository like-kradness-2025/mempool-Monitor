"""Regression tests for collector success semantics (Astra review findings)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from mempool_monitor.config import Config
from mempool_monitor.collector import collect_once_all


class CollectOnceSuccessTest(unittest.TestCase):
    """collect_once_all must return True ONLY when a snapshot was stored."""

    def _storage(self) -> MagicMock:
        storage = MagicMock()
        storage.latest_snapshot.return_value = None
        storage.snapshots_since.return_value = []
        storage.latest_mining.return_value = None
        storage.latest_difficulty.return_value = None
        storage.get_state.return_value = "0"
        return storage

    def test_snapshot_saved_returns_true(self) -> None:
        # Full success path: mempool snapshot inserted -> True
        import sys

        sys.path.insert(0, "src")
        from mempool_monitor.processor import process_collection  # noqa: F401

        snapshot = MagicMock()
        snapshot.mempool_count = 84_000
        snapshot.mempool_vsize = 42_000_000
        snapshot.congestion_level.name = "LOW"
        snapshot.fastest_fee = 3.0
        snapshot.latest_block_height = 800_000
        raw = MagicMock()
        raw.current_difficulty = 1e14
        raw.current_hashrate = 6e20

        storage = self._storage()

        with patch("mempool_monitor.collector.MempoolClient") as client_cls:
            client = MagicMock()
            client.collect.return_value = raw
            client.__enter__.return_value = client
            client_cls.return_value = client
            with patch(
                "mempool_monitor.processor.process_collection",
                return_value=(snapshot, []),
            ):
                with patch("mempool_monitor.sanity.is_suspect", return_value=False):
                    result = collect_once_all(Config(), storage)

        self.assertTrue(result)
        storage.insert_snapshot.assert_called_once()

    def test_main_failure_returns_false_even_if_mining_ok(self) -> None:
        # Astra repro: main collection throws, but mining fallback succeeds.
        # Old code returned True (ok = True in the except branch). New code
        # must return False because NO snapshot was stored.
        storage = self._storage()

        with patch("mempool_monitor.collector.MempoolClient") as client_cls:
            client = MagicMock()
            client.collect.side_effect = RuntimeError("main failed")
            client.__enter__.return_value = client
            client_cls.return_value = client
            with patch(
                "mempool_monitor.collector.fetch_mining_hashrate",
                return_value={"currentDifficulty": 1e14, "currentHashrate": 6e20},
            ):
                with patch(
                    "mempool_monitor.collector.fetch_difficulty", return_value=None
                ):
                    result = collect_once_all(Config(), storage)

        self.assertFalse(result)
        storage.insert_snapshot.assert_not_called()
        storage.insert_mining.assert_called_once()  # side effect still happens

    def test_mining_auxiliary_failure_does_not_hide_snapshot(self) -> None:
        storage = self._storage()
        storage.insert_mining.side_effect = RuntimeError("auxiliary locked")
        snapshot = MagicMock()
        snapshot.mempool_count = 84_000
        snapshot.mempool_vsize = 42_000_000
        snapshot.congestion_level.name = "LOW"
        snapshot.fastest_fee = 3.0
        snapshot.latest_block_height = 800_000
        raw = MagicMock()
        raw.current_difficulty = 1e14
        raw.current_hashrate = 6e20

        with patch("mempool_monitor.collector.MempoolClient") as client_cls, \
             patch("mempool_monitor.collector.fetch_difficulty", return_value=None):
            client = MagicMock()
            client.collect.return_value = raw
            client.__enter__.return_value = client
            client_cls.return_value = client
            with patch(
                "mempool_monitor.processor.process_collection",
                return_value=(snapshot, []),
            ):
                with patch("mempool_monitor.sanity.is_suspect", return_value=False):
                    result = collect_once_all(Config(), storage)

        self.assertTrue(result)
        storage.insert_snapshot.assert_called_once()

    def test_quorum_accepted_snapshot_not_demoted_by_median_sanity(self) -> None:
        # A real fast drain confirmed by a 7-node quorum must not be demoted
        # to suspect merely because it deviates from the recent median.
        storage = self._storage()
        snapshot = MagicMock()
        snapshot.mempool_count = 60_000
        snapshot.mempool_vsize = 30_000_000
        snapshot.congestion_level.name = "LOW"
        snapshot.fastest_fee = 3.0
        snapshot.latest_block_height = 800_000
        snapshot.quality = "accepted"
        snapshot.quality_reason = "unique quorum 7/7"
        raw = MagicMock()
        raw.current_difficulty = 1e14
        raw.current_hashrate = 6e20
        raw.quality = "accepted"
        raw.quality_reason = "unique quorum 7/7"

        with patch("mempool_monitor.collector.MempoolClient") as client_cls, \
             patch("mempool_monitor.collector.fetch_difficulty", return_value=None):
            client = MagicMock()
            client.collect.return_value = raw
            client.__enter__.return_value = client
            client_cls.return_value = client
            with patch(
                "mempool_monitor.processor.process_collection",
                return_value=(snapshot, []),
            ):
                with patch("mempool_monitor.sanity.is_suspect", return_value=True):
                    result = collect_once_all(Config(), storage)

        self.assertTrue(result)
        storage.insert_snapshot.assert_called_once()
        stored = storage.insert_snapshot.call_args[0][0]
        self.assertEqual(stored.quality, "accepted")
        client.collect.assert_called_once()  # no blind re-fetch for quorum data

    def test_difficulty_success_does_not_set_ok(self) -> None:
        # If the main collection fails but difficulty succeeds, ok stays False.
        storage = self._storage()

        with patch("mempool_monitor.collector.MempoolClient") as client_cls:
            client = MagicMock()
            client.collect.side_effect = RuntimeError("main failed")
            client.__enter__.return_value = client
            client_cls.return_value = client
            diff_data = {
                "progressPercent": 50, "difficultyChange": 1.5,
                "estimatedRetargetDate": 1_700_000_000, "remainingBlocks": 100,
                "remainingTime": 5000, "previousRetarget": 1.2,
                "previousTime": 6000, "nextRetargetHeight": 800_100,
                "timeAvg": 600, "adjustedTimeAvg": 600, "timeOffset": 0,
                "expectedBlocks": 100.0,
            }
            with patch(
                "mempool_monitor.collector.fetch_mining_hashrate", return_value=None
            ):
                with patch(
                    "mempool_monitor.collector.fetch_difficulty", return_value=diff_data
                ):
                    result = collect_once_all(Config(), storage)

        self.assertFalse(result)
        storage.insert_snapshot.assert_not_called()


if __name__ == "__main__":
    unittest.main()
