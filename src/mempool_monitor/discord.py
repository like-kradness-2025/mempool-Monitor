"""Discord webhook delivery for mempool-monitor."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from .config import Config
from .models import Alert, CongestionLevel, Snapshot

WEBHOOK_PATTERN = re.compile(r"https://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\S+")
COLORS = {
    CongestionLevel.UNKNOWN: 0x808080,
    CongestionLevel.LOW: 0x2ECC71,
    CongestionLevel.MODERATE: 0xF1C40F,
    CongestionLevel.HIGH: 0xE67E22,
    CongestionLevel.EXTREME: 0xE74C3C,
}


def mask_secrets(value: str) -> str:
    return WEBHOOK_PATTERN.sub("[REDACTED_DISCORD_WEBHOOK]", value)


@dataclass(frozen=True)
class DeliveryResult:
    status_code: int
    message_id: str


def build_report_payload(snapshot: Snapshot, chart_name: str) -> dict[str, Any]:
    return {
        "username": "BTC Mempool Monitor",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": "Bitcoin Mempool Report",
                "url": "https://mempool.space/",
                "color": COLORS[snapshot.congestion_level],
                "description": f"Congestion: **{snapshot.congestion_level.name}**",
                "fields": [
                    {
                        "name": "Fees (sat/vB)",
                        "value": (
                            f"Fastest **{snapshot.fastest_fee:g}** | "
                            f"30m {snapshot.half_hour_fee:g} | "
                            f"60m {snapshot.hour_fee:g}\n"
                            f"Economy {snapshot.economy_fee:g} | "
                            f"Minimum {snapshot.minimum_fee:g}"
                        ),
                        "inline": False,
                    },
                    {
                        "name": "Backlog",
                        "value": (
                            f">=5 sat/vB: **{snapshot.backlog_5 / 1_000_000:.1f} blocks**\n"
                            f">=10 sat/vB: {snapshot.backlog_10 / 1_000_000:.1f} blocks"
                        ),
                        "inline": True,
                    },
                    {
                        "name": "Mempool",
                        "value": (
                            f"{snapshot.mempool_count:,} tx\n"
                            f"{snapshot.mempool_vsize / 1_000_000:.1f} vMB"
                        ),
                        "inline": True,
                    },
                    {
                        "name": "Latest block",
                        "value": (
                            f"#{snapshot.latest_block_height:,}\n"
                            f"{snapshot.block_age_seconds // 60} min ago"
                        ),
                        "inline": True,
                    },
                ],
                "image": {"url": f"attachment://{chart_name}"},
                "footer": {
                    "text": (
                        f"{snapshot.provider} | API {snapshot.api_latency_ms:.0f} ms"
                    )
                },
                "timestamp": (
                    __import__("datetime")
                    .datetime.fromtimestamp(
                        snapshot.collected_at, __import__("datetime").timezone.utc
                    )
                    .isoformat()
                ),
            }
        ],
    }


def build_alert_payload(alert: Alert, snapshot: Snapshot) -> dict[str, Any]:
    color = 0x2ECC71 if alert.severity == "RECOVERY" else 0xE74C3C
    return {
        "username": "BTC Mempool Monitor",
        "allowed_mentions": {"parse": []},
        "embeds": [
            {
                "title": alert.title,
                "description": alert.description,
                "color": color,
                "fields": [
                    {
                        "name": "Current",
                        "value": (
                            f"{snapshot.congestion_level.name} | "
                            f"{snapshot.fastest_fee:g} sat/vB | "
                            f"block #{snapshot.latest_block_height:,}"
                        ),
                    }
                ],
                "url": "https://mempool.space/",
            }
        ],
    }


def build_text_report_payload(snapshot: Snapshot) -> dict[str, Any]:
    """Text-only report (no chart image)."""
    price = f" ${snapshot.btc_price_usd:,.0f}" if snapshot.btc_price_usd else ""
    diff = ""
    if snapshot.current_difficulty:
        diff = f" | diff {snapshot.current_difficulty / 1e12:.1f}T"
    return {
        "username": "BTC Mempool Monitor",
        "allowed_mentions": {"parse": []},
        "content": (
            f"**Bitcoin Mempool — {snapshot.congestion_level.name}**\n"
            f"Fee: fastest {snapshot.fastest_fee:g} sat/vB | "
            f"30m {snapshot.half_hour_fee:g} | 60m {snapshot.hour_fee:g} | "
            f"economy {snapshot.economy_fee:g}\n"
            f"Backlog: >=5 sat/vB {snapshot.backlog_5 / 1_000_000:.1f} blocks | "
            f">=10 sat/vB {snapshot.backlog_10 / 1_000_000:.1f} blocks\n"
            f"Mempool: {snapshot.mempool_vsize / 1_000_000:.1f} vMB | "
            f"{snapshot.mempool_count:,} tx | "
            f"block #{snapshot.latest_block_height:,} "
            f"(age {snapshot.block_age_seconds}s){price}{diff}"
        ),
    }


class DiscordWebhook:
    def __init__(self, config: Config, transport: httpx.BaseTransport | None = None):
        if not config.webhook_url:
            raise ValueError("DISCORD_WEBHOOK_URL is not configured")
        if not WEBHOOK_PATTERN.fullmatch(config.webhook_url):
            raise ValueError("DISCORD_WEBHOOK_URL is not a valid Discord webhook URL")
        self.url = config.webhook_url
        self.username = config.discord_username
        self.client = httpx.Client(timeout=config.timeout_seconds, transport=transport)

    def close(self) -> None:
        self.client.close()

    def __enter__(self) -> "DiscordWebhook":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _post(self, **kwargs: Any) -> DeliveryResult:
        response = self.client.post(f"{self.url}?wait=true", **kwargs)
        response.raise_for_status()
        body = response.json()
        message_id = body.get("id")
        if not isinstance(message_id, str) or not message_id:
            raise RuntimeError("Discord did not return a message id")
        return DeliveryResult(response.status_code, message_id)

    def send_report(self, snapshot: Snapshot, chart_path: Path) -> DeliveryResult:
        payload = build_report_payload(snapshot, chart_path.name)
        payload["username"] = self.username
        with chart_path.open("rb") as image:
            return self._post(
                data={"payload_json": json.dumps(payload)},
                files={"files[0]": (chart_path.name, image, "image/png")},
            )

    def send_text_report(self, snapshot: Snapshot) -> DeliveryResult:
        """Send a text-only report (no chart image)."""
        payload = build_text_report_payload(snapshot)
        payload["username"] = self.username
        return self._post(json=payload)

    def send_alert(self, alert: Alert, snapshot: Snapshot) -> DeliveryResult:
        payload = build_alert_payload(alert, snapshot)
        payload["username"] = self.username
        return self._post(json=payload)
