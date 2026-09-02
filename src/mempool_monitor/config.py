"""Configuration for mempool-monitor (TOML + env)."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    tomllib = None  # type: ignore[assignment]


@dataclass(frozen=True)
class Config:
    # API
    base_url: str = "https://mempool.space"
    timeout_seconds: float = 10.0
    max_retries: int = 3

    # Monitor
    provider: str = "mempool.space"
    retention_days: int = 90
    report_interval_minutes: int = 60
    chart_hours: int = 24
    alert_cooldown_minutes: int = 30
    stale_after_minutes: int = 15

    # Thresholds
    moderate_fee: float = 5.0
    high_fee: float = 20.0
    extreme_fee: float = 50.0
    moderate_backlog_blocks: float = 2.0
    high_backlog_blocks: float = 3.0
    extreme_backlog_blocks: float = 5.0

    # Paths
    database: Path = Path("~/.mempool-monitor/mempool.sqlite3")
    extra_database: Path = Path("~/.mempool-monitor/mempool-extra.sqlite3")
    output_dir: Path = Path("~/.mempool-monitor/charts")
    log_dir: Path = Path("~/.mempool-monitor/logs")

    # Discord
    discord_username: str = "BTC Mempool Monitor"
    webhook_url: str | None = None

    def expanded(self) -> "Config":
        """Resolve ~ in all path fields."""
        return replace(
            self,
            database=self.database.expanduser(),
            extra_database=self.extra_database.expanduser(),
            output_dir=self.output_dir.expanduser(),
            log_dir=self.log_dir.expanduser(),
        )


def _section(data: dict[str, Any], name: str) -> dict[str, Any]:
    value = data.get(name, {})
    if not isinstance(value, dict):
        raise ValueError(f"Config section [{name}] must be a table")
    return value


def load_config(path: str | Path | None = None) -> Config:
    """Load config from TOML file (arg > env > default) + env overrides."""
    config = Config()

    resolved = path or os.environ.get("MEMPOOL_MONITOR_CONFIG")
    if resolved:
        if tomllib is None:
            raise RuntimeError("tomllib is unavailable on this Python version")
        with open(resolved, "rb") as fh:
            data = tomllib.load(fh)

        api = _section(data, "api")
        monitor = _section(data, "monitor")
        thresholds = _section(data, "thresholds")
        paths = _section(data, "paths")
        discord = _section(data, "discord")

        config = replace(
            config,
            base_url=str(api.get("base_url", config.base_url)),
            timeout_seconds=float(api.get("timeout_seconds", config.timeout_seconds)),
            max_retries=int(api.get("max_retries", config.max_retries)),
            provider=str(monitor.get("provider", config.provider)),
            retention_days=int(monitor.get("retention_days", config.retention_days)),
            report_interval_minutes=int(
                monitor.get("report_interval_minutes", config.report_interval_minutes)
            ),
            chart_hours=int(monitor.get("chart_hours", config.chart_hours)),
            alert_cooldown_minutes=int(
                monitor.get("alert_cooldown_minutes", config.alert_cooldown_minutes)
            ),
            stale_after_minutes=int(
                monitor.get("stale_after_minutes", config.stale_after_minutes)
            ),
            moderate_fee=float(thresholds.get("moderate_fee", config.moderate_fee)),
            high_fee=float(thresholds.get("high_fee", config.high_fee)),
            extreme_fee=float(thresholds.get("extreme_fee", config.extreme_fee)),
            moderate_backlog_blocks=float(
                thresholds.get("moderate_backlog_blocks", config.moderate_backlog_blocks)
            ),
            high_backlog_blocks=float(
                thresholds.get("high_backlog_blocks", config.high_backlog_blocks)
            ),
            extreme_backlog_blocks=float(
                thresholds.get("extreme_backlog_blocks", config.extreme_backlog_blocks)
            ),
            database=Path(str(paths.get("database", config.database))),
            extra_database=Path(str(paths.get("extra_database", config.extra_database))),
            output_dir=Path(str(paths.get("output_dir", config.output_dir))),
            log_dir=Path(str(paths.get("log_dir", config.log_dir))),
            discord_username=str(
                discord.get("username", config.discord_username)
            ),
            webhook_url=str(discord["webhook_url"]) if "webhook_url" in discord else None,
        )

    # Validation
    if config.max_retries < 1:
        raise ValueError("max_retries must be at least 1")
    if config.timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    # Env overrides
    env_webhook = os.environ.get("DISCORD_WEBHOOK_URL")
    if env_webhook:
        config = replace(config, webhook_url=env_webhook)

    return config.expanded()
