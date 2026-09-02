"""Render 30-day BTC block history charts (dark theme)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

BG = "#0d1117"
PANEL = "#161b22"
TEXT = "#c9d1d9"
GRID = "#21262d"
FEE = "#e6194b"
TX = "#f0883e"
SIZE = "#58a6ff"
DIFF = "#3fb950"
PURPLE = "#911eb4"


def load_blocks(db: str | Path, days: int = 30) -> list[tuple]:
    conn = sqlite3.connect(str(Path(db).expanduser()))
    cutoff = None
    if days:
        row = conn.execute(
            "SELECT timestamp FROM block_history ORDER BY height DESC LIMIT 1"
        ).fetchone()
        cutoff = int(row[0]) - days * 86400
    if cutoff:
        rows = conn.execute(
            "SELECT height, timestamp, tx_count, size, weight, difficulty, median_fee, pool_name "
            "FROM block_history WHERE timestamp >= ? ORDER BY height ASC",
            (cutoff,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT height, timestamp, tx_count, size, weight, difficulty, median_fee, pool_name "
            "FROM block_history ORDER BY height ASC"
        ).fetchall()
    conn.close()
    return rows


def render_block_history(db: str | Path, out: str | Path, days: int = 30) -> Path:
    rows = load_blocks(db, days)
    if not rows:
        raise ValueError("no block history rows")

    ts = [datetime.fromtimestamp(r[1], tz=timezone.utc) for r in rows]
    tx = [r[2] for r in rows]
    size = [r[3] / 1_000_000 for r in rows]          # MB
    weight = [r[4] / 1_000_000 for r in rows]        # MWU
    diff = [r[5] / 1e12 for r in rows]               # T
    fee = [r[6] or 0 for r in rows]                  # sat/vB

    # rolling averages (7 blocks ~ 1h)
    def roll(vals: list[float], n: int = 7) -> list[float]:
        arr = np.array(vals, dtype=float)
        kernel = np.ones(n) / n
        return np.convolve(arr, kernel, mode="same").tolist()

    fig, axes = plt.subplots(
        4, 1, figsize=(16, 14), sharex=True,
        gridspec_kw={"hspace": 0.12},
    )
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(PANEL)
        ax.tick_params(colors=TEXT, labelsize=10)
        ax.grid(True, color=GRID, linewidth=0.6, alpha=0.6)
        for spine in ax.spines.values():
            spine.set_color(GRID)

    # Panel 1: median fee
    ax = axes[0]
    ax.plot(ts, fee, color=FEE, linewidth=0.4, alpha=0.4, label="median fee (per block)")
    ax.plot(ts, roll(fee), color=FEE, linewidth=1.6, label="1h avg")
    ax.set_ylabel("median fee (sat/vB)", color=TEXT, fontsize=11)
    ax.legend(loc="upper left", fontsize=9, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)
    ax.set_title(
        f"BTC Block History — last {days} days ({len(rows):,} blocks)",
        color=TEXT, fontsize=14, fontweight="bold", loc="left", pad=8,
    )

    # Panel 2: tx count
    ax = axes[1]
    ax.plot(ts, tx, color=TX, linewidth=0.4, alpha=0.4, label="tx per block")
    ax.plot(ts, roll(tx), color=TX, linewidth=1.6, label="1h avg")
    ax.set_ylabel("tx count", color=TEXT, fontsize=11)
    ax.legend(loc="upper left", fontsize=9, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    # Panel 3: block size
    ax = axes[2]
    ax.fill_between(ts, size, color=SIZE, alpha=0.3)
    ax.plot(ts, size, color=SIZE, linewidth=0.5, alpha=0.6)
    ax.plot(ts, roll(size), color=SIZE, linewidth=1.8, label="size (MB) 1h avg")
    ax.set_ylabel("block size (MB)", color=TEXT, fontsize=11)
    ax.legend(loc="upper left", fontsize=9, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)

    # Panel 4: difficulty
    ax = axes[3]
    ax.plot(ts, diff, color=DIFF, linewidth=1.4, label="difficulty (T)")
    ax.set_ylabel("difficulty (T)", color=TEXT, fontsize=11)
    ax.legend(loc="upper left", fontsize=9, facecolor=PANEL, edgecolor=GRID, labelcolor=TEXT)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d", tz=timezone.utc))

    fig.text(
        0.01, 0.005,
        f"source: mempool.space | {ts[0]:%Y-%m-%d} ~ {ts[-1]:%Y-%m-%d} UTC",
        color=TEXT, fontsize=8.5, alpha=0.7,
    )

    out_path = Path(out).expanduser()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out_path


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "~/.mempool-monitor/block_history.sqlite3"
    out = sys.argv[2] if len(sys.argv) > 2 else "charts/block_history_30d.png"
    p = render_block_history(db, out)
    print(f"saved: {p}")
