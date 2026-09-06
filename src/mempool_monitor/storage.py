"""SQLite storage for mempool-monitor."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from .models import CongestionLevel, DifficultySnapshot, MiningSnapshot, ProjectedBlock, Snapshot


class Storage:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.row_factory = sqlite3.Row

    def close(self) -> None:
        self.connection.close()

    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def initialize(self) -> None:
        # auto_vacuum must be set before any table is created (fresh DBs only);
        # it is ignored on existing DBs. Enables PRAGMA incremental_vacuum later.
        self.connection.execute("PRAGMA auto_vacuum=INCREMENTAL")
        self.connection.executescript(
            """
            PRAGMA journal_mode=WAL;
            PRAGMA foreign_keys=ON;

            CREATE TABLE IF NOT EXISTS snapshots (
                collected_at INTEGER PRIMARY KEY,
                fastest_fee REAL NOT NULL,
                half_hour_fee REAL NOT NULL,
                hour_fee REAL NOT NULL,
                economy_fee REAL NOT NULL,
                minimum_fee REAL NOT NULL,
                mempool_count INTEGER NOT NULL,
                mempool_vsize INTEGER NOT NULL,
                mempool_total_fee INTEGER NOT NULL,
                backlog_1 REAL NOT NULL,
                backlog_2 REAL NOT NULL,
                backlog_5 REAL NOT NULL,
                backlog_10 REAL NOT NULL,
                backlog_20 REAL NOT NULL,
                backlog_50 REAL NOT NULL,
                latest_block_height INTEGER NOT NULL,
                latest_block_timestamp INTEGER NOT NULL,
                block_age_seconds INTEGER NOT NULL,
                avg_block_interval_seconds REAL NOT NULL,
                latest_block_tx_count INTEGER NOT NULL,
                latest_block_size INTEGER NOT NULL,
                latest_block_weight INTEGER NOT NULL,
                congestion_level TEXT NOT NULL,
                provider TEXT NOT NULL,
                api_latency_ms REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS projected_blocks (
                collected_at INTEGER NOT NULL,
                position INTEGER NOT NULL,
                n_tx INTEGER NOT NULL,
                block_vsize REAL NOT NULL,
                total_fees INTEGER NOT NULL,
                median_fee REAL NOT NULL,
                fee_range_json TEXT NOT NULL,
                PRIMARY KEY (collected_at, position),
                FOREIGN KEY (collected_at) REFERENCES snapshots(collected_at)
                    ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS delivery_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                attempted_at INTEGER NOT NULL,
                message_type TEXT NOT NULL,
                status TEXT NOT NULL,
                http_status INTEGER,
                error_summary TEXT,
                discord_message_id TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_delivery_type_time
            ON delivery_log(message_type, attempted_at DESC);

            CREATE TABLE IF NOT EXISTS runtime_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS difficulty (
                collected_at INTEGER PRIMARY KEY,
                progress_percent REAL NOT NULL,
                difficulty_change REAL NOT NULL,
                estimated_retarget_date INTEGER NOT NULL,
                remaining_blocks INTEGER NOT NULL,
                remaining_time INTEGER NOT NULL,
                previous_retarget REAL NOT NULL,
                previous_time INTEGER NOT NULL,
                next_retarget_height INTEGER NOT NULL,
                time_avg INTEGER NOT NULL,
                adjusted_time_avg INTEGER NOT NULL,
                time_offset INTEGER NOT NULL,
                expected_blocks REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mining (
                collected_at INTEGER PRIMARY KEY,
                current_difficulty REAL NOT NULL,
                current_hashrate REAL NOT NULL
            );
            """
        )
        self.connection.commit()
        self._migrate_schema()

    def _migrate_schema(self) -> None:
        for col in ("btc_price_usd", "current_difficulty", "current_hashrate"):
            try:
                self.connection.execute(
                    f"ALTER TABLE snapshots ADD COLUMN {col} REAL"
                )
            except sqlite3.OperationalError:
                pass  # column already exists

    def insert_snapshot(
        self, snapshot: Snapshot, projected_blocks: list[ProjectedBlock]
    ) -> None:
        data = snapshot.as_dict()
        columns = ", ".join(data)
        placeholders = ", ".join(f":{name}" for name in data)
        with self.connection:
            self.connection.execute(
                f"INSERT OR REPLACE INTO snapshots ({columns}) VALUES ({placeholders})",
                data,
            )
            self.connection.execute(
                "DELETE FROM projected_blocks WHERE collected_at = ?",
                (snapshot.collected_at,),
            )
            self.connection.executemany(
                """
                INSERT INTO projected_blocks (
                    collected_at, position, n_tx, block_vsize, total_fees,
                    median_fee, fee_range_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        snapshot.collected_at,
                        block.position,
                        block.n_tx,
                        block.block_vsize,
                        block.total_fees,
                        block.median_fee,
                        json.dumps(block.fee_range),
                    )
                    for block in projected_blocks
                ],
            )

    @staticmethod
    def _snapshot(row: sqlite3.Row | None) -> Snapshot | None:
        if row is None:
            return None
        data = dict(row)
        # Remove legacy columns that no longer exist in the model
        for legacy in ("difficulty", "estimated_hashrate"):
            data.pop(legacy, None)
        data["congestion_level"] = CongestionLevel.from_name(
            data["congestion_level"]
        )
        return Snapshot(**data)

    def latest_snapshot(self, before: int | None = None) -> Snapshot | None:
        if before is None:
            row = self.connection.execute(
                "SELECT * FROM snapshots ORDER BY collected_at DESC LIMIT 1"
            ).fetchone()
        else:
            row = self.connection.execute(
                """
                SELECT * FROM snapshots
                WHERE collected_at < ?
                ORDER BY collected_at DESC LIMIT 1
                """,
                (before,),
            ).fetchone()
        return self._snapshot(row)

    def snapshot_at_or_before(self, timestamp: int) -> Snapshot | None:
        row = self.connection.execute(
            """
            SELECT * FROM snapshots
            WHERE collected_at <= ?
            ORDER BY collected_at DESC LIMIT 1
            """,
            (timestamp,),
        ).fetchone()
        return self._snapshot(row)

    def snapshots_since(self, timestamp: int) -> list[Snapshot]:
        rows = self.connection.execute(
            """
            SELECT * FROM snapshots
            WHERE collected_at >= ?
            ORDER BY collected_at ASC
            """,
            (timestamp,),
        ).fetchall()
        return [snapshot for row in rows if (snapshot := self._snapshot(row))]

    def record_delivery(
        self,
        message_type: str,
        status: str,
        http_status: int | None = None,
        error_summary: str | None = None,
        discord_message_id: str | None = None,
    ) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO delivery_log (
                    attempted_at, message_type, status, http_status,
                    error_summary, discord_message_id
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    int(time.time()),
                    message_type,
                    status,
                    http_status,
                    error_summary,
                    discord_message_id,
                ),
            )

    def latest_delivery(self, message_type: str, status: str = "success") -> int | None:
        row = self.connection.execute(
            """
            SELECT attempted_at FROM delivery_log
            WHERE message_type = ? AND status = ?
            ORDER BY attempted_at DESC LIMIT 1
            """,
            (message_type, status),
        ).fetchone()
        return int(row["attempted_at"]) if row else None

    def get_state(self, key: str, default: str = "") -> str:
        row = self.connection.execute(
            "SELECT value FROM runtime_state WHERE key = ?", (key,)
        ).fetchone()
        return str(row["value"]) if row else default

    def set_state(self, key: str, value: str) -> None:
        with self.connection:
            self.connection.execute(
                """
                INSERT INTO runtime_state (key, value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    updated_at = excluded.updated_at
                """,
                (key, value, int(time.time())),
            )

    def prune(self, retention_days: int) -> int:
        cutoff = int(time.time()) - retention_days * 86400
        with self.connection:
            cursor = self.connection.execute(
                "DELETE FROM snapshots WHERE collected_at < ?", (cutoff,)
            )
        return cursor.rowcount

    # ── Difficulty / Mining methods ──────────────────────────────────────────

    def insert_difficulty(self, snapshot: DifficultySnapshot) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO difficulty
                   (collected_at, progress_percent, difficulty_change,
                    estimated_retarget_date, remaining_blocks, remaining_time,
                    previous_retarget, previous_time, next_retarget_height,
                    time_avg, adjusted_time_avg, time_offset, expected_blocks)
                   VALUES (:collected_at, :progress_percent, :difficulty_change,
                    :estimated_retarget_date, :remaining_blocks, :remaining_time,
                    :previous_retarget, :previous_time, :next_retarget_height,
                    :time_avg, :adjusted_time_avg, :time_offset, :expected_blocks)""",
                {
                    "collected_at": snapshot.collected_at,
                    "progress_percent": snapshot.progress_percent,
                    "difficulty_change": snapshot.difficulty_change,
                    "estimated_retarget_date": snapshot.estimated_retarget_date,
                    "remaining_blocks": snapshot.remaining_blocks,
                    "remaining_time": snapshot.remaining_time,
                    "previous_retarget": snapshot.previous_retarget,
                    "previous_time": snapshot.previous_time,
                    "next_retarget_height": snapshot.next_retarget_height,
                    "time_avg": snapshot.time_avg,
                    "adjusted_time_avg": snapshot.adjusted_time_avg,
                    "time_offset": snapshot.time_offset,
                    "expected_blocks": snapshot.expected_blocks,
                },
            )

    def insert_mining(self, snapshot: MiningSnapshot) -> None:
        with self.connection:
            self.connection.execute(
                """INSERT OR REPLACE INTO mining
                   (collected_at, current_difficulty, current_hashrate)
                   VALUES (?, ?, ?)""",
                (snapshot.collected_at, snapshot.current_difficulty, snapshot.current_hashrate),
            )

    def latest_difficulty(self) -> DifficultySnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM difficulty ORDER BY collected_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return DifficultySnapshot(**dict(row))

    def latest_mining(self) -> MiningSnapshot | None:
        row = self.connection.execute(
            "SELECT * FROM mining ORDER BY collected_at DESC LIMIT 1"
        ).fetchone()
        if row is None:
            return None
        return MiningSnapshot(**dict(row))

    def difficulty_since(self, timestamp: int) -> list[DifficultySnapshot]:
        rows = self.connection.execute(
            "SELECT * FROM difficulty WHERE collected_at >= ? ORDER BY collected_at ASC",
            (timestamp,),
        ).fetchall()
        return [DifficultySnapshot(**dict(row)) for row in rows]

    def mining_since(self, timestamp: int) -> list[MiningSnapshot]:
        rows = self.connection.execute(
            "SELECT * FROM mining WHERE collected_at >= ? ORDER BY collected_at ASC",
            (timestamp,),
        ).fetchall()
        return [MiningSnapshot(**dict(row)) for row in rows]

    # ── Size management ──────────────────────────────────────────────────────

    def db_size_bytes(self) -> int:
        return self.path.stat().st_size

    # Time column used by each prunable table. delivery_log uses attempted_at;
    # the rest use collected_at. projected_blocks is intentionally absent: it is
    # CASCADE-deleted when its parent snapshots are pruned, so pruning it by
    # fraction would over-delete.
    _TIME_COLUMNS = {
        "snapshots": "collected_at",
        "projected_blocks": "collected_at",
        "delivery_log": "attempted_at",
        "difficulty": "collected_at",
        "mining": "collected_at",
    }

    def _prune_oldest(self, fraction: float = 0.3) -> int:
        """Remove oldest `fraction` of rows from time-series tables when DB exceeds limit.

        The whole prune runs in a single transaction so a failure on any table
        rolls everything back (no partial prune). projected_blocks is not pruned
        directly: deleting snapshots cascades to it.
        """
        total = 0
        with self.connection:
            for table in ("snapshots", "delivery_log", "difficulty", "mining"):
                time_col = self._TIME_COLUMNS[table]
                count_row = self.connection.execute(
                    f"SELECT COUNT(*) AS c FROM {table}"
                ).fetchone()
                cnt = int(count_row["c"])
                if cnt == 0:
                    continue
                keep = int(cnt * (1 - fraction))
                if keep <= 0:
                    keep = 1
                threshold_row = self.connection.execute(
                    f"SELECT {time_col} FROM {table} "
                    f"ORDER BY {time_col} ASC LIMIT 1 OFFSET ?",
                    (max(0, cnt - keep),),
                ).fetchone()
                if threshold_row is None:
                    continue
                threshold = int(threshold_row[time_col])
                cursor = self.connection.execute(
                    f"DELETE FROM {table} WHERE {time_col} < ?", (threshold,)
                )
                total += cursor.rowcount
        if total > 0:
            # Outside the transaction above: shrink the WAL and reclaim freed
            # pages.  auto_vacuum is only INCREMENTAL on databases created
            # after this setting shipped; existing DBs stay NONE and must be
            # fully VACUUMed instead (incremental_vacuum is a no-op there).
            self.connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            self._reclaim_space()
            self.connection.commit()
        return total

    def _reclaim_space(self) -> None:
        """Reclaim pages freed by a prune, honouring the DB's auto_vacuum mode.

        SQLite mode values: 0=NONE, 1=FULL, 2=INCREMENTAL.
        - INCREMENTAL: run PRAGMA incremental_vacuum and consume the full
          cursor (it yields one row per reclaimed page — discarding the
          cursor after execute() would reclaim only a single page).
        - NONE/FULL: incremental_vacuum is a no-op (NONE) or unnecessary
          (FULL auto-vacuums); a full VACUUM rebuilds the file so the freed
          pages actually shrink db_size_bytes().  Runs only after a prune
          deleted rows (size-limit crossing is rare).
        """
        mode = self.connection.execute("PRAGMA auto_vacuum").fetchone()[0]
        if mode == 2:  # INCREMENTAL
            cursor = self.connection.execute("PRAGMA incremental_vacuum")
            cursor.fetchall()
        else:
            self.connection.execute("VACUUM")

    def enforce_size_limit(self, max_bytes: int = 1_000_000_000) -> int:
        """Check DB size and prune if over limit. Returns number of rows deleted."""
        if self.db_size_bytes() <= max_bytes:
            return 0
        return self._prune_oldest(0.3)

    def statistics(self) -> dict[str, Any]:
        snapshot_count = self.connection.execute(
            "SELECT COUNT(*) AS count FROM snapshots"
        ).fetchone()["count"]
        first = self.connection.execute(
            "SELECT MIN(collected_at) AS value FROM snapshots"
        ).fetchone()["value"]
        last = self.connection.execute(
            "SELECT MAX(collected_at) AS value FROM snapshots"
        ).fetchone()["value"]

        def _count(table: str) -> int:
            row = self.connection.execute(f"SELECT COUNT(*) AS c FROM {table}").fetchone()
            return int(row["c"])

        def _min_max(table: str) -> tuple[int | None, int | None]:
            f = self.connection.execute(f"SELECT MIN(collected_at) AS v FROM {table}").fetchone()
            l = self.connection.execute(f"SELECT MAX(collected_at) AS v FROM {table}").fetchone()
            return (int(f["v"]) if f["v"] else None,
                    int(l["v"]) if l["v"] else None)

        diff_first, diff_last = _min_max("difficulty")
        mine_first, mine_last = _min_max("mining")
        return {
            "snapshot_count": snapshot_count,
            "first": first,
            "last": last,
            "difficulty_snapshots": _count("difficulty"),
            "difficulty_first": diff_first,
            "difficulty_last": diff_last,
            "mining_snapshots": _count("mining"),
            "mining_first": mine_first,
            "mining_last": mine_last,
            "db_size_bytes": self.db_size_bytes(),
            "db_size_mb": round(self.db_size_bytes() / 1_000_000, 1),
        }
