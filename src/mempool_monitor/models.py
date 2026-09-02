"""Data models for mempool-monitor."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Any


class CongestionLevel(IntEnum):
    UNKNOWN = -1
    LOW = 0
    MODERATE = 1
    HIGH = 2
    EXTREME = 3

    @classmethod
    def from_name(cls, value: str) -> "CongestionLevel":
        return cls[value.upper()]


@dataclass(frozen=True)
class ProjectedBlock:
    position: int
    n_tx: int
    block_vsize: float
    total_fees: int
    median_fee: float
    fee_range: list[float]


@dataclass(frozen=True)
class Snapshot:
    collected_at: int
    fastest_fee: float
    half_hour_fee: float
    hour_fee: float
    economy_fee: float
    minimum_fee: float
    mempool_count: int
    mempool_vsize: int
    mempool_total_fee: int
    backlog_1: float
    backlog_2: float
    backlog_5: float
    backlog_10: float
    backlog_20: float
    backlog_50: float
    latest_block_height: int
    latest_block_timestamp: int
    block_age_seconds: int
    avg_block_interval_seconds: float
    latest_block_tx_count: int
    latest_block_size: int
    latest_block_weight: int
    congestion_level: CongestionLevel
    provider: str
    api_latency_ms: float
    btc_price_usd: float | None = None
    current_difficulty: float | None = None
    current_hashrate: float | None = None

    def backlog(self, threshold: int) -> float:
        return float(getattr(self, f"backlog_{threshold}"))

    def as_dict(self) -> dict[str, Any]:
        result = self.__dict__.copy()
        result["congestion_level"] = self.congestion_level.name
        return result


@dataclass(frozen=True)
class DifficultySnapshot:
    collected_at: int
    progress_percent: float
    difficulty_change: float
    estimated_retarget_date: int
    remaining_blocks: int
    remaining_time: int
    previous_retarget: float
    previous_time: int
    next_retarget_height: int
    time_avg: int
    adjusted_time_avg: int
    time_offset: int
    expected_blocks: float


@dataclass(frozen=True)
class MiningSnapshot:
    collected_at: int
    current_difficulty: float
    current_hashrate: float  # H/s


@dataclass(frozen=True)
class Alert:
    key: str
    severity: str
    title: str
    description: str
