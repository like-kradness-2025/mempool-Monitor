#!/usr/bin/env python3
"""Generate the latest mempool chart PNG (no delivery).

The Hermes cron job calls this script and then delivers the PNG to the
btc-mempool Discord channel via MEDIA: attachment in its response.

Usage:
  python3 generate_mempool_chart.py [--out PATH] [--limit N] [--db PATH]

Outputs the absolute PNG path on stdout.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CHARTS_DIR = REPO_ROOT / "charts"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate mempool chart PNG")
    parser.add_argument("--out", default=str(CHARTS_DIR / "mempool_chart_latest.png"))
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--db", default="~/.mempool-monitor/mempool.db")
    args = parser.parse_args()

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    db = str(Path(args.db).expanduser())

    sys.path.insert(0, str(SRC))
    os.environ.setdefault("MPLBACKEND", "Agg")
    from mempool_monitor.chart import render_chart
    from mempool_monitor.store import SnapshotStore

    store = SnapshotStore(db)
    try:
        n = len(store.recent(limit=args.limit))
        if n == 0:
            print("no snapshots yet — run snapshot/monitor first", file=sys.stderr)
            return 1
        out = render_chart(store, out_path, limit=args.limit)
    finally:
        store.close()

    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
