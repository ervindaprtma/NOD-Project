"""
Traffic flow and raw data schemas.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


# ── Traffic Analytics (FR-02) ──────────────────────────────────

class TopApplicationItem(BaseModel):
    application: str
    total_bytes: int
    bytes_human: str


class CategoryItem(BaseModel):
    category: str
    total_bytes: int
    bytes_human: str


class SankeyNode(BaseModel):
    id: str
    label: str


class SankeyLink(BaseModel):
    source: str
    target: str
    value: int


class SankeyData(BaseModel):
    nodes: list[SankeyNode]
    links: list[SankeyLink]
    as_country_nodes: list[SankeyNode] = []
    as_country_links: list[SankeyLink] = []


class ThroughputPoint(BaseModel):
    timestamp: int  # epoch ms
    bytes: int


class TopIPItem(BaseModel):
    ip: str
    total_bytes: int
    bytes_human: str


class ProtocolItem(BaseModel):
    protocol: str
    total_bytes: int
    total_packets: int


class EgressInterfaceItem(BaseModel):
    interface: str
    total_bytes: int
    bytes_human: str


class ASCountryItem(BaseModel):
    country: str
    total_bytes: int
    bytes_human: str


class ASOrgItem(BaseModel):
    as_org: str
    as_number: int = 0
    total_bytes: int
    bytes_human: str
    country: str = ""


class TrafficSummaryResponse(BaseModel):
    top_applications: list[TopApplicationItem]
    categories: list[CategoryItem]
    sankey: SankeyData
    throughput_timeline: list[ThroughputPoint]
    top_clients: list[TopIPItem]
    top_servers: list[TopIPItem]
    protocols: list[ProtocolItem]
    egress_interfaces: list[EgressInterfaceItem]
    top_as_countries: list[ASCountryItem] = []
    top_as_orgs: list[ASOrgItem] = []


# ── Raw Data Table (FR-05) ─────────────────────────────────────

class RawFlowRecord(BaseModel):
    timestamp: str  # ISO 8601
    client_ip: str
    server_ip: str
    application: str
    category: str
    protocol: str
    dst_port: int
    total_bytes: int
    bytes_human: Optional[str] = None
    packets: int
    ingress_zone: str
    egress_link: str
    correlation_id: Optional[str] = None
    correlation_direction: Optional[str] = None


class RawFlowFilterParams(BaseModel):
    search_after: Optional[list] = None  # [timestamp_sort, _id] for pagination
    page_size: int = 25  # max 500
    sort_by: Optional[str] = None
    sort_dir: Optional[str] = "desc"
    # Filters
    client_ip: Optional[str] = None
    server_ip: Optional[str] = None
    application: Optional[list[str]] = None
    category: Optional[list[str]] = None
    protocol: Optional[list[str]] = None
    dst_port: Optional[int] = None
    ingress_zone: Optional[list[str]] = None
    egress_link: Optional[list[str]] = None
