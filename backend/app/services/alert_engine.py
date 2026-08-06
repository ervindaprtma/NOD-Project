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


def sample_render_ctx(
    *, name: str = "Test Rule", severity: str = "WARNING", site_name: str = "Site_FGT-DC",
    metric_field: str = "iface.throughput_mbps", condition: str = ">",
    threshold_value: float = 80.0, metric_value: float = 95.5, data_source: str = "interface_stats",
    aggregation: str = "max", fired_at: str = "01 Jan 2026 12:00:00 WIB",
    sent_at: str | None = None, event: str = "firing",
) -> dict:
    """A fully-populated preview context — EVERY variable the fire-time notifier
    (_flush_batch_notify) provides, with sample values. Preview endpoints must use this so a
    template that renders in preview also renders when it fires (and vice-versa): the two used
    to be hand-maintained copies and drifted, 422-ing previews of templates that used newer
    vars. Update this whenever _flush_batch_notify's ctx gains a key."""
    rc = {"name": name, "severity": severity, "site_name": site_name,
          "metric_field": metric_field, "condition": condition, "threshold_value": threshold_value}
    is_vol = metric_field in ("total_bytes", "top_user_bytes")
    metric_label, metric_unit = _metric_label_unit(metric_field)
    if (metric_field or "").startswith("app."):   # scan mode → direction label, matches fire-time
        _pt, _pv, _mk, _dir = _scan_metric_parse(metric_field)
        metric_label, metric_unit = _dir, "Mbps"
    return {
        "rule": rc, **rc, "threshold": threshold_value,
        "metric_label": metric_label, "metric_unit": metric_unit,
        "metric_value": metric_value, "data_source": data_source, "aggregation": aggregation,
        "fired_at": fired_at, "sent_at": sent_at if sent_at is not None else fired_at,
        "event": event, "event_label": "Resolved" if event == "resolved" else "Firing",
        # interface throughput extras
        "link_max_mbps": 100.0, "utilization_pct": 63.0, "threshold_pct": 50.0,
        # friendly target + appid scope (samples so target/appid templates render richly)
        "target_name": "WAN LDP", "target_key": "16",
        "filter_app": "YouTube", "filter_proto": "TCP", "filter_port": 443,
        "filter_app_not": "Teams", "filter_proto_not": "UDP", "filter_port_not": 445,
        "filter_label": "app=YouTube, port=443, not app=Teams",
        # Scan mode (metric_field "app.<path>.<metric>"): offending app(s) + who/where/whom + volume
        "scan_apps": [{"app": "YouTube", "download_mbps": 87.3}, {"app": "Zoom", "download_mbps": 61.0}],
        "scan_apps_text": "YouTube (87.3 Mbps), Zoom (61.0 Mbps)",
        "scan_volume_text": "11.2 GB",
        "scan_src_ips_text": "192.168.1.200 (8.1 GB), 192.168.1.87 (2.4 GB)",
        "scan_egress_text": "WAN-LinkNet",
        "scan_dst_orgs_text": "Google LLC, Fastly, Inc.",
        # VPN capacity (metric_mb/threshold_mb only meaningful for a byte-volume metric)
        "vpn_active_users": 7, "vpn_total_mb": 5500.0, "vpn_top_user_mb": 2100.0,
        "vpn_top_user": "someone",
        "vpn_over_users": [{"user": "someone", "mb": 2900.1}, {"user": "vpn-user-2", "mb": 2100.0}],
        "vpn_over_users_text": "someone (2900.1 MB), vpn-user-2 (2100.0 MB)",
        "metric_mb": round(metric_value / 1_000_000, 1) if is_vol else None,
        "threshold_mb": round(threshold_value / 1_000_000, 1) if is_vol else None,
        # VPN session-monitor events (kind="session"): the per-event fields.
        "vpn_type": "SSL VPN", "vpn_user": "someone",
        "remote_ip": "203.0.113.5", "active_ip": "10.212.134.8", "device": "FG_DC_GTN-01",
        "started_at": fired_at, "ended_at": "—", "duration": "—",
        "bytes_in": 524_000_000, "bytes_out": 88_000_000,
        "bytes_in_h": "524.0 MB", "bytes_out_h": "88.0 MB", "bytes_total_h": "612.0 MB",
        # Device reboot-monitor events (kind="reboot"): the per-event fields.
        "device_ip": "10.80.150.1", "reboot_at": fired_at,
        "downtime_seconds": 150, "downtime": "2m 30s",
        "new_uptime_seconds": 240, "new_uptime": "4m",
        "prev_uptime_seconds": 3_218_400, "prev_uptime": "37d 6h",
    }


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
    scan: dict | None = None,
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

                if scan is not None:
                    # Scan mode: per-APP throughput list (top-N by bytes), for "monitor all apps".
                    ex = scan.get("excludes") or {}
                    result = await tf_qb.appid_flow_app_scan(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC",
                        path=scan.get("path") or "", top_n=scan.get("top_n") or 10,
                        app_not=ex.get("app_not") or "", protocol_not=ex.get("protocol_not") or "",
                        port_not=ex.get("port_not"),
                    )
                else:
                    # Per-path dict: {internet, inbound-vip, inter-site, intra-lan, _wan}
                    # each with *_mbps / *_bytes. Extractor selects node + metric. An optional
                    # appid_filter narrows every path to one app/protocol/port.
                    af = appid_filter or {}
                    result = await tf_qb.appid_flow_alert_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name or "Site_FGT-DC",
                        app_filter=af.get("app") or "", protocol=af.get("protocol") or "",
                        dst_port=af.get("port"),
                        app_not=af.get("app_not") or "", protocol_not=af.get("protocol_not") or "",
                        port_not=af.get("port_not"),
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


# ── appid_flow "scan all apps" mode (metric_field prefixed "app.") ──
# Monitor EVERY application on a path and fire when ANY single app's speed exceeds the
# threshold, naming the offender. metric_field = "app.<path>.<metric>", e.g.
# "app.internet.download_mbps". Knobs (top_n, min_mbps, exclude twins) live in appid_filter.

