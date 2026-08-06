"""
Shared filter/mapping helpers used across traffic query builders.
Extracted to eliminate 3× duplication across traffic_flow/inbound/internal.
"""
from __future__ import annotations

from app.port_service_map import port_to_service


FLOW_INDEX = "fortigate-appid-flow-*"


def _port_to_service(port_value) -> str:
    """Convert port number/int to service name, or return as-is."""
    try:
        return port_to_service(int(port_value))
    except (ValueError, TypeError):
        return str(port_value)


# FortiGate AppID labels for "engine could not classify this flow" (application.id 0,
# category Unknown). Kept as a name set so the resolver never fetches application.id.
UNCLASSIFIED = {"app-0", "unclassified", "unknown"}


def resolve_service(app_name, port) -> str:
    """Hybrid service label: FortiGate AppID name first, port fallback when unclassified.

    - app_name classified   -> return it verbatim (HTTPS, POSTGRESQL, ISCSI-TARGET...)
    - app_name unclassified  -> _port_to_service(port)  (iscsi-target, Port-11105, ...)
    - unclassified AND no port -> keep the raw label (app-0 / Unclassified)

    flow.application.name is 100%-present on the flow index while server port is only
    89-97% present on internal paths, so keying on the name preserves full coverage.
    """
    name = str(app_name).strip() if app_name is not None else ""
    if name and name.lower() not in UNCLASSIFIED:
        return name
    resolved = _port_to_service(port) if port not in (None, "", 0, "0") else ""
    return resolved or name or "Unclassified"


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}}


def _bool_query(filt: list[dict], excl: list[dict] | None = None) -> dict:
    """Assemble the bool query. Excludes go to `must_not` (drop a row if it matches ANY).
    `must_not` is OMITTED when empty so exclude-free queries stay byte-identical to before —
    call as `_bool_query(*_base_filters(...))` where _base_filters returns (filter, must_not)."""
    return {"bool": {"filter": filt, **({"must_not": excl} if excl else {})}}


def _split_multi(value: str) -> list[str]:
    """Split comma-separated filter string to list of stripped non-empty values."""
    return [v.strip() for v in value.split(",") if v.strip()]


def _multi_term(field: str, value: str) -> dict | None:
    """Build a term/terms filter from a comma-separated value string."""
    vals = _split_multi(value)
    if not vals:
        return None
    if len(vals) == 1:
        return {"term": {field: vals[0]}}
    return {"terms": {field: vals}}


def _wildcard(field: str, val: str) -> dict:
    # case_insensitive: these target `keyword` fields, where wildcard matching is
    # case-sensitive by default — a user typing "google" would otherwise get zero
    # hits against "Google LLC".
    return {"wildcard": {field: {"value": f"*{val}*", "case_insensitive": True}}}


def _multi_term_any(fields: list[str], value: str) -> dict | None:
    """Match a comma-separated value against ANY of several fields.

    Interface filters are free text. Every surface (breakdown panels, sankey,
    raw-data table) now displays `flow.in.netif.alias` ("WAN-LinkNet"), but a user
    may still type or copy the underlying `flow.in.netif.name` ("wan1"), so match
    both rather than forcing one convention.
    """
    vals = _split_multi(value)
    if not vals:
        return None
    return {
        "bool": {
            "should": [{"terms": {f: vals}} for f in fields],
            "minimum_should_match": 1,
        }
    }


def _multi_wildcard(field: str, value: str) -> dict | None:
    """Build a wildcard or bool/should of wildcards from comma-separated value."""
    vals = _split_multi(value)
    if not vals:
        return None
    if len(vals) == 1:
        return _wildcard(field, vals[0])
    return {"bool": {"should": [_wildcard(field, v) for v in vals], "minimum_should_match": 1}}


def _resolve_ports(term: str) -> list[int]:
    """Resolve a service term to port numbers: digits → exact; text → IANA + alias scan."""
    import socket
    from app.port_service_map import _ALIAS
    s = term.strip()
    if not s:
        return []
    if s.isdigit():
        return [int(s)]
    ports: list[int] = []
    try:
        ports.append(socket.getservbyname(s))
    except OSError:
        pass
    low = s.lower()
    ports.extend(p for p, name in _ALIAS.items() if low in name.lower())
    return ports


