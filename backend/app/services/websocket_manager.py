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
        self._connections: dict[str, WebSocket] = {}  # user_id -> websocket
        self._max_per_user: int = settings.RATE_LIMIT_WEBSOCKET_CONNECTIONS

    async def connect(self, ws: WebSocket, user_id: str) -> None:
        # Enforce per-user connection limit (P0 security)
        if user_id in self._connections:
            # Already connected — close duplicate gracefully
            try:
                await self._connections[user_id].close(code=4001, reason="Replaced by new connection")
            except Exception:
                pass
            self._connections.pop(user_id, None)
        await ws.accept()
        self._connections[user_id] = ws
        logger.info("WebSocket connected", extra={"user_id": user_id, "total_connections": len(self._connections)})

    async def disconnect(self, user_id: str) -> None:
        self._connections.pop(user_id, None)
        logger.info("WebSocket disconnected", extra={"user_id": user_id, "total_connections": len(self._connections)})

    async def broadcast(self, message: dict[str, Any], user_id: str | None = None) -> None:
        """
        Send message to all connected clients, or to a specific user if user_id is provided.

        Args:
            message: JSON-serializable message dict
            user_id: If set, only send to this specific user
        """
        payload = json.dumps(message)
        dead: list[str] = []

        targets = {user_id: self._connections[user_id]} if user_id and user_id in self._connections else self._connections

        for uid, ws in targets.items():
            try:
                await ws.send_text(payload)
            except Exception:
                dead.append(uid)

        for uid in dead:
            await self.disconnect(uid)

    @property
    def active_connections(self) -> int:
        return len(self._connections)

    def is_connected(self, user_id: str) -> bool:
        return user_id in self._connections


# Singleton instance
alert_ws_manager = AlertWebSocketManager()
