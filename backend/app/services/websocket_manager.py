"""
WebSocket manager for real-time alert push (FR-10).
Handles JWT-authenticated connections and broadcasting of alert state changes.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


class AlertWebSocketManager:
    """
    Manages authenticated WebSocket connections.
    Broadcasts alert state transitions to all connected clients.
    """

    def __init__(self) -> None:
        self._connections: dict[str, list[WebSocket]] = {}  # user_id -> websockets
        self._max_per_user: int = settings.RATE_LIMIT_WEBSOCKET_CONNECTIONS

    async def connect(self, ws: WebSocket, user_id: str) -> bool:
        """Accept new connection if under per-user limit. Returns False if rejected."""
        conns = self._connections.setdefault(user_id, [])
        if len(conns) >= self._max_per_user:
            try:
                await ws.close(code=4001, reason=f"Max {self._max_per_user} WebSocket connections exceeded")
            except Exception:
                pass
            logger.warning("WebSocket rejected — per-user limit reached", extra={"user_id": user_id, "connections": len(conns)})
            return False
        conns.append(ws)
        logger.info("WebSocket connected", extra={"user_id": user_id, "total_connections": sum(len(v) for v in self._connections.values())})
        return True

    async def disconnect(self, user_id: str, ws: WebSocket | None = None) -> None:
        if ws is not None:
            conns = self._connections.get(user_id, [])
            if ws in conns:
                conns.remove(ws)
            if not conns:
                self._connections.pop(user_id, None)
        else:
            self._connections.pop(user_id, None)
        logger.info("WebSocket disconnected", extra={"user_id": user_id, "total_connections": sum(len(v) for v in self._connections.values())})

    async def broadcast(self, message: dict[str, Any], user_id: str | None = None) -> None:
        """
        Send message to all connected clients, or to a specific user if user_id is provided.

        Args:
            message: JSON-serializable message dict
            user_id: If set, only send to this specific user
        """
        payload = json.dumps(message)
        dead: list[tuple[str, WebSocket]] = []

        if user_id and user_id in self._connections:
            targets: list[tuple[str, WebSocket]] = [
                (user_id, ws) for ws in self._connections[user_id]
            ]
        else:
            targets = [
                (uid, ws) for uid, conns in self._connections.items() for ws in conns
            ]

        for uid, ws in targets:
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append((uid, ws))

        for uid, ws in dead:
            await self.disconnect(uid, ws)

    @property
    def active_connections(self) -> int:
        return sum(len(v) for v in self._connections.values())

    def is_connected(self, user_id: str) -> bool:
        return user_id in self._connections and len(self._connections[user_id]) > 0


# Singleton instance
alert_ws_manager = AlertWebSocketManager()
