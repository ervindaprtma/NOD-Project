"""Seed the initial 6 alert templates into the database (v3 §3.12).

Called at application startup if the alert_templates table is empty.
"""
from __future__ import annotations

import logging

from sqlalchemy import select

from app.db.models import AlertTemplate
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)

SEED_TEMPLATES: list[dict] = [
    {
        "name": "SD-WAN SLA Breach",
        "category": "performance",
        "icon": "📶",
        "description": "Alert when SD-WAN link SLA is breached (packet loss exceeds threshold).",
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
        "name": "VPN Tunnel Down",
        "category": "availability",
        "icon": "🔒",
        "description": "Alert when an SSL or IPsec VPN tunnel goes down (active users drop to zero).",
        "body_template": "VPN Tunnel Down: {{ name }}\nMetric: {{ metric_field }} = {{ metric_value }}\nSeverity: {{ severity }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "ha_resource",
            "metric_field": "num_active",
            "aggregation": "avg",
            "condition": "<=",
            "threshold_value": 0.0,
            "evaluation_window_minutes": 3,
            "sustained_for_minutes": 2,
            "severity": "CRITICAL",
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 2,
    },
    {
        "name": "WAN Congestion",
        "category": "performance",
        "icon": "🚦",
        "description": "Composite: detects WAN congestion when latency is high AND throughput is sustained.",
        "body_template": "WAN Congestion: {{ name }}\nLatency high + sustained throughput detected on {{ site_name }}.",
        "underlying_kind": "composite",
        "locked_fields": {
            "clauses": [
                {"data_source": "sdwan_sla", "metric_field": "avg_latency_link1", "aggregation": "avg", "condition": ">", "threshold_value": 100.0},
                {"data_source": "appid_flow", "metric_field": "app_total_bytes", "aggregation": "sum", "condition": ">", "threshold_value": 50000000.0},
            ],
            "operator": "AND",
            "evaluation_window_minutes": 10,
            "sustained_for_minutes": 5,
            "severity": "WARNING",
        },
        "exposed_fields": ["name", "site_name", "notify_channels"],
        "is_default": True,
        "sort_order": 3,
    },
    {
        "name": "Application Throughput Spike",
        "category": "capacity",
        "icon": "📈",
        "description": "Alert when application throughput exceeds the configured threshold.",
        "body_template": "Throughput Spike: {{ name }}\nValue: {{ metric_value }} bytes/s\nThreshold: > {{ threshold }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "appid_flow",
            "metric_field": "app_total_bytes",
            "aggregation": "sum",
            "condition": ">",
            "threshold_value": 100000000.0,
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
        "description": "Alert when SSL VPN active users approach the license limit.",
        "body_template": "SSL VPN Capacity: {{ name }}\nActive users: {{ metric_value }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "vpn_ssl",
            "metric_field": "num_active_users",
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
        "description": "Alert when fewer IPsec tunnels are active than expected.",
        "body_template": "IPsec Tunnel Status: {{ name }}\nActive tunnels: {{ metric_value }}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "vpn_ipsec",
            "metric_field": "num_active_tunnels",
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
]


async def seed_alert_templates() -> int:
    """Insert seed templates if the templates table is empty.

    Returns the number of templates inserted.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(select(AlertTemplate).limit(1))
        existing = result.scalar_one_or_none()

        if existing is not None:
            return 0  # Already seeded

        count = 0
        for data in SEED_TEMPLATES:
            # Build locked_fields from all the rule-related fields
            template = AlertTemplate(**data)
            db.add(template)
            # Flush per row — avoids SQLAlchemy 2.x "insertmanyvalues"
            # sentinel mismatch (Python hex UUID vs. asyncpg-returned UUID).
            await db.flush()
            count += 1

        await db.commit()
        logger.info("Seeded %d alert templates", count)
        return count


# ── Notification Templates ( §11.1 ) ───────────────────────────────────────────


SEED_NOTIFICATION_TEMPLATES: list[dict] = [
    {
        "name": "Default Alert",
        "description": "System default notification template for alert messages.",
        "subject_template": "🚨 Alert: {{ rule.name }}",
        "body_template": "🚨 *Alert: {{ rule.name }}*\nSeverity: {{ rule.severity }}\nMetric: {{ rule.metric_field }} = {{ metric_value|round(2) }}\nCondition: {{ rule.condition }} {{ rule.threshold_value }}\nFired at: {{ fired_at }}",
        "line_template": "[{{ rule.severity|upper[:3] }}] {{ rule.name }}: {{ metric_value|round(2) }} ({{ rule.condition }} {{ rule.threshold_value }})",
        "is_default": True,
        "is_user_created": False,
    },
]


async def seed_notification_templates() -> int:
    """Insert seed notification templates if empty.

    Returns the number of templates inserted.
    """
    from app.db.models import NotificationTemplate

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(NotificationTemplate).limit(1))
        existing = result.scalar_one_or_none()

        if existing is not None:
            return 0  # Already seeded

        count = 0
        for data in SEED_NOTIFICATION_TEMPLATES:
            tmpl = NotificationTemplate(**data)
            db.add(tmpl)
            await db.flush()
            count += 1

        await db.commit()
        logger.info("Seeded %d notification templates", count)
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
    # appid_flow - traffic fields
    {
        "data_source": "appid_flow",
        "field_key": "total_throughput",
        "display_name": "Traffic Internet (WAN aggregate)",
        "description": "Total internet throughput",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max", "sum"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 1000.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "flow.client.bytes_out",
        "display_name": "Traffic Outbound (client → WAN)",
        "description": "Outbound traffic from client to WAN",
        "unit": "bytes",
        "category": "traffic",
        "valid_aggregations": ["avg", "sum"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1e9,
    },
    {
        "data_source": "appid_flow",
        "field_key": "flow.server.bytes_in",
        "display_name": "Traffic Inbound (WAN → client)",
        "description": "Inbound traffic from WAN to client",
        "unit": "bytes",
        "category": "traffic",
        "valid_aggregations": ["avg", "sum"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1e9,
    },
    {
        "data_source": "appid_flow",
        "field_key": "flow.internal.inter_site_bytes",
        "display_name": "Traffic Internal - Inter-site",
        "description": "Inter-site traffic between sites",
        "unit": "bytes",
        "category": "traffic",
        "valid_aggregations": ["avg", "sum"],
        "valid_conditions": [">", ">="],
        "example_threshold": 5e8,
    },
    {
        "data_source": "appid_flow",
        "field_key": "flow.internal.intra_lan_bytes",
        "display_name": "Traffic Internal - Intra-LAN",
        "description": "Local LAN traffic within site",
        "unit": "bytes",
        "category": "traffic",
        "valid_aggregations": ["avg", "sum"],
        "valid_conditions": [">", ">="],
        "example_threshold": 5e8,
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

        if existing is not None:
            return 0

        count = 0
        for data in SEED_FIELD_CATALOG:
            catalog = AlertFieldCatalog(**data)
            db.add(catalog)
            await db.flush()
            count += 1

        await db.commit()
        logger.info("Seeded %d field catalog rows", count)
        return count


