# Field Reference — FortiGate App ID Parser v4.5-upstream

> **Parser**: `parser_appid_v2.py`  
> **Indices**: `fortigate-appid-flow-YYYY.MM.DD` / `fortigate-appid-map`  
> **Merge target**: `elastiflow-flow-codex-*` (via `update_by_query`)

> **No-null policy** (v4.5): User identity fields are only written when a value exists.  
> When an IP is NOT found in `userid_hostname.yml`, `flow.client.host.name` / `flow.server.host.name`  
> fall back to the IP address itself — all other enrichment fields (`os`, `role`, `zone`, `tags`, etc.)  
> are **omitted from the document** (no `null` keys).  
> Netif enrichment follows the same pattern: when interface data is not found, only `netif.index` is written.  
> Upstream correlation fields (`flow.customer.ip.addr`, `flow.vip.ip.addr`) are likewise only written when set.

---

## 1. Field Index

### 1.1 Flow Identity (6 merge-key fields)

| Field | Type | Description |
|---|---|---|
| `flow.src.ip.addr` | `ip` | Source IP address (raw, before client/server resolution) |
| `flow.dst.ip.addr` | `ip` | Destination IP address (raw, before client/server resolution) |
| `flow.dst.l4.port.id` | `integer` | Destination L4 port |
| `l4.proto.name` | `keyword` | Protocol name (`TCP`, `UDP`, `ICMP`, `GRE`, `ESP`, `ICMPv6`, `SCTP`) |
| `flow.src.as.asn` | `integer` | Source ASN (from IPFIX/NetFlow exporter) |
| `flow.dst.as.asn` | `integer` | Destination ASN (from IPFIX/NetFlow exporter) |

---

### 1.2 Client / Server Resolution (v4.4)

| Field | Type | Description |
|---|---|---|
| `flow.client.ip.addr` | `ip` | **Resolved client IP** — who initiated |
| `flow.server.ip.addr` | `ip` | **Resolved server IP** — who responded |
| `flow.client.l4.port.id` | `integer` | Client-side port |
| `flow.server.l4.port.id` | `integer` | Server-side port |
| `flow.client.ip.nat.addr` | `ip` | Client-side NAT address |
| `flow.server.ip.nat.addr` | `ip` | Server-side NAT address |
| `flow.client.bytes` | `long` | Bytes client→server |
| `flow.server.bytes` | `long` | Bytes server→client |
| `flow.traffic.direction` | `keyword` | `c2s` (client→server) / `s2c` (server→client) / `unknown` |
| `flow.traffic.path` | `keyword` | `internet` / `inbound-vip` / `inter-site` / `intra-lan` / `unknown` |
| `flow.client.site` | `keyword` | Client site name (from `SITE_MAP` env var) |
| `flow.server.site` | `keyword` | Server site name (from `SITE_MAP` env var) |

---

### 1.3 Application Identification

| Field | Type | Description |
|---|---|---|
| `flow.application.id` | `integer` | FortiGate Application ID |
| `flow.application.name` | `keyword` | Resolved application name (e.g., `HTTPS.BROWSER`, `YouTube`, `SSH`) |
| `flow.application.category` | `keyword` | Application category (e.g., `Web.Client`, `Video/Audio`, `Remote.Access`) |
| `flow.application.risk` | `keyword` | Risk level (`Low` / `Medium` / `High` / `Critical` / `Elevated`) |
| `flow.application.engine_id` | `integer` | IANA application engine ID |

---

### 1.4 Raw IPFIX / NetFlow Fields

