"""Regression tests for Storage._prune_oldest.

Covers the per-table time-column map (delivery_log uses attempted_at), the
atomic all-tables transaction, and the fact that projected_blocks is NOT pruned
by fraction (snapshots CASCADE removes it).
"""
from __future__ import annotations

import sqlite3
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mempool_monitor.models import (  # noqa: E402
    CongestionLevel,
    DifficultySnapshot,
    MiningSnapshot,
    ProjectedBlock,
    Snapshot,
)
from mempool_monitor.storage import Storage  # noqa: E402


def _snapshot(ts: int) -> Snapshot:
    return Snapshot(
        collected_at=ts,
        fastest_fee=1, half_hour_fee=1, hour_fee=1, economy_fee=1, minimum_fee=1,
        mempool_count=100, mempool_vsize=1000, mempool_total_fee=1,
        backlog_1=1, backlog_2=1, backlog_5=1, backlog_10=1, backlog_20=1, backlog_50=1,
        latest_block_height=965200, latest_block_timestamp=1, block_age_seconds=10,
        avg_block_interval_seconds=600, latest_block_tx_count=1000,
        latest_block_size=1_500_000, latest_block_weight=3_900_000,
        congestion_level=CongestionLevel.LOW, provider="test", api_latency_ms=10,
        btc_price_usd=77000, current_difficulty=1e14, current_hashrate=6e20,
    )


def _diff(ts: int) -> DifficultySnapshot:
    return DifficultySnapshot(
        collected_at=ts, progress_percent=50.0, difficulty_change=1.0,
        estimated_retarget_date=ts, remaining_blocks=100, remaining_time=100,
        previous_retarget=1.0, previous_time=ts, next_retarget_height=1,
        time_avg=1, adjusted_time_avg=1, time_offset=1, expected_blocks=1.0,
    )


def _mining(ts: int) -> MiningSnapshot:
    return MiningSnapshot(collected_at=ts, current_difficulty=1e14, current_hashrate=6e20)


def _projected() -> list[ProjectedBlock]:
    return [
        ProjectedBlock(position=0, n_tx=1, block_vsize=100.0,
                       total_fees=1, median_fee=1.0, fee_range=[1.0]),
        ProjectedBlock(position=1, n_tx=2, block_vsize=200.0,
                       total_fees=2, median_fee=2.0, fee_range=[1.0, 2.0]),
    ]


class PruneOldestTest(unittest.TestCase):
    def setUp(self):
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        self.storage = object.__new__(Storage)
        self.storage.path = Path("/dev/null")
        self.storage.connection = conn
        self.storage.initialize()

    def tearDown(self):
        self.storage.connection.close()

    def _count(self, table: str) -> int:
        return int(
            self.storage.connection.execute(
                f"SELECT COUNT(*) AS c FROM {table}"
            ).fetchone()["c"]
        )

    def test_prune_all_tables_no_crash_no_projected_overprune(self):
        n = 30
        with self.storage.connection:
            for i in range(n):
                ts = 100 + i
                self.storage.insert_snapshot(_snapshot(ts), _projected())
                self.storage.insert_difficulty(_diff(ts))
                self.storage.insert_mining(_mining(ts))

        # Seed delivery_log with distinct attempted_at via patched time.time.
        fake_time = iter(range(100_000, 100_000 + n))
        with mock.patch(
            "mempool_monitor.storage.time.time",
            side_effect=lambda: next(fake_time),
        ):
            for _ in range(n):
                self.storage.record_delivery("report", "success")

        self.assertEqual(self._count("snapshots"), n)
        self.assertEqual(self._count("projected_blocks"), n * 2)
        self.assertEqual(self._count("delivery_log"), n)
        self.assertEqual(self._count("difficulty"), n)
        self.assertEqual(self._count("mining"), n)

        # Should not raise (delivery_log has no collected_at column).
        deleted = self.storage._prune_oldest(0.3)
        self.assertGreater(deleted, 0)

        snap_after = self._count("snapshots")
        self.assertLess(snap_after, n)          # snapshots pruned
        self.assertLess(self._count("delivery_log"), n)
        self.assertLess(self._count("difficulty"), n)
        self.assertLess(self._count("mining"), n)

        # projected_blocks was NOT independently pruned: its surviving rows must
        # exactly match the remaining snapshots (via CASCADE).
        remaining_snaps = {
            r["collected_at"]
            for r in self.storage.connection.execute(
                "SELECT collected_at FROM snapshots"
            )
        }
        proj_rows = self.storage.connection.execute(
            "SELECT collected_at FROM projected_blocks"
        ).fetchall()
        self.assertGreater(len(proj_rows), 0)
        per_snap: dict[int, int] = {}
        for r in proj_rows:
            self.assertIn(r["collected_at"], remaining_snaps)
            per_snap[r["collected_at"]] = per_snap.get(r["collected_at"], 0) + 1
        for ts in remaining_snaps:
            self.assertEqual(per_snap.get(ts, 0), 2)


if __name__ == "__main__":
    unittest.main()
