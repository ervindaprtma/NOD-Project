"""
Async OpenSearch query builders for Internal Traffic analytics.
Index: fortigate-appid-flow-*
Path filter: flow.traffic.path IN ("intra-lan", "inter-site")
Key dimension: flow.server.l4.port.id (service/port-based).
"""
from __future__ import annotations

from collections import defaultdict
from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_drc_client, get_dc_client

FLOW_INDEX = "fortigate-appid-flow-*"

SITE_FLOW_MAP: dict[str, tuple[str, str]] = {
    "Site_FGT-DC": ("10.80.150.1", "dc"),
    "Site_FGT-DRC": ("10.90.150.1", "drc"),
    "Site_FGT_Office": ("10.10.10.10", "drc"),
}

PORT_SERVICE_MAP: dict[int, str] = {
    20: "FTP-Data", 21: "FTP-Control", 22: "SSH", 23: "Telnet", 25: "SMTP",
    53: "DNS", 67: "DHCP-Server", 68: "DHCP-Client", 69: "TFTP",
    80: "HTTP-Browser", 110: "POP3", 123: "NTP", 143: "IMAP", 161: "SNMP",
    389: "LDAP", 443: "HTTPS-Browser", 445: "SMB", 465: "SMTPS", 514: "Syslog",
    587: "SMTP-Submit", 636: "LDAPS", 993: "IMAPS", 995: "POP3S",
    1433: "MSSQL", 1521: "Oracle", 3306: "MySQL", 3389: "RDP-Access",
    5432: "PostgreSQL", 5900: "VNC", 6379: "Redis", 8080: "HTTP-Alt",
    8443: "HTTPS-Alt", 9090: "Prometheus", 9200: "Elasticsearch", 27017: "MongoDB",
}


def _port_to_service(port_value) -> str:
    try:
        return PORT_SERVICE_MAP.get(int(port_value), f"Port-{port_value}")
    except (ValueError, TypeError):
        return str(port_value)


def _get_client(site_name: str = "Site_FGT_Office") -> AsyncOpenSearch:
    _, endpoint = SITE_FLOW_MAP.get(site_name, ("", "drc"))
    return get_dc_client() if endpoint == "dc" else get_drc_client()


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}}


def _site_filter(site_name: str) -> dict:
    entry = SITE_FLOW_MAP.get(site_name, ("",))
    return {"term": {"flow.export.ip.addr": entry[0] if entry else ""}}


def _internal_path_filter() -> dict:
    return {
        "bool": {
            "should": [
                {"term": {"flow.traffic.path": "intra-lan"}},
                {"term": {"flow.traffic.path": "inter-site"}},
            ],
            "minimum_should_match": 1,
        }
    }


def _base_filters(
    gte_ms: int, lte_ms: int, site_name: str,
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
) -> list[dict]:
    filters = [_time_range(gte_ms, lte_ms), _site_filter(site_name), _internal_path_filter()]
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
# Summary
# ─────────────────────────────────────────────────────────────────