| Field | Type | Description |
|---|---|---|
| `flow.src.l4.port.id` | `integer` | Source L4 port |
| `src.nat.ip` | `ip` | Source NAT IP (raw from IPFIX field 225/281) |
| `src.nat.port` | `integer` | Source NAT port (raw from IPFIX field 227) |
| `dst.nat.ip` | `ip` | **Destination NAT IP** (raw from IPFIX field 226/282) — FortiGate DNAT target |
| `dst.nat.port` | `integer` | Destination NAT port (raw from IPFIX field 228) |
| `flow.bytes` | `long` | Total flow bytes |
| `flow.bytes_human` | `keyword` | Human-readable bytes (e.g., `1.25 GB`) |
| `flow.packets` | `long` | Total flow packets |
| `flow.packets_human` | `keyword` | Human-readable packets (e.g., `2.5M`) |
| `flow.start.ms` | `long` | Flow start timestamp (epoch milliseconds) |
| `flow.end.ms` | `long` | Flow end timestamp (epoch milliseconds) |
| `user.name` | `keyword` | FortiGate username (if user-authenticated) |

---

### 1.5 GeoIP Enrichment (MaxMind)

| Field | Type | Description |
|---|---|---|
| `flow.src.as.number` | `integer` | Source ASN (MaxMind ASN DB) |
| `flow.src.as.org` | `keyword` | Source AS organization name |
| `flow.src.as.country` | `keyword` | Source country |
| `flow.dst.as.number` | `integer` | Destination ASN (MaxMind ASN DB) |
| `flow.dst.as.org` | `keyword` | Destination AS organization name |
| `flow.dst.as.country` | `keyword` | Destination country |
| `flow.locality` | `keyword` | `isPrivate` (internal destination) / `isPublic` (external destination) |

---

### 1.6 User Identity & Hostname (v4.5-userid)

Source: `userid_hostname.yml` — subnet/range/IP → tags + metadata lookup.

> **Fallback behavior**: When an IP is NOT found in the YAML, `host.name` is set to the IP address  
> itself (e.g. `"203.0.113.50"`). All other fields (`os`, `role.user`, `role.asset`, `owner`, `zone`,  
> `env`, `criticality`, `network.name`, `tags`) are **omitted** — no `null` values written.

| Client Field | Server Field | Type | Description |
|---|---|---|---|
| `flow.client.host.name` | `flow.server.host.name` | `keyword` | **Hostname / FQDN** of the endpoint (falls back to IP if not found) |
| `flow.client.os` | `flow.server.os` | `keyword` | Operating system (only when found in YAML) |
| `flow.client.role.user` | `flow.server.role.user` | `keyword` | User role(s), comma-separated (only when found) |
| `flow.client.role.asset` | `flow.server.role.asset` | `keyword` | Asset role(s), comma-separated (only when found) |
| `flow.client.owner` | `flow.server.owner` | `keyword` | Responsible owner (only when found) |
| `flow.client.zone` | `flow.server.zone` | `keyword` | Network zone (`officehq-lt`, `dc-gtn`, `drc-la`, `remote`) |
| `flow.client.env` | `flow.server.env` | `keyword` | Environment tag (`production`, `staging`, `dmz`, etc.) |
| `flow.client.criticality` | `flow.server.criticality` | `keyword` | Criticality (`low` / `medium` / `high`) |
| `flow.client.network.name` | `flow.server.network.name` | `keyword` | Subnet-level network name |
| `flow.client.tags` | `flow.server.tags` | `keyword` | Tags (from YAML) |

---

### 1.7 Upstream / VIP Correlation (v4.5-upstream)

Auto-stamped from FortiGate DNAT. Links inbound customer flows to downstream internal flows.
These fields are **only written when correlation data exists** — absent otherwise (no nulls).

| Field | Type | Description |
|---|---|---|
| `flow.customer.ip.addr` | `ip` | **Original customer public IP** — stamped on all downstream hops |
| `flow.vip.ip.addr` | `ip` | **VIP address** the customer connected through — stamped on all downstream hops |

---

### 1.8 Interface Enrichment (from `netifs.yml`)

