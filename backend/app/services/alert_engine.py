"""
Alert Engine (FR-08).
APScheduler-based polling that evaluates alert rules against OpenSearch,
manages state machine (INACTIVE → PENDING → FIRING → RESOLVED),
and dispatches notifications.

P1: msearch batching — rules are grouped by (data_source, site, eval_window)
so one OpenSearch query serves N rules instead of N queries.
"""
from __future__ import annotations

import html
import logging
import re
import time as _time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
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

# Notification timestamps render in WIB (UTC+7, no DST) to match operators' local time
# and the Grafana-style templates. SSE/API times stay UTC ISO — the frontend localizes those.
_WIB = timezone(timedelta(hours=7))

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
    # autoescape OFF: every consumer (Telegram/Discord/WhatsApp/plain-text email) sends
    # this output as plain text, where HTML-escaping turns `>` into a literal `&gt;`.
    # SSTI protection comes from SandboxedEnvironment (dunder/attribute blocking) +
    # the _ALLOWED_FILTERS whitelist — NOT from autoescape, which is an XSS/HTML concern
    # that doesn't apply here. (The report generator renders HTML via its own separate
    # _render_template, not this sandbox.)
    autoescape=False,
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


def _degradation_forces_hold(degraded: list[str], result: Any) -> bool:
    """Decide whether a degraded OpenSearch read is too untrustworthy for the engine to
    evaluate (→ hold state), vs. usable enough to proceed.

    HOLD only on a HARD failure — timeout / circuit breaker returns an empty skeleton, so
    the number would be fabricated — or when the read produced no usable data at all.

    PROCEED on a partial-shard failure that still returned data: on a multi-index pattern
    (telegraf-index* / fortigate-appid-flow-*) that almost always means an OLD/cold-index
    shard failed while the RECENT index the alert actually needs succeeded. Holding on that
    made alerts NEVER fire on any cluster carrying one perpetually-failing cold shard — the
    pages tolerate it and show the value, so the engine must too, or it silently goes deaf.
    """
    if not degraded:
        return False
    hard = any(("timeout" in d) or ("circuit_breaker" in d) for d in degraded)
    return hard or not result


async def _run_group_query(
    data_source: str,
    site_name: str | None,
    window_minutes: int,
    appid_filter: dict | None = None,
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
                # each with *_mbps / *_bytes. Extractor selects node + metric. An optional
                # appid_filter narrows every path to one app/protocol/port.
                af = appid_filter or {}
                result = await tf_qb.appid_flow_alert_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC",
                    app_filter=af.get("app") or "", protocol=af.get("protocol") or "",
                    dst_port=af.get("port"),
                )

            elif data_source == "sdwan_sla":
                from app.opensearch import sdwan as sdwan_qb

                result = await sdwan_qb.sla_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
                )

            elif data_source == "vpn_ssl":
                from app.opensearch import sslvpn as sslvpn_qb

                # {count, total_bytes, top_user_bytes} — extractor picks count or a volume metric.
                result = await sslvpn_qb.sslvpn_usage_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms,
                    site_name=sslvpn_qb.sslvpn_measurement_for_site(site_name),
                )

            elif data_source == "vpn_ipsec":
                from app.opensearch import ipsec as ipsec_qb

                result = await ipsec_qb.ipsec_usage_summary(gte_ms=gte_ms, lte_ms=lte_ms)

            elif data_source == "interface_stats":
                from app.opensearch import interface_stats as if_qb

                # Per-ifIndex dict of rate stats; extractor picks target_key + metric.
                # All interface rules for a (site, window) share this one query.
                result = await if_qb.interface_stats_summary(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC"
                )

            elif data_source == "device_uptime":
                from app.opensearch import device_uptime as du_qb

                # {summary, devices} for the eval window. The explicit gte/lte win over
                # the named window, and resolve_range picks the bucket from the span
                # (a 5min window → 1min buckets → enough shape for silence/gap detection).
                # All device rules for a (site, window) share this one query.
                result = await du_qb.device_availability(
                    site_name=site_name or "Site_FGT-DC", gte_ms=gte_ms, lte_ms=lte_ms
                )

            else:
                logger.warning("Unsupported data_source for group query: %s", data_source)
                return None

        except Exception as e:
            logger.error("Group query failed for %s (site=%s): %s", data_source, site_name, e)
            return None

        if degraded:
            if _degradation_forces_hold(degraded, result):
                logger.warning(
                    "Holding rule group %s (site=%s, window=%dmin): unusable read — %s",
                    data_source, site_name, window_minutes, degraded[:2],
                )
                return None
            logger.warning(
                "Rule group %s (site=%s): proceeding despite partial data (recent index OK) — %s",
                data_source, site_name, degraded[:2],
            )

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


