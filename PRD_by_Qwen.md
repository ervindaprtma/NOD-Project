# Network Observability Dashboard (NOD)
## Product Requirements Document

**Version:** 1.1.0  
**Status:** Draft — Pending Engineering Review  
**Effective Date:** June 2026  
**Classification:** Internal — Confidential  
**Document Owner:** Principal Architect

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Glossary](#2-glossary)
3. [System Architecture & Tech Stack](#3-system-architecture--tech-stack)
4. [Data Sources & Index Configuration](#4-data-sources--index-configuration)
5. [Data Dictionary](#5-data-dictionary)
6. [Functional Requirements](#6-functional-requirements)
7. [Non-Functional Requirements](#7-non-functional-requirements)
8. [Development Constraints & Anti-Bloatware Policy](#8-development-constraints--anti-bloatware-policy)
9. [UI/UX Guidelines](#9-uiux-guidelines)
10. [Deployment Strategy](#10-deployment-strategy)
11. [Security & Access Control](#11-security--access-control)
12. [API Design Overview](#12-api-design-overview)
13. [Acceptance Criteria](#13-acceptance-criteria)
14. [Appendix A — Environment Variables Reference](#appendix-a--environment-variables-reference)
15. [Appendix B — Revision History](#appendix-b--revision-history)

---

## 1. Executive Summary

### 1.1 Project Overview

The **Network Observability Dashboard (NOD)** is an enterprise-grade, custom-built web application designed specifically for Network Operations Center (NOC) teams to monitor, analyze, and report on FortiGate network infrastructure in near-real-time. The system aggregates telemetry from three distinct OpenSearch indices — AppID flow data, SNMP/Telegraf metrics, and IPsec VPN tunnel records — presenting them through a unified, role-aware, high-performance interface.

The system is fully containerized via `docker-compose`, configurable entirely through a `.env` file, and engineered from the ground up to impose **zero measurable degradation** on the underlying OpenSearch clusters.

### 1.2 Business Objectives

| # | Objective | Success Metric | Measurement Method |
|---|-----------|----------------|-------------------|
| 1 | Consolidate multi-source network telemetry into a single operational view | 100% of defined data sources surfaced in the dashboard | Feature completeness audit |
| 2 | Reduce Mean Time to Detect (MTTD) for network anomalies | Alert delivery within 60 seconds of threshold breach | End-to-end alert timing tests |
| 3 | Automate scheduled and on-demand reporting in structured formats | Zero-manual-effort report generation and distribution | User workflow analysis |
| 4 | Enforce structured access control for distinct NOC personas | RBAC verified at the API middleware layer | Security penetration testing |
| 5 | Maintain operational health of the OpenSearch clusters | Zero cluster degradation attributable to dashboard queries | OpenSearch slow query logs |

### 1.3 Scope

#### In Scope:
- ✅ FortiGate AppID NetFlow/IPFIX traffic flow analytics
- ✅ SD-WAN SLA performance monitoring (latency, jitter, packet loss per link)
- ✅ FortiGate HA and resource monitoring (CPU, memory, session count, sync status)
- ✅ SSL VPN and IPsec VPN user session visibility
- ✅ Threshold-based alerting via Telegram Bot API and SMTP Email
- ✅ Structured report generation in PDF, HTML, and DOCX formats
- ✅ Report distribution via Email, Telegram, WhatsApp, Discord, and direct download
- ✅ Role-based user management (superadmin, admin, operator, viewer)
- ✅ Full containerized deployment via `docker-compose`
- ✅ Real-time WebSocket notifications for alert events

#### Out of Scope:
- ❌ FortiGate device configuration or policy management
- ❌ Modification of the OpenSearch ingestion pipeline or index templates
- ❌ OpenSearch cluster administration
- ❌ Raw syslog ingestion
- ❌ Network topology auto-discovery

### 1.4 Assumptions & Constraints

1. **OpenSearch Availability**: The OpenSearch instances at `10.90.150.108:9200` and `10.80.150.108:9200` are operational and reachable from the application Docker network.
2. **Index Stability**: Index mappings for `fortigate-appid-flow-*`, `telegraf-index*`, and `ipsec-*` are stable and conform to the field definitions in Section 5.
3. **Docker Requirements**: The deployment host runs Docker Engine ≥ 24.x and Docker Compose v2.
4. **External Integrations**: All external integrations (Telegram Bot API, SMTP, WhatsApp Business Cloud API, Discord Webhooks) require credentials provided exclusively via the `.env` file.
5. **Site Name Configuration**: FortiGate SD-WAN site names are known at deployment time and configured via environment variables to avoid wildcard queries against `measurement_name`.

---

## 2. Glossary

| Term | Definition |
|------|------------|
| **AppID Flow** | NetFlow/IPFIX records enriched with FortiGate application identification (Deep Packet Inspection) |
| **SD-WAN SLA** | Service-level agreement probe metrics (latency, jitter, packet loss) collected per WAN link |
| **HA Member** | A node in a FortiGate High Availability cluster with associated resource telemetry |
| **IPsec Normalized** | Normalized IPsec VPN tunnel session records from the `ipsec-normalized` index |
| **RBAC** | Role-Based Access Control |
| **NOC** | Network Operations Center |
| **Superadmin** | Highest-privilege application role with unrestricted access including user activity logs |
| **MTTD** | Mean Time to Detect |
| **DSL** | Domain Specific Language, referring specifically to OpenSearch Query DSL |
| **PEN** | Private Enterprise Number (IPFIX/NetFlow context) |
| **Semantic Client/Server** | Direction-agnostic flow fields (`flow.client.*`, `flow.server.*`) derived by the upstream parser from interface zones, NAT fields, RFC1918 detection, and port heuristics |
| **Flow Correlation** | Bidirectional flow pairing using `flow.correlation_id` to link client→server and server→client traffic |

---

## 3. System Architecture & Tech Stack

### 3.1 High-Level Architecture
┌─────────────────────────────────────────────────────────────────────┐
│ Docker Compose Network │
│ │
│ ┌───────────────┐ ┌───────────────┐ ┌─────────────────────┐ │
│ │ nginx │───▶│ Next.js │───▶│ FastAPI Backend │ │
│ │ (rev proxy) │ │ Frontend │ │ Python 3.11+ │ │
│ │ :{NGINX_PORT│ │ :{FRONTEND_ │ │ :${BACKEND_PORT} │ │
│ │ } │ │ PORT} │ │ │ │
│ └───────────────┘ └───────────────┘ └──────────┬──────────┘ │
│ │ │
│ ┌─────────────────────────────────────────────────────▼───────────┐ │
│ │ PostgreSQL 15 (Application State DB) │ │
│ │ users · roles · alert_rules · alert_logs · user_activity_ │ │
│ │ logs · notifications · report_jobs · user_preferences │ │
│ └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────┬──────────────────────────────────────┘
│ Outbound Read-Only Queries
┌───────────────┼────────────────────┐
▼ ▼ ▼
OpenSearch :9200 OpenSearch :9200 OpenSearch :9200
10.90.150.108 10.80.150.108 10.90.150.108
fortigate-appid- telegraf-index* ipsec-*
flow-*
(AppID Flows) (SD-WAN SLA, (IPsec Tunnels)
HA Resources,
SSL VPN)

### 3.2 Technology Stack

#### 3.2.1 Frontend

| Component | Technology | Version Constraint | Rationale |
|-----------|------------|-------------------|-----------|
| **Framework** | Next.js (App Router) | ≥ 14.x | Server Components for fast initial load; native streaming |
| **Language** | TypeScript | ≥ 5.x | Type safety across component and API boundaries |
| **UI Components** | shadcn/ui | Latest stable | Headless, accessible, fully customizable primitives |
| **Analytical Charts** | Tremor | Latest stable | High-performance analytical charts; minimal bundle footprint |
| **Data Table** | TanStack Table v8 | ≥ 8.x | Virtualized, server-side paginated raw data views |
| **Sankey Diagram** | d3-sankey (isolated import) | Latest stable | Only D3 module permitted; no full `d3` bundle import |
| **State / Data Fetching** | SWR | Latest stable | Lightweight stale-while-revalidate; no Redux overhead |
| **Styling** | Tailwind CSS | ≥ 3.x | Utility-first; unused CSS purged at build time |
| **Form Handling** | React Hook Form + Zod | Latest stable | Lightweight, schema-validated forms; no Formik |

#### 3.2.2 Backend

| Component | Technology | Version Constraint | Rationale |
|-----------|------------|-------------------|-----------|
| **Runtime** | Python | ≥ 3.11 | `asyncio` performance gains; improved typing |
| **Web Framework** | FastAPI | ≥ 0.111 | Async-native; auto-generated OpenAPI docs |
| **OpenSearch Client** | opensearch-py[async] | ≥ 2.x | Official async client; avoids blocking event loop |
| **Task Scheduler** | APScheduler | ≥ 3.10 | Alert polling and scheduled reports; no Celery/Redis overhead |
| **ORM** | SQLAlchemy (async) | ≥ 2.x | Async-native ORM for PostgreSQL |
| **Application DB** | PostgreSQL | ≥ 15 | Users, alert rules, logs, notification state |
| **JWT Auth** | python-jose | Latest stable | Stateless auth with HTTP-only cookie refresh tokens |
| **Password Hashing** | passlib[bcrypt] | Latest stable | bcrypt cost factor ≥ 12 |
| **PDF / HTML Reports** | WeasyPrint | ≥ 62 | CSS-driven HTML→PDF; embeds server-rendered chart images |
| **DOCX Reports** | python-docx | ≥ 1.x | MS Office 365-compatible document generation |
| **Server-Side Charts** | Matplotlib | ≥ 3.8 | Lightweight, dependency-light PNG chart rendering for reports |
| **Email** | aiosmtplib | Latest stable | Async SMTP; no synchronous blocking |
| **Telegram Alerts** | httpx (direct Bot API calls) | Latest stable | Thin async HTTP client; avoids heavy `python-telegram-bot` |
| **Real-Time Push** | FastAPI WebSocket | Built-in | Alert notifications to connected clients |
| **ASGI Server** | Uvicorn | Latest stable | Production-grade ASGI server |
| **DB Migrations** | Alembic | Latest stable | Schema versioning |

#### 3.2.3 Infrastructure

| Component | Technology | Notes |
|-----------|------------|-------|
| **Containerization** | Docker + Docker Compose v2 | All services containerized |
| **Reverse Proxy** | Nginx (Alpine) | TLS termination point; proxies to frontend and backend |
| **Secrets / Config** | `.env` file | All credentials and port bindings externalized |
| **Log Storage** | Host-mounted volume (`./logs/`) | Accessible outside containers for log shippers |

---

## 4. Data Sources & Index Configuration

### 4.1 Index Registry

| Index Pattern | OpenSearch Endpoint | Env Variable | Measurement Types |
|---------------|---------------------|--------------|-------------------|
| `fortigate-appid-flow-*` | 10.90.150.108:9200 | `OPENSEARCH_APPID_URL` | AppID NetFlow/IPFIX enriched flows |
| `telegraf-index*` | 10.80.150.108:9200 | `OPENSEARCH_TELEGRAF_URL` | SD-WAN SLA, HA/Resources, SSL VPN (discriminated by `measurement_name`) |
| `ipsec-*` | 10.90.150.108:9200 | `OPENSEARCH_IPSEC_URL` | IPsec VPN tunnel sessions |

### 4.2 Site Name Configuration

The `telegraf-index*` index is multiplexed by the `measurement_name` field. Because wildcard queries on `measurement_name` are **prohibited** (see Rule Q-06), all SD-WAN site names and SSLVPN site names must be declared explicitly via environment variables at deployment time:

```bash
# Comma-separated list of SD-WAN site measurement_name values
TELEGRAF_SDWAN_SITES=Site_FGT-DC,Site_FGT-DRC

# Comma-separated list of SSL VPN measurement_name values
TELEGRAF_SSLVPN_SITES=Site_FGT-DC_SSLVPN,Site_FGT-DRC_SSLVPN
The backend must parse these on startup and use them to construct exact term filters, not wildcards.
4.3 Index Routing
The backend must instantiate separate AsyncOpenSearch client instances per distinct endpoint. Routing logic must resolve the correct client from the target index pattern at the query-builder layer, not at the route handler layer.
5. Data Dictionary
Scope: Field definitions are derived exclusively from the JSON document samples provided in the project brief. No fields are inferred or invented beyond those samples. Fields marked [NO-AGG] must not be used as aggregation targets.
5.1 AppID Flow Index — fortigate-appid-flow-YYYY.MM.DD
Routing: OPENSEARCH_APPID_URL
Timestamp field: @timestamp
Field
Mapping Type
Description
Example
flow.src.ip.addr
ip
Raw source IP address (direction-agnostic; pre-semantic-layer)
192.168.100.69
flow.dst.ip.addr
ip
Raw destination IP address (direction-agnostic)
52.97.112.210
flow.dst.l4.port.id
integer
Destination Layer 4 port number
443
l4.proto.name
keyword
Layer 4 protocol name
UDP, TCP
flow.client.ip.addr
ip
Semantic client IP — derived by the upstream parser via four-level priority stack (zone, NAT, RFC1918, port heuristics)
192.168.100.69
flow.server.ip.addr
ip
Semantic server IP — counterpart to flow.client.ip.addr
52.97.112.210
flow.client.bytes
long
Bytes attributed to the client-direction of the flow
10806
flow.server.bytes
long
Bytes attributed to the server-direction of the flow
0
flow.application.name
keyword
FortiGate DPI-identified application name
Microsoft.Office.365.Portal
flow.application.category
keyword
FortiGate application category
Collaboration
flow.bytes_human
keyword
[NO-AGG] Pre-formatted total bytes string; display only
10.55 KB
flow.packets
long
Total packet count for the flow record
16
flow.in.netif.name
keyword
Ingress network interface or zone name
Infrastructure
flow.out.netif.name
keyword
Egress network interface name
port15
flow.out.netif.descr
keyword
Egress interface human-readable description (e.g., WAN link label)
WAN Internet
flow.correlation_id
keyword
Unique bidirectional flow identifier — generated from sorted IP:port pairs to link request/response flows
192.168.1.10:443-10.0.0.5:52341-TCP
Critical Note: flow.bytes_human is a pre-rendered keyword string and must not be used in any aggregation query. Byte-sum calculations must operate on flow.client.bytes + flow.server.bytes.
Flow Correlation ID Specification:
Format: {sorted_ip_1}:{sorted_port_1}-{sorted_ip_2}:{sorted_port_2}-{protocol}
Generation: Sort both IP:port pairs lexicographically, then concatenate with protocol
Purpose: Enables bidirectional flow analysis and conversation tracking
Example: Client 192.168.1.10:52341 → Server 10.0.0.5:443 becomes 10.0.0.5:443-192.168.1.10:52341-TCP
5.2 SNMP / Telegraf Index — telegraf-index*
Routing: OPENSEARCH_TELEGRAF_URL
Timestamp field: @timestamp
Discriminator field: measurement_name — every query against this index must include an exact term filter on this field.
5.2.1 SD-WAN SLA — measurement_name: Site_<SITE_NAME>
The site-specific metrics are nested under a top-level key that equals the measurement_name value (e.g., "Site_FGT-DC":{...}). Backend query builders must template this key dynamically from the configured site name list.
Field Path
Mapping Type
Description
Example
measurement_name
keyword
Measurement discriminator
Site_FGT-DC
<site>.ifname_link1
keyword
WAN interface name for link 1
wan1
<site>.ifname_link2
keyword
WAN interface name for link 2
wan2
<site>.jitter_link1
float
Measured jitter on link 1 (ms)
0.037
<site>.jitter_link2
float
Measured jitter on link 2 (ms)
0.025
<site>.latency_link1
float
Round-trip latency on link 1 (ms)
13.421
<site>.latency_link2
float
Round-trip latency on link 2 (ms)
13.095
<site>.name_sla_sdwan_1
keyword
[NO-AGG] SLA probe target name for link 1
ping google.com
<site>.name_sla_sdwan_2
keyword
[NO-AGG] SLA probe target name for link 2
ping google.com
<site>.packet_loss_link1
float
Packet loss percentage on link 1
0
<site>.packet_loss_link2
float
Packet loss percentage on link 2
0
<site>.status_link1
integer
Link 1 status: 0 = Up, non-zero = Degraded/Down
0
<site>.status_link2
integer
Link 2 status: 0 = Up, non-zero = Degraded/Down
0
5.2.2 HA & Resource Monitoring — measurement_name: ha_member
Field Path
Mapping Type
Description
Example
measurement_name
keyword
Measurement discriminator
ha_member
ha_member.cpu_usage
integer
CPU utilization percentage
1
ha_member.mem_usage
integer
Memory utilization percentage
43
ha_member.session_count
long
Active firewall session count
20457
ha_member.sync_status
integer
HA synchronization status: 1 = In Sync
1
tag.device
keyword
Device logical identifier
FGT-DC
tag.hostname
keyword
FortiGate OS hostname
FG_DC_GTN-01
tag.serial_number
keyword
[NO-AGG] Hardware serial number
FG200ETK20904635
5.2.3 SSL VPN Sessions — measurement_name: Site_<SITE_NAME>_SSLVPN
The SSLVPN metrics are nested under a key matching the full measurement_name value (e.g., "Site_FGT-DC_SSLVPN":{...}).
Field Path
Mapping Type
Description
Example
measurement_name
keyword
Measurement discriminator
Site_FGT-DC_SSLVPN
<site_SSLVPN>.bytes_in
long
Bytes received by the VPN endpoint in this session
53358
<site_SSLVPN>.bytes_out
long
Bytes sent from the VPN endpoint in this session
126869
<site_SSLVPN>.remote_ip
ip
Client's public-facing IP address
103.156.134.50
<site_SSLVPN>.vpn_ip
ip
Assigned SSL VPN tunnel IP address
10.80.148.171
tag.device
keyword
FortiGate device identifier
FGT-DC
tag.username
keyword
Authenticated SSL VPN username
v.emudhra4
5.3 IPsec VPN Index — ipsec-normalized
Routing: OPENSEARCH_IPSEC_URL
Timestamp field: @timestamp
Field Path
Mapping Type
Description
Example
measurement_name
keyword
Always ipsec_normalized
ipsec_normalized
ipsec_normalized.bytes_in
long
Bytes received through the IPsec tunnel
3150717
ipsec_normalized.bytes_out
long
Bytes transmitted through the IPsec tunnel
21871145
ipsec_normalized.tunnel_lifetime
long
Duration the tunnel has been active (seconds)
41225
tag.device
keyword
FortiGate device identifier
FGT-DRC
tag.username
keyword
IPsec tunnel or user identifier
ervinda.pratama
tag.remote_gw_ip
ip
Remote gateway public IP address
103.184.181.50
tag.assigned_ip
ip
Inner IP assigned to the IPsec peer
10.90.148.90
6. Functional Requirements
FR-01: Overview Dashboard (Summary)
Priority: P0 — Must Have
Description: The landing page presented immediately after login. Provides a high-level operational snapshot across all data sources. All panels auto-refresh on a configurable interval (default: 60 seconds). Clicking any panel navigates to the corresponding dedicated view.
Panels Required:
Panel ID
Title
Data Source
Query Type
Chart Type
Refresh Rate
P01-A
Active SSL VPN Users
telegraf-index* (SSLVPN)
cardinality agg on tag.username
KPI Card
60s
P01-B
Active IPsec VPN Users
ipsec-normalized
cardinality agg on tag.username
KPI Card
60s
P01-C
FortiGate CPU (per device)
telegraf-index* (ha_member)
top_hits (size:1, sort @timestamp desc) per tag.device → ha_member.cpu_usage
Gauge Cards
60s
P01-D
FortiGate Memory (per device)
telegraf-index* (ha_member)
top_hits (size:1) per tag.device → ha_member.mem_usage
Gauge Cards
60s
P01-E
Active Sessions (per device)
telegraf-index* (ha_member)
top_hits (size:1) per tag.device → ha_member.session_count
KPI Card with sparkline
60s
P01-F
HA Sync Status
telegraf-index* (ha_member)
top_hits (size:1) per tag.device → ha_member.sync_status
Status Badge (In Sync / Out of Sync)
60s
P01-G
Top 5 Traffic Applications
fortigate-appid-flow-*
terms agg (size:5) on flow.application.name, sub-agg sum of client+server bytes
Horizontal Bar Chart
60s
P01-H
SD-WAN Link Status
telegraf-index* (SD-WAN SLA)
top_hits (size:1) per configured site → status_link1, status_link2
Status Table
60s
P01-I
Total Throughput (selected window)
fortigate-appid-flow-*
sum of flow.client.bytes + flow.server.bytes within @timestamp range
KPI Card
60s
Acceptance Criteria:
✅ All panels render within 3 seconds on a LAN-connected client.
✅ Auto-refresh does not issue the next request until the previous one completes or times out (5-second timeout per panel).
✅ Panel P01-A and P01-B are scoped to the selected global timeframe.
✅ An individual panel query failure renders a non-blocking "Data unavailable" state without affecting adjacent panels.
✅ All KPI cards display trend indicators (↑/↓) compared to the previous time window.
FR-02: Traffic Flow View
Priority: P0 — Must Have
Description: A dedicated analytics page for AppID flow data from fortigate-appid-flow-*. All visualizations are scoped to the global timeframe selector.
Components Required:
Component ID
Component
Fields Used
Query Type
Visualization
TF-01
Top Applications Bar Chart
flow.application.name, byte fields
terms agg (size: configurable 5/10/20) with sum sub-agg
Horizontal Bar Chart
TF-02
Application Category Donut Chart
flow.application.category, byte fields
terms agg (size: 20) with sum sub-agg
Donut Chart
TF-03
Sankey Diagram
flow.in.netif.name, flow.application.name, flow.out.netif.descr
terms composite agg (size: 100 max)
D3 Sankey
TF-04
Throughput Timeline
@timestamp, byte fields
date_histogram with sum sub-agg
Stacked Area Chart
TF-05
Top Client IPs
flow.client.ip.addr, byte fields
terms agg (size: configurable) with sum sub-agg
Data Table
TF-06
Top Server IPs
flow.server.ip.addr, byte fields
terms agg (size: configurable) with sum sub-agg
Data Table
TF-07
Protocol Distribution
l4.proto.name, byte/packet fields
terms agg (size: 20) with sum sub-agg
Pie Chart
TF-08
Egress Interface Breakdown
flow.out.netif.descr, byte fields
terms agg (size: 20) with sum sub-agg
Bar Chart
TF-09
Flow Conversations Table
flow.correlation_id, client/server IPs, bytes, packets
composite agg with flow.correlation_id as key
TanStack Table
Query Requirements:
✅ All queries must include a range filter on @timestamp with both gte and lte bounds.
✅ date_histogram for TF-04 must use calendar_interval or fixed_interval appropriate to the selected time range (e.g., 1m for ≤ 2h, 5m for ≤ 12h, 15m for ≤ 24h).
✅ The Sankey source data (TF-03) must use a composite aggregation, not a nested terms aggregation, to enumerate the three-way relationship without exceeding cardinality limits.
✅ All terms aggregations must have explicit size values. The UI must not silently rely on OpenSearch defaults.
✅ Flow correlation queries (TF-09) must aggregate on flow.correlation_id to group bidirectional traffic into conversation records.
Flow Conversations Table Specification (TF-09):
Column Header
Source Field
Width
Sortable
Filterable
Format
Correlation ID
flow.correlation_id
180px
Yes
Yes
Truncated with tooltip
Client
flow.client.ip.addr → reverse DNS
150px
Yes
Yes
Hostname or IP
Client IP
flow.client.ip.addr
120px
Yes
Yes
IPv4
Server
flow.server.ip.addr → reverse DNS
150px
Yes
Yes
Hostname or IP
Server IP
flow.server.ip.addr
120px
Yes
Yes
IPv4
Service
l4.proto.name + flow.dst.l4.port.id
100px
Yes
Yes
TCP/443 format
Total Bytes
flow.client.bytes + flow.server.bytes
80px
Yes
No
Auto-scaled (KB/MB/GB)
Packets
flow.packets
80px
Yes
No
Integer with K/M suffix
Sessions
Count of flow records per flow.correlation_id
80px
Yes
No
Integer
Duration
@timestamp range per correlation
100px
Yes
No
Human-readable (s, m, h)
FR-03: SD-WAN Performance SLA View
Priority: P0 — Must Have
Description: Dedicated monitoring page for SD-WAN WAN link quality metrics from telegraf-index*.
Components Required:
Component ID
Component
Fields Used
Query Type
SLA-01
Site + Link Selector
measurement_name, tag.device
UI filter; no dedicated query
SLA-02
Latency Timeline
<site>.latency_link1, <site>.latency_link2, @timestamp
date_histogram with avg sub-agg per link
SLA-03
Jitter Timeline
<site>.jitter_link1, <site>.jitter_link2, @timestamp
date_histogram with avg sub-agg per link
SLA-04
Packet Loss Timeline
<site>.packet_loss_link1, <site>.packet_loss_link2, @timestamp
date_histogram with avg sub-agg per link
SLA-05
Link Status Table
<site>.status_link1, <site>.status_link2, tag.device
top_hits (size:1 per site, sort by @timestamp desc)
SLA-06
SLA Summary KPIs
All metric fields
avg, max aggregations for window
Query Requirements:
✅ Every query against telegraf-index* must include an exact term filter on measurement_name using one of the configured site names from TELEGRAF_SDWAN_SITES. Wildcard matching on measurement_name is prohibited.
✅ All queries must be scoped to the @timestamp range of the selected timeframe.
✅ The SLA-05 "current status" query must use top_hits aggregation (size: 1, sorted @timestamp desc), not a match_all unbounded fetch.
FR-04: FortiGate Resource View
Priority: P0 — Must Have
Description: Dedicated resource monitoring page for ha_member data from telegraf-index*.
Components Required:
Component ID
Component
Fields Used
Query Type
RES-01
Device Selector
tag.device
UI filter
RES-02
CPU Timeline
ha_member.cpu_usage, @timestamp, tag.device
date_histogram with avg sub-agg, split by tag.device
RES-03
Memory Timeline
ha_member.mem_usage, @timestamp, tag.device
Same as above
RES-04
Session Count Timeline
ha_member.session_count, @timestamp, tag.device
Same as above
RES-05
HA Sync Status Badges
ha_member.sync_status, tag.device, tag.hostname
top_hits (size:1, sort @timestamp desc) per tag.device
RES-06
Current Resource KPIs
Latest value per device for all three metrics
top_hits (size:1) within device terms agg
RES-07
Device Info Card
tag.hostname, tag.serial_number, tag.device
top_hits (size:1)
Query Requirements:
✅ "Current" / "latest" values must be retrieved via top_hits (size: 1, sort: @timestamp desc) within a terms aggregation on tag.device. Raw unbounded document fetches are prohibited.
✅ All queries must include a term filter on measurement_name: "ha_member".
✅ Multi-device timelines must use a single query with a terms aggregation on tag.device containing a date_histogram sub-aggregation — not one query per device.
FR-05: Raw Data View (Traffic Flow Table)
Priority: P1 — Should Have
Description: A fully interactive, server-side paginated table of raw fortigate-appid-flow-* records, implemented with TanStack Table v8.
Default Column Set:
Column Header
Source Field
Sortable
Filterable
Filter Type
Timestamp
@timestamp
Yes
Yes
Date range
Correlation ID
flow.correlation_id
Yes
Yes
Exact / prefix
Client IP
flow.client.ip.addr
Yes
Yes
Exact / prefix
Server IP
flow.server.ip.addr
Yes
Yes
Exact
Application
flow.application.name
Yes
Yes
Multi-select terms
Category
flow.application.category
Yes
Yes
Multi-select terms
Protocol
l4.proto.name
No
Yes
Multi-select terms
Dst Port
flow.dst.l4.port.id
Yes
Yes
Exact integer
Total Bytes
flow.client.bytes + flow.server.bytes (computed)
Yes
No
—
Packets
flow.packets
Yes
No
—
Ingress Zone
flow.in.netif.name
No
Yes
Multi-select terms
Egress Link
flow.out.netif.descr
No
Yes
Multi-select terms
Behavioral Requirements:
✅ Strict server-side operation: All sorting, filtering, and pagination logic must execute as OpenSearch DSL queries. Client-side sorting or filtering of retrieved documents is prohibited.
✅ Page size options: 25, 50, 100 (max). max page_size hard cap: 500 rows. Any client-supplied value above 500 must be rejected with HTTP 400.
✅ Pagination method: Use search_after with a sort key (@timestamp + document _id as tiebreaker) for all pagination beyond the first page. The from + size pattern is limited to the first page only and must not exceed OpenSearch's 10,000 document window limit.
✅ Source filtering: The backend must request only the columns required by the table via _source: {"includes": [...]}. Full document retrieval is prohibited.
✅ Column visibility toggle: User can show/hide columns; hidden columns are excluded from the _source filter.
✅ CSV export: Exports the current filtered result set, maximum 10,000 rows. A confirmation dialog must appear before export begins.
✅ URL state: Active filters, sort column, sort direction, and page number must be serialized into URL query parameters for deep-linking and browser back/forward navigation.
FR-06: User Management & RBAC
Priority: P0 — Must Have
Description: Role-based access control enforced at the backend API middleware layer. UI restrictions are supplementary; backend enforcement is authoritative.
Role Definitions:
Role
Description
Prohibited From
superadmin
Unrestricted full-system access
Nothing
admin
Manage users, alert rules; view alert logs
Viewing user activity logs
operator
Read all dashboard views; generate and download reports
Managing users or alert rules
viewer
Read-only dashboard access
Report export, alert management, user management
User Management Features:
✅ Create, read, update, and deactivate users (hard delete is prohibited; deactivated users cannot log in and are retained for audit purposes).
✅ A single role may be assigned per user.
✅ Admin-initiated password reset forces a mandatory password change on the user's next login.
✅ last_login timestamp is displayed in the user list.
✅ The superadmin account cannot be deactivated or have its role changed.
Acceptance Criteria:
✅ All role restrictions enforced at FastAPI dependency injection middleware, not solely in the frontend.
✅ An unauthorized API request returns HTTP 403 with the standard error envelope (see Section 12.3).
✅ Role assignments are validated on every authenticated request, not cached in the session.
FR-07: Settings Menu
Priority: P1 — Should Have
Description: A user-accessible settings panel for personal account and preference management.
Feature
Description
Accessible By
Change Password
Change own password; requires current password verification
All roles
UI Theme
Toggle light/dark mode; persisted to user profile in PostgreSQL
All roles
Alert Notification Preferences
Opt in/out of in-app notification popups for alert categories
admin, operator
API Token Management
Generate and revoke long-lived personal API tokens for programmatic report access
superadmin, admin
FR-08: Alert System (Core)
Priority: P0 — Must Have
Description: A threshold-based alerting engine that evaluates configured alert rules on a scheduled APScheduler polling cycle and dispatches notifications via configured channels.
Alert State Machine:
123
PENDING: Threshold condition is met, but the sustained_for_minutes window has not elapsed.
FIRING: Condition has been sustained for the configured duration. Notification is dispatched.
RESOLVED: Metric has returned below threshold. Optional resolution notification dispatched.
Evaluation Logic:
✅ APScheduler runs the poll job every ALERT_POLL_INTERVAL_SECONDS seconds.
✅ For each enabled alert rule, the backend executes the corresponding aggregation query against the target OpenSearch index.
✅ The resulting metric value is compared against the configured threshold and operator.
✅ If the condition is met, a PENDING timestamp is recorded. Once the elapsed time since the first PENDING state equals or exceeds sustained_for_minutes, the rule transitions to FIRING.
✅ A notification is dispatched to all configured channels for the rule.
✅ Deduplication: A FIRING alert does not re-notify unless it has transitioned through RESOLVED first, or until the configurable re-notify interval (ALERT_RENOTIFY_INTERVAL_MINUTES) has elapsed.
✅ If the poll job itself fails (e.g., OpenSearch unavailable), the error is logged to error.log, the cycle is skipped, and the next scheduled cycle proceeds normally. No crash. No partial state update.
Notification Channels:
Channel
API Mechanism
Credential Env Vars
Telegram
POST https://api.telegram.org/bot{token}/sendMessage
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
Email
SMTP via aiosmtplib
SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM_ADDRESS
Alert Templates:
Templates are stored in the PostgreSQL alert_templates table as Jinja2-compatible strings. Default templates are seeded on first run.
Available template variables:
{{ alert_name }}
{{ metric_value }}
{{ threshold }}
{{ condition }}
{{ device }}
{{ fired_at }}
{{ severity }}
{{ dashboard_url }}
FR-09: Alert Rules Engine
Priority: P0 — Must Have
Description: A UI for creating, editing, enabling/disabling, and deleting alert rules. Modeled on the Grafana alert panel interaction pattern.
Alert Rule Schema:
Field
Type
Required
Description
name
string
Yes
Human-readable rule name
severity
enum
Yes
INFO / WARNING / CRITICAL
data_source
enum
Yes
appid_flow / sdwan_sla / ha_resource / vpn_ssl / vpn_ipsec
metric_field
string
Yes
Full OpenSearch field path (e.g., ha_member.cpu_usage)
aggregation
enum
Yes
avg / max / min / sum / count
condition
enum
Yes
> / < / >= / <= / ==
threshold_value
float
Yes
Numeric threshold value
evaluation_window_minutes
integer
Yes
Lookback window for the aggregation
sustained_for_minutes
integer
Yes
How long condition must hold before transitioning to FIRING
notify_channels
array
Yes
One or more of: telegram, email
template_id
UUID
No
Reference to a custom notification template
enabled
boolean
Yes
Whether the rule is actively evaluated (default: true)
UI Components:
✅ Rule list table with inline enable/disable toggle (PATCH request on toggle).
✅ "Create Rule" and "Edit Rule" modal with schema-validated form (Zod).
✅ "Test Rule" button: Executes the rule's query immediately against live data and displays the current metric value in a non-destructive preview panel. Does not fire a notification. Does not alter alert state.
✅ "Alert History" tab per rule: shows fired_at, resolved_at, metric_value_at_firing, notified_channels.
FR-10: UI Notifications
Priority: P1 — Should Have
Description: Real-time in-app notification system for triggered alert events.
Requirements:
✅ The backend pushes alert state transitions (FIRING, RESOLVED) to all authenticated connected clients via a WebSocket endpoint at /ws/alerts.
✅ The dashboard top navigation bar displays a bell icon with an integer badge showing the count of unread FIRING notifications for the current user.
✅ Clicking the bell icon opens a notification drawer (shadcn/ui Sheet) listing recent alerts: timestamp, alert name, severity, metric value.
✅ Severity-based color coding: CRITICAL → destructive red, WARNING → amber, INFO → blue.
✅ "Mark as Read" button per notification; "Mark All as Read" bulk action.
✅ Notification read state is persisted per user in the PostgreSQL notifications table. It is not stored in the frontend only.
✅ WebSocket connections must be authenticated (JWT validation on upgrade request). Unauthenticated upgrade requests must be rejected with HTTP 401.
FR-11: System Logging
Priority: P0 — Must Have
Description: Structured internal logs for auditing, compliance, and incident investigation.
Log Types:
Log Type
Table
Contents
Retention
Access Roles
Alert Log
alert_logs
Rule name, severity, metric value at firing, fired_at, resolved_at, notified channels, rule snapshot
90 days
admin, superadmin
User Activity Log
user_activity_logs
user_id, action (login, logout, report_generated, user_created, user_deactivated, alert_rule_created, password_changed, etc.), timestamp, source_ip, details (JSON)
1 year
superadmin only
Enforcement:
✅ The GET /api/v1/logs/user-activity endpoint must return HTTP 403 for any authenticated user whose role is not superadmin.
✅ This restriction must be enforced at the FastAPI route dependency level, not solely in the frontend routing logic.
✅ All authenticated API calls that mutate state (POST, PUT, PATCH, DELETE) must generate a corresponding user activity log entry.
FR-12: Report Export Engine
Priority: P1 — Should Have
Description: On-demand and optionally scheduled generation of structured, data-rich reports. Reports are not screenshots; they contain independently rendered charts (server-side PNG), tabular data, and narrative sections.
Report Types:
Report ID
Title
Data Sources
R-01
Traffic Flow Report
fortigate-appid-flow-*
R-02
Resource Usage Report
telegraf-index* (ha_member)
R-03
Active VPN Users Report
telegraf-index* (SSLVPN), ipsec-normalized
R-04
All-in-One Report
R-01 + R-02 + R-03 combined
Common Report Sections (all types):
✅ Cover Page: Report title, time range, generated timestamp (UTC), generated by (username), report ID (UUID).
✅ Executive Summary: Auto-computed statistics for the time window (e.g., total bytes, peak CPU, active VPN user count).
✅ Charts Section: Server-side rendered PNG charts (Matplotlib) embedded inline. Minimum resolution: 150 DPI.
✅ Data Tables: Paginated summary tables with totals row. No raw record dumps in PDF/DOCX; use aggregated views.
✅ Footer: Page number, report generation timestamp, document classification label.
Output Formats:
Format
Generation Library
Compliance Note
PDF
WeasyPrint (HTML→PDF pipeline)
Charts embedded as PNG <img> in HTML template
HTML
Jinja2 templates
Self-contained; all CSS inline; no external CDN dependencies
DOCX
python-docx
Tested against MS Office 365 and LibreOffice 7+
Asynchronous Generation:
✅ Report generation must be a background task. The POST /api/v1/reports/generate endpoint returns an immediate 202 Accepted with a job_id.
✅ The client polls GET /api/v1/reports/status/{job_id} for completion.
✅ The generated file is stored temporarily (configurable TTL, default: 1 hour) and then purged.
✅ Custom Timeframe: The 24-hour warning dialog (FR-14) applies to report time range selection.
Scheduled Reports (Optional):
Alert rules engine-style scheduling for automated report generation at daily, weekly, or monthly intervals, with automatic distribution to configured channels.
FR-13: Report Distribution
Priority: P1 — Should Have
Description: Delivery mechanisms for generated report files.
Method
Mechanism
Required Configuration
Direct Download
HTTP Content-Disposition: attachment response
None
Open in New Tab
HTTP Content-Disposition: inline response
HTML and PDF only
Email
SMTP attachment via aiosmtplib; recipient address specified at request time
SMTP_* env vars
Telegram
Telegram Bot API sendDocument method
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
WhatsApp
WhatsApp Business Cloud API media upload + send
WHATSAPP_API_TOKEN, WHATSAPP_PHONE_NUMBER_ID
Discord
Discord Webhook with multipart/form-data file attachment
DISCORD_WEBHOOK_URL
File Size Guard:
✅ If a generated report file exceeds 20 MB, the user must be presented with a warning dialog before distribution is attempted.
✅ For Telegram (50 MB bot limit) and WhatsApp (100 MB API limit), the file size limits of the respective APIs must be checked before dispatch; the user must be notified if the file exceeds the channel's limit.
FR-14: Timeframe Controls
Priority: P0 — Must Have
Description: A global time range selector applied to all dashboard views and report queries.
Preset Options:
Label
Duration
Notes
Last 15 minutes
900s
Default on all page loads
Last 1 hour
3600s
Preset
Last 2 hours
7200s
Preset
Last 4 hours
14400s
Preset
Last 12 hours
43200s
Preset
Last 24 hours
86400s
Preset
Custom Range
Arbitrary from / to
See warning rule below
Warning Rule (Mandatory):
If a user-specified custom range spans more than 86,400 seconds (24 hours), the UI must render a blocking confirmation dialog before submitting the query to the backend. The dialog must display the following exact message:
"Warning: Querying data beyond 24 hours may cause high resource usage on OpenSearch. Do you want to continue?"
The query must not be dispatched until the user explicitly confirms.
Implementation Specification:
✅ Time ranges must be transmitted to the backend as absolute UTC epoch milliseconds (gte and lte). Relative expressions (e.g., now-15m) must not be sent from the frontend; the frontend computes absolute bounds from the selection.
✅ The lte bound defaults to "now" for all presets.
✅ The gte bound for custom ranges must be validated: lte must be greater than gte. Invalid ranges must be rejected client-side before submission.
Auto-Refresh:
✅ An auto-refresh toggle is present on all dashboard views (default: ON, interval: 60 seconds, configurable via DEFAULT_REFRESH_INTERVAL_SECONDS env var).
✅ Auto-refresh must be automatically disabled when a custom time range with a fixed lte is active, because the "now" end-point would no longer be current.
✅ When auto-refresh is ON, the gte and lte are recomputed relative to the current time on each cycle.
FR-15: Application Logging
Priority: P1 — Should Have
Description: Standard structured application-level logs written by the backend service.
Log Files:
File
Contents
Format
logs/access.log
Every API request: HTTP method, path, response status, response time (ms), client IP, trace_id
JSON (one object per line)
logs/error.log
All unhandled exceptions, alert evaluation failures, report generation errors, OpenSearch query timeouts
JSON with level, timestamp, trace_id, message, stack_trace
Log Rotation:
✅ Rotate at 100 MB file size.
✅ Retain the 10 most recent rotated files.
✅ Both thresholds configurable via LOG_MAX_BYTES and LOG_BACKUP_COUNT env vars.
Request Tracing:
✅ Every incoming request must be assigned a trace_id (UUID v4).
✅ This ID must be included in the access.log entry, in all error.log entries emitted during request processing, and returned to the client in the X-Trace-ID response header.
Syslog Forwarding (Optional, Disabled by Default):
✅ Enabled via SYSLOG_ENABLED=true + SYSLOG_HOST + SYSLOG_PORT in .env.
✅ Transport: UDP, RFC 3164.
✅ Implementation: Python stdlib logging.handlers.SysLogHandler only. No additional packages may be introduced for this feature.
FR-16: Enterprise-Grade Enhancements
Priority: P2 — Nice to Have
Description: Features that improve NOC operational robustness, user experience, and system resilience. All items in this section must be designed to not introduce additional service dependencies beyond the defined stack.
Feature
Specification
Dark Mode
Full dark/light theme via Tailwind dark: variant and shadcn/ui CSS variable theming. Theme persisted per user in PostgreSQL. No flash of unstyled content (FOUC) on load.
Responsive Design
Fully functional at ≥ 1280px viewport width. At 768px–1279px, a read-only view with simplified layouts is presented. Below 768px, a "Not optimized for mobile" banner is shown.
API Rate Limiting
FastAPI middleware enforces per-IP rate limits: 60 requests/minute for standard endpoints; 5 requests/minute for report generation (/api/v1/reports/*). Returns HTTP 429 with Retry-After header on breach. Implemented using Python stdlib time and an in-memory counter; no external cache (Redis) required.
Dashboard Widget Pinning
Users can pin any chart panel to a personal "My Dashboard" quick-view page. Pin configuration stored in PostgreSQL user_pinned_widgets table.
Health Check Endpoint
GET /health returns HTTP 200 with a JSON body indicating: api: ok, db: ok/error, opensearch_appid: ok/error, opensearch_telegraf: ok/error, opensearch_ipsec: ok/error. Used by Docker health check.
Session Timeout
Idle sessions (no authenticated API activity) expire after SESSION_IDLE_TIMEOUT_HOURS (default: 4). User is redirected to login with an informational message.
CORS Policy
Strict CORS; allowed origins specified via ALLOWED_ORIGINS env var (comma-separated). All origins denied by default if var is unset.
OpenSearch Connection Pooling
A single persistent AsyncOpenSearch client instance per configured endpoint. Pool size configurable via OPENSEARCH_POOL_SIZE env var (default: 10).
Graceful Panel Error Handling
Each dashboard panel independently handles query failures. A failed panel displays a non-blocking "Data unavailable — Retry" state within its own boundary. The page does not crash or show a full-page error.
Print-Friendly View
CSS @media print stylesheets ensure raw data tables and key charts render cleanly when the browser print dialog is invoked. Navigation, sidebars, and action buttons are hidden in print view.
7. Non-Functional Requirements
7.1 Performance
Requirement
Target
Measurement
Overview Dashboard cold load (all panels)
P95 ≤ 3 seconds
Measured from navigation start to all panels displaying data
Single panel API response
P95 ≤ 2 seconds
Backend API response time (measured in access.log)
Raw data table page (50 rows)
P95 ≤ 1.5 seconds
Backend API response time
Report generation — PDF, 1-hour data window
≤ 30 seconds
Backend background task duration
WebSocket alert delivery latency
≤ 5 seconds from alert state transition
End-to-end test
Alert poll cycle (20 rules)
≤ 30 seconds total
APScheduler job execution time
7.2 Scalability
✅ The backend must sustain ≥ 20 concurrent authenticated users without query timeout or response degradation.
✅ Report generation must be non-blocking (background task). The system must support ≥ 5 concurrent report generation jobs without performance degradation on the API layer.
✅ OpenSearch aggregation queries must not create scroll contexts in dashboard panels. search_after must be used for all paginated raw record access.
7.3 Reliability
✅ All Docker services must be configured with restart: unless-stopped.
✅ The alert polling job must be resilient to transient OpenSearch unavailability: log the error, skip the evaluation cycle, and proceed normally on the next interval. No partial state updates may be written on a failed cycle.
✅ A PostgreSQL connection failure must not crash the web server; the backend returns HTTP 503 with a structured error body and logs the event.
✅ Background report generation jobs that fail must update the job status to FAILED in the database and write the error to error.log. The API layer must expose the failure reason via the job status endpoint.
7.4 Security
✅ All credentials reside exclusively in .env. Hardcoded credentials in source code or Docker images are a P0 defect requiring immediate remediation.
✅ JWT access tokens: 1-hour TTL, HS256 or RS256 algorithm, minimum 256-bit secret.
✅ Refresh tokens: HTTP-only, Secure, SameSite=Strict cookie; 24-hour TTL; revocable via database record.
✅ Passwords: bcrypt hashed, minimum cost factor 12.
✅ The .env file must be listed in .gitignore. A .env.example with empty values must be committed.
✅ HTTPS termination at Nginx. Application containers serve plain HTTP internally within the Docker network only.
✅ All mutating endpoints (POST, PUT, PATCH, DELETE) require a valid CSRF token or are protected by the SameSite=Strict cookie policy.
7.5 Maintainability
✅ Backend: PEP 8 compliance enforced via flake8 or ruff in CI.
✅ Frontend: ESLint + Prettier enforced in CI.
✅ All OpenSearch queries must be defined in dedicated Python modules within app/opensearch/ — never inline within route handlers.
✅ Every environment-specific configuration value must be injectable via environment variable. No environment-specific values may be hardcoded.
✅ Database schema changes must be managed exclusively via Alembic migrations. Direct DDL changes to the production database are prohibited.
7.6 Observability
✅ GET /metrics endpoint exposes Prometheus-format metrics (disabled by default; enabled via METRICS_ENABLED=true). Exposed metrics include request counts, latency histograms, alert evaluation duration, and report generation duration.
✅ All structured logs include trace_id for distributed request correlation.
8. Development Constraints & Anti-Bloatware Policy
⚠️ MANDATORY SECTION
Compliance is required from all contributors and AI coding assistants (GitHub Copilot, Cursor, Claude, ChatGPT, etc.) involved in this project. Violations of this policy are treated as functional defects, not stylistic preferences.
8.1 Policy Statement
This project operates under a strict Anti-Bloatware Policy. The governing principle is:
Every line of code, every imported package, and every OpenSearch query must justify its existence through a direct, verifiable contribution to a defined functional requirement in this PRD. Speculative features, unused dependencies, over-engineered abstractions, and resource-hungry patterns are defects — not improvements.
8.2 Prohibitions for AI Coding Assistants
The following behaviors are strictly forbidden when AI coding assistants are used during any phase of development:
#
Prohibition
Rationale
1
Generating calls to OpenSearch APIs not documented in the official opensearch-py library documentation
Hallucinated method names produce silent runtime failures
2
Importing packages not present in the approved requirements.txt or package.json without a documented PR justification
Unreviewed dependencies introduce security surface area and binary bloat
3
Creating custom wrapper classes or abstract base classes around OpenSearch DSL query construction that re-implement functionality already provided by the opensearch-py client
Unnecessary abstraction layers increase cognitive overhead with zero functional gain
4
Generating "utility helpers", "common modules", or "shared services" that are not called by any currently defined functional requirement
Dead code obscures the codebase and misleads future developers
5
Fetching _source: true (all fields) in any production query
Returns unnecessary data; increases network I/O, parsing cost, and memory pressure
6
Writing match_all queries without a @timestamp range filter against any time-series index
Unbounded queries can scan the entire index and overwhelm the cluster
7
Introducing a scroll API usage in any dashboard or real-time panel
Scroll contexts hold cluster resources open; use search_after
8
Writing synchronous opensearch-py client calls (non-async) inside async def FastAPI route handlers or APScheduler jobs
Blocks the entire event loop; degrades concurrency under load
9
Returning raw OpenSearch _source objects directly to the frontend via the API
Leaks internal document structure; inflates payload; couples frontend to index schema
10
Generating N+1 query patterns (fetch a list, then issue one query per item in a loop)
Use composite aggregations or multi_search instead
8.3 OpenSearch Query Optimization Mandates
All queries executed against any OpenSearch index must comply with the following rules. These rules must be verified in code review for every PR that introduces or modifies an OpenSearch query.
Rule Q-01 — Time Bounds Required (Non-Negotiable):
Every production query against fortigate-appid-flow-*, telegraf-index*, or ipsec-* must include a range filter on @timestamp with explicit gte and lte values. Queries without both time bounds are prohibited in production code paths. Development/test queries are exempt only in isolated test fixtures.
Rule Q-02 — Explicit Aggregation Size:
All terms aggregations must specify an explicit size parameter. Maximum permitted values:
500 for dashboard panels
1000 for report generation queries
Implicit reliance on the OpenSearch default size of 10 is prohibited. When the actual cardinality of a field is unknown, use a conservative explicit size and document the assumption.
Rule Q-03 — Mandatory Source Filtering:
All document-fetch queries (non-aggregation) must specify _source: {"includes": [...]} listing only the fields required by the calling panel or report section. Requesting all fields via "_source": true or omitting the _source parameter entirely is prohibited in production query modules.
Rule Q-04 — No Scroll API in Real-Time Paths:
The scroll API must not be used for dashboard panels or real-time alert evaluation. It may only be used for background report generation where a large dataset must be exhaustively consumed. In all other cases, use search_after pagination.
Rule Q-05 — Aggregation Over Application-Layer Computation:
When a panel or report requires a sum, average, count, max, or min of a numeric field, use an OpenSearch aggregation query. Fetching raw documents and computing these values in Python or JavaScript is prohibited, regardless of result set size.
Rule Q-06 — Exact measurement_name Filter (Non-Negotiable for telegraf-index*):
Any query against telegraf-index* must include a term filter on measurement_name with the exact string value (e.g., "term": {"measurement_name": "ha_member"}). Wildcard or prefix queries on measurement_name are prohibited. The list of valid measurement_name values must be sourced from configured environment variables (TELEGRAF_SDWAN_SITES, TELEGRAF_SSLVPN_SITES), not from dynamic index cardinality lookups at runtime.
Rule Q-07 — No N+1 Query Patterns:
The backend must not execute a loop that issues one OpenSearch query per item in a previously fetched list (N+1 anti-pattern). Multi-device data must be retrieved using a single query with a terms aggregation on tag.device or via the multi_search API. Any code pattern of the form for device in devices: await es.search(...) requires immediate refactoring.
Rule Q-08 — Pagination Window Hard Cap:
The from + size window (from + size ≤ 10,000) is subject to OpenSearch's hard limit. For pages beyond this window, search_after with a composite sort key (@timestamp + _id) must be used. The backend must validate that no incoming pagination request would exceed this limit; if it does, return HTTP 400.
8.4 Dependency Budget
Layer
Policy
Backend (Python)
No new package may be added to requirements.txt without a documented justification in the PR description explaining which FR it addresses and why no existing approved dependency satisfies the requirement. Heavy all-in-one frameworks (Django, SQLModel, Starlette plugins not bundled with FastAPI, etc.) are prohibited. FastAPI + SQLAlchemy + the listed packages represent the approved ceiling.
Frontend (Node.js)
No new npm package exceeding 50 KB (gzip-compressed, per bundlephobia or next build analysis) may be added without documented justification. Bundle size must be reviewed in every PR that adds a dependency, using the next build output.
Infrastructure
No additional Docker services (Redis, RabbitMQ, Elasticsearch cluster, Kibana, etc.) without an architecture review and explicit approval. The defined stack — Next.js, FastAPI, PostgreSQL, Nginx — represents the approved infrastructure ceiling.
8.5 Code Review Anti-Bloatware Checklist
The following checklist must be completed by the code reviewer for every PR that introduces or modifies OpenSearch query code:
All new queries include explicit @timestamp range filter with both gte and lte bounds (Rule Q-01).
All terms aggregations have an explicit size parameter within the permitted range (Rule Q-02).
_source field filtering is applied to all document-fetch queries (Rule Q-03).
No scroll API usage in dashboard or alert code paths (Rule Q-04).
No client-side aggregation of fetched documents (Rule Q-05).
All telegraf-index* queries include exact measurement_name term filter (Rule Q-06).
No N+1 query patterns in any new code (Rule Q-07).
Pagination from+size does not exceed 10,000; search_after used for deeper pages (Rule Q-08).
No new packages introduced without documented justification.
No synchronous opensearch-py calls within async def functions.
No dead code (unused imports, unreferenced functions, commented-out blocks).
No hardcoded credentials, IP addresses, or environment-specific values in source files.
Backend API responses contain only fields required by the frontend consumer — not raw OpenSearch documents.
9. UI/UX Guidelines
9.1 Design System Reference
The dashboard strictly follows the aesthetic, component conventions, and interaction patterns of the next-shadcn-admin-dashboard reference template. Custom components are permitted only when no shadcn/ui primitive satisfies the functional requirement. All custom components must extend shadcn/ui primitives and use the defined CSS variable token system.
9.2 Page Layout Structure
12345678910111213141516171819
9.3 Color Palette & Theming
All colors must use the shadcn/ui CSS variable system. Hardcoded hex values inside component files are prohibited.
CSS Variable
Light Mode
Dark Mode
Semantic Usage
--background
#FFFFFF
#09090B
Page background
--foreground
#09090B
#FAFAFA
Primary text
--card
#FFFFFF
#09090B
Panel and card backgrounds
--primary
#18181B
#FAFAFA
Primary interactive elements
--muted
#F4F4F5
#27272A
Subdued backgrounds, skeleton loaders
--destructive
#EF4444
#EF4444
CRITICAL alerts, errors
--warning (custom token)
#F59E0B
#F59E0B
WARNING alerts, caution states
--success (custom token)
#10B981
#10B981
OK status, resolved alerts
--border
#E4E4E7
#27272A
Component borders and dividers
9.4 Chart Standards
All analytical charts must use Tremor components except the Sankey diagram (d3-sankey).
Chart Type
Tremor Component
Usage
Time-series
AreaChart or LineChart
CPU, latency, jitter, throughput timelines
Ranking / Top-N
BarChart (horizontal)
Top applications, top clients
Proportional
DonutChart
Category distribution
KPI
Metric + Flex
Overview cards with trend indicators
Sankey
d3-sankey (custom React wrapper)
Traffic flow: zone → app → egress link
Chart Standards:
✅ All chart tooltips must display formatted values with appropriate units: ms for latency/jitter, % for loss/CPU/memory, KB/MB/GB (auto-scaled) for bytes, K/M for session counts.
✅ Charts must display an explicit empty state component ("No data for selected time range") — never a blank canvas.
✅ Chart legends must be positioned below the chart area, not overlaid on the chart.
✅ Charts must not block the page render; they must use Suspense or SWR loading state with a Skeleton placeholder.
9.5 Loading States
✅ shadcn/ui Skeleton components replace every panel during initial load and on each refresh cycle.
✅ Skeleton dimensions must match the expected rendered panel dimensions to prevent cumulative layout shift (CLS).
✅ Loading state must never collapse the panel's reserved space.
9.6 Error States
✅ Each panel handles its own query errors independently.
✅ A panel in error state renders: an error icon, a concise message ("Query failed"), and a "Retry" button.
✅ A single panel error must not trigger a page-level error boundary or affect adjacent panels.
9.7 Accessibility
✅ All interactive elements must have aria-label or aria-describedby attributes.
✅ Status indicators (HA Sync, Link Status) must convey meaning through both color AND a text label. Color-only status indicators are prohibited.
✅ All modals must trap keyboard focus and support Escape to close.
✅ The contrast ratio for all text must meet WCAG 2.1 AA standard (4.5:1 for normal text, 3:1 for large text).
10. Deployment Strategy
10.1 Docker Compose Service Definitions
File: docker-compose.yml (annotated skeleton; not final production configuration)
yaml
1234567891011121314151617181920212223242526272829303132333435363738394041424344454647484950515253545556575859606162636465666768697071
10.2 First-Run Sequence
bash
12345678910111213141516
10.3 Project Directory Structure
12345678910111213141516171819202122232425262728293031323334353637383940414243444546474849505152535455565758596061626364656667686970717273747576777879808182838485868788
10.4 Port Configuration Summary
No port value may be hardcoded in any Dockerfile, source file, or Nginx configuration. All ports must reference the corresponding environment variable.
Service
Env Variable
Default
Scope
Nginx (host binding)
NGINX_PORT
80
External
Next.js Frontend
FRONTEND_PORT
3000
Internal
FastAPI Backend
BACKEND_PORT
8000
Internal
PostgreSQL
POSTGRES_PORT
5432
Internal
11. Security & Access Control
11.1 Authentication Flow
✅ User submits username + password to POST /auth/login.
✅ Backend retrieves the user record from PostgreSQL; verifies password against the bcrypt hash.
✅ On success:
Issues a short-lived access token (JWT, 1-hour TTL) in the response body.
Sets a refresh token (opaque, 24-hour TTL) as an HTTP-only, Secure, SameSite=Strict cookie.
Records the last_login timestamp in PostgreSQL.
✅ The frontend includes the access token as Authorization: Bearer {token} on all subsequent API requests.
✅ When the access token expires, the frontend calls POST /auth/refresh. The backend validates the refresh token cookie, verifies it is not revoked in the database, and issues a new access token.
✅ POST /auth/logout revokes the refresh token by marking it as invalidated in the database. The cookie is cleared.
11.2 Authorization Dependency Pattern
FastAPI dependency injection enforces role checks on every protected route. The pattern must be consistent across all route modules:
python
1234567891011
11.3 Secret Hygiene
✅ All secrets reside in .env exclusively.
✅ Minimum JWT secret length: 32 random bytes (256 bits), Base64-encoded.
✅ No secret may appear in application source code, Docker build args, or image layers.
✅ The .env.example file contains only key names with empty values and inline comments describing the expected format.
12. API Design Overview
12.1 Base Path & Versioning
All API endpoints are versioned under the prefix /api/v1/. Future breaking changes will be introduced under /api/v2/ without removing the /api/v1/ endpoints until a formal deprecation period expires.
12.2 Endpoint Summary
Method
Path
Description
Minimum Role
POST
/auth/login
Authenticate; receive access token
Public
POST
/auth/logout
Revoke session
Authenticated
POST
/auth/refresh
Refresh access token
Authenticated (cookie)
GET
/health
Service and dependency health check
Public
GET
/api/v1/overview
All Overview Dashboard panel data
viewer
GET
/api/v1/traffic/summary
Traffic analytics panels (FR-02)
viewer
GET
/api/v1/traffic/raw
Paginated raw flow records (FR-05)
operator
GET
/api/v1/sdwan/sla
SD-WAN SLA metrics (FR-03)
viewer
GET
/api/v1/resources
FortiGate HA & resource metrics (FR-04)
viewer
GET
/api/v1/vpn/ssl
Active SSL VPN sessions
viewer
GET
/api/v1/vpn/ipsec
Active IPsec VPN sessions
viewer
GET
/api/v1/alerts/rules
List alert rules
admin
POST
/api/v1/alerts/rules
Create alert rule
admin
PUT
/api/v1/alerts/rules/{id}
Update alert rule
admin
DELETE
/api/v1/alerts/rules/{id}
Delete alert rule
admin
POST
/api/v1/alerts/rules/{id}/test
Test rule (no notification)
admin
GET
/api/v1/alerts/logs
Alert firing history
admin
GET
/api/v1/logs/user-activity
User activity audit log
superadmin only
POST
/api/v1/reports/generate
Trigger async report generation
operator
GET
/api/v1/reports/status/{job_id}
Poll report generation status
operator
GET
/api/v1/reports/download/{job_id}
Download completed report
operator
POST
/api/v1/reports/distribute/{job_id}
Distribute report to channels
operator
GET
/api/v1/users
List users
admin
POST
/api/v1/users
Create user
admin
PUT
/api/v1/users/{id}
Update user
admin
PUT
/api/v1/users/me/password
Change own password
Authenticated
GET
/api/v1/notifications
Fetch user's notifications
Authenticated
PATCH
/api/v1/notifications/{id}/read
Mark notification as read
Authenticated
WS
/ws/alerts
WebSocket: real-time alert push
Authenticated
GET
/metrics
Prometheus metrics (if enabled)
Public (by design)
12.3 Standard Response Envelope
All JSON responses (success and error) must conform to this envelope structure:
Success:
json
1234567891011
Error:
json
123456789
Common Error Codes:
HTTP Status
Error Code
Trigger
400
VALIDATION_ERROR
Invalid request parameters
400
PAGINATION_LIMIT_EXCEEDED
from + size > 10,000
401
UNAUTHENTICATED
Missing or invalid JWT
403
INSUFFICIENT_PRIVILEGES
Role below required minimum
404
NOT_FOUND
Resource does not exist
429
RATE_LIMIT_EXCEEDED
Rate limit breached
503
DEPENDENCY_UNAVAILABLE
PostgreSQL or OpenSearch unreachable
13. Acceptance Criteria
13.1 Functional Acceptance Tests
Test ID
Requirement
Pass Condition
AT-01
FR-01: Overview Dashboard
All 9 panels render with live data within 3 seconds on LAN; individual panel failure does not crash the page
AT-02
FR-02: Traffic Flow — Sankey
Sankey renders correctly for ≥ 100 distinct flow records across ≥ 3 applications
AT-03
FR-03: SD-WAN SLA
Latency, jitter, and packet loss timelines match raw aggregated values verifiable in OpenSearch
AT-04
FR-04: Resource View
CPU, memory, and session counts match the latest ha_member document per device
AT-05
FR-05: Raw Data Table
Filter by flow.application.category reduces result count; pagination returns correct page
AT-06
FR-06: RBAC — API Enforcement
viewer role attempting GET /api/v1/users receives HTTP 403 with error envelope
AT-07
FR-06: RBAC — Superadmin Lock
admin role attempting GET /api/v1/logs/user-activity receives HTTP 403
AT-08
FR-08 + FR-09: Alert Firing
Alert fires within 2 poll cycles of threshold breach; Telegram message received within 60s
AT-09
FR-09: Test Rule
"Test Rule" returns current metric value; no notification dispatched; alert state unchanged
AT-10
FR-10: UI Notifications
Bell badge increments on alert fire; decrements on "Mark All as Read"
AT-11
FR-11: Activity Log Restriction
admin user cannot retrieve user activity log via direct API call
AT-12
FR-12 + FR-13: Report Formats
PDF, HTML, and DOCX variants generated; all contain embedded charts and data tables; directly downloadable
AT-13
FR-14: Timeframe Warning
Custom range > 24h renders the exact warning dialog; query is not dispatched until confirmed
AT-14
FR-14: Auto-Refresh Disable
Setting a custom fixed time range automatically disables auto-refresh
AT-15
FR-15: Log Files
logs/access.log and logs/error.log present with correct JSON structure; rotation occurs at 100 MB
AT-16
FR-16: Dark Mode
All panels, charts, modals, and tables render without white-flash or unstyled elements in dark mode
AT-17
Deployment
docker compose up -d --build brings all services to healthy state within 120 seconds on a clean host
AT-18
Deployment: First Run
alembic upgrade head + seed_superadmin.py complete without error; superadmin login succeeds
AT-19
FR-02: Flow Correlation
flow.correlation_id correctly groups bidirectional flows; conversation table shows accurate byte/packet totals
13.2 Performance Acceptance Tests
Test ID
Test Scenario
Pass Condition
PT-01
Overview dashboard cold load
P95 ≤ 3 seconds (10 test runs)
PT-02
Raw data table — page 1, 50 rows
P95 ≤ 1.5 seconds
PT-03
20 concurrent users on Overview
No query timeout; P95 ≤ 5 seconds
PT-04
Alert poll cycle — 20 enabled rules
Single poll cycle completes in ≤ 30 seconds
PT-05
All-in-One Report — 1-hour data window
Generated in ≤ 30 seconds
13.3 Security Acceptance Tests
Test ID
Test Scenario
Pass Condition
ST-01
JWT forgery attempt (invalid signature)
HTTP 401 with UNAUTHENTICATED error code
ST-02
Cross-role API access (viewer → admin route)
HTTP 403 with INSUFFICIENT_PRIVILEGES
ST-03
Secret scan on committed code
git grep + truffleHog finds no credentials
ST-04
Password storage verification
pg_dump shows bcrypt hash, not plaintext
ST-05
Rate limit enforcement
61st identical request within 60 seconds from same IP → HTTP 429
Appendix A — Environment Variables Reference
File: .env.example — All values must be populated in .env before first run.
bash
123456789101112131415161718192021222324252627282930313233343536373839404142434445464748495051525354555657585960616263646566676869707172737475767778798081828384858687888990919293949596979899100101102103104105106107108109110111112113114115116117
Appendix B — Revision History
Version
Date
Author
Summary of Changes
1.0.0
June 2026
Principal Architect
Initial release — full requirements, data dictionary, architecture, anti-bloatware policy
1.1.0
June 2026
Principal Architect
Updated conversation_id to flow.correlation_id for better field namespacing; enhanced Flow Conversations Table specification; improved overall document structure and clarity; added detailed acceptance criteria for flow correlation
End of Document
Network Observability Dashboard PRD v1.1.0 — Internal Confidential