#!/usr/bin/env python3
"""Clean stale-CDN outliers from pre-majority-vote data.

The collector fetched mempool.space through DNS round-robin until
2026-09-03 03:16 UTC, so ~2 of every 7 samples were stale reads (~10%
low) from lagging backends .203/.207.

Strategy per block height (only heights lasting < 40 minutes — longer
heights may contain real multi-block growth that looks like two
clusters):
  1. Compute the height's median tx count.
  2. Keep samples within 6% of the median (the majority cluster).
  3. Delete samples >6% away ONLY if they are a minority (<50% of the
     height's samples) — i.e. the median cluster is the majority.

Dry-run by default; pass --apply to delete.

!!! WARNING — already applied once (2026-09-03, 129 rows). The DB is now
    majority-vote clean; DO NOT re-run --apply on the live DB without a
    fresh backup and a dry-run review first.  Historical cleanup scripts
    are kept for audit, not for re-execution.

Usage: python3 scripts/clean_pre_majority.py [--apply] [--until TS]
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mempool_monitor.config import load_config  # noqa: E402
from mempool_monitor.storage import Storage  # noqa: E402

# When the majority-vote collector shipped (2026-09-03 03:16:21 UTC).
MAJORITY_SHIP_TS = 1788405381
TOL = 0.06      # deviate >6% from the height median -> candidate
MAX_HEIGHT_SPAN = 40 * 60  # skip heights lasting > 40 minutes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="delete rows (default: dry run)")
    ap.add_argument("--until", type=int, default=MAJORITY_SHIP_TS,
                    help="only clean snapshots before this ts")
    args = ap.parse_args()

    config = load_config()
    storage = Storage(config.database)
    storage.initialize()
    conn = storage.connection

    rows = conn.execute(
        "SELECT * FROM snapshots WHERE collected_at < ? ORDER BY collected_at ASC",
        (args.until,),
    ).fetchall()

    # Newest live height is excluded entirely (collector still writing it).
    newest = conn.execute(
        "SELECT MAX(latest_block_height) FROM snapshots"
    ).fetchone()[0]

    by_height: dict[int, list] = defaultdict(list)
    for r in rows:
        by_height[r["latest_block_height"]].append(r)

    deleted = []
    for h, group in sorted(by_height.items()):
        if h == newest or len(group) < 4:
            continue
        span = group[-1]["collected_at"] - group[0]["collected_at"]
        if span > MAX_HEIGHT_SPAN:
            # Height held for >40 min: mempool may have really grown,
            # splitting into two legitimate levels.  Don't touch it.
            continue
        counts = sorted(float(r["mempool_count"]) for r in group)
        med = counts[len(counts) // 2]
        if med <= 0:
            continue
        keep = [r for r in group if abs(r["mempool_count"] - med) / med <= TOL]
        stale = [r for r in group if abs(r["mempool_count"] - med) / med > TOL]
        # Only delete samples that are ALSO below 40 MB (stale-low reads).
        # Real values are 41-43 MB; the only way a healthy sample lands in
        # the "stale" bucket is a genuinely bimodal height (real growth),
        # which we must not touch.
        stale = [r for r in stale if r["mempool_vsize"] < 40_000_000]
        if not stale:
            continue
        # Only delete when the median cluster holds a STRICT majority
        # (>50%).  With an even group, exactly half is NOT a majority —
        # a 2:2 bimodal split has no majority and must not delete either
        # side (either cluster could be the real state).
        if len(keep) * 2 <= len(group):
            continue
        if len(keep) < 2:
            continue
        deleted.extend(stale)

    print(f"scanning {len(rows)} snapshots -> candidates: {len(deleted)}")
    for r in sorted(deleted, key=lambda x: x["collected_at"]):
        print(f"  del ts={r['collected_at']} h={r['latest_block_height']} "
              f"tx={r['mempool_count']:,.0f} vMB={r['mempool_vsize']/1e6:.2f}")

    if args.apply and deleted:
        with conn:
            conn.executemany("DELETE FROM snapshots WHERE collected_at = ?",
                             [(r["collected_at"],) for r in deleted])
            conn.executemany("DELETE FROM projected_blocks WHERE collected_at = ?",
                             [(r["collected_at"],) for r in deleted])
        print(f"\ndeleted: {len(deleted)} rows")
    else:
        print(f"\nwould delete: {len(deleted)} rows (run with --apply)")
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
