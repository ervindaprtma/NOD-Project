"""
Overview Dashboard API (FR-01).
Returns aggregated data for all overview panels in a single response.
Q-07: uses single query per panel, NO N+1 patterns.
"""
from __future__ import annotations

import time
import math
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.auth import get_current_user
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


@router.get("/overview", response_model=APIResponse[OverviewResponse])
async def get_overview(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """FR-01: Returns all overview panels in a single API call."""
    t0 = time.monotonic()

    # P01-A: Active SSL VPN Users
    ssl_count = await sslvpn_qb.all_sslvpn_users_count(
        gte_ms=gte_ms, lte_ms=lte_ms, site_names=settings.sslvpn_sites_list
    )

    # P01-B: Active IPsec VPN Users
    ipsec_count = await ipsec_qb.active_ipsec_users_count(gte_ms=gte_ms, lte_ms=lte_ms)

    # P01-C/D/E/F: FortiGate Device Resources (DC HA members)
    devices_raw = await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)
    sparklines_raw = await ha_qb.session_sparkline(gte_ms=gte_ms, lte_ms=lte_ms)
    sparkline_map: dict[str, list] = {s["device"]: s["points"] for s in sparklines_raw}

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
        for d in devices_raw
    ]

    # FortiGate device count: DC HA members + DRC (1) + Office (1)
    dc_device_count = len(devices_raw)
    fortigate_device_count = dc_device_count + 2  # DRC + Office

    # P01-G: Top 10 Traffic Applications
    top_apps_raw = await appid_qb.top_applications(gte_ms=gte_ms, lte_ms=lte_ms, size=10)
    top_apps = [
        TopApplication(
            application=a["application"],
            total_bytes=a["total_bytes"],
            bytes_human=_format_bytes(a["total_bytes"]),
        )
        for a in top_apps_raw
    ]

    # Top Destination AS Organizations
    top_as_orgs_raw = await appid_qb.top_dst_as_orgs(gte_ms=gte_ms, lte_ms=lte_ms, size=10)
    top_as_orgs = [
        TopASOrg(
            org_name=a["as_org"],
            total_bytes=a["total_bytes"],
            bytes_human=_format_bytes(a["total_bytes"]),
        )
        for a in top_as_orgs_raw
    ]

    # P01-H: SD-WAN Link Status
    sdwan_raw = await sdwan_qb.all_sites_link_status(
        gte_ms=gte_ms, lte_ms=lte_ms, site_names=settings.sdwan_sites_list
    )
    sdwan_sites = [
        SiteWanStatus(
            site=s["site"],
            device=s.get("device"),
            links=[
                {"link": l["link"], "link_name": l["label"], "status": l["status"]}
                for l in s["links"]
            ],
        )
        for s in sdwan_raw
    ]

    # P01-I: Total Throughput
    total_bytes = await appid_qb.total_throughput(gte_ms=gte_ms, lte_ms=lte_ms)

    # HA Status (DC only)
    ha_status = None
    try:
        ha_raw = await ha_qb.ha_cluster_status(site_name="Site_FGT-DC")
        ha_status = HAStatusKPI(
            ha_mode=ha_raw.get("ha_mode", "standalone"),
            member_count=len(ha_raw.get("members", [])),
            overall_health=ha_raw.get("overallHealth", "unknown"),
        )
    except Exception:
        pass

    # WAN Interface Bandwidth (all 3 sites, hardcoded ifIndex)
    wan_bandwidth = []
    for site_name in ["Site_FGT-DC", "Site_FGT-DRC", "Site_FGT_Office"]:
        try:
            aggs = await iface_qb.interface_stats_timeline(
                gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name
            )
            labels = iface_qb.SITE_IFINDEX_MAP.get(site_name, {})
            ifaces = []
            for b in aggs.get("by_interface", {}).get("buckets", []):
                idx = b["key"]
                lbl = labels.get(idx, f"Interface {idx}")
                time_buckets = b.get("by_time", {}).get("buckets", [])
                # Get latest throughput
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
                # Compute latest in/out Mbps from last 2 buckets
                if len(time_buckets) >= 2:
                    prev_in = time_buckets[-2].get("max_in_octets", {}).get("value")
                    curr_in = time_buckets[-1].get("max_in_octets", {}).get("value")
                    prev_out = time_buckets[-2].get("max_out_octets", {}).get("value")
                    curr_out = time_buckets[-1].get("max_out_octets", {}).get("value")
                    if prev_in is not None and curr_in is not None:
                        delta = curr_in - prev_in
                        if delta >= 0:
                            in_mbps = round(delta * 8 / 60 / 1_000_000, 2)
                    if prev_out is not None and curr_out is not None:
                        delta = curr_out - prev_out
                        if delta >= 0:
                            out_mbps = round(delta * 8 / 60 / 1_000_000, 2)
                ifaces.append(WanInterfaceSummary(
                    label=lbl,
                    in_mbps=in_mbps,
                    out_mbps=out_mbps,
                    speed_mbps=speed_mbps,
                    oper_status=oper_status,
                ))
            wan_bandwidth.append(SiteWanBandwidth(site=site_name, interfaces=ifaces))
        except Exception:
            pass

    # Inbound VIP Top Services
    inbound_vip_services = []
    try:
        inbound_raw = await ti_qb.flow_summary(gte_ms=gte_ms, lte_ms=lte_ms, site_name="Site_FGT-DC")
        inbound_vip_services = [
            TopInboundService(
                service_name=s["service_name"],
                total_bytes=s["total_bytes"],
                bytes_human=_format_bytes(s["total_bytes"]),
            )
            for s in (inbound_raw.get("top_services", []) or [])[:5]
        ]
    except Exception:
        pass

    # Active Alert Count (last 24h)
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

    return APIResponse.ok(
        data=OverviewResponse(
            ssl_vpn_users=ActiveUserKPI(active_users=ssl_count, label="SSL VPN"),
            ipsec_vpn_users=ActiveUserKPI(active_users=ipsec_count, label="IPsec VPN"),
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
        meta={"query_took_ms": elapsed},
    )
