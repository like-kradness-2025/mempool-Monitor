#!/usr/bin/env python3
"""Collect one mempool snapshot (silent on success, error on failure).

Used by the 1-minute collection cron job. Prints nothing on success so the
cron tick stays silent; exits non-zero on failure so the cron error alert
fires.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
PYTHON = sys.executable


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    result = subprocess.run(
        [PYTHON, "-m", "mempool_monitor.cli", "collect"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Log failures to a persistent file for later analysis (cron is silent).
        log_dir = Path.home() / ".mempool-monitor" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "collect_failures.log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with log_file.open("a") as fh:
            fh.write(f"[{ts}] exit={result.returncode}\n")
            if result.stderr:
                fh.write(result.stderr[-500:] + "\n")
        print(f"mempool collect failed (exit {result.returncode})", file=sys.stderr)
        return result.returncode
    return 0  # silent success


if __name__ == "__main__":
    sys.exit(main())
