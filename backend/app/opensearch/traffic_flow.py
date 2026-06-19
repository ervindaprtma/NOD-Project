"""
Async OpenSearch query builders for FortiGate AppID traffic flow analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip>
Routes: DC→dc cluster, DRC+Office→drc cluster
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client

# ── Index & site config ──────────────────────────────────────────

FLOW_INDEX = "fortigate-appid-flow-*"
SITE_SOURCE_IPS: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
    "Site_FGT_Office": "10.10.10.10",
}


def _get_client(site_name: str = "Site_FGT-DC") -> AsyncOpenSearch:
    if site_name in ("Site_FGT-DRC", "Site_FGT_Office"):
        return get_drc_client()
    return get_dc_client()


# ── Reusable filter/mapping helpers ──────────────────────────────


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}}


def _site_filter(site_name: str) -> dict:
    source_ip = SITE_SOURCE_IPS.get(site_name, "10.80.150.1")
    return {"term": {"flow.export.ip.addr": source_ip}}


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter:
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    if app_filter:
        filters.append({"wildcard": {"flow.application.name": f"*{app_filter}*"}})
    if category_filter:
        filters.append({"wildcard": {"flow.application.category": f"*{category_filter}*"}})
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
# TF-01: Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "grand_total_bytes": {"sum": {"field": "flow.bytes"}},
            "top_apps": {
                "terms": {"field": "flow.application.name", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "app_categories": {
                "terms": {"field": "flow.application.category", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "top_dst_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.dst.as.org", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_dst_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.src.as.org", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_country_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"countries": {"terms": {"field": "flow.dst.as.country", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
            },
            "top_country_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.src.as.country", "size": 20, "order": {"total_bytes": "desc"}}, "aggs": _bytes_sum()}},
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

    as_org_buckets = _merge_filter_buckets("top_dst_upload", "top_dst_download", "as_orgs")
    country_buckets = _merge_filter_buckets("top_country_upload", "top_country_download", "countries")

    total_app_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("top_apps")) or 1
    total_proto_bytes = sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets("protocol_dist")) or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    # Actual total bytes from top-level aggregation (not limited to top-20)
    actual_total_bytes = int(aggs.get("grand_total_bytes", {}).get("value", 0))

    return {
        "total_bytes": actual_total_bytes,
        "top_apps": [
            {"app_name": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
             "percentage": round(int(b["total_bytes"]["value"]) / total_app_bytes * 100, 2)}
            for b in _buckets("top_apps")
        ],
        "app_categories": [
            {"category_name": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "count": b["doc_count"]}
            for b in _buckets("app_categories")
        ],
        "top_dst_as_org": [
            {"org_name": b["key"], "total_bytes": b["total_bytes"]}
            for b in as_org_buckets
        ],
        "top_dst_as_country": [
            {"country": b["key"], "total_bytes": b["total_bytes"], "flag_code": ""}
            for b in country_buckets
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
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "percentage": round(int(b["total_bytes"]["value"]) / total_proto_bytes * 100, 2)}
            for b in _buckets("protocol_dist")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TF-02: Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", top_n: int = 20, path_filter: str = "internet",
    bucket_seconds: int = 60, app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str},
                "aggs": {
                    "top_apps": {
                        "terms": {"field": "flow.application.name", "size": top_n, "order": {"total_bytes": "desc"}},
                        "aggs": {"total_bytes": {"sum": {"field": "flow.bytes"}}},
                    }
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    result = resp["aggregations"]["per_minute"]

    app_set: set[str] = set()
    chart_data = []
    for bucket in result["buckets"]:
        row: dict[str, any] = {"timestamp": bucket["key_as_string"], "timestampMs": bucket["key"]}
        for app_bucket in bucket["top_apps"]["buckets"]:
            app_name = app_bucket["key"]
            app_bytes = int(app_bucket["total_bytes"]["value"])
            app_set.add(app_name)
            row[app_name] = app_bytes
        chart_data.append(row)

    return {"chart_data": chart_data, "app_names": sorted(app_set)}


# ─────────────────────────────────────────────────────────────────
# TF-03: Sankey — direction-aware pola
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> dict:
    """Sankey for Internet traffic flow.

    Upload:   Zone → Apps → Egress → Dst AS Org
    Download: Src AS Org → Ingress → Apps → Zone
    """
    if client is None:
        client = _get_client(site_name)

    if direction == "download":
        # Download: Src AS Org → Ingress → Apps → Egress
        sources = [
            {"src_as": {"terms": {"field": "flow.src.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["src_as", "ingress", "app", "egress"]
        level_labels = {0: "Src AS Org", 1: "Ingress", 2: "Apps", 3: "Egress"}
    else:
        # Upload: Ingress → Apps → Egress → Dst AS Org
        sources = [
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"as_org": {"terms": {"field": "flow.dst.as.org"}}},
        ]
        level_names = ["ingress", "app", "egress", "as_org"]
        level_labels = {0: "Ingress", 1: "Apps", 2: "Egress", 3: "Dst AS Org"}

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, direction, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
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
        app_name = key.get("app", "")
        if app_name == "app-0":
            continue
        bytes_val = int(bucket["total_bytes"]["value"])
        if bytes_val == 0:
            continue
        row = {}
        for i, name in enumerate(level_names):
            row[name] = key.get(name, "Unknown")
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
# TF-04: Flow Records Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", after: Optional[dict] = None, page_size: int = 100,
    path_filter: str = "internet", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    composite_body: dict = {
        "size": page_size,
        "sources": [
            {"client_ip": {"terms": {"field": "flow.client.ip.addr"}}},
            {"server_ip": {"terms": {"field": "flow.server.ip.addr"}}},
            {"app_name": {"terms": {"field": "flow.application.name", "missing_bucket": True}}},
        ],
    }
    if after:
        composite_body["after"] = after

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
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
            "app_name": key.get("app_name", ""),
            "bytes": int(bucket["total_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key")}
