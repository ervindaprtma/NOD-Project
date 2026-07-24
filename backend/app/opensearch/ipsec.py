"""
OpenSearch query builders for ipsec-* index (ipsec_normalized measurement).
Q-01: ALL queries include @timestamp range filter with gte/lte.
Q-03: _source includes only required fields.
"""
from __future__ import annotations

from opensearchpy import AsyncOpenSearch
from app.opensearch.client import get_ipsec_client
from app.opensearch.query import safe_search


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

    resp = await safe_search(client, "ipsec-*", body)
    value = resp.get("aggregations", {}).get("active_users", {}).get("value", 0) or 0
    return int(value)


async def active_ipsec_users_count_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    interval: str = "1h",
) -> dict[int, int]:
    """
    Q-05: date_histogram with cardinality sub-agg for user count over time.
    Returns dict mapping timestamp (ms) -> user count.
    """
    if client is None:
        client = get_ipsec_client()

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {
            "over_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval},
                "aggs": {"active_users": {"cardinality": {"field": "tag.username.keyword"}}},
            }
        },
    }

    resp = await safe_search(client, "ipsec-*", body)
    return {
        int(bucket["key"]): int(bucket["active_users"]["value"])
        for bucket in resp.get("aggregations", {}).get("over_time", {}).get("buckets", [])
    }


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

    resp = await safe_search(client, "ipsec-*", body)
    buckets = resp.get("aggregations", {}).get("by_user", {}).get("buckets", [])

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


async def ipsec_session_history(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """Q-05: terms agg + min/max @timestamp per user for IPsec session history."""
    if client is None:
        client = get_ipsec_client()

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {
            "by_user": {
                "terms": {"field": "tag.username.keyword", "size": 500},
                "aggs": {
                    "session_started": {"min": {"field": "@timestamp"}},
                    "last_seen": {"max": {"field": "@timestamp"}},
                    "bytes_in": {"max": {"field": "ipsec_normalized.bytes_in"}},
                    "bytes_out": {"max": {"field": "ipsec_normalized.bytes_out"}},
                },
            }
        },
    }

    resp = await safe_search(client, "ipsec-*", body)
    active_cutoff = lte_ms - 60_000
    return [
        {
            "username": bucket["key"],
            "session_started": int(bucket["session_started"]["value"]),
            "last_seen": int(bucket["last_seen"]["value"]),
            "bytes_in": int(bucket["bytes_in"]["value"] or 0),
            "bytes_out": int(bucket["bytes_out"]["value"] or 0),
            "status": "active" if int(bucket["last_seen"]["value"]) >= active_cutoff else "ended",
        }
        for bucket in resp.get("aggregations", {}).get("by_user", {}).get("buckets", [])
        if bucket["doc_count"] > 0
    ]