> **No-null policy**: When interface data is NOT found in `netifs.yml`, only `netif.index` (the SNMP  
> interface number) is written. All other fields (`name`, `descr`, `alias`, `tags`, `type.name`,  
> `sec.zone.name`, `metadata.*`) are **omitted**. When `iface_id` is `None`, no netif fields are written at all.

Dynamic fields via `flow.{in,out}.netif.*`:

| Field | Type | Description |
|---|---|---|
| `flow.in.netif.index` | `integer` | Ingress interface SNMP index |
| `flow.in.netif.name` | `keyword` | Interface name (`ifName`) |
| `flow.in.netif.descr` | `keyword` | Interface description |
| `flow.in.netif.alias` | `keyword` | Interface alias |
| `flow.in.netif.tags` | `keyword` | Interface tags |
| `flow.in.netif.type.name` | `keyword` | Interface type name |
| `flow.in.netif.sec.zone.name` | `keyword` | Security zone name (ingress) |
| `flow.in.netif.metadata.*` | `keyword` | Additional interface metadata |
| `flow.out.netif.*` | — | Same fields for egress interface |

---

### 1.9 Correlation & Meta

| Field | Type | Description |
|---|---|---|
| `flow.correlation_id` | `keyword` | SHA-256 correlation ID (16 bytes, base64url) |
| `flow.correlation_direction` | `keyword` | `initiator` / `responder` |
| `flow.correlation_bucket` | `date` | 5-minute time bucket for temporal join |
| `flow.connection_id` | `keyword` | Deterministic connection ID per IP pair |
| `flow.export.ip.addr` | `ip` | FortiGate exporter IP address |
| `timestamp_export` | `date` | Packet export timestamp |
| `@timestamp` | `date` | Document ingestion timestamp |
| `flow.appid.enriched_at` | `date` | Enrichment timestamp |
| `flow.appid.source` | `keyword` | `fortigate-parser-v4.4` |
| `_merge_key` | `keyword` | SHA-1 merge key (dedup, not indexed in `_source`) |

---

### 1.10 Index Mappings Reference (`fortigate-appid-map`)

| Field | Type | Description |
|---|---|---|
| `app_id` | `integer` | FortiGate Application ID |
| `app_name` | `keyword` | Application name |
| `app_category` | `keyword` | Application category |
| `app_risk` | `keyword` | Risk level |
| `app_desc` | `text` | Description (not indexed) |
| `first_seen` | `date` | First time seen |
| `last_updated` | `date` | Last update time |
| `source` | `keyword` | `csv` / `fortiguard` |

---

## 2. Complete Scenario Examples

### Scenario A: VPN User → Production Backend

```
VPN user ervinda.pratama connects via SSL-VPN and accesses backend app.
```

| Phase | Flow |
|---|---|
| Client | `10.80.148.231` (VPN pool IP) |
| Server | `10.80.100.31:443/TCP` (production backend) |
| Path | `inter-site` |

