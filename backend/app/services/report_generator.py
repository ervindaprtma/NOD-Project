"""
Full-stack report generator for NOD (Network Observability Dashboard).
Integrates with FastAPI background tasks.

Pipeline:
  FastAPI route → job queue → generate_report() → build_report_context()
    → Plotly charts → Jinja2 template → WeasyPrint PDF / HTML

Multi-site logic:
  Sites ordered DC → DRC → Office. When multiple sites selected,
  each site gets a full page (page break between) with its own data.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import io
import logging
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from weasyprint import HTML
from jinja2 import Environment, FileSystemLoader

from app.core.config import get_settings

# ── Module level ──────────────────────────────────────────────────────────
logger = logging.getLogger(__name__)
chart_executor = ProcessPoolExecutor(max_workers=2)
settings = get_settings()

TEMPLATE_DIR = Path("reports/templates")
REPORT_DIR = Path("reports/output")
REPORT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_SITES = ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]

CHART_COLORS = [
    "#2563eb", "#7c3aed", "#f59e0b", "#10b981", "#ef4444",
    "#06b6d4", "#f97316", "#8b5cf6", "#14b8a6", "#e11d48",
]

# ── Site ordering & labels ────────────────────────────────────────────────
SITE_ORDER = {
    "Site_FGT-DC": 0,
    "Site_FGT-DRC": 1,
    "Site_FGT_Office": 2,
}

SITE_LABELS = {
    "Site_FGT-DC": "DC",
    "Site_FGT-DRC": "DRC",
    "Site_FGT_Office": "Office",
}

# ── Section mapping: frontend sub-section IDs → backend section keys ─────────
FRONTEND_TO_BACKEND_SECTION = {
    "R-01": {
        "top_apps": "traffic_overview",
        "throughput": "traffic_overview",
        "top_as": "traffic_overview",
        "top_countries": "traffic_overview",
        "protocol_dist": "traffic_overview",
        "per_site": "traffic_overview",
    },
    "R-02": {
        "device_status": "resource_usage",
        "cpu_timeline": "resource_usage",
        "memory_timeline": "resource_usage",
        "session_timeline": "resource_usage",
    },
    "R-03": {
        "ssl_vpn": "vpn_users",
        "ipsec_vpn": "vpn_users",
    },
    "R-04": {
        "latency": "sdwan_sla",
        "jitter": "sdwan_sla",
        "packet_loss": "sdwan_sla",
        "link_status": "sdwan_sla",
    },
    "R-05": {
        "top_services": "traffic_inbound",
        "top_client_as": "traffic_inbound",
        "top_countries": "traffic_inbound",
        "egress": "traffic_inbound",
    },
    "R-06": {
        "top_services": "traffic_internal",
        "top_clients": "traffic_internal",
        "top_servers": "traffic_internal",
    },
}


def _normalize_sections(report_type: str, sections: list[str] | None) -> list[str] | None:
    """
    Map frontend sub-section IDs (e.g. 'top_apps', 'ssl_vpn')
    to backend section keys (e.g. 'traffic_overview', 'vpn_users').

    If a section ID is not in the map, pass it through as-is.
    """
    if not sections:
        return None
    section_map = FRONTEND_TO_BACKEND_SECTION.get(report_type, {})
    if not section_map:
        return sections
    mapped = set()
    for s in sections:
        if s in section_map:
            mapped.add(section_map[s])
        else:
            mapped.add(s)
    return list(mapped)


def _ordered_sites(sites: list[str] | None) -> list[str]:
    """Sort sites in fixed order: DC → DRC → Office."""
    if not sites:
        return list(SITE_ORDER.keys())
    return sorted(sites, key=lambda s: SITE_ORDER.get(s, 99))


def _site_label(site_id: str) -> str:
    """Return human-readable label for a site ID."""
    return SITE_LABELS.get(site_id, site_id)


# ══════════════════════════════════════════════════════════════════════════
# 1. TIMEFRAME FORMATTING
# ══════════════════════════════════════════════════════════════════════════

def format_time_ms(epoch_ms: int) -> str:
    """Convert epoch milliseconds to 'DD MMM YYYY HH:mm' in Asia/Jakarta (WIB).

    The code stores/query times as epoch milliseconds in UTC. For user-facing
    report headers we render Jakarta local time (UTC+7) without a timezone
    suffix to avoid confusion for users in WIB.
    """
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("Asia/Jakarta")
    dt = datetime.fromtimestamp(epoch_ms / 1000, tz=timezone.utc).astimezone(tz)
    return dt.strftime("%d %b %Y %H:%M")


def format_time_range(start_ms: int, end_ms: int) -> tuple[str, str, str]:
    """
    Return (start_str, end_str, range_str).
    range_str is 'DD MMM YYYY HH:mm — DD MMM YYYY HH:mm'.
    """
    s = format_time_ms(start_ms)
    e = format_time_ms(end_ms)
    return s, e, f"{s} — {e}"


def bytes_human(b: int) -> str:
    """Convert raw bytes to human-readable string (decimal SI units)."""
    if b < 1000:
        return f"{b:.0f} B"
    elif b < 1000**2:
        return f"{b / 1000:.1f} KB"
    elif b < 1000**3:
        return f"{b / 1000**2:.1f} MB"
    else:
        return f"{b / 1000**3:.2f} GB"


def _interval_to_seconds(interval: str) -> int:
    """Parse an OpenSearch-style interval string (e.g. '15m', '1h') to seconds.

    Falls back to 15 minutes (900s) on parse failure.
    """
    import re

    if not interval:
        return 900
    m = re.match(r"^(\d+)([smhd])$", interval.strip().lower())
    if not m:
        return 900
    val = int(m.group(1))
    unit = m.group(2)
    mapping = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    return val * mapping.get(unit, 60)


# ══════════════════════════════════════════════════════════════════════════
# 2. CHART RENDERING HELPERS (Plotly → Base64 PNG)
# ══════════════════════════════════════════════════════════════════════════

import plotly.graph_objects as go
import plotly.io as pio

# Set default template for consistent look
pio.templates.default = "plotly_white"


async def _run_chart(func, *args, **kwargs) -> str:
    """Run a CPU-bound chart function in the process pool and return base64 string."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        chart_executor, functools.partial(func, *args, **kwargs)
    )


