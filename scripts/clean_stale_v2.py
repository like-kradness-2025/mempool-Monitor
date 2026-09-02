"""Clean stale-CDN snapshots using a rolling-median band.

mempool.space's CDN intermittently serves stale /api/mempool responses
(2-6 min, tx/vsize drop ~15-25% with no block confirmations).  This
removes any snapshot whose mempool_count deviates >15% from the local
rolling median (window 11, centered) — regardless of whether neighbours
were also stale, which the height-jump heuristic misses on chained stales.

Usage:
  python3 clean_stale_v2.py [--apply] [DB]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

COUNT_DEV = 0.15   # >15% from rolling median count -> stale
VSIZE_DEV = 0.12   # >12% from rolling median vsize -> stale
WINDOW = 11        # centered rolling window (5 before + 5 after)


def clean_stale_v2(db: str | Path, dry_run: bool = True) -> list[int]:
    conn = sqlite3.connect(str(Path(db).expanduser()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT collected_at, mempool_count, mempool_vsize "
        "FROM snapshots ORDER BY collected_at ASC"
    ).fetchall()
    n = len(rows)

    stale: list[int] = []
    half = WINDOW // 2
    for i, row in enumerate(rows):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = rows[lo:hi]
        counts = sorted(r["mempool_count"] for r in window)
        vsizes = sorted(r["mempool_vsize"] for r in window)
        med_c = counts[len(counts) // 2]
        med_v = vsizes[len(vsizes) // 2]
        if med_c <= 0:
            continue
        dev_c = abs(row["mempool_count"] - med_c) / med_c
        dev_v = abs(row["mempool_vsize"] - med_v) / med_v if med_v > 0 else 0
        if dev_c > COUNT_DEV or dev_v > VSIZE_DEV:
            stale.append(row["collected_at"])

    if not dry_run and stale:
        conn.executemany(
            "DELETE FROM snapshots WHERE collected_at = ?",
            [(t,) for t in stale],
        )
        conn.execute(
            "DELETE FROM projected_blocks WHERE collected_at IN (%s)"
            % ",".join("?" * len(stale)),
            stale,
        )
        conn.commit()

    conn.close()
    return stale


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") \
        else "~/.mempool-monitor/mempool.sqlite3"
    dry = "--apply" not in sys.argv
    stale = clean_stale_v2(db, dry_run=dry)
    print(f"{'[DRY RUN] ' if dry else ''}stale snapshots: {len(stale)}")
    for t in stale[:50]:
        print(f"  {t}")
