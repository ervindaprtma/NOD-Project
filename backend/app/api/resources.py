"""
FortiGate Resource View API (FR-04).
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import ha as ha_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import DeviceCurrentResource, ResourceResponse, ResourceTimeline

router = APIRouter(prefix="/api/v1/resources", tags=["Resources"])


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
    current_user=Depends(get_current_user),
):
    """FR-04: CPU, Memory, Session count timelines + current status per device."""
    t0 = time.monotonic()
    interval = _compute_interval(gte_ms, lte_ms)

    # Q-07: single query for timeline across all devices
    timeline_raw = await ha_qb.resource_timeline(
        gte_ms=gte_ms, lte_ms=lte_ms, interval=interval
    )

    # Q-07: single query for current status across all devices
    current_raw = await ha_qb.current_device_status(gte_ms=gte_ms, lte_ms=lte_ms)

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=ResourceResponse(
            timeline=ResourceTimeline(
                cpu=[{"timestamp": p["timestamp"], "value": p["value"], "device": p["device"]} for p in timeline_raw["cpu"]],
                memory=[{"timestamp": p["timestamp"], "value": p["value"], "device": p["device"]} for p in timeline_raw["memory"]],
                sessions=[{"timestamp": p["timestamp"], "value": p["value"], "device": p["device"]} for p in timeline_raw["sessions"]],
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
        meta={"query_took_ms": elapsed},
    )
