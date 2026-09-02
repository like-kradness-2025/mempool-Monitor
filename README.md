# mempool-monitor

Bitcoin mempool monitoring & analysis — backed by the
[mempool.space API](https://mempool.space/docs/api).

> このリポジトリは、以前作った **6パネル「Mempool Monitor — Live Dashboard」** を
> セッション履歴から完全復元したものです。

## Features

- **6-panel dark-theme dashboard chart**
  1. ◆ BTC Price (CoinGecko)
  2. ■ Mempool size (vMB + tx count)
  3. ● Recommended fees (5 levels)
  4. ▲ Fee-band backlog (stackplot: 1-2 / 2-5 / 5-10 / 10-20 / 20-50 / >=50)
  5. ★ Difficulty & Hashrate
  6. ▶ Block timing (age / avg interval, 10/30/60min guides)
- Snapshot collection → SQLite (mempool, fees, backlog, blocks, mining, difficulty)
- Alert evaluation (congestion, block delay, fee surge)
- Discord webhook delivery (report / alerts)
- `collect` (single/loop) / `run` / `chart` / `status` / `prune` CLI

## Install

```bash
cd ~/Tool/mempool-Monitor
pip install -e .
```

Dependencies: `httpx`, `matplotlib`, `numpy` (+ Pillow for verification).

## Usage

```bash
# One-off collection (stores a snapshot)
mempool-monitor collect

# Continuous collection at 60s
mempool-monitor collect --loop --interval 60

# Render 6-panel dashboard chart (last 24h by default)
mempool-monitor chart --output /tmp/mempool.png

# Show status / statistics
mempool-monitor status
```

Configuration via TOML (`--config path.toml`, or `MEMPOOL_MONITOR_CONFIG` env):

```toml
[api]
base_url = "https://mempool.space"
timeout_seconds = 10
max_retries = 3

[monitor]
chart_hours = 24
report_interval_minutes = 60

[thresholds]
moderate_fee = 5.0
high_fee = 20.0
extreme_fee = 50.0
moderate_backlog_blocks = 2.0
high_backlog_blocks = 3.0
extreme_backlog_blocks = 5.0

[paths]
database = "~/.mempool-monitor/mempool.sqlite3"
output_dir = "~/.mempool-monitor/charts"
log_dir = "~/.mempool-monitor/logs"

[discord]
username = "BTC Mempool Monitor"
# webhook_url = "https://discord.com/api/webhooks/..."
```

`DISCORD_WEBHOOK_URL` env overrides the webhook.

## Data model

| table | purpose |
|---|---|
| `snapshots` | per-collection mempool snapshot (fees, count, vsize, backlogs, blocks) |
| `projected_blocks` | next 6 blocks estimated from mempool-blocks API |
| `difficulty` | difficulty adjustment progress |
| `mining` | current difficulty + hashrate |
| `delivery_log` | Discord delivery attempts |
| `runtime_state` | key/value runtime flags (consecutive failures etc.) |

## Roadmap

- [x] 6-panel dashboard (restored)
- [x] Alert evaluation + Discord delivery
- [ ] Fee histogram percentiles
- [ ] Block-clear tracking

## License

MIT
