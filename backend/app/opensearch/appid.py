"""
OpenSearch query builders for fortigate-appid-flow-* index.
ALL queries comply with Q-01 through Q-08 mandates.
"""
from __future__ import annotations

from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_drc_client


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    """Q-01: produce a @timestamp range filter with both gte and lte."""
    return {
        "range": {
            "@timestamp": {
                "gte": gte_ms,
                "lte": lte_ms,
                "format": "epoch_millis",
            }
        }
    }


def _exclude_app0() -> dict:
    """Exclude flows with application.name = 'app-0' (unknown/unclassified traffic)."""
    return {"term": {"flow.application.name": "app-0"}}


def _exclude_private_as() -> dict:
    """Exclude flows where AS Organization is 'Private' (internal/unrouted traffic)."""
    return {"term": {"flow.dst.as.org": "Private"}}


# ─────────────────────────────────────────────────────────────────
# FR-02: Traffic Flow Analytics
# ─────────────────────────────────────────────────────────────────


async def top_applications(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
) -> list[dict]:
    """
    Q-02: terms agg with explicit size, sum sub-agg on byte fields.
    Returns top applications by total bytes.
    """
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "top_apps": {
                "terms": {
                    "field": "flow.application.name",
                    "size": min(size, 500),  # Q-02: explicit size, max 500
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["top_apps"]["buckets"]
    return [
        {"application": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def application_categories(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """Q-02: terms agg on flow.application.category."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "categories": {
                "terms": {
                    "field": "flow.application.category",
                    "size": 20,  # Q-02: explicit
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["categories"]["buckets"]
    return [
        {"category": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def throughput_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    interval: str = "5m",
) -> list[dict]:
    """Q-05: date_histogram with sum sub-agg for throughput timeline."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "throughput_over_time": {
                "date_histogram": {
                    "field": "@timestamp",
                    "fixed_interval": interval,
                    "min_doc_count": 0,
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["throughput_over_time"]["buckets"]
    return [
        {"timestamp": b["key"], "bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def sankey_data(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 100,
) -> dict:
    """
    Q-07: composite aggregation for three-way relationship
    (ingress zone -> application -> egress interface).
    Also includes egress→AS Country mapping via sub-aggregation
    to build a 4-level Sankey: Zone → Top 10 Apps → Egress → AS Country.
    Returns nodes and links for Sankey diagram.
    """
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [_time_range(gte_ms, lte_ms)],
                "must_not": [
                    _exclude_app0(),
                    _exclude_private_as(),
                ],
            }
        },
        "aggs": {
            "flows": {
                "composite": {
                    "size": min(size, 500),  # Q-02
                    "sources": [
                        {"ingress": {"terms": {"field": "flow.in.netif.alias"}}},
                        {"app": {"terms": {"field": "flow.application.name"}}},
                        {"egress": {"terms": {"field": "flow.out.netif.alias"}}},
                    ],
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    },
                    "by_as_country": {
                        "terms": {
                            "field": "flow.dst.as.country",
                            "size": 50,
                            "order": {"as_country_bytes": "desc"},
                        },
                        "aggs": {
                            "as_country_bytes": {
                                "sum": {
                                    "script": {
                                        "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                        "lang": "painless",
                                    }
                                }
                            }
                        }
                    },
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["flows"]["buckets"]

    nodes: dict[str, str] = {}
    links: list[dict] = []
    as_country_nodes: dict[str, str] = {}
    as_country_links: list[dict] = []

    for bucket in buckets:
        ingress = bucket["key"]["ingress"]
        app = bucket["key"]["app"]
        egress = bucket["key"]["egress"]
        total_bytes = int(bucket["total_bytes"]["value"])

        # Unique node IDs
        ingress_id = f"ingress:{ingress}"
        app_id = f"app:{app}"
        egress_id = f"egress:{egress}"

        nodes[ingress_id] = ingress
        nodes[app_id] = app
        nodes[egress_id] = egress

        links.append({"source": ingress_id, "target": app_id, "value": total_bytes})
        links.append({"source": app_id, "target": egress_id, "value": total_bytes})

        # AS Country sub-buckets (4th level: egress → as_country)
        for country_bucket in bucket["by_as_country"]["buckets"]:
            country = country_bucket["key"]
            country_bytes = int(country_bucket["as_country_bytes"]["value"])
            country_id = f"ascountry:{country}"
            as_country_nodes[country_id] = country
            as_country_links.append({"source": egress_id, "target": country_id, "value": country_bytes})

    return {
        "nodes": [{"id": k, "label": v} for k, v in nodes.items()],
        "links": links,
        "as_country_nodes": [{"id": k, "label": v} for k, v in as_country_nodes.items()],
        "as_country_links": as_country_links,
    }


async def top_client_ips(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
) -> list[dict]:
    """Q-02: terms agg with explicit size."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "top_clients": {
                "terms": {
                    "field": "flow.client.ip.addr",
                    "size": min(size, 500),  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["top_clients"]["buckets"]
    return [
        {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def top_server_ips(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 10,
) -> list[dict]:
    """Q-02: terms agg with explicit size."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "top_servers": {
                "terms": {
                    "field": "flow.server.ip.addr",
                    "size": min(size, 500),  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["top_servers"]["buckets"]
    return [
        {"ip": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def protocol_distribution(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """Q-02: terms agg on l4.proto.name with sum on bytes and packets."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "protocols": {
                "terms": {
                    "field": "l4.proto.name",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    },
                    "total_packets": {
                        "sum": {"field": "flow.packets"}
                    },
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["protocols"]["buckets"]
    return [
        {
            "protocol": b["key"],
            "total_bytes": int(b["total_bytes"]["value"]),
            "total_packets": int(b["total_packets"]["value"]),
        }
        for b in buckets
    ]


async def egress_interface_breakdown(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """Q-02: terms agg on flow.out.netif.alias."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "egress": {
                "terms": {
                    "field": "flow.out.netif.alias",
                    "size": 20,  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["egress"]["buckets"]
    return [
        {"interface": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


# ─────────────────────────────────────────────────────────────────
# AS Destination Analytics
# ─────────────────────────────────────────────────────────────────


async def top_dst_as_countries(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 20,
) -> list[dict]:
    """Q-02: terms agg on flow.dst.as.country — top destination countries by traffic volume."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [_time_range(gte_ms, lte_ms)],
                "must_not": [_exclude_app0(), _exclude_private_as()],
            }
        },
        "aggs": {
            "top_countries": {
                "terms": {
                    "field": "flow.dst.as.country",
                    "size": min(size, 50),  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["top_countries"]["buckets"]
    return [
        {"country": b["key"], "total_bytes": int(b["total_bytes"]["value"])}
        for b in buckets
    ]


async def top_dst_as_orgs(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    size: int = 20,
) -> list[dict]:
    """Q-02: terms agg on flow.dst.as.org + max on flow.dst.as.number for AS number."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [_time_range(gte_ms, lte_ms)],
                "must_not": [_exclude_app0(), _exclude_private_as()],
            }
        },
        "aggs": {
            "top_as_orgs": {
                "terms": {
                    "field": "flow.dst.as.org",
                    "size": min(size, 50),  # Q-02
                    "order": {"total_bytes": "desc"},
                },
                "aggs": {
                    "total_bytes": {
                        "sum": {
                            "script": {
                                "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                                "lang": "painless",
                            }
                        }
                    },
                    "as_number": {
                        "max": {"field": "flow.dst.as.number"},
                    },
                    "as_country": {
                        "terms": {
                            "field": "flow.dst.as.country",
                            "size": 1,
                        }
                    },
                },
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    buckets = resp["aggregations"]["top_as_orgs"]["buckets"]
    return [
        {
            "as_org": b["key"],
            "as_number": int(b["as_number"]["value"] or 0),
            "total_bytes": int(b["total_bytes"]["value"]),
            "country": b["as_country"]["buckets"][0]["key"] if b["as_country"]["buckets"] else "",
        }
        for b in buckets
    ]


async def total_throughput(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> int:
    """Q-05: sum aggregation for total throughput in time window."""
    if client is None:
        client = get_drc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": [_time_range(gte_ms, lte_ms)], "must_not": [_exclude_app0(), _exclude_private_as()]}
        },
        "aggs": {
            "total_bytes": {
                "sum": {
                    "script": {
                        "source": "doc['flow.client.bytes'].value + doc['flow.server.bytes'].value",
                        "lang": "painless",
                    }
                }
            }
        },
    }

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
    return int(resp["aggregations"]["total_bytes"]["value"])


# ─────────────────────────────────────────────────────────────────
# FR-05: Raw Data Table (search_after pagination)
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
    site_name: str = "Site_FGT_Office",
) -> dict:
    """
    Q-03: _source includes only required fields.
    Q-04: no scroll API — uses search_after.
    Q-08: valid search_after pagination.
    Routes to correct cluster via SITE_FLOW_MAP (dc/drc).
    Returns {"records": [...], "search_after": [...]}
    """
    if client is None:
        # Route to correct cluster per site
        from app.opensearch.traffic_flow import SITE_FLOW_MAP, _get_client as _tf_get_client
        try:
            client = _tf_get_client(site_name)
        except Exception:
            client = get_drc_client()

    # Q-08: page_size max 500
    if page_size > 500:
        raise ValueError("page_size must be <= 500")

    # Q-03: Source filtering — only columns required by the table
    source_fields = [
        "@timestamp",
        "flow.client.ip.addr",
        "flow.server.ip.addr",
        "flow.application.name",
        "flow.application.category",
        "l4.proto.name",
        "flow.dst.l4.port.id",
        "flow.client.bytes",
        "flow.server.bytes",
        "flow.packets",
        "flow.in.netif.alias",
        "flow.out.netif.alias",
        "flow.correlation_id",
        "flow.correlation_direction",
    ]

    must_filters = [_time_range(gte_ms, lte_ms)]
    must_not_filters = [_exclude_app0(), _exclude_private_as()]

    # Apply additional filters
    if filters:
        if "client_ip" in filters and filters["client_ip"]:
            must_filters.append({"term": {"flow.client.ip.addr": filters["client_ip"]}})
        if "server_ip" in filters and filters["server_ip"]:
            must_filters.append({"term": {"flow.server.ip.addr": filters["server_ip"]}})
        if "application" in filters and filters["application"]:
            must_filters.append({"terms": {"flow.application.name": filters["application"]}})
        if "category" in filters and filters["category"]:
            must_filters.append({"terms": {"flow.application.category": filters["category"]}})
        if "protocol" in filters and filters["protocol"]:
            must_filters.append({"terms": {"l4.proto.name": filters["protocol"]}})
        if "dst_port" in filters and filters["dst_port"]:
            must_filters.append({"term": {"flow.dst.l4.port.id": filters["dst_port"]}})
        if "ingress_zone" in filters and filters["ingress_zone"]:
            must_filters.append({"terms": {"flow.in.netif.alias": filters["ingress_zone"]}})
        if "egress_link" in filters and filters["egress_link"]:
            must_filters.append({"terms": {"flow.out.netif.alias": filters["egress_link"]}})

    # Sort — always include _id as tiebreaker
    sort_field = sort_by if sort_by else "@timestamp"
    sort_order = "desc" if sort_dir == "desc" else "asc"
    sort_clause = [
        {sort_field: {"order": sort_order}},
        {"_id": {"order": sort_order}},  # tiebreaker
    ]

    body: dict = {
        "size": page_size,
        "query": {
            "bool": {"filter": must_filters, "must_not": must_not_filters}
        },
        "sort": sort_clause,
        "_source": {"includes": source_fields},  # Q-03
    }

    if search_after:
        body["search_after"] = search_after  # Q-04: search_after, not scroll

    resp = await client.search(index="fortigate-appid-flow-*", body=body)
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
            "dst_port": src.get("flow.dst.l4.port.id", 0),
            "total_bytes": (src.get("flow.client.bytes", 0) or 0) + (src.get("flow.server.bytes", 0) or 0),
            "packets": src.get("flow.packets", 0),
            "ingress_zone": src.get("flow.in.netif.alias", ""),
            "egress_link": src.get("flow.out.netif.alias", ""),
            "correlation_id": src.get("flow.correlation_id", ""),
            "correlation_direction": src.get("flow.correlation_direction", ""),
        })

    next_search_after = hits[-1]["sort"] if hits else None

    return {
        "records": records,
        "search_after": next_search_after,
        "total_hits": resp["hits"]["total"]["value"]
        if isinstance(resp["hits"]["total"], dict)
        else resp["hits"]["total"],
    }
