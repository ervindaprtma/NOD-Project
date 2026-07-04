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
            count += 1

        await db.commit()
        logger.info("Seeded %d alert templates", count)
        return count
