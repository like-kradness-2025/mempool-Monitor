"""mempool.space API collector (httpx-based, mirrors the original 6-panel build)."""

from __future__ import annotations

import logging
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from itertools import combinations
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
    quality: str = "accepted"
    quality_reason: str | None = None


@dataclass(frozen=True)
class MempoolRead:
    """A mempool payload plus the evidence used to accept it."""

    payload: dict[str, Any]
    quality: str
    reason: str
    valid_probes: int
    quorum_size: int


class MempoolClient:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None):
        self.config = config
        self.transport = transport
        self.client = httpx.Client(
            base_url=config.base_url,
            timeout=config.timeout_seconds,
            transport=transport,
            headers={"User-Agent": "mempool-monitor/0.1"},
        )

    def _is_public_mempool_space(self) -> bool:
        """True when the configured base URL targets the public mempool.space apex.

        The majority-vote probe list is resolved from the apex mempool.space
        DNS and sends ``Host: mempool.space``, so it is only meaningful when
        the client really talks to that exact host.  A custom base_url
        (self-hosted mirror, subdomain deployment, test transport, ...) must
        bypass the vote — a subdomain could serve a different network, and
        mixing its fees/blocks with an apex-voted mempool would corrupt the
        snapshot.
        """
        try:
            from urllib.parse import urlparse

            host = urlparse(self.config.base_url).hostname or ""
        except Exception:
            return False
        return host == "mempool.space"

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "MempoolClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_json(self, path: str, deadline: float | None = None) -> Any:
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries):
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise CollectionError(f"GET {path} exceeded collection deadline")
            try:
                request_timeout = (
                    self.config.timeout_seconds
                    if remaining is None
                    else min(self.config.timeout_seconds, remaining)
                )
                response = self.client.get(path, timeout=request_timeout)
                if response.status_code == 429:
                    retry_after = float(response.headers.get("Retry-After", "1"))
                    sleep_for = min(max(retry_after, 0.1), 30.0)
                    if remaining is not None:
                        sleep_for = min(sleep_for, max(0.0, remaining))
                    time.sleep(sleep_for)
                    continue
                response.raise_for_status()
                return response.json()
            except (httpx.HTTPError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.config.max_retries:
                    sleep_for = float(2**attempt)
                    if deadline is not None:
                        sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
                    time.sleep(sleep_for)
        raise CollectionError(f"GET {path} failed: {last_error}") from last_error

    def collect(self) -> RawCollection:
        started = time.monotonic()
        deadline = started + 35.0
        # Majority-vote mempool fetch: probe several backend IPs directly and
        # adopt the healthy-cluster median (stale CDN backends are excluded).
        # The vote only applies when the effective target really is the
        # public mempool.space AND no test transport was injected — a custom
        # base_url or a mock transport must go through the normal single
        # fetch instead (the probe list would otherwise point at the wrong
        # host, and an injected transport must not receive raw-IP requests
        # nor be shared across the temporary probe clients).
        mempool: dict[str, Any]
        mempool_quality = "accepted"
        mempool_quality_reason: str | None = None
        if self._is_public_mempool_space() and self.transport is None:
            majority = fetch_mempool_majority_result(
                timeout=min(8.0, max(0.1, deadline - time.monotonic()))
            )
            if majority is None:
                # A single DNS response is retained only as explicitly degraded
                # data.  It must never masquerade as a quorum-verified sample.
                mempool = self._get_json("/api/mempool", deadline=deadline)
                mempool_quality = "degraded"
                mempool_quality_reason = "majority quorum unavailable; single DNS read"
            else:
                mempool = majority.payload
                mempool_quality = majority.quality
                mempool_quality_reason = majority.reason
        else:
            mempool = self._get_json("/api/mempool", deadline=deadline)
        fees = self._get_json("/api/v1/fees/recommended", deadline=deadline)
        projected = self._get_json("/api/v1/fees/mempool-blocks", deadline=deadline)
        blocks = self._get_json("/api/blocks", deadline=deadline)
        mining = self._fetch_mining_hashrate(deadline - time.monotonic())
        btc_price = self._fetch_btc_price(deadline - time.monotonic())
        latency_ms = (time.monotonic() - started) * 1000

        if not isinstance(mempool, dict) or not isinstance(fees, dict):
            raise CollectionError("API returned invalid mempool or fee data")
        if not isinstance(projected, list) or not isinstance(blocks, list):
            raise CollectionError("API returned invalid block data")
        validation_error = validate_mempool_payload(mempool)
        if validation_error is not None:
            raise CollectionError(f"invalid mempool payload: {validation_error}")
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
            quality=mempool_quality,
            quality_reason=mempool_quality_reason,
        )

    def _fetch_mining_hashrate(self, timeout: float | None = None) -> dict[str, Any] | None:
        if timeout is not None and timeout <= 0:
            return None
        try:
            request_timeout = (
                self.config.timeout_seconds
                if timeout is None
                else min(self.config.timeout_seconds, timeout)
            )
            with httpx.Client(timeout=request_timeout) as client:
                resp = client.get("https://mempool.space/api/v1/mining/hashrate/24h")
                resp.raise_for_status()
                data = resp.json()
                if "currentDifficulty" not in data or "currentHashrate" not in data:
                    return None
                return data
        except Exception:
            return None

    def _fetch_btc_price(self, timeout: float | None = None) -> float | None:
        if timeout is not None and timeout <= 0:
            return None
        try:
            request_timeout = (
                self.config.timeout_seconds
                if timeout is None
                else min(self.config.timeout_seconds, timeout)
            )
            with httpx.Client(timeout=request_timeout) as client:
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
_MEMPOOL_IPS_CACHE: list[str] | None = None  # successful DNS resolution, TTL-bounded
_MEMPOOL_IPS_CACHED_AT: float = 0.0
_MEMPOOL_IPS_TTL: float = 300.0  # 5 minutes


