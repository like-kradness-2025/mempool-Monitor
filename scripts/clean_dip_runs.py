#!/usr/bin/env python3
"""Remove remaining stale-dip runs (consecutive low samples under a trend).

Second cleanup pass: after clean_pre_majority (per-height) and
clean_oscillation (single dips), some heights that lasted very long
(h=965186 held ~80 min with no block) still contain RUNS of stale-low
samples (40-42 MB) under a real growth trend (44-46 MB).  A sample whose
vsize is >5% below the ±15 rolling median AND whose previous sample is
also low is part of a stale run -> delete.

Dry-run by default; pass --apply.

Usage: python3 scripts/clean_dip_runs.py [--apply] [--until TS]
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
WIDE = 15
DOWN_TOL = 0.04  # >4% below trend counts as "low" (tighter for runs)


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
    # Mark each sample low/high vs its wide median.
    low_flags = []
    for i in range(n):
        if i < WIDE or i >= n - WIDE:
            low_flags.append(False)
            continue
        r = rows[i]
        win = [vs[j] for j in range(i - WIDE, i + WIDE + 1) if j != i]
        med = sorted(win)[len(win) // 2]
        low_flags.append(med > 0 and r["mempool_vsize"] < med * (1 - DOWN_TOL))

    deleted = []
    i = 0
    while i < n:
        if low_flags[i] and rows[i]["latest_block_height"] != newest:
            # start of a run
            j = i
            while j < n and low_flags[j]:
                j += 1
            run = rows[i:j]
            if len(run) >= 2:
                # Find the run's local high (the highest of the run + the
                # sample before/after).  A real drain keeps falling and
                # the height advances; a stale run sits under a trend at
                # the SAME height and bounces back.  Require the samples
                # AROUND the run to be clearly higher than the run AND on
                # the same block height — a run that ends in a height
                # change is a real post-block drain, never delete it.
                before = rows[i - 1] if i > 0 else None
                after = rows[j] if j < n else None
                same_h = all(
                    nb is not None
                    and nb["latest_block_height"] == rows[i]["latest_block_height"]
                    for nb in (before, after)
                )
                if not same_h:
                    i = j
                    continue
                before_v = before["mempool_vsize"] if before else None
                after_v = after["mempool_vsize"] if after else None
                hi = max(v for v in (before_v, after_v) if v)
                run_hi = max(r["mempool_vsize"] for r in run)
                if hi > 0 and hi * (1 - DOWN_TOL) > run_hi:
                    deleted.extend(run)
            i = j
        else:
            i += 1

    print(f"scanning {n} -> dip-run candidates: {len(deleted)}")
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
