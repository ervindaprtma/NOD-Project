"""Port → service name resolution.

Stdlib `socket.getservbyport` covers IANA-registered services. For unregistered
ports the calling code falls back to a string label. Extra aliases (MongoDB,
Steam, Plex, DB2, etc.) are kept for display only — kept inline because the
fallback list is short and the names matter in the UI.
"""
from __future__ import annotations

import socket

# Aliases take precedence over socket.getservbyport() below. Two jobs:
#   1. Override IANA /etc/services names that are technically correct but MISLEAD a
#      NOC operator (e.g. 53 "domain" reads as a website, not DNS; 3389 "ms-wbt-server"
#      is RDP). We render the name the operator expects, not the registry mnemonic.
#   2. Fill common ports the stdlib doesn't register (Oracle, VNC, our own OpenSearch…).
# This only ever affects UNCLASSIFIED (app-0) flows re-expanded by port; a flow that
# FortiGate's AppID already named keeps that name verbatim (see _common.resolve_service).
_ALIAS: dict[int, str] = {
    # ── Clearer names for misleading IANA mnemonics ──
    53: "DNS",              # was "domain"
    67: "DHCP-Server",      # was "bootps"
    68: "DHCP-Client",      # was "bootpc"
    111: "RPC-Portmapper",  # was "sunrpc"
    135: "MS-RPC",          # was "epmap"
    143: "IMAP",            # was "imap2"
    445: "SMB",             # was "microsoft-ds"
    465: "SMTPS",           # was "submissions"
    514: "Syslog",          # was "shell" (TCP 514 is rsh, but on a monitored network 514 is ~always syslog)
    520: "RIP",             # was "route"
    587: "SMTP-Submission",  # was "submission"
    3389: "RDP",            # was "ms-wbt-server"
    # ── Unregistered / gap fills (stdlib returns nothing → would show "Port-N") ──
    1521: "Oracle",
    1723: "PPTP",
    3128: "HTTP-Proxy",
    5900: "VNC",
    8443: "HTTPS-Alt",
    9200: "OpenSearch",
    9300: "OpenSearch-Transport",
    11211: "Memcached",
    27015: "Steam-Server",
    27017: "MongoDB",
    32400: "Plex",
    50000: "DB2",
}


def port_to_service(port: int) -> str:
    """Return a human-readable service name for a port number.

    ponytail: aliases first (misleading-name overrides + gap fills), then IANA
    via stdlib. Expand _ALIAS if a new unregistered/misleading service shows up.
    """
    if port in _ALIAS:
        return _ALIAS[port]
    try:
        return socket.getservbyport(port)
    except (OSError, OverflowError):
        return f"Port-{port}"
