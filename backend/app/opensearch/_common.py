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


def _time_range(gte_ms: int, lte_ms: int) -> dict:
    return {"range": {"@timestamp": {"gte": gte_ms, "lte": lte_ms, "format": "epoch_millis"}}}


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

    Interface filters are free text, and the two surfaces show different values for
    the same interface: the breakdown panels aggregate `flow.in.netif.name`
    ("wan1") while the raw-data table displays `flow.in.netif.alias`
    ("WAN-LinkNet"). Whichever a user copies into the shared filter bar must work,
    so match both rather than forcing one convention.
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
