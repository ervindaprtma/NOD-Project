"""
Traffic Internal schemas (intra-lan + inter-site).
"""
from __future__ import annotations

from typing import Optional
from pydantic import BaseModel


class TopServiceItem(BaseModel):
    service_name: str
    service_port: int | str = 0
    total_bytes: int
    speed_mbps: float
    percentage: float


class TopClientItem(BaseModel):
    ip: str
    total_bytes: int


class TopServerItem(BaseModel):
    ip: str
    total_bytes: int
    hostname: str = ""


class InterfaceBreakdownItem(BaseModel):
    interface: str
    total_bytes: int


class ProtocolDistItem(BaseModel):
    protocol: str
    total_bytes: int


class TrafficInternalSummaryResponse(BaseModel):
    top_services: list[TopServiceItem]
    top_clients: list[TopClientItem]
    top_servers: list[TopServerItem]
    ingress_breakdown: list[InterfaceBreakdownItem]
    egress_breakdown: list[InterfaceBreakdownItem]
    protocol_dist: list[ProtocolDistItem]


class TrafficInternalChartResponse(BaseModel):
    chart_data: list[dict]
    service_names: list[str]


class InboundFlowTableRecord(BaseModel):
    client_ip: str
    server_ip: str
    service: str = "Unknown"
    bytes: int = 0
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