def _extract_device_uptime(
    metric_field: str, target_key: str | None, group_result: dict[Any, Any]
) -> float | None:
    """Select one value from device_availability()'s {summary, devices} result.

    Metrics (§11.1): not_reporting / reboot_count / uptime_seconds / wrap_risk /
    availability_pct are per-device; collector_gap is site-level.

    target_key is the device IP (tag.source). Blank = "any device at the site":
    the metric reduces to the worst case across devices, so one down/rebooting
    device fires the rule.

    Two deliberate None (→ hold, never a false fire):
      • a named device absent from the window — evaluate on nothing, not a fake 0;
      • availability_pct unknown (insufficient history) — never breach an SLA on a
        fabricated number.

    The storm guard is split: a site-wide Telegraf outage makes every silent device
    read status "collector_gap" (not "not_reporting") in the data layer, so per-device
    rules go quiet on their own; collector_gap here is the one positive signal that fires.
    """
    devices = group_result.get("devices", []) or []

    if metric_field == "collector_gap":
        return 1.0 if any(d.get("status") == "collector_gap" for d in devices) else 0.0

    if target_key:
        devices = [d for d in devices if d.get("device_key") == target_key]
        if not devices:
            return None  # named device not in window → hold
    if not devices:
        return None

    if metric_field == "not_reporting":
        return 1.0 if any(d.get("status") == "not_reporting" for d in devices) else 0.0
    if metric_field == "wrap_risk":
        return 1.0 if any(d.get("wrap_risk") for d in devices) else 0.0
    if metric_field == "reboot_count":
        return float(max((d.get("reboot_count", 0) or 0) for d in devices))
    if metric_field == "uptime_seconds":
        vals = [d["uptime_seconds"] for d in devices if d.get("uptime_seconds") is not None]
        return float(min(vals)) if vals else None
    if metric_field == "availability_pct":
        vals = [d["availability_pct"] for d in devices if d.get("availability_pct") is not None]
        return float(min(vals)) if vals else None  # unknown → hold
    return None


def _extract_per_rule_value(
    rule: AlertRule,
    group_result: float | list[Any] | dict[Any, Any] | None,
) -> float | None:
    """Extract a single numeric value from the group result for one rule."""
    if group_result is None:
        return None

    try:
        if rule.data_source == "ha_resource":
            if isinstance(group_result, list):
                # num_active = how many HA members are currently reporting. current_device_status
                # only returns members seen in the window, so len() is the live active count; a
                # dropped member shrinks it below the "< 2" threshold. (Was unhandled → always 0.0,
                # which perma-fired the rule.)
                if rule.metric_field == "num_active":
                    return float(len(group_result))
                if group_result and rule.metric_field.startswith("ha_member."):
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
                # New model: bare base metric (status / avg_latency / avg_packet_loss /
                # avg_jitter) + target_key = link number (1-based, from the link picker).
                # Legacy rules embed the link in metric_field ("status_link3") — still honored.
                if "_link" not in rule.metric_field and rule.target_key:
                    try:
                        link_idx = int(rule.target_key) - 1
                    except (TypeError, ValueError):
                        link_idx = 0
                vals = group_result.get(base_key, [0.0])
                if isinstance(vals, list):
                    # Out-of-range link → 0.0, NOT vals[0] (which silently evaluated the wrong link).
                    return float(vals[link_idx] if 0 <= link_idx < len(vals) else 0.0)
                return float(vals or 0.0)
            return 0.0

        if rule.data_source in ("vpn_ssl", "vpn_ipsec"):
            return _extract_vpn_usage(rule.metric_field, group_result)

        if rule.data_source == "interface_stats":
            if isinstance(group_result, dict):
                return _extract_interface_stats(
                    rule.metric_field, rule.target_key, rule.aggregation, group_result
                )
            return 0.0

        if rule.data_source == "device_uptime":
            if isinstance(group_result, dict):
                return _extract_device_uptime(rule.metric_field, rule.target_key, group_result)
            return None

        return None

    except Exception as e:
        logger.error("Extract failed for rule %s: %s", rule.id, e)
        return None


