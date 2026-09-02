"""SQLite storage for mempool snapshots (stdlib only)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    ts          TEXT PRIMARY KEY,   -- ISO8601 UTC
    block_height INTEGER NOT NULL,
    count       INTEGER NOT NULL,
    vsize       INTEGER NOT NULL,
    total_fee   INTEGER NOT NULL,
    fastest_fee INTEGER NOT NULL,
    half_hour_fee INTEGER NOT NULL,
    hour_fee    INTEGER NOT NULL,
    economy_fee INTEGER NOT NULL,
    minimum_fee INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshots_ts ON snapshots(ts);
"""


class SnapshotStore:
    """Persists mempool snapshots to a local SQLite database."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def insert_snapshot(
        self,
        *,
        ts: str,
        block_height: int,
        count: int,
        vsize: int,
        total_fee: int,
        fastest_fee: int,
        half_hour_fee: int,
        hour_fee: int,
        economy_fee: int,
        minimum_fee: int,
    ) -> None:
        self._conn.execute(
            """
            INSERT OR REPLACE INTO snapshots (
                ts, block_height, count, vsize, total_fee,
                fastest_fee, half_hour_fee, hour_fee, economy_fee, minimum_fee
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                ts, block_height, count, vsize, total_fee,
                fastest_fee, half_hour_fee, hour_fee, economy_fee, minimum_fee,
            ),
        )
        self._conn.commit()

    def latest(self) -> sqlite3.Row | None:
        cur = self._conn.execute("SELECT * FROM snapshots ORDER BY ts DESC LIMIT 1")
        return cur.fetchone()

    def recent(self, limit: int = 24) -> list[sqlite3.Row]:
        cur = self._conn.execute(
            "SELECT * FROM snapshots ORDER BY ts DESC LIMIT ?", (limit,)
        )
        return list(cur.fetchall())

    def close(self) -> None:
        self._conn.close()
