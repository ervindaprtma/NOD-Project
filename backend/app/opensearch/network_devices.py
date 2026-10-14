"""OpenSearch query builders for multi-vendor Network Device Monitoring (plan v3).

Covers FortiGate (fgt_iface_stats), H3C (h3c_interface) and Cisco (cisco_interface)
over telegraf-index* on both clusters. Identity is uniform: one tag.source IP =
one physical device, name = tag.hostname (FGT: tag.device). NO dedup/merge — the
v2.1 dual-collector subsystem is gone (.13/.14 are distinct switches, v3 §12).

Step 2 scope: VENDOR_PROFILE + current-state roster/summary/interface queries +
cluster-union routing + `state` enum derivation. Charts/history (octet-rate
derivative) land in step 4.

Q-rules enforced: Q-01 @timestamp range, Q-02 explicit terms size, Q-03 _source
includes (never _source:true), Q-04 no scroll, Q-06 exact measurement term,
Q-07 no N+1 (one nested agg per measurement per cluster, issued concurrently).
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional

from opensearchpy import AsyncOpenSearch

from app.opensearch.client import get_dc_client, get_drc_client
from app.opensearch.device_uptime import STALE_AFTER_MS
from app.opensearch.query import drop_partial_tail, safe_search

INDEX_PATTERN = "telegraf-index*"

# Per-device / per-interface terms caps (Q-02). 500 ifaces covers the largest
# switch (TOR = 183); 64 sources is well above today's ~11 devices per cluster.
_IFACE_SIZE = 500
_SOURCE_SIZE = 64

# FGT has no tag.site; resolve site from source IP (plan H5, reconciled to live
# IPs). FGT-OFFICE re-IPed to 10.70.150.1 (device_ip_aliases). Switches self-
# resolve from tag.site, so they are absent here.
FGT_IP_SITE: dict[str, str] = {
    "10.80.150.1": "DC",
    "10.90.150.1": "DRC",
    "10.70.150.1": "Office",
}

# tag.site casing drifts (H3C emits "DC"/"drc", Cisco "office"). Normalize to the
# canonical DC / DRC / Office used everywhere in the UI.
_SITE_NORM = {"dc": "DC", "drc": "DRC", "office": "Office"}

# Current-state reads (roster/interfaces/topology) look back 2× the offline grace.
# Short enough to keep the per-iface top_hits scan cheap on a 183-iface switch (M1),
# long enough that a device that JUST went offline still shows (as red) rather than
# vanishing. A device silent longer than this drops off the live view — long-outage
# history is the Availability tab's job (device_uptime), not this snapshot.
# ponytail: fixed 10-min snapshot window; narrow further (or split discovery from
# state scan) only if the agg gets slow on the DC cluster.
CURRENT_STATE_WINDOW_MS: int = 2 * STALE_AFTER_MS


def current_window(now_ms: Optional[int] = None) -> tuple[int, int]:
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    return now - CURRENT_STATE_WINDOW_MS, now


# ── Vendor profiles (typed dict in code, NOT runtime JSON — plan §5) ──
# H3C and Cisco share one byte-identical schema; only the measurement differs.
_SWITCH_FIELDS = {
    "if_name": "tag.if_name",
    "ifindex": "tag.index",
    "speed": "active_speed_mbps",
    "oper_num": "oper_status",
    "oper_text": "oper_status_text",
    "admin_text": "admin_status_text",
    "desc": "description",
    "in_oct": "in_octets",
    "out_oct": "out_octets",
    "err_fields": ["in_errors", "out_errors", "in_discards", "out_discards"],
    "hostname": "tag.hostname",
    "site": "tag.site",
}

VENDOR_PROFILE: dict[str, dict[str, Any]] = {
    "fortigate": {
        "measurement": "fgt_iface_stats",
        "if_name": "tag.ifName",
        "ifindex": "tag.ifIndex",
        "speed": "ifHighSpeed_Mbps",
        "oper_num": "ifOperStatus",
        "oper_text": None,      # FGT reports only numeric ifOperStatus
        "admin_text": None,     # FGT has no admin status in this measurement
        "desc": "tag.ifAlias",  # verified present (e.g. "Testing ADVPN")
        "in_oct": "ifHCInOctets",
        "out_oct": "ifHCOutOctets",
        "err_fields": ["ifInErrors", "ifOutErrors", "ifInDiscards", "ifOutDiscards"],
        "hostname": "tag.device",  # FGT has no tag.hostname — name = tag.device
        "site": None,              # resolved via FGT_IP_SITE
    },
    "h3c": {"measurement": "h3c_interface", **_SWITCH_FIELDS},
    "cisco": {"measurement": "cisco_interface", **_SWITCH_FIELDS},
}

_MEASUREMENT_VENDOR = {p["measurement"]: v for v, p in VENDOR_PROFILE.items()}


def _norm_site(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    return _SITE_NORM.get(raw.strip().lower(), raw)


def _derive_state(admin_text: Optional[str], oper_text: Optional[str],
                  oper_num: Any) -> tuple[str, Optional[str], Optional[str]]:
    """Return (state_enum, oper_str, admin_str) from raw SNMP fields (plan §state).

    admin down         -> "admin_down" (grey)
    else oper up       -> "up" (green)
    else               -> "down" (red; enabled but no link)
    FGT: admin_text/oper_text are None -> oper derived from numeric (1=up, 2=down),
    never admin_down.
    """
    admin = admin_text.strip().lower() if isinstance(admin_text, str) else None
    oper = oper_text.strip().lower() if isinstance(oper_text, str) else None
    if oper is None and oper_num is not None:
        oper = "up" if int(oper_num) == 1 else "down"
    if admin == "down":
        return "admin_down", oper, "down"
    if oper == "up":
        return "up", "up", admin
    return "down", oper, admin


def _source_fields(p: dict) -> list[str]:
    """The _source includes list for the latest-doc top_hits (Q-03)."""
    fields = [p["if_name"], p["ifindex"], p["speed"], p["oper_num"],
              p["desc"], p["hostname"]]
    for k in ("oper_text", "admin_text", "site"):
        if p[k]:
            fields.append(p[k])
    return [f for f in fields if f]


def _interface_agg_body(p: dict, gte_ms: int, lte_ms: int,
                        source_ip: Optional[str]) -> dict:
    """Nested agg: by_source -> (last_seen, by_iface -> latest doc). One query per
    measurement per cluster (Q-07), scoped to one source when given (device detail)."""
    filt: list[dict] = [
        {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
        {"term": {"measurement_name.keyword": p["measurement"]}},
    ]
    if source_ip:
        filt.append({"term": {"tag.source.keyword": source_ip}})
    return {
        "size": 0,
        "query": {"bool": {"filter": filt}},
        "aggs": {
            "by_source": {
                "terms": {"field": "tag.source.keyword", "size": _SOURCE_SIZE},
                "aggs": {
                    "last_seen": {"max": {"field": "@timestamp"}},
                    "by_iface": {
                        "terms": {"field": f'{p["if_name"]}.keyword', "size": _IFACE_SIZE},
                        "aggs": {
                            "latest": {
                                "top_hits": {
                                    "size": 1,
                                    "sort": [{"@timestamp": "desc"}],
                                    "_source": {"includes": _source_fields(p)},
                                }
                            }
                        },
                    },
                },
            }
        },
    }


def _get(src: dict, dotted: Optional[str]) -> Any:
    """Read a possibly-dotted field path from a flattened-or-nested _source."""
    if not dotted:
        return None
    if dotted in src:                      # telegraf _source is usually flat ("tag.source")
        return src[dotted]
    cur: Any = src                         # fall back to true nesting
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _parse_source_bucket(p: dict, vendor: str, cluster: str, bucket: dict,
                         now_ms: int) -> dict:
    """Turn one by_source agg bucket into a device dict (name/site/state counts)."""
    source_ip = bucket["key"]
    last_seen = int((bucket.get("last_seen") or {}).get("value") or 0)
    ifaces: list[dict] = []
    counts = {"up": 0, "down": 0, "admin_down": 0}
    name: Optional[str] = None
    site: Optional[str] = None
    for ib in bucket.get("by_iface", {}).get("buckets", []):
        hits = ib.get("latest", {}).get("hits", {}).get("hits", [])
        if not hits:
            continue
        s = hits[0].get("_source", {})
        state, oper, admin = _derive_state(
            _get(s, p["admin_text"]), _get(s, p["oper_text"]), _get(s, p["oper_num"])
        )
        counts[state] += 1
        speed = _get(s, p["speed"])
        ifaces.append({
            "if_name": ib["key"],
            "ifindex": _get(s, p["ifindex"]),
            "state": state,
            "oper": oper,
            "admin": admin,
            "speed_mbps": float(speed) if speed not in (None, "") else None,
            "description": _get(s, p["desc"]),
        })
        if name is None:
            name = _get(s, p["hostname"])
        if site is None:
            site = _norm_site(_get(s, p["site"])) if p["site"] else None
    if site is None:                       # FGT (no tag.site) -> IP map
        site = FGT_IP_SITE.get(source_ip)
    return {
        "source_ip": source_ip,
        "vendor": vendor,
        "site": site,
        "name": name or source_ip,
        "last_seen_ms": last_seen,
        "online": last_seen > 0 and (now_ms - last_seen) <= STALE_AFTER_MS,
        "cluster": cluster,
        "counts": {**counts, "total": sum(counts.values())},
        "interfaces": sorted(ifaces, key=lambda i: str(i["if_name"])),
    }


async def collect_devices(gte_ms: int, lte_ms: int,
                          source_ip: Optional[str] = None) -> list[dict]:
    """Current-state device roster across BOTH clusters, unioned by source_ip.

    One nested agg per vendor per cluster, all issued concurrently (Q-07). When the
    same source_ip appears on both clusters (a stale DC copy of an Office device,
    frozen post-cutover), the entry with the freshest last_seen wins — so the stale
    DC copy is dropped without a hardcoded cluster map. Over a short recent window
    (M1) a stale copy simply has no docs and never appears.
    """
    now_ms = int(time.time() * 1000)
    clients: list[tuple[str, AsyncOpenSearch]] = [
        ("dc", get_dc_client()), ("drc", get_drc_client())
    ]
    jobs: list[tuple[str, str, AsyncOpenSearch]] = [
        (vendor, cluster, client)
        for vendor in VENDOR_PROFILE
        for cluster, client in clients
    ]
    results = await asyncio.gather(*(
        safe_search(client, INDEX_PATTERN,
                    _interface_agg_body(VENDOR_PROFILE[vendor], gte_ms, lte_ms, source_ip))
        for vendor, cluster, client in jobs
    ))

    by_ip: dict[str, dict] = {}
    for (vendor, cluster, _client), resp in zip(jobs, results):
        p = VENDOR_PROFILE[vendor]
        for bucket in resp.get("aggregations", {}).get("by_source", {}).get("buckets", []):
            dev = _parse_source_bucket(p, vendor, cluster, bucket, now_ms)
            existing = by_ip.get(dev["source_ip"])
            if existing is None or dev["last_seen_ms"] > existing["last_seen_ms"]:
                by_ip[dev["source_ip"]] = dev  # freshest wins (drops stale cross-cluster copy)
    return sorted(by_ip.values(), key=lambda d: (d["site"] or "~", d["vendor"], d["name"]))


async def device_interfaces(source_ip: str, gte_ms: int, lte_ms: int) -> Optional[dict]:
    """Interface boxes for one device (device detail). Returns None if not found."""
    devices = await collect_devices(gte_ms, lte_ms, source_ip=source_ip)
    return devices[0] if devices else None


# ─────────────────────────────────────────────────────────────────
# Step 4 — per-interface charts + state history
# ─────────────────────────────────────────────────────────────────

# Bounded-bucket ladder (mirrors device_uptime._bucket_for_span): keeps a chart
# at ≤ ~600 buckets so long windows stay fast (Q-08 spirit). Transition-history
# resolution equals the bucket size — fine on short windows, coarse on long ones.
_INTERVAL_LADDER = ((3_600, "1m"), (21_600, "5m"), (86_400, "15m"),
                    (604_800, "1h"), (2_592_000, "6h"), (7_776_000, "12h"))
_INTERVAL_SECONDS = {"1m": 60, "5m": 300, "15m": 900, "1h": 3600, "6h": 21600, "12h": 43200}


def _chart_interval(gte_ms: int, lte_ms: int) -> str:
    span = (lte_ms - gte_ms) / 1000
    for limit, interval in _INTERVAL_LADDER:
        if span <= limit:
            return interval
    return "12h"


async def resolve_source(source_ip: str, gte_ms: int, lte_ms: int) -> Optional[dict]:
    """Which cluster + vendor owns this source over the window? Cheap size-0 probe
    of each (vendor, cluster) for the source's freshest @timestamp; the freshest
    wins (drops the stale cross-cluster copy). Works even for a device that went
    offline mid-window (it still has docs earlier in the window), unlike the 10-min
    current-state snapshot. Returns {vendor, cluster, client} or None."""
    clients: list[tuple[str, AsyncOpenSearch]] = [("dc", get_dc_client()), ("drc", get_drc_client())]
    jobs = [(vendor, cluster, client) for vendor in VENDOR_PROFILE for cluster, client in clients]
    results = await asyncio.gather(*(
        safe_search(client, INDEX_PATTERN, {
            "size": 0,
            "query": {"bool": {"filter": [
                {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
                {"term": {"measurement_name.keyword": VENDOR_PROFILE[vendor]["measurement"]}},
                {"term": {"tag.source.keyword": source_ip}},
            ]}},
            "aggs": {"last_seen": {"max": {"field": "@timestamp"}}},
        }) for vendor, cluster, client in jobs
    ))
    best: Optional[dict] = None
    for (vendor, cluster, client), resp in zip(jobs, results):
        last = (resp.get("aggregations", {}).get("last_seen") or {}).get("value")
        if last and (best is None or last > best["last_seen"]):
            best = {"vendor": vendor, "cluster": cluster, "client": client, "last_seen": last}
    return best


async def interface_chart(source_ip: str, ifindex: str, gte_ms: int, lte_ms: int,
                          metric: str = "mbps") -> Optional[dict]:
    """Per-bucket chart series for one interface (ifindex-keyed). metric='mb' →
    volume (MB moved per bucket); 'mbps' → rate + utilization-% line.

    Octets are cumulative SNMP counters → date_histogram + derivative gives the
    per-bucket delta (same pattern + skip-rules as interface_stats.py: first bucket
    has no derivative, counter resets go negative, a gap-resume delta spans the whole
    outage and must not be divided by one bucket). min_doc_count=0 is MANDATORY for
    the derivative. Returns None if the source can't be resolved."""
    src = await resolve_source(source_ip, gte_ms, lte_ms)
    if src is None:
        return None
    p = VENDOR_PROFILE[src["vendor"]]
    interval = _chart_interval(gte_ms, lte_ms)
    secs = _INTERVAL_SECONDS[interval]
    body = {
        "size": 0,
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            {"term": {"measurement_name.keyword": p["measurement"]}},
            {"term": {"tag.source.keyword": source_ip}},
            {"term": {f'{p["ifindex"]}.keyword': str(ifindex)}},
        ]}},
        "aggs": {
            "speed": {"max": {"field": p["speed"]}},
            "by_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval, "min_doc_count": 0},
                "aggs": {
                    "in_oct": {"max": {"field": p["in_oct"]}},
                    "out_oct": {"max": {"field": p["out_oct"]}},
                    "in_d": {"derivative": {"buckets_path": "in_oct"}},
                    "out_d": {"derivative": {"buckets_path": "out_oct"}},
                },
            },
        },
    }
    resp = await safe_search(src["client"], INDEX_PATTERN, body)
    agg = resp.get("aggregations", {})
    speed = (agg.get("speed") or {}).get("value") or 0.0
    buckets = drop_partial_tail(agg.get("by_time", {}).get("buckets", []), secs, lte_ms)
    return {
        "metric": metric,
        "interval": interval,
        "speed_mbps": float(speed) if speed else None,  # None → zero-speed guard (no util)
        "points": _parse_chart_buckets(buckets, secs, speed, metric),
    }