```json
{
  "flow.src.ip.addr":             "10.80.148.231",
  "flow.dst.ip.addr":             "10.80.100.31",
  "flow.dst.l4.port.id":          443,
  "l4.proto.name":                "TCP",
  "flow.src.as.asn":              0,
  "flow.dst.as.asn":              0,

  "flow.client.ip.addr":          "10.80.148.231",
  "flow.server.ip.addr":          "10.80.100.31",
  "flow.client.l4.port.id":       52134,
  "flow.server.l4.port.id":       443,
  "flow.client.ip.nat.addr":      null,
  "flow.server.ip.nat.addr":      null,
  "flow.client.bytes":            24500000,
  "flow.server.bytes":            1800000000,
  "flow.traffic.direction":       "c2s",
  "flow.traffic.path":            "inter-site",
  "flow.client.site":             null,
  "flow.server.site":             null,

  "flow.application.id":           40568,
  "flow.application.name":        "HTTPS.BROWSER",
  "flow.application.category":    "Web.Client",
  "flow.application.risk":        "Low",
  "flow.application.engine_id":   null,

  "flow.src.l4.port.id":          52134,
  "flow.bytes":                    1824500000,
  "flow.bytes_human":             "1.82 GB",
  "flow.packets":                  450000,
  "flow.packets_human":           "450K",
  "flow.start.ms":                 1781059100000,
  "flow.end.ms":                   1781059700000,
  "user.name":                     "ervinda.pratama",

  "flow.src.as.number":            0,
  "flow.src.as.org":               "Private",
  "flow.src.as.country":           "None",
  "flow.dst.as.number":            0,
  "flow.dst.as.org":               "Private",
  "flow.dst.as.country":           "None",
  "flow.locality":                 "isPrivate",

  "flow.client.host.name":        "vpn-ervinda.pratama",
  "flow.client.role.user":        "network-team",
  "flow.client.owner":            "ervinda.pratama",
  "flow.client.zone":             "remote",
  "flow.client.env":              "vpn-remote",
  "flow.client.criticality":      "low",
  "flow.client.tags":             ["vpn-user", "vpn-ssl"],

  "flow.server.host.name":        "srv-app-backend",
  "flow.server.os":               "almalinux-9-2",
  "flow.server.role.user":        "sysadmin-team, developer-team",
  "flow.server.role.asset":       "backend-prod",
  "flow.server.owner":            "infra-team",
  "flow.server.zone":             "dc-gtn",
  "flow.server.env":              "application-area",
  "flow.server.tags":             ["backend", "production", "application"],

  "flow.correlation_id":          "AbCdEf1234567890",
  "flow.correlation_direction":   "initiator",
  "flow.correlation_bucket":      1781058900000,
  "flow.connection_id":           "XyZ9876543210Fed",
  "flow.export.ip.addr":          "10.80.1.1",
  "timestamp_export":             1781059120000,
  "@timestamp":                   "2026-06-10T02:38:40.000Z",
  "flow.appid.enriched_at":       "2026-06-10T02:38:40.000Z",
  "flow.appid.source":            "fortigate-parser-v4.4"
}
```

---

### Scenario B: Customer → VIP → Nginx → Frontend + Backend (Full Chain)

```
Customer 103.200.24.25 accesses app.ezsign.id → VIP 109.200.45.60 →
FortiGate DNAT to nginx 10.80.110.111 → proxy_pass to frontend/backend.
```

**Document 1 — Inbound VIP** (`flow.traffic.path: "inbound-vip"`)

```json
{
  "flow.src.ip.addr":             "103.200.24.25",
  "flow.dst.ip.addr":             "109.200.45.60",
  "flow.dst.l4.port.id":          443,
  "l4.proto.name":                "TCP",

  "flow.client.ip.addr":          "103.200.24.25",
  "flow.server.ip.addr":          "109.200.45.60",
  "flow.client.l4.port.id":       61234,
  "flow.server.l4.port.id":       443,
  "flow.client.bytes":            12000,
  "flow.server.bytes":            156000000,
  "flow.traffic.direction":       "c2s",
  "flow.traffic.path":            "inbound-vip",

  "dst.nat.ip":                   "10.80.110.111",
  "dst.nat.port":                 443,

  "flow.application.name":        "HTTPS.BROWSER",
  "flow.application.category":    "Web.Client",

  "flow.src.as.org":              "PT Telkom Indonesia",
  "flow.src.as.country":          "Indonesia",
  "flow.locality":                "isPublic",

  "flow.client.host.name":        "103.200.24.25",
  "flow.server.host.name":        "109.200.45.60",

  "flow.correlation_id":          "7xBoue_7XBwO3RLQObwV",
  "flow.correlation_bucket":      1781058900000,
  "flow.correlation_direction":   "initiator",
  "flow.connection_id":           "7xBoue_7XBwO3RLQObwV",
  "flow.export.ip.addr":          "10.80.1.1"
}
```

**Document 2 — Nginx → Frontend** (`flow.traffic.path: "intra-lan"`)

