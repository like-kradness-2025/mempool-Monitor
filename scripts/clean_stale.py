"""Remove stale-CDN mempool snapshots (block height unchanged, vsize jump).

mempool.space's CDN occasionally serves a stale /api/mempool response for
2-6 minutes: the reported tx count / vsize drops ~15-25% while the block
height stays the same, then recovers.  A real mempool drain of that size
requires block confirmations (height changes), so snapshots that jump by
more than VSIZE_JUMP / COUNT_DROP from the previous accepted value WITHOUT
a block height change are treated as bad reads and removed.

Usage:
  python3 clean_stale.py [--apply] [DB]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

VSIZE_JUMP_MB = 4.0    # vsize change > 4MB with no height change -> stale
COUNT_DROP = 0.12      # tx count drop > 12% with no height change -> stale


def clean_stale(db: str | Path, dry_run: bool = True) -> list[int]:
    conn = sqlite3.connect(str(Path(db).expanduser()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT collected_at, latest_block_height, mempool_count, mempool_vsize "
        "FROM snapshots ORDER BY collected_at ASC"
    ).fetchall()

    stale: list[int] = []
    prev = None
    for row in rows:
        if prev is not None:
            same_height = row["latest_block_height"] == prev["latest_block_height"]
            if same_height:
                dv_mb = abs(row["mempool_vsize"] - prev["mempool_vsize"]) / 1e6
                dc = (prev["mempool_count"] - row["mempool_count"]) / max(prev["mempool_count"], 1)
                # vsize jump (either direction) or tx drop while height static
                if dv_mb > VSIZE_JUMP_MB or dc > COUNT_DROP:
                    stale.append(row["collected_at"])
                    continue  # don't chain: next row compares to prev accepted
        prev = row

    if not dry_run and stale:
        conn.executemany(
            "DELETE FROM snapshots WHERE collected_at = ?",
            [(t,) for t in stale],
        )
        conn.commit()

    conn.close()
    return stale


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else "~/.mempool-monitor/mempool.sqlite3"
    dry = "--apply" not in sys.argv
    stale = clean_stale(db, dry_run=dry)
    print(f"{'[DRY RUN] ' if dry else ''}stale snapshots: {len(stale)}")
    for t in stale[:40]:
        print(f"  {t}")
