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
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.models import AlertLog, AlertRule, AlertState, MaintenanceWindow, NotificationTemplate
from app.db.session import AsyncSessionLocal, engine
from app.opensearch.query import degradation_scope
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.sse import sse_broadcast

logger = logging.getLogger(__name__)
settings = get_settings()

scheduler = AsyncIOScheduler()

# Phase C: last-run telemetry for the engine-health endpoint. Set at the end of
# each evaluate_all_rules() tick. Process-local (matches the in-process scheduler).
_last_run_at: datetime | None = None
_last_run_ms: int | None = None

# Phase D: Postgres session-level advisory lock key. If the backend is ever scaled to
# >1 replica, only the holder evaluates — the rest skip the tick, so notifications fire
# once, not once-per-replica. Arbitrary stable bigint (not shared with any other lock).
_EVALUATOR_LOCK_KEY = 4923017  # "nod alert evaluator"


def get_engine_health() -> dict:
    """Scheduler timings for GET /alerts/engine-health (rule count added by the API)."""
    job = scheduler.get_job("alert_evaluation")
    next_run = getattr(job, "next_run_time", None) if job else None
    return {
        "last_run_at": _last_run_at.isoformat() if _last_run_at else None,
        "last_run_ms": _last_run_ms,
        "next_run_at": next_run.isoformat() if next_run else None,
        "interval_seconds": settings.ALERT_POLL_INTERVAL_SECONDS,
        "running": scheduler.running,
    }


# ── Shared helpers ──────────────────────────────────────────────


_BASE_KEY_RE = re.compile(r"^(\w+)_link(\d+)$")


def _parse_sdwan_metric_field(metric_field: str) -> tuple[str, int]:
    """Parse 'avg_latency_link1' → ('avg_latency', 0)."""
    m = _BASE_KEY_RE.match(metric_field)
    return ((m.group(1), int(m.group(2)) - 1) if m else (metric_field, 0))


# §9.5: Jinja2 SandboxedEnvironment — only path for rendering any user-
# editable template body. SandboxedEnvironment blocks dunder / attribute-
# chain escapes by default. Do NOT swap for vanilla Environment.
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment

_SANDBOX = SandboxedEnvironment(
    autoescape=True,           # SSTI defense-in-depth: escape <, >, &, " in interpolated values
    undefined=StrictUndefined,  # {{ missing_var }} → render error, NOT empty string
    keep_trailing_newline=False,
)

# ponytail: the only Jinja2 filters a template body may use. Adding more
# here requires a §10 gate pass — see docs/alert_notification_design.md §10.
_ALLOWED_FILTERS = frozenset({"round", "upper", "lower", "default", "e"})

_SANDBOX.filters = {k: _SANDBOX.filters[k] for k in _ALLOWED_FILTERS if k in _SANDBOX.filters}


