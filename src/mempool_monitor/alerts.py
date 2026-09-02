"""Alert evaluation for mempool-monitor."""

from __future__ import annotations

from .models import Alert, CongestionLevel, Snapshot


def evaluate_alerts(
    current: Snapshot,
    previous: Snapshot | None,
    fifteen_minutes_ago: Snapshot | None,
) -> list[Alert]:
    alerts: list[Alert] = []
    if previous:
        if (
            current.congestion_level >= CongestionLevel.HIGH
            and current.congestion_level > previous.congestion_level
        ):
            alerts.append(
                Alert(
                    key="congestion",
                    severity=current.congestion_level.name,
                    title=f"混雑度 {current.congestion_level.name}",
                    description=(
                        f"推奨手数料 {current.fastest_fee:g} sat/vB、"
                        f"10 sat/vB以上 {current.backlog_10 / 1_000_000:.1f} blocks"
                    ),
                )
            )
        elif (
            previous.congestion_level >= CongestionLevel.HIGH
            and current.congestion_level < CongestionLevel.HIGH
        ):
            alerts.append(
                Alert(
                    key="congestion_recovery",
                    severity="RECOVERY",
                    title="混雑度が通常範囲へ復旧",
                    description=f"現在 {current.congestion_level.name}",
                )
            )

        previous_delay = previous.block_age_seconds
        current_delay = current.block_age_seconds
        if previous_delay < 3600 <= current_delay:
            alerts.append(
                Alert(
                    key="block_delay_critical",
                    severity="CRITICAL",
                    title="ブロック生成が60分以上停滞",
                    description=f"最新ブロックから {current_delay // 60} 分経過",
                )
            )
        elif previous_delay < 1800 <= current_delay:
            alerts.append(
                Alert(
                    key="block_delay_warning",
                    severity="WARNING",
                    title="ブロック生成が30分以上停滞",
                    description=f"最新ブロックから {current_delay // 60} 分経過",
                )
            )
        elif previous_delay >= 1800 and current_delay < 1800:
            alerts.append(
                Alert(
                    key="block_delay_recovery",
                    severity="RECOVERY",
                    title="ブロック生成が再開",
                    description=f"最新ブロック高 {current.latest_block_height:,}",
                )
            )

    if fifteen_minutes_ago and fifteen_minutes_ago.fastest_fee > 0:
        increase = current.fastest_fee - fifteen_minutes_ago.fastest_fee
        ratio = current.fastest_fee / fifteen_minutes_ago.fastest_fee
        if increase >= 5 and ratio >= 2:
            alerts.append(
                Alert(
                    key="fee_surge",
                    severity="WARNING",
                    title="推奨手数料が急騰",
                    description=(
                        f"{fifteen_minutes_ago.fastest_fee:g} → "
                        f"{current.fastest_fee:g} sat/vB"
                    ),
                )
            )
    return alerts