def _service_filter(value: str) -> dict | None:
    """App-name-first service filter (decision 3): match flow.application.name (priority),
    OR fall back to the server port when the typed term resolves to one. So typing
    "HTTPS"/"postgres" hits the AppID name and typing "443"/"iscsi" still hits the port
    that classification (app-0) couldn't name. Comma-separated OR."""
    vals = _split_multi(value)
    if not vals:
        return None
    should: list[dict] = []
    for v in vals:
        should.append(_wildcard("flow.application.name", v))
        ports = _resolve_ports(v)
        if ports:
            should.append({"terms": {"flow.server.l4.port.id": ports}})
    return {"bool": {"should": should, "minimum_should_match": 1}}


# Sort key for byte-ranked terms aggs. A terms agg cannot order by `total_bytes`
# below because bucket_script is a pipeline agg, and ordering by `_count` ranks by
# session count instead of volume — which hides the biggest talkers. flow.bytes is
# exactly client.bytes + server.bytes (one side is always zero), so it sorts
# identically to total_bytes while remaining a plain metric agg.
BYTES_DESC = {"sort_bytes": "desc"}


def _bytes_sum() -> dict:
    return {
        "upload_bytes": {"sum": {"field": "flow.client.bytes", "missing": 0}},
        "download_bytes": {"sum": {"field": "flow.server.bytes", "missing": 0}},
        "sort_bytes": {"sum": {"field": "flow.bytes", "missing": 0}},
        "total_bytes": {
            "bucket_script": {
                "buckets_path": {"up": "upload_bytes", "down": "download_bytes"},
                "script": "params.up + params.down"
            }
        }
    }


# ── Hybrid service dimension (flow.application.name + port fallback) ──
# The "Service" breakdown groups on the AppID L7 name (100% present) rather than the
# server port (89-97% present). Unclassified flows (app-0) re-expand to their server
# port so the biggest internal talkers stay distinguishable. See
# design_service_field_migration.md §4.

def service_terms_agg(size: int = 40, port_size: int = 30) -> dict:
    """Outer terms on flow.application.name, with a nested port breakdown used only to
    re-expand the unclassified buckets, plus a no-port remainder. Collapse the result
    with collapse_service_buckets()."""
    return {
        "terms": {"field": "flow.application.name", "size": size, "order": BYTES_DESC},
        "aggs": {
            **_bytes_sum(),
            "byport": {
                "terms": {"field": "flow.server.l4.port.id", "size": port_size, "order": BYTES_DESC},
                "aggs": _bytes_sum(),
            },
            "noport": {
                # Plain sums only — a bucket_script (pipeline agg) inside a single-bucket
                # filter agg triggers an InternalFilter→MultiBucket cast error in the
                # reduce phase, so total is derived in _bytes3 from up+down instead.
                "filter": {"bool": {"must_not": {"exists": {"field": "flow.server.l4.port.id"}}}},
                "aggs": {
                    "upload_bytes": {"sum": {"field": "flow.client.bytes", "missing": 0}},
                    "download_bytes": {"sum": {"field": "flow.server.bytes", "missing": 0}},
                },
            },
        },
    }


def _bytes3(b: dict) -> tuple[int, int, int]:
    # total is always upload+download (total_bytes, when present, is exactly that via
    # bucket_script) — deriving it here lets the noport filter agg skip the pipeline agg.
    up = int(b.get("upload_bytes", {}).get("value", 0))
    down = int(b.get("download_bytes", {}).get("value", 0))
    return (up + down, up, down)


def collapse_service_buckets(app_buckets: list[dict], top_n: int = 20) -> list[dict]:
    """Collapse service_terms_agg() buckets to resolved services, byte-ranked.

    Every emitted key comes from resolve_service(), so the output matches the per-doc
    resolver exactly: classified apps pass through by name; app-0 explodes into its
    per-port children; the no-port unclassified remainder keeps its raw label.
    Returns rows: {service_name, service_port(int|None), total/upload/download_bytes}.
    """
    merged: dict[str, dict] = {}

    def _accum(key: str, t: int, u: int, d: int, port) -> None:
        slot = merged.setdefault(key, {"total_bytes": 0, "upload_bytes": 0, "download_bytes": 0, "_ports": set()})
        slot["total_bytes"] += t
        slot["upload_bytes"] += u
        slot["download_bytes"] += d
        if port not in (None, "", 0, "0"):
            slot["_ports"].add(port)

    for ab in app_buckets:
        app_name = ab.get("key")
        if str(app_name).strip().lower() in UNCLASSIFIED:
            for pb in ab.get("byport", {}).get("buckets", []):
                t, u, d = _bytes3(pb)
                if t <= 0:
                    continue
                _accum(_port_to_service(pb["key"]), t, u, d, pb["key"])
            t, u, d = _bytes3(ab.get("noport", {}))
            if t > 0:
                _accum(str(app_name), t, u, d, None)  # keep raw label (decision 2)
        else:
            t, u, d = _bytes3(ab)
            if t > 0:
                _accum(str(app_name), t, u, d, None)

    rows: list[dict] = []
    for name, v in merged.items():
        ports = v.pop("_ports")
        port = next(iter(ports)) if len(ports) == 1 else None
        try:
            port = int(port) if port is not None else None
        except (ValueError, TypeError):
            port = None
        rows.append({"service_name": name, "service_port": port, **v})
    rows.sort(key=lambda r: -r["total_bytes"])
    return rows[:top_n]


