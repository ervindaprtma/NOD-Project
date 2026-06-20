"""
Alert Engine (FR-08).
APScheduler-based polling that evaluates alert rules against OpenSearch,
manages state machine (INACTIVE → PENDING → FIRING → RESOLVED),
and dispatches notifications.
"""
from __future__ import annotations

import logging
import time as _time
from datetime import datetime, timedelta, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AlertLog, AlertRule, AlertState
from app.db.session import AsyncSessionLocal
from app.services.websocket_manager import alert_ws_manager

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler()


async def _evaluate_single_rule(rule: AlertRule) -> float | None:
    """
    Execute the rule's OpenSearch query and return the current metric value.
    Returns None if evaluation fails (OpenSearch unavailable, etc.).
    """
    try:
        now_ms = int(_time.time() * 1000)
        window_ms = rule.evaluation_window_minutes * 60 * 1000
        gte_ms = now_ms - window_ms
        lte_ms = now_ms

        from app.opensearch import appid as appid_qb
        from app.opensearch import ha as ha_qb
        from app.opensearch import sdwan as sdwan_qb
        from app.opensearch import sslvpn as sslvpn_qb
        from app.opensearch import ipsec as ipsec_qb

        if rule.data_source == "ha_resource":
            devices = await ha_qb.current_device_status(
                gte_ms=gte_ms, lte_ms=lte_ms
            )
            # For simplicity, take first device's metric
            if devices and rule.metric_field.startswith("ha_member."):
                field_name = rule.metric_field.split(".", 1)[1]
                return float(devices[0].get(field_name, 0) or 0)
            return 0.0

        elif rule.data_source == "appid_flow":
            total = await appid_qb.total_throughput(
                gte_ms=gte_ms, lte_ms=lte_ms
            )
            return float(total)

        elif rule.data_source == "sdwan_sla":
            site = rule.site_name or "Site_FGT-DC"
            summary = await sdwan_qb.sla_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
            )
            # Extract the requested metric field from the summary
            # e.g. metric_field = "avg_latency_link1"
            return float(summary.get(rule.metric_field, [0.0])[0] if isinstance(summary.get(rule.metric_field), list) else summary.get(rule.metric_field, 0.0) or 0.0)

        elif rule.data_source == "vpn_ssl":
            site = rule.site_name or "Site_FGT-DC_SSLVPN"
            count = await sslvpn_qb.active_sslvpn_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
            )
            return float(count)

        elif rule.data_source == "vpn_ipsec":
            count = await ipsec_qb.active_ipsec_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms,
            )
            return float(count)

        logger.warning(f"Unsupported data_source for alert evaluation: {rule.data_source}")
        return None

    except Exception as e:
        logger.error(f"Alert {rule.id} ({rule.name}) evaluation failed: {e}")
        return None


def _check_condition(value: float, op: str, threshold: float) -> bool:
    match op:
        case ">":
            return value > threshold
        case "<":
            return value < threshold
        case ">=":
            return value >= threshold
        case "<=":
            return value <= threshold
        case "==":
            return abs(value - threshold) < 0.001
    return False


async def _notify(rule: AlertRule, metric_value: float):
    """Dispatch notifications via configured channels."""
    from app.services.notifiers.telegram import send_telegram_alert
    from app.services.notifiers.email import send_email_alert

    message = (
        f"🚨 *Alert: {rule.name}*\n"
        f"Severity: {rule.severity}\n"
        f"Metric: {rule.metric_field} = {metric_value:.2f}\n"
        f"Condition: {rule.condition} {rule.threshold_value}\n"
        f"Fired at: {datetime.now(timezone.utc).isoformat()}"
    )

    for channel in rule.notify_channels:
        try:
            if channel == "telegram":
                await send_telegram_alert(message)
            elif channel == "email":
                await send_email_alert(rule.name, message)
        except Exception as e:
            logger.error(f"Failed to notify channel {channel} for rule {rule.id}: {e}")


