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
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
PYTHON = sys.executable


def main() -> int:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)
    rc = subprocess.run(
        [PYTHON, "-m", "mempool_monitor.cli", "collect"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True,
    ).returncode
    if rc != 0:
        print("mempool collect failed", file=sys.stderr)
        return rc
    return 0  # silent success


if __name__ == "__main__":
    sys.exit(main())
