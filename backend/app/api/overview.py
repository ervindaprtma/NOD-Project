"""
Overview Dashboard API (FR-01).
Returns aggregated data for all overview panels in a single response.
Q-07: uses single query per panel, NO N+1 patterns.

All sub-queries are wrapped with try/except via safe_query() so that
one failure doesn't crash the entire overview response. Each panel
returns empty data on failure rather than 500.
"""
from __future__ import annotations

import time
import math
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
from app.api._safe import safe_query
from app.core.config import get_settings
from app.db.session import get_db, AsyncSessionLocal
from app.opensearch import appid as appid_qb
from app.opensearch import ha as ha_qb
from app.opensearch import ipsec as ipsec_qb
from app.opensearch import sdwan as sdwan_qb
from app.opensearch import sslvpn as sslvpn_qb
from app.opensearch import traffic_flow as tf_qb
from app.opensearch import traffic_inbound as ti_qb
from app.opensearch import interface_stats as iface_qb
from app.schemas.common import APIResponse
from app.schemas.overview import (
    ActiveUserKPI,
    DeviceResourceStatus,
    HAStatusKPI,
    OverviewResponse,
    SiteWanBandwidth,
    SiteWanStatus,
    ThroughputKPI,
    TopApplication,
    TopASOrg,
    TopInboundService,
    WanInterfaceSummary,
    SparklinePoint,
)

logger = logging.getLogger("nod.api.overview")
settings = get_settings()
router = APIRouter(prefix="/api/v1", tags=["Overview"])


def _format_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


def _compute_interval(gte_ms: int, lte_ms: int) -> str:
    """Compute dynamic date_histogram interval based on time range."""
    delta_sec = (lte_ms - gte_ms) / 1000
    if delta_sec <= 7200:      # ≤ 2h
        return "60s"
    elif delta_sec <= 43200:   # ≤ 12h
        return "5m"
    else:                       # > 12h
        return "15m"