def _parse_chart_buckets(buckets: list[dict], secs: int, speed: float, metric: str) -> list[dict]:
    """Pure: octet-counter buckets → per-bucket in/out points. Skips the first
    (derivative-less) bucket, counter resets (negative delta), and gap-resume deltas
    that span an outage — same rules as interface_stats.py."""
    points: list[dict] = []
    prev_present = False
    for tb in buckets:
        io = tb.get("in_oct", {}).get("value")
        oo = tb.get("out_oct", {}).get("value")
        di = tb.get("in_d", {}).get("value")
        do = tb.get("out_d", {}).get("value")
        gap_resume = not prev_present and (di is not None or do is not None)
        prev_present = io is not None or oo is not None
        if di is None or do is None:   # first bucket: no derivative
            continue
        if di < 0 or do < 0:           # counter reset (reboot) → skip
            continue
        if gap_resume:                 # cross-outage delta → not a per-bucket rate
            continue
        if metric == "mb":
            points.append({"ts_ms": tb["key"], "in": di / 1e6, "out": do / 1e6})
        else:
            rx, tx = di * 8 / secs / 1e6, do * 8 / secs / 1e6
            pt = {"ts_ms": tb["key"], "in": rx, "out": tx}
            if speed > 0:
                pt["util_pct"] = max(rx, tx) / speed * 100
            points.append(pt)
    return points


