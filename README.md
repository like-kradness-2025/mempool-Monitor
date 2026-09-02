# mempool-monitor

Bitcoin mempool monitoring & analysis — a **zero-dependency** Python CLI
(stdlib only) backed by the [mempool.space API](https://mempool.space/docs/api).

## Features

- `snapshot` — fetch current mempool stats (tx count, vsize, fees) and persist to SQLite
- `monitor` — poll continuously at a fixed interval, storing each snapshot
- `latest` / `history` — read back stored snapshots (text or JSON)
- SQLite storage with no external dependencies (`sqlite3`, `urllib` only)

## Install

```bash
cd ~/Tool/mempool-Monitor
pip install -e .
```

## Usage

```bash
# One-off snapshot
mempool-monitor snapshot

# Continuous monitoring every 60s (Ctrl+C to stop)
mempool-monitor monitor --interval 60

# Read back
mempool-monitor latest
mempool-monitor history --limit 24 --json
```

All commands accept `--db <path>` to override the default
(`~/.mempool-monitor/mempool.db`).

## Example output

```
[2026-09-02T01:00:00+00:00] height=965104 txs=86,502 vsize=43.0 MB fastest=2 sat/vB
```

## Data model

`snapshots` table:

| column | type | meaning |
|---|---|---|
| ts | TEXT (PK) | ISO8601 UTC timestamp |
| block_height | INTEGER | current chain tip |
| count | INTEGER | unconfirmed tx count |
| vsize | INTEGER | total mempool virtual size (vbytes) |
| total_fee | INTEGER | total fee (sat) |
| fastest_fee | INTEGER | fastest fee estimate (sat/vB) |
| half_hour_fee | INTEGER | 30-min estimate (sat/vB) |
| hour_fee | INTEGER | 1-hour estimate (sat/vB) |
| economy_fee | INTEGER | economy estimate (sat/vB) |
| minimum_fee | INTEGER | minimum relay fee (sat/vB) |

## Roadmap (small → big)

- [x] Snapshot + history storage
- [ ] Fee histogram percentiles (p25/p50/p75/p90)
- [ ] Mempool growth / block-clear tracking
- [ ] Discord alert integration
- [ ] Charts (matplotlib, opt-in)

## License

MIT
