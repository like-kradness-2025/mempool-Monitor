#!/usr/bin/env python3
"""Infinite-loop mempool collector daemon (1-min cadence).

Runs `mempool_monitor.cli collect` every COLLECT_INTERVAL seconds in a loop.
Self-healing: transient API failures are retried on the next tick and do not
kill the daemon; repeated failures are logged to a persistent file.
Designed to run as a systemd user service (Restart=always).

Usage:
  python3 collect_loop_daemon.py [--interval 60] [--once]
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "src"
LOG_DIR = Path.home() / ".mempool-monitor" / "logs"
LOG_FILE = LOG_DIR / "collect_daemon.log"
RUNNING = True


def _log(msg: str) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with LOG_FILE.open("a") as fh:
        fh.write(f"[{ts}] {msg}\n")


def _handle_stop(signum, frame):  # noqa: ARG001
    global RUNNING
    RUNNING = False
    _log(f"received signal {signum}; stopping")


def collect_once(env: dict) -> int:
    result = subprocess.run(
        [sys.executable, "-m", "mempool_monitor.cli", "collect"],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=45,
    )
    if result.returncode != 0:
        _log(f"collect failed (exit {result.returncode}): {result.stderr[-300:]}")
    return result.returncode


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=60)
    parser.add_argument("--once", action="store_true", help="collect once and exit")
    args = parser.parse_args()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    _log(f"collector daemon started (interval={args.interval}s)")

    failures = 0
    while RUNNING:
        started = time.monotonic()
        rc = collect_once(env)
        if rc == 0:
            failures = 0
        else:
            failures += 1
        elapsed = time.monotonic() - started
        sleep_for = max(1, args.interval - elapsed)
        if args.once:
            break
        # Sleep in small chunks so SIGTERM is handled promptly.
        slept = 0.0
        while RUNNING and slept < sleep_for:
            time.sleep(min(1.0, sleep_for - slept))
            slept += 1.0

    _log(f"collector daemon stopped (failures={failures})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
