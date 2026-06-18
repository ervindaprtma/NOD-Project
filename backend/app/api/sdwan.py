"""
SD-WAN SLA API (FR-03).
Supports 3 sites × 4 links with WAN/MPLS labeling.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.core.config import get_settings
from app.opensearch import sdwan as sdwan_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import (
    SDWANResponse,
    SLATimeline,
    SLASummaryKPI,
    SiteSLAStatus,
    LinkCurrentStatus,
    LinkMetricPoint,
    SITE_LINK_LABELS,
)

settings = get_settings()
router = APIRouter(prefix="/api/v1/sdwan", tags=["SD-WAN"])


def _compute_interval(gte_ms: int, lte_ms: int) -> str:
    delta_sec = (lte_ms - gte_ms) / 1000
    if delta_sec <= 7200:
        return "1m"
    elif delta_sec <= 43200:
        return "5m"
    else:
        return "15m"


@router.get("/sla", response_model=APIResponse[SDWANResponse])
async def get_sdwan_sla(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    site_name: str = Query(default="Site_FGT-DC", description="SD-WAN site measurement_name"),
    current_user=Depends(get_current_user),
):
    """FR-03: SD-WAN SLA latency, jitter, packet loss, link status (4 links: 2×WAN + 2×MPLS)."""
    t0 = time.monotonic()
    interval = _compute_interval(gte_ms, lte_ms)

    # Validate site_name
    if site_name not in settings.sdwan_sites_list:
        return APIResponse.fail(
            code="VALIDATION_ERROR",
            message=f"Unknown site: {site_name}. Configured: {settings.sdwan_sites_list}",
        )

    # Pre-fetch validation: check if any SLA data exists for this site/time-range
    validation = await sdwan_qb.validate_sla_data(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name
    )
    if validation["total_hits"] == 0:
        return APIResponse.fail(
            code="NO_DATA",
            message="No SLA data for this site in selected range",
        )

    source_ip: str = validation.get("source_ip", "")

    # SLA-02/03/04: Timelines (flat list with label + link_type)
    lat_raw = await sdwan_qb.sla_timeline(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, metric="latency", interval=interval
    )
    jit_raw = await sdwan_qb.sla_timeline(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, metric="jitter", interval=interval
    )
    loss_raw = await sdwan_qb.sla_timeline(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name, metric="packet_loss", interval=interval
    )

    latency_timeline = SLATimeline(links=[
        LinkMetricPoint(timestamp=p["timestamp"], value=p["value"], label=p["label"], link_type=p["link_type"])
        for p in lat_raw
    ])
    jitter_timeline = SLATimeline(links=[
        LinkMetricPoint(timestamp=p["timestamp"], value=p["value"], label=p["label"], link_type=p["link_type"])
        for p in jit_raw
    ])
    packet_loss_timeline = SLATimeline(links=[
        LinkMetricPoint(timestamp=p["timestamp"], value=p["value"], label=p["label"], link_type=p["link_type"])
        for p in loss_raw
    ])

    # SLA-05: Link Status
    link_status_raw = await sdwan_qb.sdwan_link_status(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name
    )

    link_status = SiteSLAStatus(
        site=site_name,
        links=[
            LinkCurrentStatus(
                link=l["link"],
                ifname=l["ifname"],
                label=l["label"],
                link_type=l["link_type"],
                status=l["status"],
                sla_target=l["sla_target"],
            )
            for l in link_status_raw
        ],
    )

    # SLA-06: Summary
    summary_raw = await sdwan_qb.sla_summary(
        gte_ms=gte_ms, lte_ms=lte_ms, site_name=site_name
    )

    summary = SLASummaryKPI(
        avg_latency=summary_raw["avg_latency"],
        max_latency=summary_raw["max_latency"],
        avg_jitter=summary_raw["avg_jitter"],
        avg_packet_loss=summary_raw["avg_packet_loss"],
        labels=summary_raw["labels"],
        link_types=summary_raw["link_types"],
    )

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=SDWANResponse(
            latency_timeline=latency_timeline,
            jitter_timeline=jitter_timeline,
            packet_loss_timeline=packet_loss_timeline,
            link_status=[link_status],
            summary=summary,
            source_ip=source_ip,
        ),
        meta={"query_took_ms": elapsed},
    )