def detect_transitions(series: dict[str, list[tuple[int, str]]],
                       offline_spans: tuple[tuple[int, int], ...] = ()) -> list[dict]:
    """Shared state-transition detector (one implementation, two consumers: this
    history endpoint and the alert engine's interface_state rule — DRY, plan §3.2#3).

    `series` maps if_name → time-ascending (bucket_ts, state) pairs of the buckets
    that actually reported; absent buckets inherit the last known state. A transition
    is emitted when a reported state differs from the last reported state. The carried
    duration is flagged `offline_gap` when the carry period overlaps a device outage,
    so it is never silently blended across missing data. Newest first."""
    def _in_offline(a: int, b: int) -> bool:
        return any(s < b and e > a for s, e in offline_spans)

    out: list[dict] = []
    for if_name, buckets in series.items():
        last_state: Optional[str] = None
        last_ts: Optional[int] = None
        for ts, state in buckets:  # ascending
            if last_state is None:
                last_state, last_ts = state, ts
                continue
            if state != last_state:
                out.append({
                    "at_ms": ts, "if_name": if_name, "from": last_state, "to": state,
                    "duration_before_ms": ts - (last_ts if last_ts is not None else ts),
                    "offline_gap": _in_offline(last_ts if last_ts is not None else ts, ts),
                })
                last_state, last_ts = state, ts
    out.sort(key=lambda t: t["at_ms"], reverse=True)
    return out