```json
{
  "flow.src.ip.addr":             "10.80.110.111",
  "flow.dst.ip.addr":             "10.80.100.32",
  "flow.dst.l4.port.id":          8080,
  "l4.proto.name":                "TCP",

  "flow.client.ip.addr":          "10.80.110.111",
  "flow.server.ip.addr":          "10.80.100.32",
  "flow.client.l4.port.id":       44321,
  "flow.server.l4.port.id":       8080,
  "flow.client.bytes":            148000000,
  "flow.server.bytes":            800000,
  "flow.traffic.direction":       "c2s",
  "flow.traffic.path":            "intra-lan",

  "flow.client.host.name":        "srv-nginx-external",
  "flow.client.os":               "almalinux-9-4",
  "flow.client.role.user":        "office-team",
  "flow.client.role.asset":       "proxy-server",
  "flow.client.owner":            "infra-team",
  "flow.client.zone":             "dc-gtn",
  "flow.client.env":              "dmz-area",
  "flow.client.criticality":      "high",
  "flow.client.tags":             ["proxy-server", "nginx", "dmz"],

  "flow.server.host.name":        "srv-app-frontend",
  "flow.server.os":               "almalinux-9-4",
  "flow.server.role.user":        "sysadmin-team, developer-team",
  "flow.server.role.asset":       "frontend-prod",
  "flow.server.owner":            "infra-team",
  "flow.server.zone":             "drc-la",
  "flow.server.env":              "application-area",
  "flow.server.criticality":      "high",
  "flow.server.tags":             ["frontend", "production", "application"],

  "flow.customer.ip.addr":        "103.200.24.25",
  "flow.vip.ip.addr":             "109.200.45.60",

  "flow.correlation_bucket":      1781058900000,
  "flow.correlation_direction":   "initiator",
  "flow.connection_id":           "lZiBuXmVfaweClIiYHj1",
  "flow.export.ip.addr":          "10.80.1.1"
}
```

**Document 3 — Nginx → Backend** (`flow.traffic.path: "intra-lan"`)

```json
{
  "flow.src.ip.addr":             "10.80.110.111",
  "flow.dst.ip.addr":             "10.80.100.31",
  "flow.dst.l4.port.id":          5432,
  "l4.proto.name":                "TCP",

  "flow.client.ip.addr":          "10.80.110.111",
  "flow.server.ip.addr":          "10.80.100.31",
  "flow.client.bytes":            95000000,
  "flow.server.bytes":            500000,
  "flow.traffic.direction":       "c2s",
  "flow.traffic.path":            "intra-lan",

  "flow.client.host.name":        "srv-nginx-external",
  "flow.client.tags":             ["proxy-server", "nginx", "dmz"],

  "flow.server.host.name":        "srv-app-backend",
  "flow.server.os":               "almalinux-9-2",
  "flow.server.role.asset":       "backend-prod",
  "flow.server.tags":             ["backend", "production", "application"],

  "flow.customer.ip.addr":        "103.200.24.25",
  "flow.vip.ip.addr":             "109.200.45.60",

  "flow.application.name":        "PostgreSQL",
  "flow.application.category":    "Business",

  "flow.correlation_bucket":      1781058900000,
  "flow.correlation_direction":   "initiator",
  "flow.connection_id":           "mNkOpQrStUvWxYzAbCd",
  "flow.export.ip.addr":          "10.80.1.1"
}
```

---

### Scenario C: Office User → Internet (YouTube)

```
Office user rheno.sulistyo watches YouTube from workstation.
```

| Phase | Flow |
|---|---|
| Client | `192.168.100.68` (office workstation) |
| Server | `142.250.185.78:443` (YouTube) |
| Path | `internet` |