def _fig_to_b64(fig: go.Figure, width: int = 800, height: int = 400) -> str:
    """Convert a Plotly figure to base64 PNG string using kaleido."""
    buf = io.BytesIO()
    pio.write_image(fig, buf, format="png", width=width, height=height, scale=2)
    buf.seek(0)
    return base64.b64encode(buf.read()).decode()


def render_bar_chart(
    labels: list[str],
    values: list[float | int],
    title: str = "",
    xlabel: str = "",
    color: str = "#2563eb",
    width: int = 800,
    height: int = 400,
) -> str:
    """Render a horizontal bar chart using Plotly. Returns base64 PNG."""
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=values,
        y=labels,
        orientation="h",
        marker_color=color,
        text=[bytes_human(v) for v in values],
        textposition="outside",
        textfont=dict(size=10),
    ))
    fig.update_layout(
        title=dict(text=title, font=dict(size=13), x=0.5),
        xaxis=dict(title=xlabel, tickformat="~s", title_font=dict(size=10)),
        yaxis=dict(autorange="reversed", title_font=dict(size=10)),
        margin=dict(l=10, r=80, t=40, b=20),
        height=height,
        font=dict(family="Arial, sans-serif"),
        hovermode=False,
    )
    return _fig_to_b64(fig, width=width, height=height)


