"""Topology API (plan v3 §4) — the Network Devices Topology tab.

GET /api/v1/topology returns the saved nodes/links (Postgres) joined with three
live reads from raw SNMP (node freshness, per-end interface oper, link traffic),
all batched per device (H4). Read = any logged-in role; mutating routes (PUT,
icon upload) are superadmin and land in step 9.
"""
from __future__ import annotations

import time
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api._icon_validate import IconValidationError, validate_icon
from app.api.auth import get_current_user, require_role
from app.db.models import IconLibrary, TopologyLink, TopologyNode
from app.db.session import AsyncSessionLocal, get_db
from app.opensearch import network_devices as nd_qb
from app.schemas.common import APIResponse

router = APIRouter(prefix="/api/v1/topology", tags=["Topology"])

# Icons are written here and served via the StaticFiles mount added in main.py (H3).
# Served under /api/static/icons so it rides the existing /api/ proxy (Next rewrites +
# nginx only forward /api/*, not a bare /static/*) — no new proxy rule in two places.
ICON_DIR = Path(__file__).resolve().parents[2] / "static" / "icons"
ICON_URL_PREFIX = "/api/static/icons"
_ICON_KINDS = {
    # legacy / auto (data devices): fg = FortiGate, sw = switch, srv = server
    "fg", "sw", "srv", "custom",
    # built-in default-icon picker (Add/Edit Device dialog)
    "pc", "ap", "router", "fw", "cloud", "srv_tower", "srv_rack", "sw_l2", "sw_l3",
    # text annotation node (support=true, no source_ip, renders display_name as a text block)
    "text",
}
# Per-link colour: a #RGB / #RRGGBB hex, or empty → default stroke.
_COLOR_RE = r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$"
_LINK_TYPES = {"lan", "wan", "mpls", "wireless"}


# ── Schemas ──


class TopologyNodeRead(BaseModel):
    id: str
    source_ip: str | None
    support: bool
    display_name: str | None
    icon_kind: str
    icon_id: str | None
    icon_url: str | None = None  # resolved uploaded-icon URL (all viewers; /icons list is superadmin)
    x: int
    y: int
    rotation: int = 0  # degrees clockwise (text annotation nodes)
    online: bool | None = None  # data nodes only; support nodes None (never state-coloured)


class TopologyLinkRead(BaseModel):
    id: str
    a_node_id: str
    a_if_name: str | None
    b_node_id: str
    b_if_name: str | None
    link_type: str
    color: str | None = None    # per-link stroke colour (hex); None = default muted
    a_oper: str | None = None   # per-end interface oper (support end → None)
    b_oper: str | None = None
    rx_mbps: float = 0.0        # data-bearing end's live rate (carrier; back-compat)
    tx_mbps: float = 0.0
    # Per-end interface rates (None = support/no-data end). rx = IN/download, tx = OUT/upload,
    # from THAT device's perspective — lets the canvas label each interface separately.
    a_rx_mbps: float | None = None
    a_tx_mbps: float | None = None
    b_rx_mbps: float | None = None
    b_tx_mbps: float | None = None


class TopologyRead(BaseModel):
    nodes: list[TopologyNodeRead]
    links: list[TopologyLinkRead]