async def state_history(source_ip: str, gte_ms: int, lte_ms: int) -> Optional[dict]:
    """Interface state-change rows for one device (plan §3.2 ROW B). Builds a
    per-interface state array from per-bucket latest docs, plus device-level offline
    spans (buckets with zero docs), then runs the shared detector. Returns None if
    the source can't be resolved."""
    src = await resolve_source(source_ip, gte_ms, lte_ms)
    if src is None:
        return None
    p = VENDOR_PROFILE[src["vendor"]]
    interval = _chart_interval(gte_ms, lte_ms)
    includes = [f for f in (p["oper_num"], p["oper_text"], p["admin_text"]) if f]
    body = {
        "size": 0,
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            {"term": {"measurement_name.keyword": p["measurement"]}},
            {"term": {"tag.source.keyword": source_ip}},
        ]}},
        "aggs": {
            # device-level count per bucket → zero-count buckets are offline spans
            "device_time": {"date_histogram": {"field": "@timestamp", "fixed_interval": interval,
                                               "min_doc_count": 0}},
            "by_iface": {
                "terms": {"field": f'{p["if_name"]}.keyword', "size": _IFACE_SIZE},
                "aggs": {"by_time": {
                    "date_histogram": {"field": "@timestamp", "fixed_interval": interval, "min_doc_count": 1},
                    "aggs": {"latest": {"top_hits": {"size": 1, "sort": [{"@timestamp": "desc"}],
                                                     "_source": {"includes": includes}}}},
                }},
            },
        },
    }
    resp = await safe_search(src["client"], INDEX_PATTERN, body)
    agg = resp.get("aggregations", {})

    # offline spans: merge consecutive zero-count device buckets
    offline: list[tuple[int, int]] = []
    isecs_ms = _INTERVAL_SECONDS[interval] * 1000
    for db in agg.get("device_time", {}).get("buckets", []):
        if db.get("doc_count", 0) == 0:
            start, end = db["key"], db["key"] + isecs_ms
            if offline and offline[-1][1] == start:
                offline[-1] = (offline[-1][0], end)
            else:
                offline.append((start, end))

    series: dict[str, list[tuple[int, str]]] = {}
    for ib in agg.get("by_iface", {}).get("buckets", []):
        arr: list[tuple[int, str]] = []
        for tb in ib.get("by_time", {}).get("buckets", []):
            hits = tb.get("latest", {}).get("hits", {}).get("hits", [])
            if not hits:
                continue
            s = hits[0].get("_source", {})
            state, _o, _a = _derive_state(_get(s, p["admin_text"]), _get(s, p["oper_text"]),
                                          _get(s, p["oper_num"]))
            arr.append((tb["key"], state))
        if arr:
            series[ib["key"]] = arr
    return {"interval": interval, "transitions": detect_transitions(series, tuple(offline))}


