"""
Traffic Flow API (FR-02).
Returns aggregated traffic analytics data.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import appid as appid_qb
from app.schemas.common import APIResponse
from app.schemas.traffic import TrafficSummaryResponse

router = APIRouter(prefix="/api/v1/traffic", tags=["Traffic"])


def _fmt(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    elif n < 1024**2:
        return f"{n / 1024:.1f} KB"
    elif n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    else:
        return f"{n / 1024**3:.2f} GB"


def _compute_interval(gte_ms: int, lte_ms: int) -> str:
    """Choose appropriate fixed_interval based on time range size."""
    delta_sec = (lte_ms - gte_ms) / 1000
    if delta_sec <= 7200:
        return "1m"
    elif delta_sec <= 43200:
        return "5m"
    else:
        return "15m"


@router.get("/summary", response_model=APIResponse[TrafficSummaryResponse])
async def traffic_summary(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    current_user=Depends(get_current_user),
):
    """
    FR-02: Returns all traffic analytics panels in a single call.
    """
    t0 = time.monotonic()
    interval = _compute_interval(gte_ms, lte_ms)

    # TF-01: Top Applications
    top_apps_raw = await appid_qb.top_applications(gte_ms=gte_ms, lte_ms=lte_ms, size=20)
    top_applications = [
        {"application": a["application"], "total_bytes": a["total_bytes"], "bytes_human": _fmt(a["total_bytes"])}
        for a in top_apps_raw
    ]

    # TF-02: Application Categories
    cats_raw = await appid_qb.application_categories(gte_ms=gte_ms, lte_ms=lte_ms)
    categories = [
        {"category": c["category"], "total_bytes": c["total_bytes"], "bytes_human": _fmt(c["total_bytes"])}
        for c in cats_raw
    ]

    # TF-03: Sankey — use max size 500 to avoid composite agg truncation
    sankey = await appid_qb.sankey_data(gte_ms=gte_ms, lte_ms=lte_ms, size=500)

    # TF-04: Throughput Timeline
    tp_raw = await appid_qb.throughput_timeline(gte_ms=gte_ms, lte_ms=lte_ms, interval=interval)
    throughput_timeline = [
        {"timestamp": p["timestamp"], "bytes": p["bytes"]} for p in tp_raw
    ]

    # TF-05: Top Client IPs
    clients_raw = await appid_qb.top_client_ips(gte_ms=gte_ms, lte_ms=lte_ms, size=20)
    top_clients = [
        {"ip": c["ip"], "total_bytes": c["total_bytes"], "bytes_human": _fmt(c["total_bytes"])}
        for c in clients_raw
    ]

    # TF-06: Top Server IPs
    servers_raw = await appid_qb.top_server_ips(gte_ms=gte_ms, lte_ms=lte_ms, size=20)
    top_servers = [
        {"ip": s["ip"], "total_bytes": s["total_bytes"], "bytes_human": _fmt(s["total_bytes"])}
        for s in servers_raw
    ]

    # TF-07: Protocol Distribution
    protos_raw = await appid_qb.protocol_distribution(gte_ms=gte_ms, lte_ms=lte_ms)
    protocols = [
        {"protocol": p["protocol"], "total_bytes": p["total_bytes"], "total_packets": p["total_packets"]}
        for p in protos_raw
    ]

    # TF-08: Egress Interface Breakdown
    egr_raw = await appid_qb.egress_interface_breakdown(gte_ms=gte_ms, lte_ms=lte_ms)
    egress_interfaces = [
        {"interface": e["interface"], "total_bytes": e["total_bytes"], "bytes_human": _fmt(e["total_bytes"])}
        for e in egr_raw
    ]

    # TF-09: Top Destination AS Countries
    as_countries_raw = await appid_qb.top_dst_as_countries(gte_ms=gte_ms, lte_ms=lte_ms, size=20)
    top_as_countries = [
        {"country": c["country"], "total_bytes": c["total_bytes"], "bytes_human": _fmt(c["total_bytes"])}
        for c in as_countries_raw
    ]

    # TF-10: Top Destination AS Organizations
    as_orgs_raw = await appid_qb.top_dst_as_orgs(gte_ms=gte_ms, lte_ms=lte_ms, size=20)
    top_as_orgs = [
        {"as_org": a["as_org"], "as_number": a["as_number"], "total_bytes": a["total_bytes"], "bytes_human": _fmt(a["total_bytes"]), "country": a.get("country", "")}
        for a in as_orgs_raw
    ]

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=TrafficSummaryResponse(
            top_applications=top_applications,
            categories=categories,
            sankey=sankey,
            throughput_timeline=throughput_timeline,
            top_clients=top_clients,
            top_servers=top_servers,
            protocols=protocols,
            egress_interfaces=egress_interfaces,
            top_as_countries=top_as_countries,
            top_as_orgs=top_as_orgs,
        ),
        meta={"query_took_ms": elapsed},
    )
