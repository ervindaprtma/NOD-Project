"""
FortiGate Resource View API (FR-04).
Supports HA cluster (DC) and single-device (DRC/Office) sites.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import ha as ha_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import (
    DeviceCurrentResource,
    ResourcePoint,
    ResourceResponse,
    ResourceTimeline,
)

router = APIRouter(prefix="/api/v1/resources", tags=["Resources"])

# Sites that have HA (DC only)
HA_SITES = {"Site_FGT-DC"}
# Sites that have single-device resource data
RESOURCE_SITES = {"Site_FGT-DRC", "Site_FGT_Office"}


def _compute_interval(gte_ms: int, lte_ms: int) -> str:
    delta_sec = (lte_ms - gte_ms) / 1000
    if delta_sec <= 7200:
        return "1m"
    elif delta_sec <= 43200:
        return "5m"
    else:
        return "15m"


@router.get("", response_model=APIResponse[ResourceResponse])
async def get_resources(
    gte_ms: int = Query(..., description="Start timestamp (epoch ms)"),
    lte_ms: int = Query(..., description="End timestamp (epoch ms)"),
    site_name: str = Query("Site_FGT-DC", description="Site name"),
    current_user=Depends(get_current_user),
):
    """
    FR-04: CPU, Memory, Session count timelines + current status per device.
    - Site_FGT-DC: HA cluster (ha_member measurement, 2 devices)
    - Site_FGT-DRC / Site_FGT_Office: Single device (Resource_FGT-* measurement)
    """
    t0 = time.monotonic()
    interval = _compute_interval(gte_ms, lte_ms)

    # ── DC (HA cluster) ──────────────────────────────────────────
    if site_name in HA_SITES:
        timeline_raw = await ha_qb.resource_timeline(
            gte_ms=gte_ms, lte_ms=lte_ms, interval=interval
        )
        current_raw = await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)

        elapsed = int((time.monotonic() - t0) * 1000)
        return APIResponse.ok(
            data=ResourceResponse(
                timeline=ResourceTimeline(
                    cpu=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=p["device"]) for p in timeline_raw["cpu"]],
                    memory=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=p["device"]) for p in timeline_raw["memory"]],
                    sessions=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=p["device"]) for p in timeline_raw["sessions"]],
                ),
                current=[
                    DeviceCurrentResource(
                        device=d["device"],
                        hostname=d.get("hostname"),
                        serial_number=d.get("serial_number"),
                        cpu_usage=d["cpu_usage"],
                        mem_usage=d["mem_usage"],
                        session_count=d["session_count"],
                        sync_status=d["sync_status"],
                    )
                    for d in current_raw
                ],
            ),
            meta={"query_took_ms": elapsed, "site": site_name, "mode": "ha"},
        )

    # ── DRC / Office (single device) ─────────────────────────────
    if site_name in RESOURCE_SITES:
        status_raw = await ha_qb.resource_device_status(
            site_name=site_name, gte_ms=gte_ms, lte_ms=lte_ms
        )
        timeline_raw = await ha_qb.resource_device_timeline(
            site_name=site_name, gte_ms=gte_ms, lte_ms=lte_ms, interval=interval
        )

        current_list: list[DeviceCurrentResource] = []
        if status_raw:
            current_list.append(
                DeviceCurrentResource(
                    device=status_raw["site"],
                    hostname=status_raw.get("source_ip"),
                    serial_number=status_raw.get("serial_number"),
                    cpu_usage=status_raw["cpu_usage_percent"],
                    mem_usage=status_raw["mem_usage_percent"],
                    session_count=status_raw["session_count"],
                    sync_status="standalone",
                    mem_capacity_kb=status_raw.get("mem_capacity_kb", 0),
                )
            )

        elapsed = int((time.monotonic() - t0) * 1000)
        return APIResponse.ok(
            data=ResourceResponse(
                timeline=ResourceTimeline(
                    cpu=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=site_name) for p in timeline_raw["cpu"]],
                    memory=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=site_name) for p in timeline_raw["memory"]],
                    sessions=[ResourcePoint(timestamp=p["timestamp"], value=p["value"], device=site_name) for p in timeline_raw["sessions"]],
                ),
                current=current_list,
            ),
            meta={"query_took_ms": elapsed, "site": site_name, "mode": "standalone"},
        )

    # ── Unknown site ──────────────────────────────────────────────
    return APIResponse.ok(
        data=ResourceResponse(
            timeline=ResourceTimeline(cpu=[], memory=[], sessions=[]),
            current=[],
        ),
        meta={"query_took_ms": 0, "site": site_name, "mode": "unknown"},
    )