def render_timeseries_chart(
    data: list[dict],
    title: str = "",
    ylabel: str = "",
    x_key: str = "timestamp",
    y_key: str = "value",
    series_key: Optional[str] = None,
    width: int = 800,
    height: int = 400,
) -> str:
    """Render a timeseries line chart using Plotly. Returns base64 PNG."""
    fig = go.Figure()
    fig.update_layout(
        title=dict(text=title, font=dict(size=13), x=0.5),
        yaxis=dict(title=ylabel, title_font=dict(size=10)),
        margin=dict(l=10, r=20, t=40, b=40),
        height=height,
        font=dict(family="Arial, sans-serif"),
        hovermode=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

    if series_key:
        series: dict[str, list[tuple]] = {}
        for point in data:
            key = point.get(series_key, "default")
            ts = point.get(x_key, 0)
            if isinstance(ts, (int, float)) and ts > 1e12:
                ts = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
            series.setdefault(key, []).append((ts, point.get(y_key, 0)))
        for i, (label, points) in enumerate(series.items()):
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            color = CHART_COLORS[i % len(CHART_COLORS)]
            fig.add_trace(go.Scatter(
                x=xs, y=ys, mode="lines", name=label,
                line=dict(width=1.5, color=color),
            ))
    elif data:
        xs = [
            datetime.fromtimestamp(p[x_key] / 1000, tz=timezone.utc)
            if isinstance(p.get(x_key), (int, float)) and p[x_key] > 1e12
            else p[x_key]
            for p in data
        ]
        ys = [p.get(y_key, 0) for p in data]
        fig.add_trace(go.Scatter(
            x=xs, y=ys, mode="lines",
            line=dict(width=1.5, color="#2563eb"),
            fill="tozeroy", fillcolor="rgba(37,99,235,0.08)",
        ))

    return _fig_to_b64(fig, width=width, height=height)


def render_compliance_gauge(
    value: float,
    threshold: float = 99.0,
    label: str = "SLA Compliance",
    width: int = 400,
    height: int = 300,
) -> str:
    """Render a gauge chart for SLA/health percentages using Plotly Indicator."""
    gauge_color = "#10b981" if value >= threshold else "#f59e0b" if value >= 95 else "#ef4444"
    fig = go.Figure()
    fig.add_trace(go.Indicator(
        mode="gauge+number+delta",
        value=value,
        number=dict(suffix="%", font=dict(size=28, color=gauge_color)),
        delta=dict(reference=threshold, increasing=dict(color="#10b981"), decreasing=dict(color="#ef4444")),
        gauge=dict(
            axis=dict(range=[0, 100], tickwidth=1, tickcolor="#64748b"),
            bar=dict(color=gauge_color, thickness=0.6),
            bgcolor="white",
            borderwidth=0,
            steps=[
                dict(range=[0, 95], color="#fef2f2"),
                dict(range=[95, 99], color="#fffbeb"),
                dict(range=[99, 100], color="#f0fdf4"),
            ],
            threshold=dict(
                line=dict(color="red", width=2),
                thickness=0.75,
                value=threshold,
            ),
        ),
    ))
    fig.update_layout(
        title=dict(text=label, font=dict(size=12), x=0.5),
        height=height,
        margin=dict(l=30, r=30, t=50, b=20),
        font=dict(family="Arial, sans-serif"),
    )
    return _fig_to_b64(fig, width=width, height=height)


# ══════════════════════════════════════════════════════════════════════════
# 3. CONTEXT BUILDER — produces report_data dict matching template macros
# ══════════════════════════════════════════════════════════════════════════

async def build_report_context(
    report_type: str,
    gte_ms: int,
    lte_ms: int,
    sites: list[str] | None = None,
    sections: list[str] | None = None,
) -> dict[str, Any]:
    """
    Fetch all required data from OpenSearch and build the full
    report_data context dict. If `sections` is provided, only
    fetch data for those sections.

    Multi-site: When called with a single site in the sites list,
    data is scoped to that site. Use `_ordered_sites()` for ordering.
    """
    # Normalise frontend section IDs → backend section keys
    sections = _normalize_sections(report_type, sections)

    context: dict[str, Any] = {
        "report_data": {},
        "time_range_start": format_time_ms(gte_ms),
        "time_range_end": format_time_ms(lte_ms),
        "time_range": f"{format_time_ms(gte_ms)} — {format_time_ms(lte_ms)}",
    }
    charts: dict[str, str] = {}
    site_list: list[str] = sites or DEFAULT_SITES

    # ── R-01: Traffic Overview ──────────────────────────────────────
    if report_type in ("R-01", "R-08") and (not sections or "traffic_overview" in sections):
        traffic: dict[str, Any] = {}
        try:
            from app.opensearch import appid as appid_qb
            from app.opensearch import traffic_flow as tf_qb

            # Top Applications — use traffic_flow per-site with path_filter='internet'
            site = site_list[0] if site_list else DEFAULT_SITES[0]
            sd = None
            top_apps_raw = []
            try:
                sd = await tf_qb.flow_summary(gte_ms=gte_ms, lte_ms=lte_ms, site_name=site, path_filter="internet")
                if sd and sd.get("top_apps"):
                    # normalize to same shape as legacy appid result
                    top_apps_raw = [{"application": a.get("app_name"), "total_bytes": a.get("total_bytes")} for a in sd.get("top_apps", [])]
            except Exception as exc:
                logger.debug("tf_qb.flow_summary failed for top_apps: %s", exc)

            if top_apps_raw:
                max_val = max(a["total_bytes"] for a in top_apps_raw)
                traffic["top_applications"] = [
                    {
                        "label": a["application"],
                        "value": a["total_bytes"],
                        "pct": round(a["total_bytes"] / max_val * 100, 1) if max_val else 0,
                        "color": CHART_COLORS[i % len(CHART_COLORS)],
                    }
                    for i, a in enumerate(top_apps_raw)
                ]
                labels = [a["application"] for a in top_apps_raw]
                values = [a["total_bytes"] for a in top_apps_raw]
                charts["top_applications"] = await _run_chart(
                    render_bar_chart, labels, values,
                    title="Top 10 Applications by Traffic Volume", xlabel="Bytes",
                )

            # Throughput Timeline — use traffic_flow.flow_chart per-site and sum per-bucket
            try:
                bucket_seconds = _interval_to_seconds("15m")
                flow_chart_res = await tf_qb.flow_chart(
                    gte_ms=gte_ms, lte_ms=lte_ms, site_name=site, bucket_seconds=bucket_seconds, path_filter="internet"
                )
                tp_raw = []
                for row in flow_chart_res.get("chart_data", []):
                    # each row contains timestamp/timestampMs plus per-app bytes; sum all numeric fields except timestamps
                    bytes_sum = 0
                    for k, v in row.items():
                        if k in ("timestamp", "timestampMs"):
                            continue
                        if isinstance(v, (int, float)):
                            bytes_sum += int(v)
                    tp_raw.append({"timestamp": row.get("timestampMs") or row.get("timestamp"), "bytes": bytes_sum})
            except Exception as exc:
                logger.debug("tf_qb.flow_chart failed for throughput timeline: %s", exc)

            if tp_raw:
                # Convert bytes per-bucket into Mbps to match frontend logic:
                # Mbps = (bytes * 8) / bucket_seconds / 1_000_000
                interval = "15m"
                bucket_seconds = _interval_to_seconds(interval)
                tp_mbps = []
                for b in tp_raw:
                    bytes_val = int(b.get("bytes", 0) or 0)
                    mbps = (bytes_val * 8) / bucket_seconds / 1_000_000
                    tp_mbps.append({
                        "timestamp": b.get("timestamp"),
                        "mbps": round(mbps, 2),
                    })

                charts["throughput_timeline"] = await _run_chart(
                    render_timeseries_chart, tp_mbps,
                    title="Throughput Over Time", ylabel="Throughput (Mbps)",
                    y_key="mbps",
                )

            # Total Throughput — prefer per-site total from flow_summary if available
            try:
                total_bytes = sd.get("total_bytes", None) if 'sd' in locals() else None
            except Exception:
                total_bytes = None
            if total_bytes is None:
                try:
                    total_bytes = await appid_qb.total_throughput(gte_ms=gte_ms, lte_ms=lte_ms)
                except Exception:
                    total_bytes = 0
            traffic["total_throughput_bytes"] = total_bytes or 0

            # Top AS Orgs / Countries / Protocol Distribution
            # For multi-site reports we must aggregate per-site results so
            # Top Organizations / Countries / Protocol numbers reflect the
            # union of selected sites rather than a single-site value.
            from collections import defaultdict

            as_agg: dict[str, int] = defaultdict(int)
            country_agg: dict[str, int] = defaultdict(int)
            proto_agg: dict[str, int] = defaultdict(int)

            for s in site_list:
                try:
                    sd_site = await tf_qb.flow_summary(gte_ms=gte_ms, lte_ms=lte_ms, site_name=s, path_filter="internet")
                except Exception:
                    sd_site = None
                if not sd_site:
                    continue

                # flow_summary returns 'top_dst_as_org' and 'top_dst_as_country' and 'protocol_dist'
                for a in sd_site.get("top_dst_as_org", []):
                    name = a.get("org_name") or a.get("as_org")
                    as_agg[name] += int(a.get("total_bytes", 0) or 0)

                for c in sd_site.get("top_dst_as_country", []):
                    country = c.get("country")
                    country_agg[country] += int(c.get("total_bytes", 0) or 0)

                for p in sd_site.get("protocol_dist", []):
                    proto = p.get("protocol")
                    proto_agg[proto] += int(p.get("total_bytes", 0) or 0)

            # Top AS Orgs chart/data
            if as_agg:
                as_items = sorted(as_agg.items(), key=lambda x: -x[1])[:10]
                max_val = as_items[0][1] if as_items else 0
                traffic["top_as_orgs"] = [
                    {"label": name, "value": val, "pct": round(val / max_val * 100, 1) if max_val else 0, "color": CHART_COLORS[i % len(CHART_COLORS)]}
                    for i, (name, val) in enumerate(as_items)
                ]
                labels = [n for n, _ in as_items]
                values = [v for _, v in as_items]
                charts["top_as_orgs"] = await _run_chart(render_bar_chart, labels, values, title="Top 10 Destination AS Organizations", xlabel="Bytes")

            # Top Countries chart/data
            if country_agg:
                country_items = sorted(country_agg.items(), key=lambda x: -x[1])[:10]
                max_val = country_items[0][1] if country_items else 0
                traffic["top_countries"] = [
                    {"label": name, "value": val, "pct": round(val / max_val * 100, 1) if max_val else 0, "color": CHART_COLORS[i % len(CHART_COLORS)]}
                    for i, (name, val) in enumerate(country_items)
                ]
                labels = [n for n, _ in country_items]
                values = [v for _, v in country_items]
                charts["top_countries"] = await _run_chart(render_bar_chart, labels, values, title="Top 10 Destination Countries", xlabel="Bytes")

            # Protocol Distribution chart/data
            if proto_agg:
                proto_items = sorted(proto_agg.items(), key=lambda x: -x[1])[:10]
                labels = [n for n, _ in proto_items]
                values = [v for _, v in proto_items]
                charts["protocol_distribution"] = await _run_chart(render_bar_chart, labels, values, title="Protocol Distribution by Traffic Volume", xlabel="Bytes")

            # Per-site traffic summary
            per_site = []
            for site in site_list:
                try:
                    sd = await tf_qb.flow_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site
                    )
                    per_site.append({
                        "site": _site_label(site),
                        "site_id": site,
                        "total_bytes": sd.get("total_bytes", 0),
                        "top_app": sd.get("top_apps", [{}])[0].get("app_name", "—")
                        if sd.get("top_apps") else "—",
                        "sessions": sd.get("total_sessions", 0),
                    })
                except Exception as exc:
                    logger.error("Per-site fetch failed for %s: %s", site, exc)
            if per_site:
                traffic["per_site_summary"] = per_site

        except Exception as exc:
            logger.error("R-01 data fetch failed: %s", exc, exc_info=True)

        # Attach base64 chart strings
        traffic["throughput_timeline"] = charts.pop("throughput_timeline", None)
        traffic["protocol_distribution"] = charts.pop("protocol_distribution", None)
        context["report_data"]["traffic_overview"] = traffic

    # ── R-02: Resource Usage ────────────────────────────────────────
    if report_type in ("R-02", "R-08") and (not sections or "resource_usage" in sections):
        resources: dict[str, Any] = {}
        try:
            from app.opensearch import ha as ha_qb

            devices = await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)
            if devices:
                resources["devices"] = [
                    {
                        "device": d.get("device", "—"),
                        "hostname": d.get("hostname", "—"),
                        "cpu_usage": d.get("cpu_usage", 0),
                        "mem_usage": d.get("mem_usage", 0),
                        "sessions": d.get("session_count", 0),
                        "sync_status": d.get("sync_status", "Unknown"),
                    }
                    for d in devices
                ]
                resources["device_count"] = len(devices)
                healthy = sum(
                    1 for d in devices
                    if d.get("cpu_usage", 100) < 80 and d.get("mem_usage", 100) < 80
                )
                resources["healthy_count"] = healthy
                resources["degraded_count"] = len(devices) - healthy

            # CPU timeline
            timeline = await ha_qb.resource_timeline(
                gte_ms=gte_ms, lte_ms=lte_ms, interval="15m"
            )
            if timeline.get("cpu"):
                charts["cpu_timeline"] = await _run_chart(
                    render_timeseries_chart, timeline["cpu"],
                    title="CPU Usage Over Time", ylabel="CPU %",
                    series_key="device",
                )
            if timeline.get("mem"):
                charts["mem_timeline"] = await _run_chart(
                    render_timeseries_chart, timeline["mem"],
                    title="Memory Usage Over Time", ylabel="Memory %",
                    series_key="device",
                )

            # HA cluster status
            if devices:
                ha_status = [
                    {
                        "cluster": d.get("device", "—"),
                        "device": d.get("hostname", d.get("device", "—")),
                        "status": "success"
                        if "sync" in str(d.get("sync_status", "")).lower()
                        else "warning",
                        "details": d.get("sync_status", "Unknown"),
                    }
                    for d in devices
                    if d.get("ha_role")
                ]
                if ha_status:
                    resources["ha_cluster_status"] = ha_status

        except Exception as exc:
            logger.error("R-02 data fetch failed: %s", exc, exc_info=True)

        resources["cpu_timeline"] = charts.pop("cpu_timeline", None)
        if charts.get("mem_timeline"):
            resources["resource_timelines"] = {
                "cpu": resources.get("cpu_timeline"),
                "mem": charts.pop("mem_timeline", None),
            }
        context["report_data"]["resource_usage"] = resources

    # ── R-03: VPN Users ─────────────────────────────────────────────
    if report_type in ("R-03", "R-08") and (not sections or "vpn_users" in sections):
        vpn: dict[str, Any] = {}
        try:
            from app.opensearch import sslvpn as sslvpn_qb
            from app.opensearch import ipsec as ipsec_qb

            ssl_count = await sslvpn_qb.all_sslvpn_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms,
                site_names=settings.sslvpn_sites_list,
            )
            ipsec_count = await ipsec_qb.active_ipsec_users_count(
                gte_ms=gte_ms, lte_ms=lte_ms,
            )
            vpn["ssl_vpn_count"] = ssl_count or 0
            vpn["ipsec_vpn_count"] = ipsec_count or 0
            vpn["total_vpn_count"] = (ssl_count or 0) + (ipsec_count or 0)

            # VPN bar chart
            if ssl_count or ipsec_count:
                charts["vpn_timeline"] = await _run_chart(
                    _render_vpn_bar_chart_internal,
                    ssl_count=ssl_count or 0,
                    ipsec_count=ipsec_count or 0,
                )

            # SSL VPN details
            try:
                ssl_users = await sslvpn_qb.active_sslvpn_users(
                    gte_ms=gte_ms, lte_ms=lte_ms,
                    site_names=settings.sslvpn_sites_list,
                )
                if ssl_users:
                    vpn["ssl_vpn_details"] = [
                        {
                            "user": u.get("username", "—"),
                            "device": u.get("device", "—"),
                            "login_time": u.get("login_time", u.get("login_at", "—")),
                            "bytes_rx": u.get("bytes_received", u.get("bytes_rx", 0)),
                            "bytes_tx": u.get("bytes_sent", u.get("bytes_tx", 0)),
                        }
                        for u in ssl_users[:50]
                    ]
            except Exception as exc:
                logger.debug("SSL VPN details not available: %s", exc)

        except Exception as exc:
            logger.error("R-03 data fetch failed: %s", exc, exc_info=True)

        vpn["vpn_timeline"] = charts.pop("vpn_timeline", None)
        context["report_data"]["vpn_users"] = vpn

    # ── R-04: SD-WAN SLA ────────────────────────────────────────────
    if report_type in ("R-04", "R-08") and (not sections or "sdwan_sla" in sections):
        sla: dict[str, Any] = {}
        sla_timelines: dict[str, str] = {}
        try:
            from app.opensearch import sdwan as sdwan_qb

            sla_links = []
            sla_summaries = []

            for site in site_list:
                summary: dict[str, Any] | None = None
                # Timeline per metric
                for metric in ("latency", "jitter", "packet_loss"):
                    try:
                        tl = await sdwan_qb.sla_timeline(
                            gte_ms=gte_ms, lte_ms=lte_ms,
                            site_name=site, metric=metric, interval="5m",
                        )
                        if tl:
                            chart_key = f"sla_{metric}_{site}"
                            sla_timelines[chart_key] = await _run_chart(
                                render_timeseries_chart, tl,
                                title=f"SD-WAN {metric.title()} — {_site_label(site)}",
                                ylabel=metric.title(),
                                series_key="label",
                            )
                    except Exception as exc:
                        logger.debug("SLA %s timeline failed for %s: %s", metric, site, exc)

                # SLA Summary
                try:
                    summary = await sdwan_qb.sla_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    if summary:
                        sla_summaries.append({
                            "site": _site_label(site),
                            "site_id": site,
                            "link": "Primary",
                            "avg_latency": round(summary.get("avg_latency", 0), 1),
                            "avg_jitter": round(summary.get("avg_jitter", 0), 1),
                            "avg_packet_loss": round(summary.get("avg_packet_loss", 0), 2),
                            "sla_compliance": "Met"
                            if summary.get("avg_packet_loss", 100) < 1.0
                            else "Breached",
                        })
                except Exception as exc:
                    logger.debug("SLA summary failed for %s: %s", site, exc)

                # Link status cards
                sla_links.append({
                    "site": _site_label(site),
                    "link_name": "Primary",
                    "status": "up",
                    "latency": round(summary.get("avg_latency", 0), 1)
                    if summary else 0,
                    "jitter": round(summary.get("avg_jitter", 0), 1)
                    if summary else 0,
                    "packet_loss": round(summary.get("avg_packet_loss", 0), 2)
                    if summary else 0,
                })

            if sla_links:
                sla["links"] = sla_links
            if sla_summaries:
                sla["sla_summary"] = sla_summaries
            if sla_timelines:
                sla["sla_timelines"] = sla_timelines

        except Exception as exc:
            logger.error("R-04 data fetch failed: %s", exc, exc_info=True)

        context["report_data"]["sdwan_sla"] = sla

    # ── R-05: Traffic Inbound ────────────────────────────────────────
    if report_type in ("R-05", "R-08") and (not sections or "traffic_inbound" in sections):
        inbound: dict[str, Any] = {}
        inbound_sites: dict[str, Any] = {}
        try:
            from app.opensearch import traffic_inbound as ti_qb

            for site in site_list:
                try:
                    sd = await ti_qb.flow_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    site_dict: dict[str, Any] = {}

                    if sd.get("top_services"):
                        max_val = max(
                            s["total_bytes"] for s in sd["top_services"][:10]
                        ) if sd["top_services"] else 1
                        site_dict["top_services"] = [
                            {
                                "label": s["service_name"],
                                "value": s["total_bytes"],
                                "pct": round(s["total_bytes"] / max_val * 100, 1)
                                if max_val else 0,
                                "color": CHART_COLORS[i % len(CHART_COLORS)],
                            }
                            for i, s in enumerate(sd["top_services"][:10])
                        ]
                        labels = [s["service_name"] for s in sd["top_services"][:10]]
                        values = [s["total_bytes"] for s in sd["top_services"][:10]]
                        charts[f"inbound_services_{site}"] = await _run_chart(
                            render_bar_chart, labels, values,
                            title=f"Top Inbound Services — {_site_label(site)}", xlabel="Bytes",
                        )

                    if sd.get("top_src_as_org"):
                        max_val = max(
                            a["total_bytes"] for a in sd["top_src_as_org"][:10]
                        ) if sd["top_src_as_org"] else 1
                        site_dict["top_client_as"] = [
                            {
                                "label": a["org_name"],
                                "value": a["total_bytes"],
                                "pct": round(a["total_bytes"] / max_val * 100, 1)
                                if max_val else 0,
                                "color": CHART_COLORS[i % len(CHART_COLORS)],
                            }
                            for i, a in enumerate(sd["top_src_as_org"][:10])
                        ]

                    if sd.get("top_src_as_country"):
                        max_val = max(
                            c["total_bytes"] for c in sd["top_src_as_country"][:10]
                        ) if sd["top_src_as_country"] else 1
                        site_dict["top_countries"] = [
                            {
                                "label": c["country"],
                                "value": c["total_bytes"],
                                "pct": round(c["total_bytes"] / max_val * 100, 1)
                                if max_val else 0,
                                "color": CHART_COLORS[i % len(CHART_COLORS)],
                            }
                            for i, c in enumerate(sd["top_src_as_country"][:10])
                        ]

                    if sd.get("protocol_dist"):
                        labels = [p["protocol"] for p in sd["protocol_dist"][:10]]
                        values = [p["total_bytes"] for p in sd["protocol_dist"][:10]]
                        charts[f"inbound_protocol_{site}"] = await _run_chart(
                            render_bar_chart, labels, values,
                            title=f"Inbound Protocol Distribution — {_site_label(site)}",
                            xlabel="Bytes",
                        )

                    if sd.get("egress_breakdown"):
                        labels = [e["interface"] for e in sd["egress_breakdown"][:10]]
                        values = [e["total_bytes"] for e in sd["egress_breakdown"][:10]]
                        charts[f"inbound_egress_{site}"] = await _run_chart(
                            render_bar_chart, labels, values,
                            title=f"Inbound Egress Interface — {_site_label(site)}",
                            xlabel="Bytes",
                        )

                    if site_dict:
                        inbound_sites[_site_label(site)] = site_dict

                except Exception as exc:
                    logger.error("R-05 site %s failed: %s", site, exc)

            if inbound_sites:
                inbound["sites"] = inbound_sites

        except Exception as exc:
            logger.error("R-05 data fetch failed: %s", exc, exc_info=True)

        context["report_data"]["traffic_inbound"] = inbound

    # ── R-06: Traffic Internal ──────────────────────────────────────
    if report_type in ("R-06", "R-08") and (not sections or "traffic_internal" in sections):
        internal: dict[str, Any] = {}
        internal_sites: dict[str, Any] = {}
        try:
            from app.opensearch import traffic_internal as tint_qb

            for site in site_list:
                try:
                    sd = await tint_qb.flow_summary(
                        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site,
                    )
                    site_dict: dict[str, Any] = {}

                    if sd.get("top_services"):
                        max_val = max(
                            s["total_bytes"] for s in sd["top_services"][:10]
                        ) if sd["top_services"] else 1
                        site_dict["top_services"] = [
                            {
                                "label": s["service_name"],
                                "value": s["total_bytes"],
                                "pct": round(s["total_bytes"] / max_val * 100, 1)
                                if max_val else 0,
                                "color": CHART_COLORS[i % len(CHART_COLORS)],
                            }
                            for i, s in enumerate(sd["top_services"][:10])
                        ]

                    # Intra-LAN vs Inter-Site
                    intra = sd.get("intra_lan_bytes", 0) or 0
                    inter = sd.get("inter_site_bytes", 0) or 0
                    total_b = intra + inter
                    site_dict["intra_lan_vs_inter_site"] = {
                        "intra_lan_bytes": intra,
                        "inter_site_bytes": inter,
                        "intra_pct": round(intra / total_b * 100, 1) if total_b else 0,
                        "inter_pct": round(inter / total_b * 100, 1) if total_b else 0,
                    }

                    # Ingress/Egress interfaces
                    if sd.get("ingress_egress_breakdown"):
                        site_dict["ingress_egress_interfaces"] = [
                            {
                                "interface": e.get("interface", "—"),
                                "direction": e.get("direction", "—"),
                                "bytes": e.get("total_bytes", 0),
                            }
                            for e in sd["ingress_egress_breakdown"]
                        ]

                    if site_dict:
                        internal_sites[_site_label(site)] = site_dict

                except Exception as exc:
                    logger.error("R-06 site %s failed: %s", site, exc)

            if internal_sites:
                internal["sites"] = internal_sites

        except Exception as exc:
            logger.error("R-06 data fetch failed: %s", exc, exc_info=True)

        context["report_data"]["traffic_internal"] = internal

    # ── R-07: Executive Summary ─────────────────────────────────────
    if report_type in ("R-07", "R-08") and (not sections or "executive_summary" in sections):
        summary: dict[str, Any] = {}
        try:
            to = context.get("report_data", {}).get("traffic_overview", {})
            ru = context.get("report_data", {}).get("resource_usage", {})
            vu = context.get("report_data", {}).get("vpn_users", {})
            sd = context.get("report_data", {}).get("sdwan_sla", {})

            total_bytes = to.get("total_throughput_bytes", 0)
            summary["total_throughput_bytes"] = total_bytes
            summary["total_throughput_human"] = bytes_human(total_bytes)

            vpn_total = vu.get("total_vpn_count", 0)
            summary["active_vpn_sessions"] = vpn_total

            summary["device_health"] = {
                "total": ru.get("device_count", 0),
                "healthy": ru.get("healthy_count", 0),
                "degraded": ru.get("degraded_count", 0),
            }

            summary["active_alerts"] = 0

            sla_summaries = sd.get("sla_summary", [])
            if sla_summaries:
                met = sum(1 for s in sla_summaries if s["sla_compliance"] == "Met")
                summary["sla_compliance_pct"] = round(met / len(sla_summaries) * 100, 1)
            else:
                summary["sla_compliance_pct"] = 100.0

            top_apps = to.get("top_applications", [])
            if top_apps:
                summary["top_applications"] = [
                    {"app": a["label"], "bytes": a["value"]}
                    for a in top_apps[:5]
                ]

        except Exception as exc:
            logger.error("R-07 executive summary build failed: %s", exc, exc_info=True)

        context["report_data"]["executive_summary"] = summary

    # ── Attach all charts ───────────────────────────────────────────
    context["charts"] = {
        k: base64.b64encode(v).decode() if isinstance(v, bytes) else v
        for k, v in charts.items()
    }

    _inject_charts(context)

    return context