# ── State-machine helpers ───────────────────────────────────────


def _extract_vpn_usage(metric_field: str, group_result: Any) -> float:
    """Pick a value from the VPN usage summary {count, total_bytes, top_user_bytes}.

    Volume metrics: total_bytes (all active users) / top_user_bytes (heaviest user), in BYTES.
    Anything else (incl. the legacy active_*_users_count field names) → count. Also accepts a
    bare number for backward compatibility with the old count-only group result."""
    if isinstance(group_result, (int, float)):
        return float(group_result)
    if isinstance(group_result, dict):
        if metric_field in ("total_bytes", "top_user_bytes"):
            return float(group_result.get(metric_field, 0) or 0)
        return float(group_result.get("count", 0) or 0)
    return 0.0


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


def _resolve_target_name(
    data_source: str, site_name: str | None, target_key: str | None,
    group_result: Any = None,
) -> str | None:
    """Friendly name for a rule's target so notifications read "WAN LDP" not "16".

    interface_stats → interface label, sdwan_sla → SD-WAN link name (both static maps);
    device_uptime → the hostname, taken from the device_availability result the engine
    already fetched to evaluate the rule (no extra query), falling back to the IP.
    Returns None when there's nothing to name (blank target, or an unmapped key).
    """
    if not target_key:
        return None
    if data_source == "interface_stats":
        from app.opensearch.interface_stats import SITE_IFINDEX_MAP
        return SITE_IFINDEX_MAP.get(site_name or "", {}).get(str(target_key))
    if data_source == "sdwan_sla":
        from app.schemas.sdwan_resource_vpn import SITE_LINK_LABELS
        return SITE_LINK_LABELS.get(site_name or "", {}).get(f"link{target_key}")
    if data_source == "device_uptime":
        if isinstance(group_result, dict):
            for d in group_result.get("devices", []) or []:
                if d.get("device_key") == target_key:
                    return d.get("hostname") or str(target_key)
        return str(target_key)  # fall back to the IP
    return None


def _appid_filter_for(rule: AlertRule) -> dict | None:
    """The appid_flow scoping dict {app, protocol, port} for a rule, or None. Empty → None
    so unfiltered rules keep sharing the grouped query."""
    if rule.data_source != "appid_flow":
        return None
    f = getattr(rule, "appid_filter", None) or {}
    f = {k: v for k, v in f.items() if v not in (None, "")}
    return f or None


def _appid_sig(filt: dict | None) -> tuple | None:
    """Hashable signature of an appid filter for the group key (unfiltered rules → None so
    they share one query; each distinct filter gets its own query)."""
    if not filt:
        return None
    return tuple(sorted(filt.items()))


