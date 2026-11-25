"""Ping Monitor API (plans/2026-10-21_ping-monitor.md §3.2, page design 2026-11-24).

Curated ICMP targets: an admin picks a (cluster, tag.url) that OpenSearch already
reports for measurement=ping; nothing is auto-listed. Reads are viewer+; add / edit /
delete and the discover picker are admin+. Never returns raw OpenSearch _source —
rows are mapped to the schemas below (AGENTS.md).

DB reads use a short-lived session closed BEFORE the OpenSearch query, so a stalled
cluster (e.g. DC unreachable) can't pin pooled connections (same fix as topology).
"""
from __future__ import annotations

import logging
import time
from typing import Literal, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._safe import build_meta, safe_query
from app.api.auth import get_current_user, require_role
from app.core.config import get_settings
from app.db.models import PingTarget, User
from app.db.session import AsyncSessionLocal, get_db
from app.opensearch import ping as ping_qb
from app.opensearch.query import track_degradation
from app.schemas.common import APIResponse

logger = logging.getLogger("nod.api")

router = APIRouter(prefix="/api/v1/ping", tags=["Ping Monitor"])

Cluster = Literal["dc", "drc"]
GroupKind = Literal["internal", "internet", "dr"]

_MAX_RANGE_MS = 31 * 86_400_000  # custom ranges are capped at 31 days


# ── Schemas ──────────────────────────────────────────────────────


class PingMetrics(BaseModel):
    avg_ms: Optional[float] = None
    min_ms: Optional[float] = None
    max_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    loss_pct: Optional[float] = None
    availability_pct: Optional[float] = None
    samples: int = 0


class PingTargetRead(BaseModel):
    id: str
    url: str
    cluster: str
    display_name: str          # resolved: admin name → tag.target_name → url
    custom_name: Optional[str]  # the admin-set value (for the edit form)
    group_kind: str
    expected_down: bool
    enabled: bool
    status: str                # up | degraded | down | expected_down | disabled | no_data
    last_seen_ms: Optional[int] = None
    metrics: PingMetrics


class PingTargetsResponse(BaseModel):
    window_ms: int
    warn_loss_pct: float
    warn_latency_ms: float
    targets: list[PingTargetRead]


class DiscoveredUrl(BaseModel):
    cluster: str
    url: str
    target_name: Optional[str] = None
    last_seen_ms: Optional[int] = None


class PingPoint(BaseModel):
    ts_ms: int
    avg_ms: Optional[float] = None
    max_ms: Optional[float] = None
    jitter_ms: Optional[float] = None
    loss_pct: Optional[float] = None


class PingSeriesResponse(BaseModel):
    gte_ms: int
    lte_ms: int
    bucket_seconds: int
    stats: PingMetrics
    points: list[PingPoint]


class PingTargetCreate(BaseModel):
    url: str = Field(..., min_length=1, max_length=253)
    cluster: Cluster
    display_name: Optional[str] = Field(default=None, max_length=255)
    group_kind: GroupKind = "internal"
    expected_down: bool = False


class PingTargetUpdate(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=255)
    group_kind: Optional[GroupKind] = None
    expected_down: Optional[bool] = None
    enabled: Optional[bool] = None


# ── Helpers ──────────────────────────────────────────────────────


def _to_read(t: PingTarget, m: Optional[dict]) -> PingTargetRead:
    s = get_settings()
    m = m or {}
    return PingTargetRead(
        id=t.id, url=t.url, cluster=t.cluster,
        display_name=t.display_name or m.get("target_name") or t.url,
        custom_name=t.display_name,
        group_kind=t.group_kind, expected_down=t.expected_down, enabled=t.enabled,
        status=ping_qb.derive_status(t.enabled, t.expected_down, m,
                                     s.PING_WARN_LOSS_PCT, s.PING_WARN_LATENCY_MS),
        last_seen_ms=m.get("last_seen_ms"),
        metrics=PingMetrics(**{k: m[k] for k in PingMetrics.model_fields if k in m}),
    )


async def _all_targets() -> list[PingTarget]:
    async with AsyncSessionLocal() as db:
        return list((await db.execute(select(PingTarget).order_by(PingTarget.url))).scalars().all())