def _inject_charts(context: dict[str, Any]) -> None:
    """Move chart base64 strings from context['charts'] into report_data sub-sections."""
    rd = context.setdefault("report_data", {})
    chart_map = context.get("charts", {})

    to = rd.get("traffic_overview", {})
    for k in ("throughput_timeline", "protocol_distribution"):
        if k in chart_map:
            to[k] = chart_map.pop(k, None)
    if to:
        rd["traffic_overview"] = to

    ru = rd.get("resource_usage", {})
    if "cpu_timeline" in chart_map:
        ru["cpu_timeline"] = chart_map.pop("cpu_timeline", None)
    if "mem_timeline" in chart_map:
        if "resource_timelines" not in ru:
            ru["resource_timelines"] = {}
        ru["resource_timelines"]["mem"] = chart_map.pop("mem_timeline", None)
    if ru:
        rd["resource_usage"] = ru

    vu = rd.get("vpn_users", {})
    if "vpn_timeline" in chart_map:
        vu["vpn_timeline"] = chart_map.pop("vpn_timeline", None)
    if vu:
        rd["vpn_users"] = vu

    context["charts"] = chart_map


def _render_vpn_bar_chart_internal(
    ssl_count: int = 0,
    ipsec_count: int = 0,
    width: int = 800,
    height: int = 400,
) -> str:
    """Render a grouped VPN user bar chart using Plotly."""
    labels = ["SSL VPN", "IPsec VPN"]
    values = [ssl_count, ipsec_count]
    colors = ["#10b981", "#2563eb"]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=labels, y=values,
        marker_color=colors,
        text=[str(v) for v in values],
        textposition="outside",
        textfont=dict(size=12, color="#1e293b"),
        width=0.5,
    ))
    fig.update_layout(
        title=dict(text="Active VPN Users", font=dict(size=13), x=0.5),
        yaxis=dict(title="Users", title_font=dict(size=10)),
        margin=dict(l=10, r=20, t=40, b=20),
        height=height,
        font=dict(family="Arial, sans-serif"),
        hovermode=False,
    )
    return _fig_to_b64(fig, width=width, height=height)


