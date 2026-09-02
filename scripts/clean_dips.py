"""Remove isolated stale-CDN dips (short-lived, neighbours normal).

CDN stale responses appear as short dips (1-3 min) to ~37MB while the
neighbouring snapshots (within ~6 min) are normal (~41-43MB).  Longer
periods of lower values are treated as real mempool drains and kept.

A snapshot is stale if:
  - its vsize < NEIGHBOUR_MIN_MB (39MB)
  - AND at least 3 of its 4 neighbours (2 before, 2 after) are above
    NORMAL_MIN_MB (40MB)  -> it is an isolated dip, not a trend.

Usage:
  python3 clean_dips.py [--apply] [DB]
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

DIP_MAX_MB = 39.0
NORMAL_MIN_MB = 40.5
NEIGHBOURS = 2


def clean_dips(db: str | Path, dry_run: bool = True) -> list[int]:
    conn = sqlite3.connect(str(Path(db).expanduser()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT collected_at, mempool_vsize FROM snapshots ORDER BY collected_at ASC"
    ).fetchall()
    n = len(rows)

    stale: list[int] = []
    for i, row in enumerate(rows):
        v_mb = row["mempool_vsize"] / 1e6
        if v_mb >= DIP_MAX_MB:
            continue
        # neighbour median (up to 2 each side, excluding self)
        nbr = []
        for j in range(max(0, i - NEIGHBOURS), min(n, i + NEIGHBOURS + 1)):
            if j != i:
                nbr.append(rows[j]["mempool_vsize"] / 1e6)
        if len(nbr) < 2:
            continue
        nbr_sorted = sorted(nbr)
        med = nbr_sorted[len(nbr_sorted) // 2]
        # isolated dip: median of neighbours is clearly normal
        if med >= NORMAL_MIN_MB and v_mb < med - 3.0:
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
    stale = clean_dips(db, dry_run=dry)
    print(f"{'[DRY RUN] ' if dry else ''}isolated dips: {len(stale)}")
    for t in stale[:60]:
        print(f"  {t}")