@router.get("", response_model=APIResponse[TopologyRead])
async def get_topology(current_user=Depends(get_current_user)):
    # Read map config + icon library in a short-lived session so the pooled DB
    # connection is released BEFORE the (possibly slow) OpenSearch live-state query.
    # Holding it across topology_live is what pins connections for the whole request
    # when OpenSearch stalls, exhausting the pool and taking /health + alerting down.
    # expire_on_commit=False keeps the loaded column values readable after close.
    async with AsyncSessionLocal() as db:
        nodes = (await db.execute(select(TopologyNode))).scalars().all()
        links = (await db.execute(select(TopologyLink))).scalars().all()
        # Resolve uploaded-icon URLs so every viewer sees custom icons (GET /icons is superadmin).
        # Only serve a URL when the file still exists on disk — a stale DB row (file lost to a
        # redeploy) must fall back to the built-in icon, not a broken <img>.
        icon_url_by_id = {
            r.id: f"{ICON_URL_PREFIX}/{Path(r.file_path).name}"
            for r in (await db.execute(select(IconLibrary))).scalars().all()
            if (ICON_DIR / Path(r.file_path).name).exists()
        }
    node_by_id = {n.id: n for n in nodes}

    # Which {source_ip: [if_name]} does the map reference? (data nodes with a link end).
    mappings: dict[str, list[str]] = {}
    for lk in links:
        for node_id, if_name in ((lk.a_node_id, lk.a_if_name), (lk.b_node_id, lk.b_if_name)):
            n = node_by_id.get(node_id)
            if n and n.source_ip and if_name:
                mappings.setdefault(n.source_ip, [])
                if if_name not in mappings[n.source_ip]:
                    mappings[n.source_ip].append(if_name)
    # Also include data nodes with no links so their freshness still colours them.
    for n in nodes:
        if n.source_ip and not n.support:
            mappings.setdefault(n.source_ip, [])

    now_ms = int(time.time() * 1000)
    gte, lte = now_ms - nd_qb.CURRENT_STATE_WINDOW_MS, now_ms
    live = await nd_qb.topology_live(mappings, gte, lte) if mappings else {}

    out_nodes = [
        TopologyNodeRead(
            id=n.id, source_ip=n.source_ip, support=n.support, display_name=n.display_name,
            icon_kind=n.icon_kind, icon_id=n.icon_id,
            icon_url=icon_url_by_id.get(n.icon_id) if n.icon_id else None,
            x=n.x, y=n.y, rotation=n.rotation or 0,
            online=None if n.support else bool(live.get(n.source_ip or "", {}).get("online", False)),
        )
        for n in nodes
    ]

    def end_live(node_id: str, if_name: str | None):
        n = node_by_id.get(node_id)
        if not n or not n.source_ip or not if_name:
            return None  # support end has no SNMP state of its own
        return live.get(n.source_ip, {}).get("interfaces", {}).get(if_name)

    out_links = []
    for lk in links:
        a = end_live(lk.a_node_id, lk.a_if_name)
        b = end_live(lk.b_node_id, lk.b_if_name)
        carrier = a or b or {}  # data-bearing end supplies the traffic reading
        out_links.append(TopologyLinkRead(
            id=lk.id, a_node_id=lk.a_node_id, a_if_name=lk.a_if_name,
            b_node_id=lk.b_node_id, b_if_name=lk.b_if_name, link_type=lk.link_type,
            color=lk.color,
            a_oper=(a or {}).get("oper"), b_oper=(b or {}).get("oper"),
            rx_mbps=carrier.get("rx_mbps", 0.0), tx_mbps=carrier.get("tx_mbps", 0.0),
            a_rx_mbps=(a or {}).get("rx_mbps") if a else None,
            a_tx_mbps=(a or {}).get("tx_mbps") if a else None,
            b_rx_mbps=(b or {}).get("rx_mbps") if b else None,
            b_tx_mbps=(b or {}).get("tx_mbps") if b else None,
        ))

    return APIResponse.ok(data=TopologyRead(nodes=out_nodes, links=out_links))


# ── Edit (superadmin) — full save, icon library ──


# A UUID in either form: 32 bare hex (what _new_uuid inserts) OR the canonical 36-char
# dashed form (what the Postgres UUID column returns on read). The round-trip save sends
# back whatever GET returned, so both must validate.
_UUID_RE = r"^(?:[0-9a-fA-F]{32}|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})$"


class NodeSave(BaseModel):
    id: str = Field(..., pattern=_UUID_RE)  # client-generated uuid, or echoed from GET
    source_ip: str | None = Field(default=None, max_length=45)
    support: bool = False
    display_name: str | None = Field(default=None, max_length=255)
    icon_kind: str = Field(default="sw", max_length=16)
    icon_id: str | None = Field(default=None, pattern=_UUID_RE)
    x: int = 0
    y: int = 0
    rotation: int = Field(default=0, ge=0, le=359)


class LinkSave(BaseModel):
    id: str = Field(..., pattern=_UUID_RE)
    a_node_id: str = Field(..., pattern=_UUID_RE)
    a_if_name: str | None = Field(default=None, max_length=255)
    b_node_id: str = Field(..., pattern=_UUID_RE)
    b_if_name: str | None = Field(default=None, max_length=255)
    link_type: str = Field(default="lan", max_length=16)
    color: str | None = Field(default=None, pattern=_COLOR_RE)


class TopologySave(BaseModel):
    nodes: list[NodeSave]
    links: list[LinkSave]