# ══════════════════════════════════════════════════════════════════════════
# 4. RENDERER FUNCTIONS (HTML & PDF)
# ══════════════════════════════════════════════════════════════════════════

def _render_template(context: dict[str, Any]) -> str:
    """Render the Jinja2 template with the given context. Returns HTML string."""
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    template = env.get_template("report_base.html")
    return template.render(**context)


def generate_html(context: dict[str, Any], output_path: Path) -> Path:
    """Render and write HTML report. Returns output path."""
    html = _render_template(context)
    output_path.write_text(html, encoding="utf-8")
    logger.info("HTML report written: %s", output_path)
    return output_path


def generate_pdf(context: dict[str, Any], output_path: Path) -> Path:
    """
    Render template to HTML, convert to PDF via WeasyPrint.
    Returns output path.
    """
    html = _render_template(context)
    html_path = output_path.with_suffix(".html.tmp")
    html_path.write_text(html, encoding="utf-8")
    try:
        HTML(filename=str(html_path)).write_pdf(str(output_path))
        logger.info("PDF report written: %s", output_path)
    finally:
        if html_path.exists():
            html_path.unlink()
    return output_path


def _generate_docx(context: dict, output_path: Path) -> None:
    """Delegate DOCX generation to the existing DOCX generator."""
    try:
        from app.services.docx_generator import generate_docx_report
        generate_docx_report(context, output_path)
    except ImportError:
        logger.warning("DOCX generator not available; output_path %s not created", output_path)
        raise NotImplementedError("DOCX output format is not configured.")