def _resolve_mempool_ips(force: bool = False) -> list[str]:
    """Resolve mempool.space to its backend IPs, caching for 5 minutes.

    Successful resolutions are cached briefly (the backend set changes
    rarely, so 5 minutes avoids re-resolving on every 1-min tick).  The
    failure fallback (``['mempool.space']``) is deliberately NOT cached so
    a transient DNS outage retries on the next call.  ``force=True``
    bypasses the cache and re-resolves immediately.
    """
    global _MEMPOOL_IPS_CACHE, _MEMPOOL_IPS_CACHED_AT
    now = time.time()
    if (
        not force
        and _MEMPOOL_IPS_CACHE is not None
        and now - _MEMPOOL_IPS_CACHED_AT < _MEMPOOL_IPS_TTL
    ):
        return _MEMPOOL_IPS_CACHE
    try:
        import socket

        infos = socket.getaddrinfo("mempool.space", 443, socket.AF_INET)
        raw: list[Any] = sorted({info[4][0] for info in infos})
        ips = [str(ip) for ip in raw]
        if not ips:
            # No IPs resolved: return fallback WITHOUT caching so the next
            # call retries DNS instead of serving a stale failure forever.
            return ["mempool.space"]
        _MEMPOOL_IPS_CACHE = ips
        _MEMPOOL_IPS_CACHED_AT = now
        return ips
    except Exception:
        # DNS failure: do not cache; retry on next call.
        return ["mempool.space"]


def _is_real_number(x: Any) -> bool:
    """True for real (non-bool) finite int/float numbers."""
    if isinstance(x, bool):
        return False
    if not isinstance(x, (int, float)):
        return False
    try:
        return math.isfinite(float(x))
    except (TypeError, ValueError, OverflowError):
        return False


def _is_nonnegative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def validate_mempool_payload(data: Any) -> str | None:
    """Return a reason when a mempool payload is incomplete or invalid.

    Validation happens before a response can contribute to a quorum.  This
    prevents a count/vsize-only partial response from winning and failing later
    in the processor after it has already been selected.
    """
    if not isinstance(data, dict):
        return "payload is not an object"
    for key in ("count", "vsize", "total_fee"):
        if key not in data or not _is_nonnegative_integer(data[key]):
            return f"{key} is missing or not a non-negative integer"
    histogram = data.get("fee_histogram")
    if not isinstance(histogram, list):
        return "fee_histogram is missing or not a list"
    for index, entry in enumerate(histogram):
        if (
            not isinstance(entry, list)
            or len(entry) != 2
            or not all(_is_real_number(item) for item in entry)
        ):
            return f"fee_histogram[{index}] is invalid"
        if entry[0] < 0 or entry[1] < 0:
            return f"fee_histogram[{index}] contains a negative value"
    return None