@router.put("", response_model=APIResponse[dict], dependencies=[Depends(require_role("superadmin"))])
async def save_topology(body: TopologySave, db: AsyncSession = Depends(get_db)):
    """Full all-or-nothing save of the map (plan §4.5). Client sends every node/link
    with its own uuid-hex id; the server replaces the whole map in one transaction."""
    node_ids = {n.id for n in body.nodes}
    for lk in body.links:
        if lk.a_node_id not in node_ids or lk.b_node_id not in node_ids:
            raise HTTPException(400, "link references an unknown node")
        if lk.link_type not in _LINK_TYPES:
            raise HTTPException(400, f"invalid link_type: {lk.link_type}")
    for n in body.nodes:
        if n.icon_kind not in _ICON_KINDS:
            raise HTTPException(400, f"invalid icon_kind: {n.icon_kind}")
        if n.support and n.source_ip:
            raise HTTPException(400, "support node must not carry a source_ip")

    valid_icons = set((await db.execute(select(IconLibrary.id))).scalars().all())

    # Replace wholesale: links first (FK), then nodes.
    await db.execute(delete(TopologyLink))
    await db.execute(delete(TopologyNode))
    for n in body.nodes:
        db.add(TopologyNode(
            id=n.id, source_ip=n.source_ip or None, support=n.support,
            display_name=n.display_name, icon_kind=n.icon_kind,
            icon_id=n.icon_id if n.icon_id in valid_icons else None, x=n.x, y=n.y,
            rotation=n.rotation,
        ))
    await db.flush()  # nodes must exist before links (FK)
    for lk in body.links:
        db.add(TopologyLink(
            id=lk.id, a_node_id=lk.a_node_id, a_if_name=lk.a_if_name,
            b_node_id=lk.b_node_id, b_if_name=lk.b_if_name, link_type=lk.link_type,
            color=lk.color,
        ))
    await db.commit()
    return APIResponse.ok(data={"nodes": len(body.nodes), "links": len(body.links)})


class IconRead(BaseModel):
    id: str
    name: str
    url: str


@router.get("/icons", response_model=APIResponse[list[IconRead]],
            dependencies=[Depends(require_role("superadmin"))])
async def list_icons(db: AsyncSession = Depends(get_db)):
    # Only list icons whose file still exists — rows left dangling by a redeploy (files lost,
    # DB kept) would otherwise appear in the picker as broken options. Mirrors GET /topology.
    rows = (await db.execute(select(IconLibrary))).scalars().all()
    return APIResponse.ok(data=[
        IconRead(id=r.id, name=r.name, url=f"{ICON_URL_PREFIX}/{Path(r.file_path).name}")
        for r in rows if (ICON_DIR / Path(r.file_path).name).exists()
    ])


@router.post("/icons", response_model=APIResponse[IconRead],
             dependencies=[Depends(require_role("superadmin"))])
async def upload_icon(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    current_user=Depends(get_current_user),
):
    """Upload a .png/.svg topology icon. Validated by magic bytes + size + SVG screen
    (see _icon_validate); served as-is via the StaticFiles mount."""
    content = await file.read()
    try:
        ext = validate_icon(content)
    except IconValidationError as e:
        raise HTTPException(400, str(e))

    icon_id = uuid.uuid4().hex
    ICON_DIR.mkdir(parents=True, exist_ok=True)
    fname = f"{icon_id}.{ext}"
    (ICON_DIR / fname).write_bytes(content)

    # The write above proves storage is mounted + writable, so any row whose file is now
    # missing is genuinely orphaned (e.g. lost to an earlier redeploy) — prune it. FK
    # ondelete=SET NULL clears icon_id on referencing nodes (→ built-in fallback). Safe to
    # do here precisely because storage is known-good; never on a read (a transient unmount
    # would look like every file is gone).
    for r in (await db.execute(select(IconLibrary))).scalars().all():
        if not (ICON_DIR / Path(r.file_path).name).exists():
            await db.delete(r)

    name = (file.filename or fname)[:120]
    if (await db.execute(select(IconLibrary.id).where(IconLibrary.name == name))).first():
        name = f"{name[:112]}-{icon_id[:6]}"  # name column is UNIQUE — disambiguate
    row = IconLibrary(id=icon_id, name=name, file_path=str(ICON_DIR / fname),
                      uploaded_by=getattr(current_user, "id", None))
    db.add(row)
    await db.commit()
    return APIResponse.ok(data=IconRead(id=icon_id, name=name, url=f"{ICON_URL_PREFIX}/{fname}"))