# ─────────────────────────────────────────────────────────────────
# Step 5 — alert-engine group builders (one query per device per tick, Q-07)
# ─────────────────────────────────────────────────────────────────


async def alert_interface_states(source_ip: str, gte_ms: int, lte_ms: int) -> dict[str, str]:
    """{if_name: state} for the alert engine's interface_state rule. Current state per
    interface over the eval window (reuses the roster read). Empty on no data → the
    engine holds the rule rather than false-firing."""
    dev = await device_interfaces(source_ip, gte_ms, lte_ms)
    if not dev:
        return {}
    return {i["if_name"]: i["state"] for i in dev["interfaces"]}


async def alert_interface_errors(source_ip: str, gte_ms: int, lte_ms: int) -> dict[str, float]:
    """{if_name: total error+discard delta} over the window for the interface_errors rule.

    Error/discard counters are cumulative → per-bucket derivative, summing only
    positive deltas (counter resets skipped), across all four counters. One query per
    device (Q-07). Empty on no data."""
    src = await resolve_source(source_ip, gte_ms, lte_ms)
    if src is None:
        return {}
    p = VENDOR_PROFILE[src["vendor"]]
    interval = _chart_interval(gte_ms, lte_ms)
    err_fields: list[str] = p["err_fields"]
    by_time_aggs: dict = {}
    for i, fld in enumerate(err_fields):
        by_time_aggs[f"e{i}"] = {"max": {"field": fld}}
        by_time_aggs[f"d{i}"] = {"derivative": {"buckets_path": f"e{i}"}}
    body = {
        "size": 0,
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            {"term": {"measurement_name.keyword": p["measurement"]}},
            {"term": {"tag.source.keyword": source_ip}},
        ]}},
        "aggs": {"by_iface": {
            "terms": {"field": f'{p["if_name"]}.keyword', "size": _IFACE_SIZE},
            "aggs": {"by_time": {
                "date_histogram": {"field": "@timestamp", "fixed_interval": interval, "min_doc_count": 0},
                "aggs": by_time_aggs,
            }},
        }},
    }
    resp = await safe_search(src["client"], INDEX_PATTERN, body)
    out: dict[str, float] = {}
    for ib in resp.get("aggregations", {}).get("by_iface", {}).get("buckets", []):
        total = 0.0
        for tb in ib.get("by_time", {}).get("buckets", []):
            for i in range(len(err_fields)):
                d = tb.get(f"d{i}", {}).get("value")
                if d is not None and d > 0:  # skip first bucket (None) and resets (<0)
                    total += d
        out[ib["key"]] = total
    return out


