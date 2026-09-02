"""Import block history JSON into SQLite for analysis/charts."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS block_history (
    height INTEGER PRIMARY KEY,
    timestamp INTEGER NOT NULL,
    tx_count INTEGER NOT NULL,
    size INTEGER NOT NULL,
    weight INTEGER NOT NULL,
    difficulty REAL NOT NULL,
    median_fee REAL,
    avg_fee_rate REAL,
    total_fees INTEGER,
    fee_range_json TEXT,
    pool_name TEXT,
    reward INTEGER
);
"""


def import_blocks(json_path: str | Path, db_path: str | Path) -> int:
    data = json.loads(Path(json_path).read_text())
    blocks = data["blocks"]

    db = Path(db_path).expanduser()
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db)
    conn.executescript(SCHEMA)

    rows = []
    for b in blocks:
        extras = b.get("extras") or {}
        pool = extras.get("pool") or {}
        rows.append((
            int(b["height"]),
            int(b["timestamp"]),
            int(b.get("tx_count", 0)),
            int(b.get("size", 0)),
            int(b.get("weight", 0)),
            float(b.get("difficulty", 0)),
            extras.get("medianFee"),
            extras.get("avgFeeRate"),
            extras.get("totalFees"),
            json.dumps(extras.get("feeRange", [])),
            pool.get("name"),
            extras.get("reward"),
        ))

    conn.executemany(
        """INSERT OR REPLACE INTO block_history
           (height, timestamp, tx_count, size, weight, difficulty,
            median_fee, avg_fee_rate, total_fees, fee_range_json, pool_name, reward)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        rows,
    )
    conn.commit()
    n = conn.execute("SELECT COUNT(*) FROM block_history").fetchone()[0]
    conn.close()
    return n


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "data/block_history_30d.json"
    dst = sys.argv[2] if len(sys.argv) > 2 else "~/.mempool-monitor/block_history.sqlite3"
    n = import_blocks(src, dst)
    print(f"imported {n} blocks -> {dst}")
