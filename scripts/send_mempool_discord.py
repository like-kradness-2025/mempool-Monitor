#!/usr/bin/env python3
"""Generate a mempool chart and deliver it to Discord.

Pattern mirrors send_burst_cvd_discord.py: chart generation + delivery via
btc-discord.mjs. This is a standalone sender script so the Hermes cron job can
call it directly.

Usage:
  python3 send_mempool_discord.py [--out PATH] [--hours N]

Environment:
  MEMPOOL_DB   SQLite path (default ~/.mempool-monitor/mempool.db)
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

DISCORD_ROOT = Path("/home/weed420/Tool/btc-discord-tool")
DISCORD_CLI = DISCORD_ROOT / "bin/btc-discord.mjs"
CONFIG = DISCORD_ROOT / "config.json"
ENV = DISCORD_ROOT / ".env"

WEBHOOK = "burst-cvd-btc"  # デフォルト配信先 (cvdカテゴリ)
MESSAGE = "BTC Mempool Monitor | mempool.space"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate + deliver mempool chart")
    parser.add_argument("--out", "--output", dest="output",
                        default=str(CHARTS_DIR / "mempool_chart_latest.png"),
                        help="Output PNG path (--out and --output both accepted)")
    parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours of history (default: config.chart_hours)",
    )
    parser.add_argument("--webhook", default=WEBHOOK)
    parser.add_argument("--message", default=MESSAGE)
    args = parser.parse_args()

    out_path = Path(args.output).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Use the same python that has matplotlib
    python = sys.executable
    plot_cmd = [
        python, "-m", "mempool_monitor.cli", "chart",
        "--output", str(out_path),
    ]
    if args.hours is not None:
        plot_cmd += ["--hours", str(args.hours)]
    env = dict(os.environ)
    env["PYTHONPATH"] = str(SRC)

    gen = subprocess.run(plot_cmd, capture_output=True, text=True, env=env, cwd=REPO_ROOT)
    if gen.returncode != 0:
        print("chart generation failed", file=sys.stderr)
        print(gen.stderr[-3000:], file=sys.stderr)
        return gen.returncode
    if not out_path.is_file() or out_path.stat().st_size == 0:
        print(f"missing chart: {out_path}", file=sys.stderr)
        return 1

    send = subprocess.run(
        [
            "node", str(DISCORD_CLI), "send",
            "--config", str(CONFIG), "--env", str(ENV),
            "--webhook", args.webhook, "--file", str(out_path), "--silent",
            "--message", args.message,
        ],
        cwd=DISCORD_ROOT, capture_output=True, text=True,
    )
    if send.returncode != 0:
        print(f"send failed: {send.stderr.strip()}", file=sys.stderr)
        return send.returncode

    print(f"delivered: {out_path.name} -> {args.webhook}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