# ─────────────────────────────────────────────────────────────────
# Step 8 — topology live reads (H4: one agg per mapped device, batched)
# ─────────────────────────────────────────────────────────────────


async def _device_topology_live(source_ip: str, if_names: list[str],
                                gte_ms: int, lte_ms: int) -> dict:
    """Freshness + per-mapped-interface oper + octet-rate for ONE device, in one query.

    Only the interfaces actually on the map are queried (H4). Returns
    {online, interfaces: {if_name: {oper, rx_mbps, tx_mbps}}}."""
    src = await resolve_source(source_ip, gte_ms, lte_ms)
    if src is None:
        return {"online": False, "interfaces": {}}
    p = VENDOR_PROFILE[src["vendor"]]
    now_ms = int(time.time() * 1000)
    body = {
        "size": 0,
        "query": {"bool": {"filter": [
            {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}},
            {"term": {"measurement_name.keyword": p["measurement"]}},
            {"term": {"tag.source.keyword": source_ip}},
            {"terms": {f'{p["if_name"]}.keyword': if_names}},
        ]}},
        "aggs": {
            "last_seen": {"max": {"field": "@timestamp"}},
            "by_iface": {
                "terms": {"field": f'{p["if_name"]}.keyword', "size": max(len(if_names), 1)},
                "aggs": {
                    "oper": {"top_hits": {"size": 1, "sort": [{"@timestamp": "desc"}],
                                          "_source": {"includes": [f for f in (p["oper_num"], p["oper_text"], p["admin_text"]) if f]}}},
                    "by_time": {
                        "date_histogram": {"field": "@timestamp", "fixed_interval": "1m", "min_doc_count": 0},
                        "aggs": {
                            "in_oct": {"max": {"field": p["in_oct"]}},
                            "out_oct": {"max": {"field": p["out_oct"]}},
                            "in_d": {"derivative": {"buckets_path": "in_oct"}},
                            "out_d": {"derivative": {"buckets_path": "out_oct"}},
                        },
                    },
                },
            },
        },
    }
    resp = await safe_search(src["client"], INDEX_PATTERN, body)
    agg = resp.get("aggregations", {})
    last = int((agg.get("last_seen") or {}).get("value") or 0)
    ifaces: dict[str, dict] = {}
    for ib in agg.get("by_iface", {}).get("buckets", []):
        hits = ib.get("oper", {}).get("hits", {}).get("hits", [])
        oper = None
        if hits:
            s = hits[0].get("_source", {})
            _st, oper, _a = _derive_state(_get(s, p["admin_text"]), _get(s, p["oper_text"]), _get(s, p["oper_num"]))
        pts = _parse_chart_buckets(
            drop_partial_tail(ib.get("by_time", {}).get("buckets", []), 60, lte_ms), 60, 0.0, "mbps")
        rx = sum(x["in"] for x in pts) / len(pts) if pts else 0.0
        tx = sum(x["out"] for x in pts) / len(pts) if pts else 0.0
        ifaces[ib["key"]] = {"oper": oper, "rx_mbps": rx, "tx_mbps": tx}
    return {
        "online": last > 0 and (now_ms - last) <= STALE_AFTER_MS,
        "interfaces": ifaces,
    }