async def _get_target(db: AsyncSession, target_id: str) -> PingTarget:
    row = (await db.execute(select(PingTarget).where(PingTarget.id == target_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ping target not found")
    return row


# ── Routes ───────────────────────────────────────────────────────


@router.get("/targets", response_model=APIResponse[PingTargetsResponse])
async def list_targets(
    cluster: Optional[Cluster] = Query(None),
    current_user=Depends(get_current_user),
):
    """Curated targets joined with live metrics over the last LIVE_WINDOW_MS."""
    t0 = time.monotonic()
    degraded = track_degradation()
    targets = [t for t in await _all_targets() if cluster is None or t.cluster == cluster]
    by_cluster: dict[str, list[str]] = {}
    for t in targets:
        if t.enabled:
            by_cluster.setdefault(t.cluster, []).append(t.url)
    gte, lte = ping_qb.live_window()
    live, err = await safe_query(ping_qb.ping_live, "ping_live", targets=by_cluster, gte_ms=gte, lte_ms=lte)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    live = live or {}
    s = get_settings()
    return APIResponse.ok(data=PingTargetsResponse(
        window_ms=ping_qb.LIVE_WINDOW_MS,
        warn_loss_pct=s.PING_WARN_LOSS_PCT, warn_latency_ms=s.PING_WARN_LATENCY_MS,
        targets=[_to_read(t, live.get((t.cluster, t.url))) for t in targets],
    ), meta=meta)


@router.get("/targets/{target_id}/series", response_model=APIResponse[PingSeriesResponse])
async def target_series(
    target_id: str,
    gte_ms: Optional[int] = Query(None),
    lte_ms: Optional[int] = Query(None),
    current_user=Depends(get_current_user),
):
    """Detail charts + stat tiles for one target. Defaults to the last 1 hour."""
    t0 = time.monotonic()
    degraded = track_degradation()
    async with AsyncSessionLocal() as db:
        t = await _get_target(db, target_id)
        cluster, url = t.cluster, t.url
    now = int(time.time() * 1000)
    if gte_ms is None or lte_ms is None or lte_ms <= gte_ms:
        gte_ms, lte_ms = now - 3_600_000, now
    gte_ms = max(gte_ms, lte_ms - _MAX_RANGE_MS)
    data, err = await safe_query(ping_qb.ping_series, "ping_series",
                                 cluster=cluster, url=url, gte_ms=gte_ms, lte_ms=lte_ms)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    data = data or {"bucket_seconds": ping_qb.bucket_seconds(gte_ms, lte_ms), "points": [], "stats": {}}
    return APIResponse.ok(data=PingSeriesResponse(
        gte_ms=gte_ms, lte_ms=lte_ms, bucket_seconds=data["bucket_seconds"],
        stats=PingMetrics(**data["stats"]),
        points=[PingPoint(**p) for p in data["points"]],
    ), meta=meta)


@router.get("/discover", response_model=APIResponse[list[DiscoveredUrl]],
            dependencies=[Depends(require_role("admin"))])
async def discover():
    """Ping urls OpenSearch reports (last 15 min) that are NOT curated yet."""
    t0 = time.monotonic()
    degraded = track_degradation()
    curated = {(t.cluster, t.url) for t in await _all_targets()}
    gte, lte = ping_qb.live_window()
    found, err = await safe_query(ping_qb.discover_ping_urls, "discover_ping_urls", gte_ms=gte, lte_ms=lte)
    meta = build_meta(int((time.monotonic() - t0) * 1000), degraded, err)
    rows = [DiscoveredUrl(**d) for d in (found or []) if (d["cluster"], d["url"]) not in curated]
    rows.sort(key=lambda d: (d.cluster, d.url))
    return APIResponse.ok(data=rows, meta=meta)


@router.post("/targets", response_model=APIResponse[PingTargetRead], status_code=status.HTTP_201_CREATED)
async def create_target(
    body: PingTargetCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("admin")),
):
    row = PingTarget(
        url=body.url.strip(), cluster=body.cluster,
        display_name=(body.display_name or "").strip() or None,
        group_kind=body.group_kind, expected_down=body.expected_down,
        created_by=current_user.id,
    )
    db.add(row)
    try:
        await db.flush()
    except IntegrityError:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT,
                            detail=f"{body.url} on {body.cluster.upper()} is already monitored")
    await db.refresh(row)
    return APIResponse.ok(data=_to_read(row, None))


@router.patch("/targets/{target_id}", response_model=APIResponse[PingTargetRead],
              dependencies=[Depends(require_role("admin"))])
async def update_target(target_id: str, body: PingTargetUpdate, db: AsyncSession = Depends(get_db)):
    """Edit display metadata. url + cluster are the identity and stay fixed."""
    row = await _get_target(db, target_id)
    if body.display_name is not None:
        row.display_name = body.display_name.strip() or None
    if body.group_kind is not None:
        row.group_kind = body.group_kind
    if body.expected_down is not None:
        row.expected_down = body.expected_down
    if body.enabled is not None:
        row.enabled = body.enabled
    await db.flush()
    await db.refresh(row)
    return APIResponse.ok(data=_to_read(row, None))


@router.delete("/targets/{target_id}", response_model=APIResponse[dict],
               dependencies=[Depends(require_role("admin"))])
async def delete_target(target_id: str, db: AsyncSession = Depends(get_db)):
    row = await _get_target(db, target_id)
    await db.delete(row)
    return APIResponse.ok(data={"id": target_id})
