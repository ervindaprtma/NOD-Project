"""
SSE (Server-Sent Events) alert delivery (v3 §3.10).
Replaces WebSocket-based alert push.

One in-memory asyncio.Queue per connected client via ConnectionRegistry.
alert_engine.py broadcasts via sse_broadcast() instead of alert_ws_manager.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timezone
from typing import Any

from fastapi import Request

logger = logging.getLogger(__name__)

# ── Connection registry (replaces AlertWebSocketManager) ─────────


class ConnectionRegistry:
    """In-memory per-client fan-out via asyncio.Queue.

    Each SSE client gets one queue.  broadcast() enqueues a JSON event
    to every registered queue.  The SSE endpoint generator drains its queue
    and yields formatted SSE text.
    """

    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[str]] = {}
        self._reconnect_count: int = 0  # approximates reconnect volume

    def register(self, client_id: str) -> asyncio.Queue[str]:
        """Create a new queue for a client (replaces old one if stale)."""
        self._queues[client_id] = asyncio.Queue()
        return self._queues[client_id]

    def unregister(self, client_id: str) -> None:
        self._queues.pop(client_id, None)

    async def broadcast(
        self,
        event_type: str,
        data: dict[str, Any],
        event_id: str | int | None = None,
    ) -> None:
        """Push event to every connected client."""
        payload = json.dumps(data, default=str)
        lines = []
        if event_id is not None:
            lines.append(f"id: {event_id}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {payload}\n")
        text = "\n".join(lines)

        dead: list[str] = []
        for cid, q in self._queues.items():
            try:
                q.put_nowait(text)
            except asyncio.QueueFull:
                dead.append(cid)

        for cid in dead:
            logger.warning("SSE client %s queue full — dropping", cid)
            self.unregister(cid)

    @property
    def clients_connected(self) -> int:
        return len(self._queues)

    @property
    def reconnects_last_24h(self) -> int:
        return self._reconnect_count


# Singleton — same pattern as alert_ws_manager
sse_registry = ConnectionRegistry()


# ── SSE generator ───────────────────────────────────────────────


async def sse_event_stream(
    request: Request,
    client_id: str,
) -> AsyncGenerator[str, None]:
    """Async generator yielding SSE-formatted text for one client.

    Sends a keep-alive comment every 20s.  Drains the client's queue
    and yields each event.  Stops when the client disconnects.
    """
    queue = sse_registry.register(client_id)
    logger.info("SSE client connected: %s (total: %s)", client_id, sse_registry.clients_connected)

    try:
        # Always start with a heartbeat so the client knows the stream is open
        yield ": connected\n\n"

        while True:
            try:
                # Wait for either a queue item or 20s timeout
                text = await asyncio.wait_for(queue.get(), timeout=20.0)
                yield text
            except asyncio.TimeoutError:
                # Keep-alive comment — invisible to EventSource listeners
                yield ": keep-alive\n\n"
                # Check if client disconnected
                if await request.is_disconnected():
                    logger.info("SSE client disconnected (detected): %s", client_id)
                    break
    except asyncio.CancelledError:
        logger.debug("SSE generator cancelled for %s", client_id)
    finally:
        sse_registry.unregister(client_id)
        logger.info("SSE client cleaned up: %s (total: %s)", client_id, sse_registry.clients_connected)


# ── Convenience wrapper for alert_engine.py calls ───────────────


async def sse_broadcast(
    event_type: str,
    rule_id: str | None = None,
    rule_name: str | None = None,
    severity: str | None = None,
    metric_value: float | None = None,
    fired_at: str | None = None,
    resolved_at: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Broadcast a structured alert event to all SSE clients.

    Payload shape matches the existing WebSocket payload (v3 §3.10.2)
    so the frontend can reuse the same consumption patterns.
    """
    data: dict[str, Any] = {
        "rule_id": rule_id,
        "rule_name": rule_name,
        "severity": severity,
    }
    if event_type == "alert":
        data["type"] = "firing"
        data["metric_value"] = metric_value
        data["fired_at"] = fired_at or datetime.now(timezone.utc).isoformat()
    elif event_type == "resolved":
        data["type"] = "resolved"
        data["resolved_at"] = resolved_at or datetime.now(timezone.utc).isoformat()

    if extra:
        data.update(extra)

    await sse_registry.broadcast(
        event_type=event_type,
        data=data,
        event_id=None,  # SSE id will be set from AlertLog.id at call site later
    )