def _scan_metric_parse(metric_field: str) -> tuple[str, str | None, str, str]:
    """'app.internet.download_mbps' → (path_token, path_value|None, metric_key, direction_label).

    path_value is the flow.traffic.path term to filter on, or None for all-paths ("wan").
    metric_key indexes appid_flow_app_scan's per-app dict; direction_label is the friendly
    metric name for the notification ("Download"/"Upload"/"Total")."""
    parts = (metric_field or "").split(".")
    path_token = parts[1] if len(parts) > 1 else "internet"
    metric_key = parts[2] if len(parts) > 2 else "download_mbps"
    path_value = _APPID_PATH_KEYS.get(path_token)
    if path_value == "_wan":
        path_value = None  # the _wan node is the all-paths aggregate → no path filter
    direction = metric_key.split("_")[0].title() or "Download"
    return path_token, path_value, metric_key, direction


def _extract_app_scan(rule: AlertRule, group_result: Any) -> float:
    """Reduce appid_flow_app_scan's per-app list to ONE scalar for the shared state machine.

    Returns the driver app's Mbps (worst offender for ">", best for "<"), so the loop's own
    _check_condition(value, ...) yields "ANY app breaches". Stamps rule._scan_apps = the top 1–2
    breaching apps for the notification. min_mbps floors out tiny apps so churn can't flap the rule."""
    _pt, _pv, metric_key, _dir = _scan_metric_parse(rule.metric_field)
    if not isinstance(group_result, list):
        rule._scan_apps = []
        return 0.0
    af = getattr(rule, "appid_filter", None) or {}
    try:
        min_mbps = float(af.get("min_mbps") or 0.0)
    except (TypeError, ValueError):
        min_mbps = 0.0
    val = lambda a: float(a.get(metric_key) or 0.0)  # noqa: E731
    apps = [a for a in group_result if val(a) >= min_mbps]
    if not apps:
        rule._scan_apps = []
        return 0.0
    low = rule.condition in ("<", "<=")
    driver = min(apps, key=val) if low else max(apps, key=val)
    breaching = [a for a in apps if _check_condition(val(a), rule.condition, rule.threshold_value)]
    breaching.sort(key=val, reverse=not low)
    rule._scan_apps = breaching[:2]
    return val(driver)


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
            if (rule.metric_field or "").startswith("app."):   # scan-all-apps mode
                return _extract_app_scan(rule, group_result)
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


# Human label + unit for a driver clause's metric_field, so a notification names the metric
# that ACTUALLY fired instead of the template hardcoding one — a latency-driven SD-WAN alert
# was rendering "Packet Loss: 142% (%)" because the composite driver can be any clause but the
# ctx exposed no metric name/unit. Composite SD-WAN clauses arrive agg-prefixed (avg_/max_…).
_METRIC_UNIT_HINTS = (
    ("packet_loss", "%"), ("latency", "ms"), ("jitter", "ms"),
    ("_mbps", "Mbps"), ("throughput", "Mbps"), ("_bytes", "bytes"), ("_pct", "%"),
)


def _metric_label_unit(metric_field: str) -> tuple[str, str]:
    """('avg_packet_loss') → ('Packet Loss', '%'). Strips the aggregation prefix, maps a unit
    by substring, and title-cases the rest. Unit '' when unknown (StrictUndefined-safe: the
    key is always present, just empty)."""
    base = re.sub(r"^(avg|max|min|sum|count)_", "", (metric_field or "").strip())
    unit = next((u for key, u in _METRIC_UNIT_HINTS if key in base), "")
    label = base.replace("_", " ").replace(".", " ").strip().title() or "Metric"
    return label, unit


def _clause_severity(c: dict) -> float:
    """How stressed a composite clause is relative to ITS OWN limit, normalized so
    clauses of different units/scales compare fairly. Picks the driver clause a message
    describes — the same clause at fire (most over its limit) and at resolve (closest to
    it). Without this the resolve path took the largest RAW value, so a recovered
    packet-loss-5% clause (now 2) lost to a latency clause (26.53, limit 100) and the
    message borrowed 'limit > 100'. Direction-aware; guards divide-by-zero."""
    val = c.get("value") or 0.0
    thr = c.get("threshold_value") or 0.0
    op = c.get("condition", ">")
    if op in ("<", "<="):          # lower is worse → severity rises as value drops below limit
        return (thr / val) if val else float("inf")
    if op == "==":                 # equality is binary — breached or not
        return 1.0 if c.get("breached") else 0.0
    return (val / thr) if thr else float("inf")   # ">", ">=": higher is worse


