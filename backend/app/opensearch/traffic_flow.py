"""
Async OpenSearch query builders for FortiGate AppID traffic flow analytics.
Index: fortigate-appid-flow-*
Site filter: flow.export.ip.addr = <source_ip>
Routes: DC→dc cluster, DRC+Office→drc cluster
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch._common import (
    FLOW_INDEX, _time_range, _multi_term, _multi_term_any, _multi_wildcard, _bytes_sum, BYTES_DESC,
)
from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.query import safe_search


# ── Site config ──────────────────────────────────────────────────
SITE_SOURCE_IPS: dict[str, str] = {
    "Site_FGT-DC": "10.80.150.1",
    "Site_FGT-DRC": "10.90.150.1",
    "Site_FGT_Office": "10.10.10.10",
}


def _get_client(site_name: str = "Site_FGT-DC") -> AsyncOpenSearch:
    if site_name in ("Site_FGT-DRC", "Site_FGT_Office"):
        return get_drc_client()
    return get_dc_client()


def _site_filter(site_name: str) -> dict:
    source_ip = SITE_SOURCE_IPS.get(site_name, "10.80.150.1")
    return {"term": {"flow.export.ip.addr": source_ip}}


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str, path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]
    if path_filter and path_filter != "all":
        filters.append({"term": {"flow.traffic.path": path_filter}})
    if direction == "upload":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})
    f = _multi_wildcard("flow.application.name", app_filter)
    if f: filters.append(f)
    f = _multi_wildcard("flow.application.category", category_filter)
    if f: filters.append(f)
    f = _multi_term("flow.client.ip.addr", client_ip)
    if f: filters.append(f)
    f = _multi_term("flow.server.ip.addr", server_ip)
    if f: filters.append(f)
    f = _multi_term("l4.proto.name", protocol)
    if f: filters.append(f)
    if dst_port is not None:
        # Role-based, not direction-based: flow.dst.l4.port.id is the destination of
        # each leg, so it only matches the request leg and drops the (much larger)
        # response leg. flow.server.l4.port.id is stable across both legs and matches
        # what the top_services agg buckets on.
        filters.append({"term": {"flow.server.l4.port.id": dst_port}})
    f = _multi_wildcard("flow.server.as.org", dst_as_org)
    if f: filters.append(f)
    f = _multi_term_any(['flow.in.netif.name', 'flow.in.netif.alias'], ingress_interface)
    if f: filters.append(f)
    f = _multi_term_any(['flow.out.netif.name', 'flow.out.netif.alias'], egress_interface)
    if f: filters.append(f)
    return filters


# ─────────────────────────────────────────────────────────────────
# TF-01: Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
    ingress_interface: str = "",
    egress_interface: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org, ingress_interface=ingress_interface, egress_interface=egress_interface)}},
        "aggs": {
            "grand_total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "grand_total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
            "top_apps": {
                "terms": {"field": "flow.application.name", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "app_categories": {
                "terms": {"field": "flow.application.category", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            # On the `internet` path the internal host is the client on BOTH legs —
            # client/server roles stay fixed across a correlated flow. So the external
            # org is flow.server.as.org for upload and download alike; aggregating
            # client.as.org on the download leg only ever yields "Private".
            "top_dst_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.server.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_dst_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"as_orgs": {"terms": {"field": "flow.server.as.org", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_upload": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internal"}},
                "aggs": {"countries": {"terms": {"field": "flow.dst.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_country_download": {
                "filter": {"term": {"flow.in.netif.sec.zone.name": "internet"}},
                "aggs": {"countries": {"terms": {"field": "flow.src.as.country", "size": 20, "order": BYTES_DESC}, "aggs": _bytes_sum()}},
            },
            "top_clients": {
                "terms": {"field": "flow.client.ip.addr", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {"field": "flow.server.ip.addr", "size": 20, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "protocol_dist": {
                "terms": {"field": "l4.proto.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "egress_breakdown": {
                "terms": {"field": "flow.out.netif.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "ingress_breakdown": {
                "terms": {"field": "flow.in.netif.name", "size": 10, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            # Unique session count for the timeframe (cardinality of connection_id)
            "session_count": {"cardinality": {"field": "flow.connection_id"}},
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict[str, Any]]:
        buckets: list[dict[str, Any]] = aggs.get(agg_name, {}).get("buckets", [])
        return buckets

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

    # Calculate totals in Python (pipeline aggregations can't sort, sum works fine)
    grand_upload = int(aggs.get("grand_total_upload", {}).get("value", 0))
    grand_download = int(aggs.get("grand_total_download", {}).get("value", 0))

    return {
        "total_bytes": grand_upload + grand_download,
        "total_upload": grand_upload,
        "total_download": grand_download,
        "total_sessions": int(aggs.get("session_count", {}).get("value", 0)),
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
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "upload_bytes": int(b["upload_bytes"]["value"]),
             "download_bytes": int(b["download_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
             "upload_bytes": int(b["upload_bytes"]["value"]),
             "download_bytes": int(b["download_bytes"]["value"]), "hostname": ""}
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
        "ingress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("ingress_breakdown")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# TF-02: Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", top_n: int = 50, path_filter: str = "internet",
    bucket_seconds: int = 60, app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str, "min_doc_count": 1},
                "aggs": {
                    "top_apps": {
                        "terms": {"field": "flow.application.name", "size": top_n, "order": BYTES_DESC},
                        "aggs": _bytes_sum(),
                    }
                },
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    result = resp["aggregations"]["per_minute"]

    app_totals: dict[str, int] = {}
    chart_data = []
    for bucket in result["buckets"]:
        row: dict[str, Any] = {"timestamp": bucket["key_as_string"], "timestampMs": bucket["key"]}
        for app_bucket in bucket["top_apps"]["buckets"]:
            app_name = app_bucket["key"]
            app_bytes = int(app_bucket["total_bytes"]["value"])
            app_totals[app_name] = app_totals.get(app_name, 0) + app_bytes
            row[app_name] = app_bytes
        chart_data.append(row)

    # Sort by total bytes descending (not alphabetically) so frontend gets top apps first
    sorted_apps = [name for name, _ in sorted(app_totals.items(), key=lambda x: -x[1])]
    return {"chart_data": chart_data, "app_names": sorted_apps}


# ─────────────────────────────────────────────────────────────────
# TF-03: Sankey — direction-aware pola
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT-DC", path_filter: str = "internet",
    direction: str = "", app_filter: str = "", category_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None, dst_as_org: str = "",
) -> dict:
    """Sankey for Internet traffic flow.

    Upload:   Zone → Apps → Egress → Dst AS Org
    Download: Src AS Org → Ingress → Apps → Zone
    """
    if client is None:
        client = _get_client(site_name)

    if direction == "download":
        # Download: Src AS Org → Ingress → Apps → Egress
        # The external party is the server on both legs of an `internet` flow, so the
        # source org of downloaded traffic is server.as.org (client.as.org is us).
        sources = [
            {"src_as": {"terms": {"field": "flow.server.as.org"}}},
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
        ]
        level_names = ["src_as", "ingress", "app", "egress"]
    else:
        # Upload: Ingress → Apps → Egress → Dst AS Org
        sources = [
            {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
            {"app": {"terms": {"field": "flow.application.name"}}},
            {"egress": {"terms": {"field": "flow.out.netif.name"}}},
            {"as_org": {"terms": {"field": "flow.server.as.org"}}},
        ]
        level_names = ["ingress", "app", "egress", "as_org"]

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, direction, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org)}},
        "aggs": {
            "sankey_flow": {
                "composite": {
                    "size": 1000,
                    "sources": sources,
                },
                "aggs": {
                    "upload_bytes": {"sum": {"field": "flow.client.bytes", "missing": 0}},
                    "download_bytes": {"sum": {"field": "flow.server.bytes", "missing": 0}}
                }
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp["aggregations"]["sankey_flow"]["buckets"]

    rows: list[dict] = []
    for bucket in buckets:
        key = bucket["key"]
        upload = int(bucket["upload_bytes"]["value"] or 0)
        download = int(bucket["download_bytes"]["value"] or 0)
        bytes_val = upload + download
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
            node_value = level_totals[level_names[level]].get(label, 0)
            nodes_list.append({"id": idx, "label": label, "level": level, "value": node_value})
            node_index[key] = idx
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
    dst_port: int | None = None, dst_as_org: str = "",
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
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, path_filter, app_filter=app_filter, category_filter=category_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port, dst_as_org=dst_as_org)}},
        "aggs": {
            "flow_table": {
                "composite": composite_body,
                "aggs": {
                    "upload_bytes": {"sum": {"field": "flow.client.bytes", "missing": 0}},
                    "download_bytes": {"sum": {"field": "flow.server.bytes", "missing": 0}},
                    "total_bytes": {
                        "bucket_script": {
                            "buckets_path": {"up": "upload_bytes", "down": "download_bytes"},
                            "script": "params.up + params.down"
                        }
                    },
                    "total_packets": {"sum": {"field": "flow.packets"}},
                    "session_count": {"cardinality": {"field": "flow.connection_id"}},
                },
            }
        },
    }

    resp = await safe_search(client, FLOW_INDEX, body)
    result = resp["aggregations"]["flow_table"]

    records = []
    for bucket in result["buckets"]:
        key = bucket["key"]
        records.append({
            "client_ip": key.get("client_ip", ""),
            "server_ip": key.get("server_ip", ""),
            "app_name": key.get("app_name", ""),
            "bytes": int(bucket["total_bytes"]["value"]),
            "upload_bytes": int(bucket["upload_bytes"]["value"]),
            "download_bytes": int(bucket["download_bytes"]["value"]),
            "packets": int(bucket["total_packets"]["value"]),
            "sessions": int(bucket["session_count"]["value"]),
        })

    return {"records": records, "after_key": result.get("after_key")}


async def top_dst_as_orgs(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
    site_name: str = "Site_FGT-DC",
) -> list[dict]:
    """Q-02: terms agg on flow.server.as.org — top destination AS organizations."""
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name), {"bool": {"must_not": [{"term": {"flow.server.as.org": "Private"}}]}}]}},
        "aggs": {
            "top_as_orgs": {
                "terms": {
                    "field": "flow.server.as.org",
                    "size": min(size, 500),
                    "order": BYTES_DESC,
                },
                "aggs": _bytes_sum(),
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp["aggregations"]["top_as_orgs"]["buckets"]
    return [
        {"as_org": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
         "upload_bytes": int(b["upload_bytes"]["value"]),
         "download_bytes": int(b["download_bytes"]["value"])}
        for b in buckets
    ]


async def top_applications(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
    site_name: str = "Site_FGT-DC",
) -> list[dict]:
    """Q-02: terms agg with explicit size, sum sub-agg on byte fields."""
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name)]}},
        "aggs": {
            "top_apps": {
                "terms": {
                    "field": "flow.application.name",
                    "size": min(size, 500),
                    "order": BYTES_DESC,
                },
                "aggs": _bytes_sum(),
            }
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    buckets = resp["aggregations"]["top_apps"]["buckets"]
    return [
        {"application": b["key"], "total_bytes": int(b["total_bytes"]["value"]),
         "upload_bytes": int(b["upload_bytes"]["value"]),
         "download_bytes": int(b["download_bytes"]["value"])}
        for b in buckets
    ]


async def total_throughput(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC",
) -> dict[str, int]:
    """Return {total_bytes, total_upload, total_download} for the time range.

    Annotation was `-> int` while the body returned a dict; both call sites already
    branch on isinstance(..., dict), so this corrects the signature to match reality
    rather than changing behaviour.
    """
    if client is None:
        client = _get_client(site_name)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [_time_range(gte_ms, lte_ms), _site_filter(site_name)]}},
        "aggs": {
            "total_upload": {"sum": {"field": "flow.client.bytes", "missing": 0}},
            "total_download": {"sum": {"field": "flow.server.bytes", "missing": 0}},
        },
    }
    resp = await safe_search(client, FLOW_INDEX, body)
    aggs = resp["aggregations"]
    return {
        "total_bytes": int(aggs["total_upload"]["value"] or 0) + int(aggs["total_download"]["value"] or 0),
        "total_upload": int(aggs["total_upload"]["value"] or 0),
        "total_download": int(aggs["total_download"]["value"] or 0),
    }


# ─────────────────────────────────────────────────────────────────
# TF-05: Raw Flow Records
# ─────────────────────────────────────────────────────────────────


async def raw_flows(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    page_size: int = 25,
    search_after: Optional[list] = None,
    sort_by: Optional[str] = None,
    sort_dir: str = "desc",
    filters: Optional[dict] = None,
    site_name: str = "Site_FGT-DC",
    path_filter: str = "internet",
    direction: str = "",
) -> dict:
    """
    Q-03: _source includes only required fields.
    Q-04: no scroll API — uses search_after.
    Q-08: valid search_after pagination.
    Routes to correct cluster per site (dc/drc).
    Returns {"records": [...], "search_after": [...], "total_hits": ...}
    """
    if client is None:
        client = _get_client(site_name)

    if page_size > 500:
        raise ValueError("page_size must be <= 500")

    source_fields = [
        "@timestamp",
        "flow.client.ip.addr",
        "flow.server.ip.addr",
        "flow.application.name",
        "flow.application.category",
        "l4.proto.name",
        "flow.server.l4.port.id",
        "flow.client.bytes",
        "flow.server.bytes",
        "flow.packets",
        "flow.in.netif.alias",
        "flow.out.netif.alias",
        "flow.correlation_id",
        "flow.correlation_direction",
        "flow.traffic.path",
        "flow.application.classification_method",
    ]

    must_filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name)]

    if path_filter and path_filter != "all":
        must_filters.append({"term": {"flow.traffic.path": path_filter}})

    if direction == "upload":
        must_filters.append({"term": {"flow.in.netif.sec.zone.name": "internal"}})
        must_filters.append({"term": {"flow.out.netif.sec.zone.name": "internet"}})
    elif direction == "download":
        must_filters.append({"term": {"flow.in.netif.sec.zone.name": "internet"}})
        must_filters.append({"term": {"flow.out.netif.sec.zone.name": "internal"}})

    if filters:
        if filters.get("client_ip"):
            vals = filters["client_ip"] if isinstance(filters["client_ip"], list) else [filters["client_ip"]]
            must_filters.append({"terms": {"flow.client.ip.addr": vals}})
        if filters.get("server_ip"):
            vals = filters["server_ip"] if isinstance(filters["server_ip"], list) else [filters["server_ip"]]
            must_filters.append({"terms": {"flow.server.ip.addr": vals}})
        if filters.get("application"):
            must_filters.append({"terms": {"flow.application.name": filters["application"]}})
        if filters.get("category"):
            must_filters.append({"terms": {"flow.application.category": filters["category"]}})
        if filters.get("protocol"):
            must_filters.append({"terms": {"l4.proto.name": filters["protocol"]}})
        if filters.get("dst_port"):
            vals = filters["dst_port"] if isinstance(filters["dst_port"], list) else [filters["dst_port"]]
            # Match the summary panels, which filter/bucket on the role-based service
            # port. flow.dst.l4.port.id would return only the request leg, so the same
            # filter value would disagree between this table and the charts above it.
            must_filters.append({"terms": {"flow.server.l4.port.id": vals}})
        # Accept either the interface name or its alias — the table displays the alias
        # while the breakdown panels show the name, and both feed the same filter bar.
        if filters.get("ingress_interface"):
            must_filters.append({"bool": {"should": [
                {"terms": {"flow.in.netif.name": filters["ingress_interface"]}},
                {"terms": {"flow.in.netif.alias": filters["ingress_interface"]}},
            ], "minimum_should_match": 1}})
        if filters.get("egress_interface"):
            must_filters.append({"bool": {"should": [
                {"terms": {"flow.out.netif.name": filters["egress_interface"]}},
                {"terms": {"flow.out.netif.alias": filters["egress_interface"]}},
            ], "minimum_should_match": 1}})
        if filters.get("correlation_id"):
            must_filters.append({"terms": {"flow.correlation_id": filters["correlation_id"]}})

    sort_field = sort_by if sort_by else "@timestamp"
    sort_order = "desc" if sort_dir == "desc" else "asc"
    sort_clause = [
        {sort_field: {"order": sort_order}},
        {"_id": {"order": sort_order}},
    ]

    body: dict = {
        "size": page_size,
        "query": {"bool": {"filter": must_filters}},
        "sort": sort_clause,
        "_source": {"includes": source_fields},
    }

    if search_after:
        body["search_after"] = search_after

    try:
        resp = await client.search(index=FLOW_INDEX, body=body, request_timeout=30)
    except Exception as e:
        return {"records": [], "search_after": None, "total_hits": 0, "error": str(e)}

    hits = resp["hits"]["hits"]
    records = []
    for hit in hits:
        src = hit["_source"]
        records.append({
            "timestamp": src.get("@timestamp", ""),
            "client_ip": src.get("flow.client.ip.addr", ""),
            "server_ip": src.get("flow.server.ip.addr", ""),
            "application": src.get("flow.application.name", ""),
            "category": src.get("flow.application.category", ""),
            "protocol": src.get("l4.proto.name", ""),
            # Service port (role-based), so the column agrees with the dst_port filter
            # and with the service/port dimension used across the summary panels. The
            # per-leg destination port is an ephemeral port on the response leg.
            "dst_port": src.get("flow.server.l4.port.id") or 0,
            "total_bytes": (src.get("flow.client.bytes", 0) or 0) + (src.get("flow.server.bytes", 0) or 0),
            "packets": src.get("flow.packets", 0),
            "ingress_interface": src.get("flow.in.netif.alias", ""),
            "egress_interface": src.get("flow.out.netif.alias", ""),
            "correlation_id": src.get("flow.correlation_id", ""),
            "correlation_direction": src.get("flow.correlation_direction", ""),
            "classification": src.get("flow.application.classification_method", ""),
            "path": src.get("flow.traffic.path", ""),
        })

    next_search_after = hits[-1]["sort"] if hits else None

    return {
        "records": records,
        "search_after": next_search_after,
        "total_hits": resp["hits"]["total"]["value"]
        if isinstance(resp["hits"]["total"], dict)
        else resp["hits"]["total"],
    }