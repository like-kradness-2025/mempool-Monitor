#!/usr/bin/env python3
"""Show remaining sub-40MB pre-majority samples with local context.

Prints each remaining low sample with the 3 samples before and after,
so a human (or the agent) can judge whether it is a stale-CDN read or a
real dip.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from datetime import datetime, timezone  # noqa: E402

from mempool_monitor.config import load_config  # noqa: E402
from mempool_monitor.storage import Storage  # noqa: E402

MAJORITY_SHIP_TS = 1788405381


def main() -> int:
    config = load_config()
    storage = Storage(config.database)
    storage.initialize()
    snaps = sorted(storage.snapshots_since(0), key=lambda x: x.collected_at)
    pre = [x for x in snaps if x.collected_at < MAJORITY_SHIP_TS]
    lows = [x for x in pre if x.mempool_vsize / 1e6 < 40]

    idx = {id(x): i for i, x in enumerate(pre)}
    for low in lows:
        i = idx[id(low)]
        ctx = pre[max(0, i - 3):i + 4]
        parts = []
        for c in ctx:
            t = datetime.fromtimestamp(c.collected_at, tz=timezone.utc).strftime("%H:%M:%S")
            marker = " <<<" if c is low else ""
            parts.append(
                f"{t} {c.mempool_vsize/1e6:.1f}MB/h{c.latest_block_height}{marker}"
            )
        print(" | ".join(parts))
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
