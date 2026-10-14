"""Network Devices API — multi-vendor interface monitoring (plan v3 §5).

Read routes for the merged Resources page's Overview + Devices tabs. Current-state
only (roster, per-device interface boxes, fleet KPIs); charts/history land in
step 4, topology + label overrides in step 4/9.

All reads are viewer+ (get_current_user). Mutating routes (topology, label
overrides) are superadmin and live in their own modules. Never returns raw
OpenSearch _source — the builder already maps to clean dicts, and these schemas
are the API contract (AGENTS.md).
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._safe import build_meta, safe_query
from app.api.auth import get_current_user, require_role
from app.db.models import DeviceOverride
from app.db.session import get_db
from app.opensearch import device_uptime as du_qb
from app.opensearch import network_devices as nd_qb
from app.opensearch.query import track_degradation
from app.schemas.common import APIResponse

logger = logging.getLogger("nod.api")

router = APIRouter(prefix="/api/v1/network-devices", tags=["Network Devices"])


# ── Schemas ──────────────────────────────────────────────────────


class InterfaceCounts(BaseModel):
    up: int = 0
    down: int = 0
    admin_down: int = 0
    total: int = 0


class DeviceRow(BaseModel):
    source_ip: str
    vendor: str
    site: Optional[str] = None
    name: str
    online: bool
    last_seen_ms: int
    cluster: str
    counts: InterfaceCounts


class InterfaceBox(BaseModel):
    if_name: str
    ifindex: Optional[str] = None
    state: str  # up | down | admin_down
    oper: Optional[str] = None
    admin: Optional[str] = None
    speed_mbps: Optional[float] = None
    description: Optional[str] = None


class DeviceDetail(DeviceRow):
    interfaces: list[InterfaceBox] = []


class SummaryResponse(BaseModel):
    devices_total: int
    devices_online: int
    interfaces_up: int
    interfaces_down: int
    interfaces_admin_down: int
    by_site: dict[str, list[DeviceRow]]


class ChartPoint(BaseModel):
    # "in" is a Python keyword → field in_ with JSON alias "in" (serialized by alias)
    model_config = ConfigDict(populate_by_name=True)
    ts_ms: int
    in_: float = Field(0.0, alias="in")
    out: float = 0.0
    util_pct: Optional[float] = None


class ChartResponse(BaseModel):
    metric: str           # mb | mbps
    interval: str
    speed_mbps: Optional[float] = None  # None → zero-speed guard: no util series
    points: list[ChartPoint]


class StateTransition(BaseModel):
    # "from" is a Python keyword → field from_ with JSON alias "from"
    model_config = ConfigDict(populate_by_name=True)
    at_ms: int
    if_name: str
    from_: str = Field(alias="from")
    to: str
    duration_before_ms: int
    offline_gap: bool = False


class HistoryResponse(BaseModel):
    interval: str
    transitions: list[StateTransition]


def _window_ms(window: str, gte_ms: Optional[int], lte_ms: Optional[int]) -> tuple[int, int]:
    """Resolve a reporting window to (gte, lte). Explicit gte/lte override (drag-to-zoom)."""
    if gte_ms is not None and lte_ms is not None and lte_ms > gte_ms:
        return gte_ms, lte_ms
    now = int(time.time() * 1000)
    seconds = du_qb.WINDOW_SECONDS.get(window, du_qb.WINDOW_SECONDS["24h"])
    return now - seconds * 1000, now


async def _override_map(db: AsyncSession) -> dict[str, DeviceOverride]:
    """All device label overrides, keyed by source_ip (small config table)."""
    rows = (await db.execute(select(DeviceOverride))).scalars().all()
    return {r.source_ip: r for r in rows}


def _apply_override(d: dict, ov: DeviceOverride | None) -> dict:
    """Override wins over the SNMP value (plan §4.3): display_name and per-iface
    description. Mutates the device dict in place and returns it."""
    if ov is None:
        return d
    if ov.display_name:
        d["name"] = ov.display_name
    iface_ov = ov.iface_overrides or {}
    for i in d.get("interfaces", []):
        o = iface_ov.get(i["if_name"])
        if o and o.get("description"):
            i["description"] = o["description"]
    return d


def _row(d: dict) -> DeviceRow:
    return DeviceRow(
        source_ip=d["source_ip"], vendor=d["vendor"], site=d["site"], name=d["name"],
        online=d["online"], last_seen_ms=d["last_seen_ms"], cluster=d["cluster"],
        counts=InterfaceCounts(**d["counts"]),
    )


def _box(i: dict) -> InterfaceBox:
    idx = i.get("ifindex")
    return InterfaceBox(
        if_name=str(i["if_name"]),
        ifindex=str(idx) if idx is not None else None,
        state=i["state"], oper=i.get("oper"), admin=i.get("admin"),
        speed_mbps=i.get("speed_mbps"), description=i.get("description"),
    )


# ── Routes ───────────────────────────────────────────────────────


@router.get("/summary", response_model=APIResponse[SummaryResponse])
async def get_summary(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    """Overview tab: fleet KPI strip + per-device cards grouped by site.

    Current state only (fixed snapshot window); the 24h selector drives the
    step-4 charts, not these counts.
    """
    t0 = time.monotonic()
    degraded = track_degradation()
    gte, lte = nd_qb.current_window()
    devices, err = await safe_query(nd_qb.collect_devices, "collect_devices", gte_ms=gte, lte_ms=lte)
    elapsed = int((time.monotonic() - t0) * 1000)
    meta = build_meta(elapsed, degraded, err)
    if devices is None:
        return APIResponse.ok(data=SummaryResponse(
            devices_total=0, devices_online=0, interfaces_up=0, interfaces_down=0,
            interfaces_admin_down=0, by_site={}), meta=meta)
    ovs = await _override_map(db)
    for d in devices:
        _apply_override(d, ovs.get(d["source_ip"]))
    s = nd_qb.summarize(devices)
    return APIResponse.ok(data=SummaryResponse(
        devices_total=s["devices_total"], devices_online=s["devices_online"],
        interfaces_up=s["interfaces_up"], interfaces_down=s["interfaces_down"],
        interfaces_admin_down=s["interfaces_admin_down"],
        by_site={site: [_row(d) for d in devs] for site, devs in s["by_site"].items()},
    ), meta=meta)


@router.get("/devices", response_model=APIResponse[list[DeviceRow]])
async def list_devices(db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    """Devices tab master list: one row per physical device (tag.source), site-grouped
    client-side. Interface boxes are fetched per device on detail open."""
    t0 = time.monotonic()
    degraded = track_degradation()
    gte, lte = nd_qb.current_window()
    devices, err = await safe_query(nd_qb.collect_devices, "collect_devices", gte_ms=gte, lte_ms=lte)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    ovs = await _override_map(db)
    rows = [_row(_apply_override(d, ovs.get(d["source_ip"]))) for d in (devices or [])]
    return APIResponse.ok(data=rows, meta=meta)


@router.get("/devices/{source}/interfaces", response_model=APIResponse[DeviceDetail])
async def device_interfaces(source: str, db: AsyncSession = Depends(get_db), current_user=Depends(get_current_user)):
    """Device detail: state-coloured interface boxes for one device."""
    t0 = time.monotonic()
    degraded = track_degradation()
    gte, lte = nd_qb.current_window()
    dev, err = await safe_query(
        nd_qb.device_interfaces, "device_interfaces", source_ip=source, gte_ms=gte, lte_ms=lte)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    if not dev:
        return APIResponse.fail(code="NOT_FOUND", message=f"No current data for device {source}")
    ovs = await _override_map(db)
    _apply_override(dev, ovs.get(source))
    return APIResponse.ok(data=DeviceDetail(
        **_row(dev).model_dump(), interfaces=[_box(i) for i in dev["interfaces"]]), meta=meta)


class LabelPatch(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=255)
    # {if_name: {alias, description}} — override wins over the SNMP value.
    iface_overrides: Optional[dict[str, dict[str, str]]] = None


@router.patch("/devices/{source}/labels", response_model=APIResponse[dict],
              dependencies=[Depends(require_role("superadmin"))])
async def set_labels(source: str, body: LabelPatch, db: AsyncSession = Depends(get_db)):
    """Upsert per-device label overrides (plan §4.3, keyed by source_ip). Superadmin."""
    row = (await db.execute(
        select(DeviceOverride).where(DeviceOverride.source_ip == source))).scalar_one_or_none()
    if row is None:
        row = DeviceOverride(source_ip=source)
        db.add(row)
    if body.display_name is not None:
        row.display_name = body.display_name or None
    if body.iface_overrides is not None:
        row.iface_overrides = body.iface_overrides
    await db.commit()
    return APIResponse.ok(data={"source_ip": source})


@router.get("/devices/{source}/interfaces/{ifindex}/chart",
            response_model=APIResponse[ChartResponse])
async def interface_chart(
    source: str,
    ifindex: str,
    metric: str = Query("mbps", pattern="^(mb|mbps)$"),
    window: str = Query("24h"),
    gte_ms: Optional[int] = Query(None),
    lte_ms: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Per-interface chart: 'mb' volume (MB/bucket) or 'mbps' rate + utilization %.
    ifindex-keyed so interface names containing '/' never hit the URL."""
    t0 = time.monotonic()
    degraded = track_degradation()
    gte, lte = _window_ms(window, gte_ms, lte_ms)
    data, err = await safe_query(
        nd_qb.interface_chart, "interface_chart",
        source_ip=source, ifindex=ifindex, gte_ms=gte, lte_ms=lte, metric=metric)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    if not data:
        return APIResponse.fail(code="NOT_FOUND", message=f"No data for device {source} ifindex {ifindex}")
    return APIResponse.ok(data=ChartResponse(
        metric=data["metric"], interval=data["interval"], speed_mbps=data["speed_mbps"],
        points=[ChartPoint(ts_ms=p["ts_ms"], **{"in": p["in"]}, out=p["out"],
                           util_pct=p.get("util_pct")) for p in data["points"]],
    ), meta=meta)


@router.get("/devices/{source}/history", response_model=APIResponse[HistoryResponse])
async def state_history(
    source: str,
    window: str = Query("24h"),
    gte_ms: Optional[int] = Query(None),
    lte_ms: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Interface state-change history for one device (shared detector; offline gaps flagged)."""
    t0 = time.monotonic()
    degraded = track_degradation()
    gte, lte = _window_ms(window, gte_ms, lte_ms)
    data, err = await safe_query(
        nd_qb.state_history, "state_history", source_ip=source, gte_ms=gte, lte_ms=lte)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    if not data:
        return APIResponse.fail(code="NOT_FOUND", message=f"No data for device {source}")
    return APIResponse.ok(data=HistoryResponse(
        interval=data["interval"],
        transitions=[StateTransition(at_ms=t["at_ms"], if_name=t["if_name"],
                                     **{"from": t["from"]}, to=t["to"],
                                     duration_before_ms=t["duration_before_ms"],
                                     offline_gap=t["offline_gap"]) for t in data["transitions"]],
    ), meta=meta)
