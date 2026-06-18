"""
OpenSearch query builders for telegraf-index* — SSL VPN domain.
Q-06: ALL queries include exact term filter on measurement_name.
Q-01: ALL queries include @timestamp range filter with gte/lte.
"""
from __future__ import annotations

from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client


def _sslvpn_filters(gte_ms: int, lte_ms: int, site_name: str) -> list[dict]:
    """Q-01 + Q-06."""
    return [
        {
            "range": {
                "@timestamp": {
                    "gte": gte_ms,
                    "lte": lte_ms,
                    "format": "epoch_millis",
                }
            }
        },
        {"term": {"measurement_name.keyword": site_name}},  # Q-06: exact
    ]


async def active_sslvpn_users_count(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC_SSLVPN",
) -> int:
    """
    Q-05: cardinality aggregation on tag.username.
    Returns count of distinct active SSL VPN users.
    """
    if client is None:
        client = get_dc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": _sslvpn_filters(gte_ms, lte_ms, site_name)}
        },
        "aggs": {
            "active_users": {
                "cardinality": {"field": "tag.username.keyword"}
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    return resp["aggregations"]["active_users"]["value"]


async def active_sslvpn_users_detail(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC_SSLVPN",
) -> list[dict]:
    """
    Q-03: _source includes only required fields.
    Q-07: top_hits per user using terms agg on tag.username.
    Returns list of active SSL VPN user sessions.
    """
    if client is None:
        client = get_dc_client()

    body = {
        "size": 0,
        "query": {
            "bool": {"filter": _sslvpn_filters(gte_ms, lte_ms, site_name)}
        },
        "aggs": {
            "by_user": {
                "terms": {
                    "field": "tag.username.keyword",
                    "size": 500,  # Q-02
                },
                "aggs": {
                    "latest": {
                        "top_hits": {
                            "size": 1,
                            "sort": [{"@timestamp": {"order": "desc"}}],
                            "_source": {
                                "includes": [
                                    f"{site_name}.bytes_in",
                                    f"{site_name}.bytes_out",
                                    f"{site_name}.remote_ip",
                                    f"{site_name}.vpn_ip",
                                    "tag.device",
                                    "tag.username.keyword",
                                ]
                            },  # Q-03
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    buckets = resp["aggregations"]["by_user"]["buckets"]

    results = []
    for bucket in buckets:
        hits = bucket["latest"]["hits"]["hits"]
        if not hits:
            continue
        src = hits[0]["_source"]
        site_data = src.get(site_name, {})
        tag = src.get("tag", {})

        results.append({
            "username": tag.get("username", bucket["key"]),
            "device": tag.get("device", ""),
            "remote_ip": site_data.get("remote_ip", ""),
            "vpn_ip": site_data.get("vpn_ip", ""),
            "bytes_in": int(site_data.get("bytes_in", 0) or 0),
            "bytes_out": int(site_data.get("bytes_out", 0) or 0),
        })

    return results


async def all_sslvpn_users_count(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_names: Optional[list[str]] = None,
) -> int:
    """
    Q-07: single query across all configured SSLVPN sites.
    Uses terms agg on measurement_name + cardinality sub-agg.
    """
    if client is None:
        client = get_dc_client()

    if site_names is None:
        from app.core.config import get_settings
        site_names = get_settings().sslvpn_sites_list

    if not site_names:
        return 0

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {
                        "range": {
                            "@timestamp": {
                                "gte": gte_ms,
                                "lte": lte_ms,
                                "format": "epoch_millis",
                            }
                        }
                    },
                    {"terms": {"measurement_name.keyword": site_names}},  # Q-06: exact list
                ]
            }
        },
        "aggs": {
            "active_users": {
                "cardinality": {"field": "tag.username.keyword"}
            }
        },
    }

    resp = await client.search(index="telegraf-index*", body=body)
    return resp["aggregations"]["active_users"]["value"]
