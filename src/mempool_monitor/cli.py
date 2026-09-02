"""CLI entry point for mempool-monitor (restored original)."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import logging
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

import httpx

from .alerts import evaluate_alerts
from .collector import CollectionError, MempoolClient, collect_loop_all
from .config import Config, load_config
from .discord import DiscordWebhook, mask_secrets
from .models import Alert, Snapshot
from .processor import DataValidationError, process_collection
from .storage import Storage


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(
                record.created, timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging(config: Config, verbose: bool = False) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    formatter = JsonFormatter()
    file_handler = TimedRotatingFileHandler(
        config.log_dir / "monitor.log", when="midnight", backupCount=14
    )
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)
    root.handlers[:] = [file_handler, stream_handler]


def send_report(config: Config, storage: Storage, force: bool = False) -> bool:
    latest = storage.latest_snapshot()
    if latest is None:
        logging.warning("report skipped: no snapshots")
        return False
    if not config.webhook_url:
        logging.info("report skipped: DISCORD_WEBHOOK_URL is not configured")
        return False
    if not force:
        delivered = storage.latest_delivery("regular_report")
        if delivered and time.time() - delivered < config.report_interval_minutes * 60:
            return False
    # Chart renderer removed; send text-only report
    try:
        with DiscordWebhook(config) as webhook:
            result = webhook.send_text_report(latest)
        storage.record_delivery(
            "regular_report",
            "success",
            result.status_code,
            discord_message_id=result.message_id,
        )
        logging.info("Discord report sent: message_id=%s", result.message_id)
        return True
    except (httpx.HTTPError, RuntimeError, ValueError) as exc:
        error = mask_secrets(str(exc))[:500]
        storage.record_delivery("regular_report", "failed", error_summary=error)
        logging.error("Discord report failed: %s", error)
        return False


def send_alerts(
    config: Config, storage: Storage, alerts: list[Alert], snapshot: Snapshot
) -> None:
    if not alerts or not config.webhook_url:
        return
    try:
        with DiscordWebhook(config) as webhook:
            for alert in alerts:
                message_type = f"alert:{alert.key}"
                last_sent = storage.latest_delivery(message_type)
                if (
                    last_sent
                    and time.time() - last_sent < config.alert_cooldown_minutes * 60
                    and alert.severity != "RECOVERY"
                ):
                    continue
                try:
                    result = webhook.send_alert(alert, snapshot)
                    storage.record_delivery(
                        message_type,
                        "success",
                        result.status_code,
                        discord_message_id=result.message_id,
                    )
                except (httpx.HTTPError, RuntimeError, ValueError) as exc:
                    error = mask_secrets(str(exc))[:500]
                    storage.record_delivery(
                        message_type, "failed", error_summary=error
                    )
                    logging.error("Discord alert failed: %s", error)
    except ValueError as exc:
        logging.error("Discord alert setup failed: %s", mask_secrets(str(exc)))


def collect_once(config: Config, storage: Storage, notify: bool) -> int:
    previous = storage.latest_snapshot()
    try:
        with MempoolClient(config) as client:
            raw = client.collect()
        snapshot, projected = process_collection(raw, config)
        storage.insert_snapshot(snapshot, projected)
    except (CollectionError, DataValidationError) as exc:
        failures = int(storage.get_state("api_consecutive_failures", "0")) + 1
        storage.set_state("api_consecutive_failures", str(failures))
        logging.error("collection failed (%d consecutive): %s", failures, exc)
        if notify and failures == 3 and previous:
            send_alerts(
                config,
                storage,
                [
                    Alert(
                        key="api_failure",
                        severity="CRITICAL",
                        title="mempool API取得が3回連続失敗",
                        description="最新データは更新されていません",
                    )
                ],
                previous,
            )
        return 1

    prior_failures = int(storage.get_state("api_consecutive_failures", "0"))
    storage.set_state("api_consecutive_failures", "0")
    fifteen_minutes_ago = storage.snapshot_at_or_before(snapshot.collected_at - 900)
    alerts = evaluate_alerts(snapshot, previous, fifteen_minutes_ago)
    if prior_failures >= 3:
        alerts.append(
            Alert(
                key="api_recovery",
                severity="RECOVERY",
                title="mempool API取得が復旧",
                description=f"{prior_failures}回の連続失敗後に正常取得",
            )
        )
    logging.info(
        "collected level=%s fee=%g mempool_vmb=%.1f block=%d",
        snapshot.congestion_level.name,
        snapshot.fastest_fee,
        snapshot.mempool_vsize / 1_000_000,
        snapshot.latest_block_height,
    )
    if notify:
        send_alerts(config, storage, alerts, snapshot)
        send_report(config, storage)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bitcoin mempool monitor")
    parser.add_argument("--config", help="TOML config path")
    parser.add_argument("--verbose", action="store_true")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("init-db")
    collect_parser = subparsers.add_parser("collect")
    collect_parser.add_argument(
        "--loop", action="store_true",
        help="Run continuously at 1-minute intervals",
    )
    collect_parser.add_argument(
        "--interval", type=int, default=60,
        help="Loop interval in seconds (default: 60)",
    )
    collect_parser.add_argument(
        "--max-iterations", type=int, default=None,
        help="Max iterations in loop mode",
    )
    subparsers.add_parser("run")
    chart_parser = subparsers.add_parser("chart")
    chart_parser.add_argument("--output")
    chart_parser.add_argument(
        "--hours", type=int, default=None,
        help="Hours of history to include (default: config.chart_hours)",
    )
    report_parser = subparsers.add_parser("report")
    report_parser.add_argument("--send", action="store_true")
    subparsers.add_parser("status")
    subparsers.add_parser("prune")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.config)
        configure_logging(config, args.verbose)
        with Storage(config.database) as storage:
            storage.initialize()
            if args.command == "init-db":
                print(config.database)
                return 0
            if args.command == "collect":
                if args.loop:
                    count = collect_loop_all(
                        config, storage,
                        interval=args.interval,
                        max_iterations=args.max_iterations,
                    )
                    print(f"Collect loop done: {count} snapshots")
                else:
                    return collect_once(config, storage, notify=False)
                return 0
            if args.command == "run":
                return collect_once(config, storage, notify=True)
            if args.command == "chart":
                from .chart import generate_chart  # noqa: PLC0415

                now = int(time.time())
                hours = args.hours or config.chart_hours
                since = now - hours * 3600
                snapshots = storage.snapshots_since(since)
                if not snapshots:
                    print("No snapshots in range; collecting one first …", file=sys.stderr)
                    ec = collect_once(config, storage, notify=False)
                    if ec:
                        print("Collection failed, cannot generate chart", file=sys.stderr)
                        return 1
                    snapshots = storage.snapshots_since(since)
                output_path = args.output or str(
                    config.output_dir / f"mempool-chart-{now}.png"
                )
                result = generate_chart(snapshots, output_path)
                print(f"Chart saved: {result}")
                return 0
            if args.command == "report":
                if not args.send:
                    print("Refusing live send without --send", file=sys.stderr)
                    return 2
                return 0 if send_report(config, storage, force=True) else 1
            if args.command == "status":
                latest = storage.latest_snapshot()
                payload = storage.statistics()
                payload["latest"] = latest.as_dict() if latest else None
                payload["api_consecutive_failures"] = int(
                    storage.get_state("api_consecutive_failures", "0")
                )
                print(json.dumps(payload, ensure_ascii=False, indent=2))
                return 0
            if args.command == "prune":
                print(storage.prune(config.retention_days))
                return 0
    except Exception as exc:
        print(mask_secrets(str(exc)), file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
