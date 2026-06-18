"""
HA (High Availability) Status API.
Queries FortiGate HA cluster health — members, sync status, cluster mode.
"""
from __future__ import annotations

import time

from fastapi import APIRouter, Depends, Query

from app.api.auth import get_current_user
from app.opensearch import ha as ha_qb
from app.schemas.common import APIResponse
from app.schemas.sdwan_resource_vpn import HAResponse, HAMember

router = APIRouter(prefix="/api/v1/ha", tags=["HA Status"])


@router.get("/status", response_model=APIResponse[HAResponse])
async def get_ha_status(
    site_name: str = Query(
        default="Site_FGT-DC",
        description="Site name for HA status (only Site_FGT-DC has HA configured)",
    ),
    current_user=Depends(get_current_user),
):
    """
    Get HA cluster status for a given site.

    Only Site_FGT-DC (telegraf cluster, tag.source=10.80.150.1) has HA configured.
    Other sites return standalone/critical with a descriptive message.

    Queries the last 5 minutes of telegraf-index* data:
      - measurement_name=ha_member (per-member stats + sync_status)
      - measurement_name=Site_FGT-DC_HA (cluster config: ha_mode, priority)

    Health logic:
      - healthy:   ha_mode != standalone AND all members sync_status == 1 (in-sync)
      - degraded:  ha_mode != standalone AND any member sync_status != 1
      - critical:  ha_mode == standalone OR member count < 2
    """
    t0 = time.monotonic()

    raw = await ha_qb.ha_cluster_status(site_name=site_name)

    # Build typed members list
    members = [
        HAMember(
            memberIndex=m["memberIndex"],
            role=m["role"],
            syncStatus=m["syncStatus"],
            priority=m["priority"],
            hostname=m["hostname"],
        )
        for m in raw.get("members", [])
    ]

    elapsed = int((time.monotonic() - t0) * 1000)

    return APIResponse.ok(
        data=HAResponse(
            ha_mode=raw["ha_mode"],
            members=members,
            overallHealth=raw["overallHealth"],
            message=raw.get("message", ""),
        ),
        meta={"query_took_ms": elapsed},
    )
