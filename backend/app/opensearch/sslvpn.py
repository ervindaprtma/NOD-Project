"""
OpenSearch query builders for telegraf-index* — SSL VPN domain.
Q-06: ALL queries include exact term filter on measurement_name.
Q-01: ALL queries include @timestamp range filter with gte/lte.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional
from opensearchpy import AsyncOpenSearch
from app.opensearch.client import get_dc_client
from app.opensearch.query import safe_search

# ── Session reconstruction (shared by SSL + IPsec) ───────────────────────────
# A "session" is a run of telegraf samples with no gap over GAP; the fetch window
# starts at 00:00 WIB so a run never crosses midnight (rule 1 — a pre-midnight
# session is yesterday's, closed). A gap over GAP splits a reconnect (rule 2); a
# run whose last sample is older than GAP is "ended" (rule 3).
SESSION_GAP_MS: int = 5 * 60 * 1000
SESSION_MAX_MS: int = 9 * 60 * 60 * 1000   # SSL VPN sessions cap at 9h — a longer run is split
_BUCKET_MS: int = 60_000
_WIB_OFFSET_MS: int = 7 * 60 * 60 * 1000
_BUCKET_MS_OF: dict[str, int] = {"60s": 60_000, "2m": 120_000, "5m": 300_000}


def _wib_day(ms: int) -> int:
    """WIB calendar-day index — used to cut a session at 00:00 WIB (rule 1)."""
    return (ms + _WIB_OFFSET_MS) // 86_400_000


def sessionize(
    user_buckets: dict[tuple[str, str], list[int]],
    now_ms: int,
    gap_ms: int = SESSION_GAP_MS,
    bucket_ms: int = _BUCKET_MS,
    bytes_by_bucket: dict[tuple[str, str], dict[int, tuple[int, int]]] | None = None,
    dur_by_bucket: dict[tuple[str, str], dict[int, int]] | None = None,
) -> list[dict]:
    """Split each user's minute-bucket timestamps into sessions.

    A new session begins on a gap > gap_ms (reconnect, rule 2), when two
    consecutive buckets fall on different WIB days (rule 1 — a session never spans
    00:00 WIB), OR when the device's own `session_duration` DROPS between buckets
    — a tunnel rebuild, which catches a fast forced-logout/re-login the gap rule
    cannot see (§3.5b). When the duration field is present it also fixes the exact
    login second in `_session`.

    Pure — no cluster. `user_buckets` maps (username, device) -> list of
    date_histogram bucket-start epoch-ms; keying on device keeps a user's sessions
    on different FortiGates separate. Returns one dict per session:
    {username, device, session_started, last_seen, bytes_in, bytes_out, status}.
    """
    sessions: list[dict] = []
    for key, raw in user_buckets.items():
        stamps = sorted(raw)
        if not stamps:
            continue
        durs = (dur_by_bucket or {}).get(key, {})
        run = [stamps[0]]
        run_capped = False   # was the current run's start created by a 9h cap split?
        for ts in stamps[1:]:
            prev = run[-1]
            # session_duration is monotonic within one login, so any decrease is a reset.
            reset = ts in durs and prev in durs and durs[ts] < durs[prev]
            real_split = ts - prev > gap_ms or _wib_day(ts) != _wib_day(prev) or reset
            # A run longer than the 9h cap must be more than one session (the firewall
            # caps at 9h; if its counter never reset, split on wall-clock instead).
            over_max = ts - run[0] > SESSION_MAX_MS
            if real_split or over_max:
                sessions.append(_session(key, run, now_ms, gap_ms, bucket_ms,
                                         bytes_by_bucket, durs, run_capped))
                run = [ts]
                # A pure cap split is the SAME login continuing — its duration still
                # points at the original login, so don't use it for the exact start.
                run_capped = over_max and not real_split
            else:
                run.append(ts)
        sessions.append(_session(key, run, now_ms, gap_ms, bucket_ms,
                                 bytes_by_bucket, durs, run_capped))
    return sessions


def _session(key, run, now_ms, gap_ms, bucket_ms, bytes_by_bucket, durs=None, capped=False) -> dict:
    username, device = key
    last_seen = run[-1] + bucket_ms          # bucket start + interval ≈ last activity edge
    started = run[0]
    if durs and run[-1] in durs and not capped:
        # Exact login from the device's session age: login = last_sample − duration.
        # Clamped to 00:00 WIB (rule 1) and to the first observed bucket so it never
        # reports a start after the first sample.
        day0 = _wib_day(run[0]) * 86_400_000 - _WIB_OFFSET_MS
        exact = run[-1] + bucket_ms - durs[run[-1]] * 1000
        started = max(day0, min(exact, run[0] + bucket_ms))
    b_in = b_out = 0
    if bytes_by_bucket is not None:
        vals = [bytes_by_bucket[key][t] for t in run if t in bytes_by_bucket.get(key, {})]
        if vals:                              # counter is cumulative per session -> the run's max
            b_in = max(v[0] for v in vals)
            b_out = max(v[1] for v in vals)
    return {
        "username": username,
        "device": device,
        "session_started": started,
        "last_seen": last_seen,
        "bytes_in": b_in,
        "bytes_out": b_out,
        "status": "active" if last_seen >= now_ms - gap_ms else "ended",
    }


async def fetch_session_buckets(
    client: AsyncOpenSearch,
    index: str,
    base_filter: list[dict],
    gte_ms: int,
    lte_ms: int,
    bytes_in_field: str,
    bytes_out_field: str,
    bucket: str = "60s",
    dur_field: str | None = None,
) -> tuple[
    dict[tuple[str, str], list[int]],
    dict[tuple[str, str], dict[int, tuple[int, int]]],
    dict[tuple[str, str], dict[int, int]],
]:
    """Composite agg over (username, device, 60s bucket) — paginates, so it never
    trips search.max_buckets no matter how many users are online. Keyed by
    (username, device) so a user's sessions on different FortiGates stay separate.
    When `dur_field` is given, also captures the max session age per bucket."""
    times: dict[tuple[str, str], list[int]] = defaultdict(list)
    byb: dict[tuple[str, str], dict[int, tuple[int, int]]] = defaultdict(dict)
    durs: dict[tuple[str, str], dict[int, int]] = defaultdict(dict)
    after: dict | None = None
    for _ in range(500):  # ponytail: hard page cap so a broken after_key can't loop forever
        sub_aggs: dict = {
            "bin": {"max": {"field": bytes_in_field}},
            "bout": {"max": {"field": bytes_out_field}},
        }
        if dur_field:
            sub_aggs["dur"] = {"max": {"field": dur_field}}
        comp: dict = {
            "composite": {
                "size": 1000,
                "sources": [
                    {"user": {"terms": {"field": "tag.username.keyword"}}},
                    {"device": {"terms": {"field": "tag.device.keyword", "missing_bucket": True}}},
                    {"t": {"date_histogram": {"field": "@timestamp", "fixed_interval": bucket}}},
                ],
            },
            "aggs": sub_aggs,
        }
        if after:
            comp["composite"]["after"] = after
        body = {
            "size": 0,
            "query": {"bool": {"filter": base_filter + [
                {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            ]}},
            "aggs": {"s": comp},
        }
        agg = (await safe_search(client, index, body)).get("aggregations", {}).get("s", {})
        buckets = agg.get("buckets", [])
        for b in buckets:
            key = (b["key"]["user"], b["key"].get("device") or "")
            ts = int(b["key"]["t"])
            times[key].append(ts)
            byb[key][ts] = (int((b.get("bin") or {}).get("value") or 0),
                            int((b.get("bout") or {}).get("value") or 0))
            if dur_field:
                dv = (b.get("dur") or {}).get("value")
                if dv is not None:
                    durs[key][ts] = int(dv)
        after = agg.get("after_key")
        if not after or not buckets:
            break
    return times, byb, durs


def sslvpn_measurement_for_site(site_name: str | None) -> str:
    """Map an operator site (Site_FGT-DC) to its SSL VPN measurement (Site_FGT-DC_SSLVPN).

    The alert Site dropdown offers plain site names; SSL VPN samples live under a
    per-site `*_SSLVPN` measurement. A rule that picks 'Site_FGT-DC' must query
    'Site_FGT-DC_SSLVPN' or the cardinality filters on a non-SSLVPN measurement and
    reads 0 users forever. Idempotent — an already-suffixed name is returned as-is.
    """
    if not site_name:
        return "Site_FGT-DC_SSLVPN"
    return site_name if site_name.endswith("_SSLVPN") else f"{site_name}_SSLVPN"


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


async def sslvpn_usage_summary(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC_SSLVPN",
) -> dict[str, Any]:
    """Volume consumed by active SSL VPN users in the window (for capacity alerting).

    Per user, bytes_in/bytes_out are cumulative session counters — max over the window is
    that user's consumption. Returns {count, total_bytes, top_user_bytes, top_users}:
    top_users is the per-user breakdown [{user, bytes}] sorted heaviest-first, so a
    notification can name the offending users, not just count them.
    ponytail: max-per-user = one session's peak; a reconnect (counter reset) counts the
    larger session, not the sum — fine for a capacity threshold.
    """
    if client is None:
        client = get_dc_client()
    body = {
        "size": 0,
        "query": {"bool": {"filter": _sslvpn_filters(gte_ms, lte_ms, site_name)}},
        "aggs": {
            "by_user": {
                "terms": {"field": "tag.username.keyword", "size": 1000},
                "aggs": {
                    "bin": {"max": {"field": f"{site_name}.bytes_in"}},
                    "bout": {"max": {"field": f"{site_name}.bytes_out"}},
                },
            }
        },
    }
    resp = await safe_search(client, "telegraf-index*", body)
    buckets = resp.get("aggregations", {}).get("by_user", {}).get("buckets", [])
    per_user = sorted(
        ((str(b["key"]),
          int(b.get("bin", {}).get("value") or 0) + int(b.get("bout", {}).get("value") or 0))
         for b in buckets),
        key=lambda kv: kv[1], reverse=True,
    )
    return {
        "count": len(per_user),
        "total_bytes": sum(v for _, v in per_user),
        "top_user_bytes": per_user[0][1] if per_user else 0,
        "top_users": [{"user": u, "bytes": v} for u, v in per_user[:20]],
    }


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

    resp = await safe_search(client, "telegraf-index*", body)
    value = resp.get("aggregations", {}).get("active_users", {}).get("value", 0) or 0
    return int(value)


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

    resp = await safe_search(client, "telegraf-index*", body)
    value = resp.get("aggregations", {}).get("active_users", {}).get("value", 0) or 0
    return int(value)


async def all_sslvpn_users_count_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_names: Optional[list[str]] = None,
    interval: str = "1h",
) -> dict[int, int]:
    """
    Q-05: date_histogram with cardinality sub-agg for user count over time.
    Returns dict mapping timestamp (ms) -> user count.
    """
    if client is None:
        client = get_dc_client()
    if site_names is None:
        from app.core.config import get_settings
        site_names = get_settings().sslvpn_sites_list
    if not site_names:
        return {}

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
                    {"terms": {"measurement_name.keyword": site_names}},
                ]
            }
        },
        "aggs": {
            "over_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval},
                "aggs": {"active_users": {"cardinality": {"field": "tag.username.keyword"}}},
            }
        },
    }

    resp = await safe_search(client, "telegraf-index*", body)
    return {
        int(bucket["key"]): int(bucket["active_users"]["value"])
        for bucket in resp.get("aggregations", {}).get("over_time", {}).get("buckets", [])
    }


async def active_sslvpn_users(
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
                "terms": {"field": "tag.username.keyword", "size": 500},  # Q-02
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
                                    f"{site_name}.session_duration",
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

    resp = await safe_search(client, "telegraf-index*", body)
    buckets = resp.get("aggregations", {}).get("by_user", {}).get("buckets", [])

    results = []
    for bucket in buckets:
        hits = bucket["latest"]["hits"]["hits"]
        if not hits:
            continue
        src = hits[0]["_source"]
        site_data = src.get(site_name, {})
        tag = src.get("tag", {})
        # Real login epoch-ms = latest sample time − the device's session age. `sort` carries
        # the doc's @timestamp (ms) since we sort by it. None when the age field is absent, so
        # callers can fall back. Same derivation the Sessions History page uses.
        latest_ms = int((hits[0].get("sort") or [0])[0] or 0)
        dur_s = int(site_data.get("session_duration", 0) or 0)
        session_started = (latest_ms - dur_s * 1000) if (latest_ms and dur_s > 0) else None

        results.append({
            "username": tag.get("username", bucket["key"]),
            "device": tag.get("device", ""),
            "remote_ip": site_data.get("remote_ip", ""),
            "vpn_ip": site_data.get("vpn_ip", ""),
            "bytes_in": int(site_data.get("bytes_in", 0) or 0),
            "bytes_out": int(site_data.get("bytes_out", 0) or 0),
            "session_started": session_started,
            "last_seen": latest_ms or None,  # newest sample ts — the real last-activity edge
        })

    return results


async def sslvpn_session_history(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    site_name: str = "Site_FGT-DC_SSLVPN",
    now_ms: int | None = None,
    gap_ms: int = SESSION_GAP_MS,
    bucket: str = "60s",
) -> list[dict]:
    """Per-session history: real session_started (not window-clamped min), split on
    a >5min log gap (reconnect) and at 00:00 WIB. `gte_ms` is the start-of-day of the
    selected range, so EVERY session on the covered day(s) is returned — a daily view,
    not just the sessions live at the sub-day window edge.
    """
    if client is None:
        client = get_dc_client()
    times, byb, durs = await fetch_session_buckets(
        client, "telegraf-index*",
        [{"term": {"measurement_name.keyword": site_name}}],
        gte_ms, lte_ms,
        f"{site_name}.bytes_in", f"{site_name}.bytes_out",
        bucket=bucket, dur_field=f"{site_name}.session_duration",
    )
    return sessionize(times, now_ms if now_ms is not None else lte_ms,
                      gap_ms, _BUCKET_MS_OF.get(bucket, _BUCKET_MS), byb, durs)


async def user_bandwidth_timeline(
    client: AsyncOpenSearch | None = None,
    gte_ms: int = 0,
    lte_ms: int = 0,
    username: str = "",
    site_names: Optional[list[str]] = None,
    interval: str = "5m",
) -> list[dict]:
    """
    Q-05: date_histogram with sum aggregation for bandwidth per user over time.
    Returns list of {timestamp, bytes_in, bytes_out} points.
    """
    if client is None:
        client = get_dc_client()
    if site_names is None:
        from app.core.config import get_settings
        site_names = get_settings().sslvpn_sites_list
    if not site_names or not username:
        return []

    body = {
        "size": 0,
        "query": {
            "bool": {
                "filter": [
                    {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
                    {"terms": {"measurement_name.keyword": site_names}},
                    {"term": {"tag.username.keyword": username}},
                ]
            }
        },
        "aggs": {
            "over_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval},
                "aggs": {
                    "bytes_in": {"sum": {"field": "sslvpn.bytes_received"}},
                    "bytes_out": {"sum": {"field": "sslvpn.bytes_sent"}},
                },
            }
        },
    }

    resp = await safe_search(client, "telegraf-index*", body)
    results = []
    for bucket in resp.get("aggregations", {}).get("over_time", {}).get("buckets", []):
        aggs = bucket.get("bytes_in", {})
        aggs_out = bucket.get("bytes_out", {})
        results.append({
            "timestamp": int(bucket["key"]),
            "bytes_in": int(aggs.get("value", 0) or 0),
            "bytes_out": int(aggs_out.get("value", 0) or 0),
        })
    return results