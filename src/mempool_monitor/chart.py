"""6-panel dark-theme mempool dashboard chart (restored original)."""

import math
from dataclasses import replace

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .models import Snapshot

JST = ZoneInfo("Asia/Tokyo")

# ── helper ──────────────────────────────────────────────────────────────


def _remove_isolated_spikes(snapshots: list[Snapshot]) -> list[Snapshot]:
    """Return *snapshots* with isolated vsize spikes NaN-masked (display only).

    1-minute mempool size data shows sharp V-dips from two sources:
      1. CDN glitches (stale /api/mempool responses, no block mined)
      2. Block-confirmation dips (a block is mined, mempool drains, then
         new txs refill within a minute or two)

    Both are visually identical spikes at this resolution and neither
    represents a sustained trend, so we NaN the vsize of any sample that
    is more than 7% away from the local rolling median (window 11).
    Longer drains (many consecutive low samples) shift the median and
    survive.

    The snapshot OBJECT is always kept so its other fields (price, fees,
    block timing, ...) still plot on their panels; only ``mempool_vsize``
    is replaced with ``NaN`` for the size panel, which ``_plot_nonans``
    skips.  The newest (live) reading is never masked.  This function
    never returns fewer objects than it is given.
    """
    ordered = sorted(snapshots, key=lambda s: s.collected_at)
    n = len(ordered)
    if n < 5:
        return ordered

    vsizes = [s.mempool_vsize for s in ordered]
    kept: list[Snapshot] = []
    half = 5  # window 11 -> 5 each side
    for i, s in enumerate(ordered):
        if i == n - 1:
            # The newest reading is live — keep it as-is.
            kept.append(s)
            continue
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        window = sorted(vsizes[lo:i] + vsizes[i + 1:hi])
        if not window:
            kept.append(s)
            continue
        med = window[len(window) // 2]
        if med > 0 and abs(s.mempool_vsize - med) / med > 0.07:
            # Deviates >7% from local baseline -> mask vsize only; keep
            # the object (and every other field) intact.
            kept.append(replace(s, mempool_vsize=float("nan")))
        else:
            kept.append(s)
    return kept


def _plot_nonans(axis, times, values, **kwargs):
    """Plot values on *axis*, skipping any None/NaN entries."""
    arr = np.array([v if v is not None else np.nan for v in values])
    mask = ~np.isnan(arr)
    if mask.any():
        times_arr = np.array(times)
        axis.plot(times_arr[mask], arr[mask], **kwargs)


# ── public API ──────────────────────────────────────────────────────────


def generate_chart(snapshots: list[Snapshot], output: str | Path) -> Path:
    """Render a 6-panel dark-theme chart from *snapshots*.

    Parameters
    ----------
    snapshots : list[Snapshot]
        Chronological list of mempool snapshots (must not be empty).
    output : str or Path
        Destination file path (e.g. ``chart.png``).

    Returns
    -------
    Path
        Absolute path of the generated image.

    Raises
    ------
    ValueError
        If *snapshots* is empty.
    """
    if not snapshots:
        raise ValueError("at least one snapshot is required to generate a chart")

    # ── NaN-mask isolated stale-CDN vsize spikes for display ───────────
    # Only the mempool-size series is masked; the other 5 panels keep the
    # raw snapshots (all fields) so a vsize dip never erases price/fees/
    # block-time points.  The DB keeps the raw reading either way.
    raw_snapshots = sorted(snapshots, key=lambda s: s.collected_at)
    filtered = _remove_isolated_spikes(raw_snapshots)
    times = [datetime.fromtimestamp(s.collected_at, tz=JST) for s in raw_snapshots]

    # ── figure setup ────────────────────────────────────────────────────
    plt.rcParams.update(
        {
            "figure.facecolor": "#0d1117",
            "axes.facecolor": "#161b22",
            "axes.edgecolor": "#30363d",
            "axes.labelcolor": "#c9d1d9",
            "axes.titlecolor": "#e6edf3",
            "xtick.color": "#8b949e",
            "ytick.color": "#8b949e",
            "text.color": "#c9d1d9",
            "legend.facecolor": "#1c2128",
            "legend.edgecolor": "#30363d",
            "legend.labelcolor": "#c9d1d9",
            "grid.color": "#21262d",
            "grid.alpha": 0.5,
        }
    )

    fig, axes = plt.subplots(
        nrows=6,
        ncols=1,
        figsize=(16, 18),
        sharex=True,
        constrained_layout=True,
    )

    # ── shared x-axis formatting ────────────────────────────────────────
    for ax in axes:
        ax.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%M", tz=JST))
        ax.xaxis.set_major_locator(mdates.AutoDateLocator())
        ax.tick_params(axis="x", labelsize=11)
        ax.tick_params(axis="y", labelsize=11)
        ax.grid(True, linestyle="--", alpha=0.4)

    # ── Panel 1: BTC Price ──────────────────────────────────────────────
    ax = axes[0]
    ax.set_title("◆ BTC Price", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("USD", fontsize=12)

    prices = [s.btc_price_usd for s in raw_snapshots]
    if any(p is not None for p in prices):
        _plot_nonans(ax, times, prices, color="#f7931a", linewidth=1.5, marker="o", markersize=2, label="BTC/USD")
        ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9")

    # ── Panel 2: Mempool size ───────────────────────────────────────────
    ax = axes[1]
    ax.set_title("■ Mempool size (vMB + tx count)", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("vMB / tx count", fontsize=12)

    color_vmb = "#58a6ff"
    color_tx = "#f0883e"

    # vsize comes from the NaN-masked series (spikes hidden, newest kept);
    # count uses the raw snapshots.  _plot_nonans skips the NaN entries.
    vsizes = [s.mempool_vsize / 1_000_000 for s in filtered]  # bytes → vMB
    counts = [s.mempool_count for s in raw_snapshots]

    _plot_nonans(ax, times, vsizes, color=color_vmb, linewidth=1.5, label="Size (vMB)")
    ax.tick_params(axis="y", labelsize=11)

    ax2 = ax.twinx()
    ax2.set_ylabel("tx count", fontsize=12, color=color_tx)
    ax2.tick_params(axis="y", labelsize=11, colors=color_tx)
    _plot_nonans(ax2, times, counts, color=color_tx, linewidth=1.5, label="Tx count", alpha=0.85)

    l1 = ax.get_lines()[0] if ax.lines else ax.plot([], [])[0]
    l2 = ax2.get_lines()[0] if ax2.lines else ax2.plot([], [])[0]
    lns = [l1, l2]
    labs = [l.get_label() for l in lns]
    ax.legend(lns, labs, loc="upper left", fontsize=10, labelcolor="#c9d1d9")

    # ── Panel 3: Recommended fees ───────────────────────────────────────
    ax = axes[2]
    ax.set_title("● Recommended fees", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("sat/vB", fontsize=12)

    fees = {
        "Fastest": [s.fastest_fee for s in raw_snapshots],
        "30 min": [s.half_hour_fee for s in raw_snapshots],
        "1 hour": [s.hour_fee for s in raw_snapshots],
        "Economy": [s.economy_fee for s in raw_snapshots],
        "Minimum": [s.minimum_fee for s in raw_snapshots],
    }
    fee_colors = ["#e6194b", "#f58231", "#ffe119", "#3cb44b", "#4363d8"]
    for (label, values), color in zip(fees.items(), fee_colors):
        _plot_nonans(ax, times, values, color=color, linewidth=1.2, label=label)

    ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9")

    # ── Panel 4: Fee-band backlog (stackplot) ───────────────────────────
    ax = axes[3]
    ax.set_title("▲ Fee-band backlog", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("estimated blocks", fontsize=12)

    # Calculate differential fee bands (cumulative → non-overlapping)
    band_1_2 = [(s.backlog_1 - s.backlog_2) / 1_000_000 if s.backlog_2 else s.backlog_1 / 1_000_000 for s in raw_snapshots]
    band_2_5 = [(s.backlog_2 - s.backlog_5) / 1_000_000 if s.backlog_5 else s.backlog_2 / 1_000_000 for s in raw_snapshots]
    band_5_10 = [(s.backlog_5 - s.backlog_10) / 1_000_000 if s.backlog_10 else s.backlog_5 / 1_000_000 for s in raw_snapshots]
    band_10_20 = [(s.backlog_10 - s.backlog_20) / 1_000_000 if s.backlog_20 else s.backlog_10 / 1_000_000 for s in raw_snapshots]
    band_20_50 = [(s.backlog_20 - s.backlog_50) / 1_000_000 if s.backlog_50 else s.backlog_20 / 1_000_000 for s in raw_snapshots]
    band_50 = [s.backlog_50 / 1_000_000 for s in raw_snapshots]

    band_labels = ["1-2", "2-5", "5-10", "10-20", "20-50", ">=50"]
    backlog_colors = ["#e6194b", "#3cb44b", "#ffe119", "#4363d8", "#f58231", "#911eb4"]
    ax.stackplot(times, band_1_2, band_2_5, band_5_10, band_10_20, band_20_50, band_50,
                 labels=band_labels, colors=backlog_colors, alpha=0.85)

    ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9", ncol=2)

    # ── Panel 5: Difficulty & Hashrate ──────────────────────────────────
    ax = axes[4]
    ax.set_title("★ Difficulty & Hashrate", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("Difficulty (T)", fontsize=12, color="#58a6ff")
    ax.tick_params(axis="y", labelsize=11, colors="#58a6ff")

    diffs = [s.current_difficulty / 1e12 if s.current_difficulty else None for s in raw_snapshots]
    _plot_nonans(ax, times, diffs, color="#58a6ff", linewidth=1.5, label="Difficulty (T)")

    ax3 = ax.twinx()
    ax3.set_ylabel("Hashrate (EH/s)", fontsize=12, color="#f0883e")
    ax3.tick_params(axis="y", labelsize=11, colors="#f0883e")

    hr_eh = [s.current_hashrate / 1e18 if s.current_hashrate else None for s in raw_snapshots]
    _plot_nonans(ax3, times, hr_eh, color="#f0883e", linewidth=1.5, label="Hashrate", alpha=0.85, linestyle="--")

    # ── Panel 6: Block timing ───────────────────────────────────────────
    ax = axes[5]
    ax.set_title("▶ Block timing", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("seconds", fontsize=12)

    block_ages = [s.block_age_seconds / 60 for s in raw_snapshots]
    avg_intervals = [s.avg_block_interval_seconds / 60 for s in raw_snapshots]

    _plot_nonans(ax, times, block_ages, color="#da3633", linewidth=1.5, label="Block age", alpha=0.7)
    _plot_nonans(ax, times, avg_intervals, color="#3fb950", linewidth=1.5, label="Avg interval")

    ax.axhline(y=10, color="#3fb950", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(y=30, color="#f0883e", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(y=60, color="#da3633", linestyle="--", linewidth=1, alpha=0.7)
    ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9")
    ax.set_ylabel("minutes", fontsize=12)
    # ── finalise ────────────────────────────────────────────────────────
    latest = raw_snapshots[-1]
    price_str = f" ${latest.btc_price_usd:,.0f} |" if latest.btc_price_usd else " "
    diff_str = ""
    if latest.current_difficulty:
        diff_str = f" diff {latest.current_difficulty / 1e12:.1f}T |"
    fig.suptitle(
        f"Bitcoin Mempool | {latest.congestion_level.name} |"
        f" Fastest {latest.fastest_fee:g} sat/vB |"
        f" >=5 sat/vB {latest.backlog_5 / 1_000_000:.1f} blocks |"
        f"{price_str}{diff_str} {times[-1]:%Y-%m-%d %H:%M JST}",
        fontsize=16, fontweight="bold", y=0.98,
    )

    output_path = Path(output).resolve()
    fig.savefig(str(output_path), dpi=150, bbox_inches="tight", facecolor="#0d1117")
    plt.close(fig)

    return output_path
