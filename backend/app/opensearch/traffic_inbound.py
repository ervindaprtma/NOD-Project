"""
Async OpenSearch query builders for FortiGate AppID traffic inbound (VIP).
Index: fortigate-appid-flow-*
Key difference: uses flow.server.l4.port.id (service/port-based).
Routes: DC→dc cluster, DRC→drc cluster.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.port_service_map import PORT_SERVICE_MAP

# ── Index & site config ──────────────────────────────────────────

FLOW_INDEX = "fortigate-appid-flow-*"
SITE_SOURCE_IPS: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
}


def _get_client(site_name: str = "Site_FGT-DRC") -> AsyncOpenSearch:
    if site_name == "Site_FGT-DRC":
        return get_drc_client()
    return get_dc_client()


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}}


def _site_filter(site_name: str) -> dict:
    source_ip = SITE_SOURCE_IPS.get(site_name, "10.80.150.1")
    return {"term": {"flow.export.ip.addr": source_ip}}


def _port_to_service(port_value) -> str:
    try:
        return PORT_SERVICE_MAP.get(int(port_value), f"Port-{port_value}")
    except (ValueError, TypeError):
        return str(port_value)


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "inbound-vip",
    direction: str = "", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None,
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter:
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    if app_filter:
        filters.append({"wildcard": {"flow.application.name": f"*{app_filter}*"}})
    if client_ip:
        filters.append({"term": {"flow.client.ip.addr": client_ip}})
    if server_ip:
        filters.append({"term": {"flow.server.ip.addr": server_ip}})
    if protocol:
        filters.append({"term": {"l4.proto.name": protocol}})
    if dst_port is not None:
        filters.append({"term": {"flow.dst.l4.port.id": dst_port}})
    return filters


def _bytes_sum(name: str = "total_bytes") -> dict:
    return {name: {"sum": {"field": "flow.bytes"}}}


# ─────────────────────────────────────────────────────────────────
# TI-01: Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", path_filter: str = "inbound-vip",
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "top_services": {
                "terms": {"field": "flow.server.l4.port.id", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "top_as_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.src.as.org", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_as_download": {
                "filter": {"term": {"flow.out.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.dst.as.org", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_country_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.src.as.country", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_country_download": {
                "filter": {"term": {"flow.out.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.dst.as.country", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_clients": {
                "terms": {"field": "flow.client.ip.addr", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {"field": "flow.server.ip.addr", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "protocol_dist": {
                "terms": {"field": "l4.proto.name", "size": 10, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "egress_breakdown": {
                "terms": {"field": "flow.out.netif.name", "size": 10, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict]:
        return aggs.get(agg_name, {}).get("buckets", [])

    def _merge_filter_buckets(upload_agg: str, download_agg: str, inner_agg: str) -> list[dict]:
        merged: dict[str, int] = {}
        for agg_name in (upload_agg, download_agg):
            filter_result = aggs.get(agg_name, {})
            for b in filter_result.get(inner_agg, {}).get("buckets", []):
                key = b["key"]
                merged[key] = merged.get(key, 0) + int(b["total_bytes"]["value"])
        return [{"key": k, "total_bytes": v} for k, v in sorted(merged.items(), key=lambda x: -x[1])[:20]]

    as_client_buckets = _merge_filter_buckets("top_as_upload", "top_as_download", "as_orgs")
    country_client_buckets = _merge_filter_buckets("top_country_upload", "top_country_download", "countries")

    total_service_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("top_services")) or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    return {
        "top_services": [
            {"service_name": _port_to_service(b["key"]), "service_port": int(b["key"]) if str(b["key"]).isdigit() else b["key"],
             "total_bytes": int(b["total_bytes"]["value"]),
             "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
             "percentage": round(int(b["total_bytes"]["value"]) / total_service_bytes * 100, 2)}
            for b in _buckets("top_services")
        ],
        "top_src_as_org": [
            {"org_name": b["key"], "total_bytes": b["total_bytes"]}
            for b in as_client_buckets
        ],
        "top_src_as_country": [
            {"country": b["key"], "total_bytes": b["total_bytes"], "flag_code": ""}
            for b in country_client_buckets
        ],
        "top_clients": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "hostname": ""}
            for b in _buckets("top_servers")
        ],
        "protocol_dist": [
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("protocol_dist")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TI-02: Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", top_n: int = 20, path_filter: str = "inbound-vip",
    bucket_seconds: int = 60, app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str},
                "aggs": {
                    "top_services": {
                        "terms": {"field": "flow.server.l4.port.id", "size": top_n, "order": {"total_bytes": "desc"}},
                        "aggs": {"total_bytes": {"sum": {"field": "flow.bytes"}}},
                    }
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    result = resp["aggregations"]["per_minute"]

    service_set: set[str] = set()
    chart_data = []
    for bucket in result["buckets"]:
        row: dict[str, any] = {"timestamp": bucket["key_as_string"], "timestampMs": bucket["key"]}
        for svc_bucket in bucket["top_services"]["buckets"]:
            svc_name = _port_to_service(svc_bucket["key"])
            svc_bytes = int(svc_bucket["total_bytes"]["value"])
            service_set.add(svc_name)
            row[svc_name] = svc_bytes
        chart_data.append(row)

    return {"chart_data": chart_data, "service_names": sorted(service_set)}


# ─────────────────────────────────────────────────────────────────
# TI-03: Sankey — direction-aware pola
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", path_filter: str = "inbound-vip",
    direction: str = "", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None,
) -> dict:
    """Sankey for inbound VIP traffic.

    Upload (customer→VIP):   Src AS Org → Ingress → Service → Egress
    Download (VIP→customer): Ingress → Service → Egress → Dst AS Org
    """
    if client is None:
        client = _get_client(site_name)

    if direction == "download":
        # Download: Ingress → Service → Egress → Dst AS Org
        sources = [
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"dst_as_org": {"terms": {"field": "flow.dst.as.org"}}},
        ]
        level_names = ["ingress", "service", "egress", "dst_as_org"]
        level_labels = {0: "Ingress", 1: "Service", 2: "Egress", 3: "Dst AS Org"}
    else:
        # Upload: Src AS Org → Ingress → Service → Egress
        sources = [
            {"src_as_org": {"terms": {"field": "flow.src.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["src_as_org", "ingress", "service", "egress"]
        level_labels = {0: "Src AS Org", 1: "Ingress", 2: "Service", 3: "Egress"}

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, direction, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "sankey_flow": {
                "composite": {
                    "size": 1000,
                    "sources": sources,
                },
                "aggs": _bytes_sum(),
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["sankey_flow"]["buckets"]

    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        port_key = key.get("service", "")
        if port_key == "0" or port_key == 0:
            continue
        bytes_val = int(bucket["total_bytes"]["value"])
        if bytes_val == 0:
            continue
        row = {}
        for i, name in enumerate(level_names):
            raw = key.get(name, "Unknown")
            row[name] = _port_to_service(raw) if name == "service" else raw
        row["bytes"] = bytes_val
        rows.append(row)

    if not rows:
        return {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}

    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    level_totals = {name: _level_totals(rows, name) for name in level_names}

    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: -x[1])[:n]}

    top_sets = {name: _top_n(level_totals[name]) for name in level_names}

    filtered_rows = [r for r in rows if all(r[name] in top_sets[name] for name in level_names)]

    nodes_list: list[dict] = []
    node_index: dict[tuple[int, str], int] = {}

    def _get_node_id(level: int, label: str) -> int:
        key = (level, label)
        if key not in node_index:
            idx = len(nodes_list)
            node_index[key] = idx
            nodes_list.append({"id": idx, "label": label, "level": level})
        return node_index[key]

    link_map: dict[tuple[int, int], int] = defaultdict(int)
    for r in filtered_rows:
        ids = [_get_node_id(i, r[name]) for i, name in enumerate(level_names)]
        for i in range(len(ids) - 1):
            link_map[(ids[i], ids[i + 1])] += r["bytes"]

    links_list = [
        {"source": src, "target": tgt, "value": val}
        for (src, tgt), val in link_map.items() if val > 0
    ]
    links_list.sort(key=lambda x: -x["value"])
    links_list = links_list[:30]

    return {
        "nodes": nodes_list,
        "links": links_list,
        "as_country_nodes": [],
        "as_country_links": [],
    }


# ─────────────────────────────────────────────────────────────────
# TI-04: Flow Records Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DRC", after: Optional[dict] = None, page_size: int = 100,
    path_filter: str = "inbound-vip", app_filter: str = "", client_ip: str = "",
    server_ip: str = "", protocol: str = "", dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    composite_body: dict = {
        "size": page_size,
        "sources": [
            {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
            {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
            {"service_port": {"terms": {"field": "flow.server.l4.port.id", "missing_bucket": True}}},
        ],
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "flow_table": {
                "composite": composite_body,
                "aggs": {
                    "total_bytes": {"sum": {"field": "flow.bytes"}},
                    "total_packets": {"sum": {"field": "flow.packets"}},
                    "session_count": {"cardinality": {"field": "flow.connection_id"}},
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    result = resp["aggregations"]["flow_table"]

    records = []
    for bucket in result["buckets"]:
        key = bucket["key"]
        records.append({
            "client_ip": key.get("client_ip", ""),
            "server_ip": key.get("server_ip", ""),
            "service": _port_to_service(key.get("service_port", "")),
            "bytes": int(bucket["total_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key"), "total": len(records)}
