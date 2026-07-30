"""Seed the initial 6 alert templates into the database (v3 §3.12).

Called at application startup if the alert_templates table is empty.
"""
from __future__ import annotations

import logging

from sqlalchemy import delete, select

from app.db.models import AlertTemplate
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

SEED_TEMPLATES: list[dict] = [
    {
        "name": "SD-WAN SLA Breach",
        "category": "performance",
        "icon": "📶",
        "description": "Alert when SD-WAN link SLA is breached (packet loss exceeds threshold). "
                       "Switch the metric to 'Link Status' for a Down/Up alert on the link state.",
        "body_template": "SD-WAN SLA Breach: {{ name }}\nLink: {{ metric_field }}\nValue: {{ metric_value }}\nThreshold: {{ condition }} {{ threshold }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "sdwan_sla",
            "metric_field": "avg_packet_loss_link1",
            "aggregation": "avg",
            "condition": ">",
            "threshold_value": 1.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "WARNING",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 1,
    },
    {
        "name": "Application Throughput Spike",
        "category": "capacity",
        "icon": "📈",
        # Updated: now uses traffic.wan.total_mbps (per-path Mbps, see appid_flow_alert_summary
        # + _extract_appid_flow). The legacy "app_total_bytes" returned 5-min cumulative bytes
        # — empty/instant windows rendered as "0", which an operator can't tell from a real
        # outage. Mbps is window-size-stable: 0 truly means no traffic in the window.
        "description": "Alert when WAN-aggregate throughput exceeds the configured Mbps threshold.",
        "body_template": "Throughput Spike: {{ name }}\nValue: {{ metric_value|round(2) }} Mbps\nThreshold: > {{ threshold }} Mbps",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "appid_flow",
            "metric_field": "traffic.wan.total_mbps",
            "aggregation": "avg",
            "condition": ">",
            "threshold_value": 1200.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "WARNING",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 4,
    },
    {
        "name": "SSL VPN Capacity",
        "category": "capacity",
        "icon": "🔑",
        # Updated: metric_field now matches what sslvpn.active_sslvpn_users_count() returns
        # (a single cardinality count, not the legacy "num_active_users" key — which is just
        # a string and gets ignored by the engine, returning 0).
        "description": "Alert when SSL VPN active users approach the license limit.",
        "body_template": "SSL VPN Capacity: {{ name }}\nActive users: {{ metric_value }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "vpn_ssl",
            "metric_field": "active_sslvpn_users_count",
            "aggregation": "avg",
            "condition": ">",
            "threshold_value": 100.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "WARNING",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 5,
    },
    {
        "name": "IPsec Tunnel Status",
        "category": "availability",
        "icon": "🔗",
        # Updated: matches what ipsec.active_ipsec_users_count() returns. The legacy
        # "num_active_tunnels" string was extracted via the v2 cardinal→int path that the
        # new pipeline no longer recognizes → 0.
        "description": "Alert when fewer IPsec tunnels are active than expected.",
        "body_template": "IPsec Tunnel Status: {{ name }}\nActive tunnels: {{ metric_value }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "vpn_ipsec",
            "metric_field": "active_ipsec_users_count",
            "aggregation": "avg",
            "condition": "<",
            "threshold_value": 2.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "CRITICAL",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 6,
    },
    {
        "name": "Device Availability Uptime",
        "category": "availability",
        "icon": "🖥️",
        "description": "Alert when a device's counter-based availability drops below the target "
                       "over the window (worst device at the site). Reads 'unknown' and holds when "
                       "history is insufficient, so it never false-fires on a fresh device.",
        "body_template": "Device Availability Low: {{ name }}\nAvailability: {{ metric_value }}%\n"
                         "Threshold: {{ condition }} {{ threshold }}%\nSite: {{ site_name }}",
        "underlying_kind": "single",
        # Matches the device_uptime → availability_pct field-catalog row exactly:
        # valid_aggregations ['min','avg'] (min = worst device), valid_conditions ['<','<='],
        # example_threshold 99.9. Blank device (no target_key) = "any device at the site".
        "locked_fields": {
            "data_source": "device_uptime",
            "metric_field": "availability_pct",
            "aggregation": "min",
            "condition": "<",
            "threshold_value": 99.9,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "CRITICAL",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 7,
    },
    {
        "name": "Interface Bandwidth Spike",
        "category": "capacity",
        "icon": "📊",
        "description": "Alert on a bandwidth spike — PEAK throughput (busier direction, Mbps) in the "
                       "window crosses the threshold. Set an absolute Mbps limit, or a % of a link max "
                       "you enter (the UI computes Mbps = max × %). max aggregation = window peak, so a "
                       "brief burst trips it. Pick the interface after selecting this template.",
        "body_template": "Interface Bandwidth Spike: {{ name }}\nPeak throughput: {{ metric_value }} Mbps  "
                         "(limit {{ condition }} {{ threshold }} Mbps)\nSite: {{ site_name }}",
        "underlying_kind": "single",
        # Spike logic = the interface_stats extractor's PEAK: aggregation "max" returns the
        # window's highest throughput_mbps (busier direction, see _extract_interface_stats), so
        # a momentary burst fires even when the average stays low. Matches the
        # interface_stats → iface.throughput_mbps catalog row. Window ≥2min (rate needs 2
        # buckets); short sustain so a spike alerts fast. Interface (target_key) picked per-rule.
        "locked_fields": {
            "data_source": "interface_stats",
            "metric_field": "iface.throughput_mbps",
            "aggregation": "max",
            "condition": ">",
            "threshold_value": 800.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 1,
            "severity": "WARNING",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 8,
    },
]


# Templates that were shipped once and have since been retired. Deleted from every DB
# on startup (their coverage moved elsewhere / the alert was judged unnecessary).
# Safe: AlertRule.template_id is ON DELETE SET NULL, so any rule built from one keeps
# its config and just loses the (now-gone) template link.
RETIRED_TEMPLATE_NAMES: set[str] = {"VPN Tunnel Down", "WAN Congestion"}


async def seed_alert_templates() -> int:
    """Reconcile seed templates: insert any missing by name, delete retired ones.

    Idempotent: matches on the unique `name`, so new templates (e.g. Device
    Availability Uptime) reach already-seeded DBs without duplicating rows. We
    insert-if-missing rather than delete+reinsert — these templates aren't
    user-editable, and AlertRule.template_id is ON DELETE SET NULL, so wiping a
    row would sever rules built from it and mint a new UUID. Existing (non-retired)
    rows are left untouched.

    Returns the number of templates inserted.
    """
    async with AsyncSessionLocal() as db:
        # Retire removed templates first (reaches already-seeded DBs, not just fresh ones).
        retired = (await db.execute(
            delete(AlertTemplate).where(AlertTemplate.name.in_(RETIRED_TEMPLATE_NAMES))
        )).rowcount
        if retired:
            logger.info("Retired %d alert template(s): %s", retired, sorted(RETIRED_TEMPLATE_NAMES))

        existing = {
            r.name: r for r in (await db.execute(select(AlertTemplate))).scalars().all()
        }
        # Upsert by name: these templates aren't user-editable (no CRUD endpoint), so it's
        # safe to refresh existing rows in place — this propagates definition changes (e.g.
        # a template's metric/threshold) to already-seeded DBs while keeping the row's id,
        # so AlertRule.template_id links survive. New names are inserted.
        updatable = ("category", "icon", "description", "subject_template", "body_template",
                     "underlying_kind", "locked_fields", "exposed_fields", "is_default", "sort_order")
        count = 0
        for data in SEED_TEMPLATES:
            row = existing.get(data["name"])
            if row is None:
                db.add(AlertTemplate(**data))
                # Flush per row — avoids SQLAlchemy 2.x "insertmanyvalues" sentinel
                # mismatch (Python hex UUID vs. asyncpg-returned UUID).
                await db.flush()
                count += 1
            else:
                for k in updatable:
                    if k in data:
                        setattr(row, k, data[k])

        await db.commit()
        logger.info("Seeded %d new alert templates (existing refreshed)", count)
        return count


# ── Notification Templates ( §11.1 ) ───────────────────────────────────────────


def _grafana_body(icon: str, title: str, service: str, metric_fire: str,
                  metric_res: str, foot: str, resfoot: str) -> str:
    """Build a Grafana-style HTML notification body (Telegram parse_mode=HTML).

    Branches on `event` (firing → DEGRADED, resolved → RECOVERED). Dynamic values use
    |e so `<`/`>` operators can't break HTML parsing; timestamps come from {{ fired_at }}
    (rendered in WIB by the engine). metric_fire/metric_res are the per-type value lines.
    """
    return (
        "{% if event == 'resolved' %}✅ <b>" + title + " RECOVERED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "🛠️ <b>Service:</b> " + service + "\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n"
        "📅 <b>Resolved:</b> {{ sent_at }}\n"
        "⏱️ <b>Was firing since:</b> {{ fired_at }}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟢 " + metric_res + "\nStatus : NORMAL\n"
        "━━━━━━━━━━━━━━━━━━\n" + resfoot +
        "{% else %}" + icon + " <b>" + title + " DEGRADED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "🛠️ <b>Service:</b> " + service + "\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n"
        "⚠️ <b>Severity:</b> {{ rule.severity|e }}\n"
        "📅 <b>Firing since:</b> {{ fired_at }}\n"
        "🔔 <b>Notified:</b> {{ sent_at }}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🔴 " + metric_fire + "\n"
        "━━━━━━━━━━━━━━━━━━\n" + foot + "{% endif %}"
    )


def _grafana_tpl(name: str, icon: str, title: str, service: str, metric_fire: str,
                 metric_res: str, foot: str, resfoot: str) -> dict:
    body = _grafana_body(icon, title, service, metric_fire, metric_res, foot, resfoot)
    return {
        "name": name,
        "description": f"Grafana-style HTML alert for {name} (fire + resolve, WIB, Telegram).",
        "subject_template": "{% if event == 'resolved' %}✅ " + title + " Recovered"
                            "{% else %}" + icon + " " + title + " Degraded{% endif %}"
                            " — {{ rule.site_name|e }}",
        # Same rich body for body_template and line_template: the batch dispatch renders
        # the line_template per rule, and HTML content flips the send to parse_mode=HTML.
        "body_template": body,
        "line_template": body,
        "is_default": False,
        "is_user_created": False,
    }


_L = "{{ rule.condition|e }} {{ rule.threshold_value }}"

SEED_NOTIFICATION_TEMPLATES: list[dict] = [
    {
        "name": "Default Alert",
        "description": "System default notification template for alert messages.",
        "subject_template": "🚨 Alert: {{ rule.name }}",
        "body_template": "🚨 *Alert: {{ rule.name }}*\nSeverity: {{ rule.severity }}\nMetric: {{ rule.metric_field }} = {{ metric_value|round(2) }}\nCondition: {{ rule.condition }} {{ rule.threshold_value }}\nFired at: {{ fired_at }}",
        "line_template": "[{{ (rule.severity|upper)[:3] }}] {{ rule.name }}: {{ metric_value|round(2) }} ({{ rule.condition }} {{ rule.threshold_value }})",
        "is_default": True,
        "is_user_created": False,
    },
    _grafana_tpl("SD-WAN SLA Breach", "📶", "SD-WAN", "SD-WAN",
        "<b>Packet Loss:</b> <b>{{ metric_value|round(2) }}%</b> (limit " + _L + "%)",
        "<b>Packet Loss:</b> {{ metric_value|round(2) }}% (limit " + _L + "%)",
        "⚠️ Check ISP / Tunnel / Routing Path", "🎉 All monitored paths back to normal."),
    _grafana_tpl("Application Throughput Spike", "📈", "THROUGHPUT", "AppID Flow",
        "<b>Volume:</b> <b>{{ (metric_value/1000000)|round(1) }} MB</b> (limit {{ rule.condition|e }} {{ (rule.threshold_value/1000000)|round(0) }} MB)",
        "<b>Volume:</b> {{ (metric_value/1000000)|round(1) }} MB (limit {{ rule.condition|e }} {{ (rule.threshold_value/1000000)|round(0) }} MB)",
        "⚠️ Check bandwidth hog / DDoS / backup job", "🎉 Traffic back to normal."),
    _grafana_tpl("SSL VPN Capacity", "🔑", "SSL VPN", "SSL VPN",
        "<b>Active Users:</b> <b>{{ metric_value }}</b> (limit " + _L + ")",
        "<b>Active Users:</b> {{ metric_value }} (limit " + _L + ")",
        "⚠️ Check license capacity / concurrent users", "🎉 Capacity back to normal."),
    _grafana_tpl("IPsec Tunnel Status", "🔗", "IPSEC TUNNEL", "IPsec",
        "<b>Active Tunnels:</b> <b>{{ metric_value }}</b> (alert when " + _L + ")",
        "<b>Active Tunnels:</b> {{ metric_value }} (expected clears " + _L + ")",
        "⚠️ Check tunnel peer / IKE / routing", "🎉 Tunnels back up."),
    _grafana_tpl("Device Availability Uptime", "🖥️", "DEVICE AVAILABILITY", "Device Uptime",
        "<b>Availability:</b> <b>{{ metric_value|round(2) }}%</b> (target " + _L + "%)",
        "<b>Availability:</b> {{ metric_value|round(2) }}% (target " + _L + "%)",
        "⚠️ Check device power / uplink / SNMP", "🎉 Device availability restored."),
    _grafana_tpl("Interface Bandwidth Spike", "📊", "INTERFACE BANDWIDTH", "Interface Stats",
        "<b>Peak Utilization:</b> <b>{{ metric_value|round(1) }}%</b> (limit " + _L + "%)",
        "<b>Peak Utilization:</b> {{ metric_value|round(1) }}% (limit " + _L + "%)",
        "⚠️ Check link capacity / top talkers", "🎉 Utilization back to normal."),
]


async def seed_notification_templates() -> int:
    """Insert any seed notification templates missing by name.

    Idempotent (same pattern as the alert templates): matches on `name`, so the 6
    Grafana-style HTML templates reach already-seeded DBs without duplicating or
    clobbering existing (incl. user-created) rows. Never delete+reinsert — that would
    sever AlertRule.notification_template_id links.
    """
    from app.db.models import NotificationTemplate

    async with AsyncSessionLocal() as db:
        existing_names = set(
            (await db.execute(select(NotificationTemplate.name))).scalars().all()
        )
        count = 0
        for data in SEED_NOTIFICATION_TEMPLATES:
            if data["name"] in existing_names:
                continue
            db.add(NotificationTemplate(**data))
            await db.flush()
            count += 1

        if count:
            await db.commit()
            logger.info("Seeded %d new notification templates", count)
        return count


# ── Field Catalog ( §11.2 ) ────────────────────────────────────────────────

SEED_FIELD_CATALOG: list[dict] = [
    # ha_resource
    {
        "data_source": "ha_resource",
        "field_key": "ha_member.cpu_usage",
        "display_name": "HA Member CPU",
        "description": "CPU usage percentage per HA member device",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 80.0,
    },
    {
        "data_source": "ha_resource",
        "field_key": "num_active",
        "display_name": "Device Active Count",
        "description": "Number of active devices/tunnels in HA pair",
        "unit": "count",
        "category": "state",
        "valid_aggregations": ["avg", "min", "max"],
        "valid_conditions": ["<", "<=", "=="],
        "example_threshold": 2,
    },
    # Phase E Part 1: Resources depth — mem/session already returned by
    # current_device_status(); catalog-only add (no query/extractor change).
    {
        "data_source": "ha_resource",
        "field_key": "ha_member.mem_usage",
        "display_name": "HA Member Memory",
        "description": "Memory usage percentage per HA member device",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "<"],
        "example_threshold": 85.0,
    },
    {
        "data_source": "ha_resource",
        "field_key": "ha_member.session_count",
        "display_name": "Device Session Count",
        "description": "Active session count per HA member device",
        "unit": "count",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 500000,
    },
    # sdwan_sla
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_packet_loss_link1",
        "display_name": "SD-WAN Link 1 Packet Loss",
        "description": "Average packet loss on SD-WAN link 1",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 1.0,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_packet_loss_link2",
        "display_name": "SD-WAN Link 2 Packet Loss",
        "description": "Average packet loss on SD-WAN link 2",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 1.0,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_latency_link1",
        "display_name": "SD-WAN Link 1 Latency",
        "description": "Average latency on SD-WAN link 1",
        "unit": "ms",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 100.0,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_latency_link2",
        "display_name": "SD-WAN Link 2 Latency",
        "description": "Average latency on SD-WAN link 2",
        "unit": "ms",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 100.0,
    },
    # SD-WAN link state (0=Up, non-zero=Down). metric_field "status_linkN" → base "status".
    # The UI offers a Down/Up selector that sets condition+threshold (Down: >= 1, Up: == 0).
    {
        "data_source": "sdwan_sla",
        "field_key": "status_link1",
        "display_name": "SD-WAN Link 1 Status",
        "description": "Link 1 state: 0=Up, non-zero=Down. Alert when Down (>= 1) or Up (== 0).",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">=", "<"],
        "example_threshold": 1,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "status_link2",
        "display_name": "SD-WAN Link 2 Status",
        "description": "Link 2 state: 0=Up, non-zero=Down. Alert when Down (>= 1) or Up (== 0).",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">=", "<"],
        "example_threshold": 1,
    },
    # vpn_ssl
    {
        "data_source": "vpn_ssl",
        "field_key": "active_sslvpn_users_count",
        "display_name": "SSL VPN Active Users",
        "description": "Number of active SSL VPN users",
        "unit": "count",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 100.0,
    },
    # vpn_ipsec
    {
        "data_source": "vpn_ipsec",
        "field_key": "active_ipsec_users_count",
        "display_name": "IPsec Active Tunnels",
        "description": "Number of active IPsec tunnels",
        "unit": "count",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": ["<", "<="],
        "example_threshold": 1.0,
    },
    # appid_flow - per-path traffic rate (Mbps). Split by flow.traffic.path; the engine
    # honors the "traffic.<path>.<metric>" key via appid_flow_alert_summary.
    {
        "data_source": "appid_flow",
        "field_key": "traffic.internet.download_mbps",
        "display_name": "Traffic Internet — Download",
        "description": "Internet path download rate (WAN → client)",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 800.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.internet.upload_mbps",
        "display_name": "Traffic Internet — Upload",
        "description": "Internet path upload rate (client → WAN)",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 400.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.internet.total_mbps",
        "display_name": "Traffic Internet — Total",
        "description": "Internet path total rate (up + down)",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1000.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.inbound.total_mbps",
        "display_name": "Traffic Inbound (VIP) — Total",
        "description": "Inbound VIP path total rate",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 300.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.inter_site.total_mbps",
        "display_name": "Traffic Internal — Inter-site",
        "description": "Inter-site path total rate",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 500.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.intra_lan.total_mbps",
        "display_name": "Traffic Internal — Intra-LAN",
        "description": "Intra-LAN path total rate",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 500.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.wan.total_mbps",
        "display_name": "Traffic Internet (WAN aggregate)",
        "description": "All-paths total rate (parity with old total_throughput)",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1200.0,
    },
    # Phase E Part 2: Interface Bandwidth (interface_stats). The builder pairs these with
    # an interface picker (→ target_key). Window must be ≥ 2 min (derivative needs 2 buckets).
    {
        "data_source": "interface_stats",
        "field_key": "iface.rx_mbps",
        "display_name": "Interface RX (ingress)",
        "description": "Ingress bandwidth (ifHCInOctets rate). Requires an interface + ≥2min window.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "<"],
        "example_threshold": 800.0,
    },
    {
        "data_source": "interface_stats",
        "field_key": "iface.tx_mbps",
        "display_name": "Interface TX (egress)",
        "description": "Egress bandwidth (ifHCOutOctets rate). Requires an interface + ≥2min window.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "<"],
        "example_threshold": 800.0,
    },
    {
        "data_source": "interface_stats",
        "field_key": "iface.utilization_pct",
        "display_name": "Interface Utilization",
        "description": "Busier direction vs ifHighSpeed. Requires an interface + ≥2min window.",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 85.0,
    },
    {
        "data_source": "interface_stats",
        "field_key": "iface.throughput_mbps",
        "display_name": "Interface Throughput (busier direction)",
        "description": "max(RX, TX) in Mbps. Alert on an absolute Mbps threshold, or on a % of a "
                       "link max you set (the UI computes Mbps = max × %). Requires interface + ≥2min.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 800.0,
    },
    {
        "data_source": "interface_stats",
        "field_key": "iface.oper_status",
        "display_name": "Interface Oper Status (1=up)",
        "description": "Operational status; use == 0 or < 1 to catch a down link.",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", "<"],
        "example_threshold": 1,
    },
    # device_uptime (§11.1) — availability alerting. Per-device metrics need a
    # device (target_key); leave it blank for "any device at the site". Use a
    # ≥5min window: at a 30s poll that's ~10 samples, so a dropped scrape or two
    # never trips a false alarm. collector_gap is site-level (leave device blank).
    {
        "data_source": "device_uptime",
        "field_key": "not_reporting",
        "display_name": "Device Not Reporting",
        "description": "1 when the device has been silent >300s (genuinely down). A site-wide "
                       "Telegraf outage reads 'collector_gap' instead, so this never storms. Use == 1.",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">="],
        "example_threshold": 1,
    },
    {
        "data_source": "device_uptime",
        "field_key": "collector_gap",
        "display_name": "Collector Connectivity Lost (site)",
        "description": "1 when every device at the site went silent together — Telegraf lost "
                       "connectivity, not the devices. Site-level: leave the device blank. Use == 1.",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">="],
        "example_threshold": 1,
    },
    {
        "data_source": "device_uptime",
        "field_key": "reboot_count",
        "display_name": "Reboot Count",
        "description": "Reboots detected in the window (uptime counter reset). Use >= 1.",
        "unit": "count",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": [">=", ">"],
        "example_threshold": 1,
    },
    {
        "data_source": "device_uptime",
        "field_key": "availability_pct",
        "display_name": "Availability %",
        "description": "Counter-based uptime over the window. Blank device = worst device at the "
                       "site. Reads 'unknown' (holds, never fires) when history is insufficient. Use < 99.9.",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["min", "avg"],
        "valid_conditions": ["<", "<="],
        "example_threshold": 99.9,
    },
    {
        "data_source": "device_uptime",
        "field_key": "uptime_seconds",
        "display_name": "Uptime (seconds)",
        "description": "Seconds since last boot — a small value means 'came back up recently'. "
                       "Blank device = lowest uptime at the site. Use <.",
        "unit": "s",
        "category": "state",
        "valid_aggregations": ["min"],
        "valid_conditions": ["<", "<="],
        "example_threshold": 3600,
    },
    {
        "data_source": "device_uptime",
        "field_key": "wrap_risk",
        "display_name": "Counter Wrap Approaching",
        "description": "1 when uptime nears the 32-bit SNMP counter wrap (~486d) — schedule a "
                       "reboot before the counter rolls. Use == 1.",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">="],
        "example_threshold": 1,
    },
]


