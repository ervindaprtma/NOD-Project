"""
Alert Engine (FR-08).
APScheduler-based polling that evaluates alert rules against OpenSearch,
manages state machine (INACTIVE → PENDING → FIRING → RESOLVED),
and dispatches notifications.

P1: msearch batching — rules are grouped by (data_source, site, eval_window)
so one OpenSearch query serves N rules instead of N queries.
"""
from __future__ import annotations

import logging
import re
import time as _time
from collections import defaultdict
from datetime import datetime, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select

from app.core.config import get_settings
from app.db.models import AlertLog, AlertRule, AlertState
from app.db.session import AsyncSessionLocal
from app.services.sse import sse_broadcast

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler()


# ── Shared helpers ──────────────────────────────────────────────


_BASE_KEY_RE = re.compile(r"^(\w+)_link(\d+)$")


def _parse_sdwan_metric_field(metric_field: str) -> tuple[str, int]:
    """Parse 'avg_latency_link1' → ('avg_latency', 0)."""
    m = _BASE_KEY_RE.match(metric_field)
    return ((m.group(1), int(m.group(2)) - 1) if m else (metric_field, 0))


# ── P1: Group query runner ──────────────────────────────────────
# Rules sharing the same (data_source, site_name, evaluation_window_minutes)
# are evaluated with a single OpenSearch call.  Results are cached in a
# per-cycle dict and extracted per-rule below.


async def _run_group_query(
    data_source: str,
    site_name: str | None,
    window_minutes: int,
) -> float | list | dict | None:
    """Execute ONE OpenSearch query for a rule group.

    Returns a raw result that _extract_per_rule_value can work with.
    """
    now_ms = int(_time.time() * 1000)
    window_ms = window_minutes * 60 * 1000
    gte_ms = now_ms - window_ms
    lte_ms = now_ms

    try:
        if data_source == "ha_resource":
            from app.opensearch import ha as ha_qb

            return await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)

        if data_source == "appid_flow":
            from app.opensearch import appid as appid_qb

            return await appid_qb.total_throughput(gte_ms=gte_ms, lte_ms=lte_ms)

        if data_source == "sdwan_sla":
            from app.opensearch import sdwan as sdwan_qb

            return await sdwan_qb.sla_summary(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
            )

        if data_source == "vpn_ssl":
            from app.opensearch import sslvpn as sslvpn_qb

            return await sslvpn_qb.active_sslvpn_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC_SSLVPN"
            )

        if data_source == "vpn_ipsec":
            from app.opensearch import ipsec as ipsec_qb

            return await ipsec_qb.active_ipsec_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms
            )

        logger.warning("Unsupported data_source for group query: %s", data_source)
        return None

    except Exception as e:
        logger.error("Group query failed for %s (site=%s): %s", data_source, site_name, e)
        return None


def _extract_per_rule_value(
    rule: AlertRule,
    group_result: float | list | dict | None,
) -> float | None:
    """Extract a single numeric value from the group result for one rule."""
    if group_result is None:
        return None

    try:
        if rule.data_source == "ha_resource":
            if isinstance(group_result, list) and group_result and rule.metric_field.startswith("ha_member."):
                field_name = rule.metric_field.split(".", 1)[1]
                return float(group_result[0].get(field_name, 0) or 0)
            return 0.0

        if rule.data_source == "appid_flow":
            return float(group_result)

        if rule.data_source == "sdwan_sla":
            if isinstance(group_result, dict):
                base_key, link_idx = _parse_sdwan_metric_field(rule.metric_field)
                vals = group_result.get(base_key, [0.0])
                if isinstance(vals, list):
                    return float(vals[link_idx] if link_idx < len(vals) else vals[0])
                return float(vals or 0.0)
            return 0.0

        if rule.data_source == "vpn_ssl":
            return float(group_result)

        if rule.data_source == "vpn_ipsec":
            return float(group_result)

        return None

    except Exception as e:
        logger.error("Extract failed for rule %s: %s", rule.id, e)
        return None


# ── State-machine helpers ───────────────────────────────────────


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
            logger.error("Failed to notify channel %s for rule %s: %s", channel, rule.id, e)


# ── Main evaluation entry points ────────────────────────────────


async def _evaluate_single_rule(rule: AlertRule, group_override: float | list | dict | None = None) -> float | None:
    """Evaluate one rule, optionally using a pre-fetched group result.

    Deprecated — kept for backwards compatibility / test API usage.
    New callers should use _run_group_query + _extract_per_rule_value directly.
    """
    if group_override is not None:
        return _extract_per_rule_value(rule, group_override)

    result = await _run_group_query(
        rule.data_source, rule.site_name, rule.evaluation_window_minutes
    )
    return _extract_per_rule_value(rule, result)


async def evaluate_all_rules():
    """
    Main alert evaluation job.  P1 batched: enabled rules are grouped by
    (data_source, site_name, evaluation_window) so N rules sharing the same
    OS profile make one query instead of N.

    Executed by APScheduler on ALERT_POLL_INTERVAL_SECONDS.
    Complies with FR-08 state machine.
    """
    logger.debug("Alert evaluation cycle started (P1 batched)")

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
            )
            rules = result.scalars().all()
            if not rules:
                logger.debug("No enabled alert rules to evaluate")
                return

            # ── 1. Group rules by (data_source, site, window) ──
            groups: dict[tuple, list[AlertRule]] = defaultdict(list)
            for rule in rules:
                key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes)
                groups[key].append(rule)

            # ── 2. Run one OpenSearch query per group ──
            group_cache: dict[tuple, float | list | dict | None] = {}
            for key in groups:
                ds, site, window = key
                group_cache[key] = await _run_group_query(ds, site, window)

            # ── 3. Per-rule extract + state machine ──
            for rule in rules:
                try:
                    key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes)
                    group_result = group_cache.get(key)
                    metric_value = _extract_per_rule_value(rule, group_result)
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

                                # SSE broadcast: FIRING alert
                                await sse_broadcast("alert",
                                    rule_id=rule.id,
                                    rule_name=rule.name,
                                    severity=rule.severity,
                                    metric_value=metric_value,
                                    fired_at=now.isoformat(),
                                )

                        elif state.state == "FIRING":
                            # Re-notify after interval
                            if state.last_notified_at:
                                renotify_seconds = settings.ALERT_RENOTIFY_INTERVAL_MINUTES * 60
                                elapsed = (now - state.last_notified_at).total_seconds()
                                if elapsed >= renotify_seconds:
                                    state.last_notified_at = now
                                    await db.flush()
                                    await _notify(rule, metric_value)
                                    # SSE re-broadcast for sustained alert
                                    await sse_broadcast("alert",
                                        rule_id=rule.id,
                                        rule_name=rule.name,
                                        severity=rule.severity,
                                        metric_value=metric_value,
                                        fired_at=now.isoformat(),
                                    )

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

                            # SSE broadcast: RESOLVED alert
                            await sse_broadcast("resolved",
                                rule_id=rule.id,
                                rule_name=rule.name,
                                severity=rule.severity,
                                resolved_at=now.isoformat(),
                            )

                    await db.commit()

                except Exception as e:
                    logger.error("Error evaluating rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

        except Exception as e:
            logger.error("Alert evaluation cycle failed: %s", e)
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
        "Alert scheduler started (interval=%ss)", settings.ALERT_POLL_INTERVAL_SECONDS
    )
