"""mempool.space API collector (httpx-based, mirrors the original 6-panel build)."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from .config import Config
from .models import DifficultySnapshot, MiningSnapshot


class CollectionError(RuntimeError):
    pass


# How many recent snapshots to feed the sanity median filter.
RECENT_FOR_SANITY = 7


@dataclass(frozen=True)
class RawCollection:
    collected_at: int
    mempool: dict[str, Any]
    fees: dict[str, Any]
    projected_blocks: list[dict[str, Any]]
    blocks: list[dict[str, Any]]
    latency_ms: float
    btc_price_usd: float | None = None
    current_difficulty: float | None = None
    current_hashrate: float | None = None


class MempoolClient:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            transport=transport,
            headers={"User-Agent": "mempool-monitor/0.1"},
        )

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MempoolClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_json(self, path: str) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            try:
                response = self.client.get(path)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    time.sleep(min(max(retry_after, 0.1), 30.0))
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.config.max_retries:
                    time.sleep(2**attempt)
        raise CollectionError(f"GET {path} failed: {last_error}") from last_error

    def collect(self) -> RawCollection:
        started = time.monotonic()
        mempool = self._get_json("/api/mempool")
        fees = self._get_json("/api/v1/fees/recommended")
        projected = self._get_json("/api/v1/fees/mempool-blocks")
        blocks = self._get_json("/api/blocks")
        mining = self._fetch_mining_hashrate()
        btc_price = self._fetch_btc_price()
        latency_ms = (time.monotonic() - started) * 1000

        if not isinstance(mempool, dict) or not isinstance(fees, dict):
            raise CollectionError("API returned invalid mempool or fee data")
        if not isinstance(projected, list) or not isinstance(blocks, list):
            raise CollectionError("API returned invalid block data")
        return RawCollection(
            collected_at=int(time.time()),
            mempool=mempool,
            fees=fees,
            projected_blocks=projected[:6],
            blocks=blocks,
            latency_ms=latency_ms,
            btc_price_usd=btc_price,
            current_difficulty=float(mining["currentDifficulty"]) if mining else None,
            current_hashrate=float(mining["currentHashrate"]) if mining else None,
        )

    def _fetch_mining_hashrate(self) -> dict[str, Any] | None:
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                resp = client.get("https://mempool.space/api/v1/mining/hashrate/24h")
                resp.raise_for_status()
                data = resp.json()
                if "currentDifficulty" not in data or "currentHashrate" not in data:
                    return None
                return data
        except Exception:
            return None

    def _fetch_btc_price(self) -> float | None:
        try:
            with httpx.Client(timeout=self.config.timeout_seconds) as client:
                resp = client.get(
                    "https://api.coingecko.com/api/v3/simple/price",
                    params={"ids": "bitcoin", "vs_currencies": "usd"},
                )
                resp.raise_for_status()
                return float(resp.json()["bitcoin"]["usd"])
        except Exception:
            return None


# ── Standalone helper functions ──────────────────────────────────────────────


def fetch_difficulty(timeout: float = 10.0) -> dict[str, Any] | None:
    """Fetch difficulty adjustment data from mempool.space API."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get(
                "https://mempool.space/api/v1/difficulty-adjustment"
            )
            resp.raise_for_status()
            data = resp.json()
            required = [
                "progressPercent", "difficultyChange", "estimatedRetargetDate",
                "remainingBlocks", "remainingTime", "previousRetarget",
                "previousTime", "nextRetargetHeight", "timeAvg",
                "adjustedTimeAvg", "timeOffset", "expectedBlocks",
            ]
            for key in required:
                if key not in data:
                    logging.warning("difficulty API missing field: %s", key)
                    return None
            return data
    except Exception as exc:
        logging.warning("difficulty API fetch failed: %s", exc)
        return None


def _difficulty_to_snapshot(data: dict[str, Any]) -> DifficultySnapshot:
    return DifficultySnapshot(
        collected_at=int(time.time()),
        progress_percent=float(data["progressPercent"]),
        difficulty_change=float(data["difficultyChange"]),
        estimated_retarget_date=int(data["estimatedRetargetDate"]),
        remaining_blocks=int(data["remainingBlocks"]),
        remaining_time=int(data["remainingTime"]),
        previous_retarget=float(data["previousRetarget"]),
        previous_time=int(data["previousTime"]),
        next_retarget_height=int(data["nextRetargetHeight"]),
        time_avg=int(data["timeAvg"]),
        adjusted_time_avg=int(data["adjustedTimeAvg"]),
        time_offset=int(data["timeOffset"]),
        expected_blocks=float(data["expectedBlocks"]),
    )