def _appid_filter_label(filt: dict | None) -> str | None:
    """Human label for a notification, e.g. "app=YouTube, port=443". None when unfiltered."""
    if not filt:
        return None
    parts = []
    if filt.get("app"):
        parts.append(f"app={filt['app']}")
    if filt.get("protocol"):
        parts.append(f"proto={filt['protocol']}")
    if filt.get("port") is not None:
        parts.append(f"port={filt['port']}")
    return ", ".join(parts) or None


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
    notify_queue: list[tuple[AlertRule, float, str, datetime]] | None = None,
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
        if state.state in ("INACTIVE", "RESOLVED"):
            # Re-arm on a fresh breach. RESOLVED must restart the sustain timer just like
            # INACTIVE — otherwise it was terminal: a rule that fired, resolved, then breached
            # AGAIN never re-fired (RESOLVED matched neither branch), so recurring problems
            # only ever alerted once.
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
                        notify_queue.append((rule, metric_value, "firing", state.last_fired_at or now))

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
                        notify_queue.append((rule, metric_value, "firing", state.last_fired_at or now))

                    await sse_broadcast("alert",
                        rule_id=rule.id,
                        rule_name=rule.name,
                        severity=rule.severity,
                        metric_value=metric_value,
                        fired_at=now.isoformat(),
                    )

    else:
        if state.state in ("FIRING", "PENDING"):
            was_firing = state.state == "FIRING"
            state.state = "RESOLVED"
            state.pending_since = None
            await db.flush()

            # Recovery notification — same channels that got the alert now get the
            # all-clear. Only for a rule that actually FIRED: a PENDING→RESOLVED rule
            # was still debouncing and never notified, so a "recovered" message would
            # reference an alert the operator never received.
            if was_firing and notify_queue is not None:
                notify_queue.append((rule, metric_value, "resolved", state.last_fired_at or now))

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
) -> tuple[float | None, bool, dict | None]:
    """Evaluate a composite rule's clauses and combine with AND/OR.

    Returns (metric_value, condition_met, driver). `driver` is the clause the
    notification should describe — the breaching clause with the largest value
    (or, if none breach, the largest-value clause) — as
    {metric_field, condition, threshold_value, value}. metric_value == driver's
    value, so the message's value and its limit come from the SAME clause (the
    top-level rule columns are only a mirror of clause[0], so rendering
    rule.threshold_value showed the wrong link's limit).
    Returns (None, False, None) if no clauses could be evaluated.

    If group_cache is provided, clauses reuse the pre-fetched OpenSearch
    results from the same cycle (§9.4). Without it, falls back to direct
    _run_group_query (test/standalone path).
    """
    if not rule.clauses:
        return None, False, None

    clauses_detail: list[dict] = []  # {value, breached, metric_field, condition, threshold_value}

    for clause in rule.clauses:
        ds_raw = clause.get("data_source")
        mf_raw = clause.get("metric_field")
        if not isinstance(ds_raw, str) or not isinstance(mf_raw, str):
            continue
        ds = ds_raw
        mf = mf_raw
        cond = clause.get("condition", ">")
        thresh = float(clause.get("threshold_value", 0.0) or 0.0)
        window = clause.get("evaluation_window_minutes", rule.evaluation_window_minutes)
        target_key = clause.get("target_key")
        aggregation = clause.get("aggregation", "avg")

        # §9.4: read from the cycle's pre-fetched cache when available (4-tuple key; composite
        # appid clauses use the unfiltered path → sig None).
        cache_key = (ds, rule.site_name, window, None)
        if group_cache is not None and cache_key in group_cache:
            group_result = group_cache[cache_key]
        else:
            group_result = await _run_group_query(ds, rule.site_name, window)
        if group_result is None:
            break  # one clause failed → whole rule fails

        val = _extract_per_rule_value_flat(ds, mf, group_result, target_key, aggregation)
        if val is None:
            break

        clauses_detail.append({
            "value": val,
            "breached": _check_condition(val, cond, thresh),
            "metric_field": mf,
            "condition": cond,
            "threshold_value": thresh,
            "data_source": ds,
            "target_key": target_key,
        })

    if len(clauses_detail) != len(rule.clauses):
        return None, False, None  # incomplete evaluation

    # Combine with AND/OR
    notify_when = rule.notify_when or "any"
    condition_met = all(c["breached"] for c in clauses_detail) if notify_when == "all" \
        else any(c["breached"] for c in clauses_detail)

    # Driver = the clause the message describes. Prefer breaching clauses so value+limit
    # match the reason it fired; if none breach (recovery), the largest-value clause.
    breaching = [c for c in clauses_detail if c["breached"]]
    driver = max(breaching or clauses_detail, key=lambda c: c["value"])
    return driver["value"], condition_met, driver


