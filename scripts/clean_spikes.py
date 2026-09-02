"""Remove isolated spike snapshots (stale/partial mempool.space responses).

A spike is a snapshot whose mempool_count / vsize deviates >20% / >15% from
both its immediate neighbours, where the neighbours agree with each other
(within 5%).  Those are single-call API glitches, not real mempool drains.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

COUNT_DROP = 0.20
VSIZE_DROP = 0.15
NEIGHBOUR_TOL = 0.05


def clean_spikes(db: str | Path, dry_run: bool = True) -> list[int]:
    conn = sqlite3.connect(str(Path(db).expanduser()))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT collected_at, mempool_count, mempool_vsize "
        "FROM snapshots ORDER BY collected_at ASC"
    ).fetchall()

    spikes: list[int] = []
    for i in range(1, len(rows) - 1):
        prev, cur, nxt = rows[i - 1], rows[i], rows[i + 1]
        pv, cv, nv = prev["mempool_vsize"], cur["mempool_vsize"], nxt["mempool_vsize"]
        pc, cc, nc = prev["mempool_count"], cur["mempool_count"], nxt["mempool_count"]

        # neighbours agree with each other?
        if pv <= 0 or nv <= 0:
            continue
        if abs(pv - nv) / max(pv, nv) > NEIGHBOUR_TOL:
            continue  # real movement, not a spike
        if abs(pc - nc) / max(pc, nc) > NEIGHBOUR_TOL:
            continue

        # current deviates from both neighbours?
        drop_v = max((pv - cv) / pv, (nv - cv) / nv)
        drop_c = max((pc - cc) / pc, (nc - cc) / nc)
        if drop_v > VSIZE_DROP or drop_c > COUNT_DROP:
            spikes.append(cur["collected_at"])

    if not dry_run and spikes:
        conn.executemany(
            "DELETE FROM snapshots WHERE collected_at = ?",
            [(t,) for t in spikes],
        )
        conn.commit()

    conn.close()
    return spikes


if __name__ == "__main__":
    db = sys.argv[1] if len(sys.argv) > 1 else "~/.mempool-monitor/mempool.sqlite3"
    dry = "--apply" not in sys.argv
    spikes = clean_spikes(db, dry_run=dry)
    print(f"{'[DRY RUN] ' if dry else ''}spikes detected: {len(spikes)}")
    for t in spikes[:30]:
        print(f"  {t}")