```json
{
  "flow.src.ip.addr":             "192.168.100.68",
  "flow.dst.ip.addr":             "142.250.185.78",
  "flow.dst.l4.port.id":          443,
  "l4.proto.name":                "TCP",
  "flow.traffic.path":            "internet",

  "flow.application.name":        "YouTube",
  "flow.application.category":    "Video/Audio",

  "flow.client.host.name":        "vm-rhenosulistyo-sigbeinfl002",
  "flow.client.os":               "ubuntu-linux-desktop",
  "flow.client.role.user":        "sysadmin-team",
  "flow.client.owner":            "rheno.sulistyo",
  "flow.client.zone":             "officehq-lt",
  "flow.client.env":              "infrastructure",
  "flow.client.tags":             ["rheno.sulistyo", "vm-rheno", "sysadmin-team", "subnet-office"],

  "flow.server.host.name":        "142.250.185.78",
  "flow.src.as.org":              "PT Network Provider",
  "flow.dst.as.org":              "Google LLC",
  "flow.dst.as.country":          "United States"
}
```

---

### Scenario D: Backend → Database (Internal)

```
Production backend queries staging PostgreSQL database.
```

| Phase | Flow |
|---|---|
| Client | `10.80.100.31` (app backend) |
| Server | `10.80.180.27:5432` (staging PostgreSQL) |
| Path | `intra-lan` |

```json
{
  "flow.src.ip.addr":             "10.80.100.31",
  "flow.dst.ip.addr":             "10.80.180.27",
  "flow.dst.l4.port.id":          5432,
  "l4.proto.name":                "TCP",
  "flow.traffic.path":            "intra-lan",

  "flow.application.name":        "PostgreSQL",
  "flow.application.category":    "Business",

  "flow.client.host.name":        "srv-app-backend",
  "flow.client.role.asset":       "backend-prod",
  "flow.client.zone":             "dc-gtn",
  "flow.client.env":              "application-area",

  "flow.server.host.name":        "srv-stg-postgre-master",
  "flow.server.os":               "almalinux-9-2",
  "flow.server.role.user":        "sysadmin-team, dba-team, developer-team",
  "flow.server.role.asset":       "postgresql-server",
  "flow.server.zone":             "dc-gtn",
  "flow.server.env":              "stagging-area",
  "flow.server.criticality":      "high",
  "flow.server.tags":             ["postgre-stagging-2", "linux-server", "staging"]
}
```

---

### Scenario E: Internet → Inbound VIP (DMZ Web Server)

```
External customer from Indonesia hits public-facing nginx proxy.
```

| Phase | Flow |
|---|---|
| Client | `203.0.113.50` (customer ISP) |
| Server | `10.80.110.111:443` (DMZ nginx) |
| Path | `inbound-vip` |

```json
{
  "flow.src.ip.addr":             "203.0.113.50",
  "flow.dst.ip.addr":             "109.200.45.60",
  "flow.dst.l4.port.id":          443,
  "l4.proto.name":                "TCP",
  "flow.traffic.path":            "inbound-vip",

  "dst.nat.ip":                   "10.80.110.111",

  "flow.application.name":        "HTTPS.BROWSER",
  "flow.application.category":    "Web.Client",

  "flow.src.as.org":              "Some ISP",
  "flow.src.as.country":          "Indonesia",

  "flow.client.host.name":        "203.0.113.50",
  "flow.server.host.name":        "109.200.45.60"
}
```

