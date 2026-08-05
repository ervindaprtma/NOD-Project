"""
Shared API helpers — safe query wrapper for OpenSearch-backed endpoints.

All traffic endpoints should use _safe_query() to prevent 500 errors
when OpenSearch times out or has connection issues. Returns empty
result on error so frontend can show "No data" gracefully.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence, Tuple

from fastapi import Request

from app.schemas.common import Meta

logger = logging.getLogger("nod.api")


def pack_excludes(request: Request, rename: Optional[dict] = None) -> dict:
    """Collect the `*_not` exclude-filter query params into a dict the query builders take as
    `exclude=` (forwarded to _base_filters as `**exclude`). Empty values are dropped so an
    exclude-free request yields {} → a query byte-identical to before. `dst_port_not` is
    coerced to int; `rename` maps a wire name to a builder param (internal: app_filter_not →
    service_filter_not). Keys are otherwise the builder's own `_not` param names, so new
    filters need no change here."""
    ex: dict = {k: v for k, v in request.query_params.items() if k.endswith("_not") and v}
    if "dst_port_not" in ex:
        # Accept one or several ports ("445" or "445,3389") — parse to a list of ints so a
        # multi-port exclude isn't silently dropped (int("445,3389") would raise). Drop only
        # the non-numeric tokens; keep the key out entirely if nothing valid remains.
        ports = [int(p) for p in str(ex["dst_port_not"]).split(",") if p.strip().isdigit()]
        if ports:
            ex["dst_port_not"] = ports
        else:
            ex.pop("dst_port_not")
    for src, dst in (rename or {}).items():
        if src in ex:
            ex[dst] = ex.pop(src)
    return ex


def build_meta(
    elapsed_ms: int,
    degraded: Sequence[str] = (),
    err: Optional[str] = None,
    **kwargs,
) -> Meta:
    """Build a Meta, flagging the response as degraded if anything went wrong.

    `degraded` is the sink from query.track_degradation() — populated when an
    underlying OpenSearch query timed out, tripped the circuit breaker, or returned
    partial results. `err` covers the case where the query function itself raised.
    Either way the numbers in `data` are incomplete or zeroed, and the UI needs to
    know that a 0 here is "unknown", not "no traffic".
    """
    reasons = list(degraded)
    if err:
        reasons.append(f"query_failed: {str(err)[:120]}")
    return Meta(
        query_took_ms=elapsed_ms,
        degraded=True if reasons else None,
        partial_errors=reasons[:5] or None,
        **kwargs,
    )


async def safe_query(
    fn,
    fn_name: str = "",
    **kwargs,
) -> Tuple[Optional[Any], Optional[str]]:
    """
    Run an async OpenSearch query function with error handling.

    Args:
        fn: async function returning dict
        fn_name: name for logging
        **kwargs: passed to fn

    Returns:
        (data_dict, error_string)
        - On success: (data_dict, None)
        - On error: (None, error_string)
    """
    label = fn_name or getattr(fn, "__name__", "query")
    try:
        result = await fn(**kwargs)
        # Accept any non-None return type (dict, list, int, str, etc.)
        if result is None:
            return None, "null_response"
        return result, None
    except Exception as e:
        logger.error(f"{label} failed: {type(e).__name__}: {e}")
        return None, str(e)