def _is_valid_mempool(data: Any) -> bool:
    """Per-probe validation for a complete mempool payload.

    A single malformed response (e.g. ``count='100'``, missing ``vsize``,
    or a non-finite value) must not abort the whole majority vote — it is
    simply rejected and the next IP is probed.  ``count == 0`` is valid (a
    legitimately empty mempool must not be dropped), but negative values
    are rejected: a negative vsize used as a cluster centre would make
    every ratio negative and poison the adopted result.
    """
    return validate_mempool_payload(data) is None


def _within_pct(x: float, y: float, pct: float = 0.08) -> bool:
    """True if ``x`` is within ``pct`` of ``y``; zero-safe.

    Avoids division by zero: when the reference ``y`` is 0, agreement
    requires ``x`` to be exactly 0 too.
    """
    denominator = max(abs(x), abs(y))
    if denominator == 0:
        return True
    return abs(x - y) / denominator <= pct


def fetch_mempool_majority(
    probe_count: int = 5,
    timeout: float = 8.0,
    transport: httpx.BaseTransport | None = None,
) -> dict[str, Any] | None:
    """Query several mempool.space backends and return the majority mempool.

    Each discovered backend IP is probed once with ``/api/mempool``.  A
    unique strict quorum of the complete payloads must agree on both count
    and vsize; failed or invalid targets remain in the quorum denominator.
    The selected response is an actual member of that quorum, so its
    fee_histogram and total_fee stay paired with its count/vsize.  An
    ambiguous split or insufficient quorum yields None (caller records a
    degraded DNS fallback).

    ``transport`` is optional: when provided it is used for the probes
    (instead of creating a fresh ``httpx.Client``), letting callers inject
    a mock transport and never hit the network.
    """
    result = fetch_mempool_majority_result(
        probe_count=probe_count, timeout=timeout, transport=transport
    )
    return result.payload if result is not None else None