def _render_template(text: str, ctx: dict) -> str:
    """Render a Jinja2 template body in the sandbox. Whitelist of filter names
    is enforced at module init. StrictUndefined means a typo in a variable
    name raises — caught at fire time, not silently dropped.

    Returns the rendered string. Never raises for a syntax-valid template
    referencing only allow-listed vars; re-raises for any other error so
    the engine's existing try/except logs it.
    """
    rendered: str = _SANDBOX.from_string(text).render(**ctx)
    return rendered


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

    Returns a raw result that _extract_per_rule_value can work with, or None when the
    result cannot be trusted — callers skip the rule and hold its state rather than
    evaluate on a bad number.

    safe_search() deliberately never raises: on timeout, circuit breaker, or partial
    shard results it returns an empty skeleton, which arrives here as a perfectly
    real-looking 0. Evaluating on that silently converts an infrastructure failure into
    a wrong answer in both directions — a "throughput < 10 Mbps" rule fires a false
    outage, and any already-FIRING rule sees 0, fails its condition, and reports a
    false all-clear. So a degraded read must be "unknown", never "zero".
    """
    now_ms = int(_time.time() * 1000)
    window_ms = window_minutes * 60 * 1000
    gte_ms = now_ms - window_ms
    lte_ms = now_ms

    with degradation_scope() as degraded:
        try:
            if data_source == "ha_resource":
                from app.opensearch import ha as ha_qb

                result: float | list | dict | None = await ha_qb.current_device_status(
                    gte_ms=gte_ms, lte_ms=lte_ms
                )

            elif data_source == "appid_flow":
                from app.opensearch import traffic_flow as tf_qb

                # Per-path dict: {internet, inbound-vip, inter-site, intra-lan, _wan}
                # each with *_mbps / *_bytes. Extractor selects node + metric.
                result = await tf_qb.appid_flow_alert_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
                )

            elif data_source == "sdwan_sla":
                from app.opensearch import sdwan as sdwan_qb

                result = await sdwan_qb.sla_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
                )

            elif data_source == "vpn_ssl":
                from app.opensearch import sslvpn as sslvpn_qb

                result = await sslvpn_qb.active_sslvpn_users_count(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC_SSLVPN"
                )

            elif data_source == "vpn_ipsec":
                from app.opensearch import ipsec as ipsec_qb

                result = await ipsec_qb.active_ipsec_users_count(gte_ms=gte_ms, lte_ms=lte_ms)

            elif data_source == "interface_stats":
                from app.opensearch import interface_stats as if_qb

                # Per-ifIndex dict of rate stats; extractor picks target_key + metric.
                # All interface rules for a (site, window) share this one query.
                result = await if_qb.interface_stats_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
                )

            else:
                logger.warning("Unsupported data_source for group query: %s", data_source)
                return None

        except Exception as e:
            logger.error("Group query failed for %s (site=%s): %s", data_source, site_name, e)
            return None

        if degraded:
            logger.warning(
                "Skipping rule group %s (site=%s, window=%dmin): data degraded, "
                "holding state instead of evaluating — %s",
                data_source, site_name, window_minutes, degraded[:2],
            )
            return None

    return result


# appid_flow metric_field "traffic.<path>.<metric>" → path bucket key
_APPID_PATH_KEYS = {
    "internet": "internet", "inbound": "inbound-vip",
    "inter_site": "inter-site", "intra_lan": "intra-lan", "wan": "_wan",
}


def _extract_appid_flow(metric_field: str, group_result: dict[Any, Any]) -> float:
    """Select one value from appid_flow_alert_summary's per-path dict.

    New rules use "traffic.<path>.<metric>" (e.g. traffic.internet.download_mbps).
    Legacy field_keys (total_throughput, app_total_bytes, flow.*bytes*) predate the
    per-path split — they always measured the site-wide total in *bytes*, so they map
    to the _wan total_bytes node to keep firing exactly as before (unit-preserving).
    Migrate them to the new keys to get real per-path granularity + Mbps thresholds.
    """
    if metric_field.startswith("traffic."):
        parts = metric_field.split(".", 2)
        if len(parts) == 3:
            path = _APPID_PATH_KEYS.get(parts[1])
            if path is not None:
                return float(group_result.get(path, {}).get(parts[2], 0.0) or 0.0)
        return 0.0
    # legacy → site-wide total bytes (unchanged behavior)
    return float(group_result.get("_wan", {}).get("total_bytes", 0.0) or 0.0)


def _extract_interface_stats(
    metric_field: str, target_key: str | None, aggregation: str, group_result: dict[Any, Any]
) -> float:
    """Select one value from interface_stats_summary's per-ifIndex dict.

    metric_field is "iface.<base>" (rx_mbps | tx_mbps | utilization_pct | oper_status);
    target_key is the ifIndex. Missing interface → 0 (no false fire). oper_status is a
    level (not avg/max); the rate metrics use the rule's aggregation (avg/max only).
    """
    if not target_key:
        return 0.0
    iface = group_result.get(target_key)
    if not iface:
        return 0.0
    base = metric_field.split(".", 1)[1] if "." in metric_field else metric_field
    if base == "oper_status":
        return float(iface.get("oper_status") or 0)
    agg = aggregation if aggregation in ("avg", "max") else "avg"
    return float(iface.get(base, {}).get(agg, 0.0) or 0.0)


def _extract_per_rule_value(
    rule: AlertRule,
    group_result: float | list[Any] | dict[Any, Any] | None,
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
            if isinstance(group_result, dict):
                return _extract_appid_flow(rule.metric_field, group_result)
            return 0.0

        if rule.data_source == "sdwan_sla":
            if isinstance(group_result, dict):
                base_key, link_idx = _parse_sdwan_metric_field(rule.metric_field)
                vals = group_result.get(base_key, [0.0])
                if isinstance(vals, list):
                    return float(vals[link_idx] if link_idx < len(vals) else vals[0])
                return float(vals or 0.0)
            return 0.0

        if rule.data_source == "vpn_ssl":
            if isinstance(group_result, (int, float)):
                return float(group_result)
            return 0.0

        if rule.data_source == "vpn_ipsec":
            if isinstance(group_result, (int, float)):
                return float(group_result)
            return 0.0

        if rule.data_source == "interface_stats":
            if isinstance(group_result, dict):
                return _extract_interface_stats(
                    rule.metric_field, rule.target_key, rule.aggregation, group_result
                )
            return 0.0

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
    """Dispatch notifications via configured channels (v3 §3.13).

    Loads enabled channel configs from the notification_configs table,
    decrypts secrets, and dispatches to the appropriate notifier module.
    Falls back to env-var config if DB config is empty.

    §11.1: If rule has notification_template_id, renders subject/body/line
    from the template via sandboxed Jinja2. Falls back to hardcoded format
    if no template is set.
    """
    from app.services.notifier_helper import send_alert, load_channel_configs

    fired_at = datetime.now(timezone.utc).isoformat()
    subject: str | None = None
    message: str | None = None

    # §11.1: Try to load notification template
    if rule.notification_template_id:
        async with AsyncSessionLocal() as db:
            tmpl = await db.get(NotificationTemplate, rule.notification_template_id)
        if tmpl:
            ctx = {
                "rule": {
                    "name": rule.name,
                    "severity": rule.severity,
                    "site_name": rule.site_name,
                    "metric_field": rule.metric_field,
                    "condition": rule.condition,
                    "threshold_value": rule.threshold_value,
                },
                "metric_value": metric_value,
                "fired_at": fired_at,
            }
            try:
                subject = _render_template(tmpl.subject_template, ctx)
                message = _render_template(tmpl.body_template, ctx)
            except Exception as e:
                logger.warning(f"Template render failed, using fallback: {e}")
                tmpl = None

    # Fallback if no template or render failed
    if subject is None or message is None:
        subject = rule.name
        message = (
            f"🚨 *Alert: {rule.name}*\n"
            f"Severity: {rule.severity}\n"
            f"Metric: {rule.metric_field} = {metric_value:.2f}\n"
            f"Condition: {rule.condition} {rule.threshold_value}\n"
            f"Fired at: {fired_at}"
        )

    # Load DB channel configs once
    db_configs = await load_channel_configs(min_severity=rule.severity)

    for channel in rule.notify_channels:
        try:
            channel_config = db_configs.get(channel, {})
            await send_alert(
                channel=channel,
                config=channel_config,
                subject=subject,
                body=message,
                severity=rule.severity,
            )
        except Exception as e:
            logger.error("Failed to notify channel %s for rule %s: %s", channel, rule.id, e)


# ── Main evaluation entry points ────────────────────────────────


async def _advance_state_machine(
    rule: AlertRule,
    metric_value: float,
    condition_met: bool,
    db: AsyncSession,
    notify_queue: list[tuple[AlertRule, float]] | None = None,
) -> None:
    """Shared state machine for single and composite rules (P5).

    Notifications are enqueued for batch dispatch (P7 grouping) rather
    than sent immediately.  SSE broadcasts remain per-rule for real-time.
    """
    state_result = await db.execute(
        select(AlertState).where(AlertState.rule_id == rule.id)
    )
    state = state_result.scalar_one_or_none()
    if not state:
        state = AlertState(rule_id=rule.id, state="INACTIVE")
        db.add(state)

    now = datetime.now(timezone.utc)

    # Phase C: stamp this evaluation (a real read reached here, so not degraded).
    prev_state = state.state
    state.last_evaluated_at = now
    state.last_value = metric_value
    state.last_read_degraded = False

    if condition_met:
        if state.state == "INACTIVE":
            state.state = "PENDING"
            state.pending_since = now
            await db.flush()

        elif state.state == "PENDING":
            pending_since = state.pending_since
            if pending_since is not None:
                sustained_duration = (now - pending_since).total_seconds() / 60
                if sustained_duration >= rule.sustained_for_minutes:
                    state.state = "FIRING"
                    state.last_fired_at = now
                    state.last_notified_at = now
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

                    # Notify only on the PENDING -> FIRING transition. This block used
                    # to sit one level out, so every tick of the sustain window sent a
                    # notification: sustained_for_minutes gated the state change but
                    # not the alerting, turning a 15-minute debounce on a 60s tick into
                    # ~15 messages before the rule had even fired.
                    if notify_queue is not None:
                        notify_queue.append((rule, metric_value))

                    # SSE stays per-rule (real-time)
                    await sse_broadcast("alert",
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        metric_value=metric_value,
                        fired_at=now.isoformat(),
                    )

        elif state.state == "FIRING":
            if state.last_notified_at:
                renotify_seconds = settings.ALERT_RENOTIFY_INTERVAL_MINUTES * 60
                elapsed = (now - state.last_notified_at).total_seconds()
                if elapsed >= renotify_seconds:
                    state.last_notified_at = now
                    await db.flush()

                    # Enqueue re-notification (batch)
                    if notify_queue is not None:
                        notify_queue.append((rule, metric_value))

                    await sse_broadcast("alert",
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        metric_value=metric_value,
                        fired_at=now.isoformat(),
                    )

    else:
        if state.state in ("FIRING", "PENDING"):
            state.state = "RESOLVED"
            state.pending_since = None
            await db.flush()

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

            await sse_broadcast("resolved",
                rule_id=rule.id,
                rule_name=rule.name,
                severity=rule.severity,
                resolved_at=now.isoformat(),
            )

    if state.state != prev_state:
        state.last_state_change_at = now

    await db.commit()


async def _mark_held(rule: AlertRule, db: AsyncSession) -> None:
    """Phase C: a degraded/held read — record the attempt without advancing state.

    Stamps last_evaluated_at + last_read_degraded so the UI shows a "data delayed"
    badge instead of a silent hold. State value is left untouched (no false resolve).
    """
    state_result = await db.execute(
        select(AlertState).where(AlertState.rule_id == rule.id)
    )
    state = state_result.scalar_one_or_none()
    if not state:
        state = AlertState(rule_id=rule.id, state="INACTIVE")
        db.add(state)
    state.last_evaluated_at = datetime.now(timezone.utc)
    state.last_read_degraded = True
    await db.commit()


async def _evaluate_composite_rule(
    rule: AlertRule, group_cache: dict | None = None
) -> tuple[float | None, bool]:
    """Evaluate a composite rule's clauses and combine with AND/OR.

    Returns (max_metric_value, condition_met).
    Returns (None, False) if no clauses could be evaluated.

    If group_cache is provided, clauses reuse the pre-fetched OpenSearch
    results from the same cycle (§9.4). Without it, falls back to direct
    _run_group_query (test/standalone path).
    """
    if not rule.clauses:
        return None, False

    clause_metrics: list[float] = []
    clause_breaches: list[bool] = []

    for clause in rule.clauses:
        ds_raw = clause.get("data_source")
        mf_raw = clause.get("metric_field")
        if not isinstance(ds_raw, str) or not isinstance(mf_raw, str):
            continue
        ds = ds_raw
        mf = mf_raw
        cond = clause.get("condition", ">")
        thresh = clause.get("threshold_value", 0.0)
        window = clause.get("evaluation_window_minutes", rule.evaluation_window_minutes)

        # §9.4: read from the cycle's pre-fetched cache when available
        cache_key = (ds, rule.site_name, window)
        if group_cache is not None and cache_key in group_cache:
            group_result = group_cache[cache_key]
        else:
            group_result = await _run_group_query(ds, rule.site_name, window)
        if group_result is None:
            break  # one clause failed → whole rule fails

        val = _extract_per_rule_value_flat(ds, mf, group_result)
        if val is None:
            break

        clause_metrics.append(val)
        clause_breaches.append(_check_condition(val, cond, thresh))

    if len(clause_metrics) != len(rule.clauses):
        return None, False  # incomplete evaluation

    # Combine with AND/OR
    notify_when = rule.notify_when or "any"
    condition_met = all(clause_breaches) if notify_when == "all" else any(clause_breaches)

    # Report the max metric value across breaching clauses (or all clauses if none breach)
    metric_value = max(clause_metrics)
    return metric_value, condition_met


# ponytail: _extract_per_rule_value but takes flat data_source/metric_field instead of a rule object
def _extract_per_rule_value_flat(
    data_source: str, metric_field: str, group_result: float | list[Any] | dict[Any, Any] | None
) -> float | None:
    """Same logic as _extract_per_rule_value but accepts flat params (P5 composite)."""
    if group_result is None:
        return None
    try:
        if data_source == "ha_resource":
            if isinstance(group_result, list) and group_result and metric_field.startswith("ha_member."):
                field_name = metric_field.split(".", 1)[1]
                return float(group_result[0].get(field_name, 0) or 0)
            return 0.0
        if data_source == "appid_flow":
            if isinstance(group_result, dict):
                return _extract_appid_flow(metric_field, group_result)
            return 0.0
        if data_source in ("vpn_ssl", "vpn_ipsec"):
            if isinstance(group_result, (int, float)):
                return float(group_result)
            return 0.0
        if data_source == "sdwan_sla":
            if isinstance(group_result, dict):
                base_key, link_idx = _parse_sdwan_metric_field(metric_field)
                vals = group_result.get(base_key, [0.0])
                if isinstance(vals, list):
                    return float(vals[link_idx] if link_idx < len(vals) else vals[0])
                return float(vals or 0.0)
            return 0.0
        if data_source == "interface_stats":
            # Composite clauses carry no target_key (which interface), so interface_stats
            # can't be resolved here. Single rules only — return 0 so a stray clause can't
            # false-fire. (Add a clause-level target_key if composite interface rules are
            # ever needed.)
            return 0.0
        return None
    except (TypeError, ValueError, IndexError):
        return None
async def _flush_batch_notify(notify_queue: list[tuple[AlertRule, float]]) -> None:
    """Send batched notifications — one grouped message per channel (P7).

    Instead of one message per rule, aggregates all pending notifications
    into a single message per notification channel.

    §9.5: per-rule line rendering via Jinja2 SandboxedEnvironment, if the
    rule's template has a body_template. Rules without a template use the
    legacy hardcoded line format.
    """
    if not notify_queue:
        return

    now_str = datetime.now(timezone.utc).strftime("%d %b %Y %H:%M UTC")

    # Build aggregated message
    lines = [f"🛑  NOD Alert Summary — {now_str}", "━" * 35]
    sev_emoji = {"CRITICAL": "🔴", "WARNING": "⚠️", "INFO": "ℹ️"}

    # §9.5: pre-fetch all referenced templates in one query
    template_ids = {r.template_id for r, _ in notify_queue if r.template_id}
    template_body: dict[str, str] = {}
    if template_ids:
        from app.db.models import AlertTemplate
        from app.db.session import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as tdb:
                result = await tdb.execute(
                    select(AlertTemplate.id, AlertTemplate.body_template)
                    .where(AlertTemplate.id.in_(template_ids))
                )
                template_body = {row[0]: row[1] for row in result.all() if row[1]}
        except Exception as e:
            logger.error("Failed to fetch alert templates: %s", e)

    # §11.1: pre-fetch assigned notification-message templates. These take precedence
    # over the AlertTemplate body for the per-rule line — the admin-managed message text
    # (Settings → Message Templates) is what the rule's notification_template_id points at.
    # Prefer line_template (the batch line), fall back to body_template.
    nt_ids = {r.notification_template_id for r, _ in notify_queue if r.notification_template_id}
    nt_line: dict[str, str] = {}
    if nt_ids:
        from app.db.session import AsyncSessionLocal
        try:
            async with AsyncSessionLocal() as tdb:
                result = await tdb.execute(
                    select(NotificationTemplate.id, NotificationTemplate.line_template,
                           NotificationTemplate.body_template)
                    .where(NotificationTemplate.id.in_(nt_ids))
                )
                nt_line = {row[0]: (row[1] or row[2]) for row in result.all() if (row[1] or row[2])}
        except Exception as e:
            logger.error("Failed to fetch notification templates: %s", e)

    for rule, mv in notify_queue:
        sev = sev_emoji.get(rule.severity, "🔔")
        # §11.1 message template (assigned) wins; else §9.5 AlertTemplate body; else hardcoded.
        tmpl_text = (
            nt_line.get(rule.notification_template_id) if rule.notification_template_id else None
        ) or (
            template_body.get(rule.template_id) if rule.template_id else None
        )
        if tmpl_text:
            # The seeded AlertTemplates use flat var names ({{ name }}, {{ metric_value }});
            # §11.1 NotificationTemplates use nested {{ rule.* }}. Provide both so either renders.
            ctx = {
                "rule": {
                    "name": rule.name,
                    "severity": rule.severity,
                    "site_name": rule.site_name,
                    "metric_field": rule.metric_field,
                    "condition": rule.condition,
                    "threshold_value": rule.threshold_value,
                },
                # Flat aliases for the seeded AlertTemplates.
                "name": rule.name,
                "severity": rule.severity,
                "site_name": rule.site_name,
                "metric_field": rule.metric_field,
                "condition": rule.condition,
                "threshold_value": rule.threshold_value,
                "threshold": rule.threshold_value,
                "metric_value": mv,
                "data_source": rule.data_source,
                "aggregation": rule.aggregation,
                "fired_at": now_str,
            }
            try:
                lines.append(_render_template(tmpl_text, ctx))
                continue
            except Exception as e:
                # ponytail: render error falls back to hardcoded line — never
                # lose the alert to a template typo
                logger.error("Template render failed for rule %s: %s — falling back to hardcoded", rule.id, e)
        lines.append(f"{sev} [{rule.severity}] {rule.name} @ {rule.site_name or '—'}: {mv}")
    body = "\n".join(lines)

    # Load channels and send — only the ones the firing rules actually want
    # (§9.3: was sending to every enabled channel; now respects rule.notify_channels)
    try:
        from app.db.session import AsyncSessionLocal
        from app.db.models import NotificationConfig as NotifCfg
        from app.services.notifier_helper import send_alert

        fired_channels = {ch for rule, _ in notify_queue for ch in rule.notify_channels}

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(NotifCfg).where(
                    (NotifCfg.enabled == True)  # noqa: E712
                    & (NotifCfg.channel.in_(fired_channels))
                )
            )
            channels = result.scalars().all()
            for ch in channels:
                try:
                    await send_alert(ch.channel, ch.config, subject="NOD Alert Summary", body=body)
                except Exception as e:
                    logger.error("Batch notify failed for %s: %s", ch.channel, e)
    except Exception as e:
        logger.error("Batch notify flush error: %s", e)


async def _run_evaluation_cycle() -> None:
    """One tick: enabled rules → grouped OS queries → state machine → batched notify.

    Split out from evaluate_all_rules so the advisory-lock wrapper stays thin. Opens its
    own session; per-rule commits/rollbacks happen inside _advance_state_machine.
    """
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(AlertRule).where(AlertRule.enabled == True)  # noqa: E712
            )
            rules = result.scalars().all()
            if not rules:
                logger.debug("No enabled alert rules to evaluate")
                return

            # ── Load active maintenance windows (v3 §3.14) ──
            now = datetime.now(timezone.utc)
            mw_result = await db.execute(
                select(MaintenanceWindow.site_name)
                .where(MaintenanceWindow.starts_at <= now)
                .where(MaintenanceWindow.ends_at >= now)
            )
            sites_in_maintenance = {row[0] for row in mw_result.all()}

            # Filter out rules in maintenance
            active_rules = [r for r in rules if r.site_name not in sites_in_maintenance]
            if len(active_rules) != len(rules):
                skipped = len(rules) - len(active_rules)
                logger.info("Maintenance window active — skipping %s rule(s)", skipped)

            # Split single vs composite
            single_rules = [r for r in active_rules if r.kind != "composite"]
            composite_rules = [r for r in active_rules if r.kind == "composite"]

            # ── 1. Single rules (P1 batched) ──
            notify_queue: list[tuple[AlertRule, float]] = []
            groups: dict[tuple, list[AlertRule]] = defaultdict(list)
            for rule in single_rules:
                key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes)
                groups[key].append(rule)

            # §9.4: also pre-fetch for composite clause keys, sharing the
            # cache with singles that happen to share a (ds, site, window) tuple.
            for rule in composite_rules:
                for clause in (rule.clauses or []):
                    ds = clause.get("data_source")
                    if not ds:
                        continue
                    window = clause.get("evaluation_window_minutes", rule.evaluation_window_minutes)
                    groups.setdefault((ds, rule.site_name, window), [])

            group_cache: dict[tuple, float | list | dict | None] = {}
            for key in groups:
                ds, site, window = key
                group_cache[key] = await _run_group_query(ds, site, window)

            for rule in single_rules:
                try:
                    key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes)
                    group_result = group_cache.get(key)
                    metric_value = _extract_per_rule_value(rule, group_result)
                    if metric_value is None:
                        await _mark_held(rule, db)
                        continue
                    condition_met = _check_condition(
                        metric_value, rule.condition, rule.threshold_value
                    )
                    await _advance_state_machine(rule, metric_value, condition_met, db, notify_queue)
                except Exception as e:
                    logger.error("Error evaluating rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

            # ── 2. Composite rules (per-rule evaluation) ──
            for rule in composite_rules:
                try:
                    metric_value, condition_met = await _evaluate_composite_rule(rule, group_cache)
                    if metric_value is None:
                        await _mark_held(rule, db)
                        continue
                    await _advance_state_machine(rule, metric_value, condition_met, db, notify_queue)
                except Exception as e:
                    logger.error("Error evaluating composite rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

            # ── 3. Flush batched notifications (P7 grouping) ──
            if notify_queue:
                await _flush_batch_notify(notify_queue)

        except Exception as e:
            logger.error("Alert evaluation cycle failed: %s", e)
            await db.rollback()


async def evaluate_all_rules():
    """
    Main alert evaluation job.  P1 batched: enabled rules are grouped by
    (data_source, site_name, evaluation_window) so N rules sharing the same
    OS profile make one query instead of N.  P5: composite rules are evaluated
    separately clause-by-clause.

    Executed by APScheduler on ALERT_POLL_INTERVAL_SECONDS.
    Complies with FR-08 state machine.
    """
    global _last_run_at, _last_run_ms
    logger.debug("Alert evaluation cycle started (P1 batched)")
    _t_start = _time.monotonic()

    # Phase D: single-evaluator guarantee. The advisory lock is session-level (tied to a
    # connection); the evaluation session commits per rule and cycles connections, so the
    # lock lives on its OWN dedicated connection held for the whole cycle. If another
    # replica holds it, skip this tick — notifications fire once, not once-per-replica.
    async with engine.connect() as lock_conn:
        got_lock = (
            await lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": _EVALUATOR_LOCK_KEY})
        ).scalar()
        if not got_lock:
            logger.debug("Alert evaluator lock held elsewhere — skipping this tick")
            return

        # Self-watch: warn if the previous completed run is older than 2× the interval
        # (missed ticks / a stalled loop / OpenSearch slowness). engine-health also
        # surfaces this as `stalled`.
        if _last_run_at is not None:
            gap = (datetime.now(timezone.utc) - _last_run_at).total_seconds()
            if gap > 2 * settings.ALERT_POLL_INTERVAL_SECONDS:
                logger.warning(
                    "Alert evaluator gap %.0fs > 2× interval (%ss) — missed ticks or stall",
                    gap, settings.ALERT_POLL_INTERVAL_SECONDS,
                )

        try:
            await _run_evaluation_cycle()
        finally:
            # pg_advisory_unlock is non-transactional and runs on the same connection that
            # took the lock — the only release path (rollbacks don't drop a session lock).
            await lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": _EVALUATOR_LOCK_KEY})

    _last_run_at = datetime.now(timezone.utc)
    _last_run_ms = int((_time.monotonic() - _t_start) * 1000)
    logger.debug("Alert evaluation cycle completed in %dms", _last_run_ms)


def start_alert_scheduler():
    """Start the APScheduler with the alert evaluation job."""
    scheduler.add_job(
        evaluate_all_rules,
        "interval",
        seconds=settings.ALERT_POLL_INTERVAL_SECONDS,
        id="alert_evaluation",
        replace_existing=True,
        max_instances=1,
        # Phase D: a slow tick must not stack overlapping runs. coalesce collapses
        # any ticks that piled up during a long run into one; misfire_grace_time drops
        # a tick that's already >30s late instead of firing it against stale timing.
        coalesce=True,
        misfire_grace_time=30,
    )
    scheduler.start()
    logger.info(
        "Alert scheduler started (interval=%ss)", settings.ALERT_POLL_INTERVAL_SECONDS
    )