def _resolve_target_name(
    data_source: str | None, site_name: str | None, target_key: str | None,
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


def _scan_descriptor_for(rule: AlertRule) -> dict | None:
    """Scan-mode descriptor {path, top_n, excludes} for an appid_flow rule whose metric_field is
    prefixed "app.", else None. Drives both the group query (which fn to call) and the cache key."""
    if rule.data_source != "appid_flow" or not (rule.metric_field or "").startswith("app."):
        return None
    _pt, path_value, _mk, _dir = _scan_metric_parse(rule.metric_field)
    af = getattr(rule, "appid_filter", None) or {}
    try:
        top_n = int(af.get("top_n") or 10)
    except (TypeError, ValueError):
        top_n = 10
    excludes = {k: af[k] for k in ("app_not", "protocol_not", "port_not") if af.get(k) not in (None, "")}
    return {"path": path_value, "top_n": max(1, top_n), "excludes": excludes}


def _group_key(rule: AlertRule) -> tuple:
    """Per-cycle group-query cache key. Scan rules get a distinct ("scan", path, top_n, excl-sig)
    slot so they NEVER collide with an unfiltered per-path appid rule (whose result is a dict, not
    a per-app list) — two scan rules of the same shape still share one query."""
    ds, site, window = rule.data_source, rule.site_name, rule.evaluation_window_minutes
    scan = _scan_descriptor_for(rule)
    if scan:
        return (ds, site, window, ("scan", scan["path"], scan["top_n"],
                                   _appid_sig(scan["excludes"] or None)))
    return (ds, site, window, _appid_sig(_appid_filter_for(rule)))


def _appid_filter_label(filt: dict | None) -> str | None:
    """Human label for a notification, e.g. "app=YouTube, port=443, not app=Teams". None when
    unfiltered. Exclude twins render as "not app=…" so an operator reads the rule's real scope."""
    if not filt:
        return None
    parts = []
    if filt.get("app"):
        parts.append(f"app={filt['app']}")
    if filt.get("protocol"):
        parts.append(f"proto={filt['protocol']}")
    if filt.get("port") is not None:
        parts.append(f"port={filt['port']}")
    if filt.get("app_not"):
        parts.append(f"not app={filt['app_not']}")
    if filt.get("protocol_not"):
        parts.append(f"not proto={filt['protocol_not']}")
    if filt.get("port_not") is not None:
        parts.append(f"not port={filt['port_not']}")
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
                            # Composite: the exact clause that fired, so the recovery message
                            # describes the SAME metric instead of re-picking at resolve time.
                            "driver": getattr(rule, "_fired_clause", None),
                            # Scan (app.* mode): the offending apps + who/where/whom detail, so the
                            # recovery message can name them even though no app is breaching anymore.
                            "scan_apps": getattr(rule, "_scan_apps", None) or None,
                            "scan_detail": getattr(rule, "_scan_detail", None) or None,
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
            # Per-rule re-notify cadence: None → global default; 0 → notify once (no reminders).
            interval_min = rule.renotify_interval_minutes
            if interval_min is None:
                interval_min = settings.ALERT_RENOTIFY_INTERVAL_MINUTES
            if state.last_notified_at and interval_min > 0:
                renotify_seconds = interval_min * 60
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

            # Composite recovery must describe the clause that FIRED (from the AlertLog), not
            # whatever clause is now nearest its limit — else a packet-loss alert recovers as a
            # latency message. Re-find that clause in this cycle's reads and report its current
            # value; fall back to the cycle driver if the rule's clauses changed since firing.
            resolved_value = metric_value
            fired_driver = (alert_log.rule_snapshot or {}).get("driver") if alert_log else None
            if fired_driver:
                for c in getattr(rule, "_clauses_detail", None) or []:
                    if (c.get("metric_field") == fired_driver.get("metric_field")
                            and c.get("target_key") == fired_driver.get("target_key")):
                        # Adopt the fired clause's identity AND its current value TOGETHER, so the
                        # message's label/limit and value always describe the same clause. If no
                        # clause matches (rule edited since firing), leave _fired_clause as this
                        # cycle's driver and resolved_value as its value — still self-consistent.
                        rule._fired_clause = fired_driver
                        resolved_value = c["value"]
                        break

            # Scan mode: rehydrate the offending apps + detail from the fire snapshot so the
            # recovery message names them (no app is breaching now → a fresh query would find none).
            snap = (alert_log.rule_snapshot or {}) if alert_log else {}
            if snap.get("scan_apps") is not None:
                rule._scan_apps = snap.get("scan_apps") or []
                rule._scan_detail = snap.get("scan_detail") or None
                # Clean-value parity: this cycle's driver is some unrelated top app (nothing is
                # breaching now). Name the app that FIRED and report the value it fired at, so
                # target_name / metric_value / scan_apps_text all describe the SAME app ("YouTube
                # was 87.3 Mbps"), never a mismatched current number or a filter-label target.
                if rule._scan_apps:
                    _pt, _pv, _mk, _dir = _scan_metric_parse(rule.metric_field)
                    resolved_value = float(rule._scan_apps[0].get(_mk) or resolved_value)
                    rule._target_name = rule._scan_apps[0].get("app") or getattr(rule, "_target_name", None)

            # Recovery notification — same channels that got the alert now get the
            # all-clear. Only for a rule that actually FIRED: a PENDING→RESOLVED rule
            # was still debouncing and never notified, so a "recovered" message would
            # reference an alert the operator never received.
            if was_firing and notify_queue is not None:
                notify_queue.append((rule, resolved_value, "resolved", state.last_fired_at or now))

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
    # match the reason it fired; if none breach (recovery), the clause CLOSEST to its own
    # limit — normalized per-clause so a recovered packet-loss-5% rule keeps describing
    # packet loss instead of borrowing a higher-raw-value latency clause's "limit > 100".
    breaching = [c for c in clauses_detail if c["breached"]]
    driver = max(breaching or clauses_detail, key=_clause_severity)
    # Keep this cycle's per-clause reads so the RESOLVE path can re-find the clause that
    # actually fired (stored in the AlertLog) and report ITS current value — otherwise a
    # recovery re-picks the nearest-limit clause and describes a different metric than the alert.
    rule._clauses_detail = clauses_detail
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
        # Friendly name + unit of the clause that fired, so a template renders "Latency: 142 ms"
        # instead of hardcoding "Packet Loss: 142 %" for whatever clause happened to drive it.
        metric_label, metric_unit = _metric_label_unit(eff_metric_field)
        # Scan mode (metric_field "app.<path>.<metric>"): the friendly metric is the direction
        # (Download/Upload/Total) in Mbps — _metric_label_unit's dotted-path title-case is wrong here.
        _is_scan = rule.data_source == "appid_flow" and (rule.metric_field or "").startswith("app.")
        _scan_metric_key = "download_mbps"
        if _is_scan:
            _pt, _pv, _scan_metric_key, _dir = _scan_metric_parse(rule.metric_field)
            metric_label, metric_unit = _dir, "Mbps"
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
        filter_app_not = appid_f.get("app_not")
        filter_proto_not = appid_f.get("protocol_not")
        filter_port_not = appid_f.get("port_not")
        filter_label = _appid_filter_label(appid_f or None)
        # VPN capacity extras — count + consumed volume in MB, so a template can show the whole
        # picture regardless of which metric (count / total / top-user) the rule fired on.
        vpn = getattr(rule, "_vpn_usage", None) or {}
        vpn_active_users = vpn.get("count")
        vpn_total_mb = round(vpn["total_bytes"] / 1_000_000, 1) if vpn.get("total_bytes") is not None else None
        vpn_top_user_mb = round(vpn["top_user_bytes"] / 1_000_000, 1) if vpn.get("top_user_bytes") is not None else None
        # The fired value in MB/GB when the metric is a byte volume (total_bytes/top_user_bytes).
        is_vol = eff_metric_field in ("total_bytes", "top_user_bytes")
        # WHO consumed it: the per-user breakdown, so a template can name the offending users
        # instead of only counting them. vpn_over_users = the users whose own usage exceeds the
        # rule's threshold (a per-user metric); vpn_top_user = the single heaviest.
        vpn_top_users = vpn.get("top_users") or []
        vpn_top_user = vpn_top_users[0]["user"] if vpn_top_users else None
        _over = [u for u in vpn_top_users if u.get("bytes", 0) > eff_threshold] if is_vol else []
        vpn_over_users = [{"user": u["user"], "mb": round(u["bytes"] / 1_000_000, 1)} for u in _over]
        vpn_over_users_text = ", ".join(f'{u["user"]} ({u["mb"]} MB)' for u in vpn_over_users) or None
        # Scan mode: name the offending app(s) + who used them (source IPs), where they went out
        # (egress interface), and to whom (dest AS org), plus the per-APP traffic volume. Detail is
        # focused on the driver (worst) app; scan_apps_text still lists the top 1–2 by speed. All
        # vars are always present (None for non-scan) so StrictUndefined never trips a template.
        _scan_apps = getattr(rule, "_scan_apps", None) or []
        _scan_detail = getattr(rule, "_scan_detail", None) or {}
        scan_apps_text = None
        scan_volume_text = scan_src_ips_text = scan_egress_text = scan_dst_orgs_text = None
        if _scan_apps:
            scan_apps_text = ", ".join(
                f'{a.get("app")} ({round(float(a.get(_scan_metric_key) or 0.0), 1)} Mbps)'
                for a in _scan_apps) or None
            _driver = _scan_apps[0].get("app")
            _d = _scan_detail.get(_driver) if isinstance(_scan_detail, dict) else None
            if _d:
                scan_volume_text = _fmt_bytes(_d.get("total_bytes") or 0)
                scan_src_ips_text = ", ".join(
                    f'{s.get("ip")} ({_fmt_bytes(s.get("bytes") or 0)})'
                    for s in (_d.get("src_ips") or [])) or None
                scan_egress_text = ", ".join(_d.get("egress") or []) or None
                scan_dst_orgs_text = ", ".join(_d.get("dst_orgs") or []) or None
        metric_mb = round(mv / 1_000_000, 1) if is_vol else None
        threshold_mb = round(eff_threshold / 1_000_000, 1) if is_vol else None
        # Original first-trigger time (stable across 30-min reminders); sent_at is this
        # message's time. A reminder keeps fired_at = the original fire, so the operator
        # sees how long the still-unresolved issue has been firing.
        fired_str = (fired_orig.astimezone(_WIB).strftime("%d %b %Y %H:%M:%S WIB")
                     if fired_orig else now_str)
        # Resolution: assigned (active) template → the rule's own AlertTemplate body →
        # active default → hardcoded. The AlertTemplate (built from the rule's template) is
        # more specific than the generic active default, so it must win — otherwise activating
        # the default would shadow every AlertTemplate's tailored body. nt_line only holds
        # active templates, so an inactive assignment falls through here.
        tmpl_text = (
            (nt_line.get(rule.notification_template_id) if rule.notification_template_id else None)
            or (template_body.get(rule.template_id) if rule.template_id else None)
            or default_line
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
                # Metric name + unit of the fired clause (SD-WAN composite: Packet Loss/Latency/Jitter).
                "metric_label": metric_label,
                "metric_unit": metric_unit,
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
                # appid exclude scope (None when the rule has no exclude twin):
                "filter_app_not": filter_app_not,
                "filter_proto_not": filter_proto_not,
                "filter_port_not": filter_port_not,
                "filter_label": filter_label,
                # Scan mode (None for non-scan rules): offending app(s) + who/where/whom + volume.
                "scan_apps": _scan_apps or None,
                "scan_apps_text": scan_apps_text,
                "scan_volume_text": scan_volume_text,
                "scan_src_ips_text": scan_src_ips_text,
                "scan_egress_text": scan_egress_text,
                "scan_dst_orgs_text": scan_dst_orgs_text,
                # VPN capacity (None for non-VPN rules):
                "vpn_active_users": vpn_active_users,
                "vpn_total_mb": vpn_total_mb,
                "vpn_top_user_mb": vpn_top_user_mb,
                # WHO exceeded: heaviest user's name, the over-threshold users [{user, mb}],
                # and a ready-joined string (the sandbox has no `join` filter).
                "vpn_top_user": vpn_top_user,
                "vpn_over_users": vpn_over_users,
                "vpn_over_users_text": vpn_over_users_text,
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


# ── VPN Session Monitor (kind="session") ────────────────────────
# Event-on-state-change, not threshold: each poll snapshots the currently-active VPN
# sessions and diffs against the previous snapshot to emit connect/disconnect alerts.
# Reuses the VPN Sessions page's active queries so what alerts == what the page shows.

async def _fetch_active_vpn_sessions(rule: AlertRule) -> dict[str, dict] | None:
    """Currently-active VPN sessions for a session rule, as {username: {remote_ip, active_ip,
    device}}. Presence window = the rule's evaluation window (default 5min = the session gap):
    a user seen within it is "connected". target_key is an optional username glob (e.g.
    "admin*"); blank = all users. Returns None on a read failure so the caller HOLDS instead
    of treating a failed query as "everyone disconnected"."""
    now_ms = int(_time.time() * 1000)
    window = max(rule.evaluation_window_minutes or 5, 1)
    gte_ms = now_ms - window * 60 * 1000
    out: dict[str, dict] = {}
    try:
        if rule.data_source == "vpn_ssl":
            from app.opensearch import sslvpn as q
            site = q.sslvpn_measurement_for_site(rule.site_name)
            for u in await q.active_sslvpn_users(gte_ms=gte_ms, lte_ms=now_ms, site_name=site):
                out[u["username"]] = {"remote_ip": u.get("remote_ip", ""),
                                      "active_ip": u.get("vpn_ip", ""), "device": u.get("device", ""),
                                      "bytes_in": int(u.get("bytes_in") or 0),
                                      "bytes_out": int(u.get("bytes_out") or 0),
                                      "login_ms": u.get("session_started"),
                                      "last_seen": u.get("last_seen")}
        elif rule.data_source == "vpn_ipsec":
            from app.opensearch import ipsec as ipsec_q
            for u in await ipsec_q.active_ipsec_users_detail(gte_ms=gte_ms, lte_ms=now_ms):
                out[u["username"]] = {"remote_ip": u.get("remote_gw_ip", ""),
                                      "active_ip": u.get("assigned_ip", ""), "device": u.get("device", ""),
                                      "bytes_in": int(u.get("bytes_in") or 0),
                                      "bytes_out": int(u.get("bytes_out") or 0),
                                      "login_ms": u.get("session_started"),
                                      "last_seen": u.get("last_seen")}
        else:
            return None
    except Exception as e:
        logger.error("Session fetch failed for rule %s (%s): %s", rule.id, rule.name, e)
        return None
    glob = (rule.target_key or "").strip().lower()
    if glob:
        import fnmatch
        out = {u: v for u, v in out.items() if fnmatch.fnmatch(u.lower(), glob)}
    return out


def _fmt_wib(ms: int | None) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000, tz=_WIB).strftime("%d %b %Y %H:%M:%S WIB")


def _fmt_bytes(n: int | float | None) -> str:
    """Human bytes, decimal units (1 KB = 1000 B) to match the Mbps/MB convention elsewhere."""
    b = float(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if b < 1000 or unit == "TB":
            return f"{int(b)} {unit}" if unit == "B" else f"{b:.1f} {unit}"
        b /= 1000
    return f"{b:.1f} TB"


def _fmt_duration(start_ms: int | None, end_ms: int | None) -> str:
    if not start_ms or not end_ms or end_ms < start_ms:
        return "—"
    secs = int((end_ms - start_ms) / 1000)
    h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
    return (f"{h}h {m}m" if h else f"{m}m {s}s" if m else f"{s}s")


async def _send_session_event(rule: AlertRule, event: str, user: str, info: dict) -> None:
    """Render + send ONE message for a single connect/disconnect event."""
    vpn_type = "SSL VPN" if rule.data_source == "vpn_ssl" else "IPsec VPN"
    connected = event == "connected"
    started_ms, ended_ms = info.get("started_at"), info.get("ended_at")
    # Cumulative bytes the user consumed over the session (last-known counter at disconnect).
    b_in, b_out = int(info.get("bytes_in") or 0), int(info.get("bytes_out") or 0)
    ctx = {
        "rule": {"name": rule.name, "severity": rule.severity, "site_name": rule.site_name},
        "name": rule.name, "severity": rule.severity, "site_name": rule.site_name,
        "vpn_type": vpn_type, "vpn_user": user,
        "remote_ip": info.get("remote_ip") or "—", "active_ip": info.get("active_ip") or "—",
        "device": info.get("device") or "—",
        "started_at": _fmt_wib(started_ms),
        "ended_at": _fmt_wib(ended_ms) if ended_ms else "—",
        "duration": _fmt_duration(started_ms, ended_ms) if not connected else "—",
        "bytes_in": b_in, "bytes_out": b_out,
        "bytes_in_h": _fmt_bytes(b_in), "bytes_out_h": _fmt_bytes(b_out),
        "bytes_total_h": _fmt_bytes(b_in + b_out),
        "event": event, "event_label": "Connected" if connected else "Disconnected",
        "sent_at": datetime.now(_WIB).strftime("%d %b %Y %H:%M:%S WIB"),
    }
    # Template: assigned (active) → seeded "VPN Session Monitor" → hardcoded line.
    tmpl_text: str | None = None
    try:
        async with AsyncSessionLocal() as tdb:
            if rule.notification_template_id:
                row = (await tdb.execute(
                    select(NotificationTemplate.body_template)
                    .where(NotificationTemplate.id == rule.notification_template_id)
                    .where(NotificationTemplate.is_active == True)  # noqa: E712
                )).first()
                tmpl_text = row[0] if row else None
            if not tmpl_text:
                row = (await tdb.execute(
                    select(NotificationTemplate.body_template)
                    .where(NotificationTemplate.name == "VPN Session Monitor")
                    .where(NotificationTemplate.is_active == True)  # noqa: E712
                )).first()
                tmpl_text = row[0] if row else None
    except Exception as e:
        logger.error("Session template fetch failed: %s", e)

    body: str | None = None
    if tmpl_text:
        try:
            body = _render_template(tmpl_text, ctx)
        except Exception as e:
            logger.error("Session template render failed for rule %s: %s", rule.id, e)
    if body is None:
        icon = "🟢" if connected else "🔴"
        u_e, r_e, a_e = html.escape(user), html.escape(ctx["remote_ip"]), html.escape(ctx["active_ip"])
        tail = (f"\n🕐 <b>Started:</b> {ctx['started_at']}" if connected
                else f"\n🕐 <b>Session:</b> {ctx['started_at']} → {ctx['ended_at']} ({ctx['duration']})"
                     f"\n📊 <b>Data:</b> ↓ {ctx['bytes_in_h']} · ↑ {ctx['bytes_out_h']} (total {ctx['bytes_total_h']})")
        body = (f"{icon} <b>{vpn_type} {ctx['event_label']}</b>\n"
                f"👤 <b>User:</b> {u_e} @ {html.escape(rule.site_name or '—')}\n"
                f"🌐 <b>Remote IP:</b> {r_e} · <b>Active IP:</b> {a_e}{tail}")
    parse_mode = "HTML" if "<b>" in body else None
    subject = f"VPN {ctx['event_label']}: {user}"
    try:
        from app.services.notifier_helper import send_alert, load_channel_configs
        db_configs = await load_channel_configs()
        for channel in rule.notify_channels:
            cfg = db_configs.get(channel)
            if not cfg:
                continue
            try:
                await send_alert(channel, cfg, subject=subject, body=body, parse_mode=parse_mode)
            except Exception as e:
                logger.error("Session notify failed for %s: %s", channel, e)
    except Exception as e:
        logger.error("Session notify error: %s", e)


def _diff_sessions(
    prev: dict[str, dict], current: dict[str, dict], now_ms: int,
) -> tuple[list[tuple[str, str, dict]], dict[str, dict]]:
    """Pure diff of two active-session snapshots → (events, new_state).

    A username in current but not prev = connected; in prev but not current = disconnected.
    new_state keeps each still-connected user's ORIGINAL started_at so a later disconnect
    reports the true session length. Pure — no cluster, no DB."""
    events: list[tuple[str, str, dict]] = []
    for u, v in current.items():
        if u not in prev:
            # Real login from the data (latest sample − session age), falling back to the
            # detection time only when the device didn't report an age. This makes the "Started"
            # line the actual login, not the poll tick that detected it.
            events.append(("connected", u, {**v, "started_at": v.get("login_ms") or now_ms, "ended_at": None}))
    for u, v in prev.items():
        if u not in current:
            # Ended = the user's real last-activity edge (newest sample observed while they
            # were still in the presence window), NOT now_ms. The 5-min window still decides
            # WHEN the disconnect fires; this only fixes the stamped logout time (and duration).
            # Falls back to now_ms only if last_seen was never captured.
            events.append(("disconnected", u, {**v, "ended_at": v.get("last_seen") or now_ms}))
    new_state = {
        # Keep the original started_at across polls; seed a new user from their real login.
        # last_seen rides along via {**v} (current's newest sample), so a later disconnect can stamp it.
        u: {**v, "started_at": (prev.get(u) or {}).get("started_at") or v.get("login_ms") or now_ms}
        for u, v in current.items()
    }
    return events, new_state


async def _evaluate_session_rule(rule: AlertRule, db: AsyncSession) -> None:
    """Diff current active VPN sessions vs the previous poll → connect/disconnect events."""
    current = await _fetch_active_vpn_sessions(rule)
    state = (await db.execute(select(AlertState).where(AlertState.rule_id == rule.id))).scalar_one_or_none()
    if not state:
        state = AlertState(rule_id=rule.id, state="INACTIVE")
        db.add(state)
    now = datetime.now(timezone.utc)
    now_ms = int(_time.time() * 1000)
    state.last_evaluated_at = now

    if current is None:  # read failed → hold, never emit disconnects on a bad read
        state.last_read_degraded = True
        await db.commit()
        return
    state.last_read_degraded = False
    state.last_value = float(len(current))
    prev = state.session_state

    # First run (or after enable): baseline only — never blast a connect for everyone
    # already online. Persist, no events.
    if prev is None:
        state.session_state = {u: {**v, "started_at": v.get("login_ms") or now_ms}
                               for u, v in current.items()}
        state.last_state_change_at = now
        await db.commit()
        return

    events, new_state = _diff_sessions(prev, current, now_ms)
    state.session_state = new_state
    if events:
        state.last_state_change_at = now
    await db.commit()

    for event, user, info in events:
        try:
            await _send_session_event(rule, event, user, info)
        except Exception as e:
            logger.error("Session event send failed for %s/%s: %s", rule.id, user, e)


# ── Device Reboot Monitor (kind="reboot") ───────────────────────
# Event-on-state-change, not threshold: each poll snapshots the currently-reporting devices'
# uptime counters and diffs against the previous snapshot. A DECREASE in a device's SNMP
# sys_uptime = a reboot (counter reset), so we emit one event carrying which device rebooted,
# how long it was unreachable, and the new uptime it came back with. Reuses the same
# device_availability() query the device_uptime threshold rules use (what alerts == what the
# Resources ▸ Availability page shows).

async def _fetch_device_reboots(rule: AlertRule) -> dict[str, dict] | None:
    """Currently-reporting devices for a reboot rule, as {device_key(IP):
    {hostname, uptime_seconds, downtime_seconds}}. `downtime_seconds` is the gap around the
    most recent real reset (a 32-bit counter wrap is excluded — note is None only for a genuine
    reboot). target_key is an optional device glob (IP or hostname, e.g. "10.80.*"); blank = all
    devices at the site. Returns None on a read failure so the caller HOLDS instead of treating
    a failed query as 'no reboots'."""
    now_ms = int(_time.time() * 1000)
    window = max(rule.evaluation_window_minutes or 10, 2)
    gte_ms = now_ms - window * 60 * 1000
    try:
        from app.opensearch import device_uptime as du
        result = await du.device_availability(
            site_name=rule.site_name or "Site_FGT-DC", gte_ms=gte_ms, lte_ms=now_ms, now_ms=now_ms,
        )
    except Exception as e:
        logger.error("Reboot fetch failed for rule %s (%s): %s", rule.id, rule.name, e)
        return None
    out: dict[str, dict] = {}
    for d in (result or {}).get("devices", []) or []:
        up = d.get("uptime_seconds")
        key = d.get("device_key")
        if up is None or not key:
            continue
        reboots = [r for r in (d.get("reboots") or []) if r.get("note") is None]
        out[key] = {
            "hostname": d.get("hostname") or key,
            "uptime_seconds": float(up),
            "downtime_seconds": int(reboots[-1]["downtime_seconds"]) if reboots else 0,
        }
    glob = (rule.target_key or "").strip().lower()
    if glob:
        import fnmatch
        out = {k: v for k, v in out.items()
               if fnmatch.fnmatch(str(k).lower(), glob) or fnmatch.fnmatch(v["hostname"].lower(), glob)}
    return out


def _fmt_secs(secs: int | float | None) -> str:
    """Compact duration from a raw second count: '2h 5m' / '3m 10s' / '45s' / '—'."""
    s = int(secs or 0)
    if s <= 0:
        return "—"
    h, m, sec = s // 3600, (s % 3600) // 60, s % 60
    return f"{h}h {m}m" if h else f"{m}m {sec}s" if m else f"{sec}s"


def _diff_reboots(
    prev: dict[str, dict], current: dict[str, dict], now_ms: int,
) -> tuple[list[tuple[str, dict]], dict[str, dict]]:
    """Pure diff of two device-uptime snapshots → (events, new_state).

    A device whose uptime counter DECREASED since the last poll rebooted (SNMP sys_uptime is
    monotonic, so a drop is unambiguous). Events carry the new uptime + the pre-reboot uptime.
    A device new to `current` is baselined silently (first sighting ≠ reboot). new_state records
    each current device's uptime for the next comparison. Pure — no cluster, no DB."""
    events: list[tuple[str, dict]] = []
    for k, v in current.items():
        p = prev.get(k)
        if p is None:
            continue
        prev_up = float(p.get("uptime_seconds", 0.0))
        cur_up = float(v.get("uptime_seconds", 0.0))
        # A genuine reset drops uptime toward zero; the +1s guard ignores SNMP tick rounding.
        if cur_up + 1.0 < prev_up:
            events.append((k, {**v, "reboot_at": now_ms, "prev_uptime_seconds": prev_up}))
    new_state = {
        k: {"hostname": v.get("hostname"), "uptime_seconds": float(v.get("uptime_seconds", 0.0))}
        for k, v in current.items()
    }
    return events, new_state


async def _send_reboot_event(rule: AlertRule, device_key: str, info: dict) -> None:
    """Render + send ONE message for a single detected device reboot."""
    from app.opensearch.device_uptime import format_uptime_short
    new_up = float(info.get("uptime_seconds") or 0.0)
    prev_up = float(info.get("prev_uptime_seconds") or 0.0)
    downtime = int(info.get("downtime_seconds") or 0)
    hostname = info.get("hostname") or str(device_key)
    ctx = {
        "rule": {"name": rule.name, "severity": rule.severity, "site_name": rule.site_name},
        "name": rule.name, "severity": rule.severity, "site_name": rule.site_name,
        "device": hostname, "device_ip": device_key, "target_key": device_key,
        "reboot_at": _fmt_wib(info.get("reboot_at")),
        "downtime_seconds": downtime, "downtime": _fmt_secs(downtime),
        "new_uptime_seconds": new_up, "new_uptime": format_uptime_short(new_up),
        "prev_uptime_seconds": prev_up, "prev_uptime": format_uptime_short(prev_up),
        "event": "rebooted", "event_label": "Rebooted",
        "sent_at": datetime.now(_WIB).strftime("%d %b %Y %H:%M:%S WIB"),
    }
    # Template: assigned (active) → seeded "Device Reboot Monitor" → hardcoded line.
    tmpl_text: str | None = None
    try:
        async with AsyncSessionLocal() as tdb:
            if rule.notification_template_id:
                row = (await tdb.execute(
                    select(NotificationTemplate.body_template)
                    .where(NotificationTemplate.id == rule.notification_template_id)
                    .where(NotificationTemplate.is_active == True)  # noqa: E712
                )).first()
                tmpl_text = row[0] if row else None
            if not tmpl_text:
                row = (await tdb.execute(
                    select(NotificationTemplate.body_template)
                    .where(NotificationTemplate.name == "Device Reboot Monitor")
                    .where(NotificationTemplate.is_active == True)  # noqa: E712
                )).first()
                tmpl_text = row[0] if row else None
    except Exception as e:
        logger.error("Reboot template fetch failed: %s", e)

    body: str | None = None
    if tmpl_text:
        try:
            body = _render_template(tmpl_text, ctx)
        except Exception as e:
            logger.error("Reboot template render failed for rule %s: %s", rule.id, e)
    if body is None:
        h_e, ip_e, s_e = html.escape(hostname), html.escape(str(device_key)), html.escape(rule.site_name or "—")
        body = (f"🔁 <b>Device Rebooted</b>\n"
                f"🖥️ <b>Device:</b> {h_e} ({ip_e})\n"
                f"🏢 <b>Site:</b> {s_e}\n"
                f"🕐 <b>Rebooted at:</b> {ctx['reboot_at']}\n"
                f"⏱️ <b>Unreachable:</b> {ctx['downtime']}\n"
                f"⬆️ <b>Back up · new uptime:</b> {ctx['new_uptime']} (was {ctx['prev_uptime']})")
    parse_mode = "HTML" if "<b>" in body else None
    subject = f"Device Rebooted: {hostname}"
    try:
        from app.services.notifier_helper import send_alert, load_channel_configs
        db_configs = await load_channel_configs()
        for channel in rule.notify_channels:
            cfg = db_configs.get(channel)
            if not cfg:
                continue
            try:
                await send_alert(channel, cfg, subject=subject, body=body, parse_mode=parse_mode)
            except Exception as e:
                logger.error("Reboot notify failed for %s: %s", channel, e)
    except Exception as e:
        logger.error("Reboot notify error: %s", e)


async def _evaluate_reboot_rule(rule: AlertRule, db: AsyncSession) -> None:
    """Diff current device uptimes vs the previous poll → reboot events (uptime-counter reset)."""
    current = await _fetch_device_reboots(rule)
    state = (await db.execute(select(AlertState).where(AlertState.rule_id == rule.id))).scalar_one_or_none()
    if not state:
        state = AlertState(rule_id=rule.id, state="INACTIVE")
        db.add(state)
    now = datetime.now(timezone.utc)
    now_ms = int(_time.time() * 1000)
    state.last_evaluated_at = now

    if current is None:  # read failed → hold, never emit a phantom reboot on a bad read
        state.last_read_degraded = True
        await db.commit()
        return
    state.last_read_degraded = False
    state.last_value = float(len(current))
    prev = state.session_state

    # First run (or after enable): baseline only — a device already up isn't a reboot.
    if prev is None:
        state.session_state = {
            k: {"hostname": v["hostname"], "uptime_seconds": v["uptime_seconds"]}
            for k, v in current.items()
        }
        state.last_state_change_at = now
        await db.commit()
        return

    events, new_state = _diff_reboots(prev, current, now_ms)
    state.session_state = new_state
    if events:
        state.last_state_change_at = now
    await db.commit()

    for device_key, info in events:
        try:
            await _send_reboot_event(rule, device_key, info)
        except Exception as e:
            logger.error("Reboot event send failed for %s/%s: %s", rule.id, device_key, e)


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
            single_rules = [r for r in active_rules if r.kind not in ("composite", "session", "reboot")]
            composite_rules = [r for r in active_rules if r.kind == "composite"]
            session_rules = [r for r in active_rules if r.kind == "session"]
            reboot_rules = [r for r in active_rules if r.kind == "reboot"]

            # ── 1. Single rules (P1 batched) ──
            # Group key is (ds, site, window, appid_sig): appid_flow rules with a distinct
            # app/protocol/port filter can't share the whole-path query, so the filter joins
            # the key. Unfiltered rules → sig None → keep sharing one query.
            notify_queue: list[tuple[AlertRule, float, str, datetime]] = []
            groups: dict[tuple, list[AlertRule]] = defaultdict(list)
            group_filters: dict[tuple, dict | None] = {}
            group_scans: dict[tuple, dict | None] = {}
            for rule in single_rules:
                key = _group_key(rule)
                groups[key].append(rule)
                group_filters[key] = _appid_filter_for(rule)
                group_scans[key] = _scan_descriptor_for(rule)

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
                group_cache[key] = await _run_group_query(
                    ds, site, window, group_filters.get(key), group_scans.get(key))

            for rule in single_rules:
                try:
                    key = _group_key(rule)
                    group_result = group_cache.get(key)
                    scan = _scan_descriptor_for(rule)
                    metric_value = _extract_per_rule_value(rule, group_result)
                    if metric_value is None:
                        await _mark_held(rule, db)
                        continue
                    condition_met = _check_condition(
                        metric_value, rule.condition, rule.threshold_value
                    )
                    # Transient: friendly target name for the notification (reuses group_result
                    # for the device hostname — no extra query). appid_flow has no target_key;
                    # its "target" is the app/protocol/port filter (or, in scan mode, the
                    # offending app), so surface that instead.
                    if scan is not None:
                        offenders = getattr(rule, "_scan_apps", None) or []
                        rule._target_name = offenders[0]["app"] if offenders else \
                            _appid_filter_label(_appid_filter_for(rule))
                    elif rule.data_source == "appid_flow":
                        rule._target_name = _appid_filter_label(_appid_filter_for(rule))
                    else:
                        rule._target_name = _resolve_target_name(
                            rule.data_source, rule.site_name, rule.target_key, group_result)
                    # VPN usage summary {count,total_bytes,top_user_bytes} for the notification.
                    rule._vpn_usage = (
                        group_result if rule.data_source in ("vpn_ssl", "vpn_ipsec")
                        and isinstance(group_result, dict) else None
                    )
                    # Scan enrichment (WHO/WHERE/TO WHOM + volume) — only while breaching, scoped
                    # to the ≤2 offenders. Failure degrades the message (details → None), never
                    # blocks the alert. Stamped so the FIRE message and the snapshot both carry it.
                    rule._scan_detail = None
                    if scan is not None and condition_met and getattr(rule, "_scan_apps", None):
                        try:
                            from app.opensearch import traffic_flow as tf_qb
                            _now_ms = int(_time.time() * 1000)
                            _gte = _now_ms - rule.evaluation_window_minutes * 60 * 1000
                            rule._scan_detail = await tf_qb.appid_flow_app_detail(
                                gte_ms=_gte, lte_ms=_now_ms, site_name=rule.site_name,
                                path=scan.get("path") or "",
                                apps=[a["app"] for a in rule._scan_apps],
                                internet_path=(scan.get("path") == "internet"),
                            )
                        except Exception as ee:
                            logger.warning("scan enrichment failed for rule %s: %s", rule.id, ee)
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
                    drv = driver or {}
                    rule._target_name = _resolve_target_name(
                        drv.get("data_source"), rule.site_name, drv.get("target_key")
                    )
                    await _advance_state_machine(rule, metric_value, condition_met, db, notify_queue)
                except Exception as e:
                    logger.error("Error evaluating composite rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

            # ── 3. Flush batched notifications (P7 grouping) ──
            if notify_queue:
                await _flush_batch_notify(notify_queue)

            # ── 4. VPN session monitors (event-on-change, sent inline per event) ──
            for rule in session_rules:
                try:
                    await _evaluate_session_rule(rule, db)
                except Exception as e:
                    logger.error("Error evaluating session rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

            # ── 5. Device reboot monitors (event-on-change, sent inline per event) ──
            for rule in reboot_rules:
                try:
                    await _evaluate_reboot_rule(rule, db)
                except Exception as e:
                    logger.error("Error evaluating reboot rule %s (%s): %s", rule.id, rule.name, e)
                    await db.rollback()

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