# ══════════════════════════════════════════════════════════════════════════
# 5. MULTI-SITE REPORT GENERATION
# ══════════════════════════════════════════════════════════════════════════

def _report_title(report_type: str) -> str:
    mapping = {
        "R-01": "Traffic Internet",
        "R-02": "Resource Usage Report",
        "R-03": "Active VPN Users Report",
        "R-04": "SD-WAN SLA Report",
        "R-05": "Traffic Inbound Report",
        "R-06": "Traffic Internal Report",
        "R-07": "Executive Summary Report",
        "R-08": "All-in-One Network Observability Report",
    }
    return mapping.get(report_type, "NOD Report")


async def _generate_multi_site_report(job, sites_ordered: list[str], gte_ms: int, lte_ms: int) -> str:
    """
    Generate a multi-site report: one page per site, concatenated
    with page breaks between. Each site gets its own data fetch
    scoped to that site.
    """
    html_parts = []

    for i, site in enumerate(sites_ordered):
        logger.info("Generating page %d/%d: site=%s", i + 1, len(sites_ordered), site)

        site_context = await build_report_context(
            report_type=job.report_type,
            gte_ms=gte_ms,
            lte_ms=lte_ms,
            sites=[site],
            sections=job.sections,
        )

        # Metadata
        site_context["report_title"] = _report_title(job.report_type)
        site_context["generated_at"] = format_time_ms(
            int(datetime.now(timezone.utc).timestamp() * 1000)
        )
        site_context["generated_by"] = str(job.created_by)
        site_context["job_id"] = str(job.id)
        site_context["report_type"] = job.report_type
        site_context["active_sections"] = job.sections
        site_context["site_name"] = _site_label(site)
        site_context["site_id"] = site
        site_context["is_first_page"] = (i == 0)
        site_context["total_sites"] = len(sites_ordered)
        site_context["page_number"] = i + 1

        html_parts.append(_render_template(site_context))

    # Concatenate with page break between each site page
    # But also ensure the first page doesn't have a leading page break
    full_html_parts = []
    for idx, part in enumerate(html_parts):
        if idx == 0:
            # Insert a site-nav bar after the body tag of the first page
            # and let the template handle individual page breaks
            full_html_parts.append(part)
        else:
            # For subsequent pages, inject a page break div before the content
            # Insert after <body> to avoid breaking <html>/<head> structure
            body_tag = "<body>"
            body_idx = part.find(body_tag)
            if body_idx >= 0:
                insert_pos = body_idx + len(body_tag)
                full_html_parts.append(
                    part[:insert_pos] +
                    '<div class="page-break"></div>\n<div class="site-nav">'
                    f'<span>Site: {_site_label(sites_ordered[idx])} ({idx+1}/{len(sites_ordered)})</span>'
                    '</div>' +
                    part[insert_pos:]
                )
            else:
                full_html_parts.append(
                    f'<div class="page-break"></div>\n{part}'
                )

    full_html = "".join(full_html_parts)
    return full_html


