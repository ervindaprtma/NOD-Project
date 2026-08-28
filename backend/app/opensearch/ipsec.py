"""
OpenSearch query builders for ipsec-* index (ipsec_normalized measurement).
Q-01: ALL queries include @timestamp range filter with gte/lte.
Q-03: _source includes only required fields.
"""
from __future__ import annotations

from typing import Any

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


async def _ipsec_usernames(client: AsyncOpenSearch, gte_ms: int, lte_ms: int) -> set[str]:
    """Distinct IPsec usernames active in the window on one cluster's ipsec-* index."""
    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {"users": {"terms": {"field": "tag.username.keyword", "size": 1000}}},
    }
    resp = await safe_search(client, "ipsec-*", body)
    return {b["key"] for b in resp.get("aggregations", {}).get("users", {}).get("buckets", [])}


async def _ipsec_usage_one(client: AsyncOpenSearch, gte_ms: int, lte_ms: int) -> dict[str, int]:
    """Per-username consumed bytes (max cumulative bytes_in+bytes_out) on one cluster."""
    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {
            "by_user": {
                "terms": {"field": "tag.username.keyword", "size": 1000},
                "aggs": {
                    "bin": {"max": {"field": "ipsec_normalized.bytes_in"}},
                    "bout": {"max": {"field": "ipsec_normalized.bytes_out"}},
                },
            }
        },
    }
    resp = await safe_search(client, "ipsec-*", body)
    out: dict[str, int] = {}
    for b in resp.get("aggregations", {}).get("by_user", {}).get("buckets", []):
        out[b["key"]] = int(b.get("bin", {}).get("value") or 0) + int(b.get("bout", {}).get("value") or 0)
    return out


async def ipsec_usage_summary(gte_ms: int = 0, lte_ms: int = 0) -> dict[str, Any]:
    """Volume consumed by active IPsec users in the window (for capacity alerting).

    Unions per-username bytes across BOTH clusters (DC + DRC) so it matches
    active_ipsec_users_count's coverage; a user on both endpoints is counted once (max,
    not summed). Returns {count, total_bytes, top_user_bytes, top_users}: top_users is the
    per-user breakdown [{user, bytes}] sorted heaviest-first, so a notification can name
    the offending users, not just count them.
    """
    merged: dict[str, int] = {}
    for get_client in (get_ipsec_client, get_dc_client):
        try:
            for user, val in (await _ipsec_usage_one(get_client(), gte_ms, lte_ms)).items():
                merged[user] = max(merged.get(user, 0), val)
        except Exception:  # a cluster without an ipsec-* index just contributes nothing
            pass
    per_user = sorted(merged.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "count": len(per_user),
        "total_bytes": sum(v for _, v in per_user),
        "top_user_bytes": per_user[0][1] if per_user else 0,
        "top_users": [{"user": u, "bytes": v} for u, v in per_user[:20]],
    }


async def active_ipsec_users_count(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> int:
    """Count distinct active IPsec users — same idea as SSL VPN: a username seen in the
    window = a currently-active user.

    Source is the `ipsec-*` index (ipsec_normalized), the SAME session data the VPN
    Sessions page reads and tags usernames from. It's session-based (a user emits docs
    while connected), so an empty recent window genuinely means nobody is connected — NOT
    a broken read. Unions usernames across BOTH clusters (DC + DRC) so a tunnel terminating
    on either endpoint is counted once. (An earlier version wrongly read telegraf-index*'s
    `ipsec_user` polling measurement, which over-counts.)
    """
    if client is not None:
        return len(await _ipsec_usernames(client, gte_ms, lte_ms))

    names: set[str] = set()
    for get_client in (get_ipsec_client, get_dc_client):
        try:
            names |= await _ipsec_usernames(get_client(), gte_ms, lte_ms)
        except Exception:  # a cluster without an ipsec-* index just contributes nothing
            pass
    return len(names)


async def _ipsec_user_timeline_one(
    client: AsyncOpenSearch, gte_ms: int, lte_ms: int, interval: str,
) -> dict[int, set[str]]:
    """Per-bucket SET of usernames on ONE cluster's ipsec-* — terms, not cardinality, so the
    caller can UNION sets across clusters. Summing cardinalities would double-count a user
    tunnelling on both endpoints."""
    body = {
        "size": 0,
        "query": {"bool": {"filter": _ipsec_filters(gte_ms, lte_ms)}},
        "aggs": {
            "over_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval},
                "aggs": {"users": {"terms": {"field": "tag.username.keyword", "size": 1000}}},
            }
        },
    }
    resp = await safe_search(client, "ipsec-*", body)
    return {
        int(b["key"]): {u["key"] for u in b.get("users", {}).get("buckets", [])}
        for b in resp.get("aggregations", {}).get("over_time", {}).get("buckets", [])
    }


