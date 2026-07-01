"""
Shared API helpers — safe query wrapper for OpenSearch-backed endpoints.

All traffic endpoints should use _safe_query() to prevent 500 errors
when OpenSearch times out or has connection issues. Returns empty
result on error so frontend can show "No data" gracefully.
"""
from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable, Optional, Tuple

logger = logging.getLogger("nod.api")


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