def fetch_mempool_majority_result(
    probe_count: int = 5,
    timeout: float = 8.0,
    transport: httpx.BaseTransport | None = None,
) -> MempoolRead | None:
    """Probe every discovered backend and return an evidenced quorum result.

    ``probe_count`` is retained for API compatibility but is no longer used to
    truncate the discovered set.  With the current seven-backend fleet, a
    strict 4/7 quorum is required; failed or invalid probes remain in the
    denominator and cannot silently lower the quorum.
    """
    ips = _resolve_mempool_ips()
    if len(ips) < 2:
        return None
    targets = list(dict.fromkeys(ips))
    required = len(targets) // 2 + 1

    responses: list[dict[str, Any]] = []
    # One client for all probes: a caller-supplied transport must not be
    # opened/closed per probe (closing a context-managed client closes a
    # caller-owned transport that enforces closed state, breaking every
    # subsequent probe).  Without a transport we create our own client for
    # the loop and close it once at the end.
    owns_client = transport is None
    client = httpx.Client(
        timeout=timeout,
        verify=True,
        trust_env=False,
        transport=transport,
    )
    def probe(ip: str) -> dict[str, Any] | None:
        try:
            # Connect to the backend IP directly, keeping the Host header and
            # SNI as mempool.space so TLS remains fully verified.
            resp = client.get(
                f"https://{ip}/api/mempool",
                headers={
                    "User-Agent": "mempool-monitor/0.1",
                    "Host": "mempool.space",
                },
                extensions={"sni_hostname": "mempool.space"},
            )
            resp.raise_for_status()
            data = resp.json()
            if _is_valid_mempool(data):
                return data
        except Exception as exc:  # noqa: BLE001
            logging.debug("mempool probe %s failed: %s", ip, exc)
        return None

    try:
        # Seven requests fit inside the per-request timeout without making the
        # collection deadline equal to 7 * timeout.
        with ThreadPoolExecutor(max_workers=len(targets), thread_name_prefix="mempool-probe") as pool:
            futures = {pool.submit(probe, ip): ip for ip in targets}
            results = {future: future.result() for future in as_completed(futures)}
        responses = []
        for future, _ip in sorted(
            ((future, futures[future]) for future in results), key=lambda item: item[1]
        ):
            result = results[future]
            if result is not None:
                responses.append(result)
    finally:
        if owns_client:
            client.close()

    if not responses:
        return None
    # Find complete pairwise-agreement clusters.  The old center-neighbour
    # heuristic could accept a non-transitive bridge and used an asymmetric
    # percentage denominator.  The fleet is small, so exhaustive combinations
    # are simpler and deterministic.
    def _count(r: dict[str, Any]) -> float:
        return float(r["count"])

    def _vsize(r: dict[str, Any]) -> float:
        return float(r["vsize"])

    def agrees(left: dict[str, Any], right: dict[str, Any]) -> bool:
        return _within_pct(_count(left), _count(right), 0.02) and _within_pct(
            _vsize(left), _vsize(right), 0.02
        )

    qualifying: list[tuple[dict[str, Any], ...]] = []
    max_size = min(len(responses), len(targets))
    for size in range(max_size, required - 1, -1):
        for candidate in combinations(responses, size):
            if all(agrees(left, right) for left, right in combinations(candidate, 2)):
                qualifying.append(candidate)
        if qualifying:
            break
    if not qualifying or len({tuple(id(item) for item in group) for group in qualifying}) != 1:
        logging.warning(
            "mempool majority: no unique quorum (%d valid/%d targets; required=%d)",
            len(responses), len(targets), required,
        )
        return None
    healthy = list(qualifying[0])
    median_count = sorted(_count(r) for r in healthy)[len(healthy) // 2]
    median_vsize = sorted(_vsize(r) for r in healthy)[len(healthy) // 2]
    adopted = min(
        healthy,
        key=lambda r: (
            abs(_count(r) - median_count) + abs(_vsize(r) - median_vsize),
            _count(r),
            _vsize(r),
        ),
    )
    logging.info(
        "mempool majority: %d targets, %d valid, quorum=%d, adopted tx=%d vMB=%.1f",
        len(targets), len(responses), len(healthy), adopted["count"],
        adopted["vsize"] / 1e6,
    )
    return MempoolRead(
        payload=adopted,
        quality="accepted",
        reason=f"unique quorum {len(healthy)}/{len(targets)}",
        valid_probes=len(responses),
        quorum_size=len(healthy),
    )


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
    """Fetch mempool + fees + blocks + difficulty + mining + BTC price, store all.

    Returns True ONLY when a mempool snapshot was inserted this run — the
    auxiliary mining/difficulty writes are side effects and must not make a
    run with a failed (missing) snapshot look successful.
    """
    collected_at = int(time.time())
    ok = False
    snapshot = None
    raw = None
    projected: list[Any] = []

    # Main collection (mempool/fees/blocks/btc_price) — also fetches mining hash rate
    try:
        from .processor import process_collection  # noqa: PLC0415
        from .sanity import is_suspect  # noqa: PLC0415

        with MempoolClient(config) as client:
            raw = client.collect()
            snapshot, projected = process_collection(raw, config)
            # mempool.space's CDN occasionally alternates between a current
            # and a stale/partial response. If the new snapshot deviates too
            # far from the recent median, re-fetch once before accepting it.
            previous = storage.latest_snapshot(
                before=snapshot.collected_at, quality="accepted"
            )
            recent = storage.snapshots_since(
                snapshot.collected_at - 3600, quality="accepted"
            )[-RECENT_FOR_SANITY:]
            if is_suspect(snapshot, previous, recent):
                logging.warning(
                    "implausible snapshot (count %d, vsize %.1fMB); re-fetching once",
                    snapshot.mempool_count,
                    snapshot.mempool_vsize / 1_000_000,
                )
                if raw.quality == "accepted":
                    # A multi-backend quorum corroborates the reading.  A real
                    # fast drain must not be demoted on the median heuristic
                    # alone (the old height-advance widening is gone).
                    logging.info(
                        "snapshot deviates from median but is quorum-accepted "
                        "(%s); keeping as accepted",
                        raw.quality_reason or "quorum",
                    )
                else:
                    raw = client.collect()
                    snapshot, projected = process_collection(raw, config)
                    if is_suspect(snapshot, previous, recent):
                        if raw.quality == "accepted":
                            logging.info(
                                "snapshot deviates from median but is "
                                "quorum-accepted (%s); keeping as accepted",
                                raw.quality_reason or "quorum",
                            )
                        else:
                            # Second read still deviates: keep the reading
                            # (never lose a genuine state) but label it so
                            # alerts/charts can exclude it.
                            snapshot = replace(
                                snapshot,
                                quality="suspect",
                                quality_reason="failed sanity check twice on degraded read",
                            )
                            logging.error(
                                "SUSPECT DATA stored (count=%d vsize=%.1fMB) after "
                                "double re-check; quality=suspect",
                                snapshot.mempool_count,
                                snapshot.mempool_vsize / 1_000_000,
                            )
    except Exception as exc:  # noqa: BLE001
        logging.error("main collection failed: %s", exc)
        snapshot = None

    # Backfill mining fields on the snapshot BEFORE insert so the DB row
    # carries the last-known values when the fresh ones are missing (the
    # difficulty/hashrate panel never goes blank).  Only reuse values newer
    # than 2 hours — a stale fill would misrepresent the current state.
    if snapshot is not None:
        if snapshot.current_difficulty is None or snapshot.current_hashrate is None:
            last_mine = storage.latest_mining()
            if last_mine is not None and (collected_at - last_mine.collected_at) < 7200:
                snapshot = replace(
                    snapshot,
                    current_difficulty=snapshot.current_difficulty
                    if snapshot.current_difficulty is not None
                    else last_mine.current_difficulty,
                    current_hashrate=snapshot.current_hashrate
                    if snapshot.current_hashrate is not None
                    else last_mine.current_hashrate,
                )

    # Store the mempool snapshot first.  Auxiliary tables must not be able to
    # make an otherwise valid core collection disappear.
    if snapshot is not None:
        storage.insert_snapshot(snapshot, projected)
        ok = True
        logging.info(
            "collected level=%s fee=%g mempool_vmb=%.1f block=%d quality=%s",
            snapshot.congestion_level.name,
            snapshot.fastest_fee,
            snapshot.mempool_vsize / 1_000_000,
            snapshot.latest_block_height,
            snapshot.quality,
        )

    # Mining data write is auxiliary; a collision or locked auxiliary table
    # must not invalidate the already-committed core snapshot.
    if snapshot is not None and raw is not None:
        if raw.current_difficulty is not None and raw.current_hashrate is not None:
            try:
                storage.insert_mining(
                    MiningSnapshot(
                        collected_at=snapshot.collected_at,
                        current_difficulty=raw.current_difficulty,
                        current_hashrate=raw.current_hashrate,
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logging.error("mining auxiliary write failed: %s", exc)

    # Fallback: fetch mining data separately only when the main collection
    # completely failed (no snapshot at all).
    if snapshot is None:
        mine_data = fetch_mining_hashrate()
        if mine_data is not None:
            try:
                storage.insert_mining(
                    MiningSnapshot(
                        collected_at=collected_at,
                        current_difficulty=float(mine_data["currentDifficulty"]),
                        current_hashrate=float(mine_data["currentHashrate"]),
                    )
                )
            except Exception as exc:  # noqa: BLE001
                logging.error("mining fallback write failed: %s", exc)

    # Difficulty (separate API call, but only ~hourly to keep 1-min collection light)
    try:
        last_diff = storage.latest_difficulty()
        if last_diff is None or collected_at - last_diff.collected_at > 3600:
            diff_data = fetch_difficulty()
            if diff_data is not None:
                diff_snap = _difficulty_to_snapshot(diff_data)
                try:
                    storage.insert_difficulty(diff_snap)
                except Exception as exc:  # noqa: BLE001
                    logging.error("difficulty auxiliary write failed: %s", exc)
            else:
                logging.warning("difficulty fetch failed; keeping last value")
    except Exception as exc:  # noqa: BLE001
        logging.error("difficulty maintenance failed: %s", exc)

    # Size maintenance is also outside the core collection result.
    try:
        storage.enforce_size_limit()
    except Exception as exc:  # noqa: BLE001
        logging.error("size maintenance failed: %s", exc)
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
