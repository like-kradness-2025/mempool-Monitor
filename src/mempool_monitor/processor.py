"""Process raw API data into snapshots."""

from __future__ import annotations

from typing import Any

from .collector import RawCollection
from .config import Config
from .models import CongestionLevel, ProjectedBlock, Snapshot

FEE_THRESHOLDS = (1, 2, 5, 10, 20, 50)


class DataValidationError(ValueError):
    pass


def _number(data: dict[str, Any], key: str, minimum: float = 0) -> float:
    value = data.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise DataValidationError(f"{key} is missing or not numeric")
    if value < minimum:
        raise DataValidationError(f"{key} is below {minimum}")
    return float(value)


def calculate_backlogs(histogram: Any) -> dict[int, float]:
    if not isinstance(histogram, list):
        raise DataValidationError("fee_histogram is missing or not a list")
    totals = {threshold: 0.0 for threshold in FEE_THRESHOLDS}
    for entry in histogram:
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(isinstance(item, (int, float)) for item in entry)
        ):
            raise DataValidationError("fee_histogram contains an invalid entry")
        fee_rate, vsize = float(entry[0]), float(entry[1])
        if fee_rate < 0 or vsize < 0:
            raise DataValidationError("fee_histogram contains a negative value")
        for threshold in FEE_THRESHOLDS:
            if fee_rate >= threshold:
                totals[threshold] += vsize
    return totals


def congestion_level(
    fastest_fee: float, backlogs: dict[int, float], config: Config
) -> CongestionLevel:
    backlog_5_blocks = backlogs[5] / 1_000_000
    backlog_10_blocks = backlogs[10] / 1_000_000
    backlog_20_blocks = backlogs[20] / 1_000_000
    if (
        fastest_fee >= config.extreme_fee
        or backlog_20_blocks >= config.extreme_backlog_blocks
    ):
        return CongestionLevel.EXTREME
    if (
        fastest_fee >= config.high_fee
        or backlog_10_blocks >= config.high_backlog_blocks
    ):
        return CongestionLevel.HIGH
    if (
        fastest_fee > config.moderate_fee
        or backlog_5_blocks >= config.moderate_backlog_blocks
    ):
        return CongestionLevel.MODERATE
    return CongestionLevel.LOW


def process_collection(
    raw: RawCollection, config: Config
) -> tuple[Snapshot, list[ProjectedBlock]]:
    if len(raw.blocks) < 2:
        raise DataValidationError("at least two recent blocks are required")

    backlogs = calculate_backlogs(raw.mempool.get("fee_histogram"))
    latest = raw.blocks[0]
    if not isinstance(latest, dict):
        raise DataValidationError("latest block is invalid")

    timestamps = [
        int(_number(block, "timestamp"))
        for block in raw.blocks[:6]
        if isinstance(block, dict)
    ]
    if len(timestamps) < 2:
        raise DataValidationError("recent block timestamps are incomplete")
    # Blocks are returned newest-first by height. Timestamps can be
    # slightly out of order when blocks are mined rapidly (miners have
    # timestamp flexibility).  Handle gracefully: filter non-positive
    # intervals and use a safe minimum of 1 second.
    intervals = [
        max(timestamps[i] - timestamps[i + 1], 1)
        for i in range(len(timestamps) - 1)
    ]

    fastest_fee = _number(raw.fees, "fastestFee")
    snapshot = Snapshot(
        collected_at=raw.collected_at,
        fastest_fee=fastest_fee,
        half_hour_fee=_number(raw.fees, "halfHourFee"),
        hour_fee=_number(raw.fees, "hourFee"),
        economy_fee=_number(raw.fees, "economyFee"),
        minimum_fee=_number(raw.fees, "minimumFee"),
        mempool_count=int(_number(raw.mempool, "count")),
        mempool_vsize=int(_number(raw.mempool, "vsize")),
        mempool_total_fee=int(_number(raw.mempool, "total_fee")),
        backlog_1=backlogs[1],
        backlog_2=backlogs[2],
        backlog_5=backlogs[5],
        backlog_10=backlogs[10],
        backlog_20=backlogs[20],
        backlog_50=backlogs[50],
        latest_block_height=int(_number(latest, "height")),
        latest_block_timestamp=int(_number(latest, "timestamp")),
        block_age_seconds=max(0, raw.collected_at - int(latest["timestamp"])),
        avg_block_interval_seconds=sum(intervals) / len(intervals),
        latest_block_tx_count=int(_number(latest, "tx_count")),
        latest_block_size=int(_number(latest, "size")),
        latest_block_weight=int(_number(latest, "weight")),
        congestion_level=congestion_level(fastest_fee, backlogs, config),
        provider=config.provider,
        api_latency_ms=raw.latency_ms,
        btc_price_usd=raw.btc_price_usd,
        current_difficulty=raw.current_difficulty,
        current_hashrate=raw.current_hashrate,
    )

    projected: list[ProjectedBlock] = []
    for position, block in enumerate(raw.projected_blocks):
        if not isinstance(block, dict):
            raise DataValidationError("projected block is invalid")
        fee_range = block.get("feeRange", [])
        if not isinstance(fee_range, list):
            raise DataValidationError("projected block feeRange is invalid")
        projected.append(
            ProjectedBlock(
                position=position,
                n_tx=int(_number(block, "nTx")),
                block_vsize=_number(block, "blockVSize"),
                total_fees=int(_number(block, "totalFees")),
                median_fee=_number(block, "medianFee"),
                fee_range=[float(value) for value in fee_range],
            )
        )
    return snapshot, projected