# ponytail: _extract_per_rule_value but takes flat data_source/metric_field instead of a rule object
def _extract_per_rule_value_flat(
    data_source: str, metric_field: str, group_result: float | list[Any] | dict[Any, Any] | None,
    target_key: str | None = None, aggregation: str = "avg",
) -> float | None:
    """Same logic as _extract_per_rule_value but accepts flat params (P5 composite).

    target_key + aggregation are carried per-clause so interface_stats (needs an ifIndex)
    and device_uptime (per-device) clauses resolve exactly like a single rule."""
    if group_result is None:
        return None
    try:
        if data_source == "ha_resource":
            if isinstance(group_result, list):
                if metric_field == "num_active":
                    return float(len(group_result))
                if group_result and metric_field.startswith("ha_member."):
                    field_name = metric_field.split(".", 1)[1]
                    return float(group_result[0].get(field_name, 0) or 0)
            return 0.0
        if data_source == "appid_flow":
            if isinstance(group_result, dict):
                return _extract_appid_flow(metric_field, group_result)
            return 0.0
        if data_source in ("vpn_ssl", "vpn_ipsec"):
            return _extract_vpn_usage(metric_field, group_result)
        if data_source == "sdwan_sla":
            if isinstance(group_result, dict):
                base_key, link_idx = _parse_sdwan_metric_field(metric_field)
                if "_link" not in metric_field and target_key:
                    try:
                        link_idx = int(target_key) - 1
                    except (TypeError, ValueError):
                        link_idx = 0
                vals = group_result.get(base_key, [0.0])
                if isinstance(vals, list):
                    return float(vals[link_idx] if 0 <= link_idx < len(vals) else 0.0)
                return float(vals or 0.0)
            return 0.0
        if data_source == "interface_stats":
            # Needs the clause's target_key (ifIndex); without one the interface can't be
            # picked → 0 so a mis-authored clause can't false-fire.
            if isinstance(group_result, dict):
                return _extract_interface_stats(metric_field, target_key, aggregation, group_result)
            return 0.0
        if data_source == "device_uptime":
            # target_key optional — blank = "any device at the site" reduction.
            # collector_gap is site-level (target_key ignored).
            if isinstance(group_result, dict):
                return _extract_device_uptime(metric_field, target_key, group_result)
            return None
        return None
    except (TypeError, ValueError, IndexError):
        return None