async def active_ipsec_users_count_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    interval: str = "1h",
) -> dict[int, int]:
    """Q-05: distinct IPsec users per time bucket. Unions usernames across BOTH clusters
    (DC + DRC) so the timeline matches active_ipsec_users_count — a tunnel on either endpoint
    is counted, and a user on both is counted once per bucket.
    Returns dict mapping timestamp (ms) -> user count."""
    if client is not None:
        return {ts: len(s) for ts, s in
                (await _ipsec_user_timeline_one(client, gte_ms, lte_ms, interval)).items()}

    merged: dict[int, set[str]] = {}
    for get_client in (get_ipsec_client, get_dc_client):
        try:
            for ts, s in (await _ipsec_user_timeline_one(get_client(), gte_ms, lte_ms, interval)).items():
                merged.setdefault(ts, set()).update(s)
        except Exception:  # a cluster without an ipsec-* index just contributes nothing
            pass
    return {ts: len(s) for ts, s in merged.items()}


async def _ipsec_detail_one(
    client: AsyncOpenSearch, gte_ms: int, lte_ms: int,
) -> list[dict]:
    """Active IPsec users on ONE cluster's ipsec-* (terms on tag.username + latest top_hit)."""
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
                                    "ipsec_normalized.session_duration_seconds",
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
        # Real login epoch-ms = latest sample time − session age (same as the History page).
        latest_ms = int((hits[0].get("sort") or [0])[0] or 0)
        dur_s = int(ipsec.get("session_duration_seconds", 0) or 0)
        session_started = (latest_ms - dur_s * 1000) if (latest_ms and dur_s > 0) else None

        results.append({
            "username": tag.get("username", bucket["key"]),
            "device": tag.get("device", ""),
            "remote_gw_ip": tag.get("remote_gw_ip", ""),
            "assigned_ip": tag.get("assigned_ip", ""),
            "bytes_in": int(ipsec.get("bytes_in", 0) or 0),
            "bytes_out": int(ipsec.get("bytes_out", 0) or 0),
            "tunnel_lifetime_sec": int(ipsec.get("tunnel_lifetime", 0) or 0),
            "session_started": session_started,
            "last_seen": latest_ms or None,  # newest sample ts — the real last-activity edge
        })

    return results


async def active_ipsec_users_detail(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
) -> list[dict]:
    """Per-user active IPsec sessions. Unions BOTH clusters (DC + DRC) so the VPN Sessions
    page shows every active user — matching active_ipsec_users_count. A username seen on
    both endpoints collapses to its most-recently-active record (dedupe by username, latest
    last_seen wins), so the row count equals the distinct-user count."""
    if client is not None:
        return await _ipsec_detail_one(client, gte_ms, lte_ms)

    by_user: dict[str, dict] = {}
    for get_client in (get_ipsec_client, get_dc_client):
        try:
            for u in await _ipsec_detail_one(get_client(), gte_ms, lte_ms):
                prev = by_user.get(u["username"])
                if prev is None or (u.get("last_seen") or 0) > (prev.get("last_seen") or 0):
                    by_user[u["username"]] = u
        except Exception:  # a cluster without an ipsec-* index just contributes nothing
            pass
    return list(by_user.values())


async def ipsec_session_history(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    now_ms: int | None = None,
    gap_ms: int = SESSION_GAP_MS,
    bucket: str = "60s",
) -> list[dict]:
    """Per-session IPsec history — same reconstruction as SSL (gap-split, day-bounded).
    Unions BOTH clusters (DC + DRC): fetch_session_buckets keys on (username, device) and
    each cluster's devices are distinct, so merging keeps every endpoint's sessions and
    never collides. Matches active_ipsec_users_count/_detail coverage."""
    async def _fetch(c: AsyncOpenSearch):
        return await fetch_session_buckets(
            c, "ipsec-*", [], gte_ms, lte_ms,
            "ipsec_normalized.bytes_in", "ipsec_normalized.bytes_out",
            bucket=bucket, dur_field="ipsec_normalized.session_duration_seconds",
        )

    if client is not None:
        times, byb, durs = await _fetch(client)
        return sessionize(times, now_ms if now_ms is not None else lte_ms,
                          gap_ms, _BUCKET_MS_OF.get(bucket, _BUCKET_MS), byb, durs)

    m_times: dict[tuple[str, str], list[int]] = {}
    m_byb: dict[tuple[str, str], dict[int, tuple[int, int]]] = {}
    m_durs: dict[tuple[str, str], dict[int, int]] = {}
    for get_client in (get_ipsec_client, get_dc_client):
        try:
            t2, b2, d2 = await _fetch(get_client())
            for k, v in t2.items():
                m_times.setdefault(k, []).extend(v)
            for k, bv in b2.items():
                m_byb.setdefault(k, {}).update(bv)
            for k, dv in d2.items():
                m_durs.setdefault(k, {}).update(dv)
        except Exception:  # a cluster without an ipsec-* index just contributes nothing
            pass
    return sessionize(m_times, now_ms if now_ms is not None else lte_ms,
                      gap_ms, _BUCKET_MS_OF.get(bucket, _BUCKET_MS), m_byb, m_durs)