async def evaluate_all_rules():
    """
    Main alert evaluation job.
    Executed by APScheduler on ALERT_POLL_INTERVAL_SECONDS.
    Complies with FR-08 state machine.
    """
    logger.debug("Alert evaluation cycle started")

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
            )
            rules = result.scalars().all()

            for rule in rules:
                try:
                    metric_value = await _evaluate_single_rule(rule)
                    if metric_value is None:
                        continue  # skip on evaluation failure

                    condition_met = _check_condition(
                        metric_value, rule.condition, rule.threshold_value
                    )

                    # Load or create alert state
                    state_result = await db.execute(
                        select(AlertState).where(AlertState.rule_id == rule.id)
                    )
                    state = state_result.scalar_one_or_none()
                    if not state:
                        state = AlertState(rule_id=rule.id, state="INACTIVE")
                        db.add(state)

                    now = datetime.now(timezone.utc)

                    if condition_met:
                        if state.state == "INACTIVE":
                            # Transition to PENDING
                            state.state = "PENDING"
                            state.pending_since = now
                            await db.flush()

                        elif state.state == "PENDING":
                            # Check if sustained window elapsed
                            sustained_duration = (now - state.pending_since).total_seconds() / 60
                            if sustained_duration >= rule.sustained_for_minutes:
                                # Transition to FIRING
                                state.state = "FIRING"
                                state.last_fired_at = now
                                state.last_notified_at = now
                                await db.flush()

                                # Create alert log
                                db.add(AlertLog(
                                    rule_id=rule.id,
                                    rule_name=rule.name,
                                    severity=rule.severity,
                                    metric_value_at_firing=metric_value,
                                    notified_channels=rule.notify_channels,
                                    fired_at=now,
                                    rule_snapshot={
                                        "name": rule.name,
                                        "metric_field": rule.metric_field,
                                        "aggregation": rule.aggregation,
                                        "condition": rule.condition,
                                        "threshold_value": rule.threshold_value,
                                    },
                                ))
                                await db.flush()

                                # Dispatch notification
                                await _notify(rule, metric_value)

                                # WebSocket broadcast: FIRING alert
                                await alert_ws_manager.broadcast({
                                    "type": "alert_firing",
                                    "rule_id": rule.id,
                                    "rule_name": rule.name,
                                    "severity": rule.severity,
                                    "metric_value": metric_value,
                                    "fired_at": now.isoformat(),
                                })

                        elif state.state == "FIRING":
                            # Re-notify after interval
                            if state.last_notified_at:
                                renotify_seconds = settings.ALERT_RENOTIFY_INTERVAL_MINUTES * 60
                                elapsed = (now - state.last_notified_at).total_seconds()
                                if elapsed >= renotify_seconds:
                                    state.last_notified_at = now
                                    await db.flush()
                                    await _notify(rule, metric_value)
                                    # WebSocket re-broadcast for sustained alert
                                    await alert_ws_manager.broadcast({
                                        "type": "alert_firing",
                                        "rule_id": rule.id,
                                        "rule_name": rule.name,
                                        "severity": rule.severity,
                                        "metric_value": metric_value,
                                        "fired_at": now.isoformat(),
                                    })

                    else:
                        # Condition NOT met
                        if state.state in ("FIRING", "PENDING"):
                            # Transition to RESOLVED
                            state.state = "RESOLVED"
                            state.pending_since = None
                            await db.flush()

                            # Update alert log with resolution time
                            log_result = await db.execute(
                                select(AlertLog)
                                .where(AlertLog.rule_id == rule.id)
                                .order_by(AlertLog.fired_at.desc())
                                .limit(1)
                            )
                            alert_log = log_result.scalar_one_or_none()
                            if alert_log and not alert_log.resolved_at:
                                alert_log.resolved_at = now
                                await db.flush()

                            # WebSocket broadcast: RESOLVED alert
                            await alert_ws_manager.broadcast({
                                "type": "alert_resolved",
                                "rule_id": rule.id,
                                "rule_name": rule.name,
                                "severity": rule.severity,
                                "resolved_at": now.isoformat(),
                            })

                    await db.commit()

                except Exception as e:
                    logger.error(f"Error evaluating rule {rule.id} ({rule.name}): {e}")
                    await db.rollback()

        except Exception as e:
            logger.error(f"Alert evaluation cycle failed: {e}")
            await db.rollback()

    logger.debug("Alert evaluation cycle completed")


def start_alert_scheduler():
    """Start the APScheduler with the alert evaluation job."""
    scheduler.add_job(
        evaluate_all_rules,
        "interval",
        seconds=settings.ALERT_POLL_INTERVAL_SECONDS,
        id="alert_evaluation",
        replace_existing=True,
        max_instances=1,
    )
    scheduler.start()
    logger.info(
        f"Alert scheduler started (interval={settings.ALERT_POLL_INTERVAL_SECONDS}s)"
    )
