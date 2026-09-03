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
        # Majority-vote mempool fetch: probe several backend IPs directly and
        # adopt the healthy-cluster median (stale CDN backends are excluded).
        mempool = fetch_mempool_majority() or self._get_json("/api/mempool")
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

# mempool.space resolves to 7 backend IPs (103.165.192.202-208, observed
# 2026-09-03); ~2 of them (.203/.207) serve stale mempool data (~10% lower
# tx/vsize) while reporting the current block height.  DNS round-robin gives
# a ~29% chance of reading the stale backend on any single fetch.  We query
# several backends directly and adopt the majority value.
_MEMPOOL_SPACE_IPS: list[str] | None = None  # populated on first use


def _resolve_mempool_ips() -> list[str]:
    global _MEMPOOL_SPACE_IPS
    if _MEMPOOL_SPACE_IPS:
        return _MEMPOOL_SPACE_IPS
    try:
        import socket

        infos = socket.getaddrinfo("mempool.space", 443, socket.AF_INET)
        raw: list[Any] = sorted({info[4][0] for info in infos})
        ips = [str(ip) for ip in raw]
        _MEMPOOL_SPACE_IPS = ips or ["mempool.space"]
    except Exception:
        _MEMPOOL_SPACE_IPS = ["mempool.space"]
    return _MEMPOOL_SPACE_IPS


def fetch_mempool_majority(
    probe_count: int = 5,
    timeout: float = 8.0,
) -> dict[str, Any] | None:
    """Query several mempool.space backends and return the majority mempool.

    Each backend IP is probed once with ``/api/mempool``.  The tx counts
    should cluster tightly (current state); a backend that deviates >10%
    from the median count is a stale read and is discarded.  If at least
    ``ceil(probe_count/2)`` healthy responses remain, the median of the
    healthy ones is returned as the adopted mempool state (the response
    with the count closest to that median, so fee_histogram etc. are real).

    Returns None when fewer than half of the probes succeeded or agreed
    (caller falls back to a plain DNS fetch).
    """
    ips = _resolve_mempool_ips()
    if len(ips) < 2:
        return None
    targets = ips[:probe_count]

    responses: list[dict[str, Any]] = []
    for ip in targets:
        try:
            # Connect to the backend IP directly, keeping the Host header as
            # mempool.space.  Certificate verification is disabled because
            # the cert is issued for the hostname, not the raw IP; the TLS
            # handshake still validates the chain via the system trust store
            # unless verify=False skips it — we accept that trade-off for
            # backend probing and fall back to a verified DNS fetch on
            # failure.
            with httpx.Client(timeout=timeout, verify=False) as client:
                resp = client.get(
                    f"https://{ip}/api/mempool",
                    headers={
                        "User-Agent": "mempool-monitor/0.1",
                        "Host": "mempool.space",
                    },
                )
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data, dict) and data.get("count"):
                responses.append(data)
        except Exception as exc:  # noqa: BLE001
            logging.debug("mempool probe %s failed: %s", ip, exc)

    if not responses:
        return None
    # Healthy cluster: the tx-count value around which most responses
    # cluster.  A plain median breaks when stale reads are the majority of
    # *responses* but not of the *cluster* — so instead we find the densest
    # cluster: for each response, count neighbours within 8%; the response
    # with the most neighbours is the cluster centre.
    def _cluster_size(r: dict[str, Any]) -> int:
        c = float(r["count"])
        return sum(
            1 for other in responses
            if abs(float(other["count"]) - c) / c <= 0.08
        )

    centre = max(responses, key=_cluster_size)
    centre_c = float(centre["count"])
    healthy = [r for r in responses if abs(r["count"] - centre_c) / centre_c <= 0.08]
    required = (probe_count + 1) // 2
    if len(healthy) < required:
        logging.warning(
            "mempool majority: only %d/%d healthy responses; falling back",
            len(healthy), probe_count,
        )
        return None
    # Adopt the response whose count is closest to the healthy median.
    healthy_med = sorted(float(r["count"]) for r in healthy)[
        len(healthy) // 2
    ]
    adopted = min(healthy, key=lambda r: abs(r["count"] - healthy_med))
    logging.info(
        "mempool majority: %d probes, %d healthy, adopted tx=%d vMB=%.1f",
        len(responses), len(healthy), adopted["count"],
        adopted["vsize"] / 1e6,
    )
    return adopted


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
