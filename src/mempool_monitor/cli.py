"""CLI entry point for mempool-monitor (stdlib only)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .api import MempoolClient
from .store import SnapshotStore

DEFAULT_DB = "~/.mempool-monitor/mempool.db"
DEFAULT_INTERVAL = 60  # seconds


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _fmt(vsize: int) -> str:
    return f"{vsize / 1_000_000:.1f} MB"


def cmd_snapshot(args: argparse.Namespace) -> int:
    """Fetch one snapshot, print it, and store it."""
    client = MempoolClient(base_url=args.base_url, timeout=args.timeout)
    stats = client.get_mempool_stats()
    fees = client.get_recommended_fees()
    height = client.get_block_height()

    out = {
        "ts": _now_iso(),
        "block_height": height,
        "count": stats.count,
        "vsize": stats.vsize,
        "total_fee": stats.total_fee,
        "fastest_fee": fees.fastest_fee,
        "half_hour_fee": fees.half_hour_fee,
        "hour_fee": fees.hour_fee,
        "economy_fee": fees.economy_fee,
        "minimum_fee": fees.minimum_fee,
    }

    if not args.no_store:
        store = SnapshotStore(args.db)
        store.insert_snapshot(**out)
        store.close()

    if args.json:
        print(json.dumps(out, ensure_ascii=False))
    else:
        print(
            f"[{out['ts']}] height={height} txs={stats.count:,} "
            f"vsize={_fmt(stats.vsize)} fastest={fees.fastest_fee} sat/vB"
        )
    return 0


def cmd_monitor(args: argparse.Namespace) -> int:
    """Poll the mempool periodically and persist snapshots."""
    client = MempoolClient(base_url=args.base_url, timeout=args.timeout)
    store = SnapshotStore(args.db)
    print(
        f"Monitoring mempool every {args.interval}s "
        f"(db={store.db_path}) — Ctrl+C to stop",
        file=sys.stderr,
    )
    try:
        while True:
            stats = client.get_mempool_stats()
            fees = client.get_recommended_fees()
            height = client.get_block_height()
            out = {
                "ts": _now_iso(),
                "block_height": height,
                "count": stats.count,
                "vsize": stats.vsize,
                "total_fee": stats.total_fee,
                "fastest_fee": fees.fastest_fee,
                "half_hour_fee": fees.half_hour_fee,
                "hour_fee": fees.hour_fee,
                "economy_fee": fees.economy_fee,
                "minimum_fee": fees.minimum_fee,
            }
            store.insert_snapshot(**out)
            print(
                f"[{out['ts']}] height={height} txs={stats.count:,} "
                f"vsize={_fmt(stats.vsize)} fastest={fees.fastest_fee} sat/vB",
                flush=True,
            )
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.", file=sys.stderr)
        return 0
    finally:
        store.close()


def cmd_latest(args: argparse.Namespace) -> int:
    """Show the latest stored snapshot."""
    store = SnapshotStore(args.db)
    row = store.latest()
    store.close()
    if row is None:
        print("No snapshots yet. Run `mempool-monitor snapshot` first.", file=sys.stderr)
        return 1
    print(dict(row) if args.json else _row_line(row))
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """Show recent stored snapshots."""
    store = SnapshotStore(args.db)
    rows = store.recent(limit=args.limit)
    store.close()
    if not rows:
        print("No snapshots yet.", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps([dict(r) for r in rows], ensure_ascii=False))
    else:
        for r in rows:
            print(_row_line(r))
    return 0


def _row_line(row) -> str:
    return (
        f"[{row['ts']}] height={row['block_height']} txs={row['count']:,} "
        f"vsize={_fmt(row['vsize'])} fastest={row['fastest_fee']} sat/vB"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mempool-monitor",
        description="Bitcoin mempool monitoring & analysis (mempool.space API)",
    )
    parser.add_argument("--base-url", default="https://mempool.space/api", help="API base URL")
    parser.add_argument("--timeout", type=int, default=15, help="HTTP timeout (s)")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite DB path")
    sub = parser.add_subparsers(dest="command", required=True)

    p_snap = sub.add_parser("snapshot", help="Fetch one snapshot and store it")
    p_snap.add_argument("--json", action="store_true", help="Output as JSON")
    p_snap.add_argument("--no-store", action="store_true", help="Don't persist to DB")
    p_snap.set_defaults(func=cmd_snapshot)

    p_mon = sub.add_parser("monitor", help="Poll continuously and persist")
    p_mon.add_argument("--interval", type=int, default=DEFAULT_INTERVAL, help="Poll interval (s)")
    p_mon.set_defaults(func=cmd_monitor)

    p_lat = sub.add_parser("latest", help="Show latest stored snapshot")
    p_lat.add_argument("--json", action="store_true")
    p_lat.set_defaults(func=cmd_latest)

    p_hist = sub.add_parser("history", help="Show recent stored snapshots")
    p_hist.add_argument("--limit", type=int, default=24)
    p_hist.add_argument("--json", action="store_true")
    p_hist.set_defaults(func=cmd_history)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    args.db = str(Path(args.db).expanduser())
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001 — CLI top-level
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