@router.get("/overview", response_model=APIResponse[OverviewResponse])
async def get_overview(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """FR-01: Returns all overview panels in a single API call.
    All sub-queries are isolated so one failure doesn't crash the page."""
    t0 = time.monotonic()
    errors: list[str] = []

    # Run all independent sub-queries in parallel for speed
    ssl_task = safe_query(
        sslvpn_qb.all_sslvpn_users_count, "overview.sslvpn_users_count",
        gte_ms=gte_ms, lte_ms=lte_ms, site_names=settings.sslvpn_sites_list,
    )
    ipsec_task = safe_query(
        ipsec_qb.active_ipsec_users_count, "overview.active_ipsec_users_count",
        gte_ms=gte_ms, lte_ms=lte_ms,
    )
    devices_task = safe_query(
        ha_qb.current_device_status, "overview.current_device_status",
        gte_ms=gte_ms, lte_ms=lte_ms,
    )
    sparklines_task = safe_query(
        ha_qb.session_sparkline, "overview.session_sparkline",
        gte_ms=gte_ms, lte_ms=lte_ms,
    )
    top_apps_task = safe_query(
        tf_qb.top_applications, "overview.top_applications",
        gte_ms=gte_ms, lte_ms=lte_ms, size=10,
    )
    top_as_orgs_task = safe_query(
        tf_qb.top_dst_as_orgs, "overview.top_dst_as_orgs",
        gte_ms=gte_ms, lte_ms=lte_ms, size=10,
    )
    sdwan_task = safe_query(
        sdwan_qb.all_sites_link_status, "overview.all_sites_link_status",
        gte_ms=gte_ms, lte_ms=lte_ms, site_names=settings.sdwan_sites_list,
    )
    total_bytes_task = safe_query(
        tf_qb.total_throughput, "overview.total_throughput",
        gte_ms=gte_ms, lte_ms=lte_ms,
    )
    inbound_task = safe_query(
        ti_qb.flow_summary, "overview.inbound_flow_summary",
        gte_ms=gte_ms, lte_ms=lte_ms, site_name="Site_FGT-DC",
    )
    ha_status_task = safe_query(
        ha_qb.ha_cluster_status, "overview.ha_cluster_status",
        site_name="Site_FGT-DC",
    )
    iface_tasks = []
    iface_interval = _compute_interval(gte_ms, lte_ms)
    for site_name in ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]:
        iface_tasks.append(safe_query(
            iface_qb.interface_stats_timeline, f"overview.iface_{site_name}",
            gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, interval=iface_interval,
        ))

    # Execute all in parallel
    all_results = await asyncio.gather(
        ssl_task, ipsec_task, devices_task, sparklines_task,
        top_apps_task, top_as_orgs_task, sdwan_task, total_bytes_task,
        inbound_task, ha_status_task, *iface_tasks,
        return_exceptions=True,
    )

    # Unpack
    ssl_data, ssl_err = all_results[0]
    ipsec_data, ipsec_err = all_results[1]
    devices_raw, devices_err = all_results[2]
    sparklines_raw, spark_err = all_results[3]
    top_apps_raw, top_apps_err = all_results[4]
    top_as_orgs_raw, top_as_orgs_err = all_results[5]
    sdwan_raw, sdwan_err = all_results[6]
    total_bytes_raw, total_bytes_err = all_results[7]
    inbound_raw, inbound_err = all_results[8]
    ha_raw, ha_err = all_results[9]
    iface_results = all_results[10:13]

    # Collect errors
    for name, err in [
        ("sslvpn", ssl_err), ("ipsec", ipsec_err), ("devices", devices_err),
        ("sparklines", spark_err), ("top_apps", top_apps_err),
        ("top_as_orgs", top_as_orgs_err), ("sdwan", sdwan_err),
        ("total_bytes", total_bytes_err), ("inbound", inbound_err),
        ("ha_status", ha_err),
    ]:
        if err:
            errors.append(f"{name}: {err}")

    # Build sparkline map (use empty dict on failure)
    sparkline_map: dict = {}
    if isinstance(sparklines_raw, list):
        for s in sparklines_raw:
            sparkline_map[s.get("device", "")] = s.get("points", [])

    # Devices
    devices_raw_list = devices_raw if isinstance(devices_raw, list) else []
    devices = [
        DeviceResourceStatus(
            device=d["device"],
            hostname=d.get("hostname"),
            serial_number=d.get("serial_number", ""),
            cpu_usage=d["cpu_usage"],
            mem_usage=d["mem_usage"],
            session_count=d["session_count"],
            sync_status=d["sync_status"],
            session_sparkline=[
                SparklinePoint(timestamp=p["timestamp"], value=p["value"])
                for p in sparkline_map.get(d.get("hostname", d["device"]), [])
            ],
        )
        for d in devices_raw_list
    ]
    dc_device_count = len(devices_raw_list)
    fortigate_device_count = dc_device_count + 2

    # Top apps
    top_apps_raw_list = top_apps_raw if isinstance(top_apps_raw, list) else []
    top_apps = [
        TopApplication(
            application=a["application"],
            total_bytes=a["total_bytes"],
            bytes_human=_format_bytes(a["total_bytes"]),
        )
        for a in top_apps_raw_list
    ]

    # Top AS orgs
    top_as_orgs_raw_list = top_as_orgs_raw if isinstance(top_as_orgs_raw, list) else []
    top_as_orgs = [
        TopASOrg(
            org_name=a["as_org"],
            total_bytes=a["total_bytes"],
            bytes_human=_format_bytes(a["total_bytes"]),
        )
        for a in top_as_orgs_raw_list
    ]

    # SD-WAN
    sdwan_raw_list = sdwan_raw if isinstance(sdwan_raw, list) else []
    sdwan_sites = [
        SiteWanStatus(
            site=s["site"],
            device=s.get("device"),
            links=[
                {"link": l["link"], "link_name": l["label"], "status": l["status"]}
                for l in s.get("links", [])
            ],
        )
        for s in sdwan_raw_list
    ]

    # Total throughput
    total_bytes = int(total_bytes_raw) if isinstance(total_bytes_raw, (int, float)) else 0

    # HA status
    ha_status = None
    if isinstance(ha_raw, dict):
        ha_status = HAStatusKPI(
            ha_mode=ha_raw.get("ha_mode", "standalone"),
            member_count=len(ha_raw.get("members", [])),
            overall_health=ha_raw.get("overallHealth", "unknown"),
        )

    # WAN bandwidth per site
    wan_bandwidth = []
    for idx, site_name in enumerate(["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]):
        iface_data, iface_err = iface_results[idx]
        if iface_err:
            errors.append(f"iface_{site_name}: {iface_err}")
            continue
        if not isinstance(iface_data, dict):
            continue
        aggs = iface_data.get("aggregations", {})
        interval_seconds = iface_data.get("interval_seconds", 60)
        labels = iface_qb.SITE_IFINDEX_MAP.get(site_name, {})
        ifaces = []
        for b in aggs.get("by_interface", {}).get("buckets", []):
            idx_key = b["key"]
            lbl = labels.get(idx_key, f"Interface {idx_key}")
            time_buckets = b.get("by_time", {}).get("buckets", [])
            in_mbps = None
            out_mbps = None
            speed_mbps = None
            oper_status = None
            for tb in reversed(time_buckets):
                sv = tb.get("speed_mbps", {}).get("value")
                ov = tb.get("oper_status", {}).get("value")
                if sv is not None and speed_mbps is None:
                    speed_mbps = int(sv)
                if ov is not None and oper_status is None:
                    oper_status = "UP" if int(ov) == 1 else "DOWN"
                if speed_mbps is not None and oper_status is not None:
                    break
            if len(time_buckets) >= 2:
                prev_in = time_buckets[-2].get("max_in_octets", {}).get("value")
                curr_in = time_buckets[-1].get("max_in_octets", {}).get("value")
                prev_out = time_buckets[-2].get("max_out_octets", {}).get("value")
                curr_out = time_buckets[-1].get("max_out_octets", {}).get("value")
                if prev_in is not None and curr_in is not None:
                    delta = curr_in - prev_in
                    if delta >= 0:
                        in_mbps = round(delta * 8 / interval_seconds / 1_000_000, 2)
                if prev_out is not None and curr_out is not None:
                    delta = curr_out - prev_out
                    if delta >= 0:
                        out_mbps = round(delta * 8 / interval_seconds / 1_000_000, 2)
            ifaces.append(WanInterfaceSummary(
                label=lbl,
                in_mbps=in_mbps,
                out_mbps=out_mbps,
                speed_mbps=speed_mbps,
                oper_status=oper_status,
            ))
        wan_bandwidth.append(SiteWanBandwidth(site=site_name, interfaces=ifaces))

    # Inbound VIP services
    inbound_vip_services = []
    if isinstance(inbound_raw, dict):
        for s in (inbound_raw.get("top_services", []) or [])[:5]:
            inbound_vip_services.append(TopInboundService(
                service_name=s["service_name"],
                total_bytes=s["total_bytes"],
                bytes_human=_format_bytes(s["total_bytes"]),
            ))

    # Active alert count
    active_alert_count = 0
    try:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                text("SELECT COUNT(*) FROM alert_logs WHERE acknowledged = false")
            )
            active_alert_count = result.scalar() or 0
    except Exception:
        pass

    elapsed = int((time.monotonic() - t0) * 1000)
    meta: dict = {"query_took_ms": elapsed}
    if errors:
        meta["partial_errors"] = errors[:5]  # limit to first 5
        logger.warning(f"overview partial errors ({len(errors)}): {errors[:3]}")

    return APIResponse.ok(
        data=OverviewResponse(
            ssl_vpn_users=ActiveUserKPI(active_users=int(ssl_data) if isinstance(ssl_data, (int, float)) else 0, label="SSL VPN"),
            ipsec_vpn_users=ActiveUserKPI(active_users=int(ipsec_data) if isinstance(ipsec_data, (int, float)) else 0, label="IPsec VPN"),
            fortigate_device_count=fortigate_device_count,
            devices=devices,
            top_applications=top_apps,
            top_dst_as_orgs=top_as_orgs,
            sdwan_sites=sdwan_sites,
            total_throughput=ThroughputKPI(
                total_bytes=total_bytes,
                bytes_human=_format_bytes(total_bytes),
            ),
            ha_status=ha_status,
            wan_bandwidth=wan_bandwidth,
            inbound_vip_services=inbound_vip_services,
            active_alert_count=active_alert_count,
        ),
        meta=meta,
    )
