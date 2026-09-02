#!/usr/bin/env python3
"""Periodic mempool snapshot collection + chart generation.

Used by the Hermes cron job: this script collects a fresh snapshot and renders
the latest chart PNG. The cron prompt then delivers the PNG to Discord via
MEDIA:<path> in its response.

Usage:
  python3 collect_and_chart.py [--interval N] [--ticks N] [--out PATH] [--db PATH]

Defaults: 1 tick (single snapshot + chart). The cron job itself decides cadence.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CHARTS_DIR = REPO_ROOT / "charts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect snapshot + render chart")
    parser.add_argument("--interval", type=int, default=0,
                        help="seconds between ticks (0 = single tick)")
    parser.add_argument("--ticks", type=int, default=1,
                        help="number of snapshots to collect")
    parser.add_argument("--out", default=str(CHARTS_DIR / "mempool_chart_latest.png"))
    parser.add_argument("--db", default="~/.mempool-monitor/mempool.db")
    args = parser.parse_args()

    out_path = Path(args.out).expanduser()
    db = str(Path(args.db).expanduser())

    sys.path.insert(0, str(SRC))
    os_env = __import__("os")
    os_env.environ.setdefault("MPLBACKEND", "Agg")

    from mempool_monitor.api import MempoolClient
    from mempool_monitor.chart import render_chart
    from mempool_monitor.cli import _now_iso
    from mempool_monitor.store import SnapshotStore

    client = MempoolClient(timeout=20)
    store = SnapshotStore(db)

    ok = 0
    try:
        for i in range(args.ticks):
            for attempt in range(4):
                try:
                    stats = client.get_mempool_stats()
                    fees = client.get_recommended_fees()
                    height = client.get_block_height()
                    store.insert_snapshot(
                        ts=_now_iso(), block_height=height, count=stats.count,
                        vsize=stats.vsize, total_fee=stats.total_fee,
                        fastest_fee=fees.fastest_fee,
                        half_hour_fee=fees.half_hour_fee,
                        hour_fee=fees.hour_fee, economy_fee=fees.economy_fee,
                        minimum_fee=fees.minimum_fee,
                    )
                    ok += 1
                    print(f"tick {i+1}: txs={stats.count} fees={fees.fastest_fee}",
                          flush=True)
                    break
                except Exception as exc:  # noqa: BLE001
                    print(f"tick {i+1}: retry {attempt+1}: {exc}", flush=True)
                    time.sleep(3)
            if i < args.ticks - 1:
                time.sleep(args.interval)

        if ok == 0:
            print("no snapshots collected", file=sys.stderr)
            return 1

        n = len(store.recent(limit=500))
        out = render_chart(store, out_path, limit=500)
        print(f"chart: {out} (from {n} snapshots)", flush=True)
    finally:
        store.close()

    return 0


if __name__ == "__main__":
    sys.exit(main())
