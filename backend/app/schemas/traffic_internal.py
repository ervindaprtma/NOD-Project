"""
Traffic Internal schemas (intra-lan + inter-site).
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class TopServiceItem(BaseModel):
    service_name: str
    service_port: int | None = None  # None when the label is an AppID name, not a port
    total_bytes: int
    speed_mbps: float
    percentage: float


class TopClientItem(BaseModel):
    ip: str
    total_bytes: int
    upload_bytes: int = 0
    download_bytes: int = 0


class TopServerItem(BaseModel):
    ip: str
    total_bytes: int
    upload_bytes: int = 0
    download_bytes: int = 0
    hostname: str = ""


class InterfaceBreakdownItem(BaseModel):
    interface: str
    total_bytes: int


class ProtocolDistItem(BaseModel):
    protocol: str
    total_bytes: int


class TrafficInternalSummaryResponse(BaseModel):
    total_bytes: int = 0
    total_upload: int = 0
    total_download: int = 0
    total_sessions: int = 0
    top_services: list[TopServiceItem]
    top_clients: list[TopClientItem]
    top_servers: list[TopServerItem]
    ingress_breakdown: list[InterfaceBreakdownItem]
    egress_breakdown: list[InterfaceBreakdownItem]
    protocol_dist: list[ProtocolDistItem]


class TrafficInternalChartResponse(BaseModel):
    chart_data: list[dict]
    service_names: list[str]
    bucket_seconds: int = 60  # actual bucket width used; frontend divides bytes by this for Mbps


class InboundFlowTableRecord(BaseModel):
    client_ip: str
    server_ip: str
    service: str = "Unknown"
    bytes: int = 0
    upload_bytes: int = 0
    download_bytes: int = 0
    packets: int = 0
    sessions: int = 0


class TrafficInternalTableResponse(BaseModel):
    records: list[InboundFlowTableRecord]
    after_key: Optional[dict] = None
    total: int = 0


class SankeyNode(BaseModel):
    id: int
    label: str
    level: int


class SankeyLink(BaseModel):
    source: int
    target: int
    value: int


class SankeyResponse(BaseModel):
    nodes: list[SankeyNode]
    links: list[SankeyLink]
    as_country_nodes: list[dict] = []
    as_country_links: list[dict] = []