# ── Chart timeline (per-bucket) hybrid grouping ──
# A per-bucket app×port nesting would blow search.max_buckets, so the timeline splits
# into two single-level terms: `by_app` for classified names, and `by_port` (scoped to
# the unclassified docs) for the port fallbacks. Both are pinned to the global top-N
# resolved set from resolve_top_services(), so every series has a value in every bucket.

def resolve_top_services(app_buckets: list[dict], top_n: int) -> tuple[list[str], list[str], list, list[str]]:
    """From service_terms_agg() buckets, pick the global top-N resolved services and
    split them into the keys the per-bucket query needs.

    Returns (charted_names, app_top, port_top, unclassified_labels):
      charted_names       — resolved names to chart (the series set)
      app_top             — classified application.name values (by_app include)
      port_top            — server ports whose resolved name is top-N (by_port include)
      unclassified_labels — raw application.name strings that are unclassified (app-0),
                            case-preserved, for the by_port filter.
    The no-port unclassified remainder is not charted as its own series (never ranks).
    """
    rows = collapse_service_buckets(app_buckets, top_n)
    app_top = [r["service_name"] for r in rows
               if r["service_port"] is None and str(r["service_name"]).strip().lower() not in UNCLASSIFIED]
    port_top = [r["service_port"] for r in rows if r["service_port"] is not None]
    unclassified_labels = [b["key"] for b in app_buckets if str(b.get("key")).strip().lower() in UNCLASSIFIED]
    charted = [r["service_name"] for r in rows]
    return charted, app_top, port_top, unclassified_labels


def service_histogram_aggs(interval_str: str, app_top: list[str], port_top: list, unclassified_labels: list[str]) -> dict:
    """Per-bucket date_histogram with by_app + by_port(scoped to unclassified) sub-aggs."""
    inner: dict = {}
    if app_top:
        inner["by_app"] = {
            "terms": {"field": "flow.application.name", "include": app_top, "size": len(app_top)},
            "aggs": _bytes_sum(),
        }
    if port_top and unclassified_labels:
        inner["by_port"] = {
            "filter": {"terms": {"flow.application.name": unclassified_labels}},
            "aggs": {"ports": {
                "terms": {"field": "flow.server.l4.port.id", "include": port_top, "size": len(port_top)},
                "aggs": _bytes_sum(),
            }},
        }
    # min_doc_count=1 (matches traffic_flow): default 0 makes the date_histogram emit empty
    # buckets that can extend well before gte (observed ~90min of spurious empty pre-window
    # buckets), inflating bucket count and the chart x-axis. Real in-window slots a long flow
    # is active in are re-created by spread_long_sessions' setdefault, so the leading-edge fill
    # is unaffected.
    return {"date_histogram": {"field": "@timestamp", "fixed_interval": interval_str, "min_doc_count": 1}, "aggs": inner}


def collapse_chart_bucket(bucket: dict) -> dict[str, float]:
    """Reduce one service_histogram_aggs() date bucket to {resolved_service: bytes}."""
    svc: dict[str, float] = {}
    for b in bucket.get("by_app", {}).get("buckets", []):
        v = _bytes3(b)[0]
        if v > 0:
            svc[str(b["key"])] = svc.get(str(b["key"]), 0.0) + float(v)
    for pb in bucket.get("by_port", {}).get("ports", {}).get("buckets", []):
        v = _bytes3(pb)[0]
        if v > 0:
            name = _port_to_service(pb["key"])
            svc[name] = svc.get(name, 0.0) + float(v)
    return svc
