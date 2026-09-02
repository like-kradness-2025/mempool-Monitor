"""6-panel dark-theme mempool dashboard chart (restored original)."""

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
    """Drop display spikes: vsize deviates >10% from BOTH neighbours while
    the neighbours agree within 6% (isolated CDN glitch, not a real move).
    """
    ordered = sorted(snapshots, key=lambda s: s.collected_at)
    if len(ordered) < 3:
        return ordered
    kept: list[Snapshot] = []
    for i, s in enumerate(ordered):
        if 0 < i < len(ordered) - 1:
            prev_v = ordered[i - 1].mempool_vsize
            next_v = ordered[i + 1].mempool_vsize
            cur_v = s.mempool_vsize
            if prev_v > 0 and next_v > 0:
                nbr_agree = abs(prev_v - next_v) / max(prev_v, next_v) < 0.06
                dev_prev = abs(cur_v - prev_v) / prev_v
                dev_next = abs(cur_v - next_v) / next_v
                if nbr_agree and dev_prev > 0.10 and dev_next > 0.10:
                    continue  # drop isolated spike
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

    # ── remove isolated stale-CDN spikes for display ────────────────────
    # A snapshot whose vsize deviates >18% from both neighbours (which agree
    # with each other) is a CDN glitch, not a real mempool move.  Filter for
    # display only; the DB keeps the raw reading.
    snapshots = _remove_isolated_spikes(snapshots)

    # ── prepare data ────────────────────────────────────────────────────
    snapshots = sorted(snapshots, key=lambda s: s.collected_at)
    times = [datetime.fromtimestamp(s.collected_at, tz=JST) for s in snapshots]

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

    prices = [s.btc_price_usd for s in snapshots]
    if any(p is not None for p in prices):
        _plot_nonans(ax, times, prices, color="#f7931a", linewidth=1.5, marker="o", markersize=2, label="BTC/USD")
        ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9")

    # ── Panel 2: Mempool size ───────────────────────────────────────────
    ax = axes[1]
    ax.set_title("■ Mempool size (vMB + tx count)", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("vMB / tx count", fontsize=12)

    color_vmb = "#58a6ff"
    color_tx = "#f0883e"

    vsizes = [s.mempool_vsize / 1_000_000 for s in snapshots]  # bytes → vMB
    counts = [s.mempool_count for s in snapshots]

    l1 = ax.plot(times, vsizes, color=color_vmb, linewidth=1.5, label="Size (vMB)")
    ax.tick_params(axis="y", labelsize=11)

    ax2 = ax.twinx()
    ax2.set_ylabel("tx count", fontsize=12, color=color_tx)
    ax2.tick_params(axis="y", labelsize=11, colors=color_tx)
    l2 = ax2.plot(times, counts, color=color_tx, linewidth=1.5, label="Tx count", alpha=0.85)

    lns = l1 + l2
    labs = [l.get_label() for l in lns]
    ax.legend(lns, labs, loc="upper left", fontsize=10, labelcolor="#c9d1d9")

    # ── Panel 3: Recommended fees ───────────────────────────────────────
    ax = axes[2]
    ax.set_title("● Recommended fees", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("sat/vB", fontsize=12)

    fees = {
        "Fastest": [s.fastest_fee for s in snapshots],
        "30 min": [s.half_hour_fee for s in snapshots],
        "1 hour": [s.hour_fee for s in snapshots],
        "Economy": [s.economy_fee for s in snapshots],
        "Minimum": [s.minimum_fee for s in snapshots],
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
    band_1_2 = [(s.backlog_1 - s.backlog_2) / 1_000_000 if s.backlog_2 else s.backlog_1 / 1_000_000 for s in snapshots]
    band_2_5 = [(s.backlog_2 - s.backlog_5) / 1_000_000 if s.backlog_5 else s.backlog_2 / 1_000_000 for s in snapshots]
    band_5_10 = [(s.backlog_5 - s.backlog_10) / 1_000_000 if s.backlog_10 else s.backlog_5 / 1_000_000 for s in snapshots]
    band_10_20 = [(s.backlog_10 - s.backlog_20) / 1_000_000 if s.backlog_20 else s.backlog_10 / 1_000_000 for s in snapshots]
    band_20_50 = [(s.backlog_20 - s.backlog_50) / 1_000_000 if s.backlog_50 else s.backlog_20 / 1_000_000 for s in snapshots]
    band_50 = [s.backlog_50 / 1_000_000 for s in snapshots]

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

    diffs = [s.current_difficulty / 1e12 if s.current_difficulty else None for s in snapshots]
    _plot_nonans(ax, times, diffs, color="#58a6ff", linewidth=1.5, label="Difficulty (T)")

    ax3 = ax.twinx()
    ax3.set_ylabel("Hashrate (EH/s)", fontsize=12, color="#f0883e")
    ax3.tick_params(axis="y", labelsize=11, colors="#f0883e")

    hr_eh = [s.current_hashrate / 1e18 if s.current_hashrate else None for s in snapshots]
    _plot_nonans(ax3, times, hr_eh, color="#f0883e", linewidth=1.5, label="Hashrate", alpha=0.85, linestyle="--")

    # ── Panel 6: Block timing ───────────────────────────────────────────
    ax = axes[5]
    ax.set_title("▶ Block timing", fontsize=16, fontweight="bold", pad=6)
    ax.set_ylabel("seconds", fontsize=12)

    block_ages = [s.block_age_seconds / 60 for s in snapshots]
    avg_intervals = [s.avg_block_interval_seconds / 60 for s in snapshots]

    _plot_nonans(ax, times, block_ages, color="#da3633", linewidth=1.5, label="Block age", alpha=0.7)
    _plot_nonans(ax, times, avg_intervals, color="#3fb950", linewidth=1.5, label="Avg interval")

    ax.axhline(y=10, color="#3fb950", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(y=30, color="#f0883e", linestyle="--", linewidth=1, alpha=0.7)
    ax.axhline(y=60, color="#da3633", linestyle="--", linewidth=1, alpha=0.7)
    ax.legend(loc="upper left", fontsize=10, labelcolor="#c9d1d9")
    ax.set_ylabel("minutes", fontsize=12)
    # ── finalise ────────────────────────────────────────────────────────
    latest = snapshots[-1]
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
