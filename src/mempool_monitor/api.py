"""mempool.space API client (stdlib only)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_BASE_URL = "https://mempool.space/api"
DEFAULT_TIMEOUT = 15


class MempoolAPIError(RuntimeError):
    """Raised when the mempool.space API returns an error."""


@dataclass(frozen=True)
class MempoolStats:
    """Snapshot of the mempool from /api/mempool."""

    count: int            # number of unconfirmed transactions
    vsize: int            # total virtual size in vbytes
    total_fee: int        # total fee in satoshis
    fee_histogram: list[tuple[float, int]]  # (fee_rate, vsize) buckets

    @property
    def vsize_mb(self) -> float:
        return self.vsize / 1_000_000


@dataclass(frozen=True)
class RecommendedFees:
    """Fee rates in sat/vB from /api/v1/fees/recommended."""

    fastest_fee: int
    half_hour_fee: int
    hour_fee: int
    economy_fee: int
    minimum_fee: int


class MempoolClient:
    """Thin client for the mempool.space REST API."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str) -> Any:
        url = f"{self.base_url}{path}"
        try:
            with urllib.request.urlopen(url, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise MempoolAPIError(f"HTTP {exc.code} for {url}") from exc
        except urllib.error.URLError as exc:
            raise MempoolAPIError(f"Connection error for {url}: {exc.reason}") from exc
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise MempoolAPIError(f"Invalid JSON from {url}: {exc}") from exc

    def get_mempool_stats(self) -> MempoolStats:
        data = self._get("/mempool")
        return MempoolStats(
            count=int(data["count"]),
            vsize=int(data["vsize"]),
            total_fee=int(data["total_fee"]),
            fee_histogram=[(float(r), int(v)) for r, v in data.get("fee_histogram", [])],
        )

    def get_recommended_fees(self) -> RecommendedFees:
        data = self._get("/v1/fees/recommended")
        return RecommendedFees(
            fastest_fee=int(data["fastestFee"]),
            half_hour_fee=int(data["halfHourFee"]),
            hour_fee=int(data["hourFee"]),
            economy_fee=int(data["economyFee"]),
            minimum_fee=int(data["minimumFee"]),
        )

    def get_block_height(self) -> int:
        return int(self._get("/blocks/tip/height"))
