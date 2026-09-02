"""Matplotlib chart generation for mempool monitoring (dark theme)."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

from .store import SnapshotStore

# Dark theme palette
BG = "#0d1117"
PANEL_BG = "#161b22"
TEXT = "#e6edf3"
GRID = "#21262d"
TX_COLOR = "#58a6ff"      # blue
VSIZE_COLOR = "#f0883e"   # orange
FEE_FAST = "#f6465d"      # red
FEE_HALF = "#f5a623"      # amber
FEE_HOUR = "#26d69c"      # teal

FIG_W, FIG_H, DPI = 12, 8, 110


def _parse_ts(ts: str) -> datetime:
    dt = datetime.fromisoformat(ts)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _fmt_k(x, _pos=None) -> str:
    return f"{x/1000:.0f}k"


def _fmt_mb(x, _pos=None) -> str:
    return f"{x:.0f}MB"


def render_chart(store: SnapshotStore, out_path: str | Path, limit: int = 500) -> Path:
    """Render mempool snapshot history to a dark-theme PNG.

    Returns the output path. Raises if there is not enough data.
    """
    rows = store.recent(limit=limit)
    if not rows:
        raise ValueError("no snapshots to chart — run `mempool-monitor snapshot` first")

    rows = list(reversed(rows))  # chronological
    ts = [_parse_ts(r["ts"]) for r in rows]
    counts = [r["count"] for r in rows]
    vsizes = [r["vsize"] / 1_000_000 for r in rows]
    fast = [r["fastest_fee"] for r in rows]
    half = [r["half_hour_fee"] for r in rows]
    hour = [r["hour_fee"] for r in rows]

    fig, axes = plt.subplots(
        3, 1, figsize=(FIG_W, FIG_H), dpi=DPI, sharex=True,
        gridspec_kw={"height_ratios": [2, 1, 1], "hspace": 0.08},
    )
    fig.patch.set_facecolor(BG)
    for ax in axes:
        ax.set_facecolor(PANEL_BG)
        ax.tick_params(colors=TEXT, labelsize=9)
        ax.grid(True, color=GRID, linewidth=0.6, alpha=0.7)
        for spine in ax.spines.values():
            spine.set_color(GRID)

    # Panel 1: unconfirmed tx count
    ax0 = axes[0]
    ax0.fill_between(ts, counts, color=TX_COLOR, alpha=0.25)
    ax0.plot(ts, counts, color=TX_COLOR, linewidth=1.6)
    ax0.set_ylabel("Unconfirmed TX", color=TEXT, fontsize=10)
    ax0.yaxis.set_major_formatter(FuncFormatter(_fmt_k))
    ax0.set_title(
        "BTC Mempool Monitor",
        color=TEXT, fontsize=13, fontweight="bold", loc="left", pad=10,
    )

    # Panel 2: mempool vsize
    ax1 = axes[1]
    ax1.fill_between(ts, vsizes, color=VSIZE_COLOR, alpha=0.25)
    ax1.plot(ts, vsizes, color=VSIZE_COLOR, linewidth=1.6)
    ax1.set_ylabel("Vsize (MB)", color=TEXT, fontsize=10)
    ax1.yaxis.set_major_formatter(FuncFormatter(_fmt_mb))

    # Panel 3: recommended fees
    ax2 = axes[2]
    ax2.plot(ts, fast, color=FEE_FAST, linewidth=1.6, label="fastest")
    ax2.plot(ts, half, color=FEE_HALF, linewidth=1.4, label="half-hour")
    ax2.plot(ts, hour, color=FEE_HOUR, linewidth=1.4, label="hour")
    ax2.set_ylabel("Fee (sat/vB)", color=TEXT, fontsize=10)
    ax2.set_ylim(bottom=0)
    ax2.legend(
        loc="upper left", fontsize=8, facecolor=PANEL_BG,
        edgecolor=GRID, labelcolor=TEXT,
    )
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M"))
    ax2.set_xlabel("UTC", color=TEXT, fontsize=9)

    # Latest stats annotation
    last = rows[-1]
    note = (
        f"height={last['block_height']}  txs={last['count']:,}  "
        f"vsize={last['vsize']/1_000_000:.1f}MB  "
        f"fastest={last['fastest_fee']} sat/vB  @ {_parse_ts(last['ts']):%Y-%m-%d %H:%M}Z"
    )
    fig.text(0.01, 0.01, note, color=TEXT, fontsize=8.5, alpha=0.85)

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, facecolor=BG, bbox_inches="tight")
    plt.close(fig)
    return out
