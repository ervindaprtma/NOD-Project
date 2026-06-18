"""
OpenSearch query builders for ipsec-* index (ipsec_normalized measurement).
Q-01: ALL queries include @timestamp range filter with gte/lte.
Q-03: _source includes only required fields.
"""
from __future__ import annotations

from typing import Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_ipsec_client


def _ipsec_filters(gte_ms: int, lte_ms: int) -> list[dict]:
    """Q-01: @timestamp range."""
    return [
        {
            "range": {
                "@timestamp": {
                    "gte": gte_ms,
                    "lte": lte_ms,
                    "format": "epoch_millis",
                }
            }
        }
    ]


async def active_ipsec_users_count(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> int:
    """
    Q-05: cardinality aggregation on tag.username.keyword.
    """
    if client is None:
        client = get_ipsec_client()

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {
            "active_users": {
                "cardinality": {"field": "tag.username.keyword"}
            }
        },
    }

    resp = await client.search(index="ipsec-*", body=body)
    return resp["aggregations"]["active_users"]["value"]


async def active_ipsec_users_detail(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """
    Q-07: terms agg on tag.username.keyword with top_hits sub-agg. No N+1 loop.
    """
    if client is None:
        client = get_ipsec_client()

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
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
                                    "ipsec_normalized.bytes_in",
                                    "ipsec_normalized.bytes_out",
                                    "ipsec_normalized.tunnel_lifetime",
                                    "tag.device",
                                    "tag.username",
                                    "tag.remote_gw_ip",
                                    "tag.assigned_ip",
                                ]
                            },  # Q-03
                        }
                    }
                },
            }
        },
    }

    resp = await client.search(index="ipsec-*", body=body)
    buckets = resp["aggregations"]["by_user"]["buckets"]

    results = []
    for bucket in buckets:
        hits = bucket["latest"]["hits"]["hits"]
        if not hits:
            continue
        src = hits[0]["_source"]
        ipsec = src.get("ipsec_normalized", {})
        tag = src.get("tag", {})

        results.append({
            "username": tag.get("username", bucket["key"]),
            "device": tag.get("device", ""),
            "remote_gw_ip": tag.get("remote_gw_ip", ""),
            "assigned_ip": tag.get("assigned_ip", ""),
            "bytes_in": int(ipsec.get("bytes_in", 0) or 0),
            "bytes_out": int(ipsec.get("bytes_out", 0) or 0),
            "tunnel_lifetime_sec": int(ipsec.get("tunnel_lifetime", 0) or 0),
        })

    return results
