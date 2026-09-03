#!/usr/bin/env python3
"""Remove remaining CDN oscillation from long single-height periods.

Heights that lasted >40 min (e.g. h=965186 held 15:09-16:28 UTC on
2026-09-02 while no block arrived) contain REAL mempool growth (41->46
MB) mixed with stale-CDN dips back to ~40-42 MB.  A plain per-height
median would delete the growth; instead we use a WIDE rolling median
(±15 samples ~ 30 min) as the local trend and delete only samples that
sit >5% BELOW that trend while their neighbours sit near it.

Dry-run by default; pass --apply.

Usage: python3 scripts/clean_oscillation.py [--apply] [--until TS]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from mempool_monitor.config import load_config  # noqa: E402
from mempool_monitor.storage import Storage  # noqa: E402

MAJORITY_SHIP_TS = 1788405381
WIDE = 15      # rolling window half-width (samples)
DOWN_TOL = 0.05  # >5% below the local trend -> stale dip


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--until", type=int, default=MAJORITY_SHIP_TS)
    args = ap.parse_args()

    config = load_config()
    storage = Storage(config.database)
    storage.initialize()
    conn = storage.connection

    rows = conn.execute(
        "SELECT * FROM snapshots WHERE collected_at < ? ORDER BY collected_at ASC",
        (args.until,),
    ).fetchall()
    newest = conn.execute(
        "SELECT MAX(latest_block_height) FROM snapshots"
    ).fetchone()[0]

    n = len(rows)
    vs = [r["mempool_vsize"] for r in rows]
    deleted = []
    for i in range(WIDE, n - WIDE):
        r = rows[i]
        if r["latest_block_height"] == newest:
            continue
        # Local trend from the wide window (excluding self).
        win = [vs[j] for j in range(i - WIDE, i + WIDE + 1) if j != i]
        ordered = sorted(win)
        med = ordered[len(ordered) // 2]
        if med <= 0:
            continue
        # Only delete downward dips (stale reads are always LOW).
        if r["mempool_vsize"] < med * (1 - DOWN_TOL):
            # Neighbours must sit near the trend (not a real drain edge).
            near = [j for j in (i - 1, i + 1)
                    if rows[j]["mempool_vsize"] >= med * (1 - DOWN_TOL)]
            if len(near) >= 1:
                deleted.append(r)

    print(f"scanning {n} -> oscillation candidates: {len(deleted)}")
    for r in sorted(deleted, key=lambda x: x["collected_at"]):
        print(f"  del ts={r['collected_at']} h={r['latest_block_height']} "
              f"vMB={r['mempool_vsize']/1e6:.2f}")

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
