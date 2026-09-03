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
        "description": "Alert when an SD-WAN link SLA is breached. Pick the link (WAN uplink or "
                       "IPsec/ADVPN tunnel) and the metric — packet loss, latency, jitter, or Link "
                       "Status for a Down/Up alert.",
        "body_template": "SD-WAN SLA Breach: {{ name }}\n{% if target_name %}Link: {{ target_name }}\n{% endif %}{{ metric_label }}: {{ metric_value|round(2) }}{% if metric_unit %} {{ metric_unit }}{% endif %}\nThreshold: {{ condition }} {{ threshold }}{% if metric_unit %} {{ metric_unit }}{% endif %}",
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "sdwan_sla",
            "metric_field": "avg_packet_loss",
            "target_key": "1",
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
        "name": "Application Traffic Scan",
        "category": "capacity",
        "icon": "🚦",
        # Scan mode: monitor EVERY app on the path (no app name configured) and fire when any one
        # app's speed exceeds the Mbps threshold, naming the offender + who used it (source IPs),
        # where it egressed (WAN interface) and to whom (dest AS org) + the app's traffic volume.
        # metric_field "app.<path>.<metric>" routes to appid_flow_app_scan; top_n/min_mbps in
        # appid_filter guard against tiny-app churn. Enrichment vars are populated by _flush_batch_notify.
        "description": "Monitor all applications on a path and alert when any single app's speed "
                       "exceeds the threshold — with the source IPs, egress interface, dest AS org and volume.",
        "body_template": (
            "{% if event == 'resolved' %}✅ [RESOLVED] Application traffic normal: {{ target_name }} @ {{ site_name }}\n"
            "{{ metric_label }} was {{ metric_value|round(1) }} {{ metric_unit }} "
            "(limit {{ condition }} {{ threshold_value }} {{ metric_unit }})."
            "{% else %}🚦 High application traffic: {{ target_name }} @ {{ site_name }}\n"
            "{{ metric_label }}: {{ metric_value|round(1) }} {{ metric_unit }} "
            "(limit {{ condition }} {{ threshold_value }} {{ metric_unit }})"
            "{% if scan_volume_text %}\nVolume: {{ scan_volume_text }}{% endif %}"
            "{% if scan_apps_text %}\nTop apps: {{ scan_apps_text }}{% endif %}"
            "{% if scan_protocols_text %}\nProtocol: {{ scan_protocols_text }}{% endif %}"
            "{% if scan_ports_text %}\nPort/service: {{ scan_ports_text }}{% endif %}"
            "{% if scan_src_ips_text %}\nFrom: {{ scan_src_ips_text }}{% endif %}"
            "{% if scan_dst_ips_text %}\nTo: {{ scan_dst_ips_text }}{% endif %}"
            "{% if scan_ingress_text %}\nIn via: {{ scan_ingress_text }}{% endif %}"
            "{% if scan_egress_text %}\nOut via: {{ scan_egress_text }}{% endif %}"
            "{% if scan_dst_orgs_text %}\nDestination: {{ scan_dst_orgs_text }}{% endif %}"
            "\nFired: {{ fired_at }}{% endif %}"
        ),
        "underlying_kind": "single",
        "locked_fields": {
            "data_source": "appid_flow",
            "metric_field": "app.internet.download_mbps",
            "aggregation": "avg",
            "condition": ">",
            "threshold_value": 50.0,
            "evaluation_window_minutes": 5,
            "sustained_for_minutes": 3,
            "severity": "WARNING",
            "appid_filter": {"top_n": 10, "min_mbps": 1.0},
        },
        "exposed_fields": ["name", "site_name", "threshold_value", "notify_channels"],
        "is_default": True,
        "sort_order": 6,
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
        "body_template": "Device Availability Low: {{ name }}\n"
                         "{% if target_name %}Device: {{ target_name }}\n{% endif %}"
                         "Availability: {{ metric_value }}%\n"
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
        "body_template": "Interface Bandwidth Spike: {{ name }}\n"
                         "{% if target_name %}Interface: {{ target_name }}\n{% endif %}"
                         "Peak throughput: {{ metric_value }} Mbps  "
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
    # Per-service noun for the target line (interface / SD-WAN link / device); the line is
    # hidden when the rule has no target (SSL VPN, IPsec count, throughput) via target_name.
    tlabel = {"SD-WAN": "Link", "Interface Stats": "Interface", "Device Uptime": "Device",
              "AppID Scan": "Application"}.get(service, "Target")
    tline = "{% if target_name is defined and target_name %}🎯 <b>" + tlabel + ":</b> {{ target_name|e }}\n{% endif %}"
    return (
        "{% if event == 'resolved' %}✅ <b>" + title + " RECOVERED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "🛠️ <b>Service:</b> " + service + "\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n" + tline +
        "📅 <b>Resolved:</b> {{ sent_at }}\n"
        "⏱️ <b>Was firing since:</b> {{ fired_at }}\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🟢 " + metric_res + "\nStatus : NORMAL\n"
        "━━━━━━━━━━━━━━━━━━\n" + resfoot +
        "{% else %}" + icon + " <b>" + title + " DEGRADED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "🛠️ <b>Service:</b> " + service + "\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n" + tline +
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


def _sdwan_body() -> str:
    """SD-WAN SLA composite — the FULL per-link report: every link, every metric
    (Latency / Jitter / Packet Loss / State), each marked 🔴 breached / 🟢 ok, in ONE message
    for firing AND resolved (the resolve shows all-🟢 healthy values). `sdwan_links` is supplied
    by _flush_batch_notify from the rule's per-clause reads; empty for a non-composite sdwan rule,
    where it falls back to the single driver line."""
    breakdown = (
        "{% if sdwan_links %}{% for lk in sdwan_links %}🔗 <b>{{ lk.label|e }}</b>\n"
        "{% for m in lk.metrics %}{% if m.breached %}🔴{% else %}🟢{% endif %} {{ m.name }}: "
        "{% if m.is_status %}<b>{% if m.up %}UP{% else %}DOWN{% endif %}</b>"
        "{% else %}<b>{{ m.value }}{% if m.unit %} {{ m.unit }}{% endif %}</b>"
        " (limit {{ m.limit }}{% if m.unit %} {{ m.unit }}{% endif %}){% endif %}\n"
        "{% endfor %}{% endfor %}"
        # Fallback: a single-metric (non-composite) sdwan rule keeps the driver line.
        "{% else %}{% if metric_field == 'status' %}🔴 <b>Link Status:</b> "
        "<b>{% if metric_value >= 1 %}DOWN{% else %}UP{% endif %}</b>\n"
        "{% else %}<b>{{ metric_label }}:</b> <b>{{ metric_value|round(2) }}"
        "{% if metric_unit %} {{ metric_unit }}{% endif %}</b> (limit {{ condition|e }} {{ threshold_value }}"
        "{% if metric_unit %} {{ metric_unit }}{% endif %})\n{% endif %}{% endif %}"
    )
    tline = "{% if target_name is defined and target_name %}🎯 <b>Link:</b> {{ target_name|e }}\n{% endif %}"
    return (
        "{% if event == 'resolved' %}✅ <b>SD-WAN SLA RECOVERED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n" + tline +
        "📅 <b>Resolved:</b> {{ sent_at }}\n"
        "⏱️ <b>Was firing since:</b> {{ fired_at }}\n"
        "━━━━━━━━━━━━━━━━━━\n" + breakdown +
        "━━━━━━━━━━━━━━━━━━\n🎉 All monitored links back within SLA."
        "{% else %}📶 <b>SD-WAN SLA DEGRADED</b>\n"
        "━━━━━━━━━━━━━━━━━━\n"
        "🏢 <b>Site:</b> {{ rule.site_name|e }}\n"
        "📌 <b>Alert:</b> {{ rule.name|e }}\n" + tline +
        "⚠️ <b>Severity:</b> {{ rule.severity|e }}\n"
        "📅 <b>Firing since:</b> {{ fired_at }}\n"
        "🔔 <b>Notified:</b> {{ sent_at }}\n"
        "━━━━━━━━━━━━━━━━━━\n" + breakdown +
        "━━━━━━━━━━━━━━━━━━\n⚠️ Check ISP / Tunnel / Routing Path{% endif %}"
    )


def _vpn_lines(count_label: str) -> tuple[str, str]:
    """Fire/resolve value lines for a VPN capacity template. Renders the consumed volume in MB
    for a byte-volume rule (metric_mb set), else the count (users/tunnels). Always appends the
    live active/total/top-user context when the engine provides it. `is defined` guards keep it
    safe in the template preview, which renders with a minimal ctx."""
    fire = (
        "{% if metric_mb is defined and metric_mb is not none %}"
        "<b>Consumed:</b> <b>{{ metric_mb }} MB</b> (limit {{ rule.condition|e }} {{ threshold_mb }} MB)"
        "{% else %}<b>" + count_label + ":</b> <b>{{ metric_value }}</b> (limit " + _L + "){% endif %}"
        "{% if vpn_active_users is defined and vpn_active_users is not none %}"
        "\n👥 <b>Active:</b> {{ vpn_active_users }} · <b>Total:</b> {{ vpn_total_mb }} MB · <b>Top user:</b> {{ vpn_top_user_mb }} MB{% endif %}"
    )
    res = (
        "{% if metric_mb is defined and metric_mb is not none %}"
        "<b>Consumed:</b> {{ metric_mb }} MB (limit {{ rule.condition|e }} {{ threshold_mb }} MB)"
        "{% else %}<b>" + count_label + ":</b> {{ metric_value }} (limit " + _L + "){% endif %}"
    )
    return fire, res


_SSL_FIRE, _SSL_RES = _vpn_lines("Active Users")
_IPSEC_FIRE, _IPSEC_RES = _vpn_lines("Active Tunnels")

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
    {
        "name": "SD-WAN SLA Breach",
        "description": "SD-WAN SLA composite — full per-link Latency/Jitter/Packet Loss/State "
                       "report in one message (fire + resolve, WIB, Telegram).",
        "subject_template": "{% if event == 'resolved' %}✅ SD-WAN SLA Recovered"
                            "{% else %}📶 SD-WAN SLA Degraded{% endif %} — {{ rule.site_name|e }}",
        "body_template": _sdwan_body(),
        "line_template": _sdwan_body(),
        "is_default": False,
        "is_user_created": False,
    },
    _grafana_tpl("Application Throughput Spike", "📈", "THROUGHPUT", "AppID Flow",
        # metric_value is Mbps (traffic.*.total_mbps), NOT bytes — was dividing by 1e6 and
        # showing "0.0 MB" for a real Mbps spike. threshold_value is Mbps too.
        "<b>Throughput:</b> <b>{{ metric_value|round(1) }} Mbps</b> (limit {{ rule.condition|e }} {{ rule.threshold_value|round(0) }} Mbps)",
        "<b>Throughput:</b> {{ metric_value|round(1) }} Mbps (limit {{ rule.condition|e }} {{ rule.threshold_value|round(0) }} Mbps)",
        "⚠️ Check bandwidth hog / DDoS / backup job", "🎉 Traffic back to normal."),
    _grafana_tpl("SSL VPN Capacity", "🔑", "SSL VPN", "SSL VPN",
        _SSL_FIRE, _SSL_RES,
        "⚠️ Check license capacity / concurrent users / heavy consumers", "🎉 Capacity back to normal."),
    _grafana_tpl("IPsec VPN Capacity", "🔗", "IPSEC VPN", "IPsec",
        _IPSEC_FIRE, _IPSEC_RES,
        "⚠️ Check tunnel peer / IKE / routing / heavy consumers", "🎉 Tunnels back to normal."),
    _grafana_tpl("Device Availability Uptime", "🖥️", "DEVICE AVAILABILITY", "Device Uptime",
        "<b>Availability:</b> <b>{{ metric_value|round(2) }}%</b> (target " + _L + "%)",
        "<b>Availability:</b> {{ metric_value|round(2) }}% (target " + _L + "%)",
        "⚠️ Check device power / uplink / SNMP", "🎉 Device availability restored."),
    _grafana_tpl("Interface Bandwidth Spike", "📊", "INTERFACE BANDWIDTH", "Interface Stats",
        # metric_value + threshold_value are Mbps (both threshold modes); utilization_pct is
        # only set in "% of link max" mode, where it shows the real % of the link the peak used.
        "<b>Peak Throughput:</b> <b>{{ metric_value|round(1) }} Mbps</b> (limit " + _L + " Mbps)"
        "{% if utilization_pct is defined and utilization_pct %} — {{ utilization_pct }}% of {{ link_max_mbps }} Mbps{% endif %}",
        "<b>Peak Throughput:</b> {{ metric_value|round(1) }} Mbps (limit " + _L + " Mbps)"
        "{% if utilization_pct is defined and utilization_pct %} — {{ utilization_pct }}% of {{ link_max_mbps }} Mbps{% endif %}",
        "⚠️ Check link capacity / top talkers", "🎉 Throughput back to normal."),
    _grafana_tpl("Application Traffic Scan", "🚦", "APPLICATION TRAFFIC", "AppID Scan",
        # Scan mode (metric_field "app.<path>.<metric>"): target_name = the offending app; the
        # scan_* lines carry WHO (source IPs) / WHERE OUT (egress) / TO WHOM (dest AS org) and the
        # per-app volume. Each is {% if %}-guarded — enrichment can degrade to None without
        # breaking the render — and |e-escaped (app/org names are OpenSearch data, not trusted HTML).
        "<b>{{ metric_label }}:</b> <b>{{ metric_value|round(1) }} Mbps</b> (limit {{ rule.condition|e }} {{ rule.threshold_value }} Mbps)"
        "{% if scan_volume_text %}\n📦 <b>Volume:</b> {{ scan_volume_text|e }}{% endif %}"
        "{% if scan_apps_text %}\n🏆 <b>Top apps:</b> {{ scan_apps_text|e }}{% endif %}"
        "{% if scan_src_ips_text %}\n👥 <b>From:</b> {{ scan_src_ips_text|e }}{% endif %}"
        "{% if scan_ingress_text %}\n🚪 <b>In via:</b> {{ scan_ingress_text|e }}{% endif %}"
        "{% if scan_egress_text %}\n🚪 <b>Out via:</b> {{ scan_egress_text|e }}{% endif %}"
        "{% if scan_dst_orgs_text %}\n🌍 <b>To:</b> {{ scan_dst_orgs_text|e }}{% endif %}",
        # Resolve: NOW as the headline, WAS in parentheses with the drop — the fired app's current
        # speed this tick vs the value it fired at. When the app has fallen out of the top-N there
        # is no reliable current number, so the line says so instead of printing a fake 0.
        "{% if scan_recovered_known %}<b>{{ metric_label }} now:</b> <b>{{ scan_recovered_mbps }} Mbps</b>"
        " (was {{ metric_value|round(1) }} Mbps, ↓ {{ scan_drop_mbps }}) — limit {{ rule.condition|e }} {{ rule.threshold_value }} Mbps"
        "{% else %}<b>{{ metric_label }}</b> no longer in the top talkers (was {{ metric_value|round(1) }} Mbps,"
        " limit {{ rule.condition|e }} {{ rule.threshold_value }} Mbps){% endif %}"
        "{% if scan_apps_text %}\n🏆 <b>Offender was:</b> {{ scan_apps_text|e }}{% endif %}",
        "⚠️ Check the app's top consumers / QoS / policy", "🎉 Application traffic back to normal."),
    # VPN Session Monitor (kind="session"): one message per connect/disconnect event, branching
    # on `event` (connected/disconnected). Uses the per-event vars (vpn_user, remote_ip, …).
    {
        "name": "VPN Session Monitor",
        "description": "Per-session connect/disconnect events for a VPN Session Monitor rule "
                       "(username, remote IP, active IP, start/end timestamps).",
        "subject_template": "{% if event == 'disconnected' %}🔴 VPN Disconnected: {{ vpn_user }}"
                            "{% else %}🟢 VPN Connected: {{ vpn_user }}{% endif %}",
        "body_template": (
            "{% if event == 'disconnected' %}🔴 <b>{{ vpn_type }} DISCONNECTED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>User:</b> {{ vpn_user|e }}\n"
            "🏢 <b>Site:</b> {{ site_name|e }}\n"
            "🌐 <b>Remote IP:</b> {{ remote_ip|e }} · <b>Active IP:</b> {{ active_ip|e }}\n"
            "🕐 <b>Session:</b> {{ started_at }} → {{ ended_at }} ({{ duration }})\n"
            "📊 <b>Data used:</b> ↓ {{ bytes_in_h }} · ↑ {{ bytes_out_h }} (total {{ bytes_total_h }})\n"
            "━━━━━━━━━━━━━━━━━━{% else %}🟢 <b>{{ vpn_type }} CONNECTED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "👤 <b>User:</b> {{ vpn_user|e }}\n"
            "🏢 <b>Site:</b> {{ site_name|e }}\n"
            "🌐 <b>Remote IP:</b> {{ remote_ip|e }} · <b>Active IP:</b> {{ active_ip|e }}\n"
            "🕐 <b>Started:</b> {{ started_at }}\n"
            "━━━━━━━━━━━━━━━━━━{% endif %}"
        ),
        "line_template": None,
        "is_default": False,
        "is_user_created": False,
    },
    # Device Reboot Monitor (kind="reboot"): one message per detected reboot (SNMP uptime
    # counter reset). Uses the per-event vars (device, device_ip, downtime, new/prev uptime).
    {
        "name": "Device Reboot Monitor",
        "description": "Per-reboot events for a Device Reboot Monitor rule — which device "
                       "rebooted, how long it was unreachable, and the new uptime it came back with.",
        "subject_template": "🔁 Device Rebooted: {{ device }}",
        "body_template": (
            "🔁 <b>DEVICE REBOOTED</b>\n"
            "━━━━━━━━━━━━━━━━━━\n"
            "🖥️ <b>Device:</b> {{ device|e }} ({{ device_ip }})\n"
            "🏢 <b>Site:</b> {{ site_name|e }}\n"
            "🕐 <b>Rebooted at:</b> {{ reboot_at }}\n"
            "⏱️ <b>Unreachable:</b> {{ downtime }}\n"
            "⬆️ <b>Back up · new uptime:</b> {{ new_uptime }} (was {{ prev_uptime }})\n"
            "━━━━━━━━━━━━━━━━━━"
        ),
        "line_template": None,
        "is_default": False,
        "is_user_created": False,
    },
]


