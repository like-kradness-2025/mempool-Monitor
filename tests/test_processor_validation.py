"""Boundary tests for complete API payload validation."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from mempool_monitor.collector import RawCollection, validate_mempool_payload  # noqa: E402
from mempool_monitor.config import Config  # noqa: E402
from mempool_monitor.processor import DataValidationError, process_collection  # noqa: E402


MEMPOOL = {
    "count": 100,
    "vsize": 1_000,
    "total_fee": 10_000,
    "fee_histogram": [[1, 500], [5, 500]],
}
FEES = {
    "fastestFee": 10,
    "halfHourFee": 5,
    "hourFee": 3,
    "economyFee": 2,
    "minimumFee": 1,
}
BLOCKS = [
    {"height": 965_200, "timestamp": 1_700_000_600, "tx_count": 100,
     "size": 1_000_000, "weight": 3_900_000},
    {"height": 965_199, "timestamp": 1_700_000_000, "tx_count": 90,
     "size": 900_000, "weight": 3_500_000},
]
PROJECTED = [{
    "nTx": 10,
    "blockVSize": 100_000,
    "totalFees": 10_000,
    "medianFee": 5,
    "feeRange": [1, 5],
}]


def _raw(**overrides: object) -> RawCollection:
    values: dict[str, object] = {
        "collected_at": 1_700_001_000,
        "mempool": dict(MEMPOOL),
        "fees": dict(FEES),
        "projected_blocks": list(PROJECTED),
        "blocks": list(BLOCKS),
        "latency_ms": 10.0,
    }
    values.update(overrides)
    return RawCollection(**values)  # type: ignore[arg-type]


class MempoolPayloadValidationTest(unittest.TestCase):
    def test_partial_payload_is_rejected_before_vote(self) -> None:
        self.assertIsNotNone(validate_mempool_payload({"count": 1, "vsize": 2}))

    def test_complete_payload_is_accepted(self) -> None:
        self.assertIsNone(validate_mempool_payload(MEMPOOL))


class ProcessorValidationTest(unittest.TestCase):
    def test_nan_fee_is_rejected(self) -> None:
        fees: dict[str, Any] = dict(FEES)
        fees["fastestFee"] = math.nan
        with self.assertRaises(DataValidationError):
            process_collection(_raw(fees=fees), Config())

    def test_fractional_integer_field_is_rejected(self) -> None:
        mempool: dict[str, Any] = dict(MEMPOOL)
        mempool["count"] = 1.5
        with self.assertRaises(DataValidationError):
            process_collection(_raw(mempool=mempool), Config())

    def test_boolean_histogram_value_is_rejected(self) -> None:
        mempool: dict[str, Any] = dict(MEMPOOL)
        mempool["fee_histogram"] = [[True, 500]]
        with self.assertRaises(DataValidationError):
            process_collection(_raw(mempool=mempool), Config())

    def test_nan_projected_fee_range_is_rejected(self) -> None:
        projected = [dict(PROJECTED[0], feeRange=[math.nan])]
        with self.assertRaises(DataValidationError):
            process_collection(_raw(projected_blocks=projected), Config())


if __name__ == "__main__":
    unittest.main()
