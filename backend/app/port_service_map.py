"""Port → service name resolution.

Stdlib `socket.getservbyport` covers IANA-registered services. For unregistered
ports the calling code falls back to a string label. Extra aliases (MongoDB,
Steam, Plex, DB2, etc.) are kept for display only — kept inline because the
fallback list is short and the names matter in the UI.
"""
from __future__ import annotations

import socket

# ponytail: short alias list for ports the stdlib doesn't recognize.
_ALIAS: dict[int, str] = {
    27015: "Steam-Server",
    27017: "MongoDB",
    32400: "Plex",
    50000: "DB2",
}


def port_to_service(port: int) -> str:
    """Return a human-readable service name for a port number.

    ponytail: IANA-registered ports use stdlib; the alias dict fills the
    gaps. Expand _ALIAS if a new unregistered service shows up in the UI.
    """
    if port in _ALIAS:
        return _ALIAS[port]
    try:
        return socket.getservbyport(port)
    except (OSError, OverflowError):
        return f"Port-{port}"
