#!/usr/bin/env python3
"""Fetch ~1 month of BTC block history from mempool.space and store as JSON.

API: GET /api/v1/blocks/{height} returns up to 15 blocks starting at height
(descending). We page backwards from the current tip until we cover
`target_blocks` blocks or reach a floor height.

Usage:
  python3 fetch_block_history.py [--days 30] [--out PATH] [--sleep S]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

API = "https://mempool.space/api"
BLOCKS_PER_PAGE = 15
TARGET_BLOCKS_PER_DAY = 144  # ~10min blocks


def fetch_block_history(days: int, sleep_s: float = 1.0) -> list[dict]:
    target = days * TARGET_BLOCKS_PER_DAY
    client = httpx.Client(
        timeout=httpx.Timeout(30.0, connect=10.0),
        headers={"User-Agent": "mempool-monitor/0.2"},
        follow_redirects=True,
    )
    blocks: list[dict] = []
    seen: set[int] = set()
    try:
        tip = int(client.get(f"{API}/blocks/tip/height").json())
        print(f"tip height: {tip}, target: {target} blocks ({days}d)", flush=True)
        start = tip - (tip % BLOCKS_PER_PAGE)  # align to page boundary
        floor = tip - target
        height = start
        pages = 0
        attempts = 0
        while height > floor:
            try:
                resp = client.get(f"{API}/v1/blocks/{height}")
                resp.raise_for_status()
                page = resp.json()
            except Exception as exc:  # transient retry
                print(f"  retry @ {height}: {exc}", flush=True)
                time.sleep(5)
                attempts += 1
                if attempts > 10:
                    print(f"  giving up @ {height}", file=sys.stderr, flush=True)
                    break
                continue
            if not page:
                break
            added = 0
            for b in page:
                h = int(b["height"])
                if h in seen:
                    continue
                seen.add(h)
                blocks.append(b)
                added += 1
            pages += 1
            print(
                f"  page {pages}: @{height} +{added} (total {len(blocks)})",
                flush=True,
            )
            if added == 0:
                # no new blocks -> avoid infinite loop
                height -= BLOCKS_PER_PAGE
            else:
                height = min(int(b["height"]) for b in page) - 1
            # floor alignment
            if height < floor:
                break
            time.sleep(sleep_s)
    finally:
        client.close()

    blocks.sort(key=lambda b: int(b["height"]))
    print(f"done: {len(blocks)} blocks fetched", flush=True)
    return blocks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30)
    parser.add_argument("--out", default="data/block_history_30d.json")
    parser.add_argument("--sleep", type=float, default=0.8)
    args = parser.parse_args()

    out = Path(args.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)

    blocks = fetch_block_history(args.days, args.sleep)
    if not blocks:
        print("no blocks fetched", file=sys.stderr)
        return 1

    payload = {
        "fetched_at": int(time.time()),
        "days": args.days,
        "count": len(blocks),
        "first_height": int(blocks[0]["height"]),
        "last_height": int(blocks[-1]["height"]),
        "blocks": blocks,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False))
    print(f"saved: {out} ({out.stat().st_size} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
