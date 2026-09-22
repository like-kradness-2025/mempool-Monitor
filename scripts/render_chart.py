#!/usr/bin/env python3
"""Render the 6-panel mempool dashboard chart from stored snapshots.

Used by the periodic delivery cron jobs. The window and output file name are
selectable so one script serves the 24h (5-minute), 1-week (hourly) and
1-month (6-hourly) deliveries.
Prints "chart: <path>" on success.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CHARTS_DIR = REPO_ROOT / "charts"
PYTHON = sys.executable


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--hours", type=int, default=24,
        help="Hours of history to plot (default: 24)",
    )
    parser.add_argument(
        "--name", default="mempool_chart_latest.png",
        help="Output file name inside charts/ (default: mempool_chart_latest.png)",
    )
    args = parser.parse_args(argv)

    out_path = CHARTS_DIR / args.name
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["MPLBACKEND"] = "Agg"

    rc = subprocess.run(
        [PYTHON, "-m", "mempool_monitor.cli", "chart", "--output", str(out_path),
         "--hours", str(args.hours)],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
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
