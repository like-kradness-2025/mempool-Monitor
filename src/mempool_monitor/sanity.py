"""Sanity filter for mempool snapshots.

mempool.space's CDN occasionally alternates between a current and a stale
/partial /api/mempool response (observed 2026-09-02): the reported tx count
and vsize can drop by ~15-25% for one or a few calls with NO block height
change, then recover.  A real mempool drain of that size takes many blocks
(~10 min each), so a snapshot that deviates strongly from the recent median
while the block height is unchanged is treated as a bad read and rejected.

The collector re-fetches once on rejection; if the second read still looks
bad the snapshot is stored anyway (so we never lose the true state during a
genuine fast drain).
"""

from __future__ import annotations

from .models import Snapshot

# Deviation thresholds vs recent median.
# A block confirmation (height advance) legitimately drains the mempool,
# so allow a wider band right after a new block; when the height is
# static, a >10% drop can only be a stale CDN read -> reject.
MAX_COUNT_DEV_RATIO = 0.10   # >10% away from median tx count -> reject
MAX_VSIZE_DEV_RATIO = 0.10   # >10% away from median vsize -> reject
WIDE_COUNT_DEV_RATIO = 0.15  # right after a block, allow up to 15%
WIDE_VSIZE_DEV_RATIO = 0.15  # right after a block, allow up to 15%
RECENT_WINDOW = 7            # snapshots to use for the median


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 1:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def is_plausible(
    current: Snapshot,
    previous: Snapshot | None,
    recent: list[Snapshot] | None = None,
) -> bool:
    """Return True if *current* looks like a genuine mempool state.

    Uses the recent median (previous snapshots) as the reference band.
    """
    # Fall back to comparing with just the previous snapshot.
    refs: list[Snapshot] = []
    if recent:
        refs = [s for s in recent if s is not None]
    if previous is not None and previous not in refs:
        refs.append(previous)
    refs = refs[-RECENT_WINDOW:]
    if not refs:
        return True

    med_count = _median([float(s.mempool_count) for s in refs])
    med_vsize = _median([float(s.mempool_vsize) for s in refs])
    if med_count <= 0 or med_vsize <= 0:
        return True

    # Right after a new block the mempool legitimately drains; widen the
    # band for that first sample so real post-block dips are not rejected.
    height_advanced = (
        previous is not None
        and current.latest_block_height > previous.latest_block_height
    )
    cnt_limit = WIDE_COUNT_DEV_RATIO if height_advanced else MAX_COUNT_DEV_RATIO
    vsz_limit = WIDE_VSIZE_DEV_RATIO if height_advanced else MAX_VSIZE_DEV_RATIO

    count_dev = abs(current.mempool_count - med_count) / med_count
    vsize_dev = abs(current.mempool_vsize - med_vsize) / med_vsize
    if count_dev > cnt_limit or vsize_dev > vsz_limit:
        return False
    return True