async def topology_live(mappings: dict[str, list[str]], gte_ms: int, lte_ms: int) -> dict[str, dict]:
    """Live node freshness + per-end oper + link traffic for the mapped devices.

    `mappings` = {source_ip: [if_name,...]} for data nodes only (support nodes carry no
    data). One aggregation per device, issued concurrently (H4 — N links cost ~N_devices
    sub-requests, not N). Returns {source_ip: {online, interfaces: {...}}}."""
    sources = list(mappings.keys())
    results = await asyncio.gather(*(
        _device_topology_live(ip, mappings[ip], gte_ms, lte_ms) for ip in sources
    ))
    return dict(zip(sources, results))


def summarize(devices: list[dict]) -> dict:
    """Fleet KPI strip + site grouping for the Overview tab (plan §2)."""
    online = sum(1 for d in devices if d["online"])
    up = sum(d["counts"]["up"] for d in devices)
    down = sum(d["counts"]["down"] for d in devices)
    admin_down = sum(d["counts"]["admin_down"] for d in devices)
    sites: dict[str, list[dict]] = {}
    for d in devices:
        sites.setdefault(d["site"] or "Unknown", []).append(d)
    return {
        "devices_total": len(devices),
        "devices_online": online,
        "interfaces_up": up,
        "interfaces_down": down,
        "interfaces_admin_down": admin_down,
        "by_site": sites,
    }