async def seed_notification_templates() -> int:
    """Insert missing seed notification templates, and refresh the content of existing
    seeded rows that the operator hasn't edited.

    Insert-only used to be the whole story, which meant a body fix in source (e.g. the
    Interface template that labelled Mbps as %, or the VPN templates that had no
    byte-volume branch) never reached an already-seeded DB — the row stayed stale and the
    only recourse was a manual UI edit. Now, for an existing row that is NOT user-authored
    or user-edited (is_user_created=False), we refresh the render-affecting content
    (subject/body/line/description) in place, keeping its id (so AlertRule links survive)
    and its operator-set flags (is_active/is_default/name). A row the user cloned or edited
    in the UI is marked is_user_created=True by the update endpoint, so it is never touched
    here. Never delete+reinsert — that would sever AlertRule.notification_template_id links.
    """
    from app.db.models import NotificationTemplate
    from sqlalchemy import update

    async with AsyncSessionLocal() as db:
        # Rename the old "IPsec Tunnel Status" → "IPsec VPN Capacity" (name only, so its body and
        # any rule links are preserved) when the new name isn't taken. Lets existing deployments
        # pick up the rename without clobbering a customized body — edit the body in the UI to
        # add the volume lines.
        names0 = set((await db.execute(select(NotificationTemplate.name))).scalars().all())
        if "IPsec Tunnel Status" in names0 and "IPsec VPN Capacity" not in names0:
            await db.execute(
                update(NotificationTemplate)
                .where(NotificationTemplate.name == "IPsec Tunnel Status")
                .values(name="IPsec VPN Capacity")
            )
            await db.commit()

        existing = {
            r.name: r for r in (await db.execute(select(NotificationTemplate))).scalars().all()
        }
        count = 0
        refreshed = 0
        for data in SEED_NOTIFICATION_TEMPLATES:
            row = existing.get(data["name"])
            if row is None:
                db.add(NotificationTemplate(**data))
                await db.flush()
                count += 1
            elif not row.is_user_created:
                # Refresh content in place; leave is_active/is_default/name as the operator set them.
                changed = False
                for k in ("subject_template", "body_template", "line_template", "description"):
                    if k in data and getattr(row, k) != data[k]:
                        setattr(row, k, data[k])
                        changed = True
                if changed:
                    refreshed += 1

        if count or refreshed:
            await db.commit()
            logger.info("Seeded %d new notification templates (%d refreshed)", count, refreshed)
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
    # sdwan_sla — BASE metrics (the link is chosen separately via the SD-WAN link picker →
    # target_key = link number). One entry per metric instead of per-link, so it covers every
    # link a site has (DC/DRC: 4, Office: 6) incl. the IPsec / ADVPN tunnels. The engine reads
    # metric_field + target_key (see _extract_per_rule_value sdwan_sla; legacy "<base>_linkN"
    # rules still work).
    {
        "data_source": "sdwan_sla",
        "field_key": "status",
        "display_name": "SD-WAN Link Status (Up/Down)",
        "description": "Link state on the selected link: 0=Up, non-zero=Down. Pick the link, then "
                       "the Down/Up selector sets the condition (Down: >= 1, Up: == 0).",
        "unit": "state",
        "category": "state",
        "valid_aggregations": ["max"],
        "valid_conditions": ["==", ">=", "<"],
        "example_threshold": 1,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_packet_loss",
        "display_name": "SD-WAN Packet Loss",
        "description": "Average packet loss on the selected SD-WAN link.",
        "unit": "%",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 1.0,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_latency",
        "display_name": "SD-WAN Latency",
        "description": "Average latency on the selected SD-WAN link.",
        "unit": "ms",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 100.0,
    },
    {
        "data_source": "sdwan_sla",
        "field_key": "avg_jitter",
        "display_name": "SD-WAN Jitter",
        "description": "Average jitter on the selected SD-WAN link.",
        "unit": "ms",
        "category": "state",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">=", "=="],
        "example_threshold": 5.0,
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
    {
        "data_source": "vpn_ssl",
        "field_key": "total_bytes",
        "display_name": "SSL VPN Total Consumed",
        "description": "Total volume (bytes_in+bytes_out) consumed by all active SSL VPN users "
                       "in the window — the same per-user bytes the VPN Sessions table shows. "
                       "Threshold entered as MB/GB.",
        "unit": "bytes",
        "category": "capacity",
        "valid_aggregations": ["max", "avg"],
        "valid_conditions": [">", ">="],
        "example_threshold": 5_000_000_000.0,  # 5 GB
    },
    {
        "data_source": "vpn_ssl",
        "field_key": "top_user_bytes",
        "display_name": "SSL VPN Top User Consumed",
        "description": "Volume consumed by the single heaviest active SSL VPN user — catches one "
                       "user saturating the link. Threshold entered as MB/GB.",
        "unit": "bytes",
        "category": "capacity",
        "valid_aggregations": ["max", "avg"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1_000_000_000.0,  # 1 GB
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
    {
        "data_source": "vpn_ipsec",
        "field_key": "total_bytes",
        "display_name": "IPsec VPN Total Consumed",
        "description": "Total volume (bytes_in+bytes_out) consumed by all active IPsec users in "
                       "the window, unioned across both endpoints. Threshold entered as MB/GB.",
        "unit": "bytes",
        "category": "capacity",
        "valid_aggregations": ["max", "avg"],
        "valid_conditions": [">", ">="],
        "example_threshold": 5_000_000_000.0,  # 5 GB
    },
    {
        "data_source": "vpn_ipsec",
        "field_key": "top_user_bytes",
        "display_name": "IPsec VPN Top User Consumed",
        "description": "Volume consumed by the single heaviest active IPsec user. Threshold "
                       "entered as MB/GB.",
        "unit": "bytes",
        "category": "capacity",
        "valid_aggregations": ["max", "avg"],
        "valid_conditions": [">", ">="],
        "example_threshold": 1_000_000_000.0,  # 1 GB
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
        "field_key": "traffic.inbound.download_mbps",
        "display_name": "Traffic Inbound (VIP) — Download",
        "description": "Inbound VIP path download rate (server→client, flow.server.bytes) — same "
                       "Upload/Download split the Inbound Sankey uses.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 200.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.inbound.upload_mbps",
        "display_name": "Traffic Inbound (VIP) — Upload",
        "description": "Inbound VIP path upload rate (client→server, flow.client.bytes) — same "
                       "Upload/Download split the Inbound Sankey uses.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 100.0,
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
        "field_key": "traffic.inter_site.download_mbps",
        "display_name": "Traffic Internal — Inter-site Download",
        "description": "Inter-site path download rate (flow.server.bytes) — same Upload/Download "
                       "split the Internal Sankey uses.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 300.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "traffic.inter_site.upload_mbps",
        "display_name": "Traffic Internal — Inter-site Upload",
        "description": "Inter-site path upload rate (flow.client.bytes) — same Upload/Download "
                       "split the Internal Sankey uses.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 300.0,
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
    # Scan mode ("app.<path>.<metric>"): monitor ALL apps on a path and fire when any one app's
    # speed exceeds the threshold — routed to appid_flow_app_scan + enriched with who/where/whom.
    # No app name configured; top_n/min_mbps live in appid_filter. These are the picker options.
    {
        "data_source": "appid_flow",
        "field_key": "app.internet.download_mbps",
        "display_name": "App Scan — Internet Download (per-app)",
        "description": "Fire when ANY application's internet download speed exceeds the threshold.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 50.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "app.internet.upload_mbps",
        "display_name": "App Scan — Internet Upload (per-app)",
        "description": "Fire when ANY application's internet upload speed exceeds the threshold.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 30.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "app.internet.total_mbps",
        "display_name": "App Scan — Internet Total (per-app)",
        "description": "Fire when ANY application's internet total speed (up+down) exceeds the threshold.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 60.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "app.inter_site.total_mbps",
        "display_name": "App Scan — Inter-site Total (per-app)",
        "description": "Fire when ANY application's inter-site total speed exceeds the threshold.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 40.0,
    },
    {
        "data_source": "appid_flow",
        "field_key": "app.intra_lan.total_mbps",
        "display_name": "App Scan — Intra-LAN Total (per-app)",
        "description": "Fire when ANY application's intra-LAN total speed exceeds the threshold.",
        "unit": "Mbps",
        "category": "traffic",
        "valid_aggregations": ["avg", "max"],
        "valid_conditions": [">", ">="],
        "example_threshold": 40.0,
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


