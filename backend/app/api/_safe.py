"""
Shared API helpers — safe query wrapper for OpenSearch-backed endpoints.

All traffic endpoints should use _safe_query() to prevent 500 errors
when OpenSearch times out or has connection issues. Returns empty
result on error so frontend can show "No data" gracefully.
"""
from __future__ import annotations

import logging
from typing import Any, Optional, Sequence, Tuple

from app.schemas.common import Meta

logger = logging.getLogger("nod.api")


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