async def generate_report(job) -> str:
    """
    High-level entry point called by FastAPI background task.
    Fetches data, builds context, renders template, generates output file.

    Multi-site: If more than one site is selected, generates one page per
    site in order DC → DRC → Office, with page breaks between.
    """
    gte_ms = int(job.time_range_start.timestamp() * 1000)
    lte_ms = int(job.time_range_end.timestamp() * 1000)

    sites_ordered = _ordered_sites(job.sites)

    if len(sites_ordered) > 1 and job.report_type != "R-08":
        # Multi-site: generate one page per site
        full_html = await _generate_multi_site_report(job, sites_ordered, gte_ms, lte_ms)

        # Select output path and write
        output_path = REPORT_DIR / f"{job.id}.{job.output_format}"

        match job.output_format:
            case "html":
                output_path.write_text(full_html, encoding="utf-8")
                logger.info("Multi-site HTML report written: %s", output_path)
            case "pdf":
                html_path = output_path.with_suffix(".html.tmp")
                html_path.write_text(full_html, encoding="utf-8")
                try:
                    HTML(filename=str(html_path)).write_pdf(str(output_path))
                    logger.info("Multi-site PDF report written: %s", output_path)
                finally:
                    if html_path.exists():
                        html_path.unlink()
            case "docx":
                # For DOCX, we build a single context that has all sites
                # and rely on the template to handle
                context = await build_report_context(
                    report_type=job.report_type,
                    gte_ms=gte_ms,
                    lte_ms=lte_ms,
                    sites=sites_ordered,
                    sections=job.sections,
                )
                _apply_metadata(context, job)
                _generate_docx(context, output_path)
            case _:
                raise ValueError(f"Unsupported output format: {job.output_format}")

        return str(output_path)

    # Single-site (or R-08 All-in-One): standard generation
    context = await build_report_context(
        report_type=job.report_type,
        gte_ms=gte_ms,
        lte_ms=lte_ms,
        sites=sites_ordered,
        sections=job.sections,
    )

    _apply_metadata(context, job)

    output_path = REPORT_DIR / f"{job.id}.{job.output_format}"

    match job.output_format:
        case "html":
            generate_html(context, output_path)
        case "pdf":
            generate_pdf(context, output_path)
        case "docx":
            _generate_docx(context, output_path)
        case _:
            raise ValueError(f"Unsupported output format: {job.output_format}")

    return str(output_path)


def _apply_metadata(context: dict[str, Any], job) -> None:
    """Apply standard metadata fields to a context dict."""
    context["report_title"] = _report_title(job.report_type)
    context["generated_at"] = format_time_ms(
        int(datetime.now(timezone.utc).timestamp() * 1000)
    )
    context["generated_by"] = str(job.created_by)
    context["job_id"] = str(job.id)
    context["report_type"] = job.report_type
    context["active_sections"] = job.sections
    sites = _ordered_sites(job.sites)
    context["site_name"] = ", ".join(_site_label(s) for s in sites) if len(sites) > 1 else _site_label(sites[0])
    context["site_id"] = sites[0] if sites else ""
    context["is_first_page"] = True
    context["total_sites"] = len(sites)
    context["page_number"] = 1