async def _flush_batch_notify(notify_queue: list[tuple[AlertRule, float, str, datetime]]) -> None:
    """Send batched notifications — one grouped message per channel (P7).

    Instead of one message per rule, aggregates all pending notifications
    into a single message per notification channel.

    §9.5: per-rule line rendering via Jinja2 SandboxedEnvironment, if the
    rule's template has a body_template. Rules without a template use the
    legacy hardcoded line format.
    """
    if not notify_queue:
        return

    now_str = datetime.now(_WIB).strftime("%d %b %Y %H:%M:%S WIB")

    # Adapt the header/subject to content: an all-clear batch reads as a recovery; any
    # still-firing rule keeps the alert framing.
    n_res = sum(1 for _, _, ev, _ in notify_queue if ev == "resolved")
    n_fire = len(notify_queue) - n_res
    title = "✅ NOD Recovery Summary" if n_res and not n_fire else "🛑 NOD Alert Summary"

    # Build aggregated message
    lines = [f"{title} — {now_str}", "━" * 35]
    sev_emoji = {"CRITICAL": "🔴", "WARNING": "⚠️", "INFO": "ℹ️"}

    # §9.5: pre-fetch all referenced templates in one query
    template_ids = {r.template_id for r, _, _, _ in notify_queue if r.template_id}
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

    # §11.1: pre-fetch the message templates for the per-rule line. Resolution order is
    # assigned (active) template → active default template → AlertTemplate body →
    # hardcoded, so different rules can use different templates (multi-use) and rules
    # with none fall to the admin-chosen default. Only ACTIVE templates render; an
    # inactive/retired one is skipped and the rule falls through. Prefer line_template
    # (the batch line), fall back to body_template.
    nt_ids = {r.notification_template_id for r, _, _, _ in notify_queue if r.notification_template_id}
    nt_line: dict[str, str] = {}
    default_line: str | None = None
    try:
        from app.db.session import AsyncSessionLocal
        async with AsyncSessionLocal() as tdb:
            if nt_ids:
                result = await tdb.execute(
                    select(NotificationTemplate.id, NotificationTemplate.line_template,
                           NotificationTemplate.body_template)
                    .where(NotificationTemplate.id.in_(nt_ids))
                    .where(NotificationTemplate.is_active == True)  # noqa: E712
                )
                nt_line = {row[0]: (row[1] or row[2]) for row in result.all() if (row[1] or row[2])}
            drow = (await tdb.execute(
                select(NotificationTemplate.line_template, NotificationTemplate.body_template)
                .where(NotificationTemplate.is_default == True)  # noqa: E712
                .where(NotificationTemplate.is_active == True)  # noqa: E712
                .limit(1)
            )).first()
            if drow:
                default_line = drow[0] or drow[1]
    except Exception as e:
        logger.error("Failed to fetch notification templates: %s", e)

    for rule, mv, event, fired_orig in notify_queue:
        sev = sev_emoji.get(rule.severity, "🔔")
        resolved = event == "resolved"
        # Composite: describe the clause that actually fired, not the top-level clause[0]
        # mirror (a composite's rule.threshold_value is a stale copy of clause[0], so a
        # packet-loss-5% rule showed "limit > 100"). Single rules: driver is absent → use
        # the rule's own columns.
        drv = getattr(rule, "_fired_clause", None)
        eff_metric_field = drv["metric_field"] if drv else rule.metric_field
        eff_condition = drv["condition"] if drv else rule.condition
        eff_threshold = drv["threshold_value"] if drv else rule.threshold_value
        # Interface throughput is Mbps in BOTH threshold modes (absolute, or "% of link max"
        # where threshold_value = link_max × %). So metric_value + threshold_value are Mbps —
        # the seeded template used to print them with a "%" (only right by luck when link
        # max = 100). Expose link_max + a real utilization% so a template renders either unit
        # honestly. Non-throughput rules → link_max None → the % vars are None (StrictUndefined-safe).
        link_max = getattr(rule, "link_max_mbps", None) or None
        utilization_pct = round(mv / link_max * 100, 1) if link_max else None
        threshold_pct = round(eff_threshold / link_max * 100, 1) if link_max else None
        # Friendly target name (interface / SD-WAN link / device / appid filter) from eval time.
        target_name = getattr(rule, "_target_name", None)
        eff_target_key = drv.get("target_key") if drv else rule.target_key
        # appid_flow scoping, broken out so a template can show app / protocol / port separately.
        appid_f = _appid_filter_for(rule) or {}
        filter_app = appid_f.get("app")
        filter_proto = appid_f.get("protocol")
        filter_port = appid_f.get("port")
        filter_label = _appid_filter_label(appid_f or None)
        # VPN capacity extras — count + consumed volume in MB, so a template can show the whole
        # picture regardless of which metric (count / total / top-user) the rule fired on.
        vpn = getattr(rule, "_vpn_usage", None) or {}
        vpn_active_users = vpn.get("count")
        vpn_total_mb = round(vpn["total_bytes"] / 1_000_000, 1) if vpn.get("total_bytes") is not None else None
        vpn_top_user_mb = round(vpn["top_user_bytes"] / 1_000_000, 1) if vpn.get("top_user_bytes") is not None else None
        # The fired value in MB/GB when the metric is a byte volume (total_bytes/top_user_bytes).
        is_vol = eff_metric_field in ("total_bytes", "top_user_bytes")
        metric_mb = round(mv / 1_000_000, 1) if is_vol else None
        threshold_mb = round(eff_threshold / 1_000_000, 1) if is_vol else None
        # Original first-trigger time (stable across 30-min reminders); sent_at is this
        # message's time. A reminder keeps fired_at = the original fire, so the operator
        # sees how long the still-unresolved issue has been firing.
        fired_str = (fired_orig.astimezone(_WIB).strftime("%d %b %Y %H:%M:%S WIB")
                     if fired_orig else now_str)
        # Resolution: assigned (active) template → active default → AlertTemplate body →
        # hardcoded. nt_line only holds active templates, so an inactive assignment falls
        # through to the default here.
        tmpl_text = (
            (nt_line.get(rule.notification_template_id) if rule.notification_template_id else None)
            or default_line
            or (template_body.get(rule.template_id) if rule.template_id else None)
        )
        # A recovery only renders through the template if that template branches on
        # `event` ({% if event == 'resolved' %}…) — otherwise an alert-worded template
        # would describe a resolved rule as if it were still firing. Templates that don't
        # mention `event` fall to the event-aware hardcoded line below. ponytail: substring
        # test, not a parse — mentioning `event` is how a template opts into recovery text.
        if tmpl_text and (not resolved or "event" in tmpl_text):
            # The seeded AlertTemplates use flat var names ({{ name }}, {{ metric_value }});
            # §11.1 NotificationTemplates use nested {{ rule.* }}. Provide both so either renders.
            ctx = {
                "rule": {
                    "name": rule.name,
                    "severity": rule.severity,
                    "site_name": rule.site_name,
                    "metric_field": eff_metric_field,
                    "condition": eff_condition,
                    "threshold_value": eff_threshold,
                },
                # Flat aliases for the seeded AlertTemplates.
                "name": rule.name,
                "severity": rule.severity,
                "site_name": rule.site_name,
                "metric_field": eff_metric_field,
                "condition": eff_condition,
                "threshold_value": eff_threshold,
                "threshold": eff_threshold,
                "metric_value": mv,
                # Interface throughput extras (None for other sources / absolute mode):
                "link_max_mbps": link_max,
                "utilization_pct": utilization_pct,
                "threshold_pct": threshold_pct,
                # Friendly target: interface name / SD-WAN link name / device hostname /
                # appid filter label.
                "target_name": target_name,
                "target_key": eff_target_key,
                # appid_flow scoping (None when not an appid rule or unfiltered):
                "filter_app": filter_app,
                "filter_proto": filter_proto,
                "filter_port": filter_port,
                "filter_label": filter_label,
                # VPN capacity (None for non-VPN rules):
                "vpn_active_users": vpn_active_users,
                "vpn_total_mb": vpn_total_mb,
                "vpn_top_user_mb": vpn_top_user_mb,
                "metric_mb": metric_mb,
                "threshold_mb": threshold_mb,
                "data_source": rule.data_source,
                "aggregation": rule.aggregation,
                "fired_at": fired_str,
                "sent_at": now_str,
                # Fire vs resolve — lets one template render both ({% if event == 'resolved' %}).
                "event": event,
                "event_label": "Resolved" if resolved else "Firing",
            }
            try:
                lines.append(_render_template(tmpl_text, ctx))
                continue
            except Exception as e:
                # ponytail: render error falls back to hardcoded line — never
                # lose the alert to a template typo
                logger.error("Template render failed for rule %s: %s — falling back to hardcoded", rule.id, e)
        # Escape name/site — a rule name with a stray '<' must not break HTML mode if the
        # batch turns out HTML (from another rule's template). Plain mode is unaffected.
        nm = html.escape(rule.name)
        st = html.escape(rule.site_name or "—")
        # Include the current value AND the configured threshold so even the template-less
        # fallback answers "what value hit, and what was the limit".
        if resolved:
            lines.append(f"✅ [RESOLVED] {nm} @ {st}: recovered (now {mv}, limit {eff_condition} {eff_threshold})")
        else:
            lines.append(f"{sev} [{rule.severity}] {nm} @ {st}: {mv} (limit {eff_condition} {eff_threshold})")

    # If any rule rendered an HTML template, send the rich blocks as one HTML message
    # (grouped, matching the Grafana style) and drop the plain summary header. Otherwise
    # the classic plain-text digest. `outputs` are the per-rule strings after header+rule.
    outputs = lines[2:]
    is_html = any("<b>" in s for s in outputs)
    if is_html:
        body = "\n\n".join(outputs)
        parse_mode: str | None = "HTML"
    else:
        body = "\n".join(lines)
        parse_mode = None

    # Load channels and send — only the ones the firing rules actually want
    # (§9.3: was sending to every enabled channel; now respects rule.notify_channels).
    try:
        from app.services.notifier_helper import send_alert, load_channel_configs

        fired_channels = {ch for rule, _, _, _ in notify_queue for ch in rule.notify_channels}

        # load_channel_configs DECRYPTS the secrets (bot token etc.) and returns only enabled
        # channels. The batch path used to pass the raw NotificationConfig.config straight from
        # the DB — still ENCRYPTED — so Telegram received a garbage token, rejected every send,
        # and the error was swallowed: alerts fired but no message ever arrived.
        db_configs = await load_channel_configs()
        for channel in fired_channels:
            cfg = db_configs.get(channel)
            if not cfg:
                continue  # channel not enabled/configured in Settings → nothing to send to
            try:
                await send_alert(channel, cfg, subject=title, body=body, parse_mode=parse_mode)
            except Exception as e:
                logger.error("Batch notify failed for %s: %s", channel, e)
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
            # Group key is (ds, site, window, appid_sig): appid_flow rules with a distinct
            # app/protocol/port filter can't share the whole-path query, so the filter joins
            # the key. Unfiltered rules → sig None → keep sharing one query.
            notify_queue: list[tuple[AlertRule, float, str, datetime]] = []
            groups: dict[tuple, list[AlertRule]] = defaultdict(list)
            group_filters: dict[tuple, dict | None] = {}
            for rule in single_rules:
                filt = _appid_filter_for(rule)
                key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes, _appid_sig(filt))
                groups[key].append(rule)
                group_filters[key] = filt

            # §9.4: also pre-fetch for composite clause keys, sharing the
            # cache with singles that happen to share a (ds, site, window) tuple.
            # Composite appid clauses use the unfiltered path (sig None) for now.
            for rule in composite_rules:
                for clause in (rule.clauses or []):
                    ds = clause.get("data_source")
                    if not ds:
                        continue
                    window = clause.get("evaluation_window_minutes", rule.evaluation_window_minutes)
                    groups.setdefault((ds, rule.site_name, window, None), [])

            group_cache: dict[tuple, float | list | dict | None] = {}
            for key in groups:
                ds, site, window, _sig = key
                group_cache[key] = await _run_group_query(ds, site, window, group_filters.get(key))

            for rule in single_rules:
                try:
                    key = (rule.data_source, rule.site_name, rule.evaluation_window_minutes,
                           _appid_sig(_appid_filter_for(rule)))
                    group_result = group_cache.get(key)
                    metric_value = _extract_per_rule_value(rule, group_result)
                    if metric_value is None:
                        await _mark_held(rule, db)
                        continue
                    condition_met = _check_condition(
                        metric_value, rule.condition, rule.threshold_value
                    )
                    # Transient: friendly target name for the notification (reuses group_result
                    # for the device hostname — no extra query). appid_flow has no target_key;
                    # its "target" is the app/protocol/port filter, so surface that instead.
                    rule._target_name = (
                        _appid_filter_label(_appid_filter_for(rule))
                        if rule.data_source == "appid_flow"
                        else _resolve_target_name(rule.data_source, rule.site_name, rule.target_key, group_result)
                    )
                    # VPN usage summary {count,total_bytes,top_user_bytes} for the notification.
                    rule._vpn_usage = (
                        group_result if rule.data_source in ("vpn_ssl", "vpn_ipsec")
                        and isinstance(group_result, dict) else None
                    )
                    await _advance_state_machine(rule, metric_value, condition_met, db, notify_queue)
                except Exception as e:
                    logger.error("Error evaluating rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

            # ── 2. Composite rules (per-rule evaluation) ──
            for rule in composite_rules:
                try:
                    metric_value, condition_met, driver = await _evaluate_composite_rule(rule, group_cache)
                    if metric_value is None:
                        await _mark_held(rule, db)
                        continue
                    # Transient (not persisted): tells the notifier which clause fired so the
                    # message renders that clause's threshold, not the top-level clause[0] mirror.
                    rule._fired_clause = driver
                    # Name the driver clause's target (static maps → no query; device falls back to IP).
                    rule._target_name = _resolve_target_name(
                        driver.get("data_source"), rule.site_name, driver.get("target_key")
                    )
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