async def seed_field_catalog() -> int:
    """Insert seed field catalog rows if empty.

    Returns the number of rows inserted.
    """
    from app.db.models import AlertFieldCatalog

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AlertFieldCatalog).limit(1))
        existing = result.scalar_one_or_none()

        if existing is None:
            count = 0
            for data in SEED_FIELD_CATALOG:
                db.add(AlertFieldCatalog(**data))
                await db.flush()
                count += 1
            await db.commit()
            logger.info("Seeded %d field catalog rows", count)
            return count

        # Already seeded: reconcile the data sources this codebase actively manages so
        # engine-honored fields reach existing DBs — appid_flow (per-path fix, old byte
        # keys were never honored), ha_resource (Phase E mem/session depth),
        # interface_stats (new source + throughput_mbps), sdwan_sla (link status),
        # vpn_ssl / vpn_ipsec (count metrics — keep them aligned with the templates that
        # reference the keys, so a stale row from an older catalog can't diverge).
        managed = {"appid_flow", "ha_resource", "interface_stats", "device_uptime", "sdwan_sla",
                   "vpn_ssl", "vpn_ipsec"}
        await db.execute(
            delete(AlertFieldCatalog).where(AlertFieldCatalog.data_source.in_(managed))
        )
        count = 0
        for data in SEED_FIELD_CATALOG:
            if data["data_source"] not in managed:
                continue
            db.add(AlertFieldCatalog(**data))
            await db.flush()
            count += 1
        await db.commit()
        logger.info("Reconciled %d field catalog rows for %s", count, sorted(managed))
        return count