def fetch_mining_hashrate(timeout: float = 10.0) -> dict[str, Any] | None:
    """Fetch mining hash rate data from mempool.space API."""
    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.get("https://mempool.space/api/v1/mining/hashrate/24h")
            resp.raise_for_status()
            data = resp.json()
            if "currentDifficulty" not in data or "currentHashrate" not in data:
                return None
            return data
    except Exception as exc:
        logging.warning("mining API fetch failed: %s", exc)
        return None


def collect_once_all(config: Config, storage: Any) -> bool:
    """Fetch mempool + fees + blocks + difficulty + mining + BTC price, store all."""
    collected_at = int(time.time())
    ok = False
    snapshot = None

    # Main collection (mempool/fees/blocks/btc_price) — also fetches mining hash rate
    try:
        from .processor import process_collection  # noqa: PLC0415
        from .sanity import is_plausible  # noqa: PLC0415

        with MempoolClient(config) as client:
            raw = client.collect()
            snapshot, projected = process_collection(raw, config)
            # mempool.space's CDN occasionally alternates between a current
            # and a stale/partial response. If the new snapshot deviates too
            # far from the recent median, re-fetch once before accepting it.
            previous = storage.latest_snapshot(before=snapshot.collected_at)
            recent = storage.snapshots_since(
                snapshot.collected_at - 3600
            )[-RECENT_FOR_SANITY:]
            if not is_plausible(snapshot, previous, recent):
                logging.warning(
                    "implausible snapshot (count %d, vsize %.1fMB); re-fetching once",
                    snapshot.mempool_count,
                    snapshot.mempool_vsize / 1_000_000,
                )
                raw = client.collect()
                snapshot, projected = process_collection(raw, config)
                if not is_plausible(snapshot, previous, recent):
                    logging.warning(
                        "second fetch still implausible (count=%d); storing anyway",
                        snapshot.mempool_count,
                    )
        storage.insert_snapshot(snapshot, projected)
        ok = True
        logging.info(
            "collected level=%s fee=%g mempool_vmb=%.1f block=%d",
            snapshot.congestion_level.name,
            snapshot.fastest_fee,
            snapshot.mempool_vsize / 1_000_000,
            snapshot.latest_block_height,
        )

        # Reuse mining data from main collection (avoids double API call)
        if raw.current_difficulty is not None and raw.current_hashrate is not None:
            mine_snap = MiningSnapshot(
                collected_at=collected_at,
                current_difficulty=raw.current_difficulty,
                current_hashrate=raw.current_hashrate,
            )
            storage.insert_mining(mine_snap)

        # Fallback: if mining fields are missing (API hiccup), fill snapshot
        # from the last stored values so the difficulty panel never goes blank.
        if snapshot.current_difficulty is None or snapshot.current_hashrate is None:
            last_mine = storage.latest_mining()
            if last_mine is not None:
                from dataclasses import replace as _replace  # noqa: PLC0415

                snapshot = _replace(
                    snapshot,
                    current_difficulty=snapshot.current_difficulty
                    if snapshot.current_difficulty is not None
                    else last_mine.current_difficulty,
                    current_hashrate=snapshot.current_hashrate
                    if snapshot.current_hashrate is not None
                    else last_mine.current_hashrate,
                )
    except (CollectionError, Exception) as exc:
        logging.error("main collection failed: %s", exc)
        # Fallback: fetch mining data separately
        mine_data = fetch_mining_hashrate()
        if mine_data is not None:
            mine_snap = MiningSnapshot(
                collected_at=collected_at,
                current_difficulty=float(mine_data["currentDifficulty"]),
                current_hashrate=float(mine_data["currentHashrate"]),
            )
            storage.insert_mining(mine_snap)
            ok = True

    # Difficulty (separate API call, but only ~hourly to keep 1-min collection light)
    last_diff = storage.latest_difficulty()
    if last_diff is None or collected_at - last_diff.collected_at > 3600:
        diff_data = fetch_difficulty()
        if diff_data is not None:
            diff_snap = _difficulty_to_snapshot(diff_data)
            storage.insert_difficulty(diff_snap)
            ok = True
        else:
            logging.warning("difficulty fetch failed; keeping last value")

    # Enforce size limit
    storage.enforce_size_limit()
    return ok


def collect_loop_all(
    config: Config,
    storage: Any,
    interval: int = 60,
    max_iterations: int | None = None,
) -> int:
    """Run full collection loop (mempool + difficulty + mining) at `interval` seconds."""
    count = 0
    iteration = 0
    while max_iterations is None or iteration < max_iterations:
        iteration += 1
        start = time.monotonic()
        if collect_once_all(config, storage):
            count += 1
        elapsed = time.monotonic() - start
        sleep_time = max(0, interval - elapsed)
        if max_iterations is None or iteration < max_iterations:
            time.sleep(sleep_time)
    return count