async def flow_summary(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", app_filter: str = "",
    client_ip: str = "", server_ip: str = "", protocol: str = "",
    dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "top_services": {
                "terms": {"field": "flow.server.l4.port.id", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "top_clients": {
                "terms": {"field": "flow.client.ip.addr", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "top_servers": {
                "terms": {"field": "flow.server.ip.addr", "size": 20, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "ingress_breakdown": {
                "terms": {"field": "flow.in.netif.name", "size": 10, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "egress_breakdown": {
                "terms": {"field": "flow.out.netif.name", "size": 10, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
            "protocol_dist": {
                "terms": {"field": "l4.proto.name", "size": 10, "order": {"total_bytes": "desc"}},
                "aggs": _bytes_sum(),
            },
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    aggs = resp["aggregations"]

    def _buckets(agg_name: str) -> list[dict]:
        return aggs.get(agg_name, {}).get("buckets", [])

    def _total(agg_name: str) -> int:
        return sum(int(b.get("total_bytes", {}).get("value", 0)) for b in _buckets(agg_name))

    total_service_bytes = _total("top_services") or 1
    duration_s = max((lte_ms - gte_ms) / 1000.0, 1.0)

    return {
        "top_services": [
            {"service_name": _port_to_service(b["key"]), "service_port": int(b["key"]) if str(b["key"]).isdigit() else b["key"],
             "total_bytes": int(b["total_bytes"]["value"]),
             "speed_mbps": (int(b["total_bytes"]["value"]) * 8) / duration_s / 1_000_000,
             "percentage": round(int(b["total_bytes"]["value"]) / total_service_bytes * 100, 2)}
            for b in _buckets("top_services")
        ],
        "top_clients": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("top_clients")
        ],
        "top_servers": [
            {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"]), "hostname": ""}
            for b in _buckets("top_servers")
        ],
        "ingress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("ingress_breakdown")
        ],
        "egress_breakdown": [
            {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("egress_breakdown")
        ],
        "protocol_dist": [
            {"protocol": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
            for b in _buckets("protocol_dist")
        ],
    }


# ─────────────────────────────────────────────────────────────────
# Chart
# ─────────────────────────────────────────────────────────────────


async def flow_chart(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", top_n: int = 20, bucket_seconds: int = 60,
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
) -> dict:
    if client is None:
        client = _get_client(site_name)

    interval_str = f"{bucket_seconds}s"
    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "per_minute": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval_str},
                "aggs": {
                    "top_services": {
                        "terms": {"field": "flow.server.l4.port.id", "size": min(top_n, 50), "order": {"total_bytes": "desc"}},
                        "aggs": _bytes_sum(),
                    }
                },
            }
        },
    }

    resp = await client.search(index=FLOW_INDEX, body=body)
    buckets = resp["aggregations"]["per_minute"]["buckets"]

    all_service_bytes: dict[str, int] = {}
    chart_data: list[dict] = []

    for bucket in buckets:
        ts_ms = bucket["key"]
        row: dict = {"timestamp": ts_ms, "timestampMs": ts_ms}
        for svc_bucket in bucket["top_services"]["buckets"]:
            svc_name = _port_to_service(svc_bucket["key"])
            svc_bytes = int(svc_bucket["total_bytes"]["value"])
            row[svc_name] = svc_bytes
            all_service_bytes[svc_name] = all_service_bytes.get(svc_name, 0) + svc_bytes
        chart_data.append(row)

    service_names = sorted(all_service_bytes, key=all_service_bytes.get, reverse=True)

    return {"chart_data": chart_data, "service_names": service_names}


# ─────────────────────────────────────────────────────────────────
# Sankey — Ingress → Service → Egress
# ─────────────────────────────────────────────────────────────────


async def sankey_data(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office",
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
) -> dict:
    """Sankey: Ingress → Service → Egress (3 levels, no direction needed for internal)."""
    if client is None:
        client = _get_client(site_name)

    body = {
        "size": 0,
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
        "aggs": {
            "sankey_flow": {
                "composite": {
                    "size": 1000,
                    "sources": [
                        {"ingress": {"terms": {"field": "flow.in.netif.name"}}},
                        {"service": {"terms": {"field": "flow.server.l4.port.id"}}},
                        {"egress": {"terms": {"field": "flow.out.netif.name"}}},
                    ],
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
        rows.append({
            "ingress": key.get("ingress", "Unknown"),
            "service": _port_to_service(port_key),
            "egress": key.get("egress", "Unknown"),
            "bytes": bytes_val,
        })

    if not rows:
        return {"nodes": [], "links": [], "as_country_nodes": [], "as_country_links": []}

    def _level_totals(rows: list[dict], field: str) -> dict[str, int]:
        totals: dict[str, int] = {}
        for r in rows:
            totals[r[field]] = totals.get(r[field], 0) + r["bytes"]
        return totals

    def _top_n(totals: dict[str, int], n: int = 10) -> set[str]:
        return {k for k, _ in sorted(totals.items(), key=lambda x: -x[1])[:n]}

    top_ingress = _top_n(_level_totals(rows, "ingress"))
    top_services = _top_n(_level_totals(rows, "service"))
    top_egress = _top_n(_level_totals(rows, "egress"))

    filtered_rows = [r for r in rows if r["ingress"] in top_ingress and r["service"] in top_services and r["egress"] in top_egress]

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
        i_id = _get_node_id(0, r["ingress"])
        s_id = _get_node_id(1, r["service"])
        e_id = _get_node_id(2, r["egress"])
        link_map[(i_id, s_id)] += r["bytes"]
        link_map[(s_id, e_id)] += r["bytes"]

    links_list = [{"source": s, "target": t, "value": v} for (s, t), v in link_map.items() if v > 0]
    links_list.sort(key=lambda x: -x["value"])
    links_list = links_list[:30]

    return {
        "nodes": nodes_list,
        "links": links_list,
        "as_country_nodes": [],
        "as_country_links": [],
    }


# ─────────────────────────────────────────────────────────────────
# Table
# ─────────────────────────────────────────────────────────────────


async def flow_table(
    client: AsyncOpenSearch | None = None, gte_ms: int = 0, lte_ms: int = 0,
    site_name: str = "Site_FGT_Office", after: Optional[dict] = None, page_size: int = 100,
    app_filter: str = "", client_ip: str = "", server_ip: str = "",
    protocol: str = "", dst_port: int | None = None,
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
        "query": {"bool": {"filter": _base_filters(gte_ms, lte_ms, site_name, app_filter=app_filter, client_ip=client_ip, server_ip=server_ip, protocol=protocol, dst_port=dst_port)}},
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
