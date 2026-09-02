"""Tests for mempool_monitor (stdlib unittest, no network)."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from mempool_monitor.api import MempoolClient, MempoolStats, RecommendedFees
from mempool_monitor.store import SnapshotStore


class _FakeResp:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return self._payload


def _fake_urlopen(payloads: dict[str, object]):
    def _open(url, timeout=None):
        for path, payload in payloads.items():
            if url.endswith(path):
                return _FakeResp(json.dumps(payload).encode())
        raise AssertionError(f"Unexpected URL: {url}")
    return _open


class TestMempoolClient(unittest.TestCase):
    def test_get_mempool_stats(self):
        payload = {
            "count": 86502,
            "vsize": 43026056,
            "total_fee": 8459109,
            "fee_histogram": [[3.94, 50121], [1.0, 141877]],
        }
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_fake_urlopen({"/mempool": payload}),
        ):
            stats = MempoolClient().get_mempool_stats()
        self.assertEqual(stats.count, 86502)
        self.assertEqual(stats.vsize, 43026056)
        self.assertEqual(stats.total_fee, 8459109)
        self.assertEqual(stats.fee_histogram, [(3.94, 50121), (1.0, 141877)])
        self.assertAlmostEqual(stats.vsize_mb, 43.026056)

    def test_get_recommended_fees(self):
        payload = {
            "fastestFee": 2, "halfHourFee": 1, "hourFee": 1,
            "economyFee": 1, "minimumFee": 1,
        }
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_fake_urlopen({"/v1/fees/recommended": payload}),
        ):
            fees = MempoolClient().get_recommended_fees()
        self.assertIsInstance(fees, RecommendedFees)
        self.assertEqual(fees.fastest_fee, 2)
        self.assertEqual(fees.minimum_fee, 1)

    def test_get_block_height(self):
        with mock.patch(
            "urllib.request.urlopen",
            side_effect=_fake_urlopen({"/blocks/tip/height": 965104}),
        ):
            self.assertEqual(MempoolClient().get_block_height(), 965104)

    def test_http_error_raises(self):
        err = urllib.error.HTTPError(
            "https://mempool.space/api/blocks/tip/height",
            500, "Internal Server Error", {}, None,
        )
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(Exception) as ctx:
                MempoolClient().get_block_height()
            self.assertIn("HTTP 500", str(ctx.exception))


class TestSnapshotStore(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "test.db")
        self.store = SnapshotStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_insert_and_latest(self):
        self.store.insert_snapshot(
            ts="2026-09-02T00:00:00+00:00", block_height=965104,
            count=86502, vsize=43026056, total_fee=8459109,
            fastest_fee=2, half_hour_fee=1, hour_fee=1,
            economy_fee=1, minimum_fee=1,
        )
        row = self.store.latest()
        self.assertIsNotNone(row)
        self.assertEqual(row["count"], 86502)
        self.assertEqual(row["block_height"], 965104)

    def test_insert_replace_same_ts(self):
        for count in (100, 200):
            self.store.insert_snapshot(
                ts="2026-09-02T00:00:00+00:00", block_height=965104,
                count=count, vsize=1, total_fee=1,
                fastest_fee=1, half_hour_fee=1, hour_fee=1,
                economy_fee=1, minimum_fee=1,
            )
        rows = self.store.recent(limit=10)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["count"], 200)

    def test_recent_ordering(self):
        for i in range(3):
            self.store.insert_snapshot(
                ts=f"2026-09-02T00:0{i}:00+00:00", block_height=965104 + i,
                count=1000 + i, vsize=1, total_fee=1,
                fastest_fee=1, half_hour_fee=1, hour_fee=1,
                economy_fee=1, minimum_fee=1,
            )
        rows = self.store.recent(limit=2)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["block_height"], 965106)
        self.assertEqual(rows[1]["block_height"], 965105)

    def test_latest_empty(self):
        self.assertIsNone(self.store.latest())


class TestChart(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "test.db")
        self.store = SnapshotStore(self.db)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _seed(self, store: SnapshotStore, n: int = 20) -> None:
        for i in range(n):
            store.insert_snapshot(
                ts=f"2026-09-02T00:{i:02d}:00+00:00", block_height=965100 + i,
                count=80_000 + i * 100, vsize=40_000_000 + i * 100_000,
                total_fee=8_000_000 + i, fastest_fee=2 + i % 3,
                half_hour_fee=1 + i % 2, hour_fee=1, economy_fee=1,
                minimum_fee=1,
            )

    def test_render_chart_produces_png(self):
        import matplotlib
        matplotlib.use("Agg")
        from mempool_monitor.chart import render_chart
        from PIL import Image

        self._seed(self.store)
        out = Path(self.tmp.name) / "chart.png"
        result = render_chart(self.store, out)
        self.assertTrue(result.is_file())
        self.assertGreater(result.stat().st_size, 1000)
        img = Image.open(out)
        self.assertEqual(img.format, "PNG")

    def test_render_chart_empty_raises(self):
        import matplotlib
        matplotlib.use("Agg")
        from mempool_monitor.chart import render_chart

        with self.assertRaises(ValueError):
            render_chart(self.store, Path(self.tmp.name) / "empty.png")


if __name__ == "__main__":
    unittest.main()
