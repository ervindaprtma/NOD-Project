"""
OpenSearch query builders for ipsec-* index (ipsec_normalized measurement).
Q-01: ALL queries include @timestamp range filter with gte/lte.
Q-03: _source includes only required fields.
"""
from __future__ import annotations

from opensearchpy import AsyncOpenSearch
from app.opensearch.client import get_dc_client, get_ipsec_client
from app.opensearch.query import safe_search
from app.opensearch.sslvpn import (
    _BUCKET_MS,
    _BUCKET_MS_OF,
    SESSION_GAP_MS,
    fetch_session_buckets,
    sessionize,
)


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

    Live source is the `ipsec_user` measurement in telegraf-index* on the DC cluster.
    The legacy dedicated `ipsec-*` index (DRC cluster) stopped receiving data — the
    collector was moved into the unified telegraf index — so the old query read 0
    users forever, which silently false-fired the "< 2 tunnels" CRITICAL alert.
    """
    if client is None:
        client = get_dc_client()

    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms) + [
            {"term": {"measurement_name.keyword": "ipsec_user"}},
        ]}},
        "aggs": {
            "active_users": {
                "cardinality": {"field": "tag.username.keyword"}
            }
        },
    }

    resp = await safe_search(client, "telegraf-index*", body)
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
    now_ms: int | None = None,
    gap_ms: int = SESSION_GAP_MS,
    bucket: str = "60s",
) -> list[dict]:
    """Per-session IPsec history — same reconstruction as SSL (gap-split, day-bounded)."""
    if client is None:
        client = get_ipsec_client()
    times, byb, durs = await fetch_session_buckets(
        client, "ipsec-*", [], gte_ms, lte_ms,
        "ipsec_normalized.bytes_in", "ipsec_normalized.bytes_out",
        bucket=bucket, dur_field="ipsec_normalized.session_duration_seconds",
    )
    return sessionize(times, now_ms if now_ms is not None else lte_ms,
                      gap_ms, _BUCKET_MS_OF.get(bucket, _BUCKET_MS), byb, durs)