> ⚠️ In this inbound hop, `flow.server.ip.addr` is the VIP (`109.200.45.60`) — not the nginx.  
> The VIP is NOT in `userid_hostname.yml`, so `flow.server.host.name` falls back to the VIP IP.  
> All other `flow.server.*` fields (`os`, `role`, `zone`, `tags`, etc.) are **omitted**.  
> The real server identity (`srv-nginx-external`) appears on downstream flows (Docs 2-3).
```

---

### Scenario F: Developer → GitLab (DMZ, SSH)

```
Developer lucia.retno pushes code to GitLab server via SSH.
```

| Phase | Flow |
|---|---|
| Client | `192.168.101.113` (developer workstation) |
| Server | `10.80.110.91:22` (GitLab server) |
| Path | `inter-site` |

```json
{
  "flow.src.ip.addr":             "192.168.101.113",
  "flow.dst.ip.addr":             "10.80.110.91",
  "flow.dst.l4.port.id":          22,
  "l4.proto.name":                "TCP",
  "flow.traffic.path":            "inter-site",

  "flow.application.name":        "SSH",
  "flow.application.category":    "Remote.Access",

  "flow.client.host.name":        "ws-luciaretno-jtpcaictw007",
  "flow.client.os":               "windows11-pro",
  "flow.client.role.user":        "developer-team",
  "flow.client.owner":            "lucia.retno",
  "flow.client.zone":             "officehq-lt",
  "flow.client.env":              "developer",
  "flow.client.tags":             ["lucia.retno", "developer-team", "subnet-office"],

  "flow.server.host.name":        "srv-git-server",
  "flow.server.os":               "almalinux-9-3",
  "flow.server.role.user":        "sysadmin-team, developer-team",
  "flow.server.role.asset":       "gitlab-server",
  "flow.server.zone":             "dc-gtn",
  "flow.server.env":              "dmz",
  "flow.server.criticality":      "high",
  "flow.server.tags":             ["gitlab", "linux server", "dmz"]
}
```

---

## 3. Key Queries

### Top customers by bandwidth through a VIP
```json
{
  "query": {"bool": {"filter": [
    {"exists": {"field": "flow.customer.ip.addr"}},
    {"term": {"flow.vip.ip.addr": "109.200.45.60"}}
  ]}},
  "aggs": {
    "by_customer": {
      "terms": {"field": "flow.customer.ip.addr", "size": 20},
      "aggs": {
        "total_mb": {"sum": {"field": "flow.client.bytes"}}
      }
    }
  }
}
```

### Subnet → Internet bandwidth by application
```json
{
  "query": {"bool": {"filter": [
    {"term": {"flow.traffic.path": "internet"}}
  ]}},
  "aggs": {
    "by_subnet": {
      "terms": {"field": "flow.client.network.name", "size": 10},
      "aggs": {
        "by_app": {
          "terms": {"field": "flow.application.name", "size": 5},
          "aggs": {"bytes": {"sum": {"field": "flow.client.bytes"}}}
        }
      }
    }
  }
}
```

### Full chain for one customer
```json
{
  "query": {"term": {"flow.customer.ip.addr": "103.200.24.25"}},
  "sort": [{"@timestamp": "asc"}]
}
```

### Count connections per hostname
```json
{
  "aggs": {
    "by_host": {
      "terms": {"field": "flow.client.host.name", "size": 50},
      "aggs": {
        "by_path": {"terms": {"field": "flow.traffic.path"}},
        "by_app":  {"terms": {"field": "flow.application.name"}}
      }
    }
  }
}
```

---

## 4. Field Population by Scenario

| Field Group | A: VPN→Backend | B: Customer→VIP | C: Office→YouTube | D: Backend→DB | E: Internet→DMZ | F: Dev→GitLab |
|---|---|---|---|---|---|---|
| Flow Identity (6) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Client/Server (10) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| App ID (5) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| Raw IPFIX (12) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| GeoIP (7) | private | external | external | private | external | private |
| UserID Client (10) | 7 filled | 0 (external) | 8 filled | 8 filled | 0 (external) | 8 filled |
| UserID Server (10) | 8 filled | 9 filled | 0 (external) | 9 filled | 9 filled | 9 filled |
| Customer/VIP (2) | 0 | 2 (downstream) | 0 | 0 | 0 | 0 |
| Netif (14+) | varies | varies | varies | varies | varies | varies |
| Correlation (6) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| **Total fields** | ~50+ | ~55+ | ~45+ | ~55+ | ~48+ | ~55+ |
