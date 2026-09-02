#!/usr/bin/env python3
"""Collect mempool snapshots and render the 6-panel dashboard chart.

Used by the Hermes cron job: collects fresh snapshots, then renders the
restored 6-panel "Mempool Monitor — Live Dashboard" chart via the CLI.

Usage:
  python3 collect_and_chart.py [--ticks N] [--interval S] [--out PATH]

Outputs "chart: <absolute path>" on stdout on success.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CHARTS_DIR = REPO_ROOT / "charts"
PYTHON = sys.executable


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect + render 6-panel chart")
    parser.add_argument("--ticks", type=int, default=1, help="snapshots to collect")
    parser.add_argument("--interval", type=int, default=60, help="seconds between ticks")
    parser.add_argument("--out", default=str(CHARTS_DIR / "mempool_chart_latest.png"))
    args = parser.parse_args()

    env = {"PYTHONPATH": str(SRC), "MPLBACKEND": "Agg"}

    for i in range(args.ticks):
        rc = subprocess.run(
            [PYTHON, "-m", "mempool_monitor.cli", "collect"],
            cwd=REPO_ROOT, env={**__import__("os").environ, **env},
        ).returncode
        if rc != 0:
            print(f"collect failed (tick {i+1})", file=sys.stderr)
            return rc
        if i < args.ticks - 1:
            time.sleep(args.interval)

    out_path = Path(args.out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rc = subprocess.run(
        [PYTHON, "-m", "mempool_monitor.cli", "chart", "--output", str(out_path)],
        cwd=REPO_ROOT, env={**__import__("os").environ, **env},
    ).returncode
    if rc != 0:
        print("chart generation failed", file=sys.stderr)
        return rc
    if not out_path.is_file() or out_path.stat().st_size == 0:
        print(f"missing chart: {out_path}", file=sys.stderr)
        return 1

    print(f"chart: {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
