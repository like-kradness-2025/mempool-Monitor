"""Tests for fetch_mempool_majority (multi-backend majority vote)."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from mempool_monitor.collector import fetch_mempool_majority

# Healthy backends cluster ~84.2k tx; stale ones ~76k tx.
IPS = ["10.0.0.1", "10.0.0.2", "10.0.0.3", "10.0.0.4", "10.0.0.5",
       "10.0.0.6", "10.0.0.7"]

HEALTHY = {"count": 84_200, "vsize": 42_250_000, "total_fee": 9_000_000,
           "fee_histogram": [[1, 2]]}
STALE = {"count": 75_800, "vsize": 38_000_000, "total_fee": 8_000_000,
         "fee_histogram": [[1, 2]]}


def _make_fake_get(responses: dict[str, dict]) -> object:
    """Return a fake httpx.Client.get that maps IP -> response dict."""
    class FakeResponse:
        def __init__(self, data: dict):
            self._data = data

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return self._data

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def close(self):
            return None

        def get(self, url: str, **kwargs) -> FakeResponse:
            ip = url.split("/")[2]
            return FakeResponse(responses[ip])

    return FakeClient


class MajorityVoteTest(unittest.TestCase):
    def test_majority_adopts_healthy_value(self) -> None:
        # 5 healthy + 2 stale -> healthy median adopted
        per_ip = {ip: HEALTHY for ip in IPS[:5]}
        per_ip.update({IPS[5]: STALE, IPS[6]: STALE})
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority(probe_count=7)
        self.assertIsNotNone(result)
        assert result is not None  # for type narrowing
        self.assertEqual(result["count"], HEALTHY["count"])

    def test_all_healthy_ok(self) -> None:
        per_ip = {ip: HEALTHY for ip in IPS}
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority(probe_count=5)
        self.assertIsNotNone(result)
        assert result is not None  # for type narrowing
        self.assertEqual(result["count"], HEALTHY["count"])

    def test_no_majority_returns_none(self) -> None:
        # Responses split 2 healthy : 2 stale with no 5th -> required 3
        # (probe 5) is not reached -> None
        per_ip = {IPS[0]: HEALTHY, IPS[1]: HEALTHY,
                  IPS[2]: STALE, IPS[3]: STALE}
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority(probe_count=5)
        self.assertIsNone(result)

    def test_single_ip_falls_back(self) -> None:
        # Only one IP resolvable -> majority not applicable
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=["10.0.0.1"]):
            result = fetch_mempool_majority(probe_count=5)
        self.assertIsNone(result)

    def test_observed_five_high_two_low_selects_high_cluster(self) -> None:
        near_stale = {"count": 77_492, "vsize": 38_658_000,
                      "total_fee": 8_000_000, "fee_histogram": [[1, 2]]}
        per_ip = {ip: HEALTHY for ip in IPS[:5]}
        per_ip.update({IPS[5]: near_stale, IPS[6]: near_stale})
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority(probe_count=5)
        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result["count"], HEALTHY["count"])

    def test_two_high_three_low_has_no_quorum(self) -> None:
        per_ip = {IPS[0]: HEALTHY, IPS[1]: HEALTHY,
                  IPS[2]: STALE, IPS[3]: STALE, IPS[4]: STALE}
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority()
        self.assertIsNone(result)

    def test_partial_payload_cannot_form_quorum(self) -> None:
        partial = {"count": HEALTHY["count"], "vsize": HEALTHY["vsize"]}
        per_ip = {IPS[0]: partial, IPS[1]: partial, IPS[2]: partial,
                  IPS[3]: HEALTHY, IPS[4]: HEALTHY}
        fake = _make_fake_get(per_ip)
        with patch("mempool_monitor.collector._resolve_mempool_ips",
                   return_value=IPS), \
             patch("mempool_monitor.collector.httpx.Client", fake):
            result = fetch_mempool_majority()
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
