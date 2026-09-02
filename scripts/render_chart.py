#!/usr/bin/env python3
"""Render the 6-panel mempool dashboard chart from stored snapshots.

Used by the 15-minute delivery cron job. Prints "chart: <path>" on success.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
CHARTS_DIR = REPO_ROOT / "charts"
PYTHON = sys.executable


def main() -> int:
    out_path = CHARTS_DIR / "mempool_chart_latest.png"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    env["MPLBACKEND"] = "Agg"

    rc = subprocess.run(
        [PYTHON, "-m", "mempool_monitor.cli", "chart", "--output", str(out_path)],
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
